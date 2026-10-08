package ops

// decommission — v1 QUARANTINES a tenant and never purges one.
//
// Every step here is reversible by hand with `mv`, and the one irreversible
// thing the verb could do (deleting data) it does not do at all: the data
// directory is renamed, the registry row is marked `quarantined` and keeps its
// port block (so the allocator never hands it out again), and a
// RECOVERY.json is left inside the renamed directory saying what this used to
// be. Deletion is `tenant purge` (ops/purge.go), a separately named operation, because
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

// quarantinePlaceholder stands for the stamp in a PLAN's quarantine path, as
// bundlePlaceholder does for the bundle id: the stamp is a run-time clock
// reading, and plan.json's would_write path admits only [A-Za-z0-9._/-] — the
// `<ts>` this plan used to carry made every decommission plan a document its
// own contract refused.
const quarantinePlaceholder = "new-stamp"

// quarantineDirID prefixes the external ID under which the registry step
// records the quarantine directory it DECIDED, before it writes the row. The
// rename reads it back rather than reading the clock again: the row's
// `quarantine.dir` and the directory on disk are the same string by
// construction.
const quarantineDirID = "quarantine-dir:"

// prevQuarantineRowID prefixes the external ID that carries the row's fields
// the registry step is about to overwrite, so its Rollback can put them back
// after a restart.
const prevQuarantineRowID = "prev-quarantine-row:"

// decommissionArchiveOf is `archive` with the contract's default applied:
// absent means true. The daemon applies no defaults, so this is where it is.
func decommissionArchiveOf(args map[string]any) bool {
	if v, ok := args["archive"].(bool); ok {
		return v
	}
	return true
}

