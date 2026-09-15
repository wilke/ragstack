package ops

// `restore --as`, end to end against the fakes — and always from a bundle a
// REAL backup wrote.
//
// Nothing here hand-builds a manifest. The whole point of the verb is that a
// bundle another op produced can be rebuilt from months later, so a test that
// fed the restore a manifest written by the test would be asserting that two
// pieces of this file agree with each other. Every case below runs `backup
// --fence` on the fixture tenant first and then restores from what it left on
// the fake host; the failure cases damage that bundle in one specific way.

import (
	"context"
	"encoding/json"
	"errors"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// restoreFixture is a ctl-managed source tenant with a fenced bundle already
// on the fake host, ready to be restored from.
//
// It returns the op Context, the fake host and the bundle id. The bundle is
// produced by RUNNING the backup op, so every assertion below is made against
// the document the other half of PR-D actually writes.
func restoreFixture(t *testing.T) (jobs.Context, *drivers.Fake, string) {
	t.Helper()
	oc, fake := fixture(t, "dev", managed)
	seedState(fake, "dev")
	fake.FakeFiles().Put("/rag/data/tenants/dev/config/provision.env", []byte("TENANT_STORE_KIND=sqlite\n"), 0o640)
	runBackup(t, oc, fake, map[string]any{"fence": true})
	// The backup's own last step recorded the bundle on the row the plan will
	// read: the restore marks `verified` on THAT record.
	bundle := "20260914T093000Z-backup"
	oc.Fleet.Tenants["dev"].LastBackup = &registry.BackupRecord{
		Bundle: filepath.Join("/rag/backups/tenants/dev", bundle), At: "2026-09-14T09:30:00Z",
		Kind: "backup", Fenced: true, Verified: false,
	}
	// The artifact's node_modules, which `create` builds the UI from and
	// refuses without — a fact about the fixture host, not about the restore.
	fake.FakeBuild().Installed["/rag/data/ctl/artifacts/"+testArtifactID+"/worktree"] = true
	return oc, fake, bundle
}

// runRestore plans and runs a restore, seeding the fake host with the facts a
// RESTORED tenant would present: its API port bound, its stores holding what
// the bundle recorded, and its own collection inventory back.
//
// The seeding is unavoidable and it is bounded. The fake stores keep no data:
// `Qdrant.Recover` and `_restore` move no points on this host, so without it
// the fixture could only ever represent a FAILED restore. What it does NOT fake
// is any of the work — the copies, the recover calls, the repository
// registration and the counts asked for are all asserted below on the call log.
func runRestore(t *testing.T, oc jobs.Context, fake *drivers.Fake, args map[string]any) (*jobs.Planned, *runner, error) {
	t.Helper()
	// A real sleep between probes would make this test take minutes.
	old := createReadyPoll
	createReadyPoll = time.Millisecond
	t.Cleanup(func() { createReadyPoll = old })

	p := plan(t, oc, "restore", args)
	ports, ok := p.Result()["ports"].(paths.Ports)
	if !ok {
		t.Fatalf("the plan does not report the fresh tenant's ports: %v", p.Result())
	}
	seedRestoreTarget(fake, ports, map[string]int64{"chunks": 88_000, "docs": 1200},
		map[string]int64{"dev-chunks": 88_000})

	r := newRunner(oc, fake)
	for _, s := range p.Steps {
		if _, err := r.run(s); err != nil {
			return p, r, err
		}
	}
	return p, r, nil
}

// seedRestoreTarget makes the fake host answer for a tenant that has come back.
func seedRestoreTarget(fake *drivers.Fake, ports paths.Ports, points, docs map[string]int64) {
	fake.FakeProc().Ports[ports.API] = true
	qURL := "http://127.0.0.1:" + strconv.Itoa(ports.QdrantHTTP)
	esURL := "http://127.0.0.1:" + strconv.Itoa(ports.ESHTTP)
	names := []string{}
	for c, n := range points {
		fake.FakeQdrant().Counts[qURL+"/"+c] = n
		names = append(names, c)
	}
	for i, n := range docs {
		fake.FakeElasticsearch().Counts[esURL+"/"+i] = n
	}
	sort.Strings(names)
	fake.FakeTenantAPI().CollectionsByOrigin["http://127.0.0.1:"+strconv.Itoa(ports.API)] = names
}

// damage rewrites one file of the bundle on the fake host.
func damage(fake *drivers.Fake, rel string, body []byte) {
	fake.FakeFiles().Put(filepath.Join("/rag/backups/tenants/dev/20260914T093000Z-backup", rel), body, 0o640)
}

// readBundleManifest reads the bundle's manifest off the fake host.
func readBundleManifest(t *testing.T, fake *drivers.Fake) map[string]any {
	t.Helper()
	body := fake.FakeFiles().Content("/rag/backups/tenants/dev/20260914T093000Z-backup/manifest.json")
	if body == nil {
		t.Fatalf("the fixture bundle has no manifest; it holds %v", bundleFiles(fake))
	}
	var man map[string]any
	if err := json.Unmarshal(body, &man); err != nil {
		t.Fatalf("the manifest is not JSON: %v", err)
	}
	return man
}

// ---------------------------------------------------------------- the happy path

func TestRestoreRebuildsTheTenantVerifiesItAndMarksTheBundle(t *testing.T) {
	oc, fake, bundle := restoreFixture(t)
	p, _, err := runRestore(t, oc, fake, map[string]any{"from": bundle, "as": "dev-r"})
	if err != nil {
		t.Fatalf("the restore failed: %v", err)
	}

	// 1. The fresh tenant exists, is active, and is the one the job reported.
	row, ok := oc.Fleet.Tenants["dev-r"]
	if !ok {
		t.Fatalf("no dev-r in the registry; it holds %v", tenantNames(oc.Fleet))
	}
	if row.State != "active" {
		t.Errorf("the restored tenant is %q, want active", row.State)
	}
	if rec, ok := row.LastOps["restore"]; !ok || rec.Outcome != "succeeded" {
		t.Errorf("last_ops = %v, want a succeeded restore", row.LastOps)
	}
	if _, ok := row.LastOps["create"]; ok {
		t.Errorf("the row was filed under `create`: a restore's job id must not be recorded as a create")
	}

	// 2. The bundle's data reached the fresh tenant's tree, streamed rather
	//    than read into memory.
	for _, want := range []string{
		"/rag/data/tenants/dev-r/qdrant/snapshots/chunks/",
		"/rag/data/tenants/dev-r/qdrant/snapshots/docs/",
		"/rag/data/tenants/dev-r/state/ragstack_users.db",
	} {
		if !anyPathWithPrefix(fake, want) {
			t.Errorf("nothing was copied to %s; the tenant tree holds %v", want, tenantFiles(fake, "dev-r"))
		}
	}
	if !usedCopyFile(fake) {
		t.Errorf("the copies did not go through Files.CopyFile: a bundle's snapshots are gigabytes and must be streamed")
	}

	// 3. The stores were told to recover, from a location INSIDE the container.
	rec := fake.FakeQdrant().Recovered
	if len(rec) != 2 {
		t.Fatalf("qdrant recoveries = %v, want one per collection", rec)
	}
	for _, r := range rec {
		if !strings.Contains(r, "file:///qdrant/snapshots/") {
			t.Errorf("recover location is not a container path: %q", r)
		}
	}
	if n := len(fake.FakeElasticsearch().Restored); n != 1 {
		t.Errorf("elasticsearch restores = %v, want one", fake.FakeElasticsearch().Restored)
	}
	if repos := fake.FakeElasticsearch().Repos; len(repos) != 0 {
		t.Errorf("the restore left a repository registered on the fresh cluster: %v", repos)
	}

	// 4. The counts were CHECKED, and the result carries them.
	counts, ok := p.Result()["counts"].(map[string]any)
	if !ok {
		t.Fatalf("the result carries no counts: %v", p.Result())
	}
	q, _ := counts["qdrant"].(map[string]int64)
	if q["chunks"] != 88_000 || q["docs"] != 1200 {
		t.Errorf("qdrant counts = %v", counts["qdrant"])
	}
	if es, _ := counts["elasticsearch"].(map[string]int64); es["dev-chunks"] != 88_000 {
		t.Errorf("elasticsearch counts = %v", counts["elasticsearch"])
	}
	if p.Result()["restored_as"] != "dev-r" || p.Result()["bundle"] != bundle {
		t.Errorf("result = %v", p.Result())
	}

	// 5. The bundle is now verified — in the SOURCE's registry row and in the
	//    bundle's own manifest. Nothing else in this system sets either.
	if b := oc.Fleet.Tenants["dev"].LastBackup; b == nil || !b.Verified {
		t.Errorf("the source's last_backup was not marked verified: %+v", oc.Fleet.Tenants["dev"].LastBackup)
	}
	if man := readBundleManifest(t, fake); man["verified"] != true {
		t.Errorf("the bundle's manifest still says verified=%v", man["verified"])
	}

	// 6. The credentials are FRESH and delivered through the envelope, with
	//    the source's labels and roles mirrored.
	labels := map[string]string{}
	for _, s := range p.Secrets() {
		labels[s.Label] = s.Role
		if len(s.Value) != 64 {
			t.Errorf("key %s is %d characters, want token_hex(32)", s.Label, len(s.Value))
		}
	}
	if labels[bootstrapAdminLabel] != "admin" {
		t.Errorf("the envelope has no bootstrap admin: %v", labels)
	}
	for _, k := range oc.Fleet.Tenants["dev"].Keys {
		if k.Role != "admin" && k.Role != "user" {
			continue
		}
		if _, ok := labels[k.ID]; !ok {
			t.Errorf("the source's key %q (%s) was not reproduced on the restored tenant: %v", k.ID, k.Role, labels)
		}
	}
	// And the registry keeps fingerprints, never values.
	for _, k := range row.Keys {
		if !strings.HasPrefix(k.Fingerprint, "sha256:") {
			t.Errorf("ledger row without a fingerprint: %+v", k)
		}
	}
}

// TestRestoreOnlyStartsTheStoresBeforeItPoursTheDataIn is the ordering the
// whole verb depends on: an API started against empty stores registers nothing
// and would have to be restarted, and a recover into a store that is not up
// fails for a reason that reads like a bad bundle.
func TestRestoreOnlyStartsTheStoresBeforeItPoursTheDataIn(t *testing.T) {
	oc, fake, bundle := restoreFixture(t)
	p, _, err := runRestore(t, oc, fake, map[string]any{"from": bundle, "as": "dev-r"})
	if err != nil {
		t.Fatalf("the restore failed: %v", err)
	}
	order := []struct{ kind, title string }{
		{"fs", "verify the bundle"},
		{"registry", "allocate dev-r"},
		{"fs", "copy the bundle's qdrant snapshots"},
		{"systemd", "start ragstack-dev-r-qdrant.service"},
		{"probe", "wait for dev-r's stores"},
		{"qdrant", "recover every collection"},
		{"es", "register the copied repository"},
		{"systemd", "start ragstack-dev-r-api.service"},
		{"probe", "verify the restored tenant against the bundle"},
		{"fs", "mark bundle " + bundle + " verified"},
		{"registry", "record dev-r as active"},
		{"nginx", "publish the gateway generation"},
	}
	last := -1
	for _, want := range order {
		i := stepIndex(p, want.kind, want.title)
		if i < 0 {
			t.Fatalf("no %s step %q in:\n  %s", want.kind, want.title, strings.Join(titles(p), "\n  "))
		}
		if i <= last {
			t.Errorf("%s %q is at %d, after a step that must follow it", want.kind, want.title, i)
		}
		last = i
	}
	// The API unit is not started by the create half: `Start:false` is what
	// leaves the tenant empty until the data is in it.
	if i, j := stepIndex(p, "systemd", "skip the start"), stepIndex(p, "systemd", "start ragstack-dev-r-api.service"); i > j {
		t.Errorf("the create half started the tenant (%d > %d)", i, j)
	}
}

// ---------------------------------------------------------------- the refusals

func TestRestoreRefusesAnUnfencedBundle(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	seedState(fake, "dev")
	runBackup(t, oc, fake, nil) // no fence
	oc.Fleet.Tenants["dev"].LastBackup = &registry.BackupRecord{
		Bundle: "/rag/backups/tenants/dev/20260914T093000Z-backup", Fenced: false,
	}
	fake.FakeBuild().Installed["/rag/data/ctl/artifacts/"+testArtifactID+"/worktree"] = true

	_, _, err := runRestore(t, oc, fake, map[string]any{"from": "20260914T093000Z-backup", "as": "dev-r"})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "best_effort") {
		t.Fatalf("restoring from an unfenced bundle = %v, want a refusal naming best_effort", err)
	}
	if _, ok := oc.Fleet.Tenants["dev-r"]; ok {
		t.Errorf("a refused restore allocated a tenant anyway")
	}
}

