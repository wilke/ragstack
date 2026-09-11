// Package ratelimit is the control plane's credential-failure limiter.
//
// The admin mount is public by decision, and every request through the gateway
// arrives with the SAME $remote_addr — so the source address is useless as a
// key and is not used. What is left is the credential itself: failures are
// counted per sha256(credential), which bounds an online guessing attack
// against one key without letting one attacker lock out another principal.
//
// On top of that sits a global tarpit. A distributed guess spreads its
// failures over many credential keys, so the per-credential counter never
// fires; the tarpit answers that by delaying FAILURE responses — and only
// failure responses — once the whole daemon is failing more than a threshold
// per minute. Successful requests by legitimate operators are never delayed,
// which is what makes a tarpit acceptable on an operations surface.
//
// Both counters are plain map+mutex over a one-minute sliding window: the
// daemon serves a handful of operators, and a token-bucket library would be a
// dependency bought with nothing.
//
// # The map is state an anonymous caller can grow, so it is bounded three ways
//
//   - Allow NEVER inserts. A credential that has never failed has no entry,
//     which is what keeps a stream of distinct X-API-Key values on the
//     anonymous /health — a path the auth middleware skips, so Fail is never
//     reached for it — from leaving one permanent entry each.
//   - A time-based sweep runs at most once per window, reachable from both
//     Allow and Fail, and drops every entry whose failures have aged out. The
//     size-triggered sweep it replaces was O(n) per failure under the mutex
//     and, under an attack that keeps failing, deleted nothing.
//   - A hard cap with oldest-touch eviction is the last resort when an
//     attacker fails faster than the window rolls. Eviction trims a batch in
//     one pass so its cost amortises to O(1) per failure.
//
// # The tarpit is bounded too
//
// A held response occupies a request goroutine and a connection for the whole
// delay, and the attacker picks the failure rate that decides how many are
// held at once — so the tarpit runs under a semaphore (DefaultMaxTarpits).
// When it is full the failure is answered immediately and undelayed: the
// defence must never be the thing that exhausts the daemon's connections.
package ratelimit

import (
	"context"
	"sort"
	"sync"
	"time"
)

// Defaults. Window is the counting period for both limiters.
const (
	Window = time.Minute
	// DefaultPerCredential is how many AUTHENTICATION failures one credential
	// may produce per window before it is answered 429 instead.
	DefaultPerCredential = 20
	// DefaultTarpitAt is the global failure rate that turns the tarpit on.
	DefaultTarpitAt = 20
	// DefaultTarpitDelay is how long a failure response is held once the
	// tarpit is on. Two seconds, not thirty: the delay occupies a request
	// goroutine and a connection for its whole length, so a long one is a
	// self-inflicted resource cost whose size the attacker chooses. Two
	// seconds already collapses a guessing rate by orders of magnitude.
	DefaultTarpitDelay = 2 * time.Second
	// MaxTarpitDelay caps whatever an operator configures, for the same
	// reason, and is kept below the server's WriteTimeout so a tarpitted
	// response is still written rather than cut.
	MaxTarpitDelay = 5 * time.Second
	// DefaultMaxCredentials bounds how many distinct credentials are tracked
	// at once. Beyond it the oldest-touched entries are evicted: losing an
	// attacker's counters is acceptable, unbounded memory is not.
	DefaultMaxCredentials = 50000
	// DefaultMaxTarpits bounds how many responses may be held in a tarpit at
	// the SAME time. Each held response occupies a request goroutine and a
	// connection for the whole delay, so an unbounded tarpit is a lever the
	// attacker — who picks the failure rate — points at the daemon's own
	// connection budget: the defence would take the daemon down before the
	// guessing did. Past the bound a failure is answered immediately. The
	// tarpit is a throttle, never a reason to run out of sockets.
	DefaultMaxTarpits = 256
	// evictToTenths is how far below the cap one eviction pass trims (in
	// tenths), so the pass runs once per (cap/10) failures, not once per one.
	evictToTenths = 9
)

// Config sizes a Limiter. A zero field takes the default named on it; a
// NEGATIVE field disables that limiter, which is what --fake-drivers uses
// (the conformance suite deliberately produces dozens of 401s and 403s, and
// rate-limiting them would turn a contract assertion into a flake).
type Config struct {
	PerCredential int
	TarpitAt      int
	TarpitDelay   time.Duration
	Window        time.Duration
	// MaxCredentials bounds the failure map; 0 ⇒ DefaultMaxCredentials.
	MaxCredentials int
	// MaxTarpits bounds concurrently held tarpit responses;
	// 0 ⇒ DefaultMaxTarpits. A negative value disables the bound.
	MaxTarpits int
	// Now is the clock; nil ⇒ time.Now.
	Now func() time.Time
	// Sleep is how a tarpit delay is served; nil ⇒ a wait that also returns
	// when the request context is done. Injected by the test, which must not
	// actually wait.
	Sleep func(ctx context.Context, d time.Duration)
}

