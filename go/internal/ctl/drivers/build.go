package drivers

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
)

// baseRE is the shape of a vite `--base`. It is the gateway's own route prefix
// for a tenant's UI, and it is checked because the built bundle hard-codes it
// into every asset URL: a base that did not match the route the gateway
// publishes produces a UI that loads a blank page and gives an operator no
// clue why.
var baseRE = regexp.MustCompile(`^/ragstack/[a-z][a-z0-9-]{0,31}/ui/$`)

// Build timeouts. `npm ci` on a cold cache pulls a thousand packages over the
// network; `vite build` is CPU-bound and local. Both are bounded because a job
// that hangs holds the fleet locks it took.
const (
	npmCITimeout = 15 * time.Minute
	uiTimeout    = 10 * time.Minute
)

// RealBuild is the node/vite half of preparing an artifact and creating a
// tenant.
type RealBuild struct {
	run *runner
	// Node and Npm are the absolute interpreter paths from RealOptions. They
	// are explicit because coconut's node is not where the default says it is
	// (plan "Host facts": node 26.7 under a user's ~/.local), and a driver
	// that searched PATH would pick whichever node the daemon's environment
	// happened to carry.
	Node string
	Npm  string
	// Roots bound the directory a build may write into.
	Roots []string
}

var _ jobs.Build = (*RealBuild)(nil)

// frontendDir is where both methods run: the UI lives in <worktree>/frontend.
func frontendDir(worktree string) (string, error) {
	if !filepath.IsAbs(worktree) || filepath.Clean(worktree) != worktree {
		return "", fmt.Errorf("%w: the worktree %q must be an absolute, clean path", jobs.ErrRefused, worktree)
	}
	dir := filepath.Join(worktree, "frontend")
	st, err := os.Stat(dir)
	if err != nil || !st.IsDir() {
		return "", fmt.Errorf("%w: %s has no frontend directory to build", jobs.ErrRefused, worktree)
	}
	return dir, nil
}

// NpmCI installs the frontend's locked dependencies into the artifact.
//
// It is the ONLY method in this package that reaches the network, and it is
// CLI-only: `fleet artifact prepare` runs it once per artifact, and the daemon
// never installs packages. `ci` rather than `install`, so the lockfile decides
// what is installed and the run cannot silently update a dependency; a missing
// package-lock.json is therefore a refusal rather than an unlocked install.
func (b *RealBuild) NpmCI(ctx context.Context, worktree, cacheDir string) error {
	dir, err := frontendDir(worktree)
	if err != nil {
		return err
	}
	if _, err := os.Stat(filepath.Join(dir, "package-lock.json")); err != nil {
		return fmt.Errorf("%w: %s has no package-lock.json; the ctl installs only what a lockfile pins", jobs.ErrRefused, dir)
	}
	if cacheDir != "" && !filepath.IsAbs(cacheDir) {
		return fmt.Errorf("%w: the npm cache %q must be an absolute path", jobs.ErrRefused, cacheDir)
	}
	env := b.nodeEnv()
	if cacheDir != "" {
		// An explicit cache directory, inside the deployment: npm's default
		// is ~/.npm, which on this host is a directory shared with whatever
		// else the account runs and is not part of any backup.
		env = append(env, "NPM_CONFIG_CACHE="+cacheDir)
	}
	// No audit, no funding message, errors only: the three make the output of
	// a successful install empty, so anything a job log shows is a problem.
	_, _, err = b.run.Run(ctx, Spec{
		Program:  b.Npm,
		Args:     []string{"ci", "--no-audit", "--no-fund", "--loglevel=error"},
		Dir:      dir,
		ExtraEnv: append(env, "NPM_CONFIG_UPDATE_NOTIFIER=false"),
		Timeout:  npmCITimeout,
	})
	return err
}

// UI builds the tenant's UI bundle out of an artifact that is already
// installed.
//
// It refuses when node_modules is absent rather than installing them: a
// `tenant create` that quietly pulled packages from the network would be
// running code nobody reviewed, on a host whose artifacts are prepared in
// advance precisely so that a create is offline and repeatable.
func (b *RealBuild) UI(ctx context.Context, worktree, base, outDir string) error {
	dir, err := frontendDir(worktree)
	if err != nil {
		return err
	}
	vite := filepath.Join(dir, "node_modules", ".bin", "vite")
	if _, err := os.Stat(vite); err != nil {
		return fmt.Errorf("%w: %s is absent; prepare the artifact (fleet artifact prepare) before building a tenant UI from it",
			jobs.ErrRefused, vite)
	}
	if !baseRE.MatchString(base) {
		return fmt.Errorf("%w: %q is not a UI base path (/ragstack/<tenant>/ui/)", jobs.ErrRefused, base)
	}
	if !filepath.IsAbs(outDir) || filepath.Clean(outDir) != outDir {
		return fmt.Errorf("%w: the output directory %q must be absolute and clean", jobs.ErrRefused, outDir)
	}
	// --emptyOutDir DELETES the directory's contents before writing, so the
	// check is made on the RESOLVED path and vite is handed that path. A
	// lexical check passed `<root>/dist` when `dist` — or any component above
	// it — was a symlink, and the build then emptied whatever it pointed at.
	outDir, err = resolvedContainedNoLeafLink(outDir, b.Roots)
	if err != nil {
		return err
	}
	// node runs the vite entry point directly rather than through the shebang
	// of node_modules/.bin/vite: that shebang names whichever node is first on
	// PATH, and this package does not use PATH.
	_, _, err = b.run.Run(ctx, Spec{
		Program:  b.Node,
		Args:     []string{filepath.Join("node_modules", ".bin", "vite"), "build", "--base", base, "--outDir", outDir, "--emptyOutDir"},
		Dir:      dir,
		ExtraEnv: append(b.nodeEnv(), "CI=1"),
		Timeout:  uiTimeout,
	})
	if err != nil {
		return err
	}
	// vite can exit 0 having written nothing an operator would call a UI (an
	// empty entry, a misconfigured build). The index.html is the file the
	// gateway serves, so its absence is the failure, whatever the exit code
	// said.
	index := filepath.Join(outDir, "index.html")
	if _, err := os.Stat(index); err != nil {
		return fmt.Errorf("the build exited 0 but wrote no %s: %w", index, err)
	}
	return nil
}

// nodeEnv puts the configured node's own directory at the FRONT of PATH.
//
// npm and vite both spawn `node` by name; without this they would find a
// different node than the one the operator configured — or, on a host where
// PATH has none, no node at all. It is passed as ExtraEnv and so appears after
// the sanitized PATH: os/exec keeps the LAST value of a duplicated key, which
// is what makes this an override rather than a second entry nothing reads.
func (b *RealBuild) nodeEnv() []string {
	if b.Node == "" {
		return nil
	}
	return []string{"PATH=" + filepath.Dir(b.Node) + ":" + os.Getenv("PATH")}
}
