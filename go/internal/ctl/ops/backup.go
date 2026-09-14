package ops

import (
	"archive/tar"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/render"
	"github.com/ragstack/ragstack/internal/ctl/version"
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

// envLayoutManaged is registry.json's tenant.env_layout value that means the
// secret-class keys have been moved out of tenant.env into secrets.env — which
// is what makes tenant.env a PUBLIC file a bundle may copy in the clear.
const envLayoutManaged = "managed"

// bundleKind is the manifest's `kind` for the backup verb. The other two
// kinds bundle_manifest.json allows (`pre-update`, `recovery`) are written by
// update-code and by a recovery drill, neither of which exists in v1.
const bundleKind = "backup"

// partialSuffix marks a bundle that is still being written. The directory is
// created as `<id>.partial` and renamed to `<id>` as the LAST step before the
// registry is told about it, so an interrupted job leaves something an
// operator (and `backup prune`) can recognise as unfinished rather than a
// directory that looks exactly like a complete bundle and is missing its last
// three legs.
const partialSuffix = ".partial"

// bundleIDPrefix is the external-ID namespace the bundle id is checkpointed
// under, so reconcile can find the directory a half-finished backup wrote
// into without having to guess at a clock.
const bundleIDPrefix = "bundle:"

// partsDir holds one small JSON record per LEG, written as that leg finishes.
//
// It exists because the manifest is assembled at the end from facts only the
// earlier steps could observe — the point counts on either side of a snapshot,
// the integrity string of a VACUUMed database, the digest of every file copied
// — and a resumed job re-plans: the steps that already succeeded do not run
// again, so anything they kept in the planner's memory is gone by the time the
// manifest step runs. Written down, those facts survive an interruption, and
// the bundle carries the evidence for every line of its own manifest.
const partsDir = "parts"

// backupReserveBytes is the free space a backup refuses to start without,
// whatever it is about to write: the plan's "recovery reserve". A host whose
// backup filesystem is this close to full has a problem no bundle should be
// allowed to finish.
const backupReserveBytes = 5 << 30

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

// bundleStamp is the `<ts>` half of a bundle id — the elasticsearch repository
// name the contract spells (`ctl-<ts>`) is built from it rather than from the
// whole id, which carries the kind as well.
func bundleStamp(id string) string {
	if i := strings.Index(id, "-"); i > 0 {
		return id[:i]
	}
	return id
}

// bundleDirOf is the FINISHED bundle's directory. Only the last steps (the
// rename, the tar, the registry record) name it; everything that writes goes
// through partialDirOf.
func (p *planner) bundleDirOf(sc *jobs.StepContext) (string, error) {
	id := p.bundleID(sc)
	// Once per STEP, not once per path: a leg that builds a dozen paths would
	// otherwise write the same external id a dozen times into its own record,
	// which makes a step log unreadable and a reconcile no better informed.
	if !recorded(sc, bundleIDPrefix+id) {
		if err := sc.Checkpoint(bundleIDPrefix + id); err != nil {
			return "", err
		}
	}
	return filepath.Join(sc.Ops.Roots.BackupsDir, p.t.Name, id), nil
}

// recorded reports whether this step already checkpointed id.
func recorded(sc *jobs.StepContext, id string) bool {
	if sc == nil || sc.Step == nil {
		return false
	}
	for _, have := range sc.Step.ExternalIDs {
		if have == id {
			return true
		}
	}
	return false
}

// partialDirOf is the directory this job actually writes into, with the id
// recorded as an external id BEFORE the caller writes anything — which is what
// lets reconcile-on-restart go and look at the directory.
func (p *planner) partialDirOf(sc *jobs.StepContext) (string, error) {
	dir, err := p.bundleDirOf(sc)
	if err != nil {
		return "", err
	}
	return dir + partialSuffix, nil
}

// partialPath is partialDirOf plus the relative path inside the bundle.
func (p *planner) partialPath(sc *jobs.StepContext, rel ...string) (string, error) {
	dir, err := p.partialDirOf(sc)
	if err != nil {
		return "", err
	}
	return filepath.Join(append([]string{dir}, rel...)...), nil
}

// stateDB is one SQLite database a bundle captures: the file under
// <data_dir>/state and the tenant.env key that names it.
//
// They are listed rather than read out of the registry because *_STORE_PATH is
// an executable-surface key and the registry records public settings only; the
// names are python/ragstack/config.py's defaults and render.TenantEnv's.
type stateDB struct {
	EnvKey string
	File   string
}

var sqliteDBs = []stateDB{
	{"USER_STORE_PATH", "ragstack_users.db"},
	{"JOB_STORE_PATH", "ragstack_jobs.db"},
	{"COLLECTION_STORE_PATH", "ragstack_collections.db"},
	{"GRADING_STORE_PATH", "ragstack_grading.db"},
}

// BundleManifestRequired is contracts/ctl/schemas/bundle_manifest.json's
// top-level `required` list, in the contract's own order.
//
// It is here rather than in the CLI because `backup verify`'s "the manifest
// carries every member the contract demands" and this package's "the manifest
// this code writes carries them" have to be the SAME list — and a Go test
// (bundle_test.go) reads the schema file and fails when the two drift, so a
// member added to the contract becomes a member verify demands without anybody
// remembering to copy it across.
var BundleManifestRequired = []string{
	"schema_version", "kind", "bundle_id", "created_at", "created_by", "ctl_version", "fenced",
	"best_effort", "tenant", "artifact", "python_env", "images", "paths_relative_to", "inventory",
	"stores", "sqlite", "files", "external", "secrets", "units", "registry_row", "migrate_md",
	"consistent", "verified", "warnings", "sha256sums",
}

// ---------------------------------------------------------------- manifest shapes
//
// These are contracts/ctl/schemas/bundle_manifest.json, in Go. The JSON tags
// are the schema's property names and nothing is omitempty: `additionalProperties:
// false` and a long `required` list mean the manifest is wrong both when it
// carries a member the schema does not know AND when it drops one the schema
// demands, so a struct that silently omitted an empty array would produce a
// bundle no restore would accept.

type bundleFile struct {
	File    string `json:"file"`
	PathRel string `json:"path_rel"`
	Bytes   int64  `json:"bytes"`
	Sha256  string `json:"sha256"`
}

type qdrantCollection struct {
	Name         string  `json:"name"`
	PointsBefore int64   `json:"points_before"`
	PointsAfter  int64   `json:"points_after"`
	Included     bool    `json:"included"`
	File         *string `json:"file"`
	Sha256       *string `json:"sha256"`
}

type esIndex struct {
	Name       string `json:"name"`
	DocsBefore int64  `json:"docs_before"`
	DocsAfter  int64  `json:"docs_after"`
	Included   bool   `json:"included"`
}

type sqliteEntry struct {
	EnvKey         string `json:"env_key"`
	PathRel        string `json:"path_rel"`
	File           string `json:"file"`
	Bytes          int64  `json:"bytes"`
	Sha256         string `json:"sha256"`
	IntegrityCheck string `json:"integrity_check"`
}

type externalRef struct {
	Kind   string `json:"kind"`
	Ref    string `json:"ref"`
	Reason string `json:"reason"`
}

// ---- the per-leg records written into parts/ ----

type qdrantPart struct {
	Ownership   string             `json:"ownership"`
	URL         string             `json:"url"`
	Collections []qdrantCollection `json:"collections"`
	Inventory   []string           `json:"inventory"`
	Warnings    []string           `json:"warnings"`
}

type esPart struct {
	Ownership string    `json:"ownership"`
	URL       string    `json:"url"`
	Repo      string    `json:"repo"`
	Snapshot  string    `json:"snapshot"`
	Indices   []esIndex `json:"indices"`
	Complete  bool      `json:"complete"`
	Files     []string  `json:"files"`
	Inventory []string  `json:"inventory"`
	Warnings  []string  `json:"warnings"`
}

type sqlitePart struct {
	Present bool        `json:"present"`
	Entry   sqliteEntry `json:"entry"`
}

type postgresPart struct {
	Kind      string `json:"kind"`
	Ownership string `json:"ownership"`
	Included  bool   `json:"included"`
	File      string `json:"file"`
	Sha256    string `json:"sha256"`
}

type filesPart struct {
	Files    []bundleFile `json:"files"`
	Warnings []string     `json:"warnings"`
}

type secretsPart struct {
	Included       bool     `json:"included"`
	File           string   `json:"file"`
	RecipientsFile string   `json:"recipients_file"`
	Fingerprints   []string `json:"key_fingerprints"`
	Warnings       []string `json:"warnings"`
}

// ---------------------------------------------------------------- backup

func planBackup(_ context.Context, p *planner, args map[string]any) error {
	fence := argBoolOf(args, "fence")
	tarIt := argBoolOf(args, "tar")
	// The registry lock as well as the tenant's: the last step records
	// `last_backup` and `last_ops.backup`, and the manifest projection is
	// derived from the registry, so the same two locks a settings write takes.
	p.need(model.LockRegistry, model.LockManifest, model.LockTenant)
	if fence {
		p.need(model.LockGateway)
	}
	t := p.t
	bundleDir := filepath.Join(p.oc.Roots.BackupsDir, t.Name, bundlePlaceholder)
	p.result["fenced"] = fence
	p.result["best_effort"] = !fence
	p.result["kind"] = bundleKind

	if !fence {
		p.warn("an unfenced bundle is `best_effort: true`: it is NOT eligible for restore, handover or decommission " +
			"prerequisites, because nothing stopped the tenant writing while it was taken")
	}

	// The precheck comes FIRST, before the fence: refusing a backup for want
	// of disk after the gateway has been made read-only and the API stopped
	// would be an outage in the service of an operation that was never going
	// to finish.
	p.addFreeSpaceCheck()

	if fence {
		p.warn("the tenant is fenced for the duration: the gateway serves it read-only and the API is stopped")
		p.addGatewayReadonly(true)
		p.addAPIStop()
		p.addFenceVerify()
	}

	p.addBundleDir(bundleDir)

	// ---- stores ----------------------------------------------------------
	//
	// One step per store rather than one per collection: the collection list
	// is a fact about the RUNNING store, and reading it at plan time would
	// make the plan depend on the host (and so on the moment it was made).
	// The per-collection checkpoint that reconcile needs happens inside the
	// step, before each snapshot call.
	p.addQdrantSnapshots(bundleDir, fence)
	p.addESSnapshots(bundleDir, fence)
	p.addSQLiteCopies(bundleDir)
	p.addPostgresDump(bundleDir)
	p.addConfigCopies(bundleDir)
	p.addSecrets(bundleDir)
	p.addMigrateMD(bundleDir)
	p.addBundleManifest(bundleDir, fence)
	p.addBundleFinalize(bundleDir)
	if tarIt {
		p.addBundleTar(bundleDir)
	}
	p.addBackupRecord(fence)

	if fence {
		p.addAPIStart()
		p.addGatewayReadonly(false)
	}
	return nil
}

// addFreeSpaceCheck refuses a bundle the filesystem cannot hold.
//
// The plan asks for ≥ 1.2× the size of what is being copied plus a reserve.
// The 1.2× half needs the SIZE of the qdrant storage, the ES data, the state
// files and the postgres data, and the ctl has nowhere to read that from: the
// registry records no store sizes, and walking four trees with `du` is exactly
// the kind of unbounded work a job step must not do while holding the tenant
// lock. So the reserve is ENFORCED and the ratio is reported as unmeasurable —
// an honest gap an operator can see, rather than a check that silently
// approved everything.
func (p *planner) addFreeSpaceCheck() {
	root := p.oc.Roots.BackupsDir
	p.addFor("files", step{
		Kind: "probe", Title: "check the backup filesystem has room", Targets: []string{root},
		Warnings: []string{"the registry records no store sizes, so the plan's 1.2× ratio cannot be computed " +
			"here; what is enforced is the 5 GiB recovery reserve"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			free, err := sc.Ops.Drivers.Files().DiskFree(ctx, root)
			if err != nil {
				return "", fmt.Errorf("reading the free space on %s: %w", root, err)
			}
			if free < backupReserveBytes {
				return "", fmt.Errorf("%w: %s has %s free, under the %s recovery reserve: free space before "+
					"taking a bundle", jobs.ErrRefused, root, humanBytes(free), humanBytes(backupReserveBytes))
			}
			sc.Logf("%s free on %s", humanBytes(free), root)
			return humanBytes(free) + " free", nil
		},
	})
}

// addBundleDir creates the `.partial` tree every later step writes into.
func (p *planner) addBundleDir(bundleDir string) {
	p.addFor("files", step{
		Kind: "fs", Title: "create the bundle directory (written as <id>" + partialSuffix + ", mode 2770)",
		Targets: []string{bundleDir},
		// No would_write row: plan.json types a mode as `^0[0-7]{3}$`, which
		// cannot spell the setgid bit this directory needs (2770 — so every
		// file written into it inherits the group rather than the writer's
		// primary one). The mode is in the title instead, where an operator
		// reads it, rather than as a value the contract would have to call
		// 0770 and the run would then contradict.
		Warnings: []string{"the bundle is written under a `" + partialSuffix + "` name and renamed only once its " +
			"manifest is in it, so an interrupted backup never looks like a complete one"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			dir, err := p.partialDirOf(sc)
			if err != nil {
				return "", err
			}
			if err := sc.Checkpoint("dir:" + dir); err != nil {
				return "", err
			}
			for _, sub := range []string{"", partsDir} {
				if err := sc.Ops.Drivers.Files().MkdirAll(ctx, filepath.Join(dir, sub), 0o2770); err != nil {
					return "", err
				}
			}
			return dir, nil
		},
	})
}

// ---------------------------------------------------------------- qdrant

func (p *planner) addQdrantSnapshots(bundleDir string, fence bool) {
	q := p.t.Stores.Qdrant
	if !q.Capabilities.Snapshot {
		p.skip("qdrant", "skip the qdrant leg",
			fmt.Sprintf("capabilities.snapshot is false for the qdrant store (ownership %s): the bundle records it as "+
				"`excluded` and cannot satisfy a full-recovery prerequisite for it", q.Ownership), q.URL)
		return
	}
	url := q.URL
	snapshotsDir := p.tpaths.QdrantSnapshots
	p.addFor("qdrant", step{
		Kind: "qdrant", Title: "snapshot every qdrant collection into the bundle", Targets: []string{url},
		WouldWrite: []model.WouldWrite{{Path: filepath.Join(bundleDir, "qdrant"), Mode: "0640", Preview: ""}},
		Warnings: []string{"one snapshot per collection, counted before and after; the collection list is read " +
			"when the job runs"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			drv := sc.Ops.Drivers.Qdrant()
			files := sc.Ops.Drivers.Files()
			cols, err := drv.Collections(ctx, url)
			if err != nil {
				return "", err
			}
			part := qdrantPart{Ownership: q.Ownership, URL: url, Inventory: cols,
				Collections: []qdrantCollection{}, Warnings: []string{}}
			for _, c := range cols {
				before, err := drv.Count(ctx, url, c)
				if err != nil {
					return "", fmt.Errorf("counting collection %s: %w", c, err)
				}
				// The collection is checkpointed BEFORE the snapshot call, so
				// a crash leaves a record naming the collection whose transient
				// ~2× copy may be sitting on the store's disk.
				if err := sc.Checkpoint("qdrant:pending:" + c); err != nil {
					return "", err
				}
				name, err := drv.Snapshot(ctx, url, c)
				if err != nil {
					return "", fmt.Errorf("snapshotting collection %s: %w", c, err)
				}
				if err := sc.Checkpoint("qdrant:" + c + ":" + name); err != nil {
					return "", err
				}
				dst, err := p.partialPath(sc, "qdrant", c, name)
				if err != nil {
					return "", err
				}
				if err := files.MkdirAll(ctx, filepath.Dir(dst), 0o2770); err != nil {
					return "", err
				}
				// Rename, not copy: the snapshot is already on the same
				// filesystem (both are under <data_dir>'s volume), so moving it
				// is atomic and does not double the transient cost that taking
				// it already paid.
				if err := files.Rename(ctx, filepath.Join(snapshotsDir, c, name), dst); err != nil {
					return "", fmt.Errorf("moving the snapshot of %s into the bundle: %w", c, err)
				}
				sum, size, err := files.Sha256(ctx, dst)
				if err != nil {
					return "", fmt.Errorf("checksumming the snapshot of %s: %w", c, err)
				}
				after, err := drv.Count(ctx, url, c)
				if err != nil {
					return "", fmt.Errorf("re-counting collection %s: %w", c, err)
				}
				rel := filepath.Join("qdrant", c, name)
				row := qdrantCollection{Name: c, PointsBefore: before, PointsAfter: after,
					Included: true, File: &rel, Sha256: &sum}
				if fence && before != after {
					part.Warnings = append(part.Warnings, fmt.Sprintf(
						"collection %s held %d points before the snapshot and %d after it, under a fence that was "+
							"supposed to stop every writer: the bundle is not consistent", c, before, after))
				}
				sc.Logf("collection %s -> %s (%d bytes, %d points)", c, rel, size, after)
				part.Collections = append(part.Collections, row)
			}
			if err := p.writePart(ctx, sc, "qdrant", part); err != nil {
				return "", err
			}
			return fmt.Sprintf("%d collection snapshot(s)", len(part.Collections)), nil
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			// Whatever snapshots this step made and did not move are deleted
			// from the STORE: a failed backup must not leave a second copy of
			// a collection growing inside the tenant's own storage.
			n := 0
			for _, id := range sc.Step.ExternalIDs {
				rest, ok := strings.CutPrefix(id, "qdrant:")
				if !ok || strings.HasPrefix(rest, "pending:") {
					continue
				}
				c, name, ok := strings.Cut(rest, ":")
				if !ok {
					continue
				}
				if err := sc.Ops.Drivers.Qdrant().DeleteSnapshot(ctx, url, c, name); err != nil {
					return "", err
				}
				n++
			}
			return fmt.Sprintf("deleted %d snapshot(s) from the store", n), nil
		},
	})
}

// ---------------------------------------------------------------- elasticsearch

func (p *planner) addESSnapshots(bundleDir string, fence bool) {
	es := p.t.Stores.Elasticsearch
	if !es.Capabilities.Snapshot {
		p.skip("es", "skip the elasticsearch leg",
			fmt.Sprintf("capabilities.snapshot is false for the elasticsearch store (ownership %s): the bundle records "+
				"it as `excluded`", es.Ownership), es.URL)
		return
	}
	url := es.URL
	hostRepos := p.tpaths.ESSnapshots
	// The location elasticsearch is told is the path INSIDE its container —
	// its `path.repo` — while the directory the ctl creates and later moves is
	// the host side of that same bind. Handing ES the host path would register
	// a repository at a location it cannot see.
	containerRepos := string(es.PathRepo)
	if containerRepos == "" {
		containerRepos = hostRepos
	}
	p.addFor("elasticsearch", step{
		Kind: "es", Title: "snapshot every elasticsearch index into a per-bundle repo, verify it and move it into the bundle",
		Targets:    []string{url, "ctl-<ts>"},
		WouldWrite: []model.WouldWrite{{Path: filepath.Join(bundleDir, "elasticsearch"), Mode: "0640", Preview: ""}},
		Warnings: []string{"the repository is this bundle's alone and is unregistered again before the directory " +
			"moves; the verification re-registers the same directory READ-ONLY and lists it, because `_restore` has " +
			"no dry run"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			drv := sc.Ops.Drivers.Elasticsearch()
			files := sc.Ops.Drivers.Files()
			// The bundle id, not a fresh clock reading: the repo has to be the
			// one this job's other legs and its manifest name.
			id := p.bundleID(sc)
			stamp := bundleStamp(id)
			repo, verifyRepo, name := "ctl-"+stamp, "verify-"+id, id
			hostDir, containerDir := filepath.Join(hostRepos, id), containerRepos+"/"+id
			// Both names are the ctl's own, so — unlike qdrant's — a crash
			// leaves the exact repo a cleanup has to unregister; they are
			// recorded before the call, with the bundle id itself.
			if err := sc.Checkpoint(bundleIDPrefix+id, "es:"+repo+"/"+name); err != nil {
				return "", err
			}
			if err := files.MkdirAll(ctx, hostDir, 0o2770); err != nil {
				return "", err
			}
			if err := drv.RegisterRepo(ctx, url, repo, containerDir, false); err != nil {
				return "", err
			}
			idx, err := drv.Indices(ctx, url)
			if err != nil {
				return "", err
			}
			part := esPart{Ownership: es.Ownership, URL: url, Repo: repo, Snapshot: name,
				Indices: []esIndex{}, Inventory: idx, Files: []string{}, Warnings: []string{}}
			before := map[string]int64{}
			for _, i := range idx {
				n, err := drv.Count(ctx, url, i)
				if err != nil {
					return "", fmt.Errorf("counting index %s: %w", i, err)
				}
				before[i] = n
			}
			if err := drv.Snapshot(ctx, url, repo, name); err != nil {
				return "", err
			}
			for _, i := range idx {
				after, err := drv.Count(ctx, url, i)
				if err != nil {
					return "", fmt.Errorf("re-counting index %s: %w", i, err)
				}
				part.Indices = append(part.Indices, esIndex{Name: i, DocsBefore: before[i], DocsAfter: after, Included: true})
				if fence && before[i] != after {
					part.Warnings = append(part.Warnings, fmt.Sprintf(
						"index %s held %d documents before the snapshot and %d after it, under a fence: the bundle "+
							"is not consistent", i, before[i], after))
				}
			}
			// The verification. A snapshot elasticsearch reports as SUCCESS is
			// a claim about what it wrote; this is the question asked back of
			// the directory, through a SECOND registration that is read-only
			// so the check itself can never write into the repository it is
			// verifying.
			if err := sc.Checkpoint("es:verify:" + verifyRepo); err != nil {
				return "", err
			}
			if err := drv.RegisterRepo(ctx, url, verifyRepo, containerDir, true); err != nil {
				return "", fmt.Errorf("re-registering %s read-only to verify it: %w", containerDir, err)
			}
			listed, err := drv.Snapshots(ctx, url, verifyRepo)
			if err != nil {
				return "", fmt.Errorf("listing the snapshots of the verify repository: %w", err)
			}
			part.Complete = containsStr(listed, name)
			if !part.Complete {
				return "", fmt.Errorf("%w: the repository at %s does not hold the snapshot %s this job just took "+
					"(it holds %v), so the bundle cannot claim the elasticsearch leg", jobs.ErrRefused,
					containerDir, name, listed)
			}
			if err := drv.UnregisterRepo(ctx, url, verifyRepo); err != nil {
				return "", err
			}
			// The repository registration goes BEFORE the directory moves:
			// elasticsearch must not be holding a repository whose files are
			// about to walk away underneath it.
			if err := drv.UnregisterRepo(ctx, url, repo); err != nil {
				return "", err
			}
			dst, err := p.partialPath(sc, "elasticsearch", "snapshots", id)
			if err != nil {
				return "", err
			}
			if err := files.MkdirAll(ctx, filepath.Dir(dst), 0o2770); err != nil {
				return "", err
			}
			if err := files.Rename(ctx, hostDir, dst); err != nil {
				return "", fmt.Errorf("moving the snapshot repository into the bundle: %w", err)
			}
			rel := filepath.Join("elasticsearch", "snapshots", id)
			moved, err := walkFiles(ctx, files, dst)
			if err != nil {
				part.Warnings = append(part.Warnings, "the bundle's snapshot directory ("+rel+") could not be listed")
			}
			for _, f := range moved {
				part.Files = append(part.Files, filepath.Join(rel, f))
			}
			if err := p.writePart(ctx, sc, "elasticsearch", part); err != nil {
				return "", err
			}
			sc.Logf("repo %s, snapshot %s, %d index/indices, %d file(s) in the bundle", repo, name, len(idx), len(part.Files))
			return fmt.Sprintf("snapshot %s in repo %s (%d indices)", name, repo, len(idx)), nil
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			// Unregistering an unknown repository is a no-op, so the rollback
			// is safe whether the step failed before or after either call.
			drv := sc.Ops.Drivers.Elasticsearch()
			for _, id := range sc.Step.ExternalIDs {
				switch {
				case strings.HasPrefix(id, "es:verify:"):
					if err := drv.UnregisterRepo(ctx, url, strings.TrimPrefix(id, "es:verify:")); err != nil {
						return "", err
					}
				case strings.HasPrefix(id, "es:"):
					repo, _, _ := strings.Cut(strings.TrimPrefix(id, "es:"), "/")
					if err := drv.UnregisterRepo(ctx, url, repo); err != nil {
						return "", err
					}
				}
			}
			return "the per-bundle repositories are unregistered", nil
		},
	})
}

// ---------------------------------------------------------------- sqlite

func (p *planner) addSQLiteCopies(bundleDir string) {
	for _, db := range sqliteDBs {
		db := db
		src := filepath.Join(p.tpaths.StateDir, db.File)
		dst := filepath.Join(bundleDir, "state", db.File)
		p.addFor("sqlite", step{
			Kind: "sqlitebackup", Title: "copy " + db.File + " into the bundle", Targets: []string{src},
			WouldWrite: []model.WouldWrite{{Path: dst, Mode: "0640", Preview: ""}},
			Warnings: []string{"WAL checkpoint, `PRAGMA integrity_check` and `VACUUM INTO` — never a byte copy, " +
				"which of a database with a live WAL beside it produces a file that opens and is missing the last writes"},
			Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
				real, err := p.partialPath(sc, "state", db.File)
				if err != nil {
					return "", err
				}
				if err := sc.Ops.Drivers.Files().MkdirAll(ctx, filepath.Dir(real), 0o2770); err != nil {
					return "", err
				}
				if err := sc.Checkpoint("file:" + real); err != nil {
					return "", err
				}
				integrity, err := sc.Ops.Drivers.SQLite().Backup(ctx, src, real)
				if err != nil {
					// ONLY "the file is not there" is absence. A tenant that
					// never used a store has no file, and that is a fact about
					// the tenant; an EACCES, an EIO or a directory in its place
					// is a failure to read a database this bundle claims to
					// contain, and reporting it as `absent` produced a
					// succeeded step and a manifest saying `consistent: true`
					// over a bundle with a hole in it.
					if !errors.Is(err, fs.ErrNotExist) {
						return "", fmt.Errorf("backing up %s: %w", src, err)
					}
					sc.Logf("%s is not present; nothing to copy", src)
					if err := p.writePart(ctx, sc, "sqlite-"+db.File, sqlitePart{}); err != nil {
						return "", err
					}
					return "absent: " + db.File, nil
				}
				if integrity != "ok" {
					return "", fmt.Errorf("%w: `PRAGMA integrity_check` on the copy of %s answered %q, not ok: the "+
						"database is damaged and the bundle would be claiming otherwise", jobs.ErrRefused, src, integrity)
				}
				sum, size, err := sc.Ops.Drivers.Files().Sha256(ctx, real)
				if err != nil {
					return "", err
				}
				part := sqlitePart{Present: true, Entry: sqliteEntry{
					EnvKey: db.EnvKey, PathRel: p.relToRoot(src), File: filepath.Join("state", db.File),
					Bytes: size, Sha256: sum, IntegrityCheck: integrity,
				}}
				if err := p.writePart(ctx, sc, "sqlite-"+db.File, part); err != nil {
					return "", err
				}
				return fmt.Sprintf("%s (%d bytes, integrity %s)", real, size, integrity), nil
			},
			Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
				real, err := p.partialPath(sc, "state", db.File)
				if err != nil {
					return "", err
				}
				return "removed " + real, sc.Ops.Drivers.Files().Remove(ctx, real)
			},
		})
	}
}

