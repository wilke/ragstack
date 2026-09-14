package ops

import (
	"context"
	"fmt"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/render"
)

// requireExecutePhase is the shared answer to `--phase commit` and
// `--phase rollback` arriving as NEW jobs.
//
// Both are continuations of the job that ran `execute` and stopped at its
// cutover step: the state they act on — the stopped source processes, the
// staging directory, the recorded gateway generation — belongs to that job
// and lives in its reservations. Accepting them here would start a second job
// with none of it, which is how a "rollback" comes to roll back nothing.
func (p *planner) requireExecutePhase(args map[string]any, verb string) error {
	phase := argStringOf(args, "phase")
	if phase == "execute" {
		return nil
	}
	return p.refuse("`%s --phase %s` is a CONTINUATION of the job that ran `--phase execute` and is waiting at its "+
		"cutover, not a new job: POST /v1/jobs/{id}/%s (or `ragstack-ctl job %s <id>`)", verb, phase,
		map[string]string{"commit": "continue", "rollback": "cancel"}[phase],
		map[string]string{"commit": "continue", "rollback": "cancel"}[phase])
}

// ---------------------------------------------------------------- handover

func planHandover(_ context.Context, p *planner, args map[string]any) error {
	p.need(model.LockRegistry, model.LockManifest, model.LockTenant, model.LockGateway)
	if err := p.requireExecutePhase(args, "handover"); err != nil {
		return err
	}
	t := p.t
	if t.Owner == "svcbvbrc" && t.Supervisor == supervisorSystemd {
		return p.refuse("%s is already owned by svcbvbrc and supervised by systemd; there is nothing to hand over", t.Name)
	}
	if err := p.requireFencedBackup("handover"); err != nil {
		return err
	}
	if t.RollbackDescriptor == nil {
		return p.refuse("%s has no rollback_descriptor: the pre-handover paths, ports, code and launch arguments were "+
			"never captured, so there would be no way back. Run `adopt --commit` first", t.Name)
	}
	// The units are rendered HERE, at plan time, because their refusals are
	// about the registry row (a non-loopback bind on an internet-reachable
	// host, a data dir off the layout) and an operator has to see them before
	// the tenant is stopped, not after.
	cfg := render.UnitConfig{RagRoot: p.oc.Roots.RagRoot, CtlStateDir: p.oc.Roots.CtlStateDir}
	units, err := render.Units(t, cfg)
	if err != nil {
		return p.refuse("%s cannot be handed over as it is recorded: %v", t.Name, err)
	}

	p.addGatewayReadonly(true)
	p.addSourceStop()
	p.addUnitsWrite(units)
	p.add(step{
		Kind: "fs", Title: "fix group ownership and modes of the tenant tree for svcbvbrc",
		Targets: []string{t.DataDir}, Run: p.pendingRun("files", "Chown"),
	})
	p.add(step{
		Kind: "git", Title: "check the worktree out from the artifact at the same SHA",
		Targets: []string{t.Worktree, string(t.Code.SHA)}, Run: p.pendingRun("git", "Worktree"),
	})
	p.add(step{
		Kind: "systemd", Title: "systemctl --user daemon-reload",
		WouldRun: []model.WouldRun{{Argv: []string{"/usr/bin/systemctl", "--user", "daemon-reload"}}},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return "daemon-reload", sc.Ops.Drivers.Systemd().DaemonReload(ctx)
		},
	})
	legs, _ := p.legs(nil)
	for _, c := range legs {
		if c.Managed {
			p.addUnitStep("start", c)
		}
	}
	p.addReadyStep(legs)
	origin := fmt.Sprintf("http://127.0.0.1:%d", t.Ports.API)
	p.add(step{
		Kind: "probe", Title: "post-checks against the destination API", Targets: []string{origin},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return "health ok", sc.Ops.Drivers.TenantAPI().Health(ctx, origin)
		},
	})
	// The publish is the last reversible act, so it is the cutover: after it
	// the job WAITS for an explicit `continue`, and until then `cancel` puts
	// the tenant back where it came from.
	p.add(step{
		Kind: "nginx", Title: "publish the gateway generation for the handed-over tenant",
		Targets: []string{t.Name}, Cutover: true,
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			if err := sc.Checkpoint("gateway:apply:pending"); err != nil {
				return "", err
			}
			gen, detail, err := sc.Ops.Drivers.Gateway().Apply(ctx, false)
			if err != nil {
				return "", err
			}
			if err := sc.Reserve("gateway:gen:"+strconv.Itoa(gen), nil); err != nil {
				return "", err
			}
			return detail, sc.Checkpoint("gateway:gen:" + strconv.Itoa(gen))
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			to := t.RollbackDescriptor.GatewayGeneration
			return sc.Ops.Drivers.Gateway().Rollback(ctx, int(to))
		},
	})
	p.add(step{
		Kind: "registry", Title: "commit: release the writes and record owner=svcbvbrc",
		Targets: []string{t.Name},
		Warnings: []string{"until this step the job is reversible; after it the source is never restarted, and a " +
			"return means a resync or an accepted recovery point"},
		Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			sc.Logf("owner=svcbvbrc, supervisor=systemd, state=active recorded for %s", t.Name)
			return "committed", nil
		},
	})
	p.result["owner"] = "svcbvbrc"
	p.result["supervisor"] = supervisorSystemd
	p.warn("the shared stores and neo4j-dev stay wilke-run: a handover moves the TENANT, not the host's shared services")
	return nil
}

