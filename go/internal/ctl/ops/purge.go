package ops

// purge — the ONLY op that destroys a tenant's data (PR-G1.4).
//
// It acts on a tenant `decommission` has already QUARANTINED and on nothing
// else: the row is at `state: quarantined` and records where its renamed data
// tree is (`quarantine.dir`). Everything that is the tenant's goes — the
// quarantined tree, the worktree, the units directory, and unless
// `keep_archive` the archive (every bundle under <backups>/<tenant>) — and then
// the registry row. A production block leaves a TOMBSTONE behind, so the port
// block is never handed out again; a selftest sandbox leaves none, because the
// sandbox allocator counts tombstone bases as taken and a sandbox tombstone
// would burn one of five blocks forever.
//
// The order is the design:
//
//  1. probe: nothing listens on any port of the block;
//  2. probe: <quarantine.dir>/RECOVERY.json names this tenant (or the tree is
//     already gone — an earlier purge of this row removed it);
//  3. fs: the worktree (and `git worktree prune` in the mirror);
//  4. fs: the units directory, and any rendered unit file left behind;
//  5. fs: the archive, unless keep_archive (a skipped step with it);
//  6. fs: the quarantined data tree;
//  7. registry: the row, out of `tenants` and `display_order`; a tombstone for
//     a production block.
//
// The ROW GOES LAST because the engine re-plans a job on resume and answers
// 404 for a row that is not there: every step before it must be safe to run
// again (each removal is idempotent — an absent path is done), and nothing may
// come after it. That is also why the job's `result` is assembled by step 7
// from the checkpoints of the steps before it rather than by a step of its own:
// a step after the row is a step a resume can never reach.
//
// Steps 3–6 have NO rollback. They are irreversible, and each says so; a
// failure part way leaves a quarantined row over whatever is left, and running
// purge again finishes the job.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/render"
)

// purgeFreedID prefixes the checkpoint a removal step records BEFORE it
// deletes: `freed:<bytes>:<path>`. It is how the registry step knows, after a
// resume, what the earlier steps removed and how much that was — the size of a
// tree can only be measured before it is gone.
const purgeFreedID = "freed:"

// purgeIrreversible is the warning every removal step carries.
const purgeIrreversible = "IRREVERSIBLE: there is no rollback for this step. A failure part way leaves the row " +
	"quarantined over whatever is left, and running `tenant purge` again finishes the job (an absent path is done)"

// purgeKeepArchiveOf is `keep_archive` with the contract's default applied:
// absent means false.
func purgeKeepArchiveOf(args map[string]any) bool {
	v, _ := args["keep_archive"].(bool)
	return v
}

