package api

import (
	"context"
	"fmt"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/auth"
	"github.com/ragstack/ragstack/internal/ctl/authz"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/ratelimit"
	"github.com/ragstack/ragstack/internal/ctl/session"
)

// --------------------------------------------------------------------------
// fakeEngine
//
// An in-package jobs.Engine, deliberately NOT the real one and deliberately
// not an import of the ops or drivers packages: the handler's job is to
// translate an engine answer into a contract answer, and a test that ran the
// real engine would be asserting the engine's behaviour while claiming to
// assert the handler's. Every method returns exactly what the test set.
// --------------------------------------------------------------------------

type fakeEngine struct {
	// submitted is the LAST request the handler built, which is what the
	// principal and envelope assertions read.
	submitted jobs.Request
	calls     int

	plan *model.Plan
	job  *model.Job
	err  error

	list      []model.Job
	truncated bool

	stepLog string
	secrets *model.SecretsResponse
	audit   []model.AuditRow

	continuation string // the last continuation called: resume/continue/cancel
	principal    jobs.Principal
	reconciled   []string
}

var _ jobs.Engine = (*fakeEngine)(nil)

func (f *fakeEngine) Submit(_ context.Context, req jobs.Request) (*model.Plan, *model.Job, error) {
	f.submitted, f.calls = req, f.calls+1
	return f.plan, f.job, f.err
}

func (f *fakeEngine) Get(context.Context, string) (*model.Job, error) { return f.job, f.err }

func (f *fakeEngine) List(_ context.Context, _ jobs.ListFilter) ([]model.Job, bool, error) {
	return f.list, f.truncated, f.err
}

func (f *fakeEngine) StepLog(context.Context, string, int) (string, error) {
	return f.stepLog, f.err
}

func (f *fakeEngine) Resume(_ context.Context, _ string, p jobs.Principal) (*model.Job, error) {
	f.continuation, f.principal = "resume", p
	return f.job, f.err
}

func (f *fakeEngine) Continue(_ context.Context, _ string, p jobs.Principal) (*model.Job, error) {
	f.continuation, f.principal = "continue", p
	return f.job, f.err
}

func (f *fakeEngine) Cancel(_ context.Context, _ string, p jobs.Principal) (*model.Job, error) {
	f.continuation, f.principal = "cancel", p
	return f.job, f.err
}

func (f *fakeEngine) Secrets(_ context.Context, _ string, p jobs.Principal) (*model.SecretsResponse, error) {
	f.principal = p
	return f.secrets, f.err
}

func (f *fakeEngine) Audit(context.Context, int) ([]model.AuditRow, bool, error) {
	return f.audit, f.truncated, f.err
}

func (f *fakeEngine) Reconcile(context.Context) ([]string, error) { return f.reconciled, f.err }

// extraErr is an engine error that carries the contract's `extra` object, the
// way the real engine is expected to: any error in the chain with an
// ErrorExtra method supplies it, and nothing in jobs.go's seam had to change
// for that to be true.
type extraErr struct {
	base  error
	extra map[string]any
}

func (e extraErr) Error() string              { return e.base.Error() }
func (e extraErr) Unwrap() error              { return e.base }
func (e extraErr) ErrorExtra() map[string]any { return e.extra }

// newEngineServer is newTestServer with an engine attached.
func newEngineServer(t *testing.T, eng jobs.Engine) http.Handler {
	t.Helper()
	envs := map[string]string{
		auth.EnvAPIKeys:     `["` + opKey + `","` + viewerKey + `"]`,
		auth.EnvAPIKeyRoles: `{"` + opKey + `":"operator","` + viewerKey + `":"viewer"}`,
		auth.EnvAPIKeyNames: `{"` + opKey + `":"ops","` + viewerKey + `":"watcher"}`,
	}
	keys, err := auth.LoadKeys(func(k string) string { return envs[k] })
	if err != nil {
		t.Fatal(err)
	}
	verifier, err := auth.New(auth.Options{})
	if err != nil {
		t.Fatal(err)
	}
	sessions := session.NewMemoryStore()
	return NewRouter(&Server{
		Backend: NewFakeBackend(),
		Engine:  eng,
		Resolver: &auth.Resolver{
			Keys: keys, Verifier: verifier, Sessions: sessions,
			Limiter: ratelimit.New(ratelimit.Config{PerCredential: -1, TarpitAt: -1}),
			Reject:  reject,
		},
		Sessions: sessions,
	})
}

const opStart = "/v1/tenants/dev/ops/start"

func opHeaders() map[string]string { return map[string]string{auth.HeaderAPIKey: opKey} }

func opBody(extra string) string {
	body := `"dry_run":false,"idempotency_key":"01ARZ3NDEKTSV4RR","args":{}`
	if extra != "" {
		body += "," + extra
	}
	return "{" + body + "}"
}

func samplePlan() *model.Plan {
	return &model.Plan{
		PlanHash:           "sha256:" + strings.Repeat("ab", 32),
		Op:                 "start",
		Tenant:             "dev",
		RegistryGeneration: 7,
		SchemaVersion:      1,
		Doctor:             model.DoctorResponse{Hash: "sha256:" + strings.Repeat("cd", 32)},
		RequiresConfirm:    true,
		ConfirmValue:       "dev",
		Steps:              []model.PlannedStep{},
		Warnings:           []string{},
	}
}