func TestRestoreRefusesATamperedChecksumList(t *testing.T) {
	oc, fake, bundle := restoreFixture(t)
	// One line removed from SHA256SUMS — the cheapest way to hide a changed
	// file, and the reason the check runs in both directions.
	sums := string(fake.FakeFiles().Content("/rag/backups/tenants/dev/" + bundle + "/SHA256SUMS"))
	lines := strings.Split(strings.TrimRight(sums, "\n"), "\n")
	damage(fake, "SHA256SUMS", []byte(strings.Join(lines[1:], "\n")+"\n"))

	_, _, err := runRestore(t, oc, fake, map[string]any{"from": bundle, "as": "dev-r"})
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("a bundle with an edited SHA256SUMS = %v, want a refusal", err)
	}
	if !strings.Contains(err.Error(), "has been edited") {
		t.Errorf("the refusal does not say the list was edited: %v", err)
	}
}

func TestRestoreRefusesAFileThatDoesNotHash(t *testing.T) {
	oc, fake, bundle := restoreFixture(t)
	damage(fake, filepath.Join("state", "ragstack_users.db"), []byte("not the database that was backed up"))

	_, _, err := runRestore(t, oc, fake, map[string]any{"from": bundle, "as": "dev-r"})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "ragstack_users.db") {
		t.Fatalf("a bundle with an altered file = %v, want a refusal naming it", err)
	}
}

