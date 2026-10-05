package gowe

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// stubPython writes an executable that ignores its arguments, records argv
// to <dir>/argv, prints canned stdout and exits with rc.
func stubPython(t *testing.T, dir, stdoutDoc string, rc int) string {
	t.Helper()
	p := filepath.Join(dir, "python")
	script := "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$(dirname \"$0\")/argv\"\nprintf '%s' \"$PYTHONPATH\" > \"$(dirname \"$0\")/pythonpath\"\n" +
		"cat <<'EOF'\n" + stdoutDoc + "\nEOF\nexit " + strconv.Itoa(rc) + "\n"
	if err := os.WriteFile(p, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	return p
}

func okRecord(cwl, name, state string, problems []string) string {
	rec := map[string]any{
		"cwl": cwl, "text_sha256": strings.Repeat("a", 64), "tool_image": name,
		"verdict": map[string]any{
			"name": name, "state": state, "dirs": []string{"/store"}, "path": "/store/" + name,
			"exists": true, "found_in": []string{"/store"}, "receipt_found": true,
			"sha256": strings.Repeat("b", 64), "sha256_ok": len(problems) == 0,
			"labels": map[string]string{"org.ragstack.version": "v9.9.9"}, "labels_checked": true,
			"labels_ok": len(problems) == 0, "committed_receipt_ok": true,
			"problems": problems, "warnings": []string{},
		},
	}
	b, _ := json.Marshal(rec)
	return string(b)
}

func TestResolveReadsTheRowAndTheEnv(t *testing.T) {
	dir := t.TempDir()
	env := filepath.Join(dir, "tenant.env")
	if err := os.WriteFile(env, []byte("INGEST_BACKEND=gowe\nGOWE_WORKFLOW_CWL=/abs/pdf-ingest-scatter.cwl\nGRAPH_EXTRACT_CWL=cwl/custom.cwl\nGOWE_IMAGE_DIRS=/scout/containers/ragstack,/scout/containers/ragstack-dev\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	tn := &registry.Tenant{Name: "dev", Worktree: "/wt", PythonEnv: "/envs/x"}
	in, err := Resolve(tn, env)
	if err != nil {
		t.Fatal(err)
	}
	if in.Python != "/envs/x/bin/python" {
		t.Errorf("python = %q", in.Python)
	}
	if in.ImageDirs != "/scout/containers/ragstack,/scout/containers/ragstack-dev" || in.IngestBackend != "gowe" {
		t.Errorf("dirs/backend = %q/%q", in.ImageDirs, in.IngestBackend)
	}
	want := map[string]string{
		"GOWE_WORKFLOW_CWL":      "/abs/pdf-ingest-scatter.cwl",
		"GRAPH_EXTRACT_CWL":      "/wt/cwl/custom.cwl",             // relative → against the worktree
		"COLLECTION_RESTORE_CWL": "/wt/cwl/restore-collection.cwl", // unset → the repo copy
	}
	for _, w := range in.Workflows {
		if want[w.Key] != w.Path {
			t.Errorf("%s = %q, want %q", w.Key, w.Path, want[w.Key])
		}
	}
	args := in.Args()
	if args[0] != "-m" || args[1] != "ragstack.tool_image" || args[2] != "verify" || !contains(args, "--json") {
		t.Errorf("args = %v", args)
	}
	if !contains(args, "--dirs") || !contains(args, in.ImageDirs) {
		t.Errorf("args lack --dirs: %v", args)
	}
	// No python_env on the row → the shared default.
	tn.PythonEnv = ""
	in, _ = Resolve(tn, env)
	if in.Python != DefaultPythonEnv+"/bin/python" {
		t.Errorf("default python = %q", in.Python)
	}
	// No worktree is an error, not a run from "".
	if _, err := Resolve(&registry.Tenant{Name: "x"}, env); err == nil {
		t.Error("Resolve accepted a row without a worktree")
	}
}

func TestResolveSkipsAnUnsetIngestCWL(t *testing.T) {
	dir := t.TempDir()
	env := filepath.Join(dir, "tenant.env")
	if err := os.WriteFile(env, []byte("INGEST_BACKEND=local\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	in, err := Resolve(&registry.Tenant{Name: "dev", Worktree: "/wt"}, env)
	if err != nil {
		t.Fatal(err)
	}
	for _, w := range in.Workflows {
		if w.Key == "GOWE_WORKFLOW_CWL" && w.Path != "" {
			t.Errorf("GOWE_WORKFLOW_CWL has no default but resolved to %q", w.Path)
		}
	}
	if n := len(in.Args()); n != 6+2*2 {
		t.Errorf("args should carry two --cwl pairs: %v", in.Args())
	}
}

func TestRunParsesTheVerdictAndKeysItBack(t *testing.T) {
	dir := t.TempDir()
	doc := `{"ok": false, "records": [` +
		okRecord("/wt/cwl/a.cwl", "ragstack-tools-v9.9.9-b1.sif", "ok", nil) + "," +
		okRecord("/wt/cwl/b.cwl", "ragstack-tools-v9.9.9-b1.sif", "problem", []string{"sha256 mismatch: x"}) +
		`]}`
	py := stubPython(t, dir, doc, 1)
	in := &Input{
		Tenant: "dev", Worktree: dir, Python: py, IngestBackend: "gowe", ImageDirs: "/store",
		Workflows: []Workflow{
			{"GOWE_WORKFLOW_CWL", "/wt/cwl/a.cwl"},
			{"GRAPH_EXTRACT_CWL", ""},
			{"COLLECTION_RESTORE_CWL", "/wt/cwl/b.cwl"},
		},
	}
	var errb bytes.Buffer
	rep, err := Run(context.Background(), in, &errb)
	if err != nil {
		t.Fatalf("Run: %v (stderr %s)", err, errb.String())
	}
	if rep.OK {
		t.Error("a record with a problem must make the report not ok")
	}
	if len(rep.Workflows) != 2 || rep.Workflows[0].Key != "GOWE_WORKFLOW_CWL" || rep.Workflows[1].Key != "COLLECTION_RESTORE_CWL" {
		t.Errorf("workflows = %+v", rep.Workflows)
	}
	if len(rep.Skipped) != 1 || rep.Skipped[0] != "GRAPH_EXTRACT_CWL" {
		t.Errorf("skipped = %v", rep.Skipped)
	}
	if got := rep.Workflows[1].Verdict.Problems; len(got) != 1 || got[0] != "sha256 mismatch: x" {
		t.Errorf("problems = %v", got)
	}
	// The stub saw the documented command line and the tenant's package path.
	argv, _ := os.ReadFile(filepath.Join(dir, "argv"))
	if !strings.Contains(string(argv), "ragstack.tool_image\nverify\n--json\n--dirs\n/store\n--cwl\n/wt/cwl/a.cwl\n--cwl\n/wt/cwl/b.cwl") {
		t.Errorf("argv = %q", argv)
	}
	pp, _ := os.ReadFile(filepath.Join(dir, "pythonpath"))
	if string(pp) != filepath.Join(dir, "python") {
		t.Errorf("PYTHONPATH = %q", pp)
	}
	var out bytes.Buffer
	Print(&out, rep)
	s := out.String()
	for _, want := range []string{"GOWE_WORKFLOW_CWL: /wt/cwl/a.cwl", "text sha256 (GoWe would content-hash this): " + strings.Repeat("a", 64),
		"dockerPull: ragstack-tools-v9.9.9-b1.sif -> problem at /store/ragstack-tools-v9.9.9-b1.sif",
		"problem: sha256 mismatch: x", "GRAPH_EXTRACT_CWL: unset", "FAIL"} {
		if !strings.Contains(s, want) {
			t.Errorf("report lacks %q:\n%s", want, s)
		}
	}
}

func TestRunOKReport(t *testing.T) {
	dir := t.TempDir()
	doc := `{"ok": true, "records": [` + okRecord("/wt/cwl/a.cwl", "ragstack-tools-v9.9.9-b1.sif", "ok", nil) + `]}`
	py := stubPython(t, dir, doc, 0)
	in := &Input{Tenant: "dev", Worktree: dir, Python: py, IngestBackend: "gowe",
		Workflows: []Workflow{{"GOWE_WORKFLOW_CWL", "/wt/cwl/a.cwl"}}}
	rep, err := Run(context.Background(), in, nil)
	if err != nil || !rep.OK {
		t.Fatalf("err=%v ok=%v", err, rep != nil && rep.OK)
	}
	var out bytes.Buffer
	Print(&out, rep)
	if !strings.HasSuffix(strings.TrimSpace(out.String()), "ok") {
		t.Errorf("report should end in ok:\n%s", out.String())
	}
}

func TestRunErrorsWhenTheCheckDidNotRun(t *testing.T) {
	dir := t.TempDir()
	// Exit 2 with no JSON: usage error / import failure — the check did not run.
	py := stubPython(t, dir, "usage: python -m ragstack.tool_image", 2)
	in := &Input{Tenant: "dev", Worktree: dir, Python: py,
		Workflows: []Workflow{{"GOWE_WORKFLOW_CWL", "/wt/cwl/a.cwl"}}}
	if _, err := Run(context.Background(), in, nil); err == nil || !strings.Contains(err.Error(), "exited 2") {
		t.Errorf("err = %v", err)
	}
	// A record-count mismatch is a contract drift, not a verdict.
	py = stubPython(t, dir, `{"ok": true, "records": []}`, 0)
	in.Python = py
	if _, err := Run(context.Background(), in, nil); err == nil || !strings.Contains(err.Error(), "0 records for 1") {
		t.Errorf("err = %v", err)
	}
	// No interpreter.
	in.Python = filepath.Join(dir, "missing")
	if _, err := Run(context.Background(), in, nil); err == nil {
		t.Error("a missing interpreter must be an error")
	}
	// Nothing registered: nothing to run, trivially ok.
	in.Workflows = []Workflow{{"GOWE_WORKFLOW_CWL", ""}}
	rep, err := Run(context.Background(), in, nil)
	if err != nil || !rep.OK || len(rep.Skipped) != 1 {
		t.Errorf("rep=%+v err=%v", rep, err)
	}
}

func contains(xs []string, s string) bool {
	for _, x := range xs {
		if x == s {
			return true
		}
	}
	return false
}