func sampleJob() *model.Job {
	pid := 4242
	step := 1
	return &model.Job{
		ID:             "01ARZ3NDEKTSV4RRFFQ69G5FAV",
		Op:             "start",
		Tenant:         "dev",
		Principal:      "key:ops",
		AuthMethod:     model.AuthAPIKey,
		State:          model.JobRunning,
		PlanHash:       "sha256:" + strings.Repeat("ab", 32),
		RequestID:      "0123456789abcdef",
		IdempotencyKey: "01ARZ3NDEKTSV4RR",
		CreatedAt:      "2026-09-14T10:00:00Z",
		Worker:         &model.JobWorker{PID: pid, Host: "coconut", Mode: model.WorkerDaemon},
		Lock:           &model.JobLock{Order: []model.LockName{model.LockTenant}, Since: "2026-09-14T10:00:00Z"},
		Reservations:   []model.Reservation{{Resource: "port:24040"}},
		CurrentStep:    &step,
		Steps: []model.Step{{
			N: 1, Kind: "systemd", Title: "start the api unit", State: model.StepRunning,
			Log: "step-1.log", Checkpoint: true, ExternalIDs: []string{"ragstack-dev-api.service"},
		}},
	}
}

// --------------------------------------------------------------------------
// The submit path: one answer shape, one error map
// --------------------------------------------------------------------------

// TestDryRunAnswersThePlanAndExecuteAnswersTheJob pins the two success shapes
// the contract gives every mutation. A dry run that answered 202, or an
// execute that answered 200, would make `--dry-run` indistinguishable from a
// real mutation to any client reading the status alone.
func TestDryRunAnswersThePlanAndExecuteAnswersTheJob(t *testing.T) {
	eng := &fakeEngine{plan: samplePlan(), job: sampleJob()}
	h := newEngineServer(t, eng)

	w := do(t, h, http.MethodPost, opStart, opHeaders(),
		`{"dry_run":true,"idempotency_key":"01ARZ3NDEKTSV4RR","args":{}}`)
	if w.Code != http.StatusOK {
		t.Fatalf("dry run status = %d: %s", w.Code, w.Body.String())
	}
	if body := decode(t, w); body["plan_hash"] != eng.plan.PlanHash {
		t.Fatalf("dry run did not answer the plan: %v", body)
	}
	if !eng.submitted.DryRun {
		t.Fatal("the handler did not pass dry_run to the engine")
	}

	w = do(t, h, http.MethodPost, opStart, opHeaders(), opBody(""))
	if w.Code != http.StatusAccepted {
		t.Fatalf("execute status = %d: %s", w.Code, w.Body.String())
	}
	if got := w.Header().Get("Location"); got != "/v1/jobs/"+eng.job.ID {
		t.Fatalf("Location = %q; a 202 without it leaves the caller with no job to poll", got)
	}
	if body := decode(t, w); body["id"] != eng.job.ID {
		t.Fatalf("execute did not answer the job: %v", body)
	}
}

// TestEveryEngineErrorMapsToItsContractStatus is the whole error map in one
// table. Each engine sentinel has exactly one code and each code exactly one
// status; a sentinel that fell through to the default would answer 500 for
// what is a policy decision, which is the difference between "retry with
// confirm" and "page someone".
func TestEveryEngineErrorMapsToItsContractStatus(t *testing.T) {
	cases := []struct {
		name   string
		err    error
		status int
		code   string
	}{
		{"validation", fmt.Errorf("args: %w", jobs.ErrValidation), 422, "validation"},
		{"not found", fmt.Errorf("no tenant: %w", jobs.ErrNotFound), 404, "not_found"},
		{"refused", fmt.Errorf("update-code lands in v1.1: %w", jobs.ErrRefused), 409, "refused"},
		{"locked", fmt.Errorf("held: %w", jobs.ErrLocked), 409, "locked"},
		{"plan stale", fmt.Errorf("moved: %w", jobs.ErrPlanStale), 409, "plan_stale"},
		{"doctor red", fmt.Errorf("red: %w", jobs.ErrDoctorRed), 409, "doctor_red"},
		{"duplicate", fmt.Errorf("used: %w", jobs.ErrDuplicate), 409, "duplicate"},
		{"confirm required", fmt.Errorf("needs confirm: %w", jobs.ErrConfirmRequired), 428, "confirm_required"},
		{"forbidden", fmt.Errorf("another principal: %w", jobs.ErrForbidden), 403, "forbidden"},
		// The contract's one code/status exception: a delivered envelope is
		// 410 and its CODE is still `not_found`.
		{"gone", fmt.Errorf("delivered: %w", jobs.ErrGone), 410, "not_found"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			h := newEngineServer(t, &fakeEngine{err: c.err, plan: samplePlan()})
			assertError(t, do(t, h, http.MethodPost, opStart, opHeaders(), opBody("")), c.status, c.code)
		})
	}
}

// TestAnUnknownEngineErrorIsAnOpaqueFiveHundred: an error that is not one of
// the sentinels is a FAULT, not an answer, and its text is host state nobody
// has vetted. It must never reach the body.
func TestAnUnknownEngineErrorIsAnOpaqueFiveHundred(t *testing.T) {
	h := newEngineServer(t, &fakeEngine{err: fmt.Errorf("sqlite: disk I/O error on /rag/data/ctl/jobs.db")})
	body := assertError(t, do(t, h, http.MethodPost, opStart, opHeaders(), opBody("")), 500, "internal")
	if strings.Contains(body["detail"].(string), "jobs.db") {
		t.Fatalf("the engine's own text reached the body: %v", body["detail"])
	}
}

