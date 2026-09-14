package ops

// `restore --from <bundle> --as <new>` — the deep verify, and the only thing
// in the control plane that can set `verified` on a backup.
//
// The verb is tenant-scoped on the SOURCE (`/v1/tenants/{source}/ops/restore`)
// and builds a SECOND, fresh tenant beside it. v1 never restores in place: an
// in-place restore would have to reconcile live credentials, a live gateway row
// and a live store against a bundle taken at another moment, while a fresh
// tenant has none of those problems and can be compared with the original side
// by side.
//
// Three rules shape the file.
//
// THE PLAN IS PURE, AND THE MANIFEST IS NOT A PLAN INPUT. Everything a plan
// decides is decided from the registry snapshot and the args. The bundle's
// manifest is read in the FIRST RUN STEP, never at plan time — a plan that read
// a file off the host would be a plan the engine's re-plan under the locks
// could not reproduce. So the plan is written against facts the manifest will
// provide later: where a step needs a list (which collections, which indices,
// which state files) it reads that list from the manifest inside its own Run,
// the way the backup reads the collection list from the running store.
//
// WHERE THE PLAN HAD TO COMMIT, THE FIRST STEP CHECKS. Two things cannot wait:
// `planCreateSteps` needs the ARTIFACT and the STORE KIND at plan time (they
// decide the worktree, the UI build, the units and the directory list), and
// both of those live in the manifest. The honest resolution is not to peek —
// it is to take them from the SOURCE TENANT'S REGISTRY ROW, say so in the plan
// ("from the source tenant's current artifact <id> and store kind <k>; the
// bundle must agree"), and have the verify step REFUSE when the bundle
// disagrees. A restore that silently built a tenant on different code from the
// one the data came out of is the failure this refusal exists for.
//
// A FAILED RESTORE LEAVES NO TENANT AND NO DATA. Every step that made something
// has a rollback and the create steps bring their own, so a failure anywhere —
// including in the final count check — takes the fresh tenant apart again: units
// stopped and their files removed, credentials, env files and worktree removed,
// every byte copied out of the bundle removed, registry row deleted. What it
// cannot remove is the empty directory tree `create` laid down, because the
// Files seam has no recursive delete and a rollback path is the last place to
// give it one; the plan says so in as many words rather than claiming a
// cleanliness the code does not have. The SOURCE is never touched at all except
// by the last two steps, which only set flags saying the bundle has now been
// proved.

import (
	"context"
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
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/settings"
)

// qdrantContainerSnapshots is the directory the qdrant container sees its own
// snapshot tree at. `Qdrant.Recover` takes a location INSIDE the container, and
// the unit binds <data_dir>/qdrant/snapshots there — so the host path the copy
// step writes to and the URL the recover step sends are two spellings of the
// same file, and this constant is the one place that knows it.
const qdrantContainerSnapshots = "file:///qdrant/snapshots/"

// ---------------------------------------------------------------- the manifest, as a restore reads it
//
// A subset of bundle_manifest.json: the members this verb acts on. It is a
// SUBSET on purpose — a restore that unmarshalled into a struct covering the
// whole contract would fail to read a bundle written by a later version that
// added a member, and a bundle must stay readable by the tool that has to
// recover from it.

type restoreManifest struct {
	BundleID   string `json:"bundle_id"`
	Kind       string `json:"kind"`
	CreatedAt  string `json:"created_at"`
	Fenced     bool   `json:"fenced"`
	Consistent bool   `json:"consistent"`
	Verified   bool   `json:"verified"`
	Artifact   struct {
		ID  *string `json:"id"`
		SHA *string `json:"sha"`
		Tag string  `json:"tag"`
	} `json:"artifact"`
	Images struct {
		Qdrant        manifestImage `json:"qdrant"`
		Elasticsearch manifestImage `json:"elasticsearch"`
	} `json:"images"`
	Inventory struct {
		Collections []string `json:"collections"`
		Indices     []string `json:"indices"`
		SQLite      []string `json:"sqlite"`
	} `json:"inventory"`
	Stores struct {
		Qdrant struct {
			Collections []qdrantCollection `json:"collections"`
		} `json:"qdrant"`
		Elasticsearch struct {
			Repo     *string   `json:"repo"`
			Snapshot *string   `json:"snapshot"`
			Indices  []esIndex `json:"indices"`
			Complete bool      `json:"complete"`
			Files    []string  `json:"files"`
		} `json:"elasticsearch"`
		Postgres struct {
			Kind     string  `json:"kind"`
			Included bool    `json:"included"`
			File     *string `json:"file"`
		} `json:"postgres"`
	} `json:"stores"`
	SQLite     []sqliteEntry `json:"sqlite"`
	SHA256Sums string        `json:"sha256sums"`
}

type manifestImage struct {
	Version string `json:"version"`
	Digest  string `json:"digest"`
}

// esRepo is the repository name the bundle recorded, or the empty string when
// the elasticsearch leg did not run.
func (m restoreManifest) esRepo() string {
	if m.Stores.Elasticsearch.Repo == nil {
		return ""
	}
	return *m.Stores.Elasticsearch.Repo
}

func (m restoreManifest) esSnapshot() string {
	if m.Stores.Elasticsearch.Snapshot == nil {
		return ""
	}
	return *m.Stores.Elasticsearch.Snapshot
}

// includedIndices are the indices the snapshot holds, in manifest order.
func (m restoreManifest) includedIndices() []string {
	out := []string{}
	for _, i := range m.Stores.Elasticsearch.Indices {
		if i.Included {
			out = append(out, i.Name)
		}
	}
	return out
}

// ---------------------------------------------------------------- plan

