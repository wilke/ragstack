package ops

// The `set-ui-mode` plans, frozen BEFORE PR-F F5 factored the static UI's
// dist swap out of prepare.go into a step `update-code` reuses (brief §1.3
// step 4). The golden file was written by origin/main 93b00b3 (`go test
// ./internal/ctl/ops -run TestSetUIModePlansAreUnchanged
// -update-setuimode-golden`), and every plan below has to come out
// byte-identical: the whole model.Plan (steps, targets, warnings, previews,
// plan hash), the locks and the planned result.

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"os"
	"sort"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/registry"
)

var updateSetUIModeGolden = flag.Bool("update-setuimode-golden", false,
	"rewrite testdata/setuimode_plans.golden.json")

const setUIModeGoldenPath = "testdata/setuimode_plans.golden.json"

func TestSetUIModePlansAreUnchanged(t *testing.T) {
	cases := map[string]struct {
		mutate func(*registry.Tenant)
		args   map[string]any
	}{
		"static-from-dev": {devUI(8090), map[string]any{"mode": "static"}},
		"static-no-port":  {func(tn *registry.Tenant) { managed(tn); tn.UI.Port = 0 }, map[string]any{"mode": "static"}},
		"static-from-external": {func(tn *registry.Tenant) {
			managed(tn)
			tn.UI = registry.UI{Mode: registry.UIModeExternal, Port: 8091, Base: "/ragstack/dev/ui/"}
		}, map[string]any{"mode": "static"}},
		"external":        {devUI(8090), map[string]any{"mode": "external", "ui_port": 8092}},
		"static-instance": {instanceManaged, map[string]any{"mode": "static"}},
	}
	got := map[string]backupGoldenCase{}
	for name, c := range cases {
		oc, _ := fixture(t, "dev", c.mutate)
		op, _ := NewRegistry(testDeps(oc)).Lookup("set-ui-mode")
		p, err := op.Plan(context.Background(), oc, c.args)
		if err != nil {
			got[name] = backupGoldenCase{Error: err.Error()}
			continue
		}
		planJSON, err := json.Marshal(p.Plan)
		if err != nil {
			t.Fatal(err)
		}
		locks := make([]string, 0, len(p.Locks))
		for _, l := range p.Locks {
			locks = append(locks, string(l))
		}
		resJSON, err := json.Marshal(p.Result())
		if err != nil {
			t.Fatal(err)
		}
		sum := sha256.Sum256(planJSON)
		got[name] = backupGoldenCase{PlanSHA256: hex.EncodeToString(sum[:]), Steps: titles(p), Locks: locks,
			Result: resJSON}
	}

	if *updateSetUIModeGolden {
		body, err := json.MarshalIndent(got, "", " ")
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(setUIModeGoldenPath, append(body, '\n'), 0o644); err != nil {
			t.Fatal(err)
		}
		return
	}
	raw, err := os.ReadFile(setUIModeGoldenPath)
	if err != nil {
		t.Fatalf("reading the golden plans: %v", err)
	}
	var want map[string]backupGoldenCase
	if err := json.Unmarshal(raw, &want); err != nil {
		t.Fatal(err)
	}
	keys := make([]string, 0, len(want))
	for k := range want {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	if len(got) != len(want) {
		t.Errorf("%d cases planned, the golden file has %d", len(got), len(want))
	}
	for _, k := range keys {
		w, g := want[k], got[k]
		if w.Error != g.Error {
			t.Errorf("%s: error %q, golden %q", k, g.Error, w.Error)
			continue
		}
		if w.PlanSHA256 != g.PlanSHA256 {
			t.Errorf("%s: the plan changed (sha256 %s, golden %s); steps now:\n  %v", k, g.PlanSHA256, w.PlanSHA256,
				g.Steps)
		}
		var wr, gr bytes.Buffer
		_ = json.Compact(&wr, w.Result)
		_ = json.Compact(&gr, g.Result)
		if wr.String() != gr.String() {
			t.Errorf("%s: result %s, golden %s", k, g.Result, w.Result)
		}
		if len(w.Locks) != len(g.Locks) {
			t.Errorf("%s: locks %v, golden %v", k, g.Locks, w.Locks)
		}
	}
}
