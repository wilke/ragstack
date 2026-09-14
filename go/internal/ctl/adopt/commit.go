package adopt

import (
	"errors"
	"fmt"
	"os"
	"sort"

	"github.com/ragstack/ragstack/internal/ctl/doctor"
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
		if _, exists := f.Tenants[t.Name]; exists {
			return fmt.Errorf("adopt: tenant %q is already in %s — adoption happens once", t.Name, registryPath)
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
