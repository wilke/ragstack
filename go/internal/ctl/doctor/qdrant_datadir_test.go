package doctor

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/hostfacts"
	"github.com/ragstack/ragstack/internal/ctl/model"
)

// A row that moves its qdrant data dir (stores.qdrant.data_dir): the healthy
// shape raises nothing; a missing storage/ is qdrant_data_dir_missing (warn,
// red for start/restart); a symlink is qdrant_data_dir_symlink; a dir outside
// the row's data_dir and every configured store root is
// qdrant_data_dir_outside_roots (an error since PR2); and the dir is under the
// permission checks.
func TestQdrantDataDirChecks(t *testing.T) {
	qdirCodes := []string{QdrantDataDirMissing, QdrantDataDirSymlink, QdrantDataDirOutsideRoots, QdrantDataDirRootNotMounted}

	// No field, nothing raised.
	w := newWorld(t)
	got := byCode(w.run(t))
	for _, c := range qdirCodes {
		if _, ok := got[c]; ok {
			t.Errorf("a row without data_dir raised %s", c)
		}
	}

	// Healthy: under the row's own data_dir, storage/ present.
	w = newWorld(t)
	qdir := filepath.Join(w.tenant.DataDir, "qdrant-b")
	if err := os.MkdirAll(filepath.Join(qdir, "storage"), 0o755); err != nil {
		t.Fatal(err)
	}
	w.tenant.Stores.Qdrant.DataDir = qdir
	got = byCode(w.run(t))
	for _, c := range qdirCodes {
		if f, ok := got[c]; ok {
			t.Errorf("a healthy moved qdrant dir raised %s: %s", c, f.Detail)
		}
	}
	// … and it is under the permission checks.
	w.host.Writables[qdir] = hostfacts.Writability{Writable: true, Path: qdir, Reason: "world-writable"}
	if f, ok := byCode(w.run(t))[WritableByOthers]; !ok || !strings.Contains(f.Detail, qdir) {
		t.Errorf("a world-writable qdrant data dir was not reported: %+v", f)
	}
	delete(w.host.Writables, qdir)

	// Missing storage/.
	if err := os.RemoveAll(filepath.Join(qdir, "storage")); err != nil {
		t.Fatal(err)
	}
	f, ok := byCode(w.run(t))[QdrantDataDirMissing]
	if !ok || f.Level != model.LevelWarn || !strings.Contains(f.Detail, filepath.Join(qdir, "storage")) {
		t.Fatalf("missing storage/: %+v (present=%v)", f, ok)
	}
	for _, op := range []string{"start", "restart"} {
		w.opts.Op = op
		if st := w.run(t).Status; st != model.StatusRed {
			t.Errorf("--op %s with a missing qdrant storage dir = %s, want red", op, st)
		}
	}
	w.opts.Op = ""

	// A symlinked storage/.
	elsewhere := filepath.Join(w.roots.DataDir, "elsewhere")
	if err := os.MkdirAll(elsewhere, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(elsewhere, filepath.Join(qdir, "storage")); err != nil {
		t.Fatal(err)
	}
	f, ok = byCode(w.run(t))[QdrantDataDirSymlink]
	if !ok || f.Level != model.LevelError {
		t.Fatalf("symlinked storage/: %+v (present=%v)", f, ok)
	}
	if _, dup := byCode(w.run(t))[QdrantDataDirMissing]; dup {
		t.Error("a symlink was ALSO reported missing")
	}

	// Outside every root that may hold it (no store root configured): an
	// ERROR, which makes the run red for every op on the tenant.
	w = newWorld(t)
	w.opts.StoreDataRoots = []string{}
	storeRoot := t.TempDir()
	outside := filepath.Join(storeRoot, "qdrant", "dev")
	if err := os.MkdirAll(filepath.Join(outside, "storage"), 0o755); err != nil {
		t.Fatal(err)
	}
	w.tenant.Stores.Qdrant.DataDir = outside
	got = byCode(w.run(t))
	f, ok = got[QdrantDataDirOutsideRoots]
	if !ok || f.Level != model.LevelError || !strings.Contains(f.Detail, outside) ||
		!strings.Contains(f.Detail, "CTL_STORE_DATA_ROOTS") {
		t.Fatalf("outside roots: %+v (present=%v), want an error naming the dir and the setting", f, ok)
	}
	if st := w.run(t).Status; st != model.StatusRed {
		t.Errorf("a moved dir outside every root = %s, want red", st)
	}
	// The same dir under a configured store root: no finding. (The store
	// root is what PR2 made configurable; with it the dir is honoured.)
	w.opts.StoreDataRoots = []string{storeRoot}
	if f, ok := byCode(w.run(t))[QdrantDataDirOutsideRoots]; ok {
		t.Errorf("a dir under a store root raised %s: %s", QdrantDataDirOutsideRoots, f.Detail)
	}
	// Another ctl root no longer admits it: only the row's data_dir does.
	w.opts.StoreDataRoots = []string{}
	w.tenant.Stores.Qdrant.DataDir = filepath.Join(w.roots.BackupsDir, "dev-qdrant")
	if f, ok := byCode(w.run(t))[QdrantDataDirOutsideRoots]; !ok || f.Level != model.LevelError {
		t.Errorf("a dir under the backups root = %+v (present=%v), want an error", f, ok)
	}
	w.tenant.Stores.Qdrant.DataDir = outside
	// Outside /rag and the deepest mount holding it is "/" (the fake's mount
	// table is empty): the store filesystem is not mounted.
	f, ok = got[QdrantDataDirRootNotMounted]
	if !ok || f.Level != model.LevelError || !strings.Contains(f.Detail, outside) {
		t.Fatalf("root fs: %+v (present=%v), want an error naming the dir", f, ok)
	}
	// The same dir on a mounted store filesystem: no finding.
	w.host.Mounts = map[string]string{"/": "ext4", filepath.Dir(filepath.Dir(outside)): "xfs"}
	if f, ok := byCode(w.run(t))[QdrantDataDirRootNotMounted]; ok {
		t.Errorf("a dir on a mounted filesystem raised %s: %s", QdrantDataDirRootNotMounted, f.Detail)
	}
}

// A moved dir inside the /rag tree is on /rag's own mount, which every unit is
// already conditioned on: the mount check does not apply, even when the mount
// table says "/" (as the test host's does for a TempDir).
func TestQdrantDataDirUnderRagSkipsTheMountCheck(t *testing.T) {
	w := newWorld(t)
	qdir := filepath.Join(w.roots.DataDir, "qdrant-stores", "dev")
	if err := os.MkdirAll(filepath.Join(qdir, "storage"), 0o755); err != nil {
		t.Fatal(err)
	}
	w.tenant.Stores.Qdrant.DataDir = qdir
	if f, ok := byCode(w.run(t))[QdrantDataDirRootNotMounted]; ok {
		t.Errorf("a dir under the rag root raised %s: %s", QdrantDataDirRootNotMounted, f.Detail)
	}
}

// With Options.StoreDataRoots nil, doctor reads CTL_STORE_DATA_ROOTS from the
// environment, then from <ctl config dir>/ctl.env — the order the daemon and
// --direct resolve it in — so the CLI and live doctors (which pass nothing)
// agree with the engine's.
func TestQdrantDataDirStoreRootsFromEnvironmentAndCtlEnv(t *testing.T) {
	storeRoot := t.TempDir()
	qdir := filepath.Join(storeRoot, "dev")
	if err := os.MkdirAll(filepath.Join(qdir, "storage"), 0o755); err != nil {
		t.Fatal(err)
	}
	raised := func(w *world) bool {
		_, ok := byCode(w.run(t))[QdrantDataDirOutsideRoots]
		return ok
	}

	t.Setenv(envStoreDataRoots, "")
	w := newWorld(t)
	w.tenant.Stores.Qdrant.DataDir = qdir
	if !raised(w) {
		t.Fatal("nothing configured: the finding is missing")
	}
	if err := os.MkdirAll(w.roots.CtlConfigDir, 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(w.roots.CtlConfigDir, "ctl.env"),
		[]byte("CTL_STORE_DATA_ROOTS="+storeRoot+"\n"), 0o640); err != nil {
		t.Fatal(err)
	}
	if raised(w) {
		t.Error("ctl.env names the store root, and the finding is still raised")
	}
	t.Setenv(envStoreDataRoots, "/elsewhere")
	if !raised(w) {
		t.Error("the environment overrides ctl.env, and the finding is missing")
	}
}
