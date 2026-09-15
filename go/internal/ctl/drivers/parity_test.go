package drivers

import (
	"context"
	"errors"
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
