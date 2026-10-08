package drivers

// The three narrow reads the bundle check (PR-G1.2) added: Files.ReadHead,
// Archive.Entries and SQLite.IntegrityCheck. Each is tested against the real
// thing and then for PARITY — the same input to the fake and to the real
// driver gives the same answer — because a fake that read a tar more kindly
// than the host does would let a check pass every test and fail on coconut.

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

func twoEntryTar(t *testing.T) []byte {
	t.Helper()
	var buf bytes.Buffer
	tw := tar.NewWriter(&buf)
	for _, name := range []string{"config.json", "0/segments/a.tar"} {
		body := []byte("body of " + name)
		if err := tw.WriteHeader(&tar.Header{Name: name, Mode: 0o640, Size: int64(len(body)), Typeflag: tar.TypeReg}); err != nil {
			t.Fatal(err)
		}
		if _, err := tw.Write(body); err != nil {
			t.Fatal(err)
		}
	}
	if err := tw.Close(); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}

func TestFakeAndRealArchiveEntriesReadATarTheSameWay(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	real := NewReal(RealOptions{ApprovedRoots: []string{root}}).Archive()
	fake := NewFake(FakeOptions{Roots: []string{"/rag"}})

	good := twoEntryTar(t)
	truncated := good[:1024+100] // inside the second entry's header
	garbage := []byte(strings.Repeat("not a tar archive ", 64))
	empty := func() []byte { // a valid tar with no entries: two zero blocks
		var b bytes.Buffer
		_ = tar.NewWriter(&b).Close()
		return b.Bytes()
	}()

	for _, c := range []struct {
		name    string
		body    []byte
		want    int
		wantErr bool
	}{
		{"good", good, 2, false},
		{"empty", empty, 0, false},
		{"truncated", truncated, 0, true},
		{"garbage", garbage, 0, true},
	} {
		t.Run(c.name, func(t *testing.T) {
			rp := filepath.Join(root, c.name+".snapshot")
			if err := os.WriteFile(rp, c.body, 0o640); err != nil {
				t.Fatal(err)
			}
			fp := "/rag/backups/tenants/dev/b/qdrant/" + c.name + ".snapshot"
			fake.FakeFiles().Put(fp, c.body, 0o640)
			rn, rerr := real.Entries(ctx, rp)
			fn, ferr := fake.Archive().Entries(ctx, fp)
			if (rerr != nil) != c.wantErr || (ferr != nil) != c.wantErr {
				t.Fatalf("real (%d, %v) fake (%d, %v); want error=%v", rn, rerr, fn, ferr, c.wantErr)
			}
			if !c.wantErr && (rn != c.want || fn != c.want) {
				t.Errorf("entries real=%d fake=%d, want %d", rn, fn, c.want)
			}
		})
	}
	_, rerr := real.Entries(ctx, filepath.Join(root, "absent.snapshot"))
	_, ferr := fake.Archive().Entries(ctx, "/rag/absent.snapshot")
	if !errors.Is(rerr, fs.ErrNotExist) || !errors.Is(ferr, fs.ErrNotExist) {
		t.Errorf("absent: real %v, fake %v; want fs.ErrNotExist from both", rerr, ferr)
	}
	// A symlink where a snapshot should be is not followed.
	link := filepath.Join(root, "link.snapshot")
	if err := os.Symlink(filepath.Join(root, "good.snapshot"), link); err != nil {
		t.Fatal(err)
	}
	if _, err := real.Entries(ctx, link); err == nil {
		t.Error("Entries followed a symlink")
	}
}

func TestFakeAndRealReadHeadAgree(t *testing.T) {
	ctx := context.Background()
	rf, root := realFiles(t)
	fake := NewFake(FakeOptions{Roots: []string{"/rag"}})
	for _, c := range []struct {
		name, body, want string
	}{
		{"dump", "PGDMP\x01\x0e\x00 and the rest of the archive", "PGDMP"},
		{"short", "PG", "PG"},
		{"empty", "", ""},
	} {
		rp := filepath.Join(root, c.name)
		if err := os.WriteFile(rp, []byte(c.body), 0o640); err != nil {
			t.Fatal(err)
		}
		fp := "/rag/x/" + c.name
		fake.FakeFiles().Put(fp, []byte(c.body), 0o640)
		rh, rerr := rf.ReadHead(ctx, rp, 5)
		fh, ferr := fake.Files().ReadHead(ctx, fp, 5)
		if rerr != nil || ferr != nil || string(rh) != c.want || string(fh) != c.want {
			t.Errorf("%s: real (%q, %v) fake (%q, %v), want %q", c.name, rh, rerr, fh, ferr, c.want)
		}
	}
	for _, n := range []int{0, -1, maxReadHead + 1} {
		_, rerr := rf.ReadHead(ctx, filepath.Join(root, "dump"), n)
		_, ferr := fake.Files().ReadHead(ctx, "/rag/x/dump", n)
		refusedWithSentinel(t, "real ReadHead", rerr)
		refusedWithSentinel(t, "fake ReadHead", ferr)
	}
	_, rerr := rf.ReadHead(ctx, filepath.Join(root, "absent"), 5)
	_, ferr := fake.Files().ReadHead(ctx, "/rag/x/absent", 5)
	if !errors.Is(rerr, fs.ErrNotExist) || !errors.Is(ferr, fs.ErrNotExist) {
		t.Errorf("absent: real %v, fake %v", rerr, ferr)
	}
	link := filepath.Join(root, "link")
	if err := os.Symlink(filepath.Join(root, "dump"), link); err != nil {
		t.Fatal(err)
	}
	if _, err := rf.ReadHead(ctx, link, 5); err == nil {
		t.Error("ReadHead followed a symlink")
	}
}

