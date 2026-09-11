package fleet

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/hostfacts"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// probes returns the recorded host behind the four live tenants: the API and
// dedicated-store ports are up, the shared stores are somebody else's, and
// the ctl's own unit is not running (it is started by hand today).
func probes(t *testing.T) Probes {
	t.Helper()
	h := &hostfacts.Fake{
		Self: "wilke",
		Ports: []hostfacts.Listener{
			{Port: 24000, Pid: 1, User: "wilke"}, // lucid-next api
			{Port: 24003, Pid: 2, User: "wilke"}, // lucid-next ES
			{Port: 24020, Pid: 3, User: "wilke"}, // asm-next api
			{Port: 24040, Pid: 4, User: "wilke"}, // dev api
			{Port: 24041, Pid: 5, User: "wilke"}, // dev qdrant
			{Port: 24043, Pid: 6, User: "wilke"}, // dev ES
			{Port: 8090, Pid: 7, User: "wilke"},  // dev UI
			{Port: 5211, Pid: 8, User: "wilke"},  // lucid-next UI
			{Port: 5212, Pid: 9, User: "wilke"},  // asm-next UI
		},
		Lingering:   map[string]bool{},
		MaxMapCount: 262144,
		MemTotal:    128 << 30,
		Free:        map[string]int64{"/rag": 3 << 40},
		Groups:      map[string][]string{DefaultSudoersGroup: {"svcbvbrc", "wilke"}},
		ProbeStatus: map[string]int{
			"http://127.0.0.1:24000/health": 200,
			"http://127.0.0.1:24020/health": 200,
			"http://127.0.0.1:24040/health": 200,
			"http://127.0.0.1:24060/health": 503, // demo API is up but unhealthy
			"http://localhost:24041/":       200,
			"http://localhost:24043/":       200,
			"http://localhost:24003/":       200,
		},
		DU: map[string]int64{
			"/rag/data/tenants/dev":   12 << 30,
			"/rag/data/tenants/demo":  3 << 30,
			"/rag/data/tenants/lucid": 900 << 20,
			"/rag/data/tenants/asm":   5 << 30,
		},
	}
	return Probes{
		Host: h, Prober: h, Disk: h,
		Now: func() time.Time { return time.Date(2026, 9, 11, 8, 0, 0, 0, time.UTC) },
	}
}

func build(t *testing.T) (*model.FleetResponse, map[string]model.FleetRow) {
	t.Helper()
	f := registry.LiveFixture()
	f.Generation = 3
	resp := Build(context.Background(), paths.NewRoots("/rag", paths.Overrides{}), f, probes(t))
	rows := map[string]model.FleetRow{}
	for _, r := range resp.Tenants {
		rows[r.Name] = r
	}
	return resp, rows
}

// TestBuildOrdersByDisplayOrder: the dashboard renders the order the gateway
// advertises, not the map's iteration order.
func TestBuildOrdersByDisplayOrder(t *testing.T) {
	resp, _ := build(t)
	var got []string
	for _, r := range resp.Tenants {
		got = append(got, r.Name)
	}
	want := []string{"dev", "demo", "lucid-next", "asm-next"}
	for i := range want {
		if i >= len(got) || got[i] != want[i] {
			t.Fatalf("order = %v, want %v", got, want)
		}
	}
	if resp.RegistryGeneration != 3 {
		t.Errorf("registry_generation = %d, want 3", resp.RegistryGeneration)
	}
}

// TestStoresModeAndPorts: the three ownership combinations the live fleet
// actually has, and the ports a row shows for a shared store.
func TestStoresModeAndPorts(t *testing.T) {
	_, rows := build(t)
	cases := map[string]struct {
		mode               model.StoresMode
		qdrantPort, esPort int
	}{
		"dev":        {model.StoresDedicated, 24041, 24043},
		"demo":       {model.StoresShared, 6333, 9200},
		"lucid-next": {model.StoresMixed, 6343, 24003},
		"asm-next":   {model.StoresShared, 6333, 9200},
	}
	for name, c := range cases {
		r := rows[name]
		if r.StoresMode != c.mode {
			t.Errorf("%s stores_mode = %q, want %q", name, r.StoresMode, c.mode)
		}
		if r.Ports.QdrantHTTP != c.qdrantPort || r.Ports.ESHTTP != c.esPort {
			t.Errorf("%s ports = %+v, want qdrant %d / es %d", name, r.Ports, c.qdrantPort, c.esPort)
		}
	}
}

