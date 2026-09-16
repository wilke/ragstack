// Package doctor answers one question — "is what the registry says still
// true of this host?" — and answers it the same way for the CLI, the HTTP
// API and the precondition gate in front of every mutation.
//
// A run produces findings with stable codes (codes.go), a status that is the
// max over their levels, and a hash over the findings themselves: a Plan pins
// that hash, and `--force-with-doctor-diff HASH` can only quote findings the
// operator has actually seen. With an op (`--op start`), the op's
// preconditions (preconditions.go) raise the warnings that op cannot tolerate
// to errors; nothing ever lowers an error, and a read-only op raises nothing.
//
// Like the rest of PR-A, doctor writes nothing: it reads /proc, the env
// files, the gateway maps, the permissions and the registry.
package doctor

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/envfile"
	"github.com/ragstack/ragstack/internal/ctl/hostfacts"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
)

// Defaults for the host-level checks.
const (
	// DefaultCtlUser is the service account the plan gives the control plane.
	DefaultCtlUser = "svcbvbrc"
	// DefaultSudoersGroup is the group ADR-0007 lists as fleet admins.
	DefaultSudoersGroup = "seed-admins-svcbvbrc"
	// DefaultCtlBinary is the installed binary (a symlink to …-<version>).
	DefaultCtlBinary = "/rag/bin/ragstack-ctl"
	// MinVMMaxMapCount is what Elasticsearch needs.
	MinVMMaxMapCount = 262144
	// DefaultMinFreeGB is the free-space reserve on the /rag filesystem.
	DefaultMinFreeGB = 200
	// sharedHeapGiB is what a shared Elasticsearch is assumed to hold when
	// its heap is not this tenant's fact to record.
	sharedHeapGiB = 1
)

// Options select the scope and supply the seams.
type Options struct {
	// Tenant limits the run to one tenant (host findings are always
	// included: they decide that tenant's fate too).
	Tenant string
	// Op scopes the run to one operation's preconditions.
	Op string

	Host         hostfacts.Host
	Now          func() time.Time
	RegistryPath string // default <roots>/data/tenants/registry.json
	CtlUser      string
	CtlUID       int
	CtlBinary    string
	SudoersGroup string
	MinFreeGB    int
	// ExternalStorePorts are the shared-store ports a tenant may point at.
	ExternalStorePorts []int
	// ImportCheck resolves `import ragstack` under a tenant's python env and
	// PYTHONPATH; nil ⇒ the real argv-only probe. Return ("", nil) to skip.
	ImportCheck func(pythonEnv, worktree string) (string, error)
	// CtlEnv is the CTL_* host-tool/directory values HomePathInProduction
	// checks for a home-directory path — the same names
	// api.SetHostToolsFromEnv reads into the engine's driver config (kept
	// here as literal strings: api imports doctor for the live doctor route,
	// so doctor importing api back would cycle). Nil reads the current
	// process's environment, which is what ctl-daemon.sh's load_env put
	// there for the daemon; a test passes an explicit map instead of
	// mutating its own environment.
	CtlEnv map[string]string
}

// Run diagnoses fleet against the host and returns the contract's response.
func Run(ctx context.Context, roots paths.Roots, fleet *registry.Fleet, opts Options) *model.DoctorResponse {
	d := &run{roots: roots, fleet: fleet, opts: opts, host: opts.Host}
	if d.host == nil {
		d.host = hostfacts.NewReal(roots)
	}
	d.now = time.Now().UTC()
	if opts.Now != nil {
		d.now = opts.Now().UTC()
	}
	if d.opts.CtlUser == "" {
		d.opts.CtlUser = DefaultCtlUser
	}
	if d.opts.SudoersGroup == "" {
		d.opts.SudoersGroup = DefaultSudoersGroup
	}
	if d.opts.CtlBinary == "" {
		d.opts.CtlBinary = DefaultCtlBinary
	}
	if d.opts.MinFreeGB == 0 {
		d.opts.MinFreeGB = DefaultMinFreeGB
	}
	if d.opts.RegistryPath == "" {
		d.opts.RegistryPath = roots.Registry()
	}
	if d.opts.ExternalStorePorts == nil {
		d.opts.ExternalStorePorts = hostfacts.DefaultExternalStorePorts
	}
	if d.opts.ImportCheck == nil {
		d.opts.ImportCheck = realImportCheck
	}
	if d.opts.CtlEnv == nil {
		d.opts.CtlEnv = ctlEnvFromProcess()
	}
	d.listeners()
	d.hostChecks()
	d.homePathHostChecks()
	d.manifestChecks()
	d.gatewayChecks()
	for _, t := range d.scopedTenants() {
		d.tenantChecks(ctx, t)
	}

	findings := applyPreconditions(d.findings, d.opts.Op, d.instanceTenants())
	sortFindings(findings)
	return &model.DoctorResponse{
		Status:      model.StatusFor(findings),
		Hash:        Hash(findings),
		GeneratedAt: d.now.Format(time.RFC3339),
		Scope: model.Scope{
			Tenant: registry.NullString(d.opts.Tenant),
			Op:     registry.NullString(d.opts.Op),
		},
		Findings: findings,
	}
}

// instanceTenants names the rows the ctl supervises ITSELF.
//
// It exists for one precondition: `start` and `restart` raise
// env_not_systemd_parsable to an error because a UNIT would load the wrong
// values out of a file systemd cannot parse. In instance mode there is no
// unit — the ctl parses tenant.env and secrets.env itself, with the same
// lenient parser `env-normalize` repairs them with — so the finding stays the
// warning envCheck raised it as, and the tenant is still startable by the
// operator who is on their way to fixing it.
//
// It narrows nothing else: every other precondition applies to both
// supervisors, because every other one is about the host rather than about
// systemd's grammar.
func (d *run) instanceTenants() map[string]bool {
	out := map[string]bool{}
	if d.fleet == nil {
		return out
	}
	for name, t := range d.fleet.Tenants {
		if t.Supervisor == string(model.SupervisorInstance) {
			out[name] = true
		}
	}
	return out
}

