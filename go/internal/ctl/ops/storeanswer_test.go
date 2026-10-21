package ops

// "Started" means "answers", and a zombie heals inside the start that was
// asked for (#741, #751 PR-3).

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
)

// fastStoreWaits shrinks every bound the store start waits under, so a test of
// "never answers" costs milliseconds, not three minutes.
func fastStoreWaits(t *testing.T) {
	t.Helper()
	oldPoll, oldTimeout, oldKill := createReadyPoll, storeAnswerTimeout, zombieKillWait
	createReadyPoll, storeAnswerTimeout, zombieKillWait = time.Millisecond, 50*time.Millisecond, 20*time.Millisecond
	t.Cleanup(func() { createReadyPoll, storeAnswerTimeout, zombieKillWait = oldPoll, oldTimeout, oldKill })
}

// runLogged runs one step's run half with its log captured.
func runLogged(r *runner, s jobs.Step) (string, []string, error) {
	sc := r.ctx(s)
	var logged []string
	sc.Logf = func(format string, args ...any) { logged = append(logged, fmt.Sprintf(format, args...)) }
	out, err := s.Run(context.Background(), sc)
	return out, logged, err
}

// startedFleet is an instance-mode tenant with every leg up.
func startedFleet(t *testing.T) (jobs.Context, *drivers.Fake) {
	t.Helper()
	oc, fake := instanceFixture(t)
	newRunner(oc, fake).runAll(t, plan(t, oc, "start", nil))
	if names := strings.Join(fake.FakeInstances().Names(), ","); names != "elasticsearch-dev,postgres-dev,qdrant-dev" {
		t.Fatalf("the fixture tenant did not come up: %s", names)
	}
	return oc, fake
}

func esStep(t *testing.T, p *jobs.Planned) jobs.Step {
	t.Helper()
	s, _ := stepNamed(t, p, "start the instance elasticsearch-dev")
	return s
}

// (a) `apptainer instance run` returned and the instance is registered, but
// nothing of its own ever listens: the step FAILS, naming the port — the
// sentence #741 needed instead of "succeeded".
func TestStoreStartFailsWhenNothingItOwnsListens(t *testing.T) {
	fastStoreWaits(t)
	oc, fake := instanceFixture(t)
	fake.FakeInstances().SilentOnRun = map[string]bool{"elasticsearch-dev": true}
	r := newRunner(oc, fake)
	_, _, err := runLogged(r, esStep(t, plan(t, oc, "start", map[string]any{"only": []any{"es"}})))
	want := fmt.Sprintf("instance elasticsearch-dev is registered but nothing it owns listens on :%d after ",
		oc.Tenant.Ports.ESHTTP)
	if err == nil || !strings.Contains(err.Error(), want) {
		t.Fatalf("err = %v, want %q", err, want)
	}
}

// …and when the port IS the instance's but the cluster never reaches yellow,
// the step fails too, and says that instead.
func TestStoreStartFailsWhenTheStoreNeverAnswersItsOwnProbe(t *testing.T) {
	fastStoreWaits(t)
	oc, fake := instanceFixture(t)
	fake.Fail("es.Ready", errors.New("elasticsearch is not ready: 408 timed out waiting for yellow"))
	r := newRunner(oc, fake)
	_, _, err := runLogged(r, esStep(t, plan(t, oc, "start", map[string]any{"only": []any{"es"}})))
	if err == nil || !strings.Contains(err.Error(), "holds :24043 but did not answer `_cluster/health` yellow within") ||
		!strings.Contains(err.Error(), "408 timed out") {
		t.Fatalf("err = %v", err)
	}
}

// The success line says what answered.
func TestStoreStartSaysWhatAnswered(t *testing.T) {
	fastStoreWaits(t)
	oc, fake := instanceFixture(t)
	r := newRunner(oc, fake)
	out, logged, err := runLogged(r, esStep(t, plan(t, oc, "start", map[string]any{"only": []any{"es"}})))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(out, "started elasticsearch-dev; answered `_cluster/health` yellow after ") {
		t.Errorf("result = %q", out)
	}
	if !strings.Contains(strings.Join(logged, "\n"), "started elasticsearch-dev; answered `_cluster/health` yellow") {
		t.Errorf("log = %v", logged)
	}
	// The ownership test came before the store's own probe, on the port the
	// row gives the leg.
	got := strings.Join(fake.CallKeys(), "\n")
	if !strings.Contains(got, "proc.InstanceOwnsPort(") || !strings.Contains(got, ",24043)") || !strings.Contains(got, "es.Ready(") {
		t.Errorf("the wait did not ask for ownership and readiness:\n%s", got)
	}
}

