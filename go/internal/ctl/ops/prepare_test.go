package ops

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/render"
)

// devUI is a fixture tenant as the four adopted ones are: a Vite dev server on
// a port the ctl does not supervise, and a worktree with node_modules in it.
func devUI(port int) func(*registry.Tenant) {
	return func(t *registry.Tenant) {
		managed(t)
		t.UI = registry.UI{Mode: registry.UIModeDev, Port: registry.NullPort(port), Base: "/ragstack/" + t.Name + "/ui/"}
	}
}

// ---------------------------------------------------------------- set-ui-mode: static

func TestSetUIModeStaticBuildsSwapsPublishesAndProves(t *testing.T) {
	const uiPort = 8090
	oc, fake := fixture(t, "dev", devUI(uiPort))
	tn := oc.Tenant
	dist := distDir(tn)
	// The dev server, as the host has it: a listener on the UI port owned by a
	// process whose cwd is the tenant's frontend.
	fake.FakeProc().Ports[uiPort] = true
	fake.FakeProc().Owners[uiPort] = drivers.PortOwner{PID: 7788, UID: 1000}
	installNodeModules(fake, tn.Worktree)

	p := plan(t, oc, "set-ui-mode", map[string]any{"mode": "static"})
	want := []string{
		"apptainer: install the frontend's locked dependencies, if node_modules is absent",
		"apptainer: vite build --base /ragstack/dev/ui/ into dist.building",
		"proc: stop the Vite dev server on 8090 (by port AND identity)",
		"fs: swap the new build into place, keeping the previous one as dist.prev-<ts>",
		"registry: record ui.mode static (the port is cleared: nginx serves a directory)",
		"nginx: gateway: publish a generation in which the static alias for /ragstack/dev/ui/ replaces this tenant's $tenant_ui row",
		"probe: GET /ragstack/dev/ui/ through the live gateway (expect 200)",
	}
	if got := titles(p); strings.Join(got, "|") != strings.Join(want, "|") {
		t.Fatalf("steps =\n  %s\nwant\n  %s", strings.Join(got, "\n  "), strings.Join(want, "\n  "))
	}
	// A destructive plan: it stops a process and moves the directory nginx is
	// serving, so it is confirmed by typing the tenant's name.
	if !p.Plan.RequiresConfirm || string(p.Plan.ConfirmValue) != "dev" {
		t.Errorf("confirm = %v/%q, want true/dev", p.Plan.RequiresConfirm, p.Plan.ConfirmValue)
	}

	r := newRunner(oc, fake)
	r.runAll(t, p)

	// The build went into the STAGING directory, never straight over the one
	// nginx is serving.
	builds := fake.FakeBuild().Builds
	if len(builds) != 1 || !strings.HasSuffix(builds[0], " "+dist+".building") {
		t.Fatalf("builds = %v, want one into %s.building", builds, dist)
	}
	if !strings.Contains(builds[0], " /ragstack/dev/ui/ ") {
		t.Errorf("build base is not the gateway's own route: %q", builds[0])
	}
	// node_modules were there, so npm ci did NOT run: the one network step in
	// the control plane must not fire because a UI was rebuilt.
	for _, call := range fake.CallKeys() {
		if strings.HasPrefix(call, "build.NpmCI(") {
			t.Errorf("npm ci ran although node_modules were present: %s", call)
		}
	}
	// The dev server was signalled by pid, with the identity the driver checks.
	if got := fake.CallKeys(); !containsCall(got, "proc.Signal(7788,"+tn.Worktree+"/frontend,vite,TERM)") {
		t.Errorf("the dev server was not signalled with an identity check: %v", signalCalls(got))
	}
	// The registry says static, with no port.
	row := oc.Fleet.Tenants["dev"]
	if row.UI.Mode != registry.UIModeStatic || row.UI.Port != 0 {
		t.Errorf("ui = %+v, want static with a null port", row.UI)
	}
	if _, ok := row.LastOps["set-ui-mode"]; !ok {
		t.Error("last_ops has no set-ui-mode record")
	}
	// And the gateway was published AND probed, in that order.
	if got := fake.FakeGateway().Probes; len(got) != 1 || got[0] != "/ragstack/dev/ui/" {
		t.Errorf("probes = %v, want one of /ragstack/dev/ui/", got)
	}
	if fake.FakeGateway().Generation == 0 {
		t.Error("no gateway generation was published")
	}
	if p.Result()["ui_dist"] != dist {
		t.Errorf("result ui_dist = %v, want %s", p.Result()["ui_dist"], dist)
	}
}

