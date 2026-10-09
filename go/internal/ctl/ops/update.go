package ops

// `tenant update-code <t> --image NAME` — the one-job upgrade of a ctl-run
// tenant onto a prepared API server image (PR-F F5, brief §1.3).
//
// The plan, in order (each step has a rollback unless noted):
//
//  1. a LIGHT `pre-update` bundle (config + state; the backup planner's own
//     steps, so it is the bundle `backup --scope config,state` writes);
//  2. probe the image: sha256 == the prepared record == the row-to-be, the
//     labels (`Instances().Labels`) == the receipt the record copied, the
//     commit resolves in the mirror (no rollback: it changes nothing);
//  3. (rebuild_ui) build the static UI from the ARTIFACT's worktree into
//     dist.building — the slow part, while the API still serves; no swap yet;
//  4. stop the API with the launch rendered from the CURRENT row (worktree or
//     image); rollback starts that same launch again and waits for it. The
//     tree is still the one it started from: everything that changes it
//     comes after this step and rolls back before this rollback runs;
//  5. check the worktree out at the image's commit — ALWAYS, so `gowe render`,
//     `env`, the drift check and the next static build read the code the API
//     runs; the previous HEAD is checkpointed and checked out again on
//     rollback. After the stop, because a running worktree API imports
//     modules lazily and must never load one from a tree it did not start on;
//  6. (rebuild_ui) swap dist.building in, keeping dist.prev-<ts> — the steps
//     `set-ui-mode static` uses (prepare.go); rollback swaps back;
//  7. the registry swap: server_image, code.{tag,sha}, code.previous_image
//     (and artifact_id when one was given), the previous values checkpointed
//     and restored by the rollback. This write moves the registry generation;
//  8. start the API with the NEW launch (an `api-<manifest>` instance of the
//     image); rollback stops it;
//  9. post-checks: the instance holds the port, /health, /v1/health/deep and
//     /v1/version (git_sha == the image's commit, version == pep440 of its
//     version), then the UI through the gateway when it was rebuilt and the
//     gateway routes the tenant (no rollback: a probe changes nothing — a
//     failure here unwinds 8…1);
//  10. the registry effect: last_ops.update-code, restart_pending=false.
//
// A failure anywhere is the engine's reverse rollback: the old UI is back,
// the old image (or the worktree launch, for a tenant this job was
// migrating) is the one recorded, the worktree is at its previous HEAD, and
// the OLD API is running. What cannot be rolled back by the engine is an
// INTERRUPTED job: step 1's last_backup record and step 7's swap move the
// registry generation, which the plan hash covers, so a daemon that dies after
// them leaves a job `rebuild` refuses as moved (jobs/engine.go). The runbook
// has the recovery by stop point; after step 7 the row already names the new
// image and nothing runs — `tenant start <t>` or this op again.
//
// Plans are pure: the image file, its labels, the mirror, the worktree's HEAD
// and the running ingest jobs are all step-time probes.

import (
	"context"
	"encoding/json"
	"fmt"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/render"
)

// bundleKindPreUpdate is the manifest kind of update-code's safety net.
const bundleKindPreUpdate = "pre-update"

// worktreePrevID prefixes the checkpoint of the worktree's HEAD before step 5
// moved it; step 5's rollback checks it out again.
const worktreePrevID = "worktree-prev:"

// updatePrevID prefixes the checkpoint of the row's code fields before step 6
// replaced them, as JSON (updatePrev).
const updatePrevID = "update-code-prev:"

// updatePrev is what step 6 overwrites and its rollback puts back.
type updatePrev struct {
	ServerImage *registry.ServerImage `json:"server_image"`
	Code        registry.Code         `json:"code"`
	ArtifactID  registry.NullString   `json:"artifact_id"`
}

