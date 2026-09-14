// Package drivers is the host side of the job engine (plan v3, PR-C): the
// implementations of every interface in jobs.Drivers.
//
// Two sets ship here:
//
//   - NewFake: in-memory fakes for ALL seven drivers. Every one is
//     inspectable (the calls it received, in order, and the state it keeps)
//     and every one can be told to fail a specific call, which is what lets
//     the engine and API tests exercise failure, rollback and reconcile
//     without a host. `serve --fake-drivers` runs on these.
//   - NewReal: the REAL gateway and filesystem drivers — the two whose host
//     surface PR-C already owns (internal/ctl/gateway, and atomic writes
//     under approved roots). Every other real method answers
//     `jobs.ErrRefused: <driver>.<method> lands in PR-D` rather than
//     pretending, so an op planned today runs as far as it honestly can and
//     stops with a sentence that says why.
//
// Nothing here decides policy. A driver does what it is told or refuses
// because it cannot; the refusals that mean "this operation is not allowed"
// live in internal/ctl/ops.
package drivers

import (
	"fmt"
	"strings"
	"sync"
)

// Call is one driver method invocation as the fakes record it. Args are the
// call's arguments rendered as strings, in parameter order, so a test asserts
// on a readable value rather than on a reflect.Value.
type Call struct {
	Driver string
	Method string
	Args   []string
}

// Key is "<driver>.<method>" — the coarse key of the failure table.
func (c Call) Key() string { return c.Driver + "." + c.Method }

// String is "<driver>.<method>(arg, arg)".
func (c Call) String() string { return c.Key() + "(" + strings.Join(c.Args, ",") + ")" }

// recorder is the shared call log and failure table behind every fake.
type recorder struct {
	mu    sync.Mutex
	calls []Call
	fail  map[string]error
}

// record appends the call and returns the error the failure table holds for
// it, matching "<driver>.<method>:<arg0>" before "<driver>.<method>".
func (r *recorder) record(driver, method string, args ...string) error {
	c := Call{Driver: driver, Method: method, Args: args}
	r.mu.Lock()
	defer r.mu.Unlock()
	r.calls = append(r.calls, c)
	if r.fail == nil {
		return nil
	}
	if len(args) > 0 {
		if err, ok := r.fail[c.Key()+":"+args[0]]; ok {
			return err
		}
	}
	return r.fail[c.Key()]
}

// Calls returns every call made so far, in order.
func (r *recorder) Calls() []Call {
	r.mu.Lock()
	defer r.mu.Unlock()
	out := make([]Call, len(r.calls))
	copy(out, r.calls)
	return out
}

// CallKeys returns Calls() rendered as "<driver>.<method>(args…)" strings —
// the form a test asserts an ORDER against.
func (r *recorder) CallKeys() []string {
	calls := r.Calls()
	out := make([]string, len(calls))
	for i, c := range calls {
		out[i] = c.String()
	}
	return out
}

// Count is how many times "<driver>.<method>" was called.
func (r *recorder) Count(key string) int {
	n := 0
	for _, c := range r.Calls() {
		if c.Key() == key {
			n++
		}
	}
	return n
}

// Fail makes the next and every later call to key fail with err. key is
// "<driver>.<method>" or "<driver>.<method>:<first argument>" — the second
// form is how a test fails ONE unit, collection or path.
func (r *recorder) Fail(key string, err error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.fail == nil {
		r.fail = map[string]error{}
	}
	r.fail[key] = err
}

// Clear forgets the recorded calls (not the failure table).
func (r *recorder) Clear() {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.calls = nil
}

// pending is the refusal every real driver method that lands in PR-D answers
// with. It is a jobs.ErrRefused, so the API layer reports 409 `refused` and
// the CLI exits 3 — the same answer a capability refusal gets, because from
// the caller's side they are the same fact: the ctl will not do this today.
func pending(err error, driver, method string) error {
	return fmt.Errorf("%w: %s.%s lands in %s", err, driver, method, PendingPR)
}

// PendingPR names the PR the unwired drivers land in. One string, so the
// refusal a step hits at RUN time and the warning its PLAN carries cannot
// drift apart.
const PendingPR = "PR-D"

// pendingReal is the set of drivers the REAL set has not wired yet — the
// single source both `Real.Pending` and the refusals above are read from. It
// is named by the driver names the ops package's steps declare, which are the
// names in the refusal text (`systemd.Start lands in PR-D`).
var pendingReal = []string{"systemd", "proc", "qdrant", "elasticsearch", "tenantapi"}

// Pending is the drivers this set cannot run, by name.
//
// It exists so a PLAN can say what a RUN will refuse. Before it, `tenant
// start --dry-run` on the real driver set printed a plan of systemd steps with
// nothing to suggest that every one of them would answer "lands in PR-D" the
// moment it ran — a dry run that reads as approval for an operation the build
// cannot perform. The planner asks the driver set it was given, so the same
// verb planned against the fakes (which run everything) carries no warning.
func (r *Real) Pending() []string { return append([]string(nil), pendingReal...) }

// Pending is empty for the fakes: every one of the seven drivers runs.
func (f *Fake) Pending() []string { return nil }
