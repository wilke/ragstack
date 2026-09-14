package ops

import (
	"context"
	"fmt"
	"sort"
	"strconv"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/render"
	"github.com/ragstack/ragstack/internal/ctl/settings"
)

// ---------------------------------------------------------------- create

func planCreate(_ context.Context, p *planner, args map[string]any) error {
	p.need(model.LockRegistry, model.LockManifest, model.LockTenant, model.LockGateway)
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
	if tf := argStringOf(args, "template_from"); tf != "" {
		if _, ok := f.Tenants[tf]; !ok {
			return p.refuse("template_from names %q, which is not a tenant", tf)
		}
	}
	// `settings` is public keys only — the same rule the env API enforces,
	// applied at the one moment a whole file is composed from a request.
	set := map[string]string{}
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

	index, base := registry.Allocate(f)
	t := prospectiveTenant(p.oc.Roots, f, name, artifactID, artifact, index, base, provider, argStringOf(args, "ui_mode"), set)
	p.t, p.tenant = t, name
	p.tpaths = paths.TenantPaths(p.oc.Roots, name, name)

	envBody, err := render.TenantEnv(t, render.EnvOptions{DryRun: true})
	if err != nil {
		return p.refuse("%s's tenant.env cannot be rendered as requested: %v", name, err)
	}
	units, err := render.Units(t, render.UnitConfig{RagRoot: p.oc.Roots.RagRoot, CtlStateDir: p.oc.Roots.CtlStateDir})
	if err != nil {
		return p.refuse("%s's units cannot be rendered as requested: %v", name, err)
	}

	// ---- allocate -------------------------------------------------------
	manifest := string(registry.ProjectManifest(f)) + fmt.Sprintf("%s\t%d\t%d\n", name, index, base)
	p.add(step{
		Kind: "registry", Title: fmt.Sprintf("allocate %s: index %d, ports %d–%d", name, index, base, base+f.PortStride-1),
		Targets:    []string{name, strconv.Itoa(index), strconv.Itoa(base)},
		WouldWrite: []model.WouldWrite{p.preview(p.oc.Roots.Manifest(), "0660", []byte(manifest))},
		Warnings: []string{"the allocation is tombstone-aware: the index is one past the highest index any tenant or " +
			"tombstone has ever held, so a decommissioned block is never reused"},
		// The registry write itself lands in PR-D: it has to stamp the
		// generation, re-check it under the lock, validate against the
		// contract and rewrite manifest.tsv AND generation.json as one act,
		// and doing three quarters of that through the Files driver would
		// leave the projection and the registry disagreeing. The PLAN is
		// complete, so a dry run shows the whole tenant it would create.
		Run: p.pendingRun("registry", "Save"),
	})

	// ---- files ----------------------------------------------------------
	p.add(step{
		Kind: "fs", Title: "create the tenant directories (2770)", Targets: p.tpaths.ProvisionDirs(),
		Run: p.pendingRun("files", "MkdirAll"),
	})
	p.add(step{
		Kind: "fs", Title: "write secrets.env (write-once) with the minted keys", Targets: []string{p.tpaths.SecretsEnv},
		WouldWrite: []model.WouldWrite{secretWrite(p.tpaths.SecretsEnv, "0640")},
		Warnings:   []string{"the keys are minted when the job runs and delivered once through the job's secrets envelope"},
		Run:        p.pendingRun("files", "WriteOnce"),
	})
	p.add(step{
		Kind: "envfile", Title: "write tenant.env", Targets: []string{p.tpaths.TenantEnv},
		WouldWrite: []model.WouldWrite{p.preview(p.tpaths.TenantEnv, "0640", envBody)},
		Warnings:   []string{"the preview renders <GENERATED:…> placeholders where the minted values go"},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			if err := sc.Checkpoint("file:" + p.tpaths.TenantEnv); err != nil {
				return "", err
			}
			return p.tpaths.TenantEnv, sc.Ops.Drivers.Files().WriteAtomic(ctx, p.tpaths.TenantEnv, envBody, 0o640)
		},
	})
	p.add(step{
		Kind: "git", Title: "check the worktree out from artifact " + artifactID,
		Targets: []string{t.Worktree, artifact.SHA}, Run: p.pendingRun("git", "Worktree"),
	})
	p.add(step{
		Kind: "apptainer", Title: "build the tenant UI from the artifact", Targets: []string{t.DataDir + "/ui/dist"},
		Run: p.pendingRun("exec", "ViteBuild"),
	})
	p.addUnitsWrite(units)
	p.addFor("systemd", step{
		Kind: "systemd", Title: "systemctl --user daemon-reload",
		WouldRun: []model.WouldRun{{Argv: []string{"/usr/bin/systemctl", "--user", "daemon-reload"}}},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return "daemon-reload", sc.Ops.Drivers.Systemd().DaemonReload(ctx)
		},
	})
	legs, _ := p.legs(nil)
	for _, c := range legs {
		if c.Managed {
			p.addUnitStep("enable", c)
		}
	}
	if boolArgOrDefault(args, "start", true) {
		for _, c := range legs {
			if c.Managed {
				p.addUnitStep("start", c)
			}
		}
		p.addReadyStep(legs)
	}
	origin := fmt.Sprintf("http://127.0.0.1:%d", t.Ports.API)
	for _, sa := range serviceAccountArgs(args) {
		sa := sa
		p.addFor("tenantapi", step{
			Kind: "tenantapi", Title: "register the service account " + sa.Subject, Targets: []string{sa.Subject, origin},
			Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
				if err := sc.Checkpoint("sa:create:" + sa.Subject); err != nil {
					return "", err
				}
				// The API key is empty here: the bootstrap admin key is minted
				// by the secrets step of the real create (PR-D), which holds it
				// in memory for the length of the job and hands it to this call.
				// Until that lands the driver refuses anyway, so an empty
				// credential cannot reach a tenant.
				return "created " + sa.Subject, sc.Ops.Drivers.TenantAPI().ServiceAccount(
					ctx, origin, "", sa.Subject, sa.Role, sa.Purpose, "create")
			},
		})
	}
	if boolArgOrDefault(args, "gateway", true) {
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
	}
	p.addFor("tenantapi", step{
		Kind: "probe", Title: "post-checks: /health and the gateway route", Targets: []string{origin},
		Run: func(ctx context.Context, sc *jobs.StepContext) (string, error) {
			return "health ok", sc.Ops.Drivers.TenantAPI().Health(ctx, origin)
		},
	})
	p.result["name"] = name
	p.result["index"] = index
	p.result["ports"] = t.Ports
	p.warn("rollback is bounded to the paths this job created; nothing outside them is touched")
	return nil
}

