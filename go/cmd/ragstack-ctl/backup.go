package main

// `ragstack-ctl backup list|verify|prune` — the three READS of the backup
// tree.
//
// They are not operations. Nothing here plans, locks, submits a job or talks
// to the daemon: taking a bundle is `tenant backup`, restoring one is `tenant
// restore`, and what an operator needs in between is to be able to see what is
// on disk and to check that it is intact. That makes these three plain local
// commands over `<rag-root>/backups/tenants/`, which also means they work on a
// host whose daemon is not running — which is exactly the host somebody is
// most likely to be looking at a backup from.
//
// `verify` is the DEEP check without a restore (ops.CheckBundle, the function
// the backup job's own check step runs): every file still hashes to what
// SHA256SUMS says, the manifest validates against the contract, the leg
// records' files are there, each store leg is structurally what its store
// writes. A bundle that passes is marked `checked` — in its manifest, and in
// the registry when it is the tenant's last backup — which is what
// `decommission` accepts. It is not proof the bundle restores — only a restore
// is, and it says so.

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/ops"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// retention is the plan's keep_last table. Only VERIFIED bundles count
// toward it, and the newest verified one is never a candidate whatever the
// numbers say.
var retention = map[string]int{"backup": 7, "pre-update": 2}

// partialMaxAge is how long a `<id>.partial` directory — the mark of a backup
// that was interrupted — is left before `prune` lists it.
const partialMaxAge = 24 * time.Hour

func backupUsage() int {
	return usageErr("usage: ragstack-ctl backup list [<tenant>] [--json]\n" +
		"       ragstack-ctl backup verify <tenant> <bundle-id> [--json]\n" +
		"       ragstack-ctl backup prune <tenant> --dry-run [--json]")
}

func cmdBackup(args []string, registryPath, ragRoot string, jsonOut bool) int {
	if len(args) == 0 {
		return backupUsage()
	}
	switch args[0] {
	case "list":
		return cmdBackupList(args[1:], ragRoot, jsonOut)
	case "verify":
		return cmdBackupVerify(args[1:], registryPath, ragRoot, jsonOut)
	case "prune":
		return cmdBackupPrune(args[1:], ragRoot, jsonOut)
	default:
		return backupUsage()
	}
}

// validTenant is the name check every one of the three reads makes before the
// name is joined under the backups root.
//
// These commands take a name straight from the command line and build a path
// out of it, so `backup verify ../../etc passwd` is a path traversal spelled as
// an argument. paths.ValidateName is the same grammar the registry, the ops
// endpoint and the gateway enforce; a name that cannot be a tenant cannot name
// a bundle directory either.
func validTenant(name string) bool {
	if err := paths.ValidateName(name); err != nil {
		fmt.Fprintf(stderr, "ragstack-ctl: %v\n", err)
		return false
	}
	return true
}

// ---------------------------------------------------------------- list

func cmdBackupList(args []string, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("backup list", flag.ContinueOnError)
	fs.SetOutput(stderr)
	localJSON := fs.Bool("json", false, "machine-readable output")
	root := fs.String("rag-root", ragRoot, "deployment root")
	pos, rest := takePositionals(args, 1)
	if err := fs.Parse(rest); err != nil {
		return exitUsage
	}
	pos = append(pos, fs.Args()...)
	if len(pos) > 1 {
		return backupUsage()
	}
	tenant := ""
	if len(pos) == 1 {
		// A listing of EVERY tenant reads the backups root itself and joins
		// nothing, so the check is on the one path that takes a name.
		if !validTenant(pos[0]) {
			return exitUsage
		}
		tenant = pos[0]
	}
	rows, err := readBundles(paths.NewRoots(*root, paths.Overrides{}).BackupsDir, tenant)
	if err != nil {
		fmt.Fprintf(stderr, "ragstack-ctl: %v\n", err)
		return exitError
	}
	if jsonOut || *localJSON {
		return printJSON(map[string]any{"bundles": rows})
	}
	if len(rows) == 0 {
		fmt.Fprintln(stdout, "no bundles")
		return exitOK
	}
	fmt.Fprintf(stdout, "%-12s  %-28s  %-10s  %-6s  %-7s  %-8s  %-20s  %10s\n",
		"TENANT", "ID", "KIND", "FENCED", "CHECKED", "VERIFIED", "CREATED", "SIZE")
	for _, b := range rows {
		// The scope goes under the id rather than in a column of its own: it is
		// long, it is the same on almost every row, and the row it distinguishes
		// is the one worth stopping at.
		fmt.Fprintf(stdout, "%-12s  %-28s  %-10s  %-6v  %-7v  %-8v  %-20s  %10s\n",
			b.Tenant, b.ID, b.Kind, b.Fenced, b.Checked, b.Verified, b.CreatedAt, humanSize(b.Bytes))
		if b.Scope != "" && b.Scope != "config,state,stores" {
			fmt.Fprintf(stdout, "%-12s  %-28s  scope %s (no store snapshots: not a recovery point)\n",
				"", "", b.Scope)
		}
	}
	return exitOK
}

