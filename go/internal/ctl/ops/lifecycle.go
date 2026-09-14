package ops

import (
	"context"
	"fmt"
	"strconv"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/render"
)

// Supervisor values (registry.json tenant.supervisor).
const (
	supervisorSystemd = "systemd"
	supervisorManual  = "manual"
)

// component is one leg of a tenant the lifecycle ops act on.
type component struct {
	Name string // api | ui | qdrant | es
	Unit string
	Port int
	// Managed is false when the ctl owns no unit for this leg — a shared
	// store, an external UI — in which case Why says so and the plan records
	// a skipped step rather than pretending.
	Managed bool
	Why     string
}

// legs returns the components of t in START order (stores, api, ui), filtered
// by `only` when it is non-empty.
func (p *planner) legs(only []string) ([]component, error) {
	t := p.t
	_, qdrantUnit, esUnit, apiUnit, uiUnit := render.UnitNames(t.Name)
	all := []component{
		storeLeg("qdrant", qdrantUnit, t.Stores.Qdrant.Ownership, t.Stores.Qdrant.Capabilities, t.Ports.QdrantHTTP),
		storeLeg("es", esUnit, t.Stores.Elasticsearch.Ownership, t.Stores.Elasticsearch.Capabilities, t.Ports.ESHTTP),
		{Name: "api", Unit: apiUnit, Port: t.Ports.API, Managed: true},
		uiLeg(t, uiUnit),
	}
	if len(only) == 0 {
		return all, nil
	}
	var out []component
	for _, want := range only {
		found := false
		for _, c := range all {
			if c.Name == want {
				out = append(out, c)
				found = true
			}
		}
		if !found {
			return nil, fmt.Errorf("%w: %s has no %q leg", jobs.ErrValidation, t.Name, want)
		}
	}
	return out, nil
}

func storeLeg(name, unit, ownership string, caps registry.Capabilities, port int) component {
	c := component{Name: name, Unit: unit, Port: port}
	switch {
	case ownership != registry.OwnershipExclusive:
		c.Why = fmt.Sprintf("the %s store is %s, not exclusive to this tenant: the ctl supervises only what it owns", name, ownership)
	case !caps.Stop:
		// The plan's rule, and the reason it is a rule: capabilities are all
		// false until an operator has CONFIRMED process identity, backing path
		// and exclusive ownership. Until then, starting or stopping this
		// "tenant's" store is an action against a server the ctl has only
		// guessed is the tenant's.
		c.Why = fmt.Sprintf("capabilities.stop is false for the %s store, so the ctl never touches it (confirm ownership with `adopt` first)", name)
	default:
		c.Managed = true
	}
	return c
}

func uiLeg(t *registry.Tenant, unit string) component {
	c := component{Name: "ui", Unit: unit, Port: int(t.UI.Port)}
	switch t.UI.Mode {
	case registry.UIModeDev:
		c.Managed = true
	case registry.UIModeStatic:
		c.Why = "the UI is static (nginx serves <data_dir>/ui/dist); there is no unit to start or stop"
	default:
		c.Why = "the UI is external (a hand-run dev server the gateway proxies to); the ctl does not supervise it"
	}
	return c
}

// reverse is stop order.
func reverse(cs []component) []component {
	out := make([]component, len(cs))
	for i, c := range cs {
		out[len(cs)-1-i] = c
	}
	return out
}

// ---------------------------------------------------------------- start

func planStart(_ context.Context, p *planner, args map[string]any) error {
	p.need(model.LockTenant)
	if err := p.requireSystemd("start"); err != nil {
		return err
	}
	legs, err := p.legs(argStringsOf(args, "only"))
	if err != nil {
		return err
	}
	p.add(step{
		Kind: "systemd", Title: "systemctl --user daemon-reload",
		WouldRun: []model.WouldRun{{Argv: []string{"/usr/bin/systemctl", "--user", "daemon-reload"}}},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return "daemon-reload", sc.Ops.Drivers.Systemd().DaemonReload(ctx)
		},
	})
	started := 0
	for _, c := range legs {
		if !c.Managed {
			p.skip("systemd", "skip "+c.Name, c.Why, c.Name)
			continue
		}
		p.addUnitStep("start", c)
		started++
	}
	if started == 0 {
		p.warn("nothing to start: every selected leg is a store or UI the ctl does not supervise")
		return nil
	}
	p.addReadyStep(legs)
	return nil
}

// ---------------------------------------------------------------- stop

