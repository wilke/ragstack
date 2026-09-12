package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

func capture(t *testing.T, args ...string) (int, string, string) {
	t.Helper()
	var out, errb bytes.Buffer
	stdout, stderr = &out, &errb
	defer func() { stdout, stderr = os.Stdout, os.Stderr }()
	rc := run(args)
	return rc, out.String(), errb.String()
}

func TestExitCodes(t *testing.T) {
	cases := []struct {
		args []string
		rc   int
	}{
		{nil, exitUsage},
		{[]string{"bogus"}, exitUsage},
		{[]string{"adopt", "dev"}, exitUsage},
		{[]string{"key"}, exitUsage},
		{[]string{"serve", "--registry", "/nonexistent/registry.json"}, exitError},
		{[]string{"paths", "validate"}, exitUsage},
		{[]string{"paths", "validate", "dev"}, exitOK},
		{[]string{"paths", "validate", "admin"}, exitError},
		{[]string{"paths", "validate", "Dev"}, exitError},
		{[]string{"render"}, exitUsage},
		{[]string{"render", "tenant-env"}, exitUsage},
		{[]string{"render", "tenant-env", "acme", "--store", "postgres"}, exitError},
		{[]string{"render", "nginx", "--registry", "/nonexistent/registry.json"}, exitError},
		{[]string{"version"}, exitOK},
		{[]string{"help"}, exitOK},
	}
	for _, c := range cases {
		if rc, _, _ := capture(t, c.args...); rc != c.rc {
			t.Errorf("%v: rc %d, want %d", c.args, rc, c.rc)
		}
	}
}

func TestVersionJSON(t *testing.T) {
	_, out, _ := capture(t, "version")
	var v map[string]any
	if err := json.Unmarshal([]byte(out), &v); err != nil {
		t.Fatalf("not JSON: %q", out)
	}
	for _, k := range []string{"version", "commit", "built_at", "go", "schema_version"} {
		if _, ok := v[k]; !ok {
			t.Errorf("missing %s", k)
		}
	}
}

func TestRenderNeverPrintsSecrets(t *testing.T) {
	_, env, _ := capture(t, "render", "tenant-env", "acme", "--index", "3")
	if !strings.Contains(env, "<GENERATED:API_KEY_USER>") || !strings.Contains(env, "PORT=24060") {
		t.Errorf("env: %s", env)
	}
	_, pg, _ := capture(t, "render", "tenant-env", "acme", "--store", "postgres", "--pg-host", "db", "--pg-port", "5433")
	if !strings.Contains(pg, "POSTGRES_DSN=postgresql+asyncpg://acme:<GENERATED:PG_PASSWORD>@db:5433/acme") {
		t.Errorf("pg env: %s", pg)
	}
	_, up, _ := capture(t, "render", "up-sh", "acme", "--es-heap", "2g")
	if !strings.Contains(up, `IMG="/rag/apptainer/images"`) || !strings.Contains(up, "-Xms2g -Xmx2g") {
		t.Errorf("up.sh: %s", up)
	}
	_, down, _ := capture(t, "render", "down-sh", "acme")
	if !strings.HasPrefix(down, "#!/usr/bin/env bash\n# Stop the dedicated stores for tenant 'acme'") {
		t.Errorf("down.sh: %s", down)
	}
}

