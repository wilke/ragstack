package drivers

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/gateway"
	"github.com/ragstack/ragstack/internal/ctl/paths"
)

// TestTheEngineDriverSetCanProbeTheGateway is the regression for the PR-E2
// blocker.
//
// `api.BuildEngine` constructs `drivers.NewReal` and did not pass a BaseURL,
// while `RealGateway.Probe` refused an empty one — so every gateway probe
// through the ENGINE's driver set failed on the host. Two ops end in one: the
// handover take's last post-check and `set-ui-mode`'s proof. Both ran AFTER
// the tenant had been started and BEFORE the registry write, so the failure
// rolled the job back over a tenant that was working.
//
// The fix is the one the option's own documentation already promised ("Empty
// values take the gateway package's defaults").
func TestTheEngineDriverSetCanProbeTheGateway(t *testing.T) {
	// Exactly the fields api/engine.go passes that touch the gateway — which
	// is to say, none of them.
	d := NewReal(RealOptions{Roots: paths.NewRoots("/rag", paths.Overrides{})})
	_, err := d.Gateway().Probe(context.Background(), "/ragstack/hackathon/api/health")
	if err != nil && strings.Contains(err.Error(), "no gateway base URL") {
		t.Fatalf("the driver still refuses to probe without an explicit base URL: %v", err)
	}
	// Anything else is this host's answer to a real request (usually
	// "connection refused" when no gateway is running), which is not what this
	// test is about.
}

// And the default is the deployment's gateway, not some other port: a probe
// that silently dialled the wrong server would report a healthy tenant behind
// a gateway nobody is using.
func TestTheGatewayProbeDefaultsToTheDeploymentsGateway(t *testing.T) {
	if gateway.DefaultBaseURL != "http://127.0.0.1:9000" {
		t.Fatalf("gateway.DefaultBaseURL = %q, which is not coconut's proxy", gateway.DefaultBaseURL)
	}
	var got string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = r.URL.Path
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()
	// An EXPLICIT base URL still wins, which is what CTL_GATEWAY_BASE_URL is
	// for on a host whose gateway is not on :9000.
	d := NewReal(RealOptions{Roots: paths.NewRoots("/rag", paths.Overrides{}), BaseURL: srv.URL})
	status, err := d.Gateway().Probe(context.Background(), "/ragstack/dev/api/health")
	if err != nil {
		t.Fatalf("Probe: %v", err)
	}
	if status != 200 || got != "/ragstack/dev/api/health" {
		t.Fatalf("status %d, path %q", status, got)
	}
}