func TestSQLiteIntegrityCheckReadsWithoutWriting(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	src := filepath.Join(root, "jobs.db")
	seedDB(t, src, 25)
	d := NewReal(RealOptions{ApprovedRoots: []string{root}})
	// The copy a bundle holds is VACUUM INTO's output, in its own directory.
	if err := os.Mkdir(filepath.Join(root, "bundle"), 0o750); err != nil {
		t.Fatal(err)
	}
	copyPath := filepath.Join(root, "bundle", "jobs.db")
	if _, err := d.SQLite().Backup(ctx, src, copyPath); err != nil {
		t.Fatal(err)
	}
	before, _ := os.ReadDir(filepath.Join(root, "bundle"))
	verdict, err := d.SQLite().IntegrityCheck(ctx, copyPath)
	if err != nil || verdict != "ok" {
		t.Fatalf("IntegrityCheck of a sound copy = %q, %v", verdict, err)
	}
	after, _ := os.ReadDir(filepath.Join(root, "bundle"))
	if len(after) != len(before) {
		t.Errorf("the check left files beside the database: before %v, after %v", before, after)
	}

	// A damaged page is reported, not "ok".
	f, err := os.OpenFile(copyPath, os.O_RDWR, 0)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.WriteAt(bytes4096(), 4096); err != nil {
		t.Fatal(err)
	}
	f.Close()
	if verdict, err := d.SQLite().IntegrityCheck(ctx, copyPath); err == nil || verdict == "ok" {
		t.Errorf("a damaged copy = %q, %v; want the problem", verdict, err)
	}

	// Absent is fs.ErrNotExist from both, and NOTHING is created — sqlite
	// makes a database for a path that is not there.
	fake := NewFake(FakeOptions{Roots: []string{"/rag"}})
	_, rerr := d.SQLite().IntegrityCheck(ctx, filepath.Join(root, "absent.db"))
	_, ferr := fake.SQLite().IntegrityCheck(ctx, "/rag/absent.db")
	if !errors.Is(rerr, fs.ErrNotExist) || !errors.Is(ferr, fs.ErrNotExist) {
		t.Errorf("absent: real %v, fake %v", rerr, ferr)
	}
	if _, err := os.Stat(filepath.Join(root, "absent.db")); !errors.Is(err, fs.ErrNotExist) {
		t.Error("IntegrityCheck created the database it was asked about")
	}
	// A path the driver will not take is refused by both shapes of the rule.
	refusedWithSentinel(t, "real IntegrityCheck of a relative path", errOf2(d.SQLite().IntegrityCheck(ctx, "jobs.db")))
	var _ jobs.SQLite = fake.SQLite()
}

// The fake stores write what the bundle check reads: a snapshot that is a tar
// with entries, a dump with pg_dump's magic, and an elasticsearch repository
// with an index-N root naming each index's directory.
func TestFakeStoresWriteCheckableContent(t *testing.T) {
	ctx := context.Background()
	f := NewFake(FakeOptions{
		Roots:              []string{"/rag"},
		Collections:        map[string][]string{qURL: {"docs"}},
		QdrantSnapshotDirs: map[string]string{qURL: "/rag/data/tenants/dev/qdrant/snapshots"},
		Indices:            map[string][]string{esURL: {"dev-chunks"}},
		ESSnapshotDirs:     map[string]string{esURL: "/rag/data/tenants/dev/elasticsearch/snapshots"},
	})
	name, err := f.Qdrant().Snapshot(ctx, qURL, "docs")
	if err != nil {
		t.Fatal(err)
	}
	if n, err := f.Archive().Entries(ctx, "/rag/data/tenants/dev/qdrant/snapshots/docs/"+name); err != nil || n == 0 {
		t.Errorf("the fake qdrant snapshot is not a tar with entries: %d, %v", n, err)
	}
	if err := f.Elasticsearch().RegisterRepo(ctx, esURL, "ctl-x", "/usr/share/elasticsearch/snapshots/b1", false); err != nil {
		t.Fatal(err)
	}
	if err := f.Elasticsearch().Snapshot(ctx, esURL, "ctl-x", "b1", []string{"dev-chunks"}); err != nil {
		t.Fatal(err)
	}
	root := f.FakeFiles().Content("/rag/data/tenants/dev/elasticsearch/snapshots/b1/index-0")
	if !strings.Contains(string(root), `"dev-chunks":{"id":"fakeidx-dev-chunks"`) {
		t.Errorf("index-0 = %s", root)
	}
	if f.FakeFiles().Content("/rag/data/tenants/dev/elasticsearch/snapshots/b1/indices/fakeidx-dev-chunks/0/__b1") == nil {
		t.Errorf("no per-index directory: %v", f.FakeFiles().Paths())
	}
}
