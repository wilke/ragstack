package api

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/user"
	"sort"
	"strings"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/doctor"
	"github.com/ragstack/ragstack/internal/ctl/drivers"
	"github.com/ragstack/ragstack/internal/ctl/envfile"
	"github.com/ragstack/ragstack/internal/ctl/fleet"
	"github.com/ragstack/ragstack/internal/ctl/hostfacts"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/ops"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/settings"
)

// ErrEngineNotWired is what BuildEngine returns until the store, the op
// registry and the drivers are connected to it at integration.
//
// It is an ERROR rather than a nil engine because a daemon that silently
// served a mutation surface backed by nothing would answer 200-shaped
// refusals no operator could distinguish from a policy decision. `serve` logs
// it and carries on with a nil engine, and every mutation then answers the
// contract's 409 `refused` with this text in the detail — which is a fact
// about the build, not about the fleet.
var ErrEngineNotWired = errors.New("job engine not wired")

// EngineConfig is everything BuildEngine needs to assemble the real engine.
// It is the ONE place the api package names the three implementations
// (jobs.Store, ops.Registry, drivers), so the HTTP layer and the --direct CLI
// construct the same engine from the same inputs rather than two engines that
// agree by coincidence.
type EngineConfig struct {
	// Roots is the deployment layout every path derives from.
	Roots paths.Roots
	// RegistryPath is registry.json; the engine re-reads it per plan, because
	// a plan is only valid against the generation it was computed on.
	RegistryPath string
	// StorePath is the SQLite jobs database, <state dir>/jobs.db.
	StorePath string
	// Mode distinguishes the daemon from a --direct CLI run. Both take the
	// same flock files in the same order; the mode is recorded on the job so
	// an operator can see which process owns it.
	Mode model.WorkerMode
	// Host is what job.worker.host records.
	Host string
	// FakeDrivers selects drivers.NewFake over drivers.NewReal: every verb
	// plans and executes end to end without touching a host, which is what
	// the conformance suite runs against.
	FakeDrivers bool
	// SecretsTTL is how long a delivery envelope lives (contract: 15 minutes).
	SecretsTTL time.Duration
	// Logger is the daemon's REDACTING logger. Never slog.Default(): a step
	// log is host text that has not been vetted.
	Logger *slog.Logger
	// Now is injected so a test can pin job timestamps.
	Now func() time.Time
	// LoadFleet and SaveFleet are where the engine reads and writes the
	// registry. Nil means the file at RegistryPath (registry.LoadNoRepair /
	// registry.Save); the --fake-drivers daemon points them at its in-memory
	// fixture so the mutation surface is exercised end to end without a host.
	LoadFleet func() (*registry.Fleet, error)
	SaveFleet func(*registry.Fleet) error
	// The host programs and directories the real drivers use, all absolute.
	// Empty takes drivers.NewReal's default for each. SetHostToolsFromEnv
	// fills them from the CTL_* variables, and both the daemon and the
	// --direct CLI call it, so the two build the same driver set.
	Systemctl string
	Git       string
	Node      string
	Npm       string
	Apptainer string
	Crontab   string
	Mirror    string
	NpmCache  string
	// MountPoint is what a rendered unit's `ConditionPathIsMountPoint` names.
	// Empty means Roots.RagRoot. Only a run against a SANDBOX root sets it —
	// `ragstack-ctl selftest --rag-root <scratch>` — where the paths move into
	// the scratch tree and the mount the units wait for is still /rag.
	MountPoint string
	// Owner is the account this process runs as, recorded as the owner of
	// every tenant it creates. Empty means the current OS user.
	Owner string
	// DefaultSupervisor is CTL_DEFAULT_SUPERVISOR: the supervisor a `tenant
	// create` that names none gets (PR-D2). Empty means the contract's
	// default, `systemd`. SetHostToolsFromEnv fills it, so the daemon and the
	// --direct CLI resolve it the same way.
	DefaultSupervisor string
	// Doctor is the op-scoped doctor a plan pins. Nil means the host doctor
	// over LoadFleet; the daemon passes its Backend's Doctor so the hash a
	// plan carries is the hash the dashboard shows (and, with fake drivers,
	// the fixture's doctor rather than this host's).
	Doctor func(ctx context.Context, tenant, op string) (model.DoctorResponse, error)
}

