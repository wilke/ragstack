package ops

// `fleet artifact prepare` — the operator act that decides which code this
// deployment may run.
//
// An artifact is a reviewed SHA, checked out of the mirror into the ctl's own
// state tree, with its frontend dependencies installed. `tenant create` and
// `update-code` consume an artifact ID and nothing else: never a git ref,
// never a path, never a branch. That is the whole reason the type exists —
// "which commit is this tenant running?" has to be answerable from the
// registry, and a ref somebody can move is not an answer.
//
// It is CLI-only (`--direct`), and deliberately so:
//
//   - `npm ci` is the ONE step in the control plane that reaches the network.
//     A daemon that installed packages on request would be a daemon that runs
//     code chosen by whoever could reach its socket.
//   - the mirror path is an argument here. Over HTTP that would be a request
//     saying where to get code from.
//
// So the verb exists in the op registry (it is a job: planned, locked,
// checkpointed, audited, resumable like every other) and is absent from
// api/jobs.go's `opVerbs`, which is what makes the HTTP route answer 422.

import (
	"context"
	"fmt"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// artifactIDPattern is registry.json's ArtifactID, repeated here because this
// is the one place an ID is CONSTRUCTED rather than read.
var artifactIDPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$`)

// artifactIDUnsafe matches every run of characters that may not appear in an
// ID. Tags carry slashes (`release/1.5`), plus signs and colons; the ID is a
// directory name and a registry key, so they are folded to '-' rather than
// refused — an operator who tagged a release `release/1.5` should not have to
// re-tag it to deploy it.
var artifactIDUnsafe = regexp.MustCompile(`[^A-Za-z0-9._-]+`)

// ArtifactID is `<tag>-<sha[:12]>`, sanitized. Exported because the CLI prints
// the ID it is about to create before the job runs.
func ArtifactID(tag, sha string) (string, error) {
	if len(sha) < 12 {
		return "", fmt.Errorf("%w: %q is not a resolved 40-hex sha", jobs.ErrRefused, sha)
	}
	short := sha[:12]
	// The sha suffix is 13 of the 80 characters, and it is the half that must
	// survive: two artifacts prepared from the same moving tag differ only
	// there. So the TAG is what gets truncated when the pair is too long.
	base := strings.Trim(artifactIDUnsafe.ReplaceAllString(tag, "-"), "-._")
	if base == "" {
		base = "artifact"
	}
	if max := 80 - len(short) - 1; len(base) > max {
		base = strings.TrimRight(base[:max], "-._")
	}
	id := base + "-" + short
	if !artifactIDPattern.MatchString(id) {
		return "", fmt.Errorf("%w: %q and %q do not make an artifact id matching %s", jobs.ErrRefused, tag, sha,
			artifactIDPattern)
	}
	return id, nil
}

// ArtifactDir is where a prepared artifact lives under the ctl state dir.
func ArtifactDir(roots paths.Roots, id string) string {
	return filepath.Join(roots.CtlStateDir, "artifacts", id)
}

// npmCacheDir is the shared npm cache. One per deployment rather than one per
// artifact: `npm ci` with a cold cache is a few minutes of network for every
// artifact, and the cache is content-addressed, so sharing it is safe.
func npmCacheDir(roots paths.Roots) string { return filepath.Join(roots.RagRoot, "cache", "npm") }

func planArtifactPrepare(_ context.Context, p *planner, args map[string]any) error {
	p.need(model.LockRegistry, model.LockManifest, model.LockImages)
	f := p.oc.Fleet
	if f == nil {
		return fmt.Errorf("%w: artifact-prepare needs a registry snapshot", jobs.ErrValidation)
	}
	ref := argStringOf(args, "tag")
	if ref == "" {
		return fmt.Errorf("%w: artifact-prepare: tag is required", jobs.ErrValidation)
	}
	mirror := argStringOf(args, "mirror")
	if mirror == "" {
		mirror = p.op.deps.Mirror
	}
	if mirror == "" {
		return p.refuse("no mirror is configured and none was given: `fleet artifact prepare` checks the worktree out " +
			"of a bare `git clone --mirror` (CTL_MIRROR, or --mirror). Creating it is a deploy-time item: " +
			"`git clone --mirror <repo> /rag/repos/ragstack.git`")
	}
	if _, err := paths.SafePath("/", mirror); err != nil {
		return p.refuse("mirror %q is not an absolute, clean path the ctl may read: %v", mirror, err)
	}
	pythonEnv := argStringOf(args, "python_env")
	if pythonEnv == "" {
		pythonEnv = defaultPythonEnv(p.oc.Roots)
	}
	if _, err := paths.SafePath("/", pythonEnv); err != nil {
		return p.refuse("python_env %q is not an absolute, clean path: %v", pythonEnv, err)
	}
	compatible := argBoolOf(args, "schema_compatible")

	// The ID is NOT computed at plan time: it contains the sha, and resolving a
	// ref is a read of the host. A plan is a pure function of the registry
	// snapshot and the args (ops.go's package comment), so the plan says which
	// REF it will resolve and the run says what it resolved to.
	//
	// The consequence an operator sees: `--dry-run` cannot print the artifact
	// id. It prints the ref, the mirror and every step — which is what the
	// approval is actually about — and the id is in the job result.
	stateDir := filepath.Join(p.oc.Roots.CtlStateDir, "artifacts")
	cache := npmCacheDir(p.oc.Roots)

	// prepared is filled by the resolve step and read by the three after it.
	// A plan carries no host reads; the STEPS share what they learned.
	var (
		sha      string
		id       string
		worktree string
	)

	p.addFor("git", step{
		Kind: "git", Title: fmt.Sprintf("resolve %s in %s to a commit", ref, mirror),
		Targets: []string{ref, mirror},
		WouldRun: []model.WouldRun{{Argv: []string{"/usr/bin/git", "-C", mirror, "rev-parse", "--verify",
			ref + "^{commit}"}}},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			resolved, err := sc.Ops.Drivers.Git().ResolveRef(ctx, mirror, ref)
			if err != nil {
				return "", err
			}
			newID, err := ArtifactID(ref, resolved)
			if err != nil {
				return "", err
			}
			// The uniqueness check is HERE rather than at plan time for the same
			// reason the id is: the id is not known until the ref is resolved.
			// It reads the fleet the engine loaded under the locks, so no second
			// prepare can slip in between.
			if _, exists := sc.Ops.Fleet.Artifacts[newID]; exists {
				return "", fmt.Errorf("%w: artifact %s already exists (%s resolves to %s). An artifact is immutable: "+
					"prepare a different commit, or use the one that is there", jobs.ErrRefused, newID, ref, resolved)
			}
			sha, id = resolved, newID
			worktree = filepath.Join(stateDir, id, "worktree")
			// Planned.Result closes over the SAME map, so a step that learns a
			// fact writes it straight into the result the job records.
			p.result["artifact_id"], p.result["sha"], p.result["worktree"] = id, sha, worktree
			sc.Logf("%s → %s (artifact %s)", ref, resolved, newID)
			return fmt.Sprintf("%s = %s", ref, resolved), nil
		},
	})

	p.addFor("files", step{
		Kind: "fs", Title: "create the artifact directory (2770)", Targets: []string{stateDir},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			dir := filepath.Join(stateDir, id)
			if err := sc.Ops.Drivers.Files().MkdirAll(ctx, dir, dirMode); err != nil {
				return "", err
			}
			// The npm cache is NOT created here: it lives under <RagRoot>/cache,
			// outside every approved root on purpose (it is a download cache,
			// not state the ctl owns), so the Files driver refuses it — and npm
			// creates its own cache directory on first use anyway. On coconut
			// this step failed every artifact preparation until the cache
			// mkdir was removed.
			return dir, nil
		},
	})

	p.addFor("git", step{
		Kind: "git", Title: "check the worktree out, detached, at the resolved commit",
		Targets: []string{stateDir},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			// The DIRECTORY is the external ID, recorded before the checkout:
			// a crash between the two leaves a record naming the tree to remove.
			if err := sc.Checkpoint("worktree:" + worktree); err != nil {
				return "", err
			}
			if err := sc.Ops.Drivers.Git().AddWorktree(ctx, mirror, sha, worktree); err != nil {
				return "", err
			}
			return worktree, nil
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			if worktree == "" {
				return "no worktree was checked out", nil
			}
			return "removed " + worktree, sc.Ops.Drivers.Git().RemoveWorktree(ctx, mirror, worktree)
		},
	})

	p.addFor("build", step{
		Kind: "apptainer", Title: "npm ci in the artifact's frontend (the only step that reaches the network)",
		Targets:  []string{cache},
		Warnings: []string{"this step downloads packages: it is why `fleet artifact prepare` is CLI-only"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			if err := sc.Ops.Drivers.Build().NpmCI(ctx, worktree, cache); err != nil {
				return "", err
			}
			return "node_modules installed in " + worktree + "/frontend", nil
		},
	})

	p.addFor("registry", step{
		Kind: "registry", Title: "record the artifact in the registry", Targets: []string{stateDir},
		WouldWrite: []model.WouldWrite{{Path: p.oc.Roots.Registry(), Mode: "0660", Preview: model.NullString("")}},
		Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			save := p.op.deps.SaveFleet
			if save == nil {
				return "", fmt.Errorf("%w: the registry writer is not wired", jobs.ErrRefused)
			}
			cur := sc.Ops.Fleet
			if cur.Artifacts == nil {
				cur.Artifacts = map[string]*registry.Artifact{}
			}
			if _, exists := cur.Artifacts[id]; exists {
				return "", fmt.Errorf("%w: artifact %s already exists", jobs.ErrRefused, id)
			}
			cur.Artifacts[id] = &registry.Artifact{
				SHA: sha, Tag: ref, Worktree: worktree,
				// ui_dist names where a build IN THE ARTIFACT would land. The
				// contract requires an absolute path and `tenant create` does not
				// use it: a tenant's UI is built per tenant, because vite bakes
				// the gateway base path (/ragstack/<name>/ui/) into the bundle, so
				// one shared dist could only ever serve one tenant.
				UIDist:           filepath.Join(worktree, "frontend", "dist"),
				PythonEnv:        pythonEnv,
				PreparedAt:       p.stampRFC3339(sc),
				PreparedBy:       sc.Job.Principal,
				SchemaCompatible: compatible,
			}
			if err := save(cur); err != nil {
				return "", err
			}
			sc.Logf("artifact %s recorded at registry generation %d", id, cur.Generation)
			return id, nil
		},
		Rollback: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			save := p.op.deps.SaveFleet
			if save == nil || id == "" {
				return "nothing was recorded", nil
			}
			delete(sc.Ops.Fleet.Artifacts, id)
			return "removed the artifact row " + id, save(sc.Ops.Fleet)
		},
	})

	// The result is filled by the steps; Planned.Result is read after they run.
	p.result["tag"] = ref
	p.result["mirror"] = mirror
	p.result["python_env"] = pythonEnv
	p.result["schema_compatible"] = compatible
	p.warn("an artifact is immutable: its worktree is never updated in place, and `tenant create` pins the sha it records")
	return nil
}

// defaultPythonEnv is the shared conda env every tenant runs from unless the
// operator names another.
func defaultPythonEnv(roots paths.Roots) string {
	return filepath.Join(roots.RagRoot, "envs", "ragstack")
}

// dirMode is the mode every directory the ctl creates gets: 2770, setgid, so
// the group is inherited by everything written under it. The host has no
// `ragops` group — everything is `cels`, with 1869 members — so a tenant tree
// that did not carry setgid would end up owned by whichever operator happened
// to run the create.
const dirMode uint32 = 0o2770
