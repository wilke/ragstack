package drivers

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
)

// Containment in this package used to be LEXICAL in five drivers: they checked
// the string they were given against the approved roots and then operated on a
// path the kernel resolves differently. `<root>/link/x`, where `link` is a
// symlink to somewhere else entirely, is inside every approved root as a
// string and nowhere near one on disk — so an extraction wrote outside the
// deployment, a UI build emptied a directory outside it, a VACUUM INTO and a
// pg_dump wrote outside it, and `git worktree remove --force` DELETED outside
// it. Only RealFiles resolved first.
//
// Each test below plants exactly that symlink and asserts two things: the call
// is refused, and nothing happened at the place the link pointed at.

// escapeRoot returns an approved root, a directory outside every root, and the
// path of a symlink inside the root that points at it.
func escapeRoot(t *testing.T) (root, outside, link string) {
	t.Helper()
	root, outside = t.TempDir(), t.TempDir()
	link = filepath.Join(root, "escape")
	if err := os.Symlink(outside, link); err != nil {
		t.Fatal(err)
	}
	return root, outside, link
}

// untouched fails the test unless dir still holds exactly the names given.
func untouched(t *testing.T, dir string, want ...string) {
	t.Helper()
	ents, err := os.ReadDir(dir)
	if err != nil {
		t.Fatalf("reading %s back: %v", dir, err)
	}
	got := make([]string, 0, len(ents))
	for _, e := range ents {
		got = append(got, e.Name())
	}
	if len(got) != len(want) {
		t.Fatalf("%s holds %v, want exactly %v — the driver wrote or deleted outside the approved roots", dir, got, want)
	}
	for i, w := range want {
		if got[i] != w {
			t.Fatalf("%s holds %v, want exactly %v", dir, got, want)
		}
	}
}

func TestArchiveCreateRefusesAnOutPathUnderASymlinkedParent(t *testing.T) {
	root, outside, link := escapeRoot(t)
	src := filepath.Join(root, "bundle")
	if err := os.Mkdir(src, 0o750); err != nil {
		t.Fatal(err)
	}
	a := &RealArchive{opts: RealOptions{ApprovedRoots: []string{root}}}

	err := a.Create(context.Background(), src, filepath.Join(link, "bundle.tar"))
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("Create through a symlinked parent = %v, want a refusal", err)
	}
	untouched(t, outside)
}

func TestArchiveExtractRefusesADestinationSymlinkedOutOfTheRoots(t *testing.T) {
	ctx := context.Background()
	root, outside, link := escapeRoot(t)
	tarPath := tarOf(t, entry{name: "b1/manifest.json", body: "{}"})
	a := &RealArchive{opts: RealOptions{ApprovedRoots: []string{root}}}

	// The destination IS the symlink: it passed the lexical check, and the
	// driver then resolved it for the staging directory without re-checking.
	if err := a.Extract(ctx, tarPath, link, jobs.ArchiveLimits{}); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("Extract into a symlinked dest = %v, want a refusal", err)
	}
	// And a destination one level under it.
	inner := filepath.Join(outside, "inner")
	if err := os.Mkdir(inner, 0o750); err != nil {
		t.Fatal(err)
	}
	if err := a.Extract(ctx, tarPath, filepath.Join(link, "inner"), jobs.ArchiveLimits{}); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("Extract under a symlinked parent = %v, want a refusal", err)
	}
	untouched(t, outside, "inner")
	untouched(t, inner)
}

