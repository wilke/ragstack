package api

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/auth"
	"github.com/ragstack/ragstack/internal/ctl/authz"
	"github.com/ragstack/ragstack/internal/ctl/doctor"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/ratelimit"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/session"
)

// --------------------------------------------------------------------------
// Helpers
// --------------------------------------------------------------------------

// serverWith builds a router over an explicit Keys/limiter/verifier, for the
// tests that need something newTestServer's fixed shape does not give them.
func serverWith(t *testing.T, env map[string]string, limiter *ratelimit.Limiter, verifier *auth.Verifier) (http.Handler, *Server) {
	t.Helper()
	keys, err := auth.LoadKeys(func(k string) string { return env[k] })
	if err != nil {
		t.Fatal(err)
	}
	if verifier == nil {
		verifier, err = auth.New(auth.Options{})
		if err != nil {
			t.Fatal(err)
		}
	}
	sessions := session.NewMemoryStore()
	s := &Server{
		Backend: NewFakeBackend(),
		Resolver: &auth.Resolver{
			Keys: keys, Verifier: verifier, Sessions: sessions,
			Limiter: limiter, Reject: reject,
		},
		Sessions: sessions,
	}
	return NewRouter(s), s
}

// concretePath turns a matrix pattern into a request path. Which values are
// used does not matter: authorization — and the body-key rule — precede lookup,
// so no handler ever sees them.
func concretePath(pattern string) string {
	r := strings.NewReplacer(
		"{name}", "dev",
		"{verb}", "start",
		"{id}", "01ARZ3NDEKTSV4RRFFQ69G5FAV",
		"{n}", "1",
	)
	return r.Replace(pattern)
}

// bvbrcFixture is the committed BV-BRC replay table, reused here so the api
// tests can exercise a REAL bearer principal rather than a hand-made one.
func bvbrcFixture(t *testing.T) (token string, allowlist []string, now time.Time, keyBody []byte) {
	t.Helper()
	const dir = "../../../../contracts/fixtures/identity/bvbrc"
	raw, err := os.ReadFile(filepath.Join(dir, "vectors.json"))
	if err != nil {
		t.Skipf("identity fixture unavailable: %v", err)
	}
	var vf struct {
		Allowlist []string `json:"allowlist"`
		Now       int64    `json:"now"`
		Vectors   []struct {
			Name   string `json:"name"`
			Token  string `json:"token"`
			Expect struct {
				OK      bool   `json:"ok"`
				Subject string `json:"subject"`
			} `json:"expect"`
		} `json:"vectors"`
	}
	if err := json.Unmarshal(raw, &vf); err != nil {
		t.Fatalf("parse vectors.json: %v", err)
	}
	keyBody, err = os.ReadFile(filepath.Join(dir, "public_key.json"))
	if err != nil {
		t.Fatalf("read public_key.json: %v", err)
	}
	for _, v := range vf.Vectors {
		if v.Name == "valid" && v.Expect.OK {
			return v.Token, vf.Allowlist, time.Unix(vf.Now, 0).UTC(), keyBody
		}
	}
	t.Fatal("vectors.json has no vector named \"valid\"")
	return "", nil, time.Time{}, nil
}

// --------------------------------------------------------------------------
// Finding 1 — the limiter map is state an anonymous caller can grow
// --------------------------------------------------------------------------

// TestAnonymousHealthProbesLeaveNoLimiterState is the router half of "Allow
// never inserts".
//
// GET /health is the one anonymous operation, so the auth middleware skips it
// and Fail is NEVER reached for it — but the rate-limit middleware runs first
// and calls Allow with whatever credential the request carried. An Allow that
// wrote the pruned (empty) slice back left one permanent map entry per
// distinct X-API-Key value, from an unauthenticated caller, on the daemon's
// only public path.
func TestAnonymousHealthProbesLeaveNoLimiterState(t *testing.T) {
	limiter := ratelimit.New(ratelimit.Config{PerCredential: 5, TarpitAt: -1})
	h, _ := serverWith(t, map[string]string{
		auth.EnvAPIKeys:     `["` + opKey + `"]`,
		auth.EnvAPIKeyRoles: `{"` + opKey + `":"operator"}`,
	}, limiter, nil)

	for i := 0; i < 500; i++ {
		w := do(t, h, http.MethodGet, "/health", map[string]string{auth.HeaderAPIKey: "probe-" + strconv.Itoa(i)}, "")
		if w.Code != http.StatusOK {
			t.Fatalf("/health answered %d for probe %d", w.Code, i)
		}
	}
	if n := limiter.Tracked(); n != 0 {
		t.Fatalf("500 anonymous /health probes left %d limiter entries; want 0", n)
	}

	// The same for authenticated traffic that never fails.
	for i := 0; i < 50; i++ {
		if w := asOperator(t, h, http.MethodGet, "/v1/fleet"); w.Code != http.StatusOK {
			t.Fatalf("/v1/fleet answered %d", w.Code)
		}
	}
	if n := limiter.Tracked(); n != 0 {
		t.Fatalf("successful authenticated requests left %d limiter entries; want 0", n)
	}
}

