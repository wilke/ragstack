package ops

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// runner is the engine's step loop, cut down to what a step can observe: a
// durable model.Step to checkpoint into, the op Context, and the reservations
// and logs the engine would persist.
//
// Its Checkpoint writes into the FAKE's call log as well as the step, so the
// assertions below can see that an external ID was recorded BEFORE the call
// that created it — the ordering the whole reconcile-on-restart story rests
// on.
type runner struct {
	oc    jobs.Context
	fake  *drivers.Fake
	job   *model.Job
	steps map[int]*model.Step
	resv  []string
}

func newRunner(oc jobs.Context, fake *drivers.Fake) *runner {
	// A real ULID: the registry records last_ops[verb].job_id, and the contract
	// gives it the ULID pattern — a placeholder id would make every registry
	// step fail validation for a reason no real job has.
	return &runner{oc: oc, fake: fake,
		job:   &model.Job{ID: "01ARZ3NDEKTSV4RRFFQ69G5FAV", Principal: "local:3581"},
		steps: map[int]*model.Step{}}
}

func (r *runner) ctx(s jobs.Step) *jobs.StepContext {
	st, ok := r.steps[s.Plan.N]
	if !ok {
		st = &model.Step{N: s.Plan.N, Kind: s.Plan.Kind, Title: s.Plan.Title, ExternalIDs: []string{}}
		r.steps[s.Plan.N] = st
	}
	return &jobs.StepContext{
		Job: r.job, Step: st, Ops: r.oc,
		Checkpoint: func(ids ...string) error {
			st.Checkpoint = true
			st.ExternalIDs = append(st.ExternalIDs, ids...)
			r.fake.Note("checkpoint", ids...)
			// Mirror the step records into the job the way the engine keeps
			// them: a run half that reads a checkpoint back through
			// sc.Job.Steps (the bundle id does) must see it here too.
			r.syncJobSteps()
			return nil
		},
		Reserve: func(resource string, _ *time.Time) error {
			r.resv = append(r.resv, resource)
			return nil
		},
		Logf: func(format string, args ...any) {},
	}
}

// syncJobSteps rebuilds job.Steps from the per-step records, in step order.
func (r *runner) syncJobSteps() {
	r.job.Steps = r.job.Steps[:0]
	max := 0
	for n := range r.steps {
		if n > max {
			max = n
		}
	}
	for n := 1; n <= max; n++ {
		if st, ok := r.steps[n]; ok {
			r.job.Steps = append(r.job.Steps, *st)
		}
	}
}

func (r *runner) run(s jobs.Step) (string, error) {
	return s.Run(context.Background(), r.ctx(s))
}

// rollback runs one step's Rollback against the SAME step record its Run used,
// which is what the engine does: the external IDs a Run checkpointed are how
// its Rollback finds the thing to undo.
func (r *runner) rollback(s jobs.Step) (string, error) {
	if s.Rollback == nil {
		return "", fmt.Errorf("step %d (%s) has no rollback", s.Plan.N, s.Plan.Title)
	}
	return s.Rollback(context.Background(), r.ctx(s))
}

// runAll runs every step of a plan and fails on the first error.
func (r *runner) runAll(t *testing.T, p *jobs.Planned) {
	t.Helper()
	for _, s := range p.Steps {
		if _, err := r.run(s); err != nil {
			t.Fatalf("step %d (%s): %v", s.Plan.N, s.Plan.Title, err)
		}
	}
}

func (r *runner) externalIDs(n int) []string {
	if st, ok := r.steps[n]; ok {
		return st.ExternalIDs
	}
	return nil
}

// ---------------------------------------------------------------- lifecycle

func TestStartRunsThroughTheDriversRecordingUnitsBeforeItTouchesThem(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	p := plan(t, oc, "start", nil)
	r := newRunner(oc, fake)
	r.runAll(t, p)

	want := []string{
		"systemd.DaemonReload()",
		"job.checkpoint(unit:ragstack-dev-qdrant.service)",
		"systemd.Start(ragstack-dev-qdrant.service)",
		"job.checkpoint(unit:ragstack-dev-es.service)",
		"systemd.Start(ragstack-dev-es.service)",
		"job.checkpoint(unit:ragstack-dev-api.service)",
		"systemd.Start(ragstack-dev-api.service)",
		"proc.Listening(24040)",
	}
	if got := fake.CallKeys(); strings.Join(got, "|") != strings.Join(want, "|") {
		t.Fatalf("trace =\n  %s\nwant\n  %s", strings.Join(got, "\n  "), strings.Join(want, "\n  "))
	}
	if got := strings.Join(fake.FakeSystemd().ActiveUnits(), ","); got !=
		"ragstack-dev-api.service,ragstack-dev-es.service,ragstack-dev-qdrant.service" {
		t.Errorf("active units = %s", got)
	}
}

func TestAStartStepRollsBackByStoppingWhatItStarted(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	p := plan(t, oc, "start", nil)
	r := newRunner(oc, fake)
	r.runAll(t, p)

	// The engine rolls back in reverse over the steps that succeeded.
	for i := len(p.Steps) - 1; i >= 0; i-- {
		s := p.Steps[i]
		if s.Rollback == nil {
			continue
		}
		if _, err := s.Rollback(context.Background(), r.ctx(s)); err != nil {
			t.Fatalf("rollback of step %d: %v", s.Plan.N, err)
		}
	}
	if units := fake.FakeSystemd().ActiveUnits(); len(units) != 0 {
		t.Errorf("after the rollback these are still active: %v", units)
	}
}

func TestAUnitStepReconcilesFromTheDriverState(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	p := plan(t, oc, "start", nil)
	r := newRunner(oc, fake)
	r.runAll(t, p)

	start := p.Steps[stepIndex(p, "systemd", "start ragstack-dev-api.service")]
	got, err := start.Reconcile(context.Background(), r.ctx(start))
	if err != nil || got != jobs.ReconcileDone {
		t.Fatalf("reconcile of a step that ran = %v (%v), want done", got, err)
	}
	// Somebody stopped it behind the job's back: the step has to be redone.
	if err := fake.Systemd().Stop(context.Background(), "ragstack-dev-api.service"); err != nil {
		t.Fatal(err)
	}
	if got, _ := start.Reconcile(context.Background(), r.ctx(start)); got != jobs.ReconcileRedo {
		t.Errorf("reconcile after an external stop = %v, want redo", got)
	}
}

