package ops

// The one thing a handover MOVES: a postgres data directory the taking account
// does not own.
//
// These tests exist because the second live handover of the hackathon tenant
// failed on 2026-09-17 with the tenant down, and nothing in this package could
// have caught it. postgres compares its data directory's st_uid with its own
// geteuid() and refuses when they differ — no mode and no ACL entry changes
// that — and the take started it anyway, waited the full three minutes, and
// reported `pg_isready … no response`.
//
// So the fixture below can express a directory owned by SOMEBODY ELSE
// (drivers.FakeOptions.FileOwners), and the assertions are about the things
// that decide whether a tenant comes back: the ORDER of the copy against the
// port proofs and the store starts, the two renames and the state between
// them, and whether the way back is exact.

import (
	"context"
	"errors"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// otherAccountUID is a uid that is not the one the fake host runs as
// (FakeOptions.FileUID defaults to 0). It stands for wilke on coconut: the
// account that created the tenant's postgres and that nobody here can chown
// away from.
const otherAccountUID = 3581

// pgFixture is a released tenant with a LOCAL postgres whose data directory
// belongs to another account — the hackathon shape, exactly.
//
// The tree is seeded with a `pgdata` under the bind source because that is the
// directory postgres actually inspects (PGDATA is `<bind>/pgdata`), and on
// coconut the two carry different modes and the same owner.
func pgFixture(t *testing.T, mutate func(*registry.Tenant), owners map[string]int) (jobs.Context, *drivers.Fake, pgPaths) {
	t.Helper()
	roots := paths.NewRoots("/rag", paths.Overrides{})
	tp := paths.TenantPaths(roots, "dev", "dev")
	pp := pgPaths{
		data:   tp.PostgresData,
		pgdata: filepath.Join(tp.PostgresData, "pgdata"),
		dir:    filepath.Dir(tp.PostgresData),
	}
	oc, fake := fixture(t, "dev", func(tn *registry.Tenant) {
		mutate(tn)
		withLocalPostgres(tn)
	})
	takeFixture(t, oc, fake)
	files := fake.FakeFiles()
	// The data directory as it is on the host: a `data` the group can write
	// and a 0700 `pgdata` inside it, both the OTHER account's.
	files.Dirs[pp.data] = 0o770
	files.Dirs[pp.pgdata] = 0o700
	files.Put(filepath.Join(pp.pgdata, "PG_VERSION"), []byte("16\n"), 0o600)
	files.Put(filepath.Join(pp.pgdata, "base", "1", "1259"), make([]byte, 8192), 0o600)
	if owners == nil {
		owners = map[string]int{pp.data: otherAccountUID}
	}
	for p, uid := range owners {
		files.Owners[p] = uid
	}
	// The password the postgres instance is started with, under both names.
	files.Put(tp.SecretsEnv, append(ledgerEnv(), []byte(
		"TENANT_PG_PASSWORD="+testSecret+"\n"+
			"APPTAINERENV_POSTGRES_PASSWORD="+testSecret+"\n")...), 0o640)
	return oc, fake, pp
}

type pgPaths struct{ data, pgdata, dir string }

// withLocalPostgres gives a fixture tenant the dedicated postgres instance
// `new-tenant.sh` gives a real one.
func withLocalPostgres(tn *registry.Tenant) {
	tn.Stores.Postgres = registry.Postgres{
		Kind: registry.PostgresKindLocal, Ownership: registry.OwnershipExclusive,
		Capabilities: registry.Capabilities{Stop: true, Purge: true, Restore: true, Snapshot: true},
		URL:          registry.NullString("postgresql://localhost:" + itoa(tn.Ports.PG)),
		Port:         registry.NullPort(tn.Ports.PG),
		Instance:     registry.NullString("postgres-" + tn.ManifestName),
		SIF:          registry.NullString("/rag/apptainer/images/postgres.sif"),
		DataDir:      registry.NullString(tn.DataDir + "/postgres"),
	}
}

// ---------------------------------------------------------------- the plan

// WHERE the migration sits is the fix. After the port proofs — a directory
// moved under processes the release did not manage to stop is the worst thing
// this job could do — and before the first store starts, because a postgres
// started on the old directory is the failure itself.
func TestTakeMigratesThePostgresDataDirectoryBetweenThePortProofsAndTheStores(t *testing.T) {
	oc, _, _ := pgFixture(t, released, nil)
	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	got := titles(p)

	migrate := indexOfStep(got, "migrate the postgres data directory")
	lastPort := -1
	for i, s := range got {
		if strings.Contains(s, "verify nothing listens") {
			lastPort = i
		}
	}
	firstStore := indexOfStep(got, "start the instance")
	sup := indexOfStep(got, "record supervisor: instance")
	switch {
	case migrate < 0 || lastPort < 0 || firstStore < 0 || sup < 0:
		t.Fatalf("steps =\n  %s", strings.Join(got, "\n  "))
	case migrate < lastPort:
		t.Errorf("the migration runs before the ports are proved free (%d < %d):\n  %s",
			migrate, lastPort, strings.Join(got, "\n  "))
	case migrate > firstStore:
		t.Errorf("a store starts before the data directory is migrated (%d > %d):\n  %s",
			migrate, firstStore, strings.Join(got, "\n  "))
	case migrate > sup:
		t.Errorf("the migration runs after the supervisor is recorded (%d > %d): nothing should be written to the "+
			"row before the directory the tenant will run on is in place", migrate, sup)
	}

	// The plan says what it is about to do to a data directory, and says the
	// two things an operator has to know afterwards.
	warnings := strings.Join(stepWarnings(p, "fs", "migrate the postgres data directory"), " | ")
	for _, want := range []string{"st_uid vs geteuid", "renamed aside and never written to again", "--commit"} {
		if !strings.Contains(warnings, want) {
			t.Errorf("the migration step does not warn about %q: %s", want, warnings)
		}
	}
}

// A tenant with no postgres of its own plans a SKIP with the reason on it, not
// a gap. The two are indistinguishable in a job log, and a handover is read
// out of its job log.
func TestTakeSkipsTheMigrationForATenantWithNoPostgresOfItsOwn(t *testing.T) {
	oc, fake := fixture(t, "dev", released)
	takeFixture(t, oc, fake)
	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	if !hasStep(p, "files", "skip the postgres data directory") {
		t.Fatalf("no skipped migration step:\n  %s", strings.Join(titles(p), "\n  "))
	}
	why := strings.Join(stepWarnings(p, "files", "skip the postgres data directory"), " ")
	if !strings.Contains(why, "runs no postgres server of its own") {
		t.Errorf("the skip does not say why: %q", why)
	}
}

// ---------------------------------------------------------------- the run

// The whole operation, in order: the names are checkpointed BEFORE the copy,
// the copy is made as this account, the original is renamed aside, the copy is
// renamed into place, and the row records where the original went.
func TestTheMigrationCopiesThenRenamesTwiceAndRecordsBothPaths(t *testing.T) {
	oc, fake, pp := pgFixture(t, released, nil)
	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	step, n := stepNamed(t, p, "migrate the postgres data directory")
	r := newRunner(oc, fake)

	log, err := r.run(step)
	if err != nil {
		t.Fatalf("the migration failed: %v", err)
	}
	if !strings.Contains(log, "migrated") {
		t.Errorf("the migration reported %q", log)
	}

	// The two names, recorded before anything was touched: a crash between the
	// renames leaves a step whose external ids say which directories to look
	// at, which is the difference between a recoverable state and a mystery.
	ids := r.externalIDs(n)
	copyPath, aside := idValue(t, ids, "pgcopy:"), idValue(t, ids, "pgaside:")
	if !strings.HasPrefix(filepath.Base(copyPath), "data.svcbvbrc-") {
		t.Errorf("the copy is named %q, want data.<account>-<ts>", copyPath)
	}
	if !strings.HasPrefix(filepath.Base(aside), "data.pre-handover-") {
		t.Errorf("the original is named %q, want data.pre-handover-<ts>", aside)
	}

	calls := strings.Join(fake.CallKeys(), "\n")
	cp := strings.Index(calls, "job.checkpoint(pgcopy:")
	tree := strings.Index(calls, "files.CopyTree(")
	if cp < 0 || tree < 0 || cp > tree {
		t.Errorf("the names were not checkpointed before the copy:\n%s", calls)
	}
	// Two renames, in the order that leaves a recoverable state: the original
	// goes aside first, so `data` is free for the copy. The other order would
	// need an atomic exchange, and there is none.
	r1 := strings.Index(calls, "files.Rename("+pp.data+","+aside+")")
	r2 := strings.Index(calls, "files.Rename("+copyPath+","+pp.data+")")
	if r1 < 0 || r2 < 0 || r1 > r2 {
		t.Errorf("the two renames did not happen in order (aside %d, into place %d):\n%s", r1, r2, calls)
	}

	// The tenant's data directory is now this account's, byte for byte, and
	// the original is untouched beside it.
	files := fake.FakeFiles()
	if st, err := files.Stat(context.Background(), pp.data); err != nil || st.UID != 0 {
		t.Errorf("%s = %+v (err %v), want this account's", pp.data, st, err)
	}
	if st, err := files.Stat(context.Background(), filepath.Join(aside, "pgdata")); err != nil || st.UID != otherAccountUID {
		t.Errorf("the original at %s was not left as it was: %+v (err %v)", aside, st, err)
	}
	if got := files.Content(filepath.Join(pp.data, "pgdata", "PG_VERSION")); string(got) != "16\n" {
		t.Errorf("the copy does not hold the original's files: PG_VERSION = %q", got)
	}
	if st, err := files.Stat(context.Background(), filepath.Join(pp.data, "pgdata")); err != nil || st.Mode != 0o700 {
		t.Errorf("pgdata's mode was not preserved: %+v (err %v)", st, err)
	}

	// …and the ROW says where the original is, which is what the abandon and
	// the operator both read.
	h := oc.Fleet.Tenants["dev"].Handover
	if h == nil || h.PostgresData == nil {
		t.Fatalf("the handover block records no postgres migration: %+v", h)
	}
	if h.PostgresData.PreHandover != aside || h.PostgresData.Copy != copyPath {
		t.Errorf("handover.postgres_data = %+v, want pre_handover %s and copy %s", h.PostgresData, aside, copyPath)
	}
	if h.PostgresData.MigratedAt == "" {
		t.Error("handover.postgres_data.migrated_at is empty")
	}
}

// A directory the taking account ALREADY owns is not copied. The tenants this
// control plane creates itself are in that state, and copying a postgres for
// them would be an outage's worth of IO for nothing.
func TestTheMigrationCopiesNothingWhenTheAccountAlreadyOwnsTheDirectory(t *testing.T) {
	oc, fake, _ := pgFixture(t, released, map[string]int{})
	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	step, _ := stepNamed(t, p, "migrate the postgres data directory")

	log, err := newRunner(oc, fake).run(step)
	if err != nil {
		t.Fatalf("the migration failed over a directory this account owns: %v", err)
	}
	if !strings.Contains(log, "no migration needed") {
		t.Errorf("the step reported %q, want it to say there was nothing to do", log)
	}
	if calls := strings.Join(fake.CallKeys(), "\n"); strings.Contains(calls, "files.CopyTree(") {
		t.Errorf("a directory this account owns was copied anyway:\n%s", calls)
	}
	if h := oc.Fleet.Tenants["dev"].Handover; h != nil && h.PostgresData != nil {
		t.Errorf("the row records a migration that did not happen: %+v", h.PostgresData)
	}
}

// A filesystem that cannot hold the copy is a refusal, not a half-written
// tree. The number in the message is what an operator acts on, so it is in the
// assertion.
func TestTheMigrationRefusesWhenThereIsNotRoomForTwoCopies(t *testing.T) {
	oc, fake, _ := pgFixture(t, released, nil)
	// Room for the tree but not for twice it: the copy would fit and the
	// database it becomes would not.
	fake.FakeFiles().Free = 9000
	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	step, _ := stepNamed(t, p, "migrate the postgres data directory")

	_, err := newRunner(oc, fake).run(step)
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("a take with no room = %v, want a refusal", err)
	}
	for _, want := range []string{"has to be COPIED", "needs 2\u00d7 that", "run the take again"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("the refusal does not mention %q: %v", want, err)
		}
	}
	if calls := strings.Join(fake.CallKeys(), "\n"); strings.Contains(calls, "files.Rename(") {
		t.Errorf("a refused migration renamed something:\n%s", calls)
	}
}