// (b) The registered instance whose store has exited is a zombie: the start
// stops it, says so, starts it, and waits for it to answer.
func TestStoreStartHealsAZombie(t *testing.T) {
	fastStoreWaits(t)
	oc, fake := startedFleet(t)
	ins := fake.FakeInstances()
	before := ins.Running["elasticsearch-dev"].PID
	if !ins.Zombify("elasticsearch-dev") {
		t.Fatal("no elasticsearch-dev to zombify")
	}
	fake.Clear()

	r := newRunner(oc, fake)
	out, logged, err := runLogged(r, esStep(t, plan(t, oc, "start", map[string]any{"only": []any{"es"}})))
	if err != nil {
		t.Fatalf("the heal failed: %v", err)
	}
	want := fmt.Sprintf("instance elasticsearch-dev was a zombie: registered (pid %d) with nothing on :24043; "+
		"stopped and restarting", before)
	if !strings.Contains(strings.Join(logged, "\n"), want) {
		t.Errorf("no %q in the log:\n%s", want, strings.Join(logged, "\n"))
	}
	if !strings.Contains(out, "started elasticsearch-dev; answered") || !strings.Contains(out, "zombie") {
		t.Errorf("result = %q", out)
	}
	got := strings.Join(fake.CallKeys(), "\n")
	stop, run := strings.Index(got, "instances.Stop(elasticsearch-dev,ctl)"), strings.Index(got, "instances.Run(elasticsearch-dev,")
	if stop < 0 || run < 0 || stop > run {
		t.Errorf("not stop-then-run:\n%s", got)
	}
	if strings.Contains(got, ",KILL)") {
		t.Errorf("a zombie that stopped on `instance stop` was killed anyway:\n%s", got)
	}
	after := ins.Running["elasticsearch-dev"].PID
	if after == before {
		t.Errorf("the instance was not replaced (pid %d)", after)
	}
	if owns, _ := fake.Proc().InstanceOwnsPort(context.Background(), after, 24043); !owns {
		t.Error("the restarted instance does not own its port")
	}
}

// A zombie that `apptainer instance stop` cannot take down is KILLed at its
// starter — the pid the instance table names — and then started again.
func TestStoreStartKillsAZombieThatWillNotStop(t *testing.T) {
	fastStoreWaits(t)
	oc, fake := startedFleet(t)
	ins := fake.FakeInstances()
	pid := ins.Running["elasticsearch-dev"].PID
	ins.Zombify("elasticsearch-dev")
	ins.StuckOnStop = map[string]bool{"elasticsearch-dev": true}
	fake.Clear()

	r := newRunner(oc, fake)
	_, logged, err := runLogged(r, esStep(t, plan(t, oc, "start", map[string]any{"only": []any{"es"}})))
	if err != nil {
		t.Fatalf("the heal failed: %v", err)
	}
	got := strings.Join(fake.CallKeys(), "\n")
	if !strings.Contains(got, fmt.Sprintf("proc.Signal(%d,,,KILL)", pid)) {
		t.Errorf("the starter (pid %d) was not killed:\n%s", pid, got)
	}
	if !strings.Contains(strings.Join(logged, "\n"), "sending KILL to its starter") {
		t.Errorf("the KILL is not in the log: %v", logged)
	}
	if strings.Index(got, ",KILL)") > strings.Index(got, "instances.Run(elasticsearch-dev,") {
		t.Errorf("the instance was started before the zombie was gone:\n%s", got)
	}
}

// (c) A healthy instance is left alone, with the result string the watchdog
// has always seen.
func TestStoreStartLeavesAHealthyInstanceAlone(t *testing.T) {
	fastStoreWaits(t)
	oc, fake := startedFleet(t)
	pid := fake.FakeInstances().Running["elasticsearch-dev"].PID
	fake.Clear()
	r := newRunner(oc, fake)
	out, _, err := runLogged(r, esStep(t, plan(t, oc, "start", map[string]any{"only": []any{"es"}})))
	if err != nil || out != "already running: elasticsearch-dev" {
		t.Fatalf("out = %q, err = %v", out, err)
	}
	got := strings.Join(fake.CallKeys(), "\n")
	for _, never := range []string{"instances.Stop(", "instances.Run(", "proc.Signal("} {
		if strings.Contains(got, never) {
			t.Errorf("a healthy instance saw %s:\n%s", never, got)
		}
	}
	if fake.FakeInstances().Running["elasticsearch-dev"].PID != pid {
		t.Error("the healthy instance was replaced")
	}
}

