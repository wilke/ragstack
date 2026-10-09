package settings

import (
	"reflect"
	"strings"
	"testing"
)

// surfaceCtx is clark on coconut, with mango and p3 allowed.
func surfaceCtx(image bool) SurfaceContext {
	return SurfaceContext{
		ImageRow:  image,
		DataDir:   "/rag/data/tenants/clark",
		Worktree:  "/rag/repos/tenants/clark",
		BindRoots: []string{"/rag/cache", "/scout/containers", "/rag/config"},
		Denied: []DeniedRoot{
			{Path: "/rag/config/ctl", Why: "ctl config"},
			{Path: "/rag/data/ctl", Why: "ctl state"},
			{Path: "/rag/backups/tenants", Why: "backups"},
			{Path: "/rag/data/tenants/asm-next", Why: "tenant asm-next's data dir"},
		},
		AllowedHosts: []string{"127.0.0.1", "::1", "localhost", "coconut", "mango.cels.anl.gov", "p3.theseed.org"},
	}
}

// Every executable-surface key has a row: a key added to executableSurface
// without one is refused with "no row", which this test turns into a failure
// so the omission is a decision rather than an accident.
func TestEveryExecutableSurfaceKeyHasARow(t *testing.T) {
	for _, k := range ExecutableSurfaceKeys() {
		for _, image := range []bool{false, true} {
			class, why, ok := SurfaceClassOf(k, image)
			if !ok {
				t.Errorf("%s: SurfaceClassOf says it is not executable-surface", k)
			}
			if class == SurfaceRefused && strings.Contains(why, "no row") {
				t.Errorf("%s (image=%v) has no row in the validation table", k, image)
			}
		}
	}
	if _, _, ok := SurfaceClassOf("LOG_LEVEL", false); ok {
		t.Error("a public key classified as surface")
	}
}

func TestSurfaceClassTable(t *testing.T) {
	cases := []struct {
		key   string
		image bool
		want  SurfaceClass
	}{
		{"PYTHONPATH", false, SurfaceRefused}, {"PATH", true, SurfaceRefused}, {"HF_HOME", false, SurfaceRefused},
		{"PORT", false, SurfaceRefused}, {"ROOT_PATH", true, SurfaceRefused},
		{"GOWE_WORKFLOW_CWL", true, SurfaceRefused}, {"GRAPH_EXTRACT_CWL", true, SurfaceRefused},
		{"COLLECTION_RESTORE_CWL", true, SurfaceRefused},
		{"GOWE_WORKFLOW_CWL", false, SurfaceCWL}, {"COLLECTION_RESTORE_CWL", false, SurfaceCWL},
		{"LLM_ENDPOINT", true, SurfaceURL}, {"NEO4J_URI", false, SurfaceURL}, {"REDIS_URL", false, SurfaceURL},
		{"EMBEDDING_ENDPOINTS", false, SurfaceURLList}, {"MODEL_URL_ALLOWLIST", true, SurfaceURLList},
		{"COLLECTIONS_FILE", true, SurfacePath}, {"USER_STORE_PATH", false, SurfacePath},
		{"PROMPT_TEMPLATES_FILE", true, SurfacePath},
		{"GOWE_IMAGE_DIRS", true, SurfaceImageDirs},
		{"QDRANT_COLLECTION_ROUTES", false, SurfaceRoutes}, {"ES_COLLECTION_ROUTES", true, SurfaceRoutes},
	}
	for _, c := range cases {
		got, _, ok := SurfaceClassOf(c.key, c.image)
		if !ok || got != c.want {
			t.Errorf("SurfaceClassOf(%s, image=%v) = %s, %v; want %s", c.key, c.image, got, ok, c.want)
		}
	}
}

