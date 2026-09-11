package auth

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func env(pairs map[string]string) func(string) string {
	return func(k string) string { return pairs[k] }
}

const (
	opKey     = "a0f1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f"
	viewerKey = "b1e2d3c4b5a69788796a5b4c3d2e1f00112233445566778899aabbccddeeff00"
)

func TestLoadKeysRolesAndLabels(t *testing.T) {
	k, err := LoadKeys(env(map[string]string{
		EnvAPIKeys:     `["` + opKey + `","` + viewerKey + `"]`,
		EnvAPIKeyRoles: `{"` + opKey + `":"operator","` + viewerKey + `":"viewer"}`,
		EnvAPIKeyNames: `{"` + opKey + `":"ops-laptop"}`,
	}))
	if err != nil {
		t.Fatal(err)
	}
	p, err := k.LookupKey(opKey)
	if err != nil {
		t.Fatal(err)
	}
	if p.Subject != "key:ops-laptop" || p.Role != RoleOperator || p.AuthMethod != MethodAPIKey {
		t.Fatalf("operator principal = %+v", p)
	}
	if p.ExpiresAt != nil {
		t.Error("a ctl key does not expire; it is revoked (me_response.json requires expires_at: null)")
	}
	if !strings.HasPrefix(p.KeyFingerprint, "sha256:") || len(p.KeyFingerprint) != 23 {
		t.Errorf("fingerprint %q is not the registry's sha256:<16 hex> shape", p.KeyFingerprint)
	}
	if strings.Contains(p.KeyFingerprint, opKey[:8]) {
		t.Error("the fingerprint contains a prefix of the key itself")
	}

	v, err := k.LookupKey(viewerKey)
	if err != nil {
		t.Fatal(err)
	}
	if v.Role != RoleViewer {
		t.Fatalf("viewer role = %q", v.Role)
	}
	// No label configured ⇒ the fingerprint stands in, so a principal id is
	// always legible and never the key.
	if v.Subject != "key:"+Fingerprint(viewerKey) {
		t.Fatalf("unlabelled principal = %q", v.Subject)
	}
}

func TestUnknownKeyIs401AndEnrolledWithoutRoleIs403(t *testing.T) {
	k, err := LoadKeys(env(map[string]string{
		EnvAPIKeys:     opKey + "," + viewerKey, // bare comma list, like _split_list_env
		EnvAPIKeyRoles: `{"` + opKey + `":"operator"}`,
	}))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := k.LookupKey("nope"); !errors.Is(err, ErrInvalid) {
		t.Fatalf("unknown key: err = %v; want ErrInvalid (401)", err)
	}
	// Enrolled but unroled: authentication succeeds and authorization does not.
	// Enrolment and authorization are two decisions; the second is never
	// implied by the first.
	_, err = k.LookupKey(viewerKey)
	var unlisted *ErrUnlisted
	if !errors.As(err, &unlisted) {
		t.Fatalf("roleless key: err = %v; want *ErrUnlisted (403)", err)
	}
}