// DefaultSecretsTTL is secrets_response.json's "lives 15 minutes".
const DefaultSecretsTTL = 15 * time.Minute

// BuildEngine assembles the job engine: the SQLite store, the op registry and
// the drivers, behind jobs.Engine. The HTTP daemon and the --direct CLI both
// come through here, so they run the same engine over the same jobs.db and
// the same flock files.
//
// The Sealer is nil in PR-C: minted secrets live only in daemon memory for
// their TTL (age sealing to the backup recipients lands with PR-D's bundle
// encryption).
func BuildEngine(cfg EngineConfig) (jobs.Engine, error) {
	eng, _, err := BuildEngineAndDrivers(cfg)
	return eng, err
}

// BuildEngineAndDrivers is BuildEngine, and it also hands back the driver set
// the engine was built with.
//
// One caller needs both: `ragstack-ctl selftest` submits jobs through the
// engine and then asks the HOST what happened — is anything still listening on
// the sandbox block, does systemd still know these units, is the quarantined
// directory there. Those questions have to be put to the SAME host the jobs
// ran against, or a selftest against `--fake-drivers` would be interrogating a
// second, empty fixture and reporting it as the truth.
//
// It is a second constructor rather than an accessor on the engine because a
// jobs.Engine that could hand out its drivers would be an engine any caller
// could reach around; here the drivers are available only to whoever built the
// engine in the first place.
func BuildEngineAndDrivers(cfg EngineConfig) (jobs.Engine, jobs.Drivers, error) {
	if cfg.Now == nil {
		cfg.Now = time.Now
	}
	if cfg.Owner == "" {
		if u, err := user.Current(); err == nil {
			cfg.Owner = u.Username
		}
	}
	if cfg.SecretsTTL == 0 {
		cfg.SecretsTTL = DefaultSecretsTTL
	}
	if cfg.Logger == nil {
		cfg.Logger = slog.New(slog.NewTextHandler(io.Discard, nil))
	}
	if cfg.StorePath == "" {
		cfg.StorePath = jobs.DefaultStorePath(cfg.Roots.CtlStateDir)
	}
	store, err := jobs.NewStore(cfg.StorePath)
	if err != nil {
		return nil, nil, fmt.Errorf("job store %s: %w", cfg.StorePath, err)
	}
	loadFleet := cfg.LoadFleet
	if loadFleet == nil {
		loadFleet = func() (*registry.Fleet, error) { return registry.LoadNoRepair(cfg.RegistryPath) }
	}
	saveFleet := cfg.SaveFleet
	if saveFleet == nil {
		by := fmt.Sprintf("ragstack-ctl %s on %s", cfg.Mode, cfg.Host)
		saveFleet = func(f *registry.Fleet) error { return registry.Save(cfg.RegistryPath, f, by) }
	}

	// One redactor for the whole engine: the step logs the engine writes AND
	// the program output the drivers capture pass through the same seeded
	// table, so a secret cannot reach a log by arriving through the half that
	// was built without it.
	redactor := newEngineRedactor(cfg.Roots, loadFleet, cfg.Logger)

	var drv jobs.Drivers
	if cfg.FakeDrivers {
		// The fake files driver honours the same approved roots the real one
		// defaults to; with none, every write is a containment refusal.
		opts := drivers.FakeOptions{
			Now:   cfg.Now,
			Roots: []string{cfg.Roots.DataDir, cfg.Roots.CtlConfigDir, cfg.Roots.CtlStateDir, cfg.Roots.BackupsDir, cfg.Roots.ReposDir},
		}
		if f, err := loadFleet(); err == nil {
			// The whole in-memory host, seeded from the fixture fleet: the env
			// files AND the stores those tenants' registry rows describe, so a
			// verb that reads a collection list or moves a snapshot is running
			// against a host that matches the registry it planned from.
			opts = FixtureDrivers(cfg.Roots, f, cfg.Now)
			opts.Roots = append(opts.Roots, cfg.Roots.ReposDir)
			// The fixture host knows what a prepared artifact is: its worktree
			// has node_modules (Build.UI refuses without them, exactly as the
			// real driver does) and its sha resolves in the mirror. Without
			// these, every `tenant create` against --fake-drivers would fail on
			// a fact about the fixture rather than on anything the op did.
			opts.Refs = map[string]string{}
			for _, a := range f.Artifacts {
				opts.Installed = append(opts.Installed, a.Worktree)
				opts.Refs[a.Tag] = a.SHA
			}
		}
		// The fake systemd has no PartOf: starting a target does not bind the
		// api port, so the readiness gate of a create would time out. The next
		// unallocated blocks' API ports are pre-answered instead.
		opts.Listening = append(opts.Listening, fixtureListening(loadFleet)...)
		drv = drivers.NewFake(opts)
	} else {
		drv = drivers.NewReal(drivers.RealOptions{
			Roots:        cfg.Roots,
			Fleet:        loadFleet,
			By:           fmt.Sprintf("ragstack-ctl %s on %s", cfg.Mode, cfg.Host),
			SystemctlBin: cfg.Systemctl,
			GitBin:       cfg.Git,
			NodeBin:      cfg.Node,
			NpmBin:       cfg.Npm,
			Apptainer:    cfg.Apptainer,
			CrontabBin:   cfg.Crontab,
			Mirror:       cfg.Mirror,
			NpmCache:     cfg.NpmCache,
			Logger:       cfg.Logger,
			// The drivers redact captured stderr with the same redactor the
			// engine logs through: a `git` that quotes a URL with a token in
			// it, or a `psql` that echoes a DSN, must not reach a job log
			// just because it arrived as a program's output rather than as an
			// op's string.
			Redact: redactor.Redact,
		})
	}

	// The doctor a plan pins is the same doctor the dashboard shows: same
	// host facts, same ctl account, same external store ports.
	host := hostfacts.NewReal(cfg.Roots)
	// Resolved ONCE, and passed explicitly: three of doctor's checks
	// (`runtime_dir_missing`, `user_dropin_missing`, and the ACL grant) are
	// about a numeric uid, and a doctor left to resolve it itself answers a
	// different finding set from the CLI's — which is how an operator comes to
	// read a green run on the command line and be refused by a red one from
	// the daemon, with no way to see which finding did it.
	ctlUID := hostfacts.LookupUID(fleet.DefaultCtlUser)
	doctorFn := func(ctx context.Context, tenant, op string) (model.DoctorResponse, error) {
		f, err := loadFleet()
		if err != nil {
			return model.DoctorResponse{}, err
		}
		resp := doctor.Run(ctx, cfg.Roots, f, doctor.Options{
			Tenant:             tenant,
			Op:                 op,
			Host:               host,
			Now:                cfg.Now,
			RegistryPath:       cfg.RegistryPath,
			CtlUser:            fleet.DefaultCtlUser,
			CtlUID:             ctlUID,
			SudoersGroup:       fleet.DefaultSudoersGroup,
			ExternalStorePorts: hostfacts.DefaultExternalStorePorts,
		})
		if resp == nil {
			return model.DoctorResponse{}, errors.New("doctor returned nothing")
		}
		return *resp, nil
	}

	if cfg.Doctor != nil {
		doctorFn = cfg.Doctor
	}
	eng := jobs.NewEngine(jobs.EngineOptions{
		Store: store,
		Ops: ops.NewRegistry(ops.Deps{
			Roots: cfg.Roots, Now: cfg.Now, SaveFleet: saveFleet, Mirror: cfg.Mirror,
			MountPoint: cfg.MountPoint, Owner: cfg.Owner,
			DefaultSupervisor: cfg.DefaultSupervisor,
		}),
		Roots:        cfg.Roots,
		RegistryPath: cfg.RegistryPath,
		LoadFleet:    loadFleet,
		Drivers:      drv,
		Redactor:     redactor,
		Doctor:       doctorFn,
		// The op × finding table, as a function: the engine's yellow gate asks
		// it which of a run's warnings THIS op depends on, so that the three
		// findings this deployment is permanently yellow on (the systemd trio
		// PR-D2 abandoned) stop demanding an acknowledgement from every
		// mutation. The destination is the default one — `instance`, the only
		// supervisor a handover on this host can reach.
		PreconditionCodes: func(op string) []string {
			return doctor.RedCodesForDestination(op, "")
		},
		Now:        cfg.Now,
		Host:       cfg.Host,
		Mode:       cfg.Mode,
		SecretsTTL: cfg.SecretsTTL,
		Logger:     cfg.Logger,
	})
	return eng, drv, nil
}

