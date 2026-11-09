package registry

import (
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/paths"
)

// stores.qdrant.data_dir (PR1 of the per-qdrant host data dir): an optional
// member that changes nothing until a row sets it.

const testQdrantDir = "/data2/qdrant/dev"

// A registry written before the field existed loads and re-marshals
// byte-identically, and no row grows a data_dir under stores.qdrant: the first
// write by this binary stays readable by the deployed one. (Removing
// `omitempty` from Qdrant.DataDir fails this test.)
func TestARegistryWithoutQdrantDataDirIsByteIdentical(t *testing.T) {
	raw, err := os.ReadFile(goldenPrePRF)
	if err != nil {
		t.Fatal(err)
	}
	f, err := LoadNoRepair(goldenPrePRF)
	if err != nil {
		t.Fatalf("the golden registry was refused: %v", err)
	}
	for name, tn := range f.Tenants {
		if tn.Stores.Qdrant.DataDir != "" {
			t.Errorf("%s: qdrant data_dir = %q, want absent", name, tn.Stores.Qdrant.DataDir)
		}
	}
	out, err := Marshal(f)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(out, raw) {
		t.Fatalf("re-marshal is not byte-identical:\n%s", firstDiff(raw, out))
	}
	// Belt and braces: the fixture itself, marshalled fresh, has no data_dir
	// member under any stores.qdrant.
	b, err := Marshal(LiveFixture())
	if err != nil {
		t.Fatal(err)
	}
	var doc struct {
		Tenants map[string]struct {
			Stores struct {
				Qdrant map[string]json.RawMessage `json:"qdrant"`
			} `json:"stores"`
		} `json:"tenants"`
	}
	if err := json.Unmarshal(b, &doc); err != nil {
		t.Fatal(err)
	}
	for name, tn := range doc.Tenants {
		if _, ok := tn.Stores.Qdrant["data_dir"]; ok {
			t.Errorf("%s: stores.qdrant serialised an absent data_dir", name)
		}
	}
}

// A row that sets it round-trips byte-stably, and Tenant.Paths moves exactly
// the qdrant paths.
func TestQdrantDataDirRoundTripsAndDrivesPaths(t *testing.T) {
	f := LiveFixture()
	f.Tenants["dev"].Stores.Qdrant.DataDir = testQdrantDir
	reg := filepath.Join(t.TempDir(), "registry.json")
	if err := Save(reg, f, "local:1000"); err != nil {
		t.Fatalf("a row with a qdrant data_dir was refused: %v", err)
	}
	written, err := os.ReadFile(reg)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(written, []byte(`"data_dir": "`+testQdrantDir+`"`)) {
		t.Fatal("the qdrant data_dir was not written")
	}
	back, err := LoadNoRepair(reg)
	if err != nil {
		t.Fatalf("does not load back: %v", err)
	}
	again, err := Marshal(back)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(written, again) {
		t.Errorf("not byte-stable:\n%s", firstDiff(written, again))
	}

	roots := paths.NewRoots("/rag", paths.Overrides{})
	dev := back.Tenants["dev"]
	got, base := dev.Paths(roots), paths.TenantPaths(roots, "dev", "dev")
	if got != base.WithQdrantDataDir(testQdrantDir) {
		t.Errorf("Paths = %+v", got)
	}
	if got.QdrantStorage != testQdrantDir+"/storage" || got.QdrantSnapshots != testQdrantDir+"/snapshots" {
		t.Errorf("qdrant paths = %q %q", got.QdrantStorage, got.QdrantSnapshots)
	}
	// Every row without the field derives exactly paths.TenantPaths.
	for name, tn := range back.Tenants {
		if name == "dev" {
			continue
		}
		if tn.Paths(roots) != paths.TenantPaths(roots, tn.Name, tn.ManifestName) {
			t.Errorf("%s: Paths differs from TenantPaths without a data_dir", name)
		}
	}
}