// --------------------------------------------------------------------------
// Finding 2 — the daemon had no timeouts and its tarpit outlived SIGTERM
// --------------------------------------------------------------------------

// TestServeSetsEveryServerTimeout asserts on the constructed *http.Server.
// Without these a slow client holds a connection, a goroutine and a read
// buffer for as long as it likes, having presented no credential at all.
func TestServeSetsEveryServerTimeout(t *testing.T) {
	srv := newHTTPServer("127.0.0.1:0", http.NotFoundHandler(), context.Background())
	for _, c := range []struct {
		name string
		got  time.Duration
		want time.Duration
	}{
		{"ReadHeaderTimeout", srv.ReadHeaderTimeout, readHeaderTimeout},
		{"ReadTimeout", srv.ReadTimeout, readTimeout},
		{"WriteTimeout", srv.WriteTimeout, writeTimeout},
		{"IdleTimeout", srv.IdleTimeout, idleTimeout},
	} {
		if c.got != c.want {
			t.Errorf("%s = %v; want %v", c.name, c.got, c.want)
		}
	}
	if srv.MaxHeaderBytes != maxHeaderBytes {
		t.Errorf("MaxHeaderBytes = %d; want %d", srv.MaxHeaderBytes, maxHeaderBytes)
	}
	// The tarpit must fit inside the write timeout, or the response it delays
	// is cut at the wire instead of being written.
	if ratelimit.MaxTarpitDelay >= srv.WriteTimeout {
		t.Errorf("the maximum tarpit delay (%v) is not below WriteTimeout (%v)", ratelimit.MaxTarpitDelay, srv.WriteTimeout)
	}
	if srv.BaseContext == nil {
		t.Error("BaseContext is nil; nothing can then cancel an in-flight tarpit on shutdown")
	}
}

// TestShutdownCancelsInFlightTarpits is the SIGTERM path.
//
// The tarpit delay runs in the request goroutine. Shutdown waits for handlers
// to return, so a delay that ignored its context outlived the 10 s grace,
// Shutdown returned DeadlineExceeded and `systemctl stop` became exit 1.
// Cancelling the server's base context first is what makes the stop clean.
func TestShutdownCancelsInFlightTarpits(t *testing.T) {
	limiter := ratelimit.New(ratelimit.Config{PerCredential: -1, TarpitAt: 1, TarpitDelay: time.Hour})
	limiter.Fail("something")

	entered := make(chan struct{})
	requestCtx, endRequests := context.WithCancel(context.Background())
	defer endRequests()
	srv := newHTTPServer("", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		close(entered)
		limiter.Tarpit(r.Context())
		w.WriteHeader(http.StatusUnauthorized)
	}), requestCtx)

	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	go func() { _ = srv.Serve(ln) }()

	go func() {
		client := &http.Client{Timeout: 30 * time.Second}
		resp, err := client.Get("http://" + ln.Addr().String() + "/v1/fleet")
		if err == nil {
			_ = resp.Body.Close()
		}
	}()
	<-entered

	// Exactly what RunServe does on SIGTERM, with a grace far shorter than the
	// hour-long tarpit so the assertion cannot pass by waiting it out.
	done := make(chan error, 1)
	go func() {
		endRequests()
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		done <- srv.Shutdown(ctx)
	}()
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("Shutdown returned %v; a stop during a tarpit must exit 0", err)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("Shutdown did not return: the tarpit ignored the server context")
	}
}

// --------------------------------------------------------------------------
// Finding 3 — two log calls bypassed the redacting logger
// --------------------------------------------------------------------------

// TestPanicAndBackendErrorsGoThroughTheRedactor.
//
// slog.Default() has no ReplaceAttr, so a package-level slog.Error is a hole
// straight past the redactor — and the two places that used one were logging a
// panic value and a backend error, which is exactly where a token signature
// and an `API_KEYS=` line end up.
func TestPanicAndBackendErrorsGoThroughTheRedactor(t *testing.T) {
	const sig = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
	var buf bytes.Buffer
	logger := slog.New(slog.NewTextHandler(&buf, &slog.HandlerOptions{ReplaceAttr: redactAttr}))
	s := &Server{Backend: NewFakeBackend(), Logger: logger}

	// A panic whose value carries a BV-BRC signature.
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/v1/fleet", nil)
	recoverer(logger)(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		panic("X=|sig=" + sig)
	})).ServeHTTP(rec, req)
	if rec.Code != http.StatusInternalServerError {
		t.Fatalf("a panic answered %d; want 500", rec.Code)
	}

	// A backend error whose text quotes a secrets line.
	s.backendError(httptest.NewRecorder(), req, errBackendWithSecret())

	out := buf.String()
	if strings.Contains(out, sig) {
		t.Errorf("a token signature reached the log: %s", out)
	}
	if strings.Contains(out, "s3cret-key-value") {
		t.Errorf("an API_KEYS value reached the log: %s", out)
	}
	if strings.Count(out, "<REDACTED>") < 2 {
		t.Errorf("expected both lines redacted, got: %s", out)
	}
}

