package drivers

import (
	"archive/tar"
	"bytes"
	"context"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
)

// Extraction is tested with tars CRAFTED for each attack, because the ones a
// tar command produces are exactly the ones that are safe: a traversing name,
// an absolute name, a symlink and a header that lies about its size have to be
// written by hand, and each of them is a real way a bundle could arrive.

// entry is one tar member to write.
type entry struct {
	name     string
	typeflag byte
	body     string
	// size overrides the header's Size, so a header can lie about its body.
	size     int64
	linkname string
}

// tarOf writes a tar of entries to a temp file and returns its path.
func tarOf(t *testing.T, entries ...entry) string {
	t.Helper()
	var buf bytes.Buffer
	tw := tar.NewWriter(&buf)
	for _, e := range entries {
		typ := e.typeflag
		if typ == 0 {
			typ = tar.TypeReg
		}
		size := int64(len(e.body))
		if e.size != 0 {
			size = e.size
		}
		h := &tar.Header{Name: e.name, Typeflag: typ, Mode: 0o644, Size: size, Linkname: e.linkname}
		if typ == tar.TypeDir {
			h.Size, h.Mode = 0, 0o755
		}
		if typ == tar.TypeSymlink || typ == tar.TypeLink {
			h.Size = 0
		}
		if err := tw.WriteHeader(h); err != nil {
			t.Fatal(err)
		}
		if typ == tar.TypeReg && e.body != "" {
			if _, err := tw.Write([]byte(e.body)); err != nil && !strings.Contains(err.Error(), "write too long") {
				t.Fatal(err)
			}
		}
	}
	// Close may complain about a body that did not match a lying header; that
	// is the point of those fixtures, so the error is not fatal here.
	_ = tw.Close()
	path := filepath.Join(t.TempDir(), "bundle.tar")
	if err := os.WriteFile(path, buf.Bytes(), 0o640); err != nil {
		t.Fatal(err)
	}
	return path
}

// extractFixture returns a driver and an existing destination under its root.
func extractFixture(t *testing.T) (jobs.Archive, string) {
	t.Helper()
	root := t.TempDir()
	dest := filepath.Join(root, "dest")
	if err := os.Mkdir(dest, 0o750); err != nil {
		t.Fatal(err)
	}
	return NewReal(RealOptions{ApprovedRoots: []string{root}}).Archive(), dest
}

// isEmpty reports whether dir holds nothing at all — including the staging
// directory, which must not survive a refusal either.
func isEmpty(t *testing.T, dir string) bool {
	t.Helper()
	names, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	return len(names) == 0
}

func TestArchiveExtractRefusesEveryHostileEntryAndLeavesNothing(t *testing.T) {
	ctx := context.Background()
	cases := map[string][]entry{
		"a traversing name":         {{name: "../escape"}, {name: "ok", body: "x"}},
		"a traversing subpath":      {{name: "a/../../escape", body: "x"}},
		"an absolute name":          {{name: "/etc/cron.d/x", body: "x"}},
		"a symlink":                 {{name: "link", typeflag: tar.TypeSymlink, linkname: "/etc/passwd"}},
		"a hardlink":                {{name: "link", typeflag: tar.TypeLink, linkname: "/etc/shadow"}},
		"a fifo":                    {{name: "pipe", typeflag: tar.TypeFifo}},
		"a character device":        {{name: "dev", typeflag: tar.TypeChar}},
		"a block device":            {{name: "dev", typeflag: tar.TypeBlock}},
		"an over-long name":         {{name: strings.Repeat("a", 5000), body: "x"}},
		"a name that names no file": {{name: "."}},
	}
	for name, entries := range cases {
		a, dest := extractFixture(t)
		err := a.Extract(ctx, tarOf(t, entries...), dest, jobs.ArchiveLimits{})
		if !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("extracting %s = %v, want a refusal", name, err)
		}
		// A refused entry leaves NOTHING behind — not the entries that came
		// before it, and not the staging directory.
		if !isEmpty(t, dest) {
			got, _ := os.ReadDir(dest)
			t.Errorf("extracting %s left %d entries in the destination", name, len(got))
		}
	}
}

