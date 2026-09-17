package doctor

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/acl"
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
		// path.repo: the healthy baseline HAS it, because the ES unit binds
		// it and apptainer refuses a bind whose source is missing.
		filepath.Join(dataDir, "elasticsearch", "snapshots"),
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
		// The healthy baseline is the post-`fleet grant` state: the service
		// account reaches the managed roots through a named ACL entry,
		// because on this host it can reach them no other way.
		ACLs: map[string]hostfacts.FakeACL{
			roots.DataDir:  {Access: grantedACL(ctlUID), Default: grantedACL(ctlUID)},
			roots.ReposDir: {Access: grantedACL(ctlUID), Default: grantedACL(ctlUID)},
		},
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

// grantedACL is what `fleet grant --user svcbvbrc` leaves on a managed root
// owned by wilke whose group is cels: the one account named, the group and
// the world given nothing.
func grantedACL(uid int) acl.ACL {
	return acl.ACL{
		{Tag: acl.TagUserObj, ID: acl.UndefinedID, Perm: acl.PermRWX},
		{Tag: acl.TagUser, ID: uint32(uid), Perm: acl.PermRWX},
		{Tag: acl.TagGroupObj, ID: acl.UndefinedID, Perm: acl.PermNone},
		{Tag: acl.TagMask, ID: acl.UndefinedID, Perm: acl.PermRWX},
		{Tag: acl.TagOther, ID: acl.UndefinedID, Perm: acl.PermNone},
	}
}

func (w *world) run(t *testing.T) *model.DoctorResponse {
	t.Helper()
	return Run(context.Background(), w.roots, w.fleet, w.opts)
}

// runOpts is run() with the world's seams and a caller's scope: the same host,
// the same fleet, a different question asked of them.
func (w *world) runOpts(t *testing.T, opts Options) *model.DoctorResponse {
	t.Helper()
	merged := w.opts
	merged.Op, merged.Destination, merged.Tenant = opts.Op, opts.Destination, opts.Tenant
	return Run(context.Background(), w.roots, w.fleet, merged)
}

// byCode indexes a response's findings.
func byCode(resp *model.DoctorResponse) map[string]model.Finding {
	out := map[string]model.Finding{}
	for _, f := range resp.Findings {
		out[f.Code] = f
	}
	return out
}

// confirmStores is what `adopt --readopt --confirm-stores qdrant,elasticsearch`
// writes: the three capabilities an operator confirms on an exclusive leg.
// `purge` stays false — it is the one that destroys data, and no op asks for
// it in v1.
func (w *world) confirmStores() {
	caps := registry.Capabilities{Stop: true, Snapshot: true, Restore: true}
	w.tenant.Stores.Qdrant.Capabilities = caps
	w.tenant.Stores.Elasticsearch.Capabilities = caps
}

func write(t *testing.T, path, content string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
}

// TestHealthyFleetIsGreenWithInfoOnly: the baseline raises nothing above info
// EXCEPT the one gap PR-E exists to close, and the two info rows every adopted
// tenant carries are present.
//
// The baseline is an ADOPTED tenant that nobody has prepared yet, so its two
// exclusively-owned stores still have `capabilities.stop: false` and
// stores_unconfirmed is a warn. That is the honest state of this fleet and the
// reason the finding was added; confirming the legs (what `adopt --readopt
// --confirm-stores` writes) is what makes the fleet green, and the second half
// of this test asserts exactly that.
func TestHealthyFleetIsGreenWithInfoOnly(t *testing.T) {
	w := newWorld(t)
	resp := w.run(t)
	for _, f := range resp.Findings {
		if f.Level != model.LevelInfo && f.Code != StoresUnconfirmed {
			t.Errorf("healthy fleet raised %s/%s: %s", f.Level, f.Code, f.Detail)
		}
	}
	got := byCode(resp)
	for _, code := range []string{SudoersGroup, CapabilitiesUnconfirmed, StoresUnconfirmed} {
		if _, ok := got[code]; !ok {
			t.Errorf("missing the %s row", code)
		}
	}
	if lvl := got[StoresUnconfirmed].Level; lvl != model.LevelWarn {
		t.Errorf("stores_unconfirmed = %s, want warn", lvl)
	}
	if resp.Status != model.StatusYellow {
		t.Errorf("status = %s, want yellow: two exclusive stores the ctl may not stop", resp.Status)
	}
	w.confirmStores()
	confirmed := w.run(t)
	for _, f := range confirmed.Findings {
		if f.Level != model.LevelInfo {
			t.Errorf("a confirmed fleet raised %s/%s: %s", f.Level, f.Code, f.Detail)
		}
	}
	if confirmed.Status != model.StatusGreen {
		t.Errorf("status after confirming the stores = %s, want green", confirmed.Status)
	}
	if _, still := byCode(confirmed)[CapabilitiesUnconfirmed]; still {
		t.Error("capabilities_unconfirmed survived a confirmation: it claims every capability is false")
	}
	resp = confirmed
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

// findingsForCode is byCode without the collapse: HomePathInProduction can
// legitimately fire more than once (a tenant's worktree AND its live cwd, an
// artifact AND a ctl.env value), so a test needs every occurrence, not just
// the last one a map keeps.
func findingsForCode(resp *model.DoctorResponse, code string) []model.Finding {
	var out []model.Finding
	for _, f := range resp.Findings {
		if f.Code == code {
			out = append(out, f)
		}
	}
	return out
}

// TestHomePathInProductionIsClean is the baseline: newWorld's tenant lives
// entirely under the temp /rag, so the sweep finds nothing to warn about.
func TestHomePathInProductionIsClean(t *testing.T) {
	w := newWorld(t)
	if got := findingsForCode(w.run(t), HomePathInProduction); len(got) != 0 {
		t.Errorf("home_path_in_production = %+v, want none in a fully-under-/rag fixture", got)
	}
}

// TestHomePathInProductionFlagsRegistryPaths covers data_dir, worktree and
// python_env — the plan's own examples of what still points at $HOME today.
func TestHomePathInProductionFlagsRegistryPaths(t *testing.T) {
	cases := []struct {
		name   string
		break_ func(t *registry.Tenant)
		want   string
	}{
		{"worktree", func(t *registry.Tenant) { t.Worktree = "/home/wilke/Development/ragstack" }, "worktree="},
		{"data_dir", func(t *registry.Tenant) { t.DataDir = "/home/wilke/data/dev" }, "data_dir="},
		{"python_env", func(t *registry.Tenant) { t.PythonEnv = "~/.venvs/ragstack" }, "python_env="},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			w := newWorld(t)
			c.break_(w.tenant)
			got := findingsForCode(w.run(t), HomePathInProduction)
			if len(got) != 1 {
				t.Fatalf("home_path_in_production = %+v, want exactly 1", got)
			}
			if got[0].Level != model.LevelWarn {
				t.Errorf("level = %s, want warn", got[0].Level)
			}
			if string(got[0].Tenant) != "dev" {
				t.Errorf("tenant = %q, want dev", got[0].Tenant)
			}
			if !strings.Contains(got[0].Detail, c.want) {
				t.Errorf("detail %q does not name the field (%s)", got[0].Detail, c.want)
			}
		})
	}
}

