package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/ops"
)

// writeBundle lays a bundle down on disk the way the backup op does: the
// files, then SHA256SUMS over them, then a manifest carrying its digest.
// opts lets one test break exactly one of those.
type bundleOpts struct {
	kind     string
	fenced   bool
	verified bool
	created  string
	// extraFile is a file added to the bundle AFTER the checksums were
	// written, i.e. one no SHA256SUMS line covers.
	extraFile string
	// corrupt names a file to rewrite after the checksums.
	corrupt string
	// dropManifestKey removes one required member.
	dropManifestKey string
}

func writeBundle(t *testing.T, root, tenant, id string, o bundleOpts) string {
	t.Helper()
	dir := filepath.Join(root, "backups", "tenants", tenant, id)
	files := map[string]string{
		"MIGRATE.md":              "# " + tenant + "\n",
		"registry-row.json":       `{"name":"` + tenant + `"}`,
		"config/tenant.env":       "LOG_LEVEL=INFO\n",
		"state/ragstack_users.db": "sqlite\n",
	}
	for rel, body := range files {
		path := filepath.Join(dir, rel)
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(body), 0o640); err != nil {
			t.Fatal(err)
		}
	}
	rels := make([]string, 0, len(files))
	for rel := range files {
		rels = append(rels, rel)
	}
	sort.Strings(rels)
	var sums strings.Builder
	for _, rel := range rels {
		sum := sha256.Sum256([]byte(files[rel]))
		fmt.Fprintf(&sums, "%s  %s\n", hex.EncodeToString(sum[:]), rel)
	}
	if err := os.WriteFile(filepath.Join(dir, "SHA256SUMS"), []byte(sums.String()), 0o640); err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256([]byte(sums.String()))

	man := map[string]any{"sha256sums": hex.EncodeToString(digest[:])}
	for _, key := range ops.BundleManifestRequired {
		if _, ok := man[key]; !ok {
			man[key] = nil
		}
	}
	man["bundle_id"] = id
	man["kind"] = o.kind
	man["fenced"] = o.fenced
	man["verified"] = o.verified
	man["created_at"] = o.created
	if o.dropManifestKey != "" {
		delete(man, o.dropManifestKey)
	}
	body, _ := json.MarshalIndent(man, "", "  ")
	if err := os.WriteFile(filepath.Join(dir, "manifest.json"), body, 0o640); err != nil {
		t.Fatal(err)
	}
	if o.extraFile != "" {
		if err := os.WriteFile(filepath.Join(dir, o.extraFile), []byte("added later\n"), 0o640); err != nil {
			t.Fatal(err)
		}
	}
	if o.corrupt != "" {
		if err := os.WriteFile(filepath.Join(dir, o.corrupt), []byte("tampered\n"), 0o640); err != nil {
			t.Fatal(err)
		}
	}
	return dir
}

func TestBackupListShowsEveryBundleNewestFirst(t *testing.T) {
	root := t.TempDir()
	writeBundle(t, root, "dev", "20260901T000000Z-backup", bundleOpts{kind: "backup", fenced: true, verified: true, created: "2026-09-01T00:00:00Z"})
	writeBundle(t, root, "dev", "20260914T093000Z-backup", bundleOpts{kind: "backup", fenced: true, created: "2026-09-14T09:30:00Z"})
	writeBundle(t, root, "demo", "20260910T000000Z-pre-update", bundleOpts{kind: "pre-update", created: "2026-09-10T00:00:00Z"})

	rc, out, errs := capture(t, "--rag-root", root, "backup", "list")
	if rc != exitOK {
		t.Fatalf("rc %d: %s", rc, errs)
	}
	for _, want := range []string{"20260914T093000Z-backup", "20260901T000000Z-backup", "20260910T000000Z-pre-update", "demo"} {
		if !strings.Contains(out, want) {
			t.Errorf("listing does not mention %s:\n%s", want, out)
		}
	}
	// Newest first inside a tenant: an operator looking for "the last backup"
	// reads the top of the list.
	if strings.Index(out, "20260914T093000Z-backup") > strings.Index(out, "20260901T000000Z-backup") {
		t.Errorf("the listing is not newest-first:\n%s", out)
	}

	// One tenant only.
	_, out, _ = capture(t, "--rag-root", root, "backup", "list", "demo")
	if strings.Contains(out, "dev") {
		t.Errorf("`backup list demo` listed another tenant:\n%s", out)
	}

	// And the JSON carries the fields the table shows.
	_, out, _ = capture(t, "--rag-root", root, "--json", "backup", "list", "dev")
	var doc struct {
		Bundles []map[string]any `json:"bundles"`
	}
	if err := json.Unmarshal([]byte(out), &doc); err != nil {
		t.Fatalf("not JSON: %v\n%s", err, out)
	}
	if len(doc.Bundles) != 2 {
		t.Fatalf("bundles = %v", doc.Bundles)
	}
	first := doc.Bundles[0]
	if first["id"] != "20260914T093000Z-backup" || first["fenced"] != true || first["verified"] != false {
		t.Errorf("first row = %v", first)
	}
	if b, _ := first["bytes"].(float64); b <= 0 {
		t.Errorf("the row reports no size: %v", first["bytes"])
	}
}

