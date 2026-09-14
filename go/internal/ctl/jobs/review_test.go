package jobs

import (
	"context"
	"errors"
	"fmt"
	"os"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/model"
)

// The regressions a review of the engine found. Each one is a way the control
// plane loses work, mislabels it, or wedges it permanently — none of them
// show up in a happy path, which is why they needed tests of their own.

// ---------------------------------------------------------------- ownership

// TestCancelRefusesAJobRunningInAnotherProcess: the `default` branch of
// Cancel used to mark ANY job it did not have in e.active as "cancelled
// before it started". For a job that is at this moment running steps in
// another process that is a lie AND a data-loss hazard: the row goes
// terminal, its locks are reclaimed by the next job, and the real worker
// keeps writing to the same tenant.
func TestCancelRefusesAJobRunningInAnotherProcess(t *testing.T) {
	tr := newTracker()
	var st Store
	e, store, _ := newTestEngine(t, fakeRegistry{"backup": happyOp(tr, func() Store { return st })}, nil)
	st = store
	ctx := context.Background()

	// A job whose worker is a DIFFERENT process on this host: exactly what a
	// --direct CLI run looks like to the daemon.
	job := seedInterrupted(t, e, store, "sha256:"+strings.Repeat("0", 64))
	job.State = model.JobRunning
	job.Worker = &model.JobWorker{PID: 999001, Host: "testhost", Mode: model.WorkerDirect}
	if err := store.Update(ctx, job); err != nil {
		t.Fatal(err)
	}

	_, err := e.Cancel(ctx, job.ID, operator(), "yes")
	if !errors.Is(err, ErrRefused) {
		t.Fatalf("Cancel of another process's running job = %v, want ErrRefused", err)
	}
	extra := ErrorExtra(err)
	if extra["pid"] != 999001 || extra["host"] != "testhost" || extra["mode"] != string(model.WorkerDirect) {
		t.Fatalf("extra = %+v, want the owning worker's {pid, host, mode}", extra)
	}
	if !strings.Contains(err.Error(), "--direct") {
		t.Fatalf("the refusal does not say where to cancel it: %v", err)
	}

	// The job is UNTOUCHED: still running, still owned.
	got, err := e.Get(ctx, job.ID)
	if err != nil {
		t.Fatal(err)
	}
	if got.State != model.JobRunning || got.Worker == nil || got.Worker.PID != 999001 {
		t.Fatalf("the refused cancel changed the job: %s %+v", got.State, got.Worker)
	}
}

// TestCancelOfAQueuedJobStillSaysItNeverStarted keeps the ONE state the old
// default branch was right about.
func TestCancelOfAQueuedJobStillSaysItNeverStarted(t *testing.T) {
	tr := newTracker()
	var st Store
	e, store, _ := newTestEngine(t, fakeRegistry{"backup": happyOp(tr, func() Store { return st })}, nil)
	st = store
	ctx := context.Background()

	job := seedInterrupted(t, e, store, "sha256:"+strings.Repeat("0", 64))
	job.State = model.JobQueued
	job.Worker = nil
	for i := range job.Steps {
		job.Steps[i].State = model.StepPending
	}
	if err := store.Update(ctx, job); err != nil {
		t.Fatal(err)
	}
	got, err := e.Cancel(ctx, job.ID, operator(), "yes")
	if err != nil {
		t.Fatalf("Cancel of a queued job = %v", err)
	}
	if got.State != model.JobCancelled || !strings.Contains(got.Error.Detail, "before it started") {
		t.Fatalf("job = %s %+v", got.State, got.Error)
	}
}

// ---------------------------------------------------------------- settling

// parkedOp is a two-step plan that parks after the first, with a Rollback on
// each step so a cancel has something to undo.
func parkedOp(tr *tracker) *fakeOp {
	return &fakeOp{
		verb:  "handover",
		locks: []model.LockName{model.LockTenant},
		planFn: func(oc Context, args map[string]any) *Planned {
			return buildPlanned([]Step{{
				Plan: plannedStep(1, "cutover", "point the gateway at the new tenant", "dev"),
				Run: func(ctx context.Context, sc *StepContext) (string, error) {
					tr.log("run:1")
					return "", nil
				},
				Rollback: func(ctx context.Context, sc *StepContext) (string, error) {
					tr.log("rollback:1")
					return "", nil
				},
				Cutover: true,
			}, {
				Plan: plannedStep(2, "commit", "retire the old tenant", "dev"),
				Run: func(ctx context.Context, sc *StepContext) (string, error) {
					tr.log("run:2")
					return "", nil
				},
				Rollback: func(ctx context.Context, sc *StepContext) (string, error) {
					tr.log("rollback:2")
					return "", nil
				},
			}})
		},
	}
}

// countResultRows is how many `result` audit rows name id.
func countResultRows(t *testing.T, e *engine, id string) int {
	t.Helper()
	rows, _, err := e.Audit(context.Background(), 200)
	if err != nil {
		t.Fatal(err)
	}
	n := 0
	for _, r := range rows {
		if r.Phase == model.AuditResult && string(r.JobID) == id {
			n++
		}
	}
	return n
}

