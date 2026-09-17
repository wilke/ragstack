package ops

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// TestHandoverReleaseRollbackStartsTheAPIAgain is the regression for the
// second half of the coconut outage.
//
// The registry step and the API stop both succeeded, a later step failed, and
// the engine rolled back newest-first. The API stop's rollback used to LOG
// "run restore.sh" and nothing else, on the reasoning that the ctl does not
// know a hand-started uvicorn's command line — so a job that reported a
// successful rollback left the hackathon tenant answering 502 for ten minutes
// until somebody ran the script by hand.
//
// It knows the command line: the row carries the worktree, the bind, the port
// and the env files, and render.APIArgv turns them into the argv the take
// would spawn. So the rollback STARTS THE API, as the releasing account, and
// the registry step's rollback — which asks the host rather than assuming —
// then finds the tenant up and puts the row all the way back.
func TestHandoverReleaseRollbackStartsTheAPIAgain(t *testing.T) {
	oc, fake := fixture(t, "dev", prepared)
	p := planAs(t, oc, "wilke", "handover", map[string]any{"phase": "release"})
	fake.FakeProc().MarkAlive(4242, oc.Tenant.Ports.API)
	r := newRunner(oc, fake)

	reg, apiStop := releaseRegistryAndAPIStop(t, p)
	if _, err := r.run(reg); err != nil {
		t.Fatalf("the registry step: %v", err)
	}
	if _, err := r.run(apiStop); err != nil {
		t.Fatalf("the API stop step: %v", err)
	}
	if listening, _ := fake.Proc().Listening(context.Background(), oc.Tenant.Ports.API); listening {
		t.Fatal("the API stop left the port held; this test is not testing what it thinks")
	}

	// A later step failed. Roll back newest-first, as the engine does.
	detail, err := r.rollback(apiStop)
	if err != nil {
		t.Fatalf("the API stop rollback: %v", err)
	}
	if !strings.Contains(detail, "up again") {
		t.Errorf("the API stop rollback said %q, want it to report a start", detail)
	}
	if listening, _ := fake.Proc().Listening(context.Background(), oc.Tenant.Ports.API); !listening {
		t.Fatal("the rollback did not put the API back on its port: this is the ten-minute 502")
	}
	if len(fake.FakeProc().Spawned) != 1 {
		t.Fatalf("the rollback spawned %d processes, want the one the row describes", len(fake.FakeProc().Spawned))
	}
	// As the releasing account, out of the tenant's own worktree: the same
	// launch `supervisor: instance` uses.
	if dir := fake.FakeProc().Spawned[0].Dir; !strings.HasPrefix(dir, oc.Tenant.Worktree) {
		t.Errorf("the restarted API runs in %q, want it under the row's worktree %q", dir, oc.Tenant.Worktree)
	}

	if _, err := r.rollback(reg); err != nil {
		t.Fatalf("the registry rollback: %v", err)
	}
	tn := oc.Fleet.Tenants["dev"]
	if tn.State != "active" || tn.Handover != nil {
		t.Errorf("state %q handover %+v: with the API running again the release did not happen and the row "+
			"should say so", tn.State, tn.Handover)
	}
}

// And when the restart cannot be made — the row does not render, the spawn
// fails — the rollback falls back to the sentence it used to be, and the
// registry step's rollback keeps the honest, recoverable row: `handover`,
// phase `released`, the token intact, so `--abandon` still has something to
// act on and `--release` can be re-run.
func TestHandoverReleaseRollbackKeepsTheRowWhenTheAPICannotBeStarted(t *testing.T) {
	oc, fake := fixture(t, "dev", prepared)
	p := planAs(t, oc, "wilke", "handover", map[string]any{"phase": "release"})
	fake.FakeProc().MarkAlive(4242, oc.Tenant.Ports.API)
	r := newRunner(oc, fake)

	reg, apiStop := releaseRegistryAndAPIStop(t, p)
	if _, err := r.run(reg); err != nil {
		t.Fatalf("the registry step: %v", err)
	}
	if _, err := r.run(apiStop); err != nil {
		t.Fatalf("the API stop step: %v", err)
	}
	fake.Fail("proc.Spawn", errors.New("no such python"))

	detail, err := r.rollback(apiStop)
	if err != nil {
		t.Fatalf("a rollback whose restart failed must not fail the rollback: %v", err)
	}
	if !strings.Contains(detail, "restore.sh --tenant dev") {
		t.Errorf("the rollback said %q, want the restore.sh instruction", detail)
	}
	detail, err = r.rollback(reg)
	if err != nil {
		t.Fatalf("the registry rollback: %v", err)
	}

	tn := oc.Fleet.Tenants["dev"]
	if tn.State != registry.StateHandover {
		t.Errorf("state = %q with the API stopped and not started again; want %q",
			tn.State, registry.StateHandover)
	}
	if tn.Handover == nil || tn.Handover.Phase != registry.HandoverReleased {
		t.Fatalf("the handover block was cleared over a stopped tenant: %+v — `--abandon` would now refuse, "+
			"and the operator would have no verb left", tn.Handover)
	}
	if tn.Handover.Token == "" {
		t.Error("the token was dropped: a take could no longer be run against this release")
	}
	// And it says what to run, in the order that actually works.
	for _, want := range []string{"restore.sh --tenant dev", "--abandon"} {
		if !strings.Contains(detail, want) {
			t.Errorf("the rollback detail does not name %q: %q", want, detail)
		}
	}
	if strings.Index(detail, "restore.sh") > strings.Index(detail, "--abandon") {
		t.Errorf("the rollback names the two commands in the wrong order: %q", detail)
	}
}

// releaseRegistryAndAPIStop picks the two steps these tests drive by hand.
func releaseRegistryAndAPIStop(t *testing.T, p *jobs.Planned) (reg, apiStop jobs.Step) {
	t.Helper()
	for _, s := range p.Steps {
		if s.Plan.Kind == "registry" {
			reg = s
		}
		if s.Plan.Kind == "proc" && apiStop.Plan.Title == "" {
			apiStop = s
		}
	}
	if reg.Plan.Title == "" || apiStop.Plan.Title == "" {
		t.Fatal("the release plan has no registry step or no proc step")
	}
	return reg, apiStop
}

// The other direction, kept explicit: when the API is still up — nothing was
// stopped — the rollback really does put the row all the way back, because
// then there is nothing left over to be honest about.
func TestHandoverReleaseRollbackClearsTheRowWhenNothingWasStopped(t *testing.T) {
	oc, fake := fixture(t, "dev", prepared)
	p := planAs(t, oc, "wilke", "handover", map[string]any{"phase": "release"})
	fake.FakeProc().MarkAlive(4242, oc.Tenant.Ports.API)
	r := newRunner(oc, fake)

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
		t.Fatalf("the registry rollback: %v", err)
	}
	tn := oc.Fleet.Tenants["dev"]
	if tn.State != "active" || tn.Handover != nil {
		t.Errorf("state %q handover %+v, want a row that says the handover never happened", tn.State, tn.Handover)
	}
	if _, ok := p.Result()["token"]; ok {
		t.Error("the rolled-back job still advertises a token")
	}
}