func planRestore(_ context.Context, p *planner, args map[string]any) error {
	from, as := argStringOf(args, "from"), argStringOf(args, "as")
	f := p.oc.Fleet
	if f == nil {
		return fmt.Errorf("%w: restore needs a registry snapshot", jobs.ErrValidation)
	}
	src := p.t
	srcName := src.Name
	srcPaths := p.tpaths

	if err := paths.ValidateName(as); err != nil {
		return fmt.Errorf("%w: %s", jobs.ErrValidation, err.Error())
	}
	if _, exists := f.Tenants[as]; exists {
		return p.refuse("`as` must name a FRESH tenant; %s already exists. v1 restores side by side (in-place "+
			"restore is v1.x) — pick a new name", as)
	}
	if strings.HasSuffix(from, partialSuffix) {
		// Unreachable through the args schema (`from` is `<ts>-<kind>`), stated
		// anyway: a `.partial` is a bundle whose backup never finished, and the
		// one thing that must never happen is a restore that reads one as if it
		// were whole.
		return p.refuse("%s is a `%s` directory: the backup that was writing it never finished", from, partialSuffix)
	}

	// ---- what the PLAN must commit to, and the bundle must agree with -------
	artifactID := string(src.ArtifactID)
	if artifactID == "" {
		return p.refuse("%s has no artifact_id, so there is no reviewed checkout to build %s from. `restore --as` "+
			"lays the fresh tenant down exactly as `tenant create` does, and that needs a prepared artifact: "+
			"`ragstack-ctl fleet artifact prepare --tag <tag>`, then record it on %s", srcName, as, srcName)
	}
	artifact, ok := f.Artifacts[artifactID]
	if !ok {
		return p.refuse("%s's artifact %q is not prepared on this host: the fresh tenant %s would have no worktree to "+
			"check out. Prepare it (`ragstack-ctl fleet artifact prepare --tag %s`) and restore again",
			srcName, artifactID, as, orDefault(src.Code.Tag, artifactID))
	}
	storeKind, err := restoreStoreKind(p, src)
	if err != nil {
		return err
	}

	spec := createSpec{
		Name: as, ArtifactID: artifactID, Artifact: artifact,
		StoreKind: storeKind, ESHeap: restoreESHeap(src),
		Provider: orDefault(src.Identity.Provider, "none"),
		Keys:     restoreKeys(p, src),
		Settings: publicSettings(src),
		UIMode:   src.UI.Mode,
		// Never started and never routed by the create half: the tenant is laid
		// down EMPTY, the bundle is poured into it, and only then is it started
		// and published — by the steps below.
		Start: false, Gateway: false,
		Verb:   "restore",
		Mirror: p.op.deps.Mirror, Owner: p.op.deps.owner(),
	}
	// A sandbox is restored into a SANDBOX. `registry.Allocate` ignores
	// selftest rows and hands out the next production block, so a selftest that
	// restored its own tenant would burn a production index and port block on
	// every run — and the copy, being outside the selftest range, would then
	// need a verified bundle of its own before `decommission` would clean it up.
	// The rule is the same one the allocator already states: which range a
	// tenant comes from is decided by which range it is a copy of.
	if p.isSandbox() {
		index, base, err := registry.AllocateSandbox(f)
		if err != nil {
			return fmt.Errorf("%w: %s", jobs.ErrRefused, err.Error())
		}
		spec.Index, spec.Base = index, base
		p.warn(fmt.Sprintf("%s is a selftest sandbox (ports %d–%d), so %s is allocated a SANDBOX block too: a restore "+
			"of a sandbox must not spend a production index, and a sandbox copy needs no bundle of its own to be "+
			"decommissioned", srcName, paths.SelftestBase, paths.SelftestEnd, as))
	} else {
		spec.Index, spec.Base = registry.Allocate(f)
	}
	spec.Tenant = prospectiveTenant(p.oc.Roots, f, spec)

	bundleDir := filepath.Join(p.oc.Roots.BackupsDir, srcName, from)
	p.result["restored_as"] = as
	p.result["bundle"] = from
	p.result["source"] = srcName

	p.warn(fmt.Sprintf("%s is built from the SOURCE tenant's current artifact (%s) and relational store kind (%s); "+
		"the first step refuses the bundle if its manifest disagrees, because a tenant restored onto different code "+
		"from the one its data came out of is not a copy of anything", as, artifactID, src.Stores.Postgres.Kind))
	p.warn("restore is also `backup verify`, deeply: it proves the bundle by rebuilding from it, and it is the only " +
		"operation that sets `verified` on one")
	p.warn("a failure at ANY step — including the final count check — rolls the whole job back: the fresh tenant's " +
		"registry row is deleted, its units are stopped and their files removed, its credential file, env files and " +
		"worktree are removed, and so is every file copied out of the bundle. Nothing is left running and no data " +
		"survives. What can remain is the empty directory tree and the built UI the create half laid down under " +
		"<data_dir>/" + as + ": the ctl has no recursive delete, and a rollback is not the place to acquire one. " +
		"Removing that tree by hand is safe, and restoring under the same name again works without it")
	p.warn(fmt.Sprintf("the job holds the tenant lock on %s, not on %s: %s does not exist yet, and the registry lock "+
		"plus the allocation step's own re-check are what stop a concurrent `create` claiming the name", srcName, as, as))

	// ---- 1. the bundle ----------------------------------------------------
	p.addBundleVerify(bundleDir, from, artifactID, src.Stores.Postgres.Kind)

	// ---- 2. the fresh tenant, laid down exactly as `create` lays one -------
	if err := planCreateSteps(p, spec); err != nil {
		return err
	}
	// planCreateSteps re-pointed the planner at the tenant it is making; the
	// store legs below are the TARGET's, so they are read while it still is.
	target := spec.Tenant
	targetPaths := p.tpaths
	legs, err := p.legs([]string{"qdrant", "es", "postgres"})
	if err != nil {
		return err
	}
	apiLegs, err := p.legs([]string{"api"})
	if err != nil {
		return err
	}

	// ---- 3. the data, copied into the fresh tree --------------------------
	p.addQdrantSnapshotCopies(bundleDir, targetPaths)
	p.addESRepoCopy(bundleDir, targetPaths)
	p.addSQLiteCopyIn(bundleDir, targetPaths)

	// ---- 4. the stores, and only the stores --------------------------------
	for _, c := range legs {
		if !c.Managed {
			p.skip("systemd", "skip "+c.Name, c.Why, c.Name)
			continue
		}
		p.addUnitStep("start", c)
	}
	p.addStoreReadyGate(spec)

	// ---- 5. the recovery ---------------------------------------------------
	p.addQdrantRecover(target, bundleDir)
	p.addESRestore(target, bundleDir)
	p.addPostgresRestore(spec, bundleDir, targetPaths)

	// ---- 6. the API, then the proof ---------------------------------------
	for _, c := range apiLegs {
		p.addUnitStep("start", c)
	}
	p.addAPIReadyGate(target)
	p.addRestoreVerify(target, bundleDir, from)

	// ---- 7. the records ----------------------------------------------------
	p.addBundleVerifiedFlag(bundleDir, from)
	p.addRestoreRecord(srcName, as, from)
	p.add(step{
		Kind: "nginx", Title: "publish the gateway generation including " + as, Targets: []string{as},
		Warnings: []string{"the gateway renders ACTIVE tenants, so this runs after the registry step that made " +
			as + " active"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			if err := sc.Checkpoint("gateway:apply:pending"); err != nil {
				return "", err
			}
			gen, detail, err := sc.Ops.Drivers.Gateway().Apply(ctx, false)
			if err != nil {
				return "", err
			}
			return detail, sc.Checkpoint("gateway:gen:" + strconv.Itoa(gen))
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return sc.Ops.Drivers.Gateway().Rollback(ctx, 0)
		},
	})

	// The planner goes back to the SOURCE: this is a tenant op on the source
	// (that is the row the lock, the audit and `--yes-destructive` name), and
	// planCreateSteps left the planner pointing at the tenant it made.
	p.t, p.tenant, p.tpaths = src, srcName, srcPaths
	return nil
}

