package jobs

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"regexp"
	"sync"
	"syscall"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// The state machine. Every rule it enforces is the plan's, and each one is
// there because of a way a control plane loses data:
//
//   - A dry run writes nothing and locks nothing, so "show me" is always safe.
//   - The idempotency key is persisted BEFORE the work starts, so the retry of
//     a lost 202 joins the original job instead of starting a second one.
//   - The plan is recomputed UNDER the locks and compared, so what an operator
//     approved is what runs — not what the fleet drifted into meanwhile.
//   - A step records its external ids BEFORE the call that creates them, so a
//     crash in between leaves a record reconcile can act on.
//   - An interrupted job KEEPS its reservations, so nothing else takes the port
//     block a half-finished create was holding.
//   - Every continuation is re-authorized against the CALLER, never against the
//     principal stored on the job.

// EngineOptions configures NewEngine. Store, Ops and Roots are required;
// everything else has a working default.
type EngineOptions struct {
	Store Store
	Ops   Registry
	Roots paths.Roots
	// RegistryPath is registry.json; used by the default LoadFleet.
	RegistryPath string
	// LoadFleet reads a fresh registry snapshot. The engine calls it once per
	// plan — including the re-plan under the locks, which is the whole point:
	// a generation that moved in between is what plan_stale detects.
	LoadFleet func() (*registry.Fleet, error)
	Drivers   Drivers
	Redactor  Redactor
	// Doctor runs the op-scoped preconditions.
	Doctor func(ctx context.Context, tenant, op string) (model.DoctorResponse, error)
	Now    func() time.Time
	Host   string
	Mode   model.WorkerMode
	// SecretsTTL is the delivery envelope's lifetime (contract: 15 minutes).
	SecretsTTL time.Duration
	// Sealer encrypts a minted secret payload for rest (age, PR-D). When it
	// is nil, or returns false, the payload is held in daemon memory only and
	// dies with the process — the contract's "or held only in daemon memory
	// when no recipients are configured".
	Sealer func(plaintext []byte) (sealed []byte, sealedOK bool)
	// Unsealer is required to deliver an envelope a Sealer sealed.
	Unsealer func(sealed []byte) ([]byte, error)
	Logger   *slog.Logger
}

// engine is the Engine implementation. NewEngine returns it behind the
// interface; the tests reach the concrete type for wait().
type engine struct {
	o     EngineOptions
	locks Locks
	ids   ulidGen

	mu sync.Mutex
	// active are the jobs this process is running or parking. A parked
	// (awaiting_cutover) run KEEPS its locks and its planned steps here,
	// which is what lets Continue pick up exactly where it stopped.
	active map[string]*run
	// mem holds the plaintext of memory-only envelopes (no Sealer): the
	// contract's alternative to encryption at rest, and it must not touch
	// the database.
	mem map[string][]byte
}

// run is one job executing in this process.
type run struct {
	job     *model.Job
	planned *Planned
	// oc is the planning context the steps execute against: the registry
	// snapshot the plan was computed from, the tenant row, the doctor's
	// verdict and the drivers.
	oc     Context
	locks  *LockSet
	cancel context.CancelFunc
	// cancelled records that a human asked for the stop, so the terminal
	// state is `cancelled` rather than `failed`.
	cancelled bool
}

// NewEngine builds the engine. It does not touch the database or the host.
func NewEngine(o EngineOptions) Engine {
	if o.Now == nil {
		o.Now = time.Now
	}
	if o.Redactor == nil {
		o.Redactor = nopRedactor{}
	}
	if o.SecretsTTL <= 0 {
		o.SecretsTTL = 15 * time.Minute
	}
	if o.Host == "" {
		o.Host, _ = os.Hostname()
	}
	if o.Mode == "" {
		o.Mode = model.WorkerDaemon
	}
	if o.Logger == nil {
		o.Logger = slog.New(slog.NewTextHandler(io.Discard, nil))
	}
	if o.LoadFleet == nil {
		path := o.RegistryPath
		if path == "" {
			path = o.Roots.Registry()
		}
		o.LoadFleet = func() (*registry.Fleet, error) { return registry.Load(path) }
	}
	if o.Doctor == nil {
		// No doctor wired: green with an empty finding set. The hash is over
		// the same shape a real doctor produces, so a plan made without one
		// still has a stable, comparable digest.
		o.Doctor = func(ctx context.Context, tenant, op string) (model.DoctorResponse, error) {
			return model.DoctorResponse{
				Status:      model.StatusGreen,
				Hash:        sha256Of([]byte("no-doctor")),
				GeneratedAt: o.Now().UTC().Format(time.RFC3339),
				Scope:       model.Scope{Tenant: model.NullString(tenant), Op: model.NullString(op)},
				Findings:    []model.Finding{},
			}, nil
		}
	}
	return &engine{
		o:      o,
		locks:  NewLocks(o.Roots),
		active: map[string]*run{},
		mem:    map[string][]byte{},
	}
}

// nopRedactor is the default when no redactor is wired. It is deliberately
// NOT a "redact everything" stub: the engine's contract is that whatever the
// Redactor returns is what gets stored, and a test that wires no redactor is
// testing the transitions, not the redaction.
type nopRedactor struct{}

func (nopRedactor) Redact(s string) string                     { return s }
func (nopRedactor) RedactArgs(a map[string]any) map[string]any { return a }

// ---------------------------------------------------------------- submit

