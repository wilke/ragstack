package main

// `tenant handover` and `tenant set-supervisor` — the two-account handover on
// the command line.
//
// The commands here are thin, like every other op command in this binary, with
// two exceptions that are about the PROTOCOL rather than about the verb:
//
//   - `--release` refuses before it starts when the ctl state directory this
//     invocation would use is not writable by this account. It is the one
//     failure that would otherwise appear as a SQLite error from a library
//     three layers down, at the moment an operator is about to stop a tenant;
//     and its remedy — the scratch state dir the wilke-side convention uses —
//     is not something anybody guesses.
//   - `--commit` is not a job at all. The commit belongs to the take, which is
//     parked at its cutover holding this tenant's locks, so this command finds
//     that job and continues it. Submitting `handover --phase commit` as a new
//     job is refused by the planner, with the same instruction spelled out.

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/api"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
)

func handoverUsage() int {
	fmt.Fprintf(stderr, `usage: ragstack-ctl tenant handover <name> --release|--take --token T|--commit|--abandon %s

Handover is a TWO-ACCOUNT protocol, because no single process on this host can
perform it: the service account cannot signal the owner's uvicorn (it cannot
read another account's /proc/<pid>/cwd) and cannot see the owner's apptainer
instances (each account has its own instance registry).

  --release            As the tenant's OWNER. Checks the preparation (loopback
                       bind, a UI that is not a dev server, confirmed store
                       capabilities, a backup, a rollback descriptor, a
                       postgres password the take can read), takes a census of
                       every collection and index, writes `+"`state: handover`"+`
                       with a one-shot token, then stops the API by pidfile and
                       the tenant's own apptainer instances, proving each port
                       free. Prints the token. --direct is implied.

  --take --token T     As the SERVICE ACCOUNT. Ports free, supervisor:
                       instance, the tenant's own stores, a bounded wait for
                       any SHARED store, the API; then /health, deep health,
                       the census checked back and a gateway probe. PARKS at
                       its cutover, holding this tenant's locks, so that the
                       soak happens with nothing else able to touch it.

  --commit             Continues that parked job: owner, desired_boot enabled,
                       the handover block cleared. Run it after the soak.

  --abandon            As the OWNER, after the service account has run
                       `+"`ragstack-ctl tenant stop <name>`"+` (or when the take never
                       happened): the row goes back to supervisor: manual,
                       state: active. Then `+"`ops/coconut/restore.sh --tenant <name>`"+`
                       starts the tenant exactly as it was started before.

The owner-side phases (--release, --abandon) run as jobs in the OWNER's own ctl
state directory, which is not the daemon's:

  CTL_STATE_DIR=/rag/data/ctl-selftest CTL_CONFIG_DIR=/rag/config/ctl-selftest \
    ragstack-ctl tenant handover <name> --release --yes-destructive <name>

--dry-run prints the plan and changes nothing. --yes-destructive <name> is
required to execute any phase.
`, opFlagSummary)
	return exitUsage
}

// cmdTenantHandover is `ragstack-ctl tenant handover <name> --<phase>`.
func cmdTenantHandover(args []string, registryPath, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("tenant handover", flag.ContinueOnError)
	fs.SetOutput(stderr)
	o := addOpFlags(fs, registryPath, ragRoot, jsonOut)
	release := fs.Bool("release", false, "stop the tenant and hand it over (run as its owner)")
	take := fs.Bool("take", false, "start it again under the ctl (run as the service account)")
	commit := fs.Bool("commit", false, "continue the parked take: the tenant becomes the ctl's")
	abandon := fs.Bool("abandon", false, "give the tenant back to its owner (run as the owner)")
	token := fs.String("token", "", "the token the release printed (--take)")
	acceptNoBackup := fs.Bool("accept-no-backup", false, "release although last_backup is null")

	pos, rest := takePositionals(args, 1)
	if err := fs.Parse(rest); err != nil {
		return exitUsage
	}
	pos = append(pos, fs.Args()...)
	if len(pos) != 1 {
		return handoverUsage()
	}
	name := pos[0]

	phase := ""
	for _, c := range []struct {
		on   bool
		name string
	}{{*release, "release"}, {*take, "take"}, {*commit, "commit"}, {*abandon, "abandon"}} {
		if !c.on {
			continue
		}
		if phase != "" {
			return usageErr("tenant handover: --%s and --%s say different things; pass one phase", phase, c.name)
		}
		phase = c.name
	}
	if phase == "" {
		return handoverUsage()
	}
	if *token != "" && phase != "take" {
		return usageErr("tenant handover: --token is the nonce the release printed and belongs to --take, not --%s",
			phase)
	}
	if phase == "take" && *token == "" {
		return usageErr("tenant handover --take needs --token <the token the release printed>. " +
			"`ragstack-ctl job show <release job id>` has it in its result")
	}

	// `--commit` is a continuation of somebody else's job, not a job.
	if phase == "commit" {
		return commitHandover(o, name)
	}

	// The two OWNER-side phases are local by construction: they act on
	// processes only that account can signal, so there is no daemon that could
	// perform them (see contracts/ctl/openapi.yaml's handover.phase).
	if phase == "release" || phase == "abandon" {
		if code := refuseServerFlag(fs, "tenant handover --"+phase, "it acts on processes the daemon's account "+
			"can neither see nor signal; only the tenant's owner can run it"); code != exitOK {
			return code
		}
		*o.direct = true
		if code := requireWritableStateDir(o, phase); code != exitOK {
			return code
		}
	}

	opArgs := map[string]any{"phase": phase}
	if *token != "" {
		opArgs["token"] = *token
	}
	if setFlags(fs)["accept-no-backup"] {
		opArgs["accept_no_backup"] = *acceptNoBackup
	}
	// A release mints the hand-off token into its job RESULT, and the operator
	// needs it in front of them: follow the job, exactly as `tenant restore`
	// follows the one that mints credentials.
	if !*o.dryRun {
		*o.wait = true
	}
	return submitOp(o, tenantOpTarget(name, "handover"), opArgs)
}

