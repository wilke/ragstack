package ops

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/envfile"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// The #714 verbs: `env set-surface` / `env unset-surface`, the surface half of
// `create --set`, and the registry step `env set` / `env unset` gained.

// surfaceDeps is testDeps with coconut's surface configuration: the default
// bind roots and an allowlist that carries mango and p3 beside loopback.
func surfaceDeps(oc jobs.Context) Deps {
	d := testDeps(oc)
	d.APIBindRoots = []string{"/rag/cache", "/scout/containers", "/rag/config"}
	d.AllowedEndpointHosts = []string{"127.0.0.1", "::1", "localhost", "coconut", "mango.cels.anl.gov", "p3.theseed.org"}
	return d
}

func planSurface(t *testing.T, oc jobs.Context, verb string, args map[string]any) (*jobs.Planned, error) {
	t.Helper()
	op, ok := NewRegistry(surfaceDeps(oc)).Lookup(verb)
	if !ok {
		t.Fatalf("no op %q", verb)
	}
	return op.Plan(context.Background(), oc, args)
}

func direct(oc jobs.Context) jobs.Context {
	oc.Mode = model.WorkerDirect
	return oc
}

func setSurface(kv ...string) map[string]any {
	values := map[string]any{}
	for _, s := range kv {
		k, v, _ := strings.Cut(s, "=")
		values[k] = v
	}
	return map[string]any{"values": values}
}

// The plan-time gate: the ENGINE's mode. A daemon engine — or a Context with
// no mode at all — refuses both verbs, whatever the request says; a --direct
// one plans them.
func TestSurfaceVerbsPlanOnlyInADirectEngine(t *testing.T) {
	set := setSurface("LLM_ENDPOINT=http://mango.cels.anl.gov:8003")
	unset := map[string]any{"keys": []any{"QDRANT_URL"}}
	for _, mode := range []model.WorkerMode{model.WorkerDaemon, ""} {
		for verb, args := range map[string]map[string]any{"env-set-surface": set, "env-unset-surface": unset} {
			oc, _ := fixture(t, "dev", managed)
			oc.Mode = mode
			_, err := planSurface(t, oc, verb, args)
			if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "--direct") {
				t.Errorf("%s in mode %q = %v; want a refusal naming --direct", verb, mode, err)
			}
		}
	}
	oc, _ := fixture(t, "dev", managed)
	p, err := planSurface(t, direct(oc), "env-set-surface", set)
	if err != nil {
		t.Fatalf("direct: %v", err)
	}
	if len(p.Steps) != 2 || p.Steps[0].Plan.Kind != "envfile" || p.Steps[1].Plan.Kind != "registry" {
		t.Fatalf("steps = %v", titles(p))
	}
	if preview := string(p.Steps[0].Plan.WouldWrite[0].Preview); !strings.Contains(preview,
		"LLM_ENDPOINT=http://mango.cels.anl.gov:8003") {
		t.Errorf("the dry-run preview does not show KEY=VALUE:\n%s", preview)
	}
	for _, l := range []model.LockName{model.LockRegistry, model.LockTenant} {
		found := false
		for _, have := range p.Locks {
			found = found || have == l
		}
		if !found {
			t.Errorf("locks %v lack %s", p.Locks, l)
		}
	}
	if _, err := planSurface(t, direct(oc), "env-unset-surface", unset); err != nil {
		t.Fatalf("direct unset: %v", err)
	}
}

