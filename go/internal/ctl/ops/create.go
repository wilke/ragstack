package ops

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/envfile"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/render"
	"github.com/ragstack/ragstack/internal/ctl/settings"
)

// ---------------------------------------------------------------- create
//
// `tenant create` is the verb #537 asked for: allocate a block, lay the tree
// down, mint the credentials, check the code out, build the UI, write and link
// the units, start it, register the service accounts, publish the route, and
// record every one of those in the registry — as ONE job, with a rollback for
// each step that made something.
//
// Two rules shape the whole file.
//
// A PLAN IS PURE. Everything below the validation is computed from the
// registry snapshot and the args: the allocation, the rendered env file, the
// rendered units, the provision file. Nothing reads the clock or the host,
// because the engine computes the plan twice — once to show you, once under
// the locks — and refuses the job when the two differ.
//
// A SECRET IS MINTED AT RUN TIME AND DELIVERED ONCE. The keys do not exist
// while the plan is being computed, hashed, logged and shown; they are created
// by the secrets step, held in this job's memory for the length of the run so
// the service-account step can authenticate with one, and handed to the
// operator through the job's secrets envelope. The registry gets fingerprints.

// createReadyTimeout bounds the inline readiness gate of a create. It matches
// the rendered api unit's `wait-ready --timeout 180`: the same stores, the
// same cold start, so the same patience.
const createReadyTimeout = 180 * time.Second

// createReadyPoll is how often the gate re-probes.
var createReadyPoll = 2 * time.Second

// previewSecret is the value the PLAN renders wherever a minted secret will go.
//
// render's own `<GENERATED:…>` placeholders are not usable here: composeEnv
// parses the rendered file in order to split it, and the envfile parser refuses
// `<` in an unquoted value (the shell would act on it, systemd would not). It
// does not matter what the value is — every line it appears in is secret-class
// and therefore has NO preview at all — only that it parses and could never be
// mistaken for a credential.
const previewSecret = "GENERATED-AT-RUN-TIME"

// bootstrapAdminLabel is the admin key `create` always mints, whether or not
// the request asked for one. The ctl needs an admin credential of its own to
// register the service accounts the request asked for and to run the deep
// health check, and a tenant whose only admin key was handed to a human is a
// tenant the control plane cannot finish creating.
const bootstrapAdminLabel = "bootstrap-admin"

// keySpec is one key `create` will mint.
type keySpec struct {
	Label string
	Role  string
}

// createSpec is everything the step builder needs, all of it decided at plan
// time. It is a struct rather than a dozen parameters because `restore --as`
// builds one too (with Start and Gateway false) and calls planCreateSteps
// directly: the two verbs must lay a tenant down the SAME way, or a restored
// tenant is a tenant nothing else in the control plane has ever seen.
type createSpec struct {
	Name       string
	Tenant     *registry.Tenant
	Artifact   *registry.Artifact
	ArtifactID string
	Index      int
	Base       int

	// StoreKind is paths.StoreSQLite or paths.StorePostgresLocal — the value
	// provision.env records and new-tenant.sh would have written.
	StoreKind string
	ESHeap    string
	Provider  string
	Subjects  []string
	Keys      []keySpec
	Accounts  []serviceAccountArg
	Settings  map[string]string
	UIMode    string

	Start   bool
	Gateway bool

	// Owner is the account recorded on the row: the one running the ctl.
	Owner string

	// Verb is the name the registry's last_ops entry is filed under, empty
	// meaning "create". `restore --as` lays its fresh tenant down through this
	// same builder, and a row whose last_ops said `create` with a restore job's
	// id would be the registry telling an operator the tenant was created by an
	// operation that never ran. The STATE is not a field beside it: with
	// Start false the builder records `provisioned`, which is exactly what a
	// half-restored tenant is — the restore's own final step moves it to
	// `active` once the data is back and the counts have been checked.
	Verb string

	// Mirror is the bare repository the worktree is checked out of.
	Mirror string
}

// postgresLocal reports whether this tenant runs its own postgres instance.
func (s createSpec) postgresLocal() bool { return s.StoreKind == paths.StorePostgresLocal }

// blockAllocator picks the port block a create claims. There are exactly two:
// registry.Allocate for production (tombstone-aware, sandbox-blind) and
// registry.AllocateSandbox for the selftest's own range. Making it a parameter
// rather than a boolean in the args is what keeps a request from choosing:
// `create` is wired to one of them and `create-sandbox` to the other.
type blockAllocator func(*registry.Fleet) (index, base int, err error)

// productionBlock is registry.Allocate behind the allocator signature. It
// cannot fail — the production sequence is unbounded — so the error is always
// nil, and the shape is shared so that the sandbox allocator's refusal ("every
// sandbox block is taken") travels the same path.
func productionBlock(f *registry.Fleet) (int, int, error) {
	index, base := registry.Allocate(f)
	return index, base, nil
}

func planCreate(_ context.Context, p *planner, args map[string]any) error {
	// A `ctltest-` name never lands on a production block.
	//
	// The selftest's tenants are recognised downstream by their PORTS
	// (paths.IsSelftestBlock): that is what tells `decommission` it needs no
	// verified bundle and what tells the sweep it may remove a directory. A
	// tenant carrying the sandbox NAME on a production block would read as
	// disposable to every human and as production to every one of those rules,
	// which is the worst way round.
	if name := argStringOf(args, "name"); strings.HasPrefix(name, sandboxPrefix) {
		return p.refuse("%q is a selftest sandbox name (%s…) and `create` allocates PRODUCTION blocks; "+
			"`ragstack-ctl selftest` creates these through `create-sandbox`, which allocates out of %d–%d",
			name, sandboxPrefix, paths.SelftestBase, paths.SelftestEnd)
	}
	return planCreateWith(p, args, productionBlock)
}