// The swap keeps the build that was serving, and a rollback puts it back. This
// is the one step of the op that an operator cannot undo by hand in a hurry.
func TestSetUIModeStaticSwapIsReversible(t *testing.T) {
	oc, fake := fixture(t, "dev", devUI(0))
	tn := oc.Tenant
	dist := distDir(tn)
	installNodeModules(fake, tn.Worktree)
	// A build that is already there — the one users are being served right now.
	fake.FakeFiles().Put(dist+"/index.html", []byte("<!-- the build that is live -->\n"), 0o644)

	p := plan(t, oc, "set-ui-mode", map[string]any{"mode": "static"})
	r := newRunner(oc, fake)
	r.runAll(t, p)

	if got := fileAt(fake, dist+"/index.html"); strings.Contains(got, "the build that is live") {
		t.Fatal("the new build did not replace the old one")
	}
	var prev string
	for _, id := range r.externalIDs(stepIndex(p, "fs", "swap the new build") + 1) {
		if rest, ok := strings.CutPrefix(id, prevDistPrefix); ok {
			prev = rest
		}
	}
	if prev == "" {
		t.Fatal("the swap recorded no previous-dist external id, so a rollback could not find it")
	}
	if !strings.HasPrefix(prev, dist+".prev-") {
		t.Errorf("previous dist = %q, want %s.prev-<ts>", prev, dist)
	}
	if got := fileAt(fake, prev+"/index.html"); !strings.Contains(got, "the build that is live") {
		t.Errorf("the previous build was not kept at %s", prev)
	}

	// Roll the swap back: the live build is the old one again.
	swap := p.Steps[stepIndex(p, "fs", "swap the new build")]
	if _, err := r.rollback(swap); err != nil {
		t.Fatalf("rollback: %v", err)
	}
	if got := fileAt(fake, dist+"/index.html"); !strings.Contains(got, "the build that is live") {
		t.Errorf("after the rollback %s is not the previous build: %q", dist, got)
	}
}

// npm ci runs exactly when node_modules is absent, and not otherwise.
func TestSetUIModeStaticInstallsOnlyWhenNodeModulesAreAbsent(t *testing.T) {
	oc, fake := fixture(t, "dev", devUI(0))
	// Nothing seeded: the worktree has no node_modules.
	p := plan(t, oc, "set-ui-mode", map[string]any{"mode": "static"})
	r := newRunner(oc, fake)
	r.runAll(t, p)
	if !containsCall(fake.CallKeys(), "build.NpmCI("+oc.Tenant.Worktree+",/rag/cache/npm)") {
		t.Errorf("npm ci did not run for a worktree with no node_modules: %v", fake.CallKeys())
	}
	// The plan says so, because it is the one step that reaches the network.
	warns := stepWarnings(p, "apptainer", "locked dependencies")
	if len(warns) == 0 || !strings.Contains(strings.Join(warns, " "), "NETWORK") {
		t.Errorf("the npm step does not warn that it reaches the network: %v", warns)
	}
}

// A dev server on the tenant's port whose owner this account cannot read is a
// REFUSAL: it is somebody else's process, and the op's whole promise is that
// it stops this tenant's server and nothing else.
func TestSetUIModeStaticRefusesAnUnattributableUIPort(t *testing.T) {
	const uiPort = 8090
	oc, fake := fixture(t, "dev", devUI(uiPort))
	installNodeModules(fake, oc.Tenant.Worktree)
	fake.FakeProc().Ports[uiPort] = true // listening, but no Owners entry ⇒ pid 0

	p := plan(t, oc, "set-ui-mode", map[string]any{"mode": "static"})
	r := newRunner(oc, fake)
	stop := p.Steps[stepIndex(p, "proc", "stop the Vite dev server")]
	if _, err := r.run(stop); err == nil || !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("stopping an unattributable listener = %v, want a refusal", err)
	} else if !strings.Contains(err.Error(), "cannot attribute") {
		t.Errorf("refusal %q does not say why", err)
	}
}

// The tenant must be routed by the LIVE gateway before its UI row can be
// changed: publishing a generation for a tenant nginx does not serve would
// advance the fleet's generation for a document nobody reads.
func TestSetUIModeRefusesWhenTheGatewayDoesNotRouteTheTenant(t *testing.T) {
	oc, fake := fixture(t, "dev", devUI(0))
	installNodeModules(fake, oc.Tenant.Worktree)
	fake.FakeGateway().Routed = nil

	p := plan(t, oc, "set-ui-mode", map[string]any{"mode": "static"})
	r := newRunner(oc, fake)
	publish := p.Steps[stepIndex(p, "nginx", "publish a generation")]
	if _, err := r.run(publish); err == nil || !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("publish for an unrouted tenant = %v, want a refusal", err)
	}
}

