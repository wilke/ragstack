// Package adopt turns a hand-started tenant into a registry row without
// touching it. Preview reads the tenant's env files, its data tree, the
// listening sockets and the SAFE_ENV of the processes behind them, and
// returns the row it WOULD write plus every finding it hit; Commit writes
// that row (and the manifest projection) and nothing else.
//
// Three rules the whole package obeys:
//
//   - Read-only against the tenant. Not one byte under the data dir, the
//     worktree or the gateway is written. `bin/up.sh` is inventory, never a
//     thing to run or edit.
//   - Live facts beat recorded ones. The heap a store is actually running
//     with (from /proc/<pid>/environ, SAFE_ENV-filtered) is what the registry
//     records; provision.env's value is kept beside it as `provision_heap`
//     and the disagreement becomes a drift row, never a silent reconcile.
//   - Never a secret value. Secret-class keys are recorded as {key, file}
//     refs and API keys as sha256 fingerprints; the canary test proves no
//     64-hex string and no secret value reaches the saved registry.
package adopt

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/doctor"
	"github.com/ragstack/ragstack/internal/ctl/envfile"
	"github.com/ragstack/ragstack/internal/ctl/hostfacts"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/settings"
)

// Options are the facts an operator supplies (the ones the host cannot be
// asked for) plus the seams the tests replace.
type Options struct {
	// DataDir is the tenant's data tree. Required.
	DataDir string
	// Worktree is the checkout its API runs from. Required.
	Worktree string
	// ManifestName is the manifest.tsv row / data-dir basename / apptainer
	// instance suffix. Defaults to the tenant name (lucid-next → "lucid").
	ManifestName string
	// UIPort is the Vite dev server's port (0 ⇒ no UI row).
	UIPort int
	// UIMode is how the tenant's UI is served: registry.UIModeStatic (a
	// `vite build` nginx serves from <data_dir>/ui/dist, no port),
	// UIModeDev (a Vite dev server on UIPort) or UIModeExternal (served by
	// something the ctl does not manage). Empty keeps the historical
	// inference: dev when UIPort > 0, external when it is 0.
	//
	// ValidateUIMode is the one place the combinations are judged, so the
	// CLI can reject a bad pair as a usage error before any host is read.
	UIMode string
	// PublicName is the gateway path segment when it differs from the
	// registry name. Defaults to the name.
	PublicName string

	// Host is the read-only host surface; nil ⇒ the live host.
	Host hostfacts.Host
	// Now is the clock; nil ⇒ time.Now.
	Now func() time.Time
	// ExternalStorePorts are the shared-store ports a tenant may point at
	// from outside its own block; nil ⇒ hostfacts.DefaultExternalStorePorts.
	ExternalStorePorts []int
	// PythonEnv overrides the interpreter root when no API process is
	// running to observe it.
	PythonEnv string

	// Existing is the registry row this preview would REPLACE, nil for a
	// first adoption. It is not a fallback for facts the host can be asked
	// for: it is how the preview knows whether the tenant is one the CONTROL
	// PLANE runs, and a row that is keeps its ownership, its supervision, its
	// state and its handover instead of having them re-derived from probes
	// that cannot see another account's processes. See keepCtlSupervision.
	Existing *registry.Tenant
	// Readopt is `--readopt`: this run REPLACES an existing row rather than
	// writing a first one. Without it, a tenant whose API the control plane
	// is running is refused (ErrCtlRunsTenant) rather than adopted as a
	// hand-started one.
	Readopt bool
	// CtlUser is the account the control plane runs as; "" ⇒
	// doctor.DefaultCtlUser (svcbvbrc).
	CtlUser string
}

// ErrCtlRunsTenant refuses a FIRST adoption of a tenant whose API is run by
// the control plane's own account. Adoption exists for a hand-started tenant;
// a fresh row over a ctl-run one records `supervisor: manual` and `owner` from
// a probe, which is how a supervised tenant becomes one nothing will start.
var ErrCtlRunsTenant = errors.New("this tenant is already run by the control plane; use --readopt")

// DefaultPythonEnv is the shared conda env every tenant API runs from today.
const DefaultPythonEnv = "/rag/envs/ragstack"

// DefaultAPIBind is the bind a row records when nothing about the API's own
// bind could be observed. It is the plan's `api.bind` default and the safe
// end of the range the contract allows.
const DefaultAPIBind = "127.0.0.1"

// contractBinds is the set registry.json's api.bind pattern accepts. bindOf's
// set is wider (it also reads ::1 off a live command line, which is a fact
// worth reporting), so anything adopt DERIVES is checked against this.
var contractBinds = map[string]bool{"127.0.0.1": true, "0.0.0.0": true, "localhost": true}

// apiLaunchScripts are the tenant-owned scripts that may start the API, in
// the order adopt trusts them. Inventory only — never run, never edited.
var apiLaunchScripts = []string{"bin/up.sh", "bin/api.sh", "bin/start-api.sh"}

// The contract-shaped markers for a SIF nobody has pinned yet. `fleet image
// list` replaces them with the real version and digest; adopt refuses to
// invent one. Aliased to the registry's constants so there is one definition
// of "unpinned" in the tree.
const (
	unpinnedDigest  = registry.UnpinnedDigest
	unpinnedVersion = registry.UnpinnedVersion
)

// absPathValue is the registry's AbsPath charset: a configuration value that
// matches it is a path, and a path outside the data dir is an external ref.
var absPathValue = regexp.MustCompile(`^/[A-Za-z0-9._/-]*$`)

// heapValue is the contract's heap grammar (registry.json ElasticsearchStore).
var heapValue = regexp.MustCompile(`^[1-9][0-9]*[mg]$`)

// xmxFlag finds the max-heap flag in ES_JAVA_OPTS or a java command line.
var xmxFlag = regexp.MustCompile(`-Xmx([0-9]+[kKmMgG])`)

// envValue is one key as it was found: its value and which file it came from.
type envValue struct {
	Value string
	File  string // tenant.env | secrets.env | provision.env
}

// Preview builds the registry row for name without writing anything, and
// returns every finding the read raised. The row is exactly what Commit
// would store, so `--preview` and `--commit` can never disagree.
func Preview(roots paths.Roots, name string, opts Options) (*registry.Tenant, []model.Finding, error) {
	p, err := newPreviewer(roots, name, opts)
	if err != nil {
		return nil, nil, err
	}
	t, err := p.build()
	if err != nil {
		return nil, p.findings, err
	}
	return t, p.findings, nil
}

type previewer struct {
	roots    paths.Roots
	name     string
	manifest string
	public   string
	opts     Options
	host     hostfacts.Host
	now      time.Time
	external []int

	tp        paths.Tenant
	dataDir   string
	worktree  string
	env       map[string]envValue
	order     []string
	listeners map[int]hostfacts.Listener
	ports     paths.Ports

	findings []model.Finding
}

