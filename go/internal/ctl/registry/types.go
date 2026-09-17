// Package registry is the fleet's source of truth: /rag/data/tenants/
// registry.json, its lock, its generation record and the manifest.tsv
// projection that apptainer/new-tenant.sh and the ops scripts read.
//
// The types mirror contracts/ctl/schemas/registry.json exactly
// (additionalProperties:false everywhere; TestFixtureValidatesAgainstContract
// proves it). The contract makes every key required and models "unknown" as
// an explicit null, so nothing here carries `omitempty`: nullable scalars use
// NullString / NullPort (zero value ⇒ null) and nullable objects are
// pointers that marshal to null when nil.
package registry

import (
	"encoding/json"

	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/version"
)

// SchemaVersion is the registry.json schema this package reads and writes.
const SchemaVersion = version.SchemaVersion

// NullString is a string the contract declares nullable: "" marshals as
// null and null unmarshals as "". Use plain string for required strings.
type NullString string

// MarshalJSON emits null for the empty string.
func (s NullString) MarshalJSON() ([]byte, error) {
	if s == "" {
		return []byte("null"), nil
	}
	return json.Marshal(string(s))
}

// UnmarshalJSON accepts null or a string.
func (s *NullString) UnmarshalJSON(b []byte) error {
	if string(b) == "null" {
		*s = ""
		return nil
	}
	var v string
	if err := json.Unmarshal(b, &v); err != nil {
		return err
	}
	*s = NullString(v)
	return nil
}

// NullPort is a port the contract declares nullable: 0 marshals as null.
type NullPort int

// MarshalJSON emits null for zero.
func (p NullPort) MarshalJSON() ([]byte, error) {
	if p == 0 {
		return []byte("null"), nil
	}
	return json.Marshal(int(p))
}

// UnmarshalJSON accepts null or an integer.
func (p *NullPort) UnmarshalJSON(b []byte) error {
	if string(b) == "null" {
		*p = 0
		return nil
	}
	var v int
	if err := json.Unmarshal(b, &v); err != nil {
		return err
	}
	*p = NullPort(v)
	return nil
}

// Fleet is the whole registry file.
type Fleet struct {
	SchemaVersion int                  `json:"schema_version"`
	Generation    int64                `json:"generation"`
	UpdatedAt     string               `json:"updated_at"`
	UpdatedBy     string               `json:"updated_by"`
	RagRoot       string               `json:"rag_root"`
	PortBase      int                  `json:"port_base"`
	PortStride    int                  `json:"port_stride"`
	DisplayOrder  []string             `json:"display_order"`
	LegacyRoutes  []LegacyRoute        `json:"legacy_routes"`
	Ctl           Ctl                  `json:"ctl"`
	Images        Images               `json:"images"`
	Artifacts     map[string]*Artifact `json:"artifacts"`
	Tenants       map[string]*Tenant   `json:"tenants"`
	Tombstones    []Tombstone          `json:"tombstones"`
}

// LegacyRoute is a gateway row that is not a registry tenant (the
// hand-started lucid :8010 / asm :8000 pair). Emitted before tenants; the
// gateway renders 127.0.0.1:<port> for API and UI.
type LegacyRoute struct {
	Name     string `json:"name"`
	API      int    `json:"api"`
	UI       int    `json:"ui"`
	Readonly bool   `json:"readonly"` // $tenant_readonly "ro"
	Status   string `json:"status"`   // active|retired
	Note     string `json:"note"`
}

// Ctl is the control plane's own placement.
type Ctl struct {
	Port           int    `json:"port"`
	UIDist         string `json:"ui_dist"`
	GatewayEnabled bool   `json:"gateway_enabled"`
}

// Image pins a SIF.
type Image struct {
	SIF     string `json:"sif"`
	Version string `json:"version"`
	Digest  string `json:"digest"`
}