// A published generation that does not actually serve the UI is a failure, not
// a success: this is the exact shape of doctor's ui_dist_missing, caught by the
// op that would otherwise have created it.
func TestSetUIModeStaticFailsWhenTheGatewayDoesNotServeTheUI(t *testing.T) {
	oc, fake := fixture(t, "dev", devUI(0))
	installNodeModules(fake, oc.Tenant.Worktree)
	fake.FakeGateway().ProbeStatus = map[string]int{"/ragstack/dev/ui/": 404}

	p := plan(t, oc, "set-ui-mode", map[string]any{"mode": "static"})
	r := newRunner(oc, fake)
	probe := p.Steps[stepIndex(p, "probe", "through the live gateway")]
	if _, err := r.run(probe); err == nil || !strings.Contains(err.Error(), "404") {
		t.Fatalf("a 404 on the UI route = %v, want a failure quoting it", err)
	}
}

// ---------------------------------------------------------------- set-ui-mode: external

func TestSetUIModeExternalIsRegistryOnly(t *testing.T) {
	oc, fake := fixture(t, "dev", managed) // static today
	p := plan(t, oc, "set-ui-mode", map[string]any{"mode": "external", "ui_port": 8090})
	want := []string{
		"registry: record ui.mode external on port 8090 (nothing is started or stopped)",
		"nginx: gateway: publish a generation in which the $tenant_ui row now points at 127.0.0.1:8090",
	}
	if got := titles(p); strings.Join(got, "|") != strings.Join(want, "|") {
		t.Fatalf("steps =\n  %s\nwant\n  %s", strings.Join(got, "\n  "), strings.Join(want, "\n  "))
	}
	r := newRunner(oc, fake)
	r.runAll(t, p)

	row := oc.Fleet.Tenants["dev"]
	if row.UI.Mode != registry.UIModeExternal || row.UI.Port != 8090 {
		t.Errorf("ui = %+v, want external on 8090", row.UI)
	}
	// Nothing was built and nothing was signalled — the whole point of the
	// direction that keeps somebody else's dev server running.
	for _, call := range fake.CallKeys() {
		if strings.HasPrefix(call, "build.") || strings.HasPrefix(call, "proc.Signal") {
			t.Errorf("external touched a process or a build: %s", call)
		}
	}
	if len(fake.FakeBuild().Builds) != 0 {
		t.Errorf("builds = %v, want none", fake.FakeBuild().Builds)
	}
}

func TestSetUIModeRefusesThePortMismatches(t *testing.T) {
	oc, _ := fixture(t, "dev", managed)
	err := planErr(t, oc, "set-ui-mode", map[string]any{"mode": "external"})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "--ui-port") {
		t.Errorf("external with no port = %v, want a refusal naming the flag", err)
	}
	err = planErr(t, oc, "set-ui-mode", map[string]any{"mode": "static", "ui_port": 8090})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "takes no port") {
		t.Errorf("static with a port = %v, want a refusal", err)
	}
}

// The args schema is the contract's: `dev` is not a direction this op moves a
// tenant in, and a port outside the unprivileged range is not a port.
func TestSetUIModeValidatesItsArguments(t *testing.T) {
	r := NewRegistry(Deps{})
	op, _ := r.Lookup("set-ui-mode")
	for _, c := range []struct {
		args map[string]any
		want string
	}{
		{map[string]any{"mode": "dev"}, "is not one of static, external"},
		{map[string]any{}, "missing required argument mode"},
		{map[string]any{"mode": "static", "ui_port": 80}, "below the minimum"},
		{map[string]any{"mode": "static", "ui_port": 70000}, "above the maximum"},
		{map[string]any{"mode": "static", "ui_port": "8090"}, "must be an integer"},
		{map[string]any{"mode": "static", "ui_port": 80.5}, "must be an integer"},
	} {
		err := op.Validate(c.args)
		if err == nil || !errors.Is(err, jobs.ErrValidation) || !strings.Contains(err.Error(), c.want) {
			t.Errorf("Validate(%v) = %v, want a validation error mentioning %q", c.args, err, c.want)
		}
	}
	// A JSON number arrives as float64: the daemon has to accept the same
	// value the CLI sends as an int.
	if err := op.Validate(map[string]any{"mode": "external", "ui_port": float64(8090)}); err != nil {
		t.Errorf("a JSON-decoded port was refused: %v", err)
	}
}

// ---------------------------------------------------------------- set-bind

