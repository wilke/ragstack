package main

// selftest — the control plane proving, on the host it is deployed to, that
// every verb it claims actually works.
//
// It is the acceptance test of the whole control plane, run as an operator
// command rather than as a test binary, because the thing under test is the
// HOST: this apptainer, this systemd user manager, this filesystem, these
// ports. `go test` proves the ctl agrees with itself; only this proves the ctl
// agrees with coconut.
//
// The sequence is one tenant's life, start to finish:
//
//	create-sandbox → ingest a fixture → backup --fence → stop → restore --as
//	→ decommission both → assert the host is clean → sweep
//
// Three rules shape it.
//
// IT TOUCHES NOTHING BUT ITS OWN SANDBOX. Every op it submits names a tenant
// whose name begins `ctltest-`, and a defensive check in front of every submit
// says so again — not because the first check is doubted, but because the cost
// of being wrong is somebody's production tenant and the cost of the check is
// a string comparison. The port block is the deeper guard: `create-sandbox`
// allocates out of 26000–26099, `registry.Allocate` will not hand those out,
// `decommission` needs no verified bundle for one, and the sweep refuses any
// path that is not a quarantined `ctltest-` directory under the two roots.
//
// IT RUNS EVERY STEP AS A REAL JOB. Not a shortcut through the ops package, not
// a driver call with the same effect — `Engine.Submit`, with a plan, locks,
// checkpoints, an audit row and a rollback, exactly as `ragstack-ctl tenant
// create` would. A selftest that took a shorter path than an operator would be
// testing a path no operator takes.
//
// IT DISTINGUISHES A FAILED JOB FROM A FAILED CHECK. A job that fails stops the
// run and leaves the sandbox standing for somebody to look at (exit 4). A
// named check — did Elasticsearch shut down cleanly, did the journal record a
// SIGKILL — is reported PASS or FAIL in the table and does not abort the
// sequence, because the interesting case is the one where the tenant came back
// anyway and the shutdown was ugly.

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"text/tabwriter"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/api"
	"github.com/ragstack/ragstack/internal/ctl/hostfacts"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/ops"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/render"
)

const (
	// selftestStamp is the timestamp in a sandbox tenant's name. It is
	// LOWERCASE where the bundle ids are not: a tenant name is a directory, a
	// gateway path segment, an instance name and a SQL identifier, and
	// paths.ValidateName's grammar is `^[a-z][a-z0-9-]{0,31}$`. A name carrying
	// the usual `20260914T120000Z` would be refused by the validator before the
	// first plan.
	selftestStamp = "20060102t150405z"
	// selftestJobTimeout bounds one job. A create waits for Elasticsearch to
	// open its segments and a backup snapshots every collection, so this is
	// generous; what it is for is the run that is never going to finish.
	selftestJobTimeout = 20 * time.Minute
	// selftestJobPoll is how often the in-process engine is asked.
	selftestJobPoll = 500 * time.Millisecond
	// selftestIngestTimeout bounds the ingest wait (the brief's ten minutes).
	selftestIngestTimeout = 10 * time.Minute
	// selftestIngestPoll is how often /v1/ingest/{id} is polled.
	selftestIngestPoll = 2 * time.Second
	// esLogTail is how much of the Elasticsearch log the graceful-stop check
	// reads. The shutdown lines are the last few; reading the whole file would
	// pull a gigabyte of startup noise into memory to look at its end.
	esLogTail = 64 << 10
	// journalctlBin and systemctlBin are absolute on purpose: this command runs
	// argv, never a shell, and never a program found on PATH.
	journalctlBin = "/usr/bin/journalctl"
	systemctlBin  = "/usr/bin/systemctl"
)

// ---------------------------------------------------------------- results

// stepResult is one job the selftest submitted.
type stepResult struct {
	Name     string
	JobID    string
	State    string
	Duration time.Duration
	Err      error
	// Skipped marks a step the build cannot run yet (`restore --as` before
	// PR-D's own restore lands). It is neither a pass nor a failure: the run
	// continues and the table says which step was not exercised.
	Skipped bool
	Why     string
}

// checkVerdict is a named check's answer. Three states, not two: "the evidence
// is not there" is a different fact from "the evidence says no", and collapsing
// them would make a tenant with no Elasticsearch unit look like an unclean
// shutdown.
type checkVerdict string

const (
	checkPass checkVerdict = "PASS"
	checkFail checkVerdict = "FAIL"
	checkNA   checkVerdict = "n/a"
)

// checkResult is one named check.
type checkResult struct {
	Name    string
	Verdict checkVerdict
	Detail  string
}

// ---------------------------------------------------------------- options

type selftestOptions struct {
	keep        bool
	withGateway bool
	boot        bool
	sweepOnly   bool
	artifact    string
	postgres    string
	ragRoot     string
	fixture     string
	mountPoint  string
	// supervisor is which supervisor the sandbox is created with — and so
	// which half of the control plane this run actually exercises. Empty means
	// CTL_DEFAULT_SUPERVISOR, else the contract's `systemd`: a selftest must
	// prove the path THIS deployment takes, and on coconut that is `instance`.
	supervisor string
}

// selftest is one run. Everything it talks to is a field, so the test builds
// the same object over a fake host and runs the same sequence.
type selftest struct {
	opts  selftestOptions
	roots paths.Roots
	// registryPath is the file the run allocates out of and sweeps rows from.
	registryPath string

	eng   jobs.Engine
	drv   jobs.Drivers
	princ jobs.Principal
	// host answers the boot checklist's questions (linger, the drop-in, the
	// account this process runs as).
	host hostfacts.Host
	// tenantAPI is the ingest client. It is the driver set's, separately named
	// because the selftest is the ONLY caller of the two ingest methods and a
	// test injects a stand-in for them alone.
	tenantAPI jobs.TenantAPI
	// runArgv runs one program by absolute path and returns its combined
	// output. Injected so a test can answer for journalctl and systemctl
	// without either being installed.
	runArgv func(ctx context.Context, prog string, args ...string) ([]byte, error)
	now     func() time.Time
	out     io.Writer

	// startedAt is when this run began; `journalctl --since` uses it so the
	// SIGKILL check reads this run's journal and not last week's.
	startedAt time.Time
	primary   string // ctltest-<stamp>
	restored  string // ctltest-<stamp>-r
	adminKey  string

	// ran is true once the engine has ACCEPTED a job for this run: before
	// that, nothing of the sandbox exists on the host and nothing needs
	// sweeping, however the run ends.
	ran bool

	steps  []stepResult
	checks []checkResult
}

// ---------------------------------------------------------------- command

func selftestUsage() int {
	fmt.Fprint(stderr, `usage: ragstack-ctl selftest [--keep] [--with-gateway] [--artifact ID] [--postgres local]
                             [--fixture PATH] [--rag-root R]
       ragstack-ctl selftest --boot
       ragstack-ctl selftest --sweep

Create a SANDBOX tenant (ctltest-<stamp>, ports 26000-26099), ingest a fixture
into it, take a fenced backup, stop it, restore the bundle into a second sandbox
tenant, quarantine both, and prove the host is clean afterwards. Nothing outside
the sandbox range is read, written, started or stopped.

  --keep            leave the quarantined trees and the registry rows behind
  --with-gateway    publish a gateway generation for the sandbox (and drop it
                    again); without it the sandbox is never routed
  --artifact ID     the artifact to create from (default: the newest prepared)
  --postgres local  give the sandbox its own postgres instead of sqlite state
  --fixture PATH    the document to ingest (default <rag-root>/documents/test_api.md)
  --supervisor S    systemd|instance — how the sandbox is supervised, and so
                    which half of the control plane this run exercises.
                    Default: $CTL_DEFAULT_SUPERVISOR, else systemd. Under
                    instance the post-checks read the instance table and the
                    pidfile instead of systemctl show, and the journal check
                    is n/a (there is no unit to have a journal)
  --boot            run the BOOT CHECKLIST only and exit: linger, the user@
                    drop-in, is-enabled and default.target's dependencies for
                    every tenant whose row says desired_boot enabled
  --sweep           remove the quarantined ctltest-* trees and registry rows a
                    previous run left behind, and do nothing else

There is no --server: a selftest runs the engine IN THIS PROCESS, against the
host it is testing. Pointing it at a daemon would test the daemon's host.

exit: 0 everything green · 3 refused (no prepared artifact, a red doctor, a
      --boot checklist with a FAIL) · 4 a job failed or a check FAILED

A run whose JOB failed stops where it failed and leaves the sandbox in place
for inspection; remove it afterwards with "ragstack-ctl selftest --sweep". A
run whose jobs all succeeded sweeps its sandboxes even when a CHECK failed —
they are quarantined by then, there is nothing left to look at, and there are
only five sandbox blocks. Pass --keep to leave them behind either way.
`)
	return exitUsage
}

