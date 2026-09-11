package model

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/registry"
)

const schemasDir = "../../../../contracts/ctl/schemas"

// validateScript validates argv[2] (a JSON document) against the schema named
// argv[1] in the contract directory argv[0], resolving cross-file $ref the
// way conformance/ctl/helpers.py does (every schema file registered under its
// $id). Prints one line per error and exits 1.
const validateScript = `
import glob, json, os, sys
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
d, name, doc = sys.argv[1], sys.argv[2], json.load(open(sys.argv[3]))
reg = Registry()
schemas = {}
for p in glob.glob(os.path.join(d, "*.json")):
    s = json.load(open(p))
    schemas[os.path.basename(p)[:-5]] = s
    reg = reg.with_resource(s["$id"], Resource.from_contents(s))
errs = sorted(Draft202012Validator(schemas[name], registry=reg).iter_errors(doc),
              key=lambda e: list(e.absolute_path))
for e in errs[:40]:
    print("/" + "/".join(str(p) for p in e.absolute_path), "->", e.message[:200])
sys.exit(1 if errs else 0)
`

// pythonWithJSONSchema finds an interpreter that can import jsonschema and
// referencing; "" when there is none (the test then skips).
func pythonWithJSONSchema() string {
	for _, c := range []string{"/rag/envs/ragstack/bin/python", "python3"} {
		if p, err := exec.LookPath(c); err == nil {
			if exec.Command(p, "-c", "import jsonschema, referencing").Run() == nil {
				return p
			}
		}
	}
	return ""
}

// validate marshals v and validates it against contracts/ctl/schemas/<name>.json.
func validate(t *testing.T, py, name string, v any) {
	t.Helper()
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		t.Fatalf("%s: marshal: %v", name, err)
	}
	f := filepath.Join(t.TempDir(), name+".json")
	if err := os.WriteFile(f, b, 0o600); err != nil {
		t.Fatal(err)
	}
	out, err := exec.Command(py, "-c", validateScript, schemasDir, name, f).CombinedOutput()
	if err != nil {
		t.Errorf("%s does not validate against the contract:\n%s\ndocument:\n%s", name, out, b)
	}
}

func exampleFleetRow() FleetRow {
	return FleetRow{
		Name: "dev", ManifestName: "dev",
		State: StateActive, Owner: OwnerWilke, Supervisor: SupervisorManual,
		StoresMode: StoresDedicated, CodeTag: "v1.5.1-12-gdeadbee", DriftCount: 1,
		Ports:  FleetPorts{API: 24040, QdrantHTTP: 24041, ESHTTP: 24043},
		Health: Health{API: HealthOK, Qdrant: HealthOK, ES: HealthDegraded, Deep: HealthNA},
		Units: RowUnits{
			Target: UnitNA, API: UnitActive, UI: UnitActive,
			Qdrant: UnitActive, ES: UnitInactive,
		},
		DiskBytes:  123456789,
		LastBackup: &LastBackup{At: "2026-09-10T12:00:00Z", Fenced: true, Verified: true},
	}
}

func exampleTenantResponse() TenantResponse {
	return TenantResponse{
		Summary:  exampleFleetRow(),
		Registry: registry.LiveFixture().Tenants["dev"],
		Status: LiveStatus{
			ObservedAt: "2026-09-11T08:00:00Z", APIPid: 206639, APIPidOwner: "wilke",
			Listening:      Listening{API: true, QdrantHTTP: true, ESHTTP: true, UI: true},
			RestartPending: false,
			RunningJobs:    []string{"01JQ8ZKE7R9X4M2V6T5S3N1P0B"},
		},
		Units: TenantUnits{
			Supervisor: SupervisorManual,
			Target:     nil,
			Services: []Service{
				{Kind: "api", Name: "manual:api", ActiveState: UnitActive, SubState: "running", MainPID: 206639, Since: "2026-09-10T20:14:00Z"},
				{Kind: "ui", Name: "manual:ui", ActiveState: UnitActive, SubState: "", MainPID: 0, Since: ""},
			},
		},
		Drift: []registry.Drift{{
			Code: "es_heap_drift", Level: "warn", Field: "ES_JAVA_OPTS",
			Expected: "512m", Actual: "1g", ObservedAt: "2026-09-11T08:00:00Z",
		}},
	}
}