// Submit is the one entry point for every mutation.
func (e *engine) Submit(ctx context.Context, req Request) (*model.Plan, *model.Job, error) {
	if req.Principal.Role != "operator" {
		return nil, nil, fmt.Errorf("%w: %s is not an operator", ErrForbidden, req.Principal.Subject)
	}
	op, ok := e.o.Ops.Lookup(req.Op)
	if !ok {
		return nil, nil, fmt.Errorf("%w: no such operation %q", ErrNotFound, req.Op)
	}
	if err := op.Validate(req.Args); err != nil {
		return nil, nil, fmt.Errorf("%w: %s", ErrValidation, err)
	}

	plan, planned, oc, err := e.plan(ctx, op, req)
	if err != nil {
		return nil, nil, err
	}
	argsRedacted := oc.Redactor.RedactArgs(req.Args)

	if req.DryRun {
		// Nothing persisted, nothing locked — a dry run is a read.
		return plan, nil, nil
	}
	if req.IdempotencyKey == "" {
		return nil, nil, fmt.Errorf("%w: idempotency_key is required unless dry_run", ErrValidation)
	}

	// The refusals, in the order the contract states them. Each writes a
	// single `intent` audit row saying why — a refusal is a thing that
	// happened, and an operator asking "why did nothing run?" should find it.
	if plan.RequiresConfirm && req.Confirm != string(plan.ConfirmValue) {
		err := refuse(fmt.Errorf("%w: this operation needs confirm=%q", ErrConfirmRequired, string(plan.ConfirmValue)),
			map[string]any{"confirm_value": string(plan.ConfirmValue)})
		e.auditRefusal(ctx, req, argsRedacted, plan.PlanHash, err)
		// The plan travels WITH the refusal: a client that must now confirm
		// needs to see exactly what it is confirming.
		return plan, nil, err
	}
	if err := gateOnDoctor(plan.Doctor, req.ForceWithDoctorDiff); err != nil {
		e.auditRefusal(ctx, req, argsRedacted, plan.PlanHash, err)
		return plan, nil, err
	}

	now := e.o.Now().UTC()
	job := e.newJob(req, plan, now)
	fp, err := Fingerprint(req.Op, req.Tenant, req.Args, plan.PlanHash)
	if err != nil {
		return nil, nil, err
	}
	stored, err := e.o.Store.Create(ctx, job, fp)
	if err != nil {
		if errors.Is(err, ErrDuplicate) {
			e.auditRefusal(ctx, req, argsRedacted, plan.PlanHash, err)
			return plan, nil, err
		}
		return nil, nil, err
	}
	if stored.ID != job.ID {
		// The same request under the same key: the original job, as stored.
		return plan, stored, nil
	}
	if as, ok := e.o.Store.(ArgsStore); ok {
		if err := as.PutArgs(ctx, job.ID, argsRedacted); err != nil {
			e.o.Logger.Error("remembering the job's redacted args", "job", job.ID, "err", err)
		}
	}
	e.audit(ctx, model.AuditRow{
		At: now.Format(time.RFC3339), Phase: model.AuditIntent,
		Principal: req.Principal.Subject, AuthMethod: req.Principal.Method,
		SudoUser: model.NullString(req.Principal.SudoUser), RequestID: job.RequestID,
		Op: req.Op, Tenant: model.NullString(req.Tenant), JobID: model.NullString(job.ID),
		ArgsRedacted: argsRedacted, PlanHash: model.NullString(plan.PlanHash),
		Outcome: "accepted",
	})

	// Clone BEFORE the worker starts: from here on the job document belongs
	// to the run goroutine, and the caller gets a snapshot, not a view.
	accepted := cloneJob(job)
	e.start(job, op, req, planned, oc, argsRedacted, 0)
	return plan, accepted, nil
}

// plan builds the Context, runs the op's planner and completes the Plan with
// the facts the ENGINE owns (op, tenant, generation, doctor, confirm, hash) —
// an op cannot forge them.
func (e *engine) plan(ctx context.Context, op Op, req Request) (*model.Plan, *Planned, Context, error) {
	fleet, err := e.o.LoadFleet()
	if err != nil {
		return nil, nil, Context{}, fmt.Errorf("loading the registry: %w", err)
	}
	var tenant *registry.Tenant
	if req.Tenant != "" {
		t, ok := fleet.Tenants[req.Tenant]
		if !ok || t == nil {
			return nil, nil, Context{}, fmt.Errorf("%w: no tenant %q in the registry", ErrNotFound, req.Tenant)
		}
		tenant = t
	}
	doctor, err := e.o.Doctor(ctx, req.Tenant, req.Op)
	if err != nil {
		return nil, nil, Context{}, fmt.Errorf("running the op-scoped doctor: %w", err)
	}
	oc := Context{
		Roots: e.o.Roots, Fleet: fleet, Tenant: tenant, Doctor: doctor,
		Drivers: e.o.Drivers, Now: e.o.Now, Redactor: e.o.Redactor,
	}
	planned, err := op.Plan(ctx, oc, req.Args)
	if err != nil {
		return nil, nil, oc, err
	}
	if planned == nil {
		return nil, nil, oc, fmt.Errorf("op %q planned nothing", req.Op)
	}
	if len(planned.Steps) != len(planned.Plan.Steps) {
		return nil, nil, oc, fmt.Errorf("op %q planned %d steps but %d executable halves",
			req.Op, len(planned.Plan.Steps), len(planned.Steps))
	}

	p := planned.Plan
	p.Op = req.Op
	p.Tenant = model.NullString(req.Tenant)
	p.RegistryGeneration = fleet.Generation
	p.Doctor = doctor
	if p.SchemaVersion == 0 {
		p.SchemaVersion = fleet.SchemaVersion
	}
	if p.SchemaVersion == 0 {
		p.SchemaVersion = 1
	}
	if op.Destructive() {
		// A destructive op names its subject, exactly like the CLI's
		// `--yes-destructive <name>`: typing "yes" to "decommission dev" is
		// not evidence you read which tenant it said.
		p.RequiresConfirm = true
		switch {
		case req.Tenant != "":
			p.ConfirmValue = model.NullString(req.Tenant)
		case p.ConfirmValue == "":
			p.ConfirmValue = "yes"
		}
	} else if p.RequiresConfirm && p.ConfirmValue == "" {
		p.ConfirmValue = "yes"
	}
	if !p.RequiresConfirm {
		p.ConfirmValue = ""
	}
	normalizePlan(&p)
	hash, err := PlanHash(p, e.o.Redactor.RedactArgs(req.Args))
	if err != nil {
		return nil, nil, oc, err
	}
	p.PlanHash = hash
	planned.Plan = p
	return &p, planned, oc, nil
}

