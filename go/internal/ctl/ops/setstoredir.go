package ops

// `tenant set-store-dir` (PR3 of the per-qdrant host data dir): the one verb
// that writes a row's stores.qdrant.data_dir.
//
// It is CLI-only and --direct-only, like `env set-surface`: it names a host
// directory a store will be bound from, which ADR-0007 keeps off every HTTP
// surface. It does not copy anything. The operator moves the data first
// (docs/runbooks/qdrant-data-dir.md: fenced backup, census, rsync, verify) and
// this verb then repoints the row, refusing every state in which repointing
// would hand qdrant an empty, foreign or unmounted directory:
//
//   - the store must be EXCLUSIVE to the row (a shared qdrant is not the
//     row's to move; SHARED_QDRANT_DIR in stores.env is that store's knob);
//   - the qdrant leg must be STOPPED: its instance absent from both instance
//     tables and nothing listening on its ports. A store copied while it runs
//     is a store copied mid-write, and a row repointed under a running qdrant
//     describes a process bound somewhere else;
//   - `--dir` must pass registry.Tenant.QdrantDataDirAllowed (strictly under a
//     CTL_STORE_DATA_ROOTS entry or the row's own data_dir), must not be the
//     default spelling (that is `--clear`), and the row it produces must pass
//     Fleet.Validate and ValidateContract — the cross-row rule from PR1 (no
//     sharing, no nesting under another row's data_dir or qdrant dir) refuses
//     here with its own sentence rather than at the save;
//   - `<dir>`, `<dir>/storage` and `<dir>/snapshots` must be real directories
//     (not symlinks), and outside the rag root the deepest mount holding the
//     dir must not be `/` — the same run-time check the instance supervisor
//     makes before it starts a moved qdrant (qdrantDataDirPrecheck);
//   - `--clear` requires `<data_dir>/qdrant/storage` to be a real directory:
//     the default location must actually hold the store.
//
// The registry write goes through saveTenant (registry.Save: both validation
// halves, the generation check under the registry lock, the manifest
// projection) and sets restart_pending. It deliberately records NO last_ops
// entry: last_ops' key set is the registry's OpVerb enum, which an older ctl
// binary validates on load, and a `--clear` that left `last_ops.set-store-dir`
// behind would keep every older binary refusing the registry after the field
// itself is gone — the rollback this verb exists to make cheap. The job record
// and the audit row carry the history instead (the env surface verbs made the
// same choice, envsurface.go addEnvRegistryStep).

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"path"
	"strconv"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/render"
)

// setStoreDirCLI is how the verb is spelled on the command line, for messages.
const setStoreDirCLI = "tenant set-store-dir"

// OtherBinariesWarning is the line the CLI prints after a successful
// set-store-dir, and the plan carries as a warning: the first row that sets
// stores.qdrant.data_dir is a registry every ctl binary older than PR1 refuses
// (registry.Load decodes with DisallowUnknownFields).
const OtherBinariesWarning = "warning: a registry row with stores.qdrant.data_dir can only be read by a ctl " +
	"binary that knows the field — every other ragstack-ctl on this host (the daemon, CLI copies, cron jobs) " +
	"must be this build or newer; `ragstack-ctl registry validate` checks the registry with the binary you run it as"

// binaryKnowsQdrantDataDir reports whether this binary's registry types
// serialise stores.qdrant.data_dir. It is a variable so a test can play an
// older binary: a ctl that cannot round-trip the field would write a row
// without it, silently undoing the move. (It is always true for a build that
// contains this file; the check is what makes that a stated precondition
// rather than an assumption.)
var binaryKnowsQdrantDataDir = func() bool {
	var q registry.Tenant
	q.Stores.Qdrant.DataDir = "/probe"
	b, err := json.Marshal(q.Stores.Qdrant)
	return err == nil && bytes.Contains(b, []byte(`"data_dir":"/probe"`))
}

