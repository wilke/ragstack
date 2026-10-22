package api

import (
	"encoding/json"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"testing"
)

// deepHealthLeakRE is the conformance suite's leak regex for a non-admin deep
// health body: nothing that names a backend, a port, a latency or the checks.
var deepHealthLeakRE = regexp.MustCompile(`(?i)(qdrant|elasticsearch|neo4j|postgres|sqlite|localhost|:\d{4}|latency_ms|checks)`)

func TestHealthDeepWithoutAKeyOnAKeyedServerIs401(t *testing.T) {
	srv := newGradingTestServer(t)
	code, body := do(t, srv, http.MethodGet, "/v1/health/deep", "", nil)
	if code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401: %s", code, body)
	}
}

func TestHealthDeepForAUserIs403AndLeaksNothing(t *testing.T) {
	srv := newGradingTestServer(t)
	code, body := do(t, srv, http.MethodGet, "/v1/health/deep", keyReaderA, nil)
	if code != http.StatusForbidden {
		t.Fatalf("status = %d, want 403: %s", code, body)
	}
	if m := deepHealthLeakRE.Find(body); m != nil {
		t.Fatalf("403 body leaks %q: %s", m, body)
	}
}

func TestHealthDeepForAnAdminReportsEveryCheckDisabled(t *testing.T) {
	srv := newGradingTestServer(t)
	code, body := do(t, srv, http.MethodGet, "/v1/health/deep", keyAdmin, nil)
	if code != http.StatusOK {
		t.Fatalf("status = %d, want 200: %s", code, body)
	}
	var resp struct {
		Status string           `json:"status"`
		Checks []map[string]any `json:"checks"`
	}
	decodeInto(t, body, &resp)
	if resp.Status != "ok" {
		t.Errorf("status = %q, want ok", resp.Status)
	}
	var names []string
	for _, c := range resp.Checks {
		names = append(names, c["name"].(string))
		if c["ok"] != true || c["state"] != "disabled" {
			t.Errorf("check %v: want ok:true state:disabled", c)
		}
	}
	sort.Strings(names)
	want := []string{"doi_resolver", "embedder", "graph", "ingest_engine", "jobstore", "llm", "reranker", "text", "vector"}
	if !reflect.DeepEqual(names, want) {
		t.Errorf("check names = %v, want %v", names, want)
	}
}

// TestHealthDeepValidatesAgainstContract runs the admin body through
// deep_health_response.json, which is what the conformance suite does over
// HTTP. Same shell-out pattern as internal/ctl/fleet's contract test.
func TestHealthDeepValidatesAgainstContract(t *testing.T) {
	schemas := "../../../contracts/schemas"
	if _, err := os.Stat(filepath.Join(schemas, "deep_health_response.json")); err != nil {
		t.Skip("contract schemas not present")
	}
	py := ""
	for _, c := range []string{"/rag/envs/ragstack/bin/python", "python3"} {
		if p, err := exec.LookPath(c); err == nil {
			if exec.Command(p, "-c", "import jsonschema, referencing").Run() == nil {
				py = p
				break
			}
		}
	}
	if py == "" {
		t.Skip("no python with jsonschema + referencing")
	}
	srv := newGradingTestServer(t)
	code, body := do(t, srv, http.MethodGet, "/v1/health/deep", keyAdmin, nil)
	if code != http.StatusOK {
		t.Fatalf("status = %d: %s", code, body)
	}
	doc := filepath.Join(t.TempDir(), "deep.json")
	if err := os.WriteFile(doc, body, 0o600); err != nil {
		t.Fatal(err)
	}
	script := `
import glob, json, os, sys
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
reg, schemas = Registry(), {}
for p in glob.glob(os.path.join(sys.argv[1], "*.json")):
    s = json.load(open(p)); schemas[os.path.basename(p)[:-5]] = s
    if "$id" in s:
        reg = reg.with_resource(s["$id"], Resource.from_contents(s))
errs = sorted(Draft202012Validator(schemas["deep_health_response"], registry=reg).iter_errors(json.load(open(sys.argv[2]))),
              key=lambda e: list(e.absolute_path))
for e in errs[:20]:
    print("/" + "/".join(str(p) for p in e.absolute_path), "->", e.message[:200])
sys.exit(1 if errs else 0)
`
	if out, err := exec.Command(py, "-c", script, schemas, doc).CombinedOutput(); err != nil {
		t.Fatalf("deep health body does not validate:\n%s\n%s", out, body)
	}
}

func TestDegradedIsOmittedWhenEmptyAndEncodedWhenSet(t *testing.T) {
	for name, v := range map[string]any{
		"query":    QueryResponse{},
		"retrieve": RetrieveResponse{},
	} {
		b, err := json.Marshal(v)
		if err != nil {
			t.Fatal(err)
		}
		if strings.Contains(string(b), "degraded") {
			t.Errorf("%s: empty Degraded encoded: %s", name, b)
		}
	}
	entry := []DegradedEntry{{Component: "rerank", Reason: "ConnectError: refused"}}
	want := `"degraded":[{"component":"rerank","reason":"ConnectError: refused"}]`
	for name, v := range map[string]any{
		"query":    QueryResponse{Degraded: entry},
		"retrieve": RetrieveResponse{Degraded: entry},
	} {
		b, err := json.Marshal(v)
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(string(b), want) {
			t.Errorf("%s: got %s, want it to contain %s", name, b, want)
		}
	}
}
