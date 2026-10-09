package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/registry"
)

func TestGoweRenderArgsAndExit(t *testing.T) {
	dir := t.TempDir()
	reg := filepath.Join(dir, "registry.json")
	f := registry.LiveFixture()
	dev := f.Tenants["dev"]
	dev.DataDir = filepath.Join(dir, "data", "tenants", "dev")
	dev.Worktree = dir
	if err := registry.Save(reg, f, "test"); err != nil {
		t.Fatal(err)
	}
	cfg := filepath.Join(dev.DataDir, "config")
	if err := os.MkdirAll(cfg, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(cfg, "tenant.env"), []byte("INGEST_BACKEND=gowe\nGOWE_WORKFLOW_CWL=/wt/a.cwl\nGOWE_IMAGE_DIRS=/store\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	stub := func(doc string, rc string) string {
		p := filepath.Join(dir, "py-"+rc)
		script := "#!/bin/sh\ncat <<'EOF'\n" + doc + "\nEOF\nexit " + rc + "\n"
		if err := os.WriteFile(p, []byte(script), 0o755); err != nil {
			t.Fatal(err)
		}
		return p
	}
	rec := func(problems string) string {
		return `{"cwl":"/wt/a.cwl","text_sha256":"` + strings.Repeat("a", 64) + `","tool_image":"ragstack-tools-v9.9.9-b1.sif","verdict":{"name":"ragstack-tools-v9.9.9-b1.sif","state":"ok","dirs":["/store"],"path":"/store/ragstack-tools-v9.9.9-b1.sif","exists":true,"found_in":["/store"],"receipt_found":true,"sha256":null,"sha256_ok":true,"labels":null,"labels_checked":false,"labels_ok":null,"committed_receipt_ok":null,"problems":[` + problems + `],"warnings":[]}}`
	}

	// usage
	for _, args := range [][]string{{"gowe"}, {"gowe", "render"}, {"gowe", "bogus", "dev"}} {
		if rc, _, _ := capture(t, args...); rc != exitUsage {
			t.Errorf("%v: rc %d, want usage", args, rc)
		}
	}
	// Never /rag: every call names the temp root, and the row's data_dir is
	// where tenant.env is read from.
	base := []string{"gowe", "render", "dev", "--registry", reg, "--rag-root", dir}
	// unknown tenant
	if rc, _, errs := capture(t, "gowe", "render", "nope", "--registry", reg, "--rag-root", dir); rc != exitError || !strings.Contains(errs, "not in the registry") {
		t.Errorf("unknown tenant: rc %d %q", rc, errs)
	}
	// ok, text
	okPy := stub(`{"ok":true,"records":[`+rec("")+`,`+rec("")+`,`+rec("")+`]}`, "0")
	rc, out, errs := capture(t, append(base, "--python", okPy)...)
	if rc != exitOK {
		t.Fatalf("rc %d: %s", rc, errs)
	}
	for _, want := range []string{"GOWE_WORKFLOW_CWL: /wt/a.cwl", "dockerPull: ragstack-tools-v9.9.9-b1.sif -> ok", "image dirs:     /store"} {
		if !strings.Contains(out, want) {
			t.Errorf("out lacks %q:\n%s", want, out)
		}
	}
	// ok, --json carries the records keyed by setting
	rc, out, _ = capture(t, append(base, "--python", okPy, "--json")...)
	if rc != exitOK || !strings.Contains(out, `"key": "COLLECTION_RESTORE_CWL"`) || !strings.Contains(out, `"ok": true`) {
		t.Errorf("json: rc %d\n%s", rc, out)
	}
	// a problem → refused (3), the boot's decision
	badPy := stub(`{"ok":false,"records":[`+rec(`"sha256 mismatch: file x, receipt y"`)+`,`+rec("")+`,`+rec("")+`]}`, "1")
	rc, out, _ = capture(t, append(base, "--python", badPy)...)
	if rc != exitRefused || !strings.Contains(out, "problem: sha256 mismatch") || !strings.Contains(out, "FAIL") {
		t.Errorf("problem: rc %d\n%s", rc, out)
	}
	// #673 F3: a stamped name with no store dirs ("unchecked", Python exit 4)
	// is a refusal (3), not a pass — the boot refuses it too.
	unch := func(state, image string) string {
		return `{"cwl":"/wt/a.cwl","text_sha256":"` + strings.Repeat("a", 64) + `","tool_image":"` + image + `","verdict":{"name":"` + image + `","state":"` + state + `","dirs":[],"path":null,"exists":false,"found_in":[],"receipt_found":false,"sha256":null,"sha256_ok":null,"labels":null,"labels_checked":false,"labels_ok":null,"committed_receipt_ok":null,"problems":[],"warnings":[]}}`
	}
	s := unch("unchecked", "ragstack-tools-v9.9.9-b1.sif")
	uncheckedPy := stub(`{"ok":false,"unchecked":true,"records":[`+s+`,`+s+`,`+s+`]}`, "4")
	rc, out, errs = capture(t, append(base, "--python", uncheckedPy)...)
	if rc != exitRefused || !strings.Contains(out, "not verified: GOWE_IMAGE_DIRS unset") ||
		!strings.Contains(out, "-> unchecked") || strings.Contains(errs, "without a JSON verdict") {
		t.Errorf("unchecked: rc %d\n%s\n%s", rc, out, errs)
	}
	rc, out, _ = capture(t, append(base, "--python", uncheckedPy, "--json")...)
	if rc != exitRefused || !strings.Contains(out, `"ok": false`) || !strings.Contains(out, `"GOWE_WORKFLOW_CWL"`) {
		t.Errorf("unchecked json: rc %d\n%s", rc, out)
	}
	// ... while an UNSTAMPED tree with no dirs has nothing to verify: exit 0.
	u := unch("unstamped", "ragstack-worker.sif")
	unstampedPy := stub(`{"ok":true,"unchecked":false,"records":[`+u+`,`+u+`,`+u+`]}`, "0")
	if rc, out, errs := capture(t, append(base, "--python", unstampedPy)...); rc != exitOK || !strings.Contains(out, "\nok") {
		t.Errorf("unstamped: rc %d\n%s\n%s", rc, out, errs)
	}
	// the check could not run → error (1)
	deadPy := stub("Traceback: ModuleNotFoundError", "1")
	if rc, _, errs := capture(t, append(base, "--python", deadPy)...); rc != exitError || !strings.Contains(errs, "without a JSON verdict") {
		t.Errorf("dead: rc %d %q", rc, errs)
	}
}

// An image-mode row gets one note line: the check reads the worktree, which
// update-code keeps at the image's commit. A worktree row gets none.
func TestGoweRenderNotesAnImageModeRow(t *testing.T) {
	dir := t.TempDir()
	reg := filepath.Join(dir, "registry.json")
	f := registry.LiveFixture()
	dev := f.Tenants["dev"]
	dev.DataDir = filepath.Join(dir, "data", "tenants", "dev")
	dev.Worktree = dir
	if err := os.MkdirAll(filepath.Join(dev.DataDir, "config"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dev.DataDir, "config", "tenant.env"),
		[]byte("INGEST_BACKEND=gowe\nGOWE_WORKFLOW_CWL=/wt/a.cwl\nGOWE_IMAGE_DIRS=/store\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	py := filepath.Join(dir, "py")
	if err := os.WriteFile(py, []byte("#!/bin/sh\necho '{\"ok\":true,\"records\":[]}'\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	argv := []string{"gowe", "render", "dev", "--registry", reg, "--rag-root", dir, "--python", py}
	if err := registry.Save(reg, f, "test"); err != nil {
		t.Fatal(err)
	}
	if _, out, _ := capture(t, argv...); strings.Contains(out, "server image") {
		t.Errorf("a worktree row got the image note:\n%s", out)
	}
	commit := strings.Repeat("ab", 20)
	dev.ServerImage = &registry.ServerImage{Name: "ragstack-server-v1.6.6-b1.sif", Version: "v1.6.6", Commit: commit,
		Build: 1, SHA256: strings.Repeat("0", 64), Path: "/rag/data/ctl/images/server/ragstack-server-v1.6.6-b1.sif"}
	dev.Code = registry.Code{Tag: "v1.6.6", SHA: registry.NullString(commit)}
	if err := registry.Save(reg, f, "test"); err != nil {
		t.Fatal(err)
	}
	_, out, _ := capture(t, argv...)
	if !strings.Contains(out, "note: dev runs its API from server image ragstack-server-v1.6.6-b1.sif") ||
		!strings.Contains(out, "keeps at the image's commit "+commit) {
		t.Errorf("no image-mode note:\n%s", out)
	}
	_, out, errs := capture(t, append(argv, "--json")...)
	if strings.Contains(out, "note:") || !strings.Contains(errs, "note: dev runs its API") {
		t.Errorf("--json: the note belongs on stderr (out %q, err %q)", out, errs)
	}
}
