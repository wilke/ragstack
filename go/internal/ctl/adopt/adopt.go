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
}

// DefaultPythonEnv is the shared conda env every tenant API runs from today.
const DefaultPythonEnv = "/rag/envs/ragstack"

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
	t.Owner = string(model.OwnerWilke)
	if api.Port != 0 {
		t.State = string(model.StateActive)
		if api.User != "" {
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
			t.Owner = api.User
			if !registry.KnownOwner(api.User) {
				p.err(doctor.OwnerNotInEnum, fmt.Sprintf(
					"the process on API port %d runs as %q, which is not one of the contract's owners (%s); a row recording it cannot be loaded back — hand the tenant over to a known account, or extend the enum in contracts/ctl/schemas/registry.json",
					p.ports.API, api.User, strings.Join(registry.Owners(), "|")))
			}
		}
	} else {
		p.warn(doctor.PortNotListening, fmt.Sprintf("no listener on the API port %d", p.ports.API))
	}
	t.API = registry.API{Bind: bindOf(api.Cmdline), PidFile: p.tp.PidFile, Log: p.tp.APILog}
	t.PythonEnv = p.pythonEnv(api)

	t.Code = p.code()
	t.UI = p.ui()
	t.Identity = p.identity()
	t.Settings, t.SecretRefs, t.EnvLayout = p.classify()
	t.Keys = p.keys()
	t.ExternalRefs = p.externalRefs()
	t.UnmanagedFiles = p.unmanagedFiles()
	t.Stores = p.stores(&t.Drift)
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

// ui records the dev-mode Vite server every adopted tenant runs today. The
// SHA of a `vite build` is not in play: mode `dev` is the honest answer until
// PR-D builds static bundles.
func (p *previewer) ui() registry.UI {
	ui := registry.UI{Mode: registry.UIModeDev, Base: "/ragstack/" + p.public + "/ui/"}
	if p.opts.UIPort == 0 {
		// No UI the ctl can see. The contract's mode enum is
		// static|dev|external with no `none`, so "external, port 0" is how
		// that is recorded — and render.NginxTenants skips such a tenant's
		// $tenant_ui row rather than failing the whole fleet's gateway file.
		ui.Mode = registry.UIModeExternal
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
