package api

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/auth"
	"github.com/ragstack/ragstack/internal/ctl/ratelimit"
	"github.com/ragstack/ragstack/internal/ctl/session"
	"github.com/ragstack/ragstack/internal/ctl/settings"
	"github.com/ragstack/ragstack/internal/ctl/version"
)

// Exit codes, matching the CLI grammar in the plan.
const (
	exitOK      = 0
	exitError   = 1
	exitUsage   = 2
	exitRefused = 3
)

// DefaultListen is the daemon's bind address. Loopback by construction: the
// gateway proxies to it, and nothing else should be able to reach the surface
// that holds every tenant's admin key.
const DefaultListen = "127.0.0.1:23990"

// shutdownGrace bounds the graceful shutdown on SIGTERM. Long enough for a
// dashboard poll to finish, short enough that a systemd stop does not hang.
const shutdownGrace = 10 * time.Second

// Server timeouts. A publicly mounted daemon with none of these is one slow
// client away from holding a connection, a goroutine and a read buffer for as
// long as that client likes — the classic slowloris, which needs no credential
// because it never finishes presenting one.
//
//	readHeaderTimeout — how long the request line and headers may take.
//	readTimeout       — the whole request, body included.
//	writeTimeout      — the whole response. Deliberately longer than
//	                    ratelimit.MaxTarpitDelay, so a tarpitted failure is
//	                    still WRITTEN rather than cut at the wire.
//	idleTimeout       — how long a kept-alive connection may sit unused.
//	maxHeaderBytes    — 64 KiB. Nothing this API reads needs more, and the
//	                    1 MiB default is a per-connection memory lever.
const (
	readHeaderTimeout = 10 * time.Second
	readTimeout       = 30 * time.Second
	writeTimeout      = 60 * time.Second
	idleTimeout       = 120 * time.Second
	maxHeaderBytes    = 64 << 10
)