func cmdSelftest(args []string, registryPath, ragRoot string) int {
	fs := flag.NewFlagSet("selftest", flag.ContinueOnError)
	fs.SetOutput(stderr)
	fs.Usage = func() { selftestUsage() }
	var (
		keep        = fs.Bool("keep", false, "leave the quarantined trees and registry rows behind")
		withGateway = fs.Bool("with-gateway", false, "publish a gateway generation for the sandbox")
		boot        = fs.Bool("boot", false, "run the boot checklist only")
		sweep       = fs.Bool("sweep", false, "sweep a previous run's leftovers and do nothing else")
		artifact    = fs.String("artifact", "", "the artifact id to create from (default: newest prepared)")
		postgres    = fs.String("postgres", "", "`local` gives the sandbox its own postgres")
		fixture     = fs.String("fixture", "", "the document to ingest")
		supervisor  = fs.String("supervisor", "", "systemd|instance (default $CTL_DEFAULT_SUPERVISOR, else systemd)")
		root        = fs.String("rag-root", ragRoot, "deployment root")
		reg         = fs.String("registry", registryPath, "registry.json path")
		server      = fs.String("server", "", "refused: a selftest runs on the host it tests")
	)
	if err := fs.Parse(args); err != nil {
		return exitUsage
	}
	if fs.NArg() > 0 {
		return usageErr("selftest takes no positional arguments (got %q)", fs.Arg(0))
	}
	if *server != "" {
		// Not a usage error: it is a refusal, and it has a reason. `--direct`
		// is not an option here, it is what a selftest IS — the engine runs in
		// this process, against this host's ports, this host's systemd and this
		// host's filesystem. A run pointed at a daemon would create tenants on
		// whatever host that daemon is on and then check THIS one for the
		// evidence.
		fmt.Fprintf(stderr, "ragstack-ctl: selftest has no --server: it runs the engine in this process, against "+
			"the host it is testing. Run it ON the host (as the ctl account), not against a daemon.\n")
		return exitRefused
	}
	if *boot && *sweep {
		return usageErr("--boot and --sweep are two different runs; pass one")
	}
	sup := *supervisor
	if sup == "" {
		sup = strings.TrimSpace(os.Getenv(api.EnvDefaultSupervisor))
	}
	if sup == "" {
		sup = ops.DefaultSupervisor
	}
	if !ops.KnownSupervisor(sup) {
		return usageErr("--supervisor %q is not systemd or instance", sup)
	}

	s, err := newSelftest(selftestOptions{
		keep: *keep, withGateway: *withGateway, boot: *boot, sweepOnly: *sweep,
		artifact: *artifact, postgres: *postgres, ragRoot: *root, fixture: *fixture,
		supervisor: sup,
	}, *reg)
	if err != nil {
		fmt.Fprintf(stderr, "ragstack-ctl: %v\n", err)
		return exitError
	}
	return s.main(context.Background())
}

// newSelftest builds the run against the real host.
func newSelftest(opts selftestOptions, registryPath string) (*selftest, error) {
	roots := paths.NewRoots(opts.ragRoot, paths.Overrides{})
	// The MOUNT the units wait for is /rag even when the paths are a scratch
	// tree under it: ConditionPathIsMountPoint on a directory inside the mount
	// is false, and a unit conditioned on it never starts and never says why.
	mount := "/rag"
	if roots.RagRoot == mount {
		mount = ""
	}
	opts.mountPoint = mount

	o := &opFlags{
		registry: &registryPath, ragRoot: &opts.ragRoot,
		mountPoint: mount,
	}
	// The flags the engine builder reads. They are values rather than a parsed
	// FlagSet because `selftest` has its own flags: what it needs from opFlags
	// is the engine, not the command line.
	direct, dry, wait, asJSON := true, false, true, false
	empty := ""
	o.direct, o.dryRun, o.wait, o.asJSON = &direct, &dry, &wait, &asJSON
	o.server, o.apiKeyFile = &empty, &empty

	eng, drv, err := buildDirectEngineAndDrivers(o)
	if err != nil {
		return nil, err
	}
	princ, err := directPrincipal()
	if err != nil {
		return nil, err
	}
	return &selftest{
		opts: opts, roots: roots, registryPath: resolveRegistry(registryPath, opts.ragRoot),
		eng: eng, drv: drv, princ: princ,
		host: hostfacts.NewReal(roots), tenantAPI: drv.TenantAPI(),
		runArgv: runArgvCombined, now: time.Now, out: stdout,
	}, nil
}

// runArgvCombined runs one absolute program with an argv and returns what it
// said. No shell, no PATH lookup, no environment beyond this process's: the two
// programs it runs (journalctl, systemctl) are read-only queries about this
// user's own manager.
func runArgvCombined(ctx context.Context, prog string, args ...string) ([]byte, error) {
	if !filepath.IsAbs(prog) {
		return nil, fmt.Errorf("%s is not an absolute program path", prog)
	}
	cmd := exec.CommandContext(ctx, prog, args...)
	return cmd.CombinedOutput()
}

// main is the command: one of the three runs, then the table and the exit code.
func (s *selftest) main(ctx context.Context) int {
	s.startedAt = s.now()
	switch {
	case s.opts.sweepOnly:
		return s.runSweepOnly(ctx)
	case s.opts.boot:
		return s.runBootOnly(ctx)
	}
	err := s.execute(ctx)
	s.report()
	switch {
	// A RED (or unforced yellow) doctor is a refusal like any other: the
	// engine answers 409 `doctor_red` rather than `refused`, and a classifier
	// that only knew ErrRefused reported the documented exit 3 as the exit 4
	// of a failed job — sending an operator to look for a broken step when
	// what happened is that the host is not fit to be operated on.
	case err != nil && (errors.Is(err, jobs.ErrRefused) || errors.Is(err, jobs.ErrDoctorRed)):
		fmt.Fprintf(stderr, "ragstack-ctl: selftest refused: %v\n", err)
		// Only if one exists: a refusal before the create — no artifact, a red
		// doctor on the first job — created no sandbox, and advice to sweep
		// one sends an operator looking for a tenant that is not there.
		s.sayHowToSweep()
		return exitRefused
	case err != nil && len(s.steps) == 0:
		// Nothing was submitted, so nothing is half-done: this is the command
		// failing to start (an unreadable registry, a missing fixture), not a
		// job that failed. Exit 1, and say nothing about sweeping a sandbox
		// that was never created.
		fmt.Fprintf(stderr, "ragstack-ctl: selftest could not start: %v\n", err)
		return exitError
	case err != nil:
		fmt.Fprintf(stderr, "ragstack-ctl: selftest failed: %v\n", err)
		s.sayHowToSweep()
		return exitJobFailed
	case s.failedChecks() > 0:
		// The sandboxes are GONE by now (execute sweeps unless --keep): every
		// job succeeded, so both were decommissioned and quarantined, and a
		// quarantined tree is exactly what the sweep is allowed to remove. The
		// FAIL is in the report above and the exit code is 4 either way.
		fmt.Fprintf(stderr, "ragstack-ctl: %d check(s) FAILED; the jobs themselves all succeeded\n", s.failedChecks())
		s.sayHowToSweep()
		return exitJobFailed
	}
	return exitOK
}

