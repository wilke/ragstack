package drivers

// PR-F F2: the driver half of "the API as an instance" — the `api` instance
// kind, --cleanenv, extra bind roots with per-root ro/rw, Labels, the
// instance-pid → port identity, and Git.Checkout.

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/hostfacts"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
)

// ---------------------------------------------------------------- names

func TestInstanceNameAllowlistAcceptsTheAPIKind(t *testing.T) {
	for _, ok := range []string{"api-dev", "api-hackathon", "api-x", "qdrant-dev", "elasticsearch-a1", "postgres-dev"} {
		if err := checkInstanceName(ok); err != nil {
			t.Errorf("checkInstanceName(%q) = %v, want accepted", ok, err)
		}
	}
	for _, bad := range []string{"foo-x", "api-", "api", "API-dev", "api-Dev", "api-1dev", "apix-dev", "api-dev x",
		"nginx-gateway", "uvicorn-dev", "api-" + strings.Repeat("a", 33)} {
		if err := checkInstanceName(bad); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("checkInstanceName(%q) = %v, want a refusal", bad, err)
		}
	}
}

// The fake and the real driver accept and refuse the same names.
func TestAPIInstanceNameParity(t *testing.T) {
	ctx := context.Background()
	fake := NewFake(FakeOptions{}).FakeInstances()
	real, s, _, sif := newInstances(t)
	s.respond("list", `{"instances":[]}`, "", 0)
	if err := fake.Run(ctx, jobs.InstanceSpec{Name: "api-x", SIF: sif}); err != nil {
		t.Errorf("fake Run(api-x) = %v", err)
	}
	if err := real.Run(ctx, jobs.InstanceSpec{Name: "api-x", SIF: sif}); err != nil {
		t.Errorf("real Run(api-x) = %v", err)
	}
	for _, name := range []string{"foo-x", "api-"} {
		fe := fake.Run(ctx, jobs.InstanceSpec{Name: name, SIF: sif})
		re := real.Run(ctx, jobs.InstanceSpec{Name: name, SIF: sif})
		if !errors.Is(fe, jobs.ErrRefused) || !errors.Is(re, jobs.ErrRefused) {
			t.Errorf("Run(%q): fake = %v, real = %v; both must refuse", name, fe, re)
		}
		if err := fake.Stop(ctx, name, jobs.StopOptions{}); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("fake Stop(%q) = %v, want a refusal", name, err)
		}
		if err := real.Stop(ctx, name, jobs.StopOptions{}); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("real Stop(%q) = %v, want a refusal", name, err)
		}
	}
}

// ---------------------------------------------------------------- --cleanenv

func TestInstancesRunPutsCleanEnvAfterNoHomeOnlyWhenAsked(t *testing.T) {
	for _, clean := range []bool{false, true} {
		d, s, root, sif := newInstances(t)
		s.respond("list", `{"instances":[]}`, "", 0)
		err := d.Run(context.Background(), jobs.InstanceSpec{
			Name: "api-dev", SIF: sif, CleanEnv: clean,
			Binds:    []string{filepath.Join(root, "storage") + ":" + filepath.Join(root, "storage")},
			Args:     []string{"--host", "127.0.0.1", "--port", "24040"},
			ExtraEnv: map[string]string{"APPTAINERENV_RAGSTACK_ADMIN_KEY": "s3cret-not-on-argv"},
		})
		if err != nil {
			t.Fatal(err)
		}
		got := s.argv()
		if len(got) != 2 {
			t.Fatalf("Run made %d calls: %v", len(got), got)
		}
		flag := ""
		if clean {
			flag = "--cleanenv "
		}
		want := "instance run --no-home " + flag +
			"--bind " + filepath.Join(root, "storage") + ":" + filepath.Join(root, "storage") + " " +
			sif + " api-dev --host 127.0.0.1 --port 24040"
		if got[1] != want {
			t.Errorf("CleanEnv=%v argv =\n%q\nwant\n%q", clean, got[1], want)
		}
		for _, line := range got {
			if strings.Contains(line, "s3cret") {
				t.Fatalf("an ExtraEnv value reached an argv: %q", line)
			}
		}
		if !containsLine(s.childEnv("run"), "APPTAINERENV_RAGSTACK_ADMIN_KEY=s3cret-not-on-argv") {
			t.Error("the APPTAINERENV_ entry did not reach apptainer's environment")
		}
	}
}

