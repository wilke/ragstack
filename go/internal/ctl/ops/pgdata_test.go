package ops

// The one thing a handover MOVES: a tenant's postgres.
//
// These tests exist because the second live handover of `hackathon` failed on
// 2026-09-17 with the tenant down — and because the first fix for it, copying
// the data directory as the taking account, could not have worked either. On
// the live host `pgdata` is 0700 wilke with a POSIX ACL granting svcbvbrc rwx
// and a mask of `---`, so that named-user entry has an effective permission of
// nothing; and the mask cannot be widened, because it IS the group mode bits
// and postgres refuses a PGDATA that has any. A file-by-file copy could not
// read one byte of it.
//
// So the migration is logical: the release dumps, the take initialises a
// cluster of its own and restores into it. What these tests pin is therefore
// not "were the bytes copied" but the things that decide whether a tenant comes
// back — the ORDER of the dump against the two stops, the order of the swap
// against the port proofs and the store starts, the two renames and the state
// between them, the row counts that prove the restore, and whether the way back
// is exact.

import (
	"context"
	"errors"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// otherAccountUID is a uid that is not the one the fake host runs as
// (FakeOptions.FileUID defaults to 0). It stands for wilke on coconut: the
// account that created the tenant's cluster, and that nobody here can chown
// away from.
const otherAccountUID = 3581

// pgPaths are the names this whole file is about.
type pgPaths struct{ data, dir, run string }

// pgSpec is how the pg tools reach the fixture tenant's server.
func pgSpec(pp pgPaths) jobs.PostgresSpec {
	return jobs.PostgresSpec{
		SIF: "/rag/apptainer/images/postgres.sif", RunDir: pp.run, DB: "dev", User: "dev", Port: 24045,
	}
}

// pgFixture is a tenant with a LOCAL postgres whose cluster belongs to another
// account — the hackathon shape, exactly.
//
// Note what is NOT seeded: any file under `data`. The taking account cannot
// read the cluster on the real host, so nothing here may depend on reading it,
// and a fixture that laid files out inside it would quietly permit exactly the
// design that could not work.
func pgFixture(t *testing.T, mutate func(*registry.Tenant), owners map[string]int) (jobs.Context, *drivers.Fake, pgPaths) {
	t.Helper()
	roots := paths.NewRoots("/rag", paths.Overrides{})
	tp := paths.TenantPaths(roots, "dev", "dev")
	pp := pgPaths{data: tp.PostgresData, dir: filepath.Dir(tp.PostgresData), run: tp.PostgresRun}
	oc, fake := fixtureOpts(t, "dev", func(tn *registry.Tenant) {
		mutate(tn)
		withLocalPostgres(tn)
	}, func(o *drivers.FakeOptions) {
		// What this tenant's database holds, as the release will find it.
		o.PostgresContents = map[string]jobs.PostgresCensus{
			pp.run: {
				SizeBytes: 48 << 20,
				Tables:    map[string]int64{"public.chunks": 88_000, "public.jobs": 12},
				// What the live hackathon cluster is, and what the take has to
				// reproduce: a dump and a restore do NOT carry these, and the
				// fake's initdb produces SQL_ASCII/C unless it is told.
				Encoding: "UTF8", Collate: "en_US.utf8", Ctype: "en_US.utf8",
				// The cluster holds the tenant's database and initdb's own
				// `postgres`, and one login role — which is what the live one
				// holds, and the boundary a single-database dump moves.
				Databases: []string{"dev", "postgres"}, Roles: []string{"dev"},
			},
		}
		o.FileOwners = owners
		if owners == nil {
			// The cluster directory is the OTHER account's, which is the whole
			// reason a handover has to do anything here at all.
			o.FileOwners = map[string]int{pp.data: otherAccountUID}
		}
	})
	takeFixture(t, oc, fake)
	// Starting an instance BINDS its port on this fake host, so the readiness
	// gates of a whole take answer a fact rather than hanging on a fixture
	// that never listens.
	ins := fake.FakeInstances()
	ins.BindInstancePort("qdrant-"+oc.Tenant.ManifestName, oc.Tenant.Ports.QdrantHTTP)
	ins.BindInstancePort("elasticsearch-"+oc.Tenant.ManifestName, oc.Tenant.Ports.ESHTTP)
	ins.BindInstancePort("postgres-"+oc.Tenant.ManifestName, oc.Tenant.Ports.PG)
	files := fake.FakeFiles()
	files.Dirs[pp.dir] = 0o2770
	files.Dirs[pp.data] = 0o770
	files.Dirs[pp.run] = 0o2770
	files.Put(tp.SecretsEnv, append(ledgerEnv(), []byte(
		"TENANT_PG_PASSWORD="+testSecret+"\n"+
			"APPTAINERENV_POSTGRES_PASSWORD="+testSecret+"\n")...), 0o640)
	return oc, fake, pp
}

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

// ---------------------------------------------------------------- the release

// The dump has exactly one window: after the API stop (nothing is writing) and
// before the postgres stop (there is a server to dump). One step either side,
// and both of them are in this assertion.
func TestTheReleaseDumpsBetweenTheAPIStopAndThePostgresStop(t *testing.T) {
	oc, _, _ := pgFixture(t, prepared, nil)
	p := planAs(t, oc, "wilke", "handover", map[string]any{"phase": "release"})
	got := titles(p)

	apiStop := indexOfStep(got, "stop the hand-started API")
	dump := indexOfStep(got, "dump this tenant's postgres")
	pgStop := indexOfStep(got, "stop the instance postgres-dev")
	cleanup := indexOfStep(got, "remove the stale postgres socket")
	switch {
	case apiStop < 0 || dump < 0 || pgStop < 0 || cleanup < 0:
		t.Fatalf("steps =\n  %s", strings.Join(got, "\n  "))
	case !(apiStop < dump && dump < pgStop):
		t.Errorf("the dump is not in its window (api stop %d, dump %d, postgres stop %d):\n  %s",
			apiStop, dump, pgStop, strings.Join(got, "\n  "))
	case cleanup < pgStop:
		t.Errorf("the socket cleanup runs before the server that owns those files is stopped (%d < %d)",
			cleanup, pgStop)
	case cleanup < indexOfStep(got, "verify nothing listens on 24045"):
		// …and after the port is PROVED free, not merely after the stop
		// returned: removing a live server's socket is how you get a postgres
		// that is up and unreachable.
		t.Errorf("the socket cleanup runs before the postgres port is proved free:\n  %s",
			strings.Join(got, "\n  "))
	}
}

// What the release writes, and what it records: an archive the other account
// can read, its checksum, and the exact row count of every table.
func TestTheReleaseRecordsTheDumpItsChecksumAndEveryTablesRowCount(t *testing.T) {
	oc, fake, pp := pgFixture(t, prepared, nil)
	p := planAs(t, oc, "wilke", "handover", map[string]any{"phase": "release"})
	r := newRunner(oc, fake)
	// Through the plan, not straight to the step: the dump records itself in
	// the handover block, and it is the REGISTRY step three earlier that
	// creates one.
	runUpTo(t, r, p, "dump this tenant's postgres")
	step, n := stepNamed(t, p, "dump this tenant's postgres")

	log, err := r.run(step)
	if err != nil {
		t.Fatalf("the dump failed: %v", err)
	}
	if !strings.Contains(log, "2 table(s)") {
		t.Errorf("the dump reported %q", log)
	}

	dump := idValue(t, r.externalIDs(n), "pgdump:")
	if filepath.Dir(dump) != pp.dir || !strings.HasPrefix(filepath.Base(dump), "handover-") {
		t.Errorf("the dump landed at %q, want <data_dir>/postgres/handover-<ts>.dump", dump)
	}
	// 0640: the taking account cannot read the CLUSTER, and this file is what
	// works around that. A mode without the group bit would make the whole
	// design fail on the host and nowhere else.
	if mode := fake.FakeFiles().Files[dump].Mode; mode != 0o640 {
		t.Errorf("the dump is mode %04o, want 0640 so the other account's group can read it", mode)
	}

	calls := strings.Join(fake.CallKeys(), "\n")
	cp := strings.Index(calls, "job.checkpoint(pgdump:")
	pgDump := strings.Index(calls, "postgres.Dump(")
	if cp < 0 || pgDump < 0 || cp > pgDump {
		t.Errorf("the dump's path was not checkpointed before pg_dump ran:\n%s", calls)
	}
	// The bytes, then the NAME. A dump in the page cache with an unsynced
	// directory entry is a handover whose only copy of the database does not
	// survive the machine.
	if !strings.Contains(calls, "files.Sync("+dump+")") || !strings.Contains(calls, "files.Sync("+pp.dir+")") {
		t.Errorf("the dump and its directory were not both fsynced:\n%s", calls)
	}

	pd := oc.Fleet.Tenants["dev"].Handover.PostgresData
	if pd == nil {
		t.Fatal("the handover block records no postgres migration")
	}
	if pd.Dump != dump || pd.DumpSHA256 == "" || pd.DumpedAt == "" {
		t.Errorf("handover.postgres_data = %+v", pd)
	}
	if len(pd.Tables) != 2 || pd.Tables[0].Name != "public.chunks" || pd.Tables[0].Rows != 88_000 {
		t.Errorf("the recorded row counts are %+v, want the census sorted by table name", pd.Tables)
	}
}

// A rolled-back release leaves no archive lying in the tenant's tree, and no
// row pointing at one.
func TestTheDumpsRollbackRemovesItAndTheRowEntry(t *testing.T) {
	oc, fake, _ := pgFixture(t, prepared, nil)
	p := planAs(t, oc, "wilke", "handover", map[string]any{"phase": "release"})
	r := newRunner(oc, fake)
	runUpTo(t, r, p, "dump this tenant's postgres")
	step, n := stepNamed(t, p, "dump this tenant's postgres")
	if _, err := r.run(step); err != nil {
		t.Fatal(err)
	}
	dump := idValue(t, r.externalIDs(n), "pgdump:")

	if _, err := r.rollback(step); err != nil {
		t.Fatalf("the rollback failed: %v", err)
	}
	if fake.FakeFiles().Content(dump) != nil {
		t.Errorf("%s is still there after the rollback", dump)
	}
	if pd := oc.Fleet.Tenants["dev"].Handover.PostgresData; pd != nil {
		t.Errorf("the row still records a dump that is gone: %+v", pd)
	}
}

// The release is the last cheap moment to discover that the handover cannot
// finish, and the only moment at which the tenant is still up.
func TestTheReleaseRefusesWhenThereIsNoRoomToDumpAndReCreate(t *testing.T) {
	oc, fake, _ := pgFixture(t, prepared, nil)
	fake.FakeFiles().Free = 1 << 20 // one megabyte
	p := planAs(t, oc, "wilke", "handover", map[string]any{"phase": "release"})
	step, _ := stepNamed(t, p, "check that there is room to dump")

	_, err := newRunner(oc, fake).run(step)
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("a release with no room = %v, want a refusal", err)
	}
	// The numbers say WHICH size they are: a figure in a refusal has to be
	// checkable against the command that would produce it.
	for _, want := range []string{"48 MB", "pg_database_size", "64 MB", "statfs", "Free space before releasing"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("the refusal does not say %q: %v", want, err)
		}
	}
}

