package api

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"regexp"
	"strconv"
	"strings"

	"github.com/go-chi/chi/v5"

	"github.com/ragstack/ragstack/internal/ctl/auth"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/observability"
)

// --------------------------------------------------------------------------
// The mutation surface: POST …/ops/{verb}, POST /v1/tenants, the two gateway
// mutations, the three job continuations and PUT /v1/settings.
//
// Every one of them is the SAME three steps — validate the envelope, build a
// jobs.Request with the AUTHENTICATED principal, hand it to the engine — and
// they share one error map and one answer shape (200 Plan for a dry run, 202
// Job with Location otherwise). Writing them as one submit path rather than
// six handlers is what keeps a verb from quietly acquiring its own status
// codes: the contract gives all six the same response set.
//
// What is NOT here: the audit rows. A handler that wrote them would produce a
// row for a request the engine refused before recording anything, and two
// writers of one log disagree about ordering the first time a job is
// submitted from the --direct CLI. The engine owns the audit; the handler
// logs its request line and nothing else.
// --------------------------------------------------------------------------

// opVerbs is components/parameters/Verb. A verb outside it is a path
// parameter outside its schema — 422, like every other one — and never
// reaches the engine, so "unknown verb" cannot be confused with "unknown
// tenant" by a caller that mistyped one.
var opVerbs = map[string]bool{
	"start": true, "stop": true, "restart": true, "backup": true, "restore": true,
	"handover": true, "migrate-local": true, "decommission": true,
	"key-mint": true, "key-revoke": true, "admin-add": true, "admin-remove": true,
	"sa-create": true, "sa-disable": true, "sa-enable": true,
	"env-set": true, "env-unset": true, "env-normalize": true,
	"render-units": true, "update-code": true,
}

// Non-tenant op names. The engine's registry is keyed by these exactly as it
// is by the verbs above (jobs.Request.Op: "or `create`, `gateway-apply`,
// `gateway-reload` for the non-tenant entry points").
const (
	opCreate        = "create"
	opGatewayApply  = "gateway-apply"
	opGatewayReload = "gateway-reload"
	opSettingsPut   = "settings-put"
	opJobResume     = "resume"
	opJobContinue   = "continue"
	opJobCancel     = "cancel"
)

// idempotencyKeyRE and doctorHashRE are op_request.json's own patterns.
var (
	idempotencyKeyRE = regexp.MustCompile(`^[A-Za-z0-9._:-]{8,128}$`)
	doctorHashRE     = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)
)

// errorExtra is how an engine error carries the contract's `extra` object —
// the lock holder, the stale plan hash, the doctor hash, the original job id.
//
// It is an INTERFACE rather than a concrete error type in the jobs package so
// that the HTTP layer depends on the seam (jobs.go) and on nothing else: any
// error in the chain with this method supplies the object, and an engine that
// supplies none still produces a contract-shaped body (the extras the plan
// itself can answer are filled in from the plan below).
type errorExtra interface{ ErrorExtra() map[string]any }

// engineNotWired is the answer of every mutation on a daemon whose engine
// failed to build. 409 `refused` rather than a 501: error.json promises one
// status per code, and the contract's own wording for an operation that
// cannot run yet is `refused` "with `detail` saying so".
func (s *Server) engineNotWired(w http.ResponseWriter, r *http.Request) {
	why := ErrEngineNotWired
	if s.EngineErr != nil {
		why = s.EngineErr
	}
	writeError(w, r, model.CodeRefused,
		"the job engine is unavailable on this daemon: "+why.Error()+
			" — reads still answer; fix the cause and restart the daemon", nil)
}