func planSetStoreDir(_ context.Context, p *planner, args map[string]any) error {
	if p.oc.Mode != model.WorkerDirect {
		mode := string(p.oc.Mode)
		if mode == "" {
			mode = "unknown"
		}
		return p.refuse("`%s` runs only from the CLI with --direct, and this engine runs in %s mode: it names the "+
			"host directory a store is bound from, which ADR-0007 keeps off every HTTP surface. Run it on the host "+
			"as the account that owns the row (`ops/coconut/ctl-as-svc.sh %s …` for a svcbvbrc row)",
			setStoreDirCLI, mode, setStoreDirCLI)
	}
	p.need(model.LockTenant, model.LockRegistry, model.LockManifest)

	if store := argStringOf(args, "store"); store != "qdrant" {
		return fmt.Errorf("%w: store: %q is not a store this verb moves (qdrant only)", jobs.ErrValidation, store)
	}
	dir, hasDir := args["dir"].(string)
	clear := argBoolOf(args, "clear")
	switch {
	case hasDir && clear:
		return fmt.Errorf("%w: dir and clear are exclusive: --dir points the row at a moved store, --clear points "+
			"it back at <data_dir>/qdrant", jobs.ErrValidation)
	case !hasDir && !clear:
		return fmt.Errorf("%w: one of dir or clear is required", jobs.ErrValidation)
	}
	if !binaryKnowsQdrantDataDir() {
		return p.refuse("this ragstack-ctl binary does not know stores.qdrant.data_dir, so it cannot write it; " +
			"run the verb with a build that contains the per-qdrant data dir (PR1 or newer)")
	}

	t := p.t
	if t.Handover != nil {
		return p.refuse("%s has a handover in flight (phase %s): finish or abandon it before moving its store",
			t.Name, t.Handover.Phase)
	}
	if t.Quarantine != nil || t.State == registry.StateQuarantined || t.State == "decommissioned" {
		return p.refuse("%s is %s: a quarantined or decommissioned tenant's store is not moved", t.Name, t.State)
	}
	if t.Stores.Qdrant.Ownership != registry.OwnershipExclusive {
		return p.refuse("%s's qdrant is %s, not exclusive: only a store the row owns can be moved by its row (a "+
			"shared qdrant's directory is SHARED_QDRANT_DIR / SHARED_QDRANT2_DIR in /rag/config/stores.env)",
			t.Name, orNone(t.Stores.Qdrant.Ownership))
	}

	def := path.Join(path.Clean(t.DataDir), "qdrant")
	prev := t.Stores.Qdrant.DataDir
	want := ""
	if hasDir {
		if !path.IsAbs(dir) || path.Clean(dir) != dir {
			return p.refuse("--dir %q is not an absolute, clean path (path.Clean gives %q)", dir, path.Clean(dir))
		}
		if dir == def || dir == path.Join(p.tpaths.DataDir, "qdrant") {
			return p.refuse("--dir %s is the default location of %s's qdrant; use --clear to point the row back "+
				"at it (the field is absent for the default, never spelled)", dir, t.Name)
		}
		want = dir
		if err := p.checkCandidateRow(want); err != nil {
			return err
		}
	}

	p.result["store"] = "qdrant"
	p.result["data_dir"] = want
	p.result["previous_data_dir"] = prev
	p.result["effective_dir"] = orDefault(want, def)
	if want == prev {
		// Idempotent: the row already says this. Nothing is probed and nothing
		// is written — not even restart_pending, which a no-op must not raise.
		p.result["changed"] = false
		p.add(step{
			Kind: "registry", Targets: []string{t.Name},
			Title: fmt.Sprintf("stores.qdrant.data_dir is already %s: nothing to write", describeQdrantDir(want, def)),
			Run: func(context.Context, *jobs.StepContext) (string, error) {
				return "unchanged: stores.qdrant.data_dir is " + describeQdrantDir(want, def), nil
			},
		})
		return nil
	}
	p.result["changed"] = true
	p.result["restart_pending"] = true

	p.addQdrantStoppedProbe()
	p.addStoreDirProbe(want, def)
	p.addStoreDirRegistryStep(want, prev, def)

	p.warn("the REGISTRY only: no data is copied or removed by this op — the operator moves the store first "+
		"(docs/runbooks/qdrant-data-dir.md) — and no process is started; `ragstack-ctl tenant start %s` binds the "+
		"new location", t.Name)
	if want != "" {
		p.warn("%s", OtherBinariesWarning)
	}
	return nil
}

