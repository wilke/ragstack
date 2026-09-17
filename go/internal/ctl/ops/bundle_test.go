package ops

// The bundle, end to end, against the contract that describes it.
//
// The manifest is the one document in this system a RESTORE reads back months
// later, on another host, with nothing else to go on. Asserting that it has
// "the fields we meant" in Go would be asserting the code against itself, so
// these tests read contracts/ctl/schemas/bundle_manifest.json and check the
// real document against it: every `required` key present at every level, no
// member the schema does not know (`additionalProperties: false`), and the
// patterns and consts the schema spells.
//
// It is a small structural validator rather than a JSON-Schema library because
// the repository has no Go validator wired and because the subset the bundle
// manifest uses is exactly this: objects, arrays, required, additionalProperties,
// const, enum, pattern, type and oneOf-with-null. A cross-file `$ref`
// (registry.json's Tenant, Ports, Sha256Hex) is followed where this file can
// resolve it and reported as unchecked where it cannot — and the one that
// matters, the registry row, is validated by the registry package's own
// contract test.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// ---------------------------------------------------------------- helpers

// runBackup plans and runs a backup against the fakes and returns the files
// the bundle ended up holding.
func runBackup(t *testing.T, oc jobs.Context, fake *drivers.Fake, args map[string]any) (*jobs.Planned, *runner) {
	t.Helper()
	p := plan(t, oc, "backup", args)
	r := newRunner(oc, fake)
	r.runAll(t, p)
	return p, r
}

// bundlePath is where the fixture's bundle lands.
func bundlePath(tenant string, rel ...string) string {
	return filepath.Join(append([]string{"/rag/backups/tenants", tenant, "20260914T093000Z-backup"}, rel...)...)
}

// seedState puts the four state databases on the fixture host, so the sqlite
// leg copies rather than skipping.
func seedState(fake *drivers.Fake, tenant string) {
	for _, db := range sqliteDBs {
		fake.FakeFiles().Put("/rag/data/tenants/"+tenant+"/state/"+db.File, []byte("sqlite-"+db.File), 0o640)
	}
}

// fakeSealer is a Sealer that does not encrypt: it wraps the plaintext in a
// recognisable envelope. The ops package's business is what it seals and when,
// not how — filippo.io/age is tested in internal/ctl/seal.
type fakeSealer struct {
	fps   []string
	empty bool
}

func (f fakeSealer) Fingerprints() []string { return f.fps }

func (f fakeSealer) Seal(plaintext []byte) ([]byte, error) {
	if f.empty {
		return nil, ErrNoRecipients
	}
	return append([]byte("age-encrypted-to:"+strings.Join(f.fps, ",")+"\n"), plaintext...), nil
}

// planWith is plan() with op Deps a test chose (a sealer, a mirror).
func planWith(t *testing.T, oc jobs.Context, d Deps, verb string, args map[string]any) *jobs.Planned {
	t.Helper()
	d.Roots, d.Now = oc.Roots, oc.Now
	if d.SaveFleet == nil {
		d.SaveFleet = func(f *registry.Fleet) error { f.Generation++; return nil }
	}
	op, ok := NewRegistry(d).Lookup(verb)
	if !ok {
		t.Fatalf("no op %q", verb)
	}
	p, err := op.Plan(context.Background(), oc, args)
	if err != nil {
		t.Fatalf("%s: %v", verb, err)
	}
	return p
}

// ---------------------------------------------------------------- the manifest

func TestTheBundleManifestMatchesItsContract(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	seedState(fake, "dev")
	fake.FakeFiles().Put("/rag/data/tenants/dev/config/provision.env", []byte("TENANT_STORE_KIND=sqlite\n"), 0o640)
	fake.FakeFiles().Put("/rag/data/tenants/dev/manifests/dev_g1.json", []byte(`{"name":"dev_g1"}`), 0o640)
	runBackup(t, oc, fake, map[string]any{"fence": true})

	body := fake.FakeFiles().Content(bundlePath("dev", "manifest.json"))
	if body == nil {
		t.Fatalf("no manifest in the bundle; it holds %v", bundleFiles(fake))
	}
	var man map[string]any
	if err := json.Unmarshal(body, &man); err != nil {
		t.Fatalf("the manifest is not JSON: %v", err)
	}
	for _, problem := range validateAgainstSchema(t, man) {
		t.Errorf("manifest: %s", problem)
	}

	// The facts a restore reads, spelled out: this is the contract D2 builds
	// against, so a change here is a change to somebody else's code.
	if man["bundle_id"] != "20260914T093000Z-backup" || man["kind"] != "backup" {
		t.Errorf("bundle_id/kind = %v/%v", man["bundle_id"], man["kind"])
	}
	if man["fenced"] != true || man["best_effort"] != false || man["consistent"] != true || man["verified"] != false {
		t.Errorf("fenced=%v best_effort=%v consistent=%v verified=%v",
			man["fenced"], man["best_effort"], man["consistent"], man["verified"])
	}
	stores := man["stores"].(map[string]any)
	cols := stores["qdrant"].(map[string]any)["collections"].([]any)
	if len(cols) != 2 {
		t.Fatalf("collections = %v", cols)
	}
	first := cols[0].(map[string]any)
	if first["points_before"] != first["points_after"] {
		t.Errorf("a fenced bundle recorded a moving count: %v", first)
	}
	if file, _ := first["file"].(string); !strings.HasPrefix(file, "qdrant/") {
		t.Errorf("collection file = %v, want a path inside the bundle", first["file"])
	}
	es := stores["elasticsearch"].(map[string]any)
	// The snapshot name is the bundle id LOWERCASED: ES refuses uppercase in
	// a snapshot name, and the stamp carries T and Z.
	if es["repo"] != "ctl-20260914T093000Z" || es["snapshot"] != "20260914t093000z-backup" || es["complete"] != true {
		t.Errorf("elasticsearch = %v", es)
	}
	if pg := stores["postgres"].(map[string]any); pg["kind"] != "sqlite" || pg["included"] != false {
		t.Errorf("postgres = %v", pg)
	}
	if sec := man["secrets"].(map[string]any); sec["included"] != false || sec["file"] != nil {
		t.Errorf("with no sealer the bundle must carry no secrets: %v", sec)
	}
	if n := len(man["sqlite"].([]any)); n != len(sqliteDBs) {
		t.Errorf("sqlite entries = %d, want %d", n, len(sqliteDBs))
	}
	if man["migrate_md"] != "MIGRATE.md" {
		t.Errorf("migrate_md = %v", man["migrate_md"])
	}
}