// bucket is one credential's failures plus when the entry was last touched
// (the eviction order).
type bucket struct {
	hits    []time.Time
	touched time.Time
}

// Limiter counts credential failures. Safe for concurrent use.
type Limiter struct {
	perCredential  int
	tarpitAt       int
	tarpitDelay    time.Duration
	window         time.Duration
	maxCredentials int
	now            func() time.Time
	sleep          func(ctx context.Context, d time.Duration)
	// tarpits is the concurrency semaphore: one slot per response that may be
	// held at a time. nil when the bound is disabled. A channel rather than a
	// counter so "is there room" and "take the room" are one operation, and a
	// full one is answered with `default` instead of blocking — a tarpit that
	// queued would be the resource sink it exists to prevent.
	tarpits chan struct{}

	mu sync.Mutex
	// failures[key] is that credential's failures inside the window; global is
	// every failure's timestamp. Both are pruned on touch.
	failures map[string]*bucket
	global   []time.Time
	// lastSweep is when the whole map was last walked; sweeps counts the walks
	// (asserted by the test that the cost is once per window, not per call).
	lastSweep time.Time
	sweeps    int
}

// New builds a Limiter.
func New(cfg Config) *Limiter {
	l := &Limiter{
		perCredential:  cfg.PerCredential,
		tarpitAt:       cfg.TarpitAt,
		tarpitDelay:    cfg.TarpitDelay,
		window:         cfg.Window,
		maxCredentials: cfg.MaxCredentials,
		now:            cfg.Now,
		sleep:          cfg.Sleep,
		failures:       map[string]*bucket{},
	}
	if l.perCredential == 0 {
		l.perCredential = DefaultPerCredential
	}
	if l.tarpitAt == 0 {
		l.tarpitAt = DefaultTarpitAt
	}
	if l.tarpitDelay <= 0 {
		l.tarpitDelay = DefaultTarpitDelay
	}
	if l.tarpitDelay > MaxTarpitDelay {
		l.tarpitDelay = MaxTarpitDelay
	}
	if l.window == 0 {
		l.window = Window
	}
	if l.maxCredentials <= 0 {
		l.maxCredentials = DefaultMaxCredentials
	}
	switch {
	case cfg.MaxTarpits < 0: // explicitly unbounded
	case cfg.MaxTarpits == 0:
		l.tarpits = make(chan struct{}, DefaultMaxTarpits)
	default:
		l.tarpits = make(chan struct{}, cfg.MaxTarpits)
	}
	if l.now == nil {
		l.now = time.Now
	}
	if l.sleep == nil {
		l.sleep = contextSleep
	}
	l.lastSweep = l.now()
	return l
}

// Allow reports whether a credential may be tried again, and — when it may not
// — how many whole seconds until its oldest counted failure falls out of the
// window (the `Retry-After` of the 429 and `extra.retry_after` of its body).
//
// An empty key (no credential presented at all) is always allowed: there is
// nothing to rate-limit, and refusing anonymous traffic here would take the
// contract's 401 away from the very requests that are supposed to receive it.
// Anonymous failures still feed the global tarpit.
//
// Allow never CREATES an entry. It runs on every request, including the
// anonymous ones the auth middleware skips (GET /health), for which Fail is
// never reached — so an insert here would be one permanent entry per distinct
// header value an unauthenticated caller cares to send.
func (l *Limiter) Allow(key string) (bool, int) {
	if key == "" || l.perCredential < 0 {
		return true, 0
	}
	now := l.now()
	l.mu.Lock()
	defer l.mu.Unlock()
	l.sweepLocked(now)
	b, ok := l.failures[key]
	if !ok {
		return true, 0
	}
	if hits := prune(b.hits, now, l.window); len(hits) != len(b.hits) {
		// Written back only because the entry already existed and shrank.
		if len(hits) == 0 {
			delete(l.failures, key)
			return true, 0
		}
		b.hits = hits
	}
	if len(b.hits) < l.perCredential {
		return true, 0
	}
	retry := int((l.window - now.Sub(b.hits[0])).Seconds())
	if retry < 1 {
		retry = 1
	}
	return false, retry
}

// Fail records one credential failure — an unusable credential (401), a
// verified-but-unlisted subject (403), or a mutation whose body `ctl_api_key`
// is not usable. A role refusal for a good credential is NOT a credential
// failure and must not be recorded here: the caller is who they say they are,
// and counting it would let a viewer clicking around the UI lock their own key
// out.
func (l *Limiter) Fail(key string) {
	now := l.now()
	l.mu.Lock()
	defer l.mu.Unlock()
	l.sweepLocked(now)
	if key != "" {
		b, ok := l.failures[key]
		if !ok {
			b = &bucket{}
			l.failures[key] = b
		}
		b.hits = append(prune(b.hits, now, l.window), now)
		b.touched = now
		l.evictLocked()
	}
	l.global = append(prune(l.global, now, l.window), now)
}

