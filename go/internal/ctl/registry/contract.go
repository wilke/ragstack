package registry

// Structural validation of a registry against contracts/ctl/schemas/registry.json,
// expressed in Go.
//
// Why a hand-written mirror rather than a JSON-schema evaluation: the daemon
// validates on every Load, including the load that answers `GET /v1/fleet`, and
// it must not need a Python interpreter (or a schema file on disk) at runtime —
// the binary is static and the registry is read on hosts where contracts/ is not
// installed. The contract stays authoritative: TestValidateContractAgreesWithTheSchema
// and TestFixtureValidatesAgainstContract replay the same documents through the
// real JSON schema, so a rule that drifts from the schema is a test failure, not
// a silent divergence.
//
// Every message names the JSON pointer of the offending value and both sides of
// the comparison — a registry that fails to load is an operator's whole morning,
// and "invalid registry" is not a usable diagnostic.

import (
	"fmt"
	"regexp"
	"sort"
	"strings"
)

// Patterns, byte-identical to the schema's (RE2-compatible: the contract keeps
// them lookahead-free precisely so this mirror can exist).
var (
	reTenantName    = regexp.MustCompile(`^[a-z][a-z0-9-]{0,31}$`)
	reArtifactID    = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$`)
	reAbsPath       = regexp.MustCompile(`^/[A-Za-z0-9._/-]*$`)
	reSHA256Hex     = regexp.MustCompile(`^[0-9a-f]{64}$`)
	reGitSHA        = regexp.MustCompile(`^[0-9a-f]{40}$`)
	reFingerprint   = regexp.MustCompile(`^sha256:[0-9a-f]{16}$`)
	reDigest        = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)
	reLoopbackURL   = regexp.MustCompile(`^http://(127\.0\.0\.1|localhost):[0-9]{4,5}$`)
	reEnvKey        = regexp.MustCompile(`^[A-Z][A-Z0-9_]{0,127}$`)
	reSecretEnvKey  = regexp.MustCompile(`^(API_KEYS|API_KEY_TENANTS|API_KEY_ROLES|NEO4J_AUTH)$|(_DSN|PASSWORD|TOKEN|_API_KEY)$`)
	reHeap          = regexp.MustCompile(`^[1-9][0-9]*[mg]$`)
	reUIBase        = regexp.MustCompile(`^/[A-Za-z0-9._/-]*/$`)
	reKeyID         = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,63}$`)
	reSASubject     = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`)
	reDriftCode     = regexp.MustCompile(`^[a-z][a-z0-9_]*$`)
	reJobID         = regexp.MustCompile(`^[0-7][0-9A-HJKMNP-TV-Z]{25}$`)
	reAPIBind       = regexp.MustCompile(`^(127\.0\.0\.1|0\.0\.0\.0|localhost)$`)
	reUnmanagedFile = regexp.MustCompile(`^[A-Za-z0-9._][A-Za-z0-9._/-]*$`)
)

// Enums, byte-identical to the schema's.
var (
	enumOwnership   = []string{"exclusive", "shared", "unknown"}
	enumUIMode      = []string{UIModeStatic, UIModeDev, UIModeExternal}
	enumSupervisor  = []string{"systemd", "manual"}
	enumOwner       = []string{"svcbvbrc", "wilke"}
	enumState       = []string{"provisioned", "active", "stopped", "migrating", "quarantined", "decommissioned"}
	enumDesiredBoot = []string{"enabled", "disabled"}
	enumEnvLayout   = []string{"legacy", "managed"}
	enumRole        = []string{"admin", "user"}
	enumSecretFile  = []string{"tenant.env", "secrets.env", "provision.env"}
	enumDriftLevel  = []string{"info", "warn", "error"}
	enumOutcome     = []string{"succeeded", "failed", "rolled_back", "interrupted", "cancelled"}
	enumBackupKind  = []string{"backup", "pre-update", "recovery"}
	enumSAStatus    = []string{"active", "disabled"}
	enumIdentity    = []string{"bvbrc", "oidc", "none"}
	enumRouteStatus = []string{"active", "retired"}
	enumLaunchKind  = []string{"api", "ui", "qdrant", "es"}
	enumOpVerb      = []string{
		"adopt", "create", "start", "stop", "restart", "backup", "restore", "handover",
		"migrate-local", "decommission", "key-mint", "key-revoke", "admin-add", "admin-remove",
		"sa-create", "sa-disable", "sa-enable", "env-set", "env-unset", "env-normalize",
		"render-units", "update-code",
	}
)