func planDecommission(_ context.Context, p *planner, args map[string]any) error {
	p.need(model.LockRegistry, model.LockManifest, model.LockTenant, model.LockGateway)
	t := p.t
	// v1 quarantines; it never purges. And it quarantines only what the ctl
	// made or supervises: renaming the data directory of a hand-run tenant
	// belonging to another account is destroying somebody else's work with a
	// tool that cannot put it back.
	managed := managedSupervisor(t.Supervisor) && t.Owner == p.op.deps.owner()
	sandbox := p.isSandbox()
	if !managed && !sandbox {
		return p.refuse("%s is neither a tenant this ctl runs (supervisor systemd or instance, owner "+p.op.deps.owner()+" — it is %s/%s) nor a "+
			"selftest sandbox (ports %d–%d): v1 decommission quarantines only what the ctl runs",
			t.Name, t.Supervisor, t.Owner, paths.SelftestBase, paths.SelftestEnd)
	}
	if p.sup == nil {
		// Unreachable through `managed` above, which already requires a
		// supervisor the ctl can start. It is stated because the OTHER way in
		// is the sandbox exemption, and a sandbox row that somehow carried
		// `supervisor: manual` would otherwise reach the seam with nothing
		// behind it.
		return p.refuse("%s is supervised by %q, which is not something the ctl can stop; there is nothing for "+
			"`decommission` to take down", t.Name, t.Supervisor)
	}
	archive := decommissionArchiveOf(args)
	if archive && !p.op.deps.hasRecipients() {
		// Refused HERE, before a fence stops anything: an archive whose
		// secrets were silently left out is an archive a restore cannot finish
		// from, and that is exactly what an operator decommissioning a tenant
		// would not discover until the day they needed it.
		return p.refuse("decommission archives %s first, and the archive must carry the tenant's secrets "+
			"(backup secrets=require): no age recipient is configured in %s. Run `ragstack-ctl fleet "+
			"backup-identity init` (then restart the daemon), or decommission with --archive=false over an "+
			"existing fenced bundle that is checked or verified", t.Name, p.recipientsPath())
	}
	if archive && t.State == "stopped" {
		// The archive's store legs snapshot RUNNING stores over their HTTP
		// APIs; a tenant whose row says stopped would fail at the first leg,
		// after the fence. Said here, from the row, rather than discovered
		// there.
		return p.refuse("%s is stopped, and the archive snapshots its stores through their running APIs: start it "+
			"first (`ragstack-ctl tenant start %s`), or decommission with --archive=false over its last fenced "+
			"bundle that is checked or verified", t.Name, t.Name)
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
	} else if !archive {
		if err := p.requireFencedBackup("decommission"); err != nil {
			return err
		}
	}

	if archive {
		// The archive IS the precondition, taken inside this job: a fenced,
		// full bundle with the secrets sealed, deep-checked by its own check
		// step and recorded as last_backup — and then NOT released. The API
		// stays down from the fence to the quarantine, so nothing is written
		// to the tenant that the archive does not hold.
		p.warn("--archive (the default): this job first takes a fenced full backup with the tenant's secrets " +
			"sealed (secrets=require), checks it and records it as last_backup; the API is NOT started again " +
			"afterwards — the quarantine follows directly. A failure rolls back every step that has a rollback " +
			"(the rename, the row, the backup record, the bundle's finalize); read the job's rollback block, and " +
			"`tenant start` what it leaves stopped")
		full := scopeSet{}
		for _, leg := range fullScope {
			full[leg] = true
		}
		if _, err := p.addBackupSteps(backupPlanArgs{Fence: true, Scope: full, Secrets: secretsRequire,
			RestartAPI: false}); err != nil {
			return err
		}
	}

	legs, _ := p.legs(nil)
	for _, c := range reverse(legs) {
		if !c.Managed {
			p.skip(p.sup.kind(), "skip "+c.Name, c.Why, c.Name)
			continue
		}
		if err := p.sup.stopLeg(p, c); err != nil {
			return err
		}
		if err := p.sup.disableLeg(p, c); err != nil {
			return err
		}
	}
	// The supervision artefacts: unit files for a systemd tenant, nothing for
	// an instance one — which is the only difference a decommission sees
	// between the two.
	if err := p.sup.remove(p); err != nil {
		return err
	}
	// The registry step comes BEFORE the publish: the gateway renders routes
	// for active rows only, so the generation without this tenant can only be
	// rendered once the row says quarantined.
	p.addQuarantineRegistry(archive)
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
	// row (`tenant purge`), never here: a tombstone beside a live row is the split-brain
	// the registry's validator refuses.
	p.result["tombstone"] = nil
	if !sandbox {
		p.warn("the row stays in the registry at state quarantined and keeps its port block; the tombstone is " +
			"written when the row is purged (`tenant purge`), so the block is never reused either way")
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

// addQuarantineRegistry marks the row quarantined and records the quarantine
// block. The block stays the row's, and so does the port block.
//
// It runs BEFORE the directory moves: the registry is the source of truth, and
// a crash between the two leaves a row that says `quarantined` over a directory
// that is still in place — which an operator can read and fix. The other order
// leaves a tenant the registry calls active over a directory that is not there,
// which is the state every reader of the fleet then reports as a failure.
//
// It DECIDES the quarantine directory (checkpointed as quarantineDirID before
// the row is written) and the rename reads that decision back, so the row's
// `quarantine.dir` names the directory the rename makes. `bundle` is the
// archive this job wrote — checked against the row's last_backup, which the
// archive's record step set — or, without an archive, the row's last_backup.
//
// Its Rollback puts back every field it changed, so a quarantine that fails
// later (the rename above all) leaves no `quarantine` block on a row whose tree
// never moved.
func (p *planner) addQuarantineRegistry(archive bool) {
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
			var bundle registry.NullString
			if archive {
				want := filepath.Join(sc.Ops.Roots.BackupsDir, t.Name, p.bundleID(sc))
				if row.LastBackup == nil || row.LastBackup.Bundle != want {
					got := "none"
					if row.LastBackup != nil {
						got = row.LastBackup.Bundle
					}
					return "", fmt.Errorf("%w: the archive this job wrote is %s but the row's last_backup is %s; "+
						"refusing to record a quarantine whose bundle is not the archive", jobs.ErrRefused, want, got)
				}
				bundle = registry.NullString(want)
			} else if row.LastBackup != nil {
				bundle = registry.NullString(row.LastBackup.Bundle)
			}
			dir := p.quarantineDirOf(sc)
			prev, err := json.Marshal(quarantineRowFields{State: row.State, DesiredBoot: row.DesiredBoot,
				Quarantine: row.Quarantine, LastDecommission: lastOp(row, "decommission")})
			if err != nil {
				return "", fmt.Errorf("recording %s's row before the quarantine: %w", t.Name, err)
			}
			// Both checkpoints BEFORE the write: the rollback needs the old
			// fields, and the rename needs the decided directory, whichever
			// way this step ends.
			var ids []string
			if !recorded(sc, quarantineDirID+dir) {
				ids = append(ids, quarantineDirID+dir)
			}
			if _, ok := externalIDValue(sc.Step.ExternalIDs, prevQuarantineRowID); !ok {
				ids = append(ids, prevQuarantineRowID+string(prev))
			}
			if len(ids) > 0 {
				if err := sc.Checkpoint(ids...); err != nil {
					return "", err
				}
			}
			at := p.stampRFC3339(sc)
			row.State = registry.StateQuarantined
			row.DesiredBoot = "disabled"
			row.Quarantine = &registry.Quarantine{Dir: dir, At: at, JobID: jobIDOf(sc), Bundle: bundle}
			if row.LastOps == nil {
				row.LastOps = map[string]registry.OpRecord{}
			}
			row.LastOps["decommission"] = registry.OpRecord{JobID: jobIDOf(sc), At: at, Outcome: "succeeded"}
			if err := p.op.deps.SaveFleet(cur); err != nil {
				return "", err
			}
			p.result["quarantine_dir"] = dir
			if bundle != "" {
				p.result["archive_bundle"] = string(bundle)
			}
			return fmt.Sprintf("state=quarantined, index %d kept by the row, quarantine.dir=%s", row.Ports.Index, dir), nil
		},
		Rollback: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			save := p.op.deps.SaveFleet
			raw, ok := externalIDValue(sc.Step.ExternalIDs, prevQuarantineRowID)
			if save == nil || !ok {
				return "nothing was written", nil
			}
			var prev quarantineRowFields
			if err := json.Unmarshal([]byte(raw), &prev); err != nil {
				return "", fmt.Errorf("reading the recorded row of %s: %w", t.Name, err)
			}
			cur := sc.Ops.Fleet
			row := cur.Tenants[t.Name]
			if row == nil {
				return "the registry row is gone; nothing to restore", nil
			}
			row.State, row.DesiredBoot, row.Quarantine = prev.State, prev.DesiredBoot, prev.Quarantine
			if prev.LastDecommission != nil {
				if row.LastOps == nil {
					row.LastOps = map[string]registry.OpRecord{}
				}
				row.LastOps["decommission"] = *prev.LastDecommission
			} else {
				delete(row.LastOps, "decommission")
			}
			if err := save(cur); err != nil {
				return "", err
			}
			return fmt.Sprintf("state=%s again, no quarantine recorded", prev.State), nil
		},
	})
}

