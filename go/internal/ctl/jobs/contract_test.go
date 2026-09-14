package jobs

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/model"
)

// Contract validation. The Go types live in internal/ctl/model and are
// checked there against the schemas; what THIS file checks is different and
// is the part a client actually sees: the documents the ENGINE emits — a job
// mid-flight and at rest, the plan it answers a dry run with, the audit rows
// it writes, the secrets envelope it delivers. A field the engine leaves
// empty that the schema requires, or a state name it invents, shows up here
// and nowhere else.
//
// The validator is the same one internal/ctl/model/model_test.go uses (and
// conformance/ctl/helpers.py): every schema file registered under its $id so
// cross-file $ref resolves.

const jobsSchemasDir = "../../../../contracts/ctl/schemas"

const jobsValidateScript = `
import glob, json, os, sys
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
d, name, doc = sys.argv[1], sys.argv[2], json.load(open(sys.argv[3]))
reg = Registry()
schemas = {}
for p in glob.glob(os.path.join(d, "*.json")):
    s = json.load(open(p))
    schemas[os.path.basename(p)[:-5]] = s
    reg = reg.with_resource(s["$id"], Resource.from_contents(s))
errs = sorted(Draft202012Validator(schemas[name], registry=reg).iter_errors(doc),
              key=lambda e: list(e.absolute_path))
for e in errs[:40]:
    print("/" + "/".join(str(p) for p in e.absolute_path), "->", e.message[:200])
sys.exit(1 if errs else 0)
`

func pythonWithJSONSchema(t *testing.T) string {
	t.Helper()
	for _, c := range []string{"/rag/envs/ragstack/bin/python", "python3"} {
		if p, err := exec.LookPath(c); err == nil {
			if exec.Command(p, "-c", "import jsonschema, referencing").Run() == nil {
				return p
			}
		}
	}
	return ""
}

func validateDoc(t *testing.T, py, schema string, v any) {
	t.Helper()
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		t.Fatalf("%s: marshal: %v", schema, err)
	}
	f := filepath.Join(t.TempDir(), schema+".json")
	if err := os.WriteFile(f, b, 0o600); err != nil {
		t.Fatal(err)
	}
	out, err := exec.Command(py, "-c", jobsValidateScript, jobsSchemasDir, schema, f).CombinedOutput()
	if err != nil {
		t.Errorf("%s does not validate against the contract:\n%s\ndocument:\n%s", schema, out, b)
	}
}

// TestEmittedDocumentsValidateAgainstTheContract drives one job of each shape
// through the engine and validates what comes out.
func TestEmittedDocumentsValidateAgainstTheContract(t *testing.T) {
	if _, err := os.Stat(jobsSchemasDir); err != nil {
		t.Skip("contract schemas not present")
	}
	py := pythonWithJSONSchema(t)
	if py == "" {
		t.Skip("no python with jsonschema + referencing")
	}
	ctx := context.Background()
	secret := strings.Repeat("9f", 32)

	tr := newTracker()
	var st Store
	failing := happyOp(tr, func() Store { return st })
	failing.verb = "restore"
	inner := failing.planFn
	failing.planFn = func(oc Context, args map[string]any) *Planned {
		p := inner(oc, args)
		p.Steps[2].Run = func(ctx context.Context, sc *StepContext) (string, error) {
			return "", errFailedStep
		}
		return p
	}
	reg := fakeRegistry{
		"backup":   happyOp(tr, func() Store { return st }),
		"restore":  failing,
		"key-mint": mintingOp(secret),
	}
	e, store, _ := newTestEngine(t, reg, func(o *EngineOptions) { o.Redactor = testRedactor{secret: secret} })
	st = store

	// 1. The dry-run plan.
	dry := req("backup", "dev", "")
	dry.DryRun = true
	plan, _, err := e.Submit(ctx, dry)
	if err != nil {
		t.Fatal(err)
	}
	validateDoc(t, py, "plan", plan)

	// 2. A queued job, exactly as the 202 body carries it.
	_, accepted, err := e.Submit(ctx, req("backup", "dev", "b1"))
	if err != nil {
		t.Fatal(err)
	}
	validateDoc(t, py, "job", accepted)

	// 3. A succeeded job.
	done := waitFor(t, e, accepted.ID, model.JobSucceeded, model.JobFailed)
	validateDoc(t, py, "job", done)

	// 4. A rolled-back job.
	_, bad, err := e.Submit(ctx, req("restore", "dev", "r1"))
	if err != nil {
		t.Fatal(err)
	}
	rolled := waitFor(t, e, bad.ID, model.JobRolledBack, model.JobFailed, model.JobSucceeded)
	validateDoc(t, py, "job", rolled)

	// 5. An interrupted job (the reconciled shape: reservations kept, no worker).
	interrupted := *done
	interrupted.State = model.JobInterrupted
	interrupted.Worker = nil
	interrupted.Lock = nil
	interrupted.Steps[2].State = model.StepInterrupted
	interrupted.Error = &model.JobError{Step: stepNo(3), Code: "interrupted", Detail: "the worker did not survive"}
	validateDoc(t, py, "job", &interrupted)

	// 6. A viewer's job: worker, lock null; reservations and external ids empty.
	viewerJob := *done
	viewerJob.Worker, viewerJob.Lock = nil, nil
	viewerJob.Reservations = []model.Reservation{}
	for i := range viewerJob.Steps {
		viewerJob.Steps[i].Log = ""
		viewerJob.Steps[i].ExternalIDs = []string{}
	}
	validateDoc(t, py, "job", &viewerJob)

	// 7. The jobs listing.
	list, truncated, err := e.List(ctx, ListFilter{Limit: 50})
	if err != nil {
		t.Fatal(err)
	}
	validateDoc(t, py, "jobs_response", model.JobsResponse{Jobs: list, Limit: 50, Truncated: truncated})

	// 8. The audit rows — intent and result, from a real run.
	rows, tr2, err := e.Audit(ctx, 100)
	if err != nil || len(rows) < 4 {
		t.Fatalf("audit rows = %d, %v", len(rows), err)
	}
	validateDoc(t, py, "audit_response", model.AuditResponse{Rows: rows, Limit: 100, Truncated: tr2})

	// 9. The secrets envelope.
	_, minted, err := e.Submit(ctx, req("key-mint", "dev", "m1"))
	if err != nil {
		t.Fatal(err)
	}
	waitFor(t, e, minted.ID, model.JobSucceeded, model.JobFailed)
	secrets, err := e.Secrets(ctx, minted.ID, operator())
	if err != nil {
		t.Fatalf("Secrets: %v", err)
	}
	validateDoc(t, py, "secrets_response", secrets)
	if _, err := time.Parse(time.RFC3339, secrets.ExpiresAt); err != nil {
		t.Fatalf("expires_at = %q: %v", secrets.ExpiresAt, err)
	}
}

var errFailedStep = &stepError{"tar: no space left on device"}

type stepError struct{ s string }

func (e *stepError) Error() string { return e.s }
