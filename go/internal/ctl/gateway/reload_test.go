package gateway

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// stepNames renders a result's steps in order, for a readable assertion.
func stepNames(res *Result) []string {
	out := make([]string, len(res.Steps))
	for i, s := range res.Steps {
		out[i] = s.Name
	}
	return out
}

func TestReloadTestsHUPsConfirmsAndProbesTheLiveGeneration(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, ex, sig, pr := testOpts(t, roots)
	published := publishOnce(t, f, opts, pr)

	execBefore, sigBefore := ex.Count(), sig.Count()
	res, err := Reload(context.Background(), opts)
	if err != nil {
		t.Fatalf("reload: %v (steps %+v)", err, res.Steps)
	}
	want := []string{"preconditions", "render", "nginx -t", "preflight", "reload", "confirm reload", "probe"}
	if got := strings.Join(stepNames(res), ","); got != strings.Join(want, ",") {
		t.Errorf("steps = %v, want %v", stepNames(res), want)
	}
	// The generation did not move, and neither did the pointer: a reload
	// publishes nothing.
	st := NewState(roots)
	if res.Generation != published.Generation || res.PreviousGeneration != published.Generation {
		t.Errorf("generation %d (previous %d), want both %d", res.Generation, res.PreviousGeneration, published.Generation)
	}
	if got := st.CurrentGeneration(); got != published.Generation {
		t.Errorf("current is gen-%d, want gen-%d", got, published.Generation)
	}
	if gens := st.Generations(); len(gens) != 1 {
		t.Errorf("generations on disk = %v, want only the published one", gens)
	}
	// And the durable record of the last PUBLICATION is untouched: a reload is
	// not a publication, and a txn.json saying otherwise would let a later
	// rollback skip its signal.
	txn, ok, err := st.ReadTxn()
	if err != nil || !ok {
		t.Fatalf("txn: %v (present %v)", err, ok)
	}
	if txn.State != TxnVerified || txn.Generation != published.Generation {
		t.Errorf("txn = %s of gen-%d, want the publish's verified record", txn.State, txn.Generation)
	}
	// It really did test, signal and confirm.
	if ex.Count() != execBefore+1 {
		t.Errorf("nginx -t ran %d times, want exactly one more", ex.Count()-execBefore)
	}
	if sig.Count() != sigBefore+1 {
		t.Errorf("signals = %d, want exactly one more SIGHUP", sig.Count()-sigBefore)
	}
	if pr.Count() == 0 {
		t.Error("nothing was probed")
	}
	if res.State != TxnReloaded {
		t.Errorf("state = %q, want %q", res.State, TxnReloaded)
	}
	// The staged copy is cleaned up.
	if res.StagedDir == "" {
		t.Fatal("no staged dir recorded")
	}
	if _, err := os.Stat(res.StagedDir); !os.IsNotExist(err) {
		t.Errorf("the staged tree survived at %s", res.StagedDir)
	}
}

func TestReloadDryRunStopsAfterNginxTestAndSignalsNothing(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, _, sig, pr := testOpts(t, roots)
	publishOnce(t, f, opts, pr)

	sigBefore := sig.Count()
	opts.DryRun = true
	res, err := Reload(context.Background(), opts)
	if err != nil {
		t.Fatalf("dry-run reload: %v", err)
	}
	if res.State != "dry-run" {
		t.Errorf("state = %q", res.State)
	}
	if sig.Count() != sigBefore {
		t.Errorf("a dry run sent %d signal(s)", sig.Count()-sigBefore)
	}
	for _, s := range res.Steps[3:] {
		if !strings.Contains(s.Detail, "--dry-run") {
			t.Errorf("step %q ran in a dry run: %s", s.Name, s.Detail)
		}
	}
}

// A dry run must be runnable by an account that may only LOOK: it tests a
// staged copy and never asks whether it owns the master.
func TestReloadDryRunDoesNotNeedToOwnTheMaster(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, _, sig, pr := testOpts(t, roots)
	publishOnce(t, f, opts, pr)

	sig.Info.UID = os.Getuid() + 1 // somebody else's nginx
	opts.DryRun = true
	if _, err := Reload(context.Background(), opts); err != nil {
		t.Fatalf("a dry run must not refuse over the master's owner: %v", err)
	}
}

func TestReloadRefusesWhenNothingIsPublished(t *testing.T) {
	roots := testRoots(t)
	opts, _, sig, _ := testOpts(t, roots)
	res, err := Reload(context.Background(), opts)
	if !errors.Is(err, ErrRefused) {
		t.Fatalf("reload with no generation = %v, want a refusal", err)
	}
	if !strings.Contains(err.Error(), "gateway apply") {
		t.Errorf("the refusal does not say what to do instead: %v", err)
	}
	if sig.Count() != 0 {
		t.Error("it signalled something anyway")
	}
	if res.State != TxnFailed {
		t.Errorf("state = %q", res.State)
	}
}

