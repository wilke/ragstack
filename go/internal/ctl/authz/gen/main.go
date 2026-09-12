// Command gen writes go/internal/ctl/authz/matrix_gen.go from the
// `x-ctl-authorization-matrix` extension of contracts/ctl/openapi.yaml.
//
// # Why a generator and not a runtime read
//
// The daemon must not depend on a contract file being present, readable and
// unmodified at start-up: the authorization table is a security control, and a
// control that can be edited under a running binary is not one. Generating a
// Go table makes the matrix part of the build artifact, reviewable in a diff,
// and testable by regenerating into a temp file and comparing
// (matrix_test.go). The same table drives the Python conformance suite, from
// the same YAML, so the two cannot disagree without the diff test failing.
//
// # Why python for the YAML
//
// The repo's Go dependency budget is stdlib + chi (ADR-0006); a YAML parser
// would be a new dependency bought for one build-time tool. Instead the tool
// shells out — argv-only, absolute interpreter path, no shell — to convert the
// contract to JSON, which the standard library does parse. That runs at build
// time on a developer machine, never in the daemon.
//
// Usage:
//
//	go run ./internal/ctl/authz/gen \
//	    -contract ../contracts/ctl/openapi.yaml \
//	    -out internal/ctl/authz/matrix_gen.go \
//	    [-python /rag/envs/ragstack/bin/python]
package main

import (
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"go/format"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
)

// DefaultPython is the conda env the deployment host ships; it has PyYAML.
const DefaultPython = "/rag/envs/ragstack/bin/python"

// yamlToJSON is the whole program handed to the interpreter: read one file
// named by argv, print it as JSON. It never touches the network, never writes,
// and takes its input path from argv rather than from the program text.
const yamlToJSON = `import json,sys,yaml;print(json.dumps(yaml.safe_load(open(sys.argv[1],encoding="utf-8"))))`

type row struct {
	OperationID  string `json:"operationId"`
	Method       string `json:"method"`
	Path         string `json:"path"`
	Role         string `json:"role"`
	Session      bool   `json:"session"`
	ViewerFields string `json:"viewer_fields"`
	// Mutating is DERIVED, not read from the matrix: an operation mutates when
	// it is not a GET and the contract gives it a request body that can carry
	// `ctl_api_key` (op_request.json / create_request.json). That is exactly
	// the set op_request.json calls "the body of EVERY mutating call", and
	// deriving it is what stops the guard's body-key rule from becoming a
	// second list that drifts from the contract's.
	//
	// Method alone would over-capture: `DELETE /v1/session` (a session logging
	// itself out, 204) and `POST /v1/gateway/render` (a viewer's dry render)
	// are non-GET READS whose bodies carry no key, and demanding one from a
	// session would contradict the contract's own answers for them.
	Mutating bool `json:"-"`
}

func main() {
	contract := flag.String("contract", "", "path to contracts/ctl/openapi.yaml")
	out := flag.String("out", "", "path of the matrix_gen.go to write")
	python := flag.String("python", DefaultPython, "interpreter with PyYAML (build-time only)")
	flag.Parse()
	if *contract == "" || *out == "" {
		fmt.Fprintln(os.Stderr, "usage: gen -contract <openapi.yaml> -out <matrix_gen.go> [-python P]")
		os.Exit(2)
	}
	src, err := generate(*contract, *python)
	if err != nil {
		fmt.Fprintf(os.Stderr, "gen: %v\n", err)
		os.Exit(1)
	}
	if err := os.WriteFile(*out, src, 0o644); err != nil { //nolint:gosec // generated source, not a secret
		fmt.Fprintf(os.Stderr, "gen: %v\n", err)
		os.Exit(1)
	}
}

// schemaProperties reads one schema file's top-level `properties` map. The
// contract keeps every component in its own file under contracts/ctl/schemas,
// so "does this request body carry ctl_api_key" is answered there.
func schemaProperties(path string) (map[string]json.RawMessage, error) {
	raw, err := os.ReadFile(filepath.Clean(path))
	if err != nil {
		return nil, fmt.Errorf("request body schema %s: %w", path, err)
	}
	var doc struct {
		Properties map[string]json.RawMessage `json:"properties"`
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		return nil, fmt.Errorf("request body schema %s: %w", path, err)
	}
	return doc.Properties, nil
}