func TestRestoreRefusesABundleFromAnotherArtifact(t *testing.T) {
	oc, fake, bundle := restoreFixture(t)
	man := readBundleManifest(t, fake)
	man["artifact"].(map[string]any)["id"] = "v0.9-000000000000"
	body, _ := json.MarshalIndent(man, "", "  ")
	damage(fake, "manifest.json", append(body, '\n'))

	_, _, err := runRestore(t, oc, fake, map[string]any{"from": bundle, "as": "dev-r"})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "artifact") {
		t.Fatalf("a bundle from another artifact = %v, want a refusal naming the artifact", err)
	}
}

func TestRestoreRefusesAPartialBundle(t *testing.T) {
	oc, fake, bundle := restoreFixture(t)
	// The bundle as an interrupted backup would have left it: the `.partial`
	// directory, and no finished one.
	src := "/rag/backups/tenants/dev/" + bundle
	for _, path := range fake.FakeFiles().Paths() {
		if rel, ok := strings.CutPrefix(path, src+"/"); ok {
			fake.FakeFiles().Put(src+partialSuffix+"/"+rel, fake.FakeFiles().Content(path), 0o640)
			_ = fake.FakeFiles().Remove(context.Background(), path)
		}
	}
	_, _, err := runRestore(t, oc, fake, map[string]any{"from": bundle, "as": "dev-r"})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), partialSuffix) {
		t.Fatalf("restoring from a bundle that only exists as %s = %v, want a refusal naming it", partialSuffix, err)
	}
}