// bundleRow is one directory under <backups>/<tenant>/.
type bundleRow struct {
	Tenant   string `json:"tenant"`
	ID       string `json:"id"`
	Kind     string `json:"kind"`
	Fenced   bool   `json:"fenced"`
	Verified bool   `json:"verified"`
	// Checked is the manifest's `checked` (absent in a bundle older than the
	// field, which reads as false).
	Checked bool `json:"checked"`
	// Scope is what the bundle HOLDS: "config,state,stores" for a full one,
	// "config,state" for the light bundle a handover takes. A listing without
	// it shows two rows that differ by hours of store snapshots and look
	// identical, which is the listing an operator checks before deciding they
	// have a backup. A bundle written before the member existed has none, and
	// reads as the full bundle it is.
	Scope     string `json:"scope"`
	CreatedAt string `json:"created_at"`
	Bytes     int64  `json:"bytes"`
	Path      string `json:"path"`
	// Partial marks an interrupted backup: the directory still carries the
	// `.partial` suffix, which is the ctl's way of saying "this was never
	// finished". Such a bundle has no manifest, so every other field is blank.
	Partial bool `json:"partial"`
	// Problem is why a directory could not be read as a bundle. A listing that
	// silently dropped an unreadable bundle would be the listing an operator
	// checks before deciding they have a backup.
	Problem string `json:"problem,omitempty"`
}

// readBundles reads every bundle directory, newest first.
func readBundles(backupsDir, tenant string) ([]bundleRow, error) {
	tenants := []string{}
	if tenant != "" {
		tenants = append(tenants, tenant)
	} else {
		ents, err := os.ReadDir(backupsDir)
		if err != nil {
			if os.IsNotExist(err) {
				return nil, nil
			}
			return nil, fmt.Errorf("reading %s: %w", backupsDir, err)
		}
		for _, e := range ents {
			if e.IsDir() {
				tenants = append(tenants, e.Name())
			}
		}
	}
	var out []bundleRow
	for _, name := range tenants {
		dir := filepath.Join(backupsDir, name)
		ents, err := os.ReadDir(dir)
		if err != nil {
			if os.IsNotExist(err) {
				continue
			}
			return nil, fmt.Errorf("reading %s: %w", dir, err)
		}
		for _, e := range ents {
			if !e.IsDir() {
				continue
			}
			out = append(out, readBundle(filepath.Join(dir, e.Name()), name, e.Name()))
		}
	}
	// Newest first, by id — the id begins with the timestamp, so the string
	// order IS the time order, and no clock has to be read to sort them.
	sort.Slice(out, func(i, j int) bool {
		if out[i].Tenant != out[j].Tenant {
			return out[i].Tenant < out[j].Tenant
		}
		return out[i].ID > out[j].ID
	})
	return out, nil
}

