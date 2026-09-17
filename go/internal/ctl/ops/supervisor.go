package ops

// HOW a tenant's legs are started, stopped and found — the one thing the
// lifecycle verbs used to know and now ask.
//
// There are two answers on this host (plan PR-D2, "Supervisor seam"):
//
//   - `systemd`: the units the ctl renders, driven with `systemctl --user`.
//     This is the code that was inline in lifecycle.go, create.go and
//     decommission.go, moved behind the interface and not otherwise changed —
//     the plan and run tests of every systemd verb assert byte-for-byte what
//     they asserted before.
//   - `instance`: the ctl supervises the tenant ITSELF. The stores run as
//     named apptainer instances (`qdrant-<manifest>`, the names the
//     hand-started tenants already use) and the API is a detached uvicorn with
//     a pidfile. It exists because svcbvbrc on coconut has no linger, no user
//     manager and no logind session under cron, so `systemctl --user` cannot
//     be driven from boot at all.
//
// `manual` is the third value of the registry enum and has NO supervisor here
// on purpose: it describes a tenant somebody else started, which the ctl may
// stop through its pidfile (`stop --force`) and may not start. Every verb
// refuses it by name, pointing at `handover`.
//
// Two rules the instance half keeps that the unit half got for free:
//
//   - The COMMAND is render's, not this file's. `render.StoreArgv` and
//     `render.APIArgv` render what the unit's ExecStart renders, so a bind
//     added to one is added to both; a supervisor that built its own argv
//     would start a store that cannot see its own data, and the failure would
//     appear at the tenant's first query rather than at the start.
//   - No secret is ever in a plan, a step target, a log or a checkpoint. The
//     postgres password and the tenant's whole environment are read from
//     secrets.env at RUN time and reach the child through its environment
//     only. A plan renders the argv; the argv has no credential in it.

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/envfile"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/render"
)

// Supervisor values (registry.json tenant.supervisor).
const (
	supervisorSystemd  = "systemd"
	supervisorManual   = "manual"
	supervisorInstance = "instance"
)

// DefaultSupervisor is what a `create` that names none gets when the
// deployment names none either: the contract's default
// (create_request.json), so a host that says nothing behaves as the schema
// promises.
const DefaultSupervisor = supervisorSystemd

// supervisor plans one leg's start, stop, enable and disable, the supervision
// artefacts a tenant needs, and the question "is this leg running".
//
// Every method that PLANS takes the planner and adds steps to it; `running` is
// the only one that runs, and it runs at RECONCILE and post-check time, where
// there is no plan to add to.
type supervisor interface {
	// kind is the registry value this supervisor implements.
	kind() string
	// preStart plans what has to happen before any leg of this tenant starts
	// (systemd: one daemon-reload; instance: nothing).
	preStart(p *planner) error
	startLeg(p *planner, c component) error
	stopLeg(p *planner, c component) error
	enableLeg(p *planner, c component) error
	disableLeg(p *planner, c component) error
	// startTenant starts the tenant as ONE act, which is what `create` does:
	// systemd starts the target and lets its Wants pull the services in;
	// instance mode has no target and starts the legs in order.
	startTenant(p *planner, target string) error
	// install writes and registers the supervision artefacts a fresh tenant
	// needs. For instance mode there are none — that is the point of it — and
	// the plan says so with a skipped step rather than silently doing nothing.
	install(p *planner, units map[string][]byte, target string) error
	// remove undoes install.
	remove(p *planner) error
	// running reports whether this leg is up, for reconcile, for an idempotent
	// start and for the selftest's post-checks.
	running(ctx context.Context, sc *jobs.StepContext, c component) (bool, error)
}

// supervisorFor returns the supervisor for a registry value. `manual` (and
// anything unknown) has none: the verbs refuse it by name before they would
// reach one.
func supervisorFor(kind string) supervisor {
	switch kind {
	case supervisorSystemd:
		return systemdSupervisor{}
	case supervisorInstance:
		return instanceSupervisor{}
	default:
		return nil
	}
}

// managedSupervisor reports whether kind is one the ctl can start.
func managedSupervisor(kind string) bool { return supervisorFor(kind) != nil }

// requireSupervised is the one refusal every lifecycle op shares: a
// hand-started tenant has no supervision the ctl owns, so there is nothing to
// start, restart or decommission until `handover` has given it some.
func (p *planner) requireSupervised(verb string) error {
	if p.sup != nil {
		return nil
	}
	return p.refuse("%s is a hand-started tenant (supervisor: %s); `%s` needs a supervisor the ctl owns — hand it "+
		"over first (`ragstack-ctl tenant handover %s`)", p.t.Name, p.t.Supervisor, verb, p.t.Name)
}

// ---------------------------------------------------------------- systemd

// systemdSupervisor is the unit path: `systemctl --user` over the files the
// ctl renders into its own config tree and links into the manager.
type systemdSupervisor struct{}

var _ supervisor = systemdSupervisor{}

func (systemdSupervisor) kind() string { return supervisorSystemd }

func (systemdSupervisor) preStart(p *planner) error {
	p.addFor("systemd", step{
		Kind: "systemd", Title: "systemctl --user daemon-reload",
		WouldRun: []model.WouldRun{{Argv: []string{"/usr/bin/systemctl", "--user", "daemon-reload"}}},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return "daemon-reload", sc.Ops.Drivers.Systemd().DaemonReload(ctx)
		},
	})
	return nil
}

func (systemdSupervisor) startLeg(p *planner, c component) error {
	p.addUnitStep("start", c)
	return nil
}
func (systemdSupervisor) stopLeg(p *planner, c component) error { p.addUnitStep("stop", c); return nil }
func (systemdSupervisor) enableLeg(p *planner, c component) error {
	p.addUnitStep("enable", c)
	return nil
}
func (systemdSupervisor) disableLeg(p *planner, c component) error {
	p.addUnitStep("disable", c)
	return nil
}

func (systemdSupervisor) startTenant(p *planner, target string) error {
	p.addFor("systemd", step{
		Kind: "systemd", Title: "start " + target, Targets: []string{target},
		WouldRun: []model.WouldRun{{Argv: []string{"/usr/bin/systemctl", "--user", "start", target}}},
		Run:      unitRun("start", target),
		Rollback: unitRun("stop", target),
		Reconcile: func(ctx context.Context, sc *jobs.StepContext) (jobs.Reconciliation, error) {
			active, err := sc.Ops.Drivers.Systemd().IsActive(ctx, target)
			if err != nil {
				return jobs.ReconcileStuck, err
			}
			if active {
				return jobs.ReconcileDone, nil
			}
			return jobs.ReconcileRedo, nil
		},
	})
	return nil
}