// TestHomePathInProductionFlagsLiveAPIProcess is the hostfacts half: an
// adopted tenant's process cwd (or, absent a readable cwd, argv[0]) under
// /home is exactly as much a violation as the registry row is.
func TestHomePathInProductionFlagsLiveAPIProcess(t *testing.T) {
	w := newWorld(t)
	w.host.Ports = []hostfacts.Listener{{
		Port: devAPI, Pid: 1001, User: "wilke",
		Cmdline: []string{"/home/wilke/Development/ragstack/python/.venv/bin/python", "-m", "uvicorn"},
		Cwd:     "/home/wilke/Development/ragstack/python",
	}}
	got := findingsForCode(w.run(t), HomePathInProduction)
	if len(got) != 2 {
		t.Fatalf("home_path_in_production = %+v, want 2 (cwd and argv[0])", got)
	}
	for _, f := range got {
		if string(f.Tenant) != "dev" {
			t.Errorf("tenant = %q, want dev: %+v", f.Tenant, f)
		}
	}
}

// TestHomePathInProductionFlagsArtifactWorktree is the one host-scoped
// (tenant == "") registry source: a prepared artifact belongs to no single
// tenant.
func TestHomePathInProductionFlagsArtifactWorktree(t *testing.T) {
	w := newWorld(t)
	w.fleet.Artifacts["main-abc123456789"] = &registry.Artifact{
		SHA: strings.Repeat("a", 40), Tag: "main",
		Worktree:  "/home/wilke/state/artifacts/main-abc/worktree",
		UIDist:    filepath.Join(w.roots.RagRoot, "data", "ctl", "artifacts", "main-abc", "ui", "dist"),
		PythonEnv: "/rag/envs/ragstack", PreparedBy: "wilke",
	}
	got := findingsForCode(w.run(t), HomePathInProduction)
	if len(got) != 1 {
		t.Fatalf("home_path_in_production = %+v, want exactly 1", got)
	}
	if got[0].Tenant != "" {
		t.Errorf("an artifact finding is host-scoped: tenant = %q", got[0].Tenant)
	}
	if !strings.Contains(got[0].Detail, "artifacts[main-abc123456789]") {
		t.Errorf("detail does not name the artifact: %q", got[0].Detail)
	}
}

// TestHomePathInProductionFlagsCtlEnv covers coconut's actual current state
// (plan "Host facts"): CTL_NODE_BIN at ~/.local/bin/node, until install-node
// gives it somewhere under /rag/tools to point at instead.
func TestHomePathInProductionFlagsCtlEnv(t *testing.T) {
	w := newWorld(t)
	w.opts.CtlEnv = map[string]string{"CTL_NODE_BIN": "~/.local/bin/node", "CTL_GIT_BIN": "/usr/bin/git"}
	got := findingsForCode(w.run(t), HomePathInProduction)
	if len(got) != 1 {
		t.Fatalf("home_path_in_production = %+v, want exactly 1 (CTL_GIT_BIN is not a home path)", got)
	}
	if got[0].Tenant != "" {
		t.Errorf("a ctl.env finding is host-scoped: tenant = %q", got[0].Tenant)
	}
	if !strings.Contains(got[0].Detail, "CTL_NODE_BIN") {
		t.Errorf("detail does not name the variable: %q", got[0].Detail)
	}
}