// The socket and its lock file are removed by the RELEASING account, because
// the directory is sticky and nobody else may unlink them.
func TestTheReleaseRemovesTheStaleSocketTheTakeCouldNotHave(t *testing.T) {
	oc, fake, pp := pgFixture(t, prepared, nil)
	sock := filepath.Join(pp.run, ".s.PGSQL.24045")
	files := fake.FakeFiles()
	files.Put(sock, nil, 0o777)
	files.Put(sock+".lock", []byte("73\n"), 0o600)

	p := planAs(t, oc, "wilke", "handover", map[string]any{"phase": "release"})
	step, _ := stepNamed(t, p, "remove the stale postgres socket")
	log, err := newRunner(oc, fake).run(step)
	if err != nil {
		t.Fatalf("the socket cleanup failed: %v", err)
	}
	if !strings.Contains(log, ".s.PGSQL.24045.lock") {
		t.Errorf("the cleanup reported %q", log)
	}
	for _, path := range []string{sock, sock + ".lock"} {
		if files.Content(path) != nil {
			t.Errorf("%s is still there; a take would meet a postgres refusing to start over its own port", path)
		}
	}
	// Re-running a release must not fail because there is nothing left to do.
	if _, err := newRunner(oc, fake).run(step); err != nil {
		t.Errorf("the cleanup is not re-runnable: %v", err)
	}
}

