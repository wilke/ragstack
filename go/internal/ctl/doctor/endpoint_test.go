package doctor

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/settings"
)

// endpoint_host_not_allowed (#714): a warn per URL setting whose host is
// outside CTL_ALLOWED_ENDPOINT_HOSTS — single URLs, list elements and route
// values — read from tenant.env, empty values skipped; the allowlist comes
// from the option, else the environment, else ctl.env, else the default.
func TestEndpointHostNotAllowed(t *testing.T) {
	t.Setenv(settings.EnvAllowedEndpointHosts, "")
	w := newWorld(t)
	write(t, filepath.Join(w.tenant.DataDir, "config", "tenant.env"), cleanEnv+
		"LLM_ENDPOINT=http://mango.cels.anl.gov:8003\n"+
		"GOWE_URL=\n"+
		"EMBEDDING_ENDPOINTS=http://127.0.0.1:9001,http://evil.example:9002\n"+
		"WORKSPACE_URL=https://p3.theseed.org/services/Workspace\n"+
		`QDRANT_COLLECTION_ROUTES='{"big":"http://LOCALHOST:6343"}'`+"\n")
	hosts := func() []string {
		var out []string
		for _, f := range findingsForCode(w.run(t), EndpointHostNotAllowed) {
			if f.Level != model.LevelWarn {
				t.Errorf("level %s, want warn", f.Level)
			}
			out = append(out, f.Detail)
		}
		return out
	}
	// The default allowlist: loopback and this host. mango, evil and p3 are
	// outside it; the empty GOWE_URL and the loopback route are not reported.
	got := hosts()
	if len(got) != 3 {
		t.Fatalf("default allowlist: %d findings, want 3: %v", len(got), got)
	}
	all := strings.Join(got, "\n")
	for _, want := range []string{`LLM_ENDPOINT names host "mango.cels.anl.gov"`,
		`EMBEDDING_ENDPOINTS names host "evil.example"`, `WORKSPACE_URL names host "p3.theseed.org"`} {
		if !strings.Contains(all, want) {
			t.Errorf("no %q in:\n%s", want, all)
		}
	}
	// The option — the daemon's resolved allowlist.
	w.opts.AllowedEndpointHosts = []string{"127.0.0.1", "localhost", "mango.cels.anl.gov", "p3.theseed.org"}
	if got := hosts(); len(got) != 1 || !strings.Contains(got[0], "evil.example") {
		t.Errorf("with mango and p3 allowed: %v", got)
	}
	// ctl.env, when neither the option nor the environment says: a CLI doctor
	// run through ctl-as-svc.sh sees the daemon's allowlist.
	w.opts.AllowedEndpointHosts = nil
	if err := os.MkdirAll(w.roots.CtlConfigDir, 0o755); err != nil {
		t.Fatal(err)
	}
	write(t, filepath.Join(w.roots.CtlConfigDir, "ctl.env"),
		"CTL_LISTEN=127.0.0.1:23990\nCTL_ALLOWED_ENDPOINT_HOSTS=localhost,127.0.0.1,mango.cels.anl.gov,p3.theseed.org,evil.example\n")
	if got := hosts(); len(got) != 0 {
		t.Errorf("with ctl.env allowing all three: %v", got)
	}
	// The environment wins over ctl.env.
	t.Setenv(settings.EnvAllowedEndpointHosts, "localhost,127.0.0.1")
	if got := hosts(); len(got) != 3 {
		t.Errorf("with the environment narrowing it: %v", got)
	}
}
