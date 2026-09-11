package render

import (
	"bytes"
	"flag"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/settings"
)

var update = flag.Bool("update", false, "rewrite testdata/golden/*")

const goldenDir = "testdata/golden"

func golden(t *testing.T, name string, got []byte) {
	t.Helper()
	p := filepath.Join(goldenDir, name)
	if *update {
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, got, 0o644); err != nil {
			t.Fatal(err)
		}
		return
	}
	want, err := os.ReadFile(p)
	if err != nil {
		t.Fatalf("%s: %v (run with -update)", p, err)
	}
	if !bytes.Equal(want, got) {
		t.Errorf("%s differs from golden:\n%s", name, diffLines(string(want), string(got)))
	}
}

func managedTenant(name string, idx int, boot, uiMode string) *registry.Tenant {
	r := paths.NewRoots("/rag", paths.Overrides{})
	tp := paths.TenantPaths(r, name, name)
	none := registry.Capabilities{}
	t := registry.NewTenant(name, name)
	t.DataDir, t.Worktree, t.PythonEnv = tp.DataDir, tp.Worktree, "/rag/envs/ragstack"
	t.ArtifactID = "art-01J"
	t.Code = registry.Code{Tag: "v1.5.3", SHA: "d4c07047753d4a1b2c3d4e5f60718293a4b5c6d7"}
	t.Ports = paths.Block(idx)
	t.API = registry.API{Bind: "127.0.0.1", PidFile: tp.PidFile, Log: tp.APILog}
	t.Stores.Qdrant = registry.Qdrant{URL: "http://127.0.0.1:24081", Instance: registry.NullString("qdrant-" + name), SIF: "/rag/apptainer/images/qdrant.sif", Ownership: registry.OwnershipExclusive, Capabilities: none, ExtraEnv: map[string]string{}}
	t.Stores.Elasticsearch = registry.Elasticsearch{URL: "http://127.0.0.1:24083", Instance: registry.NullString("elasticsearch-" + name), SIF: "/rag/apptainer/images/elasticsearch.sif", Ownership: registry.OwnershipExclusive, Capabilities: none, Heap: "1g", ProvisionHeap: "1g", PathRepo: "/usr/share/elasticsearch/snapshots", ExtraEnv: map[string]string{}}
	t.UI = registry.UI{Mode: uiMode, Port: 5300, Base: "/ragstack/" + name + "/ui/"}
	if uiMode == registry.UIModeStatic {
		t.UI.Port = 0
	}
	t.Supervisor, t.Owner, t.State, t.DesiredBoot, t.EnvLayout = "systemd", "svcbvbrc", "active", boot, "managed"
	t.EnvFileSHA256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
	return t
}

func TestTenantEnvGoldens(t *testing.T) {
	tn := managedTenant("sandbox", 4, "enabled", registry.UIModeStatic)
	env, err := TenantEnv(tn, EnvOptions{DryRun: true})
	if err != nil {
		t.Fatal(err)
	}
	golden(t, "tenant.env.sqlite", env)
	pg, err := TenantEnv(tn, EnvOptions{DryRun: true, StoreKind: StorePostgres, PGHost: "dbhost", PGPort: "5433"})
	if err != nil {
		t.Fatal(err)
	}
	golden(t, "tenant.env.postgres", pg)

	// Real secrets land verbatim; dry-run never does.
	real, err := TenantEnv(tn, EnvOptions{Secrets: Secrets{KeyUser: "ku-0123456789", KeyAdmin: "ka-0123456789", PGPassword: "pw"}})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(real), `API_KEYS='["ku-0123456789","ka-0123456789"]'`) || strings.Contains(string(real), "<GENERATED") {
		t.Errorf("real-mode env: %s", real)
	}
	if strings.Contains(string(env), "ku-0123456789") || !strings.Contains(string(env), PlaceholderKeyUser) {
		t.Errorf("dry-run env leaked or lacks placeholder")
	}
	// Refusals.
	if _, err := TenantEnv(tn, EnvOptions{Secrets: Secrets{KeyUser: "it's", KeyAdmin: "x", PGPassword: "y"}}); err == nil {
		t.Error("quote in secret accepted")
	}
	if _, err := TenantEnv(tn, EnvOptions{StoreKind: StorePostgres}); err == nil {
		t.Error("postgres without host accepted")
	}
	if _, err := TenantEnv(tn, EnvOptions{StoreKind: StorePostgres, PGHost: "h;x"}); err == nil {
		t.Error("unsafe pg host accepted")
	}
	bad := managedTenant("Bad", 4, "enabled", registry.UIModeStatic)
	if _, err := TenantEnv(bad, EnvOptions{DryRun: true}); err == nil {
		t.Error("invalid name accepted")
	}
	bad = managedTenant("ok", 4, "enabled", registry.UIModeStatic)
	bad.DataDir = "/rag/data/tenants/../x"
	if _, err := TenantEnv(bad, EnvOptions{DryRun: true}); err == nil {
		t.Error("unclean data_dir accepted")
	}
}