// ---------------------------------------------------------------- the take

// WHERE the swap sits is the fix: after the port proofs, before any store
// starts, and before anything is written to the row.
func TestTheTakeSwapsTheClusterBetweenThePortProofsAndTheStores(t *testing.T) {
	oc, fake, _ := pgFixture(t, released, nil)
	seedReleasedDump(t, oc, fake)
	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	got := titles(p)

	swap := indexOfStep(got, "put an empty cluster directory in place")
	lastPort := -1
	for i, s := range got {
		if strings.Contains(s, "verify nothing listens") {
			lastPort = i
		}
	}
	firstStore := indexOfStep(got, "start the instance")
	sup := indexOfStep(got, "record supervisor: instance")
	restore := indexOfStep(got, "restore the release's dump")
	api := indexOfStep(got, "start the API detached")
	switch {
	case swap < 0 || lastPort < 0 || firstStore < 0 || sup < 0 || restore < 0 || api < 0:
		t.Fatalf("steps =\n  %s", strings.Join(got, "\n  "))
	case swap < lastPort:
		t.Errorf("the swap runs before the ports are proved free (%d < %d)", swap, lastPort)
	case swap > firstStore || swap > sup:
		t.Errorf("the swap runs after a store starts or after the row is written (swap %d, store %d, row %d)",
			swap, firstStore, sup)
	case !(firstStore < restore && restore < api):
		t.Errorf("the restore is not between the stores and the API (store %d, restore %d, api %d):\n  %s",
			firstStore, restore, api, strings.Join(got, "\n  "))
	}
}

// The swap: an EMPTY directory into place, the original aside, both names
// checkpointed first — and the dump verified before anything moves.
func TestTheSwapVerifiesTheDumpThenRenamesTwice(t *testing.T) {
	oc, fake, pp := pgFixture(t, released, nil)
	dump := seedReleasedDump(t, oc, fake)
	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	step, n := stepNamed(t, p, "put an empty cluster directory in place")
	r := newRunner(oc, fake)

	if _, err := r.run(step); err != nil {
		t.Fatalf("the swap failed: %v", err)
	}
	ids := r.externalIDs(n)
	fresh, aside := idValue(t, ids, "pgfresh:"), idValue(t, ids, "pgaside:")
	if !strings.HasPrefix(filepath.Base(fresh), "data.svcbvbrc-") {
		t.Errorf("the new directory is named %q, want data.<account>-<ts>", fresh)
	}
	if !strings.HasPrefix(filepath.Base(aside), "data.pre-handover-") {
		t.Errorf("the original is named %q, want data.pre-handover-<ts>", aside)
	}

	calls := strings.Join(fake.CallKeys(), "\n")
	sum := strings.Index(calls, "files.Sha256("+dump+")")
	cp := strings.Index(calls, "job.checkpoint(pgfresh:")
	r1 := strings.Index(calls, "files.Rename("+pp.data+","+aside+")")
	r2 := strings.Index(calls, "files.Rename("+fresh+","+pp.data+")")
	switch {
	case sum < 0 || cp < 0 || r1 < 0 || r2 < 0:
		t.Fatalf("the swap's calls are not what it claims:\n%s", calls)
	case sum > cp:
		t.Error("the dump was checksummed only after the names were committed to")
	case !(cp < r1 && r1 < r2):
		t.Errorf("checkpoint/rename order wrong (checkpoint %d, aside %d, into place %d)", cp, r1, r2)
	}

	files := fake.FakeFiles()
	// `data` is now THIS account's, and it is EMPTY: that is what makes the
	// instance initialise a cluster in it.
	st, err := files.Stat(context.Background(), pp.data)
	if err != nil || st.UID != 0 {
		t.Errorf("%s = %+v (err %v), want this account's", pp.data, st, err)
	}
	for _, f := range files.Paths() {
		if strings.HasPrefix(f, pp.data+"/") {
			t.Errorf("%s is not empty: it holds %s", pp.data, f)
		}
	}
	// …and the original is untouched, still the other account's.
	if o, err := files.Stat(context.Background(), aside); err != nil || o.UID != otherAccountUID {
		t.Errorf("the original at %s = %+v (err %v), want the other account's", aside, o, err)
	}
	// Nothing ever READ the original cluster: on the live host it could not.
	for _, c := range fake.CallKeys() {
		if strings.HasPrefix(c, "files.ReadDir("+pp.data) || strings.HasPrefix(c, "files.ReadFile("+pp.data) {
			t.Errorf("the take read the original cluster (%s); on the host that is a permission error", c)
		}
	}

	pd := oc.Fleet.Tenants["dev"].Handover.PostgresData
	if pd.PreHandover != aside || pd.Copy != fresh || pd.MigratedAt == "" {
		t.Errorf("handover.postgres_data = %+v", pd)
	}
	if pd.Dump != dump {
		t.Errorf("the swap lost the release's dump from the row: %+v", pd)
	}
}

// A dump that is not the one the release recorded is a refusal BEFORE anything
// moves: the archive crosses a job boundary, an account boundary and a night.
func TestTheSwapRefusesADumpThatDoesNotMatchItsChecksum(t *testing.T) {
	oc, fake, pp := pgFixture(t, released, nil)
	dump := seedReleasedDump(t, oc, fake)
	fake.FakeFiles().Put(dump, []byte("something else entirely"), 0o640)

	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	step, _ := stepNamed(t, p, "put an empty cluster directory in place")
	_, err := newRunner(oc, fake).run(step)
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "not the archive the release recorded") {
		t.Fatalf("a take over a rewritten dump = %v, want a refusal", err)
	}
	if calls := strings.Join(fake.CallKeys(), "\n"); strings.Contains(calls, "files.Rename(") {
		t.Errorf("a refused take renamed something:\n%s", calls)
	}
	if st, err := fake.FakeFiles().Stat(context.Background(), pp.data); err != nil || st.UID != otherAccountUID {
		t.Errorf("the original cluster was disturbed: %+v (err %v)", st, err)
	}
}