func newPreviewer(roots paths.Roots, name string, opts Options) (*previewer, error) {
	if err := paths.ValidateName(name); err != nil {
		return nil, err
	}
	man := opts.ManifestName
	if man == "" {
		man = name
	}
	if err := paths.ValidateName(man); err != nil {
		return nil, fmt.Errorf("manifest name: %w", err)
	}
	if opts.DataDir == "" || opts.Worktree == "" {
		return nil, errors.New("adopt: --data-dir and --worktree are required")
	}
	if err := ValidateUIMode(opts.UIMode, opts.UIPort); err != nil {
		return nil, fmt.Errorf("adopt: %w", err)
	}
	dataDir, err := paths.SafePath(roots.RagRoot, opts.DataDir)
	if err != nil {
		return nil, fmt.Errorf("data dir: %w", err)
	}
	worktree := filepath.Clean(opts.Worktree)
	if !filepath.IsAbs(worktree) || !absPathValue.MatchString(worktree) {
		return nil, fmt.Errorf("worktree %q is not an absolute path in [A-Za-z0-9._/-]", opts.Worktree)
	}
	p := &previewer{
		roots: roots, name: name, manifest: man,
		public:   orDefault(opts.PublicName, name),
		opts:     opts,
		host:     opts.Host,
		external: opts.ExternalStorePorts,
		tp:       paths.TenantPaths(roots, name, man),
		dataDir:  dataDir,
		worktree: worktree,
		env:      map[string]envValue{},
	}
	if p.host == nil {
		p.host = hostfacts.NewReal(roots)
	}
	if p.external == nil {
		p.external = hostfacts.DefaultExternalStorePorts
	}
	p.now = time.Now().UTC()
	if opts.Now != nil {
		p.now = opts.Now().UTC()
	}
	return p, nil
}

func (p *previewer) build() (*registry.Tenant, error) {
	if p.dataDir != p.tp.DataDir {
		p.info(doctor.DataDirOffLayout, fmt.Sprintf("data dir %s is not the standard %s", p.dataDir, p.tp.DataDir))
	}
	envSHA, secretsSHA, err := p.readEnvFiles()
	if err != nil {
		return nil, err
	}
	if err := p.resolvePorts(); err != nil {
		return nil, err
	}
	if err := p.readListeners(); err != nil {
		return nil, err
	}

	t := registry.NewTenant(p.name, p.manifest)
	t.DataDir = p.dataDir
	t.Worktree = p.worktree
	t.Ports = p.ports
	t.ArtifactID = ""         // adopted tenants run from a hand-made worktree
	t.ReleaseGeneration = nil // no artifact, no release generation
	t.LastBackup = nil        // nothing the ctl made
	t.RestartPending = false
	t.Supervisor = string(model.SupervisorManual)
	t.DesiredBoot = "disabled"
	t.EnvFileSHA256 = envSHA
	t.SecretsFileSHA256 = registry.NullString(secretsSHA)
	t.AdoptedAt = registry.NullString(p.stamp())

	api := p.listeners[p.ports.API]
	t.State = string(model.StateStopped)
	owner, attributed := p.ownerOf(api)
	t.Owner = owner
	if api.Port != 0 {
		t.State = string(model.StateActive)
		switch {
		case !attributed:
			// The port is held by somebody this run cannot name: no readable
			// /proc/<pid>/fd join, and no pidfile pointing at a live process
			// either. That is a FACT about the read, not about the tenant, so
			// it is written down rather than papered over — and on a
			// ctl-supervised row it also becomes a drift row below, because
			// the row's recorded owner is the thing that stands.
			p.info(doctor.PortOwnerUnverifiable, fmt.Sprintf(
				"the process on API port %d cannot be attributed by this account (the listen table only joins THIS account's sockets to pids, and %s names no live process); owner records the default %q",
				p.ports.API, p.pidFilePath(), owner))
		case !registry.KnownOwner(owner):
			// The observed account is recorded VERBATIM, including when it is
			// outside the contract's owner enum — adopt's whole job is to
			// write down what is true, and substituting a legal-looking value
			// for the one on the host would put a lie in the registry that no
			// later doctor run could catch.
			//
			// So the row is built honestly and the run says it cannot be
			// committed: an error finding refuses the commit (and if an
			// operator forces past it, registry.Save's contract check refuses
			// the write itself, because a row like this is one Load can never
			// read back). The fix is a real one — hand the tenant over to an
			// account the contract knows, or extend the enum in
			// contracts/ctl/schemas/registry.json first.
			p.err(doctor.OwnerNotInEnum, fmt.Sprintf(
				"the process on API port %d runs as %q, which is not one of the contract's owners (%s); a row recording it cannot be loaded back — hand the tenant over to a known account, or extend the enum in contracts/ctl/schemas/registry.json",
				p.ports.API, owner, strings.Join(registry.Owners(), "|")))
		}
	} else {
		p.warn(doctor.PortNotListening, fmt.Sprintf("no listener on the API port %d", p.ports.API))
	}
	t.API = registry.API{Bind: p.apiBind(api), PidFile: p.pidFilePath(), Log: p.tp.APILog}
	t.PythonEnv = p.pythonEnv(api)

	// A first adoption of a tenant the CONTROL PLANE runs is refused outright:
	// the row it would write says `supervisor: manual` over processes the ctl
	// supervises, which is the same lie from the other direction.
	if err := p.refuseIfCtlRuns(api); err != nil {
		return nil, err
	}

	t.Code = p.code()
	t.UI = p.ui()
	t.Identity = p.identity()
	t.Settings, t.SecretRefs, t.EnvLayout = p.classify()
	t.Keys = p.keys()
	t.ExternalRefs = p.externalRefs()
	t.UnmanagedFiles = p.unmanagedFiles()
	t.Stores = p.stores(&t.Drift)
	// A re-adoption of a row the control plane RUNS keeps what the handover
	// decided. It comes after the stores so the capabilities are on the row to
	// keep, and before the rollback descriptor, which records the owner.
	p.keepCtlSupervision(t, owner, attributed)
	rb, err := p.rollback(t)
	if err != nil {
		return nil, err
	}
	t.RollbackDescriptor = rb

	if len(t.UnmanagedFiles) > 0 {
		p.info(doctor.UnmanagedFiles, fmt.Sprintf("%d file(s) under config/ and state/ that no renderer produces: %s",
			len(t.UnmanagedFiles), strings.Join(t.UnmanagedFiles, ", ")))
	}
	p.info(doctor.CapabilitiesUnconfirmed, "store capabilities stay false until an operator confirms process identity, backing path and exclusive ownership")
	return t, nil
}

// --------------------------------------------------------------- ownership

// DefaultOwner is the account a row records when NOTHING about the tenant's
// API process could be attributed — no listener, and no pidfile naming a live
// one. It is a default and is always accompanied by a finding that says so;
// it is never a substitute for an attribution that failed, which is the
// distinction ownerOf's second return value carries.
const DefaultOwner = string(model.OwnerWilke)

// CtlSupervised reports whether a row is one the CONTROL PLANE runs — the
// registry's `supervisor` is anything but `manual`, which is the one value
// that means "somebody else started this".
//
// It is the predicate the whole ownership carry hangs off, and it is written
// as "not manual" rather than "instance or systemd" deliberately: a supervisor
// value this binary has not learned yet is one it must treat as the ctl's, not
// as an invitation to take the row over.
func CtlSupervised(t *registry.Tenant) bool {
	return t != nil && t.Supervisor != "" && t.Supervisor != string(model.SupervisorManual)
}

// ctlUser is the account the control plane runs as.
func (p *previewer) ctlUser() string {
	return orDefault(p.opts.CtlUser, doctor.DefaultCtlUser)
}

// pidFilePath is what `api.pidfile` records: the row's own path on a
// re-adoption (the instance supervisor is free to have moved it), else the
// layout's `<data_dir>/api-<name>.pid`.
func (p *previewer) pidFilePath() string {
	if e := p.opts.Existing; e != nil && e.API.PidFile != "" {
		return e.API.PidFile
	}
	return p.tp.PidFile
}