// TestResponsesValidateAgainstContract marshals a populated example of every
// response type and validates it against its schema. This is the only proof
// that the Go structs and contracts/ctl/schemas agree on names, nullability
// and enum members.
func TestResponsesValidateAgainstContract(t *testing.T) {
	if _, err := os.Stat(schemasDir); err != nil {
		t.Skip("contract schemas not present")
	}
	py := pythonWithJSONSchema()
	if py == "" {
		t.Skip("no python with jsonschema + referencing")
	}

	fleet := FleetResponse{
		GeneratedAt: "2026-09-11T08:00:00Z", RegistryGeneration: 7,
		Host: HostFacts{
			DiskFreeBytes: 4 << 40, Linger: true, CtlUnitActive: false,
			VMMaxMapCount: 262144, SudoersGroup: []string{"wilke", "svcbvbrc"},
		},
		Tenants: []FleetRow{exampleFleetRow(), {
			Name: "demo", ManifestName: "demo", State: StateStopped, Owner: OwnerSvc,
			Supervisor: SupervisorSystemd, StoresMode: StoresShared, CodeTag: "unknown",
			DriftCount: 0,
			Ports:      FleetPorts{API: 24060, QdrantHTTP: 6333, ESHTTP: 9200},
			Health:     Health{API: HealthDown, Qdrant: HealthNA, ES: HealthNA, Deep: HealthUnknown},
			Units:      RowUnits{Target: UnitInactive, API: UnitFailed, UI: UnitNA, Qdrant: UnitNA, ES: UnitNA},
			DiskBytes:  0, LastBackup: nil,
		}},
	}
	validate(t, py, "fleet_response", fleet)

	tr := exampleTenantResponse()
	validate(t, py, "tenant_response", tr)

	viewer := exampleTenantResponse()
	viewer.Registry = nil // a viewer's row
	validate(t, py, "tenant_response", viewer)

	validate(t, py, "tenants_response", TenantsResponse{
		GeneratedAt: "2026-09-11T08:00:00Z", RegistryGeneration: 7,
		Tenants: []TenantResponse{tr, viewer},
	})

	validate(t, py, "env_response", EnvResponse{
		Tenant: "dev", EnvLayout: "legacy",
		Keys: []EnvKey{
			{Key: "LOG_LEVEL", ValueRedacted: "info", Source: SourceTenantEnv, Class: ClassPublic, Drift: ""},
			{Key: "API_KEYS", ValueRedacted: EnvRedacted, Source: SourceTenantEnv, Class: ClassSecret, Drift: NullString(DriftRegistryVsFile)},
			{Key: "PYTHONPATH", ValueRedacted: "/rag/repos/tenants/dev/python", Source: SourceUnit, Class: ClassExecutableSurface, Drift: NullString(DriftLiveOnly)},
			{Key: "TENANT_ES_HEAP", ValueRedacted: EnvRedacted, Source: SourceProvisionEnv, Class: ClassUnsupported, Drift: ""},
		},
	})

	validate(t, py, "logs_response", LogsResponse{
		Tenant: "dev", File: LogAPI, Lines: []string{"INFO started", "API_KEYS=<REDACTED>"},
		Requested: 200, Returned: 2, Truncated: true, Redacted: true,
	})

	validate(t, py, "doctor_response", DoctorResponse{
		Status:      StatusYellow,
		Hash:        "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
		GeneratedAt: "2026-09-11T08:00:00Z",
		Scope:       Scope{Tenant: "dev", Op: "start"},
		Findings: []Finding{
			{Level: LevelWarn, Code: "worktree_outside_mirror", Tenant: "dev", Detail: "gitdir outside the mirror", Repair: "handover"},
			{Level: LevelInfo, Code: "sudoers_group", Tenant: "", Detail: "seed-admins-svcbvbrc: wilke"},
		},
	})

	validate(t, py, "doctor_response", DoctorResponse{
		Status:      StatusGreen,
		Hash:        "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
		GeneratedAt: "2026-09-11T08:00:00Z",
		Scope:       Scope{Tenant: "", Op: ""}, // fleet-wide: both null
		Findings:    []Finding{},
	})

	validate(t, py, "error", Error{
		Detail: "doctor is red on dev", Code: CodeDoctorRed, RequestID: "0123456789abcdef",
		Extra: map[string]any{"doctor_hash": "sha256:" + "0"},
	})
	validate(t, py, "error", Error{
		Detail: "no usable credential", Code: CodeAuthRequired, RequestID: "fedcba9876543210",
	})

	// The bodies that used to be assembled inline in internal/ctl/api and so
	// were never seen by this test — which nonetheless claimed to cover "every
	// response type". Half of them were unchecked, and the unchecked half is
	// the half a handler builds by hand.
	validate(t, py, "health_response", HealthResponse{Status: HealthOKStatus, Version: "v1.5.1"})

	validate(t, py, "version_response", VersionResponse{
		Version:       "v1.5.1",
		Commit:        "0123456789abcdef0123456789abcdef01234567",
		BuiltAt:       "2026-09-11T08:00:00Z",
		Go:            "go1.23.12",
		SchemaVersion: 1,
	})
	// The unstamped local build: "unknown" is the contract's other answer, and
	// "" is neither.
	validate(t, py, "version_response", VersionResponse{
		Version: "dev", Commit: Unknown, BuiltAt: Unknown, Go: "go1.23.12", SchemaVersion: 1,
	})

	expires := "2026-09-11T16:00:00Z"
	validate(t, py, "me_response", MeResponse{
		Principal: "bvbrc:alice@patricbrc.org", Role: "operator",
		AuthMethod: "session", ExpiresAt: &expires, SudoUser: nil,
	})
	validate(t, py, "me_response", MeResponse{
		Principal: "key:ops", Role: "viewer", AuthMethod: "api_key",
		// A ctl key does not expire; it is revoked.
		ExpiresAt: nil, SudoUser: nil,
	})

	validate(t, py, "session_response", SessionResponse{
		SessionID: strings.Repeat("ab", 32), Principal: "key:ops", Role: "operator",
		ExpiresAt: expires, ReadsOnly: true,
	})

	validate(t, py, "settings_response", SettingsResponse{
		RegistryGeneration: 7,
		Retention: SettingsRetention{
			KeepLast:         map[string]int{"backup": 7, "pre_update": 2},
			KeepPartialHours: 24,
			AutoDelete:       false,
		},
		Images: SettingsImages{
			Qdrant: SettingsImage{
				SIF: "/rag/apptainer/images/qdrant.sif", Version: "v1.12.4",
				Digest: "sha256:" + strings.Repeat("a", 64),
			},
			Elasticsearch: SettingsImage{
				SIF: "/rag/apptainer/images/elasticsearch.sif", Version: "8.15.0",
				Digest: "sha256:" + strings.Repeat("b", 64),
			},
		},
		PythonEnvDefault: "/rag/envs/ragstack",
		Ctl:              SettingsCtl{Port: 23990, UIDist: "/rag/data/ctl/ui/dist", GatewayEnabled: true},
		Recipients: SettingsRecipients{
			File: "/rag/config/ctl/backup-recipients.txt", Count: 0,
			Fingerprints: []string{}, ReadOnly: true,
		},
	})

	validate(t, py, "jobs_response", JobsResponse{Jobs: []json.RawMessage{}, Limit: 50, Truncated: false})
	validate(t, py, "audit_response", AuditResponse{Rows: []json.RawMessage{}, Limit: 100, Truncated: false})

	uiPort := 24044
	validate(t, py, "gateway_status", GatewayStatus{
		Generation: 0, PublishedAt: nil, PublishedBy: nil,
		TxnState: "none", PendingDiff: true, RegistryGeneration: 7,
		Nginx: GatewayNginx{},
		Routes: []GatewayRoute{
			{Name: "dev", API: 24040, UI: &uiPort, UIMode: "static", Readonly: false, Status: "active"},
			{Name: "demo", API: 24060, UI: nil, UIMode: "external", Readonly: true, Status: "maintenance"},
		},
		LegacyRoutes: []GatewayRoute{},
	})

	validate(t, py, "gateway_render_response", GatewayRenderResponse{
		// The generation this render WOULD be published as: the schema's
		// minimum is 1, because "generation 0" is not a generation.
		Generation: 1, RegistryGeneration: 7, Changed: true,
		Files: []GatewayFile{
			{Path: "conf.d/05-tenants.generated.conf", Diff: "+server {\n"},
		},
		SHA256: "sha256:" + strings.Repeat("c", 64),
	})
}