// The rollback is the way back out of a take that failed AFTER the move — the
// exact situation of 2026-09-17, where the API step failed and the engine
// unwound every step before it. It has to put the tenant back on the directory
// it was on, and that directory has never been written to.
func TestTheMigrationsRollbackPutsTheOriginalBack(t *testing.T) {
	oc, fake, pp := pgFixture(t, released, nil)
	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	step, n := stepNamed(t, p, "migrate the postgres data directory")
	r := newRunner(oc, fake)
	if _, err := r.run(step); err != nil {
		t.Fatalf("the migration failed: %v", err)
	}
	aside := idValue(t, r.externalIDs(n), "pgaside:")
	copyPath := idValue(t, r.externalIDs(n), "pgcopy:")

	log, err := r.rollback(step)
	if err != nil {
		t.Fatalf("the rollback failed: %v", err)
	}
	if !strings.Contains(log, "is back at "+pp.data) {
		t.Errorf("the rollback reported %q", log)
	}
	files := fake.FakeFiles()
	// The ORIGINAL is back, with its owner: this is the property that makes
	// the rollback exact rather than a restore.
	st, err := files.Stat(context.Background(), pp.data)
	if err != nil || st.UID != otherAccountUID {
		t.Errorf("%s = %+v (err %v), want the other account's original", pp.data, st, err)
	}
	if _, err := files.Stat(context.Background(), aside); err == nil {
		t.Errorf("%s is still there after the rollback: the original was not moved back", aside)
	}
	if _, err := files.Stat(context.Background(), copyPath); err != nil {
		t.Errorf("this account's copy was not put back at %s: %v", copyPath, err)
	}
	// …and the row no longer names a directory that is not there.
	if h := oc.Fleet.Tenants["dev"].Handover; h != nil && h.PostgresData != nil {
		t.Errorf("the rolled-back row still records a migration: %+v", h.PostgresData)
	}
}

