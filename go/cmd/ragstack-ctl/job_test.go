package main

// The job surface: the three reads and the three continuations.

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"io"
	"net/http"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
)

// `job list` turns its flags into the contract's query parameters. A filter
// the CLI dropped would show an operator the whole fleet's jobs when they
// asked about one tenant — and they would act on the top row.
func TestJobListSendsItsFilters(t *testing.T) {
	f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) {
		w.WriteHeader(http.StatusOK)
		_ = json.NewEncoder(w).Encode(model.JobsResponse{
			Jobs: []model.Job{sampleJob(model.JobSucceeded)}, Limit: 25,
		})
	})

	rc, out, errs := capture(t, "job", "list", "--server", f.srv.URL, "--tenant", "dev", "--state", "failed", "--limit", "25")
	if rc != exitOK {
		t.Fatalf("rc %d %s", rc, errs)
	}
	last := f.last(t)
	if last.Method != http.MethodGet || last.Path != "/v1/jobs" {
		t.Errorf("%s %s", last.Method, last.Path)
	}
	want := map[string]string{"tenant": "dev", "state": "failed", "limit": "25"}
	for k, v := range want {
		if got := last.Query.Get(k); got != v {
			t.Errorf("query %s=%q, want %q", k, got, v)
		}
	}
	if !strings.Contains(out, "01JB0000000000000000000000") {
		t.Errorf("the job was not listed:\n%s", out)
	}
}

// Unstated filters are not sent at all: `limit=0` is outside the contract's
// 1..500 and would be answered 422 rather than "use the default".
func TestJobListSendsNoEmptyFilters(t *testing.T) {
	f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) {
		w.WriteHeader(http.StatusOK)
		_ = json.NewEncoder(w).Encode(model.JobsResponse{Jobs: []model.Job{}, Limit: 50})
	})
	if rc, _, errs := capture(t, "job", "list", "--server", f.srv.URL); rc != exitOK {
		t.Fatalf("rc %d %s", rc, errs)
	}
	if q := f.last(t).Query; len(q) != 0 {
		t.Errorf("unstated filters were sent: %v", q)
	}
}

// `job show` renders the job an operator has to reason about — its steps, its
// error and its reservations — rather than only the state word.
func TestJobShowPrintsTheStepsAndTheError(t *testing.T) {
	j := sampleJob(model.JobFailed)
	j.Steps[0].State = model.StepFailed
	j.Error = &model.JobError{Code: "unit_start_failed", Detail: "the api unit never became active"}
	f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) {
		w.WriteHeader(http.StatusOK)
		_ = json.NewEncoder(w).Encode(j)
	})

	rc, out, errs := capture(t, "job", "show", j.ID, "--server", f.srv.URL)
	if rc != exitOK {
		t.Fatalf("rc %d %s", rc, errs)
	}
	if f.last(t).Path != "/v1/jobs/"+j.ID {
		t.Errorf("path %s", f.last(t).Path)
	}
	for _, want := range []string{"stop the api unit", "unit_start_failed", "the api unit never became active"} {
		if !strings.Contains(out, want) {
			t.Errorf("%q missing from:\n%s", want, out)
		}
	}
}

// `job log` addresses the step by number in the path and passes --lines
// through as the query the contract caps at 5000.
func TestJobLogTailsOneStep(t *testing.T) {
	f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) {
		w.WriteHeader(http.StatusOK)
		_ = json.NewEncoder(w).Encode(model.StepLogResponse{
			JobID: "01JB0000000000000000000000", Step: 3,
			Lines:     []string{"stopping ragstack-dev-api.service", "stopped"},
			Requested: 10, Returned: 2, Redacted: true,
		})
	})

	rc, out, errs := capture(t, "job", "log", "01JB0000000000000000000000", "3", "--lines", "10", "--server", f.srv.URL)
	if rc != exitOK {
		t.Fatalf("rc %d %s", rc, errs)
	}
	last := f.last(t)
	if last.Path != "/v1/jobs/01JB0000000000000000000000/steps/3/log" {
		t.Errorf("path %s", last.Path)
	}
	if last.Query.Get("lines") != "10" {
		t.Errorf("lines %q", last.Query.Get("lines"))
	}
	if !strings.Contains(out, "stopping ragstack-dev-api.service") {
		t.Errorf("the tail was not printed:\n%s", out)
	}
}

