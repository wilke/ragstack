package ops

// The two-account handover: what each phase refuses, what it does, and in
// which ORDER it does it.
//
// The order assertions are the ones worth having. A release that stopped the
// tenant before it recorded `state: handover` would leave a row saying
// `active` over a tenant with nothing running; a release that took its census
// after the stop would record zeroes; a take that spawned the API before the
// stores answered would be the failure the instance supervisor already exists
// to prevent. Each of those is one step in the wrong place, and none of them
// is visible in a test that only asserts the set of steps.

import (
	"errors"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// prepared is a fixture tenant that has been through PR-E1's preparation ops:
// loopback bind, a UI that is not a dev server, confirmed capabilities on its
// own stores, a light backup and a rollback descriptor. It is the state a
// release is allowed to act on, and every refusal test below removes exactly
// one of these.
func prepared(t *registry.Tenant) {
	t.API.Bind = "127.0.0.1"
	caps := registry.Capabilities{Stop: true, Purge: false, Restore: true, Snapshot: true}
	t.Stores.Qdrant.Capabilities = caps
	t.Stores.Elasticsearch.Capabilities = caps
	t.LastBackup = &registry.BackupRecord{
		Bundle: "/rag/backups/tenants/dev/20260914T090000Z-backup", At: "2026-09-14T09:00:00Z",
		Kind: "backup", Scope: []string{"config", "state"},
	}
	t.RollbackDescriptor = testDescriptor(t)
}

func testDescriptor(t *registry.Tenant) *registry.RollbackDescriptor {
	return &registry.RollbackDescriptor{
		CapturedAt: "2026-09-14T08:00:00Z", Owner: "wilke",
		Paths: registry.RollbackPaths{DataDir: t.DataDir, Worktree: t.Worktree, PythonEnv: t.PythonEnv},
		Ports: t.Ports, Code: t.Code, EnvFileSHA256: t.EnvFileSHA256,
		LaunchArgs: []registry.LaunchArg{}, GatewayGeneration: 7,
	}
}

// released is a tenant the release has already acted on: stopped, `state:
// handover`, a token in the row. It is what a take, a commit and an abandon
// all start from.
func released(t *registry.Tenant) {
	prepared(t)
	t.State = registry.StateHandover
	t.Handover = &registry.Handover{
		Phase: registry.HandoverReleased, Token: strings.Repeat("ab", 16),
		StartedAt: "2026-09-14T09:29:00Z", ReleasedBy: "wilke",
		ReleasedAt: "2026-09-14T09:29:10Z", DescriptorRef: "2026-09-14T08:00:00Z",
		Census: []registry.CensusEntry{
			{Store: "qdrant", Name: "docs", Count: 1200},
			{Store: "qdrant", Name: "chunks", Count: 88_000},
		},
	}
}

const testToken = "abababababababababababababababab"

// ---------------------------------------------------------------- release

func TestHandoverReleaseRefusesUntilTheTenantIsPrepared(t *testing.T) {
	// The ACCOUNT first: a release stops processes only their owner can
	// signal, so a job running as anybody else is refused before any
	// precondition about the tenant is even looked at.
	oc, _ := fixture(t, "dev", prepared)
	err := planErrAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "release"})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "belong to wilke") {
		t.Errorf("a release run by the wrong account = %v", err)
	}

	cases := []struct {
		name   string
		mutate func(*registry.Tenant)
		want   string
	}{
		{"a non-loopback bind", func(tn *registry.Tenant) { prepared(tn); tn.API.Bind = "0.0.0.0" }, "set-bind"},
		{"a dev-mode UI", func(tn *registry.Tenant) {
			prepared(tn)
			tn.UI = registry.UI{Mode: registry.UIModeDev, Port: 8090, Base: tn.UI.Base}
		}, "set-ui-mode"},
		{"no backup", func(tn *registry.Tenant) { prepared(tn); tn.LastBackup = nil }, "--scope config,state"},
		{"no descriptor", func(tn *registry.Tenant) { prepared(tn); tn.RollbackDescriptor = nil }, "rollback_descriptor"},
		{"unconfirmed stores", func(tn *registry.Tenant) {
			prepared(tn)
			tn.Stores.Elasticsearch.Capabilities.Stop = false
		}, "--confirm-stores"},
		{"a tenant that is not running", func(tn *registry.Tenant) { prepared(tn); tn.State = "stopped" }, "not active"},
		{"a handover already in flight", released, "already has a handover in flight"},
		{"a row the ctl already supervises", func(tn *registry.Tenant) {
			prepared(tn)
			tn.Supervisor = supervisorInstance
		}, "set-supervisor"},
	}
	for _, c := range cases {
		oc, _ := fixture(t, "dev", c.mutate)
		err := planErrAs(t, oc, "wilke", "handover", map[string]any{"phase": "release"})
		if !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("%s = %v, want a refusal", c.name, err)
			continue
		}
		if !strings.Contains(err.Error(), c.want) {
			t.Errorf("%s = %v, want it to mention %q", c.name, err, c.want)
		}
	}

	// And the escape hatch, which says so in the plan rather than silently.
	oc, _ = fixture(t, "dev", func(tn *registry.Tenant) { prepared(tn); tn.LastBackup = nil })
	p := planAs(t, oc, "wilke", "handover", map[string]any{"phase": "release", "accept_no_backup": true})
	if !warnsAbout(p, "NO recovery point") {
		t.Errorf("accept_no_backup did not warn: %v", p.Plan.Warnings)
	}
}

