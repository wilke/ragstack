package jobs

import (
	"regexp"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/model"
)

func samplePlan() model.Plan {
	return model.Plan{
		Op: "backup", Tenant: "dev", RegistryGeneration: 7, SchemaVersion: 1,
		Doctor: model.DoctorResponse{
			Status: model.StatusGreen, Hash: sha256Of([]byte("findings")),
			GeneratedAt: "2026-09-14T10:00:00Z",
			Scope:       model.Scope{Tenant: "dev", Op: "backup"},
			Findings:    []model.Finding{},
		},
		Steps: []model.PlannedStep{
			{N: 1, Kind: "qdrant", Title: "snapshot", Targets: []string{"dev"},
				WouldWrite: []model.WouldWrite{{Path: "/rag/data/ctl/x", Mode: "0660", Preview: "a"}},
				WouldRun:   []model.WouldRun{{Argv: []string{"/bin/true"}}},
				Warnings:   []string{}},
		},
		Warnings: []string{},
	}
}

// TestPlanHashIgnoresMapOrder: the digest is over CANONICAL json, so two
// argument maps that differ only in iteration order hash the same. Without
// this every second dry run would look stale.
func TestPlanHashIgnoresMapOrder(t *testing.T) {
	p := samplePlan()
	a := map[string]any{"fence": true, "tar": false, "note": "nightly"}
	b := map[string]any{"note": "nightly", "tar": false, "fence": true}

	ha, err := PlanHash(p, a)
	if err != nil {
		t.Fatal(err)
	}
	hb, err := PlanHash(p, b)
	if err != nil {
		t.Fatal(err)
	}
	if ha != hb {
		t.Fatalf("map order changed the hash: %s vs %s", ha, hb)
	}
	if !regexp.MustCompile(`^sha256:[0-9a-f]{64}$`).MatchString(ha) {
		t.Fatalf("plan_hash = %q, does not match the contract pattern", ha)
	}
	// Stable across repeated computation.
	for i := 0; i < 20; i++ {
		if again, _ := PlanHash(p, a); again != ha {
			t.Fatalf("the hash is not stable: %s then %s", ha, again)
		}
	}
}

// TestPlanHashMovesWithEveryInputThatMatters: each field the contract names
// must change the digest, or plan_stale would miss a real change.
func TestPlanHashMovesWithEveryInputThatMatters(t *testing.T) {
	base := samplePlan()
	args := map[string]any{"fence": true}
	h0, _ := PlanHash(base, args)

	cases := map[string]func(p *model.Plan){
		"the registry generation":   func(p *model.Plan) { p.RegistryGeneration = 8 },
		"the doctor's findings":     func(p *model.Plan) { p.Doctor.Hash = sha256Of([]byte("other")) },
		"the op":                    func(p *model.Plan) { p.Op = "restore" },
		"the tenant":                func(p *model.Plan) { p.Tenant = "demo" },
		"the schema version":        func(p *model.Plan) { p.SchemaVersion = 2 },
		"a step's kind":             func(p *model.Plan) { p.Steps[0].Kind = "es" },
		"a step's targets":          func(p *model.Plan) { p.Steps[0].Targets = []string{"demo"} },
		"a step's title":            func(p *model.Plan) { p.Steps[0].Title = "snapshot everything" },
		"a file a step would write": func(p *model.Plan) { p.Steps[0].WouldWrite[0].Preview = "b" },
		"a command a step would run": func(p *model.Plan) {
			p.Steps[0].WouldRun[0].Argv = []string{"/bin/false"}
		},
		"an added step": func(p *model.Plan) {
			p.Steps = append(p.Steps, plannedStep(2, "tar", "bundle"))
		},
	}
	for what, mutate := range cases {
		p := samplePlan()
		mutate(&p)
		h, err := PlanHash(p, args)
		if err != nil {
			t.Fatalf("%s: %v", what, err)
		}
		if h == h0 {
			t.Fatalf("changing %s did not change the plan hash", what)
		}
	}
	if h, _ := PlanHash(base, map[string]any{"fence": false}); h == h0 {
		t.Fatal("changing an argument did not change the plan hash")
	}
	// What must NOT move it: a warning's wording, or when the doctor ran.
	p := samplePlan()
	p.Warnings = []string{"the last backup is 9 days old"}
	p.Doctor.GeneratedAt = "2026-09-14T11:22:33Z"
	if h, _ := PlanHash(p, args); h != h0 {
		t.Fatal("a warning or the doctor's timestamp moved the plan hash")
	}
}

// TestFingerprintDistinguishesRequests: the idempotency key's companion must
// separate two requests that differ ONLY in an argument — including a secret
// one, which is why it hashes the unredacted args.
func TestFingerprintDistinguishesRequests(t *testing.T) {
	hash := sha256Of([]byte("plan"))
	a, err := Fingerprint("key:ops", "key-mint", "dev", map[string]any{"label": "ops", "role": "admin"}, hash)
	if err != nil {
		t.Fatal(err)
	}
	same, _ := Fingerprint("key:ops", "key-mint", "dev", map[string]any{"role": "admin", "label": "ops"}, hash)
	if a != same {
		t.Fatal("argument order changed the fingerprint")
	}
	for _, other := range []struct {
		name string
		f    func() (string, error)
	}{
		{"a different role", func() (string, error) {
			return Fingerprint("key:ops", "key-mint", "dev", map[string]any{"label": "ops", "role": "user"}, hash)
		}},
		{"a different tenant", func() (string, error) {
			return Fingerprint("key:ops", "key-mint", "demo", map[string]any{"label": "ops", "role": "admin"}, hash)
		}},
		{"a different op", func() (string, error) {
			return Fingerprint("key:ops", "key-revoke", "dev", map[string]any{"label": "ops", "role": "admin"}, hash)
		}},
		{"a different plan", func() (string, error) {
			return Fingerprint("key:ops", "key-mint", "dev", map[string]any{"label": "ops", "role": "admin"}, sha256Of([]byte("other")))
		}},
	} {
		got, err := other.f()
		if err != nil {
			t.Fatal(err)
		}
		if got == a {
			t.Fatalf("%s produced the same fingerprint", other.name)
		}
	}
}

func TestCanonicalJSONSortsAtEveryDepth(t *testing.T) {
	a, err := canonicalJSON(map[string]any{
		"b": map[string]any{"z": 1, "a": 2},
		"a": []any{map[string]any{"y": 1, "x": 2}},
	})
	if err != nil {
		t.Fatal(err)
	}
	b, _ := canonicalJSON(map[string]any{
		"a": []any{map[string]any{"x": 2, "y": 1}},
		"b": map[string]any{"a": 2, "z": 1},
	})
	if string(a) != string(b) {
		t.Fatalf("canonical forms differ:\n%s\n%s", a, b)
	}
	if string(a) != `{"a":[{"x":2,"y":1}],"b":{"a":2,"z":1}}` {
		t.Fatalf("canonical form = %s", a)
	}
}