func TestBackupListOnAHostWithNoBackupsSaysSo(t *testing.T) {
	rc, out, _ := capture(t, "--rag-root", t.TempDir(), "backup", "list")
	if rc != exitOK || !strings.Contains(out, "no bundles") {
		t.Errorf("rc %d, out %q", rc, out)
	}
}

func TestBackupVerifyPassesAnIntactBundleAndPointsAtTheDeepCheck(t *testing.T) {
	root := t.TempDir()
	writeBundle(t, root, "dev", "20260914T093000Z-backup", bundleOpts{kind: "backup", fenced: true, created: "2026-09-14T09:30:00Z"})
	rc, out, errs := capture(t, "--rag-root", root, "backup", "verify", "dev", "20260914T093000Z-backup")
	if rc != exitOK {
		t.Fatalf("rc %d: %s\n%s", rc, errs, out)
	}
	for _, want := range []string{
		"SHA256SUMS", "bundle_id matches the directory",
		"restore dev --from 20260914T093000Z-backup --as",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("verify output does not mention %q:\n%s", want, out)
		}
	}
	if !strings.Contains(out, "not that it restores") {
		t.Errorf("verify must say it is the shallow check:\n%s", out)
	}
}

func TestBackupVerifyFailsOnEveryWayABundleCanBeWrong(t *testing.T) {
	cases := []struct {
		name string
		opts bundleOpts
		want string
	}{
		{"a changed file", bundleOpts{kind: "backup", fenced: true, corrupt: "MIGRATE.md"}, "has changed since the bundle was written"},
		{"an uncovered file", bundleOpts{kind: "backup", fenced: true, extraFile: "surprise.txt"}, "in no checksum line"},
		{"a missing member", bundleOpts{kind: "backup", fenced: true, dropManifestKey: "external"}, "missing 1 required member"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			root := t.TempDir()
			writeBundle(t, root, "dev", "20260914T093000Z-backup", c.opts)
			rc, _, errs := capture(t, "--rag-root", root, "backup", "verify", "dev", "20260914T093000Z-backup")
			if rc != exitError {
				t.Fatalf("rc %d, want a failure", rc)
			}
			if !strings.Contains(errs, c.want) {
				t.Errorf("stderr does not say %q:\n%s", c.want, errs)
			}
		})
	}
}

func TestBackupVerifyRefusesADirectoryThatIsNotABundle(t *testing.T) {
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "backups", "tenants", "dev", "20260914T093000Z-backup.partial"), 0o755); err != nil {
		t.Fatal(err)
	}
	rc, _, errs := capture(t, "--rag-root", root, "backup", "verify", "dev", "20260914T093000Z-backup.partial")
	if rc != exitError || !strings.Contains(errs, "never finished") {
		t.Errorf("rc %d, stderr %q", rc, errs)
	}
	rc, _, errs = capture(t, "--rag-root", root, "backup", "verify", "dev", "20260101T000000Z-backup")
	if rc != exitError || !strings.Contains(errs, "no readable manifest.json") {
		t.Errorf("a bundle that is not there: rc %d, stderr %q", rc, errs)
	}
}

// TestBackupPruneRefusesWithoutDryRun is the v1 rule, and it is a REFUSAL
// (exit 3) rather than a usage error: the command was spelled correctly.
func TestBackupPruneRefusesWithoutDryRun(t *testing.T) {
	root := t.TempDir()
	writeBundle(t, root, "dev", "20260914T093000Z-backup", bundleOpts{kind: "backup", fenced: true, verified: true})
	rc, _, errs := capture(t, "--rag-root", root, "backup", "prune", "dev")
	if rc != exitRefused {
		t.Fatalf("rc %d, want %d (refused)", rc, exitRefused)
	}
	if !strings.Contains(errs, "--dry-run is required") || !strings.Contains(errs, "never deletes") {
		t.Errorf("stderr = %q", errs)
	}
}

