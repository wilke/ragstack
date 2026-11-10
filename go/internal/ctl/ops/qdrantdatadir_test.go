package ops

import (
	"context"
	"errors"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/settings"
)

// PR2 of the per-qdrant data dir: the containment rule the ops hold a moved
// dir to (plan time), and the host facts the instance supervisor checks before
// it starts a qdrant there (run time).

// movedQdir is a moved dir on a store volume beside /rag, as /qdrant is on
// coconut. The fake's files are lexical, so nothing has to exist on the test
// host.
const movedQdir = "/qdrant/tenants/dev"

// mountedOn is a MountOf that answers mp for every path.
func mountedOn(mp string) func(string) (string, string, error) {
	return func(string) (string, string, error) { return mp, "xfs", nil }
}

// opWith is testDeps plus the store roots and the mount lookup a test chose.
func opWith(t *testing.T, oc jobs.Context, storeRoots []string, mountOf func(string) (string, string, error),
	verb string) jobs.Op {
	t.Helper()
	d := testDeps(oc)
	d.StoreDataRoots, d.MountOf = storeRoots, mountOf
	op, ok := NewRegistry(d).Lookup(verb)
	if !ok {
		t.Fatalf("no op %q", verb)
	}
	return op
}

// planStoreRoots is plan() with store data roots configured.
func planStoreRoots(t *testing.T, oc jobs.Context, storeRoots []string, verb string, args map[string]any) *jobs.Planned {
	t.Helper()
	p, err := opWith(t, oc, storeRoots, mountedOn("/qdrant"), verb).Plan(context.Background(), oc, args)
	if err != nil {
		t.Fatalf("%s: %v", verb, err)
	}
	return p
}

// movedInstanceFixture is instanceFixture with the row's qdrant moved to dir,
// and that dir's storage/ and snapshots/ present on the fake host.
func movedInstanceFixture(t *testing.T, dir string) (jobs.Context, *drivers.Fake) {
	t.Helper()
	oc, fake := instanceFixture(t)
	oc.Tenant.Stores.Qdrant.DataDir = dir
	// The store root joins the fake's roots, as the engine adds it: the fake
	// instance driver holds a qdrant bind to them (review F5).
	fake.FakeFiles().Roots = append(fake.FakeFiles().Roots, "/qdrant")
	for _, d := range []string{filepath.Dir(dir), dir, dir + "/storage", dir + "/snapshots"} {
		fake.FakeFiles().Dirs[d] = 0o2770
	}
	return oc, fake
}

// A backup of a row whose moved dir is outside every root that may hold it is
// refused before anything (the fence included); a store root admits it.
func TestBackupRefusesAQdrantDirOutsideEveryRoot(t *testing.T) {
	moved := func(tn *registry.Tenant) { managed(tn); tn.Stores.Qdrant.DataDir = movedQdir }
	oc, _ := fixture(t, "dev", moved)
	_, err := opWith(t, oc, nil, nil, "backup").Plan(context.Background(), oc, map[string]any{"fence": true})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "CTL_STORE_DATA_ROOTS") {
		t.Fatalf("backup of a moved dir with no store roots = %v, want a refusal naming CTL_STORE_DATA_ROOTS", err)
	}
	// A root that does not hold it is no better.
	if _, err := opWith(t, oc, []string{"/vol2"}, nil, "backup").Plan(context.Background(), oc, nil); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("backup with an unrelated store root = %v, want a refusal", err)
	}
	planStoreRoots(t, oc, []string{"/qdrant"}, "backup", map[string]any{"fence": true})
	// A light bundle takes no store snapshot, so it does not care.
	if _, err := opWith(t, oc, nil, nil, "backup").Plan(context.Background(), oc, map[string]any{"scope": []any{"config"}}); err != nil {
		t.Errorf("a config-only backup of a moved row = %v, want a plan", err)
	}
}

