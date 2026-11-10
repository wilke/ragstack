package drivers

// CTL_STORE_DATA_ROOTS (RealOptions.StoreDataRoots, PR2 of the per-qdrant data
// dir): write roots of the files and instance drivers only, under the
// approved roots' strict rule, never of git, build or proc; and never a
// directory an approved or extra bind root shares.

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/paths"
)

// storeRootReal is NewReal over a temp /rag tree with one store root beside
// it (a separate directory, as /qdrant is beside /rag), an apptainer stub and
// a stand-in SIF under the store root's tenant tree.
func storeRootReal(t *testing.T, extra ...BindRoot) (*Real, *stub, string, string) {
	t.Helper()
	ragRoot := t.TempDir()
	store := t.TempDir()
	s := newStub(t, keyVerb)
	s.respond("list", `{"instances":[]}`, "", 0)
	roots := paths.NewRoots(ragRoot, paths.Overrides{})
	for _, d := range []string{roots.DataDir, roots.CtlStateDir} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	sif := filepath.Join(roots.CtlStateDir, "qdrant.sif")
	if err := os.WriteFile(sif, []byte("not really a SIF"), 0o644); err != nil {
		t.Fatal(err)
	}
	r := NewReal(RealOptions{Roots: roots, Apptainer: s.Path, StoreDataRoots: []string{store}, ExtraBindRoots: extra})
	return r, s, store, sif
}

func TestCheckStoreDataRoots(t *testing.T) {
	approved := []string{"/rag/data/tenants", "/rag/config/ctl"}
	extra := []BindRoot{{Path: "/rag/cache"}, {Path: "/scout/containers", ReadOnly: true}}
	for _, ok := range [][]string{nil, {"/qdrant"}, {"/qdrant", "/vol2/stores"}, {"/rag/qdrant"}} {
		if err := CheckStoreDataRoots(ok, approved, extra); err != nil {
			t.Errorf("CheckStoreDataRoots(%v) = %v, want nil", ok, err)
		}
	}
	for _, bad := range [][]string{
		{"/"}, {"relative"}, {"/qdrant/../etc"}, {"/qdrant/"},
		{"/qdrant", "/qdrant"},         // twice
		{"/qdrant", "/qdrant/tenants"}, // nested
		{"/rag/data/tenants"},          // an approved root
		{"/rag/data"},                  // contains an approved root
		{"/rag"},                       // contains every approved root
		{"/rag/data/tenants/x"},        // inside an approved root
		{"/rag/cache"},                 // an extra (rw) bind root
		{"/scout/containers"},          // an extra (ro) bind root: the tie rule
		{"/scout/containers/stores"},   // under an extra bind root
		{"/scout"},                     // contains an extra bind root
	} {
		if err := CheckStoreDataRoots(bad, approved, extra); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("CheckStoreDataRoots(%v) = %v, want a refusal", bad, err)
		}
	}
}

// A bind under a store root is accepted, and the argv names it resolved.
func TestInstancesBindUnderAStoreRootIsAccepted(t *testing.T) {
	r, s, store, sif := storeRootReal(t)
	storage := filepath.Join(store, "tenants", "dev", "storage")
	if err := os.MkdirAll(storage, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := r.Instances().Run(context.Background(), jobs.InstanceSpec{
		Name: "qdrant-dev", SIF: sif, Binds: []string{storage + ":/qdrant/storage"},
	}); err != nil {
		t.Fatalf("Run with a bind under a store root = %v, want success", err)
	}
	want := "--bind " + storage + ":/qdrant/storage "
	got := s.argv()
	if len(got) != 2 || !strings.Contains(got[1], want) {
		t.Fatalf("Run argv = %v, want an instance run carrying %q", got, want)
	}
}

// TestInstancesRunRefusesToFollowASymlinkedBind, replayed under a store root:
// the store root takes the approved roots' strict rule, so a link at the leaf
// is refused rather than followed.
func TestInstancesRefuseASymlinkedBindUnderAStoreRoot(t *testing.T) {
	r, s, store, sif := storeRootReal(t)
	outside := t.TempDir()
	link := filepath.Join(store, "storage-link")
	if err := os.Symlink(outside, link); err != nil {
		t.Fatal(err)
	}
	err := r.Instances().Run(context.Background(), jobs.InstanceSpec{
		Name: "qdrant-dev", SIF: sif, Binds: []string{link + ":/qdrant/storage"},
	})
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("Run with a symlinked bind under a store root = %v, want a refusal", err)
	}
	if got := s.argv(); len(got) > 0 && strings.Contains(strings.Join(got, "\n"), "instance run") {
		t.Fatalf("a symlinked bind under a store root reached apptainer: %v", got)
	}
}

// A store root is never bound WHOLE: that is the extra bind roots' privilege,
// and a store root is an approved root, not an extra one. (Mutation: put the
// store roots in ExtraRoots instead of Roots and this test fails.)
func TestInstancesRefuseAStoreRootBoundWhole(t *testing.T) {
	r, s, store, sif := storeRootReal(t)
	err := r.Instances().Run(context.Background(), jobs.InstanceSpec{
		Name: "qdrant-dev", SIF: sif, Binds: []string{store + ":/qdrant/storage"},
	})
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("Run binding the store root itself = %v, want a refusal", err)
	}
	if strings.Contains(strings.Join(s.argv(), "\n"), "instance run") {
		t.Fatalf("binding a store root whole reached apptainer: %v", s.argv())
	}
	if got := r.ExtraBindRoots(); len(got) != 0 {
		t.Errorf("the store root leaked into the extra bind roots: %v", got)
	}
}

