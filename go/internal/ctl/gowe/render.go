// Package gowe renders what a tenant's API would register with the GoWe
// engine and verifies the tools image each workflow names (ADR-0010
// decision 7, #655 step 4).
//
// The check itself is NOT implemented here. GoWe resolves a `dockerPull` as
// `<image-dir>/<name>` and never compares the file to anything, so the only
// enforcement of image identity in the whole path is
// `ragstack.tool_image.verify_named_image` — the same function the API runs
// at boot. This package shells to it (`python -m ragstack.tool_image verify
// --cwl … --dirs … --json`) from the tenant's own checkout and interpreter,
// so there is ONE implementation of the check and the ctl reports exactly
// what the tenant's boot would decide. A Go re-implementation would be a
// second place for the rules to drift.
package gowe

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/envfile"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// DefaultPythonEnv mirrors adopt.DefaultPythonEnv without importing adopt
// (which pulls the host drivers): the shared env every tenant API runs from.
const DefaultPythonEnv = "/rag/envs/ragstack"

// Timeout bounds one verify run. Hashing a 250 MB image three times over NFS
// is seconds; a wedged `apptainer inspect` is what the bound is for.
const Timeout = 5 * time.Minute

// registrars are the three tenant.env keys whose CWL the API registers, with
// the repo-copy default the Python runners fall back to when the key is
// unset (ragstack.graph_extract.DEFAULT_CWL / ragstack.restore.DEFAULT_CWL;
// GOWE_WORKFLOW_CWL has no default — the ingest backend refuses without it).
var registrars = []struct {
	Key     string
	Default string // relative to the worktree; "" = no default
}{
	{"GOWE_WORKFLOW_CWL", ""},
	{"GRAPH_EXTRACT_CWL", filepath.Join("cwl", "graph-extract.cwl")},
	{"COLLECTION_RESTORE_CWL", filepath.Join("cwl", "restore-collection.cwl")},
}

// Workflow is one registered CWL as the tenant.env resolves it.
type Workflow struct {
	Key  string `json:"key"`  // the tenant.env key
	Path string `json:"path"` // absolute CWL path, or "" when the key is unset and has no default
}

// Input is everything the render needs, resolved from the registry row and
// the tenant.env by Resolve, or built by hand in a test.
type Input struct {
	Tenant        string
	Worktree      string
	Python        string // the interpreter: <python_env>/bin/python
	IngestBackend string // INGEST_BACKEND (informational: the boot runs the check only on gowe)
	ImageDirs     string // GOWE_IMAGE_DIRS as written (comma-separated)
	Workflows     []Workflow
}

// Verdict is ragstack.tool_image.ImageVerdict.to_dict().
type Verdict struct {
	Name               string            `json:"name"`
	State              string            `json:"state"`
	Dirs               []string          `json:"dirs"`
	Path               *string           `json:"path"`
	Exists             bool              `json:"exists"`
	FoundIn            []string          `json:"found_in"`
	ReceiptFound       bool              `json:"receipt_found"`
	SHA256             *string           `json:"sha256"`
	SHA256OK           *bool             `json:"sha256_ok"`
	Labels             map[string]string `json:"labels"`
	LabelsChecked      bool              `json:"labels_checked"`
	LabelsOK           *bool             `json:"labels_ok"`
	CommittedReceiptOK *bool             `json:"committed_receipt_ok"`
	Problems           []string          `json:"problems"`
	Warnings           []string          `json:"warnings"`
}

// Record is one entry of the Python CLI's `records`.
type Record struct {
	CWL        *string `json:"cwl"`
	TextSHA256 *string `json:"text_sha256"`
	ToolImage  *string `json:"tool_image"`
	Verdict    Verdict `json:"verdict"`
}

// Output is the Python CLI's JSON document.
type Output struct {
	OK      bool     `json:"ok"`
	Records []Record `json:"records"`
}

// Report is what `gowe render` prints: the input it resolved, the Python
// check's records keyed back to the tenant.env key that named each CWL, and
// the overall verdict.
type Report struct {
	Tenant        string   `json:"tenant"`
	Worktree      string   `json:"worktree"`
	Python        string   `json:"python"`
	IngestBackend string   `json:"ingest_backend"`
	ImageDirs     string   `json:"image_dirs"`
	Skipped       []string `json:"skipped"` // keys with no path (unset, no default)
	Workflows     []struct {
		Key string `json:"key"`
		Record
	} `json:"workflows"`
	OK bool `json:"ok"`
}

