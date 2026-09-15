package drivers

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
)

// artifact lays down a worktree that looks like a prepared artifact: a
// frontend with a lockfile and, when installed, the vite binary the UI build
// refuses to run without.
func artifact(t *testing.T, root string, installed bool) string {
	t.Helper()
	worktree := filepath.Join(root, "artifacts", "main-abc", "worktree")
	frontend := filepath.Join(worktree, "frontend")
	if err := os.MkdirAll(frontend, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(frontend, "package-lock.json"), []byte("{}"), 0o644); err != nil {
		t.Fatal(err)
	}
	if installed {
		bin := filepath.Join(frontend, "node_modules", ".bin")
		if err := os.MkdirAll(bin, 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(bin, "vite"), []byte("#!/bin/sh\nexit 0\n"), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	return worktree
}

// keyBuild keys the node/npm stubs by their first argument.
const keyBuild = `key=$1`

func TestBuildNpmCIRunsALockedInstallWithTheConfiguredCache(t *testing.T) {
	root := t.TempDir()
	worktree := artifact(t, root, false)
	npm := newStub(t, keyBuild)
	// The stub prints its environment so the test can assert what the install
	// was given, and nothing else.
	if err := os.WriteFile(npm.Path, []byte("#!/bin/sh\nprintf '%s\\n' \"$*\" >> "+
		strconv.Quote(filepath.Join(filepath.Dir(npm.Path), "argv"))+"\nenv > "+
		strconv.Quote(filepath.Join(filepath.Dir(npm.Path), "env"))+"\npwd >> "+
		strconv.Quote(filepath.Join(filepath.Dir(npm.Path), "pwd"))+"\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	cache := filepath.Join(root, "cache", "npm")
	b := &RealBuild{run: &runner{}, Npm: npm.Path, Node: "/opt/node/bin/node", Roots: []string{root}}

	if err := b.NpmCI(context.Background(), worktree, cache); err != nil {
		t.Fatalf("NpmCI = %v", err)
	}
	if got := npm.argv(); len(got) != 1 || got[0] != "ci --no-audit --no-fund --loglevel=error" {
		t.Errorf("NpmCI ran %v", got)
	}
	env, err := os.ReadFile(filepath.Join(filepath.Dir(npm.Path), "env"))
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"NPM_CONFIG_CACHE=" + cache, "NPM_CONFIG_UPDATE_NOTIFIER=false", "PATH=/opt/node/bin:"} {
		if !strings.Contains(string(env), want) {
			t.Errorf("the install environment does not contain %q:\n%s", want, env)
		}
	}
	// It runs in <worktree>/frontend, which is where the lockfile is.
	pwd, err := os.ReadFile(filepath.Join(filepath.Dir(npm.Path), "pwd"))
	if err != nil {
		t.Fatal(err)
	}
	real, _ := filepath.EvalSymlinks(filepath.Join(worktree, "frontend"))
	if strings.TrimSpace(string(pwd)) != real {
		t.Errorf("NpmCI ran in %q, want %q", strings.TrimSpace(string(pwd)), real)
	}
}

func TestBuildNpmCIRefusesAnUnlockedOrAbsentFrontend(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	npm := newStub(t, keyBuild)
	b := &RealBuild{run: &runner{}, Npm: npm.Path, Roots: []string{root}}

	// No frontend at all.
	if err := b.NpmCI(ctx, filepath.Join(root, "empty"), root); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("NpmCI without a frontend = %v, want a refusal", err)
	}
	// A frontend with no lockfile: `npm ci` is how the ctl installs exactly
	// what a lockfile pins, so an unlocked install is refused rather than
	// silently turned into `npm install`.
	worktree := filepath.Join(root, "unlocked")
	if err := os.MkdirAll(filepath.Join(worktree, "frontend"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := b.NpmCI(ctx, worktree, root); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("NpmCI without a lockfile = %v, want a refusal", err)
	}
	// A relative worktree, and a relative cache.
	if err := b.NpmCI(ctx, "artifacts/wt", root); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("NpmCI with a relative worktree = %v, want a refusal", err)
	}
	if err := b.NpmCI(ctx, artifact(t, root, false), "cache/npm"); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("NpmCI with a relative cache = %v, want a refusal", err)
	}
	npm.ranNothing("a refused install")
}

// viteStub is a fake `node` that behaves like `vite build`: it finds --outDir
// in its argv and writes the index.html a real build would produce.
func viteStub(t *testing.T, writeIndex bool) *stub {
	t.Helper()
	s := newStub(t, keyBuild)
	body := "#!/bin/sh\nprintf '%s\\n' \"$*\" >> " + strconv.Quote(filepath.Join(filepath.Dir(s.Path), "argv")) + "\n" +
		"env > " + strconv.Quote(filepath.Join(filepath.Dir(s.Path), "env")) + "\n"
	if writeIndex {
		body += "out=\nprev=\nfor a in \"$@\"; do [ \"$prev\" = --outDir ] && out=$a; prev=$a; done\n" +
			"[ -n \"$out\" ] && mkdir -p \"$out\" && echo '<!doctype html>' > \"$out/index.html\"\n"
	}
	body += "exit 0\n"
	if err := os.WriteFile(s.Path, []byte(body), 0o755); err != nil {
		t.Fatal(err)
	}
	return s
}

func TestBuildUIBuildsIntoTheApprovedOutputDirectory(t *testing.T) {
	root := t.TempDir()
	worktree := artifact(t, root, true)
	node := viteStub(t, true)
	b := &RealBuild{run: &runner{}, Node: node.Path, Roots: []string{root}}
	out := filepath.Join(root, "tenants", "dev", "ui", "dist")

	if err := b.UI(context.Background(), worktree, "/ragstack/dev/ui/", out); err != nil {
		t.Fatalf("UI = %v", err)
	}
	want := "node_modules/.bin/vite build --base /ragstack/dev/ui/ --outDir " + out + " --emptyOutDir"
	if got := node.argv(); len(got) != 1 || got[0] != want {
		t.Errorf("UI ran %v, want %q", got, want)
	}
	if _, err := os.Stat(filepath.Join(out, "index.html")); err != nil {
		t.Errorf("the build produced no index.html: %v", err)
	}
	env, _ := os.ReadFile(filepath.Join(filepath.Dir(node.Path), "env"))
	if !strings.Contains(string(env), "CI=1") {
		t.Errorf("the build environment has no CI=1:\n%s", env)
	}
}

func TestBuildUIFailsWhenTheBuildWroteNoIndex(t *testing.T) {
	root := t.TempDir()
	worktree := artifact(t, root, true)
	// vite exited 0 and wrote nothing. The index.html is the file the gateway
	// serves, so its absence is the failure whatever the exit code said.
	node := viteStub(t, false)
	b := &RealBuild{run: &runner{}, Node: node.Path, Roots: []string{root}}
	err := b.UI(context.Background(), worktree, "/ragstack/dev/ui/", filepath.Join(root, "ui", "dist"))
	if err == nil {
		t.Fatal("a build that wrote no index.html was reported as success")
	}
	if !strings.Contains(err.Error(), "index.html") {
		t.Errorf("the error does not name the missing file: %v", err)
	}
}

func TestBuildUIRefusesWhatItWillNotBuild(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	node := viteStub(t, true)
	b := &RealBuild{run: &runner{}, Node: node.Path, Roots: []string{root}}
	installed := artifact(t, root, true)
	out := filepath.Join(root, "ui", "dist")

	// node_modules absent: the ctl builds from a PREPARED artifact and never
	// installs packages on the way to creating a tenant.
	bare := filepath.Join(root, "bare")
	if err := os.MkdirAll(filepath.Join(bare, "frontend"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := b.UI(ctx, bare, "/ragstack/dev/ui/", out); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("UI without node_modules = %v, want a refusal", err)
	}

	for _, base := range []string{
		"/ragstack/dev/ui", "ragstack/dev/ui/", "/", "/ragstack//ui/",
		"/ragstack/Dev/ui/", "/ragstack/dev/ui/../../../", "",
	} {
		if err := b.UI(ctx, installed, base, out); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("UI with base %q = %v, want a refusal", base, err)
		}
	}

	// --emptyOutDir DELETES the directory's contents before writing, so an
	// unchecked outDir would be a delete of any path the caller named.
	for _, bad := range []string{filepath.Join(t.TempDir(), "dist"), "ui/dist", root + "/../dist"} {
		if err := b.UI(ctx, installed, "/ragstack/dev/ui/", bad); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("UI with outDir %q = %v, want a refusal", bad, err)
		}
	}

	// A driver with no approved roots refuses everything rather than failing
	// open, exactly as the files driver does.
	none := &RealBuild{run: &runner{}, Node: node.Path}
	if err := none.UI(ctx, installed, "/ragstack/dev/ui/", out); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("UI with no approved roots = %v, want a refusal", err)
	}
	node.ranNothing("a refused build")
}