// prospectiveTenant is the registry row `create` WOULD write. It is built at
// plan time so the plan can render the tenant's real env file and units —
// a dry run that cannot show those is a dry run of nothing.
func prospectiveTenant(roots paths.Roots, f *registry.Fleet, name, artifactID string, a *registry.Artifact,
	index, base int, provider, uiMode string, set map[string]string) *registry.Tenant {
	tp := paths.TenantPaths(roots, name, name)
	t := registry.NewTenant(name, name)
	t.DataDir, t.Worktree, t.PythonEnv = tp.DataDir, tp.Worktree, a.PythonEnv
	t.ArtifactID = registry.NullString(artifactID)
	t.Code = registry.Code{Tag: a.Tag, SHA: registry.NullString(a.SHA)}
	t.Ports = paths.BlockAt(f.PortBase, f.PortStride, index)
	t.API = registry.API{Bind: "127.0.0.1", PidFile: tp.PidFile, Log: tp.APILog}
	if uiMode == "" {
		uiMode = registry.UIModeStatic
	}
	t.UI = registry.UI{Mode: uiMode, Base: "/ragstack/" + name + "/ui/"}
	if uiMode == registry.UIModeDev {
		t.UI.Port = registry.NullPort(base + 10)
	}
	t.Supervisor, t.Owner, t.State = supervisorSystemd, "svcbvbrc", "provisioned"
	t.DesiredBoot, t.EnvLayout = "enabled", "managed"
	t.Identity = registry.Identity{Provider: provider}
	t.Settings = set
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
		ExtraEnv: map[string]string{},
	}
	return t
}

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
