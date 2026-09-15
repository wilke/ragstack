package main

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// runGit runs git in dir, failing the test on a non-zero exit. Author/
// committer identity is pinned so the fixture does not depend on the test
// runner's ~/.gitconfig existing at all (CI has none).
func runGit(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(),
		"GIT_AUTHOR_NAME=ctl-test", "GIT_AUTHOR_EMAIL=ctl-test@example.invalid",
		"GIT_COMMITTER_NAME=ctl-test", "GIT_COMMITTER_EMAIL=ctl-test@example.invalid")
	var out, errb bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &errb
	if err := cmd.Run(); err != nil {
		t.Fatalf("git %v (dir %s): %v\n%s", args, dir, err, errb.String())
	}
	return strings.TrimSpace(out.String())
}

// rebaseFixture is a bare mirror plus a "home" checkout it was cloned from,
// standing in for coconut's `~/Development/ragstack` and the mirror it was
// pushed into (plan "Host facts": "the mirror has every commit they are at").
type rebaseFixture struct {
	ragRoot, mirror, home string
	sha                   string
}

func newRebaseFixture(t *testing.T) rebaseFixture {
	t.Helper()
	tmp := t.TempDir()
	fx := rebaseFixture{
		ragRoot: tmp,
		home:    filepath.Join(tmp, "home", "ragstack"),
		mirror:  filepath.Join(tmp, "repos", "ragstack.git"),
	}
	if err := os.MkdirAll(fx.home, 0o755); err != nil {
		t.Fatal(err)
	}
	runGit(t, fx.home, "init", "-q", "-b", "main")
	if err := os.WriteFile(filepath.Join(fx.home, "f.txt"), []byte("hi\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	runGit(t, fx.home, "add", "f.txt")
	runGit(t, fx.home, "commit", "-q", "-m", "one")
	fx.sha = runGit(t, fx.home, "rev-parse", "HEAD")

	if err := os.MkdirAll(filepath.Dir(fx.mirror), 0o755); err != nil {
		t.Fatal(err)
	}
	runGit(t, fx.ragRoot, "clone", "-q", "--bare", fx.home, fx.mirror)
	return fx
}

// worktreeAt checks a detached worktree for the fixture's commit out at
// path, via git (not through the ctl — this is the "before" state the
// command operates on), and returns path.
func (fx rebaseFixture) worktreeAt(t *testing.T, repo, path string) string {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	runGit(t, repo, "worktree", "add", "-q", "--detach", path, fx.sha)
	return path
}

// registryWith saves f under fx.ragRoot and returns the registry path.
func (fx rebaseFixture) registryWith(t *testing.T, f *registry.Fleet) string {
	t.Helper()
	reg := filepath.Join(fx.ragRoot, "registry.json")
	if err := registry.Save(reg, f, "test"); err != nil {
		t.Fatal(err)
	}
	return reg
}

func TestRebaseWorktreeUnknownTenant(t *testing.T) {
	fx := newRebaseFixture(t)
	reg := fx.registryWith(t, registry.LiveFixture())
	rc, _, errs := capture(t, "tenant", "rebase-worktree", "nope", "--registry", reg, "--rag-root", fx.ragRoot)
	if rc != exitError || !strings.Contains(errs, "is not in the registry") {
		t.Fatalf("rc %d, stderr %s", rc, errs)
	}
}

func TestRebaseWorktreeRefusesDevUIWithoutFlag(t *testing.T) {
	fx := newRebaseFixture(t)
	f := registry.LiveFixture()
	// LiveFixture's tenants are all ui.mode=external; force dev to exercise
	// the refusal without needing a real worktree on disk at all — the
	// refusal fires before anything is read from the filesystem.
	f.Tenants["dev"].UI.Mode = registry.UIModeDev
	f.Tenants["dev"].UI.Port = registry.NullPort(8090)
	reg := fx.registryWith(t, f)

	rc, _, errs := capture(t, "tenant", "rebase-worktree", "dev", "--registry", reg, "--rag-root", fx.ragRoot)
	if rc != exitRefused || !strings.Contains(errs, "--include-dev-ui") {
		t.Fatalf("rc %d, stderr %s", rc, errs)
	}
}

func TestRebaseWorktreeAlreadyUnderMirror(t *testing.T) {
	fx := newRebaseFixture(t)
	// A worktree checked out of the MIRROR ITSELF (not the home repo) is the
	// post-rebase state; rebase-worktree must refuse it as a no-op rather
	// than shuffle directories around for nothing.
	wt := fx.worktreeAt(t, fx.mirror, filepath.Join(fx.ragRoot, "repos", "tenants", "dev"))
	f := registry.LiveFixture()
	f.Tenants["dev"].Worktree = wt
	reg := fx.registryWith(t, f)

	rc, _, errs := capture(t, "tenant", "rebase-worktree", "dev", "--registry", reg, "--rag-root", fx.ragRoot)
	if rc != exitRefused || !strings.Contains(errs, "already under the mirror") {
		t.Fatalf("rc %d, stderr %s", rc, errs)
	}
	if matches, _ := filepath.Glob(wt + ".*"); len(matches) != 0 {
		t.Errorf("a no-op refusal must not create any sibling directories: %v", matches)
	}
}

func TestRebaseWorktreeDryRunTouchesNothing(t *testing.T) {
	fx := newRebaseFixture(t)
	wt := fx.worktreeAt(t, fx.home, filepath.Join(fx.ragRoot, "repos", "tenants", "dev"))
	f := registry.LiveFixture()
	f.Tenants["dev"].Worktree = wt
	reg := fx.registryWith(t, f)

	rc, out, errs := capture(t, "tenant", "rebase-worktree", "dev", "--registry", reg, "--rag-root", fx.ragRoot, "--dry-run")
	if rc != exitOK || !strings.Contains(out, fx.sha) || !strings.Contains(out, "worktree add") {
		t.Fatalf("rc %d\nstdout=%s\nstderr=%s", rc, out, errs)
	}
	if matches, _ := filepath.Glob(wt + ".*"); len(matches) != 0 {
		t.Errorf("--dry-run must not touch the filesystem: %v", matches)
	}
	gitFile, err := os.ReadFile(filepath.Join(wt, ".git"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(gitFile), fx.mirror) {
		t.Errorf("--dry-run must not have rebased the worktree: %s", gitFile)
	}
}

func TestRebaseWorktreeSuccess(t *testing.T) {
	fx := newRebaseFixture(t)
	wt := fx.worktreeAt(t, fx.home, filepath.Join(fx.ragRoot, "repos", "tenants", "dev"))
	f := registry.LiveFixture()
	f.Tenants["dev"].Worktree = wt
	reg := fx.registryWith(t, f)

	rc, out, errs := capture(t, "tenant", "rebase-worktree", "dev", "--registry", reg, "--rag-root", fx.ragRoot)
	if rc != exitOK {
		t.Fatalf("rc %d\nstdout=%s\nstderr=%s", rc, out, errs)
	}
	if !strings.Contains(out, fx.sha) {
		t.Errorf("expected the sha %s in the output: %s", fx.sha, out)
	}
	// The worktree's OWN path is unchanged; only its gitdir now names the
	// mirror.
	gitFile, err := os.ReadFile(filepath.Join(wt, ".git"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(gitFile), fx.mirror) {
		t.Errorf(".git pointer does not name the mirror %s: %s", fx.mirror, gitFile)
	}
	got := runGit(t, wt, "rev-parse", "HEAD")
	if got != fx.sha {
		t.Errorf("rebased worktree HEAD = %s, want %s", got, fx.sha)
	}
	// The old checkout is preserved next to it, not deleted, and the printed
	// hint names the ORIGINAL repo (fx.home), not the mirror or the tenant
	// path.
	matches, _ := filepath.Glob(wt + ".home-*")
	if len(matches) != 1 {
		t.Fatalf("expected exactly one preserved .home-* dir next to %s, got %v", wt, matches)
	}
	if _, err := os.Stat(filepath.Join(matches[0], "f.txt")); err != nil {
		t.Errorf("the preserved old checkout is missing its file: %v", err)
	}
	if !strings.Contains(out, "git -C "+fx.home+" worktree remove --force "+matches[0]) {
		t.Errorf("expected the worktree-remove hint naming %s and %s in: %s", fx.home, matches[0], out)
	}
	if mirrorSide := wt + ".mirror"; fileExists(mirrorSide) {
		t.Errorf("the intermediate %s must not survive a successful rebase", mirrorSide)
	}
	// The mirror's own record names the FINAL path: a rename alone left it
	// naming <wt>.mirror, and `git worktree list` called the checkout prunable.
	list := runGit(t, fx.mirror, "worktree", "list", "--porcelain")
	if !strings.Contains(list, "worktree "+wt+"\n") || strings.Contains(list, ".mirror") || strings.Contains(list, "prunable") {
		t.Errorf("the mirror's worktree list was not repaired after the rename:\n%s", list)
	}
}

func fileExists(p string) bool {
	_, err := os.Lstat(p)
	return err == nil
}