// TestHomePathInProductionNeverBlocksAnOp: it is a warning under every op,
// including the ones that block on far less (plan: "keep it a warning, it
// never blocks an op").
func TestHomePathInProductionNeverBlocksAnOp(t *testing.T) {
	w := newWorld(t)
	w.tenant.Worktree = "/home/wilke/Development/ragstack"
	w.opts.Op = "handover"
	got := findingsForCode(w.run(t), HomePathInProduction)
	if len(got) != 1 || got[0].Level != model.LevelWarn {
		t.Fatalf("home_path_in_production under --op handover = %+v, want a single warn", got)
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
		// The ADDRESS, not just the port: `10.0.0.5:24040` and
		// `127.0.0.1:24040` are different machines, and a comparison built on
		// the port alone calls them the same route.
		for name, port := range want {
			if m.APIAddr[name] != fmt.Sprintf("127.0.0.1:%d", port) {
				t.Errorf("%s: APIAddr[%s] = %q, want the full host:port", p, name, m.APIAddr[name])
			}
		}
		if m.UIAddr["dev"] != "127.0.0.1:8090" {
			t.Errorf("%s: UIAddr[dev] = %q", p, m.UIAddr["dev"])
		}
	}

	// The two tenant JSON lists come out of the GENERATED file only: before
	// the first publication they are `return 200 '…'` literals in routes.conf,
	// which is a different file and a different grammar.
	gen, err := os.ReadFile("../render/testdata/golden/05-tenants.generated.conf")
	if err != nil {
		t.Skip("no golden generated include")
	}
	m := ParseTenantMaps(gen, "golden")
	var names []string
	if err := json.Unmarshal([]byte(m.NamesJSON), &names); err != nil {
		t.Fatalf("$tenants_names_json = %q: %v", m.NamesJSON, err)
	}
	if strings.Join(names, ",") != "dev,demo,lucid-next,asm-next" {
		t.Errorf("$tenants_names_json = %v", names)
	}
	if !strings.HasPrefix(m.TenantsJSON, `[{"name":"dev"`) {
		t.Errorf("$tenants_json = %q", m.TenantsJSON)
	}
	// A file with neither literal reports them absent rather than guessing.
	if m := ParseTenantMaps([]byte("map $tenant $tenant_api {\n    default \"\";\n}\n"), "x"); m.NamesJSON != "" || m.TenantsJSON != "" {
		t.Errorf("literals invented from a file that has none: %q %q", m.NamesJSON, m.TenantsJSON)
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
		OwnerNotInEnum, ESSnapshotsDirMissing, ESHeapUnparsable,
		ACLGrantsOthers, ACLGrantPresent, CtlAccountNoAccess,
		BootCronMissing, BootCronPresent, StoresUnconfirmed,
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

// TestPortOwnerUnreadablePreHandoverIsInfo: the live shape reported by the
// daemon on coconut — it runs as svcbvbrc, the registry still records
// wilke as dev's owner (pre PR-E handover), and /proc/<pid>/fd across
// accounts is unreadable. There is no readable owner to compare against the
// registry here, so this is not a mismatch: it is the designed pre-handover
// state, the same class as secrets_unreadable_by_ctl, and must not turn the
// fleet red.
func TestPortOwnerUnreadablePreHandoverIsInfo(t *testing.T) {
	w := newWorld(t)
	w.host.Self = DefaultCtlUser                                         // daemon account; tenant.Owner stays "wilke"
	w.host.Ports = []hostfacts.Listener{{Port: devAPI, Pid: 0, UID: -1}} // owner invisible
	got := byCode(w.run(t))
	if f, ok := got[PortOwnerMismatch]; ok {
		t.Errorf("port_owner_mismatch = %+v, want no error finding pre-handover", f)
	}
	f, ok := got[PortOwnerUnverifiable]
	if !ok || f.Level != model.LevelInfo {
		t.Fatalf("port_owner_unverifiable = %+v, want an info finding", f)
	}
	if !strings.Contains(f.Detail, "cannot attribute") || !strings.Contains(f.Detail, "wilke") {
		t.Errorf("the finding must say it cannot attribute the port and name the registry owner: %q", f.Detail)
	}
	if resp := w.run(t); resp.Status == model.StatusRed {
		t.Errorf("an unattributable port on a pre-handover tenant must not redden the fleet, got %s", resp.Status)
	}
}

// TestPortOwnerUnreadableOwnAccountIsAMismatch: the owner is unreadable, but
// the registry says THIS ctl account owns the port — there is no pending
// handover to explain the gap, so something is genuinely wrong and the
// finding must stay an error.
func TestPortOwnerUnreadableOwnAccountIsAMismatch(t *testing.T) {
	w := newWorld(t)
	w.host.Self = DefaultCtlUser
	w.tenant.Owner = DefaultCtlUser
	w.host.Ports = []hostfacts.Listener{{Port: devAPI, Pid: 0, UID: -1}} // owner invisible
	got := byCode(w.run(t))
	f, ok := got[PortOwnerMismatch]
	if !ok || f.Level != model.LevelError {
		t.Fatalf("port_owner_mismatch = %+v, want a red finding", f)
	}
	if _, unverifiable := got[PortOwnerUnverifiable]; unverifiable {
		t.Errorf("port_owner_unverifiable should not also fire when the registry owner is the ctl account")
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
	for _, op := range []string{"backup", "handover", "migrate-local", "update-code"} {
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

// TestOpsCoversTheContractEnum: `--op` is validated against Ops(), and the
// contract publishes the set of ops a caller may ask for. Two of them —
// `adopt` and `settings-put` — had no row in either table, so KnownOp said no
// and the CLI rejected an op the OpenAPI enum says is legal, while the daemon
// would have run the gate with no preconditions at all. The list below is
// copied from contracts/ctl/openapi.yaml (GET /v1/doctor, parameter `op`);
// drift in either direction is a failure here.
func TestOpsCoversTheContractEnum(t *testing.T) {
	contract := []string{
		"start", "stop", "restart", "backup", "restore", "handover",
		"migrate-local", "decommission", "key-mint", "key-revoke", "admin-add",
		"admin-remove", "sa-create", "sa-disable", "sa-enable", "env-set",
		"env-unset", "env-normalize", "render-units", "update-code", "create",
		"adopt", "gateway-apply", "settings-put",
		// PR-E's preparation ops. They have no HTTP ROUTE (they are
		// x-ctl-cli-op-args verbs), but they are ops a doctor run can be
		// scoped to — which is how an operator sees what would block one
		// before running it — so they are in the doctor `op` enum and must
		// have precondition rows like every other.
		"set-ui-mode", "set-bind",
		// PR-E2's two: the row repair a failed handover needs, and the
		// preparation step that makes a `new-tenant.sh` postgres startable by
		// the instance supervisor at all.
		"set-supervisor", "env-pg-password",
		// And the three that had no row at all until the PR-E2 review found
		// them. An op absent from the table has NO gate — RedCodes returns nil
		// — which is a different statement from "nothing blocks it", and these
		// three each have something that does: a host with no room, an
		// Elasticsearch that dies on vm.max_map_count, a tree the ctl account
		// cannot write. `ops.TestEveryVerbHasAPreconditionRow` is what keeps
		// the next one from being forgotten.
		"artifact-prepare", "create-sandbox", "gateway-reload",
	}
	if len(contract) != 31 {
		t.Fatalf("the op list has 31 entries, this copy has %d", len(contract))
	}
	got := Ops()
	if len(got) != len(contract) {
		t.Errorf("Ops() = %d entries %v, want %d", len(got), got, len(contract))
	}
	have := map[string]bool{}
	for _, op := range got {
		have[op] = true
	}
	for _, op := range contract {
		if !have[op] {
			t.Errorf("op %q is in the contract enum but has no precondition row", op)
		}
		if !KnownOp(op) {
			t.Errorf("KnownOp(%q) = false: the CLI would reject a legal op", op)
		}
		delete(have, op)
	}
	for op := range have {
		t.Errorf("op %q has a precondition row but is not in the contract enum", op)
	}
	// adopt records what it finds; only a store URL no op may dial and a
	// manifest row the registry does not know stop it.
	if got := strings.Join(RedCodes("adopt"), " "); got != StoreURLDisallowed+" "+ManifestUnknownRow {
		t.Errorf("RedCodes(adopt) = %q", got)
	}
	// settings-put has a row so the gate is a decision, not an omission.
	if got := RedCodes("settings-put"); len(got) != 0 {
		t.Errorf("RedCodes(settings-put) = %v, want none", got)
	}
}

// TestESSnapshotsDirMissingBlocksStart: the ES unit binds
// <data_dir>/elasticsearch/snapshots as path.repo, and apptainer refuses a
// bind whose SOURCE does not exist — so an absent directory is not a lost
// snapshot capability, it is a service that cannot start. Nothing created it
// before it joined paths.ProvisionDirs, so every script-provisioned tenant
// is in this state.
func TestESSnapshotsDirMissingBlocksStart(t *testing.T) {
	w := newWorld(t)
	snaps := filepath.Join(w.tenant.DataDir, "elasticsearch", "snapshots")
	if err := os.RemoveAll(snaps); err != nil {
		t.Fatal(err)
	}
	f, ok := byCode(w.run(t))[ESSnapshotsDirMissing]
	if !ok {
		t.Fatalf("no %s finding", ESSnapshotsDirMissing)
	}
	if f.Level != model.LevelWarn {
		t.Errorf("level = %s, want warn (a stopped tenant is not broken by it)", f.Level)
	}
	if !strings.Contains(f.Detail, snaps) {
		t.Errorf("detail does not name the directory: %s", f.Detail)
	}
	// Raised to an error for the ops that start the store, and for nothing else.
	for _, op := range []string{"start", "restart"} {
		w.opts.Op = op
		if got := w.run(t).Status; got != model.StatusRed {
			t.Errorf("--op %s = %s, want red: the unit cannot start", op, got)
		}
	}
	w.opts.Op = "stop"
	if got := w.run(t).Status; got == model.StatusRed {
		t.Error("--op stop must not block on a missing bind source: stopping needs no bind")
	}
	// A shared ES is not this tenant's to provision, so nothing is raised.
	w.opts.Op = ""
	w.tenant.Stores.Elasticsearch.Ownership = registry.OwnershipShared
	if _, ok := byCode(w.run(t))[ESSnapshotsDirMissing]; ok {
		t.Error("a shared elasticsearch raised es_snapshots_dir_missing")
	}
}

// TestSecretsUnreadableByCtlIsInfoBeforeHandover: on the host as it stands,
// every tenant is still owned by the operator who provisioned it and its env
// files are 0600. The daemon cannot seed the log redactor from them, so it
// refuses the logs endpoint — and doctor has to SAY so, at info, or the
// refusal looks like a defect on the dashboard.
func TestSecretsUnreadableByCtlIsInfoBeforeHandover(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root reads a 0000 file, so the permission case cannot be staged")
	}
	w := newWorld(t)
	w.host.Self = DefaultCtlUser // the daemon's account …
	w.tenant.Owner = "wilke"     // … and a tenant it has not been handed yet
	secrets := filepath.Join(w.tenant.DataDir, "config", "secrets.env")
	write(t, secrets, "TENANT_PG_PASSWORD=hunter2hunter2\n")
	if err := os.Chmod(secrets, 0o000); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chmod(secrets, 0o600) })

	resp := w.run(t)
	got := byCode(resp)
	f, ok := got[SecretsUnreadableByCtl]
	if !ok {
		t.Fatalf("no %s finding; the dashboard cannot explain the refused logs tab", SecretsUnreadableByCtl)
	}
	if f.Level != model.LevelInfo {
		t.Errorf("level = %s, want info: this is the designed pre-handover state, not a fault", f.Level)
	}
	if string(f.Tenant) != "dev" {
		t.Errorf("finding is not scoped to the tenant: %+v", f)
	}
	for _, want := range []string{DefaultCtlUser, "secrets.env", "wilke", "409"} {
		if !strings.Contains(f.Detail, want) {
			t.Errorf("detail %q does not carry %q", f.Detail, want)
		}
	}
	if strings.Contains(f.Detail, "hunter2hunter2") {
		t.Errorf("a finding must never quote the value: %q", f.Detail)
	}
	// And it does not ALSO come back as a broken env file: an unreadable file
	// is not an ungrammatical one, and reporting it at error (this tenant is
	// systemd-supervised) would gate every op behind a file mode that is
	// nobody's fault until the handover.
	w.tenant.Supervisor = string(model.SupervisorSystemd)
	if f, bad := byCode(w.run(t))[EnvNotSystemdParsable]; bad {
		t.Errorf("an unreadable secrets.env was also reported as unparsable: %+v", f)
	}
	// Green once the stores are confirmed: the unreadable secrets file itself
	// contributes only an info row, which is the claim under test. (Before the
	// confirmation the fleet is yellow for stores_unconfirmed, which has
	// nothing to do with a file mode.)
	w.confirmStores()
	if got := w.run(t); got.Status != model.StatusGreen {
		t.Errorf("status = %s, want green: an info finding is not a defect", got.Status)
	}
}

// The same unreadable file for a tenant this account OWNS is not this
// finding: nothing is pending, so it is not softened to info here.
func TestSecretsUnreadableIsNotReportedForAnOwnedTenant(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root reads a 0000 file, so the permission case cannot be staged")
	}
	w := newWorld(t)
	w.host.Self = DefaultCtlUser
	w.tenant.Owner = DefaultCtlUser
	secrets := filepath.Join(w.tenant.DataDir, "config", "secrets.env")
	write(t, secrets, "TENANT_PG_PASSWORD=hunter2hunter2\n")
	if err := os.Chmod(secrets, 0o000); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chmod(secrets, 0o600) })

	if f, bad := byCode(w.run(t))[SecretsUnreadableByCtl]; bad {
		t.Errorf("%s claimed a pending handover for a tenant this account owns: %+v", SecretsUnreadableByCtl, f)
	}
}

