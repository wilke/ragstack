package ops

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/render"
)

// instanceManaged is `managed` for the other supervisor: a tenant the ctl runs
// itself, with its own postgres so the password path is exercised too.
func instanceManaged(t *registry.Tenant) {
	managed(t)
	t.Supervisor = supervisorInstance
	t.Stores.Postgres = registry.Postgres{
		Kind: registry.PostgresKindLocal, Ownership: registry.OwnershipExclusive,
		Capabilities: registry.Capabilities{Stop: true, Purge: true, Restore: true, Snapshot: true},
		URL:          registry.NullString("postgresql://localhost:" + itoa(t.Ports.PG)),
		Port:         registry.NullPort(t.Ports.PG),
		Instance:     registry.NullString("postgres-" + t.ManifestName),
		SIF:          registry.NullString("/rag/apptainer/images/postgres.sif"),
		DataDir:      registry.NullString(t.DataDir + "/postgres"),
	}
}

func itoa(n int) string {
	const digits = "0123456789"
	if n == 0 {
		return "0"
	}
	var b []byte
	for n > 0 {
		b = append([]byte{digits[n%10]}, b...)
		n /= 10
	}
	return string(b)
}

// instanceFixture is `fixture` with the host facts an instance-supervised
// tenant needs: the instance names bound to the ports they hold, and an EMPTY
// elasticsearch config directory (which is what makes the seed step run).
func instanceFixture(t *testing.T) (jobs.Context, *drivers.Fake) {
	t.Helper()
	oc, fake := fixture(t, "dev", instanceManaged)
	tp := paths.TenantPaths(oc.Roots, oc.Tenant.Name, oc.Tenant.ManifestName)
	ins := fake.FakeInstances()
	ins.BindInstancePort("qdrant-"+oc.Tenant.ManifestName, oc.Tenant.Ports.QdrantHTTP)
	ins.BindInstancePort("elasticsearch-"+oc.Tenant.ManifestName, oc.Tenant.Ports.ESHTTP)
	ins.BindInstancePort("postgres-"+oc.Tenant.ManifestName, oc.Tenant.Ports.PG)
	// The directory exists and is empty: apptainer refuses a bind whose source
	// is missing, and an empty one is what the seed is for.
	fake.FakeFiles().Dirs[tp.ESConfig] = 0o2770
	// The postgres role password, under BOTH names ops/create.go writes it: the
	// one the registry's secret ref points at and the one apptainer forwards
	// into the container.
	fake.FakeFiles().Put(tp.SecretsEnv, append(ledgerEnv(), []byte(
		"TENANT_PG_PASSWORD="+testSecret+"\n"+
			"APPTAINERENV_POSTGRES_PASSWORD="+testSecret+"\n")...), 0o640)
	return oc, fake
}

// A start in instance mode runs the stores as named apptainer instances and
// the API as a detached process, recording each external ID BEFORE the call
// that creates it — the same discipline the unit path keeps with unit names.
func TestInstanceStartRunsInstancesAndSpawnsTheAPI(t *testing.T) {
	oc, fake := instanceFixture(t)
	p := plan(t, oc, "start", nil)
	r := newRunner(oc, fake)
	r.runAll(t, p)

	got := strings.Join(fake.CallKeys(), "\n")
	// No systemd at all: that is the whole point of the supervisor.
	if strings.Contains(got, "systemd.") {
		t.Errorf("an instance-mode start touched systemd:\n%s", got)
	}
	for _, want := range []string{
		"job.checkpoint(instance:qdrant-dev)",
		"instances.Run(qdrant-dev,",
		// The ES config bind is seeded from the image BEFORE the instance runs.
		"instances.SeedConfigDir(",
		"job.checkpoint(instance:elasticsearch-dev)",
		"instances.Run(elasticsearch-dev,",
		"job.checkpoint(instance:postgres-dev)",
		"instances.Run(postgres-dev,",
		// The pidfile PATH is the durable record and is checkpointed before
		// the spawn that writes it; the pid follows.
		"job.checkpoint(pidfile:",
		"proc.Spawn(",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("no %q in the call log:\n%s", want, got)
		}
	}
	if i, j := strings.Index(got, "instances.SeedConfigDir("), strings.Index(got, "instances.Run(elasticsearch-dev,"); i < 0 || j < 0 || i > j {
		t.Errorf("the config seed did not precede the elasticsearch instance:\n%s", got)
	}
	if i, j := strings.Index(got, "job.checkpoint(pidfile:"), strings.Index(got, "proc.Spawn("); i < 0 || j < 0 || i > j {
		t.Errorf("the pidfile was not checkpointed before the spawn:\n%s", got)
	}
	if names := fake.FakeInstances().Names(); strings.Join(names, ",") != "elasticsearch-dev,postgres-dev,qdrant-dev" {
		t.Errorf("running instances = %v", names)
	}
}