// TestHealthColumns: a 200 is ok, a 503 is degraded, no answer is down, a
// shared store is n/a, and deep health is n/a in PR-A because no read
// endpoint may use a tenant's admin key yet.
func TestHealthColumns(t *testing.T) {
	_, rows := build(t)
	if h := rows["dev"].Health; h.API != model.HealthOK || h.Qdrant != model.HealthOK || h.ES != model.HealthOK {
		t.Errorf("dev health = %+v, want all ok", h)
	}
	if h := rows["demo"].Health; h.API != model.HealthDegraded {
		t.Errorf("demo api health = %q, want degraded (503)", h.API)
	}
	if h := rows["demo"].Health; h.Qdrant != model.HealthNA || h.ES != model.HealthNA {
		t.Errorf("demo store health = %+v, want n/a for shared stores", h)
	}
	if h := rows["lucid-next"].Health; h.Qdrant != model.HealthNA || h.ES != model.HealthOK {
		t.Errorf("lucid-next health = %+v, want n/a qdrant and ok ES", h)
	}
	for name, r := range rows {
		if r.Health.Deep != model.HealthNA {
			t.Errorf("%s deep health = %q, want n/a in PR-A", name, r.Health.Deep)
		}
	}
}

// TestUnitsForManualTenants: a hand-started tenant has no target, its API and
// UI states come from the listeners, and a shared store is n/a.
func TestUnitsForManualTenants(t *testing.T) {
	_, rows := build(t)
	dev := rows["dev"].Units
	if dev.Target != model.UnitNA {
		t.Errorf("dev target = %q, want n/a (no units yet)", dev.Target)
	}
	if dev.API != model.UnitActive || dev.UI != model.UnitActive || dev.Qdrant != model.UnitActive || dev.ES != model.UnitActive {
		t.Errorf("dev units = %+v, want everything active", dev)
	}
	demo := rows["demo"].Units
	if demo.API != model.UnitInactive {
		t.Errorf("demo api unit = %q, want inactive (nothing listens on 24060)", demo.API)
	}
	if demo.Qdrant != model.UnitNA || demo.ES != model.UnitNA {
		t.Errorf("demo store units = %+v, want n/a for shared stores", demo)
	}
}

// TestUnitsForSystemdTenants asks the user manager and maps its ActiveState;
// a manager the ctl may not query is n/a, never a guess.
func TestUnitsForSystemdTenants(t *testing.T) {
	f := registry.LiveFixture()
	dev := f.Tenants["dev"]
	dev.Supervisor = "systemd"
	dev.Owner = "svcbvbrc"
	p := probes(t)
	h := p.Host.(*hostfacts.Fake)
	h.Units = map[string]hostfacts.UnitStatus{
		"svcbvbrc|ragstack-dev.target":         {ActiveState: "active"},
		"svcbvbrc|ragstack-dev-api.service":    {ActiveState: "failed"},
		"svcbvbrc|ragstack-dev-qdrant.service": {ActiveState: "activating"},
		// -es and -ui are deliberately unrecorded: the manager did not answer.
	}
	resp := Build(context.Background(), paths.NewRoots("/rag", paths.Overrides{}), f, p)
	var row model.FleetRow
	for _, r := range resp.Tenants {
		if r.Name == "dev" {
			row = r
		}
	}
	if row.Units.Target != model.UnitActive || row.Units.API != model.UnitFailed || row.Units.Qdrant != model.UnitActivating {
		t.Errorf("units = %+v", row.Units)
	}
	if row.Units.ES != model.UnitNA || row.Units.UI != model.UnitNA {
		t.Errorf("an unanswerable unit query must be n/a, got %+v", row.Units)
	}
}

// TestHostBand: the facts the dashboard band shows, including a ctl unit that
// is not active because the daemon was started by hand.
func TestHostBand(t *testing.T) {
	resp, _ := build(t)
	h := resp.Host
	if h.DiskFreeBytes != 3<<40 || h.VMMaxMapCount != 262144 {
		t.Errorf("host = %+v", h)
	}
	if h.Linger {
		t.Error("linger is not enabled on this host yet")
	}
	if h.CtlUnitActive {
		t.Error("the ctl unit does not exist yet and must not read as active")
	}
	if len(h.SudoersGroup) != 2 {
		t.Errorf("sudoers_group = %v", h.SudoersGroup)
	}
}