// TestPostgresStoreFindings covers the +5 port once a tenant's relational
// store is first-class in the registry (#535). The three kinds answer the
// same question differently, and the pair of findings has to stay
// complementary: exactly one of them can be true for a given port, never both
// and never neither.
func TestPostgresStoreFindings(t *testing.T) {
	local := func(w *world) {
		w.tenant.Stores.Postgres = registry.Postgres{
			Kind: registry.PostgresKindLocal, Ownership: registry.OwnershipExclusive,
			URL: "postgresql://localhost:24045", Port: registry.NullPort(w.tenant.Ports.PG),
			Instance: "postgres-dev",
			SIF:      registry.NullString(filepath.Join(w.roots.ImagesDir, "postgres.sif")),
			DataDir:  registry.NullString(filepath.Join(w.tenant.DataDir, "postgres")),
		}
	}

	// The regression #535 names: before the row existed, a dedicated
	// postgres on the +5 port was a stranger in the tenant's own block and
	// doctor said so on every single pass.
	t.Run("a dedicated instance on the pg port is no longer unexpected", func(t *testing.T) {
		w := newWorld(t)
		local(w)
		w.host.Ports = append(w.host.Ports, hostfacts.Listener{Port: w.tenant.Ports.PG, Pid: 77, User: "wilke", Cmdline: []string{"postgres", "-c", "port=24045"}})
		got := byCode(w.run(t))
		if f, ok := got[UnexpectedListener]; ok {
			t.Errorf("unexpected_listener = %+v; the port is the tenant's own with kind=local", f)
		}
		if f, ok := got[PostgresNotListening]; ok {
			t.Errorf("postgres_not_listening = %+v with a live listener", f)
		}
	})

	t.Run("an active tenant with its dedicated store down is a warning", func(t *testing.T) {
		w := newWorld(t)
		local(w)
		got := byCode(w.run(t))
		f, ok := got[PostgresNotListening]
		if !ok || f.Level != model.LevelWarn || f.Tenant != "dev" {
			t.Fatalf("postgres_not_listening = %+v (present %v), want a warn on dev", f, ok)
		}
		if !strings.Contains(f.Detail, "postgres-dev") || !strings.Contains(f.Detail, "24045") {
			t.Errorf("detail = %q, want the instance name and the port", f.Detail)
		}
		if _, ok := got[UnexpectedListener]; ok {
			t.Error("nothing listens on the port: it cannot also be an unexpected listener")
		}
	})

	t.Run("a stopped tenant's store is meant to be down", func(t *testing.T) {
		w := newWorld(t)
		local(w)
		w.tenant.State = "stopped"
		w.host.Ports = nil
		if _, ok := byCode(w.run(t))[PostgresNotListening]; ok {
			t.Error("a stopped tenant's stores are stopped too: no finding")
		}
	})

	// sqlite and external keep the OLD behaviour for the +5 port, and that is
	// the point: neither binds it, so a listener there is still a stranger.
	for _, c := range []struct {
		name string
		pg   registry.Postgres
	}{
		{"sqlite", registry.SQLiteStore()},
		{"external", registry.Postgres{Kind: registry.PostgresKindExternal, Ownership: registry.OwnershipExternal, URL: "postgresql://db.example.org:5432", Port: 5432}},
	} {
		t.Run(c.name+" does not claim the pg port", func(t *testing.T) {
			w := newWorld(t)
			w.tenant.Stores.Postgres = c.pg
			w.host.Ports = append(w.host.Ports, hostfacts.Listener{Port: w.tenant.Ports.PG, Pid: 77, User: "wilke", Cmdline: []string{"postgres"}})
			got := byCode(w.run(t))
			if f := got[UnexpectedListener]; f.Level != model.LevelWarn {
				t.Errorf("unexpected_listener = %+v, want a warning: kind %q binds nothing on that port", f, c.pg.Kind)
			}
			if _, ok := got[PostgresNotListening]; ok {
				t.Errorf("kind %q has no dedicated instance to be down", c.pg.Kind)
			}
		})
	}
}

