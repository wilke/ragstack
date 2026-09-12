package gateway

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/doctor"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// liveProxy is the captured 2026-09-10 proxy tree; every test that needs a
// proxy directory works on a COPY of it in t.TempDir(). Nothing here ever
// looks at /rag/config/proxy.
const liveProxy = "../testdata/live-2026-09-10/proxy"

// testRoots lays out a throwaway /rag: a copy of the live proxy tree, an
// empty state dir, and a pidfile naming the fake nginx master.
func testRoots(t *testing.T) paths.Roots {
	t.Helper()
	root := t.TempDir()
	roots := paths.NewRoots(root, paths.Overrides{})
	if _, err := copyTree(liveProxy, roots.ProxyDir, nil); err != nil {
		t.Fatalf("copying the proxy fixture: %v", err)
	}
	// The fixture names the real /rag/config/proxy throughout. Repoint it at
	// the copy so the temporary tree is self-consistent — and so the staging
	// tests are actually testing the rewrite rather than passing because no
	// file ever mentioned the directory under test.
	if err := rewriteConfPaths(roots.ProxyDir, "/rag/config/proxy", roots.ProxyDir); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(roots.ProxyDir, "run", "logs"), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(roots.ProxyDir, "run", "nginx.pid"), []byte("4242\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(roots.CtlStateDir, 0o750); err != nil {
		t.Fatal(err)
	}
	return roots
}

// testOpts wires the three fakes and a probe budget short enough that a
// failing test finishes in milliseconds.
func testOpts(t *testing.T, roots paths.Roots) (Options, *FakeExec, *FakeSignaller, *FakeProber) {
	t.Helper()
	ex, sig := &FakeExec{}, NewFakeSignaller(os.Getuid())
	pr := &FakeProber{Bodies: map[string][]byte{}, Statuses: map[string]int{}}
	return Options{
		Roots: roots, Exec: ex, Sig: sig, Prober: pr,
		By:            "wilke",
		StageParent:   t.TempDir(),
		ProbeTimeout:  50 * time.Millisecond,
		ProbeInterval: time.Millisecond,
	}, ex, sig, pr
}

// answerDesiredState fills the prober's table so the gateway "serves" exactly
// what the generation advertises.
func answerDesiredState(t *testing.T, pr *FakeProber, gen *Generation, f *registry.Fleet) {
	t.Helper()
	names, err := expectedNames(gen)
	if err != nil {
		t.Fatal(err)
	}
	var entries []string
	for _, n := range names {
		entries = append(entries, fmt.Sprintf(`{"name":%q,"api":"/ragstack/%s/api/v1/...","ui":"/ragstack/%s/ui/"}`, n, n, n))
	}
	pr.Bodies["/ragstack/tenants"] = []byte(`{"tenants":[` + strings.Join(entries, ",") + `]}` + "\n")
	for _, n := range names {
		p := "/ragstack/" + n + "/api/health"
		if t, ok := f.Tenants[n]; ok && t.State == "active" {
			pr.Bodies[p], pr.Statuses[p] = []byte(`{"status":"ok"}`), 200
			continue
		}
		pr.Statuses[p] = 502
	}
}

func publishOnce(t *testing.T, f *registry.Fleet, opts Options, pr *FakeProber) *Result {
	t.Helper()
	gen, err := Render(f, opts.Roots)
	if err != nil {
		t.Fatal(err)
	}
	answerDesiredState(t, pr, gen, f)
	res, err := Publish(context.Background(), f, opts)
	if err != nil {
		t.Fatalf("publish: %v (steps %+v)", err, res.Steps)
	}
	return res
}

// ---------------------------------------------------------------- rendering

func TestRenderCarriesProvenanceAndBothFiles(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	f.Generation = 12
	gen, err := Render(f, roots)
	if err != nil {
		t.Fatal(err)
	}
	if gen.N != 1 {
		t.Errorf("first generation = %d, want 1", gen.N)
	}
	if len(gen.Files) != 2 {
		t.Fatalf("files = %v, want exactly the two includes", sortedKeys(gen.Files))
	}
	for _, rel := range RelPaths() {
		b, ok := gen.Files[rel]
		if !ok {
			t.Fatalf("%s missing", rel)
		}
		head := string(b[:len(b)-len(StripHeader(b))])
		for _, want := range []string{
			"generator:", "ragstack-ctl", "registry generation: 12", "registry sha256:     sha256:", "generated at:",
		} {
			if !strings.Contains(head, want) {
				t.Errorf("%s header lacks %q:\n%s", rel, want, head)
			}
		}
	}
	// The header must not survive into a comparison, or pending_diff could
	// never be false: the timestamp alone would make every render differ.
	again, err := Render(f, roots)
	if err != nil {
		t.Fatal(err)
	}
	if again.SHA256() != gen.SHA256() {
		t.Error("two renders of one registry have different digests")
	}
}

// The PR-B go/no-go: the first generated file must route exactly what the
// hand-written maps route today.
func TestRenderedGenerationIsASemanticNoop(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	gen, err := Render(f, roots)
	if err != nil {
		t.Fatal(err)
	}
	liveMaps := filepath.Join(liveProxy, "conf.d", "00-maps.conf")
	b, err := os.ReadFile(liveMaps)
	if err != nil {
		t.Fatal(err)
	}
	live := doctor.ParseTenantMaps(b, liveMaps)
	// The two JSON lists live in routes.conf until the generated include takes
	// over, so the live half of the comparison is assembled from both files.
	names, tenants, err := liveTenantLists(filepath.Join(liveProxy, "snippets", "routes.conf"))
	if err != nil {
		t.Fatal(err)
	}
	live.NamesJSON, live.TenantsJSON = names, tenants
	rendered := doctor.ParseTenantMaps(gen.Files[FileTenants], "rendered")
	if ok, notes := semanticEqual(live, rendered); !ok {
		t.Errorf("rendered maps differ from the live ones:\n  %s", strings.Join(notes, "\n  "))
	}
	// And the same verdict through the public entry point.
	d, err := DiffDetail(roots, f)
	if err != nil {
		t.Fatal(err)
	}
	if !d.SemanticNoop {
		t.Errorf("DiffDetail says not a no-op: %v", d.Notes)
	}
	if !d.Response.Changed {
		t.Error("with nothing published the diff must still be a whole-file addition")
	}

	// $tenants_json must equal the literal the live routes.conf serves.
	routes, err := os.ReadFile(filepath.Join(liveProxy, "snippets", "routes.conf"))
	if err != nil {
		t.Fatal(err)
	}
	lit := regexp.MustCompile(`location = /ragstack/tenants \{[^}]*return 200 '(\{"tenants":\[.*\]\})\\n';`).FindSubmatch(routes)
	if lit == nil {
		t.Fatal("routes.conf tenants literal not found")
	}
	got := regexp.MustCompile(`map \$host \$tenants_json \{\n    default  '(\[.*\])';`).FindSubmatch(gen.Files[FileTenants])
	if got == nil {
		t.Fatalf("no $tenants_json in the rendered file:\n%s", gen.Files[FileTenants])
	}
	if `{"tenants":`+string(got[1])+`}` != string(lit[1]) {
		t.Errorf("$tenants_json:\n got %s\nwant %s", got[1], lit[1])
	}
}

// ---------------------------------------------------------------- publishing