// Images are the two shared store images every tenant runs from.
type Images struct {
	Qdrant        Image `json:"qdrant"`
	Elasticsearch Image `json:"elasticsearch"`
}

// Artifact is a prepared release: worktree at a reviewed SHA + built UI.
type Artifact struct {
	SHA              string `json:"sha"`
	Tag              string `json:"tag"`
	Worktree         string `json:"worktree"`
	UIDist           string `json:"ui_dist"`
	PythonEnv        string `json:"python_env"`
	PreparedAt       string `json:"prepared_at"`
	PreparedBy       string `json:"prepared_by"`
	SchemaCompatible bool   `json:"schema_compatible"`
}

// Tombstone is a decommissioned tenant's allocation. Permanent: Allocate
// never reuses an index that appears here.
type Tombstone struct {
	ManifestName     string `json:"manifest_name"`
	Index            int    `json:"index"`
	Base             int    `json:"base"`
	DecommissionedAt string `json:"decommissioned_at"`
}

// Tenant is one registry entry. Name is the registry key, gateway segment,
// worktree and pidfile/log suffix; ManifestName is the manifest row, the
// data-dir basename and the apptainer instance suffix.
type Tenant struct {
	Name         string     `json:"name"`
	ManifestName string     `json:"manifest_name"`
	DataDir      string     `json:"data_dir"`
	Worktree     string     `json:"worktree"`
	ArtifactID   NullString `json:"artifact_id"`
	Code         Code       `json:"code"`
	PythonEnv    string     `json:"python_env"`
	Ports        Ports      `json:"ports"`
	API          API        `json:"api"`
	Stores       Stores     `json:"stores"`
	UI           UI         `json:"ui"`

	Supervisor  string `json:"supervisor"`   // systemd|manual|instance
	Owner       string `json:"owner"`        // svcbvbrc|wilke
	State       string `json:"state"`        // provisioned|active|stopped|migrating|handover|quarantined|decommissioned
	DesiredBoot string `json:"desired_boot"` // enabled|disabled
	EnvLayout   string `json:"env_layout"`   // legacy|managed

	Settings          map[string]string `json:"settings"`    // public keys only
	SecretRefs        []SecretRef       `json:"secret_refs"` // never values
	EnvFileSHA256     string            `json:"env_file_sha256"`
	SecretsFileSHA256 NullString        `json:"secrets_file_sha256"`
	Identity          Identity          `json:"identity"`
	Keys              []Key             `json:"keys"`
	ServiceAccounts   []ServiceAccount  `json:"service_accounts"`
	ExternalRefs      []ExternalRef     `json:"external_refs"`
	UnmanagedFiles    []string          `json:"unmanaged_files"`
	Drift             []Drift           `json:"drift"`
	RestartPending    bool              `json:"restart_pending"`

	ReleaseGeneration  *ReleaseGeneration  `json:"release_generation"`  // null until a release is pinned
	RollbackDescriptor *RollbackDescriptor `json:"rollback_descriptor"` // null until handover captures it
	// Handover is the in-flight two-account handover, ABSENT at every other
	// moment — the second `omitempty` in this file, and it is load-bearing.
	//
	// registry.Load decodes with DisallowUnknownFields, so a registry carrying
	// `"handover": null` is a registry the DEPLOYED binary cannot read at all:
	// every tenant of the fleet becomes unreadable to the daemon that is
	// running, which is a fleet-wide outage bought for a field that says
	// nothing. Omitted, a row written by this binary still loads under the
	// previous one, and absent means exactly null (the contract leaves it out
	// of `required` for the same reason `last_backup.scope` is out).
	Handover   *Handover           `json:"handover,omitempty"`
	LastOps    map[string]OpRecord `json:"last_ops"`
	LastBackup *BackupRecord       `json:"last_backup"` // null until the first backup
	AdoptedAt  NullString          `json:"adopted_at"`
}

// Code pins the running checkout.
type Code struct {
	Tag                string     `json:"tag"`
	SHA                NullString `json:"sha"` // 40-hex or null
	PreviousArtifactID NullString `json:"previous_artifact_id"`
}