// ---------------------------------------------------------------- ACL grants

// TestACLGrantPresentIsInfo: the interim arrangement is REPORTED, not
// inferred. An operator reading a doctor run has to be able to see that
// svcbvbrc reaches the managed roots through an ACL rather than through
// ownership, because that is the fact the sysadmin migration will undo.
func TestACLGrantPresentIsInfo(t *testing.T) {
	w := newWorld(t)
	got := byCode(w.run(t))
	f, ok := got[ACLGrantPresent]
	if !ok || f.Level != model.LevelInfo {
		t.Fatalf("acl_grant_present = %+v, want an info finding", f)
	}
	if !strings.Contains(f.Detail, w.roots.DataDir) || !strings.Contains(f.Detail, DefaultCtlUser) {
		t.Errorf("the finding must name the account and the roots: %q", f.Detail)
	}
	if _, bad := got[CtlAccountNoAccess]; bad {
		t.Error("a granted root must not also be reported as unreachable")
	}
}

// TestACLGrantWithoutADefaultIsCalledOut: a grant that reached today's files
// but set no default ACL silently stops at the next tenant created under the
// root. The info finding says so rather than reading as "all good".
func TestACLGrantWithoutADefaultIsCalledOut(t *testing.T) {
	w := newWorld(t)
	w.host.ACLs[w.roots.DataDir] = hostfacts.FakeACL{Access: grantedACL(ctlUID)} // no Default
	f := byCode(w.run(t))[ACLGrantPresent]
	if !strings.Contains(f.Detail, "DEFAULT ACL") {
		t.Fatalf("a grant with no default ACL must say so: %q", f.Detail)
	}
}

func TestCtlAccountNoAccessIsWarnAndBlocksCreate(t *testing.T) {
	w := newWorld(t)
	delete(w.host.ACLs, w.roots.DataDir) // the grant never ran for this root
	got := byCode(w.run(t))
	f, ok := got[CtlAccountNoAccess]
	if !ok || f.Level != model.LevelWarn {
		t.Fatalf("ctl_account_no_access = %+v, want a warn finding", f)
	}
	if !strings.Contains(f.Repair, "fleet grant") {
		t.Errorf("the repair must be the command that fixes it: %q", f.Repair)
	}
	// The three ops that write under a managed root cannot proceed over it.
	for _, op := range []string{"create", "backup", "restore"} {
		w.opts.Op = op
		if got := byCode(w.run(t))[CtlAccountNoAccess]; got.Level != model.LevelError {
			t.Errorf("op %q: level %s, want error", op, got.Level)
		}
	}
	// A read-only op still only warns.
	w.opts.Op = ""
	if got := byCode(w.run(t))[CtlAccountNoAccess]; got.Level != model.LevelWarn {
		t.Errorf("with no op the level is %s, want warn", got.Level)
	}
}

// TestOwningTheRootNeedsNoGrant: ownership carries everything an ACL could
// add, so the post-migration state (svcbvbrc owns /rag/data/tenants) must not
// report a missing grant.
func TestOwningTheRootNeedsNoGrant(t *testing.T) {
	w := newWorld(t)
	for _, root := range []string{w.roots.DataDir, w.roots.ReposDir} {
		delete(w.host.ACLs, root)
		w.host.Writables[root] = hostfacts.Writability{Owner: DefaultCtlUser}
	}
	got := byCode(w.run(t))
	if f, bad := got[CtlAccountNoAccess]; bad {
		t.Fatalf("the owner of a root was told it has no access: %q", f.Detail)
	}
	if _, present := got[ACLGrantPresent]; present {
		t.Error("ownership is not an ACL grant and must not be reported as one")
	}
}