// gateOnDoctor is the doctor precondition: red never runs; yellow runs only
// when the caller quotes the hash of the findings they saw, which is what
// makes "I accepted these warnings" a checkable statement rather than a flag.
func gateOnDoctor(d model.DoctorResponse, force string) error {
	switch d.Status {
	case model.StatusRed:
		return refuse(fmt.Errorf("%w: the op-scoped doctor is red (%s); a red finding is never forced",
			ErrDoctorRed, d.Hash), map[string]any{"doctor_hash": d.Hash, "status": string(model.StatusRed)})
	case model.StatusYellow:
		if force != d.Hash {
			return refuse(fmt.Errorf("%w: yellow; pass force_with_doctor_diff=%s", ErrDoctorRed, d.Hash),
				map[string]any{"doctor_hash": d.Hash, "status": string(model.StatusYellow)})
		}
	}
	return nil
}

func (e *engine) newJob(req Request, plan *model.Plan, now time.Time) *model.Job {
	job := &model.Job{
		ID:             e.ids.new(now),
		Op:             req.Op,
		Tenant:         model.NullString(req.Tenant),
		Principal:      req.Principal.Subject,
		AuthMethod:     req.Principal.Method,
		SudoUser:       model.NullString(req.Principal.SudoUser),
		State:          model.JobQueued,
		PlanHash:       plan.PlanHash,
		RequestID:      normalizeRequestID(req.Principal.RequestID),
		IdempotencyKey: req.IdempotencyKey,
		CreatedAt:      now.Format(time.RFC3339),
		Reservations:   []model.Reservation{},
		Steps:          make([]model.Step, 0, len(plan.Steps)),
	}
	if job.AuthMethod == "" {
		job.AuthMethod = model.AuthLocal
	}
	for _, s := range plan.Steps {
		job.Steps = append(job.Steps, model.Step{
			N: s.N, Kind: s.Kind, Title: s.Title, State: model.StepPending,
			ExternalIDs: []string{},
		})
	}
	return job
}

// ---------------------------------------------------------------- execution

// start launches the job's worker goroutine and registers it as active.
func (e *engine) start(job *model.Job, op Op, req Request, planned *Planned, oc Context, argsRedacted map[string]any, from int) {
	ctx, cancel := context.WithCancel(context.Background())
	r := &run{job: job, planned: planned, oc: oc, cancel: cancel}
	e.mu.Lock()
	e.active[job.ID] = r
	e.mu.Unlock()
	go e.execute(ctx, r, op, req, argsRedacted, from)
}

// execute takes the locks, re-plans under them, and runs the steps.
func (e *engine) execute(ctx context.Context, r *run, op Op, req Request, argsRedacted map[string]any, from int) {
	started := e.o.Now().UTC()
	job := r.job

	holder := LockHolder{JobID: job.ID, PID: os.Getpid(), Since: started.Format(time.RFC3339)}
	set, err := e.locks.Take(r.planned.Locks, req.Tenant, holder, started)
	if err != nil {
		err = lockRefusal(err)
		var le *LockedError
		code := "locked"
		if !errors.As(err, &le) {
			code = "lock_failed"
		}
		e.finish(ctx, r, model.JobFailed, &model.JobError{Code: code, Detail: err.Error()}, argsRedacted, req, started)
		return
	}
	r.locks = set

	job.Worker = &model.JobWorker{PID: os.Getpid(), Host: e.o.Host, Mode: e.o.Mode}
	job.Lock = &model.JobLock{Order: set.Names(), Since: started.Format(time.RFC3339)}
	job.State = model.JobRunning
	job.StartedAt = model.NullString(started.Format(time.RFC3339))
	e.save(ctx, job)

	// Re-plan UNDER the locks. Until the locks were held, the registry could
	// move between the plan an operator approved and the work; now it cannot,
	// so this comparison is the last moment the two can differ.
	_, replanned, roc, err := e.plan(ctx, op, req)
	if err != nil {
		e.finish(ctx, r, model.JobFailed, &model.JobError{Code: "plan_failed", Detail: err.Error()}, argsRedacted, req, started)
		return
	}
	if replanned.Plan.PlanHash != job.PlanHash {
		err := refuse(fmt.Errorf("%w: the plan moved between submission (%s) and the locks (%s)",
			ErrPlanStale, job.PlanHash, replanned.Plan.PlanHash),
			map[string]any{
				"plan_hash":           replanned.Plan.PlanHash,
				"registry_generation": replanned.Plan.RegistryGeneration,
			})
		e.finish(ctx, r, model.JobFailed, &model.JobError{Code: "plan_stale", Detail: err.Error()}, argsRedacted, req, started)
		return
	}
	r.planned = replanned
	r.oc = roc

	e.runSteps(ctx, r, req, argsRedacted, started, from)
}

