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
	// for an explicit Continue (`migrate-local` today).
	//
	// `handover` deliberately does NOT use it. A parked job keeps its locks,
	// and one of a handover's is the REGISTRY lock — the whole fleet's — which
	// it would then hold for the length of an operator's soak. Its phases are
	// four ordinary jobs gated on the registry row instead (ops/handover.go).
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
// ErrRefused("… lands in <PR>") from that driver, never a panic.
type Drivers interface {
	Systemd() Systemd
	Proc() Proc
	Gateway() GatewayDriver
	Files() Files
	Qdrant() Qdrant
	Elasticsearch() Elasticsearch
	TenantAPI() TenantAPI
	// PR-D adds the host surfaces `tenant create`, `backup`, `restore --as`
	// and `decommission` need in order to RUN rather than only to plan.
	Git() Git
	Build() Build
	Postgres() Postgres
	SQLite() SQLite
	Archive() Archive
	// PR-D2 adds the two surfaces `supervisor: instance` needs in order to
	// supervise a tenant WITHOUT a user manager: the apptainer instances the
	// stores run as, and the crontab `@reboot` line that is this host's only
	// boot hook (svcbvbrc has no linger and cron gets no logind session, so
	// `systemctl --user` cannot be driven from boot at all).
	Instances() Instances
	Crontab() Crontab
}

// UnitInfo is `systemctl --user show -p …` for one unit. It exists because
// IsActive answers a boolean, and a unit that failed, a unit the manager never
// loaded and a unit an operator stopped are three different facts hiding
// behind the same `false`.
//
// A unit the manager does not know is NOT an error: `systemctl show` of an
// unknown unit succeeds and reports LoadState=not-found, so the driver returns
// an empty FragmentPath (and an empty UnitFileState). Callers that want "this
// unit is gone" — decommission's post-check, the selftest's — read that
// emptiness rather than matching on an error.
type UnitInfo struct {
	// ActiveState is active/inactive/failed/activating/deactivating.
	ActiveState string
	// SubState is the per-type detail: running, dead, exited, failed.
	SubState string
	// Result is the last run's result: success, exit-code, timeout, signal…
	Result string
	// UnitFileState is enabled/disabled/linked/static, empty when unknown.
	UnitFileState string
	// FragmentPath is the unit file the manager resolved; empty means the
	// manager does not know this unit.
	FragmentPath string
	// MainPID is 0 when nothing is running.
	MainPID int
	// NRestarts counts systemd's own restarts of the unit.
	NRestarts int
	// ExecMainStatus is the main process's exit status.
	ExecMainStatus int
}

// Systemd is `systemctl --user` with verb and unit-name allowlists.
type Systemd interface {
	DaemonReload(ctx context.Context) error
	Start(ctx context.Context, unit string) error
	Stop(ctx context.Context, unit string) error
	Enable(ctx context.Context, unit string) error
	Disable(ctx context.Context, unit string) error
	IsActive(ctx context.Context, unit string) (bool, error)
	// Link makes a unit file OUTSIDE the search path loadable
	// (`systemctl --user link <abs unitPath>`), and is idempotent when the
	// unit already resolves to that path. The ctl writes units into its own
	// config tree, which the user manager does not search, and the drop-in
	// that would add it (SYSTEMD_UNIT_PATH) is a root item that is not on
	// coconut yet — so linking is how a rendered unit becomes a real one.
	Link(ctx context.Context, unitPath string) error
	// IsEnabled is `systemctl --user is-enabled <unit>`.
	IsEnabled(ctx context.Context, unit string) (bool, error)
	// Show is `systemctl --user show -p ActiveState -p SubState -p Result
	// -p UnitFileState -p FragmentPath -p MainPID -p NRestarts
	// -p ExecMainStatus --value <unit>`.
	Show(ctx context.Context, unit string) (UnitInfo, error)
	// ResetFailed is `systemctl --user reset-failed <unit>`, so that retrying
	// a job whose unit failed is not refused by systemd's own start limit.
	ResetFailed(ctx context.Context, unit string) error
}