// fixtureListening is the LISTEN set the fixture host starts with: the API
// port of the next few port blocks.
//
// It exists because the fake systemd is a pair of sets and knows nothing about
// `PartOf`: starting `ragstack-<t>.target` does not start the api unit that
// binds the port, so `tenant create`'s readiness gate — which waits for the
// API to answer, as it must on a real host — would wait out its whole timeout
// against the fixture. Seeding the blocks a create would ALLOCATE is the
// smallest honest way to say "on this fake host, a started tenant answers".
//
// It is deliberately the FUTURE blocks only. The fixture tenants' own ports are
// left alone, so a plan that asserts nothing is listening on an existing
// tenant's port still answers a fact about the fixture rather than this seed.
func fixtureListening(loadFleet func() (*registry.Fleet, error)) []int {
	f, err := loadFleet()
	if err != nil {
		return nil
	}
	next, _ := registry.Allocate(f)
	out := make([]int, 0, fixtureFutureBlocks)
	for i := 0; i < fixtureFutureBlocks; i++ {
		out = append(out, paths.BlockAt(f.PortBase, f.PortStride, next+i).API)
	}
	return out
}

// fixtureFutureBlocks is how many unallocated blocks the fixture pre-answers
// on. A conformance run creates a handful of tenants in one session; the limit
// only has to cover that.
const fixtureFutureBlocks = 32