// restoreStoreKind is the relational store the fresh tenant is built with: the
// SOURCE's, because that is the shape the bundle holds.
func restoreStoreKind(p *planner, src *registry.Tenant) (string, error) {
	switch src.Stores.Postgres.Kind {
	case registry.PostgresKindLocal:
		return paths.StorePostgresLocal, nil
	case registry.PostgresKindExternal:
		return "", p.refuse("%s keeps its relational state in a postgres server somebody else runs (kind: external): "+
			"the bundle has no dump of it, so a restore could only produce a tenant with an empty ACL, job and "+
			"collection store while claiming to be a copy", src.Name)
	default:
		return paths.StoreSQLite, nil
	}
}

// restoreESHeap is the source's provisioned heap, so the copy is sized like the
// original rather than like the default.
func restoreESHeap(src *registry.Tenant) string {
	for _, h := range []string{string(src.Stores.Elasticsearch.ProvisionHeap), string(src.Stores.Elasticsearch.Heap)} {
		if h != "" {
			return h
		}
	}
	return defaultESHeap
}

// restoreKeys mirrors the source's key LEDGER — the labels and the roles, never
// the values, which are minted fresh by the create steps and delivered once.
//
// Mirrored rather than "one admin key and be done": a restored tenant is meant
// to be usable in the source's place, and a tenant whose ledger has lost the
// `ingest` user key its clients authenticate with is not a copy of anything. A
// label the key grammar does not accept is skipped with a warning rather than
// failing the restore, because an old ledger row must not make a recovery
// impossible.
func restoreKeys(p *planner, src *registry.Tenant) []keySpec {
	out := []keySpec{{Label: bootstrapAdminLabel, Role: "admin"}}
	seen := map[string]bool{bootstrapAdminLabel: true}
	skipped := []string{}
	for _, k := range src.Keys {
		label := k.ID
		if label == "" {
			label = k.Label
		}
		if seen[label] {
			continue
		}
		if !reKeyLabel.MatchString(label) || (k.Role != "admin" && k.Role != "user") {
			skipped = append(skipped, label)
			continue
		}
		seen[label] = true
		out = append(out, keySpec{Label: label, Role: k.Role})
	}
	if len(skipped) > 0 {
		p.warn("these keys of " + src.Name + " are not reproduced on the restored tenant because their ledger rows " +
			"do not match today's label/role grammar: " + strings.Join(skipped, ", "))
	}
	p.warn("the restored tenant's keys are FRESH values under the source's labels and roles, delivered once through " +
		"this job's secrets envelope; the source's own key values are not recoverable from a bundle unless it was " +
		"sealed to an age recipient, and are not used here in any case")
	if len(src.ServiceAccounts) > 0 {
		p.warn(fmt.Sprintf("%s has %d service account(s); they are NOT recreated, because a service account is "+
			"registered THROUGH the tenant API and the restored tenant is not started until its data is back — "+
			"add them with `ragstack-ctl sa create` once the restore has finished", src.Name, len(src.ServiceAccounts)))
	}
	if src.Identity.AdminSubjectsCount > 0 {
		p.warn(fmt.Sprintf("%s admits %d admin subject(s); the registry records their COUNT and not the subjects "+
			"themselves, so the restored tenant is created with the same identity provider and no admin subjects — "+
			"add them with `ragstack-ctl admin add`", src.Name, src.Identity.AdminSubjectsCount))
	}
	return out
}

// publicSettings are the source's public settings, which is what `create
// --template-from` copies and for the same reason: ports, paths and store URLs
// are not settings a copy may inherit.
func publicSettings(src *registry.Tenant) map[string]string {
	out := map[string]string{}
	for _, k := range sortedStringKeys(src.Settings) {
		if settings.Classify(k) == settings.Public {
			out[k] = src.Settings[k]
		}
	}
	return out
}

// ---------------------------------------------------------------- 1. verify the bundle

// addBundleVerify is the step that reads the manifest — the first RUN half of
// the job, and the gate everything after it depends on.
func (p *planner) addBundleVerify(bundleDir, id, artifactID, pgKind string) {
	images := p.oc.Fleet.Images
	p.addFor("files", step{
		Kind: "fs", Title: "verify the bundle: manifest, fence, every checksum", Targets: []string{bundleDir},
		Warnings: []string{"SHA256SUMS is re-computed over EVERY file in the bundle and compared both ways, so a " +
			"sum that was removed from the list fails as loudly as one that was changed",
			"the manifest must name artifact " + artifactID + " and relational store kind " + pgKind +
				", which is what this plan built the fresh tenant from"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			files := sc.Ops.Drivers.Files()
			man, err := readRestoreManifest(ctx, files, bundleDir, id)
			if err != nil {
				return "", err
			}
			if !man.Fenced {
				return "", fmt.Errorf("%w: bundle %s is best_effort (unfenced): nothing stopped the tenant writing "+
					"while it was taken, so what it holds is a copy of a moving target and no tenant may be rebuilt "+
					"from it. Take a fenced one (`tenant backup <t> --fence`)", jobs.ErrRefused, id)
			}
			if !man.Consistent {
				return "", fmt.Errorf("%w: bundle %s records `consistent: false`: a count moved under the fence, or a "+
					"store its own inventory names is not in it. It cannot be restored from", jobs.ErrRefused, id)
			}
			if got := derefString(man.Artifact.ID); got != artifactID {
				return "", fmt.Errorf("%w: bundle %s was taken from artifact %q, and this plan built the fresh tenant "+
					"from the source tenant's current artifact %q. Restoring data into a checkout it never ran on is "+
					"how a schema change becomes silent corruption — prepare and record %q, or restore an older bundle",
					jobs.ErrRefused, id, orDefault(got, "(none)"), artifactID, orDefault(got, "(none)"))
			}
			if got := man.Stores.Postgres.Kind; got != pgKind {
				return "", fmt.Errorf("%w: bundle %s holds a %q relational store and this plan built the fresh tenant "+
					"with %q; the two lay their state down in different places, so the restore would leave half of it "+
					"unread", jobs.ErrRefused, id, got, pgKind)
			}
			if err := checkBundleImages(sc, man, images); err != nil {
				return "", err
			}
			n, err := verifyBundleChecksums(ctx, files, bundleDir, id, man)
			if err != nil {
				return "", err
			}
			sc.Logf("bundle %s: fenced, consistent, artifact %s, %d file(s) re-hashed", id, artifactID, n)
			return fmt.Sprintf("%s verified: %d file(s) match SHA256SUMS", id, n), nil
		},
	})
}

