package auth

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/ratelimit"
)

// TestCredentialHashIgnoresSchemeCaseAndSpacing is the rate limiter's key.
//
// Hashing the RAW Authorization header gave `Bearer t`, `bearer t` and
// `Bearer  t` three separate failure budgets for one token — three free
// guessing budgets bought with nothing but case and whitespace the scheme
// parser already throws away.
func TestCredentialHashIgnoresSchemeCaseAndSpacing(t *testing.T) {
	const token = "un=alice@patricbrc.org|tokenid=x|sig=deadbeef"
	variants := []string{
		"Bearer " + token,
		"bearer " + token,
		"BEARER " + token,
		"Bearer  " + token,
		"Bearer \t" + token,
	}
	want := ""
	for _, v := range variants {
		r := httptest.NewRequest(http.MethodGet, "/v1/fleet", nil)
		r.Header.Set(HeaderAuthorization, v)
		c := ReadCredential(r)
		if c.Kind != "bearer" || c.Value != token {
			t.Fatalf("%q parsed as kind=%q value=%q", v, c.Kind, c.Value)
		}
		if want == "" {
			want = c.Hash
			continue
		}
		if c.Hash != want {
			t.Fatalf("%q hashes to a different budget than %q; one token must have one budget", v, variants[0])
		}
	}

	// Sessions and keys stay in separate namespaces even for an identical
	// string, so one cannot spend the other's budget.
	sess := httptest.NewRequest(http.MethodGet, "/v1/fleet", nil)
	sess.Header.Set(HeaderAuthorization, "Session "+token)
	key := httptest.NewRequest(http.MethodGet, "/v1/fleet", nil)
	key.Header.Set(HeaderAPIKey, token)
	if ReadCredential(sess).Hash == ReadCredential(key).Hash {
		t.Fatal("a session id and an API key with the same value share one failure budget")
	}
}

// TestBoundKeyPrincipalIsLoaded: CTL_API_KEY_PRINCIPALS binds a ctl key to the
// identity it belongs to, which is what makes the body-key rule satisfiable
// for a bearer principal at all. A binding that is not an issuer:subject id
// would be a binding to nobody, so it is refused at load.
func TestBoundKeyPrincipalIsLoaded(t *testing.T) {
	const bound = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	const unbound = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
	env := map[string]string{
		EnvAPIKeys:          `["` + bound + `","` + unbound + `"]`,
		EnvAPIKeyRoles:      `{"` + bound + `":"operator","` + unbound + `":"operator"}`,
		EnvAPIKeyPrincipals: `{"` + bound + `":"bvbrc:alice@patricbrc.org"}`,
	}
	k, err := LoadKeys(func(name string) string { return env[name] })
	if err != nil {
		t.Fatal(err)
	}
	p, err := k.LookupKey(bound)
	if err != nil {
		t.Fatal(err)
	}
	if p.BoundSubject != "bvbrc:alice@patricbrc.org" {
		t.Fatalf("BoundSubject = %q", p.BoundSubject)
	}
	// The binding is not an authentication input: the key still authenticates
	// as itself.
	if p.Subject != "key:"+p.KeyFingerprint {
		t.Fatalf("a bound key authenticated as %q; a key is always its own principal", p.Subject)
	}
	u, err := k.LookupKey(unbound)
	if err != nil {
		t.Fatal(err)
	}
	if u.BoundSubject != "" {
		t.Fatalf("an unlisted key reported BoundSubject %q", u.BoundSubject)
	}

	env[EnvAPIKeyPrincipals] = `{"` + bound + `":"alice"}`
	if _, err := LoadKeys(func(name string) string { return env[name] }); err == nil {
		t.Fatal("a binding with no issuer prefix was accepted; it could never match a principal")
	}
}