// Proc is the pidfile/proc surface for supervisor: manual tenants.
type Proc interface {
	// Listening reports whether port has a LISTEN socket.
	Listening(ctx context.Context, port int) (bool, error)
	// Signal sends sig to pid after verifying identity (cwd/cmdline match).
	Signal(ctx context.Context, pid int, wantCwd, wantCmd string, sig string) error
	// Owner is the pid/uid behind the LISTEN socket on port (`ss -ltnp`, or
	// /proc when ss is absent); pid is 0 when the socket belongs to another
	// account, because an unprivileged reader sees THAT a port is taken
	// without seeing by whom — and that difference decides whether a port
	// collision is the ctl's to fix or an operator's to look at. uid is
	// meaningless when pid is 0, and an unbound port is (0, 0, nil): nothing
	// listening is a fact, not an error.
	Owner(ctx context.Context, port int) (pid, uid int, err error)
	// Spawn starts a DETACHED long-lived process and returns its pid. It is
	// how `supervisor: instance` runs a tenant's API without a user manager:
	// setsid (its own session and process group, so it survives the ctl and so
	// a later stop can signal the group), stdin /dev/null, stdout AND stderr
	// appended to LogPath, umask 0002, and the environment given — nothing
	// inherited, because the daemon's own environment carries the ctl's
	// credentials and a tenant's uvicorn must not see them.
	//
	// The pidfile is written, atomically and 0644, BEFORE Spawn returns. That
	// ordering is the whole contract: the ctl's record of what it started is
	// the pidfile, and a crash between the fork and the write leaves a running
	// process nothing can find. It is the same rule every step follows when it
	// checkpoints an external ID before the call that creates it.
	//
	// Spawn NEVER waits on the child. A supervisor that reaped its tenants
	// would block a job for the life of the tenant.
	Spawn(ctx context.Context, spec SpawnSpec) (pid int, err error)
	// Alive reports whether pid is a live process (/proc/<pid> exists, or
	// kill(pid, 0) succeeds). It answers about EXISTENCE only — whether the
	// process is the one the ctl started is Signal's identity check, and a
	// caller that cares asks both.
	//
	// A pid that is not running is (false, nil): a dead tenant is a fact the
	// reconcile and the `running` post-check read, not an error.
	Alive(ctx context.Context, pid int) (bool, error)
}

// SpawnSpec is one detached process, as Proc.Spawn starts it.
//
// Env is the child's WHOLE environment ("KEY=value" entries, as os/exec takes
// it) rather than an overlay: tenant.env ∪ secrets.env ∪ the api unit's
// `Environment=` lines is exactly what the systemd path gives the same
// process, and "whatever the daemon happened to be started with, plus these"
// is not reproducible and leaks the ctl's own credentials into a tenant.
//
// It therefore carries SECRETS. Nothing may log it, audit it, or put it on a
// call record: the values reach the child through its environment and through
// nothing else.
type SpawnSpec struct {
	// Program is the absolute path of the executable. The drivers never
	// search PATH, so a program that is not where this says fails with the
	// path in the message rather than with a surprise binary.
	Program string
	// Args are the arguments after the program name (os/exec's Args[1:]).
	Args []string
	// Dir is the working directory; empty means the caller does not care.
	Dir string
	// Env is the child's complete environment, "KEY=value" each.
	Env []string
	// LogPath is the file stdout and stderr are APPENDED to (created 0640 if
	// absent). One file for both, as the api unit's two `append:` lines are.
	LogPath string
	// PidFile is where the child's pid is written, atomically, 0644, before
	// Spawn returns.
	PidFile string
}

