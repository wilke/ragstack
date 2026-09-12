package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// liveProxyFixture is the captured proxy tree. Tests read it; nothing here
// writes into it, and nothing here touches /rag/config/proxy. The path is
// made absolute because every path the renderers accept is (paths.SafePath).
func liveProxyFixture(t *testing.T) string {
	t.Helper()
	p, err := filepath.Abs("../../internal/ctl/testdata/live-2026-09-10/proxy")
	if err != nil {
		t.Fatal(err)
	}
	return p
}

func scratchRegistry(t *testing.T) (dir, reg string) {
	t.Helper()
	dir = t.TempDir()
	reg = filepath.Join(dir, "registry.json")
	if err := registry.Save(reg, registry.LiveFixture(), "test"); err != nil {
		t.Fatal(err)
	}
	return dir, reg
}

// `render nginx static` used to print the tenant MAPS: the positional was
// parsed and then ignored, so the command silently answered a different
// question than the one asked.
func TestRenderNginxPositionalSelectsTheKind(t *testing.T) {
	_, reg := scratchRegistry(t)

	rc, out, errs := capture(t, "render", "nginx", "static", "--registry", reg)
	if rc != exitOK || !strings.HasPrefix(out, "# tenants-ui-static.generated.conf") {
		t.Errorf("positional `static`: rc %d %q %s", rc, first(out), errs)
	}
	rc, out, _ = capture(t, "render", "nginx", "tenants", "--registry", reg)
	if rc != exitOK || !strings.HasPrefix(out, "# 05-tenants.generated.conf") {
		t.Errorf("positional `tenants`: rc %d %q", rc, first(out))
	}
	rc, out, _ = capture(t, "render", "nginx", "--registry", reg)
	if rc != exitOK || !strings.HasPrefix(out, "# 05-tenants.generated.conf") {
		t.Errorf("no positional still defaults to tenants: rc %d %q", rc, first(out))
	}
	if rc, _, errs := capture(t, "render", "nginx", "bogus", "--registry", reg); rc != exitError ||
		!strings.Contains(errs, `unknown nginx kind "bogus"`) {
		t.Errorf("an unknown kind must fail loudly: rc %d %s", rc, errs)
	}
	if rc, _, errs := capture(t, "render", "nginx", "static", "--kind", "tenants", "--registry", reg); rc != exitError ||
		!strings.Contains(errs, "disagree") {
		t.Errorf("a positional contradicting --kind must fail: rc %d %s", rc, errs)
	}
}

func first(s string) string {
	if i := strings.IndexByte(s, '\n'); i > 0 {
		return s[:i]
	}
	return s
}

func TestGatewayUsage(t *testing.T) {
	if rc, _, _ := capture(t, "gateway"); rc != exitUsage {
		t.Errorf("bare `gateway` rc %d", rc)
	}
	if rc, _, errs := capture(t, "gateway", "bogus"); rc != exitUsage || !strings.Contains(errs, "unknown verb") {
		t.Errorf("unknown verb rc %d %s", rc, errs)
	}
	if rc, _, _ := capture(t, "gateway", "help"); rc != exitOK {
		t.Error("gateway help")
	}
}

func TestGatewayRenderWritesTheGeneration(t *testing.T) {
	dir, reg := scratchRegistry(t)
	out := filepath.Join(dir, "gen")
	rc, stdoutText, errs := capture(t, "gateway", "render", "--registry", reg, "--rag-root", dir, "--out", out)
	if rc != exitOK {
		t.Fatalf("rc %d %s", rc, errs)
	}
	for _, rel := range []string{"conf.d/05-tenants.generated.conf", "snippets/tenants-ui-static.generated.conf", "manifest.json"} {
		if _, err := os.Stat(filepath.Join(out, rel)); err != nil {
			t.Errorf("%s: %v", rel, err)
		}
	}
	if !strings.Contains(stdoutText, "generation 1 from registry generation") {
		t.Errorf("summary: %s", stdoutText)
	}
	// The rendered maps must carry the live ports — this is the file that
	// replaces the hand-written ones.
	b, err := os.ReadFile(filepath.Join(out, "conf.d/05-tenants.generated.conf"))
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{`dev         "127.0.0.1:24040";`, `registry sha256:     sha256:`} {
		if !strings.Contains(string(b), want) {
			t.Errorf("rendered file lacks %q", want)
		}
	}
}