// runSteps executes steps [from, len) and settles the job.
func (e *engine) runSteps(ctx context.Context, r *run, req Request, argsRedacted map[string]any, started time.Time, from int) {
	job := r.job
	steps := r.planned.Steps

	for i := from; i < len(steps); i++ {
		if ctx.Err() != nil {
			e.rollbackAndSettle(r, req, argsRedacted, started, i-1, model.JobCancelled,
				&model.JobError{Step: stepNo(i + 1), Code: "cancelled", Detail: "cancelled by an operator"})
			return
		}
		st := &job.Steps[i]
		st.State = model.StepRunning
		st.Attempts++
		st.StartedAt = model.NullString(e.o.Now().UTC().Format(time.RFC3339))
		st.Error = ""
		n := st.N
		job.CurrentStep = &n
		e.save(ctx, job)

		sc := e.stepContext(ctx, r, i)
		log, err := steps[i].Run(ctx, sc)
		if log != "" {
			e.appendLog(ctx, job, st, log)
		}
		if err != nil || ctx.Err() != nil {
			if err == nil {
				err = ctx.Err()
			}
			st.State = model.StepFailed
			st.FinishedAt = model.NullString(e.o.Now().UTC().Format(time.RFC3339))
			st.Error = model.NullString(e.o.Redactor.Redact(err.Error()))
			e.save(ctx, job)

			state := model.JobFailed
			code := "step_failed"
			if e.wasCancelled(r) || errors.Is(err, context.Canceled) {
				state, code = model.JobCancelled, "cancelled"
			}
			e.rollbackAndSettle(r, req, argsRedacted, started, i-1, state,
				&model.JobError{Step: stepNo(st.N), Code: code, Detail: e.o.Redactor.Redact(err.Error())})
			return
		}
		st.State = model.StepSucceeded
		st.FinishedAt = model.NullString(e.o.Now().UTC().Format(time.RFC3339))
		e.save(ctx, job)

		if steps[i].Cutover && i < len(steps)-1 {
			// The parked state. The locks stay HELD and the run stays
			// registered: a handover that has cut traffic over but not yet
			// committed must not let anything else touch this tenant.
			job.State = model.JobAwaitingCutover
			e.save(ctx, job)
			e.o.Logger.Info("job parked awaiting cutover", "job", job.ID, "step", st.N)
			return
		}
	}

	// Success.
	if r.planned.Result != nil {
		job.Result = r.planned.Result()
	}
	if r.planned.Secrets != nil {
		if secrets := r.planned.Secrets(); len(secrets) > 0 {
			if err := e.putEnvelope(ctx, job, secrets); err != nil {
				e.o.Logger.Error("sealing the delivery envelope", "job", job.ID, "err", err)
			}
		}
	}
	e.finish(ctx, r, model.JobSucceeded, nil, argsRedacted, req, started)
}

// stepContext builds the per-step view, including the two durability hooks a
// step may use.
func (e *engine) stepContext(ctx context.Context, r *run, i int) *StepContext {
	job := r.job
	st := &job.Steps[i]
	return &StepContext{
		Job: job, Step: st, Ops: r.oc,
		Checkpoint: func(ids ...string) error {
			// Durable BEFORE the external call: the ids have to be readable
			// by the next process to open this database even if this one dies
			// on the very next instruction.
			st.ExternalIDs = append(st.ExternalIDs, ids...)
			st.Checkpoint = true
			return e.o.Store.Update(ctx, job)
		},
		Reserve: func(resource string, until *time.Time) error {
			res := model.Reservation{Resource: resource}
			if until != nil {
				res.Until = model.NullString(until.UTC().Format(time.RFC3339))
			}
			job.Reservations = append(job.Reservations, res)
			if rs, ok := e.o.Store.(ReservationStore); ok {
				if err := rs.PutReservation(ctx, job.ID, res, e.o.Now()); err != nil {
					return err
				}
			}
			return e.o.Store.Update(ctx, job)
		},
		Logf: func(format string, args ...any) {
			e.appendLog(ctx, job, st, fmt.Sprintf(format, args...))
		},
	}
}

// appendLog redacts, then appends. The redactor runs HERE, once, on the way
// into the store: a log line that reached the database unredacted cannot be
// unwritten.
func (e *engine) appendLog(ctx context.Context, job *model.Job, st *model.Step, text string) {
	if text == "" {
		return
	}
	if err := e.o.Store.AppendStepLog(ctx, job.ID, st.N, e.o.Redactor.Redact(text)); err != nil {
		e.o.Logger.Error("appending a step log", "job", job.ID, "step", st.N, "err", err)
		return
	}
	st.Log = model.NullString(fmt.Sprintf("steps/%d.log", st.N))
}

