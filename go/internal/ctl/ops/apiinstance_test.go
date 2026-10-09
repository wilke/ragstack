package ops

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"reflect"
	"strconv"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/render"
)

// ---------------------------------------------------------------- fixtures

// testImageBytes are the fake host's server image file: the sha256 the row
// records is theirs, so the start's hash probe answers a fact.
var testImageBytes = []byte("ragstack server image v1.6.6 build 1")

const testImageName = "ragstack-server-v1.6.6-b1.sif"

func testServerImage() *registry.ServerImage {
	sum := sha256.Sum256(testImageBytes)
	return &registry.ServerImage{Name: testImageName, Version: "v1.6.6", Commit: strings.Repeat("ab", 20), Build: 1,
		SHA256: hex.EncodeToString(sum[:]), Path: "/rag/data/ctl/images/server/" + testImageName}
}

// testImageLabels are the labels `apptainer inspect` gives an image built for si.
func testImageLabels(si *registry.ServerImage) map[string]string {
	return map[string]string{LabelVersion: si.Version, LabelCommit: si.Commit,
		LabelBuild: strconv.Itoa(si.Build), LabelRole: RoleServer, "org.ragstack.build-date": "2026-10-09"}
}

// imageManaged is instanceManaged in IMAGE mode: the same tenant, its API run
// from a server image.
func imageManaged(t *registry.Tenant) {
	instanceManaged(t)
	si := testServerImage()
	t.ServerImage = si
	t.Code = registry.Code{Tag: si.Version, SHA: registry.NullString(si.Commit)}
}

// devImageEnv is a tenant.env shaped like dev's: the state paths under the
// data dir, HF_HOME, two GOWE_IMAGE_DIRS entries, and a GOWE_WORKFLOW_CWL into
// the worktree that the image's unit environment shadows.
func devImageEnv() []byte {
	return append(tenantEnv(), []byte(""+
		"HF_HOME=/rag/cache\n"+
		"JOB_STORE_BACKEND=sqlite\n"+
		"JOB_STORE_PATH=/rag/data/tenants/dev/state/ragstack_jobs.db\n"+
		"COLLECTION_MANIFEST_DIR=/rag/data/tenants/dev/manifests\n"+
		"INGEST_ROOT=/rag/data/tenants/dev/ingest\n"+
		"GOWE_WORKFLOW_CWL=/rag/data/tenants/dev/worktree/cwl/pdf-ingest-scatter.cwl\n"+
		"GOWE_IMAGE_DIRS=/scout/containers/ragstack-dev,/scout/containers/ragstack\n")...)
}

// imageRowFixture is instanceFixture for an image-mode row with the given
// tenant.env: the image file and its labels on the fake host, and the API's
// port FREE — nothing serves it until the instance starts, and there is no
// pidfile in image mode.
func imageRowFixture(t *testing.T, env []byte) (jobs.Context, *drivers.Fake) {
	t.Helper()
	oc, fake := fixtureEnv(t, "dev", imageManaged, env)
	tp := paths.TenantPaths(oc.Roots, oc.Tenant.Name, oc.Tenant.ManifestName)
	ins := fake.FakeInstances()
	ins.BindInstancePort("qdrant-"+oc.Tenant.ManifestName, oc.Tenant.Ports.QdrantHTTP)
	ins.BindInstancePort("elasticsearch-"+oc.Tenant.ManifestName, oc.Tenant.Ports.ESHTTP)
	ins.BindInstancePort("postgres-"+oc.Tenant.ManifestName, oc.Tenant.Ports.PG)
	fake.FakeFiles().Dirs[tp.ESConfig] = 0o2770
	fake.FakeFiles().Put(tp.SecretsEnv, append(ledgerEnv(), []byte(
		"TENANT_PG_PASSWORD="+testSecret+"\n"+
			"APPTAINERENV_POSTGRES_PASSWORD="+testSecret+"\n")...), 0o640)
	si := oc.Tenant.ServerImage
	fake.FakeFiles().Put(si.Path, testImageBytes, 0o640)
	ins.SetLabels(si.Path, testImageLabels(si))
	fake.FakeProc().FreePort(oc.Tenant.Ports.API)
	_ = fake.FakeFiles().Remove(context.Background(), tp.PidFile)
	return oc, fake
}