// TestACLGrantsOthersIsRed: the check writable_by_others could not make. A
// named entry is invisible to the mode bits, so a 0750 unit file can still be
// rewritable by an account nobody intended.
func TestACLGrantsOthersIsRed(t *testing.T) {
	cases := []struct {
		name  string
		grant hostfacts.ACLGrant
		want  bool
		why   string
	}{
		{"a stranger with write", hostfacts.ACLGrant{
			Owner: "wilke", Name: "mallory", ID: 6001, Perm: "rw-",
		}, true, "a named user nobody granted"},
		{"any named group", hostfacts.ACLGrant{
			Owner: "wilke", Group: true, Name: "cels", ID: 20001, Perm: "rwx",
		}, true, "1869 people is exactly what the ACL scheme avoids"},
		{"the service account", hostfacts.ACLGrant{
			Owner: "wilke", Name: DefaultCtlUser, ID: 10078, Perm: "rwx",
		}, false, "that IS the grant fleet grant writes"},
		{"the owner's own redundant entry", hostfacts.ACLGrant{
			Owner: "wilke", Name: "wilke", ID: 1000, Perm: "rwx",
		}, false, "the owner already has the owner triple"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			w := newWorld(t)
			write(t, unitPath(w), "[Unit]\n")
			w.host.Writables[unitPath(w)] = hostfacts.Writability{
				Owner: "wilke", ACLGrants: []hostfacts.ACLGrant{withPath(c.grant, unitPath(w))},
			}
			got := byCode(w.run(t))
			f, raised := got[ACLGrantsOthers]
			if raised != c.want {
				t.Fatalf("acl_grants_others raised=%v, want %v (%s): %+v", raised, c.want, c.why, f)
			}
			if c.want && f.Level != model.LevelError {
				t.Errorf("level %s, want error", f.Level)
			}
			if c.want && !strings.Contains(f.Detail, c.grant.Name) {
				t.Errorf("the finding must name who holds the grant: %q", f.Detail)
			}
		})
	}
}

// TestReadOnlyACLEntriesAreNotAFinding: hostfacts only carries entries with
// WRITE, and doctor must not invent a finding for anything else. A read-only
// grant cannot replace what the ctl executes, which is what this check is for.
func TestReadOnlyACLEntriesAreNotAFinding(t *testing.T) {
	w := newWorld(t)
	write(t, unitPath(w), "[Unit]\n")
	w.host.Writables[unitPath(w)] = hostfacts.Writability{Owner: "wilke"} // no write grants carried
	if f, bad := byCode(w.run(t))[ACLGrantsOthers]; bad {
		t.Fatalf("a path with no write grants raised %+v", f)
	}
}

func unitPath(w *world) string {
	return filepath.Join(w.roots.UnitsDir(), "ragstack-dev-api.service")
}

func withPath(g hostfacts.ACLGrant, path string) hostfacts.ACLGrant {
	g.Path = path
	return g
}

// handover's preconditions depend on the runtime the tenant is moving ONTO,
// and the selection is on the live path: Options.Destination reaches
// applyPreconditions, which is what decides whether a finding is red.
//
// The two lists differ by five findings, and the difference is the point:
// gating this deployment's handover on linger/drop-in/runtime-dir — three root
// items no host here has — would refuse every handover the control plane can
// actually perform, while dropping boot_cron_missing would let one through onto
// a host where nothing restarts the tenant after a reboot.
func TestHandoverPreconditionsFollowTheDestination(t *testing.T) {
	instance := map[string]bool{}
	for _, c := range RedCodesForDestination("handover", SupervisorInstance) {
		instance[c] = true
	}
	systemd := map[string]bool{}
	for _, c := range RedCodesForDestination("handover", SupervisorSystemd) {
		systemd[c] = true
	}
	for _, c := range []string{LingerMissing, UserDropInMissing, RuntimeDirMissing} {
		if instance[c] {
			t.Errorf("%s gates an INSTANCE handover: PR-D2 postponed systemd, and no host here has it", c)
		}
		if !systemd[c] {
			t.Errorf("%s does not gate a SYSTEMD handover, which is the runtime that needs it", c)
		}
	}
	for _, c := range []string{BootCronMissing, CtlAccountNoAccess} {
		if !instance[c] {
			t.Errorf("%s does not gate an instance handover: nothing would bring the tenant back at boot", c)
		}
	}
	// Shared by both: these are facts about the TENANT, not about the runtime.
	for _, c := range []string{
		EnvNotSystemdParsable, PortOwnerMismatch, WorktreeOutsideMirror,
		WorktreeGitdirUnreadable, WritableByOthers, StoresUnconfirmed,
	} {
		if !instance[c] || !systemd[c] {
			t.Errorf("%s should gate a handover onto either supervisor", c)
		}
	}
	// port_not_listening gates NEITHER, and is tolerated by both. A handover
	// is two jobs run by two accounts and the second acts on a tenant the
	// first stopped: between the release and the take nothing is listening, by
	// construction, so raising this code refused every take there will ever
	// be. "Is there a running tenant to hand over" is the release planner's
	// question, asked of `state`, which does not misfire on the phase whose
	// whole precondition is that the tenant is down.
	if instance[PortNotListening] || systemd[PortNotListening] {
		t.Error("port_not_listening gates a handover: the take acts on a tenant the release has already stopped")
	}
	if got := Tolerated("handover"); len(got) != 1 || got[0] != PortNotListening {
		t.Errorf("Tolerated(handover) = %v, want [%s]", got, PortNotListening)
	}
	// And the engine's gate asks for the set MINUS what the op tolerates: a
	// yellow `port_not_listening` must not demand an acknowledgement from the
	// very op whose precondition is that the tenant is down.
	for _, c := range GateCodes("handover", SupervisorInstance) {
		if c == PortNotListening {
			t.Error("GateCodes(handover) still contains port_not_listening")
		}
	}
	for _, c := range GateCodes("env-normalize", "") {
		if c == EnvNotSystemdParsable {
			t.Error("GateCodes(env-normalize) demands an acknowledgement for the finding it repairs")
		}
	}
	for _, c := range GateCodes("start", "") {
		if c == PortNotListening {
			t.Error("GateCodes(start) demands an acknowledgement for a tenant that is down — which is when it runs")
		}
	}
	// The default is what RedCodes (and therefore the engine) gates on.
	if got, want := RedCodes("handover"), RedCodesForDestination("handover", DefaultHandoverDestination); len(got) != len(want) {
		t.Errorf("RedCodes(handover) = %v, want the default destination's %v", got, want)
	}
	// An unknown destination falls back to the default rather than to NO gate.
	if got := RedCodesForDestination("handover", "kubernetes"); len(got) == 0 {
		t.Error("an unknown destination turned the precondition table off")
	}
	// Every other op ignores the destination entirely.
	if a, b := RedCodesForDestination("backup", SupervisorSystemd), RedCodes("backup"); len(a) != len(b) {
		t.Errorf("backup's preconditions changed with the destination: %v vs %v", a, b)
	}
}

