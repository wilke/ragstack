package doctor

import (
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// fakeOwner reports every file whose base name ends in -shm/-wal as owned by
// uid 4242 ("wilke"), and everything else as owned by us. A test cannot chown,
// so this is how a foreign owner is simulated.
func fakeForeignProbe() SidecarProbe {
	return SidecarProbe{
		UID: func(fi fs.FileInfo) (int, bool) {
			if strings.HasSuffix(fi.Name(), "-shm") || strings.HasSuffix(fi.Name(), "-wal") {
				return 4242, true
			}
			return os.Geteuid(), true
		},
		User: func(uid int) string {
			if uid == 4242 {
				return "wilke"
			}
			return ""
		},
	}
}

func writeSized(t *testing.T, path string, n int) {
	t.Helper()
	if err := os.WriteFile(path, make([]byte, n), 0o644); err != nil {
		t.Fatal(err)
	}
}

// TestJobStoreForeignSidecarsFireTheJobEngineRow is #716 on the fake: the
// 2026-10-09 shape — `jobs.db-shm` 32 KiB and an empty `jobs.db-wal`, both
// 0644 and owned by an account that is not the daemon's. The existing
// job_engine_unavailable row is the one that fires, and its detail names the
// files, the owner by name, the sizes and the empty-WAL recovery.
func TestJobStoreForeignSidecarsFireTheJobEngineRow(t *testing.T) {
	w := newWorld(t)
	if err := os.MkdirAll(w.roots.CtlStateDir, 0o755); err != nil {
		t.Fatal(err)
	}
	store := filepath.Join(w.roots.CtlStateDir, "jobs.db")
	writeSized(t, store, 4096)
	writeSized(t, store+"-shm", 32768)
	writeSized(t, store+"-wal", 0)
	w.opts.CtlUser = DefaultCtlUser
	// The daemon is another account in another group: 0644 files that are
	// not its own are unwritable to it — exactly svcbvbrc vs wilke:cels 0644.
	w.opts.CtlUID, w.opts.CtlGID = os.Geteuid()+1, os.Getegid()+1
	w.opts.SidecarProbe = fakeForeignProbe()

	found := findingsForCode(w.run(t), JobEngineUnavailable)
	if len(found) != 1 {
		t.Fatalf("job_engine_unavailable fired %d times on foreign 0644 sidecars, want 1", len(found))
	}
	d := found[0].Detail
	for _, want := range []string{
		store + "-shm owned by wilke (uid 4242, 32768 bytes)",
		store + "-wal owned by wilke (uid 4242, 0 bytes)",
		"the WAL is empty",
		"rm " + store + "-shm " + store + "-wal",
		"never open it with sqlite3/python",
	} {
		if !strings.Contains(d, want) {
			t.Errorf("detail lacks %q:\n%s", want, d)
		}
	}
	if r := found[0].Repair; !strings.Contains(r, "chmod") || !strings.Contains(r, "EMPTY -wal") {
		t.Errorf("repair %q", r)
	}
}

func TestForeignSidecarsSkipsOwnAndAbsentFiles(t *testing.T) {
	dir := t.TempDir()
	store := filepath.Join(dir, "jobs.db")
	writeSized(t, store, 1)
	writeSized(t, store+"-shm", 8)
	// no -wal at all
	if got := ForeignSidecars(store, os.Geteuid(), SidecarProbe{}); len(got) != 0 {
		t.Errorf("our own sidecar was reported foreign: %+v", got)
	}
	got := ForeignSidecars(store, os.Geteuid(), fakeForeignProbe())
	if len(got) != 1 || got[0].Path != store+"-shm" || got[0].Owner != "wilke" || got[0].Size != 8 {
		t.Errorf("got %+v", got)
	}
	// A uid with no account name falls back to the number.
	p := fakeForeignProbe()
	p.User = func(int) string { return "" }
	if got := ForeignSidecars(store, os.Geteuid(), p); len(got) != 1 || got[0].Owner != "4242" {
		t.Errorf("fallback owner: %+v", got)
	}
}

// A WAL with bytes in it holds committed transactions: the recovery must say
// to copy it aside before anything is removed.
func TestDescribeForeignSidecarsNonEmptyWAL(t *testing.T) {
	files := []ForeignSidecar{
		{Path: "/x/ctl/jobs.db-shm", UID: 4242, Owner: "wilke", Size: 32768},
		{Path: "/x/ctl/jobs.db-wal", UID: 4242, Owner: "wilke", Size: 8272},
	}
	full := DescribeForeignSidecars(files, true)
	if !strings.Contains(full, "copy the sidecars aside first") || !strings.Contains(full, "8272 bytes") {
		t.Errorf("non-empty WAL recovery:\n%s", full)
	}
	// The /health form names files by base name only.
	short := DescribeForeignSidecars(files, false)
	if strings.Contains(short, "/x/ctl") {
		t.Errorf("the path-free form leaked a path:\n%s", short)
	}
	if !strings.Contains(short, "jobs.db-wal owned by wilke") || !strings.Contains(short, "in the ctl state directory") {
		t.Errorf("short form:\n%s", short)
	}
	if DescribeForeignSidecars(nil, true) != "" {
		t.Error("no files, but a message")
	}
}