// maxContractErrors bounds the message: a registry with 200 bad rows is one
// problem, and a page of output hides the first line.
const maxContractErrors = 20

// contractCheck accumulates findings so one load reports everything wrong with
// the file, not just the first thing.
type contractCheck struct{ errs []string }

func (c *contractCheck) failf(ptr, format string, a ...any) {
	c.errs = append(c.errs, ptr+": "+fmt.Sprintf(format, a...))
}

// pattern asserts the schema pattern for a REQUIRED string.
func (c *contractCheck) pattern(ptr, v string, re *regexp.Regexp) {
	if !re.MatchString(v) {
		c.failf(ptr, "%q does not match %s", v, re)
	}
}

// nullable asserts the pattern only when the value is present ("" is the
// contract's null for NullString).
func (c *contractCheck) nullable(ptr string, v NullString, re *regexp.Regexp) {
	if v != "" {
		c.pattern(ptr, string(v), re)
	}
}

func (c *contractCheck) enum(ptr, v string, allowed []string) {
	for _, a := range allowed {
		if v == a {
			return
		}
	}
	c.failf(ptr, "%q is not one of [%s]", v, strings.Join(allowed, " "))
}

// minLen1 is the schema's `minLength: 1` on a required string.
func (c *contractCheck) minLen1(ptr, v string) {
	if v == "" {
		c.failf(ptr, "is required and must not be empty")
	}
}

func (c *contractCheck) port(ptr string, v int) {
	if v < 1024 || v > 65535 {
		c.failf(ptr, "%d is outside [1024, 65535]", v)
	}
}

func (c *contractCheck) nullPort(ptr string, v NullPort) {
	if v != 0 {
		c.port(ptr, int(v))
	}
}

func (c *contractCheck) nonNegative(ptr string, v int64) {
	if v < 0 {
		c.failf(ptr, "%d is negative", v)
	}
}

// publicSettings applies the PublicSettingKey guard: shell-identifier shaped
// AND not of a secret class. One forbidden name fails the whole registry —
// that is the point (a bug that wrote a secret cannot be re-read into a
// dashboard).
func (c *contractCheck) publicSettings(ptr string, m map[string]string) {
	for _, k := range sortedKeys(m) {
		if !reEnvKey.MatchString(k) {
			c.failf(ptr+"/"+k, "%q is not a shell-identifier-shaped env key (%s)", k, reEnvKey)
		}
		if reSecretEnvKey.MatchString(k) {
			c.failf(ptr+"/"+k, "%q is a SECRET-class key: the registry records secrets as secret_refs[{key,file}], never their values", k)
		}
	}
}

