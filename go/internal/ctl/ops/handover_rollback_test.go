package ops

import (
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// TestHandoverReleaseRollbackTellsTheTruthAboutAStoppedTenant is the
// regression for the other half of the rollback — the half the first test did
// not cover.
//
// `TestHandoverReleaseRollsTheRowBackWhenAStopFails` covers the case where
// NOTHING was stopped. This is the case where the registry step AND the API
// stop both succeeded and a later step failed. The engine rolls back
// newest-first; the API-stop's rollback can only LOG "run restore.sh" (the ctl
// does not know a hand-started uvicorn's command line), so the registry step's
// rollback used to write `state: active` over a tenant whose API was gone —
// which then made `--abandon` refuse ("there is nothing to abandon") and left
// the operator with no verb at all.
//
// A rollback of a step whose successors could not be undone has to say what is
// TRUE, and what is true is the row the release wrote: `handover`, phase
// `released`, the block and its token intact.
func TestHandoverReleaseRollbackTellsTheTruthAboutAStoppedTenant(t *testing.T) {
	oc, fake := fixture(t, "dev", prepared)
	p := planAs(t, oc, "wilke", "handover", map[string]any{"phase": "release"})
	fake.FakeProc().MarkAlive(4242, oc.Tenant.Ports.API)
	r := newRunner(oc, fake)

	var reg, apiStop jobs.Step
	for _, s := range p.Steps {
		if s.Plan.Kind == "registry" {
			reg = s
		}
		if s.Plan.Kind == "proc" && apiStop.Plan.Title == "" {
			apiStop = s
		}
	}
	if apiStop.Plan.Title == "" {
		t.Fatal("no proc step in the release plan")
	}
	if _, err := r.run(reg); err != nil {
		t.Fatalf("the registry step: %v", err)
	}
	if _, err := r.run(apiStop); err != nil {
		t.Fatalf("the API stop step: %v", err)
	}
	// A later step failed. Roll back newest-first, as the engine does.
	if _, err := r.rollback(apiStop); err != nil {
		t.Fatalf("the API stop rollback: %v", err)
	}
	detail, err := r.rollback(reg)
	if err != nil {
		t.Fatalf("the registry rollback: %v", err)
	}

	tn := oc.Fleet.Tenants["dev"]
	if tn.State != registry.StateHandover {
		t.Errorf("state = %q with the API stopped and nothing started again; want %q",
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
