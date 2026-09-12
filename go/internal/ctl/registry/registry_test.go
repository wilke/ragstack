package registry

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"syscall"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/paths"
)

const goldenManifest = "../testdata/live-2026-09-10/tenants/manifest.tsv"

func TestProjectionMatchesLiveManifest(t *testing.T) {
	want, err := os.ReadFile(goldenManifest)
	if err != nil {
		t.Fatal(err)
	}
	got := ProjectManifest(LiveFixture())
	if !bytes.Equal(got, want) {
		t.Fatalf("projection differs from live manifest.tsv:\n got %q\nwant %q", got, want)
	}
	if !strings.HasPrefix(string(got), ManifestHeader+"\n") {
		t.Fatalf("header: %q", got)
	}
	rows := strings.Split(strings.TrimRight(string(got), "\n"), "\n")[1:]
	if strings.Join(rows, "|") != "lucid\t0\t24000|asm\t1\t24020|dev\t2\t24040|demo\t3\t24060" {
		t.Fatalf("rows: %q", rows)
	}
	if err := ReconcileManifest(LiveFixture(), want); err != nil {
		t.Fatalf("live manifest does not reconcile: %v", err)
	}
}

func TestReconcileManifestErrors(t *testing.T) {
	f := LiveFixture()
	if err := ReconcileManifest(f, []byte(ManifestHeader+"\nlucid\t0\t24000\nasm\t1\t24020\ndev\t2\t24040\ndemo\t3\t24060\nghost\t4\t24080\n")); !errors.Is(err, ErrManifestUnknownRows) || !strings.Contains(err.Error(), "ghost\t4\t24080") {
		t.Errorf("unknown rows: %v", err)
	}
	if err := ReconcileManifest(f, []byte("lucid\t0\t24000\nasm\t1\t24020\ndev\t2\t24040\ndemo\t9\t24060")); !errors.Is(err, ErrManifestMismatch) {
		t.Errorf("mismatch: %v", err)
	}
	if err := ReconcileManifest(f, []byte(ManifestHeader+"\nlucid\t0\t24000\n")); !errors.Is(err, ErrManifestMissingRows) || !strings.Contains(err.Error(), "asm, demo, dev") {
		t.Errorf("missing rows: %v", err)
	}
	if _, err := ParseManifest([]byte("lucid\tzero\t24000\n")); err == nil {
		t.Error("non-numeric row accepted")
	}
}

func TestAllocateSkipsTombstones(t *testing.T) {
	f := LiveFixture()
	if idx, base := Allocate(f); idx != 4 || base != 24080 {
		t.Fatalf("Allocate = %d/%d", idx, base)
	}
	f.Tombstones = append(f.Tombstones, Tombstone{ManifestName: "gone", Index: 7, Base: 24140, DecommissionedAt: "2026-09-10T00:00:00Z"})
	if idx, base := Allocate(f); idx != 8 || base != 24160 {
		t.Fatalf("Allocate with tombstone = %d/%d, want 8/24160 (never reuse)", idx, base)
	}
	delete(f.Tenants, "demo")
	if idx, _ := Allocate(f); idx != 8 {
		t.Fatalf("Allocate after delete = %d, want 8", idx)
	}
	if idx, base := Allocate(NewFleet("/rag")); idx != 0 || base != 24000 {
		t.Fatalf("empty fleet = %d/%d", idx, base)
	}
}

func TestValidate(t *testing.T) {
	f := LiveFixture()
	if err := f.Validate(); err != nil {
		t.Fatal(err)
	}
	dup := LiveFixture()
	dup.Tenants["dev"].Ports.Index = 3
	if err := dup.Validate(); err == nil {
		t.Error("duplicate index accepted")
	}
	bad := LiveFixture()
	bad.Tenants["dev"].Ports.Base = 1
	if err := bad.Validate(); err == nil {
		t.Error("base/index disagreement accepted")
	}
	tomb := LiveFixture()
	tomb.Tombstones = []Tombstone{{ManifestName: "x", Index: 2, Base: 24040}}
	if err := tomb.Validate(); err == nil {
		t.Error("tombstone sharing a live index accepted")
	}
	res := LiveFixture()
	res.Tenants["admin"] = NewTenant("admin", "admin")
	res.Tenants["admin"].Ports = paths.Block(9)
	if err := res.Validate(); err == nil {
		t.Error("reserved name accepted")
	}
}