func TestBearerSubjectsGetRolesOnlyFromTheCtlLists(t *testing.T) {
	k, err := LoadKeys(env(map[string]string{
		EnvAdminSubjects:  "bvbrc:alice@patricbrc.org",
		EnvViewerSubjects: `["bvbrc:bob@patricbrc.org"]`,
	}))
	if err != nil {
		t.Fatal(err)
	}
	exp := time.Unix(4102444800, 0).UTC()
	for _, tc := range []struct {
		un   string
		want string
	}{{"alice@patricbrc.org", RoleOperator}, {"bob@patricbrc.org", RoleViewer}} {
		p, err := k.PrincipalForIdentity(Identity{Subject: tc.un, Issuer: Issuer, TokenID: "t", ExpiresAt: &exp})
		if err != nil {
			t.Fatalf("%s: %v", tc.un, err)
		}
		if p.Role != tc.want || p.Subject != "bvbrc:"+tc.un || p.AuthMethod != MethodBearer {
			t.Fatalf("%s: principal = %+v", tc.un, p)
		}
		if p.ExpiresAt == nil || !p.ExpiresAt.Equal(exp) {
			t.Fatalf("%s: expires_at = %v; want the token's expiry", tc.un, p.ExpiresAt)
		}
	}

	// The case the contract singles out: a token that VERIFIES for a subject
	// nobody enrolled is 403, never a read-only default.
	_, err = k.PrincipalForIdentity(Identity{Subject: "unlisted@example.org", Issuer: Issuer, TokenID: "t"})
	var unlisted *ErrUnlisted
	if !errors.As(err, &unlisted) {
		t.Fatalf("unlisted subject: err = %v; want *ErrUnlisted", err)
	}
	if unlisted.Subject != "bvbrc:unlisted@example.org" {
		t.Fatalf("unlisted subject id = %q", unlisted.Subject)
	}
}

func TestAdminListingIsNotDemotedByAViewerListing(t *testing.T) {
	k, err := LoadKeys(env(map[string]string{
		EnvAdminSubjects:  "bvbrc:alice@patricbrc.org",
		EnvViewerSubjects: "bvbrc:alice@patricbrc.org",
	}))
	if err != nil {
		t.Fatal(err)
	}
	role, err := k.RoleForSubject("bvbrc:alice@patricbrc.org")
	if err != nil || role != RoleOperator {
		t.Fatalf("role = %q, err = %v; a subject on both lists keeps the higher role deterministically", role, err)
	}
}

func TestBadRoleValueIsARefusedConfiguration(t *testing.T) {
	_, err := LoadKeys(env(map[string]string{
		EnvAPIKeys:     `["` + opKey + `"]`,
		EnvAPIKeyRoles: `{"` + opKey + `":"admin"}`,
	}))
	if err == nil {
		t.Fatal("a role outside {viewer, operator} was accepted; a typo would silently produce a 403-everywhere key")
	}
}

func TestRoleAtLeast(t *testing.T) {
	cases := []struct {
		have, need string
		want       bool
	}{
		{RoleOperator, RoleOperator, true},
		{RoleOperator, RoleViewer, true},
		{RoleViewer, RoleViewer, true},
		{RoleViewer, RoleOperator, false},
		{"", RoleViewer, false},
		{"", "anonymous", true},
		{RoleViewer, "admin", false}, // deny by default on an unknown requirement
	}
	for _, c := range cases {
		if got := RoleAtLeast(c.have, c.need); got != c.want {
			t.Errorf("RoleAtLeast(%q, %q) = %v; want %v", c.have, c.need, got, c.want)
		}
	}
}

// --------------------------------------------------------------------------
// ReadCredential / Middleware
// --------------------------------------------------------------------------

func request(headers map[string]string) *http.Request {
	r := httptest.NewRequest(http.MethodGet, "/v1/fleet", nil)
	for k, v := range headers {
		r.Header.Set(k, v)
	}
	return r
}

func TestReadCredential(t *testing.T) {
	cases := []struct {
		name    string
		headers map[string]string
		kind    string
		value   string
	}{
		{"none", nil, "none", ""},
		{"api key", map[string]string{HeaderAPIKey: opKey}, "api_key", opKey},
		{"both", map[string]string{HeaderAPIKey: opKey, HeaderAuthorization: "Bearer x"}, "both", ""},
		{"bearer", map[string]string{HeaderAuthorization: "Bearer tok"}, "bearer", "tok"},
		{"bearer lowercase scheme", map[string]string{HeaderAuthorization: "bearer tok"}, "bearer", "tok"},
		{"bare token", map[string]string{HeaderAuthorization: "un=a|sig=ff"}, "bearer", "un=a|sig=ff"},
		{"session", map[string]string{HeaderAuthorization: "Session deadbeef"}, "session", "deadbeef"},
		{"basic is treated as a token and fails verification", map[string]string{HeaderAuthorization: "Basic Zm9vOmJhcg=="}, "bearer", "Basic Zm9vOmJhcg=="},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := ReadCredential(request(c.headers))
			if got.Kind != c.kind || got.Value != c.value {
				t.Fatalf("kind/value = %q/%q; want %q/%q", got.Kind, got.Value, c.kind, c.value)
			}
			if c.kind == "none" || c.kind == "both" {
				if got.Hash != "" {
					t.Error("nothing verifiable was presented, so there is nothing to key the rate limiter on")
				}
			} else if len(got.Hash) != 64 {
				t.Errorf("hash %q is not a sha256 hex digest", got.Hash)
			}
		})
	}
}