// ---------------------------------------------------------------- bind roots

// newInstancesWithExtraRoots is newInstances plus two extra roots: one rw and
// one ro, each a real directory outside the approved root.
func newInstancesWithExtraRoots(t *testing.T) (d *RealInstances, s *stub, approved, rw, ro, sif string) {
	t.Helper()
	d, s, approved, sif = newInstances(t)
	rw, ro = t.TempDir(), t.TempDir()
	for _, dir := range []string{filepath.Join(rw, "hub"), filepath.Join(ro, "ragstack"), filepath.Join(approved, "storage")} {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	d.ExtraRoots = []BindRoot{{Path: rw}, {Path: ro, ReadOnly: true}}
	s.respond("list", `{"instances":[]}`, "", 0)
	return d, s, approved, rw, ro, sif
}

func TestInstancesBindContainmentPerRoot(t *testing.T) {
	ctx := context.Background()
	d, _, approved, rw, ro, sif := newInstancesWithExtraRoots(t)
	run := func(bind string) error {
		return d.Run(ctx, jobs.InstanceSpec{Name: "api-dev", SIF: sif, Binds: []string{bind}})
	}
	for _, c := range []struct {
		name string
		bind string
		ok   bool
	}{
		// The approved root: unchanged.
		{"approved, rw", filepath.Join(approved, "storage") + ":/s", true},
		{"approved, ro", filepath.Join(approved, "storage") + ":/s:ro", true},
		{"approved root itself", approved + ":/s", false},
		// A read-write extra root: under it, and the root itself.
		{"rw root, under it", filepath.Join(rw, "hub") + ":/hub", true},
		{"rw root, under it, :rw", filepath.Join(rw, "hub") + ":/hub:rw", true},
		{"rw root, itself", rw + ":" + rw, true},
		{"rw root, itself, :ro", rw + ":" + rw + ":ro", true},
		// A read-only extra root: only :ro.
		{"ro root, under it, :ro", filepath.Join(ro, "ragstack") + ":/c:ro", true},
		{"ro root, itself, :ro", ro + ":" + ro + ":ro", true},
		{"ro root, under it, no mode (rw)", filepath.Join(ro, "ragstack") + ":/c", false},
		{"ro root, under it, :rw", filepath.Join(ro, "ragstack") + ":/c:rw", false},
		{"ro root, itself, no mode", ro + ":" + ro, false},
		{"ro root, itself, :rw", ro + ":" + ro + ":rw", false},
		// Outside every root.
		{"outside", t.TempDir() + ":/x:ro", false},
		{"the parent of an extra root", filepath.Dir(rw) + ":/x:ro", false},
		{"a traversal out of an extra root", rw + "/../x:/x:ro", false},
		{"an extra root with a trailing slash", rw + "/:/x", false},
	} {
		err := run(c.bind)
		if c.ok && err != nil {
			t.Errorf("%s: Run(bind %q) = %v, want accepted", c.name, c.bind, err)
		}
		if !c.ok && !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("%s: Run(bind %q) = %v, want a refusal", c.name, c.bind, err)
		}
	}
}

// The refusal says WHICH root made the bind read-only.
func TestInstancesROBindRootRefusalNamesTheRoot(t *testing.T) {
	d, s, _, _, ro, sif := newInstancesWithExtraRoots(t)
	err := d.Run(context.Background(), jobs.InstanceSpec{Name: "api-dev", SIF: sif,
		Binds: []string{filepath.Join(ro, "ragstack") + ":/c"}})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), ro) || !strings.Contains(err.Error(), ":ro") {
		t.Fatalf("Run = %v, want a refusal naming %s and :ro", err, ro)
	}
	s.ranNothing("a rw bind under a read-only root")
}

// A symlink planted under an extra root is refused the way one under an
// approved root is.
func TestInstancesExtraRootRefusesASymlinkedBind(t *testing.T) {
	d, _, _, rw, _, sif := newInstancesWithExtraRoots(t)
	link := filepath.Join(rw, "link")
	if err := os.Symlink(t.TempDir(), link); err != nil {
		t.Fatal(err)
	}
	err := d.Run(context.Background(), jobs.InstanceSpec{Name: "api-dev", SIF: sif, Binds: []string{link + ":/x"}})
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("Run with a symlinked bind under an extra root = %v, want a refusal", err)
	}
}