// planCreateWith is `create` with its allocator supplied: the whole verb but
// for the one line that decides which port block the new tenant gets.
func planCreateWith(p *planner, args map[string]any, allocate blockAllocator) error {
	f := p.oc.Fleet
	if f == nil {
		return fmt.Errorf("%w: create needs a registry snapshot", jobs.ErrValidation)
	}
	name := argStringOf(args, "name")
	if err := paths.ValidateName(name); err != nil {
		return fmt.Errorf("%w: %s", jobs.ErrValidation, err.Error())
	}
	if _, exists := f.Tenants[name]; exists {
		return p.refuse("%s already exists", name)
	}
	artifactID := argStringOf(args, "artifact_id")
	artifact, ok := f.Artifacts[artifactID]
	if !ok {
		return p.refuse("artifact %q is not prepared: `create` takes an artifact ID — a worktree at a reviewed SHA with "+
			"a built UI — never a git ref or a path (`ragstack-ctl fleet artifact prepare --tag …`)", artifactID)
	}
	provider := argStringOf(args, "identity_provider")
	if provider == "" {
		provider = "none"
	}
	subjects := argStringsOf(args, "admin_subjects")
	if provider == "none" && len(subjects) > 0 {
		return p.refuse("admin_subjects were given but identity_provider is `none`: a tenant with no identity provider " +
			"has no bearer subjects to admit")
	}
	for _, s := range subjects {
		if issuer := s[:indexColon(s)]; issuer != provider {
			return p.refuse("admin subject %q is issued by %q, but identity_provider is %q", s, issuer, provider)
		}
	}
	// `settings` is public keys only — the same rule the env API enforces,
	// applied at the one moment a whole file is composed from a request.
	set := map[string]string{}
	if tf := argStringOf(args, "template_from"); tf != "" {
		from, ok := f.Tenants[tf]
		if !ok {
			return p.refuse("template_from names %q, which is not a tenant", tf)
		}
		// PUBLIC settings and nothing else (create_request.json): a template
		// that copied ports, paths or store URLs would make the new tenant
		// point at the old one's data.
		for _, k := range sortedStringKeys(from.Settings) {
			if settings.Classify(k) == settings.Public {
				set[k] = from.Settings[k]
			}
		}
	}
	if raw, ok := args["settings"].(map[string]any); ok {
		for _, k := range sortedKeys(raw) {
			if settings.Classify(k) != settings.Public {
				return p.refuse("settings.%s is %s-class; `create` accepts public settings only", k,
					settings.Classify(k).String())
			}
			v, ok := raw[k].(string)
			if !ok {
				return fmt.Errorf("%w: settings.%s must be a string", jobs.ErrValidation, k)
			}
			set[k] = v
		}
	}
	keys, err := createKeys(args)
	if err != nil {
		return err
	}

	storeKind := paths.StoreSQLite
	switch argStringOf(args, "postgres") {
	case "", "sqlite":
	case "local":
		storeKind = paths.StorePostgresLocal
	default:
		// Unreachable: the args schema's enum is {sqlite, local}. Stated anyway,
		// because a third kind added to the schema and forgotten here would
		// otherwise provision a sqlite tenant that the request called something
		// else.
		return fmt.Errorf("%w: create.postgres must be sqlite or local", jobs.ErrValidation)
	}

	index, base, err := allocate(f)
	if err != nil {
		return fmt.Errorf("%w: %s", jobs.ErrRefused, err.Error())
	}
	esHeap := argStringOf(args, "es_heap")
	if esHeap == "" {
		esHeap = defaultESHeap
	}
	spec := createSpec{
		Name: name, ArtifactID: artifactID, Artifact: artifact, Index: index, Base: base,
		StoreKind: storeKind, ESHeap: esHeap, Provider: provider, Subjects: subjects,
		Keys: keys, Accounts: serviceAccountArgs(args), Settings: set,
		UIMode: argStringOf(args, "ui_mode"),
		Start:  boolArgOrDefault(args, "start", true), Gateway: boolArgOrDefault(args, "gateway", true),
		Mirror: p.op.deps.Mirror, Owner: p.op.deps.owner(),
	}
	if !registry.KnownOwner(spec.Owner) {
		return p.refuse("the ctl runs as %q, which is not an owner the registry admits (%v); a tenant it created "+
			"would be a row no later load accepts", spec.Owner, registry.Owners())
	}
	spec.Tenant = prospectiveTenant(p.oc.Roots, f, spec)
	return planCreateSteps(p, spec)
}

// defaultESHeap is create_request.json's default.
const defaultESHeap = "1g"

