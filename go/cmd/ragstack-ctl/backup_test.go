package main

import (
	"archive/tar"
	"bytes"
	"crypto/sha256"
	"database/sql"
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
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"

	_ "modernc.org/sqlite"
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
	stamp, _, _ := strings.Cut(id, "-")
	esDir := filepath.Join("elasticsearch", "snapshots", id)
	files := map[string][]byte{
		"MIGRATE.md":        []byte("# " + tenant + "\n"),
		"registry-row.json": []byte(`{"name":"` + tenant + `"}`),
		"config/tenant.env": []byte("LOG_LEVEL=INFO\n"),
		// A snapshot that IS a tar with entries, and a repository that IS one:
		// `backup verify` reads both structurally.
		"qdrant/docs/docs-1.snapshot":                       tarOfOne(t, "config.json", []byte(`{"collection":"docs"}`)),
		filepath.Join(esDir, "index-0"):                     []byte(`{"snapshots":[{"name":"s","uuid":"u"}],"indices":{"dev-chunks":{"id":"Xyz1","snapshots":["u"]}}}`),
		filepath.Join(esDir, "index.latest"):                {0, 0, 0, 0, 0, 0, 0, 0},
		filepath.Join(esDir, "indices", "Xyz1", "0", "__a"): []byte("segment bytes"),
	}
	parts := map[string]any{
		"qdrant": map[string]any{"ownership": "exclusive", "url": "http://127.0.0.1:24041",
			"collections": []map[string]any{{"name": "docs", "points_before": 12, "points_after": 12,
				"included": true, "file": "qdrant/docs/docs-1.snapshot", "sha256": nil}},
			"inventory": []string{"docs"}, "warnings": []string{}},
		"elasticsearch": map[string]any{"ownership": "exclusive", "repo": "ctl-" + stamp, "complete": true,
			"files": []string{filepath.Join(esDir, "index-0")}},
		"sqlite-ragstack_users.db": map[string]any{"present": true, "entry": map[string]any{"file": "state/ragstack_users.db"}},
		"config":                   map[string]any{"files": []map[string]any{{"file": "config/tenant.env"}}},
	}
	for name, v := range parts {
		b, _ := json.Marshal(v)
		files[filepath.Join("parts", name+".json")] = b
	}
	for rel, body := range files {
		path := filepath.Join(dir, rel)
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, body, 0o640); err != nil {
			t.Fatal(err)
		}
	}
	// A REAL SQLite copy: the check runs PRAGMA integrity_check on it.
	dbPath := filepath.Join(dir, "state", "ragstack_users.db")
	if err := os.MkdirAll(filepath.Dir(dbPath), 0o755); err != nil {
		t.Fatal(err)
	}
	db, err := sql.Open("sqlite", "file:"+dbPath)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT); INSERT INTO users (name) VALUES ('a'), ('b')"); err != nil {
		t.Fatal(err)
	}
	db.Close()

	rels := []string{}
	if err := filepath.Walk(dir, func(path string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() {
			return err
		}
		rel, _ := filepath.Rel(dir, path)
		rels = append(rels, rel)
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	sort.Strings(rels)
	var sums strings.Builder
	for _, rel := range rels {
		b, err := os.ReadFile(filepath.Join(dir, rel))
		if err != nil {
			t.Fatal(err)
		}
		sum := sha256.Sum256(b)
		fmt.Fprintf(&sums, "%s  %s\n", hex.EncodeToString(sum[:]), rel)
	}
	if err := os.WriteFile(filepath.Join(dir, "SHA256SUMS"), []byte(sums.String()), 0o640); err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256([]byte(sums.String()))
	zero := strings.Repeat("0", 64)
	img := map[string]any{"version": "unpinned", "digest": "sha256:" + zero}

	// A manifest the CONTRACT accepts: `backup verify` validates it against
	// bundle_manifest.json, not merely for its required keys.
	man := map[string]any{
		"schema_version": 1, "kind": o.kind, "scope": []string{"config", "state", "stores"},
		"bundle_id": id, "created_at": o.created, "created_by": "local:1000", "ctl_version": "test",
		"fenced": o.fenced, "best_effort": !o.fenced,
		"tenant":            map[string]any{"name": tenant, "manifest_name": tenant, "ports": map[string]any{}},
		"artifact":          map[string]any{"id": nil, "sha": nil, "tag": "untracked"},
		"python_env":        map[string]any{"path_rel": "envs/ragstack", "lockhash": zero},
		"images":            map[string]any{"qdrant": img, "elasticsearch": img},
		"paths_relative_to": "RAG_ROOT",
		"inventory": map[string]any{"collections": []string{"docs"}, "indices": []string{"dev-chunks"},
			"aliases": []string{}, "sqlite": []string{"ragstack_users.db"}},
		"stores": map[string]any{
			"qdrant": map[string]any{"ownership": "exclusive", "url": "http://127.0.0.1:24041",
				"collections": []map[string]any{{"name": "docs", "points_before": 12, "points_after": 12,
					"included": true, "file": "qdrant/docs/docs-1.snapshot", "sha256": nil}}},
			"elasticsearch": map[string]any{"ownership": "exclusive", "repo_type": "fs", "repo": "ctl-" + stamp,
				"snapshot": strings.ToLower(id), "complete": true, "files": []string{filepath.Join(esDir, "index-0")},
				"indices": []map[string]any{{"name": "dev-chunks", "docs_before": 3, "docs_after": 3, "included": true}}},
			"neo4j":    map[string]any{"ownership": "external", "included": false},
			"postgres": map[string]any{"kind": "sqlite", "ownership": "exclusive", "included": false, "file": nil, "sha256": nil},
		},
		"sqlite": []map[string]any{{"env_key": "USER_STORE_PATH", "path_rel": "data/tenants/" + tenant + "/state/ragstack_users.db",
			"file": "state/ragstack_users.db", "bytes": 1, "sha256": zero, "integrity_check": "ok"}},
		"files":    []map[string]any{},
		"external": []map[string]any{},
		"secrets": map[string]any{"encrypted": true, "included": false, "file": nil, "recipients_file": nil,
			"key_fingerprints": []string{}},
		"units":        []map[string]any{},
		"registry_row": map[string]any{"name": tenant},
		"migrate_md":   "MIGRATE.md",
		"consistent":   o.fenced, "verified": o.verified, "checked": false,
		"warnings":   []string{},
		"sha256sums": hex.EncodeToString(digest[:]),
	}
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
		b, err := os.ReadFile(filepath.Join(dir, o.corrupt))
		if err != nil {
			t.Fatal(err)
		}
		b[len(b)/2] ^= 0x01 // one byte
		if err := os.WriteFile(filepath.Join(dir, o.corrupt), b, 0o640); err != nil {
			t.Fatal(err)
		}
	}
	return dir
}

