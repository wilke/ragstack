package main

// The transport half: the envelope the CLI builds, the credential it presents,
// the exit code it turns each answer into, and what `--wait` does with a job.
//
// Every test here talks to an httptest server. Nothing dials a real daemon and
// nothing reaches /rag: a CLI test that needed the control plane running would
// be skipped on every machine that matters and would stop catching anything.

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"regexp"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/model"
)

// recorded is one request the fake daemon received, kept whole so a test can
// assert on the decoded `args` rather than on a string the CLI printed.
type recorded struct {
	Method string
	Path   string
	Query  url.Values
	APIKey string
	Body   []byte
}

type fakeCtl struct {
	srv     *httptest.Server
	mu      sync.Mutex
	got     []recorded
	handler func(w http.ResponseWriter, rec recorded)
}

func newFakeCtl(t *testing.T, handler func(w http.ResponseWriter, rec recorded)) *fakeCtl {
	t.Helper()
	// A key left in the environment would make the credential tests pass for
	// the wrong reason (and could send a real operator's key to httptest).
	t.Setenv(envAPIKey, "")
	t.Setenv(envServer, "")
	f := &fakeCtl{handler: handler}
	f.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(io.LimitReader(r.Body, 1<<20))
		rec := recorded{Method: r.Method, Path: r.URL.Path, Query: r.URL.Query(),
			APIKey: r.Header.Get("X-API-Key"), Body: b}
		f.mu.Lock()
		f.got = append(f.got, rec)
		f.mu.Unlock()
		f.handler(w, rec)
	}))
	t.Cleanup(f.srv.Close)
	return f
}

func (f *fakeCtl) requests() []recorded {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]recorded(nil), f.got...)
}

func (f *fakeCtl) last(t *testing.T) recorded {
	t.Helper()
	r := f.requests()
	if len(r) == 0 {
		t.Fatal("the fake control plane received no request at all")
	}
	return r[len(r)-1]
}

// envelope decodes the op_request.json the CLI sent.
func (r recorded) envelope(t *testing.T) map[string]any {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal(r.Body, &m); err != nil {
		t.Fatalf("the request body is not JSON: %v\n%s", err, r.Body)
	}
	return m
}

func (r recorded) args(t *testing.T) map[string]any {
	t.Helper()
	env := r.envelope(t)
	args, ok := env["args"].(map[string]any)
	if !ok {
		t.Fatalf("the envelope carries no args object: %s", r.Body)
	}
	return args
}

// --------------------------------------------------------------- responses

func samplePlan(op, tenant string) model.Plan {
	return model.Plan{
		PlanHash:           "sha256:" + strings.Repeat("a", 64),
		Op:                 op,
		Tenant:             model.NullString(tenant),
		RegistryGeneration: 7,
		SchemaVersion:      1,
		Doctor:             model.DoctorResponse{Status: model.StatusGreen, Hash: "sha256:" + strings.Repeat("b", 64)},
		Steps: []model.PlannedStep{{
			N: 1, Kind: "unit", Title: "stop the api unit",
			Targets:    []string{"ragstack-dev-api.service"},
			WouldWrite: []model.WouldWrite{}, WouldRun: []model.WouldRun{}, Warnings: []string{},
		}},
		Warnings: []string{},
	}
}

func sampleJob(state model.JobState) model.Job {
	return model.Job{
		ID: "01JB0000000000000000000000", Op: "stop", Tenant: "dev",
		Principal: "key:ops", AuthMethod: model.AuthAPIKey, State: state,
		PlanHash: "sha256:" + strings.Repeat("a", 64), RequestID: "0123456789abcdef",
		IdempotencyKey: "ctl-" + strings.Repeat("0", 32), CreatedAt: "2026-09-14T10:00:00Z",
		Reservations: []model.Reservation{},
		Steps: []model.Step{{
			N: 1, Kind: "unit", Title: "stop the api unit",
			State: model.StepRunning, ExternalIDs: []string{},
		}},
	}
}

func replyPlan(w http.ResponseWriter, p model.Plan) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(p)
}

func replyJob(w http.ResponseWriter, j model.Job) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Location", "/v1/jobs/"+j.ID)
	w.WriteHeader(http.StatusAccepted)
	_ = json.NewEncoder(w).Encode(j)
}

func replyError(w http.ResponseWriter, status int, code, detail string, extra map[string]any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	body := map[string]any{"detail": detail, "code": code, "request_id": "0123456789abcdef"}
	if extra != nil {
		body["extra"] = extra
	}
	_ = json.NewEncoder(w).Encode(body)
}

