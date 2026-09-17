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
//   - `--commit` and `--abandon` are ordinary jobs gated on the ROW, not
//     continuations of the take. The take used to park at a cutover, which held
//     this tenant's locks — the registry lock among them, and that one is the
//     fleet's — for the length of the soak: a 48-hour drill on `dev` would have
//     blocked every backup of every other tenant behind it.

import (
	"flag"
	"fmt"
	"os"
	"path/filepath"

	"github.com/ragstack/ragstack/internal/ctl/api"
)

func handoverUsage() int {
	fmt.Fprintf(stderr, `usage: ragstack-ctl tenant handover <name> --release|--take --token T|--commit|--abandon %s

Handover is a TWO-ACCOUNT protocol, because no single process on this host can
perform it: the service account cannot signal the owner's uvicorn (it cannot
read another account's /proc/<pid>/cwd) and cannot see the owner's apptainer
instances (an instance registry is per account AND per APPTAINER_CONFIGDIR).

--release and --abandon therefore act in the RELEASING ACCOUNT's own apptainer
instance registry ($HOME/.apptainer) rather than the control plane's: the
tenant they act on was started by hand. Do NOT export APPTAINER_CONFIGDIR when
running them — a stop aimed at the wrong registry finds nothing, and "nothing
to stop" is a SUCCESS as far as apptainer is concerned. The release checks this
before it stops anything and refuses by name if the two disagree.

  --release            As the tenant's OWNER. Checks the preparation (loopback
                       bind, a UI that is not a dev server, confirmed store
                       capabilities, a backup, a rollback descriptor, a
                       postgres password the take can read), takes a census of
                       every collection and index, proves every store instance
                       is in THIS account's registry and holds the row's port,
                       writes `+"`state: handover`"+` with a one-shot token, then stops
                       the API by pidfile and the tenant's own apptainer
                       instances, waiting for each to leave the instance table
                       and free its ports. Prints the token. --direct is
                       implied.

                       A tenant with its own postgres is also DUMPED here, in
                       the one window there is for it — the API already
                       stopped, postgres not yet — because the taking account
                       can neither own nor read a cluster directory (postgres
                       compares st_uid with its own uid, and a POSIX ACL's
                       named-user entry is filtered by the mask, which IS the
                       group mode bits a PGDATA must not have). The dump, its
                       sha256, every table's exact row count and the cluster's
                       encoding and locales go into the row.

                       --accept-extra-databases releases a tenant whose cluster
                       holds a database or a login role BESIDE its own. A
                       single-database dump does not carry them; they stay in
                       the pre-handover cluster, and the row records what was
                       left behind.

                       RE-ENTRANT: run it again over a row left at
                       `+"`handover.phase: released`"+` — it re-censuses, re-verifies
                       and keeps the token the first release minted. If a step
                       fails, the rollback starts the API and the instances
                       again, as this account, from the row.

  --take --token T     As the SERVICE ACCOUNT. Ports free, supervisor:
                       instance, the tenant's own stores, a bounded wait for
                       any SHARED store, the API; then /health, deep health,
                       the census checked back and a gateway probe. It records
                       `+"`owner`"+` (the processes are this account's now) and
                       `+"`handover.phase: taken`"+`, and FINISHES — it does not park.
                       A take that waited at a cutover would hold this tenant's
                       registry lock for the whole soak, and that lock is the
                       fleet's.

  --commit             As the SERVICE ACCOUNT, after the soak. Gated on the
                       row's `+"`handover.phase: taken`"+`: it checks the tenant is
                       still up and this account's, then sets desired_boot:
                       enabled and clears the handover block. That is the boot
                       commitment — from here `+"`fleet start --all`"+` owns it.

  --abandon            As the account that RELEASED it, after the service
                       account has run `+"`ragstack-ctl tenant stop <name>`"+` (or when
                       the take never happened): the row goes back to
                       supervisor: manual, state: active, owner: the releasing
                       account. Every port of the tenant must be free OR held
                       by this account's own process — over a handover that was
                       never taken those are the ORIGINALS and nothing needs
                       stopping. A port held by a process this account cannot
                       attribute is the take's and is refused. Then
                       `+"`ops/coconut/restore.sh --tenant <name>`"+` starts whatever of
                       the tenant is down.

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
	commit := fs.Bool("commit", false, "after the soak: desired_boot enabled and the handover block cleared "+
		"(run as the account that took it)")
	abandon := fs.Bool("abandon", false, "give the tenant back to its owner (run as the owner)")
	token := fs.String("token", "", "the token the release printed (--take)")
	acceptNoBackup := fs.Bool("accept-no-backup", false, "release although last_backup is null")
	acceptExtraDBs := fs.Bool("accept-extra-databases", false, "release although this tenant's postgres cluster "+
		"holds a database or a login role beside the tenant's own (they stay in the pre-handover cluster)")

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
	set := setFlags(fs)
	if set["accept-no-backup"] {
		opArgs["accept_no_backup"] = *acceptNoBackup
	}
	if set["accept-extra-databases"] {
		opArgs["accept_extra_databases"] = *acceptExtraDBs
	}
	// A release mints the hand-off token into its job RESULT, and the operator
	// needs it in front of them: follow the job, exactly as `tenant restore`
	// follows the one that mints credentials. The other phases are followed
	// too — every one of them ends in a registry write whose outcome is the
	// thing the operator is waiting to see.
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

// ---------------------------------------------------------------- set-supervisor

func setSupervisorUsage() int {
	fmt.Fprintf(stderr, `usage: ragstack-ctl tenant set-supervisor <name> manual|instance [--desired-boot enabled|disabled] %s

Corrects the registry's `+"`supervisor`"+` (and, with --desired-boot, the tenant's
boot intent) and NOTHING else: no process is started, stopped or signalled. It
is the repair for a row that disagrees with the host — a take that wrote
`+"`instance`"+` and then failed, an abandon nobody got to run, a `+"`--readopt`"+` that
dropped a handover block and the boot intent inside it — and it refuses the two
ways of making such a disagreement permanent:

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

--desired-boot writes `+"`desired_boot`"+`, which is what the @reboot hook
(`+"`fleet start --all`"+`) reads to decide whether to start this tenant at all.
Absent, the recorded value is left alone.
`, opFlagSummary)
	return exitUsage
}

// cmdTenantSetSupervisor is `ragstack-ctl tenant set-supervisor <name> <kind>`.
func cmdTenantSetSupervisor(args []string, registryPath, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("tenant set-supervisor", flag.ContinueOnError)
	fs.SetOutput(stderr)
	boot := fs.String("desired-boot", "",
		"also record the tenant's boot intent (enabled|disabled): whether the @reboot hook `fleet start --all` "+
			"starts it. Absent leaves the recorded value alone")
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
	if *boot != "" && *boot != "enabled" && *boot != "disabled" {
		return usageErr("set-supervisor: --desired-boot %q is not enabled or disabled", *boot)
	}
	if code := refuseServerFlag(fs, "tenant set-supervisor", "it is the repair for a row that disagrees with the "+
		"host, and the account that can see which of the two is wrong is the one sitting in front of it"); code != exitOK {
		return code
	}
	*o.direct = true
	opArgs := map[string]any{"supervisor": kind}
	// Absent means "no opinion", so the key is omitted rather than sent empty:
	// the arg schema's enum would reject "" and, more to the point, a row's
	// boot intent must not be rewritten by a command that never named it.
	if *boot != "" {
		opArgs["desired_boot"] = *boot
	}
	return submitOp(o, tenantOpTarget(name, "set-supervisor"), opArgs)
}