// TestTheBundleIsRelocatable is the plan's "no ^/rag/ or ^/home/ string
// anywhere except the registry row": a bundle is copied to another host, and a
// manifest full of this host's paths is a manifest that lies there.
func TestTheBundleIsRelocatable(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	seedState(fake, "dev")
	runBackup(t, oc, fake, map[string]any{"fence": true})
	var man map[string]any
	if err := json.Unmarshal(fake.FakeFiles().Content(bundlePath("dev", "manifest.json")), &man); err != nil {
		t.Fatal(err)
	}
	// The registry row is the documented exception: it records where the
	// tenant WAS, which is exactly what a migration needs to know.
	delete(man, "registry_row")
	walkStrings(man, "$", func(path, v string) {
		if strings.HasPrefix(v, "/rag/") || strings.HasPrefix(v, "/home/") {
			t.Errorf("%s = %q: a bundle must carry no absolute host path outside registry_row", path, v)
		}
	})
}

// TestSHA256SUMSCoversEveryFileInTheBundle is what `backup verify` re-runs: a
// file the checksum list forgot is a file nobody would notice being changed.
func TestSHA256SUMSCoversEveryFileInTheBundle(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	seedState(fake, "dev")
	runBackup(t, oc, fake, map[string]any{"fence": true})

	listed := map[string]string{}
	for _, line := range strings.Split(string(fake.FakeFiles().Content(bundlePath("dev", "SHA256SUMS"))), "\n") {
		if line == "" {
			continue
		}
		sum, rel, ok := strings.Cut(line, "  ")
		if !ok || len(sum) != 64 {
			t.Fatalf("SHA256SUMS line %q is not `<hex>  <relpath>`", line)
		}
		listed[rel] = sum
	}
	for _, rel := range bundleFiles(fake) {
		if rel == "SHA256SUMS" || rel == "manifest.json" {
			continue
		}
		sum, ok := listed[rel]
		if !ok {
			t.Errorf("%s is in the bundle and not in SHA256SUMS", rel)
			continue
		}
		got, _, err := fake.Files().Sha256(context.Background(), bundlePath("dev", rel))
		if err != nil || got != sum {
			t.Errorf("%s: SHA256SUMS says %s, the file hashes to %s (%v)", rel, sum, got, err)
		}
		delete(listed, rel)
	}
	for rel := range listed {
		t.Errorf("SHA256SUMS lists %s, which is not in the bundle", rel)
	}
}

// TestAnInterruptedBackupLeavesAPartialDirectory: the rename is the last
// write, so a job that died anywhere before it leaves a directory no reader
// mistakes for a finished bundle.
func TestAnInterruptedBackupLeavesAPartialDirectory(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	seedState(fake, "dev")
	p := plan(t, oc, "backup", map[string]any{"fence": true})
	r := newRunner(oc, fake)
	for _, s := range p.Steps {
		if strings.Contains(s.Plan.Title, "rename the bundle into place") {
			break
		}
		if _, err := r.run(s); err != nil {
			t.Fatalf("step %d (%s): %v", s.Plan.N, s.Plan.Title, err)
		}
	}
	partial, final := 0, 0
	for _, path := range fake.FakeFiles().Paths() {
		switch {
		case strings.Contains(path, "20260914T093000Z-backup.partial/"):
			partial++
		case strings.Contains(path, "20260914T093000Z-backup/"):
			final++
		}
	}
	if partial == 0 || final != 0 {
		t.Errorf("an interrupted backup left %d partial and %d finished file(s); it must leave only .partial",
			partial, final)
	}
}

