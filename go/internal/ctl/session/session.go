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

// Bounds on the store. A session is minted by anyone holding one usable
// credential, so the map is state a caller can grow: a script that exchanges
// in a loop, or a browser that re-exchanges on every reload, would otherwise
// hold every session it ever minted for the full 8 hours.
const (
	// DefaultPerSubject is how many live sessions ONE subject may hold. Past
	// it the subject's oldest session is revoked, which is also what an
	// operator expects from logging in again: the 17th tab does not lock the
	// account out, it retires the first one.
	DefaultPerSubject = 16
	// DefaultMaxSessions bounds the whole store. Past it the globally oldest
	// session is revoked. It is the last resort — the per-subject cap is what
	// normally bounds growth — so it sits far above any real operator fleet.
	DefaultMaxSessions = 10000
	// sweepEvery gates the full-map sweep. Get used to walk every entry under
	// the lock on EVERY read; the walk is now amortised to once per interval,
	// the way the rate limiter's is. Correctness does not depend on it: Get,
	// Principal and Len check each session's own ExpiresAt, so an expired
	// session is never RETURNED — it only lingers in the map for at most one
	// interval before the sweep collects it.
	sweepEvery = time.Minute
)

// ErrNotFound is returned for an unknown, expired or revoked session. The
// three are ONE answer on purpose: the caller learns nothing from which.
var ErrNotFound = errors.New("session not found")

// ErrExpiredPrincipal is returned by Create when the credential offered for
// the exchange has ALREADY expired. Minting from it would launder an expired
// token into a live 8 h session, which is the one thing the exchange must
// never be able to do.
var ErrExpiredPrincipal = errors.New("the presented credential has expired")

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
	// perSubject and maxSessions are the two caps; 0 ⇒ the default named on
	// the constant. Set through NewMemoryStoreBounded.
	perSubject  int
	maxSessions int
	// lastSweep is when the whole map was last walked and sweeps counts the
	// walks — the test asserts the cost is once per interval, not per read.
	lastSweep time.Time
	sweeps    int
	// newID is the id source; injected only by the test that proves a
	// collision cannot silently overwrite a live session.
	newID func() (string, error)
}

// NewMemoryStore returns an empty store with the contract's 8 h TTL.
func NewMemoryStore() *MemoryStore { return NewMemoryStoreWith(time.Now, TTL) }

// NewMemoryStoreWith is NewMemoryStore with the clock and TTL injected.
func NewMemoryStoreWith(now func() time.Time, ttl time.Duration) *MemoryStore {
	return NewMemoryStoreBounded(now, ttl, DefaultPerSubject, DefaultMaxSessions)
}

// NewMemoryStoreBounded is NewMemoryStoreWith with both caps injected, so a
// test can drive eviction without minting ten thousand sessions. A
// non-positive cap takes its default.
func NewMemoryStoreBounded(now func() time.Time, ttl time.Duration, perSubject, maxSessions int) *MemoryStore {
	if now == nil {
		now = time.Now
	}
	if ttl <= 0 {
		ttl = TTL
	}
	if perSubject <= 0 {
		perSubject = DefaultPerSubject
	}
	if maxSessions <= 0 {
		maxSessions = DefaultMaxSessions
	}
	return &MemoryStore{
		sessions:    map[string]Session{},
		now:         now,
		ttl:         ttl,
		perSubject:  perSubject,
		maxSessions: maxSessions,
		lastSweep:   now(),
		newID:       NewID,
	}
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
//
// The session never outlives the credential that minted it. A BV-BRC token
// carries its own `expiry`, and the full 8 h TTL applied to a token with ten
// minutes left would have turned a nearly dead credential into a fresh
// day-long one — the session would still be authenticating a subject whose
// token the issuer had already retired. The expiry is therefore the EARLIER of
// the TTL and the principal's own, and a credential that has already expired
// mints nothing at all.
func (s *MemoryStore) Create(p auth.Principal) (Session, error) {
	now := s.now()
	exp := now.Add(s.ttl)
	if p.ExpiresAt != nil && p.ExpiresAt.Before(exp) {
		exp = *p.ExpiresAt
	}
	if !exp.After(now) {
		// `expiry <= now` is expired — the same rule the token verifier and
		// Get use, so the three cannot disagree by one second.
		return Session{}, ErrExpiredPrincipal
	}
	id, err := s.newID()
	if err != nil {
		return Session{}, err
	}
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
		ExpiresAt: exp,
	}
	sess.Principal.ExpiresAt = &sess.ExpiresAt
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, clash := s.sessions[id]; clash {
		return Session{}, errors.New("session id collision")
	}
	s.sweepLocked(now)
	s.evictLocked(sess.Principal.Subject, now)
	s.sessions[id] = sess
	return sess, nil
}