// The most specific root decides: an approved root nested inside a read-only
// extra root keeps its rw binds (the store binds of today never change), and
// a read-only extra root nested inside an approved root tightens it.
func TestInstancesMostSpecificBindRootWins(t *testing.T) {
	ctx := context.Background()
	outer := t.TempDir()
	approved := filepath.Join(outer, "config", "ctl")
	roInner := filepath.Join(outer, "data", "shared")
	for _, dir := range []string{filepath.Join(approved, "x"), filepath.Join(roInner, "y"), filepath.Join(outer, "data", "t")} {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	d, s, _, sif := newInstances(t)
	s.respond("list", `{"instances":[]}`, "", 0)
	d.Roots = []string{approved, filepath.Join(outer, "data")}
	d.ExtraRoots = []BindRoot{{Path: filepath.Join(outer, "config"), ReadOnly: true}, {Path: roInner, ReadOnly: true}}
	for _, c := range []struct {
		bind string
		ok   bool
	}{
		{filepath.Join(approved, "x") + ":/x", true},               // approved is more specific than the ro root
		{filepath.Join(outer, "config", "other") + ":/x", false},   // only the ro root covers it
		{filepath.Join(outer, "config", "other") + ":/x:ro", true}, // …and :ro is fine
		{filepath.Join(outer, "data", "t") + ":/t", true},          // approved, no ro root inside
		{filepath.Join(roInner, "y") + ":/y", false},               // ro root nested in an approved one
		{filepath.Join(roInner, "y") + ":/y:ro", true},             // …tightens, never loosens
	} {
		err := d.Run(ctx, jobs.InstanceSpec{Name: "api-dev", SIF: sif, Binds: []string{c.bind}})
		if c.ok && err != nil {
			t.Errorf("Run(bind %q) = %v, want accepted", c.bind, err)
		}
		if !c.ok && !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("Run(bind %q) = %v, want a refusal", c.bind, err)
		}
	}
}

// A driver configured with an unusable extra root refuses every bind rather
// than approving everything ("/") or guessing.
func TestInstancesRefuseAnUnusableExtraRoot(t *testing.T) {
	for _, bad := range []string{"/", "", "relative", "/a/../b", "/a/"} {
		d, s, approved, sif := newInstances(t)
		s.respond("list", `{"instances":[]}`, "", 0)
		d.ExtraRoots = []BindRoot{{Path: bad}}
		err := d.Run(context.Background(), jobs.InstanceSpec{Name: "api-dev", SIF: sif,
			Binds: []string{filepath.Join(approved, "storage") + ":/s"}})
		if !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("extra root %q: Run = %v, want a refusal", bad, err)
		}
	}
}

func TestCheckBindRoot(t *testing.T) {
	for _, ok := range []string{"/rag/cache", "/scout/containers", "/rag/config"} {
		if err := CheckBindRoot(BindRoot{Path: ok}); err != nil {
			t.Errorf("CheckBindRoot(%q) = %v", ok, err)
		}
	}
	for _, bad := range []string{"", "/", "rag/cache", "/rag/../etc", "/rag/cache/", "/rag//cache", "/rag/ca che"} {
		if err := CheckBindRoot(BindRoot{Path: bad}); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("CheckBindRoot(%q) = %v, want a refusal", bad, err)
		}
	}
}

// NewReal hands ExtraBindRoots to the instance driver and to NOTHING else:
// being bindable is not being writable by the ctl.
func TestNewRealWiresExtraBindRootsIntoInstancesOnly(t *testing.T) {
	extra := []BindRoot{{Path: "/rag/cache"}, {Path: "/rag/config", ReadOnly: true}}
	r := NewReal(RealOptions{ApprovedRoots: []string{"/rag/data/tenants"}, ExtraBindRoots: extra})
	if got := r.instances.ExtraRoots; len(got) != 2 || got[0] != extra[0] || got[1] != extra[1] {
		t.Fatalf("instances.ExtraRoots = %v, want %v", got, extra)
	}
	extra[0].Path = "/mutated"
	if r.instances.ExtraRoots[0].Path != "/rag/cache" {
		t.Error("NewReal kept the caller's slice; the roots are fixed at construction")
	}
	for name, roots := range map[string][]string{
		"files": r.files.Roots, "git": r.git.Roots, "build": r.build.Roots, "proc": r.proc.Roots, "instances": r.instances.Roots,
	} {
		for _, root := range roots {
			if root == "/rag/cache" || root == "/rag/config" {
				t.Errorf("the %s driver's approved roots include the extra bind root %s", name, root)
			}
		}
	}
}