// quarantineRowFields are the row fields addQuarantineRegistry overwrites,
// recorded before it does so.
type quarantineRowFields struct {
	State            string               `json:"state"`
	DesiredBoot      string               `json:"desired_boot"`
	Quarantine       *registry.Quarantine `json:"quarantine"`
	LastDecommission *registry.OpRecord   `json:"last_decommission"`
}

// lastOp is row.LastOps[verb] as a pointer, nil when absent.
func lastOp(row *registry.Tenant, verb string) *registry.OpRecord {
	if op, ok := row.LastOps[verb]; ok {
		return &op
	}
	return nil
}

// addQuarantineRename is the rename itself.
func (p *planner) addQuarantineRename() {
	t := p.t
	p.addFor("files", step{
		Kind: "fs", Title: "quarantine the data directory (rename to .quarantined-<ts>)", Destructive: true,
		Targets: []string{t.DataDir, t.DataDir + registry.QuarantineMarker + quarantinePlaceholder},
		Warnings: []string{"nothing is deleted: the tree is renamed, stays inside the retention-protected root and " +
			"outside every deletion root. Deleting it is `tenant purge`, a separately named op",
			"`" + quarantinePlaceholder + "` stands for the stamp, decided when the registry step runs and " +
				"recorded on the row as quarantine.dir"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			dst := p.quarantineDirOf(sc)
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
					// The id is recorded BEFORE the rename, so a rename that
					// failed leaves it behind with nothing renamed: then there
					// is nothing to put back, and saying so is a successful
					// rollback rather than a second failure.
					if moved, err := pathExists(ctx, sc.Ops.Drivers.Files(), dst); err != nil {
						return "", err
					} else if !moved {
						return "nothing was renamed (" + dst + " does not exist)", nil
					}
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
		Targets: []string{t.DataDir + registry.QuarantineMarker + quarantinePlaceholder + "/" + recoveryFile},
		WouldWrite: []model.WouldWrite{{Path: t.DataDir + registry.QuarantineMarker + quarantinePlaceholder + "/" +
			recoveryFile, Mode: "0640", Preview: ""}},
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
	// The registry step's decision first (it runs before the rename and is
	// what the row records), then the rename's own record, then — for a step
	// with neither — the clock.
	for _, prefix := range []string{quarantineDirID, "dir:"} {
		want := prefix + p.t.DataDir + registry.QuarantineMarker
		if sc == nil {
			break
		}
		if sc.Step != nil {
			for _, id := range sc.Step.ExternalIDs {
				if strings.HasPrefix(id, want) {
					return strings.TrimPrefix(id, prefix)
				}
			}
		}
		if sc.Job != nil {
			for _, st := range sc.Job.Steps {
				for _, id := range st.ExternalIDs {
					if strings.HasPrefix(id, want) {
						return strings.TrimPrefix(id, prefix)
					}
				}
			}
		}
	}
	return p.t.DataDir + registry.QuarantineMarker + p.stampOf(sc)
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