// `gateway diff` with nothing published compares against the LIVE hand-written
// maps and must report the PR-B semantic no-op.
func TestGatewayDiffReportsTheSemanticNoop(t *testing.T) {
	dir, reg := scratchRegistry(t)
	rc, out, errs := capture(t, "gateway", "diff", "--registry", reg, "--rag-root", dir, "--proxy-dir", liveProxyFixture(t))
	if rc != exitOK {
		t.Fatalf("rc %d %s", rc, errs)
	}
	if !strings.Contains(out, "semantic_noop: true") {
		t.Errorf("diff does not report the no-op:\n%s", out)
	}
	if !strings.Contains(out, "00-maps.conf") {
		t.Errorf("diff does not say what it compared against:\n%s", out)
	}

	rc, jsonOut, _ := capture(t, "--json", "gateway", "diff", "--registry", reg, "--rag-root", dir, "--proxy-dir", liveProxyFixture(t))
	if rc != exitOK {
		t.Fatalf("json rc %d", rc)
	}
	var doc struct {
		SemanticNoop bool `json:"semantic_noop"`
		Render       struct {
			Generation int    `json:"generation"`
			Changed    bool   `json:"changed"`
			SHA256     string `json:"sha256"`
			Files      []struct {
				Path string `json:"path"`
			} `json:"files"`
		} `json:"render"`
	}
	if err := json.Unmarshal([]byte(jsonOut), &doc); err != nil {
		t.Fatalf("not JSON: %v\n%s", err, jsonOut)
	}
	if !doc.SemanticNoop || doc.Render.Generation != 1 || !doc.Render.Changed || len(doc.Render.Files) != 2 {
		t.Errorf("json diff: %+v", doc)
	}
	if !strings.HasPrefix(doc.Render.SHA256, "sha256:") {
		t.Errorf("sha256 %q", doc.Render.SHA256)
	}
}

func TestGatewayStatusBeforeAnyPublish(t *testing.T) {
	dir, reg := scratchRegistry(t)
	rc, out, errs := capture(t, "--json", "gateway", "status", "--registry", reg, "--rag-root", dir, "--proxy-dir", liveProxyFixture(t))
	if rc != exitOK {
		t.Fatalf("rc %d %s", rc, errs)
	}
	var s struct {
		Generation  int    `json:"generation"`
		TxnState    string `json:"txn_state"`
		PendingDiff bool   `json:"pending_diff"`
		Routes      []struct {
			Name   string `json:"name"`
			Status string `json:"status"`
		} `json:"routes"`
		LegacyRoutes []struct {
			Name string `json:"name"`
		} `json:"legacy_routes"`
	}
	if err := json.Unmarshal([]byte(out), &s); err != nil {
		t.Fatalf("not JSON: %v\n%s", err, out)
	}
	if s.Generation != 0 || s.TxnState != "none" || !s.PendingDiff {
		t.Errorf("status %+v", s)
	}
	if len(s.Routes) != 4 || s.Routes[0].Name != "dev" || s.Routes[0].Status != "active" {
		t.Errorf("routes %+v", s.Routes)
	}
	if len(s.LegacyRoutes) != 2 {
		t.Errorf("legacy routes %+v", s.LegacyRoutes)
	}

	// The human form says the same thing.
	rc, text, _ := capture(t, "gateway", "status", "--registry", reg, "--rag-root", dir, "--proxy-dir", liveProxyFixture(t))
	if rc != exitOK || !strings.Contains(text, "txn state:           none") {
		t.Errorf("text status: rc %d\n%s", rc, text)
	}
}

func TestGatewayRollbackRefusesWithNothingPublished(t *testing.T) {
	dir, reg := scratchRegistry(t)
	rc, _, errs := capture(t, "gateway", "rollback", "--yes", "--registry", reg, "--rag-root", dir, "--proxy-dir", liveProxyFixture(t))
	if rc != exitRefused {
		t.Fatalf("rc %d (want %d refused) %s", rc, exitRefused, errs)
	}
	if !strings.Contains(errs, "no previous generation") {
		t.Errorf("refusal: %s", errs)
	}
}

