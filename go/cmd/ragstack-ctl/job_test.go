package main

// The job surface: the three reads and the three continuations.

import (
	"encoding/json"
	"net/http"
	"reflect"
	"strings"
	"testing"

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
