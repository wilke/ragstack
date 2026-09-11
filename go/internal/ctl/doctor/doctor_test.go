package doctor

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/hostfacts"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// world is one doctor scenario: a temporary /rag with one tenant, a fleet
// that describes it, and a recorded host that agrees with both. Every test
// starts from this healthy baseline and breaks exactly one thing.
type world struct {
	roots  paths.Roots
	fleet  *registry.Fleet
	host   *hostfacts.Fake
	tenant *registry.Tenant
	opts   Options
}

const (
	devAPI    = 24040
	devQdrant = 24041
	devES     = 24043
	ctlUID    = 4242
)

// cleanEnv is a tenant.env systemd can load: no inline comments, no
// duplicates, no expansions.
const cleanEnv = "LOG_LEVEL=info\nQDRANT_URL=http://localhost:24041\nELASTICSEARCH_URL=http://localhost:24043\n"

func newWorld(t *testing.T) *world {
	t.Helper()
	ragRoot := t.TempDir()
	roots := paths.NewRoots(ragRoot, paths.Overrides{})
	dataDir := filepath.Join(roots.DataDir, "dev")
	for _, d := range []string{
		filepath.Join(dataDir, "config"), filepath.Join(dataDir, "bin"),
		roots.UnitsDir(), roots.ImagesDir, filepath.Join(roots.ProxyDir, "conf.d"),
		filepath.Join(roots.ReposDir, "dev"),
	} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	write(t, filepath.Join(dataDir, "config", "tenant.env"), cleanEnv)
	write(t, filepath.Join(roots.ImagesDir, "qdrant.sif"), "sif")
	write(t, filepath.Join(roots.ImagesDir, "elasticsearch.sif"), "sif")

	tenant := registry.NewTenant("dev", "dev")
	tenant.DataDir = dataDir
	tenant.Worktree = filepath.Join(roots.ReposDir, "dev")
	tenant.PythonEnv = "/rag/envs/ragstack"
	tenant.Ports = paths.Block(2)
	tenant.API = registry.API{Bind: "0.0.0.0", PidFile: dataDir + "/api-dev.pid", Log: dataDir + "/logs/api-dev.log"}
	tenant.Code = registry.Code{Tag: "v1.5.1"}
	tenant.Supervisor, tenant.Owner, tenant.State = "manual", "wilke", "active"
	tenant.DesiredBoot, tenant.EnvLayout = "disabled", "legacy"
	tenant.EnvFileSHA256 = strings.Repeat("a", 64)
	tenant.UI = registry.UI{Mode: registry.UIModeDev, Port: 8090, Base: "/ragstack/dev/ui/"}
	tenant.Stores.Qdrant = registry.Qdrant{
		Ownership: registry.OwnershipExclusive, URL: "http://localhost:24041",
		Instance: "qdrant-dev", SIF: registry.NullString(filepath.Join(roots.ImagesDir, "qdrant.sif")),
		ExtraEnv: map[string]string{},
	}
	tenant.Stores.Elasticsearch = registry.Elasticsearch{
		Ownership: registry.OwnershipExclusive, URL: "http://localhost:24043",
		Instance: "elasticsearch-dev", SIF: registry.NullString(filepath.Join(roots.ImagesDir, "elasticsearch.sif")),
		Heap: "1g", ProvisionHeap: "512m", PathRepo: "/usr/share/elasticsearch/snapshots",
		ExtraEnv: map[string]string{},
	}

	f := registry.NewFleet(ragRoot)
	f.Tenants["dev"] = tenant
	f.DisplayOrder = []string{"dev"}
	if err := registry.Save(roots.Registry(), f, "local:test"); err != nil {
		t.Fatal(err)
	}

	host := &hostfacts.Fake{
		Self:        "wilke",
		Ports:       []hostfacts.Listener{{Port: devAPI, Pid: 1001, User: "wilke", Cmdline: []string{"python", "-m", "uvicorn"}}},
		Env:         map[int]map[string]string{},
		Lingering:   map[string]bool{DefaultCtlUser: true},
		RuntimeDirs: map[int]bool{ctlUID: true},
		DropIns: map[int]hostfacts.DropIn{ctlUID: {
			Present: true, SystemdUnitPath: "/rag/config/ctl/units:", RequiresMountsFor: []string{ragRoot},
		}},
		MaxMapCount: MinVMMaxMapCount,
		MemTotal:    64 << 30,
		Free:        map[string]int64{ragRoot: 400 << 30},
		Groups:      map[string][]string{DefaultSudoersGroup: {"wilke"}},
		Writables:   map[string]hostfacts.Writability{},
		Gitdirs: map[string]hostfacts.Gitdir{
			tenant.Worktree: {Path: filepath.Join(tenant.Worktree, ".git"), Location: hostfacts.GitdirMirror},
		},
	}
	return &world{
		roots: roots, fleet: f, host: host, tenant: tenant,
		opts: Options{
			Host: host, CtlUID: ctlUID, CtlBinary: filepath.Join(ragRoot, "bin", "ragstack-ctl"),
			Now: func() time.Time { return time.Date(2026, 9, 11, 8, 0, 0, 0, time.UTC) },
			ImportCheck: func(_, worktree string) (string, error) {
				return filepath.Join(worktree, "python", "ragstack", "__init__.py"), nil
			},
		},
	}
}