// rollbackAndSettle undoes the steps that DID succeed, newest first, and
// settles the job. Reverse order is the only order that can work: step 3
// cannot be undone while step 4's effects still depend on it.
func (e *engine) rollbackAndSettle(r *run, req Request, argsRedacted map[string]any, started time.Time, lastSucceeded int, state model.JobState, jobErr *model.JobError) {
	// A fresh context: the cancellation that got us here must not also cancel
	// the undo.
	ctx := context.Background()
	job := r.job
	rb := &model.JobRollback{Attempted: false, State: model.RollbackNotNeeded}

	var failed, done int
	for i := lastSucceeded; i >= 0; i-- {
		if job.Steps[i].State != model.StepSucceeded {
			continue
		}
		step := r.planned.Steps[i]
		if step.Rollback == nil {
			continue
		}
		rb.Attempted = true
		sc := e.stepContext(ctx, r, i)
		log, err := step.Rollback(ctx, sc)
		if log != "" {
			e.appendLog(ctx, job, &job.Steps[i], log)
		}
		if err != nil {
			failed++
			job.Steps[i].Error = model.NullString(e.o.Redactor.Redact("rollback: " + err.Error()))
			continue
		}
		done++
		job.Steps[i].State = model.StepRolledBack
	}
	switch {
	case !rb.Attempted:
		rb.State = model.RollbackNotNeeded
	case failed == 0:
		rb.State = model.RollbackSucceeded
	case done == 0:
		rb.State = model.RollbackFailed
		rb.Detail = model.NullString(fmt.Sprintf("%d step(s) could not be undone", failed))
	default:
		rb.State = model.RollbackPartial
		rb.Detail = model.NullString(fmt.Sprintf("%d of %d step(s) could not be undone", failed, failed+done))
	}
	job.Rollback = rb
	if state == model.JobFailed && rb.State == model.RollbackSucceeded {
		// Everything the job did was undone: the fleet is where it started,
		// which is a different fact from "it failed halfway".
		state = model.JobRolledBack
	}
	e.finish(ctx, r, state, jobErr, argsRedacted, req, started)
}

// finish releases the locks, writes the terminal state and the `result` audit
// row, and deregisters the run.
func (e *engine) finish(ctx context.Context, r *run, state model.JobState, jobErr *model.JobError, argsRedacted map[string]any, req Request, started time.Time) {
	job := r.job
	now := e.o.Now().UTC()
	job.State = state
	job.Error = jobErr
	job.FinishedAt = model.NullString(now.Format(time.RFC3339))
	job.CurrentStep = nil
	job.Worker = nil
	if r.locks != nil {
		r.locks.Release()
		r.locks = nil
	}
	job.Lock = nil
	if job.Rollback == nil {
		job.Rollback = &model.JobRollback{Attempted: false, State: model.RollbackNotNeeded}
	}
	e.save(ctx, job)

	dur := now.Sub(started).Milliseconds()
	row := model.AuditRow{
		At: now.Format(time.RFC3339), Phase: model.AuditResult,
		Principal: job.Principal, AuthMethod: job.AuthMethod, SudoUser: job.SudoUser,
		RequestID: job.RequestID, Op: job.Op, Tenant: job.Tenant,
		JobID: model.NullString(job.ID), ArgsRedacted: argsRedacted,
		PlanHash: model.NullString(job.PlanHash), Outcome: string(state), DurationMS: &dur,
	}
	if jobErr != nil {
		row.Error = model.NullString(jobErr.Code + ": " + jobErr.Detail)
	}
	e.audit(ctx, row)

	e.mu.Lock()
	delete(e.active, job.ID)
	e.mu.Unlock()
}

func (e *engine) save(ctx context.Context, job *model.Job) {
	normalizeJob(job)
	if err := e.o.Store.Update(ctx, job); err != nil {
		e.o.Logger.Error("persisting a job transition", "job", job.ID, "state", job.State, "err", err)
	}
}

func (e *engine) wasCancelled(r *run) bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	return r.cancelled
}

// ---------------------------------------------------------------- reads

func (e *engine) Get(ctx context.Context, id string) (*model.Job, error) {
	return e.o.Store.Get(ctx, id)
}

func (e *engine) List(ctx context.Context, f ListFilter) ([]model.Job, bool, error) {
	return e.o.Store.List(ctx, f)
}

func (e *engine) StepLog(ctx context.Context, id string, n int) (string, error) {
	if _, err := e.o.Store.Get(ctx, id); err != nil {
		return "", err
	}
	return e.o.Store.StepLog(ctx, id, n)
}

func (e *engine) Audit(ctx context.Context, limit int) ([]model.AuditRow, bool, error) {
	return e.o.Store.AuditList(ctx, limit)
}

// ---------------------------------------------------------------- continuations

// reauthorize is what every continuation runs first. It checks the CALLER,
// not the principal stored on the job: a job submitted by an operator whose
// key was since revoked must not be resumable by presenting the job id.
func reauthorize(p Principal) error {
	if p.Role != "operator" {
		return fmt.Errorf("%w: %s is not an operator", ErrForbidden, p.Subject)
	}
	return nil
}

// rebuild re-plans a stored job so its steps have executable halves again
// (closures cannot be persisted), and refuses if the plan moved. A
// continuation reuses the plan it was submitted with — that is what the hash
// comparison enforces.
func (e *engine) rebuild(ctx context.Context, job *model.Job) (Request, *Planned, Context, map[string]any, error) {
	op, ok := e.o.Ops.Lookup(job.Op)
	if !ok {
		return Request{}, nil, Context{}, nil, fmt.Errorf("%w: no such operation %q", ErrNotFound, job.Op)
	}
	args := map[string]any{}
	if as, ok := e.o.Store.(ArgsStore); ok {
		stored, err := as.Args(ctx, job.ID)
		if err != nil {
			return Request{}, nil, Context{}, nil, err
		}
		args = stored
	}
	req := Request{
		Op: job.Op, Tenant: string(job.Tenant), Args: args,
		IdempotencyKey: job.IdempotencyKey,
		Principal: Principal{
			Subject: job.Principal, Role: "operator", Method: job.AuthMethod,
			SudoUser: string(job.SudoUser), RequestID: job.RequestID,
		},
		Mode: e.o.Mode,
	}
	plan, planned, oc, err := e.plan(ctx, op, req)
	if err != nil {
		return req, nil, oc, nil, err
	}
	if plan.PlanHash != job.PlanHash {
		return req, nil, oc, nil, refuse(
			fmt.Errorf("%w: the plan of job %s moved (%s → %s)", ErrPlanStale, job.ID, job.PlanHash, plan.PlanHash),
			map[string]any{"plan_hash": plan.PlanHash, "registry_generation": plan.RegistryGeneration})
	}
	return req, planned, oc, oc.Redactor.RedactArgs(req.Args), nil
}

