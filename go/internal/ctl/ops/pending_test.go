package ops

import (
	"context"
	"sort"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// watched wraps the FAKE driver set and records which driver each step
// reaches for.
//
// It is how this test learns which steps will refuse on the real set without
// running anything against a host: a step that asks for Systemd() is a step
// `systemd.Start lands in PR-D` will stop, whatever it then does with it. The
// fakes underneath actually work, so every step runs to the end and the whole
// plan is observed, not just its first step.
type watched struct {
	*drivers.Fake
	used map[string]bool
}

func newWatched(f *drivers.Fake) *watched { return &watched{Fake: f, used: map[string]bool{}} }

func (w *watched) Systemd() jobs.Systemd     { w.used["systemd"] = true; return w.Fake.Systemd() }
func (w *watched) Proc() jobs.Proc           { w.used["proc"] = true; return w.Fake.Proc() }
func (w *watched) Qdrant() jobs.Qdrant       { w.used["qdrant"] = true; return w.Fake.Qdrant() }
func (w *watched) TenantAPI() jobs.TenantAPI { w.used["tenantapi"] = true; return w.Fake.TenantAPI() }
func (w *watched) Elasticsearch() jobs.Elasticsearch {
	w.used["elasticsearch"] = true
	return w.Fake.Elasticsearch()
}

// planCase is one verb planned on the fixture, with whatever the tenant has
// to look like for it to plan at all. Every verb that reaches a plan is here:
// a verb that refuses at plan time has no steps and so nothing to warn about.
type planCase struct {
	verb   string
	args   map[string]any
	mutate func(*registry.Tenant)
}

// handoverReady is a hand-started tenant with the recovery point and the
// descriptor a handover needs.
func handoverReady(tn *registry.Tenant) {
	tn.API.Bind = "127.0.0.1"
	tn.LastBackup = &registry.BackupRecord{Bundle: "20260914T093000Z-backup", Fenced: true, Verified: true}
	tn.RollbackDescriptor = &registry.RollbackDescriptor{CapturedAt: "2026-09-14T00:00:00Z", GatewayGeneration: 7}
	tn.Stores.Qdrant.Ownership = registry.OwnershipExclusive
	tn.Stores.Qdrant.Capabilities.Stop = true
	tn.Stores.Elasticsearch.Ownership = registry.OwnershipExclusive
	tn.Stores.Elasticsearch.Capabilities.Stop = true
}

// backedUp is a ctl-managed tenant holding a fenced, verified bundle.
func backedUp(tn *registry.Tenant) {
	managed(tn)
	tn.LastBackup = &registry.BackupRecord{Bundle: "20260914T093000Z-backup", Fenced: true, Verified: true}
}

// withSA is backedUp plus the service account the sa-* verbs act on.
func withSA(tn *registry.Tenant) {
	backedUp(tn)
	tn.ServiceAccounts = append(tn.ServiceAccounts, registry.ServiceAccount{
		Subject: "gowe", Role: "user", Status: "active",
	})
}

var planCases = []planCase{
	{"start", nil, managed},
	{"stop", map[string]any{"force": true}, managed},
	{"restart", nil, managed},
	{"backup", map[string]any{"fence": true}, managed},
	{"restore", map[string]any{"from": "20260914T093000Z-backup", "as": "copy"}, managed},
	{"handover", map[string]any{"phase": "execute"}, handoverReady},
	{"migrate-local", map[string]any{"phase": "execute"}, backedUp},
	{"decommission", nil, backedUp},
	{"key-mint", map[string]any{"label": "ops", "role": "user"}, managed},
	{"admin-add", map[string]any{"subject": "bvbrc:alice"}, managed},
	{"sa-create", map[string]any{"subject": "newsa", "role": "user"}, backedUp},
	{"sa-disable", map[string]any{"subject": "gowe"}, withSA},
	{"sa-enable", map[string]any{"subject": "gowe"}, withSA},
	{"env-set", map[string]any{"key": "LOG_LEVEL", "value": "debug"}, managed},
	{"env-unset", map[string]any{"key": "LOG_LEVEL"}, managed},
	{"env-normalize", nil, managed},
	{"render-units", map[string]any{"apply": true}, managed},
}

// TestEveryStepThatWillRefuseSaysSoInThePlan is finding 6's regression: a
// dry run on the real driver set has to warn about the steps that build
// cannot run — on EVERY step that cannot run, and on no other.
func TestEveryStepThatWillRefuseSaysSoInThePlan(t *testing.T) {
	real := drivers.NewReal(drivers.RealOptions{Roots: paths.NewRoots("/rag", paths.Overrides{})})
	pending := map[string]bool{}
	for _, d := range real.Pending() {
		pending[d] = true
	}
	if len(pending) == 0 {
		t.Skip("every real driver is wired; there is nothing left to warn about")
	}

	for _, c := range planCases {
		t.Run(c.verb, func(t *testing.T) {
			// The same plan twice: once against the fakes (which run
			// everything, so no warning), once against the real set.
			ocFake, fake := fixture(t, "dev", c.mutate)
			w := newWatched(fake)
			ocFake.Drivers = w
			fakePlan := plan(t, ocFake, c.verb, c.args)

			ocReal, _ := fixture(t, "dev", c.mutate)
			ocReal.Drivers = real
			realPlan := plan(t, ocReal, c.verb, c.args)

			if len(fakePlan.Steps) != len(realPlan.Steps) {
				t.Fatalf("the two driver sets planned different step counts (%d vs %d): a plan must be a "+
					"function of the registry and the args, not of the host",
					len(fakePlan.Steps), len(realPlan.Steps))
			}

			r := newRunner(ocFake, fake)
			for i, s := range fakePlan.Steps {
				for k := range w.used {
					delete(w.used, k)
				}
				// The fakes make every step succeed or fail honestly; either
				// way it has already told us which driver it wanted.
				_, _ = r.run(s)
				var want []string
				for d := range w.used {
					if pending[d] {
						want = append(want, d)
					}
				}
				sort.Strings(want)

				got := realPlan.Steps[i].Plan.Warnings
				warned := map[string]bool{}
				for _, d := range real.Pending() {
					if containsString(got, pendingWarning(d)) {
						warned[d] = true
					}
				}
				for _, d := range want {
					if !warned[d] {
						t.Errorf("step %d %q runs through the %s driver, which refuses on this build, and the plan "+
							"does not say so; warnings = %v", s.Plan.N, s.Plan.Title, d, got)
					}
					delete(warned, d)
				}
				for d := range warned {
					t.Errorf("step %d %q warns about the %s driver it never uses", s.Plan.N, s.Plan.Title, d)
				}
				// And the fakes, which run everything, warn about nothing.
				for _, d := range real.Pending() {
					if containsString(fakePlan.Steps[i].Plan.Warnings, pendingWarning(d)) {
						t.Errorf("step %d %q warns about %s on a driver set that runs it", s.Plan.N, s.Plan.Title, d)
					}
				}
			}
		})
	}
}

// TestThePendingWarningNamesTheSamePRTheDriverRefusesWith keeps the two
// sentences reading from one source.
func TestThePendingWarningNamesTheSamePRTheDriverRefusesWith(t *testing.T) {
	real := drivers.NewReal(drivers.RealOptions{Roots: paths.NewRoots("/rag", paths.Overrides{})})
	err := real.Systemd().DaemonReload(context.Background())
	if err == nil {
		t.Skip("the systemd driver is wired")
	}
	w := pendingWarning("systemd")
	if !strings.Contains(w, drivers.PendingPR) || !strings.Contains(err.Error(), drivers.PendingPR) {
		t.Fatalf("the warning %q and the refusal %q must name the same PR (%s)", w, err, drivers.PendingPR)
	}
}

// TestAPlanOnRealDriversWarnsOnTheLifecycleSteps is the concrete case from the
// review: `tenant start --dry-run` used to print systemd steps with nothing
// saying they would all refuse.
func TestAPlanOnRealDriversWarnsOnTheLifecycleSteps(t *testing.T) {
	oc, _ := fixture(t, "dev", managed)
	real := drivers.NewReal(drivers.RealOptions{Roots: paths.NewRoots("/rag", paths.Overrides{})})
	// PR-D wired the systemd driver, so `start` no longer plans a step this
	// build cannot run. The test stays for the NEXT driver that is pending on
	// a lifecycle step: it asserts the warning wherever one is still due, and
	// skips while none is.
	if !containsString(real.Pending(), "systemd") {
		t.Skip("the systemd driver is wired; the lifecycle steps have nothing left to warn about")
	}
	oc.Drivers = real
	p := plan(t, oc, "start", nil)
	warned := 0
	for _, s := range p.Steps {
		// A `skip` step is kind systemd and calls nothing; the steps that
		// RUN systemctl are the ones that carry a would_run.
		if s.Plan.Kind != "systemd" || len(s.Plan.WouldRun) == 0 {
			continue
		}
		if !containsString(s.Plan.Warnings, pendingWarning("systemd")) {
			t.Fatalf("step %d %q carries no PR-D warning: %v", s.Plan.N, s.Plan.Title, s.Plan.Warnings)
		}
		warned++
	}
	if warned == 0 {
		t.Fatal("start planned no systemctl step at all")
	}
}

func containsString(hay []string, needle string) bool {
	for _, h := range hay {
		if h == needle {
			return true
		}
	}
	return false
}