func TestRestoreRefusesATenantThatAlreadyExists(t *testing.T) {
	oc, _, bundle := restoreFixture(t)
	err := planErr(t, oc, "restore", map[string]any{"from": bundle, "as": "demo"})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "FRESH") {
		t.Fatalf("restoring over an existing tenant = %v, want a refusal", err)
	}
}

func TestRestoreRefusesWhenTheStoreImagesDiffer(t *testing.T) {
	oc, fake, bundle := restoreFixture(t)
	// The fleet has moved to another qdrant image since the bundle was taken.
	oc.Fleet.Images.Qdrant.Version = "1.16.0"
	oc.Fleet.Images.Qdrant.Digest = "sha256:" + strings.Repeat("ab", 32)
	man := readBundleManifest(t, fake)
	man["images"].(map[string]any)["qdrant"] = map[string]any{
		"version": "1.12.0", "digest": "sha256:" + strings.Repeat("cd", 32),
	}
	body, _ := json.MarshalIndent(man, "", "  ")
	damage(fake, "manifest.json", append(body, '\n'))

	_, _, err := runRestore(t, oc, fake, map[string]any{"from": bundle, "as": "dev-r"})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "1.12.0") {
		t.Fatalf("a bundle from another store image = %v, want a refusal naming the versions", err)
	}
}