// apiInstanceStep is the one image-mode API start step of a plan.
func apiInstanceStep(t *testing.T, p *jobs.Planned, prefix string) jobs.Step {
	t.Helper()
	for _, s := range p.Steps {
		if strings.HasPrefix(s.Plan.Title, prefix) {
			return s
		}
	}
	titles := make([]string, 0, len(p.Steps))
	for _, s := range p.Steps {
		titles = append(titles, s.Plan.Title)
	}
	t.Fatalf("no step titled %q… in:\n%s", prefix, strings.Join(titles, "\n"))
	return jobs.Step{}
}

// bindsOf reads the --bind values off an argv.
func bindsOf(argv []string) []string {
	var out []string
	for i, a := range argv {
		if a == "--bind" && i+1 < len(argv) {
			out = append(out, argv[i+1])
		}
	}
	return out
}

// apiSpec is the InstanceSpec the fake got for the API instance.
func apiSpec(t *testing.T, fake *drivers.Fake) jobs.InstanceSpec {
	t.Helper()
	var found *jobs.InstanceSpec
	for i := range fake.FakeInstances().Specs {
		if s := fake.FakeInstances().Specs[i]; s.Name == "api-dev" {
			found = &s
		}
	}
	if found == nil {
		t.Fatalf("no api-dev instance was run")
	}
	return *found
}

// ---------------------------------------------------------------- the plan

// A dev-like image row's start: an instance of the image, `--cleanenv`, the
// runscript's two flags, the four binds the brief derives — and nothing that
// makes the argv a carrier: no `--env`, no PYTHONPATH, no secret.
func TestImageStartPlansTheInstanceWithTheDerivedBindsAndNoEnvOnTheArgv(t *testing.T) {
	oc, _ := imageRowFixture(t, devImageEnv())
	p := plan(t, oc, "start", map[string]any{"only": []string{"api"}})
	s := apiInstanceStep(t, p, "start the API instance api-dev")
	if s.Plan.Kind != "instance" {
		t.Errorf("kind = %s", s.Plan.Kind)
	}
	argv := s.Plan.WouldRun[0].Argv
	want := []string{"/usr/bin/apptainer", "instance", "run", "--no-home", "--cleanenv",
		"--bind", "/rag/cache:/rag/cache:rw",
		"--bind", "/rag/data/tenants/dev:/rag/data/tenants/dev:rw",
		"--bind", "/scout/containers/ragstack-dev:/scout/containers/ragstack-dev:ro",
		"--bind", "/scout/containers/ragstack:/scout/containers/ragstack:ro",
		"/rag/data/ctl/images/server/" + testImageName, "api-dev",
		"--host", "127.0.0.1", "--port", strconv.Itoa(oc.Tenant.Ports.API)}
	if !reflect.DeepEqual(argv, want) {
		t.Errorf("argv =\n%q\nwant\n%q", argv, want)
	}
	for _, a := range argv {
		if a == "--env" || strings.Contains(a, "PYTHONPATH") {
			t.Errorf("the argv carries %q", a)
		}
	}
	// No pidfile anywhere in the plan, and no proc step for the API.
	blob, _ := json.Marshal(p.Plan)
	for _, bad := range []string{"PYTHONPATH", testSecret, "api-dev.pid", "proc.Spawn", "uvicorn ragstack"} {
		if strings.Contains(string(blob), bad) {
			t.Errorf("the plan carries %q:\n%s", bad, blob)
		}
	}
	for _, st := range p.Steps {
		if len(st.Plan.WouldWrite) > 0 && strings.HasSuffix(st.Plan.WouldWrite[0].Path, ".pid") {
			t.Errorf("step %q writes a pidfile", st.Plan.Title)
		}
	}
}

// demo's COLLECTIONS_FILE lives in /rag/config: the bind is THAT FILE, read
// only — never /rag/config, which holds /rag/config/ctl (the ctl's secrets and
// the backup identity).
func TestImageBindsASettingOutsideTheTreeAsItsOwnPathNeverItsRoot(t *testing.T) {
	env := append(tenantEnv(), []byte("COLLECTIONS_FILE=/rag/config/demo.collections.json\n")...)
	oc, _ := imageRowFixture(t, env)
	p := plan(t, oc, "start", map[string]any{"only": []string{"api"}})
	binds := bindsOf(apiInstanceStep(t, p, "start the API instance").Plan.WouldRun[0].Argv)
	want := []string{
		"/rag/cache:/rag/cache:rw",
		"/rag/config/demo.collections.json:/rag/config/demo.collections.json:ro",
		"/rag/data/tenants/dev:/rag/data/tenants/dev:rw",
	}
	if !reflect.DeepEqual(binds, want) {
		t.Errorf("binds = %q, want %q", binds, want)
	}
	for _, b := range binds {
		if strings.HasPrefix(b, "/rag/config:") {
			t.Errorf("the whole /rag/config is bound: %s", b)
		}
	}
}