// jobsError maps an engine error onto the contract. One code per error, one
// status per code; plan is the plan the engine computed (it may be non-nil
// alongside a refusal), and is the source of the extras the engine did not
// attach itself.
func (s *Server) jobsError(w http.ResponseWriter, r *http.Request, plan *model.Plan, err error) {
	extra := extraFrom(err)
	switch {
	case errors.Is(err, jobs.ErrValidation):
		writeError(w, r, model.CodeValidation, err.Error(), extra)
	case errors.Is(err, jobs.ErrNotFound):
		writeError(w, r, model.CodeNotFound, err.Error(), extra)
	case errors.Is(err, jobs.ErrLocked):
		writeError(w, r, model.CodeLocked, err.Error(), extra)
	case errors.Is(err, jobs.ErrPlanStale):
		writeError(w, r, model.CodePlanStale, err.Error(), planExtra(extra, plan))
	case errors.Is(err, jobs.ErrDoctorRed):
		writeError(w, r, model.CodeDoctorRed, err.Error(), doctorExtra(extra, plan))
	case errors.Is(err, jobs.ErrDuplicate):
		writeError(w, r, model.CodeDuplicate, err.Error(), extra)
	case errors.Is(err, jobs.ErrConfirmRequired):
		writeError(w, r, model.CodeConfirmRequired, err.Error(), confirmExtra(extra, plan))
	case errors.Is(err, jobs.ErrGone):
		// The contract's one code/status exception: an envelope that was
		// already delivered is 410 with code `not_found`.
		writeErrorStatus(w, r, http.StatusGone, model.CodeNotFound, err.Error(), extra)
	case errors.Is(err, jobs.ErrForbidden):
		writeError(w, r, model.CodeForbidden, err.Error(), extra)
	case errors.Is(err, jobs.ErrRefused):
		writeError(w, r, model.CodeRefused, err.Error(), extra)
	default:
		// An engine fault, not a policy answer: logged through the REDACTING
		// logger and reported as the opaque 500 every other backend fault is.
		s.log().Error("job engine",
			"request_id", observability.RequestIDFromContext(r.Context()), "err", err.Error())
		writeError(w, r, model.CodeInternal, "the control plane could not answer this request", nil)
	}
}

func extraFrom(err error) map[string]any {
	var e errorExtra
	if errors.As(err, &e) {
		if x := e.ErrorExtra(); len(x) > 0 {
			return x
		}
	}
	return nil
}

// planExtra, doctorExtra and confirmExtra fill in the extras the CONTRACT
// promises for three of the 409/428 answers when the engine did not attach
// them. Each is a fact of the plan the engine handed back, so deriving it
// here cannot disagree with the engine — and a body missing `confirm_value`
// leaves a caller with a 428 it cannot satisfy.
func planExtra(extra map[string]any, plan *model.Plan) map[string]any {
	if extra != nil || plan == nil {
		return extra
	}
	return map[string]any{"plan_hash": plan.PlanHash, "registry_generation": plan.RegistryGeneration}
}

func doctorExtra(extra map[string]any, plan *model.Plan) map[string]any {
	if extra != nil || plan == nil {
		return extra
	}
	return map[string]any{"doctor_hash": plan.Doctor.Hash}
}

func confirmExtra(extra map[string]any, plan *model.Plan) map[string]any {
	if extra != nil || plan == nil {
		return extra
	}
	return map[string]any{"confirm_value": string(plan.ConfirmValue)}
}

// --------------------------------------------------------------------------
// The envelope
// --------------------------------------------------------------------------

// jobPrincipal is the AUTHENTICATED caller, as the authn middleware
// established it. Nothing in it comes from the body: a caller-supplied
// subject, role or sudo user would be an unauthenticated identity claim, and
// the engine re-authorizes every continuation against exactly this value.
func jobPrincipal(r *http.Request) jobs.Principal {
	p, _ := auth.PrincipalFromContext(r.Context())
	return jobs.Principal{
		Subject:   p.Subject,
		Role:      p.Role,
		Method:    model.AuthMethod(p.AuthMethod),
		SudoUser:  "", // a --direct CLI fact; over HTTP it is always empty
		RequestID: observability.RequestIDFromContext(r.Context()),
		// The guard has already refused a session that did not re-present a
		// ctl operator key, so this flag reaches the engine meaning "verified
		// session mutation", not "unverified".
		FromSession: p.AuthMethod == auth.MethodSession,
	}
}