func (systemdSupervisor) install(p *planner, units map[string][]byte, target string) error {
	p.addUnitsWrite(units)
	for _, unit := range sortedUnitNames(units) {
		unit := unit
		path := filepath.Join(p.oc.Roots.UnitsDir(), unit)
		p.addFor("systemd", step{
			Kind: "systemd", Title: "systemctl --user link " + unit, Targets: []string{path},
			WouldRun: []model.WouldRun{{Argv: []string{"/usr/bin/systemctl", "--user", "link", path}}},
			Warnings: []string{"the ctl writes units into its own config tree, which the user manager does not " +
				"search; linking is what makes a rendered unit a real one until the SYSTEMD_UNIT_PATH drop-in exists"},
			Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
				if err := sc.Checkpoint("unit:" + unit); err != nil {
					return "", err
				}
				return "linked " + unit, sc.Ops.Drivers.Systemd().Link(ctx, path)
			},
			// The link is undone by `disable`, which is what removes the symlink
			// `link` made (the unit was never enabled, so that is all it
			// removes). Without this rollback coconut's first failed sandbox
			// create left three dangling links in the user manager, listed as
			// loaded/failed units of a tenant that no longer existed. The
			// failed state is cleared too, so a later create of the same name
			// does not inherit a start-limit counter; "not loaded" there is not
			// an error, it is the state this rollback wants.
			Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
				// A refusal here means the manager already forgot the unit — the
				// enable step's own rollback (disable, in reverse order before
				// this one) does that for the target — and "already gone" is
				// this rollback's goal, not its failure.
				if err := sc.Ops.Drivers.Systemd().Disable(ctx, unit); err != nil {
					sc.Logf("disable %s: %v (treated as already unlinked)", unit, err)
					return "already unlinked " + unit, nil
				}
				_ = sc.Ops.Drivers.Systemd().ResetFailed(ctx, unit)
				return "unlinked " + unit, nil
			},
		})
	}
	if err := (systemdSupervisor{}).preStart(p); err != nil {
		return err
	}
	if p.t.DesiredBoot == "enabled" {
		// The TARGET is what is enabled, and only the target: the service units
		// carry no [Install] section (they are PartOf the target and pulled in by
		// its Wants), so `systemctl enable` on one of them is an error, not a
		// stronger guarantee.
		p.addFor("systemd", step{
			Kind: "systemd", Title: "enable " + target + " (desired_boot)", Targets: []string{target},
			WouldRun: []model.WouldRun{{Argv: []string{"/usr/bin/systemctl", "--user", "enable", target}}},
			Run:      unitRun("enable", target),
			Rollback: unitRun("disable", target),
		})
	}
	return nil
}

func (systemdSupervisor) remove(p *planner) error { p.addUnitFileRemoval(); return nil }

func (systemdSupervisor) running(ctx context.Context, sc *jobs.StepContext, c component) (bool, error) {
	return sc.Ops.Drivers.Systemd().IsActive(ctx, c.Unit)
}

// ---------------------------------------------------------------- instance

// instanceSupervisor is the ctl supervising the tenant itself: named apptainer
// instances for the stores, a detached uvicorn with a pidfile for the API.
type instanceSupervisor struct{}

var _ supervisor = instanceSupervisor{}

func (instanceSupervisor) kind() string { return supervisorInstance }

// preStart has nothing to do: there is no manager to reload.
func (instanceSupervisor) preStart(*planner) error { return nil }

func (s instanceSupervisor) startTenant(p *planner, _ string) error {
	legs, err := p.legs(nil)
	if err != nil {
		return err
	}
	for _, c := range legs {
		if !c.Managed {
			p.skip("instance", "skip "+c.Name, c.Why, c.Name)
			continue
		}
		if err := s.startLeg(p, c); err != nil {
			return err
		}
	}
	return nil
}

// install writes nothing. That is the whole difference between the two
// supervisors on a fresh tenant, and the plan states it as a step rather than
// leaving a gap where four unit writes used to be.
func (instanceSupervisor) install(p *planner, _ map[string][]byte, _ string) error {
	p.skip("instance", "skip the unit files",
		"supervisor is `instance`: the ctl starts this tenant itself (apptainer instances plus a detached uvicorn "+
			"with a pidfile), so there are no unit files to write and nothing to link into a user manager",
		p.tenant)
	return nil
}

func (instanceSupervisor) remove(p *planner) error {
	p.skip("instance", "skip removing the unit files",
		"supervisor is `instance`: this tenant never had any", p.tenant)
	return nil
}

// enableLeg and disableLeg record the INTENT and change nothing on the host.
//
// `desired_boot` is the registry's field and the registry step is what writes
// it; in instance mode "enabled" means "`ragstack-ctl fleet start --all`
// starts this tenant", and that is read out of the row at boot rather than out
// of a link in a manager's wants directory. The steps exist so a plan reads
// the same in both supervisors — an operator comparing `stop --dry-run` on two
// tenants should see the same shape, with this one saying where the state
// actually lives.
func (instanceSupervisor) enableLeg(p *planner, c component) error {
	return instanceBootIntent(p, c, "enabled")
}

func (instanceSupervisor) disableLeg(p *planner, c component) error {
	return instanceBootIntent(p, c, "disabled")
}

func instanceBootIntent(p *planner, c component, want string) error {
	verb := "enable"
	if want == "disabled" {
		verb = "disable"
	}
	p.addFor("instance", step{
		Kind: "instance", Title: verb + " " + c.Name + " (desired_boot)", Targets: []string{c.Name},
		Warnings: []string{"instance mode has no boot links: `desired_boot` is the registry row, and `" + want +
			"` means `ragstack-ctl fleet start --all` " + map[string]string{"enabled": "starts", "disabled": "skips"}[want] +
			" this tenant. The registry step of this plan is what writes it"},
		Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			sc.Logf("desired_boot %s is recorded in the registry; there is no unit to %s", want, verb)
			return "desired_boot " + want + " (registry)", nil
		},
	})
	return nil
}

// startLeg is `apptainer instance run` for a store and a detached spawn for
// the api.
func (s instanceSupervisor) startLeg(p *planner, c component) error {
	switch c.Name {
	case "api":
		return s.startAPI(p, c)
	case "ui":
		return p.refuse("%s's UI is in `dev` mode and this tenant is supervised by `instance`: a Vite dev server is "+
			"not supervised in instance mode. Build the UI (`ui_mode: static`, which nginx serves from "+
			"<data_dir>/ui/dist) or put the tenant on systemd units", p.t.Name)
	default:
		return s.startStore(p, c)
	}
}

func (s instanceSupervisor) stopLeg(p *planner, c component) error {
	switch c.Name {
	case "api":
		return s.stopAPI(p, c)
	case "ui":
		return p.refuse("%s's UI is in `dev` mode and this tenant is supervised by `instance`: the ctl never started "+
			"that Vite server, so it will not stop it", p.t.Name)
	default:
		return s.stopStore(p, c)
	}
}