func (w *world) run(t *testing.T) *model.DoctorResponse {
	t.Helper()
	return Run(context.Background(), w.roots, w.fleet, w.opts)
}

// byCode indexes a response's findings.
func byCode(resp *model.DoctorResponse) map[string]model.Finding {
	out := map[string]model.Finding{}
	for _, f := range resp.Findings {
		out[f.Code] = f
	}
	return out
}

func write(t *testing.T, path, content string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
}

// TestHealthyFleetIsGreenWithInfoOnly: the baseline raises nothing above
// info, and the two info rows every adopted tenant carries are present.
func TestHealthyFleetIsGreenWithInfoOnly(t *testing.T) {
	w := newWorld(t)
	resp := w.run(t)
	for _, f := range resp.Findings {
		if f.Level != model.LevelInfo {
			t.Errorf("healthy fleet raised %s/%s: %s", f.Level, f.Code, f.Detail)
		}
	}
	if resp.Status != model.StatusGreen {
		t.Errorf("status = %s, want green", resp.Status)
	}
	got := byCode(resp)
	for _, code := range []string{SudoersGroup, CapabilitiesUnconfirmed} {
		if _, ok := got[code]; !ok {
			t.Errorf("missing the %s row", code)
		}
	}
	if resp.Scope.Tenant != "" || resp.Scope.Op != "" {
		t.Errorf("fleet-wide scope must be null/null, got %+v", resp.Scope)
	}
	if !strings.HasPrefix(resp.Hash, "sha256:") || len(resp.Hash) != 71 {
		t.Errorf("hash = %q", resp.Hash)
	}
}

// TestHostFindings breaks one host fact at a time.
func TestHostFindings(t *testing.T) {
	cases := []struct {
		name   string
		break_ func(*world)
		code   string
		level  model.Level
	}{
		{"linger", func(w *world) { w.host.Lingering = map[string]bool{} }, LingerMissing, model.LevelWarn},
		{"runtime dir", func(w *world) { w.host.RuntimeDirs = map[int]bool{} }, RuntimeDirMissing, model.LevelWarn},
		{"no drop-in", func(w *world) { w.host.DropIns = map[int]hostfacts.DropIn{} }, UserDropInMissing, model.LevelWarn},
		{"half a drop-in", func(w *world) {
			w.host.DropIns = map[int]hostfacts.DropIn{ctlUID: {Present: true, SystemdUnitPath: "/rag/config/ctl/units:"}}
		}, UserDropInMissing, model.LevelWarn},
		{"max_map_count", func(w *world) { w.host.MaxMapCount = 65530 }, VMMaxMapCountLow, model.LevelError},
		{"disk", func(w *world) { w.host.Free = map[string]int64{w.roots.RagRoot: 10 << 30} }, DiskLow, model.LevelWarn},
		{"heap sum", func(w *world) { w.host.MemTotal = 1 << 30 }, ESHeapSumHigh, model.LevelWarn},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			w := newWorld(t)
			c.break_(w)
			got := byCode(w.run(t))
			f, ok := got[c.code]
			if !ok {
				t.Fatalf("no %s finding", c.code)
			}
			if f.Level != c.level {
				t.Errorf("%s level = %s, want %s", c.code, f.Level, c.level)
			}
			if f.Tenant != "" {
				t.Errorf("%s is a host finding and must carry a null tenant, got %q", c.code, f.Tenant)
			}
		})
	}
}