// Resolve reads the tenant's row and its tenant.env into an Input.
func Resolve(t *registry.Tenant, tenantEnv string) (*Input, error) {
	if t.Worktree == "" {
		return nil, fmt.Errorf("tenant %q has no worktree in the registry", t.Name)
	}
	raw, err := os.ReadFile(tenantEnv)
	if err != nil {
		return nil, fmt.Errorf("tenant.env: %w", err)
	}
	f, _, err := envfile.ParseLenient(raw)
	if err != nil {
		return nil, fmt.Errorf("tenant.env: %w", err)
	}
	get := func(k string) string {
		v, _ := f.Get(k)
		return strings.TrimSpace(v)
	}
	env := t.PythonEnv
	if env == "" {
		env = DefaultPythonEnv
	}
	in := &Input{
		Tenant:        t.Name,
		Worktree:      t.Worktree,
		Python:        filepath.Join(env, "bin", "python"),
		IngestBackend: get("INGEST_BACKEND"),
		ImageDirs:     get("GOWE_IMAGE_DIRS"),
	}
	for _, r := range registrars {
		p := get(r.Key)
		if p == "" && r.Default != "" {
			p = filepath.Join(t.Worktree, r.Default)
		}
		if p != "" && !filepath.IsAbs(p) {
			// The API resolves a relative CWL against its CWD, which the unit
			// sets to the worktree.
			p = filepath.Join(t.Worktree, p)
		}
		in.Workflows = append(in.Workflows, Workflow{Key: r.Key, Path: p})
	}
	return in, nil
}

// Args is the argv (after the interpreter) the render runs. Exported so a
// test can hold the ctl to the one command line the Python CLI documents.
func (in *Input) Args() []string {
	args := []string{"-m", "ragstack.tool_image", "verify", "--json", "--dirs", in.ImageDirs}
	for _, w := range in.Workflows {
		if w.Path != "" {
			args = append(args, "--cwl", w.Path)
		}
	}
	return args
}

// Run executes the Python check and returns the Report. A Report with
// OK=false is not an error: the check ran and found a problem. An error is
// the check NOT running (no interpreter, no checkout, unparseable output).
func Run(ctx context.Context, in *Input, stderr io.Writer) (*Report, error) {
	rep := &Report{
		Tenant: in.Tenant, Worktree: in.Worktree, Python: in.Python,
		IngestBackend: in.IngestBackend, ImageDirs: in.ImageDirs, OK: true,
	}
	var named []Workflow
	for _, w := range in.Workflows {
		if w.Path == "" {
			rep.Skipped = append(rep.Skipped, w.Key)
			continue
		}
		named = append(named, w)
	}
	if len(named) == 0 {
		return rep, nil
	}
	if !filepath.IsAbs(in.Python) {
		return nil, fmt.Errorf("python interpreter %q is not an absolute path", in.Python)
	}
	if _, err := os.Stat(in.Python); err != nil {
		return nil, fmt.Errorf("python interpreter: %w", err)
	}
	ctx, cancel := context.WithTimeout(ctx, Timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, in.Python, in.Args()...)
	cmd.Dir = in.Worktree
	// The tenant's package, from its checkout: PYTHONPATH=<worktree>/python,
	// exactly what the unit gives the API. Nothing else from this process's
	// environment but what the interpreter needs to start.
	cmd.Env = sanitizedEnv(filepath.Join(in.Worktree, "python"))
	var out, errb bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &errb
	err := cmd.Run()
	var exit *exec.ExitError
	if err != nil && !errors.As(err, &exit) {
		return nil, fmt.Errorf("running %s: %w", in.Python, err)
	}
	if errb.Len() > 0 && stderr != nil {
		fmt.Fprint(stderr, errb.String())
	}
	var doc Output
	if jerr := json.Unmarshal(out.Bytes(), &doc); jerr != nil {
		// Exit 1 with JSON is "problems found"; exit 2 (usage) or an import
		// error prints no JSON and IS an error — the check did not run.
		if exit != nil {
			return nil, fmt.Errorf("%s exited %d without a JSON verdict: %s",
				in.Python, exit.ExitCode(), strings.TrimSpace(errb.String()))
		}
		return nil, fmt.Errorf("unparseable verify output: %v", jerr)
	}
	if len(doc.Records) != len(named) {
		return nil, fmt.Errorf("verify returned %d records for %d workflows", len(doc.Records), len(named))
	}
	for i, w := range named {
		rec := doc.Records[i]
		rep.Workflows = append(rep.Workflows, struct {
			Key string `json:"key"`
			Record
		}{w.Key, rec})
		if len(rec.Verdict.Problems) > 0 {
			rep.OK = false
		}
	}
	// A Python-side "ok" that disagrees with our own reading of the records
	// would be a contract drift; trust the records (they carry the problems).
	if doc.OK != rep.OK && stderr != nil {
		fmt.Fprintf(stderr, "gowe render: verify said ok=%v but the records say %v; using the records\n", doc.OK, rep.OK)
	}
	return rep, nil
}