// And the wiring itself: a doctor run scoped to `handover` raises what the
// DESTINATION says it should. The systemd trio is the visible difference, so
// that is what this drives through Run.
func TestTheDestinationReachesTheDoctorRun(t *testing.T) {
	w := newWorld(t)
	w.confirmStores()
	w.host.Lingering = map[string]bool{} // no linger for anyone: the systemd gate

	instance := byCode(w.runOpts(t, Options{Op: "handover", Destination: SupervisorInstance}))
	if f, ok := instance[LingerMissing]; ok && f.Level == model.LevelError {
		t.Errorf("an instance handover was refused for want of linger: %+v", f)
	}
	systemd := byCode(w.runOpts(t, Options{Op: "handover", Destination: SupervisorSystemd}))
	if f, ok := systemd[LingerMissing]; !ok || f.Level != model.LevelError {
		t.Errorf("a systemd handover was NOT refused for want of linger: %+v", f)
	}
}

// TestTheBootRecordIsADeploymentFactNotAStateDirFact is the owner-side
// handover's precondition, and the reason it is written this way.
//
// `handover --release` and `handover --abandon` run as the tenant's owner
// through `--direct`, in a scratch CTL_STATE_DIR — they have to, because the
// daemon's jobs.db belongs to the service account and no other account can
// write it. Reading the boot record only out of the CONFIGURED state dir made
// `boot_cron_missing` (a red precondition for a handover onto `instance`) fire
// for every one of those runs: a handover refused because the operator was not
// the daemon.
func TestTheBootRecordIsADeploymentFactNotAStateDirFact(t *testing.T) {
	ragRoot := t.TempDir()
	canonical := filepath.Join(ragRoot, "data", "ctl")
	if err := os.MkdirAll(canonical, 0o770); err != nil {
		t.Fatal(err)
	}
	rec := `{"cron":true,"line":"@reboot /rag/bin/ragstack-ctl fleet start --all","at":"2026-09-15T11:18:10Z"}`
	if err := os.WriteFile(filepath.Join(canonical, BootRecordFile), []byte(rec), 0o640); err != nil {
		t.Fatal(err)
	}
	scratch := t.TempDir()

	for _, tc := range []struct {
		name  string
		state string
	}{
		{"the daemon's own state dir", canonical},
		{"an owner-side scratch state dir", scratch},
	} {
		t.Run(tc.name, func(t *testing.T) {
			roots := paths.NewRoots(ragRoot, paths.Overrides{CtlStateDir: tc.state})
			d := &run{roots: roots, opts: Options{CtlUser: "svcbvbrc", CtlUID: 10078}, host: &hostfacts.Fake{Lingering: map[string]bool{}}}
			d.bootHook()
			var codes []string
			for _, f := range d.findings {
				codes = append(codes, f.Code)
			}
			if len(codes) != 1 || codes[0] != BootCronPresent {
				t.Fatalf("findings = %v, want just %s", codes, BootCronPresent)
			}
		})
	}
}

// TestAnEmptyPreconditionRowIsNotTheSameAsAnUnknownOp is the doctor half of
// the gate bug that refused `ragstack-ctl env pg-password hackathon` on a host
// whose only warnings were the systemd trio PR-D2 abandoned.
//
// `env-pg-password` has a row — an EMPTY one, deliberately: it rewrites
// secrets.env, touches no process, and is run on a tenant whose findings are
// often exactly what the preparation exists to clear. But GateCodes built its
// answer with `var out []string` and appended nothing, so an empty row came
// back as NIL — indistinguishable from the nil an unknown op returns, which
// the engine's gate reads as "unknown op, take every warning to be this op's".
// An op that deliberately gates on nothing was gated on everything.
func TestAnEmptyPreconditionRowIsNotTheSameAsAnUnknownOp(t *testing.T) {
	// A row that raises nothing, and a row whose every entry is tolerated,
	// both mean "this op gates on no warning" — and both must SAY so.
	for _, op := range []string{"env-pg-password", "env-normalize"} {
		got := GateCodes(op, "")
		if len(got) != 0 {
			t.Errorf("GateCodes(%s) = %v, want an empty set", op, got)
		}
		if got == nil {
			t.Errorf("GateCodes(%s) is nil: an empty precondition row is reported as an UNKNOWN op, and the "+
				"engine's yellow gate then demands force_with_doctor_diff for warnings %s does not depend on", op, op)
		}
		if !KnownOp(op) {
			t.Errorf("KnownOp(%s) = false; the row exists", op)
		}
	}
	// A verb nobody wrote a row for is the one case that stays nil: the gate
	// has nothing to scope itself by, and TestEveryVerbHasAPreconditionRow
	// is what keeps that case from being reachable through a real op.
	if got := GateCodes("no-such-verb", ""); got != nil {
		t.Errorf("GateCodes(no-such-verb) = %v, want nil for an op the table does not know", got)
	}
	if KnownOp("no-such-verb") {
		t.Error("KnownOp(no-such-verb) = true")
	}
}

// --------------------------------------------------------------------------
// What a handover leaves behind, and who owns the job store
// --------------------------------------------------------------------------

// localPostgres turns the world's dev tenant into one with a dedicated
// postgres on the block's +5 port, the only kind whose data directory this
// deployment owns — and therefore the only kind a handover has to copy.
func (w *world) localPostgres() {
	w.tenant.Stores.Postgres = registry.Postgres{
		Kind: registry.PostgresKindLocal, Ownership: registry.OwnershipExclusive,
		URL:      registry.NullString(fmt.Sprintf("postgresql://localhost:%d", w.tenant.Ports.PG)),
		Port:     registry.NullPort(w.tenant.Ports.PG),
		Instance: registry.NullString("postgres-dev"),
		DataDir:  registry.NullString(filepath.Join(w.tenant.DataDir, "postgres")),
	}
}

// TestAPreHandoverCopyIsNamedUntilSomebodyRemovesIt. A handover's take cannot
// start postgres on a data directory it does not own — postgres compares
// st_uid with geteuid() and refuses — so it copies, and renames the original
// out of the way rather than deleting it. Keeping it is CORRECT: it is the
// last copy of the tenant's database as the releasing account had it, and
// `--abandon` renames it back. But the commit clears the handover block, and
// from that moment the registry no longer remembers the directory at all: the
// only place the fact still lives is the disk, and a second copy of a
// production database on a filesystem every tenant shares is not a thing to
// leave unnamed.
func TestAPreHandoverCopyIsNamedUntilSomebodyRemovesIt(t *testing.T) {
	w := newWorld(t)
	w.localPostgres()
	left := filepath.Join(w.tenant.DataDir, "postgres", PreHandoverDirPrefix+"20260917T083000Z")
	if err := os.MkdirAll(left, 0o700); err != nil {
		t.Fatal(err)
	}

	found := findingsForCode(w.run(t), PreHandoverCopyPresent)
	if len(found) != 1 {
		t.Fatalf("got %d %s findings, want exactly 1: %+v", len(found), PreHandoverCopyPresent, found)
	}
	f := found[0]
	// Info, not a warning. Nothing is broken and no op should be gated on it;
	// what it needs is to stay visible.
	if f.Level != model.LevelInfo {
		t.Errorf("level = %s, want info: leaving the copy is correct until the handover has soaked", f.Level)
	}
	if f.Tenant != "dev" {
		t.Errorf("tenant = %q, want dev: this is a tenant finding, not a host one", f.Tenant)
	}
	// The PATH is the finding — the size deliberately is not, because `du`
	// over a postgres data directory on every doctor poll is IO nobody asked
	// for, and the operator about to delete it will run `du` once themselves.
	if !strings.Contains(f.Detail, left) {
		t.Errorf("the finding does not name the directory: %q", f.Detail)
	}
	if !strings.Contains(f.Repair, "rm -rf "+left) {
		t.Errorf("the repair is not the command that clears it: %q", f.Repair)
	}
}