// RunServe is `ragstack-ctl serve`. It returns the process exit code and
// writes its own diagnostics; main.go wires it to a subcommand.
func RunServe(args []string) int {
	fs := flag.NewFlagSet("ragstack-ctl serve", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	listen := fs.String("listen", envOr(EnvListen, DefaultListen), "bind address (loopback unless --allow-non-loopback)")
	allowNonLoopback := fs.Bool("allow-non-loopback", false, "permit a non-loopback bind (the admin mount is public; the gateway is the only intended client)")
	fakeDrivers := fs.Bool("fake-drivers", false, "serve the recorded 2026-09-10 fixture instead of probing this host")
	registryPath := fs.String("registry", os.Getenv(EnvRegistry), "registry.json path (default <rag-root>/data/tenants/registry.json)")
	ragRoot := fs.String("rag-root", envOr(EnvRagRoot, "/rag"), "deployment root")
	// The daemon writes its OWN pid, and only once it is listening.
	//
	// A supervisor cannot record it correctly from outside. `setsid nohup … &`
	// under job control makes `$!` the setsid parent, not the daemon, so
	// ops/coconut/ctl-daemon.sh recorded a pid that had already exited — the
	// start then "failed", deleted the pidfile and left a live daemon nobody
	// could stop. Only the process itself knows its pid, and writing it after
	// the bind means the file's existence also means "it is up".
	pidFile := fs.String("pidfile", "", "write this process's pid here once it is listening, and remove it at exit")
	if err := fs.Parse(args); err != nil {
		return exitUsage
	}
	if fs.NArg() > 0 {
		fmt.Fprintf(os.Stderr, "ragstack-ctl serve: unexpected argument %q\n", fs.Arg(0))
		return exitUsage
	}

	logger := newLogger()

	if err := checkLoopback(*listen, *allowNonLoopback); err != nil {
		logger.Error("refusing to bind", "listen", *listen, "err", err.Error())
		return exitRefused
	}

	keys, err := auth.LoadKeysFromEnv()
	if err != nil {
		logger.Error("credential configuration", "err", err.Error())
		return exitError
	}
	verifier, err := newVerifier(*fakeDrivers, logger)
	if err != nil {
		logger.Error("identity configuration", "err", err.Error())
		return exitError
	}

	backend, err := newBackend(*fakeDrivers, *ragRoot, *registryPath, logger)
	if err != nil {
		logger.Error("registry", "err", err.Error())
		return exitError
	}

	sessions := session.NewMemoryStore()
	srv := &Server{
		Backend: backend,
		Resolver: &auth.Resolver{
			Keys:     keys,
			Verifier: verifier,
			Sessions: sessions,
			Limiter:  newLimiter(*fakeDrivers),
			Reject:   reject,
		},
		Sessions: sessions,
		Logger:   logger,
	}

	// requestCtx is the parent of EVERY request context. Cancelling it before
	// Shutdown is what ends an in-flight tarpit: Shutdown waits for handlers to
	// return, and a handler sleeping out a tarpit delay that ignored its
	// context would outlive the grace and turn `systemctl stop` into exit 1.
	requestCtx, endRequests := context.WithCancel(context.Background())
	defer endRequests()

	handler := NewRouter(srv)
	httpServer := newHTTPServer(*listen, handler, requestCtx)

	ln, err := net.Listen("tcp", *listen)
	if err != nil {
		logger.Error("listen", "listen", *listen, "err", err.Error())
		return exitError
	}

	if *pidFile != "" {
		if err := writePIDFile(*pidFile); err != nil {
			logger.Error("pidfile", "path", *pidFile, "err", err.Error())
			_ = ln.Close()
			return exitError
		}
		// Removed on every ORDERLY exit. A crash leaves it behind, which is why
		// anything reading it must verify /proc/<pid> before trusting it.
		defer func() { _ = os.Remove(*pidFile) }()
	}

	logger.Info("serving",
		"listen", ln.Addr().String(),
		"version", version.Version,
		"fake_drivers", *fakeDrivers,
		// Counts and subject names only. A key, a fingerprint or a session id
		// never reaches a log line — the redactor below is the second net, not
		// the first.
		"api_keys", keys.Len(),
		"bearer_subjects", keys.Subjects(),
	)

	// SIGTERM is what `systemctl --user stop ragstack-ctl` sends; SIGINT is
	// Ctrl-C in a terminal. Both drain in-flight requests rather than cutting
	// a dashboard poll mid-body.
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGTERM, syscall.SIGINT)

	errCh := make(chan error, 1)
	go func() {
		if err := httpServer.Serve(ln); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errCh <- err
		}
		close(errCh)
	}()

	select {
	case err, ok := <-errCh:
		if ok && err != nil {
			logger.Error("serve", "err", err.Error())
			return exitError
		}
		return exitOK
	case sig := <-stop:
		logger.Info("shutting down", "signal", sig.String())
		// Order matters: end the tarpits first, THEN drain. The other way
		// round, Shutdown spends the whole grace waiting for delays that are
		// not going to end early, and returns DeadlineExceeded.
		endRequests()
		ctx, cancel := context.WithTimeout(context.Background(), shutdownGrace)
		defer cancel()
		if err := httpServer.Shutdown(ctx); err != nil {
			logger.Error("shutdown", "err", err.Error())
			return exitError
		}
		return exitOK
	}
}

// writePIDFile writes this process's pid via a temp file and a rename, so a
// reader never sees a half-written number.
func writePIDFile(path string) error {
	if dir := filepath.Dir(path); dir != "" {
		if err := os.MkdirAll(dir, 0o2770); err != nil {
			return err
		}
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), "."+filepath.Base(path)+".tmp-*")
	if err != nil {
		return err
	}
	defer func() { _ = os.Remove(tmp.Name()) }()
	if _, err := fmt.Fprintf(tmp, "%d\n", os.Getpid()); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Chmod(0o644); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmp.Name(), path)
}

