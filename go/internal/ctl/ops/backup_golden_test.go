package ops

// The backup plan, frozen. PR-G1.3 extracted planBackup's body into
// addBackupSteps so that `decommission --archive` can plan the same bundle
// inside its own job; that refactor promised NO behaviour change for
// `backup`, and this test is the promise. The golden file was written by the
// code BEFORE the refactor (`go test ./internal/ctl/ops -run
// TestBackupPlansAreUnchanged -update-backup-golden`), and every plan of every
// fixture tenant, in every argument shape, has to come out byte-identical:
// the full model.Plan (steps, targets, warnings, would_write previews and the
// plan hash), the locks and the planned result.

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"os"
	"path/filepath"
	"sort"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/registry"
)

var updateBackupGolden = flag.Bool("update-backup-golden", false, "rewrite testdata/backup_plans.golden.json")

const backupGoldenPath = "testdata/backup_plans.golden.json"

// backupGoldenCase is one recorded plan (or refusal).
//
// The plan itself is recorded as the sha256 of its JSON (the whole document:
// every step, target, warning, would_write preview and the plan hash), so the
// golden file does not carry four megabytes of plans; a failure prints the
// fresh plan's step titles.
type backupGoldenCase struct {
	PlanSHA256 string          `json:"plan_sha256,omitempty"`
	Steps      []string        `json:"-"`
	Locks      []string        `json:"locks,omitempty"`
	Result     json.RawMessage `json:"result,omitempty"`
	Error      string          `json:"error,omitempty"`
}

func TestBackupPlansAreUnchanged(t *testing.T) {
	variants := map[string]func(*registry.Tenant){
		"as-recorded": nil,
		"managed":     managed,
		"instance": func(tn *registry.Tenant) {
			managed(tn)
			tn.Supervisor = "instance"
		},
		"rollback-descriptor-less": func(tn *registry.Tenant) {
			managed(tn)
			tn.RollbackDescriptor = nil
		},
	}
	argSets := map[string]map[string]any{
		"none":         nil,
		"fence":        {"fence": true},
		"fence-tar":    {"fence": true, "tar": true},
		"light":        {"scope": []any{"config", "state"}},
		"secrets-skip": {"fence": true, "secrets": "skip"},
		"secrets-req":  {"fence": true, "secrets": "require"},
		"fence-light":  {"fence": true, "scope": []any{"config", "state"}},
		"config-only":  {"scope": []any{"config"}},
		"no-config":    {"scope": []any{"state", "stores"}},
		"tar-unfenced": {"tar": true},
	}
	sealers := map[string]Sealer{"no-sealer": nil, "sealer": fakeSealer{fps: []string{"sha256:0123456789abcdef"}}}

	got := map[string]backupGoldenCase{}
	for _, name := range registry.LiveFixture().DisplayOrder {
		for vname, mutate := range variants {
			for aname, args := range argSets {
				for sname, sealer := range sealers {
					oc, _ := fixture(t, name, mutate)
					d := testDeps(oc)
					d.Sealer = sealer
					op, _ := NewRegistry(d).Lookup("backup")
					key := name + "/" + vname + "/" + aname + "/" + sname
					p, err := op.Plan(context.Background(), oc, args)
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
		}
	}

	if *updateBackupGolden {
		body, err := json.MarshalIndent(got, "", " ")
		if err != nil {
			t.Fatal(err)
		}
		if err := os.MkdirAll(filepath.Dir(backupGoldenPath), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(backupGoldenPath, append(body, '\n'), 0o644); err != nil {
			t.Fatal(err)
		}
		return
	}
	raw, err := os.ReadFile(backupGoldenPath)
	if err != nil {
		t.Fatalf("reading the golden plans (regenerate ONLY from code whose backup behaviour is meant to change): %v", err)
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
			t.Errorf("%s: the plan moved (sha256 %s, golden %s); its steps now: %v", k,
				g.PlanSHA256, w.PlanSHA256, g.Steps)
		}
		if !jsonEqual(t, w.Result, g.Result) {
			t.Errorf("%s: the planned result moved:\n got %s\nwant %s", k, g.Result, w.Result)
		}
		if len(w.Locks) != len(g.Locks) {
			t.Errorf("%s: locks %v, golden %v", k, g.Locks, w.Locks)
			continue
		}
		for i := range w.Locks {
			if w.Locks[i] != g.Locks[i] {
				t.Errorf("%s: locks %v, golden %v", k, g.Locks, w.Locks)
				break
			}
		}
	}
}

// jsonEqual compares two JSON documents after a canonical round trip (the
// golden file is indented; the fresh marshal is compact).
func jsonEqual(t *testing.T, a, b json.RawMessage) bool {
	t.Helper()
	if len(a) == 0 || len(b) == 0 {
		return len(a) == len(b)
	}
	var x, y any
	if err := json.Unmarshal(a, &x); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(b, &y); err != nil {
		t.Fatal(err)
	}
	cx, _ := json.Marshal(x)
	cy, _ := json.Marshal(y)
	return string(cx) == string(cy)
}