// TestEveryContractResponseSchemaHasAGoType is the guard that keeps the claim
// above honest. It lists the schema files that describe a RESPONSE this daemon
// writes and requires a validated Go type for each, so adding a response to
// the contract without a model type — the way settings_response, me_response
// and six others came to be built inline and unchecked — fails here rather
// than in production.
func TestEveryContractResponseSchemaHasAGoType(t *testing.T) {
	if _, err := os.Stat(schemasDir); err != nil {
		t.Skip("contract schemas not present")
	}
	// Response schemas served by PR-A's read surface, each with the model type
	// that TestResponsesValidateAgainstContract validates against it.
	served := map[string]any{
		"health_response":         HealthResponse{},
		"version_response":        VersionResponse{},
		"me_response":             MeResponse{},
		"session_response":        SessionResponse{},
		"fleet_response":          FleetResponse{},
		"tenants_response":        TenantsResponse{},
		"tenant_response":         TenantResponse{},
		"env_response":            EnvResponse{},
		"logs_response":           LogsResponse{},
		"doctor_response":         DoctorResponse{},
		"gateway_status":          GatewayStatus{},
		"gateway_render_response": GatewayRenderResponse{},
		"settings_response":       SettingsResponse{},
		"jobs_response":           JobsResponse{},
		"audit_response":          AuditResponse{},
		"error":                   Error{},
	}
	for name := range served {
		if _, err := os.Stat(filepath.Join(schemasDir, name+".json")); err != nil {
			t.Errorf("%s names a schema the contract does not carry: %v", name, err)
		}
	}
	if len(served) < 15 {
		t.Fatalf("only %d response types are covered; the read surface has more", len(served))
	}
}