// Pep440 is python/ragstack/version.py's pep440 for the two shapes a server
// image's receipt version takes: `vX` → `X`, `vX+<sha>` → `X+<sha>` (the
// leading `v` dropped, nothing else touched). It is what the API answers as
// `/v1/version.version` when it runs from that image: the runscript's
// RAGSTACK_GIT_TAG override, or the image's _release.py, either way through
// pep440.
func Pep440(version string) string {
	if strings.HasPrefix(version, "v") {
		return version[1:]
	}
	return version
}

func planUpdateCode(_ context.Context, p *planner, args map[string]any) error {
	p.need(model.LockTenant, model.LockRegistry, model.LockManifest)
	t, f := p.t, p.oc.Fleet
	name := t.Name
	image := argStringOf(args, "image")
	artifactID := argStringOf(args, "artifact_id")

	// ---- preconditions: the registry, and nothing else ------------------
	if t.Handover != nil {
		return p.refuse("%s has a handover in flight (phase %s): the tenant is between two accounts, and an upgrade "+
			"would move its code under that protocol. Take it, commit it, or abandon it first", name, t.Handover.Phase)
	}
	if err := p.requireSupervised("update-code"); err != nil {
		return err
	}
	if t.Supervisor != supervisorInstance {
		return p.refuse("%s is supervised by %s units; update-code moves the API onto a server image, which runs as "+
			"an apptainer instance only `supervisor: instance` supervises (there are no systemd units for one)", name,
			t.Supervisor)
	}
	if t.State != string(model.StateActive) {
		return p.refuse("%s is %s, not active: update-code swaps the code under a RUNNING API and proves the new one "+
			"answers. A tenant that is down is brought up with `ragstack-ctl tenant start %s`, never by an upgrade",
			name, orNone(t.State), name)
	}
	if t.Worktree == "" {
		return p.refuse("%s has no worktree recorded: an image-mode tenant keeps its worktree at the image's commit "+
			"(gowe render, env and the static UI build read it), and there is none to keep", name)
	}
	if p.op.deps.Mirror == "" {
		return p.refuse("no mirror is configured (CTL_MIRROR), so the image's commit cannot be proved to be one the " +
			"worktree can be checked out at")
	}
	if f == nil {
		return p.refuse("the engine loaded no registry")
	}
	rec, ok := f.ServerImages[image]
	if !ok || rec == nil {
		return p.refuse("server image %q is not prepared on this host: update-code takes the name of an image "+
			"`ragstack-ctl fleet image prepare --sif …` has verified and recorded (`ragstack-ctl fleet image list`)", image)
	}
	rebuild := t.UI.Mode == registry.UIModeStatic
	if v, set := args["rebuild_ui"].(bool); set {
		rebuild = v
	}
	if rebuild && t.UI.Mode != registry.UIModeStatic {
		return p.refuse("rebuild_ui asks for a static UI build, and %s's UI is `%s`: nginx serves no dist for it. "+
			"Drop --rebuild-ui (or `ragstack-ctl tenant set-ui-mode %s static` first)", name, orNone(t.UI.Mode), name)
	}
	var artifact *registry.Artifact
	switch {
	case rebuild && artifactID == "":
		return p.refuse("%s's UI is static and is rebuilt with the API (rebuild_ui), from a prepared ARTIFACT at the "+
			"image's commit %s — and none was given. Pass --artifact <id> for an artifact prepared at that commit, or "+
			"--no-rebuild-ui for an API-only patch that keeps the UI being served", name, rec.Commit)
	case rebuild:
		a, ok := f.Artifacts[artifactID]
		if !ok || a == nil {
			return p.refuse("artifact %q is not prepared on this host (`ragstack-ctl fleet artifact list`)", artifactID)
		}
		if a.SHA != rec.Commit {
			return p.refuse("artifact %s is at commit %s but server image %s was built at %s: the UI and the API "+
				"would come from different code. Prepare an artifact at %s, or pass --no-rebuild-ui", artifactID,
				a.SHA, image, rec.Commit, rec.Commit)
		}
		artifact = a
	case artifactID != "":
		return p.refuse("--artifact names the source of the UI rebuild, and rebuild_ui is false: nothing would read "+
			"%s. Drop --artifact, or rebuild the UI", artifactID)
	}
	same := t.ImageMode() && t.ServerImage.Name == image
	if same && !rebuild {
		return p.refuse("%s already runs %s, and rebuild_ui is false: this upgrade would stop the API and start the "+
			"same image again, which is a restart, not an upgrade — that is `ragstack-ctl tenant restart %s --only api`",
			name, image, name)
	}

	// ---- the two launches, rendered now (plans are pure) ----------------
	port := t.Ports.API
	_, curErr := p.apiLaunch(port)
	if curErr != nil {
		return p.refuse("%s's CURRENT API cannot be rendered from its row (%v): a failed upgrade would have nothing "+
			"to start again. Repair the row (or `tenant start` it) before upgrading", name, curErr)
	}
	newRow := cloneTenant(t)
	newRow.ServerImage = &registry.ServerImage{Name: image, Version: rec.Version, Commit: rec.Commit,
		Build: rec.Build, SHA256: rec.SHA256, Path: rec.Path}
	newRow.Code.Tag, newRow.Code.SHA = rec.Version, registry.NullString(rec.Commit)
	if t.ImageMode() && !same {
		newRow.Code.PreviousImage = t.ServerImage.Name
	}
	if artifact != nil {
		newRow.ArtifactID = registry.NullString(artifactID)
		if string(t.ArtifactID) != artifactID {
			newRow.Code.PreviousArtifactID = t.ArtifactID
		}
	}
	next, err := p.apiLaunchFor(newRow, port)
	if err != nil {
		return p.refuse("%s's API cannot be started from %s as the row would record it: %v", name, image, err)
	}
	migrating := !t.ImageMode()
	commit := rec.Commit

	// ---- 1. the pre-update bundle (light) --------------------------------
	light := scopeSet{scopeConfig: true, scopeState: true}
	if _, err := p.addBackupSteps(backupPlanArgs{Scope: light, Secrets: secretsInclude,
		Kind: bundleKindPreUpdate}); err != nil {
		return err
	}

	// ---- 2. probe the image ---------------------------------------------
	mirror := p.op.deps.Mirror
	p.addFor("instance", step{
		Kind: "probe", Title: "prove server image " + image + ": sha256, labels, and its commit in the mirror",
		Targets: []string{rec.Path, commit},
		Warnings: []string{"the file must hash to the prepared record's sha256 (" + rec.SHA256 + "), its labels " +
			"(`apptainer inspect --labels`) must be the receipt's — version " + rec.Version + ", commit " + commit +
			", build " + strconv.Itoa(rec.Build) + ", role server — and the commit must resolve in " + mirror +
			" to itself; the image start in step 7 proves the file and the labels again"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			if sc.Ops.Fleet != nil {
				if now, ok := sc.Ops.Fleet.ServerImages[image]; !ok || now == nil || now.SHA256 != rec.SHA256 {
					return "", fmt.Errorf("%w: the fleet's record of %s is not the one this job was planned "+
						"against", jobs.ErrRefused, image)
				}
			}
			detail, err := probeAPIImage(ctx, sc, next)
			if err != nil {
				return "", err
			}
			got, err := sc.Ops.Drivers.Git().ResolveRef(ctx, mirror, commit)
			if err != nil {
				return "", fmt.Errorf("%w: %s's commit %s does not resolve in %s (%v): push it to the mirror first",
					jobs.ErrRefused, image, commit, mirror, err)
			}
			if got != commit {
				return "", fmt.Errorf("%w: %s resolves to %s in %s, not to itself", jobs.ErrRefused, commit, got, mirror)
			}
			return detail + "; " + commit[:12] + " is in the mirror", nil
		},
	})

	// ---- 3. the static UI, built from the artifact (no swap yet) ---------
	//
	// The slow half runs while the API still serves; the swap waits for the
	// stop (step 6), so nothing the running tenant reads changes under it.
	dist := distDir(t)
	base := uiBase(t)
	staging := filepath.Join(filepath.Dir(dist), stagingDist)
	if rebuild {
		p.addUIStagingBuild(artifact.Worktree, base, staging,
			"vite build --base "+base+" from artifact "+artifactID+" into "+stagingDist,
			filepath.Join(artifact.Worktree, "frontend"))
	} else {
		why := "rebuild_ui is false: the UI being served is left exactly as it is"
		if t.UI.Mode != registry.UIModeStatic {
			why = "the UI is `" + orNone(t.UI.Mode) + "`, not static: there is no dist to build"
		}
		p.skip("apptainer", "skip the UI rebuild", why, dist)
	}

	// ---- 4. stop the API (the CURRENT launch, on the tree it started from) -
	//
	// Its rollback restarts that launch; the worktree is still the one it ran
	// from, because the checkout (5) comes after it and rolls back before it.
	origin := fmt.Sprintf("http://127.0.0.1:%d", port)
	p.addNoRunningIngestBefore(origin, p.tpaths.SecretsEnv, "the upgrade stops the API under them")
	_, _, _, _, apiUnit, _ := render.UnitNames(name)
	if err := (instanceSupervisor{}).stopAPI(p, apiLeg(p, apiUnit)); err != nil {
		return err
	}

	// ---- 5. the worktree at the image's commit (the API is stopped) -------
	worktree := t.Worktree
	p.addFor("git", step{
		Kind: "git", Title: "check the worktree out at " + rec.Version + " (" + commit[:12] + ")",
		Targets: []string{worktree, commit},
		Warnings: []string{"ALWAYS, in image mode: gowe render, env, the code drift check and the next static build " +
			"read the worktree, so it is kept at the commit the API runs. A worktree with local changes is refused, " +
			"never overwritten; the previous HEAD is recorded and checked out again by the rollback. It runs AFTER the " +
			"stop: a running worktree API imports modules lazily (graph extraction, restore, tool checks), and none of " +
			"them may come from a tree it was not started on"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			git := sc.Ops.Drivers.Git()
			prev, err := git.HeadSHA(ctx, worktree)
			if err != nil {
				return "", fmt.Errorf("reading %s's HEAD: %w", worktree, err)
			}
			if err := sc.Checkpoint(worktreePrevID + prev); err != nil {
				return "", err
			}
			if err := git.Checkout(ctx, worktree, commit); err != nil {
				return "", err
			}
			return fmt.Sprintf("%s @ %s (was %s)", worktree, commit[:12], shortSHA(prev)), nil
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			prev, ok := externalIDValue(sc.Step.ExternalIDs, worktreePrevID)
			if !ok {
				return "the worktree was not moved", nil
			}
			if err := sc.Ops.Drivers.Git().Checkout(ctx, worktree, prev); err != nil {
				return "", err
			}
			return worktree + " is at " + shortSHA(prev) + " again", nil
		},
	})

	// ---- 6. the dist swap ------------------------------------------------
	if rebuild {
		p.addDistSwap(dist, staging)
	}

	// ---- 7. the registry swap --------------------------------------------
	p.add(step{
		Kind: "registry", Title: "record server_image " + image + " and code " + rec.Version + " on " + name,
		Targets:    []string{name},
		WouldWrite: []model.WouldWrite{{Path: p.oc.Roots.Registry(), Mode: "0660", Preview: model.NullString("")}},
		Warnings: []string{"this write moves the registry generation: a job INTERRUPTED after it (the daemon died) " +
			"cannot be resumed — the recovery is `ragstack-ctl tenant start " + name + "` (the row already names " +
			image + ") or update-code again. A failure inside the job rolls it back like every other step"},
		Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			cur := p.rowOf(sc)
			if cur == nil {
				return "", fmt.Errorf("%w: %s is no longer in the registry", jobs.ErrRefused, name)
			}
			prev, err := json.Marshal(updatePrev{ServerImage: cur.ServerImage, Code: cur.Code,
				ArtifactID: cur.ArtifactID})
			if err != nil {
				return "", err
			}
			if err := sc.Checkpoint(updatePrevID + string(prev)); err != nil {
				return "", err
			}
			if err := p.saveTenant(sc, "", func(row *registry.Tenant) error {
				si := *newRow.ServerImage
				row.ServerImage = &si
				row.Code = newRow.Code
				row.ArtifactID = newRow.ArtifactID
				return nil
			}); err != nil {
				return "", err
			}
			return fmt.Sprintf("%s server_image = %s, code %s @ %s (generation %d)", name, image, rec.Version,
				commit[:12], sc.Ops.Fleet.Generation), nil
		},
		Rollback: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			raw, ok := externalIDValue(sc.Step.ExternalIDs, updatePrevID)
			if !ok {
				return "nothing was written", nil
			}
			var prev updatePrev
			if err := json.Unmarshal([]byte(raw), &prev); err != nil {
				return "", fmt.Errorf("reading the recorded previous code of %s: %w", name, err)
			}
			if p.rowOf(sc) == nil {
				return "the registry row is gone; nothing to restore", nil
			}
			if err := p.saveTenant(sc, "", func(row *registry.Tenant) error {
				row.ServerImage, row.Code, row.ArtifactID = prev.ServerImage, prev.Code, prev.ArtifactID
				return nil
			}); err != nil {
				return "", err
			}
			if prev.ServerImage == nil {
				return name + " is a worktree-mode row again (code " + prev.Code.Tag + ")", nil
			}
			return name + " records server_image " + prev.ServerImage.Name + " again", nil
		},
	})

	// ---- 8. start the API (the NEW launch) -------------------------------
	p.addAPIInstanceStart(next)

	// ---- 9. post-checks ---------------------------------------------------
	wantVersion := Pep440(rec.Version)
	p.addFor("tenantapi", step{
		Kind: "probe", Title: "post-checks: the instance holds " + strconv.Itoa(port) + ", /health, /v1/health/deep, " +
			"/v1/version is " + wantVersion + " at " + commit[:12],
		Targets: []string{origin},
		Warnings: []string{"the admin credential is read from the tenant's env files at RUN time and is never in the " +
			"plan, the log or the job result",
			"/v1/version must answer git_sha " + commit + " and version " + wantVersion + " (pep440 of " +
				rec.Version + "): the API that answers is the image this job started, not a process that was " +
				"already on the port"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			ready, err := awaitAPIReady(ctx, sc, next)
			if err != nil {
				return "", err
			}
			api := sc.Ops.Drivers.TenantAPI()
			if err := api.Health(ctx, origin); err != nil {
				return "", fmt.Errorf("GET %s/health: %w", origin, err)
			}
			key, err := adminKeyFromEnvFiles(ctx, sc, p.tpaths.TenantEnv, p.tpaths.SecretsEnv)
			if err != nil {
				return "", err
			}
			if key == "" {
				return "", fmt.Errorf("%w: no admin key could be read from %s's env files, so the version the API "+
					"runs cannot be proved", jobs.ErrRefused, name)
			}
			if err := api.DeepHealth(ctx, origin, key); err != nil {
				return "", fmt.Errorf("GET %s/v1/health/deep: %w", origin, err)
			}
			v, err := api.Version(ctx, origin, key)
			if err != nil {
				return "", fmt.Errorf("GET %s/v1/version: %w", origin, err)
			}
			gotSHA, _ := v["git_sha"].(string)
			gotVersion, _ := v["version"].(string)
			if gotSHA != commit || gotVersion != wantVersion {
				return "", fmt.Errorf("%w: /v1/version answered version %q at git_sha %q; %s is version %q at %s — "+
					"the API on %d is not the image this job started", jobs.ErrRefused, gotVersion, gotSHA, image,
					wantVersion, commit, port)
			}
			p.result["version"] = v
			return ready + "; health, deep health ok; version " + gotVersion + " at " + commit[:12], nil
		},
	})
	if rebuild {
		// addUIProbe's question, asked only of a tenant the LIVE gateway
		// routes: an unrouted tenant (a selftest sandbox without
		// --with-gateway, a tenant not yet published) has no UI route to
		// prove, and a 404 there would fail an upgrade over a fact this op
		// did not change. set-ui-mode publishes first and so always asks.
		p.addFor("gateway", step{
			Kind: "probe", Title: "GET " + base + " through the live gateway (expect 200), when it routes " + name,
			Targets: []string{base},
			Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
				routed, why, err := gatewayRoutes(ctx, sc, name)
				if err != nil {
					return "", err
				}
				if !routed {
					sc.Logf("skipped: %s", why)
					return "skipped: " + why, nil
				}
				return probeUI(ctx, sc, base)
			},
		})
	}

	// ---- 10. the registry effect -------------------------------------------
	p.addRegistryEffect("update-code", fmt.Sprintf("record update-code on %s (restart_pending cleared)", name),
		func(row *registry.Tenant) { row.RestartPending = false })

	// ---- what the plan says ---------------------------------------------
	from := "the worktree launch (python_env " + orNone(t.PythonEnv) + ")"
	if t.ImageMode() {
		from = "server image " + t.ServerImage.Name
	}
	p.result["image"] = image
	prevImage := ""
	if t.ImageMode() {
		prevImage = t.ServerImage.Name
	}
	p.result["previous_image"] = nullString(prevImage)
	p.result["version"] = rec.Version
	p.result["commit"] = commit
	p.result["rebuild_ui"] = rebuild
	p.result["artifact_id"] = nullString(artifactID)
	p.result["migration"] = migrating
	p.warn("%s moves from %s to server image %s (version %s, commit %s): the API is DOWN from step %d's stop "+
		"until the new instance answers", name, from, image, rec.Version, commit[:12], stopStepN(p))
	p.warn("the worktree and, with rebuild_ui, the served UI change ON DISK only after the API is stopped (the " +
		"UI is BUILT before the stop, into dist.building); a rollback puts both back, keeping the new build as " +
		"dist.building and the old one as dist")
	if migrating {
		p.warn("this is the MIGRATION of a worktree-mode tenant: server_image is set for the first time, python_env " +
			"stays recorded and unused, and a rollback returns the API to today's worktree launch, on its previous " +
			"checkout")
	}
	p.warn("a job INTERRUPTED (the daemon died) cannot be resumed once the pre-update bundle is recorded: that "+
		"write and the registry swap both move the registry generation, which the plan hash covers. Interrupted "+
		"after the swap, the row already names %s and nothing may be running: recover with `ragstack-ctl tenant "+
		"start %s` or run update-code again (docs/runbooks/tenant-upgrade.md, \"When it fails\")", image, name)
	p.warn("the pre-update bundle is LIGHT (config + state, no store snapshots, no fence): it rebuilds the tenant's "+
		"configuration and identity, not its stores — which this op does not touch. A failed job rolls it back to "+
		"%s/<id>.partial with the rest", filepath.Join(p.oc.Roots.BackupsDir, name))
	return nil
}

// stopStepN is the number of the API stop step planned so far (the last step
// whose title starts with "stop the API"), for the plan's warning.
func stopStepN(p *planner) int {
	for i := len(p.steps) - 1; i >= 0; i-- {
		if strings.HasPrefix(p.steps[i].Plan.Title, "stop the API") {
			return p.steps[i].Plan.N
		}
	}
	return 0
}

// shortSHA is a commit as the step details print it.
func shortSHA(s string) string {
	if len(s) > 12 {
		return s[:12]
	}
	return s
}