func planPurge(_ context.Context, p *planner, args map[string]any) error {
	p.need(model.LockRegistry, model.LockManifest, model.LockTenant, model.LockGateway)
	t := p.t
	if t == nil {
		return p.refuse("purge names a tenant")
	}
	// The same ownership rule as decommission: the ctl destroys only what it
	// runs (or a selftest sandbox). A quarantined row the ctl does not own is
	// not one this account made, and deleting it is not this account's call.
	managed := managedSupervisor(t.Supervisor) && t.Owner == p.op.deps.owner()
	sandbox := p.isSandbox()
	if !managed && !sandbox {
		return p.refuse("%s is neither a tenant this ctl runs (supervisor systemd or instance, owner %s — it is %s/%s) "+
			"nor a selftest sandbox (ports %d–%d): purge deletes only what the ctl runs",
			t.Name, p.op.deps.owner(), t.Supervisor, t.Owner, paths.SelftestBase, paths.SelftestEnd)
	}
	if t.State != registry.StateQuarantined {
		return p.refuse("%s is %s, not quarantined: purge deletes only a tenant `decommission` has already "+
			"quarantined — run `ragstack-ctl tenant decommission %s` first", t.Name, t.State, t.Name)
	}
	q := t.Quarantine
	if q == nil || q.Dir == "" {
		return p.refuse("%s is quarantined but its row records no quarantine.dir (it was quarantined before the "+
			"row recorded where): purge deletes only a tree the registry names. Record the renamed directory on "+
			"the row, or remove it by hand", t.Name)
	}
	roots := p.oc.Roots
	// The tree the row names must be where decommission puts one: directly
	// under the data root, named after THIS tenant. The driver refuses any
	// other shape at run time too; saying so here is a plan an operator can
	// read rather than a step that fails.
	if filepath.Dir(q.Dir) != roots.DataDir ||
		!strings.HasPrefix(filepath.Base(q.Dir), t.ManifestName+registry.QuarantineMarker) {
		return p.refuse("%s's quarantine.dir %s is not %s/%s%s<stamp>: purge deletes only the tree decommission "+
			"renamed aside", t.Name, q.Dir, roots.DataDir, t.ManifestName, registry.QuarantineMarker)
	}
	keep := purgeKeepArchiveOf(args)
	bundles := filepath.Join(roots.BackupsDir, t.Name)
	worktree := filepath.Join(roots.ReposDir, t.Name)
	unitsDir := filepath.Join(roots.UnitsDir(), t.Name)
	if !keep {
		// Defensive: another row naming a bundle under this tenant's backup
		// directory as ITS recovery point (or as its quarantine's archive)
		// would lose it. Nothing in the control plane writes such a row today;
		// a hand edit could.
		for _, name := range sortedTenantNames(p.oc.Fleet) {
			if name == t.Name {
				continue
			}
			other := p.oc.Fleet.Tenants[name]
			for _, ref := range [][2]string{{"last_backup", lastBackupBundle(other)},
				{"quarantine.bundle", quarantineBundle(other)}} {
				what, b := ref[0], ref[1]
				if b != "" && (b == bundles || strings.HasPrefix(b, bundles+"/")) {
					return p.refuse("%s's %s is %s, under %s's backup directory: purging the archive would delete "+
						"another tenant's recovery point. Purge with --keep-archive, or move that bundle first",
						name, what, b, t.Name)
				}
			}
		}
	}
	if t.Worktree != "" && t.Worktree != worktree {
		p.warn("the row's worktree is %s, not %s: purge removes only the tenant's directory under the worktree "+
			"root, so %s is left where it is", t.Worktree, worktree, t.Worktree)
	}

	p.addPurgePortProbe()
	p.addPurgeRecoveryProbe(q.Dir)
	p.addPurgeWorktree(worktree)
	p.addPurgeUnits(unitsDir)
	if keep {
		p.skip("fs", "keep the archive "+bundles,
			"keep_archive: every bundle directory and .tar under "+bundles+" is left in place; everything else of "+
				"the tenant goes", bundles)
	} else {
		p.addPurgeRemoval("remove the archive (every bundle and .tar)", bundles,
			"the archive the decommission wrote goes too: after this job there is NO bundle to restore "+t.Name+
				" from. Purge with --keep-archive to retain it")
	}
	p.addPurgeRemoval("remove the quarantined data tree", q.Dir,
		"the tenant's data — its stores, its state, its env files and the RECOVERY.json decommission left — is "+
			"deleted, not renamed")
	p.addPurgeRegistry(q.Dir, sandbox, keep)

	what := "the quarantined data tree, the worktree, the units directory and the archive"
	if keep {
		what = "the quarantined data tree, the worktree and the units directory (the archive is kept: --keep-archive)"
	}
	p.warn("purge DELETES %s: %s, then the registry row. Nothing of the tenant remains except %s and the audit "+
		"rows. None of it can be undone", t.Name, what, map[bool]string{true: "the audit rows' record of it (a sandbox " +
		"leaves no tombstone)", false: "the tombstone that keeps its port block from being reused"}[sandbox])
	if keep {
		p.warn("--keep-archive: %s stays, and it is then the only copy of %s", bundles, t.Name)
	}

	p.result["removed"] = []string{}
	p.result["bytes_freed"] = int64(0)
	p.result["tombstone"] = nil
	p.result["archive_kept"] = keep
	return nil
}

