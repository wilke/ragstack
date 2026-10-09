package ops

// `tenant update-code` (PR-F F5, brief §1.3) on the fake host: the step order,
// the worktree→image migration, image→image, the rollback of a failure at the
// post-checks back to the old image + old UI + OLD API running, the plan-time
// refusals, and the interrupted-after-the-swap recovery through `tenant start`.

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// The second server image: a later release at another commit, with an
// artifact prepared at that commit for the static UI.
var testImage2Bytes = []byte("ragstack server image v1.6.7 build 1")

const (
	testImage2Name  = "ragstack-server-v1.6.7-b1.sif"
	testArtifact2ID = "v1.6.7-cdcdcdcdcdcd"
	// oldWorktreeSHA is where a worktree-mode tenant's checkout sits before
	// its migration: code that is NOT the image's.
	oldWorktreeSHA = "efefefefefefefefefefefefefefefefefefefef"
)

func testServerImage2() *registry.ServerImage {
	sum := sha256.Sum256(testImage2Bytes)
	return &registry.ServerImage{Name: testImage2Name, Version: "v1.6.7", Commit: strings.Repeat("cd", 20), Build: 1,
		SHA256: hex.EncodeToString(sum[:]), Path: "/rag/data/ctl/images/server/" + testImage2Name}
}

func recordOf(si *registry.ServerImage) *registry.ServerImageRecord {
	return &registry.ServerImageRecord{Version: si.Version, Commit: si.Commit, Build: si.Build, SHA256: si.SHA256,
		Path: si.Path, PreparedAt: "2026-10-09T00:00:00Z", PreparedBy: "local:3581"}
}

// updateFixture is the dev tenant, ctl-run under `supervisor: instance`,
// ACTIVE with its API RUNNING — from its worktree (imageMode false) or from
// testServerImage (true) — with both server images prepared and on the host,
// an artifact at each image's commit with node_modules, a static UI being
// served from <data_dir>/ui/dist, and the worktree checked out where the row
// says.
func updateFixture(t *testing.T, imageMode bool) (jobs.Context, *drivers.Fake) {
	t.Helper()
	mutate := instanceManaged
	worktreeAt := oldWorktreeSHA
	if imageMode {
		mutate = imageManaged
		worktreeAt = testServerImage().Commit
	}
	var wt string
	oc, fake := fixtureFull(t, "dev", func(tn *registry.Tenant) {
		mutate(tn)
		wt = tn.Worktree
		if !imageMode {
			tn.Code = registry.Code{Tag: "v1.6.5", SHA: registry.NullString(oldWorktreeSHA)}
		}
	}, devImageEnv(), func(o *drivers.FakeOptions) {
		o.Worktrees = map[string]string{wt: worktreeAt}
	})
	f := oc.Fleet
	si1, si2 := testServerImage(), testServerImage2()
	f.ServerImages = map[string]*registry.ServerImageRecord{si1.Name: recordOf(si1), si2.Name: recordOf(si2)}
	f.Artifacts[testArtifact2ID] = &registry.Artifact{
		SHA: si2.Commit, Tag: "v1.6.7",
		Worktree:  "/rag/data/ctl/artifacts/" + testArtifact2ID + "/worktree",
		UIDist:    "/rag/data/ctl/artifacts/" + testArtifact2ID + "/worktree/frontend/dist",
		PythonEnv: "/rag/envs/ragstack", PreparedAt: "2026-10-09T09:00:00Z", PreparedBy: "local:3581",
		SchemaCompatible: true,
	}
	installNodeModules(fake, f.Artifacts[testArtifactID].Worktree)
	installNodeModules(fake, f.Artifacts[testArtifact2ID].Worktree)

	tn := oc.Tenant
	tp := paths.TenantPaths(oc.Roots, tn.Name, tn.ManifestName)
	ins := fake.FakeInstances()
	ins.BindInstancePort("qdrant-"+tn.ManifestName, tn.Ports.QdrantHTTP)
	ins.BindInstancePort("elasticsearch-"+tn.ManifestName, tn.Ports.ESHTTP)
	ins.BindInstancePort("postgres-"+tn.ManifestName, tn.Ports.PG)
	fake.FakeFiles().Dirs[tp.ESConfig] = 0o2770
	fake.FakeFiles().Put(tp.SecretsEnv, append(ledgerEnv(), []byte(
		"TENANT_PG_PASSWORD="+testSecret+"\n"+
			"APPTAINERENV_POSTGRES_PASSWORD="+testSecret+"\n")...), 0o640)
	for _, si := range []*registry.ServerImage{si1, si2} {
		body := testImageBytes
		if si.Name == si2.Name {
			body = testImage2Bytes
		}
		fake.FakeFiles().Put(si.Path, body, 0o640)
		ins.SetLabels(si.Path, testImageLabels(si))
	}
	seedState(fake, tn.Name)
	fake.FakeFiles().Put(distDir(tn)+"/index.html", []byte("the UI that was serving\n"), 0o644)

	// The API is UP, as the precondition says, started the way the row says.
	fake.FakeProc().FreePort(tn.Ports.API)
	_ = fake.FakeFiles().Remove(context.Background(), tp.PidFile)
	newRunner(oc, fake).runAll(t, plan(t, oc, "start", nil))
	return oc, fake
}