func TestADriverFailureIsTheStepsFailure(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	p := plan(t, oc, "start", nil)
	boom := errors.New("Job for ragstack-dev-es.service failed")
	fake.Fail("systemd.Start:ragstack-dev-es.service", boom)

	r := newRunner(oc, fake)
	var failed int
	for _, s := range p.Steps {
		if _, err := r.run(s); err != nil {
			if !errors.Is(err, boom) {
				t.Fatalf("step %d: %v", s.Plan.N, err)
			}
			failed = s.Plan.N
			break
		}
	}
	if failed != 3 {
		t.Fatalf("the job stopped at step %d, want the es start (3)", failed)
	}
	// The external ID was recorded even though the call failed — that record
	// is what tells a reconcile which unit to go and look at.
	if got := r.externalIDs(3); len(got) != 1 || got[0] != "unit:ragstack-dev-es.service" {
		t.Errorf("external ids = %v", got)
	}
	if fake.Count("systemd.Start") != 2 {
		t.Errorf("Start calls = %d; the job must not continue past a failed step", fake.Count("systemd.Start"))
	}
}

func TestManualStopSignalsTheVerifiedPidAndProvesThePortIsFree(t *testing.T) {
	oc, fake := fixture(t, "dev", nil)
	// The signalled process really dies: its port goes away.
	fake.FakeProc().StopsListening = map[int]int{4242: 24040}

	p := plan(t, oc, "stop", map[string]any{"force": true})
	r := newRunner(oc, fake)
	r.runAll(t, p)

	want := []string{
		"files.ReadFile(/rag/data/tenants/dev/api-dev.pid)",
		"job.checkpoint(pid:4242)",
		"proc.Signal(4242,/rag/repos/tenants/dev,uvicorn,TERM)",
		"proc.Listening(24040)",
	}
	if got := fake.CallKeys(); strings.Join(got, "|") != strings.Join(want, "|") {
		t.Fatalf("trace =\n  %s\nwant\n  %s", strings.Join(got, "\n  "), strings.Join(want, "\n  "))
	}
	// And when it does NOT die, the verify step says so rather than reporting
	// a stopped tenant.
	oc, fake = fixture(t, "dev", nil)
	p = plan(t, oc, "stop", map[string]any{"force": true})
	r = newRunner(oc, fake)
	if _, err := r.run(p.Steps[0]); err != nil {
		t.Fatal(err)
	}
	if _, err := r.run(p.Steps[1]); !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("a surviving process = %v, want a refusal", err)
	}
}

// ---------------------------------------------------------------- backup

func TestBackupRecordsEverySnapshotNameBeforeItAsksForIt(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	p := plan(t, oc, "backup", map[string]any{"fence": true})
	r := newRunner(oc, fake)
	r.runAll(t, p)

	trace := strings.Join(fake.CallKeys(), "\n")
	if strings.Contains(trace, "gateway.Apply") {
		t.Errorf("a fenced backup published a gateway generation; nothing in the render changes:\n%s", trace)
	}
	for _, want := range []string{
		// the fence: the API unit stops, and that is the whole of it
		"systemd.Stop(ragstack-dev-api.service)",
		// every snapshot name recorded before the call that makes it
		"job.checkpoint(qdrant:pending:chunks)\nqdrant.Snapshot(chunks,http://localhost:24041)",
		"job.checkpoint(qdrant:pending:docs)\nqdrant.Snapshot(docs,http://localhost:24041)",
		// The repository name the contract spells is `ctl-<ts>`; the snapshot
		// inside it is the whole bundle id.
		"job.checkpoint(bundle:20260914T093000Z-backup,es:ctl-20260914T093000Z/20260914t093000z-backup)",
		// The verification: the same directory, re-registered READ-ONLY under
		// a second name, listed, and both registrations dropped again.
		"es.RegisterRepo(verify-20260914T093000Z-backup,/usr/share/elasticsearch/snapshots/20260914T093000Z-backup,true,http://localhost:24043)",
		"es.Snapshots(verify-20260914T093000Z-backup,http://localhost:24043)",
		"es.UnregisterRepo(verify-20260914T093000Z-backup,http://localhost:24043)",
		"es.UnregisterRepo(ctl-20260914T093000Z,http://localhost:24043)",
		// and the tenant comes back
		"systemd.Start(ragstack-dev-api.service)",
	} {
		if !strings.Contains(trace, want) {
			t.Errorf("the trace does not contain\n%s\ntrace:\n%s", want, trace)
		}
	}
	// The fence really fenced: the API port was gone when it was checked.
	idx := func(sub string) int { return strings.Index(trace, sub) }
	if idx("proc.Listening(24040)") < idx("systemd.Stop(ragstack-dev-api.service)") {
		t.Error("the fence was verified before the API was stopped")
	}
	if names := fake.FakeQdrant().Snapshots["docs"]; len(names) != 1 {
		t.Errorf("qdrant snapshot ledger = %v", fake.FakeQdrant().Snapshots)
	}
	// The ES snapshot covers EXACTLY the inventory this part recorded. The
	// driver used to snapshot `*`, which is not the same set: Indices() leaves
	// out the cluster's own dot-prefixed system indices, so the bundle held
	// indices its manifest never listed.
	if got := fake.FakeElasticsearch().SnapshotIndices; len(got) != 1 ||
		!strings.HasSuffix(got[0], " dev-chunks") {
		t.Errorf("es snapshots = %v, want one over the inventory (dev-chunks)", got)
	}
	// The bundle landed under the backups root with the run-time stamp.
	var manifest string
	for _, path := range fake.FakeFiles().Paths() {
		if strings.HasSuffix(path, "manifest.json") {
			manifest = path
		}
	}
	if manifest != "/rag/backups/tenants/dev/20260914T093000Z-backup/manifest.json" {
		t.Errorf("bundle manifest at %q", manifest)
	}
	if body := string(fake.FakeFiles().Content(manifest)); !strings.Contains(body, `"fenced": true`) {
		t.Errorf("manifest =\n%s", body)
	}
}

