package ops

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
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
	return &runner{oc: oc, fake: fake, job: &model.Job{ID: "job-1"}, steps: map[int]*model.Step{}}
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
			return nil
		},
		Reserve: func(resource string, _ *time.Time) error {
			r.resv = append(r.resv, resource)
			return nil
		},
		Logf: func(format string, args ...any) {},
	}
}

func (r *runner) run(s jobs.Step) (string, error) {
	return s.Run(context.Background(), r.ctx(s))
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

	start := p.Steps[3] // start the api
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
	for _, want := range []string{
		// the fence, in order
		"job.checkpoint(gateway:apply:pending)\ngateway.Apply(false)",
		"systemd.Stop(ragstack-dev-api.service)",
		// every snapshot name recorded before the call that makes it
		"job.checkpoint(qdrant:pending:chunks)\nqdrant.Snapshot(chunks,http://localhost:24041)",
		"job.checkpoint(qdrant:pending:docs)\nqdrant.Snapshot(docs,http://localhost:24041)",
		// The repository name the contract spells is `ctl-<ts>`; the snapshot
		// inside it is the whole bundle id.
		"job.checkpoint(bundle:20260914T093000Z-backup,es:ctl-20260914T093000Z/20260914T093000Z-backup)",
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
		tn.Keys = []registry.Key{
			{ID: "ops", Role: "admin", Fingerprint: fingerprint(testSecret), Effective: true},
			{ID: "survivor", Role: "admin", Fingerprint: "sha256:ffffffffffffffff", Effective: true},
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
		tn.LastBackup = &registry.BackupRecord{Bundle: "20260914T093000Z-backup", Fenced: true, Verified: true}
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
