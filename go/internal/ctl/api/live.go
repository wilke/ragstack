package api

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"log/slog"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/doctor"
	"github.com/ragstack/ragstack/internal/ctl/envfile"
	"github.com/ragstack/ragstack/internal/ctl/fleet"
	"github.com/ragstack/ragstack/internal/ctl/gateway"
	"github.com/ragstack/ragstack/internal/ctl/hostfacts"
	"github.com/ragstack/ragstack/internal/ctl/logs"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// diskCacheTTL is how long a tenant's `du` result is reused. The probe shells
// out to `du -s -B1` over a whole tenant tree, so it is minutes of IO on a
// large one — which is exactly why the cache must OUTLIVE a request.
const diskCacheTTL = time.Hour

// liveBackend serves the real registry and probes the real host. EVERY
// Backend method is implemented here, over fleet/logs/doctor and the probes
// below.
//
// It deliberately embeds nothing. It used to embed *FakeBackend "for the
// registry→response projections", and the three methods it did not override —
// Tenant, Tenants and Env — therefore answered a real-driver caller with
// FIXTURE data: GET /v1/tenants/dev reported `listening.qdrant_http: false`
// and `units.services[*].active_state: "n/a"` as constants (fake.go's
// `Listening{API: t.State == "active", QdrantHTTP: false, …}` and
// fakeServices), while /v1/fleet from the SAME daemon showed those same units
// active. An embedded fallback cannot be audited by the compiler: a method
// nobody wrote is a method that silently exists. Without the embedding, a
// Backend method this type does not implement fails to BUILD.
//
// The probes and the doctor options are built ONCE, here, and reused by every
// request. Constructing them per request gave each `/v1/fleet` poll a brand
// new hostfacts.CachedDU — a cache whose whole purpose is to outlive one call
// — so a dashboard polling every few seconds re-ran `du -s -B1` over every
// tenant directory forever, and the cache never once hit.
type liveBackend struct {
	// fleet is the registry as it was read at start-up. Fleet, Logs, Doctor
	// and Registry answer from it; the per-tenant reads reload the file (see
	// reloadRegistry).
	fleet        *registry.Fleet
	roots        paths.Roots
	registryPath string
	probes       fleet.Probes
	doctorOpts   doctor.Options
	// signaller reads /proc for the gateway status. Built once, like the
	// probes: it is a value, not a connection, and the handler must not
	// construct one per request.
	signaller gateway.Signaller
	// logger is the daemon's REDACTING logger, kept so a failed per-request
	// reload can say so. A backend that reached for slog.Default() here would
	// be logging a registry parse error — host text quoting the line it
	// failed on — past the redactor.
	logger *slog.Logger
}

// The live daemon's backend is the WHOLE read surface, stated here so that
// removing a method — or adding one to Backend and implementing it only on
// the fixture — is a build failure rather than a fixture served to an
// operator.
var _ Backend = (*liveBackend)(nil)

func newLiveBackend(ragRoot, registryPath string) (*liveBackend, error) {
	return newLiveBackendWithLogger(ragRoot, registryPath, slog.Default())
}

