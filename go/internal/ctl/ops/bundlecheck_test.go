package ops

// The `checked` level (PR-G1.2): the backup reads its own bundle back before
// the fence lifts, and `decommission` accepts what that check passed.

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

const checkTitle = "check the bundle: hashes, manifest, snapshot structure, counts"

// manifestOfBundle reads the finished fixture bundle's manifest.
func manifestOfBundle(t *testing.T, fake *drivers.Fake) map[string]any {
	t.Helper()
	body := fake.FakeFiles().Content(bundlePath("dev", "manifest.json"))
	if body == nil {
		t.Fatalf("no manifest in the bundle; it holds %v", bundleFiles(fake))
	}
	var man map[string]any
	if err := json.Unmarshal(body, &man); err != nil {
		t.Fatal(err)
	}
	return man
}

// runUntil runs the plan's steps up to (not including) the first whose title
// contains stop, calling between(title) after each step it ran.
func runUntil(t *testing.T, r *runner, p *jobs.Planned, stop string, after func(title string)) jobs.Step {
	t.Helper()
	for _, s := range p.Steps {
		if strings.Contains(s.Plan.Title, stop) {
			return s
		}
		if _, err := r.run(s); err != nil {
			t.Fatalf("step %d (%s): %v", s.Plan.N, s.Plan.Title, err)
		}
		if after != nil {
			after(s.Plan.Title)
		}
	}
	t.Fatalf("no step %q in %v", stop, titles(p))
	return jobs.Step{}
}

func TestAFencedBackupChecksItsOwnBundle(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	seedState(fake, "dev")
	p, _ := runBackup(t, oc, fake, map[string]any{"fence": true})

	man := manifestOfBundle(t, fake)
	if man["checked"] != true || man["verified"] != false {
		t.Errorf("manifest checked=%v verified=%v, want true/false", man["checked"], man["verified"])
	}
	for _, problem := range validateAgainstSchema(t, man) {
		t.Errorf("manifest: %s", problem)
	}
	lb := oc.Fleet.Tenants["dev"].LastBackup
	if lb == nil || !lb.Checked || lb.Verified || !lb.Fenced {
		t.Errorf("last_backup = %+v, want fenced, checked, not verified", lb)
	}
	if p.Result()["checked"] != true {
		t.Errorf("result = %v, want checked: true", p.Result())
	}
	// The legs ran through the drivers, not around them: both snapshots read
	// as tars, all four state copies integrity-checked, both counts re-taken.
	for key, want := range map[string]int{"archive.Entries": 2, "sqlite.IntegrityCheck": len(sqliteDBs)} {
		if got := fake.Count(key); got != want {
			t.Errorf("%s called %d time(s), want %d", key, got, want)
		}
	}
	// The check comes after the rename and before the record and the unfence.
	check := stepIndex(p, "backup", checkTitle)
	if check < 0 || check < stepIndex(p, "fs", "rename the bundle into place") ||
		check > stepIndex(p, "registry", "record the bundle") || check > stepIndex(p, "systemd", "start ragstack-dev-api") {
		t.Errorf("the check step is out of place:\n  %s", strings.Join(titles(p), "\n  "))
	}
}

func TestAPostgresLocalBundleIsCheckedForTheDumpHeader(t *testing.T) {
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
	r := newRunner(oc, fake)
	dump := bundlePath("dev", "postgres", "dev.dump")
	check := runUntil(t, r, p, checkTitle, func(title string) {
		if !strings.Contains(title, "dump the tenant's postgres") {
			return
		}
		// A file with the right name and the wrong contents, written before
		// SHA256SUMS so that only the header leg can object to it.
		partial := strings.Replace(dump, "-backup/", "-backup.partial/", 1)
		fake.FakeFiles().Put(partial, []byte("not a dump\n"), 0o640)
	})
	_, err := r.run(check)
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "postgres/dev.dump") ||
		!strings.Contains(err.Error(), "custom-format header") {
		t.Fatalf("a dump without PGDMP = %v, want a refusal naming postgres/dev.dump", err)
	}

	// And the dump the fake pg_dump really writes passes.
	oc, fake = fixture(t, "dev", func(tn *registry.Tenant) {
		managed(tn)
		tn.Stores.Postgres = oc.Tenant.Stores.Postgres
	})
	runBackup(t, oc, fake, map[string]any{"fence": true})
	if man := manifestOfBundle(t, fake); man["checked"] != true {
		t.Errorf("checked = %v after a good dump", man["checked"])
	}
}