// TestARolledBackBackupRecordPutsTheOldRecoveryPointBack is the MEDIUM
// finding: `last_backup` pointing at a directory that is not there.
//
// The record step is not the last step of a fenced backup — the fence release
// comes after it — so a failure there rolls this step back. The finalize step's
// own rollback renames the bundle back to `<id>.partial`, and a record step
// with no rollback left the row naming the finished path: a recovery point an
// operator would go looking for and not find, in place of the one that really
// is on disk.
func TestARolledBackBackupRecordPutsTheOldRecoveryPointBack(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	seedState(fake, "dev")
	// The recovery point this tenant already had, and must still have.
	prev := &registry.BackupRecord{
		Bundle: "/rag/backups/tenants/dev/20260901T000000Z-backup", At: "2026-09-01T00:00:00Z",
		Kind: "backup", Fenced: true, Verified: true, Scope: fullScope,
	}
	oc.Fleet.Tenants["dev"].LastBackup = prev

	p, r := runBackup(t, oc, fake, map[string]any{"fence": true})
	rec := p.Steps[stepIndex(p, "registry", "record the bundle as this tenant's last backup")]
	if got := oc.Fleet.Tenants["dev"].LastBackup; got == nil || got.Bundle != bundlePath("dev") {
		t.Fatalf("after the run last_backup = %+v, want the new bundle", got)
	}
	// The previous record was written DURABLY before the row was changed: a
	// worker that died here still has it to put back.
	ids := r.externalIDs(rec.Plan.N)
	if _, ok := externalIDValue(ids, prevLastBackupID); !ok {
		t.Fatalf("the step recorded no previous last_backup: %v", ids)
	}

	if _, err := rec.Rollback(context.Background(), r.ctx(rec)); err != nil {
		t.Fatalf("rollback: %v", err)
	}
	got := oc.Fleet.Tenants["dev"].LastBackup
	if got == nil || got.Bundle != prev.Bundle || !got.Verified || got.At != prev.At {
		t.Fatalf("after the rollback last_backup = %+v, want the previous record %+v", got, prev)
	}
}

// TestARolledBackFirstBackupLeavesNoRecoveryPointAtAll is the same rule for a
// tenant that had never been backed up: the row goes back to naming nothing,
// not to naming a bundle the finalize rollback has just un-named.
func TestARolledBackFirstBackupLeavesNoRecoveryPointAtAll(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	seedState(fake, "dev")
	oc.Fleet.Tenants["dev"].LastBackup = nil

	p, r := runBackup(t, oc, fake, map[string]any{"fence": true})
	rec := p.Steps[stepIndex(p, "registry", "record the bundle as this tenant's last backup")]
	if _, err := rec.Rollback(context.Background(), r.ctx(rec)); err != nil {
		t.Fatalf("rollback: %v", err)
	}
	if got := oc.Fleet.Tenants["dev"].LastBackup; got != nil {
		t.Fatalf("after the rollback last_backup = %+v, want none", got)
	}
}

// TestBackupWritesEveryLegIntoOneStampedBundle is finding 4's regression: the
// sqlite leg baked the plan's `new-bundle` PLACEHOLDER into the path it wrote
// at run time, so every backup this tenant ever took overwrote one directory
// and no `<stamp>-backup` bundle held any state at all.
func TestBackupWritesEveryLegIntoOneStampedBundle(t *testing.T) {
	oc, fake := fixtureEnv(t, "dev", managed, tenantEnv())
	// The state databases exist, so the sqlite leg copies rather than skips.
	for _, db := range sqliteDBs {
		fake.FakeFiles().Put("/rag/data/tenants/dev/state/"+db.File, []byte("sqlite-"+db.File), 0o640)
	}
	p := plan(t, oc, "backup", map[string]any{"fence": true})
	r := newRunner(oc, fake)
	r.runAll(t, p)

	want := "/rag/backups/tenants/dev/20260914T093000Z-backup/"
	var bundled []string
	for _, path := range fake.FakeFiles().Paths() {
		if strings.Contains(path, "/backups/") {
			bundled = append(bundled, path)
		}
		if strings.Contains(path, bundlePlaceholder) {
			t.Errorf("%s carries the PLAN's placeholder %q; the run must stamp the real bundle id",
				path, bundlePlaceholder)
		}
	}
	for _, db := range sqliteDBs {
		if got := string(fake.FakeFiles().Content(want + "state/" + db.File)); got != "sqlite-"+db.File {
			t.Errorf("%s is not in the stamped bundle; the bundle holds %v", db.File, bundled)
		}
	}
	if string(fake.FakeFiles().Content(want+"manifest.json")) == "" {
		t.Errorf("the manifest is not in the same bundle as the state copies; bundle holds %v", bundled)
	}
	// One id, checkpointed, for the whole job.
	ids := map[string]bool{}
	for _, c := range fake.Calls() {
		if c.Driver != "job" {
			continue
		}
		for _, id := range c.Args {
			if strings.HasPrefix(id, bundleIDPrefix) {
				ids[id] = true
			}
		}
	}
	if len(ids) != 1 {
		t.Errorf("the job checkpointed %d bundle ids (%v); a backup writes ONE bundle", len(ids), ids)
	}
}

// TestBackupFailsAStateFileItCannotREAD is the other half of finding 4: every
// read error was reported as `absent`, so an EACCES produced a succeeded step
// and a manifest claiming `consistent: true` over a bundle with a hole in it.
func TestBackupFailsAStateFileItCannotREAD(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	boom := errors.New("permission denied")
	fake.Fail("sqlite.Backup:/rag/data/tenants/dev/state/ragstack_users.db", boom)
	p := plan(t, oc, "backup", map[string]any{"fence": true})
	r := newRunner(oc, fake)
	var got error
	for _, s := range p.Steps {
		if _, err := r.run(s); err != nil {
			got = err
			break
		}
	}
	if got == nil || !strings.Contains(got.Error(), "permission denied") {
		t.Fatalf("an unreadable state database = %v, want the step to FAIL rather than call it absent", got)
	}
}

// ---------------------------------------------------------------- credentials

func TestKeyMintWritesTheLedgerBacksItUpAndDeliversTheValueOnce(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	p := plan(t, oc, "key-mint", map[string]any{"label": "ops", "role": "admin"})
	r := newRunner(oc, fake)
	r.runAll(t, p)

	secretsEnv := "/rag/data/tenants/dev/config/secrets.env"
	body := string(fake.FakeFiles().Content(secretsEnv))
	if body == "" {
		t.Fatalf("secrets.env was not written; files = %v", fake.FakeFiles().Paths())
	}
	// The JSON triple stays single-quoted compact JSON — the file is shell-
	// sourced, and a double-quoted value would lose the inner quotes.
	for _, key := range []string{"API_KEYS='[", "API_KEY_TENANTS='{", "API_KEY_ROLES='{"} {
		if !strings.Contains(body, key) {
			t.Errorf("secrets.env does not carry %s:\n%s", key, body)
		}
	}
	// The value is delivered through the envelope and appears nowhere else.
	got := p.Secrets()
	if len(got) != 1 || got[0].ID != "ops" || got[0].Role != "admin" || len(got[0].Value) != 64 {
		t.Fatalf("delivered secrets = %+v", got)
	}
	if !strings.Contains(body, got[0].Value) {
		t.Error("the minted key is not in the file it was minted into")
	}
	if strings.Contains(strings.Join(fake.CallKeys(), " "), got[0].Value) {
		t.Error("the minted value reached the call log")
	}
	// The previous content is one file away, and the ORIGINAL key still works
	// from it: a mint never rewrites what was there.
	var bak string
	for _, path := range fake.FakeFiles().Paths() {
		if strings.Contains(path, ".bak-key-mint-") {
			bak = path
		}
	}
	if bak != secretsEnv+".bak-key-mint-20260914T093000Z" {
		t.Fatalf("backup at %q", bak)
	}
	if old := string(fake.FakeFiles().Content(bak)); !strings.Contains(old, testSecret) || strings.Contains(old, got[0].Value) {
		t.Error("the backup is not the file as it was before the mint")
	}
	// And the old key survived the edit: a mint adds, it does not replace.
	if !strings.Contains(body, testSecret) {
		t.Error("the mint dropped the existing key")
	}
}