// buildRequest turns the envelope the GUARD parsed into a jobs.Request.
//
// The envelope's own members are validated here against op_request.json —
// the pattern of `idempotency_key`, the bounds of `confirm`, the shape of
// `force_with_doctor_diff` — because a body that does not match the envelope
// is 422 `validation` and never a planning input. `args` is NOT validated
// here: that is x-ctl-op-args, which the engine's Op.Validate owns, so the
// per-verb schema has one reader.
func (s *Server) buildRequest(w http.ResponseWriter, r *http.Request, op, tenant string) (jobs.Request, bool) {
	body, ok := mutationBodyFromContext(r.Context())
	if !ok {
		// Unreachable on a route the matrix marks Mutating; written so a
		// handler reached any other way fails closed rather than planning
		// with a zero envelope.
		writeError(w, r, model.CodeInternal, "the control plane could not read this request body", nil)
		return jobs.Request{}, false
	}
	var bad []string
	if body.IdempotencyKey == "" || !idempotencyKeyRE.MatchString(body.IdempotencyKey) {
		bad = append(bad, "idempotency_key")
	}
	if n := len(body.Confirm); n > 64 {
		bad = append(bad, "confirm")
	}
	if body.ForceWithDoctorDiff != "" && !doctorHashRE.MatchString(body.ForceWithDoctorDiff) {
		bad = append(bad, "force_with_doctor_diff")
	}
	args, err := decodeArgs(body.Args)
	if err != nil {
		bad = append(bad, "args")
	}
	if len(bad) > 0 {
		writeError(w, r, model.CodeValidation,
			"the request body does not match op_request.json: "+strings.Join(bad, ", "),
			map[string]any{"fields": bad})
		return jobs.Request{}, false
	}
	return jobs.Request{
		Op:                  op,
		Tenant:              tenant,
		Args:                args,
		DryRun:              body.DryRun,
		IdempotencyKey:      body.IdempotencyKey,
		Confirm:             body.Confirm,
		ForceWithDoctorDiff: body.ForceWithDoctorDiff,
		Principal:           jobPrincipal(r),
		Mode:                model.WorkerDaemon,
	}, true
}

// decodeArgs reads the `args` member. An absent or null `args` is the empty
// object, which is what every no-argument verb sends; anything that is not an
// object is a validation failure rather than a planning input.
func decodeArgs(raw json.RawMessage) (map[string]any, error) {
	if len(raw) == 0 || string(raw) == "null" {
		return map[string]any{}, nil
	}
	var args map[string]any
	if err := json.Unmarshal(raw, &args); err != nil {
		return nil, err
	}
	if args == nil {
		args = map[string]any{}
	}
	return args, nil
}

// submit is the one path every mutation takes.
func (s *Server) submit(w http.ResponseWriter, r *http.Request, req jobs.Request) {
	if s.Engine == nil {
		s.engineNotWired(w, r)
		return
	}
	// The request line. Not an audit row: `args` are unredacted here and the
	// engine is what redacts and records them. Op, tenant and the request id
	// are enough to tie this line to the row the engine writes.
	s.log().Info("mutation",
		"request_id", req.Principal.RequestID,
		"op", req.Op, "tenant", req.Tenant,
		"principal", req.Principal.Subject, "dry_run", req.DryRun)

	plan, job, err := s.Engine.Submit(r.Context(), req)
	if err != nil {
		s.jobsError(w, r, plan, err)
		return
	}
	if req.DryRun {
		if plan == nil {
			writeError(w, r, model.CodeInternal, "the control plane produced no plan for a dry run", nil)
			return
		}
		writeJSON(w, http.StatusOK, plan)
		return
	}
	if job == nil {
		writeError(w, r, model.CodeInternal, "the control plane accepted no job", nil)
		return
	}
	w.Header().Set("Location", "/v1/jobs/"+job.ID)
	writeJSON(w, http.StatusAccepted, job)
}

// --------------------------------------------------------------------------
// Mutation handlers
// --------------------------------------------------------------------------

func (s *Server) handleTenantOp(w http.ResponseWriter, r *http.Request) {
	name, ok := tenantName(w, r)
	if !ok {
		return
	}
	verb := chi.URLParam(r, "verb")
	if !opVerbs[verb] {
		writeError(w, r, model.CodeValidation,
			fmt.Sprintf("verb %q is not one of the contract's operations", verb),
			map[string]any{"fields": []string{"verb"}})
		return
	}
	req, ok := s.buildRequest(w, r, verb, name)
	if !ok {
		return
	}
	s.submit(w, r, req)
}

