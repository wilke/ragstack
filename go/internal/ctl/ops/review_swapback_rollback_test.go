package ops

// ADVERSARIAL REVIEW (not committed): the F6 fix overshot. A rollback of a
// swap that never moved anything now REFUSES, and names the original as "the
// cluster the take created".
//
// runPGClusterSwap checkpoints both names BEFORE it does anything — correctly,
// that is what makes a crash recoverable — and then does MkdirAll, Rename,
// Rename. A failure between the checkpoint and the FIRST rename (a full
// filesystem, a parent the account cannot write, the rename itself refused)
// leaves the world untouched: `data` is still the original, and there is no
// `aside`.
//
// rollbackAndSettle offers the Rollback to a failed step precisely because it
// checkpointed, so it runs. It has both ids, so it calls swapPGClusterBack with
// moved=true — and that branch reads "the original is GONE" from an absent
// aside, stats `data`, finds another account's uid, and refuses with
//
//	the original postgres cluster … is GONE, and …/data is owned by uid 3581 —
//	the cluster the take created. This handover cannot be put back
//
// Every clause of which is false. Nothing was moved, `data` IS the original,
// and the rollback that would have been a no-op is recorded as failed — which
// is what an operator then has to reason about while the tenant is down.
//
// The function already holds the fact that settles it: if `data` exists and is
// NOT this account's, the take never swapped, and there is nothing to put back.

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/registry"
)

func TestRollingBackASwapThatNeverMovedAnythingIsANoOpNotARefusal(t *testing.T) {
	oc, fake, pp := pgFixture(t, released, nil)
	seedReleasedDump(t, oc, fake)

	// The step fails AFTER its checkpoint and BEFORE the first rename: the
	// empty directory cannot be created. A full filesystem is the ordinary
	// way to get here, and the free-space check two lines above only promises
	// about 64 MB.
	boom := errors.New("no space left on device")
	fake.Fail("files.MkdirAll:"+pp.dir+"/data.svcbvbrc-20260914T093000Z", boom)

	takePlan := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	swap, mn := stepNamed(t, takePlan, "put an empty cluster directory in place")
	tr := newRunner(oc, fake)
	if _, err := tr.run(swap); err == nil {
		t.Fatal("the swap succeeded; this test needs it to fail before the first rename")
	}

	ids := tr.externalIDs(mn)
	if len(ids) == 0 {
		t.Fatal("the step recorded no external ids, so its Rollback would not be offered")
	}
	aside := idValue(t, ids, "pgaside:")

	// The world is untouched: `data` is the original and there is no aside.
	files := fake.FakeFiles()
	st, err := files.Stat(context.Background(), pp.data)
	if err != nil {
		t.Fatalf("stat %s: %v", pp.data, err)
	}
	if st.UID != otherAccountUID {
		t.Fatalf("%s is uid %d, not the releasing account's: the fixture already swapped", pp.data, st.UID)
	}
	if _, err := files.Stat(context.Background(), aside); err == nil {
		t.Fatalf("%s exists; the first rename ran after all", aside)
	}

	// The rollback rollbackAndSettle would run for this failed-but-checkpointed
	// step. It has nothing to undo.
	msg, rerr := tr.rollback(swap)
	if rerr != nil {
		t.Errorf("rolling back a swap that moved nothing failed: %v\n"+
			"  %s is the ORIGINAL (uid %d) and %s was never created — there is nothing to put back, and a "+
			"rollback that refuses here turns a clean failure into a job whose rollback is recorded as failed",
			rerr, pp.data, st.UID, aside)
		if strings.Contains(rerr.Error(), "the cluster the take created") {
			t.Errorf("…and it says the original IS the take's cluster, which is the opposite of the truth")
		}
		return
	}
	// And it must leave the original where it is.
	if st2, err := files.Stat(context.Background(), pp.data); err != nil || st2.UID != otherAccountUID {
		t.Errorf("the rollback moved the original: %s = %+v (err %v)", pp.data, st2, err)
	}
	_ = msg
	_ = registry.HandoverReleased
}
