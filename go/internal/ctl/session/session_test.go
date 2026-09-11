package session

import (
	"errors"
	"regexp"
	"strconv"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/auth"
)

// sessionIDRE is session_response.json's pattern, restated: if this test and
// the contract ever disagree, the admin UI's session breaks silently.
var sessionIDRE = regexp.MustCompile(`^[0-9a-f]{64}$`)

func operator() auth.Principal {
	return auth.Principal{Subject: "key:ops", Role: auth.RoleOperator, AuthMethod: auth.MethodAPIKey, KeyFingerprint: "sha256:0123456789abcdef"}
}

func TestCreateMintsAContractShapedSession(t *testing.T) {
	now := time.Date(2026, 9, 11, 12, 0, 0, 0, time.UTC)
	s := NewMemoryStoreWith(func() time.Time { return now }, TTL)
	sess, err := s.Create(operator())
	if err != nil {
		t.Fatal(err)
	}
	if !sessionIDRE.MatchString(sess.ID) {
		t.Fatalf("session id %q is not 32 random bytes as hex", sess.ID)
	}
	if want := now.Add(8 * time.Hour); !sess.ExpiresAt.Equal(want) {
		t.Fatalf("expires_at = %v; want %v (the contract's 8 h)", sess.ExpiresAt, want)
	}
	// A session authenticates AS a session whatever minted it — /v1/me must
	// say so, and the matrix's `session: false` rows must be able to tell.
	if sess.Principal.AuthMethod != auth.MethodSession {
		t.Fatalf("auth_method = %q; want %q", sess.Principal.AuthMethod, auth.MethodSession)
	}
	if sess.Principal.Subject != "key:ops" || sess.Principal.Role != auth.RoleOperator {
		t.Fatalf("the session is not bound to its subject and role: %+v", sess.Principal)
	}
	if sess.Principal.ExpiresAt == nil || !sess.Principal.ExpiresAt.Equal(sess.ExpiresAt) {
		t.Fatal("the principal's expiry must be the session's, so /v1/me and the create response agree")
	}
}

func TestIDsAreUnique(t *testing.T) {
	s := NewMemoryStore()
	seen := map[string]bool{}
	for i := 0; i < 200; i++ {
		sess, err := s.Create(operator())
		if err != nil {
			t.Fatal(err)
		}
		if seen[sess.ID] {
			t.Fatalf("duplicate session id after %d mints", i)
		}
		seen[sess.ID] = true
	}
}

func TestGetRevokeAndSubjectRevoke(t *testing.T) {
	s := NewMemoryStore()
	a, _ := s.Create(operator())
	b, _ := s.Create(operator())
	other, _ := s.Create(auth.Principal{Subject: "key:other", Role: auth.RoleViewer})

	if _, err := s.Get(a.ID); err != nil {
		t.Fatal(err)
	}
	s.Revoke(a.ID)
	if _, err := s.Get(a.ID); !errors.Is(err, ErrNotFound) {
		t.Fatalf("revoked session still resolves: %v", err)
	}
	// Idempotent: DELETE /v1/session is 204 whether or not the id was live.
	s.Revoke(a.ID)

	if n := s.RevokeSubject("key:ops"); n != 1 {
		t.Fatalf("RevokeSubject removed %d; want 1 (b)", n)
	}
	if _, err := s.Get(b.ID); !errors.Is(err, ErrNotFound) {
		t.Fatal("log-me-out-everywhere left a session of the same subject alive")
	}
	if _, err := s.Get(other.ID); err != nil {
		t.Fatal("log-me-out-everywhere revoked ANOTHER subject's session")
	}
}

func TestExpiryAndSweep(t *testing.T) {
	now := time.Date(2026, 9, 11, 12, 0, 0, 0, time.UTC)
	s := NewMemoryStoreWith(func() time.Time { return now }, time.Hour)
	sess, err := s.Create(operator())
	if err != nil {
		t.Fatal(err)
	}
	// Exactly at the expiry is expired — the same `<= now` rule the token
	// verifier uses, so the two credentials do not disagree by one second.
	now = sess.ExpiresAt
	if _, err := s.Get(sess.ID); !errors.Is(err, ErrNotFound) {
		t.Fatalf("a session at exactly its expiry still resolves: %v", err)
	}
	if s.Len() != 0 {
		t.Fatalf("expired sessions were not swept: %d live", s.Len())
	}
}