func TestQdrantDataDirRowRules(t *testing.T) {
	for _, tc := range []struct {
		name   string
		mutate func(*Fleet)
		want   string
	}{
		{"on a shared store", func(f *Fleet) { f.Tenants["demo"].Stores.Qdrant.DataDir = "/data2/qdrant/demo" }, `"shared" store`},
		{"the default spelled out", func(f *Fleet) { f.Tenants["dev"].Stores.Qdrant.DataDir = "/rag/data/tenants/dev/qdrant" }, "default spelling"},
		{"relative", func(f *Fleet) { f.Tenants["dev"].Stores.Qdrant.DataDir = "data2/qdrant" }, "does not match"},
		{"not clean", func(f *Fleet) { f.Tenants["dev"].Stores.Qdrant.DataDir = "/data2/qdrant/../dev/" }, "not clean"},
		{"equal to the data dir", func(f *Fleet) { f.Tenants["dev"].Stores.Qdrant.DataDir = "/rag/data/tenants/dev" }, "equals or contains"},
		{"containing the data dir", func(f *Fleet) { f.Tenants["dev"].Stores.Qdrant.DataDir = "/rag/data" }, "equals or contains"},
		{"the filesystem root", func(f *Fleet) { f.Tenants["dev"].Stores.Qdrant.DataDir = "/" }, "filesystem root"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := LiveFixture()
			f.UpdatedBy, f.UpdatedAt = "local:test", "2026-11-09T00:00:00Z"
			tc.mutate(f)
			err := f.ValidateContract()
			if err == nil || !strings.Contains(err.Error(), "/stores/qdrant/data_dir") || !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("ValidateContract = %v, want a /stores/qdrant/data_dir refusal saying %q", err, tc.want)
			}
		})
	}
	// Legal: absent, and a clean off-tree dir on an exclusive store, and a
	// non-default dir under the row's own data dir.
	for _, d := range []string{"", testQdrantDir, "/rag/data/tenants/dev/qdrant-b"} {
		f := LiveFixture()
		f.UpdatedBy, f.UpdatedAt = "local:test", "2026-11-09T00:00:00Z"
		f.Tenants["dev"].Stores.Qdrant.DataDir = d
		if err := f.ValidateContract(); err != nil {
			t.Errorf("data_dir %q refused: %v", d, err)
		}
	}
}

// exclusiveFleet is LiveFixture with lucid-next's qdrant made exclusive, so two
// rows can legally carry a data_dir.
func exclusiveFleet() *Fleet {
	f := LiveFixture()
	f.Tenants["lucid-next"].Stores.Qdrant.Ownership = OwnershipExclusive
	return f
}

func TestQdrantDataDirCrossRowRules(t *testing.T) {
	for _, tc := range []struct {
		name   string
		mutate func(*Fleet)
		want   string
	}{
		{"shared between two rows", func(f *Fleet) {
			f.Tenants["dev"].Stores.Qdrant.DataDir = "/data2/qdrant/x"
			f.Tenants["lucid-next"].Stores.Qdrant.DataDir = "/data2/qdrant/x"
		}, "share stores.qdrant.data_dir"},
		{"under another row's data dir", func(f *Fleet) {
			f.Tenants["dev"].Stores.Qdrant.DataDir = "/rag/data/tenants/lucid/qdrant-dev"
		}, `inside tenant "lucid-next"'s data_dir`},
		{"equal to another row's data dir", func(f *Fleet) {
			f.Tenants["dev"].Stores.Qdrant.DataDir = "/rag/data/tenants/asm"
		}, `inside tenant "asm-next"'s data_dir`},
		{"under another row's moved qdrant dir", func(f *Fleet) {
			f.Tenants["lucid-next"].Stores.Qdrant.DataDir = "/data2/qdrant"
			f.Tenants["dev"].Stores.Qdrant.DataDir = "/data2/qdrant/dev"
		}, "qdrant"},
		{"containing another row's data dir", func(f *Fleet) {
			f.Tenants["dev"].Stores.Qdrant.DataDir = "/rag/data/tenants/dev/qdrant-b"
			f.Tenants["lucid-next"].DataDir = "/rag/data/tenants/dev/qdrant-b/lucid"
		}, "lucid-next"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := exclusiveFleet()
			tc.mutate(f)
			err := f.Validate()
			if err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("Validate = %v, want a refusal containing %q", err, tc.want)
			}
		})
	}
	f := exclusiveFleet()
	f.Tenants["dev"].Stores.Qdrant.DataDir = "/data2/qdrant/dev"
	f.Tenants["lucid-next"].Stores.Qdrant.DataDir = "/data2/qdrant/lucid"
	if err := f.Validate(); err != nil {
		t.Errorf("two distinct sibling dirs refused: %v", err)
	}
}