func TestKeyRevokeRemovesTheKeyTheLedgerFingerprintNames(t *testing.T) {
	oc, fake := fixture(t, "dev", func(tn *registry.Tenant) {
		managed(tn)
		// Complete ledger rows: `key revoke` now WRITES the registry, so the
		// rows it leaves behind have to satisfy the contract — which they did
		// not when nothing ever validated a row a test had invented.
		tn.Keys = []registry.Key{
			ledgerKey("ops", "admin", fingerprint(testSecret)),
			ledgerKey("survivor", "admin", "sha256:ffffffffffffffff"),
		}
	})
	p := plan(t, oc, "key-revoke", map[string]any{"id": "ops"})
	r := newRunner(oc, fake)
	r.runAll(t, p)

	body := string(fake.FakeFiles().Content("/rag/data/tenants/dev/config/secrets.env"))
	if strings.Contains(body, testSecret) {
		t.Errorf("the revoked key is still in the file:\n%s", body)
	}
	// envfile quotes what needs quoting: a ledger holding a key carries `"`
	// and comes out single-quoted (see the mint test), an empty list needs no
	// quoting at all.
	if !strings.Contains(body, "API_KEYS=[]") || !strings.Contains(body, "API_KEY_ROLES={}") {
		t.Errorf("the ledger is not the empty list it should be:\n%s", body)
	}
	if p.Result()["pending_until_restart"] != true {
		t.Errorf("a revoke without a restart is pending; result = %v", p.Result())
	}
}

// ledgerKey is a contract-complete registry.Key for the fixtures.
func ledgerKey(id, role, fp string) registry.Key {
	return registry.Key{
		ID: id, Label: id, Role: role, TenantString: "dev", Fingerprint: fp,
		CreatedAt: "2026-09-14T09:00:00Z", CreatedBy: "local:3581", Effective: true,
	}
}

func TestAnEnvEditRollsBackFromItsOwnBackup(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	before := string(fake.FakeFiles().Content("/rag/data/tenants/dev/config/tenant.env"))
	p := plan(t, oc, "env-set", map[string]any{"key": "LOG_LEVEL", "value": "DEBUG"})
	r := newRunner(oc, fake)
	r.runAll(t, p)
	if after := string(fake.FakeFiles().Content("/rag/data/tenants/dev/config/tenant.env")); !strings.Contains(after, "LOG_LEVEL=DEBUG") {
		t.Fatalf("the edit did not land:\n%s", after)
	}
	s := p.Steps[0]
	if _, err := s.Rollback(context.Background(), r.ctx(s)); err != nil {
		t.Fatalf("rollback: %v", err)
	}
	if got := string(fake.FakeFiles().Content("/rag/data/tenants/dev/config/tenant.env")); got != before {
		t.Errorf("after the rollback:\n%s\nwant:\n%s", got, before)
	}
}

func TestEnvNormalizeSplitsTheFileInTwo(t *testing.T) {
	oc, fake := fixtureEnv(t, "dev", managed, messyEnv())
	p := plan(t, oc, "env-normalize", nil)
	newRunner(oc, fake).runAll(t, p)

	public := string(fake.FakeFiles().Content("/rag/data/tenants/dev/config/tenant.env"))
	secrets := string(fake.FakeFiles().Content("/rag/data/tenants/dev/config/secrets.env"))
	if strings.Contains(public, "API_KEYS=") {
		t.Errorf("a secret-class key stayed in tenant.env:\n%s", public)
	}
	if !strings.Contains(secrets, "API_KEYS=") || !strings.Contains(secrets, testSecret) {
		t.Errorf("secrets.env did not receive the ledger:\n%s", secrets)
	}
	if !strings.Contains(public, "# raised for the demo\nMAX_TOP_K=50") {
		t.Errorf("the inline comment was not moved:\n%s", public)
	}
}

// ---------------------------------------------------------------- quarantine

func TestDecommissionRenamesTheTreeAndCanPutItBack(t *testing.T) {
	oc, fake := fixture(t, "dev", func(tn *registry.Tenant) {
		managed(tn)
		tn.LastBackup = &registry.BackupRecord{Bundle: "/rag/backups/tenants/dev/20260914T093000Z-backup",
			At: "2026-09-14T09:30:00Z", Kind: "backup", Fenced: true, Verified: true, Scope: fullScope}
	})
	p := plan(t, oc, "decommission", nil)
	r := newRunner(oc, fake)
	r.runAll(t, p)

	quarantined := "/rag/data/tenants/dev.quarantined-20260914T093000Z/config/tenant.env"
	if fake.FakeFiles().Content(quarantined) == nil {
		t.Fatalf("the tree was not renamed; files = %v", fake.FakeFiles().Paths())
	}
	// Nothing was deleted, and the gateway published a generation without it.
	if len(fake.FakeGateway().Applies) != 1 {
		t.Errorf("gateway applies = %v", fake.FakeGateway().Applies)
	}
	// The ONLY deletions are the ctl's own rendered unit files: v1 never
	// deletes a byte of a tenant's data.
	for _, c := range fake.Calls() {
		if c.Key() != "files.Remove" {
			continue
		}
		if !strings.HasPrefix(c.Args[0], "/rag/config/ctl/units/") {
			t.Errorf("decommission deleted %s; v1 removes its own unit files and nothing else", c.Args[0])
		}
	}
	// The rename is the step that has to be reversible: rolling it back is
	// how an operator gets a tenant they quarantined by mistake back.
	var rename jobs.Step
	for _, s := range p.Steps {
		if strings.Contains(s.Plan.Title, "quarantine the data directory") {
			rename = s
		}
	}
	if rename.Rollback == nil {
		t.Fatalf("the quarantine rename has no rollback: %v", titles(p))
	}
	if _, err := rename.Rollback(context.Background(), r.ctx(rename)); err != nil {
		t.Fatalf("rollback: %v", err)
	}
	if fake.FakeFiles().Content("/rag/data/tenants/dev/config/tenant.env") == nil {
		t.Errorf("the rollback did not put the tree back; files = %v", fake.FakeFiles().Paths())
	}
}

