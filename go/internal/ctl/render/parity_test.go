package render

import (
	"bytes"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// The oracle: apptainer/new-tenant.sh --dry-run. Until the script is retired
// its output defines tenant.env, up.sh, down.sh, the port math and the
// manifest row; these tests hold the Go renderers to it byte-for-byte.
const (
	scriptRel   = "../../../../apptainer/new-tenant.sh"
	dryRunGold  = "../testdata/live-2026-09-10/new-tenant-dryrun/ctltest.txt"
	parityBase  = 41000
	parityImage = "images"
)

var sectionHeader = regexp.MustCompile(`^-- (.*) --$`)

// plan is a parsed dry-run.
type plan struct {
	header   map[string]string // "RAG_DATA", "RAG_IMAGES", "tenant dir", "acl/registry store", "port block"
	sections map[string]string // header text → body (files: exact bytes)
	order    []string
}

func parsePlan(t *testing.T, out string) *plan {
	t.Helper()
	p := &plan{header: map[string]string{}, sections: map[string]string{}}
	lines := strings.Split(out, "\n")
	// Header block: "== new-tenant plan: X ==" then "key:  value" until blank.
	i := 0
	if !strings.HasPrefix(lines[0], "== new-tenant plan: ") {
		t.Fatalf("not a dry-run plan: %q", lines[0])
	}
	for i = 1; i < len(lines) && lines[i] != ""; i++ {
		k, v, ok := strings.Cut(lines[i], ":")
		if !ok {
			t.Fatalf("header line %q", lines[i])
		}
		p.header[k] = strings.TrimSpace(v)
	}
	// Sections: "-- name --" … up to the blank line preceding the next header.
	var cur string
	var body []string
	flush := func() {
		if cur == "" {
			return
		}
		// The script prints `echo` (one blank line) after every section body.
		if n := len(body); n > 0 && body[n-1] == "" {
			body = body[:n-1]
		}
		p.sections[cur] = strings.Join(body, "\n") + "\n"
		p.order = append(p.order, cur)
	}
	for ; i < len(lines); i++ {
		if m := sectionHeader.FindStringSubmatch(lines[i]); m != nil {
			flush()
			cur, body = m[1], nil
			continue
		}
		if cur != "" {
			body = append(body, lines[i])
		}
	}
	// The final section (start/stop) has no trailing echo; keep its body as is.
	if cur != "" {
		for n := len(body); n > 0 && body[n-1] == ""; n = len(body) {
			body = body[:n-1]
		}
		p.sections[cur] = strings.Join(body, "\n") + "\n"
		p.order = append(p.order, cur)
	}
	return p
}

func (p *plan) file(t *testing.T, path string) string {
	t.Helper()
	s, ok := p.sections["file: "+path]
	if !ok {
		t.Fatalf("plan has no section for file %s (have %v)", path, p.order)
	}
	return s
}

// runScript runs the oracle for name under ragData; nil when bash or the
// script are unavailable (the caller replays the golden instead).
func runScript(t *testing.T, ragData, name string, extra ...string) (string, bool) {
	t.Helper()
	if _, err := exec.LookPath("bash"); err != nil {
		return "", false
	}
	if _, err := os.Stat(scriptRel); err != nil {
		return "", false
	}
	if err := os.MkdirAll(filepath.Join(ragData, parityImage), 0o755); err != nil {
		t.Fatal(err)
	}
	args := append([]string{scriptRel, name, "--dry-run"}, extra...)
	cmd := exec.Command("bash", args...)
	cmd.Env = append(os.Environ(),
		"RAG_DATA="+ragData,
		"RAG_IMAGES="+filepath.Join(ragData, parityImage),
		fmt.Sprintf("TENANT_PORT_BASE=%d", parityBase),
		"TENANT_PORT_STRIDE=20",
	)
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	if err := cmd.Run(); err != nil {
		if strings.Contains(stderr.String(), "already in use") {
			skipOrFail(t, "parity port block %d busy on this host: %s", parityBase, strings.TrimSpace(stderr.String()))
		}
		t.Fatalf("new-tenant.sh failed: %v\n%s", err, stderr.String())
	}
	return stdout.String(), true
}

// parityRequired reports whether this run must actually exercise the live
// script. Every skip below is a real gap in coverage — a developer laptop
// without bash, a busy port block — and a suite that is allowed to skip
// silently is a suite that can go green having tested nothing. CI sets
// CTL_PARITY_REQUIRED=1 so those become failures there.
func parityRequired() bool { return os.Getenv("CTL_PARITY_REQUIRED") == "1" }

// skipOrFail skips the test, or fails it when CTL_PARITY_REQUIRED=1.
func skipOrFail(t *testing.T, format string, args ...any) {
	t.Helper()
	if parityRequired() {
		t.Fatalf("CTL_PARITY_REQUIRED=1: "+format, args...)
	}
	t.Skipf(format, args...)
}

// oracle returns a parsed plan for name, live when possible, else replayed
// from the golden (only ctltest / sqlite is captured).
func oracle(t *testing.T, name string, extra ...string) (*plan, string) {
	t.Helper()
	ragData := t.TempDir()
	if out, ok := runScript(t, ragData, name, extra...); ok {
		return parsePlan(t, out), ragData
	}
	if name != "ctltest" || len(extra) > 0 {
		skipOrFail(t, "bash or new-tenant.sh unavailable; only the ctltest golden is replayable offline")
	}
	b, err := os.ReadFile(dryRunGold)
	if err != nil {
		skipOrFail(t, "no oracle: %v", err)
	}
	p := parsePlan(t, string(b))
	return p, p.header["RAG_DATA"]
}

func parityTenant(name string, index int, ragData string) *registry.Tenant {
	r := paths.NewRoots(ragData, paths.Overrides{DataDir: filepath.Join(ragData, "tenants")})
	tp := paths.TenantPaths(r, name, name)
	return &registry.Tenant{Name: name, ManifestName: name, DataDir: tp.DataDir, Ports: paths.BlockAt(parityBase, paths.PortStride, index)}
}

// normalizeGenerator rewrites the attribution line so the ctl's and the
// script's tenant.env compare byte-for-byte everywhere else.
func normalizeGenerator(s string) string {
	return strings.Replace(s, "(generated by "+ScriptGenerator+")", "(generated by ragstack-ctl)", 1)
}

func assertParity(t *testing.T, p *plan, ragData, name string, index int, extra EnvOptions) {
	t.Helper()
	tn := parityTenant(name, index, ragData)
	tdir := tn.DataDir
	if got := p.header["tenant dir"]; got != tdir {
		t.Errorf("tenant dir: script %q paths %q", got, tdir)
	}
	// Ports.
	ports := p.sections["ports"]
	for _, w := range []string{
		fmt.Sprintf("api:            %d", tn.Ports.API),
		fmt.Sprintf("qdrant http:    %d", tn.Ports.QdrantHTTP),
		fmt.Sprintf("qdrant grpc:    %d", tn.Ports.QdrantGRPC),
		fmt.Sprintf("es http:        %d", tn.Ports.ESHTTP),
		fmt.Sprintf("es transport:   %d", tn.Ports.ESTransport),
		fmt.Sprintf("postgres:       %d (reserved", tn.Ports.PG),
	} {
		if !strings.Contains(ports, w) {
			t.Errorf("ports section lacks %q:\n%s", w, ports)
		}
	}
	// Manifest row.
	if got := p.sections[fmt.Sprintf("manifest row (%s/tenants/manifest.tsv)", ragData)]; got != fmt.Sprintf("%s\t%d\t%d\n", name, index, tn.Ports.Base) {
		t.Errorf("manifest row: %q", got)
	}
	// Directories == paths.ProvisionDirs, same order.
	tp := paths.TenantPaths(paths.NewRoots(ragData, paths.Overrides{DataDir: filepath.Join(ragData, "tenants")}), name, name)
	if got := p.sections["directories (mkdir -p; every writable path enumerated — house rule: no tmpfs overlays)"]; got != strings.Join(tp.ProvisionDirs(), "\n")+"\n" {
		t.Errorf("directories:\n got %q\nwant %q", got, tp.ProvisionDirs())
	}
	// Files.
	images := filepath.Join(ragData, parityImage)
	env, err := TenantEnv(tn, extra)
	if err != nil {
		t.Fatal(err)
	}
	if want := normalizeGenerator(p.file(t, tp.TenantEnv)); string(env) != want {
		t.Errorf("tenant.env parity:\n%s", diffLines(want, string(env)))
	}
	up, err := UpSh(tn, StoreOptions{Images: images})
	if err != nil {
		t.Fatal(err)
	}
	if want := p.file(t, filepath.Join(tdir, "bin", "up.sh")); string(up) != want {
		t.Errorf("up.sh parity:\n%s", diffLines(want, string(up)))
	}
	down, err := DownSh(tn)
	if err != nil {
		t.Fatal(err)
	}
	if want := p.file(t, filepath.Join(tdir, "bin", "down.sh")); string(down) != want {
		t.Errorf("down.sh parity:\n%s", diffLines(want, string(down)))
	}
}

func diffLines(want, got string) string {
	w, g := strings.Split(want, "\n"), strings.Split(got, "\n")
	var b strings.Builder
	for i := 0; i < len(w) || i < len(g); i++ {
		var a, c string
		if i < len(w) {
			a = w[i]
		}
		if i < len(g) {
			c = g[i]
		}
		if a != c {
			fmt.Fprintf(&b, "line %d:\n  script: %q\n  render: %q\n", i+1, a, c)
		}
	}
	return b.String()
}

func TestParitySQLiteFreshManifest(t *testing.T) {
	p, ragData := oracle(t, "ctltest")
	if !strings.Contains(p.header["port block"], "new allocation, index 0") {
		t.Fatalf("port block: %q", p.header["port block"])
	}
	assertParity(t, p, ragData, "ctltest", 0, EnvOptions{DryRun: true})
}

func TestParitySecondTenantAfterManifestRow(t *testing.T) {
	ragData := t.TempDir()
	tenants := filepath.Join(ragData, "tenants")
	if err := os.MkdirAll(tenants, 0o755); err != nil {
		t.Fatal(err)
	}
	// Seed the manifest through the registry projection: the script must read
	// what the ctl writes.
	f := registry.NewFleet(ragData)
	f.PortBase = parityBase
	f.Tenants["ctltest"] = &registry.Tenant{Name: "ctltest", ManifestName: "ctltest", Ports: paths.BlockAt(parityBase, 20, 0)}
	if err := os.WriteFile(filepath.Join(tenants, "manifest.tsv"), registry.ProjectManifest(f), 0o644); err != nil {
		t.Fatal(err)
	}
	out, ok := runScript(t, ragData, "ctltest2")
	if !ok {
		skipOrFail(t, "bash or new-tenant.sh unavailable")
	}
	p := parsePlan(t, out)
	if !strings.Contains(p.header["port block"], "new allocation, index 1") {
		t.Fatalf("port block: %q", p.header["port block"])
	}
	idx, base := registry.Allocate(f)
	if idx != 1 || base != parityBase+20 {
		t.Fatalf("Allocate = %d/%d", idx, base)
	}
	assertParity(t, p, ragData, "ctltest2", 1, EnvOptions{DryRun: true})
}

func TestParityPostgres(t *testing.T) {
	p, ragData := oracle(t, "ctltest", "--postgres", "postgresql://u:p@h:5/db")
	if p.header["acl/registry store"] != "postgres" {
		t.Fatalf("store: %q", p.header["acl/registry store"])
	}
	if _, ok := p.sections["postgres provisioning (server h:5 via --postgres DSN)"]; !ok {
		t.Fatalf("no psql plan section: %v", p.order)
	}
	assertParity(t, p, ragData, "ctltest", 0, EnvOptions{DryRun: true, StoreKind: StorePostgres, PGHost: "h", PGPort: "5"})
}

func TestParityReservedNames(t *testing.T) {
	b, err := os.ReadFile(scriptRel)
	if err != nil {
		skipOrFail(t, "new-tenant.sh unavailable")
	}
	// The `case "$NAME" in` arm: a|b|c) die "… reserved …"
	re := regexp.MustCompile(`(?m)^\s*([a-z0-9|-]+)\)\s*\n?\s*die "tenant name '\$NAME' is reserved`)
	m := re.FindSubmatch(b)
	if m == nil {
		t.Fatal("reserved-name case arm not found in new-tenant.sh")
	}
	got := strings.Split(string(m[1]), "|")
	if strings.Join(got, " ") != strings.Join(paths.Reserved, " ") {
		t.Errorf("reserved names differ:\n script %v\n paths  %v", got, paths.Reserved)
	}
	// And the name regex.
	if !bytes.Contains(b, []byte(`^[a-z][a-z0-9-]{0,31}$`)) {
		t.Error("script name regex changed; update paths.ValidateName")
	}
}

func TestParityGoldenReplayParses(t *testing.T) {
	b, err := os.ReadFile(dryRunGold)
	if err != nil {
		t.Fatal(err)
	}
	p := parsePlan(t, string(b))
	ragData := p.header["RAG_DATA"]
	if !strings.HasPrefix(ragData, "/tmp/ctl-capture-") {
		t.Fatalf("golden RAG_DATA = %q", ragData)
	}
	assertParity(t, p, ragData, "ctltest", 0, EnvOptions{DryRun: true})
}
