package main

// `fleet start --all`, `fleet stop --all` and `fleet enable-boot` — the three
// commands a host with no user manager needs in order to come back from a
// reboot (plan PR-D2, "Fleet boot").
//
// What they are NOT is a fleet-scoped OPERATION. There is no `fleet-start`
// verb, no route, no plan covering five tenants and no lock held across them.
// Each tenant is its own job, submitted through the same path `ragstack-ctl
// tenant start` uses, planned against the same registry and taking the same
// per-tenant locks — so a fleet start is exactly five tenant starts an
// operator would otherwise have typed, in display order, and a tenant that
// fails is one failed job rather than a half-applied fleet plan.
//
// Two rules the loop keeps:
//
//   - It CONTINUES past a failure. A fleet start that stopped at the first
//     broken tenant would leave the four behind it down for a reason that has
//     nothing to do with them. Every failure is printed, and the run exits 4
//     at the end.
//   - Every run is a NEW run. The derived idempotency key would otherwise
//     make the second `fleet start --all` a replay of the first — which is
//     the right answer for a retried command and precisely the wrong one for
//     the periodic run that IS this deployment's watchdog: a tenant that died
//     since the last sweep has to be started, not reported as already done.
//     So each run mints one id and keys its tenants off it.

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/api"
	"github.com/ragstack/ragstack/internal/ctl/doctor"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/ops"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// bootMarker is the comment that identifies the ONE crontab line this ctl
// owns. Every other line in the account's crontab is somebody else's — the
// gateway's own `@reboot start-proxy.sh` lives there too — and is copied
// through untouched.
const bootMarker = "# ragstack-ctl boot"

// bootCommand is the line's command half. Two commands, because the daemon
// has to be up before a --direct run would contend with it, and because
// `ctl-daemon.sh` stays the launcher: putting a daemonize mode in the ctl
// would give this host two ways to start the same process.
const bootCommand = "/rag/bin/ctl-daemon.sh start && /rag/bin/ragstack-ctl fleet start --all --direct"

// bootLine is the whole line, marker included.
func bootLine() string { return "@reboot " + bootCommand + "  " + bootMarker }

func fleetOpsUsage() int {
	fmt.Fprintf(stderr, `usage: ragstack-ctl fleet start --all [--direct] %s
       ragstack-ctl fleet stop --all --yes-destructive all [--direct] %s
       ragstack-ctl fleet enable-boot --cron|--no-cron [--dry-run] [--direct]

  start --all   start every tenant whose row says desired_boot enabled, in
                display order. Idempotent: a leg that is already running is a
                no-op in both supervisors, so a periodic run of this is the
                watchdog instance mode does not otherwise have.
  stop  --all   stop every tenant the ctl supervises, in REVERSE display
                order. Destructive: it needs --yes-destructive all once for
                the fleet, and each tenant's own plan is then confirmed with
                that tenant's name.
  enable-boot   install (or remove) the single marked @reboot line in THIS
                account's crontab. No other line is read back out, rewritten
                or removed, and the ctl refuses to act when its own marker
                appears more than once.

  supervisor: manual rows are skipped by start and stop, with the reason
  printed: the ctl did not start those tenants and will not start them
  (`+"`ragstack-ctl tenant handover <name>`"+`).

exit: 0 everything succeeded · 3 the request was refused · 4 at least one
      tenant's job failed (the others were still attempted)
`, opFlagSummary, opFlagSummary)
	return exitUsage
}

// cmdFleetOps dispatches the three.
func cmdFleetOps(verb string, args []string, registryPath, ragRoot string, jsonOut bool) int {
	switch verb {
	case "start", "stop":
		return cmdFleetLifecycle(verb, args, registryPath, ragRoot, jsonOut)
	case "enable-boot":
		return cmdFleetEnableBoot(args, registryPath, ragRoot, jsonOut)
	}
	return fleetOpsUsage()
}

