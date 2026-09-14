// Package jobs is the mutation engine of ragstack-ctl (plan v3 "Job model",
// PR-C): every write the control plane performs is a Job — planned first,
// authorized, idempotent, executed under the single fleet lock order with
// durable checkpoints, audited, and reconciled on restart.
//
// This file is the SEAM. It declares the interfaces three implementations
// meet:
//
//   - Store (store.go, SQLite via modernc.org/sqlite): durable jobs, steps,
//     idempotency keys, reservations, audit rows, envelopes.
//   - Op (internal/ctl/ops): one type per verb that PLANS steps against the
//     registry and RUNS them through Drivers.
//   - Engine (engine.go): the state machine that ties them together and that
//     the HTTP layer (internal/ctl/api) and the --direct CLI call.
//
// Nothing here touches a host. The rules the engine enforces are the plan's:
// a dry run locks and writes nothing; execution persists the idempotency key
// first; the plan is re-validated AFTER the locks; every step records its
// external IDs BEFORE the external call; an interrupted job keeps its
// reservations; every continuation is re-authorized.
package jobs

import (
	"context"
	"errors"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// ---------------------------------------------------------------- errors
//
// Each maps to exactly one contract error code (schemas/error.json) and one
// HTTP status; the API layer does the mapping, the CLI maps them to exit 3.

var (
	// ErrRefused wraps every refusal the contract answers 409 `refused`
	// (capability, fencing, ownership, containment, "lands in PR-D").
	ErrRefused = errors.New("refused")
	// ErrLocked: another job holds a lock in the path (409 `locked`, holder in extra).
	ErrLocked = errors.New("locked")
	// ErrPlanStale: the plan recomputed under the locks differs (409 `plan_stale`).
	ErrPlanStale = errors.New("plan_stale")
	// ErrDoctorRed: the op-scoped doctor is red, or yellow without a matching
	// force_with_doctor_diff (409 `doctor_red`).
	ErrDoctorRed = errors.New("doctor_red")
	// ErrConfirmRequired: the plan needs `confirm` and it is absent or wrong (428).
	ErrConfirmRequired = errors.New("confirm_required")
	// ErrDuplicate: a DIFFERENT request under an existing idempotency key (409 `duplicate`).
	ErrDuplicate = errors.New("duplicate")
	// ErrValidation: args do not match x-ctl-op-args[verb] (422 `validation`).
	ErrValidation = errors.New("validation")
	// ErrNotFound: unknown job, tenant or verb (404).
	ErrNotFound = errors.New("not_found")
	// ErrGone: the envelope was already delivered or expired (410).
	ErrGone = errors.New("gone")
	// ErrForbidden: the continuation or envelope belongs to another principal (403).
	ErrForbidden = errors.New("forbidden")
)

// ---------------------------------------------------------------- principal

// Principal is who asked, as the authn layer established it. Every
// continuation (Resume/Continue/Cancel) and every envelope read is
// re-authorized against it; the engine never trusts a job's stored principal
// to authorize a later call.
type Principal struct {
	// Subject is the audit identity: "bvbrc:<user>", "key:<name>", "local:<uid>".
	Subject string
	// Role is viewer or operator; the engine refuses anything but operator.
	Role string
	// Method is how the credential arrived.
	Method model.AuthMethod
	// SudoUser is SUDO_USER for --direct CLI runs, else "".
	SudoUser string
	// RequestID is the X-Request-Id (or a generated one for the CLI).
	RequestID string
	// FromSession is true when the request authenticated with a session
	// cookie/token; such a request must also carry OpRequest.CtlAPIKey, which
	// the API layer verifies BEFORE calling the engine (plan: "every mutation
	// requires re-presenting a ctl-specific API key in the request body").
	FromSession bool
}

// ---------------------------------------------------------------- ops

// Request is one mutation as the engine sees it, after the API/CLI layer has
// authenticated the caller and validated args against x-ctl-op-args[verb].
type Request struct {
	// Op is the verb ("start", "backup", …), or "create", "gateway-apply",
	// "gateway-reload" for the non-tenant entry points.
	Op string
	// Tenant is the registry key, "" for fleet-scoped ops.
	Tenant string
	// Args is the validated, still-unredacted args object.
	Args map[string]any
	// DryRun returns a Plan and nothing else.
	DryRun bool
	// IdempotencyKey is the caller's key; required unless DryRun.
	IdempotencyKey string
	// Confirm is the caller's confirm string (matched to Plan.ConfirmValue).
	Confirm string
	// ForceWithDoctorDiff is the doctor hash the caller accepted.
	ForceWithDoctorDiff string
	// Principal is the authenticated caller.
	Principal Principal
	// Mode is daemon or direct; direct runs take the same flock files.
	Mode model.WorkerMode
}

// Context is what an Op gets to plan against: a registry snapshot, the
// roots, the doctor findings for this op, and the drivers.
type Context struct {
	Roots    paths.Roots
	Fleet    *registry.Fleet
	Tenant   *registry.Tenant // nil for fleet-scoped ops
	Doctor   model.DoctorResponse
	Drivers  Drivers
	Now      func() time.Time
	Redactor Redactor
}

// Redactor replaces every secret-class value in text or args before anything
// is logged, audited or previewed. The engine seeds it from the same files
// the logs endpoint uses; an Op MUST pass every preview through it.
type Redactor interface {
	Redact(s string) string
	RedactArgs(args map[string]any) map[string]any
}

// StepFunc executes one planned step. It receives the checkpoint recorded
// for this step (nil on first attempt), and returns the external IDs it
// created (recorded durably BEFORE the call by way of Checkpoint) and a
// human log. On error the engine marks the step failed and runs Rollback in
// reverse over the steps that succeeded.
type StepFunc func(ctx context.Context, sc *StepContext) (log string, err error)

// StepContext is the per-step view the engine hands a StepFunc.
type StepContext struct {
	Job  *model.Job
	Step *model.Step
	Ops  Context
	// Checkpoint persists durable external IDs for this step NOW, before the
	// external call that creates them, so a crash between the two leaves a
	// record reconcile-on-restart can act on. It is the only way a step may
	// record ExternalIDs.
	Checkpoint func(externalIDs ...string) error
	// Reserve records a resource the job holds until it reaches a terminal
	// state (or Until), surviving interruption.
	Reserve func(resource string, until *time.Time) error
	// Logf appends to the step log (redacted by the engine).
	Logf func(format string, args ...any)
}

// Step is a planned step with its executable halves. Reconcile is consulted
// on resume for a step that was running when the worker died: it inspects
// the recorded ExternalIDs and reports whether the external work happened
// (done), must be redone (redo) or cannot be decided (stuck → the job stays
// interrupted and says why).
type Step struct {
	Plan      model.PlannedStep
	Run       StepFunc
	Rollback  StepFunc // nil when the step is not reversible; the plan says so
	Reconcile func(ctx context.Context, sc *StepContext) (Reconciliation, error)
	// Cutover marks the step after which the job waits in awaiting_cutover
	// for an explicit Continue (handover, migrate-local).
	Cutover bool
}

// Reconciliation is Step.Reconcile's answer.
type Reconciliation string

const (
	ReconcileDone  Reconciliation = "done"
	ReconcileRedo  Reconciliation = "redo"
	ReconcileStuck Reconciliation = "stuck"
)

// Planned is what an Op returns: the contract Plan (already redacted) and the
// executable steps in the same order and count.
type Planned struct {
	Plan  model.Plan
	Steps []Step
	// Locks lists the locks this op needs, a subset of model.LockOrder; the
	// engine takes them in LockOrder regardless of the order given here.
	Locks []model.LockName
	// Secrets, when non-nil, is called after success with the values the op
	// minted; the engine seals them into the delivery envelope and NEVER
	// stores them anywhere else.
	Secrets func() []model.Secret
	// Result is the op-specific result object recorded on success (no secrets).
	Result func() map[string]any
}

// Op plans one verb. Plan must be a pure function of (Context, Args): the
// engine calls it twice — once for the dry run or the initial answer, once
// again UNDER THE LOCKS — and refuses with ErrPlanStale when the hashes
// differ. Validate is the x-ctl-op-args check, called before Plan.
type Op interface {
	Verb() string
	// Destructive says whether the plan requires confirm with the tenant name.
	Destructive() bool
	Validate(args map[string]any) error
	Plan(ctx context.Context, oc Context, args map[string]any) (*Planned, error)
}

// Registry maps verbs to Ops; the ops package fills it, the engine reads it.
type Registry interface {
	Lookup(verb string) (Op, bool)
	Verbs() []string
}

// ---------------------------------------------------------------- drivers

// Drivers is the whole host surface an Op may touch, as interfaces so that
// `serve --fake-drivers` and the tests run every verb end to end without a
// host. PR-C ships the interfaces and the fakes; PR-D ships the real ones.
// An Op that needs a driver the real set does not provide yet gets
// ErrRefused("… lands in PR-D") from that driver, never a panic.
type Drivers interface {
	Systemd() Systemd
	Proc() Proc
	Gateway() GatewayDriver
	Files() Files
	Qdrant() Qdrant
	Elasticsearch() Elasticsearch
	TenantAPI() TenantAPI
}

// Systemd is `systemctl --user` with verb and unit-name allowlists.
type Systemd interface {
	DaemonReload(ctx context.Context) error
	Start(ctx context.Context, unit string) error
	Stop(ctx context.Context, unit string) error
	Enable(ctx context.Context, unit string) error
	Disable(ctx context.Context, unit string) error
	IsActive(ctx context.Context, unit string) (bool, error)
}

// Proc is the pidfile/proc surface for supervisor: manual tenants.
type Proc interface {
	// Listening reports whether port has a LISTEN socket.
	Listening(ctx context.Context, port int) (bool, error)
	// Signal sends sig to pid after verifying identity (cwd/cmdline match).
	Signal(ctx context.Context, pid int, wantCwd, wantCmd string, sig string) error
}

// GatewayDriver publishes and reloads the gateway (internal/ctl/gateway).
type GatewayDriver interface {
	// Apply publishes the next generation; the result is the gateway package's.
	Apply(ctx context.Context, dryRun bool) (generation int, detail string, err error)
	// Reload tests the LIVE tree, HUPs, confirms and probes — no new generation.
	Reload(ctx context.Context, dryRun bool) (detail string, err error)
	Rollback(ctx context.Context, to int) (detail string, err error)
}

// Files is the atomic-write surface (mode and group set BEFORE rename;
// per-operation approved roots; O_NOFOLLOW on destructive paths).
type Files interface {
	WriteAtomic(ctx context.Context, path string, data []byte, mode uint32) error
	Rename(ctx context.Context, from, to string) error
	Remove(ctx context.Context, path string) error
	ReadFile(ctx context.Context, path string) ([]byte, error)
}

// Qdrant is the store driver subset PR-C plans against (fakes only).
type Qdrant interface {
	Collections(ctx context.Context, baseURL string) ([]string, error)
	Snapshot(ctx context.Context, baseURL, collection string) (name string, err error)
}

// Elasticsearch is the store driver subset PR-C plans against (fakes only).
type Elasticsearch interface {
	Indices(ctx context.Context, baseURL string) ([]string, error)
	Snapshot(ctx context.Context, baseURL, repo, name string) error
}

// TenantAPI is the exact-allowlist client to a registered tenant origin.
type TenantAPI interface {
	Health(ctx context.Context, origin string) error
	ServiceAccount(ctx context.Context, origin, subject, action string) error
}

// ---------------------------------------------------------------- store

// Envelope is the sealed one-time delivery of minted secrets. Value is the
// serialized SecretsResponse.secrets, sealed with age to the backup
// recipients when configured, else kept only in daemon memory (Sealed false).
type Envelope struct {
	JobID     string
	Principal string
	Op        string
	CreatedAt time.Time
	ExpiresAt time.Time
	Sealed    bool
	Payload   []byte
}

// ListFilter is GET /v1/jobs' query.
type ListFilter struct {
	Tenant string
	State  model.JobState
	Limit  int
}

// Store is the durable record. Every method is safe for one daemon and any
// number of --direct CLIs on the same host: the implementation serializes
// through SQLite and the flock files, not through process memory.
type Store interface {
	// Create persists a queued job together with its idempotency key in one
	// transaction. It returns ErrDuplicate when the key exists with a
	// different request fingerprint, and (existing, nil) when it exists with
	// the same one.
	Create(ctx context.Context, job *model.Job, fingerprint string) (*model.Job, error)
	Get(ctx context.Context, id string) (*model.Job, error)
	List(ctx context.Context, f ListFilter) ([]model.Job, bool, error)
	// Update replaces the job record; the engine calls it at every transition.
	Update(ctx context.Context, job *model.Job) error
	// AppendStepLog appends redacted text to a step's log.
	AppendStepLog(ctx context.Context, id string, n int, text string) error
	StepLog(ctx context.Context, id string, n int) (string, error)
	// Running lists jobs in running/awaiting_cutover, for reconcile on start.
	Running(ctx context.Context) ([]model.Job, error)

	Audit(ctx context.Context, row model.AuditRow) error
	AuditList(ctx context.Context, limit int) ([]model.AuditRow, bool, error)

	PutEnvelope(ctx context.Context, e Envelope) error
	// TakeEnvelope returns the envelope once and destroys it (ErrGone after,
	// or when expired; ErrForbidden for another principal).
	TakeEnvelope(ctx context.Context, jobID, principal string, now time.Time) (*Envelope, error)

	// Backup writes a consistent copy of the database (VACUUM INTO).
	Backup(ctx context.Context, dest string) error
	Close() error
}

// ---------------------------------------------------------------- engine

// Engine is what the API layer and the CLI call. Submit is the single entry
// point for every mutation; the continuations re-authorize against the
// principal they are given, never against the job's stored one.
type Engine interface {
	// Submit plans req; with DryRun it returns (plan, nil, nil) and touches
	// nothing. Otherwise it persists the job and starts it, returning the
	// accepted job (state queued or running) — the caller polls Get.
	Submit(ctx context.Context, req Request) (*model.Plan, *model.Job, error)
	Get(ctx context.Context, id string) (*model.Job, error)
	List(ctx context.Context, f ListFilter) ([]model.Job, bool, error)
	StepLog(ctx context.Context, id string, n int) (string, error)
	// Resume restarts an interrupted job after reconciling its running step.
	Resume(ctx context.Context, id string, p Principal) (*model.Job, error)
	// Continue releases a job waiting in awaiting_cutover.
	Continue(ctx context.Context, id string, p Principal) (*model.Job, error)
	// Cancel stops a queued/running/interrupted job, rolling back what it
	// can. confirm is openapi.yaml's: a cancel that would roll succeeded
	// steps back is a mutation of the fleet in its own right, so it carries
	// the same confirm value the plan does (the tenant name for a destructive
	// op, "yes" otherwise) and answers ErrConfirmRequired without it.
	Cancel(ctx context.Context, id string, p Principal, confirm string) (*model.Job, error)
	// Secrets delivers the envelope once (ErrGone after; ErrForbidden for
	// another principal or a session credential).
	Secrets(ctx context.Context, id string, p Principal) (*model.SecretsResponse, error)
	Audit(ctx context.Context, limit int) ([]model.AuditRow, bool, error)
	// Reconcile is called once at daemon start: jobs whose worker is dead
	// become interrupted; their reservations are kept; nothing is resumed.
	Reconcile(ctx context.Context) (interrupted []string, err error)
}