// addPurgePortProbe is step 1: nothing of this block is listening. A
// quarantined tenant is down by construction, so a listener here is something
// that is NOT this tenant, sitting on its ports — and deleting the tree it may
// be serving from is exactly the mistake to refuse.
func (p *planner) addPurgePortProbe() {
	ports := purgePorts(p.t.Ports)
	targets := make([]string, 0, len(ports))
	for _, port := range ports {
		targets = append(targets, strconv.Itoa(port))
	}
	p.addFor("proc", step{
		Kind: "probe", Title: "nothing listens on any port of the block", Targets: targets,
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			var busy []string
			for _, port := range ports {
				listening, err := sc.Ops.Drivers.Proc().Listening(ctx, port)
				if err != nil {
					return "", err
				}
				if listening {
					busy = append(busy, strconv.Itoa(port))
				}
			}
			if len(busy) > 0 {
				return "", fmt.Errorf("%w: something listens on %s, in %s's port block: a quarantined tenant runs "+
					"nothing, so this is somebody else's process on its ports — purge deletes nothing while it is "+
					"there", jobs.ErrRefused, strings.Join(busy, ", "), p.t.Name)
			}
			return fmt.Sprintf("%d port(s) free", len(ports)), nil
		},
	})
}

// purgePorts is every service port the row records, deduplicated and sorted.
func purgePorts(b paths.Ports) []int {
	seen := map[int]bool{}
	var out []int
	for _, port := range []int{b.API, b.QdrantHTTP, b.QdrantGRPC, b.ESHTTP, b.ESTransport, b.PG} {
		if port > 0 && !seen[port] {
			seen[port] = true
			out = append(out, port)
		}
	}
	sort.Ints(out)
	return out
}

// addPurgeRecoveryProbe is step 2: the tree the row names is the tree
// decommission labelled for THIS tenant. Read at RUN time — a plan never
// reads the host.
//
// A tree that is not there at all passes: an earlier purge of this same row
// removed it (step 6) and failed after, and running purge again is how that
// job is finished. A tree that IS there must carry the note, naming this
// tenant and manifest; anything else is a directory the ctl did not quarantine
// for this row, and it is not deleted.
func (p *planner) addPurgeRecoveryProbe(dir string) {
	t := p.t
	note := filepath.Join(dir, recoveryFile)
	p.addFor("files", step{
		Kind: "probe", Title: "the quarantined tree's " + recoveryFile + " names " + t.Name, Targets: []string{note},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			files := sc.Ops.Drivers.Files()
			if present, err := pathExists(ctx, files, dir); err != nil {
				return "", err
			} else if !present {
				sc.Logf("%s is not there: an earlier purge of this row removed it", dir)
				return dir + " is already gone", nil
			}
			body, err := files.ReadFile(ctx, note)
			if errors.Is(err, fs.ErrNotExist) {
				return "", fmt.Errorf("%w: %s holds no %s: purge deletes only a tree decommission labelled. If an "+
					"earlier purge was interrupted inside this tree, inspect it and remove it by hand",
					jobs.ErrRefused, dir, recoveryFile)
			}
			if err != nil {
				return "", err
			}
			var rec struct {
				Tenant       string `json:"tenant"`
				ManifestName string `json:"manifest_name"`
			}
			if err := json.Unmarshal(body, &rec); err != nil {
				return "", fmt.Errorf("%w: %s is not the note decommission writes: %v", jobs.ErrRefused, note, err)
			}
			if rec.Tenant != t.Name || rec.ManifestName != t.ManifestName {
				return "", fmt.Errorf("%w: %s names tenant %q / manifest %q, not %q / %q: this tree is not the one "+
					"the row quarantined", jobs.ErrRefused, note, rec.Tenant, rec.ManifestName, t.Name, t.ManifestName)
			}
			return note + " names " + t.Name, nil
		},
	})
}