// The crash between the two renames. It is the one state this operation can be
// interrupted in that is neither "before" nor "after", and the whole reason the
// names are checkpointed first: a re-run FINISHES it rather than starting over
// or refusing.
func TestTheMigrationFinishesAfterACrashBetweenTheTwoRenames(t *testing.T) {
	oc, fake, pp := pgFixture(t, released, nil)
	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	step, n := stepNamed(t, p, "migrate the postgres data directory")
	r := newRunner(oc, fake)
	if _, err := r.run(step); err != nil {
		t.Fatalf("the migration failed: %v", err)
	}
	copyPath, aside := idValue(t, r.externalIDs(n), "pgcopy:"), idValue(t, r.externalIDs(n), "pgaside:")

	// Put the host back into the mid-rename state: the original is aside, this
	// account's copy is under its own name, and `data` does not exist.
	files := fake.FakeFiles()
	if err := files.Rename(context.Background(), pp.data, copyPath); err != nil {
		t.Fatal(err)
	}

	// Reconcile says REDO — the work is not done and it can be decided — and
	// the re-run finishes the second rename.
	got, err := step.Reconcile(context.Background(), r.ctx(step))
	if err != nil || got != jobs.ReconcileRedo {
		t.Fatalf("reconcile over the mid-rename state = %v, %v; want redo", got, err)
	}
	log, err := r.run(step)
	if err != nil {
		t.Fatalf("the re-run did not finish the migration: %v", err)
	}
	if !strings.Contains(log, "migrated") {
		t.Errorf("the re-run reported %q", log)
	}
	if st, err := files.Stat(context.Background(), pp.data); err != nil || st.UID != 0 {
		t.Errorf("%s = %+v (err %v), want this account's copy in place", pp.data, st, err)
	}
	if _, err := files.Stat(context.Background(), aside); err != nil {
		t.Errorf("the original is no longer at %s: %v", aside, err)
	}
}

