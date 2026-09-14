package drivers

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
)

// keyGit separates the two `rev-parse` calls (the bare-repository probe and
// the ref resolution) and the three `worktree` subcommands, which the generic
// first-word key cannot tell apart.
const keyGit = `
case "$*" in
  *--is-bare-repository*) key=isbare ;;
  *rev-parse*)            key=revparse ;;
  *"worktree add"*)       key=wtadd ;;
  *"worktree remove"*)    key=wtremove ;;
  *"worktree prune"*)     key=wtprune ;;
  *describe*)             key=describe ;;
  *)                      key=other ;;
esac
`

const testSHA = "0123456789abcdef0123456789abcdef01234567"

// newGit is a RealGit pointed at a stub `git` that reports a bare mirror.
func newGit(t *testing.T) (*RealGit, *stub, string) {
	t.Helper()
	s := newStub(t, keyGit)
	s.respond("isbare", "true\n", "", 0)
	root := t.TempDir()
	return &RealGit{run: &runner{}, Bin: s.Path, Roots: []string{root}}, s, root
}

func TestGitResolveRefRefusesARefItWillNotHandToGit(t *testing.T) {
	ctx := context.Background()
	for _, ref := range []string{
		"--upload-pack=touch /tmp/x", "-v", "", "main..other", "../etc",
		"tag with space", "ref\nname", "ref;rm -rf /", "réf",
		strings.Repeat("a", 129),
	} {
		d, stub, _ := newGit(t)
		if _, err := d.ResolveRef(ctx, "/rag/repos/ragstack.git", ref); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("ResolveRef(%q) = %v, want a refusal", ref, err)
		}
		stub.ranNothing("a ref outside the allowlist")
	}
}

func TestGitResolveRefPeelsToACommit(t *testing.T) {
	d, stub, _ := newGit(t)
	stub.respond("revparse", testSHA+"\n", "", 0)
	sha, err := d.ResolveRef(context.Background(), t.TempDir(), "v0.20.1")
	if err != nil {
		t.Fatalf("ResolveRef = %v", err)
	}
	if sha != testSHA {
		t.Errorf("sha = %q", sha)
	}
	last := stub.argv()[len(stub.argv())-1]
	for _, want := range []string{"rev-parse", "--verify", "--end-of-options", "v0.20.1^{commit}"} {
		if !strings.Contains(last, want) {
			t.Errorf("the resolve argv %q does not contain %q", last, want)
		}
	}
}

func TestGitRefusesAMirrorThatIsNotBare(t *testing.T) {
	ctx := context.Background()
	d, stub, root := newGit(t)
	// A working checkout — which is what coconut has today, and what doctor
	// reports as worktree_outside_mirror. Adding worktrees to a repository
	// somebody is editing would tie a tenant's code to a developer's branch.
	stub.respond("isbare", "false\n", "", 0)
	mirror := t.TempDir()
	if _, err := d.ResolveRef(ctx, mirror, "main"); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("ResolveRef against a non-bare repo = %v, want a refusal", err)
	}
	if err := d.AddWorktree(ctx, mirror, testSHA, filepath.Join(root, "wt")); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("AddWorktree against a non-bare repo = %v, want a refusal", err)
	}
	// A mirror that is not a directory at all is refused before git runs.
	d2, stub2, _ := newGit(t)
	if _, err := d2.ResolveRef(ctx, "/nonexistent/ragstack.git", "main"); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("ResolveRef against an absent mirror = %v, want a refusal", err)
	}
	stub2.ranNothing("an absent mirror")
}

func TestGitAddWorktreeRefusesWhatItWillNotCreate(t *testing.T) {
	ctx := context.Background()
	d, _, root := newGit(t)
	mirror := t.TempDir()
	existing := filepath.Join(root, "taken")
	if err := os.Mkdir(existing, 0o755); err != nil {
		t.Fatal(err)
	}
	for _, c := range []struct {
		name string
		sha  string
		dest string
	}{
		{"a ref instead of a sha", "main", filepath.Join(root, "wt")},
		{"a short sha", testSHA[:12], filepath.Join(root, "wt")},
		{"a destination outside the approved roots", testSHA, filepath.Join(t.TempDir(), "wt")},
		{"a relative destination", testSHA, "worktrees/wt"},
		{"a traversing destination", testSHA, root + "/../wt"},
		{"a destination that already exists", testSHA, existing},
	} {
		if err := d.AddWorktree(ctx, mirror, c.sha, c.dest); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("AddWorktree with %s = %v, want a refusal", c.name, err)
		}
	}
}

func TestGitAddAndRemoveWorktreeBuildTheExpectedArgv(t *testing.T) {
	ctx := context.Background()
	d, stub, root := newGit(t)
	mirror := t.TempDir()
	dest := filepath.Join(root, "artifacts", "wt")

	if err := d.AddWorktree(ctx, mirror, testSHA, dest); err != nil {
		t.Fatalf("AddWorktree = %v", err)
	}
	want := "-C " + mirror + " worktree add --detach " + dest + " " + testSHA
	if got := stub.argv(); got[len(got)-1] != want {
		t.Errorf("AddWorktree ran %q, want %q", got[len(got)-1], want)
	}

	// The destination does not exist, so `worktree remove` is skipped and only
	// the prune runs: this is the rollback half, and "already gone" is the
	// state it wanted. The prune still matters — the mirror keeps a record of
	// a worktree whose directory vanished, and it would refuse a later add at
	// the same path.
	if err := d.RemoveWorktree(ctx, mirror, dest); err != nil {
		t.Fatalf("RemoveWorktree of an absent dest = %v", err)
	}
	argv := stub.argv()
	if last := argv[len(argv)-1]; !strings.HasSuffix(last, "worktree prune") {
		t.Errorf("RemoveWorktree ran %q last, want a prune", last)
	}
	for _, call := range argv {
		if strings.Contains(call, "worktree remove") {
			t.Errorf("RemoveWorktree ran %q for a destination that does not exist", call)
		}
	}

	// A destination that does exist is removed and then pruned.
	if err := os.MkdirAll(dest, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := d.RemoveWorktree(ctx, mirror, dest); err != nil {
		t.Fatalf("RemoveWorktree = %v", err)
	}
	argv = stub.argv()
	if want := "-C " + mirror + " worktree remove --force " + dest; argv[len(argv)-2] != want {
		t.Errorf("RemoveWorktree ran %q, want %q", argv[len(argv)-2], want)
	}

	// And a destination outside the approved roots is refused: `worktree
	// remove --force` deletes a directory tree.
	if err := d.RemoveWorktree(ctx, mirror, filepath.Join(t.TempDir(), "elsewhere")); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("RemoveWorktree outside the roots = %v, want a refusal", err)
	}
}