// addPurgeWorktree is step 3. The checkout goes with RemoveTree, and when a
// mirror is configured its administrative entry goes with `git worktree
// prune` — which is what RemoveWorktree runs once the directory is gone.
func (p *planner) addPurgeWorktree(worktree string) {
	mirror := p.op.deps.Mirror
	s := step{
		Kind: "fs", Title: "remove the worktree", Destructive: true, Targets: []string{worktree},
		Warnings: []string{purgeIrreversible, "code, not data: the tenant's checkout"},
	}
	if mirror != "" {
		s.WouldRun = []model.WouldRun{{Argv: []string{"/usr/bin/git", "-C", mirror, "worktree", "prune"}}}
		s.Warnings = append(s.Warnings, "then `git worktree prune` in the mirror, so it forgets the checkout too")
	} else {
		s.Warnings = append(s.Warnings, "no mirror is configured (ctl.env CTL_MIRROR): nothing is pruned")
	}
	s.Run = func(ctx context.Context, sc *jobs.StepContext) (string, error) {
		detail, err := purgeRemove(ctx, sc, worktree)
		if err != nil {
			return "", err
		}
		if mirror != "" {
			// The directory is gone, so this is the prune alone.
			if err := sc.Ops.Drivers.Git().RemoveWorktree(ctx, mirror, worktree); err != nil {
				return "", err
			}
			detail += "; mirror pruned"
		}
		return detail, nil
	}
	p.addFor("files", s)
}

// addPurgeUnits is step 4: the tenant's directory under the units directory,
// and any rendered unit file a decommission left behind.
//
// The rendered units are FLAT in the units directory (SYSTEMD_UNIT_PATH is not
// searched recursively), and decommission removes them for a systemd tenant —
// so for a tenant quarantined by this ctl the files are already gone and these
// removals are no-ops. They are here so that "everything of the tenant" is
// true even of a row quarantined by an older ctl.
func (p *planner) addPurgeUnits(unitsDir string) {
	target, qdrant, es, postgres, api, ui := render.UnitNames(p.t.Name)
	dir := filepath.Dir(unitsDir)
	files := []string{}
	for _, n := range []string{target, qdrant, es, postgres, api, ui} {
		files = append(files, filepath.Join(dir, n))
	}
	p.addFor("files", step{
		Kind: "fs", Title: "remove the units directory and any rendered unit file", Destructive: true,
		Targets:  append([]string{unitsDir}, files...),
		Warnings: []string{purgeIrreversible, "files the ctl rendered, under its own config tree"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			detail, err := purgeRemove(ctx, sc, unitsDir)
			if err != nil {
				return "", err
			}
			for _, f := range files {
				// Remove is idempotent: an absent unit file is not an error.
				if err := sc.Ops.Drivers.Files().Remove(ctx, f); err != nil {
					return "", err
				}
			}
			return detail + "; no rendered unit file left", nil
		},
	})
}

// addPurgeRemoval is one RemoveTree step (5 and 6).
func (p *planner) addPurgeRemoval(title, path, why string) {
	p.addFor("files", step{
		Kind: "fs", Title: title, Destructive: true, Targets: []string{path},
		Warnings: []string{purgeIrreversible, why},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return purgeRemove(ctx, sc, path)
		},
	})
}

