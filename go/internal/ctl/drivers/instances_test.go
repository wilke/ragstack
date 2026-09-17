package drivers

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
)

// keyVerb is the instance driver's response key: the argv is always
// `instance <verb> …`, so the verb is the second word.
const keyVerb = "key=\"$2\"\n"

// newInstances is a RealInstances pointed at a stub `apptainer`, with a
// temporary directory as its only approved root and a file standing in for a
// SIF (checkSIF stats the image, so a fixture path would refuse before the
// argv was ever built).
func newInstances(t *testing.T) (*RealInstances, *stub, string, string) {
	t.Helper()
	s := newStub(t, keyVerb)
	root := t.TempDir()
	sif := filepath.Join(root, "qdrant.sif")
	if err := os.WriteFile(sif, []byte("not really a SIF"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(root, "storage"), 0o755); err != nil {
		t.Fatal(err)
	}
	return &RealInstances{run: &runner{}, Bin: s.Path, Roots: []string{root},
		Env:        []string{"APPTAINER_CONFIGDIR=" + filepath.Join(root, "apptainer", "config")},
		AccountEnv: []string{"APPTAINER_CACHEDIR=" + filepath.Join(root, "apptainer", "cache")},
	}, s, root, sif
}

// Every apptainer call carries the ctl's APPTAINER_CONFIGDIR: the instance
// registry lives there, and a List or Stop without it answers about a
// different, empty registry (which is how sandboxes were once left running).
func TestInstancesEveryCallCarriesTheCtlApptainerConfigDir(t *testing.T) {
	d, s, root, sif := newInstances(t)
	s.respond("list", `{"instances":[]}`, "", 0)
	want := "APPTAINER_CONFIGDIR=" + filepath.Join(root, "apptainer", "config")
	_, _ = d.List(context.Background(), jobs.ListOptions{})
	_ = d.Stop(context.Background(), "qdrant-dev", jobs.StopOptions{})
	_ = d.Run(context.Background(), jobs.InstanceSpec{Name: "qdrant-dev", SIF: sif})
	if err := d.SeedConfigDir(context.Background(), sif, "/usr/share/elasticsearch/config", filepath.Join(root, "storage")); err != nil {
		t.Fatalf("SeedConfigDir: %v", err)
	}
	// The stub keys a call by its SECOND argv element: `instance <verb>` gives
	// the verb, `exec --bind …` gives "--bind".
	for _, key := range []string{"list", "stop", "run", "--bind"} {
		env := s.childEnv(key)
		if len(env) == 0 {
			t.Errorf("no %s call reached the stub", key)
			continue
		}
		found := false
		for _, e := range env {
			if e == want {
				found = true
			}
		}
		if !found {
			t.Errorf("the %s call lacks %s in its environment: %v", key, want, env)
		}
	}
}

// listJSON is a captured `apptainer instance list --json` document.
//
// It is the real output of apptainer 1.5.3 on coconut (two of the running
// tenants' stores, trimmed), kept verbatim so that the FIELD NAMES this driver
// decodes — `instance`, `pid`, `img`, not `name` and `image` — are pinned by
// something apptainer actually printed. Decoding to empty names would make
// every `running` check answer "no" and every start a second copy of a store
// that is already up.
const listJSON = `{
	"instances": [
		{
			"instance": "qdrant-dev",
			"pid": 189637,
			"img": "/rag/apptainer/images/qdrant.sif",
			"ip": "",
			"logErrPath": "/home/wilke/.apptainer/instances/logs/coconut/wilke/qdrant-dev.err",
			"logOutPath": "/home/wilke/.apptainer/instances/logs/coconut/wilke/qdrant-dev.out"
		},
		{
			"instance": "elasticsearch-hackathon",
			"pid": 580508,
			"img": "/rag/apptainer/images/elasticsearch.sif",
			"ip": "",
			"logErrPath": "/home/wilke/.apptainer/instances/logs/coconut/wilke/elasticsearch-hackathon.err",
			"logOutPath": "/home/wilke/.apptainer/instances/logs/coconut/wilke/elasticsearch-hackathon.out"
		}
	]
}
`

func TestInstancesListParsesWhatApptainerPrints(t *testing.T) {
	d, s, _, _ := newInstances(t)
	s.respond("list", listJSON, "", 0)
	got, err := d.List(context.Background(), jobs.ListOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if len(s.argv()) != 1 || s.argv()[0] != "instance list --json" {
		t.Fatalf("List ran %v", s.argv())
	}
	// Sorted by name, which is not the order the document holds.
	want := []jobs.Instance{
		{Name: "elasticsearch-hackathon", PID: 580508, Image: "/rag/apptainer/images/elasticsearch.sif"},
		{Name: "qdrant-dev", PID: 189637, Image: "/rag/apptainer/images/qdrant.sif"},
	}
	if len(got) != len(want) {
		t.Fatalf("List = %+v, want %+v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("instance %d = %+v, want %+v", i, got[i], want[i])
		}
	}
}

func TestInstancesListOfAnEmptyHostIsNotAnError(t *testing.T) {
	ctx := context.Background()
	for _, out := range []string{`{"instances":[]}`, `{}`, "", "\n"} {
		d, s, _, _ := newInstances(t)
		s.respond("list", out, "", 0)
		got, err := d.List(ctx, jobs.ListOptions{})
		if err != nil {
			t.Errorf("List of %q = %v, want no error", out, err)
		}
		if len(got) != 0 {
			t.Errorf("List of %q = %v, want nothing running", out, got)
		}
	}
}

func TestInstancesListRefusesOutputItCannotParse(t *testing.T) {
	d, s, _, _ := newInstances(t)
	s.respond("list", "not json at all", "", 0)
	if _, err := d.List(context.Background(), jobs.ListOptions{}); !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("List of unparseable output = %v, want a refusal", err)
	}
}

func TestInstancesRunBuildsTheArgvApptainerExpects(t *testing.T) {
	d, s, root, sif := newInstances(t)
	s.respond("list", `{"instances":[]}`, "", 0)
	err := d.Run(context.Background(), jobs.InstanceSpec{
		Name:  "qdrant-dev",
		SIF:   sif,
		Binds: []string{filepath.Join(root, "storage") + ":/qdrant/storage"},
		// Out of order on purpose: the map has none, so the driver sorts.
		Env: map[string]string{
			"QDRANT__SERVICE__HTTP_PORT": "24041",
			"QDRANT__SERVICE__GRPC_PORT": "24042",
		},
		Args: []string{"/bin/sh", "-c", "cd /qdrant && exec ./entrypoint.sh"},
	})
	if err != nil {
		t.Fatal(err)
	}
	want := []string{
		// The idempotency check comes first: the driver refuses a name that
		// is already up rather than letting apptainer fail halfway.
		"instance list --json",
		"instance run --no-home " +
			"--bind " + filepath.Join(root, "storage") + ":/qdrant/storage " +
			"--env QDRANT__SERVICE__GRPC_PORT=24042 --env QDRANT__SERVICE__HTTP_PORT=24041 " +
			sif + " qdrant-dev /bin/sh -c cd /qdrant && exec ./entrypoint.sh",
	}
	got := s.argv()
	if len(got) != len(want) {
		t.Fatalf("Run made %d calls: %v", len(got), got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("call %d =\n%q\nwant\n%q", i, got[i], want[i])
		}
	}
}

func TestInstancesSeedConfigDirCopiesTheImageConfigIntoTheBind(t *testing.T) {
	d, s, root, sif := newInstances(t)
	host := filepath.Join(root, "elasticsearch", "config")
	if err := os.MkdirAll(host, 0o750); err != nil {
		t.Fatal(err)
	}
	if err := d.SeedConfigDir(context.Background(), sif, "/usr/share/elasticsearch/config", host); err != nil {
		t.Fatal(err)
	}
	want := "exec --bind " + host + ":/__seed " + sif + " cp -R /usr/share/elasticsearch/config/. /__seed/"
	if got := s.argv(); len(got) != 1 || got[0] != want {
		t.Fatalf("SeedConfigDir argv = %v, want [%q]", got, want)
	}
	// The host side is a bind like any other: outside the roots it is refused
	// before apptainer runs, and a relative container path is refused too.
	if err := d.SeedConfigDir(context.Background(), sif, "/usr/share/elasticsearch/config", "/etc/es"); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("a host dir outside the roots = %v, want a refusal", err)
	}
	if err := d.SeedConfigDir(context.Background(), sif, "config", host); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("a relative container dir = %v, want a refusal", err)
	}
}

