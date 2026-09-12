package api

import (
	"context"
	"errors"

	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// Backend is everything the read surface needs from the rest of the ctl.
//
// It exists so the HTTP layer can be tested, and the daemon run, without a
// live host: `--fake-drivers` supplies FakeBackend, the conformance suite runs
// against that, and PR-B/PR-C swap in the implementation over
// registry + hostfacts + the op packages without touching a handler.
//
// Every method is a READ. Nothing here writes, starts, stops or reloads —
// PR-A has no mutation path at all, and keeping the interface read-only is
// what makes that statement checkable rather than aspirational.
type Backend interface {
	// Fleet is the dashboard poll: host facts plus one summary row per tenant,
	// in display order.
	Fleet(ctx context.Context) (*model.FleetResponse, error)
	// Tenants lists every tenant in the operator shape; the handler applies
	// the viewer reduction.
	Tenants(ctx context.Context) (*model.TenantsResponse, error)
	// Tenant is one tenant. viewer asks the backend not to assemble the
	// registry row at all, rather than assembling it and dropping it in the
	// handler: the row is the expensive part and a viewer never receives it.
	Tenant(ctx context.Context, name string, viewer bool) (*model.TenantResponse, error)
	// Env is the classified key list of one tenant. Values appear only for
	// public keys; everything else is the literal "<redacted>".
	Env(ctx context.Context, name string) (*model.EnvResponse, error)
	// Logs is a redacted, bounded tail of one tenant log.
	Logs(ctx context.Context, name, file string, lines int) (*model.LogsResponse, error)
	// Doctor runs the diagnostics, optionally scoped to one tenant and one op.
	Doctor(ctx context.Context, tenant, op string) (*model.DoctorResponse, error)
	// Registry is the current fleet record. The gateway and settings reads are
	// projections of it, so they take it from here rather than from a second
	// source that could disagree. (Not in the PR-A plan's method list; added
	// because /v1/gateway and /v1/settings are viewer operations the
	// conformance authorization matrix calls, and inventing a second registry
	// accessor for them would be the drift this interface exists to prevent.)
	Registry(ctx context.Context) (*registry.Fleet, error)
}

// ErrNotFound is what a backend returns for an unknown tenant, or for a log
// file a tenant does not have. The handler turns it into the contract's 404 —
// always AFTER the authorization gate, so a viewer cannot probe for existence.
var ErrNotFound = errors.New("not found")
