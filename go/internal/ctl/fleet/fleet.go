// Package fleet builds the dashboard's one poll: host facts plus a summary
// row per registry tenant. The shape is the viewer shape by construction —
// no path, no fingerprint, no key name appears anywhere in it — so viewers
// and operators receive the same body.
//
// Every probe is read-only and bounded: GET-only HTTP with a 2 s timeout and
// no redirects, `systemctl --user show` for the ctl's own account, and a
// cached `du` for the tenant data dirs (the poll runs every 15 s; `du` over
// a terabyte does not).
package fleet

import (
	"context"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/hostfacts"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// CtlUnit is the control plane's own user unit.
const CtlUnit = "ragstack-ctl.service"

// DefaultCtlUser is the account that runs it.
const DefaultCtlUser = "svcbvbrc"

// DefaultSudoersGroup is the fleet-admin group ADR-0007 names.
const DefaultSudoersGroup = "seed-admins-svcbvbrc"

// Probes are the read-only surfaces the view is built from. A nil member is
// replaced by its live implementation.
type Probes struct {
	Host   hostfacts.Host
	Prober hostfacts.Prober
	Disk   hostfacts.DiskUsage
	Now    func() time.Time

	CtlUser      string
	SudoersGroup string
	// ExternalStorePorts are the shared-store ports a tenant may point at
	// from outside its own block; nil ⇒ hostfacts.DefaultExternalStorePorts.
	// Every probe below is gated on them.
	ExternalStorePorts []int
}

// withDefaults fills the nil seams with their live implementations. It is
// called once per view build, never per tenant.
func (p Probes) withDefaults(roots paths.Roots) Probes {
	if p.Host == nil {
		p.Host = hostfacts.NewReal(roots)
	}
	if p.Prober == nil {
		p.Prober = hostfacts.NewProber()
	}
	if p.Disk == nil {
		p.Disk = hostfacts.NewCachedDU(time.Hour)
	}
	if p.CtlUser == "" {
		p.CtlUser = DefaultCtlUser
	}
	if p.SudoersGroup == "" {
		p.SudoersGroup = DefaultSudoersGroup
	}
	if p.ExternalStorePorts == nil {
		p.ExternalStorePorts = hostfacts.DefaultExternalStorePorts
	}
	return p
}

// Build assembles the fleet view. It never fails: a probe that cannot answer
// contributes `unknown` (or `n/a` where the ctl deliberately does not look),
// because a dashboard that 500s tells an operator less than one that says
// which leg is unreachable.
func Build(ctx context.Context, roots paths.Roots, f *registry.Fleet, p Probes) *model.FleetResponse {
	p = p.withDefaults(roots)
	now := time.Now().UTC()
	if p.Now != nil {
		now = p.Now().UTC()
	}

	resp := &model.FleetResponse{
		GeneratedAt:        now.Format(time.RFC3339),
		RegistryGeneration: f.Generation,
		Host:               hostFacts(roots, f, p),
		Tenants:            []model.FleetRow{},
	}
	listeners := listenerPorts(p.Host)
	for _, t := range Order(f) {
		resp.Tenants = append(resp.Tenants, row(ctx, t, p, listeners))
	}
	return resp
}

// Order returns the fleet's tenants in display_order, then everything not
// listed there by name — the order the dashboard renders and the order
// /v1/tenants uses.
func Order(f *registry.Fleet) []*registry.Tenant {
	var out []*registry.Tenant
	seen := map[string]bool{}
	for _, name := range f.DisplayOrder {
		if t, ok := f.Tenants[name]; ok && !seen[name] {
			seen[name] = true
			out = append(out, t)
		}
	}
	var rest []string
	for name := range f.Tenants {
		if !seen[name] {
			rest = append(rest, name)
		}
	}
	sort.Strings(rest)
	for _, name := range rest {
		out = append(out, f.Tenants[name])
	}
	return out
}

func hostFacts(roots paths.Roots, f *registry.Fleet, p Probes) model.HostFacts {
	h := model.HostFacts{SudoersGroup: []string{}}
	if free, err := p.Host.DiskFree(roots.RagRoot); err == nil && free > 0 {
		h.DiskFreeBytes = free
	}
	h.Linger = p.Host.Linger(p.CtlUser)
	h.CtlUnitActive = p.Host.UnitState(p.CtlUser, CtlUnit).ActiveState == "active"
	if n, err := p.Host.SysctlMaxMapCount(); err == nil && n > 0 {
		h.VMMaxMapCount = n
	}
	if members, err := p.Host.SudoersGroupMembers(p.SudoersGroup); err == nil {
		h.SudoersGroup = members
	}
	_ = f
	return h
}

func listenerPorts(h hostfacts.Host) map[int]hostfacts.Listener {
	out := map[int]hostfacts.Listener{}
	ls, err := h.Listeners()
	if err != nil {
		return out
	}
	for _, l := range ls {
		out[l.Port] = l
	}
	return out
}

func row(ctx context.Context, t *registry.Tenant, p Probes, listeners map[int]hostfacts.Listener) model.FleetRow {
	r := model.FleetRow{
		Name:         t.Name,
		ManifestName: t.ManifestName,
		State:        model.State(t.State),
		Owner:        model.Owner(t.Owner),
		Supervisor:   model.Supervisor(t.Supervisor),
		StoresMode:   StoresMode(t.Stores),
		CodeTag:      orDefault(t.Code.Tag, "unknown"),
		DriftCount:   len(t.Drift),
		Ports: model.FleetPorts{
			// The port the tenant actually talks to, which for a shared
			// store is outside its own block (6333/6343/9200).
			API:        t.Ports.API,
			QdrantHTTP: portOf(t.Stores.Qdrant.URL, t.Ports.QdrantHTTP),
			ESHTTP:     portOf(t.Stores.Elasticsearch.URL, t.Ports.ESHTTP),
		},
		Health:     health(ctx, t, p),
		Units:      units(t, p, listeners),
		DiskBytes:  0,
		LastBackup: nil,
	}
	if n, err := p.Disk.Usage(ctx, t.DataDir); err == nil && n > 0 {
		r.DiskBytes = n
	}
	if b := t.LastBackup; b != nil {
		r.LastBackup = &model.LastBackup{At: b.At, Fenced: b.Fenced, Verified: b.Verified}
	}
	return r
}

// StoresMode collapses the two store ownerships into the dashboard's word.
func StoresMode(s registry.Stores) model.StoresMode {
	q, e := s.Qdrant.Ownership, s.Elasticsearch.Ownership
	switch {
	case q == registry.OwnershipExclusive && e == registry.OwnershipExclusive:
		return model.StoresDedicated
	case q == registry.OwnershipShared && e == registry.OwnershipShared:
		return model.StoresShared
	case (q == registry.OwnershipExclusive && e == registry.OwnershipShared) ||
		(q == registry.OwnershipShared && e == registry.OwnershipExclusive):
		return model.StoresMixed
	default:
		return model.StoresUnknown
	}
}

// health probes the API and each store the ctl owns. A shared store is `n/a`:
// the ctl observes it, it is not this tenant's to be healthy or not. `deep`
// is `n/a` in PR-A — the deep probe needs the tenant's admin key, and no read
// endpoint in this PR is allowed to use one.
func health(ctx context.Context, t *registry.Tenant, p Probes) model.Health {
	h := model.Health{
		API:    probe(ctx, p, t, "http://127.0.0.1:"+strconv.Itoa(t.Ports.API)+"/health"),
		Qdrant: model.HealthNA,
		ES:     model.HealthNA,
		Deep:   model.HealthNA,
	}
	if t.Stores.Qdrant.Ownership == registry.OwnershipExclusive && t.Stores.Qdrant.URL != "" {
		h.Qdrant = probe(ctx, p, t, strings.TrimSuffix(t.Stores.Qdrant.URL, "/")+"/")
	}
	if t.Stores.Elasticsearch.Ownership == registry.OwnershipExclusive && t.Stores.Elasticsearch.URL != "" {
		h.ES = probe(ctx, p, t, strings.TrimSuffix(t.Stores.Elasticsearch.URL, "/")+"/")
	}
	return h
}

// probe is the ONLY way this package makes an HTTP request. Every URL is
// checked by hostfacts.AllowedStoreURL first — loopback, http, no userinfo,
// a port in the tenant's own block or in the external store set — and a URL
// that fails is never dialled at all.
//
// It matters because the URL comes from `stores.*.url` in the registry, which
// adopt copies out of a tenant.env an operator wrote. Probing it unchecked
// made the daemon a GET-anything-you-can-write-into-the-registry proxy, and
// `http://u:p@localhost:9200` would have put a credential on the wire (and
// into the probe's error strings) while satisfying every other test.
//
// A refused URL is `unknown`, not `down`: nothing was asked, so nothing is
// known. (The finding an operator acts on is doctor's store_url_disallowed,
// which names the same reason.)
func probe(ctx context.Context, p Probes, t *registry.Tenant, url string) model.HealthState {
	if ok, _ := hostfacts.AllowedStoreURL(url, t.Ports, p.ExternalStorePorts); !ok {
		return model.HealthUnknown
	}
	return statusOf(p.Prober.Probe(ctx, url))
}

// statusOf maps a probe result onto the contract's health states: 2xx is ok,
// any other answer is degraded (something replied, just not well), and no
// answer at all is down.
func statusOf(code int, err error) model.HealthState {
	switch {
	case err != nil:
		return model.HealthDown
	case code >= 200 && code < 300:
		return model.HealthOK
	case code == 0:
		return model.HealthDown
	default:
		return model.HealthDegraded
	}
}

// units reports the four services. For a systemd tenant that is what the
// user manager says (and `n/a` when the ctl may not ask — another account's
// manager). For a manual tenant there are no units, so the honest analogue is
// the listener: active when the port is held, inactive when it is not, and
// `n/a` for a leg this tenant does not run (a shared store, no UI).
func units(t *registry.Tenant, p Probes, listeners map[int]hostfacts.Listener) model.RowUnits {
	if t.Supervisor == string(model.SupervisorSystemd) {
		target, qdrant, es, api, ui := unitNames(t.Name)
		return model.RowUnits{
			Target: unitState(p, t.Owner, target),
			API:    unitState(p, t.Owner, api),
			UI:     unitState(p, t.Owner, ui),
			Qdrant: storeUnitState(p, t, t.Stores.Qdrant.Ownership, qdrant),
			ES:     storeUnitState(p, t, t.Stores.Elasticsearch.Ownership, es),
		}
	}
	listening := func(port int) model.UnitState {
		if port == 0 {
			return model.UnitNA
		}
		if _, ok := listeners[port]; ok {
			return model.UnitActive
		}
		return model.UnitInactive
	}
	u := model.RowUnits{
		Target: model.UnitNA, // a hand-started tenant has no target
		API:    listening(t.Ports.API),
		UI:     listening(int(t.UI.Port)),
		Qdrant: model.UnitNA,
		ES:     model.UnitNA,
	}
	if t.Stores.Qdrant.Ownership == registry.OwnershipExclusive {
		u.Qdrant = listening(t.Ports.QdrantHTTP)
	}
	if t.Stores.Elasticsearch.Ownership == registry.OwnershipExclusive {
		u.ES = listening(t.Ports.ESHTTP)
	}
	return u
}

func storeUnitState(p Probes, t *registry.Tenant, ownership, unit string) model.UnitState {
	if ownership != registry.OwnershipExclusive {
		return model.UnitNA
	}
	return unitState(p, t.Owner, unit)
}

// unitState maps systemd's ActiveState onto the contract's enum. Anything the
// ctl cannot ask about (another user's manager, no bus) is `n/a` rather than
// a guess.
func unitState(p Probes, owner, unit string) model.UnitState {
	switch st := p.Host.UnitState(owner, unit); st.ActiveState {
	case "active":
		return model.UnitActive
	case "activating":
		return model.UnitActivating
	case "deactivating":
		return model.UnitDeactivating
	case "inactive":
		return model.UnitInactive
	case "failed":
		return model.UnitFailed
	default:
		return model.UnitNA
	}
}

// unitNames mirrors render.UnitNames without importing the renderer (the view
// must not depend on the writer).
func unitNames(name string) (target, qdrant, es, api, ui string) {
	p := "ragstack-" + name
	return p + ".target", p + "-qdrant.service", p + "-es.service", p + "-api.service", p + "-ui.service"
}

// portOf extracts the port from a store URL, falling back to the block port
// when the URL is absent or unparsable.
func portOf(raw string, fallback int) int {
	if raw == "" {
		return fallback
	}
	u, err := url.Parse(raw)
	if err != nil {
		return fallback
	}
	p, err := strconv.Atoi(u.Port())
	if err != nil || p < 1024 || p > 65535 {
		return fallback
	}
	return p
}

func orDefault(v, def string) string {
	if v == "" {
		return def
	}
	return v
}
