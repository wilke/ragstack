package api

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"regexp"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/doctor"
	"github.com/ragstack/ragstack/internal/ctl/fleet"
	"github.com/ragstack/ragstack/internal/ctl/gateway"
	"github.com/ragstack/ragstack/internal/ctl/hostfacts"
	"github.com/ragstack/ragstack/internal/ctl/logs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// diskCacheTTL is how long a tenant's `du` result is reused. The probe shells
// out to `du -s -B1` over a whole tenant tree, so it is minutes of IO on a
// large one — which is exactly why the cache must OUTLIVE a request.
const diskCacheTTL = time.Hour

// liveBackend serves the real registry and probes the real host.
//
// Three of its six reads are the production implementations already:
// fleet.Build, logs.Read and doctor.Run each take their host probes through a
// seam and default to the live one. The other three — Tenants, Tenant and Env
// — still project from the registry row alone (see FakeBackend), because the
// per-tenant status/units/env-classification builders land with adopt's
// remaining half. The difference is visible rather than hidden: a projected
// row reports `n/a` and null where it has not looked, never a value it did not
// observe.
//
// The probes and the doctor options are built ONCE, here, and reused by every
// request. Constructing them per request gave each `/v1/fleet` poll a brand
// new hostfacts.CachedDU — a cache whose whole purpose is to outlive one call
// — so a dashboard polling every few seconds re-ran `du -s -B1` over every
// tenant directory forever, and the cache never once hit.
type liveBackend struct {
	*FakeBackend // the registry→response projections, over the REAL registry
	roots        paths.Roots
	registryPath string
	probes       fleet.Probes
	doctorOpts   doctor.Options
	// signaller reads /proc for the gateway status. Built once, like the
	// probes: it is a value, not a connection, and the handler must not
	// construct one per request.
	signaller gateway.Signaller
}

func newLiveBackend(ragRoot, registryPath string) (*liveBackend, error) {
	return newLiveBackendWithLogger(ragRoot, registryPath, slog.Default())
}

// newLiveBackendWithLogger is newLiveBackend with the daemon's REDACTING
// logger passed in, so the stale-projection warning goes through it.
func newLiveBackendWithLogger(ragRoot, registryPath string, logger *slog.Logger) (*liveBackend, error) {
	if logger == nil {
		logger = slog.Default()
	}
	roots := paths.NewRoots(ragRoot, paths.Overrides{})
	if registryPath == "" {
		registryPath = roots.Registry()
	}
	// LoadWithDiagnostics, never a repairing load: a READ must not rewrite
	// manifest.tsv (see registry.ErrProjectionStale for the port-reissue bug
	// that repair-on-read caused). A stale projection is a diagnostic, and
	// /v1/doctor reports it as `registry_manifest_mismatch` /
	// `manifest_unknown_row` from the same registry path passed below.
	f, diags, err := registry.LoadWithDiagnostics(registryPath)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", registryPath, err)
	}
	if diags.Stale() {
		logger.Warn("registry projection is stale",
			"registry", registryPath,
			"detail", diags.ProjectionStale.Error(),
			"repair", "ragstack-ctl fleet repair")
	}

	// The CLI resolves the uid from doctor.DefaultCtlUser; fleet names the same
	// account for its own probes. They are one account, and the doctor options
	// below must be built from the one the CLI uses or the hashes part again.
	ctlUser := doctor.DefaultCtlUser
	return &liveBackend{
		FakeBackend:  &FakeBackend{fleet: f, now: time.Now},
		roots:        roots,
		registryPath: registryPath,
		probes: fleet.Probes{
			Host:   hostfacts.NewReal(roots),
			Prober: hostfacts.NewProber(),
			// One cache for the process, not one per request.
			Disk:         hostfacts.NewCachedDU(diskCacheTTL),
			CtlUser:      fleet.DefaultCtlUser,
			SudoersGroup: fleet.DefaultSudoersGroup,
		},
		signaller: gateway.NewRealSignaller(),
		doctorOpts: doctor.Options{
			RegistryPath: registryPath,
			// CtlUID is what the `user_dropin_missing` and
			// `runtime_dir_missing` checks need. Omitting it here while the
			// CLI passed it meant those two findings never ran over HTTP — so
			// `ragstack-ctl doctor` and GET /v1/doctor produced DIFFERENT
			// hashes for the same host, and a plan pinned by one could not be
			// confirmed against the other.
			CtlUID: ctlUID(ctlUser),
		},
	}, nil
}

// ctlUID resolves the ctl user's uid, or 0 when that user does not exist on
// this host (a developer checkout). Deliberately identical to the CLI's own
// helper, so both doctor paths see the same value.
func ctlUID(username string) int {
	if uid := hostfacts.LookupUID(username); uid > 0 {
		return uid
	}
	return 0
}