// TestTheContractExtrasAreAlwaysPresent. A 428 without `confirm_value` leaves
// the caller with a refusal it cannot satisfy, and a 409 plan_stale without
// the hash leaves it unable to say what moved. The engine may attach them; a
// handler that relied on it would produce an unsatisfiable body the first
// time one did not.
func TestTheContractExtrasAreAlwaysPresent(t *testing.T) {
	plan := samplePlan()

	h := newEngineServer(t, &fakeEngine{plan: plan, err: fmt.Errorf("x: %w", jobs.ErrConfirmRequired)})
	body := assertError(t, do(t, h, http.MethodPost, opStart, opHeaders(), opBody("")), 428, "confirm_required")
	if extra, _ := body["extra"].(map[string]any); extra["confirm_value"] != "dev" {
		t.Fatalf("confirm_value missing from the 428: %v", body["extra"])
	}

	h = newEngineServer(t, &fakeEngine{plan: plan, err: fmt.Errorf("x: %w", jobs.ErrPlanStale)})
	body = assertError(t, do(t, h, http.MethodPost, opStart, opHeaders(), opBody("")), 409, "plan_stale")
	if extra, _ := body["extra"].(map[string]any); extra["plan_hash"] != plan.PlanHash {
		t.Fatalf("plan_hash missing from the 409: %v", body["extra"])
	}

	h = newEngineServer(t, &fakeEngine{plan: plan, err: fmt.Errorf("x: %w", jobs.ErrDoctorRed)})
	body = assertError(t, do(t, h, http.MethodPost, opStart, opHeaders(), opBody("")), 409, "doctor_red")
	if extra, _ := body["extra"].(map[string]any); extra["doctor_hash"] != plan.Doctor.Hash {
		t.Fatalf("doctor_hash missing from the 409: %v", body["extra"])
	}
}

// TestAnEngineSuppliedExtraWins: when the engine DOES attach an extra — the
// lock holder, the original job id of a duplicate — it is the one that
// reaches the caller, not a plan-derived guess.
func TestAnEngineSuppliedExtraWins(t *testing.T) {
	err := extraErr{
		base:  fmt.Errorf("held by another job: %w", jobs.ErrLocked),
		extra: map[string]any{"job_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV", "since": "2026-09-14T10:00:00Z"},
	}
	h := newEngineServer(t, &fakeEngine{err: err, plan: samplePlan()})
	body := assertError(t, do(t, h, http.MethodPost, opStart, opHeaders(), opBody("")), 409, "locked")
	extra, _ := body["extra"].(map[string]any)
	if extra["job_id"] != "01ARZ3NDEKTSV4RRFFQ69G5FAV" || extra["since"] == nil {
		t.Fatalf("the engine's extra did not reach the caller: %v", body["extra"])
	}
}

// --------------------------------------------------------------------------
// The envelope
// --------------------------------------------------------------------------

// TestTheEnvelopeIsValidatedBeforeTheEngineIsCalled. op_request.json's own
// bounds are the handler's to enforce: an idempotency key outside the pattern
// is not a key the store can dedupe on, and reaching the engine with one
// would make "same key, same request" a property of whatever the caller sent.
func TestTheEnvelopeIsValidatedBeforeTheEngineIsCalled(t *testing.T) {
	cases := []struct {
		name, body, field string
	}{
		{"no idempotency key", `{"dry_run":false,"args":{}}`, "idempotency_key"},
		{"short idempotency key", `{"dry_run":false,"idempotency_key":"abc","args":{}}`, "idempotency_key"},
		{"key with a space", `{"dry_run":false,"idempotency_key":"01ARZ3ND KTSV4RR","args":{}}`, "idempotency_key"},
		{"doctor hash is not a hash", `{"dry_run":false,"idempotency_key":"01ARZ3NDEKTSV4RR","args":{},"force_with_doctor_diff":"beef"}`, "force_with_doctor_diff"},
		{"args is not an object", `{"dry_run":false,"idempotency_key":"01ARZ3NDEKTSV4RR","args":[]}`, "args"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			eng := &fakeEngine{plan: samplePlan(), job: sampleJob()}
			h := newEngineServer(t, eng)
			body := assertError(t, do(t, h, http.MethodPost, opStart, opHeaders(), c.body), 422, "validation")
			fields := body["extra"].(map[string]any)["fields"].([]any)
			if len(fields) == 0 || fields[0] != c.field {
				t.Fatalf("extra.fields = %v; want %q", fields, c.field)
			}
			if eng.calls != 0 {
				t.Fatal("the engine was called with an envelope the contract rejects")
			}
		})
	}
}

// TestAnUnknownBodyMemberNamesItself. `ctl_api_kye` used to read as absent;
// it now reads as a 422 whose extra.fields says which member was not
// understood, because a caller that has to diff its body against the schema
// to find a typo will guess instead.
func TestAnUnknownBodyMemberNamesItself(t *testing.T) {
	h := newEngineServer(t, &fakeEngine{})
	body := assertError(t, do(t, h, http.MethodPost, opStart, opHeaders(),
		`{"dry_run":false,"idempotency_key":"01ARZ3NDEKTSV4RR","args":{},"ctl_api_kye":"x"}`), 422, "validation")
	fields := body["extra"].(map[string]any)["fields"].([]any)
	if len(fields) != 1 || fields[0] != "ctl_api_kye" {
		t.Fatalf("extra.fields = %v; want the misspelt member", fields)
	}
}