// requireWritableStateDir refuses an owner-side phase whose ctl state dir this
// account cannot write, and names the convention that fixes it.
//
// The daemon's `/rag/data/ctl` is owned by the service account: a wilke
// `--direct` job cannot open its jobs.db, and what that looks like without
// this check is a SQLite "attempt to write a readonly database" from three
// layers down, at the moment an operator is about to stop a production tenant.
// The remedy is a scratch state dir the owner owns, and nobody guesses that
// from the error.
func requireWritableStateDir(o *opFlags, phase string) int {
	roots := api.RootsFromEnv(*o.ragRoot)
	dir := roots.CtlStateDir
	if err := os.MkdirAll(dir, 0o770); err != nil {
		return reportStateDir(dir, phase, err)
	}
	probe := filepath.Join(dir, ".ctl-write-probe")
	f, err := os.OpenFile(probe, os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		return reportStateDir(dir, phase, err)
	}
	_ = f.Close()
	_ = os.Remove(probe)
	return exitOK
}

func reportStateDir(dir, phase string, err error) int {
	fmt.Fprintf(stderr, `ragstack-ctl: refused: `+"`tenant handover --%s`"+` runs as this account and cannot write its
job database under %s (%v).

That directory belongs to the control-plane service account. The owner-side
phases of a handover run in the OWNER's own ctl state, against the same real
registry:

  CTL_STATE_DIR=/rag/data/ctl-selftest CTL_CONFIG_DIR=/rag/config/ctl-selftest \
    ragstack-ctl tenant handover <name> --%s …

Set both, or point them at any directory this account owns.
`, phase, dir, err, phase)
	return exitRefused
}

// commitHandover finds the parked take and continues it.
//
// The commit is not a new job: the take is sitting in `awaiting_cutover`
// holding this tenant's locks and its recorded plan, and the last step of that
// plan is the commit. So this resolves the job id — exactly one parked
// handover for this tenant, or a refusal that says what it found — and issues
// the continuation the daemon (or the local engine) already knows how to run.
func commitHandover(o *opFlags, tenant string) int {
	id, code := parkedHandoverJob(o, tenant)
	if code != exitOK {
		return code
	}
	fmt.Fprintf(stderr, "ragstack-ctl: continuing the parked handover job %s for %s\n", id, tenant)
	return cmdJobContinuation("continue", continuationArgs(o, id), *o.registry, *o.ragRoot, *o.asJSON)
}

// continuationArgs rebuilds the flags `job continue` needs out of the ones this
// command was given, so that `--server`, `--direct`, `--api-key-file`, `--yes`
// and `--json` mean the same thing on both.
func continuationArgs(o *opFlags, id string) []string {
	out := []string{id}
	if *o.direct {
		out = append(out, "--direct")
	}
	if *o.server != "" {
		out = append(out, "--server", *o.server)
	}
	if *o.apiKeyFile != "" {
		out = append(out, "--api-key-file", *o.apiKeyFile)
	}
	if *o.yesDestructive != "" {
		out = append(out, "--yes-destructive", *o.yesDestructive)
	} else if *o.yes {
		out = append(out, "--yes")
	}
	if *o.asJSON {
		out = append(out, "--json")
	}
	if *o.ragRoot != "" {
		out = append(out, "--rag-root", *o.ragRoot)
	}
	if *o.registry != "" {
		out = append(out, "--registry", *o.registry)
	}
	return out
}