func (s *selftest) sayHowToSweep() {
	if s.primary == "" || !s.ran {
		// No job was ever accepted, so there is no row, no tree and no units.
		// Saying "the sandbox is left in place" anyway is how an operator comes
		// to run the one destructive command in the control plane looking for a
		// leftover that does not exist.
		return
	}
	if s.opts.keep {
		fmt.Fprintf(stderr, "--keep: the sandbox tenants and their trees are left behind; remove them with "+
			"`ragstack-ctl selftest --sweep`\n")
		return
	}
	fmt.Fprintf(stderr, "the sandbox tenants are left in place for inspection; remove them with "+
		"`ragstack-ctl selftest --sweep`\n")
}

func (s *selftest) runSweepOnly(ctx context.Context) int {
	removed, refused, err := s.sweep(ctx)
	for _, r := range refused {
		fmt.Fprintf(stderr, "ragstack-ctl: refused to sweep %s\n", r)
	}
	for _, r := range removed {
		fmt.Fprintf(s.out, "removed %s\n", r)
	}
	if err != nil {
		fmt.Fprintf(stderr, "ragstack-ctl: %v\n", err)
		return exitError
	}
	if len(removed) == 0 {
		fmt.Fprintf(s.out, "nothing to sweep: no quarantined ctltest-* tree and no sandbox row\n")
	}
	return exitOK
}

func (s *selftest) runBootOnly(ctx context.Context) int {
	s.checks = append(s.checks, s.bootChecklist(ctx)...)
	s.report()
	if s.failedChecks() > 0 {
		fmt.Fprintf(stderr, "ragstack-ctl: the boot checklist has %d FAIL(s): this host does not bring its tenants "+
			"back by itself. See docs/runbooks/ctl-deploy.md\n", s.failedChecks())
		return exitRefused
	}
	return exitOK
}

// ---------------------------------------------------------------- sequence

func (s *selftest) execute(ctx context.Context) error {
	artifact, err := s.pickArtifact()
	if err != nil {
		return err
	}
	stamp := s.now().UTC().Format(selftestStamp)
	s.primary, s.restored = "ctltest-"+stamp, "ctltest-"+stamp+"-r"
	fmt.Fprintf(s.out, "selftest: artifact %s, supervisor %s, sandbox %s (restored into %s)\n",
		artifact, s.supervisorKind(), s.primary, s.restored)

	fixture := s.opts.fixture
	if fixture == "" {
		fixture = filepath.Join(s.roots.RagRoot, "documents", "test_api.md")
	}
	if _, err := os.Stat(fixture); err != nil {
		return fmt.Errorf("%w: the ingest fixture %s is not there (%v). Point --fixture at a document under the "+
			"tenant's ingest root", jobs.ErrRefused, fixture, err)
	}

	// ---- create ---------------------------------------------------------
	createArgs := map[string]any{
		"name": s.primary, "artifact_id": artifact,
		"start": true, "gateway": s.opts.withGateway,
	}
	if s.opts.supervisor != "" {
		// Named EXPLICITLY even when it equals the deployment's default: the
		// run's report says which supervisor was proved, and an argument the
		// request carried is the only thing that makes that claim true of the
		// tenant that was actually created.
		createArgs["supervisor"] = s.opts.supervisor
	}
	if s.opts.postgres != "" {
		createArgs["postgres"] = s.opts.postgres
	}
	job, err := s.submit(ctx, "create-sandbox", "create-sandbox", s.primary, createArgs, "")
	if err != nil {
		return err
	}
	// The bootstrap admin key, delivered ONCE. It is held for the length of
	// this run so the ingest can authenticate, and it is never printed.
	if err := s.collectAdminKey(ctx, job.ID); err != nil {
		return err
	}

	// ---- ingest ---------------------------------------------------------
	if err := s.ingest(ctx, fixture); err != nil {
		return err
	}

	// ---- credentials: mint, restart, prove; revoke, restart, prove --------
	if err := s.credentials(ctx); err != nil {
		return err
	}

	// ---- backup --fence --------------------------------------------------
	backup, err := s.submit(ctx, "backup --fence", "backup", s.primary, map[string]any{"fence": true}, "")
	if err != nil {
		return err
	}
	bundle, _ := backup.Result["bundle"].(string)
	if bundle == "" {
		return fmt.Errorf("the backup job %s named no bundle, so there is nothing to restore from", backup.ID)
	}
	fmt.Fprintf(s.out, "selftest: bundle %s\n", bundle)

	// ---- stop, and how Elasticsearch took it ------------------------------
	if _, err := s.submit(ctx, "stop", "stop", s.primary, map[string]any{}, s.primary); err != nil {
		return err
	}
	s.checks = append(s.checks, s.esShutdownChecks(ctx, s.primary)...)

	// ---- restore --as ----------------------------------------------------
	//
	// The restore op is the other half of PR-D and may not be merged into this
	// build yet. Its steps then refuse with `… lands in PR-D`, which is a fact
	// about the build rather than about the host: the run says so and carries
	// on to the quarantine, so the rest of the sequence is still exercised.
	restored := true
	if _, err := s.submit(ctx, "restore --as", "restore", s.primary,
		map[string]any{"from": bundle, "as": s.restored}, s.primary); err != nil {
		if !isLandsInPRD(err) {
			return err
		}
		restored = false
		s.markSkipped("restore --as", "the restore op lands in PR-D and is not in this build")
	}
	if restored {
		s.checks = append(s.checks, s.compareSummaries())
	}

	// ---- decommission both ------------------------------------------------
	names := []string{s.primary}
	if restored {
		names = append(names, s.restored)
	}
	for _, name := range names {
		if _, err := s.submit(ctx, "decommission "+name, "decommission", name, map[string]any{}, name); err != nil {
			return err
		}
	}

	// ---- the host is clean -------------------------------------------------
	s.checks = append(s.checks, s.quarantineChecks(ctx, names)...)

	// The sweep is conditioned on `--keep` and on nothing else.
	//
	// It used to be suppressed by a failed CHECK as well, and that was the
	// wrong rule twice over: a run that gets this far has had every JOB
	// succeed, so both sandboxes are decommissioned and quarantined and there
	// is nothing live to inspect — and the five sandbox blocks are exhausted
	// by three such runs, which is exactly the acceptance ("run it three
	// times"). The FAIL is still in the report and the exit code is still 4.
	// A run whose JOB failed never reaches here: execute returns at the error,
	// the sandbox is left in place, and main says how to remove it.
	if !s.opts.keep {
		removed, refused, err := s.sweep(ctx)
		for _, r := range refused {
			s.checks = append(s.checks, checkResult{Name: "sweep refusal", Verdict: checkFail, Detail: r})
		}
		if err != nil {
			return err
		}
		s.checks = append(s.checks, checkResult{
			Name: "sweep", Verdict: checkPass,
			Detail: fmt.Sprintf("%d quarantined tree(s) and their registry rows removed", len(removed)),
		})
	}
	return nil
}