// readRestoreManifest reads and parses the bundle's manifest, and says the
// useful thing when it is not there: an interrupted backup leaves `<id>.partial`
// beside where `<id>` would be, and "no such directory" is a much worse answer
// than "that backup never finished".
func readRestoreManifest(ctx context.Context, files jobs.Files, bundleDir, id string) (restoreManifest, error) {
	var man restoreManifest
	b, err := files.ReadFile(ctx, filepath.Join(bundleDir, "manifest.json"))
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			if _, derr := files.ReadDir(ctx, bundleDir+partialSuffix); derr == nil {
				return man, fmt.Errorf("%w: there is no bundle %s — only %s%s, which is a backup that was "+
					"interrupted before it wrote its manifest. Take a new one", jobs.ErrRefused, id, id, partialSuffix)
			}
			return man, fmt.Errorf("%w: bundle %s has no manifest.json (%s)", jobs.ErrRefused, id, bundleDir)
		}
		return man, fmt.Errorf("%w: bundle %s is unreadable: %v", jobs.ErrRefused, id, err)
	}
	if err := json.Unmarshal(b, &man); err != nil {
		return man, fmt.Errorf("%w: bundle %s has no readable manifest.json: %v", jobs.ErrRefused, id, err)
	}
	if man.BundleID != id {
		return man, fmt.Errorf("%w: the manifest in %s calls itself %q: the directory has been renamed, and a bundle "+
			"whose own name disagrees with where it lives is one nothing should be rebuilt from",
			jobs.ErrRefused, bundleDir, man.BundleID)
	}
	return man, nil
}

// checkBundleImages is the plan's "target store versions = source versions".
//
// A restore recovers a qdrant snapshot and an elasticsearch repository into
// servers running from THIS fleet's images. Those formats are versioned, and a
// newer store reading an older snapshot is at best a one-way upgrade nobody
// asked for. An UNPINNED digest is not a mismatch — nobody has recorded what
// this fleet runs — so it warns instead, which is the honest difference between
// "these differ" and "we do not know".
func checkBundleImages(sc *jobs.StepContext, man restoreManifest, have registry.Images) error {
	for _, pair := range []struct {
		store string
		was   manifestImage
		now   registry.Image
	}{
		{"qdrant", man.Images.Qdrant, have.Qdrant},
		{"elasticsearch", man.Images.Elasticsearch, have.Elasticsearch},
	} {
		switch {
		case pair.now.Digest == registry.UnpinnedDigest || pair.was.Digest == registry.UnpinnedDigest:
			sc.Logf("the %s image is unpinned on one side (bundle %s, fleet %s): the version check is SKIPPED, and "+
				"whether the snapshot format matches is not something this job can answer",
				pair.store, pair.was.Version, pair.now.Version)
		case pair.was.Digest != pair.now.Digest:
			return fmt.Errorf("%w: the bundle was taken from %s %s (%s) and this fleet runs %s (%s): a snapshot is a "+
				"store-version artefact, so restoring across the two is not something the ctl will do silently. "+
				"Restore on a host running the bundle's image, or re-pin the fleet's", jobs.ErrRefused,
				pair.store, pair.was.Version, shortDigest(pair.was.Digest), pair.now.Version, shortDigest(pair.now.Digest))
		}
	}
	return nil
}

// verifyBundleChecksums re-hashes every file the bundle holds.
//
// Both directions, and the manifest's own digest of the list:
//
//   - every line of SHA256SUMS must name a file that is there and hashes to
//     what the line says;
//   - every file in the bundle (except SHA256SUMS and manifest.json, which is
//     where its digest is recorded) must HAVE a line — otherwise a tampered
//     bundle need only delete the line for the file it changed;
//   - SHA256SUMS itself must hash to the manifest's `sha256sums`, which is what
//     makes the manifest the single root of trust rather than one of two
//     documents that can be edited independently.
func verifyBundleChecksums(ctx context.Context, files jobs.Files, dir, id string, man restoreManifest) (int, error) {
	body, err := files.ReadFile(ctx, filepath.Join(dir, "SHA256SUMS"))
	if err != nil {
		return 0, fmt.Errorf("%w: bundle %s has no SHA256SUMS: there is nothing to check it against",
			jobs.ErrRefused, id)
	}
	if got := sha256Hex(body); got != man.SHA256Sums {
		return 0, fmt.Errorf("%w: bundle %s's SHA256SUMS hashes to %s and its manifest says %s: the checksum list "+
			"has been edited since the manifest was written", jobs.ErrRefused, id, shortHex(got), shortHex(man.SHA256Sums))
	}
	want := map[string]string{}
	for _, line := range strings.Split(string(body), "\n") {
		line = strings.TrimRight(line, "\r")
		if strings.TrimSpace(line) == "" {
			continue
		}
		sum, rel, ok := strings.Cut(line, "  ")
		if !ok {
			return 0, fmt.Errorf("%w: bundle %s has a SHA256SUMS line this build cannot read: %q",
				jobs.ErrRefused, id, line)
		}
		want[rel] = sum
	}
	have, err := walkFiles(ctx, files, dir)
	if err != nil {
		return 0, fmt.Errorf("listing bundle %s: %w", id, err)
	}
	n := 0
	for _, rel := range have {
		if rel == "SHA256SUMS" || rel == "manifest.json" {
			continue
		}
		sum, ok := want[rel]
		if !ok {
			return 0, fmt.Errorf("%w: bundle %s holds %s, which SHA256SUMS does not cover: a file nothing vouches "+
				"for is a file that could have been put there", jobs.ErrRefused, id, rel)
		}
		got, _, err := files.Sha256(ctx, filepath.Join(dir, rel))
		if err != nil {
			return 0, fmt.Errorf("%w: bundle %s: %s cannot be read to check it: %v", jobs.ErrRefused, id, rel, err)
		}
		if got != sum {
			return 0, fmt.Errorf("%w: bundle %s: %s hashes to %s, and SHA256SUMS says %s — the bundle is damaged or "+
				"has been altered, and nothing may be rebuilt from it", jobs.ErrRefused, id, rel, shortHex(got), shortHex(sum))
		}
		delete(want, rel)
		n++
	}
	if len(want) > 0 {
		missing := make([]string, 0, len(want))
		for rel := range want {
			missing = append(missing, rel)
		}
		sort.Strings(missing)
		return 0, fmt.Errorf("%w: bundle %s is missing %d file(s) its own SHA256SUMS lists: %s",
			jobs.ErrRefused, id, len(missing), strings.Join(missing, ", "))
	}
	return n, nil
}

// ---------------------------------------------------------------- 3. the copies

