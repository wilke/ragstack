package ops

// "Started" means "answers" (#741, #751 PR-3).
//
// The instance step of a store used to succeed as soon as `apptainer instance
// run` returned, and to skip as "already running" whenever the NAME was in the
// ctl's instance table. On 2026-10-17 dev's Elasticsearch JVM exited and left
// its instance registered — `appinit` and `squashfuse_ll` alive, no JVM,
// nothing on :24043 — and `tenant start dev --only es` reported "succeeded"
// twice while every GoWe ingest failed. Both halves are fixed here:
//
//   - after the run, the step waits (createReadyTimeout) until the process on
//     the leg's port DESCENDS FROM THE INSTANCE (Proc.InstanceOwnsPort) and the
//     store's own readiness probe answers, and FAILS otherwise;
//   - a listed instance that owns nothing on its port is a ZOMBIE: the step
//     that was asked to start that leg stops it (TERM through `apptainer
//     instance stop`, KILL to the starter if it is still listed after that) and
//     starts it again. Nothing outside a start/restart step heals — `fleet
//     status` and `doctor` only flag (owner decision 2 on #751, PR-4).

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/render"
)

// storeAnswerTimeout is how long a store start waits for the store to answer:
// createReadyTimeout, the bound every other readiness wait in the ctl uses. A
// variable so a test does not wait it out.
var storeAnswerTimeout = createReadyTimeout

// zombieKillWait bounds how long a starter that was sent KILL may stay in the
// instance table. A KILL is not negotiable; ten seconds is apptainer noticing.
// A variable so a test does not wait it out.
var zombieKillWait = 10 * time.Second

// storeAnswer is one store leg's "does it answer" question, resolved at plan
// time from the row: the instance, the port it must hold, the leg's own
// readiness probe and the words the success line uses for it.
type storeAnswer struct {
	instance string
	port     int
	// answered is what the success line says the store answered.
	answered string
	// ready is the leg's own readiness probe (ownStoreProbes): ES
	// `_cluster/health?wait_for_status=yellow`, qdrant `/readyz`. Nil for
	// postgres, whose probe is the port itself — the TCP wait addReadyStep has
	// always used, made stricter by asking that the listener be the instance's.
	ready func(context.Context, *jobs.StepContext) error
}

// storeAnswerFor resolves the question for one managed store leg.
func storeAnswerFor(p *planner, c component, instance string) storeAnswer {
	a := storeAnswer{instance: instance, port: c.Port}
	want := ""
	switch c.Leg {
	case render.LegES:
		want, a.answered = "elasticsearch", "`_cluster/health` yellow"
	case render.LegQdrant:
		want, a.answered = "qdrant", "`/readyz`"
	}
	if want != "" {
		for _, pr := range ownStoreProbes(p.t, p.tpaths, true) {
			if pr.what == want {
				a.ready = pr.probe
			}
		}
	}
	if a.ready == nil {
		a.answered = fmt.Sprintf("on :%d (TCP)", c.Port)
	}
	return a
}

// storeSilentError is the zombie's symptom: the instance is in the ctl's table
// and nothing it owns listens on the leg's port.
type storeSilentError struct {
	instance  string
	pid, port int
}

func (e *storeSilentError) Error() string {
	return fmt.Sprintf("instance %s is registered (pid %d) but nothing it owns listens on :%d", e.instance, e.pid, e.port)
}

// storeNotReadyError is the other way a wait can end: the port is the
// instance's, and the store's own probe still says no.
type storeNotReadyError struct {
	instance string
	port     int
	err      error
}

func (e *storeNotReadyError) Error() string {
	return fmt.Sprintf("instance %s holds :%d but does not answer yet: %v", e.instance, e.port, e.err)
}

func (e *storeNotReadyError) Unwrap() error { return e.err }

// owned reports whether the LISTED instance in owns a's port. A table that
// gives the instance no pid cannot tie it to anything; the port merely
// listening is all there is to go on then, and that is what it answers.
func (a storeAnswer) owned(ctx context.Context, sc *jobs.StepContext, in jobs.Instance) (bool, error) {
	proc := sc.Ops.Drivers.Proc()
	if in.PID <= 1 {
		return proc.Listening(ctx, a.port)
	}
	return proc.InstanceOwnsPort(ctx, in.PID, a.port)
}