// fastPolling makes --wait poll without sleeping. The interval is a package
// var precisely so a test never has to wait two real seconds per transition.
func fastPolling(t *testing.T) {
	t.Helper()
	old := jobPollInterval
	jobPollInterval = time.Millisecond
	t.Cleanup(func() { jobPollInterval = old })
}

// --------------------------------------------------------------- tests

// A dry run must print the PLAN and exit 0. The 200 answer is the whole point
// of --dry-run: a client that treated a plan as an unexpected non-202 would
// make the preview an error and push operators straight to the real run.
func TestDryRunPrintsThePlanAndExitsZero(t *testing.T) {
	f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) { replyPlan(w, samplePlan("stop", "dev")) })

	rc, out, errs := capture(t, "tenant", "stop", "dev", "--server", f.srv.URL, "--dry-run")
	if rc != exitOK {
		t.Fatalf("rc %d (want 0) %s", rc, errs)
	}
	if !strings.Contains(out, "plan stop dev") || !strings.Contains(out, "stop the api unit") {
		t.Errorf("the plan was not printed:\n%s", out)
	}
	if env := f.last(t).envelope(t); env["dry_run"] != true {
		t.Errorf("--dry-run did not set dry_run: %s", f.last(t).Body)
	}
}

// The ctl key is read from a file, sent as the X-API-Key HEADER, and never
// appears in the body or in anything the CLI prints. Putting it in
// `ctl_api_key` as well is answered 400 `both_credentials` when the two
// differ, and the header alone is the documented exemption; printing it is how
// a key ends up in a terminal recording.
func TestTheAPIKeyIsReadFromAFileAndNeverPrinted(t *testing.T) {
	const secret = "ctl-key-do-not-print-7f3a"
	dir := t.TempDir()
	keyFile := dir + "/ctl.key"
	if err := os.WriteFile(keyFile, []byte("  "+secret+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) { replyPlan(w, samplePlan("stop", "dev")) })

	rc, out, errs := capture(t, "tenant", "stop", "dev", "--server", f.srv.URL, "--dry-run", "--api-key-file", keyFile)
	if rc != exitOK {
		t.Fatalf("rc %d %s", rc, errs)
	}
	last := f.last(t)
	if last.APIKey != secret {
		t.Errorf("X-API-Key %q, want the trimmed file contents", last.APIKey)
	}
	if _, ok := last.envelope(t)["ctl_api_key"]; ok {
		t.Errorf("the key was also put in the body: %s", last.Body)
	}
	if strings.Contains(out, secret) || strings.Contains(errs, secret) {
		t.Error("the ctl key was printed")
	}
}

// With no --api-key-file the key comes from the environment, and still only as
// a header.
func TestTheAPIKeyFallsBackToTheEnvironment(t *testing.T) {
	f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) { replyPlan(w, samplePlan("stop", "dev")) })
	t.Setenv(envAPIKey, "env-key-9c1")

	if rc, _, errs := capture(t, "tenant", "stop", "dev", "--server", f.srv.URL, "--dry-run"); rc != exitOK {
		t.Fatalf("rc %d %s", rc, errs)
	}
	if got := f.last(t).APIKey; got != "env-key-9c1" {
		t.Errorf("X-API-Key %q", got)
	}
}

// An unreadable --api-key-file is a usage error naming the FILE — never its
// contents, and never a request sent unauthenticated instead.
func TestAMissingAPIKeyFileIsAUsageError(t *testing.T) {
	f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) { replyPlan(w, samplePlan("stop", "dev")) })

	rc, _, errs := capture(t, "tenant", "stop", "dev", "--server", f.srv.URL, "--dry-run",
		"--api-key-file", t.TempDir()+"/absent.key")
	if rc != exitUsage {
		t.Fatalf("rc %d (want 2)", rc)
	}
	if !strings.Contains(errs, "api-key-file") {
		t.Errorf("the refusal does not name the flag: %s", errs)
	}
	if len(f.requests()) != 0 {
		t.Error("the request was sent anyway, without a credential")
	}
}