// addQdrantSnapshotCopies puts the bundle's snapshot files where the fresh
// tenant's qdrant will look for them.
func (p *planner) addQdrantSnapshotCopies(bundleDir string, tp paths.Tenant) {
	p.addFor("files", step{
		Kind: "fs", Title: "copy the bundle's qdrant snapshots into the fresh tenant's snapshot tree",
		Targets:    []string{bundleDir, tp.QdrantSnapshots},
		WouldWrite: []model.WouldWrite{{Path: tp.QdrantSnapshots, Mode: "0640", Preview: model.NullString("")}},
		Warnings: []string{"which collections, and which file for each, is read from the manifest when the job runs; " +
			"the copy is streamed, because a snapshot of a real collection does not fit in memory"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			files := sc.Ops.Drivers.Files()
			man, err := p.manifestOf(ctx, sc, bundleDir)
			if err != nil {
				return "", err
			}
			n := 0
			for _, c := range man.Stores.Qdrant.Collections {
				if !c.Included || c.File == nil {
					sc.Logf("collection %s is recorded as excluded; nothing to copy", c.Name)
					continue
				}
				dst := filepath.Join(tp.QdrantSnapshots, c.Name, filepath.Base(*c.File))
				if err := files.MkdirAll(ctx, filepath.Dir(dst), dirMode); err != nil {
					return "", err
				}
				if err := sc.Checkpoint("file:" + dst); err != nil {
					return "", err
				}
				if err := files.CopyFile(ctx, filepath.Join(bundleDir, *c.File), dst, 0o640); err != nil {
					return "", fmt.Errorf("copying the snapshot of %s into the fresh tenant: %w", c.Name, err)
				}
				n++
			}
			return fmt.Sprintf("%d snapshot file(s)", n), nil
		},
		Rollback: removeCheckpointedFiles,
	})
}

// addESRepoCopy copies the bundle's snapshot REPOSITORY — a directory of
// segment files elasticsearch wrote — into the fresh tenant's path.repo.
func (p *planner) addESRepoCopy(bundleDir string, tp paths.Tenant) {
	p.addFor("files", step{
		Kind: "fs", Title: "copy the bundle's elasticsearch snapshot repository into the fresh tenant's path.repo",
		Targets:    []string{bundleDir, tp.ESSnapshots},
		WouldWrite: []model.WouldWrite{{Path: tp.ESSnapshots, Mode: "0640", Preview: model.NullString("")}},
		Warnings: []string{"the whole repository directory, file by file: a snapshot repository is only readable as " +
			"a whole, and one missing segment is a restore that fails after the tenant has been built"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			files := sc.Ops.Drivers.Files()
			man, err := p.manifestOf(ctx, sc, bundleDir)
			if err != nil {
				return "", err
			}
			if man.esRepo() == "" {
				sc.Logf("the bundle records no elasticsearch leg; nothing to copy")
				return "no elasticsearch repository in this bundle", nil
			}
			rel := filepath.Join("elasticsearch", "snapshots", man.BundleID)
			from, to := filepath.Join(bundleDir, rel), filepath.Join(tp.ESSnapshots, man.BundleID)
			names, err := walkFiles(ctx, files, from)
			if err != nil {
				return "", fmt.Errorf("listing the bundle's snapshot repository: %w", err)
			}
			// The manifest lists what the repository held when the bundle was
			// written; the directory is what is actually there. Every listed
			// file must be present — a repository missing a segment is a
			// restore that fails after the tenant has been built — and anything
			// else in the directory is copied too, because a snapshot
			// repository is only readable as a whole.
			have := map[string]bool{}
			for _, name := range names {
				have[name] = true
			}
			missing := []string{}
			for _, listed := range man.Stores.Elasticsearch.Files {
				name := strings.TrimPrefix(strings.TrimPrefix(listed, rel), "/")
				if name != "" && !have[name] {
					missing = append(missing, listed)
				}
			}
			if len(missing) > 0 {
				return "", fmt.Errorf("%w: the bundle's manifest lists %d repository file(s) that are not in %s: %s",
					jobs.ErrRefused, len(missing), rel, strings.Join(missing, ", "))
			}
			if len(names) == 0 {
				sc.Logf("the bundle's snapshot repository directory is empty and its manifest lists no files")
			}
			for _, name := range names {
				dst := filepath.Join(to, name)
				if err := files.MkdirAll(ctx, filepath.Dir(dst), dirMode); err != nil {
					return "", err
				}
				if err := sc.Checkpoint("file:" + dst); err != nil {
					return "", err
				}
				if err := files.CopyFile(ctx, filepath.Join(from, name), dst, 0o640); err != nil {
					return "", fmt.Errorf("copying %s into the fresh tenant's repository: %w", name, err)
				}
			}
			sc.Logf("%d repository file(s) -> %s", len(names), to)
			return fmt.Sprintf("%d file(s) of repository %s", len(names), man.BundleID), nil
		},
		Rollback: removeCheckpointedFiles,
	})
}

// addSQLiteCopyIn restores the tenant's own SQLite state files.
//
// A plain copy and not `SQLite.Backup`: the bundle's copies were taken with
// VACUUM INTO from a fenced tenant and have no WAL beside them, so the file IS
// the database. Running a VACUUM on the way back in would rewrite a database
// nothing has opened yet in order to prove something the backup already proved.
func (p *planner) addSQLiteCopyIn(bundleDir string, tp paths.Tenant) {
	p.addFor("files", step{
		Kind: "fs", Title: "copy the bundle's SQLite state files into the fresh tenant's state directory",
		Targets:    []string{bundleDir, tp.StateDir},
		WouldWrite: []model.WouldWrite{{Path: tp.StateDir, Mode: "0640", Preview: model.NullString("")}},
		Warnings:   []string{"which files, from the manifest's `sqlite` list, when the job runs"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			files := sc.Ops.Drivers.Files()
			man, err := p.manifestOf(ctx, sc, bundleDir)
			if err != nil {
				return "", err
			}
			copied := []string{}
			for _, e := range man.SQLite {
				dst := filepath.Join(tp.StateDir, filepath.Base(e.File))
				if err := files.MkdirAll(ctx, tp.StateDir, dirMode); err != nil {
					return "", err
				}
				if err := sc.Checkpoint("file:" + dst); err != nil {
					return "", err
				}
				if err := files.CopyFile(ctx, filepath.Join(bundleDir, e.File), dst, 0o640); err != nil {
					return "", fmt.Errorf("copying %s into the fresh tenant: %w", e.File, err)
				}
				copied = append(copied, filepath.Base(e.File))
			}
			if len(copied) == 0 {
				return "the bundle holds no SQLite state files", nil
			}
			return strings.Join(copied, ", "), nil
		},
		Rollback: removeCheckpointedFiles,
	})
}

// removeCheckpointedFiles is the rollback of every copy step: it deletes the
// files the step recorded before it wrote them, and nothing else. A rollback
// that removed a DIRECTORY would take the tenant tree the create steps made
// with it, and those have their own rollback.
func removeCheckpointedFiles(ctx context.Context, sc *jobs.StepContext) (string, error) {
	n := 0
	for _, id := range sc.Step.ExternalIDs {
		path, ok := strings.CutPrefix(id, "file:")
		if !ok {
			continue
		}
		if err := sc.Ops.Drivers.Files().Remove(ctx, path); err != nil {
			return "", err
		}
		n++
	}
	return fmt.Sprintf("removed %d copied file(s)", n), nil
}

// ---------------------------------------------------------------- 4. readiness