// handleTenantCreate is POST /v1/tenants. The tenant is `args.name` — the
// path names no tenant, because the tenant does not exist yet.
//
// The name is COPIED onto the request, not validated here: create's args are
// create_request.json's CreateArgs, which the engine's `create` Op validates
// like every other verb's, and a second reader of that schema in the handler
// is the drift the one-reader rule exists to prevent. What the copy buys is
// that the engine's audit row and lock path know which tenant this is before
// the plan exists; a name that is not a tenant name at all is left empty and
// the engine answers the same 422 it answers for every other bad argument.
func (s *Server) handleTenantCreate(w http.ResponseWriter, r *http.Request) {
	req, ok := s.buildRequest(w, r, opCreate, "")
	if !ok {
		return
	}
	if name, _ := req.Args["name"].(string); tenantNameRE.MatchString(name) {
		req.Tenant = name
	}
	s.submit(w, r, req)
}

func (s *Server) handleGatewayApply(w http.ResponseWriter, r *http.Request) {
	req, ok := s.buildRequest(w, r, opGatewayApply, "")
	if !ok {
		return
	}
	s.submit(w, r, req)
}

// handleGatewayReload publishes NOTHING: it tests the live tree, HUPs,
// confirms the master survived and probes. It exists as its own operation
// because adopting a hand-written proxy change through `apply` would first
// overwrite it with a fresh render — the generation the registry describes,
// not the one an operator edited.
func (s *Server) handleGatewayReload(w http.ResponseWriter, r *http.Request) {
	req, ok := s.buildRequest(w, r, opGatewayReload, "")
	if !ok {
		return
	}
	s.submit(w, r, req)
}

// handleSettingsPut is the one mutation whose `args` the HTTP layer validates
// itself: the writable subset is a projection of settings_response.json, not
// a per-verb schema in x-ctl-op-args, so there is no Op schema for the engine
// to check it against.
func (s *Server) handleSettingsPut(w http.ResponseWriter, r *http.Request) {
	req, ok := s.buildRequest(w, r, opSettingsPut, "")
	if !ok {
		return
	}
	refused, bad := validateSettingsArgs(req.Args)
	if len(refused) > 0 {
		// 409, not 422: these members EXIST and are well formed. What is
		// refused is the caller — the contract makes them CLI-only /
		// server-owned — and a 422 would tell a client its document was
		// malformed and invite it to fix the spelling.
		writeError(w, r, model.CodeRefused,
			"CLI-only / server-owned: "+strings.Join(refused, ", ")+
				" may not be set over HTTP (recipients decides who can decrypt every future backup, "+
				"python_env_default is a trusted-operator change, and registry_generation is the server's own counter)",
			map[string]any{"fields": refused})
		return
	}
	if len(bad) > 0 {
		writeError(w, r, model.CodeValidation,
			"settings args are outside the writable subset of settings_response.json: "+strings.Join(bad, ", "),
			map[string]any{"fields": bad})
		return
	}
	s.submit(w, r, req)
}