// planCreateSteps is the create step builder, and the SEAM `restore --as`
// reuses: it calls this with Start:false and Gateway:false to lay a fresh
// tenant down before recovering data into it, so a restored tenant has the
// same directories, the same units, the same env layout and the same registry
// row as one `create` made. Two builders would be two definitions of what a
// tenant is, and the second one would be wrong within a release.
func planCreateSteps(p *planner, spec createSpec) error {
	p.need(model.LockRegistry, model.LockManifest, model.LockTenant, model.LockGateway)
	t := spec.Tenant
	name := spec.Name
	p.t, p.tenant = t, name
	p.tpaths = paths.TenantPaths(p.oc.Roots, name, name)
	tp := p.tpaths

	// ---- rendered at PLAN time, so the dry run shows the real thing --------
	//
	// The env file is rendered TWICE with the same template: once here with
	// the placeholder secrets, for the preview, and once in the secrets step
	// with the minted ones. The preview is of the PUBLIC half only, because
	// that is the half that will actually be written to tenant.env — showing
	// the whole render would show an operator a file the job is not going to
	// write.
	envPreview, _, err := composeEnv(spec, tp, render.Secrets{
		KeyUser: previewSecret, KeyAdmin: previewSecret, PGPassword: previewSecret,
	})
	if err != nil {
		return p.refuse("%s's tenant.env cannot be rendered as requested: %v", name, err)
	}
	units, err := render.Units(t, render.UnitConfig{
		RagRoot: p.oc.Roots.RagRoot, CtlStateDir: p.oc.Roots.CtlStateDir,
		// Empty is RagRoot (render's own default). It is non-empty only for a
		// run against a sandbox root, where the paths move and the mount does
		// not — see Deps.MountPoint.
		MountPoint: p.op.deps.MountPoint,
	})
	if err != nil {
		return p.refuse("%s's units cannot be rendered as requested: %v", name, err)
	}
	provision := renderProvision(spec)
	target, _, _, _, _, _ := render.UnitNames(name)
	dirs := append(tp.ProvisionDirsFor(spec.StoreKind), tp.LogsDir, filepath.Dir(tp.UIDist))

	// minted is filled by the secrets step and read by everything after it:
	// the service-account calls authenticate with the bootstrap admin key, the
	// registry row records fingerprints, and Planned.Secrets hands the values
	// to the operator once. Nothing else ever holds them.
	var minted []mintedKey
	var envSHA, secretsSHA string
	p.secrets = func() []model.Secret {
		// nil, not an empty slice: a rollback clears `minted`, and the engine
		// must then seal NO envelope at all rather than an empty one an
		// operator would go looking for.
		if len(minted) == 0 {
			return nil
		}
		out := make([]model.Secret, 0, len(minted))
		for _, k := range minted {
			out = append(out, model.Secret{ID: k.Label, Label: k.Label, Role: k.Role, Value: k.Value})
		}
		return out
	}

	// ---- 1. registry: allocate ------------------------------------------
	manifest := string(registry.ProjectManifest(p.oc.Fleet)) + fmt.Sprintf("%s\t%d\t%d\n", name, spec.Index, spec.Base)
	p.add(step{
		Kind: "registry", Title: fmt.Sprintf("allocate %s: index %d, ports %d–%d", name, spec.Index, spec.Base,
			spec.Base+p.oc.Fleet.PortStride-1),
		Targets:    []string{name, strconv.Itoa(spec.Index), strconv.Itoa(spec.Base)},
		WouldWrite: []model.WouldWrite{p.preview(p.oc.Roots.Manifest(), "0660", []byte(manifest))},
		Warnings: []string{"the allocation is tombstone-aware: the index is one past the highest index any tenant or " +
			"tombstone has ever held, so a decommissioned block is never reused"},
		Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			save := p.op.deps.SaveFleet
			if save == nil {
				return "", fmt.Errorf("%w: the registry writer is not wired", jobs.ErrRefused)
			}
			// The row goes into the fleet the ENGINE loaded under the locks. The
			// plan hash has already proved that fleet equals the plan-time
			// snapshot, so the block this row claims is the block the plan named.
			f := sc.Ops.Fleet
			if _, exists := f.Tenants[name]; exists {
				return "", fmt.Errorf("%w: %s appeared in the registry between the plan and the lock", jobs.ErrRefused, name)
			}
			// The ROW is the external ID, recorded before the write: a crash
			// between the two leaves a record naming the allocation to undo.
			if err := sc.Checkpoint("tenant:" + name); err != nil {
				return "", err
			}
			row := cloneTenant(t)
			row.AdoptedAt = registry.NullString(p.stampRFC3339(sc))
			f.Tenants[name] = row
			f.DisplayOrder = append(f.DisplayOrder, name)
			if err := save(f); err != nil {
				delete(f.Tenants, name)
				f.DisplayOrder = f.DisplayOrder[:len(f.DisplayOrder)-1]
				return "", err
			}
			sc.Logf("%s allocated index %d, ports %d–%d (registry generation %d)",
				name, spec.Index, spec.Base, spec.Base+f.PortStride-1, f.Generation)
			return fmt.Sprintf("%s index %d base %d", name, spec.Index, spec.Base), nil
		},
		Rollback: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			// No tombstone: nothing ran under this allocation, so the index is
			// not spent. A tombstone here would burn a port block for every
			// create that failed on its second step.
			save := p.op.deps.SaveFleet
			if save == nil {
				return "nothing was written", nil
			}
			f := sc.Ops.Fleet
			delete(f.Tenants, name)
			f.DisplayOrder = withoutString(f.DisplayOrder, name)
			return "removed the registry row for " + name, save(f)
		},
		Reconcile: func(_ context.Context, sc *jobs.StepContext) (jobs.Reconciliation, error) {
			if _, ok := sc.Ops.Fleet.Tenants[name]; ok {
				return jobs.ReconcileDone, nil
			}
			return jobs.ReconcileRedo, nil
		},
	})

	// ---- 2. fs: the tenant tree -----------------------------------------
	//
	// The step REFUSES a data dir that already exists and holds anything, and
	// its rollback RENAMES the tree it made rather than deleting it. The two
	// halves are one rule: a job that fails after this step leaves whatever the
	// stores had written under `<data_dir>.failed-<stamp>`, so the NEXT create
	// or restore of the same name finds an empty path and the operator finds
	// the evidence. Without the refusal the second attempt would lay a fresh
	// tenant down on top of the first one's store files; without the rename it
	// would be the ctl that deleted them.
	p.addFor("files", step{
		Kind: "fs", Title: "create the tenant directories (2770, setgid)", Targets: dirs,
		Warnings: []string{"refuses when " + tp.DataDir + " already exists and is not empty; a failure after this " +
			"step RENAMES the tree to " + tp.DataDir + ".failed-<ts> rather than deleting it"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			files := sc.Ops.Drivers.Files()
			// An EMPTY directory is fine: the deployment's own layout may have
			// been laid down by an operator, and a rolled-back create that had
			// nothing to leave behind leaves an empty tree by design.
			switch ents, err := files.ReadDir(ctx, tp.DataDir); {
			case err == nil && len(ents) > 0:
				return "", fmt.Errorf("%w: %s already exists and is not empty (%d entries): remove or rename it "+
					"first — a previous create or restore of this name left it, and laying a fresh tenant down on "+
					"top of another one's store files is how two tenants come to share a data directory",
					jobs.ErrRefused, tp.DataDir, len(ents))
			case err != nil && !isNotExist(err):
				return "", fmt.Errorf("checking %s: %w", tp.DataDir, err)
			}
			// The directory is the external ID, recorded before it is made: the
			// rollback renames a tree only when this record says the job is the
			// one that created it.
			if err := sc.Checkpoint("dir:" + tp.DataDir); err != nil {
				return "", err
			}
			// The data dir first, so the setgid bit is set on the parent before
			// anything is created under it — a subdirectory made before its
			// parent carried setgid inherits the creator's primary group, and on
			// this host that is a group with 1869 members.
			for _, d := range append([]string{tp.DataDir}, dirs...) {
				if err := files.MkdirAll(ctx, d, dirMode); err != nil {
					return "", err
				}
			}
			return fmt.Sprintf("%d directories under %s", len(dirs)+1, tp.DataDir), nil
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			if len(sc.Step.ExternalIDs) == 0 {
				return "nothing was created", nil
			}
			// A RENAME, never a delete. The ctl has no recursive delete and a
			// rollback is not the place to acquire one: what is under this tree
			// by the time the job fails is whatever qdrant, elasticsearch and
			// postgres wrote into it, and the one operation that is always safe
			// on somebody's data is moving it aside.
			failed := tp.DataDir + ".failed-" + p.stampOf(sc)
			if err := sc.Ops.Drivers.Files().Rename(ctx, tp.DataDir, failed); err != nil {
				if isNotExist(err) {
					return "nothing was created", nil
				}
				return "", err
			}
			sc.Logf("the tenant tree was renamed, not deleted: %s holds whatever the stores wrote. "+
				"Nothing runs from it, it has no registry row and no units; remove it by hand", failed)
			return "renamed the tenant tree aside: " + failed, nil
		},
	})

	// ---- 3. secrets.env (write-once) ------------------------------------
	p.addFor("files", step{
		Kind: "fs", Title: "mint the keys and write secrets.env (write-once, 0640)",
		Targets:    []string{tp.SecretsEnv},
		WouldWrite: []model.WouldWrite{secretWrite(tp.SecretsEnv, "0640")},
		Warnings: []string{"the keys are minted when the job runs and delivered ONCE through the job's secrets " +
			"envelope; the registry keeps fingerprints"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			files := sc.Ops.Drivers.Files()
			// WRITE-ONCE is a policy of this op, not of the Files driver.
			//
			// The driver's job is to write a file atomically with the right mode;
			// a driver that refused to overwrite would make `env normalize`, the
			// `.bak-` rewrites and the restore path all impossible, and a driver
			// with a "no really, overwrite it" flag is a driver whose guarantee
			// is whatever the caller passed. So the rule lives here, where the
			// reason is: this file holds the ONLY copy of credentials that are
			// never shown again, and overwriting it silently destroys them.
			if _, err := files.ReadFile(ctx, tp.SecretsEnv); err == nil {
				return "", fmt.Errorf("%w: %s already exists. Its keys were delivered once and are not recoverable, "+
					"so `create` never overwrites it — move it aside deliberately if this tenant is being rebuilt",
					jobs.ErrRefused, tp.SecretsEnv)
			} else if !isNotExist(err) {
				return "", fmt.Errorf("checking %s: %w", tp.SecretsEnv, err)
			}
			mk, err := mintKeys(spec, p.stampRFC3339(sc), sc.Job.Principal)
			if err != nil {
				return "", err
			}
			pgPassword := ""
			if spec.postgresLocal() {
				if pgPassword, err = mintToken(); err != nil {
					return "", err
				}
			}
			pub, sec, err := composeEnv(spec, tp, render.Secrets{
				KeyUser: userKeyValue(mk), KeyAdmin: adminKeyValue(mk), PGPassword: pgPassword,
			})
			if err != nil {
				return "", err
			}
			// The full ledger replaces the template's two-key one: `create` mints
			// whatever the request asked for plus the bootstrap admin, and the
			// three env keys are edited as ONE unit (creds.go) or the tenant ends
			// up with a key that has the default role.
			if err := setLedger(sec, spec.Name, mk); err != nil {
				return "", err
			}
			if spec.postgresLocal() {
				if err := sec.Set("TENANT_PG_PASSWORD", pgPassword); err != nil {
					return "", err
				}
			}
			body := sec.Render()
			if err := sc.Checkpoint("file:" + tp.SecretsEnv); err != nil {
				return "", err
			}
			if err := files.WriteAtomic(ctx, tp.SecretsEnv, body, 0o640); err != nil {
				return "", err
			}
			// Assigned only after the write succeeded: Planned.Secrets is called
			// on success, and a job that failed here must deliver no envelope for
			// keys that are in no file.
			minted, secretsSHA = mk, sha256Hex(body)
			envSHA = sha256Hex(pub.Render())
			// Fingerprints only. Planned.Result closes over this same map, so the
			// job's result names every key it created without carrying one.
			p.result["keys"] = resultKeys(mk)
			sc.Logf("minted %d key(s): %s", len(mk), labelsOf(mk))
			return fmt.Sprintf("%s (%d keys)", tp.SecretsEnv, len(mk)), nil
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			minted = nil
			return "removed " + tp.SecretsEnv, sc.Ops.Drivers.Files().Remove(ctx, tp.SecretsEnv)
		},
	})

	// ---- 3b. tenant.env (the public half) --------------------------------
	p.addFor("files", step{
		Kind: "envfile", Title: "write tenant.env (secret-class lines live in secrets.env)",
		Targets:    []string{tp.TenantEnv},
		WouldWrite: []model.WouldWrite{p.preview(tp.TenantEnv, "0640", envPreview.Render())},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			// Re-rendered from the MINTED values rather than from the preview:
			// the two halves are one render, split by settings.Classify, so a key
			// that changed class between them cannot end up in both files or in
			// neither.
			pub, _, err := composeEnv(spec, tp, render.Secrets{
				KeyUser: userKeyValue(minted), KeyAdmin: adminKeyValue(minted), PGPassword: "x",
			})
			if err != nil {
				return "", err
			}
			body := pub.Render()
			envSHA = sha256Hex(body)
			if err := sc.Checkpoint("file:" + tp.TenantEnv); err != nil {
				return "", err
			}
			return tp.TenantEnv, sc.Ops.Drivers.Files().WriteAtomic(ctx, tp.TenantEnv, body, 0o640)
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return "removed " + tp.TenantEnv, sc.Ops.Drivers.Files().Remove(ctx, tp.TenantEnv)
		},
	})

	// ---- 4. provision.env -------------------------------------------------
	p.addFor("files", step{
		Kind: "envfile", Title: "write provision.env (the provisioning choices adopt reads back)",
		Targets:    []string{tp.ProvisionEnv},
		WouldWrite: []model.WouldWrite{p.preview(tp.ProvisionEnv, "0640", provision)},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			if err := sc.Checkpoint("file:" + tp.ProvisionEnv); err != nil {
				return "", err
			}
			return tp.ProvisionEnv, sc.Ops.Drivers.Files().WriteAtomic(ctx, tp.ProvisionEnv, provision, 0o640)
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return "removed " + tp.ProvisionEnv, sc.Ops.Drivers.Files().Remove(ctx, tp.ProvisionEnv)
		},
	})

	// ---- 5. git: the tenant's own checkout --------------------------------
	p.addFor("git", step{
		Kind: "git", Title: "check the worktree out at artifact " + spec.ArtifactID + "'s commit",
		Targets: []string{t.Worktree, spec.Artifact.SHA},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			if spec.Mirror == "" {
				return "", fmt.Errorf("%w: no mirror is configured, so there is nothing to check %s out of "+
					"(set CTL_MIRROR; creating the mirror is a deploy-time item)", jobs.ErrRefused, t.Worktree)
			}
			if err := sc.Checkpoint("worktree:" + t.Worktree); err != nil {
				return "", err
			}
			if err := sc.Ops.Drivers.Git().AddWorktree(ctx, spec.Mirror, spec.Artifact.SHA, t.Worktree); err != nil {
				return "", err
			}
			return t.Worktree + " @ " + spec.Artifact.SHA[:12], nil
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return "removed " + t.Worktree, sc.Ops.Drivers.Git().RemoveWorktree(ctx, spec.Mirror, t.Worktree)
		},
	})

	// ---- 6. build: the tenant's UI ----------------------------------------
	//
	// Built from the ARTIFACT's worktree (which has node_modules) into the
	// TENANT's data dir. Per tenant rather than once per artifact because vite
	// bakes the gateway base path into the bundle: /ragstack/<name>/ui/ is in
	// the asset URLs, so one shared dist could serve exactly one tenant.
	if t.UI.Mode == registry.UIModeExternal {
		p.skip("apptainer", "skip the UI build",
			"ui_mode is `external`: the gateway proxies to a server somebody else runs, so there is nothing to build",
			t.DataDir+"/ui/dist")
	} else {
		p.addFor("build", step{
			Kind: "apptainer", Title: "build the tenant UI from the artifact's node_modules",
			Targets: []string{tp.UIDist},
			Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
				if err := sc.Ops.Drivers.Build().UI(ctx, spec.Artifact.Worktree, t.UI.Base, tp.UIDist); err != nil {
					return "", err
				}
				return tp.UIDist, nil
			},
		})
	}

	// ---- 7. units ---------------------------------------------------------
	p.addUnitsWrite(units)
	for _, unit := range sortedUnitNames(units) {
		unit := unit
		path := filepath.Join(p.oc.Roots.UnitsDir(), unit)
		p.addFor("systemd", step{
			Kind: "systemd", Title: "systemctl --user link " + unit, Targets: []string{path},
			WouldRun: []model.WouldRun{{Argv: []string{"/usr/bin/systemctl", "--user", "link", path}}},
			Warnings: []string{"the ctl writes units into its own config tree, which the user manager does not " +
				"search; linking is what makes a rendered unit a real one until the SYSTEMD_UNIT_PATH drop-in exists"},
			Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
				if err := sc.Checkpoint("unit:" + unit); err != nil {
					return "", err
				}
				return "linked " + unit, sc.Ops.Drivers.Systemd().Link(ctx, path)
			},
			// The link is undone by `disable`, which is what removes the symlink
			// `link` made (the unit was never enabled, so that is all it
			// removes). Without this rollback coconut's first failed sandbox
			// create left three dangling links in the user manager, listed as
			// loaded/failed units of a tenant that no longer existed. The
			// failed state is cleared too, so a later create of the same name
			// does not inherit a start-limit counter; "not loaded" there is not
			// an error, it is the state this rollback wants.
			Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
				// A refusal here means the manager already forgot the unit — the
				// enable step's own rollback (disable, in reverse order before
				// this one) does that for the target — and "already gone" is
				// this rollback's goal, not its failure.
				if err := sc.Ops.Drivers.Systemd().Disable(ctx, unit); err != nil {
					sc.Logf("disable %s: %v (treated as already unlinked)", unit, err)
					return "already unlinked " + unit, nil
				}
				_ = sc.Ops.Drivers.Systemd().ResetFailed(ctx, unit)
				return "unlinked " + unit, nil
			},
		})
	}
	p.addFor("systemd", step{
		Kind: "systemd", Title: "systemctl --user daemon-reload",
		WouldRun: []model.WouldRun{{Argv: []string{"/usr/bin/systemctl", "--user", "daemon-reload"}}},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return "daemon-reload", sc.Ops.Drivers.Systemd().DaemonReload(ctx)
		},
	})
	if t.DesiredBoot == "enabled" {
		// The TARGET is what is enabled, and only the target: the service units
		// carry no [Install] section (they are PartOf the target and pulled in by
		// its Wants), so `systemctl enable` on one of them is an error, not a
		// stronger guarantee.
		p.addFor("systemd", step{
			Kind: "systemd", Title: "enable " + target + " (desired_boot)", Targets: []string{target},
			WouldRun: []model.WouldRun{{Argv: []string{"/usr/bin/systemctl", "--user", "enable", target}}},
			Run:      unitRun("enable", target),
			Rollback: unitRun("disable", target),
		})
	}

	// ---- 8. start + readiness ---------------------------------------------
	if spec.Start {
		p.addFor("systemd", step{
			Kind: "systemd", Title: "start " + target, Targets: []string{target},
			WouldRun: []model.WouldRun{{Argv: []string{"/usr/bin/systemctl", "--user", "start", target}}},
			Run:      unitRun("start", target),
			Rollback: unitRun("stop", target),
			Reconcile: func(ctx context.Context, sc *jobs.StepContext) (jobs.Reconciliation, error) {
				active, err := sc.Ops.Drivers.Systemd().IsActive(ctx, target)
				if err != nil {
					return jobs.ReconcileStuck, err
				}
				if active {
					return jobs.ReconcileDone, nil
				}
				return jobs.ReconcileRedo, nil
			},
		})
		p.addReadyGate(spec)
	} else {
		p.skip("systemd", "skip the start", "start is false: the tenant is provisioned and enabled but not running",
			target)
	}

	// ---- 9. service accounts + post-checks --------------------------------
	origin := fmt.Sprintf("http://127.0.0.1:%d", t.Ports.API)
	if spec.Start {
		for _, sa := range spec.Accounts {
			sa := sa
			p.addFor("tenantapi", step{
				Kind: "tenantapi", Title: "register the service account " + sa.Subject,
				Targets: []string{sa.Subject, origin},
				Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
					if err := sc.Checkpoint("sa:create:" + sa.Subject); err != nil {
						return "", err
					}
					// The bootstrap admin key, held in this job's memory since the
					// secrets step. It is never logged, never checkpointed, never
					// a step target.
					return "created " + sa.Subject, sc.Ops.Drivers.TenantAPI().ServiceAccount(
						ctx, origin, adminKeyValue(minted), sa.Subject, sa.Role, sa.Purpose, "create")
				},
				Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
					return "disabled " + sa.Subject, sc.Ops.Drivers.TenantAPI().ServiceAccount(
						ctx, origin, adminKeyValue(minted), sa.Subject, sa.Role, sa.Purpose, "disable")
				},
			})
		}
		p.addFor("tenantapi", step{
			Kind: "probe", Title: "post-checks: /health, /v1/health/deep and /v1/version", Targets: []string{origin},
			Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
				api := sc.Ops.Drivers.TenantAPI()
				if err := api.Health(ctx, origin); err != nil {
					return "", err
				}
				// The tenant's OWN verdict on every store it was configured with.
				// A port that accepts a connection proves uvicorn started; this
				// proves the tenant can reach the qdrant, the ES and the
				// relational store this job just gave it.
				if err := api.DeepHealth(ctx, origin, adminKeyValue(minted)); err != nil {
					return "", err
				}
				v, err := api.Version(ctx, origin, adminKeyValue(minted))
				if err != nil {
					return "", err
				}
				got, _ := v["commit"].(string)
				if got == "" {
					got, _ = v["version"].(string)
				}
				p.result["version"] = v
				sc.Logf("health ok, deep health ok, version %v", got)
				return "health, deep health and version ok", nil
			},
		})
	} else if len(spec.Accounts) > 0 {
		p.skip("tenantapi", "skip the service accounts",
			"start is false: the tenant API is not running, and a service account is created THROUGH it — create them "+
				"with `ragstack-ctl sa create` once the tenant is started", origin)
	}

	// ---- 10. registry: the finished row ------------------------------------
	finalState := "active"
	if !spec.Start {
		finalState = "provisioned"
	}
	verb := spec.Verb
	if verb == "" {
		verb = "create"
	}
	p.addRegistryEffect(verb, fmt.Sprintf("record %s as %s, with the key ledger and the file hashes", name, finalState),
		func(row *registry.Tenant) {
			row.State = finalState
			row.EnvFileSHA256 = envSHA
			row.SecretsFileSHA256 = registry.NullString(secretsSHA)
			row.Keys = ledgerRows(minted, name)
			row.SecretRefs = secretRefsFor(spec)
			if spec.Start {
				row.ServiceAccounts = serviceAccountRows(spec, p.stampRFC3339Now())
			}
		})

	// ---- 11. gateway ------------------------------------------------------
	//
	// AFTER the row is active: the gateway renders routes for active tenants
	// only, so a generation published before the registry step would carry
	// no route for the tenant it was published for.
	if spec.Gateway {
		p.add(step{
			Kind: "nginx", Title: "publish the gateway generation including " + name, Targets: []string{name},
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
	} else {
		p.skip("nginx", "skip the gateway publish", "gateway is false: the tenant is not routed until "+
			"`ragstack-ctl gateway apply` runs", name)
	}

	p.result["name"] = name
	p.result["index"] = spec.Index
	p.result["ports"] = t.Ports
	p.result["artifact_id"] = spec.ArtifactID
	p.result["postgres"] = t.Stores.Postgres.Kind
	p.result["keys"] = []any{}
	p.warn("rollback is bounded to the paths this job created; nothing outside them is touched")
	if spec.postgresLocal() {
		p.warn("postgres `local`: the instance's role and database are created by the image's entrypoint on its FIRST " +
			"start, from TENANT_PG_PASSWORD in secrets.env — the password is never a literal in a unit file")
	}
	return nil
}

// addReadyGate is create's inline `wait-ready`: the stores this tenant owns,
// then the API port. It is the same gate the api unit's ExecStartPre runs, done
// here because a create that returned before the tenant answered would report
// success for a tenant that never came up.
func (p *planner) addReadyGate(spec createSpec) {
	t := spec.Tenant
	type readyCheck struct {
		what  string
		probe func(context.Context, *jobs.StepContext) error
	}
	checks := []readyCheck{
		{"qdrant", func(c context.Context, sc *jobs.StepContext) error {
			return sc.Ops.Drivers.Qdrant().Ready(c, t.Stores.Qdrant.URL)
		}},
		{"elasticsearch", func(c context.Context, sc *jobs.StepContext) error {
			return sc.Ops.Drivers.Elasticsearch().Ready(c, t.Stores.Elasticsearch.URL)
		}},
	}
	if spec.postgresLocal() {
		pgSpec := jobs.PostgresSpec{
			SIF: string(t.Stores.Postgres.SIF), RunDir: p.tpaths.PostgresRun, DB: spec.Name, User: spec.Name,
			Port: pgPortOf(t),
		}
		checks = append(checks, readyCheck{"postgres", func(c context.Context, sc *jobs.StepContext) error {
			return sc.Ops.Drivers.Postgres().Ready(c, pgSpec)
		}})
	}
	checks = append(checks, readyCheck{"api", func(c context.Context, sc *jobs.StepContext) error {
		listening, err := sc.Ops.Drivers.Proc().Listening(c, t.Ports.API)
		if err != nil {
			return err
		}
		if !listening {
			return fmt.Errorf("nothing is listening on %d", t.Ports.API)
		}
		return nil
	}})
	p.addFor("proc", step{
		Kind: "probe", Title: fmt.Sprintf("wait for %s's stores and API to answer (up to %s)", spec.Name, createReadyTimeout),
		Targets: []string{t.Stores.Qdrant.URL, t.Stores.Elasticsearch.URL, strconv.Itoa(t.Ports.API)},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			// The clock here is the WALL clock and the sleep is a real sleep,
			// deliberately: this is a run half, not a plan, and what it waits for
			// is Elasticsearch opening its segments. An injected clock would make
			// the gate return before the store was up — which is the bug the gate
			// exists to prevent.
			deadline := time.Now().Add(createReadyTimeout)
			for _, c := range checks {
				for {
					err := c.probe(ctx, sc)
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
			}
			return "every store and the API answered", nil
		},
	})
}

// ---------------------------------------------------------------- env files

// composeEnv renders tenant.env from the shared template and splits it into
// the two files the managed layout has: the PUBLIC half (tenant.env) and the
// SECRET half (secrets.env). One render, one split by settings.Classify, so
// there is exactly one statement of which key is a credential.
func composeEnv(spec createSpec, tp paths.Tenant, secrets render.Secrets) (public, secret *envfile.File, err error) {
	o := render.EnvOptions{Secrets: secrets, StoreKind: spec.StoreKind}
	if spec.postgresLocal() {
		o.PGHost, o.PGPort = "localhost", strconv.Itoa(spec.Tenant.Ports.PG)
	}
	body, err := render.TenantEnv(spec.Tenant, o)
	if err != nil {
		return nil, nil, err
	}
	f, err := envfile.Parse(body)
	if err != nil {
		return nil, nil, err
	}
	// The template hard-codes IDENTITY_PROVIDER=none; a tenant created with a
	// provider needs the real value and its admin subjects, and both are PUBLIC
	// (a subject is an identity, not a credential).
	if err := f.Set("IDENTITY_PROVIDER", spec.Provider); err != nil {
		return nil, nil, err
	}
	if len(spec.Subjects) > 0 {
		b, mErr := json.Marshal(spec.Subjects)
		if mErr != nil {
			return nil, nil, mErr
		}
		if err := f.Set("ADMIN_SUBJECTS", string(b)); err != nil {
			return nil, nil, err
		}
	}
	// The operator's settings last: they are the request's own overrides, and
	// they are public-class by construction (planCreate refuses anything else).
	for _, k := range sortedStringKeys(spec.Settings) {
		if err := f.Set(k, spec.Settings[k]); err != nil {
			return nil, nil, err
		}
	}
	public, secret = envfile.SplitSecrets(f, settings.Classify)
	return public, secret, nil
}

// setLedger writes the API_KEYS / API_KEY_TENANTS / API_KEY_ROLES triple for
// every minted key. The three are one unit: a key in API_KEYS with no row in
// API_KEY_ROLES is a key with the default role, which is not what anybody asked
// for.
func setLedger(f *envfile.File, tenant string, keys []mintedKey) error {
	values := make([]string, 0, len(keys))
	tenants := map[string]string{}
	roles := map[string]string{}
	for _, k := range keys {
		values = append(values, k.Value)
		tenants[k.Value], roles[k.Value] = tenant, k.Role
	}
	if err := setJSON(f, keyAPIKeys, values); err != nil {
		return err
	}
	if err := setJSON(f, keyAPITenant, tenants); err != nil {
		return err
	}
	return setJSON(f, keyAPIRoles, roles)
}

// renderProvision is new-tenant.sh's render_provision, byte-compatible in the
// keys it writes: `adopt` reads exactly these four back to reconstruct the
// relational-store row, so a tenant the ctl created and a tenant the script
// created must look the same to it.
func renderProvision(spec createSpec) []byte {
	host, port := "", ""
	if spec.postgresLocal() {
		host, port = "localhost", strconv.Itoa(spec.Tenant.Ports.PG)
	}
	return []byte(fmt.Sprintf(`# provision.env — tenant '%s' (written by ragstack-ctl)
# Persists provisioning choices across re-runs; flags on a later run update it.
TENANT_ES_HEAP=%s
TENANT_STORE_KIND=%s
TENANT_PG_HOST=%s
TENANT_PG_PORT=%s
`, spec.Name, spec.ESHeap, spec.StoreKind, host, port))
}

// ---------------------------------------------------------------- keys

// mintedKey is one key this job created. The VALUE lives here and in the
// delivery envelope and nowhere else — never in a plan, a log, a step target,
// a checkpoint, a result or the registry.
type mintedKey struct {
	Label     string
	Role      string
	Value     string
	CreatedAt string
	CreatedBy string
}

// createKeys is the `keys` argument plus the mandatory bootstrap admin.
func createKeys(args map[string]any) ([]keySpec, error) {
	items, _ := toSlice(args["keys"])
	seen := map[string]bool{}
	var out []keySpec
	for i, it := range items {
		m, ok := it.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("%w: create.keys[%d] must be an object", jobs.ErrValidation, i)
		}
		label, _ := m["label"].(string)
		role, _ := m["role"].(string)
		if !reKeyLabel.MatchString(label) {
			return nil, fmt.Errorf("%w: create.keys[%d].label %q does not match %s", jobs.ErrValidation, i, label, patLabel)
		}
		if role != "admin" && role != "user" {
			return nil, fmt.Errorf("%w: create.keys[%d].role must be admin or user, got %q", jobs.ErrValidation, i, role)
		}
		if seen[label] {
			return nil, fmt.Errorf("%w: create.keys names %q twice; a ledger id identifies exactly one key",
				jobs.ErrValidation, label)
		}
		seen[label] = true
		out = append(out, keySpec{Label: label, Role: role})
	}
	if !seen[bootstrapAdminLabel] {
		// Prepended, so it is the first admin key and adminKeyValue finds it
		// whatever else the request asked for.
		out = append([]keySpec{{Label: bootstrapAdminLabel, Role: "admin"}}, out...)
	}
	return out, nil
}