// Ports is the tenant's port block (same shape as paths.Ports).
type Ports = paths.Ports

// API is how the tenant API process is launched and found.
type API struct {
	Bind    string `json:"bind"` // 127.0.0.1 for managed tenants; 0.0.0.0 for adopted ones
	PidFile string `json:"pidfile"`
	Log     string `json:"log"`
}

// Ownership of a store relative to this tenant.
const (
	OwnershipExclusive = "exclusive"
	OwnershipShared    = "shared"
	OwnershipUnknown   = "unknown"
	OwnershipExternal  = "external"
)

// Capabilities an operator has confirmed for a store. All false until
// process identity, backing path and exclusive ownership are confirmed.
type Capabilities struct {
	Stop     bool `json:"stop"`
	Purge    bool `json:"purge"`
	Restore  bool `json:"restore"`
	Snapshot bool `json:"snapshot"`
}

// Qdrant store facts.
type Qdrant struct {
	Ownership    string            `json:"ownership"`
	Capabilities Capabilities      `json:"capabilities"`
	URL          string            `json:"url"` // http://127.0.0.1|localhost:<port>
	Instance     NullString        `json:"instance"`
	SIF          NullString        `json:"sif"`
	ExtraEnv     map[string]string `json:"extra_env"`
}

// Elasticsearch store facts.
type Elasticsearch struct {
	Ownership     string            `json:"ownership"`
	Capabilities  Capabilities      `json:"capabilities"`
	URL           string            `json:"url"`
	Instance      NullString        `json:"instance"`
	SIF           NullString        `json:"sif"`
	Heap          NullString        `json:"heap"`           // live (ES_JAVA_OPTS)
	ProvisionHeap NullString        `json:"provision_heap"` // provision.env TENANT_ES_HEAP
	PathRepo      NullString        `json:"path_repo"`      // in-container snapshot repo
	ExtraEnv      map[string]string `json:"extra_env"`
}

// Neo4j is always external to the ctl in v1.
type Neo4j struct {
	Ownership string     `json:"ownership"` // "external"
	URL       NullString `json:"url"`
}

// Postgres store kinds — new-tenant.sh's TENANT_STORE_KIND, normalised.
const (
	// PostgresKindSQLite is the default: files under <data_dir>/state/, no
	// server, every other field null.
	PostgresKindSQLite = "sqlite"
	// PostgresKindLocal is `--postgres local`: a dedicated apptainer
	// instance postgres-<name> on the block's +5 port (ports.pg).
	PostgresKindLocal = "local"
	// PostgresKindExternal is `--postgres <admin-dsn>`: a database inside a
	// server somebody else runs.
	PostgresKindExternal = "external"
)

// Postgres is the tenant's RELATIONAL store (ACL / job / collection store).
// Every tenant has one of the three kinds above, so the row is always
// present — a `sqlite` row is the honest "no server" statement, not an
// absence.
//
// URL is host+port ONLY (postgresql://<host>:<port>). The DSNs the tenant
// connects with carry a password and stay in SecretRefs; nothing in this
// struct may ever hold one.
type Postgres struct {
	Kind         string       `json:"kind"`      // sqlite|local|external
	Ownership    string       `json:"ownership"` // sqlite,local ⇒ exclusive; external ⇒ external
	Capabilities Capabilities `json:"capabilities"`
	URL          NullString   `json:"url"`
	Port         NullPort     `json:"port"`
	Instance     NullString   `json:"instance"` // postgres-<name>, local only
	SIF          NullString   `json:"sif"`
	DataDir      NullString   `json:"data_dir"` // <data_dir>/postgres, local only
}

// SQLiteStore is the relational-store row of a tenant that keeps its ACL,
// job and collection state in SQLite files under <data_dir>/state/.
// Exclusive because those files are this tenant's alone.
func SQLiteStore() Postgres {
	return Postgres{Kind: PostgresKindSQLite, Ownership: OwnershipExclusive}
}