func TestSetBindWritesTheRegistryAndNothingElse(t *testing.T) {
	oc, fake := fixture(t, "dev", func(t *registry.Tenant) {
		managed(t)
		t.API.Bind = "0.0.0.0"
	})
	p := plan(t, oc, "set-bind", map[string]any{"bind": "127.0.0.1"})
	if got := titles(p); len(got) != 1 || !strings.HasPrefix(got[0], "registry: record api.bind = 127.0.0.1") {
		t.Fatalf("steps = %v, want one registry write", got)
	}
	if p.Plan.RequiresConfirm {
		t.Error("set-bind stops nothing and moves nothing: it is not destructive")
	}
	// The plan says what an operator has to know before approving it.
	joined := strings.Join(p.Plan.Warnings, " ")
	for _, want := range []string{"next restart", "coconut:24040"} {
		if !strings.Contains(joined, want) {
			t.Errorf("plan warnings %q do not mention %q", joined, want)
		}
	}

	r := newRunner(oc, fake)
	r.runAll(t, p)
	row := oc.Fleet.Tenants["dev"]
	if row.API.Bind != "127.0.0.1" {
		t.Errorf("api.bind = %q, want 127.0.0.1", row.API.Bind)
	}
	if !row.RestartPending {
		t.Error("the row does not say a restart is pending, but the running API still has the old bind")
	}
	// Nothing was signalled, stopped or published: this op cannot disturb a
	// running tenant, which is what makes it safe to run days in advance.
	for _, call := range fake.CallKeys() {
		if strings.HasPrefix(call, "proc.") || strings.HasPrefix(call, "systemd.") ||
			strings.HasPrefix(call, "gateway.") {
			t.Errorf("set-bind touched the host: %s", call)
		}
	}
	// And the value the renderer would hand uvicorn is the one just written —
	// which is the whole reason there is no env key.
	_, args, _, err := render.APIArgv(row, render.UnitConfig{RagRoot: oc.Roots.RagRoot, CtlStateDir: oc.Roots.CtlStateDir})
	if err != nil {
		t.Fatalf("rendering the api argv: %v", err)
	}
	if !containsArg(args, "--host", "127.0.0.1") {
		t.Errorf("the rendered argv does not carry --host 127.0.0.1: %v", args)
	}
}

func TestSetBindRollsBack(t *testing.T) {
	oc, fake := fixture(t, "dev", func(t *registry.Tenant) {
		managed(t)
		t.API.Bind = "0.0.0.0"
	})
	p := plan(t, oc, "set-bind", map[string]any{"bind": "127.0.0.1"})
	r := newRunner(oc, fake)
	r.runAll(t, p)
	if _, err := r.rollback(p.Steps[0]); err != nil {
		t.Fatalf("rollback: %v", err)
	}
	if got := oc.Fleet.Tenants["dev"].API.Bind; got != "0.0.0.0" {
		t.Errorf("api.bind after the rollback = %q, want 0.0.0.0", got)
	}
}

func TestSetBindValidatesItsArguments(t *testing.T) {
	op, _ := NewRegistry(Deps{}).Lookup("set-bind")
	for _, c := range []struct{ args map[string]any }{
		{map[string]any{"bind": "localhost"}},
		{map[string]any{"bind": "10.0.0.5"}},
		{map[string]any{}},
	} {
		if err := op.Validate(c.args); err == nil || !errors.Is(err, jobs.ErrValidation) {
			t.Errorf("Validate(%v) = %v, want a validation error", c.args, err)
		}
	}
}

// ---------------------------------------------------------------- helpers

func containsCall(calls []string, want string) bool {
	for _, c := range calls {
		if c == want {
			return true
		}
	}
	return false
}

func signalCalls(calls []string) []string {
	var out []string
	for _, c := range calls {
		if strings.HasPrefix(c, "proc.") {
			out = append(out, c)
		}
	}
	return out
}

func containsArg(args []string, flag, value string) bool {
	for i := 0; i+1 < len(args); i++ {
		if args[i] == flag && args[i+1] == value {
			return true
		}
	}
	return false
}

// installNodeModules is a worktree whose frontend dependencies are already
// installed, in BOTH senses the op needs: the directory exists (which is what
// the step reads, through the Files driver) and the fake build driver agrees
// (which is what its UI() refuses without, exactly as the real one does).
func installNodeModules(fake *drivers.Fake, worktree string) {
	fake.FakeBuild().Installed[worktree] = true
	fake.FakeFiles().Put(worktree+"/frontend/node_modules/.package-lock.json", []byte("{}\n"), 0o644)
}

// fileAt is one in-memory file's content, as a string, or "" when it is not
// there.
func fileAt(fake *drivers.Fake, path string) string {
	b, err := fake.FakeFiles().ReadFile(context.Background(), path)
	if err != nil {
		return ""
	}
	return string(b)
}
