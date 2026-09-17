package ops

import (
	"context"
	"fmt"
	"net/url"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/envfile"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/registry"
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
	// LockRegistry/LockManifest because this op now ends in a registry write:
	// splitting the secrets out is exactly what moves a tenant from
	// `env_layout: legacy` to `managed`, and a row that still said `legacy`
	// afterwards was a row that made `tenant backup` copy tenant.env as a
	// public file and made every reader believe the split had not happened.
	p.need(model.LockTenant, model.LockRegistry, model.LockManifest)
	tenantEnv, secretsEnv := p.tpaths.TenantEnv, p.tpaths.SecretsEnv
	// Filled by the run half below and read by the registry step after it.
	var (
		movedKeys  []string
		publicSHA  string
		secretsSHA string
	)

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
			movedKeys = secrets.SecretKeys()
			stamp := p.stampOf(sc)
			bak := tenantEnv + ".bak-env-normalize-" + stamp
			if err := sc.Checkpoint("file:"+bak, "file:"+tenantEnv, "file:"+secretsEnv); err != nil {
				return "", err
			}
			if err := files.WriteAtomic(ctx, bak, b, 0o640); err != nil {
				return "", err
			}
			publicBody := public.Render()
			if err := files.WriteAtomic(ctx, tenantEnv, publicBody, 0o640); err != nil {
				return "", err
			}
			publicSHA = sha256Hex(publicBody)
			if len(movedKeys) > 0 {
				secretsBody := secrets.Render()
				if err := files.WriteAtomic(ctx, secretsEnv, secretsBody, 0o640); err != nil {
					return "", err
				}
				secretsSHA = sha256Hex(secretsBody)
			}
			sc.Logf("%d normalization(s); %d secret key(s) moved", len(notes), len(movedKeys))
			return fmt.Sprintf("normalized (%d change(s))", len(notes)), nil
		},
	})
	p.addEnvLayoutStep(&movedKeys, &publicSHA, &secretsSHA)
	p.result["pending_until_restart"] = true
	return nil
}

// addEnvLayoutStep records what the split actually did.
//
// `env_layout` is the registry's word for WHERE this tenant's secrets live,
// and nothing wrote it after adoption: `env normalize` moved the keys into
// secrets.env and left the row saying `legacy`, so on hackathon the field
// still read `legacy` over a tenant whose secrets had been split for days.
// Everything downstream believes that field — `tenant backup` decides from it
// whether tenant.env is a public file it may copy in the clear, the handover
// runbook reads it as a prerequisite, `tenant show` prints it — so the op that
// performs the split is the op that has to record it.
//
// The two checksums go with it: they are what doctor compares the files
// against, and a normalize that rewrote both files and left the old sums in
// the row would raise a drift finding for its own work.
func (p *planner) addEnvLayoutStep(movedKeys *[]string, publicSHA, secretsSHA *string) {
	name := p.tenant
	p.add(step{
		Kind: "registry", Title: "record env_layout and the new file checksums", Targets: []string{name},
		WouldWrite: []model.WouldWrite{{Path: p.oc.Roots.Registry(), Mode: "0660", Preview: model.NullString("")}},
		Warnings: []string{"env_layout becomes `managed` when — and only when — a secret-class key actually moved " +
			"into secrets.env; a tenant that had none stays `legacy`, which is the truth about it"},
		Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			moved := map[string]bool{}
			for _, k := range *movedKeys {
				moved[k] = true
			}
			layout := p.t.EnvLayout
			err := p.saveTenant(sc, "env-normalize", func(t *registry.Tenant) error {
				if len(moved) > 0 || t.SecretsFileSHA256 != "" {
					t.EnvLayout = envLayoutManaged
				}
				layout = t.EnvLayout
				if *publicSHA != "" {
					t.EnvFileSHA256 = *publicSHA
				}
				if *secretsSHA != "" {
					t.SecretsFileSHA256 = registry.NullString(*secretsSHA)
				}
				// A secret_ref still pointing at tenant.env after its key has
				// moved is a pointer at a file that no longer holds it, which
				// is what `backup` follows when it seals the secrets.
				for i := range t.SecretRefs {
					if moved[t.SecretRefs[i].Key] {
						t.SecretRefs[i].File = "secrets.env"
					}
				}
				return nil
			})
			if err != nil {
				return "", err
			}
			p.result["env_layout"] = layout
			p.result["secrets_moved"] = len(moved)
			return fmt.Sprintf("%s env_layout = %s (%d secret key(s) in secrets.env)", name, layout, len(moved)), nil
		},
	})
}

