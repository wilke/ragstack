package drivers

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
)

// refRE is the shape of a ref this driver will hand to git.
//
// git's own ref rules are broader; this is narrower on purpose. Every ref the
// ctl resolves comes from an operator's `--tag` or from a registry row, and a
// leading `-` would be read by git as an OPTION rather than a ref — which is
// how an argument becomes a flag even in a driver that never uses a shell.
// `--end-of-options` closes that door as well; both are cheap and the failure
// mode is not.
var refRE = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$`)

// shaRE is a full object name. Worktrees are checked out at a resolved sha,
// never at a ref: a tenant pinned to `main` would silently change code under
// itself the next time anything re-ran (MEMORY: "tenant code isolation").
var shaRE = regexp.MustCompile(`^[0-9a-f]{40}$`)

// gitTimeout bounds one git call. Nothing here fetches — the mirror is updated
// by an operator — so every one of these is a local object-database read or a
// worktree file operation.
const gitTimeout = 2 * time.Minute

// RealGit is the mirror-and-worktree surface of `fleet artifact prepare` and
// `tenant create`.
type RealGit struct {
	run *runner
	// Bin is the absolute git path from RealOptions.
	Bin string
	// Roots are the directories a worktree may be created in or removed from —
	// the same approved roots the files driver writes under. `git worktree
	// remove --force` deletes a directory tree, so it is bounded by exactly the
	// containment check every other destructive driver method uses.
	Roots []string
}

var _ jobs.Git = (*RealGit)(nil)

func (g *RealGit) git(ctx context.Context, args ...string) ([]byte, error) {
	stdout, _, err := g.run.Run(ctx, Spec{Program: g.Bin, Args: args, Timeout: gitTimeout})
	return stdout, err
}

// checkMirror refuses anything but a bare repository.
//
// `fleet artifact prepare` works against /rag/repos/ragstack.git, a mirror an
// operator clones once. Pointing it at a working checkout instead — which is
// what coconut has today, and what doctor reports as `worktree_outside_mirror`
// — would add worktrees to a repository somebody is editing, and a tenant's
// code would then depend on a developer's branch state.
func (g *RealGit) checkMirror(ctx context.Context, mirror string) error {
	if !filepath.IsAbs(mirror) {
		return fmt.Errorf("%w: the mirror %q must be an absolute path", jobs.ErrRefused, mirror)
	}
	if st, err := os.Stat(mirror); err != nil || !st.IsDir() {
		return fmt.Errorf("%w: the mirror %s is not a directory on this host", jobs.ErrRefused, mirror)
	}
	out, err := g.git(ctx, "-C", mirror, "rev-parse", "--is-bare-repository")
	if err != nil {
		return fmt.Errorf("%w: %s is not a git repository: %v", jobs.ErrRefused, mirror, err)
	}
	if strings.TrimSpace(string(out)) != "true" {
		return fmt.Errorf("%w: %s is not a BARE mirror; the ctl adds worktrees only to a mirror an operator cloned for it",
			jobs.ErrRefused, mirror)
	}
	return nil
}

// ResolveRef turns a tag, branch or sha into the 40-hex commit it names.
func (g *RealGit) ResolveRef(ctx context.Context, mirror, ref string) (string, error) {
	if !refRE.MatchString(ref) || strings.Contains(ref, "..") {
		return "", fmt.Errorf("%w: %q is not a ref name this driver resolves", jobs.ErrRefused, ref)
	}
	if err := g.checkMirror(ctx, mirror); err != nil {
		return "", err
	}
	// `^{commit}` peels an annotated tag to the commit it points at, so an
	// artifact id is always a commit sha and never a tag object's.
	out, err := g.git(ctx, "-C", mirror, "rev-parse", "--verify", "--end-of-options", ref+"^{commit}")
	if err != nil {
		return "", err
	}
	sha := strings.TrimSpace(string(out))
	if !shaRE.MatchString(sha) {
		return "", fmt.Errorf("%w: %s resolved %s to %q, which is not a commit sha", jobs.ErrRefused, mirror, ref, sha)
	}
	return sha, nil
}

// AddWorktree checks sha out into dest, detached.
func (g *RealGit) AddWorktree(ctx context.Context, mirror, sha, dest string) error {
	if !shaRE.MatchString(sha) {
		return fmt.Errorf("%w: %q is not a 40-hex commit; a worktree is only ever checked out at a resolved sha", jobs.ErrRefused, sha)
	}
	if err := g.checkDest(dest); err != nil {
		return err
	}
	if _, err := os.Lstat(dest); err == nil {
		return fmt.Errorf("%w: %s already exists; the ctl never checks out over an existing directory", jobs.ErrRefused, dest)
	} else if !os.IsNotExist(err) {
		return err
	}
	if err := g.checkMirror(ctx, mirror); err != nil {
		return err
	}
	// --detach: a worktree on a branch would move when the branch does, and
	// two tenants on the same branch cannot both hold its checkout anyway.
	_, err := g.git(ctx, "-C", mirror, "worktree", "add", "--detach", dest, sha)
	return err
}

// RemoveWorktree removes dest and prunes the mirror's administrative record.
//
// A dest that is already gone is not an error: this is the rollback half of
// AddWorktree, and "the directory is not there" is the state the rollback
// wanted. The prune still runs, because the mirror keeps a record of a
// worktree whose directory vanished and a later `worktree add` at the same
// path would be refused by it.
func (g *RealGit) RemoveWorktree(ctx context.Context, mirror, dest string) error {
	if err := g.checkDest(dest); err != nil {
		return err
	}
	if err := g.checkMirror(ctx, mirror); err != nil {
		return err
	}
	if _, err := os.Lstat(dest); err == nil {
		if _, err := g.git(ctx, "-C", mirror, "worktree", "remove", "--force", dest); err != nil {
			return err
		}
	} else if !os.IsNotExist(err) {
		return err
	}
	_, err := g.git(ctx, "-C", mirror, "worktree", "prune")
	return err
}

// Describe names the code in dir.
func (g *RealGit) Describe(ctx context.Context, dir string) (string, error) {
	if !filepath.IsAbs(dir) {
		return "", fmt.Errorf("%w: %q must be an absolute path", jobs.ErrRefused, dir)
	}
	out, err := g.git(ctx, "-C", dir, "describe", "--tags", "--always", "--dirty")
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(out)), nil
}

// checkDest bounds the two methods that create and delete directory trees.
func (g *RealGit) checkDest(dest string) error {
	if !filepath.IsAbs(dest) || filepath.Clean(dest) != dest {
		return fmt.Errorf("%w: the worktree path %q must be absolute and clean", jobs.ErrRefused, dest)
	}
	if !contained(dest, g.Roots) {
		return outsideRoots(dest, g.Roots)
	}
	return nil
}