// Every file SHA256SUMS lists: one byte changed, and the step refuses naming
// it — and the manifest is not marked.
func TestATamperedByteFailsTheCheckNamingTheFile(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	seedState(fake, "dev")
	fake.FakeFiles().Put("/rag/data/tenants/dev/config/provision.env", []byte("TENANT_STORE_KIND=sqlite\n"), 0o640)
	p := plan(t, oc, "backup", map[string]any{"fence": true})
	r := newRunner(oc, fake)
	check := runUntil(t, r, p, checkTitle, nil)

	files := fake.FakeFiles()
	sums := string(files.Content(bundlePath("dev", "SHA256SUMS")))
	lines := strings.Split(strings.TrimSpace(sums), "\n")
	if len(lines) < 10 {
		t.Fatalf("SHA256SUMS lists only %d file(s): %q", len(lines), sums)
	}
	for _, line := range lines {
		_, rel, _ := strings.Cut(line, "  ")
		path := bundlePath("dev", rel)
		orig := files.Content(path)
		if len(orig) == 0 {
			t.Fatalf("%s is empty in the fixture bundle", rel)
		}
		bad := append([]byte(nil), orig...)
		bad[len(bad)/2] ^= 0x01
		files.Put(path, bad, 0o640)
		_, err := r.run(check)
		if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), rel) {
			t.Errorf("one byte of %s changed: the check said %v, want a refusal naming it", rel, err)
		}
		if strings.Contains(err.Error(), "\n") {
			t.Errorf("the refusal is not one line: %q", err)
		}
		files.Put(path, orig, 0o640)
		if man := manifestOfBundle(t, fake); man["checked"] != false {
			t.Fatalf("a failed check left checked=%v in the manifest", man["checked"])
		}
	}
	// Put back, the bundle checks.
	if _, err := r.run(check); err != nil {
		t.Fatalf("the restored bundle: %v", err)
	}
	if man := manifestOfBundle(t, fake); man["checked"] != true {
		t.Errorf("checked = %v after a passing check", man["checked"])
	}
	// The step's rollback takes the mark off again.
	if _, err := r.rollback(check); err != nil {
		t.Fatal(err)
	}
	if man := manifestOfBundle(t, fake); man["checked"] != false {
		t.Errorf("checked = %v after the rollback", man["checked"])
	}
}

// The census: a collection that gained a point while the tenant was fenced is
// a bundle whose manifest does not describe the store.
func TestAQdrantCountMismatchFailsTheCheck(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	seedState(fake, "dev")
	p := plan(t, oc, "backup", map[string]any{"fence": true})
	r := newRunner(oc, fake)
	check := runUntil(t, r, p, checkTitle, nil)
	fake.FakeQdrant().Counts[oc.Tenant.Stores.Qdrant.URL+"/docs"]++
	_, err := r.run(check)
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "collection docs") ||
		!strings.Contains(err.Error(), "census") {
		t.Fatalf("a moved count = %v, want a refusal naming collection docs and the census", err)
	}

	// An unfenced bundle has no census to compare with, so the same drift is
	// not the check's business (the bundle already says best_effort).
	oc, fake = fixture(t, "dev", managed)
	p = plan(t, oc, "backup", nil)
	r = newRunner(oc, fake)
	check = runUntil(t, r, p, checkTitle, nil)
	fake.FakeQdrant().Counts[oc.Tenant.Stores.Qdrant.URL+"/docs"]++
	if _, err := r.run(check); err != nil {
		t.Errorf("an unfenced bundle's check took a census: %v", err)
	}
}

// A snapshot that is not an archive, written before SHA256SUMS so that the
// hashes agree and only the structure leg can object.
func TestAQdrantSnapshotThatIsNotATarFailsTheCheck(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	p := plan(t, oc, "backup", map[string]any{"fence": true})
	r := newRunner(oc, fake)
	check := runUntil(t, r, p, checkTitle, func(title string) {
		if !strings.Contains(title, "snapshot every qdrant collection") {
			return
		}
		for _, path := range fake.FakeFiles().Paths() {
			if strings.Contains(path, ".partial/qdrant/chunks/") {
				fake.FakeFiles().Put(path, []byte("this is not a tar archive, and it is long enough to have a "+
					"header's worth of bytes in it so the reader has to decide that for itself..."+
					strings.Repeat(".", 600)), 0o640)
			}
		}
	})
	_, err := r.run(check)
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "collection chunks") {
		t.Fatalf("a snapshot that is not a tar = %v, want a refusal naming collection chunks", err)
	}
}

