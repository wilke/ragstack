package api

import (
	"errors"
	"log/slog"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
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
// the drivers, behind jobs.Engine.
//
// TODO(integration): wire the three implementations the sibling PR-C branches
// land, exactly as below, and delete the stub return. The constructor names
// and their inputs are fixed by go/internal/ctl/jobs/jobs.go's seam:
//
//	store, err := jobs.NewStore(cfg.StorePath)
//	if err != nil { return nil, err }
//	var drv jobs.Drivers
//	if cfg.FakeDrivers {
//	        drv = drivers.NewFake(drivers.FakeOptions{Now: cfg.Now})
//	} else {
//	        drv = drivers.NewReal(drivers.RealOptions{Roots: cfg.Roots, Logger: cfg.Logger})
//	}
//	eng := jobs.NewEngine(jobs.EngineOptions{
//	        Store:        store,
//	        Ops:          ops.NewRegistry(ops.Deps{Roots: cfg.Roots, Now: cfg.Now}),
//	        Roots:        cfg.Roots,
//	        RegistryPath: cfg.RegistryPath,
//	        LoadFleet:    func() (*registry.Fleet, error) { return registry.LoadNoRepair(cfg.RegistryPath) },
//	        Drivers:      drv,
//	        Redactor:     settings.NewRedactor(cfg.Roots),
//	        Doctor:       doctorFn,
//	        Now:          cfg.Now,
//	        Host:         cfg.Host,
//	        Mode:         cfg.Mode,
//	        SecretsTTL:   cfg.SecretsTTL,
//	        Sealer:       sealer,
//	        Logger:       cfg.Logger,
//	})
//	return eng, nil
//
// Until then it returns ErrEngineNotWired so that the surface exists, is
// authorized, is tested against a fake engine, and refuses honestly.
func BuildEngine(cfg EngineConfig) (jobs.Engine, error) {
	_ = cfg
	return nil, ErrEngineNotWired
}
