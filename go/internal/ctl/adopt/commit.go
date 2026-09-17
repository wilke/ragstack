package adopt

import (
	"errors"
	"fmt"
	"os"
	"sort"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/doctor"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// CommitOptions carry what a write needs beyond the rows themselves.
type CommitOptions struct {
	// Roots is the deployment layout (used for the fleet defaults and to
	// find the live routes.conf when the registry is created).
	Roots paths.Roots
	// UpdatedBy is the audit principal recorded on the write
	// ("local:<uid>", "key:<label>", "bvbrc:<user>"[/sudo_user]).
	UpdatedBy string
	// RepairProjection authorises rewriting a stale manifest.tsv FROM the
	// registry before the commit. Off by default and deliberately so: the
	// repair is destructive (see registry.ErrProjectionStale — a one-tenant
	// registry over a four-row live manifest truncates it, and the next
	// new-tenant.sh reissues index 0 onto a live tenant's ports), so a
	// commit REFUSES over a stale projection rather than quietly fixing it.
	RepairProjection bool
	// Readopt replaces the row of a tenant that is ALREADY in the registry
	// with a fresh preview (a tenant re-provisioned, a store kind that the
	// contract only just learned — #535). The fields adoption cannot observe
	// on the host are carried over from the existing row: adopted_at,
	// desired_boot, restart_pending, rollback_descriptor, last_ops,
	// last_backup. Off by default: adoption happens once, and a silent
	// re-adoption is how a hand edit gets lost.
	Readopt bool
	// Overrides names the DECISIONS this call is making explicitly. Everything
	// it does not name that an operator may have decided through a preparation
	// op is carried over from the existing row rather than re-derived from the
	// host — see carryOver for why that is not the same as "live facts beat
	// recorded ones".
	Overrides Overrides
}

// Overrides are the preparation-op decisions a re-adoption is replacing.
//
// UI corresponds to --ui-mode/--ui-port and Bind to --api-bind. A false field
// means "this call has no opinion", and carryOver keeps what the row said.
//
// ConfirmedLegs is the same idea PER LEG, and it has to be: `--confirm-stores
// qdrant` on a tenant whose elasticsearch was confirmed last week is a
// statement about qdrant and about nothing else. A single boolean would have
// reset the leg it did not name, which is the same class of silent revert this
// whole mechanism exists to prevent.
type Overrides struct {
	UI            bool
	Bind          bool
	ConfirmedLegs []string
}

// confirmed reports whether this call decided leg's capabilities itself.
func (o Overrides) confirmed(leg string) bool {
	for _, l := range o.ConfirmedLegs {
		if l == leg {
			return true
		}
	}
	return false
}

// Commit writes one adopted tenant. It is CommitAll with a single row, and
// carries the same manifest reconciliation: a manifest.tsv naming tenants the
// registry would not reproduce is a refusal, not something to overwrite.
func Commit(registryPath string, t *registry.Tenant, opts CommitOptions) error {
	return CommitAll(registryPath, []*registry.Tenant{t}, opts)
}

// CommitAll writes a batch of adopted tenants as one registry generation.
// Adopting the four live tenants in one call is what lets the batch reconcile
// with a four-row manifest.tsv that a single adoption never could.
func CommitAll(registryPath string, tenants []*registry.Tenant, opts CommitOptions) error {
	if len(tenants) == 0 {
		return errors.New("adopt: nothing to commit")
	}
	f, diag, created, err := loadCurrent(registryPath, opts)
	if err != nil {
		return err
	}
	for _, t := range tenants {
		if t == nil {
			return errors.New("adopt: nil tenant in batch")
		}
		if prev, exists := f.Tenants[t.Name]; exists {
			if !opts.Readopt {
				return fmt.Errorf("adopt: tenant %q is already in %s — adoption happens once (pass --readopt to replace the row from a fresh preview)", t.Name, registryPath)
			}
			carryOver(t, prev, opts.Overrides)
		}
	}
	// Every refusal decidable from the load alone (duplicate tenants above;
	// more may join them later) has had its chance. Only now that the commit
	// is certain to proceed may the projection be repaired: repairing first
	// and refusing on a LATER check would leave manifest.tsv rewritten FROM
	// the registry on disk — dropping any row the registry does not yet
	// know, such as one just appended for the very tenant this commit is
	// trying, and failing, to add — with nothing committed to show for it.
	if diag.Stale() {
		if !opts.RepairProjection {
			return fmt.Errorf(
				"adopt: %w — pass --repair-projection to rewrite manifest.tsv FROM the registry first, "+
					"but only after checking that the registry is the side that is right", diag.ProjectionStale)
		}
		repaired, rerr := registry.Repair(registryPath)
		if rerr != nil {
			return fmt.Errorf("adopt: repair projection: %w", rerr)
		}
		f = repaired
	}
	for _, t := range tenants {
		f.Tenants[t.Name] = t
	}
	order, err := displayOrder(f, tenants, created, opts.Roots)
	if err != nil {
		return err
	}
	f.DisplayOrder = order
	if created {
		routes, err := legacyRoutes(f, opts.Roots)
		if err != nil {
			return err
		}
		f.LegacyRoutes = routes
	}
	if err := reconcile(registryPath, f); err != nil {
		return err
	}
	by := opts.UpdatedBy
	if by == "" {
		by = fmt.Sprintf("local:%d", os.Getuid())
	}
	return registry.Save(registryPath, f, by)
}

// loadCurrent reads the registry, or builds the fleet defaults (port base
// 24000, stride 20, the standard image paths) when there is none yet. It
// NEVER repairs the projection itself — repairing is destructive (it
// rewrites manifest.tsv FROM the registry) and CommitAll must run every
// other refusal check first, against this as-loaded fleet, before deciding
// whether a repair is even reachable. The staleness diagnostic is returned
// alongside the fleet so CommitAll can act on it once it knows the commit
// will proceed.
func loadCurrent(registryPath string, opts CommitOptions) (*registry.Fleet, registry.Diagnostics, bool, error) {
	f, diag, err := registry.LoadWithDiagnostics(registryPath)
	switch {
	case err == nil:
		return f, diag, false, nil
	case errors.Is(err, os.ErrNotExist):
		// NewFleet already stamps registry.UnpinnedVersion/UnpinnedDigest on
		// both shared images — nobody has pinned the SIFs yet (`fleet image
		// list` does, at PR-D) — so there is nothing for adopt to fill in
		// here. The contract types version/digest as required strings and
		// adopt refuses to invent a digest.
		return registry.NewFleet(opts.Roots.RagRoot), registry.Diagnostics{}, true, nil
	default:
		return nil, registry.Diagnostics{}, false, err
	}
}

// displayOrder keeps the order the gateway already advertises. On a fresh
// registry it is read from the live routes.conf literal (so the landing page
// does not reorder itself the day the ctl takes over) and anything not listed
// there is appended in adoption order; on an existing registry the new names
// are appended.
func displayOrder(f *registry.Fleet, added []*registry.Tenant, created bool, roots paths.Roots) ([]string, error) {
	var order []string
	seen := map[string]bool{}
	keep := func(name string) {
		if seen[name] || f.Tenants[name] == nil {
			return
		}
		seen[name] = true
		order = append(order, name)
	}
	if created {
		live, err := doctor.DisplayOrder(roots.ProxyDir)
		if err != nil {
			// An unreadable proxy tree must NOT be read as "the gateway
			// advertises nothing": that silently reorders the landing page.
			return nil, fmt.Errorf("adopt: %w", err)
		}
		for _, n := range live {
			keep(n)
		}
	}
	for _, n := range f.DisplayOrder {
		keep(n)
	}
	for _, t := range added {
		keep(t.Name)
	}
	// Any tenant the registry holds but nothing ordered (a row written
	// before display_order existed) goes last, by name, so the list always
	// covers the fleet.
	var rest []string
	for name := range f.Tenants {
		if !seen[name] {
			rest = append(rest, name)
		}
	}
	sort.Strings(rest)
	return append(order, rest...), nil
}

// legacyRoutes carries over the gateway rows that are NOT registry tenants —
// the pre-ctl lucid (:8010) and asm (:8000) read-only routes. They are read
// from the live routing table rather than assumed, and a row is kept only
// when the map gives it both an API and a UI port (the renderer needs both).
// Without this, the first generated gateway file would silently drop two
// live routes instead of being the semantic no-op PR-B requires.
func legacyRoutes(f *registry.Fleet, roots paths.Roots) ([]registry.LegacyRoute, error) {
	maps, ok, err := doctor.GatewayMaps(roots.ProxyDir)
	if err != nil {
		// Same reason as displayOrder: an unreadable live routing table would
		// otherwise DROP the legacy rows from the first generated gateway file.
		return nil, fmt.Errorf("adopt: reading the live routing table under %s: %w", roots.ProxyDir, err)
	}
	if !ok {
		return f.LegacyRoutes, nil
	}
	out := append([]registry.LegacyRoute(nil), f.LegacyRoutes...)
	known := map[string]bool{}
	for _, r := range out {
		known[r.Name] = true
	}
	var names []string
	for name := range maps.API {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		if known[name] || f.Tenants[name] != nil || paths.ValidateName(name) != nil {
			continue
		}
		ui, hasUI := maps.UI[name]
		if !hasUI {
			continue
		}
		out = append(out, registry.LegacyRoute{
			Name: name, API: maps.API[name], UI: ui,
			Readonly: maps.Readonly[name], Status: "active",
			Note: "carried over from " + maps.Source + " at adoption; not a registry tenant",
		})
	}
	return out, nil
}

// reconcile refuses a write whose manifest.tsv holds rows the registry would
// not reproduce. Unknown rows (a tenant the registry does not know) and
// index/base disagreements are refusals — the manifest is the allocator's
// record and a mismatch means two allocators. Rows the manifest is MISSING
// are not: Save rewrites the projection from the registry, which is exactly
// what adding a tenant does.
func reconcile(registryPath string, f *registry.Fleet) error {
	man := registry.PathsFor(registryPath).Manifest
	b, err := os.ReadFile(man)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil
		}
		return err
	}
	err = registry.ReconcileManifest(f, b)
	switch {
	case err == nil, errors.Is(err, registry.ErrManifestMissingRows):
		return nil
	default:
		return fmt.Errorf("adopt: %s does not reconcile with the registry: %w — adopt every row in one batch", man, err)
	}
}

