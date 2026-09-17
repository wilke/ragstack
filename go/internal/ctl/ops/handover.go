package ops

// The handover: a tenant somebody started by hand becomes a tenant the
// control plane supervises.
//
// It is TWO jobs run by TWO accounts, not one job with three phases, and the
// reason is a hard fact about this host rather than a preference:
//
//   - the daemon (svcbvbrc) cannot signal the hand-started uvicorn — reading
//     `/proc/<pid>/cwd` of another account's process is denied, so the
//     identity check every signal in this control plane makes cannot even be
//     attempted (drivers/proc.go);
//   - it cannot see, let alone stop, the owner's apptainer instances: each
//     account has its own `APPTAINER_CONFIGDIR` and therefore its own instance
//     registry (drivers/instances.go).
//
// So the OWNER releases and the SERVICE ACCOUNT takes:
//
//	wilke$     ragstack-ctl tenant handover <t> --release --yes-destructive <t>
//	           → census, stop, `state: handover`, a token printed
//	svcbvbrc$  ragstack-ctl tenant handover <t> --take --token <token> …
//	           → start under `supervisor: instance`, check the census back,
//	             PARK at the cutover
//	svcbvbrc$  ragstack-ctl tenant handover <t> --commit     (the continuation)
//	           → owner, desired_boot, the handover block cleared
//
// and, when the take does not convince:
//
//	svcbvbrc$  ragstack-ctl tenant stop <t>
//	wilke$     ragstack-ctl tenant handover <t> --abandon --yes-destructive <t>
//	wilke$     ops/coconut/restore.sh --tenant <t>
//
// Two invariants hold at every point between them:
//
//   - the ROW says what is true. `state: handover` means "down on purpose,
//     mid-move"; it is written BEFORE anything is stopped, so a job that dies
//     between the registry write and the last `instance stop` leaves a row
//     that tells the next operator where they are. Nothing here ever leaves
//     the row claiming `active` over a tenant that is not.
//   - the tenant is RESTORABLE. Before the release: `restore.sh --tenant <t>`.
//     After the take: `fleet start --all`. In between, and after an abandon:
//     `restore.sh --tenant <t>` again, from the rollback descriptor the row
//     has carried since `adopt --commit`.
//
// A handover moves no data. The same directories serve the same processes
// under another account, through the ACLs PR-D2 installed — so the risk this
// file is written against is not data loss, it is a tenant that neither
// account can start.

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/render"
)

// The phases, as the contract spells them.
const (
	phaseRelease = "release"
	phaseTake    = "take"
	phaseCommit  = "commit"
	phaseAbandon = "abandon"
)

// sharedStoreWait is how long the take waits for a store this tenant does NOT
// own before it spawns the API.
//
// It exists because the boot order of the two accounts is not coordinated:
// wilke's `restore.sh` brings the shared qdrant/elasticsearch up, svcbvbrc's
// `@reboot` crontab line runs `fleet start --all`, and neither waits for the
// other. Without this wait the API of a shared-store tenant comes up first,
// fails to reach a store that is thirty seconds away, and dies — which is
// exactly what "the reboot brought three tenants back and one did not" looked
// like before there was one.
//
// 180 s rather than the 120 s an exclusive store gets: a shared Elasticsearch
// opening every tenant's segments is slower than one opening this tenant's,
// and the cost of waiting too long is a late start rather than a dead API.
const sharedStoreWait = 180 * time.Second

func planHandover(ctx context.Context, p *planner, args map[string]any) error {
	phase := argStringOf(args, "phase")
	token := argStringOf(args, "token")
	if token != "" && phase != phaseTake {
		return fmt.Errorf("%w: handover.token belongs to `--take` (it is the nonce the release printed); "+
			"`--%s` does not take one", jobs.ErrValidation, phase)
	}
	if phase == phaseTake && token == "" {
		// Checked HERE, before the row is looked at: it is a statement about
		// the request rather than about the fleet, and the contract's 422/409
		// split is what tells an operator "you typed it wrong" apart from "the
		// fleet says no".
		return fmt.Errorf("%w: handover: `--take` needs the token the release printed (--token)",
			jobs.ErrValidation)
	}
	if argBoolOf(args, "accept_no_backup") && phase != phaseRelease {
		return fmt.Errorf("%w: handover.accept_no_backup is a decision the RELEASE makes (it is the phase with a "+
			"backup prerequisite); `--%s` does not take it", jobs.ErrValidation, phase)
	}
	switch phase {
	case phaseRelease:
		return planHandoverRelease(ctx, p, args)
	case phaseTake:
		return planHandoverTake(ctx, p, token)
	case phaseCommit:
		// Like `migrate-local --phase commit`: the commit belongs to the job
		// that is parked at its cutover, with that job's locks, that job's
		// reservations and that job's recorded plan. Accepting it as a new job
		// would start a second one holding none of them.
		return p.refuse("`handover --commit` is the CONTINUATION of the job that ran `--take` and is parked at its "+
			"cutover, not a new job: `ragstack-ctl job list --tenant %s --state awaiting_cutover` names it and "+
			"`ragstack-ctl job continue <id>` releases it (POST /v1/jobs/{id}/continue). "+
			"`ragstack-ctl tenant handover %s --commit` does both for you", p.tenant, p.tenant)
	case phaseAbandon:
		return planHandoverAbandon(p)
	default:
		// Unreachable: the args schema's enum is the four phases.
		return fmt.Errorf("%w: handover: phase %q is not one of release, take, commit, abandon",
			jobs.ErrValidation, phase)
	}
}

// ---------------------------------------------------------------- release

