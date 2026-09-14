package jobs

import (
	"context"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// The doubles the engine tests run against. There is no host here: an Op is a
// plan plus closures, a "driver call" is a function the step calls, and the
// only real I/O is SQLite and the flock files — which are exactly the two
// things whose behaviour under a crash the tests need to be real.

// fakeOp is an Op whose plan the test supplies.
type fakeOp struct {
	verb        string
	destructive bool
	confirm     bool
	locks       []model.LockName
	validate    func(args map[string]any) error
	planFn      func(oc Context, args map[string]any) *Planned
}

func (o *fakeOp) Verb() string      { return o.verb }
func (o *fakeOp) Destructive() bool { return o.destructive }

func (o *fakeOp) Validate(args map[string]any) error {
	if o.validate != nil {
		return o.validate(args)
	}
	return nil
}

func (o *fakeOp) Plan(ctx context.Context, oc Context, args map[string]any) (*Planned, error) {
	p := o.planFn(oc, args)
	p.Locks = o.locks
	p.Plan.RequiresConfirm = o.confirm
	return p, nil
}

// fakeRegistry is jobs.Registry over a map.
type fakeRegistry map[string]Op

func (r fakeRegistry) Lookup(v string) (Op, bool) { op, ok := r[v]; return op, ok }
func (r fakeRegistry) Verbs() []string {
	var out []string
	for v := range r {
		out = append(out, v)
	}
	return out
}

// tracker records what the steps did, in order, so a test can assert on the
// SEQUENCE (checkpoint before the call, rollback in reverse) rather than only
// on the final state.
type tracker struct {
	mu sync.Mutex
	// calls is the ordered trace: "run:1", "driver:1", "rollback:2"…
	calls []string
	// idsAtCall is what the STORE held for the step's external ids at the
	// moment the fake driver call ran — the proof that the checkpoint is
	// durable before the external effect, not after.
	idsAtCall map[string][]string
}

func newTracker() *tracker { return &tracker{idsAtCall: map[string][]string{}} }

func (tr *tracker) log(s string) {
	tr.mu.Lock()
	defer tr.mu.Unlock()
	tr.calls = append(tr.calls, s)
}

func (tr *tracker) trace() []string {
	tr.mu.Lock()
	defer tr.mu.Unlock()
	return append([]string(nil), tr.calls...)
}

func (tr *tracker) seen(key string) []string {
	tr.mu.Lock()
	defer tr.mu.Unlock()
	return append([]string(nil), tr.idsAtCall[key]...)
}

func (tr *tracker) record(key string, ids []string) {
	tr.mu.Lock()
	defer tr.mu.Unlock()
	tr.idsAtCall[key] = append([]string(nil), ids...)
}

// testRedactor replaces a literal, the way the real one replaces every value
// seeded from the secret files.
type testRedactor struct{ secret string }

func (r testRedactor) Redact(s string) string {
	if r.secret == "" {
		return s
	}
	return strings.ReplaceAll(s, r.secret, "<REDACTED>")
}

func (r testRedactor) RedactArgs(args map[string]any) map[string]any {
	out := map[string]any{}
	for k, v := range args {
		if s, ok := v.(string); ok {
			out[k] = r.Redact(s)
			continue
		}
		out[k] = v
	}
	return out
}

// testFleet is a deterministic registry snapshot: every process that builds
// it computes the same plan hash from it, which the kill-during-job test
// depends on (the parent must be able to re-plan the child's job).
func testFleet() *registry.Fleet {
	return &registry.Fleet{
		SchemaVersion: 1,
		Generation:    7,
		RagRoot:       "/rag",
		Tenants: map[string]*registry.Tenant{
			"dev": {Name: "dev", ManifestName: "dev", State: "active", Supervisor: "manual"},
		},
	}
}

func testRoots(dir string) paths.Roots {
	return paths.NewRoots(dir, paths.Overrides{})
}

// operator is the principal every happy-path test submits as.
func operator() Principal {
	return Principal{
		Subject: "key:ops", Role: "operator", Method: model.AuthAPIKey,
		RequestID: "0123456789abcdef",
	}
}

// newTestEngine wires an engine over a fresh store in t.TempDir().
func newTestEngine(t *testing.T, reg Registry, tweak func(*EngineOptions)) (*engine, Store, paths.Roots) {
	t.Helper()
	dir := t.TempDir()
	roots := testRoots(dir)
	st, err := NewStore(filepath.Join(roots.CtlStateDir, "jobs.db"))
	if err != nil {
		t.Fatalf("NewStore: %v", err)
	}
	t.Cleanup(func() { _ = st.Close() })
	o := EngineOptions{
		Store:     st,
		Ops:       reg,
		Roots:     roots,
		LoadFleet: func() (*registry.Fleet, error) { return testFleet(), nil },
		Host:      "testhost",
		Mode:      model.WorkerDirect,
	}
	if tweak != nil {
		tweak(&o)
	}
	return NewEngine(o).(*engine), o.Store, roots
}

// waitFor polls until the job reaches one of want. Polling the STORE (not an
// in-process channel) is deliberate: it is the same view the API layer and a
// second process have, so a state a test can see is a state that was durably
// written.
func waitFor(t *testing.T, e Engine, id string, want ...model.JobState) *model.Job {
	t.Helper()
	deadline := time.Now().Add(15 * time.Second)
	var last model.JobState
	for time.Now().Before(deadline) {
		j, err := e.Get(context.Background(), id)
		if err == nil {
			last = j.State
			for _, w := range want {
				if j.State == w {
					if j.State.Terminal() {
						settle(t, e, id)
					}
					return j
				}
			}
		}
		time.Sleep(3 * time.Millisecond)
	}
	t.Fatalf("job %s never reached %v (last state %q)", id, want, last)
	return nil
}

// plannedStep is the shorthand the fake plans are built from.
func plannedStep(n int, kind, title string, targets ...string) model.PlannedStep {
	return model.PlannedStep{
		N: n, Kind: kind, Title: title, Targets: targets,
		WouldWrite: []model.WouldWrite{},
		WouldRun:   []model.WouldRun{{Argv: []string{"/bin/true", kind}}},
		Warnings:   []string{},
	}
}

// settle waits until the engine has no worker registered for id. A job's
// state reaches the store just before its last audit row and the release of
// its bookkeeping, so a test that stops at the state alone can still race the
// worker's tail — and a t.TempDir() torn down under a live writer fails in a
// way that looks like a bug in the code under test.
func settle(t *testing.T, e Engine, id string) {
	t.Helper()
	ee, ok := e.(*engine)
	if !ok {
		return
	}
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		ee.mu.Lock()
		_, busy := ee.active[id]
		ee.mu.Unlock()
		if !busy {
			return
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatalf("the worker of job %s never let go", id)
}