// mintKeys creates the values. `token_hex(32)` — 32 random bytes as 64 hex
// characters — which is what the tenant API's own key format is.
func mintKeys(spec createSpec, at, by string) ([]mintedKey, error) {
	out := make([]mintedKey, 0, len(spec.Keys))
	for _, k := range spec.Keys {
		v, err := mintToken()
		if err != nil {
			return nil, err
		}
		out = append(out, mintedKey{Label: k.Label, Role: k.Role, Value: v, CreatedAt: at, CreatedBy: by})
	}
	return out, nil
}

// adminKeyValue is the credential the ctl authenticates with for the rest of
// the job: the bootstrap admin when it is there, else the first admin key.
func adminKeyValue(keys []mintedKey) string {
	for _, k := range keys {
		if k.Label == bootstrapAdminLabel {
			return k.Value
		}
	}
	for _, k := range keys {
		if k.Role == "admin" {
			return k.Value
		}
	}
	return ""
}

// userKeyValue is a `user` key for the template's API_KEYS line; the ledger is
// rewritten from the full set immediately afterwards, so this only decides
// which value the template happens to carry first.
func userKeyValue(keys []mintedKey) string {
	for _, k := range keys {
		if k.Role == "user" {
			return k.Value
		}
	}
	return adminKeyValue(keys)
}