func dirEntries(t *testing.T, dir string) []string {
	t.Helper()
	es, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	for _, e := range es {
		names = append(names, e.Name())
	}
	sort.Strings(names)
	return names
}

func TestSaveLoadRoundTripAndLockOrder(t *testing.T) {
	dir := t.TempDir()
	reg := filepath.Join(dir, "registry.json")
	var order []string
	lockTrace = func(p string) { order = append(order, filepath.Base(p)) }
	defer func() { lockTrace = nil }()

	f := LiveFixture()
	if err := Save(reg, f, "test"); err != nil {
		t.Fatal(err)
	}
	if f.Generation != 1 || f.UpdatedBy != "test" || f.UpdatedAt == "" {
		t.Fatalf("generation bookkeeping: %+v", f)
	}
	if strings.Join(order, ">") != "registry.json.lock>manifest.tsv.lock" {
		t.Fatalf("lock order = %v, want registry then manifest", order)
	}
	want := []string{"manifest.tsv", "manifest.tsv.lock", "registry.json", "registry.json.generation", "registry.json.lock"}
	if got := dirEntries(t, dir); strings.Join(got, ",") != strings.Join(want, ",") {
		t.Fatalf("dir after save = %v (tmp files left behind?)", got)
	}
	man, _ := os.ReadFile(filepath.Join(dir, "manifest.tsv"))
	golden, _ := os.ReadFile(goldenManifest)
	if !bytes.Equal(man, golden) {
		t.Fatalf("saved manifest differs from live golden")
	}
	st, _ := os.Stat(reg)
	if st.Mode().Perm() != modeRegistry {
		t.Errorf("registry mode %o", st.Mode().Perm())
	}

	got, err := Load(reg)
	if err != nil {
		t.Fatal(err)
	}
	a, _ := Marshal(f)
	b, _ := Marshal(got)
	if !bytes.Equal(a, b) {
		t.Fatalf("round trip differs:\n%s\n---\n%s", a, b)
	}
	// Every field is present (no omitempty on non-nullable fields).
	var raw map[string]any
	_ = json.Unmarshal(b, &raw)
	for _, k := range []string{"schema_version", "generation", "updated_at", "updated_by", "rag_root", "port_base", "port_stride", "display_order", "legacy_routes", "ctl", "images", "artifacts", "tenants", "tombstones"} {
		if _, ok := raw[k]; !ok {
			t.Errorf("fleet json missing %q", k)
		}
	}
	tj := raw["tenants"].(map[string]any)["dev"].(map[string]any)
	for _, k := range []string{"name", "manifest_name", "data_dir", "worktree", "ports", "stores", "ui", "settings", "secret_refs", "keys", "drift", "restart_pending", "last_ops", "adopted_at"} {
		if _, ok := tj[k]; !ok {
			t.Errorf("tenant json missing %q", k)
		}
	}
	// The contract makes nullable keys REQUIRED: present as null, never omitted.
	for _, k := range []string{"last_backup", "rollback_descriptor", "release_generation", "artifact_id", "adopted_at"} {
		v, ok := tj[k]
		if !ok {
			t.Errorf("nullable %q omitted (contract requires the key)", k)
		} else if v != nil {
			t.Errorf("nullable %q = %v, want null", k, v)
		}
	}
	if ui := tj["ui"].(map[string]any); ui["port"] != float64(8090) {
		t.Errorf("ui.port = %v", ui["port"])
	}
	if st := tj["stores"].(map[string]any); st["neo4j"].(map[string]any)["url"] != "bolt://localhost:24047" {
		t.Errorf("neo4j url = %v", st["neo4j"])
	}
	if st := raw["tenants"].(map[string]any)["demo"].(map[string]any)["stores"].(map[string]any); st["neo4j"].(map[string]any)["url"] != nil {
		t.Errorf("demo neo4j url should be null: %v", st["neo4j"])
	}

	// Second save bumps the generation and keeps the dir clean.
	if err := Save(reg, got, "test2"); err != nil {
		t.Fatal(err)
	}
	if got.Generation != 2 {
		t.Fatalf("generation = %d", got.Generation)
	}
	if e := dirEntries(t, dir); len(e) != 5 {
		t.Fatalf("dir after second save = %v", e)
	}
	var g Generation
	gb, _ := os.ReadFile(reg + ".generation")
	_ = json.Unmarshal(gb, &g)
	if g.Generation != 2 || len(g.ManifestSHA256) != 64 {
		t.Fatalf("generation record: %+v", g)
	}
}