// cmdFleetLifecycle is `fleet start --all` / `fleet stop --all`.
func cmdFleetLifecycle(verb string, args []string, registryPath, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("fleet "+verb, flag.ContinueOnError)
	fs.SetOutput(stderr)
	o := addOpFlags(fs, registryPath, ragRoot, jsonOut)
	all := fs.Bool("all", false, "act on every tenant (required: there is no default scope)")
	if err := fs.Parse(args); err != nil {
		return exitUsage
	}
	if fs.NArg() > 0 {
		return usageErr("fleet %s takes no tenant names: it acts on the whole fleet (--all)", verb)
	}
	if !*all {
		// --all is mandatory rather than implied. "fleet stop" with a typo
		// after it must never be read as "stop everything".
		return usageErr("fleet %s needs --all: it has no other scope", verb)
	}
	// A fleet stop is destructive in the plan's sense for every row it
	// touches, so it is confirmed ONCE, here, by typing what it acts on — the
	// same rule a fleet-scoped plan follows (model.Plan's confirm_value is
	// "gateway" for the gateway ops). Each tenant's own plan still demands its
	// own name, which the loop supplies below; what the operator is confirming
	// here is the SCOPE.
	if verb == "stop" && !*o.dryRun && o.confirm() != "all" {
		fmt.Fprintf(stderr, "ragstack-ctl: `fleet stop --all` stops every tenant this ctl supervises. "+
			"Re-run with --yes-destructive all (or --dry-run to see which rows it would act on).\n")
		return exitRefused
	}
	f, err := loadForRead(resolveRegistry(*o.registry, *o.ragRoot))
	if err != nil {
		return fail(err)
	}

	// The engine is built ONCE for a --direct run and handed to every
	// submission, so a fleet of five opens one jobs.db rather than five.
	if *o.direct {
		eng, err := buildDirectEngine(o)
		if err != nil {
			return failClient(err)
		}
		o.engine = eng
	}
	// --wait, whether or not it was typed: the exit code of a fleet run is
	// the outcome of its tenants' jobs, and a loop that submitted five jobs
	// and exited would report nothing about any of them.
	if !*o.dryRun {
		*o.wait = true
	}
	runID, err := randomHex(6)
	if err != nil {
		return failClient(err)
	}

	names := fleetOrder(f)
	if verb == "stop" {
		names = reverseStrings(names)
	}
	acted, failed, skipped := 0, 0, 0
	for _, name := range names {
		t := f.Tenants[name]
		if t == nil {
			continue
		}
		if why, ok := fleetSkip(verb, t); !ok {
			fmt.Fprintf(stdout, "%-16s skipped: %s\n", name, why)
			skipped++
			continue
		}
		// One key per tenant per RUN. See the file comment: a derived key
		// would make the watchdog a replay.
		key := fmt.Sprintf("fleet-%s-%s-%s", verb, runID, name)
		*o.idempotency = key
		if verb == "stop" {
			// Each tenant's plan demands its OWN name as the confirm value.
			// The operator typed `--yes-destructive all` once for the fleet;
			// this is what that means for each row.
			*o.yesDestructive = name
		}
		fmt.Fprintf(stdout, "\n%s %s\n", verb, name)
		if code := submitOp(o, tenantOpTarget(name, verb), map[string]any{}); code != exitOK {
			fmt.Fprintf(stderr, "ragstack-ctl: %s %s exited %d — continuing with the rest of the fleet\n",
				verb, name, code)
			failed++
			continue
		}
		acted++
	}
	fmt.Fprintf(stdout, "\nfleet %s: %d succeeded, %d failed, %d skipped\n", verb, acted, failed, skipped)
	if failed > 0 {
		return exitJobFailed
	}
	return exitOK
}

// fleetOrder is the registry's display order, with any tenant the order
// forgot appended: a row missing from display_order is a registry defect, not
// a reason to leave a tenant down.
func fleetOrder(f *registry.Fleet) []string {
	seen := map[string]bool{}
	out := make([]string, 0, len(f.Tenants))
	for _, name := range f.DisplayOrder {
		if f.Tenants[name] != nil && !seen[name] {
			seen[name] = true
			out = append(out, name)
		}
	}
	rest := make([]string, 0, len(f.Tenants))
	for name := range f.Tenants {
		if !seen[name] {
			rest = append(rest, name)
		}
	}
	sortStrings(rest)
	return append(out, rest...)
}

