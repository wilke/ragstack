package model

// Wire types for the job engine (PR-C): job.json, plan.json, jobs_response.json,
// audit_response.json, secrets_response.json and op_request.json. Every field
// name and enum value is the contract's; the engine in internal/ctl/jobs owns
// the transitions, this file only describes the shapes a client sees.

// JobState is job.json#/$defs/JobState.
type JobState string

const (
	JobQueued          JobState = "queued"
	JobRunning         JobState = "running"
	JobAwaitingCutover JobState = "awaiting_cutover"
	JobSucceeded       JobState = "succeeded"
	JobFailed          JobState = "failed"
	JobRolledBack      JobState = "rolled_back"
	JobInterrupted     JobState = "interrupted"
	JobCancelled       JobState = "cancelled"
)

// Terminal reports whether no transition can follow s.
func (s JobState) Terminal() bool {
	switch s {
	case JobSucceeded, JobFailed, JobRolledBack, JobCancelled:
		return true
	}
	return false
}

// StepState is job.json#/$defs/Step.state.
type StepState string

const (
	StepPending     StepState = "pending"
	StepRunning     StepState = "running"
	StepSucceeded   StepState = "succeeded"
	StepFailed      StepState = "failed"
	StepSkipped     StepState = "skipped"
	StepRolledBack  StepState = "rolled_back"
	StepInterrupted StepState = "interrupted"
)

// AuthMethod is how the principal of a job authenticated (job.json auth_method).
type AuthMethod string

const (
	AuthAPIKey  AuthMethod = "api_key"
	AuthBearer  AuthMethod = "bearer"
	AuthSession AuthMethod = "session"
	AuthLocal   AuthMethod = "local"
)

// LockName is one element of the single fleet lock order
// (job.json lock.order): registry → manifest → tenant → gateway → images.
type LockName string

const (
	LockRegistry LockName = "registry"
	LockManifest LockName = "manifest"
	LockTenant   LockName = "tenant"
	LockGateway  LockName = "gateway"
	LockImages   LockName = "images"
)

// LockOrder is the only order locks may be taken in, by daemon and --direct
// CLI alike. A holder of a later lock never waits for an earlier one.
var LockOrder = []LockName{LockRegistry, LockManifest, LockTenant, LockGateway, LockImages}

// WorkerMode is job.json worker.mode.
type WorkerMode string

const (
	WorkerDaemon WorkerMode = "daemon"
	WorkerDirect WorkerMode = "direct"
)

// RollbackState is job.json rollback.state.
type RollbackState string

const (
	RollbackNotNeeded RollbackState = "not_needed"
	RollbackSucceeded RollbackState = "succeeded"
	RollbackFailed    RollbackState = "failed"
	RollbackPartial   RollbackState = "partial"
)

// JobWorker is job.json worker (null while queued or after the worker exits).
type JobWorker struct {
	PID  int        `json:"pid"`
	Host string     `json:"host"`
	Mode WorkerMode `json:"mode"`
}

// JobLock is job.json lock: the lock files held, in LockOrder.
type JobLock struct {
	Order []LockName `json:"order"`
	Since string     `json:"since"`
}

// Reservation is job.json reservations[]: a resource (port block, staging
// dir, snapshot name…) that stays reserved while the job is interrupted or
// awaiting cutover. Until is null for "until the job reaches a terminal
// state".
type Reservation struct {
	Resource string     `json:"resource"`
	Until    NullString `json:"until"`
}

// JobError is job.json error. Code follows the finding-code grammar.
type JobError struct {
	Step   *int   `json:"step"`
	Code   string `json:"code"`
	Detail string `json:"detail"`
}

// JobRollback is job.json rollback.
type JobRollback struct {
	Attempted bool          `json:"attempted"`
	State     RollbackState `json:"state"`
	Detail    NullString    `json:"detail"`
}

// Step is job.json#/$defs/Step. Log is the redacted step log for an
// operator; a viewer receives null (jobs_response viewer_fields). Checkpoint
// says the step recorded durable state (ExternalIDs) BEFORE its external
// call, which is what makes reconcile-on-restart possible. ExternalIDs are
// snapshot names, unit names, staging dirs, gateway generations.
type Step struct {
	N           int        `json:"n"`
	Kind        string     `json:"kind"`
	Title       string     `json:"title"`
	State       StepState  `json:"state"`
	Attempts    int        `json:"attempts"`
	StartedAt   NullString `json:"started_at"`
	FinishedAt  NullString `json:"finished_at"`
	Error       NullString `json:"error"`
	Log         NullString `json:"log"`
	Checkpoint  bool       `json:"checkpoint"`
	ExternalIDs []string   `json:"external_ids"`
}

