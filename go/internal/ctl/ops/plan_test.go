package ops

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/settings"
)

// ---------------------------------------------------------------- fixtures

// testSecret is a credential shaped exactly like a real one (64 hex
// characters) and assembled at run time rather than written out, so the
// repository's own canary sweep never has to make an exception for this file.
var testSecret = strings.Repeat("a1b2c3d4", 8)

// redactor is the jobs.Redactor the engine supplies in production, built here
// on the same settings.Redactor the logs endpoint uses.
type redactor struct{ r *settings.Redactor }

func (rd redactor) Redact(s string) string { return rd.r.Redact(s) }

func (rd redactor) RedactArgs(args map[string]any) map[string]any {
	out := make(map[string]any, len(args))
	for k, v := range args {
		if s, ok := v.(string); ok {
			out[k] = rd.r.Redact(s)
			continue
		}
		out[k] = v
	}
	return out
}

// tenantEnv is a plausible legacy tenant.env: public settings, an
// executable-surface key and the key ledger. ALLOWED_ORIGINS carries the
// canary inside a PUBLIC key's value, where only a seeded redactor can find
// it — which is how a preview proves it went through one.
func tenantEnv() []byte {
	return []byte("" +
		"# a hand-edited file\n" +
		"LOG_LEVEL=INFO\n" +
		"ALLOWED_ORIGINS=https://example.test/" + testSecret + "\n" +
		"QDRANT_URL=http://localhost:24041\n" +
		"ADMIN_SUBJECTS='[\"bvbrc:alice\"]'\n" +
		"API_KEYS='[\"" + testSecret + "\"]'\n" +
		"API_KEY_TENANTS='{\"" + testSecret + "\":\"dev\"}'\n" +
		"API_KEY_ROLES='{\"" + testSecret + "\":\"admin\"}'\n")
}

// ledgerEnv is the tenant's secrets.env: the key ledger and nothing else.
func ledgerEnv() []byte {
	return []byte("" +
		"API_KEYS='[\"" + testSecret + "\"]'\n" +
		"API_KEY_TENANTS='{\"" + testSecret + "\":\"dev\"}'\n" +
		"API_KEY_ROLES='{\"" + testSecret + "\":\"admin\"}'\n")
}

// messyEnv is what a hand-edited file actually looks like: an inline comment,
// which systemd keeps as part of the value. It is what `env normalize`
// exists for, and what every other env op refuses over (doctor's
// env_not_systemd_parsable).
func messyEnv() []byte {
	return append(tenantEnv(), []byte("MAX_TOP_K=50   # raised for the demo\n")...)
}

// fixture builds the op Context for one tenant of the live registry fixture,
// with an in-memory host under it.
func fixture(t *testing.T, name string, mutate func(*registry.Tenant)) (jobs.Context, *drivers.Fake) {
	t.Helper()
	return fixtureEnv(t, name, mutate, tenantEnv())
}

// fixtureEnv is fixture with a chosen tenant.env.
func fixtureEnv(t *testing.T, name string, mutate func(*registry.Tenant), env []byte) (jobs.Context, *drivers.Fake) {
	t.Helper()
	roots := paths.NewRoots("/rag", paths.Overrides{})
	f := registry.LiveFixture()
	tenant := f.Tenants[name]
	if tenant == nil {
		t.Fatalf("no fixture tenant %q", name)
	}
	if mutate != nil {
		mutate(tenant)
	}
	// The artifact every ctl-managed fixture tenant was built from. It is in
	// the FLEET rather than only on the row because `restore --as` lays its
	// fresh tenant down from the source's artifact, and a row naming an
	// artifact the fleet does not have is an inconsistent registry rather than
	// a fixture.
	f.Artifacts[testArtifactID] = &registry.Artifact{
		SHA: strings.Repeat("ab", 20), Tag: "v1.5.3",
		Worktree:  "/rag/data/ctl/artifacts/" + testArtifactID + "/worktree",
		UIDist:    "/rag/data/ctl/artifacts/" + testArtifactID + "/worktree/frontend/dist",
		PythonEnv: "/rag/envs/ragstack", PreparedAt: "2026-09-14T09:00:00Z", PreparedBy: "local:3581",
		SchemaCompatible: true,
	}
	tp := paths.TenantPaths(roots, tenant.Name, tenant.ManifestName)
	fake := drivers.NewFake(drivers.FakeOptions{
		Roots: []string{"/rag"},
		// The fixture tenant is routed by the live gateway, so the fence and
		// the quarantine have a route to publish over.
		Routed: []string{name},
		Files: map[string][]byte{
			tp.TenantEnv:  env,
			tp.SecretsEnv: ledgerEnv(),
			tp.PidFile:    []byte("4242\n"),
		},
		// A started unit makes its port listen and a stopped one frees it, so
		// the readiness probes and the fence verify answer a fact.
		UnitPorts:   map[string]int{"ragstack-" + tenant.Name + "-api.service": tenant.Ports.API},
		Listening:   []int{tenant.Ports.API},
		Collections: map[string][]string{tenant.Stores.Qdrant.URL: {"docs", "chunks"}},
		Indices:     map[string][]string{tenant.Stores.Elasticsearch.URL: {"dev-chunks"}},
		// Where this store's snapshots land on the host, so the backup's move
		// into the bundle is moving a file rather than a name.
		QdrantSnapshotDirs: map[string]string{tenant.Stores.Qdrant.URL: tp.QdrantSnapshots},
		QdrantCounts: map[string]int64{
			tenant.Stores.Qdrant.URL + "/docs": 1200, tenant.Stores.Qdrant.URL + "/chunks": 88_000,
		},
		ESCounts: map[string]int64{tenant.Stores.Elasticsearch.URL + "/dev-chunks": 88_000},
		Now:      func() time.Time { return time.Date(2026, 9, 14, 9, 30, 0, 0, time.UTC) },
	})
	return jobs.Context{
		Roots: roots, Fleet: f, Tenant: tenant, Drivers: fake,
		Now:      func() time.Time { return time.Date(2026, 9, 14, 9, 30, 0, 0, time.UTC) },
		Redactor: redactor{settings.NewRedactor(testSecret)},
		Doctor:   model.DoctorResponse{Status: model.StatusGreen, Hash: "sha256:" + strings.Repeat("0", 64)},
	}, fake
}