func TestReloadRefusesWhenTheStagedTreeDoesNotTest(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, ex, sig, pr := testOpts(t, roots)
	publishOnce(t, f, opts, pr)

	sigBefore := sig.Count()
	ex.Runner = func([]string) ([]byte, []byte, error) {
		return nil, []byte("nginx: [emerg] unknown directive \"proxy_pas\"\n"), errors.New("exit status 1")
	}
	res, err := Reload(context.Background(), opts)
	if err == nil {
		t.Fatal("a failing nginx -t must stop the reload")
	}
	if sig.Count() != sigBefore {
		t.Error("it HUPed the master after the configuration failed to test")
	}
	if !strings.Contains(res.ConfigTest, "proxy_pas") {
		t.Errorf("the config test output is not reported: %q", res.ConfigTest)
	}
}

// The reason this operation exists rather than a bare `kill -HUP`: nginx
// answers a SIGHUP it cannot use by logging [emerg], keeping the old
// configuration and carrying on. The worker set is what gives it away.
func TestReloadFailsWhenTheMasterRejectedTheConfiguration(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, _, sig, pr := testOpts(t, roots)
	publishOnce(t, f, opts, pr)

	sig.FrozenWorkers = true
	res, err := Reload(context.Background(), opts)
	if err == nil {
		t.Fatal("a master that kept its workers must fail the reload")
	}
	if !errors.Is(err, ErrRefused) {
		t.Errorf("error = %v, want a refusal", err)
	}
	last := res.Steps[len(res.Steps)-1]
	if last.Name != "confirm reload" || last.OK {
		t.Errorf("last step = %+v, want a failed confirmation", last)
	}
}

func TestReloadFailsWhenTheGatewayDoesNotAnswerTheGeneration(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, _, _, pr := testOpts(t, roots)
	publishOnce(t, f, opts, pr)

	// The route the generation advertises answers 404: the gateway does not
	// have it at all, which is the one answer that contradicts the generation.
	pr.Statuses["/ragstack/dev/api/health"] = 404
	res, err := Reload(context.Background(), opts)
	if err == nil {
		t.Fatal("a 404 on an advertised route must fail the probe")
	}
	if last := res.Steps[len(res.Steps)-1]; last.Name != "probe" || last.OK {
		t.Errorf("last step = %+v, want a failed probe", last)
	}
	// Nothing was published or reverted: the pointer is where it was.
	if got := NewState(roots).CurrentGeneration(); got != 1 {
		t.Errorf("current = gen-%d, want gen-1", got)
	}
}

// A reload tests the WHOLE tree, because the change it is reloading is a hand
// edit somewhere in it — not in the generated includes.
func TestReloadTestsTheHandEditedTree(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, ex, _, pr := testOpts(t, roots)
	publishOnce(t, f, opts, pr)

	marker := "# a hand edit in the coconut-proxy repo\n"
	path := filepath.Join(roots.ProxyDir, "snippets", "routes.conf")
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, append([]byte(marker), b...), 0o644); err != nil {
		t.Fatal(err)
	}
	var staged string
	ex.Runner = func(argv []string) ([]byte, []byte, error) {
		for i, a := range argv {
			if a == "-c" && i+1 < len(argv) {
				staged = filepath.Dir(argv[i+1])
			}
		}
		got, err := os.ReadFile(filepath.Join(staged, "snippets", "routes.conf"))
		if err != nil {
			return nil, nil, err
		}
		if !strings.HasPrefix(string(got), marker) {
			return nil, []byte("staged tree lacks the hand edit"), errors.New("exit status 1")
		}
		return nil, []byte("nginx: configuration file test is successful\n"), nil
	}
	if _, err := Reload(context.Background(), opts); err != nil {
		t.Fatalf("reload: %v", err)
	}
	if staged == "" {
		t.Fatal("nginx -t was never run against a staged tree")
	}
}

// ------------------------------------------------- the reload preconditions

// A reload makes the running master ADOPT whatever the live tree says. So it
// must first establish that the live tree is the generation the ctl published
// — an unfinished publication or a hand-edited include is exactly the state a
// reload would quietly bless.

func TestReloadRefusesWhileTheLastPublicationIsIncomplete(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, ex, sig, pr := testOpts(t, roots)
	published := publishOnce(t, f, opts, pr)

	// A publication that died between its switch and its verification: the
	// state names a step, and there is no finished_at.
	st := NewState(roots)
	txn, _, err := st.ReadTxn()
	if err != nil {
		t.Fatal(err)
	}
	txn.State, txn.FinishedAt = TxnSwitched, ""
	if err := st.WriteTxn(txn); err != nil {
		t.Fatal(err)
	}

	execBefore, sigBefore := ex.Count(), sig.Count()
	_, err = Reload(context.Background(), opts)
	if !errors.Is(err, ErrRefused) {
		t.Fatalf("reload over an incomplete txn = %v, want a refusal", err)
	}
	for _, want := range []string{"never finished", "gateway repair"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("the refusal does not mention %q: %v", want, err)
		}
	}
	if ex.Count() != execBefore || sig.Count() != sigBefore {
		t.Error("the refusal ran nginx -t or signalled the master; it must decide before either")
	}
	if got := st.CurrentGeneration(); got != published.Generation {
		t.Errorf("the refusal moved `current` to gen-%d", got)
	}
}