// handleJobContinuation is resume, continue and cancel. The three differ only
// in which engine method they call: each RE-AUTHORIZES against the principal
// of THIS request, never against the one stored on the job, which is what
// makes a revoked operator's parked job unresumable by the credential that
// created it.
func (s *Server) handleJobContinuation(op string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		id, ok := jobID(w, r)
		if !ok {
			return
		}
		// The envelope is still validated: the contract gives these routes an
		// OpRequest body and the same 422, and an idempotency key on a
		// continuation is what stops a double-clicked "resume" from starting
		// the same step twice.
		req, ok := s.buildRequest(w, r, op, "")
		if !ok {
			return
		}
		if req.DryRun {
			// The contract lists a 200 Plan for these three, and the engine
			// seam (jobs.Engine) gives Resume/Continue/Cancel no way to
			// produce one: they act on a job that already carries its plan,
			// and the plan of a continuation is the plan of that job. Rather
			// than invent a second planning path here — which would compute a
			// plan the engine would not execute — the dry run is refused and
			// the detail says where the plan actually is.
			//
			// TODO(integration): if the engine grows a planning continuation,
			// route this through it instead of refusing.
			writeError(w, r, model.CodeRefused,
				"a continuation has no dry run: "+op+" acts on job "+id+
					", whose plan is already recorded — read it with GET /v1/jobs/"+id, nil)
			return
		}
		if s.Engine == nil {
			s.engineNotWired(w, r)
			return
		}
		p := jobPrincipal(r)
		s.log().Info("mutation", "request_id", p.RequestID, "op", op, "job", id, "principal", p.Subject)

		var (
			job *model.Job
			err error
		)
		switch op {
		case opJobResume:
			job, err = s.Engine.Resume(r.Context(), id, p)
		case opJobContinue:
			job, err = s.Engine.Continue(r.Context(), id, p)
		case opJobCancel:
			// The envelope's `confirm` is not decoration on this route:
			// openapi.yaml requires it when the cancel would roll back
			// succeeded steps, and the engine is what decides whether it
			// would. Dropping it here made every such cancel unconfirmable.
			job, err = s.Engine.Cancel(r.Context(), id, p, req.Confirm)
		default:
			writeError(w, r, model.CodeInternal, "unknown job continuation", nil)
			return
		}
		if err != nil {
			s.jobsError(w, r, nil, err)
			return
		}
		if job == nil {
			writeError(w, r, model.CodeInternal, "the control plane accepted no job", nil)
			return
		}
		w.Header().Set("Location", "/v1/jobs/"+job.ID)
		writeJSON(w, http.StatusAccepted, job)
	}
}

// --------------------------------------------------------------------------
// Job reads
// --------------------------------------------------------------------------

// reduceJob applies ctlJobsList's viewer_fields: "worker, lock null;
// reservations, steps[].external_ids empty; steps[].log null".
//
// It reduces a COPY. The engine hands back records it may hold in its own
// cache, and a reduction that mutated them would make the next operator's
// read of the same job return the viewer's shape — the kind of leak that runs
// the wrong way and is therefore never noticed.
func reduceJob(j model.Job) model.Job {
	j.Worker = nil
	j.Lock = nil
	j.Reservations = []model.Reservation{}
	steps := make([]model.Step, len(j.Steps))
	copy(steps, j.Steps)
	for i := range steps {
		steps[i].ExternalIDs = []string{}
		steps[i].Log = ""
	}
	j.Steps = steps
	return j
}

// normalizeJob fills the members job.json requires to be PRESENT even when
// they are empty. A nil slice marshals to `null` and the schema says
// `reservations` and `steps` are arrays, so an engine that left them nil
// would produce a body its own contract test rejects.
func normalizeJob(j model.Job) model.Job {
	if j.Reservations == nil {
		j.Reservations = []model.Reservation{}
	}
	if j.Steps == nil {
		j.Steps = []model.Step{}
	}
	steps := make([]model.Step, len(j.Steps))
	copy(steps, j.Steps)
	for i := range steps {
		if steps[i].ExternalIDs == nil {
			steps[i].ExternalIDs = []string{}
		}
	}
	j.Steps = steps
	return j
}

func presentJob(j model.Job, viewer bool) model.Job {
	j = normalizeJob(j)
	if viewer {
		j = reduceJob(j)
	}
	return j
}