// An absent dump is the same class of refusal, and it names the way out.
func TestTheSwapRefusesWhenTheReleasesDumpIsGone(t *testing.T) {
	oc, fake, _ := pgFixture(t, released, nil)
	dump := seedReleasedDump(t, oc, fake)
	delete(fake.FakeFiles().Files, dump)

	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	step, _ := stepNamed(t, p, "put an empty cluster directory in place")
	_, err := newRunner(oc, fake).run(step)
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "re-run the release") {
		t.Fatalf("a take with no dump = %v, want a refusal naming the way out", err)
	}
}

// A cluster directory this account ALREADY owns is left alone: a re-take of a
// tenant the control plane already runs must not put an empty directory where
// a live cluster is.
func TestTheSwapDoesNothingWhenTheAccountAlreadyOwnsTheCluster(t *testing.T) {
	oc, fake, _ := pgFixture(t, released, map[string]int{})
	seedReleasedDump(t, oc, fake)
	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	step, _ := stepNamed(t, p, "put an empty cluster directory in place")

	log, err := newRunner(oc, fake).run(step)
	if err != nil {
		t.Fatalf("the swap failed over a directory this account owns: %v", err)
	}
	if !strings.Contains(log, "no swap needed") {
		t.Errorf("the step reported %q", log)
	}
	if calls := strings.Join(fake.CallKeys(), "\n"); strings.Contains(calls, "files.Rename(") {
		t.Errorf("a cluster this account owns was moved anyway:\n%s", calls)
	}
	if pd := oc.Fleet.Tenants["dev"].Handover.PostgresData; pd.PreHandover != "" {
		t.Errorf("the row records a swap that did not happen: %+v", pd)
	}
}

// The crash between the two renames: the one state that is neither before nor
// after, and the whole reason both names are checkpointed first.
func TestTheSwapFinishesAfterACrashBetweenTheTwoRenames(t *testing.T) {
	oc, fake, pp := pgFixture(t, released, nil)
	seedReleasedDump(t, oc, fake)
	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	step, n := stepNamed(t, p, "put an empty cluster directory in place")
	r := newRunner(oc, fake)
	if _, err := r.run(step); err != nil {
		t.Fatal(err)
	}
	fresh, aside := idValue(t, r.externalIDs(n), "pgfresh:"), idValue(t, r.externalIDs(n), "pgaside:")

	// Put the host back into the mid-rename state.
	files := fake.FakeFiles()
	if err := files.Rename(context.Background(), pp.data, fresh); err != nil {
		t.Fatal(err)
	}
	got, err := step.Reconcile(context.Background(), r.ctx(step))
	if err != nil || got != jobs.ReconcileRedo {
		t.Fatalf("reconcile over the mid-rename state = %v, %v; want redo", got, err)
	}
	if _, err := r.run(step); err != nil {
		t.Fatalf("the re-run did not finish the swap: %v", err)
	}
	if st, err := files.Stat(context.Background(), pp.data); err != nil || st.UID != 0 {
		t.Errorf("%s = %+v (err %v), want this account's directory in place", pp.data, st, err)
	}
	if _, err := files.Stat(context.Background(), aside); err != nil {
		t.Errorf("the original is no longer at %s: %v", aside, err)
	}
}

// A state the step cannot name is STUCK with all three paths in the message,
// never a guess. The directories in question are a tenant's database.
func TestTheSwapIsStuckRatherThanGuessing(t *testing.T) {
	oc, fake, pp := pgFixture(t, released, nil)
	seedReleasedDump(t, oc, fake)
	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	step, n := stepNamed(t, p, "put an empty cluster directory in place")
	r := newRunner(oc, fake)
	if _, err := r.run(step); err != nil {
		t.Fatal(err)
	}
	aside := idValue(t, r.externalIDs(n), "pgaside:")

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

// The restore, and the proof. The cluster the instance initialised is EMPTY;
// after the restore every table has to be back with exactly the rows the
// release recorded.
func TestTheRestoreLoadsTheDumpAndProvesEveryTablesRowCount(t *testing.T) {
	oc, fake, pp := pgFixture(t, released, nil)
	dump := seedReleasedDump(t, oc, fake)
	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	r := newRunner(oc, fake)
	runUpTo(t, r, p, "restore the release's dump")

	// The cluster the take started is empty — initdb's work, not the
	// original's.
	before, err := fake.Postgres().Census(context.Background(), pgSpec(pp))
	if err != nil {
		t.Fatal(err)
	}
	if len(before.Tables) != 0 {
		t.Fatalf("the new cluster is not empty before the restore: %+v", before.Tables)
	}

	step, _ := stepNamed(t, p, "restore the release's dump")
	log, err := r.run(step)
	if err != nil {
		t.Fatalf("the restore failed: %v", err)
	}
	if !strings.Contains(log, "2 table(s)") {
		t.Errorf("the restore reported %q", log)
	}
	if got := fake.FakePostgres().Restores; len(got) != 1 || !strings.Contains(got[0], dump) {
		t.Errorf("restores = %v, want exactly the release's dump", got)
	}
}

// A restore that succeeded and moved LESS than everything is the failure the
// row counts exist to catch. It must fail the take rather than surface later as
// a tenant that is quietly short.
func TestTheRestoreRefusesWhenATableComesBackShort(t *testing.T) {
	oc, fake, _ := pgFixture(t, released, nil)
	seedReleasedDump(t, oc, fake)
	fake.FakePostgres().RestoreDrops = map[string]int64{"public.chunks": 5}

	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	r := newRunner(oc, fake)
	runUpTo(t, r, p, "restore the release's dump")
	step, _ := stepNamed(t, p, "restore the release's dump")

	_, err := r.run(step)
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("a restore that lost rows = %v, want a refusal", err)
	}
	for _, want := range []string{"public.chunks 88000 → 87995", "FEWER rows", "abandon the handover"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("the refusal does not say %q: %v", want, err)
		}
	}
}

// ---------------------------------------------------------------- end to end