// ---------------------------------------------------------------- postgres

func (p *planner) addPostgresDump(bundleDir string) {
	pg := p.t.Stores.Postgres
	if pg.Kind != registry.PostgresKindLocal {
		why := "this tenant keeps its relational state in SQLite under <data_dir>/state, which the state leg " +
			"already copies"
		if pg.Kind == registry.PostgresKindExternal {
			why = "the postgres server is external: the ctl does not own it, and the bundle records it as " +
				"`excluded` rather than dumping a database somebody else runs"
		}
		p.skip("postgres", "skip the postgres leg", why, pg.Kind)
		return
	}
	spec := p.postgresSpec()
	out := filepath.Join(bundleDir, "postgres", p.t.Name+".dump")
	p.addFor("postgres", step{
		Kind: "postgres", Title: "dump the tenant's postgres database into the bundle",
		Targets:    []string{spec.RunDir, spec.DB},
		WouldWrite: []model.WouldWrite{{Path: out, Mode: "0640", Preview: ""}},
		Warnings: []string{"`pg_dump -Fc` over the socket bind inside the tenant's own image: no password, no TCP " +
			"connection, and a custom-format archive a restore can be selective about"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			files := sc.Ops.Drivers.Files()
			real, err := p.partialPath(sc, "postgres", p.t.Name+".dump")
			if err != nil {
				return "", err
			}
			if err := files.MkdirAll(ctx, filepath.Dir(real), 0o2770); err != nil {
				return "", err
			}
			if err := sc.Checkpoint("file:" + real); err != nil {
				return "", err
			}
			if err := sc.Ops.Drivers.Postgres().Ready(ctx, spec); err != nil {
				return "", fmt.Errorf("the tenant's postgres does not answer on %s: %w", spec.RunDir, err)
			}
			if err := sc.Ops.Drivers.Postgres().Dump(ctx, spec, real); err != nil {
				return "", err
			}
			sum, size, err := files.Sha256(ctx, real)
			if err != nil {
				return "", err
			}
			part := postgresPart{Kind: pg.Kind, Ownership: pg.Ownership, Included: true,
				File: filepath.Join("postgres", p.t.Name+".dump"), Sha256: sum}
			if err := p.writePart(ctx, sc, "postgres", part); err != nil {
				return "", err
			}
			return fmt.Sprintf("%s (%d bytes)", real, size), nil
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			real, err := p.partialPath(sc, "postgres", p.t.Name+".dump")
			if err != nil {
				return "", err
			}
			return "removed " + real, sc.Ops.Drivers.Files().Remove(ctx, real)
		},
	})
}