// The acceptance refusals of the brief, through the planner, each naming why.
func TestSetSurfaceRefusals(t *testing.T) {
	cases := []struct {
		kv   string
		want string
	}{
		{"LLM_ENDPOINT=http://evil.example/v1", "CTL_ALLOWED_ENDPOINT_HOSTS"},
		{"LLM_ENDPOINT=http://u:p@mango.cels.anl.gov:8003", "userinfo"},
		{"PROMPT_TEMPLATES_FILE=/rag/data/tenants/asm-next/secrets.env", "credential file"},
		{"PROMPT_TEMPLATES_FILE=/rag/data/tenants/asm-next/config/prompts.yaml", "asm-next"},
		{"COLLECTIONS_FILE=/rag/config/ctl/ctl-secrets.env", "credential file"},
		{"COLLECTIONS_FILE=/rag/data/ctl/registry.json", "ctl's state"},
		{"COLLECTIONS_FILE=/rag/backups/tenants/dev/x.json", "backup"},
		{"INGEST_ROOT=relative/ingest", "not absolute"},
		{"PYTHONPATH=/rag/data/tenants/dev/py", "unit-owned"},
		{"PORT=9999", "argv"},
		{"LOG_LEVEL=DEBUG", "PUBLIC"},
		{"API_KEYS=x", "secret-class"},
		{"NOT_A_SETTING=x", "not a known ragstack setting"},
		{"GOWE_TOOL_IMAGE=x", "ADR-0010"},
		// Representable by neither reader: the env grammar's own refusal,
		// found at plan time rather than after the backup was written.
		{`LLM_ENDPOINT=http://127.0.0.1:1/a'$b`, "cannot be written to tenant.env"},
	}
	for _, c := range cases {
		oc, _ := fixture(t, "dev", managed)
		_, err := planSurface(t, direct(oc), "env-set-surface", setSurface(c.kv))
		if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), c.want) {
			t.Errorf("%s = %v; want a refusal mentioning %q", c.kv, err, c.want)
		}
	}
	// Shape errors are validation (422), not refusals.
	for _, args := range []map[string]any{
		{"values": map[string]any{}},
		{"values": map[string]any{"lower": "x"}},
		{"values": map[string]any{"LLM_ENDPOINT": 7}},
		{"values": "LLM_ENDPOINT=x"},
	} {
		oc, _ := fixture(t, "dev", managed)
		if _, err := planSurface(t, direct(oc), "env-set-surface", args); !errors.Is(err, jobs.ErrValidation) {
			t.Errorf("%v = %v; want a validation error", args, err)
		}
	}
	// unset-surface takes surface keys only.
	oc, _ := fixture(t, "dev", managed)
	if _, err := planSurface(t, direct(oc), "env-unset-surface", map[string]any{"keys": []any{"LOG_LEVEL"}}); !errors.Is(err,
		jobs.ErrRefused) || !strings.Contains(err.Error(), "PUBLIC") {
		t.Errorf("unset-surface of a public key = %v", err)
	}
}

// A value holding BOTH quote kinds is representable (double-quoted, the `"`
// escaped), so it is accepted — and it reads back as exactly what was set.
func TestSetSurfaceBothQuoteKindsRoundTrips(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	oc = direct(oc)
	value := `http://127.0.0.1:1/a'b"c`
	p, err := planSurface(t, oc, "env-set-surface", setSurface("LLM_ENDPOINT="+value))
	if err != nil {
		t.Fatal(err)
	}
	newRunner(oc, fake).runAll(t, p)
	f, err := envfile.Parse(fake.FakeFiles().Content("/rag/data/tenants/dev/config/tenant.env"))
	if err != nil {
		t.Fatal(err)
	}
	if got, _ := f.Get("LLM_ENDPOINT"); got != value {
		t.Errorf("read back %q, want %q", got, value)
	}
}

// End to end on the fake host: the file is backed up and rewritten, the row
// records restart_pending and the new file's checksum, and settings{} never
// receives the surface key.
func TestSetSurfaceRunsAndRecordsTheRow(t *testing.T) {
	oc, fake := fixture(t, "dev", managed)
	oc = direct(oc)
	oc.Tenant.RestartPending = false
	p, err := planSurface(t, oc, "env-set-surface",
		setSurface("LLM_ENDPOINT=http://mango.cels.anl.gov:8003", "EMBEDDING_ENDPOINTS=http://127.0.0.1:9001, http://localhost:9002"))
	if err != nil {
		t.Fatal(err)
	}
	r := newRunner(oc, fake)
	r.runAll(t, p)
	path := "/rag/data/tenants/dev/config/tenant.env"
	body := fake.FakeFiles().Content(path)
	for _, want := range []string{"LLM_ENDPOINT=http://mango.cels.anl.gov:8003",
		"EMBEDDING_ENDPOINTS=http://127.0.0.1:9001,http://localhost:9002"} {
		if !strings.Contains(string(body), want) {
			t.Errorf("tenant.env lacks %q:\n%s", want, body)
		}
	}
	bak := false
	for _, f := range fake.FakeFiles().Paths() {
		bak = bak || strings.HasPrefix(f, path+".bak-env-set-surface-")
	}
	if !bak {
		t.Errorf("no .bak-env-set-surface-<ts> beside tenant.env: %v", fake.FakeFiles().Paths())
	}
	row := oc.Fleet.Tenants["dev"]
	if !row.RestartPending {
		t.Error("restart_pending was not set")
	}
	if row.EnvFileSHA256 != sha256Hex(body) {
		t.Errorf("env_file_sha256 = %s, want the hash of the written file %s", row.EnvFileSHA256, sha256Hex(body))
	}
	for _, k := range []string{"LLM_ENDPOINT", "EMBEDDING_ENDPOINTS"} {
		if _, ok := row.Settings[k]; ok {
			t.Errorf("settings{} received the surface key %s", k)
		}
	}
	if _, ok := row.LastOps["env-set-surface"]; ok {
		t.Error("last_ops gained a key outside the registry's OpVerb enum")
	}

	// unset-surface removes them again, with its own backup and the same row step.
	p, err = planSurface(t, oc, "env-unset-surface", map[string]any{"keys": []any{"LLM_ENDPOINT", "EMBEDDING_ENDPOINTS"}})
	if err != nil {
		t.Fatal(err)
	}
	if !p.Steps[0].Plan.Destructive {
		t.Error("unset-surface's edit is not marked destructive")
	}
	r.runAll(t, p)
	body = fake.FakeFiles().Content(path)
	if strings.Contains(string(body), "LLM_ENDPOINT") || strings.Contains(string(body), "EMBEDDING_ENDPOINTS") {
		t.Errorf("unset-surface left the keys:\n%s", body)
	}
	if row.EnvFileSHA256 != sha256Hex(body) {
		t.Error("unset-surface did not record the new checksum")
	}
}