// TestASealedBundleCarriesTheSecretsAndNeverTheirValues: with recipients the
// payload is written, and nothing about it — not the plan, not the step log,
// not the manifest — carries a credential.
func TestASealedBundleCarriesTheSecretsAndNeverTheirValues(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	seedState(fake, "dev")
	fake.FakeFiles().Put("/rag/data/tenants/dev/config/secrets.env.bak-20260101T000000Z", ledgerEnv(), 0o600)
	sealer := fakeSealer{fps: []string{"age1qqqqexample"}}
	p := planWith(t, oc, Deps{Sealer: sealer}, "backup", map[string]any{"fence": true})
	r := newRunner(oc, fake)
	r.runAll(t, p)

	sealed := fake.FakeFiles().Content(bundlePath("dev", "secrets.age"))
	if sealed == nil {
		t.Fatalf("no secrets.age in the bundle; it holds %v", bundleFiles(fake))
	}
	if !strings.HasPrefix(string(sealed), "age-encrypted-to:") {
		t.Errorf("secrets.age was not sealed: %q", string(sealed)[:40])
	}
	// Both the current file and its historical sibling are in the payload:
	// a restore that reconciles revoked keys needs the history.
	for _, want := range []string{"secrets.env", "secrets.env.bak-20260101T000000Z"} {
		if !strings.Contains(string(sealed), want) {
			t.Errorf("the payload does not carry %s", want)
		}
	}
	// The manifest says where it is and who can open it, and never a value.
	var man map[string]any
	if err := json.Unmarshal(fake.FakeFiles().Content(bundlePath("dev", "manifest.json")), &man); err != nil {
		t.Fatal(err)
	}
	sec := man["secrets"].(map[string]any)
	if sec["included"] != true || sec["file"] != "secrets.age" || sec["encrypted"] != true {
		t.Errorf("secrets = %v", sec)
	}
	if sec["recipients_file"] != "config/ctl/backup-recipients.txt" {
		t.Errorf("recipients_file = %v", sec["recipients_file"])
	}
	// The canary: the fixture's key value must appear nowhere in the plan.
	for _, s := range p.Steps {
		for _, w := range s.Plan.WouldWrite {
			if strings.Contains(string(w.Preview), testSecret) {
				t.Errorf("step %q previews a credential", s.Plan.Title)
			}
		}
		if strings.Contains(strings.Join(s.Plan.Targets, " "), testSecret) {
			t.Errorf("step %q names a credential in its targets", s.Plan.Title)
		}
	}
	for _, problem := range validateAgainstSchema(t, man) {
		t.Errorf("manifest: %s", problem)
	}
}

// TestABundleWithoutRecipientsExcludesTheSecretsAndSaysSoInThePlan is the
// fail-closed rule, asserted where an operator would see it: in the PLAN,
// before the backup runs.
func TestABundleWithoutRecipientsExcludesTheSecretsAndSaysSoInThePlan(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	for _, d := range []Deps{{}, {Sealer: fakeSealer{}}} {
		p := planWith(t, oc, d, "backup", map[string]any{"fence": true})
		found := false
		for _, w := range p.Plan.Warnings {
			if strings.Contains(w, "no age recipient is configured") && strings.Contains(w, "EXCLUDED") {
				found = true
			}
		}
		if !found {
			t.Errorf("the plan does not warn that the secrets are excluded: %v", p.Plan.Warnings)
		}
		if !hasStep(p, "fs", "skip the encrypted secrets payload") {
			t.Errorf("no skip step: %v", titles(p))
		}
	}
	// And a bundle taken that way holds no secrets.age at all.
	p := planWith(t, oc, Deps{}, "backup", nil)
	newRunner(oc, fake).runAll(t, p)
	for _, rel := range bundleFiles(fake) {
		if strings.Contains(rel, "secrets") && !strings.HasPrefix(rel, "parts/") {
			t.Errorf("a bundle with no recipients carries %s", rel)
		}
	}
}

// TestTheBackupRecordsItselfInTheRegistry: the row learns its recovery point,
// and learns that nothing has proved it.
func TestTheBackupRecordsItselfInTheRegistry(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	saved := 0
	p := planWith(t, oc, Deps{SaveFleet: func(f *registry.Fleet) error { saved++; f.Generation++; return nil }},
		"backup", map[string]any{"fence": true})
	newRunner(oc, fake).runAll(t, p)

	if saved != 1 {
		t.Fatalf("the registry was saved %d times; a backup writes one generation", saved)
	}
	row := oc.Fleet.Tenants["dev"]
	if row.LastBackup == nil {
		t.Fatal("last_backup is still null")
	}
	// The absolute directory, which is what registry.json's AbsPath types —
	// not the id, which is what the CLI and the manifest speak.
	if row.LastBackup.Bundle != bundlePath("dev") || !row.LastBackup.Fenced ||
		row.LastBackup.Verified || row.LastBackup.Kind != "backup" {
		t.Errorf("last_backup = %+v", row.LastBackup)
	}
	if got := row.LastOps["backup"]; got.Outcome != "succeeded" || got.At == "" {
		t.Errorf("last_ops.backup = %+v", got)
	}
}