// Tracked reports how many credentials currently have an entry. For tests and
// for doctor; never the keys themselves.
func (l *Limiter) Tracked() int {
	l.mu.Lock()
	defer l.mu.Unlock()
	return len(l.failures)
}

// TarpitDelay returns how long a FAILURE response should be held before it is
// written, or 0 when the tarpit is off. Read once per failing request.
func (l *Limiter) TarpitDelay() time.Duration {
	if l.tarpitAt < 0 {
		return 0
	}
	now := l.now()
	l.mu.Lock()
	defer l.mu.Unlock()
	l.global = prune(l.global, now, l.window)
	if len(l.global) < l.tarpitAt {
		return 0
	}
	return l.tarpitDelay
}

// Tarpit holds the caller for TarpitDelay, if any, and returns the delay it
// decided on.
//
// ctx is the REQUEST context — and, through the server's BaseContext, the
// daemon's. A tarpit that ignored it would keep a request goroutine and its
// connection alive for the whole delay after the client hung up, and would
// outlive a SIGTERM's shutdown grace, which is how a stop turned into a
// non-zero exit. The returned duration is what was ASKED for, not what was
// waited: the caller writes the same response either way.
//
// Concurrency is bounded (DefaultMaxTarpits). Every held response occupies a
// goroutine and a connection for its whole delay, and the failure rate that
// decides how many there are is the ATTACKER's to choose — so an unbounded
// tarpit hands them the daemon's connection budget as a target. When every
// slot is taken the failure is answered at once, undelayed: throttling is
// worth doing until it costs more than the attack.
func (l *Limiter) Tarpit(ctx context.Context) time.Duration {
	d := l.TarpitDelay()
	if d <= 0 {
		return 0
	}
	if l.tarpits == nil {
		l.sleep(ctx, d)
		return d
	}
	select {
	case l.tarpits <- struct{}{}:
		defer func() { <-l.tarpits }()
		l.sleep(ctx, d)
	default:
		// Saturated: answer now rather than add one more held connection.
	}
	return d
}

// TarpitsHeld reports how many responses are being held right now. For tests
// and for doctor.
func (l *Limiter) TarpitsHeld() int {
	if l.tarpits == nil {
		return 0
	}
	return len(l.tarpits)
}

// contextSleep waits d, or until ctx is done, whichever comes first.
func contextSleep(ctx context.Context, d time.Duration) {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-t.C:
	case <-ctx.Done():
	}
}

// Reset clears both counters (tests, and the CLI between --direct runs).
func (l *Limiter) Reset() {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.failures = map[string]*bucket{}
	l.global = nil
	l.lastSweep = l.now()
}

// sweepLocked drops every entry whose failures have all aged out, at most once
// per window. Called from both Allow and Fail, so a daemon that is only being
// probed — Allow without Fail — still collects.
func (l *Limiter) sweepLocked(now time.Time) {
	if now.Sub(l.lastSweep) < l.window {
		return
	}
	l.lastSweep = now
	l.sweeps++
	for k, b := range l.failures {
		hits := prune(b.hits, now, l.window)
		if len(hits) == 0 {
			delete(l.failures, k)
			continue
		}
		b.hits = hits
	}
}

// evictLocked enforces the hard cap by dropping the oldest-touched entries.
// One pass trims to 9/10 of the cap, so the O(n log n) sort is paid once per
// cap/10 insertions rather than on every failure.
func (l *Limiter) evictLocked() {
	if len(l.failures) <= l.maxCredentials {
		return
	}
	target := l.maxCredentials * evictToTenths / 10
	type aged struct {
		key     string
		touched time.Time
	}
	all := make([]aged, 0, len(l.failures))
	for k, b := range l.failures {
		all = append(all, aged{key: k, touched: b.touched})
	}
	sort.Slice(all, func(i, j int) bool { return all[i].touched.Before(all[j].touched) })
	for i := 0; i < len(all)-target; i++ {
		delete(l.failures, all[i].key)
	}
}

// prune drops timestamps older than the window. The slice is kept sorted by
// construction (appends are monotonic under a monotonic clock), so a prefix
// cut is enough.
func prune(hits []time.Time, now time.Time, window time.Duration) []time.Time {
	cutoff := now.Add(-window)
	i := 0
	for i < len(hits) && !hits[i].After(cutoff) {
		i++
	}
	if i == 0 {
		return hits
	}
	return append([]time.Time(nil), hits[i:]...)
}