func TestHandoverReleaseCountsAndRecordsBeforeItStopsAnything(t *testing.T) {
	oc, _ := fixture(t, "dev", prepared)
	p := planAs(t, oc, "wilke", "handover", map[string]any{"phase": "release"})

	// The order is the operation. Every read happens while the tenant is up,
	// the row is written before the first stop, and the proofs follow each
	// stop.
	want := []string{
		"envfile: check that the take can obtain the postgres password",
		"probe: check that no ingest job is still running",
		"probe: census: count every collection in the tenant's own qdrant",
		"probe: census: count every index in the tenant's own elasticsearch",
		"probe: census: the tenant API's own collection listing, with counts",
		"registry: record state: handover and the hand-off token",
		"proc: stop the hand-started API through its pidfile (TERM, then KILL)",
		"probe: verify nothing listens on 24040 (the API)",
		"instance: stop the instance elasticsearch-dev",
		"probe: verify nothing listens on 24043 (es)",
		"instance: stop the instance qdrant-dev",
		"probe: verify nothing listens on 24041 (qdrant)",
	}
	got := titles(p)
	if len(got) != len(want) {
		t.Fatalf("steps =\n  %s", strings.Join(got, "\n  "))
	}
	for i := range want {
		if !strings.HasPrefix(got[i], strings.SplitN(want[i], ":", 2)[0]+":") ||
			!strings.Contains(got[i], strings.TrimSpace(strings.SplitN(want[i], ":", 2)[1])) {
			t.Errorf("step %d = %q, want %q", i+1, got[i], want[i])
		}
	}
	if string(p.Plan.ConfirmValue) != "dev" {
		t.Errorf("confirm value = %q, want the tenant name", p.Plan.ConfirmValue)
	}
	// The stores come down in the reverse of the start order, and the API
	// before either of them: nothing may be left writing to a store that is
	// going away.
	if !warnsAbout(p, "restore.sh --tenant dev") {
		t.Errorf("the plan does not say how to get back: %v", p.Plan.Warnings)
	}
}