// Resume restarts an interrupted job. The interrupted step is RECONCILED
// first — "does the snapshot exist? is the unit active?" — because repeating
// a step that already happened is how a resume makes things worse.
func (e *engine) Resume(ctx context.Context, id string, p Principal) (*model.Job, error) {
	if err := reauthorize(p); err != nil {
		return nil, err
	}
	job, err := e.o.Store.Get(ctx, id)
	if err != nil {
		return nil, err
	}
	if job.State != model.JobInterrupted {
		return nil, fmt.Errorf("%w: job %s is %s; only an interrupted job can be resumed", ErrRefused, id, job.State)
	}
	req, planned, oc, argsRedacted, err := e.rebuild(ctx, job)
	if err != nil {
		return nil, err
	}
	started := e.o.Now().UTC()
	holder := LockHolder{JobID: job.ID, PID: os.Getpid(), Since: started.Format(time.RFC3339)}
	set, err := e.locks.Take(planned.Locks, string(job.Tenant), holder, started)
	if err != nil {
		return nil, lockRefusal(err)
	}
	r := &run{job: job, planned: planned, oc: oc, locks: set}

	// Reconcile the step that was in flight when the worker died.
	from := len(job.Steps)
	for i := range job.Steps {
		if job.Steps[i].State == model.StepInterrupted || job.Steps[i].State == model.StepRunning {
			from = i
			break
		}
		if job.Steps[i].State == model.StepPending {
			from = i
			break
		}
	}
	if from < len(job.Steps) && job.Steps[from].State == model.StepInterrupted {
		st := &job.Steps[from]
		verdict := ReconcileRedo
		var rerr error
		if planned.Steps[from].Reconcile != nil {
			verdict, rerr = planned.Steps[from].Reconcile(ctx, e.stepContext(ctx, r, from))
		}
		if rerr != nil {
			set.Release()
			return job, fmt.Errorf("%w: reconciling step %d of job %s: %s", ErrRefused, st.N, job.ID, rerr)
		}
		switch verdict {
		case ReconcileDone:
			st.State = model.StepSucceeded
			st.FinishedAt = model.NullString(started.Format(time.RFC3339))
			from++
		case ReconcileStuck:
			set.Release()
			detail := fmt.Sprintf("step %d cannot be reconciled from its external ids %v; an operator must decide",
				st.N, st.ExternalIDs)
			job.Error = &model.JobError{Step: stepNo(st.N), Code: "reconcile_stuck", Detail: detail}
			e.save(ctx, job)
			return job, fmt.Errorf("%w: %s", ErrRefused, detail)
		default:
			st.State = model.StepPending
		}
	}

	job.State = model.JobRunning
	job.Error = nil
	job.Worker = &model.JobWorker{PID: os.Getpid(), Host: e.o.Host, Mode: e.o.Mode}
	job.Lock = &model.JobLock{Order: set.Names(), Since: started.Format(time.RFC3339)}
	e.save(ctx, job)

	runCtx, cancel := context.WithCancel(context.Background())
	r.cancel = cancel
	e.mu.Lock()
	e.active[job.ID] = r
	e.mu.Unlock()
	resumed := cloneJob(job)
	go e.runSteps(runCtx, r, req, argsRedacted, started, from)
	return resumed, nil
}

// Continue releases a job parked in awaiting_cutover.
func (e *engine) Continue(ctx context.Context, id string, p Principal) (*model.Job, error) {
	if err := reauthorize(p); err != nil {
		return nil, err
	}
	job, err := e.o.Store.Get(ctx, id)
	if err != nil {
		return nil, err
	}
	if job.State != model.JobAwaitingCutover {
		return nil, fmt.Errorf("%w: job %s is %s; only a job awaiting cutover can be continued", ErrRefused, id, job.State)
	}
	e.mu.Lock()
	r := e.active[id]
	e.mu.Unlock()

	var req Request
	var argsRedacted map[string]any
	started := e.o.Now().UTC()
	if r == nil {
		// The daemon restarted while the job was parked: re-take the locks
		// and rebuild the executable plan before going on.
		rq, planned, oc, ar, err := e.rebuild(ctx, job)
		if err != nil {
			return nil, err
		}
		holder := LockHolder{JobID: job.ID, PID: os.Getpid(), Since: started.Format(time.RFC3339)}
		set, err := e.locks.Take(planned.Locks, string(job.Tenant), holder, started)
		if err != nil {
			return nil, lockRefusal(err)
		}
		r = &run{job: job, planned: planned, oc: oc, locks: set}
		req, argsRedacted = rq, ar
		e.mu.Lock()
		e.active[id] = r
		e.mu.Unlock()
	} else {
		job = r.job
		req = Request{Op: job.Op, Tenant: string(job.Tenant), Args: map[string]any{}}
		argsRedacted = map[string]any{}
	}

	from := 0
	for i := range job.Steps {
		if job.Steps[i].State == model.StepPending {
			from = i
			break
		}
		from = i + 1
	}
	job.State = model.JobRunning
	e.save(ctx, job)

	runCtx, cancel := context.WithCancel(context.Background())
	r.cancel = cancel
	continued := cloneJob(job)
	go e.runSteps(runCtx, r, req, argsRedacted, started, from)
	return continued, nil
}