func TestInstancesRunPutsExtraEnvInTheChildEnvironmentAndNeverOnTheArgv(t *testing.T) {
	d, s, root, sif := newInstances(t)
	s.respond("list", `{"instances":[]}`, "", 0)
	const password = "hunter2-not-on-a-cmdline"
	err := d.Run(context.Background(), jobs.InstanceSpec{
		Name: "postgres-dev",
		SIF:  sif,
		ExtraEnv: map[string]string{
			"APPTAINERENV_POSTGRES_PASSWORD": password,
			"APPTAINER_CACHEDIR":             "/rag/state/apptainer/cache",
			"APPTAINER_CONFIGDIR":            "/rag/state/apptainer/config",
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	// /proc/<pid>/cmdline is 0444 on a 1869-member host. The password may not
	// be in the argv of ANY call, including the list.
	for _, line := range s.argv() {
		if strings.Contains(line, password) {
			t.Fatalf("the postgres password reached an argv: %q", line)
		}
	}
	env := s.childEnv("run")
	for _, want := range []string{
		"APPTAINERENV_POSTGRES_PASSWORD=" + password,
		// The DRIVER's apptainer directories, not the spec's: os/exec keeps
		// the last value of a repeated key and the driver appends its own
		// last, so a spec cannot redirect apptainer's instance registry.
		"APPTAINER_CONFIGDIR=" + filepath.Join(root, "apptainer", "config"),
	} {
		if !containsLine(env, want) {
			t.Errorf("the child environment is missing %q (it has %v)", want, env)
		}
	}
	if last := lastLineWithPrefix(env, "APPTAINER_CONFIGDIR="); last != "APPTAINER_CONFIGDIR="+filepath.Join(root, "apptainer", "config") {
		t.Errorf("the last APPTAINER_CONFIGDIR the child sees is %q, want the driver's", last)
	}
}

func TestInstancesRunRefusesANameThatIsAlreadyUp(t *testing.T) {
	d, s, _, sif := newInstances(t)
	s.respond("list", listJSON, "", 0)
	err := d.Run(context.Background(), jobs.InstanceSpec{Name: "qdrant-dev", SIF: sif})
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("Run against a running instance = %v, want a refusal", err)
	}
	if got := s.argv(); len(got) != 1 {
		t.Fatalf("Run started something anyway: %v", got)
	}
}

func TestInstancesRefuseEveryNameOutsideTheAllowlist(t *testing.T) {
	ctx := context.Background()
	// Each is a name that could reach a driver from a registry row, a manifest
	// or an argument. None is a store this control plane owns, and `apptainer
	// instance stop nginx-gateway` would take down the host's proxy.
	for _, name := range []string{
		"nginx-gateway", "crossencoder", "qdrant", "qdrant-", "Qdrant-dev",
		"redis-dev", "qdrant-dev extra", "qdrant-dev\n", "../qdrant-dev", "", "*",
	} {
		d, s, _, sif := newInstances(t)
		if err := d.Run(ctx, jobs.InstanceSpec{Name: name, SIF: sif}); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("Run(%q) = %v, want a refusal", name, err)
		}
		if err := d.Stop(ctx, name, jobs.StopOptions{}); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("Stop(%q) = %v, want a refusal", name, err)
		}
		s.ranNothing("an instance name outside the allowlist")
	}
}

