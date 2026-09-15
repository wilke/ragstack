package main

// `tenant rebase-worktree` — re-checks a tenant's worktree out of the bare
// mirror, in place of a worktree whose gitdir still points at a developer's
// home-directory checkout (plan "Production layout": nothing in production
// may reference a home directory).
//
// This is a LOCAL, DIRECT-ONLY action, never an op: it renames the worktree
// directory itself, which only the account that owns it (today, wilke) can
// do, and which the job engine has no business doing on the ctl account's
// behalf before PR-E's handover moves ownership to svcbvbrc. It has no
// daemon route, submits nothing through jobs.Engine, and never touches the
// registry — a rebase changes WHERE the code is checked out from, not WHAT
// code (t.Code.SHA) is running, so there is nothing in registry.json to
// update.
//
// It reuses two pieces of the existing driver seam rather than re-deriving
// them: hostfacts.Host.Gitdir (the same worktree-classification doctor's
// codeChecks and adopt use, so "is this gitdir already in the mirror" means
// the same thing here as everywhere else) and jobs.Git / jobs.Files off a
// bare drivers.NewReal — the same git binary resolution, the same worktree
// containment rules (git.go's checkDest, files.go's approved roots) `fleet
// artifact prepare` and `tenant create` already use, so a mirror this can
// check a worktree out of is a mirror those can too, and vice versa.

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/user"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/api"
	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/hostfacts"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

func rebaseWorktreeUsage() int {
	fmt.Fprintf(stderr, `usage: ragstack-ctl tenant rebase-worktree <name> [--mirror DIR] [--dry-run] [--include-dev-ui]

Re-checks <name>'s worktree out of the bare mirror, at the sha its CURRENT
worktree's HEAD already resolves to, and swaps it in — for a worktree whose
gitdir still points outside the mirror (typically a developer's home
directory: doctor's worktree_outside_mirror). No code changes: the sha is
read from the existing worktree, not chosen here.

  --mirror DIR       the bare mirror to check the worktree out of
                      (default $%s or <rag-root>/repos/ragstack.git)
  --dry-run          print the plan; touch nothing
  --include-dev-ui   proceed even though ui.mode is "dev" (see below)

What it does, in order:
  1. resolve <name>'s worktree's HEAD to a commit sha
  2. verify that sha exists in the mirror
  3. git worktree add --detach <worktree>.mirror <sha>   (in the mirror)
  4. rename <worktree>        -> <worktree>.home-<ts>
  5. rename <worktree>.mirror -> <worktree>

The OLD checkout is left on disk at <worktree>.home-<ts> — this command never
deletes a directory it did not itself just create. It prints, but never runs,
the command that removes it from its original repository's worktree list.

A dev-mode UI (ui.mode=dev) runs its Vite dev server FROM the worktree, and a
worktree fresh out of the mirror has no node_modules (only a prepared
artifact's build step installs one) — the dev server would fail on the next
restart. rebase-worktree refuses a dev-mode tenant unless --include-dev-ui is
given, so that refusal is a choice made on purpose, not a 3am surprise.

Safe to run against a tenant whose API is up: the running process keeps the
inode of the code it already loaded open underneath the rename, so it keeps
working — restart it at the next maintenance window to pick up the new path.
`, api.EnvMirror)
	return exitUsage
}

