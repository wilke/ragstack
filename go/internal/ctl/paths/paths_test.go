package paths

import (
	"path/filepath"
	"strings"
	"testing"
)

func TestValidateNameAccepts(t *testing.T) {
	for _, n := range []string{"a", "dev", "lucid-next", "asm-next", "t0", "a-b-c", strings.Repeat("a", 32)} {
		if err := ValidateName(n); err != nil {
			t.Errorf("ValidateName(%q) = %v, want nil", n, err)
		}
	}
}

func TestValidateNameRejects(t *testing.T) {
	bad := []string{
		"", "..", ".", "Acme", "DEV", "1acme", "-acme", "a_b", "a:b", "acme!", "a b",
		strings.Repeat("a", 33), "a/b", "a\n", "é",
	}
	for _, n := range bad {
		if err := ValidateName(n); err == nil {
			t.Errorf("ValidateName(%q) = nil, want error", n)
		}
	}
	for _, r := range Reserved {
		if r == "" || r != strings.ToLower(r) {
			t.Errorf("reserved word %q is not lowercase", r)
		}
		if err := ValidateName(r); err == nil || !strings.Contains(err.Error(), "reserved") {
			t.Errorf("ValidateName(reserved %q) = %v, want reserved error", r, err)
		}
	}
	// The plan's list, verbatim; keep this test honest if the list changes.
	want := "qdrant elasticsearch neo4j postgres redis embedding crossencoder faiss tenants manifest default public admin services health ragstack api ui gowe vaxpipe grafana sfr ctl"
	if got := strings.Join(Reserved, " "); got != want {
		t.Errorf("Reserved = %q\nwant %q", got, want)
	}
}

func TestSelftestRangeNeverOverlaps(t *testing.T) {
	if SelftestBase > SelftestEnd {
		t.Fatal("selftest range inverted")
	}
	for i := 0; i < 100; i++ {
		b := Block(i)
		if b.Base != PortBase+PortStride*i {
			t.Fatalf("Block(%d).Base = %d", i, b.Base)
		}
		lo, hi := b.Base, b.Base+PortStride-1
		if lo <= SelftestEnd && SelftestBase <= hi {
			t.Fatalf("block %d [%d,%d] overlaps selftest range [%d,%d]", i, lo, hi, SelftestBase, SelftestEnd)
		}
		if lo <= CtlPort && CtlPort <= hi {
			t.Fatalf("block %d [%d,%d] contains the ctl port %d", i, lo, hi, CtlPort)
		}
	}
	// The selftest range is itself a whole number of blocks at stride 20.
	if (SelftestEnd-SelftestBase+1)%PortStride != 0 {
		t.Fatalf("selftest range size %d is not a multiple of the stride", SelftestEnd-SelftestBase+1)
	}
}

func TestBlockOffsets(t *testing.T) {
	// The four live tenants (manifest.tsv 2026-09-10).
	for name, want := range map[string]int{"lucid": 24000, "asm": 24020, "dev": 24040, "demo": 24060} {
		idx := map[string]int{"lucid": 0, "asm": 1, "dev": 2, "demo": 3}[name]
		b := Block(idx)
		if b.API != want || b.QdrantHTTP != want+1 || b.QdrantGRPC != want+2 || b.ESHTTP != want+3 || b.ESTransport != want+4 || b.PG != want+5 {
			t.Errorf("%s: %+v", name, b)
		}
	}
	// new-tenant.sh under TENANT_PORT_BASE=41000 (the python test's pin).
	if b := BlockAt(41000, 20, 4); b.API != 41080 {
		t.Errorf("BlockAt(41000,20,4) = %+v", b)
	}
}