// A state the step cannot name is STUCK with all three paths in the message,
// never a guess. The directories in question are a tenant's database.
func TestTheMigrationIsStuckRatherThanGuessingOverAStateItCannotName(t *testing.T) {
	oc, fake, pp := pgFixture(t, released, nil)
	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	step, n := stepNamed(t, p, "migrate the postgres data directory")
	r := newRunner(oc, fake)
	if _, err := r.run(step); err != nil {
		t.Fatalf("the migration failed: %v", err)
	}
	aside := idValue(t, r.externalIDs(n), "pgaside:")

	// Somebody moved the original away and left `data` in place: neither
	// "done" nor "the crash between the renames".
	files := fake.FakeFiles()
	if err := files.Rename(context.Background(), aside, pp.dir+"/somewhere-else"); err != nil {
		t.Fatal(err)
	}
	if err := files.Rename(context.Background(), pp.data, pp.dir+"/gone"); err != nil {
		t.Fatal(err)
	}
	got, err := step.Reconcile(context.Background(), r.ctx(step))
	if got != jobs.ReconcileStuck || err == nil {
		t.Fatalf("reconcile over an unnameable state = %v, %v; want stuck with a reason", got, err)
	}
	for _, want := range []string{pp.data, aside, "is NOT there"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("the stuck reason does not name %q: %v", want, err)
		}
	}
}