// testArtifactID is the prepared artifact the fixture fleet carries.
const testArtifactID = "v1.5.3-abababababab"

// managed turns a fixture tenant into one the ctl supervises and owns.
func managed(t *registry.Tenant) {
	t.Supervisor, t.Owner, t.State = "systemd", "svcbvbrc", "active"
	t.ArtifactID = testArtifactID
	t.EnvLayout, t.API.Bind = "managed", "127.0.0.1"
	t.UI = registry.UI{Mode: registry.UIModeStatic, Base: "/ragstack/" + t.Name + "/ui/"}
	caps := registry.Capabilities{Stop: true, Purge: true, Restore: true, Snapshot: true}
	t.Stores.Qdrant.Ownership, t.Stores.Qdrant.Capabilities = registry.OwnershipExclusive, caps
	t.Stores.Elasticsearch.Ownership, t.Stores.Elasticsearch.Capabilities = registry.OwnershipExclusive, caps
}

// testMirror is the bare repository the fixture deployment checks tenant
// worktrees out of.
const testMirror = "/rag/repos/ragstack.git"

// testDeps are the Deps a planned op runs with in these tests: a registry
// writer that validates and bumps the generation IN MEMORY (registry.Save's
// contract without the file), and the configured mirror.
//
// SaveFleet is wired rather than nil because the ops that write the registry
// are the ops whose last step is the one worth asserting: a test whose
// registry step refused would be a test that never executed the step it was
// written for.
func testDeps(oc jobs.Context) Deps {
	return Deps{
		Roots: oc.Roots, Now: oc.Now, Mirror: testMirror,
		SaveFleet: func(f *registry.Fleet) error {
			if err := f.Validate(); err != nil {
				return err
			}
			// registry.Save stamps these three before it validates the document
			// it is about to write; a memory saver that skipped them would fail
			// the contract for a reason the real one never hits.
			f.Generation++
			f.UpdatedAt = oc.Now().UTC().Format(time.RFC3339)
			f.UpdatedBy = "ops tests"
			return f.ValidateContract()
		},
	}
}

// plan runs one verb and returns the planned steps.
func plan(t *testing.T, oc jobs.Context, verb string, args map[string]any) *jobs.Planned {
	t.Helper()
	op, ok := NewRegistry(testDeps(oc)).Lookup(verb)
	if !ok {
		t.Fatalf("no op %q", verb)
	}
	planned, err := op.Plan(context.Background(), oc, args)
	if err != nil {
		t.Fatalf("%s: %v", verb, err)
	}
	return planned
}

// planErr runs one verb expecting a refusal.
func planErr(t *testing.T, oc jobs.Context, verb string, args map[string]any) error {
	t.Helper()
	op, ok := NewRegistry(testDeps(oc)).Lookup(verb)
	if !ok {
		t.Fatalf("no op %q", verb)
	}
	_, err := op.Plan(context.Background(), oc, args)
	if err == nil {
		t.Fatalf("%s: expected a refusal, got a plan", verb)
	}
	return err
}

// titles renders a plan's steps as "kind: title" for a readable assertion.
func titles(p *jobs.Planned) []string {
	out := make([]string, 0, len(p.Steps))
	for _, s := range p.Steps {
		out = append(out, s.Plan.Kind+": "+s.Plan.Title)
	}
	return out
}

// stepWarnings are the warnings of the first step matching kind/substr.
func stepWarnings(p *jobs.Planned, kind, substr string) []string {
	for _, s := range p.Steps {
		if s.Plan.Kind == kind && strings.Contains(s.Plan.Title, substr) {
			return s.Plan.Warnings
		}
	}
	return nil
}

// stepIndex is the index of the first step matching kind/substr, or -1.
func stepIndex(p *jobs.Planned, kind, substr string) int {
	for i, s := range p.Steps {
		if s.Plan.Kind == kind && strings.Contains(s.Plan.Title, substr) {
			return i
		}
	}
	return -1
}

func hasStep(p *jobs.Planned, kind, substr string) bool {
	for _, s := range p.Steps {
		if s.Plan.Kind == kind && strings.Contains(s.Plan.Title, substr) {
			return true
		}
	}
	return false
}

// ---------------------------------------------------------------- lifecycle

func TestPlanStartRefusesAHandStartedTenant(t *testing.T) {
	oc, _ := fixture(t, "dev", nil) // the fixture tenants are all supervisor: manual
	err := planErr(t, oc, "start", nil)
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("error = %v, want a refusal", err)
	}
	if !strings.Contains(err.Error(), "handover") {
		t.Errorf("the refusal does not say what to do instead: %v", err)
	}
}

func TestPlanStartOrdersStoresThenAPIAndGatesOnReadiness(t *testing.T) {
	oc, _ := fixture(t, "dev", managed)
	p := plan(t, oc, "start", nil)
	want := []string{
		"systemd: systemctl --user daemon-reload",
		"systemd: start ragstack-dev-qdrant.service",
		"systemd: start ragstack-dev-es.service",
		"systemd: skip postgres",
		"systemd: start ragstack-dev-api.service",
		"systemd: skip ui",
		"probe: wait for the API to listen on 24040",
		"registry: record dev as active (desired_boot enabled) in the registry",
	}
	if got := titles(p); strings.Join(got, "|") != strings.Join(want, "|") {
		t.Fatalf("steps =\n  %s\nwant\n  %s", strings.Join(got, "\n  "), strings.Join(want, "\n  "))
	}
	if p.Plan.RequiresConfirm {
		t.Error("start is not destructive and must not demand a confirmation")
	}
	for _, s := range p.Steps {
		if s.Plan.Destructive {
			t.Errorf("step %q is marked destructive", s.Plan.Title)
		}
	}
	// The skipped UI step says WHY, and the plan's steps are numbered 1..n.
	for i, s := range p.Steps {
		if s.Plan.N != i+1 {
			t.Errorf("step %d is numbered %d", i+1, s.Plan.N)
		}
	}
	if w := stepWarnings(p, "systemd", "skip ui"); len(w) != 1 || !strings.Contains(w[0], "static") {
		t.Errorf("the skipped UI step does not explain itself: %v", w)
	}
	// The sqlite relational store is skipped for its own reason, not the UI's.
	if w := stepWarnings(p, "systemd", "skip postgres"); len(w) != 1 || !strings.Contains(w[0], "SQLite") {
		t.Errorf("the skipped postgres leg does not explain itself: %v", w)
	}
}