func (s *Server) handleJobs(w http.ResponseWriter, r *http.Request) {
	limit, ok := intQuery(w, r, "limit", 50, 1, 500)
	if !ok {
		return
	}
	state := r.URL.Query().Get("state")
	if state != "" && !jobStates[state] {
		writeError(w, r, model.CodeValidation, fmt.Sprintf("state %q is not a job state", state),
			map[string]any{"fields": []string{"state"}})
		return
	}
	tenant := r.URL.Query().Get("tenant")
	if tenant != "" && !tenantNameRE.MatchString(tenant) {
		writeError(w, r, model.CodeValidation,
			fmt.Sprintf("tenant %q is outside ^[a-z][a-z0-9-]{0,31}$", tenant),
			map[string]any{"fields": []string{"tenant"}})
		return
	}
	if s.Engine == nil {
		// A read, not a mutation: the honest answer on a daemon with no
		// engine is that it has run no jobs. The mutation routes are where
		// "not wired" is reported, because that is where it changes an
		// answer a caller would otherwise act on.
		writeJSON(w, http.StatusOK, model.JobsResponse{Jobs: []model.Job{}, Limit: limit, Truncated: false})
		return
	}
	list, truncated, err := s.Engine.List(r.Context(), jobs.ListFilter{
		Tenant: tenant, State: model.JobState(state), Limit: limit,
	})
	if err != nil {
		s.jobsError(w, r, nil, err)
		return
	}
	viewer := isViewer(r)
	out := make([]model.Job, 0, len(list))
	for _, j := range list {
		out = append(out, presentJob(j, viewer))
	}
	writeJSON(w, http.StatusOK, model.JobsResponse{Jobs: out, Limit: limit, Truncated: truncated})
}

func (s *Server) handleJob(w http.ResponseWriter, r *http.Request) {
	id, ok := jobID(w, r)
	if !ok {
		return
	}
	if s.Engine == nil {
		writeError(w, r, model.CodeNotFound, "no job "+id, nil)
		return
	}
	job, err := s.Engine.Get(r.Context(), id)
	if err != nil {
		s.jobsError(w, r, nil, err)
		return
	}
	if job == nil {
		writeError(w, r, model.CodeNotFound, "no job "+id, nil)
		return
	}
	if !isViewer(r) {
		// An operator's job names each step's log: the contract's `log` is
		// the relative path under /rag/data/ctl/jobs/<id>/ that the
		// steps/{n}/log endpoint serves (redacted), null when the step wrote
		// nothing; the viewer reduction nulls it always.
		for i := range job.Steps {
			if text, err := s.Engine.StepLog(r.Context(), id, job.Steps[i].N); err == nil && text != "" {
				job.Steps[i].Log = model.NullString(fmt.Sprintf("steps/%d.log", job.Steps[i].N))
			}
		}
	}
	writeJSON(w, http.StatusOK, presentJob(*job, isViewer(r)))
}

func (s *Server) handleJobStepLog(w http.ResponseWriter, r *http.Request) {
	id, ok := jobID(w, r)
	if !ok {
		return
	}
	n, err := strconv.Atoi(chi.URLParam(r, "n"))
	if err != nil || n < 1 {
		writeError(w, r, model.CodeValidation, "the step number must be a positive integer",
			map[string]any{"fields": []string{"n"}})
		return
	}
	lines, ok := intQuery(w, r, "lines", 200, 1, model.MaxLogLines)
	if !ok {
		return
	}
	if s.Engine == nil {
		writeError(w, r, model.CodeNotFound, "no job "+id, nil)
		return
	}
	text, err := s.Engine.StepLog(r.Context(), id, n)
	if err != nil {
		s.jobsError(w, r, nil, err)
		return
	}
	all := splitLogLines(text)
	truncated := false
	if len(all) > lines {
		all = all[len(all)-lines:]
		truncated = true
	}
	writeJSON(w, http.StatusOK, model.StepLogResponse{
		JobID: id, Step: n, Lines: all,
		Requested: lines, Returned: len(all), Truncated: truncated,
		// The engine redacts every line before it is stored; the constant
		// says the property holds for this body, as it does for LogsResponse.
		Redacted: true,
	})
}

func splitLogLines(text string) []string {
	text = strings.TrimSuffix(text, "\n")
	if text == "" {
		return []string{}
	}
	return strings.Split(text, "\n")
}