// postgresSpec is the tenant's own image and socket directory. The SIF comes
// from the registry row when adopt or create recorded one, and otherwise from
// the shared images directory — never from an argument, because the image a
// dump runs through decides what format the archive is in.
func (p *planner) postgresSpec() jobs.PostgresSpec {
	sif := string(p.t.Stores.Postgres.SIF)
	if sif == "" {
		sif = filepath.Join(p.oc.Roots.ImagesDir, "postgres.sif")
	}
	return jobs.PostgresSpec{SIF: sif, RunDir: p.tpaths.PostgresRun, DB: p.t.Name, User: p.t.Name}
}

// ---------------------------------------------------------------- config

// addConfigCopies is the ALLOWLIST: the public files a bundle carries, named
// one by one.
//
// An allowlist rather than "copy <data_dir>/config": that directory holds
// secrets.env and its historical siblings, and a bundle that copied a
// directory would carry every credential this tenant ever had, in the clear,
// the first time somebody dropped a file in it.
func (p *planner) addConfigCopies(bundleDir string) {
	tp := p.tpaths
	targets := []string{tp.ProvisionEnv, tp.ManifestsDir}
	if p.t.EnvLayout == envLayoutManaged {
		targets = append([]string{tp.TenantEnv}, targets...)
	} else {
		p.warn("tenant.env is NOT copied as a public file: this tenant's env_layout is `" + p.t.EnvLayout +
			"`, so the file still carries secret-class keys and belongs only inside the encrypted payload")
	}
	p.addFor("files", step{
		Kind: "fs", Title: "copy the public config allowlist, the manifests and the rendered units into the bundle",
		Targets: targets,
		WouldWrite: []model.WouldWrite{
			{Path: filepath.Join(bundleDir, "config"), Mode: "0640", Preview: ""},
			{Path: filepath.Join(bundleDir, "manifests"), Mode: "0640", Preview: ""},
			{Path: filepath.Join(bundleDir, "units"), Mode: "0640", Preview: ""},
			{Path: filepath.Join(bundleDir, "registry-row.json"), Mode: "0640", Preview: ""},
		},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			files := sc.Ops.Drivers.Files()
			part := filesPart{Files: []bundleFile{}, Warnings: []string{}}
			units := filesPart{Files: []bundleFile{}, Warnings: []string{}}
			copyIn := func(src, rel string) error {
				b, err := files.ReadFile(ctx, src)
				if err != nil {
					if errors.Is(err, fs.ErrNotExist) {
						// The RELATIVE path: a manifest is read on another
						// host, where this one's layout means nothing.
						part.Warnings = append(part.Warnings,
							p.relToRoot(src)+" is not on the host, so it is not in the bundle")
						return nil
					}
					return fmt.Errorf("reading %s: %w", src, err)
				}
				dst, err := p.partialPath(sc, rel)
				if err != nil {
					return err
				}
				if err := files.MkdirAll(ctx, filepath.Dir(dst), 0o2770); err != nil {
					return err
				}
				if err := files.WriteAtomic(ctx, dst, b, 0o640); err != nil {
					return err
				}
				sum := sha256.Sum256(b)
				part.Files = append(part.Files, bundleFile{File: rel, PathRel: p.relToRoot(src),
					Bytes: int64(len(b)), Sha256: hex.EncodeToString(sum[:])})
				return nil
			}

			if p.t.EnvLayout == envLayoutManaged {
				if err := copyIn(tp.TenantEnv, filepath.Join("config", "tenant.env")); err != nil {
					return "", err
				}
			}
			if err := copyIn(tp.ProvisionEnv, filepath.Join("config", "provision.env")); err != nil {
				return "", err
			}
			// The per-collection manifests are named by the host, not by the
			// ctl, so they are listed rather than guessed.
			ents, err := files.ReadDir(ctx, tp.ManifestsDir)
			if err != nil && !errors.Is(err, fs.ErrNotExist) {
				return "", fmt.Errorf("listing %s: %w", tp.ManifestsDir, err)
			}
			for _, e := range ents {
				if e.IsDir || !strings.HasSuffix(e.Name, ".json") {
					continue
				}
				if err := copyIn(filepath.Join(tp.ManifestsDir, e.Name), filepath.Join("manifests", e.Name)); err != nil {
					return "", err
				}
			}

			// The registry row, as the registry holds it: secret REFS only,
			// never values (registry.json's own rule).
			row, err := json.MarshalIndent(p.rowOf(sc), "", "  ")
			if err != nil {
				return "", err
			}
			rowPath, err := p.partialPath(sc, "registry-row.json")
			if err != nil {
				return "", err
			}
			if err := files.WriteAtomic(ctx, rowPath, append(row, '\n'), 0o640); err != nil {
				return "", err
			}
			sum := sha256.Sum256(append(row, '\n'))
			part.Files = append(part.Files, bundleFile{File: "registry-row.json",
				PathRel: p.relToRoot(p.oc.Roots.Registry()), Bytes: int64(len(row) + 1),
				Sha256: hex.EncodeToString(sum[:])})

			// The units are RENDERED rather than read: what a restore needs is
			// the unit this tenant would run under today's renderer, and an
			// adopted tenant has no unit files on disk at all.
			rendered, rerr := render.Units(p.rowOf(sc), render.UnitConfig{
				RagRoot: sc.Ops.Roots.RagRoot, CtlStateDir: sc.Ops.Roots.CtlStateDir,
			})
			if rerr != nil {
				units.Warnings = append(units.Warnings,
					"the units could not be rendered for this tenant as it is recorded, so the bundle carries none: "+rerr.Error())
			}
			for _, name := range sortedNames(rendered) {
				body := rendered[name]
				dst, err := p.partialPath(sc, "units", name)
				if err != nil {
					return "", err
				}
				if err := files.MkdirAll(ctx, filepath.Dir(dst), 0o2770); err != nil {
					return "", err
				}
				if err := files.WriteAtomic(ctx, dst, body, 0o640); err != nil {
					return "", err
				}
				s := sha256.Sum256(body)
				units.Files = append(units.Files, bundleFile{File: filepath.Join("units", name),
					PathRel: p.relToRoot(filepath.Join(sc.Ops.Roots.UnitsDir(), name)),
					Bytes:   int64(len(body)), Sha256: hex.EncodeToString(s[:])})
			}
			if err := p.writePart(ctx, sc, "config", part); err != nil {
				return "", err
			}
			if err := p.writePart(ctx, sc, "units", units); err != nil {
				return "", err
			}
			return fmt.Sprintf("%d config file(s), %d unit(s)", len(part.Files), len(units.Files)), nil
		},
	})
}