func TestGitDescribe(t *testing.T) {
	d, stub, _ := newGit(t)
	stub.respond("describe", "v0.20.1-3-gdeadbee-dirty\n", "", 0)
	got, err := d.Describe(context.Background(), "/rag/repos/tenants/dev")
	if err != nil {
		t.Fatalf("Describe = %v", err)
	}
	if got != "v0.20.1-3-gdeadbee-dirty" {
		t.Errorf("Describe = %q", got)
	}
	if _, err := d.Describe(context.Background(), "repos/tenants/dev"); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("Describe of a relative path = %v, want a refusal", err)
	}
}

// TestGitAgainstRealGit is the one test that uses the host's own git: the
// stubs prove the argv and the refusals, and this proves the argv is one git
// actually accepts. It is also the only test in this file that is skipped
// when a tool is missing, and it says so.
func TestGitAgainstRealGit(t *testing.T) {
	gitBin, ok := execLooksAvailable("git")
	if !ok {
		t.Skip("git is not on PATH; skipping the ONE integration test (the stub-driven tests above cover the driver's own logic)")
	}
	ctx := context.Background()
	root := t.TempDir()
	source := filepath.Join(root, "source")
	run := func(dir string, args ...string) {
		t.Helper()
		cmd := exec.Command(gitBin, args...)
		cmd.Dir = dir
		cmd.Env = append(os.Environ(), "GIT_CONFIG_GLOBAL=/dev/null", "GIT_CONFIG_SYSTEM=/dev/null")
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("git %s: %v\n%s", strings.Join(args, " "), err, out)
		}
	}
	if err := os.MkdirAll(source, 0o755); err != nil {
		t.Fatal(err)
	}
	run(source, "init", "-q", "-b", "main", ".")
	if err := os.WriteFile(filepath.Join(source, "README.md"), []byte("hello\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	run(source, "add", "README.md")
	run(source, "-c", "user.name=ctl", "-c", "user.email=ctl@example.invalid", "commit", "-q", "-m", "first")
	mirror := filepath.Join(root, "ragstack.git")
	run(root, "clone", "--quiet", "--mirror", source, mirror)

	d := &RealGit{run: &runner{}, Bin: gitBin, Roots: []string{root}}

	// A working checkout is refused; the mirror is not.
	if _, err := d.ResolveRef(ctx, source, "main"); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("ResolveRef against the non-bare source = %v, want a refusal", err)
	}
	sha, err := d.ResolveRef(ctx, mirror, "main")
	if err != nil {
		t.Fatalf("ResolveRef = %v", err)
	}
	if !shaRE.MatchString(sha) {
		t.Fatalf("ResolveRef = %q, want a 40-hex sha", sha)
	}
	// A sha resolves to itself, which is what `--tag <sha>` relies on.
	if again, err := d.ResolveRef(ctx, mirror, sha); err != nil || again != sha {
		t.Errorf("ResolveRef(%s) = %q, %v", sha, again, err)
	}
	if _, err := d.ResolveRef(ctx, mirror, "no-such-tag"); err == nil {
		t.Error("ResolveRef of an unknown ref succeeded")
	}

	dest := filepath.Join(root, "artifacts", "wt")
	if err := d.AddWorktree(ctx, mirror, sha, dest); err != nil {
		t.Fatalf("AddWorktree = %v", err)
	}
	if b, err := os.ReadFile(filepath.Join(dest, "README.md")); err != nil || string(b) != "hello\n" {
		t.Fatalf("the worktree does not hold the commit's files: %v", err)
	}
	if desc, err := d.Describe(ctx, dest); err != nil || !strings.HasPrefix(sha, desc) {
		t.Errorf("Describe = %q, %v; want the short sha of %s", desc, err, sha)
	}
	// Adding the same destination twice is refused rather than left to git.
	if err := d.AddWorktree(ctx, mirror, sha, dest); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("a second AddWorktree = %v, want a refusal", err)
	}
	if err := d.RemoveWorktree(ctx, mirror, dest); err != nil {
		t.Fatalf("RemoveWorktree = %v", err)
	}
	if _, err := os.Stat(dest); !os.IsNotExist(err) {
		t.Errorf("the worktree survived the removal: %v", err)
	}
	// Idempotent: the rollback may run twice.
	if err := d.RemoveWorktree(ctx, mirror, dest); err != nil {
		t.Errorf("a second RemoveWorktree = %v, want success", err)
	}
	// And the mirror will take the path again, which is what the prune is for.
	if err := d.AddWorktree(ctx, mirror, sha, dest); err != nil {
		t.Errorf("AddWorktree after a removal = %v", err)
	}
}