// The elasticsearch repository root is what elasticsearch writes LAST; a
// directory without it is a snapshot that never finished.
func TestAMissingElasticsearchIndexRootFailsTheCheck(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	p := plan(t, oc, "backup", map[string]any{"fence": true})
	r := newRunner(oc, fake)
	removed := 0
	check := runUntil(t, r, p, checkTitle, func(title string) {
		if !strings.Contains(title, "snapshot every elasticsearch index") {
			return
		}
		for _, path := range fake.FakeFiles().Paths() {
			if strings.Contains(path, ".partial/elasticsearch/snapshots/") && strings.Contains(filepath.Base(path), "index-") {
				if err := fake.Files().Remove(context.Background(), path); err != nil {
					t.Fatal(err)
				}
				removed++
			}
		}
	})
	if removed == 0 {
		t.Fatalf("the fake elasticsearch wrote no index-* into the bundle: %v", bundleFilesPartial(fake))
	}
	// The step fails (the leg record still names the file, so the parts leg
	// is the first to say so); the elasticsearch leg says why in its own terms.
	if _, err := r.run(check); !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("a repository without index-* = %v, want a refusal", err)
	}
	if leg := checkLeg(t, fake, CheckLegElasticsearch); leg.OK || !strings.Contains(leg.Detail, "index-*") ||
		!strings.Contains(leg.Detail, "elasticsearch/snapshots/20260914T093000Z-backup") {
		t.Fatalf("elasticsearch leg = %+v, want a failure naming the directory and index-*", leg)
	}

	// And a manifest index whose directory is missing.
	oc, fake = fixture(t, "dev", managed)
	p = plan(t, oc, "backup", map[string]any{"fence": true})
	r = newRunner(oc, fake)
	check = runUntil(t, r, p, checkTitle, func(title string) {
		if !strings.Contains(title, "snapshot every elasticsearch index") {
			return
		}
		for _, path := range fake.FakeFiles().Paths() {
			if strings.Contains(path, ".partial/elasticsearch/snapshots/") && strings.Contains(path, "/indices/") {
				_ = fake.Files().Remove(context.Background(), path)
			}
		}
	})
	if _, err := r.run(check); !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("an index without its directory = %v, want a refusal", err)
	}
	if leg := checkLeg(t, fake, CheckLegElasticsearch); leg.OK || !strings.Contains(leg.Detail, "index dev-chunks") {
		t.Fatalf("elasticsearch leg = %+v, want a failure naming index dev-chunks", leg)
	}
}

// A state copy SQLite calls damaged.
func TestASQLiteCopyThatFailsIntegrityFailsTheCheck(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	seedState(fake, "dev")
	p := plan(t, oc, "backup", map[string]any{"fence": true})
	r := newRunner(oc, fake)
	check := runUntil(t, r, p, checkTitle, nil)
	fake.Fail("sqlite.IntegrityCheck:"+bundlePath("dev", "state", "ragstack_jobs.db"),
		errors.New("PRAGMA integrity_check reported *** in database main *** Page 3 is never used"))
	_, err := r.run(check)
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "state/ragstack_jobs.db") {
		t.Fatalf("a damaged state copy = %v, want a refusal naming state/ragstack_jobs.db", err)
	}
}

// A leg record that says a file was included, and the file is empty.
func TestAnIncludedEmptyFileFailsThePartsLeg(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	seedState(fake, "dev")
	p := plan(t, oc, "backup", map[string]any{"fence": true})
	r := newRunner(oc, fake)
	check := runUntil(t, r, p, checkTitle, func(title string) {
		if !strings.Contains(title, "copy ragstack_users.db") {
			return
		}
		for _, path := range fake.FakeFiles().Paths() {
			if strings.HasSuffix(path, ".partial/state/ragstack_users.db") {
				fake.FakeFiles().Put(path, nil, 0o640)
			}
		}
	})
	if _, err := r.run(check); !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "state/ragstack_users.db") {
		t.Fatalf("an included, empty state copy = %v, want a refusal naming it", err)
	}
	if parts := checkLeg(t, fake, CheckLegParts); parts.OK || !strings.Contains(parts.Detail, "state/ragstack_users.db is EMPTY") {
		t.Fatalf("parts leg = %+v", parts)
	}
}

// ---------------------------------------------------------------- the gate

