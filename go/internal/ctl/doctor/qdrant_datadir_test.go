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
// the drivers' roots is qdrant_data_dir_outside_roots; and the dir is under the
// permission checks.
func TestQdrantDataDirChecks(t *testing.T) {
	qdirCodes := []string{QdrantDataDirMissing, QdrantDataDirSymlink, QdrantDataDirOutsideRoots}

	// No field, nothing raised.
	w := newWorld(t)
	got := byCode(w.run(t))
	for _, c := range qdirCodes {
		if _, ok := got[c]; ok {
			t.Errorf("a row without data_dir raised %s", c)
		}
	}

	// Healthy: under the tenants root, storage/ present.
	w = newWorld(t)
	qdir := filepath.Join(w.roots.DataDir, "qdrant-stores", "dev")
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

	// Outside every approved root.
	w = newWorld(t)
	outside := filepath.Join(t.TempDir(), "qdrant", "dev")
	if err := os.MkdirAll(filepath.Join(outside, "storage"), 0o755); err != nil {
		t.Fatal(err)
	}
	w.tenant.Stores.Qdrant.DataDir = outside
	f, ok = byCode(w.run(t))[QdrantDataDirOutsideRoots]
	if !ok || f.Level != model.LevelError || !strings.Contains(f.Detail, outside) {
		t.Fatalf("outside roots: %+v (present=%v)", f, ok)
	}
}