func labelsOf(keys []mintedKey) string {
	out := make([]string, 0, len(keys))
	for _, k := range keys {
		out = append(out, k.Label)
	}
	return strings.Join(out, ", ")
}

// ledgerRows is the registry's key ledger: fingerprints, never values.
func ledgerRows(keys []mintedKey, tenant string) []registry.Key {
	out := make([]registry.Key, 0, len(keys))
	for _, k := range keys {
		out = append(out, registry.Key{
			ID: k.Label, Label: k.Label, Role: k.Role, TenantString: tenant,
			Fingerprint: fingerprint(k.Value), CreatedAt: k.CreatedAt, CreatedBy: k.CreatedBy,
			Effective: true,
		})
	}
	return out
}

// resultKeys is what the job's RESULT carries: the ledger rows, with
// fingerprints. The values go through the secrets envelope and nowhere else.
func resultKeys(keys []mintedKey) []any {
	out := make([]any, 0, len(keys))
	for _, k := range keys {
		out = append(out, map[string]any{
			"id": k.Label, "label": k.Label, "role": k.Role, "fingerprint": fingerprint(k.Value),
		})
	}
	return out
}

// secretRefsFor names the secret-class keys and the file they live in — never
// a value. It is what `doctor` and the env API read to know a credential
// exists without being able to read it.
func secretRefsFor(spec createSpec) []registry.SecretRef {
	refs := []registry.SecretRef{
		{Key: keyAPIKeys, File: "secrets.env"},
		{Key: keyAPITenant, File: "secrets.env"},
		{Key: keyAPIRoles, File: "secrets.env"},
	}
	if spec.postgresLocal() {
		refs = append(refs,
			registry.SecretRef{Key: "TENANT_PG_PASSWORD", File: "secrets.env"},
			registry.SecretRef{Key: "USER_STORE_DSN", File: "secrets.env"},
			registry.SecretRef{Key: "POSTGRES_DSN", File: "secrets.env"},
			registry.SecretRef{Key: "COLLECTION_STORE_DSN", File: "secrets.env"},
		)
	}
	return refs
}

