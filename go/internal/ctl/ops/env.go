package ops

import (
	"context"
	"fmt"
	"path/filepath"
	"sort"

	"github.com/ragstack/ragstack/internal/ctl/envfile"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/render"
	"github.com/ragstack/ragstack/internal/ctl/settings"
)

// ---------------------------------------------------------------- env set/unset

func planEnvSet(ctx context.Context, p *planner, args map[string]any) error {
	p.need(model.LockTenant)
	key, value := argStringOf(args, "key"), argStringOf(args, "value")
	if err := p.requirePublicKey(key); err != nil {
		return err
	}
	p.addEnvEdit(ctx, envEdit{
		Path: p.tpaths.TenantEnv, Op: "env-set",
		Title:   fmt.Sprintf("set %s in tenant.env", key),
		Targets: []string{key},
		Mutate: func(f *envfile.File) (string, error) {
			return fmt.Sprintf("%s=%s", key, value), f.Set(key, value)
		},
	})
	p.result["key"] = key
	p.result["pending_until_restart"] = true
	p.warn("the API reads its environment at start-up: this setting is `pending` until the tenant restarts")
	return nil
}

func planEnvUnset(ctx context.Context, p *planner, args map[string]any) error {
	p.need(model.LockTenant)
	key := argStringOf(args, "key")
	if err := p.requirePublicKey(key); err != nil {
		return err
	}
	p.addEnvEdit(ctx, envEdit{
		Path: p.tpaths.TenantEnv, Op: "env-unset", Destructive: true,
		Title:   fmt.Sprintf("unset %s in tenant.env", key),
		Targets: []string{key},
		Mutate: func(f *envfile.File) (string, error) {
			if !f.Unset(key) {
				return "", fmt.Errorf("%w: %s is not set in tenant.env", jobs.ErrRefused, key)
			}
			return "unset " + key, nil
		},
	})
	p.result["key"] = key
	p.result["pending_until_restart"] = true
	p.warn("the setting falls back to its default when the tenant restarts")
	return nil
}

// requirePublicKey is the env API's whole authorization rule: the typed env
// surface edits PUBLIC keys and nothing else.
//
// The two refusals are different facts and say so. A secret-class key belongs
// in secrets.env, whose values the ctl never shows and never accepts over
// HTTP. An executable-surface key decides WHAT code runs or WHERE the process
// reaches — PYTHONPATH, COLLECTIONS_FILE, QDRANT_URL — so editing one over an
// API is editing the tenant's behaviour with a config call; it stays with the
// trusted operators on the CLI.
func (p *planner) requirePublicKey(key string) error {
	switch settings.Classify(key) {
	case settings.Public:
		return nil
	case settings.Secret:
		return p.refuse("%s is a secret-class key: it lives in secrets.env and is never edited through the env API "+
			"(use `key mint` / `key revoke`, or edit secrets.env on the CLI)", key)
	case settings.ExecutableSurface:
		return p.refuse("%s is an executable-surface key — it decides what code runs or where the process connects — "+
			"so it is CLI-only for trusted operators, never the env API", key)
	default:
		return p.refuse("%s is not a known ragstack setting; the typed env API edits the public allowlist only "+
			"(`ragstack-ctl env get %s` lists it)", key, p.t.Name)
	}
}

// ---------------------------------------------------------------- env normalize