// TestAVerbOutsideTheContractIsNotALookupMiss: `frobnicate` is a path
// parameter outside its schema (422), not an unknown tenant (404) — the two
// send an operator looking in different places.
func TestAVerbOutsideTheContractIsNotALookupMiss(t *testing.T) {
	eng := &fakeEngine{}
	h := newEngineServer(t, eng)
	body := assertError(t, do(t, h, http.MethodPost, "/v1/tenants/dev/ops/frobnicate",
		opHeaders(), opBody("")), 422, "validation")
	if fields := body["extra"].(map[string]any)["fields"].([]any); fields[0] != "verb" {
		t.Fatalf("extra.fields = %v", fields)
	}
	if eng.calls != 0 {
		t.Fatal("an unknown verb reached the engine")
	}
}

// TestTheEngineSeesTheAuthenticatedPrincipalNotTheBody. Everything the engine
// authorizes against comes from the authn middleware; a body that could
// supply a subject or a role would be an unauthenticated identity claim, and
// the engine re-authorizes every continuation against exactly this value.
func TestTheEngineSeesTheAuthenticatedPrincipalNotTheBody(t *testing.T) {
	eng := &fakeEngine{job: sampleJob()}
	h := newEngineServer(t, eng)
	do(t, h, http.MethodPost, opStart, opHeaders(), opBody(""))

	p := eng.submitted.Principal
	if p.Role != auth.RoleOperator {
		t.Fatalf("role = %q; want operator", p.Role)
	}
	if p.Method != model.AuthAPIKey {
		t.Fatalf("auth method = %q; want api_key", p.Method)
	}
	if p.FromSession {
		t.Fatal("an X-API-Key request was reported as a session mutation")
	}
	if !ridRE.MatchString(p.RequestID) {
		t.Fatalf("request id %q is not the response's X-Request-Id shape", p.RequestID)
	}
	if p.SudoUser != "" {
		t.Fatal("SUDO_USER is a --direct fact; over HTTP it must be empty")
	}
	if eng.submitted.Mode != model.WorkerDaemon {
		t.Fatalf("mode = %q; an HTTP submission is a daemon job", eng.submitted.Mode)
	}
	if eng.submitted.Op != "start" || eng.submitted.Tenant != "dev" {
		t.Fatalf("op/tenant = %q/%q", eng.submitted.Op, eng.submitted.Tenant)
	}
}

// TestTheNonTenantEntryPointsCarryTheirOwnOpNames. The engine's registry is
// keyed by op name, so a create that submitted itself as "" or a reload that
// submitted itself as "gateway-apply" would run the wrong plan against the
// right authorization.
func TestTheNonTenantEntryPointsCarryTheirOwnOpNames(t *testing.T) {
	cases := []struct {
		method, path, body, wantOp, wantTenant string
	}{
		{http.MethodPost, "/v1/tenants",
			`{"dry_run":true,"idempotency_key":"01ARZ3NDEKTSV4RR","args":{"name":"zz-new","artifact_id":"a1"}}`,
			"create", "zz-new"},
		{http.MethodPost, "/v1/gateway/apply", opBody(""), "gateway-apply", ""},
		{http.MethodPost, "/v1/gateway/reload", opBody(""), "gateway-reload", ""},
		{http.MethodPut, "/v1/settings", opBody(""), "settings-put", ""},
	}
	for _, c := range cases {
		t.Run(c.wantOp, func(t *testing.T) {
			eng := &fakeEngine{plan: samplePlan(), job: sampleJob()}
			h := newEngineServer(t, eng)
			w := do(t, h, c.method, c.path, opHeaders(), c.body)
			if w.Code != http.StatusOK && w.Code != http.StatusAccepted {
				t.Fatalf("status = %d: %s", w.Code, w.Body.String())
			}
			if eng.submitted.Op != c.wantOp {
				t.Fatalf("op = %q; want %q", eng.submitted.Op, c.wantOp)
			}
			if eng.submitted.Tenant != c.wantTenant {
				t.Fatalf("tenant = %q; want %q", eng.submitted.Tenant, c.wantTenant)
			}
		})
	}
}

// --------------------------------------------------------------------------
// The session rule
// --------------------------------------------------------------------------