func sortedKeys(m map[string]string) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// ValidateContract checks f against contracts/ctl/schemas/registry.json —
// every required field present and shaped, every enum a member, every name and
// fingerprint matching its pattern, and no secret-class key under `settings` or
// `extra_env`. It is the structural half of loading; Fleet.Validate holds the
// cross-field invariants (port arithmetic, unique indexes) the schema cannot
// express.
//
// It reports up to maxContractErrors findings in one error, each prefixed with
// the JSON pointer of the offending value.
func (f *Fleet) ValidateContract() error {
	c := &contractCheck{}

	if f.SchemaVersion != SchemaVersion {
		c.failf("/schema_version", "%d != %d", f.SchemaVersion, SchemaVersion)
	}
	c.nonNegative("/generation", f.Generation)
	c.minLen1("/updated_by", f.UpdatedBy)
	c.pattern("/rag_root", f.RagRoot, reAbsPath)
	c.port("/port_base", f.PortBase)
	if f.PortStride < 1 || f.PortStride > 1000 {
		c.failf("/port_stride", "%d is outside [1, 1000]", f.PortStride)
	}

	seen := map[string]bool{}
	for i, name := range f.DisplayOrder {
		ptr := fmt.Sprintf("/display_order/%d", i)
		c.pattern(ptr, name, reTenantName)
		if seen[name] {
			c.failf(ptr, "%q appears twice (display_order is uniqueItems)", name)
		}
		seen[name] = true
	}
	for i, r := range f.LegacyRoutes {
		ptr := fmt.Sprintf("/legacy_routes/%d", i)
		c.pattern(ptr+"/name", r.Name, reTenantName)
		c.port(ptr+"/api", r.API)
		c.port(ptr+"/ui", r.UI)
		c.enum(ptr+"/status", r.Status, enumRouteStatus)
	}

	c.port("/ctl/port", f.Ctl.Port)
	c.pattern("/ctl/ui_dist", f.Ctl.UIDist, reAbsPath)

	c.image("/images/qdrant", f.Images.Qdrant)
	c.image("/images/elasticsearch", f.Images.Elasticsearch)

	for _, id := range sortedArtifactKeys(f.Artifacts) {
		ptr := "/artifacts/" + id
		c.pattern(ptr, id, reArtifactID)
		a := f.Artifacts[id]
		if a == nil {
			c.failf(ptr, "is null")
			continue
		}
		c.pattern(ptr+"/sha", a.SHA, reGitSHA)
		c.minLen1(ptr+"/tag", a.Tag)
		c.pattern(ptr+"/worktree", a.Worktree, reAbsPath)
		c.pattern(ptr+"/ui_dist", a.UIDist, reAbsPath)
		c.pattern(ptr+"/python_env", a.PythonEnv, reAbsPath)
		c.minLen1(ptr+"/prepared_by", a.PreparedBy)
	}

	for _, name := range sortedTenantKeys(f.Tenants) {
		c.tenant("/tenants/"+name, name, f.Tenants[name])
	}

	for i, tb := range f.Tombstones {
		ptr := fmt.Sprintf("/tombstones/%d", i)
		c.pattern(ptr+"/manifest_name", tb.ManifestName, reTenantName)
		c.nonNegative(ptr+"/index", int64(tb.Index))
		c.port(ptr+"/base", tb.Base)
	}

	if len(c.errs) == 0 {
		return nil
	}
	shown := c.errs
	suffix := ""
	if len(shown) > maxContractErrors {
		shown, suffix = shown[:maxContractErrors], fmt.Sprintf(" (+%d more)", len(c.errs)-maxContractErrors)
	}
	return fmt.Errorf("does not match contracts/ctl/schemas/registry.json: %s%s",
		strings.Join(shown, "; "), suffix)
}

func (c *contractCheck) image(ptr string, im Image) {
	c.pattern(ptr+"/sif", im.SIF, reAbsPath)
	c.minLen1(ptr+"/version", im.Version)
	c.pattern(ptr+"/digest", im.Digest, reDigest)
}