// The three continuations post the SAME envelope an operation does, with an
// empty args object, to /v1/jobs/{id}/{verb}. The idempotency key is what
// stops a double-pressed resume from re-running the step the first one is
// still in the middle of.
func TestJobContinuationsPostTheOperationEnvelope(t *testing.T) {
	for _, verb := range []string{"resume", "continue", "cancel"} {
		t.Run(verb, func(t *testing.T) {
			f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) { replyJob(w, sampleJob(model.JobRunning)) })
			id := "01JB0000000000000000000000"

			rc, out, errs := capture(t, "job", verb, id, "--server", f.srv.URL)
			if rc != exitOK {
				t.Fatalf("rc %d %s", rc, errs)
			}
			last := f.last(t)
			if last.Method != http.MethodPost || last.Path != "/v1/jobs/"+id+"/"+verb {
				t.Errorf("%s %s", last.Method, last.Path)
			}
			env := last.envelope(t)
			if key, _ := env["idempotency_key"].(string); !idempotencyKeyPattern.MatchString(key) {
				t.Errorf("idempotency_key %q", key)
			}
			if got := last.args(t); !reflect.DeepEqual(got, map[string]any{}) {
				t.Errorf("args %#v, want an empty object", got)
			}
			if !strings.Contains(out, id) {
				t.Errorf("the job was not printed:\n%s", out)
			}
		})
	}
}

// A continuation the daemon refuses — resuming a job that is not interrupted
// is 409 `refused` — is exit 3 with the daemon's own detail, not a CLI-side
// guess about which states are resumable.
func TestARefusedContinuationExitsThree(t *testing.T) {
	f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) {
		replyError(w, http.StatusConflict, "refused", "only an interrupted job may be resumed", nil)
	})
	rc, _, errs := capture(t, "job", "resume", "01JB0000000000000000000000", "--server", f.srv.URL)
	if rc != exitRefused || !strings.Contains(errs, "only an interrupted job may be resumed") {
		t.Errorf("rc %d %s", rc, errs)
	}
}

// A continuation accepts --wait like an operation, and the exit code is the
// resumed job's outcome.
func TestAWaitedContinuationExitsWithTheJobsOutcome(t *testing.T) {
	f := waitScript(t, model.JobSucceeded)
	rc, _, errs := capture(t, "job", "resume", "01JB0000000000000000000000", "--server", f.srv.URL, "--wait")
	if rc != exitOK {
		t.Fatalf("rc %d %s", rc, errs)
	}
	f2 := waitScript(t, model.JobFailed)
	if rc, _, _ := capture(t, "job", "resume", "01JB0000000000000000000000", "--server", f2.srv.URL, "--wait"); rc != exitJobFailed {
		t.Errorf("a failed resume: rc %d (want 4)", rc)
	}
}