// Stores groups the four store legs. All four are always present; a
// tenant without a graph leg has Neo4j{Ownership: external, URL: null}, and
// one without a Postgres server has Postgres{Kind: sqlite}.
type Stores struct {
	Qdrant                 Qdrant        `json:"qdrant"`
	Elasticsearch          Elasticsearch `json:"elasticsearch"`
	Neo4j                  Neo4j         `json:"neo4j"`
	Postgres               Postgres      `json:"postgres"`
	DormantProvisionedDirs bool          `json:"dormant_provisioned_dirs"`
}

// UI modes.
const (
	UIModeStatic   = "static"   // vite build served by nginx from <data_dir>/ui/dist
	UIModeDev      = "dev"      // vite dev server unit managed by the ctl
	UIModeExternal = "external" // a hand-run dev server the gateway proxies to
)

// UI describes how the tenant UI is served.
type UI struct {
	Mode string   `json:"mode"`
	Port NullPort `json:"port"` // null for static
	Base string   `json:"base"` // /ragstack/<name>/ui/
}

// SecretRef names a secret without its value.
type SecretRef struct {
	Key  string `json:"key"`
	File string `json:"file"`
}

// Identity summarises the bearer-identity configuration.
type Identity struct {
	Provider           string `json:"provider"` // bvbrc|oidc|none
	AdminSubjectsCount int    `json:"admin_subjects_count"`
}

// Key is an API key's ledger row: fingerprint only.
type Key struct {
	ID           string     `json:"id"`
	Label        string     `json:"label"`
	Role         string     `json:"role"`
	TenantString string     `json:"tenant_string"`
	Fingerprint  string     `json:"fingerprint"` // "sha256:" + hex[:16]
	CreatedAt    string     `json:"created_at"`
	CreatedBy    string     `json:"created_by"`
	RevokedAt    NullString `json:"revoked_at"`
	Effective    bool       `json:"effective"`
}

// ServiceAccount is a tenant-API service account the ctl created.
type ServiceAccount struct {
	Subject   string `json:"subject"`
	Role      string `json:"role"`
	Purpose   string `json:"purpose"`
	Status    string `json:"status"` // active|disabled
	CreatedAt string `json:"created_at"`
}

// ExternalRef is a tenant.env key that points outside the tenant tree
// (e.g. COLLECTIONS_FILE=/rag/config/demo.collections.json).
type ExternalRef struct {
	Key  string `json:"key"`
	Path string `json:"path"`
}

// Drift is one registry-vs-reality finding recorded at adopt/doctor time.
type Drift struct {
	Code       string `json:"code"`  // ^[a-z][a-z0-9_]*$
	Level      string `json:"level"` // info|warn|error
	Field      string `json:"field,omitempty"`
	Expected   string `json:"expected"`
	Actual     string `json:"actual"`
	ObservedAt string `json:"observed_at"`
	Note       string `json:"note,omitempty"`
}

// ReleaseGeneration ties an artifact to a config generation.
type ReleaseGeneration struct {
	ArtifactID       string `json:"artifact_id"`
	ConfigGeneration int64  `json:"config_generation"`
	SchemaCompatible bool   `json:"schema_compatible"`
}

// RollbackPaths are the pre-handover locations.
type RollbackPaths struct {
	DataDir   string     `json:"data_dir"`
	Worktree  string     `json:"worktree"`
	PythonEnv string     `json:"python_env"`
	UpSh      NullString `json:"up_sh"`
}

// RollbackImages are the SIFs in use before handover (null when shared).
type RollbackImages struct {
	Qdrant        *Image `json:"qdrant"`
	Elasticsearch *Image `json:"elasticsearch"`
}