// TestASessionMutationMustRePresentAnOperatorKey. A session authenticates
// reads; the ctl key is what authorizes the write, and the key must itself be
// an operator key. Without the role check a viewer key would satisfy
// "re-present a ctl key" while authorizing nothing, which makes the
// re-presentation a formality rather than a control — and the role is checked
// BEFORE ownership, so the refusal names the reason the caller can act on.
func TestASessionMutationMustRePresentAnOperatorKey(t *testing.T) {
	eng := &fakeEngine{plan: samplePlan(), job: sampleJob()}
	h := newEngineServer(t, eng)

	created := do(t, h, http.MethodPost, "/v1/session", opHeaders(), "")
	sid := decode(t, created)["session_id"].(string)
	sess := map[string]string{auth.HeaderAuthorization: "Session " + sid}

	// No key at all: the refusal says what to do.
	body := assertError(t, do(t, h, http.MethodPost, opStart, sess, opBody("")), 403, "forbidden")
	if !strings.Contains(body["detail"].(string), "re-present a ctl API key") {
		t.Fatalf("detail = %v", body["detail"])
	}
	if eng.calls != 0 {
		t.Fatal("a session with no ctl key reached the engine")
	}

	// A VIEWER key: refused on the role, before ownership is even considered.
	body = assertError(t, do(t, h, http.MethodPost, opStart, sess,
		opBody(`"ctl_api_key":"`+viewerKey+`"`)), 403, "forbidden")
	if !strings.Contains(body["detail"].(string), "operator key") {
		t.Fatalf("detail = %v; the refusal must name the role", body["detail"])
	}
	if eng.calls != 0 {
		t.Fatal("a session with a viewer key reached the engine")
	}

	// The operator key the session was minted from: accepted, and the engine
	// is told it came over a session so it can record the auth method on the
	// job and re-authorize the continuations the same way.
	w := do(t, h, http.MethodPost, opStart, sess, opBody(`"ctl_api_key":"`+opKey+`"`))
	if w.Code != http.StatusAccepted {
		t.Fatalf("status = %d: %s", w.Code, w.Body.String())
	}
	if !eng.submitted.Principal.FromSession {
		t.Fatal("the engine was not told the mutation arrived over a session")
	}
	if eng.submitted.Principal.Method != model.AuthSession {
		t.Fatalf("auth method = %q; want session", eng.submitted.Principal.Method)
	}
}

// --------------------------------------------------------------------------
// The continuations
// --------------------------------------------------------------------------

// TestEachContinuationCallsItsOwnEngineMethod. The three share a handler, and
// a switch that fell through would let `cancel` resume a job — a mutation
// with the opposite meaning under the same authorization.
func TestEachContinuationCallsItsOwnEngineMethod(t *testing.T) {
	for _, op := range []string{"resume", "continue", "cancel"} {
		t.Run(op, func(t *testing.T) {
			eng := &fakeEngine{job: sampleJob()}
			h := newEngineServer(t, eng)
			w := do(t, h, http.MethodPost, "/v1/jobs/01ARZ3NDEKTSV4RRFFQ69G5FAV/"+op, opHeaders(), opBody(""))
			if w.Code != http.StatusAccepted {
				t.Fatalf("status = %d: %s", w.Code, w.Body.String())
			}
			if eng.continuation != op {
				t.Fatalf("called %q; want %q", eng.continuation, op)
			}
			if eng.principal.Role != auth.RoleOperator {
				t.Fatalf("the continuation was not re-authorized against THIS request: %+v", eng.principal)
			}
			if got := w.Header().Get("Location"); got != "/v1/jobs/"+eng.job.ID {
				t.Fatalf("Location = %q", got)
			}
		})
	}
}

// TestAContinuationOnAMalformedJobIDIsAValidationFailure, not a 404: a value
// that is not a ULID names nothing, and answering 404 would let a caller read
// "there is no such job" into a request the daemon never looked up.
func TestAContinuationOnAMalformedJobIDIsAValidationFailure(t *testing.T) {
	eng := &fakeEngine{job: sampleJob()}
	h := newEngineServer(t, eng)
	body := assertError(t, do(t, h, http.MethodPost, "/v1/jobs/not-a-ulid/resume", opHeaders(), opBody("")), 422, "validation")
	if fields := body["extra"].(map[string]any)["fields"].([]any); fields[0] != "id" {
		t.Fatalf("extra.fields = %v", fields)
	}
	if eng.continuation != "" {
		t.Fatal("a malformed job id reached the engine")
	}
}

// --------------------------------------------------------------------------
// Reads: the viewer reduction
// --------------------------------------------------------------------------

// TestTheViewerReductionOnJobs is ctlJobsList's viewer_fields, asserted
// against a job that HAS every one of the reduced members populated — a
// reduction tested against an empty job asserts nothing.
func TestTheViewerReductionOnJobs(t *testing.T) {
	eng := &fakeEngine{list: []model.Job{*sampleJob()}, job: sampleJob()}
	h := newEngineServer(t, eng)

	// The operator sees all of it — which is what makes the viewer assertion
	// below non-vacuous.
	w := do(t, h, http.MethodGet, "/v1/jobs", opHeaders(), "")
	rows := decode(t, w)["jobs"].([]any)
	first := rows[0].(map[string]any)
	if first["worker"] == nil || first["lock"] == nil {
		t.Fatal("the operator's own view is already reduced; the viewer test would prove nothing")
	}
	if len(first["reservations"].([]any)) == 0 {
		t.Fatal("the fixture job has no reservations; the viewer test would prove nothing")
	}
	step := first["steps"].([]any)[0].(map[string]any)
	if step["log"] == nil || len(step["external_ids"].([]any)) == 0 {
		t.Fatal("the fixture step has no log or external ids; the viewer test would prove nothing")
	}

	for _, path := range []string{"/v1/jobs", "/v1/jobs/01ARZ3NDEKTSV4RRFFQ69G5FAV"} {
		w := do(t, h, http.MethodGet, path, map[string]string{auth.HeaderAPIKey: viewerKey}, "")
		if w.Code != http.StatusOK {
			t.Fatalf("%s: status = %d: %s", path, w.Code, w.Body.String())
		}
		body := decode(t, w)
		job := body
		if rows, ok := body["jobs"].([]any); ok {
			job = rows[0].(map[string]any)
		}
		if job["worker"] != nil {
			t.Errorf("%s: a viewer received worker %v", path, job["worker"])
		}
		if job["lock"] != nil {
			t.Errorf("%s: a viewer received lock %v", path, job["lock"])
		}
		if res := job["reservations"].([]any); len(res) != 0 {
			t.Errorf("%s: a viewer received reservations %v", path, res)
		}
		st := job["steps"].([]any)[0].(map[string]any)
		if st["log"] != nil {
			t.Errorf("%s: a viewer received a step log path %v", path, st["log"])
		}
		if ids := st["external_ids"].([]any); len(ids) != 0 {
			t.Errorf("%s: a viewer received external ids %v", path, ids)
		}
	}
}