// newLiveBackendWithLogger is newLiveBackend with the daemon's REDACTING
// logger passed in, so the stale-projection warning goes through it.
func newLiveBackendWithLogger(ragRoot, registryPath string, logger *slog.Logger) (*liveBackend, error) {
	if logger == nil {
		logger = slog.Default()
	}
	roots := paths.NewRoots(ragRoot, paths.Overrides{})
	if registryPath == "" {
		registryPath = roots.Registry()
	}
	// LoadWithDiagnostics, never a repairing load: a READ must not rewrite
	// manifest.tsv (see registry.ErrProjectionStale for the port-reissue bug
	// that repair-on-read caused). A stale projection is a diagnostic, and
	// /v1/doctor reports it as `registry_manifest_mismatch` /
	// `manifest_unknown_row` from the same registry path passed below.
	f, diags, err := registry.LoadWithDiagnostics(registryPath)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", registryPath, err)
	}
	if diags.Stale() {
		logger.Warn("registry projection is stale",
			"registry", registryPath,
			"detail", diags.ProjectionStale.Error(),
			"repair", "ragstack-ctl fleet repair")
	}

	// The CLI resolves the uid from doctor.DefaultCtlUser; fleet names the same
	// account for its own probes. They are one account, and the doctor options
	// below must be built from the one the CLI uses or the hashes part again.
	ctlUser := doctor.DefaultCtlUser
	return &liveBackend{
		fleet:        f,
		roots:        roots,
		registryPath: registryPath,
		probes: fleet.Probes{
			Host:   hostfacts.NewReal(roots),
			Prober: hostfacts.NewProber(),
			// One cache for the process, not one per request.
			Disk:         hostfacts.NewCachedDU(diskCacheTTL),
			CtlUser:      fleet.DefaultCtlUser,
			SudoersGroup: fleet.DefaultSudoersGroup,
		},
		signaller: gateway.NewRealSignaller(),
		logger:    logger,
		doctorOpts: doctor.Options{
			RegistryPath: registryPath,
			// CtlUID is what the `user_dropin_missing` and
			// `runtime_dir_missing` checks need. Omitting it here while the
			// CLI passed it meant those two findings never ran over HTTP — so
			// `ragstack-ctl doctor` and GET /v1/doctor produced DIFFERENT
			// hashes for the same host, and a plan pinned by one could not be
			// confirmed against the other.
			CtlUID: ctlUID(ctlUser),
		},
	}, nil
}

// ctlUID resolves the ctl user's uid, or 0 when that user does not exist on
// this host (a developer checkout). Deliberately identical to the CLI's own
// helper, so both doctor paths see the same value.
func ctlUID(username string) int {
	if uid := hostfacts.LookupUID(username); uid > 0 {
		return uid
	}
	return 0
}

// Fleet probes the host: listeners, units, disk, linger, the store health
// columns, through the process-lifetime probes built in newLiveBackend.
//
// It RELOADS the registry, like Tenant and Tenants (#541). It used to answer
// from the snapshot this process read at start-up, which made the dashboard
// the one view that never saw an adopt, a create or a decommission: a daemon
// up for a week showed the fleet of the week before, and the per-tenant page
// showed a tenant the fleet page did not list. A failed reload falls back to
// the snapshot rather than failing the poll — a dashboard that goes blank
// because the registry was being replaced at that instant is worse than one
// that is a second stale.
func (b *liveBackend) Fleet(ctx context.Context) (*model.FleetResponse, error) {
	return fleet.Build(ctx, b.roots, b.fleetNow(), b.probes), nil
}

// Registry is the fleet record the gateway and settings projections read, and
// the one handleDoctor path-redacts a viewer's findings from.
//
// It reloads too (#541), with the same fallback: settings and the gateway
// projection answered from a start-up snapshot reported the generation of
// whenever the daemon last restarted, so `registry_generation` — the number a
// plan pins and `plan_stale` is decided against — was the one field on the
// settings page guaranteed to be wrong after any mutation.
//
// The fallback is what keeps handleDoctor's viewer redaction SAFE. The
// redaction needs a fleet to know which absolute paths to replace; on a
// failed read the handler skips it, and skipping it leaks the host layout
// into a viewer's finding details. Answering from the snapshot means the
// handler always has a fleet, and the redaction has no path that silently
// does nothing.
func (b *liveBackend) Registry(context.Context) (*registry.Fleet, error) {
	return b.fleetNow(), nil
}

// fleetNow is the registry as it is on disk, or the start-up snapshot when it
// cannot be read. The failure is logged once per occurrence rather than
// swallowed: a registry that stops loading is a host fault an operator must
// see, even while the daemon keeps answering from the last good copy.
func (b *liveBackend) fleetNow() *registry.Fleet {
	f, err := b.reloadRegistry()
	if err != nil {
		b.logger.Warn("registry reload failed; answering from the start-up snapshot",
			"registry", b.registryPath, "err", err.Error())
		return b.fleet
	}
	return f
}