func readBundle(dir, tenant, id string) bundleRow {
	row := bundleRow{Tenant: tenant, ID: id, Path: dir}
	row.Bytes, _ = dirSize(dir)
	if strings.HasSuffix(id, ".partial") {
		row.Partial, row.Kind = true, "partial"
		return row
	}
	man, err := readManifest(dir)
	if err != nil {
		row.Problem = err.Error()
		return row
	}
	row.Kind, _ = man["kind"].(string)
	row.Fenced, _ = man["fenced"].(bool)
	row.Verified, _ = man["verified"].(bool)
	row.Checked, _ = man["checked"].(bool)
	row.Scope = manifestScope(man)
	row.CreatedAt, _ = man["created_at"].(string)
	return row
}

// manifestScope renders the manifest's `scope`. An ABSENT one is the full
// bundle: the member arrived after bundles without it had been written, and
// every one of those holds all three legs.
func manifestScope(man map[string]any) string {
	raw, ok := man["scope"].([]any)
	if !ok {
		return "config,state,stores"
	}
	out := make([]string, 0, len(raw))
	for _, v := range raw {
		if s, ok := v.(string); ok {
			out = append(out, s)
		}
	}
	if len(out) == 0 {
		return "config,state,stores"
	}
	return strings.Join(out, ",")
}

func readManifest(dir string) (map[string]any, error) {
	b, err := os.ReadFile(filepath.Join(dir, "manifest.json"))
	if err != nil {
		return nil, fmt.Errorf("no readable manifest.json")
	}
	var man map[string]any
	if err := json.Unmarshal(b, &man); err != nil {
		return nil, fmt.Errorf("manifest.json is not JSON: %v", err)
	}
	return man, nil
}

// ---------------------------------------------------------------- verify

func cmdBackupVerify(args []string, registryPath, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("backup verify", flag.ContinueOnError)
	fs.SetOutput(stderr)
	localJSON := fs.Bool("json", false, "machine-readable output")
	root := fs.String("rag-root", ragRoot, "deployment root")
	pos, rest := takePositionals(args, 2)
	if err := fs.Parse(rest); err != nil {
		return exitUsage
	}
	pos = append(pos, fs.Args()...)
	if len(pos) != 2 {
		return backupUsage()
	}
	tenant, id := pos[0], pos[1]
	if !validTenant(tenant) {
		return exitUsage
	}
	roots := paths.NewRoots(*root, paths.Overrides{})
	tenantBackups := filepath.Join(roots.BackupsDir, tenant)
	dir := filepath.Join(tenantBackups, id)
	// The bundle id is an argument too, and it is the second half of the path.
	if _, err := paths.SafePath(tenantBackups, dir); err != nil {
		fmt.Fprintf(stderr, "ragstack-ctl: %v\n", err)
		return exitUsage
	}
	res := verifyBundle(context.Background(), roots, resolveRegistry(registryPath, *root), dir, tenant, id)
	if jsonOut || *localJSON {
		_ = printJSON(res)
	} else {
		for _, l := range res.Legs {
			if l.OK {
				fmt.Fprintf(stdout, "ok    %-13s %s\n", l.Leg, l.Detail)
			}
		}
		for _, line := range res.Checks {
			fmt.Fprintln(stdout, line)
		}
		for _, p := range res.Problems {
			fmt.Fprintln(stderr, "FAIL: "+p)
		}
		if res.OK {
			fmt.Fprintf(stdout, "\n%s/%s: CHECKED — %d file(s) verified against SHA256SUMS, the manifest valid, every "+
				"store leg structurally sound.\n", tenant, id, res.Files)
			fmt.Fprintln(stdout, "This is not a restore: it proves the bundle is intact and well-formed, not that it restores.")
			fmt.Fprintf(stdout, "`checked` satisfies decommission. The deep verify is `ragstack-ctl tenant restore %s --from "+
				"%s --as <fresh-name>`, which rebuilds a tenant from it and is the only thing that sets `verified`.\n",
				tenant, id)
		}
	}
	if !res.OK {
		return exitError
	}
	return exitOK
}

