package main

// The per-verb argument mapping. These tests assert on the `args` object the
// fake daemon RECEIVED, not on anything the CLI printed, because args is the
// only per-verb knowledge in this binary and a silent divergence from
// x-ctl-op-args is exactly the bug that survives every other kind of test: the
// request is well-formed, the daemon answers 422 or — worse — plans something
// the operator did not ask for.

import (
	"net/http"
	"reflect"
	"strings"
	"testing"
)

// opArgsCase is one subverb and the args x-ctl-op-args says it produces.
type opArgsCase struct {
	name     string
	argv     []string
	wantPath string
	wantArgs map[string]any
}

func runOpArgsCases(t *testing.T, cases []opArgsCase) {
	t.Helper()
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) { replyPlan(w, samplePlan("op", "dev")) })
			argv := append(append([]string{}, c.argv...), "--server", f.srv.URL, "--dry-run")
			rc, _, errs := capture(t, argv...)
			if rc != exitOK {
				t.Fatalf("rc %d (want 0) %s", rc, errs)
			}
			last := f.last(t)
			if last.Method != http.MethodPost || last.Path != c.wantPath {
				t.Errorf("%s %s, want POST %s", last.Method, last.Path, c.wantPath)
			}
			if got := last.args(t); !reflect.DeepEqual(got, c.wantArgs) {
				t.Errorf("args %#v, want %#v", got, c.wantArgs)
			}
		})
	}
}

// The six tenant operations, mapped onto x-ctl-op-args[start|stop|restart|
// backup|restore|decommission].
func TestTenantOpArgsMatchTheContract(t *testing.T) {
	runOpArgsCases(t, []opArgsCase{
		{"start with nothing stated", []string{"tenant", "start", "dev"},
			"/v1/tenants/dev/ops/start", map[string]any{}},
		{"start with a comma list and force", []string{"tenant", "start", "dev", "--only", "api,ui", "--force"},
			"/v1/tenants/dev/ops/start", map[string]any{"only": []any{"api", "ui"}, "force": true}},
		{"start with a repeated only", []string{"tenant", "start", "dev", "--only", "qdrant", "--only", "es"},
			"/v1/tenants/dev/ops/start", map[string]any{"only": []any{"qdrant", "es"}}},
		{"only is deduplicated", []string{"tenant", "restart", "dev", "--only", "api,api"},
			"/v1/tenants/dev/ops/restart", map[string]any{"only": []any{"api"}}},
		{"stop keeps desired_boot when asked", []string{"tenant", "stop", "dev", "--keep-enabled"},
			"/v1/tenants/dev/ops/stop", map[string]any{"keep_enabled": true}},
		{"stop over running ingest", []string{"tenant", "stop", "dev", "--force"},
			"/v1/tenants/dev/ops/stop", map[string]any{"force": true}},
		{"backup fenced and tarred", []string{"tenant", "backup", "dev", "--fence", "--tar"},
			"/v1/tenants/dev/ops/backup", map[string]any{"fence": true, "tar": true}},
		{"restore names the bundle and the fresh tenant",
			[]string{"tenant", "restore", "asm-next", "--from", "20260914T101500Z-backup", "--as", "asm-restore"},
			"/v1/tenants/asm-next/ops/restore",
			map[string]any{"from": "20260914T101500Z-backup", "as": "asm-restore"}},
		{"decommission takes nothing", []string{"tenant", "decommission", "dev"},
			"/v1/tenants/dev/ops/decommission", map[string]any{}},
	})
}

// `backup` without --fence must send NO `fence` member. The schema gives it
// `default: false`, so an absent member and `false` mean the same thing to the
// engine — but a CLI that always sent `false` would put a value nobody chose
// into the plan hash and the audit row, and an operator reading that row could
// not tell a deliberate unfenced backup from a default.
func TestTenantBackupSendsFenceOnlyWhenAsked(t *testing.T) {
	f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) { replyPlan(w, samplePlan("backup", "dev")) })

	if rc, _, errs := capture(t, "tenant", "backup", "dev", "--server", f.srv.URL, "--dry-run"); rc != exitOK {
		t.Fatalf("rc %d %s", rc, errs)
	}
	args := f.last(t).args(t)
	if _, ok := args["fence"]; ok {
		t.Errorf("an unstated --fence was sent anyway: %#v", args)
	}
	if _, ok := args["tar"]; ok {
		t.Errorf("an unstated --tar was sent anyway: %#v", args)
	}
}

