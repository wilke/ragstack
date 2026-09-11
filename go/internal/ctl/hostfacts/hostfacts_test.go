package hostfacts

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"syscall"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/paths"
)

// TestParseNetTCP reads the two real /proc/net/tcp shapes: an IPv4 listener
// with a little-endian address, an IPv6 wildcard, and a non-LISTEN row that
// must be ignored.
func TestParseNetTCP(t *testing.T) {
	v4 := []byte(`  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:5DF6 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 123456 1 0000000000000000 100 0 0 10 0
   1: 00000000:5DE8 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 123457 1 0000000000000000 100 0 0 10 0
   2: 0100007F:8000 0100007F:9000 01 00000000:00000000 00:00000000 00000000  1000        0 123458 1 0000000000000000 100 0 0 10 0
`)
	got := parseNetTCP(v4)
	if len(got) != 2 {
		t.Fatalf("parseNetTCP: %d rows, want 2 (established rows must be skipped): %+v", len(got), got)
	}
	if got[0].Addr != "127.0.0.1" || got[0].Port != 24054 || got[0].Inode != 123456 {
		t.Errorf("row 0 = %+v, want 127.0.0.1:24054 inode 123456", got[0])
	}
	if got[1].Addr != "0.0.0.0" || got[1].Port != 24040 {
		t.Errorf("row 1 = %+v, want 0.0.0.0:24040", got[1])
	}

	v6 := []byte(`  sl  local_address                         remote_address                        st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 00000000000000000000000000000000:1F90 00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 999 1 0000000000000000 100 0 0 10 0
`)
	got6 := parseNetTCP(v6)
	if len(got6) != 1 || got6[0].Addr != "::" || got6[0].Port != 8080 {
		t.Fatalf("ipv6 parse = %+v, want [:: 8080]", got6)
	}
}

// TestSafeEnvAllowed pins the allowlist against ops/coconut/snapshot.sh: the
// keys adopt needs pass, and a key that merely looks harmless but names a
// credential never does.
func TestSafeEnvAllowed(t *testing.T) {
	allowed := []string{"ES_JAVA_OPTS", "PYTHONPATH", "PORT", "HF_HOME", "HOME", "QDRANT__SERVICE__HTTP_PORT", "GOWE_URL", "NEO4J_server_bolt_listen__address"}
	for _, k := range allowed {
		if !SafeEnvAllowed(k) {
			t.Errorf("SafeEnvAllowed(%q) = false, want true", k)
		}
	}
	refused := []string{"GOWE_TOKEN", "API_KEYS", "NEO4J_AUTH", "TENANT_PG_PASSWORD", "AWS_SECRET_ACCESS_KEY", "LD_PRELOAD", "PATH"}
	for _, k := range refused {
		if SafeEnvAllowed(k) {
			t.Errorf("SafeEnvAllowed(%q) = true, want false", k)
		}
	}
}

// TestAllowedStoreURL is the SSRF guard: loopback http only, no userinfo, and
// a port that is either the tenant's own or a declared external store port.
func TestAllowedStoreURL(t *testing.T) {
	block := paths.Block(2) // dev: 24040-24045
	cases := []struct {
		url  string
		want bool
	}{
		{"http://localhost:24041", true},
		{"http://127.0.0.1:24043", true},
		{"http://localhost:6333", true},   // shared qdrant
		{"http://localhost:6343", true},   // qdrant2
		{"http://localhost:9200", true},   // shared ES
		{"http://localhost:24061", false}, // another tenant's block
		{"https://localhost:24041", false},
		{"http://evil.example.com:6333", false},
		{"http://127.0.0.1", false},
		{"", false},
		{"://nope", false},
		// An IPv6 loopback literal is loopback: refusing it pushed a
		// perfectly ordinary store URL into store_url_disallowed.
		{"http://[::1]:24041", true},
		{"http://[::1]:24061", false},
		// Userinfo is a CREDENTIAL. It satisfied every other test here —
		// http, loopback host, allowed port — so the daemon would have put
		// it on the wire and into its own error strings.
		{"http://u:p@localhost:9200", false},
		{"http://u:p@127.0.0.1:24041", false},
		{"http://elastic@localhost:9200", false},
	}
	for _, c := range cases {
		got, reason := AllowedStoreURL(c.url, block, DefaultExternalStorePorts)
		if got != c.want {
			t.Errorf("AllowedStoreURL(%q) = %v (%s), want %v", c.url, got, reason, c.want)
		}
		if !got && reason == "" {
			t.Errorf("AllowedStoreURL(%q): refused without a reason", c.url)
		}
	}
}