func TestGatewayApplyAndReloadGoStraightThroughTheDriver(t *testing.T) {
	oc, fake := fixture(t, "dev", nil)
	oc.Tenant = nil
	apply := plan(t, oc, "gateway-apply", nil)
	r := newRunner(oc, fake)
	r.runAll(t, apply)
	if fake.FakeGateway().Generation != 1 {
		t.Errorf("generation = %d", fake.FakeGateway().Generation)
	}
	if got := r.externalIDs(1); len(got) != 2 || got[1] != "gateway:gen:1" {
		t.Errorf("external ids = %v, want the intent and then the number", got)
	}
	reload := plan(t, oc, "gateway-reload", nil)
	newRunner(oc, fake).runAll(t, reload)
	if len(fake.FakeGateway().Reloads) != 1 {
		t.Errorf("reloads = %v", fake.FakeGateway().Reloads)
	}
	if fake.FakeGateway().Generation != 1 {
		t.Error("a reload published a generation")
	}
}

// ---------------------------------------------------------------- create

// createRunner is `create` planned and ready to run against the fake host:
// the artifact's node_modules are there, and the API port comes up when the
// target starts (the fake links units to ports; a target is not a unit with a
// port, so the fixture says so directly).
func createRunner(t *testing.T, args map[string]any) (*jobs.Planned, *runner, jobs.Context, *drivers.Fake) {
	t.Helper()
	oc, fake := createFixture(t)
	art := oc.Fleet.Artifacts["v1.5.3"]
	fake.FakeBuild().Installed[art.Worktree] = true
	p := plan(t, oc, "create", args)
	// The tenant's API answers as soon as its target is started.
	fake.FakeProc().Ports[oc.Fleet.PortBase+4*oc.Fleet.PortStride] = true
	return p, newRunner(oc, fake), oc, fake
}

func TestCreateRunsEveryStepAgainstTheFakeHost(t *testing.T) {
	p, r, oc, fake := createRunner(t, map[string]any{
		"name": "sandbox", "artifact_id": "v1.5.3",
		"keys":             []any{map[string]any{"label": "ops", "role": "user"}},
		"service_accounts": []any{map[string]any{"subject": "gowe", "role": "user", "purpose": "workflows"}},
	})
	r.runAll(t, p)

	// 1. The registry row exists, is active, and carries FINGERPRINTS.
	row, ok := oc.Fleet.Tenants["sandbox"]
	if !ok {
		t.Fatal("no registry row for the tenant that was just created")
	}
	if row.State != "active" || row.DesiredBoot != "enabled" || row.EnvLayout != "managed" {
		t.Errorf("row = state %q, desired_boot %q, env_layout %q", row.State, row.DesiredBoot, row.EnvLayout)
	}
	if row.Ports.Base != 24080 || row.Ports.PG != 24085 {
		t.Errorf("ports = %+v", row.Ports)
	}
	if len(row.Keys) != 2 {
		t.Fatalf("key ledger has %d rows, want the bootstrap admin plus the one asked for: %+v", len(row.Keys), row.Keys)
	}
	if row.Keys[0].ID != bootstrapAdminLabel || row.Keys[0].Role != "admin" {
		t.Errorf("the bootstrap admin is not the first ledger row: %+v", row.Keys[0])
	}
	for _, k := range row.Keys {
		if !strings.HasPrefix(k.Fingerprint, "sha256:") || len(k.Fingerprint) != len("sha256:")+16 {
			t.Errorf("key %q fingerprint = %q", k.ID, k.Fingerprint)
		}
	}
	if row.LastOps["create"].Outcome != "succeeded" || row.LastOps["create"].JobID != r.job.ID {
		t.Errorf("last_ops.create = %+v", row.LastOps["create"])
	}
	if err := oc.Fleet.ValidateContract(); err != nil {
		t.Errorf("the registry the create wrote does not match the contract: %v", err)
	}

	// 2. The files. secrets.env holds the ledger; tenant.env holds none of it.
	files := fake.FakeFiles()
	secrets := string(files.Content("/rag/data/tenants/sandbox/config/secrets.env"))
	if !strings.Contains(secrets, "API_KEYS=") || !strings.Contains(secrets, "API_KEY_ROLES=") {
		t.Errorf("secrets.env has no key ledger:\n%s", secrets)
	}
	env := string(files.Content("/rag/data/tenants/sandbox/config/tenant.env"))
	if strings.Contains(env, "API_KEY") {
		t.Errorf("tenant.env carries a credential:\n%s", env)
	}
	if !strings.Contains(env, "QDRANT_URL=http://localhost:24081") {
		t.Errorf("tenant.env is not this tenant's:\n%s", env)
	}
	if got := files.Content("/rag/data/tenants/sandbox/config/provision.env"); !strings.Contains(string(got),
		"TENANT_STORE_KIND=sqlite") {
		t.Errorf("provision.env = %s", got)
	}
	// The tree was made 2770 so the group is inherited, and the secrets file
	// 0640 so only the ctl account and its group can read it.
	if mode := files.Dirs["/rag/data/tenants/sandbox"]; mode != 0o2770 {
		t.Errorf("the tenant dir was created %04o, want 2770 (setgid)", mode)
	}
	if mode := files.Files["/rag/data/tenants/sandbox/config/secrets.env"].Mode; mode != 0o640 {
		t.Errorf("secrets.env mode = %04o", mode)
	}

	// 3. The units are on disk AND linked, and the target was enabled+started.
	for _, unit := range []string{"ragstack-sandbox-qdrant.service", "ragstack-sandbox-es.service",
		"ragstack-sandbox-api.service", "ragstack-sandbox.target"} {
		if files.Content("/rag/config/ctl/units/"+unit) == nil {
			t.Errorf("unit %s was not written", unit)
		}
		if fake.FakeSystemd().Linked[unit] != "/rag/config/ctl/units/"+unit {
			t.Errorf("unit %s was not linked (linked = %v)", unit, fake.FakeSystemd().Linked[unit])
		}
	}
	if got := fake.FakeSystemd().ActiveUnits(); strings.Join(got, ",") != "ragstack-sandbox.target" {
		t.Errorf("active units = %v, want just the target (the ctl starts the target, not each leg)", got)
	}

	// 4. The worktree, the UI build, the service account and the gateway.
	if sha := fake.FakeGit().Worktrees["/rag/repos/tenants/sandbox"]; sha != strings.Repeat("ab", 20) {
		t.Errorf("worktree sha = %q", sha)
	}
	if got := fake.FakeBuild().Builds; len(got) != 1 ||
		!strings.Contains(got[0], "/ragstack/sandbox/ui/ /rag/data/tenants/sandbox/ui/dist") {
		t.Errorf("UI builds = %v", got)
	}
	if got := fake.FakeTenantAPI().Accounts; len(got) != 1 || !strings.Contains(got[0], "create gowe user") {
		t.Errorf("service accounts = %v", got)
	}
	if fake.FakeGateway().Generation == 0 {
		t.Error("no gateway generation was published")
	}

	// 5. The credentials are delivered ONCE, through the envelope, and the
	// result carries fingerprints only.
	secretsOut := p.Secrets()
	if len(secretsOut) != 2 {
		t.Fatalf("the envelope carries %d secrets, want 2", len(secretsOut))
	}
	for _, s := range secretsOut {
		if len(s.Value) != 64 {
			t.Errorf("secret %q is %d characters, want token_hex(32)", s.Label, len(s.Value))
		}
		if strings.Contains(secrets, s.Value) != true {
			t.Errorf("the minted value for %q is not in the file that was written", s.Label)
		}
		// And nowhere else: not in the plan, not in the result, not in a target.
		for _, st := range p.Steps {
			if strings.Contains(st.Plan.Title+strings.Join(st.Plan.Targets, " "), s.Value) {
				t.Errorf("step %d leaks the %q credential", st.Plan.N, s.Label)
			}
		}
		for _, id := range r.externalIDs(3) {
			if strings.Contains(id, s.Value) {
				t.Errorf("a checkpoint leaks the %q credential", s.Label)
			}
		}
	}
	res := p.Result()
	blob, err := json.Marshal(res)
	if err != nil {
		t.Fatal(err)
	}
	for _, s := range secretsOut {
		if strings.Contains(string(blob), s.Value) {
			t.Fatalf("the job RESULT carries the %q credential", s.Label)
		}
	}
	if len(res["keys"].([]any)) != 2 {
		t.Errorf("result keys = %v", res["keys"])
	}
}