// Instance is one apptainer instance, as `apptainer instance list --json`
// reports it.
type Instance struct {
	// Name is the instance name — `<kind>-<manifest_name>`, the names the
	// hand-started tenants already run under (`qdrant-dev`,
	// `elasticsearch-hackathon`), so that an adopted tenant's live instances
	// are the ones the ctl manages after its handover.
	Name string
	// PID is the instance's starter process.
	PID int
	// Image is the SIF the instance was started from, which is how a caller
	// tells "this instance is the tenant's qdrant" from "something else is
	// using that name".
	Image string
}

// InstanceSpec is one `apptainer instance run`.
type InstanceSpec struct {
	// Name is the instance name (see Instance.Name). The real driver holds it
	// to `^(qdrant|elasticsearch|postgres)-[a-z][a-z0-9-]{0,31}$`: the ctl
	// supervises a tenant's three stores and nothing else on this host.
	Name string
	// SIF is the absolute path of the image.
	SIF string
	// Binds are "host:container" pairs, one --bind each.
	Binds []string
	// Env are the --env pairs — the CONTAINER's environment as apptainer puts
	// it on the argv, and therefore PUBLIC: /proc/<pid>/cmdline is 0444 on a
	// 1869-member host. No secret may be in here. The driver renders them in
	// sorted key order, so a job log and a `ps` line are comparable across
	// runs (apptainer itself does not care about the order).
	Env map[string]string
	// Args are the runscript arguments, after the instance name.
	Args []string
	// ExtraEnv is the CHILD PROCESS's environment — apptainer's own, not the
	// container's: APPTAINER_CACHEDIR and APPTAINER_CONFIGDIR, and for
	// postgres the APPTAINERENV_POSTGRES_PASSWORD that apptainer forwards
	// into the container as POSTGRES_PASSWORD. It NEVER reaches the argv,
	// which is the entire reason it is separate from Env: the unit learned the
	// same lesson the hard way (render/units.go's postgres comment).
	//
	// It may therefore carry a secret, and like SpawnSpec.Env it must not be
	// logged, audited or recorded on a call.
	ExtraEnv map[string]string
}

// Instances is the apptainer-instance surface `supervisor: instance` runs a
// tenant's stores through. It is the same thing the unit's ExecStart does
// (`apptainer run …`) with a name attached, which is what makes an instance
// stoppable and findable without a user manager.
type Instances interface {
	// List is `apptainer instance list --json`, every instance of the CURRENT
	// account. No instances is an empty list, not an error.
	List(ctx context.Context) ([]Instance, error)
	// Run is
	// `apptainer instance run --no-home <--bind …> <--env …> <sif> <name> <args…>`
	// with spec.ExtraEnv in the child's environment. It returns when apptainer
	// has started the instance; readiness is the caller's gate, not this one's.
	Run(ctx context.Context, spec InstanceSpec) error
	// Stop is `apptainer instance stop <name>` — SIGTERM to the instance, so
	// elasticsearch gets the graceful shutdown its units' TimeoutStopSec=120
	// exists for.
	//
	// An instance that is not running is SUCCESS. Stop is a step that gets
	// re-run — by a rollback, by a resumed job, by `fleet stop --all` over a
	// fleet half of which is already down — and a stop that failed because it
	// had nothing to do would turn every one of those into a failed job.
	Stop(ctx context.Context, name string) error
	// SeedConfigDir copies <sif>:<containerDir>/. into hostDir —
	// `apptainer exec --bind <hostDir>:/__seed <sif> cp -R <containerDir>/. /__seed/`,
	// which is exactly what `ragstack-ctl es-seed-config` runs today from the
	// ES unit's ExecStartPre.
	//
	// It is on THIS interface rather than behind a general "run any apptainer
	// command" seam for the same reason every other driver here is an exact
	// allowlist: the ctl needs one `apptainer exec`, with one bind and one
	// fixed argv, and a generic exec seam would be the place the next caller
	// puts a string a tenant's own configuration reached.
	//
	// It is unconditional. WHETHER to seed — the host directory is empty, so
	// an operator's own elasticsearch.yml is never overwritten — is the
	// caller's decision, taken through Files.ReadDir, because that is policy
	// and this is the copy.
	//
	// `supervisor: instance` needs it because the config bind SHADOWS the
	// image's own /usr/share/elasticsearch/config: an empty host directory is
	// an Elasticsearch that exits before it logs anything useful, and an
	// instance supervisor has no ExecStartPre to hang the seed off.
	SeedConfigDir(ctx context.Context, sif, containerDir, hostDir string) error
}