// TestBackwardsWallClockDoesNotPinTheKey: the key cache ages against a
// MONOTONIC reading. With a wall clock, one NTP step backwards makes every
// cached key look fetched in the future, so a rotated-away key stays pinned
// for the length of the step and no token signed with the new one can verify.
func TestBackwardsWallClockDoesNotPinTheKey(t *testing.T) {
	vf := loadVectors(t)
	token := validToken(t, vf)
	ff := &fakeFetch{body: []byte(`{"pubkey": "` + otherPEM + `"}`)}
	wall := time.Unix(vf.Now, 0).UTC()
	var mono time.Duration
	v, err := New(Options{
		Allowlist:  vf.Allowlist,
		MinRefetch: time.Minute,
		Now:        func() time.Time { return wall },
		Mono:       func() time.Duration { return mono },
		Fetch:      ff.fetch,
	})
	if err != nil {
		t.Fatal(err)
	}
	// Cold fetch of the wrong key (1 fetch); the rotation refetch is rate-
	// limited by the cold one.
	if _, err := v.Authenticate(context.Background(), token); err == nil {
		t.Fatal("the wrong key verified a token")
	}
	if ff.count() != 1 {
		t.Fatalf("cold fetches = %d; want 1", ff.count())
	}

	// The issuer rotates, and the host's clock is stepped an hour BACKWARDS.
	// Monotonic time has moved past MinRefetch, so the refetch is allowed and
	// the token verifies.
	ff.mu.Lock()
	ff.body = publicKeyBody(t)
	ff.mu.Unlock()
	wall = wall.Add(-time.Hour)
	mono += 2 * time.Minute
	if _, err := v.Authenticate(context.Background(), token); err != nil {
		t.Fatalf("a backwards clock step pinned the stale key: %v", err)
	}
	if ff.count() != 2 {
		t.Fatalf("fetches = %d; want 2 (cold + the rotation refetch)", ff.count())
	}
}

// TestFailedKeyFetchIsNegativelyCached: with the key server down, every
// unverifiable token used to cost a fresh outbound fetch with a 5 s timeout —
// so anyone could turn a dead key server into a request-thread sink by
// replaying garbage. The failed ATTEMPT is remembered, like a successful one.
func TestFailedKeyFetchIsNegativelyCached(t *testing.T) {
	vf := loadVectors(t)
	token := validToken(t, vf)
	ff := &fakeFetch{err: errors.New("connection refused")}
	v, err := New(Options{
		Allowlist:  vf.Allowlist,
		MinRefetch: time.Minute,
		Now:        fixedNow(vf.Now),
		Fetch:      ff.fetch,
	})
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 20; i++ {
		if _, err := v.Authenticate(context.Background(), token); !errors.Is(err, ErrUnavailable) {
			t.Fatalf("attempt %d: err = %v; want ErrUnavailable", i, err)
		}
	}
	if got := ff.count(); got != 1 {
		t.Fatalf("20 requests against a dead key server made %d outbound fetches; want 1", got)
	}
}

// TestNegativeCacheExpiresWithMinRefetch: the negative cache is a rate limit,
// not a latch — once MinRefetch has passed, a key server that came back is
// tried again.
func TestNegativeCacheExpiresWithMinRefetch(t *testing.T) {
	vf := loadVectors(t)
	token := validToken(t, vf)
	ff := &fakeFetch{err: errors.New("connection refused")}
	var mono time.Duration
	v, err := New(Options{
		Allowlist:  vf.Allowlist,
		MinRefetch: time.Minute,
		Now:        fixedNow(vf.Now),
		Mono:       func() time.Duration { return mono },
		Fetch:      ff.fetch,
	})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := v.Authenticate(context.Background(), token); !errors.Is(err, ErrUnavailable) {
		t.Fatalf("err = %v; want ErrUnavailable", err)
	}
	ff.mu.Lock()
	ff.err, ff.body = nil, publicKeyBody(t)
	ff.mu.Unlock()
	mono += 2 * time.Minute
	if _, err := v.Authenticate(context.Background(), token); err != nil {
		t.Fatalf("the key server recovered but the negative cache latched: %v", err)
	}
	if got := ff.count(); got != 2 {
		t.Fatalf("fetches = %d; want 2 (the failure, then the retry past MinRefetch)", got)
	}
}

