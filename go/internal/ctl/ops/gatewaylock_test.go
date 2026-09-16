package ops

import (
	"context"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/model"
)

// Every plan that carries a gateway step must declare the gateway lock: the
// real gateway driver publishes with LockHeld, trusting that the engine took
// the gateway package's lock file as part of the fleet lock order. A plan
// with an nginx step and no LockGateway would publish with no lock at all.
func TestEveryPlanWithAGatewayStepDeclaresTheGatewayLock(t *testing.T) {
	reg := NewRegistry(Deps{})
	for _, verb := range reg.Verbs() {
		verb := verb
		t.Run(verb, func(t *testing.T) {
			oc, _ := fixture(t, "dev", managed)
			o, _ := reg.Lookup(verb)
			args := minimalArgs(verb)
			planned, err := o.Plan(context.Background(), oc, args)
			if err != nil {
				t.Skipf("%s does not plan on the fixture with minimal args: %v", verb, err)
			}
			hasNginx := false
			for _, st := range planned.Steps {
				if st.Plan.Kind == "nginx" {
					hasNginx = true
				}
			}
			if !hasNginx {
				return
			}
			for _, l := range planned.Locks {
				if l == model.LockGateway {
					return
				}
			}
			t.Fatalf("%s plans a gateway step but does not declare LockGateway (locks %v)", verb, planned.Locks)
		})
	}
}

// minimalArgs is the smallest args object each verb accepts, for the
// invariant above. A verb absent here plans with none.
func minimalArgs(verb string) map[string]any {
	switch verb {
	case "backup":
		return map[string]any{"fence": true}
	case "restore":
		return map[string]any{"from": "20260914T093000Z-backup", "as": "dev-r"}
	case "create":
		return map[string]any{"name": "newone", "artifact_id": "v1.5.3-abababababab"}
	case "create-sandbox":
		// A name the SANDBOX rule accepts: `create-sandbox` refuses every name
		// that is not `ctltest-…`, so the shared "newone" made this verb skip
		// the invariant instead of being covered by it.
		return map[string]any{"name": "ctltest-20260914t000000z", "artifact_id": "v1.5.3-abababababab"}
	case "key-mint":
		return map[string]any{"label": "ops", "role": "user"}
	case "key-revoke":
		return map[string]any{"id": "k1"}
	case "admin-add", "admin-remove":
		return map[string]any{"subject": "bvbrc:x@example.org"}
	case "sa-create":
		return map[string]any{"subject": "svc", "role": "user", "purpose": "t"}
	case "sa-disable", "sa-enable":
		return map[string]any{"subject": "svc"}
	case "env-set":
		return map[string]any{"key": "LOG_LEVEL", "value": "info"}
	case "env-unset":
		return map[string]any{"key": "LOG_LEVEL"}
	case "update-code":
		return map[string]any{"artifact_id": "v1.5.3-abababababab"}
	case "artifact-prepare":
		return map[string]any{"tag": "main"}
	case "settings-put":
		return map[string]any{"ctl": map[string]any{"gateway_enabled": true}}
	case "set-ui-mode":
		// `static` is the direction that publishes a generation, so it is the
		// one this invariant has to see. A verb left out here plans with no
		// args, fails Validate and SKIPS — which is how a gateway step can
		// acquire no lock and no test.
		return map[string]any{"mode": "static"}
	case "set-bind":
		return map[string]any{"bind": "127.0.0.1"}
	}
	return map[string]any{}
}