func TestUpDownGoldens(t *testing.T) {
	tn := managedTenant("sandbox", 4, "enabled", registry.UIModeStatic)
	up, err := UpSh(tn, StoreOptions{Images: "/rag/apptainer/images", ESHeap: "2g"})
	if err != nil {
		t.Fatal(err)
	}
	golden(t, "up.sh", up)
	down, err := DownSh(tn)
	if err != nil {
		t.Fatal(err)
	}
	golden(t, "down.sh", down)
	if _, err := UpSh(tn, StoreOptions{Images: "/rag/apptainer/images", ESHeap: "lots"}); err == nil {
		t.Error("bad heap accepted")
	}
	if _, err := UpSh(tn, StoreOptions{}); err == nil {
		t.Error("missing images accepted")
	}
	if _, err := UpSh(tn, StoreOptions{Images: "/rag/$(id)"}); err == nil {
		t.Error("shell-expanding images path accepted")
	}
}

func TestUnitsGoldens(t *testing.T) {
	cfg := UnitConfig{}
	tn := managedTenant("sandbox", 4, "enabled", registry.UIModeStatic)
	units, err := Units(tn, cfg)
	if err != nil {
		t.Fatal(err)
	}
	names := make([]string, 0, len(units))
	for n := range units {
		names = append(names, n)
	}
	sort.Strings(names)
	if strings.Join(names, ",") != "ragstack-sandbox-api.service,ragstack-sandbox-es.service,ragstack-sandbox-qdrant.service,ragstack-sandbox.target" {
		t.Fatalf("units = %v", names)
	}
	for _, n := range names {
		golden(t, "units/"+n, units[n])
	}
	// The plan's must-haves, asserted independently of the golden.
	api := string(units["ragstack-sandbox-api.service"])
	for _, w := range []string{
		"ExecStartPre=/rag/bin/ragstack-ctl wait-ready sandbox --timeout 180",
		"KillMode=mixed", "ConditionPathIsMountPoint=/rag", "UMask=0002",
		"EnvironmentFile=/rag/data/tenants/sandbox/config/tenant.env",
		"EnvironmentFile=-/rag/data/tenants/sandbox/config/secrets.env",
		"Environment=PYTHONPATH=/rag/repos/tenants/sandbox/python",
		"Environment=RAGSTACK_GIT_TAG=v1.5.3", "Environment=RAGSTACK_GIT_SHA=d4c07047753d4a1b2c3d4e5f60718293a4b5c6d7",
		"ExecStart=/rag/envs/ragstack/bin/python -m uvicorn ragstack.api.main:app --host 127.0.0.1 --port 24080",
		"Requires=ragstack-sandbox-qdrant.service ragstack-sandbox-es.service",
		"After=ragstack-sandbox-qdrant.service ragstack-sandbox-es.service",
		"TimeoutStopSec=60", "StandardOutput=append:/rag/data/tenants/sandbox/logs/api-sandbox.log",
	} {
		if !strings.Contains(api, w) {
			t.Errorf("api unit lacks %q", w)
		}
	}
	es := string(units["ragstack-sandbox-es.service"])
	for _, w := range []string{
		"--bind /rag/data/tenants/sandbox/elasticsearch/data:/usr/share/elasticsearch/data",
		"--bind /rag/data/tenants/sandbox/elasticsearch/logs:/usr/share/elasticsearch/logs",
		"--bind /rag/data/tenants/sandbox/elasticsearch/config:/usr/share/elasticsearch/config",
		"--bind /rag/data/tenants/sandbox/elasticsearch/snapshots:/usr/share/elasticsearch/snapshots",
		"-Epath.repo=/usr/share/elasticsearch/snapshots", "-Ehttp.port=24083", "-Etransport.port=24084",
		`--env "ES_JAVA_OPTS=-Xms1g -Xmx1g"`, "KillMode=mixed", "TimeoutStopSec=120", "StartLimitBurst=3",
		"ConditionPathIsDirectory=/rag/data/tenants/sandbox/elasticsearch/data",
		"Environment=APPTAINER_CACHEDIR=/rag/data/ctl/apptainer/cache",
	} {
		if !strings.Contains(es, w) {
			t.Errorf("es unit lacks %q", w)
		}
	}
	if q := string(units["ragstack-sandbox-qdrant.service"]); !strings.Contains(q, "--env QDRANT__SERVICE__HTTP_PORT=24081 --env QDRANT__SERVICE__GRPC_PORT=24082") || !strings.Contains(q, "/bin/sh -c 'cd /qdrant && exec ./entrypoint.sh'") {
		t.Errorf("qdrant unit: %s", q)
	}
	target := string(units["ragstack-sandbox.target"])
	if !strings.Contains(target, "WantedBy=default.target") || !strings.Contains(target, "Wants=ragstack-sandbox-qdrant.service ragstack-sandbox-es.service ragstack-sandbox-api.service") {
		t.Errorf("target: %s", target)
	}

	// desired_boot=disabled → no [Install]; dev UI → ui unit.
	dev := managedTenant("sandbox", 4, "disabled", registry.UIModeDev)
	units, err = Units(dev, cfg)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(units["ragstack-sandbox.target"]), "WantedBy") {
		t.Error("disabled boot still WantedBy")
	}
	ui, ok := units["ragstack-sandbox-ui.service"]
	if !ok {
		t.Fatal("no ui unit for dev mode")
	}
	golden(t, "units/ragstack-sandbox-ui.service", ui)
	golden(t, "units/ragstack-sandbox.target.disabled", units["ragstack-sandbox.target"])
	if !strings.Contains(string(ui), "--base /ragstack/sandbox/ui/") || !strings.Contains(string(ui), "--port 5300") {
		t.Errorf("ui unit: %s", ui)
	}

	// Shared stores get no unit and the api has no Requires.
	shared := managedTenant("sandbox", 4, "enabled", registry.UIModeStatic)
	shared.Stores.Qdrant.Ownership = registry.OwnershipShared
	shared.Stores.Elasticsearch.Ownership = registry.OwnershipShared
	units, err = Units(shared, cfg)
	if err != nil {
		t.Fatal(err)
	}
	if len(units) != 2 || strings.Contains(string(units["ragstack-sandbox-api.service"]), "Requires=") {
		t.Errorf("shared stores: %d units", len(units))
	}

	// Refusals.
	for _, mut := range []func(*registry.Tenant){
		func(x *registry.Tenant) { x.DesiredBoot = "" },
		func(x *registry.Tenant) { x.Code.Tag = "v1 ;rm" },
		func(x *registry.Tenant) { x.Worktree = "/rag/repos/tenants/../x" },
		func(x *registry.Tenant) { x.Stores.Qdrant.SIF = "" },
		func(x *registry.Tenant) { x.Stores.Elasticsearch.Heap = "1x" },
		func(x *registry.Tenant) { x.UI.Mode = registry.UIModeDev; x.UI.Base = "/evil/" },
		func(x *registry.Tenant) { x.PythonEnv = "/rag/envs/a b" },
	} {
		x := managedTenant("sandbox", 4, "enabled", registry.UIModeStatic)
		mut(x)
		if _, err := Units(x, cfg); err == nil {
			t.Errorf("mutation accepted: %+v", x)
		}
	}
}

