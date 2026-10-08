package drivers

import (
	"context"
	"errors"
	"io/fs"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
)

// A fake that accepts what the real driver refuses is worse than no fake: the
// step that does the forbidden thing passes every test and is refused for the
// first time on the host, inside a job, where the refusal reads as an outage
// rather than as a test failure. Eight such divergences are pinned here — each
// case runs the SAME call against the fake and against the real driver and
// requires both to refuse.

func refused(t *testing.T, what string, err error) {
	t.Helper()
	if err == nil {
		t.Errorf("%s = nil, want a refusal", what)
	}
}

func refusedWithSentinel(t *testing.T, what string, err error) {
	t.Helper()
	if !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("%s = %v, want a jobs.ErrRefused", what, err)
	}
}

func TestFakeAndRealSystemdRefuseTheSameUnitNames(t *testing.T) {
	ctx := context.Background()
	f := NewFake(FakeOptions{})
	real, stub := newSystemd(t)

	// `ssh-agent.service` is the case the allowlist exists for: this control
	// plane runs in a user manager whose other units are the operator's own
	// session. The fake took any name at all.
	for _, unit := range []string{"ssh-agent.service", "ragstack-dev-redis.service", "", "ragstack-dev.target\n"} {
		refusedWithSentinel(t, "fake Stop("+unit+")", f.Systemd().Stop(ctx, unit))
		refusedWithSentinel(t, "real Stop("+unit+")", real.Stop(ctx, unit))
		refusedWithSentinel(t, "fake Start("+unit+")", f.Systemd().Start(ctx, unit))
		refusedWithSentinel(t, "real Start("+unit+")", real.Start(ctx, unit))
		refusedWithSentinel(t, "fake Enable("+unit+")", f.Systemd().Enable(ctx, unit))
		refusedWithSentinel(t, "fake Disable("+unit+")", f.Systemd().Disable(ctx, unit))
		refusedWithSentinel(t, "fake ResetFailed("+unit+")", f.Systemd().ResetFailed(ctx, unit))
		_, ferr := f.Systemd().IsActive(ctx, unit)
		refusedWithSentinel(t, "fake IsActive("+unit+")", ferr)
		_, ferr = f.Systemd().IsEnabled(ctx, unit)
		refusedWithSentinel(t, "fake IsEnabled("+unit+")", ferr)
		_, ferr = f.Systemd().Show(ctx, unit)
		refusedWithSentinel(t, "fake Show("+unit+")", ferr)
	}
	// Link is checked on the unit file's BASE name, in both.
	refusedWithSentinel(t, "fake Link", f.Systemd().Link(ctx, "/rag/config/ctl/units/ssh-agent.service"))
	refusedWithSentinel(t, "real Link", real.Link(ctx, "/rag/config/ctl/units/ssh-agent.service"))
	refusedWithSentinel(t, "fake Link (relative)", f.Systemd().Link(ctx, "units/ragstack-dev-api.service"))
	stub.ranNothing("a unit name outside the allowlist")

	// The names the renderer really produces still work on the fake.
	if err := f.Systemd().Start(ctx, "ragstack-dev-api.service"); err != nil {
		t.Errorf("Start of a managed unit = %v", err)
	}
}

func TestFakeAndRealGitAddWorktreeRefuseTheSameCheckouts(t *testing.T) {
	ctx := context.Background()
	f := NewFake(FakeOptions{Roots: []string{"/rag"}, Worktrees: map[string]string{"/rag/data/worktrees/taken": sha40}})
	real, stub, root := newGit(t)
	taken := filepath.Join(root, "taken")
	if err := os.Mkdir(taken, 0o750); err != nil {
		t.Fatal(err)
	}

	// A ref that is not a resolved 40-hex commit: a worktree on a branch moves
	// under the tenant the next time anything re-runs.
	for _, sha := range []string{"main", "v1.5.3", sha40[:39], strings.ToUpper(sha40), ""} {
		refusedWithSentinel(t, "fake AddWorktree("+sha+")",
			f.Git().AddWorktree(ctx, "/rag/repos/ragstack.git", sha, "/rag/data/worktrees/new"))
		refusedWithSentinel(t, "real AddWorktree("+sha+")",
			real.AddWorktree(ctx, t.TempDir(), sha, filepath.Join(root, "new")))
	}
	// A destination that already exists.
	refusedWithSentinel(t, "fake AddWorktree over an existing dest",
		f.Git().AddWorktree(ctx, "/rag/repos/ragstack.git", sha40, "/rag/data/worktrees/taken"))
	refusedWithSentinel(t, "real AddWorktree over an existing dest",
		real.AddWorktree(ctx, t.TempDir(), testSHA, taken))
	stub.ranNothing("a checkout the driver refuses")
}