func TestLoadRejectsSchemaAndUnknownFields(t *testing.T) {
	dir := t.TempDir()
	reg := filepath.Join(dir, "registry.json")
	f := LiveFixture()
	if err := Save(reg, f, "t"); err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(reg)
	if err := os.WriteFile(reg, bytes.Replace(b, []byte(`"schema_version": 1`), []byte(`"schema_version": 99`), 1), 0o660); err != nil {
		t.Fatal(err)
	}
	if _, err := Load(reg); !errors.Is(err, ErrSchema) {
		t.Errorf("schema: %v", err)
	}
	if err := os.WriteFile(reg, bytes.Replace(b, []byte(`"rag_root"`), []byte(`"rag_rooot"`), 1), 0o660); err != nil {
		t.Fatal(err)
	}
	if _, err := Load(reg); err == nil {
		t.Error("unknown field accepted (additionalProperties:false)")
	}
}

// TestLoadNeverWritesTheProjection is the regression for the truncation bug:
// a registry that knows ONE tenant, a live manifest that carries FOUR, and a
// plain read. Repair-on-read rewrote the manifest to the single row it knew,
// after which apptainer/new-tenant.sh would reissue index 0 — a live tenant's
// port block — to the next tenant created.
func TestLoadNeverWritesTheProjection(t *testing.T) {
	dir := t.TempDir()
	reg := filepath.Join(dir, "registry.json")
	one := NewFleet("/rag")
	dev := NewTenant("dev", "dev")
	*dev = *LiveFixture().Tenants["dev"]
	one.Tenants["dev"] = dev
	one.DisplayOrder = []string{"dev"}
	if err := Save(reg, one, "t"); err != nil {
		t.Fatal(err)
	}
	// The live manifest: four tenants, only one of which the registry knows.
	live, err := os.ReadFile(goldenManifest)
	if err != nil {
		t.Fatal(err)
	}
	manPath := filepath.Join(dir, "manifest.tsv")
	if err := os.WriteFile(manPath, live, 0o664); err != nil {
		t.Fatal(err)
	}
	before := statSnapshot(t, dir)

	f, diag, err := LoadWithDiagnostics(reg)
	if err != nil {
		t.Fatalf("a stale projection must not fail the read: %v", err)
	}
	if f == nil || len(f.Tenants) != 1 {
		t.Fatalf("fleet not returned: %+v", f)
	}
	if !diag.Stale() || !errors.Is(diag.ProjectionStale, ErrProjectionStale) {
		t.Fatalf("stale projection not reported: %+v", diag)
	}
	for _, want := range []string{manPath, reg} {
		if !strings.Contains(diag.ProjectionStale.Error(), want) {
			t.Errorf("diagnostic does not name %s: %v", want, diag.ProjectionStale)
		}
	}
	if got, _ := os.ReadFile(manPath); !bytes.Equal(got, live) {
		t.Fatalf("Load REWROTE the live manifest:\n got %q\nwant %q", got, live)
	}
	if after := statSnapshot(t, dir); after != before {
		t.Fatalf("Load wrote to the registry dir:\n before %s\n after  %s", before, after)
	}
	// A plain Load agrees, and still writes nothing.
	if _, err := Load(reg); err != nil {
		t.Fatal(err)
	}
	if after := statSnapshot(t, dir); after != before {
		t.Fatalf("Load wrote to the registry dir: %s", after)
	}
}