func (c *contractCheck) tenant(ptr, key string, t *Tenant) {
	if t == nil {
		c.failf(ptr, "is null")
		return
	}
	c.pattern(ptr, key, reTenantName)
	c.pattern(ptr+"/name", t.Name, reTenantName)
	c.pattern(ptr+"/manifest_name", t.ManifestName, reTenantName)
	c.pattern(ptr+"/data_dir", t.DataDir, reAbsPath)
	c.pattern(ptr+"/worktree", t.Worktree, reAbsPath)
	c.pattern(ptr+"/python_env", t.PythonEnv, reAbsPath)
	c.nullable(ptr+"/artifact_id", t.ArtifactID, reArtifactID)
	c.code(ptr+"/code", t.Code)

	c.nonNegative(ptr+"/ports/index", int64(t.Ports.Index))
	for label, p := range map[string]int{
		"base": t.Ports.Base, "api": t.Ports.API, "qdrant_http": t.Ports.QdrantHTTP,
		"qdrant_grpc": t.Ports.QdrantGRPC, "es_http": t.Ports.ESHTTP,
		"es_transport": t.Ports.ESTransport, "pg": t.Ports.PG,
	} {
		c.port(ptr+"/ports/"+label, p)
	}

	c.pattern(ptr+"/api/bind", t.API.Bind, reAPIBind)
	c.pattern(ptr+"/api/pidfile", t.API.PidFile, reAbsPath)
	c.pattern(ptr+"/api/log", t.API.Log, reAbsPath)

	q := t.Stores.Qdrant
	c.enum(ptr+"/stores/qdrant/ownership", q.Ownership, enumOwnership)
	c.pattern(ptr+"/stores/qdrant/url", q.URL, reLoopbackURL)
	c.nullable(ptr+"/stores/qdrant/sif", q.SIF, reAbsPath)
	c.publicSettings(ptr+"/stores/qdrant/extra_env", q.ExtraEnv)

	e := t.Stores.Elasticsearch
	c.enum(ptr+"/stores/elasticsearch/ownership", e.Ownership, enumOwnership)
	c.pattern(ptr+"/stores/elasticsearch/url", e.URL, reLoopbackURL)
	c.nullable(ptr+"/stores/elasticsearch/sif", e.SIF, reAbsPath)
	c.nullable(ptr+"/stores/elasticsearch/heap", e.Heap, reHeap)
	c.nullable(ptr+"/stores/elasticsearch/provision_heap", e.ProvisionHeap, reHeap)
	c.publicSettings(ptr+"/stores/elasticsearch/extra_env", e.ExtraEnv)

	if t.Stores.Neo4j.Ownership != OwnershipExternal {
		c.failf(ptr+"/stores/neo4j/ownership", "%q != %q (neo4j is never ctl-managed in v1)",
			t.Stores.Neo4j.Ownership, OwnershipExternal)
	}

	c.enum(ptr+"/ui/mode", t.UI.Mode, enumUIMode)
	c.nullPort(ptr+"/ui/port", t.UI.Port)
	c.pattern(ptr+"/ui/base", t.UI.Base, reUIBase)

	c.enum(ptr+"/supervisor", t.Supervisor, enumSupervisor)
	c.enum(ptr+"/owner", t.Owner, enumOwner)
	c.enum(ptr+"/state", t.State, enumState)
	c.enum(ptr+"/desired_boot", t.DesiredBoot, enumDesiredBoot)
	c.enum(ptr+"/env_layout", t.EnvLayout, enumEnvLayout)

	c.publicSettings(ptr+"/settings", t.Settings)
	for i, s := range t.SecretRefs {
		sp := fmt.Sprintf("%s/secret_refs/%d", ptr, i)
		c.pattern(sp+"/key", s.Key, reEnvKey)
		c.enum(sp+"/file", s.File, enumSecretFile)
	}
	c.pattern(ptr+"/env_file_sha256", t.EnvFileSHA256, reSHA256Hex)
	c.nullable(ptr+"/secrets_file_sha256", t.SecretsFileSHA256, reSHA256Hex)

	c.enum(ptr+"/identity/provider", t.Identity.Provider, enumIdentity)
	c.nonNegative(ptr+"/identity/admin_subjects_count", int64(t.Identity.AdminSubjectsCount))

	for i, k := range t.Keys {
		kp := fmt.Sprintf("%s/keys/%d", ptr, i)
		c.pattern(kp+"/id", k.ID, reKeyID)
		c.minLen1(kp+"/label", k.Label)
		if len(k.Label) > 64 {
			c.failf(kp+"/label", "%d characters > 64", len(k.Label))
		}
		c.enum(kp+"/role", k.Role, enumRole)
		c.minLen1(kp+"/tenant_string", k.TenantString)
		c.pattern(kp+"/fingerprint", k.Fingerprint, reFingerprint)
		c.minLen1(kp+"/created_by", k.CreatedBy)
	}
	for i, sa := range t.ServiceAccounts {
		sp := fmt.Sprintf("%s/service_accounts/%d", ptr, i)
		c.pattern(sp+"/subject", sa.Subject, reSASubject)
		c.enum(sp+"/role", sa.Role, enumRole)
		c.enum(sp+"/status", sa.Status, enumSAStatus)
	}
	for i, x := range t.ExternalRefs {
		xp := fmt.Sprintf("%s/external_refs/%d", ptr, i)
		c.pattern(xp+"/key", x.Key, reEnvKey)
		c.pattern(xp+"/path", x.Path, reAbsPath)
	}
	for i, u := range t.UnmanagedFiles {
		c.pattern(fmt.Sprintf("%s/unmanaged_files/%d", ptr, i), u, reUnmanagedFile)
	}
	for i, d := range t.Drift {
		dp := fmt.Sprintf("%s/drift/%d", ptr, i)
		c.pattern(dp+"/code", d.Code, reDriftCode)
		c.enum(dp+"/level", d.Level, enumDriftLevel)
	}

	if rg := t.ReleaseGeneration; rg != nil {
		c.pattern(ptr+"/release_generation/artifact_id", rg.ArtifactID, reArtifactID)
		c.nonNegative(ptr+"/release_generation/config_generation", rg.ConfigGeneration)
	}
	if rd := t.RollbackDescriptor; rd != nil {
		rp := ptr + "/rollback_descriptor"
		c.enum(rp+"/owner", rd.Owner, enumOwner)
		c.pattern(rp+"/paths/data_dir", rd.Paths.DataDir, reAbsPath)
		c.pattern(rp+"/paths/worktree", rd.Paths.Worktree, reAbsPath)
		c.pattern(rp+"/paths/python_env", rd.Paths.PythonEnv, reAbsPath)
		c.nullable(rp+"/paths/up_sh", rd.Paths.UpSh, reAbsPath)
		c.code(rp+"/code", rd.Code)
		c.pattern(rp+"/env_file_sha256", rd.EnvFileSHA256, reSHA256Hex)
		if im := rd.Images.Qdrant; im != nil {
			c.image(rp+"/images/qdrant", *im)
		}
		if im := rd.Images.Elasticsearch; im != nil {
			c.image(rp+"/images/elasticsearch", *im)
		}
		for i, la := range rd.LaunchArgs {
			lp := fmt.Sprintf("%s/launch_args/%d", rp, i)
			c.enum(lp+"/kind", la.Kind, enumLaunchKind)
			c.pattern(lp+"/cwd", la.Cwd, reAbsPath)
		}
		c.nonNegative(rp+"/gateway_generation", rd.GatewayGeneration)
	}
	for _, verb := range sortedOpKeys(t.LastOps) {
		op := t.LastOps[verb]
		lp := ptr + "/last_ops/" + verb
		c.enum(lp, verb, enumOpVerb)
		c.pattern(lp+"/job_id", op.JobID, reJobID)
		c.enum(lp+"/outcome", op.Outcome, enumOutcome)
	}
	if b := t.LastBackup; b != nil {
		bp := ptr + "/last_backup"
		c.pattern(bp+"/bundle", b.Bundle, reAbsPath)
		c.enum(bp+"/kind", b.Kind, enumBackupKind)
	}
}

func (c *contractCheck) code(ptr string, code Code) {
	c.minLen1(ptr+"/tag", code.Tag)
	c.nullable(ptr+"/sha", code.SHA, reGitSHA)
	c.nullable(ptr+"/previous_artifact_id", code.PreviousArtifactID, reArtifactID)
}

// Owners returns the contract's `owner` enum. It is exported because adopt
// has to ask the question BEFORE it builds a row: the owner it records is the
// account it observed on the API port, and an account outside this enum
// produces a registry that Save now refuses and Load could never read back.
// Asking here turns that into a finding naming the account, instead of a
// contract error naming a JSON pointer.
func Owners() []string { return append([]string(nil), enumOwner...) }

// KnownOwner reports whether owner is in the contract's enum.
func KnownOwner(owner string) bool {
	for _, o := range enumOwner {
		if o == owner {
			return true
		}
	}
	return false
}

func sortedTenantKeys(m map[string]*Tenant) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func sortedArtifactKeys(m map[string]*Artifact) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func sortedOpKeys(m map[string]OpRecord) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