func TestFakeAndRealBuildUIRefuseTheSameBuilds(t *testing.T) {
	ctx := context.Background()
	f := NewFake(FakeOptions{Roots: []string{"/rag"}, Installed: []string{"/rag/data/artifacts/main-abc/worktree"}})
	root := t.TempDir()
	node := viteStub(t, true)
	real := &RealBuild{run: &runner{}, Node: node.Path, Roots: []string{root}}
	worktree := artifact(t, root, true)

	for _, base := range []string{"/ragstack/dev/ui", "ragstack/dev/ui/", "/ragstack/Dev/ui/", ""} {
		refusedWithSentinel(t, "fake UI(base="+base+")",
			f.Build().UI(ctx, "/rag/data/artifacts/main-abc/worktree", base, "/rag/data/tenants/dev/ui/dist"))
		refusedWithSentinel(t, "real UI(base="+base+")",
			real.UI(ctx, worktree, base, filepath.Join(root, "dist")))
	}
	// --emptyOutDir deletes first, so an outDir outside the approved roots is
	// a delete of any path the caller named.
	refusedWithSentinel(t, "fake UI(outDir outside)",
		f.Build().UI(ctx, "/rag/data/artifacts/main-abc/worktree", "/ragstack/dev/ui/", "/etc/ragstack/ui"))
	refusedWithSentinel(t, "real UI(outDir outside)",
		real.UI(ctx, worktree, "/ragstack/dev/ui/", filepath.Join(t.TempDir(), "ui")))
	node.ranNothing("a build the driver refuses")
}

func TestFakeAndRealSQLiteBackupRefuseTheSameDestinations(t *testing.T) {
	ctx := context.Background()
	f := seededFake(t)
	src := "/rag/data/tenants/dev/state/jobs.db"
	// The bundle directory exists (seededFake made it) and already holds a
	// backup: a second one never overwrites it.
	dst := "/rag/backups/dev/b1/state/jobs.db"
	if _, err := f.SQLite().Backup(ctx, src, dst); err != nil {
		t.Fatal(err)
	}
	refusedWithSentinel(t, "fake Backup over an existing dst", errOf2(f.SQLite().Backup(ctx, src, dst)))
	refusedWithSentinel(t, "fake Backup into a missing directory",
		errOf2(f.SQLite().Backup(ctx, src, "/rag/backups/dev/b2/state/jobs.db")))

	root := t.TempDir()
	real := &RealSQLite{opts: RealOptions{ApprovedRoots: []string{root}}}
	rsrc := filepath.Join(root, "jobs.db")
	seedDB(t, rsrc, 2)
	rdst := filepath.Join(root, "b1", "jobs.db")
	if err := os.Mkdir(filepath.Dir(rdst), 0o750); err != nil {
		t.Fatal(err)
	}
	if _, err := real.Backup(ctx, rsrc, rdst); err != nil {
		t.Fatal(err)
	}
	refusedWithSentinel(t, "real Backup over an existing dst", errOf2(real.Backup(ctx, rsrc, rdst)))
	refusedWithSentinel(t, "real Backup into a missing directory",
		errOf2(real.Backup(ctx, rsrc, filepath.Join(root, "b2", "jobs.db"))))
}

// errOf2 drops the value of a (T, error) pair.
func errOf2(_ string, err error) error { return err }

func TestFakeAndRealPostgresDumpRequireTheOutputDirectory(t *testing.T) {
	ctx := context.Background()
	f := NewFake(FakeOptions{Roots: []string{"/rag"}})
	refusedWithSentinel(t, "fake Dump into a missing directory",
		f.Postgres().Dump(ctx, pgSpec(), "/rag/backups/dev/b1/postgres/dev.dump"))

	bin, log := stubApptainer(t, pgDumpStub)
	d, spec, root := pgFixture(t, bin)
	refusedWithSentinel(t, "real Dump into a missing directory",
		d.Postgres().Dump(ctx, spec, filepath.Join(root, "b1", "postgres", "dev.dump")))
	if _, err := os.Stat(log); err == nil {
		t.Error("the stub apptainer ran; the directory check happens before the container")
	}
}