// TestTheTarIsOnlyMadeWhenItIsAskedFor, and of the FINISHED directory.
func TestTheTarIsOnlyMadeWhenItIsAskedFor(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	p := plan(t, oc, "backup", map[string]any{"fence": true})
	if hasStep(p, "fs", "tar the finished bundle") {
		t.Errorf("a backup without --tar planned one: %v", titles(p))
	}
	p = plan(t, oc, "backup", map[string]any{"fence": true, "tar": true})
	newRunner(oc, fake).runAll(t, p)
	created := fake.FakeArchive().Created
	if len(created) != 1 || !strings.HasPrefix(created[0], bundlePath("dev")+" ") {
		t.Fatalf("archives = %v, want one of the finished bundle directory", created)
	}
	if strings.Contains(created[0], partialSuffix) {
		t.Errorf("the tar was taken of the .partial directory: %s", created[0])
	}
}

// TestThePostgresLegIsPlannedOnlyForAPostgresLocalTenant.
func TestThePostgresLegIsPlannedOnlyForAPostgresLocalTenant(t *testing.T) {
	oc, _ := fixture(t, "dev", managed)
	if !hasStep(plan(t, oc, "backup", nil), "postgres", "skip the postgres leg") {
		t.Error("a sqlite tenant must SKIP the postgres leg, visibly")
	}

	oc, fake := fixture(t, "dev", func(tn *registry.Tenant) {
		managed(tn)
		tn.Stores.Postgres = registry.Postgres{
			Kind: registry.PostgresKindLocal, Ownership: registry.OwnershipExclusive,
			URL: "postgresql://localhost:24045", Port: registry.NullPort(24045),
			Instance: "postgres-dev", SIF: "/rag/apptainer/images/postgres.sif",
			DataDir: "/rag/data/tenants/dev/postgres",
		}
	})
	p := plan(t, oc, "backup", map[string]any{"fence": true})
	if !hasStep(p, "postgres", "dump the tenant's postgres database") {
		t.Fatalf("no postgres leg for a postgres-local tenant: %v", titles(p))
	}
	newRunner(oc, fake).runAll(t, p)
	if got := fake.FakePostgres().Dumps; len(got) != 1 ||
		!strings.HasSuffix(got[0], bundlePath("dev", "postgres", "dev.dump")+partialSuffix) &&
			!strings.Contains(got[0], "postgres/dev.dump") {
		t.Fatalf("dumps = %v", got)
	}
	var man map[string]any
	if err := json.Unmarshal(fake.FakeFiles().Content(bundlePath("dev", "manifest.json")), &man); err != nil {
		t.Fatal(err)
	}
	pg := man["stores"].(map[string]any)["postgres"].(map[string]any)
	if pg["kind"] != "local" || pg["included"] != true || pg["file"] != "postgres/dev.dump" {
		t.Errorf("stores.postgres = %v", pg)
	}
	if sum, _ := pg["sha256"].(string); len(sum) != 64 {
		t.Errorf("the dump is not checksummed: %v", pg["sha256"])
	}
	for _, problem := range validateAgainstSchema(t, man) {
		t.Errorf("manifest: %s", problem)
	}
}

// TestAnUnfencedBundleIsNeverConsistent: best-effort means what it says.
func TestAnUnfencedBundleIsNeverConsistent(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	p := plan(t, oc, "backup", nil)
	newRunner(oc, fake).runAll(t, p)
	var man map[string]any
	if err := json.Unmarshal(fake.FakeFiles().Content(bundlePath("dev", "manifest.json")), &man); err != nil {
		t.Fatal(err)
	}
	if man["fenced"] != false || man["best_effort"] != true || man["consistent"] != false {
		t.Errorf("an unfenced bundle: fenced=%v best_effort=%v consistent=%v",
			man["fenced"], man["best_effort"], man["consistent"])
	}
	for _, problem := range validateAgainstSchema(t, man) {
		t.Errorf("manifest: %s", problem)
	}
}

// TestTheFreeSpacePrecheckRefusesBeforeTheFence.
func TestTheFreeSpacePrecheckRefusesBeforeTheFence(t *testing.T) {
	oc, fake := fixtureDisk(t, 1<<20)
	p := plan(t, oc, "backup", map[string]any{"fence": true})
	if p.Steps[0].Plan.Kind != "probe" || !strings.Contains(p.Steps[0].Plan.Title, "room") {
		t.Fatalf("the precheck is not the first step: %v", titles(p))
	}
	r := newRunner(oc, fake)
	_, err := r.run(p.Steps[0])
	if err == nil || !strings.Contains(err.Error(), "recovery reserve") {
		t.Fatalf("a full filesystem = %v, want a refusal naming the reserve", err)
	}
	if len(fake.FakeGateway().Applies) != 0 {
		t.Error("the gateway was touched before the precheck refused")
	}
}