// engineRedactor is the jobs.Redactor the engine applies to step logs,
// previews and audit args. Text goes through the value-seeded
// settings.Redactor (seeded from every tenant's secret files the ctl account
// can read — pre-handover that is not all of them, which is why key-shaped
// redaction runs as well); args are walked and every secret-class KEY is
// blanked by name regardless of whether its value was ever seeded.
type engineRedactor struct {
	text *settings.Redactor
}

func newEngineRedactor(roots paths.Roots, loadFleet func() (*registry.Fleet, error), log *slog.Logger) *engineRedactor {
	r := settings.NewRedactor()
	if f, err := loadFleet(); err == nil {
		for name, t := range f.Tenants {
			tp := paths.TenantPaths(roots, name, t.ManifestName)
			if err := envfile.SeedRedactor(tp.ConfigDir, r); err != nil {
				// Unreadable pre-handover files are the expected case; the
				// key-shaped pass below still covers them by name.
				log.Debug("redactor seed", "tenant", name, "err", err.Error())
			}
		}
	}
	return &engineRedactor{text: r}
}

func (e *engineRedactor) Redact(s string) string { return e.text.Redact(s) }

func (e *engineRedactor) RedactArgs(args map[string]any) map[string]any {
	// The redacted args are not just what an audit row shows — they are what
	// a RE-PLAN is computed from, because the plan hash is over
	// args_redacted and a continuation has only the stored copy. So every
	// name blanked here is a name a resume, a rebuild or a cancel of an
	// interrupted job will re-plan with the literal string "<redacted>".
	// Blank a name that IDENTIFIES the work rather than a value, and the job
	// is unresumable forever.
	secretValue := siblingKeyIsSecret(args)
	out := make(map[string]any, len(args))
	for k, v := range args {
		if isSecretArg(k) || (k == "value" && secretValue) {
			out[k] = argRedacted
			continue
		}
		out[k] = e.redactValue(k, v)
	}
	return out
}