// Generate is exported through generate() for the test, which regenerates into
// a temp file and diffs against the committed one.
func generate(contract, python string) ([]byte, error) {
	abs, err := filepath.Abs(contract)
	if err != nil {
		return nil, err
	}
	// argv-only: the interpreter path and the two arguments are separate argv
	// entries, so nothing here is parsed by a shell.
	cmd := exec.Command(python, "-c", yamlToJSON, abs) //nolint:gosec // fixed program, argv-only, build-time
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	if err := cmd.Run(); err != nil {
		return nil, fmt.Errorf("%s: %v: %s", python, err, strings.TrimSpace(stderr.String()))
	}
	var doc struct {
		Matrix []row `json:"x-ctl-authorization-matrix"`
		Paths  map[string]map[string]struct {
			OperationID string `json:"operationId"`
			Role        string `json:"x-ctl-role"`
			RequestBody struct {
				Content map[string]struct {
					Schema struct {
						Ref string `json:"$ref"`
					} `json:"schema"`
				} `json:"content"`
			} `json:"requestBody"`
		} `json:"paths"`
		Components struct {
			Schemas map[string]struct {
				// A component is either inline or, as this contract writes
				// them, a $ref to a file under contracts/ctl/schemas.
				Ref        string                     `json:"$ref"`
				Properties map[string]json.RawMessage `json:"properties"`
			} `json:"schemas"`
		} `json:"components"`
	}
	if err := json.Unmarshal(stdout.Bytes(), &doc); err != nil {
		return nil, fmt.Errorf("contract is not JSON-decodable after yaml.safe_load: %w", err)
	}
	if len(doc.Matrix) == 0 {
		return nil, fmt.Errorf("%s has no x-ctl-authorization-matrix rows", contract)
	}
	// The extension table and the per-operation x-ctl-role annotation are one
	// fact; a generator that silently preferred one would let them drift
	// exactly as far as the next reader's attention.
	byID := map[string]row{}
	for _, r := range doc.Matrix {
		byID[r.OperationID] = r
	}
	for path, item := range doc.Paths {
		for method, op := range item {
			if op.OperationID == "" {
				continue
			}
			r, ok := byID[op.OperationID]
			if !ok {
				return nil, fmt.Errorf("operation %s (%s %s) has no matrix row", op.OperationID, method, path)
			}
			if !strings.EqualFold(r.Method, method) || r.Path != path || r.Role != op.Role {
				return nil, fmt.Errorf("operation %s: matrix says %s %s role=%s, contract says %s %s role=%s",
					op.OperationID, r.Method, r.Path, r.Role, strings.ToUpper(method), path, op.Role)
			}
			// Derived, from the contract and nothing else: see row.Mutating.
			if !strings.EqualFold(method, "get") {
				for _, media := range op.RequestBody.Content {
					name := strings.TrimPrefix(media.Schema.Ref, "#/components/schemas/")
					if name == "" {
						continue
					}
					schema := doc.Components.Schemas[name]
					props := schema.Properties
					if props == nil && schema.Ref != "" {
						props, err = schemaProperties(filepath.Join(filepath.Dir(abs), schema.Ref))
						if err != nil {
							return nil, err
						}
					}
					if _, carriesKey := props["ctl_api_key"]; carriesKey {
						r.Mutating = true
						byID[op.OperationID] = r
					}
				}
			}
		}
	}
	mutating := 0
	for _, r := range byID {
		if r.Mutating {
			mutating++
		}
	}
	if mutating == 0 {
		return nil, fmt.Errorf("%s: no operation has a request body carrying ctl_api_key; the mutation set cannot be empty", contract)
	}
	rows := make([]row, 0, len(doc.Matrix))
	for _, r := range doc.Matrix {
		rows = append(rows, byID[r.OperationID])
	}
	sort.SliceStable(rows, func(i, j int) bool {
		if rows[i].Path != rows[j].Path {
			return rows[i].Path < rows[j].Path
		}
		return rows[i].Method < rows[j].Method
	})

	var b bytes.Buffer
	fmt.Fprintf(&b, `// Code generated by "go run ./internal/ctl/authz/gen"; DO NOT EDIT.
//
// Source: contracts/ctl/openapi.yaml, extension x-ctl-authorization-matrix.
// Regenerate after any contract change; matrix_test.go fails the build
// otherwise.

package authz

// Matrix is the deny-by-default authorization table: one row per contract
// operation, keyed by (METHOD, chi route pattern).
var Matrix = []Row{
`)
	for _, r := range rows {
		fmt.Fprintf(&b, "\t{OperationID: %q, Method: %q, Path: %q, Role: %q, Session: %t, Mutating: %t, ViewerFields: %q},\n",
			r.OperationID, strings.ToUpper(r.Method), r.Path, r.Role, r.Session, r.Mutating, r.ViewerFields)
	}
	fmt.Fprint(&b, "}\n")
	return format.Source(b.Bytes())
}