// verifyResult is `backup verify`'s answer.
type verifyResult struct {
	Tenant string `json:"tenant"`
	Bundle string `json:"bundle"`
	OK     bool   `json:"ok"`
	Files  int    `json:"files"`
	// Legs are the deep check's verdicts, one per leg (ops.CheckBundle — the
	// same function the backup job's own check step runs).
	Legs []ops.BundleCheckLeg `json:"legs"`
	// Checks are the lines about what was marked, Problems everything that
	// failed (a failing leg, or a mark that could not be written).
	Checks   []string `json:"checks"`
	Problems []string `json:"problems"`
	// Checked is whether the manifest now says `checked: true`; Registry says
	// what happened to the row's last_backup.
	Checked  bool   `json:"checked"`
	Registry string `json:"registry"`
	Deep     string `json:"deep_verify"`
}

// verifyBundle runs the deep check over the bundle in dir through the REAL
// drivers (reads only), and — when it passes — marks it: `checked: true` in
// the manifest, and in the registry row's last_backup when this bundle IS the
// row's last backup.
//
// The marks are written under the registry and tenant locks, the same flock
// files the daemon's jobs take, so a `backup verify` cannot interleave with a
// backup recording a NEWER last_backup (or a restore marking this one
// verified). A held lock is a refusal naming the holder, and nothing is
// marked: the check is cheap to run again.
func verifyBundle(ctx context.Context, roots paths.Roots, registryPath, dir, tenant, id string) verifyResult {
	res := verifyResult{Bundle: id, Tenant: tenant, Problems: []string{}, Checks: []string{},
		Legs: []ops.BundleCheckLeg{}, Deep: "restore --as"}
	drv := drivers.NewReal(drivers.RealOptions{Roots: roots})
	check, err := ops.CheckBundle(ctx, ops.BundleCheckDrivers{Files: drv.Files(), Archive: drv.Archive(),
		SQLite: drv.SQLite()}, dir, id, nil)
	res.Legs, res.Files = check.Legs, check.Files
	for _, l := range check.Legs {
		if !l.OK {
			res.Problems = append(res.Problems, l.Leg+": "+l.Detail)
		}
	}
	if err != nil {
		if len(res.Problems) == 0 {
			res.Problems = append(res.Problems, err.Error())
		}
		res.Registry = "not touched: the bundle did not pass"
		return res
	}
	if check.Fenced {
		res.Checks = append(res.Checks, "the bundle is fenced")
	} else {
		res.Checks = append(res.Checks, "NOTE: this bundle is best-effort (unfenced): it is checked, and it cannot be "+
			"restored from or satisfy a decommission")
	}

	locks, err := jobs.NewLocks(roots).Take([]model.LockName{model.LockRegistry, model.LockTenant}, tenant,
		jobs.LockHolder{PID: os.Getpid()}, time.Now())
	if err != nil {
		res.Problems = append(res.Problems, "the bundle passed every leg, and marking it needs the registry and "+
			tenant+" locks: "+err.Error()+". Nothing was marked; run `backup verify` again when that is done")
		res.Registry = "not touched: locked"
		return res
	}
	defer locks.Release()

	// The manifest FIRST: a registry that said `checked` about a bundle whose
	// own manifest did not would be a claim with nothing behind it.
	if err := ops.MarkBundleChecked(ctx, drv.Files(), dir); err != nil {
		res.Problems = append(res.Problems, "the bundle passed, and its manifest could not be marked: "+err.Error())
		res.Registry = "not touched: the manifest was not marked"
		return res
	}
	res.Checked = true
	res.Checks = append(res.Checks, "manifest.json: checked = true")

	note, err := markLastBackupChecked(registryPath, tenant, id)
	res.Registry = note
	if err != nil {
		res.Problems = append(res.Problems, "the manifest is marked, and the registry could not be: "+err.Error())
		return res
	}
	res.Checks = append(res.Checks, "registry: "+note)
	res.OK = true
	return res
}