// probe is the storeProbe awaitStores polls: listed, owns the port, answers.
// Its `gone` is the same fast-fail the API start's postgres wait has had since
// the handover — an instance that has left the table is not coming back, and
// its .err says why.
func (a storeAnswer) probe() storeProbe {
	return storeProbe{
		what: a.instance,
		probe: func(ctx context.Context, sc *jobs.StepContext) error {
			in, listed, err := instanceIn(ctx, sc, a.instance, jobs.NamespaceCtl)
			if err != nil {
				return err
			}
			if !listed {
				return fmt.Errorf("instance %s is not in the ctl's instance table", a.instance)
			}
			owns, err := a.owned(ctx, sc, in)
			if err != nil {
				return err
			}
			if !owns {
				return &storeSilentError{instance: a.instance, pid: in.PID, port: a.port}
			}
			if a.ready != nil {
				if err := a.ready(ctx, sc); err != nil {
					return &storeNotReadyError{instance: a.instance, port: a.port, err: err}
				}
			}
			return nil
		},
		gone: func(ctx context.Context, sc *jobs.StepContext) (string, bool, error) {
			return instanceGoneReason(ctx, sc, a.instance)
		},
	}
}

// await is the wait inside the instance step. It answers the success detail
// ("answered `_cluster/health` yellow after 16 s") or the failure the step
// reports.
func (a storeAnswer) await(ctx context.Context, sc *jobs.StepContext, bound time.Duration) (string, error) {
	start := time.Now()
	err := awaitStores(ctx, sc, []storeProbe{a.probe()}, bound)
	if err == nil {
		return fmt.Sprintf("answered %s after %s", a.answered, seconds(time.Since(start))), nil
	}
	var timeout *readyTimeoutError
	if !errors.As(err, &timeout) {
		return "", err
	}
	var silent *storeSilentError
	if errors.As(timeout.last, &silent) {
		return "", fmt.Errorf("instance %s is registered but nothing it owns listens on :%d after %s",
			a.instance, a.port, seconds(bound))
	}
	var notReady *storeNotReadyError
	if errors.As(timeout.last, &notReady) {
		return "", fmt.Errorf("instance %s holds :%d but did not answer %s within %s: %w",
			a.instance, a.port, a.answered, seconds(bound), notReady.err)
	}
	return "", err
}

// seconds is a duration as the step output writes it: "16 s", "180 s".
func seconds(d time.Duration) string {
	return fmt.Sprintf("%d s", int64(d.Round(time.Second)/time.Second))
}

// listedState is what a start finds when the instance is ALREADY in the table.
type listedState int

const (
	listedHealthy listedState = iota // it owns its port: nothing to do
	listedZombie                     // registered, nothing of its own on the port
	listedGone                       // it left the table while being looked at
)

// classify decides whether a listed instance is healthy or a zombie.
//
// The ownership test is asked twice, one poll apart, before the answer is
// "zombie": an instance is briefly listed before its service binds, and a heal
// that stopped one of those would be a restart loop. A port held by a process
// that is NOT the instance's is neither — it is a refusal: stopping the
// instance cannot give the port back, and a restart would fail to bind it.
func (a storeAnswer) classify(ctx context.Context, sc *jobs.StepContext, in jobs.Instance) (listedState, jobs.Instance, error) {
	if in.PID <= 1 {
		// No pid: nothing to tie the port to. The old rule (listed = running)
		// is the only honest answer, and it is said out loud.
		sc.Logf("the instance table gives %s no pid, so it cannot be tied to :%d; treating it as running",
			a.instance, a.port)
		return listedHealthy, in, nil
	}
	proc := sc.Ops.Drivers.Proc()
	for attempt := 0; ; attempt++ {
		owns, err := proc.InstanceOwnsPort(ctx, in.PID, a.port)
		if err != nil {
			return 0, in, err
		}
		if owns {
			return listedHealthy, in, nil
		}
		listening, err := proc.Listening(ctx, a.port)
		if err != nil {
			return 0, in, err
		}
		if listening {
			owner, _, err := proc.Owner(ctx, a.port)
			if err != nil {
				return 0, in, err
			}
			return 0, in, fmt.Errorf("%w: the instance %s is registered (pid %d) but :%d is held by %s, which is not "+
				"part of it; stopping the instance would not give the port back and a new one could not bind it. "+
				"Find what holds :%d first", jobs.ErrRefused, a.instance, in.PID, a.port, describePid(owner), a.port)
		}
		if attempt >= 1 {
			return listedZombie, in, nil
		}
		select {
		case <-ctx.Done():
			return 0, in, ctx.Err()
		case <-time.After(createReadyPoll):
		}
		again, listed, err := instanceIn(ctx, sc, a.instance, jobs.NamespaceCtl)
		if err != nil {
			return 0, in, err
		}
		if !listed {
			return listedGone, in, nil
		}
		if again.PID != in.PID {
			// Somebody else restarted it in between; judge the new one.
			in = again
			if in.PID <= 1 {
				return listedHealthy, in, nil
			}
		}
	}
}