// checkCandidateRow runs the row this op would write through every rule the
// registry holds it to — the containment rule over the configured store roots,
// the per-row contract and the cross-row invariant — on a COPY of the fleet,
// so a refusal comes back at plan time with the rule's own sentence.
func (p *planner) checkCandidateRow(want string) error {
	if p.oc.Fleet == nil {
		return p.refuse("the engine loaded no registry to check %s against", want)
	}
	f, err := cloneFleetJSON(p.oc.Fleet)
	if err != nil {
		return err
	}
	row, ok := f.Tenants[p.tenant]
	if !ok {
		return p.refuse("%s is not in the registry", p.tenant)
	}
	row.Stores.Qdrant.DataDir = want
	if err := row.QdrantDataDirAllowed(p.op.deps.StoreDataRoots); err != nil {
		return p.refuse("%v", err)
	}
	if err := f.Validate(); err != nil {
		return p.refuse("the registry would not accept %s's stores.qdrant.data_dir = %s: %v", p.tenant, want, err)
	}
	// registry.Save stamps these before its contract check; the copy gets
	// placeholders (a plan reads no clock), and is never written.
	f.SchemaVersion = registry.SchemaVersion
	f.Generation++
	f.UpdatedAt, f.UpdatedBy = "1970-01-01T00:00:00Z", "set-store-dir plan check"
	if err := f.ValidateContract(); err != nil {
		return p.refuse("the registry contract would not accept %s's stores.qdrant.data_dir = %s: %v",
			p.tenant, want, err)
	}
	return nil
}

// cloneFleetJSON is a JSON round trip: the registry types are the contract's
// shapes, so this is exactly what a Save would write and a Load read back.
func cloneFleetJSON(f *registry.Fleet) (*registry.Fleet, error) {
	b, err := json.Marshal(f)
	if err != nil {
		return nil, err
	}
	var out registry.Fleet
	if err := json.Unmarshal(b, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// addQdrantStoppedProbe is the liveness half: the tenant's qdrant instance is
// in neither instance table (the ctl's own, the account's default one a hand
// start uses) and nothing listens on its HTTP or gRPC port. Either is a qdrant
// that has the store open.
func (p *planner) addQdrantStoppedProbe() {
	name := p.tenant
	instance := instanceNameFor(render.LegQdrant, p.t.ManifestName)
	var ports []int
	for _, port := range []int{p.t.Ports.QdrantHTTP, p.t.Ports.QdrantGRPC} {
		if port > 0 {
			ports = append(ports, port)
		}
	}
	targets := []string{instance}
	for _, port := range ports {
		targets = append(targets, strconv.Itoa(port))
	}
	p.addFor("proc", step{
		Kind: "probe", Title: "check that " + name + "'s qdrant is stopped", Targets: targets,
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			for _, ns := range []jobs.InstanceNamespace{jobs.NamespaceCtl, jobs.NamespaceAccountDefault} {
				up, err := instanceRunning(ctx, sc, instance, ns)
				if err != nil {
					return "", err
				}
				if up {
					return "", fmt.Errorf("%w: instance %s is running: the store is open, and repointing the row "+
						"under it describes a process bound somewhere else. `ragstack-ctl tenant stop %s` first",
						jobs.ErrRefused, instance, name)
				}
			}
			var busy []string
			for _, port := range ports {
				listening, err := sc.Ops.Drivers.Proc().Listening(ctx, port)
				if err != nil {
					return "", err
				}
				if listening {
					busy = append(busy, strconv.Itoa(port))
				}
			}
			if len(busy) > 0 {
				return "", fmt.Errorf("%w: something listens on %s, %s's qdrant port(s): a qdrant is running with "+
					"the store open. `ragstack-ctl tenant stop %s` first", jobs.ErrRefused,
					strings.Join(busy, ", "), name, name)
			}
			return fmt.Sprintf("%s is not running and port(s) %s are free", instance, joinInts(ports)), nil
		},
	})
}

// addStoreDirProbe checks the directory the row will point at holds a store:
// for --dir the moved-dir pre-start check itself; for --clear a real
// <data_dir>/qdrant/storage.
func (p *planner) addStoreDirProbe(want, def string) {
	if want == "" {
		storage := path.Join(def, "storage")
		p.addFor("files", step{
			Kind: "probe", Title: "check that the default location " + def + " holds the store",
			Targets: []string{storage},
			Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
				if err := realDir(ctx, sc, storage); err != nil {
					return "", fmt.Errorf("%w: --clear points the row back at %s, and %v; a qdrant started there "+
						"would start EMPTY. Copy the store back first, or keep the row where it is",
						jobs.ErrRefused, def, err)
				}
				return storage + " is a directory", nil
			},
		})
		return
	}
	candidate := *p.t
	candidate.Stores.Qdrant.DataDir = want
	tp := candidate.Paths(p.oc.Roots)
	ragRoot, mountOf := p.oc.Roots.RagRoot, p.op.deps.MountOf
	p.addFor("files", step{
		Kind: "probe", Title: "check that " + want + " holds a store (storage/ and snapshots/ real directories, " +
			"on a mounted volume)",
		Targets: []string{tp.QdrantDir, tp.QdrantStorage, tp.QdrantSnapshots},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return qdrantDataDirPrecheck(ctx, sc, tp, ragRoot, mountOf)
		},
	})
}

