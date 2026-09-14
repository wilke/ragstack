package ops

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// bundlePlaceholder stands for the bundle id in a PLAN. The id is
// `<ts>-<kind>` and the timestamp is stamped when the job runs, but a plan
// must be a pure function of (registry, doctor, args) — a clock in it would
// make every job `plan_stale` — and plan.json's `would_write[].path` accepts
// only [A-Za-z0-9._/-], so it cannot carry a `<ts>` placeholder either. This
// one segment is what the plan shows and every step warns about.
const bundlePlaceholder = "new-bundle"

// bundleSuffix is the kind half of a bundle id: `<stamp>-backup`.
const bundleSuffix = "-backup"

// bundleIDPrefix is the external-ID namespace the bundle id is checkpointed
// under, so reconcile can find the directory a half-finished backup wrote
// into without having to guess at a clock.
const bundleIDPrefix = "bundle:"

// bundleID is THE bundle id of this job: one value, for every step of it.
//
// It used to be `p.stampOf(sc)`, called wherever a step needed a name — which
// is a fresh reading of the clock each time. On a host whose clock moves
// between two steps (it always does), the qdrant leg, the elasticsearch leg
// and the manifest could each land in a DIFFERENT `<stamp>-backup` directory,
// and a restore reading the manifest's directory would find one file in it.
// Worse, the sqlite leg did not stamp at all: it wrote the plan's
// `new-bundle` placeholder, so every backup this tenant ever took overwrote
// the same four state files and no `<stamp>-backup` directory held any.
//
// So the id is decided ONCE and every later step reads it back:
//
//   - from the job's own checkpoints when an earlier step already stamped it
//     (which is also what survives an interruption: a resumed backup must keep
//     writing into the directory it started);
//   - else from the job's created_at, which is a fact about the JOB and not
//     about the moment this particular step happened to run;
//   - else from the op clock, for a caller with no job (the unit runner).
func (p *planner) bundleID(sc *jobs.StepContext) string {
	if sc != nil && sc.Job != nil {
		for _, st := range sc.Job.Steps {
			for _, id := range st.ExternalIDs {
				if strings.HasPrefix(id, bundleIDPrefix) {
					return strings.TrimPrefix(id, bundleIDPrefix)
				}
			}
		}
		if t, err := time.Parse(time.RFC3339, sc.Job.CreatedAt); err == nil {
			return t.UTC().Format(stampFormat) + bundleSuffix
		}
	}
	return p.stampOf(sc) + bundleSuffix
}

// bundleDirOf is the real directory this job's bundle is written into, with
// the id recorded as an external id BEFORE the caller writes anything — which
// is what lets reconcile-on-restart go and look at the directory.
//
// Every RUN half that names a path inside the bundle goes through it; only the
// PLAN uses bundlePlaceholder.
func (p *planner) bundleDirOf(sc *jobs.StepContext) (string, error) {
	id := p.bundleID(sc)
	if err := sc.Checkpoint(bundleIDPrefix + id); err != nil {
		return "", err
	}
	return filepath.Join(sc.Ops.Roots.BackupsDir, p.t.Name, id), nil
}

// realBundlePath is bundleDirOf plus the relative path inside the bundle.
func (p *planner) realBundlePath(sc *jobs.StepContext, rel ...string) (string, error) {
	dir, err := p.bundleDirOf(sc)
	if err != nil {
		return "", err
	}
	return filepath.Join(append([]string{dir}, rel...)...), nil
}

// sqliteDBs are the state files a bundle captures, named as
// python/ragstack/config.py's *_STORE_PATH defaults spell them under
// <data_dir>/state. They are listed rather than read out of the registry
// because *_STORE_PATH is an executable-surface key and the registry records
// public settings only.
var sqliteDBs = []string{
	"ragstack_users.db", "ragstack_jobs.db", "ragstack_collections.db", "ragstack_grading.db",
}

// ---------------------------------------------------------------- backup

func planBackup(_ context.Context, p *planner, args map[string]any) error {
	fence := argBoolOf(args, "fence")
	p.need(model.LockTenant)
	if fence {
		p.need(model.LockGateway)
	}
	t := p.t
	bundleDir := filepath.Join(p.oc.Roots.BackupsDir, t.Name, bundlePlaceholder)
	p.result["fenced"] = fence
	p.result["best_effort"] = !fence

	if !fence {
		p.warn("an unfenced bundle is `best_effort: true`: it is NOT eligible for restore, handover or decommission " +
			"prerequisites, because nothing stopped the tenant writing while it was taken")
	} else {
		p.warn("the tenant is fenced for the duration: the gateway serves it read-only and the API is stopped")
		p.addGatewayReadonly(true)
		p.addAPIStop()
		p.addFenceVerify()
	}

	// ---- stores ----------------------------------------------------------
	//
	// One step per store rather than one per collection: the collection list
	// is a fact about the RUNNING store, and reading it at plan time would
	// make the plan depend on the host (and so on the moment it was made).
	// The per-collection checkpoint that reconcile needs happens inside the
	// step, before each snapshot call.
	p.addQdrantSnapshots(bundleDir)
	p.addESSnapshots(bundleDir)
	p.addSQLiteCopies(bundleDir)
	p.addBundleManifest(bundleDir, fence)

	if fence {
		p.addAPIStart()
		p.addGatewayReadonly(false)
	}
	return nil
}

