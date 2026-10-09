package ops

// `env set-surface` / `env unset-surface` (#714): the executable-surface keys
// — LLM_ENDPOINT, GOWE_URL, COLLECTIONS_FILE, GOWE_IMAGE_DIRS, … — for a
// trusted operator on the CLI.
//
// ADR-0007's split is the whole design constraint: HTTP mutates typed PUBLIC
// settings only; a path, a URL, a code reference is a trusted-operator action
// on the host. These two verbs keep it with TWO gates, either of which is
// enough on its own:
//
//  1. they are not in the ops endpoint's verb enum (api/jobs.go opVerbs), so
//     POST …/ops/env-set-surface is 422 before anything is looked up; and
//  2. their planners refuse unless the ENGINE runs in direct mode
//     (jobs.Context.Mode, set by the engine and never by the request) — which
//     also makes the re-plan of a daemon-side `resume` refuse, the hole
//     api/jobs.go's continuation guard closes from the other side.
//
// Every value goes through settings.ValidateSurface (the table in
// settings/surface.go), and on an image-mode row the API instance's bind
// derivation and path probe run over the resulting environment, so a value is
// refused at plan time rather than at the next start.

import (
	"context"
	"fmt"
	"regexp"
	"sort"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/envfile"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/render"
	"github.com/ragstack/ragstack/internal/ctl/settings"
)

// maxSurfaceValues bounds one request: a handful of keys is the use, and a
// plan with hundreds of them is a mistake nobody could review.
const maxSurfaceValues = 32

// maxSurfaceValueLen is env-set's value bound.
const maxSurfaceValueLen = 4096

// requireDirect is the plan-time gate: the engine running this plan is a
// --direct CLI process, not the daemon.
func (p *planner) requireDirect(cli string) error {
	if p.oc.Mode == model.WorkerDirect {
		return nil
	}
	mode := string(p.oc.Mode)
	if mode == "" {
		mode = "unknown"
	}
	return p.refuse("`%s` runs only from the CLI with --direct, and this engine runs in %s mode: it edits "+
		"executable-surface keys — where the tenant's API connects and which files it loads — which ADR-0007 "+
		"keeps off every HTTP surface. Run it on the host as the ctl account "+
		"(`ops/coconut/ctl-as-svc.sh %s …`); an interrupted job of it is resumed with "+
		"`ragstack-ctl job resume <id> --direct`", cli, mode, cli)
}

// allowedHosts is the endpoint allowlist this plan validates against.
func (d Deps) allowedHosts() []string {
	if d.AllowedEndpointHosts != nil {
		return d.AllowedEndpointHosts
	}
	hosts, _ := settings.AllowedHostsFrom("")
	return hosts
}

// surfaceContext is what settings.ValidateSurface needs for row: its own
// dirs, the bind roots, the allowlist, and every directory a tenant setting
// must never reach — the ctl's config, state and backups, and every OTHER
// tenant's data dir.
func (p *planner) surfaceContext(row *registry.Tenant) settings.SurfaceContext {
	r := p.oc.Roots
	c := settings.SurfaceContext{
		ImageRow: row.ServerImage != nil, DataDir: row.DataDir, Worktree: row.Worktree,
		BindRoots: p.op.deps.APIBindRoots, AllowedHosts: p.op.deps.allowedHosts(),
		Denied: []settings.DeniedRoot{
			{Path: r.CtlConfigDir, Why: "the ctl's configuration, its API keys and the backup identity"},
			{Path: r.CtlStateDir, Why: "the ctl's state: jobs.db, prepared artifacts and images"},
			{Path: r.BackupsDir, Why: "every tenant's backup bundles"},
		},
	}
	if p.oc.Fleet != nil {
		names := make([]string, 0, len(p.oc.Fleet.Tenants))
		for name := range p.oc.Fleet.Tenants {
			names = append(names, name)
		}
		sort.Strings(names)
		for _, name := range names {
			t := p.oc.Fleet.Tenants[name]
			if name == row.Name || t == nil || t.DataDir == "" {
				continue
			}
			c.Denied = append(c.Denied, settings.DeniedRoot{Path: t.DataDir, Why: "tenant " + name + "'s data dir"})
		}
	}
	return c
}

// classifySurfaceKey refuses a key that is not executable-surface, each class
// with the verb that does edit it.
func (p *planner) classifySurfaceKey(key, unsetVerb string) error {
	switch settings.Classify(key) {
	case settings.ExecutableSurface:
		return nil
	case settings.Public:
		return p.refuse("%s is a PUBLIC setting: edit it with `ragstack-ctl env %s %s …`, which the env API "+
			"also serves; the surface verbs take executable-surface keys only", key, unsetVerb, p.tenant)
	case settings.Secret:
		return p.refuse("%s is a secret-class key: it lives in secrets.env and is never edited through the env "+
			"verbs (use `key mint` / `key revoke`, or edit secrets.env on the host)", key)
	default:
		if reason, retired := settings.Retired(key); retired {
			return p.refuse("%s is %s", key, reason)
		}
		return p.refuse("%s is not a known ragstack setting", key)
	}
}