// Tenant is one tenant as the HOST has it right now: the listener scan, the
// unit states, the live drift — fleet.TenantView, the same builder the CLI's
// `tenant show` uses, through the same process-lifetime probes /v1/fleet uses.
//
// viewer is the contract's reduction for ctlTenantShow ("summary, status,
// units, drift; registry is null"): the registry row is not assembled at all
// rather than assembled and dropped, because the row carries secret refs, key
// fingerprints and the rollback descriptor.
//
// The registry is reloaded: an adopt run from the CLI moves it, and a tenant
// that exists on the host but not in this process's start-up snapshot is a
// 404 nobody can explain.
func (b *liveBackend) Tenant(ctx context.Context, name string, viewer bool) (*model.TenantResponse, error) {
	f, err := b.reloadRegistry()
	if err != nil {
		return nil, err
	}
	t, ok := f.Tenants[name]
	if !ok {
		return nil, ErrNotFound
	}
	view := fleet.TenantView(ctx, b.roots, t, b.probes, !viewer)
	return &view, nil
}

// Tenants is every tenant in display order, in the OPERATOR shape — the
// handler applies the viewer reduction for ctlTenantsList. One /proc scan is
// shared across the rows (fleet.TenantsView), so the four answers are taken
// at one instant rather than four.
func (b *liveBackend) Tenants(ctx context.Context) (*model.TenantsResponse, error) {
	f, err := b.reloadRegistry()
	if err != nil {
		return nil, err
	}
	view := fleet.TenantsView(ctx, b.roots, f, b.probes, true)
	return &view, nil
}

// envSources are the three files a tenant's configuration lives in, in the
// order the api unit's EnvironmentFile= lines load them: a key defined twice
// is reported once, attributed to the file that defines it first.
var envSources = []struct {
	file   string
	source model.Source
}{
	{"tenant.env", model.SourceTenantEnv},
	{"secrets.env", model.SourceSecretsEnv},
	{"provision.env", model.SourceProvisionEnv},
}

// Env is the tenant's real configuration keys, classified.
//
// env_response.json: "every key found in tenant.env, secrets.env and
// provision.env is listed with its class". The fixture backend INVENTS that
// list from the registry row, which is right for the conformance suite and
// wrong for an operator — it showed keys a tenant does not have and hid the
// ones it does.
//
// Values: envRow shows the verbatim value for a `public` key and the literal
// `<redacted>` for every other class, so no secret-class value has a path out
// of this process — the same rule, through the same helper, as the fake.
//
// A file that cannot be read or parsed contributes no keys rather than
// failing the request: a missing secrets.env is normal, an unparsable
// tenant.env is a `doctor` finding (env_not_systemd_parsable), and neither is
// a reason to refuse the classification of the files that DID read.
//
// Pre-handover fallback: until PR-E hands a tenant over, its env files are
// 0600 and owned by the operator who provisioned it — the same shape
// logs.SecretsUnreadableError and doctor.SecretsUnreadableByCtl exist for.
// Silently reporting zero keys there would be indistinguishable from "this
// tenant has no configuration", which is a lie. When a file is unreadable
// with a permission error AND the registry's owner is not this account
// (envPreHandover, the same formula as
// logs.SecretsUnreadableError.PreHandover), the WHOLE response is rebuilt
// from the registry's own settings/secret_refs (envFromRegistry) instead of
// whatever subset of files happened to read: on the real host all three
// files share one owner and one mode, so a partial merge would only invite
// the question of why some keys are live and others are not.
//
// A permission error on a tenant THIS account already owns is not the
// expected pending-handover state — it is the same class of fault doctor's
// PortOwnerMismatch treats as an error rather than an info, so it is
// returned as a request error (500) instead of being papered over.
func (b *liveBackend) Env(_ context.Context, name string) (*model.EnvResponse, error) {
	f, err := b.reloadRegistry()
	if err != nil {
		return nil, err
	}
	t, ok := f.Tenants[name]
	if !ok {
		return nil, ErrNotFound
	}
	resp := &model.EnvResponse{Tenant: t.Name, EnvLayout: t.EnvLayout, Keys: []model.EnvKey{}, Source: model.EnvResponseSourceLive}
	seen := map[string]bool{}
	for _, src := range envSources {
		path := filepath.Join(t.DataDir, "config", src.file)
		raw, rerr := os.ReadFile(path)
		if rerr != nil {
			if errors.Is(rerr, fs.ErrPermission) {
				if !envPreHandover(t) {
					return nil, fmt.Errorf("env: %s: %w", path, rerr)
				}
				return envFromRegistry(t, src.file), nil
			}
			continue // ENOENT (normal) or another read fault
		}
		parsed, _, perr := envfile.ParseLenient(raw)
		if perr != nil {
			continue
		}
		for _, key := range parsed.Keys() {
			if seen[key] {
				continue
			}
			seen[key] = true
			value, _ := parsed.Get(key)
			resp.Keys = append(resp.Keys, envRow(key, value, src.source))
		}
	}
	return resp, nil
}

