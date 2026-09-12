package ratelimit

import (
	"context"
	"strconv"
	"testing"
	"time"
)

type clock struct{ t time.Time }

func (c *clock) now() time.Time      { return c.t }
func (c *clock) add(d time.Duration) { c.t = c.t.Add(d) }

func newTestLimiter(cfg Config) (*Limiter, *clock, *[]time.Duration) {
	c := &clock{t: time.Date(2026, 9, 11, 12, 0, 0, 0, time.UTC)}
	var slept []time.Duration
	cfg.Now = c.now
	cfg.Sleep = func(_ context.Context, d time.Duration) { slept = append(slept, d) }
	return New(cfg), c, &slept
}

func TestPerCredentialBudget(t *testing.T) {
	l, c, _ := newTestLimiter(Config{PerCredential: 3, TarpitAt: -1})
	const key = "cafebabe"

	for i := 0; i < 3; i++ {
		if ok, _ := l.Allow(key); !ok {
			t.Fatalf("refused after %d failures; the budget is 3", i)
		}
		l.Fail(key)
	}
	ok, retryAfter := l.Allow(key)
	if ok {
		t.Fatal("a credential over its budget was allowed through to the verifier")
	}
	if retryAfter < 1 || retryAfter > 60 {
		t.Fatalf("retry_after = %d; want 1..60 seconds", retryAfter)
	}

	// Another credential is unaffected: one attacker must not be able to lock
	// an operator out by guessing at their key.
	if ok, _ := l.Allow("deadbeef"); !ok {
		t.Fatal("one credential's failures locked out a different credential")
	}

	// The window slides: past it, the budget is back.
	c.add(Window + time.Second)
	if ok, _ := l.Allow(key); !ok {
		t.Fatal("the window did not slide")
	}
}

func TestAnonymousIsNotKeyedButStillCounts(t *testing.T) {
	l, _, _ := newTestLimiter(Config{PerCredential: 2, TarpitAt: 3})
	// No credential ⇒ nothing to key on. Refusing these would take the
	// contract's 401 away from exactly the requests meant to receive it.
	for i := 0; i < 10; i++ {
		if ok, _ := l.Allow(""); !ok {
			t.Fatalf("anonymous request %d was rate-limited", i)
		}
		l.Fail("")
	}
	// They do feed the global tarpit, which is what a distributed guess trips.
	if d := l.TarpitDelay(); d != DefaultTarpitDelay {
		t.Fatalf("tarpit delay = %v after 10 anonymous failures with threshold 3; want %v", d, DefaultTarpitDelay)
	}
}

func TestGlobalTarpit(t *testing.T) {
	// 30 s was the old default and is now above MaxTarpitDelay, which New
	// clamps: the delay occupies a request goroutine for its whole length.
	l, c, slept := newTestLimiter(Config{PerCredential: -1, TarpitAt: 20, TarpitDelay: MaxTarpitDelay})

	for i := 0; i < 19; i++ {
		l.Fail("key-" + string(rune('a'+i%26)))
	}
	if d := l.TarpitDelay(); d != 0 {
		t.Fatalf("tarpit on at 19 failures (threshold 20): %v", d)
	}
	l.Fail("key-last")
	if d := l.TarpitDelay(); d != MaxTarpitDelay {
		t.Fatalf("tarpit delay = %v at the threshold; want %v", d, MaxTarpitDelay)
	}
	if got := l.Tarpit(context.Background()); got != MaxTarpitDelay || len(*slept) != 1 || (*slept)[0] != MaxTarpitDelay {
		t.Fatalf("Tarpit() did not serve the delay: %v %v", got, *slept)
	}

	// The tarpit is a rate, not a latch: once the window rolls past the
	// failures, successful traffic is not punished for what happened a minute
	// ago.
	c.add(Window + time.Second)
	if d := l.TarpitDelay(); d != 0 {
		t.Fatalf("tarpit still on a window later: %v", d)
	}
}

func TestDisabledLimiters(t *testing.T) {
	// A negative budget disables that limiter — what --fake-drivers sets,
	// because the conformance suite deliberately produces dozens of 401s and
	// 403s and rate-limiting them would turn a contract assertion into a flake.
	l, _, slept := newTestLimiter(Config{PerCredential: -1, TarpitAt: -1})
	for i := 0; i < 1000; i++ {
		l.Fail("one-key")
	}
	if ok, _ := l.Allow("one-key"); !ok {
		t.Fatal("a disabled per-credential limiter refused a request")
	}
	if d := l.Tarpit(context.Background()); d != 0 || len(*slept) != 0 {
		t.Fatalf("a disabled tarpit delayed a response: %v", d)
	}
}

func TestDefaults(t *testing.T) {
	l := New(Config{})
	if l.perCredential != DefaultPerCredential || l.tarpitAt != DefaultTarpitAt ||
		l.tarpitDelay != DefaultTarpitDelay || l.window != Window {
		t.Fatalf("zero Config did not take the documented defaults: perCredential=%d tarpitAt=%d delay=%v window=%v",
			l.perCredential, l.tarpitAt, l.tarpitDelay, l.window)
	}
}

