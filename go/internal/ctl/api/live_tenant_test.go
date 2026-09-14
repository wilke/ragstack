package api

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/auth"
	"github.com/ragstack/ragstack/internal/ctl/hostfacts"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// --------------------------------------------------------------------------
// The live backend's per-tenant reads — the fixture fall-through
// --------------------------------------------------------------------------
//
// liveBackend embedded *FakeBackend and implemented Fleet/Logs/Doctor only, so
// GET /v1/tenants/{name} on the REAL daemon was answered by fake.go: listening
// was `{api: t.State == "active", qdrant_http: false, es_http: false, ui:
// false}` and every unit row was `n/a`, regardless of the host. The same
// daemon's /v1/fleet — a method the live backend did own — showed those units
// active at the same instant, and the CLI's `tenant show` (fleet.TenantView)
// showed all four ports listening. Three answers, one host.
//
// dev is the fixture tenant with both stores exclusive: API 24040, qdrant
// 24041, ES 24043 (paths.Block(2)) and UI 8090.
const (
	devAPIPort    = 24040
	devQdrantPort = 24041
	devESPort     = 24043
	devUIPort     = 8090
)

// devHost is a host on which every one of dev's four ports is listening, with
// the store legs owned by a process this account cannot see (Pid 0) — the
// normal case on coconut, where the stores run as `wilke` and the ctl as
// `svcbvbrc`, and the case a listener check must not read as "down".
func devHost(ports ...int) *hostfacts.Fake {
	h := &hostfacts.Fake{
		Self:        "svcbvbrc",
		MaxMapCount: 262144,
		MemTotal:    128 << 30,
		Free:        map[string]int64{"/": 3 << 40},
		Groups:      map[string][]string{"seed-admins-svcbvbrc": {"svcbvbrc", "wilke"}},
		ProbeStatus: map[string]int{
			"http://127.0.0.1:24040/health": 200,
			"http://localhost:24041/":       200,
			"http://localhost:24043/":       200,
		},
	}
	for _, p := range ports {
		l := hostfacts.Listener{Port: p}
		if p == devAPIPort {
			l.Pid, l.User = 4242, "wilke"
		}
		h.Ports = append(h.Ports, l)
	}
	return h
}

// liveBackendOverFixture is a LIVE backend (not the fixture one) whose
// registry is the coconut fixture written to a temp file and whose probes are
// the recorded host facts h. It is the only seam the bug needed: the backend
// builds its probes once in newLiveBackend, so a test substitutes them the
// same way TestLiveBackendReusesOneSetOfProbes does.
func liveBackendOverFixture(t *testing.T, h *hostfacts.Fake) *liveBackend {
	t.Helper()
	dir := t.TempDir()
	f := registry.LiveFixture()
	f.UpdatedAt, f.UpdatedBy = "2026-09-11T08:00:00Z", "test"
	// The tenant's config lives under the temp root, so the env read has real
	// files to classify rather than the host's.
	for _, tn := range f.Tenants {
		tn.DataDir = filepath.Join(dir, "data", "tenants", tn.ManifestName)
	}
	raw, err := json.Marshal(f)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(dir, "registry.json")
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	b, err := newLiveBackendWithLogger(dir, path, slog.New(slog.NewTextHandler(&bytes.Buffer{}, nil)))
	if err != nil {
		t.Fatalf("newLiveBackend: %v", err)
	}
	b.probes.Host, b.probes.Prober, b.probes.Disk = h, h, h
	b.probes.Now = func() time.Time { return time.Date(2026, 9, 11, 8, 0, 0, 0, time.UTC) }
	return b
}

func liveServer(t *testing.T, h *hostfacts.Fake) http.Handler {
	t.Helper()
	return newTestServerWith(t, liveBackendOverFixture(t, h))
}

