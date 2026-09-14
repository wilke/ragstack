package main

// The selftest is the one command whose output is a verdict on a host, so the
// thing worth testing is the SEQUENCE and the REFUSALS: that it creates on a
// sandbox block and nowhere else, that every op it submits names its own
// tenant, that the post-conditions are asked of the host the jobs ran against,
// and — above all — that the one deletion in the whole control plane refuses
// every path it was not built to remove.
//
// It runs against `api.BuildEngineAndDrivers` with FakeDrivers: the same
// engine, the same ops, the same plans and the same locks as a real run, over
// an in-memory host. What that cannot prove is what Elasticsearch does when it
// is stopped; that is what running the command on coconut is for.

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/api"
	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/hostfacts"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/render"
)

// selftestClock is the pinned clock. It makes the sandbox names deterministic,
// which is what lets the fixture host be told about their units before they
// exist (see fixtureBindPorts).
var selftestClock = time.Date(2026, 9, 14, 12, 30, 0, 0, time.UTC)

const (
	fixtureStamp    = "20260914t123000z"
	fixturePrimary  = "ctltest-" + fixtureStamp
	fixtureRestored = fixturePrimary + "-r"
	fixtureArtifact = "selftest-0123456789ab"
)

// newFixtureSelftest builds a selftest over a scratch /rag and the in-memory
// host, ready to run.
func newFixtureSelftest(t *testing.T) (*selftest, *drivers.Fake, *bytes.Buffer) {
	t.Helper()
	root := t.TempDir()
	roots := paths.NewRoots(root, paths.Overrides{})
	for _, d := range []string{roots.DataDir, roots.ReposDir, roots.BackupsDir, roots.UnitsDir(),
		roots.CtlStateDir, roots.ImagesDir, filepath.Join(root, "documents")} {
		if err := os.MkdirAll(d, 0o770); err != nil {
			t.Fatal(err)
		}
	}
	fixture := filepath.Join(root, "documents", "test_api.md")
	if err := os.WriteFile(fixture, []byte("# test\n"), 0o640); err != nil {
		t.Fatal(err)
	}

	// A fleet with one prepared artifact and no tenants: `create-sandbox` needs
	// an artifact and nothing else, and an empty fleet makes the assertions
	// about what the run touched unambiguous.
	f := registry.NewFleet(root)
	f.Artifacts[fixtureArtifact] = &registry.Artifact{
		SHA: strings.Repeat("ab", 20), Tag: "selftest",
		Worktree:   filepath.Join(roots.CtlStateDir, "artifacts", fixtureArtifact, "worktree"),
		UIDist:     filepath.Join(roots.CtlStateDir, "artifacts", fixtureArtifact, "worktree", "frontend", "dist"),
		PythonEnv:  filepath.Join(root, "envs", "ragstack"),
		PreparedAt: "2026-09-14T00:00:00Z", PreparedBy: "local:0", SchemaCompatible: true,
	}
	regPath := roots.Registry()
	if err := registry.Save(regPath, f, "test"); err != nil {
		t.Fatal(err)
	}

	now := func() time.Time { return selftestClock }
	eng, drv, err := api.BuildEngineAndDrivers(api.EngineConfig{
		Roots: roots, RegistryPath: regPath,
		StorePath:   filepath.Join(roots.CtlStateDir, "jobs.db"),
		Mode:        model.WorkerDirect,
		Host:        "test",
		FakeDrivers: true,
		Mirror:      filepath.Join(root, "repos", "ragstack.git"),
		MountPoint:  "/rag",
		Now:         now,
		// A GREEN doctor, injected. BuildEngine's default doctor reads THIS
		// host's facts — real listeners, real /proc, real mounts — which is
		// right for a daemon and meaningless for a scratch tree under
		// t.TempDir(): every op would be gated on findings about the machine
		// running `go test`. What the doctor gate itself does (red never runs,
		// yellow needs its hash quoted) is tested in the jobs package.
		Doctor: func(context.Context, string, string) (model.DoctorResponse, error) {
			return model.DoctorResponse{
				Status: model.StatusGreen, Hash: "sha256:" + strings.Repeat("0", 64),
				GeneratedAt: selftestClock.Format(time.RFC3339), Findings: []model.Finding{},
			}, nil
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	fake, ok := drv.(*drivers.Fake)
	if !ok {
		t.Fatalf("FakeDrivers gave a %T", drv)
	}
	fixtureBindPorts(fake, roots, fixturePrimary, fixtureRestored)

	out := &bytes.Buffer{}
	s := &selftest{
		opts:         selftestOptions{ragRoot: root, fixture: fixture, artifact: fixtureArtifact},
		roots:        roots,
		registryPath: regPath,
		eng:          eng,
		drv:          drv,
		princ:        jobs.Principal{Subject: "local:0", Role: "operator", Method: model.AuthLocal, RequestID: "req-selftest"},
		host:         &hostfacts.Fake{Self: "wilke"},
		tenantAPI:    drv.TenantAPI(),
		runArgv:      func(context.Context, string, ...string) ([]byte, error) { return nil, errors.New("not on this host") },
		now:          now,
		out:          out,
	}
	return s, fake, out
}

// fixtureBindPorts tells the in-memory manager which port each sandbox's units
// own. The fixture driver set is built from the tenants that EXIST, and these
// two do not exist yet — without this, starting the target binds nothing and
// the create's readiness gate waits for an API that was never going to answer.
func fixtureBindPorts(fake *drivers.Fake, roots paths.Roots, names ...string) {
	// The blocks create-sandbox will allocate, in order.
	for i, name := range names {
		block := paths.BlockAt(paths.SelftestBase+i*paths.PortStride, 0, 0)
		target, _, _, _, apiUnit, _ := render.UnitNames(name)
		fake.FakeSystemd().BindUnitPort(target, block.API)
		fake.FakeSystemd().BindUnitPort(apiUnit, block.API)
	}
	_ = roots
}

// The whole sequence, end to end. Every step is a job through the engine, and
// the run ends with a host on which nothing of the sandbox is left.
func TestSelftestRunsTheWholeSequenceAgainstTheFixtureHost(t *testing.T) {
	s, fake, out := newFixtureSelftest(t)
	err := s.execute(context.Background())
	s.report()

	// `restore --as` is the other half of PR-D. Until it merges, its steps
	// refuse with "lands in PR-D" and the run says so rather than failing: the
	// create, the ingest, the backup, the stop and the quarantine are all
	// exercised either way.
	restorePending := false
	for _, st := range s.steps {
		if st.Name == "restore --as" && st.Skipped {
			restorePending = true
		}
	}
	if err != nil {
		if isLandsInPRD(err) {
			t.Fatalf("the restore refusal was not treated as a skip: %v", err)
		}
		t.Fatalf("selftest.execute: %v\n%s", err, out.String())
	}

	want := []string{"create-sandbox", "ingest", "backup --fence", "stop", "restore --as",
		"decommission " + fixturePrimary}
	if !restorePending {
		want = append(want, "decommission "+fixtureRestored)
	}
	var got []string
	for _, st := range s.steps {
		got = append(got, st.Name)
	}
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Fatalf("steps =\n  %v\nwant\n  %v", got, want)
	}
	for _, st := range s.steps {
		switch {
		case st.Skipped:
			continue
		case st.Name == "ingest":
			if st.State != "succeeded" {
				t.Errorf("ingest ended %q", st.State)
			}
		case st.State != string(model.JobSucceeded):
			t.Errorf("step %s ended %q: %v", st.Name, st.State, st.Err)
		}
	}
	if s.failedChecks() > 0 {
		t.Errorf("%d check(s) failed:\n%s", s.failedChecks(), out.String())
	}

	// The tenant it created was on a SANDBOX block, and the production
	// allocator never moved.
	f, err := loadForRead(s.registryPath)
	if err != nil {
		t.Fatal(err)
	}
	if _, still := f.Tenants[fixturePrimary]; still {
		t.Errorf("the sweep left %s in the registry", fixturePrimary)
	}
	if idx, base := registry.Allocate(f); idx != 0 || base != f.PortBase {
		t.Errorf("the production allocator moved to index %d (port %d): a selftest must leave it where it found it",
			idx, base)
	}
	if len(f.Tombstones) != 0 {
		t.Errorf("a sandbox wrote %d tombstone(s)", len(f.Tombstones))
	}

	// The ingest reached the sandbox's own API and carried a real path.
	ing := fake.FakeTenantAPI().Ingests
	if len(ing) != 1 || !strings.Contains(ing[0], "test_api.md") {
		t.Errorf("ingests = %v", ing)
	}
	if !strings.Contains(ing[0], fmt.Sprintf(":%d ", paths.SelftestBase)) {
		t.Errorf("the ingest did not go to the sandbox's API port: %v", ing)
	}
}

// Every op the selftest submits names one of its own tenants. The check is in
// front of the submit rather than after it, so a bug that reached it cannot
// have already run.
func TestSelftestRefusesToSubmitAgainstATenantItDidNotCreate(t *testing.T) {
	s, fake, _ := newFixtureSelftest(t)
	before := len(fake.Calls())
	_, err := s.submit(context.Background(), "stop asm-next", "stop", "asm-next", map[string]any{}, "asm-next")
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("submit against a production tenant = %v, want a refusal", err)
	}
	if !strings.Contains(err.Error(), "sandbox") {
		t.Errorf("the refusal does not say why: %v", err)
	}
	if len(fake.Calls()) != before {
		t.Errorf("the refusal came AFTER %d driver call(s)", len(fake.Calls())-before)
	}
}

// A `ctltest-` name must not reach a production block, and a production name
// must not reach a sandbox one. Both directions, because either mistake ends
// with a tenant whose ports say one thing and whose name says another.
func TestTheTwoCreateVerbsRefuseEachOthersNames(t *testing.T) {
	s, _, _ := newFixtureSelftest(t)
	ctx := context.Background()

	_, _, err := s.eng.Submit(ctx, jobs.Request{
		Op: "create", Tenant: fixturePrimary, DryRun: true,
		Args:      map[string]any{"name": fixturePrimary, "artifact_id": fixtureArtifact},
		Principal: s.princ, Mode: model.WorkerDirect,
	})
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("`create` of a ctltest- name = %v, want a refusal", err)
	}

	_, _, err = s.eng.Submit(ctx, jobs.Request{
		Op: "create-sandbox", Tenant: "prod-thing", DryRun: true,
		Args:      map[string]any{"name": "prod-thing", "artifact_id": fixtureArtifact},
		Principal: s.princ, Mode: model.WorkerDirect,
	})
	// The args grammar refuses it first, which is the stronger answer: the name
	// never reaches a plan at all.
	if !errors.Is(err, jobs.ErrValidation) && !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("`create-sandbox` of a production name = %v, want a refusal", err)
	}
}