// pidFilePID reads the pid out of the tenant's api pidfile. Anything that is
// not a positive integer on its own is "no pid", not an error: a pidfile is
// evidence, and half a line of one is no evidence at all.
func (p *previewer) pidFilePID() (int, bool) {
	b, err := os.ReadFile(p.pidFilePath())
	if err != nil {
		return 0, false
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(b)))
	if err != nil || pid <= 0 {
		return 0, false
	}
	return pid, true
}

// ownerOf attributes the tenant's API process to an account, and says whether
// it managed to.
//
// The listen table is the first source and the weakest one: it joins a socket
// to a pid through /proc/<pid>/fd, and those links are readable only for THIS
// account's own processes. Every cross-account read — the normal shape on
// coconut, where the ctl runs as svcbvbrc and most tenants were hand-started
// by wilke, and the shape of every tenant AFTER a handover when the adopt is
// run by wilke — therefore arrives with Pid 0 and User "". The old code read
// that empty string as "wilke", which is how a handed-over row got its
// ownership rewritten by a command that had only meant to refresh a key
// ledger (#587's sequel; see doctor.CtlSupervisedRow).
//
// So when the port is unattributable the pidfile is asked instead: it names
// the pid outright, and /proc/<pid> tells anyone on the host which account
// owns it. Only a pid that is actually a live process counts — a stale pidfile
// left by a tenant that died is not evidence about anybody.
func (p *previewer) ownerOf(api hostfacts.Listener) (owner string, attributed bool) {
	if api.Port == 0 {
		return DefaultOwner, false
	}
	if api.User != "" {
		return api.User, true
	}
	pid, ok := p.pidFilePID()
	if !ok {
		return DefaultOwner, false
	}
	_, user, err := p.host.ProcessOwner(pid)
	if err != nil || user == "" {
		return DefaultOwner, false
	}
	return user, true
}

// refuseIfCtlRuns is the guard on a FIRST adoption. The row `adopt` writes
// says `supervisor: manual` — "somebody else started this" — and the tenant it
// is allowed to say that about is a hand-started one. A tenant whose API
// PIDFILE names a live process belonging to the control plane's own account is
// not that: the pidfile is the instance supervisor's own record of the uvicorn
// it started, so the ctl both runs this API and knows it does.
//
// The evidence is deliberately the pidfile and not the listen table. "Some
// process owned by svcbvbrc holds the port" is also true of a tenant svcbvbrc
// started BY HAND — an honest adoption — while a pidfile the supervisor wrote
// is not something a hand start produces. It also has to be the pidfile
// because the listen table cannot attribute another account's socket at all,
// which is the whole reason this file learned to read /proc/<pid> in the first
// place. Only a live process counts: a pidfile left behind by a tenant that
// died names nobody.
//
// It is a refusal from Preview rather than an error finding, and that is the
// point: `--force` exists for a row that is merely awkward to record, not for
// unlearning that the control plane runs this tenant.
func (p *previewer) refuseIfCtlRuns(api hostfacts.Listener) error {
	if p.opts.Readopt || api.Port == 0 {
		return nil
	}
	pid, ok := p.pidFilePID()
	if !ok {
		return nil
	}
	_, user, err := p.host.ProcessOwner(pid)
	if err != nil || user != p.ctlUser() {
		return nil
	}
	return fmt.Errorf("%w: %s names pid %d, which runs as %s, and port %d is listening. "+
		"`ragstack-ctl adopt %s … --readopt` refreshes the row the control plane already has; a first adoption "+
		"would record `supervisor: manual` over processes the ctl supervises, and nothing would start the tenant "+
		"again", ErrCtlRunsTenant, p.pidFilePath(), pid, user, p.ports.API, p.name)
}

// keepCtlSupervision is what `--readopt` does to a row the control plane RUNS:
// nothing. Owner, supervisor, state, the handover block, the boot intent, the
// api pidfile and the confirmed store capabilities are all DECISIONS — a
// handover made them, or an operator did — and adoption's "live facts beat
// recorded ones" rule has no jurisdiction over a decision. What the run does
// refresh is what adoption exists to refresh: the settings classification, the
// secret refs and their checksums, the key ledger, the drift rows, the
// external refs, the unmanaged files and the worktree's code sha.
//
// A probe that CONTRADICTS the row is written down, never acted on. There are
// two ways it can, and they are different facts:
//
//   - the port is held by a readable process belonging to another account: a
//     real disagreement (port_owner_mismatch), warn.
//   - the port is held by a process this account cannot attribute at all: the
//     expected shape when the adopt is run by anyone but the tenant's owner
//     (port_owner_unverifiable), info.
//
// Either way the recorded owner stands, which is the whole correction.
func (p *previewer) keepCtlSupervision(t *registry.Tenant, observed string, attributed bool) {
	prev := p.opts.Existing
	if !CtlSupervised(prev) {
		return
	}
	p.warn(doctor.CtlSupervisedRow, fmt.Sprintf(
		"row is ctl-supervised: ownership and supervision are not re-derived — owner (%s), supervisor (%s), "+
			"state (%s), handover (%s), desired_boot (%s), api.pidfile and the confirmed store capabilities are "+
			"kept as the registry records them. This run refreshes the settings classification, the secret refs "+
			"and checksums, the keys[] ledger, drift, external refs, unmanaged files and the worktree's code sha, "+
			"and nothing else",
		prev.Owner, prev.Supervisor, prev.State, handoverPhaseOf(prev), orDefault(prev.DesiredBoot, "unset")))

	switch {
	case attributed && observed != prev.Owner:
		t.Drift = append(t.Drift, registry.Drift{
			Code: doctor.PortOwnerMismatch, Level: string(model.LevelWarn), Field: "owner",
			Expected: prev.Owner, Actual: observed, ObservedAt: p.stamp(),
			Note: "the recorded owner is kept: a re-adoption does not re-derive who owns a ctl-supervised tenant",
		})
	case !attributed && t.State == string(model.StateActive):
		t.Drift = append(t.Drift, registry.Drift{
			Code: doctor.PortOwnerUnverifiable, Level: string(model.LevelInfo), Field: "owner",
			Expected: prev.Owner, Actual: "unattributable", ObservedAt: p.stamp(),
			Note: fmt.Sprintf("API port %d is held by a process this account cannot attribute; the recorded owner is kept", p.ports.API),
		})
	}

	t.Owner = prev.Owner
	t.Supervisor = prev.Supervisor
	t.State = prev.State
	t.Handover = prev.Handover
	if prev.DesiredBoot != "" {
		t.DesiredBoot = prev.DesiredBoot
	}
	if prev.API.PidFile != "" {
		t.API.PidFile = prev.API.PidFile
	}
	// Capabilities are kept HERE as well as in carryOver so that `--preview`
	// shows the row `--commit` would write. `--confirm-stores` runs on the
	// preview AFTER this, so an explicit confirmation still wins.
	t.Stores.Qdrant.Capabilities = prev.Stores.Qdrant.Capabilities
	t.Stores.Elasticsearch.Capabilities = prev.Stores.Elasticsearch.Capabilities
	t.Stores.Postgres.Capabilities = prev.Stores.Postgres.Capabilities
}

// handoverPhaseOf renders a row's handover for a message: the phase, or
// "none".
func handoverPhaseOf(t *registry.Tenant) string {
	if t == nil || t.Handover == nil {
		return "none"
	}
	return t.Handover.Phase
}