func TestHandoverReleaseWritesTheRowAndTheTokenBeforeTheStops(t *testing.T) {
	oc, fake := fixture(t, "dev", prepared)
	// The hand-started API the release is about to stop: the pid the fixture's
	// pidfile names, alive and holding the API port.
	fake.FakeProc().MarkAlive(4242, oc.Tenant.Ports.API)
	p := planAs(t, oc, "wilke", "handover", map[string]any{"phase": "release"})
	r := newRunner(oc, fake)
	r.runAll(t, p)

	tn := oc.Fleet.Tenants["dev"]
	if tn.State != registry.StateHandover {
		t.Fatalf("state = %q, want %q", tn.State, registry.StateHandover)
	}
	h := tn.Handover
	if h == nil {
		t.Fatal("no handover block was written")
	}
	if h.Phase != registry.HandoverReleased || h.ReleasedBy != "wilke" {
		t.Errorf("handover = %+v", h)
	}
	if len(h.Token) != 32 {
		t.Errorf("token = %q, want 32 hex characters", h.Token)
	}
	if got := p.Result()["token"]; got != h.Token {
		t.Errorf("the token is not on the job result: %v", p.Result())
	}
	if string(h.DescriptorRef) != "2026-09-14T08:00:00Z" {
		t.Errorf("descriptor_ref = %q", h.DescriptorRef)
	}
	// The census is what the fixture's stores actually hold, read before the
	// stop: qdrant's two collections with their counts, ES's one index, and
	// the API's own listing.
	counts := map[string]int64{}
	for _, e := range h.Census {
		counts[e.Store+"/"+e.Name] = e.Count
	}
	for key, want := range map[string]int64{
		"qdrant/docs": 1200, "qdrant/chunks": 88_000, "elasticsearch/dev-chunks": 88_000,
	} {
		if counts[key] != want {
			t.Errorf("census[%s] = %d, want %d (census = %+v)", key, counts[key], want, h.Census)
		}
	}
	// And the instances really were stopped.
	if names := fake.FakeInstances().Names(); len(names) != 0 {
		t.Errorf("instances still running after the release: %v", names)
	}
}

func TestHandoverReleaseRollsTheRowBackWhenAStopFails(t *testing.T) {
	oc, fake := fixture(t, "dev", prepared)
	p := planAs(t, oc, "wilke", "handover", map[string]any{"phase": "release"})
	r := newRunner(oc, fake)
	// Only the registry step, then its rollback: the case is "the job died
	// after recording the handover", and what must not survive it is a row
	// claiming a move that never happened.
	var reg jobs.Step
	for _, s := range p.Steps {
		if s.Plan.Kind == "registry" {
			reg = s
		}
	}
	if _, err := r.run(reg); err != nil {
		t.Fatalf("the registry step: %v", err)
	}
	if _, err := r.rollback(reg); err != nil {
		t.Fatalf("its rollback: %v", err)
	}
	tn := oc.Fleet.Tenants["dev"]
	if tn.State != "active" || tn.Handover != nil {
		t.Errorf("after the rollback: state %q, handover %+v", tn.State, tn.Handover)
	}
	if _, ok := p.Result()["token"]; ok {
		t.Error("the rolled-back job still advertises a token")
	}
}

// ---------------------------------------------------------------- take

func TestHandoverTakeChecksTheTokenAndTheRow(t *testing.T) {
	oc, _ := fixture(t, "dev", prepared)
	err := planErrAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	if !strings.Contains(err.Error(), "no handover in flight") {
		t.Errorf("a take with no release = %v", err)
	}

	oc, _ = fixture(t, "dev", released)
	err = planErrAs(t, oc, "svcbvbrc", "handover",
		map[string]any{"phase": "take", "token": strings.Repeat("cd", 16)})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "does not match") {
		t.Errorf("a wrong token = %v", err)
	}
	// The refusal must not hand the caller the answer.
	if strings.Contains(err.Error(), testToken) {
		t.Errorf("the refusal echoed the expected token: %v", err)
	}

	// A take that arrives without one at all is a VALIDATION error rather than
	// a refusal: nothing about the host was consulted.
	if err := planErrAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take"}); !errors.Is(err, jobs.ErrValidation) {
		t.Errorf("a take with no token = %v, want a validation error", err)
	}
	// And a token on any other phase is refused before a plan exists.
	if err := planErrAs(t, oc, "wilke", "handover",
		map[string]any{"phase": "abandon", "token": testToken}); !errors.Is(err, jobs.ErrValidation) {
		t.Errorf("a token on --abandon = %v, want a validation error", err)
	}
}