// TestMigrateMDIsWrittenAndReadable: the page a human gets when the bundle is
// all there is.
func TestMigrateMDIsWrittenAndReadable(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	seedState(fake, "dev")
	runBackup(t, oc, fake, map[string]any{"fence": true})
	md := string(fake.FakeFiles().Content(bundlePath("dev", "MIGRATE.md")))
	if md == "" {
		t.Fatalf("no MIGRATE.md; the bundle holds %v", bundleFiles(fake))
	}
	for _, want := range []string{
		"# dev — backup bundle 20260914T093000Z-backup",
		"ragstack-ctl backup verify dev 20260914T093000Z-backup",
		"ragstack-ctl tenant restore dev --from 20260914T093000Z-backup --as <fresh-name>",
		"**No secrets are in this bundle.**",
		"`docs`", // the collection inventory
	} {
		if !strings.Contains(md, want) {
			t.Errorf("MIGRATE.md does not contain %q:\n%s", want, md)
		}
	}
	if strings.Contains(md, testSecret) {
		t.Error("MIGRATE.md carries a credential")
	}
}

// ---------------------------------------------------------------- fixtures

// fixtureDisk is the fixture with a nearly-full backup filesystem.
func fixtureDisk(t *testing.T, free int64) (jobs.Context, *drivers.Fake) {
	t.Helper()
	oc, fake := fixture(t, "dev", managed)
	fake.FakeFiles().Free = free
	return oc, fake
}

// bundleFiles lists the finished bundle's files, relative to its root.
func bundleFiles(fake *drivers.Fake) []string {
	prefix := bundlePath("dev") + "/"
	var out []string
	for _, p := range fake.FakeFiles().Paths() {
		if strings.HasPrefix(p, prefix) {
			out = append(out, strings.TrimPrefix(p, prefix))
		}
	}
	sort.Strings(out)
	return out
}

func walkStrings(node any, path string, fn func(path, v string)) {
	switch v := node.(type) {
	case map[string]any:
		for k, child := range v {
			walkStrings(child, path+"."+k, fn)
		}
	case []any:
		for i, child := range v {
			walkStrings(child, fmt.Sprintf("%s[%d]", path, i), fn)
		}
	case string:
		fn(path, v)
	}
}

// TestBundleManifestRequiredIsTheContracts pins the list `backup verify` reads
// to the schema itself: a member added to the contract must become a member
// verify demands, without anybody remembering to copy it across.
func TestBundleManifestRequiredIsTheContracts(t *testing.T) {
	raw, err := os.ReadFile(schemaPath)
	if err != nil {
		t.Fatalf("reading the bundle manifest contract: %v", err)
	}
	var schema map[string]any
	if err := json.Unmarshal(raw, &schema); err != nil {
		t.Fatal(err)
	}
	want := stringsOf(schema["required"])
	if strings.Join(want, ",") != strings.Join(BundleManifestRequired, ",") {
		t.Errorf("BundleManifestRequired =\n  %v\nthe contract says\n  %v", BundleManifestRequired, want)
	}
}

// ---------------------------------------------------------------- the validator

// schemaPath is the contract this package writes against.
const schemaPath = "../../../../contracts/ctl/schemas/bundle_manifest.json"

func validateAgainstSchema(t *testing.T, doc map[string]any) []string {
	t.Helper()
	raw, err := os.ReadFile(schemaPath)
	if err != nil {
		t.Fatalf("reading the bundle manifest contract: %v", err)
	}
	var schema map[string]any
	if err := json.Unmarshal(raw, &schema); err != nil {
		t.Fatalf("the contract is not JSON: %v", err)
	}
	v := &schemaChecker{root: schema}
	v.check(schema, doc, "$")
	sort.Strings(v.problems)
	return v.problems
}

// schemaChecker is the subset of JSON Schema bundle_manifest.json uses.
type schemaChecker struct {
	root     map[string]any
	problems []string
}

func (v *schemaChecker) fail(path, format string, a ...any) {
	v.problems = append(v.problems, path+": "+fmt.Sprintf(format, a...))
}

// resolve follows a local `$ref` ("#/$defs/RelPath"). A cross-file ref is not
// resolvable here and answers nil, which the caller treats as "unchecked".
func (v *schemaChecker) resolve(ref string) map[string]any {
	if !strings.HasPrefix(ref, "#/") {
		return nil
	}
	node := any(v.root)
	for _, seg := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		m, ok := node.(map[string]any)
		if !ok {
			return nil
		}
		node = m[seg]
	}
	out, _ := node.(map[string]any)
	return out
}