// updateArgs is the request the migration and the image→image upgrade make.
func updateArgs(image, artifact string) map[string]any {
	return map[string]any{"image": image, "artifact_id": artifact}
}

// The plan's order is the brief's: bundle, image proof, worktree, UI, stop,
// registry swap, start, post-checks, registry effect.
func TestUpdateCodePlansTheStepsInTheBriefsOrder(t *testing.T) {
	oc, _ := updateFixture(t, false)
	p := plan(t, oc, "update-code", updateArgs(testImageName, testArtifactID))
	order := []struct{ kind, substr string }{
		{"fs", "create the bundle directory"},
		{"registry", "record the bundle as this tenant's last backup"},
		{"probe", "prove server image " + testImageName},
		{"git", "check the worktree out at v1.6.6"},
		{"apptainer", "vite build --base /ragstack/dev/ui/ from artifact " + testArtifactID},
		{"fs", "swap the new build into place"},
		{"probe", "check that no ingest job is still running"},
		{"proc", "stop the API through its pidfile"},
		{"registry", "record server_image " + testImageName},
		{"instance", "start the API instance api-dev from " + testImageName},
		{"probe", "post-checks: the instance holds"},
		{"probe", "GET /ragstack/dev/ui/ through the live gateway"},
		{"registry", "record update-code on dev"},
	}
	last := -1
	for _, o := range order {
		i := stepIndex(p, o.kind, o.substr)
		if i < 0 {
			t.Fatalf("no %s step %q in:\n%s", o.kind, o.substr, strings.Join(titles(p), "\n"))
		}
		if i <= last {
			t.Errorf("%s %q is step %d, before the step it must follow (%d):\n%s", o.kind, o.substr, i+1, last+1,
				strings.Join(titles(p), "\n"))
		}
		last = i
	}
	if !p.Plan.RequiresConfirm || p.Plan.ConfirmValue != "dev" {
		t.Errorf("confirm = %v %q, want the tenant name", p.Plan.RequiresConfirm, p.Plan.ConfirmValue)
	}
	if got := p.Result()["kind"]; got != bundleKindPreUpdate {
		t.Errorf("bundle kind = %v, want pre-update", got)
	}
	warned := strings.Join(p.Plan.Warnings, "\n")
	for _, want := range []string{"cannot be resumed", "tenant start dev", "MIGRATION", "change ON DISK"} {
		if !strings.Contains(warned, want) {
			t.Errorf("no plan warning mentions %q:\n%s", want, warned)
		}
	}
	// The plan is pure: the same snapshot plans the same hash.
	if again := plan(t, oc, "update-code", updateArgs(testImageName, testArtifactID)); again.Plan.PlanHash != p.Plan.PlanHash {
		t.Errorf("two plans of the same request differ")
	}
}