// TestDiskAndBackupColumns: du is cached per data dir, and a tenant with no
// backup carries null rather than a zero row.
func TestDiskAndBackupColumns(t *testing.T) {
	_, rows := build(t)
	if rows["dev"].DiskBytes != 12<<30 {
		t.Errorf("dev disk_bytes = %d", rows["dev"].DiskBytes)
	}
	for name, r := range rows {
		if r.LastBackup != nil {
			t.Errorf("%s last_backup = %+v, want null: the ctl has made no backup", name, r.LastBackup)
		}
	}
}

// TestFleetResponseValidatesAgainstContract runs the built body through the
// schema, which is what the conformance suite will do over HTTP.
func TestFleetResponseValidatesAgainstContract(t *testing.T) {
	schemas := "../../../../contracts/ctl/schemas"
	if _, err := os.Stat(schemas); err != nil {
		t.Skip("contract schemas not present")
	}
	py := ""
	for _, c := range []string{"/rag/envs/ragstack/bin/python", "python3"} {
		if p, err := exec.LookPath(c); err == nil {
			if exec.Command(p, "-c", "import jsonschema, referencing").Run() == nil {
				py = p
				break
			}
		}
	}
	if py == "" {
		t.Skip("no python with jsonschema + referencing")
	}
	resp, _ := build(t)
	b, err := json.MarshalIndent(resp, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	doc := filepath.Join(t.TempDir(), "fleet.json")
	if err := os.WriteFile(doc, b, 0o600); err != nil {
		t.Fatal(err)
	}
	script := `
import glob, json, os, sys
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
reg, schemas = Registry(), {}
for p in glob.glob(os.path.join(sys.argv[1], "*.json")):
    s = json.load(open(p)); schemas[os.path.basename(p)[:-5]] = s
    reg = reg.with_resource(s["$id"], Resource.from_contents(s))
errs = sorted(Draft202012Validator(schemas["fleet_response"], registry=reg).iter_errors(json.load(open(sys.argv[2]))),
              key=lambda e: list(e.absolute_path))
for e in errs[:20]:
    print("/" + "/".join(str(p) for p in e.absolute_path), "->", e.message[:200])
sys.exit(1 if errs else 0)
`
	if out, err := exec.Command(py, "-c", script, schemas, doc).CombinedOutput(); err != nil {
		t.Fatalf("fleet response does not validate:\n%s\n%s", out, b)
	}
	// The fleet view must never name a secret, at any depth.
	var tree any
	if err := json.Unmarshal(b, &tree); err != nil {
		t.Fatal(err)
	}
	assertNoSecretNames(t, tree, "")
}

func assertNoSecretNames(t *testing.T, v any, path string) {
	t.Helper()
	switch node := v.(type) {
	case map[string]any:
		for k, child := range node {
			for _, bad := range []string{"api_key", "password", "secret", "token", "dsn"} {
				if strings.Contains(strings.ToLower(k), bad) {
					t.Errorf("%s/%s: the fleet view names a secret", path, k)
				}
			}
			assertNoSecretNames(t, child, path+"/"+k)
		}
	case []any:
		for i, child := range node {
			assertNoSecretNames(t, child, path+"/"+strconv.Itoa(i))
		}
	}
}

// TestTenantViewShapes: the operator/viewer split, the live status from the
// listeners, the manual-process service rows, and the drift computed now.
func TestTenantViewShapes(t *testing.T) {
	f := registry.LiveFixture()
	dev := f.Tenants["dev"]
	p := probes(t)
	h := p.Host.(*hostfacts.Fake)
	h.Describes = map[string]string{dev.Worktree: "v1.6.0"}
	roots := paths.NewRoots("/rag", paths.Overrides{})

	operator := TenantView(context.Background(), roots, dev, p, true)
	if operator.Registry == nil {
		t.Fatal("an operator receives the registry row")
	}
	viewer := TenantView(context.Background(), roots, dev, p, false)
	if viewer.Registry != nil {
		t.Fatal("a viewer must receive registry: null")
	}
	st := operator.Status
	if st.APIPid != 4 || st.APIPidOwner != "wilke" {
		t.Errorf("status = %+v, want the recorded pid and owner", st)
	}
	if !st.Listening.API || !st.Listening.QdrantHTTP || !st.Listening.ESHTTP || !st.Listening.UI {
		t.Errorf("listening = %+v, want all four up", st.Listening)
	}
	if len(st.RunningJobs) != 0 {
		t.Errorf("running_jobs = %v, want empty: PR-A has no job engine", st.RunningJobs)
	}
	if operator.Units.Supervisor != model.SupervisorManual || operator.Units.Target != nil {
		t.Errorf("units = %+v, want a manual tenant with no target", operator.Units)
	}
	kinds := map[string]model.Service{}
	for _, s := range operator.Units.Services {
		kinds[s.Kind] = s
	}
	if len(kinds) != 4 {
		t.Errorf("services = %+v, want api/ui/qdrant/es", operator.Units.Services)
	}
	if kinds["api"].Name != "manual:api" || kinds["api"].MainPID != 4 {
		t.Errorf("api service = %+v", kinds["api"])
	}
	var codeDrift bool
	for _, d := range operator.Drift {
		if d.Code == "code_tag" && d.Expected == "untracked" && d.Actual == "v1.6.0" {
			codeDrift = true
		}
	}
	if !codeDrift {
		t.Errorf("drift = %+v, want the tag disagreement computed now", operator.Drift)
	}

	// A shared-store tenant lists only the legs it runs.
	demo := TenantView(context.Background(), roots, f.Tenants["demo"], p, true)
	for _, s := range demo.Units.Services {
		if s.Kind == "qdrant" || s.Kind == "es" {
			t.Errorf("demo lists a shared store as its own service: %+v", s)
		}
	}
	var stateDrift bool
	for _, d := range demo.Drift {
		if d.Code == "state" {
			stateDrift = true
		}
	}
	if !stateDrift {
		t.Error("demo is recorded active but nothing listens: that is drift")
	}
}

// TestTenantsViewIsOrderedAndCounted mirrors GET /v1/tenants.
func TestTenantsViewIsOrderedAndCounted(t *testing.T) {
	f := registry.LiveFixture()
	resp := TenantsView(context.Background(), paths.NewRoots("/rag", paths.Overrides{}), f, probes(t), false)
	if len(resp.Tenants) != 4 {
		t.Fatalf("%d tenants, want 4", len(resp.Tenants))
	}
	if resp.Tenants[0].Summary.Name != "dev" || resp.Tenants[3].Summary.Name != "asm-next" {
		t.Errorf("order = %s … %s", resp.Tenants[0].Summary.Name, resp.Tenants[3].Summary.Name)
	}
	for _, row := range resp.Tenants {
		if row.Registry != nil {
			t.Errorf("%s: a viewer list must carry registry: null", row.Summary.Name)
		}
	}
}

// recordingProber answers every probe 200 and remembers what it was asked to
// dial, so a test can assert that a refused URL was never requested at all —
// "it returned down" is not the same guarantee as "no packet left the host".
// It is mutex-guarded because Build probes the tenants concurrently: the
// recorder is the only mutable thing a fleet build touches.
type recordingProber struct {
	hostfacts.Prober
	mu    sync.Mutex
	asked []string
}

func (p *recordingProber) Probe(_ context.Context, url string) (int, error) {
	p.mu.Lock()
	p.asked = append(p.asked, url)
	p.mu.Unlock()
	return 200, nil
}

// Asked returns a copy of the URLs this prober was handed.
func (p *recordingProber) Asked() []string {
	p.mu.Lock()
	defer p.mu.Unlock()
	return append([]string(nil), p.asked...)
}

// TestHealthNeverProbesADisallowedURL is S10. `stores.*.url` is a registry
// value that adopt copied out of a tenant.env an operator wrote, so probing it
// unchecked made the dashboard poll a GET-anything proxy. Two shapes matter:
// an off-host address (plain SSRF) and a loopback URL carrying userinfo, which
// passed every other test while putting a credential on the wire.
func TestHealthNeverProbesADisallowedURL(t *testing.T) {
	for name, url := range map[string]string{
		"off-host":     "http://169.254.169.254:24041",
		"userinfo":     "http://u:p@localhost:24041",
		"https":        "https://localhost:24041",
		"other tenant": "http://localhost:24061",
	} {
		t.Run(name, func(t *testing.T) {
			f := registry.LiveFixture()
			dev := f.Tenants["dev"]
			dev.Stores.Qdrant.URL = url
			rec := &recordingProber{}
			p := probes(t)
			p.Prober = rec

			resp := Build(context.Background(), paths.NewRoots("/rag", paths.Overrides{}), f, p)
			var row model.FleetRow
			for _, r := range resp.Tenants {
				if r.Name == "dev" {
					row = r
				}
			}
			if row.Health.Qdrant != model.HealthUnknown {
				t.Errorf("qdrant health = %q, want %q (nothing was asked, so nothing is known)",
					row.Health.Qdrant, model.HealthUnknown)
			}
			for _, asked := range rec.Asked() {
				if strings.Contains(asked, "169.254.169.254") || strings.Contains(asked, "u:p@") ||
					strings.HasPrefix(asked, "https://") || strings.Contains(asked, ":24061") {
					t.Errorf("a refused URL was dialled anyway: %q", asked)
				}
			}
		})
	}
}

// TestHealthStillProbesAllowedURLs: the gate must not have turned the whole
// health column off.
func TestHealthStillProbesAllowedURLs(t *testing.T) {
	f := registry.LiveFixture()
	rec := &recordingProber{}
	p := probes(t)
	p.Prober = rec
	Build(context.Background(), paths.NewRoots("/rag", paths.Overrides{}), f, p)

	want := []string{
		"http://127.0.0.1:24040/health", // dev api, in its own block
		"http://localhost:24041/",       // dev qdrant, exclusive, own block
		"http://localhost:24003/",       // lucid-next ES, exclusive, own block
	}
	for _, w := range want {
		found := false
		for _, asked := range rec.Asked() {
			if asked == w {
				found = true
			}
		}
		if !found {
			t.Errorf("%s was never probed; asked: %v", w, rec.Asked())
		}
	}
}

// slowProber answers every probe after a fixed delay and counts how many were
// in flight at once.
type slowProber struct {
	delay time.Duration
	mu    sync.Mutex
	cur   int
	max   int
}

func (s *slowProber) Probe(ctx context.Context, url string) (int, error) {
	s.mu.Lock()
	s.cur++
	if s.cur > s.max {
		s.max = s.cur
	}
	s.mu.Unlock()
	select {
	case <-time.After(s.delay):
	case <-ctx.Done():
	}
	s.mu.Lock()
	s.cur--
	s.mu.Unlock()
	return 200, nil
}

// The listing legs are not part of the fleet view; they exist only to satisfy
// the interface.
func (s *slowProber) QdrantCollections(context.Context, string) (hostfacts.StoreListing, error) {
	return hostfacts.StoreListing{}, nil
}

func (s *slowProber) ESIndices(context.Context, string) (hostfacts.StoreListing, error) {
	return hostfacts.StoreListing{}, nil
}

// TestBuildProbesTenantsConcurrently is item 12 of the PR-A review.
//
// Every leg of a row is a bounded, read-only probe, and they ran strictly in
// series — so the dashboard's 15 s poll cost the SUM over the fleet, and four
// tenants with an unreachable store meant four consecutive timeouts before
// the first row was complete. The probes hold no shared mutable state, so the
// fan-out is free; what it must not change is the ORDER, which is display
// order and is what /v1/tenants returns.
func TestBuildProbesTenantsConcurrently(t *testing.T) {
	const delay = 120 * time.Millisecond
	p := probes(t)
	sp := &slowProber{delay: delay}
	p.Prober = sp

	// Every store shared, so each tenant costs exactly one probe (its API):
	// four in series is 4×delay, four at once is one.
	f := registry.LiveFixture()
	for _, tn := range f.Tenants {
		tn.Stores.Qdrant.Ownership = registry.OwnershipShared
		tn.Stores.Elasticsearch.Ownership = registry.OwnershipShared
	}
	start := time.Now()
	resp := Build(context.Background(), paths.NewRoots("/rag", paths.Overrides{}), f, p)
	elapsed := time.Since(start)

	if len(resp.Tenants) != 4 {
		t.Fatalf("%d rows, want the fixture's 4", len(resp.Tenants))
	}
	if elapsed >= 3*delay {
		t.Errorf("Build took %v for 4 tenants at %v a probe each — the fan-out is not happening", elapsed, delay)
	}
	if sp.max < 2 {
		t.Errorf("max concurrent probes = %d: the tenants were probed in series", sp.max)
	}
	// Display order survives the fan-out (the rows are written into their own
	// slots, not appended by whichever goroutine finishes first).
	var got []string
	for _, r := range resp.Tenants {
		got = append(got, r.Name)
	}
	if strings.Join(got, ",") != strings.Join(f.DisplayOrder, ",") {
		t.Errorf("order = %v, want display order %v", got, f.DisplayOrder)
	}
	// An empty fleet still renders as [] rather than null.
	empty := Build(context.Background(), paths.NewRoots("/rag", paths.Overrides{}), registry.NewFleet("/rag"), probes(t))
	if empty.Tenants == nil {
		t.Error("an empty fleet must marshal as [], not null")
	}
}
