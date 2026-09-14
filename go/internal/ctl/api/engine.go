package api

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"strings"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/doctor"
	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/envfile"
	"github.com/ragstack/ragstack/internal/ctl/fleet"
	"github.com/ragstack/ragstack/internal/ctl/hostfacts"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/ops"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/settings"
)

// ErrEngineNotWired is what BuildEngine returns until the store, the op
// registry and the drivers are connected to it at integration.
//
// It is an ERROR rather than a nil engine because a daemon that silently
// served a mutation surface backed by nothing would answer 200-shaped
// refusals no operator could distinguish from a policy decision. `serve` logs
// it and carries on with a nil engine, and every mutation then answers the
// contract's 409 `refused` with this text in the detail — which is a fact
// about the build, not about the fleet.
var ErrEngineNotWired = errors.New("job engine not wired")

// EngineConfig is everything BuildEngine needs to assemble the real engine.
// It is the ONE place the api package names the three implementations
// (jobs.Store, ops.Registry, drivers), so the HTTP layer and the --direct CLI
// construct the same engine from the same inputs rather than two engines that
// agree by coincidence.
type EngineConfig struct {
	// Roots is the deployment layout every path derives from.
	Roots paths.Roots
	// RegistryPath is registry.json; the engine re-reads it per plan, because
	// a plan is only valid against the generation it was computed on.
	RegistryPath string
	// StorePath is the SQLite jobs database, <state dir>/jobs.db.
	StorePath string
	// Mode distinguishes the daemon from a --direct CLI run. Both take the
	// same flock files in the same order; the mode is recorded on the job so
	// an operator can see which process owns it.
	Mode model.WorkerMode
	// Host is what job.worker.host records.
	Host string
	// FakeDrivers selects drivers.NewFake over drivers.NewReal: every verb
	// plans and executes end to end without touching a host, which is what
	// the conformance suite runs against.
	FakeDrivers bool
	// SecretsTTL is how long a delivery envelope lives (contract: 15 minutes).
	SecretsTTL time.Duration
	// Logger is the daemon's REDACTING logger. Never slog.Default(): a step
	// log is host text that has not been vetted.
	Logger *slog.Logger
	// Now is injected so a test can pin job timestamps.
	Now func() time.Time
}

// DefaultSecretsTTL is secrets_response.json's "lives 15 minutes".
const DefaultSecretsTTL = 15 * time.Minute

// BuildEngine assembles the job engine: the SQLite store, the op registry and
// the drivers, behind jobs.Engine. The HTTP daemon and the --direct CLI both
// come through here, so they run the same engine over the same jobs.db and
// the same flock files.
//
// The Sealer is nil in PR-C: minted secrets live only in daemon memory for
// their TTL (age sealing to the backup recipients lands with PR-D's bundle
// encryption).
func BuildEngine(cfg EngineConfig) (jobs.Engine, error) {
	if cfg.Now == nil {
		cfg.Now = time.Now
	}
	if cfg.SecretsTTL == 0 {
		cfg.SecretsTTL = DefaultSecretsTTL
	}
	if cfg.Logger == nil {
		cfg.Logger = slog.New(slog.NewTextHandler(io.Discard, nil))
	}
	if cfg.StorePath == "" {
		cfg.StorePath = jobs.DefaultStorePath(cfg.Roots.CtlStateDir)
	}
	store, err := jobs.NewStore(cfg.StorePath)
	if err != nil {
		return nil, fmt.Errorf("job store %s: %w", cfg.StorePath, err)
	}
	loadFleet := func() (*registry.Fleet, error) { return registry.LoadNoRepair(cfg.RegistryPath) }

	var drv jobs.Drivers
	if cfg.FakeDrivers {
		drv = drivers.NewFake(drivers.FakeOptions{Now: cfg.Now})
	} else {
		drv = drivers.NewReal(drivers.RealOptions{
			Roots: cfg.Roots,
			Fleet: loadFleet,
			By:    fmt.Sprintf("ragstack-ctl %s on %s", cfg.Mode, cfg.Host),
		})
	}

	// The doctor a plan pins is the same doctor the dashboard shows: same
	// host facts, same ctl account, same external store ports.
	host := hostfacts.NewReal(cfg.Roots)
	doctorFn := func(ctx context.Context, tenant, op string) (model.DoctorResponse, error) {
		f, err := loadFleet()
		if err != nil {
			return model.DoctorResponse{}, err
		}
		resp := doctor.Run(ctx, cfg.Roots, f, doctor.Options{
			Tenant:             tenant,
			Op:                 op,
			Host:               host,
			Now:                cfg.Now,
			RegistryPath:       cfg.RegistryPath,
			CtlUser:            fleet.DefaultCtlUser,
			SudoersGroup:       fleet.DefaultSudoersGroup,
			ExternalStorePorts: hostfacts.DefaultExternalStorePorts,
		})
		if resp == nil {
			return model.DoctorResponse{}, errors.New("doctor returned nothing")
		}
		return *resp, nil
	}

	eng := jobs.NewEngine(jobs.EngineOptions{
		Store:        store,
		Ops:          ops.NewRegistry(ops.Deps{Roots: cfg.Roots, Now: cfg.Now}),
		Roots:        cfg.Roots,
		RegistryPath: cfg.RegistryPath,
		LoadFleet:    loadFleet,
		Drivers:      drv,
		Redactor:     newEngineRedactor(cfg.Roots, loadFleet, cfg.Logger),
		Doctor:       doctorFn,
		Now:          cfg.Now,
		Host:         cfg.Host,
		Mode:         cfg.Mode,
		SecretsTTL:   cfg.SecretsTTL,
		Logger:       cfg.Logger,
	})
	return eng, nil
}