// The identity and configuration groups map onto the tenant ops route with
// the hyphenated verbs (`key mint` → key-mint, `env set` → env-set, …).
func TestIdentityAndEnvOpArgsMatchTheContract(t *testing.T) {
	runOpArgsCases(t, []opArgsCase{
		{"key mint", []string{"key", "mint", "dev", "ci-runner", "--role", "user"},
			"/v1/tenants/dev/ops/key-mint", map[string]any{"label": "ci-runner", "role": "user"}},
		{"key mint with a restart", []string{"key", "mint", "dev", "ci-runner", "--role", "admin", "--restart"},
			"/v1/tenants/dev/ops/key-mint", map[string]any{"label": "ci-runner", "role": "admin", "restart": true}},
		{"key revoke", []string{"key", "revoke", "dev", "ci-runner"},
			"/v1/tenants/dev/ops/key-revoke", map[string]any{"id": "ci-runner"}},
		{"admin add", []string{"admin", "add", "dev", "bvbrc:alice@patricbrc.org"},
			"/v1/tenants/dev/ops/admin-add", map[string]any{"subject": "bvbrc:alice@patricbrc.org"}},
		{"admin remove", []string{"admin", "remove", "dev", "bvbrc:alice@patricbrc.org"},
			"/v1/tenants/dev/ops/admin-remove", map[string]any{"subject": "bvbrc:alice@patricbrc.org"}},
		{"sa create", []string{"sa", "create", "dev", "ingest", "--role", "user"},
			"/v1/tenants/dev/ops/sa-create", map[string]any{"subject": "ingest", "role": "user"}},
		{"sa create with a purpose", []string{"sa", "create", "dev", "ingest", "--role", "user", "--purpose", "nightly ingest"},
			"/v1/tenants/dev/ops/sa-create",
			map[string]any{"subject": "ingest", "role": "user", "purpose": "nightly ingest"}},
		{"sa disable", []string{"sa", "disable", "dev", "ingest"},
			"/v1/tenants/dev/ops/sa-disable", map[string]any{"subject": "ingest"}},
		{"sa enable", []string{"sa", "enable", "dev", "ingest"},
			"/v1/tenants/dev/ops/sa-enable", map[string]any{"subject": "ingest"}},
		{"env set", []string{"env", "set", "dev", "RAG_LOG_LEVEL", "debug"},
			"/v1/tenants/dev/ops/env-set", map[string]any{"key": "RAG_LOG_LEVEL", "value": "debug"}},
		{"env unset", []string{"env", "unset", "dev", "RAG_LOG_LEVEL"},
			"/v1/tenants/dev/ops/env-unset", map[string]any{"key": "RAG_LOG_LEVEL"}},
		{"env normalize", []string{"env", "normalize", "dev"},
			"/v1/tenants/dev/ops/env-normalize", map[string]any{}},
	})
}

// `units render` and `units diff` are the SAME operation with apply false;
// only `units apply` writes. The boolean is decided by the subverb rather than
// by a flag, so there is no spelling of `units diff` that writes units.
func TestUnitsArgsCarryTheApplyBoolean(t *testing.T) {
	runOpArgsCases(t, []opArgsCase{
		{"render plans only", []string{"units", "render", "dev"},
			"/v1/tenants/dev/ops/render-units", map[string]any{"apply": false}},
		{"diff plans only", []string{"units", "diff", "dev"},
			"/v1/tenants/dev/ops/render-units", map[string]any{"apply": false}},
		{"apply writes", []string{"units", "apply", "dev"},
			"/v1/tenants/dev/ops/render-units", map[string]any{"apply": true}},
	})
}

// `gateway reload` is an OPERATION, submitted to the daemon with an empty
// args object. It is the only way to adopt a hand-written proxy change, and it
// must not acquire arguments of its own: an arg that selected what to reload
// would be a way to publish a generation nobody previewed.
func TestGatewayReloadPostsAnEmptyArgsObject(t *testing.T) {
	runOpArgsCases(t, []opArgsCase{
		{"reload", []string{"gateway", "reload"}, "/v1/gateway/reload", map[string]any{}},
	})
}