func TestPublishHappyPath(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, ex, sig, pr := testOpts(t, roots)
	bodies, err := LoadExpectBodies("../testdata/live-2026-09-10/gateway")
	if err != nil {
		t.Fatal(err)
	}
	opts.ExpectBodies = bodies

	gen, err := Render(f, roots)
	if err != nil {
		t.Fatal(err)
	}
	answerDesiredState(t, pr, gen, f)
	for p, b := range bodies {
		pr.Bodies[p] = b
	}
	res, err := Publish(context.Background(), f, opts)
	if err != nil {
		t.Fatalf("publish: %v\nsteps: %+v", err, res.Steps)
	}
	if res.State != TxnVerified || res.Generation != 1 {
		t.Fatalf("state %q generation %d, want verified/1", res.State, res.Generation)
	}
	if ex.Count() != 1 {
		t.Errorf("nginx -t ran %d times, want 1", ex.Count())
	}
	if sig.Count() != 1 {
		t.Errorf("%d signals, want exactly one HUP", sig.Count())
	}
	if res.NginxPID != 4242 {
		t.Errorf("nginx pid %d, want the one in the pidfile", res.NginxPID)
	}

	st := NewState(roots)
	if st.CurrentGeneration() != 1 {
		t.Fatalf("current points at %d", st.CurrentGeneration())
	}
	// Both include paths must be symlinks THROUGH current, and resolve to the
	// generation's bytes.
	for _, rel := range RelPaths() {
		live := filepath.Join(roots.ProxyDir, rel)
		fi, err := os.Lstat(live)
		if err != nil {
			t.Fatalf("%s: %v", rel, err)
		}
		if fi.Mode()&os.ModeSymlink == 0 {
			t.Fatalf("%s is not a symlink", rel)
		}
		target, _ := os.Readlink(live)
		if want := filepath.Join(st.CurrentLink(), rel); target != want {
			t.Errorf("%s -> %s, want %s", rel, target, want)
		}
		got, err := os.ReadFile(live) // resolves current -> gen-1
		if err != nil {
			t.Fatalf("%s does not resolve: %v", rel, err)
		}
		if string(got) != string(gen.Files[rel]) {
			t.Errorf("%s resolves to different bytes than were rendered", rel)
		}
	}
	// manifest.json records a digest per file.
	m, err := readManifest(st, 1)
	if err != nil {
		t.Fatal(err)
	}
	for _, rel := range RelPaths() {
		if !strings.HasPrefix(m.Files[rel], "sha256:") || len(m.Files[rel]) != 71 {
			t.Errorf("manifest digest for %s = %q", rel, m.Files[rel])
		}
	}
	// The staged copy is cleaned up.
	if _, err := os.Stat(res.StagedDir); !errors.Is(err, os.ErrNotExist) {
		t.Errorf("staged dir %s survived the publish", res.StagedDir)
	}

	// Status right after a publish: complete, nothing pending.
	status, err := StatusWith(roots, f, Options{Sig: sig})
	if err != nil {
		t.Fatal(err)
	}
	if status.Generation != 1 || status.TxnState != TxnStateComplete {
		t.Errorf("status generation %d txn_state %q", status.Generation, status.TxnState)
	}
	if status.PendingDiff {
		t.Error("pending_diff is true immediately after a publish")
	}
	if status.Nginx.MasterPID == nil || *status.Nginx.MasterPID != 4242 {
		t.Errorf("master pid = %v", status.Nginx.MasterPID)
	}
	if status.Nginx.ConfigOK == nil || !*status.Nginx.ConfigOK {
		t.Error("config_ok not recorded from the staged nginx -t")
	}
	if status.Nginx.LastReloadAt == nil {
		t.Error("last_reload_at not recorded")
	}
	if status.RegistryGeneration != f.Generation {
		t.Errorf("registry_generation %d, want the published %d", status.RegistryGeneration, f.Generation)
	}

	// A registry change that moves a ROUTE makes the diff pending again.
	// A bump that does not — see
	// TestARegistryBumpWithIdenticalRoutingIsNotAPendingDiff — deliberately
	// does not.
	f.Tenants["dev"].Ports.API = 24049
	f.Generation++
	status2, err := StatusWith(roots, f, Options{Sig: sig})
	if err != nil {
		t.Fatal(err)
	}
	if !status2.PendingDiff {
		t.Error("pending_diff is false after a port move")
	}
	f.Tenants["dev"].State = "stopped"
	if got := RouteStatus(f.Tenants["dev"].State); got != "maintenance" {
		t.Errorf("a stopped tenant's route status = %q", got)
	}
}

func TestPublishDryRunWritesNothing(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, ex, sig, _ := testOpts(t, roots)
	opts.DryRun = true
	res, err := Publish(context.Background(), f, opts)
	if err != nil {
		t.Fatalf("dry run: %v", err)
	}
	if ex.Count() != 1 {
		t.Errorf("dry run ran nginx -t %d times", ex.Count())
	}
	if sig.Count() != 0 {
		t.Error("a dry run signalled the master")
	}
	st := NewState(roots)
	if st.CurrentGeneration() != 0 || len(st.Generations()) != 0 {
		t.Error("a dry run wrote a generation")
	}
	if _, ok, _ := st.ReadTxn(); ok {
		t.Error("a dry run wrote txn.json")
	}
	for _, rel := range RelPaths() {
		if _, err := os.Lstat(filepath.Join(roots.ProxyDir, rel)); !errors.Is(err, os.ErrNotExist) {
			t.Errorf("a dry run created %s in the proxy tree", rel)
		}
	}
	if res.State != "dry-run" {
		t.Errorf("state %q", res.State)
	}
}

func TestPublishRefusesWhenNginxTestFails(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, ex, sig, _ := testOpts(t, roots)
	ex.Runner = func([]string) ([]byte, []byte, error) {
		return nil, []byte(`nginx: [emerg] duplicate variable name "tenant_api" in /staged/conf.d/05-tenants.generated.conf:9`),
			errors.New("exit status 1")
	}
	res, err := Publish(context.Background(), f, opts)
	if err == nil {
		t.Fatal("publish succeeded with a failing nginx -t")
	}
	if !strings.Contains(res.ConfigTest, "duplicate variable name") {
		t.Errorf("the nginx -t output is not reported: %q", res.ConfigTest)
	}
	if sig.Count() != 0 {
		t.Error("the master was signalled although the config test failed")
	}
	st := NewState(roots)
	if st.CurrentGeneration() != 0 {
		t.Error("current moved although the config test failed")
	}
	for _, rel := range RelPaths() {
		if _, err := os.Lstat(filepath.Join(roots.ProxyDir, rel)); !errors.Is(err, os.ErrNotExist) {
			t.Errorf("%s was published although the config test failed", rel)
		}
	}
	// The generation dir is KEPT so the operator can look at what failed.
	if _, err := os.Stat(st.GenDir(1)); err != nil {
		t.Errorf("gen-1 was removed: %v", err)
	}
	txn, ok, err := st.ReadTxn()
	if err != nil || !ok {
		t.Fatalf("txn: %v %v", ok, err)
	}
	if txn.State != TxnFailed || !txn.Complete() {
		t.Errorf("txn = %+v, want a finished `failed`", txn)
	}
	if txn.ConfigOK == nil || *txn.ConfigOK {
		t.Error("config_ok must be recorded false")
	}
}

func TestProbeFailureAfterSwitchRevertsAndReloadsTwice(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, _, sig, pr := testOpts(t, roots)
	publishOnce(t, f, opts, pr)
	if sig.Count() != 1 {
		t.Fatalf("first publish sent %d signals", sig.Count())
	}

	// Second generation, and a gateway that keeps answering the OLD list.
	f.Tenants["demo"].State = "stopped"
	f.Generation++
	gen2, err := Render(f, roots)
	if err != nil {
		t.Fatal(err)
	}
	res, err := Publish(context.Background(), f, opts)
	if err == nil {
		t.Fatal("publish succeeded although the probe never matched")
	}
	if !res.Reverted || res.State != TxnReverted {
		t.Fatalf("result %+v, want a revert", res)
	}
	st := NewState(roots)
	if st.CurrentGeneration() != 1 {
		t.Errorf("current = gen-%d after the revert, want gen-1", st.CurrentGeneration())
	}
	if sig.Count() != 3 {
		t.Errorf("%d signals, want three (publish 1, publish 2, revert)", sig.Count())
	}
	txn, _, err := st.ReadTxn()
	if err != nil {
		t.Fatal(err)
	}
	if txn.State != TxnReverted || !txn.Complete() || txn.Generation != gen2.N {
		t.Errorf("txn = %+v", txn)
	}
	// The include symlinks still resolve — through current, now gen-1.
	for _, rel := range RelPaths() {
		if _, err := os.ReadFile(filepath.Join(roots.ProxyDir, rel)); err != nil {
			t.Errorf("%s does not resolve after the revert: %v", rel, err)
		}
	}
}

// A FIRST publish that fails after the switch must still leave a proxy tree
// nginx can load.
//
// The proxy configuration includes both generated paths unconditionally —
// snippets/routes.conf includes the static snippet, conf.d/10-gateway.conf
// reads $tenant_api — so "undo" cannot mean "remove". The revert used to
// remove the symlinks it created and delete `current`, which left the deployed
// tree with a missing include (absent-before) or a dangling symlink (a
// bootstrap copy it had adopted). Either one is a gateway that refuses to
// start at its next reload, produced by a publish whose only fault was a
// failing probe.
//
// Both variants are checked with os.Stat, which FOLLOWS links: the assertion is
// "this path resolves to bytes", not "something exists here".
func TestFirstPublishProbeFailureLeavesLoadableIncludes(t *testing.T) {
	t.Run("absent before the publish", func(t *testing.T) {
		roots := testRoots(t)
		f := registry.LiveFixture()
		opts, _, _, pr := testOpts(t, roots)
		gen, err := Render(f, roots)
		if err != nil {
			t.Fatal(err)
		}
		for _, rel := range RelPaths() {
			if _, err := os.Lstat(filepath.Join(roots.ProxyDir, rel)); !errors.Is(err, os.ErrNotExist) {
				t.Fatalf("the fixture already has %s; this variant needs it absent", rel)
			}
		}
		pr.Statuses["/ragstack/tenants"] = 500
		res, err := Publish(context.Background(), f, opts)
		if err == nil {
			t.Fatal("publish succeeded with a failing probe")
		}
		if !res.Reverted {
			t.Fatal("no revert recorded")
		}
		for _, rel := range RelPaths() {
			live := filepath.Join(roots.ProxyDir, rel)
			fi, err := os.Stat(live) // follows links: a dangling one fails here
			if err != nil {
				t.Fatalf("%s does not resolve after the revert: %v", rel, err)
			}
			if !fi.Mode().IsRegular() {
				t.Errorf("%s is not a regular file (%s)", rel, fi.Mode())
			}
			b, err := os.ReadFile(live)
			if err != nil {
				t.Fatal(err)
			}
			if string(b) != string(gen.Files[rel]) {
				t.Errorf("%s does not hold the generation's bytes", rel)
			}
		}
		var said bool
		for _, w := range res.Warnings {
			if strings.Contains(w, "REGULAR file so the configuration still loads") {
				said = true
			}
		}
		if !said {
			t.Errorf("the revert did not say it had left a loadable copy behind: %v", res.Warnings)
		}
		// `current` is gone — nothing resolves through it any more, and leaving
		// it would make Status report an unverified generation as serving.
		if NewState(roots).CurrentGeneration() != 0 {
			t.Error("current survived a reverted first publish")
		}
	})

	t.Run("the coconut-proxy bootstrap copy", func(t *testing.T) {
		roots := testRoots(t)
		f := registry.LiveFixture()
		opts, _, _, pr := testOpts(t, roots)
		gen, err := Render(f, roots)
		if err != nil {
			t.Fatal(err)
		}
		// deploy.sh's committed bootstrap: the same routing, an older header.
		bootstrap := map[string][]byte{}
		for _, rel := range RelPaths() {
			bootstrap[rel] = append([]byte(headerBegin+"\n# generated at:        2001-01-01T00:00:00Z\n"+headerEnd+"\n"),
				StripHeader(gen.Files[rel])...)
			p := filepath.Join(roots.ProxyDir, rel)
			if err := os.MkdirAll(filepath.Dir(p), 0o750); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(p, bootstrap[rel], 0o644); err != nil {
				t.Fatal(err)
			}
		}
		pr.Statuses["/ragstack/tenants"] = 500
		res, err := Publish(context.Background(), f, opts)
		if err == nil {
			t.Fatal("publish succeeded with a failing probe")
		}
		if !res.Reverted {
			t.Fatal("no revert recorded")
		}
		for _, rel := range RelPaths() {
			live := filepath.Join(roots.ProxyDir, rel)
			fi, err := os.Stat(live)
			if err != nil {
				t.Fatalf("%s does not resolve after the revert: %v", rel, err)
			}
			if !fi.Mode().IsRegular() {
				t.Errorf("%s is not a regular file again (%s)", rel, fi.Mode())
			}
			b, err := os.ReadFile(live)
			if err != nil {
				t.Fatal(err)
			}
			if string(b) != string(bootstrap[rel]) {
				t.Errorf("%s is not the bootstrap copy that was there before the publish", rel)
			}
			if fi.Mode().Perm() != 0o644 {
				t.Errorf("%s mode = %s, want the 0644 it had", rel, fi.Mode().Perm())
			}
		}
	})
}

