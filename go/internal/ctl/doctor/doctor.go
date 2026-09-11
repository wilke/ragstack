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
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
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
	d.listeners()
	d.hostChecks()
	d.manifestChecks()
	d.gatewayChecks()
	for _, t := range d.scopedTenants() {
		d.tenantChecks(ctx, t)
	}

	findings := applyPreconditions(d.findings, d.opts.Op)
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
	maps, ok := GatewayMaps(d.roots.ProxyDir)
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
	case listening && api.User == "":
		// The owner is UNREADABLE, not "fine". /proc/<pid>/fd is not
		// readable across accounts, so a ctl running as svcbvbrc sees
		// wilke's uvicorn as an ownerless socket — precisely the case the
		// identity gate exists for. Passing it silently let every mutation
		// through on the one host layout the gate was written for.
		d.add(model.LevelError, PortOwnerMismatch, t.Name, fmt.Sprintf(
			":%d is held by a process this account cannot attribute (owner unreadable: /proc/<pid>/fd is not readable across accounts); the registry records owner %s",
			t.Ports.API, t.Owner))
	case listening && api.User != t.Owner:
		d.add(model.LevelError, PortOwnerMismatch, t.Name, fmt.Sprintf(":%d is held by pid %d running as %s, the registry records owner %s", t.Ports.API, api.Pid, api.User, t.Owner))
	}
	d.unexpectedListeners(t)
	d.envCheck(t)
	d.codeChecks(t)
	d.storeChecks(t)
	d.permissionChecks(t)
	d.add(model.LevelInfo, CapabilitiesUnconfirmed, t.Name, "store capabilities are all false: stop, purge, snapshot and restore refuse until an operator confirms process identity, backing path and exclusive ownership")
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

// permissionChecks walk the paths the ctl has to trust for this tenant.
func (d *run) permissionChecks(t *registry.Tenant) {
	d.writable(t.Name, t.DataDir)
	d.writable(t.Name, filepath.Join(t.DataDir, "config", "secrets.env"))
	for _, sif := range []string{string(t.Stores.Qdrant.SIF), string(t.Stores.Elasticsearch.SIF)} {
		if sif != "" {
			d.writable(t.Name, sif)
		}
	}
}

func (d *run) writable(tenant, path string) {
	if path == "" {
		return
	}
	if _, err := os.Lstat(path); err != nil {
		return // absent paths are another check's problem
	}
	w, err := d.host.WritableByOthers(path)
	if err != nil || !w.Writable {
		return
	}
	d.add(model.LevelError, WritableByOthers, tenant, fmt.Sprintf("%s: %s is %s", path, w.Path, w.Reason))
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