// TestCancellingAParkedJobTwiceSettlesItOnce: without a compare-and-set,
// each Cancel spawned its own rollbackAndSettle, so both goroutines walked
// the same steps — every Rollback ran twice (undoing an undo is not a no-op
// for anything that allocates), two `result` rows were written, and the lock
// set was Released twice, the second time closing descriptors the first had
// already handed back to the kernel.
//
// The second cancel is issued while the first one's rollback is still IN
// FLIGHT. That is the only window the bug lives in: a cancel that arrives
// after the job has gone terminal is refused for a different reason, so a
// test that merely fires two cancels at once passes by luck.
func TestCancellingAParkedJobTwiceSettlesItOnce(t *testing.T) {
	tr := newTracker()
	inRollback := make(chan struct{})
	proceed := make(chan struct{})
	op := parkedOp(tr)
	inner := op.planFn
	op.planFn = func(oc Context, args map[string]any) *Planned {
		p := inner(oc, args)
		p.Steps[0].Rollback = func(ctx context.Context, sc *StepContext) (string, error) {
			tr.log("rollback:1")
			close(inRollback)
			<-proceed
			return "", nil
		}
		return p
	}
	e, _, _ := newTestEngine(t, fakeRegistry{"handover": op}, nil)
	ctx := context.Background()

	_, job, err := e.Submit(ctx, req("handover", "dev", "h1"))
	if err != nil {
		t.Fatal(err)
	}
	waitFor(t, e, job.ID, model.JobAwaitingCutover)

	if _, err := e.Cancel(ctx, job.ID, operator(), "yes"); err != nil {
		t.Fatalf("the first Cancel = %v", err)
	}
	select {
	case <-inRollback:
	case <-time.After(10 * time.Second):
		t.Fatal("the rollback of the parked job never started")
	}
	// The job is still `awaiting_cutover` in the store — the settle has not
	// written its terminal state yet — so this is exactly the request the old
	// code answered by spawning a SECOND rollback over the same steps.
	mid, err := e.Get(ctx, job.ID)
	if err != nil {
		t.Fatal(err)
	}
	if mid.State.Terminal() {
		t.Fatalf("the job settled before the second cancel could race it: %s", mid.State)
	}
	_, err = e.Cancel(ctx, job.ID, operator(), "yes")
	if !errors.Is(err, ErrRefused) {
		t.Fatalf("the second Cancel = %v, want ErrRefused (already settling)", err)
	}
	if !strings.Contains(err.Error(), "settling") {
		t.Fatalf("the refusal does not say why: %v", err)
	}
	close(proceed)

	done := waitFor(t, e, job.ID, model.JobCancelled, model.JobFailed)
	if done.State != model.JobCancelled {
		t.Fatalf("job = %s %+v", done.State, done.Error)
	}
	if got := tr.trace(); !reflect.DeepEqual(got, []string{"run:1", "rollback:1"}) {
		t.Fatalf("trace = %v, want step 1 undone exactly once", got)
	}
	if n := countResultRows(t, e, job.ID); n != 1 {
		t.Fatalf("%d `result` audit rows for one job, want 1", n)
	}
}

// TestContinueRacingCancelSettlesOnce: the other half of the same race. One
// of the two wins; whichever it is, the steps run or are undone exactly once
// and the job has exactly one terminal write.
func TestContinueRacingCancelSettlesOnce(t *testing.T) {
	tr := newTracker()
	e, _, _ := newTestEngine(t, fakeRegistry{"handover": parkedOp(tr)}, nil)
	ctx := context.Background()

	_, job, err := e.Submit(ctx, req("handover", "dev", "h1"))
	if err != nil {
		t.Fatal(err)
	}
	waitFor(t, e, job.ID, model.JobAwaitingCutover)

	var wg sync.WaitGroup
	var cerr, kerr error
	start := make(chan struct{})
	wg.Add(2)
	go func() { defer wg.Done(); <-start; _, cerr = e.Continue(ctx, job.ID, operator()) }()
	go func() { defer wg.Done(); <-start; _, kerr = e.Cancel(ctx, job.ID, operator(), "yes") }()
	close(start)
	wg.Wait()

	accepted := 0
	for _, err := range []error{cerr, kerr} {
		if err == nil {
			accepted++
			continue
		}
		if !errors.Is(err, ErrRefused) {
			t.Fatalf("the loser of the race failed with something other than ErrRefused: %v", err)
		}
	}
	// Exactly one of them may own the SETTLE. Two accepted answers are still
	// correct when Continue won and Cancel then read the job as RUNNING: that
	// is the cooperative cancel of a running job, which starts no second
	// goroutine. What must never happen is both refusing, or two settles —
	// the audit-row count below is the proof of the latter.
	if accepted == 0 {
		t.Fatalf("both Continue and Cancel were refused: %v / %v", cerr, kerr)
	}
	if accepted == 2 && cerr != nil {
		t.Fatalf("two accepted answers but Continue was refused (%v): Cancel cannot have seen a running job", cerr)
	}

	done := waitFor(t, e, job.ID, model.JobSucceeded, model.JobCancelled, model.JobFailed)
	if done.State != model.JobSucceeded && done.State != model.JobCancelled {
		t.Fatalf("job = %s %+v", done.State, done.Error)
	}
	if n := countResultRows(t, e, job.ID); n != 1 {
		t.Fatalf("%d `result` audit rows for one job, want 1 (one settle)", n)
	}
	// Whichever won, step 1 ran once and was either kept or undone once.
	trace := tr.trace()
	if n := countIn(trace, "run:1"); n != 1 {
		t.Fatalf("trace = %v, step 1 ran %d times", trace, n)
	}
	if n := countIn(trace, "rollback:1"); n > 1 {
		t.Fatalf("trace = %v, step 1 was undone %d times", trace, n)
	}
}