// secrets.env is the only copy of credentials that are never shown again, so
// `create` refuses to overwrite one — and refuses for the right reason, not
// because a read failed.
func TestCreateRefusesToOverwriteAnExistingSecretsFile(t *testing.T) {
	p, r, _, fake := createRunner(t, map[string]any{"name": "sandbox", "artifact_id": "v1.5.3"})
	fake.FakeFiles().Put("/rag/data/tenants/sandbox/config/secrets.env", []byte("API_KEYS='[]'\n"), 0o640)
	var err error
	for _, s := range p.Steps {
		if _, err = r.run(s); err != nil {
			break
		}
	}
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "already exists") {
		t.Fatalf("error = %v, want a refusal naming the existing file", err)
	}
	if got := string(fake.FakeFiles().Content("/rag/data/tenants/sandbox/config/secrets.env")); got != "API_KEYS='[]'\n" {
		t.Errorf("the existing secrets file was modified: %q", got)
	}
}

// TestCreateRefusesADataDirectoryThatIsNotEmpty is the other half of the
// rename-aside rollback below.
//
// A create or restore that failed leaves `<data_dir>.failed-<ts>` — and an
// operator who moves that tree back, or a create over a tenant somebody laid
// down by hand, must not lay a second tenant on top of the first one's store
// files. An EMPTY directory is fine: that is what a rollback with nothing to
// preserve leaves, and what the deployment's own layout may already have.
func TestCreateRefusesADataDirectoryThatIsNotEmpty(t *testing.T) {
	p, r, _, fake := createRunner(t, map[string]any{"name": "sandbox", "artifact_id": "v1.5.3"})
	fake.FakeFiles().Put("/rag/data/tenants/sandbox/qdrant/storage/collections/chunks/segment", []byte("x"), 0o640)

	fs := p.Steps[stepIndex(p, "fs", "create the tenant directories")]
	_, err := r.run(fs)
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "remove or rename it first") {
		t.Fatalf("error = %v, want a refusal naming the non-empty directory", err)
	}
	if !strings.Contains(err.Error(), "/rag/data/tenants/sandbox") {
		t.Errorf("the refusal does not name the path: %v", err)
	}
	// And it touched nothing: the refusal is before the first MkdirAll.
	if got := string(fake.FakeFiles().Content("/rag/data/tenants/sandbox/qdrant/storage/collections/chunks/segment")); got != "x" {
		t.Errorf("the refused step modified the existing tree: %q", got)
	}
	if fake.Count("files.MkdirAll") != 0 {
		t.Errorf("the refused step made %d directories", fake.Count("files.MkdirAll"))
	}
}

// TestCreateRefusesAnEmptyDataDirectoryNot is the exception stated as a test:
// an empty tree is not evidence of anything and never blocks a create.
func TestCreateRefusesAnEmptyDataDirectoryNot(t *testing.T) {
	p, r, _, fake := createRunner(t, map[string]any{"name": "sandbox", "artifact_id": "v1.5.3"})
	if err := fake.Files().MkdirAll(context.Background(), "/rag/data/tenants/sandbox", 0o2770); err != nil {
		t.Fatal(err)
	}
	fs := p.Steps[stepIndex(p, "fs", "create the tenant directories")]
	if _, err := r.run(fs); err != nil {
		t.Fatalf("an EMPTY existing data dir = %v, want the step to proceed", err)
	}
}

