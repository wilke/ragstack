package ops

import (
	"context"
	"fmt"
	"strconv"
	"strings"
	"time"

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
	Name string // api | ui | qdrant | es | postgres
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
	_, qdrantUnit, esUnit, pgUnit, apiUnit, uiUnit := render.UnitNames(t.Name)
	all := []component{
		storeLeg("qdrant", qdrantUnit, t.Stores.Qdrant.Ownership, t.Stores.Qdrant.Capabilities, t.Ports.QdrantHTTP),
		storeLeg("es", esUnit, t.Stores.Elasticsearch.Ownership, t.Stores.Elasticsearch.Capabilities, t.Ports.ESHTTP),
		postgresLeg(t, pgUnit),
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

// postgresLeg is the tenant's OWN relational store. Only `local` is a server
// the ctl supervises: `sqlite` is a file under <data_dir>/state (nothing to
// start), and `external` is a database in a server somebody else runs — the
// same rule storeLeg applies to a shared qdrant, said for the one store whose
// "no server at all" case is the common one.
func postgresLeg(t *registry.Tenant, unit string) component {
	c := component{Name: "postgres", Unit: unit, Port: t.Ports.PG}
	switch t.Stores.Postgres.Kind {
	case registry.PostgresKindLocal:
		if !t.Stores.Postgres.Capabilities.Stop {
			c.Why = "capabilities.stop is false for the postgres store, so the ctl never touches it (confirm ownership with `adopt` first)"
			return c
		}
		if port := int(t.Stores.Postgres.Port); port != 0 {
			c.Port = port
		}
		c.Managed = true
	case registry.PostgresKindExternal:
		c.Why = "the relational store is a database in a server somebody else runs (kind: external); the ctl does not supervise it"
	default:
		c.Why = "the relational store is SQLite under <data_dir>/state; there is no server to start or stop"
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
	// LockRegistry/LockManifest because the last step records the new state:
	// the registry write is part of the operation, not a side effect of it.
	p.need(model.LockTenant, model.LockRegistry, model.LockManifest)
	if err := p.requireSystemd("start"); err != nil {
		return err
	}
	only := argStringsOf(args, "only")
	legs, err := p.legs(only)
	if err != nil {
		return err
	}
	p.addFor("systemd", step{
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
	if len(only) > 0 {
		p.warn("this is a partial start (--only): the tenant's `state` and `desired_boot` are left as they are, " +
			"because starting one leg says nothing about the whole tenant")
		return nil
	}
	p.result["state"] = "active"
	p.result["desired_boot"] = "enabled"
	p.addRegistryEffect("start", fmt.Sprintf("record %s as active (desired_boot enabled) in the registry", p.tenant),
		func(t *registry.Tenant) { t.State, t.DesiredBoot = "active", "enabled" })
	return nil
}

// ---------------------------------------------------------------- stop

func planStop(_ context.Context, p *planner, args map[string]any) error {
	p.need(model.LockTenant, model.LockRegistry, model.LockManifest)
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
	p.result["state"] = "stopped"
	// A PARTIAL stop (`--only es`) leaves the tenant running, so it is not a
	// tenant that is `stopped`; recording it as such would tell the next boot
	// and every dashboard something false about the API that is still serving.
	// The registry write is for the whole-tenant stop only.
	if len(only) > 0 {
		delete(p.result, "state")
		p.warn("this is a partial stop (--only): the tenant's `state` is left as it is, because part of it is still running")
		if !keepEnabled {
			p.warn("desired_boot is left as it is too: a partial stop cannot say whether the TENANT should come back at boot")
			delete(p.result, "desired_boot")
		}
		return nil
	}
	p.addRegistryEffect("stop", fmt.Sprintf("record %s as stopped in the registry", p.t.Name), func(t *registry.Tenant) {
		t.State = "stopped"
		if !keepEnabled {
			t.DesiredBoot = "disabled"
		}
	})
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
	p.addFor("proc", step{
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
	p.addFor("proc", step{
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
	p.need(model.LockTenant, model.LockRegistry, model.LockManifest)
	if err := p.requireSystemd("restart"); err != nil {
		return err
	}
	only := argStringsOf(args, "only")
	legs, err := p.legs(only)
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
	if len(only) > 0 {
		p.warn("this is a partial restart (--only): the tenant's `state` is left as it is")
		return nil
	}
	p.result["state"] = "active"
	p.addRegistryEffect("restart", fmt.Sprintf("record %s as active in the registry", p.tenant),
		func(t *registry.Tenant) { t.State = "active" })
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
	p.addFor("systemd", step{
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

// ---------------------------------------------------------------- registry effects
//
// `start`, `stop` and `restart` change what the fleet IS, not only what is
// running: a stopped tenant whose registry still says `active` is a row the
// dashboard, the doctor and the next boot all read as a lie. So each of the
// three ends with ONE registry write — state, desired_boot when the verb moved
// it, and last_ops[verb] — saved after the unit steps succeeded.
//
// One write, not one per leg: registry.Save bumps the generation, and a job
// that touched four units would otherwise advance the fleet's generation four
// times for a single operator action, which makes "what changed at generation
// 91?" unanswerable.

// addRegistryEffect is that step. apply mutates the tenant row of the fleet
// the ENGINE loaded under the locks (sc.Ops.Fleet) — never the plan-time
// snapshot, which the plan hash has already proved equal to it.
func (p *planner) addRegistryEffect(verb, title string, apply func(t *registry.Tenant)) {
	name := p.tenant
	p.add(step{
		Kind: "registry", Title: title, Targets: []string{name},
		WouldWrite: []model.WouldWrite{{Path: p.oc.Roots.Registry(), Mode: "0660", Preview: model.NullString("")}},
		Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			save := p.op.deps.SaveFleet
			if save == nil {
				return "", fmt.Errorf("%w: the registry writer is not wired", jobs.ErrRefused)
			}
			f := sc.Ops.Fleet
			if f == nil {
				return "", fmt.Errorf("%w: the engine loaded no registry under the locks", jobs.ErrRefused)
			}
			t, ok := f.Tenants[name]
			if !ok {
				return "", fmt.Errorf("%w: %s is no longer in the registry", jobs.ErrRefused, name)
			}
			apply(t)
			if t.LastOps == nil {
				t.LastOps = map[string]registry.OpRecord{}
			}
			// The op record is the CONTRACT's three fields (job_id, at,
			// outcome); the principal is on the job and in the audit row, which
			// is where an "who did this" question is answered without the
			// registry growing a second copy of it.
			t.LastOps[verb] = registry.OpRecord{JobID: sc.Job.ID, At: p.stampRFC3339(sc), Outcome: "succeeded"}
			if err := save(f); err != nil {
				return "", err
			}
			sc.Logf("registry generation %d: %s state %s, desired_boot %s", f.Generation, name, t.State, t.DesiredBoot)
			return fmt.Sprintf("%s state %s (generation %d)", name, t.State, f.Generation), nil
		},
	})
}

// stampRFC3339 is the run-time clock in the format the registry records.
func (p *planner) stampRFC3339(sc *jobs.StepContext) string {
	if sc != nil && sc.Ops.Now != nil {
		return sc.Ops.Now().UTC().Format(time.RFC3339)
	}
	return p.op.deps.now().UTC().Format(time.RFC3339)
}

// addReadyStep plans the readiness gate: the tenant's own stores, then the API
// port, have to be answering before the job calls itself done — because
// "systemctl start returned" is not "the tenant answers".
func (p *planner) addReadyStep(legs []component) {
	port := 0
	for _, c := range legs {
		if c.Name == "api" && c.Managed {
			port = c.Port
		}
		// The postgres leg is a TCP probe rather than pg_isready: the api unit
		// runs `wait-ready` before it starts and the socket auth that pg_isready
		// would use is INSIDE the container, so "the port accepts a connection"
		// is the strongest thing an outside observer can honestly assert here.
		if c.Name == "postgres" && c.Managed {
			pg := c
			p.addFor("proc", step{
				Kind: "probe", Title: fmt.Sprintf("wait for postgres to listen on %d", pg.Port),
				Targets: []string{strconv.Itoa(pg.Port)},
				Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
					return awaitListening(ctx, sc, pg.Port, "the tenant's postgres")
				},
			})
		}
	}
	if port == 0 {
		return
	}
	p.addFor("proc", step{
		Kind: "probe", Title: fmt.Sprintf("wait for the API to listen on %d", port),
		Targets: []string{strconv.Itoa(port)},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return awaitListening(ctx, sc, port, "the API")
		},
	})
}

// awaitListening polls the LISTEN table for port until it answers or the
// readiness timeout passes. It polls — a single probe right after
// `systemctl start` returned is a probe of a process that has not finished
// importing yet, and on coconut the fenced backup's API restart failed that
// way while the API came up two seconds later. Wall clock and a real sleep,
// for the reason create's readiness gate gives: this is a run half waiting
// for a process, not a plan.
func awaitListening(ctx context.Context, sc *jobs.StepContext, port int, what string) (string, error) {
	deadline := time.Now().Add(createReadyTimeout)
	for {
		listening, err := sc.Ops.Drivers.Proc().Listening(ctx, port)
		if err != nil {
			return "", err
		}
		if listening {
			return fmt.Sprintf("port %d is listening", port), nil
		}
		if time.Now().After(deadline) {
			return "", fmt.Errorf("nothing is listening on %d after %s: %s did not come up", port, createReadyTimeout, what)
		}
		select {
		case <-ctx.Done():
			return "", ctx.Err()
		case <-time.After(createReadyPoll):
		}
	}
}