// addSourceStop stops the hand-started processes of the source owner through
// the pidfile, after proving the pid is this tenant's.
func (p *planner) addSourceStop() {
	pidfile := p.t.API.PidFile
	if pidfile == "" {
		pidfile = p.tpaths.PidFile
	}
	worktree, port := p.t.Worktree, p.t.Ports.API
	p.add(step{
		Kind: "proc", Title: "stop the source processes (pidfile + /proc identity)", Destructive: true,
		Targets: []string{pidfile},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			b, err := sc.Ops.Drivers.Files().ReadFile(ctx, pidfile)
			if err != nil {
				return "", fmt.Errorf("reading %s: %w", pidfile, err)
			}
			pid, err := strconv.Atoi(strings.TrimSpace(string(b)))
			if err != nil || pid <= 1 {
				return "", fmt.Errorf("%w: %s does not name a pid", jobs.ErrRefused, pidfile)
			}
			if err := sc.Checkpoint("pid:" + strconv.Itoa(pid)); err != nil {
				return "", err
			}
			return fmt.Sprintf("SIGTERM to pid %d", pid),
				sc.Ops.Drivers.Proc().Signal(ctx, pid, worktree, "uvicorn", "TERM")
		},
	})
	p.add(step{
		Kind: "probe", Title: fmt.Sprintf("verify the source is gone (nothing on %d)", port),
		Targets: []string{strconv.Itoa(port)},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			listening, err := sc.Ops.Drivers.Proc().Listening(ctx, port)
			if err != nil {
				return "", err
			}
			if listening {
				return "", fmt.Errorf("%w: the source is still listening on %d; the destination must not be started "+
					"while it is", jobs.ErrRefused, port)
			}
			return "the source is gone", nil
		},
	})
}

// addUnitsWrite plans the unit files of a handover or a create.
func (p *planner) addUnitsWrite(units map[string][]byte) {
	dir := filepath.Join(p.oc.Roots.UnitsDir(), p.t.Name)
	for _, name := range sortedNames(units) {
		path, body := filepath.Join(dir, name), units[name]
		p.add(step{
			Kind: "fs", Title: "write the unit " + name, Targets: []string{path},
			WouldWrite: []model.WouldWrite{p.preview(path, "0644", body)},
			Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
				if err := sc.Checkpoint("file:" + path); err != nil {
					return "", err
				}
				return path, sc.Ops.Drivers.Files().WriteAtomic(ctx, path, body, 0o644)
			},
			Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
				return "removed " + path, sc.Ops.Drivers.Files().Remove(ctx, path)
			},
		})
	}
}

// requireFencedBackup is the prerequisite handover, migrate-local and
// decommission share: a bundle that a fence made consistent and a restore
// proved. An unfenced bundle is a copy of a moving target.
func (p *planner) requireFencedBackup(verb string) error {
	b := p.t.LastBackup
	switch {
	case b == nil:
		return p.refuse("%s has no backup: `%s` needs a fenced, verified bundle to be reversible "+
			"(`ragstack-ctl tenant backup %s --fence`)", p.t.Name, verb, p.t.Name)
	case !b.Fenced:
		return p.refuse("%s's last bundle (%s) is best-effort: nothing stopped the tenant writing while it was taken, "+
			"so it cannot be the recovery point for `%s`", p.t.Name, b.Bundle, verb)
	case !b.Verified:
		return p.refuse("%s's last bundle (%s) was never verified; verify it (a restore `--as` proves it) before `%s`",
			p.t.Name, b.Bundle, verb)
	}
	return nil
}

// ---------------------------------------------------------------- migrate-local

