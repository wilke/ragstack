package api

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/settings"
)

// FakeBackend serves the 2026-09-10 capture instead of the live host: the four
// coconut tenants as registry.LiveFixture records them
// (go/internal/ctl/testdata/live-2026-09-10), with plausible, DETERMINISTIC
// live facts layered on top.
//
// It is what `--fake-drivers` runs and what the conformance suite is pointed
// at. Two properties it must keep:
//
//   - Deterministic. `GET /v1/doctor` twice must produce the same findings and
//     therefore the same hash; the conformance suite asserts exactly that, and
//     a fake that jittered would make a contract test flaky rather than a
//     daemon wrong.
//   - No secret, ever. Not even a fake one: the suite walks every read
//     response for field names matching (?i)(api_key|password|secret|token|dsn)
//     and for values shaped like a credential, and a fixture that carried a
//     plausible-looking 64-hex string would fail that check exactly as a real
//     leak would — which is the point.
type FakeBackend struct {
	fleet *registry.Fleet
	now   func() time.Time
}

// NewFakeBackend returns the fixture backend.
func NewFakeBackend() *FakeBackend {
	return &FakeBackend{fleet: registry.LiveFixture(), now: time.Now}
}

// NewFakeBackendAt is NewFakeBackend with the clock injected, so a test can
// assert on `generated_at`.
func NewFakeBackendAt(now func() time.Time) *FakeBackend {
	b := NewFakeBackend()
	if now != nil {
		b.now = now
	}
	return b
}

// Registry returns the fixture fleet.
func (b *FakeBackend) Registry(context.Context) (*registry.Fleet, error) { return b.fleet, nil }

func (b *FakeBackend) stamp() string { return b.now().UTC().Format(time.RFC3339) }

// order returns the tenants in display order, then anything display_order
// forgot, by name — the same rule the real fleet view uses, so the fake cannot
// hide an ordering bug.
func (b *FakeBackend) order() []*registry.Tenant {
	seen := map[string]bool{}
	out := make([]*registry.Tenant, 0, len(b.fleet.Tenants))
	for _, name := range b.fleet.DisplayOrder {
		if t, ok := b.fleet.Tenants[name]; ok && !seen[name] {
			seen[name] = true
			out = append(out, t)
		}
	}
	rest := make([]string, 0)
	for name := range b.fleet.Tenants {
		if !seen[name] {
			rest = append(rest, name)
		}
	}
	sort.Strings(rest)
	for _, name := range rest {
		out = append(out, b.fleet.Tenants[name])
	}
	return out
}

// Fleet is the dashboard poll.
func (b *FakeBackend) Fleet(context.Context) (*model.FleetResponse, error) {
	rows := make([]model.FleetRow, 0, len(b.fleet.Tenants))
	for _, t := range b.order() {
		rows = append(rows, b.row(t))
	}
	return &model.FleetResponse{
		GeneratedAt:        b.stamp(),
		RegistryGeneration: b.fleet.Generation,
		Host: model.HostFacts{
			// The facts the 2026-09-10 reboot made load-bearing: max_map_count
			// is the ES prerequisite the host lost on reboot, linger and the
			// ctl unit are what "nothing starts at boot" means.
			DiskFreeBytes: 1_800_000_000_000,
			Linger:        false,
			CtlUnitActive: false,
			VMMaxMapCount: 262144,
			SudoersGroup:  []string{"wilke"},
		},
		Tenants: rows,
	}, nil
}

func (b *FakeBackend) row(t *registry.Tenant) model.FleetRow {
	return model.FleetRow{
		Name:         t.Name,
		ManifestName: t.ManifestName,
		State:        model.State(t.State),
		Owner:        model.Owner(t.Owner),
		Supervisor:   model.Supervisor(t.Supervisor),
		StoresMode:   storesMode(t),
		CodeTag:      t.Code.Tag,
		DriftCount:   len(t.Drift),
		Ports: model.FleetPorts{
			API:        t.Ports.API,
			QdrantHTTP: t.Ports.QdrantHTTP,
			ESHTTP:     t.Ports.ESHTTP,
		},
		Health: model.Health{
			API:    healthFor(t.State == "active"),
			Qdrant: storeHealth(t.Stores.Qdrant.Ownership),
			ES:     storeHealth(t.Stores.Elasticsearch.Ownership),
			// Deep health needs a tenant API key. PR-A holds none and sends
			// none, so the honest answer is "not probed", not "ok".
			Deep: model.HealthNA,
		},
		Units:      rowUnits(t),
		DiskBytes:  fakeDisk(t.Name),
		LastBackup: nil, // no backup tooling exists yet; that is the plan's premise
	}
}