// ---------------------------------------------------------------- Labels

// inspectJSON is `apptainer inspect --json --labels` of
// ragstack-tools-v1.6.5-b1.sif on coconut (apptainer 1.5.3), trimmed.
const inspectJSON = `{
	"data": {
		"attributes": {
			"labels": {
				"org.label-schema.build-arch": "amd64",
				"org.ragstack.build": "1",
				"org.ragstack.build-date": "2026-10-09T02:09:27Z",
				"org.ragstack.commit": "d5c970e28e4139caebe0a815c3ba6bd73b5e2b89",
				"org.ragstack.role": "worker",
				"org.ragstack.version": "v1.6.5"
			}
		}
	},
	"type": "container"
}
`

var inspectWant = map[string]string{
	"org.label-schema.build-arch": "amd64",
	"org.ragstack.build":          "1",
	"org.ragstack.build-date":     "2026-10-09T02:09:27Z",
	"org.ragstack.commit":         "d5c970e28e4139caebe0a815c3ba6bd73b5e2b89",
	"org.ragstack.role":           "worker",
	"org.ragstack.version":        "v1.6.5",
}

func sameLabels(a, b map[string]string) bool {
	if len(a) != len(b) {
		return false
	}
	for k, v := range a {
		if bv, ok := b[k]; !ok || bv != v {
			return false
		}
	}
	return true
}

func TestInstancesLabelsParsesWhatApptainerPrints(t *testing.T) {
	d, s, root, sif := newInstances(t)
	// keyVerb keys on $2: `inspect --json --labels <sif>` → "--json".
	s.respond("--json", inspectJSON, "", 0)
	got, err := d.Labels(context.Background(), sif)
	if err != nil {
		t.Fatal(err)
	}
	if !sameLabels(got, inspectWant) {
		t.Errorf("Labels = %v, want %v", got, inspectWant)
	}
	if argv := s.argv(); len(argv) != 1 || argv[0] != "inspect --json --labels "+sif {
		t.Errorf("Labels ran %v", argv)
	}
	// In the ctl's apptainer namespace, like every other call.
	if !containsLine(s.childEnv("--json"), "APPTAINER_CONFIGDIR="+filepath.Join(root, "apptainer", "config")) {
		t.Error("Labels ran without the ctl's APPTAINER_CONFIGDIR")
	}
}

func TestInstancesLabelsRefusesWhatItCannotRead(t *testing.T) {
	ctx := context.Background()
	d, s, root, _ := newInstances(t)
	for _, sif := range []string{"", "images/x.sif", filepath.Join(root, "absent.sif"), filepath.Join(root, "storage")} {
		if _, err := d.Labels(ctx, sif); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("Labels(%q) = %v, want a refusal", sif, err)
		}
	}
	s.ranNothing("an unusable image")

	d, s, _, sif := newInstances(t)
	s.respond("--json", "not json", "", 0)
	if _, err := d.Labels(ctx, sif); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("Labels of unparseable output = %v, want a refusal", err)
	}
	// A non-string label value never compares equal to a receipt string.
	got, err := parseInspectLabels(sif, []byte(`{"data":{"attributes":{"labels":{"n":1,"s":"x"}}}}`))
	if err != nil || got["n"] != "1" || got["s"] != "x" {
		t.Errorf("parseInspectLabels = %v, %v", got, err)
	}
	// An image with no labels at all is an empty map, not an error.
	got, err = parseInspectLabels(sif, []byte(`{"data":{"attributes":{"labels":null}},"type":"container"}`))
	if err != nil || len(got) != 0 {
		t.Errorf("parseInspectLabels of no labels = %v, %v", got, err)
	}
}