// ------------------------------------------------------------------- env

// readEnvFiles parses tenant.env (required), secrets.env and provision.env
// (optional) and records every key with the file it came from. Inline
// comments are tolerated (ParseLenient) and reported one finding per key —
// systemd's EnvironmentFile= would keep them as part of the value.
func (p *previewer) readEnvFiles() (envSHA, secretsSHA string, err error) {
	type src struct {
		path, label string
		required    bool
	}
	files := []src{
		{filepath.Join(p.dataDir, "config", "tenant.env"), string(model.SourceTenantEnv), true},
		{filepath.Join(p.dataDir, "config", "secrets.env"), string(model.SourceSecretsEnv), false},
		{filepath.Join(p.dataDir, "config", "provision.env"), string(model.SourceProvisionEnv), false},
	}
	for _, s := range files {
		b, rerr := os.ReadFile(s.path)
		if rerr != nil {
			if s.required {
				return "", "", fmt.Errorf("adopt %s: %w", p.name, rerr)
			}
			continue
		}
		f, problems, perr := envfile.ParseLenient(b)
		if perr != nil {
			return "", "", fmt.Errorf("adopt %s: %s: %w", p.name, s.path, perr)
		}
		for _, pr := range problems {
			p.warn(doctor.EnvNotSystemdParsable, fmt.Sprintf("%s line %d: %s (%s): %s", filepath.Base(s.path), pr.Line, pr.Class, pr.Key, pr.Msg))
		}
		for _, pr := range f.Validate() {
			if pr.Class == envfile.ClassInlineComment {
				continue // already reported above
			}
			p.warn(doctor.EnvNotSystemdParsable, fmt.Sprintf("%s line %d: %s (%s): %s", filepath.Base(s.path), pr.Line, pr.Class, pr.Key, pr.Msg))
		}
		for _, k := range f.Keys() {
			v, _ := f.Get(k)
			if _, dup := p.env[k]; !dup {
				p.order = append(p.order, k)
			}
			p.env[k] = envValue{Value: v, File: s.label}
		}
		switch s.label {
		case string(model.SourceTenantEnv):
			envSHA = sha256Hex(b)
		case string(model.SourceSecretsEnv):
			secretsSHA = sha256Hex(b)
		}
	}
	return envSHA, secretsSHA, nil
}

func (p *previewer) get(key string) (string, bool) {
	v, ok := p.env[key]
	return v.Value, ok
}

// value is get without the presence flag, for keys where "absent" and "empty"
// mean the same thing (provision.env writes TENANT_PG_HOST= for a sqlite
// tenant, so both shapes occur in the same file).
func (p *previewer) value(key string) string {
	v, _ := p.get(key)
	return v
}

// classify splits every key by the settings table: public values go into the
// registry verbatim, secret keys become {key, file} refs, executable-surface
// and unsupported keys are recorded nowhere (they stay in the file, which the
// ctl does not own yet). env_layout is legacy as soon as ONE secret-class key
// sits in tenant.env.
func (p *previewer) classify() (map[string]string, []registry.SecretRef, string) {
	values := map[string]string{}
	var refs []registry.SecretRef
	layout := "managed"
	for _, k := range p.order {
		e := p.env[k]
		switch settings.Classify(k) {
		case settings.Public:
			values[k] = e.Value
		case settings.Secret:
			refs = append(refs, registry.SecretRef{Key: k, File: e.File})
			if e.File == string(model.SourceTenantEnv) {
				layout = "legacy"
			}
		case settings.ExecutableSurface:
			// Paths, URLs and interpreter knobs: CLI-only, so the registry
			// does not carry them as editable settings.
		default:
			// provision.env is new-tenant.sh's own bookkeeping, not a
			// ragstack setting: its keys are read (TENANT_ES_HEAP becomes
			// provision_heap) but never reported as unsupported drift.
			if e.File != string(model.SourceProvisionEnv) {
				p.info(doctor.UnsupportedEnvKey, fmt.Sprintf("%s is not in the settings classification table (%s)", k, e.File))
			}
		}
	}
	sort.Slice(refs, func(i, j int) bool { return refs[i].Key < refs[j].Key })
	if refs == nil {
		refs = []registry.SecretRef{}
	}
	return values, refs, layout
}

// ------------------------------------------------------------------ ports

// resolvePorts takes the tenant's block from manifest.tsv — the file
// new-tenant.sh allocated it in — and falls back to deriving it from PORT.
func (p *previewer) resolvePorts() error {
	man := filepath.Join(filepath.Dir(p.dataDir), "manifest.tsv")
	if b, err := os.ReadFile(man); err == nil {
		rows, perr := registry.ParseManifest(b)
		if perr != nil {
			return fmt.Errorf("adopt %s: %s: %w", p.name, man, perr)
		}
		for _, r := range rows {
			if r.Name != p.manifest {
				continue
			}
			p.ports = paths.Block(r.Index)
			if p.ports.Base != r.Base {
				return fmt.Errorf("adopt %s: manifest row %s claims base %d, the standard layout gives %d for index %d",
					p.name, r.Name, r.Base, p.ports.Base, r.Index)
			}
			return nil
		}
	}
	if v, ok := p.get("PORT"); ok {
		port, err := strconv.Atoi(v)
		if err == nil && port >= paths.PortBase && (port-paths.PortBase)%paths.PortStride == 0 {
			p.ports = paths.Block((port - paths.PortBase) / paths.PortStride)
			return nil
		}
	}
	return fmt.Errorf("adopt %s: no manifest.tsv row for %q and no derivable PORT — pass --manifest-name", p.name, p.manifest)
}

func (p *previewer) readListeners() error {
	ls, err := p.host.Listeners()
	if err != nil {
		return fmt.Errorf("adopt %s: listeners: %w", p.name, err)
	}
	p.listeners = make(map[int]hostfacts.Listener, len(ls))
	for _, l := range ls {
		p.listeners[l.Port] = l
	}
	return nil
}

// ------------------------------------------------------------------ stores

func (p *previewer) stores(drift *[]registry.Drift) registry.Stores {
	st := registry.Stores{
		Qdrant:        p.qdrant(),
		Elasticsearch: p.elasticsearch(drift),
		Neo4j:         p.neo4j(),
		Postgres:      p.postgres(),
	}
	// A tenant whose ES it owns will be started from a unit that binds
	// <data_dir>/elasticsearch/snapshots as path.repo, and apptainer refuses
	// a bind whose SOURCE is missing. Nothing created that directory before
	// it joined paths.ProvisionDirs, so every tenant adopted from the older
	// script is missing it — and the operator finds out when the unit fails
	// to start, not when the row is written.
	if st.Elasticsearch.Ownership == registry.OwnershipExclusive {
		if snaps := filepath.Join(p.dataDir, "elasticsearch", "snapshots"); !isDir(snaps) {
			p.warn(doctor.ESSnapshotsDirMissing, fmt.Sprintf(
				"%s is absent; the es unit binds it as path.repo and apptainer refuses a bind whose source is missing — create it before starting", snaps))
		}
	}
	st.DormantProvisionedDirs = p.dormant(st)
	if st.DormantProvisionedDirs {
		p.warn(doctor.DormantProvisionedDirs, fmt.Sprintf(
			"%s has provisioned store directories it does not use (its config points at a shared store); start/stop must never touch them", p.manifest))
	}
	return st
}