// credentials is the creds phase: a key minted into the sandbox's ledger, the
// API restarted so the ledger is live, and the tenant DIALLED to prove it —
// then the same key withdrawn and dialled again to prove the 401.
//
// It is here rather than in a unit test because the thing being proved is not
// a rewritten file: it is that the tenant API, restarted by this control
// plane, actually accepts the key the ctl minted and actually refuses the one
// it revoked. Nothing short of a live tenant can answer that, and the sandbox
// is the only live tenant the selftest is allowed to touch.
//
// The proof's credentials are read from the sandbox's own secrets.env by the
// job; this function passes none and prints none.
func (s *selftest) credentials(ctx context.Context) error {
	const label = "selftest"
	mintArgs := map[string]any{"label": label, "role": "user", "restart": true, "prove": true}
	mint, err := s.submit(ctx, "key mint --restart --prove", "key-mint", s.primary, mintArgs, "")
	if err != nil {
		return err
	}
	s.checks = append(s.checks, proofCheck("key mint --prove", mint, []string{"surviving_admin", "minted"}))

	revokeArgs := map[string]any{"id": label, "restart": true, "prove": true}
	revoke, err := s.submit(ctx, "key revoke --restart --prove", "key-revoke", s.primary, revokeArgs, s.primary)
	if err != nil {
		return err
	}
	s.checks = append(s.checks, proofCheck("key revoke --prove", revoke, []string{"surviving_admin", "revoked"}))

	// The ledger is the registry's, not only the file's: a key the ctl minted
	// has to be a key the ctl can find afterwards, which is the whole reason
	// the row exists.
	row, err := s.tenantRow(s.primary)
	if err != nil {
		return err
	}
	for _, k := range row.Keys {
		if k.ID != label {
			continue
		}
		verdict, detail := checkPass, "the ledger row is present and revoked ("+k.Fingerprint+")"
		if k.RevokedAt == "" || k.Effective {
			verdict, detail = checkFail, "the ledger row is still effective after a revoke"
		}
		s.checks = append(s.checks, checkResult{Name: "key ledger", Verdict: verdict, Detail: detail})
		return nil
	}
	s.checks = append(s.checks, checkResult{
		Name: "key ledger", Verdict: checkFail,
		Detail: "the registry holds no keys[] row for " + label + ": a ctl-minted key that cannot be ctl-revoked",
	})
	return nil
}

// proofCheck turns a job's `result.proof` into a check.
//
// It asserts the SHAPE the op promises — one entry per credential, each with
// the status it expected — rather than re-deriving the verdict: the job
// already failed if a credential answered the wrong thing, so what is left to
// check here is that the proof was actually taken.
func proofCheck(name string, job *model.Job, want []string) checkResult {
	proof, _ := job.Result["proof"].(map[string]any)
	if len(proof) == 0 {
		return checkResult{Name: name, Verdict: checkFail, Detail: "the job recorded no proof"}
	}
	var missing []string
	var parts []string
	for _, k := range want {
		row, ok := proof[k].(map[string]any)
		if !ok {
			missing = append(missing, k)
			continue
		}
		parts = append(parts, fmt.Sprintf("%s=%v", k, row["status"]))
	}
	if len(missing) > 0 {
		return checkResult{Name: name, Verdict: checkFail,
			Detail: "the proof has no " + strings.Join(missing, ", ") + " entry"}
	}
	return checkResult{Name: name, Verdict: checkPass, Detail: strings.Join(parts, ", ")}
}

// isLandsInPRD reports whether err is the placeholder refusal of an op whose
// driver half has not merged.
func isLandsInPRD(err error) bool {
	return err != nil && strings.Contains(err.Error(), "lands in PR-D")
}

// markSkipped rewrites the last step's verdict as "not exercised".
func (s *selftest) markSkipped(name, why string) {
	for i := len(s.steps) - 1; i >= 0; i-- {
		if s.steps[i].Name == name {
			s.steps[i].Skipped, s.steps[i].Why, s.steps[i].Err = true, why, nil
			return
		}
	}
}

// ---------------------------------------------------------------- artifacts

// pickArtifact is --artifact, or the newest prepared one.
func (s *selftest) pickArtifact() (string, error) {
	f, err := loadForRead(s.registryPath)
	if err != nil {
		return "", fmt.Errorf("reading the registry %s: %w", s.registryPath, err)
	}
	if s.opts.artifact != "" {
		if _, ok := f.Artifacts[s.opts.artifact]; !ok {
			return "", fmt.Errorf("%w: artifact %q is not prepared on this host (`ragstack-ctl fleet artifact list`)",
				jobs.ErrRefused, s.opts.artifact)
		}
		return s.opts.artifact, nil
	}
	best, bestAt := "", ""
	for id, a := range f.Artifacts {
		// Newest by prepared_at, ties broken by id so the choice is stable:
		// two artifacts prepared in the same second must not make two runs of
		// the same command create from different code.
		if a.PreparedAt > bestAt || (a.PreparedAt == bestAt && id > best) {
			best, bestAt = id, a.PreparedAt
		}
	}
	if best == "" {
		return "", fmt.Errorf("%w: no artifact is prepared on this host, and a selftest creates a tenant from one. "+
			"Prepare one first: `ragstack-ctl --direct fleet artifact prepare --tag <ref>`", jobs.ErrRefused)
	}
	return best, nil
}

// ---------------------------------------------------------------- jobs

// submit runs one op as a job and follows it to a settled state.
//
// The defensive check is the point of the function: a selftest that submitted
// an op against a tenant it did not create would be the one bug in this whole
// command that nobody could undo.
func (s *selftest) submit(ctx context.Context, label, verb, tenant string, args map[string]any, confirm string) (*model.Job, error) {
	if !ops.IsSandboxName(tenant) {
		return nil, fmt.Errorf("%w: selftest refuses to submit %s against %q: it operates on its own sandbox tenants "+
			"and nothing else", jobs.ErrRefused, verb, tenant)
	}
	key, err := randomIdempotencyKey()
	if err != nil {
		return nil, err
	}
	started := s.now()
	res := stepResult{Name: label}
	req := jobs.Request{
		Op: verb, Tenant: tenant, Args: args,
		IdempotencyKey: key, Confirm: confirm,
		Principal: s.princ, Mode: model.WorkerDirect,
	}
	_, job, err := s.eng.Submit(ctx, req)
	if hash := yellowDoctorHash(err); hash != "" {
		// A YELLOW doctor is quoted back rather than ignored.
		//
		// The engine's rule is that yellow runs only when the caller names the
		// hash of the findings they saw — "I accepted these warnings" as a
		// checkable statement. coconut is permanently yellow (drift on an
		// adopted tenant, a worktree outside the mirror), so a selftest that
		// refused to run on a yellow host would never run at all. It therefore
		// does what an operator would: quotes the hash, and RECORDS in the
		// report that it did. A RED doctor is never forced, by the engine or
		// by anything here.
		s.noteYellowDoctor(hash)
		req.ForceWithDoctorDiff = hash
		_, job, err = s.eng.Submit(ctx, req)
	}
	if err != nil {
		res.State, res.Err, res.Duration = "refused", err, s.now().Sub(started)
		s.steps = append(s.steps, res)
		return nil, err
	}
	res.JobID = job.ID
	// A job was ACCEPTED, so from here on something may exist on the host: a
	// row, a tree, units. It is what tells `main` whether advice to sweep a
	// sandbox names anything real — a refusal at the doctor gate or the plan
	// never got this far and created nothing.
	s.ran = true
	job, err = s.follow(ctx, job)
	res.Duration = s.now().Sub(started)
	if job != nil {
		res.State = string(job.State)
	}
	if err == nil && job != nil && job.State != model.JobSucceeded {
		err = fmt.Errorf("job %s (%s) ended %s: %s", job.ID, verb, job.State, jobFailure(job))
	}
	res.Err = err
	s.steps = append(s.steps, res)
	if err != nil {
		return job, err
	}
	return job, nil
}

// yellowDoctorHash is the hash a YELLOW doctor refusal names, or "".
//
// It reads the refusal's `extra` rather than parsing the sentence: the extra is
// the contract's, the sentence is prose, and a selftest that scraped the prose
// would quote the wrong hash the first time somebody rewords it.
func yellowDoctorHash(err error) string {
	if !errors.Is(err, jobs.ErrDoctorRed) {
		return ""
	}
	extra := jobs.ErrorExtra(err)
	if status, _ := extra["status"].(string); status != string(model.StatusYellow) {
		return ""
	}
	hash, _ := extra["doctor_hash"].(string)
	return hash
}

// noteYellowDoctor records, once, that this run accepted a yellow doctor and
// which findings it accepted.
func (s *selftest) noteYellowDoctor(hash string) {
	for _, c := range s.checks {
		if c.Name == "doctor" {
			return
		}
	}
	s.checks = append(s.checks, checkResult{Name: "doctor", Verdict: checkNA,
		Detail: "the op-scoped doctor is YELLOW; this run quoted " + hash + " to proceed (a red doctor is never forced)"})
}