type backendSecretError struct{}

func (backendSecretError) Error() string { return `API_KEYS='["s3cret-key-value"]'` }

func errBackendWithSecret() error { return backendSecretError{} }

// --------------------------------------------------------------------------
// Finding 4 — a bearer principal could never satisfy the body-key rule
// --------------------------------------------------------------------------

// TestBoundBodyKeyAuthorizesAnIdentityPrincipal.
//
// A ctl key's principal id is "key:<label>" and a verified identity's is
// "bvbrc:<un>", so comparing the two could never succeed: `ctl_api_key` — the
// contract's whole mechanism for letting a browser authorize a mutation —
// was unusable by the browser flow it was designed for. CTL_API_KEY_PRINCIPALS
// states the binding, and only a caller who IS that subject may use the key.
func TestBoundBodyKeyAuthorizesAnIdentityPrincipal(t *testing.T) {
	token, allowlist, now, keyBody := bvbrcFixture(t)
	verifier, err := auth.New(auth.Options{
		Allowlist: allowlist,
		Now:       func() time.Time { return now },
		Fetch:     func(context.Context, string) ([]byte, error) { return keyBody, nil },
	})
	if err != nil {
		t.Fatal(err)
	}
	const subject = "bvbrc:alice@patricbrc.org"
	h, _ := serverWith(t, map[string]string{
		auth.EnvAPIKeys:          `["` + opKey + `","` + viewerKey + `"]`,
		auth.EnvAPIKeyRoles:      `{"` + opKey + `":"operator","` + viewerKey + `":"operator"}`,
		auth.EnvAPIKeyNames:      `{"` + opKey + `":"alices-key","` + viewerKey + `":"someone-elses"}`,
		auth.EnvAPIKeyPrincipals: `{"` + opKey + `":"` + subject + `"}`,
		auth.EnvAdminSubjects:    subject,
	}, ratelimit.New(ratelimit.Config{PerCredential: -1, TarpitAt: -1}), verifier)

	bearer := map[string]string{auth.HeaderAuthorization: "Bearer " + token}
	const op = "/v1/tenants/dev/ops/start"

	// The key bound to this identity: accepted, then refused for PR-A reasons.
	bound := do(t, h, http.MethodPost, op, bearer,
		`{"dry_run":false,"idempotency_key":"01ARZ3NDEKTSV4RR","args":{},"ctl_api_key":"`+opKey+`"}`)
	assertError(t, bound, 409, "refused")

	// A key bound to nobody is not this identity's to present.
	unbound := do(t, h, http.MethodPost, op, bearer,
		`{"dry_run":false,"idempotency_key":"01ARZ3NDEKTSV4RR","args":{},"ctl_api_key":"`+viewerKey+`"}`)
	assertError(t, unbound, 403, "forbidden")

	// The same through a session minted from the bearer token: the session
	// carries the subject, so the binding still matches.
	created := do(t, h, http.MethodPost, "/v1/session", bearer, "")
	if created.Code != http.StatusCreated {
		t.Fatalf("session create answered %d: %s", created.Code, created.Body.String())
	}
	sid := decode(t, created)["session_id"].(string)
	sess := map[string]string{auth.HeaderAuthorization: "Session " + sid}
	viaSession := do(t, h, http.MethodPost, op, sess,
		`{"dry_run":false,"idempotency_key":"01ARZ3NDEKTSV4RR","args":{},"ctl_api_key":"`+opKey+`"}`)
	assertError(t, viaSession, 409, "refused")

	// …and a session with the unbound key is still refused.
	assertError(t, do(t, h, http.MethodPost, op, sess,
		`{"dry_run":false,"idempotency_key":"01ARZ3NDEKTSV4RR","args":{},"ctl_api_key":"`+viewerKey+`"}`), 403, "forbidden")
}

// --------------------------------------------------------------------------
// Finding 5 — the session mutation rule lived in one handler, not in the guard
// --------------------------------------------------------------------------

