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

// taken is the row after a successful take: the tenant is running as the
// service account, the block is still there, and a commit or an abandon is
// owed.
func taken(t *registry.Tenant) {
	released(t)
	t.State = "active"
	t.Owner = "svcbvbrc"
	t.Supervisor = supervisorInstance
	t.Handover.Phase = registry.HandoverTaken
	t.Handover.TakenAt = "2026-09-14T09:31:00Z"
	t.Handover.TakenBy = "svcbvbrc"
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
		// BOTH of a leg's ports: elasticsearch binds HTTP and transport,
		// qdrant HTTP and gRPC. A release that proved only the first free
		// leaves the second held, and the take then dies binding it.
		"probe: verify nothing listens on 24043 (es)",
		"probe: verify nothing listens on 24044 (es)",
		"instance: stop the instance qdrant-dev",
		"probe: verify nothing listens on 24041 (qdrant)",
		"probe: verify nothing listens on 24042 (qdrant)",
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

// The release's ROLLBACKS — both directions of them — are in
// handover_rollback_test.go: what a rollback may say about a tenant whose API
// it has already stopped is the one thing about this plan that was wrong, and
// it earns a file with the reasoning in it.

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

func TestHandoverTakeStartsTheStoresThenTheAPIAndFinishes(t *testing.T) {
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

	// NO cutover step, and the last thing it does is the registry write. A
	// take that parked would hold this tenant's registry lock for the length
	// of the soak, and that lock is the FLEET's: every backup of every other
	// tenant would queue behind one operator's 48 hours.
	for _, st := range p.Steps {
		if st.Cutover {
			t.Errorf("the take parks at %q: a soak must not hold the registry lock", st.Plan.Title)
		}
	}
	last := got[len(got)-1]
	if !strings.Contains(last, "registry") || !strings.Contains(last, "handover.phase: taken") {
		t.Errorf("the last step is %q, want the registry write that records the take", last)
	}
	// And `desired_boot` is not among the things it touches: an uncommitted
	// handover must not be something `fleet start --all` adopts at boot.
	if !warnsAbout(p, "`desired_boot` stays") {
		t.Errorf("the plan does not say that desired_boot waits for the commit: %v", p.Plan.Warnings)
	}
}

// The take moves `owner` and nothing else about the tenant's FUTURE: the
// processes are the service account's from the moment it spawns them, so the
// row has to say so, but `desired_boot` — the boot commitment — waits for a
// commit that the operator has to come back and make.
func TestHandoverTakeMovesTheOwnerAndLeavesTheRestToTheCommit(t *testing.T) {
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
	if tn.State != "active" {
		t.Errorf("state %q, want active", tn.State)
	}
	if tn.DesiredBoot != "disabled" {
		t.Errorf("desired_boot %q: an UNCOMMITTED handover must not be something `fleet start --all` adopts",
			tn.DesiredBoot)
	}
	h := tn.Handover
	if h == nil || h.Phase != registry.HandoverTaken {
		t.Fatalf("handover = %+v, want phase %s", h, registry.HandoverTaken)
	}
	if h.Token != testToken {
		t.Errorf("the token was cleared at the take: it is what the commit and the abandon are gated on")
	}
	if string(h.TakenBy) != "svcbvbrc" || h.TakenAt == "" {
		t.Errorf("handover = %+v", h)
	}
	if p.Result()["owner"] != "svcbvbrc" {
		t.Errorf("result = %v", p.Result())
	}
}

// And the commit, which is an ordinary job gated on that row.
func TestHandoverCommitIsTheBootCommitment(t *testing.T) {
	oc, fake := fixture(t, "dev", func(tn *registry.Tenant) {
		taken(tn)
		tn.RestartPending = true
	})
	// The API the take started, still up and attributable to this account.
	fake.FakeProc().MarkAlive(30001, oc.Tenant.Ports.API)
	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "commit"})
	got := titles(p)
	// It PROVES the tenant is up before it promises to bring it back at boot.
	if indexOfStep(got, "still up on 24040") < 0 || indexOfStep(got, "GET /health") < 0 {
		t.Fatalf("the commit does not check the tenant is running: %v", got)
	}
	for _, st := range p.Steps {
		if st.Cutover {
			t.Errorf("the commit parks at %q", st.Plan.Title)
		}
	}
	r := newRunner(oc, fake)
	r.runAll(t, p)

	tn := oc.Fleet.Tenants["dev"]
	if tn.DesiredBoot != "enabled" {
		t.Errorf("desired_boot %q, want enabled", tn.DesiredBoot)
	}
	if tn.Handover != nil {
		t.Errorf("the handover block survived the commit: %+v", tn.Handover)
	}
	if tn.RestartPending {
		t.Error("restart_pending survived a commit whose own take started the processes the row describes")
	}
	if tn.Owner != "svcbvbrc" || tn.Supervisor != supervisorInstance {
		t.Errorf("owner %q supervisor %q", tn.Owner, tn.Supervisor)
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

func TestHandoverCommitRefusesAnythingButATakenRowAndItsOwnAccount(t *testing.T) {
	for _, c := range []struct {
		name   string
		mutate func(*registry.Tenant)
		as     string
		want   string
	}{
		{"no handover", prepared, "svcbvbrc", "nothing to commit"},
		{"a released row", released, "svcbvbrc", "only a TAKEN handover"},
		{"the wrong account", taken, "wilke", "Run it as svcbvbrc"},
		{"a row the ctl does not supervise", func(tn *registry.Tenant) {
			taken(tn)
			tn.Supervisor = supervisorManual
		}, "svcbvbrc", "nothing to commit to"},
	} {
		oc, _ := fixture(t, "dev", c.mutate)
		err := planErrAs(t, oc, c.as, "handover", map[string]any{"phase": "commit"})
		if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), c.want) {
			t.Errorf("%s = %v, want it to mention %q", c.name, err, c.want)
		}
	}

	// A commit over a tenant that is DOWN would enable a boot for something
	// that is not running, and the soak it concludes concluded nothing.
	oc, fake := fixture(t, "dev", taken)
	fake.FakeProc().FreePort(oc.Tenant.Ports.API)
	p := planAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "commit"})
	r := newRunner(oc, fake)
	if _, err := r.run(p.Steps[0]); err == nil || !strings.Contains(err.Error(), "nothing is listening") {
		t.Fatalf("a commit over a stopped tenant = %v", err)
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

// TestHandoverAbandonAfterATakeGoesToTheReleasingAccount is the case the
// owner check exists for.
//
// After a take the ROW's owner is the service account — that is the take's
// whole point — so an abandon gated on `owner` would be the service account
// handing the tenant back to itself, and the account that actually has to
// start it again would be refused. It is gated on `handover.released_by`.
func TestHandoverAbandonAfterATakeGoesToTheReleasingAccount(t *testing.T) {
	oc, fake := fixture(t, "dev", taken)
	if err := planErrAs(t, oc, "svcbvbrc", "handover", map[string]any{"phase": "abandon"}); !strings.Contains(
		err.Error(), "released by wilke") {
		t.Errorf("an abandon by the account that TOOK it = %v", err)
	}

	// And every port has to be free first, not only the API's: after a take
	// the service account runs this tenant's stores too, and a row recording
	// `manual` over them would leave them with nothing that stops them.
	p := planAs(t, oc, "wilke", "handover", map[string]any{"phase": "abandon"})
	titlesOf := titles(p)
	for _, want := range []string{"24040 (the API)", "24041 (qdrant)", "24043 (es)"} {
		if indexOfStep(titlesOf, want) < 0 {
			t.Errorf("no port check for %s: %v", want, titlesOf)
		}
	}
	fake.FakeProc().SetOwner(oc.Tenant.Ports.QdrantHTTP, 0, 0)
	r := newRunner(oc, fake)
	var refused error
	for _, st := range p.Steps {
		if _, err := r.run(st); err != nil {
			refused = err
			break
		}
	}
	if refused == nil || !strings.Contains(refused.Error(), "still running as the other account") {
		t.Fatalf("an abandon over the take's running store = %v", refused)
	}

	// With everything stopped, the row goes back to the releasing account.
	oc2, fake2 := fixture(t, "dev", taken)
	for _, port := range []int{oc2.Tenant.Ports.API, oc2.Tenant.Ports.QdrantHTTP, oc2.Tenant.Ports.ESHTTP} {
		fake2.FakeProc().FreePort(port)
	}
	p2 := planAs(t, oc2, "wilke", "handover", map[string]any{"phase": "abandon"})
	newRunner(oc2, fake2).runAll(t, p2)
	tn := oc2.Fleet.Tenants["dev"]
	if tn.Owner != "wilke" || tn.Supervisor != supervisorManual || tn.State != "active" || tn.Handover != nil {
		t.Errorf("after the abandon: owner %q supervisor %q state %q handover %+v",
			tn.Owner, tn.Supervisor, tn.State, tn.Handover)
	}
	if p2.Result()["owner"] != "wilke" {
		t.Errorf("result = %v", p2.Result())
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