func TestReloadRefusesAnIncludeThatIsNotASymlinkIntoTheGeneration(t *testing.T) {
	for _, tc := range []struct {
		name string
		make func(t *testing.T, live string)
		want string
	}{
		{
			name: "a hand edit or bootstrap copy",
			make: func(t *testing.T, live string) {
				b, err := os.ReadFile(live)
				if err != nil {
					t.Fatal(err)
				}
				if err := os.Remove(live); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(live, b, 0o640); err != nil {
					t.Fatal(err)
				}
			},
			want: "regular file",
		},
		{
			name: "missing",
			make: func(t *testing.T, live string) {
				if err := os.Remove(live); err != nil {
					t.Fatal(err)
				}
			},
			want: "does not exist",
		},
		{
			name: "pointing somewhere else",
			make: func(t *testing.T, live string) {
				other := filepath.Join(t.TempDir(), "elsewhere.conf")
				if err := os.WriteFile(other, []byte("# not the ctl's\n"), 0o640); err != nil {
					t.Fatal(err)
				}
				if err := os.Remove(live); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(other, live); err != nil {
					t.Fatal(err)
				}
			},
			want: "not into the published generation",
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			roots := testRoots(t)
			f := registry.LiveFixture()
			opts, ex, sig, pr := testOpts(t, roots)
			publishOnce(t, f, opts, pr)

			live := filepath.Join(roots.ProxyDir, filepath.FromSlash(FileTenants))
			tc.make(t, live)

			execBefore, sigBefore := ex.Count(), sig.Count()
			_, err := Reload(context.Background(), opts)
			if !errors.Is(err, ErrRefused) {
				t.Fatalf("reload = %v, want a refusal", err)
			}
			if !strings.Contains(err.Error(), tc.want) {
				t.Errorf("the refusal does not say what it found (%q): %v", tc.want, err)
			}
			if !strings.Contains(err.Error(), live) {
				t.Errorf("the refusal does not name the path it looked at: %v", err)
			}
			if ex.Count() != execBefore || sig.Count() != sigBefore {
				t.Error("the refusal ran nginx -t or signalled the master")
			}
		})
	}
}

// TestReloadTestsTheLIVEIncludeBytesNotTheGenerations is the third half of
// finding 5: staging used to overwrite both includes with the generation's
// own bytes, so `nginx -t` validated a render and the result was reported as
// proof that the live tree loads.
func TestReloadTestsTheLIVEIncludeBytesNotTheGenerations(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, ex, sig, pr := testOpts(t, roots)
	published := publishOnce(t, f, opts, pr)

	// A marker written into the PUBLISHED generation file, which both include
	// symlinks resolve to. A staged tree carrying the generation's bytes and
	// one carrying the live bytes are the same file here — so the marker also
	// has to be absent from a tree staged the publish way, which is what the
	// second half asserts.
	genFile := filepath.Join(NewState(roots).GenDir(published.Generation), filepath.FromSlash(FileTenants))
	b, err := os.ReadFile(genFile)
	if err != nil {
		t.Fatal(err)
	}
	marker := "# live-edit-marker\n"
	if err := os.WriteFile(genFile, append([]byte(marker), b...), 0o640); err != nil {
		t.Fatal(err)
	}

	opts.KeepStage = true
	execBefore, sigBefore := ex.Count(), sig.Count()
	res, err := Reload(context.Background(), opts)
	if err != nil {
		t.Fatalf("reload: %v (steps %+v)", err, res.Steps)
	}
	if ex.Count() != execBefore+1 || sig.Count() != sigBefore+1 {
		t.Fatalf("reload ran %d tests and %d signals", ex.Count()-execBefore, sig.Count()-sigBefore)
	}
	defer os.RemoveAll(res.StagedDir)
	stagedBody, err := os.ReadFile(filepath.Join(res.StagedDir, filepath.FromSlash(FileTenants)))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(stagedBody), strings.TrimSpace(marker)) {
		t.Error("the staged include does not carry what the LIVE path serves; nginx -t tested something else")
	}
	// It is a real file in the staged tree, not a link back into the live one:
	// the path rewrite must not be able to reach the published generation.
	fi, err := os.Lstat(filepath.Join(res.StagedDir, filepath.FromSlash(FileTenants)))
	if err != nil {
		t.Fatal(err)
	}
	if fi.Mode()&os.ModeSymlink != 0 {
		t.Error("the staged include is a symlink into the live tree; a rewrite through it would edit the real generation")
	}
}