// ---------------------------------------------------------------- env pg-password

// pgPasswordKeys are the connection strings the role password can be read out
// of, in the order they are tried. All three carry the same password on a
// tenant `new-tenant.sh` provisioned; a tenant where they DISAGREE is a tenant
// nobody can pick a password for, and this op refuses rather than guessing.
var pgPasswordKeys = []string{"POSTGRES_DSN", "USER_STORE_DSN", "COLLECTION_STORE_DSN"}

// upShPasswordRe finds the literal in the generated `bin/up.sh`
// (`--env POSTGRES_PASSWORD=…`, or a plain assignment). It is the LAST resort:
// a tenant whose DSNs carry no password at all.
//
// Three alternatives rather than a back-referenced quote: RE2 has no
// back-references, and spelling the single-quoted, double-quoted and bare
// forms out is also the only version of this that cannot match across a
// closing quote.
var upShPasswordRe = regexp.MustCompile(`POSTGRES_PASSWORD=(?:'([^']*)'|"([^"]*)"|([^\s'";]+))`)

// planEnvPGPassword makes the tenant's own postgres startable by the control
// plane, and changes nothing else.
//
// The instance supervisor starts `postgres-<manifest>` with the role password
// in `APPTAINERENV_POSTGRES_PASSWORD`, read out of secrets.env at run time
// (ops/supervisor.go). A tenant provisioned by `apptainer/new-tenant.sh` has
// it under NO name the supervisor looks for: the literal is inside the
// generated `bin/up.sh` and inside the connection strings, and provision.env
// carries the host and the port but not the password. So a handover of such a
// tenant stops everything and then cannot start its postgres — which is why
// this op is a PREPARATION step, run days before, and why the release checks
// its result before it stops anything.
//
// It is idempotent (a secrets.env that already carries the key is left exactly
// as it is), it keeps a timestamped backup of what was there, and it never
// prints, logs, checkpoints or previews the value.
func planEnvPGPassword(_ context.Context, p *planner, _ map[string]any) error {
	p.need(model.LockTenant)
	if p.t.Stores.Postgres.Kind != registry.PostgresKindLocal {
		return p.refuse("%s runs no postgres server of its own (stores.postgres.kind is %s): there is no role "+
			"password to write", p.t.Name, p.t.Stores.Postgres.Kind)
	}
	secretsEnv := p.tpaths.SecretsEnv
	upSh := filepath.Join(p.t.DataDir, "bin", "up.sh")
	p.addFor("files", step{
		Kind: "envfile", Title: "derive " + render.APPTAINERENVPostgresPassword + " into secrets.env",
		Targets:    []string{secretsEnv},
		WouldWrite: []model.WouldWrite{secretWrite(secretsEnv, "0640")},
		Warnings: []string{"the value is derived from the connection strings ALREADY in secrets.env and is never " +
			"printed, logged or previewed; the previous content is kept beside it as `.bak-pg-password-<ts>`",
			"idempotent: a secrets.env that already carries the key is not rewritten at all"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			files := sc.Ops.Drivers.Files()
			b, err := files.ReadFile(ctx, secretsEnv)
			if err != nil {
				return "", fmt.Errorf("reading %s: %w", secretsEnv, err)
			}
			f, _, err := envfile.ParseLenient(b)
			if err != nil {
				return "", fmt.Errorf("%w: %s does not parse: %v", jobs.ErrRefused, secretsEnv, err)
			}
			if v, ok := f.Get(render.APPTAINERENVPostgresPassword); ok && strings.TrimSpace(v) != "" {
				sc.Logf("%s already carries %s; nothing to do", secretsEnv, render.APPTAINERENVPostgresPassword)
				p.result["written"] = false
				return "already present", nil
			}
			pw, from, err := derivePGPassword(ctx, sc, f, upSh)
			if err != nil {
				return "", err
			}
			if err := f.Set(render.APPTAINERENVPostgresPassword, pw); err != nil {
				return "", err
			}
			bak := secretsEnv + ".bak-pg-password-" + p.stampOf(sc)
			if err := sc.Checkpoint("file:"+bak, "file:"+secretsEnv); err != nil {
				return "", err
			}
			if err := files.WriteAtomic(ctx, bak, b, 0o640); err != nil {
				return "", fmt.Errorf("writing the backup %s: %w", bak, err)
			}
			if err := files.WriteAtomic(ctx, secretsEnv, f.Render(), 0o640); err != nil {
				return "", err
			}
			sc.Logf("%s written from %s (backup %s)", render.APPTAINERENVPostgresPassword, from, filepath.Base(bak))
			p.result["written"] = true
			p.result["derived_from"] = from
			return "derived from " + from, nil
		},
		Rollback: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			bak, ok := externalIDValue(sc.Step.ExternalIDs, "file:")
			if !ok || !strings.Contains(bak, ".bak-pg-password-") {
				return "nothing was written", nil
			}
			b, err := sc.Ops.Drivers.Files().ReadFile(ctx, bak)
			if err != nil {
				return "", err
			}
			return "restored " + secretsEnv, sc.Ops.Drivers.Files().WriteAtomic(ctx, secretsEnv, b, 0o640)
		},
	})
	p.warn("this is a PREPARATION op: nothing is restarted, and the running postgres is untouched. It exists so " +
		"that the handover's take can start `" + instanceNameFor(render.LegPostgres, p.t.ManifestName) + "` at all")
	return nil
}