func TestPlanStopNeverTouchesAStoreItMayNotStop(t *testing.T) {
	oc, _ := fixture(t, "dev", func(t *registry.Tenant) {
		managed(t)
		// The plan's rule: capabilities are false until an operator confirmed
		// ownership, and until then the ctl leaves the store alone.
		t.Stores.Qdrant.Capabilities.Stop = false
	})
	p := plan(t, oc, "stop", map[string]any{"keep_enabled": true})
	for _, s := range p.Steps {
		if s.Plan.Kind == "systemd" && strings.Contains(s.Plan.Title, "stop ragstack-dev-qdrant") {
			t.Fatalf("the plan stops a store whose capabilities.stop is false: %v", titles(p))
		}
	}
	if !hasStep(p, "systemd", "skip qdrant") {
		t.Errorf("the untouchable store is not reported as skipped: %v", titles(p))
	}
	if !hasStep(p, "systemd", "stop ragstack-dev-es.service") {
		t.Errorf("the store it MAY stop was not stopped: %v", titles(p))
	}
	// keep_enabled leaves desired_boot alone.
	if hasStep(p, "systemd", "disable") {
		t.Errorf("keep_enabled must not disable anything: %v", titles(p))
	}
	if p.Result()["desired_boot"] != nil {
		t.Errorf("keep_enabled recorded desired_boot = %v", p.Result()["desired_boot"])
	}
}

func TestPlanStopDisablesBootUnlessKeepEnabled(t *testing.T) {
	oc, _ := fixture(t, "dev", managed)
	p := plan(t, oc, "stop", nil)
	if !hasStep(p, "systemd", "disable ragstack-dev-api.service") {
		t.Errorf("stop did not disable the boot entry: %v", titles(p))
	}
	if p.Result()["desired_boot"] != "disabled" {
		t.Errorf("result desired_boot = %v", p.Result()["desired_boot"])
	}
	if !p.Plan.RequiresConfirm || string(p.Plan.ConfirmValue) != "dev" {
		t.Errorf("a destructive tenant op must be confirmed with the tenant name, got %q (required %v)",
			p.Plan.ConfirmValue, p.Plan.RequiresConfirm)
	}
}

func TestPlanStopForceSignalsThePidfileOfAHandStartedTenant(t *testing.T) {
	oc, _ := fixture(t, "dev", nil)
	if err := planErr(t, oc, "stop", nil); !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("stop without force = %v, want a refusal", err)
	}
	p := plan(t, oc, "stop", map[string]any{"force": true})
	want := []string{
		"proc: stop the hand-started API through its pidfile, after verifying cwd and cmdline",
		"probe: verify nothing listens on 24040 any more",
	}
	if got := titles(p); strings.Join(got, "|") != strings.Join(want, "|") {
		t.Fatalf("steps = %v, want %v", got, want)
	}
	if !p.Steps[0].Plan.Destructive {
		t.Error("signalling a process is a destructive step")
	}
	if got := p.Steps[0].Plan.Targets; len(got) != 1 || !strings.HasSuffix(got[0], "api-dev.pid") {
		t.Errorf("targets = %v, want the pidfile", got)
	}
}

// ---------------------------------------------------------------- backup

func TestPlanBackupFencesInOrderAndUnfencesAfterwards(t *testing.T) {
	oc, _ := fixture(t, "dev", managed)
	p := plan(t, oc, "backup", map[string]any{"fence": true})
	want := []string{
		// The precheck comes BEFORE the fence: refusing for want of disk
		// after the API is stopped would be an outage for nothing.
		"probe: check the backup filesystem has room",
		"nginx: gateway: publish a generation serving dev read-only",
		"systemd: stop ragstack-dev-api.service",
		"probe: fence verify: nothing listens on 24040",
		"fs: create the bundle directory (written as <id>.partial, mode 2770)",
		"qdrant: snapshot every qdrant collection into the bundle",
		"es: snapshot every elasticsearch index into a per-bundle repo, verify it and move it into the bundle",
		"sqlitebackup: copy ragstack_users.db into the bundle",
		"sqlitebackup: copy ragstack_jobs.db into the bundle",
		"sqlitebackup: copy ragstack_collections.db into the bundle",
		"sqlitebackup: copy ragstack_grading.db into the bundle",
		"postgres: skip the postgres leg",
		"fs: copy the public config allowlist, the manifests and the rendered units into the bundle",
		"fs: skip the encrypted secrets payload",
		"fs: write MIGRATE.md, the bundle's own runbook",
		"fs: write SHA256SUMS and the bundle manifest",
		// The rename is the LAST write: until it happens the directory is
		// `.partial` and no reader mistakes it for a finished bundle.
		"fs: rename the bundle into place (drop the .partial suffix)",
		"registry: record the bundle as this tenant's last backup",
		"systemd: start ragstack-dev-api.service",
		"probe: wait for the API to listen on 24040",
		"nginx: gateway: publish a generation serving dev read-write",
	}
	if got := titles(p); strings.Join(got, "|") != strings.Join(want, "|") {
		t.Fatalf("steps =\n  %s\nwant\n  %s", strings.Join(got, "\n  "), strings.Join(want, "\n  "))
	}
	if p.Result()["fenced"] != true || p.Result()["best_effort"] != false {
		t.Errorf("result = %v", p.Result())
	}
	// The bundle's paths carry a placeholder, never a clock: a plan is
	// computed twice and compared.
	for _, s := range p.Steps {
		for _, w := range s.Plan.WouldWrite {
			if strings.Contains(w.Path, "2026") {
				t.Errorf("step %q names a timestamped path in the PLAN: %s", s.Plan.Title, w.Path)
			}
		}
	}
}

func TestPlanBackupUnfencedIsBestEffortAndSkipsTheFence(t *testing.T) {
	oc, _ := fixture(t, "dev", managed)
	p := plan(t, oc, "backup", nil)
	if hasStep(p, "nginx", "read-only") || hasStep(p, "probe", "fence verify") {
		t.Errorf("an unfenced backup fenced anyway: %v", titles(p))
	}
	if p.Result()["best_effort"] != true {
		t.Errorf("result = %v", p.Result())
	}
	if len(p.Plan.Warnings) == 0 || !strings.Contains(p.Plan.Warnings[0], "best_effort") {
		t.Errorf("warnings = %v", p.Plan.Warnings)
	}
}