// Crontab is the CURRENT user's crontab, which on this host is the only boot
// hook there is: svcbvbrc has no linger and cron sessions get no logind
// session, so `systemctl --user` cannot be driven from `@reboot` at all
// (plan PR-D2, "Host facts").
//
// Two methods and no line editing: WHICH line the ctl owns, and the marker
// that identifies it, are policy and live in ops. The driver reads the whole
// crontab and writes the whole crontab.
type Crontab interface {
	// List is `/usr/bin/crontab -l`. An account with NO crontab is an empty
	// body and no error — crontab(1) exits 1 with "no crontab for <user>" on
	// stderr for that case, and a driver that passed the exit status through
	// would make "this host has never had a crontab" indistinguishable from
	// "cron is broken".
	List(ctx context.Context) ([]byte, error)
	// Set is `/usr/bin/crontab -` with body on STDIN. Not a file argument:
	// a file the ctl wrote and crontab(1) then read is a window in which
	// anything that can write that path chooses the account's cron jobs.
	//
	// body replaces the whole crontab, so a caller reads with List, edits the
	// one line it owns, and writes back.
	Set(ctx context.Context, body []byte) error
}

// GatewayDriver publishes and reloads the gateway (internal/ctl/gateway).
type GatewayDriver interface {
	// Apply publishes the next generation; the result is the gateway package's.
	Apply(ctx context.Context, dryRun bool) (generation int, detail string, err error)
	// Reload tests the LIVE tree, HUPs, confirms and probes — no new generation.
	Reload(ctx context.Context, dryRun bool) (detail string, err error)
	Rollback(ctx context.Context, to int) (detail string, err error)
	// Routes names the tenants the LIVE gateway routes right now (the
	// published include's tenant list). A fence or a quarantine of a tenant
	// that is not routed has nothing to publish, and says so.
	Routes(ctx context.Context) ([]string, error)
	// Probe GETs one PATH (`/ragstack/<t>/ui/`) through the live gateway and
	// returns the status code.
	//
	// It is a path rather than a URL because the base is the gateway's own —
	// a driver that accepted a URL would be a driver an op could point at any
	// host — and it answers the status rather than the body because the one
	// question a step asks here is "does this route serve, 404 or 502".
	//
	// A publish already probes `/ragstack/<t>/api/health` for every tenant it
	// lists (gateway/publish.go's probeOnce), which proves the API route
	// exists; nothing in that path looks at the UI route, and the static UI is
	// exactly the route that can be published correctly and still 404 because
	// the directory behind the alias is empty.
	Probe(ctx context.Context, path string) (status int, err error)
}