// --json on a read prints the daemon's body VERBATIM. Re-encoding the decoded
// struct would silently drop any member this build's model does not know,
// which is exactly the member a script would be asking about.
func TestJobReadsPrintTheRawBodyUnderJSON(t *testing.T) {
	f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"jobs":[],"limit":50,"truncated":false,"a_member_from_the_future":1}`))
	})
	rc, out, _ := capture(t, "--json", "job", "list", "--server", f.srv.URL)
	if rc != exitOK {
		t.Fatalf("rc %d", rc)
	}
	if !strings.Contains(out, "a_member_from_the_future") {
		t.Errorf("the body was re-encoded rather than passed through:\n%s", out)
	}
}

// An unknown job is exit 1, not 3: no flag the operator can change makes a job
// that does not exist answer.
func TestAnUnknownJobExitsOne(t *testing.T) {
	f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) {
		replyError(w, http.StatusNotFound, "not_found", "no job 01JB0000000000000000000000", nil)
	})
	if rc, _, _ := capture(t, "job", "show", "01JB0000000000000000000000", "--server", f.srv.URL); rc != exitError {
		t.Errorf("rc %d (want 1)", rc)
	}
}

// `job` with no verb, and `job help`, both print the surface; only the first
// is a usage error.
func TestJobUsage(t *testing.T) {
	if rc, _, errs := capture(t, "job"); rc != exitUsage || !strings.Contains(errs, "usage:") {
		t.Errorf("bare job: rc %d %s", rc, errs)
	}
	if rc, _, _ := capture(t, "job", "help"); rc != exitOK {
		t.Error("job help")
	}
}

// --------------------------------------------------------------- --direct

// A continuation's envelope is read BEFORE the transport is chosen. It used to
// be built after the --direct branch, so `job cancel --direct --dry-run`
// cancelled the job: the flag that refuses it had not been looked at yet.
func TestADirectContinuationRefusesADryRunExactlyAsTheDaemonDoes(t *testing.T) {
	t.Setenv(envAPIKey, "")
	t.Setenv(envServer, "")
	for _, verb := range []string{"resume", "continue", "cancel"} {
		t.Run(verb, func(t *testing.T) {
			// --rag-root points at an empty directory: if this reached the
			// engine at all it would have to build one, and the test would
			// then be about whatever that engine did.
			rc, out, errs := capture(t, "job", verb, "01JB0000000000000000000000",
				"--direct", "--dry-run", "--rag-root", t.TempDir())
			if rc != exitRefused {
				t.Fatalf("rc %d, want %d (refused): %s%s", rc, exitRefused, out, errs)
			}
			for _, want := range []string{"no dry run", "01JB0000000000000000000000", "GET /v1/jobs/"} {
				if !strings.Contains(errs, want) {
					t.Errorf("the refusal does not mention %q: %s", want, errs)
				}
			}
		})
	}
	// The HTTP path answers the same thing, from the daemon.
	f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) {
		replyError(w, http.StatusConflict, "refused",
			"a continuation has no dry run: cancel acts on job X, whose plan is already recorded", nil)
	})
	if rc, _, _ := capture(t, "job", "cancel", "01JB0000000000000000000000",
		"--server", f.srv.URL, "--dry-run"); rc != exitRefused {
		t.Errorf("the HTTP path answered rc %d, want %d", rc, exitRefused)
	}
}

// followDirect is what makes --direct imply --wait: the engine runs the job on
// a goroutine, so returning at the queued snapshot exits the process while the
// worker is mid-step, and os.Exit does not wait for goroutines.
func TestDirectFollowsTheLocalEngineToATerminalState(t *testing.T) {
	old := directPollInterval
	directPollInterval = time.Millisecond
	t.Cleanup(func() { directPollInterval = old })

	eng := &scriptedEngine{states: []model.JobState{
		model.JobQueued, model.JobRunning, model.JobRunning, model.JobSucceeded,
	}}
	o := directFlags(t)
	accepted := sampleJob(model.JobQueued)

	var out, errb bytes.Buffer
	stdout, stderr = &out, &errb
	defer func() { stdout, stderr = os.Stdout, os.Stderr }()
	rc := followDirect(eng, o, &accepted)

	if rc != exitOK {
		t.Fatalf("rc %d, want 0 — the engine finished: %s%s", rc, out.String(), errb.String())
	}
	if eng.gets == 0 {
		t.Fatal("--direct never polled the engine; it printed the queued snapshot and returned")
	}
	if !strings.Contains(out.String(), "succeeded") {
		t.Errorf("the outcome was not printed:\n%s", out.String())
	}
}

// And a --direct job that parks at its cutover exits with the parked code, not
// 0: nothing is going to release it once this process is gone.
func TestDirectExitsWithTheParkedCodeAtACutover(t *testing.T) {
	old := directPollInterval
	directPollInterval = time.Millisecond
	t.Cleanup(func() { directPollInterval = old })

	eng := &scriptedEngine{states: []model.JobState{model.JobRunning, model.JobAwaitingCutover}}
	o := directFlags(t)
	accepted := sampleJob(model.JobQueued)

	var out, errb bytes.Buffer
	stdout, stderr = &out, &errb
	defer func() { stdout, stderr = os.Stdout, os.Stderr }()
	if rc := followDirect(eng, o, &accepted); rc != exitJobAwaitingCutover {
		t.Fatalf("rc %d, want %d", rc, exitJobAwaitingCutover)
	}
	if !strings.Contains(errb.String(), "job continue") {
		t.Errorf("the next move was not named: %s", errb.String())
	}
}

// directFlags is an opFlags with the defaults every --direct run has.
func directFlags(t *testing.T) *opFlags {
	t.Helper()
	fs := flag.NewFlagSet("test", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	o := addOpFlags(fs, "", t.TempDir(), false)
	if err := fs.Parse(nil); err != nil {
		t.Fatal(err)
	}
	return o
}

// scriptedEngine is a jobs.Engine that hands back one state per Get. Only Get
// is exercised: followDirect is the poll loop and nothing else.
type scriptedEngine struct {
	states []model.JobState
	gets   int
}

func (e *scriptedEngine) Get(_ context.Context, id string) (*model.Job, error) {
	i := e.gets
	if i >= len(e.states) {
		i = len(e.states) - 1
	}
	e.gets++
	j := sampleJob(e.states[i])
	j.ID = id
	if e.states[i] == model.JobSucceeded {
		j.Steps[0].State = model.StepSucceeded
	}
	return &j, nil
}

func (e *scriptedEngine) Submit(context.Context, jobs.Request) (*model.Plan, *model.Job, error) {
	return nil, nil, errors.New("not used")
}
func (e *scriptedEngine) List(context.Context, jobs.ListFilter) ([]model.Job, bool, error) {
	return nil, false, nil
}
func (e *scriptedEngine) StepLog(context.Context, string, int) (string, error) { return "", nil }
func (e *scriptedEngine) Resume(context.Context, string, jobs.Principal) (*model.Job, error) {
	return nil, nil
}
func (e *scriptedEngine) Continue(context.Context, string, jobs.Principal) (*model.Job, error) {
	return nil, nil
}
func (e *scriptedEngine) Cancel(context.Context, string, jobs.Principal, string) (*model.Job, error) {
	return nil, nil
}
func (e *scriptedEngine) Secrets(context.Context, string, jobs.Principal) (*model.SecretsResponse, error) {
	return nil, nil
}
func (e *scriptedEngine) Audit(context.Context, int) ([]model.AuditRow, bool, error) {
	return nil, false, nil
}
func (e *scriptedEngine) Reconcile(context.Context) ([]string, error) { return nil, nil }