// TestTheViewerReductionDoesNotMutateTheEngineRecord. The engine may hand
// back records it holds in its own cache; a reduction that wrote through them
// would make the NEXT operator's read of the same job return the viewer
// shape — a leak that runs the safe way and is therefore never noticed.
func TestTheViewerReductionDoesNotMutateTheEngineRecord(t *testing.T) {
	eng := &fakeEngine{list: []model.Job{*sampleJob()}}
	h := newEngineServer(t, eng)
	do(t, h, http.MethodGet, "/v1/jobs", map[string]string{auth.HeaderAPIKey: viewerKey}, "")

	held := eng.list[0]
	if held.Worker == nil || held.Lock == nil || len(held.Reservations) == 0 {
		t.Fatal("the viewer reduction wrote through to the engine's own record")
	}
	if len(held.Steps[0].ExternalIDs) == 0 || held.Steps[0].Log == "" {
		t.Fatal("the viewer reduction wrote through to the engine's own step")
	}
}

// TestJobQueryParametersAreBounded: an out-of-range limit or an unknown state
// is a 422 naming the parameter, never a silent clamp — a caller that asked
// for 9999 jobs should learn the cap rather than believe it got everything.
func TestJobQueryParametersAreBounded(t *testing.T) {
	h := newEngineServer(t, &fakeEngine{list: []model.Job{}})
	for _, c := range []struct{ query, field string }{
		{"?limit=9999", "limit"},
		{"?limit=0", "limit"},
		{"?state=nonsense", "state"},
		{"?tenant=Not_A_Tenant", "tenant"},
	} {
		body := assertError(t, do(t, h, http.MethodGet, "/v1/jobs"+c.query, opHeaders(), ""), 422, "validation")
		if fields := body["extra"].(map[string]any)["fields"].([]any); fields[0] != c.field {
			t.Errorf("%s: extra.fields = %v; want %q", c.query, fields, c.field)
		}
	}
}

// --------------------------------------------------------------------------
// Step logs, secrets, audit
// --------------------------------------------------------------------------

// TestStepLogTailsAndReportsTruncation: the same bound and the same honesty
// as the tenant log tail. A `returned` that did not match the lines sent, or
// a `truncated` that stayed false after dropping lines, would make the
// response lie about what the operator is looking at.
func TestStepLogTailsAndReportsTruncation(t *testing.T) {
	eng := &fakeEngine{stepLog: "one\ntwo\nthree\n"}
	h := newEngineServer(t, eng)

	body := decode(t, do(t, h, http.MethodGet,
		"/v1/jobs/01ARZ3NDEKTSV4RRFFQ69G5FAV/steps/1/log?lines=2", opHeaders(), ""))
	lines := body["lines"].([]any)
	if len(lines) != 2 || lines[0] != "two" || lines[1] != "three" {
		t.Fatalf("lines = %v; want the TAIL", lines)
	}
	if body["truncated"] != true || body["returned"].(float64) != 2 || body["redacted"] != true {
		t.Fatalf("body = %v", body)
	}
	if body["step"].(float64) != 1 {
		t.Fatalf("step = %v", body["step"])
	}

	// A step number that is not a positive integer names nothing.
	assertError(t, do(t, h, http.MethodGet,
		"/v1/jobs/01ARZ3NDEKTSV4RRFFQ69G5FAV/steps/0/log", opHeaders(), ""), 422, "validation")
}

// TestSecretsAreNoStoreOnEveryAnswer. The header is set before the engine is
// consulted, so a 410 that names a delivery time is no more cacheable than
// the 200 that carried the value.
func TestSecretsAreNoStoreOnEveryAnswer(t *testing.T) {
	const id = "/v1/jobs/01ARZ3NDEKTSV4RRFFQ69G5FAV/secrets"

	eng := &fakeEngine{secrets: &model.SecretsResponse{
		JobID:       "01ARZ3NDEKTSV4RRFFQ69G5FAV",
		DeliveredAt: "2026-09-14T10:00:00Z",
		ExpiresAt:   "2026-09-14T10:15:00Z",
		Secrets:     []model.Secret{{ID: "admin", Label: "bootstrap admin", Role: "admin", Value: strings.Repeat("0f", 32)}},
	}}
	h := newEngineServer(t, eng)
	w := do(t, h, http.MethodGet, id, opHeaders(), "")
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", w.Code, w.Body.String())
	}
	if got := w.Header().Get("Cache-Control"); got != "no-store" {
		t.Fatalf("Cache-Control = %q on the one response that carries a secret", got)
	}

	gone := newEngineServer(t, &fakeEngine{err: fmt.Errorf("delivered at …: %w", jobs.ErrGone)})
	w = do(t, gone, http.MethodGet, id, opHeaders(), "")
	assertError(t, w, 410, "not_found")
	if got := w.Header().Get("Cache-Control"); got != "no-store" {
		t.Fatalf("Cache-Control = %q on a 410", got)
	}
}

