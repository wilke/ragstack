// Package ops is one Op per verb: the half of the job engine that knows what
// a verb MEANS.
//
// An Op does exactly three things, and the split is the point:
//
//   - Validate(args) enforces contracts/ctl/openapi.yaml's `x-ctl-op-args`
//     for the verb — nothing about the host, so a malformed request is
//     refused before any lock is taken.
//   - Plan(ctx, oc, args) is a PURE function of the registry snapshot, the
//     doctor findings and the args. The engine calls it twice (once for the
//     answer, once under the locks) and refuses the job when the two plans
//     differ, so a Plan that consults the clock or the host would make every
//     job `plan_stale`.
//   - the steps a plan carries RUN through jobs.Drivers and nothing else.
//
// Where an operation is not allowed, the refusal happens HERE, at plan time,
// with a sentence that says what to do instead (`hand-started tenant;
// handover first`). Where an operation is allowed but its driver lands in
// PR-D, the plan is rendered in full and the refusal comes from the driver at
// run time — because a dry run that cannot show you the plan is worse than a
// dry run whose last steps say where they stop.
package ops

import (
	"context"
	"fmt"
	"sort"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/version"
)

// Deps are what the op registry needs to exist. Roots spells every path the
// plans name; Now is the clock the RUN halves stamp names with (a plan never
// reads it — see the package comment).
type Deps struct {
	Roots paths.Roots
	Now   func() time.Time
	// SaveFleet persists a registry change (settings-put, and every op whose
	// step writes a registry row). Nil refuses those steps with ErrRefused —
	// a plan still renders, the run says the registry writer is not wired.
	SaveFleet func(*registry.Fleet) error
	// Mirror is the bare `git clone --mirror` every artifact worktree is
	// checked out of (/rag/repos/ragstack.git). It is deployment
	// configuration, not an op argument: an operation that took a repository
	// path from a request would be an operation that chooses which code a
	// tenant runs. `fleet artifact prepare` may name a different one, because
	// it is CLI-only and trusted-operator; `tenant create` never can — it
	// checks out the sha an already-prepared artifact recorded, from here.
	//
	// Empty means no mirror is configured: the plan still renders in full and
	// the git step refuses at run time, saying which item is missing.
	Mirror string
}

func (d Deps) now() time.Time {
	if d.Now != nil {
		return d.Now()
	}
	return time.Now()
}

// opRegistry is the jobs.Registry this package builds.
type opRegistry struct {
	ops map[string]jobs.Op
}

// NewRegistry returns every verb the control plane knows: the twenty of the
// ops endpoint's enum plus `create`, `gateway-apply` and `gateway-reload`,
// which are their own endpoints rather than verbs on a tenant.
func NewRegistry(d Deps) jobs.Registry {
	r := &opRegistry{ops: map[string]jobs.Op{}}
	add := func(verb string, destructive bool, plan planFunc) {
		spec, ok := argSchemas[verb]
		if !ok {
			panic("ops: no args schema for verb " + verb) // unreachable: both tables are in this package
		}
		r.ops[verb] = &op{verb: verb, destructive: destructive, spec: spec, plan: plan, deps: d}
	}

	// The destructive set is the plan's: an op that stops a service, destroys
	// or moves state, or withdraws a credential. `--yes-destructive <name>`
	// (confirm = the tenant name) is required for each.
	add("start", false, planStart)
	add("stop", true, planStop)
	add("restart", true, planRestart)
	add("backup", false, planBackup)
	add("restore", true, planRestore)
	add("handover", true, planHandover)
	add("migrate-local", true, planMigrateLocal)
	add("decommission", true, planDecommission)
	add("key-mint", false, planKeyMint)
	add("key-revoke", true, planKeyRevoke)
	add("admin-add", false, planAdminAdd)
	add("admin-remove", true, planAdminRemove)
	add("sa-create", false, planSACreate)
	add("sa-disable", true, planSADisable)
	add("sa-enable", false, planSAEnable)
	add("env-set", false, planEnvSet)
	add("env-unset", true, planEnvUnset)
	add("env-normalize", false, planEnvNormalize)
	add("render-units", false, planRenderUnits)
	add("update-code", true, planUpdateCode)
	add("create", false, planCreate)
	// artifact-prepare is CLI-ONLY: it is not in the ops endpoint's verb enum
	// (api/jobs.go's opVerbs), so POST …/ops/artifact-prepare is 422 like any
	// other name that is not a verb. It runs `npm ci`, which is the one step in
	// the whole control plane that reaches the network, and it decides which
	// code a tenant may later be created from — both are trusted-operator,
	// `--direct` decisions and neither belongs on an HTTP surface a session can
	// reach.
	add("artifact-prepare", false, planArtifactPrepare)
	add("gateway-apply", false, planGatewayApply)
	add("gateway-reload", false, planGatewayReload)
	add("settings-put", false, planSettingsPut)
	return r
}

