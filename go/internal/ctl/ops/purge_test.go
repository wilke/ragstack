package ops

// PR-G1.4: `purge` — the one destructive op — and #693, the instance
// supervisor's stop step finally rolling back with a start.

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

const (
	purgeQDir     = "/rag/data/tenants/dev.quarantined-20260914T093000Z"
	purgeWorktree = "/rag/repos/tenants/dev"
	purgeBundles  = "/rag/backups/tenants/dev"
	purgeUnitsDir = "/rag/config/ctl/units/dev"
	purgeBundle   = purgeBundles + "/20260914T080000Z-backup"
)

// quarantinedRow is the row a decommission leaves: managed, quarantined, the
// block recorded. sandbox moves it onto the first selftest block.
func quarantinedRow(sandbox bool) func(*registry.Tenant) {
	return func(tn *registry.Tenant) {
		decomBackedUp(tn)
		if sandbox {
			b := paths.BlockAt(paths.SelftestBase, 0, 0)
			b.Index = registry.SandboxIndexBase
			tn.Ports = b
		}
		tn.State, tn.DesiredBoot = registry.StateQuarantined, "disabled"
		tn.Quarantine = &registry.Quarantine{Dir: purgeQDir, At: "2026-09-14T09:30:00Z",
			JobID: "01ARZ3NDEKTSV4RRFFQ69G5FAV", Bundle: registry.NullString(purgeBundle)}
	}
}

// purgeFixture is a quarantined `dev` on a host that holds every tree a purge
// removes — and, beside them, the LIVE data path the fixture seeds, which no
// purge may touch.
func purgeFixture(t *testing.T, sandbox bool) (jobs.Context, *drivers.Fake) {
	t.Helper()
	oc, fake := fixture(t, "dev", quarantinedRow(sandbox))
	// A quarantined tenant runs nothing.
	fake.FakeProc().Ports = map[int]bool{}
	ff := fake.FakeFiles()
	note, _ := json.Marshal(map[string]any{"tenant": "dev", "manifest_name": oc.Tenant.ManifestName})
	ff.Put(purgeQDir+"/"+recoveryFile, note, 0o640)
	ff.Put(purgeQDir+"/qdrant/storage/segment", make([]byte, 1000), 0o640)
	ff.Put(purgeQDir+"/config/tenant.env", []byte("LOG_LEVEL=INFO\n"), 0o640)
	ff.Put(purgeWorktree+"/python/main.py", make([]byte, 10), 0o640)
	ff.Put(purgeBundle+"/SHA256SUMS", make([]byte, 100), 0o640)
	ff.Put(purgeBundle+".tar", make([]byte, 200), 0o640)
	ff.Put(purgeUnitsDir+"/override.conf", make([]byte, 5), 0o640)
	return oc, fake
}

// gone fails unless nothing at or under path is left on the fake host.
func goneFrom(t *testing.T, fake *drivers.Fake, path string) {
	t.Helper()
	for _, p := range fake.FakeFiles().Paths() {
		if p == path || strings.HasPrefix(p, path+"/") {
			t.Errorf("%s survived the purge (%s)", path, p)
			return
		}
	}
}