// The bind derivation itself: identity binds, the rw roots first so a path
// under them is not bound twice, a relative value binds nothing, sorted.
func TestAPIImageBindsDeduplicateAndSort(t *testing.T) {
	got := render.APIImageBinds("/rag/data/tenants/x", "/rag/cache", map[string]string{
		"GOWE_IMAGE_DIRS":       "/scout/containers/ragstack, /rag/data/tenants/x/images ,",
		"COLLECTIONS_FILE":      "/rag/config/x.json",
		"PROMPT_TEMPLATES_FILE": "/rag/config/x.json",
		"MODELS_REGISTRY_FILE":  "models.json",
		"JOB_STORE_PATH":        "/rag/data/tenants/x/state/jobs.db",
		"QDRANT_URL":            "/not/a/path/key",
	})
	want := []string{
		"/rag/cache:/rag/cache:rw",
		"/rag/config/x.json:/rag/config/x.json:ro",
		"/rag/data/tenants/x:/rag/data/tenants/x:rw",
		"/scout/containers/ragstack:/scout/containers/ragstack:ro",
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("binds =\n%q\nwant\n%q", got, want)
	}
}

// Keys that would hijack the container are refused at plan time: from
// tenant.env, and from the row's secret_refs (the plan never reads
// secrets.env, but its KEYS are on the row).
func TestImageStartRefusesHijackingKeys(t *testing.T) {
	for _, key := range []string{"PYTHONPATH", "PATH", "LD_PRELOAD", "APPTAINER_BIND", "PREPEND_PATH", "APPEND_PATH"} {
		oc, _ := imageRowFixture(t, append(tenantEnv(), []byte(key+"=/opt/evil\n")...))
		err := planErr(t, oc, "start", nil)
		if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), key) {
			t.Errorf("%s in tenant.env: %v, want a refusal naming it", key, err)
		}
	}
	oc, _ := fixtureEnv(t, "dev", func(tn *registry.Tenant) {
		imageManaged(tn)
		tn.SecretRefs = append(tn.SecretRefs, registry.SecretRef{Key: "LD_LIBRARY_PATH", File: "secrets.env"})
	}, tenantEnv())
	if err := planErr(t, oc, "start", nil); !errors.Is(err, jobs.ErrRefused) ||
		!strings.Contains(err.Error(), "LD_LIBRARY_PATH") {
		t.Errorf("LD_LIBRARY_PATH in secret_refs: %v", err)
	}
	// The postgres instance's own password, which `create` writes into
	// secrets.env for every postgres-local tenant, is dropped, not refused.
	oc, _ = fixtureEnv(t, "dev", func(tn *registry.Tenant) {
		imageManaged(tn)
		tn.SecretRefs = append(tn.SecretRefs, registry.SecretRef{Key: render.APPTAINERENVPostgresPassword,
			File: "secrets.env"})
	}, tenantEnv())
	plan(t, oc, "start", nil)
}

// A worktree-mode row plans exactly what it planned before PR-F F4: the same
// detached uvicorn, the same pidfile.
func TestWorktreeStartIsUnchangedByImageMode(t *testing.T) {
	oc, _ := instanceFixture(t)
	p := plan(t, oc, "start", map[string]any{"only": []string{"api"}})
	s := apiInstanceStep(t, p, "start the API detached once its stores answer (pidfile api-dev.pid)")
	if s.Plan.Kind != "proc" || s.Plan.WouldRun[0].Argv[0] != "/rag/envs/ragstack/bin/python" {
		t.Errorf("worktree start = %+v", s.Plan)
	}
}

// ---------------------------------------------------------------- running it