// purgeRemove measures path, checkpoints what it is about to free, and removes
// it. An absent path is done (no checkpoint: nothing was removed by THIS job).
// A path measured by an earlier attempt of the same step is not measured again
// — what is left of a half-removed tree is not what the purge freed.
func purgeRemove(ctx context.Context, sc *jobs.StepContext, path string) (string, error) {
	files := sc.Ops.Drivers.Files()
	n, measured := freedRecorded(sc.Step, path)
	if !measured {
		size, present, err := treeBytes(ctx, files, path)
		if err != nil {
			return "", err
		}
		if !present {
			sc.Logf("%s is not there: nothing to remove", path)
			return path + " is already gone", nil
		}
		if err := sc.Checkpoint(purgeFreedID + strconv.FormatInt(size, 10) + ":" + path); err != nil {
			return "", err
		}
		n = size
	}
	if err := files.RemoveTree(ctx, path); err != nil {
		return "", err
	}
	return fmt.Sprintf("removed %s (%d bytes)", path, n), nil
}

// freedRecorded reads a `freed:<n>:<path>` checkpoint for path off one step.
func freedRecorded(st *model.Step, path string) (int64, bool) {
	if st == nil {
		return 0, false
	}
	for _, id := range st.ExternalIDs {
		if n, p, ok := parseFreed(id); ok && p == path {
			return n, true
		}
	}
	return 0, false
}

func parseFreed(id string) (int64, string, bool) {
	rest, ok := strings.CutPrefix(id, purgeFreedID)
	if !ok {
		return 0, "", false
	}
	num, path, ok := strings.Cut(rest, ":")
	if !ok {
		return 0, "", false
	}
	n, err := strconv.ParseInt(num, 10, 64)
	if err != nil {
		return 0, "", false
	}
	return n, path, true
}

// treeBytes is the apparent size of everything under path, summed through
// the Files driver (ReadDir + lstat): approximate by design — a file that
// vanishes or cannot be listed mid-walk is left out rather than failing a
// deletion over its accounting. present is false for an absent path.
func treeBytes(ctx context.Context, files jobs.Files, path string) (size int64, present bool, err error) {
	st, err := files.Stat(ctx, path)
	switch {
	case errors.Is(err, fs.ErrNotExist):
		return 0, false, nil
	case err != nil:
		return 0, false, fmt.Errorf("measuring %s: %w", path, err)
	case !st.IsDir:
		// A file, or a symlink the removal will refuse: its own size.
		return st.Size, true, nil
	}
	var walk func(dir string)
	walk = func(dir string) {
		ents, err := files.ReadDir(ctx, dir)
		if err != nil {
			return
		}
		for _, e := range ents {
			child := filepath.Join(dir, e.Name)
			if e.IsDir {
				walk(child)
				continue
			}
			if st, err := files.Stat(ctx, child); err == nil {
				size += st.Size
			}
		}
	}
	walk(path)
	return size, true, nil
}