// TestRepairIsTheExplicitVerb: the repair still exists, it is just no longer
// reachable from a read.
func TestRepairIsTheExplicitVerb(t *testing.T) {
	dir := t.TempDir()
	reg := filepath.Join(dir, "registry.json")
	f := LiveFixture()
	if err := Save(reg, f, "t"); err != nil {
		t.Fatal(err)
	}
	// Hand-edit the manifest (the split-brain the projection guards against).
	if err := os.WriteFile(filepath.Join(dir, "manifest.tsv"), []byte(ManifestHeader+"\nlucid\t0\t24000\n# ghost\t9\t24180\n"), 0o664); err != nil {
		t.Fatal(err)
	}
	if ok, _ := ProjectionCurrent(reg, f); ok {
		t.Fatal("edited manifest reported current")
	}
	g, err := Repair(reg)
	if err != nil {
		t.Fatal(err)
	}
	man, _ := os.ReadFile(filepath.Join(dir, "manifest.tsv"))
	if !bytes.Equal(man, ProjectManifest(f)) {
		t.Fatalf("manifest not repaired: %q", man)
	}
	if g.Generation != 1 {
		t.Fatalf("repair bumped generation to %d", g.Generation)
	}
	// A missing generation record is repaired too, and stays at generation 1.
	if err := os.Remove(reg + ".generation"); err != nil {
		t.Fatal(err)
	}
	if _, err := Repair(reg); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(reg + ".generation"); err != nil {
		t.Fatal("generation record not recreated")
	}
	if _, diag, _ := LoadWithDiagnostics(reg); diag.Stale() {
		t.Fatalf("still stale after repair: %v", diag.ProjectionStale)
	}
}

// statSnapshot is "what the directory looks like", precisely enough that any
// write to any file in it shows up: name, size and mtime.
func statSnapshot(t *testing.T, dir string) string {
	t.Helper()
	es, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	var out []string
	for _, e := range es {
		info, err := e.Info()
		if err != nil {
			t.Fatal(err)
		}
		out = append(out, fmt.Sprintf("%s:%d:%d", e.Name(), info.Size(), info.ModTime().UnixNano()))
	}
	sort.Strings(out)
	return strings.Join(out, ",")
}

func TestSaveCrashLeavesNoTempAndOldRegistry(t *testing.T) {
	dir := t.TempDir()
	reg := filepath.Join(dir, "registry.json")
	f := LiveFixture()
	if err := Save(reg, f, "t"); err != nil {
		t.Fatal(err)
	}
	before, _ := os.ReadFile(reg)
	beforeMan, _ := os.ReadFile(filepath.Join(dir, "manifest.tsv"))

	// Crash between registry.json write and manifest projection.
	writeHook = func(final string) error {
		if filepath.Base(final) == "manifest.tsv" {
			return errors.New("simulated crash")
		}
		return nil
	}
	defer func() { writeHook = nil }()
	next := LiveFixture()
	next.Generation = f.Generation
	next.Tombstones = append(next.Tombstones, Tombstone{ManifestName: "gone", Index: 5, Base: 24100})
	if err := Save(reg, next, "t"); err == nil {
		t.Fatal("expected simulated crash")
	}
	if e := dirEntries(t, dir); len(e) != 5 {
		t.Fatalf("temp file left behind: %v", e)
	}
	afterMan, _ := os.ReadFile(filepath.Join(dir, "manifest.tsv"))
	if !bytes.Equal(beforeMan, afterMan) {
		t.Fatal("manifest changed despite crash before its rename")
	}
	// registry.json was already renamed (generation 2) → the projection is
	// stale. Load REPORTS that and leaves it alone; Repair fixes it.
	writeHook = nil
	got, diag, err := LoadWithDiagnostics(reg)
	if err != nil {
		t.Fatal(err)
	}
	if got.Generation != 2 {
		t.Fatalf("generation = %d", got.Generation)
	}
	if !errors.Is(diag.ProjectionStale, ErrProjectionStale) {
		t.Fatal("crash-stale projection not reported by Load")
	}
	got, err = Repair(reg)
	if err != nil {
		t.Fatal(err)
	}
	if ok, _ := ProjectionCurrent(reg, got); !ok {
		t.Fatal("projection not repaired by Repair")
	}

	// Crash before the registry rename: everything untouched.
	writeHook = func(final string) error { return errors.New("crash") }
	if err := Save(reg, got, "t"); err == nil {
		t.Fatal("expected crash")
	}
	after, _ := os.ReadFile(reg)
	if bytes.Equal(before, after) {
		t.Fatal("test setup: expected generation 2 registry on disk")
	}
	if e := dirEntries(t, dir); len(e) != 5 {
		t.Fatalf("temp file left behind: %v", e)
	}
}