func TestPlanBackupExcludesAStoreItMayNotSnapshot(t *testing.T) {
	oc, _ := fixture(t, "asm-next", nil) // shared stores, every capability false
	p := plan(t, oc, "backup", nil)
	if !hasStep(p, "qdrant", "skip the qdrant leg") || !hasStep(p, "es", "skip the elasticsearch leg") {
		t.Fatalf("a shared store was not excluded: %v", titles(p))
	}
	for _, s := range p.Steps {
		if s.Plan.Kind == "qdrant" && !strings.Contains(s.Plan.Title, "skip") {
			t.Errorf("the plan snapshots a store it does not own: %q", s.Plan.Title)
		}
	}
}

func TestPlanRestoreOnlyEverIntoAFreshTenant(t *testing.T) {
	oc, _ := fixture(t, "dev", managed)
	bundle := "20260914T093000Z-backup"
	err := planErr(t, oc, "restore", map[string]any{"from": bundle, "as": "demo"})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "FRESH") {
		t.Fatalf("restoring over an existing tenant = %v, want a refusal", err)
	}
	p := plan(t, oc, "restore", map[string]any{"from": bundle, "as": "dev-copy"})
	if !hasStep(p, "fs", "verify the bundle") {
		t.Errorf("a restore that does not verify the bundle first: %v", titles(p))
	}
	if p.Result()["restored_as"] != "dev-copy" {
		t.Errorf("result = %v", p.Result())
	}
	// A reserved name is a validation error, not a refusal to plan.
	if err := planErr(t, oc, "restore", map[string]any{"from": bundle, "as": "admin"}); !errors.Is(err, jobs.ErrValidation) {
		t.Errorf("restoring as a reserved name = %v, want a validation error", err)
	}
}

// ---------------------------------------------------------------- handover, decommission

func TestPlanHandoverNeedsAFencedVerifiedBundleAndADescriptor(t *testing.T) {
	oc, _ := fixture(t, "dev", nil)
	err := planErr(t, oc, "handover", map[string]any{"phase": "execute"})
	if !strings.Contains(err.Error(), "fenced") {
		t.Errorf("without a bundle = %v, want the fenced-backup prerequisite", err)
	}
	oc.Tenant.LastBackup = &registry.BackupRecord{Bundle: "b", Fenced: true, Verified: false}
	if err := planErr(t, oc, "handover", map[string]any{"phase": "execute"}); !strings.Contains(err.Error(), "verified") {
		t.Errorf("with an unverified bundle = %v", err)
	}
	oc.Tenant.LastBackup.Verified = true
	if err := planErr(t, oc, "handover", map[string]any{"phase": "execute"}); !strings.Contains(err.Error(), "rollback_descriptor") {
		t.Errorf("without a descriptor = %v", err)
	}
}

func TestPlanHandoverStopsTheSourceAndWaitsAtTheCutover(t *testing.T) {
	oc, _ := fixture(t, "dev", func(tn *registry.Tenant) {
		tn.LastBackup = &registry.BackupRecord{Bundle: "20260914T093000Z-backup", Fenced: true, Verified: true}
		tn.RollbackDescriptor = &registry.RollbackDescriptor{CapturedAt: "2026-09-14T00:00:00Z", GatewayGeneration: 7}
		tn.API.Bind = "127.0.0.1" // the managed unit binds loopback; see render.UnitConfig
		tn.Stores.Qdrant.Ownership = registry.OwnershipExclusive
		tn.Stores.Qdrant.Capabilities.Stop = true
		tn.Stores.Elasticsearch.Ownership = registry.OwnershipExclusive
		tn.Stores.Elasticsearch.Capabilities.Stop = true
	})
	p := plan(t, oc, "handover", map[string]any{"phase": "execute"})
	if !hasStep(p, "proc", "stop the source processes") || !hasStep(p, "probe", "verify the source is gone") {
		t.Fatalf("a handover that does not prove the source is gone: %v", titles(p))
	}
	cutovers := 0
	var after []string
	for _, s := range p.Steps {
		if s.Cutover {
			cutovers++
		}
		if cutovers == 1 && s.Plan.Kind == "registry" {
			after = append(after, s.Plan.Title)
		}
	}
	if cutovers != 1 {
		t.Errorf("cutover steps = %d, want exactly one", cutovers)
	}
	if len(after) != 1 || !strings.Contains(after[0], "commit") {
		t.Errorf("the step after the cutover = %v, want the commit", after)
	}
	if string(p.Plan.ConfirmValue) != "dev" {
		t.Errorf("confirm value = %q", p.Plan.ConfirmValue)
	}
}

func TestPlanHandoverCommitAndRollbackAreContinuations(t *testing.T) {
	oc, _ := fixture(t, "dev", nil)
	for _, phase := range []string{"commit", "rollback"} {
		err := planErr(t, oc, "handover", map[string]any{"phase": phase})
		if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "CONTINUATION") {
			t.Errorf("phase %s = %v, want the continuation refusal", phase, err)
		}
	}
}

func TestPlanDecommissionQuarantinesOnlyWhatTheCtlRuns(t *testing.T) {
	oc, _ := fixture(t, "dev", nil)
	err := planErr(t, oc, "decommission", nil)
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "quarantines only what the ctl runs") {
		t.Fatalf("a wilke-owned tenant = %v, want a refusal", err)
	}
	oc, _ = fixture(t, "dev", func(tn *registry.Tenant) {
		managed(tn)
		tn.LastBackup = &registry.BackupRecord{Bundle: "b", Fenced: true, Verified: true}
	})
	p := plan(t, oc, "decommission", nil)
	for _, want := range [][2]string{
		{"fs", "quarantine the data directory"},
		{"fs", "remove the rendered unit files"},
		{"systemd", "daemon-reload"},
		{"registry", "quarantined"},
		{"fs", "RECOVERY.json"},
	} {
		if !hasStep(p, want[0], want[1]) {
			t.Fatalf("no %s step containing %q: %v", want[0], want[1], titles(p))
		}
	}
	// Everything decommission writes is a RECORD — the registry row, the
	// recovery note — and never a change to the tenant's own data, which is
	// renamed and left exactly as it was.
	allowed := map[string]bool{
		oc.Roots.Registry(): true,
		oc.Tenant.DataDir + ".quarantined-<ts>/" + recoveryFile: true,
	}
	for _, s := range p.Steps {
		for _, w := range s.Plan.WouldWrite {
			if !allowed[w.Path] {
				t.Errorf("decommission writes %s — v1 renames, it never deletes or rewrites tenant data", w.Path)
			}
		}
	}
	if p.Result()["state"] != "quarantined" {
		t.Errorf("result = %v", p.Result())
	}
}