func TestFakeAndRealArchiveCreateRefuseAnExistingOut(t *testing.T) {
	ctx := context.Background()
	f := seededFake(t)
	out := "/rag/backups/dev/b1.tar"
	if err := f.Archive().Create(ctx, "/rag/backups/dev/b1", out); err != nil {
		t.Fatal(err)
	}
	refusedWithSentinel(t, "fake Create over an existing tar", f.Archive().Create(ctx, "/rag/backups/dev/b1", out))

	root := t.TempDir()
	real := &RealArchive{opts: RealOptions{ApprovedRoots: []string{root}}}
	dir := filepath.Join(root, "b1")
	if err := os.Mkdir(dir, 0o750); err != nil {
		t.Fatal(err)
	}
	rout := filepath.Join(root, "b1.tar")
	if err := real.Create(ctx, dir, rout); err != nil {
		t.Fatal(err)
	}
	refusedWithSentinel(t, "real Create over an existing tar", real.Create(ctx, dir, rout))
}

func TestFakeAndRealFilesRemoveRefuseANonEmptyDirectory(t *testing.T) {
	ctx := context.Background()
	f := NewFake(FakeOptions{
		Roots: []string{"/rag"},
		Files: map[string][]byte{"/rag/data/tenants/dev/config/tenant.env": []byte("x")},
	})
	refused(t, "fake Remove of a non-empty directory", f.Files().Remove(ctx, "/rag/data/tenants/dev"))
	if f.FakeFiles().Content("/rag/data/tenants/dev/config/tenant.env") == nil {
		t.Error("the fake deleted the directory's contents; Remove is not a recursive delete")
	}

	real, root := realFiles(t)
	dir := filepath.Join(root, "tenant")
	if err := os.MkdirAll(filepath.Join(dir, "config"), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "config", "tenant.env"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	refused(t, "real Remove of a non-empty directory", real.Remove(ctx, dir))
	if _, err := os.Stat(filepath.Join(dir, "config", "tenant.env")); err != nil {
		t.Errorf("the real driver deleted through a non-empty directory: %v", err)
	}
	// An EMPTY directory goes, in both.
	empty := filepath.Join(root, "empty")
	if err := os.Mkdir(empty, 0o750); err != nil {
		t.Fatal(err)
	}
	if err := real.Remove(ctx, empty); err != nil {
		t.Errorf("real Remove of an empty directory = %v", err)
	}
	if err := f.Files().MkdirAll(ctx, "/rag/data/tenants/empty", 0o2770); err != nil {
		t.Fatal(err)
	}
	if err := f.Files().Remove(ctx, "/rag/data/tenants/empty"); err != nil {
		t.Errorf("fake Remove of an empty directory = %v", err)
	}
	if _, still := f.FakeFiles().Dirs["/rag/data/tenants/empty"]; still {
		t.Error("the fake kept the directory it removed")
	}
}

func TestFakeAndRealFilesRenameRefuseAnExistingDestination(t *testing.T) {
	ctx := context.Background()
	f := NewFake(FakeOptions{
		Roots: []string{"/rag"},
		Files: map[string][]byte{
			"/rag/data/tenants/dev/config/tenant.env":  []byte("new"),
			"/rag/data/tenants/dev2/config/tenant.env": []byte("precious"),
		},
	})
	refusedWithSentinel(t, "fake Rename onto an existing file", f.Files().Rename(ctx,
		"/rag/data/tenants/dev/config/tenant.env", "/rag/data/tenants/dev2/config/tenant.env"))
	if got := string(f.FakeFiles().Content("/rag/data/tenants/dev2/config/tenant.env")); got != "precious" {
		t.Errorf("the fake overwrote the destination: %q", got)
	}

	real, root := realFiles(t)
	from, to := filepath.Join(root, "from"), filepath.Join(root, "to")
	for _, p := range []string{from, to} {
		if err := os.WriteFile(p, []byte(filepath.Base(p)), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	refusedWithSentinel(t, "real Rename onto an existing file", real.Rename(ctx, from, to))
	if got, _ := os.ReadFile(to); string(got) != "to" {
		t.Errorf("the real driver overwrote the destination: %q", got)
	}
}

func TestFakeAndRealQdrantCountRefuseAnUnknownCollection(t *testing.T) {
	ctx := context.Background()
	f := NewFake(FakeOptions{Collections: map[string][]string{"http://localhost:24041": {"docs"}}})
	if _, err := f.Qdrant().Count(ctx, "http://localhost:24041", "ghost"); err == nil {
		t.Error("the fake counted a collection that is not there; the store answers 404")
	}
	if _, err := f.Qdrant().Count(ctx, "http://localhost:24041", "docs"); err != nil {
		t.Errorf("Count of a known collection = %v", err)
	}

	s := newStubStore(t)
	s.routes["POST /collections/ghost/points/count"] = func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"status":{"error":"Not found: Collection ghost doesn't exist!"}}`))
	}
	if _, err := realStores(t).Qdrant().Count(ctx, s.url(), "ghost"); err == nil {
		t.Error("the real driver accepted a 404 count")
	}
}

// ---------------------------------------------------------------- RemoveTree

// removeTreeHost lays the same host down twice — on disk under a temp root for
// the real driver, and in memory at the SAME absolute paths for the fake — so
// that one table of RemoveTree calls can be put to both and their verdicts
// compared one for one. Every deletable shape is there, and next to each one
// the near miss a bug would reach for: the live data directory beside its
// quarantined copy, a non-bundle beside a bundle, a symlink wearing a
// deletable name, a symlinked component between a root and a deletable leaf.
type removeTreeHost struct {
	R, outside string
	tree       TreeRoots
	real       *RealFiles
	fake       *FakeFiles
}

const (
	rtQuarantined = "dev.quarantined-20260914T093000Z"
	rtBundle      = "20260914T080000Z-backup"
)

func newRemoveTreeHost(t *testing.T) *removeTreeHost {
	t.Helper()
	R, outside := t.TempDir(), t.TempDir()
	h := &removeTreeHost{R: R, outside: outside, tree: TreeRoots{
		DataDir: filepath.Join(R, "data", "tenants"), BackupsDir: filepath.Join(R, "backups", "tenants"),
		ReposDir: filepath.Join(R, "repos", "tenants"), UnitsDir: filepath.Join(R, "config", "ctl", "units"),
	}}
	h.real = &RealFiles{Roots: []string{R}, Tree: h.tree}
	h.fake = NewFake(FakeOptions{Roots: []string{R}, TreeRoots: h.tree}).FakeFiles()

	// Somebody else's data, which every symlink below points into.
	if err := os.WriteFile(filepath.Join(outside, "precious"), []byte("not the ctl's"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(outside, rtBundle), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(outside, rtBundle, "x"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	files := []string{
		"data/tenants/dev/config/tenant.env", // the LIVE tenant
		"data/tenants/" + rtQuarantined + "/RECOVERY.json",
		"data/tenants/" + rtQuarantined + "/qdrant/storage/seg",
		"backups/tenants/dev/" + rtBundle + "/SHA256SUMS",
		"backups/tenants/dev/" + rtBundle + "/parts/config.json",
		"backups/tenants/dev/" + rtBundle + ".tar",
		"backups/tenants/dev/notabundle/x",
		"backups/tenants/dev/20260914T090000Z-backup.tar/x", // a DIRECTORY wearing the .tar name
		"repos/tenants/dev/python/main.py",
		"config/ctl/units/dev/x",
		"config/ctl/units/ragstack-dev-api.service",
		"config/ctl/ctl.env",
		"data/ctl/jobs.db",
	}
	for _, rel := range files {
		p := filepath.Join(R, rel)
		if err := os.MkdirAll(filepath.Dir(p), 0o750); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(rel), 0o640); err != nil {
			t.Fatal(err)
		}
		h.fake.Put(p, []byte(rel), 0o640)
	}
	// A regular FILE wearing a quarantined tree's name.
	notDir := filepath.Join(R, "data", "tenants", "file.quarantined-20260914T093000Z")
	if err := os.WriteFile(notDir, []byte("f"), 0o640); err != nil {
		t.Fatal(err)
	}
	h.fake.Put(notDir, []byte("f"), 0o640)
	// The symlinks: one per root wearing a deletable name, and one as the
	// COMPONENT between the backup root and a bundle-shaped leaf.
	for _, rel := range []string{
		"data/tenants/linked.quarantined-20260914T093000Z",
		"backups/tenants/linkt",
		"repos/tenants/linkw",
		"config/ctl/units/linku",
		"backups/tenants/via",
	} {
		p := filepath.Join(R, rel)
		if err := os.Symlink(outside, p); err != nil {
			t.Fatal(err)
		}
		h.fake.PutSymlink(p)
	}
	// What the fake sees through `via`: the bundle the link points at.
	h.fake.Put(filepath.Join(R, "backups/tenants/via", rtBundle, "x"), []byte("x"), 0o600)
	return h
}

// gone reports whether path is absent on BOTH hosts, failing the test if the
// two disagree.
func (h *removeTreeHost) gone(t *testing.T, path string) bool {
	t.Helper()
	_, rerr := os.Lstat(path)
	_, ferr := h.fake.Stat(context.Background(), path)
	realGone, fakeGone := errors.Is(rerr, os.ErrNotExist), errors.Is(ferr, fs.ErrNotExist)
	if realGone != fakeGone {
		t.Errorf("%s: real gone=%v, fake gone=%v", path, realGone, fakeGone)
	}
	return realGone
}

// removeTreeRefusals are the paths RemoveTree must refuse, on both drivers,
// each with why. Every containment rule of PR-G1.4 is a row here.
func removeTreeRefusals(h *removeTreeHost) []struct{ path, why string } {
	R, tr := h.R, h.tree
	return []struct{ path, why string }{
		// Each root itself.
		{tr.DataDir, "the data root itself"},
		{tr.BackupsDir, "the backup root itself"},
		{tr.ReposDir, "the worktree root itself"},
		{tr.UnitsDir, "the units directory itself"},
		// Outside every deletion root (though inside the approved root R).
		{filepath.Join(R, "config", "ctl", "ctl.env"), "the ctl's own config, outside every deletion root"},
		{filepath.Join(R, "data", "ctl"), "the ctl state dir, outside every deletion root"},
		{R, "the approved root itself"},
		{"/etc", "outside everything"},
		{h.outside, "a temp dir outside everything"},
		// A symlinked directory inside each root, wearing a deletable name.
		{filepath.Join(tr.DataDir, "linked.quarantined-20260914T093000Z"), "a symlink under the data root"},
		{filepath.Join(tr.BackupsDir, "linkt"), "a symlink under the backup root"},
		{filepath.Join(tr.ReposDir, "linkw"), "a symlink under the worktree root"},
		{filepath.Join(tr.UnitsDir, "linku"), "a symlink under the units directory"},
		// A symlinked COMPONENT between the root and a bundle-shaped leaf.
		{filepath.Join(tr.BackupsDir, "via", rtBundle), "a bundle reached through a symlinked tenant dir"},
		// The data root: only a quarantined tree, one level down.
		{filepath.Join(tr.DataDir, "dev"), "a LIVE tenant's data directory"},
		{filepath.Join(tr.DataDir, rtQuarantined, "qdrant"), "inside a quarantined tree, not the tree"},
		{filepath.Join(tr.DataDir, ".quarantined-20260914T093000Z"), "a marker with no tenant name"},
		{filepath.Join(tr.DataDir, "dev.quarantined-"), "a marker with no stamp"},
		{filepath.Join(tr.DataDir, "file.quarantined-20260914T093000Z"), "a regular file, not a tree"},
		// The backup root: <tenant>, <tenant>/<bundle-id>, <tenant>/<bundle-id>.tar.
		{filepath.Join(tr.BackupsDir, "dev", "notabundle"), "two levels down, not a bundle id"},
		{filepath.Join(tr.BackupsDir, "dev", rtBundle, "parts"), "three levels down"},
		{filepath.Join(tr.BackupsDir, "dev", "20260914T090000Z-backup.tar"), "a .tar that is a directory"},
		{filepath.Join(tr.BackupsDir, "Dev"), "not a tenant name"},
		// The worktree root and the units directory: <tenant>, one level.
		{filepath.Join(tr.ReposDir, "dev", "python"), "inside a worktree, not the worktree"},
		{filepath.Join(tr.UnitsDir, "ragstack-dev-api.service"), "a unit FILE, not a tenant's directory"},
		// Relative and unclean spellings.
		{"data/tenants/" + rtQuarantined, "a relative path"},
		{"", "the empty path"},
		{filepath.Join(tr.ReposDir, "dev") + "/", "a trailing slash"},
		{tr.ReposDir + "/../tenants/dev", "a `..` spelling of a deletable path"},
		{tr.DataDir + "/" + rtQuarantined + "/../dev", "a `..` that lands on the live tenant"},
		{tr.ReposDir + "//dev", "a doubled slash"},
	}
}

// The fake and the real driver give the SAME verdict on every RemoveTree in
// the table, refuse with jobs.ErrRefused, and delete nothing when they refuse.
func TestFakeAndRealFilesRemoveTreeAgreeOnEveryVerdict(t *testing.T) {
	ctx := context.Background()
	h := newRemoveTreeHost(t)
	for _, c := range removeTreeRefusals(h) {
		rerr := h.real.RemoveTree(ctx, c.path)
		ferr := h.fake.RemoveTree(ctx, c.path)
		if !errors.Is(rerr, jobs.ErrRefused) || !errors.Is(ferr, jobs.ErrRefused) {
			t.Errorf("%s (%q): real = %v, fake = %v; both must refuse with ErrRefused", c.why, c.path, rerr, ferr)
		}
	}
	// Nothing was touched by a refusal: the live tenant, the trees beside it,
	// and above all what every symlink points at.
	for _, rel := range []string{"data/tenants/dev/config/tenant.env", "data/tenants/" + rtQuarantined + "/RECOVERY.json",
		"backups/tenants/dev/notabundle/x", "repos/tenants/dev/python/main.py", "config/ctl/ctl.env"} {
		if h.gone(t, filepath.Join(h.R, rel)) {
			t.Errorf("a refused RemoveTree deleted %s", rel)
		}
	}
	untouched(t, h.outside, rtBundle, "precious")

	// The deletable shapes, in an order that leaves each parent for later.
	tr := h.tree
	for _, p := range []string{
		filepath.Join(tr.DataDir, rtQuarantined),
		filepath.Join(tr.BackupsDir, "dev", rtBundle+".tar"),
		filepath.Join(tr.BackupsDir, "dev", rtBundle),
		filepath.Join(tr.BackupsDir, "dev"),
		filepath.Join(tr.ReposDir, "dev"),
		filepath.Join(tr.UnitsDir, "dev"),
	} {
		if err := h.real.RemoveTree(ctx, p); err != nil {
			t.Errorf("real RemoveTree(%s) = %v", p, err)
		}
		if err := h.fake.RemoveTree(ctx, p); err != nil {
			t.Errorf("fake RemoveTree(%s) = %v", p, err)
		}
		if !h.gone(t, p) {
			t.Errorf("%s survived RemoveTree", p)
		}
		// Idempotent: an absent path is success, on both.
		if err := h.real.RemoveTree(ctx, p); err != nil {
			t.Errorf("real RemoveTree(%s) again = %v, want nil (absent is success)", p, err)
		}
		if err := h.fake.RemoveTree(ctx, p); err != nil {
			t.Errorf("fake RemoveTree(%s) again = %v, want nil (absent is success)", p, err)
		}
	}
	// Absent below an absent parent is success too.
	ghost := filepath.Join(tr.BackupsDir, "ghost", rtBundle)
	if rerr, ferr := h.real.RemoveTree(ctx, ghost), h.fake.RemoveTree(ctx, ghost); rerr != nil || ferr != nil {
		t.Errorf("RemoveTree of an absent bundle: real = %v, fake = %v", rerr, ferr)
	}
	// The live tenant and the other account's data are still there.
	if h.gone(t, filepath.Join(h.R, "data/tenants/dev/config/tenant.env")) {
		t.Error("the live tenant's data directory is gone")
	}
	untouched(t, h.outside, rtBundle, "precious")
	if _, err := os.Lstat(filepath.Join(tr.ReposDir, "linkw")); err != nil {
		t.Errorf("a refused symlink was removed: %v", err)
	}
}

// A driver with no deletion roots configured deletes nothing, on both.
func TestFakeAndRealFilesRemoveTreeWithNoRootsRefuseEverything(t *testing.T) {
	ctx := context.Background()
	h := newRemoveTreeHost(t)
	real := &RealFiles{Roots: []string{h.R}}
	fake := NewFake(FakeOptions{Roots: []string{h.R}}).FakeFiles()
	p := filepath.Join(h.tree.DataDir, rtQuarantined)
	refusedWithSentinel(t, "real RemoveTree with no tree roots", real.RemoveTree(ctx, p))
	refusedWithSentinel(t, "fake RemoveTree with no tree roots", fake.RemoveTree(ctx, p))
	if _, err := os.Stat(p); err != nil {
		t.Fatalf("a refused RemoveTree deleted %s: %v", p, err)
	}
}