// ---------------------------------------------------------------- secrets

// addSecrets seals the tenant's CURRENT and HISTORICAL secret files into the
// bundle, or says plainly that it did not.
//
// The rule is the plan's and it is absolute: no recipient, no secrets. A
// bundle that fell back to writing secrets.env in the clear because nobody had
// configured an age key would be the single worst artefact this tool can
// produce — every credential of a tenant, on a shared filesystem, inside a
// directory whose whole purpose is to be copied elsewhere.
func (p *planner) addSecrets(bundleDir string) {
	sealer := p.op.deps.Sealer
	if sealer == nil || len(sealer.Fingerprints()) == 0 {
		p.warn("no age recipient is configured (" + p.recipientsPath() + "): the secret files are EXCLUDED from the " +
			"bundle, which is recorded as `secrets.included: false`. A restore from it mints fresh credentials; the " +
			"tenant's current keys are not recoverable from this bundle")
		p.add(step{
			Kind: "fs", Title: "skip the encrypted secrets payload", Targets: []string{p.recipientsPath()},
			Warnings: []string{"the ctl never writes a credential to a bundle in the clear, so with no recipient " +
				"there is nothing to write"},
			Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
				part := secretsPart{Included: false, RecipientsFile: p.relToRoot(p.recipientsPath()),
					Fingerprints: []string{}, Warnings: []string{"no age recipient was configured when this bundle was written"}}
				if err := p.writePart(ctx, sc, "secrets", part); err != nil {
					return "", err
				}
				sc.Logf("no recipients: the bundle carries no secrets")
				return "excluded: no age recipient is configured", nil
			},
		})
		return
	}
	tp := p.tpaths
	p.addFor("files", step{
		Kind: "fs", Title: "seal the secret files into secrets.age", Targets: []string{tp.ConfigDir},
		// secretWrite, not preview: the CONTENT of this file is every
		// credential the tenant has, and a preview is exactly where one would
		// leak into an audit row.
		WouldWrite: []model.WouldWrite{secretWrite(filepath.Join(bundleDir, "secrets.age"), "0640")},
		Warnings: []string{"age-encrypted to " + strings.Join(sealer.Fingerprints(), ", ") + "; the daemon holds no " +
			"identity, so it can write this file and never read it back"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			files := sc.Ops.Drivers.Files()
			// The plaintext tar is built in MEMORY and sealed before anything
			// is written: a temporary file would be the one moment these bytes
			// existed unencrypted on a shared filesystem.
			var buf bytes.Buffer
			tw := tar.NewWriter(&buf)
			add := func(name, src string) error {
				b, err := files.ReadFile(ctx, src)
				if err != nil {
					if errors.Is(err, fs.ErrNotExist) {
						return nil
					}
					return fmt.Errorf("reading %s: %w", src, err)
				}
				if err := tw.WriteHeader(&tar.Header{Name: name, Mode: 0o600, Size: int64(len(b))}); err != nil {
					return err
				}
				_, err = tw.Write(b)
				return err
			}
			names := []string{}
			ents, err := files.ReadDir(ctx, tp.ConfigDir)
			if err != nil && !errors.Is(err, fs.ErrNotExist) {
				return "", fmt.Errorf("listing %s: %w", tp.ConfigDir, err)
			}
			for _, e := range ents {
				if e.IsDir {
					continue
				}
				// secrets.env, every historical secrets.env.bak-*, and — for a
				// LEGACY tenant only — tenant.env, which still carries the
				// secret-class keys a managed tenant has moved out of it.
				secret := e.Name == "secrets.env" || strings.HasPrefix(e.Name, "secrets.env.bak-") ||
					(e.Name == "tenant.env" && p.t.EnvLayout != envLayoutManaged) ||
					strings.HasPrefix(e.Name, "tenant.env.bak-")
				if !secret {
					continue
				}
				if err := add(e.Name, filepath.Join(tp.ConfigDir, e.Name)); err != nil {
					return "", err
				}
				names = append(names, e.Name)
			}
			if err := tw.Close(); err != nil {
				return "", err
			}
			if len(names) == 0 {
				part := secretsPart{Included: false, RecipientsFile: p.relToRoot(p.recipientsPath()),
					Fingerprints: []string{}, Warnings: []string{"this tenant has no secret files on disk"}}
				if err := p.writePart(ctx, sc, "secrets", part); err != nil {
					return "", err
				}
				return "nothing to seal: this tenant has no secret files", nil
			}
			sealed, err := sealer.Seal(buf.Bytes())
			if err != nil {
				if errors.Is(err, ErrNoRecipients) || noRecipients(err) {
					part := secretsPart{Included: false, RecipientsFile: p.relToRoot(p.recipientsPath()),
						Fingerprints: []string{},
						Warnings:     []string{"no age recipient was configured when this bundle was written"}}
					if err := p.writePart(ctx, sc, "secrets", part); err != nil {
						return "", err
					}
					return "excluded: no age recipient is configured", nil
				}
				return "", fmt.Errorf("sealing the secret files: %w", err)
			}
			dst, err := p.partialPath(sc, "secrets.age")
			if err != nil {
				return "", err
			}
			if err := sc.Checkpoint("file:" + dst); err != nil {
				return "", err
			}
			if err := files.WriteAtomic(ctx, dst, sealed, 0o640); err != nil {
				return "", err
			}
			part := secretsPart{Included: true, File: "secrets.age",
				RecipientsFile: p.relToRoot(p.recipientsPath()), Fingerprints: p.keyFingerprints(),
				Warnings: []string{}}
			if err := p.writePart(ctx, sc, "secrets", part); err != nil {
				return "", err
			}
			// The file NAMES, never the contents and never a count of keys.
			sc.Logf("sealed %d file(s) to %d recipient(s)", len(names), len(sealer.Fingerprints()))
			return fmt.Sprintf("secrets.age (%d file(s))", len(names)), nil
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			dst, err := p.partialPath(sc, "secrets.age")
			if err != nil {
				return "", err
			}
			return "removed " + dst, sc.Ops.Drivers.Files().Remove(ctx, dst)
		},
	})
}