func TestDecommissionAcceptsACheckedBundleAndMigrateLocalDoesNot(t *testing.T) {
	checked := func(tn *registry.Tenant) {
		managed(tn)
		tn.LastBackup = &registry.BackupRecord{Bundle: "/rag/backups/tenants/dev/20260914T093000Z-backup",
			At: "2026-09-14T09:30:00Z", Kind: "backup", Fenced: true, Checked: true, Scope: fullScope}
	}
	oc, _ := fixture(t, "dev", checked)
	p := plan(t, oc, "decommission", noArchive)
	if !hasStep(p, "fs", "quarantine the data directory") {
		t.Fatalf("decommission over a fenced, checked bundle did not plan the quarantine: %v", titles(p))
	}
	err := planErr(t, oc, "migrate-local", map[string]any{"phase": "execute"})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "never verified") ||
		!strings.Contains(err.Error(), "it is checked") {
		t.Errorf("migrate-local over a checked, unverified bundle = %v, want a refusal that says checked is not enough", err)
	}

	// Fenced and neither: refused, and the refusal says how to check it.
	oc, _ = fixture(t, "dev", func(tn *registry.Tenant) {
		checked(tn)
		tn.LastBackup.Checked = false
	})
	err = planErr(t, oc, "decommission", noArchive)
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "neither checked nor verified") ||
		!strings.Contains(err.Error(), "backup verify dev 20260914T093000Z-backup") {
		t.Errorf("decommission over an unchecked bundle = %v", err)
	}
	// Checked but unfenced: still best-effort, for both.
	oc, _ = fixture(t, "dev", func(tn *registry.Tenant) {
		checked(tn)
		tn.LastBackup.Fenced = false
	})
	if err := planErr(t, oc, "decommission", noArchive); !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "best-effort") {
		t.Errorf("decommission over an unfenced, checked bundle = %v", err)
	}
	// Verified alone still satisfies both.
	oc, _ = fixture(t, "dev", func(tn *registry.Tenant) {
		checked(tn)
		tn.LastBackup.Checked, tn.LastBackup.Verified = false, true
	})
	plan(t, oc, "decommission", noArchive)
	if err := planErrMaybe(t, oc, "migrate-local", map[string]any{"phase": "execute"}); err != nil &&
		strings.Contains(err.Error(), "verified") {
		t.Errorf("migrate-local over a verified bundle = %v", err)
	}
}

// ---------------------------------------------------------------- the schema

func TestTheEmbeddedManifestSchemaIsTheContract(t *testing.T) {
	want, err := os.ReadFile(schemaPath)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(want, bundleManifestSchema) {
		t.Fatal("go/internal/ctl/ops/bundle_manifest.schema.json differs from contracts/ctl/schemas/bundle_manifest.json; " +
			"copy the contract over it")
	}
}

// A manifest member the schema does not know fails the manifest leg: that is
// the schema validation, and not only the required-member list.
func TestTheManifestLegValidatesAgainstTheSchema(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	runBackup(t, oc, fake, map[string]any{"fence": true})
	man := manifestOfBundle(t, fake)
	man["surprise"] = true
	body, _ := json.Marshal(man)
	fake.FakeFiles().Put(bundlePath("dev", "manifest.json"), body, 0o640)
	_, err := CheckBundle(context.Background(), BundleCheckDrivers{Files: fake.Files(), Archive: fake.Archive(),
		SQLite: fake.SQLite()}, bundlePath("dev"), "20260914T093000Z-backup", nil)
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), `"surprise"`) {
		t.Fatalf("an unknown member = %v, want the schema to refuse it", err)
	}
}

// checkLeg runs CheckBundle over the finished fixture bundle and returns one
// leg's verdict.
func checkLeg(t *testing.T, fake *drivers.Fake, leg string) BundleCheckLeg {
	t.Helper()
	res, _ := CheckBundle(context.Background(), BundleCheckDrivers{Files: fake.Files(), Archive: fake.Archive(),
		SQLite: fake.SQLite()}, bundlePath("dev"), "20260914T093000Z-backup", nil)
	for _, l := range res.Legs {
		if l.Leg == leg {
			return l
		}
	}
	t.Fatalf("no %s leg in %+v", leg, res.Legs)
	return BundleCheckLeg{}
}

// bundleFilesPartial lists the unfinished bundle's files.
func bundleFilesPartial(fake *drivers.Fake) []string {
	var out []string
	for _, p := range fake.FakeFiles().Paths() {
		if strings.Contains(p, "-backup.partial/") {
			out = append(out, p)
		}
	}
	return out
}

// planErrMaybe is planErr without the "must fail" assertion.
func planErrMaybe(t *testing.T, oc jobs.Context, verb string, args map[string]any) error {
	t.Helper()
	op, ok := NewRegistry(testDeps(oc)).Lookup(verb)
	if !ok {
		t.Fatalf("no op %q", verb)
	}
	_, err := op.Plan(context.Background(), oc, args)
	return err
}
