package registry

import (
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// quarantinedDev is the fixture fleet with dev decommissioned: state
// quarantined and the block a decommission writes.
func quarantinedDev() *Fleet {
	f := LiveFixture()
	f.UpdatedBy = "local:1000" // what Save stamps, so ValidateContract alone can be asked
	dev := f.Tenants["dev"]
	dev.State = StateQuarantined
	dev.Quarantine = &Quarantine{
		Dir: dev.DataDir + QuarantineMarker + "20261008T120000Z", At: "2026-10-08T12:00:00Z",
		JobID: "01ARZ3NDEKTSV4RRFFQ69G5FAV", Bundle: "/rag/backups/tenants/dev/20261008T115500Z-backup",
	}
	return f
}

// The block round-trips, validates with `state: quarantined`, and only then.
func TestQuarantineBlockRoundTripsAndIsChecked(t *testing.T) {
	f := quarantinedDev()
	dir := t.TempDir()
	reg := filepath.Join(dir, "registry.json")
	if err := Save(reg, f, "local:1000"); err != nil {
		t.Fatalf("a quarantined row with its block was refused: %v", err)
	}
	back, err := LoadNoRepair(reg)
	if err != nil {
		t.Fatal(err)
	}
	q := back.Tenants["dev"].Quarantine
	if q == nil || q.Dir != f.Tenants["dev"].Quarantine.Dir || q.Bundle == "" {
		t.Fatalf("the quarantine block did not round-trip: %+v", q)
	}

	// A null bundle is legal (a sandbox decommissioned with neither archive
	// nor backup).
	back.Tenants["dev"].Quarantine.Bundle = ""
	if err := back.ValidateContract(); err != nil {
		t.Errorf("a quarantine with a null bundle was refused: %v", err)
	}

	for name, c := range map[string]struct {
		mutate func(*Tenant)
		want   string
	}{
		"an active row": {func(t *Tenant) { t.State = "active" }, "only a \"quarantined\" row"},
		"a dir outside the data dir": {func(t *Tenant) {
			t.Quarantine.Dir = "/tmp/dev" + QuarantineMarker + "20261008T120000Z"
		}, "renamed aside"},
		"another tenant's tree": {func(t *Tenant) {
			t.Quarantine.Dir = "/rag/data/tenants/demo" + QuarantineMarker + "20261008T120000Z"
		}, "renamed aside"},
		"no marker": {func(t *Tenant) { t.Quarantine.Dir = t.DataDir + ".old-20261008T120000Z" }, "does not match"},
		"no stamp":  {func(t *Tenant) { t.Quarantine.Dir = t.DataDir + QuarantineMarker + "later" }, "does not match"},
		"a dotdot path": {func(t *Tenant) {
			t.Quarantine.Dir = t.DataDir + "/../demo" + QuarantineMarker + "20261008T120000Z"
		}, "clean"},
		"a bad job id":      {func(t *Tenant) { t.Quarantine.JobID = "job-1" }, "job_id"},
		"no timestamp":      {func(t *Tenant) { t.Quarantine.At = "" }, "/quarantine/at"},
		"a relative bundle": {func(t *Tenant) { t.Quarantine.Bundle = "backups/dev" }, "/quarantine/bundle"},
	} {
		t.Run(name, func(t *testing.T) {
			f := quarantinedDev()
			c.mutate(f.Tenants["dev"])
			err := f.ValidateContract()
			if err == nil || !strings.Contains(err.Error(), c.want) {
				t.Errorf("ValidateContract = %v, want a refusal containing %q", err, c.want)
			}
		})
	}

	// Absent is the ordinary shape (every row that is not quarantined, and a
	// row quarantined before the block existed), and it is not serialised.
	f2 := LiveFixture()
	f2.UpdatedBy = "local:1000"
	f2.Tenants["dev"].State = StateQuarantined
	if err := f2.ValidateContract(); err != nil {
		t.Errorf("a quarantined row without the block (written before it existed) was refused: %v", err)
	}
	b, err := json.Marshal(LiveFixture())
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(b, []byte(`"quarantine"`)) {
		t.Error("an absent quarantine block is serialised; an older ragstack-ctl would refuse the registry")
	}
}

// The JSON schema accepts the block's shape and refuses a malformed one.
func TestQuarantineBlockAgreesWithTheSchema(t *testing.T) {
	schema := "../../../../contracts/ctl/schemas/registry.json"
	py := ""
	for _, c := range []string{"/rag/envs/ragstack/bin/python", "python3"} {
		if p, err := exec.LookPath(c); err == nil && exec.Command(p, "-c", "import jsonschema").Run() == nil {
			py = p
			break
		}
	}
	if py == "" {
		t.Skip("no python with jsonschema")
	}
	script := `
import json, sys
from jsonschema import Draft202012Validator
schema = json.load(open(sys.argv[1])); doc = json.load(open(sys.argv[2]))
sys.exit(1 if list(Draft202012Validator(schema).iter_errors(doc)) else 0)
`
	dir := t.TempDir()
	good := filepath.Join(dir, "registry.json")
	if err := Save(good, quarantinedDev(), "local:1000"); err != nil {
		t.Fatal(err)
	}
	if out, err := exec.Command(py, "-c", script, schema, good).CombinedOutput(); err != nil {
		t.Fatalf("a quarantined registry does not validate against registry.json: %v\n%s", err, out)
	}
	raw, err := os.ReadFile(good)
	if err != nil {
		t.Fatal(err)
	}
	for name, edit := range map[string][2]string{
		"no marker":      {`.quarantined-20261008T120000Z`, `.old-20261008T120000Z`},
		"an extra field": {`"job_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV"`, `"job_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV", "by": "x"`},
		"a bad job id":   {`"job_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV"`, `"job_id": "job-1"`},
	} {
		doc := bytes.Replace(raw, []byte(edit[0]), []byte(edit[1]), 1)
		if bytes.Equal(doc, raw) {
			t.Fatalf("%s: test setup, %q not in the saved registry", name, edit[0])
		}
		path := filepath.Join(dir, strings.ReplaceAll(name, " ", "-")+".json")
		if err := os.WriteFile(path, doc, 0o600); err != nil {
			t.Fatal(err)
		}
		if err := exec.Command(py, "-c", script, schema, path).Run(); err == nil {
			t.Errorf("%s: registry.json accepted it", name)
		}
	}
}