// TestAuditFiltersByTenantAndNeverEmitsNull: `args_redacted` is a required
// object in audit_response.json, and a nil map marshals to null — a body its
// own contract test rejects.
func TestAuditFiltersByTenantAndNeverEmitsNull(t *testing.T) {
	eng := &fakeEngine{audit: []model.AuditRow{
		{ID: 1, At: "2026-09-14T10:00:00Z", Phase: model.AuditIntent, Principal: "key:ops",
			AuthMethod: model.AuthAPIKey, RequestID: "0123456789abcdef", Op: "start", Tenant: "dev", Outcome: "accepted"},
		{ID: 2, At: "2026-09-14T10:00:01Z", Phase: model.AuditIntent, Principal: "key:ops",
			AuthMethod: model.AuthAPIKey, RequestID: "0123456789abcdef", Op: "start", Tenant: "demo", Outcome: "accepted"},
	}}
	h := newEngineServer(t, eng)

	body := decode(t, do(t, h, http.MethodGet, "/v1/audit?tenant=dev", opHeaders(), ""))
	rows := body["rows"].([]any)
	if len(rows) != 1 || rows[0].(map[string]any)["tenant"] != "dev" {
		t.Fatalf("rows = %v", rows)
	}
	if args, ok := rows[0].(map[string]any)["args_redacted"].(map[string]any); !ok || args == nil {
		t.Fatalf("args_redacted is not an object: %v", rows[0])
	}

	// A viewer never reads the audit log at all.
	assertError(t, do(t, h, http.MethodGet, "/v1/audit",
		map[string]string{auth.HeaderAPIKey: viewerKey}, ""), 403, "forbidden")
}

// --------------------------------------------------------------------------
// PUT /v1/settings
// --------------------------------------------------------------------------

// TestSettingsPutAcceptsOnlyTheWritableSubset. `recipients` decides who can
// DECRYPT a backup and `registry_generation` is the server's own counter;
// neither is a value a browser may send, and accepting one silently would be
// worse than refusing it loudly.
func TestSettingsPutAcceptsOnlyTheWritableSubset(t *testing.T) {
	refused := []struct{ name, args, field string }{
		{"recipients", `{"recipients":{"count":2}}`, "recipients"},
		{"registry generation", `{"registry_generation":9}`, "registry_generation"},
		{"unknown member", `{"nope":1}`, "nope"},
		{"auto delete is const false", `{"retention":{"auto_delete":true}}`, "retention.auto_delete"},
		{"keep_last must be positive", `{"retention":{"keep_last":{"backup":0}}}`, "retention.keep_last.backup"},
		{"unknown image", `{"images":{"neo4j":{"version":"5"}}}`, "images.neo4j"},
		{"digest must be a digest", `{"images":{"qdrant":{"digest":"latest"}}}`, "images.qdrant.digest"},
		{"port out of range", `{"ctl":{"port":80}}`, "ctl.port"},
		{"ui_dist must be absolute", `{"ctl":{"ui_dist":"relative/dist"}}`, "ctl.ui_dist"},
		{"gateway_enabled must be a boolean", `{"ctl":{"gateway_enabled":"yes"}}`, "ctl.gateway_enabled"},
		{"python env must be absolute", `{"python_env_default":"envs/ragstack"}`, "python_env_default"},
	}
	for _, c := range refused {
		t.Run(c.name, func(t *testing.T) {
			eng := &fakeEngine{plan: samplePlan(), job: sampleJob()}
			h := newEngineServer(t, eng)
			body := assertError(t, do(t, h, http.MethodPut, "/v1/settings", opHeaders(),
				`{"dry_run":false,"idempotency_key":"01ARZ3NDEKTSV4RR","args":`+c.args+`}`), 422, "validation")
			fields := body["extra"].(map[string]any)["fields"].([]any)
			found := false
			for _, f := range fields {
				if f == c.field {
					found = true
				}
			}
			if !found {
				t.Fatalf("extra.fields = %v; want %q", fields, c.field)
			}
			if eng.calls != 0 {
				t.Fatal("a settings body outside the writable subset reached the engine")
			}
		})
	}
}