func TestNginxLiveFixture(t *testing.T) {
	f := registry.LiveFixture()
	f.Generation = 1
	out, err := NginxTenants(f, NginxConfig{})
	if err != nil {
		t.Fatal(err)
	}
	golden(t, "05-tenants.generated.conf", out)
	s := string(out)
	// The plan's semantic no-op for the four live tenants (PR-B).
	for _, w := range []string{
		`lucid       "127.0.0.1:8010";`, `asm         "127.0.0.1:8000";`, `dev         "127.0.0.1:24040";`,
		`demo        "127.0.0.1:24060";`, `lucid-next  "127.0.0.1:24000";`, `asm-next    "127.0.0.1:24020";`,
		`lucid       "127.0.0.1:5175";`, `asm         "127.0.0.1:5173";`, `dev         "127.0.0.1:8090";`,
		`demo        "127.0.0.1:5210";`, `lucid-next  "127.0.0.1:5211";`, `asm-next    "127.0.0.1:5212";`,
		`lucid  "ro";`, `asm    "ro";`,
		`default  '["dev","demo","lucid-next","asm-next"]';`,
	} {
		if !strings.Contains(s, w) {
			t.Errorf("maps lack %q\n%s", w, s)
		}
	}
	// $tenants_json must equal the literal in the live routes.conf and the
	// body the gateway serves for /ragstack/tenants.
	routes, err := os.ReadFile("../testdata/live-2026-09-10/proxy/snippets/routes.conf")
	if err != nil {
		t.Fatal(err)
	}
	lit := regexp.MustCompile(`location = /ragstack/tenants \{[^}]*return 200 '(\{"tenants":\[.*\]\})\\n';`).FindSubmatch(routes)
	if lit == nil {
		t.Fatal("routes.conf tenants literal not found")
	}
	wantJSON := string(lit[1])
	m := regexp.MustCompile(`map \$host \$tenants_json \{\n    default  '(\{"tenants":\[.*\]\})';`)
	if body, err := os.ReadFile("../testdata/live-2026-09-10/gateway/tenants.json"); err == nil {
		if strings.TrimSpace(string(body)) != wantJSON {
			t.Errorf("live gateway body != routes.conf literal:\n%s\n%s", body, wantJSON)
		}
	}
	// Our $tenants_json is the inner array; wrap it the way routes.conf will.
	got := regexp.MustCompile(`map \$host \$tenants_json \{\n    default  '(\[.*\])';`).FindStringSubmatch(s)
	if got == nil {
		t.Fatalf("tenants_json map not found:\n%s", s)
	}
	if `{"tenants":`+got[1]+`}` != wantJSON {
		t.Errorf("tenants_json:\n got %s\nwant %s", got[1], wantJSON)
	}
	_ = m
	// Names list matches the literal in routes.conf's / and 404 bodies.
	if !bytes.Contains(routes, []byte(`"tenants":["dev","demo","lucid-next","asm-next"]`)) {
		t.Error("routes.conf names literal changed; update the fixture display_order")
	}
	// Legacy rows precede tenant rows.
	if strings.Index(s, `lucid       "127.0.0.1:8010"`) > strings.Index(s, `dev         "127.0.0.1:24040"`) {
		t.Error("legacy routes must come first")
	}

	static, err := NginxStatic(f, NginxConfig{})
	if err != nil {
		t.Fatal(err)
	}
	golden(t, "tenants-ui-static.generated.conf.live", static)
	if strings.Contains(string(static), "location") {
		t.Errorf("live fixture has no static tenants and no admin mount:\n%s", static)
	}
}