func (instanceSupervisor) running(ctx context.Context, sc *jobs.StepContext, c component) (bool, error) {
	if c.Name == "api" {
		return apiRunning(ctx, sc, c)
	}
	return instanceRunning(ctx, sc, c.Instance, jobs.NamespaceCtl)
}

// ---------------------------------------------------------------- stores

// startStore plans one `apptainer instance run`.
//
// The argv is rendered HERE, at plan time, by the same function the unit
// renderer calls — so `--dry-run` prints the command that will actually run,
// and a plan that cannot be rendered is a refusal before anything is touched.
func (instanceSupervisor) startStore(p *planner, c component) error {
	st, err := render.StoreArgv(p.t, c.Leg, p.unitConfig())
	if err != nil {
		return p.refuse("%s's %s store cannot be started as it is recorded: %v", p.t.Name, c.Name, err)
	}
	name, sif := st.Instance, st.SIF
	seedFrom, seedInto := "", ""
	if c.Leg == render.LegES {
		// The config bind SHADOWS the image's own config directory, so the
		// host directory has to hold something before ES starts. The unit does
		// it with `ExecStartPre=… es-seed-config`; this supervisor has no
		// ExecStartPre, so it owes the same step itself.
		seedFrom, seedInto = esImageConfigDir, p.tpaths.ESConfig
	}
	needsPassword := c.Leg == render.LegPostgres
	secretsEnv := p.tpaths.SecretsEnv

	warnings := []string{"the instance name is the one the hand-started tenants already use, so a tenant the ctl " +
		"adopts is supervised through the instances that are already there rather than beside them"}
	if needsPassword {
		warnings = append(warnings, "the postgres password is read from secrets.env at RUN time and reaches the "+
			"container through APPTAINERENV_POSTGRES_PASSWORD in apptainer's own environment: it is not on this "+
			"command line, which is world-readable as /proc/<pid>/cmdline")
	}
	if seedInto != "" {
		warnings = append(warnings, "the config bind "+seedInto+" is seeded from the image first when it is empty; "+
			"an Elasticsearch whose config directory is empty exits before it logs why")
	}

	p.addFor("instance", step{
		Kind: "instance", Title: "start the instance " + name, Targets: []string{name},
		WouldRun: []model.WouldRun{{Argv: append([]string{"/usr/bin/apptainer", "instance", "run", "--no-home"},
			append(append(st.BindArgs(), st.EnvArgs()...), append([]string{sif, name}, st.Args...)...)...)}},
		Warnings: warnings,
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			up, err := instanceRunning(ctx, sc, name, jobs.NamespaceCtl)
			if err != nil {
				return "", err
			}
			if up {
				// Idempotent by design: `fleet start --all` runs over a fleet
				// half of which is already up, and a periodic run of it is the
				// only watchdog instance mode has.
				sc.Logf("instance %s is already running", name)
				return "already running: " + name, nil
			}
			if seedInto != "" {
				seeded, err := seedESConfig(ctx, sc, sif, seedFrom, seedInto)
				if err != nil {
					return "", err
				}
				sc.Logf("%s", seeded)
			}
			spec := jobs.InstanceSpec{
				Name: name, SIF: sif, Binds: st.Binds, Env: st.Env, Args: st.Args,
				ExtraEnv: map[string]string{},
			}
			for k, v := range st.ProcessEnv {
				spec.ExtraEnv[k] = v
			}
			if needsPassword {
				// The VALUE, read now and held only for the length of this
				// call. It is never in the plan, never in a checkpoint, never
				// in a log, and never on the argv.
				pw, err := postgresPassword(ctx, sc, secretsEnv)
				if err != nil {
					return "", err
				}
				spec.ExtraEnv[render.APPTAINERENVPostgresPassword] = pw
			}
			// The instance NAME is the external ID, recorded before the call
			// that creates it: a crash between the two leaves a record
			// reconcile can act on.
			//
			// …and, beside it, HOW LONG the instance's stderr log already is.
			// apptainer APPENDS to that file for the life of the host, so it
			// holds every previous run of this instance — including the ones
			// that failed. A later step that quoted its tail would quote a line
			// from a run that is not this one, which is worse than quoting
			// nothing: it would report yesterday's "wrong ownership" about a
			// postgres that died of something else today. The offset is what
			// makes the quote honest (instanceGoneReason).
			if err := sc.Checkpoint("instance:"+name, errLogMark(ctx, sc, name)); err != nil {
				return "", err
			}
			if err := sc.Ops.Drivers.Instances().Run(ctx, spec); err != nil {
				return "", err
			}
			return "started " + name, nil
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return "stopped " + name, sc.Ops.Drivers.Instances().Stop(ctx, name,
				jobs.StopOptions{Namespace: jobs.NamespaceCtl})
		},
		Reconcile: func(ctx context.Context, sc *jobs.StepContext) (jobs.Reconciliation, error) {
			up, err := instanceRunning(ctx, sc, name, jobs.NamespaceCtl)
			if err != nil {
				return jobs.ReconcileStuck, err
			}
			if up {
				return jobs.ReconcileDone, nil
			}
			return jobs.ReconcileRedo, nil
		},
	})
	return nil
}

func (instanceSupervisor) stopStore(p *planner, c component) error {
	name := c.Instance
	if name == "" {
		return p.refuse("%s's %s leg has no instance name, so the ctl does not know what to stop", p.t.Name, c.Name)
	}
	p.addFor("instance", step{
		Kind: "instance", Title: "stop the instance " + name, Destructive: true, Targets: []string{name},
		WouldRun: []model.WouldRun{{Argv: []string{"/usr/bin/apptainer", "instance", "stop", name}}},
		Warnings: []string{"SIGTERM to the instance, which is the graceful shutdown elasticsearch needs; an " +
			"instance that is not running is success, so this step is safe to re-run"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			if err := sc.Checkpoint("instance:" + name); err != nil {
				return "", err
			}
			if err := sc.Ops.Drivers.Instances().Stop(ctx, name,
				jobs.StopOptions{Namespace: jobs.NamespaceCtl}); err != nil {
				return "", err
			}
			return "stopped " + name, nil
		},
		Reconcile: func(ctx context.Context, sc *jobs.StepContext) (jobs.Reconciliation, error) {
			up, err := instanceRunning(ctx, sc, name, jobs.NamespaceCtl)
			if err != nil {
				return jobs.ReconcileStuck, err
			}
			if up {
				return jobs.ReconcileRedo, nil
			}
			return jobs.ReconcileDone, nil
		},
	})
	return nil
}

// esImageConfigDir is where the elasticsearch image keeps the configuration
// the tenant's bind shadows.
const esImageConfigDir = "/usr/share/elasticsearch/config"