func TestPrincipalSatisfiesTheAuthSeam(t *testing.T) {
	var _ auth.SessionResolver = NewMemoryStore()
	s := NewMemoryStore()
	sess, _ := s.Create(operator())
	p, err := s.Principal(sess.ID)
	if err != nil || p.AuthMethod != auth.MethodSession {
		t.Fatalf("Principal(%q) = %+v, %v", sess.ID, p, err)
	}
	if _, err := s.Principal("0000"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("unknown id: %v", err)
	}
}

func TestStoreInterfaceIsSatisfied(t *testing.T) {
	// PR-C swaps in a sqlite store behind this interface; the assertion is
	// what makes that a constructor change rather than a refactor.
	var _ Store = NewMemoryStore()
}

// --------------------------------------------------------------------------
// The session must never outlive the credential that minted it
// --------------------------------------------------------------------------

// TestSessionNeverOutlivesTheMintingCredential.
//
// A BV-BRC token carries its own `expiry`. Stamping the full 8 h TTL on a
// session minted from a token with ten minutes left turned a nearly dead
// credential into a fresh day-long one: the session kept authenticating a
// subject whose token the issuer had already retired.
func TestSessionNeverOutlivesTheMintingCredential(t *testing.T) {
	now := time.Date(2026, 9, 11, 12, 0, 0, 0, time.UTC)
	s := NewMemoryStoreWith(func() time.Time { return now }, TTL)

	shortly := now.Add(10 * time.Minute)
	p := operator()
	p.ExpiresAt = &shortly
	sess, err := s.Create(p)
	if err != nil {
		t.Fatal(err)
	}
	if !sess.ExpiresAt.Equal(shortly) {
		t.Fatalf("expires_at = %v; want the credential's own expiry %v", sess.ExpiresAt, shortly)
	}
	if sess.Principal.ExpiresAt == nil || !sess.Principal.ExpiresAt.Equal(shortly) {
		t.Fatalf("the principal reported %v; /v1/me and the create response must agree", sess.Principal.ExpiresAt)
	}

	// A credential that outlives the TTL does NOT extend it: 8 h is the cap,
	// the credential's expiry is only ever a floor on it.
	far := now.Add(30 * 24 * time.Hour)
	p.ExpiresAt = &far
	long, err := s.Create(p)
	if err != nil {
		t.Fatal(err)
	}
	if want := now.Add(TTL); !long.ExpiresAt.Equal(want) {
		t.Fatalf("expires_at = %v; want the 8 h TTL %v", long.ExpiresAt, want)
	}

	// A ctl API key has no expiry at all: the TTL stands.
	noExpiry, err := s.Create(operator())
	if err != nil {
		t.Fatal(err)
	}
	if want := now.Add(TTL); !noExpiry.ExpiresAt.Equal(want) {
		t.Fatalf("a key-minted session expires at %v; want %v", noExpiry.ExpiresAt, want)
	}
}

// TestCreateRefusesAnAlreadyExpiredPrincipal: the exchange must not launder an
// expired token into a live session. `expiry <= now` is expired, the same rule
// the verifier and Get use.
func TestCreateRefusesAnAlreadyExpiredPrincipal(t *testing.T) {
	now := time.Date(2026, 9, 11, 12, 0, 0, 0, time.UTC)
	s := NewMemoryStoreWith(func() time.Time { return now }, TTL)
	for _, expiry := range []time.Time{now.Add(-time.Second), now} {
		p := operator()
		p.ExpiresAt = &expiry
		if _, err := s.Create(p); !errors.Is(err, ErrExpiredPrincipal) {
			t.Fatalf("Create with expiry %v returned %v; want ErrExpiredPrincipal", expiry, err)
		}
	}
	if s.Len() != 0 {
		t.Fatalf("a refused exchange still left %d sessions", s.Len())
	}
}

// --------------------------------------------------------------------------
// The store is state a caller can grow
// --------------------------------------------------------------------------

// TestGetSweepsAtMostOncePerInterval: Get used to walk the whole map under the
// lock on EVERY read, so every request was O(live sessions) — a cost a caller
// could raise just by minting. The walk is amortised now, the way the rate
// limiter's is.
func TestGetSweepsAtMostOncePerInterval(t *testing.T) {
	now := time.Date(2026, 9, 11, 12, 0, 0, 0, time.UTC)
	s := NewMemoryStoreBounded(func() time.Time { return now }, TTL, 1000, 1000)
	sess, err := s.Create(operator())
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 500; i++ {
		if _, err := s.Get(sess.ID); err != nil {
			t.Fatal(err)
		}
	}
	s.mu.Lock()
	sweeps := s.sweeps
	s.mu.Unlock()
	if sweeps != 0 {
		t.Fatalf("%d sweeps inside one interval; the cost must not be per read", sweeps)
	}

	// Past the interval exactly one sweep runs, and it collects.
	now = now.Add(TTL + time.Minute)
	if _, err := s.Get(sess.ID); !errors.Is(err, ErrNotFound) {
		t.Fatalf("an expired session resolved: %v", err)
	}
	for i := 0; i < 10; i++ {
		_, _ = s.Get(sess.ID)
	}
	s.mu.Lock()
	sweeps, held := s.sweeps, len(s.sessions)
	s.mu.Unlock()
	if sweeps != 1 {
		t.Fatalf("sweeps = %d after crossing the interval; want exactly 1", sweeps)
	}
	if held != 0 {
		t.Fatalf("the sweep left %d expired sessions in the map", held)
	}
}

