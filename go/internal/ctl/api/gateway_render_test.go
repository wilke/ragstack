package api

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/auth"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/ratelimit"
	"github.com/ragstack/ragstack/internal/ctl/session"
)

// failingGatewayBackend is a backend that CAN read the published gateway state
// (so the handler takes the gatewayBackend branch) and fails the way the live
// one does.
type failingGatewayBackend struct {
	*FakeBackend
	diffErr error
}

func (b *failingGatewayBackend) GatewayStatus(context.Context) (*model.GatewayStatus, error) {
	return &model.GatewayStatus{TxnState: "none", Routes: []model.GatewayRoute{}, LegacyRoutes: []model.GatewayRoute{}}, nil
}

func (b *failingGatewayBackend) GatewayDiff(context.Context) (*model.GatewayRenderResponse, error) {
	return nil, b.diffErr
}

func routerWithBackend(t *testing.T, b Backend) http.Handler {
	t.Helper()
	envs := map[string]string{
		auth.EnvAPIKeys:     `["` + opKey + `","` + viewerKey + `"]`,
		auth.EnvAPIKeyRoles: `{"` + opKey + `":"operator","` + viewerKey + `":"viewer"}`,
	}
	keys, err := auth.LoadKeys(func(k string) string { return envs[k] })
	if err != nil {
		t.Fatal(err)
	}
	verifier, err := auth.New(auth.Options{})
	if err != nil {
		t.Fatal(err)
	}
	sessions := session.NewMemoryStore()
	return NewRouter(&Server{
		Backend: b,
		Resolver: &auth.Resolver{
			Keys: keys, Verifier: verifier, Sessions: sessions,
			Limiter: ratelimit.New(ratelimit.Config{PerCredential: -1, TarpitAt: -1}),
			Reject:  reject,
		},
		Sessions: sessions,
	})
}

// POST /v1/gateway/render is a VIEWER operation. It used to answer a failure by
// concatenating whatever the host handed back: the registry path for a load
// failure (plus the offending line for a parse error), and absolute tenant
// paths for a render refusal. Neither belongs in a body read by an account whose
// whole permission is "look at the dashboard".
func TestGatewayRenderDoesNotLeakHostPathsToAViewer(t *testing.T) {
	t.Run("a registry that cannot be read says nothing at all", func(t *testing.T) {
		h := routerWithBackend(t, &failingGatewayBackend{
			FakeBackend: NewFakeBackend(),
			diffErr: fmt.Errorf("/rag/data/tenants/registry.json: invalid character '}' after object key:value pair, line 41: %s",
				`"api_keys": ["deadbeefdeadbeef"]}`),
		})
		w := do(t, h, http.MethodPost, "/v1/gateway/render", map[string]string{auth.HeaderAPIKey: viewerKey}, "")
		body := assertError(t, w, 500, "internal")
		msg, _ := body["detail"].(string)
		for _, leak := range []string{"/rag/", "registry.json", "line 41", "deadbeef"} {
			if strings.Contains(msg, leak) {
				t.Errorf("the body carries %q: %s", leak, msg)
			}
		}
	})

	t.Run("a render refusal is returned, path-redacted", func(t *testing.T) {
		h := routerWithBackend(t, &failingGatewayBackend{
			FakeBackend: NewFakeBackend(),
			diffErr:     fmt.Errorf("%w: tenant demo: /rag/data/tenants/demo/ui/dist escapes the root", ErrGatewayRender),
		})
		w := do(t, h, http.MethodPost, "/v1/gateway/render", map[string]string{auth.HeaderAPIKey: viewerKey}, "")
		body := assertError(t, w, 500, "internal")
		msg, _ := body["detail"].(string)
		if strings.Contains(msg, "/rag/") {
			t.Errorf("the body carries a host path: %s", msg)
		}
		// What the caller actually needs — which tenant, and what was wrong —
		// survives, or the redaction has made the error useless.
		if !strings.Contains(msg, "tenant demo") || !strings.Contains(msg, "escapes the root") {
			t.Errorf("the redaction removed the answer too: %s", msg)
		}
		if !strings.Contains(msg, "<path>") {
			t.Errorf("the redacted path is not marked as one: %s", msg)
		}
	})
}

func TestRedactHostPathsKeepsRelativePaths(t *testing.T) {
	cases := map[string]string{
		"gateway: rendering conf.d/05-tenants.generated.conf: bad": "gateway: rendering conf.d/05-tenants.generated.conf: bad",
		"open /rag/config/proxy/tls/proxy.key: denied":             "open <path>: denied",
		`ui_dist "/rag/data/ctl/ui/dist" is not a directory`:       `ui_dist "<path>" is not a directory`,
		"/rag/x and /rag/y": "<path> and <path>",
		"nothing to redact": "nothing to redact",
	}
	for in, want := range cases {
		if got := RedactHostPaths(in); got != want {
			t.Errorf("RedactHostPaths(%q)\n got %q\nwant %q", in, got, want)
		}
	}
	// And the marker is stable regardless of what came before the slash.
	if got := RedactHostPaths("/rag/a"); got != "<path>" {
		t.Errorf("a message that IS a path: %q", got)
	}
	if !errors.Is(fmt.Errorf("%w: x", ErrGatewayRender), ErrGatewayRender) {
		t.Error("ErrGatewayRender does not survive wrapping")
	}
}