// addGatewayReadonly plans the gateway half of a fence. The read-only FLAG is
// a registry field; what this step does is publish a generation that carries
// it, which is the only part of the change nginx can see.
func (p *planner) addGatewayReadonly(on bool) {
	what := "read-only"
	if !on {
		what = "read-write"
	}
	p.add(step{
		Kind: "nginx", Title: fmt.Sprintf("gateway: publish a generation serving %s %s", p.t.Name, what),
		Targets: []string{p.t.Name},
		Warnings: []string{"the read-only flag itself is a registry field; this step publishes the generation that " +
			"carries it, so the fence is visible to nginx"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			// There is no generation number to record before a publish — the
			// gateway package assigns it — so what is checkpointed first is
			// the INTENT, and the number as soon as it exists. A crash between
			// the two leaves "a publish was started" on the record, which is
			// what reconcile needs to know to go and look.
			if err := sc.Checkpoint("gateway:apply:pending"); err != nil {
				return "", err
			}
			gen, detail, err := sc.Ops.Drivers.Gateway().Apply(ctx, false)
			if err != nil {
				return "", err
			}
			if err := sc.Checkpoint("gateway:gen:" + strconv.Itoa(gen)); err != nil {
				return "", err
			}
			return detail, nil
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			_, detail, err := sc.Ops.Drivers.Gateway().Apply(ctx, false)
			return detail, err
		},
	})
}

// addAPIStop stops the tenant API whichever way this tenant is supervised.
func (p *planner) addAPIStop() {
	if p.t.Supervisor != supervisorSystemd {
		_ = p.planManualStop(nil)
		return
	}
	legs, _ := p.legs([]string{"api"})
	for _, c := range legs {
		p.addUnitStep("stop", c)
	}
}

func (p *planner) addAPIStart() {
	if p.t.Supervisor != supervisorSystemd {
		p.warn("this tenant is hand-started: the ctl stopped it for the fence but cannot start it again — " +
			"restart it the way it was started, or hand it over first")
		return
	}
	legs, _ := p.legs([]string{"api"})
	for _, c := range legs {
		p.addUnitStep("start", c)
	}
	p.addReadyStep(legs)
}

func (p *planner) addFenceVerify() {
	port := p.t.Ports.API
	p.addFor("proc", step{
		Kind: "probe", Title: fmt.Sprintf("fence verify: nothing listens on %d", port),
		Targets: []string{strconv.Itoa(port)},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			listening, err := sc.Ops.Drivers.Proc().Listening(ctx, port)
			if err != nil {
				return "", err
			}
			if listening {
				return "", fmt.Errorf("%w: something is still listening on %d, so the tenant is not fenced and this "+
					"bundle could not be called consistent", jobs.ErrRefused, port)
			}
			return "the tenant is fenced", nil
		},
	})
}

func (p *planner) addQdrantSnapshots(bundleDir string) {
	q := p.t.Stores.Qdrant
	if !q.Capabilities.Snapshot {
		p.skip("qdrant", "skip the qdrant leg",
			fmt.Sprintf("capabilities.snapshot is false for the qdrant store (ownership %s): the bundle records it as "+
				"`excluded` and cannot satisfy a full-recovery prerequisite for it", q.Ownership), q.URL)
		return
	}
	url := q.URL
	p.addFor("qdrant", step{
		Kind: "qdrant", Title: "snapshot every qdrant collection", Targets: []string{url},
		WouldWrite: []model.WouldWrite{{Path: filepath.Join(bundleDir, "qdrant"), Mode: "0640", Preview: ""}},
		Warnings:   []string{"one snapshot per collection; the collection list is read when the job runs"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			cols, err := sc.Ops.Drivers.Qdrant().Collections(ctx, url)
			if err != nil {
				return "", err
			}
			var made []string
			for _, c := range cols {
				// The collection is checkpointed BEFORE the snapshot call, so
				// a crash leaves a record naming the collection whose transient
				// ~2× copy may be sitting on the store's disk.
				if err := sc.Checkpoint("qdrant:pending:" + c); err != nil {
					return "", err
				}
				name, err := sc.Ops.Drivers.Qdrant().Snapshot(ctx, url, c)
				if err != nil {
					return "", fmt.Errorf("snapshotting collection %s: %w", c, err)
				}
				if err := sc.Checkpoint("qdrant:" + c + ":" + name); err != nil {
					return "", err
				}
				sc.Logf("collection %s -> %s", c, name)
				made = append(made, name)
			}
			return fmt.Sprintf("%d collection snapshot(s)", len(made)), nil
		},
	})
}