// handleJobSecrets is the one response in this API that carries a secret
// value.
//
// A session cannot reach it at all: the matrix row is `session: false` and
// the guard refuses before this handler runs — which also means that
// particular 403 carries no Cache-Control, because it is written by the gate
// and never gets here. That is what the contract asks for (it attaches
// `no-store` to the 200) and it is not a leak: the gate's body is a refusal,
// not an envelope.
//
// Within the handler the header is set BEFORE the engine is consulted, so
// every answer this route itself produces is no-store — including the 410 of
// an already-delivered envelope, whose body names a delivery time that is
// not worth leaving in a proxy cache either.
func (s *Server) handleJobSecrets(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	id, ok := jobID(w, r)
	if !ok {
		return
	}
	if s.Engine == nil {
		writeError(w, r, model.CodeNotFound, "no job "+id, nil)
		return
	}
	resp, err := s.Engine.Secrets(r.Context(), id, jobPrincipal(r))
	if err != nil {
		s.jobsError(w, r, nil, err)
		return
	}
	if resp == nil {
		writeError(w, r, model.CodeNotFound, "job "+id+" minted nothing", nil)
		return
	}
	writeJSON(w, http.StatusOK, resp)
}

func (s *Server) handleAudit(w http.ResponseWriter, r *http.Request) {
	limit, ok := intQuery(w, r, "limit", 100, 1, 1000)
	if !ok {
		return
	}
	tenant := r.URL.Query().Get("tenant")
	if tenant != "" && !tenantNameRE.MatchString(tenant) {
		writeError(w, r, model.CodeValidation,
			fmt.Sprintf("tenant %q is outside ^[a-z][a-z0-9-]{0,31}$", tenant),
			map[string]any{"fields": []string{"tenant"}})
		return
	}
	if s.Engine == nil {
		writeJSON(w, http.StatusOK, model.AuditResponse{Rows: []model.AuditRow{}, Limit: limit, Truncated: false})
		return
	}
	rows, truncated, err := s.Engine.Audit(r.Context(), limit)
	if err != nil {
		s.jobsError(w, r, nil, err)
		return
	}
	out := make([]model.AuditRow, 0, len(rows))
	for _, row := range rows {
		if tenant != "" && string(row.Tenant) != tenant {
			continue
		}
		if row.ArgsRedacted == nil {
			row.ArgsRedacted = map[string]any{}
		}
		out = append(out, row)
	}
	writeJSON(w, http.StatusOK, model.AuditResponse{Rows: out, Limit: limit, Truncated: truncated})
}

// --------------------------------------------------------------------------
// PUT /v1/settings — the writable subset
// --------------------------------------------------------------------------

// settingsWritable is settings_response.json minus what the contract makes
// read-only over HTTP. `recipients` decides who can DECRYPT a backup and
// `registry_generation` is the server's own counter, so neither is a value a
// browser may send; both are CLI-only, trusted-operator changes.
var settingsWritable = map[string]bool{
	"retention": true, "images": true, "ctl": true,
}

// settingsCLIOnly is the rest of settings_response.json's top level: members
// that exist, are well formed, and are still not a browser's to send.
//
// `recipients` decides who can DECRYPT every future backup; `python_env_default`
// re-points the interpreter every tenant API is started with — a
// trusted-operator change, and one nothing over HTTP should be able to make;
// `registry_generation` is the server's own counter, and a caller that could
// forge it could forge the generation a plan is validated against.
//
// python_env_default used to be in settingsWritable, so the handler ACCEPTED
// it while the contract says 409 — the one of the three that actually got
// through.
var settingsCLIOnly = map[string]bool{
	"recipients": true, "python_env_default": true, "registry_generation": true,
}

// validateSettingsArgs splits the args into the members that are refused
// (409 `refused`: CLI-only / server-owned) and the ones that are invalid
// (422 `validation`: unknown, or malformed), either list empty when there is
// nothing of that kind.
//
// Partial by design: `PUT /v1/settings {"ctl": {"gateway_enabled": true}}` is
// the flip that mattered on coconut, and demanding the whole document for it
// would make every settings change a read-modify-write race against the
// registry generation.
func validateSettingsArgs(args map[string]any) (refused, bad []string) {
	for key := range args {
		switch {
		case settingsCLIOnly[key]:
			refused = append(refused, key)
		case !settingsWritable[key]:
			bad = append(bad, key)
		}
	}
	if v, ok := args["retention"]; ok {
		bad = append(bad, validateRetention(v)...)
	}
	if v, ok := args["images"]; ok {
		bad = append(bad, validateImages(v)...)
	}
	if v, ok := args["ctl"]; ok {
		bad = append(bad, validateCtlSettings(v)...)
	}
	sortStrings(refused)
	sortStrings(bad)
	return refused, bad
}