// jobFailure is the reason a job gives for not having succeeded: the first
// failed step's error, which is what an operator needs, rather than the state
// name they can already see.
func jobFailure(j *model.Job) string {
	if j.Error != nil && j.Error.Detail != "" {
		return j.Error.Detail
	}
	for _, st := range j.Steps {
		if st.Error != "" {
			return fmt.Sprintf("step %d (%s): %s", st.N, st.Title, st.Error)
		}
	}
	return "no reason was recorded"
}

// follow polls the in-process engine to a state that does not advance on its
// own. It is followDirect's loop without the printing: the selftest prints a
// table at the end, and a per-step transcript in the middle of it would bury it.
func (s *selftest) follow(ctx context.Context, job *model.Job) (*model.Job, error) {
	deadline := s.now().Add(selftestJobTimeout)
	for {
		if job.State.Terminal() || job.State == model.JobInterrupted || job.State == model.JobAwaitingCutover {
			return job, nil
		}
		if s.now().After(deadline) {
			return job, fmt.Errorf("job %s is still %s after %s", job.ID, job.State, selftestJobTimeout)
		}
		select {
		case <-ctx.Done():
			return job, ctx.Err()
		case <-time.After(selftestJobPoll):
		}
		next, err := s.eng.Get(ctx, job.ID)
		if err != nil {
			return job, err
		}
		job = next
	}
}

// collectAdminKey takes the create job's one-time envelope and keeps the
// bootstrap admin key for the length of this run. Nothing prints it.
func (s *selftest) collectAdminKey(ctx context.Context, jobID string) error {
	resp, err := s.eng.Secrets(ctx, jobID, s.princ)
	if err != nil {
		return fmt.Errorf("collecting %s's credentials: %w", s.primary, err)
	}
	for _, sec := range resp.Secrets {
		if sec.Role == "admin" {
			s.adminKey = sec.Value
			return nil
		}
	}
	return fmt.Errorf("the create job delivered %d secret(s) and none of them is an admin key, so the ingest has "+
		"nothing to authenticate with", len(resp.Secrets))
}

// ---------------------------------------------------------------- ingest

// ingest puts one document into the sandbox and waits for the tenant's own job.
//
// It is not a ctl job: ingest is the TENANT's operation, submitted through its
// API with its own admin key, and the control plane has no verb for it. What
// makes it safe is the driver, which refuses the two ingest calls against any
// origin outside the sandbox port range.
func (s *selftest) ingest(ctx context.Context, fixture string) error {
	t, err := s.tenantRow(s.primary)
	if err != nil {
		return err
	}
	origin := fmt.Sprintf("http://127.0.0.1:%d", t.Ports.API)
	started := s.now()
	res := stepResult{Name: "ingest"}

	// The tenant API ingests only from under its own INGEST_ROOT
	// (<data_dir>/ingest): the fixture is copied there first, and the copy is
	// what is ingested. Handing it the original path — the first thing this
	// selftest tried on coconut — is refused by the API as "outside the
	// permitted ingest root".
	// Read from the OS and written through the Files driver: the fixture is a
	// document on this host (small by construction), and the driver is what
	// keeps the write inside the tenant tree on the real host and inside the
	// fake host in a test.
	staged := filepath.Join(t.DataDir, "ingest", filepath.Base(fixture))
	body, err := os.ReadFile(fixture)
	if err == nil {
		err = s.drv.Files().WriteAtomic(ctx, staged, body, 0o640)
	}
	if err != nil {
		res.State, res.Err, res.Duration = "failed", fmt.Errorf("staging the fixture under the ingest root: %w", err), s.now().Sub(started)
		s.steps = append(s.steps, res)
		return res.Err
	}
	id, err := s.tenantAPI.Ingest(ctx, origin, s.adminKey, staged)
	if err != nil {
		res.State, res.Err, res.Duration = "failed", err, s.now().Sub(started)
		s.steps = append(s.steps, res)
		return err
	}
	res.JobID = id
	state, err := s.waitForIngest(ctx, origin, id)
	res.State, res.Duration = state, s.now().Sub(started)
	if err != nil {
		res.Err = err
	}
	s.steps = append(s.steps, res)
	return err
}

// ingestDone maps the tenant's ingest states onto "finished, and how".
//
// The contract spells the terminal success `completed`; the in-memory fake
// spells it `succeeded`. Both are accepted rather than one being made to move:
// the selftest is asking "is this job over", and a poller that recognised only
// the spelling it expected would wait out its whole timeout against a tenant
// that had already finished.
func ingestDone(state string) (done, ok bool) {
	switch strings.ToLower(state) {
	case "completed", "succeeded", "success":
		return true, true
	case "failed", "error":
		return true, false
	default:
		return false, false
	}
}

func (s *selftest) waitForIngest(ctx context.Context, origin, jobID string) (string, error) {
	deadline := s.now().Add(selftestIngestTimeout)
	last := ""
	for {
		state, err := s.tenantAPI.IngestStatus(ctx, origin, s.adminKey, jobID)
		if err != nil {
			return last, err
		}
		last = state
		if done, ok := ingestDone(state); done {
			if !ok {
				return state, fmt.Errorf("the ingest of the fixture ended %q", state)
			}
			return state, nil
		}
		if s.now().After(deadline) {
			return state, fmt.Errorf("ingest job %s is still %q after %s", jobID, state, selftestIngestTimeout)
		}
		select {
		case <-ctx.Done():
			return state, ctx.Err()
		case <-time.After(selftestIngestPoll):
		}
	}
}

// ---------------------------------------------------------------- checks

// esShutdownChecks are the two halves of "did Elasticsearch stop, or was it
// killed".
//
// They are CHECKS rather than steps because the stop job has already succeeded
// by the time they run: systemd reports a unit stopped whether it exited or was
// killed after TimeoutStopSec, and the difference — a JVM that flushed its
// translog against one that did not — is exactly what a bundle taken afterwards
// depends on. A `n/a` verdict means the evidence is not there to read (no
// Elasticsearch log because this tenant runs no ES unit, no journalctl on this
// host); a FAIL means it is there and says the wrong thing.
func (s *selftest) esShutdownChecks(ctx context.Context, name string) []checkResult {
	tp := paths.TenantPaths(s.roots, name, name)
	logPath := filepath.Join(tp.LogsDir, "es-"+name+".log")
	out := []checkResult{s.esLogCheck(logPath)}

	_, _, esUnit, _, _, _ := render.UnitNames(name)
	if s.instanceMode() {
		// There is no unit, so there is no journal to read. The LOG check
		// above is unchanged and is the one that matters: it is Elasticsearch
		// saying it shut itself down, which is the same evidence either way.
		// `apptainer instance stop` sends SIGTERM exactly as systemd does.
		out = append(out, checkResult{Name: "es journal has no SIGKILL", Verdict: checkNA,
			Detail: "supervisor is `instance`: there is no " + esUnit + " and so no journal; " +
				"the ES log check above is the evidence"})
		return out
	}
	since := s.startedAt.Format("2006-01-02 15:04:05")
	data, err := s.runArgv(ctx, journalctlBin, "--user", "-u", esUnit, "--since", since, "--no-pager")
	switch {
	case err != nil && len(data) == 0:
		out = append(out, checkResult{Name: "es journal has no SIGKILL", Verdict: checkNA,
			Detail: fmt.Sprintf("journalctl could not be read (%v)", err)})
	case strings.Contains(string(data), "SIGKILL"):
		out = append(out, checkResult{Name: "es journal has no SIGKILL", Verdict: checkFail,
			Detail: esUnit + " was killed: systemd reports SIGKILL, so the JVM did not shut down within TimeoutStopSec"})
	default:
		out = append(out, checkResult{Name: "es journal has no SIGKILL", Verdict: checkPass, Detail: esUnit})
	}
	return out
}

