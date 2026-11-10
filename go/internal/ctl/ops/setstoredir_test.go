package ops

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// PR3 of the per-qdrant data dir: `tenant set-store-dir`.

const defaultQdir = "/rag/data/tenants/dev/qdrant"

// storeDirFixture is a ctl-managed dev row whose qdrant is exclusive and
// STOPPED (nothing listens on its ports, no instance runs), with a store both
// at the default location and at movedQdir, planned by a --direct engine.
func storeDirFixture(t *testing.T, tweak func(*drivers.FakeOptions)) (jobs.Context, *drivers.Fake) {
	t.Helper()
	oc, fake := fixtureOpts(t, "dev", managed, tweak)
	for _, d := range []string{
		defaultQdir, defaultQdir + "/storage", defaultQdir + "/snapshots",
		movedQdir, movedQdir + "/storage", movedQdir + "/snapshots",
	} {
		fake.FakeFiles().Dirs[d] = 0o2770
	}
	return direct(oc), fake
}

func setDirArgs(dir string) map[string]any { return map[string]any{"store": "qdrant", "dir": dir} }

var clearArgs = map[string]any{"store": "qdrant", "clear": true}

func planStoreDir(t *testing.T, oc jobs.Context, mountOf func(string) (string, string, error),
	args map[string]any) (*jobs.Planned, error) {
	t.Helper()
	return opWith(t, oc, []string{"/qdrant"}, mountOf, "set-store-dir").Plan(context.Background(), oc, args)
}

// runStoreDir plans and runs the verb, returning the plan or the first error
// (plan-time or run-time).
func runStoreDir(t *testing.T, oc jobs.Context, fake *drivers.Fake, mountOf func(string) (string, string, error),
	args map[string]any) (*jobs.Planned, error) {
	t.Helper()
	p, err := planStoreDir(t, oc, mountOf, args)
	if err != nil {
		return nil, err
	}
	r := newRunner(oc, fake)
	for _, s := range p.Steps {
		if _, err := r.run(s); err != nil {
			return p, err
		}
	}
	return p, nil
}