func validateRetention(v any) []string {
	obj, ok := v.(map[string]any)
	if !ok {
		return []string{"retention"}
	}
	var bad []string
	for key, val := range obj {
		switch key {
		case "keep_last":
			inner, ok := val.(map[string]any)
			if !ok {
				bad = append(bad, "retention.keep_last")
				continue
			}
			for k, n := range inner {
				if k != "backup" && k != "pre_update" {
					bad = append(bad, "retention.keep_last."+k)
					continue
				}
				if !positiveInt(n) {
					bad = append(bad, "retention.keep_last."+k)
				}
			}
		case "keep_partial_hours":
			if !positiveInt(val) {
				bad = append(bad, "retention.keep_partial_hours")
			}
		case "auto_delete":
			// `const: false` in the schema: v1 never deletes a bundle on its
			// own, so "true" is not a setting that exists to be chosen.
			if b, ok := val.(bool); !ok || b {
				bad = append(bad, "retention.auto_delete")
			}
		default:
			bad = append(bad, "retention."+key)
		}
	}
	return bad
}

func validateImages(v any) []string {
	obj, ok := v.(map[string]any)
	if !ok {
		return []string{"images"}
	}
	var bad []string
	for name, val := range obj {
		if name != "qdrant" && name != "elasticsearch" {
			bad = append(bad, "images."+name)
			continue
		}
		image, ok := val.(map[string]any)
		if !ok {
			bad = append(bad, "images."+name)
			continue
		}
		for field, fv := range image {
			switch field {
			case "sif":
				if !absPathValue(fv) {
					bad = append(bad, "images."+name+".sif")
				}
			case "version":
				if s, ok := fv.(string); !ok || s == "" {
					bad = append(bad, "images."+name+".version")
				}
			case "digest":
				// Pinned by digest: choosing an image over HTTP means
				// choosing among SIFs already on disk, never fetching one.
				if s, ok := fv.(string); !ok || !digestRE.MatchString(s) {
					bad = append(bad, "images."+name+".digest")
				}
			default:
				bad = append(bad, "images."+name+"."+field)
			}
		}
	}
	return bad
}

func validateCtlSettings(v any) []string {
	obj, ok := v.(map[string]any)
	if !ok {
		return []string{"ctl"}
	}
	var bad []string
	for key, val := range obj {
		switch key {
		case "port":
			n, ok := intValue(val)
			if !ok || n < 1024 || n > 65535 {
				bad = append(bad, "ctl.port")
			}
		case "ui_dist":
			if !absPathValue(val) {
				bad = append(bad, "ctl.ui_dist")
			}
		case "gateway_enabled":
			if _, ok := val.(bool); !ok {
				bad = append(bad, "ctl.gateway_enabled")
			}
		default:
			bad = append(bad, "ctl."+key)
		}
	}
	return bad
}

// absPathRE is registry.json#/$defs/AbsPath.
var absPathRE = regexp.MustCompile(`^/[A-Za-z0-9._/-]*$`)

func absPathValue(v any) bool {
	s, ok := v.(string)
	return ok && absPathRE.MatchString(s)
}

// intValue accepts what encoding/json produces for a JSON integer. A float
// with a fractional part is NOT an integer: 1024.5 as a port is a value the
// schema rejects, and truncating it here would accept what the contract does
// not.
func intValue(v any) (int, bool) {
	f, ok := v.(float64)
	if !ok || f != float64(int(f)) {
		return 0, false
	}
	return int(f), true
}

func positiveInt(v any) bool {
	n, ok := intValue(v)
	return ok && n >= 1
}

// sortStrings is an insertion sort over a list that is never long: the
// offending-field list of one request. It exists so `extra.fields` is stable
// across runs — a test that asserted on map iteration order would pass and
// fail for no reason.
func sortStrings(s []string) {
	for i := 1; i < len(s); i++ {
		for j := i; j > 0 && s[j] < s[j-1]; j-- {
			s[j], s[j-1] = s[j-1], s[j]
		}
	}
}