// obj is one JSON object member, or a fatal error naming the path that was
// missing — a nil map deref in a table-driven assertion says nothing.
func obj(t *testing.T, m map[string]any, key string) map[string]any {
	t.Helper()
	v, ok := m[key].(map[string]any)
	if !ok {
		t.Fatalf("%q is not an object: %#v", key, m[key])
	}
	return v
}

// serviceRow returns the units.services row for one leg.
func serviceRow(t *testing.T, body map[string]any, kind string) map[string]any {
	t.Helper()
	units := obj(t, body, "units")
	rows, ok := units["services"].([]any)
	if !ok {
		t.Fatalf("units.services is not a list: %#v", units["services"])
	}
	for _, r := range rows {
		row, _ := r.(map[string]any)
		if row["kind"] == kind {
			return row
		}
	}
	t.Fatalf("units.services has no %q row: %#v", kind, rows)
	return nil
}

// TestLiveTenantShowReportsTheHostNotTheFixture is the reported bug.
//
// Every one of dev's four ports is listening; the live daemon must say so on
// GET /v1/tenants/dev, and must report the store units by the evidence a
// hand-started process leaves (a listener), not as the fixture's flat `n/a`.
func TestLiveTenantShowReportsTheHostNotTheFixture(t *testing.T) {
	h := liveServer(t, devHost(devAPIPort, devQdrantPort, devESPort, devUIPort))
	w := asOperator(t, h, http.MethodGet, "/v1/tenants/dev")
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", w.Code, w.Body.String())
	}
	body := decode(t, w)
	listening := obj(t, obj(t, body, "status"), "listening")
	for _, leg := range []string{"api", "qdrant_http", "es_http", "ui"} {
		if listening[leg] != true {
			// The fixture's constants are exactly false/false/false here.
			t.Errorf("status.listening.%s = %v, want true (the port IS listening; "+
				"false for all three store legs is fake.go's constant)", leg, listening[leg])
		}
	}
	for _, kind := range []string{"api", "ui", "qdrant", "es"} {
		row := serviceRow(t, body, kind)
		if row["active_state"] != string(model.UnitActive) {
			t.Errorf("units.services[%s].active_state = %v, want %q (%q is the fixture's answer for every leg)",
				kind, row["active_state"], model.UnitActive, model.UnitNA)
		}
	}
	// The API's owning pid is attributable; the store legs' is not, and a row
	// that invented one would cost a debugging session.
	status := obj(t, body, "status")
	if status["api_pid"] != float64(4242) {
		t.Errorf("status.api_pid = %v, want 4242", status["api_pid"])
	}
	if status["api_pid_owner"] != "wilke" {
		t.Errorf("status.api_pid_owner = %v, want wilke", status["api_pid_owner"])
	}
}

// TestLiveTenantListeningFollowsTheListener: the flags are a PROBE, not a
// constant either way. With qdrant up and ES down the two must disagree.
func TestLiveTenantListeningFollowsTheListener(t *testing.T) {
	b := liveBackendOverFixture(t, devHost(devAPIPort, devQdrantPort))
	view, err := b.Tenant(context.Background(), "dev", false)
	if err != nil {
		t.Fatal(err)
	}
	if !view.Status.Listening.QdrantHTTP {
		t.Error("listening.qdrant_http = false while :24041 is listening")
	}
	if view.Status.Listening.ESHTTP {
		t.Error("listening.es_http = true while nothing is listening on :24043")
	}
	if view.Status.Listening.UI {
		t.Error("listening.ui = true while nothing is listening on :8090")
	}
	// The unit rows follow the same evidence.
	states := map[string]model.UnitState{}
	for _, s := range view.Units.Services {
		states[s.Kind] = s.ActiveState
	}
	if states["qdrant"] != model.UnitActive || states["es"] != model.UnitInactive {
		t.Errorf("units qdrant=%q es=%q, want active/inactive", states["qdrant"], states["es"])
	}
}

