package adopt

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
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
	f, created, err := loadOrCreate(registryPath, opts)
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
		f.Tenants[t.Name] = t
	}
	f.DisplayOrder = displayOrder(f, tenants, created, opts.Roots)
	if created {
		f.LegacyRoutes = legacyRoutes(f, opts.Roots)
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

// loadOrCreate reads the registry, or builds the fleet defaults (port base
// 24000, stride 20, the standard image paths) when there is none yet.
//
// The load NEVER repairs the projection on the way in: reconcile below decides
// whether this registry and this manifest.tsv agree, and a load that silently
// rewrote manifest.tsv first would be answering that question by erasing the
// evidence. A stale projection is therefore a REFUSAL, which --repair-projection
// converts into an explicit repair-then-commit.
func loadOrCreate(registryPath string, opts CommitOptions) (*registry.Fleet, bool, error) {
	f, diag, err := registry.LoadWithDiagnostics(registryPath)
	switch {
	case err == nil:
		if !diag.Stale() {
			return f, false, nil
		}
		if !opts.RepairProjection {
			return nil, false, fmt.Errorf(
				"adopt: %w — pass --repair-projection to rewrite manifest.tsv FROM the registry first, "+
					"but only after checking that the registry is the side that is right", diag.ProjectionStale)
		}
		repaired, rerr := registry.Repair(registryPath)
		if rerr != nil {
			return nil, false, fmt.Errorf("adopt: repair projection: %w", rerr)
		}
		return repaired, false, nil
	case errors.Is(err, os.ErrNotExist):
		// NewFleet already stamps registry.UnpinnedVersion/UnpinnedDigest on
		// both shared images — nobody has pinned the SIFs yet (`fleet image
		// list` does, at PR-D) — so there is nothing for adopt to fill in
		// here. The contract types version/digest as required strings and
		// adopt refuses to invent a digest.
		return registry.NewFleet(opts.Roots.RagRoot), true, nil
	default:
		return nil, false, err
	}
}

// displayOrder keeps the order the gateway already advertises. On a fresh
// registry it is read from the live routes.conf literal (so the landing page
// does not reorder itself the day the ctl takes over) and anything not listed
// there is appended in adoption order; on an existing registry the new names
// are appended.
func displayOrder(f *registry.Fleet, added []*registry.Tenant, created bool, roots paths.Roots) []string {
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
		for _, n := range doctor.DisplayOrderFromRoutes(filepath.Join(roots.ProxyDir, "snippets", "routes.conf")) {
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
	return append(order, rest...)
}

// legacyRoutes carries over the gateway rows that are NOT registry tenants —
// the pre-ctl lucid (:8010) and asm (:8000) read-only routes. They are read
// from the live routing table rather than assumed, and a row is kept only
// when the map gives it both an API and a UI port (the renderer needs both).
// Without this, the first generated gateway file would silently drop two
// live routes instead of being the semantic no-op PR-B requires.
func legacyRoutes(f *registry.Fleet, roots paths.Roots) []registry.LegacyRoute {
	maps, ok := doctor.GatewayMaps(roots.ProxyDir)
	if !ok {
		return f.LegacyRoutes
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
	return out
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