func planHandoverRelease(ctx context.Context, p *planner, args map[string]any) error {
	p.need(model.LockTenant, model.LockRegistry, model.LockManifest)
	t := p.t
	account := p.op.deps.owner()

	// WHO is running this. A release stops processes through a pidfile and an
	// `apptainer instance stop`, and both of those are the OWNER's to do: run
	// by anybody else they refuse — the signal because /proc is unreadable
	// across accounts, the instance stop because it would look in the wrong
	// instance registry and report "not running" for a tenant that is.
	if t.Owner != account {
		return p.refuse("%s's processes belong to %s and this job is running as %s: a release stops the tenant, and "+
			"one account cannot signal another's processes on this host. Run it as %s "+
			"(`ragstack-ctl tenant handover %s --release --direct`, with CTL_STATE_DIR/CTL_CONFIG_DIR pointing at "+
			"a state dir %s can write)", t.Name, t.Owner, account, t.Owner, t.Name, t.Owner)
	}
	if t.Supervisor != supervisorManual {
		return p.refuse("%s is already supervised by `%s`: there is nothing to release. A row that disagrees with "+
			"the host is corrected with `ragstack-ctl tenant set-supervisor %s manual`", t.Name, t.Supervisor, t.Name)
	}
	if h := t.Handover; h != nil {
		return p.refuse("%s already has a handover in flight (phase %s since %s): take it "+
			"(`ragstack-ctl tenant handover %s --take --token <token>`, as the service account) or abandon it "+
			"(`ragstack-ctl tenant handover %s --abandon`)", t.Name, h.Phase, h.StartedAt, t.Name, t.Name)
	}
	if t.State != "active" {
		return p.refuse("%s is `%s`, not active: a release stops a RUNNING tenant and records what it was holding. "+
			"Start it first, or — if it is already down — hand it over with "+
			"`ragstack-ctl tenant set-supervisor %s instance` and `ragstack-ctl tenant start %s` as the service "+
			"account", t.Name, t.State, t.Name, t.Name)
	}
	if t.API.Bind != "127.0.0.1" {
		return p.refuse("%s's api.bind is %s: a handed-over API listens on loopback and is reached through the "+
			"gateway (plan PR-E decision D1). Run `ragstack-ctl tenant set-bind %s 127.0.0.1` first — it takes "+
			"effect at the restart this handover performs", t.Name, orNone(t.API.Bind), t.Name)
	}
	if t.UI.Mode == registry.UIModeDev {
		return p.refuse("%s's UI is in `dev` mode: instance supervision has nowhere to put a Vite dev server. "+
			"Build it (`ragstack-ctl tenant set-ui-mode %s static`) or declare the hand-run one unmanaged "+
			"(`ragstack-ctl tenant set-ui-mode %s external --ui-port <P>`)", t.Name, t.Name, t.Name)
	}
	if t.RollbackDescriptor == nil {
		return p.refuse("%s has no rollback_descriptor: the pre-handover paths, ports, code and launch arguments "+
			"were never captured, so `ops/coconut/restore.sh --tenant %s` would have nothing to start it from. "+
			"Run `ragstack-ctl adopt %s --commit` first", t.Name, t.Name, t.Name)
	}
	if err := p.requireHandoverBackup(args); err != nil {
		return err
	}
	legs, err := p.legs(nil)
	if err != nil {
		return err
	}
	if err := requireConfirmedStores(p, legs); err != nil {
		return err
	}

	origin := fmt.Sprintf("http://127.0.0.1:%d", t.Ports.API)
	secretsEnv := p.tpaths.SecretsEnv
	// Filled by the run halves, read by the registry step and by the result.
	census := &[]registry.CensusEntry{}

	// ---- everything that can refuse runs BEFORE anything is stopped.

	p.addHandoverPGPasswordCheck(legs)
	p.addNoRunningIngest(origin, secretsEnv)
	p.addCensus(legs, origin, secretsEnv, census)

	// ---- the row, then the processes. In that order, always.

	p.addReleaseRegistryStep(census)
	p.addReleaseAPIStop()
	for _, c := range reverse(legs) {
		if c.Name == "api" || c.Name == "ui" || !c.Managed {
			continue
		}
		p.addReleaseStoreStop(c)
	}

	p.result["phase"] = registry.HandoverReleased
	p.result["released_by"] = account
	p.warn("after this job the tenant is DOWN and the gateway answers 502 for it. The next step is the take, as the " +
		"service account: `ragstack-ctl tenant handover " + t.Name + " --take --token <the token this job prints>`")
	p.warn("if the take cannot be made to work, `ops/coconut/restore.sh --tenant " + t.Name + "` starts the tenant " +
		"again exactly as it was started before, and `ragstack-ctl tenant handover " + t.Name + " --abandon` puts " +
		"the row back")
	return nil
}

// requireHandoverBackup is the release's safety net: a bundle of ANY scope.
//
// Not the fenced, verified bundle `decommission` demands, and deliberately so
// (plan decision D4): a handover moves no data — the same directories serve
// the same processes under another account — so what has to be recoverable is
// the tenant's CONFIGURATION and its SQLite state, which the light bundle
// (`--scope config,state`) captures in seconds with the API still serving.
// Demanding a fenced full bundle would buy an outage for a copy of a corpus
// that is not going anywhere.
func (p *planner) requireHandoverBackup(args map[string]any) error {
	if b := p.t.LastBackup; b != nil {
		if !b.Fenced {
			p.warn("the safety net is " + b.Bundle + ", taken without a fence: it is the config/state recovery " +
				"point, not a consistent copy of the stores. That is what a handover needs (plan decision D4)")
		}
		return nil
	}
	if argBoolOf(args, "accept_no_backup") {
		p.warn("last_backup is null and accept_no_backup was passed: this handover has NO recovery point for the " +
			"tenant's configuration. `ragstack-ctl tenant backup " + p.t.Name + " --scope config,state` takes " +
			"seconds and does not stop anything")
		return nil
	}
	return p.refuse("%s has no backup: take the light one first — `ragstack-ctl tenant backup %s --scope "+
		"config,state` — which captures the config allowlist, the sealed secrets, the SQLite state, the rendered "+
		"units and the rollback descriptor in seconds, with the API still serving. Pass accept_no_backup to "+
		"proceed without one", p.t.Name, p.t.Name)
}

// requireConfirmedStores refuses a release that would leave the tenant's own
// stores running as the account that is handing it over.
//
// doctor's `stores_unconfirmed` says the same thing and is raised to an error
// for this op, so this is the second reader — but it is the one that names the
// LEG and the command, and a precondition an operator can act on is worth
// saying twice.
func requireConfirmedStores(p *planner, legs []component) error {
	var unconfirmed []string
	for _, c := range legs {
		if c.Managed || c.Leg == "" {
			continue
		}
		if exclusiveLeg(p.t, c) {
			unconfirmed = append(unconfirmed, c.Name)
		}
	}
	if len(unconfirmed) == 0 {
		return nil
	}
	return p.refuse("%s owns %s exclusively but capabilities.stop is false for %s: a handover that moved the API "+
		"and left those stores running as %s would split the tenant between two accounts, and neither could then "+
		"restart it. Confirm them first: `ragstack-ctl adopt %s --readopt --confirm-stores %s`",
		p.t.Name, strings.Join(unconfirmed, ", "), plural2(len(unconfirmed)), p.t.Owner, p.t.Name,
		strings.Join(confirmNames(unconfirmed), ","))
}