// The start runs the instance with the environment in the CHILD only: every
// key as APPTAINERENV_<KEY>, the unit environment winning (the CWL path into
// the worktree is shadowed by the image's), secrets present only there, no
// PYTHONPATH, and the postgres instance's password not forwarded.
func TestImageStartRunsTheInstanceWithTheEnvironmentInTheChildOnly(t *testing.T) {
	oc, fake := imageRowFixture(t, devImageEnv())
	r := newRunner(oc, fake)
	r.runAll(t, plan(t, oc, "start", nil))

	spec := apiSpec(t, fake)
	if !spec.CleanEnv || len(spec.Env) != 0 {
		t.Errorf("CleanEnv %v, --env %v", spec.CleanEnv, spec.Env)
	}
	si := oc.Tenant.ServerImage
	want := map[string]string{
		"APPTAINERENV_HF_HOME":                "/rag/cache",
		"APPTAINERENV_PYTHONUNBUFFERED":       "1",
		"APPTAINERENV_RAGSTACK_GIT_TAG":       si.Version,
		"APPTAINERENV_RAGSTACK_GIT_SHA":       si.Commit,
		"APPTAINERENV_RAGSTACK_API_LOG":       "/rag/data/tenants/dev/logs/api-dev.log",
		"APPTAINERENV_GOWE_WORKFLOW_CWL":      render.APIImageWorkflowCWL,
		"APPTAINERENV_GRAPH_EXTRACT_CWL":      render.APIImageGraphCWL,
		"APPTAINERENV_COLLECTION_RESTORE_CWL": render.APIImageRestoreCWL,
		"APPTAINERENV_TENANT_PG_PASSWORD":     testSecret,
		"APPTAINERENV_LOG_LEVEL":              "INFO",
	}
	for k, v := range want {
		if spec.ExtraEnv[k] != v {
			t.Errorf("ExtraEnv[%s] = %q, want %q", k, spec.ExtraEnv[k], v)
		}
	}
	for k := range spec.ExtraEnv {
		if !strings.HasPrefix(k, "APPTAINERENV_") {
			t.Errorf("ExtraEnv key %s is not forwarded as APPTAINERENV_*", k)
		}
		if strings.Contains(k, "PYTHONPATH") || strings.Contains(k, "APPTAINERENV_APPTAINERENV") {
			t.Errorf("ExtraEnv carries %s", k)
		}
	}
	if log := strings.Join(fake.CallKeys(), "\n"); strings.Contains(log, testSecret) {
		t.Errorf("the call log carries the secret")
	}
	// The image was proved before the run, and the name checkpointed.
	log := strings.Join(fake.CallKeys(), "\n")
	for _, want := range []string{"instances.Labels(" + si.Path, "files.Sha256(" + si.Path,
		"job.checkpoint(instance:api-dev,errlog:", "instances.Run(api-dev,"} {
		if !strings.Contains(log, want) {
			t.Errorf("no %q in the call log:\n%s", want, log)
		}
	}
	if strings.Contains(log, "proc.Spawn(") {
		t.Errorf("an image-mode start spawned a process:\n%s", log)
	}
	// Running is the instance table plus the port.
	sc := r.ctx(jobs.Step{Plan: model.PlannedStep{N: 999}})
	up, in, err := apiInstanceRunning(context.Background(), sc, "api-dev", oc.Tenant.Ports.API)
	if err != nil || !up || in.PID == 0 {
		t.Errorf("apiInstanceRunning = %v %+v %v", up, in, err)
	}
	// And a second start is a no-op that says so.
	runs := fake.Count("instances.Run")
	r2 := newRunner(oc, fake)
	r2.runAll(t, plan(t, oc, "start", map[string]any{"only": []string{"api"}}))
	if fake.Count("instances.Run") != runs {
		t.Errorf("a start of a running API instance ran another")
	}
}

// The image must BE the row's: a label that disagrees, or bytes that hash to
// something else, refuse the start before anything runs.
func TestImageStartRefusesAnImageThatIsNotTheRows(t *testing.T) {
	cases := map[string]func(*drivers.Fake, *registry.ServerImage){
		"a commit label from another build": func(f *drivers.Fake, si *registry.ServerImage) {
			l := testImageLabels(si)
			l[LabelCommit] = strings.Repeat("cd", 20)
			f.FakeInstances().SetLabels(si.Path, l)
		},
		"a worker image": func(f *drivers.Fake, si *registry.ServerImage) {
			l := testImageLabels(si)
			l[LabelRole] = "worker"
			f.FakeInstances().SetLabels(si.Path, l)
		},
		"bytes that were swapped": func(f *drivers.Fake, si *registry.ServerImage) {
			f.FakeFiles().Put(si.Path, []byte("something else"), 0o640)
		},
	}
	for name, mutate := range cases {
		oc, fake := imageRowFixture(t, devImageEnv())
		mutate(fake, oc.Tenant.ServerImage)
		p := plan(t, oc, "start", map[string]any{"only": []string{"api"}})
		r := newRunner(oc, fake)
		_, err := r.run(apiInstanceStep(t, p, "start the API instance"))
		if !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("%s: %v, want a refusal", name, err)
		}
		if fake.Count("instances.Run") != 0 {
			t.Errorf("%s: the instance ran anyway", name)
		}
	}
}