// TestAFailedCreateRenamesTheTreeAsideRatherThanDeletingIt is the HIGH finding:
// the rollback used to leave the tree at the tenant's own path, so the plan's
// "what can remain is the empty tree" was false the moment a store had written
// into it — and the next create of that name laid a tenant on top of it.
//
// The rule is a RENAME. The ctl has no recursive delete and a rollback is not
// the place to acquire one: whatever the stores wrote is moved aside, named
// with the job's stamp, and left for a person.
func TestAFailedCreateRenamesTheTreeAsideRatherThanDeletingIt(t *testing.T) {
	p, r, _, fake := createRunner(t, map[string]any{"name": "sandbox", "artifact_id": "v1.5.3"})
	// The git step fails: far enough in that the directories exist and the
	// stores could have written, early enough that nothing is running.
	boom := errors.New("fatal: could not create work tree")
	fake.Fail("git.AddWorktree:/rag/repos/tenants/sandbox", boom)

	ran := []jobs.Step{}
	failed := false
	for _, s := range p.Steps {
		if _, err := r.run(s); err != nil {
			if !errors.Is(err, boom) {
				t.Fatalf("step %d (%s): %v", s.Plan.N, s.Plan.Title, err)
			}
			failed = true
			break
		}
		ran = append(ran, s)
	}
	if !failed {
		t.Fatal("the create ran to the end; this case is about one that does not")
	}
	if len(ran) < 2 {
		t.Fatalf("the job stopped after %d step(s); it must get past the directories", len(ran))
	}
	// A store wrote into the tree before the failure, which is the whole point.
	fake.FakeFiles().Put("/rag/data/tenants/sandbox/qdrant/storage/collections/chunks/segment", []byte("points"), 0o640)

	for i := len(ran) - 1; i >= 0; i-- {
		if ran[i].Rollback == nil {
			continue
		}
		if _, err := ran[i].Rollback(context.Background(), r.ctx(ran[i])); err != nil {
			t.Fatalf("rollback of step %d (%s): %v", ran[i].Plan.N, ran[i].Plan.Title, err)
		}
	}
	// Nothing at the tenant's own path — the name is free for the next attempt.
	if got := fake.FakeFiles().Dirs["/rag/data/tenants/sandbox"]; got != 0 {
		t.Errorf("the rolled-back create left /rag/data/tenants/sandbox in place (mode %04o)", got)
	}
	if anyPathWithPrefix(fake, "/rag/data/tenants/sandbox/") {
		t.Errorf("the rolled-back create left files under the tenant's own path: %v", tenantFiles(fake, "sandbox"))
	}
	// …and the bytes are still there, one rename away, stamped with the job.
	aside := failedTrees(fake, "sandbox")
	if len(aside) != 1 {
		t.Fatalf("trees left aside = %v, want one /rag/data/tenants/sandbox.failed-<ts>", aside)
	}
	if got := string(fake.FakeFiles().Content(aside[0] + "/qdrant/storage/collections/chunks/segment")); got != "points" {
		t.Errorf("%s does not hold what the store wrote (%q): the rollback deleted data", aside[0], got)
	}
	// The stamp is the run's clock, not the plan's placeholder.
	if !strings.HasSuffix(aside[0], ".failed-20260914T093000Z") {
		t.Errorf("the tree aside is named %q; want the job's stamp", aside[0])
	}
}

func TestCreateRollsBackEverythingItMade(t *testing.T) {
	p, r, oc, fake := createRunner(t, map[string]any{"name": "sandbox", "artifact_id": "v1.5.3"})
	r.runAll(t, p)
	for i := len(p.Steps) - 1; i >= 0; i-- {
		s := p.Steps[i]
		if s.Rollback == nil {
			continue
		}
		if _, err := s.Rollback(context.Background(), r.ctx(s)); err != nil {
			t.Fatalf("rollback of step %d (%s): %v", s.Plan.N, s.Plan.Title, err)
		}
	}
	if _, ok := oc.Fleet.Tenants["sandbox"]; ok {
		t.Error("the registry row survived the rollback")
	}
	// No tombstone: nothing ever ran under this allocation, so the port block
	// is not spent. A tombstone here would burn a block per failed create.
	if len(oc.Fleet.Tombstones) != 0 {
		t.Errorf("the rollback left %d tombstone(s): %+v", len(oc.Fleet.Tombstones), oc.Fleet.Tombstones)
	}
	if _, ok := fake.FakeGit().Worktrees["/rag/repos/tenants/sandbox"]; ok {
		t.Error("the worktree survived the rollback")
	}
	if fake.FakeFiles().Content("/rag/data/tenants/sandbox/config/secrets.env") != nil {
		t.Error("secrets.env survived the rollback")
	}
	if units := fake.FakeSystemd().ActiveUnits(); len(units) != 0 {
		t.Errorf("still active after the rollback: %v", units)
	}
	if p.Secrets() != nil {
		t.Error("the envelope survived a rollback: those keys are in no file")
	}
}

func TestCreateWithPostgresLocalStartsAndRecordsTheInstance(t *testing.T) {
	p, r, oc, fake := createRunner(t, map[string]any{
		"name": "sandbox", "artifact_id": "v1.5.3", "postgres": "local",
	})
	r.runAll(t, p)
	row := oc.Fleet.Tenants["sandbox"]
	pg := row.Stores.Postgres
	if pg.Kind != registry.PostgresKindLocal || int(pg.Port) != 24085 || string(pg.URL) != "postgresql://localhost:24085" {
		t.Errorf("stores.postgres = %+v", pg)
	}
	if string(pg.Instance) != "postgres-sandbox" || string(pg.DataDir) != "/rag/data/tenants/sandbox/postgres" {
		t.Errorf("stores.postgres = %+v", pg)
	}
	secrets := string(fake.FakeFiles().Content("/rag/data/tenants/sandbox/config/secrets.env"))
	// TWO names for ONE password. TENANT_PG_PASSWORD is what new-tenant.sh, the
	// runbooks and the registry's secret ref use;
	// APPTAINERENV_POSTGRES_PASSWORD is what gets the value INTO the container
	// without putting it on a command line — the unit loads this file with
	// EnvironmentFile and apptainer forwards APPTAINERENV_<KEY> as <KEY>. The
	// unit used to carry `--env POSTGRES_PASSWORD=${TENANT_PG_PASSWORD}`, which
	// systemd expanded straight into a /proc/<pid>/cmdline every account on the
	// host can read.
	for _, want := range []string{
		"TENANT_PG_PASSWORD=", "APPTAINERENV_POSTGRES_PASSWORD=",
		"USER_STORE_DSN=", "POSTGRES_DSN=", "COLLECTION_STORE_DSN=",
	} {
		if !strings.Contains(secrets, want) {
			t.Errorf("secrets.env lacks %s:\n%s", want, secrets)
		}
	}
	// Same value under both names, or the role the entrypoint creates has a
	// password nothing else knows.
	pgPass, apptainerPass := envValue(secrets, "TENANT_PG_PASSWORD"), envValue(secrets, "APPTAINERENV_POSTGRES_PASSWORD")
	if pgPass == "" || pgPass != apptainerPass {
		t.Errorf("TENANT_PG_PASSWORD=%q but APPTAINERENV_POSTGRES_PASSWORD=%q; they are one secret", pgPass, apptainerPass)
	}
	env := string(fake.FakeFiles().Content("/rag/data/tenants/sandbox/config/tenant.env"))
	if strings.Contains(env, "_DSN") {
		t.Errorf("a DSN (which carries the password) stayed in tenant.env:\n%s", env)
	}
	if !strings.Contains(env, "USER_STORE_BACKEND=postgres") {
		t.Errorf("tenant.env does not point at postgres:\n%s", env)
	}
	// Both writable paths of the instance exist, or apptainer refuses the bind.
	for _, d := range []string{"/rag/data/tenants/sandbox/postgres/data", "/rag/data/tenants/sandbox/postgres/run"} {
		if _, ok := fake.FakeFiles().Dirs[d]; !ok {
			t.Errorf("%s was not created; the unit binds it", d)
		}
	}
	// The registry row must still satisfy the contract with a postgres store.
	if err := oc.Fleet.ValidateContract(); err != nil {
		t.Errorf("the registry does not match the contract: %v", err)
	}
	// And the readiness gate really asked postgres.
	found := false
	for _, k := range fake.CallKeys() {
		if strings.HasPrefix(k, "postgres.Ready(") {
			found = true
		}
	}
	if !found {
		t.Errorf("the readiness gate never probed postgres: %v", fake.CallKeys())
	}
	_ = p
}