func TestNginxStaticAndAdmin(t *testing.T) {
	f := registry.LiveFixture()
	f.Generation = 7
	f.Ctl.GatewayEnabled = true
	f.Tenants["dev"].UI.Mode = registry.UIModeStatic
	f.Tenants["dev"].UI.Port = 0
	sb := managedTenant("sandbox", 4, "enabled", registry.UIModeStatic)
	f.Tenants["sandbox"] = sb
	out, err := NginxStatic(f, NginxConfig{})
	if err != nil {
		t.Fatal(err)
	}
	golden(t, "tenants-ui-static.generated.conf", out)
	s := string(out)
	for _, w := range []string{
		"location = /ragstack/dev/ui {\n    return 301 /ragstack/dev/ui/;\n}",
		"location ^~ /ragstack/dev/ui/ {\n    include /rag/config/proxy/snippets/cors.conf;\n    alias /rag/data/tenants/dev/ui/dist/;\n    try_files $uri $uri/ /ragstack/dev/ui/index.html;\n}",
		"alias /rag/data/tenants/sandbox/ui/dist/;",
		"location ^~ /ragstack/admin/ui/ {\n    include /rag/config/proxy/snippets/cors.conf;\n    alias /rag/data/ctl/ui/dist/;\n    try_files $uri $uri/ /ragstack/admin/ui/admin.html;\n}",
		"location ^~ /ragstack/admin/api/ {\n    include /rag/config/proxy/snippets/cors.conf;\n    include /rag/config/proxy/snippets/proxy-common.conf;\n    proxy_set_header X-Forwarded-Prefix /ragstack/admin/api;\n    proxy_set_header Host $host;\n    proxy_pass http://127.0.0.1:23990/;\n}",
	} {
		if !strings.Contains(s, w) {
			t.Errorf("static snippet lacks %q\n%s", w, s)
		}
	}
	// dev is static now → no $tenant_ui row; sandbox appended after display_order.
	maps, err := NginxTenants(f, NginxConfig{})
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(maps), `dev         "127.0.0.1:8090"`) {
		t.Error("static tenant still in $tenant_ui")
	}
	if !strings.Contains(string(maps), `'["dev","demo","lucid-next","asm-next","sandbox"]'`) {
		t.Errorf("names: %s", maps)
	}
	golden(t, "05-tenants.generated.conf.with-sandbox", maps)
}