// envPreHandover is logs.SecretsUnreadableError.PreHandover's formula, reused
// rather than re-derived: the registry records an owner other than the
// account this process runs as.
func envPreHandover(t *registry.Tenant) bool {
	return t.Owner != "" && t.Owner != logs.CtlUser()
}

// envFromRegistry answers GET .../env from the registry row alone, for a
// tenant whose env files this account cannot read yet. unreadableFile is the
// file the failed read named, for the note's prose.
//
// Settings is documented as "public keys only" (registry/types.go), so every
// entry becomes a public-class row via envRow, verbatim; SecretRefs becomes a
// secret-class row per key, always "<redacted>" — the same values a live read
// would have shown, just sourced from the registry snapshot taken at
// adoption rather than the files on disk today. Keys in a class registry
// does not capture at all (executable-surface, unsupported) are simply
// absent: the registry never recorded them, so there is nothing to show.
func envFromRegistry(t *registry.Tenant, unreadableFile string) *model.EnvResponse {
	keys := make([]model.EnvKey, 0, len(t.Settings)+len(t.SecretRefs))
	for key, value := range t.Settings {
		keys = append(keys, envRow(key, value, model.SourceRegistry))
	}
	for _, ref := range t.SecretRefs {
		keys = append(keys, envRow(ref.Key, "", model.SourceRegistry))
	}
	sort.Slice(keys, func(i, j int) bool { return keys[i].Key < keys[j].Key })
	adopted := "an unknown time"
	if t.AdoptedAt != "" {
		adopted = string(t.AdoptedAt)
	}
	note := fmt.Sprintf(
		"%s is not readable by %s; showing the public settings recorded in the registry at adoption (%s). "+
			"The tenant is owned by %s until its handover (PR-E); drift against the live file cannot be checked until then.",
		unreadableFile, logs.CtlAccount(), adopted, t.Owner)
	return &model.EnvResponse{
		Tenant: t.Name, EnvLayout: t.EnvLayout, Keys: keys,
		Source: model.EnvResponseSourceRegistry, Note: model.NullString(note),
	}
}

// Logs tails the tenant's real log, redacted with that tenant's own secret
// material. A file the tenant does not have is the contract's 404, not an
// empty tail — a `ui` log for a static-UI tenant, a store log for a shared
// store.
func (b *liveBackend) Logs(_ context.Context, name, file string, lines int) (*model.LogsResponse, error) {
	// Reloaded (#541): a tenant adopted after this daemon started had no log
	// at all — a 404 on a file that was on disk and readable.
	t, ok := b.fleetNow().Tenants[name]
	if !ok {
		return nil, ErrNotFound
	}
	resp, err := logs.Read(b.roots, t, model.LogFile(file), lines)
	if errors.Is(err, logs.ErrNoLog) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	return resp, nil
}