// TestEveryMutatingRowRefusesASessionWithoutABodyKey is driven off the matrix
// rather than a list written here: the rule is a property of every mutating
// operation, and a rule enforced inside one handler is one forgotten call away
// from absent the moment a real mutation handler is written.
func TestEveryMutatingRowRefusesASessionWithoutABodyKey(t *testing.T) {
	h := newTestServer(t)
	created := do(t, h, http.MethodPost, "/v1/session", map[string]string{auth.HeaderAPIKey: opKey}, "")
	sid := decode(t, created)["session_id"].(string)
	sess := map[string]string{auth.HeaderAuthorization: "Session " + sid}

	mutating := 0
	for _, row := range authz.Matrix {
		if !row.Mutating {
			continue
		}
		mutating++
		path := concretePath(row.Path)

		body := `{"dry_run":false,"idempotency_key":"01ARZ3NDEKTSV4RR","args":{}}`
		w := do(t, h, row.Method, path, sess, body)
		got := assertError(t, w, 403, "forbidden")
		if !strings.Contains(got["detail"].(string), "read-only") {
			t.Errorf("%s: detail = %v", row.OperationID, got["detail"])
		}

		wrong := `{"dry_run":false,"idempotency_key":"01ARZ3NDEKTSV4RR","args":{},"ctl_api_key":"` + viewerKey + `"}`
		assertError(t, do(t, h, row.Method, path, sess, wrong), 403, "forbidden")

		// The same session WITH the key it was minted from gets past the rule
		// and lands on PR-A's refusal — the rule gates, it does not block.
		right := `{"dry_run":false,"idempotency_key":"01ARZ3NDEKTSV4RR","args":{},"ctl_api_key":"` + opKey + `"}`
		assertError(t, do(t, h, row.Method, path, sess, right), 409, "refused")
	}
	if mutating == 0 {
		t.Fatal("the matrix marks no operation mutating; the test would assert nothing")
	}
}

// TestGuardHandsTheParsedBodyToTheHandler: the body is a STREAM, so the guard
// reading it and the handler reading it again would give the handler an empty
// one — and re-reading is also how a check and the thing it checked come to
// disagree. The guard parses once and passes the result down.
func TestGuardHandsTheParsedBodyToTheHandler(t *testing.T) {
	var seen opRequest
	var ok bool
	s := &Server{Backend: NewFakeBackend()}
	row, found := authz.Lookup(http.MethodPost, "/v1/tenants/{name}/ops/{verb}")
	if !found || !row.Mutating {
		t.Fatal("ctlTenantOp is not a mutating row; the test would assert nothing")
	}
	h := s.guard(row, func(_ http.ResponseWriter, r *http.Request) {
		seen, ok = mutationBodyFromContext(r.Context())
	})
	req := httptest.NewRequest(http.MethodPost, "/v1/tenants/dev/ops/start",
		strings.NewReader(`{"dry_run":true,"idempotency_key":"01ARZ3NDEKTSV4RR","confirm":"yes","args":{}}`))
	req = req.WithContext(auth.WithPrincipal(req.Context(), auth.Principal{
		Subject: "key:ops", Role: auth.RoleOperator, AuthMethod: auth.MethodAPIKey,
	}))
	h(httptest.NewRecorder(), req)
	if !ok {
		t.Fatal("the handler received no parsed body; it would have to read the stream a second time")
	}
	if !seen.DryRun || seen.Confirm != "yes" {
		t.Fatalf("the handler received a different body than the guard checked: %+v", seen)
	}
	// …and a non-mutating row hands down nothing, so a handler cannot assume it.
	readRow, _ := authz.Lookup(http.MethodGet, "/v1/fleet")
	s.guard(readRow, func(_ http.ResponseWriter, r *http.Request) {
		if _, present := mutationBodyFromContext(r.Context()); present {
			t.Error("a read route carried a mutation body in its context")
		}
	})(httptest.NewRecorder(), req)
}

// TestNonMutatingWritesStayReachableFromASession guards the other direction:
// `Mutating` is derived from the contract (a non-GET whose body can carry
// ctl_api_key), NOT from the method, because DELETE /v1/session is a session
// logging itself out and POST /v1/gateway/render is a viewer's dry render.
// Deriving it from the method would have made both answer 403.
func TestNonMutatingWritesStayReachableFromASession(t *testing.T) {
	h := newTestServer(t)
	created := do(t, h, http.MethodPost, "/v1/session", map[string]string{auth.HeaderAPIKey: opKey}, "")
	sid := decode(t, created)["session_id"].(string)
	sess := map[string]string{auth.HeaderAuthorization: "Session " + sid}

	for _, row := range authz.Matrix {
		if row.Mutating || strings.EqualFold(row.Method, http.MethodGet) {
			continue
		}
		if row.OperationID == "ctlSessionCreate" {
			continue // a session may not mint a session: its own, documented 401
		}
		w := do(t, h, row.Method, concretePath(row.Path), sess, "")
		if w.Code == http.StatusForbidden {
			t.Errorf("%s (%s %s) is not a mutation but answered 403 to a session: %s",
				row.OperationID, row.Method, row.Path, w.Body.String())
		}
	}
}

// --------------------------------------------------------------------------
// Findings 13 and 14 — the mutation body
// --------------------------------------------------------------------------