// exclusiveLeg reports whether this store leg is this tenant's alone — the
// only kind a handover has to move.
func exclusiveLeg(t *registry.Tenant, c component) bool {
	switch c.Leg {
	case render.LegQdrant:
		return t.Stores.Qdrant.Ownership == registry.OwnershipExclusive
	case render.LegES:
		return t.Stores.Elasticsearch.Ownership == registry.OwnershipExclusive
	case render.LegPostgres:
		return t.Stores.Postgres.Kind == registry.PostgresKindLocal
	}
	return false
}

// confirmNames maps the leg names this package uses onto the ones
// `--confirm-stores` takes.
func confirmNames(legs []string) []string {
	out := make([]string, 0, len(legs))
	for _, l := range legs {
		if l == "es" {
			l = "elasticsearch"
		}
		out = append(out, l)
	}
	sort.Strings(out)
	return out
}

func plural2(n int) string {
	if n == 1 {
		return "it"
	}
	return "them"
}

// addHandoverPGPasswordCheck proves, BEFORE anything is stopped, that the take
// will be able to start the tenant's postgres.
//
// The instance supervisor reads the role password out of `secrets.env` at run
// time (`APPTAINERENV_POSTGRES_PASSWORD`, falling back to
// `TENANT_PG_PASSWORD`). A tenant provisioned by `new-tenant.sh` has it in
// neither: the literal lives inside the generated `bin/up.sh` and inside the
// connection strings in `secrets.env`, and nowhere under a name the supervisor
// looks for. Discovering that AFTER the release is discovering it with the
// tenant already down and its postgres refusing to start.
func (p *planner) addHandoverPGPasswordCheck(legs []component) {
	local := false
	for _, c := range legs {
		if c.Leg == render.LegPostgres && c.Managed {
			local = true
		}
	}
	if !local {
		p.skip("envfile", "check that the take can obtain the postgres password",
			"this tenant runs no postgres server of its own (stores.postgres.kind is "+
				p.t.Stores.Postgres.Kind+"), so there is no role password to hand over", p.tenant)
		return
	}
	secretsEnv := p.tpaths.SecretsEnv
	p.addFor("files", step{
		Kind: "envfile", Title: "check that the take can read the postgres password out of secrets.env",
		Targets: []string{secretsEnv},
		Warnings: []string{"the VALUE is never read into the plan, the log or the job: this step asks only whether " +
			"the key is there, because the take starts a postgres instance with it and a take that discovers it " +
			"is missing discovers it with the tenant already stopped"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			if _, err := postgresPassword(ctx, sc, secretsEnv); err != nil {
				return "", fmt.Errorf("%w — run `ragstack-ctl env pg-password %s` first: it derives "+
					"%s from the connection strings already in secrets.env, writes it there with a backup, and "+
					"never prints it", err, p.tenant, render.APPTAINERENVPostgresPassword)
			}
			return "secrets.env carries the postgres role password", nil
		},
	})
}

// addNoRunningIngest refuses a release over an ingest that is still writing.
//
// The release is the last moment at which anybody can decide to wait: after it
// the API is gone and whatever that job was in the middle of — a collection
// half its chunks, a manifest not yet written — is what the take will find.
func (p *planner) addNoRunningIngest(origin, secretsEnv string) {
	p.addFor("tenantapi", step{
		Kind: "probe", Title: "check that no ingest job is still running", Targets: []string{origin},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			key, err := adminKeyFromEnvFiles(ctx, sc, p.tpaths.TenantEnv, secretsEnv)
			if err != nil {
				return "", err
			}
			if key == "" {
				sc.Logf("no admin key could be read from the tenant's env files; the ingest check is skipped")
				return "skipped: no admin credential to ask with", nil
			}
			running, err := sc.Ops.Drivers.TenantAPI().RunningIngestJobs(ctx, origin, key)
			if err != nil {
				return "", fmt.Errorf("asking %s for its running ingest jobs: %w", origin, err)
			}
			if len(running) > 0 {
				return "", fmt.Errorf("%w: %d ingest job(s) are still running on %s (%s); a release stops the API "+
					"under them. Wait for them, or cancel them through the tenant, and run this again",
					jobs.ErrRefused, len(running), p.tenant, strings.Join(running, ", "))
			}
			return "no ingest job is running", nil
		},
	})
}

