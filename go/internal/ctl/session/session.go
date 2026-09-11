// Package session holds the control plane's browser sessions: an opaque,
// server-side, subject-bound id a verified credential is exchanged for, valid
// for 8 hours and accepted for READS ONLY.
//
// Why sessions exist at all: the admin UI is mounted publicly by decision, and
// the alternative to a session is the browser holding a ctl API key in storage
// for the length of a working day. A session carries no authority beyond the
// role of the subject it was minted for, cannot mint another session, and
// cannot authorize a mutation — every mutation re-presents the ctl key in the
// request body (contracts/ctl/openapi.yaml, `ctl_api_key`).
//
// PR-A ships the in-memory store. PR-C adds a sqlite-backed one behind the
// same Store interface so a daemon restart does not log every operator out
// mid-job; the interface exists now so that change is a constructor swap.
package session

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"sync"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/auth"
)

// TTL is how long a session lives. Fixed at 8 hours by the contract
// (session_response.json); not configurable, because a browser that can ask
// for a longer one is a browser that keeps a credential for longer.
const TTL = 8 * time.Hour

// ErrNotFound is returned for an unknown, expired or revoked session. The
// three are ONE answer on purpose: the caller learns nothing from which.
var ErrNotFound = errors.New("session not found")

// Session is one minted session. The id is never stored anywhere else and
// never logged; Principal is what the session authenticates as.
type Session struct {
	ID        string
	Principal auth.Principal
	CreatedAt time.Time
	ExpiresAt time.Time
}

// Store is the session backend. PR-C's sqlite implementation satisfies it.
type Store interface {
	// Create mints a session for principal. The returned Session carries the
	// only copy of the id the caller will ever see.
	Create(p auth.Principal) (Session, error)
	// Get returns a live session, or ErrNotFound.
	Get(id string) (Session, error)
	// Revoke removes one session. Idempotent: revoking an unknown session is
	// not an error (DELETE /v1/session is 204 either way).
	Revoke(id string)
	// RevokeSubject removes EVERY session of one subject — the "log me out
	// everywhere" path DELETE /v1/session takes when it is called with a key
	// or bearer credential rather than with the session itself. Returns how
	// many were removed.
	RevokeSubject(subject string) int
	// Len reports live sessions (for doctor and tests; never the ids).
	Len() int
}

// MemoryStore is the in-process Store. Safe for concurrent use.
type MemoryStore struct {
	mu       sync.Mutex
	sessions map[string]Session
	now      func() time.Time
	ttl      time.Duration
	// newID is the id source; injected only by the test that proves a
	// collision cannot silently overwrite a live session.
	newID func() (string, error)
}

// NewMemoryStore returns an empty store with the contract's 8 h TTL.
func NewMemoryStore() *MemoryStore { return NewMemoryStoreWith(time.Now, TTL) }

// NewMemoryStoreWith is NewMemoryStore with the clock and TTL injected.
func NewMemoryStoreWith(now func() time.Time, ttl time.Duration) *MemoryStore {
	if now == nil {
		now = time.Now
	}
	if ttl <= 0 {
		ttl = TTL
	}
	return &MemoryStore{sessions: map[string]Session{}, now: now, ttl: ttl, newID: NewID}
}

// NewID returns a fresh session id: 32 random bytes, hex — the 64 lowercase
// hex characters session_response.json pins. Opaque by construction: it
// encodes nothing about the subject, so it cannot be forged from one.
func NewID() (string, error) {
	var b [32]byte
	if _, err := rand.Read(b[:]); err != nil {
		// Unlike a request id, there is no acceptable fallback here: a
		// guessable session id is a credential anyone can mint. Fail the
		// exchange instead (the caller still has its original credential).
		return "", err
	}
	return hex.EncodeToString(b[:]), nil
}

// Create mints a session bound to p's subject and role.
func (s *MemoryStore) Create(p auth.Principal) (Session, error) {
	id, err := s.newID()
	if err != nil {
		return Session{}, err
	}
	now := s.now()
	sess := Session{
		ID: id,
		// The session authenticates AS a session, whatever minted it: /v1/me
		// must report `session`, and the authz matrix's `session: false` rows
		// must be able to tell the two apart.
		Principal: auth.Principal{
			Subject:        p.Subject,
			Role:           p.Role,
			AuthMethod:     auth.MethodSession,
			KeyFingerprint: p.KeyFingerprint,
		},
		CreatedAt: now,
		ExpiresAt: now.Add(s.ttl),
	}
	sess.Principal.ExpiresAt = &sess.ExpiresAt
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, clash := s.sessions[id]; clash {
		return Session{}, errors.New("session id collision")
	}
	s.sweepLocked(now)
	s.sessions[id] = sess
	return sess, nil
}

// Get returns the session, sweeping expired ones on the way past.
func (s *MemoryStore) Get(id string) (Session, error) {
	now := s.now()
	s.mu.Lock()
	defer s.mu.Unlock()
	s.sweepLocked(now)
	sess, ok := s.sessions[id]
	if !ok {
		return Session{}, ErrNotFound
	}
	if !sess.ExpiresAt.After(now) {
		delete(s.sessions, id)
		return Session{}, ErrNotFound
	}
	return sess, nil
}

// Principal resolves an id to the principal it authenticates as, satisfying
// auth.SessionResolver. It exists so the auth middleware can look a session up
// without importing this package (which imports auth): the dependency runs one
// way, and the interface is the seam.
func (s *MemoryStore) Principal(id string) (auth.Principal, error) {
	sess, err := s.Get(id)
	if err != nil {
		return auth.Principal{}, err
	}
	return sess.Principal, nil
}

// Revoke removes one session; unknown ids are not an error.
func (s *MemoryStore) Revoke(id string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.sessions, id)
}

// RevokeSubject removes every session of one subject.
func (s *MemoryStore) RevokeSubject(subject string) int {
	s.mu.Lock()
	defer s.mu.Unlock()
	n := 0
	for id, sess := range s.sessions {
		if sess.Principal.Subject == subject {
			delete(s.sessions, id)
			n++
		}
	}
	return n
}

// Len reports live sessions after a sweep.
func (s *MemoryStore) Len() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.sweepLocked(s.now())
	return len(s.sessions)
}

// sweepLocked drops expired sessions. Called on every operation rather than
// from a goroutine: the store is small, and a background sweeper would be one
// more thing to stop cleanly on SIGTERM for no benefit.
func (s *MemoryStore) sweepLocked(now time.Time) {
	for id, sess := range s.sessions {
		if !sess.ExpiresAt.After(now) {
			delete(s.sessions, id)
		}
	}
}