// addStoreReadyGate waits for the fresh tenant's stores — and only its stores.
// The API is not started yet: it would come up against empty stores, register
// nothing and have to be restarted.
func (p *planner) addStoreReadyGate(spec createSpec) {
	t := spec.Tenant
	pgSpec := jobs.PostgresSpec{
		SIF: string(t.Stores.Postgres.SIF), RunDir: p.tpaths.PostgresRun, DB: spec.Name, User: spec.Name,
	}
	local := spec.postgresLocal()
	p.addFor("proc", step{
		Kind: "probe", Title: fmt.Sprintf("wait for %s's stores to answer (up to %s)", spec.Name, createReadyTimeout),
		Targets: []string{t.Stores.Qdrant.URL, t.Stores.Elasticsearch.URL},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			checks := []struct {
				what  string
				probe func() error
			}{
				{"qdrant", func() error { return sc.Ops.Drivers.Qdrant().Ready(ctx, t.Stores.Qdrant.URL) }},
				{"elasticsearch", func() error {
					return sc.Ops.Drivers.Elasticsearch().Ready(ctx, t.Stores.Elasticsearch.URL)
				}},
			}
			if local {
				checks = append(checks, struct {
					what  string
					probe func() error
				}{"postgres", func() error { return sc.Ops.Drivers.Postgres().Ready(ctx, pgSpec) }})
			}
			return waitFor(ctx, sc, checks)
		},
	})
}

// addAPIReadyGate waits for the restored tenant's API to bind its port.
func (p *planner) addAPIReadyGate(t *registry.Tenant) {
	port := t.Ports.API
	p.addFor("proc", step{
		Kind: "probe", Title: fmt.Sprintf("wait for the restored API to listen on %d (up to %s)", port, createReadyTimeout),
		Targets: []string{strconv.Itoa(port)},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return waitFor(ctx, sc, []struct {
				what  string
				probe func() error
			}{{"api", func() error {
				listening, err := sc.Ops.Drivers.Proc().Listening(ctx, port)
				if err != nil {
					return err
				}
				if !listening {
					return fmt.Errorf("nothing is listening on %d", port)
				}
				return nil
			}}})
		},
	})
}

// waitFor polls each check to success or to the readiness deadline.
//
// The WALL clock and a real sleep, deliberately — this is a run half, and what
// it waits for is elasticsearch opening the segments the copy step just put
// there. An injected clock would make the gate return before the store was up,
// which is the bug the gate exists to prevent.
func waitFor(ctx context.Context, sc *jobs.StepContext, checks []struct {
	what  string
	probe func() error
}) (string, error) {
	deadline := time.Now().Add(createReadyTimeout)
	names := make([]string, 0, len(checks))
	for _, c := range checks {
		for {
			err := c.probe()
			if err == nil {
				break
			}
			if time.Now().After(deadline) {
				return "", fmt.Errorf("%s did not become ready within %s: %w", c.what, createReadyTimeout, err)
			}
			select {
			case <-ctx.Done():
				return "", ctx.Err()
			case <-time.After(createReadyPoll):
			}
		}
		sc.Logf("%s ready", c.what)
		names = append(names, c.what)
	}
	return strings.Join(names, ", ") + " answered", nil
}

// ---------------------------------------------------------------- 5. the recovery

func (p *planner) addQdrantRecover(t *registry.Tenant, bundleDir string) {
	url := t.Stores.Qdrant.URL
	p.addFor("qdrant", step{
		Kind: "qdrant", Title: "recover every collection into the fresh qdrant from its snapshot",
		Targets: []string{url},
		Warnings: []string{"the location handed to qdrant is a path INSIDE its container (" +
			qdrantContainerSnapshots + "<collection>/<file>), which is the snapshot tree the copy step wrote to"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			man, err := p.manifestOf(ctx, sc, bundleDir)
			if err != nil {
				return "", err
			}
			drv := sc.Ops.Drivers.Qdrant()
			done := []string{}
			for _, c := range man.Stores.Qdrant.Collections {
				if !c.Included || c.File == nil {
					continue
				}
				// The collection is checkpointed BEFORE the call: a crash
				// between the two leaves a record naming the collection whose
				// recovery may be half done inside the store.
				if err := sc.Checkpoint("qdrant:recover:" + c.Name); err != nil {
					return "", err
				}
				location := qdrantContainerSnapshots + c.Name + "/" + filepath.Base(*c.File)
				if err := drv.Recover(ctx, url, c.Name, location); err != nil {
					return "", fmt.Errorf("recovering collection %s from %s: %w", c.Name, location, err)
				}
				sc.Logf("recovered %s (%d points expected)", c.Name, c.PointsAfter)
				done = append(done, c.Name)
			}
			if len(done) == 0 {
				return "the bundle holds no qdrant collections", nil
			}
			return fmt.Sprintf("%d collection(s): %s", len(done), strings.Join(done, ", ")), nil
		},
	})
}

func (p *planner) addESRestore(t *registry.Tenant, bundleDir string) {
	url := t.Stores.Elasticsearch.URL
	containerRepos := string(t.Stores.Elasticsearch.PathRepo)
	p.addFor("elasticsearch", step{
		Kind: "es", Title: "register the copied repository and _restore every index into the fresh elasticsearch",
		Targets: []string{url},
		Warnings: []string{"the repository is registered at the path INSIDE the container and unregistered again " +
			"whether the restore succeeds or fails, so the fresh cluster is never left holding a repository that " +
			"belongs to a bundle"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			man, err := p.manifestOf(ctx, sc, bundleDir)
			if err != nil {
				return "", err
			}
			if man.esRepo() == "" {
				return "the bundle holds no elasticsearch snapshot", nil
			}
			indices := man.includedIndices()
			if len(indices) == 0 {
				return "the bundle's elasticsearch snapshot holds no included index", nil
			}
			drv := sc.Ops.Drivers.Elasticsearch()
			// The repository is registered under the name the BUNDLE recorded.
			// A repository is identified by its directory, and the bundle's
			// manifest is the only record of which directory this is; inventing
			// a second name for the same files would mean a half-finished
			// restore left an operator with a repository that matches nothing
			// in the manifest they are reading.
			repo := man.esRepo()
			// The repository name is the ctl's own and is recorded before the
			// registration, so the rollback (and reconcile) can unregister
			// exactly what this step may have created.
			if err := sc.Checkpoint("es:restore:" + repo); err != nil {
				return "", err
			}
			location := strings.TrimSuffix(containerRepos, "/") + "/" + man.BundleID
			if err := drv.RegisterRepo(ctx, url, repo, location, true); err != nil {
				return "", fmt.Errorf("registering the bundle's repository at %s: %w", location, err)
			}
			listed, err := drv.Snapshots(ctx, url, repo)
			if err != nil {
				return "", fmt.Errorf("listing the copied repository: %w", err)
			}
			if !containsStr(listed, man.esSnapshot()) {
				return "", fmt.Errorf("%w: the copied repository at %s does not hold the snapshot %s the manifest "+
					"names (it holds %v): the copy is incomplete or the bundle is not what it says it is",
					jobs.ErrRefused, location, man.esSnapshot(), listed)
			}
			if err := drv.Restore(ctx, url, repo, man.esSnapshot(), indices); err != nil {
				return "", fmt.Errorf("restoring %v from %s: %w", indices, man.esSnapshot(), err)
			}
			if err := drv.UnregisterRepo(ctx, url, repo); err != nil {
				return "", err
			}
			sc.Logf("restored %d index/indices from %s", len(indices), man.esSnapshot())
			return fmt.Sprintf("%d index/indices: %s", len(indices), strings.Join(indices, ", ")), nil
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			// Unregistering an unknown repository is a no-op, so this is safe
			// whether the step failed before or after the registration.
			for _, id := range sc.Step.ExternalIDs {
				if repo, ok := strings.CutPrefix(id, "es:restore:"); ok {
					if err := sc.Ops.Drivers.Elasticsearch().UnregisterRepo(ctx, url, repo); err != nil {
						return "", err
					}
				}
			}
			return "the bundle's repository is unregistered", nil
		},
	})
}