func planStop(_ context.Context, p *planner, args map[string]any) error {
	p.need(model.LockTenant)
	force, keepEnabled := argBoolOf(args, "force"), argBoolOf(args, "keep_enabled")
	only := argStringsOf(args, "only")

	if p.t.Supervisor != supervisorSystemd {
		// A hand-started tenant has no unit to stop. With --force there is
		// still an honest way to stop it — the pidfile, verified against
		// /proc before the signal — and that path is the rollback half of a
		// handover, so it has to exist before handover does.
		if !force {
			return p.refuse("%s is a hand-started tenant (supervisor: %s); the ctl has no unit to stop. "+
				"`handover` first, or pass force to stop it through its pidfile", p.t.Name, p.t.Supervisor)
		}
		return p.planManualStop(only)
	}
	legs, err := p.legs(only)
	if err != nil {
		return err
	}
	if !force {
		p.warn("a stop over a running ingest job is refused; pass force to stop anyway")
	}
	for _, c := range reverse(legs) {
		if !c.Managed {
			p.skip("systemd", "skip "+c.Name, c.Why, c.Name)
			continue
		}
		p.addUnitStep("stop", c)
	}
	if keepEnabled {
		p.warn("desired_boot is left as it is (keep_enabled)")
	} else {
		p.warn("desired_boot becomes `disabled`: the tenant will not come back at the next boot until it is started again")
		p.result["desired_boot"] = "disabled"
		for _, c := range reverse(legs) {
			if c.Managed {
				p.addUnitStep("disable", c)
			}
		}
	}
	return nil
}

// planManualStop is `stop --force` on a supervisor: manual tenant: the
// pidfile, the identity check, the signal, and proof that the port is free.
func (p *planner) planManualStop(only []string) error {
	if len(only) > 0 && (len(only) != 1 || only[0] != "api") {
		return p.refuse("a hand-started tenant is stopped as a whole; only=[api] is the only selection it accepts")
	}
	pidfile := p.t.API.PidFile
	if pidfile == "" {
		pidfile = p.tpaths.PidFile
	}
	worktree, port := p.t.Worktree, p.t.Ports.API
	p.add(step{
		Kind: "proc", Title: "stop the hand-started API through its pidfile, after verifying cwd and cmdline",
		Destructive: true, Targets: []string{pidfile},
		Warnings: []string{"the ctl signals a pid it has verified is this tenant's; it never runs pkill"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			b, err := sc.Ops.Drivers.Files().ReadFile(ctx, pidfile)
			if err != nil {
				return "", fmt.Errorf("reading %s: %w", pidfile, err)
			}
			pid, err := strconv.Atoi(strings.TrimSpace(string(b)))
			if err != nil || pid <= 1 {
				return "", fmt.Errorf("%w: %s does not name a pid (%q)", jobs.ErrRefused, pidfile, strings.TrimSpace(string(b)))
			}
			// The pid is the external ID, recorded BEFORE the signal: a crash
			// between the two leaves a record reconcile can act on.
			if err := sc.Checkpoint("pid:" + strconv.Itoa(pid)); err != nil {
				return "", err
			}
			if err := sc.Ops.Drivers.Proc().Signal(ctx, pid, worktree, "uvicorn", "TERM"); err != nil {
				return "", err
			}
			return fmt.Sprintf("SIGTERM to pid %d (cwd %s)", pid, worktree), nil
		},
		Reconcile: func(ctx context.Context, sc *jobs.StepContext) (jobs.Reconciliation, error) {
			listening, err := sc.Ops.Drivers.Proc().Listening(ctx, port)
			if err != nil {
				return jobs.ReconcileStuck, err
			}
			if listening {
				return jobs.ReconcileRedo, nil
			}
			return jobs.ReconcileDone, nil
		},
	})
	p.add(step{
		Kind: "probe", Title: fmt.Sprintf("verify nothing listens on %d any more", port),
		Targets: []string{strconv.Itoa(port)},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			listening, err := sc.Ops.Drivers.Proc().Listening(ctx, port)
			if err != nil {
				return "", err
			}
			if listening {
				return "", fmt.Errorf("%w: something is still listening on %d after the signal", jobs.ErrRefused, port)
			}
			return fmt.Sprintf("port %d is free", port), nil
		},
	})
	p.warn("this tenant is hand-started: the ctl stops it but cannot start it again — that is what `handover` is for")
	return nil
}

// ---------------------------------------------------------------- restart