func TestPurgePlansTheStepsInOrderWithTheRowLast(t *testing.T) {
	oc, _ := purgeFixture(t, false)
	p := plan(t, oc, "purge", nil)
	want := []string{
		"probe: nothing listens on any port of the block",
		"probe: the quarantined tree's RECOVERY.json names dev",
		"fs: remove the worktree",
		"fs: remove the units directory and any rendered unit file",
		"fs: remove the archive (every bundle and .tar)",
		"fs: remove the quarantined data tree",
		"registry: delete dev's registry row (a tombstone keeps its port block)",
	}
	if got := titles(p); !reflect.DeepEqual(got, want) {
		t.Fatalf("steps =\n  %s\nwant\n  %s", strings.Join(got, "\n  "), strings.Join(want, "\n  "))
	}
	// Every removal says it cannot be undone, and none has a rollback.
	for _, s := range p.Steps[2:6] {
		if s.Rollback != nil {
			t.Errorf("step %d (%s) has a rollback; a deletion cannot have one", s.Plan.N, s.Plan.Title)
		}
		if !s.Plan.Destructive || !strings.Contains(strings.Join(s.Plan.Warnings, " "), "IRREVERSIBLE") {
			t.Errorf("step %d (%s) does not say it is irreversible: %v", s.Plan.N, s.Plan.Title, s.Plan.Warnings)
		}
	}
	if !p.Plan.RequiresConfirm || string(p.Plan.ConfirmValue) != "dev" {
		t.Errorf("confirm = %v / %q, want the tenant name", p.Plan.RequiresConfirm, p.Plan.ConfirmValue)
	}
	all := strings.Join(p.Plan.Warnings, " ")
	if !strings.Contains(all, "Nothing of the tenant remains except the tombstone") ||
		!strings.Contains(all, "and the archive") {
		t.Errorf("the plan does not say plainly what goes: %v", p.Plan.Warnings)
	}
	// keep_archive turns the archive's removal into a skipped step, and says so.
	kp := plan(t, oc, "purge", map[string]any{"keep_archive": true})
	if got := titles(kp)[4]; got != "fs: keep the archive "+purgeBundles {
		t.Errorf("keep_archive step 5 = %q", got)
	}
	if all := strings.Join(kp.Plan.Warnings, " "); !strings.Contains(all, "the archive is kept") {
		t.Errorf("keep_archive plan warnings: %v", kp.Plan.Warnings)
	}
}

func TestPurgeRefusesWhatItWasNotBuiltToDelete(t *testing.T) {
	cases := map[string]func(*registry.Tenant){
		"an ACTIVE managed row": managed,
		"a MANUAL row":          func(*registry.Tenant) {},
		"a quarantined row with no quarantine.dir": func(tn *registry.Tenant) {
			quarantinedRow(false)(tn)
			tn.Quarantine.Dir = ""
		},
		"a quarantined row owned by another account": func(tn *registry.Tenant) {
			quarantinedRow(false)(tn)
			tn.Owner = "wilke"
		},
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			oc, _ := fixture(t, "dev", mutate)
			err := planErr(t, oc, "purge", nil)
			if !errors.Is(err, jobs.ErrRefused) {
				t.Fatalf("purge of %s = %v, want ErrRefused", name, err)
			}
		})
	}
}

// Another row that names a bundle under this tenant's backup directory as its
// recovery point would lose it: refused, unless the archive is kept.
func TestPurgeRefusesToDeleteAnotherRowsRecoveryPoint(t *testing.T) {
	oc, _ := purgeFixture(t, false)
	oc.Fleet.Tenants["demo"].LastBackup = &registry.BackupRecord{Bundle: purgeBundle,
		At: "2026-09-14T08:00:00Z", Kind: "backup", Scope: fullScope}
	err := planErr(t, oc, "purge", nil)
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "demo") {
		t.Fatalf("purge over another row's recovery point = %v", err)
	}
	plan(t, oc, "purge", map[string]any{"keep_archive": true}) // allowed: nothing under it is deleted
}