func rowJSON(t *testing.T, tn *registry.Tenant) string {
	t.Helper()
	b, err := json.Marshal(tn)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

// The refusal table: every state in which repointing the row would hand qdrant
// an empty, foreign, running or unmounted directory. Each case must leave the
// row untouched.
func TestSetStoreDirRefusals(t *testing.T) {
	for _, c := range []struct {
		name    string
		tweak   func(*drivers.FakeOptions)
		setup   func(*testing.T, jobs.Context, *drivers.Fake) jobs.Context
		mountOf func(string) (string, string, error)
		args    map[string]any
		want    string
	}{
		{name: "the daemon's engine (not --direct)", args: setDirArgs(movedQdir), want: "--direct",
			setup: func(_ *testing.T, oc jobs.Context, _ *drivers.Fake) jobs.Context { oc.Mode = ""; return oc }},
		{name: "instance running in the ctl's table", args: setDirArgs(movedQdir), want: "is running",
			tweak: func(o *drivers.FakeOptions) { o.RunningInstances = []string{"qdrant-dev"} }},
		{name: "instance running in the account's table", args: setDirArgs(movedQdir), want: "is running",
			setup: func(_ *testing.T, oc jobs.Context, f *drivers.Fake) jobs.Context {
				f.FakeInstances().StartInAccount("qdrant-" + oc.Tenant.ManifestName)
				return oc
			}},
		{name: "qdrant http port listening", args: setDirArgs(movedQdir), want: "listens on",
			setup: func(_ *testing.T, oc jobs.Context, f *drivers.Fake) jobs.Context {
				f.FakeProc().SetOwner(oc.Tenant.Ports.QdrantHTTP, 4321, 1000)
				return oc
			}},
		{name: "qdrant grpc port listening", args: setDirArgs(movedQdir), want: "listens on",
			setup: func(_ *testing.T, oc jobs.Context, f *drivers.Fake) jobs.Context {
				f.FakeProc().SetOwner(oc.Tenant.Ports.QdrantGRPC, 4321, 1000)
				return oc
			}},
		{name: "--clear with the qdrant running", args: clearArgs, want: "listens on",
			setup: func(_ *testing.T, oc jobs.Context, f *drivers.Fake) jobs.Context {
				oc.Tenant.Stores.Qdrant.DataDir = movedQdir
				f.FakeProc().SetOwner(oc.Tenant.Ports.QdrantHTTP, 4321, 1000)
				return oc
			}},
		{name: "store not exclusive", args: setDirArgs(movedQdir), want: "not exclusive",
			setup: func(_ *testing.T, oc jobs.Context, _ *drivers.Fake) jobs.Context {
				oc.Tenant.Stores.Qdrant.Ownership = registry.OwnershipShared
				return oc
			}},
		{name: "dir outside every root", args: setDirArgs("/srv/qdrant/dev"), want: "CTL_STORE_DATA_ROOTS"},
		{name: "dir is a store root itself", args: setDirArgs("/qdrant"), want: "CTL_STORE_DATA_ROOTS"},
		{name: "dir is the default spelling", args: setDirArgs(defaultQdir), want: "--clear"},
		{name: "dir not clean", args: setDirArgs("/qdrant/tenants/../tenants/dev"), want: "clean"},
		{name: "storage missing", args: setDirArgs(movedQdir), want: movedQdir + "/storage",
			setup: func(_ *testing.T, oc jobs.Context, f *drivers.Fake) jobs.Context {
				delete(f.FakeFiles().Dirs, movedQdir+"/storage")
				return oc
			}},
		{name: "storage is a symlink", args: setDirArgs(movedQdir), want: "symlink",
			setup: func(_ *testing.T, oc jobs.Context, f *drivers.Fake) jobs.Context {
				delete(f.FakeFiles().Dirs, movedQdir+"/storage")
				f.FakeFiles().PutSymlink(movedQdir + "/storage")
				return oc
			}},
		{name: "snapshots missing", args: setDirArgs(movedQdir), want: movedQdir + "/snapshots",
			setup: func(_ *testing.T, oc jobs.Context, f *drivers.Fake) jobs.Context {
				delete(f.FakeFiles().Dirs, movedQdir+"/snapshots")
				return oc
			}},
		{name: "an intermediate component is a symlink", args: setDirArgs(movedQdir), want: "/qdrant/tenants is a symlink",
			setup: func(_ *testing.T, oc jobs.Context, f *drivers.Fake) jobs.Context {
				// ln -s <elsewhere> /qdrant/tenants: dir, storage/ and
				// snapshots/ still answer as directories (PR2 review F1).
				f.FakeFiles().PutSymlink("/qdrant/tenants")
				return oc
			}},
		{name: "volume not mounted", args: setDirArgs(movedQdir), mountOf: mountedOn("/"), want: "root filesystem"},
		{name: "another row's qdrant dir", args: setDirArgs("/qdrant/tenants/other"), want: "share",
			setup: func(t *testing.T, oc jobs.Context, _ *drivers.Fake) jobs.Context {
				moveOther(t, oc, "/qdrant/tenants/other")
				return oc
			}},
		{name: "inside another row's qdrant dir", args: setDirArgs("/qdrant/tenants/other/dev"), want: "inside",
			setup: func(t *testing.T, oc jobs.Context, _ *drivers.Fake) jobs.Context {
				moveOther(t, oc, "/qdrant/tenants/other")
				return oc
			}},
		{name: "--clear without a default store", args: clearArgs, want: defaultQdir + "/storage does not exist",
			setup: func(_ *testing.T, oc jobs.Context, f *drivers.Fake) jobs.Context {
				oc.Tenant.Stores.Qdrant.DataDir = movedQdir
				delete(f.FakeFiles().Dirs, defaultQdir+"/storage")
				return oc
			}},
		{name: "--clear with a symlinked default store", args: clearArgs, want: "symlink",
			setup: func(_ *testing.T, oc jobs.Context, f *drivers.Fake) jobs.Context {
				oc.Tenant.Stores.Qdrant.DataDir = movedQdir
				delete(f.FakeFiles().Dirs, defaultQdir+"/storage")
				f.FakeFiles().PutSymlink(defaultQdir + "/storage")
				return oc
			}},
		{name: "handover in flight", args: setDirArgs(movedQdir), want: "handover",
			setup: func(_ *testing.T, oc jobs.Context, _ *drivers.Fake) jobs.Context {
				released(oc.Tenant)
				return oc
			}},
	} {
		t.Run(c.name, func(t *testing.T) {
			oc, fake := storeDirFixture(t, c.tweak)
			if c.setup != nil {
				oc = c.setup(t, oc, fake)
			}
			mountOf := c.mountOf
			if mountOf == nil {
				mountOf = mountedOn("/qdrant")
			}
			before := rowJSON(t, oc.Fleet.Tenants["dev"])
			_, err := runStoreDir(t, oc, fake, mountOf, c.args)
			if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), c.want) {
				t.Fatalf("set-store-dir %v = %v, want a refusal mentioning %q", c.args, err, c.want)
			}
			if after := rowJSON(t, oc.Fleet.Tenants["dev"]); after != before {
				t.Errorf("a refused set-store-dir changed the row:\nbefore %s\nafter  %s", before, after)
			}
		})
	}
}