// engineRedactor is the jobs.Redactor the engine applies to step logs,
// previews and audit args. Text goes through the value-seeded
// settings.Redactor (seeded from every tenant's secret files the ctl account
// can read — pre-handover that is not all of them, which is why key-shaped
// redaction runs as well); args are walked and every secret-class KEY is
// blanked by name regardless of whether its value was ever seeded.
type engineRedactor struct {
	text *settings.Redactor
}

func newEngineRedactor(roots paths.Roots, loadFleet func() (*registry.Fleet, error), log *slog.Logger) *engineRedactor {
	r := settings.NewRedactor()
	if f, err := loadFleet(); err == nil {
		for name, t := range f.Tenants {
			tp := paths.TenantPaths(roots, name, t.ManifestName)
			if err := envfile.SeedRedactor(tp.ConfigDir, r); err != nil {
				// Unreadable pre-handover files are the expected case; the
				// key-shaped pass below still covers them by name.
				log.Debug("redactor seed", "tenant", name, "err", err.Error())
			}
		}
	}
	return &engineRedactor{text: r}
}

func (e *engineRedactor) Redact(s string) string { return e.text.Redact(s) }

func (e *engineRedactor) RedactArgs(args map[string]any) map[string]any {
	out := make(map[string]any, len(args))
	for k, v := range args {
		out[k] = e.redactValue(k, v)
	}
	return out
}

func (e *engineRedactor) redactValue(key string, v any) any {
	if isSecretArg(key) {
		return "<redacted>"
	}
	switch x := v.(type) {
	case string:
		return e.text.Redact(x)
	case map[string]any:
		return e.RedactArgs(x)
	case []any:
		res := make([]any, len(x))
		for i := range x {
			res[i] = e.redactValue(key, x[i])
		}
		return res
	default:
		return v
	}
}

// isSecretArg: an arg named like a secret-class setting (value of env-set on
// a secret key, a minted key, a password) is never recorded in clear. The
// settings classifier knows the tenant.env vocabulary; the fixed list covers
// the op vocabulary (ctl_api_key, token, secret, password).
func isSecretArg(key string) bool {
	k := strings.ToUpper(key)
	if settings.Classify(k) == settings.Secret {
		return true
	}
	for _, needle := range []string{"KEY", "TOKEN", "SECRET", "PASSWORD", "DSN"} {
		if strings.Contains(k, needle) {
			return true
		}
	}
	return false
}