// TestWritableByOthersIsRed covers the permission table: the ctl binary, a
// unit file, a SIF and the tenant data dir are all checked.
func TestWritableByOthersIsRed(t *testing.T) {
	w := newWorld(t)
	write(t, filepath.Join(w.roots.UnitsDir(), "ragstack-dev-api.service"), "[Unit]\n")
	w.host.Writables = map[string]hostfacts.Writability{
		filepath.Join(w.roots.UnitsDir(), "ragstack-dev-api.service"): {
			Writable: true, Path: w.roots.UnitsDir(), Reason: "group-writable by cels (mode 0775), not ragops",
		},
	}
	got := byCode(w.run(t))
	f, ok := got[WritableByOthers]
	if !ok || f.Level != model.LevelError {
		t.Fatalf("writable_by_others = %+v, want a red finding", f)
	}
	if !strings.Contains(f.Detail, "ragstack-dev-api.service") {
		t.Errorf("the finding must name the path: %q", f.Detail)
	}
}

// TestPortFindings: the three ways ports and the registry disagree.
func TestPortFindings(t *testing.T) {
	// A crashed tenant is a WARNING on its own merits: `start` and `restart`
	// are the ops that leave that state, and an unconditional error refused
	// exactly those. preconditions.go raises it for the ops that must not act
	// on a tenant whose live state contradicts the registry (see
	// TestPortNotListeningLetsStartThrough).
	t.Run("not listening", func(t *testing.T) {
		w := newWorld(t)
		w.host.Ports = nil
		got := byCode(w.run(t))
		if f := got[PortNotListening]; f.Level != model.LevelWarn {
			t.Errorf("port_not_listening = %+v, want warn", f)
		}
	})
	t.Run("owner mismatch", func(t *testing.T) {
		w := newWorld(t)
		w.host.Ports = []hostfacts.Listener{{Port: devAPI, Pid: 9, User: "svcbvbrc"}}
		got := byCode(w.run(t))
		f := got[PortOwnerMismatch]
		if f.Level != model.LevelError || f.Tenant != "dev" {
			t.Errorf("port_owner_mismatch = %+v, want red on dev", f)
		}
	})
	t.Run("unexpected listener", func(t *testing.T) {
		w := newWorld(t)
		w.host.Ports = append(w.host.Ports, hostfacts.Listener{Port: w.tenant.Ports.PG, Pid: 77, Cmdline: []string{"postgres"}})
		got := byCode(w.run(t))
		if f := got[UnexpectedListener]; f.Level != model.LevelWarn {
			t.Errorf("unexpected_listener = %+v, want a warning", f)
		}
	})
	t.Run("a store port the registry uses is not unexpected", func(t *testing.T) {
		w := newWorld(t)
		w.host.Ports = append(w.host.Ports,
			hostfacts.Listener{Port: devQdrant, Pid: 3, User: "wilke"},
			hostfacts.Listener{Port: devES, Pid: 4, User: "wilke"})
		if _, ok := byCode(w.run(t))[UnexpectedListener]; ok {
			t.Error("the tenant's own exclusive store ports must not be flagged")
		}
	})
}

// TestEnvGrammarLevelFollowsSupervisor: the same file is a warning for a
// hand-started tenant and an error for a systemd one.
func TestEnvGrammarLevelFollowsSupervisor(t *testing.T) {
	for _, c := range []struct {
		supervisor string
		want       model.Level
	}{{"manual", model.LevelWarn}, {"systemd", model.LevelError}} {
		w := newWorld(t)
		w.tenant.Supervisor = c.supervisor
		write(t, filepath.Join(w.tenant.DataDir, "config", "tenant.env"),
			"LOG_LEVEL=info   # chatty\nQDRANT_URL=http://localhost:24041\n")
		got := byCode(w.run(t))
		f, ok := got[EnvNotSystemdParsable]
		if !ok {
			t.Fatalf("%s: no env finding for an inline comment", c.supervisor)
		}
		if f.Level != c.want {
			t.Errorf("%s: level = %s, want %s", c.supervisor, f.Level, c.want)
		}
		if f.Repair != "env-normalize" {
			t.Errorf("%s: repair = %q, want env-normalize", c.supervisor, f.Repair)
		}
	}
}