// esLogCheck reads the tail of the tenant's Elasticsearch log for a line
// saying the node shut itself down.
func (s *selftest) esLogCheck(logPath string) checkResult {
	name := "es stopped gracefully"
	tail, err := tailFile(logPath, esLogTail)
	if err != nil {
		return checkResult{Name: name, Verdict: checkNA,
			Detail: fmt.Sprintf("%s is not readable (%v)", logPath, err)}
	}
	low := strings.ToLower(tail)
	if strings.Contains(low, "stopped") || strings.Contains(low, "closed") {
		return checkResult{Name: name, Verdict: checkPass, Detail: logPath}
	}
	return checkResult{Name: name, Verdict: checkFail,
		Detail: logPath + " ends without a `stopped`/`closed` line: the node did not log its own shutdown"}
}

// tailFile reads at most n bytes from the END of path.
func tailFile(path string, n int64) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	fi, err := f.Stat()
	if err != nil {
		return "", err
	}
	if fi.Size() > n {
		if _, err := f.Seek(-n, io.SeekEnd); err != nil {
			return "", err
		}
	}
	b, err := io.ReadAll(io.LimitReader(f, n))
	if err != nil {
		return "", err
	}
	return string(b), nil
}

// compareSummaries compares the source and the restored tenant where the two
// are supposed to agree.
//
// The deep comparison — that every collection came back, with every point — is
// the restore op's OWN verification, and repeating it here would be a second
// implementation of it. What this adds is the shape of the row: a restore that
// produced a tenant configured differently from its source is a restore whose
// data is right and whose tenant is not.
func (s *selftest) compareSummaries() checkResult {
	const name = "restored tenant matches the source"
	a, err := s.tenantRow(s.primary)
	if err != nil {
		return checkResult{Name: name, Verdict: checkNA, Detail: err.Error()}
	}
	b, err := s.tenantRow(s.restored)
	if err != nil {
		return checkResult{Name: name, Verdict: checkNA, Detail: err.Error()}
	}
	type field struct{ what, want, got string }
	fields := []field{
		{"artifact_id", string(a.ArtifactID), string(b.ArtifactID)},
		{"ui.mode", a.UI.Mode, b.UI.Mode},
		{"env_layout", a.EnvLayout, b.EnvLayout},
		{"identity.provider", a.Identity.Provider, b.Identity.Provider},
		{"stores.postgres.kind", a.Stores.Postgres.Kind, b.Stores.Postgres.Kind},
		{"stores.qdrant.ownership", a.Stores.Qdrant.Ownership, b.Stores.Qdrant.Ownership},
		{"stores.elasticsearch.ownership", a.Stores.Elasticsearch.Ownership, b.Stores.Elasticsearch.Ownership},
		{"supervisor", a.Supervisor, b.Supervisor},
		{"owner", a.Owner, b.Owner},
	}
	var bad []string
	for _, f := range fields {
		if f.want != f.got {
			bad = append(bad, fmt.Sprintf("%s: %s has %q, %s has %q", f.what, s.primary, f.want, s.restored, f.got))
		}
	}
	if len(bad) > 0 {
		return checkResult{Name: name, Verdict: checkFail, Detail: strings.Join(bad, "; ")}
	}
	return checkResult{Name: name, Verdict: checkPass,
		Detail: fmt.Sprintf("%d fields agree", len(fields))}
}

// quarantineChecks are the post-conditions of a decommission, asked of the
// HOST rather than of the registry: nothing is listening on either sandbox
// block, systemd has never heard of either tenant's units, and the data
// directories are where the quarantine put them.
func (s *selftest) quarantineChecks(ctx context.Context, names []string) []checkResult {
	var out []checkResult
	for _, name := range names {
		t, err := s.tenantRow(name)
		if err != nil {
			out = append(out, checkResult{Name: name + ": registry row", Verdict: checkFail, Detail: err.Error()})
			continue
		}
		out = append(out, s.portsFreeCheck(ctx, name, t.Ports))
		if s.instanceMode() {
			out = append(out, s.instancesGoneCheck(ctx, name, t))
		} else {
			out = append(out, s.unitsGoneCheck(ctx, name))
		}
		out = append(out, s.quarantinedDirCheck(ctx, name))
	}
	return out
}

func (s *selftest) portsFreeCheck(ctx context.Context, name string, block paths.Ports) checkResult {
	check := checkResult{Name: name + ": no LISTEN on its block"}
	var busy []string
	for port := block.Base; port < block.Base+paths.PortStride; port++ {
		listening, err := s.drv.Proc().Listening(ctx, port)
		if err != nil {
			return checkResult{Name: check.Name, Verdict: checkNA, Detail: err.Error()}
		}
		if listening {
			busy = append(busy, strconv.Itoa(port))
		}
	}
	if len(busy) > 0 {
		check.Verdict, check.Detail = checkFail, "still listening on "+strings.Join(busy, ", ")
		return check
	}
	check.Verdict = checkPass
	check.Detail = fmt.Sprintf("%d–%d are free", block.Base, block.Base+paths.PortStride-1)
	return check
}

// unitsGoneCheck reads the EMPTINESS of FragmentPath: `systemctl show` of a
// unit the manager never loaded succeeds and reports nothing, which is exactly
// how "these units no longer exist" is spelled.
func (s *selftest) unitsGoneCheck(ctx context.Context, name string) checkResult {
	check := checkResult{Name: name + ": systemd knows no unit"}
	target, qdrant, es, postgres, api, ui := render.UnitNames(name)
	var known []string
	for _, unit := range []string{target, qdrant, es, postgres, api, ui} {
		info, err := s.drv.Systemd().Show(ctx, unit)
		if err != nil {
			return checkResult{Name: check.Name, Verdict: checkNA, Detail: err.Error()}
		}
		if info.FragmentPath != "" {
			known = append(known, unit+" → "+info.FragmentPath)
		}
	}
	if len(known) > 0 {
		check.Verdict, check.Detail = checkFail, "still loaded: "+strings.Join(known, ", ")
		return check
	}
	check.Verdict, check.Detail = checkPass, "6 units are unknown to the manager"
	return check
}

// supervisorKind is which supervisor this run is proving.
func (s *selftest) supervisorKind() string {
	if s.opts.supervisor == "" {
		return ops.DefaultSupervisor
	}
	return s.opts.supervisor
}

func (s *selftest) instanceMode() bool { return s.supervisorKind() == "instance" }

// instancesGoneCheck is unitsGoneCheck's twin for the other supervisor: after
// a decommission, no apptainer instance of this tenant is running and the API
// pidfile is gone.
//
// The pidfile is half the check because it is the ctl's ONLY record of the
// process it started: a decommissioned tenant that left one behind would hand
// the next reader a pid to signal, and pids are reused.
func (s *selftest) instancesGoneCheck(ctx context.Context, name string, t *registry.Tenant) checkResult {
	check := checkResult{Name: name + ": no instance, no pidfile"}
	list, err := s.drv.Instances().List(ctx)
	if err != nil {
		return checkResult{Name: check.Name, Verdict: checkNA, Detail: err.Error()}
	}
	want := map[string]bool{
		"qdrant-" + t.ManifestName:        true,
		"elasticsearch-" + t.ManifestName: true,
		"postgres-" + t.ManifestName:      true,
	}
	var alive []string
	for _, in := range list {
		if want[in.Name] {
			alive = append(alive, in.Name)
		}
	}
	pidfile := t.API.PidFile
	if pidfile == "" {
		pidfile = paths.TenantPaths(s.roots, name, t.ManifestName).PidFile
	}
	if _, err := s.drv.Files().ReadFile(ctx, pidfile); err == nil {
		alive = append(alive, pidfile)
	}
	if len(alive) > 0 {
		check.Verdict, check.Detail = checkFail, "still there: "+strings.Join(alive, ", ")
		return check
	}
	check.Verdict, check.Detail = checkPass, "3 instance names are unknown to apptainer and "+pidfile+" is gone"
	return check
}