// The first upgrade of a worktree-mode tenant: afterwards the row is in image
// mode, the API is the instance api-dev of the image (the spawned uvicorn is
// gone), /v1/version is the image's, the worktree is at its commit, the UI was
// rebuilt from the artifact, and the pre-update bundle is recorded.
func TestUpdateCodeMigratesAWorktreeTenantOntoAnImage(t *testing.T) {
	oc, fake := updateFixture(t, false)
	tn := oc.Tenant
	spawnedBefore := fake.Count("proc.Spawn")
	if spawnedBefore == 0 {
		t.Fatalf("the fixture did not spawn the worktree API")
	}
	p := plan(t, oc, "update-code", updateArgs(testImageName, testArtifactID))
	r := newRunner(oc, fake)
	r.runAll(t, p)

	row := oc.Fleet.Tenants["dev"]
	si := testServerImage()
	if row.ServerImage == nil || row.ServerImage.Name != si.Name || row.ServerImage.SHA256 != si.SHA256 {
		t.Fatalf("server_image = %+v", row.ServerImage)
	}
	if row.Code.Tag != "v1.6.6" || string(row.Code.SHA) != si.Commit || row.Code.PreviousImage != "" {
		t.Errorf("code = %+v", row.Code)
	}
	if row.RestartPending {
		t.Errorf("restart_pending is still set")
	}
	if rec, ok := row.LastOps["update-code"]; !ok || rec.Outcome != "succeeded" {
		t.Errorf("last_ops.update-code = %+v", row.LastOps)
	}
	if row.LastBackup == nil || row.LastBackup.Kind != bundleKindPreUpdate || !strings.HasSuffix(row.LastBackup.Bundle, "-pre-update") {
		t.Errorf("last_backup = %+v, want the pre-update bundle", row.LastBackup)
	}
	up, _, err := apiInstanceRunning(context.Background(), r.ctx(p.Steps[0]), "api-dev", tn.Ports.API)
	if err != nil || !up {
		t.Errorf("api-dev is not running and holding %d (%v)", tn.Ports.API, err)
	}
	if spec := apiSpec(t, fake); spec.SIF != si.Path || spec.ExtraEnv["APPTAINERENV_RAGSTACK_GIT_SHA"] != si.Commit {
		t.Errorf("api-dev ran %s with RAGSTACK_GIT_SHA %q", spec.SIF, spec.ExtraEnv["APPTAINERENV_RAGSTACK_GIT_SHA"])
	}
	if head, _ := fake.Git().HeadSHA(context.Background(), tn.Worktree); head != si.Commit {
		t.Errorf("the worktree is at %s, want the image's commit", head)
	}
	if got := fileAt(fake, distDir(tn)+"/index.html"); !strings.Contains(got, "fake vite build of "+
		oc.Fleet.Artifacts[testArtifactID].Worktree) {
		t.Errorf("dist/index.html = %q, want the artifact's build", got)
	}
	if v, _ := p.Result()["version"].(map[string]any); v["git_sha"] != si.Commit || v["version"] != "1.6.6" {
		t.Errorf("result.version = %v", p.Result()["version"])
	}
	log := strings.Join(fake.CallKeys(), "\n")
	// The worktree API was stopped through its pidfile, never started again.
	if fake.Count("proc.Spawn") != spawnedBefore || !strings.Contains(log, "proc.Signal(") {
		t.Errorf("the worktree API was not stopped exactly once:\n%s", log)
	}
	if !strings.Contains(log, "tenantapi.Version(") || !strings.Contains(log, "tenantapi.DeepHealth(") {
		t.Errorf("the post-checks did not ask the API:\n%s", log)
	}
}

// image → image: the previous image is recorded, the instance runs the new
// SIF, and the version answer is the new image's.
func TestUpdateCodeMovesAnImageTenantToAnotherImage(t *testing.T) {
	oc, fake := updateFixture(t, true)
	p := plan(t, oc, "update-code", updateArgs(testImage2Name, testArtifact2ID))
	newRunner(oc, fake).runAll(t, p)
	row := oc.Fleet.Tenants["dev"]
	si2 := testServerImage2()
	if row.ServerImage.Name != testImage2Name || row.Code.PreviousImage != testImageName ||
		string(row.Code.SHA) != si2.Commit || string(row.ArtifactID) != testArtifact2ID ||
		string(row.Code.PreviousArtifactID) != testArtifactID {
		t.Errorf("row after the swap: server_image %+v code %+v artifact %s", row.ServerImage, row.Code, row.ArtifactID)
	}
	if spec := apiSpec(t, fake); spec.SIF != si2.Path {
		t.Errorf("api-dev runs %s, want %s", spec.SIF, si2.Path)
	}
	if v, _ := p.Result()["version"].(map[string]any); v["version"] != "1.6.7" {
		t.Errorf("result.version = %v", p.Result()["version"])
	}
	if fake.Count("proc.Spawn") != 0 {
		t.Errorf("an image→image upgrade spawned a worktree API")
	}
}