func TestValidateSurface(t *testing.T) {
	type tc struct {
		key, value string
		image      bool
		want       string // the written value when accepted ("" = same as value)
		refuse     string // a substring of the refusal; "" = accepted
	}
	cases := []tc{
		// ---- refused always, with the reason.
		{key: "PYTHONPATH", value: "/rag/data/tenants/clark/py", refuse: "unit-owned"},
		{key: "PATH", value: "/usr/bin", refuse: "unit-owned"},
		{key: "HF_HOME", value: "/rag/cache", refuse: "unit-owned"},
		{key: "PORT", value: "8080", refuse: "argv"},
		{key: "ROOT_PATH", value: "/x", refuse: "gateway-owned"},
		{key: "GOWE_WORKFLOW_CWL", value: "/rag/repos/tenants/clark/cwl/x.cwl", image: true, refuse: "APIImageUnitEnv"},

		// ---- URLs: the acceptance cases first.
		{key: "LLM_ENDPOINT", value: "http://mango.cels.anl.gov:8003"},
		{key: "LLM_ENDPOINT", value: "http://MANGO.cels.anl.gov:8003/v1"},
		{key: "LLM_ENDPOINT", value: "http://evil.example/v1", refuse: "CTL_ALLOWED_ENDPOINT_HOSTS"},
		{key: "LLM_ENDPOINT", value: "http://u:p@mango.cels.anl.gov:8003", refuse: "userinfo"},
		{key: "LLM_ENDPOINT", value: "http://u@mango.cels.anl.gov:8003", refuse: "userinfo"},
		{key: "LLM_ENDPOINT", value: "http://mango.cels.anl.gov.evil.example/", refuse: "CTL_ALLOWED_ENDPOINT_HOSTS"},
		{key: "LLM_ENDPOINT", value: "http://mango.cels.anl.gov:8003/#x", refuse: "fragment"},
		{key: "LLM_ENDPOINT", value: "ftp://mango.cels.anl.gov/", refuse: "scheme"},
		{key: "LLM_ENDPOINT", value: "mango.cels.anl.gov:8003", refuse: "scheme"},
		{key: "LLM_ENDPOINT", value: "/v1/chat", refuse: "absolute URL"},
		{key: "LLM_ENDPOINT", value: "", refuse: "unset-surface"},
		{key: "LLM_ENDPOINT", value: " http://127.0.0.1:1", refuse: "whitespace"},
		{key: "WORKSPACE_URL", value: "https://p3.theseed.org/services/Workspace"},
		{key: "QDRANT_URL", value: "http://[::1]:6333"},
		{key: "QDRANT_URL", value: "http://127.0.0.2:6333", refuse: "CTL_ALLOWED_ENDPOINT_HOSTS"},
		{key: "NEO4J_URI", value: "bolt://localhost:7687"},
		{key: "NEO4J_URI", value: "neo4j+s://localhost:7687"},
		{key: "NEO4J_URI", value: "http://localhost:7474", refuse: "scheme"},
		{key: "REDIS_URL", value: "rediss://localhost:6380/0"},
		{key: "REDIS_URL", value: "redis://:pw@localhost:6379", refuse: "userinfo"},
		{key: "GOWE_URL", value: "redis://localhost:1", refuse: "scheme"},

		// ---- URL lists: both forms, written back in the comma form.
		{key: "EMBEDDING_ENDPOINTS", value: "http://127.0.0.1:9001, http://localhost:9002",
			want: "http://127.0.0.1:9001,http://localhost:9002"},
		{key: "EMBEDDING_ENDPOINTS", value: `["http://127.0.0.1:9001","http://mango.cels.anl.gov:9002"]`,
			want: "http://127.0.0.1:9001,http://mango.cels.anl.gov:9002"},
		{key: "EMBEDDING_ENDPOINTS", value: "http://127.0.0.1:9001,http://evil.example:9", refuse: "evil.example"},
		{key: "MODEL_URL_ALLOWLIST", value: "http://localhost,http://127.0.0.1", want: "http://localhost,http://127.0.0.1"},
		{key: "MODEL_URL_ALLOWLIST", value: ",,", refuse: "empty"},
		{key: "MODEL_URL_ALLOWLIST", value: `["http://localhost",1]`, refuse: "array of strings"},

		// ---- paths.
		{key: "COLLECTIONS_FILE", value: "/rag/config/collections/clark.json"},
		{key: "COLLECTIONS_FILE", value: "/rag/data/tenants/clark/config/collections.json"},
		{key: "PROMPT_TEMPLATES_FILE", value: "/rag/data/tenants/asm-next/secrets.env", refuse: "credential file"},
		{key: "PROMPT_TEMPLATES_FILE", value: "/rag/data/tenants/asm-next/config/prompts.yaml", refuse: "asm-next"},
		{key: "COLLECTIONS_FILE", value: "/rag/config/ctl/ctl-secrets.env", refuse: "credential file"},
		{key: "COLLECTIONS_FILE", value: "/rag/config/ctl/registry-notes.json", refuse: "ctl config"},
		{key: "COLLECTIONS_FILE", value: "/rag/data/tenants/clark/config/secrets.env", refuse: "credential file"},
		{key: "INGEST_ROOT", value: "relative/dir", refuse: "not absolute"},
		{key: "INGEST_ROOT", value: "/rag/data/tenants/clark/../asm-next", refuse: "not clean"},
		{key: "INGEST_ROOT", value: "/rag/data/tenants/clark", refuse: "outside every approved root"},
		{key: "INGEST_ROOT", value: "/home/wilke/docs", refuse: "outside every approved root"},
		{key: "INGEST_ROOT", value: "/rag/data/tenants/clark/ingest dir", refuse: "characters"},
		// A directory that CONTAINS a denied root is refused too: a bind of it
		// would hand the API the tree beneath.
		{key: "COLLECTION_MANIFEST_DIR", value: "/rag/backups", refuse: "overlaps"},
		{key: "USER_STORE_PATH", value: "/rag/data/tenants/clark/state/users.db"},
		{key: "USER_STORE_PATH", value: "/rag/backups/tenants/clark/x.db", refuse: "backups"},
		{key: "DOI_ENRICHMENT_CACHE_DIR", value: "/rag/cache/doi/clark"},

		// ---- CWL on a worktree row: the worktree or the data dir.
		{key: "GOWE_WORKFLOW_CWL", value: "/rag/repos/tenants/clark/cwl/pdf-ingest-scatter.cwl"},
		{key: "GRAPH_EXTRACT_CWL", value: "/rag/config/cwl/graph.cwl", refuse: "outside every approved root"},

		// ---- GOWE_IMAGE_DIRS: under a bind root, each entry.
		{key: "GOWE_IMAGE_DIRS", value: "/scout/containers/ragstack, /scout/containers/ragstack-dev",
			want: "/scout/containers/ragstack,/scout/containers/ragstack-dev"},
		{key: "GOWE_IMAGE_DIRS", value: "/scout/containers/x,/home/wilke/images", refuse: "CTL_API_BIND_ROOTS"},
		{key: "GOWE_IMAGE_DIRS", value: "/rag/data/tenants/clark/images", refuse: "CTL_API_BIND_ROOTS"},
		{key: "GOWE_IMAGE_DIRS", value: "/rag/config/ctl/images", refuse: "ctl config"},

		// ---- routing tables.
		{key: "QDRANT_COLLECTION_ROUTES", value: `{"ragstack_sfr_semantic_full":"http://localhost:6333"}`},
		{key: "ES_COLLECTION_ROUTES", value: `{}`},
		{key: "QDRANT_COLLECTION_ROUTES", value: `{"Bad Name":"http://localhost:6333"}`, refuse: "physical store name"},
		{key: "QDRANT_COLLECTION_ROUTES", value: `{"c":"http://evil.example:6333"}`, refuse: "CTL_ALLOWED_ENDPOINT_HOSTS"},
		{key: "QDRANT_COLLECTION_ROUTES", value: `{"c":"http://a:b@localhost:6333"}`, refuse: "userinfo"},
		{key: "ES_COLLECTION_ROUTES", value: `{"c":9200}`, refuse: "JSON object"},
		{key: "ES_COLLECTION_ROUTES", value: `["http://localhost:9200"]`, refuse: "JSON object"},
		{key: "ES_COLLECTION_ROUTES", value: `{"c":"http://localhost:9200"} {}`, refuse: "trailing"},
	}
	for _, c := range cases {
		got, err := ValidateSurface(c.key, c.value, surfaceCtx(c.image))
		switch {
		case c.refuse == "" && err != nil:
			t.Errorf("%s=%q (image=%v): refused: %v", c.key, c.value, c.image, err)
		case c.refuse != "" && err == nil:
			t.Errorf("%s=%q (image=%v): accepted, want a refusal mentioning %q", c.key, c.value, c.image, c.refuse)
		case c.refuse != "" && !strings.Contains(err.Error(), c.refuse):
			t.Errorf("%s=%q (image=%v): refusal %q does not mention %q", c.key, c.value, c.image, err, c.refuse)
		case c.refuse == "":
			want := c.want
			if want == "" {
				want = c.value
			}
			if got != want {
				t.Errorf("%s=%q: writes %q, want %q", c.key, c.value, got, want)
			}
		}
		if err != nil && !strings.Contains(err.Error(), c.key) {
			t.Errorf("%s: the refusal does not name the key: %v", c.key, err)
		}
	}
}

