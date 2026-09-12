package api

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/doctor"
	"github.com/ragstack/ragstack/internal/ctl/fleet"
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