func TestInstancesRunRefusesAnImageItCannotUse(t *testing.T) {
	ctx := context.Background()
	d, s, root, _ := newInstances(t)
	for _, sif := range []string{
		"", "images/qdrant.sif", filepath.Join(root, "absent.sif"),
		filepath.Join(root, "storage"), // a directory
		filepath.Join(root, "..", "qdrant.sif"),
	} {
		err := d.Run(ctx, jobs.InstanceSpec{Name: "qdrant-dev", SIF: sif})
		if !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("Run with image %q = %v, want a refusal", sif, err)
		}
	}
	s.ranNothing("an unusable image")
}

func TestInstancesRunRefusesABindItCannotVouchFor(t *testing.T) {
	ctx := context.Background()
	d, s, root, sif := newInstances(t)
	outside := t.TempDir()
	for _, bind := range []string{
		"", "/qdrant/storage",
		"storage:/qdrant/storage",                               // relative host
		outside + ":/qdrant/storage",                            // outside every approved root
		filepath.Join(root, "storage") + ":qdrant/storage",      // relative container
		filepath.Join(root, "storage") + ":/qdrant/../storage",  // unclean container
		filepath.Join(root, "storage") + ":/qdrant/storage:rwx", // not a bind option
		filepath.Join(root, "storage") + ":/a:ro:extra",         // too many fields
	} {
		err := d.Run(ctx, jobs.InstanceSpec{Name: "qdrant-dev", SIF: sif, Binds: []string{bind}})
		if !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("Run with bind %q = %v, want a refusal", bind, err)
		}
	}
	s.ranNothing("a bind outside the approved roots")
}