// TestCodeFindings: an unreadable gitdir, a gitdir outside the mirror, and an
// import that resolves outside the worktree.
func TestCodeFindings(t *testing.T) {
	t.Run("unreadable", func(t *testing.T) {
		w := newWorld(t)
		w.host.Gitdirs = map[string]hostfacts.Gitdir{}
		if f := byCode(w.run(t))[WorktreeGitdirUnreadable]; f.Level != model.LevelWarn {
			t.Errorf("worktree_gitdir_unreadable = %+v", f)
		}
	})
	t.Run("outside the mirror", func(t *testing.T) {
		w := newWorld(t)
		w.host.Gitdirs[w.tenant.Worktree] = hostfacts.Gitdir{Path: "/home/wilke/Development/ragstack/.git", Location: hostfacts.GitdirOutside}
		f := byCode(w.run(t))[WorktreeOutsideMirror]
		if f.Level != model.LevelWarn || f.Repair != "handover" {
			t.Errorf("worktree_outside_mirror = %+v", f)
		}
	})
	t.Run("import resolves elsewhere", func(t *testing.T) {
		w := newWorld(t)
		w.opts.ImportCheck = func(string, string) (string, error) {
			return "/rag/envs/ragstack/lib/python3.12/site-packages/ragstack/__init__.py", nil
		}
		if f := byCode(w.run(t))[ImportRagstackOutsideWorktree]; f.Level != model.LevelWarn {
			t.Errorf("import_ragstack_outside_worktree = %+v", f)
		}
	})
	t.Run("import inside the worktree is silent", func(t *testing.T) {
		w := newWorld(t)
		if _, ok := byCode(w.run(t))[ImportRagstackOutsideWorktree]; ok {
			t.Error("an import that resolves inside the worktree is not a finding")
		}
	})
}

// TestStoreFindings: a disallowed URL, and the dormant pair that turns red
// when a bin/up.sh could start it.
func TestStoreFindings(t *testing.T) {
	t.Run("disallowed url", func(t *testing.T) {
		w := newWorld(t)
		w.tenant.Stores.Qdrant.URL = "http://evil.example.com:6333"
		f := byCode(w.run(t))[StoreURLDisallowed]
		if f.Level != model.LevelError || !strings.Contains(f.Detail, "QDRANT_URL") {
			t.Errorf("store_url_disallowed = %+v", f)
		}
	})
	t.Run("dormant without up.sh", func(t *testing.T) {
		w := newWorld(t)
		w.tenant.Stores.DormantProvisionedDirs = true
		if f := byCode(w.run(t))[DormantProvisionedDirs]; f.Level != model.LevelWarn {
			t.Errorf("dormant = %+v, want a warning", f)
		}
	})
	t.Run("dormant with up.sh", func(t *testing.T) {
		w := newWorld(t)
		w.tenant.Stores.DormantProvisionedDirs = true
		write(t, filepath.Join(w.tenant.DataDir, "bin", "up.sh"), "#!/bin/sh\n")
		f := byCode(w.run(t))[DormantProvisionedDirs]
		if f.Level != model.LevelError || !strings.Contains(f.Detail, "up.sh") {
			t.Errorf("dormant with up.sh = %+v, want red naming the script", f)
		}
	})
}