func planEnvNormalize(ctx context.Context, p *planner, _ map[string]any) error {
	p.need(model.LockTenant)
	tenantEnv, secretsEnv := p.tpaths.TenantEnv, p.tpaths.SecretsEnv

	// The preview is the whole point of this op — it is the one that RE-WRITES
	// a file somebody hand-edited — so it is computed from the live file and
	// shown redacted.
	var writes []model.WouldWrite
	var warns []string
	if b, err := p.readFile(ctx, tenantEnv); err != nil {
		warns = append(warns, "tenant.env could not be read while planning ("+err.Error()+"), so there is no preview")
		writes = []model.WouldWrite{{Path: tenantEnv, Mode: "0640", Preview: ""}, secretWrite(secretsEnv, "0640")}
	} else {
		f, problems, perr := envfile.ParseLenient(b)
		if perr != nil {
			return fmt.Errorf("%w: tenant.env cannot be read even leniently: %v", jobs.ErrRefused, perr)
		}
		for _, pr := range problems {
			warns = append(warns, pr.Error())
		}
		if moved := f.Normalize(); len(moved) > 0 {
			warns = append(warns, fmt.Sprintf("%d inline comment(s) move onto their own line: %v", len(moved), moved))
		}
		public, secrets := envfile.SplitSecrets(f, settings.Classify)
		writes = []model.WouldWrite{
			p.preview(tenantEnv, "0640", public.Render()),
			secretWrite(secretsEnv, "0640"),
		}
		if len(secrets.SecretKeys()) > 0 {
			warns = append(warns, fmt.Sprintf("%d secret-class key(s) move out of tenant.env into secrets.env: %v",
				len(secrets.SecretKeys()), secrets.SecretKeys()))
		}
	}
	p.add(step{
		Kind: "envfile", Title: "normalize tenant.env and split the secrets out into secrets.env",
		Targets: []string{tenantEnv, secretsEnv}, WouldWrite: writes,
		Warnings: append(warns, "the previous content is kept beside each file as `.bak-env-normalize-<ts>`"),
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			files := sc.Ops.Drivers.Files()
			b, err := files.ReadFile(ctx, tenantEnv)
			if err != nil {
				return "", err
			}
			f, _, err := envfile.ParseLenient(b)
			if err != nil {
				return "", err
			}
			notes := f.Normalize()
			public, secrets := envfile.SplitSecrets(f, settings.Classify)
			stamp := p.stampOf(sc)
			bak := tenantEnv + ".bak-env-normalize-" + stamp
			if err := sc.Checkpoint("file:"+bak, "file:"+tenantEnv, "file:"+secretsEnv); err != nil {
				return "", err
			}
			if err := files.WriteAtomic(ctx, bak, b, 0o640); err != nil {
				return "", err
			}
			if err := files.WriteAtomic(ctx, tenantEnv, public.Render(), 0o640); err != nil {
				return "", err
			}
			if len(secrets.SecretKeys()) > 0 {
				if err := files.WriteAtomic(ctx, secretsEnv, secrets.Render(), 0o640); err != nil {
					return "", err
				}
			}
			sc.Logf("%d normalization(s); %d secret key(s) moved", len(notes), len(secrets.SecretKeys()))
			return fmt.Sprintf("normalized (%d change(s))", len(notes)), nil
		},
	})
	p.result["pending_until_restart"] = true
	return nil
}

// ---------------------------------------------------------------- render-units

func planRenderUnits(_ context.Context, p *planner, args map[string]any) error {
	p.need(model.LockTenant)
	apply := argBoolOf(args, "apply")
	cfg := render.UnitConfig{
		RagRoot:     p.oc.Roots.RagRoot,
		CtlStateDir: p.oc.Roots.CtlStateDir,
	}
	units, err := render.Units(p.t, cfg)
	if err != nil {
		// The renderer's refusals are real ones — a non-loopback bind on an
		// internet-reachable host, a data dir that does not match the manifest
		// name — and they are about the REGISTRY ROW, so they belong here and
		// not at run time.
		return p.refuse("%s's units cannot be rendered: %v", p.t.Name, err)
	}
	dir := filepath.Join(p.oc.Roots.UnitsDir(), p.t.Name)
	names := make([]string, 0, len(units))
	for name := range units {
		names = append(names, name)
	}
	sort.Strings(names)

	var writes []model.WouldWrite
	for _, name := range names {
		writes = append(writes, p.preview(filepath.Join(dir, name), "0644", units[name]))
	}
	if !apply {
		p.add(step{
			Kind: "render", Title: fmt.Sprintf("render %s's %d unit file(s) and compare", p.t.Name, len(names)),
			Targets: append([]string{dir}, names...), WouldWrite: writes,
			Warnings: []string{"apply is false: the units are rendered and shown, and nothing is written"},
			Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
				sc.Logf("rendered %d unit file(s) for %s; nothing written (apply=false)", len(names), p.t.Name)
				return fmt.Sprintf("rendered %d unit file(s); nothing written", len(names)), nil
			},
		})
		p.warn("pass apply to write the units into " + dir + " and run `daemon-reload`")
		return nil
	}
	for _, name := range names {
		path, body := filepath.Join(dir, name), units[name]
		p.add(step{
			Kind: "fs", Title: "write " + name, Targets: []string{path},
			WouldWrite: []model.WouldWrite{p.preview(path, "0644", body)},
			Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
				if err := sc.Checkpoint("file:" + path); err != nil {
					return "", err
				}
				return path, sc.Ops.Drivers.Files().WriteAtomic(ctx, path, body, 0o644)
			},
			Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
				return "removed " + path, sc.Ops.Drivers.Files().Remove(ctx, path)
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
	p.result["units"] = names
	return nil
}