// The schema and the Go mirror agree on the field's SHAPE: a valid value is
// accepted by both, and each malformed value is refused by both. (The
// coherence rules — exclusive only, not the default spelling, no nesting — are
// Go-only: JSON Schema cannot compare two members.)
func TestQdrantDataDirAgreesWithTheSchema(t *testing.T) {
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
errs = list(Draft202012Validator(schema).iter_errors(doc))
for e in errs[:10]:
    print("/" + "/".join(str(p) for p in e.path), "->", e.message[:200])
sys.exit(1 if errs else 0)
`
	dir := t.TempDir()
	good := filepath.Join(dir, "registry.json")
	f := LiveFixture()
	f.Tenants["dev"].Stores.Qdrant.DataDir = testQdrantDir
	if err := Save(good, f, "local:1000"); err != nil {
		t.Fatal(err)
	}
	if out, err := exec.Command(py, "-c", script, schema, good).CombinedOutput(); err != nil {
		t.Fatalf("a registry with a qdrant data_dir does not validate: %v\n%s", err, out)
	}
	if _, err := LoadNoRepair(good); err != nil {
		t.Fatalf("the Go contract refuses it: %v", err)
	}
	raw, err := os.ReadFile(good)
	if err != nil {
		t.Fatal(err)
	}
	from := `"data_dir": "` + testQdrantDir + `"`
	for name, to := range map[string]string{
		"relative":       `"data_dir": "data2/qdrant"`,
		"a space":        `"data_dir": "/data 2/qdrant"`,
		"a number":       `"data_dir": 7`,
		"a shell dollar": `"data_dir": "/data2/$HOME"`,
	} {
		doc := bytes.Replace(raw, []byte(from), []byte(to), 1)
		if bytes.Equal(doc, raw) {
			t.Fatalf("%s: test setup", name)
		}
		p := filepath.Join(dir, strings.ReplaceAll(name, " ", "-")+".json")
		if err := os.WriteFile(p, doc, 0o600); err != nil {
			t.Fatal(err)
		}
		if exec.Command(py, "-c", script, schema, p).Run() == nil {
			t.Errorf("%s: registry.json accepted it", name)
		}
		if _, err := LoadNoRepair(p); err == nil {
			t.Errorf("%s: the Go contract accepted it", name)
		}
	}
	// null is the schema's absent; the Go side reads it as absent too.
	doc := bytes.Replace(raw, []byte(from), []byte(`"data_dir": null`), 1)
	p := filepath.Join(dir, "null.json")
	if err := os.WriteFile(p, doc, 0o600); err != nil {
		t.Fatal(err)
	}
	if out, err := exec.Command(py, "-c", script, schema, p).CombinedOutput(); err != nil {
		t.Errorf("null refused by the schema: %s", out)
	}
	back, err := LoadNoRepair(p)
	if err != nil || back.Tenants["dev"].Stores.Qdrant.DataDir != "" {
		t.Errorf("null: err=%v data_dir=%q", err, back.Tenants["dev"].Stores.Qdrant.DataDir)
	}
}