// validateSurfaceValues checks every KEY=VALUE of a request against the table
// and the env grammar, and returns the values to write (a URL list in its
// comma form). Validation failures of the SHAPE are 422; a value the table
// refuses is a 409 refusal naming the key.
func (p *planner) validateSurfaceValues(row *registry.Tenant, raw map[string]any) (map[string]string, []string, error) {
	if len(raw) == 0 {
		return nil, nil, fmt.Errorf("%w: values: at least one KEY=VALUE is required", jobs.ErrValidation)
	}
	if len(raw) > maxSurfaceValues {
		return nil, nil, fmt.Errorf("%w: values: %d keys in one request (at most %d)", jobs.ErrValidation, len(raw),
			maxSurfaceValues)
	}
	keys := sortedKeys(raw)
	c := p.surfaceContext(row)
	scratch, err := envfile.Parse(nil)
	if err != nil {
		return nil, nil, err
	}
	out := make(map[string]string, len(keys))
	for _, k := range keys {
		if !envKeyPattern.MatchString(k) {
			return nil, nil, fmt.Errorf("%w: values: key %q does not match %s", jobs.ErrValidation, k, patEnvKey)
		}
		v, ok := raw[k].(string)
		if !ok {
			return nil, nil, fmt.Errorf("%w: values.%s must be a string", jobs.ErrValidation, k)
		}
		if len(v) > maxSurfaceValueLen {
			return nil, nil, fmt.Errorf("%w: values.%s is longer than %d", jobs.ErrValidation, k, maxSurfaceValueLen)
		}
		if err := p.classifySurfaceKey(k, "set"); err != nil {
			return nil, nil, err
		}
		norm, err := settings.ValidateSurface(k, v, c)
		if err != nil {
			return nil, nil, p.refuse("%v", err)
		}
		// The env grammar's own refusals (a value holding both quote kinds
		// has no representation) are found HERE, at plan time, rather than
		// by the run half after the backup was written.
		if err := scratch.Set(k, norm); err != nil {
			return nil, nil, p.refuse("%s cannot be written to tenant.env: %v", k, err)
		}
		out[k] = norm
	}
	return out, keys, nil
}

var envKeyPattern = regexp.MustCompile(patEnvKey)

// imageSurfaceCheck runs an image row's bind derivation and the start's path
// probe over the environment the API would see after this edit — the file as
// the plan reads it (or the one a create is about to write), with values laid
// over it and unset removed. It is what catches a sqlite *_STORE_PATH in a
// read-only bind: the path rule alone cannot, because every absolute path-key
// value becomes its own read-only bind.
func (p *planner) imageSurfaceCheck(row *registry.Tenant, tp paths.Tenant, values map[string]string,
	unset []string) error {
	env, _ := p.apiPublicEnv(row, tp)
	for k, v := range values {
		env[k] = v
	}
	for _, k := range unset {
		delete(env, k)
	}
	img, err := render.APIImageLaunch(row, p.unitConfig(), env, tp.APILog)
	if err != nil {
		return err
	}
	return checkAPIImagePaths(env, img.Binds)
}

// ---------------------------------------------------------------- set-surface

func planEnvSetSurface(ctx context.Context, p *planner, args map[string]any) error {
	if err := p.requireDirect("env set-surface"); err != nil {
		return err
	}
	// The registry lock because the job ends in a registry write
	// (restart_pending + env_file_sha256), as every registry writer takes it.
	p.need(model.LockRegistry, model.LockManifest, model.LockTenant)
	raw, _ := args["values"].(map[string]any)
	values, keys, err := p.validateSurfaceValues(p.t, raw)
	if err != nil {
		return err
	}
	if p.t.ServerImage != nil {
		if err := p.imageSurfaceCheck(p.t, p.tpaths, values, nil); err != nil {
			return p.refuse("%s runs its API from a server image, and with this edit it could not start: %v",
				p.t.Name, err)
		}
	}
	var written []byte
	p.addEnvEdit(ctx, envEdit{
		Path: p.tpaths.TenantEnv, Op: "env-set-surface",
		Title:   fmt.Sprintf("set %s in tenant.env (executable-surface)", strings.Join(keys, ", ")),
		Targets: keys, Written: &written,
		Mutate: func(f *envfile.File) (string, error) {
			var done []string
			for _, k := range keys {
				if err := f.Set(k, values[k]); err != nil {
					return "", err
				}
				// Surface values are not secrets (a secret-class key is
				// refused above), so the log names the value.
				done = append(done, k+"="+values[k])
			}
			return "set " + strings.Join(done, " "), nil
		},
	})
	p.addEnvRegistryStep("", &written)
	p.result["keys"] = keys
	p.result["pending_until_restart"] = true
	p.warn("the API reads its environment at start-up: these settings are `pending` until the tenant restarts "+
		"(`ragstack-ctl tenant restart %s`)", p.t.Name)
	return nil
}