// A path the API would see that the plan did not bind — or a relative sqlite
// store, which would land on the read-only image root — is refused at run time.
func TestImageStartRefusesPathsOutsideTheBinds(t *testing.T) {
	oc, fake := imageRowFixture(t, devImageEnv())
	p := plan(t, oc, "start", map[string]any{"only": []string{"api"}})
	// secrets.env is read at RUN time only, so a path it adds was never bound.
	tp := paths.TenantPaths(oc.Roots, "dev", "dev")
	fake.FakeFiles().Put(tp.SecretsEnv, append(ledgerEnv(),
		[]byte("GRADING_STORE_BACKEND=sqlite\nMODELS_REGISTRY_FILE=/srv/models.json\n")...), 0o640)
	r := newRunner(oc, fake)
	_, err := r.run(apiInstanceStep(t, p, "start the API instance"))
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "MODELS_REGISTRY_FILE=/srv/models.json") ||
		!strings.Contains(err.Error(), "GRADING_STORE_PATH (sqlite)") {
		t.Errorf("err = %v", err)
	}
	// And a hijacking key that only secrets.env carries is refused there too.
	fake.FakeFiles().Put(tp.SecretsEnv, append(ledgerEnv(), []byte("PYTHONPATH=/tmp/x\n")...), 0o640)
	if _, err := r.run(apiInstanceStep(t, p, "start the API instance")); !errors.Is(err, jobs.ErrRefused) ||
		!strings.Contains(err.Error(), "PYTHONPATH") {
		t.Errorf("PYTHONPATH in secrets.env = %v", err)
	}
	if fake.Count("instances.Run") != 0 {
		t.Errorf("the instance ran")
	}
}

// The stop is `instance stop` and proof the port is free, with the instance's
// presence checkpointed BEFORE the stop; its rollback starts the same instance
// again from the same image.
func TestImageStopStopsTheInstanceProvesThePortAndRollsBackByRestarting(t *testing.T) {
	oc, fake := imageRowFixture(t, devImageEnv())
	newRunner(oc, fake).runAll(t, plan(t, oc, "start", nil))
	port := oc.Tenant.Ports.API

	p := plan(t, oc, "stop", map[string]any{"only": []string{"api"}})
	s := apiInstanceStep(t, p, "stop the API instance api-dev")
	if got := s.Plan.WouldRun[0].Argv; strings.Join(got, " ") != "/usr/bin/apptainer instance stop api-dev" {
		t.Errorf("stop argv = %q", got)
	}
	r := newRunner(oc, fake)
	detail, err := r.run(s)
	if err != nil {
		t.Fatalf("stop: %v", err)
	}
	if !strings.Contains(detail, "is free") {
		t.Errorf("detail = %q", detail)
	}
	if ids := strings.Join(r.externalIDs(s.Plan.N), ","); !strings.Contains(ids, "was-running:api-dev") {
		t.Errorf("checkpoints = %s", ids)
	}
	if listening, _ := fake.Proc().Listening(context.Background(), port); listening {
		t.Errorf("the port is still held after the stop")
	}
	log := strings.Join(fake.CallKeys(), "\n")
	if !strings.Contains(log, "instances.Stop(api-dev") || strings.Contains(log, "proc.Signal(") ||
		strings.Contains(log, "files.ReadFile(/rag/data/tenants/dev/api-dev.pid") {
		t.Errorf("the stop did not go through the instance:\n%s", log)
	}

	before := fake.Count("instances.Run")
	if _, err := r.rollback(s); err != nil {
		t.Fatalf("rollback: %v", err)
	}
	if fake.Count("instances.Run") != before+1 {
		t.Errorf("the rollback did not start the instance again")
	}
	if listening, _ := fake.Proc().Listening(context.Background(), port); !listening {
		t.Errorf("the API is not listening after the rollback")
	}

	// A stop of an API that was not running records nothing to undo, and its
	// rollback starts nothing.
	fake.FakeInstances().StopAll()
	r2 := newRunner(oc, fake)
	if _, err := r2.run(s); err != nil {
		t.Fatalf("stop of a stopped API: %v", err)
	}
	before = fake.Count("instances.Run")
	if _, err := r2.rollback(s); err != nil || fake.Count("instances.Run") != before {
		t.Errorf("the rollback of a no-op stop started something (%v)", err)
	}
}