// addCensus records what the tenant held at the moment it was released.
//
// One step per source, so a store that cannot be counted is one failed step
// with a name on it rather than a census that silently came back short. A
// collection that answers no count is recorded as -1 — "listed, not counted" —
// which the take reports and never treats as agreement.
func (p *planner) addCensus(legs []component, origin, secretsEnv string, census *[]registry.CensusEntry) {
	t := p.t
	if q := t.Stores.Qdrant; q.Ownership == registry.OwnershipExclusive && q.URL != "" && legManaged(legs, "qdrant") {
		url := q.URL
		p.addFor("qdrant", step{
			Kind: "probe", Title: "census: count every collection in the tenant's own qdrant",
			Targets: []string{url},
			Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
				names, err := sc.Ops.Drivers.Qdrant().Collections(ctx, url)
				if err != nil {
					return "", err
				}
				for _, name := range names {
					n, err := sc.Ops.Drivers.Qdrant().Count(ctx, url, name)
					if err != nil {
						return "", fmt.Errorf("counting the qdrant collection %s: %w", name, err)
					}
					*census = append(*census, registry.CensusEntry{Store: "qdrant", Name: name, Count: n})
				}
				return fmt.Sprintf("%d qdrant collection(s) counted", len(names)), nil
			},
		})
	}
	if e := t.Stores.Elasticsearch; e.Ownership == registry.OwnershipExclusive && e.URL != "" && legManaged(legs, "es") {
		url := e.URL
		p.addFor("elasticsearch", step{
			Kind: "probe", Title: "census: count every index in the tenant's own elasticsearch",
			Targets: []string{url},
			Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
				names, err := sc.Ops.Drivers.Elasticsearch().Indices(ctx, url)
				if err != nil {
					return "", err
				}
				for _, name := range names {
					n, err := sc.Ops.Drivers.Elasticsearch().Count(ctx, url, name)
					if err != nil {
						return "", fmt.Errorf("counting the elasticsearch index %s: %w", name, err)
					}
					*census = append(*census, registry.CensusEntry{Store: "elasticsearch", Name: name, Count: n})
				}
				return fmt.Sprintf("%d elasticsearch index/indices counted", len(names)), nil
			},
		})
	}
	p.addFor("tenantapi", step{
		Kind: "probe", Title: "census: the tenant API's own collection listing, with counts",
		Targets: []string{origin},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			key, err := adminKeyFromEnvFiles(ctx, sc, p.tpaths.TenantEnv, secretsEnv)
			if err != nil {
				return "", err
			}
			if key == "" {
				sc.Logf("no admin key could be read from the tenant's env files; the API census is skipped")
				return "skipped: no admin credential to ask with", nil
			}
			counts, err := sc.Ops.Drivers.TenantAPI().CollectionCounts(ctx, origin, key)
			if err != nil {
				return "", fmt.Errorf("reading %s's collection counts: %w", p.tenant, err)
			}
			for _, name := range sortedCountKeys(counts) {
				*census = append(*census, registry.CensusEntry{Store: "api", Name: name, Count: counts[name]})
			}
			return fmt.Sprintf("%d collection(s) listed by the API", len(counts)), nil
		},
	})
}

func legManaged(legs []component, name string) bool {
	for _, c := range legs {
		if c.Name == name {
			return c.Managed
		}
	}
	return false
}

func sortedCountKeys(m map[string]int64) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// addReleaseRegistryStep writes `state: handover` and the block, BEFORE the
// first thing is stopped.
//
// The ordering is the whole design. A job that dies between this step and the
// last `instance stop` leaves a row that says "mid-move", which is true; the
// other order would leave a row saying `active` over a tenant with nothing
// running, which is the state an operator cannot tell from a crash.
func (p *planner) addReleaseRegistryStep(census *[]registry.CensusEntry) {
	name := p.tenant
	account := p.op.deps.owner()
	p.add(step{
		Kind: "registry", Title: "record state: handover and the hand-off token", Targets: []string{name},
		WouldWrite: []model.WouldWrite{{Path: p.oc.Roots.Registry(), Mode: "0660", Preview: model.NullString("")}},
		Warnings: []string{"this is written BEFORE anything is stopped: from here on the row says the tenant is " +
			"mid-move, which is what makes an interrupted release diagnosable"},
		Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			token, err := handoverToken()
			if err != nil {
				return "", err
			}
			at := p.stampRFC3339(sc)
			err = p.saveTenant(sc, "handover", func(t *registry.Tenant) error {
				t.State = registry.StateHandover
				t.Handover = &registry.Handover{
					Phase: registry.HandoverReleased, Token: token, StartedAt: at,
					ReleasedBy: account, ReleasedAt: registry.NullString(at),
					DescriptorRef: descriptorRef(t), Census: append([]registry.CensusEntry{}, *census...),
				}
				return nil
			})
			if err != nil {
				return "", err
			}
			// The token is a RESULT, not a log line: `job show` prints the
			// result, the CLI prints it, and the operator types it into the
			// take. It is a nonce and grants nothing, but it does not belong
			// in a step log that a viewer with less access can read.
			p.result["token"] = token
			p.result["census_rows"] = len(*census)
			return fmt.Sprintf("%s is `%s` with %d census row(s)", name, registry.StateHandover, len(*census)), nil
		},
		Rollback: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			err := p.saveTenant(sc, "", func(t *registry.Tenant) error {
				t.State, t.Handover = "active", nil
				return nil
			})
			if err != nil {
				// Said as loudly as a rollback can say it: a row left in
				// `handover` with the tenant running again is the one outcome
				// this whole file is written to avoid.
				sc.Logf("the row could NOT be put back: %s is still recorded as `%s` with a handover block. "+
					"Clear it with `ragstack-ctl tenant handover %s --abandon`", name, registry.StateHandover, name)
				return "", err
			}
			delete(p.result, "token")
			return name + " is `active` again and the handover block is cleared", nil
		},
	})
}

// descriptorRef is the captured_at of the descriptor this handover is
// reversible against.
func descriptorRef(t *registry.Tenant) registry.NullString {
	if t.RollbackDescriptor == nil {
		return ""
	}
	return registry.NullString(t.RollbackDescriptor.CapturedAt)
}