// The backport: `env set` / `env unset` now end in the same registry step, so
// the row stops reporting the job's own edit as drift and says the change is
// pending a restart.
func TestEnvSetRecordsRestartPendingAndTheChecksum(t *testing.T) {
	for _, c := range []struct {
		verb string
		args map[string]any
	}{
		{"env-set", map[string]any{"key": "LOG_LEVEL", "value": "DEBUG"}},
		{"env-unset", map[string]any{"key": "LOG_LEVEL"}},
	} {
		oc, fake := fixture(t, "dev", managed)
		oc.Tenant.RestartPending = false
		before := oc.Tenant.EnvFileSHA256
		p := plan(t, oc, c.verb, c.args)
		newRunner(oc, fake).runAll(t, p)
		body := fake.FakeFiles().Content("/rag/data/tenants/dev/config/tenant.env")
		row := oc.Fleet.Tenants["dev"]
		if !row.RestartPending {
			t.Errorf("%s: restart_pending stayed false", c.verb)
		}
		if row.EnvFileSHA256 != sha256Hex(body) || row.EnvFileSHA256 == before {
			t.Errorf("%s: env_file_sha256 = %s, want %s (was %s)", c.verb, row.EnvFileSHA256, sha256Hex(body), before)
		}
		if _, ok := row.LastOps[c.verb]; !ok {
			t.Errorf("%s: last_ops[%s] was not recorded", c.verb, c.verb)
		}
	}
}

// An image-mode row: the API instance's bind derivation and path probe run
// over the edited environment, so a sqlite store in a READ-ONLY bind — a
// path the path rule alone accepts, /rag/config being a bind root — is refused
// at plan time. The CWL keys are refused there (the image overrides them).
func TestSetSurfaceOnAnImageRow(t *testing.T) {
	oc, _ := imageRowFixture(t, devImageEnv())
	oc = direct(oc)
	_, err := planSurface(t, oc, "env-set-surface", setSurface("JOB_STORE_PATH=/rag/config/jobs.db"))
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "read-only bind") ||
		!strings.Contains(err.Error(), "JOB_STORE_PATH") {
		t.Errorf("a sqlite store in a ro bind = %v", err)
	}
	if _, err := planSurface(t, oc, "env-set-surface",
		setSurface("JOB_STORE_PATH=/rag/data/tenants/dev/state/jobs2.db")); err != nil {
		t.Errorf("a sqlite store under the data dir: %v", err)
	}
	if _, err := planSurface(t, oc, "env-set-surface",
		setSurface("GOWE_WORKFLOW_CWL=/rag/data/tenants/dev/cwl/x.cwl")); !errors.Is(err, jobs.ErrRefused) ||
		!strings.Contains(err.Error(), "APIImageUnitEnv") {
		t.Errorf("a CWL key on an image row = %v", err)
	}
	// On a WORKTREE row the same key is a path, under the worktree or data dir.
	oc2, _ := fixture(t, "dev", managed)
	if _, err := planSurface(t, direct(oc2), "env-set-surface",
		setSurface("GOWE_WORKFLOW_CWL=/rag/data/tenants/dev/cwl/x.cwl")); err != nil {
		t.Errorf("a CWL key on a worktree row: %v", err)
	}
}

