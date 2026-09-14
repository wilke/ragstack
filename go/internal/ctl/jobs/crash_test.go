package jobs

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"os/exec"
	"reflect"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// The crash test. Everything else in this package is one process deciding
// things about itself; this one is a CHILD process running a job, being
// SIGKILLed in the middle of a step, and the parent reconciling and finishing
// the job off the same jobs.db. No in-process state survives a kill -9, so it
// is the only test that can tell whether the durability rules actually work.

// crashOp is the plan both processes build. The PLAN is identical in each
// (which is what lets the parent re-plan the child's job and get the same
// hash); only the closures differ, and closures are not hashed.
//
// block says whether step 2 waits forever after its checkpoint — the child
// does, the parent must never re-run it at all.
func crashOp(tr *tracker, block bool) *fakeOp {
	return &fakeOp{
		verb:  "backup",
		locks: []model.LockName{model.LockTenant, model.LockRegistry},
		planFn: func(oc Context, args map[string]any) *Planned {
			return buildPlanned([]Step{{
				Plan: plannedStep(1, "probe", "check the tenant is quiet", "dev"),
				Run: func(ctx context.Context, sc *StepContext) (string, error) {
					tr.log("run:1")
					return "", sc.Reserve("port:24040", nil)
				},
			}, {
				Plan: plannedStep(2, "qdrant", "snapshot the collections", "dev"),
				Run: func(ctx context.Context, sc *StepContext) (string, error) {
					tr.log("run:2")
					if err := sc.Checkpoint("snap-20260914T100000Z"); err != nil {
						return "", err
					}
					if !block {
						return "", fmt.Errorf("step 2 was re-run although its work was already done")
					}
					// The external call that never returns: the process is
					// killed while the snapshot is in flight.
					<-time.After(5 * time.Minute)
					return "", nil
				},
				Reconcile: func(ctx context.Context, sc *StepContext) (Reconciliation, error) {
					// The real one asks the store "does this snapshot exist?".
					if len(sc.Step.ExternalIDs) > 0 {
						return ReconcileDone, nil
					}
					return ReconcileRedo, nil
				},
			}, {
				Plan: plannedStep(3, "tar", "write the bundle", "dev"),
				Run: func(ctx context.Context, sc *StepContext) (string, error) {
					tr.log("run:3")
					sc.Logf("bundle written")
					return "", nil
				},
			}})
		},
	}
}

const (
	helperEnv = "RAGSTACK_JOBS_HELPER"
	helperDir = "RAGSTACK_JOBS_HELPER_DIR"
)

// TestHelperProcess is not a test: re-executed with RAGSTACK_JOBS_HELPER=1 it
// is the worker process the crash test kills.
func TestHelperProcess(t *testing.T) {
	if os.Getenv(helperEnv) != "1" {
		t.Skip("not the helper process")
	}
	roots := testRoots(os.Getenv(helperDir))
	st, err := NewStore(DefaultStorePath(roots.CtlStateDir))
	if err != nil {
		t.Fatalf("helper: NewStore: %v", err)
	}
	e := NewEngine(EngineOptions{
		Store:     st,
		Ops:       fakeRegistry{"backup": crashOp(newTracker(), true)},
		Roots:     roots,
		LoadFleet: func() (*registry.Fleet, error) { return testFleet(), nil },
		Host:      "testhost",
		Mode:      model.WorkerDirect,
	})
	if _, _, err := e.Submit(context.Background(), req("backup", "dev", "crash-1")); err != nil {
		t.Fatalf("helper: Submit: %v", err)
	}
	// Wait to be killed. A sleep rather than a blocking channel: a process
	// whose every goroutine is parked trips the runtime's deadlock detector.
	time.Sleep(5 * time.Minute)
}

func helperCommand(t *testing.T, dir string) (*exec.Cmd, *bytes.Buffer) {
	t.Helper()
	cmd := exec.Command(os.Args[0], "-test.run=^TestHelperProcess$", "-test.timeout=10m")
	cmd.Env = append(os.Environ(), helperEnv+"=1", helperDir+"="+dir)
	var out bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &out
	return cmd, &out
}