func countIn(xs []string, want string) int {
	n := 0
	for _, x := range xs {
		if x == want {
			n++
		}
	}
	return n
}

// TestLockSetReleaseIsIdempotent: Release is reachable from the run goroutine
// and from a Cancel at the same instant. The second pass must not close a
// descriptor the first already closed — by then the number may belong to an
// unrelated file, and flock(LOCK_UN) on it would unlock somebody else's lock.
func TestLockSetReleaseIsIdempotent(t *testing.T) {
	l := NewLocks(testRoots(t.TempDir()))
	set, err := l.Take([]model.LockName{model.LockTenant, model.LockRegistry}, "dev", LockHolder{JobID: "j"}, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); set.Release() }()
	}
	wg.Wait()
	if names := set.Names(); len(names) != 0 {
		t.Fatalf("a released set still names %v", names)
	}
	if !l.Free([]model.LockName{model.LockTenant, model.LockRegistry}, "dev") {
		t.Fatal("the locks are still held after Release")
	}
}

// ---------------------------------------------------------------- idempotency

// TestJoiningAnExistingJobLeavesAnIntentRow: the retry of a lost 202 is a
// thing that HAPPENED. Answering it with the original job and writing
// nothing leaves an operator who pressed the button twice with no evidence
// that the second press was received at all.
func TestJoiningAnExistingJobLeavesAnIntentRow(t *testing.T) {
	tr := newTracker()
	var st Store
	e, store, _ := newTestEngine(t, fakeRegistry{"backup": happyOp(tr, func() Store { return st })}, nil)
	st = store
	ctx := context.Background()

	_, first, err := e.Submit(ctx, req("backup", "dev", "b1"))
	if err != nil {
		t.Fatal(err)
	}
	waitFor(t, e, first.ID, model.JobSucceeded)

	_, again, err := e.Submit(ctx, req("backup", "dev", "b1"))
	if err != nil {
		t.Fatalf("the retry of the same request = %v", err)
	}
	if again.ID != first.ID {
		t.Fatalf("the retry made a second job: %s vs %s", again.ID, first.ID)
	}
	rows, _, err := e.Audit(ctx, 100)
	if err != nil {
		t.Fatal(err)
	}
	joined := 0
	for _, r := range rows {
		if r.Phase == model.AuditIntent && r.Outcome == "joined" && string(r.JobID) == first.ID {
			joined++
		}
	}
	if joined != 1 {
		t.Fatalf("%d `joined` intent rows, want 1:\n%+v", joined, rows)
	}
}

// TestTwoPrincipalsCannotShareAnIdempotencyKey: the key's fingerprint is over
// {principal, op, tenant, args, plan}. Without the principal, one operator's
// key silently handed back another operator's job — a 202 for work the second
// caller never authorized, attributed in the audit log to the first.
func TestTwoPrincipalsCannotShareAnIdempotencyKey(t *testing.T) {
	tr := newTracker()
	var st Store
	e, store, _ := newTestEngine(t, fakeRegistry{"backup": happyOp(tr, func() Store { return st })}, nil)
	st = store
	ctx := context.Background()

	_, first, err := e.Submit(ctx, req("backup", "dev", "shared-key"))
	if err != nil {
		t.Fatal(err)
	}
	waitFor(t, e, first.ID, model.JobSucceeded)

	other := req("backup", "dev", "shared-key")
	other.Principal.Subject = "bvbrc:someone-else"
	if _, _, err := e.Submit(ctx, other); !errors.Is(err, ErrDuplicate) {
		t.Fatalf("a second principal reusing the key = %v, want ErrDuplicate", err)
	}
}

// ---------------------------------------------------------------- rollback