// Job is job.json. Tenant is null for fleet-scoped ops (gateway apply).
// Result is op-specific and never carries a secret: minted values travel only
// in the delivery envelope (SecretsResponse).
type Job struct {
	ID             string         `json:"id"`
	Op             string         `json:"op"`
	Tenant         NullString     `json:"tenant"`
	Principal      string         `json:"principal"`
	AuthMethod     AuthMethod     `json:"auth_method"`
	SudoUser       NullString     `json:"sudo_user"`
	State          JobState       `json:"state"`
	PlanHash       string         `json:"plan_hash"`
	RequestID      string         `json:"request_id"`
	IdempotencyKey string         `json:"idempotency_key"`
	CreatedAt      string         `json:"created_at"`
	StartedAt      NullString     `json:"started_at"`
	FinishedAt     NullString     `json:"finished_at"`
	Worker         *JobWorker     `json:"worker"`
	Lock           *JobLock       `json:"lock"`
	Reservations   []Reservation  `json:"reservations"`
	CurrentStep    *int           `json:"current_step"`
	Steps          []Step         `json:"steps"`
	Result         map[string]any `json:"result"`
	Error          *JobError      `json:"error"`
	Rollback       *JobRollback   `json:"rollback"`
}

// WouldWrite is plan.json steps[].would_write[]: a file the step would create
// or replace, with the mode it would get and a redacted preview (≤ 64 KiB,
// null for binary or secret-bearing content).
type WouldWrite struct {
	Path    string     `json:"path"`
	Mode    string     `json:"mode"`
	Preview NullString `json:"preview"`
}

// WouldRun is plan.json steps[].would_run[]: an argv the step would execute
// (absolute program, no shell).
type WouldRun struct {
	Argv []string `json:"argv"`
}

// PlannedStep is plan.json#/$defs/PlannedStep.
type PlannedStep struct {
	N           int          `json:"n"`
	Kind        string       `json:"kind"`
	Title       string       `json:"title"`
	Destructive bool         `json:"destructive"`
	Targets     []string     `json:"targets"`
	WouldWrite  []WouldWrite `json:"would_write"`
	WouldRun    []WouldRun   `json:"would_run"`
	Warnings    []string     `json:"warnings"`
}

// Plan is plan.json: the dry-run answer and the thing a job is executed
// against. PlanHash = sha256 over the canonical JSON of {op, tenant,
// args_redacted, registry_generation, doctor.hash, steps[].{kind, target,
// args_sha256}, schema_version}; the engine recomputes it after taking the
// locks and refuses (plan_stale) when it moved. ConfirmValue is the exact
// string `confirm` must carry when RequiresConfirm (a destructive op names
// the tenant; a non-destructive one uses "yes").
type Plan struct {
	PlanHash           string         `json:"plan_hash"`
	Op                 string         `json:"op"`
	Tenant             NullString     `json:"tenant"`
	RegistryGeneration int64          `json:"registry_generation"`
	SchemaVersion      int            `json:"schema_version"`
	Doctor             DoctorResponse `json:"doctor"`
	RequiresConfirm    bool           `json:"requires_confirm"`
	ConfirmValue       NullString     `json:"confirm_value"`
	Steps              []PlannedStep  `json:"steps"`
	Warnings           []string       `json:"warnings"`
}

// OpRequest is op_request.json, the body of every mutation. ForceWithDoctorDiff
// carries the doctor hash the caller saw; it lets a YELLOW doctor through,
// never a red one, and never bypasses authz, containment, ownership or
// fencing. CtlAPIKey is required when the request authenticated over a
// session (a browser): mutations re-present the ctl key per request and the
// browser never persists it.
type OpRequest struct {
	DryRun              bool           `json:"dry_run"`
	IdempotencyKey      string         `json:"idempotency_key"`
	Confirm             string         `json:"confirm,omitempty"`
	CtlAPIKey           string         `json:"ctl_api_key,omitempty"`
	ForceWithDoctorDiff string         `json:"force_with_doctor_diff,omitempty"`
	Args                map[string]any `json:"args"`
}

// AuditPhase is audit_response.json rows[].phase: a job writes one row at
// submit (intent) and one at the end (result); a refused request writes a
// single intent row whose outcome says why.
type AuditPhase string

const (
	AuditIntent AuditPhase = "intent"
	AuditResult AuditPhase = "result"
)

// AuditRow is audit_response.json#/$defs/AuditRow. ArgsRedacted has every
// secret-class value replaced before it is written anywhere.
type AuditRow struct {
	ID           int64          `json:"id"`
	At           string         `json:"at"`
	Phase        AuditPhase     `json:"phase"`
	Principal    string         `json:"principal"`
	AuthMethod   AuthMethod     `json:"auth_method"`
	SudoUser     NullString     `json:"sudo_user"`
	RequestID    string         `json:"request_id"`
	Op           string         `json:"op"`
	Tenant       NullString     `json:"tenant"`
	JobID        NullString     `json:"job_id"`
	ArgsRedacted map[string]any `json:"args_redacted"`
	PlanHash     NullString     `json:"plan_hash"`
	Outcome      string         `json:"outcome"`
	Error        NullString     `json:"error"`
	DurationMS   *int64         `json:"duration_ms"`
}

// Secret is secrets_response.json secrets[]: one minted value, delivered once.
type Secret struct {
	ID    string `json:"id"`
	Label string `json:"label"`
	Role  string `json:"role"`
	Value string `json:"value"`
}

// SecretsResponse is GET /v1/jobs/{id}/secrets (secrets_response.json). The
// first successful read destroys the envelope; later reads are 410.
type SecretsResponse struct {
	JobID       string   `json:"job_id"`
	DeliveredAt string   `json:"delivered_at"`
	ExpiresAt   string   `json:"expires_at"`
	Secrets     []Secret `json:"secrets"`
}