// TestWritableByOthers checks the three verdicts on a real tree: clean,
// world-writable, group-writable by a group that is not ragops (the test
// process's own group stands in — it is never ragops in CI).
func TestWritableByOthers(t *testing.T) {
	dir := t.TempDir()
	clean := filepath.Join(dir, "clean")
	if err := os.WriteFile(clean, []byte("x"), 0o640); err != nil {
		t.Fatal(err)
	}
	r := &Real{CheckRoot: dir} // the walk stops at the fixture root: /tmp itself is 1777
	r.init()
	r.ragopsGID = -2 // no group matches: every group-writable path is a finding

	if w, err := r.WritableByOthers(clean); err != nil || w.Writable {
		t.Errorf("clean file: %+v %v, want not writable", w, err)
	}
	world := filepath.Join(dir, "world")
	if err := os.WriteFile(world, []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(world, 0o666); err != nil { // umask would have eaten the mode
		t.Fatal(err)
	}
	w, err := r.WritableByOthers(world)
	if err != nil || !w.Writable || w.Path != world {
		t.Errorf("world-writable file: %+v %v", w, err)
	}
	grp := filepath.Join(dir, "grp")
	if err := os.WriteFile(grp, []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(grp, 0o660); err != nil {
		t.Fatal(err)
	}
	if w, err := r.WritableByOthers(grp); err != nil || !w.Writable {
		t.Errorf("group-writable file: %+v %v, want a finding", w, err)
	}
	// The same file is fine once its group IS ragops.
	if st, err := os.Stat(grp); err == nil {
		r.ragopsGID = int(statGID(t, st))
		if w, err := r.WritableByOthers(grp); err != nil || w.Writable {
			t.Errorf("group-writable by ragops: %+v %v, want no finding", w, err)
		}
	}
	if _, err := r.WritableByOthers("relative/path"); err == nil {
		t.Error("WritableByOthers accepted a relative path")
	}
	// A clean leaf under a world-writable ancestor is still a finding, and the
	// finding names the ancestor.
	nested := filepath.Join(dir, "openbox", "leaf")
	if err := os.MkdirAll(filepath.Dir(nested), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(filepath.Dir(nested), 0o777); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(nested, []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	if w, err := r.WritableByOthers(nested); err != nil || !w.Writable || w.Path != filepath.Dir(nested) {
		t.Errorf("leaf under a world-writable dir: %+v %v, want the directory flagged", w, err)
	}
}

// TestGitdirClassification covers the three locations a worktree's gitdir can
// be in, through both the directory and the `gitdir:` pointer-file form.
func TestGitdirClassification(t *testing.T) {
	root := t.TempDir()
	mirror := filepath.Join(root, "repos", "ragstack.git")
	artifacts := filepath.Join(root, "data", "ctl", "artifacts")
	r := &Real{MirrorDir: mirror, ArtifactsDir: artifacts}

	mk := func(name, content string) string {
		wt := filepath.Join(root, name)
		if err := os.MkdirAll(wt, 0o755); err != nil {
			t.Fatal(err)
		}
		if content == "" {
			if err := os.MkdirAll(filepath.Join(wt, ".git"), 0o755); err != nil {
				t.Fatal(err)
			}
		} else if err := os.WriteFile(filepath.Join(wt, ".git"), []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
		return wt
	}
	cases := []struct {
		name, content, want string
	}{
		{"from-mirror", "gitdir: " + filepath.Join(mirror, "worktrees", "dev") + "\n", GitdirMirror},
		{"from-artifact", "gitdir: " + filepath.Join(artifacts, "a1", "worktree", ".git") + "\n", GitdirArtifact},
		{"from-home", "gitdir: /home/wilke/Development/ragstack/.git\n", GitdirOutside},
		{"own-clone", "", GitdirOutside},
		{"garbage", "not a gitdir pointer\n", GitdirUnreadable},
	}
	for _, c := range cases {
		wt := mk(c.name, c.content)
		g, err := r.Gitdir(wt)
		if err != nil {
			t.Fatalf("%s: %v", c.name, err)
		}
		if g.Location != c.want {
			t.Errorf("%s: location %q, want %q (path %q)", c.name, g.Location, c.want, g.Path)
		}
	}
	if g, _ := r.Gitdir(filepath.Join(root, "no-such-worktree")); g.Location != GitdirUnreadable {
		t.Errorf("missing worktree: %+v, want unreadable", g)
	}
}

// TestProbeRefusesRedirects proves the probe reports a redirect instead of
// following it, and that it only ever issues GET.
func TestProbeRefusesRedirects(t *testing.T) {
	var methods []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		methods = append(methods, r.Method)
		switch r.URL.Path {
		case "/health":
			w.WriteHeader(http.StatusOK)
		case "/collections":
			_, _ = w.Write([]byte(`{"result":{"collections":[{"name":"a"},{"name":"b"}]}}`))
		case "/_cat/indices":
			_, _ = w.Write([]byte(`[{"index":"i1"}]`))
		default:
			http.Redirect(w, r, "/health", http.StatusFound)
		}
	}))
	defer srv.Close()

	p := NewProber()
	ctx := context.Background()
	if code, err := p.Probe(ctx, srv.URL+"/health"); err != nil || code != 200 {
		t.Fatalf("probe /health = %d %v", code, err)
	}
	if code, err := p.Probe(ctx, srv.URL+"/elsewhere"); err != nil || code != 302 {
		t.Fatalf("probe redirect = %d %v, want 302 and no follow", code, err)
	}
	col, err := p.QdrantCollections(ctx, srv.URL)
	if err != nil || col.Count != 2 || col.Names[0] != "a" {
		t.Fatalf("QdrantCollections = %+v %v", col, err)
	}
	idx, err := p.ESIndices(ctx, srv.URL)
	if err != nil || idx.Count != 1 || idx.Names[0] != "i1" {
		t.Fatalf("ESIndices = %+v %v", idx, err)
	}
	for _, m := range methods {
		if m != http.MethodGet {
			t.Fatalf("probe issued a %s request", m)
		}
	}
	if _, err := p.Probe(ctx, "http://127.0.0.1:1/nothing"); err == nil {
		t.Error("probe of a dead port returned no error")
	}
}

// TestLoadFake reads the 2026-09-10 capture and checks the joins the adopt
// tests depend on: port → pid → owner → SAFE_ENV.
func TestLoadFake(t *testing.T) {
	dir := "../testdata/live-2026-09-10"
	if _, err := os.Stat(dir); err != nil {
		t.Skip("no capture fixture")
	}
	f, err := LoadFake(dir)
	if err != nil {
		t.Fatal(err)
	}
	byPort := map[int]Listener{}
	for _, l := range f.Ports {
		byPort[l.Port] = l
	}
	api, ok := byPort[24040]
	if !ok || api.User != "wilke" || api.Cwd != "/rag/repos/tenants/dev/python" {
		t.Fatalf("dev API listener = %+v", api)
	}
	es, ok := byPort[24043]
	if !ok {
		t.Fatal("no listener recorded on the dev ES port")
	}
	env, err := f.ProcEnv(es.Pid, SafeEnvAllowed)
	if err != nil {
		t.Fatal(err)
	}
	if env["ES_JAVA_OPTS"] != "-Xms1g -Xmx1g" {
		t.Errorf("dev ES heap env = %q, want -Xms1g -Xmx1g", env["ES_JAVA_OPTS"])
	}
	if l := byPort[9000]; l.Pid != 0 || l.User != "" {
		t.Errorf("the nginx listener is owned by another account and must stay unattributed: %+v", l)
	}
	if l := byPort[24003]; l.Addr != "0.0.0.0" {
		t.Errorf("listen.txt `*:24003` should normalise to 0.0.0.0, got %q", l.Addr)
	}
}

// statGID is the owning gid of fi (test helper: os.FileInfo does not expose
// it portably, and this package is Linux-only anyway).
func statGID(t *testing.T, fi os.FileInfo) uint32 {
	t.Helper()
	st, ok := fi.Sys().(*syscall.Stat_t)
	if !ok {
		t.Fatalf("no Stat_t for %s", fi.Name())
	}
	return st.Gid
}