// seedESConfig is `ragstack-ctl es-seed-config` as a step half: seed the
// config bind from the image when, and only when, the host directory holds
// nothing.
//
// The emptiness test is the whole policy. An operator's own elasticsearch.yml
// is never overwritten, and a directory that cannot be listed is fatal here
// rather than at the start — apptainer refuses a bind whose source is missing,
// and saying so before the attempt is the point of the check.
func seedESConfig(ctx context.Context, sc *jobs.StepContext, sif, containerDir, hostDir string) (string, error) {
	entries, err := sc.Ops.Drivers.Files().ReadDir(ctx, hostDir)
	switch {
	case errors.Is(err, fs.ErrNotExist):
		return "", fmt.Errorf("%w: %s does not exist, and apptainer refuses a bind whose source is missing",
			jobs.ErrRefused, hostDir)
	case err != nil:
		return "", fmt.Errorf("listing %s: %w", hostDir, err)
	case len(entries) > 0:
		return hostDir + " is already populated — not seeding", nil
	}
	if err := sc.Ops.Drivers.Instances().SeedConfigDir(ctx, sif, containerDir, hostDir); err != nil {
		return "", err
	}
	return "seeded " + hostDir + " from " + sif + ":" + containerDir, nil
}

// instanceRunning asks ONE instance table, by name.
//
// The namespace is a parameter rather than a default because the answer "it is
// not running" is only ever true OF A REGISTRY: the ctl's own for everything
// the ctl started, the releasing account's default for a tenant somebody
// started by hand (jobs.InstanceNamespace).
func instanceRunning(ctx context.Context, sc *jobs.StepContext, name string,
	ns jobs.InstanceNamespace) (bool, error) {
	_, up, err := instanceIn(ctx, sc, name, ns)
	return up, err
}

// instanceIn is instanceRunning with the ROW: the pid a caller needs to prove
// identity against the port.
func instanceIn(ctx context.Context, sc *jobs.StepContext, name string,
	ns jobs.InstanceNamespace) (jobs.Instance, bool, error) {
	list, err := sc.Ops.Drivers.Instances().List(ctx, jobs.ListOptions{Namespace: ns})
	if err != nil {
		return jobs.Instance{}, false, err
	}
	for _, in := range list {
		if in.Name == name {
			return in, true, nil
		}
	}
	return jobs.Instance{}, false, nil
}

// postgresPassword reads the value out of the tenant's secrets.env.
//
// TWO names hold it (ops/create.go writes both): TENANT_PG_PASSWORD is what
// the registry's secret ref points at, and APPTAINERENV_POSTGRES_PASSWORD is
// the name apptainer forwards into the container as POSTGRES_PASSWORD. The
// second is preferred because it is the one the unit path uses, and the first
// is the fallback for a tenant whose secrets.env predates the pair.
//
// Nothing here is logged. The error names the FILE and the KEY, never a value.
func postgresPassword(ctx context.Context, sc *jobs.StepContext, secretsEnv string) (string, error) {
	vals, err := readEnvFile(ctx, sc, secretsEnv)
	if err != nil {
		return "", fmt.Errorf("reading %s for the postgres password: %w", secretsEnv, err)
	}
	for _, key := range []string{render.APPTAINERENVPostgresPassword, "TENANT_PG_PASSWORD"} {
		if v := vals[key]; v != "" {
			return v, nil
		}
	}
	return "", fmt.Errorf("%w: %s holds neither %s nor TENANT_PG_PASSWORD, so the postgres instance would come up "+
		"with no role password", jobs.ErrRefused, secretsEnv, render.APPTAINERENVPostgresPassword)
}

// readEnvFile parses one env file through the drivers. An ABSENT file is an
// empty map and no error — the api unit loads secrets.env with a leading `-`
// for exactly that reason.
func readEnvFile(ctx context.Context, sc *jobs.StepContext, path string) (map[string]string, error) {
	b, err := sc.Ops.Drivers.Files().ReadFile(ctx, path)
	if errors.Is(err, fs.ErrNotExist) {
		return map[string]string{}, nil
	}
	if err != nil {
		return nil, err
	}
	// Lenient: the grammar problems are doctor's to report
	// (env_not_systemd_parsable) and a tenant whose env file has an inline
	// comment must still be startable by the op that fixes it.
	f, _, err := envfile.ParseLenient(b)
	if err != nil {
		return nil, fmt.Errorf("parsing %s: %w", path, err)
	}
	out := map[string]string{}
	for _, a := range f.Assignments() {
		out[a.Key] = a.Value
	}
	return out, nil
}

// ---------------------------------------------------------------- api

// apiLaunch is everything a spawn of this tenant's API needs, resolved from
// the ROW at plan time: the argv render.APIArgv produces, the two env files
// the child's environment is read out of, the pidfile, the log, and the store
// probes the spawn waits on.
//
// It exists so that there is exactly ONE launch. `supervisor: instance` starts
// the API with it, and so does the handover release's ROLLBACK — the rollback
// that used to do nothing but print `restore.sh`, which is how the first real
// release left the hackathon tenant's API down for ten minutes while the job
// it belonged to reported a rollback. A rollback that restarts what the job
// stopped has to start the SAME process the row describes, and the only way to
// keep that true under later edits is for both callers to read one struct.
//
// The account is whoever runs the job: the release runs as the tenant's owner,
// which is the account whose uvicorn it stopped.
type apiLaunch struct {
	Program string
	Args    []string
	// UnitEnv is the api unit's `Environment=` lines — the third and lowest
	// layer of the child's environment, under tenant.env and secrets.env.
	UnitEnv []string
	// Dir is the working directory: <worktree>/python, the same one the unit's
	// WorkingDirectory names and the one stopAPIProcess's identity check
	// compares /proc/<pid>/cwd against.
	Dir      string
	Worktree string
	PidFile  string
	LogPath  string
	// TenantEnv and SecretsEnv are PATHS. No value read out of either ever
	// leaves apiEnviron.
	TenantEnv    string
	SecretsEnv   string
	Port         int
	OwnStores    []storeProbe
	SharedStores []storeProbe
}

// apiLaunch resolves the launch from the row. It is a plan-time call: it
// renders an argv and reads nothing off the host.
func (p *planner) apiLaunch(port int) (apiLaunch, error) {
	program, args, unitEnv, err := render.APIArgv(p.t, p.unitConfig())
	if err != nil {
		return apiLaunch{}, err
	}
	tp := p.tpaths
	return apiLaunch{
		Program: program, Args: args, UnitEnv: unitEnv,
		Dir: filepath.Join(p.t.Worktree, "python"), Worktree: p.t.Worktree,
		PidFile: p.apiPidFile(), LogPath: tp.APILog,
		TenantEnv: tp.TenantEnv, SecretsEnv: tp.SecretsEnv, Port: port,
		// The supervisor this plan is being made AGAINST, not the one the row
		// happens to say: a take plans every leg against instanceSupervisor
		// before the registry step has written it (handover.go), and the
		// readiness wait of that very job is the one that needs the fast-fail.
		OwnStores:    ownStoreProbes(p.t, tp, p.sup != nil && p.sup.kind() == supervisorInstance),
		SharedStores: sharedStoreProbes(p.t, tp),
	}, nil
}