// ---------------------------------------------------------------- release, abandon, commit

// The release is the last cheap moment to discover that the take cannot work,
// and the only moment at which the tenant is still up. It measures and warns;
// it copies nothing.
func TestTheReleaseWarnsAboutTheCopyTheTakeWillHaveToMake(t *testing.T) {
	oc, fake, _ := pgFixture(t, prepared, nil)
	p := planAs(t, oc, "wilke", "handover", map[string]any{"phase": "release"})
	step, _ := stepNamed(t, p, "check whether the take can own the postgres data directory")

	log, err := newRunner(oc, fake).run(step)
	if err != nil {
		t.Fatalf("the release precondition failed: %v", err)
	}
	for _, want := range []string{"owned by uid 3581", "will copy", "free"} {
		if !strings.Contains(log, want) {
			t.Errorf("the release does not report %q: %s", want, log)
		}
	}
	if calls := strings.Join(fake.CallKeys(), "\n"); strings.Contains(calls, "files.CopyTree(") {
		t.Errorf("the release copied something:\n%s", calls)
	}
}

// …and it REFUSES when the room is not there, because the alternative is
// discovering it with the tenant stopped and the take the only way back up.
func TestTheReleaseRefusesWhenTheTakeCouldNotMakeTheCopy(t *testing.T) {
	oc, fake, _ := pgFixture(t, prepared, nil)
	fake.FakeFiles().Free = 9000
	p := planAs(t, oc, "wilke", "handover", map[string]any{"phase": "release"})
	step, _ := stepNamed(t, p, "check whether the take can own the postgres data directory")

	_, err := newRunner(oc, fake).run(step)
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("a release with no room for the take's copy = %v, want a refusal", err)
	}
	for _, want := range []string{"postgres data owned by uid 3581", "needs 2\u00d7 that", "Free space before releasing"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("the refusal does not say %q: %v", want, err)
		}
	}
}

