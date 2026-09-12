// Package model holds the wire types of the ctl's read surface: the body of
// EVERY response the daemon writes — health, version, me, session, fleet,
// tenants, env, logs, doctor, gateway status and render, settings, jobs and
// audit — plus the error envelope every non-2xx shares.
//
// "Every" is load-bearing. TestResponsesValidateAgainstContract can only see
// types declared here, so a body assembled inline in a handler is a body
// nothing checks against its schema — which is how a settings response that
// violated settings_response.json shipped under a test that claimed to cover
// every response type.
//
// Each type mirrors one file under contracts/ctl/schemas exactly — field
// names, required-vs-nullable, enum members. The contract makes every member
// required and models "unknown" as an explicit null, so nothing here carries
// `omitempty` except the two members the contract itself declares optional
// (Finding.repair, Error.extra). Nullable scalars reuse the registry's
// NullString/NullPort (zero value ⇒ null); nullable objects are pointers.
//
// TestResponsesValidateAgainstContract marshals a populated example of every
// type and validates it against the schema with python jsonschema, so a
// drift between these structs and the contract fails the Go test run.
package model

import (
	"encoding/json"

	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// NullString is the registry's nullable string: "" marshals as null.
type NullString = registry.NullString

// NullInt is the registry's nullable integer (NullPort): 0 marshals as null.
// Reused for pids, which are never 0 either.
type NullInt = registry.NullPort

// HealthState is one probe's verdict (fleet_response.json#/$defs/HealthState).
type HealthState string

// Health states.
const (
	HealthOK       HealthState = "ok"
	HealthDegraded HealthState = "degraded"
	HealthDown     HealthState = "down"
	HealthUnknown  HealthState = "unknown"
	// HealthNA is "the ctl does not probe this leg": a shared store it only
	// observes, or a probe this PR does not make (deep health).
	HealthNA HealthState = "n/a"
)

// UnitState is a systemd ActiveState, or its manual-supervision analogue
// (fleet_response.json#/$defs/UnitState).
type UnitState string

// Unit states.
const (
	UnitActive       UnitState = "active"
	UnitActivating   UnitState = "activating"
	UnitDeactivating UnitState = "deactivating"
	UnitInactive     UnitState = "inactive"
	UnitFailed       UnitState = "failed"
	UnitNA           UnitState = "n/a"
)

// StoresMode summarises a tenant's two store ownerships in one word.
type StoresMode string

// Stores modes.
const (
	StoresDedicated StoresMode = "dedicated" // both exclusive
	StoresShared    StoresMode = "shared"    // both shared
	StoresMixed     StoresMode = "mixed"     // one of each (lucid-next)
	StoresUnknown   StoresMode = "unknown"   // ownership not confirmed
)

// State is a tenant's lifecycle state (registry.json#/$defs/Tenant.state).
type State string

// Tenant states.
const (
	StateProvisioned    State = "provisioned"
	StateActive         State = "active"
	StateStopped        State = "stopped"
	StateMigrating      State = "migrating"
	StateQuarantined    State = "quarantined"
	StateDecommissioned State = "decommissioned"
)

// Owner is the account that runs a tenant's processes.
type Owner string

// Owners.
const (
	OwnerSvc   Owner = "svcbvbrc"
	OwnerWilke Owner = "wilke"
)

// Supervisor is how a tenant's processes are started and found.
type Supervisor string

// Supervisors.
const (
	SupervisorSystemd Supervisor = "systemd"
	SupervisorManual  Supervisor = "manual"
)

// Level is a doctor finding's severity (doctor_response.json#/$defs/Finding).
type Level string

// Finding levels. Error blocks mutations on the scope, Warn warns, Info tells.
const (
	LevelInfo  Level = "info"
	LevelWarn  Level = "warn"
	LevelError Level = "error"
)

// Status is the max over a doctor run's finding levels.
type Status string

// Doctor statuses.
const (
	StatusGreen  Status = "green"
	StatusYellow Status = "yellow"
	StatusRed    Status = "red"
)

// Class is a settings-classification class as the env API spells it.
type Class string

// Env key classes (env_response.json#/$defs/EnvKey.class).
const (
	ClassPublic            Class = "public"
	ClassSecret            Class = "secret"
	ClassExecutableSurface Class = "executable-surface"
	ClassUnsupported       Class = "unsupported"
)

// Source is where an env key was read from.
type Source string

// Env key sources.
const (
	SourceTenantEnv    Source = "tenant.env"
	SourceSecretsEnv   Source = "secrets.env"
	SourceProvisionEnv Source = "provision.env"
	SourceUnit         Source = "unit"
	SourceRegistry     Source = "registry"
)

// EnvDrift names which pair of (registry, file, live process) disagrees.
type EnvDrift string

// Env drift kinds (null ⇒ the three agree).
const (
	DriftRegistryVsFile EnvDrift = "registry_vs_file"
	DriftFileVsLive     EnvDrift = "file_vs_live"
	DriftRegistryVsLive EnvDrift = "registry_vs_live"
	DriftFileOnly       EnvDrift = "file_only"
	DriftLiveOnly       EnvDrift = "live_only"
)

// LogFile selects which of a tenant's logs to tail.
type LogFile string

// Log files.
const (
	LogAPI    LogFile = "api"
	LogQdrant LogFile = "qdrant"
	LogES     LogFile = "es"
	LogUI     LogFile = "ui"
)

// ErrorCode is the machine-readable failure class of error.json. One status
// per code, so the code alone reconstructs the status in a log.
type ErrorCode string

// Error codes.
const (
	CodeAuthRequired    ErrorCode = "auth_required"    // 401
	CodeForbidden       ErrorCode = "forbidden"        // 403
	CodeBothCredentials ErrorCode = "both_credentials" // 400
	CodeNotFound        ErrorCode = "not_found"        // 404
	CodeValidation      ErrorCode = "validation"       // 422
	CodeLocked          ErrorCode = "locked"           // 409
	CodePlanStale       ErrorCode = "plan_stale"       // 409
	CodeDuplicate       ErrorCode = "duplicate"        // 409
	CodeDoctorRed       ErrorCode = "doctor_red"       // 409
	CodeConfirmRequired ErrorCode = "confirm_required" // 428
	CodeRefused         ErrorCode = "refused"          // 409
	CodeRateLimited     ErrorCode = "rate_limited"     // 429
	CodeInternal        ErrorCode = "internal"         // 500
)

// HTTPStatus maps a code to the one status it is ever returned with.
func (c ErrorCode) HTTPStatus() int {
	switch c {
	case CodeBothCredentials:
		return 400
	case CodeAuthRequired:
		return 401
	case CodeForbidden:
		return 403
	case CodeNotFound:
		return 404
	case CodeValidation:
		return 422
	case CodeConfirmRequired:
		return 428
	case CodeRateLimited:
		return 429
	case CodeInternal:
		return 500
	case CodeLocked, CodePlanStale, CodeDuplicate, CodeDoctorRed, CodeRefused:
		return 409
	default:
		return 500
	}
}

// Error is the body of every non-2xx response (error.json).
type Error struct {
	Detail    string         `json:"detail"`
	Code      ErrorCode      `json:"code"`
	RequestID string         `json:"request_id"` // ^[0-9a-f]{16}$, = X-Request-Id
	Extra     map[string]any `json:"extra,omitempty"`
}

// FleetResponse is GET /v1/fleet (fleet_response.json).
type FleetResponse struct {
	GeneratedAt        string     `json:"generated_at"`
	RegistryGeneration int64      `json:"registry_generation"`
	Host               HostFacts  `json:"host"`
	Tenants            []FleetRow `json:"tenants"` // display_order, then the rest by name
}

// HostFacts are the host-level facts the dashboard band shows
// (fleet_response.json#/$defs/Host).
type HostFacts struct {
	DiskFreeBytes int64    `json:"disk_free_bytes"` // free bytes on rag_root's filesystem
	Linger        bool     `json:"linger"`          // /var/lib/systemd/linger/<ctl user>
	CtlUnitActive bool     `json:"ctl_unit_active"` // ragstack-ctl.service active
	VMMaxMapCount int      `json:"vm_max_map_count"`
	SudoersGroup  []string `json:"sudoers_group"` // members of seed-admins-svcbvbrc
}

// FleetPorts are the three ports a fleet row shows.
type FleetPorts struct {
	API        int `json:"api"`
	QdrantHTTP int `json:"qdrant_http"`
	ESHTTP     int `json:"es_http"`
}

// Health is one row's four probes.
type Health struct {
	API    HealthState `json:"api"`
	Qdrant HealthState `json:"qdrant"`
	ES     HealthState `json:"es"`
	Deep   HealthState `json:"deep"`
}

// RowUnits is one row's five unit states.
type RowUnits struct {
	Target UnitState `json:"target"`
	API    UnitState `json:"api"`
	UI     UnitState `json:"ui"`
	Qdrant UnitState `json:"qdrant"`
	ES     UnitState `json:"es"`
}

// LastBackup is the summary of a row's last backup (null when there is none).
type LastBackup struct {
	At       string `json:"at"`
	Fenced   bool   `json:"fenced"`
	Verified bool   `json:"verified"`
}

// FleetRow is one tenant's dashboard row (fleet_response.json#/$defs/FleetRow).
// Every member is summary-safe: no path, no fingerprint, no key.
type FleetRow struct {
	Name         string      `json:"name"`
	ManifestName string      `json:"manifest_name"`
	State        State       `json:"state"`
	Owner        Owner       `json:"owner"`
	Supervisor   Supervisor  `json:"supervisor"`
	StoresMode   StoresMode  `json:"stores_mode"`
	CodeTag      string      `json:"code_tag"`
	DriftCount   int         `json:"drift_count"`
	Ports        FleetPorts  `json:"ports"`
	Health       Health      `json:"health"`
	Units        RowUnits    `json:"units"`
	DiskBytes    int64       `json:"disk_bytes"`
	LastBackup   *LastBackup `json:"last_backup"`
}

// Listening reports which of a tenant's allocated ports have a listener.
type Listening struct {
	API        bool `json:"api"`
	QdrantHTTP bool `json:"qdrant_http"`
	ESHTTP     bool `json:"es_http"`
	UI         bool `json:"ui"`
}

// LiveStatus is what /proc says right now
// (tenant_response.json#/$defs/LiveStatus).
type LiveStatus struct {
	ObservedAt     string     `json:"observed_at"`
	APIPid         NullInt    `json:"api_pid"`
	APIPidOwner    NullString `json:"api_pid_owner"`
	Listening      Listening  `json:"listening"`
	RestartPending bool       `json:"restart_pending"`
	RunningJobs    []string   `json:"running_jobs"` // ULIDs; always [] in PR-A
}

// UnitTarget is the tenant's systemd target, or null when it has none.
type UnitTarget struct {
	Name        string    `json:"name"` // ragstack-<name>.target
	ActiveState UnitState `json:"active_state"`
	Enabled     bool      `json:"enabled"`
}

// Service is one supervised process. Name is the unit name, or
// "manual:<kind>" for a hand-started one.
type Service struct {
	Kind        string     `json:"kind"` // api|ui|qdrant|es
	Name        string     `json:"name"`
	ActiveState UnitState  `json:"active_state"`
	SubState    NullString `json:"sub_state"`
	MainPID     NullInt    `json:"main_pid"`
	Since       NullString `json:"since"`
}

// TenantUnits is the units member of a tenant response.
type TenantUnits struct {
	Supervisor Supervisor  `json:"supervisor"`
	Target     *UnitTarget `json:"target"`
	Services   []Service   `json:"services"`
}

// TenantResponse is GET /v1/tenants/{name} (tenant_response.json). Registry
// is nil (JSON null) for a viewer — the row carries secret refs, fingerprints
// and the rollback descriptor, which are the operator's business.
type TenantResponse struct {
	Summary  FleetRow         `json:"summary"`
	Registry *registry.Tenant `json:"registry"`
	Status   LiveStatus       `json:"status"`
	Units    TenantUnits      `json:"units"`
	Drift    []registry.Drift `json:"drift"` // computed NOW, not what adopt recorded
}

// TenantsResponse is GET /v1/tenants (tenants_response.json).
type TenantsResponse struct {
	GeneratedAt        string           `json:"generated_at"`
	RegistryGeneration int64            `json:"registry_generation"`
	Tenants            []TenantResponse `json:"tenants"`
}

// EnvKey is one configuration key as the env API shows it
// (env_response.json#/$defs/EnvKey). ValueRedacted is verbatim for a public
// key and the literal "<redacted>" for every other class.
type EnvKey struct {
	Key           string     `json:"key"`
	ValueRedacted string     `json:"value_redacted"`
	Source        Source     `json:"source"`
	Class         Class      `json:"class"`
	Drift         NullString `json:"drift"` // an EnvDrift, or null when all agree
}

// EnvRedacted is the placeholder env_response.json specifies for a
// non-public value. Lower case and distinct from settings.Redacted on
// purpose: this is the HTTP surface's constant, not the file redactor's.
const EnvRedacted = "<redacted>"

// EnvResponse is GET /v1/tenants/{name}/env (env_response.json).
type EnvResponse struct {
	Tenant    string   `json:"tenant"`
	EnvLayout string   `json:"env_layout"` // legacy|managed
	Keys      []EnvKey `json:"keys"`
}

// LogsResponse is GET /v1/tenants/{name}/logs (logs_response.json). The
// on-disk path is deliberately absent and Redacted is a constant true.
type LogsResponse struct {
	Tenant    string   `json:"tenant"`
	File      LogFile  `json:"file"`
	Lines     []string `json:"lines"`
	Requested int      `json:"requested"`
	Returned  int      `json:"returned"`
	Truncated bool     `json:"truncated"`
	Redacted  bool     `json:"redacted"` // const true
}

// MaxLogLines is the contract's cap on `lines` (logs_response.json).
const MaxLogLines = 5000

// Scope is what one doctor run covered.
type Scope struct {
	Tenant NullString `json:"tenant"` // null for a fleet-wide run
	Op     NullString `json:"op"`     // null when no op preconditions were applied
}

// Finding is one doctor result (doctor_response.json#/$defs/Finding).
type Finding struct {
	Level  Level      `json:"level"`
	Code   string     `json:"code"`   // stable snake_case, see doctor/codes.go
	Tenant NullString `json:"tenant"` // null for a host-level finding
	Detail string     `json:"detail"`
	Repair string     `json:"repair,omitempty"` // the op that would clear it
}

// DoctorResponse is GET /v1/doctor (doctor_response.json). Hash is what a
// Plan pins and what --force-with-doctor-diff must quote.
type DoctorResponse struct {
	Status      Status    `json:"status"`
	Hash        string    `json:"hash"` // ^sha256:[0-9a-f]{64}$
	GeneratedAt string    `json:"generated_at"`
	Scope       Scope     `json:"scope"`
	Findings    []Finding `json:"findings"`
}

// StatusFor returns the max level over findings as a status.
func StatusFor(findings []Finding) Status {
	s := StatusGreen
	for _, f := range findings {
		switch f.Level {
		case LevelError:
			return StatusRed
		case LevelWarn:
			s = StatusYellow
		}
	}
	return s
}

// --------------------------------------------------------------------------
// The remaining response bodies
//
// These used to be built inline in internal/ctl/api (anonymous maps and
// unexported structs) and in internal/ctl/version. That put them outside the
// reach of TestResponsesValidateAgainstContract, which walks the types
// DECLARED HERE — so "every response type is validated against its schema" was
// true of about half of them, and the half it missed is the half a handler
// assembles by hand. Declaring them here is what makes the claim true.
// --------------------------------------------------------------------------

// HealthResponse is GET /health (health_response.json). `status` is pinned to
// "ok" by the schema: an unhealthy daemon does not answer 200.
type HealthResponse struct {
	Status  string `json:"status"`
	Version string `json:"version"`
}

// HealthOKStatus is the only `status` health_response.json permits.
const HealthOKStatus = "ok"

// VersionResponse is GET /v1/version (version_response.json). It mirrors
// version.BuildInfo, which the version package keeps so that
// `ragstack-ctl version` can report a schema version without importing the
// registry; the handler normalises the two "unknown" cases into it.
type VersionResponse struct {
	Version       string `json:"version"`
	Commit        string `json:"commit"`
	BuiltAt       string `json:"built_at"`
	Go            string `json:"go"`
	SchemaVersion int    `json:"schema_version"`
}

// Unknown is what the contract requires where an unstamped local build has
// nothing: version_response.json pins `commit` to 40 hex or this literal, and
// `built_at` to RFC 3339 or this literal. "" is not one of the answers.
const Unknown = "unknown"

// MeResponse is GET /v1/me (me_response.json).
type MeResponse struct {
	Principal  string  `json:"principal"`
	Role       string  `json:"role"`
	AuthMethod string  `json:"auth_method"`
	ExpiresAt  *string `json:"expires_at"`
	// SudoUser is a --direct CLI fact. Over HTTP it is always null: a
	// caller-supplied value would be an unauthenticated identity claim.
	SudoUser *string `json:"sudo_user"`
}

// SessionResponse is POST /v1/session (session_response.json). ReadsOnly is a
// constant true so a client that ignores the docs still sees it.
type SessionResponse struct {
	SessionID string `json:"session_id"`
	Principal string `json:"principal"`
	Role      string `json:"role"`
	ExpiresAt string `json:"expires_at"`
	ReadsOnly bool   `json:"reads_only"`
}

// SettingsRetention is settings_response.json#/properties/retention.
type SettingsRetention struct {
	KeepLast         map[string]int `json:"keep_last"`
	KeepPartialHours int            `json:"keep_partial_hours"`
	// AutoDelete is constant false in v1: `backup prune` is --dry-run only.
	AutoDelete bool `json:"auto_delete"`
}

// SettingsRecipients is the age backup-recipient block, READ-ONLY over HTTP.
type SettingsRecipients struct {
	File         string   `json:"file"`
	Count        int      `json:"count"`
	Fingerprints []string `json:"fingerprints"`
	ReadOnly     bool     `json:"read_only"`
}

// SettingsImage pins one store SIF (registry.json#/$defs/Image). Restated here
// rather than reused from registry so a response can be NORMALISED without
// mutating the registry row it was projected from.
type SettingsImage struct {
	SIF     string `json:"sif"`
	Version string `json:"version"`
	Digest  string `json:"digest"`
}

// SettingsImages are the two shared store images.
type SettingsImages struct {
	Qdrant        SettingsImage `json:"qdrant"`
	Elasticsearch SettingsImage `json:"elasticsearch"`
}

// SettingsCtl is the ctl block of settings_response.json.
type SettingsCtl struct {
	Port           int    `json:"port"`
	UIDist         string `json:"ui_dist"`
	GatewayEnabled bool   `json:"gateway_enabled"`
}

// SettingsResponse is GET /v1/settings (settings_response.json).
type SettingsResponse struct {
	RegistryGeneration int64              `json:"registry_generation"`
	Retention          SettingsRetention  `json:"retention"`
	Images             SettingsImages     `json:"images"`
	PythonEnvDefault   string             `json:"python_env_default"`
	Ctl                SettingsCtl        `json:"ctl"`
	Recipients         SettingsRecipients `json:"recipients"`
}

// JobsResponse is GET /v1/jobs (jobs_response.json). Jobs is typed as raw
// documents until the job engine lands in PR-C; the schema validates their
// shape either way, and an empty list is the truthful answer today.
type JobsResponse struct {
	Jobs      []json.RawMessage `json:"jobs"`
	Limit     int               `json:"limit"`
	Truncated bool              `json:"truncated"`
}

// AuditResponse is GET /v1/audit (audit_response.json). A read produces no
// audit row, so an empty log is the truth on a read-only daemon.
type AuditResponse struct {
	Rows      []json.RawMessage `json:"rows"`
	Limit     int               `json:"limit"`
	Truncated bool              `json:"truncated"`
}

// GatewayRoute is one published route (gateway_status.json#/$defs/Route).
type GatewayRoute struct {
	Name     string `json:"name"`
	API      int    `json:"api"`
	UI       *int   `json:"ui"`
	UIMode   string `json:"ui_mode"`
	Readonly bool   `json:"readonly"`
	Status   string `json:"status"`
}

// GatewayNginx is what /proc says about the nginx master. Every field is null
// until a PR reads it; null is "not looked at", never an assumed value.
type GatewayNginx struct {
	MasterPID    *int    `json:"master_pid"`
	MasterUID    *int    `json:"master_uid"`
	ConfigOK     *bool   `json:"config_ok"`
	LastReloadAt *string `json:"last_reload_at"`
}

// GatewayStatus is GET /v1/gateway (gateway_status.json).
type GatewayStatus struct {
	Generation         int            `json:"generation"`
	PublishedAt        *string        `json:"published_at"`
	PublishedBy        *string        `json:"published_by"`
	TxnState           string         `json:"txn_state"`
	PendingDiff        bool           `json:"pending_diff"`
	RegistryGeneration int64          `json:"registry_generation"`
	Nginx              GatewayNginx   `json:"nginx"`
	Routes             []GatewayRoute `json:"routes"`
	LegacyRoutes       []GatewayRoute `json:"legacy_routes"`
}

// GatewayFile is one rendered file and its diff against what is serving.
type GatewayFile struct {
	Path string `json:"path"`
	Diff string `json:"diff"`
}

// GatewayRenderResponse is POST /v1/gateway/render — a READ despite the verb.
type GatewayRenderResponse struct {
	Generation         int           `json:"generation"`
	RegistryGeneration int64         `json:"registry_generation"`
	Changed            bool          `json:"changed"`
	Files              []GatewayFile `json:"files"`
	SHA256             string        `json:"sha256"`
}