func TestNginxRefusesInjection(t *testing.T) {
	for _, mut := range []func(*registry.Fleet){
		func(f *registry.Fleet) { f.LegacyRoutes[0].API = 80 },
		func(f *registry.Fleet) { f.LegacyRoutes[0].UI = 70000 },
		func(f *registry.Fleet) { f.LegacyRoutes[0].Name = "x\ninclude /etc/passwd" },
		func(f *registry.Fleet) { f.LegacyRoutes[0].Name = "dev" },
		func(f *registry.Fleet) { f.LegacyRoutes[0].Name = "$tenant" },
		func(f *registry.Fleet) { f.Tenants["dev"].UI.Mode = "weird" },
		// A dev UI IS a port (the ctl renders a vite unit for it), so a
		// missing one is a broken row. `external` with port 0 is NOT — see
		// TestNginxSkipsUIlessExternalTenant.
		func(f *registry.Fleet) { f.Tenants["dev"].UI.Mode = registry.UIModeDev; f.Tenants["dev"].UI.Port = 0 },
		func(f *registry.Fleet) { f.Tenants["dev"].UI.Port = 70000 },
		func(f *registry.Fleet) { f.Tenants["dev"].Ports.API = 0 },
		// A legacy row may not claim a reserved gateway route: `admin` is
		// the ctl's own mount, and isLegacyName used to wave it through
		// because it was the same regex ValidateName applies before its
		// reserved check.
		func(f *registry.Fleet) { f.LegacyRoutes[0].Name = "admin" },
		func(f *registry.Fleet) { f.LegacyRoutes[0].Name = "ctl" },
		func(f *registry.Fleet) { f.LegacyRoutes[0].Name = "api" },
	} {
		f := registry.LiveFixture()
		mut(f)
		if _, err := NginxTenants(f, NginxConfig{}); err == nil {
			t.Errorf("mutation accepted")
		}
	}
	for _, mut := range []func(*registry.Fleet){
		func(f *registry.Fleet) {
			f.Tenants["dev"].UI.Mode = registry.UIModeStatic
			f.Tenants["dev"].DataDir = "/rag/data/tenants/dev;x"
		},
		func(f *registry.Fleet) { f.Ctl.GatewayEnabled = true; f.Ctl.UIDist = "/rag/data/ctl/ui/dist'" },
		func(f *registry.Fleet) { f.Ctl.GatewayEnabled = true; f.Ctl.Port = 0 },
	} {
		f := registry.LiveFixture()
		mut(f)
		if _, err := NginxStatic(f, NginxConfig{}); err == nil {
			t.Errorf("static mutation accepted")
		}
	}
	// The generated files never carry a secret shape.
	f := registry.LiveFixture()
	a, _ := NginxTenants(f, NginxConfig{})
	b, _ := NginxStatic(f, NginxConfig{})
	if settings.RedactText(string(a)+string(b)) != string(a)+string(b) {
		t.Error("redactor changed nginx output")
	}
}