// TestManifestFindings: a row the registry does not know, and a row whose
// ports disagree.
func TestManifestFindings(t *testing.T) {
	t.Run("unknown row", func(t *testing.T) {
		w := newWorld(t)
		write(t, registry.PathsFor(w.roots.Registry()).Manifest,
			registry.ManifestHeader+"\ndev\t2\t24040\nghost\t9\t24180\n")
		if f := byCode(w.run(t))[ManifestUnknownRow]; f.Level != model.LevelError {
			t.Errorf("manifest_unknown_row = %+v", f)
		}
	})
	t.Run("port mismatch", func(t *testing.T) {
		w := newWorld(t)
		write(t, registry.PathsFor(w.roots.Registry()).Manifest,
			registry.ManifestHeader+"\ndev\t7\t24140\n")
		if f := byCode(w.run(t))[RegistryManifestMismatch]; f.Level != model.LevelError {
			t.Errorf("registry_manifest_mismatch = %+v", f)
		}
	})
}

// TestGatewayMapMismatch parses the live map file and compares it with the
// registry's allocation.
func TestGatewayMapMismatch(t *testing.T) {
	w := newWorld(t)
	write(t, filepath.Join(w.roots.ProxyDir, "conf.d", "00-maps.conf"), `
map $tenant $tenant_api {
    default  "";
    dev      "127.0.0.1:8020";
}
`)
	f := byCode(w.run(t))[GatewayMapMismatch]
	if f.Level != model.LevelError || !strings.Contains(f.Detail, "8020") {
		t.Errorf("gateway_map_mismatch = %+v", f)
	}

	w2 := newWorld(t)
	write(t, filepath.Join(w2.roots.ProxyDir, "conf.d", "00-maps.conf"), `
map $tenant $tenant_api {
    default  "";
    dev      "127.0.0.1:24040";
}
`)
	if _, ok := byCode(w2.run(t))[GatewayMapMismatch]; ok {
		t.Error("a gateway that agrees with the registry is not a finding")
	}
}

// TestParseTenantMapsReadsTheLiveFiles pins the parser against both shapes:
// the hand-written 00-maps.conf and the generated include.
func TestParseTenantMapsReadsTheLiveFiles(t *testing.T) {
	for _, p := range []string{
		"../testdata/live-2026-09-10/proxy/conf.d/00-maps.conf",
		"../render/testdata/golden/05-tenants.generated.conf",
	} {
		b, err := os.ReadFile(p)
		if err != nil {
			t.Skipf("%s not present", p)
		}
		m := ParseTenantMaps(b, p)
		want := map[string]int{"lucid": 8010, "asm": 8000, "dev": 24040, "demo": 24060, "lucid-next": 24000, "asm-next": 24020}
		for name, port := range want {
			if m.API[name] != port {
				t.Errorf("%s: API[%s] = %d, want %d", p, name, m.API[name], port)
			}
		}
		if m.UI["dev"] != 8090 || m.UI["asm-next"] != 5212 {
			t.Errorf("%s: UI map = %v", p, m.UI)
		}
		if !m.Readonly["lucid"] || !m.Readonly["asm"] || m.Readonly["dev"] {
			t.Errorf("%s: readonly map = %v", p, m.Readonly)
		}
	}
}

// TestDisplayOrderFromRoutes reads the landing-page order out of the live
// routes.conf literal.
func TestDisplayOrderFromRoutes(t *testing.T) {
	got := DisplayOrderFromRoutes("../testdata/live-2026-09-10/proxy/snippets/routes.conf")
	want := []string{"dev", "demo", "lucid-next", "asm-next"}
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Errorf("display order = %v, want %v", got, want)
	}
	if DisplayOrderFromRoutes(filepath.Join(t.TempDir(), "absent.conf")) != nil {
		t.Error("a missing routes.conf must yield nil, not an empty order")
	}
}

// TestPreconditionsRaiseWarningsForTheOp: `start` cannot proceed over an
// unparsable env file, while a read-only run keeps it a warning.
func TestPreconditionsRaiseWarningsForTheOp(t *testing.T) {
	w := newWorld(t)
	write(t, filepath.Join(w.tenant.DataDir, "config", "tenant.env"), "LOG_LEVEL=info   # chatty\n")

	read := w.run(t)
	if read.Status != model.StatusYellow {
		t.Fatalf("unscoped status = %s, want yellow", read.Status)
	}

	w.opts.Op = "start"
	scoped := w.run(t)
	if scoped.Status != model.StatusRed {
		t.Errorf("start status = %s, want red", scoped.Status)
	}
	if f := byCode(scoped)[EnvNotSystemdParsable]; f.Level != model.LevelError {
		t.Errorf("start must raise env_not_systemd_parsable to an error, got %s", f.Level)
	}
	if scoped.Scope.Op != "start" {
		t.Errorf("scope.op = %q", scoped.Scope.Op)
	}
	// The hash is over the findings, not their levels: the same host state
	// hashes identically whichever op asks.
	if scoped.Hash != read.Hash {
		t.Error("the doctor hash must not change with the op scope")
	}
}