// Files is the atomic-write surface (mode and group set BEFORE rename;
// per-operation approved roots; O_NOFOLLOW on destructive paths).
type Files interface {
	WriteAtomic(ctx context.Context, path string, data []byte, mode uint32) error
	Rename(ctx context.Context, from, to string) error
	Remove(ctx context.Context, path string) error
	ReadFile(ctx context.Context, path string) ([]byte, error)
	// MkdirAll creates path and every missing parent, and is how `tenant
	// create` lays a tenant tree down (2770, setgid, so the group is
	// inherited). It refuses a path outside the approved roots exactly as
	// WriteAtomic does, and it NEVER chmods a directory that already existed:
	// /rag/data/tenants is wilke 755 and shared with 1869 members of `cels`,
	// so a driver that "fixed" the mode of a parent it did not create would be
	// changing a directory nobody asked it to touch.
	MkdirAll(ctx context.Context, path string, mode uint32) error
	// CopyFile copies src to dst with mode, STREAMING: dst is written through a
	// temporary file in its own directory whose mode is set before the rename,
	// exactly as WriteAtomic does, and under the same approved roots.
	//
	// It exists because `restore --as` copies a bundle's store files into a
	// fresh tenant, and those are the largest files this system has: a qdrant
	// snapshot or an elasticsearch segment is gigabytes. ReadFile + WriteAtomic
	// would be correct and would load every one of them into the daemon's heap,
	// which is how a restore takes the host down.
	//
	// src is a READ and so is not root-checked (like ReadFile), but it is
	// opened without following a final symlink: a bundle is operator input, and
	// a link planted in one must not become a copy of whatever it points at
	// inside the new tenant's tree.
	CopyFile(ctx context.Context, src, dst string, mode uint32) error
	// ReadDir lists ONE directory (not recursively), sorted by name. It is a
	// read, so it is not root-checked; an absent directory is fs.ErrNotExist,
	// which callers tell apart from "unreadable" with errors.Is exactly as
	// they do for ReadFile.
	//
	// A backup needs it because two of the things it copies are named by the
	// host rather than by the ctl — the per-collection `manifests/*.json` and
	// the `secrets.env.bak-*` siblings — and a step that guessed those names
	// would silently leave files out of a bundle that claims to be complete.
	ReadDir(ctx context.Context, dir string) ([]DirEntry, error)
	// Sha256 is the hex digest and the size of one file, computed by STREAMING
	// it. The bundle's SHA256SUMS covers elasticsearch segment files that are
	// gigabytes each; a checksum built on ReadFile would load every one of them
	// into the daemon's heap.
	Sha256(ctx context.Context, path string) (hex string, size int64, err error)
	// DiskFree is the bytes available to this account on the filesystem
	// holding path (statfs f_bavail × f_bsize). The backup's precheck refuses
	// to start a bundle onto a filesystem that cannot hold it, which is the
	// one failure mode that leaves a half-written bundle AND a full disk for
	// every other tenant on the host.
	DiskFree(ctx context.Context, path string) (int64, error)
}

// DirEntry is one entry of Files.ReadDir: the base name and whether it is a
// directory. Nothing else — a step that wanted a mode or an mtime would be
// making a decision the registry should already have recorded.
type DirEntry struct {
	Name  string
	IsDir bool
}

// Qdrant is the store driver subset the ctl talks to over loopback.
type Qdrant interface {
	Collections(ctx context.Context, baseURL string) ([]string, error)
	Snapshot(ctx context.Context, baseURL, collection string) (name string, err error)
	// Ready is GET /readyz (falling back to /collections) answering 200.
	Ready(ctx context.Context, baseURL string) error
	// Count is POST /collections/{collection}/points/count with exact=true:
	// an inexact count cannot prove a fenced backup kept every point, which
	// is the only reason the ctl counts at all.
	Count(ctx context.Context, baseURL, collection string) (int64, error)
	// Recover is PUT /collections/{collection}/snapshots/recover?wait=true
	// with {"location": "file:///qdrant/snapshots/…"} — the location is a
	// path INSIDE the container, which is why it is the caller's to build.
	Recover(ctx context.Context, baseURL, collection, location string) error
	// DeleteSnapshot is DELETE /collections/{collection}/snapshots/{name};
	// it is the rollback of Snapshot, so a failed backup leaves no file
	// growing inside the tenant's storage.
	DeleteSnapshot(ctx context.Context, baseURL, collection, name string) error
}