// sortStrings and reverseStrings: the two orderings a fleet run needs, in the
// binary that needs them.
func sortStrings(s []string) { sort.Strings(s) }

func reverseStrings(s []string) []string {
	out := make([]string, len(s))
	for i, v := range s {
		out[len(s)-1-i] = v
	}
	return out
}

// fleetSkip decides whether this tenant takes part, and says why when it does
// not. The reasons are PRINTED rather than swallowed: "the fleet started" over
// a tenant that was silently left out is the report this command must never
// produce.
func fleetSkip(verb string, t *registry.Tenant) (why string, act bool) {
	switch {
	case !ops.KnownSupervisor(t.Supervisor):
		return fmt.Sprintf("supervisor is %q — the ctl did not start this tenant and will not %s it "+
			"(`ragstack-ctl tenant handover %s`)", t.Supervisor, verb, t.Name), false
	case t.State == "quarantined":
		return "the row is quarantined", false
	case verb == "start" && t.DesiredBoot != "enabled":
		return fmt.Sprintf("desired_boot is %q: this tenant is not meant to come back on its own "+
			"(`ragstack-ctl tenant start %s` starts it and sets it)", t.DesiredBoot, t.Name), false
	}
	return "", true
}

// ---------------------------------------------------------------- enable-boot

func cmdFleetEnableBoot(args []string, registryPath, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("fleet enable-boot", flag.ContinueOnError)
	fs.SetOutput(stderr)
	o := addOpFlags(fs, registryPath, ragRoot, jsonOut)
	cron := fs.Bool("cron", false, "install the @reboot line")
	noCron := fs.Bool("no-cron", false, "remove the @reboot line")
	if err := fs.Parse(args); err != nil {
		return exitUsage
	}
	if *cron == *noCron {
		return usageErr("fleet enable-boot needs exactly one of --cron and --no-cron")
	}
	roots := api.RootsFromEnv(*o.ragRoot)
	_, _ = registryPath, jsonOut

	// The crontab is read and written through the driver seam, so the same
	// argv rules the rest of the ctl runs under apply: an absolute program, no
	// shell, and the new body on STDIN rather than through a file anything
	// else could rewrite between the write and the read.
	drv, err := bootDrivers(o)
	if err != nil {
		return failClient(err)
	}
	ctx := context.Background()
	current, err := drv.Crontab().List(ctx)
	if err != nil {
		return failClient(fmt.Errorf("reading this account's crontab: %w", err))
	}
	next, changed, err := editBootCrontab(string(current), *cron)
	if err != nil {
		fmt.Fprintf(stderr, "ragstack-ctl: %v\n", err)
		return exitRefused
	}
	if *o.dryRun {
		fmt.Fprintf(stdout, "the one line ragstack-ctl owns:\n  %s\n", bootLine())
		if !changed {
			fmt.Fprintln(stdout, "\nthe crontab already says this; nothing would change")
			return exitOK
		}
		fmt.Fprintf(stdout, "\nthe crontab would become (%d line(s)):\n%s\n", countLines(next), next)
		return exitOK
	}
	if changed {
		if err := drv.Crontab().Set(ctx, []byte(next)); err != nil {
			return failClient(fmt.Errorf("writing this account's crontab: %w", err))
		}
	}
	if err := writeBootRecord(roots, *cron); err != nil {
		// The crontab is already right; the RECORD is what doctor reads, so a
		// failure here is reported and not fatal.
		fmt.Fprintf(stderr, "ragstack-ctl: the crontab was updated but %s could not be written (%v): "+
			"`doctor` will keep reporting boot_cron_missing until it can be\n",
			filepath.Join(roots.CtlStateDir, doctor.BootRecordFile), err)
		return exitError
	}
	switch {
	case *cron && changed:
		fmt.Fprintf(stdout, "installed:\n  %s\n", bootLine())
	case *cron:
		fmt.Fprintf(stdout, "already installed:\n  %s\n", bootLine())
	case changed:
		fmt.Fprintln(stdout, "removed the @reboot line ragstack-ctl owned; every other line is untouched")
	default:
		fmt.Fprintln(stdout, "there was no ragstack-ctl @reboot line to remove")
	}
	if *cron {
		fmt.Fprintf(stdout, "\nA watchdog is the same command on a timer, and is the operator's to add:\n"+
			"  */5 * * * * /rag/bin/ragstack-ctl fleet start --all --direct >>%s/boot-watchdog.log 2>&1\n"+
			"`fleet start --all` is idempotent — a running leg is a no-op in both supervisors — so a periodic run "+
			"restarts what died and touches nothing else.\n", roots.CtlStateDir)
	}
	return exitOK
}