// A port held by something that is not the API instance is not "stopped":
// the stop of an absent instance over a held port refuses.
func TestImageStopRefusesAPortItCannotAccountFor(t *testing.T) {
	oc, fake := imageRowFixture(t, devImageEnv())
	fake.FakeProc().SetOwner(oc.Tenant.Ports.API, 777, 1000)
	p := plan(t, oc, "stop", map[string]any{"only": []string{"api"}})
	if _, err := newRunner(oc, fake).run(apiInstanceStep(t, p, "stop the API instance")); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("err = %v", err)
	}
	// …and so does a start: the instance would fail to bind it.
	p = plan(t, oc, "start", map[string]any{"only": []string{"api"}})
	if _, err := newRunner(oc, fake).run(apiInstanceStep(t, p, "start the API instance")); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("start over a held port = %v", err)
	}
}

// ---------------------------------------------------------------- the callers

// The backup fence on an image row stops the API instance and starts it again
// through the same two functions — no pidfile, no spawn.
func TestBackupFenceOnAnImageRowStopsAndRestartsTheInstance(t *testing.T) {
	oc, fake := imageRowFixture(t, devImageEnv())
	newRunner(oc, fake).runAll(t, plan(t, oc, "start", nil))
	seedState(fake, "dev")
	runBackup(t, oc, fake, map[string]any{"fence": true})
	log := strings.Join(fake.CallKeys(), "\n")
	stop, run := strings.LastIndex(log, "instances.Stop(api-dev"), strings.LastIndex(log, "instances.Run(api-dev")
	if stop < 0 || run < stop {
		t.Errorf("the fence did not stop then restart api-dev:\n%s", log)
	}
	if strings.Contains(log, "proc.Spawn(") || strings.Contains(log, "proc.Signal(") {
		t.Errorf("the fence used the worktree process path:\n%s", log)
	}
	if up, _, _ := apiInstanceRunning(context.Background(), newRunner(oc, fake).ctx(jobs.Step{Plan: model.PlannedStep{N: 1}}),
		"api-dev", oc.Tenant.Ports.API); !up {
		t.Errorf("the API instance is not running after the fenced backup")
	}
}

// decommission's dry run on an image row plans the instance stop.
func TestDecommissionPlansTheAPIInstanceStopOnAnImageRow(t *testing.T) {
	oc, _ := imageRowFixture(t, devImageEnv())
	oc.Tenant.LastBackup = &registry.BackupRecord{Bundle: "/rag/backups/tenants/dev/20260914T080000Z-backup",
		At: "2026-09-14T08:00:00Z", Kind: "backup", Fenced: true, Verified: true, Scope: fullScope}
	apiInstanceStep(t, plan(t, oc, "decommission", noArchive), "stop the API instance api-dev")
	// And the archiving decommission: its fence stops the instance too.
	apiInstanceStep(t, planDecom(t, oc, archiveDeps(oc), map[string]any{}), "stop the API instance api-dev")
}

// A hand-started tenant cannot be in image mode: the release would stop a
// pidfile process and its rollback would start an instance.
func TestHandoverReleaseRefusesAnImageRow(t *testing.T) {
	oc, _ := fixture(t, "dev", func(tn *registry.Tenant) {
		prepared(tn)
		tn.ServerImage = testServerImage()
	})
	err := planErrAs(t, oc, "wilke", "handover", map[string]any{"phase": "release"})
	if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "server_image") {
		t.Errorf("err = %v", err)
	}
}

// An image row on systemd units is refused at start: there is no unit for an
// API instance.
func TestSystemdRefusesToStartAnImageRow(t *testing.T) {
	oc, _ := fixture(t, "dev", func(tn *registry.Tenant) {
		managed(tn)
		tn.ServerImage = testServerImage()
	})
	if err := planErr(t, oc, "start", nil); !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), "instance") {
		t.Errorf("err = %v", err)
	}
}