// stopZombie takes a zombie instance out of the table: `apptainer instance
// stop` (SIGTERM, apptainer's own bound, then the driver's
// instanceStopTimeout), and — when the starter is STILL listed under the same
// pid after that — KILL to the starter. The pid is the one apptainer's own
// table named a moment ago and names again now; Proc.Signal re-checks it
// against /proc's start time immediately before the kill.
func stopZombie(ctx context.Context, sc *jobs.StepContext, name string, in jobs.Instance) error {
	stopErr := sc.Ops.Drivers.Instances().Stop(ctx, name, jobs.StopOptions{Namespace: jobs.NamespaceCtl})
	after, listed, err := instanceIn(ctx, sc, name, jobs.NamespaceCtl)
	if err != nil {
		return err
	}
	if !listed {
		if stopErr != nil {
			sc.Logf("`apptainer instance stop %s` reported %v; the instance is gone anyway", name, stopErr)
		}
		return nil
	}
	if after.PID != in.PID || in.PID <= 1 {
		return fmt.Errorf("%w: %s is still registered after `apptainer instance stop` and its pid is now %d (was %d); "+
			"the ctl kills only the starter it looked at", jobs.ErrRefused, name, after.PID, in.PID)
	}
	why := ""
	if stopErr != nil {
		why = " (" + stopErr.Error() + ")"
	}
	sc.Logf("instance %s (pid %d) is still registered after `apptainer instance stop`%s; sending KILL to its starter",
		name, in.PID, why)
	if err := sc.Ops.Drivers.Proc().Signal(ctx, in.PID, "", "", "KILL"); err != nil {
		return fmt.Errorf("killing the starter of the zombie instance %s (pid %d): %w", name, in.PID, err)
	}
	deadline := time.Now().Add(zombieKillWait)
	for {
		_, listed, err := instanceIn(ctx, sc, name, jobs.NamespaceCtl)
		if err != nil {
			return err
		}
		if !listed {
			return nil
		}
		if time.Now().After(deadline) {
			return fmt.Errorf("%w: %s is still registered %s after KILL to its starter (pid %d)", jobs.ErrRefused,
				name, seconds(zombieKillWait), in.PID)
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(createReadyPoll):
		}
	}
}

// reconcileStore is the instance step's Reconcile: DONE only for an instance
// that is listed AND owns its port — a resumed job must never call a zombie
// started. Anything else runs the step again, which heals a zombie.
func (a storeAnswer) reconcile(ctx context.Context, sc *jobs.StepContext) (jobs.Reconciliation, error) {
	in, listed, err := instanceIn(ctx, sc, a.instance, jobs.NamespaceCtl)
	if err != nil {
		return jobs.ReconcileStuck, err
	}
	if !listed {
		return jobs.ReconcileRedo, nil
	}
	owns, err := a.owned(ctx, sc, in)
	if err != nil {
		return jobs.ReconcileStuck, err
	}
	if owns {
		return jobs.ReconcileDone, nil
	}
	sc.Logf("instance %s is registered (pid %d) but nothing it owns listens on :%d; the step runs again",
		a.instance, in.PID, a.port)
	return jobs.ReconcileRedo, nil
}