// An absent --idempotency-key is GENERATED, matches op_request.json's pattern,
// and is announced on stderr. Without the announcement the retry of a request
// whose answer was lost carries a fresh key, and one `tenant restore` becomes
// two.
func TestIdempotencyKeyIsGeneratedAndMatchesTheContract(t *testing.T) {
	f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) { replyPlan(w, samplePlan("stop", "dev")) })
	pattern := regexp.MustCompile(`^[A-Za-z0-9._:-]{8,128}$`)

	rc, _, errs := capture(t, "tenant", "stop", "dev", "--server", f.srv.URL, "--dry-run")
	if rc != exitOK {
		t.Fatalf("rc %d %s", rc, errs)
	}
	key, _ := f.last(t).envelope(t)["idempotency_key"].(string)
	if !pattern.MatchString(key) {
		t.Errorf("generated idempotency key %q is outside the contract's pattern", key)
	}
	if !strings.Contains(errs, key) {
		t.Errorf("the generated key was not announced on stderr: %s", errs)
	}

	// Two runs must not collide: a constant key would make the second request
	// a duplicate of the first.
	if rc, _, _ := capture(t, "tenant", "stop", "dev", "--server", f.srv.URL, "--dry-run"); rc != exitOK {
		t.Fatalf("second run rc %d", rc)
	}
	if second, _ := f.last(t).envelope(t)["idempotency_key"].(string); second == key {
		t.Error("two runs generated the same idempotency key")
	}
}

// A key the operator supplies is sent verbatim (that is what makes a retry a
// retry), and one outside the contract's grammar is refused here rather than
// 422'd after crossing the network.
func TestASuppliedIdempotencyKeyIsSentVerbatim(t *testing.T) {
	f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) { replyPlan(w, samplePlan("stop", "dev")) })

	rc, _, errs := capture(t, "tenant", "stop", "dev", "--server", f.srv.URL, "--dry-run", "--idempotency-key", "ops-2026-09-14-a")
	if rc != exitOK {
		t.Fatalf("rc %d %s", rc, errs)
	}
	if got := f.last(t).envelope(t)["idempotency_key"]; got != "ops-2026-09-14-a" {
		t.Errorf("idempotency_key %v", got)
	}

	rc, _, errs = capture(t, "tenant", "stop", "dev", "--server", f.srv.URL, "--dry-run", "--idempotency-key", "short")
	if rc != exitUsage || !strings.Contains(errs, "idempotency-key") {
		t.Errorf("a key outside the pattern: rc %d %s", rc, errs)
	}
}

// --yes and --yes-destructive set `confirm`, and the destructive one wins:
// a plan that demands the tenant's name is never satisfied by "yes".
func TestConfirmFlagsSetTheEnvelopeConfirm(t *testing.T) {
	f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) { replyPlan(w, samplePlan("stop", "dev")) })

	capture(t, "tenant", "stop", "dev", "--server", f.srv.URL, "--dry-run", "--yes")
	if got := f.last(t).envelope(t)["confirm"]; got != "yes" {
		t.Errorf("--yes sent confirm %v", got)
	}
	capture(t, "tenant", "decommission", "dev", "--server", f.srv.URL, "--dry-run", "--yes", "--yes-destructive", "dev")
	if got := f.last(t).envelope(t)["confirm"]; got != "dev" {
		t.Errorf("--yes-destructive sent confirm %v", got)
	}
	capture(t, "tenant", "stop", "dev", "--server", f.srv.URL, "--dry-run")
	if _, ok := f.last(t).envelope(t)["confirm"]; ok {
		t.Error("confirm was sent without either flag")
	}
}

// --force-with-doctor-diff travels as force_with_doctor_diff. It lets a YELLOW
// doctor through and nothing else, so a client that dropped it would make
// every yellow host unoperatable from the CLI.
func TestForceWithDoctorDiffTravelsInTheEnvelope(t *testing.T) {
	f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) { replyPlan(w, samplePlan("stop", "dev")) })
	hash := "sha256:" + strings.Repeat("c", 64)

	capture(t, "tenant", "stop", "dev", "--server", f.srv.URL, "--dry-run", "--force-with-doctor-diff", hash)
	if got := f.last(t).envelope(t)["force_with_doctor_diff"]; got != hash {
		t.Errorf("force_with_doctor_diff %v", got)
	}
}

