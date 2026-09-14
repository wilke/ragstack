package doctor

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// proxyTree builds an empty proxy tree with the two directories the live
// files sit in.
func proxyTree(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	for _, d := range []string{"conf.d", "snippets"} {
		if err := os.MkdirAll(filepath.Join(dir, d), 0o750); err != nil {
			t.Fatal(err)
		}
	}
	return dir
}

const includeWithLists = `map $tenant $tenant_api {
    default  "";
    inc  "127.0.0.1:24040";
}

map $host $tenants_names_json {
    default  '["inc-a","inc-b"]';
}

map $host $tenants_json {
    default  '[{"name":"inc-a","api":"/ragstack/inc-a/api/v1/...","ui":"/ragstack/inc-a/ui/"}]';
}
`

// TestLiveTenantListsPrefersTheRoutesConfLiteral: a `return` body is served
// verbatim, so in a MIXED tree — routes.conf still carrying its hand-written
// literal while coconut-proxy's deploy has already dropped the generated
// include in — the literal is what clients see.
func TestLiveTenantListsPrefersTheRoutesConfLiteral(t *testing.T) {
	dir := proxyTree(t)
	routes := `location = / { return 200 '{"tenants":["live-a","live-b"]}\n'; }` + "\n" +
		`location = /ragstack/tenants { return 200 '{"tenants":[{"name":"live-a","api":"/a","ui":"/u"}]}` + "\n"
	write(t, filepath.Join(dir, "snippets", "routes.conf"), routes)
	write(t, filepath.Join(dir, "conf.d", "05-tenants.generated.conf"), includeWithLists)

	lists, err := LiveTenantLists(dir)
	if err != nil {
		t.Fatal(err)
	}
	if lists.NamesJSON != `["live-a","live-b"]` {
		t.Errorf("names = %s, want the routes.conf literal", lists.NamesJSON)
	}
	if !strings.Contains(lists.TenantsJSON, "live-a") {
		t.Errorf("tenants = %s, want the routes.conf literal", lists.TenantsJSON)
	}
	if want := filepath.Join(dir, "snippets", "routes.conf"); lists.Source() != want {
		t.Errorf("source = %s, want %s", lists.Source(), want)
	}
	order, err := DisplayOrder(dir)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Join(order, ",") != "live-a,live-b" {
		t.Errorf("display order = %v, want the routes.conf literal", order)
	}
}

// TestLiveTenantListsFillsEachListIndependently: a tree really can carry one
// list in each file — routes.conf's landing-page literal survives a deploy
// that only rewrote the /ragstack/tenants block. Taking BOTH from whichever
// file answered first dropped the other.
func TestLiveTenantListsFillsEachListIndependently(t *testing.T) {
	dir := proxyTree(t)
	// routes.conf carries the bare names list and no object table.
	write(t, filepath.Join(dir, "snippets", "routes.conf"),
		`location = / { return 200 '{"tenants":["live-a","live-b"]}\n'; }`+"\n")
	write(t, filepath.Join(dir, "conf.d", "05-tenants.generated.conf"), includeWithLists)

	lists, err := LiveTenantLists(dir)
	if err != nil {
		t.Fatal(err)
	}
	if lists.NamesJSON != `["live-a","live-b"]` {
		t.Errorf("names = %s, want routes.conf's", lists.NamesJSON)
	}
	if !strings.Contains(lists.TenantsJSON, "inc-a") {
		t.Errorf("tenants = %q, want the include's — routes.conf carries none", lists.TenantsJSON)
	}
	if lists.TenantsSource != filepath.Join(dir, "conf.d", "05-tenants.generated.conf") {
		t.Errorf("tenants source = %s", lists.TenantsSource)
	}
}

// TestLiveTenantListsTreatsAnEmptyListAsNotFound: `"tenants":[]` advertises
// nothing, and a tree whose include DOES carry tenants is not a fleet with no
// tenants. `!= nil` called the empty slice an answer and stopped there.
func TestLiveTenantListsTreatsAnEmptyListAsNotFound(t *testing.T) {
	dir := proxyTree(t)
	write(t, filepath.Join(dir, "snippets", "routes.conf"),
		`location = / { return 200 '{"tenants":[]}\n'; }`+"\n")
	write(t, filepath.Join(dir, "conf.d", "05-tenants.generated.conf"), includeWithLists)

	order, err := DisplayOrder(dir)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Join(order, ",") != "inc-a,inc-b" {
		t.Errorf("display order = %v, want the include's list", order)
	}
}