func TestHandoverTakeStartsTheStoresThenTheAPIAndParksAtItsCutover(t *testing.T) {
	oc, _ := fixture(t, "dev", released)
	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	got := titles(p)

	// Ports proved free BEFORE anything starts; the supervisor recorded before
	// the first process, because the pidfile this job writes is the instance
	// supervisor's and a row still saying `manual` would be a row no later
	// `tenant stop` acts on.
	if !strings.Contains(got[0], "verify nothing listens") {
		t.Errorf("the take does not begin by proving the ports free: %v", got)
	}
	supIdx := indexOfStep(got, "record supervisor: instance")
	qdrantIdx := indexOfStep(got, "start the instance qdrant-dev")
	apiIdx := indexOfStep(got, "start the API detached")
	censusIdx := indexOfStep(got, "compare the census")
	gatewayIdx := indexOfStep(got, "through the live gateway")
	switch {
	case supIdx < 0 || qdrantIdx < 0 || apiIdx < 0 || censusIdx < 0 || gatewayIdx < 0:
		t.Fatalf("steps =\n  %s", strings.Join(got, "\n  "))
	case !(supIdx < qdrantIdx && qdrantIdx < apiIdx && apiIdx < censusIdx && censusIdx < gatewayIdx):
		t.Errorf("out of order (supervisor %d, qdrant %d, api %d, census %d, gateway %d):\n  %s",
			supIdx, qdrantIdx, apiIdx, censusIdx, gatewayIdx, strings.Join(got, "\n  "))
	}

	// Exactly one cutover, and exactly one step after it: the commit.
	cutover, after := -1, []string{}
	for i, s := range p.Steps {
		if s.Cutover {
			if cutover >= 0 {
				t.Fatalf("more than one cutover step: %v", got)
			}
			cutover = i
			continue
		}
		if cutover >= 0 {
			after = append(after, s.Plan.Title)
		}
	}
	if cutover < 0 {
		t.Fatalf("no cutover step: %v", got)
	}
	if len(after) != 1 || !strings.Contains(after[0], "commit") {
		t.Errorf("after the cutover = %v, want exactly the commit", after)
	}
}

func TestHandoverTakeThenCommitMakesTheTenantTheControlPlanes(t *testing.T) {
	oc, fake := fixture(t, "dev", func(tn *registry.Tenant) {
		released(tn)
		// The release stopped these; the take starts them again.
		tn.RestartPending = true
	})
	takeFixture(t, oc, fake)
	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	r := newRunner(oc, fake)
	r.runAll(t, p)

	tn := oc.Fleet.Tenants["dev"]
	if tn.Owner != "svcbvbrc" || tn.Supervisor != supervisorInstance {
		t.Errorf("owner %q supervisor %q, want svcbvbrc/instance", tn.Owner, tn.Supervisor)
	}
	if tn.State != "active" || tn.DesiredBoot != "enabled" {
		t.Errorf("state %q desired_boot %q", tn.State, tn.DesiredBoot)
	}
	if tn.Handover != nil {
		t.Errorf("the handover block survived the commit: %+v", tn.Handover)
	}
	if tn.RestartPending {
		t.Error("restart_pending survived a commit whose own steps started the processes the row describes")
	}
	// The descriptor is the only way back for `restore.sh --tenant`, so a
	// commit never touches it.
	if tn.RollbackDescriptor == nil || tn.RollbackDescriptor.CapturedAt != "2026-09-14T08:00:00Z" {
		t.Errorf("the rollback descriptor was changed: %+v", tn.RollbackDescriptor)
	}
	if tn.LastOps["handover"].Outcome != "succeeded" {
		t.Errorf("last_ops = %+v", tn.LastOps)
	}
}