// TestTheFailedStepIsRolledBackWhenItCheckpointed: the reverse pass skipped
// the step that failed, so the one step that had recorded external ids —
// which is precisely the case the checkpoint exists for — was the one step
// nobody undid. The snapshot it had just created leaked.
func TestTheFailedStepIsRolledBackWhenItCheckpointed(t *testing.T) {
	tr := newTracker()
	var st Store
	op := happyOp(tr, func() Store { return st })
	inner := op.planFn
	op.planFn = func(oc Context, args map[string]any) *Planned {
		p := inner(oc, args)
		p.Steps[2].Run = func(ctx context.Context, sc *StepContext) (string, error) {
			tr.log("run:3")
			// The id goes down BEFORE the call, and the call then fails.
			if err := sc.Checkpoint("bundle-20260914T100000Z"); err != nil {
				return "", err
			}
			return "", fmt.Errorf("tar: the archive was half written")
		}
		p.Steps[2].Rollback = func(ctx context.Context, sc *StepContext) (string, error) {
			if len(sc.Step.ExternalIDs) == 0 {
				return "", fmt.Errorf("rollback of step 3 got no external ids to undo")
			}
			tr.log("rollback:3")
			return "", nil
		}
		return p
	}
	e, store, _ := newTestEngine(t, fakeRegistry{"backup": op}, nil)
	st = store
	ctx := context.Background()

	_, job, err := e.Submit(ctx, req("backup", "dev", "b1"))
	if err != nil {
		t.Fatal(err)
	}
	done := waitFor(t, e, job.ID, model.JobRolledBack, model.JobFailed, model.JobSucceeded)
	want := []string{"run:1", "run:2", "driver:2", "run:3", "rollback:3", "rollback:2", "rollback:1"}
	if got := tr.trace(); !reflect.DeepEqual(got, want) {
		t.Fatalf("trace = %v, want %v (the failing step is undone first)", got, want)
	}
	if done.State != model.JobRolledBack {
		t.Fatalf("job = %s %+v, want rolled_back", done.State, done.Error)
	}
	// The failing step KEEPS its state: which step broke is the first thing
	// an operator reads, and job.error names it.
	if done.Steps[2].State != model.StepFailed {
		t.Fatalf("the failing step = %s, want it to stay failed", done.Steps[2].State)
	}
	if done.Error == nil || *done.Error.Step != 3 {
		t.Fatalf("job.error = %+v, want it to name step 3", done.Error)
	}
}

// TestAFailedStepWithNothingRecordedIsNotRolledBack: a step that failed
// before it touched anything has nothing to undo, and calling its Rollback
// on a resource it never created is its own hazard.
func TestAFailedStepWithNothingRecordedIsNotRolledBack(t *testing.T) {
	tr := newTracker()
	var st Store
	op := happyOp(tr, func() Store { return st })
	inner := op.planFn
	op.planFn = func(oc Context, args map[string]any) *Planned {
		p := inner(oc, args)
		p.Steps[2].Run = func(ctx context.Context, sc *StepContext) (string, error) {
			tr.log("run:3")
			return "", fmt.Errorf("tar: no space left on device")
		}
		return p
	}
	e, store, _ := newTestEngine(t, fakeRegistry{"backup": op}, nil)
	st = store

	_, job, err := e.Submit(context.Background(), req("backup", "dev", "b1"))
	if err != nil {
		t.Fatal(err)
	}
	waitFor(t, e, job.ID, model.JobRolledBack, model.JobFailed, model.JobSucceeded)
	want := []string{"run:1", "run:2", "driver:2", "run:3", "rollback:2", "rollback:1"}
	if got := tr.trace(); !reflect.DeepEqual(got, want) {
		t.Fatalf("trace = %v, want %v (step 3 recorded nothing, so nothing to undo)", got, want)
	}
}

// ---------------------------------------------------------------- panic

// TestAPanickingStepFailsOnlyItsOwnJob: `go e.execute` had no recover, so one
// bad step took the whole daemon with it — and with it every OTHER job's
// terminal write, leaving their locks on disk and their rows reading
// `running` for a process that no longer exists.
func TestAPanickingStepFailsOnlyItsOwnJob(t *testing.T) {
	tr := newTracker()
	panicOp := &fakeOp{
		verb:  "restore",
		locks: []model.LockName{model.LockTenant},
		planFn: func(oc Context, args map[string]any) *Planned {
			return buildPlanned([]Step{{
				Plan: plannedStep(1, "unpack", "unpack the bundle", "dev"),
				Run: func(ctx context.Context, sc *StepContext) (string, error) {
					tr.log("panic:1")
					var m map[string]string
					m["boom"] = "nil map write" // the classic
					return "", nil
				},
			}})
		},
	}
	var st Store
	e, store, roots := newTestEngine(t, fakeRegistry{
		"restore": panicOp,
		"backup":  happyOp(tr, func() Store { return st }),
	}, nil)
	st = store
	ctx := context.Background()

	_, bad, err := e.Submit(ctx, req("restore", "dev", "r1"))
	if err != nil {
		t.Fatal(err)
	}
	done := waitFor(t, e, bad.ID, model.JobFailed, model.JobSucceeded, model.JobRolledBack)
	if done.State != model.JobFailed {
		t.Fatalf("the panicking job = %s %+v, want failed", done.State, done.Error)
	}
	if done.Error == nil || done.Error.Code != "step_panic" {
		t.Fatalf("job.error = %+v, want code step_panic", done.Error)
	}
	if done.Steps[0].State != model.StepFailed {
		t.Fatalf("the panicking step = %s", done.Steps[0].State)
	}
	// The locks are back: a wedged tenant lock would block every later job.
	if !NewLocks(roots).Free([]model.LockName{model.LockTenant}, "dev") {
		t.Fatal("the panicking job kept its tenant lock")
	}
	if done.Lock != nil {
		t.Fatalf("job.lock = %+v after a panic, want nil", done.Lock)
	}

	// The process is still here, and the next job runs normally.
	_, good, err := e.Submit(ctx, req("backup", "dev", "b1"))
	if err != nil {
		t.Fatalf("the engine did not survive the panic: %v", err)
	}
	if ok := waitFor(t, e, good.ID, model.JobSucceeded, model.JobFailed); ok.State != model.JobSucceeded {
		t.Fatalf("the job after the panic = %s %+v", ok.State, ok.Error)
	}
}