// A sandbox create lands in the selftest range, and the plan says so.
func TestCreateSandboxAllocatesOutOfTheSelftestRange(t *testing.T) {
	s, _, _ := newFixtureSelftest(t)
	plan, _, err := s.eng.Submit(context.Background(), jobs.Request{
		Op: "create-sandbox", Tenant: fixturePrimary, DryRun: true,
		Args:      map[string]any{"name": fixturePrimary, "artifact_id": fixtureArtifact},
		Principal: s.princ, Mode: model.WorkerDirect,
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Steps) == 0 {
		t.Fatal("the plan has no steps")
	}
	if !strings.Contains(plan.Steps[0].Title, fmt.Sprint(paths.SelftestBase)) {
		t.Errorf("the allocation step does not name the sandbox base: %q", plan.Steps[0].Title)
	}
}

// ---------------------------------------------------------------- the sweep

// The sweep is the ONLY deletion in v1. Everything it refuses is more
// important than everything it removes.
func TestSweepRefusesEveryPathItWasNotBuiltToRemove(t *testing.T) {
	root := t.TempDir()
	mk := func(name string) string {
		p := filepath.Join(root, name)
		if err := os.MkdirAll(p, 0o770); err != nil {
			t.Fatal(err)
		}
		return p
	}
	good := "ctltest-20260914t123000z.quarantined-20260914t124500z"
	mk(good)
	if _, err := sweepPath(root, good); err != nil {
		t.Fatalf("sweepPath refused a real quarantined sandbox: %v", err)
	}

	refused := []string{
		"dev.quarantined-20260914t124500z",         // a production tenant's quarantine
		"asm",                                      // a live tenant
		"ctltest-20260914t123000z",                 // a sandbox that is NOT quarantined
		"ctltest-20260914t123000z.quarantined",     // no timestamp: not the shape decommission writes
		"ctltest.quarantined-20260914t124500z",     // no stamp after the prefix
		"..",                                       // the parent directory
		"../tenants",                               // an escape
		"ctltest-x.quarantined-y/../../etc",        // an escape wearing the right name
		"CTLTEST-20260914t1.quarantined-20260914t", // the prefix is lowercase, like the names
	}
	for _, name := range refused {
		if p, err := sweepPath(root, name); err == nil {
			t.Errorf("sweepPath(%q) returned %q; it must refuse everything but a quarantined sandbox tree", name, p)
		}
	}

	// A symlink wearing a sweepable name, pointing at somebody else's tree.
	victim := mk("a-production-tenant")
	link := filepath.Join(root, "ctltest-20260101t000000z.quarantined-20260101t000001z")
	if err := os.Symlink(victim, link); err != nil {
		t.Fatal(err)
	}
	if _, err := sweepPath(root, filepath.Base(link)); err == nil {
		t.Error("sweepPath followed a symlink out of the sandbox and would have removed a production tree")
	}
	if _, err := os.Stat(victim); err != nil {
		t.Errorf("the victim tree is gone: %v", err)
	}
}

// The row half of the sweep: sandbox rows go, and a row that is named like a
// sandbox but sits on a production block is REFUSED rather than deleted —
// deleting it would destroy the evidence of how it came to exist.
func TestSweepDeletesOnlySandboxRows(t *testing.T) {
	s, _, _ := newFixtureSelftest(t)
	f, err := loadForRead(s.registryPath)
	if err != nil {
		t.Fatal(err)
	}
	sandbox := registry.NewTenant(fixturePrimary, fixturePrimary)
	sandbox.Ports = paths.BlockAt(paths.SelftestBase, 0, 0)
	sandbox.Ports.Index = registry.SandboxIndexBase
	prod := registry.NewTenant("dev", "dev")
	prod.Ports = paths.Block(0)
	for _, t2 := range []*registry.Tenant{sandbox, prod} {
		fillFixtureRow(s.roots, t2, "quarantined")
		f.Tenants[t2.Name] = t2
		f.DisplayOrder = append(f.DisplayOrder, t2.Name)
	}
	if err := registry.Save(s.registryPath, f, "test"); err != nil {
		t.Fatal(err)
	}

	removed, refused, err := s.sweep(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(refused) != 0 {
		t.Errorf("unexpected refusals: %v", refused)
	}
	if strings.Join(removed, " ") != "registry row "+fixturePrimary {
		t.Errorf("removed = %v, want only the sandbox row", removed)
	}
	after, err := loadForRead(s.registryPath)
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := after.Tenants["dev"]; !ok {
		t.Fatal("the sweep deleted a production tenant's row")
	}
	if _, ok := after.Tenants[fixturePrimary]; ok {
		t.Error("the sandbox row survived the sweep")
	}

	// And a row wearing the sandbox NAME on a production BLOCK is refused.
	impostor := registry.NewTenant("ctltest-impostor", "ctltest-impostor")
	impostor.Ports = paths.Block(7)
	fillFixtureRow(s.roots, impostor, "active")
	after.Tenants[impostor.Name] = impostor
	after.DisplayOrder = append(after.DisplayOrder, impostor.Name)
	if err := registry.Save(s.registryPath, after, "test"); err != nil {
		t.Fatal(err)
	}
	_, refused, err = s.sweep(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(refused) != 1 || !strings.Contains(refused[0], "port block") {
		t.Fatalf("refusals = %v, want one naming the port block", refused)
	}
	final, err := loadForRead(s.registryPath)
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := final.Tenants[impostor.Name]; !ok {
		t.Error("a row on a production block was deleted because of its NAME")
	}
}

// ---------------------------------------------------------------- --boot

// The checklist renders each fact as its own named line, and a host missing
// linger is a FAIL rather than a crash.
func TestBootChecklistReadsTheHostFacts(t *testing.T) {
	s, _, _ := newFixtureSelftest(t)
	s.host = &hostfacts.Fake{Self: "wilke"}
	checks := s.bootChecklist(context.Background())
	got := map[string]checkVerdict{}
	for _, c := range checks {
		got[c.Name] = c.Verdict
	}
	if got["linger"] != checkFail {
		t.Errorf("linger on a host without it = %q, want FAIL", got["linger"])
	}
	// The drop-in is a root item on the DAEMON account; reporting a FAIL for an
	// operator's own account would be a finding nobody can act on.
	if got["user@ drop-in"] != checkNA {
		t.Errorf("drop-in as wilke = %q, want n/a", got["user@ drop-in"])
	}

	// With linger, the daemon account and a complete drop-in, the same facts
	// read green.
	s.host = &hostfacts.Fake{
		Self:      ctlServiceUser,
		Lingering: map[string]bool{ctlServiceUser: true},
		DropIns: map[int]hostfacts.DropIn{os.Getuid(): {
			Present: true, SystemdUnitPath: "/rag/config/ctl/units:",
			RequiresMountsFor: []string{"/rag"},
			Files:             []string{fmt.Sprintf("/etc/systemd/system/user@%d.service.d/ragstack.conf", os.Getuid())},
		}},
	}
	got = map[string]checkVerdict{}
	for _, c := range s.bootChecklist(context.Background()) {
		got[c.Name] = c.Verdict
	}
	if got["linger"] != checkPass || got["user@ drop-in"] != checkPass {
		t.Errorf("a host with linger and a drop-in reads %v", got)
	}
}

// A drop-in that exists but does not wait for /rag is the failure this check is
// FOR: the manager starts, finds no /rag, and every unit's condition is false.
func TestBootChecklistFailsADropInThatDoesNotWaitForRag(t *testing.T) {
	s, _, _ := newFixtureSelftest(t)
	s.host = &hostfacts.Fake{
		Self:      ctlServiceUser,
		Lingering: map[string]bool{ctlServiceUser: true},
		DropIns: map[int]hostfacts.DropIn{os.Getuid(): {
			Present: true, SystemdUnitPath: "/rag/config/ctl/units:",
			Files: []string{"/etc/systemd/system/user@10078.service.d/ragstack.conf"},
		}},
	}
	for _, c := range s.bootChecklist(context.Background()) {
		if c.Name != "user@ drop-in" {
			continue
		}
		if c.Verdict != checkFail || !strings.Contains(c.Detail, "RequiresMountsFor") {
			t.Fatalf("drop-in check = %s %q", c.Verdict, c.Detail)
		}
		return
	}
	t.Fatal("no drop-in check was produced")
}

// `--boot` with a FAIL exits 3: the host will not bring its tenants back, and
// that is a refusal to certify rather than a job that failed.
func TestBootOnlyExitsRefusedOnAFail(t *testing.T) {
	s, _, _ := newFixtureSelftest(t)
	s.opts.boot = true
	if rc := s.runBootOnly(context.Background()); rc != exitRefused {
		t.Fatalf("rc %d, want %d", rc, exitRefused)
	}
}

// ---------------------------------------------------------------- refusals

// A selftest pointed at a daemon would create tenants on that daemon's host and
// then look for the evidence on this one.
func TestSelftestRefusesAServer(t *testing.T) {
	rc, _, errs := capture(t, "selftest", "--server", "http://127.0.0.1:23990")
	if rc != exitRefused {
		t.Fatalf("rc %d, want %d (%s)", rc, exitRefused, errs)
	}
	if !strings.Contains(errs, "runs the engine in this process") {
		t.Errorf("the refusal does not say why: %s", errs)
	}
}

// Without a prepared artifact there is nothing to create a tenant from, and the
// refusal has to say what to run instead.
func TestSelftestRefusesWhenNoArtifactIsPrepared(t *testing.T) {
	s, _, _ := newFixtureSelftest(t)
	f, err := loadForRead(s.registryPath)
	if err != nil {
		t.Fatal(err)
	}
	f.Artifacts = map[string]*registry.Artifact{}
	if err := registry.Save(s.registryPath, f, "test"); err != nil {
		t.Fatal(err)
	}
	s.opts.artifact = ""
	_, err = s.pickArtifact()
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("pickArtifact with no artifact = %v, want a refusal", err)
	}
	if !strings.Contains(err.Error(), "fleet artifact prepare") {
		t.Errorf("the refusal does not say what to run: %v", err)
	}
}

// ---------------------------------------------------------------- es checks

// The graceful-stop pair: a log that says the node stopped and a journal
// without a SIGKILL are PASS; a journal WITH one is FAIL; evidence that is not
// there at all is n/a, because a tenant with no Elasticsearch unit has not
// failed to shut one down.
func TestElasticsearchShutdownChecksReadTheEvidence(t *testing.T) {
	s, _, _ := newFixtureSelftest(t)
	tp := paths.TenantPaths(s.roots, fixturePrimary, fixturePrimary)
	if err := os.MkdirAll(tp.LogsDir, 0o770); err != nil {
		t.Fatal(err)
	}
	logPath := filepath.Join(tp.LogsDir, "es-"+fixturePrimary+".log")

	verdicts := func() map[string]checkResult {
		out := map[string]checkResult{}
		for _, c := range s.esShutdownChecks(context.Background(), fixturePrimary) {
			out[c.Name] = c
		}
		return out
	}

	// No log and no journalctl: nothing to read, so nothing is claimed.
	got := verdicts()
	if got["es stopped gracefully"].Verdict != checkNA || got["es journal has no SIGKILL"].Verdict != checkNA {
		t.Errorf("with no evidence the checks claim %v", got)
	}

	if err := os.WriteFile(logPath, []byte("...\n[INFO ][o.e.n.Node] [x] stopped\n[INFO] closed\n"), 0o640); err != nil {
		t.Fatal(err)
	}
	s.runArgv = func(context.Context, string, ...string) ([]byte, error) {
		return []byte("Sep 14 12:30:01 coconut systemd[1]: Stopped ragstack tenant.\n"), nil
	}
	got = verdicts()
	if got["es stopped gracefully"].Verdict != checkPass {
		t.Errorf("a log that says `stopped` = %s (%s)", got["es stopped gracefully"].Verdict,
			got["es stopped gracefully"].Detail)
	}
	if got["es journal has no SIGKILL"].Verdict != checkPass {
		t.Errorf("a clean journal = %s", got["es journal has no SIGKILL"].Verdict)
	}

	// A killed node: the unit "stopped" either way, which is exactly why this
	// is checked separately from the job's own outcome.
	s.runArgv = func(context.Context, string, ...string) ([]byte, error) {
		return []byte("systemd[1]: ragstack-x-es.service: Killing process 1234 with signal SIGKILL.\n"), nil
	}
	if v := verdicts()["es journal has no SIGKILL"]; v.Verdict != checkFail {
		t.Errorf("a SIGKILL in the journal = %s", v.Verdict)
	}
	if err := os.WriteFile(logPath, []byte("[INFO] starting ...\n"), 0o640); err != nil {
		t.Fatal(err)
	}
	if v := verdicts()["es stopped gracefully"]; v.Verdict != checkFail {
		t.Errorf("a log with no shutdown line = %s", v.Verdict)
	}
}

// fillFixtureRow completes a hand-built registry row so registry.Save accepts
// it: the schema requires a code tag, loopback store URLs and a 64-hex env
// hash, and a test that wrote half a row would be failing on the validator
// rather than on what it is about.
func fillFixtureRow(roots paths.Roots, t *registry.Tenant, state string) {
	tp := paths.TenantPaths(roots, t.Name, t.ManifestName)
	t.DataDir, t.Worktree, t.PythonEnv = tp.DataDir, tp.Worktree, "/rag/envs/ragstack"
	t.Code = registry.Code{Tag: "selftest"}
	t.API = registry.API{Bind: "127.0.0.1", PidFile: tp.PidFile, Log: tp.APILog}
	t.UI = registry.UI{Mode: registry.UIModeStatic, Base: "/ragstack/" + t.Name + "/ui/"}
	t.Supervisor, t.Owner, t.State = "systemd", "svcbvbrc", state
	t.DesiredBoot, t.EnvLayout = "disabled", "managed"
	t.EnvFileSHA256 = strings.Repeat("0", 64)
	t.Stores.Qdrant.URL = fmt.Sprintf("http://127.0.0.1:%d", t.Ports.QdrantHTTP)
	t.Stores.Elasticsearch.URL = fmt.Sprintf("http://127.0.0.1:%d", t.Ports.ESHTTP)
	t.Stores.Postgres = registry.SQLiteStore()
}