// newHTTPServer builds the daemon's *http.Server with every timeout set.
//
// Split out of RunServe so the timeouts can be asserted without binding a
// port: "the server has a WriteTimeout" is a security property, and a property
// nothing tests is one a refactor removes silently.
func newHTTPServer(listen string, handler http.Handler, requestCtx context.Context) *http.Server {
	return &http.Server{
		Addr:              listen,
		Handler:           handler,
		ReadHeaderTimeout: readHeaderTimeout,
		ReadTimeout:       readTimeout,
		WriteTimeout:      writeTimeout,
		IdleTimeout:       idleTimeout,
		MaxHeaderBytes:    maxHeaderBytes,
		BaseContext:       func(net.Listener) context.Context { return requestCtx },
	}
}

// newBackend picks the fixture backend or the live one.
func newBackend(fake bool, ragRoot, registryPath string, logger *slog.Logger) (Backend, error) {
	if fake {
		return NewFakeBackend(), nil
	}
	return newLiveBackendWithLogger(ragRoot, registryPath, logger)
}

// newVerifier builds the BV-BRC verifier from CTL_IDENTITY_ISSUER_ALLOWLIST.
//
// SET-BUT-BLANK is refused, and is not the same as unset. env.go documents the
// variable as "an explicitly EMPTY value is refused at start-up", and Getenv
// cannot tell the two apart. A template that renders the variable as an empty
// string, or an operator who blanks the value believing it narrows the list,
// used to get the WIDEST setting there is — the four default issuers — which is
// the opposite of what blanking a security allowlist reads as.
func newVerifier(fake bool, logger *slog.Logger) (*auth.Verifier, error) {
	// The REDACTING logger, like every other line this package writes: the
	// verifier logs when it falls back to a stale key, and a transport error
	// is host text nobody has vetted.
	opts := auth.Options{Logger: logger}
	if raw, set := os.LookupEnv(EnvIdentityIssuerAllowlist); set {
		trimmed := strings.TrimSpace(raw)
		if trimmed == "" {
			return nil, fmt.Errorf(
				"%s is set but empty; an empty issuer allowlist authenticates nobody and must be stated by REMOVING the variable (which takes the four default BV-BRC issuers), not by blanking it",
				EnvIdentityIssuerAllowlist)
		}
		list, err := splitList(trimmed)
		if err != nil {
			return nil, fmt.Errorf("%s: %w", EnvIdentityIssuerAllowlist, err)
		}
		opts.Allowlist = list
	}
	if path := strings.TrimSpace(os.Getenv(EnvIdentityKeyFetchFile)); path != "" {
		if !fake {
			// On a real daemon this would let whoever can write that file
			// decide which signing key authenticates every caller.
			return nil, fmt.Errorf("%s is a test-only override and requires --fake-drivers", EnvIdentityKeyFetchFile)
		}
		opts.Fetch = func(context.Context, string) ([]byte, error) { return os.ReadFile(path) }
	}
	return auth.New(opts)
}

// newLimiter sizes the failure limiter.
//
// --fake-drivers disables both halves. That is not laziness: the conformance
// suite deliberately produces dozens of 401s and 403s (every operation with no
// credential, every operator operation with the viewer key), and a limiter
// doing its job would turn those contract assertions into 429s. The limits are
// tested directly in internal/ctl/ratelimit instead, where a fake clock can
// assert them without a wall-clock wait.
func newLimiter(fake bool) *ratelimit.Limiter {
	cfg := ratelimit.Config{
		PerCredential: intEnv(EnvRateLimitPerCredential, ratelimit.DefaultPerCredential),
		TarpitAt:      intEnv(EnvRateLimitTarpitAt, ratelimit.DefaultTarpitAt),
		TarpitDelay:   durationEnv(EnvRateLimitTarpitDelay, ratelimit.DefaultTarpitDelay),
	}
	if fake {
		cfg.PerCredential, cfg.TarpitAt = -1, -1
	}
	return ratelimit.New(cfg)
}