func (v *schemaChecker) check(schema map[string]any, doc any, path string) {
	if ref, ok := schema["$ref"].(string); ok {
		if target := v.resolve(ref); target != nil {
			v.check(target, doc, path)
		}
		return
	}
	if opts, ok := schema["oneOf"].([]any); ok {
		// oneOf here is always "<something> or null".
		if doc == nil {
			return
		}
		for _, o := range opts {
			if m, ok := o.(map[string]any); ok && m["type"] != "null" {
				v.check(m, doc, path)
				return
			}
		}
		return
	}
	// `"type": ["string", "null"]` — the contract's other way of spelling
	// nullable, beside oneOf. A null under such a schema is VALID and must not
	// be type-checked against the non-null half, which is what a light bundle's
	// excluded elasticsearch leg (repo: null, snapshot: null) is.
	if doc == nil && nullable(schema) {
		return
	}
	if c, ok := schema["const"]; ok && !sameJSON(c, doc) {
		v.fail(path, "is %v, the contract says %v", doc, c)
	}
	if enum, ok := schema["enum"].([]any); ok {
		found := false
		for _, e := range enum {
			if sameJSON(e, doc) {
				found = true
			}
		}
		if !found {
			v.fail(path, "is %v, outside the enum %v", doc, enum)
		}
	}
	switch typeOf(schema) {
	case "object":
		m, ok := doc.(map[string]any)
		if !ok {
			v.fail(path, "is %T, the contract says object", doc)
			return
		}
		props, _ := schema["properties"].(map[string]any)
		for _, req := range stringsOf(schema["required"]) {
			if _, ok := m[req]; !ok {
				v.fail(path, "is missing the required member %q", req)
			}
		}
		if extra, ok := schema["additionalProperties"].(bool); ok && !extra {
			for k := range m {
				if _, known := props[k]; !known {
					v.fail(path, "carries %q, which the contract does not allow", k)
				}
			}
		}
		for k, sub := range props {
			child, ok := m[k]
			if !ok {
				continue
			}
			if subSchema, ok := sub.(map[string]any); ok {
				v.check(subSchema, child, path+"."+k)
			}
		}
	case "array":
		items, ok := doc.([]any)
		if !ok {
			v.fail(path, "is %T, the contract says array", doc)
			return
		}
		sub, _ := schema["items"].(map[string]any)
		if sub == nil {
			return
		}
		for i, child := range items {
			v.check(sub, child, fmt.Sprintf("%s[%d]", path, i))
		}
	case "string":
		s, ok := doc.(string)
		if !ok {
			v.fail(path, "is %T, the contract says string", doc)
			return
		}
		if pat, ok := schema["pattern"].(string); ok {
			if !regexp.MustCompile(pat).MatchString(s) {
				v.fail(path, "%q does not match %s", s, pat)
			}
		}
		if min, ok := schema["minLength"].(float64); ok && len(s) < int(min) {
			v.fail(path, "%q is shorter than %v", s, min)
		}
	case "integer", "number":
		n, ok := doc.(float64)
		if !ok {
			v.fail(path, "is %T, the contract says a number", doc)
			return
		}
		if min, ok := schema["minimum"].(float64); ok && n < min {
			v.fail(path, "%v is under the minimum %v", n, min)
		}
	case "boolean":
		if _, ok := doc.(bool); !ok {
			v.fail(path, "is %T, the contract says boolean", doc)
		}
	}
}

// typeOf reads `type`, which may be a string or a list ("string"|null).
// nullable reports whether the schema's `type` list admits null.
func nullable(schema map[string]any) bool {
	list, ok := schema["type"].([]any)
	if !ok {
		return false
	}
	for _, one := range list {
		if s, ok := one.(string); ok && s == "null" {
			return true
		}
	}
	return false
}

func typeOf(schema map[string]any) string {
	switch t := schema["type"].(type) {
	case string:
		return t
	case []any:
		for _, one := range t {
			if s, ok := one.(string); ok && s != "null" {
				return s
			}
		}
	}
	return ""
}

func stringsOf(v any) []string {
	items, _ := v.([]any)
	out := make([]string, 0, len(items))
	for _, i := range items {
		if s, ok := i.(string); ok {
			out = append(out, s)
		}
	}
	return out
}

func sameJSON(a, b any) bool {
	ja, _ := json.Marshal(a)
	jb, _ := json.Marshal(b)
	return string(ja) == string(jb)
}

// Two backups of one tenant created in the same second must not share a
// bundle directory: the second job's legs landed beside the first's, its
// SHA256SUMS vouched for files it had not written, and a restore of either
// refused the bundle as tampered. The second id moves forward one second.
func TestTwoBackupsInTheSameSecondGetDistinctBundleIDs(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	ids := map[string]bool{}
	for i := 0; i < 2; i++ {
		p := plan(t, oc, "backup", map[string]any{"fence": true})
		r := newRunner(oc, fake)
		r.job.ID = "01ARZ3NDEKTSV4RRFFQ69G5FA" + string(rune('A'+i))
		r.job.CreatedAt = "2026-09-14T09:30:00Z"
		r.runAll(t, p)
		for _, st := range r.steps {
			for _, id := range st.ExternalIDs {
				if strings.HasPrefix(id, bundleIDPrefix) {
					ids[strings.TrimPrefix(id, bundleIDPrefix)] = true
				}
			}
		}
	}
	if len(ids) != 2 {
		t.Fatalf("two same-second backups used bundle ids %v, want two distinct ones", ids)
	}
	for _, want := range []string{"20260914T093000Z-backup", "20260914T093001Z-backup"} {
		if !ids[want] {
			t.Errorf("bundle id %s missing from %v", want, ids)
		}
		if fake.FakeFiles().Content("/rag/backups/tenants/dev/"+want+"/manifest.json") == nil {
			t.Errorf("bundle %s has no manifest; files = %v", want, fake.FakeFiles().Paths())
		}
	}
}