// An instance whose port is held by somebody ELSE is not a zombie to heal:
// stopping it would not give the port back. The step refuses and stops nothing.
func TestStoreStartRefusesWhenAForeignProcessHoldsThePort(t *testing.T) {
	fastStoreWaits(t)
	oc, fake := startedFleet(t)
	fake.FakeInstances().Zombify("elasticsearch-dev")
	fake.FakeProc().MarkAlive(31337, 24043)
	fake.FakeProc().SetOwner(24043, 31337, 1000)
	fake.Clear()
	r := newRunner(oc, fake)
	_, _, err := runLogged(r, esStep(t, plan(t, oc, "start", map[string]any{"only": []any{"es"}})))
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "which is not part of it") {
		t.Fatalf("err = %v, want a refusal naming the foreign holder", err)
	}
	if got := strings.Join(fake.CallKeys(), "\n"); strings.Contains(got, "instances.Stop(") {
		t.Errorf("the instance was stopped over a port it cannot get back:\n%s", got)
	}
}

// Reconcile asks the same question: a resumed job never calls a zombie DONE.
func TestStoreStartReconcileDoesNotTrustAZombie(t *testing.T) {
	fastStoreWaits(t)
	oc, fake := startedFleet(t)
	step := esStep(t, plan(t, oc, "start", map[string]any{"only": []any{"es"}}))
	r := newRunner(oc, fake)
	if v, err := step.Reconcile(context.Background(), r.ctx(step)); err != nil || v != jobs.ReconcileDone {
		t.Fatalf("healthy: %v, %v; want done", v, err)
	}
	fake.FakeInstances().Zombify("elasticsearch-dev")
	if v, err := step.Reconcile(context.Background(), r.ctx(step)); err != nil || v != jobs.ReconcileRedo {
		t.Fatalf("zombie: %v, %v; want redo", v, err)
	}
}

// (d) The wait lives INSIDE the instance step: `start --only es` is the
// instance step and the desired_boot note — no separate readiness step — and
// the instance step's warnings say what it waits for.
func TestStoreStartPlanHasTheWaitInsideTheInstanceStep(t *testing.T) {
	oc, _ := instanceFixture(t)
	p := plan(t, oc, "start", map[string]any{"only": []any{"es"}})
	got := strings.Join(titles(p), "\n")
	if want := "instance: start the instance elasticsearch-dev\ninstance: enable es (desired_boot)"; got != want {
		t.Errorf("steps =\n%s\nwant\n%s", got, want)
	}
	if !stepWarns(p, "start the instance elasticsearch-dev", "succeeds only once the store answers") ||
		!stepWarns(p, "start the instance elasticsearch-dev", "is a zombie") {
		t.Errorf("the instance step does not say it waits: %v", stepWarnings(p, "instance", "elasticsearch-dev"))
	}
	// The full start keeps its shape: one instance step per store and the
	// readiness steps it always had, nothing new.
	full := titles(plan(t, oc, "start", nil))
	for _, s := range full {
		if strings.Contains(s, "wait for elasticsearch") || strings.Contains(s, "wait for qdrant") {
			t.Errorf("a separate store wait step appeared: %v", full)
		}
	}
}

// The partial start's desired_boot step reads the TENANT's row and says what
// the next boot does; it never writes it.
func TestPartialStartSaysWhatDesiredBootWillDo(t *testing.T) {
	fastStoreWaits(t)
	for _, c := range []struct{ row, want string }{
		{"enabled", "`fleet start --all` starts es with the rest of the tenant"},
		{"disabled", "WARNING: desired_boot is disabled for dev"},
	} {
		oc, fake := instanceFixture(t)
		oc.Fleet.Tenants["dev"].DesiredBoot = c.row
		p := plan(t, oc, "start", map[string]any{"only": []any{"es"}})
		step, _ := stepNamed(t, p, "enable es (desired_boot)")
		out, logged, err := runLogged(newRunner(oc, fake), step)
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(strings.Join(logged, "\n"), c.want) || !strings.HasPrefix(out, "desired_boot "+c.row) {
			t.Errorf("row %s: out %q, log %v", c.row, out, logged)
		}
		if oc.Fleet.Tenants["dev"].DesiredBoot != c.row {
			t.Errorf("row %s: the partial start wrote desired_boot", c.row)
		}
	}
}