// TestOversizedMutationBodyIsRefusedNotTruncated: io.LimitReader silently cut
// the body at 1 MiB and parsed the PREFIX, so an oversized request produced a
// decision about a body nobody sent — including, for a body whose ctl_api_key
// fell past the cut, "this session presented no key".
func TestOversizedMutationBodyIsRefusedNotTruncated(t *testing.T) {
	h := newTestServer(t)
	big := `{"dry_run":false,"idempotency_key":"01ARZ3NDEKTSV4RR","args":{},"confirm":"` +
		strings.Repeat("a", maxBodyBytes+1024) + `"}`
	w := do(t, h, http.MethodPost, "/v1/tenants/dev/ops/start", map[string]string{auth.HeaderAPIKey: opKey}, big)
	body := assertError(t, w, 422, "validation")
	if !strings.Contains(body["detail"].(string), "1 MiB") {
		t.Fatalf("detail does not name the limit: %v", body["detail"])
	}
}

// TestUnknownAndNullMutationBodiesAreRefused: an unknown member used to be
// dropped, so `ctl_api_kye` read as "no key presented"; and the literal `null`
// decoded into the zero envelope, a mutation with no body that looked like one
// with an empty body.
func TestUnknownAndNullMutationBodiesAreRefused(t *testing.T) {
	h := newTestServer(t)
	hdr := map[string]string{auth.HeaderAPIKey: opKey}
	for _, c := range []struct{ name, body string }{
		{"unknown member", `{"dry_run":false,"idempotency_key":"01ARZ3NDEKTSV4RR","args":{},"ctl_api_kye":"x"}`},
		{"null body", `null`},
		{"trailing content", `{"dry_run":false,"idempotency_key":"01ARZ3NDEKTSV4RR","args":{}} {"more":1}`},
	} {
		c := c
		t.Run(c.name, func(t *testing.T) {
			assertError(t, do(t, h, http.MethodPost, "/v1/tenants/dev/ops/start", hdr, c.body), 422, "validation")
		})
	}
	// A body the contract DOES define still parses.
	ok := do(t, h, http.MethodPost, "/v1/tenants/dev/ops/start", hdr,
		`{"dry_run":true,"idempotency_key":"01ARZ3NDEKTSV4RR","confirm":"x","force_with_doctor_diff":"sha256:`+
			strings.Repeat("0", 64)+`","args":{"any":"thing"}}`)
	assertError(t, ok, 409, "refused")
}

// TestBadBodyKeysAreRateLimited: the body is a second place a ctl key can be
// presented, and refusals there were counted by nothing — so one read-only
// session was an unmetered guessing channel against every operator key.
func TestBadBodyKeysAreRateLimited(t *testing.T) {
	limiter := ratelimit.New(ratelimit.Config{PerCredential: 3, TarpitAt: -1})
	h, _ := serverWith(t, map[string]string{
		auth.EnvAPIKeys:     `["` + opKey + `"]`,
		auth.EnvAPIKeyRoles: `{"` + opKey + `":"operator"}`,
	}, limiter, nil)

	created := do(t, h, http.MethodPost, "/v1/session", map[string]string{auth.HeaderAPIKey: opKey}, "")
	sid := decode(t, created)["session_id"].(string)
	sess := map[string]string{auth.HeaderAuthorization: "Session " + sid}

	for i := 0; i < 3; i++ {
		guess := `{"dry_run":false,"idempotency_key":"01ARZ3NDEKTSV4RR","args":{},"ctl_api_key":"guess-` +
			strconv.Itoa(i) + `-0000000000000000"}`
		assertError(t, do(t, h, http.MethodPost, "/v1/tenants/dev/ops/start", sess, guess), 403, "forbidden")
	}
	last := `{"dry_run":false,"idempotency_key":"01ARZ3NDEKTSV4RR","args":{},"ctl_api_key":"guess-3-0000000000000000"}`
	w := do(t, h, http.MethodPost, "/v1/tenants/dev/ops/start", sess, last)
	assertError(t, w, 429, "rate_limited")
	if w.Header().Get("Retry-After") == "" {
		t.Error("a 429 without Retry-After tells the caller nothing about when to come back")
	}
}

// --------------------------------------------------------------------------
// Findings 9, 12, 15 — smaller fail-open cases
// --------------------------------------------------------------------------