// LaunchArg is one observed process launch (secret-bearing argv redacted).
type LaunchArg struct {
	Kind string   `json:"kind"` // api|ui|qdrant|es
	Argv []string `json:"argv"`
	Cwd  string   `json:"cwd"`
}

// RollbackDescriptor is the immutable pre-handover record.
type RollbackDescriptor struct {
	CapturedAt        string         `json:"captured_at"`
	Owner             string         `json:"owner"` // wilke|svcbvbrc
	Paths             RollbackPaths  `json:"paths"`
	Ports             Ports          `json:"ports"`
	Code              Code           `json:"code"`
	EnvFileSHA256     string         `json:"env_file_sha256"`
	Images            RollbackImages `json:"images"`
	LaunchArgs        []LaunchArg    `json:"launch_args"`
	GatewayGeneration int64          `json:"gateway_generation"`
}

// Handover phases (Handover.Phase).
const (
	// HandoverReleased: the owner has stopped the tenant and nothing of it is
	// running. The token in the row is what the take must quote.
	HandoverReleased = "released"
	// HandoverTaken: the service account has started it again under
	// `supervisor: instance` and the operator is soaking before the commit.
	HandoverTaken = "taken"
)

// StateHandover is the transitional `state` of a tenant between the release
// and the take. Probes expect a 502 for it, so a gateway generation published
// during the window stays honest.
const StateHandover = "handover"

// Handover is the in-flight two-account handover.
//
// It exists because handover CANNOT be one job on this host: the daemon
// (svcbvbrc) can neither signal the hand-started API — `/proc/<pid>/cwd` of
// another account's process is unreadable — nor see the owner's apptainer
// instances, which live in that account's own instance registry. So the row
// carries the state between two jobs run by two accounts, and the token is
// what ties the second to the first.
type Handover struct {
	Phase      string     `json:"phase"` // released|taken
	Token      string     `json:"token"` // 32 hex characters; a nonce, not a credential
	StartedAt  string     `json:"started_at"`
	ReleasedBy string     `json:"released_by"`
	ReleasedAt NullString `json:"released_at"`
	TakenAt    NullString `json:"taken_at"`
	TakenBy    NullString `json:"taken_by"`
	// DescriptorRef is the rollback descriptor's captured_at, so a descriptor
	// rewritten under a parked handover shows up as a disagreement.
	DescriptorRef NullString `json:"descriptor_ref"`
	// Census is what the tenant held when it was released: the take compares
	// its own reading against it. A count of -1 is "this store could not be
	// counted", which the take reports and does not treat as agreement.
	Census []CensusEntry `json:"census"`
	// PostgresData records the ONE thing a handover moves, and it is null for
	// every tenant that does not run a postgres of its own.
	//
	// A handover moves no data — except here. postgres compares its data
	// directory's st_uid against its own geteuid() and refuses to start when
	// they differ, which no mode and no ACL entry changes, and nobody on this
	// host can chown to another account. So the take COPIES the directory as
	// itself and renames the original aside; this block says where the
	// original went. It is what an abandon swaps back and what the operator
	// eventually deletes.
	PostgresData *PostgresDataMigration `json:"postgres_data"`
}