func (r *opRegistry) Lookup(verb string) (jobs.Op, bool) {
	o, ok := r.ops[verb]
	return o, ok
}

func (r *opRegistry) Verbs() []string {
	out := make([]string, 0, len(r.ops))
	for v := range r.ops {
		out = append(out, v)
	}
	sort.Strings(out)
	return out
}

// planFunc is one verb's planner.
type planFunc func(ctx context.Context, p *planner, args map[string]any) error

// op is every verb: the same three methods, a different plan.
type op struct {
	verb        string
	destructive bool
	spec        argSpec
	plan        planFunc
	deps        Deps
}

func (o *op) Verb() string      { return o.verb }
func (o *op) Destructive() bool { return o.destructive }

// CreatesTenant satisfies the engine's optional tenantCreator interface:
// `create`'s request names a tenant that does not exist yet, and every other
// verb's names one that must. The planner is where "that name is taken" is
// refused; the engine only needs to know not to 404 first.
func (o *op) CreatesTenant() bool { return o.verb == "create" }

func (o *op) Validate(a map[string]any) error {
	if a == nil {
		a = map[string]any{}
	}
	return o.spec.validate(a)
}

func (o *op) Plan(ctx context.Context, oc jobs.Context, args map[string]any) (*jobs.Planned, error) {
	if args == nil {
		args = map[string]any{}
	}
	if err := o.spec.validate(args); err != nil {
		return nil, err
	}
	p := newPlanner(o, oc, args)
	if err := o.plan(ctx, p, args); err != nil {
		return nil, err
	}
	return p.planned(), nil
}

// ---------------------------------------------------------------- planner

// planner accumulates the steps of one plan and the facts they share.
type planner struct {
	op   *op
	oc   jobs.Context
	args map[string]any

	tenant   string
	tpaths   paths.Tenant
	t        *registry.Tenant
	steps    []jobs.Step
	warnings []string
	locks    []model.LockName
	secrets  func() []model.Secret
	result   map[string]any
}

func newPlanner(o *op, oc jobs.Context, args map[string]any) *planner {
	p := &planner{op: o, oc: oc, args: args, result: map[string]any{}}
	if oc.Tenant != nil {
		p.t = oc.Tenant
		p.tenant = oc.Tenant.Name
		p.tpaths = paths.TenantPaths(oc.Roots, oc.Tenant.Name, oc.Tenant.ManifestName)
	}
	return p
}

// stampFormat is the timestamp component of every generated name (bundle
// ids, snapshot repos, .bak- suffixes).
const stampFormat = "20060102T150405Z"

// stampOf is the RUN-time clock: the engine's Context clock when it set one,
// else the registry's own. A plan must never call it (see the package
// comment); the steps do.
func (p *planner) stampOf(sc *jobs.StepContext) string {
	if sc != nil && sc.Ops.Now != nil {
		return sc.Ops.Now().UTC().Format(stampFormat)
	}
	return p.op.deps.now().UTC().Format(stampFormat)
}

// need records the locks this op takes. The engine orders them.
func (p *planner) need(locks ...model.LockName) { p.locks = append(p.locks, locks...) }

// warn adds a plan-level warning.
func (p *planner) warn(format string, a ...any) {
	p.warnings = append(p.warnings, fmt.Sprintf(format, a...))
}

// refuse is the plan-time "no": a capability, ownership, containment or
// fencing rule, answered 409 `refused`.
func (p *planner) refuse(format string, a ...any) error {
	return fmt.Errorf("%w: %s", jobs.ErrRefused, fmt.Sprintf(format, a...))
}

// step is the spec of one planned step: what it says it will do, and the two
// or three functions that do it.
type step struct {
	Kind        string
	Title       string
	Destructive bool
	Targets     []string
	WouldWrite  []model.WouldWrite
	WouldRun    []model.WouldRun
	Warnings    []string
	Run         jobs.StepFunc
	Rollback    jobs.StepFunc
	Reconcile   func(ctx context.Context, sc *jobs.StepContext) (jobs.Reconciliation, error)
	Cutover     bool
}