// realDir is nil when p exists, is a directory and is not a symlink.
func realDir(ctx context.Context, sc *jobs.StepContext, p string) error {
	st, err := sc.Ops.Drivers.Files().Stat(ctx, p)
	switch {
	case errors.Is(err, fs.ErrNotExist):
		return fmt.Errorf("%s does not exist", p)
	case err != nil:
		return fmt.Errorf("checking %s: %v", p, err)
	case st.IsSymlink:
		return fmt.Errorf("%s is a symlink", p)
	case !st.IsDir:
		return fmt.Errorf("%s is not a directory", p)
	}
	return nil
}

// addStoreDirRegistryStep writes the field and restart_pending, and nothing
// else. The previous values are checkpointed BEFORE the write, so the rollback
// of a resumed job restores what was there rather than what a re-plan sees.
func (p *planner) addStoreDirRegistryStep(want, prev, def string) {
	name := p.tenant
	p.add(step{
		Kind: "registry",
		Title: fmt.Sprintf("record stores.qdrant.data_dir = %s (was %s) and restart_pending",
			describeQdrantDir(want, def), describeQdrantDir(prev, def)),
		Targets:    []string{name},
		WouldWrite: []model.WouldWrite{{Path: p.oc.Roots.Registry(), Mode: "0660", Preview: model.NullString("")}},
		Warnings: []string{"restart_pending becomes true: the qdrant leg binds the new location at its next " +
			"start. No last_ops entry is recorded (an older ctl binary validates last_ops' keys on load)"},
		Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			if f := sc.Ops.Fleet; f != nil {
				if t, ok := f.Tenants[name]; ok {
					if err := sc.Checkpoint(storeDirPrevID+t.Stores.Qdrant.DataDir,
						restartPrevID+strconv.FormatBool(t.RestartPending)); err != nil {
						return "", err
					}
				}
			}
			if err := p.saveTenant(sc, "", func(t *registry.Tenant) error {
				t.Stores.Qdrant.DataDir = want
				t.RestartPending = true
				return nil
			}); err != nil {
				return "", err
			}
			return fmt.Sprintf("%s stores.qdrant.data_dir = %s", name, describeQdrantDir(want, def)), nil
		},
		Rollback: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			was, ok := externalIDValue(sc.Step.ExternalIDs, storeDirPrevID)
			if !ok {
				return "nothing was written", nil
			}
			pending, _ := externalIDValue(sc.Step.ExternalIDs, restartPrevID)
			if err := p.saveTenant(sc, "", func(t *registry.Tenant) error {
				t.Stores.Qdrant.DataDir = was
				t.RestartPending = pending == "true"
				return nil
			}); err != nil {
				return "", err
			}
			return name + " stores.qdrant.data_dir = " + describeQdrantDir(was, def) + " again", nil
		},
	})
}

const (
	storeDirPrevID = "qdrant-data-dir-prev:"
	restartPrevID  = "restart-pending-prev:"
)

// describeQdrantDir renders the field for a title: the path, or the default.
func describeQdrantDir(v, def string) string {
	if v == "" {
		return "(absent: default " + def + ")"
	}
	return v
}

func joinInts(ns []int) string {
	s := make([]string, len(ns))
	for i, n := range ns {
		s[i] = strconv.Itoa(n)
	}
	return strings.Join(s, ", ")
}