// The postgres password reaches the container through apptainer's OWN
// environment and appears nowhere else — not in the plan, not on the argv, not
// in the call log.
func TestInstanceStartKeepsThePostgresPasswordOutOfEverythingButTheChildEnv(t *testing.T) {
	oc, fake := instanceFixture(t)
	p := plan(t, oc, "start", nil)

	blob, err := json.Marshal(p.Plan)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(blob), testSecret) {
		t.Fatalf("the plan carries the secret:\n%s", blob)
	}
	r := newRunner(oc, fake)
	r.runAll(t, p)
	if log := strings.Join(fake.CallKeys(), "\n"); strings.Contains(log, testSecret) {
		t.Fatalf("the call log carries the secret:\n%s", log)
	}
}

// Starting a leg that is ALREADY up is a no-op that says so. That is what
// makes `fleet start --all` — and therefore a periodic run of it — safe.
func TestInstanceStartIsIdempotent(t *testing.T) {
	oc, fake := instanceFixture(t)
	r := newRunner(oc, fake)
	r.runAll(t, plan(t, oc, "start", nil))
	runs := fake.Count("instances.Run")
	spawns := fake.Count("proc.Spawn")

	// The same plan again, against a host on which everything is now running.
	r2 := newRunner(oc, fake)
	r2.runAll(t, plan(t, oc, "start", nil))
	if n := fake.Count("instances.Run"); n != runs {
		t.Errorf("a second start ran %d instances, want %d", n-runs, 0)
	}
	if n := fake.Count("proc.Spawn"); n != spawns {
		t.Errorf("a second start spawned the API again (%d spawns)", n-spawns)
	}
}

// A stop is `apptainer instance stop` per store and TERM-then-proof for the
// API, with the pidfile removed LAST.
func TestInstanceStopSignalsThenProvesThePortIsFree(t *testing.T) {
	oc, fake := instanceFixture(t)
	r := newRunner(oc, fake)
	r.runAll(t, plan(t, oc, "start", nil))
	fake.Clear()

	r2 := newRunner(oc, fake)
	r2.runAll(t, plan(t, oc, "stop", map[string]any{"force": true}))

	got := strings.Join(fake.CallKeys(), "\n")
	for _, want := range []string{
		"proc.Signal(",
		"files.Remove(",
		// `,ctl` is the namespace: `tenant stop` acts on instances the
		// CONTROL PLANE started, which is the only registry it may stop in.
		// The handover's release is the one phase that names the other
		// (ops/handover.go's releaseNamespace).
		"instances.Stop(postgres-dev,ctl)",
		"instances.Stop(elasticsearch-dev,ctl)",
		"instances.Stop(qdrant-dev,ctl)",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("no %q in the call log:\n%s", want, got)
		}
	}
	if !strings.Contains(got, "proc.Signal(") || !strings.Contains(got, ",TERM)") {
		t.Errorf("the API was not asked to stop gracefully first:\n%s", got)
	}
	if strings.Contains(got, ",KILL)") {
		t.Errorf("a process that stopped on TERM was killed anyway:\n%s", got)
	}
	if i, j := strings.Index(got, "proc.Signal("), strings.Index(got, "files.Remove("); i > j {
		t.Errorf("the pidfile was removed before the process was gone:\n%s", got)
	}
	if names := fake.FakeInstances().Names(); len(names) != 0 {
		t.Errorf("instances still running after a stop: %v", names)
	}
}

// The plan an operator reads shows the real command, and it is the command the
// unit renderer would have put in ExecStart.
func TestInstanceStartPlanShowsTheSameArgvTheUnitWould(t *testing.T) {
	oc, _ := instanceFixture(t)
	p := plan(t, oc, "start", nil)

	var argv []string
	for _, s := range p.Steps {
		if strings.Contains(s.Plan.Title, "start the instance qdrant-dev") && len(s.Plan.WouldRun) > 0 {
			argv = s.Plan.WouldRun[0].Argv
		}
	}
	if len(argv) == 0 {
		t.Fatalf("no would_run for the qdrant instance: %v", titles(p))
	}
	st, err := render.StoreArgv(oc.Tenant, render.LegQdrant, render.UnitConfig{
		RagRoot: oc.Roots.RagRoot, CtlStateDir: oc.Roots.CtlStateDir,
	})
	if err != nil {
		t.Fatal(err)
	}
	line := strings.Join(argv, " ")
	if !strings.HasPrefix(line, "/usr/bin/apptainer instance run --no-home ") {
		t.Errorf("the instance command is not `apptainer instance run --no-home …`: %s", line)
	}
	// Every bind and every --env of the unit's ExecStart, and the image, are
	// on it; the instance NAME is the one extra argument.
	for _, want := range append(st.BindArgs(), append(st.EnvArgs(), st.SIF, st.Instance)...) {
		if !strings.Contains(line, want) {
			t.Errorf("the instance command is missing %q: %s", want, line)
		}
	}
}