// Cancel stops a job and undoes what it managed to do.
func (e *engine) Cancel(ctx context.Context, id string, p Principal) (*model.Job, error) {
	if err := reauthorize(p); err != nil {
		return nil, err
	}
	job, err := e.o.Store.Get(ctx, id)
	if err != nil {
		return nil, err
	}
	if job.State.Terminal() {
		return nil, fmt.Errorf("%w: job %s is already %s", ErrRefused, id, job.State)
	}
	e.mu.Lock()
	r := e.active[id]
	if r != nil {
		r.cancelled = true
	}
	e.mu.Unlock()

	switch {
	case r != nil && job.State == model.JobRunning:
		// Cooperative: the step sees the cancelled context, the run goroutine
		// rolls back what succeeded and settles the job as `cancelled`.
		r.cancel()
		return job, nil
	case r != nil && job.State == model.JobAwaitingCutover:
		// The parked run has no goroutine watching; undo it from here.
		last := len(r.job.Steps) - 1
		go e.rollbackAndSettle(r, Request{Op: job.Op, Tenant: string(job.Tenant)}, map[string]any{},
			e.o.Now().UTC(), last, model.JobCancelled,
			&model.JobError{Code: "cancelled", Detail: "cancelled by an operator while awaiting cutover"})
		return job, nil
	case job.State == model.JobInterrupted:
		req, planned, oc, ar, err := e.rebuild(ctx, job)
		if err != nil {
			return nil, err
		}
		started := e.o.Now().UTC()
		holder := LockHolder{JobID: job.ID, PID: os.Getpid(), Since: started.Format(time.RFC3339)}
		set, err := e.locks.Take(planned.Locks, string(job.Tenant), holder, started)
		if err != nil {
			return nil, lockRefusal(err)
		}
		rr := &run{job: job, planned: planned, oc: oc, locks: set, cancelled: true}
		e.mu.Lock()
		e.active[id] = rr
		e.mu.Unlock()
		cancelled := cloneJob(job)
		go e.rollbackAndSettle(rr, req, ar, started, len(job.Steps)-1, model.JobCancelled,
			&model.JobError{Code: "cancelled", Detail: "cancelled by an operator while interrupted"})
		return cancelled, nil
	default:
		// Queued and not yet started anywhere: nothing ran, so nothing to undo.
		now := e.o.Now().UTC()
		job.State = model.JobCancelled
		job.FinishedAt = model.NullString(now.Format(time.RFC3339))
		job.Error = &model.JobError{Code: "cancelled", Detail: "cancelled by an operator before it started"}
		job.Rollback = &model.JobRollback{Attempted: false, State: model.RollbackNotNeeded}
		e.save(ctx, job)
		e.audit(ctx, model.AuditRow{
			At: now.Format(time.RFC3339), Phase: model.AuditResult, Principal: p.Subject,
			AuthMethod: p.Method, SudoUser: model.NullString(p.SudoUser),
			RequestID: job.RequestID, Op: job.Op, Tenant: job.Tenant,
			JobID: model.NullString(job.ID), ArgsRedacted: map[string]any{},
			PlanHash: model.NullString(job.PlanHash), Outcome: string(model.JobCancelled),
		})
		return cloneJob(job), nil
	}
}

// ---------------------------------------------------------------- secrets

// putEnvelope seals the minted values. They go NOWHERE else: not into
// job.result, not into the audit row, not into a step log.
func (e *engine) putEnvelope(ctx context.Context, job *model.Job, secrets []model.Secret) error {
	plaintext, err := json.Marshal(secrets)
	if err != nil {
		return err
	}
	now := e.o.Now().UTC()
	env := Envelope{
		JobID: job.ID, Principal: job.Principal, Op: job.Op,
		CreatedAt: now, ExpiresAt: now.Add(e.o.SecretsTTL),
	}
	if e.o.Sealer != nil {
		if sealed, ok := e.o.Sealer(plaintext); ok {
			env.Sealed = true
			env.Payload = sealed
		}
	}
	if !env.Sealed {
		// No recipients configured: the values live in this process's memory
		// and die with it. That is the contract's fallback, and it is why a
		// lost value's remedy is a new mint rather than a recovery.
		e.mu.Lock()
		e.mem[job.ID] = plaintext
		e.mu.Unlock()
	}
	return e.o.Store.PutEnvelope(ctx, env)
}