func TestBuildUIRefusesAnOutDirSymlinkedOutOfTheRoots(t *testing.T) {
	ctx := context.Background()
	root, outside, link := escapeRoot(t)
	// Something for --emptyOutDir to have destroyed.
	if err := os.WriteFile(filepath.Join(outside, "precious"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	worktree := artifact(t, root, true)
	node := viteStub(t, true)
	b := &RealBuild{run: &runner{}, Node: node.Path, Roots: []string{root}}

	for _, outDir := range []string{link, filepath.Join(link, "dist")} {
		if err := b.UI(ctx, worktree, "/ragstack/dev/ui/", outDir); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("UI with outDir %q = %v, want a refusal", outDir, err)
		}
	}
	node.ranNothing("a build whose outDir resolves outside the approved roots")
	untouched(t, outside, "precious")
}

func TestSQLiteBackupRefusesADestinationUnderASymlinkedParent(t *testing.T) {
	root, outside, link := escapeRoot(t)
	src := filepath.Join(root, "jobs.db")
	seedDB(t, src, 3)
	s := &RealSQLite{opts: RealOptions{ApprovedRoots: []string{root}}}

	_, err := s.Backup(context.Background(), src, filepath.Join(link, "jobs.db"))
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("Backup through a symlinked parent = %v, want a refusal", err)
	}
	untouched(t, outside)
}

func TestPostgresDumpRefusesAnOutPathUnderASymlinkedParent(t *testing.T) {
	bin, log := stubApptainer(t, pgDumpStub)
	d, spec, root := pgFixture(t, bin)
	outside := t.TempDir()
	link := filepath.Join(root, "escape")
	if err := os.Symlink(outside, link); err != nil {
		t.Fatal(err)
	}

	err := d.Postgres().Dump(context.Background(), spec, filepath.Join(link, "dev.dump"))
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("Dump through a symlinked parent = %v, want a refusal", err)
	}
	// The bind source is the directory apptainer would have mounted at
	// /mnt/ctl: the refusal has to come BEFORE the container runs.
	if _, statErr := os.Stat(log); statErr == nil {
		t.Error("the stub apptainer ran; a refused dump must never reach the container")
	}
	untouched(t, outside)
}

func TestGitWorktreeRefusesADestinationSymlinkedOutOfTheRoots(t *testing.T) {
	ctx := context.Background()
	g, stub, root := newGit(t)
	outside := t.TempDir()
	if err := os.WriteFile(filepath.Join(outside, "precious"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(root, "escape")
	if err := os.Symlink(outside, link); err != nil {
		t.Fatal(err)
	}
	mirror := t.TempDir()

	if err := g.AddWorktree(ctx, mirror, testSHA, filepath.Join(link, "wt")); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("AddWorktree under a symlinked parent = %v, want a refusal", err)
	}
	// `worktree remove --force` deletes a directory TREE, which is why the
	// leaf may not be a symlink either.
	if err := g.RemoveWorktree(ctx, mirror, link); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("RemoveWorktree of a symlinked dest = %v, want a refusal", err)
	}
	if err := g.RemoveWorktree(ctx, mirror, filepath.Join(link, "wt")); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("RemoveWorktree under a symlinked parent = %v, want a refusal", err)
	}
	stub.ranNothing("a worktree path that resolves outside the approved roots")
	untouched(t, outside, "precious")
}

// The other half of the same fix: the drivers do not merely refuse the escape,
// they USE the resolved path. A deployment whose data root is reached through
// a symlink — /rag/data itself may be one — must still work, and every path
// the driver then names must be the resolved one.
func TestDriversOperateOnTheResolvedPathWhenTheRootItselfIsALink(t *testing.T) {
	ctx := context.Background()
	real := t.TempDir()
	link := filepath.Join(t.TempDir(), "data")
	if err := os.Symlink(real, link); err != nil {
		t.Fatal(err)
	}
	// The approved root is the LINK's spelling; the driver resolves it.
	s := &RealSQLite{opts: RealOptions{ApprovedRoots: []string{link}}}
	src := filepath.Join(real, "jobs.db")
	seedDB(t, src, 2)

	if _, err := s.Backup(ctx, src, filepath.Join(link, "jobs.bak")); err != nil {
		t.Fatalf("Backup under a symlinked approved root = %v, want it to work", err)
	}
	if _, err := os.Stat(filepath.Join(real, "jobs.bak")); err != nil {
		t.Errorf("the backup is not at the resolved path: %v", err)
	}
}