// release → take → commit, every step, against the fake host: the shape an
// operator actually performs, and the one thing no per-step test can show —
// that the tenant ends up owning its postgres AND holding its rows.
func TestReleaseTakeCommitMovesThePostgresEndToEnd(t *testing.T) {
	oc, fake, pp := pgFixture(t, prepared, nil)

	// ---- release, as the owner.
	rel := planAs(t, oc, "wilke", "handover", map[string]any{"phase": "release"})
	newRunner(oc, fake).runAll(t, rel)
	pd := oc.Fleet.Tenants["dev"].Handover.PostgresData
	if pd == nil || pd.Dump == "" || len(pd.Tables) != 2 {
		t.Fatalf("the release recorded no usable dump: %+v", pd)
	}

	// ---- take, as the service account, quoting the token THIS release
	// minted (a nonce, freshly generated: the take is gated on it).
	takeFixture(t, oc, fake)
	take := planAs(t, oc, "svcbvbrc", "handover",
		map[string]any{"phase": "take", "token": oc.Fleet.Tenants["dev"].Handover.Token})
	newRunner(oc, fake).runAll(t, take)

	tn := oc.Fleet.Tenants["dev"]
	if tn.Owner != "svcbvbrc" || tn.Supervisor != supervisorInstance {
		t.Errorf("after the take the row says owner %s / supervisor %s", tn.Owner, tn.Supervisor)
	}
	// The cluster is this account's, and it holds the rows the release counted.
	if st, err := fake.FakeFiles().Stat(context.Background(), pp.data); err != nil || st.UID != 0 {
		t.Errorf("%s = %+v (err %v), want the taking account's", pp.data, st, err)
	}
	after, err := fake.Postgres().Census(context.Background(), pgSpec(pp))
	if err != nil {
		t.Fatal(err)
	}
	if after.Tables["public.chunks"] != 88_000 || after.Tables["public.jobs"] != 12 {
		t.Errorf("the migrated database holds %+v, want what the release counted", after.Tables)
	}
	// The original is still there, untouched, in the other account's name.
	aside := tn.Handover.PostgresData.PreHandover
	if o, err := fake.FakeFiles().Stat(context.Background(), aside); err != nil || o.UID != otherAccountUID {
		t.Errorf("the original cluster at %s = %+v (err %v)", aside, o, err)
	}
	// The take's cluster carries no postmaster.pid from the original — under a
	// dump-and-restore it never could, and this is the assertion that keeps it
	// so if anyone ever reaches for a file copy again.
	if fake.FakeFiles().Content(filepath.Join(pp.data, "pgdata", "postmaster.pid")) != nil {
		t.Error("the new cluster carries a postmaster.pid from the original")
	}

	// ---- commit. The API the take spawned has to be ATTRIBUTABLE to this
	// account for the commit's own proof; the fake's spawn binds the port but
	// models no /proc owner for it.
	fake.FakeProc().MarkAlive(30001, oc.Tenant.Ports.API)
	commit := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "commit"})
	newRunner(oc, fake).runAll(t, commit)
	tn = oc.Fleet.Tenants["dev"]
	if tn.Handover != nil || tn.DesiredBoot != "enabled" {
		t.Errorf("after the commit: handover %+v, desired_boot %q", tn.Handover, tn.DesiredBoot)
	}
	// Both artefacts survive the commit, and the plan says where they are.
	if !warnsAbout(commit, aside) || !warnsAbout(commit, pd.Dump) {
		t.Errorf("the commit does not name what it is leaving behind: %v", commit.Plan.Warnings)
	}
	if fake.FakeFiles().Content(pd.Dump) == nil {
		t.Error("the commit deleted the release's dump")
	}
}

// release → take → abandon: the way back, and the property that makes it safe —
// the original cluster is never opened, so putting it back is exact.
func TestReleaseTakeAbandonPutsTheOriginalClusterBack(t *testing.T) {
	oc, fake, pp := pgFixture(t, prepared, nil)

	rel := planAs(t, oc, "wilke", "handover", map[string]any{"phase": "release"})
	newRunner(oc, fake).runAll(t, rel)
	takeFixture(t, oc, fake)
	take := planAs(t, oc, "svcbvbrc", "handover",
		map[string]any{"phase": "take", "token": oc.Fleet.Tenants["dev"].Handover.Token})
	newRunner(oc, fake).runAll(t, take)

	pd := oc.Fleet.Tenants["dev"].Handover.PostgresData
	aside, fresh := pd.PreHandover, pd.Copy

	// The operator stops the tenant, then abandons as the account that
	// released it.
	fake.FakeInstances().StopAll()
	fake.FakeProc().FreePort(oc.Tenant.Ports.API)
	fake.FakeProc().FreePort(oc.Tenant.Ports.PG)
	ab := planAs(t, oc, "wilke", "handover", map[string]any{"phase": "abandon"})
	// The cluster goes back BEFORE the row does: a row saying `manual` over a
	// directory the releasing account cannot start on is a tenant handed back
	// broken.
	got := titles(ab)
	if i, j := indexOfStep(got, "put the original postgres cluster back"), indexOfStep(got, "put the row back"); i < 0 || j < 0 || i > j {
		t.Fatalf("the cluster is not put back before the row (%d, %d):\n  %s", i, j, strings.Join(got, "\n  "))
	}
	newRunner(oc, fake).runAll(t, ab)

	files := fake.FakeFiles()
	if st, err := files.Stat(context.Background(), pp.data); err != nil || st.UID != otherAccountUID {
		t.Errorf("%s = %+v (err %v), want the releasing account's original back", pp.data, st, err)
	}
	if _, err := files.Stat(context.Background(), aside); err == nil {
		t.Errorf("%s is still there: the original was not moved back", aside)
	}
	// The soak's cluster is KEPT under its own name: it has diverged, and
	// deleting it is nobody's call but the operator's.
	if _, err := files.Stat(context.Background(), fresh); err != nil {
		t.Errorf("the take's cluster was deleted rather than kept at %s: %v", fresh, err)
	}
	tn := oc.Fleet.Tenants["dev"]
	if tn.Handover != nil || tn.Owner != "wilke" || tn.Supervisor != supervisorManual {
		t.Errorf("after the abandon: handover %+v, owner %s, supervisor %s", tn.Handover, tn.Owner, tn.Supervisor)
	}
}

// ---------------------------------------------------------------- the readiness fast-fail