// ownership is exclusive when the URL's port is inside the tenant's own
// block and shared otherwise. Ports are evidence, not authority: a URL that
// is neither loopback nor in the allowed set is refused outright.
func (p *previewer) ownership(key, raw string) (ownership string, port int, ok bool) {
	if raw == "" {
		return registry.OwnershipUnknown, 0, false
	}
	allowed, reason := hostfacts.AllowedStoreURL(raw, p.ports, p.external)
	if !allowed {
		p.err(doctor.StoreURLDisallowed, fmt.Sprintf("%s=%s: %s", key, raw, reason))
		return registry.OwnershipUnknown, 0, false
	}
	u, _ := url.Parse(raw)
	port, _ = strconv.Atoi(u.Port())
	if port == p.ports.QdrantHTTP || port == p.ports.ESHTTP {
		return registry.OwnershipExclusive, port, true
	}
	return registry.OwnershipShared, port, true
}

func (p *previewer) qdrant() registry.Qdrant {
	raw, _ := p.get("QDRANT_URL")
	q := registry.Qdrant{Ownership: registry.OwnershipUnknown, ExtraEnv: map[string]string{}, URL: raw}
	own, port, ok := p.ownership("QDRANT_URL", raw)
	q.Ownership = own
	if !ok {
		return q
	}
	if own != registry.OwnershipExclusive {
		return q // a shared store the ctl only probes: no instance, no SIF
	}
	q.Instance = registry.NullString("qdrant-" + p.manifest)
	q.SIF = registry.NullString(filepath.Join(p.roots.ImagesDir, "qdrant.sif"))
	l, live := p.listeners[port]
	if !live || l.Pid == 0 {
		p.warn(doctor.StoreNotListening, fmt.Sprintf("qdrant-%s: nothing listening on %d", p.manifest, port))
		return q
	}
	if !looksLike(l, "qdrant") {
		p.warn(doctor.UnexpectedListener, fmt.Sprintf("port %d is held by pid %d (%s), which does not look like qdrant", port, l.Pid, firstArg(l.Cmdline)))
	}
	for k, v := range p.safeEnv(l.Pid) {
		if strings.HasPrefix(k, "QDRANT__") {
			q.ExtraEnv[k] = v
		}
	}
	return q
}

func (p *previewer) elasticsearch(drift *[]registry.Drift) registry.Elasticsearch {
	raw, _ := p.get("ELASTICSEARCH_URL")
	es := registry.Elasticsearch{Ownership: registry.OwnershipUnknown, ExtraEnv: map[string]string{}, URL: raw}
	if v, ok := p.get("TENANT_ES_HEAP"); ok && heapValue.MatchString(strings.ToLower(v)) {
		es.ProvisionHeap = registry.NullString(strings.ToLower(v))
	}
	own, port, ok := p.ownership("ELASTICSEARCH_URL", raw)
	es.Ownership = own
	if !ok || own != registry.OwnershipExclusive {
		return es
	}
	es.Instance = registry.NullString("elasticsearch-" + p.manifest)
	es.SIF = registry.NullString(filepath.Join(p.roots.ImagesDir, "elasticsearch.sif"))
	es.PathRepo = "/usr/share/elasticsearch/snapshots"
	l, live := p.listeners[port]
	if !live || l.Pid == 0 {
		p.warn(doctor.StoreNotListening, fmt.Sprintf("elasticsearch-%s: nothing listening on %d", p.manifest, port))
		return es
	}
	if !looksLike(l, "elasticsearch") {
		p.warn(doctor.UnexpectedListener, fmt.Sprintf("port %d is held by pid %d (%s), which does not look like elasticsearch", port, l.Pid, firstArg(l.Cmdline)))
	}
	env := p.safeEnv(l.Pid)
	if opts, ok := env["ES_JAVA_OPTS"]; ok {
		es.ExtraEnv["ES_JAVA_OPTS"] = opts
	}
	if h := heapOf(env["ES_JAVA_OPTS"], l.Cmdline); h != "" {
		es.Heap = registry.NullString(h)
	}
	if es.Heap != "" && es.ProvisionHeap != "" && es.Heap != es.ProvisionHeap {
		*drift = append(*drift, registry.Drift{
			Code: doctor.ESHeapDrift, Level: string(model.LevelWarn), Field: "ES_JAVA_OPTS",
			Expected: string(es.ProvisionHeap), Actual: string(es.Heap), ObservedAt: p.stamp(),
			Note: "live heap differs from provision.env",
		})
		p.warn(doctor.ESHeapDrift, fmt.Sprintf("elasticsearch-%s runs with %s, provision.env says %s", p.manifest, es.Heap, es.ProvisionHeap))
	}
	return es
}

// postgres derives the relational-store row from provision.env, which is
// where new-tenant.sh records the choice: TENANT_STORE_KIND is `sqlite`
// (default), `postgres-local` (a dedicated apptainer instance on the block's
// +5 port) or `postgres` (a database in a server somebody else runs), with
// TENANT_PG_HOST/TENANT_PG_PORT naming the server for the latter two.
//
// tenant.env's USER_STORE_DSN / JOB_STORE_DSN / COLLECTION_STORE_DSN are
// deliberately NOT read for this: they carry a password, they are already
// classified secret-class into secret_refs, and parsing one here would be the
// one code path that could put a credential into the registry. provision.env
// records the same host and port in the clear, so there is nothing to gain.
func (p *previewer) postgres() registry.Postgres {
	pg := registry.SQLiteStore()
	kind, _ := p.get("TENANT_STORE_KIND")
	switch strings.TrimSpace(kind) {
	case "", "sqlite":
		return pg
	case "postgres-local":
		pg.Kind = registry.PostgresKindLocal
	case "postgres":
		pg.Kind, pg.Ownership = registry.PostgresKindExternal, registry.OwnershipExternal
	default:
		// An unknown kind is recorded as sqlite — the default the tenant
		// would actually be running with if new-tenant.sh never rendered a
		// postgres env block — and said out loud rather than guessed at.
		p.warn(doctor.UnsupportedEnvKey, fmt.Sprintf(
			"provision.env TENANT_STORE_KIND=%q is not sqlite|postgres-local|postgres; the relational store is recorded as sqlite", kind))
		return pg
	}

	host := strings.TrimSpace(p.value("TENANT_PG_HOST"))
	port, _ := strconv.Atoi(strings.TrimSpace(p.value("TENANT_PG_PORT")))

	if pg.Kind == registry.PostgresKindExternal {
		if host == "" || port == 0 {
			p.err(doctor.StoreURLDisallowed, fmt.Sprintf(
				"provision.env records TENANT_STORE_KIND=postgres but not both TENANT_PG_HOST (%q) and TENANT_PG_PORT (%q); the server this tenant's DSNs point at cannot be recorded",
				host, p.value("TENANT_PG_PORT")))
			return registry.SQLiteStore()
		}
		pg.URL = registry.NullString(fmt.Sprintf("postgresql://%s:%d", host, port))
		pg.Port = registry.NullPort(port)
		return pg // no instance, no SIF, no data dir: not ours to start or back up
	}

	// postgres-local. The instance binds the block's +5 port; provision.env
	// is evidence of what was allocated, never authority over it, so a
	// disagreement with the block is reported and the BLOCK wins (that is
	// the port every other reader — doctor, the gateway, a future unit —
	// computes).
	if port != 0 && port != p.ports.PG {
		p.warn(doctor.PostgresPortDrift, fmt.Sprintf(
			"provision.env says TENANT_PG_PORT=%d but %s's block puts postgres on %d; recording %d",
			port, p.manifest, p.ports.PG, p.ports.PG))
	}
	pg.Port = registry.NullPort(p.ports.PG)
	pg.Instance = registry.NullString("postgres-" + p.name)
	pg.SIF = registry.NullString(filepath.Join(p.roots.ImagesDir, "postgres.sif"))
	pg.DataDir = registry.NullString(filepath.Join(p.dataDir, "postgres"))
	if host == "" {
		host = "localhost"
	}
	pg.URL = registry.NullString(fmt.Sprintf("postgresql://%s:%d", host, int(pg.Port)))

	// Attribution. The instance is `postgres-<name>` and its argv is
	// literally `postgres -c port=<pg> -c listen_addresses=127.0.0.1`, so
	// either name in the cmdline (or the cwd) identifies it. An unattributed
	// socket (Pid 0, the normal case when the ctl runs as svcbvbrc and the
	// tenant as wilke) is still THIS tenant's port: it is claimed, not
	// reported as a stranger.
	l, live := p.listeners[int(pg.Port)]
	switch {
	case !live:
		p.warn(doctor.PostgresNotListening, fmt.Sprintf(
			"postgres-%s: nothing listening on %d", p.name, int(pg.Port)))
	case l.Pid != 0 && !looksLike(l, "postgres") && !looksLike(l, "postgres-"+p.name):
		p.warn(doctor.UnexpectedListener, fmt.Sprintf(
			"port %d is held by pid %d (%s), which does not look like postgres", int(pg.Port), l.Pid, firstArg(l.Cmdline)))
	}
	return pg
}