// TestOpsTableIsSane: every op's precondition codes are real codes, and the
// op that exists to fix a finding does not block on it.
func TestOpsTableIsSane(t *testing.T) {
	known := map[string]bool{}
	for _, c := range []string{
		RegistryManifestMismatch, ManifestUnknownRow, PortOwnerMismatch, PortNotListening,
		UnexpectedListener, GatewayMapMismatch, EnvNotSystemdParsable, WorktreeGitdirUnreadable,
		WorktreeOutsideMirror, ImportRagstackOutsideWorktree, WritableByOthers, LingerMissing,
		UserDropInMissing, RuntimeDirMissing, VMMaxMapCountLow, DiskLow, ESHeapSumHigh,
		SudoersGroup, DormantProvisionedDirs, StoreURLDisallowed, CapabilitiesUnconfirmed,
		ESHeapDrift, StoreNotListening, UIPortNotListening, UnsupportedEnvKey,
		APIKeyRoleUnknown, ExternalRefOutsideDataDir, UnmanagedFiles, DataDirOffLayout,
	} {
		known[c] = true
	}
	for _, op := range Ops() {
		for _, code := range RedCodes(op) {
			if !known[code] {
				t.Errorf("op %q lists unknown code %q", op, code)
			}
		}
	}
	for _, code := range RedCodes("env-normalize") {
		if code == EnvNotSystemdParsable {
			t.Error("env-normalize is the repair for env_not_systemd_parsable and must not block on it")
		}
	}
	if RedCodes("") != nil || RedCodes("fleet-status") != nil {
		t.Error("a read-only or unknown op raises nothing")
	}
}

// TestHashIsStableAndScoped: identical findings hash identically, a changed
// detail does not, and a tenant-scoped run carries its tenant.
func TestHashIsStableAndScoped(t *testing.T) {
	a := []model.Finding{
		{Level: model.LevelWarn, Code: LingerMissing, Detail: "no linger"},
		{Level: model.LevelInfo, Code: SudoersGroup, Tenant: "dev", Detail: "wilke"},
	}
	b := []model.Finding{a[1], a[0]} // order must not matter
	if Hash(a) != Hash(b) {
		t.Error("the hash must not depend on finding order")
	}
	c := []model.Finding{a[0], {Level: model.LevelInfo, Code: SudoersGroup, Tenant: "dev", Detail: "wilke, svcbvbrc"}}
	if Hash(a) == Hash(c) {
		t.Error("a changed detail must change the hash")
	}

	w := newWorld(t)
	w.opts.Tenant = "dev"
	resp := w.run(t)
	if resp.Scope.Tenant != "dev" {
		t.Errorf("scope.tenant = %q", resp.Scope.Tenant)
	}
	for _, f := range resp.Findings {
		if f.Tenant != "" && f.Tenant != "dev" {
			t.Errorf("a dev-scoped run returned a finding about %q", f.Tenant)
		}
	}
}

// TestPortOwnerUnreadableIsAMismatch: /proc/<pid>/fd is not readable across
// accounts, so a ctl running as svcbvbrc sees wilke's uvicorn as a socket
// with no owner at all. The gate used to require `api.User != ""` before it
// compared anything, which made "I cannot tell who owns this port" indistin-
// guishable from "the owner is right" — on exactly the host layout the gate
// was written for.
func TestPortOwnerUnreadableIsAMismatch(t *testing.T) {
	w := newWorld(t)
	w.host.Ports = []hostfacts.Listener{{Port: devAPI, Pid: 0, UID: -1}} // owner invisible
	got := byCode(w.run(t))
	f, ok := got[PortOwnerMismatch]
	if !ok || f.Level != model.LevelError {
		t.Fatalf("port_owner_mismatch = %+v, want a red finding", f)
	}
	if !strings.Contains(f.Detail, "owner unreadable") {
		t.Errorf("the finding must say why it cannot attribute the port: %q", f.Detail)
	}
	if string(f.Tenant) != "dev" {
		t.Errorf("finding tenant = %q, want dev", f.Tenant)
	}
	// And it still blocks the ops whose preconditions name it.
	w.opts.Op = "handover"
	if resp := w.run(t); resp.Status != model.StatusRed {
		t.Errorf("handover over an unattributable port = %s, want red", resp.Status)
	}
}