// addPurgeRegistry is step 7, and the LAST: the row goes, and a production
// block leaves its tombstone.
func (p *planner) addPurgeRegistry(dir string, sandbox, keep bool) {
	t := p.t
	warnings := []string{"the row is deleted and dropped from display_order; `last_ops` is not written (there is " +
		"no row to write it on) — the job and its audit rows are the record"}
	if sandbox {
		warnings = append(warnings, "a selftest sandbox block: NO tombstone, because the sandbox allocator counts "+
			"tombstone bases as taken and there are only "+strconv.Itoa(registry.SandboxBlocks(paths.PortStride))+
			" sandbox blocks")
	} else {
		warnings = append(warnings, fmt.Sprintf("a tombstone {manifest_name %s, index %d, base %d} is written, so "+
			"the allocator never hands this port block out again", t.ManifestName, t.Ports.Index, t.Ports.Base))
	}
	p.add(step{
		Kind: "registry", Title: "delete " + t.Name + "'s registry row" + map[bool]string{true: "", false: " (a tombstone keeps its port block)"}[sandbox],
		Destructive: true, Targets: []string{t.Name},
		WouldWrite: []model.WouldWrite{{Path: p.oc.Roots.Registry(), Mode: "0660", Preview: ""}},
		Warnings:   warnings,
		Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			save := p.op.deps.SaveFleet
			if save == nil {
				return "", fmt.Errorf("%w: the registry writer is not wired", jobs.ErrRefused)
			}
			cur := sc.Ops.Fleet
			if cur == nil {
				return "", fmt.Errorf("%w: the engine loaded no registry under the locks", jobs.ErrRefused)
			}
			row := cur.Tenants[t.Name]
			if row == nil {
				p.fillPurgeResult(sc, nil, keep)
				return t.Name + "'s row is already gone", nil
			}
			// The row the locks loaded must still be the row this plan was made
			// from: quarantined, over the same tree.
			if row.State != registry.StateQuarantined || row.Quarantine == nil || row.Quarantine.Dir != dir {
				return "", fmt.Errorf("%w: %s's row is no longer quarantined over %s; refusing to delete it",
					jobs.ErrRefused, t.Name, dir)
			}
			var tomb *registry.Tombstone
			if !sandbox {
				at := row.Quarantine.At
				if at == "" {
					at = p.stampRFC3339(sc)
				}
				tb := registry.Tombstone{ManifestName: row.ManifestName, Index: row.Ports.Index, Base: row.Ports.Base,
					DecommissionedAt: at}
				dup := false
				for _, have := range cur.Tombstones {
					if have.Index == tb.Index && have.Base == tb.Base && have.ManifestName == tb.ManifestName {
						dup = true
					}
				}
				if !dup {
					cur.Tombstones = append(cur.Tombstones, tb)
				}
				tomb = &tb
			}
			delete(cur.Tenants, t.Name)
			order := make([]string, 0, len(cur.DisplayOrder))
			for _, n := range cur.DisplayOrder {
				if n != t.Name {
					order = append(order, n)
				}
			}
			cur.DisplayOrder = order
			if err := save(cur); err != nil {
				return "", err
			}
			p.fillPurgeResult(sc, tomb, keep)
			if tomb == nil {
				return t.Name + "'s row deleted; a sandbox block leaves no tombstone", nil
			}
			return fmt.Sprintf("%s's row deleted; tombstone index %d base %d", t.Name, tomb.Index, tomb.Base), nil
		},
	})
}

// fillPurgeResult assembles the job's result from what the removal steps
// checkpointed — this job's own steps, read through the job, so a resumed job
// reports the whole purge and not only the steps the last attempt ran.
func (p *planner) fillPurgeResult(sc *jobs.StepContext, tomb *registry.Tombstone, keep bool) {
	removed := []string{}
	var freed int64
	seen := map[string]bool{}
	note := func(ids []string) {
		for _, id := range ids {
			if n, path, ok := parseFreed(id); ok && !seen[path] {
				seen[path] = true
				removed = append(removed, path)
				freed += n
			}
		}
	}
	if sc != nil && sc.Job != nil {
		for _, st := range sc.Job.Steps {
			note(st.ExternalIDs)
		}
	}
	if sc != nil && sc.Step != nil {
		note(sc.Step.ExternalIDs)
	}
	p.result["removed"] = removed
	p.result["bytes_freed"] = freed
	p.result["archive_kept"] = keep
	if tomb == nil {
		p.result["tombstone"] = nil
		return
	}
	p.result["tombstone"] = map[string]any{
		"manifest_name": tomb.ManifestName, "index": tomb.Index, "base": tomb.Base,
		"decommissioned_at": tomb.DecommissionedAt,
	}
}

// sortedTenantNames is the fleet's row names, sorted (nil fleet: none).
func sortedTenantNames(f *registry.Fleet) []string {
	if f == nil {
		return nil
	}
	out := make([]string, 0, len(f.Tenants))
	for name := range f.Tenants {
		out = append(out, name)
	}
	sort.Strings(out)
	return out
}

func lastBackupBundle(t *registry.Tenant) string {
	if t == nil || t.LastBackup == nil {
		return ""
	}
	return t.LastBackup.Bundle
}

func quarantineBundle(t *registry.Tenant) string {
	if t == nil || t.Quarantine == nil {
		return ""
	}
	return string(t.Quarantine.Bundle)
}