// TestKillDuringJobLeavesAResumableJob: kill -9 the worker mid-step, then
// reconcile and resume from the database alone.
func TestKillDuringJobLeavesAResumableJob(t *testing.T) {
	dir := t.TempDir()
	roots := testRoots(dir)

	cmd, out := helperCommand(t, dir)
	if err := cmd.Start(); err != nil {
		t.Fatalf("starting the helper: %v", err)
	}
	killed := false
	t.Cleanup(func() {
		if !killed {
			_ = cmd.Process.Kill()
			_, _ = cmd.Process.Wait()
		}
	})

	st, err := NewStore(DefaultStorePath(roots.CtlStateDir))
	if err != nil {
		t.Fatalf("opening the worker's store: %v", err)
	}
	defer func() { _ = st.Close() }()
	ctx := context.Background()

	// Wait until step 2's checkpoint is durable and the step is still running.
	var child model.Job
	deadline := time.Now().Add(90 * time.Second)
	for {
		if time.Now().After(deadline) {
			t.Fatalf("the worker never reached step 2's checkpoint; its output was:\n%s", out.String())
		}
		jobs, _, err := st.List(ctx, ListFilter{})
		if err == nil && len(jobs) == 1 && len(jobs[0].Steps) == 3 &&
			jobs[0].State == model.JobRunning && jobs[0].Steps[1].Checkpoint {
			child = jobs[0]
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if child.Worker == nil || child.Worker.PID != cmd.Process.Pid {
		t.Fatalf("the job's worker is %+v, want pid %d", child.Worker, cmd.Process.Pid)
	}
	if len(child.Reservations) != 1 || child.Reservations[0].Resource != "port:24040" {
		t.Fatalf("the running job's reservations = %+v", child.Reservations)
	}

	if err := cmd.Process.Signal(syscall.SIGKILL); err != nil {
		t.Fatalf("kill -9: %v", err)
	}
	_, _ = cmd.Process.Wait()
	killed = true

	// The kernel released the worker's flocks when it died.
	l := NewLocks(roots)
	set, err := l.Take([]model.LockName{model.LockTenant, model.LockRegistry}, "dev", LockHolder{}, time.Now())
	if err != nil {
		t.Fatalf("a killed worker left its locks held: %v", err)
	}
	set.Release()

	// This process is the daemon starting up.
	tr := newTracker()
	e := NewEngine(EngineOptions{
		Store:     st,
		Ops:       fakeRegistry{"backup": crashOp(tr, false)},
		Roots:     roots,
		LoadFleet: func() (*registry.Fleet, error) { return testFleet(), nil },
		Host:      "testhost",
		Mode:      model.WorkerDaemon,
	})

	ids, err := e.Reconcile(ctx)
	if err != nil || len(ids) != 1 || ids[0] != child.ID {
		t.Fatalf("Reconcile = %v, %v", ids, err)
	}
	got, err := e.Get(ctx, child.ID)
	if err != nil {
		t.Fatal(err)
	}
	if got.State != model.JobInterrupted || got.Steps[1].State != model.StepInterrupted {
		t.Fatalf("after Reconcile: job %s, step 2 %s", got.State, got.Steps[1].State)
	}
	if len(got.Reservations) != 1 || got.Reservations[0].Resource != "port:24040" {
		t.Fatalf("the killed job's reservations = %+v, want them KEPT", got.Reservations)
	}
	if got.Steps[1].ExternalIDs[0] != "snap-20260914T100000Z" {
		t.Fatalf("the checkpointed external id did not survive: %+v", got.Steps[1].ExternalIDs)
	}
	if trace := tr.trace(); len(trace) != 0 {
		t.Fatalf("Reconcile ran something: %v", trace)
	}

	if _, err := e.Resume(ctx, child.ID, operator()); err != nil {
		t.Fatalf("Resume: %v", err)
	}
	done := waitFor(t, e, child.ID, model.JobSucceeded, model.JobFailed)
	if done.State != model.JobSucceeded {
		t.Fatalf("after Resume: %s %+v", done.State, done.Error)
	}
	if trace := tr.trace(); !reflect.DeepEqual(trace, []string{"run:3"}) {
		t.Fatalf("trace = %v, want only step 3 — step 2's work already happened", trace)
	}
	log, err := e.StepLog(ctx, child.ID, 3)
	if err != nil || !strings.Contains(log, "bundle written") {
		t.Fatalf("step 3 log = %q, %v", log, err)
	}
}