// markLastBackupChecked sets `last_backup.checked` on tenant's row when its
// last backup IS bundle id (by basename: the registry records the absolute
// directory, the CLI speaks the id). Any other state is not an error — a host
// with no registry, a tenant that is not in it, a row whose recovery point is
// a newer bundle — and is said in the returned note. Caller holds the
// registry and tenant locks.
func markLastBackupChecked(registryPath, tenant, id string) (string, error) {
	if _, err := os.Stat(registryPath); errors.Is(err, os.ErrNotExist) {
		return "no registry at " + registryPath + "; only the manifest is marked", nil
	}
	f, err := registry.LoadNoRepair(registryPath)
	if err != nil {
		return "", err
	}
	row := f.Tenants[tenant]
	switch {
	case row == nil:
		return tenant + " is not in the registry; only the manifest is marked", nil
	case row.LastBackup == nil:
		return tenant + " has no last_backup; only the manifest is marked", nil
	case filepath.Base(row.LastBackup.Bundle) != id:
		return fmt.Sprintf("%s's last_backup is %s, not %s, so the row was left alone", tenant,
			filepath.Base(row.LastBackup.Bundle), id), nil
	case row.LastBackup.Checked:
		return tenant + "'s last_backup was already checked", nil
	}
	row.LastBackup.Checked = true
	if err := registry.Save(registryPath, f, "ragstack-ctl backup verify"); err != nil {
		return "", err
	}
	return tenant + "'s last_backup is checked", nil
}

// ---------------------------------------------------------------- prune

func cmdBackupPrune(args []string, ragRoot string, jsonOut bool) int {
	fs := flag.NewFlagSet("backup prune", flag.ContinueOnError)
	fs.SetOutput(stderr)
	localJSON := fs.Bool("json", false, "machine-readable output")
	dryRun := fs.Bool("dry-run", false, "print what would be removed and remove nothing (required in v1)")
	root := fs.String("rag-root", ragRoot, "deployment root")
	pos, rest := takePositionals(args, 1)
	if err := fs.Parse(rest); err != nil {
		return exitUsage
	}
	pos = append(pos, fs.Args()...)
	if len(pos) != 1 {
		return backupUsage()
	}
	if !*dryRun {
		// Not a usage error: the command is spelled correctly and the answer
		// is no. v1 deletes no backup, ever — automatic retention is v1.x, and
		// a `prune` that quietly grew the power to delete between releases is
		// how a fleet loses its recovery points.
		fmt.Fprintln(stderr, "ragstack-ctl backup prune: --dry-run is required. v1 never deletes a bundle: this "+
			"command prints what a future retention pass WOULD remove, and removing it is an operator's own `rm -rf` "+
			"after reading that list.")
		return exitRefused
	}
	tenant := pos[0]
	if !validTenant(tenant) {
		return exitUsage
	}
	rows, err := readBundles(paths.NewRoots(*root, paths.Overrides{}).BackupsDir, tenant)
	if err != nil {
		fmt.Fprintf(stderr, "ragstack-ctl: %v\n", err)
		return exitError
	}
	plan := prunePlan(rows, time.Now())
	if jsonOut || *localJSON {
		return printJSON(plan)
	}
	fmt.Fprintf(stdout, "backup prune %s — DRY RUN, nothing is removed\n\n", tenant)
	if len(plan.Remove) == 0 {
		fmt.Fprintln(stdout, "nothing would be removed")
	} else {
		var total int64
		fmt.Fprintln(stdout, "WOULD REMOVE:")
		for _, c := range plan.Remove {
			total += c.Bytes
			fmt.Fprintf(stdout, "  %-28s  %-10s  %10s  %s\n", c.ID, c.Kind, humanSize(c.Bytes), c.Why)
		}
		fmt.Fprintf(stdout, "  %s in %d bundle(s)\n", humanSize(total), len(plan.Remove))
	}
	fmt.Fprintln(stdout, "\nKEPT:")
	for _, c := range plan.Keep {
		fmt.Fprintf(stdout, "  %-28s  %-10s  %10s  %s\n", c.ID, c.Kind, humanSize(c.Bytes), c.Why)
	}
	return exitOK
}