// addPostgresRestore pours the bundle's dump into the fresh instance.
//
// The dump is read from where it lies, inside the bundle: `pg_restore` streams
// it, and copying a multi-gigabyte archive into the tenant tree first would
// double the space a restore needs for no gain.
func (p *planner) addPostgresRestore(spec createSpec, bundleDir string, tp paths.Tenant) {
	if !spec.postgresLocal() {
		p.skip("postgres", "skip the postgres restore",
			"this tenant keeps its relational state in SQLite under <data_dir>/state, which the state copy already "+
				"put in place", spec.StoreKind)
		return
	}
	pgSpec := jobs.PostgresSpec{
		SIF: string(spec.Tenant.Stores.Postgres.SIF), RunDir: tp.PostgresRun, DB: spec.Name, User: spec.Name,
	}
	p.addFor("postgres", step{
		Kind: "postgres", Title: "restore the bundle's postgres dump into the fresh instance",
		Targets: []string{pgSpec.RunDir, pgSpec.DB},
		Warnings: []string{"`pg_restore --no-owner --role=" + spec.Name + "`: the dump belongs to the SOURCE tenant's " +
			"role and the fresh tenant has its own, so ownership is re-assigned rather than carried across"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			man, err := p.manifestOf(ctx, sc, bundleDir)
			if err != nil {
				return "", err
			}
			if !man.Stores.Postgres.Included || man.Stores.Postgres.File == nil {
				return "", fmt.Errorf("%w: the bundle records a %q relational store but carries no dump of it, so the "+
					"restored tenant would have an empty ACL, job and collection store",
					jobs.ErrRefused, man.Stores.Postgres.Kind)
			}
			in := filepath.Join(bundleDir, *man.Stores.Postgres.File)
			if err := sc.Checkpoint("postgres:restore:" + pgSpec.DB); err != nil {
				return "", err
			}
			if err := sc.Ops.Drivers.Postgres().Restore(ctx, pgSpec, in); err != nil {
				return "", err
			}
			return fmt.Sprintf("%s -> %s", *man.Stores.Postgres.File, pgSpec.DB), nil
		},
	})
}

// ---------------------------------------------------------------- 6. the proof

// addRestoreVerify is what makes this verb a verification rather than a copy.
//
// Three questions, all asked of the RESTORED tenant and all answered against
// the manifest: does each collection hold the points the bundle says it held,
// does each index hold the documents, and does the tenant's own API report the
// inventory the bundle recorded. A mismatch fails the job — which rolls the
// whole restore back — and names what disagreed, because "the restore
// succeeded" over a store that came back half full is the one outcome nobody
// could detect later.
func (p *planner) addRestoreVerify(t *registry.Tenant, bundleDir, id string) {
	qURL, esURL := t.Stores.Qdrant.URL, t.Stores.Elasticsearch.URL
	origin := fmt.Sprintf("http://127.0.0.1:%d", t.Ports.API)
	secrets := p.secrets
	p.addFor("tenantapi", step{
		Kind: "probe", Title: "verify the restored tenant against the bundle: point counts, document counts, inventory",
		Targets: []string{qURL, esURL, origin},
		Warnings: []string{"any disagreement fails the job, and a failed restore rolls back completely: the fresh " +
			"tenant is removed rather than left half full"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			man, err := p.manifestOf(ctx, sc, bundleDir)
			if err != nil {
				return "", err
			}
			qCounts, esCounts := map[string]int64{}, map[string]int64{}
			for _, c := range man.Stores.Qdrant.Collections {
				if !c.Included {
					continue
				}
				got, err := sc.Ops.Drivers.Qdrant().Count(ctx, qURL, c.Name)
				if err != nil {
					return "", fmt.Errorf("counting the restored collection %s: %w", c.Name, err)
				}
				if got != c.PointsAfter {
					return "", fmt.Errorf("%w: collection %s came back with %d point(s) and bundle %s recorded %d: "+
						"the restore is incomplete", jobs.ErrRefused, c.Name, got, id, c.PointsAfter)
				}
				qCounts[c.Name] = got
			}
			for _, i := range man.Stores.Elasticsearch.Indices {
				if !i.Included {
					continue
				}
				got, err := sc.Ops.Drivers.Elasticsearch().Count(ctx, esURL, i.Name)
				if err != nil {
					return "", fmt.Errorf("counting the restored index %s: %w", i.Name, err)
				}
				if got != i.DocsAfter {
					return "", fmt.Errorf("%w: index %s came back with %d document(s) and bundle %s recorded %d: "+
						"the restore is incomplete", jobs.ErrRefused, i.Name, got, id, i.DocsAfter)
				}
				esCounts[i.Name] = got
			}

			// The tenant's OWN inventory, read with the admin key this job
			// minted minutes ago. The key is held in the job's memory (the same
			// place the create steps keep it for the service-account calls); it
			// is never logged, never checkpointed and never a step target.
			key := adminKeyFromSecrets(secrets)
			if key == "" {
				return "", fmt.Errorf("%w: the restore has no admin credential for %s, so its inventory cannot be "+
					"read back", jobs.ErrRefused, origin)
			}
			got, err := sc.Ops.Drivers.TenantAPI().Collections(ctx, origin, key)
			if err != nil {
				return "", fmt.Errorf("reading the restored tenant's collection inventory: %w", err)
			}
			want := append([]string(nil), man.Inventory.Collections...)
			sort.Strings(want)
			sort.Strings(got)
			if strings.Join(got, ",") != strings.Join(want, ",") {
				return "", fmt.Errorf("%w: the restored tenant reports collections %v and bundle %s recorded %v: "+
					"the stores came back but the tenant's own collection registry did not",
					jobs.ErrRefused, got, id, want)
			}
			p.result["counts"] = map[string]any{"qdrant": qCounts, "elasticsearch": esCounts}
			sc.Logf("%d collection(s) and %d index/indices match the bundle; the inventory agrees",
				len(qCounts), len(esCounts))
			return fmt.Sprintf("%d collection(s), %d index/indices and the inventory match bundle %s",
				len(qCounts), len(esCounts), id), nil
		},
	})
}