// recipientsPath is where the age recipients live: one CLI-managed file for
// the whole fleet, under the ctl's config root.
func (p *planner) recipientsPath() string {
	return filepath.Join(p.oc.Roots.CtlConfigDir, "backup-recipients.txt")
}

// keyFingerprints are the ledger fingerprints of the keys whose VALUES are
// inside the sealed payload — the reconciliation a restore does against the
// revocation ledger. Fingerprints, never values.
func (p *planner) keyFingerprints() []string {
	out := []string{}
	for _, k := range p.t.Keys {
		if k.Fingerprint != "" {
			out = append(out, k.Fingerprint)
		}
	}
	sort.Strings(out)
	return out
}

// ---------------------------------------------------------------- MIGRATE.md

func (p *planner) addMigrateMD(bundleDir string) {
	p.addFor("files", step{
		Kind: "fs", Title: "write MIGRATE.md, the bundle's own runbook",
		Targets:    []string{bundleDir},
		WouldWrite: []model.WouldWrite{{Path: filepath.Join(bundleDir, "MIGRATE.md"), Mode: "0640", Preview: ""}},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			parts, err := p.readParts(ctx, sc)
			if err != nil {
				return "", err
			}
			info := render.MigrateInfo{
				BundleID: p.bundleID(sc), Kind: bundleKind,
				CreatedAt: p.stampRFC3339(sc), CtlVersion: version.Version,
				Fenced: p.result["fenced"] == true, ArtifactID: string(p.t.ArtifactID),
				Collections: parts.qdrant.Inventory, Indices: parts.es.Inventory,
				SQLite: parts.sqliteFiles(), Postgres: parts.postgres.File,
				SecretsIncluded: parts.secrets.Included, External: parts.externalLines(p.t),
			}
			body, err := render.MigrateMD(p.rowOf(sc), info)
			if err != nil {
				return "", err
			}
			dst, err := p.partialPath(sc, "MIGRATE.md")
			if err != nil {
				return "", err
			}
			if err := sc.Ops.Drivers.Files().WriteAtomic(ctx, dst, body, 0o640); err != nil {
				return "", err
			}
			return dst, nil
		},
	})
}

