package api

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/doctor"
	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/fleet"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/render"
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
	mu    sync.RWMutex
	fleet *registry.Fleet
	now   func() time.Time
}

// f is the fixture fleet under the read lock: SaveFleet swaps the pointer,
// and every projection reads through here so the race detector, not luck,
// says the two never overlap.
func (b *FakeBackend) f() *registry.Fleet {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return b.fleet
}

// LoadFleet is the job engine's fleet loader in --fake-drivers mode: a DEEP
// COPY of the fixture, so a plan (or an op's run) can never mutate what the
// read surface is serving.
func (b *FakeBackend) LoadFleet() (*registry.Fleet, error) {
	return cloneFleet(b.f())
}

// SaveFleet is the job engine's fleet saver in --fake-drivers mode: the
// fixture is replaced by a copy of f with the generation advanced, which is
// exactly what registry.Save does to the file in real mode — so GET
// /v1/settings and /v1/fleet reflect a settings-put the way they would on a
// host.
func (b *FakeBackend) SaveFleet(f *registry.Fleet) error {
	if err := f.Validate(); err != nil {
		return err
	}
	next, err := cloneFleet(f)
	if err != nil {
		return err
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	next.Generation = b.fleet.Generation + 1
	next.UpdatedAt = b.now().UTC().Format(time.RFC3339)
	next.UpdatedBy = "settings-put (fake drivers)"
	b.fleet = next
	// Like registry.Save, the caller's copy learns the generation it became.
	f.Generation, f.UpdatedAt, f.UpdatedBy = next.Generation, next.UpdatedAt, next.UpdatedBy
	return nil
}

// cloneFleet is a JSON round trip: the registry types are the contract's
// shapes, so this is exactly a Load of what a Save would write.
func cloneFleet(f *registry.Fleet) (*registry.Fleet, error) {
	b, err := json.Marshal(f)
	if err != nil {
		return nil, err
	}
	var out registry.Fleet
	if err := json.Unmarshal(b, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ConformanceArtifactID is the prepared artifact the fixture carries so that
// `tenant create` can be exercised end to end against --fake-drivers.
//
// It is in the FIXTURE rather than in registry.LiveFixture because it is not a
// fact about coconut on 2026-09-10: it is a fact about the fake host the
// conformance suite runs against, and the live fixture is the reference the
// manifest, nginx and display-order goldens are asserted against.
const ConformanceArtifactID = "conformance-artifact"

// ConformanceMirror is the bare repository the fixture daemon prepares and
// checks out from. Nothing reads it on disk — the fake Git driver answers from
// its ref table — but the path has to be a real, absolute one because the ops
// refuse a mirror that is not.
const ConformanceMirror = "/rag/repos/ragstack.git"

// conformanceSHA is the commit the fixture artifact pins. Forty hex characters
// so it matches the contract's GitSha, and recognisably not a real commit.
const conformanceSHA = "c0f0c0f0c0f0c0f0c0f0c0f0c0f0c0f0c0f0c0f0"

// NewFakeBackend returns the fixture backend.
func NewFakeBackend() *FakeBackend {
	return &FakeBackend{fleet: FixtureFleet(), now: time.Now}
}

// FixtureFleet is the 2026-09-10 capture PLUS one tenant the ctl itself
// supervises.
//
// The four captured tenants are all hand-started, wilke-owned, with every
// store capability false — which is the truth about coconut and is also a
// fleet on which no lifecycle verb can do anything. `backup`, on such a
// tenant, skips both stores and stops the API through a pidfile; a
// conformance suite pointed at only those four could not tell a backup that
// works from one that does nothing.
//
// So the fixture carries `ctlfixture`: systemd-supervised, svcbvbrc-owned,
// exclusive stores with snapshot confirmed, a postgres-local relational store.
// It is the tenant the mutation conformance exercises, and it exists ONLY in
// the fake backend — registry.LiveFixture, which the adopt and projection
// tests compare against the real host, is untouched.
func FixtureFleet() *registry.Fleet {
	f := registry.LiveFixture()
	addManagedFixture(f)
	// One prepared artifact, so `tenant create` has something to create from.
	f.Artifacts[ConformanceArtifactID] = &registry.Artifact{
		SHA: conformanceSHA, Tag: "conformance",
		Worktree:   "/rag/data/ctl/artifacts/" + ConformanceArtifactID + "/worktree",
		UIDist:     "/rag/data/ctl/artifacts/" + ConformanceArtifactID + "/worktree/frontend/dist",
		PythonEnv:  "/rag/envs/ragstack",
		PreparedAt: "2026-09-14T00:00:00Z", PreparedBy: "local:0", SchemaCompatible: true,
	}
	// And the artifact the managed fixture tenant was BUILT from. A row whose
	// `artifact_id` names nothing the fleet holds is an inconsistent registry,
	// and `restore --as` is the verb that notices: it lays the fresh tenant
	// down from the SOURCE tenant's artifact and refuses when that artifact is
	// not prepared on this host.
	f.Artifacts[managedFixtureArtifactID] = &registry.Artifact{
		SHA: strings.Repeat("ab", 20), Tag: "v1.5.3",
		Worktree:   "/rag/data/ctl/artifacts/" + managedFixtureArtifactID + "/worktree",
		UIDist:     "/rag/data/ctl/artifacts/" + managedFixtureArtifactID + "/worktree/frontend/dist",
		PythonEnv:  "/rag/envs/ragstack",
		PreparedAt: "2026-09-14T00:00:00Z", PreparedBy: "local:0", SchemaCompatible: true,
	}
	return f
}

// managedFixtureArtifactID is the artifact `ctlfixture` records.
const managedFixtureArtifactID = "v1.5.3-abababababab"

// managedFixtureName is the tenant the mutation conformance acts on.
const managedFixtureName = "ctlfixture"

func addManagedFixture(f *registry.Fleet) {
	r := paths.NewRoots(f.RagRoot, paths.Overrides{})
	tp := paths.TenantPaths(r, managedFixtureName, managedFixtureName)
	t := registry.NewTenant(managedFixtureName, managedFixtureName)
	t.DataDir, t.Worktree, t.PythonEnv = tp.DataDir, tp.Worktree, "/rag/envs/ragstack"
	t.Code = registry.Code{Tag: "v1.5.3", SHA: registry.NullString(strings.Repeat("ab", 20))}
	t.ArtifactID = managedFixtureArtifactID
	t.Ports = paths.Block(9)
	t.API = registry.API{Bind: "127.0.0.1", PidFile: tp.PidFile, Log: tp.APILog}
	t.UI = registry.UI{Mode: registry.UIModeStatic, Base: "/ragstack/" + managedFixtureName + "/ui/"}
	t.Supervisor, t.Owner, t.State = "systemd", "svcbvbrc", "active"
	t.DesiredBoot, t.EnvLayout = "enabled", "managed"
	t.EnvFileSHA256, t.SecretsFileSHA256 = emptySHA256Hex, emptySHA256Hex
	t.Identity = registry.Identity{Provider: "bvbrc", AdminSubjectsCount: 1}
	t.SecretRefs = []registry.SecretRef{
		{Key: "API_KEYS", File: "secrets.env"},
		{Key: "API_KEY_TENANTS", File: "secrets.env"},
		{Key: "API_KEY_ROLES", File: "secrets.env"},
		{Key: "TENANT_PG_PASSWORD", File: "secrets.env"},
	}
	// Capabilities all true: this is a tenant whose stores an operator has
	// CONFIRMED are its own, which is what lets backup snapshot them.
	caps := registry.Capabilities{Stop: true, Purge: true, Restore: true, Snapshot: true}
	t.Stores.Qdrant = registry.Qdrant{
		URL: fmt.Sprintf("http://localhost:%d", t.Ports.QdrantHTTP), Instance: registry.NullString("qdrant-" + managedFixtureName),
		SIF: "/rag/apptainer/images/qdrant.sif", Ownership: registry.OwnershipExclusive, Capabilities: caps,
		ExtraEnv: map[string]string{},
	}
	t.Stores.Elasticsearch = registry.Elasticsearch{
		URL: fmt.Sprintf("http://localhost:%d", t.Ports.ESHTTP), Instance: registry.NullString("elasticsearch-" + managedFixtureName),
		SIF: "/rag/apptainer/images/elasticsearch.sif", Ownership: registry.OwnershipExclusive, Capabilities: caps,
		Heap: "1g", ProvisionHeap: "1g", PathRepo: "/usr/share/elasticsearch/snapshots", ExtraEnv: map[string]string{},
	}
	// The one postgres-local tenant in the fixture, so the backup's postgres
	// leg is exercised rather than skipped everywhere.
	t.Stores.Postgres = registry.Postgres{
		Kind: registry.PostgresKindLocal, Ownership: registry.OwnershipExclusive, Capabilities: caps,
		URL: registry.NullString(fmt.Sprintf("postgresql://localhost:%d", t.Ports.PG)), Port: registry.NullPort(t.Ports.PG),
		Instance: registry.NullString("postgres-" + managedFixtureName), SIF: "/rag/apptainer/images/postgres.sif",
		DataDir: registry.NullString(tp.DataDir + "/postgres"),
	}
	f.Tenants[managedFixtureName] = t
	f.DisplayOrder = append(f.DisplayOrder, managedFixtureName)
}

// emptySHA256Hex is the digest of nothing — the fixture's stand-in for a file
// hash, and a value that can never be mistaken for a credential.
const emptySHA256Hex = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

// FixtureDrivers is the in-memory HOST the fake-drivers daemon runs on: the
// env files the fixture tenants would have, plus, for every tenant whose
// stores the registry says are its own, the collections, indices, counts and
// snapshot directory those stores would hold.
//
// Seeded from the FLEET rather than written out, so a tenant added to the
// fixture gets a host that matches its registry row instead of one somebody
// remembered to update.
func FixtureDrivers(roots paths.Roots, f *registry.Fleet, now func() time.Time) drivers.FakeOptions {
	opts := drivers.FakeOptions{
		Now:         now,
		Roots:       []string{roots.DataDir, roots.CtlConfigDir, roots.CtlStateDir, roots.BackupsDir},
		Files:       fixtureFiles(roots, f),
		Collections: map[string][]string{}, Indices: map[string][]string{},
		QdrantCounts: map[string]int64{}, ESCounts: map[string]int64{},
		QdrantSnapshotDirs: map[string]string{}, UnitPorts: map[string]int{},
		CollectionsByOrigin: map[string][]string{},
	}
	for name, t := range f.Tenants {
		tp := paths.TenantPaths(roots, name, t.ManifestName)
		// The API is up for an active tenant, and its unit moves the port, so
		// a fence that stops the unit really frees the socket.
		_, _, _, _, apiUnit, _ := render.UnitNames(name)
		opts.UnitPorts[apiUnit] = t.Ports.API
		if t.State == "active" {
			opts.Listening = append(opts.Listening, t.Ports.API)
			if t.Supervisor == string(model.SupervisorSystemd) {
				opts.Active = append(opts.Active, apiUnit)
			}
		}
		if t.Stores.Qdrant.Ownership == registry.OwnershipExclusive && t.Stores.Qdrant.Capabilities.Snapshot {
			url := t.Stores.Qdrant.URL
			opts.Collections[url] = []string{name + "_docs", name + "_chunks"}
			opts.QdrantCounts[url+"/"+name+"_docs"] = 1200
			opts.QdrantCounts[url+"/"+name+"_chunks"] = 34000
			opts.QdrantSnapshotDirs[url] = tp.QdrantSnapshots
		}
		if t.Stores.Elasticsearch.Ownership == registry.OwnershipExclusive && t.Stores.Elasticsearch.Capabilities.Snapshot {
			url := t.Stores.Elasticsearch.URL
			opts.Indices[url] = []string{name + "-chunks"}
			opts.ESCounts[url+"/"+name+"-chunks"] = 34000
		}
	}
	fixtureRestoreTargets(f, &opts)
	return opts
}

// fixtureRestoreTargets pre-answers, for the port blocks a `restore --as` would
// ALLOCATE, the facts a tenant that has just been restored would present: its
// stores holding the counts the bundle recorded, and its own API reporting the
// collection inventory.
//
// It exists for the same reason fixtureListening does, and it is the same kind
// of accommodation. The fake stores keep no data: `Qdrant.Recover` and
// `_restore` move no points on this host, and the fake tenant API has no
// collection store to read back. Without this seed the fixture could only ever
// represent a FAILED restore — the verification step would ask the fresh
// tenant's stores for the bundle's counts and be told zero — and the verb could
// not be exercised end to end at all.
//
// What it does NOT fake is any of the work. The copies, the recover calls, the
// repository registration and the counts the step asks for are all real calls
// on this host, and they are asserted directly in
// go/internal/ctl/ops/restore_test.go, where the target's numbers are seeded per
// test rather than for every block.
func fixtureRestoreTargets(f *registry.Fleet, opts *drivers.FakeOptions) {
	next, _ := registry.Allocate(f)
	for _, t := range f.Tenants {
		if t.Supervisor != string(model.SupervisorSystemd) ||
			t.Stores.Qdrant.Ownership != registry.OwnershipExclusive ||
			!t.Stores.Qdrant.Capabilities.Snapshot {
			continue
		}
		collections := append([]string(nil), opts.Collections[t.Stores.Qdrant.URL]...)
		indices := append([]string(nil), opts.Indices[t.Stores.Elasticsearch.URL]...)
		sort.Strings(collections)
		for i := 0; i < fixtureFutureBlocks; i++ {
			block := paths.BlockAt(f.PortBase, f.PortStride, next+i)
			// `prospectiveTenant` builds the fresh tenant's URLs on 127.0.0.1;
			// the captured tenants spell theirs `localhost`. A seed under the
			// wrong spelling is a seed that answers nothing.
			qURL := fmt.Sprintf("http://127.0.0.1:%d", block.QdrantHTTP)
			esURL := fmt.Sprintf("http://127.0.0.1:%d", block.ESHTTP)
			for _, c := range collections {
				opts.QdrantCounts[qURL+"/"+c] = opts.QdrantCounts[t.Stores.Qdrant.URL+"/"+c]
			}
			for _, idx := range indices {
				opts.ESCounts[esURL+"/"+idx] = opts.ESCounts[t.Stores.Elasticsearch.URL+"/"+idx]
			}
			opts.CollectionsByOrigin[fmt.Sprintf("http://127.0.0.1:%d", block.API)] = collections
		}
	}
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
func (b *FakeBackend) Registry(context.Context) (*registry.Fleet, error) { return b.f(), nil }

func (b *FakeBackend) stamp() string { return b.now().UTC().Format(time.RFC3339) }

// order returns the tenants in display order, then anything display_order
// forgot, by name. It is fleet.Order itself, not a copy of its rule: a fake
// that ordered tenants its own way could hide an ordering bug in the real one,
// which is the one thing the fixture backend must never do.
func (b *FakeBackend) order() []*registry.Tenant { return fleet.Order(b.f()) }

// Fleet is the dashboard poll.
func (b *FakeBackend) Fleet(context.Context) (*model.FleetResponse, error) {
	rows := make([]model.FleetRow, 0, len(b.f().Tenants))
	for _, t := range b.order() {
		rows = append(rows, b.row(t))
	}
	return &model.FleetResponse{
		GeneratedAt:        b.stamp(),
		RegistryGeneration: b.f().Generation,
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
		StoresMode:   fleet.StoresMode(t.Stores),
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
		Units:     rowUnits(t),
		DiskBytes: fakeDisk(t.Name),
		// The bundle the backup verb recorded, if one has run against this
		// fixture: the projection is fleet.Row's, so what the dashboard shows
		// with fake drivers is what it shows on a host.
		LastBackup: fakeLastBackup(t),
	}
}

// fakeLastBackup projects the registry's backup record exactly as
// fleet.Row does.
func fakeLastBackup(t *registry.Tenant) *model.LastBackup {
	if t.LastBackup == nil {
		return nil
	}
	return &model.LastBackup{At: t.LastBackup.At, Fenced: t.LastBackup.Fenced, Verified: t.LastBackup.Verified}
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
	rows := make([]model.TenantResponse, 0, len(b.f().Tenants))
	for _, t := range b.order() {
		one, err := b.Tenant(ctx, t.Name, false)
		if err != nil {
			return nil, err
		}
		rows = append(rows, *one)
	}
	return &model.TenantsResponse{
		GeneratedAt:        b.stamp(),
		RegistryGeneration: b.f().Generation,
		Tenants:            rows,
	}, nil
}

// Tenant is one tenant. With viewer, the registry row is not assembled at all.
func (b *FakeBackend) Tenant(_ context.Context, name string, viewer bool) (*model.TenantResponse, error) {
	t, ok := b.f().Tenants[name]
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
	t, ok := b.f().Tenants[name]
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
	return &model.EnvResponse{Tenant: t.Name, EnvLayout: t.EnvLayout, Keys: keys, Source: model.EnvResponseSourceLive}, nil
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
	t, ok := b.f().Tenants[name]
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
		if _, ok := b.f().Tenants[tenant]; !ok {
			return nil, ErrNotFound
		}
	}
	findings := []model.Finding{{
		Level:  model.LevelInfo,
		Code:   "fake_drivers",
		Tenant: "",
		Detail: "the daemon is running with --fake-drivers: every host probe below is a recorded fixture, not this machine",
	}}
	if !b.f().Ctl.GatewayEnabled {
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
		Hash:        doctor.Hash(findings),
		GeneratedAt: b.stamp(),
		Scope:       model.Scope{Tenant: registry.NullString(tenant), Op: registry.NullString(op)},
		Findings:    findings,
	}, nil
}