// TestRestoreSkipsTheImageCheckWhenNothingIsPinned is the other half of the
// same rule: an all-zero digest is "nobody recorded what this fleet runs", and
// refusing on it would make every restore on an unpinned fleet impossible.
func TestRestoreSkipsTheImageCheckWhenNothingIsPinned(t *testing.T) {
	oc, fake, bundle := restoreFixture(t)
	if oc.Fleet.Images.Qdrant.Digest != registry.UnpinnedDigest {
		t.Skip("the fixture fleet pins its images; this case is about one that does not")
	}
	if _, _, err := runRestore(t, oc, fake, map[string]any{"from": bundle, "as": "dev-r"}); err != nil {
		t.Fatalf("a restore on an unpinned fleet = %v, want it to proceed with a warning", err)
	}
}

// ---------------------------------------------------------------- the rollback

// TestAFailedVerifyRollsTheWholeRestoreBack is the promise the plan makes in as
// many words: a restore that cannot prove itself leaves NOTHING behind.
func TestAFailedVerifyRollsTheWholeRestoreBack(t *testing.T) {
	oc, fake, bundle := restoreFixture(t)
	old := createReadyPoll
	createReadyPoll = time.Millisecond
	t.Cleanup(func() { createReadyPoll = old })

	p := plan(t, oc, "restore", map[string]any{"from": bundle, "as": "dev-r"})
	ports := p.Result()["ports"].(paths.Ports)
	// The target's stores come back HALF FULL: the count the bundle recorded
	// for `chunks` is 88,000 and this host answers 41.
	seedRestoreTarget(fake, ports, map[string]int64{"chunks": 41, "docs": 1200},
		map[string]int64{"dev-chunks": 88_000})

	r := newRunner(oc, fake)
	failed := -1
	for i, s := range p.Steps {
		if _, err := r.run(s); err != nil {
			if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "88000") {
				t.Fatalf("step %d failed for the wrong reason: %v", s.Plan.N, err)
			}
			failed = i
			break
		}
	}
	if failed < 0 {
		t.Fatalf("a restore into stores that came back half full succeeded")
	}
	// The engine rolls back in reverse over the steps that succeeded.
	for i := failed - 1; i >= 0; i-- {
		s := p.Steps[i]
		if s.Rollback == nil {
			continue
		}
		if _, err := s.Rollback(context.Background(), r.ctx(s)); err != nil {
			t.Fatalf("rollback of step %d (%s): %v", s.Plan.N, s.Plan.Title, err)
		}
	}
	if _, ok := oc.Fleet.Tenants["dev-r"]; ok {
		t.Errorf("the failed restore left a registry row behind")
	}
	// Nothing is left AT the tenant's own path: the create half's fs step
	// renamed the whole tree aside on the way out, so `dev-r` is free for the
	// next attempt and the bytes the stores wrote are still there to look at.
	if got := tenantFiles(fake, "dev-r"); len(got) > 0 {
		t.Errorf("the failed restore left %v at the tenant's own path", got)
	}
	if fake.FakeFiles().Dirs["/rag/data/tenants/dev-r"] != 0 {
		t.Errorf("the failed restore left the directory /rag/data/tenants/dev-r behind")
	}
	aside := failedTrees(fake, "dev-r")
	if len(aside) != 1 {
		t.Fatalf("a failed restore left %v, want exactly one /rag/data/tenants/dev-r.failed-<ts>", aside)
	}
	// And it is a RENAME, not a delete: what the create half laid down is
	// still under the renamed tree.
	if !anyPathWithPrefix(fake, aside[0]+"/") && fake.FakeFiles().Dirs[aside[0]] == 0 {
		t.Errorf("%s holds nothing: the rollback deleted the tree instead of moving it", aside[0])
	}
	// The plan warned about exactly this path, in as many words.
	if !anyWarning(p, ".failed-<ts>") {
		t.Errorf("the plan does not warn that a failure leaves <data_dir>.failed-<ts>: %v", p.Plan.Warnings)
	}
	if units := fake.FakeSystemd().ActiveUnits(); containsPrefix(units, "ragstack-dev-r-") {
		t.Errorf("the failed restore left units running: %v", units)
	}
	// And the SOURCE is untouched: nothing claims the bundle was proved.
	if b := oc.Fleet.Tenants["dev"].LastBackup; b != nil && b.Verified {
		t.Errorf("a failed restore marked the source's bundle verified")
	}
	if man := readBundleManifest(t, fake); man["verified"] == true {
		t.Errorf("a failed restore marked the bundle's manifest verified")
	}
}