// A start refuses the same row under BOTH supervisors (the unit binds the dir
// as surely as the instance does), and a store root admits it.
func TestStartRefusesAQdrantDirOutsideEveryRoot(t *testing.T) {
	for _, sup := range []string{supervisorSystemd, supervisorInstance} {
		moved := func(tn *registry.Tenant) {
			instanceManaged(tn)
			tn.Supervisor = sup
			tn.Stores.Qdrant.DataDir = movedQdir
		}
		oc, _ := fixture(t, "dev", moved)
		for _, args := range []map[string]any{nil, {"only": []string{"qdrant"}}} {
			_, err := opWith(t, oc, nil, nil, "start").Plan(context.Background(), oc, args)
			if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), movedQdir) {
				t.Errorf("%s start %v of a moved dir with no store roots = %v, want a refusal naming it", sup, args, err)
			}
			planStoreRoots(t, oc, []string{"/qdrant"}, "start", args)
		}
		// Under the row's own data_dir needs no store root.
		oc.Tenant.Stores.Qdrant.DataDir = oc.Tenant.DataDir + "/qdrant-b"
		if _, err := opWith(t, oc, nil, nil, "start").Plan(context.Background(), oc, nil); err != nil {
			t.Errorf("%s start of a dir under the row's data_dir = %v, want a plan", sup, err)
		}
	}
}

// runQdrantStart plans `start --only qdrant` over the fixture and runs it,
// returning the first step error.
func runQdrantStart(t *testing.T, oc jobs.Context, fake *drivers.Fake, mountOf func(string) (string, string, error)) error {
	t.Helper()
	p, err := opWith(t, oc, []string{"/qdrant"}, mountOf, "start").Plan(context.Background(), oc,
		map[string]any{"only": []string{"qdrant"}})
	if err != nil {
		t.Fatalf("plan: %v", err)
	}
	r := newRunner(oc, fake)
	for _, s := range p.Steps {
		if _, err := r.run(s); err != nil {
			return err
		}
	}
	return nil
}

// The pre-start check: dir, storage/ and snapshots/ real directories, and the
// volume mounted. Each failure refuses and starts nothing.
func TestInstanceStartPrechecksAMovedQdrantDir(t *testing.T) {
	ran := func(fake *drivers.Fake) bool {
		return strings.Contains(strings.Join(fake.CallKeys(), "\n"), "instances.Run(qdrant-dev,")
	}

	// Healthy, on a mounted volume: started.
	oc, fake := movedInstanceFixture(t, movedQdir)
	if err := runQdrantStart(t, oc, fake, mountedOn("/qdrant")); err != nil {
		t.Fatalf("a healthy moved dir on a mounted volume = %v, want a start", err)
	}
	if !ran(fake) {
		t.Fatal("a healthy moved dir was not started")
	}

	for _, c := range []struct {
		name    string
		break_  func(*drivers.Fake)
		mountOf func(string) (string, string, error)
		want    string
	}{
		{"storage missing", func(f *drivers.Fake) { delete(f.FakeFiles().Dirs, movedQdir+"/storage") },
			mountedOn("/qdrant"), movedQdir + "/storage"},
		{"snapshots missing", func(f *drivers.Fake) { delete(f.FakeFiles().Dirs, movedQdir+"/snapshots") },
			mountedOn("/qdrant"), movedQdir + "/snapshots"},
		{"the dir itself missing", func(f *drivers.Fake) {
			for _, d := range []string{movedQdir, movedQdir + "/storage", movedQdir + "/snapshots"} {
				delete(f.FakeFiles().Dirs, d)
			}
		}, mountedOn("/qdrant"), movedQdir},
		// Review F1: a link BETWEEN the store root and the dir, with the dir,
		// storage/ and snapshots/ all real directories behind it.
		{"an intermediate component is a symlink", func(f *drivers.Fake) {
			delete(f.FakeFiles().Dirs, "/qdrant/tenants")
			f.FakeFiles().PutSymlink("/qdrant/tenants")
		}, mountedOn("/qdrant"), "/qdrant/tenants is a symlink"},
		{"storage is a symlink", func(f *drivers.Fake) {
			delete(f.FakeFiles().Dirs, movedQdir+"/storage")
			f.FakeFiles().PutSymlink(movedQdir + "/storage")
		}, mountedOn("/qdrant"), "symlink"},
		{"snapshots is a file", func(f *drivers.Fake) {
			delete(f.FakeFiles().Dirs, movedQdir+"/snapshots")
			f.FakeFiles().Put(movedQdir+"/snapshots", []byte("x"), 0o640)
		}, mountedOn("/qdrant"), "not a directory"},
		{"the volume is not mounted", func(*drivers.Fake) {}, mountedOn("/"), "root filesystem"},
		{"no mount table", func(*drivers.Fake) {}, nil, "mount table"},
		{"the mount lookup fails", func(*drivers.Fake) {},
			func(string) (string, string, error) { return "", "", errors.New("no mountinfo") }, "mount"},
	} {
		t.Run(c.name, func(t *testing.T) {
			oc, fake := movedInstanceFixture(t, movedQdir)
			c.break_(fake)
			err := runQdrantStart(t, oc, fake, c.mountOf)
			if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), c.want) {
				t.Fatalf("start = %v, want a refusal mentioning %q", err, c.want)
			}
			if ran(fake) {
				t.Fatal("the instance was started anyway")
			}
		})
	}

	// A moved dir under the rag root (the row's own data_dir) is on /rag's
	// mount: no mount check, even with no mount table at all.
	oc, fake = movedInstanceFixture(t, "/rag/data/tenants/dev/qdrant-b")
	if err := runQdrantStart(t, oc, fake, nil); err != nil {
		t.Fatalf("a moved dir under the rag root = %v, want a start", err)
	}
	if !ran(fake) {
		t.Fatal("a moved dir under the rag root was not started")
	}
}

