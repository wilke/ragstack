package ops

// The WORKTREE-mode lifecycle plans, frozen at PR-F F4 (brief §1.2, §4: "one
// launch per mode"). F4 moved the API launch behind apiLaunchFor and put a mode
// switch inside startAPIProcess/stopAPIProcess; for a row with no
// server_image that change promised NO plan change. The golden file was
// written by the code BEFORE F4 (origin/main 1ad203d, `go test
// ./internal/ctl/ops -run TestWorktreeLifecyclePlansAreUnchanged
// -update-lifecycle-golden`), and every plan below — start, stop, restart, the
// two decommissions, create and restore --as, under both supervisors — has to
// come out byte-identical: the whole model.Plan (steps, targets, warnings,
// previews, plan hash), the locks and the planned result.

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

var updateLifecycleGolden = flag.Bool("update-lifecycle-golden", false,
	"rewrite testdata/lifecycle_plans.golden.json")

const lifecycleGoldenPath = "testdata/lifecycle_plans.golden.json"

func TestWorktreeLifecyclePlansAreUnchanged(t *testing.T) {
	backedUp := func(tn *registry.Tenant) {
		tn.LastBackup = &registry.BackupRecord{Bundle: "/rag/backups/tenants/" + tn.Name + "/20260914T080000Z-backup",
			At: "2026-09-14T08:00:00Z", Kind: "backup", Fenced: true, Verified: true, Scope: fullScope}
	}
	variants := map[string]func(*registry.Tenant){
		"systemd":  func(tn *registry.Tenant) { managed(tn); backedUp(tn) },
		"instance": func(tn *registry.Tenant) { instanceManaged(tn); backedUp(tn) },
	}
	type opCase struct {
		verb string
		args map[string]any
	}
	ops := map[string]opCase{
		"start":            {"start", nil},
		"start-api":        {"start", map[string]any{"only": []any{"api"}}},
		"stop":             {"stop", nil},
		"stop-force":       {"stop", map[string]any{"force": true}},
		"restart":          {"restart", nil},
		"decommission":     {"decommission", map[string]any{"archive": false}},
		"decommission-arc": {"decommission", map[string]any{}},
		"create-systemd":   {"create", map[string]any{"name": "golden", "artifact_id": testArtifactID}},
		"create-instance": {"create", map[string]any{"name": "golden", "artifact_id": testArtifactID,
			"supervisor": "instance", "postgres": "local"}},
		"restore-as": {"restore", map[string]any{"from": "20260914T080000Z-backup", "as": "golden"}},
	}
	got := map[string]backupGoldenCase{}
	for vname, mutate := range variants {
		for oname, c := range ops {
			oc, _ := fixture(t, "dev", mutate)
			d := testDeps(oc)
			d.Sealer = fakeSealer{fps: []string{"sha256:0123456789abcdef"}}
			op, _ := NewRegistry(d).Lookup(c.verb)
			key := vname + "/" + oname
			p, err := op.Plan(context.Background(), oc, c.args)
			if err != nil {
				got[key] = backupGoldenCase{Error: err.Error()}
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
			got[key] = backupGoldenCase{PlanSHA256: hex.EncodeToString(sum[:]), Steps: titles(p),
				Locks: locks, Result: resJSON}
		}
	}

	if *updateLifecycleGolden {
		body, err := json.MarshalIndent(got, "", " ")
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(lifecycleGoldenPath, append(body, '\n'), 0o644); err != nil {
			t.Fatal(err)
		}
		return
	}
	raw, err := os.ReadFile(lifecycleGoldenPath)
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