func storesMode(t *registry.Tenant) model.StoresMode {
	q, e := t.Stores.Qdrant.Ownership, t.Stores.Elasticsearch.Ownership
	switch {
	case q == registry.OwnershipExclusive && e == registry.OwnershipExclusive:
		return model.StoresDedicated
	case q == registry.OwnershipShared && e == registry.OwnershipShared:
		return model.StoresShared
	case (q == registry.OwnershipExclusive && e == registry.OwnershipShared) ||
		(q == registry.OwnershipShared && e == registry.OwnershipExclusive):
		return model.StoresMixed
	default:
		return model.StoresUnknown
	}
}

func healthFor(up bool) model.HealthState {
	if up {
		return model.HealthOK
	}
	return model.HealthDown
}

// storeHealth: a SHARED store is not this tenant's to report on — the ctl
// observes it, never manages it, and a green light on a store three tenants
// write to would be a claim the ctl cannot make.
func storeHealth(ownership string) model.HealthState {
	if ownership == registry.OwnershipExclusive {
		return model.HealthOK
	}
	return model.HealthNA
}

// rowUnits: every fixture tenant is hand-started (`supervisor: manual`), so
// there are no units to report and the honest answer is n/a across the board.
func rowUnits(t *registry.Tenant) model.RowUnits {
	if t.Supervisor != string(model.SupervisorSystemd) {
		return model.RowUnits{
			Target: model.UnitNA, API: model.UnitNA, UI: model.UnitNA,
			Qdrant: model.UnitNA, ES: model.UnitNA,
		}
	}
	state := model.UnitInactive
	if t.State == "active" {
		state = model.UnitActive
	}
	return model.RowUnits{Target: state, API: state, UI: state, Qdrant: state, ES: state}
}

// fakeDisk is a stable per-tenant size: the same name always answers the same
// number, so two polls never disagree for a reason that is not a fact.
func fakeDisk(name string) int64 {
	sum := sha256.Sum256([]byte("disk:" + name))
	// 1 GiB … ~1 TiB, quantised to MiB so the number reads like a `du`.
	return (1 << 30) + int64(uint32(sum[0])<<16|uint32(sum[1])<<8|uint32(sum[2]))*(1<<20)/16
}

// Tenants lists every tenant in the OPERATOR shape; the handler reduces.
func (b *FakeBackend) Tenants(ctx context.Context) (*model.TenantsResponse, error) {
	rows := make([]model.TenantResponse, 0, len(b.fleet.Tenants))
	for _, t := range b.order() {
		one, err := b.Tenant(ctx, t.Name, false)
		if err != nil {
			return nil, err
		}
		rows = append(rows, *one)
	}
	return &model.TenantsResponse{
		GeneratedAt:        b.stamp(),
		RegistryGeneration: b.fleet.Generation,
		Tenants:            rows,
	}, nil
}

// Tenant is one tenant. With viewer, the registry row is not assembled at all.
func (b *FakeBackend) Tenant(_ context.Context, name string, viewer bool) (*model.TenantResponse, error) {
	t, ok := b.fleet.Tenants[name]
	if !ok {
		return nil, ErrNotFound
	}
	resp := &model.TenantResponse{
		Summary: b.row(t),
		Status: model.LiveStatus{
			ObservedAt: b.stamp(),
			// The fake has no /proc to read. Inventing a pid would be the one
			// kind of lie that costs an operator a debugging session.
			APIPid:         0,
			APIPidOwner:    "",
			Listening:      model.Listening{API: t.State == "active", QdrantHTTP: false, ESHTTP: false, UI: false},
			RestartPending: t.RestartPending,
			RunningJobs:    []string{}, // the job engine lands in PR-C
		},
		Units: model.TenantUnits{
			Supervisor: model.Supervisor(t.Supervisor),
			Target:     nil,
			Services:   fakeServices(t),
		},
		Drift: append([]registry.Drift{}, t.Drift...),
	}
	if !viewer {
		resp.Registry = t
	}
	return resp, nil
}

func fakeServices(t *registry.Tenant) []model.Service {
	kinds := []string{"api", "ui"}
	if t.Stores.Qdrant.Ownership == registry.OwnershipExclusive {
		kinds = append(kinds, "qdrant")
	}
	if t.Stores.Elasticsearch.Ownership == registry.OwnershipExclusive {
		kinds = append(kinds, "es")
	}
	out := make([]model.Service, 0, len(kinds))
	for _, kind := range kinds {
		out = append(out, model.Service{
			Kind: kind,
			// A hand-started process has no unit name; saying so is more
			// useful than an invented one that `systemctl` would not know.
			Name:        "manual:" + kind,
			ActiveState: model.UnitNA,
			SubState:    "",
			MainPID:     0,
			Since:       "",
		})
	}
	return out
}