// ---------------------------------------------------------------- env + units

func TestPlanEnvSetEditsPublicKeysOnly(t *testing.T) {
	oc, _ := fixture(t, "dev", managed)
	for key, want := range map[string]string{
		"API_KEYS":         "secret-class",
		"QDRANT_URL":       "executable-surface",
		"NOT_A_SETTING":    "not a known ragstack setting",
		"USER_STORE_PATH":  "executable-surface",
		"ADMIN_ROLE_CACHE": "not a known ragstack setting",
	} {
		err := planErr(t, oc, "env-set", map[string]any{"key": key, "value": "x"})
		if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), want) {
			t.Errorf("env-set %s = %v, want a refusal mentioning %q", key, err, want)
		}
	}
	p := plan(t, oc, "env-set", map[string]any{"key": "LOG_LEVEL", "value": "DEBUG"})
	if len(p.Steps) != 1 || p.Steps[0].Plan.Kind != "envfile" {
		t.Fatalf("steps = %v", titles(p))
	}
	preview := string(p.Steps[0].Plan.WouldWrite[0].Preview)
	if !strings.Contains(preview, "LOG_LEVEL=DEBUG") {
		t.Errorf("the preview does not show the edit:\n%s", preview)
	}
	if p.Result()["pending_until_restart"] != true {
		t.Errorf("an env edit is pending until the API restarts; result = %v", p.Result())
	}
}

// The canary: a plan is shown, logged, hashed and audited, so no secret may
// survive anywhere in it — not in a preview, not in an argument, not in a
// title.
func TestNoPlanEverCarriesASecretValue(t *testing.T) {
	oc, _ := fixture(t, "dev", managed)
	cases := []struct {
		verb string
		args map[string]any
	}{
		{"env-normalize", nil},
		{"env-set", map[string]any{"key": "LOG_LEVEL", "value": "DEBUG"}},
		{"key-mint", map[string]any{"label": "ops", "role": "admin"}},
		{"backup", map[string]any{"fence": true}},
		{"admin-add", map[string]any{"subject": "bvbrc:bob"}},
	}
	for _, c := range cases {
		p := plan(t, oc, c.verb, c.args)
		body, err := json.Marshal(p.Plan)
		if err != nil {
			t.Fatal(err)
		}
		if strings.Contains(string(body), testSecret) {
			t.Errorf("%s: the plan carries a secret value", c.verb)
		}
		// (encoding/json escapes the angle brackets of settings.Redacted)
		if c.verb == "env-set" && !strings.Contains(string(body), "REDACTED") {
			// The fixture's ALLOWED_ORIGINS carries the canary inside a PUBLIC
			// key's value, where only the seeded redactor can find it. A
			// preview that shows the file unredacted would show it.
			t.Errorf("a preview that keeps the file did not go through the redactor: %s", body)
		}
	}
}

func TestPlanEnvNormalizeShowsTheNormalizedFileAndHidesTheSecretsOne(t *testing.T) {
	oc, _ := fixtureEnv(t, "dev", managed, messyEnv())
	p := plan(t, oc, "env-normalize", nil)
	if len(p.Steps) != 1 {
		t.Fatalf("steps = %v", titles(p))
	}
	w := p.Steps[0].Plan.WouldWrite
	if len(w) != 2 {
		t.Fatalf("would_write = %v, want tenant.env and secrets.env", w)
	}
	if !strings.HasSuffix(w[0].Path, "/tenant.env") || w[0].Preview == "" {
		t.Errorf("tenant.env is not previewed: %+v", w[0])
	}
	if !strings.HasSuffix(w[1].Path, "/secrets.env") || w[1].Preview != "" {
		t.Errorf("secrets.env must have a null preview, got %+v", w[1])
	}
	// The inline comment moved and the three ledger keys left tenant.env.
	preview := string(w[0].Preview)
	if strings.Contains(preview, "API_KEYS=") {
		t.Errorf("a secret-class key survived in the public file:\n%s", preview)
	}
	if !strings.Contains(preview, "# raised for the demo\nMAX_TOP_K=50") {
		t.Errorf("the inline comment was not moved onto its own line:\n%s", preview)
	}
}

func TestPlanRenderUnitsWritesNothingUnlessApply(t *testing.T) {
	oc, _ := fixture(t, "dev", managed)
	p := plan(t, oc, "render-units", nil)
	if len(p.Steps) != 1 || p.Steps[0].Plan.Kind != "render" {
		t.Fatalf("steps = %v", titles(p))
	}
	if len(p.Steps[0].Plan.WouldWrite) == 0 {
		t.Error("the rendered units are not shown")
	}
	for _, w := range p.Steps[0].Plan.WouldWrite {
		if !strings.HasPrefix(w.Path, "/rag/config/ctl/units/ragstack-dev") {
			t.Errorf("unit path = %s, want it under the ctl's units dir", w.Path)
		}
		if w.Mode != "0644" {
			t.Errorf("unit mode = %s", w.Mode)
		}
	}
	applied := plan(t, oc, "render-units", map[string]any{"apply": true})
	if !hasStep(applied, "systemd", "daemon-reload") {
		t.Errorf("apply must reload systemd: %v", titles(applied))
	}
	if len(applied.Steps) != len(p.Steps[0].Plan.WouldWrite)+1 {
		t.Errorf("apply steps = %v", titles(applied))
	}
}

func TestPlanRenderUnitsRefusesANonLoopbackBind(t *testing.T) {
	oc, _ := fixture(t, "dev", nil) // the live fixture really does bind 0.0.0.0
	err := planErr(t, oc, "render-units", nil)
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "loopback") {
		t.Fatalf("error = %v, want the bind refusal", err)
	}
}