func (p *planner) addESSnapshots(bundleDir string) {
	es := p.t.Stores.Elasticsearch
	if !es.Capabilities.Snapshot {
		p.skip("es", "skip the elasticsearch leg",
			fmt.Sprintf("capabilities.snapshot is false for the elasticsearch store (ownership %s): the bundle records "+
				"it as `excluded`", es.Ownership), es.URL)
		return
	}
	url := es.URL
	p.addFor("elasticsearch", step{
		Kind: "es", Title: "snapshot every elasticsearch index into a per-bundle repo",
		Targets:    []string{url, "ctl-<bundle-id>"},
		WouldWrite: []model.WouldWrite{{Path: filepath.Join(bundleDir, "elasticsearch"), Mode: "0640", Preview: ""}},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			// The bundle id, not a fresh clock reading: the repo has to be the
			// one this job's other legs and its manifest name.
			id := p.bundleID(sc)
			repo, name := "ctl-"+id, id
			// Both names are the ctl's own, so — unlike qdrant's — a crash
			// leaves the exact repo a cleanup has to unregister; they are
			// recorded before the call, with the bundle id itself.
			if err := sc.Checkpoint(bundleIDPrefix+id, "es:"+repo+"/"+name); err != nil {
				return "", err
			}
			idx, err := sc.Ops.Drivers.Elasticsearch().Indices(ctx, url)
			if err != nil {
				return "", err
			}
			if err := sc.Ops.Drivers.Elasticsearch().Snapshot(ctx, url, repo, name); err != nil {
				return "", err
			}
			sc.Logf("repo %s, snapshot %s, %d index/indices", repo, name, len(idx))
			return fmt.Sprintf("snapshot %s in repo %s (%d indices)", name, repo, len(idx)), nil
		},
	})
}

func (p *planner) addSQLiteCopies(bundleDir string) {
	for _, db := range sqliteDBs {
		src := filepath.Join(p.tpaths.StateDir, db)
		dst := filepath.Join(bundleDir, "state", db)
		p.add(step{
			Kind: "sqlitebackup", Title: "copy " + db + " into the bundle", Targets: []string{src},
			WouldWrite: []model.WouldWrite{{Path: dst, Mode: "0640", Preview: ""}},
			Warnings: []string{"a byte copy under the fence; the WAL checkpoint + `VACUUM INTO` + `integrity_check` " +
				"of an UNFENCED copy lands in PR-D"},
			Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
				b, err := sc.Ops.Drivers.Files().ReadFile(ctx, src)
				if err != nil {
					// ONLY "the file is not there" is absence. A tenant that
					// never used a store has no file, and that is a fact about
					// the tenant; an EACCES, an EIO or a directory in its place
					// is a failure to read a database this bundle claims to
					// contain, and reporting it as `absent` produced a
					// succeeded step and a manifest saying `consistent: true`
					// over a bundle with a hole in it.
					if !errors.Is(err, fs.ErrNotExist) {
						return "", fmt.Errorf("reading %s: %w", src, err)
					}
					sc.Logf("%s is not present; nothing to copy", src)
					return "absent: " + db, nil
				}
				real, err := p.realBundlePath(sc, "state", db)
				if err != nil {
					return "", err
				}
				if err := sc.Checkpoint("file:" + real); err != nil {
					return "", err
				}
				if err := sc.Ops.Drivers.Files().WriteAtomic(ctx, real, b, 0o640); err != nil {
					return "", err
				}
				return fmt.Sprintf("%s (%d bytes)", real, len(b)), nil
			},
			Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
				real, err := p.realBundlePath(sc, "state", db)
				if err != nil {
					return "", err
				}
				return "removed " + real, sc.Ops.Drivers.Files().Remove(ctx, real)
			},
		})
	}
}

func (p *planner) addBundleManifest(bundleDir string, fence bool) {
	manifest := map[string]any{
		"schema_version":      p.oc.Fleet.SchemaVersion,
		"kind":                "backup",
		"fenced":              fence,
		"best_effort":         !fence,
		"paths_relative_to":   "RAG_ROOT",
		"tenant":              map[string]any{"name": p.t.Name, "manifest_name": p.t.ManifestName, "ports": p.t.Ports},
		"artifact":            string(p.t.ArtifactID),
		"stores":              storeInventory(p.t),
		"sqlite":              sqliteDBs,
		"verified":            false,
		"consistent":          fence,
		"secrets":             map[string]any{"encrypted": true, "file": "secrets.age"},
		"registry_generation": p.oc.Fleet.Generation,
	}
	body, _ := json.MarshalIndent(manifest, "", "  ")
	path := filepath.Join(bundleDir, "manifest.json")
	p.add(step{
		Kind: "fs", Title: "write the bundle manifest", Targets: []string{bundleDir},
		WouldWrite: []model.WouldWrite{p.preview(path, "0640", body)},
		Warnings: []string{"`" + bundlePlaceholder + "` stands for the bundle id `<ts>-backup`, which is stamped when " +
			"the job runs"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			real, err := p.realBundlePath(sc, "manifest.json")
			if err != nil {
				return "", err
			}
			if err := sc.Ops.Drivers.Files().WriteAtomic(ctx, real, body, 0o640); err != nil {
				return "", err
			}
			return real, nil
		},
	})
}