// The abandon hands the tenant back to the account that released it, and that
// account's postgres will not start on the take's copy either. So the names go
// back — and the copy that was serving during the soak is KEPT, under its own
// name, because it is divergent data and deleting it is nobody's call but the
// operator's.
func TestTheAbandonSwapsThePostgresDirectoryBack(t *testing.T) {
	oc, fake, pp := pgFixture(t, released, nil)
	// Run the take's migration first, so the row and the host are in the state
	// an abandon actually finds.
	takePlan := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	migrate, mn := stepNamed(t, takePlan, "migrate the postgres data directory")
	tr := newRunner(oc, fake)
	if _, err := tr.run(migrate); err != nil {
		t.Fatalf("the migration failed: %v", err)
	}
	copyPath, aside := idValue(t, tr.externalIDs(mn), "pgcopy:"), idValue(t, tr.externalIDs(mn), "pgaside:")

	// …then the abandon, as the account that released it, over a row that has
	// since been taken.
	tn := oc.Fleet.Tenants["dev"]
	tn.State, tn.Owner, tn.Supervisor = "active", "svcbvbrc", supervisorInstance
	tn.Handover.Phase = registry.HandoverTaken
	fake.FakeInstances().StopAll()
	fake.FakeProc().FreePort(tn.Ports.API)
	fake.FakeProc().FreePort(tn.Ports.PG)
	p := planAs(t, oc, "wilke", "handover", map[string]any{"phase": "abandon"})
	swap, _ := stepNamed(t, p, "put the postgres data directory back")

	// It runs BEFORE the row is put back: a row that said `manual` over a
	// directory the releasing account cannot start on would hand the operator
	// a tenant that refuses to come up.
	got := titles(p)
	if i, j := indexOfStep(got, "put the postgres data directory back"), indexOfStep(got, "put the row back"); i < 0 || j < 0 || i > j {
		t.Errorf("the directory is not put back before the row (%d, %d):\n  %s", i, j, strings.Join(got, "\n  "))
	}

	if _, err := newRunner(oc, fake).run(swap); err != nil {
		t.Fatalf("the abandon's swap failed: %v", err)
	}
	files := fake.FakeFiles()
	if st, err := files.Stat(context.Background(), pp.data); err != nil || st.UID != otherAccountUID {
		t.Errorf("%s = %+v (err %v), want the releasing account's original back", pp.data, st, err)
	}
	if _, err := files.Stat(context.Background(), aside); err == nil {
		t.Errorf("%s is still there: the original was not moved back", aside)
	}
	if _, err := files.Stat(context.Background(), copyPath); err != nil {
		t.Errorf("the soak's copy was deleted rather than kept at %s: %v", copyPath, err)
	}
}

// An abandon over a handover whose take moved nothing plans a skip that says
// so, rather than a step that would have nothing to rename.
func TestTheAbandonSkipsTheSwapWhenTheTakeMovedNothing(t *testing.T) {
	oc, fake := fixture(t, "dev", taken)
	fake.FakeInstances().StopAll()
	fake.FakeProc().FreePort(oc.Tenant.Ports.API)
	p := planAs(t, oc, "wilke", "handover", map[string]any{"phase": "abandon"})
	if !hasStep(p, "files", "skip the postgres data directory") {
		t.Fatalf("no skipped swap:\n  %s", strings.Join(titles(p), "\n  "))
	}
	why := strings.Join(stepWarnings(p, "files", "skip the postgres data directory"), " ")
	if !strings.Contains(why, "postgres_data is null") {
		t.Errorf("the skip does not say why: %q", why)
	}
}