// TestSettingsPutAcceptsThePartialThatMatteredOnCoconut: flipping
// `ctl.gateway_enabled` alone. Demanding the whole document for it would make
// every settings change a read-modify-write race against the generation the
// change itself moves.
func TestSettingsPutAcceptsThePartialThatMatteredOnCoconut(t *testing.T) {
	for _, args := range []string{
		`{"ctl":{"gateway_enabled":true}}`,
		`{"retention":{"keep_last":{"backup":14},"keep_partial_hours":48,"auto_delete":false}}`,
		`{"images":{"qdrant":{"sif":"/rag/apptainer/images/qdrant.sif","version":"1.12.0","digest":"sha256:` + strings.Repeat("ab", 32) + `"}}}`,
		`{"python_env_default":"/rag/envs/ragstack"}`,
		`{}`,
	} {
		eng := &fakeEngine{plan: samplePlan(), job: sampleJob()}
		h := newEngineServer(t, eng)
		w := do(t, h, http.MethodPut, "/v1/settings", opHeaders(),
			`{"dry_run":false,"idempotency_key":"01ARZ3NDEKTSV4RR","args":`+args+`}`)
		if w.Code != http.StatusAccepted {
			t.Fatalf("%s: status = %d: %s", args, w.Code, w.Body.String())
		}
		if eng.submitted.Op != opSettingsPut {
			t.Fatalf("op = %q", eng.submitted.Op)
		}
	}
}

// --------------------------------------------------------------------------
// The engine-less daemon
// --------------------------------------------------------------------------

// TestWithoutAnEngineEveryMutationIsRefusedAndEveryReadIsHonest. A daemon
// whose engine did not build still serves the read surface — that is the only
// view an operator has of the host the engine failed on — and every mutation
// says WHY rather than answering a 200-shaped nothing.
func TestWithoutAnEngineEveryMutationIsRefusedAndEveryReadIsHonest(t *testing.T) {
	h := newTestServer(t) // no Engine
	mutating := 0
	for _, row := range authz.Matrix {
		if !row.Mutating {
			continue
		}
		mutating++
		// dry_run FALSE: a dry-run continuation is refused for a different
		// reason (it has no plan of its own), and this test is about the
		// missing engine.
		w := do(t, h, row.Method, concretePath(row.Path), opHeaders(),
			`{"dry_run":false,"idempotency_key":"01ARZ3NDEKTSV4RR","args":{}}`)
		body := assertError(t, w, http.StatusConflict, "refused")
		if !strings.Contains(body["detail"].(string), "not wired") {
			t.Errorf("%s: detail = %v", row.OperationID, body["detail"])
		}
	}
	if mutating == 0 {
		t.Fatal("the matrix marks no operation mutating; the test asserted nothing")
	}

	jobsBody := decode(t, asOperator(t, h, http.MethodGet, "/v1/jobs"))
	if len(jobsBody["jobs"].([]any)) != 0 || jobsBody["truncated"] != false {
		t.Fatalf("a daemon with no engine claimed to have jobs: %v", jobsBody)
	}
	auditBody := decode(t, asOperator(t, h, http.MethodGet, "/v1/audit"))
	if len(auditBody["rows"].([]any)) != 0 {
		t.Fatalf("a daemon with no engine claimed to have audit rows: %v", auditBody)
	}
	assertError(t, asOperator(t, h, http.MethodGet, "/v1/jobs/01ARZ3NDEKTSV4RRFFQ69G5FAV"), 404, "not_found")
}

// TestAContinuationHasNoDryRun. The contract lists a 200 Plan for resume,
// continue and cancel, and the engine seam gives them no way to produce one:
// they act on a job that already carries its plan. Computing a second plan
// here would answer with something the engine would never execute, so the
// dry run is refused and the detail says where the real plan is.
func TestAContinuationHasNoDryRun(t *testing.T) {
	eng := &fakeEngine{job: sampleJob()}
	h := newEngineServer(t, eng)
	for _, op := range []string{"resume", "continue", "cancel"} {
		body := assertError(t, do(t, h, http.MethodPost,
			"/v1/jobs/01ARZ3NDEKTSV4RRFFQ69G5FAV/"+op, opHeaders(),
			`{"dry_run":true,"idempotency_key":"01ARZ3NDEKTSV4RR","args":{}}`), 409, "refused")
		if !strings.Contains(body["detail"].(string), "GET /v1/jobs/") {
			t.Errorf("%s: the refusal does not say where the plan is: %v", op, body["detail"])
		}
	}
	if eng.continuation != "" {
		t.Fatal("a dry run performed a continuation")
	}
}

// TestBuildEngineReportsThatItIsNotWired pins the integration seam: the stub
// returns an ERROR rather than a nil engine, so `serve` can say so once at
// start-up instead of every handler discovering it independently.
func TestBuildEngineBuildsAFakeDriverEngine(t *testing.T) {
	dir := t.TempDir()
	eng, err := BuildEngine(EngineConfig{
		Roots:        paths.NewRoots(dir, paths.Overrides{}),
		RegistryPath: filepath.Join(dir, "registry.json"), // absent: plans refuse, the engine still builds
		StorePath:    filepath.Join(dir, "jobs.db"),
		Mode:         model.WorkerDaemon,
		Host:         "test",
		FakeDrivers:  true,
	})
	if err != nil || eng == nil {
		t.Fatalf("BuildEngine = %v, %v; want an engine", eng, err)
	}
	if _, err := os.Stat(filepath.Join(dir, "jobs.db")); err != nil {
		t.Fatalf("the store was not created: %v", err)
	}
	if _, _, err := eng.List(context.Background(), jobs.ListFilter{Limit: 5}); err != nil {
		t.Fatalf("List on a fresh engine: %v", err)
	}
}

// assertNoRecorderLeak keeps the compiler honest about httptest being used
// through do() only; without a reference the import would be dropped and the
// helper signature would drift from the rest of the package's tests.
var _ = func() *httptest.ResponseRecorder { return httptest.NewRecorder() }