// ---------------------------------------------------------------- manifest

func (p *planner) addBundleManifest(bundleDir string, fence bool) {
	// The PLAN's preview is the manifest's SHAPE: every key the contract
	// requires, with the values that are already known and a placeholder where
	// the run will put a fact it cannot have yet. An operator reading a dry run
	// sees exactly which document is going to be written.
	preview, _ := json.MarshalIndent(p.manifestSkeleton(fence), "", "  ")
	path := filepath.Join(bundleDir, "manifest.json")
	p.addFor("files", step{
		Kind: "fs", Title: "write SHA256SUMS and the bundle manifest", Targets: []string{bundleDir},
		WouldWrite: []model.WouldWrite{
			{Path: filepath.Join(bundleDir, "SHA256SUMS"), Mode: "0640", Preview: ""},
			p.preview(path, "0640", preview),
		},
		Warnings: []string{"`" + bundlePlaceholder + "` stands for the bundle id `<ts>-backup`, which is stamped when " +
			"the job runs; SHA256SUMS covers every file in the bundle except itself and manifest.json, and the " +
			"manifest carries its digest"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			files := sc.Ops.Drivers.Files()
			dir, err := p.partialDirOf(sc)
			if err != nil {
				return "", err
			}
			parts, err := p.readParts(ctx, sc)
			if err != nil {
				return "", err
			}
			// SHA256SUMS first: the manifest names its digest, so it cannot be
			// inside it. Everything else is, which is what makes the manifest
			// the single root of trust a verify starts from.
			names, err := walkFiles(ctx, files, dir)
			if err != nil {
				return "", fmt.Errorf("listing the bundle: %w", err)
			}
			var sums bytes.Buffer
			for _, rel := range names {
				if rel == "SHA256SUMS" || rel == "manifest.json" {
					continue
				}
				sum, _, err := files.Sha256(ctx, filepath.Join(dir, rel))
				if err != nil {
					return "", fmt.Errorf("checksumming %s: %w", rel, err)
				}
				fmt.Fprintf(&sums, "%s  %s\n", sum, rel)
			}
			sumsPath := filepath.Join(dir, "SHA256SUMS")
			if err := files.WriteAtomic(ctx, sumsPath, sums.Bytes(), 0o640); err != nil {
				return "", err
			}
			digest := sha256.Sum256(sums.Bytes())

			man := p.manifest(sc, fence, parts, hex.EncodeToString(digest[:]))
			body, err := json.MarshalIndent(man, "", "  ")
			if err != nil {
				return "", err
			}
			real := filepath.Join(dir, "manifest.json")
			if err := files.WriteAtomic(ctx, real, append(body, '\n'), 0o640); err != nil {
				return "", err
			}
			sc.Logf("manifest over %d file(s), consistent=%v", len(names), man["consistent"])
			return real, nil
		},
	})
}

// manifestSkeleton is the manifest as the PLAN can know it: every required key
// present, the host facts blank. It is what the dry run previews.
func (p *planner) manifestSkeleton(fence bool) map[string]any {
	return p.manifestFrom(nil, bundlePlaceholder, "", fence, bundleParts{}, strings.Repeat("0", 64))
}

// manifest is the real document.
func (p *planner) manifest(sc *jobs.StepContext, fence bool, parts bundleParts, sumsDigest string) map[string]any {
	return p.manifestFrom(sc, p.bundleID(sc), p.stampRFC3339(sc), fence, parts, sumsDigest)
}

func (p *planner) manifestFrom(sc *jobs.StepContext, id, createdAt string, fence bool,
	parts bundleParts, sumsDigest string) map[string]any {
	t := p.rowOf(sc)
	warnings := append([]string{}, parts.warnings()...)
	consistent := fence && parts.consistent()
	if fence && !consistent && sc != nil {
		warnings = append(warnings, "a count moved under the fence, or a store the inventory named is not in the "+
			"bundle: this bundle is NOT consistent")
	}
	principal := "ragstack-ctl"
	if sc != nil && sc.Job != nil && sc.Job.Principal != "" {
		principal = sc.Job.Principal
	}
	return map[string]any{
		"schema_version": version.SchemaVersion,
		"kind":           bundleKind,
		"bundle_id":      id,
		"created_at":     createdAt,
		"created_by":     principal,
		"ctl_version":    version.Version,
		"fenced":         fence,
		"best_effort":    !fence,
		"tenant": map[string]any{
			"name": t.Name, "manifest_name": t.ManifestName, "ports": t.Ports,
		},
		"artifact": map[string]any{
			"id": nullString(string(t.ArtifactID)), "sha": nullString(string(t.Code.SHA)), "tag": orDefault(t.Code.Tag, "untracked"),
		},
		"python_env": map[string]any{
			"path_rel": p.relToRoot(t.PythonEnv), "lockhash": parts.lockhash,
		},
		"images": map[string]any{
			"qdrant":        imageRef(p.oc.Fleet.Images.Qdrant),
			"elasticsearch": imageRef(p.oc.Fleet.Images.Elasticsearch),
		},
		"paths_relative_to": "RAG_ROOT",
		"inventory": map[string]any{
			"collections": nonNilStrings(parts.qdrant.Inventory),
			"indices":     nonNilStrings(parts.es.Inventory),
			// Aliases are an elasticsearch concept the ctl does not yet read;
			// an empty list is the honest statement that none was captured.
			"aliases": []string{},
			"sqlite":  parts.sqliteFiles(),
		},
		"stores": map[string]any{
			"qdrant": map[string]any{
				"ownership":   orDefault(parts.qdrant.Ownership, t.Stores.Qdrant.Ownership),
				"url":         orDefault(parts.qdrant.URL, t.Stores.Qdrant.URL),
				"collections": parts.qdrantCollections(),
			},
			"elasticsearch": map[string]any{
				"ownership": orDefault(parts.es.Ownership, t.Stores.Elasticsearch.Ownership),
				"repo_type": "fs",
				"repo":      nullString(parts.es.Repo),
				"snapshot":  nullString(parts.es.Snapshot),
				"indices":   parts.esIndices(),
				"complete":  parts.es.Complete,
				"files":     nonNilStrings(parts.es.Files),
			},
			"neo4j": map[string]any{"ownership": "external", "included": false},
			"postgres": map[string]any{
				"kind":      orDefault(parts.postgres.Kind, t.Stores.Postgres.Kind),
				"ownership": orDefault(parts.postgres.Ownership, t.Stores.Postgres.Ownership),
				"included":  parts.postgres.Included,
				"file":      nullString(parts.postgres.File),
				"sha256":    nullString(parts.postgres.Sha256),
			},
		},
		"sqlite":   parts.sqliteEntries(),
		"files":    parts.config.entries(),
		"external": externalRefs(t),
		"secrets": map[string]any{
			"encrypted":        true,
			"included":         parts.secrets.Included,
			"file":             nullString(parts.secrets.File),
			"recipients_file":  nullString(parts.secrets.RecipientsFile),
			"key_fingerprints": nonNilStrings(parts.secrets.Fingerprints),
		},
		"units":        parts.units.entries(),
		"registry_row": t,
		"migrate_md":   "MIGRATE.md",
		"consistent":   consistent,
		// A bundle is verified by a restore that rebuilt from it, never by the
		// job that wrote it: a writer that marked its own output verified would
		// be attesting to work nobody did.
		"verified":   false,
		"warnings":   warnings,
		"sha256sums": sumsDigest,
	}
}