// The commit KEEPS the original and says where it is — the last moment at
// which the registry can, because the commit is what clears the block. After
// that only doctor remembers.
func TestTheCommitNamesTheDirectoryItIsNotDeleting(t *testing.T) {
	oc, _, pp := pgFixture(t, taken, nil)
	aside := filepath.Join(pp.dir, "data.pre-handover-20260917T083000Z")
	tn := oc.Fleet.Tenants["dev"]
	tn.Handover.PostgresData = &registry.PostgresDataMigration{
		PreHandover: aside, Copy: filepath.Join(pp.dir, "data.svcbvbrc-20260917T083000Z"),
		MigratedAt: "2026-09-17T08:30:00Z",
	}
	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "commit"})

	if !warnsAbout(p, aside) {
		t.Errorf("the commit does not name the pre-handover directory: %v", p.Plan.Warnings)
	}
	if !warnsAbout(p, "pre_handover_copy_present") {
		t.Errorf("the commit does not say what will keep reporting it: %v", p.Plan.Warnings)
	}
	if !warnsAbout(p, "rm -rf "+aside) {
		t.Errorf("the commit does not say how to delete it: %v", p.Plan.Warnings)
	}
	if p.Result == nil {
		t.Fatal("the commit produced no result object")
	}
	if got := p.Result()["pre_handover_postgres_data"]; got != aside {
		t.Errorf("the commit's result carries %v, want the pre-handover path %s", got, aside)
	}
}

// ---------------------------------------------------------------- the readiness fast-fail

// A store that started and DIED is not going to answer, and waiting out the
// bound for it is what turned a five-second failure into a three-minute one —
// and then reported `pg_isready … no response`, which says nothing about why.
//
// The instance's own .err file said why the whole time.
func TestTheReadinessWaitStopsWhenTheInstanceIsGoneAndQuotesItsLog(t *testing.T) {
	oc, fake, _ := pgFixture(t, released, nil)
	// postgres started, wrote its one line and exited: apptainer's `instance
	// run` SUCCEEDED, the table is empty, and there is a log.
	ins := fake.FakeInstances()
	ins.ExitOnRun = map[string]string{"postgres-dev": strings.Join([]string{
		"2026-09-17 08:31:02.114 UTC [1] FATAL:  data directory \"/var/lib/postgresql/data/pgdata\" has wrong ownership",
		"2026-09-17 08:31:02.114 UTC [1] HINT:  The server must be started by the user that owns the data directory.",
		"child process exited with exit code 1",
	}, "\n")}
	// …and pg_isready answers what it answered on coconut.
	fake.FakePostgres().Readiness = map[string]bool{
		paths.TenantPaths(oc.Roots, "dev", "dev").PostgresRun: false,
	}

	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	r := newRunner(oc, fake)
	for _, s := range p.Steps {
		if strings.Contains(s.Plan.Title, "start the API detached") {
			_, err := r.run(s)
			if err == nil {
				t.Fatal("the API step succeeded over a postgres that never started")
			}
			// The CAUSE, out of the container's own log, and the hoisted
			// ownership line first: postgres prints two more lines after it.
			if !strings.Contains(err.Error(), "wrong ownership") {
				t.Errorf("the failure does not quote the postgres log: %v", err)
			}
			if !strings.Contains(err.Error(), "postgres-dev") ||
				!strings.Contains(err.Error(), "is not coming up") {
				t.Errorf("the failure does not say the instance is gone: %v", err)
			}
			return
		}
		if _, err := r.run(s); err != nil {
			t.Fatalf("step %d (%s): %v", s.Plan.N, s.Plan.Title, err)
		}
	}
	t.Fatal("the take plans no API start")
}

// ---------------------------------------------------------------- helpers

// stepNamed is the one step of a plan whose title contains substr, and its
// number. A test that silently matched two steps, or none, would assert
// nothing.
func stepNamed(t *testing.T, p *jobs.Planned, substr string) (jobs.Step, int) {
	t.Helper()
	var found []jobs.Step
	for _, s := range p.Steps {
		if strings.Contains(s.Plan.Title, substr) {
			found = append(found, s)
		}
	}
	if len(found) != 1 {
		t.Fatalf("%d steps match %q:\n  %s", len(found), substr, strings.Join(titles(p), "\n  "))
	}
	return found[0], found[0].Plan.N
}

// idValue is one checkpointed external id by prefix.
func idValue(t *testing.T, ids []string, prefix string) string {
	t.Helper()
	v, ok := externalIDValue(ids, prefix)
	if !ok {
		t.Fatalf("no %s external id in %v", prefix, ids)
	}
	return v
}