// ---------------------------------------------------------------- credentials

func TestPlanKeyMintNeverPreviewsTheSecretsFile(t *testing.T) {
	oc, _ := fixture(t, "dev", managed)
	p := plan(t, oc, "key-mint", map[string]any{"label": "ops", "role": "admin"})
	w := p.Steps[0].Plan.WouldWrite
	if len(w) != 1 || w[0].Preview != "" {
		t.Fatalf("would_write = %+v, want one entry with a null preview", w)
	}
	if p.Secrets == nil {
		t.Fatal("a mint with no secrets delivery")
	}
	if got := p.Secrets(); got != nil {
		t.Errorf("the value exists before the job ran: %v", got)
	}
	if p.Result()["effective"] != false {
		t.Errorf("a mint without restart is not effective yet; result = %v", p.Result())
	}
}

func TestPlanKeyRevokeRefusesTheLastAdminAndAnUnknownID(t *testing.T) {
	oc, _ := fixture(t, "dev", func(tn *registry.Tenant) {
		managed(tn)
		tn.Keys = []registry.Key{{ID: "ops", Role: "admin", Fingerprint: fingerprint(testSecret), Effective: true}}
	})
	if err := planErr(t, oc, "key-revoke", map[string]any{"id": "nope"}); !strings.Contains(err.Error(), "ledger") {
		t.Errorf("an unknown id = %v", err)
	}
	if err := planErr(t, oc, "key-revoke", map[string]any{"id": "ops"}); !strings.Contains(err.Error(), "last effective admin") {
		t.Errorf("the last admin key = %v", err)
	}
}

func TestPlanAdminEditsFollowTheIdentityProvider(t *testing.T) {
	oc, _ := fixture(t, "dev", managed) // the fixture's dev is identity_provider bvbrc
	if err := planErr(t, oc, "admin-add", map[string]any{"subject": "oidc:bob"}); !strings.Contains(err.Error(), "issuer") {
		t.Errorf("a foreign issuer = %v", err)
	}
	p := plan(t, oc, "admin-add", map[string]any{"subject": "bvbrc:bob"})
	preview := string(p.Steps[0].Plan.WouldWrite[0].Preview)
	if !strings.Contains(preview, "bvbrc:bob") || !strings.Contains(preview, "bvbrc:alice") {
		t.Errorf("the preview does not show the new subject list:\n%s", preview)
	}
	oc, _ = fixture(t, "demo", func(tn *registry.Tenant) { managed(tn); tn.Identity.Provider = "none" })
	if err := planErr(t, oc, "admin-add", map[string]any{"subject": "bvbrc:bob"}); !strings.Contains(err.Error(), "API keys only") {
		t.Errorf("a tenant with no provider = %v", err)
	}
}

func TestPlanSADisableRefusesWhileAKeyCarriesTheSubject(t *testing.T) {
	oc, _ := fixture(t, "dev", func(tn *registry.Tenant) {
		managed(tn)
		tn.ServiceAccounts = []registry.ServiceAccount{{Subject: "gowe", Role: "user", Status: "active"}}
		tn.Keys = []registry.Key{{ID: "gowe-key", Role: "user", TenantString: "gowe", Effective: true}}
	})
	err := planErr(t, oc, "sa-disable", map[string]any{"subject": "gowe"})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "revoke the key first") {
		t.Fatalf("error = %v", err)
	}
}

// ---------------------------------------------------------------- the rest

func TestPlanUpdateCodeIsRefusedUntilV11(t *testing.T) {
	oc, _ := fixture(t, "dev", managed)
	err := planErr(t, oc, "update-code", map[string]any{"artifact_id": "v1.5.3"})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "v1.1") {
		t.Fatalf("error = %v, want the v1.1 refusal the contract promises", err)
	}
}

// createFixture is a fleet with one prepared artifact and no tenant selected:
// `create` is fleet-scoped until it has allocated the tenant it is making.
func createFixture(t *testing.T) (jobs.Context, *drivers.Fake) {
	t.Helper()
	oc, fake := fixture(t, "dev", nil)
	oc.Fleet.Artifacts["v1.5.3"] = &registry.Artifact{
		SHA: strings.Repeat("ab", 20), Tag: "v1.5.3",
		Worktree: "/rag/data/ctl/artifacts/v1.5.3/worktree", UIDist: "/rag/data/ctl/artifacts/v1.5.3/worktree/frontend/dist",
		PythonEnv: "/rag/envs/ragstack", PreparedAt: "2026-09-14T09:00:00Z", PreparedBy: "local:3581",
		SchemaCompatible: true,
	}
	oc.Tenant = nil
	return oc, fake
}

func TestPlanCreateRendersTheWholeTenant(t *testing.T) {
	oc, _ := createFixture(t)
	p := plan(t, oc, "create", map[string]any{"name": "sandbox", "artifact_id": "v1.5.3"})
	if p.Steps[0].Plan.Kind != "registry" || !strings.Contains(p.Steps[0].Plan.Title, "index 4") {
		t.Fatalf("the first step is not the allocation: %v", titles(p))
	}
	if string(p.Plan.Tenant) != "sandbox" {
		t.Errorf("plan tenant = %q", p.Plan.Tenant)
	}
	// Every driver the create touches has a step, in order.
	for _, want := range [][2]string{
		{"registry", "allocate sandbox"},
		{"fs", "create the tenant directories"},
		{"fs", "write secrets.env"},
		{"envfile", "write tenant.env"},
		{"envfile", "write provision.env"},
		{"git", "check the worktree out"},
		{"apptainer", "build the tenant UI"},
		{"systemd", "systemctl --user link ragstack-sandbox-api.service"},
		{"systemd", "systemctl --user daemon-reload"},
		{"systemd", "enable ragstack-sandbox.target"},
		{"systemd", "start ragstack-sandbox.target"},
		{"probe", "wait for sandbox's stores and API"},
		{"nginx", "publish the gateway generation"},
		{"registry", "record sandbox as active"},
	} {
		if !hasStep(p, want[0], want[1]) {
			t.Errorf("no %s step %q in:\n  %s", want[0], want[1], strings.Join(titles(p), "\n  "))
		}
	}
	if a, b := stepIndex(p, "registry", "allocate sandbox"), stepIndex(p, "fs", "write secrets.env"); a > b {
		t.Errorf("the allocation must precede the secrets write (%d > %d)", a, b)
	}

	// The whole tenant is visible in the dry run: its env file and its units.
	var env, unit bool
	for _, s := range p.Steps {
		for _, w := range s.Plan.WouldWrite {
			if strings.HasSuffix(w.Path, "/config/tenant.env") {
				env = true
				// The key ledger is NOT in tenant.env at all any more: the
				// managed layout puts every secret-class line in secrets.env,
				// so the preview an operator approves is the file that will be
				// written, whole.
				if strings.Contains(string(w.Preview), "API_KEYS") {
					t.Errorf("tenant.env preview carries a key line:\n%s", w.Preview)
				}
				if !strings.Contains(string(w.Preview), "USER_STORE_PATH=/rag/data/tenants/sandbox/state") {
					t.Errorf("tenant.env preview is not this tenant's file:\n%s", w.Preview)
				}
			}
			if strings.HasSuffix(w.Path, "/config/secrets.env") && string(w.Preview) != "" {
				t.Errorf("secrets.env must have NO preview, got %q", w.Preview)
			}
			if strings.HasSuffix(w.Path, "-api.service") {
				unit = true
				if !strings.Contains(string(w.Preview), "--host 127.0.0.1") {
					t.Errorf("a created tenant's api unit must bind loopback:\n%s", w.Preview)
				}
			}
		}
	}
	if !env || !unit {
		t.Errorf("the plan shows env=%v unit=%v; a dry run has to show both", env, unit)
	}
}

