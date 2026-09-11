package fleet

import (
	"context"
	"fmt"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/hostfacts"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// TenantView builds one tenant's response: the dashboard row, the live
// status from /proc, the unit (or manual-process) states, and the drift
// computed NOW — as opposed to registry.drift, which is what the last
// adopt/doctor RECORDED.
//
// includeRegistry is the role gate: an operator receives the full registry
// row, a viewer receives null, because the row carries secret refs, key
// fingerprints, pidfile paths and the rollback descriptor.
func TenantView(ctx context.Context, roots paths.Roots, t *registry.Tenant, p Probes, includeRegistry bool) model.TenantResponse {
	p = p.withDefaults(roots)
	return tenantView(ctx, t, p, includeRegistry, listenerPorts(p.Host))
}

// tenantView is TenantView with the /proc scan already done. Every caller
// that builds more than one row shares ONE scan: TenantsView used to call
// TenantView per tenant, so listing four tenants walked /proc — every pid,
// every fd link — four times over, and the four answers could disagree with
// each other because they were taken at four different instants.
func tenantView(ctx context.Context, t *registry.Tenant, p Probes, includeRegistry bool, listeners map[int]hostfacts.Listener) model.TenantResponse {
	now := time.Now().UTC()
	if p.Now != nil {
		now = p.Now().UTC()
	}
	view := model.TenantResponse{
		Summary: row(ctx, t, p, listeners),
		Status:  status(t, listeners, now),
		Units:   services(t, p, listeners),
		Drift:   LiveDrift(t, p, listeners, now),
	}
	if includeRegistry {
		view.Registry = t
	}
	return view
}

// TenantsView builds the list body in display order.
func TenantsView(ctx context.Context, roots paths.Roots, f *registry.Fleet, p Probes, includeRegistry bool) model.TenantsResponse {
	p = p.withDefaults(roots)
	now := time.Now().UTC()
	if p.Now != nil {
		now = p.Now().UTC()
	}
	out := model.TenantsResponse{
		GeneratedAt:        now.Format(time.RFC3339),
		RegistryGeneration: f.Generation,
		Tenants:            []model.TenantResponse{},
	}
	listeners := listenerPorts(p.Host)
	for _, t := range Order(f) {
		out.Tenants = append(out.Tenants, tenantView(ctx, t, p, includeRegistry, listeners))
	}
	return out
}

func status(t *registry.Tenant, listeners map[int]hostfacts.Listener, now time.Time) model.LiveStatus {
	api, up := listeners[t.Ports.API]
	s := model.LiveStatus{
		ObservedAt:     now.Format(time.RFC3339),
		RestartPending: t.RestartPending,
		// PR-A has no job engine: nothing holds a tenant lock yet.
		RunningJobs: []string{},
		Listening: model.Listening{
			API:        up,
			QdrantHTTP: has(listeners, portOf(t.Stores.Qdrant.URL, t.Ports.QdrantHTTP)),
			ESHTTP:     has(listeners, portOf(t.Stores.Elasticsearch.URL, t.Ports.ESHTTP)),
			UI:         has(listeners, int(t.UI.Port)),
		},
	}
	if up && api.Pid > 0 {
		s.APIPid = model.NullInt(api.Pid)
		s.APIPidOwner = model.NullString(api.User)
	}
	return s
}

func has(listeners map[int]hostfacts.Listener, port int) bool {
	if port == 0 {
		return false
	}
	_, ok := listeners[port]
	return ok
}

// services lists the tenant's four legs. A systemd tenant reports its units;
// a hand-started one reports `manual:<kind>` rows whose state is the
// listener's, which is the only evidence a hand-started process leaves.
func services(t *registry.Tenant, p Probes, listeners map[int]hostfacts.Listener) model.TenantUnits {
	u := model.TenantUnits{Supervisor: model.Supervisor(t.Supervisor), Services: []model.Service{}}
	rowUnits := units(t, p, listeners)
	target, qdrantUnit, esUnit, apiUnit, uiUnit := unitNames(t.Name)
	systemd := t.Supervisor == string(model.SupervisorSystemd)
	if systemd {
		u.Target = &model.UnitTarget{
			Name:        target,
			ActiveState: rowUnits.Target,
			// desired_boot is the registry's record of WantedBy=default.target.
			Enabled: t.DesiredBoot == "enabled",
		}
	}
	add := func(kind, unit string, state model.UnitState, port int) {
		if state == model.UnitNA {
			return
		}
		s := model.Service{Kind: kind, Name: unit, ActiveState: state}
		if !systemd {
			s.Name = "manual:" + kind
			if l, ok := listeners[port]; ok && l.Pid > 0 {
				s.MainPID = model.NullInt(l.Pid)
			}
		}
		u.Services = append(u.Services, s)
	}
	add("api", apiUnit, rowUnits.API, t.Ports.API)
	add("ui", uiUnit, rowUnits.UI, int(t.UI.Port))
	add("qdrant", qdrantUnit, rowUnits.Qdrant, portOf(t.Stores.Qdrant.URL, t.Ports.QdrantHTTP))
	add("es", esUnit, rowUnits.ES, portOf(t.Stores.Elasticsearch.URL, t.Ports.ESHTTP))
	return u
}

// LiveDrift compares the registry with what is true right now. PR-A computes
// the three facts it can establish read-only and cheaply: who owns the API
// process, whether the recorded state matches reality, and which tag the
// worktree describes to. Everything else (heap, env values) stays in
// registry.drift, where adopt recorded it.
func LiveDrift(t *registry.Tenant, p Probes, listeners map[int]hostfacts.Listener, now time.Time) []registry.Drift {
	out := []registry.Drift{}
	at := now.Format(time.RFC3339)
	api, up := listeners[t.Ports.API]
	if up && api.User != "" && api.User != t.Owner {
		out = append(out, registry.Drift{
			Code: "pid_owner", Level: string(model.LevelError), Field: "owner",
			Expected: t.Owner, Actual: api.User, ObservedAt: at,
			Note: fmt.Sprintf("pid %d holds :%d", api.Pid, t.Ports.API),
		})
	}
	wantActive := t.State == string(model.StateActive)
	if wantActive != up {
		out = append(out, registry.Drift{
			Code: "state", Level: string(model.LevelWarn), Field: "state",
			Expected: t.State, Actual: liveState(up), ObservedAt: at,
			Note: fmt.Sprintf(":%d %s", t.Ports.API, listeningWord(up)),
		})
	}
	if tag, err := p.Host.GitDescribe(t.Worktree); err == nil && tag != "" && t.Code.Tag != "" && tag != t.Code.Tag {
		out = append(out, registry.Drift{
			Code: "code_tag", Level: string(model.LevelWarn), Field: "code.tag",
			Expected: t.Code.Tag, Actual: tag, ObservedAt: at,
			Note: "the worktree describes to a different tag than the registry records",
		})
	}
	return out
}

func liveState(up bool) string {
	if up {
		return string(model.StateActive)
	}
	return string(model.StateStopped)
}

func listeningWord(up bool) string {
	if up {
		return "is listening"
	}
	return "is not listening"
}