// addReleaseAPIStop stops the hand-started API the way `stop --force` does:
// the pidfile, the identity check, TERM, proof that the port is free, KILL if
// it is not.
func (p *planner) addReleaseAPIStop() {
	pidfile, worktree, port := p.apiPidFile(), p.t.Worktree, p.t.Ports.API
	name := p.tenant
	p.addFor("proc", step{
		Kind: "proc", Title: "stop the hand-started API through its pidfile (TERM, then KILL)", Destructive: true,
		Targets: []string{pidfile},
		Warnings: []string{"the ctl signals a pid it has verified is this tenant's (pidfile, cwd and cmdline); it " +
			"never runs pkill (MEMORY #402)"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return stopAPIProcess(ctx, sc, pidfile, worktree, port)
		},
		// The ctl cannot start this process again: its command line is the
		// operator's, not the registry's, and what recorded it is the rollback
		// descriptor rather than anything this job holds. So the rollback says
		// exactly what to run, which is a better answer than a spawn that
		// guesses at an argv.
		Rollback: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			sc.Logf("the API was stopped and this job does not know how to start it again: run "+
				"`ops/coconut/restore.sh --tenant %s`, which starts it from the rollback descriptor", name)
			return "start it again with `ops/coconut/restore.sh --tenant " + name + "`", nil
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
	p.addPortFreeStep(port, "the API")
}

// addReleaseStoreStop stops one exclusive store instance of the CURRENT
// account and proves its port is free.
func (p *planner) addReleaseStoreStop(c component) {
	instance := c.Instance
	if instance == "" {
		p.skip("instance", "stop "+c.Name,
			"this leg has no apptainer instance name recorded, so the ctl does not know what to stop", c.Name)
		return
	}
	st, err := render.StoreArgv(p.t, c.Leg, p.unitConfig())
	restartable := err == nil
	p.addFor("instance", step{
		Kind: "instance", Title: "stop the instance " + instance, Destructive: true, Targets: []string{instance},
		WouldRun: []model.WouldRun{{Argv: []string{"/usr/bin/apptainer", "instance", "stop", instance}}},
		Warnings: []string{"SIGTERM to the instance, which is the graceful shutdown elasticsearch needs; an " +
			"instance that is not running is success, so this step is safe to re-run"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			if err := sc.Checkpoint("instance:" + instance); err != nil {
				return "", err
			}
			if err := sc.Ops.Drivers.Instances().Stop(ctx, instance); err != nil {
				return "", err
			}
			return "stopped " + instance, nil
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			if !restartable {
				sc.Logf("%s cannot be rendered from the registry row (%v); start it again with "+
					"`ops/coconut/restore.sh --tenant %s`", instance, err, p.tenant)
				return "start it again with `ops/coconut/restore.sh --tenant " + p.tenant + "`", nil
			}
			// BEST EFFORT, and it says so: this runs as the account that owned
			// the instance, so it is the one rollback in this file that can
			// actually put a process back. A failure here is reported and does
			// not stop the remaining rollbacks — the restore script is still
			// the whole answer.
			spec := jobs.InstanceSpec{Name: instance, SIF: st.SIF, Binds: st.Binds, Env: st.Env,
				Args: st.Args, ExtraEnv: map[string]string{}}
			for k, v := range st.ProcessEnv {
				spec.ExtraEnv[k] = v
			}
			if c.Leg == render.LegPostgres {
				pw, perr := postgresPassword(ctx, sc, p.tpaths.SecretsEnv)
				if perr != nil {
					sc.Logf("%s needs its role password to start and secrets.env does not carry one (%v); "+
						"run `ops/coconut/restore.sh --tenant %s`", instance, perr, p.tenant)
					return "start it again with `ops/coconut/restore.sh --tenant " + p.tenant + "`", nil
				}
				spec.ExtraEnv[render.APPTAINERENVPostgresPassword] = pw
			}
			if rerr := sc.Ops.Drivers.Instances().Run(ctx, spec); rerr != nil {
				sc.Logf("%s could not be started again (%v); run `ops/coconut/restore.sh --tenant %s`",
					instance, rerr, p.tenant)
				return "start it again with `ops/coconut/restore.sh --tenant " + p.tenant + "`", nil
			}
			return "started " + instance + " again", nil
		},
		Reconcile: func(ctx context.Context, sc *jobs.StepContext) (jobs.Reconciliation, error) {
			up, err := instanceRunning(ctx, sc, instance)
			if err != nil {
				return jobs.ReconcileStuck, err
			}
			if up {
				return jobs.ReconcileRedo, nil
			}
			return jobs.ReconcileDone, nil
		},
	})
	if c.Port != 0 {
		p.addPortFreeStep(c.Port, c.Name)
	}
}

// addPortFreeStep is the proof: nothing listens there any more, so the other
// account can bind it.
func (p *planner) addPortFreeStep(port int, what string) {
	p.addFor("proc", step{
		Kind: "probe", Title: fmt.Sprintf("verify nothing listens on %d (%s)", port, what),
		Targets: []string{strconv.Itoa(port)},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			listening, err := sc.Ops.Drivers.Proc().Listening(ctx, port)
			if err != nil {
				return "", err
			}
			if listening {
				return "", fmt.Errorf("%w: something is still listening on %d after %s was stopped; the other "+
					"account cannot bind it, so the take would fail", jobs.ErrRefused, port, what)
			}
			return fmt.Sprintf("port %d is free", port), nil
		},
	})
}

// ---------------------------------------------------------------- take

func planHandoverTake(_ context.Context, p *planner, token string) error {
	p.need(model.LockTenant, model.LockRegistry, model.LockManifest)
	t := p.t
	account := p.op.deps.owner()

	h := t.Handover
	switch {
	case h == nil:
		return p.refuse("%s has no handover in flight: `--take` continues a release, and none has been run. "+
			"As %s: `ragstack-ctl tenant handover %s --release`", t.Name, t.Owner, t.Name)
	case h.Phase != registry.HandoverReleased:
		return p.refuse("%s's handover is in phase `%s`, not `%s`: it has already been taken. Commit it "+
			"(`ragstack-ctl tenant handover %s --commit`) or abandon it", t.Name, h.Phase,
			registry.HandoverReleased, t.Name)
	case token != h.Token:
		// The expected value is NOT echoed. It is a nonce rather than a
		// credential, but a refusal that prints the answer is a refusal that
		// teaches nothing, and the operator who ran the release has it.
		return p.refuse("the token does not match the one %s's release recorded at %s. Read it off that job "+
			"(`ragstack-ctl job show <id>`), or abandon the handover and release again", t.Name, h.StartedAt)
	}
	if t.State != registry.StateHandover {
		return p.refuse("%s's handover says `released` but its state is `%s`: the row disagrees with itself. "+
			"Abandon the handover and start again", t.Name, t.State)
	}
	if t.Supervisor != supervisorManual {
		return p.refuse("%s is already supervised by `%s`; a take gives a tenant instance supervision and this row "+
			"already claims some", t.Name, t.Supervisor)
	}

	// The supervisor this tenant is MOVING ONTO, not the one the row records.
	// The row still says `manual` — that is what the release left behind — and
	// the registry step below is what changes it; every leg step from here on
	// is planned against the instance supervisor explicitly.
	p.sup = instanceSupervisor{}
	legs, err := p.legs(nil)
	if err != nil {
		return err
	}

	// Ports first: a take that started a second copy of a store on top of a
	// process the release did not manage to stop is the worst outcome here,
	// and it is one probe away.
	for _, c := range legs {
		if !c.Managed || c.Port == 0 {
			continue
		}
		p.addPortFreeStep(c.Port, c.Name)
	}

	p.addTakeSupervisorStep()
	for _, c := range legs {
		if !c.Managed {
			p.skip("instance", "skip "+c.Name, c.Why, c.Name)
			continue
		}
		if c.Name == "api" {
			continue
		}
		if err := p.sup.startLeg(p, c); err != nil {
			return err
		}
	}
	for _, c := range legs {
		if c.Name == "api" && c.Managed {
			if err := p.sup.startLeg(p, c); err != nil {
				return err
			}
		}
	}
	p.addReadyStep(legs)

	origin := fmt.Sprintf("http://127.0.0.1:%d", t.Ports.API)
	secretsEnv := p.tpaths.SecretsEnv
	p.addFor("tenantapi", step{
		Kind: "probe", Title: "post-check: GET /health on the tenant's own port", Targets: []string{origin},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return "health ok", sc.Ops.Drivers.TenantAPI().Health(ctx, origin)
		},
	})
	p.addDeepHealthCheck(origin, secretsEnv)
	p.addCensusCheck(h.Census, origin, secretsEnv)
	p.addGatewayProbe("/ragstack/" + t.Name + "/api/health")

	p.addTakeCutoverStep(account)
	p.addCommitStep(account)

	p.result["phase"] = registry.HandoverTaken
	p.result["supervisor"] = supervisorInstance
	p.warn("this job PARKS after its cutover, holding this tenant's locks: soak the tenant, then " +
		"`ragstack-ctl tenant handover " + t.Name + " --commit` (which continues this job). Until then " +
		"`ragstack-ctl tenant stop " + t.Name + "` as this account, plus `ragstack-ctl tenant handover " +
		t.Name + " --abandon` as " + t.Owner + ", is the way back")
	p.warn("the shared stores stay " + t.Owner + "-run: a handover moves the TENANT, not the host's shared services")
	return nil
}