// tarOfOne is a one-entry tar.
func tarOfOne(t *testing.T, name string, body []byte) []byte {
	t.Helper()
	var buf bytes.Buffer
	tw := tar.NewWriter(&buf)
	if err := tw.WriteHeader(&tar.Header{Name: name, Mode: 0o640, Size: int64(len(body)), Typeflag: tar.TypeReg}); err != nil {
		t.Fatal(err)
	}
	if _, err := tw.Write(body); err != nil {
		t.Fatal(err)
	}
	if err := tw.Close(); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
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
		{"a changed file", bundleOpts{kind: "backup", fenced: true, corrupt: "MIGRATE.md"}, "MIGRATE.md hashes to"},
		{"a changed snapshot", bundleOpts{kind: "backup", fenced: true, corrupt: "qdrant/docs/docs-1.snapshot"},
			"qdrant/docs/docs-1.snapshot hashes to"},
		{"an uncovered file", bundleOpts{kind: "backup", fenced: true, extraFile: "surprise.txt"}, "surprise.txt, which SHA256SUMS does not cover"},
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

// The three reads take a tenant NAME from the command line and build a path
// out of it, so the name is checked against the same grammar the registry
// enforces before it is joined under `/rag/backups/tenants` — and the bundle
// id, which is the second half of that path, is checked for containment.
func TestBackupReadsRefuseANameThatIsNotATenant(t *testing.T) {
	root := t.TempDir()
	for _, c := range []struct {
		name string
		args []string
	}{
		{"list", []string{"backup", "list", "../../etc", "--rag-root", root}},
		{"verify", []string{"backup", "verify", "../../etc", "passwd", "--rag-root", root}},
		{"verify-id", []string{"backup", "verify", "dev", "../../../etc", "--rag-root", root}},
		{"prune", []string{"backup", "prune", "..", "--dry-run", "--rag-root", root}},
	} {
		t.Run(c.name, func(t *testing.T) {
			rc, out, errs := capture(t, c.args...)
			if rc != exitUsage {
				t.Fatalf("rc %d, want %d (out %q, err %q)", rc, exitUsage, out, errs)
			}
			if errs == "" {
				t.Errorf("the refusal says nothing")
			}
		})
	}
	// …and a real name still works: the check is a grammar, not a whitelist.
	if rc, _, errs := capture(t, "backup", "list", "dev", "--rag-root", root); rc != exitOK {
		t.Errorf("listing a tenant with no bundles = %d (%s)", rc, errs)
	}
}

// seedRegistry writes a registry under root whose dev row names bundle id as
// its last backup, fenced and neither checked nor verified.
func seedRegistry(t *testing.T, root, id string) string {
	t.Helper()
	roots := paths.NewRoots(root, paths.Overrides{})
	if err := os.MkdirAll(filepath.Dir(roots.Registry()), 0o755); err != nil {
		t.Fatal(err)
	}
	f := registry.LiveFixture()
	f.Tenants["dev"].LastBackup = &registry.BackupRecord{
		Bundle: filepath.Join(roots.BackupsDir, "dev", id), At: "2026-09-14T09:30:00Z", Kind: "backup",
		Fenced: true, Scope: []string{"config", "state", "stores"},
	}
	if err := registry.Save(roots.Registry(), f, "test"); err != nil {
		t.Fatal(err)
	}
	return roots.Registry()
}

func manifestChecked(t *testing.T, dir string) any {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(dir, "manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	var man map[string]any
	if err := json.Unmarshal(b, &man); err != nil {
		t.Fatal(err)
	}
	return man["checked"]
}

func TestBackupVerifyMarksTheBundleAndTheRegistryChecked(t *testing.T) {
	root := t.TempDir()
	id := "20260914T093000Z-backup"
	dir := writeBundle(t, root, "dev", id, bundleOpts{kind: "backup", fenced: true, created: "2026-09-14T09:30:00Z"})
	reg := seedRegistry(t, root, id)

	rc, out, errs := capture(t, "--rag-root", root, "backup", "verify", "dev", id)
	if rc != exitOK {
		t.Fatalf("rc %d: %s\n%s", rc, errs, out)
	}
	// Every leg is printed with its verdict.
	for _, leg := range []string{ops.CheckLegManifest, ops.CheckLegChecksums, ops.CheckLegParts, ops.CheckLegQdrant,
		ops.CheckLegElasticsearch, ops.CheckLegPostgres, ops.CheckLegSQLite} {
		if !strings.Contains(out, "ok    "+leg) {
			t.Errorf("no verdict for the %s leg:\n%s", leg, out)
		}
	}
	if manifestChecked(t, dir) != true {
		t.Errorf("manifest checked = %v after a passing verify", manifestChecked(t, dir))
	}
	f, err := registry.LoadNoRepair(reg)
	if err != nil {
		t.Fatal(err)
	}
	if lb := f.Tenants["dev"].LastBackup; !lb.Checked || lb.Verified {
		t.Errorf("last_backup = %+v, want checked and not verified", lb)
	}
	if !strings.Contains(out, "dev's last_backup is checked") {
		t.Errorf("the output does not say the registry was marked:\n%s", out)
	}
	// The SHA256SUMS still hold: marking the manifest changed nothing they cover.
	if rc, _, errs := capture(t, "--rag-root", root, "backup", "verify", "dev", id); rc != exitOK {
		t.Errorf("a second verify of the marked bundle failed: %s", errs)
	}
	// And `backup list` shows it.
	_, out, _ = capture(t, "--rag-root", root, "--json", "backup", "list", "dev")
	if !strings.Contains(out, `"checked": true`) {
		t.Errorf("backup list does not show the bundle checked:\n%s", out)
	}
}

func TestBackupVerifyLeavesAnotherBundlesRowAlone(t *testing.T) {
	root := t.TempDir()
	old := "20260901T000000Z-backup"
	dir := writeBundle(t, root, "dev", old, bundleOpts{kind: "backup", fenced: true, created: "2026-09-01T00:00:00Z"})
	reg := seedRegistry(t, root, "20260914T093000Z-backup")
	rc, out, errs := capture(t, "--rag-root", root, "backup", "verify", "dev", old)
	if rc != exitOK {
		t.Fatalf("rc %d: %s", rc, errs)
	}
	if manifestChecked(t, dir) != true {
		t.Error("the older bundle's manifest was not marked")
	}
	f, _ := registry.LoadNoRepair(reg)
	if f.Tenants["dev"].LastBackup.Checked {
		t.Error("verifying an OLDER bundle marked the row's newer last_backup checked")
	}
	if !strings.Contains(out, "left alone") {
		t.Errorf("the output does not say the row was left alone:\n%s", out)
	}
}

func TestBackupVerifyOfATamperedBundleMarksNothing(t *testing.T) {
	root := t.TempDir()
	id := "20260914T093000Z-backup"
	dir := writeBundle(t, root, "dev", id, bundleOpts{kind: "backup", fenced: true, corrupt: "state/ragstack_users.db"})
	reg := seedRegistry(t, root, id)
	rc, _, errs := capture(t, "--rag-root", root, "--json", "backup", "verify", "dev", id)
	if rc != exitError {
		t.Fatalf("rc %d, want %d", rc, exitError)
	}
	_ = errs
	if manifestChecked(t, dir) != false {
		t.Errorf("a tampered bundle's manifest says checked = %v", manifestChecked(t, dir))
	}
	f, _ := registry.LoadNoRepair(reg)
	if f.Tenants["dev"].LastBackup.Checked {
		t.Error("a tampered bundle marked the registry checked")
	}
}

// A structural failure the hashes cannot see: a snapshot that was never a tar,
// checksummed as it is.
func TestBackupVerifyReadsTheSnapshotAsATar(t *testing.T) {
	root := t.TempDir()
	id := "20260914T093000Z-backup"
	dir := writeBundle(t, root, "dev", id, bundleOpts{kind: "backup", fenced: true})
	snap := filepath.Join(dir, "qdrant", "docs", "docs-1.snapshot")
	if err := os.WriteFile(snap, []byte(strings.Repeat("not a tar ", 200)), 0o640); err != nil {
		t.Fatal(err)
	}
	// Re-seal the bundle around the bad file, so only the structure leg objects.
	resealBundle(t, dir)
	rc, _, errs := capture(t, "--rag-root", root, "backup", "verify", "dev", id)
	if rc != exitError || !strings.Contains(errs, "collection docs") {
		t.Fatalf("rc %d, stderr %q; want a failure naming collection docs", rc, errs)
	}
}

// resealBundle recomputes SHA256SUMS and the manifest's digest of it.
func resealBundle(t *testing.T, dir string) {
	t.Helper()
	var rels []string
	_ = filepath.Walk(dir, func(path string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() {
			return err
		}
		rel, _ := filepath.Rel(dir, path)
		if rel != "SHA256SUMS" && rel != "manifest.json" {
			rels = append(rels, rel)
		}
		return nil
	})
	sort.Strings(rels)
	var sums strings.Builder
	for _, rel := range rels {
		b, _ := os.ReadFile(filepath.Join(dir, rel))
		sum := sha256.Sum256(b)
		fmt.Fprintf(&sums, "%s  %s\n", hex.EncodeToString(sum[:]), rel)
	}
	if err := os.WriteFile(filepath.Join(dir, "SHA256SUMS"), []byte(sums.String()), 0o640); err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(filepath.Join(dir, "manifest.json"))
	var man map[string]any
	_ = json.Unmarshal(b, &man)
	digest := sha256.Sum256([]byte(sums.String()))
	man["sha256sums"] = hex.EncodeToString(digest[:])
	out, _ := json.MarshalIndent(man, "", "  ")
	if err := os.WriteFile(filepath.Join(dir, "manifest.json"), out, 0o640); err != nil {
		t.Fatal(err)
	}
}