// `create --set`: surface keys only under a --direct engine (the plan-time
// refusal is the only gate there, with the existing message otherwise), the
// same table — so a refused-always key cannot be smuggled in at birth — and
// the key lands in tenant.env, never in the row's settings{}.
func TestCreateSetSurfaceOnlyWhenDirect(t *testing.T) {
	args := func(kv ...string) map[string]any {
		return map[string]any{"name": "sandbox", "artifact_id": "v1.5.3", "settings": setSurface(kv...)["values"]}
	}
	oc, _ := createFixture(t)
	oc.Mode = model.WorkerDaemon
	_, err := planSurface(t, oc, "create", args("LLM_ENDPOINT=http://mango.cels.anl.gov:8003"))
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "accepts public settings only") {
		t.Errorf("daemon create with a surface key = %v", err)
	}
	for kv, want := range map[string]string{
		"PYTHONPATH=/rag/data/tenants/sandbox/py":       "unit-owned",
		"LLM_ENDPOINT=http://evil.example/v1":           "CTL_ALLOWED_ENDPOINT_HOSTS",
		"COLLECTIONS_FILE=/rag/data/tenants/dev/x.json": "dev's data dir",
		"API_KEYS=x": "accepts public settings only",
	} {
		oc, _ := createFixture(t)
		_, err := planSurface(t, direct(oc), "create", args(kv))
		if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), want) {
			t.Errorf("direct create --set %s = %v; want %q", kv, err, want)
		}
	}
	oc, fake := createFixture(t)
	oc = direct(oc)
	p, err := planSurface(t, oc, "create", args("LLM_ENDPOINT=http://mango.cels.anl.gov:8003", "LOG_LEVEL=DEBUG"))
	if err != nil {
		t.Fatalf("direct create: %v", err)
	}
	var preview string
	for _, s := range p.Steps {
		if strings.HasPrefix(s.Plan.Title, "write tenant.env") {
			preview = string(s.Plan.WouldWrite[0].Preview)
		}
	}
	if !strings.Contains(preview, "LLM_ENDPOINT=http://mango.cels.anl.gov:8003") || !strings.Contains(preview, "LOG_LEVEL=DEBUG") {
		t.Errorf("tenant.env preview lacks the settings:\n%s", preview)
	}
	r := newRunner(oc, fake)
	for _, s := range p.Steps {
		if s.Plan.Kind != "registry" {
			continue
		}
		if _, err := r.run(s); err != nil {
			t.Fatalf("%s: %v", s.Plan.Title, err)
		}
		break
	}
	row := oc.Fleet.Tenants["sandbox"]
	if row == nil {
		t.Fatal("the allocation step recorded no row")
	}
	if _, ok := row.Settings["LLM_ENDPOINT"]; ok {
		t.Error("settings{} received the surface key")
	}
	if row.Settings["LOG_LEVEL"] != "DEBUG" {
		t.Errorf("settings{} = %v; the public key belongs there", row.Settings)
	}
}

// template_from stays public-only even under --direct: a template that copied
// a store URL would point the new tenant at the old one's data.
func TestTemplateFromStaysPublicOnly(t *testing.T) {
	oc, _ := createFixture(t)
	oc = direct(oc)
	oc.Fleet.Tenants["dev"].Settings["QDRANT_URL"] = "http://localhost:24041"
	p, err := planSurface(t, oc, "create", map[string]any{"name": "sandbox", "artifact_id": "v1.5.3",
		"template_from": "dev"})
	if err != nil {
		t.Fatal(err)
	}
	for _, s := range p.Steps {
		if strings.HasPrefix(s.Plan.Title, "write tenant.env") &&
			strings.Contains(string(s.Plan.WouldWrite[0].Preview), "24041") {
			t.Errorf("template_from copied an executable-surface key:\n%s", s.Plan.WouldWrite[0].Preview)
		}
	}
	_ = registry.Tenant{}
}

// restart_pending means "an env edit the running API has not read yet". An env
// edit sets it (#714's registry step); a successful full `start` or `restart`
// — the API started and past the readiness gate — clears it in the same final
// registry effect that records the state.
func TestRestartClearsRestartPendingAfterAnEnvEdit(t *testing.T) {
	for _, verb := range []string{"restart", "start"} {
		oc, fake := fixture(t, "dev", managed)
		oc.Tenant.RestartPending = false
		r := newRunner(oc, fake)
		r.runAll(t, plan(t, oc, "env-set", map[string]any{"key": "LOG_LEVEL", "value": "DEBUG"}))
		if !oc.Fleet.Tenants["dev"].RestartPending {
			t.Fatalf("%s: env set did not set restart_pending", verb)
		}
		if verb == "start" {
			// A stopped tenant: start's readiness gate needs the port free first.
			fake.FakeProc().FreePort(oc.Tenant.Ports.API)
		}
		p := plan(t, oc, verb, nil)
		last := p.Steps[len(p.Steps)-1]
		if last.Plan.Kind != "registry" || !strings.Contains(last.Plan.Title, "restart_pending cleared") {
			t.Fatalf("%s: the final step is %s %q", verb, last.Plan.Kind, last.Plan.Title)
		}
		newRunner(oc, fake).runAll(t, p)
		if oc.Fleet.Tenants["dev"].RestartPending {
			t.Errorf("%s: restart_pending is still true after the API was started", verb)
		}
	}
}