// Every refusal — the daemon understood the request and declined it — is
// exit 3. Distinguishing it from exit 1 is what tells a script "change
// something and ask again" apart from "the control plane could not answer".
func TestRefusalsExitThree(t *testing.T) {
	cases := []struct {
		name   string
		status int
		code   string
	}{
		{"an operator-level refusal", http.StatusConflict, "refused"},
		{"another job holds the lock", http.StatusConflict, "locked"},
		{"the plan moved under the locks", http.StatusConflict, "plan_stale"},
		{"the key was already used", http.StatusConflict, "duplicate"},
		{"the doctor is red", http.StatusConflict, "doctor_red"},
		{"the args do not validate", http.StatusUnprocessableEntity, "validation"},
		{"the subject may not do this", http.StatusForbidden, "forbidden"},
		{"the credential was not accepted", http.StatusUnauthorized, "auth_required"},
		{"the plan needs a confirm", http.StatusPreconditionRequired, "confirm_required"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) {
				replyError(w, c.status, c.code, "the daemon declined this request", nil)
			})
			rc, _, errs := capture(t, "tenant", "stop", "dev", "--server", f.srv.URL, "--yes")
			if rc != exitRefused {
				t.Fatalf("rc %d (want 3) %s", rc, errs)
			}
			if !strings.Contains(errs, "the daemon declined this request") {
				t.Errorf("the detail was not printed: %s", errs)
			}
		})
	}
}

// A 401 says so in the words an operator can act on: it is the KEY that was
// rejected, not the operation that was refused on its merits.
func TestUnauthorizedSaysTheKeyWasNotAccepted(t *testing.T) {
	f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) {
		replyError(w, http.StatusUnauthorized, "auth_required", "no usable credential", nil)
	})
	rc, _, errs := capture(t, "tenant", "stop", "dev", "--server", f.srv.URL, "--yes")
	if rc != exitRefused || !strings.Contains(errs, "the ctl key was not accepted") {
		t.Errorf("rc %d %s", rc, errs)
	}
}

// A 428 whose body carries `confirm_value` must be reported with the exact
// retry the operator has to type. Without it they are left holding a refusal
// they cannot satisfy — the confirm value is the tenant's name, and guessing
// is the failure mode the confirm exists to prevent.
func TestConfirmRequiredNamesTheConfirmValue(t *testing.T) {
	f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) {
		replyError(w, http.StatusPreconditionRequired, "confirm_required",
			"this plan is destructive", map[string]any{"confirm_value": "asm-next"})
	})
	rc, _, errs := capture(t, "tenant", "decommission", "asm-next", "--server", f.srv.URL)
	if rc != exitRefused {
		t.Fatalf("rc %d (want 3)", rc)
	}
	if !strings.Contains(errs, "--yes-destructive asm-next") {
		t.Errorf("the retry was not named: %s", errs)
	}
}

// The same for a yellow doctor: the hash in `extra` is what
// --force-with-doctor-diff quotes, so the message hands it over ready to paste.
func TestDoctorRedNamesTheForceFlag(t *testing.T) {
	hash := "sha256:" + strings.Repeat("d", 64)
	f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) {
		replyError(w, http.StatusConflict, "doctor_red", "the op-scoped doctor is yellow",
			map[string]any{"doctor_hash": hash})
	})
	rc, _, errs := capture(t, "tenant", "start", "dev", "--server", f.srv.URL, "--yes")
	if rc != exitRefused || !strings.Contains(errs, "--force-with-doctor-diff "+hash) {
		t.Errorf("rc %d %s", rc, errs)
	}
}

// A duplicate key already produced a job; naming it is what lets the operator
// look at the run that is already happening instead of forcing another.
func TestDuplicateNamesTheOriginalJob(t *testing.T) {
	f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) {
		replyError(w, http.StatusConflict, "duplicate", "that key belongs to another request",
			map[string]any{"job_id": "01JB0000000000000000000000"})
	})
	rc, _, errs := capture(t, "tenant", "stop", "dev", "--server", f.srv.URL, "--yes")
	if rc != exitRefused || !strings.Contains(errs, "job show 01JB0000000000000000000000") {
		t.Errorf("rc %d %s", rc, errs)
	}
}

