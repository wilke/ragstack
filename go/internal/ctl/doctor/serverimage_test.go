package doctor

import (
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

const testServerImage = "ragstack-server-v1.6.6-b1.sif"

// imageWorld is newWorld with the tenant in IMAGE mode: an image file in the
// ctl's store, a fleet record for it, and the row pointing at it. hashes
// counts the hash seam's calls; the cache is the world's own.
func imageWorld(t *testing.T) (*world, *int) {
	t.Helper()
	t.Setenv(envAPIBindRoots, "")
	w := newWorld(t)
	dir := w.roots.ServerImagesDir()
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(dir, testServerImage)
	write(t, path, "the server image bytes")
	sum := sha256.Sum256([]byte("the server image bytes"))
	sha := hex.EncodeToString(sum[:])
	commit := strings.Repeat("ab", 20)
	w.tenant.ServerImage = &registry.ServerImage{Name: testServerImage, Version: "v1.6.6", Commit: commit,
		Build: 1, SHA256: sha, Path: path}
	w.tenant.Code = registry.Code{Tag: "v1.6.6", SHA: registry.NullString(commit)}
	w.fleet.ServerImages = map[string]*registry.ServerImageRecord{testServerImage: {
		Version: "v1.6.6", Commit: commit, Build: 1, SHA256: sha, Path: path,
		PreparedAt: "2026-10-09T00:00:00Z", PreparedBy: "local:test"}}
	n := 0
	w.opts.ImageHashes = NewImageHashCache()
	w.opts.HashImage = func(p string) (string, error) {
		n++
		return hashFile(p)
	}
	return w, &n
}

func imageCodes(resp *model.DoctorResponse) map[string]model.Level {
	out := map[string]model.Level{}
	for _, f := range resp.Findings {
		switch f.Code {
		case ServerImageMissing, ServerImageMismatch, ServerImageUnregistered, ImageDirOutsideBindRoots:
			out[f.Code] = f.Level
		}
	}
	return out
}

// A healthy image row raises nothing, and only the two ops that are about to
// run the image hash it; every other run reads the cache.
func TestServerImageIsHashedOnlyForStartAndUpdateCode(t *testing.T) {
	w, hashes := imageWorld(t)
	if got := imageCodes(w.run(t)); len(got) != 0 {
		t.Fatalf("a healthy image row raised %v", got)
	}
	if *hashes != 0 {
		t.Fatalf("an unscoped run hashed the image %d times; it must only read the cache", *hashes)
	}
	for _, op := range []string{"backup", "stop", "update-code", "start"} {
		before := *hashes
		w.opts.Op = op
		if got := imageCodes(w.run(t)); len(got) != 0 {
			t.Errorf("--op %s raised %v", op, got)
		}
		want := before
		if op == "start" || op == "update-code" {
			want++
		}
		if *hashes != want {
			t.Errorf("--op %s: %d hashes, want %d", op, *hashes, want)
		}
	}
}

func TestServerImageMismatchIsRedAndCached(t *testing.T) {
	w, hashes := imageWorld(t)
	w.tenant.ServerImage.SHA256 = strings.Repeat("0", 64) // the file is not what the row records
	w.fleet.ServerImages[testServerImage].SHA256 = strings.Repeat("0", 64)

	// No hash cached yet: an unscoped run does not hash, so it cannot know.
	if got := imageCodes(w.run(t)); len(got) != 0 || *hashes != 0 {
		t.Fatalf("unscoped, cold cache: %v after %d hashes; want nothing and no hash", got, *hashes)
	}
	// The gate hashes and refuses.
	w.opts.Op = "update-code"
	resp := w.run(t)
	if lvl := imageCodes(resp)[ServerImageMismatch]; lvl != model.LevelError || resp.Status != model.StatusRed {
		t.Fatalf("--op update-code over a swapped image: %v, status %s; want server_image_mismatch error, red",
			imageCodes(resp), resp.Status)
	}
	// Now cached: an unscoped run reports it without hashing again.
	w.opts.Op = ""
	n := *hashes
	if lvl := imageCodes(w.run(t))[ServerImageMismatch]; lvl != model.LevelError || *hashes != n {
		t.Errorf("unscoped, warm cache: mismatch=%s after %d new hashes; want error from the cache", lvl, *hashes-n)
	}
	// A rewritten file (different size) misses the cache.
	write(t, w.tenant.ServerImage.Path, "a different, longer set of bytes")
	if got := imageCodes(w.run(t)); len(got) != 0 {
		t.Errorf("a rewritten file was judged from a stale cache entry: %v", got)
	}
}

func TestServerImageMissingIsRedAndGatesStart(t *testing.T) {
	w, _ := imageWorld(t)
	if err := os.Remove(w.tenant.ServerImage.Path); err != nil {
		t.Fatal(err)
	}
	for _, op := range []string{"", "start", "update-code"} {
		w.opts.Op = op
		resp := w.run(t)
		if lvl := imageCodes(resp)[ServerImageMissing]; lvl != model.LevelError {
			t.Errorf("--op %q: server_image_missing = %q, want error", op, lvl)
		}
		if resp.Status != model.StatusRed {
			t.Errorf("--op %q: status %s, want red", op, resp.Status)
		}
	}
	// A directory where the file should be is not an image either.
	if err := os.MkdirAll(w.tenant.ServerImage.Path, 0o755); err != nil {
		t.Fatal(err)
	}
	w.opts.Op = ""
	if lvl := imageCodes(w.run(t))[ServerImageMissing]; lvl != model.LevelError {
		t.Errorf("a directory: server_image_missing = %q", lvl)
	}
}

func TestServerImageUnregisteredIsAWarning(t *testing.T) {
	w, _ := imageWorld(t)
	delete(w.fleet.ServerImages, testServerImage)
	resp := w.run(t)
	if lvl := imageCodes(resp)[ServerImageUnregistered]; lvl != model.LevelWarn {
		t.Fatalf("server_image_unregistered = %q, want warn", lvl)
	}
	// A record under the name with OTHER bytes is the same finding.
	w2, _ := imageWorld(t)
	w2.fleet.ServerImages[testServerImage].SHA256 = strings.Repeat("1", 64)
	if f := byCode(w2.run(t))[ServerImageUnregistered]; f.Level != model.LevelWarn || !strings.Contains(f.Detail, "prepared that name") {
		t.Errorf("a record with another sha: %+v", f)
	}
}

func TestImageDirOutsideBindRoots(t *testing.T) {
	w, _ := imageWorld(t)
	w.tenant.Settings["GOWE_IMAGE_DIRS"] = "/scout/containers/ragstack-dev, /scout/containers/ragstack,/home/wilke/images,relative"
	found := findingsForCode(w.run(t), ImageDirOutsideBindRoots)
	var got []string
	for _, f := range found {
		if f.Level != model.LevelWarn {
			t.Errorf("level %s, want warn", f.Level)
		}
		got = append(got, f.Detail)
	}
	if len(got) != 2 || !strings.Contains(got[0]+got[1], `"/home/wilke/images"`) || !strings.Contains(got[0]+got[1], `"relative"`) {
		t.Errorf("findings = %v; want exactly the /home and the relative entry under the default roots", got)
	}

	// The daemon's configuration narrows the roots (the mode suffix is
	// ignored): now the /scout entries are outside too.
	w.opts.APIBindRoots = []string{"/rag/cache:rw", "/rag/config:ro"}
	if n := len(findingsForCode(w.run(t), ImageDirOutsideBindRoots)); n != 4 {
		t.Errorf("with narrowed roots: %d findings, want 4", n)
	}
	// And CTL_API_BIND_ROOTS is read when the option is nil.
	w.opts.APIBindRoots = nil
	t.Setenv(envAPIBindRoots, "/scout/containers:ro,/home/wilke")
	if n := len(findingsForCode(w.run(t), ImageDirOutsideBindRoots)); n != 1 {
		t.Errorf("with CTL_API_BIND_ROOTS: %d findings, want 1 (the relative entry)", n)
	}

	// A WORKTREE row is not checked: it binds nothing.
	w.tenant.ServerImage = nil
	if n := len(findingsForCode(w.run(t), ImageDirOutsideBindRoots)); n != 0 {
		t.Errorf("a worktree row raised %d image-dir findings", n)
	}
}

// python_env is recorded and unused in image mode: where `import ragstack`
// resolves under it, and whether it is under /home, say nothing about the code
// that runs.
func TestImageRowsSkipThePythonEnvChecks(t *testing.T) {
	w, _ := imageWorld(t)
	w.tenant.PythonEnv = "/home/wilke/miniconda3/envs/ragstack"
	w.opts.ImportCheck = func(_, _ string) (string, error) { return "/home/wilke/elsewhere/ragstack/__init__.py", nil }
	resp := w.run(t)
	if f := findingsForCode(resp, ImportRagstackOutsideWorktree); len(f) != 0 {
		t.Errorf("an image row raised %v", f)
	}
	for _, f := range findingsForCode(resp, HomePathInProduction) {
		if strings.Contains(f.Detail, "python_env") {
			t.Errorf("an image row's python_env was checked: %s", f.Detail)
		}
	}
	// The same row in worktree mode raises both.
	w.tenant.ServerImage = nil
	resp = w.run(t)
	if len(findingsForCode(resp, ImportRagstackOutsideWorktree)) != 1 {
		t.Error("a worktree row lost import_ragstack_outside_worktree")
	}
	python := false
	for _, f := range findingsForCode(resp, HomePathInProduction) {
		python = python || strings.Contains(f.Detail, "python_env")
	}
	if !python {
		t.Error("a worktree row lost the python_env home-path check")
	}
}

func TestUpdateCodeAndStartGateOnTheServerImage(t *testing.T) {
	want := []string{EnvNotSystemdParsable, PortNotListening, WorktreeOutsideMirror, WorktreeGitdirUnreadable,
		ServerImageMissing, ServerImageMismatch}
	got := RedCodes("update-code")
	sort.Strings(want)
	sort.Strings(got)
	if !reflect.DeepEqual(got, want) {
		t.Errorf("RedCodes(update-code) = %v, want %v", got, want)
	}
	start := map[string]bool{}
	for _, c := range RedCodes("start") {
		start[c] = true
	}
	for _, c := range []string{ServerImageMissing, ServerImageMismatch, PortOwnerMismatch, ESSnapshotsDirMissing} {
		if !start[c] {
			t.Errorf("RedCodes(start) lacks %s", c)
		}
	}
	if !KnownOp("image-prepare") {
		t.Error("image-prepare has no precondition row")
	}
}
