package registry

import "github.com/ragstack/ragstack/internal/ctl/paths"

// sha256 of the empty string — a placeholder for the fixture's file hashes
// (the contract requires a 64-hex value; adopt fills in the real one).
const emptySHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

// LiveFixture is the four coconut tenants as they stood on 2026-09-10 (the
// facts in the plan's Registry section and testdata/live-2026-09-10). It is
// the reference the goldens are asserted against: the manifest projection,
// the nginx maps and the display order must reproduce the live files
// byte-for-byte. Adopt --preview reconciles its findings against this shape.
// Code tags/SHAs, file hashes and key ledgers are placeholders until adopt
// runs — nothing here is a secret.
func LiveFixture() *Fleet {
	f := NewFleet("/rag")
	f.DisplayOrder = []string{"dev", "demo", "lucid-next", "asm-next"}
	f.LegacyRoutes = []LegacyRoute{
		{Name: "lucid", API: 8010, UI: 5175, Readonly: true, Status: "active", Note: "customer org, isolated qdrant2 (:6343); no process listening as of 2026-08-25 (502)"},
		{Name: "asm", API: 8000, UI: 5173, Readonly: true, Status: "active", Note: "ragstack_sfr_tok256, the long-running prod explorer; no process listening as of 2026-08-25 (502)"},
	}
	f.Ctl = Ctl{Port: paths.CtlPort, UIDist: "/rag/data/ctl/ui/dist", GatewayEnabled: false}
	// Image version/digest keep NewFleet's "unpinned" markers until
	// `fleet image list` / adopt records the real ones.
	r := paths.NewRoots("/rag", paths.Overrides{})
	add := func(name, man string, idx, uiPort int) *Tenant {
		tp := paths.TenantPaths(r, name, man)
		t := NewTenant(name, man)
		t.DataDir, t.Worktree, t.PythonEnv = tp.DataDir, tp.Worktree, "/rag/envs/ragstack"
		t.Code = Code{Tag: "untracked"} // hand-started from a checkout; adopt records git describe
		t.Ports = paths.Block(idx)
		t.API = API{Bind: "0.0.0.0", PidFile: tp.PidFile, Log: tp.APILog}
		t.UI = UI{Mode: UIModeExternal, Port: NullPort(uiPort), Base: "/ragstack/" + name + "/ui/"}
		t.Supervisor, t.Owner, t.State, t.DesiredBoot, t.EnvLayout = "manual", "wilke", "active", "disabled", "legacy"
		t.EnvFileSHA256 = emptySHA256
		t.Identity = Identity{Provider: "bvbrc"}
		f.Tenants[name] = t
		return t
	}
	none := Capabilities{}
	lucid := add("lucid-next", "lucid", 0, 5211)
	lucid.Stores.Qdrant = Qdrant{URL: "http://localhost:6343", Instance: "qdrant2", Ownership: OwnershipShared, Capabilities: none, ExtraEnv: map[string]string{}}
	lucid.Stores.Elasticsearch = Elasticsearch{URL: "http://localhost:24003", Instance: "elasticsearch-lucid", SIF: "/rag/apptainer/images/elasticsearch.sif",
		Ownership: OwnershipExclusive, Capabilities: none, Heap: "2g", ProvisionHeap: "2g", PathRepo: "/usr/share/elasticsearch/snapshots", ExtraEnv: map[string]string{}}
	lucid.Identity.AdminSubjectsCount = 2

	asm := add("asm-next", "asm", 1, 5212)
	asm.Stores.Qdrant = Qdrant{URL: "http://localhost:6333", Instance: "qdrant", Ownership: OwnershipShared, Capabilities: none, ExtraEnv: map[string]string{}}
	asm.Stores.Elasticsearch = Elasticsearch{URL: "http://localhost:9200", Instance: "elasticsearch", Ownership: OwnershipShared, Capabilities: none, ExtraEnv: map[string]string{}}
	asm.Identity.AdminSubjectsCount = 3

	dev := add("dev", "dev", 2, 8090)
	dev.Stores.Qdrant = Qdrant{URL: "http://localhost:24041", Instance: "qdrant-dev", SIF: "/rag/apptainer/images/qdrant.sif", Ownership: OwnershipExclusive, Capabilities: none, ExtraEnv: map[string]string{}}
	dev.Stores.Elasticsearch = Elasticsearch{URL: "http://localhost:24043", Instance: "elasticsearch-dev", SIF: "/rag/apptainer/images/elasticsearch.sif",
		Ownership: OwnershipExclusive, Capabilities: none, Heap: "1g", ProvisionHeap: "512m", PathRepo: "/usr/share/elasticsearch/snapshots", ExtraEnv: map[string]string{}}
	dev.Stores.Neo4j = Neo4j{Ownership: OwnershipExternal, URL: "bolt://localhost:24047"}
	dev.Drift = []Drift{{Code: "es_heap_drift", Level: "warn", Field: "ES_JAVA_OPTS", Expected: "512m", Actual: "1g", ObservedAt: "2026-09-10T00:00:00Z", Note: "live heap differs from provision.env"}}
	dev.SecretsFileSHA256 = emptySHA256
	dev.Identity.AdminSubjectsCount = 2

	demo := add("demo", "demo", 3, 5210)
	demo.Stores.Qdrant = Qdrant{URL: "http://localhost:6333", Instance: "qdrant", Ownership: OwnershipShared, Capabilities: none, ExtraEnv: map[string]string{}}
	demo.Stores.Elasticsearch = Elasticsearch{URL: "http://localhost:9200", Instance: "elasticsearch", Ownership: OwnershipShared, Capabilities: none, ExtraEnv: map[string]string{}}
	demo.Stores.DormantProvisionedDirs = true
	demo.ExternalRefs = []ExternalRef{{Key: "COLLECTIONS_FILE", Path: "/rag/config/demo.collections.json"}}
	demo.SecretsFileSHA256 = emptySHA256
	demo.Identity.AdminSubjectsCount = 1
	return f
}