// addTakeSupervisorStep records `supervisor: instance` before the first leg is
// started.
//
// Before, not after: the pidfile this job is about to write, and every
// reconcile that reads it, are the INSTANCE supervisor's, and a row that still
// said `manual` while the ctl's own uvicorn was running under it would be a
// row no later `tenant stop` would act on.
func (p *planner) addTakeSupervisorStep() {
	name := p.tenant
	p.add(step{
		Kind: "registry", Title: "record supervisor: instance", Targets: []string{name},
		WouldWrite: []model.WouldWrite{{Path: p.oc.Roots.Registry(), Mode: "0660", Preview: model.NullString("")}},
		Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			err := p.saveTenant(sc, "", func(t *registry.Tenant) error {
				t.Supervisor = supervisorInstance
				return nil
			})
			if err != nil {
				return "", err
			}
			return name + " supervisor = " + supervisorInstance, nil
		},
		Rollback: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			if err := p.saveTenant(sc, "", func(t *registry.Tenant) error {
				t.Supervisor = supervisorManual
				return nil
			}); err != nil {
				return "", err
			}
			return name + " supervisor = " + supervisorManual + " again", nil
		},
	})
}

// addDeepHealthCheck asks the tenant itself about every store it was
// configured with — a stronger post-check than a port that accepts a
// connection, and the one that catches a tenant started against a store the
// other account still owns.
func (p *planner) addDeepHealthCheck(origin, secretsEnv string) {
	p.addFor("tenantapi", step{
		Kind: "probe", Title: "post-check: GET /v1/health/deep with the tenant's own admin key",
		Targets: []string{origin},
		Warnings: []string{"the credential is read from the tenant's secrets.env at RUN time and is never in the " +
			"plan, the log or the job result"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			key, err := adminKeyFromEnvFiles(ctx, sc, p.tpaths.TenantEnv, secretsEnv)
			if err != nil {
				return "", err
			}
			if key == "" {
				sc.Logf("no admin key could be read from the tenant's env files; the deep health check is skipped")
				return "skipped: no admin credential to ask with", nil
			}
			if err := sc.Ops.Drivers.TenantAPI().DeepHealth(ctx, origin, key); err != nil {
				return "", err
			}
			return "deep health ok", nil
		},
	})
}

// addCensusCheck compares what came back with what went down.
//
// It refuses on a SHORTFALL and reports a surplus. A collection with more rows
// than the census recorded is a tenant that was written to between the two
// readings — surprising, worth saying, and not a reason to refuse a start that
// has already happened. Fewer rows is a store that came up on the wrong data
// directory, which is.
func (p *planner) addCensusCheck(census []registry.CensusEntry, origin, secretsEnv string) {
	if len(census) == 0 {
		p.skip("probe", "compare the census against what came back",
			"the release recorded no census rows (an empty tenant, or no credential to ask the API with)", p.tenant)
		return
	}
	t := p.t
	qURL, esURL := t.Stores.Qdrant.URL, t.Stores.Elasticsearch.URL
	p.addFor("qdrant", step{
		Kind: "probe", Title: fmt.Sprintf("compare the census (%d row(s)) against what came back", len(census)),
		Targets: []string{p.tenant},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			var short, surplus, uncounted []string
			for _, e := range census {
				if e.Count < 0 {
					uncounted = append(uncounted, e.Store+"/"+e.Name)
					continue
				}
				now, err := censusCount(ctx, sc, e, qURL, esURL, origin, p.tpaths.TenantEnv, secretsEnv)
				if err != nil {
					return "", fmt.Errorf("re-counting %s/%s: %w", e.Store, e.Name, err)
				}
				switch {
				case now < 0:
					uncounted = append(uncounted, e.Store+"/"+e.Name)
				case now < e.Count:
					short = append(short, fmt.Sprintf("%s/%s %d → %d", e.Store, e.Name, e.Count, now))
				case now > e.Count:
					surplus = append(surplus, fmt.Sprintf("%s/%s %d → %d", e.Store, e.Name, e.Count, now))
				}
			}
			if len(short) > 0 {
				return "", fmt.Errorf("%w: %s came back with FEWER rows than it went down with (%s). The store is "+
					"up but it is not on the data it was on: stop this tenant (`ragstack-ctl tenant stop %s`), "+
					"abandon the handover and start it again with `ops/coconut/restore.sh --tenant %s`",
					jobs.ErrRefused, p.tenant, strings.Join(short, "; "), p.tenant, p.tenant)
			}
			for _, s := range surplus {
				sc.Logf("more rows than the census recorded (%s): the tenant was written to between the release "+
					"and the take", s)
			}
			for _, u := range uncounted {
				sc.Logf("%s could not be counted on one side of the handover; it is not part of the proof", u)
			}
			return fmt.Sprintf("%d of %d census row(s) checked, none short", len(census)-len(uncounted), len(census)), nil
		},
	})
}