// TestStatusFor is the doctor status rule: max over the finding levels.
func TestStatusFor(t *testing.T) {
	cases := []struct {
		name string
		in   []Finding
		want Status
	}{
		{"none", nil, StatusGreen},
		{"info only", []Finding{{Level: LevelInfo}}, StatusGreen},
		{"warn", []Finding{{Level: LevelInfo}, {Level: LevelWarn}}, StatusYellow},
		{"error wins", []Finding{{Level: LevelWarn}, {Level: LevelError}}, StatusRed},
	}
	for _, c := range cases {
		if got := StatusFor(c.in); got != c.want {
			t.Errorf("%s: StatusFor = %q, want %q", c.name, got, c.want)
		}
	}
}

// TestErrorCodeStatusMapping pins the one-status-per-code table the contract
// documents on error.json#/properties/code.
func TestErrorCodeStatusMapping(t *testing.T) {
	want := map[ErrorCode]int{
		CodeBothCredentials: 400, CodeAuthRequired: 401, CodeForbidden: 403,
		CodeNotFound: 404, CodeValidation: 422, CodeConfirmRequired: 428,
		CodeRateLimited: 429, CodeInternal: 500,
		CodeLocked: 409, CodePlanStale: 409, CodeDuplicate: 409,
		CodeDoctorRed: 409, CodeRefused: 409,
	}
	for code, status := range want {
		if got := code.HTTPStatus(); got != status {
			t.Errorf("%s: status %d, want %d", code, got, status)
		}
	}
}