// moveOther makes another fixture row own its qdrant and moves it to dir.
func moveOther(t *testing.T, oc jobs.Context, dir string) {
	t.Helper()
	other := oc.Fleet.Tenants["lucid-next"]
	if other == nil {
		t.Fatal("the fixture fleet has no lucid-next")
	}
	managed(other)
	other.Stores.Qdrant.DataDir = dir
	if err := oc.Fleet.Validate(); err != nil {
		t.Fatalf("test setup: %v", err)
	}
}

// The args are refused before anything else: one of dir/clear, not both, and
// only qdrant.
func TestSetStoreDirArgValidation(t *testing.T) {
	oc, _ := storeDirFixture(t, nil)
	for _, args := range []map[string]any{
		{"store": "qdrant"},
		{"store": "qdrant", "dir": movedQdir, "clear": true},
		{"store": "elasticsearch", "dir": movedQdir},
		{"dir": movedQdir},
		{"store": "qdrant", "dir": "relative/dir"},
	} {
		if _, err := planStoreDir(t, oc, mountedOn("/qdrant"), args); !errors.Is(err, jobs.ErrValidation) {
			t.Errorf("set-store-dir %v = %v, want a validation error", args, err)
		}
	}
}

// A binary that cannot serialise the field refuses rather than writing a row
// without it.
func TestSetStoreDirRefusesABinaryThatDoesNotKnowTheField(t *testing.T) {
	if !binaryKnowsQdrantDataDir() {
		t.Fatal("this build does not serialise stores.qdrant.data_dir")
	}
	was := binaryKnowsQdrantDataDir
	binaryKnowsQdrantDataDir = func() bool { return false }
	defer func() { binaryKnowsQdrantDataDir = was }()
	oc, _ := storeDirFixture(t, nil)
	if _, err := planStoreDir(t, oc, mountedOn("/qdrant"), setDirArgs(movedQdir)); !errors.Is(err, jobs.ErrRefused) ||
		!strings.Contains(err.Error(), "does not know") {
		t.Fatalf("set-store-dir with an old binary = %v, want a refusal", err)
	}
}