// TestLiveTenantsListIsLiveToo: /v1/tenants is the same builder, sharing one
// /proc scan. A list that disagreed with the show of the same tenant would be
// the original bug in its second half.
func TestLiveTenantsListIsLiveToo(t *testing.T) {
	b := liveBackendOverFixture(t, devHost(devAPIPort, devQdrantPort, devESPort, devUIPort))
	list, err := b.Tenants(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	var dev *model.TenantResponse
	for i := range list.Tenants {
		if list.Tenants[i].Summary.Name == "dev" {
			dev = &list.Tenants[i]
		}
	}
	if dev == nil {
		t.Fatalf("no dev row in %d tenants", len(list.Tenants))
	}
	if !dev.Status.Listening.QdrantHTTP || !dev.Status.Listening.ESHTTP {
		t.Errorf("the list disagrees with the host: %+v", dev.Status.Listening)
	}
	// The list is the OPERATOR shape; the handler reduces it.
	if dev.Registry == nil {
		t.Error("Tenants() dropped the registry row; the viewer reduction is the handler's job")
	}
}

// TestLiveTenantViewerReductionMatchesTheFake: the contract's viewer_fields
// for ctlTenantShow/ctlTenantsList is "summary, status, units, drift;
// registry is null", and the two backends must reduce identically — a
// conformance suite that only ever runs the fake cannot see a live backend
// that leaks the row.
func TestLiveTenantViewerReductionMatchesTheFake(t *testing.T) {
	backends := map[string]http.Handler{
		"live": liveServer(t, devHost(devAPIPort, devQdrantPort, devESPort, devUIPort)),
		"fake": newTestServer(t),
	}
	for name, h := range backends {
		for _, path := range []string{"/v1/tenants/dev", "/v1/tenants"} {
			w := do(t, h, http.MethodGet, path, map[string]string{auth.HeaderAPIKey: viewerKey}, "")
			if w.Code != http.StatusOK {
				t.Fatalf("%s %s: status = %d: %s", name, path, w.Code, w.Body.String())
			}
			for _, row := range tenantRows(t, w, path) {
				if row["registry"] != nil {
					t.Errorf("%s %s: a viewer received the registry row", name, path)
				}
				for _, field := range []string{"summary", "status", "units", "drift"} {
					if _, ok := row[field]; !ok {
						t.Errorf("%s %s: a viewer lost %q", name, path, field)
					}
				}
			}
			// The operator keeps it, on both backends.
			ow := asOperator(t, h, http.MethodGet, path)
			for _, row := range tenantRows(t, ow, path) {
				if row["registry"] == nil {
					t.Errorf("%s %s: an operator lost the registry row", name, path)
				}
			}
		}
	}
}

// tenantRows normalises the show body and the list body to a list of rows.
func tenantRows(t *testing.T, w *httptest.ResponseRecorder, path string) []map[string]any {
	t.Helper()
	body := decode(t, w)
	if path != "/v1/tenants" {
		return []map[string]any{body}
	}
	raw, ok := body["tenants"].([]any)
	if !ok {
		t.Fatalf("tenants is not a list: %#v", body["tenants"])
	}
	out := make([]map[string]any, 0, len(raw))
	for _, r := range raw {
		row, _ := r.(map[string]any)
		out = append(out, row)
	}
	return out
}

// TestLiveTenantUnknownIs404: the same error the fixture backend raises, so
// the contract's 404 `not_found` does not depend on which backend answered.
func TestLiveTenantUnknownIs404(t *testing.T) {
	h := liveServer(t, devHost(devAPIPort))
	for _, path := range []string{"/v1/tenants/nosuch", "/v1/tenants/nosuch/env"} {
		assertError(t, asOperator(t, h, http.MethodGet, path), http.StatusNotFound, string(model.CodeNotFound))
	}
	b := liveBackendOverFixture(t, devHost(devAPIPort))
	if _, err := b.Tenant(context.Background(), "nosuch", false); err != ErrNotFound {
		t.Errorf("Tenant(unknown) = %v, want ErrNotFound", err)
	}
	if _, err := b.Env(context.Background(), "nosuch"); err != ErrNotFound {
		t.Errorf("Env(unknown) = %v, want ErrNotFound", err)
	}
}

// TestLiveEnvReadsTheTenantsFiles: env_response.json says "every key found in
// tenant.env, secrets.env and provision.env". The fixture backend INVENTS
// that list from the registry row; over real drivers an operator was shown
// keys the tenant does not have, and not shown the ones it does.
func TestLiveEnvReadsTheTenantsFiles(t *testing.T) {
	b := liveBackendOverFixture(t, devHost(devAPIPort))
	f, err := b.reloadRegistry()
	if err != nil {
		t.Fatal(err)
	}
	cfg := filepath.Join(f.Tenants["dev"].DataDir, "config")
	if err := os.MkdirAll(cfg, 0o700); err != nil {
		t.Fatal(err)
	}
	write := func(name, content string) {
		if err := os.WriteFile(filepath.Join(cfg, name), []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	write("tenant.env", "LOG_LEVEL=DEBUG\nRERANK_ENABLED=true\n")
	write("secrets.env", "API_KEYS='[\"0f1e2d3c4b5a69788796a5b4c3d2e1f00112233445566778899aabbccddeeff0\"]'\n")

	resp, err := b.Env(context.Background(), "dev")
	if err != nil {
		t.Fatal(err)
	}
	got := map[string]model.EnvKey{}
	for _, k := range resp.Keys {
		got[k.Key] = k
	}
	if len(got) != 3 {
		t.Fatalf("keys = %v, want exactly the three the files define", got)
	}
	if got["LOG_LEVEL"].ValueRedacted != "DEBUG" || got["LOG_LEVEL"].Source != model.SourceTenantEnv {
		t.Errorf("LOG_LEVEL = %+v, want DEBUG from tenant.env", got["LOG_LEVEL"])
	}
	// A secret's value has no path out of the process, on either backend.
	secret := got["API_KEYS"]
	if secret.Source != model.SourceSecretsEnv || secret.ValueRedacted != model.EnvRedacted {
		t.Errorf("API_KEYS = %+v, want <redacted> from secrets.env", secret)
	}
	for _, k := range resp.Keys {
		if strings.Contains(k.ValueRedacted, "0f1e2d3c") {
			t.Fatalf("the env read returned a secret value: %+v", k)
		}
	}
	// EMBEDDING_MODEL is in fakePublicEnv and in no file here: a key the
	// tenant does not have must not be reported as one it has.
	if _, ok := got["EMBEDDING_MODEL"]; ok {
		t.Error("the live env read answered with the fixture's invented keys")
	}
}

// TestLiveBackendEmbedsNoFixtureBackend is the regression guard for the shape
// of the bug rather than one of its symptoms.
//
// The fall-through was structural: *FakeBackend was embedded, so every method
// the live type did not write existed anyway and served fixture data. With no
// embedded backend the compiler is the guard — an unimplemented Backend method
// fails `var _ Backend = (*liveBackend)(nil)` in live.go — and this test keeps
// the embedding from coming back as a convenience.
func TestLiveBackendEmbedsNoFixtureBackend(t *testing.T) {
	ty := reflect.TypeOf(liveBackend{})
	for i := 0; i < ty.NumField(); i++ {
		if f := ty.Field(i); f.Anonymous {
			t.Errorf("liveBackend embeds %s: a Backend method nobody wrote would resolve to it "+
				"and answer a real-driver caller with %s's data", f.Type, f.Type)
		}
	}
	// Every Backend method is declared on *liveBackend itself.
	live, backend := reflect.TypeOf(&liveBackend{}), reflect.TypeOf((*Backend)(nil)).Elem()
	for i := 0; i < backend.NumMethod(); i++ {
		name := backend.Method(i).Name
		if _, ok := live.MethodByName(name); !ok {
			t.Errorf("liveBackend does not implement %s", name)
		}
	}
}