// quarantinedDirCheck asks the Files driver for the tenant root's listing: the
// quarantine renamed the data directory rather than deleting it, and the whole
// promise of `decommission` is that the tree is still there.
func (s *selftest) quarantinedDirCheck(ctx context.Context, name string) checkResult {
	check := checkResult{Name: name + ": quarantined tree present"}
	entries, err := s.drv.Files().ReadDir(ctx, s.roots.DataDir)
	if err != nil {
		return checkResult{Name: check.Name, Verdict: checkNA, Detail: err.Error()}
	}
	for _, e := range entries {
		if strings.HasPrefix(e.Name, name+".quarantined-") {
			check.Verdict, check.Detail = checkPass, filepath.Join(s.roots.DataDir, e.Name)
			return check
		}
	}
	check.Verdict = checkFail
	check.Detail = fmt.Sprintf("no %s.quarantined-* under %s: decommission renames the tree, it never deletes it",
		name, s.roots.DataDir)
	return check
}

// tenantRow reads one row out of the registry file the run is allocating from.
func (s *selftest) tenantRow(name string) (*registry.Tenant, error) {
	f, err := loadForRead(s.registryPath)
	if err != nil {
		return nil, err
	}
	t, ok := f.Tenants[name]
	if !ok {
		return nil, fmt.Errorf("the registry has no row for %s", name)
	}
	return t, nil
}

// ---------------------------------------------------------------- boot

// bootChecklist is the rehearsal of a reboot, asked one question at a time.
//
// It is the plan's boot-persistence story turned into evidence: a user manager
// that survives logout (linger), a manager that waits for /rag and can find the
// ctl's units (the root drop-in), the targets that say they want to come back
// (desired_boot), and default.target actually pulling them. Every one of those
// is something a host can lose silently and only tell you about after a reboot.
func (s *selftest) bootChecklist(ctx context.Context) []checkResult {
	var out []checkResult
	user := s.host.Username()
	uid := os.Getuid()

	if s.host.Linger(user) {
		out = append(out, checkResult{Name: "linger", Verdict: checkPass,
			Detail: "/var/lib/systemd/linger/" + user})
	} else {
		out = append(out, checkResult{Name: "linger", Verdict: checkFail,
			Detail: fmt.Sprintf("%s has no linger: its user manager dies at logout and nothing restarts the tenants "+
				"at boot. Root item: `loginctl enable-linger %s`", user, user)})
	}

	// The drop-in is a ROOT item on the daemon account. Asking about it for an
	// operator's own account would report a FAIL nobody can fix and nobody
	// needs: wilke runs the ctl through --direct, with a live session.
	if user == ctlServiceUser {
		d, err := s.host.UserDropIn(uid)
		switch {
		case err != nil:
			out = append(out, checkResult{Name: "user@ drop-in", Verdict: checkNA, Detail: err.Error()})
		case !d.Present:
			out = append(out, checkResult{Name: "user@ drop-in", Verdict: checkFail,
				Detail: fmt.Sprintf("/etc/systemd/system/user@%d.service.d is empty: the manager neither waits for "+
					"/rag nor searches the ctl's unit directory. See docs/runbooks/ctl-deploy.md", uid)})
		default:
			out = append(out, dropInDetail(d))
		}
	} else {
		out = append(out, checkResult{Name: "user@ drop-in", Verdict: checkNA,
			Detail: fmt.Sprintf("this run is %s, not %s: the drop-in is a root item on the daemon account",
				user, ctlServiceUser)})
	}

	targets, err := s.bootTargets()
	if err != nil {
		out = append(out, checkResult{Name: "desired_boot targets", Verdict: checkNA, Detail: err.Error()})
		return out
	}
	if len(targets) == 0 {
		out = append(out, checkResult{Name: "desired_boot targets", Verdict: checkNA,
			Detail: "no tenant's row says desired_boot enabled, so there is nothing that should come back"})
		return out
	}
	for _, unit := range targets {
		enabled, err := s.drv.Systemd().IsEnabled(ctx, unit)
		switch {
		case err != nil:
			out = append(out, checkResult{Name: unit + ": is-enabled", Verdict: checkNA, Detail: err.Error()})
		case enabled:
			out = append(out, checkResult{Name: unit + ": is-enabled", Verdict: checkPass})
		default:
			out = append(out, checkResult{Name: unit + ": is-enabled", Verdict: checkFail,
				Detail: "the row says desired_boot enabled and systemd says disabled: this tenant will not come back"})
		}
	}
	out = append(out, s.defaultTargetCheck(ctx, targets))
	return out
}

// ctlServiceUser is the account the daemon runs as. The drop-in and the unit
// search path are root items on THAT account.
const ctlServiceUser = "svcbvbrc"

// dropInDetail turns a present drop-in into the two facts that matter.
func dropInDetail(d hostfacts.DropIn) checkResult {
	var missing []string
	found := false
	for _, m := range d.RequiresMountsFor {
		if m == "/rag" {
			found = true
		}
	}
	if !found {
		missing = append(missing, "RequiresMountsFor=/rag (the manager would start before /rag is mounted)")
	}
	if d.SystemdUnitPath == "" {
		missing = append(missing, "Environment=SYSTEMD_UNIT_PATH (the manager cannot find the ctl's units)")
	}
	if len(missing) > 0 {
		return checkResult{Name: "user@ drop-in", Verdict: checkFail,
			Detail: strings.Join(d.Files, ", ") + " is missing " + strings.Join(missing, " and ")}
	}
	return checkResult{Name: "user@ drop-in", Verdict: checkPass, Detail: strings.Join(d.Files, ", ")}
}

// bootTargets is every managed tenant target whose row says desired_boot
// enabled.
func (s *selftest) bootTargets() ([]string, error) {
	f, err := loadForRead(s.registryPath)
	if err != nil {
		return nil, err
	}
	var out []string
	for name, t := range f.Tenants {
		if t.DesiredBoot != "enabled" || t.Supervisor != "systemd" {
			continue
		}
		target, _, _, _, _, _ := render.UnitNames(name)
		out = append(out, target)
	}
	sort.Strings(out)
	return out, nil
}

// defaultTargetCheck asks the manager what default.target actually pulls in.
//
// `is-enabled` says a symlink exists; this says the manager agrees it is in the
// boot graph. The two come apart after a unit file is edited and nothing is
// reloaded, which is the state a host is in for exactly as long as it takes
// somebody to reboot it and find out.
func (s *selftest) defaultTargetCheck(ctx context.Context, targets []string) checkResult {
	const name = "default.target pulls in every enabled tenant"
	data, err := s.runArgv(ctx, systemctlBin, "--user", "list-dependencies", "default.target", "--no-pager", "--plain")
	if err != nil && len(data) == 0 {
		return checkResult{Name: name, Verdict: checkNA,
			Detail: fmt.Sprintf("`systemctl --user list-dependencies` could not be read (%v)", err)}
	}
	text := string(data)
	var missing []string
	for _, t := range targets {
		if !strings.Contains(text, t) {
			missing = append(missing, t)
		}
	}
	if len(missing) > 0 {
		return checkResult{Name: name, Verdict: checkFail,
			Detail: "default.target does not depend on " + strings.Join(missing, ", ")}
	}
	return checkResult{Name: name, Verdict: checkPass, Detail: fmt.Sprintf("%d target(s)", len(targets))}
}

// ---------------------------------------------------------------- sweep

// sweepable is the ONLY thing this command will ever delete: a quarantined
// sandbox tenant's tree, directly under the tenant data root or the tenant
// worktree root.
//
// The pattern is deliberately narrow and anchored at both ends. `rm -rf` driven
// by a glob is how a cleanup step comes to remove a production tenant, and the
// three facts that have to hold together here — the `ctltest-` prefix, the
// `.quarantined-` infix, and a parent that is exactly one of two roots — are
// checked separately so that no single mistake is enough.
var sweepable = regexp.MustCompile(`^ctltest-[0-9a-z-]+\.quarantined-[0-9A-Za-z-]+$`)