// TestViewerReductionIsAppliedWithoutAPrincipal: isViewer answered "operator"
// when there was no principal at all, so any handler path reached without the
// auth middleware — a unit test, the --direct CLI, a future router — emitted
// the operator shape, which carries the registry rows, the secret refs and the
// key fingerprints.
func TestViewerReductionIsAppliedWithoutAPrincipal(t *testing.T) {
	s := &Server{Backend: NewFakeBackend()}
	w := httptest.NewRecorder()
	s.handleTenants(w, httptest.NewRequest(http.MethodGet, "/v1/tenants", nil))
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", w.Code, w.Body.String())
	}
	var body struct {
		Tenants []struct {
			Name     string          `json:"name"`
			Registry json.RawMessage `json:"registry"`
		} `json:"tenants"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if len(body.Tenants) == 0 {
		t.Fatal("the fixture backend returned no tenants; the test would assert nothing")
	}
	for _, row := range body.Tenants {
		if s := strings.TrimSpace(string(row.Registry)); s != "null" {
			t.Fatalf("a principal-free request got the operator shape for %q: registry = %s", row.Name, s)
		}
	}
}

// TestAbortHandlerPanicIsNotSwallowed: http.ErrAbortHandler is net/http's own
// signal for "this response is deliberately abandoned" — the server recognises
// it, logs nothing and closes the connection. Recovering it turned an
// intentional abort into a 500 written onto a connection the handler had given
// up on.
func TestAbortHandlerPanicIsNotSwallowed(t *testing.T) {
	logger := slog.New(slog.NewTextHandler(&bytes.Buffer{}, nil))
	h := recoverer(logger)(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		panic(http.ErrAbortHandler)
	}))
	defer func() {
		rec := recover()
		if rec != http.ErrAbortHandler {
			t.Fatalf("recovered %v; ErrAbortHandler must be re-panicked for net/http to handle it", rec)
		}
	}()
	h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/v1/fleet", nil))
}

// TestAnonymousRowsHaveNoPathParameters: the credential skip matches RAW
// request paths against matrix PATTERNS, which is exact only while every
// anonymous row is a literal path. NewRouter asserts it at start-up; this
// gives the assertion a name.
func TestAnonymousRowsHaveNoPathParameters(t *testing.T) {
	anonymous := 0
	for _, row := range authz.Matrix {
		if row.Role != "anonymous" {
			continue
		}
		anonymous++
		if strings.ContainsAny(row.Path, "{}") {
			t.Errorf("anonymous operation %s has a templated path %q", row.OperationID, row.Path)
		}
	}
	if anonymous == 0 {
		t.Fatal("no anonymous row in the matrix; the test would assert nothing")
	}
	// And the start-up assertion itself fires rather than shipping the hole.
	func() {
		defer func() {
			if recover() == nil {
				t.Error("assertAnonymousRowsArePlainPaths accepted a templated anonymous path")
			}
		}()
		assertAnonymousRowsArePlainPathsIn([]authz.Row{
			{OperationID: "ctlFake", Method: http.MethodGet, Path: "/v1/thing/{id}", Role: "anonymous"},
		})
	}()
}

// --------------------------------------------------------------------------
// Finding 8 — a blank issuer allowlist silently widened to the default
// --------------------------------------------------------------------------

// TestIssuerAllowlistSetButBlankIsRefused. Getenv cannot tell "unset" from
// "set to nothing", so a template that rendered the variable empty — or an
// operator who blanked it believing it narrowed the list — got the WIDEST
// setting there is, which is the opposite of what blanking an allowlist reads
// as. env.go has documented the refusal since PR-A; now it happens.
func TestIssuerAllowlistSetButBlankIsRefused(t *testing.T) {
	t.Setenv(EnvIdentityIssuerAllowlist, "")
	if _, err := newVerifier(true); err == nil {
		t.Fatal("a set-but-blank issuer allowlist started the daemon")
	} else if !strings.Contains(err.Error(), EnvIdentityIssuerAllowlist) {
		t.Fatalf("the refusal does not name the variable: %v", err)
	}

	t.Setenv(EnvIdentityIssuerAllowlist, "   ")
	if _, err := newVerifier(true); err == nil {
		t.Fatal("a whitespace-only issuer allowlist started the daemon")
	}

	t.Setenv(EnvIdentityIssuerAllowlist, "https://user.bv-brc.org/public_key")
	v, err := newVerifier(true)
	if err != nil {
		t.Fatalf("a narrowed allowlist was refused: %v", err)
	}
	if got := v.Allowlist(); len(got) != 1 {
		t.Fatalf("allowlist = %v; want the one configured issuer", got)
	}
}

// TestUnsetIssuerAllowlistTakesTheDefaults is the other half: UNSET is the
// documented way to say "the four canonical BV-BRC issuers".
func TestUnsetIssuerAllowlistTakesTheDefaults(t *testing.T) {
	if err := os.Unsetenv(EnvIdentityIssuerAllowlist); err != nil {
		t.Fatal(err)
	}
	v, err := newVerifier(true)
	if err != nil {
		t.Fatal(err)
	}
	if got := len(v.Allowlist()); got != len(auth.DefaultSigningSubjects) {
		t.Fatalf("unset allowlist gave %d issuers; want the %d defaults", got, len(auth.DefaultSigningSubjects))
	}
}

// TestKeyFetchFileRequiresFakeDrivers: the override names the file the BV-BRC
// SIGNING KEY is read from, so on a real daemon whoever can write that file
// chooses who may authenticate as anyone.
func TestKeyFetchFileRequiresFakeDrivers(t *testing.T) {
	t.Setenv(EnvIdentityKeyFetchFile, filepath.Join(t.TempDir(), "public_key.json"))
	if _, err := newVerifier(false); err == nil {
		t.Fatal("the test-only key-fetch override was accepted without --fake-drivers")
	}
	if _, err := newVerifier(true); err != nil {
		t.Fatalf("the override was refused WITH --fake-drivers: %v", err)
	}
}

// TestErrorCodesKeepOneStatusEach: the body-size refusal is reported as 422
// `validation` rather than a 413 precisely because error.json promises one
// status per code. Stated here so a later "413 would be more correct" change
// has to argue with the contract first.
func TestErrorCodesKeepOneStatusEach(t *testing.T) {
	if model.CodeValidation.HTTPStatus() != http.StatusUnprocessableEntity {
		t.Fatalf("validation is %d; the oversized-body refusal is written as that code", model.CodeValidation.HTTPStatus())
	}
}

// --------------------------------------------------------------------------
// Settings — a response that violated its own schema, and a hard-coded path
// --------------------------------------------------------------------------

// TestSettingsResponseIsNormalisedForTheContract.
//
// The registry is a file an operator can hand-edit and an older schema version
// can have written, so an image block with no version/digest/sif, or a ctl
// block with no ui_dist, is a shape that reaches the handler. Passing it
// through emitted a body that violates settings_response.json — empty
// `version` fails minLength, empty `digest` fails the sha256 pattern, empty
// `sif`/`ui_dist` fail AbsPath — so the daemon's own contract test passed
// while the wire response did not parse for its clients.
func TestSettingsResponseIsNormalisedForTheContract(t *testing.T) {
	f := registry.NewFleet("/srv/rag")
	f.Images.Qdrant = registry.Image{}        // nothing at all
	f.Images.Elasticsearch.Digest = "garbage" // present but not a sha256
	f.Images.Elasticsearch.Version = ""
	f.Ctl = registry.Ctl{} // no port, no ui_dist

	s := &Server{Backend: &FakeBackend{fleet: f, now: time.Now}}
	w := httptest.NewRecorder()
	s.handleSettings(w, httptest.NewRequest(http.MethodGet, "/v1/settings", nil))
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", w.Code, w.Body.String())
	}
	var body model.SettingsResponse
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}

	for name, im := range map[string]model.SettingsImage{
		"qdrant": body.Images.Qdrant, "elasticsearch": body.Images.Elasticsearch,
	} {
		if !strings.HasPrefix(im.SIF, "/") {
			t.Errorf("%s.sif = %q; the schema's AbsPath has no empty member", name, im.SIF)
		}
		if im.Version == "" {
			t.Errorf("%s.version is empty; minLength is 1", name)
		}
		if !digestRE.MatchString(im.Digest) {
			t.Errorf("%s.digest = %q; want ^sha256:[0-9a-f]{64}$", name, im.Digest)
		}
	}
	if body.Ctl.Port < 1024 || body.Ctl.Port > 65535 {
		t.Errorf("ctl.port = %d; outside the schema's range", body.Ctl.Port)
	}
	if !strings.HasPrefix(body.Ctl.UIDist, "/") {
		t.Errorf("ctl.ui_dist = %q; not an AbsPath", body.Ctl.UIDist)
	}

	// …and the derived path follows the registry's OWN rag_root. A hard-coded
	// "/rag/envs/ragstack" reported the deployment host's env to a daemon
	// serving a different tree — a value the caller never observed.
	if body.PythonEnvDefault != "/srv/rag/envs/ragstack" {
		t.Errorf("python_env_default = %q; want it derived from rag_root /srv/rag", body.PythonEnvDefault)
	}

	// A registry that DOES carry pins is passed through untouched.
	pinned := registry.NewFleet("/rag")
	pinned.Images.Qdrant = registry.Image{
		SIF: "/rag/apptainer/images/qdrant.sif", Version: "v1.12.4",
		Digest: "sha256:" + strings.Repeat("a", 64),
	}
	s2 := &Server{Backend: &FakeBackend{fleet: pinned, now: time.Now}}
	w2 := httptest.NewRecorder()
	s2.handleSettings(w2, httptest.NewRequest(http.MethodGet, "/v1/settings", nil))
	var body2 model.SettingsResponse
	if err := json.Unmarshal(w2.Body.Bytes(), &body2); err != nil {
		t.Fatal(err)
	}
	if body2.Images.Qdrant != (model.SettingsImage{
		SIF: "/rag/apptainer/images/qdrant.sif", Version: "v1.12.4",
		Digest: "sha256:" + strings.Repeat("a", 64),
	}) {
		t.Errorf("a pinned image was rewritten: %+v", body2.Images.Qdrant)
	}
}

// --------------------------------------------------------------------------
// The live backend — per-request probes and a doctor that disagreed with the CLI
// --------------------------------------------------------------------------

// countingDU records every Usage call, so a test can tell whether the backend
// reused the probe it was built with or made a new one per request.
type countingDU struct{ calls int }

func (c *countingDU) Usage(context.Context, string) (int64, error) {
	c.calls++
	return 0, nil
}

func liveBackendForTest(t *testing.T) *liveBackend {
	t.Helper()
	dir := t.TempDir()
	f := registry.NewFleet(dir)
	f.UpdatedAt, f.UpdatedBy = "2026-09-11T08:00:00Z", "test"
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
	return b
}

// TestLiveBackendReusesOneSetOfProbes.
//
// Fleet built a fresh fleet.Probes{} per request, so every /v1/fleet poll got
// a brand-new hostfacts.CachedDU — a cache whose entire purpose is to outlive
// one call. A dashboard polling every few seconds therefore re-ran `du -s -B1`
// over every tenant tree forever and the cache never hit once. The test
// substitutes the backend's OWN probe: with a per-request Probes{} it would
// never be consulted at all.
func TestLiveBackendReusesOneSetOfProbes(t *testing.T) {
	b := liveBackendForTest(t)
	if b.probes.Host == nil || b.probes.Prober == nil || b.probes.Disk == nil {
		t.Fatalf("the backend did not build its probes once: %+v", b.probes)
	}
	du := &countingDU{}
	b.probes.Disk = du
	before := b.probes.Disk

	for i := 0; i < 3; i++ {
		if _, err := b.Fleet(context.Background()); err != nil {
			t.Fatal(err)
		}
	}
	if b.probes.Disk != before {
		t.Fatal("Fleet replaced the backend's disk probe; the cache cannot outlive a request")
	}
	// The fixture fleet has no tenants, so Usage may legitimately be called 0
	// times; what must hold is that the backend's own probe is the one in use.
	f := registry.NewFleet(b.roots.RagRoot)
	f.Tenants["dev"] = registry.NewTenant("dev", "dev")
	f.DisplayOrder = []string{"dev"}
	b.FakeBackend.fleet = f
	if _, err := b.Fleet(context.Background()); err != nil {
		t.Fatal(err)
	}
	if du.calls == 0 {
		t.Fatal("the backend's disk probe was never consulted; Fleet built its own")
	}
}

// TestLiveDoctorUsesTheSameOptionsAsTheCLI.
//
// The CLI passed CtlUID; the HTTP path did not. The two checks that need it —
// `user_dropin_missing` and `runtime_dir_missing` — therefore never ran over
// HTTP, so `ragstack-ctl doctor` and GET /v1/doctor produced DIFFERENT hashes
// for the same host. The hash is what a plan pins and what
// --force-with-doctor-diff must quote, so a plan made by one could not be
// confirmed by the other.
func TestLiveDoctorUsesTheSameOptionsAsTheCLI(t *testing.T) {
	b := liveBackendForTest(t)
	if want := ctlUID(doctor.DefaultCtlUser); b.doctorOpts.CtlUID != want {
		t.Errorf("doctor CtlUID = %d; the CLI passes %d", b.doctorOpts.CtlUID, want)
	}
	if b.doctorOpts.RegistryPath != b.registryPath {
		t.Errorf("doctor RegistryPath = %q; want %q", b.doctorOpts.RegistryPath, b.registryPath)
	}

	// Scoping one run must not leave the scope on the shared options.
	if _, err := b.Doctor(context.Background(), "", "start"); err != nil {
		t.Fatal(err)
	}
	if b.doctorOpts.Op != "" || b.doctorOpts.Tenant != "" {
		t.Fatalf("one scoped run mutated the shared options: %+v", b.doctorOpts)
	}
}

// TestLiveBackendNeverRepairsOnLoad: a READ must not rewrite manifest.tsv.
// Repair-on-read is what reissued a live tenant's ports; the stale projection
// is a diagnostic that /v1/doctor reports, never something a GET fixes.
func TestLiveBackendNeverRepairsOnLoad(t *testing.T) {
	dir := t.TempDir()
	f := registry.NewFleet(dir)
	f.UpdatedAt, f.UpdatedBy = "2026-09-11T08:00:00Z", "test"
	raw, err := json.Marshal(f)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(dir, "registry.json")
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	manifest := registry.PathsFor(path).Manifest
	if err := os.WriteFile(manifest, []byte("stale\tcontent\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	before, err := os.ReadFile(manifest)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := newLiveBackendWithLogger(dir, path, slog.New(slog.NewTextHandler(&bytes.Buffer{}, nil))); err != nil {
		t.Fatalf("a stale projection must not fail the read: %v", err)
	}
	after, err := os.ReadFile(manifest)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(before, after) {
		t.Fatalf("building the backend rewrote %s:\nbefore %q\nafter  %q", manifest, before, after)
	}
}