// startAPIProcess is the whole start: the already-running check, the store
// waits, the environment read at RUN time, and the pidfile checkpointed before
// the spawn that writes it.
func startAPIProcess(ctx context.Context, sc *jobs.StepContext, l apiLaunch) (string, error) {
	up, pid, err := apiRunningPID(ctx, sc, l.PidFile, l.Port)
	if err != nil {
		return "", err
	}
	if up {
		sc.Logf("the API is already running as pid %d", pid)
		return fmt.Sprintf("already running: pid %d", pid), nil
	}
	// The stores first, exactly as the api unit's ExecStartPre `wait-ready`
	// does: uvicorn's startup creates the qdrant collection and the
	// elasticsearch index, and an API spawned while Elasticsearch is still
	// opening its segments dies on a connection timeout — coconut's first
	// instance-mode selftest did, fifteen seconds after the spawn.
	if err := awaitStores(ctx, sc, l.OwnStores, createReadyTimeout); err != nil {
		return "", err
	}
	// Then the stores this tenant does NOT own. The ctl did not start them and
	// never will — they belong to the other account — but it has to WAIT for
	// them, because at boot the two accounts run independently: wilke's
	// restore.sh brings the shared qdrant and elasticsearch up, svcbvbrc's
	// @reboot crontab line runs `fleet start --all`, and nothing orders the
	// two. Without this wait the API of a shared-store tenant is spawned
	// first, cannot reach a store that is half a minute away, and dies — the
	// reboot in which three tenants came back and one did not.
	if err := awaitStores(ctx, sc, l.SharedStores, sharedStoreWait); err != nil {
		return "", fmt.Errorf("%w (the ctl does not start this store: it belongs to another account, and "+
			"this wait is all it can do)", err)
	}
	env, err := apiEnviron(ctx, sc, l.TenantEnv, l.SecretsEnv, l.UnitEnv)
	if err != nil {
		return "", err
	}
	// The PIDFILE is the durable record — it is what `running`, the stop and
	// every later reconcile read — so its PATH is checkpointed before the
	// spawn that writes it. The pid follows once there is one.
	if err := sc.Checkpoint("pidfile:" + l.PidFile); err != nil {
		return "", err
	}
	pid, err = sc.Ops.Drivers.Proc().Spawn(ctx, jobs.SpawnSpec{
		Program: l.Program, Args: l.Args, Dir: l.Dir,
		Env: env, LogPath: l.LogPath, PidFile: l.PidFile,
	})
	if err != nil {
		return "", err
	}
	if err := sc.Checkpoint("pid:" + strconv.Itoa(pid)); err != nil {
		return "", err
	}
	return fmt.Sprintf("started pid %d (log %s)", pid, l.LogPath), nil
}

// startAPI plans the detached uvicorn.
func (instanceSupervisor) startAPI(p *planner, c component) error {
	l, err := p.apiLaunch(c.Port)
	if err != nil {
		return p.refuse("%s's API cannot be started as it is recorded: %v", p.t.Name, err)
	}
	pidfile, port := l.PidFile, l.Port

	p.addFor("proc", step{
		Kind: "proc", Title: fmt.Sprintf("start the API detached once its stores answer (pidfile %s)", filepath.Base(pidfile)),
		Targets:  []string{pidfile},
		WouldRun: []model.WouldRun{{Argv: append([]string{l.Program}, l.Args...)}},
		WouldWrite: []model.WouldWrite{
			{Path: pidfile, Mode: "0644", Preview: model.NullString("")},
		},
		Warnings: []string{"the child's environment is tenant.env ∪ secrets.env ∪ the api unit's `Environment=` " +
			"lines, read at RUN time — the same environment systemd would give it, and no part of it is in this plan",
			"there is no restart-on-failure in instance mode: a dead API comes back when `fleet start --all` " +
				"next runs, which is why the runbook suggests a periodic one"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return startAPIProcess(ctx, sc, l)
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return stopAPIProcess(ctx, sc, pidfile, l.Worktree, port)
		},
		Reconcile: func(ctx context.Context, sc *jobs.StepContext) (jobs.Reconciliation, error) {
			up, _, err := apiRunningPID(ctx, sc, pidfile, port)
			if err != nil {
				return jobs.ReconcileStuck, err
			}
			if up {
				return jobs.ReconcileDone, nil
			}
			return jobs.ReconcileRedo, nil
		},
	})
	return nil
}

