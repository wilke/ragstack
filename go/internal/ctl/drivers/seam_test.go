package drivers

import (
	"context"
	"errors"
	"strconv"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
)

// The PR-D2 seam's fakes: apptainer instances, the detached spawn, and the
// crontab. Each test asserts the behaviour the instance supervisor DEPENDS on,
// not the fake's internals — a fake that models a host loosely is a suite that
// passes and a supervisor that does not work.

const (
	seamSIF    = "/rag/apptainer/images/qdrant.sif"
	seamESSIF  = "/rag/apptainer/images/elasticsearch.sif"
	seamPidDir = "/rag/data/tenants/dev"
)

func seamFake(t *testing.T) *Fake {
	t.Helper()
	return NewFake(FakeOptions{
		Roots:         []string{"/rag"},
		InstancePorts: map[string]int{"qdrant-dev": 24081, "elasticsearch-dev": 24083},
	})
}

func TestFakeInstancesRunListAndStop(t *testing.T) {
	ctx := context.Background()
	f := seamFake(t)
	in := f.Instances()

	// An empty host lists nothing, and that is not an error.
	got, err := in.List(ctx, jobs.ListOptions{})
	if err != nil || len(got) != 0 {
		t.Fatalf("List on an empty host = %v, %v; want no instances and no error", got, err)
	}

	// Run the two stores out of alphabetical order, to prove List sorts.
	if err := in.Run(ctx, jobs.InstanceSpec{Name: "qdrant-dev", SIF: seamSIF}); err != nil {
		t.Fatalf("Run qdrant-dev: %v", err)
	}
	if err := in.Run(ctx, jobs.InstanceSpec{Name: "elasticsearch-dev", SIF: seamESSIF}); err != nil {
		t.Fatalf("Run elasticsearch-dev: %v", err)
	}
	got, err = in.List(ctx, jobs.ListOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 || got[0].Name != "elasticsearch-dev" || got[1].Name != "qdrant-dev" {
		t.Fatalf("List = %v, want the two instances sorted by name", got)
	}
	if got[1].Image != seamSIF || got[1].PID == 0 {
		t.Errorf("List's qdrant row = %+v, want the image it was started from and a pid", got[1])
	}

	// Running stores own their ports, so a readiness probe answers a fact.
	if listening, _ := f.Proc().Listening(ctx, 24081); !listening {
		t.Error("running qdrant-dev did not bind its port")
	}

	// apptainer refuses a name that is taken, and so does this: a start that
	// silently succeeded would report a restarted tenant while the old
	// process kept serving.
	if err := in.Run(ctx, jobs.InstanceSpec{Name: "qdrant-dev", SIF: seamSIF}); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("a duplicate instance name = %v, want a refusal", err)
	}

	if err := in.Stop(ctx, "qdrant-dev", jobs.StopOptions{}); err != nil {
		t.Fatalf("Stop: %v", err)
	}
	if listening, _ := f.Proc().Listening(ctx, 24081); listening {
		t.Error("stopping qdrant-dev left its port bound")
	}
	if names := f.FakeInstances().Names(); strings.Join(names, ",") != "elasticsearch-dev" {
		t.Errorf("after the stop, Names() = %v", names)
	}

	// Stopping what is not running is SUCCESS: every caller of Stop is a step
	// that gets re-run — by a rollback, by a resumed job, by `fleet stop --all`
	// over a fleet half of which is already down.
	if err := in.Stop(ctx, "qdrant-dev", jobs.StopOptions{}); err != nil {
		t.Errorf("stopping an absent instance = %v, want nil", err)
	}
}

func TestFakeInstancesRefuseNamesThisControlPlaneDoesNotManage(t *testing.T) {
	ctx := context.Background()
	f := seamFake(t)
	for _, name := range []string{
		"nginx",           // the gateway, which this account also runs
		"ssh-agent",       // anything else on a shared host
		"qdrant",          // a kind with no tenant
		"redis-dev",       // a kind the ctl does not supervise
		"qdrant-Dev",      // the tenant grammar is lower-case
		"qdrant-dev/../x", // a path, not a name
		"",
	} {
		if err := f.Instances().Run(ctx, jobs.InstanceSpec{Name: name, SIF: seamSIF}); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("Run(%q) = %v, want a refusal", name, err)
		}
		if err := f.Instances().Stop(ctx, name, jobs.StopOptions{}); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("Stop(%q) = %v, want a refusal — a stop is the call that takes something down", name, err)
		}
	}
	// A well-formed name with no image is a refusal too: apptainer has
	// nothing to run.
	if err := f.Instances().Run(ctx, jobs.InstanceSpec{Name: "postgres-dev"}); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("Run with no sif = %v, want a refusal", err)
	}
}