// Flags may be written before or after the positionals. Go's flag package
// stops at the first non-flag, so a command that only read leading positionals
// would silently drop the tenant name in `env set --server U dev K V`.
func TestPositionalsAreAcceptedOnEitherSideOfTheFlags(t *testing.T) {
	f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) { replyPlan(w, samplePlan("env-set", "dev")) })

	rc, _, errs := capture(t, "env", "set", "--server", f.srv.URL, "--dry-run", "dev", "RAG_LOG_LEVEL", "debug")
	if rc != exitOK {
		t.Fatalf("rc %d %s", rc, errs)
	}
	last := f.last(t)
	if last.Path != "/v1/tenants/dev/ops/env-set" {
		t.Errorf("path %s", last.Path)
	}
	if got := last.args(t); !reflect.DeepEqual(got, map[string]any{"key": "RAG_LOG_LEVEL", "value": "debug"}) {
		t.Errorf("args %#v", got)
	}
}

// An unknown subverb and a missing positional are usage errors (exit 2) with a
// usage line, and neither reaches the daemon. A CLI that posted a half-built
// args object instead would turn a typo into a 422 the operator has to decode.
func TestUnknownSubverbsAndMissingArgumentsAreUsageErrors(t *testing.T) {
	cases := [][]string{
		{"key"},
		{"key", "bogus", "dev"},
		{"key", "mint", "dev"},              // no label
		{"key", "mint", "dev", "ci-runner"}, // no --role
		{"key", "revoke", "dev"},
		{"admin"},
		{"admin", "bogus", "dev", "x"},
		{"admin", "add", "dev"},
		{"sa"},
		{"sa", "bogus", "dev", "x"},
		{"sa", "create", "dev", "ingest"}, // no --role
		{"sa", "disable", "dev"},
		{"env"},
		{"env", "bogus", "dev"},
		{"env", "set", "dev", "RAG_LOG_LEVEL"}, // no value
		{"env", "unset", "dev"},
		{"env", "normalize"},
		{"units"},
		{"units", "bogus", "dev"},
		{"units", "apply"},
		{"tenant", "restore", "dev"}, // no --from/--as
		{"tenant", "restore", "dev", "--from", "20260914T101500Z-backup"}, // no --as
		{"tenant", "backup"},
		{"job", "bogus"},
		{"job", "show"},
		{"job", "log", "01JB0000000000000000000000"},
		{"job", "log", "01JB0000000000000000000000", "zero"},
	}
	for _, argv := range cases {
		t.Run(strings.Join(argv, " "), func(t *testing.T) {
			f := newFakeCtl(t, func(w http.ResponseWriter, rec recorded) { replyPlan(w, samplePlan("op", "dev")) })
			argv := append(append([]string{}, argv...), "--server", f.srv.URL)
			rc, _, errs := capture(t, argv...)
			if rc != exitUsage {
				t.Fatalf("rc %d (want 2) %s", rc, errs)
			}
			if !strings.Contains(strings.ToLower(errs), "usage") && !strings.Contains(errs, "unknown verb") {
				t.Errorf("no usage line on stderr: %s", errs)
			}
			if n := len(f.requests()); n != 0 {
				t.Errorf("%d request(s) reached the daemon for a usage error", n)
			}
		})
	}
}

// `tenant list|show|logs` are reads and must keep working exactly as they did
// when the operation verbs joined the group: a dispatch that swallowed every
// `tenant <x>` into the op path would break the three commands PR-A shipped.
func TestTenantReadsStillWorkBesideTheOperations(t *testing.T) {
	_, reg := scratchRegistry(t)
	if rc, out, errs := capture(t, "tenant", "list", "--registry", reg); rc != exitOK || !strings.Contains(out, "TENANT") {
		t.Errorf("tenant list: rc %d %s\n%s", rc, errs, out)
	}
	if rc, _, _ := capture(t, "tenant", "bogus", "--registry", reg); rc != exitUsage {
		t.Errorf("an unknown tenant verb should still be a usage error, got %d", rc)
	}
}
