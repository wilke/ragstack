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
	"runtime/debug"
	"strconv"
	"strings"
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
	// the database. Each entry carries its own expiry so the sweep can drop
	// it on time — a secret that outlives its envelope is a secret nobody is
	// still watching.
	mem map[string]memEnvelope
}

// memEnvelope is one memory-only envelope: the plaintext and the moment it
// stops being deliverable.
type memEnvelope struct {
	payload   []byte
	expiresAt time.Time
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
	// settling is the compare-and-set that makes exactly ONE goroutine
	// responsible for driving this run to its terminal state. A second
	// Cancel, or a Cancel racing a Continue, finds it set and refuses rather
	// than starting a second rollback over the same steps.
	settling bool
	// finished guards the terminal write itself: one result audit row, one
	// Release, however many paths reach finish.
	finished bool
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
		mem:    map[string]memEnvelope{},
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
	// Every submission is also a chance to drop envelopes nobody came for.
	e.sweepMem(now)
	job := e.newJob(req, plan, now)
	fp, err := Fingerprint(req.Principal.Subject, req.Op, req.Tenant, req.Args, plan.PlanHash)
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
		// The attempt still leaves a trace — an operator who pressed the
		// button twice, or a client that retried a lost 202, did something,
		// and an audit log that shows nothing cannot tell the two apart.
		e.audit(ctx, model.AuditRow{
			At: now.Format(time.RFC3339), Phase: model.AuditIntent,
			Principal: req.Principal.Subject, AuthMethod: req.Principal.Method,
			SudoUser:  model.NullString(req.Principal.SudoUser),
			RequestID: normalizeRequestID(req.Principal.RequestID),
			Op:        req.Op, Tenant: model.NullString(req.Tenant),
			JobID: model.NullString(stored.ID), ArgsRedacted: argsRedacted,
			PlanHash: model.NullString(plan.PlanHash), Outcome: "joined",
		})
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
	defer e.recoverRun(r, req, argsRedacted, started)

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
	e.recordWorker(ctx, job.ID)
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
	defer e.recoverRun(r, req, argsRedacted, started)
	job := r.job
	steps := r.planned.Steps

	for i := from; i < len(steps); i++ {
		if ctx.Err() != nil {
			e.rollbackAndSettle(r, req, argsRedacted, started, i, model.JobCancelled,
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
		if log == "" && err == nil {
			// A step that says nothing still leaves a line: the operator's
			// log is never null for a step that ran (the viewer's always is,
			// which is the reduction the contract promises).
			log = "done"
		}
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
			e.rollbackAndSettle(r, req, argsRedacted, started, i, state,
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
				// A mint that cannot be delivered must not report success:
				// job.result would say `secrets_available` for an envelope
				// nobody can ever take, and the operator would go looking for
				// a value that does not exist. The mint is UNDONE instead —
				// the step that wrote the credential has a Rollback keyed on
				// what it wrote — so the remedy is a fresh, audited re-mint
				// rather than a key that lives on a host with no owner.
				e.o.Logger.Error("sealing the delivery envelope", "job", job.ID, "err", err)
				job.Result = nil
				e.rollbackAndSettle(r, req, argsRedacted, started, len(job.Steps)-1, model.JobFailed,
					&model.JobError{Code: "secrets_undeliverable", Detail: e.o.Redactor.Redact(
						"the minted secrets could not be sealed into the delivery envelope (" + err.Error() +
							"); what was minted has been rolled back — re-mint to get a value that can be delivered")})
				return
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
	// The durability hooks run on a context that CANNOT be cancelled. A step
	// being cancelled is exactly the step that most needs to record the ids
	// of the work it already started: if Checkpoint failed because the cancel
	// beat it to the store, the external effect would exist with nothing in
	// the database pointing at it, and reconcile would have nothing to act
	// on. The cancellation still reaches the STEP through ctx — it just does
	// not reach the step's bookkeeping.
	dctx := context.WithoutCancel(ctx)
	return &StepContext{
		Job: job, Step: st, Ops: r.oc,
		Checkpoint: func(ids ...string) error {
			// Durable BEFORE the external call: the ids have to be readable
			// by the next process to open this database even if this one dies
			// on the very next instruction.
			st.ExternalIDs = append(st.ExternalIDs, ids...)
			st.Checkpoint = true
			return e.o.Store.Update(dctx, job)
		},
		Reserve: func(resource string, until *time.Time) error {
			res := model.Reservation{Resource: resource}
			if until != nil {
				res.Until = model.NullString(until.UTC().Format(time.RFC3339))
			}
			job.Reservations = append(job.Reservations, res)
			if rs, ok := e.o.Store.(ReservationStore); ok {
				if err := rs.PutReservation(dctx, job.ID, res, e.o.Now()); err != nil {
					return err
				}
			}
			return e.o.Store.Update(dctx, job)
		},
		Logf: func(format string, args ...any) {
			e.appendLog(dctx, job, st, fmt.Sprintf(format, args...))
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
// lastStep is the index of the last step the job TOUCHED — the one that
// failed or was cancelled, not the one before it.
func (e *engine) rollbackAndSettle(r *run, req Request, argsRedacted map[string]any, started time.Time, lastStep int, state model.JobState, jobErr *model.JobError) {
	// A fresh context: the cancellation that got us here must not also cancel
	// the undo.
	ctx := context.Background()
	job := r.job
	rb := &model.JobRollback{Attempted: false, State: model.RollbackNotNeeded}

	if lastStep > len(job.Steps)-1 {
		lastStep = len(job.Steps) - 1
	}
	var failed, done int
	for i := lastStep; i >= 0; i-- {
		if !rollbackable(&job.Steps[i]) {
			continue
		}
		if i >= len(r.planned.Steps) {
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
		if job.Steps[i].State == model.StepSucceeded {
			// The step that FAILED keeps its state: which step broke is the
			// first thing an operator reads, and job.error names it. That its
			// half-done work was undone is recorded by the job's rollback
			// block, not by relabelling the failure.
			job.Steps[i].State = model.StepRolledBack
		}
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

// rollbackable says whether the reverse pass should offer this step to its
// Rollback. A succeeded step, always. A FAILED step too, but only when it got
// far enough to leave something behind: a checkpoint, or external ids. That
// is the case the plan's "a step records its external ids BEFORE the call
// that creates them" exists for — the snapshot was created and the call then
// timed out, and the only record of it is the id the step checkpointed. A
// rollback that skips the failing step leaks exactly the resource the
// checkpoint was written to make recoverable, and the step's own Rollback is
// the only code that knows how to key off those ids.
func rollbackable(st *model.Step) bool {
	switch st.State {
	case model.StepSucceeded:
		return true
	case model.StepFailed:
		return st.Checkpoint || len(st.ExternalIDs) > 0
	default:
		return false
	}
}

// recoverRun turns a panicking step into a failed job instead of a dead
// daemon. A control plane that loses its process to one bad step loses every
// OTHER job's bookkeeping with it: their locks stay on disk, their rows stay
// `running`, and the restart has to reconcile work that was never in trouble.
// So the panic is caught HERE, at the one boundary that owns the job's locks
// and its terminal write, and the stack goes to the log.
func (e *engine) recoverRun(r *run, req Request, argsRedacted map[string]any, started time.Time) {
	rec := recover()
	if rec == nil {
		return
	}
	job := r.job
	detail := e.o.Redactor.Redact(fmt.Sprintf("the step panicked: %v", rec))
	e.o.Logger.Error("a job step panicked", "job", job.ID, "op", job.Op,
		"panic", fmt.Sprint(rec), "stack", string(debug.Stack()))

	jobErr := &model.JobError{Code: "step_panic", Detail: detail}
	for i := range job.Steps {
		if job.Steps[i].State == model.StepRunning {
			job.Steps[i].State = model.StepFailed
			job.Steps[i].FinishedAt = model.NullString(e.o.Now().UTC().Format(time.RFC3339))
			job.Steps[i].Error = model.NullString(detail)
			jobErr.Step = stepNo(job.Steps[i].N)
			break
		}
	}
	// No rollback: a step that panicked left the host in a state its own undo
	// has no reason to understand. The locks ARE released — holding a fleet
	// lock for a goroutine that no longer exists would wedge every later job
	// on this tenant — and the job is failed so a human decides what is true.
	e.finish(context.Background(), r, model.JobFailed, jobErr, argsRedacted, req, started)
}

// finish releases the locks, writes the terminal state and the `result` audit
// row, and deregisters the run. It settles a run exactly ONCE: two paths can
// reach it (a Cancel's rollback and the run goroutine finding its context
// done), and a second terminal write would emit a second `result` audit row
// and Release a lock set the first pass already dropped.
func (e *engine) finish(ctx context.Context, r *run, state model.JobState, jobErr *model.JobError, argsRedacted map[string]any, req Request, started time.Time) {
	e.mu.Lock()
	if r.finished {
		e.mu.Unlock()
		return
	}
	r.finished = true
	e.mu.Unlock()

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
	e.recordWorker(ctx, job.ID)
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

	// Take responsibility for this run before anything else touches it. A
	// Cancel that arrived a microsecond earlier already owns it, and two
	// goroutines driving one parked job would roll its steps back while the
	// remaining ones were still running.
	if !e.claimSettle(r) {
		return nil, fmt.Errorf("%w: job %s is already settling; wait for it to reach a terminal state", ErrRefused, id)
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

// claimSettle is the compare-and-set that makes ONE caller responsible for
// driving r to a terminal state. Everything that spawns a goroutine which
// will settle the run — Continue, and Cancel on a parked or interrupted job —
// goes through it, so a double Cancel, or a Cancel racing a Continue, refuses
// instead of starting a second pass over the same steps.
func (e *engine) claimSettle(r *run) bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	if r.settling {
		return false
	}
	r.settling = true
	return true
}

// Cancel stops a job and undoes what it managed to do.
//
// confirm is the contract's: cancelling a job that has succeeded steps with a
// rollback is itself a mutation of the fleet, so openapi.yaml gives the route
// the same `confirm` every destructive call has. A cancel that would undo
// nothing needs none.
func (e *engine) Cancel(ctx context.Context, id string, p Principal, confirm string) (*model.Job, error) {
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
	e.mu.Unlock()

	switch {
	case r != nil && (job.State == model.JobRunning || job.State == model.JobQueued):
		// Cooperative: the step sees the cancelled context, the run goroutine
		// rolls back what succeeded and settles the job as `cancelled`. No
		// second goroutine is started, so no compare-and-set is needed — and
		// a repeated Cancel just cancels an already-cancelled context.
		if err := e.gateCancelConfirm(job, r.planned, confirm); err != nil {
			return nil, err
		}
		e.markCancelled(r)
		r.cancel()
		return job, nil

	case r != nil && job.State == model.JobAwaitingCutover:
		// The parked run has no goroutine watching; undo it from here.
		if err := e.gateCancelConfirm(job, r.planned, confirm); err != nil {
			return nil, err
		}
		if !e.claimSettle(r) {
			// A Continue, or another Cancel, already owns this run.
			return nil, fmt.Errorf("%w: job %s is already settling; wait for it to reach a terminal state", ErrRefused, id)
		}
		e.markCancelled(r)
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
		if err := e.gateCancelConfirm(job, planned, confirm); err != nil {
			return nil, err
		}
		started := e.o.Now().UTC()
		holder := LockHolder{JobID: job.ID, PID: os.Getpid(), Since: started.Format(time.RFC3339)}
		set, err := e.locks.Take(planned.Locks, string(job.Tenant), holder, started)
		if err != nil {
			return nil, lockRefusal(err)
		}
		rr := &run{job: job, planned: planned, oc: oc, locks: set, cancelled: true, settling: true}
		e.mu.Lock()
		if prev := e.active[id]; prev != nil {
			// Something claimed this job between the Get and the locks.
			e.mu.Unlock()
			set.Release()
			return nil, fmt.Errorf("%w: job %s is already settling; wait for it to reach a terminal state", ErrRefused, id)
		}
		e.active[id] = rr
		e.mu.Unlock()
		cancelled := cloneJob(job)
		go e.rollbackAndSettle(rr, req, ar, started, len(job.Steps)-1, model.JobCancelled,
			&model.JobError{Code: "cancelled", Detail: "cancelled by an operator while interrupted"})
		return cancelled, nil

	case job.State == model.JobQueued:
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

	default:
		// Running or parked, and NOT ours. The old code marked it "cancelled
		// before it started" — a lie about a job that is at this moment
		// running steps in another process, and one that frees the row while
		// that process goes on writing to the same fleet. Only the process
		// holding the run can stop it, so say so and name it.
		extra := map[string]any{}
		who := "another process"
		if job.Worker != nil {
			extra["pid"], extra["host"], extra["mode"] = job.Worker.PID, job.Worker.Host, string(job.Worker.Mode)
			who = fmt.Sprintf("pid %d on %s (%s)", job.Worker.PID, job.Worker.Host, job.Worker.Mode)
		}
		return nil, refuse(fmt.Errorf(
			"%w: job %s is %s in %s, not in this one; cancel it from there (`ragstack-ctl --direct job cancel %s` on that host) or wait for reconcile to interrupt it",
			ErrRefused, id, job.State, who, id), extra)
	}
}

// markCancelled records that a HUMAN asked for the stop, so the terminal
// state is `cancelled` rather than `failed`.
func (e *engine) markCancelled(r *run) {
	e.mu.Lock()
	r.cancelled = true
	e.mu.Unlock()
}

// gateCancelConfirm is openapi.yaml's "cancel requires confirm when a
// rollback would run". The confirm VALUE is the plan's: a destructive op is
// confirmed by typing the tenant's name, everything else by "yes" — typing
// "yes" to "undo the handover of prod" is not evidence you read which tenant
// it said.
func (e *engine) gateCancelConfirm(job *model.Job, planned *Planned, confirm string) error {
	if !wouldRollBack(job, planned) {
		return nil
	}
	want := e.cancelConfirmValue(job)
	if confirm == want {
		return nil
	}
	return refuse(fmt.Errorf("%w: cancelling job %s rolls back what it already did; pass confirm=%q",
		ErrConfirmRequired, job.ID, want), map[string]any{"confirm_value": want})
}

// wouldRollBack reports whether a cancel now would actually undo something:
// at least one succeeded step whose planned half has a Rollback.
func wouldRollBack(job *model.Job, planned *Planned) bool {
	if planned == nil {
		return false
	}
	for i := range job.Steps {
		if i >= len(planned.Steps) {
			break
		}
		if rollbackable(&job.Steps[i]) && planned.Steps[i].Rollback != nil {
			return true
		}
	}
	return false
}

// cancelConfirmValue is the same rule plan() applies: the tenant name for a
// destructive op, "yes" otherwise.
func (e *engine) cancelConfirmValue(job *model.Job) string {
	if op, ok := e.o.Ops.Lookup(job.Op); ok && op.Destructive() && job.Tenant != "" {
		return string(job.Tenant)
	}
	return "yes"
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
		e.mem[job.ID] = memEnvelope{payload: plaintext, expiresAt: env.ExpiresAt}
		e.mu.Unlock()
	}
	e.sweepMem(now)
	if err := e.o.Store.PutEnvelope(ctx, env); err != nil {
		// The store refused the envelope, so nothing will ever deliver it.
		// Drop the plaintext rather than leave an undeliverable secret in
		// this process's heap for the rest of the daemon's life.
		e.mu.Lock()
		e.forgetMem(job.ID)
		e.mu.Unlock()
		return err
	}
	return nil
}

// sweepMem drops every memory-only envelope whose TTL has passed. The store
// expires the envelope ROW on read, but the plaintext lives here, and an
// entry nobody ever comes back for would otherwise sit in the daemon's heap
// until the process ends — an unbounded, unauditable pile of live
// credentials. It runs on every Secrets call, on Submit and on each mint, so
// no ticker (and no extra goroutine to leak) is needed.
func (e *engine) sweepMem(now time.Time) {
	e.mu.Lock()
	defer e.mu.Unlock()
	for id, m := range e.mem {
		if !m.expiresAt.After(now) {
			e.forgetMem(id)
		}
	}
}

// forgetMem zeroes and drops one entry. Callers hold e.mu.
func (e *engine) forgetMem(id string) {
	m, ok := e.mem[id]
	if !ok {
		return
	}
	for i := range m.payload {
		m.payload[i] = 0
	}
	delete(e.mem, id)
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
	e.sweepMem(now)
	env, err := e.o.Store.TakeEnvelope(ctx, id, p.Subject, now)
	if err != nil {
		if errors.Is(err, ErrGone) {
			// Delivered already, or expired: either way the plaintext must
			// not outlive the row that authorized reading it.
			e.mu.Lock()
			e.forgetMem(id)
			e.mu.Unlock()
		}
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
		m, ok := e.mem[id]
		held := m.payload
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
		if e.workerAlive(ctx, &job) {
			continue // still ours, still alive, still holding its locks
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

// workerAlive decides whether job's recorded worker is still running it. It
// is the whole basis of reconcile's "did this job's process survive?", so
// getting it wrong in the optimistic direction is the expensive mistake: a
// job wrongly judged alive is never interrupted, its locks are never
// reclaimed, and the fleet waits on a process that no longer exists.
//
// kill(pid, 0) alone cannot answer it. Pids are RECYCLED, and a daemon
// restart on a busy host is exactly when the number the dead worker had is
// most likely to belong to something new. So three things must agree:
//
//   - the HOST, because a pid on another machine says nothing here;
//   - the worker's process START TIME, recorded when the job took its locks —
//     the pair (pid, start time) is unique for as long as the kernel runs,
//     which is what makes pid reuse detectable rather than invisible;
//   - the job's FLOCKS, because a live worker holds them and a dead one
//     cannot. flock(2) belongs to the open file description, so a set we can
//     take is a set nobody holds.
func (e *engine) workerAlive(ctx context.Context, job *model.Job) bool {
	w := job.Worker
	if w == nil || w.Host != e.o.Host || !pidAlive(w.PID) {
		return false
	}
	if ws, ok := e.o.Store.(WorkerStore); ok {
		rec, err := ws.Worker(ctx, job.ID)
		if err != nil {
			e.o.Logger.Error("reading a job's recorded worker identity", "job", job.ID, "err", err)
		} else if rec != nil {
			if rec.Host != e.o.Host || rec.PID != w.PID {
				return false
			}
			started, ok := procStartTime(w.PID)
			if !ok || started != rec.StartTime {
				// The pid exists but it is NOT the process that took this
				// job: the number was recycled.
				e.o.Logger.Warn("a job's pid was reused by another process",
					"job", job.ID, "pid", w.PID, "recorded_start", rec.StartTime, "now", started)
				return false
			}
		}
	}
	if job.Lock != nil && len(job.Lock.Order) > 0 && e.locks.Free(job.Lock.Order, string(job.Tenant)) {
		// Nothing holds the locks this job says it holds.
		return false
	}
	return true
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

// procStartTime is field 22 of /proc/<pid>/stat: the process's start time in
// clock ticks since boot. Same field ops/coconut/ctl-daemon.sh reads, for the
// same reason.
//
// The comm field (2) is in parentheses and may itself contain spaces and
// parentheses, so the split starts after the LAST ')' — the only parse of
// this file that is correct for a process called "(evil) thing".
func procStartTime(pid int) (uint64, bool) {
	if pid <= 0 {
		return 0, false
	}
	b, err := os.ReadFile(fmt.Sprintf("/proc/%d/stat", pid))
	if err != nil {
		return 0, false
	}
	i := strings.LastIndex(string(b), ")")
	if i < 0 {
		return 0, false
	}
	fields := strings.Fields(string(b)[i+1:])
	// After comm, field 3 is state; start time is field 22, i.e. index 19 here.
	const startTimeIndex = 19
	if len(fields) <= startTimeIndex {
		return 0, false
	}
	v, err := strconv.ParseUint(fields[startTimeIndex], 10, 64)
	if err != nil {
		return 0, false
	}
	return v, true
}

// recordWorker persists the identity of the process that is about to run
// job, so a later reconcile can tell this process from whatever inherits its
// pid. It is deliberately NOT part of model.JobWorker: job.json is the
// contract, the start time is a host implementation detail, and widening a
// published schema to carry it would make every client's parser care.
func (e *engine) recordWorker(ctx context.Context, jobID string) {
	ws, ok := e.o.Store.(WorkerStore)
	if !ok {
		return
	}
	pid := os.Getpid()
	started, _ := procStartTime(pid)
	// Uncancellable: the identity has to be on disk before the steps run,
	// and a cancel arriving in that window must not leave reconcile blind.
	if err := ws.PutWorker(context.WithoutCancel(ctx), jobID, WorkerIdentity{
		PID: pid, Host: e.o.Host, StartTime: started, Mode: e.o.Mode,
	}); err != nil {
		e.o.Logger.Error("recording the worker identity", "job", jobID, "err", err)
	}
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