// A NUL and a backslash cannot be put through archive/tar's own writer (it
// refuses to encode them), so the two names that only a hand-rolled tar could
// carry are checked against the validator directly.
func TestSafeEntryNameRefusesWhatNoTarWriterWillEncode(t *testing.T) {
	for _, name := range []string{"a\x00b", `..\..\x`, "", strings.Repeat("a", maxNameBytes+1)} {
		if _, err := safeEntryName(name); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("safeEntryName(%q) = %v, want a refusal", name, err)
		}
	}
	got, err := safeEntryName("bundle-1/qdrant/alpha.snapshot")
	if err != nil || got != "bundle-1/qdrant/alpha.snapshot" {
		t.Errorf("safeEntryName of an ordinary name = %q, %v", got, err)
	}
}

func TestArchiveExtractRefusesAnEntryLongerThanItsHeaderClaims(t *testing.T) {
	// The size is enforced WHILE copying, not trusted from the header: a
	// decompression bomb gets the header wrong on purpose.
	a, dest := extractFixture(t)
	tp := tarOf(t, entry{name: "big", body: strings.Repeat("x", 4096), size: 4096})
	err := a.Extract(context.Background(), tp, dest, jobs.ArchiveLimits{MaxBytes: 1024})
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("extracting past the byte budget = %v, want a refusal", err)
	}
	if !isEmpty(t, dest) {
		t.Error("the over-budget extraction left files behind")
	}
}

func TestArchiveExtractRefusesTooManyEntries(t *testing.T) {
	a, dest := extractFixture(t)
	var entries []entry
	for i := 0; i < 20; i++ {
		entries = append(entries, entry{name: "f" + string(rune('a'+i)), body: "x"})
	}
	err := a.Extract(context.Background(), tarOf(t, entries...), dest, jobs.ArchiveLimits{MaxEntries: 5})
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("extracting past the entry limit = %v, want a refusal", err)
	}
	if !isEmpty(t, dest) {
		t.Error("the over-count extraction left files behind")
	}
}

func TestArchiveExtractUnpacksAGoodTarWithTheCtlsOwnModes(t *testing.T) {
	a, dest := extractFixture(t)
	tp := tarOf(t,
		entry{name: "bundle-1", typeflag: tar.TypeDir},
		entry{name: "bundle-1/manifest.json", body: `{"id":"bundle-1"}`},
		entry{name: "bundle-1/qdrant", typeflag: tar.TypeDir},
		entry{name: "bundle-1/qdrant/alpha.snapshot", body: "snapshot bytes"},
	)
	if err := a.Extract(context.Background(), tp, dest, jobs.ArchiveLimits{}); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join(dest, "bundle-1", "manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	if string(data) != `{"id":"bundle-1"}` {
		t.Errorf("manifest = %q", data)
	}
	// The modes are the ctl's, never the archive's: a tar carrying 0777 (or
	// setuid) must not re-create it.
	st, err := os.Stat(filepath.Join(dest, "bundle-1", "manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	if st.Mode().Perm() != extractModeFile {
		t.Errorf("file mode = %o, want %o", st.Mode().Perm(), extractModeFile)
	}
	dst, err := os.Stat(filepath.Join(dest, "bundle-1", "qdrant"))
	if err != nil {
		t.Fatal(err)
	}
	if dst.Mode().Perm() != extractModeDir {
		t.Errorf("dir mode = %o, want %o", dst.Mode().Perm(), extractModeDir)
	}
	// The staging directory is gone: it is an implementation detail, not
	// something a caller should find in its destination.
	names, err := os.ReadDir(dest)
	if err != nil {
		t.Fatal(err)
	}
	if len(names) != 1 || names[0].Name() != "bundle-1" {
		t.Errorf("destination holds %v, want only bundle-1", names)
	}
}

func TestArchiveExtractRefusesToOverwriteAnExistingTopLevelEntry(t *testing.T) {
	a, dest := extractFixture(t)
	if err := os.Mkdir(filepath.Join(dest, "bundle-1"), 0o750); err != nil {
		t.Fatal(err)
	}
	tp := tarOf(t, entry{name: "bundle-1/manifest.json", body: "{}"})
	if err := a.Extract(context.Background(), tp, dest, jobs.ArchiveLimits{}); !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("extracting over an existing bundle = %v, want a refusal", err)
	}
}

func TestArchiveExtractRefusesADestinationOutsideTheApprovedRootsOrAbsent(t *testing.T) {
	ctx := context.Background()
	a, dest := extractFixture(t)
	tp := tarOf(t, entry{name: "f", body: "x"})
	if err := a.Extract(ctx, tp, filepath.Join(t.TempDir(), "elsewhere"), jobs.ArchiveLimits{}); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("extracting outside the approved roots = %v, want a refusal", err)
	}
	if err := a.Extract(ctx, tp, filepath.Join(dest, "not-made"), jobs.ArchiveLimits{}); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("extracting into a directory nobody made = %v, want a refusal", err)
	}
}