// Hash is sha256 over the sorted `code|tenant|detail` lines of a finding set.
// Levels are deliberately NOT part of it: the op scope changes levels, and an
// operator who has seen the findings has seen them whatever the op.
func Hash(findings []model.Finding) string {
	lines := make([]string, 0, len(findings))
	for _, f := range findings {
		lines = append(lines, fmt.Sprintf("%s|%s|%s", f.Code, string(f.Tenant), f.Detail))
	}
	sort.Strings(lines)
	sum := sha256.Sum256([]byte(strings.Join(lines, "\n")))
	return "sha256:" + hex.EncodeToString(sum[:])
}

type run struct {
	roots    paths.Roots
	fleet    *registry.Fleet
	opts     Options
	host     hostfacts.Host
	now      time.Time
	ports    map[int]hostfacts.Listener
	findings []model.Finding
}

func (d *run) listeners() {
	d.ports = map[int]hostfacts.Listener{}
	ls, err := d.host.Listeners()
	if err != nil {
		d.add(model.LevelWarn, UnexpectedListener, "", "cannot read the listening sockets: "+err.Error())
		return
	}
	for _, l := range ls {
		d.ports[l.Port] = l
	}
}

func (d *run) scopedTenants() []*registry.Tenant {
	var out []*registry.Tenant
	for _, t := range d.fleet.Tenants {
		if d.opts.Tenant != "" && t.Name != d.opts.Tenant {
			continue
		}
		out = append(out, t)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out
}

// ------------------------------------------------------------ host checks

func (d *run) hostChecks() {
	if members, err := d.host.SudoersGroupMembers(d.opts.SudoersGroup); err == nil {
		d.add(model.LevelInfo, SudoersGroup, "", fmt.Sprintf("%s: %s", d.opts.SudoersGroup, strings.Join(members, ", ")))
	}
	if !d.host.Linger(d.opts.CtlUser) {
		d.add(model.LevelWarn, LingerMissing, "", fmt.Sprintf("no /var/lib/systemd/linger/%s: the user manager does not survive logout and nothing starts at boot", d.opts.CtlUser))
	}
	if d.opts.CtlUID > 0 {
		if !d.host.RuntimeDir(d.opts.CtlUID) {
			d.add(model.LevelWarn, RuntimeDirMissing, "", fmt.Sprintf("/run/user/%d is absent: there is no user bus to talk to", d.opts.CtlUID))
		}
		dropin, err := d.host.UserDropIn(d.opts.CtlUID)
		switch {
		case err != nil || !dropin.Present:
			d.add(model.LevelWarn, UserDropInMissing, "", fmt.Sprintf("no /etc/systemd/system/user@%d.service.d drop-in: units would load from the NFS home and may start before /rag is mounted", d.opts.CtlUID))
		case dropin.SystemdUnitPath == "" || !mentions(dropin.RequiresMountsFor, d.roots.RagRoot):
			d.add(model.LevelWarn, UserDropInMissing, "", fmt.Sprintf("the user@%d drop-in does not set both SYSTEMD_UNIT_PATH and RequiresMountsFor=%s", d.opts.CtlUID, d.roots.RagRoot))
		}
	}
	if n, err := d.host.SysctlMaxMapCount(); err == nil && n < MinVMMaxMapCount {
		d.add(model.LevelError, VMMaxMapCountLow, "", fmt.Sprintf("vm.max_map_count=%d < %d: Elasticsearch will not start", n, MinVMMaxMapCount))
	}
	if free, err := d.host.DiskFree(d.roots.RagRoot); err == nil {
		if min := int64(d.opts.MinFreeGB) << 30; free < min {
			d.add(model.LevelWarn, DiskLow, "", fmt.Sprintf("%s has %.1f GiB free, below the %d GiB reserve", d.roots.RagRoot, float64(free)/(1<<30), d.opts.MinFreeGB))
		}
	}
	d.heapSum()
	d.bootHook()
	d.aclManagedRoots()
	d.writable("", d.opts.CtlBinary)
	if units, err := filepath.Glob(filepath.Join(d.roots.UnitsDir(), "*")); err == nil {
		for _, u := range units {
			d.writable("", u)
		}
	}
}

// heapSum adds up what Elasticsearch is allowed to take: every exclusive
// tenant heap plus one GiB for each shared store a tenant leans on. Above
// half of MemTotal the host is one ingest away from swapping.
func (d *run) heapSum() {
	total, err := d.host.MemTotalBytes()
	if err != nil || total <= 0 {
		return
	}
	var sum int64
	shared := map[string]bool{}
	for _, t := range d.fleet.Tenants {
		es := t.Stores.Elasticsearch
		switch es.Ownership {
		case registry.OwnershipExclusive:
			n, err := heapBytes(string(es.Heap))
			if err != nil {
				d.add(model.LevelWarn, ESHeapUnparsable, t.Name, fmt.Sprintf(
					"stores.elasticsearch.heap=%q cannot be read as a size: it is left out of the heap sum, which is therefore an UNDER-estimate", es.Heap))
				continue
			}
			sum += n
		case registry.OwnershipShared:
			if es.URL != "" && !shared[es.URL] {
				shared[es.URL] = true
				sum += int64(sharedHeapGiB) << 30
			}
		}
	}
	if sum*2 > total {
		d.add(model.LevelWarn, ESHeapSumHigh, "", fmt.Sprintf("Elasticsearch heaps total %.1f GiB of %.1f GiB RAM (over half)", float64(sum)/(1<<30), float64(total)/(1<<30)))
	}
}

// manifestChecks compares the on-disk projection with the registry.
func (d *run) manifestChecks() {
	man := registry.PathsFor(d.opts.RegistryPath).Manifest
	b, err := os.ReadFile(man)
	if err != nil {
		return // no projection yet: nothing to disagree with
	}
	err = registry.ReconcileManifest(d.fleet, b)
	switch {
	case err == nil:
	case errors.Is(err, registry.ErrManifestUnknownRows):
		d.add(model.LevelError, ManifestUnknownRow, "", fmt.Sprintf("%s: %v", man, err))
	case errors.Is(err, registry.ErrManifestMismatch), errors.Is(err, registry.ErrManifestMissingRows):
		d.add(model.LevelError, RegistryManifestMismatch, "", fmt.Sprintf("%s: %v", man, err))
	default:
		d.add(model.LevelError, RegistryManifestMismatch, "", fmt.Sprintf("%s: %v", man, err))
	}
}

// gatewayChecks compare the live routing table with the registry's ports.
func (d *run) gatewayChecks() {
	maps, ok, err := GatewayMaps(d.roots.ProxyDir)
	if err != nil {
		// Not "no gateway yet": the live routing table is THERE and
		// unreadable, so every port comparison below is one this run silently
		// did not make.
		d.add(model.LevelError, GatewayMapMismatch, "", fmt.Sprintf(
			"%s: the live routing table could not be read, so no tenant's gateway port was checked: %v", d.roots.ProxyDir, err))
		return
	}
	if !ok {
		return
	}
	for _, t := range d.scopedTenants() {
		port, routed := maps.API[t.Name]
		if !routed {
			continue // an unrouted tenant is PR-B's business, not a mismatch
		}
		if port != t.Ports.API {
			d.add(model.LevelError, GatewayMapMismatch, t.Name, fmt.Sprintf("%s routes %s to :%d, the registry allocates :%d", maps.Source, t.Name, port, t.Ports.API))
		}
	}
}

// ---------------------------------------------------------- tenant checks

func (d *run) tenantChecks(_ context.Context, t *registry.Tenant) {
	api, listening := d.ports[t.Ports.API]
	switch {
	case t.State == string(model.StateActive) && !listening:
		// Warn, not error: this IS the crashed-tenant shape, and an
		// unconditional error refused the very ops that repair it (`start`,
		// `restart`). preconditions.go raises it for the ops that must not
		// run over a tenant whose live state contradicts the registry.
		d.add(model.LevelWarn, PortNotListening, t.Name, fmt.Sprintf("state is active but nothing listens on :%d", t.Ports.API))
	case listening && api.User == "" && d.preHandover(t):
		// The owner is UNREADABLE, and the registry owner is not this
		// account — the pre-handover shape every tenant is in until PR-E, the
		// same class as secrets_unreadable_by_ctl one directory over. There is
		// no readable owner to compare against the registry, so this is not a
		// mismatch: info, not error.
		d.add(model.LevelInfo, PortOwnerUnverifiable, t.Name, fmt.Sprintf(
			":%d is held by a process this account cannot attribute (owner unreadable: /proc/<pid>/fd is not readable across accounts); the registry records owner %s",
			t.Ports.API, t.Owner))
	case listening && api.User == "":
		// Unreadable, but the registry says THIS account owns it — the ctl
		// should be able to read its own listener. Something is genuinely
		// wrong, so this stays an error rather than being folded into the
		// pre-handover info case above.
		d.add(model.LevelError, PortOwnerMismatch, t.Name, fmt.Sprintf(
			":%d is held by a process this account cannot attribute (owner unreadable: /proc/<pid>/fd is not readable across accounts); the registry records owner %s",
			t.Ports.API, t.Owner))
	case listening && api.User != t.Owner:
		d.add(model.LevelError, PortOwnerMismatch, t.Name, fmt.Sprintf(":%d is held by pid %d running as %s, the registry records owner %s", t.Ports.API, api.Pid, api.User, t.Owner))
	}
	d.unexpectedListeners(t)
	d.postgresCheck(t)
	d.uiCheck(t)
	d.envCheck(t)
	d.codeChecks(t)
	d.storeChecks(t)
	d.permissionChecks(t)
	d.homePathCheck(t)
	d.capabilityChecks(t)
}

// capabilityChecks reports what the ctl may and may not do to this tenant's
// stores.
//
// Two findings, and the difference between them is the whole point:
//
//   - capabilities_unconfirmed (INFO) is the adopted tenant's normal state —
//     nothing is confirmed, so every store op refuses. It used to be added
//     unconditionally, which meant a tenant whose legs an operator HAD
//     confirmed still reported "store capabilities are all false" for as long
//     as the row existed.
//   - stores_unconfirmed (WARN) is narrower and is the one a handover reads:
//     a leg this tenant owns EXCLUSIVELY, which the ctl would therefore be
//     expected to start and stop after the handover, whose `stop` capability
//     is still false. Such a handover moves the API to the service account
//     and leaves the tenant's own qdrant/elasticsearch/postgres running as
//     whoever started them — which is not a handover, it is a split tenant.
//     The repair is `adopt <t> --readopt --confirm-stores <legs>`, and `adopt`
//     TOLERATES the finding (preconditions.go) because it is the op that
//     clears it.
func (d *run) capabilityChecks(t *registry.Tenant) {
	var unconfirmed []string
	confirmed := 0
	for _, leg := range exclusiveLegs(t) {
		if leg.caps.Stop {
			confirmed++
			continue
		}
		unconfirmed = append(unconfirmed, leg.name)
	}
	if allCapabilitiesFalse(t) {
		d.add(model.LevelInfo, CapabilitiesUnconfirmed, t.Name,
			"store capabilities are all false: stop, purge, snapshot and restore refuse until an operator confirms "+
				"process identity, backing path and exclusive ownership")
	}
	if len(unconfirmed) > 0 {
		d.add(model.LevelWarn, StoresUnconfirmed, t.Name, fmt.Sprintf(
			"%s exclusively owned by this tenant but capabilities.stop is still false: the ctl would supervise the "+
				"API and leave %s running as whoever started them. Confirm with `ragstack-ctl adopt %s --readopt "+
				"--confirm-stores %s --commit`",
			strings.Join(unconfirmed, ", "), plural(len(unconfirmed), "it", "them"), t.Name,
			strings.Join(unconfirmed, ",")))
	}
	_ = confirmed
}

// exclusiveLeg is one store leg of a tenant, for the two questions this file
// asks of it: what it is called, and what an operator has confirmed about it.
type exclusiveLeg struct {
	name string
	caps registry.Capabilities
}

// exclusiveLegs are the store legs this tenant owns ALONE — the ones a
// handover would make the ctl responsible for starting and stopping. A shared
// leg is deliberately absent: confirming `stop` on a store three tenants use
// would be confirming the ctl may take the other two down.
func exclusiveLegs(t *registry.Tenant) []exclusiveLeg {
	var out []exclusiveLeg
	if t.Stores.Qdrant.Ownership == registry.OwnershipExclusive {
		out = append(out, exclusiveLeg{"qdrant", t.Stores.Qdrant.Capabilities})
	}
	if t.Stores.Elasticsearch.Ownership == registry.OwnershipExclusive {
		out = append(out, exclusiveLeg{"elasticsearch", t.Stores.Elasticsearch.Capabilities})
	}
	// Only a `local` postgres is a server of this tenant's that the ctl could
	// start or stop. `sqlite` is exclusive too (its files are the tenant's
	// alone) and has no process at all, so a `stop` capability on it would
	// describe nothing.
	if t.Stores.Postgres.Kind == registry.PostgresKindLocal &&
		t.Stores.Postgres.Ownership == registry.OwnershipExclusive {
		out = append(out, exclusiveLeg{"postgres", t.Stores.Postgres.Capabilities})
	}
	return out
}

// allCapabilitiesFalse reports the adopted tenant's starting state: nothing
// confirmed on any leg.
func allCapabilitiesFalse(t *registry.Tenant) bool {
	for _, c := range []registry.Capabilities{
		t.Stores.Qdrant.Capabilities, t.Stores.Elasticsearch.Capabilities, t.Stores.Postgres.Capabilities,
	} {
		if c.Stop || c.Purge || c.Snapshot || c.Restore {
			return false
		}
	}
	return true
}

func plural(n int, one, many string) string {
	if n == 1 {
		return one
	}
	return many
}

// postgresCheck asks the one question a dedicated relational store raises:
// is it up? A `local` tenant keeps its ACL, job and collection state in
// postgres-<name>, so an active tenant with nothing on that port has an API
// that cannot answer — a fact no other finding covers, because the +5 port is
// now attributed to the tenant and so no longer reaches unexpectedListeners.
//
// A stopped tenant is not asked (its stores are meant to be down), and the
// other two kinds have no server of this tenant's to be up or down.
func (d *run) postgresCheck(t *registry.Tenant) {
	pg := t.Stores.Postgres
	if pg.Kind != registry.PostgresKindLocal || t.State != string(model.StateActive) {
		return
	}
	port := int(pg.Port)
	if port == 0 {
		port = t.Ports.PG
	}
	if _, ok := d.ports[port]; ok {
		return
	}
	instance := string(pg.Instance)
	if instance == "" {
		instance = "postgres-" + t.Name
	}
	d.add(model.LevelWarn, PostgresNotListening, t.Name, fmt.Sprintf(
		"state is active but nothing listens on :%d, the dedicated relational store (%s); the tenant's user, job and collection stores are all unreachable",
		port, instance))
}

// uiCheck re-runs adoption's static-UI precondition on EVERY pass.
//
// adopt checks <data_dir>/ui/dist/index.html once, at adoption. The dist is a
// build artifact: it is deleted by a `git clean`, replaced by a rebuild, and
// moved by a handover — long after adoption, and with no finding to say so.
// nginx's alias block keeps pointing at it either way, so the tenant's UI
// answers 404 while doctor reports a clean fleet. Same code, same finding,
// re-asked.
func (d *run) uiCheck(t *registry.Tenant) {
	if t.UI.Mode != registry.UIModeStatic || StaticUIDistOK(t.DataDir) {
		return
	}
	base := t.UI.Base
	if base == "" {
		base = "/ragstack/" + t.Name + "/ui/"
	}
	d.add(model.LevelError, UIDistMissing, t.Name, fmt.Sprintf(
		"ui mode static serves %s/ui/dist, but %s is not a regular file; the gateway answers %s with 404 until `vite build --base %s` is run",
		t.DataDir, UIDistIndex(t.DataDir), base, base))
}

// unexpectedListeners looks at the six allocated service ports of a tenant's
// block and reports a listener on one the registry does not use. (The rest of
// the 20-port block is unallocated by design and not policed here.)
func (d *run) unexpectedListeners(t *registry.Tenant) {
	used := map[int]bool{t.Ports.API: true}
	if t.Stores.Qdrant.Ownership == registry.OwnershipExclusive {
		used[t.Ports.QdrantHTTP], used[t.Ports.QdrantGRPC] = true, true
	}
	if t.Stores.Elasticsearch.Ownership == registry.OwnershipExclusive {
		used[t.Ports.ESHTTP], used[t.Ports.ESTransport] = true, true
	}
	if t.UI.Port != 0 {
		used[int(t.UI.Port)] = true
	}
	// The +5 port is the tenant's own ONLY with a dedicated instance
	// (`new-tenant.sh --postgres local`). A `sqlite` or `external` tenant
	// binds nothing there, so a listener on it stays what it has always been:
	// a stranger in this tenant's block, reported.
	if t.Stores.Postgres.Kind == registry.PostgresKindLocal {
		used[t.Ports.PG] = true
		if pg := int(t.Stores.Postgres.Port); pg != 0 {
			used[pg] = true
		}
	}
	for _, p := range []int{t.Ports.API, t.Ports.QdrantHTTP, t.Ports.QdrantGRPC, t.Ports.ESHTTP, t.Ports.ESTransport, t.Ports.PG} {
		if used[p] {
			continue
		}
		if l, ok := d.ports[p]; ok {
			d.add(model.LevelWarn, UnexpectedListener, t.Name, fmt.Sprintf(":%d is in %s's block and unused by the registry, but pid %d (%s) listens on it", p, t.Name, l.Pid, firstArg(l.Cmdline)))
		}
	}
}

// envCheck re-parses the env files the unit LOADS against the systemd
// EnvironmentFile grammar. A systemd-supervised tenant would load wrong
// values, so there it is an error; a manual one is still sourced by a shell
// that reads it correctly.
//
// Both files are checked: the api unit has `EnvironmentFile=<tenant.env>` AND
// `EnvironmentFile=-<secrets.env>`, so a stray inline comment in secrets.env
// puts ` # rotated` on the end of an API key just as surely — and that one is
// invisible in a log, because the value is redacted before anyone sees it.
// tenant.env is required (its absence is a finding); secrets.env is optional.
func (d *run) envCheck(t *registry.Tenant) {
	level := model.LevelWarn
	if t.Supervisor == string(model.SupervisorSystemd) {
		level = model.LevelError
	}
	for _, name := range []string{"tenant.env", "secrets.env"} {
		path := filepath.Join(t.DataDir, "config", name)
		b, err := os.ReadFile(path)
		if err != nil {
			if name == "secrets.env" && errors.Is(err, os.ErrNotExist) {
				continue // the unit loads it with `-`: absence is legal
			}
			if errors.Is(err, fs.ErrPermission) && d.preHandover(t) {
				// Not a grammar finding, and not this account's business
				// yet: the file belongs to the tenant's owner until PR-E.
				// secrets_unreadable_by_ctl states it once, at info, instead
				// of this check reporting an unreadable file as a broken one
				// (at ERROR for a systemd tenant) on every doctor run.
				continue
			}
			d.add(level, EnvNotSystemdParsable, t.Name, fmt.Sprintf("%s: %v", path, err))
			continue
		}
		f, problems, perr := envfile.ParseLenient(b)
		if perr != nil {
			d.add(level, EnvNotSystemdParsable, t.Name, fmt.Sprintf("%s: %v", path, perr))
			continue
		}
		problems = append(problems, f.Validate()...)
		seen := map[string]bool{}
		for _, p := range problems {
			key := fmt.Sprintf("%d|%s|%s", p.Line, p.Class, p.Key)
			if seen[key] {
				continue
			}
			seen[key] = true
			d.addRepair(level, EnvNotSystemdParsable, t.Name,
				fmt.Sprintf("%s line %d: %s (%s): %s", name, p.Line, p.Class, p.Key, p.Msg), "env-normalize")
		}
	}
}

func (d *run) codeChecks(t *registry.Tenant) {
	g, err := d.host.Gitdir(t.Worktree)
	if err != nil || g.Location == hostfacts.GitdirUnreadable {
		d.add(model.LevelWarn, WorktreeGitdirUnreadable, t.Name, fmt.Sprintf("%s: no readable gitdir; code.tag cannot be confirmed", t.Worktree))
	} else if g.Location == hostfacts.GitdirOutside {
		d.addRepair(model.LevelWarn, WorktreeOutsideMirror, t.Name,
			fmt.Sprintf("%s: gitdir %s is neither in the bare mirror nor in a prepared artifact", t.Worktree, g.Path), "handover")
	}
	resolved, err := d.opts.ImportCheck(t.PythonEnv, t.Worktree)
	if err != nil || resolved == "" {
		return
	}
	if !under(resolved, t.Worktree) {
		d.add(model.LevelWarn, ImportRagstackOutsideWorktree, t.Name, fmt.Sprintf("import ragstack resolves to %s, outside %s: the running code is not the recorded code", resolved, t.Worktree))
	}
}

// ctlEnvKeys are the CTL_* variables api/env.go defines for a host-tool
// binary, the mirror, or a cache/state directory — the ones the
// production-layout plan means by "a ctl.env value": a home-directory path
// there is exactly how coconut ran CTL_NODE_BIN=~/.local/bin/node until
// `make install-node` gave it somewhere under /rag to point at instead.
var ctlEnvKeys = []string{
	"CTL_SYSTEMCTL_BIN", "CTL_GIT_BIN", "CTL_NODE_BIN", "CTL_NPM_BIN",
	"CTL_APPTAINER_BIN", "CTL_MIRROR", "CTL_NPM_CACHE",
	"CTL_STATE_DIR", "CTL_CONFIG_DIR",
}

// ctlEnvFromProcess is Options.CtlEnv's default: whatever of ctlEnvKeys is
// set in THIS process's environment, which for the daemon is ctl.env as
// ctl-daemon.sh's load_env put it there, and for a --direct CLI run is
// whatever the operator's shell exported.
func ctlEnvFromProcess() map[string]string {
	out := map[string]string{}
	for _, k := range ctlEnvKeys {
		if v := strings.TrimSpace(os.Getenv(k)); v != "" {
			out[k] = v
		}
	}
	return out
}

// homePathReason says why path violates "nothing in production may
// reference a home directory" (plan, 2026-09-15), or "" when it does not.
// /home is a literal prefix, not a lookup of any one account's actual home:
// a production path must not depend on WHICH account's home it happens to
// be under, so /home/<anyone> is flagged the same way as /home/wilke.
func homePathReason(path string) string {
	switch p := strings.TrimSpace(path); {
	case p == "":
		return ""
	case p == "~" || strings.HasPrefix(p, "~/"):
		return "starts with ~"
	case p == "/home" || strings.HasPrefix(p, "/home/"):
		return "is under /home"
	default:
		return ""
	}
}

// homePathHostChecks is HomePathInProduction's host-scoped half: a prepared
// artifact's worktree and the ctl.env host-tool values, neither of which
// belongs to one tenant. codeChecks's registry paths and a tenant's live API
// process are homePathCheck below, run per tenant.
func (d *run) homePathHostChecks() {
	for id, a := range d.fleet.Artifacts {
		if why := homePathReason(a.Worktree); why != "" {
			d.add(model.LevelWarn, HomePathInProduction, "", fmt.Sprintf("artifacts[%s].worktree=%q %s", id, a.Worktree, why))
		}
	}
	for _, k := range ctlEnvKeys {
		v, ok := d.opts.CtlEnv[k]
		if !ok {
			continue
		}
		if why := homePathReason(v); why != "" {
			d.add(model.LevelWarn, HomePathInProduction, "", fmt.Sprintf("%s=%q (ctl.env) %s", k, v, why))
		}
	}
}

// homePathCheck is HomePathInProduction's per-tenant half: t's own registry
// paths, and — the one live-host fact this needs — its API process's cwd and
// argv[0], off the SAME listener table tenantChecks already built from
// hostfacts (which reads /proc for exactly this, for an adopted,
// pre-handover tenant this account cannot otherwise attribute).
func (d *run) homePathCheck(t *registry.Tenant) {
	check := func(field, path string) {
		if why := homePathReason(path); why != "" {
			d.add(model.LevelWarn, HomePathInProduction, t.Name, fmt.Sprintf("%s=%q %s", field, path, why))
		}
	}
	check("data_dir", t.DataDir)
	check("worktree", t.Worktree)
	check("python_env", t.PythonEnv)
	if l, ok := d.ports[t.Ports.API]; ok {
		check("live api process cwd", l.Cwd)
		if len(l.Cmdline) > 0 {
			check("live api process argv[0]", l.Cmdline[0])
		}
	}
}

func (d *run) storeChecks(t *registry.Tenant) {
	for _, s := range []struct{ key, url string }{
		{"QDRANT_URL", t.Stores.Qdrant.URL},
		{"ELASTICSEARCH_URL", t.Stores.Elasticsearch.URL},
	} {
		if s.url == "" {
			continue
		}
		if ok, reason := hostfacts.AllowedStoreURL(s.url, t.Ports, d.opts.ExternalStorePorts); !ok {
			d.add(model.LevelError, StoreURLDisallowed, t.Name, fmt.Sprintf("%s=%s: %s", s.key, s.url, reason))
		}
	}
	// A store the tenant owns exclusively is started from a unit that binds
	// <data_dir>/elasticsearch/snapshots as ES's path.repo. apptainer refuses
	// a bind whose SOURCE is missing, so an absent directory is not a
	// degraded snapshot capability — it is a service that will not come up.
	// Nothing created that directory before it joined paths.ProvisionDirs, so
	// every tenant provisioned by the older script is in this state.
	if t.Stores.Elasticsearch.Ownership == registry.OwnershipExclusive {
		snaps := filepath.Join(t.DataDir, "elasticsearch", "snapshots")
		if fi, err := os.Stat(snaps); err != nil || !fi.IsDir() {
			d.add(model.LevelWarn, ESSnapshotsDirMissing, t.Name, fmt.Sprintf(
				"%s is absent; the es unit binds it as path.repo and apptainer refuses a bind whose source is missing — create it before starting", snaps))
		}
	}
	if !t.Stores.DormantProvisionedDirs {
		return
	}
	up := filepath.Join(t.DataDir, "bin", "up.sh")
	if _, err := os.Stat(up); err == nil {
		d.add(model.LevelError, DormantProvisionedDirs, t.Name, fmt.Sprintf(
			"%s runs on shared stores but %s would start empty instances on its block ports; the ctl never edits that file — retire it by hand", t.Name, up))
		return
	}
	d.add(model.LevelWarn, DormantProvisionedDirs, t.Name, fmt.Sprintf("%s has provisioned store directories it does not use", t.Name))
}

// permissionChecks walk the paths the ctl has to trust for this tenant: the
// ones nobody else may WRITE, and the ones the ctl itself must be able to
// READ.
func (d *run) permissionChecks(t *registry.Tenant) {
	d.secretsReadable(t)
	d.writable(t.Name, t.DataDir)
	d.writable(t.Name, filepath.Join(t.DataDir, "config", "secrets.env"))
	for _, sif := range []string{string(t.Stores.Qdrant.SIF), string(t.Stores.Elasticsearch.SIF)} {
		if sif != "" {
			d.writable(t.Name, sif)
		}
	}
}

// secretsReadable reports the secret-class env files this account cannot
// read for a tenant it does not own — the state every tenant is in until PR-E
// hands it over, and the reason its logs endpoint answers 409 `refused`
// rather than serving a tail the ctl could not redact.
//
// The file list comes from envfile.SeedPaths, the same list the redactor
// actually mines (live files AND the historical copies beside them), so this
// finding cannot describe a different set of files than the one that refuses
// the request.
//
// Bounded to an owner mismatch on purpose: when the tenant is already this
// account's, an unreadable secrets.env is a real defect rather than a pending
// handover, and it is not softened to info here — it surfaces through
// env_not_systemd_parsable with that tenant's supervisor level.
func (d *run) secretsReadable(t *registry.Tenant) {
	if !d.preHandover(t) {
		return
	}
	var blocked []string
	for _, p := range envfile.SeedPaths(filepath.Join(t.DataDir, "config")) {
		if unreadable(p) {
			blocked = append(blocked, filepath.Base(p))
		}
	}
	if len(blocked) == 0 {
		return
	}
	d.add(model.LevelInfo, SecretsUnreadableByCtl, t.Name, fmt.Sprintf(
		"%s cannot read %s in %s (owner %s): those values seed the log redactor, so GET /v1/tenants/%s/logs answers 409 refused until the handover (PR-E)",
		d.ctlAccount(), strings.Join(blocked, ", "), filepath.Join(t.DataDir, "config"), t.Owner, t.Name))
}

// preHandover: the tenant's registry owner is not the account this process
// runs as.
func (d *run) preHandover(t *registry.Tenant) bool {
	return t.Owner != "" && t.Owner != d.ctlAccount()
}

// ctlAccount is the account this process actually runs as — the host's answer,
// not the configured expectation, falling back to the option when the host
// cannot say.
func (d *run) ctlAccount() string {
	if u := d.host.Username(); u != "" {
		return u
	}
	return d.opts.CtlUser
}

// unreadable reports whether path exists but this account is refused it. An
// absent path is not unreadable (the redactor skips it), and any other error
// is another check's problem.
func unreadable(path string) bool {
	f, err := os.Open(path)
	if err == nil {
		_ = f.Close()
		return false
	}
	return errors.Is(err, fs.ErrPermission)
}

func (d *run) writable(tenant, path string) {
	if path == "" {
		return
	}
	if _, err := os.Lstat(path); err != nil {
		return // absent paths are another check's problem
	}
	w, err := d.host.WritableByOthers(path)
	if err != nil {
		return
	}
	if w.Writable {
		d.add(model.LevelError, WritableByOthers, tenant, fmt.Sprintf("%s: %s is %s", path, w.Path, w.Reason))
	}
	d.aclGrantsOthers(tenant, path, w)
}

// aclGrantsOthers reports the named ACL entries on a trusted path that hand
// WRITE to someone the ctl did not intend.
//
// Named entries that are legitimate and silent: the ctl SERVICE account's
// (that is what `fleet grant` writes), the account this process happens to be
// running as, and the path owner's own (a redundant entry for somebody who
// already holds the owner triple). Everything else is an error — including
// every named GROUP, because the grant never writes one and a group on this
// host means the 1869 members of cels.
//
// The service account is exempted by its CONFIGURED name rather than by
// Username(): doctor is run by wilke as often as by svcbvbrc (the acceptance
// step is "as wilke: fleet grant, then doctor"), and a check that only knew
// the running account would paint every granted path red the moment the owner
// ran it.
func (d *run) aclGrantsOthers(tenant, path string, w hostfacts.Writability) {
	var offenders []string
	seen := map[string]bool{}
	for _, g := range w.ACLGrants {
		if !g.Group && (g.Name == d.opts.CtlUser || g.Name == d.ctlAccount() || (g.Owner != "" && g.Name == g.Owner) ||
			g.Name == d.managedRootOwner(path)) {
			// The managed ROOT's owner is exempt too: `fleet grant` names the
			// tree owner in every default ACL so that files the service
			// account creates stay shared, and a doctor run AS the daemon
			// would otherwise report the owner's own entry on every tenant
			// the ctl created.
			continue
		}
		if seen[g.String()] {
			continue
		}
		seen[g.String()] = true
		offenders = append(offenders, g.String())
	}
	if len(offenders) == 0 {
		return
	}
	d.addRepair(model.LevelError, ACLGrantsOthers, tenant,
		fmt.Sprintf("%s: POSIX ACL grants write to %s", path, strings.Join(offenders, "; ")),
		fmt.Sprintf("ragstack-ctl fleet grant --user <name> --revoke --roots %s (run as the owner)", filepath.Dir(path)))
}

// BootRecordFile is where `fleet enable-boot --cron` records what it
// installed, under the ctl's own state directory. It is the doctor's ONLY
// evidence about the crontab.
const BootRecordFile = "boot.json"

// BootRecord is that file: whether a marked `@reboot` line is installed, what
// the line says, and when the ctl put it there.
//
// It is a RECORD, not an observation. The doctor does not run `crontab -l` —
// a diagnostic that shells out to read an account's boot configuration fails
// differently on every host, needs the right account to be asked from, and
// would make the doctor a thing that executes programs on behalf of a read.
// The ctl knows what it installed, so it writes it down; `fleet enable-boot
// --no-cron` clears it. The cost is that a line an operator added by hand is
// invisible here, and the finding says so.
type BootRecord struct {
	Cron bool   `json:"cron"`
	Line string `json:"line"`
	At   string `json:"at"`
}

// bootHook reports whether ANYTHING will bring the tenants back after a
// reboot.
//
// There are two hooks on this host and the account has at most one of them: a
// user manager that survives logout (linger, which systemd tenants need) or a
// `@reboot` crontab line running `fleet start --all` (which is what instance
// mode has, because cron gets no logind session here and so cannot drive
// `systemctl --user` at all). With neither, a reboot is a fleet that stays
// down until somebody notices.
func (d *run) bootHook() {
	path := filepath.Join(d.roots.CtlStateDir, BootRecordFile)
	var rec BootRecord
	b, err := os.ReadFile(path)
	if err == nil {
		if jerr := json.Unmarshal(b, &rec); jerr != nil {
			d.add(model.LevelWarn, BootCronMissing, "", fmt.Sprintf(
				"%s cannot be read as a boot record (%v), so the ctl cannot say whether a @reboot line is installed; "+
					"re-run `ragstack-ctl fleet enable-boot --cron`", path, jerr))
			return
		}
	} else if !errors.Is(err, fs.ErrNotExist) {
		return // an unreadable state dir is not evidence about the crontab
	}
	if rec.Cron {
		detail := fmt.Sprintf("a @reboot crontab line is recorded in %s", path)
		if rec.At != "" {
			detail += " (installed " + rec.At + ")"
		}
		if rec.Line != "" {
			detail += ": " + rec.Line
		}
		d.add(model.LevelInfo, BootCronPresent, "", detail)
		return
	}
	if d.host.Linger(d.opts.CtlUser) {
		return // the user manager is the boot hook; linger_missing covers the rest
	}
	d.add(model.LevelWarn, BootCronMissing, "", fmt.Sprintf(
		"%s has no linger AND the ctl has recorded no @reboot crontab line (%s): nothing on this host starts a "+
			"tenant after a reboot. `ragstack-ctl fleet enable-boot --cron` installs one. A line added by hand is "+
			"not visible here — the ctl reports only what it installed itself", d.opts.CtlUser, path))
}

// aclManagedRoots answers the question the interim runtime turns on: can the
// service account the daemon runs as actually write the three managed roots?
//
// It can, in exactly two ways — it owns the root, or a named ACL entry gives
// it rwx. Neither is assumed. The host has no root this week, so ownership is
// not something the ctl can arrange, and `fleet grant` (run by the OWNER,
// wilke) is the arrangement that exists. This check is what tells an operator
// which of the three roots the grant has reached.
//
// It asks about the CONFIGURED service account, not about whoever is running
// doctor: the useful answer for wilke — who is the one who can fix it — is
// "can svcbvbrc get in", and that answer must not change with the shell it
// was asked from.
func (d *run) aclManagedRoots() {
	uid := d.ctlUID()
	if uid < 0 {
		return // an account this host does not know: nothing to say about it
	}
	ctl := d.opts.CtlUser
	var granted, missing, noDefault []string
	for _, root := range []string{d.roots.DataDir, d.roots.ReposDir, d.roots.BackupsDir} {
		if _, err := os.Lstat(root); err != nil {
			continue // a root that does not exist yet is `tenant create`'s problem
		}
		w, werr := d.host.WritableByOthers(root)
		if werr == nil && w.Owner == ctl {
			continue // ownership already carries everything an ACL could add
		}
		access, dflt, err := d.host.ACL(root)
		if err != nil {
			continue // an unreadable ACL is not evidence of a missing one
		}
		switch {
		case access.HasRWX(uint32(uid)):
			granted = append(granted, root)
			// A grant with no default ACL stops at the files that exist
			// today: the next tenant's data dir inherits nothing.
			if !dflt.HasRWX(uint32(uid)) {
				noDefault = append(noDefault, root)
			}
		default:
			missing = append(missing, root)
		}
	}
	if len(granted) > 0 {
		detail := fmt.Sprintf("%s holds rwx through a POSIX ACL on %s", ctl, strings.Join(granted, ", "))
		if len(noDefault) > 0 {
			detail += fmt.Sprintf(" — but %s carry no DEFAULT ACL, so anything created under them later inherits nothing", strings.Join(noDefault, ", "))
		}
		d.add(model.LevelInfo, ACLGrantPresent, "", detail)
	}
	if len(missing) > 0 {
		d.addRepair(model.LevelWarn, CtlAccountNoAccess, "",
			fmt.Sprintf("%s neither owns nor holds an ACL grant on %s: every op that writes there fails with EACCES",
				ctl, strings.Join(missing, ", ")),
			fmt.Sprintf("as the owner: ragstack-ctl fleet grant --user %s --roots %s", ctl, strings.Join(missing, ",")))
	}
}

// ctlUID is the numeric id of the configured service account: the option when
// a caller stated it (every test does), else NSS. -1 when unresolvable.
func (d *run) ctlUID() int {
	if d.opts.CtlUID > 0 {
		return d.opts.CtlUID
	}
	return hostfacts.LookupUID(d.opts.CtlUser)
}

// ---------------------------------------------------------------- helpers

func (d *run) add(level model.Level, code, tenant, detail string) {
	d.addRepair(level, code, tenant, detail, "")
}

func (d *run) addRepair(level model.Level, code, tenant, detail, repair string) {
	d.findings = append(d.findings, model.Finding{
		Level: level, Code: code, Tenant: registry.NullString(tenant), Detail: detail, Repair: repair,
	})
}

// realImportCheck runs `python -c 'import ragstack; print(ragstack.__file__)'`
// with the unit's PYTHONPATH — argv only, no shell, 5 s, a sanitized
// environment. A missing interpreter is not a finding: it is skipped.
func realImportCheck(pythonEnv, worktree string) (string, error) {
	py := filepath.Join(pythonEnv, "bin", "python")
	if _, err := os.Stat(py); err != nil {
		return "", nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, py, "-c", "import ragstack, sys; sys.stdout.write(ragstack.__file__)")
	cmd.Env = []string{
		"PYTHONPATH=" + filepath.Join(worktree, "python"),
		"PATH=/usr/bin:/bin",
		"HOME=" + os.TempDir(),
		"PYTHONDONTWRITEBYTECODE=1",
	}
	out, err := cmd.Output()
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(out)), nil
}

