package gateway

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// interruptGen2 leaves behind exactly what a publish killed between the HUP and
// the verification leaves: gen-2 on disk, `current` pointing at it, both include
// symlinks resolving through it, and a txn.json with no finished_at.
//
// The registry change makes gen-2's tenant LIST differ from gen-1's, so the
// probes of the two generations are distinguishable.
func interruptGen2(t *testing.T, f *registry.Fleet, roots paths.Roots, state string) *Generation {
	t.Helper()
	f.Generation++
	f.Tenants["demo"].State = "decommissioned"
	gen2, err := Render(f, roots)
	if err != nil {
		t.Fatal(err)
	}
	st := NewState(roots)
	if err := gen2.Write(st); err != nil {
		t.Fatal(err)
	}
	prior, err := inspectIncludes(roots.ProxyDir)
	if err != nil {
		t.Fatal(err)
	}
	if err := st.pointCurrentAt(gen2.N); err != nil {
		t.Fatal(err)
	}
	if err := st.WriteTxn(&Txn{
		Generation: gen2.N, RegistryGeneration: f.Generation, StartedAt: now().UTC().Format(time.RFC3339),
		State: state, PublishedBy: "wilke", PreviousGeneration: 1, NginxPID: 4242,
		ReloadedAt: now().UTC().Format(time.RFC3339), Includes: prior,
	}); err != nil {
		t.Fatal(err)
	}
	return gen2
}

// hashDir digests a directory tree by name, kind, symlink target and content —
// everything that matters and nothing that does not (no mtimes), so a test can
// say "this state dir was not written to".
func hashDir(t *testing.T, dir string) string {
	t.Helper()
	var lines []string
	err := filepath.WalkDir(dir, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(dir, p)
		if err != nil {
			return err
		}
		switch {
		case d.IsDir():
			lines = append(lines, "dir  "+rel)
		case d.Type()&fs.ModeSymlink != 0:
			target, err := os.Readlink(p)
			if err != nil {
				return err
			}
			lines = append(lines, "link "+rel+" -> "+target)
		default:
			b, err := os.ReadFile(p)
			if err != nil {
				return err
			}
			sum := sha256.Sum256(b)
			lines = append(lines, "file "+rel+" "+hex.EncodeToString(sum[:]))
		}
		return nil
	})
	if err != nil {
		t.Fatalf("hashing %s: %v", dir, err)
	}
	sort.Strings(lines)
	return strings.Join(lines, "\n")
}