// GatewayStatus reads the published gateway generation off the host: the
// `current` pointer, txn.json and the nginx master's /proc identity. Every
// read repairs an incomplete publication first, so the dashboard never shows
// a pointer the next reload would disagree with.
//
// It re-reads the registry rather than using the one loaded at start-up: an
// adopt or a publish moves the registry on, and `pending_diff` has to compare
// what is published against what the registry says NOW.
func (b *liveBackend) GatewayStatus(_ context.Context) (*model.GatewayStatus, error) {
	f, err := b.reloadRegistry()
	if err != nil {
		return nil, err
	}
	return gateway.StatusWith(b.roots, f, gateway.Options{Sig: b.signaller})
}

// ErrGatewayRender marks a failure of the gateway RENDERER — the registry does
// not render to a loadable configuration — as opposed to a failure to READ the
// registry.
//
// The two need different answers. A load failure is a host fault whose text
// quotes the registry path and, on a parse error, the line it failed on; it
// goes through backendError, which logs it and tells the caller nothing. A
// render refusal is the caller's own registry content and is worth returning —
// but its text still carries host paths (`tenant demo: /rag/data/tenants/demo/
// ui/dist escapes …`), so it is redacted first. GET/POST /v1/gateway/render is
// a VIEWER operation.
var ErrGatewayRender = errors.New("the gateway renderer refused the registry")

// GatewayDiff renders the next generation and diffs it against the published
// one. A READ despite the verb: nothing is written and no lock is taken.
func (b *liveBackend) GatewayDiff(_ context.Context) (*model.GatewayRenderResponse, error) {
	f, err := b.reloadRegistry()
	if err != nil {
		return nil, err
	}
	resp, err := gateway.Diff(b.roots, f)
	if err != nil {
		// Marked, not redacted: what leaves the process is the handler's
		// decision, and it redacts once for every backend rather than trusting
		// each one to have done it.
		return nil, fmt.Errorf("%w: %v", ErrGatewayRender, err)
	}
	return resp, nil
}

// absPath matches an absolute filesystem path in an error message: a slash at
// a word boundary and everything up to whitespace, a quote or a closing
// bracket. The leading group keeps a RELATIVE path (`conf.d/05-tenants…`, which
// names a file inside the generation and no host layout) out of the match.
var absPath = regexp.MustCompile(`(^|[\s"'(=])(/[^\s"'` + "`" + `),;:]*)`)

// RedactHostPaths replaces every absolute path in a message with `<path>`.
//
// The renderer's refusals name files under /rag — a tenant's data dir, the
// admin bundle, the proxy tree — and the layout of the host's filesystem is not
// something a viewer is entitled to read out of an error body. The rest of the
// message (which tenant, which field, what was wrong with it) is exactly what
// the caller needs and survives.
func RedactHostPaths(s string) string {
	return absPath.ReplaceAllString(s, "${1}<path>")
}

// reloadRegistry re-reads the registry file. LoadNoRepair, never a repairing
// load: a READ must not rewrite manifest.tsv (see newLiveBackend).
func (b *liveBackend) reloadRegistry() (*registry.Fleet, error) {
	f, err := registry.LoadNoRepair(b.registryPath)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", b.registryPath, err)
	}
	return f, nil
}

// Doctor runs the real diagnostics with the SAME options the CLI uses. The
// doctor hash is what a plan pins, so two callers that disagree about the
// option set disagree about whether a plan is still valid.
func (b *liveBackend) Doctor(ctx context.Context, tenant, op string) (*model.DoctorResponse, error) {
	// Reloaded (#541). The doctor HASH is what a plan pins and what
	// `force_with_doctor_diff` quotes, so a doctor computed against a stale
	// registry produces a hash the CLI — which reads the file every run —
	// never reproduces, and every plan taken over HTTP goes stale for a
	// reason no operator can see.
	f := b.fleetNow()
	if tenant != "" {
		if _, ok := f.Tenants[tenant]; !ok {
			return nil, ErrNotFound
		}
	}
	opts := b.doctorOpts
	opts.Tenant, opts.Op = tenant, op
	return doctor.Run(ctx, b.roots, f, opts), nil
}