// newLogger builds the slog logger, with the redactor applied to every line.
func newLogger() *slog.Logger {
	level := slog.LevelInfo
	switch strings.ToLower(os.Getenv(EnvLogLevel)) {
	case "debug":
		level = slog.LevelDebug
	case "warn":
		level = slog.LevelWarn
	case "error":
		level = slog.LevelError
	}
	opts := &slog.HandlerOptions{Level: level, ReplaceAttr: redactAttr}
	var h slog.Handler = slog.NewTextHandler(os.Stderr, opts)
	if strings.EqualFold(os.Getenv(EnvLogFormat), "json") {
		h = slog.NewJSONHandler(os.Stderr, opts)
	}
	return slog.New(h)
}

// redactAttr passes every string a log line carries through the shared
// redactor: `KEY=value` assignments of secret-class keys and `|sig=<hex>`
// token signatures become <REDACTED>.
//
// It is the LAST net, not the first — nothing in this package deliberately
// logs a credential — but the canary tests (settings/canary_test.go) exist
// because "nothing deliberately logs a credential" has been wrong before, in
// a redacted diff, in a rendered unit and in a bundle manifest.
func redactAttr(_ []string, a slog.Attr) slog.Attr {
	if a.Value.Kind() == slog.KindString {
		if cleaned := settings.RedactText(a.Value.String()); cleaned != a.Value.String() {
			a.Value = slog.StringValue(cleaned)
		}
	}
	return a
}

// checkLoopback refuses a non-loopback bind unless the operator asked for one.
//
// The admin mount is internet-reachable by decision and the gateway is the
// only intended client; a daemon bound to 0.0.0.0 would put the surface that
// holds every tenant's admin key on the network directly, with the nginx
// controls bypassed.
func checkLoopback(listen string, allow bool) error {
	host, port, err := net.SplitHostPort(listen)
	if err != nil {
		return fmt.Errorf("%q is not host:port: %w", listen, err)
	}
	if port == "" {
		return fmt.Errorf("%q names no port", listen)
	}
	if allow {
		return nil
	}
	if host == "" {
		return errors.New("an empty host binds every interface; pass --allow-non-loopback if that is really meant")
	}
	ip := net.ParseIP(host)
	if host == "localhost" || (ip != nil && ip.IsLoopback()) {
		return nil
	}
	return fmt.Errorf("%q is not a loopback address; the control plane is reached through the gateway, not directly (pass --allow-non-loopback to override)", host)
}

func envOr(name, def string) string {
	if v := strings.TrimSpace(os.Getenv(name)); v != "" {
		return v
	}
	return def
}

// durationEnv reads a duration setting, accepting either a Go duration
// ("2s", "500ms") or a bare number of seconds, since an operator writing an
// env file has no reason to know which this program parses. An unparseable
// value takes the default rather than failing the daemon: it is a tuning knob,
// not a security control, and ratelimit.New caps whatever comes out.
func durationEnv(name string, def time.Duration) time.Duration {
	raw := strings.TrimSpace(os.Getenv(name))
	if raw == "" {
		return def
	}
	if d, err := time.ParseDuration(raw); err == nil {
		return d
	}
	if n, err := strconv.Atoi(raw); err == nil {
		return time.Duration(n) * time.Second
	}
	return def
}

func intEnv(name string, def int) int {
	raw := strings.TrimSpace(os.Getenv(name))
	if raw == "" {
		return def
	}
	n, err := strconv.Atoi(raw)
	if err != nil {
		return def
	}
	return n
}

// splitList parses a JSON list or a bare comma list, the same rule the tenant
// API's `_split_list_env` applies.
func splitList(raw string) ([]string, error) {
	out, err := auth.SplitListEnv("value", raw)
	if err != nil {
		return nil, err
	}
	if len(out) == 0 {
		return nil, errors.New("is empty; an empty issuer allowlist authenticates nobody and must be stated by removing the variable, not by blanking it")
	}
	return out, nil
}
