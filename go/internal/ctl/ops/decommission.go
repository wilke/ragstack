package ops

// decommission — v1 QUARANTINES a tenant and never purges one.
//
// Every step here is reversible by hand with `mv`, and the one irreversible
// thing the verb could do (deleting data) it does not do at all: the data
// directory is renamed, the registry row is marked `quarantined` and keeps its
// port block (so the allocator never hands it out again), and a
// RECOVERY.json is left inside the renamed directory saying what this used to
// be. A live purge is v1.x and is a separately named operation, because
// "decommission" is the word an operator types when they are tidying up and
// destruction must never be what tidying up does.
//
// The selftest's sandbox tenants are the one exemption: they live in their own
// port block, they are created and destroyed by a test, and requiring each of
// them to carry a verified restore before it can be cleaned up would make the
// selftest a three-minute operation that leaves rubbish behind when it fails.

import (
	"context"
	"encoding/json"
	"fmt"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/render"
)

// recoveryFile is the note left INSIDE the quarantined directory. Inside, not
// beside it: whoever finds `…/dev.quarantined-20260914T093000Z` in six months
// is looking at that directory, and a record kept anywhere else is a record
// they will not find.
const recoveryFile = "RECOVERY.json"

func planDecommission(_ context.Context, p *planner, _ map[string]any) error {
	p.need(model.LockRegistry, model.LockManifest, model.LockTenant, model.LockGateway)
	t := p.t
	// v1 quarantines; it never purges. And it quarantines only what the ctl
	// made or supervises: renaming the data directory of a hand-run tenant
	// belonging to another account is destroying somebody else's work with a
	// tool that cannot put it back.
	managed := t.Supervisor == supervisorSystemd && t.Owner == p.op.deps.owner()
	sandbox := p.isSandbox()
	if !managed && !sandbox {
		return p.refuse("%s is neither a ctl-managed tenant (supervisor systemd, owner svcbvbrc — it is %s/%s) nor a "+
			"selftest sandbox (ports %d–%d): v1 decommission quarantines only what the ctl runs",
			t.Name, t.Supervisor, t.Owner, paths.SelftestBase, paths.SelftestEnd)
	}
	if sandbox {
		// A sandbox needs no recovery point. It was created by the selftest
		// minutes ago, its contents are a fixture, and the bundle a
		// non-sandbox tenant must have is the thing the selftest is ON ITS WAY
		// to prove — demanding it here would make the test depend on its own
		// later steps.
		p.warn("this is a selftest sandbox (ports " + strconv.Itoa(paths.SelftestBase) + "–" +
			strconv.Itoa(paths.SelftestEnd) + "): no fenced backup is required, and the allocator reuses sandbox blocks once the row is gone, " +
			"or the selftest would exhaust them")
	} else if err := p.requireFencedBackup("decommission"); err != nil {
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
	p.addUnitFileRemoval()
	// The registry step comes BEFORE the publish: the gateway renders routes
	// for active rows only, so the generation without this tenant can only be
	// rendered once the row says quarantined.
	p.addQuarantineRegistry(sandbox)
	p.add(step{
		Kind: "nginx", Title: "publish a generation without " + t.Name, Destructive: true, Targets: []string{t.Name},
		Warnings: []string{"the gateway renders only ACTIVE tenants, so a quarantined row drops out of the map by " +
			"itself; this publishes the generation in which it has"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			if routed, why, err := gatewayRoutes(ctx, sc, t.Name); err != nil {
				return "", err
			} else if !routed {
				sc.Logf("%s", why)
				return "skipped: " + why, nil
			}
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
	p.addQuarantineRename()
	p.addRecoveryNote()
	p.addWorktreeRemoval()

	p.result["state"] = "quarantined"
	// The row STAYS, at state quarantined, and keeps its index: the allocator
	// counts every row, so the block is never handed out while the tree is
	// recoverable. The tombstone — the registry's permanent record of an
	// allocation whose row is gone — is written by the purge that removes the
	// row (v1.x), never here: a tombstone beside a live row is the split-brain
	// the registry's validator refuses.
	p.result["tombstone"] = nil
	if !sandbox {
		p.warn("the row stays in the registry at state quarantined and keeps its port block; the tombstone is " +
			"written when the row is purged (v1.x), so the block is never reused either way")
	}
	p.warn("nothing is deleted: the units are removed, the data directory is renamed and a " + recoveryFile +
		" is written into it. Bringing the tenant back is a rename, a `units apply` and a registry edit")
	return nil
}

// isSandbox is the selftest port-block rule, in one place.
func (p *planner) isSandbox() bool {
	return p.t.Ports.Base >= paths.SelftestBase && p.t.Ports.Base <= paths.SelftestEnd
}

// addUnitFileRemoval deletes the unit FILES after the units are stopped and
// disabled, and reloads the manager.
//
// Without it a decommissioned tenant left its units in the ctl's config tree:
// `systemctl --user list-unit-files` still showed five units for a tenant that
// no longer exists, a `daemon-reload` would load them again, and the selftest's
// post-check ("no ragstack-ctltest-* unit is known") could never pass.
func (p *planner) addUnitFileRemoval() {
	// FLAT under the units dir: SYSTEMD_UNIT_PATH is not searched recursively,
	// so that is where `create` wrote them.
	dir := p.oc.Roots.UnitsDir()
	target, qdrant, es, postgres, api, ui := render.UnitNames(p.t.Name)
	names := []string{target, qdrant, es, postgres, api, ui}
	unitPaths := make([]string, 0, len(names))
	for _, n := range names {
		unitPaths = append(unitPaths, filepath.Join(dir, n))
	}
	p.addFor("files", step{
		Kind: "fs", Title: "remove the rendered unit files", Destructive: true, Targets: unitPaths,
		Warnings: []string{"the files the ctl RENDERED, under its own config tree — never a unit file in the user " +
			"manager's own search path, which the ctl does not own"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			// Forget the units in the manager before their files go: a unit
			// whose file vanished while it was `failed` stays listed as
			// loaded/failed until reset, and the selftest's "no unit is known"
			// post-check reads exactly that listing. Neither call is an error
			// for a unit the manager never loaded.
			for _, n := range names {
				_ = sc.Ops.Drivers.Systemd().Disable(ctx, n)
				_ = sc.Ops.Drivers.Systemd().ResetFailed(ctx, n)
			}
			removed := 0
			for _, path := range unitPaths {
				// Remove is idempotent (an absent path is not an error), which
				// is what makes this step safe to re-run after an interruption.
				if err := sc.Ops.Drivers.Files().Remove(ctx, path); err != nil {
					return "", err
				}
				removed++
			}
			return fmt.Sprintf("removed %d unit file(s) from %s", removed, dir), nil
		},
	})
	p.addFor("systemd", step{
		Kind: "systemd", Title: "systemctl --user daemon-reload",
		WouldRun: []model.WouldRun{{Argv: []string{"/usr/bin/systemctl", "--user", "daemon-reload"}}},
		Warnings: []string{"after the files are gone, so the manager forgets the units rather than keeping them " +
			"loaded from a path that no longer exists"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return "daemon-reload", sc.Ops.Drivers.Systemd().DaemonReload(ctx)
		},
	})
}

// addQuarantineRegistry marks the row quarantined. The block stays the row's.
//
// It runs BEFORE the directory moves: the registry is the source of truth, and
// a crash between the two leaves a row that says `quarantined` over a directory
// that is still in place — which an operator can read and fix. The other order
// leaves a tenant the registry calls active over a directory that is not there,
// which is the state every reader of the fleet then reports as a failure.
func (p *planner) addQuarantineRegistry(sandbox bool) {
	t := p.t
	p.add(step{
		Kind: "registry", Title: "mark " + t.Name + " quarantined (the row keeps its port block)",
		Destructive: true, Targets: []string{t.Name},
		WouldWrite: []model.WouldWrite{{Path: p.oc.Roots.Registry(), Mode: "0660", Preview: ""}},
		Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			if p.op.deps.SaveFleet == nil {
				return "", fmt.Errorf("%w: the registry writer is not wired", jobs.ErrRefused)
			}
			cur := sc.Ops.Fleet
			row := cur.Tenants[t.Name]
			if row == nil {
				return "", fmt.Errorf("%w: %s is no longer in the registry", jobs.ErrRefused, t.Name)
			}
			at := p.stampRFC3339(sc)
			row.State = "quarantined"
			row.DesiredBoot = "disabled"
			if row.LastOps == nil {
				row.LastOps = map[string]registry.OpRecord{}
			}
			row.LastOps["decommission"] = registry.OpRecord{JobID: jobIDOf(sc), At: at, Outcome: "succeeded"}
			if err := p.op.deps.SaveFleet(cur); err != nil {
				return "", err
			}
			return fmt.Sprintf("state=quarantined, index %d kept by the row", row.Ports.Index), nil
		},
	})
}

// addQuarantineRename is the rename itself.
func (p *planner) addQuarantineRename() {
	t := p.t
	p.addFor("files", step{
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
}

// addRecoveryNote writes RECOVERY.json into the quarantined tree.
func (p *planner) addRecoveryNote() {
	t := p.t
	p.addFor("files", step{
		Kind: "fs", Title: "write " + recoveryFile + " into the quarantined directory",
		Targets:    []string{t.DataDir + ".quarantined-<ts>/" + recoveryFile},
		WouldWrite: []model.WouldWrite{{Path: t.DataDir + ".quarantined-<ts>/" + recoveryFile, Mode: "0640", Preview: ""}},
		Warnings: []string{"the registry row, the last bundle, the unit names and the ports — everything needed to " +
			"decide, later, whether this tree can go"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			dir := p.quarantineDirOf(sc)
			target, qdrant, es, postgres, api, ui := render.UnitNames(t.Name)
			row := p.rowOf(sc)
			note := map[string]any{
				"quarantined_at": p.stampRFC3339(sc),
				"job_id":         jobIDOf(sc),
				"tenant":         t.Name,
				"manifest_name":  t.ManifestName,
				"original_dir":   t.DataDir,
				"worktree":       t.Worktree,
				"ports":          t.Ports,
				"units":          []string{target, qdrant, es, postgres, api, ui},
				"last_backup":    row.LastBackup,
				"sandbox":        p.isSandbox(),
				"registry_row":   row,
				"note": "Nothing here was deleted. To bring this tenant back: rename this directory to " +
					"original_dir, restore the registry row (state active), `ragstack-ctl units apply`, " +
					"`ragstack-ctl tenant start`, `ragstack-ctl gateway apply`.",
			}
			body, err := json.MarshalIndent(note, "", "  ")
			if err != nil {
				return "", err
			}
			path := filepath.Join(dir, recoveryFile)
			if err := sc.Ops.Drivers.Files().WriteAtomic(ctx, path, append(body, '\n'), 0o640); err != nil {
				return "", err
			}
			return path, nil
		},
	})
}

// quarantineDirOf is the directory the rename step made, read back from the
// job's own checkpoints — the same discipline as the bundle id, and for the
// same reason: the stamp in the name is a run-time clock reading, and a step
// that read the clock a second time would name a directory nobody made.
//
// The fallback is the clock, for a job whose steps are not readable from here
// (the unit runner, a test harness): with the engine's pinned clock it is the
// same string the rename step built.
func (p *planner) quarantineDirOf(sc *jobs.StepContext) string {
	prefix := "dir:" + p.t.DataDir + ".quarantined-"
	if sc != nil && sc.Job != nil {
		for _, st := range sc.Job.Steps {
			for _, id := range st.ExternalIDs {
				if strings.HasPrefix(id, prefix) {
					return strings.TrimPrefix(id, "dir:")
				}
			}
		}
	}
	return p.t.DataDir + ".quarantined-" + p.stampOf(sc)
}

// addWorktreeRemoval gives the mirror's administrative entry back.
//
// `git worktree remove` rather than a directory delete: the checkout is only
// half of a worktree — the other half is an entry in the mirror's
// `worktrees/` directory, and a tenant tree removed with `rm -rf` leaves the
// mirror believing a checkout exists until somebody runs `worktree prune`.
func (p *planner) addWorktreeRemoval() {
	mirror, dest := p.op.deps.Mirror, p.t.Worktree
	if mirror == "" {
		p.skip("git", "skip removing the git worktree",
			"no mirror is configured (ctl.env CTL_MIRROR): the checkout at "+dest+" is left in place, and the "+
				"mirror's administrative entry with it — `git worktree prune` in the mirror when one exists", dest)
		return
	}
	p.addFor("git", step{
		Kind: "git", Title: "remove the tenant's git worktree", Destructive: true, Targets: []string{dest},
		Warnings: []string{"`worktree remove --force` plus `prune`: the checkout AND the mirror's record of it. " +
			"The tenant's data is not here — this is code"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			if err := sc.Checkpoint("worktree:" + dest); err != nil {
				return "", err
			}
			if err := sc.Ops.Drivers.Git().RemoveWorktree(ctx, mirror, dest); err != nil {
				return "", err
			}
			return "removed " + dest, nil
		},
	})
}