// Elasticsearch is the store driver subset the ctl talks to over loopback.
type Elasticsearch interface {
	Indices(ctx context.Context, baseURL string) ([]string, error)
	// Snapshot is PUT _snapshot/{repo}/{name}?wait_for_completion=true over
	// EXACTLY the given indices; it is an error unless the response says state
	// SUCCESS and failed == 0, because a PARTIAL snapshot that the ctl
	// recorded as a backup is the worst outcome this whole verb has.
	//
	// The list is a parameter rather than a `*` inside the driver because the
	// caller's inventory (Indices) already excludes the cluster's own
	// dot-prefixed system indices: a snapshot of `*` covered indices the
	// manifest did not list, and on a shared node that means another tenant's
	// .security landing in this tenant's bundle.
	Snapshot(ctx context.Context, baseURL, repo, name string, indices []string) error
	// Ready is GET _cluster/health?wait_for_status=yellow&timeout=… → 200.
	Ready(ctx context.Context, baseURL string) error
	// RegisterRepo is PUT _snapshot/{repo} {type: fs, settings: {location,
	// readonly}}; location must be under the cluster's path.repo or ES itself
	// refuses.
	RegisterRepo(ctx context.Context, baseURL, repo, location string, readonly bool) error
	// UnregisterRepo is DELETE _snapshot/{repo}. It removes the registration
	// only — the snapshot files stay where they are, which is what lets the
	// backup move the directory into the bundle afterwards.
	UnregisterRepo(ctx context.Context, baseURL, repo string) error
	// Snapshots is GET _snapshot/{repo}/_all, the names the repository holds.
	//
	// It is how a backup VERIFIES its own snapshot: `_restore` has no dry run,
	// so the bundle's proof is that the directory, re-registered read-only
	// under a second name, is a repository elasticsearch can open and that the
	// snapshot this job took is in it. A repository ES cannot read answers an
	// error; an empty repository answers an empty list.
	Snapshots(ctx context.Context, baseURL, repo string) ([]string, error)
	// Restore is POST _snapshot/{repo}/{name}/_restore?wait_for_completion=true
	// for the named indices (all of the snapshot's when indices is empty).
	Restore(ctx context.Context, baseURL, repo, name string, indices []string) error
	// Count is GET /{index}/_count.
	Count(ctx context.Context, baseURL, index string) (int64, error)
}