// TestFixtureValidatesAgainstContract proves the Go types and the contract
// schema agree: the saved fixture must validate against
// contracts/ctl/schemas/registry.json. Needs python3 + jsonschema (the
// ragstack env has it); skipped where neither is available.
func TestFixtureValidatesAgainstContract(t *testing.T) {
	schema := "../../../../contracts/ctl/schemas/registry.json"
	if _, err := os.Stat(schema); err != nil {
		t.Skip("contract schema not present")
	}
	py := ""
	for _, c := range []string{"/rag/envs/ragstack/bin/python", "python3"} {
		if p, err := exec.LookPath(c); err == nil {
			if exec.Command(p, "-c", "import jsonschema").Run() == nil {
				py = p
				break
			}
		}
	}
	if py == "" {
		t.Skip("no python with jsonschema")
	}
	script := `
import json, sys
from jsonschema import Draft202012Validator
schema = json.load(open(sys.argv[1])); doc = json.load(open(sys.argv[2]))
errs = sorted(Draft202012Validator(schema).iter_errors(doc), key=lambda e: list(e.path))
for e in errs[:40]:
    print("/" + "/".join(str(p) for p in e.path), "->", e.message[:200])
sys.exit(1 if errs else 0)
`
	// Both shapes a registry is ever born in: the adopted live fleet, and an
	// EMPTY one straight out of NewFleet — the second is where the unset
	// image version/digest used to slip through, because only adopt patched
	// them in.
	for _, tc := range []struct {
		name  string
		fleet *Fleet
	}{
		{"live fixture", LiveFixture()},
		{"NewFleet", NewFleet("/rag")},
	} {
		t.Run(tc.name, func(t *testing.T) {
			reg := filepath.Join(t.TempDir(), "registry.json")
			if err := Save(reg, tc.fleet, "local:1000"); err != nil {
				t.Fatal(err)
			}
			out, err := exec.Command(py, "-c", script, schema, reg).CombinedOutput()
			if err != nil {
				t.Fatalf("does not validate against the contract:\n%s", out)
			}
		})
	}
}

