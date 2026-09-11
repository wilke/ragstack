package api

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"regexp"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/auth"
	"github.com/ragstack/ragstack/internal/ctl/authz"
	"github.com/ragstack/ragstack/internal/ctl/ratelimit"
	"github.com/ragstack/ragstack/internal/ctl/session"
)

const (
	opKey     = "a0f1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f"
	viewerKey = "b1e2d3c4b5a69788796a5b4c3d2e1f00112233445566778899aabbccddeeff00"
)

var ridRE = regexp.MustCompile(`^[0-9a-f]{16}$`)

func newTestServer(t *testing.T) http.Handler {
	t.Helper()
	envs := map[string]string{
		auth.EnvAPIKeys:     `["` + opKey + `","` + viewerKey + `"]`,
		auth.EnvAPIKeyRoles: `{"` + opKey + `":"operator","` + viewerKey + `":"viewer"}`,
		auth.EnvAPIKeyNames: `{"` + opKey + `":"ops","` + viewerKey + `":"watcher"}`,
	}
	keys, err := auth.LoadKeys(func(k string) string { return envs[k] })
	if err != nil {
		t.Fatal(err)
	}
	verifier, err := auth.New(auth.Options{})
	if err != nil {
		t.Fatal(err)
	}
	sessions := session.NewMemoryStore()
	return NewRouter(&Server{
		Backend: NewFakeBackend(),
		Resolver: &auth.Resolver{
			Keys: keys, Verifier: verifier, Sessions: sessions,
			// Off for the router tests: they deliberately produce dozens of
			// 401s and 403s, which is exactly what the limiter is for.
			Limiter: ratelimit.New(ratelimit.Config{PerCredential: -1, TarpitAt: -1}),
			Reject:  reject,
		},
		Sessions: sessions,
	})
}

