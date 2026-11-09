package ops

import (
	"os"
	"strings"
	"syscall"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// A row whose qdrant data dir is on another filesystem (stores.qdrant.data_dir)
// takes its snapshots from THAT dir, and when the move into the bundle gets
// EXDEV from rename(2) it streams the snapshot across and removes the source.
// The fast path (no EXDEV) still renames and never copies.
func TestBackupMovesQdrantSnapshotsAcrossFilesystems(t *testing.T) {
	// Under /rag only because the fake's approved roots are /rag; the
	// cross-filesystem fact is the injected EXDEV.
	const qdir = "/rag/stores2/qdrant/dev"
	moved := func(tn *registry.Tenant) {
		managed(tn)
		tn.Stores.Qdrant.DataDir = qdir
	}
	seedDir := func(o *drivers.FakeOptions) {
		for url := range o.QdrantSnapshotDirs {
			o.QdrantSnapshotDirs[url] = qdir + "/snapshots"
		}
	}

	for _, exdev := range []bool{false, true} {
		oc, fake := fixtureOpts(t, "dev", moved, seedDir)
		if exdev {
			// Every name the fake can give the two snapshots of this run.
			for _, c := range []string{"docs", "chunks"} {
				for _, n := range []string{"1", "2"} {
					src := qdir + "/snapshots/" + c + "/" + c + "-20260914T093000Z-" + n + ".snapshot"
					fake.Fail("files.Rename:"+src, &os.LinkError{Op: "rename", Old: src, New: "bundle", Err: syscall.EXDEV})
				}
			}
		}
		p := plan(t, oc, "backup", map[string]any{"fence": true})
		r := newRunner(oc, fake)
		r.runAll(t, p)

		var renames, copies, removes int
		for _, k := range fake.CallKeys() {
			if !strings.Contains(k, qdir+"/snapshots/") {
				continue
			}
			switch {
			case strings.HasPrefix(k, "files.Rename("):
				renames++
			case strings.HasPrefix(k, "files.CopyFile("):
				copies++
			case strings.HasPrefix(k, "files.Remove("):
				removes++
			}
		}
		if renames != 2 {
			t.Errorf("exdev=%v: %d renames out of the moved snapshots dir, want 2 (one per collection)", exdev, renames)
		}
		wantCopies := 0
		if exdev {
			wantCopies = 2
		}
		if copies != wantCopies || removes != wantCopies {
			t.Errorf("exdev=%v: copies=%d removes=%d, want %d each", exdev, copies, removes, wantCopies)
		}
		var inBundle int
		for _, path := range fake.FakeFiles().Paths() {
			if strings.HasPrefix(path, qdir+"/snapshots/") {
				t.Errorf("exdev=%v: snapshot left in the store: %s", exdev, path)
			}
			if strings.HasPrefix(path, "/rag/backups/tenants/dev/") && strings.HasSuffix(path, ".snapshot") {
				if len(fake.FakeFiles().Content(path)) == 0 {
					t.Errorf("exdev=%v: empty snapshot in the bundle: %s", exdev, path)
				}
				inBundle++
			}
		}
		if inBundle != 2 {
			t.Errorf("exdev=%v: %d snapshots in the bundle, want 2:\n%s", exdev, inBundle,
				strings.Join(fake.FakeFiles().Paths(), "\n"))
		}
	}
}

// Any other rename failure is still a failure: the fallback is EXDEV's alone.
func TestBackupQdrantMoveOtherErrorsStillFail(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	fake.Fail("files.Rename", &os.LinkError{Op: "rename", Err: syscall.EACCES})
	p := plan(t, oc, "backup", map[string]any{})
	r := newRunner(oc, fake)
	var failed bool
	for _, s := range p.Steps {
		if _, err := r.run(s); err != nil {
			failed = true
			break
		}
	}
	if !failed {
		t.Fatal("an EACCES rename did not fail the backup")
	}
	if fake.Count("files.CopyFile") != 0 {
		t.Error("a non-EXDEV rename failure took the copy fallback")
	}
}
