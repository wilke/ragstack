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
// `verify` is the SHALLOW check: every file still hashes to what SHA256SUMS
// says, and the manifest is the document the contract describes. It is not
// proof the bundle restores — only a restore is, and it says so.

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/ops"
	"github.com/ragstack/ragstack/internal/ctl/paths"
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

func cmdBackup(args []string, ragRoot string, jsonOut bool) int {
	if len(args) == 0 {
		return backupUsage()
	}
	switch args[0] {
	case "list":
		return cmdBackupList(args[1:], ragRoot, jsonOut)
	case "verify":
		return cmdBackupVerify(args[1:], ragRoot, jsonOut)
	case "prune":
		return cmdBackupPrune(args[1:], ragRoot, jsonOut)
	default:
		return backupUsage()
	}
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
	fmt.Fprintf(stdout, "%-12s  %-28s  %-10s  %-6s  %-8s  %-20s  %10s\n",
		"TENANT", "ID", "KIND", "FENCED", "VERIFIED", "CREATED", "SIZE")
	for _, b := range rows {
		fmt.Fprintf(stdout, "%-12s  %-28s  %-10s  %-6v  %-8v  %-20s  %10s\n",
			b.Tenant, b.ID, b.Kind, b.Fenced, b.Verified, b.CreatedAt, humanSize(b.Bytes))
	}
	return exitOK
}

// bundleRow is one directory under <backups>/<tenant>/.
type bundleRow struct {
	Tenant    string `json:"tenant"`
	ID        string `json:"id"`
	Kind      string `json:"kind"`
	Fenced    bool   `json:"fenced"`
	Verified  bool   `json:"verified"`
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
	row.CreatedAt, _ = man["created_at"].(string)
	return row
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

func cmdBackupVerify(args []string, ragRoot string, jsonOut bool) int {
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
	dir := filepath.Join(paths.NewRoots(*root, paths.Overrides{}).BackupsDir, tenant, id)
	res := verifyBundle(dir, id)
	if jsonOut || *localJSON {
		_ = printJSON(res)
	} else {
		for _, line := range res.Checks {
			fmt.Fprintln(stdout, line)
		}
		for _, p := range res.Problems {
			fmt.Fprintln(stderr, "FAIL: "+p)
		}
		if res.OK {
			fmt.Fprintf(stdout, "\n%s/%s: %d file(s) verified against SHA256SUMS, manifest complete.\n",
				tenant, id, res.Files)
			fmt.Fprintln(stdout, "This is the SHALLOW check: it proves the bundle is intact, not that it restores.")
			fmt.Fprintf(stdout, "The deep verify is `ragstack-ctl tenant restore %s --from %s --as <fresh-name>`, "+
				"which rebuilds a tenant from it and is the only thing that sets `verified`.\n", tenant, id)
		}
	}
	if !res.OK {
		return exitError
	}
	return exitOK
}

// verifyResult is `backup verify`'s answer.
type verifyResult struct {
	Tenant   string   `json:"tenant"`
	Bundle   string   `json:"bundle"`
	OK       bool     `json:"ok"`
	Files    int      `json:"files"`
	Checks   []string `json:"checks"`
	Problems []string `json:"problems"`
	Deep     string   `json:"deep_verify"`
}

func verifyBundle(dir, id string) verifyResult {
	res := verifyResult{Bundle: id, Tenant: filepath.Base(filepath.Dir(dir)), Problems: []string{}, Checks: []string{},
		Deep: "restore --as"}
	add := func(format string, a ...any) { res.Checks = append(res.Checks, fmt.Sprintf(format, a...)) }
	fail := func(format string, a ...any) { res.Problems = append(res.Problems, fmt.Sprintf(format, a...)) }

	if strings.HasSuffix(id, ".partial") {
		fail("%s is a `.partial` directory: the backup that was writing it never finished, so there is nothing to "+
			"verify. Take a new one", id)
		return res
	}
	man, err := readManifest(dir)
	if err != nil {
		fail("%s: %v", dir, err)
		return res
	}
	add("manifest.json parses")

	// Every key the contract requires. The list is the ops package's, which a
	// test pins to contracts/ctl/schemas/bundle_manifest.json — so a member
	// added to the contract is a member this check demands.
	var missing []string
	for _, key := range ops.BundleManifestRequired {
		if _, ok := man[key]; !ok {
			missing = append(missing, key)
		}
	}
	if len(missing) > 0 {
		fail("the manifest is missing %d required member(s): %s", len(missing), strings.Join(missing, ", "))
	} else {
		add("the manifest carries all %d required members", len(ops.BundleManifestRequired))
	}
	if got, _ := man["bundle_id"].(string); got != id {
		fail("the manifest calls this bundle %q; it is in a directory called %q. One of the two moved, and a "+
			"restore reading the manifest would look in the wrong place", got, id)
	} else {
		add("bundle_id matches the directory")
	}
	if fenced, _ := man["fenced"].(bool); !fenced {
		add("NOTE: this bundle is best-effort (unfenced): it can be read, and it cannot be restored from")
	}

	// SHA256SUMS: every line re-hashed, and every file in the bundle listed.
	sums, err := os.ReadFile(filepath.Join(dir, "SHA256SUMS"))
	if err != nil {
		fail("no readable SHA256SUMS: %v", err)
		return res
	}
	listed := map[string]string{}
	for n, line := range strings.Split(string(sums), "\n") {
		if strings.TrimSpace(line) == "" {
			continue
		}
		sum, rel, ok := strings.Cut(line, "  ")
		if !ok || len(sum) != 64 {
			fail("SHA256SUMS line %d is not `<hex>  <relpath>`: %q", n+1, line)
			continue
		}
		listed[rel] = sum
	}
	for rel, want := range listed {
		got, err := fileSha256(filepath.Join(dir, rel))
		switch {
		case err != nil:
			fail("%s is listed in SHA256SUMS and could not be read: %v", rel, err)
		case got != want:
			fail("%s has changed since the bundle was written (SHA256SUMS says %s, it hashes to %s)", rel, want, got)
		default:
			res.Files++
		}
	}
	// And the other direction: a file added to a bundle after the fact is a
	// file no checksum covers, which is the case a checksum list exists for.
	onDisk, err := walkRel(dir)
	if err != nil {
		fail("listing %s: %v", dir, err)
	}
	for _, rel := range onDisk {
		if rel == "SHA256SUMS" || rel == "manifest.json" {
			continue
		}
		if _, ok := listed[rel]; !ok {
			fail("%s is in the bundle and in no checksum line", rel)
		}
	}
	if len(res.Problems) == 0 {
		add("%d file(s) match SHA256SUMS, and nothing in the bundle is uncovered", res.Files)
	}

	// The manifest's own digest of the checksum file: SHA256SUMS cannot list
	// itself, so this is what ties it to the manifest.
	digest := sha256.Sum256(sums)
	if want, _ := man["sha256sums"].(string); want != hex.EncodeToString(digest[:]) {
		fail("the manifest's sha256sums (%s) is not the digest of SHA256SUMS (%s)", want, hex.EncodeToString(digest[:]))
	} else {
		add("SHA256SUMS matches the digest the manifest carries")
	}
	res.OK = len(res.Problems) == 0
	return res
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

func fileSha256(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

// walkRel lists every regular file under dir, relative to it.
func walkRel(dir string) ([]string, error) {
	var out []string
	err := filepath.Walk(dir, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() || !info.Mode().IsRegular() {
			return nil
		}
		rel, err := filepath.Rel(dir, path)
		if err != nil {
			return err
		}
		out = append(out, rel)
		return nil
	})
	sort.Strings(out)
	return out, err
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