// adminKeyFromSecrets reads the bootstrap admin key out of the envelope the
// create steps filled. It is the one credential this job has, and it exists
// only while the job runs.
func adminKeyFromSecrets(secrets func() []model.Secret) string {
	if secrets == nil {
		return ""
	}
	minted := secrets()
	for _, s := range minted {
		if s.Label == bootstrapAdminLabel {
			return s.Value
		}
	}
	for _, s := range minted {
		if s.Role == "admin" {
			return s.Value
		}
	}
	return ""
}

// ---------------------------------------------------------------- 7. the records

// addBundleVerifiedFlag rewrites the bundle's manifest with `verified: true`.
//
// The manifest only — SHA256SUMS deliberately does NOT cover manifest.json (the
// manifest carries the checksum file's digest, so it cannot be inside it), so
// there is no second document to keep in step. And it happens BEFORE the
// registry write: if the bundle cannot be marked, the job fails and rolls back,
// rather than leaving a registry that claims a verification the bundle has no
// record of.
func (p *planner) addBundleVerifiedFlag(bundleDir, id string) {
	path := filepath.Join(bundleDir, "manifest.json")
	p.addFor("files", step{
		Kind: "fs", Title: "mark bundle " + id + " verified in its own manifest", Targets: []string{path},
		WouldWrite: []model.WouldWrite{{Path: path, Mode: "0640", Preview: model.NullString("")}},
		Warnings: []string{"SHA256SUMS does not cover manifest.json (the manifest carries ITS digest, not the other " +
			"way round), so marking the bundle leaves every checksum in it still true"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return setManifestVerified(ctx, sc, path, true)
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return setManifestVerified(ctx, sc, path, false)
		},
	})
}

// setManifestVerified rewrites one member of the manifest, leaving every other
// member exactly as the bundle wrote it — a generic map, not this build's
// struct, because a manifest written by a later version must survive being
// marked by this one.
func setManifestVerified(ctx context.Context, sc *jobs.StepContext, path string, verified bool) (string, error) {
	files := sc.Ops.Drivers.Files()
	body, err := files.ReadFile(ctx, path)
	if err != nil {
		return "", err
	}
	var man map[string]any
	if err := json.Unmarshal(body, &man); err != nil {
		return "", fmt.Errorf("%w: %s is not readable as JSON: %v", jobs.ErrRefused, path, err)
	}
	man["verified"] = verified
	out, err := json.MarshalIndent(man, "", "  ")
	if err != nil {
		return "", err
	}
	if err := files.WriteAtomic(ctx, path, append(out, '\n'), 0o640); err != nil {
		return "", err
	}
	return fmt.Sprintf("%s verified=%v", path, verified), nil
}

// addRestoreRecord is the registry's half, and it is ONE write for both rows:
// the fresh tenant becomes `active` now that its data is in it and has been
// counted, and the SOURCE's backup record learns that a restore has proved it.
//
// One save rather than two because registry.Save bumps the generation, and a
// single operator action that advanced it twice makes "what changed at
// generation 91?" unanswerable.
func (p *planner) addRestoreRecord(srcName, as, id string) {
	p.add(step{
		Kind: "registry", Title: fmt.Sprintf("record %s as active and mark %s's bundle %s verified", as, srcName, id),
		Targets:    []string{as, srcName},
		WouldWrite: []model.WouldWrite{{Path: p.oc.Roots.Registry(), Mode: "0660", Preview: model.NullString("")}},
		Warnings: []string{"this is the only thing in the control plane that sets `verified` on a backup, and it sets " +
			"it because a tenant was rebuilt from the bundle and its counts were checked"},
		Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			save := p.op.deps.SaveFleet
			if save == nil {
				return "", fmt.Errorf("%w: the registry writer is not wired", jobs.ErrRefused)
			}
			f := sc.Ops.Fleet
			if f == nil {
				return "", fmt.Errorf("%w: the engine loaded no registry under the locks", jobs.ErrRefused)
			}
			target, ok := f.Tenants[as]
			if !ok {
				return "", fmt.Errorf("%w: %s is no longer in the registry", jobs.ErrRefused, as)
			}
			at := p.stampRFC3339(sc)
			target.State = "active"
			if target.LastOps == nil {
				target.LastOps = map[string]registry.OpRecord{}
			}
			target.LastOps["restore"] = registry.OpRecord{JobID: jobIDOf(sc), At: at, Outcome: "succeeded"}

			marked := "the source row was not changed"
			if row, ok := f.Tenants[srcName]; ok && row.LastBackup != nil {
				// By BASENAME: registry.json types last_backup.bundle as the
				// absolute directory and every operator-facing surface speaks
				// the id, so the two differ by exactly this.
				if filepath.Base(row.LastBackup.Bundle) == id {
					row.LastBackup.Verified = true
					marked = srcName + "'s last_backup is verified"
				} else {
					// Not an error: restoring from an older bundle is a
					// legitimate thing to do, and quietly moving the tenant's
					// recovery point backwards would be worse than saying so.
					sc.Logf("%s's last_backup is %s, not %s, so its `verified` flag is left alone",
						srcName, filepath.Base(row.LastBackup.Bundle), id)
					marked = srcName + "'s last_backup is a different bundle and was left alone"
				}
			}
			if err := save(f); err != nil {
				return "", err
			}
			sc.Logf("registry generation %d: %s is active, %s", f.Generation, as, marked)
			return fmt.Sprintf("%s active (generation %d); %s", as, f.Generation, marked), nil
		},
	})
}

// ---------------------------------------------------------------- helpers

// manifestOf re-reads the bundle manifest inside a run half.
//
// Re-read rather than carried in the planner: a resumed job RE-PLANS, so the
// steps that already succeeded do not run again and anything they left in the
// plan's memory is gone by the time a later step needs it. The manifest is a
// small document on the same filesystem the step is about to copy gigabytes
// from; reading it once per step is the cheapest correct answer.
func (p *planner) manifestOf(ctx context.Context, sc *jobs.StepContext, bundleDir string) (restoreManifest, error) {
	return readRestoreManifest(ctx, sc.Ops.Drivers.Files(), bundleDir, filepath.Base(bundleDir))
}

func derefString(s *string) string {
	if s == nil {
		return ""
	}
	return *s
}

// shortHex is a digest as an operator reads it in a refusal.
func shortHex(s string) string {
	if len(s) > 12 {
		return s[:12] + "…"
	}
	return s
}

func shortDigest(s string) string {
	return shortHex(strings.TrimPrefix(s, "sha256:"))
}