func serviceAccountRows(spec createSpec, at string) []registry.ServiceAccount {
	out := make([]registry.ServiceAccount, 0, len(spec.Accounts))
	for _, sa := range spec.Accounts {
		out = append(out, registry.ServiceAccount{
			Subject: sa.Subject, Role: sa.Role, Purpose: sa.Purpose, Status: "active", CreatedAt: at,
		})
	}
	return out
}

// ---------------------------------------------------------------- the row

// prospectiveTenant is the registry row `create` WOULD write. It is built at
// plan time so the plan can render the tenant's real env file and units —
// a dry run that cannot show those is a dry run of nothing.
func prospectiveTenant(roots paths.Roots, f *registry.Fleet, spec createSpec) *registry.Tenant {
	name := spec.Name
	tp := paths.TenantPaths(roots, name, name)
	a := spec.Artifact
	t := registry.NewTenant(name, name)
	t.DataDir, t.Worktree, t.PythonEnv = tp.DataDir, tp.Worktree, a.PythonEnv
	t.ArtifactID = registry.NullString(spec.ArtifactID)
	t.Code = registry.Code{Tag: a.Tag, SHA: registry.NullString(a.SHA)}
	t.Ports = paths.BlockAt(f.PortBase, f.PortStride, spec.Index)
	if paths.IsSelftestBlock(spec.Base) {
		// A sandbox's ports come from the selftest range, not from the
		// production sequence (registry.AllocateSandbox).
		t.Ports = paths.BlockAt(spec.Base, 0, 0)
		t.Ports.Index = spec.Index
	}
	t.API = registry.API{Bind: "127.0.0.1", PidFile: tp.PidFile, Log: tp.APILog}
	uiMode := spec.UIMode
	if uiMode == "" {
		uiMode = registry.UIModeStatic
	}
	t.UI = registry.UI{Mode: uiMode, Base: "/ragstack/" + name + "/ui/"}
	if uiMode == registry.UIModeDev {
		t.UI.Port = registry.NullPort(t.Ports.Base + 10)
	}
	t.Supervisor, t.Owner, t.State = supervisorSystemd, orDefault(spec.Owner, "svcbvbrc"), "provisioned"
	t.DesiredBoot, t.EnvLayout = "enabled", "managed"
	t.Identity = registry.Identity{Provider: spec.Provider, AdminSubjectsCount: len(spec.Subjects)}
	t.Settings = spec.Settings
	// A tenant the ctl created owns every store in its block, so every
	// capability is true from the start. The false-until-confirmed rule exists
	// for ADOPTED tenants, where the ctl is guessing which server is whose; here
	// it is not guessing — it allocated the ports and wrote the units.
	caps := registry.Capabilities{Stop: true, Purge: true, Restore: true, Snapshot: true}
	t.Stores.Qdrant = registry.Qdrant{
		Ownership: registry.OwnershipExclusive, Capabilities: caps,
		URL: fmt.Sprintf("http://127.0.0.1:%d", t.Ports.QdrantHTTP), Instance: registry.NullString("qdrant-" + name),
		SIF: registry.NullString(f.Images.Qdrant.SIF), ExtraEnv: map[string]string{},
	}
	t.Stores.Elasticsearch = registry.Elasticsearch{
		Ownership: registry.OwnershipExclusive, Capabilities: caps,
		URL: fmt.Sprintf("http://127.0.0.1:%d", t.Ports.ESHTTP), Instance: registry.NullString("elasticsearch-" + name),
		SIF: registry.NullString(f.Images.Elasticsearch.SIF), PathRepo: "/usr/share/elasticsearch/snapshots",
		Heap: registry.NullString(spec.ESHeap), ProvisionHeap: registry.NullString(spec.ESHeap),
		ExtraEnv: map[string]string{},
	}
	if spec.postgresLocal() {
		t.Stores.Postgres = registry.Postgres{
			Kind: registry.PostgresKindLocal, Ownership: registry.OwnershipExclusive, Capabilities: caps,
			URL:  registry.NullString(fmt.Sprintf("postgresql://localhost:%d", t.Ports.PG)),
			Port: registry.NullPort(t.Ports.PG), Instance: registry.NullString("postgres-" + name),
			// registry.Images pins the two SHARED store images only; the postgres
			// SIF is derived from the images directory, the same place
			// new-tenant.sh reads it from ($IMG/postgres.sif).
			SIF:     registry.NullString(filepath.Join(roots.ImagesDir, "postgres.sif")),
			DataDir: registry.NullString(filepath.Join(tp.DataDir, "postgres")),
		}
	} else {
		t.Stores.Postgres = registry.SQLiteStore()
	}
	// The contract requires a 64-hex env_file_sha256 on every row, and the file
	// does not exist when step 1 writes this. The hash of the EMPTY string is
	// the honest placeholder — it is a real digest of what is there — and step
	// 11 replaces it with the hash of the file that was actually written.
	t.EnvFileSHA256 = sha256Hex(nil)
	return t
}