// fakePublicEnv are the public-class keys the fixture tenants actually carry
// (see testdata/live-2026-09-10/tenants/*/config/tenant.env), with the values
// derived from the registry row so the two cannot disagree.
func (b *FakeBackend) fakePublicEnv(t *registry.Tenant) [][2]string {
	rows := [][2]string{
		{"IDENTITY_PROVIDER", t.Identity.Provider},
		{"DEFAULT_ROLE", "user"},
		{"VECTOR_BACKEND", "qdrant"},
		{"TEXT_BACKEND", "elasticsearch"},
		{"USER_STORE_BACKEND", "sqlite"},
		{"JOB_STORE_BACKEND", "sqlite"},
		{"COLLECTION_STORE_BACKEND", "sqlite"},
		{"MAX_COLLECTIONS", "100"},
		{"RERANK_ENABLED", "true"},
		{"LOG_LEVEL", "INFO"},
		{"EMBEDDING_MODEL", "Salesforce/SFR-Embedding-Mistral"},
		{"EMBEDDING_MODEL_DIM", "4096"},
	}
	if t.Identity.AdminSubjectsCount > 0 {
		subjects := make([]string, 0, t.Identity.AdminSubjectsCount)
		for i := 1; i <= t.Identity.AdminSubjectsCount; i++ {
			subjects = append(subjects, fmt.Sprintf("bvbrc:admin%d@example.org", i))
		}
		rows = append(rows, [2]string{"ADMIN_SUBJECTS", strings.Join(subjects, ",")})
	}
	if t.Stores.Neo4j.Ownership == registry.OwnershipExternal && t.Stores.Neo4j.URL != "" {
		rows = append(rows, [2]string{"GRAPH_BACKEND", "neo4j"}, [2]string{"NEO4J_USER", "neo4j"})
	}
	return rows
}

// Env is the classified key list. The class comes from settings.Classify — the
// same table adopt, backup and the redactors use — rather than from a second
// list here, so a key cannot be public on this surface and secret on that one.
func (b *FakeBackend) Env(_ context.Context, name string) (*model.EnvResponse, error) {
	t, ok := b.fleet.Tenants[name]
	if !ok {
		return nil, ErrNotFound
	}
	keys := make([]model.EnvKey, 0, 20)
	for _, kv := range b.fakePublicEnv(t) {
		keys = append(keys, envRow(kv[0], kv[1], model.SourceTenantEnv))
	}
	// The key triple every tenant carries. Its VALUES never leave the host;
	// what the env API shows is that the key exists and where it lives.
	for _, key := range []string{"API_KEYS", "API_KEY_TENANTS", "API_KEY_ROLES"} {
		keys = append(keys, envRow(key, "unused: the value is never read here", model.SourceSecretsEnv))
	}
	// One executable-surface key, so the class is exercised rather than
	// theoretical: these are the keys the HTTP API refuses to set at all.
	if exec := settings.ExecutableSurfaceKeys(); len(exec) > 0 {
		keys = append(keys, envRow(exec[0], "unused", model.SourceTenantEnv))
	}
	return &model.EnvResponse{Tenant: t.Name, EnvLayout: t.EnvLayout, Keys: keys}, nil
}

// envRow builds one row, applying the contract's redaction rule: a value is
// shown only for a PUBLIC key; every other class shows the literal placeholder
// and the caller cannot tell a long secret from a short one.
func envRow(key, value string, source model.Source) model.EnvKey {
	class := settings.Classify(key)
	shown := model.EnvRedacted
	if class == settings.Public {
		shown = value
	}
	return model.EnvKey{
		Key:           key,
		ValueRedacted: shown,
		Source:        source,
		Class:         model.Class(class.String()),
		Drift:         "",
	}
}