// argRedacted is the placeholder a blanked ARG value carries.
const argRedacted = "<redacted>"

// siblingKeyIsSecret answers "is this args object an edit of a secret-class
// setting?" — env-set's `{key, value}` pair. The VALUE is a credential only
// when the KEY names one; `{key: LOG_LEVEL, value: DEBUG}` is neither, and
// redacting either half of it makes the job unresumable.
func siblingKeyIsSecret(args map[string]any) bool {
	k, ok := args["key"].(string)
	if !ok {
		return false
	}
	return settings.Classify(strings.ToUpper(strings.TrimSpace(k))) == settings.Secret
}

func (e *engineRedactor) redactValue(key string, v any) any {
	if isSecretArg(key) {
		return argRedacted
	}
	switch x := v.(type) {
	case string:
		return e.text.Redact(x)
	case map[string]any:
		return e.RedactArgs(x)
	case []any:
		res := make([]any, len(x))
		for i := range x {
			res[i] = e.redactValue(key, x[i])
		}
		return res
	default:
		return v
	}
}

// secretArgNames is the op vocabulary whose VALUE is always a credential,
// listed by exact name. `value` is not here: it is conditional on its sibling
// `key` (see siblingKeyIsSecret).
var secretArgNames = map[string]bool{
	"ctl_api_key": true,
	"token":       true,
	"secret":      true,
	"password":    true,
	"dsn":         true,
	"api_key":     true,
}

// neverSecretArgs are the arg names that IDENTIFY what an op acts on. They
// are checked first and win over everything, because the cost of getting
// them wrong is not a leak — it is a job that can never be replanned.
//
// `key` is the reason this list exists. It is env-set's SETTING NAME
// ("LOG_LEVEL"), never a credential, but it contains the substring "KEY". A
// substring test blanked it, the stored args_redacted then read
// {key: "<redacted>"}, and every rebuild — resume, cancel of an interrupted
// job — re-planned env-set for a setting called "<redacted>", which the op
// refuses as not a known ragstack setting. The job was stuck, permanently,
// with no way for an operator to unstick it.
var neverSecretArgs = map[string]bool{
	"key":     true,
	"label":   true,
	"subject": true,
	"id":      true,
	"role":    true,
}

// isSecretArg classifies one arg by its EXACT name — never by substring. The
// settings classifier knows the tenant.env vocabulary (API_KEY*, *_KEY,
// SECRET, PASSWORD, TOKEN, DSN, AUTH); secretArgNames covers the op
// vocabulary on top of it.
func isSecretArg(name string) bool {
	n := strings.ToLower(strings.TrimSpace(name))
	if neverSecretArgs[n] {
		return false
	}
	if secretArgNames[n] {
		return true
	}
	return settings.Classify(strings.ToUpper(n)) == settings.Secret
}