// ---------------------------------------------------------------- artifact

func TestArtifactPrepareResolvesChecksOutInstallsAndRecords(t *testing.T) {
	oc, fake := fixture(t, "dev", nil)
	oc.Tenant = nil
	sha := strings.Repeat("cd", 20)
	fake.FakeGit().Refs["v1.6.0"] = sha
	p := plan(t, oc, "artifact-prepare", map[string]any{"tag": "v1.6.0"})
	r := newRunner(oc, fake)
	r.runAll(t, p)

	id := "v1.6.0-" + sha[:12]
	a, ok := oc.Fleet.Artifacts[id]
	if !ok {
		t.Fatalf("no artifact %q; have %v", id, oc.Fleet.Artifacts)
	}
	if a.SHA != sha || a.Tag != "v1.6.0" || a.PythonEnv != "/rag/envs/ragstack" {
		t.Errorf("artifact = %+v", a)
	}
	want := "/rag/data/ctl/artifacts/" + id + "/worktree"
	if a.Worktree != want {
		t.Errorf("worktree = %q, want %q", a.Worktree, want)
	}
	if fake.FakeGit().Worktrees[want] != sha {
		t.Errorf("the worktree was not checked out at the resolved sha: %v", fake.FakeGit().Worktrees)
	}
	if !fake.FakeBuild().Installed[want] {
		t.Error("npm ci did not run in the artifact's worktree")
	}
	if err := oc.Fleet.ValidateContract(); err != nil {
		t.Errorf("the registry the prepare wrote does not match the contract: %v", err)
	}
	if got := p.Result()["artifact_id"]; got != id {
		t.Errorf("result artifact_id = %v", got)
	}

	// An artifact is IMMUTABLE: preparing the same commit again is refused
	// rather than silently replacing a worktree tenants are running from.
	p2 := plan(t, oc, "artifact-prepare", map[string]any{"tag": "v1.6.0"})
	if _, err := r.run(p2.Steps[0]); !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "already exists") {
		t.Errorf("re-preparing = %v, want a refusal", err)
	}
}

func TestArtifactPrepareRefusesWithoutAMirror(t *testing.T) {
	oc, _ := fixture(t, "dev", nil)
	oc.Tenant = nil
	op, ok := NewRegistry(Deps{Roots: oc.Roots, Now: oc.Now}).Lookup("artifact-prepare") // no Mirror
	if !ok {
		t.Fatal("no artifact-prepare op")
	}
	_, err := op.Plan(context.Background(), oc, map[string]any{"tag": "v1"})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "clone --mirror") {
		t.Fatalf("error = %v, want a refusal that says how to create the mirror", err)
	}
}

func TestArtifactIDIsSanitizedAndKeepsTheSha(t *testing.T) {
	sha := strings.Repeat("ab", 20)
	for _, tc := range []struct{ tag, want string }{
		{"v1.5.3", "v1.5.3-" + sha[:12]},
		{"release/1.5", "release-1.5-" + sha[:12]},
		{"feature/a b+c", "feature-a-b-c-" + sha[:12]},
		{"///", "artifact-" + sha[:12]},
		{strings.Repeat("x", 200), strings.Repeat("x", 67) + "-" + sha[:12]},
	} {
		got, err := ArtifactID(tc.tag, sha)
		if err != nil {
			t.Errorf("ArtifactID(%q) = %v", tc.tag, err)
			continue
		}
		if got != tc.want {
			t.Errorf("ArtifactID(%q) = %q, want %q", tc.tag, got, tc.want)
		}
		if !strings.HasSuffix(got, sha[:12]) {
			t.Errorf("ArtifactID(%q) lost the sha: %q", tc.tag, got)
		}
	}
	if _, err := ArtifactID("v1", "short"); err == nil {
		t.Error("an unresolved sha was accepted")
	}
}

// ---------------------------------------------------------------- lock safety
//
// The engine holds `<state>/locks/registry.lock` for the length of a job;
// registry.Save takes `<data>/tenants/registry.json.lock` and
// `manifest.tsv.lock` of its own. Those are different inodes, so the two
// cannot deadlock — but "different inodes" is a fact about the path
// derivation, and a refactor that pointed the engine's lock at the registry's
// file would deadlock every registry-writing job forever, with no error and no
// timeout. So it is asserted rather than reasoned about.
func TestSaveFleetInsideAJobDoesNotDeadlockAgainstTheEngineLocks(t *testing.T) {
	root := t.TempDir()
	roots := paths.NewRoots(root, paths.Overrides{})
	regPath := roots.Registry()
	f := registry.NewFleet(root)
	if err := registry.Save(regPath, f, "test"); err != nil {
		t.Fatal(err)
	}
	if got := jobs.NewLocks(roots).Path(model.LockRegistry, ""); got == regPath+".lock" {
		t.Fatalf("the engine's registry lock IS registry.json.lock (%s): a job that writes the registry would "+
			"deadlock against itself", got)
	}

	// Hold exactly what a `start` job holds, through the real flock files.
	set, err := jobs.NewLocks(roots).Take(
		[]model.LockName{model.LockRegistry, model.LockManifest, model.LockTenant}, "dev",
		jobs.LockHolder{JobID: "01ARZ3NDEKTSV4RRFFQ69G5FAV", PID: os.Getpid(), Since: "2026-09-14T09:30:00Z"},
		time.Now())
	if err != nil {
		t.Fatal(err)
	}
	defer set.Release()

	done := make(chan error, 1)
	go func() {
		loaded, lerr := registry.LoadNoRepair(regPath)
		if lerr != nil {
			done <- lerr
			return
		}
		loaded.DisplayOrder = []string{}
		done <- registry.Save(regPath, loaded, "inside the job")
	}()
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("registry.Save under the engine's locks: %v", err)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("registry.Save blocked for 10s while the engine held its own locks — the two lock sets overlap")
	}
	if got, err := registry.LoadNoRepair(regPath); err != nil || got.Generation != 2 {
		t.Fatalf("after the save: generation %v (%v), want 2", got, err)
	}
}

// envValue reads one KEY=VALUE line out of a rendered env file.
func envValue(body, key string) string {
	for _, line := range strings.Split(body, "\n") {
		if v, ok := strings.CutPrefix(strings.TrimSpace(line), key+"="); ok {
			return v
		}
	}
	return ""
}