// TenantAPI is the exact-allowlist client to a registered tenant origin.
type TenantAPI interface {
	Health(ctx context.Context, origin string) error
	// ServiceAccount is POST /v1/admin/service-accounts with X-API-Key, for
	// action create|disable|enable.
	//
	// It carries the CREDENTIAL and the RECORD: a tenant the ctl just created
	// has exactly one admin key, minted minutes ago and held in memory for the
	// length of the job, and role/purpose are what the registry row and the
	// tenant's own ledger have to agree on afterwards. apiKey is a secret: it
	// is never logged, never checkpointed and never part of a step's targets.
	ServiceAccount(ctx context.Context, origin, apiKey, subject, role, purpose, action string) error
	// Version is GET /v1/version, as the post-create proof that the API that
	// answered is the artifact the ctl checked out.
	Version(ctx context.Context, origin, apiKey string) (map[string]any, error)
	// DeepHealth is GET /v1/health/deep → 200: the tenant's own verdict on
	// every store it was configured with, which is a stronger post-check than
	// a port that accepts a connection.
	DeepHealth(ctx context.Context, origin, apiKey string) error
	// Collections is GET /v1/collections?counts=false — the inventory a
	// restore compares against the bundle manifest.
	Collections(ctx context.Context, origin, apiKey string) ([]string, error)
	// CollectionCounts is GET /v1/collections?counts=true: the inventory WITH
	// each collection's chunk count, which is the census a handover's release
	// takes and its take checks back.
	//
	// It is a separate method rather than a flag on Collections because the
	// counts cost a query against every store: the restore that compares an
	// inventory must not start paying for numbers it does not read.
	CollectionCounts(ctx context.Context, origin, apiKey string) (map[string]int64, error)
	// RunningIngestJobs is GET /v1/jobs (admin) reduced to the ids of the jobs
	// that have not finished. A handover's release refuses over one: stopping
	// the API mid-ingest leaves a collection half-written, and the release is
	// the last moment at which anybody can decide to wait.
	RunningIngestJobs(ctx context.Context, origin, apiKey string) ([]string, error)
	// KeyStatus dials the tenant with ONE credential and returns the status it
	// answered — 200 for a key the tenant accepts, 401 for one it does not.
	//
	// It is the only method here that treats a 401 as an ANSWER rather than as
	// a failure, and that is its whole purpose: `key revoke --prove` has to be
	// able to state "the old key now answers 401" as a checked fact rather
	// than as an assumption about what rewriting a file did.
	KeyStatus(ctx context.Context, origin, apiKey string) (status int, err error)
	// Ingest is POST /v1/ingest with a SERVER-SIDE path (the tenant confines
	// it under INGEST_ROOT), answering the ingest job's id.
	//
	// It exists for ONE caller: `ragstack-ctl selftest`, which has to put a
	// document into the tenant it just created so that the backup has
	// something to fence and the restore has something to prove. That is why
	// the real client refuses it unless the origin's port is in the SANDBOX
	// range: the control plane writing into a production tenant's corpus is
	// not an operation anybody asked for, and the exact allowlist this driver
	// is must not grow a write verb for the adopted tenants by accident.
	Ingest(ctx context.Context, origin, apiKey, path string) (jobID string, err error)
	// IngestStatus is GET /v1/ingest/{job_id}: the job's state — accepted,
	// running, completed or failed, and "unknown" for an id the tenant does
	// not have (which it answers 200). The selftest polls it; nothing else
	// does, and it carries the same sandbox-only refusal as Ingest.
	IngestStatus(ctx context.Context, origin, apiKey, jobID string) (state string, err error)
}

// Git is the mirror-and-worktree surface `fleet artifact prepare` and
// `tenant create` need. Every tenant runs its own checkout at a pinned sha
// (MEMORY: "tenant code isolation"), so resolving a ref once and checking THAT
// sha out is the whole contract.
type Git interface {
	// ResolveRef is `git -C <mirror> rev-parse --verify <ref>^{commit}`,
	// returning the 40-hex sha.
	ResolveRef(ctx context.Context, mirror, ref string) (sha string, err error)
	// AddWorktree is `git -C <mirror> worktree add --detach <dest> <sha>`;
	// dest must be under the approved roots, and detached because a tenant's
	// checkout must never follow a branch somebody moves.
	AddWorktree(ctx context.Context, mirror, sha, dest string) error
	// RemoveWorktree is `git -C <mirror> worktree remove --force <dest>`
	// followed by `worktree prune`, so the mirror's administrative entry goes
	// with the directory.
	RemoveWorktree(ctx context.Context, mirror, dest string) error
	// Describe is `git -C <dir> describe --tags --always --dirty`, recorded in
	// manifests so a bundle says which code wrote it.
	Describe(ctx context.Context, dir string) (string, error)
	// HeadSHA is `git -C <dir> rev-parse --verify HEAD^{commit}`, the 40-hex
	// commit dir's HEAD resolves to right now — on a branch, detached, or
	// anything else HEAD can be. Unlike ResolveRef, dir is a WORKING TREE, not
	// a bare mirror: `tenant rebase-worktree` uses it to pin the sha a
	// pre-existing (non-ctl-managed) worktree is actually sitting at, before
	// checking that same sha out fresh from the mirror.
	HeadSHA(ctx context.Context, dir string) (sha string, err error)
	// RepairWorktree is `git -C <mirror> worktree repair <path>`: after a
	// worktree directory is RENAMED (tenant rebase-worktree swaps
	// `<worktree>.mirror` into place), the mirror's administrative entry
	// still names the old path and `git worktree list` shows it prunable;
	// repair rewrites both back-pointers from the directory's own .git file.
	RepairWorktree(ctx context.Context, mirror, path string) error
}

