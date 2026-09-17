package api

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/auth"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/ratelimit"
	"github.com/ragstack/ragstack/internal/ctl/session"
)

// --------------------------------------------------------------------------
// Whose state directory is this?
//
// The tests below are the 2026-09-17 incident written down: a wilke `--direct`
// run pointed at svcbvbrc's /rag/data/ctl opened svcbvbrc's jobs.db and left
// wilke-owned `jobs.db-wal` and `jobs.db-shm` beside it, after which the
// DAEMON could not open its own store — "attempt to write a readonly database
// (8)" — until a human noticed the next day and chmod'd them.
// --------------------------------------------------------------------------

// TestAStateDirThisAccountOwnsIsNotRefused pins the three shapes that must
// stay silent. A check that refused any of them would refuse the ordinary case
// — a first run, an unset variable, a store this account created itself — and
// an ownership guard that cries wolf is a guard operators learn to route
// around with a flag.
func TestAStateDirThisAccountOwnsIsNotRefused(t *testing.T) {
	dir := t.TempDir() // created by this process, so owned by it

	if err := CheckStateDirOwnership(dir); err != nil {
		t.Fatalf("a state dir this process owns was refused: %v", err)
	}

	// The store itself is checked separately from the directory, because the
	// pair that broke the daemon was a directory one account owned holding a
	// database another had opened.
	store := jobs.DefaultStorePath(dir)
	if err := os.WriteFile(store, []byte("not really sqlite"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := CheckStateDirOwnership(dir); err != nil {
		t.Fatalf("a jobs.db this process owns was refused: %v", err)
	}

	// A directory that does not exist yet is the FIRST run, not a conflict:
	// this account is about to create it, and it will own what it creates.
	if err := CheckStateDirOwnership(filepath.Join(dir, "not-created-yet")); err != nil {
		t.Errorf("an absent state dir was refused: %v", err)
	}

	// An empty CTL_STATE_DIR says nothing at all; the caller's own defaulting
	// decides the path, and there is no path here to have an owner.
	if err := CheckStateDirOwnership(""); err != nil {
		t.Errorf("an empty state dir was refused: %v", err)
	}
	if err := CheckStateDirOwnership("   "); err != nil {
		t.Errorf("a blank state dir was refused: %v", err)
	}
}

// TestAStateDirOwnedByAnotherAccountIsRefusedWithTheCommandToRunInstead is the
// refusal itself, and it asserts the TEXT because the text is the fix. An
// operator meeting this error is mid-handover with a command they believed was
// safe; a refusal that only said "wrong owner" would leave them to guess a
// state directory, and the guess that looks obvious — reuse the daemon's, it
// is the one in the docs — is exactly what caused the incident.
//
// /etc stands in for the daemon's /rag/data/ctl: root-owned, stable, and not
// this process's, which is the only property CheckStateDirOwnership reads. A
// test cannot chown a file to another uid, so the foreign-uid branch has to be
// reached by finding a foreign uid rather than by making one.
func TestAStateDirOwnedByAnotherAccountIsRefusedWithTheCommandToRunInstead(t *testing.T) {
	const foreign = "/etc"
	st, err := os.Lstat(foreign)
	if err != nil {
		t.Skipf("%s is not readable on this host: %v", foreign, err)
	}
	sys, ok := st.Sys().(*syscall.Stat_t)
	if !ok {
		t.Skipf("%s has no stat_t on this platform", foreign)
	}
	if int(sys.Uid) == os.Geteuid() {
		t.Skipf("this test runs as uid %d, which owns %s; there is no foreign directory to point at",
			os.Geteuid(), foreign)
	}

	err = CheckStateDirOwnership(foreign)
	if err == nil {
		t.Fatalf("a state dir owned by uid %d was accepted by uid %d", sys.Uid, os.Geteuid())
	}
	// ErrRefused, not a bare error: `--direct` maps the refusal onto exit 3
	// and the contract's `refused`, and an ownership problem is a policy
	// answer rather than a crash.
	if !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("the refusal does not wrap jobs.ErrRefused: %v", err)
	}
	msg := err.Error()
	if !strings.Contains(msg, foreign) {
		t.Errorf("the refusal does not name the directory it refused: %v", msg)
	}
	// The MECHANISM, not just the verdict. "jobs.db-wal" is the word that
	// explains why a directory the operator can read, and a database they can
	// open, is still not theirs to run an engine out of.
	if !strings.Contains(msg, "jobs.db-wal") {
		t.Errorf("the refusal does not say what would be written as this account: %v", msg)
	}
	// And the command to run instead. Both halves: a CTL_STATE_DIR without a
	// matching CTL_CONFIG_DIR writes units into the daemon's tree.
	want := "CTL_STATE_DIR=" + SelftestStateDir + " CTL_CONFIG_DIR=" + SelftestConfigDir
	if !strings.Contains(msg, want) {
		t.Errorf("the refusal does not carry the convention %q: %v", want, msg)
	}
}

// --------------------------------------------------------------------------
// GET /health says whether mutations can land
// --------------------------------------------------------------------------

// engineStateServer is newEngineServer with the *Server kept, and with an
// EngineErr a caller can set: the health tests need the server itself, because
// what they assert is that a swap made after the router was built is visible
// to a handler.
func engineStateServer(t *testing.T, eng jobs.Engine, engineErr error) (*Server, http.Handler) {
	t.Helper()
	envs := map[string]string{
		auth.EnvAPIKeys:     `["` + opKey + `"]`,
		auth.EnvAPIKeyRoles: `{"` + opKey + `":"operator"}`,
		auth.EnvAPIKeyNames: `{"` + opKey + `":"ops"}`,
	}
	keys, err := auth.LoadKeys(func(k string) string { return envs[k] })
	if err != nil {
		t.Fatal(err)
	}
	verifier, err := auth.New(auth.Options{})
	if err != nil {
		t.Fatal(err)
	}
	sessions := session.NewMemoryStore()
	srv := &Server{
		Backend:   NewFakeBackend(),
		Engine:    eng,
		EngineErr: engineErr,
		Resolver: &auth.Resolver{
			Keys: keys, Verifier: verifier, Sessions: sessions,
			Limiter: ratelimit.New(ratelimit.Config{PerCredential: -1, TarpitAt: -1}),
			Reject:  reject,
		},
		Sessions: sessions,
	}
	return srv, NewRouter(srv)
}

// healthEngine reads `engine` out of GET /health, asserting the invariants
// that hold whatever the engine is doing: anonymous, 200, status "ok".
func healthEngine(t *testing.T, h http.Handler) string {
	t.Helper()
	w := do(t, h, http.MethodGet, "/health", nil, "")
	if w.Code != http.StatusOK {
		t.Fatalf("/health = %d: %s", w.Code, w.Body.String())
	}
	body := decode(t, w)
	// `status` is pinned to "ok" by the schema and must stay there: this
	// daemon IS up and its read surface is complete. A daemon that reported
	// itself unhealthy because its store was unwritable would be restarted by
	// whatever watches it, losing the only view of the host that broke it.
	if body["status"] != model.HealthOKStatus {
		t.Fatalf("status = %v, want %q", body["status"], model.HealthOKStatus)
	}
	engine, ok := body["engine"].(string)
	if !ok {
		t.Fatalf("health_response.json requires `engine`; body = %v", body)
	}
	return engine
}

// TestHealthReportsWhetherTheEngineIsAvailable is why the field exists. Before
// it, "the daemon is up" and "a mutation submitted now would reach an engine"
// were the same question to every client, and on 2026-09-17 they were different
// answers for a day: /health said ok, the dashboard rendered, and every
// mutation answered 409 refused because two SQLite sidecars had the wrong
// owner. Nothing a monitor could poll knew the difference.
func TestHealthReportsWhetherTheEngineIsAvailable(t *testing.T) {
	_, wired := engineStateServer(t, &fakeEngine{}, nil)
	if got := healthEngine(t, wired); got != model.EngineAvailable {
		t.Errorf("a daemon with an engine reports engine=%q, want %q", got, model.EngineAvailable)
	}

	_, broken := engineStateServer(t, nil,
		errors.New("job store /rag/data/ctl/jobs.db: attempt to write a readonly database (8)"))
	if got := healthEngine(t, broken); got != model.EngineUnavailable {
		t.Errorf("a daemon whose store would not open reports engine=%q, want %q", got, model.EngineUnavailable)
	}
}

// TestSetEngineIsVisibleToHandlersWithoutRebuildingTheRouter is the property
// the background retry depends on. The retry loop runs alongside the listener,
// on a router that was built while the engine was still missing; if a handler
// read the Server's Engine FIELD, a recovered store would be invisible until
// the daemon was restarted — which is the manual step the retry exists to
// remove.
func TestSetEngineIsVisibleToHandlersWithoutRebuildingTheRouter(t *testing.T) {
	srv, h := engineStateServer(t, nil, errors.New("job store: no such file or directory"))
	if got := healthEngine(t, h); got != model.EngineUnavailable {
		t.Fatalf("engine=%q before the swap, want %q", got, model.EngineUnavailable)
	}
	if srv.EngineAvailable() {
		t.Fatal("EngineAvailable() is true on a server built with no engine")
	}

	srv.SetEngine(&fakeEngine{}, nil)

	if !srv.EngineAvailable() {
		t.Error("EngineAvailable() is false after SetEngine published an engine")
	}
	if got := healthEngine(t, h); got != model.EngineAvailable {
		t.Errorf("engine=%q after SetEngine on the SAME router, want %q", got, model.EngineAvailable)
	}

	// And back: a later attempt that fails republishes the reason, so a
	// recovered-then-broken daemon does not keep claiming to be available.
	srv.SetEngine(nil, errors.New("job store: disk I/O error"))
	if got := healthEngine(t, h); got != model.EngineUnavailable {
		t.Errorf("engine=%q after the engine was withdrawn, want %q", got, model.EngineUnavailable)
	}
}

// TestAnEnginelessMutationPointsAtTheRetryAndAtHealth. The refusal is the only
// thing an operator sees when they try the command again, so it has to say two
// things the old wording did not: that the daemon is fixing this itself, and
// where to watch. Without them the documented next step is "restart the
// daemon", which is both unnecessary and, on a busy host, destructive.
func TestAnEnginelessMutationPointsAtTheRetryAndAtHealth(t *testing.T) {
	_, h := engineStateServer(t, nil,
		errors.New("job store /rag/data/ctl/jobs.db: attempt to write a readonly database"))
	w := do(t, h, http.MethodPost, opStart, opHeaders(), opBody(""))
	body := assertError(t, w, http.StatusConflict, string(model.CodeRefused))
	detail, _ := body["detail"].(string)
	for _, want := range []string{
		"readonly database", // the cause, as the daemon currently sees it
		"retries",           // it is being fixed without an operator
		"/health",           // …and where to watch that happen
		"job_engine_unavailable",
	} {
		if !strings.Contains(detail, want) {
			t.Errorf("the refusal does not mention %q: %s", want, detail)
		}
	}
}

// --------------------------------------------------------------------------
// The daemon's background retry
// --------------------------------------------------------------------------

// blockedStoreConfig is an EngineConfig whose store CANNOT be opened, and the
// function that repairs it.
//
// The block is a regular FILE where the store's parent directory belongs:
// jobs.NewStore's MkdirAll fails with ENOTDIR, which is a real filesystem
// refusal of the same shape as the permission problem the retry was written
// for, and one a test can both create and clear. Calling the returned repair
// makes the very next BuildEngine succeed.
func blockedStoreConfig(t *testing.T) (EngineConfig, func()) {
	t.Helper()
	dir := t.TempDir()
	parent := filepath.Join(dir, "ctl")
	if err := os.WriteFile(parent, []byte("a file, not a directory"), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg := EngineConfig{
		Roots:        paths.NewRoots(dir, paths.Overrides{CtlStateDir: parent}),
		RegistryPath: filepath.Join(dir, "registry.json"),
		StorePath:    filepath.Join(parent, "jobs.db"),
		Mode:         model.WorkerDaemon,
		Host:         "test",
		FakeDrivers:  true,
		Logger:       slog.New(slog.NewTextHandler(io.Discard, nil)),
	}
	if _, err := BuildEngine(cfg); err == nil {
		t.Fatal("the blocked store opened; this test would assert nothing")
	}
	return cfg, func() {
		if err := os.Remove(parent); err != nil {
			t.Error(err)
		}
		if err := os.MkdirAll(parent, 0o755); err != nil {
			t.Error(err)
		}
	}
}

// discardLogger keeps the retry's WARN-per-attempt out of the test output.
// What the attempts say is asserted by reading the server, not the log.
func discardLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

// TestTheDaemonRecoversItsEngineWithoutARestart is the whole point of the
// retry. On 2026-09-17 the cause — two sidecar files with the wrong owner —
// was corrected by hand in seconds, and the daemon went on refusing every
// mutation for the rest of the day because nothing re-tried the store.
//
// The repair happens INSIDE the backoff hook, on the second attempt, so the
// test is deterministic: attempt 1 meets the broken store, attempt 2 meets the
// fixed one, and no wall-clock sleep decides which.
func TestTheDaemonRecoversItsEngineWithoutARestart(t *testing.T) {
	cfg, repair := blockedStoreConfig(t)
	srv, _ := engineStateServer(t, nil, errors.New("job store: not a directory"))

	backoff := func(attempt int) time.Duration {
		if attempt == 2 {
			repair()
		}
		return time.Millisecond
	}

	done := make(chan struct{})
	go func() {
		defer close(done)
		retryEngine(context.Background(), srv, cfg, cfg.StorePath, discardLogger(), backoff)
	}()
	select {
	case <-done:
	case <-time.After(30 * time.Second):
		t.Fatal("retryEngine did not return")
	}

	if !srv.EngineAvailable() {
		t.Fatal("the store opened on the second attempt and the mutation surface is still closed")
	}
	// Published, not merely built: every handler reads through Server.engine(),
	// and an engine the retry kept to itself would recover nothing.
	eng, err := srv.engine()
	if eng == nil || err != nil {
		t.Fatalf("srv.engine() = %v, %v; want the recovered engine and no error", eng, err)
	}
}

// TestTheRetryGivesUpAndSaysSo. Bounded is a decision, not an oversight: a
// genuinely broken store retried every two minutes forever is a log nobody
// reads and a daemon that never tells anyone it needs a human. The loop must
// RETURN — a goroutine still spinning after the bound would also hold the
// EngineConfig and its fake driver set for the life of the process.
func TestTheRetryGivesUpAndSaysSo(t *testing.T) {
	cfg, _ := blockedStoreConfig(t) // never repaired
	srv, _ := engineStateServer(t, nil, errors.New("job store: not a directory"))

	var attempts int
	backoff := func(int) time.Duration { attempts++; return time.Millisecond }

	done := make(chan struct{})
	go func() {
		defer close(done)
		retryEngine(context.Background(), srv, cfg, cfg.StorePath, discardLogger(), backoff)
	}()
	select {
	case <-done:
	case <-time.After(30 * time.Second):
		t.Fatal("retryEngine never gave up; the bound does not hold")
	}

	if attempts != engineRetryAttempts {
		t.Errorf("the loop ran %d attempts, want the bounded %d", attempts, engineRetryAttempts)
	}
	if srv.EngineAvailable() {
		t.Error("a store that never opened left the mutation surface claiming to be available")
	}
	// The LATEST reason is what a mutation quotes, so a failed attempt has to
	// republish it rather than leave the start-up error standing.
	_, why := srv.engine()
	if why == nil {
		t.Fatal("the server carries no reason the engine is missing")
	}
	if !strings.Contains(why.Error(), "jobs.db") {
		t.Errorf("the published reason does not name the store: %v", why)
	}
}

// TestACancelledContextEndsTheRetry: the retry runs under the same context
// every request runs under, so `systemctl stop` ends it with everything else.
// A loop that slept out its backoff before noticing would hold shutdown open
// for up to two minutes — longer than shutdownGrace — and turn an orderly stop
// into exit 1.
func TestACancelledContextEndsTheRetry(t *testing.T) {
	cfg, _ := blockedStoreConfig(t)
	srv, _ := engineStateServer(t, nil, errors.New("job store: not a directory"))

	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	done := make(chan struct{})
	go func() {
		defer close(done)
		// An hour of backoff: if the cancellation were not honoured, the only
		// way out of the first iteration would be the test's own timeout.
		retryEngine(ctx, srv, cfg, cfg.StorePath, discardLogger(), func(int) time.Duration { return time.Hour })
	}()
	select {
	case <-done:
	case <-time.After(10 * time.Second):
		t.Fatal("retryEngine ignored a cancelled context and sat in its backoff")
	}
	if srv.EngineAvailable() {
		t.Error("a cancelled retry published an engine")
	}
}