func TestReset(t *testing.T) {
	l, _, _ := newTestLimiter(Config{PerCredential: 1, TarpitAt: 1})
	l.Fail("k")
	if ok, _ := l.Allow("k"); ok {
		t.Fatal("budget of 1 not enforced")
	}
	l.Reset()
	if ok, _ := l.Allow("k"); !ok {
		t.Fatal("Reset did not clear the per-credential counter")
	}
	if d := l.TarpitDelay(); d != 0 {
		t.Fatal("Reset did not clear the global counter")
	}
}

func TestConcurrentUseIsSafe(t *testing.T) {
	l := New(Config{PerCredential: 1000, TarpitAt: -1})
	done := make(chan struct{})
	for i := 0; i < 8; i++ {
		go func(n int) {
			defer func() { done <- struct{}{} }()
			for j := 0; j < 100; j++ {
				l.Fail("k")
				l.Allow("k")
				l.TarpitDelay()
			}
		}(i)
	}
	for i := 0; i < 8; i++ {
		<-done
	}
}

// TestAllowNeverInsertsAnEntry is the memory half of the anonymous-surface
// problem: GET /health is skipped by the auth middleware, so Fail is never
// reached for it, and an Allow that wrote `failures[key] = hits` left one
// permanent entry per distinct credential an unauthenticated caller chose to
// present.
func TestAllowNeverInsertsAnEntry(t *testing.T) {
	l, _, _ := newTestLimiter(Config{PerCredential: 3, TarpitAt: -1})
	for i := 0; i < 1000; i++ {
		if ok, _ := l.Allow("never-fails-" + strconv.Itoa(i)); !ok {
			t.Fatalf("a credential with no failures was refused at %d", i)
		}
	}
	if n := l.Tracked(); n != 0 {
		t.Fatalf("Allow left %d map entries for credentials that never failed; want 0", n)
	}
}

// TestExpiredEntryIsDroppedByAllow: an entry that exists and has aged out is
// pruned — and removed — on the read path, which is the one write-back Allow
// is allowed to make.
func TestExpiredEntryIsDroppedByAllow(t *testing.T) {
	l, c, _ := newTestLimiter(Config{PerCredential: 3, TarpitAt: -1})
	l.Fail("k")
	if n := l.Tracked(); n != 1 {
		t.Fatalf("Fail did not record: %d", n)
	}
	c.add(Window + time.Second)
	if ok, _ := l.Allow("k"); !ok {
		t.Fatal("the window did not slide")
	}
	if n := l.Tracked(); n != 0 {
		t.Fatalf("an aged-out entry survived Allow: %d", n)
	}
}

// TestTimeBasedSweepRunsAtMostOncePerWindow replaces the old size-triggered
// sweep, which walked the whole map under the mutex on EVERY failure past
// 10k and — under an attack that keeps failing — deleted nothing.
func TestTimeBasedSweepRunsAtMostOncePerWindow(t *testing.T) {
	l, c, _ := newTestLimiter(Config{PerCredential: 100, TarpitAt: -1})
	for i := 0; i < 500; i++ {
		l.Fail("k" + strconv.Itoa(i))
		l.Allow("k" + strconv.Itoa(i))
	}
	l.mu.Lock()
	sweeps := l.sweeps
	l.mu.Unlock()
	if sweeps != 0 {
		t.Fatalf("%d sweeps inside one window; want 0 — the cost must not be per call", sweeps)
	}
	c.add(Window + time.Second)
	l.Fail("later")
	l.Allow("later")
	l.Fail("later")
	l.mu.Lock()
	sweeps, tracked := l.sweeps, len(l.failures)
	l.mu.Unlock()
	if sweeps != 1 {
		t.Fatalf("sweeps = %d after crossing one window; want exactly 1", sweeps)
	}
	if tracked != 1 {
		t.Fatalf("the sweep left %d entries; only the one failure inside the window should remain", tracked)
	}
}

// TestHardCapBoundsTheMap is the last resort: an attacker failing faster than
// the window rolls cannot grow the map without limit, and the eviction pass
// is amortised (it runs once per cap/10 insertions, not once per failure).
func TestHardCapBoundsTheMap(t *testing.T) {
	const cap = 100
	l, _, _ := newTestLimiter(Config{PerCredential: 5, TarpitAt: -1, MaxCredentials: cap})
	for i := 0; i < 10*cap; i++ {
		l.Fail("attacker-" + strconv.Itoa(i))
	}
	if n := l.Tracked(); n > cap {
		t.Fatalf("the failure map grew to %d entries with a cap of %d", n, cap)
	}
	// Eviction is oldest-touch: the most recent failures are the ones kept, so
	// the counter that is actually being hammered survives.
	l.mu.Lock()
	_, kept := l.failures["attacker-"+strconv.Itoa(10*cap-1)]
	l.mu.Unlock()
	if !kept {
		t.Fatal("eviction dropped the most recently touched credential")
	}
}