// TestPerSubjectCapEvictsTheOldest: one subject cannot hold the store open by
// re-exchanging. The cap retires that subject's oldest session and nobody
// else's.
func TestPerSubjectCapEvictsTheOldest(t *testing.T) {
	now := time.Date(2026, 9, 11, 12, 0, 0, 0, time.UTC)
	s := NewMemoryStoreBounded(func() time.Time { return now }, TTL, 3, 1000)

	other, err := s.Create(auth.Principal{Subject: "key:other", Role: auth.RoleViewer})
	if err != nil {
		t.Fatal(err)
	}
	ids := make([]string, 0, 6)
	for i := 0; i < 6; i++ {
		now = now.Add(time.Second)
		sess, err := s.Create(operator())
		if err != nil {
			t.Fatal(err)
		}
		ids = append(ids, sess.ID)
	}
	// Three newest survive; the three oldest were retired.
	for _, id := range ids[:3] {
		if _, err := s.Get(id); !errors.Is(err, ErrNotFound) {
			t.Fatalf("session %s survived the per-subject cap of 3", id[:8])
		}
	}
	for _, id := range ids[3:] {
		if _, err := s.Get(id); err != nil {
			t.Fatalf("the cap evicted a session it should have kept: %v", err)
		}
	}
	// A DIFFERENT subject is untouched: one operator logging in repeatedly
	// must never log another one out.
	if _, err := s.Get(other.ID); err != nil {
		t.Fatalf("the per-subject cap evicted another subject's session: %v", err)
	}
}

// TestGlobalCapBoundsTheStore is the last resort: many subjects, each under
// the per-subject cap, must still not grow the map without limit.
func TestGlobalCapBoundsTheStore(t *testing.T) {
	now := time.Date(2026, 9, 11, 12, 0, 0, 0, time.UTC)
	const max = 20
	s := NewMemoryStoreBounded(func() time.Time { return now }, TTL, 16, max)
	var last string
	for i := 0; i < 10*max; i++ {
		now = now.Add(time.Second)
		sess, err := s.Create(auth.Principal{Subject: "key:" + strconv.Itoa(i), Role: auth.RoleViewer})
		if err != nil {
			t.Fatal(err)
		}
		last = sess.ID
	}
	if n := s.Len(); n > max {
		t.Fatalf("the store grew to %d sessions with a cap of %d", n, max)
	}
	// Eviction is oldest-first, so the session just minted is the one kept.
	if _, err := s.Get(last); err != nil {
		t.Fatalf("the global cap evicted the newest session: %v", err)
	}
}

// TestCapsPreferAnExpiredSessionOverALiveOne: the sweep is time-gated, so the
// map can hold expired entries. Retiring a live session while a dead one sat
// next to it would log an operator out for nothing.
func TestCapsPreferAnExpiredSessionOverALiveOne(t *testing.T) {
	now := time.Date(2026, 9, 11, 12, 0, 0, 0, time.UTC)
	s := NewMemoryStoreBounded(func() time.Time { return now }, time.Hour, 2, 1000)

	dead, err := s.Create(operator()) // oldest, and will be expired
	if err != nil {
		t.Fatal(err)
	}
	now = now.Add(30 * time.Minute)
	live, err := s.Create(operator())
	if err != nil {
		t.Fatal(err)
	}
	// Past `dead`'s expiry but inside the sweep interval of the last sweep,
	// so `dead` is still occupying a slot when the third session arrives.
	now = now.Add(31 * time.Minute)
	if _, err := s.Create(operator()); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Get(dead.ID); !errors.Is(err, ErrNotFound) {
		t.Fatal("the expired session was not the one retired")
	}
	if _, err := s.Get(live.ID); err != nil {
		t.Fatalf("a live session was retired while an expired one sat next to it: %v", err)
	}
}