func TestGatewayRepairWithNothingToRepair(t *testing.T) {
	dir, reg := scratchRegistry(t)
	rc, out, errs := capture(t, "gateway", "repair", "--registry", reg, "--rag-root", dir, "--proxy-dir", liveProxyFixture(t))
	if rc != exitOK {
		t.Fatalf("rc %d %s", rc, errs)
	}
	if !strings.Contains(out, "nothing to repair") {
		t.Errorf("out: %s", out)
	}
}

// `--json` promises a document, not a document with a banner in front of it.
// The dry-run banner and the `[y/N]` prompt both went to stdout unconditionally,
// so every scripted `gateway apply --json` got unparseable output — and in the
// no-`--yes` case the prompt was written to a terminal that was not there and
// the answer read off a stdin nobody was typing into.
func TestGatewayApplyJSONOutputIsOnlyJSON(t *testing.T) {
	dir, reg := scratchRegistry(t)
	common := []string{"--registry", reg, "--rag-root", dir,
		"--proxy-dir", liveProxyFixture(t), "--state-dir", filepath.Join(dir, "state")}

	// Without --yes: a refusal stated AS JSON, exit 3, nothing published.
	stdin = strings.NewReader("y\n") // would be accepted if it were ever read
	defer func() { stdin = os.Stdin }()
	rc, out, _ := capture(t, append([]string{"--json", "gateway", "apply"}, common...)...)
	if rc != exitRefused {
		t.Fatalf("rc %d, want %d refused\n%s", rc, exitRefused, out)
	}
	var doc map[string]any
	if err := json.Unmarshal([]byte(out), &doc); err != nil {
		t.Fatalf("--json emitted something that is not JSON: %v\n%q", err, out)
	}
	errObj, _ := doc["error"].(map[string]any)
	if errObj == nil || errObj["code"] != "refused" {
		t.Errorf("the refusal is not a JSON error document: %v", doc)
	}
	if detail, _ := errObj["detail"].(string); !strings.Contains(detail, "--yes") {
		t.Errorf("the refusal does not name the remedy: %v", errObj)
	}
	if _, err := os.Stat(filepath.Join(dir, "state", "gateway", "gen-1")); err == nil {
		t.Error("a refused --json apply published a generation")
	}

	// --dry-run --json: one JSON document, no banner.
	rc, out, _ = capture(t, append([]string{"--json", "gateway", "apply", "--dry-run"}, common...)...)
	if strings.Contains(out, "--dry-run:") || strings.Contains(out, "plan:") {
		t.Errorf("--json printed the human banner:\n%s", out)
	}
	// The apply itself fails on this host (no apptainer), which is fine: what
	// matters is that whatever it printed is a single JSON document.
	var res map[string]any
	if err := json.Unmarshal([]byte(out), &res); err != nil {
		t.Fatalf("--json --dry-run emitted something that is not JSON (rc %d): %v\n%q", rc, err, out)
	}
	if _, ok := res["steps"]; !ok {
		t.Errorf("the JSON document is not a publish result: %v", res)
	}
}

// The four golden bodies are the PR-B go/no-go. Skipping them is allowed —
// there is no golden set on a host that has not installed one — but a publish
// that did not run its own acceptance test must say so where the operator
// cannot miss it.
func TestApplyWarnsWithoutGoldenBodies(t *testing.T) {
	dir, reg := scratchRegistry(t)
	stdin = strings.NewReader("n\n")
	defer func() { stdin = os.Stdin }()
	rc, out, _ := capture(t, "gateway", "apply",
		"--registry", reg, "--rag-root", dir, "--proxy-dir", liveProxyFixture(t),
		"--state-dir", filepath.Join(dir, "state"))
	if rc != exitRefused {
		t.Fatalf("rc %d", rc)
	}
	// In the PLAN, before the prompt: the operator has to be able to answer `n`
	// because of it.
	for _, want := range []string{"--expect-bodies was NOT passed", "are NOT compared", "/rag/data/ctl/goldens"} {
		if !strings.Contains(out, want) {
			t.Errorf("the plan does not warn that the go/no-go is skipped (%q):\n%s", want, out)
		}
	}
	if i, j := strings.Index(out, "--expect-bodies was NOT passed"), strings.Index(out, "[y/N]"); i < 0 || j < 0 || i > j {
		t.Errorf("the warning comes after the prompt, where it cannot change the answer:\n%s", out)
	}
	// With a golden set it says what it compares instead, and does not warn.
	rc, out, _ = capture(t, "gateway", "apply",
		"--registry", reg, "--rag-root", dir, "--proxy-dir", liveProxyFixture(t),
		"--state-dir", filepath.Join(dir, "state2"),
		"--expect-bodies", "../../internal/ctl/testdata/live-2026-09-10/gateway")
	if rc != exitRefused {
		t.Fatalf("rc %d\n%s", rc, out)
	}
	if strings.Contains(out, "--expect-bodies was NOT passed") {
		t.Errorf("warned although the goldens were passed:\n%s", out)
	}
	if !strings.Contains(out, "4 bodies compared byte-for-byte") {
		t.Errorf("the plan does not say what is compared:\n%s", out)
	}
}