func TestFakeInstancesLabelsParity(t *testing.T) {
	ctx := context.Background()
	f := NewFake(FakeOptions{})
	fake := f.FakeInstances()
	real, s, _, sif := newInstances(t)
	s.respond("--json", inspectJSON, "", 0)

	// Unknown to the fake: refused, with the path in the error.
	if _, err := fake.Labels(ctx, sif); !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), sif) {
		t.Fatalf("fake Labels of an unknown image = %v, want a refusal naming it", err)
	}
	fake.SetLabels(sif, inspectWant)
	fl, ferr := fake.Labels(ctx, sif)
	rl, rerr := real.Labels(ctx, sif)
	if ferr != nil || rerr != nil || !sameLabels(fl, rl) {
		t.Fatalf("Labels: fake = %v, %v; real = %v, %v; must agree", fl, ferr, rl, rerr)
	}
	// The answer is a copy.
	fl["org.ragstack.role"] = "server"
	if again, _ := fake.Labels(ctx, sif); again["org.ragstack.role"] != "worker" {
		t.Error("mutating the returned map changed the fixture")
	}
	// The same refusals for a path neither could use.
	for _, bad := range []string{"", "relative.sif", "/a/../b.sif"} {
		_, fe := fake.Labels(ctx, bad)
		_, re := real.Labels(ctx, bad)
		if !errors.Is(fe, jobs.ErrRefused) || !errors.Is(re, jobs.ErrRefused) {
			t.Errorf("Labels(%q): fake = %v, real = %v; both must refuse", bad, fe, re)
		}
	}
	fake.DeleteLabels(sif)
	if _, err := fake.Labels(ctx, sif); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("Labels after DeleteLabels = %v, want a refusal", err)
	}
	if n := f.Count("instances.Labels"); n < 4 {
		t.Errorf("instances.Labels recorded %d calls", n)
	}
}

// The real thing, against a SIF on this host, when apptainer is installed.
func TestInstancesLabelsAgainstRealApptainer(t *testing.T) {
	bin, ok := execLooksAvailable("apptainer")
	if !ok {
		t.Skip("apptainer is not on PATH")
	}
	var sif string
	for _, pattern := range []string{"/scout/containers/ragstack/*.sif", "/rag/apptainer/images/*.sif"} {
		if m, _ := filepath.Glob(pattern); len(m) > 0 {
			sif = m[0]
			break
		}
	}
	if sif == "" {
		t.Skip("no SIF under /scout/containers/ragstack or /rag/apptainer/images")
	}
	state := t.TempDir()
	d := &RealInstances{run: &runner{}, Bin: bin, Env: apptainerEnv(state), ConfigDir: apptainerConfigDir(state)}
	got, err := d.Labels(context.Background(), sif)
	if err != nil {
		t.Fatalf("Labels(%s) = %v", sif, err)
	}
	if len(got) == 0 {
		t.Fatalf("Labels(%s) answered no labels; every SIF apptainer builds has org.label-schema.*", sif)
	}
	if strings.HasPrefix(filepath.Base(sif), "ragstack-") {
		for _, k := range []string{"org.ragstack.role", "org.ragstack.version", "org.ragstack.commit", "org.ragstack.build"} {
			if got[k] == "" {
				t.Errorf("%s has no %s label (labels: %v)", sif, k, got)
			}
		}
	}
}

// ---------------------------------------------------------------- InstanceOwnsPort