func TestRootsAndTenantPaths(t *testing.T) {
	r := NewRoots("/rag", Overrides{})
	if r.DataDir != "/rag/data/tenants" || r.ReposDir != "/rag/repos/tenants" || r.CtlConfigDir != "/rag/config/ctl" ||
		r.CtlStateDir != "/rag/data/ctl" || r.ImagesDir != "/rag/apptainer/images" || r.ProxyDir != "/rag/config/proxy" ||
		r.BackupsDir != "/rag/backups/tenants" {
		t.Fatalf("roots: %+v", r)
	}
	if r.Registry() != "/rag/data/tenants/registry.json" || r.Manifest() != "/rag/data/tenants/manifest.tsv" {
		t.Fatalf("registry/manifest: %s %s", r.Registry(), r.Manifest())
	}
	o := NewRoots("/rag/", Overrides{DataDir: "/tmp/x/tenants/"})
	if o.DataDir != "/tmp/x/tenants" || o.RagRoot != "/rag" || o.ReposDir != "/rag/repos/tenants" {
		t.Fatalf("overrides: %+v", o)
	}

	tp := TenantPaths(r, "lucid-next", "lucid")
	want := map[string]string{
		"DataDir":         "/rag/data/tenants/lucid",
		"TenantEnv":       "/rag/data/tenants/lucid/config/tenant.env",
		"SecretsEnv":      "/rag/data/tenants/lucid/config/secrets.env",
		"ProvisionEnv":    "/rag/data/tenants/lucid/config/provision.env",
		"StateDir":        "/rag/data/tenants/lucid/state",
		"ManifestsDir":    "/rag/data/tenants/lucid/manifests",
		"IngestDir":       "/rag/data/tenants/lucid/ingest",
		"LogsDir":         "/rag/data/tenants/lucid/logs",
		"QdrantStorage":   "/rag/data/tenants/lucid/qdrant/storage",
		"QdrantSnapshots": "/rag/data/tenants/lucid/qdrant/snapshots",
		"ESData":          "/rag/data/tenants/lucid/elasticsearch/data",
		"ESLogs":          "/rag/data/tenants/lucid/elasticsearch/logs",
		"ESConfig":        "/rag/data/tenants/lucid/elasticsearch/config",
		"ESSnapshots":     "/rag/data/tenants/lucid/elasticsearch/snapshots",
		"UIDist":          "/rag/data/tenants/lucid/ui/dist",
		"Worktree":        "/rag/repos/tenants/lucid-next",
		"PidFile":         "/rag/data/tenants/lucid/api-lucid-next.pid",
		"APILog":          "/rag/data/tenants/lucid/logs/api-lucid-next.log",
	}
	got := map[string]string{
		"DataDir": tp.DataDir, "TenantEnv": tp.TenantEnv, "SecretsEnv": tp.SecretsEnv, "ProvisionEnv": tp.ProvisionEnv,
		"StateDir": tp.StateDir, "ManifestsDir": tp.ManifestsDir, "IngestDir": tp.IngestDir, "LogsDir": tp.LogsDir,
		"QdrantStorage": tp.QdrantStorage, "QdrantSnapshots": tp.QdrantSnapshots, "ESData": tp.ESData, "ESLogs": tp.ESLogs,
		"ESConfig": tp.ESConfig, "ESSnapshots": tp.ESSnapshots, "UIDist": tp.UIDist, "Worktree": tp.Worktree,
		"PidFile": tp.PidFile, "APILog": tp.APILog,
	}
	for k, w := range want {
		if got[k] != w {
			t.Errorf("%s = %q, want %q", k, got[k], w)
		}
	}
	if d := TenantPaths(r, "dev", "").DataDir; d != "/rag/data/tenants/dev" {
		t.Errorf("manifestName default: %s", d)
	}
	if n := len(tp.ProvisionDirs()); n != 11 {
		t.Errorf("ProvisionDirs = %d entries, want 11 (new-tenant.sh TENANT_DIRS)", n)
	}
}

// TestProvisionDirsIncludesESSnapshots: the rendered ES unit binds
// <data_dir>/elasticsearch/snapshots as ES's path.repo, and apptainer refuses
// a bind whose source does not exist. Nothing else ever created that
// directory, so leaving it out of the provisioned set produced a tenant whose
// own unit could not start. (render/parity_test.go holds new-tenant.sh's
// TENANT_DIRS array to this slice, element by element.)
func TestProvisionDirsIncludesESSnapshots(t *testing.T) {
	tp := TenantPaths(NewRoots("/rag", Overrides{}), "dev", "dev")
	dirs := tp.ProvisionDirs()
	found := false
	for _, d := range dirs {
		if d == tp.ESSnapshots {
			found = true
		}
	}
	if !found {
		t.Errorf("ProvisionDirs %v lacks ESSnapshots %q — the ES unit binds it", dirs, tp.ESSnapshots)
	}
	if dirs[4] != tp.ESConfig || dirs[5] != tp.ESSnapshots {
		t.Errorf("ProvisionDirs order changed: [4]=%q [5]=%q", dirs[4], dirs[5])
	}
}

func TestSafePath(t *testing.T) {
	ok := []string{"/rag/data/tenants/dev", "/rag/repos/tenants/lucid-next", "/rag/a.b_c-d/e"}
	for _, p := range ok {
		if got, err := SafePath("/rag", p); err != nil || got != p {
			t.Errorf("SafePath(%q) = %q, %v", p, got, err)
		}
	}
	bad := []string{
		"", "rag/data", "/rag", "/rag/", "/rag/../etc", "/rag/data/../data", "/rag//data", "/rag/data/",
		"/ragx/data", "/etc/passwd", "/rag/data/$HOME", "/rag/data/a b", "/rag/data/a;b", "/rag/data/a\nb",
		"/rag/data/ünicode", "/rag/data/a'b", "/rag/data/a\"b",
	}
	for _, p := range bad {
		if _, err := SafePath("/rag", p); err == nil {
			t.Errorf("SafePath(%q) = nil error, want refusal", p)
		}
	}
	if _, err := SafePath("rel", "/rag/x"); err == nil {
		t.Error("relative root accepted")
	}
	// Root "/" accepts any clean absolute path except "/" itself (the
	// renderers use it as the charset/cleanliness gate).
	if got, err := SafePath("/", "/rag/data/tenants/acme"); err != nil || got != "/rag/data/tenants/acme" {
		t.Errorf("SafePath(/, …) = %q, %v", got, err)
	}
	if _, err := SafePath("/", "/"); err == nil {
		t.Error("SafePath(/, /) accepted")
	}
	// Clean-stability is the property that lets callers compare paths as strings:
	// a cleaned path passes, its uncleaned spelling does not.
	if got, err := SafePath("/rag", filepath.Join("/rag", "data", "..", "data")); err != nil || got != "/rag/data" {
		t.Errorf("cleaned path refused: %q %v", got, err)
	}
}