// ---------------------------------------------------------------- unset-surface

func planEnvUnsetSurface(ctx context.Context, p *planner, args map[string]any) error {
	if err := p.requireDirect("env unset-surface"); err != nil {
		return err
	}
	p.need(model.LockRegistry, model.LockManifest, model.LockTenant)
	keys := argStringsOf(args, "keys")
	if len(keys) == 0 {
		return fmt.Errorf("%w: keys: at least one KEY is required", jobs.ErrValidation)
	}
	if len(keys) > maxSurfaceValues {
		return fmt.Errorf("%w: keys: %d keys in one request (at most %d)", jobs.ErrValidation, len(keys),
			maxSurfaceValues)
	}
	keys = append([]string(nil), keys...)
	sort.Strings(keys)
	for _, k := range keys {
		// Any executable-surface key may be REMOVED — including the refused-
		// always ones: taking PYTHONPATH out of tenant.env is the repair an
		// image row's hijack refusal asks for.
		if err := p.classifySurfaceKey(k, "unset"); err != nil {
			return err
		}
	}
	if p.t.ServerImage != nil {
		// A warning and not a refusal: removing a key can only fall back to
		// the API's default, and a row already broken elsewhere must still
		// be repairable. The start's own probe refuses what this predicts.
		if err := p.imageSurfaceCheck(p.t, p.tpaths, nil, keys); err != nil {
			p.warn("after this edit the API instance's start would refuse: %v", err)
		}
	}
	var written []byte
	p.addEnvEdit(ctx, envEdit{
		Path: p.tpaths.TenantEnv, Op: "env-unset-surface", Destructive: true,
		Title:   fmt.Sprintf("unset %s in tenant.env (executable-surface)", strings.Join(keys, ", ")),
		Targets: keys, Written: &written,
		Mutate: func(f *envfile.File) (string, error) {
			var missing []string
			for _, k := range keys {
				if !f.Unset(k) {
					missing = append(missing, k)
				}
			}
			if len(missing) > 0 {
				return "", fmt.Errorf("%w: %s not set in tenant.env", jobs.ErrRefused, strings.Join(missing, ", "))
			}
			return "unset " + strings.Join(keys, " "), nil
		},
	})
	p.addEnvRegistryStep("", &written)
	p.result["keys"] = keys
	p.result["pending_until_restart"] = true
	p.warn("the settings fall back to their defaults when the tenant restarts")
	return nil
}

// ---------------------------------------------------------------- the registry step

// addEnvRegistryStep records what an env edit did to the row: restart_pending
// (the running API read its environment at start-up and still runs with the
// old one) and env_file_sha256 (the hash of the file this job wrote, so doctor
// does not report the job's own edit as `env_file_changed` drift).
//
// `env set` / `env unset` left both stale until #714; the surface verbs and
// they now share this step. verb, when non-empty, is recorded in last_ops —
// the surface verbs pass "" because last_ops' key set is the registry
// schema's OpVerb enum, a durable format an older ctl binary validates on
// load, and widening it for a bookkeeping row is not worth that one-way door.
func (p *planner) addEnvRegistryStep(verb string, written *[]byte) {
	name, path := p.tenant, p.tpaths.TenantEnv
	p.add(step{
		Kind: "registry", Title: "record restart_pending and tenant.env's new checksum", Targets: []string{name},
		WouldWrite: []model.WouldWrite{{Path: p.oc.Roots.Registry(), Mode: "0660", Preview: model.NullString("")}},
		Warnings: []string{"restart_pending becomes true until the tenant restarts; env_file_sha256 becomes the " +
			"hash of the file this job wrote"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			var body []byte
			if written != nil {
				body = *written
			}
			if body == nil {
				// A resumed job whose edit step ran in an earlier process:
				// the file on disk is the one it wrote (the tenant lock has
				// been held across both steps of this job).
				b, err := sc.Ops.Drivers.Files().ReadFile(ctx, path)
				if err != nil {
					return "", fmt.Errorf("reading %s for its checksum: %w", path, err)
				}
				body = b
			}
			sum := sha256Hex(body)
			if err := p.saveTenant(sc, verb, func(t *registry.Tenant) error {
				t.RestartPending = true
				t.EnvFileSHA256 = sum
				return nil
			}); err != nil {
				return "", err
			}
			p.result["restart_pending"] = true
			p.result["env_file_sha256"] = sum
			return fmt.Sprintf("%s restart_pending = true, env_file_sha256 = %s", name, sum), nil
		},
	})
}