type recorder struct {
	status int
	code   string
	detail string
}

func (rec *recorder) reject(w http.ResponseWriter, _ *http.Request, status int, code, detail string, _ map[string]any) {
	rec.status, rec.code, rec.detail = status, code, detail
	w.WriteHeader(status)
}

func testResolver(t *testing.T, rec *recorder) *Resolver {
	t.Helper()
	k, err := LoadKeys(env(map[string]string{
		EnvAPIKeys:       `["` + opKey + `","` + viewerKey + `"]`,
		EnvAPIKeyRoles:   `{"` + opKey + `":"operator","` + viewerKey + `":"viewer"}`,
		EnvAdminSubjects: "bvbrc:alice@patricbrc.org",
	}))
	if err != nil {
		t.Fatal(err)
	}
	vf := loadVectors(t)
	ff := &fakeFetch{body: publicKeyBody(t)}
	v, err := New(Options{Allowlist: vf.Allowlist, Now: fixedNow(vf.Now), Fetch: ff.fetch})
	if err != nil {
		t.Fatal(err)
	}
	return &Resolver{Keys: k, Verifier: v, Reject: rec.reject}
}

func TestMiddlewareAnswers(t *testing.T) {
	vf := loadVectors(t)
	// The fixture's "valid" token is alice@patricbrc.org, who is enrolled as
	// an operator above; there is no vector for a different verified subject,
	// so the unlisted case is covered by the keys-level test and by the
	// conformance suite's RAGSTACK_CTL_UNLISTED_BEARER.
	valid := validToken(t, vf)

	cases := []struct {
		name    string
		headers map[string]string
		status  int
		code    string
	}{
		{"none", nil, http.StatusUnauthorized, CodeAuthRequired},
		{"both", map[string]string{HeaderAPIKey: opKey, HeaderAuthorization: "Bearer " + valid}, http.StatusBadRequest, CodeBothCredentials},
		{"unknown key", map[string]string{HeaderAPIKey: "nope"}, http.StatusUnauthorized, CodeAuthRequired},
		{"garbage bearer", map[string]string{HeaderAuthorization: "Bearer not-a-token"}, http.StatusUnauthorized, CodeAuthRequired},
		{"unknown session", map[string]string{HeaderAuthorization: "Session 0123456789abcdef"}, http.StatusUnauthorized, CodeAuthRequired},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			rec := &recorder{}
			a := testResolver(t, rec)
			var reached bool
			h := a.Middleware(nil)(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { reached = true }))
			h.ServeHTTP(httptest.NewRecorder(), request(c.headers))
			if reached {
				t.Fatal("the handler was reached with an unusable credential")
			}
			if rec.status != c.status || rec.code != c.code {
				t.Fatalf("%d %s; want %d %s (%s)", rec.status, rec.code, c.status, c.code, rec.detail)
			}
		})
	}

	t.Run("valid key reaches the handler", func(t *testing.T) {
		rec := &recorder{}
		a := testResolver(t, rec)
		var got Principal
		h := a.Middleware(nil)(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
			got, _ = PrincipalFromContext(r.Context())
		}))
		h.ServeHTTP(httptest.NewRecorder(), request(map[string]string{HeaderAPIKey: opKey}))
		if got.Role != RoleOperator || got.AuthMethod != MethodAPIKey {
			t.Fatalf("principal = %+v (%d %s)", got, rec.status, rec.code)
		}
	})

	t.Run("valid bearer for an enrolled subject reaches the handler", func(t *testing.T) {
		rec := &recorder{}
		a := testResolver(t, rec)
		var got Principal
		h := a.Middleware(nil)(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
			got, _ = PrincipalFromContext(r.Context())
		}))
		h.ServeHTTP(httptest.NewRecorder(), request(map[string]string{HeaderAuthorization: "Bearer " + valid}))
		if got.Subject != "bvbrc:alice@patricbrc.org" || got.Role != RoleOperator || got.AuthMethod != MethodBearer {
			t.Fatalf("principal = %+v (%d %s %s)", got, rec.status, rec.code, rec.detail)
		}
	})

	t.Run("verified but unlisted subject is 403", func(t *testing.T) {
		rec := &recorder{}
		a := testResolver(t, rec)
		// Same verified token, but the daemon enrols nobody.
		empty, err := LoadKeys(env(nil))
		if err != nil {
			t.Fatal(err)
		}
		a.Keys = empty
		h := a.Middleware(nil)(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
			t.Error("an unlisted subject reached the handler — that is a default role")
		}))
		h.ServeHTTP(httptest.NewRecorder(), request(map[string]string{HeaderAuthorization: "Bearer " + valid}))
		if rec.status != http.StatusForbidden || rec.code != CodeForbidden {
			t.Fatalf("%d %s; want 403 forbidden — verified is not the same answer as unknown", rec.status, rec.code)
		}
	})

	t.Run("skip predicate lets an anonymous operation through", func(t *testing.T) {
		rec := &recorder{}
		a := testResolver(t, rec)
		var reached bool
		h := a.Middleware(func(*http.Request) bool { return true })(
			http.HandlerFunc(func(http.ResponseWriter, *http.Request) { reached = true }))
		h.ServeHTTP(httptest.NewRecorder(), request(nil))
		if !reached {
			t.Fatal("the anonymous operation was refused")
		}
	})
}