// A store that started and DIED is not going to answer, and waiting out the
// bound for it is what turned a five-second failure into a three-minute one —
// and then reported `pg_isready … no response`, which says nothing about why.
//
// This one also pins the other half: only THIS attempt's output is quoted.
func TestTheReadinessWaitStopsWhenTheInstanceIsGoneAndIgnoresAnOlderLog(t *testing.T) {
	oc, fake, pp := pgFixture(t, released, nil)
	seedReleasedDump(t, oc, fake)
	logPath := "/rag/data/ctl/apptainer/config/instances/logs/coconut/svcbvbrc/postgres-dev.err"
	// What a PREVIOUS run of this instance left in the log — apptainer appends
	// for the life of the host. It must not appear in this attempt's failure,
	// however apt its wording.
	fake.FakeFiles().Put(logPath, []byte(
		"2026-09-17 08:31:02 UTC [1] FATAL:  data directory \"/var/lib/postgresql/data/pgdata\" has wrong ownership\n"), 0o644)
	fake.FakeInstances().ExitOnRun = map[string]string{"postgres-dev": ""}
	fake.FakePostgres().Readiness = map[string]bool{pp.run: false}

	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	r := newRunner(oc, fake)
	runUpTo(t, r, p, "restore the release's dump")
	step, _ := stepNamed(t, p, "restore the release's dump")

	_, err := r.run(step)
	if err == nil {
		t.Fatal("the restore succeeded over a postgres that never started")
	}
	if !strings.Contains(err.Error(), "postgres-dev") || !strings.Contains(err.Error(), "is not coming up") {
		t.Errorf("the failure does not say the instance is gone: %v", err)
	}
	if strings.Contains(err.Error(), "wrong ownership") {
		t.Errorf("the failure quotes a line from an earlier run of this instance: %v", err)
	}
	if !strings.Contains(err.Error(), "without writing anything") {
		t.Errorf("the failure does not say this attempt wrote nothing: %v", err)
	}
}

// …and when this attempt DOES write, its own words are what comes back.
func TestTheReadinessWaitQuotesWhatThisAttemptWrote(t *testing.T) {
	oc, fake, pp := pgFixture(t, released, nil)
	seedReleasedDump(t, oc, fake)
	fake.FakeInstances().ExitOnRun = map[string]string{"postgres-dev": strings.Join([]string{
		"2026-09-17 09:02:11 UTC [1] FATAL:  could not create lock file \"postmaster.pid\": Permission denied",
		"child process exited with exit code 1",
	}, "\n")}
	fake.FakePostgres().Readiness = map[string]bool{pp.run: false}

	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	r := newRunner(oc, fake)
	runUpTo(t, r, p, "restore the release's dump")
	step, _ := stepNamed(t, p, "restore the release's dump")

	_, err := r.run(step)
	if err == nil || !strings.Contains(err.Error(), "could not create lock file") {
		t.Fatalf("the failure does not quote this attempt's log: %v", err)
	}
}

// ---------------------------------------------------------------- helpers

// seedReleasedDump puts a release's artefact on the fake host and in the row:
// the archive the take verifies, its checksum, and the row counts the take will
// prove the restore against.
//
// It runs the RELEASE's own dump step rather than writing a file by hand, so
// that no take test can pass against an archive no release would have produced.
func seedReleasedDump(t *testing.T, oc jobs.Context, fake *drivers.Fake) string {
	t.Helper()
	rel := planAs(t, oc, "wilke", "handover", map[string]any{"phase": "release"})
	step, n := stepNamed(t, rel, "dump this tenant's postgres")
	r := newRunner(oc, fake)
	if _, err := r.run(step); err != nil {
		t.Fatalf("seeding the release's dump: %v", err)
	}
	return idValue(t, r.externalIDs(n), "pgdump:")
}

// runUpTo runs a plan's steps until (not including) the one named.
func runUpTo(t *testing.T, r *runner, p *jobs.Planned, substr string) {
	t.Helper()
	for _, s := range p.Steps {
		if strings.Contains(s.Plan.Title, substr) {
			return
		}
		if _, err := r.run(s); err != nil {
			t.Fatalf("step %d (%s): %v", s.Plan.N, s.Plan.Title, err)
		}
	}
	t.Fatalf("no step named %q in:\n  %s", substr, strings.Join(titles(p), "\n  "))
}

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

// An image that cannot initdb is the one failure a handover could not discover
// any later than its release: the ctl never runs initdb — the image's
// entrypoint does — so nothing would notice until a take had already renamed
// this tenant's cluster aside.
func TestTheReleaseRefusesAnImageThatCannotInitdb(t *testing.T) {
	oc, fake, _ := pgFixture(t, prepared, nil)
	fake.FakePostgres().MissingTools = map[string]bool{"initdb": true}
	p := planAs(t, oc, "wilke", "handover", map[string]any{"phase": "release"})
	step, _ := stepNamed(t, p, "check that there is room to dump")

	_, err := newRunner(oc, fake).run(step)
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "initdb") {
		t.Fatalf("a release against an image with no initdb = %v, want a refusal naming it", err)
	}
}

// A re-take of a tenant this control plane ALREADY runs creates no cluster —
// and must therefore restore nothing. The postgres answering by then is the
// tenant's own, live and populated; pouring the release's dump into it would
// fail on the first relation that already exists, and any restore that did not
// fail would be worse.
func TestTheRestoreDoesNothingWhenTheTakeCreatedNoCluster(t *testing.T) {
	oc, fake, pp := pgFixture(t, released, map[string]int{}) // the directory is already this account's
	// …and it holds a LIVE cluster. The fixture leaves `data` empty for every
	// other test, because the taking account cannot read another account's
	// cluster and nothing may depend on doing so — but this is the one case
	// where the directory IS this account's, so it can have contents, and a
	// postgres started on it finds a cluster rather than initialising one.
	fake.FakeFiles().Put(filepath.Join(pp.data, "pgdata", "PG_VERSION"), []byte("16\n"), 0o600)
	seedReleasedDump(t, oc, fake)
	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	r := newRunner(oc, fake)
	runUpTo(t, r, p, "restore the release's dump")

	// The tenant's own rows are there, because no cluster was replaced.
	before, err := fake.Postgres().Census(context.Background(), pgSpec(pp))
	if err != nil {
		t.Fatal(err)
	}
	if before.Tables["public.chunks"] != 88_000 {
		t.Fatalf("the fixture's own cluster was replaced: %+v", before.Tables)
	}

	step, _ := stepNamed(t, p, "restore the release's dump")
	log, err := r.run(step)
	if err != nil {
		t.Fatalf("the restore failed: %v", err)
	}
	if !strings.Contains(log, "skipped") {
		t.Errorf("the restore reported %q, want it to skip", log)
	}
	if got := fake.FakePostgres().Restores; len(got) != 0 {
		t.Errorf("a live cluster was restored into: %v", got)
	}
}