func TestInstancesRunRefusesToFollowASymlinkedBind(t *testing.T) {
	d, s, root, sif := newInstances(t)
	// A link inside the approved root pointing at one that is not: lexically
	// contained, and a mount of somebody else's directory into a store that
	// writes.
	outside := t.TempDir()
	link := filepath.Join(root, "storage-link")
	if err := os.Symlink(outside, link); err != nil {
		t.Fatal(err)
	}
	err := d.Run(context.Background(), jobs.InstanceSpec{
		Name: "qdrant-dev", SIF: sif, Binds: []string{link + ":/qdrant/storage"},
	})
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("Run with a symlinked bind = %v, want a refusal", err)
	}
	s.ranNothing("a symlinked bind")
}

func TestInstancesRunRefusesAnEnvironmentItCannotRender(t *testing.T) {
	ctx := context.Background()
	d, s, _, sif := newInstances(t)
	for _, env := range []map[string]string{
		{"NOT A NAME": "x"},
		{"WITH=EQUALS": "x"},
		{"": "x"},
		{"OK": "has\x00a nul"},
	} {
		if err := d.Run(ctx, jobs.InstanceSpec{Name: "qdrant-dev", SIF: sif, Env: env}); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("Run with env %v = %v, want a refusal", env, err)
		}
		if err := d.Run(ctx, jobs.InstanceSpec{Name: "qdrant-dev", SIF: sif, ExtraEnv: env}); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("Run with extra env %v = %v, want a refusal", env, err)
		}
	}
	s.ranNothing("an environment that is not KEY=VALUE")
}

func TestInstancesStopOfAnAbsentInstanceIsSuccess(t *testing.T) {
	ctx := context.Background()

	// What apptainer 1.5.3 actually says.
	d, s, _, _ := newInstances(t)
	s.respond("stop", "", "Error for command \"stop\": no instance found\n", 1)
	if err := d.Stop(ctx, "qdrant-dev", jobs.StopOptions{}); err != nil {
		t.Errorf("Stop of an absent instance = %v, want success", err)
	}

	// A build that words it some other way: the List fallback settles it.
	d, s, _, _ = newInstances(t)
	s.respond("stop", "", "FATAL: could not stop instance\n", 1)
	s.respond("list", `{"instances":[]}`, "", 0)
	if err := d.Stop(ctx, "qdrant-dev", jobs.StopOptions{}); err != nil {
		t.Errorf("Stop of an instance List says is gone = %v, want success", err)
	}

	// And a stop that really failed, with the instance still there, is an
	// error — a `fleet stop --all` that reported success over a store that is
	// still writing is the failure this half prevents.
	d, s, _, _ = newInstances(t)
	s.respond("stop", "", "FATAL: could not stop instance\n", 1)
	s.respond("list", listJSON, "", 0)
	if err := d.Stop(ctx, "qdrant-dev", jobs.StopOptions{}); err == nil {
		t.Error("Stop of an instance that is still running = nil, want the failure")
	}
}

func TestInstancesStopBuildsTheArgvApptainerExpects(t *testing.T) {
	d, s, _, _ := newInstances(t)
	if err := d.Stop(context.Background(), "elasticsearch-dev", jobs.StopOptions{}); err != nil {
		t.Fatal(err)
	}
	if got := s.argv(); len(got) != 1 || got[0] != "instance stop elasticsearch-dev" {
		t.Fatalf("Stop ran %v", got)
	}
}