// TestResolveSessionUsesTheStore covers the session branch without importing
// package session (which imports this one).
type fakeSessions map[string]Principal

func (f fakeSessions) Principal(id string) (Principal, error) {
	p, ok := f[id]
	if !ok {
		return Principal{}, errors.New("not found")
	}
	return p, nil
}

func TestResolveSession(t *testing.T) {
	rec := &recorder{}
	a := testResolver(t, rec)
	a.Sessions = fakeSessions{
		"live":     {Subject: "key:ops", Role: RoleOperator, AuthMethod: MethodSession},
		"unroled":  {Subject: "bvbrc:x@y", Role: "", AuthMethod: MethodSession},
		"whatever": {Subject: "key:v", Role: RoleViewer, AuthMethod: MethodSession},
	}
	p, err := a.Resolve(context.Background(), Credential{Kind: "session", Value: "live"})
	if err != nil || p.AuthMethod != MethodSession || p.Role != RoleOperator {
		t.Fatalf("live session: %+v %v", p, err)
	}
	if _, err := a.Resolve(context.Background(), Credential{Kind: "session", Value: "gone"}); !errors.Is(err, ErrInvalid) {
		t.Fatalf("unknown session: err = %v; want ErrInvalid (401)", err)
	}
	// A session outlives its subject's enrolment: the role is re-checked, not
	// grandfathered.
	_, err = a.Resolve(context.Background(), Credential{Kind: "session", Value: "unroled"})
	var unlisted *ErrUnlisted
	if !errors.As(err, &unlisted) {
		t.Fatalf("de-enrolled session: err = %v; want *ErrUnlisted (403)", err)
	}
}