// externalRefs is what the bundle does NOT contain, so a recovery knows what
// is still missing when it has finished restoring what it does.
func externalRefs(t *registry.Tenant) []externalRef {
	out := []externalRef{}
	if t.Stores.Qdrant.Ownership != registry.OwnershipExclusive || !t.Stores.Qdrant.Capabilities.Snapshot {
		out = append(out, externalRef{Kind: "store", Ref: t.Stores.Qdrant.URL, Reason: reasonFor(t.Stores.Qdrant.Ownership)})
	}
	if t.Stores.Elasticsearch.Ownership != registry.OwnershipExclusive || !t.Stores.Elasticsearch.Capabilities.Snapshot {
		out = append(out, externalRef{Kind: "store", Ref: t.Stores.Elasticsearch.URL,
			Reason: reasonFor(t.Stores.Elasticsearch.Ownership)})
	}
	if url := string(t.Stores.Neo4j.URL); url != "" {
		out = append(out, externalRef{Kind: "store", Ref: url, Reason: "external"})
	}
	if t.Stores.Postgres.Kind == registry.PostgresKindExternal {
		out = append(out, externalRef{Kind: "store", Ref: string(t.Stores.Postgres.URL), Reason: "external"})
	}
	for _, ref := range t.ExternalRefs {
		out = append(out, externalRef{Kind: "file", Ref: ref.Path, Reason: "external"})
	}
	return out
}

func reasonFor(ownership string) string {
	switch ownership {
	case registry.OwnershipShared:
		return "shared"
	case registry.OwnershipExternal:
		return "external"
	default:
		return "excluded"
	}
}

func imageRef(i registry.Image) map[string]any {
	return map[string]any{"version": i.Version, "digest": i.Digest}
}

// ---------------------------------------------------------------- finalize

// addBundleFinalize is the rename that makes the bundle real. It is the LAST
// write, so the presence of `<id>` (rather than `<id>.partial`) is exactly the
// statement "this bundle has a manifest and a checksum file in it".
func (p *planner) addBundleFinalize(bundleDir string) {
	p.addFor("files", step{
		Kind: "fs", Title: "rename the bundle into place (drop the " + partialSuffix + " suffix)",
		Targets: []string{bundleDir},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			from, err := p.partialDirOf(sc)
			if err != nil {
				return "", err
			}
			to, err := p.bundleDirOf(sc)
			if err != nil {
				return "", err
			}
			if err := sc.Checkpoint("dir:" + to); err != nil {
				return "", err
			}
			if err := sc.Ops.Drivers.Files().Rename(ctx, from, to); err != nil {
				return "", err
			}
			return to, nil
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			from, err := p.partialDirOf(sc)
			if err != nil {
				return "", err
			}
			to, err := p.bundleDirOf(sc)
			if err != nil {
				return "", err
			}
			return "unfinished again: " + from, sc.Ops.Drivers.Files().Rename(ctx, to, from)
		},
	})
}

func (p *planner) addBundleTar(bundleDir string) {
	p.addFor("archive", step{
		Kind: "fs", Title: "tar the finished bundle beside itself", Targets: []string{bundleDir + ".tar"},
		WouldWrite: []model.WouldWrite{{Path: bundleDir + ".tar", Mode: "0640", Preview: ""}},
		Warnings:   []string{"the tar is a COPY: the directory stays, and both are covered by the same manifest"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			dir, err := p.bundleDirOf(sc)
			if err != nil {
				return "", err
			}
			out := dir + ".tar"
			if err := sc.Checkpoint("file:" + out); err != nil {
				return "", err
			}
			if err := sc.Ops.Drivers.Archive().Create(ctx, dir, out); err != nil {
				return "", err
			}
			return out, nil
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			dir, err := p.bundleDirOf(sc)
			if err != nil {
				return "", err
			}
			return "removed " + dir + ".tar", sc.Ops.Drivers.Files().Remove(ctx, dir+".tar")
		},
	})
}

// addBackupRecord is the registry's half: the row learns which bundle is its
// recovery point, and that nothing has verified it yet.
func (p *planner) addBackupRecord(fence bool) {
	name := p.t.Name
	p.add(step{
		Kind: "registry", Title: "record the bundle as this tenant's last backup", Targets: []string{name},
		WouldWrite: []model.WouldWrite{{Path: p.oc.Roots.Registry(), Mode: "0660", Preview: ""}},
		Warnings: []string{"`verified` stays false until a `restore --as` has rebuilt a tenant from this bundle: " +
			"only a restore can prove a backup"},
		Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			if p.op.deps.SaveFleet == nil {
				return "", fmt.Errorf("%w: the registry writer is not wired", jobs.ErrRefused)
			}
			id := p.bundleID(sc)
			dir, err := p.bundleDirOf(sc)
			if err != nil {
				return "", err
			}
			cur := sc.Ops.Fleet
			row := cur.Tenants[name]
			if row == nil {
				return "", fmt.Errorf("%w: %s is no longer in the registry", jobs.ErrRefused, name)
			}
			at := p.stampRFC3339(sc)
			// The registry records the bundle's absolute DIRECTORY
			// (registry.json types last_backup.bundle as an AbsPath), while
			// every operator-facing surface — `backup list`, `restore --from`,
			// the manifest's own bundle_id — speaks the id. The two differ by
			// a basename, and this is the one place that has to know it.
			row.LastBackup = &registry.BackupRecord{Bundle: dir, At: at, Kind: bundleKind, Fenced: fence, Verified: false}
			if row.LastOps == nil {
				row.LastOps = map[string]registry.OpRecord{}
			}
			row.LastOps["backup"] = registry.OpRecord{JobID: jobIDOf(sc), At: at, Outcome: "succeeded"}
			if err := p.op.deps.SaveFleet(cur); err != nil {
				return "", err
			}
			// The job's RESULT learns the real id here. The plan could only
			// carry the placeholder (a plan that read the clock would be stale
			// the moment it was made), and `bundle: new-bundle` in a finished
			// job's result is a value no caller can do anything with.
			p.result["bundle"] = id
			p.result["bundle_dir"] = dir
			return fmt.Sprintf("last_backup = %s (fenced=%v, verified=false)", id, fence), nil
		},
	})
	p.result["bundle"] = bundlePlaceholder
}

// ---------------------------------------------------------------- the fence

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

// ---------------------------------------------------------------- parts I/O