// driftStamp is when the disagreement was observed: the preview's own adoption
// stamp, which is the moment the host was read. A row whose adopted_at is
// somehow empty falls back to now — a drift row the contract refuses for want
// of a timestamp would take the whole commit down over a note.
func driftStamp(t *registry.Tenant) string {
	if t.AdoptedAt != "" {
		return string(t.AdoptedAt)
	}
	return time.Now().UTC().Format(time.RFC3339)
}

// carryOver copies onto a re-adopted row the state adoption does not read
// from the host, so a --readopt never resets what an operator or a job set.
//
// Three of those fields ARE observable, and carrying them over anyway is the
// correction PR-E forced. Adoption's rule is "live facts beat recorded ones",
// which is right for a fact the host is the authority on (an ES heap, a pid, a
// listening port) and WRONG for a DECISION an operator recorded and the host
// has not caught up with yet:
//
//   - ui.mode / ui.port: `tenant set-ui-mode <t> static` records `static` with
//     no port. A fresh preview of the same tenant infers `external` (or `dev`)
//     from --ui-port, so the runbook's own A5 command would have flipped
//     hackathon's live static UI to `external` with port 0 — a tenant whose
//     gateway row then points at nothing.
//   - api.bind: `tenant set-bind <t> 127.0.0.1` is effective at the NEXT
//     restart, so between A4 and the handover the live process still binds
//     0.0.0.0 and a re-adoption would write that back over the decision.
//   - stores.*.capabilities: adoption starts every capability false by design.
//     A re-adoption that reset them would undo A5 — and `--confirm-stores`
//     runs on the preview row, so without Overrides.ConfirmedLegs below it
//     would undo the confirmation made in the very same command. The carry is
//     per LEG: `--confirm-stores qdrant` says nothing about elasticsearch.
//
// The disagreement is not swallowed: a bind that differs from the live process
// is recorded as a drift row, which is what the registry has for "these two
// facts do not match and a human should know".
func carryOver(next, prev *registry.Tenant, over Overrides) {
	if prev == nil || next == nil {
		return
	}
	next.AdoptedAt = prev.AdoptedAt
	if prev.DesiredBoot != "" {
		next.DesiredBoot = prev.DesiredBoot
	}
	next.RestartPending = prev.RestartPending
	next.RollbackDescriptor = prev.RollbackDescriptor
	if len(prev.LastOps) > 0 {
		next.LastOps = prev.LastOps
	}
	next.LastBackup = prev.LastBackup

	if !over.UI && prev.UI.Mode != "" {
		next.UI = prev.UI
	}
	if !over.Bind && prev.API.Bind != "" {
		if observed := next.API.Bind; observed != "" && observed != prev.API.Bind {
			next.Drift = append(next.Drift, registry.Drift{
				Code: doctor.APIBindDrift, Level: string(model.LevelWarn), Field: "api.bind",
				Expected: prev.API.Bind, Actual: observed, ObservedAt: driftStamp(next),
				Note: "the recorded bind is kept: it takes effect at the tenant's next restart",
			})
		}
		next.API.Bind = prev.API.Bind
	}
	// Per leg: a confirmation this call made stands, and every OTHER leg keeps
	// what it had. Adoption starts every capability false, so a leg the caller
	// did not name would otherwise be un-confirmed by a command that never
	// mentioned it.
	if !over.confirmed("qdrant") {
		next.Stores.Qdrant.Capabilities = prev.Stores.Qdrant.Capabilities
	}
	if !over.confirmed("elasticsearch") {
		next.Stores.Elasticsearch.Capabilities = prev.Stores.Elasticsearch.Capabilities
	}
	if !over.confirmed("postgres") {
		next.Stores.Postgres.Capabilities = prev.Stores.Postgres.Capabilities
	}
}