func do(t *testing.T, h http.Handler, method, path string, headers map[string]string, body string) *httptest.ResponseRecorder {
	t.Helper()
	var r *http.Request
	if body == "" {
		r = httptest.NewRequest(method, path, nil)
	} else {
		r = httptest.NewRequest(method, path, strings.NewReader(body))
		r.Header.Set("Content-Type", "application/json")
	}
	for k, v := range headers {
		r.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	return w
}

func asOperator(t *testing.T, h http.Handler, method, path string) *httptest.ResponseRecorder {
	return do(t, h, method, path, map[string]string{auth.HeaderAPIKey: opKey}, "")
}

func decode(t *testing.T, w *httptest.ResponseRecorder) map[string]any {
	t.Helper()
	var body map[string]any
	raw, _ := io.ReadAll(w.Body)
	if err := json.Unmarshal(raw, &body); err != nil {
		t.Fatalf("body is not a JSON object (%d): %s", w.Code, string(raw))
	}
	return body
}

func assertError(t *testing.T, w *httptest.ResponseRecorder, status int, code string) map[string]any {
	t.Helper()
	if w.Code != status {
		t.Fatalf("status = %d, want %d: %s", w.Code, status, w.Body.String())
	}
	body := decode(t, w)
	if body["code"] != code {
		t.Fatalf("code = %v, want %q: %v", body["code"], code, body["detail"])
	}
	rid, _ := body["request_id"].(string)
	if !ridRE.MatchString(rid) {
		t.Fatalf("request_id %q is not 16 hex", rid)
	}
	if got := w.Header().Get("X-Request-Id"); got != rid {
		t.Fatalf("header X-Request-Id %q != body request_id %q", got, rid)
	}
	return body
}

// TestEveryResponseCarriesARequestID is the one property a user pasting a body
// into a ticket depends on, asserted across a success, a refusal and a 404.
func TestEveryResponseCarriesARequestID(t *testing.T) {
	h := newTestServer(t)
	for _, path := range []string{"/health", "/v1/fleet", "/v1/this-route-does-not-exist"} {
		w := asOperator(t, h, http.MethodGet, path)
		if rid := w.Header().Get("X-Request-Id"); !ridRE.MatchString(rid) {
			t.Errorf("%s: X-Request-Id %q", path, rid)
		}
	}
	first := do(t, h, http.MethodGet, "/health", nil, "").Header().Get("X-Request-Id")
	second := do(t, h, http.MethodGet, "/health", nil, "").Header().Get("X-Request-Id")
	if first == second {
		t.Error("two requests received the same X-Request-Id")
	}
	// An inbound id is recorded for correlation, never echoed: echoing it
	// would hand the caller a log-forging primitive back.
	echoed := do(t, h, http.MethodGet, "/health",
		map[string]string{"X-Request-Id": "upstream-id.1"}, "").Header().Get("X-Request-Id")
	if echoed == "upstream-id.1" || !ridRE.MatchString(echoed) {
		t.Errorf("inbound id was echoed: %q", echoed)
	}
}

func TestHealthIsAnonymousAndEverythingElseIsNot(t *testing.T) {
	h := newTestServer(t)
	w := do(t, h, http.MethodGet, "/health", nil, "")
	if w.Code != http.StatusOK {
		t.Fatalf("/health without a credential: %d %s", w.Code, w.Body.String())
	}
	body := decode(t, w)
	if body["status"] != "ok" || body["version"] == "" {
		t.Fatalf("health body = %v", body)
	}
	assertError(t, do(t, h, http.MethodGet, "/v1/version", nil, ""), 401, "auth_required")
}

func TestUnknownRouteIsAContractErrorBody(t *testing.T) {
	h := newTestServer(t)
	assertError(t, asOperator(t, h, http.MethodGet, "/v1/this-route-does-not-exist"), 404, "not_found")
}

func TestMeReportsTheRoleAndNeverTheKey(t *testing.T) {
	h := newTestServer(t)
	w := asOperator(t, h, http.MethodGet, "/v1/me")
	if w.Code != http.StatusOK {
		t.Fatalf("%d %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Header().Get("Cache-Control"), "no-store") {
		t.Error("/v1/me is cacheable")
	}
	body := decode(t, w)
	if body["role"] != "operator" || body["auth_method"] != "api_key" {
		t.Fatalf("me = %v", body)
	}
	if body["expires_at"] != nil {
		t.Error("a ctl key does not expire; it is revoked")
	}
	if body["sudo_user"] != nil {
		t.Error("sudo_user is a --direct fact and must be null over HTTP")
	}
	if strings.Contains(w.Body.String(), opKey) {
		t.Fatal("the credential that authenticated the request is in the body")
	}
	// The principal id is the label, never the key or a prefix of it.
	if body["principal"] != "key:ops" {
		t.Fatalf("principal = %v", body["principal"])
	}
}

func TestSessionLifecycle(t *testing.T) {
	h := newTestServer(t)
	created := do(t, h, http.MethodPost, "/v1/session", map[string]string{auth.HeaderAPIKey: opKey}, "")
	if created.Code != http.StatusCreated {
		t.Fatalf("create: %d %s", created.Code, created.Body.String())
	}
	if !strings.Contains(created.Header().Get("Cache-Control"), "no-store") {
		t.Error("a session response is cacheable")
	}
	body := decode(t, created)
	sid, _ := body["session_id"].(string)
	if len(sid) != 64 || body["reads_only"] != true || body["role"] != "operator" {
		t.Fatalf("session = %v", body)
	}
	sess := map[string]string{auth.HeaderAuthorization: "Session " + sid}

	me := decode(t, do(t, h, http.MethodGet, "/v1/me", sess, ""))
	if me["auth_method"] != "session" || me["principal"] != body["principal"] || me["expires_at"] != body["expires_at"] {
		t.Fatalf("me over a session = %v (session = %v)", me, body)
	}

	// A session cannot mint a session — 401, because no credential the
	// exchange accepts was presented.
	assertError(t, do(t, h, http.MethodPost, "/v1/session", sess, ""), 401, "auth_required")

	// A session plus a key is the same ambiguity as two headers: 400.
	both := do(t, h, http.MethodGet, "/v1/fleet",
		map[string]string{auth.HeaderAuthorization: "Session " + sid, auth.HeaderAPIKey: opKey}, "")
	assertError(t, both, 400, "both_credentials")

	// A session may not collect a secrets envelope at all.
	secrets := do(t, h, http.MethodGet, "/v1/jobs/01ARZ3NDEKTSV4RRFFQ69G5FAV/secrets", sess, "")
	if secrets.Code != http.StatusForbidden {
		t.Fatalf("secrets over a session: %d %s", secrets.Code, secrets.Body.String())
	}

	revoked := do(t, h, http.MethodDelete, "/v1/session", sess, "")
	if revoked.Code != http.StatusNoContent {
		t.Fatalf("revoke: %d %s", revoked.Code, revoked.Body.String())
	}
	assertError(t, do(t, h, http.MethodGet, "/v1/me", sess, ""), 401, "auth_required")
	// Idempotent.
	if again := do(t, h, http.MethodDelete, "/v1/session", sess, ""); again.Code != 204 && again.Code != 401 {
		t.Fatalf("second revoke: %d", again.Code)
	}
}

func TestLogMeOutEverywhere(t *testing.T) {
	h := newTestServer(t)
	ids := make([]string, 0, 2)
	for i := 0; i < 2; i++ {
		w := do(t, h, http.MethodPost, "/v1/session", map[string]string{auth.HeaderAPIKey: opKey}, "")
		ids = append(ids, decode(t, w)["session_id"].(string))
	}
	other := do(t, h, http.MethodPost, "/v1/session", map[string]string{auth.HeaderAPIKey: viewerKey}, "")
	otherID := decode(t, other)["session_id"].(string)

	if w := do(t, h, http.MethodDelete, "/v1/session", map[string]string{auth.HeaderAPIKey: opKey}, ""); w.Code != 204 {
		t.Fatalf("revoke-all: %d", w.Code)
	}
	for _, id := range ids {
		assertError(t, do(t, h, http.MethodGet, "/v1/me",
			map[string]string{auth.HeaderAuthorization: "Session " + id}, ""), 401, "auth_required")
	}
	// Another subject's session survives.
	if w := do(t, h, http.MethodGet, "/v1/me",
		map[string]string{auth.HeaderAuthorization: "Session " + otherID}, ""); w.Code != 200 {
		t.Fatalf("revoke-all took another subject's session: %d", w.Code)
	}
}

// TestAuthorizationPrecedesLookup is the property the conformance matrix leans
// on: a viewer probing an operator route learns the role answer, never whether
// the target exists.
func TestAuthorizationPrecedesLookup(t *testing.T) {
	h := newTestServer(t)
	viewer := map[string]string{auth.HeaderAPIKey: viewerKey}
	for _, op := range authz.Operations() {
		if op.Role != auth.RoleOperator {
			continue
		}
		path := concrete(op.Path)
		if op.Path == "/v1/tenants/{name}/logs" {
			path += "?file=api"
		}
		w := do(t, h, op.Method, path, viewer, `{"dry_run":true,"args":{}}`)
		assertErrorNamed(t, op.OperationID, w, 403, "forbidden")
	}
	// And with no credential at all, every non-anonymous operation is 401.
	for _, op := range authz.Operations() {
		if op.Role == "anonymous" {
			continue
		}
		path := concrete(op.Path)
		if op.Path == "/v1/tenants/{name}/logs" {
			path += "?file=api"
		}
		w := do(t, h, op.Method, path, nil, `{"dry_run":true,"args":{}}`)
		assertErrorNamed(t, op.OperationID, w, 401, "auth_required")
	}
}

// TestViewerReachesEveryViewerOperation: never 401/403, and never a 5xx.
func TestViewerReachesEveryViewerOperation(t *testing.T) {
	h := newTestServer(t)
	viewer := map[string]string{auth.HeaderAPIKey: viewerKey}
	for _, op := range authz.Operations() {
		if op.Role != auth.RoleViewer {
			continue
		}
		path := strings.ReplaceAll(op.Path, "{name}", "dev")
		path = concrete(path)
		w := do(t, h, op.Method, path, viewer, "")
		if w.Code == 401 || w.Code == 403 {
			t.Errorf("%s: viewer refused with %d: %s", op.OperationID, w.Code, w.Body.String())
		}
		if w.Code >= 500 {
			t.Errorf("%s: %d %s", op.OperationID, w.Code, w.Body.String())
		}
		if !strings.Contains(op.Path, "{") && w.Code >= 400 {
			t.Errorf("%s: a parameterless viewer read answered %d: %s", op.OperationID, w.Code, w.Body.String())
		}
	}
}

func concrete(path string) string {
	path = strings.ReplaceAll(path, "{name}", "zz-nope")
	path = strings.ReplaceAll(path, "{id}", "01ARZ3NDEKTSV4RRFFQ69G5FAV")
	path = strings.ReplaceAll(path, "{n}", "1")
	path = strings.ReplaceAll(path, "{verb}", "start")
	return path
}

func assertErrorNamed(t *testing.T, name string, w *httptest.ResponseRecorder, status int, code string) {
	t.Helper()
	if w.Code != status {
		t.Errorf("%s: status = %d, want %d: %s", name, w.Code, status, w.Body.String())
		return
	}
	var body map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Errorf("%s: body is not JSON: %s", name, w.Body.String())
		return
	}
	if body["code"] != code {
		t.Errorf("%s: code = %v, want %q", name, body["code"], code)
	}
}

func TestViewerReductions(t *testing.T) {
	h := newTestServer(t)
	viewer := map[string]string{auth.HeaderAPIKey: viewerKey}

	// tenants list and show: registry is null for a viewer, present for an operator.
	list := decode(t, do(t, h, http.MethodGet, "/v1/tenants", viewer, ""))
	rows, _ := list["tenants"].([]any)
	if len(rows) == 0 {
		t.Fatal("the fixture fleet is empty")
	}
	for _, row := range rows {
		if row.(map[string]any)["registry"] != nil {
			t.Fatal("a viewer received a registry row")
		}
	}
	opList := decode(t, asOperator(t, h, http.MethodGet, "/v1/tenants"))
	for _, row := range opList["tenants"].([]any) {
		if row.(map[string]any)["registry"] == nil {
			t.Fatal("an operator did not receive the registry row")
		}
	}

	// env: a viewer sees public rows only, and cannot enumerate which secrets
	// exist — that is a fact of its own.
	vEnv := decode(t, do(t, h, http.MethodGet, "/v1/tenants/dev/env", viewer, ""))
	for _, k := range vEnv["keys"].([]any) {
		if k.(map[string]any)["class"] != "public" {
			t.Fatalf("a viewer received a non-public env row: %v", k)
		}
	}
	oEnv := decode(t, asOperator(t, h, http.MethodGet, "/v1/tenants/dev/env"))
	sawSecret := false
	for _, k := range oEnv["keys"].([]any) {
		row := k.(map[string]any)
		if row["class"] != "public" {
			sawSecret = true
			if row["value_redacted"] != "<redacted>" {
				t.Fatalf("a non-public value was shown: %v", row)
			}
		}
	}
	if !sawSecret {
		t.Fatal("the fixture shows no non-public key, so the redaction assertion is vacuous")
	}

	// doctor: a viewer's findings carry no host paths.
	vDoc := decode(t, do(t, h, http.MethodGet, "/v1/doctor", viewer, ""))
	for _, f := range vDoc["findings"].([]any) {
		detail := f.(map[string]any)["detail"].(string)
		if strings.Contains(detail, "/rag/data/tenants/") {
			t.Fatalf("a viewer received an absolute host path: %q", detail)
		}
	}
	oDoc := decode(t, asOperator(t, h, http.MethodGet, "/v1/doctor"))
	if oDoc["hash"] != vDoc["hash"] {
		t.Error("the doctor hash changed with the caller's role; a plan pins that hash")
	}
}

func TestFleetAndTenantsAgree(t *testing.T) {
	h := newTestServer(t)
	fleet := decode(t, asOperator(t, h, http.MethodGet, "/v1/fleet"))
	tenants := decode(t, asOperator(t, h, http.MethodGet, "/v1/tenants"))
	fleetRows := fleet["tenants"].([]any)
	tenantRows := tenants["tenants"].([]any)
	if len(fleetRows) != len(tenantRows) {
		t.Fatalf("%d fleet rows vs %d tenant rows", len(fleetRows), len(tenantRows))
	}
	for i := range fleetRows {
		a := fleetRows[i].(map[string]any)["name"]
		b := tenantRows[i].(map[string]any)["summary"].(map[string]any)["name"]
		if a != b {
			t.Fatalf("row %d: fleet says %v, tenants says %v", i, a, b)
		}
	}
}

// TestNoSecretShapedNamesOrValues is the redaction pin, run over every read
// the suite can reach without a job.
func TestNoSecretShapedNamesOrValues(t *testing.T) {
	h := newTestServer(t)
	allowNames := map[string]bool{"secret_refs": true, "secrets_file_sha256": true}
	digestFields := map[string]bool{
		"sha256": true, "env_file_sha256": true, "secrets_file_sha256": true,
		"lockhash": true, "sha256sums": true, "session_id": true,
	}
	nameRE := regexp.MustCompile(`(?i)(api_key|password|secret|token|dsn)`)
	hexRE := regexp.MustCompile(`^[0-9a-f]{64}$`)
	sigRE := regexp.MustCompile(`\|sig=[0-9a-f]{64,}`)

	for _, path := range []string{
		"/v1/fleet", "/v1/tenants", "/v1/tenants/dev", "/v1/tenants/dev/env",
		"/v1/tenants/dev/logs?file=api", "/v1/doctor", "/v1/gateway", "/v1/settings",
		"/v1/jobs", "/v1/audit",
	} {
		w := asOperator(t, h, http.MethodGet, path)
		if w.Code != http.StatusOK {
			t.Fatalf("%s: %d %s", path, w.Code, w.Body.String())
		}
		var tree any
		if err := json.Unmarshal(w.Body.Bytes(), &tree); err != nil {
			t.Fatalf("%s: %v", path, err)
		}
		walk(tree, func(key string, value any) {
			if key != "" && nameRE.MatchString(key) && !allowNames[key] {
				t.Errorf("%s: field name %q looks like a secret", path, key)
			}
			s, ok := value.(string)
			if !ok {
				return
			}
			if hexRE.MatchString(s) && !digestFields[key] {
				t.Errorf("%s: %q carries a credential-shaped value", path, key)
			}
			if sigRE.MatchString(s) {
				t.Errorf("%s: %q carries an unredacted token signature", path, key)
			}
		})
	}
}

func walk(node any, visit func(key string, value any)) {
	switch n := node.(type) {
	case map[string]any:
		for k, v := range n {
			visit(k, v)
			walk(v, visit)
		}
	case []any:
		for _, v := range n {
			visit("", v)
			walk(v, visit)
		}
	}
}

func TestValidationAndLookupAnswers(t *testing.T) {
	h := newTestServer(t)
	cases := []struct {
		path   string
		status int
		code   string
	}{
		// Outside the name grammar: a value that could not name anything, so
		// it never reaches the registry.
		{"/v1/tenants/Not_A_Valid_Name", 422, "validation"},
		{"/v1/tenants/zz-conformance-nope", 404, "not_found"},
		{"/v1/tenants/zz-conformance-nope/env", 404, "not_found"},
		{"/v1/doctor?tenant=zz-conformance-nope", 404, "not_found"},
		{"/v1/doctor?op=explode", 422, "validation"},
		{"/v1/tenants/dev/logs", 422, "validation"},           // file is required
		{"/v1/tenants/dev/logs?file=nope", 422, "validation"}, // outside the enum
		{"/v1/tenants/dev/logs?file=api&lines=999999", 422, "validation"},
		{"/v1/tenants/dev/logs?file=qdrant", 200, ""},           // dev owns its qdrant
		{"/v1/tenants/demo/logs?file=qdrant", 404, "not_found"}, // demo shares it
		{"/v1/jobs/not-a-ulid", 422, "validation"},
		{"/v1/jobs/01ARZ3NDEKTSV4RRFFQ69G5FAV", 404, "not_found"},
	}
	for _, c := range cases {
		w := asOperator(t, h, http.MethodGet, c.path)
		if c.code == "" {
			if w.Code != c.status {
				t.Errorf("%s: %d, want %d: %s", c.path, w.Code, c.status, w.Body.String())
			}
			continue
		}
		assertErrorNamed(t, c.path, w, c.status, c.code)
	}
}

func TestDoctorHashIsStableAndScoped(t *testing.T) {
	h := newTestServer(t)
	a := decode(t, asOperator(t, h, http.MethodGet, "/v1/doctor"))
	b := decode(t, asOperator(t, h, http.MethodGet, "/v1/doctor"))
	if a["hash"] != b["hash"] {
		t.Fatal("two doctor runs over an unchanged fixture hashed differently; every plan would go stale on its own")
	}
	scope := a["scope"].(map[string]any)
	if scope["tenant"] != nil || scope["op"] != nil {
		t.Fatalf("a fleet-wide run reported scope %v", scope)
	}
	// status is the max over the finding levels.
	levels := map[string]bool{}
	for _, f := range a["findings"].([]any) {
		levels[f.(map[string]any)["level"].(string)] = true
	}
	want := "green"
	switch {
	case levels["error"]:
		want = "red"
	case levels["warn"]:
		want = "yellow"
	}
	if a["status"] != want {
		t.Fatalf("status = %v, want %v (levels %v)", a["status"], want, levels)
	}

	scoped := decode(t, asOperator(t, h, http.MethodGet, "/v1/doctor?tenant=dev&op=start"))
	sc := scoped["scope"].(map[string]any)
	if sc["tenant"] != "dev" || sc["op"] != "start" {
		t.Fatalf("scope = %v", sc)
	}
	for _, f := range scoped["findings"].([]any) {
		if tn := f.(map[string]any)["tenant"]; tn != nil && tn != "dev" {
			t.Fatalf("a dev-scoped run reported a finding for %v", tn)
		}
	}
}

func TestMutationsAreRefusedAfterAuthorization(t *testing.T) {
	h := newTestServer(t)
	// The operator gets the refusal…
	w := do(t, h, http.MethodPost, "/v1/tenants/dev/ops/start",
		map[string]string{auth.HeaderAPIKey: opKey}, `{"dry_run":true,"args":{}}`)
	body := assertError(t, w, 409, "refused")
	if !strings.Contains(body["detail"].(string), "PR-C") {
		t.Fatalf("detail does not say when mutations arrive: %v", body["detail"])
	}
	// …and the viewer never reaches it: the role answer comes first.
	assertError(t, do(t, h, http.MethodPost, "/v1/tenants/dev/ops/start",
		map[string]string{auth.HeaderAPIKey: viewerKey}, `{"dry_run":true,"args":{}}`), 403, "forbidden")
}

func TestBodyKeyRules(t *testing.T) {
	h := newTestServer(t)

	// A body key that differs from the header key is the same ambiguity as
	// two headers.
	w := do(t, h, http.MethodPost, "/v1/tenants/dev/ops/start",
		map[string]string{auth.HeaderAPIKey: opKey},
		`{"dry_run":false,"ctl_api_key":"`+viewerKey+`"}`)
	assertError(t, w, 400, "both_credentials")

	// The same key in both places is fine (and then refused for PR-A reasons).
	same := do(t, h, http.MethodPost, "/v1/tenants/dev/ops/start",
		map[string]string{auth.HeaderAPIKey: opKey},
		`{"dry_run":false,"ctl_api_key":"`+opKey+`"}`)
	assertError(t, same, 409, "refused")

	// A session with no body key cannot mutate at all.
	created := do(t, h, http.MethodPost, "/v1/session", map[string]string{auth.HeaderAPIKey: opKey}, "")
	sid := decode(t, created)["session_id"].(string)
	sess := map[string]string{auth.HeaderAuthorization: "Session " + sid}
	nokey := do(t, h, http.MethodPost, "/v1/tenants/dev/ops/start", sess, `{"dry_run":false}`)
	body := assertError(t, nokey, 403, "forbidden")
	if !strings.Contains(body["detail"].(string), "read-only") {
		t.Fatalf("detail = %v", body["detail"])
	}

	// A session plus a key bound to ANOTHER principal is an escalation with
	// two owners: 403.
	wrong := do(t, h, http.MethodPost, "/v1/tenants/dev/ops/start", sess,
		`{"dry_run":false,"ctl_api_key":"`+viewerKey+`"}`)
	assertError(t, wrong, 403, "forbidden")

	// A session plus the key it was minted from is accepted, then refused for
	// PR-A reasons.
	right := do(t, h, http.MethodPost, "/v1/tenants/dev/ops/start", sess,
		`{"dry_run":false,"ctl_api_key":"`+opKey+`"}`)
	assertError(t, right, 409, "refused")
}

func TestRateLimiterAnswers429(t *testing.T) {
	envs := map[string]string{auth.EnvAPIKeys: `["` + opKey + `"]`, auth.EnvAPIKeyRoles: `{"` + opKey + `":"operator"}`}
	keys, err := auth.LoadKeys(func(k string) string { return envs[k] })
	if err != nil {
		t.Fatal(err)
	}
	verifier, _ := auth.New(auth.Options{})
	sessions := session.NewMemoryStore()
	h := NewRouter(&Server{
		Backend: NewFakeBackend(),
		Resolver: &auth.Resolver{
			Keys: keys, Verifier: verifier, Sessions: sessions,
			Limiter: ratelimit.New(ratelimit.Config{PerCredential: 2, TarpitAt: -1}),
			Reject:  reject,
		},
		Sessions: sessions,
	})
	bad := map[string]string{auth.HeaderAPIKey: "not-a-key"}
	for i := 0; i < 2; i++ {
		assertError(t, do(t, h, http.MethodGet, "/v1/fleet", bad, ""), 401, "auth_required")
	}
	w := do(t, h, http.MethodGet, "/v1/fleet", bad, "")
	body := assertError(t, w, 429, "rate_limited")
	if w.Header().Get("Retry-After") == "" {
		t.Error("a 429 without Retry-After tells the caller nothing about when to come back")
	}
	if extra, ok := body["extra"].(map[string]any); !ok || extra["retry_after"] == nil {
		t.Errorf("extra.retry_after missing: %v", body["extra"])
	}
	// The good key is untouched: one credential's failures must not lock out
	// another principal.
	if ok := do(t, h, http.MethodGet, "/v1/fleet", map[string]string{auth.HeaderAPIKey: opKey}, ""); ok.Code != 200 {
		t.Fatalf("a valid key was rate-limited by another credential's failures: %d", ok.Code)
	}
}

func TestEveryRegisteredRouteHasAMatrixRow(t *testing.T) {
	// route() panics on a path the matrix does not list; building the router
	// is the assertion. Stated as a test so the failure has a name.
	defer func() {
		if rec := recover(); rec != nil {
			t.Fatalf("a registered route has no authorization row: %v", rec)
		}
	}()
	newTestServer(t)
}

func TestCheckLoopback(t *testing.T) {
	for _, c := range []struct {
		listen string
		allow  bool
		ok     bool
	}{
		{"127.0.0.1:23990", false, true},
		{"localhost:23990", false, true},
		{"[::1]:23990", false, true},
		{"0.0.0.0:23990", false, false},
		{"0.0.0.0:23990", true, true},
		{"10.0.0.5:23990", false, false},
		{":23990", false, false},
		{"nonsense", false, false},
	} {
		err := checkLoopback(c.listen, c.allow)
		if (err == nil) != c.ok {
			t.Errorf("checkLoopback(%q, %v) = %v; want ok=%v", c.listen, c.allow, err, c.ok)
		}
	}
}

func TestEnvKeysAreDocumentedOnce(t *testing.T) {
	seen := map[string]bool{}
	for _, k := range EnvKeys() {
		if !strings.HasPrefix(k, "CTL_") {
			t.Errorf("%q is not a CTL_ variable", k)
		}
		if seen[k] {
			t.Errorf("%q is listed twice", k)
		}
		seen[k] = true
	}
	// Everything the auth package reads must be in the documented list, or
	// the Ansible template can be reconciled against an incomplete one.
	for _, k := range []string{
		auth.EnvAPIKeys, auth.EnvAPIKeyRoles, auth.EnvAPIKeyNames,
		auth.EnvAdminSubjects, auth.EnvViewerSubjects,
	} {
		if !seen[k] {
			t.Errorf("%s is read by internal/ctl/auth but is not in api.EnvKeys()", k)
		}
	}
	for _, k := range SecretEnvKeys() {
		if !seen[k] {
			t.Errorf("%s is a secret variable that EnvKeys() does not list", k)
		}
	}
}