// Fleet probes the host: listeners, units, disk, linger, the store health
// columns, through the process-lifetime probes built in newLiveBackend.
func (b *liveBackend) Fleet(ctx context.Context) (*model.FleetResponse, error) {
	return fleet.Build(ctx, b.roots, b.fleet, b.probes), nil
}

// Logs tails the tenant's real log, redacted with that tenant's own secret
// material. A file the tenant does not have is the contract's 404, not an
// empty tail — a `ui` log for a static-UI tenant, a store log for a shared
// store.
func (b *liveBackend) Logs(_ context.Context, name, file string, lines int) (*model.LogsResponse, error) {
	t, ok := b.fleet.Tenants[name]
	if !ok {
		return nil, ErrNotFound
	}
	resp, err := logs.Read(b.roots, t, model.LogFile(file), lines)
	if errors.Is(err, logs.ErrNoLog) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	return resp, nil
}

// GatewayStatus reads the published gateway generation off the host: the
// `current` pointer, txn.json and the nginx master's /proc identity. Every
// read repairs an incomplete publication first, so the dashboard never shows
// a pointer the next reload would disagree with.
//
// It re-reads the registry rather than using the one loaded at start-up: an
// adopt or a publish moves the registry on, and `pending_diff` has to compare
// what is published against what the registry says NOW.
func (b *liveBackend) GatewayStatus(_ context.Context) (*model.GatewayStatus, error) {
	f, err := b.reloadRegistry()
	if err != nil {
		return nil, err
	}
	return gateway.StatusWith(b.roots, f, gateway.Options{Sig: b.signaller})
}

// ErrGatewayRender marks a failure of the gateway RENDERER — the registry does
// not render to a loadable configuration — as opposed to a failure to READ the
// registry.
//
// The two need different answers. A load failure is a host fault whose text
// quotes the registry path and, on a parse error, the line it failed on; it
// goes through backendError, which logs it and tells the caller nothing. A
// render refusal is the caller's own registry content and is worth returning —
// but its text still carries host paths (`tenant demo: /rag/data/tenants/demo/
// ui/dist escapes …`), so it is redacted first. GET/POST /v1/gateway/render is
// a VIEWER operation.
var ErrGatewayRender = errors.New("the gateway renderer refused the registry")

// GatewayDiff renders the next generation and diffs it against the published
// one. A READ despite the verb: nothing is written and no lock is taken.
func (b *liveBackend) GatewayDiff(_ context.Context) (*model.GatewayRenderResponse, error) {
	f, err := b.reloadRegistry()
	if err != nil {
		return nil, err
	}
	resp, err := gateway.Diff(b.roots, f)
	if err != nil {
		// Marked, not redacted: what leaves the process is the handler's
		// decision, and it redacts once for every backend rather than trusting
		// each one to have done it.
		return nil, fmt.Errorf("%w: %v", ErrGatewayRender, err)
	}
	return resp, nil
}

// absPath matches an absolute filesystem path in an error message: a slash at
// a word boundary and everything up to whitespace, a quote or a closing
// bracket. The leading group keeps a RELATIVE path (`conf.d/05-tenants…`, which
// names a file inside the generation and no host layout) out of the match.
var absPath = regexp.MustCompile(`(^|[\s"'(=])(/[^\s"'` + "`" + `),;:]*)`)

// RedactHostPaths replaces every absolute path in a message with `<path>`.
//
// The renderer's refusals name files under /rag — a tenant's data dir, the
// admin bundle, the proxy tree — and the layout of the host's filesystem is not
// something a viewer is entitled to read out of an error body. The rest of the
// message (which tenant, which field, what was wrong with it) is exactly what
// the caller needs and survives.
func RedactHostPaths(s string) string {
	return absPath.ReplaceAllString(s, "${1}<path>")
}

// reloadRegistry re-reads the registry file. LoadNoRepair, never a repairing
// load: a READ must not rewrite manifest.tsv (see newLiveBackend).
func (b *liveBackend) reloadRegistry() (*registry.Fleet, error) {
	f, err := registry.LoadNoRepair(b.registryPath)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", b.registryPath, err)
	}
	return f, nil
}

// Doctor runs the real diagnostics with the SAME options the CLI uses. The
// doctor hash is what a plan pins, so two callers that disagree about the
// option set disagree about whether a plan is still valid.
func (b *liveBackend) Doctor(ctx context.Context, tenant, op string) (*model.DoctorResponse, error) {
	if tenant != "" {
		if _, ok := b.fleet.Tenants[tenant]; !ok {
			return nil, ErrNotFound
		}
	}
	opts := b.doctorOpts
	opts.Tenant, opts.Op = tenant, op
	return doctor.Run(ctx, b.roots, b.fleet, opts), nil
}