// ---------------------------------------------------------------- create / restore

// imageCreateFixture is createFixture with the server image prepared: a
// record in the fleet, the file and its labels on the fake host, and the new
// tenant's store instances bound to the ports they will hold.
func imageCreateFixture(t *testing.T) (jobs.Context, *drivers.Fake) {
	t.Helper()
	oc, fake := createFixture(t)
	si := testServerImage()
	oc.Fleet.ServerImages = map[string]*registry.ServerImageRecord{si.Name: {
		Version: si.Version, Commit: si.Commit, Build: si.Build, SHA256: si.SHA256, Path: si.Path,
		PreparedAt: "2026-10-09T00:00:00Z", PreparedBy: "local:3581"}}
	fake.FakeFiles().Put(si.Path, testImageBytes, 0o640)
	fake.FakeInstances().SetLabels(si.Path, testImageLabels(si))
	fake.FakeBuild().Installed[oc.Fleet.Artifacts["v1.5.3"].Worktree] = true
	block := paths.BlockAt(oc.Fleet.PortBase, oc.Fleet.PortStride, 4)
	fake.FakeInstances().BindInstancePort("qdrant-sandbox", block.QdrantHTTP)
	fake.FakeInstances().BindInstancePort("elasticsearch-sandbox", block.ESHTTP)
	return oc, fake
}

// `create --image` lays the tenant down in image mode from birth: the row
// records the image and the image's code, the worktree is at the image's
// commit, the API comes up as the instance api-<name>, and the service account
// and the post-checks run against it exactly as for a worktree tenant.
func TestCreateInImageModeEndToEnd(t *testing.T) {
	oc, fake := imageCreateFixture(t)
	p := plan(t, oc, "create", map[string]any{
		"name": "sandbox", "image": testImageName, "artifact_id": "v1.5.3", "supervisor": supervisorInstance,
		"service_accounts": []any{map[string]any{"subject": "gowe", "role": "user", "purpose": "workflows"}},
	})
	list := strings.Join(titles(p), "\n")
	for _, want := range []string{"check the worktree out at server image " + testImageName + "'s commit",
		"start the API instance api-sandbox from " + testImageName, "register the service account gowe",
		"post-checks: /health"} {
		if !strings.Contains(list, want) {
			t.Errorf("no %q in the plan:\n%s", want, list)
		}
	}
	if strings.Contains(list, "start the API detached") {
		t.Errorf("an image-mode create plans the worktree API:\n%s", list)
	}
	// The binds come from the tenant.env this job is about to write.
	argv := apiInstanceStep(t, p, "start the API instance").Plan.WouldRun[0].Argv
	if got := strings.Join(bindsOf(argv), " "); got != "/rag/cache:/rag/cache:rw "+
		"/rag/data/tenants/sandbox:/rag/data/tenants/sandbox:rw" {
		t.Errorf("binds = %s", got)
	}

	r := newRunner(oc, fake)
	r.runAll(t, p)
	row := oc.Fleet.Tenants["sandbox"]
	si := testServerImage()
	if row == nil || row.ServerImage == nil || *row.ServerImage != *si {
		t.Fatalf("server_image = %+v", row)
	}
	if row.Code.Tag != si.Version || string(row.Code.SHA) != si.Commit || row.State != "active" {
		t.Errorf("code = %+v, state %s", row.Code, row.State)
	}
	if err := oc.Fleet.ValidateContract(); err != nil {
		t.Errorf("the registry does not match the contract: %v", err)
	}
	if sha := fake.FakeGit().Worktrees["/rag/repos/tenants/sandbox"]; sha != si.Commit {
		t.Errorf("worktree sha = %q", sha)
	}
	if got := fake.FakeTenantAPI().Accounts; len(got) != 1 || !strings.Contains(got[0], "create gowe user") {
		t.Errorf("service accounts = %v", got)
	}
	log := strings.Join(fake.CallKeys(), "\n")
	if !strings.Contains(log, "instances.Run(api-sandbox,"+si.Path) || strings.Contains(log, "proc.Spawn(") {
		t.Errorf("the API did not start as an instance:\n%s", log)
	}
	if !strings.Contains(log, "tenantapi.Version(") {
		t.Errorf("the post-checks did not run:\n%s", log)
	}
	if res := p.Result(); res["server_image"] != testImageName {
		t.Errorf("result server_image = %v", res["server_image"])
	}
}