// neo4j is never managed in v1: the constant is `external`, and the URL is
// recorded only so doctor and the backup manifest can name what is excluded.
func (p *previewer) neo4j() registry.Neo4j {
	uri, _ := p.get("NEO4J_URI")
	return registry.Neo4j{Ownership: registry.OwnershipExternal, URL: registry.NullString(uri)}
}

// dormant reports whether new-tenant.sh provisioned per-tenant store
// directories the tenant does not use — i.e. the directory exists while the
// corresponding store URL points at a shared instance. `start`/`stop` must
// refuse to touch that pair, and bin/up.sh (which would start empty
// instances on the block ports) must never run.
func (p *previewer) dormant(st registry.Stores) bool {
	if st.Qdrant.Ownership == registry.OwnershipShared && isDir(filepath.Join(p.dataDir, "qdrant", "storage")) {
		return true
	}
	if st.Elasticsearch.Ownership == registry.OwnershipShared && isDir(filepath.Join(p.dataDir, "elasticsearch", "data")) {
		return true
	}
	return false
}

// --------------------------------------------------------------- identity

func (p *previewer) identity() registry.Identity {
	id := registry.Identity{Provider: "none"}
	switch v, _ := p.get("IDENTITY_PROVIDER"); v {
	case "bvbrc", "oidc", "none":
		id.Provider = v
	case "":
	default:
		p.warn(doctor.UnsupportedEnvKey, fmt.Sprintf("IDENTITY_PROVIDER=%q is not one of bvbrc|oidc|none; recorded as none", v))
	}
	id.AdminSubjectsCount = len(adminSubjects(p.env["ADMIN_SUBJECTS"].Value))
	return id
}

// adminSubjects counts the entries of ADMIN_SUBJECTS in either accepted form
// (comma-separated, or a JSON array). The subjects themselves are identities
// and are deliberately NOT carried into the registry.
func adminSubjects(v string) []string {
	v = strings.TrimSpace(v)
	if v == "" {
		return nil
	}
	if strings.HasPrefix(v, "[") {
		var arr []string
		if err := json.Unmarshal([]byte(v), &arr); err == nil {
			return arr
		}
	}
	var out []string
	for _, s := range strings.Split(v, ",") {
		if s = strings.TrimSpace(s); s != "" {
			out = append(out, s)
		}
	}
	return out
}

// keys builds the key ledger from the API_KEYS / API_KEY_TENANTS /
// API_KEY_ROLES triple: one row per key, addressed by fingerprint. The key
// VALUES are hashed and dropped on the floor — they never reach a struct
// field, a log line or the registry.
func (p *previewer) keys() []registry.Key {
	raw, ok := p.get("API_KEYS")
	if !ok {
		return []registry.Key{}
	}
	var list []string
	if err := json.Unmarshal([]byte(raw), &list); err != nil {
		p.warn(doctor.UnsupportedEnvKey, "API_KEYS is not a JSON array; no key ledger was built")
		return []registry.Key{}
	}
	roles := p.jsonMap("API_KEY_ROLES")
	tenants := p.jsonMap("API_KEY_TENANTS")
	out := make([]registry.Key, 0, len(list))
	for i, k := range list {
		fp := fingerprint(k)
		role := roles[k]
		switch role {
		case "admin", "user":
		case "":
			role = orDefault(p.env["DEFAULT_ROLE"].Value, "user")
			if role != "admin" && role != "user" {
				role = "user"
			}
		default:
			p.warn(doctor.APIKeyRoleUnknown, fmt.Sprintf("key %d has role %q, which is not admin|user; recorded as user", i+1, role))
			role = "user"
		}
		out = append(out, registry.Key{
			ID:           "k" + strings.TrimPrefix(fp, "sha256:")[:11],
			Label:        fmt.Sprintf("adopted-%d", i+1),
			Role:         role,
			TenantString: orDefault(tenants[k], p.name),
			Fingerprint:  fp,
			CreatedAt:    p.stamp(), // when the ledger row was made; the key itself predates adoption
			CreatedBy:    "adopt",
			RevokedAt:    "",
			Effective:    true,
		})
	}
	return out
}

func (p *previewer) jsonMap(key string) map[string]string {
	raw, ok := p.get(key)
	if !ok {
		return map[string]string{}
	}
	var m map[string]string
	if err := json.Unmarshal([]byte(raw), &m); err != nil {
		p.warn(doctor.UnsupportedEnvKey, key+" is not a JSON object; ignored")
		return map[string]string{}
	}
	return m
}

// --------------------------------------------------------------- code / ui

func (p *previewer) code() registry.Code {
	c := registry.Code{Tag: "unknown"}
	g, err := p.host.Gitdir(p.worktree)
	if err != nil || g.Location == hostfacts.GitdirUnreadable {
		p.warn(doctor.WorktreeGitdirUnreadable, fmt.Sprintf("%s: no readable gitdir; code.tag recorded as unknown", p.worktree))
		return c
	}
	if g.Location == hostfacts.GitdirOutside {
		p.warn(doctor.WorktreeOutsideMirror, fmt.Sprintf("%s: gitdir %s is neither in the bare mirror nor in a prepared artifact", p.worktree, g.Path))
	}
	tag, err := p.host.GitDescribe(p.worktree)
	if err != nil || tag == "" {
		p.warn(doctor.WorktreeGitdirUnreadable, fmt.Sprintf("%s: git describe failed; code.tag recorded as unknown", p.worktree))
		return c
	}
	c.Tag = tag
	return c
}