// `create --supervisor instance` lays a tenant down with no unit files at all,
// and starts it through the instance seam.
func TestCreateWithInstanceSupervisorWritesNoUnits(t *testing.T) {
	oc, _ := fixture(t, "dev", managed)
	p := plan(t, oc, "create", map[string]any{
		"name": "acme", "artifact_id": testArtifactID, "supervisor": supervisorInstance,
	})
	list := strings.Join(titles(p), "\n")
	if strings.Contains(list, "write the unit ") || strings.Contains(list, "systemctl --user link") {
		t.Errorf("an instance-mode create wrote unit files:\n%s", list)
	}
	if !strings.Contains(list, "skip the unit files") {
		t.Errorf("the plan does not say why there are no units:\n%s", list)
	}
	for _, want := range []string{"start the instance qdrant-acme", "start the instance elasticsearch-acme",
		"start the API detached"} {
		if !strings.Contains(list, want) {
			t.Errorf("no %q in the plan:\n%s", want, list)
		}
	}
	for _, s := range p.Plan.Steps {
		for _, w := range s.WouldWrite {
			if strings.Contains(w.Path, "/units/") {
				t.Errorf("step %d would write a unit file: %s", s.N, w.Path)
			}
		}
	}
}

// And a create that names NO supervisor takes the deployment's.
func TestCreateTakesTheDeploymentDefaultSupervisor(t *testing.T) {
	oc, _ := fixture(t, "dev", managed)
	d := testDeps(oc)
	d.DefaultSupervisor = supervisorInstance
	op, _ := NewRegistry(d).Lookup("create")
	planned, err := op.Plan(context.Background(), oc, map[string]any{"name": "acme", "artifact_id": testArtifactID})
	if err != nil {
		t.Fatal(err)
	}
	list := strings.Join(titles(planned), "\n")
	if !strings.Contains(list, "skip the unit files") {
		t.Errorf("CTL_DEFAULT_SUPERVISOR=instance did not reach the plan:\n%s", list)
	}

	// With nothing configured, the contract's default applies.
	op2, _ := NewRegistry(testDeps(oc)).Lookup("create")
	planned2, err := op2.Plan(context.Background(), oc, map[string]any{"name": "acme", "artifact_id": testArtifactID})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(strings.Join(titles(planned2), "\n"), "write the unit ") {
		t.Errorf("the default is not systemd")
	}
}

// A dev-mode UI cannot be supervised in instance mode, and the refusal says
// what to do instead.
func TestInstanceRefusesADevModeUI(t *testing.T) {
	oc, _ := fixture(t, "dev", managed)
	err := planErr(t, oc, "create", map[string]any{
		"name": "acme", "artifact_id": testArtifactID,
		"supervisor": supervisorInstance, "ui_mode": registry.UIModeDev,
	})
	if !strings.Contains(err.Error(), "not supervised in instance mode") {
		t.Errorf("refusal = %v", err)
	}
}

// decommission of an instance-mode tenant stops its legs and removes no unit
// files, because there are none.
func TestDecommissionOfAnInstanceTenant(t *testing.T) {
	oc, _ := instanceFixture(t)
	oc.Tenant.LastBackup = &registry.BackupRecord{
		Bundle: "20260914T090000Z-fenced", Fenced: true, Verified: true, At: "2026-09-14T09:00:00Z",
		Scope: fullScope,
	}
	d := testDeps(oc)
	d.Owner = "svcbvbrc"
	op, _ := NewRegistry(d).Lookup("decommission")
	planned, err := op.Plan(context.Background(), oc, nil)
	if err != nil {
		t.Fatalf("decommission: %v", err)
	}
	list := strings.Join(titles(planned), "\n")
	if strings.Contains(list, "remove the rendered unit files") || strings.Contains(list, "daemon-reload") {
		t.Errorf("an instance-mode decommission touched unit files:\n%s", list)
	}
	for _, want := range []string{"stop the instance qdrant-dev", "stop the API through its pidfile",
		"skip removing the unit files", "quarantine the data directory"} {
		if !strings.Contains(list, want) {
			t.Errorf("no %q in the plan:\n%s", want, list)
		}
	}
}