// BindInstancePort is how a tenant CREATED after the driver set was built says
// which port its stores own — the same gap FakeSystemd.BindUnitPort fills for
// units, and the one that made a sandbox's readiness gate wait out its whole
// timeout against a fixture that was never going to answer.
func TestFakeInstancesBindInstancePortCoversATenantCreatedLater(t *testing.T) {
	ctx := context.Background()
	f := NewFake(FakeOptions{Roots: []string{"/rag"}})
	f.FakeInstances().BindInstancePort("qdrant-sandbox", 24581)
	if err := f.Instances().Run(ctx, jobs.InstanceSpec{Name: "qdrant-sandbox", SIF: seamSIF}); err != nil {
		t.Fatal(err)
	}
	if listening, _ := f.Proc().Listening(ctx, 24581); !listening {
		t.Fatal("the bound port did not become a LISTEN socket")
	}
}

// The failure table reaches the new drivers by "<driver>.<method>" and by
// "<driver>.<method>:<first arg>", exactly as it does the old ones.
func TestFakeInstancesFailureTable(t *testing.T) {
	ctx := context.Background()
	f := seamFake(t)
	boom := errors.New("apptainer: image not found")
	f.Fail("instances.Run:elasticsearch-dev", boom)
	if err := f.Instances().Run(ctx, jobs.InstanceSpec{Name: "qdrant-dev", SIF: seamSIF}); err != nil {
		t.Fatalf("the untargeted instance failed too: %v", err)
	}
	if err := f.Instances().Run(ctx, jobs.InstanceSpec{Name: "elasticsearch-dev", SIF: seamESSIF}); !errors.Is(err, boom) {
		t.Errorf("Run = %v, want the seeded failure", err)
	}
}

// A secret reaches a container through ExtraEnv, and the call log is printed
// by tests and compared in CI. This is the assertion that keeps the two apart.
func TestFakeInstancesNeverLogTheChildEnvironment(t *testing.T) {
	ctx := context.Background()
	f := seamFake(t)
	err := f.Instances().Run(ctx, jobs.InstanceSpec{
		Name: "postgres-dev", SIF: "/rag/apptainer/images/postgres.sif",
		Env:      map[string]string{"POSTGRES_USER": "dev"},
		ExtraEnv: map[string]string{"APPTAINERENV_POSTGRES_PASSWORD": "s3cret-do-not-log"},
	})
	if err != nil {
		t.Fatal(err)
	}
	for _, line := range f.CallKeys() {
		if strings.Contains(line, "s3cret-do-not-log") {
			t.Fatalf("the child environment reached the call log: %s", line)
		}
	}
}

func TestFakeProcSpawnWritesThePidfileAndBindsThePort(t *testing.T) {
	ctx := context.Background()
	f := NewFake(FakeOptions{Roots: []string{"/rag"}})
	spec := jobs.SpawnSpec{
		Program: "/rag/envs/ragstack/bin/python",
		Args:    []string{"-m", "uvicorn", "ragstack.api.main:app", "--host", "127.0.0.1", "--port", "24080"},
		Dir:     "/rag/repos/tenants/dev/python",
		Env:     []string{"PYTHONPATH=/rag/repos/tenants/dev/python", "RAGSTACK_API_KEY=s3cret-do-not-log"},
		LogPath: seamPidDir + "/logs/api-dev.log",
		PidFile: seamPidDir + "/api-dev.pid",
	}
	pid, err := f.Proc().Spawn(ctx, spec)
	if err != nil {
		t.Fatalf("Spawn: %v", err)
	}
	if pid == 0 {
		t.Fatal("Spawn returned pid 0")
	}
	// The pidfile is there the moment Spawn returns — the whole contract, and
	// the reason a crash between the fork and the write is not a running
	// process nothing can find.
	got := strings.TrimSpace(string(f.FakeFiles().Content(spec.PidFile)))
	if got != strconv.Itoa(pid) {
		t.Errorf("pidfile = %q, want the pid %d Spawn returned", got, pid)
	}
	// The port comes off the argv, so a test never states it twice.
	if listening, _ := f.Proc().Listening(ctx, 24080); !listening {
		t.Error("the spawned process did not bind the --port it was given")
	}
	// The environment is the child's and carries secrets; the call log is not
	// the place for it.
	for _, line := range f.CallKeys() {
		if strings.Contains(line, "s3cret-do-not-log") {
			t.Fatalf("the spawn environment reached the call log: %s", line)
		}
	}
	if len(f.FakeProc().Spawned) != 1 || f.FakeProc().Spawned[0].PidFile != spec.PidFile {
		t.Errorf("Spawned = %+v, want the one spec for inspection", f.FakeProc().Spawned)
	}
}