func sortFindings(f []model.Finding) {
	sort.SliceStable(f, func(i, j int) bool {
		if a, b := string(f[i].Tenant), string(f[j].Tenant); a != b {
			return a < b
		}
		if f[i].Code != f[j].Code {
			return f[i].Code < f[j].Code
		}
		return f[i].Detail < f[j].Detail
	})
}

func mentions(list []string, want string) bool {
	for _, v := range list {
		if v == want || strings.HasPrefix(v, want+"/") {
			return true
		}
	}
	return false
}

// heapBytes converts a JVM heap size (`<n>k|<n>m|<n>g`, either case) into
// bytes. The unit is case-INSENSITIVE because `-Xmx4G` is what a hand-written
// ES_JAVA_OPTS actually says: the lowercase-only version silently read that
// as 0 and quietly removed the biggest heap on the host from the sum the
// es_heap_sum_high check is made of. An unrecognised value is an error, never
// a zero.
func heapBytes(h string) (int64, error) {
	if h == "" {
		return 0, nil
	}
	n, err := strconv.ParseInt(h[:len(h)-1], 10, 64)
	if err != nil || n < 0 {
		return 0, fmt.Errorf("heap %q: not <n><k|m|g>", h)
	}
	switch h[len(h)-1] {
	case 'g', 'G':
		return n << 30, nil
	case 'm', 'M':
		return n << 20, nil
	case 'k', 'K':
		return n << 10, nil
	}
	return 0, fmt.Errorf("heap %q: unit %q is not k, m or g", h, h[len(h)-1:])
}

func under(path, root string) bool {
	path, root = filepath.Clean(path), filepath.Clean(root)
	return path == root || strings.HasPrefix(path, root+string(filepath.Separator))
}

func firstArg(argv []string) string {
	if len(argv) == 0 {
		return "unknown"
	}
	return argv[0]
}

// managedRootOwner is the owner of the managed root (data, repos or backups
// tree) that contains path, or "" when path is under none or the root cannot
// be stat'ed here (a fixture run): the tree's owner is the account whose entry
// every default ACL under it carries.
func (d *run) managedRootOwner(path string) string {
	for _, root := range []string{d.roots.DataDir, d.roots.ReposDir, d.roots.BackupsDir} {
		if root == "" || !(path == root || strings.HasPrefix(path, root+"/")) {
			continue
		}
		fi, err := os.Stat(root)
		if err != nil {
			return ""
		}
		if st, ok := fi.Sys().(*syscall.Stat_t); ok {
			return hostfacts.UsernameOf(int(st.Uid))
		}
	}
	return ""
}