func TestRenderFromRegistry(t *testing.T) {
	dir := t.TempDir()
	reg := filepath.Join(dir, "registry.json")
	f := registry.LiveFixture()
	if err := registry.Save(reg, f, "test"); err != nil {
		t.Fatal(err)
	}
	rc, maps, errs := capture(t, "render", "nginx", "--registry", reg)
	if rc != exitOK || !strings.Contains(maps, `lucid-next  "127.0.0.1:24000";`) {
		t.Errorf("nginx: rc %d %s %s", rc, maps, errs)
	}
	rc, static, _ := capture(t, "render", "nginx", "--registry", reg, "--kind", "static")
	if rc != exitOK || !strings.HasPrefix(static, "# tenants-ui-static.generated.conf") {
		t.Errorf("static: rc %d %s", rc, static)
	}
	// The live tenants bind 0.0.0.0 — a fact the fixture keeps — and
	// rendering a unit that RE-CREATES that on an internet-reachable host is
	// a deliberate act: refused by default, named by the operator otherwise.
	if rc, _, errs := capture(t, "render", "units", "dev", "--registry", reg); rc != exitError ||
		!strings.Contains(errs, "not loopback") {
		t.Errorf("a 0.0.0.0 bind must be refused without the flag: rc %d %s", rc, errs)
	}
	rc, units, errs := capture(t, "render", "units", "dev", "--registry", reg, "--allow-non-loopback-bind")
	if rc != exitOK || !strings.Contains(units, "-- unit: ragstack-dev-api.service --") || !strings.Contains(units, "-- unit: ragstack-dev-qdrant.service --") {
		t.Errorf("units: rc %d %s %s", rc, units, errs)
	}
	if !strings.Contains(units, "--host 0.0.0.0") {
		t.Errorf("the opted-in render keeps the recorded bind: %s", units)
	}
	if rc, _, _ := capture(t, "render", "units", "nope", "--registry", reg); rc != exitError {
		t.Errorf("unknown tenant rc %d", rc)
	}
	// A tenant with no desired_boot value never reaches the renderer at all
	// any more: Save validates against the contract before writing, and Load
	// against the same contract on the way back in, so the unpinned row
	// cannot exist on disk. (render.Units still refuses it directly —
	// render.TestUnitsGoldens covers that — for a Tenant built in memory.)
	f.Tenants["dev"].DesiredBoot = ""
	if err := registry.Save(reg, f, "test"); err == nil {
		t.Error("Save wrote a row whose desired_boot is outside the contract enum")
	}
}

// TestDoctorRejectsUnknownOp is S15. RedCodes returns nil for an op nobody
// wrote a row for, so `--op strat` did not fail — it silently ran doctor with
// NO preconditions at all and reported green. A typo in a runbook bought a
// mutation past the gate.
func TestDoctorRejectsUnknownOp(t *testing.T) {
	dir := t.TempDir()
	reg := filepath.Join(dir, "registry.json")
	if err := registry.Save(reg, registry.LiveFixture(), "test"); err != nil {
		t.Fatal(err)
	}
	rc, _, errs := capture(t, "doctor", "--op", "strat", "--registry", reg, "--rag-root", dir)
	if rc != exitUsage {
		t.Errorf("unknown --op rc = %d, want %d", rc, exitUsage)
	}
	if !strings.Contains(errs, "unknown --op") || !strings.Contains(errs, "known ops:") {
		t.Errorf("the error must name the op and list the known ones: %q", errs)
	}
	// A known op is still accepted (it may be red on this host, which is
	// exitRefused, or green; either way it is not a usage error).
	if rc, _, _ := capture(t, "doctor", "--op", "start", "--registry", reg, "--rag-root", dir); rc == exitUsage {
		t.Error("--op start was rejected as a usage error")
	}
}