// A publication interrupted AFTER its SIGHUP leaves the workers on gen-2 while
// repair puts `current` back on gen-1 — pointer only, because repair never
// signals. `Rollback(to=1)` then found cur == to and reported `rolled_back`
// without a single HUP: the pointer and the result said gen-1 while every
// request was still answered by gen-2.
//
// The pointer is not evidence about nginx. A rollback whose target is not
// KNOWN to be confirmed has to do the whole sequence.
func TestRollbackAfterAnInterruptedPublishReloads(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, ex, sig, pr := testOpts(t, roots)
	publishOnce(t, f, opts, pr) // gen-1, verified
	st := NewState(roots)

	interruptGen2(t, f, roots, TxnReloaded)
	if st.CurrentGeneration() != 2 {
		t.Fatalf("the interrupted publication did not leave current on gen-2 (got %d)", st.CurrentGeneration())
	}

	// The gateway answers gen-1's routing, which is what a rollback to gen-1
	// has to see before it may call itself done.
	gen1files, err := st.ReadGeneration(1)
	if err != nil {
		t.Fatal(err)
	}
	answerDesiredState(t, pr, &Generation{N: 1, Files: gen1files}, f)

	beforeSig, beforeExec, beforeWorkers, beforeGets := sig.Count(), ex.Count(), sig.WorkersCount(), pr.Count()
	res, err := Rollback(context.Background(), f, 1, opts)
	if err != nil {
		t.Fatalf("rollback: %v (steps %+v)", err, res.Steps)
	}
	if got := sig.Count() - beforeSig; got != 1 {
		t.Errorf("%d HUPs, want exactly one: a rollback after an interrupted publication MUST reload", got)
	}
	if got := ex.Count() - beforeExec; got != 1 {
		t.Errorf("%d nginx -t runs, want exactly one (the target staged against today's tree)", got)
	}
	if sig.WorkersCount() <= beforeWorkers+1 {
		t.Error("the worker set was not sampled after the HUP, so the reload was never confirmed")
	}
	if pr.Count() <= beforeGets {
		t.Error("no probe ran")
	}
	var probedGen1 bool
	for _, u := range pr.Gets[beforeGets:] {
		// demo is in gen-1's tenant list and NOT in gen-2's, so asking for it is
		// proof the probe checked the generation being rolled back to.
		if strings.HasSuffix(u, "/ragstack/demo/api/health") {
			probedGen1 = true
		}
	}
	if !probedGen1 {
		t.Errorf("the probe did not check gen-1's own routing: %v", pr.Gets[beforeGets:])
	}
	if res.State != TxnRolledBack {
		t.Errorf("state %q, want %q", res.State, TxnRolledBack)
	}
	if st.CurrentGeneration() != 1 {
		t.Errorf("current = gen-%d after the rollback", st.CurrentGeneration())
	}
	// The operator is told the host was found mid-publication.
	if !hasWarning(res, "the previous publication was incomplete") {
		t.Errorf("the repair was not surfaced in the result: %v", res.Warnings)
	}
	txn, ok, err := st.ReadTxn()
	if err != nil || !ok {
		t.Fatalf("txn: %v %v", ok, err)
	}
	if txn.State != TxnRolledBack || !txn.Complete() || txn.Generation != 1 {
		t.Errorf("txn = %+v, want a finished rolled_back naming gen-1", txn)
	}
	if !st.WasVerified(1) {
		t.Error("gen-1 is no longer recorded as verified")
	}
}

// The shortcut is still taken — no HUP, no nginx -t — when the record proves
// the running master was confirmed on the target.
func TestRollbackShortcutOnlyWhenTheLastTxnVerified(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, ex, sig, pr := testOpts(t, roots)
	publishOnce(t, f, opts, pr) // gen-1, verified, current = 1
	beforeSig, beforeExec := sig.Count(), ex.Count()

	res, err := Rollback(context.Background(), f, 1, opts)
	if err != nil {
		t.Fatalf("rollback to the generation already serving: %v", err)
	}
	if res.State != TxnRolledBack {
		t.Errorf("state %q", res.State)
	}
	if sig.Count() != beforeSig {
		t.Errorf("%d HUPs, want none: gen-1 is verified and already current", sig.Count()-beforeSig)
	}
	if ex.Count() != beforeExec {
		t.Errorf("%d nginx -t runs, want none", ex.Count()-beforeExec)
	}
	if len(res.Steps) != 1 || res.Steps[0].Name != "switch" {
		t.Errorf("steps = %+v, want the single no-op switch", res.Steps)
	}
}