// add appends a step, numbering it.
func (p *planner) add(s step) {
	run := s.Run
	if run == nil {
		run = func(context.Context, *jobs.StepContext) (string, error) { return "nothing to do", nil }
	}
	p.steps = append(p.steps, jobs.Step{
		Plan: model.PlannedStep{
			N:           len(p.steps) + 1,
			Kind:        s.Kind,
			Title:       s.Title,
			Destructive: s.Destructive,
			Targets:     nonNilStrings(s.Targets),
			WouldWrite:  nonNilWrites(s.WouldWrite),
			WouldRun:    nonNilRuns(s.WouldRun),
			Warnings:    nonNilStrings(s.Warnings),
		},
		Run:       run,
		Rollback:  s.Rollback,
		Reconcile: s.Reconcile,
		Cutover:   s.Cutover,
	})
}

// skip records a step the plan will NOT take, and why. It is a step rather
// than a warning because the operator asked for something and has to see it
// answered: "qdrant is shared, capabilities.stop is false, so it is left
// running" is an outcome, not a footnote.
func (p *planner) skip(kind, title, why string, targets ...string) {
	p.add(step{
		Kind: kind, Title: title, Targets: targets, Warnings: []string{why},
		Run: func(_ context.Context, sc *jobs.StepContext) (string, error) {
			sc.Logf("skipped: %s", why)
			return "skipped: " + why, nil
		},
	})
}

// planned assembles the contract Plan and the executable steps.
func (p *planner) planned() *jobs.Planned {
	steps := make([]model.PlannedStep, 0, len(p.steps))
	for _, s := range p.steps {
		steps = append(steps, s.Plan)
	}
	tenant := model.NullString(p.tenant)
	confirm := model.NullString("")
	if p.op.destructive {
		// The plan's rule: a destructive tenant op is confirmed by typing the
		// tenant's name (the CLI's `--yes-destructive <name>`); a fleet-scoped
		// one by typing what it acts on.
		if p.tenant != "" {
			confirm = model.NullString(p.tenant)
		} else {
			confirm = model.NullString("gateway")
		}
	}
	var regGen int64
	if p.oc.Fleet != nil {
		regGen = p.oc.Fleet.Generation
	}
	plan := model.Plan{
		Op:                 p.op.verb,
		Tenant:             tenant,
		RegistryGeneration: regGen,
		SchemaVersion:      version.SchemaVersion,
		Doctor:             p.oc.Doctor,
		RequiresConfirm:    p.op.destructive,
		ConfirmValue:       confirm,
		Steps:              steps,
		Warnings:           nonNilStrings(p.warnings),
	}
	// The digest is jobs.PlanHash's, not a second implementation of the same
	// spec. The engine recomputes it under the locks and refuses when the two
	// differ, so a planner that hashed the plan its OWN way would make
	// `plan_stale` fire on every job — or, worse, not fire when the plan
	// really had moved.
	if h, err := jobs.PlanHash(plan, redactArgs(p.oc, p.args)); err == nil {
		plan.PlanHash = h
	} else {
		p.warn("the plan hash could not be computed: " + err.Error())
	}
	result := p.result
	return &jobs.Planned{
		Plan:    plan,
		Steps:   p.steps,
		Locks:   p.locks,
		Secrets: p.secrets,
		Result:  func() map[string]any { return result },
	}
}

// ---------------------------------------------------------------- previews

// maxPreview is plan.json's cap on would_write[].preview.
const maxPreview = 64 << 10

// preview renders a file the plan would write, through the redactor and
// capped. It is the ONLY way an op puts file content in a plan.
func (p *planner) preview(path string, mode string, content []byte) model.WouldWrite {
	text := string(content)
	if p.oc.Redactor != nil {
		text = p.oc.Redactor.Redact(text)
	}
	if len(text) > maxPreview {
		text = text[:maxPreview]
	}
	return model.WouldWrite{Path: path, Mode: mode, Preview: model.NullString(text)}
}

// secretWrite is a file whose CONTENT is a credential: the plan names the
// path and the mode and nothing else, because plan.json's preview is null for
// secret-bearing content and because a preview is exactly the place a minted
// key would leak into an audit row.
func secretWrite(path, mode string) model.WouldWrite {
	return model.WouldWrite{Path: path, Mode: mode, Preview: model.NullString("")}
}

func redactArgs(oc jobs.Context, args map[string]any) map[string]any {
	if oc.Redactor == nil {
		return args
	}
	return oc.Redactor.RedactArgs(args)
}

// ---------------------------------------------------------------- misc

func nonNilStrings(s []string) []string {
	if s == nil {
		return []string{}
	}
	return s
}

func nonNilWrites(w []model.WouldWrite) []model.WouldWrite {
	if w == nil {
		return []model.WouldWrite{}
	}
	return w
}

func nonNilRuns(r []model.WouldRun) []model.WouldRun {
	if r == nil {
		return []model.WouldRun{}
	}
	return r
}