// Success writes exactly stores.qdrant.data_dir and restart_pending; every
// other byte of the row (last_ops included) is what it was, the plan carries
// the other-binaries warning, and nothing destructive is planned.
func TestSetStoreDirWritesTheFieldAndRestartPendingOnly(t *testing.T) {
	oc, fake := storeDirFixture(t, nil)
	row := oc.Fleet.Tenants["dev"]
	row.RestartPending = false
	// A deep copy: a shallow one shares last_ops' map with the row, and would
	// see a last_ops entry the op wrongly added as "expected".
	var want registry.Tenant
	if err := json.Unmarshal([]byte(rowJSON(t, row)), &want); err != nil {
		t.Fatal(err)
	}
	want.Stores.Qdrant.DataDir = movedQdir
	want.RestartPending = true
	gen := oc.Fleet.Generation

	p, err := runStoreDir(t, oc, fake, mountedOn("/qdrant"), setDirArgs(movedQdir))
	if err != nil {
		t.Fatalf("set-store-dir: %v", err)
	}
	if got, w := rowJSON(t, oc.Fleet.Tenants["dev"]), rowJSON(t, &want); got != w {
		t.Errorf("row after set-store-dir:\n got %s\nwant %s", got, w)
	}
	if oc.Fleet.Generation != gen+1 {
		t.Errorf("generation %d -> %d, want one save", gen, oc.Fleet.Generation)
	}
	for _, s := range p.Steps {
		if s.Plan.Destructive {
			t.Errorf("set-store-dir planned a destructive step: %s", s.Plan.Title)
		}
	}
	if !strings.Contains(strings.Join(p.Plan.Warnings, "\n"), "registry validate") {
		t.Errorf("the plan does not warn about older ctl binaries: %v", p.Plan.Warnings)
	}
	if p.Result()["changed"] != true || p.Result()["data_dir"] != movedQdir || p.Result()["previous_data_dir"] != "" {
		t.Errorf("result = %v", p.Result())
	}
	// The verb read the host it was asked about: the stopped probe and the
	// moved-dir check both ran.
	calls := strings.Join(fake.CallKeys(), "\n")
	for _, w := range []string{"proc.Listening(", "instances.List(", "files.Stat(" + movedQdir + "/storage)"} {
		if !strings.Contains(calls, w) {
			t.Errorf("no %s in the call trace:\n%s", w, calls)
		}
	}
}

// Re-running with the value already recorded is a no-op success: one step,
// no host probe, no save, restart_pending untouched.
func TestSetStoreDirIsIdempotent(t *testing.T) {
	oc, fake := storeDirFixture(t, nil)
	if _, err := runStoreDir(t, oc, fake, mountedOn("/qdrant"), setDirArgs(movedQdir)); err != nil {
		t.Fatalf("first run: %v", err)
	}
	oc.Fleet.Tenants["dev"].RestartPending = false
	gen := oc.Fleet.Generation
	before := rowJSON(t, oc.Fleet.Tenants["dev"])
	// Even with the qdrant running again: a no-op does not need it stopped.
	fake.FakeProc().SetOwner(oc.Tenant.Ports.QdrantHTTP, 4321, 1000)
	p, err := runStoreDir(t, oc, fake, mountedOn("/qdrant"), setDirArgs(movedQdir))
	if err != nil {
		t.Fatalf("second run: %v", err)
	}
	if len(p.Steps) != 1 || p.Result()["changed"] != false {
		t.Errorf("a no-op planned %v, result %v", titles(p), p.Result())
	}
	if oc.Fleet.Generation != gen || rowJSON(t, oc.Fleet.Tenants["dev"]) != before {
		t.Error("a no-op set-store-dir wrote the registry")
	}
	// --clear of a row that has no field is a no-op too.
	oc2, fake2 := storeDirFixture(t, nil)
	gen2 := oc2.Fleet.Generation
	if p, err := runStoreDir(t, oc2, fake2, mountedOn("/qdrant"), clearArgs); err != nil || len(p.Steps) != 1 {
		t.Fatalf("--clear of a default row = %v, %v", p, err)
	}
	if oc2.Fleet.Generation != gen2 {
		t.Error("a no-op --clear wrote the registry")
	}
}