// A rollback whose reload the master does not take is a FAILURE, not a
// `rolled_back` with a warning: the workers kept the configuration they had.
func TestRollbackReloadFailureIsReported(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, _, sig, pr := testOpts(t, roots)
	publishOnce(t, f, opts, pr) // gen-1
	f.Generation++
	f.Tenants["dev"].Ports.API = 24044
	publishOnce(t, f, opts, pr) // gen-2, verified and serving
	st := NewState(roots)

	gen1files, err := st.ReadGeneration(1)
	if err != nil {
		t.Fatal(err)
	}
	answerDesiredState(t, pr, &Generation{N: 1, Files: gen1files}, f)

	// The master answers the signal and keeps its workers: nginx logged
	// [emerg] and carried on with the old configuration.
	sig.FrozenWorkers = true
	res, err := Rollback(context.Background(), f, 1, opts)
	if err == nil {
		t.Fatal("rollback reported success although the master never took the configuration")
	}
	if res.State == TxnRolledBack || res.State == TxnVerified {
		t.Errorf("state %q claims success", res.State)
	}
	if res.State != TxnRollbackFailed || !res.Reverted {
		t.Errorf("result %+v, want %q with a revert", res, TxnRollbackFailed)
	}
	if st.CurrentGeneration() != 2 {
		t.Errorf("current = gen-%d, want the gen-2 the rollback started from", st.CurrentGeneration())
	}
	txn, _, err := st.ReadTxn()
	if err != nil {
		t.Fatal(err)
	}
	if txn.State == TxnVerified || txn.State == TxnRolledBack {
		t.Errorf("txn = %+v, want a failure state", txn)
	}
}

// `gateway apply --dry-run` used to call repair BEFORE it looked at DryRun, so
// a dry run against a host with an interrupted publication moved `current` —
// arming a routing change nobody asked for at whatever reload came next.
//
// A dry run may not write anything in the state dir. It reports the incomplete
// transaction and still validates the freshly rendered generation, which is
// faithful because the staged tree resolves no include through `current` (see
// the comment in Publish).
func TestDryRunNeverTouchesState(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, ex, sig, pr := testOpts(t, roots)
	publishOnce(t, f, opts, pr) // gen-1, verified
	st := NewState(roots)
	interruptGen2(t, f, roots, TxnSwitched)
	if st.CurrentGeneration() != 2 {
		t.Fatalf("current = gen-%d before the dry run", st.CurrentGeneration())
	}

	before := hashDir(t, st.Dir)
	beforeSig, beforeExec := sig.Count(), ex.Count()
	opts.DryRun = true
	res, err := Publish(context.Background(), f, opts)
	if err != nil {
		t.Fatalf("dry run: %v (steps %+v)", err, res.Steps)
	}
	if res.State != "dry-run" {
		t.Errorf("state %q", res.State)
	}
	if st.CurrentGeneration() != 2 {
		t.Errorf("the dry run repointed current to gen-%d", st.CurrentGeneration())
	}
	if after := hashDir(t, st.Dir); after != before {
		t.Errorf("the dry run wrote in the state dir:\nbefore:\n%s\nafter:\n%s", before, after)
	}
	if _, err := os.Stat(st.GenDir(3)); !errors.Is(err, os.ErrNotExist) {
		t.Errorf("the dry run wrote gen-3: %v", err)
	}
	if sig.Count() != beforeSig {
		t.Error("the dry run signalled the master")
	}
	if ex.Count() != beforeExec+1 {
		t.Errorf("the dry run ran nginx -t %d times, want exactly one against the staged tree", ex.Count()-beforeExec)
	}
	// It says what it found, and names the command that fixes it.
	if !hasWarning(res, "ragstack-ctl gateway repair") {
		t.Errorf("the incomplete publication is not reported: %v", res.Warnings)
	}
	var repairStep bool
	for _, s := range res.Steps {
		if s.Name == "repair" && strings.Contains(s.Detail, "ragstack-ctl gateway repair") {
			repairStep = true
		}
	}
	if !repairStep {
		t.Errorf("no repair step naming the remedy: %+v", res.Steps)
	}

	// And it still takes the lock: a dry run must not race a real publish.
	lock, err := st.lock()
	if err != nil {
		t.Fatal(err)
	}
	_, err = Publish(context.Background(), f, opts)
	lock.unlock()
	if !errors.Is(err, ErrLocked) {
		t.Errorf("a dry run ran while the gateway lock was held: %v", err)
	}
}

func hasWarning(res *Result, substr string) bool {
	for _, w := range res.Warnings {
		if strings.Contains(w, substr) {
			return true
		}
	}
	return false
}