// --------------------------------------------------------------------------
// A dead key server is not a credential failure
// --------------------------------------------------------------------------

// TestUnavailableIdentityProviderIsNotCountedAsACredentialFailure.
//
// ErrUnavailable means the KEY SERVER did not answer: nothing whatever was
// learned about the credential. Counting it as a credential failure charged
// the caller for the provider's outage — an operator retrying during one burnt
// their own budget down to a 429 and was tarpitted on every attempt, so a
// provider outage became a lockout that outlived it. The answer is still 401
// (error.json maps one status per code and has no 503 code), but it is not
// recorded and not delayed, and its detail says which side failed.
func TestUnavailableIdentityProviderIsNotCountedAsACredentialFailure(t *testing.T) {
	vf := loadVectors(t)
	token := validToken(t, vf)
	rec := &recorder{}
	a := testResolver(t, rec)
	// A verifier whose key server is down: the token itself is the fixture's
	// valid one, so the ONLY reason it cannot be verified is the outage.
	v, err := New(Options{
		Allowlist: vf.Allowlist,
		Now:       fixedNow(vf.Now),
		Fetch:     (&fakeFetch{err: errors.New("connection refused")}).fetch,
	})
	if err != nil {
		t.Fatal(err)
	}
	a.Verifier = v
	// A tarpit that is ON and whose delay is recorded rather than served: the
	// assertion is that this path never asks for one, and a test must not pay
	// five real seconds to prove it.
	var tarpitted []time.Duration
	limiter := ratelimit.New(ratelimit.Config{
		PerCredential: 2, TarpitAt: 1, TarpitDelay: time.Hour,
		Sleep: func(context.Context, time.Duration) { tarpitted = append(tarpitted, time.Hour) },
	})
	a.Limiter = limiter

	h := a.Middleware(nil)(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		t.Error("an unverifiable credential reached the handler")
	}))
	for i := 0; i < 10; i++ {
		rec.status, rec.code, rec.detail = 0, "", ""
		h.ServeHTTP(httptest.NewRecorder(), request(map[string]string{HeaderAuthorization: "Bearer " + token}))
		if rec.status != http.StatusUnauthorized || rec.code != CodeAuthRequired {
			t.Fatalf("attempt %d: %d %s; want 401 %s", i, rec.status, rec.code, CodeAuthRequired)
		}
		if !strings.Contains(rec.detail, "identity provider unavailable") {
			t.Fatalf("attempt %d: detail %q does not name the provider outage", i, rec.detail)
		}
		if len(tarpitted) != 0 {
			t.Fatalf("attempt %d was tarpitted for a key-server outage", i)
		}
	}
	if n := limiter.Tracked(); n != 0 {
		t.Fatalf("a key-server outage left %d credential-failure entries; want 0", n)
	}
	if d := limiter.TarpitDelay(); d != 0 {
		t.Fatalf("a key-server outage turned the global tarpit on (%v)", d)
	}
	// A genuinely bad credential through the same resolver IS still counted:
	// the exemption is for the provider, not for the caller.
	h.ServeHTTP(httptest.NewRecorder(), request(map[string]string{HeaderAPIKey: "not-an-enrolled-key"}))
	if n := limiter.Tracked(); n != 1 {
		t.Fatalf("an unknown ctl key was not counted: %d entries", n)
	}
	if len(tarpitted) != 1 {
		t.Fatalf("a real credential failure was not tarpitted: %v", tarpitted)
	}
}