// writeStat writes a /proc/<pid>/stat whose ppid is ppid.
func writeStat(t *testing.T, root string, pid, ppid int, comm string) {
	t.Helper()
	dir := filepath.Join(root, strconv.Itoa(pid))
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	line := strconv.Itoa(pid) + " (" + comm + ") S " + strconv.Itoa(ppid) + " 1 1 0 -1 4194560 0 0 0 0 0 0 0 0 20 0 1 0 100\n"
	if err := os.WriteFile(filepath.Join(dir, "stat"), []byte(line), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestRealProcInstanceOwnsPort(t *testing.T) {
	ctx := context.Background()
	procRoot := t.TempDir()
	// appinit 500 → starter 501 → uvicorn 502 on :24040; a stranger 900 on
	// :24041; another account's socket on :24042 (pid 0).
	writeStat(t, procRoot, 500, 1, "appinit")
	writeStat(t, procRoot, 501, 500, "sh")
	writeStat(t, procRoot, 502, 501, "python")
	writeStat(t, procRoot, 900, 1, "python")
	p := &RealProc{
		ProcRoot: func() string { return procRoot },
		Listeners: func() ([]hostfacts.Listener, error) {
			return []hostfacts.Listener{
				{Port: 24040, Addr: "127.0.0.1", Pid: 502, UID: 1000},
				{Port: 24041, Addr: "127.0.0.1", Pid: 900, UID: 1000},
				{Port: 24042, Addr: "127.0.0.1", Pid: 0, UID: -1},
			}, nil
		},
	}
	for _, c := range []struct {
		pid, port int
		want      bool
	}{
		{500, 24040, true},  // grandchild of the instance
		{502, 24040, true},  // the holder itself
		{500, 24041, false}, // held by a stranger
		{500, 24042, false}, // unattributable
		{500, 24099, false}, // free
	} {
		got, err := p.InstanceOwnsPort(ctx, c.pid, c.port)
		if err != nil || got != c.want {
			t.Errorf("InstanceOwnsPort(%d, %d) = %v, %v; want %v", c.pid, c.port, got, err, c.want)
		}
	}
	for _, bad := range [][2]int{{0, 24040}, {1, 24040}, {500, 0}, {500, 70000}} {
		if _, err := p.InstanceOwnsPort(ctx, bad[0], bad[1]); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("InstanceOwnsPort(%d, %d) = %v, want a refusal", bad[0], bad[1], err)
		}
	}
}

func TestFakeProcInstanceOwnsPort(t *testing.T) {
	ctx := context.Background()
	f := NewFake(FakeOptions{})
	p := f.FakeProc()
	p.SetInstancePortHolder(700, 702, 24040)
	p.SetParent(702, 701)
	p.SetParent(701, 700) // 702 → 701 → 700, overriding the direct link
	p.SetInstancePortHolder(800, 800, 24050)
	p.SetOwner(24060, 0, 0)
	for _, c := range []struct {
		pid, port int
		want      bool
	}{
		{700, 24040, true},
		{701, 24040, true},
		{999, 24040, false},
		{800, 24050, true},  // the starter holds it itself
		{700, 24060, false}, // unattributable
		{700, 24099, false}, // free
	} {
		got, err := p.InstanceOwnsPort(ctx, c.pid, c.port)
		if err != nil || got != c.want {
			t.Errorf("fake InstanceOwnsPort(%d, %d) = %v, %v; want %v", c.pid, c.port, got, err, c.want)
		}
	}
	if _, err := p.InstanceOwnsPort(ctx, 0, 24040); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("fake InstanceOwnsPort(0, …) = %v, want a refusal", err)
	}
	p.FreePort(24040)
	if got, _ := p.InstanceOwnsPort(ctx, 700, 24040); got {
		t.Error("a freed port is still owned")
	}
	if n := f.Count("proc.InstanceOwnsPort"); n != 8 {
		t.Errorf("proc.InstanceOwnsPort recorded %d calls, want 8", n)
	}
}

// End to end on the fake host: an API instance run with `--port` binds it
// through a child of the instance, InstanceOwnsPort says so, and a stop frees
// it. The spec — ExtraEnv and CleanEnv included — is kept for inspection and
// never put in the call log.
func TestFakeAPIInstanceBindsItsPortFromTheArgv(t *testing.T) {
	ctx := context.Background()
	f := NewFake(FakeOptions{})
	spec := jobs.InstanceSpec{
		Name: "api-dev", SIF: "/rag/data/ctl/images/server/ragstack-server-v1.6.6-b1.sif", CleanEnv: true,
		Args:     []string{"--host", "127.0.0.1", "--port", "24040"},
		ExtraEnv: map[string]string{"APPTAINERENV_SECRET": "s3cret"},
	}
	if err := f.Instances().Run(ctx, spec); err != nil {
		t.Fatal(err)
	}
	spec.ExtraEnv["APPTAINERENV_SECRET"] = "mutated"
	list, _ := f.Instances().List(ctx, jobs.ListOptions{})
	if len(list) != 1 || list[0].Name != "api-dev" {
		t.Fatalf("List = %v", list)
	}
	if up, _ := f.Proc().Listening(ctx, 24040); !up {
		t.Fatal("the API instance did not bind the port its argv names")
	}
	owner, _, _ := f.Proc().Owner(ctx, 24040)
	if owner == list[0].PID {
		t.Error("the fake gives the instance and its listener the same pid; the host does not")
	}
	if ok, err := f.Proc().InstanceOwnsPort(ctx, list[0].PID, 24040); err != nil || !ok {
		t.Errorf("InstanceOwnsPort = %v, %v; want true", ok, err)
	}
	specs := f.FakeInstances().Specs
	if len(specs) != 1 || !specs[0].CleanEnv || specs[0].ExtraEnv["APPTAINERENV_SECRET"] != "s3cret" {
		t.Errorf("Specs = %+v; want the spec as given (and a copy)", specs)
	}
	for _, c := range f.CallKeys() {
		if strings.Contains(c, "s3cret") {
			t.Fatalf("an ExtraEnv value reached the call log: %s", c)
		}
	}
	if err := f.Instances().Stop(ctx, "api-dev", jobs.StopOptions{}); err != nil {
		t.Fatal(err)
	}
	if up, _ := f.Proc().Listening(ctx, 24040); up {
		t.Error("stopping the API instance left its port bound")
	}
	if ok, _ := f.Proc().InstanceOwnsPort(ctx, list[0].PID, 24040); ok {
		t.Error("a stopped instance still owns the port")
	}
}