func TestSwitchRefusesAHandEditedRegularFile(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, _, sig, pr := testOpts(t, roots)
	hand := filepath.Join(roots.ProxyDir, FileStatic)
	if err := os.WriteFile(hand, []byte("# someone's hand edit\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	gen, _ := Render(f, roots)
	answerDesiredState(t, pr, gen, f)
	res, err := Publish(context.Background(), f, opts)
	if !errors.Is(err, ErrRefused) {
		t.Fatalf("err = %v, want a refusal", err)
	}
	if !strings.Contains(err.Error(), "hand edit") {
		t.Errorf("the refusal does not explain itself: %v", err)
	}
	if sig.Count() != 0 {
		t.Error("the master was signalled although the switch was refused")
	}
	b, _ := os.ReadFile(hand)
	if string(b) != "# someone's hand edit\n" {
		t.Error("the hand-edited file was overwritten")
	}
	if res.State != TxnFailed {
		t.Errorf("state %q", res.State)
	}
}

// The coconut-proxy repo ships a committed BOOTSTRAP copy of each generated
// include, so a freshly deployed proxy tree loads before the ctl has ever
// published. deploy.sh writes it as a regular file; the first publish must
// take it over rather than refuse it as a hand edit.
func TestSwitchAdoptsTheCoconutProxyBootstrapCopy(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, _, _, pr := testOpts(t, roots)
	gen, err := Render(f, roots)
	if err != nil {
		t.Fatal(err)
	}
	// A bootstrap copy is the same configuration under a DIFFERENT provenance
	// header — rendered by another binary, at another moment.
	for _, rel := range RelPaths() {
		bootstrap := append([]byte(headerBegin+"\n# generated at:        2001-01-01T00:00:00Z\n"+headerEnd+"\n"),
			StripHeader(gen.Files[rel])...)
		p := filepath.Join(roots.ProxyDir, rel)
		if err := os.MkdirAll(filepath.Dir(p), 0o750); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, bootstrap, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	answerDesiredState(t, pr, gen, f)
	res, err := Publish(context.Background(), f, opts)
	if err != nil {
		t.Fatalf("publish over the bootstrap copies: %v (%+v)", err, res.Steps)
	}
	for _, rel := range RelPaths() {
		fi, err := os.Lstat(filepath.Join(roots.ProxyDir, rel))
		if err != nil || fi.Mode()&os.ModeSymlink == 0 {
			t.Errorf("%s is still a regular file", rel)
		}
	}
	var adopted int
	for _, w := range res.Warnings {
		if strings.Contains(w, "bootstrap copy") {
			adopted++
		}
	}
	if adopted != 2 {
		t.Errorf("warnings do not report both adopted bootstrap copies: %v", res.Warnings)
	}
}

// The coconut-proxy bootstrap copy is rendered once and committed; the
// registry keeps moving. Its body opens with
// `# … from registry generation N. DO NOT EDIT.`, so the moment anything bumps
// the registry generation — a tenant edited elsewhere, a second `registry
// save` — that line differs from the render being published even when the
// ROUTING is identical to the byte.
//
// Comparing the bodies as-is therefore turned the shipped bootstrap into a
// refused "hand edit" on a host that had done nothing wrong, and left
// `pending_diff` true forever with nothing to publish.
func TestSwitchAdoptsABootstrapCopyRenderedFromAnEarlierRegistryGeneration(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	f.Generation = 11
	opts, _, _, pr := testOpts(t, roots)

	// deploy.sh's committed copy: generation 11.
	older, err := Render(f, roots)
	if err != nil {
		t.Fatal(err)
	}
	for _, rel := range RelPaths() {
		p := filepath.Join(roots.ProxyDir, rel)
		if err := os.MkdirAll(filepath.Dir(p), 0o750); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, older.Files[rel], 0o644); err != nil {
			t.Fatal(err)
		}
	}
	// The registry has moved on without changing a single route.
	f.Generation = 14
	newer, err := Render(f, roots)
	if err != nil {
		t.Fatal(err)
	}
	if string(older.Files[FileTenants]) == string(newer.Files[FileTenants]) {
		t.Fatal("the two renders are byte-identical, so this test proves nothing")
	}
	if older.SHA256() != newer.SHA256() {
		t.Errorf("two renders of identical routing have different digests:\n%s\n%s", older.SHA256(), newer.SHA256())
	}

	// pending_diff must be FALSE: there is nothing to publish.
	d, err := DiffDetail(roots, f)
	if err != nil {
		t.Fatal(err)
	}
	if !d.SemanticNoop {
		t.Errorf("a registry bump with identical routing is not a no-op: %v", d.Notes)
	}

	answerDesiredState(t, pr, newer, f)
	res, err := Publish(context.Background(), f, opts)
	if err != nil {
		t.Fatalf("publish over a bootstrap copy from an earlier registry generation: %v (%+v)", err, res.Steps)
	}
	for _, rel := range RelPaths() {
		fi, err := os.Lstat(filepath.Join(roots.ProxyDir, rel))
		if err != nil || fi.Mode()&os.ModeSymlink == 0 {
			t.Errorf("%s was not adopted (%v)", rel, err)
		}
	}

	// And once published, a further registry bump that changes no route leaves
	// pending_diff false.
	f.Generation = 15
	status, err := StatusWith(roots, f, Options{Sig: NewFakeSignaller(os.Getuid())})
	if err != nil {
		t.Fatal(err)
	}
	if status.PendingDiff {
		d, _ := DiffDetail(roots, f)
		t.Errorf("pending_diff is true after a registry bump with identical routing: %+v", d.Response.Files)
	}
}

// bootstrapFixture is the coconut-proxy repo's own committed pair, byte for
// byte. See its README: the point is to test against the files that SHIP, not
// against a render made inside the test.
const bootstrapFixture = "../testdata/coconut-proxy-bootstrap"

// The real thing: the two files `coconut-proxy` deploys, adopted by a publish
// whose registry has moved on since they were rendered.
//
// They were regenerated from the current renderer at commit 84fd604, so their
// ROUTING is what the ctl renders today — and their first line still says
// `from registry generation 1`, which the registry on a live host passes within
// a day. Comparing the bodies as-is made that line the difference, and the
// first apply then refused the shipped bootstrap as a hand edit.
func TestSwitchAdoptsTheShippedCoconutProxyBootstrap(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	// The shipped files were rendered from the ADOPTED registry on coconut,
	// whose legacy rows are stored asm-then-lucid; LiveFixture lists them the
	// other way round and the renderer emits them in slice order. Line them up,
	// or this test fails on a difference that has nothing to do with adoption.
	f.LegacyRoutes = []registry.LegacyRoute{f.LegacyRoutes[1], f.LegacyRoutes[0]}
	f.Generation = 23 // the host has saved the registry a few times since the deploy
	opts, _, _, pr := testOpts(t, roots)

	for _, rel := range RelPaths() {
		b, err := os.ReadFile(filepath.Join(bootstrapFixture, filepath.FromSlash(rel)))
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(string(b), "from registry generation 1.") {
			t.Fatalf("%s no longer carries the provenance line this test is about:\n%s", rel, b)
		}
		p := filepath.Join(roots.ProxyDir, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(p), 0o750); err != nil {
			t.Fatal(err)
		}
		// deploy.sh writes them as regular files, 0644.
		if err := os.WriteFile(p, b, 0o644); err != nil {
			t.Fatal(err)
		}
	}

	// Before anything is published, the shipped copies must already be a
	// semantic no-op against the render — that is what makes the first apply
	// safe to run at all.
	gen, err := Render(f, roots)
	if err != nil {
		t.Fatal(err)
	}
	for _, rel := range RelPaths() {
		shipped, err := os.ReadFile(filepath.Join(bootstrapFixture, filepath.FromSlash(rel)))
		if err != nil {
			t.Fatal(err)
		}
		if got, want := string(StripHeader(shipped)), string(StripHeader(gen.Files[rel])); got != want {
			t.Errorf("the shipped %s is not today's render once the provenance line is off:\n got %q\nwant %q", rel, got, want)
		}
	}

	answerDesiredState(t, pr, gen, f)
	res, err := Publish(context.Background(), f, opts)
	if err != nil {
		t.Fatalf("publishing over the shipped bootstrap copies: %v (%+v)", err, res.Steps)
	}
	var adopted int
	for _, w := range res.Warnings {
		if strings.Contains(w, "bootstrap copy") {
			adopted++
		}
	}
	if adopted != 2 {
		t.Errorf("both shipped copies should have been adopted: %v", res.Warnings)
	}
	for _, rel := range RelPaths() {
		live := filepath.Join(roots.ProxyDir, rel)
		fi, err := os.Lstat(live)
		if err != nil || fi.Mode()&os.ModeSymlink == 0 {
			t.Errorf("%s is still a regular file (%v)", rel, err)
		}
		if _, err := os.ReadFile(live); err != nil {
			t.Errorf("%s does not resolve: %v", rel, err)
		}
	}
	// And nothing is pending afterwards.
	status, err := StatusWith(roots, f, Options{Sig: NewFakeSignaller(os.Getuid())})
	if err != nil {
		t.Fatal(err)
	}
	if status.PendingDiff {
		t.Error("pending_diff is true immediately after adopting the shipped bootstrap")
	}
}

func TestReloadRefusesAMasterOwnedByAnotherAccount(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, _, sig, pr := testOpts(t, roots)
	sig.Info.UID = os.Getuid() + 1000
	gen, _ := Render(f, roots)
	answerDesiredState(t, pr, gen, f)
	_, err := Publish(context.Background(), f, opts)
	if !errors.Is(err, ErrRefused) {
		t.Fatalf("err = %v, want a refusal", err)
	}
	if !strings.Contains(err.Error(), "run as that account") {
		t.Errorf("the refusal does not name the remedy: %v", err)
	}
	if sig.Count() != 0 {
		t.Error("a signal was sent to a master owned by someone else")
	}
	// The preflight refuses BEFORE the switch, so nothing moved at all.
	if NewState(roots).CurrentGeneration() != 0 {
		t.Error("current moved although the master is not ours")
	}
}

func TestReloadRefusesAStalePidfile(t *testing.T) {
	roots := testRoots(t)
	opts, _, sig, _ := testOpts(t, roots)
	opts.defaults()
	sig.Info = ProcInfo{Exists: true, Comm: "python3", UID: os.Getuid()}
	if _, err := reload(opts); !errors.Is(err, ErrRefused) {
		t.Fatalf("err = %v, want a refusal", err)
	}
	sig.Info = ProcInfo{}
	if _, err := reload(opts); !errors.Is(err, ErrRefused) {
		t.Fatalf("err = %v, want a refusal for a dead pid", err)
	}
}

// ---------------------------------------------------------------- staging

// fixtureWithIncludingSnippet is the fleet whose render actually NAMES the
// proxy tree.
//
// A static-UI tenant and an enabled admin mount are what make
// tenants-ui-static.generated.conf emit `include <ProxyDir>/snippets/cors.conf`
// and `…/proxy-common.conf`. With the plain LiveFixture — every tenant
// `external`, `gateway_enabled: false` — that file is a header and nothing
// else, so a staging bug that left those includes pointing at the LIVE tree
// was invisible to every test.
func fixtureWithIncludingSnippet() *registry.Fleet {
	f := registry.LiveFixture()
	f.Ctl.GatewayEnabled = true
	f.Tenants["demo"].UI.Mode = registry.UIModeStatic
	return f
}

func TestStageIsolatesTheProxyTree(t *testing.T) {
	roots := testRoots(t)
	f := fixtureWithIncludingSnippet()
	opts, _, _, _ := testOpts(t, roots)
	opts.defaults()
	gen, err := Render(f, roots)
	if err != nil {
		t.Fatal(err)
	}
	// Guard the guard: if the render stopped naming the proxy dir, the walk
	// below would pass for the wrong reason.
	if !strings.Contains(string(gen.Files[FileStatic]), roots.ProxyDir) {
		t.Fatalf("the rendered %s names no path in %s, so this test proves nothing:\n%s",
			FileStatic, roots.ProxyDir, gen.Files[FileStatic])
	}
	staged, _, err := stage(opts, gen)
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(staged)

	// No staged .conf may still name the real proxy dir, or the test would
	// read the LIVE files under a staged name.
	err = filepath.Walk(staged, func(p string, fi os.FileInfo, err error) error {
		if err != nil || fi.IsDir() || filepath.Ext(p) != ".conf" {
			return err
		}
		b, err := os.ReadFile(p)
		if err != nil {
			return err
		}
		if strings.Contains(string(b), roots.ProxyDir) {
			t.Errorf("%s still references %s", p, roots.ProxyDir)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if b, err := os.ReadFile(filepath.Join(staged, "nginx.conf")); err != nil || !strings.Contains(string(b), staged) {
		t.Errorf("the staged nginx.conf does not include the staged conf.d: %v", err)
	}
	for _, rel := range RelPaths() {
		b, err := os.ReadFile(filepath.Join(staged, rel))
		if err != nil {
			t.Fatalf("staged %s: %v", rel, err)
		}
		// The generation's bytes with every reference to the live tree pointed
		// at the staging dir — the same rewrite every other .conf gets, which
		// is only true because the generation is written BEFORE the rewrite
		// runs.
		want := strings.ReplaceAll(string(gen.Files[rel]), roots.ProxyDir, staged)
		if string(b) != want {
			t.Errorf("staged %s is not the generation's bytes rewritten for the staging dir:\n got %s\nwant %s", rel, b, want)
		}
	}
	if _, err := os.Stat(filepath.Join(staged, "run", "logs")); err != nil {
		t.Errorf("the staged tree has no run/logs for nginx -t to name: %v", err)
	}
	if _, err := os.Stat(filepath.Join(staged, "run", "nginx.pid")); !errors.Is(err, os.ErrNotExist) {
		t.Error("run/ was copied into the staged tree")
	}
	// And the argv is the documented one.
	argv := []string{
		opts.Apptainer, "exec", "-B", roots.RagRoot, "-B", staged + ":" + staged,
		opts.NginxSIF, "nginx", "-t", "-c", filepath.Join(staged, "nginx.conf"),
	}
	if _, err := nginxTest(context.Background(), opts, staged); err != nil {
		t.Fatal(err)
	}
	got := opts.Exec.(*FakeExec).Calls[0]
	if strings.Join(got, " ") != strings.Join(argv, " ") {
		t.Errorf("argv:\n got %v\nwant %v", got, argv)
	}
}

// Staging a CHECKOUT of the coconut-proxy repo (testing a branch before it is
// deployed) is the same flow with two twists: the config files name
// /rag/config/proxy, which is not the directory being staged, and a checkout
// has no tls/ of its own.
func TestStageACheckoutOfTheProxyRepo(t *testing.T) {
	roots := testRoots(t) // the deployed tree, with the canonical path
	if err := os.MkdirAll(filepath.Join(roots.ProxyDir, "tls"), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(roots.ProxyDir, "tls", "proxy.crt"), []byte("cert\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	// A checkout is a copy of the tree that still NAMES the deployed one.
	checkout := filepath.Join(t.TempDir(), "coconut-proxy")
	if _, err := copyTree(roots.ProxyDir, checkout, map[string]bool{"run": true, "tls": true}); err != nil {
		t.Fatal(err)
	}

	f := registry.LiveFixture()
	opts, _, _, _ := testOpts(t, roots)
	opts.Roots = paths.NewRoots(roots.RagRoot, paths.Overrides{ProxyDir: checkout})
	opts.defaults()
	gen, err := Render(f, opts.Roots)
	if err != nil {
		t.Fatal(err)
	}
	staged, warns, err := stage(opts, gen)
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(staged)

	b, err := os.ReadFile(filepath.Join(staged, "nginx.conf"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(b), roots.ProxyDir) {
		t.Errorf("the staged nginx.conf still includes the DEPLOYED tree:\n%s", b)
	}
	if !strings.Contains(string(b), staged) {
		t.Errorf("the staged nginx.conf does not name the staging dir:\n%s", b)
	}
	if _, err := os.Stat(filepath.Join(staged, "tls", "proxy.crt")); err != nil {
		t.Errorf("tls/ was not borrowed from the deployed tree: %v", err)
	}
	var said bool
	for _, w := range warns {
		if strings.Contains(w, "no tls/ of its own") {
			said = true
		}
	}
	if !said {
		t.Errorf("borrowing tls/ was not reported: %v", warns)
	}
}

// A key this account cannot read must not turn `nginx -t` into a test of file
// permissions: the staged copy gets a throwaway pair and says so.
func TestStageSubstitutesAnUnreadableTLSKey(t *testing.T) {
	roots := testRoots(t)
	tlsDir := filepath.Join(roots.ProxyDir, "tls")
	if err := os.MkdirAll(tlsDir, 0o750); err != nil {
		t.Fatal(err)
	}
	real := []byte("-----BEGIN EC PRIVATE KEY-----\nnot really\n-----END EC PRIVATE KEY-----\n")
	if err := os.WriteFile(filepath.Join(tlsDir, "proxy.crt"), []byte("cert\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(tlsDir, "proxy.key"), real, 0o000); err != nil {
		t.Fatal(err)
	}
	if readable(filepath.Join(tlsDir, "proxy.key")) {
		t.Skip("running as a user that can read a mode-000 file (root); the substitution cannot be exercised")
	}
	f := registry.LiveFixture()
	opts, _, _, _ := testOpts(t, roots)
	opts.defaults()
	gen, err := Render(f, roots)
	if err != nil {
		t.Fatal(err)
	}
	staged, warns, err := stage(opts, gen)
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(staged)

	b, err := os.ReadFile(filepath.Join(staged, "tls", "proxy.key"))
	if err != nil {
		t.Fatalf("the staged key is still unreadable: %v", err)
	}
	if strings.Contains(string(b), "not really") {
		t.Error("the staged key is the real one")
	}
	// The REAL key must be untouched — the staged copy may have been a symlink
	// to it, and writing through that would have overwritten the live key.
	orig, err := os.ReadFile(filepath.Join(tlsDir, "proxy.key"))
	if err == nil && string(orig) != string(real) {
		t.Error("the live key was overwritten by the substitution")
	}
	var said bool
	for _, w := range warns {
		if strings.Contains(w, "throwaway self-signed pair") {
			said = true
		}
	}
	if !said {
		t.Errorf("the substitution was not reported: %v", warns)
	}
}

// A certificate this account cannot read is a DIFFERENT situation from a key
// it cannot read, and substituting there would mask a cert/key mismatch behind
// a pair this package generated and made consistent by construction.
func TestStageDoesNotSubstituteWhenOnlyTheCertificateIsUnreadable(t *testing.T) {
	roots := testRoots(t)
	tlsDir := filepath.Join(roots.ProxyDir, "tls")
	if err := os.MkdirAll(tlsDir, 0o750); err != nil {
		t.Fatal(err)
	}
	realKey := []byte("-----BEGIN EC PRIVATE KEY-----\nthe real key\n-----END EC PRIVATE KEY-----\n")
	if err := os.WriteFile(filepath.Join(tlsDir, "proxy.key"), realKey, 0o640); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(tlsDir, "proxy.crt"), []byte("cert\n"), 0o000); err != nil {
		t.Fatal(err)
	}
	if readable(filepath.Join(tlsDir, "proxy.crt")) {
		t.Skip("running as a user that can read a mode-000 file (root)")
	}
	f := registry.LiveFixture()
	opts, _, _, _ := testOpts(t, roots)
	opts.defaults()
	gen, err := Render(f, roots)
	if err != nil {
		t.Fatal(err)
	}
	staged, warns, err := stage(opts, gen)
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(staged)

	b, err := os.ReadFile(filepath.Join(staged, "tls", "proxy.key"))
	if err != nil {
		t.Fatalf("the staged key: %v", err)
	}
	if string(b) != string(realKey) {
		t.Error("the readable real key was replaced although only the certificate was unreadable")
	}
	var said bool
	for _, w := range warns {
		if strings.Contains(w, "mask a cert/key mismatch") {
			said = true
		}
	}
	if !said {
		t.Errorf("the refusal to substitute was not reported: %v", warns)
	}
}

// The staged tree holds a copy of tls/ — a readable private key wherever this
// account can read the real one. /tmp is world-traversable and survives a kill
// until the next reboot; the ctl's own state dir is neither.
func TestStageParentDefaultsToThePrivateCtlTmp(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, _, _, _ := testOpts(t, roots)
	opts.StageParent = "" // the production default
	opts.defaults()
	gen, err := Render(f, roots)
	if err != nil {
		t.Fatal(err)
	}
	staged, _, err := stage(opts, gen)
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(staged)

	want := filepath.Join(roots.CtlStateDir, "tmp")
	if filepath.Dir(staged) != want {
		t.Errorf("staged in %s, want a directory under %s", staged, want)
	}
	fi, err := os.Stat(want)
	if err != nil {
		t.Fatal(err)
	}
	if fi.Mode().Perm() != 0o700 {
		t.Errorf("%s is mode %s, want 0700", want, fi.Mode().Perm())
	}
	// (The test root itself lives under the OS temp dir, so "not in /tmp" is
	// not assertable here; "under CtlStateDir, mode 0700" is the property.)
}

// ---------------------------------------------------------------- repair

func TestRepairAfterACrashBetweenSwitchAndVerify(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, _, sig, pr := testOpts(t, roots)
	publishOnce(t, f, opts, pr) // gen-1, verified

	// Simulate the crash: gen-2 written, current moved, txn `switched` with no
	// finished_at — exactly what a kill -9 mid-publish leaves behind.
	f.Generation++
	gen2, err := Render(f, roots)
	if err != nil {
		t.Fatal(err)
	}
	st := NewState(roots)
	if err := gen2.Write(st); err != nil {
		t.Fatal(err)
	}
	if err := st.pointCurrentAt(gen2.N); err != nil {
		t.Fatal(err)
	}
	if err := st.WriteTxn(&Txn{
		Generation: gen2.N, RegistryGeneration: f.Generation, StartedAt: now().UTC().Format(time.RFC3339),
		State: TxnSwitched, PublishedBy: "wilke", PreviousGeneration: 1,
	}); err != nil {
		t.Fatal(err)
	}

	rep, err := st.Repair()
	if err != nil {
		t.Fatal(err)
	}
	if !rep.Incomplete {
		t.Fatal("Repair did not report the incomplete publication")
	}
	if st.CurrentGeneration() != 1 {
		t.Errorf("current = gen-%d after repair, want the last verified gen-1", st.CurrentGeneration())
	}
	if !strings.Contains(rep.Action, "gen-1") {
		t.Errorf("action %q", rep.Action)
	}
	// Status keeps SAYING incomplete: the automatic repair fixes the pointer,
	// it does not make the crash disappear from the dashboard.
	status, err := StatusWith(roots, f, Options{Sig: sig})
	if err != nil {
		t.Fatal(err)
	}
	if status.TxnState != TxnStateIncomplete {
		t.Errorf("txn_state = %q, want incomplete", status.TxnState)
	}
	if status.Generation != 1 {
		t.Errorf("status generation = %d", status.Generation)
	}
	// An operator acknowledging it is what turns it into `repaired`.
	if err := st.Acknowledge("wilke"); err != nil {
		t.Fatal(err)
	}
	status, err = StatusWith(roots, f, Options{Sig: sig})
	if err != nil {
		t.Fatal(err)
	}
	if status.TxnState != TxnStateRepaired {
		t.Errorf("txn_state = %q after acknowledge, want repaired", status.TxnState)
	}
	// Repair is idempotent.
	if rep2, err := st.Repair(); err != nil || rep2.Incomplete {
		t.Errorf("second repair: %+v %v", rep2, err)
	}
}

// A crash INSIDE switchTo — `current` already repointed, txn.json not yet past
// `switching` — is the window the old Repair could not see. It keyed on the
// recorded STATE (`staging` meant "the switch never happened") while the fact
// that mattered was the POINTER, which had moved. The result was a gateway
// left on a generation nothing verified, with a repair that reported "none".
func TestRepairAfterACrashInsideSwitchTo(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, _, _, pr := testOpts(t, roots)
	publishOnce(t, f, opts, pr) // gen-1, verified
	st := NewState(roots)

	f.Generation++
	f.Tenants["dev"].Ports.API = 24049
	gen2, err := Render(f, roots)
	if err != nil {
		t.Fatal(err)
	}
	if err := gen2.Write(st); err != nil {
		t.Fatal(err)
	}
	// The kill lands between pointCurrentAt and the first include relink, so
	// the durable record still says `switching`.
	if err := st.WriteTxn(&Txn{
		Generation: gen2.N, RegistryGeneration: f.Generation, StartedAt: now().UTC().Format(time.RFC3339),
		State: TxnSwitching, PublishedBy: "wilke", PreviousGeneration: 1,
	}); err != nil {
		t.Fatal(err)
	}
	if err := st.pointCurrentAt(gen2.N); err != nil {
		t.Fatal(err)
	}

	rep, err := st.Repair()
	if err != nil {
		t.Fatal(err)
	}
	if !rep.Incomplete {
		t.Fatal("Repair did not report the incomplete publication")
	}
	if st.CurrentGeneration() != 1 {
		t.Errorf("current = gen-%d after repair, want the last verified gen-1", st.CurrentGeneration())
	}
	if !strings.Contains(rep.Action, "gen-1") {
		t.Errorf("action %q", rep.Action)
	}

	// The honest `staging` crash — the switch really did not happen, so
	// `current` is still on the old generation — is the SAME test and needs no
	// special case: the pointer is not on the incomplete generation.
	if err := st.WriteTxn(&Txn{
		Generation: 9, State: TxnStaging, StartedAt: "2026-09-12T00:00:00Z", PreviousGeneration: 1,
	}); err != nil {
		t.Fatal(err)
	}
	rep, err = st.Repair()
	if err != nil {
		t.Fatal(err)
	}
	if !rep.Incomplete || rep.CurrentGeneration != 1 || !strings.Contains(rep.Action, "none") {
		t.Errorf("report = %+v", rep)
	}
}

// GET /v1/gateway is a VIEWER operation. It used to call Repair, which moves
// the symlink nginx resolves both generated includes through — unlocked, from
// a dashboard poll, possibly while a publish was between its switch and its
// probe.
func TestGatewayStatusDoesNotMutateTheStateDir(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, _, sig, pr := testOpts(t, roots)
	publishOnce(t, f, opts, pr)
	st := NewState(roots)

	// An incomplete publication, exactly what a kill -9 mid-publish leaves.
	f.Generation++
	f.Tenants["dev"].Ports.API = 24049
	gen2, err := Render(f, roots)
	if err != nil {
		t.Fatal(err)
	}
	if err := gen2.Write(st); err != nil {
		t.Fatal(err)
	}
	if err := st.pointCurrentAt(gen2.N); err != nil {
		t.Fatal(err)
	}
	if err := st.WriteTxn(&Txn{
		Generation: gen2.N, RegistryGeneration: f.Generation, StartedAt: now().UTC().Format(time.RFC3339),
		State: TxnSwitched, PublishedBy: "wilke", PreviousGeneration: 1,
	}); err != nil {
		t.Fatal(err)
	}

	before := snapshotTree(t, st.Dir)
	status, err := StatusWith(roots, f, Options{Sig: sig})
	if err != nil {
		t.Fatal(err)
	}
	if got := snapshotTree(t, st.Dir); got != before {
		t.Errorf("GET /v1/gateway changed the state dir:\nbefore:\n%s\nafter:\n%s", before, got)
	}
	if status.TxnState != TxnStateIncomplete {
		t.Errorf("txn_state = %q, want incomplete — status REPORTS the crash, it does not fix it", status.TxnState)
	}
	if status.Generation != gen2.N {
		t.Errorf("status generation = %d, want the %d the pointer really names", status.Generation, gen2.N)
	}
	// And the repair an operator runs still works afterwards.
	if _, err := st.Repair(); err != nil {
		t.Fatal(err)
	}
	if st.CurrentGeneration() != 1 {
		t.Errorf("current = %d after an explicit repair", st.CurrentGeneration())
	}
}

// snapshotTree renders a directory as text: every entry, its type, its mode and
// either its symlink target or a digest of its bytes.
func snapshotTree(t *testing.T, dir string) string {
	t.Helper()
	var b strings.Builder
	err := filepath.Walk(dir, func(p string, fi os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(dir, p)
		if err != nil {
			return err
		}
		switch {
		case fi.Mode()&os.ModeSymlink != 0:
			target, err := os.Readlink(p)
			if err != nil {
				return err
			}
			fmt.Fprintf(&b, "%s link -> %s\n", rel, target)
		case fi.IsDir():
			fmt.Fprintf(&b, "%s dir %s\n", rel, fi.Mode().Perm())
		default:
			raw, err := os.ReadFile(p)
			if err != nil {
				return err
			}
			fmt.Fprintf(&b, "%s file %s %x\n", rel, fi.Mode().Perm(), sha256.Sum256(raw))
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return b.String()
}

// Two applies at once would each read the other's half-finished `current`, and
// the loser's revert would undo the winner's switch.
func TestConcurrentPublishIsRefusedByTheLock(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, ex, sig, _ := testOpts(t, roots)
	st := NewState(roots)

	// Another process's lock, taken the way lock() takes it but through an
	// independent descriptor, so this is not the same code asserting itself.
	if err := os.MkdirAll(st.Dir, 0o2770); err != nil {
		t.Fatal(err)
	}
	held, err := os.OpenFile(filepath.Join(st.Dir, LockName), os.O_CREATE|os.O_RDWR, 0o660)
	if err != nil {
		t.Fatal(err)
	}
	defer held.Close()
	if err := syscall.Flock(int(held.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		t.Fatal(err)
	}

	res, err := Publish(context.Background(), f, opts)
	if !errors.Is(err, ErrLocked) {
		t.Fatalf("err = %v, want ErrLocked", err)
	}
	if !errors.Is(err, ErrRefused) {
		t.Error("a lock conflict must be a REFUSAL (exit 3), not a host error")
	}
	if ex.Count() != 0 || sig.Count() != 0 {
		t.Errorf("the locked-out publish still ran nginx -t (%d) or signalled (%d)", ex.Count(), sig.Count())
	}
	if len(st.Generations()) != 0 {
		t.Errorf("the locked-out publish wrote generations %v", st.Generations())
	}
	if len(res.Steps) != 1 || res.Steps[0].Name != "lock" {
		t.Errorf("steps = %+v, want the lock step alone", res.Steps)
	}

	// Released, the same publish goes through.
	if err := syscall.Flock(int(held.Fd()), syscall.LOCK_UN); err != nil {
		t.Fatal(err)
	}
	_, _, _, pr := testOpts(t, roots)
	opts.Prober = pr
	publishOnce(t, f, opts, pr)
}

// A foreign nginx master is knowable from /proc before anything is written.
// The checks used to run inside reload(), i.e. after the generation was
// written, `current` moved and both include symlinks rewritten.
func TestPublishRefusesAForeignMasterBeforeTheSwitch(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, ex, sig, pr := testOpts(t, roots)
	publishOnce(t, f, opts, pr) // gen-1 is serving and must stay serving
	st := NewState(roots)
	beforeIncludes := snapshotTree(t, roots.ProxyDir)
	beforeSignals := sig.Count()
	beforeExec := ex.Count()

	sig.Info.UID = os.Getuid() + 1000 // the proxy belongs to someone else now
	f.Generation++
	f.Tenants["dev"].Ports.API = 24049

	res, err := Publish(context.Background(), f, opts)
	if !errors.Is(err, ErrRefused) {
		t.Fatalf("err = %v, want a refusal", err)
	}
	if !strings.Contains(err.Error(), "run as that account") {
		t.Errorf("the refusal does not name the remedy: %v", err)
	}
	if st.CurrentGeneration() != 1 {
		t.Errorf("current = gen-%d, want the gen-1 that was serving", st.CurrentGeneration())
	}
	if sig.Count() != beforeSignals {
		t.Errorf("%d signals were sent for a refused publish", sig.Count()-beforeSignals)
	}
	if ex.Count() != beforeExec {
		t.Error("the refused publish still staged a tree and ran nginx -t")
	}
	if got := snapshotTree(t, roots.ProxyDir); got != beforeIncludes {
		t.Error("the refused publish touched the proxy tree")
	}
	if len(st.Generations()) != 1 {
		t.Errorf("the refused publish wrote generations %v", st.Generations())
	}
	if res.State != TxnFailed {
		t.Errorf("state %q", res.State)
	}
}

// A SIGHUP nginx rejects is not an error: kill(2) succeeds, the master logs
// [emerg], keeps the OLD configuration and carries on. The publish used to end
// `verified` on the strength of probes the old configuration answered.
func TestPublishFailsWhenTheMasterRejectedTheReload(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, _, sig, pr := testOpts(t, roots)
	publishOnce(t, f, opts, pr) // gen-1, verified
	st := NewState(roots)

	// The master answers the HUP by keeping its workers and logging [emerg].
	sig.FrozenWorkers = true
	errorLog := filepath.Join(roots.ProxyDir, "run", "logs", "error.log")
	if err := os.WriteFile(errorLog, []byte(now().Format("2006/01/02 15:04:05")+
		` [emerg] 4242#4242: duplicate "map" directive in /rag/config/proxy/conf.d/05-tenants.generated.conf:9`+"\n"), 0o640); err != nil {
		t.Fatal(err)
	}

	f.Generation++
	f.Tenants["dev"].Ports.API = 24049
	gen2, err := Render(f, roots)
	if err != nil {
		t.Fatal(err)
	}
	answerDesiredState(t, pr, gen2, f) // the probes would all pass

	res, err := Publish(context.Background(), f, opts)
	if err == nil {
		t.Fatal("publish succeeded although the master refused the configuration")
	}
	if !res.Reverted || res.State != TxnReverted {
		t.Fatalf("result %+v, want a revert", res)
	}
	if st.CurrentGeneration() != 1 {
		t.Errorf("current = gen-%d, want the gen-1 that is actually serving", st.CurrentGeneration())
	}
	if !strings.Contains(err.Error(), "same workers") && !strings.Contains(err.Error(), "[emerg]") {
		t.Errorf("the failure does not say the master rejected the reload: %v", err)
	}
}

// An [emerg] the master logged BEFORE the HUP is somebody else's old problem
// and must not fail this publish.
func TestReloadConfirmationIgnoresAnOlderEmerg(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, _, sig, pr := testOpts(t, roots)
	opts.defaults()
	errorLog := filepath.Join(roots.ProxyDir, "run", "logs", "error.log")
	if err := os.WriteFile(errorLog, []byte(now().Add(-time.Hour).Format("2006/01/02 15:04:05")+
		" [emerg] 1#1: bind() to 0.0.0.0:9000 failed\n"), 0o640); err != nil {
		t.Fatal(err)
	}
	publishOnce(t, f, opts, pr)
	if sig.Count() != 1 {
		t.Errorf("%d signals", sig.Count())
	}
	if NewState(roots).CurrentGeneration() != 1 {
		t.Error("a stale [emerg] failed the publish")
	}
}

// The pointer moved and the record could not be written. That used to return
// straight out of Publish, leaving `current` on a generation nothing verified
// and a txn.json that did not say so.
func TestTransitionWriteFailureAfterSwitchReverts(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, _, sig, pr := testOpts(t, roots)
	publishOnce(t, f, opts, pr) // gen-1, verified
	st := NewState(roots)
	beforeSignals := sig.Count()

	boom := errors.New("no space left on device")
	txnWriteHook = func(state string) error {
		if state == TxnVerified {
			return boom
		}
		return nil
	}
	defer func() { txnWriteHook = nil }()

	f.Generation++
	f.Tenants["dev"].Ports.API = 24049
	gen2, err := Render(f, roots)
	if err != nil {
		t.Fatal(err)
	}
	answerDesiredState(t, pr, gen2, f)
	res, err := Publish(context.Background(), f, opts)
	if err == nil {
		t.Fatal("publish succeeded although txn.json could not be written after the switch")
	}
	if !res.Reverted {
		t.Fatalf("no revert: %+v", res.Steps)
	}
	if st.CurrentGeneration() != 1 {
		t.Errorf("current = gen-%d after the failed transition, want gen-1", st.CurrentGeneration())
	}
	if sig.Count() != beforeSignals+2 {
		t.Errorf("%d signals, want two (the publish's HUP and the revert's)", sig.Count()-beforeSignals)
	}
	for _, rel := range RelPaths() {
		if _, err := os.Stat(filepath.Join(roots.ProxyDir, rel)); err != nil {
			t.Errorf("%s does not resolve after the revert: %v", rel, err)
		}
	}
}

// ---------------------------------------------------------------- pruning

func TestPruneKeepsTheLastFivePlusCurrentAndPrevious(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, _, _, pr := testOpts(t, roots)
	st := NewState(roots)
	for i := 0; i < 8; i++ {
		f.Generation++
		// Every publish must differ, or nothing interesting is being pruned.
		f.Tenants["dev"].Ports.API = 24040 + i
		publishOnce(t, f, opts, pr)
	}
	gens := st.Generations()
	if len(gens) != 5 {
		t.Fatalf("generations kept = %v, want the last five", gens)
	}
	if gens[len(gens)-1] != 8 || st.CurrentGeneration() != 8 {
		t.Errorf("current = %d, generations %v", st.CurrentGeneration(), gens)
	}
	for _, n := range []int{7, 8} {
		if _, err := os.Stat(st.GenDir(n)); err != nil {
			t.Errorf("gen-%d (current/previous) was pruned", n)
		}
	}
	for _, n := range []int{1, 2, 3} {
		if _, err := os.Stat(st.GenDir(n)); !errors.Is(err, os.ErrNotExist) {
			t.Errorf("gen-%d survived pruning", n)
		}
	}
}

// ---------------------------------------------------------------- rollback

func TestRollbackReturnsToThePreviousGeneration(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, ex, sig, pr := testOpts(t, roots)
	publishOnce(t, f, opts, pr)
	f.Generation++
	f.Tenants["dev"].Ports.API = 24044
	publishOnce(t, f, opts, pr)
	st := NewState(roots)
	if st.CurrentGeneration() != 2 {
		t.Fatalf("current = %d", st.CurrentGeneration())
	}

	// Rolling back means the gateway must again answer gen-1's desired state.
	gen1files, err := st.ReadGeneration(1)
	if err != nil {
		t.Fatal(err)
	}
	answerDesiredState(t, pr, &Generation{N: 1, Files: gen1files}, f)
	beforeSig, beforeExec := sig.Count(), ex.Count()
	res, err := Rollback(context.Background(), f, 0, opts)
	if err != nil {
		t.Fatalf("rollback: %v (%+v)", err, res.Steps)
	}
	if st.CurrentGeneration() != 1 {
		t.Errorf("current = %d after rollback", st.CurrentGeneration())
	}
	if sig.Count() != beforeSig+1 {
		t.Errorf("rollback sent %d signals", sig.Count()-beforeSig)
	}
	// The target's bytes are tested against TODAY's proxy tree before they are
	// switched to: the tree around them has moved since they were written.
	if ex.Count() != beforeExec+1 {
		t.Errorf("rollback ran nginx -t %d times, want exactly one", ex.Count()-beforeExec)
	}
	if res.State != TxnRolledBack {
		t.Errorf("state %q, want %q", res.State, TxnRolledBack)
	}
	txn, _, err := st.ReadTxn()
	if err != nil {
		t.Fatal(err)
	}
	if txn.State != TxnRolledBack || !txn.Complete() || txn.ConfigOK == nil || !*txn.ConfigOK {
		t.Errorf("txn = %+v", txn)
	}
}

func TestRollbackRefusesWithoutAPrevious(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, _, _, pr := testOpts(t, roots)
	if _, err := Rollback(context.Background(), f, 0, opts); !errors.Is(err, ErrRefused) {
		t.Fatalf("err = %v, want a refusal with nothing published", err)
	}
	publishOnce(t, f, opts, pr) // gen-1, previous 0
	if _, err := Rollback(context.Background(), f, 0, opts); !errors.Is(err, ErrRefused) {
		t.Fatalf("err = %v, want a refusal when gen-1 is the first", err)
	}
	if _, err := Rollback(context.Background(), f, 9, opts); !errors.Is(err, ErrRefused) {
		t.Fatalf("err = %v, want a refusal for a generation that is not on disk", err)
	}
}

// A generation that was published and then REVERTED is on disk, has a
// manifest, and was never a state this host served. Rolling back to it is not
// going back to anything.
func TestRollbackRefusesAGenerationThatWasNeverVerified(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, _, _, pr := testOpts(t, roots)
	publishOnce(t, f, opts, pr) // gen-1, verified
	st := NewState(roots)

	// gen-2 is published and reverted by a failing probe.
	f.Generation++
	f.Tenants["dev"].Ports.API = 24049
	pr.Statuses["/ragstack/tenants"] = 500
	if _, err := Publish(context.Background(), f, opts); err == nil {
		t.Fatal("the second publish was supposed to fail its probe")
	}
	delete(pr.Statuses, "/ragstack/tenants")
	if _, err := os.Stat(filepath.Join(st.GenDir(2), ManifestName)); err != nil {
		t.Fatalf("gen-2 is not on disk, so the test proves nothing: %v", err)
	}
	if got, _ := st.VerifiedGenerations(); len(got) != 1 || got[0] != 1 {
		t.Errorf("verified generations = %v, want just gen-1", got)
	}
	_, err := Rollback(context.Background(), f, 2, opts)
	if !errors.Is(err, ErrRefused) {
		t.Fatalf("err = %v, want a refusal", err)
	}
	if !strings.Contains(err.Error(), "never reached `verified`") {
		t.Errorf("the refusal does not explain itself: %v", err)
	}
	if st.CurrentGeneration() != 1 {
		t.Errorf("current = %d after a refused rollback", st.CurrentGeneration())
	}
}

// A rollback that fails after its switch must not leave the gateway on the
// target: it goes back to the generation the rollback started from.
func TestRollbackRevertsWhenTheTargetDoesNotVerify(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, _, sig, pr := testOpts(t, roots)
	publishOnce(t, f, opts, pr) // gen-1: four tenants
	f.Generation++
	f.Tenants["demo"].State = "decommissioned"
	publishOnce(t, f, opts, pr) // gen-2: three, and now serving
	st := NewState(roots)

	// The gateway keeps answering gen-2's three-tenant list, so gen-1 — which
	// advertises four — cannot be verified.
	beforeSig := sig.Count()
	res, err := Rollback(context.Background(), f, 1, opts)
	if err == nil {
		t.Fatal("rollback succeeded although the gateway never answered the target's routing")
	}
	if !res.Reverted || res.State != TxnRollbackFailed {
		t.Fatalf("result %+v, want %q with a revert", res, TxnRollbackFailed)
	}
	if st.CurrentGeneration() != 2 {
		t.Errorf("current = gen-%d, want the gen-2 the rollback started from", st.CurrentGeneration())
	}
	if sig.Count() != beforeSig+2 {
		t.Errorf("%d signals, want two (the rollback's HUP and the revert's)", sig.Count()-beforeSig)
	}
	txn, _, err := st.ReadTxn()
	if err != nil {
		t.Fatal(err)
	}
	if txn.State != TxnRollbackFailed || !txn.Complete() {
		t.Errorf("txn = %+v", txn)
	}
	for _, rel := range RelPaths() {
		if _, err := os.Stat(filepath.Join(roots.ProxyDir, rel)); err != nil {
			t.Errorf("%s does not resolve after the failed rollback: %v", rel, err)
		}
	}
}

// A rollback target whose bytes no longer load against the CURRENT proxy tree
// (a snippet renamed since it was written) is refused before the switch.
func TestRollbackRefusesATargetThatFailsNginxTest(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, ex, sig, pr := testOpts(t, roots)
	publishOnce(t, f, opts, pr)
	f.Generation++
	f.Tenants["dev"].Ports.API = 24044
	publishOnce(t, f, opts, pr)
	st := NewState(roots)
	beforeSig := sig.Count()

	ex.Runner = func([]string) ([]byte, []byte, error) {
		return nil, []byte(`nginx: [emerg] open() ".../snippets/cors.conf" failed`), errors.New("exit status 1")
	}
	res, err := Rollback(context.Background(), f, 1, opts)
	if err == nil {
		t.Fatal("rollback succeeded although nginx -t rejected the target")
	}
	if st.CurrentGeneration() != 2 {
		t.Errorf("current = gen-%d, want the untouched gen-2", st.CurrentGeneration())
	}
	if sig.Count() != beforeSig {
		t.Error("the master was signalled although the target failed nginx -t")
	}
	if res.State != TxnFailed || res.Reverted {
		t.Errorf("result %+v, want a plain failure with nothing to revert", res)
	}
}

// ---------------------------------------------------------------- status/diff

func TestStatusBeforeAnyPublish(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	_, _, sig, _ := testOpts(t, roots)
	s, err := StatusWith(roots, f, Options{Sig: sig})
	if err != nil {
		t.Fatal(err)
	}
	if s.Generation != 0 || s.TxnState != TxnStateNone || !s.PendingDiff {
		t.Errorf("status = %+v, want an unpublished gateway", s)
	}
	if s.PublishedAt != nil || s.PublishedBy != nil {
		t.Error("published_at/by must be null before the first publish")
	}
	if len(s.Routes) != 4 || len(s.LegacyRoutes) != 2 {
		t.Errorf("routes %d legacy %d", len(s.Routes), len(s.LegacyRoutes))
	}
	if s.Routes[0].Name != "dev" {
		t.Errorf("routes are not in display order: %s", s.Routes[0].Name)
	}
	// The response must survive a round trip through the contract's shape.
	if _, err := json.Marshal(s); err != nil {
		t.Fatal(err)
	}
}

func TestDiffAgainstThePublishedGeneration(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, _, _, pr := testOpts(t, roots)
	publishOnce(t, f, opts, pr)

	d, err := DiffDetail(roots, f)
	if err != nil {
		t.Fatal(err)
	}
	if d.Response.Changed {
		t.Errorf("diff against the just-published generation is not empty:\n%+v", d.Response.Files)
	}
	if d.ComparedTo != "gen-1" {
		t.Errorf("compared to %q", d.ComparedTo)
	}

	f.Tenants["dev"].Ports.API = 24049
	f.Generation++
	d, err = DiffDetail(roots, f)
	if err != nil {
		t.Fatal(err)
	}
	if !d.Response.Changed {
		t.Fatal("a port change produced no diff")
	}
	var body string
	for _, file := range d.Response.Files {
		if file.Path == FileTenants {
			body = file.Diff
		}
	}
	if !strings.Contains(body, `-    dev         "127.0.0.1:24040";`) || !strings.Contains(body, `+    dev         "127.0.0.1:24049";`) {
		t.Errorf("diff does not show the port move:\n%s", body)
	}
	if !strings.HasPrefix(body, "--- current/") {
		t.Errorf("diff has no unified header:\n%s", body)
	}
}

// The semantic no-op is PR-B's go/no-go, and it used to compare PORTS only:
// the host half of every map value was parsed and thrown away. `10.0.0.5:24040`
// and `127.0.0.1:24040` are different machines and were called the same route
// — on a check whose entire job is to prove the generated maps route what the
// hand-written ones route.
func TestSemanticDiffCatchesANonLoopbackLiveRow(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	maps := filepath.Join(roots.ProxyDir, "conf.d", "00-maps.conf")
	b, err := os.ReadFile(maps)
	if err != nil {
		t.Fatal(err)
	}
	// The live gateway sends dev's API off-host; the render only ever emits
	// loopback.
	edited := strings.Replace(string(b), `dev      "127.0.0.1:24040";`, `dev      "10.0.0.5:24040";`, 1)
	if edited == string(b) {
		t.Fatal("the fixture row this test edits is gone")
	}
	if err := os.WriteFile(maps, []byte(edited), 0o644); err != nil {
		t.Fatal(err)
	}
	d, err := DiffDetail(roots, f)
	if err != nil {
		t.Fatal(err)
	}
	if d.SemanticNoop {
		t.Fatal("a live row pointing off-host was reported as a semantic no-op")
	}
	var said bool
	for _, n := range d.Notes {
		if strings.Contains(n, "10.0.0.5:24040") && strings.Contains(n, "127.0.0.1:24040") {
			said = true
		}
	}
	if !said {
		t.Errorf("the notes do not name the address change: %v", d.Notes)
	}
}

// A tenant that is routed but not LISTED is reachable and invisible. The
// comparison used to ignore both JSON literals entirely.
func TestSemanticDiffComparesTheTenantLists(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	routes := filepath.Join(roots.ProxyDir, "snippets", "routes.conf")
	b, err := os.ReadFile(routes)
	if err != nil {
		t.Fatal(err)
	}
	edited := strings.ReplaceAll(string(b), `"tenants":["dev","demo","lucid-next","asm-next"]`, `"tenants":["dev","demo"]`)
	if edited == string(b) {
		t.Fatal("the fixture literal this test edits is gone")
	}
	if err := os.WriteFile(routes, []byte(edited), 0o644); err != nil {
		t.Fatal(err)
	}
	d, err := DiffDetail(roots, f)
	if err != nil {
		t.Fatal(err)
	}
	if d.SemanticNoop {
		t.Fatal("a shortened live tenant list was reported as a semantic no-op")
	}
	var said bool
	for _, n := range d.Notes {
		if strings.Contains(n, "$tenants_names_json") {
			said = true
		}
	}
	if !said {
		t.Errorf("the notes do not name the list that changed: %v", d.Notes)
	}
}

func TestUnifiedDiffShape(t *testing.T) {
	a := "one\ntwo\nthree\nfour\nfive\nsix\nseven\n"
	b := "one\ntwo\nthree\nFOUR\nfive\nsix\nseven\n"
	got := unified(a, b, "a", "b")
	want := "--- a\n+++ b\n@@ -1,7 +1,7 @@\n one\n two\n three\n-four\n+FOUR\n five\n six\n seven\n"
	if got != want {
		t.Errorf("diff:\n%q\nwant\n%q", got, want)
	}
	if unified(a, a, "a", "b") != "" {
		t.Error("identical inputs produced a diff")
	}
	add := unified("", "x\ny\n", "/dev/null", "b")
	if add != "--- /dev/null\n+++ b\n@@ -0,0 +1,2 @@\n+x\n+y\n" {
		t.Errorf("whole-file addition:\n%q", add)
	}
}

func TestExpectBodiesCoverTheFourGoldenPaths(t *testing.T) {
	bodies, err := LoadExpectBodies("../testdata/live-2026-09-10/gateway")
	if err != nil {
		t.Fatal(err)
	}
	for _, p := range []string{"/", "/ragstack/tenants", "/ragstack/nope/api/health", "/ragstack/x"} {
		if len(bodies[p]) == 0 {
			t.Errorf("no golden body for %s", p)
		}
	}
	if _, err := LoadExpectBodies(t.TempDir()); err == nil {
		t.Error("an empty directory must not pass as a golden set")
	}
}

func TestProbeRejectsAWrongTenantList(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, _, _, pr := testOpts(t, roots)
	opts.defaults()
	gen, err := Render(f, roots)
	if err != nil {
		t.Fatal(err)
	}
	answerDesiredState(t, pr, gen, f)
	pr.Bodies["/ragstack/tenants"] = []byte(`{"tenants":[{"name":"dev"}]}`)
	err = probe(context.Background(), opts, f, gen)
	if err == nil || !strings.Contains(err.Error(), "lists [dev]") {
		t.Fatalf("err = %v", err)
	}
}

func TestProbeRequires200ForAnActiveTenantAnd502ForAStoppedOne(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	opts, _, _, pr := testOpts(t, roots)
	opts.defaults()
	f.Tenants["demo"].State = "stopped"
	gen, err := Render(f, roots)
	if err != nil {
		t.Fatal(err)
	}
	answerDesiredState(t, pr, gen, f)
	if err := probe(context.Background(), opts, f, gen); err != nil {
		t.Fatalf("desired state rejected: %v", err)
	}
	// A stopped tenant that answers 200 means the gateway is routing traffic
	// the registry says should be down.
	pr.Statuses["/ragstack/demo/api/health"] = 200
	if err := probe(context.Background(), opts, f, gen); err == nil {
		t.Fatal("a stopped tenant answering 200 was accepted")
	}
	pr.Statuses["/ragstack/demo/api/health"] = 502
	pr.Statuses["/ragstack/dev/api/health"] = 502
	if err := probe(context.Background(), opts, f, gen); err == nil {
		t.Fatal("an active tenant answering 502 was accepted")
	}
}