// The config allowlist carries the tenant's YAML — the prompt templates
// (ADR-0008) among them — and carries no credential.
//
// A bundle with the data and without the prompts restores a tenant that
// answers differently from the one that was backed up. A bundle that took
// "the config directory" would carry secrets.env and every .bak- of it in the
// clear, which is why this is a suffix allowlist in one directory and not a
// copy of a tree.
func TestTheBundleCarriesConfigYAMLAndNoSecretFile(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	seedState(fake, "dev")
	files := fake.FakeFiles()
	files.Put("/rag/data/tenants/dev/config/provision.env", []byte("TENANT_STORE_KIND=sqlite\n"), 0o640)
	files.Put("/rag/data/tenants/dev/config/prompts.yaml", []byte("templates:\n  - id: default\n"), 0o640)
	files.Put("/rag/data/tenants/dev/config/personas.yml", []byte("personas: []\n"), 0o640)
	// Things the allowlist must NOT pick up, in the same directory.
	files.Put("/rag/data/tenants/dev/config/secrets.env", ledgerEnv(), 0o600)
	files.Put("/rag/data/tenants/dev/config/secrets.env.bak-20260101T000000Z", ledgerEnv(), 0o600)
	files.Put("/rag/data/tenants/dev/config/notes.txt", []byte("not a config file\n"), 0o640)

	runBackup(t, oc, fake, map[string]any{"fence": true})

	var man map[string]any
	if err := json.Unmarshal(fake.FakeFiles().Content(bundlePath("dev", "manifest.json")), &man); err != nil {
		t.Fatal(err)
	}
	listed := map[string]bool{}
	for _, e := range man["files"].([]any) {
		listed[e.(map[string]any)["file"].(string)] = true
	}
	for _, want := range []string{"config/tenant.env", "config/provision.env",
		"config/prompts.yaml", "config/personas.yml"} {
		if !listed[want] {
			t.Errorf("%s is not in the manifest's files[]: %v", want, listed)
		}
	}
	for _, unwanted := range []string{"config/secrets.env",
		"config/secrets.env.bak-20260101T000000Z", "config/notes.txt"} {
		if listed[unwanted] {
			t.Errorf("%s is in the manifest's files[] and must never be", unwanted)
		}
	}
	// And the bytes really travelled, not just the name.
	if got := fake.FakeFiles().Content(bundlePath("dev", "config/prompts.yaml")); len(got) == 0 {
		t.Errorf("config/prompts.yaml is listed but the bundle holds nothing at it")
	}
	for _, rel := range bundleFiles(fake) {
		if strings.Contains(rel, "secrets.env") {
			t.Errorf("%s is in the bundle directory", rel)
		}
	}
}

// ---------------------------------------------------------------- the light bundle
//
// `tenant backup <t> --scope config,state`: the safety net an operator takes
// in the minutes before a handover. The claim under test is that it captures
// everything a tenant's IDENTITY is made of and nothing that takes hours, and
// that it never pretends to be more than that.