func (e *engine) Secrets(ctx context.Context, id string, p Principal) (*model.SecretsResponse, error) {
	if err := reauthorize(p); err != nil {
		return nil, err
	}
	if p.FromSession {
		return nil, fmt.Errorf("%w: secrets are not delivered over a session; present the ctl key itself", ErrForbidden)
	}
	if _, err := e.o.Store.Get(ctx, id); err != nil {
		return nil, err
	}
	now := e.o.Now().UTC()
	env, err := e.o.Store.TakeEnvelope(ctx, id, p.Subject, now)
	if err != nil {
		return nil, err
	}

	plaintext := env.Payload
	if env.Sealed {
		if e.o.Unsealer == nil {
			return nil, fmt.Errorf("%w: the envelope of job %s is sealed and no unsealer is configured", ErrRefused, id)
		}
		plaintext, err = e.o.Unsealer(env.Payload)
		if err != nil {
			return nil, fmt.Errorf("unsealing the envelope of job %s: %w", id, err)
		}
	} else {
		e.mu.Lock()
		held, ok := e.mem[id]
		delete(e.mem, id)
		e.mu.Unlock()
		if !ok {
			return nil, fmt.Errorf("%w: the secrets of job %s were held in daemon memory and the daemon restarted; the remedy is a new, audited mint", ErrGone, id)
		}
		plaintext = held
	}
	var secrets []model.Secret
	if err := json.Unmarshal(plaintext, &secrets); err != nil {
		return nil, fmt.Errorf("decoding the envelope of job %s: %w", id, err)
	}
	return &model.SecretsResponse{
		JobID:       id,
		DeliveredAt: now.Format(time.RFC3339),
		ExpiresAt:   env.ExpiresAt.UTC().Format(time.RFC3339),
		Secrets:     secrets,
	}, nil
}

// ---------------------------------------------------------------- reconcile

// Reconcile runs once at daemon start. It NEVER resumes anything: a job whose
// worker died is moved to `interrupted`, its reservations are kept, and a
// human decides. Automatic resumption of a half-finished mutation, after a
// crash whose cause is unknown, is how a restart turns one outage into two.
func (e *engine) Reconcile(ctx context.Context) ([]string, error) {
	running, err := e.o.Store.Running(ctx)
	if err != nil {
		return nil, err
	}
	now := e.o.Now().UTC()
	var out []string
	for i := range running {
		job := running[i]
		if job.Worker != nil && job.Worker.Host == e.o.Host && pidAlive(job.Worker.PID) {
			continue // still ours, still alive
		}
		for j := range job.Steps {
			if job.Steps[j].State == model.StepRunning {
				job.Steps[j].State = model.StepInterrupted
			}
		}
		job.State = model.JobInterrupted
		job.Worker = nil
		job.Lock = nil // the kernel dropped the flocks when the worker died
		detail := "the worker did not survive; reconciled at daemon start"
		if job.Error == nil {
			job.Error = &model.JobError{Code: "interrupted", Detail: detail}
		}
		e.save(ctx, &job)
		e.audit(ctx, model.AuditRow{
			At: now.Format(time.RFC3339), Phase: model.AuditResult,
			Principal: job.Principal, AuthMethod: job.AuthMethod, SudoUser: job.SudoUser,
			RequestID: job.RequestID, Op: job.Op, Tenant: job.Tenant,
			JobID: model.NullString(job.ID), ArgsRedacted: map[string]any{},
			PlanHash: model.NullString(job.PlanHash), Outcome: string(model.JobInterrupted),
			Error: model.NullString(detail),
		})
		out = append(out, job.ID)
	}
	return out, nil
}

// pidAlive is signal 0: it asks the kernel whether the pid exists without
// touching the process. EPERM means it exists and belongs to someone else,
// which for our purposes is "alive".
func pidAlive(pid int) bool {
	if pid <= 0 {
		return false
	}
	err := syscall.Kill(pid, 0)
	return err == nil || errors.Is(err, syscall.EPERM)
}

// ---------------------------------------------------------------- helpers

func (e *engine) audit(ctx context.Context, row model.AuditRow) {
	if row.RequestID == "" {
		row.RequestID = normalizeRequestID("")
	}
	if err := e.o.Store.Audit(ctx, row); err != nil {
		e.o.Logger.Error("writing an audit row", "op", row.Op, "err", err)
	}
}

// auditRefusal writes the single `intent` row a refused request gets.
func (e *engine) auditRefusal(ctx context.Context, req Request, argsRedacted map[string]any, planHash string, cause error) {
	now := e.o.Now().UTC()
	e.audit(ctx, model.AuditRow{
		At: now.Format(time.RFC3339), Phase: model.AuditIntent,
		Principal: req.Principal.Subject, AuthMethod: req.Principal.Method,
		SudoUser:  model.NullString(req.Principal.SudoUser),
		RequestID: normalizeRequestID(req.Principal.RequestID),
		Op:        req.Op, Tenant: model.NullString(req.Tenant),
		ArgsRedacted: argsRedacted, PlanHash: model.NullString(planHash),
		Outcome: "refused", Error: model.NullString(cause.Error()),
	})
}

// requestIDPattern is the contract's: 16 lowercase hex characters.
var requestIDPattern = regexp.MustCompile(`^[0-9a-f]{16}$`)

func normalizeRequestID(id string) string {
	if requestIDPattern.MatchString(id) {
		return id
	}
	var b [8]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "0000000000000000"
	}
	return hex.EncodeToString(b[:])
}

func stepNo(n int) *int { return &n }

// normalizePlan makes every array field non-nil, because plan.json spells
// them `type: array` and a nil slice marshals as null.
func normalizePlan(p *model.Plan) {
	if p.Steps == nil {
		p.Steps = []model.PlannedStep{}
	}
	if p.Warnings == nil {
		p.Warnings = []string{}
	}
	if p.Doctor.Findings == nil {
		p.Doctor.Findings = []model.Finding{}
	}
	for i := range p.Steps {
		s := &p.Steps[i]
		s.N = i + 1
		if s.Targets == nil {
			s.Targets = []string{}
		}
		if s.WouldWrite == nil {
			s.WouldWrite = []model.WouldWrite{}
		}
		if s.WouldRun == nil {
			s.WouldRun = []model.WouldRun{}
		}
		if s.Warnings == nil {
			s.Warnings = []string{}
		}
	}
}