func TestHandoverTakeRefusesWhenAStoreCameBackShort(t *testing.T) {
	oc, fake := fixture(t, "dev", func(tn *registry.Tenant) {
		released(tn)
		tn.Handover.Census = []registry.CensusEntry{{Store: "qdrant", Name: "docs", Count: 9_999_999}}
	})
	takeFixture(t, oc, fake)
	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	r := newRunner(oc, fake)
	for _, s := range p.Steps {
		_, err := r.run(s)
		if err == nil {
			continue
		}
		if !strings.Contains(err.Error(), "FEWER rows") {
			t.Fatalf("step %d (%s): %v", s.Plan.N, s.Plan.Title, err)
		}
		if !strings.Contains(err.Error(), "restore.sh --tenant dev") {
			t.Errorf("the refusal does not say how to get back: %v", err)
		}
		return
	}
	t.Fatal("a store that came back with a fraction of its rows was accepted")
}

// A shared-store tenant is the case the wait exists for: the ctl starts
// nothing for it and must still not spawn the API before the store answers.
func TestHandoverTakeOfASharedStoreTenantStartsNoStoreAndStillWaits(t *testing.T) {
	oc, _ := fixture(t, "demo", func(tn *registry.Tenant) {
		prepared(tn)
		// demo's stores are SHARED: `prepared` confirmed capabilities it does
		// not own, which the row must not claim.
		tn.Stores.Qdrant.Capabilities = registry.Capabilities{}
		tn.Stores.Elasticsearch.Capabilities = registry.Capabilities{}
		released(tn)
		tn.Stores.Qdrant.Capabilities = registry.Capabilities{}
		tn.Stores.Elasticsearch.Capabilities = registry.Capabilities{}
	})
	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "take", "token": testToken})
	got := titles(p)
	for _, s := range got {
		if strings.Contains(s, "start the instance") {
			t.Errorf("the take started a store this tenant does not own: %v", got)
		}
	}
	if indexOfStep(got, "start the API detached") < 0 {
		t.Errorf("the API is not started: %v", got)
	}
	// The wait itself is inside the API start step (it is a run-time gate, not
	// a plan step), and the step says so.
	if !stepWarns(p, "start the API detached", "restart-on-failure") {
		t.Errorf("the API step lost its warnings: %v", got)
	}
}

// ---------------------------------------------------------------- commit, abandon

func TestHandoverCommitIsRefusedAsANewJob(t *testing.T) {
	oc, _ := fixture(t, "dev", released)
	err := planErrAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "commit"})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "CONTINUATION") {
		t.Fatalf("phase commit = %v, want the continuation refusal", err)
	}
	if !strings.Contains(err.Error(), "job continue") {
		t.Errorf("the refusal does not name the continuation: %v", err)
	}
}

func TestHandoverAbandonPutsTheRowBackAndNamesTheRestore(t *testing.T) {
	oc, _ := fixture(t, "dev", prepared)
	if err := planErrAs(t, oc, "wilke", "handover", map[string]any{"phase": "abandon"}); !strings.Contains(
		err.Error(), "nothing to abandon") {
		t.Errorf("an abandon with no handover = %v", err)
	}

	oc, fake := fixture(t, "dev", released)
	if err := planErrAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "abandon"}); !strings.Contains(
		err.Error(), "Run it as wilke") {
		t.Errorf("an abandon by the wrong account = %v", err)
	}

	p := planAs(t, oc, "wilke", "handover", map[string]any{"phase": "abandon"})
	// The released tenant is down, so the port check passes; run it all.
	fake.FakeProc().FreePort(oc.Tenant.Ports.API)
	r := newRunner(oc, fake)
	r.runAll(t, p)

	tn := oc.Fleet.Tenants["dev"]
	if tn.Supervisor != supervisorManual || tn.State != "active" || tn.Handover != nil {
		t.Errorf("after the abandon: supervisor %q state %q handover %+v", tn.Supervisor, tn.State, tn.Handover)
	}
	if p.Result()["next"] != "ops/coconut/restore.sh --tenant dev" {
		t.Errorf("result = %v, want the restore command", p.Result())
	}
}

