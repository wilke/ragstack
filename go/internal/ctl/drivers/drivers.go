// Package drivers is the host side of the job engine (plan v3, PR-C): the
// implementations of every interface in jobs.Drivers.
//
// Two sets ship here:
//
//   - NewFake: in-memory fakes for EVERY driver. Every one is
//     inspectable (the calls it received, in order, and the state it keeps)
//     and every one can be told to fail a specific call, which is what lets
//     the engine and API tests exercise failure, rollback and reconcile
//     without a host. `serve --fake-drivers` runs on these.
//   - NewReal: the REAL drivers — the gateway and the filesystem (PR-C), the
//     host drivers that run a program (systemd, proc, git and build, over the
//     argv runner in exec.go) and the store drivers (qdrant, elasticsearch,
//     the tenant API, postgres, sqlite and tar) from PR-D, and PR-D2's
//     instances (apptainer) and crontab, through which `supervisor: instance`
//     runs a tenant's stores and installs its boot hook. All fourteen are
//     wired — Real.Pending() is empty — and the machinery that let an unwired
//     driver say so in a dry run is still there for the next one.
//
// Nothing here decides policy. A driver does what it is told or refuses
// because it cannot; the refusals that mean "this operation is not allowed"
// live in internal/ctl/ops.
package drivers

import (
	"fmt"
	"regexp"
	"strings"
	"sync"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
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

// PendingPR names the PR an unwired driver would land in. It is read by
// ops/pending.go, which puts the same sentence in a PLAN that the driver would
// refuse a RUN with, so the two cannot drift apart.
//
// Nothing is pending today: PR-D2 wired the last two (instances and crontab)
// and proc's Spawn and Alive with them. The const and pendingReal stay because
// the machinery is what makes the NEXT unwired driver honest in a dry run —
// a name goes back on the list, and every plan step that uses it says so.
const PendingPR = "PR-D2"

// pendingReal is the set of drivers the REAL set has not wired — the single
// source both `Real.Pending` and a plan's warning are read from. Empty: every
// one of the fourteen drivers runs.
var pendingReal []string

func (r *Real) Pending() []string { return append([]string(nil), pendingReal...) }

// Pending is empty for the fakes: every one of the fourteen drivers runs.
func (f *Fake) Pending() []string { return nil }

// ---------------------------------------------------------------- instances

// instanceNameRE is the ONLY shape an instance name the ctl starts or stops
// may have: `<kind>-<tenant>`, for the three store kinds a tenant owns.
//
// It is the same class of allowlist as systemd.go's checkUnit, for the same
// reason. `apptainer instance stop` takes a name, coconut's account runs
// instances this control plane did not start (the gateway's nginx among
// them), and a stop of an unconstrained name is one registry typo away from
// taking down something nobody asked about. The tenant half is the tenant-name
// grammar, so an instance name is derivable from a row and nothing else.
var instanceNameRE = regexp.MustCompile(`^(qdrant|elasticsearch|postgres)-[a-z][a-z0-9-]{0,31}$`)

// checkInstanceName applies that allowlist. Shared by the fake and the real
// driver so the refusal a test sees is the refusal the host gives.
func checkInstanceName(name string) error {
	if !instanceNameRE.MatchString(name) {
		return fmt.Errorf("%w: %q is not an instance this control plane manages "+
			"(want <qdrant|elasticsearch|postgres>-<tenant>)", jobs.ErrRefused, name)
	}
	return nil
}