// TestAdoptCommitRefusesErrorFindings is S14. An error-level finding is the
// preview saying this row does not describe something the ctl can safely
// operate; committing it anyway wrote a row every later op would refuse to
// act on, and did it silently — the findings scrolled past above the
// "committed" line.
func TestAdoptCommitRefusesErrorFindings(t *testing.T) {
	ragRoot := t.TempDir()
	dataDir := filepath.Join(ragRoot, "data", "tenants", "acme")
	worktree := filepath.Join(ragRoot, "repos", "tenants", "acme")
	for _, d := range []string{filepath.Join(dataDir, "config"), worktree} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	// A store URL the ctl refuses to dial — loopback and contract-shaped, but
	// neither in this tenant's own port block nor an external store port, so
	// the probe gate rejects it. An error-level finding no later op could
	// proceed over.
	//
	// PORT is block 49, which NOTHING listens on: this test used to sit on
	// 24040 and so passed only on coconut, where the live dev tenant answers
	// there with `--host 0.0.0.0`. Off that host — a CI runner, a dev laptop,
	// or coconut itself from inside a rootless container, where the
	// listener→pid mapping is unreadable — the API was unobservable, adopt
	// recorded an empty api.bind and registry.Save refused the row, so the
	// --force leg exited 3. The fix is adopt's assumed-bind default; this
	// port makes the test exercise it instead of the live host.
	env := "PORT=24980\nLOG_LEVEL=info\nQDRANT_URL=http://localhost:29999\nELASTICSEARCH_URL=http://localhost:9200\n"
	if err := os.WriteFile(filepath.Join(dataDir, "config", "tenant.env"), []byte(env), 0o600); err != nil {
		t.Fatal(err)
	}
	reg := filepath.Join(ragRoot, "data", "tenants", "registry.json")
	args := []string{"adopt", "acme", "--data-dir", dataDir, "--worktree", worktree,
		"--registry", reg, "--rag-root", ragRoot}

	// --preview always prints and never writes.
	if rc, _, errs := capture(t, append(append([]string{}, args...), "--preview")...); rc != exitOK {
		t.Fatalf("preview rc = %d: %s", rc, errs)
	}
	if _, err := os.Stat(reg); err == nil {
		t.Fatal("--preview wrote the registry")
	}

	// --commit refuses, names the finding, and writes nothing.
	rc, _, errs := capture(t, append(append([]string{}, args...), "--commit")...)
	if rc != exitRefused {
		t.Errorf("commit over an error finding rc = %d, want %d (refused)", rc, exitRefused)
	}
	if !strings.Contains(errs, "store_url_disallowed") || !strings.Contains(errs, "--force") {
		t.Errorf("the refusal must name the finding and the override: %q", errs)
	}
	if _, err := os.Stat(reg); err == nil {
		t.Fatal("a refused commit wrote the registry anyway")
	}

	// --force records the row as it stands.
	if rc, _, errs := capture(t, append(append([]string{}, args...), "--commit", "--force")...); rc != exitOK {
		t.Fatalf("--force rc = %d: %s", rc, errs)
	}
	f, err := registry.LoadNoRepair(reg)
	if err != nil {
		t.Fatal(err)
	}
	tn, ok := f.Tenants["acme"]
	if !ok {
		t.Fatal("--force did not write the row")
	}
	// The row a stopped tenant gets: the loopback default, not "" (unsavable)
	// and not 0.0.0.0 (a guess that publishes an API to the internet).
	if tn.API.Bind != "127.0.0.1" {
		t.Errorf("api.bind = %q for a tenant nothing listens for, want 127.0.0.1", tn.API.Bind)
	}
}

// TestWaitReadyIsImplemented is S25: every rendered api unit carries
// `ExecStartPre=… wait-ready <name> --timeout S`, so a verb that exits 2
// would fail the start of every tenant on the host.
func TestWaitReadyIsImplemented(t *testing.T) {
	dir := t.TempDir()
	reg := filepath.Join(dir, "registry.json")
	f := registry.LiveFixture()
	if err := registry.Save(reg, f, "test"); err != nil {
		t.Fatal(err)
	}
	// No name: a usage error, as for every other verb.
	if rc, _, _ := capture(t, "wait-ready", "--registry", reg); rc != exitUsage {
		t.Error("wait-ready with no tenant should be a usage error")
	}
	if rc, _, _ := capture(t, "wait-ready", "dev", "--registry", reg, "--timeout", "0"); rc != exitUsage {
		t.Error("a non-positive timeout should be a usage error")
	}
	if rc, _, _ := capture(t, "wait-ready", "nope", "--registry", reg); rc != exitError {
		t.Error("an unknown tenant should be an error")
	}
	// asm-next owns NO store exclusively (both are shared), so there is
	// nothing to wait for and the unit must start immediately rather than
	// sit in ExecStartPre for somebody else's store.
	rc, out, errs := capture(t, "wait-ready", "asm-next", "--registry", reg)
	if rc != exitOK || !strings.Contains(out, "no exclusively-owned store") {
		t.Errorf("shared-store tenant: rc %d out %q err %q", rc, out, errs)
	}
	// A tenant that owns its stores, on a port block nothing on this host
	// holds, times out — bounded, and as a failure rather than a hang.
	dev := f.Tenants["dev"]
	dev.Ports = paths.Block(900) // 42000-42005, well clear of the live fleet
	dev.Stores.Qdrant.URL = "http://127.0.0.1:42001"
	dev.Stores.Elasticsearch.URL = "http://127.0.0.1:42003"
	if err := registry.Save(reg, f, "test"); err != nil {
		t.Fatal(err)
	}
	start := time.Now()
	rc, _, errs = capture(t, "wait-ready", "dev", "--registry", reg, "--timeout", "1")
	if rc != exitError || !strings.Contains(errs, "did not become ready") {
		t.Errorf("timeout: rc %d err %q", rc, errs)
	}
	if elapsed := time.Since(start); elapsed > 10*time.Second {
		t.Errorf("--timeout 1 waited %s: the bound is not a bound", elapsed)
	}
}