// A 404, a 500 and a body this client cannot parse are all FAULTS (exit 1):
// nothing in any of them tells the operator which flag to change.
func TestFaultsExitOne(t *testing.T) {
	t.Run("not found", func(t *testing.T) {
		f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) {
			replyError(w, http.StatusNotFound, "not_found", "no tenant ghost", nil)
		})
		if rc, _, _ := capture(t, "tenant", "stop", "ghost", "--server", f.srv.URL, "--yes"); rc != exitError {
			t.Errorf("rc %d (want 1)", rc)
		}
	})
	t.Run("internal", func(t *testing.T) {
		f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) {
			replyError(w, http.StatusInternalServerError, "internal", "the control plane could not answer", nil)
		})
		if rc, _, _ := capture(t, "tenant", "stop", "dev", "--server", f.srv.URL, "--yes"); rc != exitError {
			t.Errorf("rc %d (want 1)", rc)
		}
	})
	t.Run("an unparseable body", func(t *testing.T) {
		f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) {
			w.WriteHeader(http.StatusConflict)
			_, _ = w.Write([]byte("<html>nginx says no</html>"))
		})
		rc, _, errs := capture(t, "tenant", "stop", "dev", "--server", f.srv.URL, "--yes")
		if rc != exitError || !strings.Contains(errs, "cannot read") {
			t.Errorf("rc %d %s", rc, errs)
		}
	})
	t.Run("a 200 that is not a plan", func(t *testing.T) {
		f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) {
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte("[]"))
		})
		if rc, _, _ := capture(t, "tenant", "stop", "dev", "--server", f.srv.URL, "--dry-run"); rc != exitError {
			t.Errorf("rc %d (want 1)", rc)
		}
	})
}

// A daemon that is not listening is exit 1: the request never reached a
// policy, so nothing about it is known to be wrong.
func TestATransportFailureExitsOne(t *testing.T) {
	f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) {})
	dead := f.srv.URL
	f.srv.Close()

	rc, _, errs := capture(t, "tenant", "stop", "dev", "--server", dead, "--yes")
	if rc != exitError {
		t.Fatalf("rc %d (want 1) %s", rc, errs)
	}
}

// A --server that is not an http(s) URL is a usage error before anything is
// dialled.
func TestANonHTTPServerIsAUsageError(t *testing.T) {
	rc, _, errs := capture(t, "tenant", "stop", "dev", "--server", "unix:///tmp/ctl.sock", "--yes")
	if rc != exitUsage || !strings.Contains(errs, "--server") {
		t.Errorf("rc %d %s", rc, errs)
	}
}

// Without --wait the CLI prints the accepted job and stops: the 202 is an
// acceptance, not an outcome, and exiting 0 on it is what makes a scripted
// `&& next-step` run before the job has done anything.
func TestAnAcceptedJobIsPrintedWithoutWait(t *testing.T) {
	f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) { replyJob(w, sampleJob(model.JobRunning)) })

	rc, out, errs := capture(t, "tenant", "stop", "dev", "--server", f.srv.URL, "--yes")
	if rc != exitOK {
		t.Fatalf("rc %d %s", rc, errs)
	}
	if !strings.Contains(out, "01JB0000000000000000000000") || !strings.Contains(out, "state running") {
		t.Errorf("the accepted job was not printed:\n%s", out)
	}
	if len(f.requests()) != 1 {
		t.Errorf("%d requests: the CLI polled without --wait", len(f.requests()))
	}
}

// waitScript answers the POST with a running job and then walks the GETs
// through the given states, one per poll.
func waitScript(t *testing.T, states ...model.JobState) *fakeCtl {
	t.Helper()
	fastPolling(t)
	var n int
	var mu sync.Mutex
	return newFakeCtl(t, func(w http.ResponseWriter, rec recorded) {
		if rec.Method == http.MethodPost {
			replyJob(w, sampleJob(model.JobRunning))
			return
		}
		mu.Lock()
		i := n
		if i < len(states)-1 {
			n++
		}
		mu.Unlock()
		j := sampleJob(states[i])
		j.Steps[0].State = model.StepSucceeded
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_ = json.NewEncoder(w).Encode(j)
	})
}

// With --wait the exit code IS the job's outcome. A wait that exited 0 on a
// failed job would make every scripted operation report success for a fleet it
// had just broken.
func TestWaitExitsZeroOnASucceededJob(t *testing.T) {
	f := waitScript(t, model.JobRunning, model.JobSucceeded)
	rc, out, errs := capture(t, "tenant", "stop", "dev", "--server", f.srv.URL, "--yes", "--wait")
	if rc != exitOK {
		t.Fatalf("rc %d (want 0) %s", rc, errs)
	}
	if !strings.Contains(out, "succeeded") {
		t.Errorf("the outcome was not printed:\n%s", out)
	}
}

func TestWaitExitsFourOnAFailedJob(t *testing.T) {
	for _, state := range []model.JobState{model.JobFailed, model.JobRolledBack} {
		f := waitScript(t, state)
		rc, _, errs := capture(t, "tenant", "stop", "dev", "--server", f.srv.URL, "--yes", "--wait")
		if rc != exitJobFailed {
			t.Errorf("%s: rc %d (want 4) %s", state, rc, errs)
		}
	}
}