func TestFakeProcSpawnRefusesAnIncompleteSpec(t *testing.T) {
	ctx := context.Background()
	f := NewFake(FakeOptions{Roots: []string{"/rag"}})
	base := jobs.SpawnSpec{
		Program: "/rag/envs/ragstack/bin/python",
		LogPath: seamPidDir + "/logs/api-dev.log",
		PidFile: seamPidDir + "/api-dev.pid",
	}
	relative := base
	relative.Program = "python"
	noLog := base
	noLog.LogPath = ""
	noPid := base
	noPid.PidFile = ""
	for name, spec := range map[string]jobs.SpawnSpec{
		"a program off PATH": relative,
		"no log":             noLog,
		"no pidfile":         noPid,
	} {
		if _, err := f.Proc().Spawn(ctx, spec); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("Spawn with %s = %v, want a refusal", name, err)
		}
	}
}

func TestFakeProcAliveFollowsSpawnAndSignal(t *testing.T) {
	ctx := context.Background()
	f := NewFake(FakeOptions{Roots: []string{"/rag"}})
	// A pid nobody spawned is not running, and that is an answer, not an error.
	if alive, err := f.Proc().Alive(ctx, 4242); alive || err != nil {
		t.Fatalf("Alive of an unknown pid = %v, %v; want false and no error", alive, err)
	}
	pid, err := f.Proc().Spawn(ctx, jobs.SpawnSpec{
		Program: "/rag/envs/ragstack/bin/python",
		Args:    []string{"--port", "24080"},
		LogPath: seamPidDir + "/logs/api-dev.log",
		PidFile: seamPidDir + "/api-dev.pid",
	})
	if err != nil {
		t.Fatal(err)
	}
	if alive, _ := f.Proc().Alive(ctx, pid); !alive {
		t.Fatal("a just-spawned process is not alive")
	}
	// A HUP leaves it running — that is why one is sent.
	if err := f.Proc().Signal(ctx, pid, "", "uvicorn", "HUP"); err != nil {
		t.Fatal(err)
	}
	if alive, _ := f.Proc().Alive(ctx, pid); !alive {
		t.Error("a HUP killed the process")
	}
	// A TERM does not, and the port goes with it: without that, an
	// instance-mode stop waits out its whole TimeoutStopSec against a fixture
	// that was never going to say the process had died.
	if err := f.Proc().Signal(ctx, pid, "", "uvicorn", "TERM"); err != nil {
		t.Fatal(err)
	}
	if alive, _ := f.Proc().Alive(ctx, pid); alive {
		t.Error("a TERMed process is still alive")
	}
	if listening, _ := f.Proc().Listening(ctx, 24080); listening {
		t.Error("a TERMed process still holds its port")
	}
}

func TestFakeCrontabReadsAndReplacesTheWholeBody(t *testing.T) {
	ctx := context.Background()
	// An account that has never had a crontab: an empty body, no error.
	f := NewFake(FakeOptions{})
	body, err := f.Crontab().List(ctx)
	if err != nil || len(body) != 0 {
		t.Fatalf("List with no crontab = %q, %v; want empty and no error", body, err)
	}

	seeded := "@reboot /rag/bin/start-proxy.sh\n"
	f = NewFake(FakeOptions{Crontab: []byte(seeded)})
	body, err = f.Crontab().List(ctx)
	if err != nil || string(body) != seeded {
		t.Fatalf("List = %q, %v; want the seeded crontab", body, err)
	}
	// The caller edits and writes the whole thing back.
	next := seeded + "@reboot /rag/bin/ctl-daemon.sh start # ragstack-ctl boot\n"
	if err := f.Crontab().Set(ctx, []byte(next)); err != nil {
		t.Fatal(err)
	}
	body, _ = f.Crontab().List(ctx)
	if string(body) != next {
		t.Errorf("after Set, List = %q, want %q", body, next)
	}
	if len(f.FakeCrontab().Sets) != 1 || string(f.FakeCrontab().Sets[0]) != next {
		t.Errorf("Sets = %q, want the one body written", f.FakeCrontab().Sets)
	}
	// List returns a COPY: a caller that edits what it was given must not be
	// editing the host.
	body[0] = 'X'
	again, _ := f.Crontab().List(ctx)
	if string(again) != next {
		t.Error("List handed out the fake's own slice")
	}
}