// --clear round trip: set, clear, and the qdrant store's serialisation is
// byte-identical to the original (the field is absent, not empty).
func TestSetStoreDirClearRoundTrip(t *testing.T) {
	oc, fake := storeDirFixture(t, nil)
	orig, _ := json.Marshal(oc.Fleet.Tenants["dev"].Stores.Qdrant)
	lastOpsBefore := len(oc.Fleet.Tenants["dev"].LastOps)
	if _, err := runStoreDir(t, oc, fake, mountedOn("/qdrant"), setDirArgs(movedQdir)); err != nil {
		t.Fatalf("set: %v", err)
	}
	oc.Fleet.Tenants["dev"].RestartPending = false
	p, err := runStoreDir(t, oc, fake, mountedOn("/qdrant"), clearArgs)
	if err != nil {
		t.Fatalf("clear: %v", err)
	}
	row := oc.Fleet.Tenants["dev"]
	got, _ := json.Marshal(row.Stores.Qdrant)
	if !bytes.Equal(got, orig) {
		t.Errorf("qdrant store after set+clear:\n got %s\nwant %s", got, orig)
	}
	if bytes.Contains(got, []byte("data_dir")) {
		t.Errorf("the cleared field is still spelled: %s", got)
	}
	if !row.RestartPending {
		t.Error("--clear did not set restart_pending")
	}
	if p.Result()["previous_data_dir"] != movedQdir {
		t.Errorf("result = %v", p.Result())
	}
	if len(row.LastOps) != lastOpsBefore {
		t.Errorf("set-store-dir changed last_ops: %v", row.LastOps)
	}
}

// The registry step's rollback restores the field and restart_pending from
// what it checkpointed.
func TestSetStoreDirRollback(t *testing.T) {
	oc, fake := storeDirFixture(t, nil)
	oc.Fleet.Tenants["dev"].RestartPending = false
	before := rowJSON(t, oc.Fleet.Tenants["dev"])
	p, err := planStoreDir(t, oc, mountedOn("/qdrant"), setDirArgs(movedQdir))
	if err != nil {
		t.Fatal(err)
	}
	r := newRunner(oc, fake)
	r.runAll(t, p)
	last := p.Steps[len(p.Steps)-1]
	if _, err := r.rollback(last); err != nil {
		t.Fatalf("rollback: %v", err)
	}
	if after := rowJSON(t, oc.Fleet.Tenants["dev"]); after != before {
		t.Errorf("rollback left the row changed:\nbefore %s\nafter  %s", before, after)
	}
}

// After the verb the registry marshals, saves and loads back through the
// strict loader (what `registry validate` runs), carrying the field.
func TestSetStoreDirRegistryRoundTrip(t *testing.T) {
	oc, fake := storeDirFixture(t, nil)
	if _, err := runStoreDir(t, oc, fake, mountedOn("/qdrant"), setDirArgs(movedQdir)); err != nil {
		t.Fatalf("set-store-dir: %v", err)
	}
	f, err := cloneFleetJSON(oc.Fleet)
	if err != nil {
		t.Fatal(err)
	}
	f.Generation = 0 // a fresh file: Save's generation check expects none
	path := filepath.Join(t.TempDir(), "registry.json")
	if err := registry.Save(path, f, "set-store-dir test"); err != nil {
		t.Fatalf("Save: %v", err)
	}
	back, err := registry.LoadNoRepair(path)
	if err != nil {
		t.Fatalf("LoadNoRepair (registry validate): %v", err)
	}
	if got := back.Tenants["dev"].Stores.Qdrant.DataDir; got != movedQdir {
		t.Errorf("data_dir after the round trip = %q", got)
	}
	b, _ := os.ReadFile(path)
	if !bytes.Contains(b, []byte(`"data_dir": "`+movedQdir+`"`)) {
		t.Error("the saved registry does not spell the field")
	}
}

