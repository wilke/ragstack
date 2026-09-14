package jobs

import (
	"context"
	"errors"
	"fmt"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// buildPlanned assembles a Planned from executable steps, keeping
// Plan.Steps and Steps in lockstep (the engine refuses a mismatch).
func buildPlanned(steps []Step) *Planned {
	p := &Planned{Steps: steps}
	for i := range steps {
		steps[i].Plan.N = i + 1
		p.Plan.Steps = append(p.Plan.Steps, steps[i].Plan)
	}
	return p
}

func req(op, tenant, key string) Request {
	return Request{
		Op: op, Tenant: tenant, Args: map[string]any{"fence": true},
		IdempotencyKey: key, Principal: operator(), Mode: model.WorkerDirect,
	}
}

// happyOp is the three-step backup every happy-path test runs: a probe, a
// snapshot that CHECKPOINTS its external id before the call that creates it,
// and a bundle step that reserves a resource. Every step is reversible.
func happyOp(tr *tracker, store func() Store) *fakeOp {
	return &fakeOp{
		verb:  "backup",
		locks: []model.LockName{model.LockTenant, model.LockRegistry},
		planFn: func(oc Context, args map[string]any) *Planned {
			pl := buildPlanned([]Step{{
				Plan: plannedStep(1, "probe", "check the tenant is quiet", "dev"),
				Run: func(ctx context.Context, sc *StepContext) (string, error) {
					tr.log("run:1")
					sc.Logf("probe: no running ingest on %s", "dev")
					return "", nil
				},
				Rollback: func(ctx context.Context, sc *StepContext) (string, error) {
					tr.log("rollback:1")
					return "", nil
				},
			}, {
				Plan: plannedStep(2, "qdrant", "snapshot the collections", "dev"),
				Run: func(ctx context.Context, sc *StepContext) (string, error) {
					tr.log("run:2")
					// The external id goes down FIRST; only then the call.
					if err := sc.Checkpoint("snap-20260914T100000Z"); err != nil {
						return "", err
					}
					j, err := store().Get(ctx, sc.Job.ID)
					if err != nil {
						return "", err
					}
					tr.record("driver:2", j.Steps[1].ExternalIDs)
					tr.log("driver:2")
					return "snapshot taken", nil
				},
				Rollback: func(ctx context.Context, sc *StepContext) (string, error) {
					tr.log("rollback:2")
					return "", nil
				},
				Reconcile: func(ctx context.Context, sc *StepContext) (Reconciliation, error) {
					if len(sc.Step.ExternalIDs) > 0 {
						return ReconcileDone, nil
					}
					return ReconcileRedo, nil
				},
			}, {
				Plan: plannedStep(3, "tar", "write the bundle", "dev"),
				Run: func(ctx context.Context, sc *StepContext) (string, error) {
					tr.log("run:3")
					return "", sc.Reserve("dir:/rag/backups/tenants/dev/20260914T100000Z-backup", nil)
				},
				Rollback: func(ctx context.Context, sc *StepContext) (string, error) {
					tr.log("rollback:3")
					return "", nil
				},
			}})
			pl.Result = func() map[string]any {
				return map[string]any{"bundle": "20260914T100000Z-backup", "fenced": true}
			}
			return pl
		},
	}
}

// ---------------------------------------------------------------- submit

func TestDryRunPlansAndPersistsNothing(t *testing.T) {
	tr := newTracker()
	var st Store
	e, store, roots := newTestEngine(t, fakeRegistry{"backup": happyOp(tr, func() Store { return st })}, nil)
	st = store
	ctx := context.Background()

	r := req("backup", "dev", "")
	r.DryRun = true
	plan, job, err := e.Submit(ctx, r)
	if err != nil {
		t.Fatalf("dry run: %v", err)
	}
	if job != nil {
		t.Fatal("a dry run produced a job")
	}
	if len(plan.Steps) != 3 || plan.RegistryGeneration != 7 || plan.Op != "backup" {
		t.Fatalf("plan = %+v", plan)
	}
	if !strings.HasPrefix(plan.PlanHash, "sha256:") {
		t.Fatalf("plan_hash = %q", plan.PlanHash)
	}
	jobs, _, _ := store.List(ctx, ListFilter{})
	if len(jobs) != 0 {
		t.Fatalf("a dry run persisted %d jobs", len(jobs))
	}
	if trace := tr.trace(); len(trace) != 0 {
		t.Fatalf("a dry run executed %v", trace)
	}
	// Nothing was locked either: the lock is free for a real taker.
	l := NewLocks(roots)
	set, err := l.Take([]model.LockName{model.LockTenant}, "dev", LockHolder{}, time.Now())
	if err != nil {
		t.Fatalf("a dry run left the tenant lock held: %v", err)
	}
	set.Release()
}

// TestHappyPathRunsEveryStepAndCheckpointsBeforeTheCall is the core claim of
// the engine: the steps run in order, the external id is DURABLE before the
// call that creates it, and the job settles with a result and two audit rows.
func TestHappyPathRunsEveryStepAndCheckpointsBeforeTheCall(t *testing.T) {
	tr := newTracker()
	var st Store
	e, store, _ := newTestEngine(t, fakeRegistry{"backup": happyOp(tr, func() Store { return st })}, nil)
	st = store
	ctx := context.Background()

	_, job, err := e.Submit(ctx, req("backup", "dev", "backup-1"))
	if err != nil {
		t.Fatalf("Submit: %v", err)
	}
	done := waitFor(t, e, job.ID, model.JobSucceeded, model.JobFailed, model.JobRolledBack)
	if done.State != model.JobSucceeded {
		t.Fatalf("job %s = %s (%+v)", done.ID, done.State, done.Error)
	}

	want := []string{"run:1", "run:2", "driver:2", "run:3"}
	if got := tr.trace(); !reflect.DeepEqual(got, want) {
		t.Fatalf("trace = %v, want %v", got, want)
	}
	if ids := tr.seen("driver:2"); len(ids) != 1 || ids[0] != "snap-20260914T100000Z" {
		t.Fatalf("the store held %v when the external call ran; the checkpoint is not durable first", ids)
	}
	if done.Result["bundle"] != "20260914T100000Z-backup" {
		t.Fatalf("result = %+v", done.Result)
	}
	if done.Worker != nil || done.Lock != nil {
		t.Fatalf("a finished job still claims a worker/lock: %+v %+v", done.Worker, done.Lock)
	}
	if len(done.Reservations) != 1 {
		t.Fatalf("reservations = %+v", done.Reservations)
	}
	for _, s := range done.Steps {
		if s.State != model.StepSucceeded {
			t.Fatalf("step %d = %s", s.N, s.State)
		}
	}
	if done.Steps[1].Checkpoint != true || len(done.Steps[1].ExternalIDs) != 1 {
		t.Fatalf("step 2 checkpoint = %+v", done.Steps[1])
	}

	log, err := e.StepLog(ctx, job.ID, 1)
	if err != nil || !strings.Contains(log, "no running ingest") {
		t.Fatalf("step log = %q, %v", log, err)
	}

	rows, _, err := e.Audit(ctx, 10)
	if err != nil || len(rows) != 2 {
		t.Fatalf("audit rows = %d, %v", len(rows), err)
	}
	if rows[1].Phase != model.AuditIntent || rows[1].Outcome != "accepted" {
		t.Fatalf("intent row = %+v", rows[1])
	}
	if rows[0].Phase != model.AuditResult || rows[0].Outcome != "succeeded" || rows[0].DurationMS == nil {
		t.Fatalf("result row = %+v", rows[0])
	}
}

func TestSubmitIsIdempotent(t *testing.T) {
	tr := newTracker()
	var st Store
	e, store, _ := newTestEngine(t, fakeRegistry{"backup": happyOp(tr, func() Store { return st })}, nil)
	st = store
	ctx := context.Background()

	_, first, err := e.Submit(ctx, req("backup", "dev", "backup-1"))
	if err != nil {
		t.Fatal(err)
	}
	waitFor(t, e, first.ID, model.JobSucceeded)

	_, again, err := e.Submit(ctx, req("backup", "dev", "backup-1"))
	if err != nil {
		t.Fatalf("the retry of the same request: %v", err)
	}
	if again.ID != first.ID {
		t.Fatalf("the retry made a second job %s", again.ID)
	}
	if again.State != model.JobSucceeded {
		t.Fatalf("the retry returned state %q, want the stored one", again.State)
	}

	other := req("backup", "dev", "backup-1")
	other.Args = map[string]any{"fence": false}
	if _, _, err := e.Submit(ctx, other); !errors.Is(err, ErrDuplicate) {
		t.Fatalf("a different request under the same key = %v, want ErrDuplicate", err)
	}
	all, _, _ := store.List(ctx, ListFilter{})
	if len(all) != 1 {
		t.Fatalf("%d jobs exist, want 1", len(all))
	}
}

func TestSubmitRefusesUnknownVerbsTenantsAndArgs(t *testing.T) {
	tr := newTracker()
	var st Store
	op := happyOp(tr, func() Store { return st })
	op.validate = func(args map[string]any) error {
		if _, ok := args["fence"]; !ok {
			return fmt.Errorf("fence is required")
		}
		return nil
	}
	e, store, _ := newTestEngine(t, fakeRegistry{"backup": op}, nil)
	st = store
	ctx := context.Background()

	if _, _, err := e.Submit(ctx, req("nosuch", "dev", "k")); !errors.Is(err, ErrNotFound) {
		t.Fatalf("unknown verb = %v, want ErrNotFound", err)
	}
	if _, _, err := e.Submit(ctx, req("backup", "nosuch", "k")); !errors.Is(err, ErrNotFound) {
		t.Fatalf("unknown tenant = %v, want ErrNotFound", err)
	}
	bad := req("backup", "dev", "k")
	bad.Args = map[string]any{}
	if _, _, err := e.Submit(ctx, bad); !errors.Is(err, ErrValidation) {
		t.Fatalf("bad args = %v, want ErrValidation", err)
	}
	viewer := req("backup", "dev", "k")
	viewer.Principal.Role = "viewer"
	if _, _, err := e.Submit(ctx, viewer); !errors.Is(err, ErrForbidden) {
		t.Fatalf("a viewer = %v, want ErrForbidden", err)
	}
	noKey := req("backup", "dev", "")
	if _, _, err := e.Submit(ctx, noKey); !errors.Is(err, ErrValidation) {
		t.Fatalf("a missing idempotency key = %v, want ErrValidation", err)
	}
}

// TestConfirmRules: a destructive op is confirmed by NAMING the tenant, a
// non-destructive one that asks for confirmation by "yes".
func TestConfirmRules(t *testing.T) {
	tr := newTracker()
	var st Store
	destructive := happyOp(tr, func() Store { return st })
	destructive.verb = "decommission"
	destructive.destructive = true
	asks := happyOp(tr, func() Store { return st })
	asks.verb = "restart"
	asks.confirm = true

	e, store, _ := newTestEngine(t, fakeRegistry{"decommission": destructive, "restart": asks}, nil)
	st = store
	ctx := context.Background()

	plan, _, err := e.Submit(ctx, func() Request { r := req("decommission", "dev", ""); r.DryRun = true; return r }())
	if err != nil {
		t.Fatal(err)
	}
	if !plan.RequiresConfirm || string(plan.ConfirmValue) != "dev" {
		t.Fatalf("destructive plan confirm = %v/%q, want true/dev", plan.RequiresConfirm, plan.ConfirmValue)
	}
	if _, _, err := e.Submit(ctx, req("decommission", "dev", "d1")); !errors.Is(err, ErrConfirmRequired) {
		t.Fatalf("no confirm = %v, want ErrConfirmRequired", err)
	}
	wrong := req("decommission", "dev", "d1")
	wrong.Confirm = "yes"
	if _, _, err := e.Submit(ctx, wrong); !errors.Is(err, ErrConfirmRequired) {
		t.Fatalf(`confirm="yes" on a destructive op = %v, want ErrConfirmRequired`, err)
	}
	right := req("decommission", "dev", "d1")
	right.Confirm = "dev"
	_, job, err := e.Submit(ctx, right)
	if err != nil {
		t.Fatalf("confirm=dev: %v", err)
	}
	waitFor(t, e, job.ID, model.JobSucceeded)

	plan, _, err = e.Submit(ctx, func() Request { r := req("restart", "dev", ""); r.DryRun = true; return r }())
	if err != nil {
		t.Fatal(err)
	}
	if !plan.RequiresConfirm || string(plan.ConfirmValue) != "yes" {
		t.Fatalf("non-destructive confirm value = %q, want yes", plan.ConfirmValue)
	}

	// The refusal is audited as a single intent row.
	rows, _, _ := e.Audit(ctx, 20)
	var refused int
	for _, r := range rows {
		if r.Outcome == "refused" {
			refused++
			if r.Phase != model.AuditIntent {
				t.Fatalf("a refusal was audited as %q", r.Phase)
			}
		}
	}
	if refused != 2 {
		t.Fatalf("%d refusals audited, want 2", refused)
	}
}

func TestDoctorRedRefusesAndYellowNeedsTheHash(t *testing.T) {
	tr := newTracker()
	var st Store
	status := model.StatusRed
	hash := sha256Of([]byte("findings"))
	e, store, _ := newTestEngine(t, fakeRegistry{"backup": happyOp(tr, func() Store { return st })},
		func(o *EngineOptions) {
			o.Doctor = func(ctx context.Context, tenant, op string) (model.DoctorResponse, error) {
				return model.DoctorResponse{
					Status: status, Hash: hash, GeneratedAt: "2026-09-14T10:00:00Z",
					Scope:    model.Scope{Tenant: model.NullString(tenant), Op: model.NullString(op)},
					Findings: []model.Finding{{Level: model.LevelError, Code: "es_down", Tenant: "dev", Detail: "es is down"}},
				}, nil
			}
		})
	st = store
	ctx := context.Background()

	err := func() error { _, _, err := e.Submit(ctx, req("backup", "dev", "b1")); return err }()
	if !errors.Is(err, ErrDoctorRed) {
		t.Fatalf("red doctor = %v, want ErrDoctorRed", err)
	}
	forced := req("backup", "dev", "b1")
	forced.ForceWithDoctorDiff = hash
	if _, _, err := e.Submit(ctx, forced); !errors.Is(err, ErrDoctorRed) {
		t.Fatalf("a red doctor was forced: %v", err)
	}

	status = model.StatusYellow
	err = func() error { _, _, err := e.Submit(ctx, req("backup", "dev", "b2")); return err }()
	if !errors.Is(err, ErrDoctorRed) || !strings.Contains(err.Error(), "force_with_doctor_diff="+hash) {
		t.Fatalf("yellow without the hash = %v, want the remedy in the message", err)
	}
	ok := req("backup", "dev", "b3")
	ok.ForceWithDoctorDiff = hash
	_, job, err := e.Submit(ctx, ok)
	if err != nil {
		t.Fatalf("yellow with the hash: %v", err)
	}
	waitFor(t, e, job.ID, model.JobSucceeded)

	stale := req("backup", "dev", "b4")
	stale.ForceWithDoctorDiff = sha256Of([]byte("an older finding set"))
	if _, _, err := e.Submit(ctx, stale); !errors.Is(err, ErrDoctorRed) {
		t.Fatalf("yellow with a STALE hash = %v, want ErrDoctorRed", err)
	}
}

// TestPlanStaleWhenTheRegistryMovesUnderTheLocks: the plan an operator
// approved is recomputed after the locks; a registry that moved in between is
// refused rather than executed.
func TestPlanStaleWhenTheRegistryMovesUnderTheLocks(t *testing.T) {
	tr := newTracker()
	var st Store
	calls := 0
	e, store, _ := newTestEngine(t, fakeRegistry{"backup": happyOp(tr, func() Store { return st })},
		func(o *EngineOptions) {
			o.LoadFleet = func() (*registry.Fleet, error) {
				f := testFleet()
				calls++
				if calls > 1 {
					f.Generation = 8 // someone else wrote the registry
				}
				return f, nil
			}
		})
	st = store
	ctx := context.Background()

	_, job, err := e.Submit(ctx, req("backup", "dev", "b1"))
	if err != nil {
		t.Fatalf("Submit: %v", err)
	}
	done := waitFor(t, e, job.ID, model.JobFailed, model.JobSucceeded)
	if done.State != model.JobFailed || done.Error == nil || done.Error.Code != "plan_stale" {
		t.Fatalf("job = %s %+v, want failed/plan_stale", done.State, done.Error)
	}
	if trace := tr.trace(); len(trace) != 0 {
		t.Fatalf("a stale plan executed %v", trace)
	}
	if done.Lock != nil {
		t.Fatal("the locks were not released")
	}
}

// TestLockedJobFailsNamingTheHolder: the locks are the fleet's, not the
// engine's — a job that cannot take one fails with the holder's identity.
func TestLockedJobFailsNamingTheHolder(t *testing.T) {
	tr := newTracker()
	var st Store
	e, store, roots := newTestEngine(t, fakeRegistry{"backup": happyOp(tr, func() Store { return st })}, nil)
	st = store
	ctx := context.Background()

	l := NewLocks(roots)
	held, err := l.Take([]model.LockName{model.LockTenant}, "dev",
		LockHolder{JobID: "01OTHERJOB", PID: 4242, Since: "2026-09-14T09:00:00Z"}, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	defer held.Release()

	_, job, err := e.Submit(ctx, req("backup", "dev", "b1"))
	if err != nil {
		t.Fatalf("Submit: %v", err)
	}
	done := waitFor(t, e, job.ID, model.JobFailed, model.JobSucceeded)
	if done.State != model.JobFailed || done.Error == nil || done.Error.Code != "locked" {
		t.Fatalf("job = %s %+v, want failed/locked", done.State, done.Error)
	}
	if !strings.Contains(done.Error.Detail, "01OTHERJOB") || !strings.Contains(done.Error.Detail, "4242") {
		t.Fatalf("the failure does not name the holder: %q", done.Error.Detail)
	}
}

// ---------------------------------------------------------------- failure

func TestStepFailureRollsBackInReverse(t *testing.T) {
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
	ctx := context.Background()

	_, job, err := e.Submit(ctx, req("backup", "dev", "b1"))
	if err != nil {
		t.Fatal(err)
	}
	done := waitFor(t, e, job.ID, model.JobRolledBack, model.JobFailed, model.JobSucceeded)
	if done.State != model.JobRolledBack {
		t.Fatalf("job = %s %+v, want rolled_back", done.State, done.Error)
	}
	want := []string{"run:1", "run:2", "driver:2", "run:3", "rollback:2", "rollback:1"}
	if got := tr.trace(); !reflect.DeepEqual(got, want) {
		t.Fatalf("trace = %v, want %v (rollback newest-first over the SUCCEEDED steps)", got, want)
	}
	if done.Error.Code != "step_failed" || *done.Error.Step != 3 {
		t.Fatalf("error = %+v", done.Error)
	}
	if !strings.Contains(done.Error.Detail, "no space left") {
		t.Fatalf("error detail = %q", done.Error.Detail)
	}
	if done.Rollback == nil || done.Rollback.State != model.RollbackSucceeded || !done.Rollback.Attempted {
		t.Fatalf("rollback = %+v", done.Rollback)
	}
	if done.Steps[0].State != model.StepRolledBack || done.Steps[1].State != model.StepRolledBack {
		t.Fatalf("step states = %s %s", done.Steps[0].State, done.Steps[1].State)
	}
	if done.Steps[2].State != model.StepFailed {
		t.Fatalf("the failing step = %s", done.Steps[2].State)
	}
}

// ---------------------------------------------------------------- cutover

func TestCutoverParksTheJobAndContinueCompletesIt(t *testing.T) {
	tr := newTracker()
	var st Store
	op := happyOp(tr, func() Store { return st })
	op.verb = "handover"
	inner := op.planFn
	op.planFn = func(oc Context, args map[string]any) *Planned {
		p := inner(oc, args)
		p.Steps[1].Cutover = true
		return p
	}
	e, store, roots := newTestEngine(t, fakeRegistry{"handover": op}, nil)
	st = store
	ctx := context.Background()

	_, job, err := e.Submit(ctx, req("handover", "dev", "h1"))
	if err != nil {
		t.Fatal(err)
	}
	parked := waitFor(t, e, job.ID, model.JobAwaitingCutover, model.JobFailed)
	if parked.State != model.JobAwaitingCutover {
		t.Fatalf("job = %s %+v", parked.State, parked.Error)
	}
	if got := tr.trace(); len(got) != 3 {
		t.Fatalf("trace at the cutover = %v, want the first two steps only", got)
	}
	// The locks are KEPT while parked: nothing else may touch this tenant.
	l := NewLocks(roots)
	if _, err := l.Take([]model.LockName{model.LockTenant}, "dev", LockHolder{}, time.Now()); !errors.Is(err, ErrLocked) {
		t.Fatalf("a parked job released its tenant lock: %v", err)
	}
	if parked.Lock == nil || len(parked.Lock.Order) != 2 {
		t.Fatalf("parked lock = %+v", parked.Lock)
	}

	if _, err := e.Continue(ctx, job.ID, Principal{Subject: "bvbrc:x", Role: "viewer"}); !errors.Is(err, ErrForbidden) {
		t.Fatalf("Continue as a viewer = %v, want ErrForbidden", err)
	}
	if _, err := e.Continue(ctx, job.ID, operator()); err != nil {
		t.Fatalf("Continue: %v", err)
	}
	done := waitFor(t, e, job.ID, model.JobSucceeded, model.JobFailed)
	if done.State != model.JobSucceeded {
		t.Fatalf("after Continue: %s %+v", done.State, done.Error)
	}
	want := []string{"run:1", "run:2", "driver:2", "run:3"}
	if got := tr.trace(); !reflect.DeepEqual(got, want) {
		t.Fatalf("trace = %v, want %v (step 2 must not be repeated)", got, want)
	}
	if _, err := e.Continue(ctx, job.ID, operator()); !errors.Is(err, ErrRefused) {
		t.Fatalf("Continue on a finished job = %v, want ErrRefused", err)
	}
}

// ---------------------------------------------------------------- cancel

func TestCancelDuringASlowStep(t *testing.T) {
	tr := newTracker()
	var st Store
	entered := make(chan struct{})
	op := happyOp(tr, func() Store { return st })
	inner := op.planFn
	op.planFn = func(oc Context, args map[string]any) *Planned {
		p := inner(oc, args)
		p.Steps[2].Run = func(ctx context.Context, sc *StepContext) (string, error) {
			tr.log("run:3")
			select {
			case entered <- struct{}{}:
			default:
			}
			<-ctx.Done() // cooperative: the step watches the context
			return "", ctx.Err()
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
	select {
	case <-entered:
	case <-time.After(10 * time.Second):
		t.Fatal("the slow step never started")
	}
	if _, err := e.Cancel(ctx, job.ID, operator(), "yes"); err != nil {
		t.Fatalf("Cancel: %v", err)
	}
	done := waitFor(t, e, job.ID, model.JobCancelled, model.JobFailed, model.JobSucceeded)
	if done.State != model.JobCancelled {
		t.Fatalf("job = %s %+v, want cancelled", done.State, done.Error)
	}
	want := []string{"run:1", "run:2", "driver:2", "run:3", "rollback:2", "rollback:1"}
	if got := tr.trace(); !reflect.DeepEqual(got, want) {
		t.Fatalf("trace = %v, want %v", got, want)
	}
	if _, err := e.Cancel(ctx, job.ID, operator(), "yes"); !errors.Is(err, ErrRefused) {
		t.Fatalf("cancelling a terminal job = %v, want ErrRefused", err)
	}
}

// ---------------------------------------------------------------- secrets

func mintingOp(secret string) *fakeOp {
	return &fakeOp{
		verb:  "key-mint",
		locks: []model.LockName{model.LockTenant},
		planFn: func(oc Context, args map[string]any) *Planned {
			p := buildPlanned([]Step{{
				Plan: plannedStep(1, "registry", "record the new key", "dev"),
				Run: func(ctx context.Context, sc *StepContext) (string, error) {
					// A step log that MENTIONS the secret: the redactor must
					// catch it on the way into the store.
					sc.Logf("minted key ops-2 with value %s", secret)
					return "", nil
				},
			}})
			p.Result = func() map[string]any { return map[string]any{"id": "ops-2", "secrets_available": true} }
			p.Secrets = func() []model.Secret {
				return []model.Secret{{ID: "ops-2", Label: "ops", Role: "admin", Value: secret}}
			}
			return p
		},
	}
}

func TestSecretsAreDeliveredOnceToTheMintingPrincipal(t *testing.T) {
	secret := strings.Repeat("ab", 32)
	e, _, _ := newTestEngine(t, fakeRegistry{"key-mint": mintingOp(secret)}, func(o *EngineOptions) {
		o.Redactor = testRedactor{secret: secret}
	})
	ctx := context.Background()

	_, job, err := e.Submit(ctx, req("key-mint", "dev", "m1"))
	if err != nil {
		t.Fatal(err)
	}
	done := waitFor(t, e, job.ID, model.JobSucceeded, model.JobFailed)
	if done.State != model.JobSucceeded {
		t.Fatalf("job = %s %+v", done.State, done.Error)
	}

	// The value is nowhere but the envelope.
	log, _ := e.StepLog(ctx, job.ID, 1)
	if strings.Contains(log, secret) || !strings.Contains(log, "<REDACTED>") {
		t.Fatalf("the secret reached the step log: %q", log)
	}
	if strings.Contains(fmt.Sprint(done.Result), secret) {
		t.Fatalf("the secret reached the job result: %+v", done.Result)
	}
	rows, _, _ := e.Audit(ctx, 10)
	if strings.Contains(fmt.Sprint(rows), secret) {
		t.Fatal("the secret reached the audit log")
	}

	if _, err := e.Secrets(ctx, job.ID, Principal{Subject: "key:other", Role: "operator"}); !errors.Is(err, ErrForbidden) {
		t.Fatalf("another operator = %v, want ErrForbidden", err)
	}
	session := operator()
	session.FromSession = true
	if _, err := e.Secrets(ctx, job.ID, session); !errors.Is(err, ErrForbidden) {
		t.Fatalf("over a session = %v, want ErrForbidden", err)
	}
	got, err := e.Secrets(ctx, job.ID, operator())
	if err != nil {
		t.Fatalf("Secrets: %v", err)
	}
	if len(got.Secrets) != 1 || got.Secrets[0].Value != secret || got.JobID != job.ID {
		t.Fatalf("delivered = %+v", got)
	}
	if _, err := e.Secrets(ctx, job.ID, operator()); !errors.Is(err, ErrGone) {
		t.Fatalf("the second read = %v, want ErrGone", err)
	}
}

func TestSecretsExpireAfterTheTTL(t *testing.T) {
	secret := strings.Repeat("cd", 32)
	now := time.Date(2026, 9, 14, 10, 0, 0, 0, time.UTC)
	clock := now
	e, _, _ := newTestEngine(t, fakeRegistry{"key-mint": mintingOp(secret)}, func(o *EngineOptions) {
		o.Now = func() time.Time { return clock }
		o.SecretsTTL = 15 * time.Minute
	})
	ctx := context.Background()
	_, job, err := e.Submit(ctx, req("key-mint", "dev", "m1"))
	if err != nil {
		t.Fatal(err)
	}
	waitFor(t, e, job.ID, model.JobSucceeded)

	clock = now.Add(16 * time.Minute)
	if _, err := e.Secrets(ctx, job.ID, operator()); !errors.Is(err, ErrGone) {
		t.Fatalf("an expired envelope = %v, want ErrGone", err)
	}
}

// TestSealedSecretsNeedAnUnsealer: with a Sealer configured the ciphertext is
// what the database holds, and only an Unsealer can turn it back into values.
func TestSealedSecretsNeedAnUnsealer(t *testing.T) {
	secret := strings.Repeat("ef", 32)
	reg := fakeRegistry{"key-mint": mintingOp(secret)}
	seal := func(b []byte) ([]byte, bool) { return append([]byte("SEALED:"), b...), true }

	e, store, _ := newTestEngine(t, reg, func(o *EngineOptions) { o.Sealer = seal })
	ctx := context.Background()
	_, job, err := e.Submit(ctx, req("key-mint", "dev", "m1"))
	if err != nil {
		t.Fatal(err)
	}
	waitFor(t, e, job.ID, model.JobSucceeded)
	if _, err := e.Secrets(ctx, job.ID, operator()); !errors.Is(err, ErrRefused) {
		t.Fatalf("a sealed envelope without an unsealer = %v, want ErrRefused", err)
	}
	_ = store

	e2, _, _ := newTestEngine(t, reg, func(o *EngineOptions) {
		o.Sealer = seal
		o.Unsealer = func(b []byte) ([]byte, error) {
			return b[len("SEALED:"):], nil
		}
	})
	_, job2, err := e2.Submit(ctx, req("key-mint", "dev", "m1"))
	if err != nil {
		t.Fatal(err)
	}
	waitFor(t, e2, job2.ID, model.JobSucceeded)
	got, err := e2.Secrets(ctx, job2.ID, operator())
	if err != nil || len(got.Secrets) != 1 || got.Secrets[0].Value != secret {
		t.Fatalf("Secrets = %+v, %v", got, err)
	}
}

// ---------------------------------------------------------------- reconcile

// TestReconcileInterruptsADeadWorkerAndResumeFinishesIt is the crash story
// without a crash: a `running` job whose worker pid is gone. Reconcile must
// interrupt it, KEEP its reservations, and resume nothing by itself.
func TestReconcileInterruptsADeadWorkerAndResumeFinishesIt(t *testing.T) {
	tr := newTracker()
	var st Store
	e, store, _ := newTestEngine(t, fakeRegistry{"backup": happyOp(tr, func() Store { return st })}, nil)
	st = store
	ctx := context.Background()

	// The plan hash the op produces right now — a resumed job must re-plan
	// to exactly this.
	dry := req("backup", "dev", "")
	dry.DryRun = true
	plan, _, err := e.Submit(ctx, dry)
	if err != nil {
		t.Fatal(err)
	}

	var gen ulidGen
	job := &model.Job{
		ID: gen.new(time.Now()), Op: "backup", Tenant: "dev", Principal: "key:ops",
		AuthMethod: model.AuthAPIKey, State: model.JobRunning, PlanHash: plan.PlanHash,
		RequestID: "0123456789abcdef", IdempotencyKey: "b1",
		CreatedAt: time.Now().UTC().Format(time.RFC3339),
		Worker:    &model.JobWorker{PID: 0x7FFFFFFE, Host: "testhost", Mode: model.WorkerDirect},
		Lock:      &model.JobLock{Order: []model.LockName{model.LockRegistry, model.LockTenant}, Since: "2026-09-14T10:00:00Z"},
		Reservations: []model.Reservation{
			{Resource: "dir:/rag/backups/tenants/dev/20260914T100000Z-backup"},
		},
		Steps: []model.Step{
			{N: 1, Kind: "probe", Title: "check the tenant is quiet", State: model.StepSucceeded, Attempts: 1, ExternalIDs: []string{}},
			{N: 2, Kind: "qdrant", Title: "snapshot the collections", State: model.StepRunning, Attempts: 1,
				Checkpoint: true, ExternalIDs: []string{"snap-20260914T100000Z"}},
			{N: 3, Kind: "tar", Title: "write the bundle", State: model.StepPending, ExternalIDs: []string{}},
		},
	}
	if _, err := store.Create(ctx, job, "fp"); err != nil {
		t.Fatal(err)
	}
	// The redacted args the submission would have remembered: a resume
	// re-plans from them.
	if err := store.(ArgsStore).PutArgs(ctx, job.ID, map[string]any{"fence": true}); err != nil {
		t.Fatal(err)
	}

	ids, err := e.Reconcile(ctx)
	if err != nil || len(ids) != 1 || ids[0] != job.ID {
		t.Fatalf("Reconcile = %v, %v", ids, err)
	}
	got, _ := e.Get(ctx, job.ID)
	if got.State != model.JobInterrupted {
		t.Fatalf("state = %s, want interrupted", got.State)
	}
	if got.Steps[1].State != model.StepInterrupted {
		t.Fatalf("the in-flight step = %s, want interrupted", got.Steps[1].State)
	}
	if len(got.Reservations) != 1 {
		t.Fatalf("reconcile dropped the reservations: %+v", got.Reservations)
	}
	if got.Worker != nil || got.Lock != nil {
		t.Fatal("reconcile kept the dead worker's claim on the locks")
	}
	if trace := tr.trace(); len(trace) != 0 {
		t.Fatalf("reconcile RAN something: %v", trace)
	}

	if _, err := e.Resume(ctx, job.ID, Principal{Subject: "x", Role: "viewer"}); !errors.Is(err, ErrForbidden) {
		t.Fatalf("Resume as a viewer = %v, want ErrForbidden", err)
	}
	if _, err := e.Resume(ctx, job.ID, operator()); err != nil {
		t.Fatalf("Resume: %v", err)
	}
	done := waitFor(t, e, job.ID, model.JobSucceeded, model.JobFailed)
	if done.State != model.JobSucceeded {
		t.Fatalf("after Resume: %s %+v", done.State, done.Error)
	}
	// Step 2 reconciled as `done` (its external id exists), so only step 3 ran.
	if got := tr.trace(); !reflect.DeepEqual(got, []string{"run:3"}) {
		t.Fatalf("trace = %v, want only step 3 (step 2 was already done)", got)
	}
	if _, err := e.Resume(ctx, job.ID, operator()); !errors.Is(err, ErrRefused) {
		t.Fatalf("Resume of a succeeded job = %v, want ErrRefused", err)
	}
}

// TestResumeStaysInterruptedWhenReconcileIsStuck: an undecidable step is not
// guessed at — the job stays interrupted and says why.
func TestResumeStaysInterruptedWhenReconcileIsStuck(t *testing.T) {
	tr := newTracker()
	var st Store
	op := happyOp(tr, func() Store { return st })
	inner := op.planFn
	op.planFn = func(oc Context, args map[string]any) *Planned {
		p := inner(oc, args)
		p.Steps[1].Reconcile = func(ctx context.Context, sc *StepContext) (Reconciliation, error) {
			return ReconcileStuck, nil
		}
		return p
	}
	e, store, roots := newTestEngine(t, fakeRegistry{"backup": op}, nil)
	st = store
	ctx := context.Background()

	dry := req("backup", "dev", "")
	dry.DryRun = true
	plan, _, _ := e.Submit(ctx, dry)

	var gen ulidGen
	job := &model.Job{
		ID: gen.new(time.Now()), Op: "backup", Tenant: "dev", Principal: "key:ops",
		AuthMethod: model.AuthAPIKey, State: model.JobInterrupted, PlanHash: plan.PlanHash,
		RequestID: "0123456789abcdef", IdempotencyKey: "b1",
		CreatedAt: time.Now().UTC().Format(time.RFC3339), Reservations: []model.Reservation{},
		Steps: []model.Step{
			{N: 1, Kind: "probe", Title: "check the tenant is quiet", State: model.StepSucceeded, ExternalIDs: []string{}},
			{N: 2, Kind: "qdrant", Title: "snapshot the collections", State: model.StepInterrupted, ExternalIDs: []string{"snap-x"}},
			{N: 3, Kind: "tar", Title: "write the bundle", State: model.StepPending, ExternalIDs: []string{}},
		},
	}
	if _, err := store.Create(ctx, job, "fp"); err != nil {
		t.Fatal(err)
	}
	// The redacted args the submission would have remembered: a resume
	// re-plans from them.
	if err := store.(ArgsStore).PutArgs(ctx, job.ID, map[string]any{"fence": true}); err != nil {
		t.Fatal(err)
	}
	got, err := e.Resume(ctx, job.ID, operator())
	if !errors.Is(err, ErrRefused) || !strings.Contains(err.Error(), "snap-x") {
		t.Fatalf("Resume of a stuck step = %v, want ErrRefused naming the external ids", err)
	}
	if got.State != model.JobInterrupted {
		t.Fatalf("state = %s, want interrupted", got.State)
	}
	// And the locks it took to reconcile were given back.
	l := NewLocks(roots)
	set, err := l.Take([]model.LockName{model.LockTenant, model.LockRegistry}, "dev", LockHolder{}, time.Now())
	if err != nil {
		t.Fatalf("a stuck resume kept the locks: %v", err)
	}
	set.Release()
}

// ---------------------------------------------------------------- refusals

// seedInterrupted writes an interrupted job whose plan hash is the one the
// registered op produces right now — the shape Resume expects to find.
func seedInterrupted(t *testing.T, e *engine, store Store, planHash string) *model.Job {
	t.Helper()
	var gen ulidGen
	job := &model.Job{
		ID: gen.new(time.Now()), Op: "backup", Tenant: "dev", Principal: "key:ops",
		AuthMethod: model.AuthAPIKey, State: model.JobInterrupted, PlanHash: planHash,
		RequestID: "0123456789abcdef", IdempotencyKey: "seeded",
		CreatedAt: time.Now().UTC().Format(time.RFC3339), Reservations: []model.Reservation{},
		Steps: []model.Step{
			{N: 1, Kind: "probe", Title: "check the tenant is quiet", State: model.StepSucceeded, ExternalIDs: []string{}},
			{N: 2, Kind: "qdrant", Title: "snapshot the collections", State: model.StepInterrupted, ExternalIDs: []string{"snap-x"}},
			{N: 3, Kind: "tar", Title: "write the bundle", State: model.StepPending, ExternalIDs: []string{}},
		},
	}
	if _, err := store.Create(context.Background(), job, "fp"); err != nil {
		t.Fatal(err)
	}
	if err := store.(ArgsStore).PutArgs(context.Background(), job.ID, map[string]any{"fence": true}); err != nil {
		t.Fatal(err)
	}
	return job
}

// TestRefusalsCarryTheContractsExtra: every refusal the API answers with a
// 409/428 carries the `extra` object that makes it actionable. The holder of
// a lock and the id of the job a key already named exist only in here.
func TestRefusalsCarryTheContractsExtra(t *testing.T) {
	ctx := context.Background()

	t.Run("duplicate names the original job", func(t *testing.T) {
		tr := newTracker()
		var st Store
		e, store, _ := newTestEngine(t, fakeRegistry{"backup": happyOp(tr, func() Store { return st })}, nil)
		st = store
		_, first, err := e.Submit(ctx, req("backup", "dev", "b1"))
		if err != nil {
			t.Fatal(err)
		}
		waitFor(t, e, first.ID, model.JobSucceeded)

		other := req("backup", "dev", "b1")
		other.Args = map[string]any{"fence": false}
		plan, _, err := e.Submit(ctx, other)
		if !errors.Is(err, ErrDuplicate) {
			t.Fatalf("err = %v, want ErrDuplicate", err)
		}
		if plan == nil {
			t.Fatal("a duplicate refusal did not carry the plan")
		}
		extra := ErrorExtra(err)
		if extra["job_id"] != first.ID {
			t.Fatalf("extra = %+v, want job_id %s", extra, first.ID)
		}
	})

	t.Run("locked names the holder", func(t *testing.T) {
		tr := newTracker()
		var st Store
		e, store, roots := newTestEngine(t, fakeRegistry{"backup": happyOp(tr, func() Store { return st })}, nil)
		st = store
		dry := req("backup", "dev", "")
		dry.DryRun = true
		plan, _, err := e.Submit(ctx, dry)
		if err != nil {
			t.Fatal(err)
		}
		job := seedInterrupted(t, e, store, plan.PlanHash)

		held, err := NewLocks(roots).Take([]model.LockName{model.LockTenant}, "dev",
			LockHolder{JobID: "01OTHERJOB", PID: 4242, Since: "2026-09-14T09:00:00Z"}, time.Now())
		if err != nil {
			t.Fatal(err)
		}
		defer held.Release()

		_, err = e.Resume(ctx, job.ID, operator())
		if !errors.Is(err, ErrLocked) {
			t.Fatalf("err = %v, want ErrLocked", err)
		}
		extra := ErrorExtra(err)
		if extra["job_id"] != "01OTHERJOB" || extra["pid"] != 4242 ||
			extra["since"] != "2026-09-14T09:00:00Z" || extra["lock"] != string(model.LockTenant) {
			t.Fatalf("extra = %+v", extra)
		}
	})

	t.Run("plan_stale names the new hash and generation", func(t *testing.T) {
		tr := newTracker()
		var st Store
		gen := int64(7)
		e, store, _ := newTestEngine(t, fakeRegistry{"backup": happyOp(tr, func() Store { return st })},
			func(o *EngineOptions) {
				o.LoadFleet = func() (*registry.Fleet, error) {
					f := testFleet()
					f.Generation = gen
					return f, nil
				}
			})
		st = store
		dry := req("backup", "dev", "")
		dry.DryRun = true
		plan, _, err := e.Submit(ctx, dry)
		if err != nil {
			t.Fatal(err)
		}
		job := seedInterrupted(t, e, store, plan.PlanHash)
		gen = 9 // the registry moved while the job was interrupted

		if _, err := e.Resume(ctx, job.ID, operator()); !errors.Is(err, ErrPlanStale) {
			t.Fatalf("err = %v, want ErrPlanStale", err)
		} else {
			extra := ErrorExtra(err)
			if extra["registry_generation"] != int64(9) || extra["plan_hash"] == plan.PlanHash {
				t.Fatalf("extra = %+v", extra)
			}
		}
	})

	t.Run("confirm_required and doctor_red name their values", func(t *testing.T) {
		tr := newTracker()
		var st Store
		status := model.StatusRed
		hash := sha256Of([]byte("findings"))
		op := happyOp(tr, func() Store { return st })
		op.verb = "decommission"
		op.destructive = true
		e, store, _ := newTestEngine(t, fakeRegistry{"decommission": op}, func(o *EngineOptions) {
			o.Doctor = func(ctx context.Context, tenant, opName string) (model.DoctorResponse, error) {
				return model.DoctorResponse{
					Status: status, Hash: hash, GeneratedAt: "2026-09-14T10:00:00Z",
					Scope:    model.Scope{Tenant: model.NullString(tenant), Op: model.NullString(opName)},
					Findings: []model.Finding{},
				}, nil
			}
		})
		st = store

		plan, _, err := e.Submit(ctx, req("decommission", "dev", "d1"))
		if !errors.Is(err, ErrConfirmRequired) {
			t.Fatalf("err = %v, want ErrConfirmRequired", err)
		}
		if plan == nil || plan.PlanHash == "" {
			t.Fatal("a confirm_required refusal did not carry the plan")
		}
		if extra := ErrorExtra(err); extra["confirm_value"] != "dev" {
			t.Fatalf("extra = %+v", extra)
		}

		confirmed := req("decommission", "dev", "d1")
		confirmed.Confirm = "dev"
		plan, _, err = e.Submit(ctx, confirmed)
		if !errors.Is(err, ErrDoctorRed) {
			t.Fatalf("err = %v, want ErrDoctorRed", err)
		}
		if plan == nil {
			t.Fatal("a doctor_red refusal did not carry the plan")
		}
		extra := ErrorExtra(err)
		if extra["doctor_hash"] != hash || extra["status"] != "red" {
			t.Fatalf("extra = %+v", extra)
		}

		status = model.StatusYellow
		_, _, err = e.Submit(ctx, confirmed)
		if !errors.Is(err, ErrDoctorRed) {
			t.Fatalf("err = %v, want ErrDoctorRed", err)
		}
		if extra := ErrorExtra(err); extra["status"] != "yellow" || extra["doctor_hash"] != hash {
			t.Fatalf("extra = %+v", extra)
		}
	})
}