// ValidateUIMode judges the (mode, port) pair on its own, before any host is
// read, so `adopt` and `adopt-all --spec` reject the same combinations the
// same way and can report them as a usage error rather than a failed read.
//
// The empty mode is the historical inference and is always legal: dev when a
// port was given, external when it was not.
func ValidateUIMode(mode string, port int) error {
	switch mode {
	case "":
		return nil
	case registry.UIModeStatic:
		if port != 0 {
			return fmt.Errorf("ui mode %s is served by nginx from <data_dir>/ui/dist and has no port; drop --ui-port %d", registry.UIModeStatic, port)
		}
	case registry.UIModeDev:
		// render.NginxTenants REFUSES a dev row without a port in range, and
		// it refuses it for the whole fleet's gateway file, not just this
		// tenant. Catch it here, where it is one operator's typo.
		if port == 0 {
			return fmt.Errorf("ui mode %s is a Vite dev server the ctl renders a unit for; it needs --ui-port", registry.UIModeDev)
		}
	case registry.UIModeExternal:
	default:
		return fmt.Errorf("ui mode %q is not one of %s|%s|%s", mode,
			registry.UIModeStatic, registry.UIModeDev, registry.UIModeExternal)
	}
	return nil
}

// ui records how the tenant's UI is served. Three shapes, and which one is
// recorded is the operator's statement (--ui-mode), not a guess:
//
//   - static: a `vite build` nginx serves from <data_dir>/ui/dist. No port —
//     the contract types ui.port nullable for exactly this — and
//     render.NginxStatic emits the alias block while NginxTenants leaves the
//     tenant out of $tenant_ui. The dist has to BE there, so an absent
//     index.html is an error-level finding rather than a mount that 404s.
//   - dev: the Vite dev server on UIPort, which the ctl renders a unit for.
//   - external: served by something the ctl does not manage. "external with
//     port 0" is also how "no UI the ctl can see" is recorded, because the
//     contract's mode enum is static|dev|external with no `none` — and
//     NginxTenants skips such a tenant's $tenant_ui row rather than failing
//     the whole fleet's gateway file.
func (p *previewer) ui() registry.UI {
	// The base is built from the REGISTRY KEY, not from --public-name: the
	// gateway renderer keys every location block, every map row and every
	// try_files fallback on t.Name, so a base derived from a different name
	// described a mount nginx does not serve. (render.NginxStatic now takes
	// t.UI.Base when it is set, which is the other half of the same fix: the
	// two cannot disagree because only one of them is authoritative.)
	ui := registry.UI{Mode: p.opts.UIMode, Base: "/ragstack/" + p.name + "/ui/"}
	if ui.Mode == "" {
		ui.Mode = registry.UIModeDev
		if p.opts.UIPort == 0 {
			ui.Mode = registry.UIModeExternal
		}
	}
	if ui.Mode == registry.UIModeStatic {
		index := doctor.UIDistIndex(p.dataDir)
		if !doctor.StaticUIDistOK(p.dataDir) {
			p.err(doctor.UIDistMissing, fmt.Sprintf(
				"ui mode static serves %s/ui/dist, but %s is not a regular file; run the tenant's `vite build --base %s` first",
				p.dataDir, index, ui.Base))
		}
		return ui
	}
	if p.opts.UIPort == 0 {
		return ui
	}
	ui.Port = registry.NullPort(p.opts.UIPort)
	if l, ok := p.listeners[p.opts.UIPort]; !ok || l.Port == 0 {
		p.warn(doctor.UIPortNotListening, fmt.Sprintf("no listener on the UI port %d", p.opts.UIPort))
	}
	return ui
}

// --------------------------------------------------------- files & refs

// externalRefs lists configuration values that are paths outside the data
// dir: they are backed up by reference and never moved.
func (p *previewer) externalRefs() []registry.ExternalRef {
	var out []registry.ExternalRef
	for _, k := range p.order {
		e := p.env[k]
		switch settings.Classify(k) {
		case settings.Public, settings.ExecutableSurface:
		default:
			continue
		}
		v := e.Value
		if !absPathValue.MatchString(v) || filepath.Clean(v) != v {
			continue
		}
		if under(v, p.dataDir) {
			continue
		}
		out = append(out, registry.ExternalRef{Key: k, Path: v})
		p.info(doctor.ExternalRefOutsideDataDir, fmt.Sprintf("%s=%s is outside %s", k, v, p.dataDir))
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Key < out[j].Key })
	if out == nil {
		out = []registry.ExternalRef{}
	}
	return out
}

// unmanagedFiles walks config/ and state/ for the leftovers a hand-run
// deployment accumulates: *.bak / *.bak-*, backup-*/ directories, *.orig,
// *.recovered and zero-byte *.db files. They are listed so nothing is
// silently orphaned; the ctl never edits or deletes them.
func (p *previewer) unmanagedFiles() []string {
	var out []string
	for _, sub := range []string{"config", "state"} {
		root := filepath.Join(p.dataDir, sub)
		_ = filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
			if err != nil {
				return nil //nolint:nilerr // an unreadable subtree is not a reason to fail adoption
			}
			rel, rerr := filepath.Rel(p.dataDir, path)
			if rerr != nil || rel == "." {
				return nil
			}
			base := d.Name()
			if d.IsDir() {
				if strings.HasPrefix(base, "backup-") {
					out = append(out, rel)
					return filepath.SkipDir
				}
				return nil
			}
			switch {
			case strings.Contains(base, ".bak-"), strings.HasSuffix(base, ".bak"),
				strings.HasSuffix(base, ".orig"), strings.HasSuffix(base, ".recovered"):
				out = append(out, rel)
			case strings.HasSuffix(base, ".db"):
				if fi, err := d.Info(); err == nil && fi.Size() == 0 {
					out = append(out, rel)
				}
			}
			return nil
		})
	}
	sort.Strings(out)
	if out == nil {
		out = []string{}
	}
	return out
}

// ------------------------------------------------------------- rollback

// rollback captures how the tenant was running BEFORE the ctl knew about it:
// enough for ops/coconut/restore.sh --tenant <n> to bring exactly this tenant
// back without the control plane. It is written once and never rewritten.
func (p *previewer) rollback(t *registry.Tenant) (*registry.RollbackDescriptor, error) {
	// A SEEDED redactor, not settings.RedactText: the descriptor records the
	// argv of every process the tenant runs, and an argv carries values
	// (`--api-key <k>`, a DSN) that no pattern can recognise but the tenant's
	// own secret files name exactly. The pattern-only redactor let those
	// through into the registry, which is a world-readable file.
	red := settings.NewRedactor()
	if err := envfile.SeedRedactor(filepath.Join(p.dataDir, "config"), red); err != nil {
		return nil, err
	}
	rd := &registry.RollbackDescriptor{
		CapturedAt: p.stamp(),
		Owner:      t.Owner,
		Paths: registry.RollbackPaths{
			DataDir: t.DataDir, Worktree: t.Worktree, PythonEnv: t.PythonEnv,
		},
		Ports:         t.Ports,
		Code:          t.Code,
		EnvFileSHA256: t.EnvFileSHA256,
		Images:        registry.RollbackImages{},
		LaunchArgs:    []registry.LaunchArg{},
		// No gateway generation exists yet (PR-B publishes the first one);
		// the contract types this as an integer, so 0 means "none".
		GatewayGeneration: 0,
	}
	if up := filepath.Join(p.dataDir, "bin", "up.sh"); fileExists(up) {
		rd.Paths.UpSh = registry.NullString(up)
	}
	if t.Stores.Qdrant.SIF != "" {
		rd.Images.Qdrant = &registry.Image{SIF: string(t.Stores.Qdrant.SIF), Version: unpinnedVersion, Digest: unpinnedDigest}
	}
	if t.Stores.Elasticsearch.SIF != "" {
		rd.Images.Elasticsearch = &registry.Image{SIF: string(t.Stores.Elasticsearch.SIF), Version: unpinnedVersion, Digest: unpinnedDigest}
	}
	add := func(kind string, port int) {
		l, ok := p.listeners[port]
		if !ok || l.Pid == 0 || len(l.Cmdline) == 0 {
			return
		}
		argv := make([]string, len(l.Cmdline))
		for i, a := range l.Cmdline {
			argv[i] = red.Redact(a)
		}
		cwd := l.Cwd
		if !absPathValue.MatchString(cwd) {
			cwd = "/"
		}
		rd.LaunchArgs = append(rd.LaunchArgs, registry.LaunchArg{Kind: kind, Argv: argv, Cwd: cwd})
	}
	add("api", p.ports.API)
	if p.opts.UIPort != 0 {
		add("ui", p.opts.UIPort)
	}
	if t.Stores.Qdrant.Ownership == registry.OwnershipExclusive {
		add("qdrant", p.ports.QdrantHTTP)
	}
	if t.Stores.Elasticsearch.Ownership == registry.OwnershipExclusive {
		add("es", p.ports.ESHTTP)
	}
	return rd, nil
}