// cloneTenant is a JSON round trip: the registry types are the contract's
// shapes, so a copy through them is exactly what a Load of a Save would give.
// The plan's prospective row must not be the row the engine mutates — the plan
// is recomputed and re-hashed, and a shared pointer would let a run half change
// what the plan said.
func cloneTenant(t *registry.Tenant) *registry.Tenant {
	b, err := json.Marshal(t)
	if err != nil {
		return t
	}
	var out registry.Tenant
	if err := json.Unmarshal(b, &out); err != nil {
		return t
	}
	return &out
}

// ---------------------------------------------------------------- helpers

func indexColon(s string) int {
	for i := 0; i < len(s); i++ {
		if s[i] == ':' {
			return i
		}
	}
	return 0
}

func boolArgOrDefault(args map[string]any, name string, def bool) bool {
	if v, ok := args[name].(bool); ok {
		return v
	}
	return def
}

func sortedStringKeys(m map[string]string) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func sortedUnitNames(units map[string][]byte) []string {
	out := make([]string, 0, len(units))
	for n := range units {
		out = append(out, n)
	}
	sort.Strings(out)
	return out
}

func withoutString(ss []string, drop string) []string {
	out := ss[:0:0]
	for _, s := range ss {
		if s != drop {
			out = append(out, s)
		}
	}
	return out
}