// A plan is computed twice and must be identical both times: the engine hashes
// it under the locks and refuses the job when it moved. `create` is the plan
// with the most moving parts (an allocation, three rendered files, five units),
// so it is the one worth asserting directly.
func TestPlanCreateIsPure(t *testing.T) {
	oc, _ := createFixture(t)
	args := map[string]any{
		"name": "sandbox", "artifact_id": "v1.5.3", "postgres": "local",
		"keys": []any{map[string]any{"label": "ops", "role": "admin"}},
	}
	a := plan(t, oc, "create", args)
	b := plan(t, oc, "create", args)
	if a.Plan.PlanHash == "" {
		t.Fatal("the plan carries no hash")
	}
	if a.Plan.PlanHash != b.Plan.PlanHash {
		t.Errorf("two plans of the same request differ:\n  %s\n  %s", a.Plan.PlanHash, b.Plan.PlanHash)
	}
}

func TestPlanCreateWithPostgresLocalAddsTheUnitAndTheSecrets(t *testing.T) {
	oc, _ := createFixture(t)
	p := plan(t, oc, "create", map[string]any{"name": "sandbox", "artifact_id": "v1.5.3", "postgres": "local"})
	var unit string
	for _, s := range p.Steps {
		for _, w := range s.Plan.WouldWrite {
			if strings.HasSuffix(w.Path, "-postgres.service") {
				unit = string(w.Preview)
			}
			if strings.HasSuffix(w.Path, "/config/provision.env") {
				if !strings.Contains(string(w.Preview), "TENANT_STORE_KIND=postgres-local") {
					t.Errorf("provision.env does not record the store kind:\n%s", w.Preview)
				}
				if !strings.Contains(string(w.Preview), "TENANT_PG_PORT=24085") {
					t.Errorf("provision.env does not record the +5 port:\n%s", w.Preview)
				}
			}
		}
	}
	if unit == "" {
		t.Fatalf("no postgres unit in:\n  %s", strings.Join(titles(p), "\n  "))
	}
	if strings.Contains(unit, "POSTGRES_PASSWORD") || strings.Contains(unit, "TENANT_PG_PASSWORD") {
		t.Errorf("the unit names the password; it must not appear in the unit file or on its argv at all:\n%s", unit)
	}
	// The password reaches the unit through secrets.env alone, and never
	// through the command line: systemd expands ${…} in ExecStart into the
	// argv, and /proc/<pid>/cmdline is 0444 to every account on this host. The
	// unit therefore names no password at all (asserted just above); what it
	// has is the EnvironmentFile, which carries
	// APPTAINERENV_POSTGRES_PASSWORD for apptainer to forward into the
	// container.
	for _, want := range []string{
		"EnvironmentFile=/rag/data/tenants/sandbox/config/secrets.env",
		"-c port=24085 -c listen_addresses=127.0.0.1",
		"TimeoutStopSec=90",
	} {
		if !strings.Contains(unit, want) {
			t.Errorf("the postgres unit lacks %q:\n%s", want, unit)
		}
	}
	// The api unit orders itself after it, and the target wants it.
	for _, s := range p.Steps {
		for _, w := range s.Plan.WouldWrite {
			if strings.HasSuffix(w.Path, "-api.service") && !strings.Contains(string(w.Preview),
				"Requires=ragstack-sandbox-qdrant.service ragstack-sandbox-es.service ragstack-sandbox-postgres.service") {
				t.Errorf("the api unit does not require the postgres unit:\n%s", w.Preview)
			}
			if strings.HasSuffix(w.Path, "sandbox.target") && !strings.Contains(string(w.Preview),
				"ragstack-sandbox-postgres.service") {
				t.Errorf("the target does not want the postgres unit:\n%s", w.Preview)
			}
		}
	}
}

func TestPlanCreateWithoutStartSkipsTheStartAndTheServiceAccounts(t *testing.T) {
	oc, _ := createFixture(t)
	p := plan(t, oc, "create", map[string]any{
		"name": "sandbox", "artifact_id": "v1.5.3", "start": false, "gateway": false,
		"service_accounts": []any{map[string]any{"subject": "gowe", "role": "user", "purpose": "workflows"}},
	})
	if hasStep(p, "systemd", "start ragstack-sandbox.target") {
		t.Error("start:false still started the target")
	}
	if !hasStep(p, "systemd", "skip the start") {
		t.Error("start:false did not say it was skipping the start")
	}
	if !hasStep(p, "tenantapi", "skip the service accounts") {
		t.Errorf("a service account was planned against an API that is not running:\n  %s",
			strings.Join(titles(p), "\n  "))
	}
	if !hasStep(p, "nginx", "skip the gateway publish") {
		t.Error("gateway:false did not say it was skipping the publish")
	}
	if !hasStep(p, "registry", "record sandbox as provisioned") {
		t.Errorf("a tenant that was not started must not be recorded active:\n  %s",
			strings.Join(titles(p), "\n  "))
	}
}

