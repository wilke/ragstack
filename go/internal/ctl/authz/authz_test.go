package authz

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/auth"
)

const contractPath = "../../../../contracts/ctl/openapi.yaml"

// TestMatrixIsRegeneratedFromTheContract is the drift alarm. The committed
// matrix_gen.go is a security control derived from the contract; regenerating
// it here and diffing is what stops the two from parting company when someone
// edits the YAML and forgets the `go run`.
//
// Skips (loudly) when no interpreter with PyYAML is available — the generator
// shells out to one at build time by design, and a developer machine without
// the conda env must still be able to run `go test ./...`.
func TestMatrixIsRegeneratedFromTheContract(t *testing.T) {
	python := findPython(t)
	if python == "" {
		t.Skip("no interpreter with PyYAML found (tried /rag/envs/ragstack/bin/python, python3); the matrix diff needs one")
	}
	dir := t.TempDir()
	out := filepath.Join(dir, "matrix_gen.go")
	cmd := exec.Command("go", "run", "./gen", "-contract", contractPath, "-out", out, "-python", python)
	cmd.Dir = "."
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		t.Fatalf("regenerate: %v: %s", err, stderr.String())
	}
	got, err := os.ReadFile(out)
	if err != nil {
		t.Fatal(err)
	}
	want, err := os.ReadFile("matrix_gen.go")
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, want) {
		t.Fatalf("matrix_gen.go is stale: regenerate with\n"+
			"  cd go && go run ./internal/ctl/authz/gen -contract ../contracts/ctl/openapi.yaml -out internal/ctl/authz/matrix_gen.go\n"+
			"got %d bytes, committed %d bytes", len(got), len(want))
	}
}

func findPython(t *testing.T) string {
	t.Helper()
	candidates := []string{"/rag/envs/ragstack/bin/python", "python3"}
	for _, c := range candidates {
		path, err := exec.LookPath(c)
		if err != nil {
			continue
		}
		if err := exec.Command(path, "-c", "import yaml").Run(); err == nil {
			return path
		}
	}
	return ""
}

func TestDenyByDefault(t *testing.T) {
	// A route nobody put in the contract is refused for every role. Adding a
	// handler without adding the operation is then a 403, not a publication.
	for _, role := range []string{"", auth.RoleViewer, auth.RoleOperator} {
		if Allowed("GET", "/v1/not-in-the-contract", role) {
			t.Fatalf("an unknown route was allowed for role %q", role)
		}
	}
	if SessionAllowed("GET", "/v1/not-in-the-contract") {
		t.Fatal("an unknown route accepted a session")
	}
	if IsAnonymous("GET", "/v1/not-in-the-contract") {
		t.Fatal("an unknown route was treated as anonymous")
	}
}

func TestKnownOperations(t *testing.T) {
	cases := []struct {
		method, path, role string
		want               bool
	}{
		{"GET", "/health", "", true},                               // anonymous
		{"GET", "/v1/fleet", auth.RoleViewer, true},                // viewer read
		{"GET", "/v1/fleet", auth.RoleOperator, true},              // operator ⊃ viewer
		{"GET", "/v1/fleet", "", false},                            // no role at all
		{"GET", "/v1/audit", auth.RoleViewer, false},               // operator-only read
		{"GET", "/v1/audit", auth.RoleOperator, true},              //
		{"GET", "/v1/tenants/{name}", auth.RoleViewer, true},       // path parameters are not resolved here
		{"GET", "/v1/tenants/{name}/logs", auth.RoleViewer, false}, // raw logs are operator-only
		{"POST", "/v1/tenants/{name}/ops/{verb}", auth.RoleViewer, false},
		{"POST", "/v1/tenants/{name}/ops/{verb}", auth.RoleOperator, true},
		{"POST", "/v1/gateway/render", auth.RoleViewer, true}, // a READ despite the verb
		{"POST", "/v1/gateway/apply", auth.RoleViewer, false},
		{"get", "/v1/fleet", auth.RoleViewer, true}, // method compared case-insensitively
	}
	for _, c := range cases {
		if got := Allowed(c.method, c.path, c.role); got != c.want {
			t.Errorf("Allowed(%s %s, %q) = %v; want %v", c.method, c.path, c.role, got, c.want)
		}
	}
}

func TestSessionsAreReadsOnlyWhereTheContractSaysSo(t *testing.T) {
	// The two rows the contract marks `session: false`: minting a session from
	// a session, and collecting a minted secret.
	if SessionAllowed("POST", "/v1/session") {
		t.Error("a session was allowed to mint another session")
	}
	if SessionAllowed("GET", "/v1/jobs/{id}/secrets") {
		t.Error("a session was allowed to collect a secrets envelope")
	}
	if !SessionAllowed("GET", "/v1/fleet") {
		t.Error("a session was refused a plain read")
	}
	if !SessionAllowed("DELETE", "/v1/session") {
		t.Error("a session was refused its own revocation")
	}
}

func TestEveryRowIsWellFormed(t *testing.T) {
	if len(Matrix) == 0 {
		t.Fatal("the generated matrix is empty")
	}
	seen := map[string]bool{}
	for _, r := range Matrix {
		key := r.Method + " " + r.Path
		if seen[key] {
			t.Errorf("duplicate row for %s", key)
		}
		seen[key] = true
		switch r.Role {
		case "anonymous", auth.RoleViewer, auth.RoleOperator:
		default:
			t.Errorf("%s: role %q is outside {anonymous, viewer, operator}", r.OperationID, r.Role)
		}
		if r.ViewerFields == "" {
			t.Errorf("%s: no viewer_fields note; every row states what a viewer gets, even if that is n/a", r.OperationID)
		}
		if row, ok := LookupID(r.OperationID); !ok || row.Path != r.Path {
			t.Errorf("%s: LookupID disagrees with Lookup", r.OperationID)
		}
	}
}

func TestViewerFields(t *testing.T) {
	// "all" and "n/a" carry no reduction to apply.
	if got := ViewerFields("ctlFleet"); got != nil {
		t.Errorf(`ctlFleet viewer_fields is "all"; ViewerFields returned %v`, got)
	}
	if got := ViewerFields("ctlTenantLogs"); got != nil {
		t.Errorf(`ctlTenantLogs viewer_fields is "n/a"; ViewerFields returned %v`, got)
	}
	if got := ViewerFields("ctlNotAnOperation"); got != nil {
		t.Errorf("unknown operation returned %v", got)
	}
	got := ViewerFields("ctlTenantShow")
	want := []string{"summary, status, units, drift", "registry is null"}
	if len(got) != len(want) {
		t.Fatalf("ctlTenantShow ViewerFields = %v; want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("ctlTenantShow ViewerFields = %v; want %v", got, want)
		}
	}
}