// `render nginx` and `gateway render` render the same two files and must agree
// about the proxy tree those files NAME. `render nginx` had no --proxy-dir at
// all, so it silently emitted the deployed tree's paths while
// `gateway render --proxy-dir <checkout>` emitted the checkout's — a diff
// against a branch that reads clean and then fails on the host.
func TestRenderNginxAndGatewayRenderAgreeOnTheProxyDir(t *testing.T) {
	// A static UI and the admin mount are what make the snippet carry
	// `include <ProxyDir>/snippets/…` lines at all.
	f := registry.LiveFixture()
	f.Ctl.GatewayEnabled = true
	f.Tenants["demo"].UI.Mode = registry.UIModeStatic
	dir := t.TempDir()
	reg := filepath.Join(dir, "registry.json")
	if err := registry.Save(reg, f, "test"); err != nil {
		t.Fatal(err)
	}
	checkout := filepath.Join(dir, "coconut-proxy")

	rc, direct, errs := capture(t, "render", "nginx", "static", "--registry", reg, "--rag-root", dir, "--proxy-dir", checkout)
	if rc != exitOK {
		t.Fatalf("render nginx: rc %d %s", rc, errs)
	}
	if !strings.Contains(direct, filepath.Join(checkout, "snippets", "cors.conf")) {
		t.Errorf("`render nginx --proxy-dir` did not use the directory it was given:\n%s", direct)
	}

	out := filepath.Join(dir, "gen")
	rc, _, errs = capture(t, "gateway", "render", "--registry", reg, "--rag-root", dir, "--proxy-dir", checkout, "--out", out)
	if rc != exitOK {
		t.Fatalf("gateway render: rc %d %s", rc, errs)
	}
	b, err := os.ReadFile(filepath.Join(out, "snippets", "tenants-ui-static.generated.conf"))
	if err != nil {
		t.Fatal(err)
	}
	// `gateway render` adds a provenance header; below it the two must be the
	// same bytes.
	if got := gatewayBody(b); got != direct {
		t.Errorf("the two renderers disagree:\n gateway render:\n%s\n render nginx:\n%s", got, direct)
	}
	// And the usage names the positional form the parser actually implements.
	if _, usage, _ := capture(t, "help"); !strings.Contains(usage, "render nginx tenants|static") {
		if _, _, errUsage := capture(t, "help"); !strings.Contains(errUsage, "render nginx tenants|static") {
			t.Errorf("usage still documents the old --kind-only form")
		}
	}
}

// gatewayBody is everything after the ctl's provenance header block.
func gatewayBody(b []byte) string {
	const end = "# ragstack-ctl gateway generation header — end\n"
	if i := strings.Index(string(b), end); i >= 0 {
		return string(b)[i+len(end):]
	}
	return string(b)
}

// An apply without --yes and without a confirmation on stdin publishes
// nothing: the refusal is exit 3, and the state dir stays empty.
func TestGatewayApplyRefusedAtThePrompt(t *testing.T) {
	dir, reg := scratchRegistry(t)
	stdin = strings.NewReader("n\n")
	defer func() { stdin = os.Stdin }()
	rc, out, errs := capture(t, "gateway", "apply",
		"--registry", reg, "--rag-root", dir, "--proxy-dir", liveProxyFixture(t), "--state-dir", filepath.Join(dir, "state"))
	if rc != exitRefused {
		t.Fatalf("rc %d %s", rc, errs)
	}
	if !strings.Contains(out, "plan:") || !strings.Contains(out, "semantic_noop: true") {
		t.Errorf("the plan was not printed before the prompt:\n%s", out)
	}
	if _, err := os.Stat(filepath.Join(dir, "state", "gateway")); err == nil {
		t.Error("a refused apply created gateway state")
	}
}