func storeInventory(t *registry.Tenant) map[string]any {
	return map[string]any{
		"qdrant": map[string]any{
			"ownership": t.Stores.Qdrant.Ownership, "url": t.Stores.Qdrant.URL,
			"included": t.Stores.Qdrant.Capabilities.Snapshot,
		},
		"elasticsearch": map[string]any{
			"ownership": t.Stores.Elasticsearch.Ownership, "url": t.Stores.Elasticsearch.URL,
			"included": t.Stores.Elasticsearch.Capabilities.Snapshot,
		},
		"neo4j": map[string]any{"ownership": t.Stores.Neo4j.Ownership, "included": false},
	}
}

// ---------------------------------------------------------------- restore

func planRestore(_ context.Context, p *planner, args map[string]any) error {
	p.need(model.LockRegistry, model.LockManifest, model.LockTenant, model.LockGateway)
	from, as := argStringOf(args, "from"), argStringOf(args, "as")

	// v1 restores into a FRESH tenant and nothing else. An in-place restore
	// would have to reconcile live credentials, a live gateway row and a live
	// store with a bundle taken at another moment; a fresh tenant has none of
	// those problems and can be compared with the original side by side.
	if p.oc.Fleet != nil {
		if _, exists := p.oc.Fleet.Tenants[as]; exists {
			return p.refuse("`as` must name a FRESH tenant; %s already exists. v1 restores side by side (in-place "+
				"restore is v1.x) — pick a new name", as)
		}
	}
	if err := paths.ValidateName(as); err != nil {
		return fmt.Errorf("%w: %s", jobs.ErrValidation, err.Error())
	}
	src := filepath.Join(p.oc.Roots.BackupsDir, p.t.Name, from)
	p.result["restored_as"] = as
	p.result["bundle"] = from

	p.add(step{
		Kind: "fs", Title: "verify the bundle manifest and SHA256SUMS", Targets: []string{src},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			b, err := sc.Ops.Drivers.Files().ReadFile(ctx, filepath.Join(src, "manifest.json"))
			if err != nil {
				return "", fmt.Errorf("%w: bundle %s is unreadable: %v", jobs.ErrRefused, from, err)
			}
			var man map[string]any
			if err := json.Unmarshal(b, &man); err != nil {
				return "", fmt.Errorf("%w: bundle %s has no readable manifest.json: %v", jobs.ErrRefused, from, err)
			}
			if fenced, _ := man["fenced"].(bool); !fenced {
				return "", fmt.Errorf("%w: bundle %s is best-effort (unfenced), so it cannot be restored from",
					jobs.ErrRefused, from)
			}
			return fmt.Sprintf("bundle %s is fenced and its manifest parses", from), nil
		},
	})
	p.add(step{
		Kind: "registry", Title: "allocate the fresh tenant " + as, Targets: []string{as},
		Run: p.pendingRun("registry", "AllocateFromBundle"),
	})
	p.add(step{
		Kind: "qdrant", Title: "recover every collection from the bundle's snapshots", Targets: []string{as},
		Run: p.pendingRun("qdrant", "Recover"),
	})
	p.add(step{
		Kind: "es", Title: "register the copied repo and _restore every index", Targets: []string{as},
		Run: p.pendingRun("elasticsearch", "Restore"),
	})
	p.add(step{
		Kind: "probe", Title: "verify counts and a fixture query against " + as, Targets: []string{as},
		Run: p.pendingRun("tenantapi", "VerifyRestore"),
	})
	p.warn("restore also serves as `backup verify`: it proves the bundle by rebuilding from it")
	return nil
}

// pendingRun is a step whose driver method lands in PR-D. The plan is
// rendered in full — a dry run has to be able to SHOW the operation — and the
// refusal happens when it runs, naming the method, exactly as the real driver
// set does.
func (p *planner) pendingRun(driver, method string) jobs.StepFunc {
	return func(context.Context, *jobs.StepContext) (string, error) {
		return "", fmt.Errorf("%w: %s.%s lands in PR-D", jobs.ErrRefused, driver, method)
	}
}