// The whole purge on the fake host, production block: every tree gone, the
// live path untouched, the row gone, a tombstone that validates and that the
// allocator steps over.
func TestPurgeRemovesEverythingButATombstone(t *testing.T) {
	oc, fake := purgeFixture(t, false)
	before := *oc.Tenant
	noteSize := int64(len(fake.FakeFiles().Content(purgeQDir + "/" + recoveryFile)))
	p := plan(t, oc, "purge", nil)
	r := newRunner(oc, fake)
	r.runAll(t, p)

	for _, path := range []string{purgeQDir, purgeWorktree, purgeBundles, purgeUnitsDir} {
		goneFrom(t, fake, path)
	}
	if fake.FakeFiles().Content("/rag/data/tenants/dev/config/tenant.env") == nil {
		t.Error("the purge touched the LIVE data path /rag/data/tenants/dev")
	}
	// RemoveTree in step order, and the mirror pruned after the worktree went.
	var trees []string
	for _, c := range fake.Calls() {
		if c.Key() == "files.RemoveTree" {
			trees = append(trees, c.Args[0])
		}
	}
	if want := []string{purgeWorktree, purgeUnitsDir, purgeBundles, purgeQDir}; !reflect.DeepEqual(trees, want) {
		t.Errorf("RemoveTree calls = %v, want %v", trees, want)
	}
	if fake.Count("git.RemoveWorktree") != 1 {
		t.Errorf("the mirror was not pruned: %v", fake.CallKeys())
	}

	f := oc.Fleet
	if _, ok := f.Tenants["dev"]; ok {
		t.Fatal("the row survived the purge")
	}
	for _, n := range f.DisplayOrder {
		if n == "dev" {
			t.Errorf("display_order still names dev: %v", f.DisplayOrder)
		}
	}
	want := registry.Tombstone{ManifestName: before.ManifestName, Index: before.Ports.Index, Base: before.Ports.Base,
		DecommissionedAt: "2026-09-14T09:30:00Z"}
	if len(f.Tombstones) != 1 || f.Tombstones[0] != want {
		t.Fatalf("tombstones = %+v, want [%+v]", f.Tombstones, want)
	}
	if err := f.ValidateContract(); err != nil {
		t.Errorf("the purged registry does not validate: %v", err)
	}
	if err := f.Validate(); err != nil {
		t.Errorf("the purged registry breaks the allocator's invariants: %v", err)
	}
	if idx, base := registry.Allocate(f); idx == want.Index || base == want.Base {
		t.Errorf("Allocate handed out the tombstoned block again: index %d base %d", idx, base)
	}

	res := p.Result()
	removed, _ := res["removed"].([]string)
	if wantR := []string{purgeWorktree, purgeUnitsDir, purgeBundles, purgeQDir}; !reflect.DeepEqual(removed, wantR) {
		t.Errorf("result removed = %v, want %v", removed, wantR)
	}
	// 10 (worktree) + 5 (units) + 100 + 200 (archive) + the data tree.
	if got, want := res["bytes_freed"].(int64), int64(315+1000+len("LOG_LEVEL=INFO\n"))+noteSize; got != want {
		t.Errorf("bytes_freed = %d, want %d", got, want)
	}
	if res["archive_kept"] != false {
		t.Errorf("archive_kept = %v", res["archive_kept"])
	}
	tomb, _ := res["tombstone"].(map[string]any)
	if tomb == nil || tomb["index"] != want.Index || tomb["base"] != want.Base {
		t.Errorf("result tombstone = %v", res["tombstone"])
	}
	// purge is not recorded in last_ops: there is no row to record it on, and
	// nothing else in the fleet grew one.
	for name, row := range f.Tenants {
		if _, ok := row.LastOps["purge"]; ok {
			t.Errorf("%s records a purge in last_ops", name)
		}
	}
}

// A sandbox block leaves NO tombstone: the sandbox allocator counts tombstone
// bases as taken, and a selftest that left one per run would run out of
// blocks in five.
func TestPurgeOfASandboxWritesNoTombstone(t *testing.T) {
	oc, fake := purgeFixture(t, true)
	p := plan(t, oc, "purge", nil)
	newRunner(oc, fake).runAll(t, p)
	if _, ok := oc.Fleet.Tenants["dev"]; ok {
		t.Fatal("the sandbox row survived")
	}
	if len(oc.Fleet.Tombstones) != 0 {
		t.Fatalf("a sandbox purge wrote tombstones: %+v", oc.Fleet.Tombstones)
	}
	if p.Result()["tombstone"] != nil {
		t.Errorf("result tombstone = %v, want null", p.Result()["tombstone"])
	}
	if _, base, err := registry.AllocateSandbox(oc.Fleet); err != nil || base != paths.SelftestBase {
		t.Errorf("the sandbox block is not free again: base %d, %v", base, err)
	}
}

