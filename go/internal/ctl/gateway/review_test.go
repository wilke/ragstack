package gateway

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// TestFirstPublishAdoptsABootstrapCopyItCannotVerify.
//
// coconut-proxy's bootstrap copy is rendered ONCE and committed; the registry
// keeps moving, and not only in its generation counter. Give one tenant a
// static UI and the shipped snippets/tenants-ui-static.generated.conf — which
// is header-only, because no tenant had one when it was rendered — stops
// being byte-identical to anything the ctl renders today, and so does the
// tenant map that no longer carries that tenant's $tenant_ui row.
//
// Demanding byte identity refused the very FIRST apply on a host whose only
// sin was editing its own registry, and left pending_diff true with no way to
// clear it. Before the first publish the ctl has never written at either path,
// so whatever is there is the bootstrap copy by definition: adopt it, record
// its bytes for the revert, and say so.
func TestFirstPublishAdoptsABootstrapCopyItCannotVerify(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()
	f.LegacyRoutes = []registry.LegacyRoute{f.LegacyRoutes[1], f.LegacyRoutes[0]}
	// A static UI nothing in the shipped bootstrap knows about.
	f.Tenants["dev"].UI = registry.UI{Mode: registry.UIModeStatic, Base: "/ragstack/dev/ui/"}
	opts, _, _, pr := testOpts(t, roots)

	sizes := map[string]int{}
	shipped := map[string][]byte{}
	for _, rel := range RelPaths() {
		b, err := os.ReadFile(filepath.Join(bootstrapFixture, filepath.FromSlash(rel)))
		if err != nil {
			t.Fatal(err)
		}
		shipped[rel], sizes[rel] = b, len(b)
		p := filepath.Join(roots.ProxyDir, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(p), 0o750); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, b, 0o644); err != nil { // deploy.sh writes 0644
			t.Fatal(err)
		}
	}

	gen, err := Render(f, roots)
	if err != nil {
		t.Fatal(err)
	}
	// If the fixture happened to match the render, the old rule would have
	// let this publish through and the test would prove nothing.
	for _, rel := range RelPaths() {
		if string(StripHeader(shipped[rel])) == string(StripHeader(gen.Files[rel])) {
			t.Fatalf("the shipped %s IS today's render, so this test proves nothing", rel)
		}
	}

	answerDesiredState(t, pr, gen, f)
	res, err := Publish(context.Background(), f, opts)
	if err != nil {
		t.Fatalf("the FIRST publish refused the shipped bootstrap: %v (%+v)", err, res.Steps)
	}
	for _, rel := range RelPaths() {
		live := filepath.Join(roots.ProxyDir, filepath.FromSlash(rel))
		fi, err := os.Lstat(live)
		if err != nil || fi.Mode()&os.ModeSymlink == 0 {
			t.Errorf("%s was not adopted (%v)", rel, err)
		}
		var named bool
		for _, w := range res.Warnings {
			if strings.Contains(w, live) && strings.Contains(w, fmt.Sprintf("%d bytes", sizes[rel])) &&
				strings.Contains(w, "a revert restores it") {
				named = true
			}
		}
		if !named {
			t.Errorf("no warning names %s with its %d bytes and the revert: %v", live, sizes[rel], res.Warnings)
		}
	}
}

// TestInspectIncludesRefusesADirectory: os.ReadFile on a fifo BLOCKS and on a
// directory fails with a message about no bootstrap copy; either way a
// non-regular, non-symlink path cannot be recorded for a revert, so the
// publish must refuse it before the pointer moves.
func TestInspectIncludesRefusesADirectory(t *testing.T) {
	roots := testRoots(t)
	p := filepath.Join(roots.ProxyDir, filepath.FromSlash(FileStatic))
	_ = os.Remove(p)
	if err := os.MkdirAll(p, 0o750); err != nil {
		t.Fatal(err)
	}
	if _, err := inspectIncludes(roots.ProxyDir); err == nil {
		t.Fatal("a directory at an include path was accepted")
	}
}

// TestDiffDetailReportsAnUnreadableLiveTree: an EACCES on the live include is
// not "nothing published yet". Reporting `semantic_noop` after comparing
// against nothing is the dangerous half of that mistake — it is the line that
// tells an operator the first publish changes no route.
func TestDiffDetailReportsAnUnreadableLiveTree(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root reads an 0000 file")
	}
	roots := testRoots(t)
	f := registry.LiveFixture()
	inc := filepath.Join(roots.ProxyDir, filepath.FromSlash(FileTenants))
	if err := os.MkdirAll(filepath.Dir(inc), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(inc, []byte("map $tenant $tenant_api {\n    default  \"\";\n}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(inc, 0o000); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chmod(inc, 0o600) })

	d, err := DiffDetail(roots, f)
	if err != nil {
		t.Fatal(err)
	}
	if d.SemanticNoop {
		t.Error("an unreadable live routing table was reported as a semantic no-op")
	}
	if !strings.Contains(strings.Join(d.Notes, "\n"), "could NOT be read") {
		t.Errorf("the notes do not say the live routing was unreadable: %v", d.Notes)
	}
}

// TestSemanticNoopIsDisqualifiedByAStaticSnippetDifference: the static snippet
// is routing — it decides which alias block serves which tenant's `vite build`
// — and it is in NEITHER of the three maps. Comparing only the maps called a
// first publish that adds, drops or repoints an alias block a no-op.
func TestSemanticNoopIsDisqualifiedByAStaticSnippetDifference(t *testing.T) {
	roots := testRoots(t)
	f := registry.LiveFixture()

	// This fixture is the PR-B no-op: every map row the render emits is what
	// the live tree already routes (TestRenderedGenerationIsASemanticNoop).
	// So the only difference below is the static snippet — a live alias block
	// for a tenant the render serves through $tenant_ui instead.
	if d, err := DiffDetail(roots, f); err != nil {
		t.Fatal(err)
	} else if !d.SemanticNoop {
		t.Fatalf("the baseline is not a no-op, so this test would prove nothing: %v", d.Notes)
	}

	live := filepath.Join(roots.ProxyDir, filepath.FromSlash(FileStatic))
	if err := os.MkdirAll(filepath.Dir(live), 0o750); err != nil {
		t.Fatal(err)
	}
	body := "location = /ragstack/demo/ui {\n    return 301 /ragstack/demo/ui/;\n}\n\n" +
		"location ^~ /ragstack/demo/ui/ {\n    alias /somewhere/else/;\n    try_files $uri $uri/ /ragstack/demo/ui/index.html;\n}\n"
	if err := os.WriteFile(live, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}

	d, err := DiffDetail(roots, f)
	if err != nil {
		t.Fatal(err)
	}
	notes := strings.Join(d.Notes, "\n")
	if d.SemanticNoop {
		t.Errorf("a live alias block the render drops was called a semantic no-op: %v", d.Notes)
	}
	if !strings.Contains(notes, "/ragstack/demo/ui/") || !strings.Contains(notes, "/somewhere/else") {
		t.Errorf("the notes do not say WHICH alias block changed: %v", d.Notes)
	}
}
