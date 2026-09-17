package doctor

// From the adversarial review of the handover fix, and KEPT: jobStoreCheck
// used to ask the wrong question, and the answer it got was RED.
//
// The check compares OWNERS. SQLite needs WRITE ACCESS, which on this host
// usually comes from the group bits, not from ownership: svcbvbrc's primary
// group is cels, and the sidecars a human already repaired on 2026-09-17 are
//
//	-rw-r--r-- 1 svcbvbrc cels  /rag/data/ctl/jobs.db
//	-rw-rw-r-- 1 wilke    cels  /rag/data/ctl/jobs.db-shm
//	-rw-rw-r-- 1 wilke    cels  /rag/data/ctl/jobs.db-wal
//
// — foreign-owned and perfectly writable by the daemon. The check calls that
// an ERROR, an error makes the run RED, and gateOnDoctor refuses every
// mutation over a red run with "a red finding is never forced". There is no
// escape hatch: `--force-with-doctor-diff` only lifts YELLOW.
//
// Verified against the live host with this PR's binary:
//
//	$ ragstack-ctl doctor --op backup --json | jq .status
//	"red"      # the only error finding is job_engine_unavailable

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/model"
)

// TestAGroupWritableForeignSidecarIsNotAnEngineFailure. The daemon can open
// this store. `doctor` says it cannot, at error level, and that verdict costs
// the deployment every mutation it has.
func TestAGroupWritableForeignSidecarIsNotAnEngineFailure(t *testing.T) {
	w := newWorld(t)
	if err := os.MkdirAll(w.roots.CtlStateDir, 0o755); err != nil {
		t.Fatal(err)
	}
	store := filepath.Join(w.roots.CtlStateDir, "jobs.db")
	// The three files as they stand on coconut today. The daemon OWNS
	// `jobs.db` there (`-rw-r--r-- svcbvbrc`) and the sidecars are another
	// account's but group-writable — and a test cannot chown, so all three are
	// made group-writable here instead. That is the same question for this
	// check: can the daemon's account write the file, by any route.
	for _, p := range []string{store, store + "-wal", store + "-shm"} {
		write(t, p, "x")
		if err := os.Chmod(p, 0o664); err != nil {
			t.Fatal(err)
		}
	}
	w.opts.CtlUser = DefaultCtlUser
	// The daemon is somebody else, in OUR group — which is the fact that makes
	// those files writable, and the fact an ownership check cannot see.
	w.opts.CtlUID, w.opts.CtlGID = os.Geteuid()+1, os.Getegid()

	resp := w.run(t)
	found := findingsForCode(resp, JobEngineUnavailable)
	if len(found) != 0 {
		t.Errorf("a store the daemon can WRITE was reported as unavailable at %s:\n  %s",
			found[0].Level, found[0].Detail)
	}
	// And the consequence, which is the part that matters: an error finding
	// makes the whole run red, and jobs.gateOnDoctor refuses every op over a
	// red run — unforceably.
	if resp.Status == model.StatusRed {
		var reds []string
		for _, f := range resp.Findings {
			if f.Level == model.LevelError {
				reds = append(reds, f.Code)
			}
		}
		t.Errorf("doctor is RED on a healthy deployment, which refuses every mutation: %v", reds)
	}
}

// TestTheJobStoreRepairDoesNotTellAnOperatorToDeleteALiveWAL.
//
// `rm -f jobs.db-wal jobs.db-shm` is not a safe repair for a store a daemon
// has OPEN. SQLite recovers a -wal into the database on the next open; a -wal
// removed out from under a live connection loses every committed transaction
// it still holds and can leave the database inconsistent. The repair a human
// actually applied on 2026-09-17 was a chmod, and the finding should say so.
func TestTheJobStoreRepairDoesNotTellAnOperatorToDeleteALiveWAL(t *testing.T) {
	w := newWorld(t)
	if err := os.MkdirAll(w.roots.CtlStateDir, 0o755); err != nil {
		t.Fatal(err)
	}
	store := filepath.Join(w.roots.CtlStateDir, "jobs.db")
	write(t, store, "sqlite")
	write(t, store+"-wal", "wal")
	w.opts.CtlUser = DefaultCtlUser
	w.opts.CtlUID, w.opts.CtlGID = os.Geteuid()+1, os.Getegid()+1

	found := findingsForCode(w.run(t), JobEngineUnavailable)
	if len(found) != 1 {
		t.Skipf("no finding to inspect (%d)", len(found))
	}
	if r := found[0].Repair; contains(r, "rm -f") && !contains(r, "chmod") {
		t.Errorf("the repair tells an operator to delete the WAL of a store the daemon may have open, "+
			"and offers no safer alternative: %q", r)
	}
}

func contains(s, sub string) bool {
	return len(sub) == 0 || (len(s) >= len(sub) && indexOf(s, sub) >= 0)
}

func indexOf(s, sub string) int {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return i
		}
	}
	return -1
}