// TestRegistryRepairIsAVerbNotASideEffect: a READ must never rewrite
// manifest.tsv — repairing on read would truncate a live four-row manifest to
// match a one-tenant registry, and the next apptainer/new-tenant.sh would then
// reissue index 0 onto a live tenant's ports. The read reports it; the verb
// fixes it.
func TestRegistryRepairIsAVerbNotASideEffect(t *testing.T) {
	dir := t.TempDir()
	reg := filepath.Join(dir, "registry.json")
	if err := registry.Save(reg, registry.LiveFixture(), "test"); err != nil {
		t.Fatal(err)
	}
	man := registry.PathsFor(reg).Manifest
	good, err := os.ReadFile(man)
	if err != nil {
		t.Fatal(err)
	}
	stale := append(append([]byte{}, good...), []byte("ghost\t99\t25980\n")...)
	if err := os.WriteFile(man, stale, 0o644); err != nil {
		t.Fatal(err)
	}

	// A read says so on stderr and leaves the file alone.
	rc, _, errs := capture(t, "tenant", "list", "--registry", reg, "--rag-root", dir)
	if rc != exitOK {
		t.Errorf("tenant list rc = %d", rc)
	}
	if !strings.Contains(errs, "manifest_projection_stale") || !strings.Contains(errs, "registry repair") {
		t.Errorf("a read must report the stale projection and name the verb: %q", errs)
	}
	if now, _ := os.ReadFile(man); !bytes.Equal(now, stale) {
		t.Fatal("a READ rewrote manifest.tsv")
	}

	// A commit refuses over it rather than repairing silently…
	spec := filepath.Join(dir, "spec.json")
	dataDir := filepath.Join(dir, "data", "tenants", "acme")
	worktree := filepath.Join(dir, "repos", "tenants", "acme")
	for _, d := range []string{filepath.Join(dataDir, "config"), worktree} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(dataDir, "config", "tenant.env"), []byte("PORT=24140\nLOG_LEVEL=info\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	body := fmt.Sprintf(`[{"name":"acme","data_dir":%q,"worktree":%q}]`, dataDir, worktree)
	if err := os.WriteFile(spec, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	rc, _, errs = capture(t, "adopt-all", "--commit", "--spec", spec, "--registry", reg, "--rag-root", dir)
	if rc != exitRefused || !strings.Contains(errs, "manifest_projection_stale") {
		t.Errorf("commit over a stale projection: rc %d err %q", rc, errs)
	}
	if now, _ := os.ReadFile(man); !bytes.Equal(now, stale) {
		t.Fatal("a refused commit rewrote manifest.tsv")
	}

	// …and the explicit verb rewrites it FROM the registry.
	rc, out, errs := capture(t, "registry", "repair", "--registry", reg, "--rag-root", dir)
	if rc != exitOK || !strings.Contains(out, "repaired") {
		t.Fatalf("registry repair: rc %d out %q err %q", rc, out, errs)
	}
	if now, _ := os.ReadFile(man); !bytes.Equal(now, good) {
		t.Errorf("repair did not restore the projection:\n%s", now)
	}
	if rc, _, errs := capture(t, "tenant", "list", "--registry", reg, "--rag-root", dir); rc != exitOK ||
		strings.Contains(errs, "manifest_projection_stale") {
		t.Errorf("after the repair the read should be quiet: rc %d err %q", rc, errs)
	}
	if rc, _, _ := capture(t, "registry"); rc != exitUsage {
		t.Error("`registry` with no verb should be a usage error")
	}
}

// TestRenderScriptsUseTheRegistryRow is item 5 of the PR-A review.
//
// `render tenant-env|up-sh|down-sh` ignored the registry entirely and built a
// throwaway tenant at index 0 with a 512m heap. For a tenant that EXISTS that
// is not a rehearsal, it is a wrong answer: the printed up.sh starts that
// tenant's stores on ports 24001-24004, which belong to whoever holds index 0.
// The registry is the allocator; --index and --es-heap are for a name it does
// not know yet.
func TestRenderScriptsUseTheRegistryRow(t *testing.T) {
	dir := t.TempDir()
	reg := filepath.Join(dir, "registry.json")
	f := registry.LiveFixture()
	if err := registry.Save(reg, f, "test"); err != nil {
		t.Fatal(err)
	}
	// lucid-next is index 0 with a 2g heap and a data dir named `lucid`.
	rc, up, errs := capture(t, "render", "up-sh", "lucid-next", "--registry", reg)
	if rc != exitOK {
		t.Fatalf("rc %d: %s", rc, errs)
	}
	for _, want := range []string{
		`TDIR="/rag/data/tenants/lucid"`, "-Xms2g -Xmx2g",
		"http :24001, grpc :24002",
	} {
		if !strings.Contains(up, want) {
			t.Errorf("up.sh lacks %q:\n%s", want, up)
		}
	}
	// asm-next is index 1: its env file must carry ITS ports, not 24000's.
	rc, env, errs := capture(t, "render", "tenant-env", "asm-next", "--registry", reg)
	if rc != exitOK {
		t.Fatalf("rc %d: %s", rc, errs)
	}
	if !strings.Contains(env, "PORT=24020") || !strings.Contains(env, "QDRANT_URL=http://localhost:24021") {
		t.Errorf("tenant-env used the flag default instead of the row:\n%s", env)
	}
	if !strings.Contains(env, "USER_STORE_PATH=/rag/data/tenants/asm/state/") {
		t.Errorf("tenant-env used the derived data dir, not the row's:\n%s", env)
	}
	// Quoting an allocator flag at an existing tenant is refused, not ignored.
	for _, args := range [][]string{
		{"render", "up-sh", "lucid-next", "--registry", reg, "--index", "7"},
		{"render", "up-sh", "lucid-next", "--registry", reg, "--es-heap", "8g"},
	} {
		rc, _, errs := capture(t, args...)
		if rc != exitError || !strings.Contains(errs, "does not exist yet") {
			t.Errorf("%v: rc %d err %q", args, rc, errs)
		}
	}
	// A name the registry does not know is still greenfield, flags and all.
	rc, up, errs = capture(t, "render", "up-sh", "acme", "--registry", reg, "--index", "3", "--es-heap", "4g")
	if rc != exitOK || !strings.Contains(up, "-Xms4g -Xmx4g") || !strings.Contains(up, "http :24061") {
		t.Errorf("greenfield render: rc %d %s %s", rc, up, errs)
	}
	// And so is every name when there is no registry at all.
	rc, up, errs = capture(t, "render", "up-sh", "acme", "--registry", filepath.Join(dir, "nope.json"))
	if rc != exitOK || !strings.Contains(up, "http :24001") {
		t.Errorf("no registry must not fail a greenfield render: rc %d %s %s", rc, up, errs)
	}
}

// TestRenderHonoursTheGlobalRagRoot is item 6: `render`'s own --rag-root
// defaulted to the literal "/rag" instead of the global flag's value, so
// `ragstack-ctl --rag-root /tmp/x render up-sh acme` silently rendered /rag
// paths — and the operator rehearsing against a scratch root got a script
// pointed at the live deployment.
func TestRenderHonoursTheGlobalRagRoot(t *testing.T) {
	rc, up, errs := capture(t, "--rag-root", "/srv/rag", "render", "up-sh", "acme")
	if rc != exitOK {
		t.Fatalf("rc %d: %s", rc, errs)
	}
	if !strings.Contains(up, `TDIR="/srv/rag/data/tenants/acme"`) || !strings.Contains(up, `IMG="/srv/rag/apptainer/images"`) {
		t.Errorf("the global --rag-root was ignored:\n%s", up)
	}
	// The subcommand flag still wins when it is given explicitly.
	rc, up, _ = capture(t, "--rag-root", "/srv/rag", "render", "up-sh", "acme", "--rag-root", "/opt/rag")
	if rc != exitOK || !strings.Contains(up, `TDIR="/opt/rag/data/tenants/acme"`) {
		t.Errorf("the explicit --rag-root must win:\n%s", up)
	}
}

// TestESSeedConfig is item 4: the ES unit's ExecStartPre. The unit binds the
// tenant's elasticsearch/config over the image's own copy, so an EMPTY host
// directory hands Elasticsearch no jvm.options and no log4j2.properties and
// it exits before logging anything useful. bin/up.sh seeded it (seed_if_empty)
// and the unit did not.
func TestESSeedConfig(t *testing.T) {
	dir := t.TempDir()
	reg := filepath.Join(dir, "registry.json")
	f := registry.LiveFixture()
	dev := f.Tenants["dev"]
	dev.DataDir = filepath.Join(dir, "data", "tenants", "dev")
	dev.Stores.Elasticsearch.SIF = registry.NullString(filepath.Join(dir, "elasticsearch.sif"))
	if err := registry.Save(reg, f, "test"); err != nil {
		t.Fatal(err)
	}
	cfgDir := filepath.Join(dev.DataDir, "elasticsearch", "config")
	if err := os.MkdirAll(cfgDir, 0o755); err != nil {
		t.Fatal(err)
	}

	var argv []string
	real := esSeedConfig
	esSeedConfig = func(bin string, args ...string) error {
		argv = append([]string{bin}, args...)
		return os.WriteFile(filepath.Join(cfgDir, "jvm.options"), []byte("-Xss1m\n"), 0o644)
	}
	realMMC := esSeedMaxMapCount
	esSeedMaxMapCount = func(paths.Roots) (int, error) { return 65530, nil }
	defer func() { esSeedConfig, esSeedMaxMapCount = real, realMMC }()

	rc, out, errs := capture(t, "es-seed-config", "dev", "--registry", reg)
	if rc != exitOK {
		t.Fatalf("rc %d: %s", rc, errs)
	}
	if !strings.Contains(out, "seeding "+cfgDir) {
		t.Errorf("out = %q", out)
	}
	// The command is argv-only: no shell, nothing a tenant's own config could
	// reach, and the bind source is the tenant's own config directory.
	wantArgv := []string{
		"/usr/bin/apptainer", "exec", "--bind", cfgDir + ":/__seed",
		string(dev.Stores.Elasticsearch.SIF),
		"cp", "-R", "/usr/share/elasticsearch/config/.", "/__seed/",
	}
	if strings.Join(argv, "\x00") != strings.Join(wantArgv, "\x00") {
		t.Errorf("argv  = %v\nwant = %v", argv, wantArgv)
	}
	// The max_map_count preflight warns (and does not refuse: the sysctl is
	// the host admin's, and refusing here takes the store down on a reboot
	// that lost it).
	if !strings.Contains(errs, "vm.max_map_count=65530") || !strings.Contains(errs, "262144") {
		t.Errorf("no max_map_count warning: %q", errs)
	}

	// A populated directory is left strictly alone — an operator's own
	// elasticsearch.yml is never overwritten.
	argv = nil
	rc, out, _ = capture(t, "es-seed-config", "dev", "--registry", reg)
	if rc != exitOK || argv != nil || !strings.Contains(out, "already populated") {
		t.Errorf("a populated config dir was re-seeded: rc %d argv %v out %q", rc, argv, out)
	}
	// A shared store is not the ctl's to seed.
	rc, out, _ = capture(t, "es-seed-config", "asm-next", "--registry", reg)
	if rc != exitOK || !strings.Contains(out, "not exclusively owned") {
		t.Errorf("shared ES: rc %d out %q", rc, out)
	}
	// Usage and unknown-tenant behave like every other verb.
	if rc, _, _ := capture(t, "es-seed-config", "--registry", reg); rc != exitUsage {
		t.Error("no tenant should be a usage error")
	}
	if rc, _, _ := capture(t, "es-seed-config", "nope", "--registry", reg); rc != exitError {
		t.Error("an unknown tenant should be an error")
	}
	// A missing bind source is fatal here rather than at the failed start.
	if err := os.RemoveAll(cfgDir); err != nil {
		t.Fatal(err)
	}
	if rc, _, errs := capture(t, "es-seed-config", "dev", "--registry", reg); rc != exitError || !strings.Contains(errs, cfgDir) {
		t.Errorf("missing config dir: rc %d err %q", rc, errs)
	}
}