// A failure at the post-checks (the API answers a version that is not the
// image's) rolls EVERYTHING back: the old image recorded, the old UI served,
// the worktree where it was, and the OLD API running.
func TestUpdateCodeRollsBackAFailedPostCheckToTheOldImageUIAndAPI(t *testing.T) {
	oc, fake := updateFixture(t, true)
	tn := oc.Tenant
	si1 := testServerImage()
	before := *oc.Fleet.Tenants["dev"]
	beforeCode, beforeImage := before.Code, *before.ServerImage
	// The API on the port answers somebody else's commit.
	fake.FakeTenantAPI().Versions = map[string]map[string]any{
		"http://127.0.0.1:" + itoa(tn.Ports.API): {"version": "1.6.7", "git_sha": strings.Repeat("00", 20)},
	}
	p := plan(t, oc, "update-code", updateArgs(testImage2Name, testArtifact2ID))
	failed, runErr, _ := runWithRollback(t, newRunner(oc, fake), p)
	if failed != stepIndex(p, "probe", "post-checks")+1 || !errors.Is(runErr, jobs.ErrRefused) {
		t.Fatalf("failed at step %d (%v), want the post-checks", failed, runErr)
	}
	row := oc.Fleet.Tenants["dev"]
	if row.ServerImage == nil || *row.ServerImage != beforeImage || row.Code != beforeCode {
		t.Errorf("the row was not restored: server_image %+v code %+v", row.ServerImage, row.Code)
	}
	if spec := apiSpec(t, fake); spec.SIF != si1.Path {
		t.Errorf("the API running after the rollback is %s, want the old image %s", spec.SIF, si1.Path)
	}
	up, _, err := apiInstanceRunning(context.Background(), newRunner(oc, fake).ctx(p.Steps[0]), "api-dev", tn.Ports.API)
	if err != nil || !up {
		t.Errorf("the old API is not running after the rollback (%v)", err)
	}
	if got := fileAt(fake, distDir(tn)+"/index.html"); got != "the UI that was serving\n" {
		t.Errorf("dist/index.html = %q, want the old UI back", got)
	}
	if head, _ := fake.Git().HeadSHA(context.Background(), tn.Worktree); head != si1.Commit {
		t.Errorf("the worktree is at %s, want %s again", head, si1.Commit)
	}
}

// The same failure while MIGRATING: the rollback returns the API to today's
// worktree launch — a spawned uvicorn — and puts the worktree back at its old
// HEAD BEFORE it spawns it, so the restarted API runs the code it ran.
func TestUpdateCodeRollsAMigrationBackToTheWorktreeLaunch(t *testing.T) {
	oc, fake := updateFixture(t, false)
	tn := oc.Tenant
	fake.FakeTenantAPI().Versions = map[string]map[string]any{
		"http://127.0.0.1:" + itoa(tn.Ports.API): {"version": "1.6.5", "git_sha": oldWorktreeSHA},
	}
	spawned := fake.Count("proc.Spawn")
	p := plan(t, oc, "update-code", updateArgs(testImageName, testArtifactID))
	failed, runErr, _ := runWithRollback(t, newRunner(oc, fake), p)
	if failed == 0 || runErr == nil {
		t.Fatalf("the upgrade did not fail")
	}
	row := oc.Fleet.Tenants["dev"]
	if row.ServerImage != nil || string(row.Code.SHA) != oldWorktreeSHA {
		t.Errorf("the row is not a worktree-mode row again: %+v %+v", row.ServerImage, row.Code)
	}
	if fake.Count("proc.Spawn") != spawned+1 {
		t.Errorf("the rollback did not spawn the worktree API again")
	}
	if _, listed, _ := instanceIn(context.Background(), newRunner(oc, fake).ctx(p.Steps[0]), "api-dev",
		jobs.NamespaceCtl); listed {
		t.Errorf("api-dev is still running after the rollback")
	}
	log := fake.CallKeys()
	checkout, spawn := -1, -1
	for i, c := range log {
		if strings.HasPrefix(c, "git.Checkout("+tn.Worktree+","+oldWorktreeSHA) && checkout < 0 {
			checkout = i
		}
		if strings.HasPrefix(c, "proc.Spawn(") {
			spawn = i
		}
	}
	if checkout < 0 || spawn < checkout {
		t.Errorf("the worktree was not put back before the API was spawned again (checkout %d, spawn %d):\n%s",
			checkout, spawn, strings.Join(log, "\n"))
	}
	if head, _ := fake.Git().HeadSHA(context.Background(), tn.Worktree); head != oldWorktreeSHA {
		t.Errorf("the worktree is at %s, want %s", head, oldWorktreeSHA)
	}
}