// Get returns the session, or ErrNotFound.
//
// The sweep it runs on the way past is TIME-GATED (see sweepEvery): a read
// that walked the whole map under the lock made every request O(live
// sessions), which is a cost an unauthenticated caller could raise by minting.
// The session's own expiry is still checked here on every read, so gating the
// sweep cannot hand out an expired session.
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

// Len reports LIVE sessions. It counts rather than returning len(map),
// because the sweep is time-gated: an expired session may still occupy a map
// slot, and a doctor line that reported it as live would be wrong.
func (s *MemoryStore) Len() int {
	now := s.now()
	s.mu.Lock()
	defer s.mu.Unlock()
	n := 0
	for _, sess := range s.sessions {
		if sess.ExpiresAt.After(now) {
			n++
		}
	}
	return n
}

// sweepLocked drops expired sessions, at most once per sweepEvery. Called from
// the operations rather than from a goroutine: a background sweeper would be
// one more thing to stop cleanly on SIGTERM for no benefit.
func (s *MemoryStore) sweepLocked(now time.Time) {
	if now.Sub(s.lastSweep) < sweepEvery {
		return
	}
	s.lastSweep = now
	s.sweeps++
	for id, sess := range s.sessions {
		if !sess.ExpiresAt.After(now) {
			delete(s.sessions, id)
		}
	}
}

// evictLocked makes room for one new session of subject.
//
// Two caps, in order: the subject's own (the common case — one operator's
// browser re-exchanging), then the store's (the last resort, when many
// subjects each stay under theirs). Both drop the OLDEST session, and both
// prefer an already-expired one: the gated sweep means the map can hold
// expired entries, and retiring a live session while a dead one sits next to
// it would log someone out for nothing.
func (s *MemoryStore) evictLocked(subject string, now time.Time) {
	for s.countLocked(subject) >= s.perSubject {
		if !s.dropOldestLocked(subject, now) {
			return
		}
	}
	for len(s.sessions) >= s.maxSessions {
		if !s.dropOldestLocked("", now) {
			return
		}
	}
}

// countLocked counts one subject's sessions, expired ones included: they are
// still occupying the slot the cap is about.
func (s *MemoryStore) countLocked(subject string) int {
	n := 0
	for _, sess := range s.sessions {
		if sess.Principal.Subject == subject {
			n++
		}
	}
	return n
}

// dropOldestLocked removes the oldest session of subject ("" ⇒ any subject),
// preferring an expired one. It reports whether anything was removed.
func (s *MemoryStore) dropOldestLocked(subject string, now time.Time) bool {
	var (
		oldestID string
		oldest   Session
		expired  bool
	)
	for id, sess := range s.sessions {
		if subject != "" && sess.Principal.Subject != subject {
			continue
		}
		dead := !sess.ExpiresAt.After(now)
		switch {
		case oldestID == "":
		case dead && !expired: // a dead session always beats a live one
		case dead == expired && sess.CreatedAt.Before(oldest.CreatedAt):
		default:
			continue
		}
		oldestID, oldest, expired = id, sess, dead
	}
	if oldestID == "" {
		return false
	}
	delete(s.sessions, oldestID)
	return true
}