// TestValidateContractAgreesWithTheSchema replays the hand-edited registries
// of TestValidateContractRejectsHandEdits through the REAL JSON schema: the Go
// mirror must reject exactly what the contract rejects, so a rule that drifts
// shows up here rather than as a daemon that accepts an invalid file.
func TestValidateContractAgreesWithTheSchema(t *testing.T) {
	schema := "../../../../contracts/ctl/schemas/registry.json"
	if _, err := os.Stat(schema); err != nil {
		t.Skip("contract schema not present")
	}
	py := ""
	for _, c := range []string{"/rag/envs/ragstack/bin/python", "python3"} {
		if p, err := exec.LookPath(c); err == nil {
			if exec.Command(p, "-c", "import jsonschema").Run() == nil {
				py = p
				break
			}
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
sys.exit(1 if errs else 0)
`
	dir := t.TempDir()
	reg := filepath.Join(dir, "registry.json")
	if err := Save(reg, LiveFixture(), "local:1000"); err != nil {
		t.Fatal(err)
	}
	valid, err := os.ReadFile(reg)
	if err != nil {
		t.Fatal(err)
	}
	edited := filepath.Join(dir, "edited.json")
	if err := exec.Command(py, "-c", script, schema, reg).Run(); err != nil {
		t.Fatalf("the fixture registry itself does not satisfy the schema")
	}
	for _, tc := range contractEditCases {
		t.Run(tc.name, func(t *testing.T) {
			doc := bytes.Replace(valid, []byte(tc.from), []byte(tc.to), 1)
			if bytes.Equal(doc, valid) {
				t.Fatalf("test setup: %q not found", tc.from)
			}
			if err := os.WriteFile(edited, doc, 0o660); err != nil {
				t.Fatal(err)
			}
			if err := exec.Command(py, "-c", script, schema, edited).Run(); err == nil {
				t.Fatalf("the JSON schema ACCEPTS %q but the Go mirror rejects it — the mirror is stricter than the contract", tc.name)
			}
		})
	}
}

// TestSaveRefusesAStaleGeneration: two writers that both loaded generation N
// must not both write N+1 — the second would silently discard the first, on a
// file whose whole purpose is being the single source of truth.
func TestSaveRefusesAStaleGeneration(t *testing.T) {
	dir := t.TempDir()
	reg := filepath.Join(dir, "registry.json")
	if err := Save(reg, LiveFixture(), "seed"); err != nil {
		t.Fatal(err)
	}
	a, err := Load(reg)
	if err != nil {
		t.Fatal(err)
	}
	b, err := Load(reg)
	if err != nil {
		t.Fatal(err)
	}
	if a.Generation != 1 || b.Generation != 1 {
		t.Fatalf("both copies should be at generation 1: %d/%d", a.Generation, b.Generation)
	}

	a.Tombstones = append(a.Tombstones, Tombstone{ManifestName: "gone", Index: 5, Base: 24100, DecommissionedAt: "2026-09-11T00:00:00Z"})
	if err := Save(reg, a, "writer-a"); err != nil {
		t.Fatal(err)
	}

	b.DisplayOrder = []string{"dev"}
	err = Save(reg, b, "writer-b")
	if !errors.Is(err, ErrStaleGeneration) {
		t.Fatalf("second writer was not refused: %v", err)
	}
	if !strings.Contains(err.Error(), "generation 2") || !strings.Contains(err.Error(), "loaded at 1") {
		t.Errorf("message names neither side: %v", err)
	}
	// A's write survived intact.
	got, err := Load(reg)
	if err != nil {
		t.Fatal(err)
	}
	if got.Generation != 2 || got.UpdatedBy != "writer-a" || len(got.Tombstones) != 1 {
		t.Fatalf("writer-a's generation was clobbered: gen=%d by=%s tombstones=%d", got.Generation, got.UpdatedBy, len(got.Tombstones))
	}
	// Reload-and-reapply is the documented recovery, and it works.
	got.DisplayOrder = []string{"dev"}
	if err := Save(reg, got, "writer-b"); err != nil {
		t.Fatal(err)
	}

	// A registry that vanished under a loaded fleet is stale too, never a
	// fresh start that drops every tenant.
	if err := os.Remove(reg); err != nil {
		t.Fatal(err)
	}
	if err := Save(reg, got, "writer-b"); !errors.Is(err, ErrStaleGeneration) {
		t.Fatalf("write onto a deleted registry: %v", err)
	}
}

// TestLockFilesAreGroupWritable: the lock files under /rag/data/tenants are
// shared between the ctl (svcbvbrc) and operator accounts. OpenFile's mode is
// masked by the umask — 0022 turns 0660 into 0640 — and the live
// manifest.tsv.lock already exists as 0644 wilke:cels, so Save as another
// member of the group died with a bare EACCES.
func TestLockFilesAreGroupWritable(t *testing.T) {
	old := syscall.Umask(0o022)
	defer syscall.Umask(old)

	dir := t.TempDir()
	reg := filepath.Join(dir, "registry.json")
	// A pre-existing lock, exactly as the live one: 0644, ours.
	if err := os.WriteFile(reg+".lock", nil, 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(reg+".lock", 0o644); err != nil { // WriteFile's mode is umasked too
		t.Fatal(err)
	}
	if err := Save(reg, LiveFixture(), "t"); err != nil {
		t.Fatal(err)
	}
	for _, p := range []string{reg + ".lock", filepath.Join(dir, "manifest.tsv.lock")} {
		st, err := os.Stat(p)
		if err != nil {
			t.Fatal(err)
		}
		if st.Mode().Perm() != modeLock {
			t.Errorf("%s is %#o, want %#o — the next writer in the group cannot open it",
				filepath.Base(p), st.Mode().Perm(), modeLock)
		}
	}
}

// TestUnusableLockNamesTheFix: when the lock cannot be opened at all, the
// error says which file, what it is, and the two commands that fix it.
func TestUnusableLockNamesTheFix(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root ignores file modes")
	}
	dir := t.TempDir()
	reg := filepath.Join(dir, "registry.json")
	if err := os.WriteFile(reg+".lock", nil, 0o400); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(reg+".lock", 0o400); err != nil {
		t.Fatal(err)
	}
	err := Save(reg, LiveFixture(), "t")
	if !errors.Is(err, ErrLockUnusable) {
		t.Fatalf("err = %v, want ErrLockUnusable", err)
	}
	for _, want := range []string{reg + ".lock", "mode 0400", "chmod 0660"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("message does not carry %q: %v", want, err)
		}
	}
	if _, statErr := os.Stat(reg); !os.IsNotExist(statErr) {
		t.Error("registry written despite an unusable lock")
	}
}

// contractEditCases are one-byte-level hand edits of a valid registry, the
// JSON pointer the Go error must name, and why. Replayed twice: through
// Fleet.ValidateContract (the Go mirror) and through the real JSON schema, so
// the two cannot drift.
var contractEditCases = []struct{ name, from, to, wantPtr, wantWhy string }{
	{"bad enum", `"state": "active"`, `"state": "runing"`, "/tenants/", `"runing" is not one of`},
	{"bad ui mode", `"mode": "external"`, `"mode": "iframe"`, "/ui/mode", `"iframe" is not one of`},
	{"bad owner", `"owner": "wilke"`, `"owner": "root"`, "/owner", `"root" is not one of`},
	{"unpinned digest wiped", `"digest": "sha256:0000`, `"digest": "0000`, "/images/", "does not match"},
	{"non-loopback store url", `"url": "http://localhost:6343"`, `"url": "http://10.0.0.1:6343"`, "/stores/qdrant/url", "does not match"},
	{"relative path", `"rag_root": "/rag"`, `"rag_root": "rag"`, "/rag_root", "does not match"},
	{"secret in settings", `"settings": {}`, `"settings": {"OPENAI_API_KEY": "sk-live"}`, "/settings/OPENAI_API_KEY", "SECRET-class"},
	{"lowercase setting", `"settings": {}`, `"settings": {"openai_base": "x"}`, "/settings/openai_base", "shell-identifier"},
	{"tenant name", `"name": "dev"`, `"name": "Dev"`, "/name", "does not match"},
	{"bad fingerprint", `"keys": []`, `"keys": [{"id":"k1","label":"l","role":"admin","tenant_string":"dev","fingerprint":"sha256:zz","created_at":"2026-09-11T00:00:00Z","created_by":"t","revoked_at":null,"effective":true}]`, "/keys/0/fingerprint", "does not match"},
}

// TestValidateContractRejectsHandEdits: the structural half of loading. Each
// case is one byte-level edit of a valid registry and the pointer the error
// must name.
func TestValidateContractRejectsHandEdits(t *testing.T) {
	dir := t.TempDir()
	reg := filepath.Join(dir, "registry.json")
	if err := Save(reg, LiveFixture(), "t"); err != nil {
		t.Fatal(err)
	}
	valid, err := os.ReadFile(reg)
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range contractEditCases {
		t.Run(tc.name, func(t *testing.T) {
			edited := bytes.Replace(valid, []byte(tc.from), []byte(tc.to), 1)
			if bytes.Equal(edited, valid) {
				t.Fatalf("test setup: %q not found in the fixture registry", tc.from)
			}
			if err := os.WriteFile(reg, edited, 0o660); err != nil {
				t.Fatal(err)
			}
			_, err := Load(reg)
			if err == nil {
				t.Fatal("hand-edited registry loaded")
			}
			if !strings.Contains(err.Error(), tc.wantPtr) || !strings.Contains(err.Error(), tc.wantWhy) {
				t.Fatalf("error does not say where or why (want %q + %q): %v", tc.wantPtr, tc.wantWhy, err)
			}
			if !strings.Contains(err.Error(), "contracts/ctl/schemas/registry.json") {
				t.Errorf("error does not name the contract: %v", err)
			}
		})
	}
	// And the untouched file still loads (the cases above are the only
	// difference, not a fixture that never validated).
	if err := os.WriteFile(reg, valid, 0o660); err != nil {
		t.Fatal(err)
	}
	if _, err := Load(reg); err != nil {
		t.Fatalf("valid registry refused: %v", err)
	}
}

// TestNewFleetIsBornContractValid: a fresh fleet must not need a caller to
// patch it (adopt used to fill in the image version/digest markers, so every
// other creator produced a registry that cannot be loaded).
func TestNewFleetIsBornContractValid(t *testing.T) {
	f := NewFleet("/rag")
	if f.Images.Qdrant.Version != UnpinnedVersion || f.Images.Qdrant.Digest != UnpinnedDigest {
		t.Errorf("qdrant image not marked unpinned: %+v", f.Images.Qdrant)
	}
	if f.Images.Elasticsearch.Version != UnpinnedVersion || f.Images.Elasticsearch.Digest != UnpinnedDigest {
		t.Errorf("elasticsearch image not marked unpinned: %+v", f.Images.Elasticsearch)
	}
	if f.Ctl.UIDist != "/rag/data/ctl/ui/dist" {
		t.Errorf("ctl.ui_dist = %q", f.Ctl.UIDist)
	}
	if alt := NewFleet("/tmp/rag-alt"); alt.Ctl.UIDist != "/tmp/rag-alt/data/ctl/ui/dist" {
		t.Errorf("ctl.ui_dist does not derive from rag_root: %q", alt.Ctl.UIDist)
	}
	// Everything except updated_at/updated_by, which Save is what fills in.
	saved := *f
	saved.UpdatedBy = "local:1000"
	if err := saved.ValidateContract(); err != nil {
		t.Fatalf("NewFleet is not contract-valid: %v", err)
	}
	dir := t.TempDir()
	reg := filepath.Join(dir, "registry.json")
	if err := Save(reg, f, "local:1000"); err != nil {
		t.Fatalf("a fresh fleet must be savable: %v", err)
	}
	if _, err := Load(reg); err != nil {
		t.Fatalf("a fresh fleet must be loadable: %v", err)
	}
}

// TestSaveRefusesARowLoadCannotReadBack is item 2 of the PR-A review.
//
// Save ran Validate (the allocator's invariants) but not ValidateContract
// (the shape Load enforces), so a writer could persist a registry the next
// Load refuses — adopt did exactly that, copying the API process's account
// into `owner`, whose enum is svcbvbrc|wilke. The file was written, the
// command reported success, and the fleet became unreadable at the next
// restart, with nothing left to say which write had done it.
func TestSaveRefusesARowLoadCannotReadBack(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "registry.json")
	f := LiveFixture()
	if err := Save(path, f, "local:test"); err != nil {
		t.Fatal(err)
	}
	before, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}

	f.Tenants["dev"].Owner = "someoneelse"
	err = Save(path, f, "local:test")
	if err == nil {
		t.Fatal("Save accepted an owner outside the contract's enum")
	}
	if !strings.Contains(err.Error(), "owner") {
		t.Errorf("the refusal must name the offending field: %v", err)
	}
	// And it refused BEFORE writing: the generation on disk is untouched.
	after, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(before, after) {
		t.Error("the refused Save still rewrote the registry")
	}
	if _, err := Load(path); err != nil {
		t.Errorf("the registry on disk is no longer loadable: %v", err)
	}

	// The check is on the document as WRITTEN — generation, updated_at and
	// updated_by are stamped by Save, so validating the caller's copy would
	// have failed on three required fields that are Save's own to fill in.
	f.Tenants["dev"].Owner = "svcbvbrc"
	if err := Save(path, f, "local:test"); err != nil {
		t.Fatalf("a contract-valid fleet must still save: %v", err)
	}
	if !KnownOwner("wilke") || !KnownOwner("svcbvbrc") || KnownOwner("root") {
		t.Errorf("KnownOwner disagrees with the enum %v", Owners())
	}
}