func TestPurgeKeepArchiveKeepsTheBundles(t *testing.T) {
	oc, fake := purgeFixture(t, false)
	p := plan(t, oc, "purge", map[string]any{"keep_archive": true})
	newRunner(oc, fake).runAll(t, p)
	for _, path := range []string{purgeBundle + "/SHA256SUMS", purgeBundle + ".tar"} {
		if fake.FakeFiles().Content(path) == nil {
			t.Errorf("--keep-archive deleted %s", path)
		}
	}
	goneFrom(t, fake, purgeQDir)
	goneFrom(t, fake, purgeWorktree)
	res := p.Result()
	if res["archive_kept"] != true {
		t.Errorf("archive_kept = %v", res["archive_kept"])
	}
	for _, r := range res["removed"].([]string) {
		if strings.HasPrefix(r, purgeBundles) {
			t.Errorf("result says it removed %s", r)
		}
	}
	if _, ok := oc.Fleet.Tenants["dev"]; ok {
		t.Error("the row survived")
	}
}

// A purge that fails part way is finished by running it again: every removal
// is idempotent, and the row — the last step — is still there to plan from.
func TestPurgeFinishesWhenRunAgainAfterAMidJobFailure(t *testing.T) {
	oc, fake := purgeFixture(t, false)
	key := "files.RemoveTree:" + purgeQDir
	fake.Fail(key, errors.New("EIO"))
	p := plan(t, oc, "purge", nil)
	r := newRunner(oc, fake)
	failedAt := 0
	for _, s := range p.Steps {
		if _, err := r.run(s); err != nil {
			failedAt = s.Plan.N
			break
		}
	}
	if failedAt != 6 {
		t.Fatalf("the injected failure stopped the job at step %d, want 6", failedAt)
	}
	if row := oc.Fleet.Tenants["dev"]; row == nil || row.State != registry.StateQuarantined {
		t.Fatalf("the row moved before the data tree went: %+v", row)
	}
	goneFrom(t, fake, purgeWorktree)
	goneFrom(t, fake, purgeBundles)

	// Run it again: a new plan from the same row, every step from the top.
	fake.Fail(key, nil)
	p2 := plan(t, oc, "purge", nil)
	newRunner(oc, fake).runAll(t, p2)
	goneFrom(t, fake, purgeQDir)
	if _, ok := oc.Fleet.Tenants["dev"]; ok {
		t.Fatal("the second run did not delete the row")
	}
	if len(oc.Fleet.Tombstones) != 1 {
		t.Errorf("tombstones = %+v, want exactly one", oc.Fleet.Tombstones)
	}

	// And the same run resumed in place (the engine's resume re-runs the
	// interrupted step on its own step record): the measured size is not
	// measured again from what is left.
	oc3, fake3 := purgeFixture(t, false)
	fake3.Fail(key, errors.New("EIO"))
	p3 := plan(t, oc3, "purge", nil)
	r3 := newRunner(oc3, fake3)
	for _, s := range p3.Steps {
		if _, err := r3.run(s); err != nil {
			break
		}
	}
	fake3.Fail(key, nil)
	for _, s := range p3.Steps[5:] {
		if _, err := r3.run(s); err != nil {
			t.Fatalf("resumed step %d: %v", s.Plan.N, err)
		}
	}
	if got := r3.externalIDs(6); len(got) != 1 || !strings.HasPrefix(got[0], purgeFreedID) {
		t.Errorf("step 6 checkpoints = %v, want one freed: record", got)
	}
	if removed := p3.Result()["removed"].([]string); len(removed) != 4 {
		t.Errorf("the resumed job reports removed = %v, want all four trees", removed)
	}
}