// pruneCandidate is one bundle and the reason it is on the list it is on.
type pruneCandidate struct {
	ID    string `json:"id"`
	Kind  string `json:"kind"`
	Bytes int64  `json:"bytes"`
	Path  string `json:"path"`
	Why   string `json:"why"`
}

type prunePlanResult struct {
	Tenant string           `json:"tenant"`
	DryRun bool             `json:"dry_run"`
	Keep   []pruneCandidate `json:"keep"`
	Remove []pruneCandidate `json:"remove"`
}

// prunePlan applies the plan's retention rules. Rows arrive newest first.
func prunePlan(rows []bundleRow, now time.Time) prunePlanResult {
	out := prunePlanResult{DryRun: true, Keep: []pruneCandidate{}, Remove: []pruneCandidate{}}
	seenVerified := map[string]int{}
	for _, b := range rows {
		out.Tenant = b.Tenant
		c := pruneCandidate{ID: b.ID, Kind: b.Kind, Bytes: b.Bytes, Path: b.Path}
		switch {
		case b.Partial:
			age := partialAge(b.Path, now)
			if age > partialMaxAge {
				c.Why = fmt.Sprintf("an interrupted backup, %s old: it has no manifest and can never be restored from",
					age.Round(time.Hour))
				out.Remove = append(out.Remove, c)
				continue
			}
			c.Why = "an interrupted backup younger than " + partialMaxAge.String() +
				" — it may still belong to a job that is running"
		case b.Problem != "":
			c.Why = "unreadable (" + b.Problem + "): looked at by a person, never removed by a rule"
		case !b.Verified:
			// The rule that matters: retention counts VERIFIED bundles, and a
			// bundle nothing has restored from is not a bundle this tool will
			// propose deleting. It may be the only copy that works.
			c.Why = "never verified — it does not count toward keep_last, and v1 never proposes removing a bundle " +
				"no restore has proved"
		default:
			keep, known := retention[b.Kind]
			if !known {
				c.Why = "kind " + b.Kind + " has no retention rule"
				break
			}
			seenVerified[b.Kind]++
			switch n := seenVerified[b.Kind]; {
			case n == 1:
				c.Why = fmt.Sprintf("the newest verified %s: never pruned", b.Kind)
			case n <= keep:
				c.Why = fmt.Sprintf("verified %s %d of keep_last %d", b.Kind, n, keep)
			default:
				c.Why = fmt.Sprintf("verified %s %d, past keep_last %d", b.Kind, n, keep)
				out.Remove = append(out.Remove, c)
				continue
			}
		}
		out.Keep = append(out.Keep, c)
	}
	return out
}

// partialAge is how long ago a `.partial` directory was last written. The
// directory's own mtime, not the timestamp in its name: a job that is still
// running is writing into it, and its name was chosen when it started.
func partialAge(dir string, now time.Time) time.Duration {
	st, err := os.Stat(dir)
	if err != nil {
		return 0
	}
	return now.Sub(st.ModTime())
}

// ---------------------------------------------------------------- helpers

// printJSON writes one document to stdout, indented.
func printJSON(v any) int {
	enc := json.NewEncoder(stdout)
	enc.SetIndent("", "  ")
	if err := enc.Encode(v); err != nil {
		fmt.Fprintf(stderr, "ragstack-ctl: %v\n", err)
		return exitError
	}
	return exitOK
}

func dirSize(dir string) (int64, error) {
	var n int64
	err := filepath.Walk(dir, func(_ string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.Mode().IsRegular() {
			n += info.Size()
		}
		return nil
	})
	return n, err
}

func humanSize(n int64) string {
	const unit = 1024
	if n < unit {
		return fmt.Sprintf("%d B", n)
	}
	div, exp := int64(unit), 0
	for m := n / unit; m >= unit; m /= unit {
		div *= unit
		exp++
	}
	return fmt.Sprintf("%.1f %ciB", float64(n)/float64(div), "KMGTPE"[exp])
}