// ---------------------------------------------------------------- encoding

// The take's cluster has to be encoded like the one it replaces, and the row
// counts cannot see whether it is: a `pg_restore` into an SQL_ASCII/C cluster
// exits 0 and puts every row back. What differs is `length()`, `upper()`,
// `LIKE` and every index's sort order — silently, and for good.
func TestTheTakeBuildsTheClusterWithTheSourcesEncodingAndLocales(t *testing.T) {
	oc, fake, pp := pgFixture(t, released, nil)
	seedReleasedDump(t, oc, fake)
	// The release recorded them…
	pd := oc.Fleet.Tenants["dev"].Handover.PostgresData
	if pd.Encoding != "UTF8" || pd.Collate != "en_US.utf8" || pd.Ctype != "en_US.utf8" {
		t.Fatalf("the release recorded %q/%q/%q, want the source cluster's", pd.Encoding, pd.Collate, pd.Ctype)
	}

	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	r := newRunner(oc, fake)
	runUpTo(t, r, p, "restore the release's dump")

	// …and the cluster the instance initialised has them, rather than the
	// SQL_ASCII/C an unpinned initdb produces under an `LC_ALL=C`.
	fresh, err := fake.Postgres().Census(context.Background(), pgSpec(pp))
	if err != nil {
		t.Fatal(err)
	}
	if fresh.Encoding != "UTF8" || fresh.Collate != "en_US.utf8" || fresh.Ctype != "en_US.utf8" {
		t.Errorf("the new cluster is %q/%q/%q: the take did not pin what the release recorded",
			fresh.Encoding, fresh.Collate, fresh.Ctype)
	}
	if _, err := r.run(mustStep(t, p, "restore the release's dump")); err != nil {
		t.Fatalf("the restore failed: %v", err)
	}
}

// …and when the pinning does not take — an image, an entrypoint or an
// environment that ignores POSTGRES_INITDB_ARGS — the take REFUSES, before it
// restores anything. This is the 2026-09-17-shaped failure of the redesign, and
// the only thing standing between it and a silently re-encoded tenant.
func TestTheTakeRefusesAClusterThatCameOutWithTheWrongEncoding(t *testing.T) {
	oc, fake, pp := pgFixture(t, released, nil)
	seedReleasedDump(t, oc, fake)
	fake.FakeInstances().IgnoreInitdbArgs = true

	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	r := newRunner(oc, fake)
	runUpTo(t, r, p, "restore the release's dump")

	_, err := r.run(mustStep(t, p, "restore the release's dump"))
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("a take whose cluster came out SQL_ASCII = %v, want a refusal", err)
	}
	for _, want := range []string{"encoding UTF8 → SQL_ASCII", "lc_collate en_US.utf8 → C", "Nothing has been restored"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("the refusal does not say %q: %v", want, err)
		}
	}
	// …and it really did not restore: the proof is the driver's own record,
	// because a restore that happened would be a tenant's data in the wrong
	// cluster.
	if got := fake.FakePostgres().Restores; len(got) != 0 {
		t.Errorf("the dump was restored into a mis-encoded cluster anyway: %v", got)
	}
	if c, _ := fake.Postgres().Census(context.Background(), pgSpec(pp)); len(c.Tables) != 0 {
		t.Errorf("the mis-encoded cluster holds %d table(s)", len(c.Tables))
	}
}

// A source whose collate and ctype DIFFER cannot be reproduced with one
// `--locale`, so it is spelled out rather than silently flattened.
func TestTheInitdbArgsSpellOutASplitCollation(t *testing.T) {
	tn := &registry.Tenant{Handover: &registry.Handover{PostgresData: &registry.PostgresDataMigration{
		Encoding: "UTF8", Collate: "en_US.utf8", Ctype: "en_US.utf8",
	}}}
	if got := pgInitdbArgs(tn); got != "--encoding=UTF8 --locale=en_US.utf8" {
		t.Errorf("matching collate/ctype = %q", got)
	}
	tn.Handover.PostgresData.Ctype = "C"
	if got := pgInitdbArgs(tn); got != "--encoding=UTF8 --lc-collate=en_US.utf8 --lc-ctype=C" {
		t.Errorf("split collate/ctype = %q", got)
	}
	// A tenant with no handover, or a release that recorded nothing, pins
	// nothing — and must not pass a half-built flag.
	tn.Handover.PostgresData.Encoding = ""
	if got := pgInitdbArgs(tn); got != "" {
		t.Errorf("an incomplete record = %q, want no args at all", got)
	}
	if got := pgInitdbArgs(&registry.Tenant{}); got != "" {
		t.Errorf("a tenant with no handover = %q", got)
	}
}

// ---------------------------------------------------------------- the cluster's boundary

// `pg_dump -d <tenant>` moves ONE database and no roles. A cluster holding more
// than the tenant's own is a handover that would leave something behind in the
// directory the commit invites the operator to delete — so the release refuses
// rather than move a tenant silently short.
func TestTheReleaseRefusesAClusterHoldingMoreThanTheTenant(t *testing.T) {
	oc, fake, pp := pgFixture(t, prepared, nil)
	c := fake.FakePostgres().Contents[pp.run]
	c.Databases = []string{"dev", "postgres", "scratch"}
	c.Roles = []string{"dev", "analyst"}
	fake.FakePostgres().Contents[pp.run] = c

	p := planAs(t, oc, "wilke", "handover", map[string]any{"phase": "release"})
	_, err := newRunner(oc, fake).run(mustStep(t, p, "check that there is room to dump"))
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("a release over a shared cluster = %v, want a refusal", err)
	}
	for _, want := range []string{"database(s) scratch", "login role(s) analyst", "accept_extra_databases"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("the refusal does not say %q: %v", want, err)
		}
	}
	// `postgres` is initdb's own and is never "extra": the take's cluster will
	// have one too. The extras are named exactly, so the list is the assertion.
	if !strings.Contains(err.Error(), "database(s) scratch and login role(s) analyst") {
		t.Errorf("the refusal does not name exactly what is extra: %v", err)
	}
}