// A corrupted copy of the image is refused at step 2, before anything moves.
func TestUpdateCodeRefusesACorruptedImageBeforeAnythingMoves(t *testing.T) {
	oc, fake := updateFixture(t, true)
	fake.FakeFiles().Put(testServerImage2().Path, []byte("not the image that was prepared"), 0o640)
	p := plan(t, oc, "update-code", updateArgs(testImage2Name, testArtifact2ID))
	failed, runErr, _ := runWithRollback(t, newRunner(oc, fake), p)
	if failed != stepIndex(p, "probe", "prove server image")+1 || !errors.Is(runErr, jobs.ErrRefused) {
		t.Fatalf("failed at step %d (%v), want the image proof", failed, runErr)
	}
	if fake.Count("git.Checkout") != 0 || fake.Count("instances.Stop") != 0 {
		t.Errorf("something moved before the proof failed:\n%s", strings.Join(fake.CallKeys(), "\n"))
	}
}

// --no-rebuild-ui: an API-only patch leaves the served UI alone and needs no
// artifact.
func TestUpdateCodeWithoutAUIRebuildLeavesTheUIAlone(t *testing.T) {
	oc, fake := updateFixture(t, true)
	p := plan(t, oc, "update-code", map[string]any{"image": testImage2Name, "rebuild_ui": false})
	if hasStep(p, "apptainer", "vite build") || hasStep(p, "probe", "GET /ragstack/") {
		t.Errorf("a no-rebuild plan builds or probes the UI:\n%s", strings.Join(titles(p), "\n"))
	}
	newRunner(oc, fake).runAll(t, p)
	if got := fileAt(fake, distDir(oc.Tenant)+"/index.html"); got != "the UI that was serving\n" {
		t.Errorf("dist/index.html = %q", got)
	}
	if string(oc.Fleet.Tenants["dev"].ArtifactID) != testArtifactID {
		t.Errorf("artifact_id moved without a UI rebuild")
	}
}

// Everything the plan refuses, refused from the registry alone.
func TestUpdateCodeRefusals(t *testing.T) {
	cases := []struct {
		name   string
		image  bool
		mutate func(jobs.Context)
		args   map[string]any
		want   string
	}{
		{"unknown image", false, nil, map[string]any{"image": "ragstack-server-v9-b1.sif", "artifact_id": testArtifactID},
			"not prepared"},
		{"static UI with no artifact", false, nil, map[string]any{"image": testImageName}, "--no-rebuild-ui"},
		{"artifact at another commit", false, nil, updateArgs(testImageName, testArtifact2ID), "different code"},
		{"artifact without a rebuild", false, nil,
			map[string]any{"image": testImageName, "artifact_id": testArtifactID, "rebuild_ui": false}, "nothing would read"},
		{"the same image, no rebuild", true, nil, map[string]any{"image": testImageName, "rebuild_ui": false},
			"a restart, not an upgrade"},
		{"handover in flight", false, func(oc jobs.Context) {
			oc.Tenant.Handover = &registry.Handover{Phase: "released"}
		}, updateArgs(testImageName, testArtifactID), "handover in flight"},
		{"hand-started", false, func(oc jobs.Context) { oc.Tenant.Supervisor = "manual" },
			updateArgs(testImageName, testArtifactID), "handover"},
		{"systemd", false, func(oc jobs.Context) { oc.Tenant.Supervisor = "systemd" },
			updateArgs(testImageName, testArtifactID), "supervisor: instance"},
		{"stopped", false, func(oc jobs.Context) { oc.Tenant.State = "stopped" },
			updateArgs(testImageName, testArtifactID), "tenant start"},
		{"rebuild on an external UI", false, func(oc jobs.Context) {
			oc.Tenant.UI = registry.UI{Mode: registry.UIModeExternal, Port: 8090, Base: "/ragstack/dev/ui/"}
		}, map[string]any{"image": testImageName, "rebuild_ui": true, "artifact_id": testArtifactID}, "no dist"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			oc, _ := updateFixture(t, c.image)
			if c.mutate != nil {
				c.mutate(oc)
			}
			err := planErr(t, oc, "update-code", c.args)
			if !errors.Is(err, jobs.ErrRefused) || !strings.Contains(err.Error(), c.want) {
				t.Errorf("err = %v, want a refusal mentioning %q", err, c.want)
			}
		})
	}
	// The same image WITH a UI rebuild is allowed: it is how a static UI is
	// rebuilt at the image's own commit.
	oc, _ := updateFixture(t, true)
	plan(t, oc, "update-code", updateArgs(testImageName, testArtifactID))
}