// TestPortNotListeningLetsStartThrough is S13: a crashed tenant (state active,
// no listener) must not refuse the ops that exist to repair it.
func TestPortNotListeningLetsStartThrough(t *testing.T) {
	w := newWorld(t)
	w.host.Ports = nil
	for _, op := range []string{"start", "restart", "stop"} {
		w.opts.Op = op
		resp := w.run(t)
		if resp.Status == model.StatusRed {
			t.Errorf("--op %s over a crashed tenant = red; it is the repair", op)
		}
		for _, f := range resp.Findings {
			if f.Code == PortNotListening && f.Level != model.LevelWarn {
				t.Errorf("--op %s: port_not_listening = %s, want warn", op, f.Level)
			}
		}
	}
	// Every other mutating op that names it still refuses.
	for _, op := range []string{"backup", "handover", "migrate-local", "decommission", "update-code"} {
		w.opts.Op = op
		if resp := w.run(t); resp.Status != model.StatusRed {
			t.Errorf("--op %s over a crashed tenant = %s, want red", op, resp.Status)
		}
	}
}

// TestEnvNormalizeToleratesTheFindingItRepairs is S12: env-normalize is the
// only way to fix env_not_systemd_parsable, and on a systemd tenant that
// finding is an error on its own merits — so an empty precondition row was
// not enough: the op was refused by the very finding it repairs.
func TestEnvNormalizeToleratesTheFindingItRepairs(t *testing.T) {
	w := newWorld(t)
	w.tenant.Supervisor = string(model.SupervisorSystemd)
	write(t, filepath.Join(w.tenant.DataDir, "config", "tenant.env"),
		"LOG_LEVEL=info   # noisy in prod\nLOG_LEVEL=debug\n")

	w.opts.Op = ""
	base := byCode(w.run(t))
	if f := base[EnvNotSystemdParsable]; f.Level != model.LevelError {
		t.Fatalf("unscoped env finding = %+v, want error for a systemd tenant", f)
	}
	if f := base[EnvNotSystemdParsable]; f.Repair != "env-normalize" {
		t.Errorf("the finding must name its repair op, got %q", f.Repair)
	}

	w.opts.Op = "env-normalize"
	resp := w.run(t)
	if resp.Status == model.StatusRed {
		t.Fatalf("--op env-normalize = red; it is the op that fixes this")
	}
	for _, f := range resp.Findings {
		if f.Code == EnvNotSystemdParsable && f.Level != model.LevelWarn {
			t.Errorf("scoped env finding = %s, want warn", f.Level)
		}
	}
	// Tolerating is scoped to that one op: every other env-touching op is
	// still refused.
	w.opts.Op = "env-set"
	if resp := w.run(t); resp.Status != model.StatusRed {
		t.Errorf("--op env-set over an unparsable env = %s, want red", resp.Status)
	}
	// And the hash does not move: levels are not part of it, so a plan
	// pinned before the scope still quotes the same findings.
	w.opts.Op = "env-normalize"
	if a, b := Hash(base2(w, t, "")), Hash(base2(w, t, "env-normalize")); a != b {
		t.Errorf("hash changed with the op scope: %s vs %s", a, b)
	}
}

func base2(w *world, t *testing.T, op string) []model.Finding {
	t.Helper()
	w.opts.Op = op
	return w.run(t).Findings
}