func TestPlanCreateRefusesAnUnpreparedArtifactAndAMismatchedIssuer(t *testing.T) {
	oc, _ := fixture(t, "dev", nil)
	oc.Tenant = nil
	if err := planErr(t, oc, "create", map[string]any{"name": "sandbox", "artifact_id": "nope"}); !strings.Contains(err.Error(), "not prepared") {
		t.Errorf("an unknown artifact = %v", err)
	}
	oc.Fleet.Artifacts["v1"] = &registry.Artifact{SHA: strings.Repeat("ab", 20), Tag: "v1", PythonEnv: "/rag/envs/ragstack"}
	err := planErr(t, oc, "create", map[string]any{
		"name": "sandbox", "artifact_id": "v1", "identity_provider": "none", "admin_subjects": []any{"bvbrc:alice"},
	})
	if !strings.Contains(err.Error(), "no identity provider") {
		t.Errorf("subjects without a provider = %v", err)
	}
}

func TestGatewayVerbsAreFleetScoped(t *testing.T) {
	oc, _ := fixture(t, "dev", nil)
	oc.Tenant = nil
	for verb, want := range map[string]string{
		"gateway-apply":  "publish the next gateway generation",
		"gateway-reload": "reload the published generation",
	} {
		p := plan(t, oc, verb, nil)
		if len(p.Steps) != 1 || !strings.Contains(p.Steps[0].Plan.Title, want) {
			t.Errorf("%s steps = %v", verb, titles(p))
		}
		if string(p.Plan.Tenant) != "" {
			t.Errorf("%s names a tenant: %q", verb, p.Plan.Tenant)
		}
		if len(p.Locks) != 1 || p.Locks[0] != model.LockGateway {
			t.Errorf("%s locks = %v", verb, p.Locks)
		}
	}
}

// ---------------------------------------------------------------- hash, validate

func TestPlanHashIsStableAcrossPlansAndMovesWithTheRegistry(t *testing.T) {
	oc, _ := fixture(t, "dev", managed)
	a := plan(t, oc, "stop", nil)
	b := plan(t, oc, "stop", nil)
	if a.Plan.PlanHash != b.Plan.PlanHash {
		t.Fatalf("two plans of one registry hash differently:\n%s\n%s", a.Plan.PlanHash, b.Plan.PlanHash)
	}
	if !strings.HasPrefix(a.Plan.PlanHash, "sha256:") || len(a.Plan.PlanHash) != 71 {
		t.Errorf("plan_hash = %q", a.Plan.PlanHash)
	}
	oc.Fleet.Generation++
	if c := plan(t, oc, "stop", nil); c.Plan.PlanHash == a.Plan.PlanHash {
		t.Error("the hash did not move when the registry generation did — plan_stale could never fire")
	}
	oc.Fleet.Generation--
	if d := plan(t, oc, "stop", map[string]any{"keep_enabled": true}); d.Plan.PlanHash == a.Plan.PlanHash {
		t.Error("the hash did not move when the arguments did")
	}
}

func TestValidateEnforcesTheArgsSchema(t *testing.T) {
	r := NewRegistry(Deps{})
	cases := []struct {
		verb string
		args map[string]any
		want string
	}{
		{"start", map[string]any{"onyl": []any{"api"}}, "unknown argument onyl"},
		{"start", map[string]any{"only": []any{"api", "api"}}, "duplicate"},
		{"start", map[string]any{"only": []any{"redis"}}, "is not one of api, ui, qdrant, es"},
		{"start", map[string]any{"force": "yes"}, "must be a boolean"},
		{"restore", map[string]any{"as": "x"}, "missing required argument from"},
		{"restore", map[string]any{"from": "yesterday", "as": "x"}, "does not match"},
		{"handover", map[string]any{"phase": "later"}, "is not one of execute, commit, rollback"},
		{"key-mint", map[string]any{"label": "Ops", "role": "admin"}, "does not match"},
		{"sa-create", map[string]any{"subject": "gowe", "role": "user", "purpose": strings.Repeat("x", 257)}, "over the 256-byte limit"},
		{"env-set", map[string]any{"key": "lowercase", "value": "x"}, "does not match"},
		{"decommission", map[string]any{"force": true}, "unknown argument force"},
	}
	for _, c := range cases {
		op, _ := r.Lookup(c.verb)
		err := op.Validate(c.args)
		if err == nil {
			t.Errorf("%s %v: expected a validation error", c.verb, c.args)
			continue
		}
		if !errors.Is(err, jobs.ErrValidation) {
			t.Errorf("%s: %v is not a jobs.ErrValidation", c.verb, err)
		}
		if !strings.Contains(err.Error(), c.want) {
			t.Errorf("%s: %v, want it to mention %q", c.verb, err, c.want)
		}
	}
	// And the happy path: every verb accepts its own minimal arguments.
	for verb, args := range map[string]map[string]any{
		"start": nil, "stop": {"force": true}, "backup": {"fence": true},
		"restore":  {"from": "20260914T093000Z-backup", "as": "copy"},
		"handover": {"phase": "execute"}, "decommission": {}, "env-normalize": nil,
		"key-mint": {"label": "ops", "role": "user"}, "admin-add": {"subject": "bvbrc:alice"},
		"sa-create": {"subject": "gowe", "role": "user"}, "render-units": {"apply": true},
		"update-code": {"artifact_id": "v1.5.3"},
	} {
		op, _ := r.Lookup(verb)
		if err := op.Validate(args); err != nil {
			t.Errorf("%s %v: %v", verb, args, err)
		}
	}
}

func TestDestructiveVerbsAreExactlyThePlansList(t *testing.T) {
	want := map[string]bool{
		"stop": true, "restart": true, "restore": true, "handover": true, "migrate-local": true,
		"decommission": true, "key-revoke": true, "admin-remove": true, "sa-disable": true,
		"env-unset": true, "update-code": true,
	}
	r := NewRegistry(Deps{})
	for _, verb := range r.Verbs() {
		op, _ := r.Lookup(verb)
		if op.Destructive() != want[verb] {
			t.Errorf("%s: Destructive() = %v, want %v", verb, op.Destructive(), want[verb])
		}
	}
}