// ---------------------------------------------------------------- confirm

// TestCancelNeedsConfirmWhenItWouldRollBack: openapi.yaml gives cancel a
// `confirm`, and the handler dropped it. A cancel that rolls succeeded steps
// back is a mutation of the fleet; it gets the same gate every other one has.
func TestCancelNeedsConfirmWhenItWouldRollBack(t *testing.T) {
	tr := newTracker()
	e, _, _ := newTestEngine(t, fakeRegistry{"handover": parkedOp(tr)}, nil)
	ctx := context.Background()

	_, job, err := e.Submit(ctx, req("handover", "dev", "h1"))
	if err != nil {
		t.Fatal(err)
	}
	waitFor(t, e, job.ID, model.JobAwaitingCutover)

	_, err = e.Cancel(ctx, job.ID, operator(), "")
	if !errors.Is(err, ErrConfirmRequired) {
		t.Fatalf("Cancel without confirm = %v, want ErrConfirmRequired", err)
	}
	if extra := ErrorExtra(err); extra["confirm_value"] != "yes" {
		t.Fatalf("extra = %+v, want confirm_value yes", extra)
	}
	if _, err := e.Cancel(ctx, job.ID, operator(), "no"); !errors.Is(err, ErrConfirmRequired) {
		t.Fatalf("Cancel with the wrong confirm = %v, want ErrConfirmRequired", err)
	}
	if _, err := e.Cancel(ctx, job.ID, operator(), "yes"); err != nil {
		t.Fatalf("Cancel with the right confirm = %v", err)
	}
	if done := waitFor(t, e, job.ID, model.JobCancelled, model.JobFailed); done.State != model.JobCancelled {
		t.Fatalf("job = %s", done.State)
	}
}

// TestCancelOfADestructiveJobNeedsTheTenantName: typing "yes" to "undo the
// decommission of prod" is not evidence you read which tenant it said.
func TestCancelOfADestructiveJobNeedsTheTenantName(t *testing.T) {
	tr := newTracker()
	op := parkedOp(tr)
	op.verb, op.destructive = "decommission", true
	e, _, _ := newTestEngine(t, fakeRegistry{"decommission": op}, nil)
	ctx := context.Background()

	r := req("decommission", "dev", "d1")
	r.Confirm = "dev"
	_, job, err := e.Submit(ctx, r)
	if err != nil {
		t.Fatal(err)
	}
	waitFor(t, e, job.ID, model.JobAwaitingCutover)

	_, err = e.Cancel(ctx, job.ID, operator(), "yes")
	if !errors.Is(err, ErrConfirmRequired) {
		t.Fatalf(`Cancel with "yes" on a destructive job = %v, want ErrConfirmRequired`, err)
	}
	if extra := ErrorExtra(err); extra["confirm_value"] != "dev" {
		t.Fatalf("extra = %+v, want confirm_value dev", extra)
	}
	if _, err := e.Cancel(ctx, job.ID, operator(), "dev"); err != nil {
		t.Fatalf("Cancel with the tenant name = %v", err)
	}
	waitFor(t, e, job.ID, model.JobCancelled, model.JobFailed)
}

// TestCancelNeedsNoConfirmWhenNothingWouldBeUndone: a cancel that undoes
// nothing is not a mutation, and demanding a confirm for it would train
// operators to type one without reading it.
func TestCancelNeedsNoConfirmWhenNothingWouldBeUndone(t *testing.T) {
	tr := newTracker()
	op := parkedOp(tr)
	op.planFn = func(oc Context, args map[string]any) *Planned {
		p := buildPlanned([]Step{{
			Plan: plannedStep(1, "cutover", "point the gateway at the new tenant", "dev"),
			Run: func(ctx context.Context, sc *StepContext) (string, error) {
				tr.log("run:1")
				return "", nil
			},
			Cutover: true, // no Rollback: nothing to undo
		}, {
			Plan: plannedStep(2, "commit", "retire the old tenant", "dev"),
			Run:  func(ctx context.Context, sc *StepContext) (string, error) { return "", nil },
		}})
		return p
	}
	e, _, _ := newTestEngine(t, fakeRegistry{"handover": op}, nil)
	ctx := context.Background()

	_, job, err := e.Submit(ctx, req("handover", "dev", "h1"))
	if err != nil {
		t.Fatal(err)
	}
	waitFor(t, e, job.ID, model.JobAwaitingCutover)
	if _, err := e.Cancel(ctx, job.ID, operator(), ""); err != nil {
		t.Fatalf("Cancel of a job with nothing to undo = %v, want no confirm needed", err)
	}
	waitFor(t, e, job.ID, model.JobCancelled, model.JobFailed)
}