// sweepPath validates one directory entry and returns the absolute path to
// remove, or a refusal saying which rule it broke.
//
// It re-resolves the path with EvalSymlinks and re-checks containment
// AFTERWARDS, because the name is not the thing being deleted: a symlink named
// `ctltest-x.quarantined-y` pointing at /rag/data/tenants/asm matches every
// pattern in the world and is not a sandbox.
func sweepPath(root, name string, live map[string]bool) (string, error) {
	switch {
	case sweepable.MatchString(name):
	case orphanSandbox.MatchString(name) && !live[name]:
		// An orphan: the registry has no row of this name, so nothing can be
		// running under it and nothing can bring it back.
	case orphanSandbox.MatchString(name) && live[name]:
		return "", fmt.Errorf("%q still has a registry row: decommission it first", name)
	default:
		return "", fmt.Errorf("%q is not a quarantined sandbox tree (want %s)", name, sweepable)
	}
	candidate := filepath.Join(root, name)
	if _, err := paths.SafePath(root, candidate); err != nil {
		return "", err
	}
	resolved, err := filepath.EvalSymlinks(candidate)
	if err != nil {
		return "", err
	}
	if _, err := paths.SafePath(root, resolved); err != nil {
		return "", fmt.Errorf("%s resolves to %s, which is outside %s: refusing to remove it", candidate, resolved, root)
	}
	if filepath.Dir(resolved) != filepath.Clean(root) {
		return "", fmt.Errorf("%s resolves to %s, which is not directly under %s", candidate, resolved, root)
	}
	if base := filepath.Base(resolved); !sweepable.MatchString(base) && !(orphanSandbox.MatchString(base) && !live[base]) {
		return "", fmt.Errorf("%s resolves to %s, whose name is not a sweepable sandbox tree", candidate, resolved)
	}
	return resolved, nil
}

// sweep removes the quarantined sandbox trees under the two roots and the
// sandbox rows from the registry. It is the ONLY deletion in v1, which is why
// it is the most defensive function in the package.
func (s *selftest) sweep(ctx context.Context) (removed, refused []string, err error) {
	// The registry rows come first: a tree whose row is gone is an orphan a
	// rolled-back create left behind (create's directory step has no
	// rollback), and it is only knowable as one once the rows are settled.
	rows, rowRefusals, rerr := s.sweepRows()
	refused = append(refused, rowRefusals...)
	removed = append(removed, rows...)
	if rerr != nil {
		return removed, refused, rerr
	}
	live, lerr := s.sandboxRows()
	if lerr != nil {
		return removed, refused, lerr
	}
	for _, root := range []string{s.roots.DataDir, s.roots.ReposDir, s.roots.BackupsDir} {
		entries, rerr := os.ReadDir(root)
		if rerr != nil {
			if os.IsNotExist(rerr) {
				continue
			}
			return removed, refused, rerr
		}
		for _, e := range entries {
			if !strings.HasPrefix(e.Name(), "ctltest-") {
				// Not a candidate at all. Saying nothing about it is the point:
				// a sweep that listed every tenant it declined to delete would
				// bury the one line that matters.
				continue
			}
			path, perr := sweepPath(root, e.Name(), live)
			if perr != nil {
				refused = append(refused, perr.Error())
				continue
			}
			if rmErr := os.RemoveAll(path); rmErr != nil {
				return removed, refused, rmErr
			}
			removed = append(removed, path)
		}
	}
	_ = ctx
	return removed, refused, nil
}

// sandboxRows is the set of sandbox names that still have a registry row.
func (s *selftest) sandboxRows() (map[string]bool, error) {
	f, err := loadForRead(s.registryPath)
	if err != nil {
		return nil, err
	}
	out := map[string]bool{}
	for name := range f.Tenants {
		if ops.IsSandboxName(name) {
			out[name] = true
		}
	}
	return out, nil
}

// orphanSandbox is a sandbox tree no registry row claims: either a bare
// sandbox name — no infix at all — or the `.failed-<ts>` tree a rolled-back
// create or restore renames aside. In both cases the row is gone, the units are
// gone and only the directory tree remains. Such a tree is sweepable when NO
// registry row of that name exists; with a row it is a tenant, and the sweep
// refuses it. (A `.failed-` name can never match a row: registry names have no
// dot in them.)
var orphanSandbox = regexp.MustCompile(`^ctltest-[0-9a-z-]+(\.failed-[0-9A-Za-z-]+)?$`)

// sweepRows deletes the sandbox tenants' registry rows.
//
// It is the one place in the control plane that removes a row, and it removes
// only rows whose PORT BLOCK is a sandbox — the name is checked too, but the
// block is the authority everywhere else and it is the authority here. A row
// that is named like a sandbox and sits on a production block is refused and
// reported, because that combination should not exist and quietly deleting it
// would destroy the evidence of how it came to.
func (s *selftest) sweepRows() (removed, refused []string, err error) {
	f, err := loadForRead(s.registryPath)
	if err != nil {
		return nil, nil, err
	}
	var names []string
	for name := range f.Tenants {
		names = append(names, name)
	}
	sort.Strings(names)
	var doomed []string
	for _, name := range names {
		if !ops.IsSandboxName(name) {
			continue
		}
		if !paths.IsSelftestBlock(f.Tenants[name].Ports.Base) {
			refused = append(refused, fmt.Sprintf("%s is named like a sandbox but sits on port block %d, which is "+
				"not in %d–%d: refusing to delete its row", name, f.Tenants[name].Ports.Base,
				paths.SelftestBase, paths.SelftestEnd))
			continue
		}
		if st := f.Tenants[name].State; st != "quarantined" {
			// A row that is not quarantined belongs to a sandbox that is, or
			// may be, still running. The sweep once deleted the row of a live
			// sandbox on coconut and left its units and tree orphaned; the row
			// is what `decommission` needs to stop them, so it stays until
			// decommission has run.
			refused = append(refused, fmt.Sprintf("%s is %s, not quarantined: decommission it first "+
				"(`ragstack-ctl tenant decommission %s --direct --yes-destructive %s`), then sweep", name, st, name, name))
			continue
		}
		doomed = append(doomed, name)
	}
	if len(doomed) == 0 {
		return nil, refused, nil
	}
	for _, name := range doomed {
		delete(f.Tenants, name)
		f.DisplayOrder = withoutName(f.DisplayOrder, name)
		removed = append(removed, "registry row "+name)
	}
	if err := registry.Save(s.registryPath, f, "ragstack-ctl selftest sweep"); err != nil {
		return nil, refused, err
	}
	return removed, refused, nil
}

func withoutName(ss []string, name string) []string {
	out := make([]string, 0, len(ss))
	for _, s := range ss {
		if s != name {
			out = append(out, s)
		}
	}
	return out
}

// ---------------------------------------------------------------- report

func (s *selftest) failedChecks() int {
	n := 0
	for _, c := range s.checks {
		if c.Verdict == checkFail {
			n++
		}
	}
	return n
}

// report prints the two tables: the jobs, and the named checks.
func (s *selftest) report() {
	if len(s.steps) > 0 {
		fmt.Fprintf(s.out, "\nstep                  job                         state       duration\n")
		w := tabwriter.NewWriter(s.out, 0, 0, 2, ' ', 0)
		for _, st := range s.steps {
			state := st.State
			if st.Skipped {
				state = "skipped"
			}
			fmt.Fprintf(w, "%s\t%s\t%s\t%s\n", st.Name, orNone(st.JobID), state, st.Duration.Round(time.Millisecond))
			if st.Why != "" {
				fmt.Fprintf(w, "  \t\t\t%s\n", st.Why)
			}
			if st.Err != nil {
				fmt.Fprintf(w, "  \t\t\t%s\n", st.Err)
			}
		}
		_ = w.Flush()
	}
	if len(s.checks) > 0 {
		fmt.Fprintf(s.out, "\ncheck                                          verdict\n")
		w := tabwriter.NewWriter(s.out, 0, 0, 2, ' ', 0)
		for _, c := range s.checks {
			fmt.Fprintf(w, "%s\t%s\t%s\n", c.Name, c.Verdict, c.Detail)
		}
		_ = w.Flush()
	}
	fmt.Fprintln(s.out)
}