// NewReal over a store root that shares a directory with an extra bind root
// refuses every start (and admits no store root), whatever the bind.
func TestNewRealRefusesAStoreRootThatOverlapsABindRoot(t *testing.T) {
	ragRoot := t.TempDir()
	store := t.TempDir()
	s := newStub(t, keyVerb)
	s.respond("list", `{"instances":[]}`, "", 0)
	roots := paths.NewRoots(ragRoot, paths.Overrides{})
	if err := os.MkdirAll(roots.CtlStateDir, 0o755); err != nil {
		t.Fatal(err)
	}
	sif := filepath.Join(roots.CtlStateDir, "qdrant.sif")
	if err := os.WriteFile(sif, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	storage := filepath.Join(store, "tenants", "dev", "storage")
	if err := os.MkdirAll(storage, 0o755); err != nil {
		t.Fatal(err)
	}
	r := NewReal(RealOptions{Roots: roots, Apptainer: s.Path, StoreDataRoots: []string{store},
		ExtraBindRoots: []BindRoot{{Path: store, ReadOnly: true}}})
	if got := r.StoreDataRoots(); len(got) != 0 {
		t.Errorf("an overlapping store root was admitted: %v", got)
	}
	err := r.Instances().Run(context.Background(), jobs.InstanceSpec{
		Name: "qdrant-dev", SIF: sif, Binds: []string{storage + ":/qdrant/storage"},
	})
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("Run through a driver with an overlapping store root = %v, want a refusal", err)
	}
	s.ranNothing("an instance driver built over an overlapping store root")
	if err := r.Files().MkdirAll(context.Background(), filepath.Join(store, "tenants", "x"), 0o750); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("the files driver wrote under a refused store root: %v", err)
	}
}

// The files driver writes, renames, copies and removes under a store root;
// git, build and proc — which never get the store roots — refuse the same
// path. (Mutation: hand the store roots to git and this test fails.)
func TestStoreRootsAreFilesAndInstancesRootsOnly(t *testing.T) {
	ctx := context.Background()
	r, _, store, _ := storeRootReal(t)
	files := r.Files()

	dir := filepath.Join(store, "tenants", "dev", "snapshots")
	if err := files.MkdirAll(ctx, dir, 0o750); err != nil {
		t.Fatalf("MkdirAll under a store root: %v", err)
	}
	a := filepath.Join(dir, "a.snapshot")
	if err := files.WriteAtomic(ctx, a, []byte("x"), 0o640); err != nil {
		t.Fatalf("WriteAtomic under a store root: %v", err)
	}
	b := filepath.Join(dir, "b.snapshot")
	if err := files.Rename(ctx, a, b); err != nil {
		t.Fatalf("Rename under a store root: %v", err)
	}
	c := filepath.Join(dir, "c.snapshot")
	if err := files.CopyFile(ctx, b, c, 0o640); err != nil {
		t.Fatalf("CopyFile into a store root: %v", err)
	}
	for _, p := range []string{b, c} {
		if err := files.Remove(ctx, p); err != nil {
			t.Fatalf("Remove under a store root: %v", err)
		}
	}
	// The store root itself is never a write target: strictly under, as for
	// every approved root.
	if err := files.WriteAtomic(ctx, store, []byte("x"), 0o640); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("WriteAtomic AT the store root = %v, want a refusal", err)
	}
	// RemoveTree keeps its own four deletion roots: no store root joins them.
	if err := files.RemoveTree(ctx, filepath.Join(store, "tenants", "dev")); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("RemoveTree under a store root = %v, want a refusal (purge of a moved dir is out of scope)", err)
	}

	target := filepath.Join(store, "tenants", "dev", "worktree")
	if _, err := r.git.checkDest(target); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("git may check a worktree out under a store root: %v", err)
	}
	if _, err := resolvedContainedNoLeafLink(filepath.Join(store, "tenants", "dev", "dist"), r.build.Roots); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("a UI build may write under a store root: %v", err)
	}
	if _, err := r.proc.spawnPath("pidfile", filepath.Join(store, "tenants", "dev", "api.pid")); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("Spawn may write a pidfile under a store root: %v", err)
	}
}

// The fake files driver, built with the store roots among its roots (as the
// engine builds it), refuses and accepts exactly what the real one does.
func TestFakeAndRealFilesAgreeUnderAStoreRoot(t *testing.T) {
	ctx := context.Background()
	r, _, store, _ := storeRootReal(t)
	approved := r.opts.ApprovedRoots
	fake := NewFake(FakeOptions{Roots: append(append([]string(nil), approved...), store)}).Files()
	other := t.TempDir()
	for _, c := range []struct {
		path string
		ok   bool
	}{
		{filepath.Join(store, "tenants", "dev", "storage"), true},
		{filepath.Join(store, "x"), true},
		{store, false},
		{filepath.Join(other, "x"), false},
		{filepath.Join(store, "..", filepath.Base(other), "x"), false},
	} {
		realErr := r.Files().MkdirAll(ctx, c.path, 0o750)
		fakeErr := fake.MkdirAll(ctx, c.path, 0o750)
		if c.ok && (realErr != nil || fakeErr != nil) {
			t.Errorf("MkdirAll(%s): real = %v, fake = %v; both must succeed", c.path, realErr, fakeErr)
		}
		if !c.ok && (!errors.Is(realErr, jobs.ErrRefused) || !errors.Is(fakeErr, jobs.ErrRefused)) {
			t.Errorf("MkdirAll(%s): real = %v, fake = %v; both must refuse", c.path, realErr, fakeErr)
		}
	}
}