// sha256Hex is the registry's file-hash format: lowercase hex, no prefix.
func sha256Hex(b []byte) string {
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

// isNotExist is "the file is not there", through whatever the Files driver
// wrapped it in. Both drivers wrap fs.ErrNotExist for exactly this: the
// write-once check has to tell "no secrets.env yet" from "secrets.env could
// not be read", and treating the second as the first would overwrite a
// credential file the process merely lacked permission to open.
func isNotExist(err error) bool { return errors.Is(err, fs.ErrNotExist) }

// reKeyLabel is patLabel compiled once: create is the one place a whole list
// of labels is checked before anything is minted.
var reKeyLabel = regexp.MustCompile(patLabel)

// stampRFC3339Now is the plan-side clock for values recorded outside a step
// context (a service-account row created by the step that ran just before).
func (p *planner) stampRFC3339Now() string {
	return p.op.deps.now().UTC().Format(time.RFC3339)
}

// serviceAccountArg is one `service_accounts` entry of the create request.
// The role and the purpose travel with the subject because the tenant API
// records all three: a service account created with the wrong role is a
// credential with the wrong authority, not a cosmetic difference.
type serviceAccountArg struct {
	Subject string
	Role    string
	Purpose string
}

// serviceAccountArgs reads the entries, sorted by subject so the plan is a
// pure function of the args.
func serviceAccountArgs(args map[string]any) []serviceAccountArg {
	items, err := toSlice(args["service_accounts"])
	if err != nil {
		return nil
	}
	var out []serviceAccountArg
	for _, it := range items {
		m, ok := it.(map[string]any)
		if !ok {
			continue
		}
		subject, ok := m["subject"].(string)
		if !ok {
			continue
		}
		sa := serviceAccountArg{Subject: subject}
		sa.Role, _ = m["role"].(string)
		sa.Purpose, _ = m["purpose"].(string)
		out = append(out, sa)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Subject < out[j].Subject })
	return out
}

// ---------------------------------------------------------------- update-code

func planUpdateCode(_ context.Context, p *planner, args map[string]any) error {
	// The contract says so, and the reason is in the plan: swapping the
	// worktree under a running API needs the schema-compatibility assertion,
	// the pre-update bundle and the rollback descriptor that v1.1 introduces.
	// Refusing is the honest answer; pretending would be a code change with no
	// way back.
	return p.refuse("update-code is v1.1 and is not implemented yet (requested artifact %q). Until then: prepare the "+
		"artifact, `backup --fence`, and hand the swap to an operator", argStringOf(args, "artifact_id"))
}

// ---------------------------------------------------------------- gateway

func planGatewayApply(_ context.Context, p *planner, _ map[string]any) error {
	p.need(model.LockGateway)
	p.add(step{
		Kind: "nginx", Title: "publish the next gateway generation (stage, nginx -t, switch, HUP, confirm, probe)",
		Targets: []string{p.oc.Roots.ProxyDir},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
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
			sc.Logf("published gen-%d", gen)
			return detail, nil
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return sc.Ops.Drivers.Gateway().Rollback(ctx, 0)
		},
		Reconcile: func(_ context.Context, sc *jobs.StepContext) (jobs.Reconciliation, error) {
			// A generation number on the record means the publish got as far
			// as assigning one; the gateway package's own txn.json is the only
			// thing that can say whether it was verified, and `gateway repair`
			// is what reads it. Saying "stuck" sends the operator there rather
			// than re-publishing over a half-switched tree.
			for _, id := range sc.Step.ExternalIDs {
				if len(id) > len("gateway:gen:") && id[:len("gateway:gen:")] == "gateway:gen:" {
					return jobs.ReconcileStuck, fmt.Errorf("a generation was assigned (%s) but not confirmed; run "+
						"`ragstack-ctl gateway status` and `gateway repair`", id)
				}
			}
			return jobs.ReconcileRedo, nil
		},
	})
	return nil
}

func planGatewayReload(_ context.Context, p *planner, _ map[string]any) error {
	p.need(model.LockGateway)
	p.add(step{
		Kind: "nginx", Title: "reload the published generation (nginx -t on the live tree, HUP, confirm, probe)",
		Targets:  []string{p.oc.Roots.ProxyDir},
		Warnings: []string{"no new generation and no pointer change: this reloads what is already published"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return sc.Ops.Drivers.Gateway().Reload(ctx, false)
		},
	})
	return nil
}