// TestInstancesRefusalsMatchTheFake keeps the two implementations of
// jobs.Instances answering the same question the same way: a test that passes
// against the fake and a host that refuses would be a conformance suite
// vouching for an op that cannot run.
func TestInstancesRefusalsMatchTheFake(t *testing.T) {
	ctx := context.Background()
	fake := NewFake(FakeOptions{}).FakeInstances()
	real, s, _, sif := newInstances(t)
	s.respond("list", `{"instances":[]}`, "", 0)

	// The name allowlist.
	for _, name := range []string{"nginx-gateway", "qdrant-", ""} {
		fakeErr := fake.Run(ctx, jobs.InstanceSpec{Name: name, SIF: sif})
		realErr := real.Run(ctx, jobs.InstanceSpec{Name: name, SIF: sif})
		if !errors.Is(fakeErr, jobs.ErrRefused) || !errors.Is(realErr, jobs.ErrRefused) {
			t.Errorf("Run(%q): fake = %v, real = %v; both must refuse", name, fakeErr, realErr)
		}
		if fakeErr := fake.Stop(ctx, name, jobs.StopOptions{}); !errors.Is(fakeErr, jobs.ErrRefused) {
			t.Errorf("fake Stop(%q) = %v, want a refusal", name, fakeErr)
		}
		if realErr := real.Stop(ctx, name, jobs.StopOptions{}); !errors.Is(realErr, jobs.ErrRefused) {
			t.Errorf("real Stop(%q) = %v, want a refusal", name, realErr)
		}
	}

	// An image that is not there.
	if err := fake.Run(ctx, jobs.InstanceSpec{Name: "qdrant-dev", SIF: ""}); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("fake Run with no image = %v, want a refusal", err)
	}
	if err := real.Run(ctx, jobs.InstanceSpec{Name: "qdrant-dev", SIF: ""}); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("real Run with no image = %v, want a refusal", err)
	}

	// A name that is already running.
	if err := fake.Run(ctx, jobs.InstanceSpec{Name: "qdrant-dev", SIF: sif}); err != nil {
		t.Fatal(err)
	}
	fakeErr := fake.Run(ctx, jobs.InstanceSpec{Name: "qdrant-dev", SIF: sif})
	s.respond("list", listJSON, "", 0)
	realErr := real.Run(ctx, jobs.InstanceSpec{Name: "qdrant-dev", SIF: sif})
	if !errors.Is(fakeErr, jobs.ErrRefused) || !errors.Is(realErr, jobs.ErrRefused) {
		t.Errorf("a second Run of a live name: fake = %v, real = %v; both must refuse", fakeErr, realErr)
	}

	// And a stop of something that is not running is success for both.
	if err := fake.Stop(ctx, "postgres-dev", jobs.StopOptions{}); err != nil {
		t.Errorf("fake Stop of an absent instance = %v, want success", err)
	}
	real2, s2, _, _ := newInstances(t)
	s2.respond("stop", "", "no instance found\n", 1)
	if err := real2.Stop(ctx, "postgres-dev", jobs.StopOptions{}); err != nil {
		t.Errorf("real Stop of an absent instance = %v, want success", err)
	}
}

func containsLine(lines []string, want string) bool {
	for _, l := range lines {
		if l == want {
			return true
		}
	}
	return false
}

// lastLineWithPrefix is the last environment line starting with prefix: the
// value os/exec hands the child for a repeated key.
func lastLineWithPrefix(env []string, prefix string) string {
	last := ""
	for _, e := range env {
		if strings.HasPrefix(e, prefix) {
			last = e
		}
	}
	return last
}

// A call in jobs.NamespaceAccountDefault carries NO APPTAINER_CONFIGDIR at
// all, so apptainer falls back to $HOME/.apptainer.
//
// This is the regression for the ten-minute outage on the hackathon tenant.
// The ctl namespaces every apptainer call under its own state dir so the
// daemon's instances are findable from any session (PR-D2); the handover's
// RELEASE half acts on instances the OWNER started by hand, which are in the
// owner's default registry. Forcing the ctl's CONFIGDIR on that call made
// `apptainer instance stop postgres-hackathon` look in a registry that held
// nothing, and Stop's "an instance that is not running is success" rule
// reported a stop over a postgres that went on serving 24085.
//
// The assertion is an ABSENCE, which is the whole point: an empty string would
// be a config dir named "", not the default.
func TestInstancesAccountDefaultNamespaceCarriesNoConfigDir(t *testing.T) {
	d, s, root, sif := newInstances(t)
	s.respond("list", `{"instances":[]}`, "", 0)
	ctx := context.Background()
	acct := jobs.NamespaceAccountDefault
	_, _ = d.List(ctx, jobs.ListOptions{Namespace: acct})
	_ = d.Stop(ctx, "qdrant-dev", jobs.StopOptions{Namespace: acct})
	_ = d.Run(ctx, jobs.InstanceSpec{Name: "qdrant-dev", SIF: sif, Namespace: acct})
	for _, key := range []string{"list", "stop", "run"} {
		env := s.childEnv(key)
		if len(env) == 0 {
			t.Errorf("no %s call reached the stub", key)
			continue
		}
		for _, e := range env {
			if strings.HasPrefix(e, "APPTAINER_CONFIGDIR=") {
				t.Errorf("the %s call in the account namespace carries %q: it would look in the ctl's registry, "+
					"not the account's", key, e)
			}
		}
		// The cache is still the ctl's: only the REGISTRY moves.
		want := "APPTAINER_CACHEDIR=" + filepath.Join(root, "apptainer", "cache")
		found := false
		for _, e := range env {
			if e == want {
				found = true
			}
		}
		if !found {
			t.Errorf("the %s call in the account namespace is missing %q: %v", key, want, env)
		}
	}
}