// TestUnitsRefusesANonLoopbackBind is S2. coconut is internet-reachable and
// a tenant API authenticates on a header, so `ExecStart=… --host 0.0.0.0` in
// a unit file publishes it. An UNRECORDED bind is the loopback default rather
// than a guess: adopt returns "" when there was no API process to observe,
// and the old code turned that absence of evidence into the most exposed
// bind there is.
func TestUnitsRefusesANonLoopbackBind(t *testing.T) {
	base := func() *registry.Tenant {
		t.Helper()
		f := registry.LiveFixture()
		return f.Tenants["dev"]
	}
	cfg := UnitConfig{RagRoot: "/rag"}

	t.Run("recorded 0.0.0.0 is refused", func(t *testing.T) {
		tn := base()
		tn.API.Bind = "0.0.0.0"
		if _, err := Units(tn, cfg); err == nil {
			t.Fatal("Units rendered --host 0.0.0.0 without an opt-in")
		}
	})
	t.Run("opt-in renders it", func(t *testing.T) {
		tn := base()
		tn.API.Bind = "0.0.0.0"
		out, err := Units(tn, UnitConfig{RagRoot: "/rag", AllowNonLoopbackBind: true})
		if err != nil {
			t.Fatal(err)
		}
		if !bytes.Contains(out["ragstack-dev-api.service"], []byte("--host 0.0.0.0")) {
			t.Error("the opted-in render must keep the recorded bind")
		}
	})
	t.Run("unknown bind is loopback, not a guess", func(t *testing.T) {
		tn := base()
		tn.API.Bind = ""
		out, err := Units(tn, cfg)
		if err != nil {
			t.Fatal(err)
		}
		if !bytes.Contains(out["ragstack-dev-api.service"], []byte("--host 127.0.0.1")) {
			t.Errorf("an unobserved bind must default to loopback:\n%s", out["ragstack-dev-api.service"])
		}
	})
	for _, ok := range []string{"127.0.0.1", "localhost", "::1"} {
		tn := base()
		tn.API.Bind = ok
		if _, err := Units(tn, cfg); err != nil {
			t.Errorf("loopback bind %q refused: %v", ok, err)
		}
	}
}

// TestUnitsValidatesManifestName: the manifest name is a path segment too —
// it names the data dir, the log files, the apptainer instances and the
// manifest.tsv row — and was the only one of the two names nothing checked.
func TestUnitsValidatesManifestName(t *testing.T) {
	for _, bad := range []string{"../etc", "Dev", "admin", "a b", "x;y"} {
		f := registry.LiveFixture()
		tn := f.Tenants["dev"]
		tn.API.Bind = "127.0.0.1"
		tn.ManifestName = bad
		if _, err := Units(tn, UnitConfig{RagRoot: "/rag"}); err == nil {
			t.Errorf("manifest name %q accepted", bad)
		}
	}
}

// TestNginxSkipsUIlessExternalTenant is S20. `adopt` without --ui-port
// records mode `external` with port 0 (the contract's enum is
// static|dev|external, so there is no `none`), and NginxTenants used to fail
// on it — taking the WHOLE fleet's gateway file down because one tenant was
// adopted without a UI port.
func TestNginxSkipsUIlessExternalTenant(t *testing.T) {
	f := registry.LiveFixture()
	f.Tenants["demo"].UI.Mode = registry.UIModeExternal
	f.Tenants["demo"].UI.Port = 0

	out, err := NginxTenants(f, NginxConfig{})
	if err != nil {
		t.Fatalf("a UI-less tenant must not fail the fleet's gateway file: %v", err)
	}
	s := string(out)
	if strings.Contains(s, `"127.0.0.1:0"`) {
		t.Errorf("port 0 reached the rendered map:\n%s", s)
	}
	// No $tenant_ui row for it…
	ui := s[strings.Index(s, "$tenant_ui"):]
	ui = ui[:strings.Index(ui, "}")]
	if strings.Contains(ui, "demo") {
		t.Errorf("a UI-less tenant must not appear in $tenant_ui:\n%s", ui)
	}
	// …but its API route survives, which is the whole point.
	if !strings.Contains(s, `demo        "127.0.0.1:24060"`) {
		t.Errorf("the API route was dropped too:\n%s", s)
	}
}
