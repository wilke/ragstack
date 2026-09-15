package ops

// Saying, in the PLAN, which steps this build cannot actually run.
//
// A plan is the operator's approval document: `--dry-run` prints it, a human
// reads it, and `--yes` says "do that". On the REAL driver set five of the
// seven drivers are not wired yet and refuse at run time with
// `<driver>.<method> lands in PR-D` — so `tenant start --dry-run` printed a
// tidy list of systemd steps, every one of which would refuse the moment it
// ran. A plan that reads as approval for an operation the build cannot perform
// is worse than no plan: the operator finds out by watching a job fail
// halfway, with the gateway already fenced.
//
// migrate-local said so by hand, in a warning written next to its steps. That
// is the same fact stated in a second place, which is how the two come to
// disagree the day a driver lands. So the list lives ONCE, in the drivers
// package, beside the refusals themselves, and both the run-time refusal and
// the plan-time warning are read from it.
//
// The driver set is asked, not assumed: the fakes run everything, so a plan
// made against them carries no warning and the goldens stay the goldens.
//
// The granularity is the DRIVER, and PR-D2 is the first PR where that is not
// the whole truth. `proc` is wired, but its two new methods — Spawn and Alive
// — are not, so a step that spawns the tenant's API declares the driver
// "proc" and carries NO warning even though it will refuse with
// `proc.Spawn lands in PR-D2`. Per-method granularity would mean every addFor
// call naming a method as well as a driver, for a window that closes in this
// same PR series; naming `proc` pending instead would warn on every step that
// only signals or probes a port, which is a plan lying about operations that
// work today. drivers.pendingReal carries the full reasoning and
// drivers_test.go asserts the two methods refuse.

import (
	"fmt"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
)

// pendingDrivers is the OPTIONAL interface a jobs.Drivers set implements to
// say which of its drivers are not wired on this build.
//
// Optional rather than a method on jobs.Drivers: a driver set that does not
// implement it is simply one that makes no claim, and is taken at its word
// that everything works — which is the right default for a set somebody wrote
// outside this repository.
type pendingDrivers interface{ Pending() []string }

// driverPending reports whether the set this plan will RUN through has said
// it cannot run `name`.
func (p *planner) driverPending(name string) bool {
	d, ok := p.oc.Drivers.(pendingDrivers)
	if !ok {
		return false
	}
	for _, have := range d.Pending() {
		if have == name {
			return true
		}
	}
	return false
}

// pendingWarning is the sentence such a step carries. It names the driver, so
// an operator reading a plan of eleven steps can see which four of them stop.
func pendingWarning(driver string) string {
	return fmt.Sprintf("this step needs the %s driver, which lands in %s: on this build the job will fail at this step",
		driver, drivers.PendingPR)
}

// addFor is add() for a step that runs through ONE driver: it adds the
// warning when that driver is not wired, and is otherwise add().
//
// Every step whose Run touches sc.Ops.Drivers.<X>() goes through this rather
// than through add(), which is what keeps "the plan warns" and "the run
// refuses" the same statement.
func (p *planner) addFor(driver string, s step) {
	if p.driverPending(driver) {
		s.Warnings = append(append([]string(nil), s.Warnings...), pendingWarning(driver))
	}
	p.add(s)
}