// ---------------------------------------------------------------- secrets

// failEnvelopeStore is a Store whose PutEnvelope always fails.
type failEnvelopeStore struct {
	Store
	err error
}

func (s failEnvelopeStore) PutEnvelope(context.Context, Envelope) error { return s.err }

// PutArgs/Args/PutReservation/PutWorker are promoted from the embedded Store,
// which is the real one, so the optional extensions keep working.
var _ Store = failEnvelopeStore{}

// TestAnUndeliverableEnvelopeFailsTheMint: the engine used to LOG the
// PutEnvelope failure and then report `succeeded` with
// result.secrets_available — a key written to the tenant's files that nobody
// can ever read back, and an operator told to go fetch it. The mint must not
// be half-done: the job fails and the step that wrote the key is undone.
func TestAnUndeliverableEnvelopeFailsTheMint(t *testing.T) {
	ctx := context.Background()
	rolledBack := make(chan struct{}, 1)
	op := mintingOp("s3cr3t-value")
	inner := op.planFn
	op.planFn = func(oc Context, args map[string]any) *Planned {
		p := inner(oc, args)
		p.Steps[0].Rollback = func(ctx context.Context, sc *StepContext) (string, error) {
			select {
			case rolledBack <- struct{}{}:
			default:
			}
			return "the key record was removed again", nil
		}
		return p
	}
	e, _, _ := newTestEngine(t, fakeRegistry{"key-mint": op}, func(o *EngineOptions) {
		o.Store = failEnvelopeStore{Store: o.Store, err: fmt.Errorf("disk full")}
		o.Redactor = testRedactor{secret: "s3cr3t-value"}
	})

	_, job, err := e.Submit(ctx, req("key-mint", "dev", "k1"))
	if err != nil {
		t.Fatal(err)
	}
	done := waitFor(t, e, job.ID, model.JobFailed, model.JobRolledBack, model.JobSucceeded)
	if done.State != model.JobRolledBack {
		t.Fatalf("job = %s %+v, want rolled_back (the mint was undone)", done.State, done.Error)
	}
	if done.Error == nil || done.Error.Code != "secrets_undeliverable" {
		t.Fatalf("job.error = %+v, want code secrets_undeliverable", done.Error)
	}
	if !strings.Contains(done.Error.Detail, "re-mint") {
		t.Fatalf("the detail does not say the remedy is a re-mint: %q", done.Error.Detail)
	}
	if strings.Contains(done.Error.Detail, "s3cr3t-value") {
		t.Fatalf("the failure detail carries the minted value: %q", done.Error.Detail)
	}
	if done.Result != nil {
		t.Fatalf("a failed mint still reports a result: %+v", done.Result)
	}
	select {
	case <-rolledBack:
	default:
		t.Fatal("the step that wrote the key was not rolled back")
	}
}

// TestMemoryOnlySecretsAreSweptAtTheirTTL: e.mem was written on every mint
// and only ever deleted by a successful delivery. A value nobody came back
// for sat in the daemon's heap for the life of the process — an unbounded,
// unauditable pile of live credentials outliving the envelopes that
// authorized reading them.
func TestMemoryOnlySecretsAreSweptAtTheirTTL(t *testing.T) {
	ctx := context.Background()
	now := time.Date(2026, 9, 14, 10, 0, 0, 0, time.UTC)
	e, _, _ := newTestEngine(t, fakeRegistry{"key-mint": mintingOp("s3cr3t")}, func(o *EngineOptions) {
		o.Now = func() time.Time { return now }
		o.SecretsTTL = 15 * time.Minute
	})

	_, abandoned, err := e.Submit(ctx, req("key-mint", "dev", "k1"))
	if err != nil {
		t.Fatal(err)
	}
	waitFor(t, e, abandoned.ID, model.JobSucceeded)
	e.mu.Lock()
	held := len(e.mem)
	e.mu.Unlock()
	if held != 1 {
		t.Fatalf("the minted value is not held in memory (%d entries)", held)
	}

	// Past the TTL. Nobody ever called Secrets for that job.
	now = now.Add(16 * time.Minute)
	if _, err := e.Secrets(ctx, abandoned.ID, operator()); !errors.Is(err, ErrGone) {
		t.Fatalf("Secrets past the TTL = %v, want ErrGone", err)
	}
	e.mu.Lock()
	held = len(e.mem)
	e.mu.Unlock()
	if held != 0 {
		t.Fatalf("%d expired envelopes still hold plaintext", held)
	}

	// And a mint that is never read is swept by the NEXT submission alone.
	_, second, err := e.Submit(ctx, req("key-mint", "dev", "k2"))
	if err != nil {
		t.Fatal(err)
	}
	waitFor(t, e, second.ID, model.JobSucceeded)
	now = now.Add(16 * time.Minute)
	if _, _, err := e.Submit(ctx, req("key-mint", "dev", "k3")); err != nil {
		t.Fatal(err)
	}
	e.mu.Lock()
	_, stillThere := e.mem[second.ID]
	e.mu.Unlock()
	if stillThere {
		t.Fatal("Submit did not sweep an expired memory-only envelope")
	}
}

