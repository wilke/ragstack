package adopt

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/doctor"
	"github.com/ragstack/ragstack/internal/ctl/hostfacts"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

const fixtureDir = "../testdata/live-2026-09-10"

// live is the four tenants as they ran on coconut on 2026-09-10, with the
// arguments the operator passes to `adopt` for each.
var live = []struct {
	name, manifest, dataDirName string
	uiPort                      int
}{
	{"lucid-next", "lucid", "lucid", 5211},
	{"asm-next", "asm", "asm", 5212},
	{"dev", "dev", "dev", 8090},
	{"demo", "demo", "demo", 5210},
}

// materialize rebuilds the captured tenant trees under a temporary /rag: the
// directories and files of each tree.txt, with the real config files copied
// in and their absolute /rag paths rewritten to the temporary root, so the
// "is this path inside the data dir" decisions are made on real paths.
func materialize(t *testing.T, ragRoot string) paths.Roots {
	t.Helper()
	roots := paths.NewRoots(ragRoot, paths.Overrides{})
	mkdir := func(p string) {
		if err := os.MkdirAll(p, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	mkdir(roots.DataDir)
	mkdir(roots.ReposDir)
	mkdir(roots.ImagesDir)
	mkdir(filepath.Join(roots.ProxyDir, "snippets"))
	for _, sif := range []string{"qdrant.sif", "elasticsearch.sif"} {
		if err := os.WriteFile(filepath.Join(roots.ImagesDir, sif), []byte("sif"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	copyRewritten := func(src, dst string) {
		b, err := os.ReadFile(src)
		if err != nil {
			t.Fatal(err)
		}
		b = []byte(strings.ReplaceAll(string(b), "/rag/", ragRoot+"/"))
		if err := os.WriteFile(dst, b, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	copyRewritten(filepath.Join(fixtureDir, "tenants", "manifest.tsv"), filepath.Join(roots.DataDir, "manifest.tsv"))
	copyRewritten(filepath.Join(fixtureDir, "proxy", "snippets", "routes.conf"), filepath.Join(roots.ProxyDir, "snippets", "routes.conf"))
	mkdir(filepath.Join(roots.ProxyDir, "conf.d"))
	copyRewritten(filepath.Join(fixtureDir, "proxy", "conf.d", "00-maps.conf"), filepath.Join(roots.ProxyDir, "conf.d", "00-maps.conf"))

	for _, l := range live {
		src := filepath.Join(fixtureDir, "tenants", l.dataDirName)
		dst := filepath.Join(roots.DataDir, l.manifest)
		mkdir(dst)
		mkdir(filepath.Join(roots.ReposDir, l.name))
		tree, err := os.ReadFile(filepath.Join(src, "tree.txt"))
		if err != nil {
			t.Fatal(err)
		}
		for _, line := range strings.Split(strings.TrimSpace(string(tree)), "\n") {
			f := strings.SplitN(line, " ", 3)
			if len(f) < 3 || f[2] == "" {
				continue
			}
			rel := f[2]
			target := filepath.Join(dst, rel)
			if f[0] == "d" {
				mkdir(target)
				continue
			}
			mkdir(filepath.Dir(target))
			if _, err := os.Stat(filepath.Join(src, rel)); err == nil {
				copyRewritten(filepath.Join(src, rel), target)
				continue
			}
			// A file the capture listed but did not keep: one byte, so the
			// zero-byte-*.db rule is exercised only where a test asks for it.
			if err := os.WriteFile(target, []byte("x"), 0o600); err != nil {
				t.Fatal(err)
			}
		}
	}
	return roots
}

// liveHost is the recorded host with the gitdir facts adopt asks for: every
// worktree is a checkout outside the mirror (which is exactly what the four
// live tenants are today).
func liveHost(t *testing.T, roots paths.Roots) *hostfacts.Fake {
	t.Helper()
	h, err := hostfacts.LoadFake(fixtureDir)
	if err != nil {
		t.Fatal(err)
	}
	h.Gitdirs = map[string]hostfacts.Gitdir{}
	h.Describes = map[string]string{}
	for _, l := range live {
		wt := filepath.Join(roots.ReposDir, l.name)
		h.Gitdirs[wt] = hostfacts.Gitdir{Path: filepath.Join(wt, ".git"), Location: hostfacts.GitdirOutside}
		h.Describes[wt] = "v1.5.1-3-gabc1234"
	}
	return h
}

func previewLive(t *testing.T, roots paths.Roots, h hostfacts.Host) map[string]*registry.Tenant {
	t.Helper()
	at := time.Date(2026, 9, 11, 8, 0, 0, 0, time.UTC)
	out := map[string]*registry.Tenant{}
	for _, l := range live {
		tenant, findings, err := Preview(roots, l.name, Options{
			DataDir:      filepath.Join(roots.DataDir, l.manifest),
			Worktree:     filepath.Join(roots.ReposDir, l.name),
			ManifestName: l.manifest,
			UIPort:       l.uiPort,
			Host:         h,
			Now:          func() time.Time { return at },
		})
		if err != nil {
			t.Fatalf("preview %s: %v", l.name, err)
		}
		for _, f := range findings {
			if f.Level == model.LevelError {
				t.Errorf("preview %s: unexpected red finding %s: %s", l.name, f.Code, f.Detail)
			}
		}
		out[l.name] = tenant
	}
	return out
}

// TestPreviewDev is the tenant with two exclusive stores, a live-vs-provision
// heap disagreement, an external graph store and the 13 inline comments that
// make its env file unparsable by systemd.
func TestPreviewDev(t *testing.T) {
	roots := materialize(t, t.TempDir())
	h := liveHost(t, roots)
	at := time.Date(2026, 9, 11, 8, 0, 0, 0, time.UTC)
	tenant, findings, err := Preview(roots, "dev", Options{
		DataDir:  filepath.Join(roots.DataDir, "dev"),
		Worktree: filepath.Join(roots.ReposDir, "dev"),
		UIPort:   8090, Host: h, Now: func() time.Time { return at },
	})
	if err != nil {
		t.Fatal(err)
	}
	if tenant.Ports.Index != 2 || tenant.Ports.API != 24040 {
		t.Errorf("ports = %+v, want index 2 / api 24040 (from manifest.tsv)", tenant.Ports)
	}
	if tenant.Stores.Qdrant.Ownership != registry.OwnershipExclusive || tenant.Stores.Elasticsearch.Ownership != registry.OwnershipExclusive {
		t.Errorf("stores = %s/%s, want exclusive/exclusive", tenant.Stores.Qdrant.Ownership, tenant.Stores.Elasticsearch.Ownership)
	}
	if tenant.Stores.Qdrant.Instance != "qdrant-dev" || tenant.Stores.Elasticsearch.Instance != "elasticsearch-dev" {
		t.Errorf("instances = %q/%q", tenant.Stores.Qdrant.Instance, tenant.Stores.Elasticsearch.Instance)
	}
	if tenant.Stores.Elasticsearch.Heap != "1g" || tenant.Stores.Elasticsearch.ProvisionHeap != "512m" {
		t.Errorf("heap = %q live / %q provisioned, want 1g / 512m", tenant.Stores.Elasticsearch.Heap, tenant.Stores.Elasticsearch.ProvisionHeap)
	}
	if len(tenant.Drift) != 1 || tenant.Drift[0].Code != doctor.ESHeapDrift {
		t.Errorf("drift = %+v, want one es_heap_drift row", tenant.Drift)
	}
	if tenant.Stores.Elasticsearch.ExtraEnv["ES_JAVA_OPTS"] != "-Xms1g -Xmx1g" {
		t.Errorf("extra_env = %v, want the live ES_JAVA_OPTS", tenant.Stores.Elasticsearch.ExtraEnv)
	}
	if tenant.Stores.DormantProvisionedDirs {
		t.Error("dev uses both of its dedicated stores: nothing is dormant")
	}
	if tenant.Stores.Neo4j.Ownership != registry.OwnershipExternal || tenant.Stores.Neo4j.URL == "" {
		t.Errorf("neo4j = %+v, want external with the recorded bolt URL", tenant.Stores.Neo4j)
	}
	if n := countCode(findings, doctor.EnvNotSystemdParsable); n != 13 {
		t.Errorf("%d env_not_systemd_parsable findings, want the 13 inline comments", n)
	}
	if tenant.EnvLayout != "legacy" {
		t.Errorf("env_layout = %q, want legacy (API_KEYS lives in tenant.env)", tenant.EnvLayout)
	}
	if tenant.State != "active" || tenant.Owner != "wilke" || tenant.Supervisor != "manual" || tenant.DesiredBoot != "disabled" {
		t.Errorf("state/owner/supervisor/boot = %s/%s/%s/%s", tenant.State, tenant.Owner, tenant.Supervisor, tenant.DesiredBoot)
	}
	if tenant.Identity.Provider != "bvbrc" || tenant.Identity.AdminSubjectsCount != 2 {
		t.Errorf("identity = %+v, want bvbrc with 2 admin subjects", tenant.Identity)
	}
	if len(tenant.Keys) != 2 {
		t.Fatalf("%d key rows, want 2", len(tenant.Keys))
	}
	roles := map[string]int{}
	for _, k := range tenant.Keys {
		roles[k.Role]++
		if !regexp.MustCompile(`^sha256:[0-9a-f]{16}$`).MatchString(k.Fingerprint) {
			t.Errorf("key %s fingerprint %q is not the contract shape", k.ID, k.Fingerprint)
		}
		if k.TenantString != "dev" || k.CreatedBy != "adopt" || !k.Effective {
			t.Errorf("key row = %+v", k)
		}
	}
	if roles["admin"] != 1 || roles["user"] != 1 {
		t.Errorf("roles = %v, want one admin and one user", roles)
	}
	if got := tenant.Settings["LOG_LEVEL"]; got != "info" {
		t.Errorf("settings[LOG_LEVEL] = %q", got)
	}
	if _, leaked := tenant.Settings["API_KEYS"]; leaked {
		t.Error("a secret-class key reached settings")
	}
	if !hasSecretRef(tenant.SecretRefs, "API_KEYS", "tenant.env") || !hasSecretRef(tenant.SecretRefs, "NEO4J_PASSWORD", "secrets.env") {
		t.Errorf("secret_refs = %+v", tenant.SecretRefs)
	}
	if tenant.UI.Mode != registry.UIModeDev || tenant.UI.Port != 8090 || tenant.UI.Base != "/ragstack/dev/ui/" {
		t.Errorf("ui = %+v", tenant.UI)
	}
	if tenant.Code.Tag != "v1.5.1-3-gabc1234" || tenant.Code.SHA != "" {
		t.Errorf("code = %+v, want the describe output and a null sha", tenant.Code)
	}
	if countCode(findings, doctor.WorktreeOutsideMirror) != 1 {
		t.Errorf("expected one worktree_outside_mirror finding, got %+v", codes(findings))
	}
	rb := tenant.RollbackDescriptor
	if rb == nil || rb.Paths.DataDir != tenant.DataDir || rb.Paths.UpSh == "" {
		t.Fatalf("rollback descriptor = %+v", rb)
	}
	if len(rb.LaunchArgs) != 4 {
		t.Errorf("launch args = %+v, want api+ui+qdrant+es", rb.LaunchArgs)
	}
	if rb.Images.Qdrant == nil || rb.Images.Elasticsearch == nil {
		t.Error("rollback images: both SIFs should be recorded for a dedicated tenant")
	}
	if !containsFile(tenant.UnmanagedFiles, "config/tenant.env.orig") ||
		!containsFile(tenant.UnmanagedFiles, "state/backup-20260825") {
		t.Errorf("unmanaged_files = %v", tenant.UnmanagedFiles)
	}
	for _, f := range tenant.UnmanagedFiles {
		if strings.HasPrefix(f, "/") {
			t.Errorf("unmanaged file %q must be relative to the data dir", f)
		}
	}
}

// TestPreviewDemo is the shared-store tenant: nothing of its own is running,
// its provisioned store dirs are dormant, and its collections file lives
// outside the data dir.
func TestPreviewDemo(t *testing.T) {
	roots := materialize(t, t.TempDir())
	tenants := previewLive(t, roots, liveHost(t, roots))
	demo := tenants["demo"]
	if demo.Stores.Qdrant.Ownership != registry.OwnershipShared || demo.Stores.Elasticsearch.Ownership != registry.OwnershipShared {
		t.Errorf("stores = %s/%s, want shared/shared", demo.Stores.Qdrant.Ownership, demo.Stores.Elasticsearch.Ownership)
	}
	if demo.Stores.Qdrant.Instance != "" || demo.Stores.Elasticsearch.SIF != "" {
		t.Error("a shared store the ctl only probes has no instance and no SIF of its own")
	}
	if !demo.Stores.DormantProvisionedDirs {
		t.Error("demo has provisioned qdrant/elasticsearch dirs it does not use")
	}
	var collections string
	for _, r := range demo.ExternalRefs {
		if r.Key == "COLLECTIONS_FILE" {
			collections = r.Path
		}
	}
	if collections == "" || strings.HasPrefix(collections, demo.DataDir) {
		t.Errorf("external_refs = %+v, want COLLECTIONS_FILE outside the data dir", demo.ExternalRefs)
	}
	if demo.Stores.Elasticsearch.Heap != "" {
		t.Errorf("heap = %q: a shared store's heap is not this tenant's fact", demo.Stores.Elasticsearch.Heap)
	}
	if demo.Stores.Elasticsearch.ProvisionHeap != "512m" {
		t.Errorf("provision_heap = %q, want what provision.env records", demo.Stores.Elasticsearch.ProvisionHeap)
	}
	if demo.Identity.AdminSubjectsCount != 1 {
		t.Errorf("admin_subjects_count = %d, want 1", demo.Identity.AdminSubjectsCount)
	}
}

// TestPreviewLucidAndAsm covers the mixed tenant (shared qdrant2, dedicated
// ES) and the fully shared one, including the renamed manifest row.
func TestPreviewLucidAndAsm(t *testing.T) {
	roots := materialize(t, t.TempDir())
	tenants := previewLive(t, roots, liveHost(t, roots))

	lucid := tenants["lucid-next"]
	if lucid.ManifestName != "lucid" || lucid.Ports.Index != 0 {
		t.Errorf("lucid-next manifest/index = %s/%d, want lucid/0", lucid.ManifestName, lucid.Ports.Index)
	}
	if lucid.Stores.Qdrant.Ownership != registry.OwnershipShared || !strings.HasSuffix(lucid.Stores.Qdrant.URL, ":6343") {
		t.Errorf("lucid qdrant = %+v, want the shared :6343", lucid.Stores.Qdrant)
	}
	if lucid.Stores.Elasticsearch.Ownership != registry.OwnershipExclusive ||
		!strings.HasSuffix(lucid.Stores.Elasticsearch.URL, ":24003") ||
		lucid.Stores.Elasticsearch.Heap != "2g" ||
		lucid.Stores.Elasticsearch.Instance != "elasticsearch-lucid" {
		t.Errorf("lucid elasticsearch = %+v, want exclusive :24003 heap 2g", lucid.Stores.Elasticsearch)
	}
	if lucid.Stores.Elasticsearch.PathRepo == "" {
		t.Error("an exclusive ES must record its path.repo")
	}
	if len(lucid.Keys) != 3 {
		t.Errorf("%d lucid keys, want 3", len(lucid.Keys))
	}
	for _, k := range lucid.Keys {
		if k.Role != "admin" && k.Role != "user" {
			t.Errorf("key %s role %q is outside the contract enum", k.ID, k.Role)
		}
	}

	asm := tenants["asm-next"]
	if asm.Stores.Qdrant.Ownership != registry.OwnershipShared || asm.Stores.Elasticsearch.Ownership != registry.OwnershipShared {
		t.Errorf("asm-next stores = %s/%s, want shared/shared", asm.Stores.Qdrant.Ownership, asm.Stores.Elasticsearch.Ownership)
	}
	if asm.Identity.AdminSubjectsCount != 3 {
		t.Errorf("asm-next admin_subjects_count = %d, want 3", asm.Identity.AdminSubjectsCount)
	}
	if asm.Stores.Neo4j.URL != "" {
		t.Errorf("asm-next has no graph store: %+v", asm.Stores.Neo4j)
	}
}

// TestCommitAllReproducesTheLiveManifest is the PR-A go/no-go: adopting the
// four tenants in one batch writes a manifest.tsv byte-identical to the one
// new-tenant.sh maintains, and the registry it saves validates against the
// contract.
func TestCommitAllReproducesTheLiveManifest(t *testing.T) {
	ragRoot := t.TempDir()
	roots := materialize(t, ragRoot)
	tenants := previewLive(t, roots, liveHost(t, roots))
	before, err := os.ReadFile(roots.Manifest())
	if err != nil {
		t.Fatal(err)
	}
	regPath := roots.Registry()

	// A single adoption cannot reconcile with a four-row manifest.
	if err := Commit(regPath, tenants["dev"], CommitOptions{Roots: roots, UpdatedBy: "local:1000"}); err == nil {
		t.Fatal("committing one tenant against a four-row manifest must be refused")
	} else if !errors.Is(err, registry.ErrManifestUnknownRows) {
		t.Fatalf("refusal should name the unknown manifest rows, got: %v", err)
	}

	batch := []*registry.Tenant{tenants["lucid-next"], tenants["asm-next"], tenants["dev"], tenants["demo"]}
	if err := CommitAll(regPath, batch, CommitOptions{Roots: roots, UpdatedBy: "local:1000"}); err != nil {
		t.Fatal(err)
	}
	after, err := os.ReadFile(roots.Manifest())
	if err != nil {
		t.Fatal(err)
	}
	if string(after) != string(before) {
		t.Errorf("manifest projection changed the live file:\n--- before\n%s\n--- after\n%s", before, after)
	}

	f, err := registry.Load(regPath)
	if err != nil {
		t.Fatal(err)
	}
	if got, want := f.DisplayOrder, []string{"dev", "demo", "lucid-next", "asm-next"}; strings.Join(got, ",") != strings.Join(want, ",") {
		t.Errorf("display_order = %v, want the live routes.conf order %v", got, want)
	}
	if len(f.Tenants) != 4 || f.Generation != 1 {
		t.Errorf("registry has %d tenants at generation %d", len(f.Tenants), f.Generation)
	}
	if err := CommitAll(regPath, []*registry.Tenant{tenants["dev"]}, CommitOptions{Roots: roots}); err == nil {
		t.Error("re-adopting an existing tenant must be refused")
	}

	legacy := map[string]registry.LegacyRoute{}
	for _, r := range f.LegacyRoutes {
		legacy[r.Name] = r
	}
	if l := legacy["lucid"]; l.API != 8010 || l.UI != 5175 || !l.Readonly || l.Status != "active" {
		t.Errorf("legacy route lucid = %+v, want the live :8010/:5175 read-only row", l)
	}
	if l := legacy["asm"]; l.API != 8000 || l.UI != 5173 || !l.Readonly {
		t.Errorf("legacy route asm = %+v", l)
	}
	if len(legacy) != 2 {
		t.Errorf("legacy_routes = %+v, want only the two non-tenant gateway rows", f.LegacyRoutes)
	}

	assertNoSecretMaterial(t, regPath)
	validateRegistry(t, regPath)
}

// assertNoSecretMaterial is the canary: the saved registry must carry no API
// key, no secret value and no 64-hex string other than the two file hashes
// the contract requires.
func assertNoSecretMaterial(t *testing.T, regPath string) {
	t.Helper()
	b, err := os.ReadFile(regPath)
	if err != nil {
		t.Fatal(err)
	}
	text := string(b)
	for _, marker := range []string{"<REDACTED", "<GENERATED:", "BEGIN PRIVATE KEY", "sig="} {
		if strings.Contains(text, marker) {
			t.Errorf("registry carries %q — a secret-shaped value reached the file", marker)
		}
	}
	// Strip the two hash members the contract requires, then nothing of
	// sha256 length may remain.
	stripped := regexp.MustCompile(`"(env_file_sha256|secrets_file_sha256)": "[0-9a-f]{64}"`).ReplaceAllString(text, `"$1": ""`)
	stripped = regexp.MustCompile(`"digest": "sha256:[0-9a-f]{64}"`).ReplaceAllString(stripped, `"digest": ""`)
	if m := regexp.MustCompile(`[0-9a-fA-F]{64}`).FindString(stripped); m != "" {
		t.Errorf("registry carries an unexplained 64-hex string: %s…", m[:16])
	}
}

// validateRegistry runs the saved file through the contract schema.
func validateRegistry(t *testing.T, regPath string) {
	t.Helper()
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
	out, err := exec.Command(py, "-c", script, schema, regPath).CombinedOutput()
	if err != nil {
		t.Fatalf("the adopted registry does not validate against the contract:\n%s", out)
	}
}

// TestPreviewRefusesADisallowedStoreURL proves the SSRF guard is a red
// finding rather than a silently recorded URL.
func TestPreviewRefusesADisallowedStoreURL(t *testing.T) {
	ragRoot := t.TempDir()
	roots := materialize(t, ragRoot)
	envPath := filepath.Join(roots.DataDir, "dev", "config", "tenant.env")
	b, err := os.ReadFile(envPath)
	if err != nil {
		t.Fatal(err)
	}
	rewritten := strings.Replace(string(b), "QDRANT_URL=http://localhost:24041", "QDRANT_URL=http://evil.example.com:6333", 1)
	if err := os.WriteFile(envPath, []byte(rewritten), 0o600); err != nil {
		t.Fatal(err)
	}
	_, findings, err := Preview(roots, "dev", Options{
		DataDir: filepath.Join(roots.DataDir, "dev"), Worktree: filepath.Join(roots.ReposDir, "dev"),
		UIPort: 8090, Host: liveHost(t, roots),
	})
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, f := range findings {
		if f.Code == doctor.StoreURLDisallowed && f.Level == model.LevelError {
			found = true
		}
	}
	if !found {
		t.Errorf("expected a red store_url_disallowed finding, got %v", codes(findings))
	}
}

// TestUnmanagedFilesFindsEmptyDatabases covers the one rule the capture's
// tree.txt cannot express: a zero-byte *.db is an unmanaged leftover.
func TestUnmanagedFilesFindsEmptyDatabases(t *testing.T) {
	roots := materialize(t, t.TempDir())
	empty := filepath.Join(roots.DataDir, "dev", "state", "ragstack_empty.db")
	if err := os.WriteFile(empty, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	tenant, _, err := Preview(roots, "dev", Options{
		DataDir: filepath.Join(roots.DataDir, "dev"), Worktree: filepath.Join(roots.ReposDir, "dev"),
		UIPort: 8090, Host: liveHost(t, roots),
	})
	if err != nil {
		t.Fatal(err)
	}
	if !containsFile(tenant.UnmanagedFiles, "state/ragstack_empty.db") {
		t.Errorf("unmanaged_files = %v, want the zero-byte database", tenant.UnmanagedFiles)
	}
}

// TestPreviewValidatesItsArguments: a reserved name, a missing flag and a
// data dir outside the deployment root are refusals, not findings.
func TestPreviewValidatesItsArguments(t *testing.T) {
	roots := materialize(t, t.TempDir())
	cases := []struct {
		name string
		opts Options
	}{
		{"admin", Options{DataDir: filepath.Join(roots.DataDir, "dev"), Worktree: filepath.Join(roots.ReposDir, "dev")}},
		{"dev", Options{Worktree: filepath.Join(roots.ReposDir, "dev")}},
		{"dev", Options{DataDir: "/etc", Worktree: filepath.Join(roots.ReposDir, "dev")}},
		{"dev", Options{DataDir: filepath.Join(roots.DataDir, "dev"), Worktree: "../relative"}},
	}
	for i, c := range cases {
		c.opts.Host = liveHost(t, roots)
		if _, _, err := Preview(roots, c.name, c.opts); err == nil {
			t.Errorf("case %d (%s) was accepted", i, c.name)
		}
	}
}

// TestPortsFallBackToPORT proves a tenant with no manifest row is still
// adoptable when its env pins the API port.
func TestPortsFallBackToPORT(t *testing.T) {
	roots := materialize(t, t.TempDir())
	if err := os.Remove(roots.Manifest()); err != nil {
		t.Fatal(err)
	}
	tenant, _, err := Preview(roots, "dev", Options{
		DataDir: filepath.Join(roots.DataDir, "dev"), Worktree: filepath.Join(roots.ReposDir, "dev"),
		UIPort: 8090, Host: liveHost(t, roots),
	})
	if err != nil {
		t.Fatal(err)
	}
	if tenant.Ports.Index != 2 || tenant.Ports.Base != 24040 {
		t.Errorf("ports = %+v, want the block PORT=24040 implies", tenant.Ports)
	}
	// lucid has no PORT and no manifest row: it cannot be adopted blind.
	if _, _, err := Preview(roots, "lucid-next", Options{
		DataDir: filepath.Join(roots.DataDir, "lucid"), Worktree: filepath.Join(roots.ReposDir, "lucid-next"),
		ManifestName: "lucid", Host: liveHost(t, roots),
	}); err == nil {
		t.Error("a tenant with neither a manifest row nor a PORT must be refused")
	}
}

// TestTenantRowMarshalsWithNoOmittedMembers guards the contract's "every
// member required" rule at the Go level: the row must round-trip through JSON
// with the same member set the schema lists.
func TestTenantRowMarshalsWithNoOmittedMembers(t *testing.T) {
	roots := materialize(t, t.TempDir())
	tenants := previewLive(t, roots, liveHost(t, roots))
	b, err := json.Marshal(tenants["demo"])
	if err != nil {
		t.Fatal(err)
	}
	var row map[string]any
	if err := json.Unmarshal(b, &row); err != nil {
		t.Fatal(err)
	}
	for _, member := range []string{
		"name", "manifest_name", "data_dir", "worktree", "artifact_id", "code", "python_env",
		"ports", "api", "stores", "ui", "supervisor", "owner", "state", "desired_boot",
		"env_layout", "settings", "secret_refs", "env_file_sha256", "secrets_file_sha256",
		"identity", "keys", "service_accounts", "external_refs", "unmanaged_files", "drift",
		"restart_pending", "release_generation", "rollback_descriptor", "last_ops",
		"last_backup", "adopted_at",
	} {
		if _, ok := row[member]; !ok {
			t.Errorf("tenant row is missing the required member %q", member)
		}
	}
}

func countCode(findings []model.Finding, code string) int {
	n := 0
	for _, f := range findings {
		if f.Code == code {
			n++
		}
	}
	return n
}

func codes(findings []model.Finding) []string {
	out := make([]string, 0, len(findings))
	for _, f := range findings {
		out = append(out, fmt.Sprintf("%s/%s", f.Level, f.Code))
	}
	return out
}

func hasSecretRef(refs []registry.SecretRef, key, file string) bool {
	for _, r := range refs {
		if r.Key == key && r.File == file {
			return true
		}
	}
	return false
}

func containsFile(files []string, want string) bool {
	for _, f := range files {
		if f == want {
			return true
		}
	}
	return false
}

// TestBindOfIsEmptyWhenUnobserved is S2. `bindOf` used to default to
// "0.0.0.0" — recording the most exposed bind there is on the strength of NO
// evidence: no API process running, a process owned by another account, a
// command line without --host. render.Units then wrote `--host 0.0.0.0` into
// a systemd unit on an internet-reachable host. Unknown is now "".
func TestBindOfIsEmptyWhenUnobserved(t *testing.T) {
	for _, argv := range [][]string{
		nil,
		{},
		{"python", "-m", "uvicorn", "ragstack.api.main:app"},
		{"python", "-m", "uvicorn", "--host"}, // truncated
		{"python", "--host", "10.0.0.5"},      // not one of the known binds
	} {
		if got := bindOf(argv); got != "" {
			t.Errorf("bindOf(%v) = %q, want \"\" (unknown, not a guess)", argv, got)
		}
	}
	for argv, want := range map[*[]string]string{
		{"uvicorn", "--host", "127.0.0.1", "--port", "24040"}: "127.0.0.1",
		{"uvicorn", "--host", "0.0.0.0"}:                      "0.0.0.0",
		{"uvicorn", "--host", "localhost"}:                    "localhost",
		{"uvicorn", "--host", "::1"}:                          "::1",
	} {
		if got := bindOf(*argv); got != want {
			t.Errorf("bindOf(%v) = %q, want %q", *argv, got, want)
		}
	}
}

// TestPreviewRecordsNoBindWhenTheAPIIsDown: the end-to-end half of S2 — a
// tenant whose API is not running is adopted with an EMPTY bind, so the
// renderer applies its loopback default instead of republishing 0.0.0.0.
func TestPreviewRecordsNoBindWhenTheAPIIsDown(t *testing.T) {
	roots := materialize(t, t.TempDir())
	h := liveHost(t, roots)
	h.Ports = nil // nothing listening at all
	at := time.Date(2026, 9, 11, 8, 0, 0, 0, time.UTC)
	tenant, _, err := Preview(roots, "dev", Options{
		DataDir:  filepath.Join(roots.DataDir, "dev"),
		Worktree: filepath.Join(roots.ReposDir, "dev"),
		UIPort:   8090, Host: h, Now: func() time.Time { return at },
	})
	if err != nil {
		t.Fatal(err)
	}
	if tenant.API.Bind != "" {
		t.Errorf("api.bind = %q for an unobservable API, want \"\"", tenant.API.Bind)
	}
}

// TestRollbackArgvIsRedactedWithTheTenantsOwnSecrets is S17. The descriptor
// records the argv of every process the tenant runs, and an argv carries
// values (`--api-key <k>`, a DSN) no pattern can recognise but the tenant's
// own secret files name exactly. It used to go through the SEEDLESS
// settings.RedactText, so those values landed verbatim in registry.json.
func TestRollbackArgvIsRedactedWithTheTenantsOwnSecrets(t *testing.T) {
	roots := materialize(t, t.TempDir())
	h := liveHost(t, roots)
	// A secret whose shape nothing recognises: it is only a secret because
	// the tenant's own secrets.env says so.
	const canary = "zq7-opaque-value-nothing-matches"
	cfg := filepath.Join(roots.DataDir, "dev", "config", "secrets.env")
	b, err := os.ReadFile(cfg)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(cfg, append(b, []byte("\nTENANT_PG_PASSWORD="+canary+"\n")...), 0o600); err != nil {
		t.Fatal(err)
	}
	for i, p := range h.Ports {
		if p.Port == 24040 {
			h.Ports[i].Cmdline = []string{"python", "-m", "uvicorn", "--pg-password", canary,
				"--dsn", "postgresql://raguser:hunter2hunter2@localhost:5432/dev"}
		}
	}
	at := time.Date(2026, 9, 11, 8, 0, 0, 0, time.UTC)
	tenant, _, err := Preview(roots, "dev", Options{
		DataDir:  filepath.Join(roots.DataDir, "dev"),
		Worktree: filepath.Join(roots.ReposDir, "dev"),
		UIPort:   8090, Host: h, Now: func() time.Time { return at },
	})
	if err != nil {
		t.Fatal(err)
	}
	blob, err := json.Marshal(tenant.RollbackDescriptor)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(blob), canary) {
		t.Errorf("a secrets.env value reached the rollback descriptor: %s", blob)
	}
	if strings.Contains(string(blob), "hunter2hunter2") {
		t.Errorf("a URL credential reached the rollback descriptor: %s", blob)
	}
}