// ---------------------------------------------------------------- create

func TestArchiveCreateWritesRelativeNamesAndRoundTrips(t *testing.T) {
	root := t.TempDir()
	src := filepath.Join(root, "bundle-1")
	if err := os.MkdirAll(filepath.Join(src, "qdrant"), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(src, "manifest.json"), []byte(`{"id":"bundle-1"}`), 0o640); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(src, "qdrant", "alpha.snapshot"), []byte("snapshot"), 0o640); err != nil {
		t.Fatal(err)
	}
	a := NewReal(RealOptions{ApprovedRoots: []string{root}}).Archive()
	out := filepath.Join(root, "bundle-1.tar")
	if err := a.Create(context.Background(), src, out); err != nil {
		t.Fatal(err)
	}
	st, err := os.Stat(out)
	if err != nil {
		t.Fatal(err)
	}
	if st.Mode().Perm() != archiveMode {
		t.Errorf("tar mode = %o, want %o", st.Mode().Perm(), archiveMode)
	}
	for _, name := range tarNames(t, out) {
		// RELATIVE names: an archive of absolute paths is one that extracts
		// over the host it came from.
		if strings.HasPrefix(name, "/") || strings.Contains(name, "..") {
			t.Errorf("entry %q is not a relative name", name)
		}
	}
	// It round-trips through the driver's own extractor, which is the only
	// consumer that matters.
	dest := filepath.Join(root, "dest")
	if err := os.Mkdir(dest, 0o750); err != nil {
		t.Fatal(err)
	}
	if err := a.Extract(context.Background(), out, dest, jobs.ArchiveLimits{}); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join(dest, "qdrant", "alpha.snapshot"))
	if err != nil || string(data) != "snapshot" {
		t.Fatalf("round trip lost the snapshot: %q %v", data, err)
	}
}

// tarNames lists the entry names of a tar.
func tarNames(t *testing.T, path string) []string {
	t.Helper()
	f, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	var out []string
	tr := tar.NewReader(f)
	for {
		h, err := tr.Next()
		if err != nil {
			return out
		}
		out = append(out, h.Name)
	}
}

func TestArchiveCreateRefusesTheWholeArchiveWhenItMeetsASymlink(t *testing.T) {
	root := t.TempDir()
	src := filepath.Join(root, "bundle-1")
	if err := os.MkdirAll(src, 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(src, "manifest.json"), []byte("{}"), 0o640); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink("/etc/passwd", filepath.Join(src, "sneaky")); err != nil {
		t.Fatal(err)
	}
	out := filepath.Join(root, "bundle-1.tar")
	err := NewReal(RealOptions{ApprovedRoots: []string{root}}).Archive().Create(context.Background(), src, out)
	// Skipping is the tempting answer and it is wrong: an archive that
	// silently lacks a file the directory has is a backup that restores to
	// something other than what was backed up.
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("archiving a tree with a symlink = %v, want a refusal", err)
	}
	if !strings.Contains(err.Error(), "sneaky") {
		t.Errorf("error = %q, want it to name the entry", err)
	}
	if _, err := os.Stat(out); !errors.Is(err, fs.ErrNotExist) {
		t.Errorf("a half-written tar survived: %v", err)
	}
}

func TestArchiveCreateRefusesToOverwriteOrToLeaveTheApprovedRoots(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	src := filepath.Join(root, "b")
	if err := os.MkdirAll(src, 0o750); err != nil {
		t.Fatal(err)
	}
	a := NewReal(RealOptions{ApprovedRoots: []string{root}}).Archive()
	existing := filepath.Join(root, "b.tar")
	if err := os.WriteFile(existing, []byte("an earlier tar"), 0o640); err != nil {
		t.Fatal(err)
	}
	if err := a.Create(ctx, src, existing); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("Create over an existing tar = %v, want a refusal", err)
	}
	if data, _ := os.ReadFile(existing); string(data) != "an earlier tar" {
		t.Error("the existing tar was overwritten anyway")
	}
	if err := a.Create(ctx, src, filepath.Join(t.TempDir(), "out.tar")); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("Create outside the approved roots = %v, want a refusal", err)
	}
	if err := a.Create(ctx, filepath.Join(root, "no-such-dir"), filepath.Join(root, "x.tar")); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("Create of an absent directory = %v, want a refusal", err)
	}
}