// TestTarpitDelayIsCancelledByContext: the delay runs in the request goroutine,
// so it must end when the request (or the daemon) does. Without this a SIGTERM
// during a tarpit outlived the shutdown grace and the process exited 1.
func TestTarpitDelayIsCancelledByContext(t *testing.T) {
	l := New(Config{PerCredential: -1, TarpitAt: 1, TarpitDelay: time.Hour})
	l.Fail("k")
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	done := make(chan time.Duration, 1)
	go func() { done <- l.Tarpit(ctx) }()
	select {
	case d := <-done:
		if d != MaxTarpitDelay {
			t.Fatalf("Tarpit reported %v; want the capped delay %v", d, MaxTarpitDelay)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("Tarpit ignored a cancelled context and was still waiting")
	}
}

// TestTarpitDelayIsCapped: an operator cannot configure a delay that pins a
// request goroutine for longer than the server's write timeout.
func TestTarpitDelayIsCapped(t *testing.T) {
	if d := New(Config{TarpitDelay: time.Hour}).tarpitDelay; d != MaxTarpitDelay {
		t.Fatalf("configured delay 1h became %v; want the %v cap", d, MaxTarpitDelay)
	}
	if d := New(Config{}).tarpitDelay; d != DefaultTarpitDelay {
		t.Fatalf("default delay = %v; want %v", d, DefaultTarpitDelay)
	}
}

// TestConcurrentTarpitsAreBounded.
//
// Every held response occupies a request goroutine and a connection for the
// whole delay, and the failure rate that decides how many are held at once is
// the ATTACKER's to choose — so an unbounded tarpit hands them the daemon's
// connection budget as a target: the defence would take the daemon down before
// the guessing did. Past the bound a failure is answered at once, undelayed.
func TestConcurrentTarpitsAreBounded(t *testing.T) {
	const maxTarpits = 4
	release := make(chan struct{})
	entered := make(chan struct{}, 64)
	l := New(Config{
		PerCredential: -1, TarpitAt: 1, TarpitDelay: time.Second,
		MaxTarpits: maxTarpits,
		Sleep: func(context.Context, time.Duration) {
			entered <- struct{}{}
			<-release
		},
	})
	l.Fail("k")

	held := make(chan time.Duration, 64)
	for i := 0; i < 4*maxTarpits; i++ {
		go func() { held <- l.Tarpit(context.Background()) }()
	}
	// The ones past the bound return immediately; the bounded ones are parked
	// in Sleep until release is closed.
	for i := 0; i < 4*maxTarpits-maxTarpits; i++ {
		select {
		case d := <-held:
			// The caller writes the same response either way, so the reported
			// delay is what was ASKED for, not what was waited.
			if d != time.Second {
				t.Fatalf("an immediate answer reported %v; want the asked-for delay", d)
			}
		case <-time.After(10 * time.Second):
			t.Fatal("a tarpit past the bound blocked instead of answering immediately")
		}
	}
	for i := 0; i < maxTarpits; i++ {
		<-entered
	}
	if n := l.TarpitsHeld(); n != maxTarpits {
		t.Fatalf("%d responses held; the bound is %d", n, maxTarpits)
	}
	close(release)
	for i := 0; i < maxTarpits; i++ {
		select {
		case <-held:
		case <-time.After(10 * time.Second):
			t.Fatal("a held tarpit never returned after the delay ended")
		}
	}
	if n := l.TarpitsHeld(); n != 0 {
		t.Fatalf("%d slots were never released", n)
	}
}

// TestTarpitBoundDefaultsAndCanBeDisabled: the default bound is in force
// without configuration, and a negative value is the deliberate "no bound"
// (nothing in the daemon sets it; it exists so the semaphore is not an
// untestable constant).
func TestTarpitBoundDefaultsAndCanBeDisabled(t *testing.T) {
	if got := cap(New(Config{}).tarpits); got != DefaultMaxTarpits {
		t.Fatalf("default bound = %d; want %d", got, DefaultMaxTarpits)
	}
	if l := New(Config{MaxTarpits: -1}); l.tarpits != nil {
		t.Fatal("a negative MaxTarpits did not disable the bound")
	}
	if got := cap(New(Config{MaxTarpits: 7}).tarpits); got != 7 {
		t.Fatalf("configured bound = %d; want 7", got)
	}
	// A disabled bound still serves the delay, and reports nothing held.
	var slept int
	l := New(Config{
		PerCredential: -1, TarpitAt: 1, MaxTarpits: -1,
		Sleep: func(context.Context, time.Duration) { slept++ },
	})
	l.Fail("k")
	if d := l.Tarpit(context.Background()); d != DefaultTarpitDelay || slept != 1 {
		t.Fatalf("unbounded tarpit: d=%v slept=%d", d, slept)
	}
	if n := l.TarpitsHeld(); n != 0 {
		t.Fatalf("TarpitsHeld = %d with the bound disabled; want 0", n)
	}
}