// Logs is a redacted, bounded tail. The fixture lines are synthetic but carry
// the shapes a redactor must survive — an assignment and a token signature —
// so the canary is exercised on the read path too.
func (b *FakeBackend) Logs(_ context.Context, name, file string, lines int) (*model.LogsResponse, error) {
	t, ok := b.fleet.Tenants[name]
	if !ok {
		return nil, ErrNotFound
	}
	if !b.hasLog(t, file) {
		return nil, ErrNotFound
	}
	raw := []string{
		fmt.Sprintf("INFO %s: ragstack %s starting (tenant=%s)", file, t.Code.Tag, t.Name),
		fmt.Sprintf("INFO %s: bound %s", file, t.API.Bind),
		"API_KEYS='[\"0f1e2d3c4b5a69788796a5b4c3d2e1f00112233445566778899aabbccddeeff0\"]'",
		"INFO identity: verified un=alice@patricbrc.org|sig=" + strings.Repeat("ab", 32),
		fmt.Sprintf("INFO %s: ready", file),
	}
	redacted := make([]string, 0, len(raw))
	for _, line := range raw {
		redacted = append(redacted, settings.RedactText(line))
	}
	truncated := false
	if lines < len(redacted) {
		redacted = redacted[len(redacted)-lines:]
		truncated = true
	}
	return &model.LogsResponse{
		Tenant:    t.Name,
		File:      model.LogFile(file),
		Lines:     redacted,
		Requested: lines,
		Returned:  len(redacted),
		Truncated: truncated,
		Redacted:  true,
	}, nil
}

// hasLog: a tenant has no `ui` log when its UI is served statically, and no
// store log for a store it shares — asking for one is a 404, not an empty tail.
func (b *FakeBackend) hasLog(t *registry.Tenant, file string) bool {
	switch file {
	case "api":
		return true
	case "ui":
		return t.UI.Mode != registry.UIModeStatic
	case "qdrant":
		return t.Stores.Qdrant.Ownership == registry.OwnershipExclusive
	case "es":
		return t.Stores.Elasticsearch.Ownership == registry.OwnershipExclusive
	default:
		return false
	}
}

// Doctor runs the fixture diagnostics. Deterministic by construction: every
// finding is derived from the registry row, none from a clock or a probe.
func (b *FakeBackend) Doctor(_ context.Context, tenant, op string) (*model.DoctorResponse, error) {
	if tenant != "" {
		if _, ok := b.fleet.Tenants[tenant]; !ok {
			return nil, ErrNotFound
		}
	}
	findings := []model.Finding{{
		Level:  model.LevelInfo,
		Code:   "fake_drivers",
		Tenant: "",
		Detail: "the daemon is running with --fake-drivers: every host probe below is a recorded fixture, not this machine",
	}}
	if !b.fleet.Ctl.GatewayEnabled {
		findings = append(findings, model.Finding{
			Level:  model.LevelInfo,
			Code:   "gateway_not_managed",
			Tenant: "",
			Detail: "the ctl does not own a gateway generation yet; routing is still hand-edited (PR-B)",
			Repair: "gateway-apply",
		})
	}
	for _, t := range b.order() {
		if t.Supervisor != string(model.SupervisorSystemd) {
			findings = append(findings, model.Finding{
				Level:  model.LevelWarn,
				Code:   "supervisor_manual",
				Tenant: registry.NullString(t.Name),
				Detail: fmt.Sprintf("tenant %s is hand-started (owner %s); nothing restarts it at boot", t.Name, t.Owner),
				Repair: "render-units",
			})
		}
		for _, d := range t.Drift {
			findings = append(findings, model.Finding{
				Level:  model.Level(d.Level),
				Code:   d.Code,
				Tenant: registry.NullString(t.Name),
				Detail: fmt.Sprintf("%s: %s is %q live but %q in %s/config/provision.env", t.Name, d.Field, d.Actual, d.Expected, t.DataDir),
			})
		}
	}
	if tenant != "" {
		kept := findings[:0:0]
		for _, f := range findings {
			if string(f.Tenant) == tenant || f.Tenant == "" {
				kept = append(kept, f)
			}
		}
		findings = kept
	}
	return &model.DoctorResponse{
		Status:      model.StatusFor(findings),
		Hash:        doctorHash(findings),
		GeneratedAt: b.stamp(),
		Scope:       model.Scope{Tenant: registry.NullString(tenant), Op: registry.NullString(op)},
		Findings:    findings,
	}, nil
}

// doctorHash is what a Plan pins and what --force-with-doctor-diff quotes: a
// digest of the FINDINGS alone. Not of the timestamp, not of the scope — two
// runs a second apart over an unchanged host must hash the same, or every plan
// would go stale on its own.
func doctorHash(findings []model.Finding) string {
	canonical, err := json.Marshal(findings)
	if err != nil {
		canonical = []byte(fmt.Sprintf("%v", findings))
	}
	sum := sha256.Sum256(canonical)
	return "sha256:" + hex.EncodeToString(sum[:])
}
