package ops

// From the adversarial review of the handover fix, and KEPT: the abandon's
// swap-back used to report SUCCESS over the one state in which it had done
// nothing useful.
//
// The first branch was "the original is not there → success with a sentence",
// justified as "nothing was moved, or somebody already moved it back". Those
// are two different worlds and the function could not tell them apart, so it
// picked the harmless reading of both. The harmful one is real:
//
//   - an operator deletes `data.pre-handover-<ts>` after reading doctor's
//     `pre_handover_copy_present` (the repair that finding prints is `rm -rf`),
//     and only afterwards decides to abandon;
//   - or the take's worker died between its second rename and the registry
//     write, so the row names a pre-handover path the take had already
//     consumed — recoverable by a resume, invisible to an abandon.
//
// In both, `data` is the cluster the TAKE created. The abandon said "left
// exactly as it is", succeeded, cleared the handover block and handed the
// tenant back to `restore.sh` — and the releasing account's postgres then
// refused the directory for exactly the reason this whole file exists.
//
// It now refuses, and says which of the two directories is where.

import (
	"context"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

func TestTheAbandonRefusesWhenTheOriginalIsGoneAndDataIsStillTheTakesCopy(t *testing.T) {
	oc, fake, pp := pgFixture(t, released, nil)

	// The take's swap, for real: `data` becomes this account's own (empty)
	// cluster directory and the original goes to `data.pre-handover-<ts>`.
	seedReleasedDump(t, oc, fake)
	takePlan := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	swap, mn := stepNamed(t, takePlan, "put an empty cluster directory in place")
	tr := newRunner(oc, fake)
	if _, err := tr.run(swap); err != nil {
		t.Fatalf("the swap failed: %v", err)
	}
	copyPath := idValue(t, tr.externalIDs(mn), "pgfresh:")
	aside := idValue(t, tr.externalIDs(mn), "pgaside:")

	// The original is gone: an operator followed doctor's own `rm -rf` repair
	// while the handover had not been committed.
	files := fake.FakeFiles()
	removeTree(files, aside)
	if _, err := files.Stat(context.Background(), aside); err == nil {
		t.Fatalf("%s is still there; the fixture did not remove it", aside)
	}

	tn := oc.Fleet.Tenants["dev"]
	tn.State, tn.Owner, tn.Supervisor = "active", "svcbvbrc", supervisorInstance
	tn.Handover.Phase = registry.HandoverTaken
	tn.Handover.PostgresData.PreHandover = aside
	tn.Handover.PostgresData.Copy = copyPath
	tn.Handover.PostgresData.MigratedAt = "2026-09-17T08:30:00Z"
	fake.FakeInstances().StopAll()
	fake.FakeProc().FreePort(tn.Ports.API)
	fake.FakeProc().FreePort(tn.Ports.PG)

	p := planAs(t, oc, "wilke", "handover", map[string]any{"phase": "abandon"})
	back, _ := stepNamed(t, p, "put the original postgres cluster back")
	msg, err := newRunner(oc, fake).run(back)

	// `data` is still the TAKING account's copy: the releasing account's
	// postgres will refuse it. Whatever the step says, it must not be success.
	st, serr := files.Stat(context.Background(), pp.data)
	if serr != nil {
		t.Fatalf("stat %s: %v", pp.data, serr)
	}
	self, _ := files.SelfUID(context.Background())
	if st.UID == otherAccountUID {
		t.Fatalf("the fixture put the original back by itself; nothing is asserted (uid %d)", st.UID)
	}
	switch {
	case err == nil:
		t.Errorf("the abandon SUCCEEDED (%q) while %s is still uid %d's cluster, not the releasing account's "+
			"(uid %d): the row is now cleared and the tenant is handed back on a directory its owner's "+
			"postgres will refuse", msg, pp.data, st.UID, otherAccountUID)
	case !errorIsRefused(err):
		t.Errorf("the abandon failed with something other than a refusal: %v", err)
	default:
		// The refusal has to say WHICH state it found, or an operator holding
		// a tenant that will not start learns nothing from it.
		for _, want := range []string{aside, pp.data, "is GONE"} {
			if !strings.Contains(err.Error(), want) {
				t.Errorf("the refusal does not name %q: %v", want, err)
			}
		}
		if st.UID == self && !strings.Contains(err.Error(), "this account") {
			t.Errorf("the refusal does not say whose cluster is in place: %v", err)
		}
	}
}

func errorIsRefused(err error) bool {
	return err != nil && strings.Contains(err.Error(), jobs.ErrRefused.Error())
}

// removeTree is `rm -rf` on the fake filesystem: the directory and everything
// the fake records under it.
func removeTree(f *drivers.FakeFiles, root string) {
	prefix := strings.TrimSuffix(root, "/") + "/"
	for p := range f.Files {
		if p == root || strings.HasPrefix(p, prefix) {
			delete(f.Files, p)
		}
	}
	for d := range f.Dirs {
		if d == root || strings.HasPrefix(d, prefix) {
			delete(f.Dirs, d)
		}
	}
	for o := range f.Owners {
		if o == root || strings.HasPrefix(o, prefix) {
			delete(f.Owners, o)
		}
	}
}