// TestStaleKeyIsUsedWhenTheKeyServerIsDown.
//
// Past the 24 h TTL a failed refetch answered `unavailable` even though a
// perfectly good key sat in the cache — so every BV-BRC operator was locked
// out of the control plane for exactly as long as the key server was down. A
// key that verified yesterday is a better answer than no answer; the refetch
// is still retried once per MinRefetch, so a rotation is picked up as soon as
// the server can be reached.
func TestStaleKeyIsUsedWhenTheKeyServerIsDown(t *testing.T) {
	vf := loadVectors(t)
	token := validToken(t, vf)
	ff := &fakeFetch{body: publicKeyBody(t)}
	var mono time.Duration
	v, err := New(Options{
		Allowlist:  vf.Allowlist,
		KeyTTL:     24 * time.Hour,
		MinRefetch: time.Minute,
		Now:        fixedNow(vf.Now),
		Mono:       func() time.Duration { return mono },
		Fetch:      ff.fetch,
		Logger:     slog.New(slog.NewTextHandler(io.Discard, nil)),
	})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := v.Authenticate(context.Background(), token); err != nil {
		t.Fatalf("cold verification: %v", err)
	}

	// The key server dies, and the cached key ages past its TTL.
	ff.mu.Lock()
	ff.err = errors.New("connection refused")
	ff.mu.Unlock()
	mono += 25 * time.Hour
	for i := 0; i < 5; i++ {
		if _, err := v.Authenticate(context.Background(), token); err != nil {
			t.Fatalf("attempt %d: a stale-but-cached key refused a good token: %v", i, err)
		}
		mono += time.Second
	}
	// One outbound attempt per MinRefetch, not one per request.
	if got := ff.count(); got != 2 {
		t.Fatalf("fetches = %d; want 2 (the cold one, then one retry)", got)
	}
	mono += 2 * time.Minute
	if _, err := v.Authenticate(context.Background(), token); err != nil {
		t.Fatalf("the stale key stopped working past MinRefetch: %v", err)
	}
	if got := ff.count(); got != 3 {
		t.Fatalf("fetches = %d; want 3 — the retry must keep happening", got)
	}

	// The server comes back with a rotated key; the stale one is dropped.
	ff.mu.Lock()
	ff.err, ff.body = nil, publicKeyBody(t)
	ff.mu.Unlock()
	mono += 2 * time.Minute
	if _, err := v.Authenticate(context.Background(), token); err != nil {
		t.Fatalf("the recovered key server was not used: %v", err)
	}
}

// TestNeverCachedKeyStaysUnavailable is the other half: `unavailable` is
// reserved for the case where the ctl genuinely cannot decide, because no key
// for that issuer was EVER fetched. A daemon that started while the key server
// was down must not authenticate anyone.
func TestNeverCachedKeyStaysUnavailable(t *testing.T) {
	vf := loadVectors(t)
	token := validToken(t, vf)
	v, err := New(Options{
		Allowlist: vf.Allowlist,
		Now:       fixedNow(vf.Now),
		Fetch:     (&fakeFetch{err: errors.New("connection refused")}).fetch,
	})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := v.Authenticate(context.Background(), token); !errors.Is(err, ErrUnavailable) {
		t.Fatalf("err = %v; want ErrUnavailable — nothing was ever cached", err)
	}
}

// --------------------------------------------------------------------------
// Credential-less failures need a budget of their own
// --------------------------------------------------------------------------

