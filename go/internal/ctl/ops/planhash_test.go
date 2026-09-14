package ops

import (
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
)

// TestPlannedCarriesTheHashTheEngineAccepts: there used to be TWO
// implementations of plan.json's digest — one here, one in jobs/hash.go — and
// they disagreed in shape (this one joined targets into a string and hashed
// step.destructive; the engine's did neither). Whichever value the planner
// wrote, the engine overwrote it with its own, so the disagreement was
// invisible until something compared them — at which point `plan_stale` would
// have fired on a plan that had not moved at all, or, worse, failed to fire on
// one that had.
//
// There is one function now, and this is the test that says so: the hash the
// planner puts on a Planned is the hash the engine computes for it.
func TestPlannedCarriesTheHashTheEngineAccepts(t *testing.T) {
	oc, _ := fixture(t, "dev", managed)
	for _, c := range []struct {
		verb string
		args map[string]any
	}{
		{"env-set", map[string]any{"key": "LOG_LEVEL", "value": "DEBUG"}},
		{"backup", map[string]any{"fence": true}},
		{"key-mint", map[string]any{"label": "ops", "role": "admin"}},
		{"start", nil},
	} {
		p := plan(t, oc, c.verb, c.args)
		if !strings.HasPrefix(p.Plan.PlanHash, "sha256:") {
			t.Fatalf("%s: plan_hash = %q", c.verb, p.Plan.PlanHash)
		}
		// The engine's own call, on the plan the planner produced.
		want, err := jobs.PlanHash(p.Plan, redactArgs(oc, c.args))
		if err != nil {
			t.Fatalf("%s: jobs.PlanHash: %v", c.verb, err)
		}
		if p.Plan.PlanHash != want {
			t.Errorf("%s: the planner's hash %s is not the engine's %s", c.verb, p.Plan.PlanHash, want)
		}
	}
}

// TestPlanHashMovesWithEveryFactThatChangesTheOutcome: the digest is only
// worth having if it covers what an operator approved. A registry that moved,
// a doctor that changed its mind, or a step that would write something else
// must each produce a different hash.
func TestPlanHashMovesWithEveryFactThatChangesTheOutcome(t *testing.T) {
	oc, _ := fixture(t, "dev", managed)
	args := map[string]any{"key": "LOG_LEVEL", "value": "DEBUG"}
	base := plan(t, oc, "env-set", args).Plan

	same := base
	if h, err := jobs.PlanHash(same, redactArgs(oc, args)); err != nil || h != base.PlanHash {
		t.Fatalf("the same plan hashed differently: %s vs %s (%v)", h, base.PlanHash, err)
	}
	for name, mutate := range map[string]func(p *model.Plan){
		"the registry generation": func(p *model.Plan) { p.RegistryGeneration++ },
		"the doctor's verdict":    func(p *model.Plan) { p.Doctor.Hash = "sha256:" + strings.Repeat("a", 64) },
		"what a step would write": func(p *model.Plan) { p.Steps[0].WouldWrite[0].Path = "/rag/elsewhere.env" },
		"the schema version":      func(p *model.Plan) { p.SchemaVersion++ },
	} {
		moved := base
		moved.Steps = append([]model.PlannedStep(nil), base.Steps...)
		moved.Steps[0].WouldWrite = append([]model.WouldWrite(nil), base.Steps[0].WouldWrite...)
		mutate(&moved)
		h, err := jobs.PlanHash(moved, redactArgs(oc, args))
		if err != nil {
			t.Fatal(err)
		}
		if h == base.PlanHash {
			t.Errorf("%s changed and the plan hash did not", name)
		}
	}
}