// writePart records one leg's facts inside the bundle.
func (p *planner) writePart(ctx context.Context, sc *jobs.StepContext, name string, v any) error {
	body, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	dst, err := p.partialPath(sc, partsDir, name+".json")
	if err != nil {
		return err
	}
	return sc.Ops.Drivers.Files().WriteAtomic(ctx, dst, append(body, '\n'), 0o640)
}

// bundleParts is every leg's record, read back for the manifest.
type bundleParts struct {
	qdrant   qdrantPart
	es       esPart
	sqlite   map[string]sqlitePart
	postgres postgresPart
	config   filesPart
	units    filesPart
	secrets  secretsPart
	lockhash string
}

// readParts reads what the earlier steps wrote. A leg that did not run (a
// shared store, a tenant with no postgres) leaves no file, and its zero value
// is the honest "not in this bundle".
func (p *planner) readParts(ctx context.Context, sc *jobs.StepContext) (bundleParts, error) {
	out := bundleParts{sqlite: map[string]sqlitePart{}}
	read := func(name string, v any) error {
		src, err := p.partialPath(sc, partsDir, name+".json")
		if err != nil {
			return err
		}
		b, err := sc.Ops.Drivers.Files().ReadFile(ctx, src)
		if err != nil {
			if errors.Is(err, fs.ErrNotExist) {
				return nil
			}
			return fmt.Errorf("reading %s: %w", src, err)
		}
		if err := json.Unmarshal(b, v); err != nil {
			return fmt.Errorf("%s is not a record this build wrote: %w", src, err)
		}
		return nil
	}
	if err := read("qdrant", &out.qdrant); err != nil {
		return out, err
	}
	if err := read("elasticsearch", &out.es); err != nil {
		return out, err
	}
	for _, db := range sqliteDBs {
		var part sqlitePart
		if err := read("sqlite-"+db.File, &part); err != nil {
			return out, err
		}
		out.sqlite[db.File] = part
	}
	if err := read("postgres", &out.postgres); err != nil {
		return out, err
	}
	if err := read("config", &out.config); err != nil {
		return out, err
	}
	if err := read("units", &out.units); err != nil {
		return out, err
	}
	if err := read("secrets", &out.secrets); err != nil {
		return out, err
	}
	out.lockhash = p.pythonLockhash(ctx, sc)
	return out, nil
}

// pythonLockhash is the digest of the env's requirements.lock — what a restore
// compares to decide whether the interpreter it is about to run the tenant on
// is the one the bundle was taken from. An env with no lock file answers the
// digest of nothing, which is a value that can never match a real lock.
func (p *planner) pythonLockhash(ctx context.Context, sc *jobs.StepContext) string {
	empty := sha256.Sum256(nil)
	if sc == nil || p.t.PythonEnv == "" {
		return hex.EncodeToString(empty[:])
	}
	sum, _, err := sc.Ops.Drivers.Files().Sha256(ctx, filepath.Join(p.t.PythonEnv, "requirements.lock"))
	if err != nil {
		return hex.EncodeToString(empty[:])
	}
	return sum
}

func (b bundleParts) warnings() []string {
	out := []string{}
	out = append(out, b.qdrant.Warnings...)
	out = append(out, b.es.Warnings...)
	out = append(out, b.config.Warnings...)
	out = append(out, b.units.Warnings...)
	out = append(out, b.secrets.Warnings...)
	return out
}

// consistent is the manifest's own definition: every inventory entry is
// accounted for and every before/after pair agrees.
func (b bundleParts) consistent() bool {
	if len(b.qdrant.Collections) != len(b.qdrant.Inventory) || len(b.es.Indices) != len(b.es.Inventory) {
		return false
	}
	for _, c := range b.qdrant.Collections {
		if !c.Included || c.PointsBefore != c.PointsAfter {
			return false
		}
	}
	for _, i := range b.es.Indices {
		if !i.Included || i.DocsBefore != i.DocsAfter {
			return false
		}
	}
	if b.es.Repo != "" && !b.es.Complete {
		return false
	}
	return true
}

func (b bundleParts) qdrantCollections() []qdrantCollection {
	if b.qdrant.Collections == nil {
		return []qdrantCollection{}
	}
	return b.qdrant.Collections
}

func (b bundleParts) esIndices() []esIndex {
	if b.es.Indices == nil {
		return []esIndex{}
	}
	return b.es.Indices
}

func (b bundleParts) sqliteFiles() []string {
	out := []string{}
	for _, db := range sqliteDBs {
		if b.sqlite[db.File].Present {
			out = append(out, db.File)
		}
	}
	return out
}

func (b bundleParts) sqliteEntries() []sqliteEntry {
	out := []sqliteEntry{}
	for _, db := range sqliteDBs {
		if part := b.sqlite[db.File]; part.Present {
			out = append(out, part.Entry)
		}
	}
	return out
}

func (f filesPart) entries() []bundleFile {
	if f.Files == nil {
		return []bundleFile{}
	}
	return f.Files
}

// externalLines renders the external list for MIGRATE.md.
func (b bundleParts) externalLines(t *registry.Tenant) []string {
	out := []string{}
	for _, e := range externalRefs(t) {
		out = append(out, fmt.Sprintf("`%s` (%s, %s)", e.Ref, e.Kind, e.Reason))
	}
	return out
}

// ---------------------------------------------------------------- helpers

// walkFiles lists every FILE under dir, depth first, as paths relative to dir.
// An absent directory is an empty list, not an error: a leg that did not run
// left nothing behind, and the checksum step must not fail because of it.
func walkFiles(ctx context.Context, files jobs.Files, dir string) ([]string, error) {
	var out []string
	var walk func(rel string) error
	walk = func(rel string) error {
		ents, err := files.ReadDir(ctx, filepath.Join(dir, rel))
		if err != nil {
			if errors.Is(err, fs.ErrNotExist) {
				return nil
			}
			return err
		}
		for _, e := range ents {
			child := filepath.Join(rel, e.Name)
			if e.IsDir {
				if err := walk(child); err != nil {
					return err
				}
				continue
			}
			out = append(out, child)
		}
		return nil
	}
	if err := walk(""); err != nil {
		return nil, err
	}
	sort.Strings(out)
	return out, nil
}

// relToRoot is a path as the bundle spells it: relative to RAG_ROOT, so a
// bundle copied to another host does not carry this one's layout. A path
// outside the root keeps its shape minus the leading slash, which the
// contract's RelPath still accepts and a reader can still recognise.
func (p *planner) relToRoot(path string) string {
	if path == "" {
		return ""
	}
	clean := filepath.Clean(path)
	root := filepath.Clean(p.oc.Roots.RagRoot)
	if rel, err := filepath.Rel(root, clean); err == nil && !strings.HasPrefix(rel, "..") {
		return rel
	}
	return strings.TrimPrefix(clean, "/")
}

// rowOf is the tenant row the step should write down: the one the ENGINE
// loaded under the locks when there is one, else the plan-time snapshot.
func (p *planner) rowOf(sc *jobs.StepContext) *registry.Tenant {
	if sc != nil && sc.Ops.Fleet != nil {
		if row := sc.Ops.Fleet.Tenants[p.t.Name]; row != nil {
			return row
		}
	}
	return p.t
}

func jobIDOf(sc *jobs.StepContext) string {
	if sc != nil && sc.Job != nil {
		return sc.Job.ID
	}
	return ""
}

func nullString(s string) any {
	if s == "" {
		return nil
	}
	return s
}

func orDefault(s, def string) string {
	if s == "" {
		return def
	}
	return s
}

func containsStr(hay []string, needle string) bool {
	for _, h := range hay {
		if h == needle {
			return true
		}
	}
	return false
}

// humanBytes is for an operator reading a refusal, not for a machine.
func humanBytes(n int64) string {
	const unit = 1024
	if n < unit {
		return strconv.FormatInt(n, 10) + " B"
	}
	div, exp := int64(unit), 0
	for m := n / unit; m >= unit; m /= unit {
		div *= unit
		exp++
	}
	return fmt.Sprintf("%.1f %ciB", float64(n)/float64(div), "KMGTPE"[exp])
}

// ---------------------------------------------------------------- pending

// pendingRun is a step whose driver method lands in PR-D. The plan is
// rendered in full — a dry run has to be able to SHOW the operation — and the
// refusal happens when it runs, naming the method, exactly as the real driver
// set does.
func (p *planner) pendingRun(driver, method string) jobs.StepFunc {
	return func(context.Context, *jobs.StepContext) (string, error) {
		return "", fmt.Errorf("%w: %s.%s lands in PR-D", jobs.ErrRefused, driver, method)
	}
}