// ---------------------------------------------------------------- secrets

// TestARestorePlanNeverShowsACredential walks the plan an operator approves.
func TestARestorePlanNeverShowsACredential(t *testing.T) {
	oc, _, bundle := restoreFixture(t)
	p := plan(t, oc, "restore", map[string]any{"from": bundle, "as": "dev-r"})
	for _, s := range p.Steps {
		for _, w := range s.Plan.WouldWrite {
			if strings.HasSuffix(w.Path, "/config/secrets.env") && string(w.Preview) != "" {
				t.Errorf("step %q previews the credential file: %q", s.Plan.Title, w.Preview)
			}
		}
		for _, target := range s.Plan.Targets {
			if len(target) == 64 && !strings.ContainsAny(target, "/:. ") {
				t.Errorf("step %q has a token-shaped target: %q", s.Plan.Title, target)
			}
		}
	}
	// The plan is made before anything is minted, so it can carry no envelope.
	if secrets := p.Secrets(); len(secrets) != 0 {
		t.Errorf("the plan already holds %d secret(s)", len(secrets))
	}
	// And a destructive op on the SOURCE: the confirm value is the source's
	// name, not the fresh tenant's.
	if string(p.Plan.ConfirmValue) != "dev" || string(p.Plan.Tenant) != "dev" {
		t.Errorf("plan tenant/confirm = %q/%q, want dev/dev", p.Plan.Tenant, p.Plan.ConfirmValue)
	}
}

// ---------------------------------------------------------------- helpers

func tenantNames(f *registry.Fleet) []string {
	out := make([]string, 0, len(f.Tenants))
	for n := range f.Tenants {
		out = append(out, n)
	}
	sort.Strings(out)
	return out
}

// failedTrees are the `<data_dir>.failed-<ts>` directories on the fake host.
func failedTrees(fake *drivers.Fake, tenant string) []string {
	seen := map[string]bool{}
	prefix := "/rag/data/tenants/" + tenant + ".failed-"
	for d := range fake.FakeFiles().Dirs {
		if strings.HasPrefix(d, prefix) {
			seen[strings.SplitN(d, "/", 6)[4]] = true
		}
	}
	for _, f := range fake.FakeFiles().Paths() {
		if strings.HasPrefix(f, prefix) {
			seen[strings.SplitN(f, "/", 6)[4]] = true
		}
	}
	out := []string{}
	for d := range seen {
		out = append(out, "/rag/data/tenants/"+d)
	}
	sort.Strings(out)
	return out
}