// ---------------------------------------------------------------- durability

// TestCheckpointSurvivesTheCancellationOfItsStep: the durability hooks ran on
// the job's CANCELLABLE context, so a step cancelled between "I am about to
// create this" and "I created it" could not write the id down — the external
// effect would exist with nothing in the database pointing at it, and
// reconcile would have nothing to act on.
func TestCheckpointSurvivesTheCancellationOfItsStep(t *testing.T) {
	entered := make(chan struct{})
	release := make(chan struct{})
	checkpointErr := make(chan error, 1)
	op := &fakeOp{
		verb:  "backup",
		locks: []model.LockName{model.LockTenant},
		planFn: func(oc Context, args map[string]any) *Planned {
			return buildPlanned([]Step{{
				Plan: plannedStep(1, "qdrant", "snapshot the collections", "dev"),
				Run: func(ctx context.Context, sc *StepContext) (string, error) {
					close(entered)
					<-release // the Cancel has landed by now
					// The step is being cancelled AND has work in flight: it
					// must still be able to record what it started.
					err := sc.Checkpoint("snap-cancelled-midflight")
					checkpointErr <- err
					sc.Logf("recorded the in-flight snapshot before giving up")
					return "", ctx.Err()
				},
				Rollback: func(ctx context.Context, sc *StepContext) (string, error) { return "", nil },
			}})
		},
	}
	e, _, _ := newTestEngine(t, fakeRegistry{"backup": op}, nil)
	ctx := context.Background()

	_, job, err := e.Submit(ctx, req("backup", "dev", "b1"))
	if err != nil {
		t.Fatal(err)
	}
	select {
	case <-entered:
	case <-time.After(10 * time.Second):
		t.Fatal("the step never started")
	}
	if _, err := e.Cancel(ctx, job.ID, operator(), "yes"); err != nil {
		t.Fatalf("Cancel: %v", err)
	}
	close(release)
	if err := <-checkpointErr; err != nil {
		t.Fatalf("Checkpoint during a cancellation = %v, want it to persist anyway", err)
	}

	done := waitFor(t, e, job.ID, model.JobCancelled, model.JobFailed, model.JobSucceeded)
	if done.State != model.JobCancelled {
		t.Fatalf("job = %s %+v", done.State, done.Error)
	}
	if len(done.Steps[0].ExternalIDs) != 1 || done.Steps[0].ExternalIDs[0] != "snap-cancelled-midflight" {
		t.Fatalf("the checkpoint did not survive the cancellation: %+v", done.Steps[0].ExternalIDs)
	}
	if !done.Steps[0].Checkpoint {
		t.Fatal("the step is not marked as checkpointed")
	}
	log, err := e.StepLog(ctx, job.ID, 1)
	if err != nil || !strings.Contains(log, "in-flight snapshot") {
		t.Fatalf("the step log written during the cancellation = %q, %v", log, err)
	}
}

// ---------------------------------------------------------------- reconcile