// editBootCrontab returns the crontab with the ctl's line added or removed,
// and whether anything changed.
//
// Every other line is copied THROUGH, in order, including the blank ones and
// the comments: this account's crontab already carries the gateway's own
// `@reboot start-proxy.sh`, and a tool that rewrote a crontab it did not
// author would eventually eat it.
//
// More than one marked line is a REFUSAL rather than a cleanup. Two lines
// means either two ctl installations sharing an account or an operator who
// edited one by hand, and in both cases deleting "the" line is deleting
// something somebody meant.
func editBootCrontab(current string, want bool) (string, bool, error) {
	body := strings.TrimRight(current, "\n")
	var lines []string
	if body != "" {
		lines = strings.Split(body, "\n")
	}
	marked := 0
	for _, l := range lines {
		if strings.Contains(l, bootMarker) {
			marked++
		}
	}
	if marked > 1 {
		return "", false, fmt.Errorf("this crontab has %d lines carrying `%s`; ragstack-ctl owns exactly one and "+
			"will not choose between them — remove the extras by hand (`crontab -e`) and run this again",
			marked, bootMarker)
	}
	if !want {
		if marked == 0 {
			return current, false, nil
		}
		out := make([]string, 0, len(lines))
		for _, l := range lines {
			if !strings.Contains(l, bootMarker) {
				out = append(out, l)
			}
		}
		return joinCrontab(out), true, nil
	}
	want1 := bootLine()
	if marked == 1 {
		out := make([]string, len(lines))
		copy(out, lines)
		for i, l := range out {
			if strings.Contains(l, bootMarker) {
				if l == want1 {
					return current, false, nil
				}
				out[i] = want1
			}
		}
		return joinCrontab(out), true, nil
	}
	return joinCrontab(append(lines, want1)), true, nil
}

// joinCrontab ends the body with exactly one newline: crontab(1) drops a
// final line that has none.
func joinCrontab(lines []string) string {
	if len(lines) == 0 {
		return ""
	}
	return strings.Join(lines, "\n") + "\n"
}

func countLines(s string) int {
	if strings.TrimSpace(s) == "" {
		return 0
	}
	return len(strings.Split(strings.TrimRight(s, "\n"), "\n"))
}

// writeBootRecord is the fact doctor reads. The ctl records what IT installed;
// doctor never runs `crontab -l` (see doctor.BootRecord for why).
func writeBootRecord(roots paths.Roots, cron bool) error {
	rec := doctor.BootRecord{Cron: cron, At: time.Now().UTC().Format(time.RFC3339)}
	if cron {
		rec.Line = bootLine()
	}
	b, err := json.MarshalIndent(rec, "", "  ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(roots.CtlStateDir, 0o2770); err != nil && !os.IsExist(err) {
		return err
	}
	path := filepath.Join(roots.CtlStateDir, doctor.BootRecordFile)
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, append(b, '\n'), 0o640); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

// bootDrivers is the driver set `enable-boot` runs through. It is the same
// builder every --direct submission uses, so the crontab driver is the one
// ctl.env configured rather than a second one this command made up.
func bootDrivers(o *opFlags) (jobs.Drivers, error) {
	_, drv, err := buildDirectEngineAndDrivers(o)
	return drv, err
}
