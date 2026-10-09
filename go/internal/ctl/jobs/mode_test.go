package jobs

import (
	"context"
	"errors"
	"fmt"
	"reflect"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// #714, layer (a): the Context an Op plans against carries the ENGINE's mode,
// never the request's — on a submission AND on the re-plan a continuation
// runs. A planner that refuses unless the engine is a --direct one therefore
// refuses a daemon-side resume of a job a --direct run left interrupted, so
// that job's steps never run in the daemon's account.
func TestAContinuationRePlansUnderTheEnginesOwnMode(t *testing.T) {
	ctx := context.Background()
	tr := newTracker()
	var st Store
	op := happyOp(tr, func() Store { return st })
	var seen []model.WorkerMode
	op.planErr = func(oc Context) error {
		seen = append(seen, oc.Mode)
		if oc.Mode != model.WorkerDirect {
			return fmt.Errorf("%w: direct-only op planned in %q mode", ErrRefused, oc.Mode)
		}
		return nil
	}
	reg := fakeRegistry{"backup": op}
	daemon, store, roots := newTestEngine(t, reg, func(o *EngineOptions) { o.Mode = model.WorkerDaemon })
	st = store
	directEng := NewEngine(EngineOptions{
		Store: store, Ops: reg, Roots: roots, Host: "testhost", Mode: model.WorkerDirect,
		LoadFleet: func() (*registry.Fleet, error) { return testFleet(), nil },
	}).(*engine)

	// The REQUEST says direct (req() does); the daemon engine plans as daemon.
	dry := req("backup", "dev", "")
	dry.DryRun = true
	if _, _, err := daemon.Submit(ctx, dry); !errors.Is(err, ErrRefused) {
		t.Fatalf("the daemon planned a direct-only op because the request claimed direct: %v", err)
	}
	plan, _, err := directEng.Submit(ctx, dry)
	if err != nil {
		t.Fatalf("the direct engine: %v", err)
	}
	if want := []model.WorkerMode{model.WorkerDaemon, model.WorkerDirect}; !reflect.DeepEqual(seen, want) {
		t.Fatalf("modes seen by the planner = %v, want %v", seen, want)
	}

	// A job a --direct worker was running when it died.
	var gen ulidGen
	job := &model.Job{
		ID: gen.new(time.Now()), Op: "backup", Tenant: "dev", Principal: "local:1000",
		AuthMethod: model.AuthLocal, State: model.JobRunning, PlanHash: plan.PlanHash,
		RequestID: "0123456789abcdef", IdempotencyKey: "m1",
		CreatedAt: time.Now().UTC().Format(time.RFC3339),
		Worker:    &model.JobWorker{PID: 0x7FFFFFFE, Host: "testhost", Mode: model.WorkerDirect},
		Lock:      &model.JobLock{Order: []model.LockName{model.LockRegistry, model.LockTenant}, Since: "2026-09-14T10:00:00Z"},
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
	if err := store.(ArgsStore).PutArgs(ctx, job.ID, map[string]any{"fence": true}); err != nil {
		t.Fatal(err)
	}
	if ids, err := daemon.Reconcile(ctx); err != nil || len(ids) != 1 {
		t.Fatalf("Reconcile = %v, %v", ids, err)
	}

	// The daemon's resume and cancel re-plan as daemon and are refused;
	// nothing ran and the job is still waiting for the CLI.
	if _, err := daemon.Resume(ctx, job.ID, operator()); !errors.Is(err, ErrRefused) {
		t.Fatalf("daemon Resume = %v, want the planner's refusal", err)
	}
	if got, _ := daemon.Get(ctx, job.ID); got.State != model.JobInterrupted {
		t.Fatalf("after the refused resume the job is %s, want interrupted", got.State)
	}
	if trace := tr.trace(); len(trace) != 0 {
		t.Fatalf("a refused resume RAN something: %v", trace)
	}

	// The --direct engine resumes it.
	if _, err := directEng.Resume(ctx, job.ID, operator()); err != nil {
		t.Fatalf("direct Resume: %v", err)
	}
	if done := waitFor(t, directEng, job.ID, model.JobSucceeded, model.JobFailed); done.State != model.JobSucceeded {
		t.Fatalf("after the direct resume: %s %+v", done.State, done.Error)
	}
}