// fixtureFiles seeds the fake files driver with the env files the fixture
// tenants would have on a host: tenant.env from the registry's public
// settings, and every secret-class key from secret_refs with a PLACEHOLDER
// value — the key ledger (API_KEYS / API_KEY_TENANTS / API_KEY_ROLES) is
// rebuilt from the registry's key records so the credential ops can plan
// and run end to end without a single real secret anywhere.
func fixtureFiles(roots paths.Roots, f *registry.Fleet) map[string][]byte {
	out := map[string][]byte{}
	for name, t := range f.Tenants {
		tp := paths.TenantPaths(roots, name, t.ManifestName)
		byFile := map[string][]string{}
		for _, ref := range t.SecretRefs {
			byFile[ref.File] = append(byFile[ref.File], ref.Key)
		}
		// Public settings, sorted for a stable file.
		keys := make([]string, 0, len(t.Settings))
		for k := range t.Settings {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		var tenantEnv strings.Builder
		tenantEnv.WriteString("# fixture tenant.env rendered from the registry (--fake-drivers)\n")
		for _, k := range keys {
			tenantEnv.WriteString(k + "=" + envQuote(t.Settings[k]) + "\n")
		}
		for _, k := range byFile["tenant.env"] {
			tenantEnv.WriteString(k + "=" + fixtureSecret(k, t) + "\n")
		}
		out[tp.TenantEnv] = []byte(tenantEnv.String())
		if secretKeys := byFile["secrets.env"]; len(secretKeys) > 0 || t.SecretsFileSHA256 != "" {
			var secrets strings.Builder
			secrets.WriteString("# fixture secrets.env (--fake-drivers): placeholders, never real values\n")
			for _, k := range secretKeys {
				secrets.WriteString(k + "=" + fixtureSecret(k, t) + "\n")
			}
			out[tp.SecretsEnv] = []byte(secrets.String())
		}
	}
	return out
}

// fixtureSecret renders one secret-class key. The API key triple is derived
// from the registry's key records with placeholder values that carry the key
// id, so a plan's redaction canary and a mint's ledger edit both have real
// structure to work on.
func fixtureSecret(key string, t *registry.Tenant) string {
	switch key {
	case "API_KEYS", "API_KEY_TENANTS", "API_KEY_ROLES":
		vals := make([]string, 0, len(t.Keys))
		tenants := map[string]string{}
		roles := map[string]string{}
		for _, k := range t.Keys {
			v := "fixture-" + k.ID
			vals = append(vals, v)
			tenants[v] = k.TenantString
			roles[v] = k.Role
		}
		var b []byte
		switch key {
		case "API_KEYS":
			b, _ = json.Marshal(vals)
		case "API_KEY_TENANTS":
			b, _ = json.Marshal(tenants)
		default:
			b, _ = json.Marshal(roles)
		}
		return "'" + string(b) + "'"
	default:
		return "'fixture-" + strings.ToLower(key) + "'"
	}
}

// envQuote writes a value in the envfile grammar: bare when it is plain,
// single-quoted otherwise (a single quote inside is not representable and is
// replaced, which for fixture data is acceptable).
func envQuote(v string) string {
	if v == "" {
		return ""
	}
	if !strings.ContainsAny(v, " \t#'\"$\\") {
		return v
	}
	return "'" + strings.ReplaceAll(v, "'", "") + "'"
}

// SetHostToolsFromEnv fills cfg's host-program paths and directories from the
// CTL_* environment.
//
// It is ONE function because the daemon and `--direct` must build the same
// driver set: a host where `systemctl` lives somewhere unusual, or whose node
// is not where the plan says, would otherwise work through one entry point and
// refuse through the other, and the operator debugging that would have no
// reason to suspect which process read which variable. An unset or blank
// variable leaves the field empty, which is how a caller says "take
// drivers.NewReal's default".
func SetHostToolsFromEnv(cfg *EngineConfig) {
	for _, f := range []struct {
		env   string
		field *string
	}{
		{EnvSystemctlBin, &cfg.Systemctl},
		{EnvGitBin, &cfg.Git},
		{EnvNodeBin, &cfg.Node},
		{EnvNpmBin, &cfg.Npm},
		{EnvApptainerBin, &cfg.Apptainer},
		{EnvCrontabBin, &cfg.Crontab},
		{EnvMirror, &cfg.Mirror},
		{EnvNpmCache, &cfg.NpmCache},
		// Not a host TOOL, but the same rule and the same two callers: a
		// --direct run and the daemon must give a create the same default
		// supervisor, or an operator would be creating two kinds of tenant
		// depending on which way the request arrived.
		{EnvDefaultSupervisor, &cfg.DefaultSupervisor},
	} {
		if v := strings.TrimSpace(os.Getenv(f.env)); v != "" {
			*f.field = v
		}
	}
}