func planMigrateLocal(_ context.Context, p *planner, args map[string]any) error {
	p.need(model.LockRegistry, model.LockManifest, model.LockTenant, model.LockGateway)
	if err := p.requireExecutePhase(args, "migrate-local"); err != nil {
		return err
	}
	if err := p.requireFencedBackup("migrate-local"); err != nil {
		return err
	}
	t := p.t
	p.warn("migrate-local is v1.1: the plan is complete, and the copy driver (rsync into a job staging dir, then a " +
		"`--delete` reconciliation against the STOPPED source) lands in PR-D")
	p.addGatewayReadonly(true)
	p.addSourceStop()
	staging := filepath.Join(p.oc.Roots.CtlStateDir, "staging", t.Name)
	p.add(step{
		Kind: "fs", Title: "copy the data tree into the job staging directory",
		Targets: []string{t.DataDir, staging}, Run: p.pendingRun("files", "Rsync"),
	})
	p.add(step{
		Kind: "fs", Title: "reconcile the copy against the stopped source (--delete)",
		Targets: []string{staging}, Run: p.pendingRun("files", "Rsync"),
	})
	p.add(step{
		Kind: "nginx", Title: "cutover: publish the generation pointing at the new location",
		Targets: []string{t.Name}, Cutover: true, Run: p.pendingRun("gateway", "ApplyAt"),
	})
	p.add(step{
		Kind: "registry", Title: "commit: record the new data dir and port block", Targets: []string{t.Name},
		Run: p.pendingRun("registry", "Relocate"),
	})
	return nil
}

// ---------------------------------------------------------------- decommission

func planDecommission(_ context.Context, p *planner, _ map[string]any) error {
	p.need(model.LockRegistry, model.LockManifest, model.LockTenant, model.LockGateway)
	t := p.t
	// v1 quarantines; it never purges. And it quarantines only what the ctl
	// made or supervises: renaming the data directory of a hand-run tenant
	// belonging to another account is destroying somebody else's work with a
	// tool that cannot put it back.
	managed := t.Supervisor == supervisorSystemd && t.Owner == "svcbvbrc"
	sandbox := t.Ports.Base >= paths.SelftestBase && t.Ports.Base <= paths.SelftestEnd
	if !managed && !sandbox {
		return p.refuse("%s is neither a ctl-managed tenant (supervisor systemd, owner svcbvbrc — it is %s/%s) nor a "+
			"selftest sandbox (ports %d–%d): v1 decommission quarantines only what the ctl runs",
			t.Name, t.Supervisor, t.Owner, paths.SelftestBase, paths.SelftestEnd)
	}
	if err := p.requireFencedBackup("decommission"); err != nil {
		return err
	}
	legs, _ := p.legs(nil)
	for _, c := range reverse(legs) {
		if !c.Managed {
			p.skip("systemd", "skip "+c.Name, c.Why, c.Name)
			continue
		}
		p.addUnitStep("stop", c)
		p.addUnitStep("disable", c)
	}
	p.add(step{
		Kind: "nginx", Title: "publish a generation without " + t.Name, Destructive: true, Targets: []string{t.Name},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			if err := sc.Checkpoint("gateway:apply:pending"); err != nil {
				return "", err
			}
			gen, detail, err := sc.Ops.Drivers.Gateway().Apply(ctx, false)
			if err != nil {
				return "", err
			}
			return detail, sc.Checkpoint("gateway:gen:" + strconv.Itoa(gen))
		},
	})
	p.add(step{
		Kind: "fs", Title: "quarantine the data directory (rename to .quarantined-<ts>)", Destructive: true,
		Targets: []string{t.DataDir, t.DataDir + ".quarantined-<ts>"},
		Warnings: []string{"nothing is deleted: the tree is renamed, stays inside the retention-protected root and " +
			"outside every deletion root. A live purge is v1.x and a separately named op"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			dst := t.DataDir + ".quarantined-" + p.stampOf(sc)
			if err := sc.Checkpoint("dir:" + dst); err != nil {
				return "", err
			}
			if err := sc.Ops.Drivers.Files().Rename(ctx, t.DataDir, dst); err != nil {
				return "", err
			}
			return "quarantined at " + dst, nil
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			for _, id := range sc.Step.ExternalIDs {
				if strings.HasPrefix(id, "dir:") {
					dst := strings.TrimPrefix(id, "dir:")
					return "restored " + t.DataDir, sc.Ops.Drivers.Files().Rename(ctx, dst, t.DataDir)
				}
			}
			return "", fmt.Errorf("no quarantine directory was recorded")
		},
	})
	p.result["state"] = "quarantined"
	p.result["tombstone"] = map[string]any{"manifest_name": t.ManifestName, "index": t.Ports.Index, "base": t.Ports.Base}
	p.warn("the port block is tombstoned permanently: the allocator never reuses an index that has been decommissioned")
	return nil
}

func sortedNames(m map[string][]byte) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