// sanitizedEnv is the child's environment: PATH (apptainer is found on it),
// HOME (apptainer and git want one), the locale, and the tenant's package
// path. No tenant.env, no secrets — the check reads files, not services.
func sanitizedEnv(pythonPath string) []string {
	env := []string{"PYTHONPATH=" + pythonPath, "PYTHONDONTWRITEBYTECODE=1"}
	for _, k := range []string{"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "HF_HOME"} {
		if v, ok := os.LookupEnv(k); ok {
			env = append(env, k+"="+v)
		}
	}
	return env
}

// Print writes the human report.
func Print(w io.Writer, rep *Report) {
	fmt.Fprintf(w, "tenant:         %s\n", rep.Tenant)
	fmt.Fprintf(w, "worktree:       %s\n", rep.Worktree)
	fmt.Fprintf(w, "python:         %s\n", rep.Python)
	fmt.Fprintf(w, "ingest backend: %s\n", orNone(rep.IngestBackend))
	fmt.Fprintf(w, "image dirs:     %s\n", orNone(rep.ImageDirs))
	if rep.IngestBackend != "gowe" {
		fmt.Fprintf(w, "note: INGEST_BACKEND is not gowe, so the API's boot does not run this check; this is what it WOULD find\n")
	}
	skipped := append([]string(nil), rep.Skipped...)
	sort.Strings(skipped)
	for _, k := range skipped {
		fmt.Fprintf(w, "\n%s: unset (no default) — nothing registered\n", k)
	}
	for _, wf := range rep.Workflows {
		fmt.Fprintf(w, "\n%s: %s\n", wf.Key, deref(wf.CWL))
		fmt.Fprintf(w, "  text sha256 (GoWe would content-hash this): %s\n", deref(wf.TextSHA256))
		v := wf.Verdict
		where := ""
		if v.Path != nil {
			where = " at " + *v.Path
		}
		fmt.Fprintf(w, "  dockerPull: %s -> %s%s\n", orNone(deref(wf.ToolImage)), v.State, where)
		if v.SHA256 != nil {
			fmt.Fprintf(w, "  image sha256: %s (receipt: %s)\n", *v.SHA256, okWord(v.SHA256OK))
		}
		if v.LabelsChecked {
			fmt.Fprintf(w, "  labels: %s\n", okWord(v.LabelsOK))
		}
		for _, p := range v.Problems {
			fmt.Fprintf(w, "  problem: %s\n", p)
		}
		for _, p := range v.Warnings {
			fmt.Fprintf(w, "  warning: %s\n", p)
		}
	}
	if rep.OK {
		fmt.Fprintln(w, "\nok")
	} else {
		fmt.Fprintln(w, "\nFAIL: the boot of this tenant would refuse (ADR-0010 decision 7)")
	}
}

func orNone(s string) string {
	if s == "" {
		return "(unset)"
	}
	return s
}

func deref(p *string) string {
	if p == nil {
		return ""
	}
	return *p
}

func okWord(b *bool) string {
	switch {
	case b == nil:
		return "not checked"
	case *b:
		return "ok"
	default:
		return "MISMATCH"
	}
}