// PostgresDataMigration is how a handover moves the one thing it cannot leave
// where it is: the tenant's postgres.
//
// It cannot be left because postgres refuses a data directory it does not own
// (st_uid vs geteuid), and it cannot be copied because the taking account
// cannot READ one either — a POSIX ACL's named-user entry is filtered by the
// mask, the mask is the group mode bits, and a PGDATA postgres accepts has
// none. So the release DUMPS and the take restores into a cluster it
// initialises itself, and this block carries the handover across the two jobs.
//
// The RELEASE fills the first four fields; the TAKE fills the last three, and
// they are empty until it does. That split is why the paths are plain strings
// rather than a nested object per phase: an operator reading the row mid-
// handover should see one block that fills in, not two that have to be
// correlated.
type PostgresDataMigration struct {
	// Dump is the release's `pg_dump -Fc` archive:
	// `<data_dir>/postgres/handover-<ts>.dump`, mode 0640 so the group both
	// accounts are in can read it.
	Dump string `json:"dump"`
	// DumpSHA256 is that file's digest, checked by the take before it moves
	// anything: the archive crosses a job boundary, an account boundary and an
	// unbounded amount of wall-clock time.
	DumpSHA256 string `json:"dump_sha256"`
	// DumpedAt is when the release took it.
	DumpedAt string `json:"dumped_at"`
	// Tables is the exact row count of every table at that moment. A dump and
	// a restore cannot be compared byte for byte; these counts are what the
	// take proves the migration against.
	Tables []PostgresTableCount `json:"tables"`
	// Encoding, Collate and Ctype are the SOURCE database's character encoding
	// and its two locales, and they are here because a dump and a restore do
	// not carry them.
	//
	// The take's cluster is initialised by the image's entrypoint, and initdb
	// takes its encoding from its environment unless it is told otherwise — so
	// without these, the encoding of a tenant's database after a handover is
	// decided by whatever shell, cron job or unit happened to run the take. A
	// UTF8 dump restored into an SQL_ASCII/C cluster exits 0 and keeps every
	// row, and the row counts cannot see the difference; `length()`, `upper()`,
	// `LIKE` and every index's sort order can. The take pins them
	// (POSTGRES_INITDB_ARGS) and checks them back.
	Encoding string `json:"encoding"`
	Collate  string `json:"collate"`
	Ctype    string `json:"ctype"`
	// ExtraDatabases and ExtraRoles are what the release found in the cluster
	// BESIDE the tenant's own database and role — and, being non-empty, what
	// an operator explicitly accepted with `--accept-extra-databases`.
	//
	// A single-database dump does not carry them. They stay in the
	// pre-handover cluster, which is the copy the commit invites the operator
	// to delete, so a handover that moved a tenant and silently left a second
	// database behind is one nobody would notice until it was gone.
	ExtraDatabases []string `json:"extra_databases"`
	ExtraRoles     []string `json:"extra_roles"`
	// PreHandover is where the take renamed the ORIGINAL cluster:
	// `<data_dir>/postgres/data.pre-handover-<ts>`. Nothing opens it again; an
	// abandon renames it back, and a commit leaves it for the operator.
	PreHandover string `json:"pre_handover"`
	// Copy is the name the take's own cluster directory was created under,
	// `<data_dir>/postgres/data.<account>-<ts>`, before it was renamed into
	// place. An abandon renames the live directory back to it.
	Copy string `json:"copy"`
	// MigratedAt is when the two renames happened.
	MigratedAt string `json:"migrated_at"`
}

// PostgresTableCount is one table and how many rows it held when the release
// dumped it.
type PostgresTableCount struct {
	// Name is "<schema>.<table>".
	Name string `json:"name"`
	Rows int64  `json:"rows"`
}

// CensusEntry is one collection or index and how many rows it held at the
// release.
type CensusEntry struct {
	Store string `json:"store"` // qdrant|elasticsearch|api
	Name  string `json:"name"`
	Count int64  `json:"count"`
}

// OpRecord is the last outcome of one op (keyed by OpVerb in Tenant.LastOps).
type OpRecord struct {
	JobID   string `json:"job_id"`
	At      string `json:"at"`
	Outcome string `json:"outcome"`
}