// censusCount re-reads ONE census row. -1 is "could not be counted", which the
// caller reports and does not treat as agreement.
func censusCount(ctx context.Context, sc *jobs.StepContext, e registry.CensusEntry,
	qURL, esURL, origin, tenantEnv, secretsEnv string) (int64, error) {
	switch e.Store {
	case "qdrant":
		if qURL == "" {
			return -1, nil
		}
		return sc.Ops.Drivers.Qdrant().Count(ctx, qURL, e.Name)
	case "elasticsearch":
		if esURL == "" {
			return -1, nil
		}
		return sc.Ops.Drivers.Elasticsearch().Count(ctx, esURL, e.Name)
	default:
		key, err := adminKeyFromEnvFiles(ctx, sc, tenantEnv, secretsEnv)
		if err != nil || key == "" {
			return -1, err
		}
		counts, err := sc.Ops.Drivers.TenantAPI().CollectionCounts(ctx, origin, key)
		if err != nil {
			return 0, err
		}
		n, ok := counts[e.Name]
		if !ok {
			return 0, nil
		}
		return n, nil
	}
}

// addGatewayProbe is the outside view: the route the users use answers.
func (p *planner) addGatewayProbe(path string) {
	p.addFor("gateway", step{
		Kind: "probe", Title: "post-check: GET " + path + " through the live gateway (expect 200)",
		Targets: []string{path},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			status, err := sc.Ops.Drivers.Gateway().Probe(ctx, path)
			if err != nil {
				return "", fmt.Errorf("GET %s through the gateway: %w", path, err)
			}
			if status != 200 {
				return "", fmt.Errorf("%w: GET %s answered %d, want 200: the tenant is up on its own port but the "+
					"gateway is not serving it", jobs.ErrRefused, path, status)
			}
			return fmt.Sprintf("GET %s = 200", path), nil
		},
	})
}

// addTakeCutoverStep is the CUTOVER: the row says the tenant is active again
// and the handover is `taken`.
//
// After it the job PARKS (jobs/engine.go: a cutover step that is not the last
// one parks the run in `awaiting_cutover`, holding its locks). The soak
// happens there, and `job continue` runs the commit below it.
func (p *planner) addTakeCutoverStep(account string) {
	name := p.tenant
	pidfile := p.apiPidFile()
	p.add(step{
		Kind: "registry", Title: "cutover: record state: active and handover.phase: taken", Targets: []string{name},
		Cutover:    true,
		WouldWrite: []model.WouldWrite{{Path: p.oc.Roots.Registry(), Mode: "0660", Preview: model.NullString("")}},
		Warnings: []string{"the job PARKS here holding this tenant's locks: nothing else may touch it while a " +
			"handover is uncommitted. `ragstack-ctl tenant handover " + name + " --commit` releases it"},
		Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			at := p.stampRFC3339(sc)
			err := p.saveTenant(sc, "", func(t *registry.Tenant) error {
				t.State = "active"
				t.API.PidFile = pidfile
				if t.Handover == nil {
					return fmt.Errorf("%w: %s's handover block is gone; something else cleared it while this job "+
						"was running", jobs.ErrRefused, name)
				}
				t.Handover.Phase = registry.HandoverTaken
				t.Handover.TakenAt = registry.NullString(at)
				t.Handover.TakenBy = registry.NullString(account)
				return nil
			})
			if err != nil {
				return "", err
			}
			return name + " is active again, handover.phase = " + registry.HandoverTaken, nil
		},
		Rollback: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			err := p.saveTenant(sc, "", func(t *registry.Tenant) error {
				t.State = registry.StateHandover
				if t.Handover != nil {
					t.Handover.Phase = registry.HandoverReleased
					t.Handover.TakenAt, t.Handover.TakenBy = "", ""
				}
				return nil
			})
			if err != nil {
				return "", err
			}
			return name + " is `" + registry.StateHandover + "` again", nil
		},
	})
}

// addCommitStep is what `job continue` runs: the tenant becomes the service
// account's, for good.
//
// The rollback descriptor is deliberately NOT touched. It is the immutable
// record of how the tenant ran before the ctl had it, and it is what
// `restore.sh --tenant <n>` reads — a commit that cleared it would be a commit
// after which the only way back was gone.
func (p *planner) addCommitStep(account string) {
	name := p.tenant
	p.add(step{
		Kind: "registry", Title: "commit: owner " + account + ", desired_boot enabled, handover cleared",
		Targets:    []string{name},
		WouldWrite: []model.WouldWrite{{Path: p.oc.Roots.Registry(), Mode: "0660", Preview: model.NullString("")}},
		Warnings: []string{"after this step the tenant is the control plane's: `fleet start --all` brings it back " +
			"at boot and `ops/coconut/restore.sh` skips it. The rollback_descriptor is kept, untouched"},
		Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			err := p.saveTenant(sc, "handover", func(t *registry.Tenant) error {
				t.Owner = account
				t.DesiredBoot = "enabled"
				t.Supervisor = supervisorInstance
				t.State = "active"
				// The processes running right now ARE the row: the bind, the
				// pidfile and the env the take started them with are what the
				// registry records, so nothing is pending any more.
				t.RestartPending = false
				t.Handover = nil
				return nil
			})
			if err != nil {
				return "", err
			}
			p.result["owner"] = account
			p.result["desired_boot"] = "enabled"
			return name + " is owned by " + account + " and supervised by " + supervisorInstance, nil
		},
	})
}

// ---------------------------------------------------------------- abandon