// The data tree goes and the registry write then fails: running purge again
// finds the tree already gone (step 2 passes over an absent tree) and deletes
// the row.
func TestPurgeFinishesWhenTheRegistryWriteFailedAfterTheTreeWent(t *testing.T) {
	oc, fake := purgeFixture(t, false)
	d := testDeps(oc)
	good := d.SaveFleet
	d.SaveFleet = func(*registry.Fleet) error { return errors.New("registry.json: no space left on device") }
	op, _ := NewRegistry(d).Lookup("purge")
	p, err := op.Plan(context.Background(), oc, nil)
	if err != nil {
		t.Fatal(err)
	}
	r := newRunner(oc, fake)
	for _, s := range p.Steps {
		if _, err := r.run(s); err != nil {
			if s.Plan.Kind != "registry" {
				t.Fatalf("step %d (%s) failed: %v", s.Plan.N, s.Plan.Title, err)
			}
			break
		}
	}
	goneFrom(t, fake, purgeQDir)
	// The failed save mutated the in-memory fleet the step was handed; the
	// engine reloads the registry for the next job, and so does this test.
	oc2, _ := purgeFixture(t, false)
	oc2.Fleet.Generation = oc.Fleet.Generation
	d.SaveFleet = good
	op, _ = NewRegistry(d).Lookup("purge")
	p2, err := op.Plan(context.Background(), jobs.Context{Roots: oc2.Roots, Fleet: oc2.Fleet, Tenant: oc2.Tenant,
		Drivers: fake, Now: oc2.Now, Redactor: oc2.Redactor, Doctor: oc2.Doctor}, nil)
	if err != nil {
		t.Fatal(err)
	}
	oc2.Drivers = fake
	r2 := newRunner(oc2, fake)
	r2.runAll(t, p2)
	if _, ok := oc2.Fleet.Tenants["dev"]; ok {
		t.Fatal("the second run did not delete the row")
	}
}

// RECOVERY.json is the label: a tree whose note names another tenant is not
// deleted, and the job stops before the first removal.
func TestPurgeRefusesATreeWhoseRecoveryNoteNamesSomebodyElse(t *testing.T) {
	oc, fake := purgeFixture(t, false)
	fake.FakeFiles().Put(purgeQDir+"/"+recoveryFile, []byte(`{"tenant":"demo","manifest_name":"demo"}`), 0o640)
	p := plan(t, oc, "purge", nil)
	r := newRunner(oc, fake)
	r.runAll(t, &jobs.Planned{Steps: p.Steps[:1]})
	if _, err := r.run(p.Steps[1]); !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("the recovery probe = %v, want ErrRefused", err)
	}
	// And a tree with no note at all.
	oc2, fake2 := purgeFixture(t, false)
	_ = fake2.FakeFiles().Remove(context.Background(), purgeQDir+"/"+recoveryFile)
	p2 := plan(t, oc2, "purge", nil)
	if _, err := newRunner(oc2, fake2).run(p2.Steps[1]); !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("the recovery probe over a tree with no note = %v, want ErrRefused", err)
	}
	if fake.Count("files.RemoveTree") != 0 || fake2.Count("files.RemoveTree") != 0 {
		t.Error("a refused probe was followed by a removal")
	}
}

// Something listening on the block refuses the purge at step 1.
func TestPurgeRefusesWhileAnythingListensOnTheBlock(t *testing.T) {
	oc, fake := purgeFixture(t, false)
	fake.FakeProc().Ports[oc.Tenant.Ports.ESHTTP] = true
	p := plan(t, oc, "purge", nil)
	if _, err := newRunner(oc, fake).run(p.Steps[0]); !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("the port probe = %v, want ErrRefused", err)
	}
}