// ---------------------------------------------------------------- Checkout

const keyGitCheckout = `
case "$*" in
  *"status --porcelain"*) key=status ;;
  *"checkout --detach"*)  key=checkout ;;
  *)                      key=other ;;
esac
`

func newGitCheckout(t *testing.T) (*RealGit, *stub, string) {
	t.Helper()
	s := newStub(t, keyGitCheckout)
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "dev"), 0o755); err != nil {
		t.Fatal(err)
	}
	return &RealGit{run: &runner{}, Bin: s.Path, Roots: []string{root}}, s, root
}

func TestGitCheckoutBuildsTheExpectedArgv(t *testing.T) {
	d, s, root := newGitCheckout(t)
	wt := filepath.Join(root, "dev")
	if err := d.Checkout(context.Background(), wt, testSHA); err != nil {
		t.Fatal(err)
	}
	want := []string{
		"-c safe.directory=* -c core.hooksPath=/dev/null -C " + wt + " status --porcelain",
		"-c safe.directory=* -c core.hooksPath=/dev/null -C " + wt + " checkout --detach " + testSHA,
	}
	got := s.argv()
	if len(got) != len(want) {
		t.Fatalf("Checkout ran %v", got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("call %d = %q, want %q", i, got[i], want[i])
		}
	}
}

func TestGitCheckoutRefusals(t *testing.T) {
	ctx := context.Background()
	d, s, root := newGitCheckout(t)
	wt := filepath.Join(root, "dev")
	file := filepath.Join(root, "afile")
	if err := os.WriteFile(file, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(root, "link")
	if err := os.Symlink(t.TempDir(), link); err != nil {
		t.Fatal(err)
	}
	for _, c := range []struct{ name, wt, sha string }{
		{"a ref", wt, "main"},
		{"a short sha", wt, testSHA[:12]},
		{"an upper-case sha", wt, strings.ToUpper(testSHA)},
		{"an option", wt, "--orphan"},
		{"a relative worktree", "dev", testSHA},
		{"an unclean worktree", root + "/x/../dev", testSHA},
		{"a worktree outside the roots", t.TempDir(), testSHA},
		{"a missing worktree", filepath.Join(root, "absent"), testSHA},
		{"a file", file, testSHA},
		{"a symlinked worktree", link, testSHA},
	} {
		if err := d.Checkout(ctx, c.wt, c.sha); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("Checkout with %s = %v, want a refusal", c.name, err)
		}
	}
	s.ranNothing("a refused checkout")

	// A dirty tree: status ran, checkout did not.
	s.respond("status", " M python/ragstack/api/main.py\n?? notes.txt\n", "", 0)
	err := d.Checkout(ctx, wt, testSHA)
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "local changes") || !strings.Contains(err.Error(), "2 path") {
		t.Fatalf("Checkout of a dirty tree = %v, want a refusal counting 2 paths", err)
	}
	for _, line := range s.argv() {
		if strings.Contains(line, "checkout --detach") {
			t.Fatalf("a dirty tree was checked out anyway: %v", s.argv())
		}
	}
}