func (instanceSupervisor) stopAPI(p *planner, c component) error {
	pidfile, worktree, port := p.apiPidFile(), p.t.Worktree, c.Port
	p.addFor("proc", step{
		Kind: "proc", Title: "stop the API through its pidfile (TERM, then KILL)", Destructive: true,
		Targets: []string{pidfile},
		Warnings: []string{"the ctl signals a pid it has verified is this tenant's (pidfile, cwd and cmdline); it " +
			"never runs pkill. TERM first, then up to " + apiStopTimeout.String() + " for the port to free, then KILL"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return stopAPIProcess(ctx, sc, pidfile, worktree, port)
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
	return nil
}

// apiStopTimeout is the api unit's TimeoutStopSec, kept the same here: a
// uvicorn that is finishing a request gets as long to do it under the ctl's
// own supervision as it gets under systemd's.
const apiStopTimeout = 60 * time.Second

// apiStopPoll is how often the port is re-checked while TERM is working.
const apiStopPoll = 500 * time.Millisecond

// stopAPIProcess is the whole stop: the pidfile, the identity check, TERM,
// proof the port is free, KILL if it is not, and the pidfile removed last.
//
// The pidfile goes LAST because it is the ctl's only record of what it
// started: removing it before the process is gone would leave a running
// uvicorn nothing can find.
func stopAPIProcess(ctx context.Context, sc *jobs.StepContext, pidfile, worktree string, port int) (string, error) {
	pid, err := readPidFile(ctx, sc, pidfile)
	if errors.Is(err, fs.ErrNotExist) {
		// No pidfile is a stop with nothing to stop. It is SUCCESS for the
		// same reason `apptainer instance stop` of an absent instance is: this
		// step is re-run by rollbacks, resumes and `fleet stop --all`.
		sc.Logf("%s is not there: nothing to stop", pidfile)
		return "no pidfile: nothing to stop", nil
	}
	if err != nil {
		return "", err
	}
	// The pid is the external ID, recorded BEFORE the signal.
	if err := sc.Checkpoint("pid:" + strconv.Itoa(pid)); err != nil {
		return "", err
	}
	proc := sc.Ops.Drivers.Proc()
	// A pidfile naming a process that no longer exists is a STALE pidfile,
	// not a tenant to stop: the API died (or was killed with the session that
	// started it — coconut's first instance-mode selftest ended that way) and
	// nothing removed the file. Signalling it would refuse "there is no
	// process", and a decommission or a `fleet stop --all` would then never
	// get past a tenant that is already down. The port check below still
	// runs: a stale pidfile with something ELSE on the port is refused there.
	if alive, err := proc.Alive(ctx, pid); err != nil {
		return "", err
	} else if !alive {
		sc.Logf("pid %d from %s is not running: a stale pidfile, nothing to signal", pid, pidfile)
		if listening, err := proc.Listening(ctx, port); err != nil {
			return "", err
		} else if listening {
			return "", fmt.Errorf("%w: pid %d from %s is gone but something else listens on %d; the ctl will not "+
				"remove a pidfile over a port it cannot account for", jobs.ErrRefused, pid, pidfile, port)
		}
		if err := sc.Ops.Drivers.Files().Remove(ctx, pidfile); err != nil {
			return "", err
		}
		return fmt.Sprintf("pid %d was already gone; stale %s removed", pid, pidfile), nil
	}
	if err := proc.Signal(ctx, pid, worktree, "uvicorn", "TERM"); err != nil {
		return "", err
	}
	killed := false
	deadline := time.Now().Add(apiStopTimeout)
	for {
		listening, err := proc.Listening(ctx, port)
		if err != nil {
			return "", err
		}
		if !listening {
			break
		}
		if time.Now().After(deadline) {
			if killed {
				return "", fmt.Errorf("%w: pid %d still holds %d after SIGKILL", jobs.ErrRefused, pid, port)
			}
			sc.Logf("pid %d still holds %d after %s of SIGTERM: sending SIGKILL", pid, port, apiStopTimeout)
			if err := proc.Signal(ctx, pid, worktree, "uvicorn", "KILL"); err != nil {
				return "", err
			}
			killed, deadline = true, time.Now().Add(apiStopTimeout)
			continue
		}
		select {
		case <-ctx.Done():
			return "", ctx.Err()
		case <-time.After(apiStopPoll):
		}
	}
	if err := sc.Ops.Drivers.Files().Remove(ctx, pidfile); err != nil {
		return "", err
	}
	how := "SIGTERM"
	if killed {
		how = "SIGTERM then SIGKILL"
	}
	return fmt.Sprintf("stopped pid %d (%s); %s removed", pid, how, pidfile), nil
}

// readPidFile reads and validates the pid. A pidfile holding something that is
// not a pid is a refusal, not a parse to shrug at: the next thing that would
// happen is a signal.
func readPidFile(ctx context.Context, sc *jobs.StepContext, pidfile string) (int, error) {
	b, err := sc.Ops.Drivers.Files().ReadFile(ctx, pidfile)
	if err != nil {
		return 0, err
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(b)))
	if err != nil || pid <= 1 {
		return 0, fmt.Errorf("%w: %s does not name a pid (%q)", jobs.ErrRefused, pidfile, strings.TrimSpace(string(b)))
	}
	return pid, nil
}

// apiRunning is the `running` answer for the api leg.
func apiRunning(ctx context.Context, sc *jobs.StepContext, c component) (bool, error) {
	pidfile := c.PidFile
	up, _, err := apiRunningPID(ctx, sc, pidfile, c.Port)
	return up, err
}

// apiRunningPID is the pidfile, the process and the identity, in that order.
//
// The pidfile alone is a file somebody may have left behind; /proc alone
// cannot tell this tenant's uvicorn from any other process that inherited the
// pid. So: the pidfile names a pid, the pid is alive, and — WHEN this account
// can read it — the process holding the API port is that same pid. An
// unreadable listener owner (pid 0, which is what `ss -ltnp` gives for another
// account's socket) is not evidence against, so it is not treated as any.
func apiRunningPID(ctx context.Context, sc *jobs.StepContext, pidfile string, port int) (bool, int, error) {
	pid, err := readPidFile(ctx, sc, pidfile)
	if errors.Is(err, fs.ErrNotExist) {
		return false, 0, nil
	}
	if errors.Is(err, jobs.ErrRefused) {
		// A pidfile with rubbish in it is "not running", loudly: the caller is
		// deciding whether to start, and refusing the start over a stale file
		// would leave a tenant down for a file nobody reads.
		sc.Logf("%v", err)
		return false, 0, nil
	}
	if err != nil {
		return false, 0, err
	}
	alive, err := sc.Ops.Drivers.Proc().Alive(ctx, pid)
	if err != nil {
		return false, 0, err
	}
	if !alive {
		sc.Logf("%s names pid %d, which is not running", pidfile, pid)
		return false, pid, nil
	}
	owner, _, err := sc.Ops.Drivers.Proc().Owner(ctx, port)
	if err != nil {
		return false, 0, err
	}
	if owner != 0 && owner != pid {
		sc.Logf("%s names pid %d but :%d is held by pid %d — that is not this tenant's API", pidfile, pid, port, owner)
		return false, pid, nil
	}
	return true, pid, nil
}

// apiEnviron is the child's WHOLE environment: tenant.env, then secrets.env,
// then the api unit's `Environment=` lines — the same three sources, in the
// same precedence, that `EnvironmentFile=`/`EnvironmentFile=-`/`Environment=`
// give the unit.
//
// It carries secrets and is returned to exactly one caller, which puts it in a
// SpawnSpec and nowhere else. Nothing logs it, previews it or checkpoints it.
func apiEnviron(ctx context.Context, sc *jobs.StepContext, tenantEnv, secretsEnv string, unitEnv []string) ([]string, error) {
	merged := map[string]string{}
	for _, path := range []string{tenantEnv, secretsEnv} {
		vals, err := readEnvFile(ctx, sc, path)
		if err != nil {
			return nil, err
		}
		for k, v := range vals {
			merged[k] = v
		}
	}
	for _, kv := range unitEnv {
		if i := strings.Index(kv, "="); i > 0 {
			merged[kv[:i]] = kv[i+1:]
		}
	}
	keys := make([]string, 0, len(merged))
	for k := range merged {
		keys = append(keys, k)
	}
	// Sorted, so the same tenant spawns with the same environment every time
	// and a `diff` of two /proc/<pid>/environ is readable.
	sort.Strings(keys)
	out := make([]string, 0, len(keys))
	for _, k := range keys {
		out = append(out, k+"="+merged[k])
	}
	sc.Logf("the child environment is %d variables from tenant.env, secrets.env and the api unit's Environment= lines",
		len(out))
	return out, nil
}

// ---------------------------------------------------------------- planner glue

// unitConfig is the render configuration every renderer in this package uses,
// built from the roots the op planned against. It is the SAME value create
// passes to render.Units, which is what makes the unit's ExecStart and the
// instance command agree.
func (p *planner) unitConfig() render.UnitConfig {
	return render.UnitConfig{
		RagRoot:     p.oc.Roots.RagRoot,
		CtlStateDir: p.oc.Roots.CtlStateDir,
		MountPoint:  p.op.deps.MountPoint,
	}
}

// apiPidFile is the registry's recorded pidfile, or the layout's.
func (p *planner) apiPidFile() string {
	if p.t != nil && p.t.API.PidFile != "" {
		return p.t.API.PidFile
	}
	return p.tpaths.PidFile
}

// instanceNameFor is render.StoreArgv's `Instance` without rendering a whole
// command: `<kind>-<manifest_name>`, the names the hand-started tenants
// already run under.
func instanceNameFor(leg, manifestName string) string {
	switch leg {
	case render.LegQdrant:
		return "qdrant-" + manifestName
	case render.LegES:
		return "elasticsearch-" + manifestName
	case render.LegPostgres:
		return "postgres-" + manifestName
	}
	return ""
}

// defaultSupervisor is the value a create that names none gets.
func (d Deps) defaultSupervisor() string {
	switch d.DefaultSupervisor {
	case supervisorSystemd, supervisorInstance:
		return d.DefaultSupervisor
	case "":
		return DefaultSupervisor
	default:
		// An unusable CTL_DEFAULT_SUPERVISOR is not a reason to refuse every
		// create: the contract's default is the documented behaviour, and the
		// value is reported by the plan warning create adds.
		return DefaultSupervisor
	}
}

// KnownSupervisor reports whether s is a value `create` may be given. It is
// exported for the CLI, which validates the flag before the request is built.
func KnownSupervisor(s string) bool { return s == supervisorSystemd || s == supervisorInstance }

// storeProbe is one readiness question about a store this tenant owns.
type storeProbe struct {
	what  string
	probe func(context.Context, *jobs.StepContext) error
	// gone, when set, answers "the thing I am waiting for is not coming": a
	// reason string and true when the process behind this probe has already
	// exited. awaitStores calls it between polls and STOPS, with that reason,
	// rather than serving out the bound.
	//
	// It exists because of what a timeout costs and what it hides. The second
	// live handover take waited the full three minutes for a postgres that had
	// died in the first second, and then reported `pg_isready … no response` —
	// a sentence that names the symptom, says nothing about the cause, and
	// arrives after the rollback has become the longest part of the outage.
	// The cause was in the instance's own .err file the whole time.
	gone func(context.Context, *jobs.StepContext) (reason string, dead bool, err error)
}

// ownStoreProbes are the stores the tenant runs itself — an exclusive qdrant,
// an exclusive elasticsearch, a local postgres — which is what `wait-ready`
// gates the api unit on. A shared or external store is somebody else's to
// keep up and is not waited for.
//
// instanceMode says whether this tenant's stores are apptainer instances the
// ctl started (`supervisor: instance`). It is what lets the postgres probe ask
// the second question a readiness wait should always have asked: not only "is
// it answering yet", but "is it still there at all".
func ownStoreProbes(t *registry.Tenant, tp paths.Tenant, instanceMode bool) []storeProbe {
	var out []storeProbe
	if q := t.Stores.Qdrant; q.Ownership == registry.OwnershipExclusive && q.URL != "" {
		url := q.URL
		out = append(out, storeProbe{"qdrant", func(c context.Context, sc *jobs.StepContext) error {
			return sc.Ops.Drivers.Qdrant().Ready(c, url)
		}, nil})
	}
	if e := t.Stores.Elasticsearch; e.Ownership == registry.OwnershipExclusive && e.URL != "" {
		url := e.URL
		out = append(out, storeProbe{"elasticsearch", func(c context.Context, sc *jobs.StepContext) error {
			return sc.Ops.Drivers.Elasticsearch().Ready(c, url)
		}, nil})
	}
	if pg := t.Stores.Postgres; pg.Kind == registry.PostgresKindLocal {
		spec := jobs.PostgresSpec{SIF: string(pg.SIF), RunDir: tp.PostgresRun, DB: t.Name, User: t.Name, Port: pgPortOf(t)}
		p := storeProbe{"postgres", func(c context.Context, sc *jobs.StepContext) error {
			return sc.Ops.Drivers.Postgres().Ready(c, spec)
		}, nil}
		if instanceMode {
			instance := instanceNameFor(render.LegPostgres, t.ManifestName)
			p.gone = func(c context.Context, sc *jobs.StepContext) (string, bool, error) {
				return instanceGoneReason(c, sc, instance)
			}
		}
		out = append(out, p)
	}
	return out
}

// instanceGoneReason answers "this instance is not coming back, and here is
// what it said on the way out".
//
// An instance that is in the ctl's table is alive as far as anything here can
// tell, and the answer is (–, false). One that is NOT is a container apptainer
// started successfully and that then exited: there is no exit status to read
// (the instance file is gone with it) and no signal anywhere — only the .err
// file apptainer has been appending its stderr to. That file is where postgres
// writes
//
//	FATAL:  data directory "…/pgdata" has wrong ownership
//
// which is the entire answer to the outage this function was written for.
//
// It never fails the caller on its own account: a log that cannot be read is
// still an instance that is gone, and the reason then says so instead of
// quoting. The one thing it must not do is claim an instance is dead when the
// instance table could not be read at all — that is a host problem, not a dead
// store, and it answers (–, false, err).
func instanceGoneReason(ctx context.Context, sc *jobs.StepContext, instance string) (string, bool, error) {
	up, err := instanceRunning(ctx, sc, instance, jobs.NamespaceCtl)
	if err != nil {
		return "", false, err
	}
	if up {
		return "", false, nil
	}
	_, errLog, lerr := sc.Ops.Drivers.Instances().LogPaths(ctx, instance, jobs.ListOptions{Namespace: jobs.NamespaceCtl})
	if lerr != nil || errLog == "" {
		return fmt.Sprintf("the instance %s is not running and this account cannot say where apptainer put its log",
			instance), true, nil
	}
	offset, marked := errLogOffset(sc, errLog)
	if !marked {
		// No mark means no step in THIS job started this instance, so every
		// byte in that file belongs to an earlier run. Naming the file is
		// honest; quoting it would not be.
		return fmt.Sprintf("the instance %s is not running; its log is %s, and nothing in this job recorded where "+
			"this attempt's output starts, so none of it is quoted here", instance, errLog), true, nil
	}
	body, rerr := sc.Ops.Drivers.Files().ReadFile(ctx, errLog)
	if rerr != nil {
		return fmt.Sprintf("the instance %s is not running; its log %s could not be read (%v)",
			instance, errLog, rerr), true, nil
	}
	if offset > int64(len(body)) {
		// The file is SHORTER than when this job started the instance:
		// something truncated or replaced it, and what is in it now is not
		// this attempt's output.
		return fmt.Sprintf("the instance %s exited; %s was truncated since this job started it, so none of what "+
			"is in it now is this attempt's output", instance, errLog), true, nil
	}
	tail := logTail(body[offset:])
	if tail == "" {
		return fmt.Sprintf("the instance %s exited without writing anything to %s", instance, errLog), true, nil
	}
	return fmt.Sprintf("the instance %s exited; %s says: %s", instance, errLog, tail), true, nil
}

// errLogMark is the external id that records how long an instance's stderr log
// is BEFORE this job starts it: `errlog:<path>@<bytes>`.
//
// A log that cannot be measured is recorded as a bare `errlog:`, which reads as
// "unmarked" later: a step must not fail to START a store because it could not
// stat a log file.
func errLogMark(ctx context.Context, sc *jobs.StepContext, instance string) string {
	_, errLog, err := sc.Ops.Drivers.Instances().LogPaths(ctx, instance, jobs.ListOptions{Namespace: jobs.NamespaceCtl})
	if err != nil || errLog == "" {
		return "errlog:"
	}
	size := int64(0)
	if st, serr := sc.Ops.Drivers.Files().Stat(ctx, errLog); serr == nil {
		size = st.Size
	} else if !errors.Is(serr, fs.ErrNotExist) {
		return "errlog:"
	}
	return "errlog:" + errLog + "@" + strconv.FormatInt(size, 10)
}

// errLogOffset finds the mark THIS JOB recorded for this log.
//
// It looks across the whole job rather than at one step, because the step that
// started the instance and the step that discovers it is gone are two different
// steps (the store start, and the readiness wait inside the API start or the
// postgres restore). sc.Job.Steps is how a run half reads back a checkpoint
// another step wrote — the same way the bundle id is read.
func errLogOffset(sc *jobs.StepContext, errLog string) (int64, bool) {
	if sc.Job == nil {
		return 0, false
	}
	want := "errlog:" + errLog + "@"
	for _, st := range sc.Job.Steps {
		for _, id := range st.ExternalIDs {
			rest, ok := strings.CutPrefix(id, want)
			if !ok {
				continue
			}
			n, err := strconv.ParseInt(rest, 10, 64)
			if err != nil {
				return 0, false
			}
			return n, true
		}
	}
	return 0, false
}

// logTailLines and logTailBytes bound what a failure message quotes. A store's
// .err file is small (kilobytes), but "small" is not a guarantee, and a job
// result is read in a terminal.
const (
	logTailLines = 12
	logTailBytes = 2000
)

// logTail is the last few lines of a log, joined with " / " so that the whole
// thing is one line of a job error.
//
// It does NOT reorder them. An earlier version hoisted any line containing
// "wrong ownership" to the front, on the theory that postgres prints it before
// two lines of consequence — which is true, and which would also hoist a line
// from a PREVIOUS run of the same instance into the report of this one. The
// caller slices the log at the offset this job recorded before it started the
// instance; inside that slice the order is postgres's own.
func logTail(body []byte) string {
	if len(body) > logTailBytes {
		body = body[len(body)-logTailBytes:]
	}
	var lines []string
	for _, l := range strings.Split(string(body), "\n") {
		if l = strings.TrimSpace(l); l != "" {
			lines = append(lines, l)
		}
	}
	if len(lines) > logTailLines {
		lines = lines[len(lines)-logTailLines:]
	}
	return strings.Join(lines, " / ")
}

// sharedStoreProbes are the stores this tenant uses and does NOT own: a shared
// qdrant, a shared elasticsearch, a postgres in somebody else's server.
//
// The ctl never starts or stops one — `capabilities.stop` is false for them by
// construction, and `legs` reports them unmanaged — but a tenant cannot serve
// without them, so the one thing it CAN do is wait. That is the difference
// between this list and ownStoreProbes: the same probe, a longer bound, and no
// claim of ownership anywhere.
func sharedStoreProbes(t *registry.Tenant, tp paths.Tenant) []storeProbe {
	var out []storeProbe
	if q := t.Stores.Qdrant; q.Ownership != registry.OwnershipExclusive && q.URL != "" {
		url := q.URL
		out = append(out, storeProbe{"the shared qdrant at " + url, func(c context.Context, sc *jobs.StepContext) error {
			return sc.Ops.Drivers.Qdrant().Ready(c, url)
		}, nil})
	}
	if e := t.Stores.Elasticsearch; e.Ownership != registry.OwnershipExclusive && e.URL != "" {
		url := e.URL
		out = append(out, storeProbe{"the shared elasticsearch at " + url, func(c context.Context, sc *jobs.StepContext) error {
			return sc.Ops.Drivers.Elasticsearch().Ready(c, url)
		}, nil})
	}
	// An EXTERNAL postgres — a database inside a server somebody else runs —
	// is exactly the same case as a shared qdrant, and was the one leg this
	// list forgot: a tenant whose ACL and job state live in another account's
	// server cannot serve a single request until that server answers, and at
	// boot the ctl has no idea when that will be.
	if pg := t.Stores.Postgres; pg.Kind == registry.PostgresKindExternal {
		spec := jobs.PostgresSpec{SIF: string(pg.SIF), RunDir: tp.PostgresRun, DB: t.Name, User: t.Name, Port: pgPortOf(t)}
		out = append(out, storeProbe{"the external postgres on " + strconv.Itoa(spec.Port),
			func(c context.Context, sc *jobs.StepContext) error {
				return sc.Ops.Drivers.Postgres().Ready(c, spec)
			}, nil})
	}
	return out
}

// awaitStores polls every probe until it answers or the bound passes. Wall
// clock and a real sleep, for the reason create's readiness gate gives: this
// is a run half waiting for a process to open its files.
func awaitStores(ctx context.Context, sc *jobs.StepContext, probes []storeProbe, bound time.Duration) error {
	deadline := time.Now().Add(bound)
	for _, pr := range probes {
		attempt := 0
		for {
			err := pr.probe(ctx, sc)
			if err == nil {
				sc.Logf("%s answers", pr.what)
				break
			}
			attempt++
			// Is it even still there? A store that has ALREADY EXITED is not
			// going to answer, and waiting out the bound for it costs the
			// three minutes the failed take spent and then reports the symptom
			// instead of the cause.
			//
			// Not on the first attempt: a store is briefly neither answering
			// nor yet in the instance table, and a fast-fail on that race
			// would be worse than the wait it replaces.
			if pr.gone != nil && attempt > 1 {
				reason, dead, gerr := pr.gone(ctx, sc)
				switch {
				case gerr != nil:
					// A host that cannot be asked is not a dead store. Log it
					// and go on waiting.
					sc.Logf("could not check whether %s is still running: %v", pr.what, gerr)
				case dead:
					return fmt.Errorf("%s is not coming up: %s (the wait was %s and %s of it had passed; it was cut "+
						"short because the process is already gone) — the readiness probe's own answer was: %w",
						pr.what, reason, bound, time.Since(deadline.Add(-bound)).Truncate(time.Second), err)
				}
			}
			if time.Now().After(deadline) {
				return fmt.Errorf("%s did not become ready within %s: %w", pr.what, bound, err)
			}
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(createReadyPoll):
			}
		}
	}
	return nil
}