// The driver's bundle-id grammar is the args grammar's, byte for byte.
func TestTheDriversBundleIDPatternIsTheArgsGrammars(t *testing.T) {
	if drivers.BundleIDPattern != patBundleID {
		t.Errorf("drivers.BundleIDPattern %q != patBundleID %q", drivers.BundleIDPattern, patBundleID)
	}
}

// ---------------------------------------------------------------- #693

// A fenced backup of an instance tenant that fails AFTER the fence is rolled
// back the way the engine does it — newest first, every succeeded step with a
// rollback — and the API stop's rollback spawns the API again and waits for it
// to listen. Before #693 the stop had no rollback and the tenant stayed down
// until an operator ran `tenant start`.
func TestAFailedFencedBackupOfAnInstanceTenantStartsTheAPIAgain(t *testing.T) {
	oc, fake := instanceFixture(t)
	newRunner(oc, fake).runAll(t, plan(t, oc, "start", nil))
	if !fake.FakeProc().Ports[oc.Tenant.Ports.API] {
		t.Fatal("the fixture API did not come up")
	}
	fake.Clear()
	fake.Fail("qdrant.Snapshot", errors.New("qdrant: 500"))

	p := plan(t, oc, "backup", map[string]any{"fence": true})
	r := newRunner(oc, fake)
	failed, fenced := -1, false
	for i, s := range p.Steps {
		if strings.HasPrefix(s.Plan.Title, "fence verify") {
			fenced = true
		}
		if _, err := r.run(s); err != nil {
			failed = i
			break
		}
	}
	if failed < 0 || !fenced {
		t.Fatalf("the injected failure did not land after the fence (failed %d, fenced %v)", failed, fenced)
	}
	if fake.FakeProc().Ports[oc.Tenant.Ports.API] {
		t.Fatal("the API is listening although the fence stopped it")
	}
	spawnsBefore := fake.Count("proc.Spawn")
	for i := failed; i >= 0; i-- {
		s := p.Steps[i]
		st := r.steps[s.Plan.N]
		if i == failed && (st == nil || len(st.ExternalIDs) == 0) {
			continue
		}
		if s.Rollback == nil {
			continue
		}
		if _, err := r.rollback(s); err != nil {
			t.Fatalf("rollback of step %d (%s): %v", s.Plan.N, s.Plan.Title, err)
		}
	}
	if fake.Count("proc.Spawn") != spawnsBefore+1 {
		t.Errorf("the rollback spawned the API %d time(s), want once:\n%s", fake.Count("proc.Spawn")-spawnsBefore,
			strings.Join(fake.CallKeys(), "\n"))
	}
	if !fake.FakeProc().Ports[oc.Tenant.Ports.API] {
		t.Error("after the rollback nothing listens on the API port: the tenant is still down")
	}
}

// Rolling back a stop of an API that was NOT running starts nothing.
func TestTheInstanceStopRollbackStartsOnlyWhatItStopped(t *testing.T) {
	oc, fake := instanceFixture(t)         // the pidfile names pid 4242, which is not running
	fake.FakeProc().Ports = map[int]bool{} // and nothing listens: the tenant is down
	p := plan(t, oc, "stop", map[string]any{"force": true})
	r := newRunner(oc, fake)
	r.runAll(t, p)
	for i := len(p.Steps) - 1; i >= 0; i-- {
		if p.Steps[i].Rollback != nil {
			if _, err := r.rollback(p.Steps[i]); err != nil {
				t.Fatalf("rollback of step %d (%s): %v", p.Steps[i].Plan.N, p.Steps[i].Plan.Title, err)
			}
		}
	}
	if n := fake.Count("proc.Spawn"); n != 0 {
		t.Errorf("rolling back a stop of a dead API spawned it %d time(s)", n)
	}
	if n := fake.Count("instances.Run"); n != 0 {
		t.Errorf("rolling back a stop of stopped instances ran %d instance(s)", n)
	}
}
