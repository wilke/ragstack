package auth

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
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