func TestALightBundleCapturesConfigAndStateAndNoStores(t *testing.T) {
	oc, fake := fixture(t, "dev", func(tn *registry.Tenant) {
		managed(tn)
		// An adopted row's descriptor: the thing restore.sh --tenant reads.
		tn.RollbackDescriptor = &registry.RollbackDescriptor{
			CapturedAt: "2026-09-14T08:00:00Z", Owner: "wilke",
			Paths:         registry.RollbackPaths{DataDir: tn.DataDir, Worktree: tn.Worktree, PythonEnv: tn.PythonEnv},
			Ports:         tn.Ports,
			Code:          tn.Code,
			EnvFileSHA256: strings.Repeat("ab", 32),
			Images:        registry.RollbackImages{}, LaunchArgs: []registry.LaunchArg{},
		}
	})
	seedState(fake, "dev")
	fake.FakeFiles().Put("/rag/data/tenants/dev/config/provision.env", []byte("TENANT_STORE_KIND=sqlite\n"), 0o640)

	p := plan(t, oc, "backup", map[string]any{"scope": []string{"config", "state"}})
	// Every store leg is answered, in the place its real step would have been,
	// so two plans of the same tenant read side by side.
	for _, want := range []string{
		"qdrant: skip the qdrant leg", "es: skip the elasticsearch leg", "postgres: skip the postgres leg",
	} {
		if stepIndex(p, strings.SplitN(want, ": ", 2)[0], strings.SplitN(want, ": ", 2)[1]) < 0 {
			t.Errorf("no step %q in:\n  %s", want, strings.Join(titles(p), "\n  "))
		}
	}
	// And nothing stops the API: the tenant serves right through it.
	for _, tl := range titles(p) {
		if strings.Contains(tl, "stop ragstack-dev-api") || strings.Contains(tl, "fence verify") {
			t.Errorf("a light bundle fenced the tenant: %s", tl)
		}
	}

	r := newRunner(oc, fake)
	r.runAll(t, p)

	body := fake.FakeFiles().Content(bundlePath("dev", "manifest.json"))
	if body == nil {
		t.Fatalf("no manifest in the bundle; it holds %v", bundleFiles(fake))
	}
	var man map[string]any
	if err := json.Unmarshal(body, &man); err != nil {
		t.Fatalf("the manifest is not JSON: %v", err)
	}
	// It is a contract-valid manifest, light or not: a restore reads the same
	// document either way and must not have to guess which kind it has.
	for _, problem := range validateAgainstSchema(t, man) {
		t.Errorf("manifest: %s", problem)
	}
	if got := man["scope"]; fmt.Sprint(got) != "[config state]" {
		t.Errorf("scope = %v, want [config state]", got)
	}
	// A bundle with no stores in it can never claim consistency, and says in
	// its own warnings what it does not hold.
	if man["consistent"] != false {
		t.Errorf("consistent = %v: a bundle with no store snapshots cannot be consistent", man["consistent"])
	}
	if !strings.Contains(fmt.Sprint(man["warnings"]), "no store snapshots") {
		t.Errorf("warnings %v do not say the stores are missing", man["warnings"])
	}

	// The four things it MUST carry.
	files := bundleFiles(fake)
	for _, want := range []string{
		"config/tenant.env", "state/ragstack_users.db", "registry-row.json", "rollback-descriptor.json",
		"SHA256SUMS", "manifest.json",
	} {
		if !containsStr(files, want) {
			t.Errorf("the light bundle has no %s; it holds %v", want, files)
		}
	}
	// And the ones it must NOT: a store snapshot is what makes a backup take
	// hours, and taking none is the entire point.
	for _, f := range files {
		if strings.HasPrefix(f, "qdrant/") || strings.HasPrefix(f, "elasticsearch/") || strings.HasPrefix(f, "postgres/") {
			t.Errorf("the light bundle carries a store leg: %s", f)
		}
	}
	// The descriptor in the bundle is the row's, byte for byte.
	var rd registry.RollbackDescriptor
	if err := json.Unmarshal(fake.FakeFiles().Content(bundlePath("dev", "rollback-descriptor.json")), &rd); err != nil {
		t.Fatalf("rollback-descriptor.json is not a descriptor: %v", err)
	}
	if rd.Paths.DataDir != oc.Tenant.DataDir || rd.Owner != "wilke" {
		t.Errorf("descriptor = %+v, want the row's", rd)
	}

	// The registry records the scope, and the record is honest about what the
	// bundle is not: unfenced, so no prerequisite reads it as one.
	row := oc.Fleet.Tenants["dev"]
	if row.LastBackup == nil {
		t.Fatal("last_backup was not recorded")
	}
	if got := strings.Join(row.LastBackup.Scope, ","); got != "config,state" {
		t.Errorf("last_backup.scope = %q, want config,state", got)
	}
	if row.LastBackup.Fenced || row.LastBackup.Verified {
		t.Errorf("last_backup = %+v, want neither fenced nor verified", row.LastBackup)
	}
}

// A light bundle is not a recovery point: the ops that need one say so by
// name, and say which op to run instead.
func TestALightBundleIsNotAHandoverPrerequisite(t *testing.T) {
	oc, _ := fixture(t, "dev", func(tn *registry.Tenant) {
		managed(tn)
		tn.LastBackup = &registry.BackupRecord{
			Bundle: "/rag/backups/tenants/dev/20260914T093000Z-backup", At: "2026-09-14T09:30:00Z",
			Kind: "backup", Fenced: false, Verified: false, Scope: []string{"config", "state"},
		}
	})
	err := planErr(t, oc, "migrate-local", map[string]any{"phase": "execute"})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "best-effort") {
		t.Errorf("migrate-local over a light bundle = %v, want a refusal", err)
	}
}

// The two scopes that describe a bundle nobody could use.
func TestBackupRefusesAnUnusableScope(t *testing.T) {
	oc, _ := fixture(t, "dev", managed)
	err := planErr(t, oc, "backup", map[string]any{"scope": []string{"state", "stores"}})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "nothing could be restored") {
		t.Errorf("a scope without config = %v, want a refusal", err)
	}
	err = planErr(t, oc, "backup", map[string]any{"fence": true, "scope": []string{"config", "state"}})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "outage for nothing") {
		t.Errorf("--fence on a light scope = %v, want a refusal", err)
	}
	// And a leg nobody defined is a validation error, not a refusal: it never
	// reaches the planner.
	op, _ := NewRegistry(Deps{}).Lookup("backup")
	if err := op.Validate(map[string]any{"scope": []string{"secrets"}}); err == nil ||
		!errors.Is(err, jobs.ErrValidation) {
		t.Errorf("scope=[secrets] = %v, want a validation error", err)
	}
}

// The default is unchanged: a `backup` with no scope is the full bundle every
// existing caller already gets.
func TestBackupWithNoScopeIsTheFullBundle(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	seedState(fake, "dev")
	runBackup(t, oc, fake, map[string]any{"fence": true})
	var man map[string]any
	if err := json.Unmarshal(fake.FakeFiles().Content(bundlePath("dev", "manifest.json")), &man); err != nil {
		t.Fatalf("the manifest is not JSON: %v", err)
	}
	if got := fmt.Sprint(man["scope"]); got != "[config state stores]" {
		t.Errorf("scope = %v, want all three legs", got)
	}
	if got := strings.Join(oc.Fleet.Tenants["dev"].LastBackup.Scope, ","); got != "config,state,stores" {
		t.Errorf("last_backup.scope = %q", got)
	}
}