// TestEnvCheckCoversSecretsEnv is S22: the api unit loads BOTH env files, so
// a grammar violation in secrets.env breaks it exactly as surely as one in
// tenant.env — and is harder to spot, because the value is redacted wherever
// anyone would look at it.
func TestEnvCheckCoversSecretsEnv(t *testing.T) {
	w := newWorld(t)
	w.tenant.Supervisor = string(model.SupervisorSystemd)
	write(t, filepath.Join(w.tenant.DataDir, "config", "secrets.env"),
		"TENANT_PG_PASSWORD=hunter2hunter2   # rotated 2026-09-01\n")

	got := byCode(w.run(t))
	f, ok := got[EnvNotSystemdParsable]
	if !ok || f.Level != model.LevelError {
		t.Fatalf("env_not_systemd_parsable = %+v, want a red finding from secrets.env", f)
	}
	if !strings.Contains(f.Detail, "secrets.env") {
		t.Errorf("the finding must name the file: %q", f.Detail)
	}
	if strings.Contains(f.Detail, "hunter2hunter2") {
		t.Errorf("a finding must never quote the value: %q", f.Detail)
	}
	// An ABSENT secrets.env is not a finding: the unit loads it with `-`.
	w2 := newWorld(t)
	if _, bad := byCode(w2.run(t))[EnvNotSystemdParsable]; bad {
		t.Error("a tenant with no secrets.env must not raise the grammar finding")
	}
}

// TestHeapBytesUnits is S21: `-Xmx4G` is what a hand-written ES_JAVA_OPTS
// says, and the lowercase-only conversion read it as 0 — quietly dropping the
// biggest heap on the host out of the sum es_heap_sum_high is made of.
func TestHeapBytesUnits(t *testing.T) {
	for in, want := range map[string]int64{
		"":     0,
		"512m": 512 << 20,
		"512M": 512 << 20,
		"4g":   4 << 30,
		"4G":   4 << 30,
		"64k":  64 << 10,
		"64K":  64 << 10,
	} {
		got, err := heapBytes(in)
		if err != nil || got != want {
			t.Errorf("heapBytes(%q) = %d, %v; want %d", in, got, err, want)
		}
	}
	for _, in := range []string{"4", "4t", "gg", "-4g", "4gb", "x"} {
		if got, err := heapBytes(in); err == nil {
			t.Errorf("heapBytes(%q) = %d, nil; want an error", in, got)
		}
	}
}

// TestUnparsableHeapIsReported: an unreadable heap must be loud, because the
// alternative (counting it as zero) is a silent under-estimate of the one sum
// that decides whether this host is about to swap.
func TestUnparsableHeapIsReported(t *testing.T) {
	w := newWorld(t)
	w.tenant.Stores.Elasticsearch.Heap = "4G"
	got := byCode(w.run(t))
	if _, bad := got[ESHeapUnparsable]; bad {
		t.Error("4G is a valid heap now; it must not be reported as unparsable")
	}
	w.tenant.Stores.Elasticsearch.Heap = "4tons"
	got = byCode(w.run(t))
	f, ok := got[ESHeapUnparsable]
	if !ok || f.Level != model.LevelWarn {
		t.Fatalf("es_heap_unparsable = %+v, want a warn finding", f)
	}
	if !strings.Contains(f.Detail, "UNDER-estimate") {
		t.Errorf("the finding must say the sum is now wrong: %q", f.Detail)
	}
}

// TestOpsAreKnown backs the CLI's --op validation: every op the table knows
// is reported by Ops(), and nothing else is.
func TestOpsAreKnown(t *testing.T) {
	ops := Ops()
	if len(ops) == 0 {
		t.Fatal("Ops() is empty")
	}
	for i := 1; i < len(ops); i++ {
		if ops[i-1] >= ops[i] {
			t.Fatalf("Ops() is not sorted: %v", ops)
		}
	}
	for _, op := range ops {
		if !KnownOp(op) {
			t.Errorf("KnownOp(%q) = false", op)
		}
	}
	for _, op := range []string{"strat", "", "restarts", "START"} {
		if KnownOp(op) {
			t.Errorf("KnownOp(%q) = true", op)
		}
	}
	// env-normalize has no preconditions but IS an op: it is only in the
	// tolerates table, and Ops() is the union.
	if !KnownOp("env-normalize") {
		t.Error("env-normalize must be a known op")
	}
}