// The default allowlist is loopback and this host, and nothing else; a
// configured one is parsed strictly.
func TestAllowedHosts(t *testing.T) {
	if got := DefaultAllowedHosts("Coconut"); !reflect.DeepEqual(got, []string{"127.0.0.1", "::1", "localhost", "coconut"}) {
		t.Errorf("default = %v", got)
	}
	got, err := ParseAllowedHosts(" mango.cels.anl.gov,P3.theseed.org,[::1],10.0.0.5 ", "coconut")
	if err != nil || !reflect.DeepEqual(got, []string{"mango.cels.anl.gov", "p3.theseed.org", "::1", "10.0.0.5"}) {
		t.Errorf("parsed = %v, %v", got, err)
	}
	for _, bad := range []string{"http://mango", "mango:8003", "*.anl.gov", "a,,b", "mango/x", "u@mango"} {
		if _, err := ParseAllowedHosts(bad, "coconut"); err == nil {
			t.Errorf("ParseAllowedHosts(%q) accepted a malformed allowlist", bad)
		}
	}
	// A configured list REPLACES the default: loopback is not implied.
	got, _ = ParseAllowedHosts("mango.cels.anl.gov", "coconut")
	if !reflect.DeepEqual(got, []string{"mango.cels.anl.gov"}) {
		t.Errorf("a configured list = %v", got)
	}
	t.Setenv(EnvAllowedEndpointHosts, "")
	if hosts, err := AllowedHostsFrom("p3.theseed.org"); err != nil || !reflect.DeepEqual(hosts, []string{"p3.theseed.org"}) {
		t.Errorf("the ctl.env fallback = %v, %v", hosts, err)
	}
	t.Setenv(EnvAllowedEndpointHosts, "mango.cels.anl.gov")
	if hosts, _ := AllowedHostsFrom("p3.theseed.org"); !reflect.DeepEqual(hosts, []string{"mango.cels.anl.gov"}) {
		t.Errorf("the environment must win over ctl.env: %v", hosts)
	}
}

func TestSurfaceURLHosts(t *testing.T) {
	cases := map[string][]string{
		"LLM_ENDPOINT=http://Mango.cels.anl.gov:8003":                    {"mango.cels.anl.gov"},
		"EMBEDDING_ENDPOINTS=http://a:1,http://b:2":                      {"a", "b"},
		`QDRANT_COLLECTION_ROUTES={"x":"http://h1:1","y":"http://h2:2"}`: {"h1", "h2"},
		"LOG_LEVEL=http://not-a-url-key":                                 nil,
		"LLM_ENDPOINT=":                                                  nil,
		"LLM_ENDPOINT=not a url":                                         nil,
	}
	for kv, want := range cases {
		k, v, _ := strings.Cut(kv, "=")
		if got := SurfaceURLHosts(k, v); !reflect.DeepEqual(got, want) {
			t.Errorf("SurfaceURLHosts(%s) = %v, want %v", kv, got, want)
		}
	}
}