// An interrupted job is exit 5 and NOT exit 4: its reservations are still
// held and `job resume` is the next move, which is a different operator
// action from investigating a failure.
func TestWaitExitsFiveOnAnInterruptedJob(t *testing.T) {
	f := waitScript(t, model.JobInterrupted)
	rc, _, errs := capture(t, "tenant", "stop", "dev", "--server", f.srv.URL, "--yes", "--wait")
	if rc != exitJobInterrupted {
		t.Fatalf("rc %d (want 5)", rc)
	}
	if !strings.Contains(errs, "job resume") {
		t.Errorf("the next move was not named: %s", errs)
	}
}

// A cancelled job is a refusal (3): somebody declined it, it did not fail.
func TestWaitExitsThreeOnACancelledJob(t *testing.T) {
	f := waitScript(t, model.JobCancelled)
	if rc, _, _ := capture(t, "tenant", "stop", "dev", "--server", f.srv.URL, "--yes", "--wait"); rc != exitRefused {
		t.Errorf("rc %d (want 3)", rc)
	}
}

// awaiting_cutover is not terminal, but nothing advances it except the
// operator. A wait that only stopped at JobState.Terminal() would sit there
// until the timeout on exactly the runs (handover, migrate-local) where the
// operator is the next step.
func TestWaitStopsAtAwaitingCutoverAndSaysWhatIsNext(t *testing.T) {
	f := waitScript(t, model.JobAwaitingCutover)
	rc, _, errs := capture(t, "tenant", "stop", "dev", "--server", f.srv.URL, "--yes", "--wait")
	if rc != exitOK {
		t.Fatalf("rc %d (want 0)", rc)
	}
	if !strings.Contains(errs, "job continue") {
		t.Errorf("the next move was not named: %s", errs)
	}
}

// --wait prints each step as its state changes, and prints it ONCE. A poll
// loop that reprinted the unchanged steps would bury the one line that moved.
func TestWaitPrintsEachStepTransitionOnce(t *testing.T) {
	f := waitScript(t, model.JobRunning, model.JobRunning, model.JobSucceeded)
	rc, out, _ := capture(t, "tenant", "stop", "dev", "--server", f.srv.URL, "--yes", "--wait")
	if rc != exitOK {
		t.Fatalf("rc %d", rc)
	}
	var steps []string
	for _, line := range strings.Split(out, "\n") {
		if strings.HasPrefix(line, "  step") {
			steps = append(steps, line)
		}
	}
	// Exactly two: the step was running when the job was accepted and
	// succeeded by the end, and the three polls in between changed nothing.
	if len(steps) != 2 || !strings.HasSuffix(steps[0], "running") || !strings.HasSuffix(steps[1], "succeeded") {
		t.Errorf("step transitions printed %d times: %q\n%s", len(steps), steps, out)
	}
}

// --json prints the raw document rather than the human summary, because a
// script that has to re-parse a table is a script that breaks on the next
// column.
func TestJSONPrintsTheRawPlan(t *testing.T) {
	f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) { replyPlan(w, samplePlan("stop", "dev")) })
	rc, out, _ := capture(t, "--json", "tenant", "stop", "dev", "--server", f.srv.URL, "--dry-run")
	if rc != exitOK {
		t.Fatalf("rc %d", rc)
	}
	var p model.Plan
	if err := json.Unmarshal([]byte(out), &p); err != nil {
		t.Fatalf("not JSON: %v\n%s", err, out)
	}
	if p.Op != "stop" || p.RegistryGeneration != 7 {
		t.Errorf("plan %+v", p)
	}
}

// --direct builds the SAME engine the daemon does (api.BuildEngine) and must
// report honestly that it is not wired yet. A CLI that stubbed an engine of
// its own would take locks the daemon does not honour.
func TestDirectRunsTheEngineLocally(t *testing.T) {
	f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) { replyPlan(w, samplePlan("stop", "dev")) })
	// No registry under this rag root: the engine builds, the plan refuses
	// with the registry error, and no daemon is consulted.
	rc, _, errs := capture(t, "tenant", "stop", "dev", "--direct", "--dry-run", "--rag-root", t.TempDir())
	if rc == exitOK {
		t.Fatalf("rc 0 with no registry; stderr: %s", errs)
	}
	if strings.Contains(errs, "not wired") {
		t.Errorf("the engine is wired now; stderr still says otherwise: %s", errs)
	}
	if len(f.requests()) != 0 {
		t.Error("--direct still talked to a daemon")
	}
}