func planRestart(ctx context.Context, p *planner, args map[string]any) error {
	p.need(model.LockTenant)
	if err := p.requireSystemd("restart"); err != nil {
		return err
	}
	legs, err := p.legs(argStringsOf(args, "only"))
	if err != nil {
		return err
	}
	for _, c := range reverse(legs) {
		if !c.Managed {
			p.skip("systemd", "skip "+c.Name, c.Why, c.Name)
			continue
		}
		p.addUnitStep("stop", c)
	}
	for _, c := range legs {
		if c.Managed {
			p.addUnitStep("start", c)
		}
	}
	p.addReadyStep(legs)
	_ = ctx
	return nil
}

// requireSystemd is the one refusal every lifecycle op shares.
func (p *planner) requireSystemd(verb string) error {
	if p.t.Supervisor == supervisorSystemd {
		return nil
	}
	return p.refuse("%s is a hand-started tenant (supervisor: %s); `%s` needs units the ctl owns — hand it over first "+
		"(`ragstack-ctl tenant handover %s`)", p.t.Name, p.t.Supervisor, verb, p.t.Name)
}

// addUnitStep plans one systemctl verb against one unit, with the unit name
// checkpointed before the call, the inverse verb as the rollback, and a
// reconcile that asks systemd what actually happened.
func (p *planner) addUnitStep(verb string, c component) {
	dest := verb == "stop" || verb == "disable"
	title := map[string]string{
		"start":   "start " + c.Unit,
		"stop":    "stop " + c.Unit,
		"disable": "disable " + c.Unit + " (desired_boot)",
	}[verb]
	want := verb == "start"
	p.add(step{
		Kind: "systemd", Title: title, Destructive: dest, Targets: []string{c.Unit},
		WouldRun: []model.WouldRun{{Argv: []string{"/usr/bin/systemctl", "--user", verb, c.Unit}}},
		Run:      unitRun(verb, c.Unit),
		Rollback: unitRun(inverseVerb(verb), c.Unit),
		Reconcile: func(ctx context.Context, sc *jobs.StepContext) (jobs.Reconciliation, error) {
			if verb == "disable" {
				// Enablement is not readable through the Systemd seam, so a
				// worker that died here cannot tell; saying so is the honest
				// answer, and re-running `disable` is safe anyway.
				return jobs.ReconcileRedo, nil
			}
			active, err := sc.Ops.Drivers.Systemd().IsActive(ctx, c.Unit)
			if err != nil {
				return jobs.ReconcileStuck, err
			}
			if active == want {
				return jobs.ReconcileDone, nil
			}
			return jobs.ReconcileRedo, nil
		},
	})
}

func inverseVerb(verb string) string {
	switch verb {
	case "start":
		return "stop"
	case "stop":
		return "start"
	case "disable":
		return "enable"
	default:
		return verb
	}
}

func unitRun(verb, unit string) jobs.StepFunc {
	return func(ctx context.Context, sc *jobs.StepContext) (string, error) {
		// The unit name is the external ID: recorded before the call, so a
		// crash mid-systemctl leaves a record reconcile can resolve.
		if err := sc.Checkpoint("unit:" + unit); err != nil {
			return "", err
		}
		s := sc.Ops.Drivers.Systemd()
		var err error
		switch verb {
		case "start":
			err = s.Start(ctx, unit)
		case "stop":
			err = s.Stop(ctx, unit)
		case "enable":
			err = s.Enable(ctx, unit)
		case "disable":
			err = s.Disable(ctx, unit)
		}
		if err != nil {
			return "", err
		}
		return verb + " " + unit, nil
	}
}

// addReadyStep plans the readiness gate: the API port has to be listening
// before the job calls itself done, because "systemctl start returned" is not
// "the tenant answers".
func (p *planner) addReadyStep(legs []component) {
	port := 0
	for _, c := range legs {
		if c.Name == "api" && c.Managed {
			port = c.Port
		}
	}
	if port == 0 {
		return
	}
	p.add(step{
		Kind: "probe", Title: fmt.Sprintf("wait for the API to listen on %d", port),
		Targets: []string{strconv.Itoa(port)},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			listening, err := sc.Ops.Drivers.Proc().Listening(ctx, port)
			if err != nil {
				return "", err
			}
			if !listening {
				return "", fmt.Errorf("nothing is listening on %d: the API did not come up", port)
			}
			return fmt.Sprintf("port %d is listening", port), nil
		},
	})
}