// TestLiveTenantListsResolvesTheIncludeSymlink: after the first publish the
// include IS a symlink into a generation, and "compared to
// conf.d/05-tenants.generated.conf" answers nothing. Name the generation.
func TestLiveTenantListsResolvesTheIncludeSymlink(t *testing.T) {
	dir := proxyTree(t)
	gen := filepath.Join(dir, "generations", "gen-7", "conf.d")
	if err := os.MkdirAll(gen, 0o750); err != nil {
		t.Fatal(err)
	}
	target := filepath.Join(gen, "05-tenants.generated.conf")
	write(t, target, includeWithLists)
	if err := os.Symlink(target, filepath.Join(dir, "conf.d", "05-tenants.generated.conf")); err != nil {
		t.Fatal(err)
	}
	lists, err := LiveTenantLists(dir)
	if err != nil {
		t.Fatal(err)
	}
	if lists.Source() != target {
		t.Errorf("source = %s, want the generation path %s", lists.Source(), target)
	}
	maps, ok, err := GatewayMaps(dir)
	if err != nil || !ok {
		t.Fatalf("GatewayMaps: ok=%v err=%v", ok, err)
	}
	if maps.Source != target {
		t.Errorf("maps source = %s, want %s", maps.Source, target)
	}
}

// TestGatewayMapsSeparatesAbsentFromUnreadable is the whole point of finding
// 4: os.ErrNotExist means "coconut-proxy has not deployed yet" and falls
// through to the legacy file; EACCES means the live routing is THERE and this
// account cannot read it, and continuing on the legacy file answered a
// question about a file nginx does not serve.
func TestGatewayMapsSeparatesAbsentFromUnreadable(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root reads an 0000 file")
	}
	dir := proxyTree(t)
	inc := filepath.Join(dir, "conf.d", "05-tenants.generated.conf")
	write(t, inc, includeWithLists)
	write(t, filepath.Join(dir, "conf.d", "00-maps.conf"), `map $tenant $tenant_api {
    default  "";
    legacy  "127.0.0.1:8000";
}
`)
	if err := os.Chmod(inc, 0o000); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chmod(inc, 0o600) })

	maps, ok, err := GatewayMaps(dir)
	if err == nil {
		t.Fatalf("an unreadable include was reported as ok=%v, maps from %s", ok, maps.Source)
	}
	if !errors.Is(err, os.ErrPermission) {
		t.Errorf("err = %v, want the permission error surfaced", err)
	}
	if ok {
		t.Error("ok must be false when the read failed")
	}
	if _, derr := DisplayOrder(dir); derr == nil {
		t.Error("DisplayOrder swallowed the read error and returned an order anyway")
	}
}

// TestUIDistOKWantsARegularFile: a directory named index.html, or a dangling
// symlink, is a 404 from nginx just as surely as an absent file.
func TestUIDistOKWantsARegularFile(t *testing.T) {
	dir := t.TempDir()
	if StaticUIDistOK(dir) {
		t.Error("an absent dist passed")
	}
	if err := os.MkdirAll(UIDistIndex(dir), 0o750); err != nil {
		t.Fatal(err)
	}
	if StaticUIDistOK(dir) {
		t.Error("a DIRECTORY named index.html passed")
	}
	if err := os.RemoveAll(UIDistIndex(dir)); err != nil {
		t.Fatal(err)
	}
	write(t, UIDistIndex(dir), "<html></html>")
	if !StaticUIDistOK(dir) {
		t.Error("a real index.html failed")
	}
}

// TestStaticUIDistIsRecheckedOnEveryDoctorPass: adopt checks the dist ONCE, at
// adoption. It is a build artifact — a `git clean`, a rebuild, a handover all
// remove it long afterwards — and nginx keeps pointing its alias block at the
// hole. doctor has to ask again, with the same code adopt raises.
func TestStaticUIDistIsRecheckedOnEveryDoctorPass(t *testing.T) {
	w := newWorld(t)
	w.tenant.UI = registry.UI{Mode: registry.UIModeStatic, Base: "/ragstack/dev/ui/"}

	got := byCode(w.run(t))
	f, ok := got[UIDistMissing]
	if !ok {
		t.Fatalf("a static tenant with no dist raised no %s", UIDistMissing)
	}
	if f.Level != model.LevelError {
		t.Errorf("level = %s, want error", f.Level)
	}
	if !strings.Contains(f.Detail, UIDistIndex(w.tenant.DataDir)) {
		t.Errorf("the finding does not name the file: %s", f.Detail)
	}
	if !strings.Contains(f.Detail, w.tenant.UI.Base) {
		t.Errorf("the finding does not name the mount: %s", f.Detail)
	}

	// Build it, and the finding goes away.
	if err := os.MkdirAll(filepath.Dir(UIDistIndex(w.tenant.DataDir)), 0o750); err != nil {
		t.Fatal(err)
	}
	write(t, UIDistIndex(w.tenant.DataDir), "<html></html>")
	if _, still := byCode(w.run(t))[UIDistMissing]; still {
		t.Error("the finding survived a built dist")
	}

	// And a dev-mode tenant is never asked.
	w.tenant.UI = registry.UI{Mode: registry.UIModeDev, Port: 8090, Base: "/ragstack/dev/ui/"}
	if err := os.RemoveAll(filepath.Join(w.tenant.DataDir, "ui")); err != nil {
		t.Fatal(err)
	}
	if _, raised := byCode(w.run(t))[UIDistMissing]; raised {
		t.Errorf("%s raised for a dev-mode UI", UIDistMissing)
	}
}