// parkedHandoverJob is the ONE handover job of this tenant that is waiting at
// its cutover.
//
// Zero and more-than-one are both refusals with the list in them: continuing
// "a" parked job when there are two would be picking one of them for the
// operator, and a control plane does not do that with a cutover.
func parkedHandoverJob(o *opFlags, tenant string) (string, int) {
	list, code := listJobs(o, tenant, model.JobAwaitingCutover)
	if code != exitOK {
		return "", code
	}
	var ids []string
	for _, j := range list {
		if j.Op == "handover" {
			ids = append(ids, j.ID)
		}
	}
	switch len(ids) {
	case 1:
		return ids[0], exitOK
	case 0:
		fmt.Fprintf(stderr, "ragstack-ctl: refused: %s has no handover job parked at its cutover. A commit "+
			"continues the job that ran `--take`; if the take has not run yet, run it "+
			"(`ragstack-ctl tenant handover %s --take --token <token>`), and if it failed, the way back is "+
			"`ragstack-ctl tenant stop %s` here plus `ragstack-ctl tenant handover %s --abandon` as its owner.\n",
			tenant, tenant, tenant, tenant)
		return "", exitRefused
	default:
		fmt.Fprintf(stderr, "ragstack-ctl: refused: %s has %d handover jobs parked at a cutover (%s). Continue the "+
			"one you mean by hand: `ragstack-ctl job show <id>` then `ragstack-ctl job continue <id>`.\n",
			tenant, len(ids), strings.Join(ids, ", "))
		return "", exitRefused
	}
}

// listJobs reads the job list the same way both transports do.
func listJobs(o *opFlags, tenant string, state model.JobState) ([]model.Job, int) {
	if *o.direct {
		eng, err := buildDirectEngine(o)
		if err != nil {
			return nil, failClient(err)
		}
		list, _, err := eng.List(context.Background(), jobs.ListFilter{Tenant: tenant, State: state, Limit: 50})
		if err != nil {
			return nil, directExit(err)
		}
		return list, exitOK
	}
	c, err := o.client()
	if err != nil {
		fmt.Fprintf(stderr, "ragstack-ctl: %v\n", err)
		return nil, exitUsage
	}
	q := url.Values{"tenant": []string{tenant}, "state": []string{string(state)}}
	resp, err := c.get(context.Background(), "/v1/jobs", q)
	if err != nil {
		return nil, failClient(err)
	}
	if resp.Status != http.StatusOK {
		return nil, reportHTTPError(resp)
	}
	var out model.JobsResponse
	if err := json.Unmarshal(resp.Body, &out); err != nil {
		return nil, failClient(fmt.Errorf("the job list is not jobs_response.json: %w", err))
	}
	return out.Jobs, exitOK
}

// ---------------------------------------------------------------- set-supervisor

func setSupervisorUsage() int {
	fmt.Fprintf(stderr, `usage: ragstack-ctl tenant set-supervisor <name> manual|instance %s

Corrects the registry's `+"`supervisor`"+` and NOTHING else: no process is started,
stopped or signalled. It is the repair for a row that disagrees with the host
— a take that wrote `+"`instance`"+` and then failed, an abandon nobody got to run —
and it refuses the two ways of making such a disagreement permanent:

  * moving a row to `+"`instance`"+` while the API port is held by a process this
    account cannot attribute (another account's): the ctl would be claiming it
    supervises an API it can neither signal nor restart.
  * moving a row to `+"`manual`"+` while this account's own apptainer instances are
    running for the tenant: nothing would ever stop them again.

It also refuses while a handover is in flight — that protocol is moving the
same field, and a row edited underneath it strands the tenant between two
accounts. Abandon or commit the handover first.

`+"`systemd`"+` is not offered: PR-D2 postponed that path, and a row pointing at it
would name units no manager on this host can be made to load.
`, opFlagSummary)
	return exitUsage
}

// cmdTenantSetSupervisor is `ragstack-ctl tenant set-supervisor <name> <kind>`.
func cmdTenantSetSupervisor(args []string, registryPath, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("tenant set-supervisor", flag.ContinueOnError)
	fs.SetOutput(stderr)
	o := addOpFlags(fs, registryPath, ragRoot, jsonOut)

	pos, rest := takePositionals(args, 2)
	if err := fs.Parse(rest); err != nil {
		return exitUsage
	}
	pos = append(pos, fs.Args()...)
	if len(pos) != 2 {
		return setSupervisorUsage()
	}
	name, kind := pos[0], pos[1]
	if kind != "manual" && kind != "instance" {
		if kind == "systemd" {
			return usageErr("set-supervisor does not move a row onto `systemd`: PR-D2 postponed that path, and the " +
				"row would name units no user manager on this host can be made to load")
		}
		return usageErr("set-supervisor: %q is not manual or instance", kind)
	}
	if code := refuseServerFlag(fs, "tenant set-supervisor", "it is the repair for a row that disagrees with the "+
		"host, and the account that can see which of the two is wrong is the one sitting in front of it"); code != exitOK {
		return code
	}
	*o.direct = true
	return submitOp(o, tenantOpTarget(name, "set-supervisor"), map[string]any{"supervisor": kind})
}