// A row on the default layout is never prechecked: nothing about it changes.
func TestInstanceStartOfADefaultQdrantDirHasNoPrecheck(t *testing.T) {
	oc, fake := instanceFixture(t)
	if err := runQdrantStart(t, oc, fake, nil); err != nil {
		t.Fatalf("start of a default-layout qdrant with no mount table = %v, want a start", err)
	}
	if strings.Contains(strings.Join(fake.CallKeys(), "\n"), "files.Stat(/rag/data/tenants/dev/qdrant") {
		t.Error("a default-layout qdrant was prechecked")
	}
}

// The surface denylist names every row's moved qdrant dir (this row's
// included) and every store root.
func TestSurfaceDeniesQdrantDataDirsAndStoreRoots(t *testing.T) {
	oc, _ := fixture(t, "dev", managed)
	var other *registry.Tenant
	for name, tn := range oc.Fleet.Tenants {
		if name != "dev" {
			other = tn
			break
		}
	}
	if other == nil {
		t.Fatal("the fixture fleet has one tenant")
	}
	oc.Tenant.Stores.Qdrant.DataDir = "/qdrant/tenants/dev"
	other.Stores.Qdrant.DataDir = "/qdrant/tenants/" + other.Name
	d := testDeps(oc)
	d.StoreDataRoots = []string{"/qdrant", "/vol2"}
	p := &planner{oc: oc, t: oc.Tenant, op: &op{deps: d}}
	c := p.surfaceContext(oc.Tenant)
	denied := map[string]bool{}
	for _, r := range c.Denied {
		denied[r.Path] = true
	}
	for _, want := range []string{"/qdrant", "/vol2", "/qdrant/tenants/dev", "/qdrant/tenants/" + other.Name} {
		if !denied[want] {
			t.Errorf("%s is not in the surface denylist %v", want, c.Denied)
		}
	}
	// …and a path setting into one is refused.
	if _, err := settings.ValidateSurface("JOB_STORE_PATH", "/qdrant/tenants/dev/jobs.db", c); err == nil {
		t.Error("a surface path under a moved qdrant dir was accepted")
	}
}