// BackupRecord summarises the last backup.
//
// Scope says WHAT the bundle holds: the full bundle's ["config","state",
// "stores"], or the ["config","state"] of a light one (`tenant backup --scope
// config,state`). It is not redundant with Fenced — a light bundle is
// unfenced by construction, but so is a full best-effort one, and only Scope
// tells them apart. Nothing reads it as a prerequisite (Fenced+Verified do
// that); it exists so that `last_backup` cannot be read as a claim the bundle
// does not make.
type BackupRecord struct {
	Bundle   string `json:"bundle"`
	At       string `json:"at"`
	Kind     string `json:"kind"` // backup|pre-update|recovery
	Fenced   bool   `json:"fenced"`
	Verified bool   `json:"verified"`
	// Scope is the ONE omitempty in this file, and it is deliberate. The field
	// arrived after the deployed binary had already written records without it,
	// so it is optional in the contract and ABSENT means the full bundle —
	// the only kind that existed before. The loader backfills it
	// (backfillBackupScope), so nothing downstream has to know that.
	//
	// omitempty rather than a nullable wrapper because the contract types it as
	// an array: a nil slice marshals as `null`, and `"scope": null` against
	// `type: array` is a document the schema refuses. Absent is the shape the
	// contract allows.
	Scope []string `json:"scope,omitempty"`
}

// UnpinnedVersion and UnpinnedDigest are the contract-shaped markers for a
// SIF nobody has pinned yet (`fleet image list` records the real ones at
// PR-D). The contract types image version and digest as REQUIRED strings, so a
// fresh fleet carries these rather than empty values that ValidateContract
// refuses; an all-zero sha256 can never be a real digest, so the marker cannot
// be mistaken for a pin.
const (
	UnpinnedVersion = "unpinned"
	UnpinnedDigest  = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
)

// NewFleet returns an empty registry for ragRoot with the default port layout
// and the shared images under the standard layout.
//
// Every field the contract requires is filled in HERE, so the invariant is "a
// fleet is born valid" rather than "whoever creates one remembers to patch it
// afterwards". The image version/digest markers used to be applied by adopt's
// loadOrCreate; any other creator therefore produced a registry that
// LoadNoRepair (which now checks the contract) would refuse. adopt's patch is
// idempotent and left in place.
func NewFleet(ragRoot string) *Fleet {
	r := paths.NewRoots(ragRoot, paths.Overrides{})
	return &Fleet{
		SchemaVersion: SchemaVersion,
		RagRoot:       r.RagRoot,
		PortBase:      paths.PortBase,
		PortStride:    paths.PortStride,
		DisplayOrder:  []string{},
		LegacyRoutes:  []LegacyRoute{},
		// UIDist derives from rag_root (/rag/data/ctl/ui/dist under the
		// standard layout), so a fleet created for a test root does not point
		// at the deployment host's admin UI.
		Ctl: Ctl{Port: paths.CtlPort, UIDist: r.CtlUIDist()},
		Images: Images{
			Qdrant:        Image{SIF: r.ImagesDir + "/qdrant.sif", Version: UnpinnedVersion, Digest: UnpinnedDigest},
			Elasticsearch: Image{SIF: r.ImagesDir + "/elasticsearch.sif", Version: UnpinnedVersion, Digest: UnpinnedDigest},
		},
		Artifacts:  map[string]*Artifact{},
		Tenants:    map[string]*Tenant{},
		Tombstones: []Tombstone{},
	}
}

// NewTenant returns a Tenant with every collection initialised (so the JSON
// carries [] / {} rather than null where the contract wants arrays/objects)
// and the external-neo4j default.
func NewTenant(name, manifestName string) *Tenant {
	return &Tenant{
		Name: name, ManifestName: manifestName,
		Settings: map[string]string{}, SecretRefs: []SecretRef{}, Keys: []Key{},
		ServiceAccounts: []ServiceAccount{}, ExternalRefs: []ExternalRef{}, UnmanagedFiles: []string{},
		Drift: []Drift{}, LastOps: map[string]OpRecord{},
		Stores: Stores{
			Qdrant:        Qdrant{Ownership: OwnershipUnknown, ExtraEnv: map[string]string{}},
			Elasticsearch: Elasticsearch{Ownership: OwnershipUnknown, ExtraEnv: map[string]string{}},
			Neo4j:         Neo4j{Ownership: OwnershipExternal},
			Postgres:      SQLiteStore(),
		},
		Identity: Identity{Provider: "none"},
	}
}