func planHandoverAbandon(p *planner) error {
	p.need(model.LockTenant, model.LockRegistry, model.LockManifest)
	t := p.t
	account := p.op.deps.owner()
	h := t.Handover
	if h == nil {
		return p.refuse("%s has no handover in flight: there is nothing to abandon", t.Name)
	}
	// The abandon puts the tenant back into the OWNER's hands, and the owner
	// is who has to start it again. Running it as the other account would
	// write a row nobody can act on.
	if t.Owner != account {
		return p.refuse("%s's rollback_descriptor belongs to %s and this job is running as %s: `--abandon` hands "+
			"the tenant back to the account that will start it again (`ops/coconut/restore.sh --tenant %s`). "+
			"Run it as %s", t.Name, t.Owner, account, t.Name, t.Owner)
	}
	port := t.Ports.API
	p.addFor("proc", step{
		Kind: "probe", Title: fmt.Sprintf("check that nothing this account cannot see holds %d", port),
		Targets: []string{strconv.Itoa(port)},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			proc := sc.Ops.Drivers.Proc()
			listening, err := proc.Listening(ctx, port)
			if err != nil {
				return "", err
			}
			if !listening {
				return fmt.Sprintf("port %d is free", port), nil
			}
			pid, _, err := proc.Owner(ctx, port)
			if err != nil {
				return "", err
			}
			if pid == 0 {
				return "", fmt.Errorf("%w: %d is held by a process this account cannot attribute — the take's API "+
					"is still running as the other account. Stop it there first (`ragstack-ctl tenant stop %s`), "+
					"then abandon", jobs.ErrRefused, port, p.tenant)
			}
			sc.Logf("pid %d holds %d and belongs to this account: the tenant is already running as %s",
				pid, port, p.t.Owner)
			return fmt.Sprintf("pid %d on %d belongs to this account", pid, port), nil
		},
	})
	name := p.tenant
	phase := h.Phase
	p.add(step{
		Kind: "registry", Title: "put the row back: supervisor manual, state active, handover cleared",
		Targets:    []string{name},
		WouldWrite: []model.WouldWrite{{Path: p.oc.Roots.Registry(), Mode: "0660", Preview: model.NullString("")}},
		Warnings: []string{"this writes the REGISTRY only: it starts nothing. The row says `active` because that " +
			"is what the tenant is about to be — `ops/coconut/restore.sh --tenant " + name + "` is the next command"},
		Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			err := p.saveTenant(sc, "handover", func(t *registry.Tenant) error {
				t.Supervisor = supervisorManual
				t.State = "active"
				t.Handover = nil
				return nil
			})
			if err != nil {
				return "", err
			}
			return name + " is hand-started again (the handover in phase " + phase + " is abandoned)", nil
		},
	})
	p.result["phase"] = "abandoned"
	p.result["supervisor"] = supervisorManual
	p.result["next"] = "ops/coconut/restore.sh --tenant " + name
	p.warn("nothing is running yet: run `ops/coconut/restore.sh --tenant " + name + "` to start the tenant from " +
		"its rollback descriptor, exactly as it was started before the release")
	return nil
}

// ---------------------------------------------------------------- shared helpers

// saveTenant applies one change to the tenant row of the fleet the ENGINE
// loaded under the locks and persists it.
//
// verb, when non-empty, is also recorded in last_ops. Every registry step in
// this file goes through it so that "the registry writer is not wired" and
// "the row is gone" are refused in one place with one sentence each.
func (p *planner) saveTenant(sc *jobs.StepContext, verb string, apply func(*registry.Tenant) error) error {
	save := p.op.deps.SaveFleet
	if save == nil {
		return fmt.Errorf("%w: the registry writer is not wired", jobs.ErrRefused)
	}
	f := sc.Ops.Fleet
	if f == nil {
		return fmt.Errorf("%w: the engine loaded no registry under the locks", jobs.ErrRefused)
	}
	t, ok := f.Tenants[p.tenant]
	if !ok {
		return fmt.Errorf("%w: %s is no longer in the registry", jobs.ErrRefused, p.tenant)
	}
	if err := apply(t); err != nil {
		return err
	}
	if verb != "" {
		if t.LastOps == nil {
			t.LastOps = map[string]registry.OpRecord{}
		}
		t.LastOps[verb] = registry.OpRecord{JobID: jobIDOf(sc), At: p.stampRFC3339(sc), Outcome: "succeeded"}
	}
	return save(f)
}

// handoverToken is the nonce: 16 random bytes as hex.
func handoverToken() (string, error) {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		return "", fmt.Errorf("the system random source failed: %w", err)
	}
	return hex.EncodeToString(b), nil
}

// adminKeyFromEnvFiles reads ONE admin credential out of the tenant's own env
// files, in the order the tenant API itself resolves them: LATER paths win,
// which is tenant.env then secrets.env — the same precedence
// `EnvironmentFile=`/`EnvironmentFile=-` gives the unit and `apiEnviron` gives
// a spawned one. A `legacy` tenant keeps its ledger in tenant.env and a
// `managed` one in secrets.env; a tenant mid-normalize has a stale copy in the
// first and the live one in the second, and reading them the other way round
// presents a key the tenant has already withdrawn.
//
// The ledger is the source: API_KEYS is the list of values and API_KEY_ROLES
// maps each to a role, so "an admin key" is a key this tenant will actually
// accept as an admin rather than a name somebody chose. TENANT_API_KEY_ADMIN
// is the fallback for a tenant created by `new-tenant.sh`, which writes that
// name as well.
//
// The value is returned to exactly one caller, which puts it in a header. It
// is never logged, never checkpointed, never a step target, and never a
// result — and an ABSENT key is "" with no error, because a tenant with no
// credential in a file is a tenant whose post-checks are skipped rather than a
// job that fails.
func adminKeyFromEnvFiles(ctx context.Context, sc *jobs.StepContext, paths ...string) (string, error) {
	merged := map[string]string{}
	for _, path := range paths {
		if path == "" {
			continue
		}
		vals, err := readEnvFile(ctx, sc, path)
		if err != nil {
			return "", err
		}
		for k, v := range vals {
			if v != "" {
				merged[k] = v
			}
		}
	}
	if key := adminKeyFromLedger(merged["API_KEYS"], merged["API_KEY_ROLES"]); key != "" {
		return key, nil
	}
	return merged["TENANT_API_KEY_ADMIN"], nil
}

// adminKeyFromLedger picks the first admin-role key out of the two JSON env
// values. Unparseable values are "no key", not an error: the grammar of those
// files is doctor's to report and a post-check is not the place to refuse over
// it.
func adminKeyFromLedger(keysJSON, rolesJSON string) string {
	var keys []string
	if json.Unmarshal([]byte(keysJSON), &keys) != nil {
		return ""
	}
	roles := map[string]string{}
	if json.Unmarshal([]byte(rolesJSON), &roles) != nil {
		return ""
	}
	for _, k := range keys {
		if roles[k] == "admin" {
			return k
		}
	}
	return ""
}