func tenantFiles(fake *drivers.Fake, tenant string) []string {
	out := []string{}
	for _, p := range fake.FakeFiles().Paths() {
		if strings.HasPrefix(p, "/rag/data/tenants/"+tenant+"/") {
			out = append(out, p)
		}
	}
	return out
}

func anyPathWithPrefix(fake *drivers.Fake, prefix string) bool {
	for _, p := range fake.FakeFiles().Paths() {
		if strings.HasPrefix(p, prefix) {
			return true
		}
	}
	return false
}

func usedCopyFile(fake *drivers.Fake) bool {
	for _, key := range fake.CallKeys() {
		if strings.HasPrefix(key, "files.CopyFile(") {
			return true
		}
	}
	return false
}

func containsPrefix(ss []string, prefix string) bool {
	for _, s := range ss {
		if strings.HasPrefix(s, prefix) {
			return true
		}
	}
	return false
}

// ---------------------------------------------------------------- postgres-local

// managedPG is a ctl-managed tenant whose relational state is its own postgres
// instance rather than SQLite files.
func managedPG(t *registry.Tenant) {
	managed(t)
	t.Stores.Postgres = registry.Postgres{
		Kind: registry.PostgresKindLocal, Ownership: registry.OwnershipExclusive,
		Capabilities: registry.Capabilities{Stop: true, Purge: true, Restore: true, Snapshot: true},
		URL:          registry.NullString("postgresql://localhost:" + strconv.Itoa(t.Ports.PG)),
		Port:         registry.NullPort(t.Ports.PG), Instance: registry.NullString("postgres-" + t.Name),
		SIF: "/rag/apptainer/images/postgres.sif", DataDir: registry.NullString(t.DataDir + "/postgres"),
	}
}

// TestRestoreOfAPostgresLocalTenantPoursTheDumpBackIn is the other relational
// shape: the fresh tenant runs its OWN postgres, its unit is started with the
// stores, and the bundle's dump is read straight out of the bundle rather than
// copied into the tenant tree first.
func TestRestoreOfAPostgresLocalTenantPoursTheDumpBackIn(t *testing.T) {
	oc, fake := fixture(t, "dev", managedPG)
	seedState(fake, "dev")
	runBackup(t, oc, fake, map[string]any{"fence": true})
	bundle := "20260914T093000Z-backup"
	oc.Fleet.Tenants["dev"].LastBackup = &registry.BackupRecord{
		Bundle: "/rag/backups/tenants/dev/" + bundle, Kind: "backup", Fenced: true,
	}
	fake.FakeBuild().Installed["/rag/data/ctl/artifacts/"+testArtifactID+"/worktree"] = true

	p, _, err := runRestore(t, oc, fake, map[string]any{"from": bundle, "as": "dev-r"})
	if err != nil {
		t.Fatalf("the restore failed: %v", err)
	}
	if !hasStep(p, "postgres", "restore the bundle's postgres dump") {
		t.Fatalf("no postgres restore step: %v", titles(p))
	}
	if !hasStep(p, "systemd", "start ragstack-dev-r-postgres.service") {
		t.Errorf("the fresh instance's unit is never started: %v", titles(p))
	}
	restores := fake.FakePostgres().Restores
	if len(restores) != 1 {
		t.Fatalf("pg_restore calls = %v, want one", restores)
	}
	// The dump is read from the BUNDLE: copying a multi-gigabyte archive into
	// the tenant tree first would double the space a restore needs.
	if !strings.Contains(restores[0], "/rag/backups/tenants/dev/"+bundle+"/postgres/dev.dump") {
		t.Errorf("pg_restore read %q, want the dump inside the bundle", restores[0])
	}
	// …and into the FRESH tenant's own socket directory and database.
	if !strings.Contains(restores[0], "/rag/data/tenants/dev-r/postgres/run") || !strings.Contains(restores[0], " dev-r ") {
		t.Errorf("pg_restore did not target the fresh instance: %q", restores[0])
	}
}

// ---------------------------------------------------------------- the sandbox rule