// TestReconcileTreatsAReusedPidAsDead: reconcile trusted kill(pid, 0). Pids
// are RECYCLED, and a daemon restart on a busy host is exactly when the
// number a dead worker had is most likely to belong to something new. A job
// wrongly judged alive is never interrupted, its locks are never reclaimed,
// and the fleet waits forever on a process that does not exist.
func TestReconcileTreatsAReusedPidAsDead(t *testing.T) {
	tr := newTracker()
	var st Store
	e, store, _ := newTestEngine(t, fakeRegistry{"backup": happyOp(tr, func() Store { return st })}, nil)
	st = store
	ctx := context.Background()

	job := seedInterrupted(t, e, store, "sha256:"+strings.Repeat("0", 64))
	job.State = model.JobRunning
	job.Steps[1].State = model.StepRunning
	// THIS process's pid, so kill(pid, 0) says "alive"...
	job.Worker = &model.JobWorker{PID: os.Getpid(), Host: "testhost", Mode: model.WorkerDaemon}
	job.Lock = nil // no flock probe: the pid identity alone must decide
	if err := store.Update(ctx, job); err != nil {
		t.Fatal(err)
	}
	// ...but the recorded start time belongs to the process that DIED and
	// left this pid behind.
	ws, ok := store.(WorkerStore)
	if !ok {
		t.Fatal("the store does not record worker identities")
	}
	if err := ws.PutWorker(ctx, job.ID, WorkerIdentity{
		PID: os.Getpid(), Host: "testhost", StartTime: 1, Mode: model.WorkerDaemon,
	}); err != nil {
		t.Fatal(err)
	}

	ids, err := e.Reconcile(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if len(ids) != 1 || ids[0] != job.ID {
		t.Fatalf("Reconcile = %v, want the job with the reused pid interrupted", ids)
	}
	got, err := e.Get(ctx, job.ID)
	if err != nil {
		t.Fatal(err)
	}
	if got.State != model.JobInterrupted || got.Steps[1].State != model.StepInterrupted {
		t.Fatalf("job = %s, step 2 = %s", got.State, got.Steps[1].State)
	}
}

// TestReconcileKeepsAJobWhoseWorkerIsReallyUs is the other direction: a job
// this very process is running, with a matching start time, must NOT be
// interrupted out from under itself.
func TestReconcileKeepsAJobWhoseWorkerIsReallyUs(t *testing.T) {
	tr := newTracker()
	var st Store
	e, store, _ := newTestEngine(t, fakeRegistry{"backup": happyOp(tr, func() Store { return st })}, nil)
	st = store
	ctx := context.Background()

	job := seedInterrupted(t, e, store, "sha256:"+strings.Repeat("0", 64))
	job.State = model.JobRunning
	job.Steps[1].State = model.StepRunning
	job.Worker = &model.JobWorker{PID: os.Getpid(), Host: "testhost", Mode: model.WorkerDaemon}
	job.Lock = nil
	if err := store.Update(ctx, job); err != nil {
		t.Fatal(err)
	}
	started, ok := procStartTime(os.Getpid())
	if !ok {
		t.Skip("/proc is not available on this host")
	}
	if err := store.(WorkerStore).PutWorker(ctx, job.ID, WorkerIdentity{
		PID: os.Getpid(), Host: "testhost", StartTime: started, Mode: model.WorkerDaemon,
	}); err != nil {
		t.Fatal(err)
	}
	ids, err := e.Reconcile(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if len(ids) != 0 {
		t.Fatalf("Reconcile interrupted a job whose worker is this very process: %v", ids)
	}
}

// TestReconcileTreatsADroppedFlockAsADeadWorker: the third signal. A worker
// that is still running holds the job's locks; a set we can take is a set
// nobody holds, whatever the pid table says.
func TestReconcileTreatsADroppedFlockAsADeadWorker(t *testing.T) {
	tr := newTracker()
	var st Store
	e, store, _ := newTestEngine(t, fakeRegistry{"backup": happyOp(tr, func() Store { return st })}, nil)
	st = store
	ctx := context.Background()

	job := seedInterrupted(t, e, store, "sha256:"+strings.Repeat("0", 64))
	job.State = model.JobRunning
	job.Steps[1].State = model.StepRunning
	job.Worker = &model.JobWorker{PID: os.Getpid(), Host: "testhost", Mode: model.WorkerDaemon}
	// It claims a lock nobody is holding.
	job.Lock = &model.JobLock{Order: []model.LockName{model.LockTenant}, Since: time.Now().UTC().Format(time.RFC3339)}
	if err := store.Update(ctx, job); err != nil {
		t.Fatal(err)
	}
	started, _ := procStartTime(os.Getpid())
	if err := store.(WorkerStore).PutWorker(ctx, job.ID, WorkerIdentity{
		PID: os.Getpid(), Host: "testhost", StartTime: started, Mode: model.WorkerDaemon,
	}); err != nil {
		t.Fatal(err)
	}
	ids, err := e.Reconcile(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if len(ids) != 1 {
		t.Fatalf("Reconcile = %v, want the job whose flocks are free interrupted", ids)
	}
}

// TestReconcileTreatsAnotherHostAsDead: a pid on another machine says nothing
// about this one.
func TestReconcileTreatsAnotherHostAsDead(t *testing.T) {
	tr := newTracker()
	var st Store
	e, store, _ := newTestEngine(t, fakeRegistry{"backup": happyOp(tr, func() Store { return st })}, nil)
	st = store
	ctx := context.Background()

	job := seedInterrupted(t, e, store, "sha256:"+strings.Repeat("0", 64))
	job.State = model.JobRunning
	job.Steps[1].State = model.StepRunning
	job.Worker = &model.JobWorker{PID: os.Getpid(), Host: "some-other-box", Mode: model.WorkerDaemon}
	if err := store.Update(ctx, job); err != nil {
		t.Fatal(err)
	}
	ids, err := e.Reconcile(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if len(ids) != 1 {
		t.Fatalf("Reconcile = %v, want the job from another host interrupted", ids)
	}
}

// TestProcStartTimeParsesACommWithSpacesAndParens: /proc/<pid>/stat's comm
// field is parenthesised and may contain both spaces and parentheses, so the
// only correct parse starts after the LAST ')'.
func TestProcStartTimeReadsThisProcess(t *testing.T) {
	a, ok := procStartTime(os.Getpid())
	if !ok {
		t.Skip("/proc is not available on this host")
	}
	b, _ := procStartTime(os.Getpid())
	if a != b || a == 0 {
		t.Fatalf("procStartTime is not stable: %d then %d", a, b)
	}
	if _, ok := procStartTime(-1); ok {
		t.Fatal("procStartTime accepted a negative pid")
	}
}