// ---------------------------------------------------------------- set-supervisor

func TestSetSupervisorRefusesWhileAHandoverIsInFlight(t *testing.T) {
	oc, _ := fixture(t, "dev", released)
	err := planErr(t, oc, "set-supervisor", map[string]any{"supervisor": "instance"})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "handover in flight") {
		t.Fatalf("err = %v", err)
	}
}

func TestSetSupervisorRefusesAPortThisAccountCannotAttribute(t *testing.T) {
	oc, fake := fixture(t, "dev", prepared)
	// The API port is held by a process with no readable owner — another
	// account's, which is what pid 0 means from `ss -ltnp`.
	fake.FakeProc().SetOwner(oc.Tenant.Ports.API, 0, 0)
	p := plan(t, oc, "set-supervisor", map[string]any{"supervisor": "instance"})
	r := newRunner(oc, fake)
	_, err := r.run(p.Steps[0])
	if err == nil || !strings.Contains(err.Error(), "cannot attribute") {
		t.Fatalf("err = %v, want a refusal naming the unattributable process", err)
	}
	if !strings.Contains(err.Error(), "handover") {
		t.Errorf("the refusal does not point at the handover: %v", err)
	}
}

func TestSetSupervisorWritesTheRowAndNothingElse(t *testing.T) {
	oc, fake := fixture(t, "dev", prepared)
	fake.FakeProc().FreePort(oc.Tenant.Ports.API)
	p := plan(t, oc, "set-supervisor", map[string]any{"supervisor": "instance"})
	r := newRunner(oc, fake)
	r.runAll(t, p)
	if got := oc.Fleet.Tenants["dev"].Supervisor; got != supervisorInstance {
		t.Errorf("supervisor = %q", got)
	}
	for _, s := range p.Steps {
		if s.Plan.Destructive {
			t.Errorf("set-supervisor planned a destructive step: %s", s.Plan.Title)
		}
	}
	if p.Result()["previous_supervisor"] != supervisorManual {
		t.Errorf("result = %v", p.Result())
	}
}

// ---------------------------------------------------------------- helpers

// takeFixture is the host a take actually runs on: the release has stopped
// everything, and the elasticsearch config bind exists (apptainer refuses a
// bind whose source is missing, so the instance supervisor checks it first).
func takeFixture(t *testing.T, oc jobs.Context, fake *drivers.Fake) {
	t.Helper()
	fake.FakeInstances().StopAll()
	fake.FakeProc().FreePort(oc.Tenant.Ports.API)
	tp := paths.TenantPaths(oc.Roots, oc.Tenant.Name, oc.Tenant.ManifestName)
	fake.FakeFiles().Dirs[tp.ESConfig] = 0o2770
}

func indexOfStep(titles []string, substr string) int {
	for i, s := range titles {
		if strings.Contains(s, substr) {
			return i
		}
	}
	return -1
}

func warnsAbout(p *jobs.Planned, substr string) bool {
	for _, w := range p.Plan.Warnings {
		if strings.Contains(w, substr) {
			return true
		}
	}
	return false
}

func stepWarns(p *jobs.Planned, titleSubstr, warnSubstr string) bool {
	for _, s := range p.Steps {
		if !strings.Contains(s.Plan.Title, titleSubstr) {
			continue
		}
		for _, w := range s.Plan.Warnings {
			if strings.Contains(w, warnSubstr) {
				return true
			}
		}
	}
	return false
}
