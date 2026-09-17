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
//
// planHandover and its four phases live in ops/handover.go: it is a
// two-account protocol rather than one job, and it is long enough that
// sharing a file with `migrate-local` hid both.

// addSourceStop stops the hand-started processes of the source owner through
// the pidfile, after proving the pid is this tenant's.
func (p *planner) addSourceStop() {
	pidfile := p.t.API.PidFile
	if pidfile == "" {
		pidfile = p.tpaths.PidFile
	}
	worktree, port := p.t.Worktree, p.t.Ports.API
	p.addFor("proc", step{
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
	p.addFor("proc", step{
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
//
// FLAT under UnitsDir, not in a per-tenant subdirectory: the user manager's
// drop-in sets `SYSTEMD_UNIT_PATH=/rag/config/ctl/units:` and systemd does not
// search a unit path recursively, so a unit written one level down is a unit
// the manager can never find by name. The names already carry the tenant
// (`ragstack-<name>-api.service`), which is what made a subdirectory look
// harmless.
func (p *planner) addUnitsWrite(units map[string][]byte) {
	dir := p.oc.Roots.UnitsDir()
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

func sortedNames(m map[string][]byte) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