// The args are the contract's: image required, the old {artifact_id,
// restart} shape refused.
func TestUpdateCodeValidatesItsArguments(t *testing.T) {
	op, _ := NewRegistry(Deps{}).Lookup("update-code")
	for _, bad := range []map[string]any{
		{},
		{"artifact_id": testArtifactID},
		{"image": testImageName, "restart": true},
		{"image": "../etc/passwd"},
		{"image": testImageName, "rebuild_ui": "yes"},
	} {
		if err := op.Validate(bad); !errors.Is(err, jobs.ErrValidation) {
			t.Errorf("Validate(%v) = %v, want a validation error", bad, err)
		}
	}
	if err := op.Validate(map[string]any{"image": testImageName, "rebuild_ui": false}); err != nil {
		t.Errorf("a valid request was refused: %v", err)
	}
	if !op.Destructive() {
		t.Errorf("update-code is not destructive")
	}
}

// A daemon that dies between the registry swap and the new start leaves a job
// the engine cannot resume — the swap moved the registry generation, so the
// re-plan's hash differs — and a row that already names the new image with
// nothing running. `tenant start` is the recovery, and it starts the NEW image.
func TestAnUpdateInterruptedAfterTheSwapIsRecoveredByTenantStart(t *testing.T) {
	oc, fake := updateFixture(t, true)
	p := plan(t, oc, "update-code", updateArgs(testImage2Name, testArtifact2ID))
	swap := stepIndex(p, "registry", "record server_image "+testImage2Name)
	r := newRunner(oc, fake)
	for _, s := range p.Steps[:swap+1] {
		if _, err := r.run(s); err != nil {
			t.Fatalf("step %d (%s): %v", s.Plan.N, s.Plan.Title, err)
		}
	}
	// "The daemon died." The plan the engine would rebuild is not the plan it
	// ran: the generation the original plan hashed is gone.
	oc.Tenant = oc.Fleet.Tenants["dev"]
	if again := plan(t, oc, "update-code", updateArgs(testImage2Name, testArtifact2ID)); again.Plan.PlanHash == p.Plan.PlanHash {
		t.Errorf("the re-plan after the swap has the same hash: an interrupted job would resume over a moved registry")
	}
	if _, listed, _ := instanceIn(context.Background(), r.ctx(p.Steps[0]), "api-dev", jobs.NamespaceCtl); listed {
		t.Fatalf("api-dev is running after the stop")
	}
	// The recovery the runbook names.
	newRunner(oc, fake).runAll(t, plan(t, oc, "start", nil))
	if spec := apiSpec(t, fake); spec.SIF != testServerImage2().Path {
		t.Errorf("tenant start ran %s, want the new image the row names", spec.SIF)
	}
}

// Pep440 is version.py's pep440 for the receipt's shapes.
func TestPep440MirrorsVersionPy(t *testing.T) {
	for in, want := range map[string]string{
		"v1.6.6": "1.6.6", "v1.6.6+a2be96f": "1.6.6+a2be96f", "1.6.6": "1.6.6", "conformance": "conformance",
		"v2.0.0rc1": "2.0.0rc1",
	} {
		if got := Pep440(in); got != want {
			t.Errorf("Pep440(%q) = %q, want %q", in, got, want)
		}
	}
}

// The UI probe asks the live gateway only about a tenant it routes: a
// sandbox without --with-gateway has no route to prove, and the upgrade must
// not fail over a 404 it did not cause.
func TestUpdateCodeSkipsTheUIProbeOfAnUnroutedTenant(t *testing.T) {
	oc, fake := updateFixture(t, true)
	fake.FakeGateway().Routed = nil
	p := plan(t, oc, "update-code", updateArgs(testImage2Name, testArtifact2ID))
	newRunner(oc, fake).runAll(t, p)
	if fake.Count("gateway.Probe") != 0 {
		t.Errorf("the UI of an unrouted tenant was probed through the gateway")
	}
	if oc.Fleet.Tenants["dev"].ServerImage.Name != testImage2Name {
		t.Errorf("the upgrade did not complete")
	}
}