// cmdTenantRebaseWorktree is `ragstack-ctl tenant rebase-worktree <name>`.
func cmdTenantRebaseWorktree(args []string, registryPath, ragRoot string) int {
	fs := flag.NewFlagSet("tenant rebase-worktree", flag.ContinueOnError)
	fs.SetOutput(stderr)
	reg := fs.String("registry", registryPath, "registry.json path")
	root := fs.String("rag-root", ragRoot, "deployment root")
	mirror := fs.String("mirror", "", "the bare mirror to check the worktree out of")
	dryRun := fs.Bool("dry-run", false, "print the plan; touch nothing")
	includeDevUI := fs.Bool("include-dev-ui", false, "proceed even though ui.mode is dev (no node_modules after a rebase)")

	pos, rest := takePositionals(args, 1)
	if err := fs.Parse(rest); err != nil {
		return exitUsage
	}
	pos = append(pos, fs.Args()...)
	if len(pos) != 1 {
		return rebaseWorktreeUsage()
	}
	name := pos[0]

	f, err := loadForRead(resolveRegistry(*reg, *root))
	if err != nil {
		return fail(err)
	}
	t, ok := f.Tenants[name]
	if !ok {
		fmt.Fprintf(stderr, "ragstack-ctl: tenant %q is not in the registry\n", name)
		return exitError
	}

	// The frontend caveat first: it is the cheapest refusal, and the one an
	// operator is most likely to hit by habit (rebasing every tenant in a
	// loop) rather than by a considered choice.
	if t.UI.Mode == registry.UIModeDev && !*includeDevUI {
		fmt.Fprintf(stderr,
			"ragstack-ctl: %s runs its UI in dev mode (ui.mode=%s) from %s — a worktree checked out fresh from "+
				"the mirror has no node_modules, so its Vite dev server would fail at the next restart. "+
				"Pass --include-dev-ui once you have a plan to reinstall node_modules in the new worktree, "+
				"or leave this tenant's worktree where it is.\n",
			name, t.UI.Mode, t.Worktree)
		return exitRefused
	}

	mirrorDir := strings.TrimSpace(*mirror)
	if mirrorDir == "" {
		mirrorDir = mirrorPath(*root)
	}
	if !filepath.IsAbs(mirrorDir) {
		fmt.Fprintf(stderr, "ragstack-ctl: --mirror %q must be an absolute path\n", mirrorDir)
		return exitUsage
	}

	// Ownership: this command renames the worktree DIRECTORY, which the
	// filesystem lets only its owner (or root) do. Refusing up front with a
	// named account beats the bare EPERM os.Rename would return after the
	// mirror-side worktree has already been checked out.
	if err := refuseUnlessOwner(t.Worktree); err != nil {
		fmt.Fprintln(stderr, "ragstack-ctl: "+err.Error())
		return exitRefused
	}

	host := hostfacts.NewReal(api.RootsFromEnv(*root))
	g, gerr := host.Gitdir(t.Worktree)
	if gerr != nil {
		return fail(gerr)
	}
	if g.Location == hostfacts.GitdirUnreadable {
		fmt.Fprintf(stderr, "ragstack-ctl: %s has no readable gitdir (%s) — nothing to rebase\n",
			t.Worktree, filepath.Join(t.Worktree, ".git"))
		return exitError
	}
	if under(g.Path, mirrorDir) {
		fmt.Fprintf(stderr, "ragstack-ctl: %s's gitdir (%s) is already under the mirror %s — nothing to do\n",
			name, g.Path, mirrorDir)
		return exitRefused
	}

	mirrorSide := t.Worktree + ".mirror"
	ts := time.Now().UTC().Format("20060102T150405Z")
	homeSide := t.Worktree + ".home-" + ts
	for _, p := range []string{mirrorSide, homeSide} {
		if _, err := os.Lstat(p); err == nil {
			fmt.Fprintf(stderr, "ragstack-ctl: refusing: %s already exists\n", p)
			return exitRefused
		} else if !os.IsNotExist(err) {
			return fail(err)
		}
	}

	// The line the operator runs later, never here: `git worktree remove`
	// needs the ORIGINAL repository (the one whose .git/worktrees/ entry this
	// is), not the mirror and not the tenant path, so it is computed from the
	// gitdir's own commondir record.
	removeLine := oldWorktreeRemoveHint(g.Path, homeSide)

	real := drivers.NewReal(drivers.RealOptions{
		Roots:  api.RootsFromEnv(*root),
		GitBin: hostToolFromEnv(api.EnvGitBin),
	})
	ctx := context.Background()

	sha, err := real.Git().HeadSHA(ctx, t.Worktree)
	if err != nil {
		return fail(fmt.Errorf("resolving %s's HEAD: %w", t.Worktree, err))
	}
	if _, err := real.Git().ResolveRef(ctx, mirrorDir, sha); err != nil {
		return fail(fmt.Errorf("%s's HEAD %s is not in the mirror %s (push or fetch it in first): %w", name, sha, mirrorDir, err))
	}

	if *dryRun {
		fmt.Fprintf(stdout, "[dry-run] rebase-worktree %s (sha %s, mirror %s):\n", name, sha, mirrorDir)
		fmt.Fprintf(stdout, "  1. git -C %s worktree add --detach %s %s\n", mirrorDir, mirrorSide, sha)
		fmt.Fprintf(stdout, "  2. mv %s %s\n", t.Worktree, homeSide)
		fmt.Fprintf(stdout, "  3. mv %s %s\n", mirrorSide, t.Worktree)
		fmt.Fprintf(stdout, "then, once you no longer need the old checkout:\n  %s\n", removeLine)
		return exitOK
	}

	if err := real.Git().AddWorktree(ctx, mirrorDir, sha, mirrorSide); err != nil {
		return fail(fmt.Errorf("checking %s out of %s at %s: %w", mirrorSide, mirrorDir, sha, err))
	}
	if err := real.Files().Rename(ctx, t.Worktree, homeSide); err != nil {
		// Roll back the worktree we just added: nothing has moved yet, so
		// this is a clean abort, not a half-done rebase.
		_ = real.Git().RemoveWorktree(ctx, mirrorDir, mirrorSide)
		return fail(fmt.Errorf("moving %s aside as %s failed, rolled back: %w", t.Worktree, homeSide, err))
	}
	if err := real.Files().Rename(ctx, mirrorSide, t.Worktree); err != nil {
		// The bad spot: the home-side rename above already succeeded, so
		// t.Worktree is now MISSING rather than merely stale. Say exactly
		// what is on disk and exactly what puts it back, rather than
		// attempting a further rename this deep into a failure.
		return fail(fmt.Errorf(
			"CRITICAL: %s is now at %s and could not be renamed into place as %s (%v) — %s is currently MISSING; "+
				"recover by hand: mv %s %s",
			mirrorSide, mirrorSide, t.Worktree, err, t.Worktree, mirrorSide, t.Worktree))
	}

	fmt.Fprintf(stdout, "rebased %s: %s now checked out from %s at %s\n", name, t.Worktree, mirrorDir, sha)
	fmt.Fprintf(stdout, "the old checkout is untouched at %s; once you no longer need it:\n  %s\n", homeSide, removeLine)
	fmt.Fprintln(stdout, "the running API keeps working (same code, modules already loaded) — restart it at the next maintenance window to pick up the new path.")
	return exitOK
}

