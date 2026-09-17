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
//   - it cannot see, let alone stop, the owner's apptainer instances: an
//     instance registry is per account AND per `APPTAINER_CONFIGDIR`
//     (drivers/instances.go, jobs.InstanceNamespace).
//
// That second fact cuts both ways, and the RELEASE is where it bites. Every
// apptainer call the ctl makes is namespaced under `<CtlStateDir>/apptainer/
// config` so the daemon's instances are findable from any session (PR-D2) —
// but the tenant a release is releasing was started BY HAND, so its instances
// are in the owner's DEFAULT registry (`$HOME/.apptainer`). The first real
// release on coconut ran with the ctl's namespace forced on it: `apptainer
// instance stop postgres-hackathon` looked in a table that held nothing, the
// "an instance that is not running is success" rule reported a stop, and the
// tenant's postgres went on holding 24085 with the API already down. Ten
// minutes of 502s, and the job's own rollback could put none of it back.
//
// So the release half acts in `releaseNamespace`, it VERIFIES before it stops
// (addReleaseInstanceCheck, before the row is written), it POLLS after
// (awaitInstanceGone) rather than trusting the driver's idempotent success,
// its API stop ROLLS BACK BY STARTING THE API AGAIN, and a row it leaves at
// `released` can be re-run or abandoned rather than needing a registry edit.
//
// So the OWNER releases and the SERVICE ACCOUNT takes:
//
//	wilke$     ragstack-ctl tenant handover <t> --release --yes-destructive <t>
//	           → census, stop, `state: handover`, a token printed
//	svcbvbrc$  ragstack-ctl tenant handover <t> --take --token <token> …
//	           → start under `supervisor: instance`, check the census back,
//	             `owner: svcbvbrc`, `handover.phase: taken`. The job FINISHES.
//	svcbvbrc$  ragstack-ctl tenant handover <t> --commit      (after the soak)
//	           → desired_boot enabled, the handover block cleared
//
// Four jobs, and not one of them parks. A take that stopped at a cutover would
// hold this tenant's registry lock for the length of the soak — 48 hours for
// the `dev` drill — and the registry lock is the FLEET's: every backup of every
// other tenant would queue behind one operator's coffee. So the take completes,
// the row carries the state between the phases (`handover.phase: taken`, the
// token kept until a commit or an abandon clears it), and the commit is an
// ordinary job gated on that row.
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
//     After the take: `ragstack-ctl tenant start <t>` as the service account,
//     and `fleet start --all` once the commit has enabled its boot. In
//     between, and after an abandon: `restore.sh --tenant <t>` again, from the
//     rollback descriptor the row has carried since `adopt --commit`.
//
// The row's `owner` moves at the TAKE, not at the commit, because that is when
// it stops being true of the old account: from the moment the take spawns the
// API, the processes are the service account's, and a row that still named the
// owner would make doctor's `port_owner_mismatch` a lie in the other
// direction. What the commit adds is the BOOT commitment — `desired_boot:
// enabled`, after which `fleet start --all` owns this tenant — and the removal
// of the block that says a way back is still open.
//
// A handover moves no data. The same directories serve the same processes
// under another account, through the ACLs PR-D2 installed — so the risk this
// file is written against is not data loss, it is a tenant that neither
// account can start.
//
// "No ctl job holds the tenant" is not a check in this file: every phase takes
// LockTenant (plus registry and manifest), and those three live in a directory
// BOTH accounts write (jobs.SharedLockDir). A phase that runs while another
// job holds the tenant is refused by the engine with the holder's job id, pid
// and start time read out of the lock file — which is a better answer than
// anything a plan-time probe could give, because it cannot go stale between
// the check and the act.

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
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
		return planHandoverCommit(p)
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
	// A row already at `released` is RE-ENTRANT, not a refusal.
	//
	// That is the state a failed release leaves behind — and the state
	// coconut's first real one did leave behind, with the tenant's own
	// processes still running: the row said `handover`/`released`, the API had
	// been stopped and then restarted by hand, and from there `--release`
	// refused "handover in flight" while `--abandon` refused "a port is still
	// held". The operator had two verbs and could use neither.
	//
	// So a release over such a row RUNS AGAIN: it takes the census again, it
	// re-verifies the instances, and it KEEPS the token it already minted (a
	// take the operator is holding a token for must not be invalidated by a
	// retry). What it still refuses is a handover somebody has already TAKEN —
	// that one belongs to the other account — and one released by a different
	// account, because a release acts on processes only its own may signal.
	reentrant := false
	if h := t.Handover; h != nil {
		switch {
		case h.Phase != registry.HandoverReleased:
			return p.refuse("%s already has a handover in flight (phase %s since %s): take it "+
				"(`ragstack-ctl tenant handover %s --take --token <token>`, as the service account) or abandon it "+
				"(`ragstack-ctl tenant handover %s --abandon`)", t.Name, h.Phase, h.StartedAt, t.Name, t.Name)
		case h.ReleasedBy != account:
			return p.refuse("%s was released by %s at %s and this job is running as %s: re-running a release is the "+
				"RELEASING account's to do — it stops processes only that account can signal. Run it as %s, or "+
				"abandon the handover there", t.Name, h.ReleasedBy, h.StartedAt, account, h.ReleasedBy)
		default:
			reentrant = true
		}
	}
	if t.State != "active" && !(reentrant && t.State == registry.StateHandover) {
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
	// …and, beside it, the OTHER thing about postgres that can only be
	// discovered cheaply now: whether the take will be able to own the data
	// directory, and whether this filesystem has room for the copy that takes.
	// Both questions are the same shape — a take that fails on either fails
	// with the tenant already stopped.
	p.addPGDataOwnershipCheck(legs)
	p.addNoRunningIngest(origin, secretsEnv)
	p.addCensus(legs, origin, secretsEnv, census)
	// The LAST thing that can refuse, and the one that has to run before the
	// API is touched: every store this release will stop has to be findable,
	// in the namespace this release will stop it in, and has to be the process
	// actually holding the row's port. See addReleaseInstanceCheck.
	p.addReleaseInstanceCheck(legs)

	// ---- the row, then the processes. In that order, always.

	p.addReleaseRegistryStep(census, reentrant)
	p.addReleaseAPIStop()
	for _, c := range reverse(legs) {
		if c.Name == "api" || c.Name == "ui" || !c.Managed {
			continue
		}
		p.addReleaseStoreStop(c)
	}

	p.result["phase"] = registry.HandoverReleased
	p.result["released_by"] = account
	if reentrant {
		h := t.Handover
		p.result["reentrant"] = true
		p.warn("%s is already `%s` at phase `%s` (released by %s at %s) and this job RE-RUNS that release: the "+
			"census is taken again and the instances are re-verified. The token is the one that release minted — "+
			"it is NOT rotated, so a `--take` command an operator is already holding stays valid",
			t.Name, registry.StateHandover, h.Phase, h.ReleasedBy, h.StartedAt)
	}
	p.warn("after this job the tenant is DOWN and the gateway answers 502 for it. The next step is the take, as the " +
		"service account: `ragstack-ctl tenant handover " + t.Name + " --take --token <the token this job prints>`")
	// The ORDER matters and is the one an operator gets wrong: `--abandon`
	// refuses while any of the tenant's ports is still held, so the restore
	// cannot come first. Stop what the service account started (if the take
	// ran at all), clear the row, and only then start the tenant again.
	p.warn("if the take cannot be made to work, the way back is, in this order: `ragstack-ctl tenant stop " +
		t.Name + "` as the service account (skip it if the take never ran), then `ragstack-ctl tenant handover " +
		t.Name + " --abandon` here, then `ops/coconut/restore.sh --tenant " + t.Name + "`, which starts the " +
		"tenant exactly as it was started before")
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
func (p *planner) addReleaseRegistryStep(census *[]registry.CensusEntry, reentrant bool) {
	name := p.tenant
	account := p.op.deps.owner()
	apiPort := p.t.Ports.API
	title := "record state: handover and the hand-off token"
	warnings := []string{"this is written BEFORE anything is stopped: from here on the row says the tenant is " +
		"mid-move, which is what makes an interrupted release diagnosable"}
	if reentrant {
		title = "re-record state: handover, keeping the token the first release minted"
		warnings = append(warnings, "this release is a RE-RUN of one that did not finish: the block's token and "+
			"started_at are kept (a `--take` the operator already has stays valid) and the census is replaced "+
			"with the one this job just took")
	}
	p.add(step{
		Kind: "registry", Title: title, Targets: []string{name},
		WouldWrite: []model.WouldWrite{{Path: p.oc.Roots.Registry(), Mode: "0660", Preview: model.NullString("")}},
		Warnings:   warnings,
		Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			at := p.stampRFC3339(sc)
			var token string
			err := p.saveTenant(sc, "handover", func(t *registry.Tenant) error {
				// The token is minted ONCE per handover. A re-run keeps the
				// one in the row — rotating it would silently invalidate the
				// `--take` command the operator copied out of the first job,
				// and the refusal they would then get ("the token does not
				// match") names neither cause nor cure.
				started := at
				if h := t.Handover; h != nil && h.Phase == registry.HandoverReleased && h.Token != "" {
					token, started = h.Token, string(h.StartedAt)
				} else {
					fresh, terr := handoverToken()
					if terr != nil {
						return terr
					}
					token = fresh
				}
				t.State = registry.StateHandover
				t.Handover = &registry.Handover{
					Phase: registry.HandoverReleased, Token: token, StartedAt: started,
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
		// The rollback writes what is TRUE, which is not always `active`.
		//
		// The engine rolls back newest-first, so by the time this runs the
		// steps after it have already undone what they could — and the API
		// stop is not one of them: the ctl cannot start a hand-started uvicorn
		// again (its command line is the operator's). So this step ASKS the
		// host. Nothing on the API port means the release got as far as
		// stopping the tenant, and a row saying `active` over that would be
		// the exact lie the ordering of this plan exists to prevent — it would
		// also make `--abandon` refuse ("there is nothing to abandon") and
		// leave the operator with no verb at all.
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			listening, perr := sc.Ops.Drivers.Proc().Listening(ctx, apiPort)
			if perr != nil {
				// An unreadable LISTEN table is not evidence that the tenant
				// is up. Keep the honest, recoverable shape.
				sc.Logf("the API port could not be probed (%v): the row keeps its handover block", perr)
				listening = false
			}
			if !listening {
				// The tenant is DOWN, so the row this step wrote is still the
				// true one: `state: handover`, phase `released`, the block and
				// its token intact. It is LEFT ALONE — that is what a
				// truthful rollback of a step whose successors could not be
				// undone looks like — and the operator is told the two
				// commands that finish the job.
				//
				// It also keeps every other reader right: a fleet-wide
				// restore.sh skips a `handover` row, `restore.sh --tenant <n>`
				// starts it, and `--abandon` has a block to act on. Writing
				// `active` here (which this step used to do unconditionally)
				// broke all three at once.
				sc.Logf("%s is DOWN: the row keeps `%s` with its handover block, which is what it is. Start it "+
					"with `ops/coconut/restore.sh --tenant %s`, then clear the row with "+
					"`ragstack-ctl tenant handover %s --abandon`", name, registry.StateHandover, name, name)
				return name + " is still `" + registry.StateHandover + "` (it is down): run " +
					"`ops/coconut/restore.sh --tenant " + name + "` then `ragstack-ctl tenant handover " +
					name + " --abandon`", nil
			}
			// Nothing was stopped: the row goes all the way back and the
			// handover never happened.
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
	// The launch the rollback puts back. It is resolved HERE, at plan time,
	// from the same row the instance supervisor's start reads — so a row that
	// cannot be rendered into an argv is a plan-time refusal rather than a
	// discovery made with the tenant already down.
	launch, lerr := p.apiLaunch(port)
	p.addFor("proc", step{
		Kind: "proc", Title: "stop the hand-started API through its pidfile (TERM, then KILL)", Destructive: true,
		Targets: []string{pidfile},
		Warnings: []string{"the ctl signals a pid it has verified is this tenant's (pidfile, cwd and cmdline); it " +
			"never runs pkill (MEMORY #402)",
			"if a later step fails, the rollback STARTS THIS API AGAIN, as this account, from the row — the same " +
				"launch `supervisor: instance` uses. `ops/coconut/restore.sh --tenant " + name + "` remains the " +
				"answer when that start cannot be made"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return stopAPIProcess(ctx, sc, pidfile, worktree, port)
		},
		// The rollback RESTARTS the API.
		//
		// It used to print `restore.sh` and nothing else, on the reasoning
		// that a hand-started uvicorn's command line is the operator's rather
		// than the registry's. That reasoning stopped being true at `adopt
		// --commit`: the row carries the worktree, the bind, the port and the
		// env files, and render.APIArgv turns them into the argv the take
		// would have spawned. What the old rollback actually bought was ten
		// minutes of 502 on the hackathon tenant while a job that had
		// "rolled back" left its API down.
		//
		// It runs as the RELEASING account — this job's account, the one whose
		// process was stopped — so the pid it leaves behind is the same
		// account's as the one it replaced, and the row's `owner` stays true.
		//
		// A failure here is LOGGED and does not fail the rollback: the restore
		// script is still the whole answer, and a rollback that refused would
		// stop the remaining rollbacks from running at all.
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			if lerr != nil {
				sc.Logf("the API cannot be rendered from the registry row (%v); start it again with "+
					"`ops/coconut/restore.sh --tenant %s`", lerr, name)
				return "start it again with `ops/coconut/restore.sh --tenant " + name + "`", nil
			}
			detail, err := startAPIProcess(ctx, sc, launch)
			if err != nil {
				sc.Logf("the API could not be started again (%v); run `ops/coconut/restore.sh --tenant %s`, "+
					"which starts it from the rollback descriptor", err, name)
				return "start it again with `ops/coconut/restore.sh --tenant " + name + "`", nil
			}
			return "the API is up again: " + detail, nil
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

// releaseNamespace is the apptainer instance registry every RELEASE-side call
// acts in: the releasing account's DEFAULT one.
//
// A handover release runs as the tenant's owner, over instances that owner
// started BY HAND — `apptainer instance run` from a shell, or out of
// `restore.sh`, with no APPTAINER_CONFIGDIR set. Those live in
// `$HOME/.apptainer`. The ctl's own calls are namespaced under
// `<CtlStateDir>/apptainer/config` so that the daemon's instances are findable
// from any session (PR-D2), and applying that namespace to the release is what
// made the first real one on coconut report "stopped postgres-hackathon" over
// a postgres that kept serving 24085 for another ten minutes.
//
// Everything else — the take, `tenant start`/`stop`, the fleet verbs — keeps
// jobs.NamespaceCtl: those act on instances the ctl itself started.
const releaseNamespace = jobs.NamespaceAccountDefault

// releaseNamespaceName is how the refusals name it. "$HOME/.apptainer" rather
// than a resolved path on purpose: the operator reproduces the lookup by
// running `apptainer instance list` with nothing exported, and that is the
// command this phrase describes.
const releaseNamespaceName = "the releasing account's own apptainer instance registry " +
	"($HOME/.apptainer — `apptainer instance list` with no APPTAINER_CONFIGDIR set)"

// addReleaseInstanceCheck is the pre-step: EVERY leg this release will stop is
// listed in the namespace it will be stopped in, and is the process holding
// the row's port.
//
// It runs after the census and BEFORE the registry write and the API stop,
// which is the whole point of it. The failure it exists to prevent is not a
// store that will not stop — it is a store the ctl cannot SEE: a lookup in the
// wrong instance registry answers "not running", `Stop`'s idempotency rule
// turns that into a success, and the release walks on to a port-free check
// that fails with the tenant's API already down. On coconut that cost ten
// minutes of 502s, and the job's own rollback could not put any of it back.
//
// Refusing here costs nothing: at this point in the plan the tenant is
// entirely up, the row is untouched, and the operator's next command is
// whichever one the refusal names.
func (p *planner) addReleaseInstanceCheck(legs []component) {
	var checked []component
	for _, c := range legs {
		if c.Name == "api" || c.Name == "ui" || !c.Managed || c.Instance == "" {
			continue
		}
		checked = append(checked, c)
	}
	if len(checked) == 0 {
		p.skip("instance", "check the instances this release will stop",
			"this tenant has no exclusive store instance for the ctl to stop", p.tenant)
		return
	}
	names := make([]string, 0, len(checked))
	for _, c := range checked {
		names = append(names, c.Instance)
	}
	p.addFor("instance", step{
		Kind: "instance", Title: "check that " + strings.Join(names, ", ") + " are this account's and hold their ports",
		Targets:  names,
		WouldRun: []model.WouldRun{{Argv: []string{"/usr/bin/apptainer", "instance", "list", "--json"}}},
		Warnings: []string{"this runs BEFORE the row is written and before the API is stopped: a namespace " +
			"mismatch is refused with the whole tenant still up",
			"the instances are looked for in " + releaseNamespaceName + ", NOT in the control plane's own " +
				"namespace — a release acts on processes the owner started by hand"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			var proved []string
			for _, c := range checked {
				in, up, err := instanceIn(ctx, sc, c.Instance, releaseNamespace)
				if err != nil {
					return "", err
				}
				if !up {
					// Not registered here. The question that decides whether
					// this is a problem is whether the PORT is held: a leg a
					// previous release already stopped is nothing to do, and a
					// port still held by something the ctl cannot find is the
					// outage.
					held, owner, err := portHolder(ctx, sc, c.Port)
					if err != nil {
						return "", err
					}
					if !held {
						sc.Logf("%s is not registered in %s and nothing listens on %d: this leg is already "+
							"released", c.Instance, releaseNamespaceName, c.Port)
						continue
					}
					return "", fmt.Errorf("%w: the instance %s is NOT in %s, and %d is held by %s. A stop by name "+
						"would look in that registry, find nothing, and REPORT SUCCESS — which is how a release "+
						"leaves a tenant's API down and its stores up. Nothing has been stopped. Check "+
						"`apptainer instance list` as %s: if the store is there, this job is looking in the wrong "+
						"registry (do not export APPTAINER_CONFIGDIR); if it is not, the store was started some "+
						"other way and the row's stores.* ports are what to look at",
						jobs.ErrRefused, c.Instance, releaseNamespaceName, c.Port, describeHolder(owner), p.t.Owner)
				}
				if err := instanceHoldsPort(ctx, sc, c.Instance, in.PID, c.Port); err != nil {
					return "", err
				}
				proved = append(proved, fmt.Sprintf("%s (pid %d) holds %d", c.Instance, in.PID, c.Port))
			}
			if len(proved) == 0 {
				return "every leg of this release is already stopped", nil
			}
			return strings.Join(proved, "; "), nil
		},
	})
}

// addReleaseStoreStop stops one exclusive store instance of the CURRENT
// account, in that account's own instance registry, and proves its port is
// free before it reports success.
func (p *planner) addReleaseStoreStop(c component) {
	instance := c.Instance
	if instance == "" {
		p.skip("instance", "stop "+c.Name,
			"this leg has no apptainer instance name recorded, so the ctl does not know what to stop", c.Name)
		return
	}
	st, err := render.StoreArgv(p.t, c.Leg, p.unitConfig())
	restartable := err == nil
	port := c.Port
	ports := legPorts(p.t, c)
	p.addFor("instance", step{
		Kind: "instance", Title: "stop the instance " + instance, Destructive: true, Targets: []string{instance},
		WouldRun: []model.WouldRun{{Argv: []string{"/usr/bin/apptainer", "instance", "stop", instance}}},
		Warnings: []string{"SIGTERM to the instance, which is the graceful shutdown elasticsearch needs",
			"the instance is looked up in " + releaseNamespaceName + " and checked against the row's port before " +
				"it is stopped: a NAME is not identity, and " + instance + " must be the process family holding " +
				strconv.Itoa(port) + " for this step to signal it",
			"an instance this step cannot FIND is not a success: it is a refusal, unless the port is already free. " +
				"After the stop this step waits, up to " + instanceGoneTimeout.String() + ", for the instance to " +
				"leave the table AND for every port of this leg to free"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			// A name is not an identity. `apptainer instance stop qdrant-dev`
			// stops whatever this account happens to have called that — which,
			// on a host where a tenant has been re-provisioned or a name
			// reused, is not necessarily the process serving the port the row
			// names. And a name that is not in the table at all is not a stop
			// with nothing to do: it is a lookup in a registry that does not
			// hold this tenant, which is exactly what the release must never
			// mistake for success (jobs.Instances.Stop).
			in, up, err := instanceIn(ctx, sc, instance, releaseNamespace)
			if err != nil {
				return "", err
			}
			if !up {
				held, owner, err := portHolder(ctx, sc, port)
				if err != nil {
					return "", err
				}
				if held {
					return "", fmt.Errorf("%w: the instance %s is not in %s, but %d is still held by %s. This step "+
						"will not report a stop it did not make; look at `apptainer instance list` as %s",
						jobs.ErrRefused, instance, releaseNamespaceName, port, describeHolder(owner), p.t.Owner)
				}
				sc.Logf("%s is not in %s and %d is free: there is nothing left to stop", instance,
					releaseNamespaceName, port)
				return instance + " was already stopped", nil
			}
			if err := instanceHoldsPort(ctx, sc, instance, in.PID, port); err != nil {
				return "", err
			}
			if err := sc.Checkpoint("instance:" + instance); err != nil {
				return "", err
			}
			if err := sc.Ops.Drivers.Instances().Stop(ctx, instance,
				jobs.StopOptions{Namespace: releaseNamespace}); err != nil {
				return "", err
			}
			// And then the PROOF, because `apptainer instance stop` returning
			// is not it: the take has to bind these ports as another account,
			// and a TERM that elasticsearch is still flushing a translog under
			// has not freed anything yet.
			if err := awaitInstanceGone(ctx, sc, instance, ports); err != nil {
				return "", err
			}
			return "stopped " + instance + " (gone from " + releaseNamespaceName + ", its ports free)", nil
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
			// Back into the SAME registry it was stopped in. An instance
			// restarted into the ctl's namespace would be invisible to the
			// `apptainer instance list` the owner runs next, and to the next
			// release.
			spec := jobs.InstanceSpec{Name: instance, SIF: st.SIF, Binds: st.Binds, Env: st.Env,
				Args: st.Args, ExtraEnv: map[string]string{}, Namespace: releaseNamespace}
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
			up, err := instanceRunning(ctx, sc, instance, releaseNamespace)
			if err != nil {
				return jobs.ReconcileStuck, err
			}
			if up {
				return jobs.ReconcileRedo, nil
			}
			return jobs.ReconcileDone, nil
		},
	})
	// The port-free steps stay, and they stay SEPARATE from the stop: the stop
	// proves the ports of ITS leg free, and these prove it again as their own
	// named steps, which is what an operator reads off `job show` when a take
	// later fails to bind one.
	for _, port := range ports {
		p.addPortFreeStep(port, c.Name)
	}
}

// legPorts are EVERY port a leg binds, not just the one the row calls its own.
//
// qdrant listens on an HTTP port and a gRPC port; elasticsearch on HTTP and
// transport. A release that proved only the first free left the second one
// held — and apptainer's `instance run` on the other account then fails to
// bind it, which is a take that dies on a port nobody checked.
func legPorts(t *registry.Tenant, c component) []int {
	switch c.Leg {
	case render.LegQdrant:
		return nonZeroPorts(t.Ports.QdrantHTTP, t.Ports.QdrantGRPC)
	case render.LegES:
		return nonZeroPorts(t.Ports.ESHTTP, t.Ports.ESTransport)
	case render.LegPostgres:
		return nonZeroPorts(c.Port)
	}
	return nonZeroPorts(c.Port)
}

func nonZeroPorts(ports ...int) []int {
	out := make([]int, 0, len(ports))
	for _, p := range ports {
		if p != 0 {
			out = append(out, p)
		}
	}
	return out
}

// instanceHoldsPort refuses to stop an instance that is not the process family
// serving this tenant's port.
//
// The caller has already established that the instance IS listed, and in which
// namespace, and passes its pid: "not listed" is a different answer with a
// different remedy and is no longer buried in here.
//
// Identity is ANCESTRY, not pid equality. `apptainer instance list` names the
// instance's starter process and the server runs as its child — on coconut,
// instance postgres-hackathon is pid 630746 and the postgres listening on
// 24085 is pid 631059, its direct child. The check this function used to make
// (`owner == pid`) is therefore false of every real instance on this host: it
// passed only because the lookup above it never found one.
//
// Two answers are still fine. A port nothing listens on belongs to a store
// that is starting or has already gone, and the instance is this account's and
// listed, so it is the one to stop; a table that gives the instance no pid is a
// build of apptainer this driver cannot ask about, and a name is all there is.
// The dangerous case is a port held by a process outside the instance's family
// — the name was reused, and stopping it would take down a store this tenant
// does not own.
func instanceHoldsPort(ctx context.Context, sc *jobs.StepContext, instance string, pid, port int) error {
	if port == 0 {
		return nil
	}
	held, owner, err := portHolder(ctx, sc, port)
	if err != nil {
		return err
	}
	switch {
	case !held:
		sc.Logf("nothing listens on %d; %s is this account's and is listed, so it is the instance to stop",
			port, instance)
		return nil
	case pid == 0:
		sc.Logf("the instance table gives %s no pid; stopping it by name", instance)
		return nil
	case owner == 0:
		return fmt.Errorf("%w: %d is held by a process this account cannot attribute — another account's — so it "+
			"is not %s's. Stopping the instance would leave that port held, and the take would fail to bind it. "+
			"Look at the port and at the row's stores.* before going on", jobs.ErrRefused, port, instance)
	}
	// /proc/<pid>/stat is world-readable, so this walk answers even for a pid
	// the account could not otherwise attribute.
	own, err := sc.Ops.Drivers.Proc().Descends(ctx, owner, pid)
	if err != nil {
		return err
	}
	if own {
		return nil
	}
	return fmt.Errorf("%w: %d is held by pid %d, which does not descend from the instance %s (pid %d) — the name "+
		"and the port do not describe the same process. Stopping it would take down something this tenant does "+
		"not own; look at `apptainer instance list` and at the row's stores.* ports before going on",
		jobs.ErrRefused, port, owner, instance, pid)
}

// portHolder is "is this port held, and by whom this account can see".
//
// The two questions are one call site everywhere in this file because the
// answers combine: held with a readable pid is a process to reason about, held
// with pid 0 is another account's, and not held at all is a port the take can
// bind — which is the only one of the three that is good news.
func portHolder(ctx context.Context, sc *jobs.StepContext, port int) (bool, int, error) {
	if port == 0 {
		return false, 0, nil
	}
	proc := sc.Ops.Drivers.Proc()
	listening, err := proc.Listening(ctx, port)
	if err != nil {
		return false, 0, err
	}
	if !listening {
		return false, 0, nil
	}
	pid, _, err := proc.Owner(ctx, port)
	if err != nil {
		return false, 0, err
	}
	return true, pid, nil
}

// describeHolder names the process on a port the way a refusal has to: a pid
// when there is one, and otherwise the fact that there is not.
func describeHolder(pid int) string {
	if pid == 0 {
		return "a process this account cannot attribute (another account's)"
	}
	return "pid " + strconv.Itoa(pid)
}

// instanceGoneTimeout bounds the wait for a stopped instance to actually be
// gone and its ports free.
//
// It is the API stop's 60 s doubled, for the reason the store units carry
// TimeoutStopSec=120: elasticsearch flushes its translog on SIGTERM, and a
// release that gave up before that finished would report a port still held
// over a store that was shutting down correctly.
//
// They are VARIABLES rather than constants for one reason: a test that proves
// the timeout actually fires must not take two minutes to do it. Nothing in
// the ctl writes them.
var instanceGoneTimeout = 120 * time.Second

// instanceGonePoll is how often the two facts are re-read while TERM works.
var instanceGonePoll = 500 * time.Millisecond

// awaitInstanceGone is the release's proof that a stop actually stopped
// something: the instance has left the registry it was stopped in, AND every
// port of its leg is free.
//
// Both, not either. The instance table empties when apptainer reaps the
// starter process, which can happen while the server inside is still holding
// its socket; and a port can free while a wedged instance stays in the table.
// The take binds the PORTS as another account and the next release looks in
// the TABLE, so a release owes the operator both facts.
func awaitInstanceGone(ctx context.Context, sc *jobs.StepContext, instance string, ports []int) error {
	deadline := time.Now().Add(instanceGoneTimeout)
	for {
		up, err := instanceRunning(ctx, sc, instance, releaseNamespace)
		if err != nil {
			return err
		}
		var stillHeld []string
		if !up {
			for _, port := range ports {
				held, owner, err := portHolder(ctx, sc, port)
				if err != nil {
					return err
				}
				if held {
					stillHeld = append(stillHeld, fmt.Sprintf("%d (%s)", port, describeHolder(owner)))
				}
			}
		}
		if !up && len(stillHeld) == 0 {
			return nil
		}
		if time.Now().After(deadline) {
			if up {
				return fmt.Errorf("%w: %s is still in %s %s after its stop. The tenant's API is already down; "+
					"look at the instance, then re-run this release — it is re-entrant", jobs.ErrRefused,
					instance, releaseNamespaceName, instanceGoneTimeout)
			}
			return fmt.Errorf("%w: %s is gone from %s but %s after %s. The other account cannot bind those ports, "+
				"so the take would fail", jobs.ErrRefused, instance, releaseNamespaceName,
				strings.Join(stillHeld, " and ")+" still held", instanceGoneTimeout)
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(instanceGonePoll):
		}
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
	case subtle.ConstantTimeCompare([]byte(token), []byte(h.Token)) != 1:
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
	// The take is the OTHER account's half. Run by the one that released it,
	// every process it starts is still that account's, every port proof still
	// passes, and the row ends up claiming a handover that moved nothing —
	// which is the one way to reach `owner: wilke, supervisor: instance`, a
	// combination the daemon can neither supervise nor undo.
	if account == h.ReleasedBy {
		return p.refuse("%s was released by %s and this job is running as %s too: a take is the OTHER account "+
			"starting the tenant. Run it as the service account (`/rag/bin/ctl-as-svc.sh tenant handover %s "+
			"--take --token <token>`), or abandon the handover if you meant to put the tenant back",
			t.Name, h.ReleasedBy, account, t.Name)
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
		if !c.Managed {
			continue
		}
		if c.Name == "api" {
			p.addPortFreeStep(c.Port, c.Name)
			continue
		}
		for _, port := range legPorts(t, c) {
			p.addPortFreeStep(port, c.Name)
		}
	}

	// The data directory, before ANY store is started and after the ports are
	// proved free. Both halves of that placement are load-bearing: a directory
	// moved under processes the release did not manage to stop is the worst
	// thing this job could do, and a postgres started before the move is the
	// failure the whole step exists to prevent (ops/pgdata.go).
	p.addPGDataMigration(legs, account)

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

	p.addTakeRegistryStep(account)

	p.result["phase"] = registry.HandoverTaken
	p.result["supervisor"] = supervisorInstance
	p.result["owner"] = account
	p.result["next"] = "ragstack-ctl tenant handover " + t.Name + " --commit"
	p.warn("this job FINISHES rather than parking: a take that waited at a cutover would hold this tenant's " +
		"registry lock for the length of the soak, and that lock is the FLEET's — every backup of every other " +
		"tenant would queue behind it. The row carries the state instead (`handover.phase: taken`)")
	p.warn("soak the tenant, then `ragstack-ctl tenant handover " + t.Name + " --commit` as this account. Until " +
		"then the way back is `ragstack-ctl tenant stop " + t.Name + "` here, then `ragstack-ctl tenant handover " +
		t.Name + " --abandon` as " + h.ReleasedBy + ", then `ops/coconut/restore.sh --tenant " + t.Name + "`")
	p.warn("`desired_boot` stays `" + t.DesiredBoot + "` until the commit: an uncommitted handover must not be " +
		"something `fleet start --all` brings back at the next boot")
	p.warn("the shared stores stay " + h.ReleasedBy + "-run: a handover moves the TENANT, not the host's shared " +
		"services")
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

// addTakeRegistryStep is the take's last act: the row now describes the
// processes this job started.
//
// `owner` moves HERE rather than at the commit, and that is the point of the
// step. From the moment the API was spawned, the pid on the tenant's port is
// the service account's; a row that went on naming the previous owner through
// a 48-hour soak would make doctor's `port_owner_mismatch` fire against the
// truth, and would make `ragstack-ctl tenant stop <t>` — the first half of the
// way back — refuse for the wrong reason.
//
// `desired_boot` is deliberately NOT moved: an uncommitted handover must not be
// something `fleet start --all` brings back at the next boot. That is the
// commit's, and it is the whole difference between "running under the ctl" and
// "the ctl's tenant".
func (p *planner) addTakeRegistryStep(account string) {
	name := p.tenant
	pidfile := p.apiPidFile()
	p.add(step{
		Kind: "registry", Title: "record state: active, owner " + account + ", handover.phase: taken",
		Targets:    []string{name},
		WouldWrite: []model.WouldWrite{{Path: p.oc.Roots.Registry(), Mode: "0660", Preview: model.NullString("")}},
		Warnings: []string{"the handover block and its token STAY in the row until a commit or an abandon clears " +
			"them: they are what those two are gated on, and what says a way back is still open",
			"desired_boot is left as it is — `fleet start --all` does not adopt an uncommitted handover"},
		Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			at := p.stampRFC3339(sc)
			previousOwner := ""
			err := p.saveTenant(sc, "handover", func(t *registry.Tenant) error {
				if t.Handover == nil {
					return fmt.Errorf("%w: %s's handover block is gone; something else cleared it while this job "+
						"was running", jobs.ErrRefused, name)
				}
				previousOwner = t.Owner
				t.State = "active"
				t.Owner = account
				t.API.PidFile = pidfile
				t.Handover.Phase = registry.HandoverTaken
				t.Handover.TakenAt = registry.NullString(at)
				t.Handover.TakenBy = registry.NullString(account)
				return nil
			})
			if err != nil {
				return "", err
			}
			if err := sc.Checkpoint("owner-prev:" + previousOwner); err != nil {
				return "", err
			}
			return fmt.Sprintf("%s is active, owned by %s, handover.phase = %s",
				name, account, registry.HandoverTaken), nil
		},
		Rollback: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			was, _ := externalIDValue(sc.Step.ExternalIDs, "owner-prev:")
			err := p.saveTenant(sc, "", func(t *registry.Tenant) error {
				t.State = registry.StateHandover
				if was != "" {
					t.Owner = was
				}
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

// ---------------------------------------------------------------- commit

// planHandoverCommit is the soak's end: the tenant stops being one the ctl is
// trying out and becomes one it owns.
//
// It is an ordinary JOB rather than the continuation of a parked take. The
// take used to park at a cutover, which kept this tenant's locks — including
// the REGISTRY lock, which is the fleet's — for as long as the operator
// soaked. A 48-hour drill on `dev` would have blocked every backup of every
// other tenant behind it. So the take finishes, the ROW carries the state, and
// this op is gated on that row.
//
// What it adds is the boot commitment. `owner` moved at the take (the
// processes were already the service account's); `desired_boot: enabled` is
// what makes `fleet start --all` responsible for this tenant at the next boot,
// and clearing the block is what closes the way back. The rollback descriptor
// is deliberately kept: it is the immutable record of how the tenant ran before
// the ctl had it, and `restore.sh --tenant <n>` still reads it.
func planHandoverCommit(p *planner) error {
	p.need(model.LockTenant, model.LockRegistry, model.LockManifest)
	t := p.t
	account := p.op.deps.owner()
	h := t.Handover
	switch {
	case h == nil:
		return p.refuse("%s has no handover in flight: there is nothing to commit. A committed handover leaves no "+
			"block behind — `ragstack-ctl tenant show %s` says who owns it now", t.Name, t.Name)
	case h.Phase != registry.HandoverTaken:
		return p.refuse("%s's handover is in phase `%s`: only a TAKEN handover can be committed. Take it first "+
			"(`ragstack-ctl tenant handover %s --take --token <token>`, as the service account)",
			t.Name, h.Phase, t.Name)
	case t.Owner != account:
		return p.refuse("%s is owned by %s and this job is running as %s: the commit is the account that TOOK the "+
			"tenant confirming what it is running. Run it as %s", t.Name, t.Owner, account, t.Owner)
	case t.Supervisor != supervisorInstance:
		return p.refuse("%s's supervisor is `%s`, not `%s`: the row does not describe a tenant this control plane "+
			"is supervising, so there is nothing to commit to", t.Name, t.Supervisor, supervisorInstance)
	}

	// A commit over a tenant that is not actually up would enable a boot for
	// something that is down — and the soak it concludes would have concluded
	// nothing.
	origin := fmt.Sprintf("http://127.0.0.1:%d", t.Ports.API)
	port := t.Ports.API
	p.addFor("proc", step{
		Kind: "probe", Title: fmt.Sprintf("check that the tenant is still up on %d and is this account's", port),
		Targets: []string{strconv.Itoa(port)},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			proc := sc.Ops.Drivers.Proc()
			listening, err := proc.Listening(ctx, port)
			if err != nil {
				return "", err
			}
			if !listening {
				return "", fmt.Errorf("%w: nothing is listening on %d: the tenant the take started is not running, "+
					"so a commit would enable a boot for something that is down. Start it "+
					"(`ragstack-ctl tenant start %s`) or abandon the handover", jobs.ErrRefused, port, p.tenant)
			}
			pid, _, err := proc.Owner(ctx, port)
			if err != nil {
				return "", err
			}
			if pid == 0 {
				return "", fmt.Errorf("%w: %d is held by a process this account cannot attribute, so it is not the "+
					"one the take started. Do not commit a tenant this account does not run", jobs.ErrRefused, port)
			}
			return fmt.Sprintf("pid %d holds %d and belongs to this account", pid, port), nil
		},
	})
	p.addFor("tenantapi", step{
		Kind: "probe", Title: "post-check: GET /health before the boot commitment", Targets: []string{origin},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return "health ok", sc.Ops.Drivers.TenantAPI().Health(ctx, origin)
		},
	})

	name := p.tenant
	p.add(step{
		Kind: "registry", Title: "commit: desired_boot enabled, the handover block cleared", Targets: []string{name},
		WouldWrite: []model.WouldWrite{{Path: p.oc.Roots.Registry(), Mode: "0660", Preview: model.NullString("")}},
		Warnings: []string{"after this step the tenant is the control plane's: `fleet start --all` brings it back " +
			"at boot and `ops/coconut/restore.sh` skips it. The rollback_descriptor is kept, untouched",
			"this is the last moment at which `ragstack-ctl tenant handover " + name + " --abandon` was an option"},
		Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			err := p.saveTenant(sc, "handover", func(t *registry.Tenant) error {
				t.DesiredBoot = "enabled"
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
			return name + " is owned by " + account + " and supervised by " + supervisorInstance, nil
		},
	})
	p.result["phase"] = "committed"
	p.result["owner"] = account
	p.result["desired_boot"] = "enabled"
	// The one thing a commit leaves on the disk. The block is about to be
	// cleared, so this is the LAST moment at which the registry can say where
	// the tenant's pre-handover postgres directory is; from here on doctor's
	// `pre_handover_copy_present` is what remembers, because it reads the disk.
	if pd := h.PostgresData; pd != nil {
		p.result["pre_handover_postgres_data"] = pd.PreHandover
		p.warn("the take copied this tenant's postgres data directory (it could not start on one it did not own) "+
			"and the ORIGINAL is still at %s. This commit does not delete it: it is the last copy of the tenant's "+
			"postgres as %s had it, and removing it is a decision for after the soak. `doctor` reports "+
			"`pre_handover_copy_present` until it is gone; delete it as %s (`rm -rf %s`)",
			pd.PreHandover, h.ReleasedBy, h.ReleasedBy, pd.PreHandover)
	}
	p.warn("`ops/coconut/restore.sh` skips this tenant from now on (\"handed over to the control plane\") and " +
		"`ragstack-ctl fleet start --all` — the service account's @reboot line — is what brings it back")
	return nil
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
	// The abandon hands the tenant back to whoever RELEASED it, and that is
	// the account that will start it again. It is `handover.released_by`
	// rather than the row's `owner`, because after a take the row's owner is
	// the service account — that is the take's whole point — and asking for
	// the owner here would mean the service account handing the tenant back to
	// itself.
	releasedBy := h.ReleasedBy
	if releasedBy != account {
		return p.refuse("%s was released by %s and this job is running as %s: `--abandon` hands the tenant back to "+
			"the account that will start it again (`ops/coconut/restore.sh --tenant %s`). Run it as %s",
			t.Name, releasedBy, account, t.Name, releasedBy)
	}

	// EVERY port, not just the API's. After a take the service account is
	// running this tenant's stores as well, and a row that said `manual` over
	// them would orphan them: nothing would ever stop them again. A port this
	// account cannot attribute is the take's, and that one may not survive an
	// abandon.
	//
	// A port held by THIS account's OWN processes is the other case, and it is
	// the one an abandon used to refuse for no reason. On a row still at
	// `released` — a release that failed, or one whose take never ran — the
	// processes on these ports are the ORIGINALS: the tenant the releasing
	// account started by hand, either never stopped or put back with
	// `restore.sh`. `supervisor: manual` is exactly what is true of them.
	// Refusing there left the operator with no verb at all: `--release`
	// refused "handover in flight" (it no longer does — see
	// planHandoverRelease) and `--abandon` refused "a port is still held",
	// which is how the hackathon row sat at `handover` with the tenant serving.
	//
	// After a TAKE the refusal stands whatever the pid says: those processes
	// are the service account's, they are supervised, and `ragstack-ctl tenant
	// stop` is what ends them.
	legs, err := p.legs(nil)
	if err != nil {
		return err
	}
	keepRunning := h.Phase == registry.HandoverReleased
	type abandonPort struct {
		port     int
		what     string
		instance string
	}
	ports := []abandonPort{{port: t.Ports.API, what: "the API"}}
	for _, c := range legs {
		if c.Leg != "" && c.Port != 0 && exclusiveLeg(t, c) {
			ports = append(ports, abandonPort{port: c.Port, what: c.Name, instance: c.Instance})
		}
	}
	for _, pr := range ports {
		port, what, instance := pr.port, pr.what, pr.instance
		title := fmt.Sprintf("check that nothing holds %d (%s) any more", port, what)
		if keepRunning {
			title = fmt.Sprintf("check that %d (%s) is free or held by this account's own process", port, what)
		}
		p.addFor("proc", step{
			Kind: "probe", Title: title, Targets: []string{strconv.Itoa(port)},
			Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
				held, pid, err := portHolder(ctx, sc, port)
				if err != nil {
					return "", err
				}
				if !held {
					return fmt.Sprintf("port %d is free", port), nil
				}
				if pid == 0 {
					return "", fmt.Errorf("%w: %d (%s) is held by a process this account cannot attribute — the "+
						"take's, still running as the other account. Stop it there first "+
						"(`ragstack-ctl tenant stop %s` as %s), then abandon", jobs.ErrRefused, port, what,
						p.tenant, orNone(p.t.Owner))
				}
				if !keepRunning {
					return "", fmt.Errorf("%w: pid %d still holds %d (%s). An abandon records `supervisor: "+
						"manual`, and a row that said that over processes the TAKE started would leave them with "+
						"nothing that stops them. `ragstack-ctl tenant stop %s` as %s first",
						jobs.ErrRefused, pid, port, what, p.tenant, orNone(p.t.Owner))
				}
				// This account's own process, over a handover that was only
				// ever released. A store leg has to be this account's own
				// INSTANCE as well — an abandon that recorded `manual` over a
				// server started some other way would name a leg nothing in
				// this control plane can stop.
				if instance != "" {
					in, up, err := instanceIn(ctx, sc, instance, releaseNamespace)
					if err != nil {
						return "", err
					}
					if !up {
						return "", fmt.Errorf("%w: %d (%s) is held by pid %d, which is this account's, but %s is "+
							"not in %s. An abandon hands the tenant back as a hand-started one and this leg is "+
							"not one this account can stop by name; look at `apptainer instance list` before "+
							"going on", jobs.ErrRefused, port, what, pid, instance, releaseNamespaceName)
					}
					if err := instanceHoldsPort(ctx, sc, instance, in.PID, port); err != nil {
						return "", err
					}
				}
				sc.Logf("%d (%s) is held by pid %d, this account's own: the handover was released and never "+
					"taken, so this is the tenant's ORIGINAL process. Nothing is stopped and nothing needs "+
					"starting — the row is simply put back", port, what, pid)
				return fmt.Sprintf("port %d is held by this account's own pid %d (the original process)", port, pid), nil
			},
		})
	}
	// The data directory goes back BEFORE the row does. An abandon that
	// recorded `manual` and handed the tenant to `restore.sh` while its
	// postgres directory was still the take's copy would start the releasing
	// account's postgres on a directory it does not own — the same refusal in
	// the other direction.
	p.addPGDataSwapBack(h)

	name := p.tenant
	phase := h.Phase
	p.add(step{
		Kind: "registry", Title: "put the row back: supervisor manual, state active, handover cleared",
		Targets:    []string{name},
		WouldWrite: []model.WouldWrite{{Path: p.oc.Roots.Registry(), Mode: "0660", Preview: model.NullString("")}},
		Warnings: []string{"this writes the REGISTRY only: it starts nothing and stops nothing. The row says " +
			"`active` because that is what the tenant is, or is about to be — `ops/coconut/restore.sh --tenant " +
			name + "` starts whatever of it is down"},
		Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			err := p.saveTenant(sc, "handover", func(t *registry.Tenant) error {
				t.Supervisor = supervisorManual
				t.State = "active"
				// The owner goes back to whoever released it. A take moved it
				// to the service account because the processes were that
				// account's; there are none now, and the account about to
				// start them is this one.
				t.Owner = releasedBy
				t.Handover = nil
				return nil
			})
			if err != nil {
				return "", err
			}
			return name + " is hand-started again, owned by " + releasedBy +
				" (the handover in phase " + phase + " is abandoned)", nil
		},
	})
	p.result["phase"] = "abandoned"
	p.result["supervisor"] = supervisorManual
	p.result["owner"] = releasedBy
	p.result["next"] = "ops/coconut/restore.sh --tenant " + name
	if keepRunning {
		p.warn("this handover was released and never taken, so whatever of %s is STILL RUNNING is this account's "+
			"own, original process: the steps above allow that and stop nothing. Run "+
			"`ops/coconut/restore.sh --tenant %s` for whatever is down — it starts only what is not already up — "+
			"and check `ragstack-ctl tenant show %s` afterwards", name, name, name)
	} else {
		p.warn("nothing is running yet: run `ops/coconut/restore.sh --tenant " + name + "` to start the tenant " +
			"from its rollback descriptor, exactly as it was started before the release")
	}
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