func TestFakeGitCheckout(t *testing.T) {
	ctx := context.Background()
	const wt = "/rag/repos/tenants/dev"
	other := strings.Repeat("b", 40)
	f := NewFake(FakeOptions{Worktrees: map[string]string{wt: testSHA}})
	g := f.FakeGit()
	for _, c := range []struct{ name, wt, sha string }{
		{"a ref", wt, "main"},
		{"a relative worktree", "dev", other},
		{"an unclean worktree", wt + "/", other},
		{"an unknown worktree", "/rag/repos/tenants/nope", other},
	} {
		if err := g.Checkout(ctx, c.wt, c.sha); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("fake Checkout with %s = %v, want a refusal", c.name, err)
		}
	}
	g.SetDirty(wt, true)
	if err := g.Checkout(ctx, wt, other); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("fake Checkout of a dirty tree = %v, want a refusal", err)
	}
	if head, _ := g.HeadSHA(ctx, wt); head != testSHA {
		t.Errorf("a refused checkout moved HEAD to %s", head)
	}
	g.SetDirty(wt, false)
	if err := g.Checkout(ctx, wt, other); err != nil {
		t.Fatal(err)
	}
	if head, _ := g.HeadSHA(ctx, wt); head != other {
		t.Errorf("HeadSHA after Checkout = %s, want %s", head, other)
	}
	if len(g.CheckedOut) != 1 || g.CheckedOut[0] != wt+"@"+other {
		t.Errorf("CheckedOut = %v", g.CheckedOut)
	}
	if n := f.Count("git.Checkout"); n != 6 {
		t.Errorf("git.Checkout recorded %d calls, want 6", n)
	}
}

// Against real git: a clean worktree moves, HEAD follows, and a modified or
// an untracked file stops the next one.
func TestGitCheckoutAgainstRealGit(t *testing.T) {
	gitBin, ok := execLooksAvailable("git")
	if !ok {
		t.Skip("git is not on PATH")
	}
	ctx := context.Background()
	root := t.TempDir()
	source := filepath.Join(root, "source")
	run := func(dir string, args ...string) string {
		t.Helper()
		cmd := exec.Command(gitBin, args...)
		cmd.Dir = dir
		cmd.Env = append(os.Environ(), "GIT_CONFIG_GLOBAL=/dev/null", "GIT_CONFIG_SYSTEM=/dev/null")
		out, err := cmd.CombinedOutput()
		if err != nil {
			t.Fatalf("git %s: %v\n%s", strings.Join(args, " "), err, out)
		}
		return strings.TrimSpace(string(out))
	}
	if err := os.MkdirAll(source, 0o755); err != nil {
		t.Fatal(err)
	}
	run(source, "init", "-q", "-b", "main", ".")
	commit := func(body string) string {
		if err := os.WriteFile(filepath.Join(source, "README.md"), []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
		run(source, "add", "README.md")
		run(source, "-c", "user.name=ctl", "-c", "user.email=ctl@example.invalid", "commit", "-q", "-m", body)
		return run(source, "rev-parse", "HEAD")
	}
	first := commit("one\n")
	second := commit("two\n")
	mirror := filepath.Join(root, "ragstack.git")
	run(root, "clone", "--quiet", "--mirror", source, mirror)

	d := &RealGit{run: &runner{}, Bin: gitBin, Roots: []string{root}}
	wt := filepath.Join(root, "tenants", "dev")
	if err := d.AddWorktree(ctx, mirror, first, wt); err != nil {
		t.Fatal(err)
	}
	if err := d.Checkout(ctx, wt, second); err != nil {
		t.Fatalf("Checkout = %v", err)
	}
	if head, err := d.HeadSHA(ctx, wt); err != nil || head != second {
		t.Fatalf("HeadSHA after Checkout = %s, %v; want %s", head, err, second)
	}
	if b, _ := os.ReadFile(filepath.Join(wt, "README.md")); string(b) != "two\n" {
		t.Errorf("README.md = %q after the checkout", b)
	}
	// A modified tracked file.
	if err := os.WriteFile(filepath.Join(wt, "README.md"), []byte("hot fix\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := d.Checkout(ctx, wt, first); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("Checkout over a modified file = %v, want a refusal", err)
	}
	run(wt, "checkout", "--", "README.md")
	// An untracked file.
	if err := os.WriteFile(filepath.Join(wt, "stray.txt"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := d.Checkout(ctx, wt, first); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("Checkout over an untracked file = %v, want a refusal", err)
	}
	if err := os.Remove(filepath.Join(wt, "stray.txt")); err != nil {
		t.Fatal(err)
	}
	if err := d.Checkout(ctx, wt, first); err != nil {
		t.Fatalf("Checkout back = %v", err)
	}
	if head, _ := d.HeadSHA(ctx, wt); head != first {
		t.Errorf("HeadSHA = %s, want %s", head, first)
	}
}