// TestRestoringASandboxAllocatesASandboxBlock is the selftest's arithmetic.
//
// `registry.Allocate` ignores selftest rows and hands out the next PRODUCTION
// block, so a selftest that restored its own tenant would spend a production
// index and port block on every run — and the copy, living outside the selftest
// range, would then need a verified bundle of its own before `decommission`
// would clean it up. A copy of a sandbox is a sandbox.
func TestRestoringASandboxAllocatesASandboxBlock(t *testing.T) {
	oc, fake, bundle := restoreFixture(t)
	src := oc.Fleet.Tenants["dev"]
	sandbox := paths.BlockAt(paths.SelftestBase, 0, 0)
	sandbox.Index = registry.SandboxIndexBase
	src.Ports = sandbox
	src.Stores.Qdrant.URL = "http://127.0.0.1:" + strconv.Itoa(sandbox.QdrantHTTP)
	src.Stores.Elasticsearch.URL = "http://127.0.0.1:" + strconv.Itoa(sandbox.ESHTTP)

	// The copy is named like a sandbox as well as blocked like one: the two
	// halves are the same rule, and `restore --as` refuses them apart. (The
	// selftest's real pairing is `ctltest-<stamp>` and `ctltest-<stamp>-r`;
	// the fixture source is called `dev` and only its BLOCK is a sandbox.)
	p := plan(t, oc, "restore", map[string]any{"from": bundle, "as": "ctltest-dev-r"})
	ports, ok := p.Result()["ports"].(paths.Ports)
	if !ok {
		t.Fatalf("the plan reports no ports: %v", p.Result())
	}
	if !paths.IsSelftestBlock(ports.Base) {
		t.Errorf("a sandbox restored into base %d, which is outside the selftest range %d–%d",
			ports.Base, paths.SelftestBase, paths.SelftestEnd)
	}
	if ports.Base == sandbox.Base {
		t.Errorf("the copy was given the source's own block (%d)", ports.Base)
	}
	if !anyWarning(p, "allocated a SANDBOX block too") {
		t.Errorf("the plan does not say the copy is a sandbox: %v", p.Plan.Warnings)
	}
	_ = fake
}

// TestARestoreRefusesToCrossTheSandboxBoundary is the other half of the rule
// above, in both directions.
//
// The BLOCK a copy is allocated is decided by the source (a copy of a sandbox
// is a sandbox); the NAME is decided by the request. When the two disagree the
// result is a row nothing can clean up: a production name on a selftest block is
// swept by `selftest --sweep`'s block rule, and a `ctltest-` name on a
// production block is the one combination the sweep refuses and reports.
func TestARestoreRefusesToCrossTheSandboxBoundary(t *testing.T) {
	t.Run("a sandbox restored under a production name", func(t *testing.T) {
		oc, _, bundle := restoreFixture(t)
		src := oc.Fleet.Tenants["dev"]
		sandbox := paths.BlockAt(paths.SelftestBase, 0, 0)
		sandbox.Index = registry.SandboxIndexBase
		src.Ports = sandbox
		src.Stores.Qdrant.URL = "http://127.0.0.1:" + strconv.Itoa(sandbox.QdrantHTTP)
		src.Stores.Elasticsearch.URL = "http://127.0.0.1:" + strconv.Itoa(sandbox.ESHTTP)

		err := planErr(t, oc, "restore", map[string]any{"from": bundle, "as": "dev-r"})
		if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "SANDBOX block") {
			t.Fatalf("restoring a sandbox as `dev-r` = %v", err)
		}
	})
	t.Run("a production tenant restored under a sandbox name", func(t *testing.T) {
		oc, _, bundle := restoreFixture(t)
		err := planErr(t, oc, "restore", map[string]any{"from": bundle, "as": "ctltest-dev-r"})
		if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "PRODUCTION index") {
			t.Fatalf("restoring `dev` as a sandbox name = %v", err)
		}
	})
}

func anyWarning(p *jobs.Planned, needle string) bool {
	for _, w := range p.Plan.Warnings {
		if strings.Contains(w, needle) {
			return true
		}
	}
	return false
}