// What `create --image` refuses, all at plan time from the registry alone.
func TestCreateInImageModeRefusals(t *testing.T) {
	cases := []struct {
		name string
		args map[string]any
		want string
	}{
		{"an image nobody prepared", map[string]any{"image": "ragstack-server-v9-b1.sif", "artifact_id": "v1.5.3",
			"supervisor": supervisorInstance}, "not prepared"},
		{"systemd", map[string]any{"image": testImageName, "artifact_id": "v1.5.3", "supervisor": "systemd"},
			"supervisor: instance"},
		{"a static UI with no artifact", map[string]any{"image": testImageName, "supervisor": supervisorInstance},
			"--artifact"},
		{"an artifact at another commit", map[string]any{"image": testImageName, "artifact_id": "other",
			"supervisor": supervisorInstance}, "different code"},
	}
	for _, c := range cases {
		oc, _ := imageCreateFixture(t)
		oc.Fleet.Artifacts["other"] = &registry.Artifact{SHA: strings.Repeat("cd", 20), Tag: "x",
			Worktree: "/rag/data/ctl/artifacts/other/worktree", UIDist: "/rag/data/ctl/artifacts/other/worktree/frontend/dist",
			PythonEnv: "/rag/envs/ragstack", PreparedAt: "2026-09-14T09:00:00Z", PreparedBy: "local:3581"}
		c.args["name"] = "sandbox"
		err := planErr(t, oc, "create", c.args)
		if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), c.want) {
			t.Errorf("%s: %v, want a refusal mentioning %q", c.name, err, c.want)
		}
	}
	// Neither an artifact nor an image is a request that names no code.
	oc, _ := imageCreateFixture(t)
	if err := planErr(t, oc, "create", map[string]any{"name": "sandbox"}); !errors.Is(err, jobs.ErrValidation) {
		t.Errorf("no artifact, no image = %v", err)
	}
	// An external UI needs no artifact: python_env is the deployment default.
	p := plan(t, oc, "create", map[string]any{"name": "sandbox", "image": testImageName,
		"supervisor": supervisorInstance, "ui_mode": registry.UIModeExternal})
	if !strings.Contains(strings.Join(titles(p), "\n"), "skip the UI build") {
		t.Errorf("an external-UI image create builds a UI:\n%s", strings.Join(titles(p), "\n"))
	}
}

// `restore --as` of an image-mode source lays an image-mode twin down; one
// whose image is not prepared here, or a static-UI source with no artifact, is
// refused with a sentence that says which.
func TestRestoreAsInheritsImageMode(t *testing.T) {
	src := func(tn *registry.Tenant) {
		imageManaged(tn)
		tn.LastBackup = &registry.BackupRecord{Bundle: "/rag/backups/tenants/dev/20260914T080000Z-backup",
			At: "2026-09-14T08:00:00Z", Kind: "backup", Fenced: true, Verified: true, Scope: fullScope}
	}
	prepare := func(oc jobs.Context) {
		si := testServerImage()
		oc.Fleet.ServerImages = map[string]*registry.ServerImageRecord{si.Name: {Version: si.Version,
			Commit: si.Commit, Build: si.Build, SHA256: si.SHA256, Path: si.Path,
			PreparedAt: "2026-10-09T00:00:00Z", PreparedBy: "local:3581"}}
	}
	args := map[string]any{"from": "20260914T080000Z-backup", "as": "dev-copy"}

	oc, _ := fixture(t, "dev", src)
	prepare(oc)
	p := plan(t, oc, "restore", args)
	apiInstanceStep(t, p, "start the API instance api-dev-copy from "+testImageName)

	oc, _ = fixture(t, "dev", src)
	if err := planErr(t, oc, "restore", args); !errors.Is(err, jobs.ErrRefused) ||
		!strings.Contains(err.Error(), "not prepared on this host") {
		t.Errorf("an unprepared image = %v", err)
	}

	oc, _ = fixture(t, "dev", func(tn *registry.Tenant) { src(tn); tn.ArtifactID = "" })
	prepare(oc)
	if err := planErr(t, oc, "restore", args); !errors.Is(err, jobs.ErrRefused) ||
		!strings.Contains(err.Error(), "STATIC UI") {
		t.Errorf("a static-UI image source with no artifact = %v", err)
	}
}