// Build is the node/vite surface. It is split from Git because the two fail
// for unrelated reasons and an operator reading a failed job should not have
// to guess which half broke.
type Build interface {
	// NpmCI is `npm ci --no-audit --no-fund` in <worktree>/frontend with
	// NPM_CONFIG_CACHE=<cacheDir>. It is the ONLY step that may reach the
	// network, and it is CLI-only (`fleet artifact prepare`): the daemon never
	// installs packages.
	NpmCI(ctx context.Context, worktree, cacheDir string) error
	// UI is `node_modules/.bin/vite build --base <base> --outDir <outDir>
	// --emptyOutDir` in <worktree>/frontend. It refuses when node_modules is
	// absent rather than installing them, because a tenant create that
	// silently pulled packages from the network would be a build nobody
	// reviewed.
	UI(ctx context.Context, worktree, base, outDir string) error
}

// PostgresSpec names one tenant's postgres: the image to exec, the socket
// directory bound into it, and the database and role inside. There is no
// password here on purpose — socket auth inside the container is `trust`, and
// the role password lives only in the tenant's secrets.env.
type PostgresSpec struct {
	// SIF is the absolute path of the postgres Apptainer image.
	SIF string
	// RunDir is <data_dir>/postgres/run, bound to /var/run/postgresql.
	RunDir string
	// DB is the database name, User the role.
	DB   string
	User string
	// Port is the server's port — the tenant's +5 — which names the socket
	// file too (`.s.PGSQL.<port>`): pg_isready, pg_dump and pg_restore dial
	// 5432 by default and answered "no response" to every local instance
	// until they were told. Zero means the tools' default.
	Port int
}

// Postgres is `apptainer exec` against the tenant's own image, over the socket
// bind — never a TCP connection with a password.
type Postgres interface {
	// Ready is `apptainer exec --bind <RunDir>:/var/run/postgresql <SIF>
	// pg_isready -h /var/run/postgresql`.
	Ready(ctx context.Context, spec PostgresSpec) error
	// Dump is `pg_dump -Fc -h /var/run/postgresql -U <User> -d <DB> -f <out>`
	// (out bound into the container). Custom format, so a restore can be
	// selective and does not depend on psql parsing.
	Dump(ctx context.Context, spec PostgresSpec, out string) error
	// Restore is `pg_restore --no-owner --role=<User> -d <DB> <in>`: the
	// bundle's dump belongs to whichever role wrote it, and a restore --as
	// creates a tenant with a different one.
	Restore(ctx context.Context, spec PostgresSpec, in string) error
}

// SQLite backs the ctl's own state files and the tenant's.
type SQLite interface {
	// Backup runs PRAGMA wal_checkpoint(TRUNCATE), then PRAGMA
	// integrity_check (which must answer "ok"), then VACUUM INTO dst at 0640.
	// VACUUM INTO rather than a file copy: copying a database with a live WAL
	// beside it produces a file that opens and is missing the last writes.
	Backup(ctx context.Context, src, dst string) (integrity string, err error)
}

// ArchiveLimits bound what an extraction may produce. A bundle is operator
// input, and an archive with a million entries or a terabyte of zeroes is the
// cheapest way to take a host down.
type ArchiveLimits struct {
	MaxEntries int
	MaxBytes   int64
}

// Archive is the tar surface for `backup --tar` and `restore --from`.
type Archive interface {
	// Create writes a tar of dir to out with RELATIVE paths and without
	// following symlinks.
	Create(ctx context.Context, dir, out string) error
	// Extract unpacks tarPath under dest, refusing absolute or traversing
	// entry names, symlinks, hardlinks, device and other special entries, and
	// anything over limits.
	Extract(ctx context.Context, tarPath, dest string, limits ArchiveLimits) error
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