// TestCredentialLessFailuresAreBudgetedAndAnswered429.
//
// A request with no credential hashes to "" and Allow("") is always true, so
// credential-less traffic was tarpitted but never given a budget and never
// told to come back later: the one failure shape with no 429 at all. All of it
// is counted in one fixed bucket instead.
func TestCredentialLessFailuresAreBudgetedAndAnswered429(t *testing.T) {
	rec := &recorder{}
	a := testResolver(t, rec)
	limiter := ratelimit.New(ratelimit.Config{PerCredential: 3, TarpitAt: -1})
	a.Limiter = limiter
	h := a.Middleware(nil)(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		t.Error("a credential-less request reached the handler")
	}))

	for i := 0; i < 3; i++ {
		w := httptest.NewRecorder()
		h.ServeHTTP(w, request(nil))
		if rec.status != http.StatusUnauthorized {
			t.Fatalf("attempt %d: %d %s; want 401", i, rec.status, rec.code)
		}
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, request(nil))
	if rec.status != http.StatusTooManyRequests || rec.code != CodeRateLimited {
		t.Fatalf("over the budget: %d %s; want 429 %s", rec.status, rec.code, CodeRateLimited)
	}
	if ra := w.Header().Get("Retry-After"); ra == "" || ra == "0" {
		t.Fatalf("Retry-After = %q; the 429 must say when to come back", ra)
	}
	if n := limiter.Tracked(); n != 1 {
		t.Fatalf("credential-less failures produced %d buckets; want exactly the one anon bucket", n)
	}

	// A real credential is unaffected: the anon bucket must not be a way to
	// lock an operator out.
	rec.status, rec.code = 0, ""
	var reached bool
	ok := a.Middleware(nil)(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { reached = true }))
	ok.ServeHTTP(httptest.NewRecorder(), request(map[string]string{HeaderAPIKey: opKey}))
	if !reached {
		t.Fatalf("anonymous failures locked out a valid key: %d %s", rec.status, rec.code)
	}
}

// TestAnonBucketDoesNotRateLimitTheAnonymousOperation: the rate-limit
// middleware runs for GET /health too, and it is credential-less by design. If
// it consulted the anon bucket, a flood of credential-less 401s anywhere else
// would turn the daemon's public health probe into a 429.
func TestAnonBucketDoesNotRateLimitTheAnonymousOperation(t *testing.T) {
	rec := &recorder{}
	a := testResolver(t, rec)
	a.Limiter = ratelimit.New(ratelimit.Config{PerCredential: 1, TarpitAt: -1})
	for i := 0; i < 50; i++ {
		a.Limiter.Fail(AnonBucket)
	}
	var reached bool
	h := a.RateLimitMiddleware(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { reached = true }))
	h.ServeHTTP(httptest.NewRecorder(), request(nil))
	if !reached {
		t.Fatalf("a drained anon bucket refused an anonymous request before the skip could run: %d %s", rec.status, rec.code)
	}
}

// --------------------------------------------------------------------------
// CTL_ADMIN_SUBJECTS / CTL_VIEWER_SUBJECTS are principal ids
// --------------------------------------------------------------------------

// TestSubjectListsMustBeIssuerQualified.
//
// A resolved bearer principal is "bvbrc:<un>", so a bare "alice" in
// CTL_ADMIN_SUBJECTS enrols nobody: the daemon started, the list appeared to
// name the operator, and the operator was 403. The same check
// CTL_API_KEY_PRINCIPALS already made — refuse to start.
func TestSubjectListsMustBeIssuerQualified(t *testing.T) {
	for _, name := range []string{EnvAdminSubjects, EnvViewerSubjects} {
		if _, err := LoadKeys(env(map[string]string{name: "alice@patricbrc.org"})); err == nil {
			t.Fatalf("%s accepted a bare subject that could never match a principal id", name)
		} else if !strings.Contains(err.Error(), "issuer:subject") {
			t.Fatalf("%s: error %q does not say what the value must look like", name, err)
		}
		// One bad entry in a list of good ones is still a refusal.
		if _, err := LoadKeys(env(map[string]string{name: "bvbrc:alice@patricbrc.org,bob"})); err == nil {
			t.Fatalf("%s accepted a list with one unqualified entry", name)
		}
		if _, err := LoadKeys(env(map[string]string{name: "bvbrc:alice@patricbrc.org"})); err != nil {
			t.Fatalf("%s refused a well-formed principal id: %v", name, err)
		}
	}
}