// And the two namespaces really are two registries as far as the fake is
// concerned: an instance started in one is absent from the other, and a stop
// in the wrong one is the no-op that reports success.
func TestFakeInstancesKeepsTheTwoRegistriesApart(t *testing.T) {
	ctx := context.Background()
	f := NewFake(FakeOptions{
		InstancePorts:           map[string]int{"postgres-hackathon": 24085},
		AccountRunningInstances: []string{"postgres-hackathon"},
	})
	in := f.Instances()

	ctl, err := in.List(ctx, jobs.ListOptions{Namespace: jobs.NamespaceCtl})
	if err != nil {
		t.Fatal(err)
	}
	if len(ctl) != 0 {
		t.Errorf("the ctl registry holds %v; the tenant was started by hand", ctl)
	}
	acct, err := in.List(ctx, jobs.ListOptions{Namespace: jobs.NamespaceAccountDefault})
	if err != nil {
		t.Fatal(err)
	}
	if len(acct) != 1 || acct[0].Name != "postgres-hackathon" {
		t.Fatalf("the account registry holds %v, want postgres-hackathon", acct)
	}

	// The coconut stop, exactly: it succeeds, and it changes nothing.
	if err := in.Stop(ctx, "postgres-hackathon", jobs.StopOptions{Namespace: jobs.NamespaceCtl}); err != nil {
		t.Fatalf("a stop in the ctl registry = %v; apptainer would say the same", err)
	}
	if held, _ := f.Proc().Listening(ctx, 24085); !held {
		t.Error("the stop in the WRONG registry freed the port: the fake is not modelling the outage")
	}
	if got := f.FakeInstances().AccountNames(); len(got) != 1 {
		t.Errorf("the account registry = %v after a stop in the other one", got)
	}

	// And the right one takes it down.
	if err := in.Stop(ctx, "postgres-hackathon", jobs.StopOptions{Namespace: jobs.NamespaceAccountDefault}); err != nil {
		t.Fatal(err)
	}
	if held, _ := f.Proc().Listening(ctx, 24085); held {
		t.Error("24085 is still held after the instance was stopped in its own registry")
	}
}

// The process on an instance's port is its CHILD, and Descends is what ties
// the two together. An identity check written as `owner == instance.PID`
// answers "no" about every instance on coconut.
func TestFakeInstancesPortIsHeldByAChildOfTheInstance(t *testing.T) {
	ctx := context.Background()
	f := NewFake(FakeOptions{
		InstancePorts:           map[string]int{"postgres-hackathon": 24085},
		AccountRunningInstances: []string{"postgres-hackathon"},
	})
	list, err := f.Instances().List(ctx, jobs.ListOptions{Namespace: jobs.NamespaceAccountDefault})
	if err != nil {
		t.Fatal(err)
	}
	owner, _, err := f.Proc().Owner(ctx, 24085)
	if err != nil {
		t.Fatal(err)
	}
	if owner == list[0].PID {
		t.Fatal("the fake gives the instance and its listener the same pid; the host does not")
	}
	ok, err := f.Proc().Descends(ctx, owner, list[0].PID)
	if err != nil || !ok {
		t.Fatalf("Descends(%d, %d) = %v, %v; the listener is the instance's child", owner, list[0].PID, ok, err)
	}
	// And a stranger does not descend from it.
	if ok, _ := f.Proc().Descends(ctx, 999999, list[0].PID); ok {
		t.Error("an unrelated pid was reported as the instance's")
	}
}