// derivePGPassword reads the role password out of what is already on disk.
//
// The DSNs first, and all of them: they are the tenant's own connection
// strings, so a password read from them is by construction the one postgres is
// running with. Disagreement is a REFUSAL — a tenant whose three DSNs carry
// two passwords is one where picking either would start a server the other
// half of the tenant cannot log into. `bin/up.sh` is the last resort, for a
// tenant whose DSNs carry no password at all.
//
// Nothing here is logged: the error names the FILE and the KEY, never a value.
func derivePGPassword(ctx context.Context, sc *jobs.StepContext, f *envfile.File, upSh string) (string, string, error) {
	seen := map[string][]string{}
	for _, key := range pgPasswordKeys {
		v, ok := f.Get(key)
		if !ok || strings.TrimSpace(v) == "" {
			continue
		}
		u, err := url.Parse(strings.TrimSpace(v))
		if err != nil || u.User == nil {
			continue
		}
		pw, set := u.User.Password()
		if !set || pw == "" {
			continue
		}
		seen[pw] = append(seen[pw], key)
	}
	switch len(seen) {
	case 1:
		for pw, keys := range seen {
			sort.Strings(keys)
			return pw, "the password in " + strings.Join(keys, ", "), nil
		}
	default:
		if len(seen) > 1 {
			var where []string
			for _, keys := range seen {
				sort.Strings(keys)
				where = append(where, strings.Join(keys, "+"))
			}
			sort.Strings(where)
			return "", "", fmt.Errorf("%w: the connection strings disagree about the role password (%s carry "+
				"different ones). Fix them first — a password picked from one of them would start a server the "+
				"rest of the tenant cannot log into", jobs.ErrRefused, strings.Join(where, " and "))
		}
	}
	// The generated up.sh, for a tenant whose DSNs carry none.
	b, err := sc.Ops.Drivers.Files().ReadFile(ctx, upSh)
	if err != nil {
		return "", "", fmt.Errorf("%w: no connection string in secrets.env carries a password and %s could not be "+
			"read (%v), so there is nowhere to derive one from. Write %s into secrets.env by hand",
			jobs.ErrRefused, upSh, err, render.APPTAINERENVPostgresPassword)
	}
	m := upShPasswordRe.FindSubmatch(b)
	if m == nil {
		return "", "", fmt.Errorf("%w: neither the connection strings in secrets.env nor %s carries a postgres "+
			"password, so there is nowhere to derive one from", jobs.ErrRefused, upSh)
	}
	// Exactly one of the three alternatives matched.
	for _, g := range m[1:] {
		if len(g) > 0 {
			return string(g), filepath.Base(upSh), nil
		}
	}
	return "", "", fmt.Errorf("%w: %s names POSTGRES_PASSWORD with an empty value", jobs.ErrRefused, upSh)
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
	// FLAT under the units dir, exactly where `create` puts them: the
	// SYSTEMD_UNIT_PATH drop-in names that directory and systemd does not
	// search it recursively, so a per-tenant subdirectory would be a unit
	// file no manager ever finds.
	dir := p.oc.Roots.UnitsDir()
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
