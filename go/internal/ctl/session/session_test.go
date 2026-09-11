package session

import (
	"errors"
	"regexp"
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