// Fake/real parity for the run-time checks the verb makes: the real files
// driver (lstat on a temp tree) and the fake give the same verdict for every
// shape the refusal table uses.
func TestSetStoreDirDirCheckFakeRealParity(t *testing.T) {
	type shape struct {
		name string
		real func(t *testing.T, dir string)
		fake func(f *drivers.FakeFiles, dir string)
		ok   bool
	}
	mk := func(t *testing.T, ps ...string) {
		for _, p := range ps {
			if err := os.MkdirAll(p, 0o750); err != nil {
				t.Fatal(err)
			}
		}
	}
	fdirs := func(f *drivers.FakeFiles, ps ...string) {
		for _, p := range ps {
			f.Dirs[p] = 0o2770
		}
	}
	shapes := []shape{
		{"healthy", func(t *testing.T, d string) { mk(t, d+"/storage", d+"/snapshots") },
			func(f *drivers.FakeFiles, d string) { fdirs(f, d, d+"/storage", d+"/snapshots") }, true},
		{"storage missing", func(t *testing.T, d string) { mk(t, d+"/snapshots") },
			func(f *drivers.FakeFiles, d string) { fdirs(f, d, d+"/snapshots") }, false},
		{"snapshots missing", func(t *testing.T, d string) { mk(t, d+"/storage") },
			func(f *drivers.FakeFiles, d string) { fdirs(f, d, d+"/storage") }, false},
		{"storage symlink", func(t *testing.T, d string) {
			mk(t, d+"/snapshots", d+"-elsewhere")
			if err := os.Symlink(d+"-elsewhere", d+"/storage"); err != nil {
				t.Fatal(err)
			}
		}, func(f *drivers.FakeFiles, d string) { fdirs(f, d, d+"/snapshots"); f.PutSymlink(d + "/storage") }, false},
		{"intermediate symlink", func(t *testing.T, d string) {
			parent := filepath.Dir(d) // <store>/tenants
			elsewhere := filepath.Dir(parent) + "-victim"
			mk(t, elsewhere+"/dev/storage", elsewhere+"/dev/snapshots")
			if err := os.Symlink(elsewhere, parent); err != nil {
				t.Fatal(err)
			}
		}, func(f *drivers.FakeFiles, d string) {
			fdirs(f, d, d+"/storage", d+"/snapshots")
			f.PutSymlink(filepath.Dir(d))
		}, false},
		{"storage a file", func(t *testing.T, d string) {
			mk(t, d+"/snapshots")
			if err := os.WriteFile(d+"/storage", []byte("x"), 0o640); err != nil {
				t.Fatal(err)
			}
		}, func(f *drivers.FakeFiles, d string) {
			fdirs(f, d, d+"/snapshots")
			f.Put(d+"/storage", []byte("x"), 0o640)
		}, false},
	}
	for _, s := range shapes {
		t.Run(s.name, func(t *testing.T) {
			// Real: a store root beside a temp rag root, as /qdrant is beside /rag.
			ragRoot, store := t.TempDir(), t.TempDir()
			dir := filepath.Join(store, "tenants", "dev")
			s.real(t, dir)
			real := drivers.NewReal(drivers.RealOptions{Roots: paths.NewRoots(ragRoot, paths.Overrides{}),
				StoreDataRoots: []string{store}})
			realErr := storeDirCheck(t, real, ragRoot, store, dir)

			_, fake := storeDirFixture(t, nil)
			s.fake(fake.FakeFiles(), movedQdir+"-parity")
			fakeErr := storeDirCheck(t, fake, "/rag", "/qdrant", movedQdir+"-parity")

			if (realErr == nil) != s.ok || (fakeErr == nil) != s.ok {
				t.Fatalf("%s: real = %v, fake = %v, want ok=%v", s.name, realErr, fakeErr, s.ok)
			}
			if !s.ok && (!errors.Is(realErr, jobs.ErrRefused) || !errors.Is(fakeErr, jobs.ErrRefused)) {
				t.Errorf("%s: real = %v, fake = %v, want refusals from both", s.name, realErr, fakeErr)
			}
		})
	}
}

// storeDirCheck runs the verb's moved-dir probe against one driver set.
func storeDirCheck(t *testing.T, d jobs.Drivers, ragRoot, storeRoot, dir string) error {
	t.Helper()
	roots := paths.NewRoots(ragRoot, paths.Overrides{})
	tp := paths.TenantPaths(roots, "dev", "dev").WithQdrantDataDir(dir)
	sc := &jobs.StepContext{Ops: jobs.Context{Roots: roots, Drivers: d}, Logf: func(string, ...any) {}}
	_, err := qdrantDataDirPrecheck(context.Background(), sc, tp, ragRoot, []string{storeRoot}, mountedOn("/store"))
	return err
}