// TestNoPreHandoverCopyIsNoFinding covers the two silences. A doctor that
// raised this row on every tenant with a postgres would put a permanent info
// line on a fleet where no handover has ever run, and the operator who learns
// to scroll past it is the operator who scrolls past the real one.
func TestNoPreHandoverCopyIsNoFinding(t *testing.T) {
	t.Run("a local postgres with nothing left behind", func(t *testing.T) {
		w := newWorld(t)
		w.localPostgres()
		// The live data directory exists; only the pre-handover sibling does not.
		if err := os.MkdirAll(filepath.Join(w.tenant.DataDir, "postgres", "data"), 0o700); err != nil {
			t.Fatal(err)
		}
		if found := findingsForCode(w.run(t), PreHandoverCopyPresent); len(found) != 0 {
			t.Fatalf("a tenant with no pre-handover copy raised %+v", found)
		}
	})

	// A directory with the right NAME under a tenant whose postgres this
	// deployment does not own says nothing about a handover: the check exists
	// because the take copies a local data directory, and there is nothing to
	// copy on a sqlite or external leg. The baseline tenant is `sqlite`.
	t.Run("a postgres leg that is not local", func(t *testing.T) {
		w := newWorld(t)
		if w.tenant.Stores.Postgres.Kind == registry.PostgresKindLocal {
			t.Fatal("the baseline tenant is local; this case asserts nothing")
		}
		if err := os.MkdirAll(
			filepath.Join(w.tenant.DataDir, "postgres", PreHandoverDirPrefix+"20260917T083000Z"), 0o700); err != nil {
			t.Fatal(err)
		}
		if found := findingsForCode(w.run(t), PreHandoverCopyPresent); len(found) != 0 {
			t.Fatalf("a %s postgres raised %+v", w.tenant.Stores.Postgres.Kind, found)
		}
	})
}

// TestAForeignJobStoreIsRed is 2026-09-17 as a finding. A wilke `--direct` run
// against CTL_STATE_DIR=/rag/data/ctl left wilke-owned `jobs.db-wal` and
// `jobs.db-shm` beside svcbvbrc's `jobs.db`, and SQLite opens a WAL database
// read-write only when it can write all three files — so the daemon's whole
// mutation surface answered 409 refused for a day while reads, doctor and the
// dashboard all worked perfectly. Nothing was visibly wrong, which is why this
// has to be a finding and not only a log line.
//
// The check compares OWNERS rather than trying to open the database: doctor
// often runs as an operator who is not the daemon's account at all, and "can I
// write it" would then answer a question nobody asked.
func TestAForeignJobStoreIsRed(t *testing.T) {
	w := newWorld(t)
	store := filepath.Join(w.roots.CtlStateDir, "jobs.db")
	if err := os.MkdirAll(w.roots.CtlStateDir, 0o755); err != nil {
		t.Fatal(err)
	}
	write(t, store, "sqlite")
	w.opts.CtlUser = DefaultCtlUser

	// The store belongs to whoever ran this test, so a daemon account that IS
	// this process owns its own store and there is nothing to say.
	w.opts.CtlUID = os.Geteuid()
	if found := findingsForCode(w.run(t), JobEngineUnavailable); len(found) != 0 {
		t.Fatalf("a store owned by the daemon's own account raised %+v", found)
	}

	// …and a daemon account that is anybody else cannot write it.
	w.opts.CtlUID = os.Geteuid() + 1
	found := findingsForCode(w.run(t), JobEngineUnavailable)
	if len(found) != 1 {
		t.Fatalf("got %d %s findings, want exactly 1: %+v", len(found), JobEngineUnavailable, found)
	}
	f := found[0]
	if f.Level != model.LevelError {
		t.Errorf("level = %s, want error: every mutation on this daemon is refused", f.Level)
	}
	if f.Tenant != "" {
		t.Errorf("tenant = %q; the job store is a host fact, not a tenant's", f.Tenant)
	}
	if !strings.Contains(f.Detail, store) {
		t.Errorf("the finding does not name the store: %q", f.Detail)
	}
	// The SIDECARS are the mechanism and the reason the finding is not
	// obvious: an operator looking at a jobs.db they can read has no reason to
	// suspect the two files beside it.
	if !strings.Contains(f.Repair, "jobs.db-wal") || !strings.Contains(f.Repair, "jobs.db-shm") {
		t.Errorf("the repair does not name the sidecars to remove: %q", f.Repair)
	}
	// And the repair must not say "restart the daemon": it retries the store
	// in the background and recovers on its own.
	if !strings.Contains(f.Repair, "background") {
		t.Errorf("the repair does not say the daemon recovers by itself: %q", f.Repair)
	}
}

// TestNoCtlUIDIsNoJobStoreGuess. On a developer checkout, or a host where the
// service account does not exist, there is no reference account to compare
// against — and a check that guessed would raise a red finding on every laptop
// that ever ran the test suite. Silence beats a guess.
func TestNoCtlUIDIsNoJobStoreGuess(t *testing.T) {
	w := newWorld(t)
	if err := os.MkdirAll(w.roots.CtlStateDir, 0o755); err != nil {
		t.Fatal(err)
	}
	write(t, filepath.Join(w.roots.CtlStateDir, "jobs.db"), "sqlite")
	write(t, filepath.Join(w.roots.CtlStateDir, "jobs.db-wal"), "wal")
	for _, uid := range []int{0, -1} {
		w.opts.CtlUID = uid
		if found := findingsForCode(w.run(t), JobEngineUnavailable); len(found) != 0 {
			t.Errorf("CtlUID %d raised %+v; there is no account to compare against", uid, found)
		}
	}
}