func TestBackupPruneKeepsSevenVerifiedBackupsAndTheNewestAlways(t *testing.T) {
	root := t.TempDir()
	// Ten verified backups, oldest first, plus two unverified ones and a
	// pre-update trio.
	for i := 1; i <= 10; i++ {
		writeBundle(t, root, "dev", fmt.Sprintf("202609%02dT000000Z-backup", i),
			bundleOpts{kind: "backup", fenced: true, verified: true})
	}
	writeBundle(t, root, "dev", "20260920T000000Z-backup", bundleOpts{kind: "backup", fenced: true})
	for i := 1; i <= 3; i++ {
		writeBundle(t, root, "dev", fmt.Sprintf("202608%02dT000000Z-pre-update", i),
			bundleOpts{kind: "pre-update", fenced: true, verified: true})
	}
	rc, out, errs := capture(t, "--rag-root", root, "--json", "backup", "prune", "dev", "--dry-run")
	if rc != exitOK {
		t.Fatalf("rc %d: %s", rc, errs)
	}
	var plan prunePlanResult
	if err := json.Unmarshal([]byte(out), &plan); err != nil {
		t.Fatalf("not JSON: %v\n%s", err, out)
	}
	removed := map[string]string{}
	for _, c := range plan.Remove {
		removed[c.ID] = c.Why
	}
	// Ten verified backups, keep_last 7 → the three oldest go.
	for _, want := range []string{"20260901T000000Z-backup", "20260902T000000Z-backup", "20260903T000000Z-backup"} {
		if _, ok := removed[want]; !ok {
			t.Errorf("%s is past keep_last 7 and was not listed; removals = %v", want, removed)
		}
	}
	// The newest verified is never a candidate, and neither is an unverified
	// bundle — nothing has proved it, so nothing may propose deleting it.
	for _, never := range []string{"20260910T000000Z-backup", "20260920T000000Z-backup"} {
		if why, ok := removed[never]; ok {
			t.Errorf("%s must never be pruned; it was listed as %q", never, why)
		}
	}
	// pre-update keeps 2 of 3.
	if _, ok := removed["20260801T000000Z-pre-update"]; !ok {
		t.Errorf("the oldest pre-update is past keep_last 2; removals = %v", removed)
	}
	if plan.DryRun != true {
		t.Error("the result must say it was a dry run")
	}
	// And nothing was actually removed.
	for id := range removed {
		if _, err := os.Stat(filepath.Join(root, "backups", "tenants", "dev", id)); err != nil {
			t.Errorf("%s was DELETED by a dry run: %v", id, err)
		}
	}
}

func TestBackupPruneListsAnOldPartialAndSparesAFreshOne(t *testing.T) {
	root := t.TempDir()
	old := filepath.Join(root, "backups", "tenants", "dev", "20260901T000000Z-backup.partial")
	fresh := filepath.Join(root, "backups", "tenants", "dev", "20260914T093000Z-backup.partial")
	for _, dir := range []string{old, fresh} {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.Chtimes(old, time.Now().Add(-48*time.Hour), time.Now().Add(-48*time.Hour)); err != nil {
		t.Fatal(err)
	}
	_, out, _ := capture(t, "--rag-root", root, "--json", "backup", "prune", "dev", "--dry-run")
	var plan prunePlanResult
	if err := json.Unmarshal([]byte(out), &plan); err != nil {
		t.Fatalf("not JSON: %v\n%s", err, out)
	}
	if len(plan.Remove) != 1 || plan.Remove[0].ID != "20260901T000000Z-backup.partial" {
		t.Fatalf("removals = %v, want only the stale .partial", plan.Remove)
	}
	if !strings.Contains(plan.Remove[0].Why, "interrupted backup") {
		t.Errorf("why = %q", plan.Remove[0].Why)
	}
	for _, k := range plan.Keep {
		if k.ID == "20260914T093000Z-backup.partial" && !strings.Contains(k.Why, "may still belong to a job") {
			t.Errorf("a fresh .partial must be spared for the running job: %q", k.Why)
		}
	}
}