// ------------------------------------------------------------- helpers

func (p *previewer) safeEnv(pid int) map[string]string {
	env, err := p.host.ProcEnv(pid, hostfacts.SafeEnvAllowed)
	if err != nil {
		return map[string]string{}
	}
	return env
}

func (p *previewer) pythonEnv(api hostfacts.Listener) string {
	if len(api.Cmdline) > 0 && strings.HasSuffix(api.Cmdline[0], "/bin/python") {
		return filepath.Dir(filepath.Dir(api.Cmdline[0]))
	}
	return orDefault(p.opts.PythonEnv, DefaultPythonEnv)
}

func (p *previewer) stamp() string { return p.now.Format(time.RFC3339) }

func (p *previewer) finding(level model.Level, code, detail string) {
	p.findings = append(p.findings, model.Finding{
		Level: level, Code: code, Tenant: registry.NullString(p.name), Detail: detail,
	})
}

func (p *previewer) info(code, detail string) { p.finding(model.LevelInfo, code, detail) }
func (p *previewer) warn(code, detail string) { p.finding(model.LevelWarn, code, detail) }
func (p *previewer) err(code, detail string)  { p.finding(model.LevelError, code, detail) }

// apiBind decides what goes in `api.bind`, in descending order of evidence:
// the live command line of the process on the API port, then the `--host` of
// the tenant's own launch script, then DefaultAPIBind.
//
// The last step is the one that has to exist. `api.bind` is a REQUIRED field
// whose contract pattern is (127.0.0.1|0.0.0.0|localhost), so an empty bind —
// which is what a stopped tenant produced, and what ANY host produces when
// the listener→pid mapping is unreadable (a rootless container cannot read
// the /proc/<pid>/fd links of host processes) — is a row registry.Save
// refuses, and `adopt --commit --force` exited 3 on a tenant that was merely
// not running.
//
// The default is recorded WITH a finding, never silently: 127.0.0.1 is the
// safe half of the old S2 lesson (an absent observation must never become
// 0.0.0.0), but it is still a default and not a fact, so the row says so and
// the tenant's next start replaces it with an observation.
func (p *previewer) apiBind(api hostfacts.Listener) string {
	if b := bindOf(api.Cmdline); b != "" {
		return b
	}
	if b, script := p.scriptBind(); b != "" {
		p.info(doctor.APIBindFromScript, fmt.Sprintf(
			"the API on port %d is not observable; %s starts it with --host %s, so that is what api.bind records",
			p.ports.API, script, b))
		return b
	}
	p.warn(doctor.APIBindAssumed, fmt.Sprintf(
		"the API on port %d is not listening and no launch script names a --host, so its bind could not be observed; api.bind records the default %s and the tenant's next start will confirm or correct it",
		p.ports.API, DefaultAPIBind))
	return DefaultAPIBind
}

// scriptBind reads `--host X` out of the tenant's launch script — the same
// files adopt already treats as inventory (bin/up.sh is the one the rollback
// descriptor records). Only a value the registry contract can store counts:
// bindOf also accepts ::1, which `api.bind` cannot hold, and recording it
// would put back the unsavable row this function exists to prevent.
func (p *previewer) scriptBind() (bind, script string) {
	for _, rel := range apiLaunchScripts {
		path := filepath.Join(p.dataDir, filepath.FromSlash(rel))
		b, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		if v := bindOf(scriptTokens(b)); contractBinds[v] {
			return v, path
		}
	}
	return "", ""
}

// scriptTokens splits a shell script into argv-like tokens, unquoting each
// one, so `--host "0.0.0.0"` reads the same as `--host 0.0.0.0`.
func scriptTokens(b []byte) []string {
	f := strings.Fields(string(b))
	for i, tok := range f {
		f[i] = strings.Trim(tok, "\"'")
	}
	return f
}

// bindOf reads `--host X` out of a uvicorn command line (the adopted tenants
// bind 0.0.0.0; managed ones will bind loopback).
//
// It returns "" when there is nothing to read — no API process to observe, a
// process this account cannot see, a command line without --host. UNKNOWN is
// not 0.0.0.0: the old default recorded the most exposed bind there is on the
// strength of no evidence, and render.Units then wrote `--host 0.0.0.0` into
// a systemd unit on an internet-reachable host. An empty bind means the
// renderer applies its loopback default instead.
func bindOf(argv []string) string {
	for i, a := range argv {
		if a == "--host" && i+1 < len(argv) {
			switch argv[i+1] {
			case "127.0.0.1", "0.0.0.0", "localhost", "::1":
				return argv[i+1]
			}
		}
	}
	return ""
}

// looksLike reports whether a listener's command line or cwd names want.
func looksLike(l hostfacts.Listener, want string) bool {
	if strings.Contains(strings.ToLower(l.Cwd), want) {
		return true
	}
	for _, a := range l.Cmdline {
		if strings.Contains(strings.ToLower(a), want) {
			return true
		}
	}
	return false
}

func firstArg(argv []string) string {
	if len(argv) == 0 {
		return ""
	}
	return argv[0]
}

// heapOf normalises the max heap from ES_JAVA_OPTS (preferred) or the java
// command line into the contract's `<n>m|<n>g` grammar.
func heapOf(javaOpts string, argv []string) string {
	for _, s := range append([]string{javaOpts}, argv...) {
		m := xmxFlag.FindStringSubmatch(s)
		if m == nil {
			continue
		}
		v := strings.ToLower(m[1])
		if heapValue.MatchString(v) {
			return v
		}
	}
	return ""
}

func fingerprint(secret string) string {
	sum := sha256.Sum256([]byte(secret))
	return "sha256:" + hex.EncodeToString(sum[:])[:16]
}

func sha256Hex(b []byte) string {
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

func under(path, root string) bool {
	path, root = filepath.Clean(path), filepath.Clean(root)
	return path == root || strings.HasPrefix(path, root+string(filepath.Separator))
}

func isDir(path string) bool {
	fi, err := os.Stat(path)
	return err == nil && fi.IsDir()
}

func fileExists(path string) bool {
	fi, err := os.Stat(path)
	return err == nil && !fi.IsDir()
}

func orDefault(v, def string) string {
	if v == "" {
		return def
	}
	return v
}