// …and the override says so in the ROW, not only in the flag: what was left
// behind has to be findable after the handover, because the pre-handover
// cluster is the only copy of it.
func TestAcceptingExtraDatabasesRecordsThemInTheRow(t *testing.T) {
	oc, fake, pp := pgFixture(t, prepared, nil)
	c := fake.FakePostgres().Contents[pp.run]
	c.Databases = []string{"dev", "scratch"}
	c.Roles = []string{"dev", "analyst"}
	fake.FakePostgres().Contents[pp.run] = c

	p := planAs(t, oc, "wilke", "handover",
		map[string]any{"phase": "release", "accept_extra_databases": true})
	r := newRunner(oc, fake)
	if _, err := r.run(mustStep(t, p, "check that there is room to dump")); err != nil {
		t.Fatalf("the release refused although the extras were accepted: %v", err)
	}
	runUpTo(t, r, p, "dump this tenant's postgres")
	if _, err := r.run(mustStep(t, p, "dump this tenant's postgres")); err != nil {
		t.Fatal(err)
	}
	pd := oc.Fleet.Tenants["dev"].Handover.PostgresData
	if len(pd.ExtraDatabases) != 1 || pd.ExtraDatabases[0] != "scratch" {
		t.Errorf("extra_databases = %v", pd.ExtraDatabases)
	}
	if len(pd.ExtraRoles) != 1 || pd.ExtraRoles[0] != "analyst" {
		t.Errorf("extra_roles = %v", pd.ExtraRoles)
	}
}

// The override belongs to the RELEASE: it is the phase that reads the cluster.
func TestAcceptExtraDatabasesIsAReleaseDecision(t *testing.T) {
	oc, _, _ := pgFixture(t, released, nil)
	err := planErrAs(t, oc, "svcbvbrc", "handover",
		map[string]any{"phase": "take", "token": testToken, "accept_extra_databases": true})
	if !errors.Is(err, jobs.ErrValidation) {
		t.Errorf("accept_extra_databases on a take = %v, want a validation error", err)
	}
}

// ---------------------------------------------------------------- refusals the plan makes

// A tenant with its own postgres whose row records NO dump is a release that
// did not finish. Going on would start this account's postgres on a cluster it
// does not own — the original failure — so the take refuses at PLAN time,
// before it has taken a single lock on the host.
func TestTheTakeRefusesAtPlanTimeWhenTheReleaseRecordedNoDump(t *testing.T) {
	oc, _, _ := pgFixture(t, released, nil) // `released` leaves postgres_data nil
	err := planErrAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("a take with no dump recorded = %v, want a refusal", err)
	}
	for _, want := range []string{"records no dump", "Re-run the release", "wilke"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("the refusal does not say %q: %v", want, err)
		}
	}
}

// The restore runs in ONE transaction, so an interrupted one leaves the
// database EMPTY. Its reconcile reads that: empty is redo, the release's own
// counts are done, anything else is a database somebody wrote to and is stuck.
func TestTheRestoresReconcileTellsEmptyFromDoneFromSomethingElse(t *testing.T) {
	oc, fake, pp := pgFixture(t, released, nil)
	seedReleasedDump(t, oc, fake)
	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	r := newRunner(oc, fake)
	runUpTo(t, r, p, "restore the release's dump")
	step := mustStep(t, p, "restore the release's dump")

	// Empty: the restore either has not run or rolled itself back.
	if got, err := step.Reconcile(context.Background(), r.ctx(step)); got != jobs.ReconcileRedo || err != nil {
		t.Errorf("reconcile over an empty database = %v, %v; want redo", got, err)
	}
	// Done: exactly what the release recorded.
	if _, err := r.run(step); err != nil {
		t.Fatal(err)
	}
	if got, err := step.Reconcile(context.Background(), r.ctx(step)); got != jobs.ReconcileDone || err != nil {
		t.Errorf("reconcile over a finished restore = %v, %v; want done", got, err)
	}
	// Something else: neither empty nor right.
	c := fake.FakePostgres().Contents[pp.run]
	c.Tables["public.chunks"] = 1
	fake.FakePostgres().Contents[pp.run] = c
	got, err := step.Reconcile(context.Background(), r.ctx(step))
	if got != jobs.ReconcileStuck || err == nil {
		t.Fatalf("reconcile over a database somebody wrote to = %v, %v; want stuck", got, err)
	}
	if !strings.Contains(err.Error(), "ONE transaction") {
		t.Errorf("the stuck reason does not say why a half-applied dump is not the explanation: %v", err)
	}
}

// mustStep is stepNamed without the number, for the assertions that do not
// need it.
func mustStep(t *testing.T, p *jobs.Planned, substr string) jobs.Step {
	t.Helper()
	s, _ := stepNamed(t, p, substr)
	return s
}

// One job can start the same instance more than once — a resumed step, a
// `fleet start --all` over a leg that was already up, a retry after a rollback
// — and each start appends to the same log. The offset a failure quotes from
// must be the LAST mark, or everything a later attempt wrote is reported as
// though this one had written it: the bug the offset exists to prevent,
// arriving by the other door.
func TestTheLogOffsetIsTheLastMarkThisJobRecorded(t *testing.T) {
	const path = "/rag/data/ctl/apptainer/config/instances/logs/coconut/svcbvbrc/postgres-dev.err"
	sc := &jobs.StepContext{Job: &model.Job{Steps: []model.Step{
		{N: 1, ExternalIDs: []string{"instance:postgres-dev", "errlog:" + path + "@0"}},
		{N: 2, ExternalIDs: []string{"pidfile:/somewhere"}},
		{N: 3, ExternalIDs: []string{"instance:postgres-dev", "errlog:" + path + "@4096"}},
	}}}
	got, ok := errLogOffset(sc, path)
	if !ok || got != 4096 {
		t.Errorf("errLogOffset = %d, %v; want the LAST mark (4096)", got, ok)
	}
	// A log no step in this job marked is quoted from nowhere: every byte in
	// it belongs to some earlier run.
	if _, ok := errLogOffset(sc, "/rag/data/ctl/…/qdrant-dev.err"); ok {
		t.Error("an unmarked log reported an offset")
	}
	if _, ok := errLogOffset(&jobs.StepContext{}, path); ok {
		t.Error("a context with no job reported an offset")
	}
}