// under reports whether path is root or under it, both cleaned first. Local
// to this file because it is a plain string/prefix check against whatever
// --mirror named, not the hostfacts package's own (fixed-mirror) gitdir
// classification.
func under(path, root string) bool {
	path, root = filepath.Clean(path), filepath.Clean(root)
	return path == root || strings.HasPrefix(path, root+string(filepath.Separator))
}

// refuseUnlessOwner errors unless this process's effective user owns path.
//
// A rename is a filesystem operation with no privilege escalation of its
// own: the OS already enforces this, and os.Rename would fail on it anyway.
// Checking first turns that into a message naming the account to run this
// as, instead of a bare "permission denied" after the mirror-side worktree
// has already been checked out.
func refuseUnlessOwner(path string) error {
	fi, err := os.Lstat(path)
	if err != nil {
		return fmt.Errorf("%s: %w", path, err)
	}
	st, ok := fi.Sys().(*syscall.Stat_t)
	if !ok {
		return nil // not a platform this check applies to; let the rename itself decide
	}
	if int(st.Uid) == os.Geteuid() {
		return nil
	}
	owner := strconv.Itoa(int(st.Uid))
	if u, err := user.LookupId(owner); err == nil {
		owner = u.Username
	}
	return fmt.Errorf("%s is owned by %s; run this command as that account (a rename needs the directory's owner, not root or the ctl account)", path, owner)
}

// oldWorktreeRemoveHint is the `git worktree remove` line the operator runs,
// by hand, once the old checkout at homeSide is no longer needed.
//
// gitdirPath is <original-repo>/.git/worktrees/<name> for a linked worktree;
// its commondir file names <original-repo>/.git relative to itself, so the
// original repository is that file's target's parent. When commondir cannot
// be read (a plain, non-worktree checkout — not the case any of the five
// tenant worktrees are in today, but not this command's business to assume),
// the hint says so instead of guessing a path that might delete the wrong
// thing if run against it.
func oldWorktreeRemoveHint(gitdirPath, homeSide string) string {
	b, err := os.ReadFile(filepath.Join(gitdirPath, "commondir"))
	if err != nil {
		return fmt.Sprintf("(could not determine the old worktree's original repository from %s: %v; "+
			"find it yourself and run: git -C <that repo> worktree remove --force %s)", gitdirPath, err, homeSide)
	}
	commondir := strings.TrimSpace(string(b))
	if !filepath.IsAbs(commondir) {
		commondir = filepath.Join(gitdirPath, commondir)
	}
	oldRepo := filepath.Dir(filepath.Clean(commondir)) // .../repo/.git -> .../repo
	return fmt.Sprintf("git -C %s worktree remove --force %s", oldRepo, homeSide)
}

// hostToolFromEnv reads one of the CTL_* host-tool variables api.env.go
// defines, the same way api.SetHostToolsFromEnv does for the engine — so a
// --direct job and this local action resolve `git` identically without this
// command building (and having to keep in sync) a whole api.EngineConfig for
// a single field.
func hostToolFromEnv(name string) string {
	return strings.TrimSpace(os.Getenv(name))
}
