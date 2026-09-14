package drivers

import (
	"context"
	"fmt"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
	"github.com/ragstack/ragstack/internal/ctl/paths"
)

// FakeOptions seed the in-memory host. Every map is optional; the zero value
// is an empty host on which nothing is running and nothing exists.
type FakeOptions struct {
	// Active is the set of units that start out active.
	Active []string
	// Enabled is the set of units that start out enabled.
	Enabled []string
	// Listening is the set of ports that start out bound.
	Listening []int
	// Collections maps a qdrant base URL to the collections it holds.
	Collections map[string][]string
	// Indices maps an elasticsearch base URL to the indices it holds.
	Indices map[string][]string
	// Files seeds the in-memory filesystem (path -> content).
	Files map[string][]byte
	// Roots are the approved roots the fake Files driver writes under. Empty
	// means "no containment" — a test that wants the containment refusal
	// asserts it by naming the roots.
	Roots []string
	// UnitPorts links a unit to the port it makes listen, so that starting
	// and stopping units moves the Proc driver's LISTEN set the way a real
	// host does — a readiness probe or a fence verify against this host is
	// then answering a fact rather than a fixture.
	UnitPorts map[string]int
	// Generation is the gateway generation already published.
	Generation int
	// Now is the clock the snapshot names are stamped from.
	Now func() time.Time
}

// Fake is the whole in-memory host. It satisfies jobs.Drivers, and every
// sub-driver it hands out records into the same call log, so a test asserts
// the ORDER of calls across drivers — which is what a step sequence is.
type Fake struct {
	recorder
	systemd *FakeSystemd
	proc    *FakeProc
	gateway *FakeGateway
	files   *FakeFiles
	qdrant  *FakeQdrant
	es      *FakeElasticsearch
	api     *FakeTenantAPI
	now     func() time.Time
}

var _ jobs.Drivers = (*Fake)(nil)

// NewFake builds the in-memory host described by opts.
func NewFake(opts FakeOptions) *Fake {
	now := opts.Now
	if now == nil {
		now = func() time.Time { return time.Date(2026, 9, 14, 12, 0, 0, 0, time.UTC) }
	}
	f := &Fake{now: now}
	f.proc = &FakeProc{r: &f.recorder, Ports: map[int]bool{}}
	for _, p := range opts.Listening {
		f.proc.Ports[p] = true
	}
	f.systemd = &FakeSystemd{
		r: &f.recorder, Active: setOf(opts.Active), Enabled: setOf(opts.Enabled),
		proc: f.proc, ports: opts.UnitPorts,
	}
	f.gateway = &FakeGateway{r: &f.recorder, Generation: opts.Generation}
	f.files = &FakeFiles{r: &f.recorder, Files: map[string]FakeFile{}, Roots: append([]string(nil), opts.Roots...)}
	for p, b := range opts.Files {
		f.files.Files[p] = FakeFile{Data: append([]byte(nil), b...), Mode: 0o640}
	}
	f.qdrant = &FakeQdrant{r: &f.recorder, now: now, ByURL: copyMapSlice(opts.Collections), Snapshots: map[string][]string{}}
	f.es = &FakeElasticsearch{r: &f.recorder, ByURL: copyMapSlice(opts.Indices), Snapshots: map[string][]string{}}
	f.api = &FakeTenantAPI{r: &f.recorder}
	return f
}

// The jobs.Drivers surface.
func (f *Fake) Systemd() jobs.Systemd             { return f.systemd }
func (f *Fake) Proc() jobs.Proc                   { return f.proc }
func (f *Fake) Gateway() jobs.GatewayDriver       { return f.gateway }
func (f *Fake) Files() jobs.Files                 { return f.files }
func (f *Fake) Qdrant() jobs.Qdrant               { return f.qdrant }
func (f *Fake) Elasticsearch() jobs.Elasticsearch { return f.es }
func (f *Fake) TenantAPI() jobs.TenantAPI         { return f.api }

// Note records something that is not a driver call — a job engine
// checkpoint, say — in the SAME log the driver calls go into. It is how a
// test asserts that an external ID was recorded BEFORE the external call that
// created it, which is the ordering reconcile-on-restart depends on.
func (f *Fake) Note(kind string, args ...string) { _ = f.record("job", kind, args...) }

// The concrete fakes, for inspection.
func (f *Fake) FakeSystemd() *FakeSystemd             { return f.systemd }
func (f *Fake) FakeProc() *FakeProc                   { return f.proc }
func (f *Fake) FakeGateway() *FakeGateway             { return f.gateway }
func (f *Fake) FakeFiles() *FakeFiles                 { return f.files }
func (f *Fake) FakeQdrant() *FakeQdrant               { return f.qdrant }
func (f *Fake) FakeElasticsearch() *FakeElasticsearch { return f.es }
func (f *Fake) FakeTenantAPI() *FakeTenantAPI         { return f.api }

// ---------------------------------------------------------------- systemd

// FakeSystemd is `systemctl --user` as a pair of sets.
type FakeSystemd struct {
	r       *recorder
	mu      sync.Mutex
	proc    *FakeProc
	ports   map[string]int
	Active  map[string]bool
	Enabled map[string]bool
	Reloads int
}

func (s *FakeSystemd) DaemonReload(context.Context) error {
	if err := s.r.record("systemd", "DaemonReload"); err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.Reloads++
	return nil
}

func (s *FakeSystemd) Start(_ context.Context, unit string) error {
	return s.set("Start", unit, s.Active, true)
}
func (s *FakeSystemd) Stop(_ context.Context, unit string) error {
	return s.set("Stop", unit, s.Active, false)
}
func (s *FakeSystemd) Enable(_ context.Context, unit string) error {
	return s.set("Enable", unit, s.Enabled, true)
}
func (s *FakeSystemd) Disable(_ context.Context, unit string) error {
	return s.set("Disable", unit, s.Enabled, false)
}

func (s *FakeSystemd) set(method, unit string, m map[string]bool, v bool) error {
	if err := s.r.record("systemd", method, unit); err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	m[unit] = v
	// Starting and stopping a SERVICE moves the LISTEN set; enabling and
	// disabling one does not.
	if port, ok := s.ports[unit]; ok && (method == "Start" || method == "Stop") {
		s.proc.mu.Lock()
		if v {
			s.proc.Ports[port] = true
		} else {
			delete(s.proc.Ports, port)
		}
		s.proc.mu.Unlock()
	}
	return nil
}

func (s *FakeSystemd) IsActive(_ context.Context, unit string) (bool, error) {
	if err := s.r.record("systemd", "IsActive", unit); err != nil {
		return false, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.Active[unit], nil
}

// ActiveUnits lists the active units, sorted.
func (s *FakeSystemd) ActiveUnits() []string { return trueKeys(&s.mu, s.Active) }

// EnabledUnits lists the enabled units, sorted.
func (s *FakeSystemd) EnabledUnits() []string { return trueKeys(&s.mu, s.Enabled) }

// ---------------------------------------------------------------- proc

// FakeProc is the pidfile/proc surface of a manual tenant.
type FakeProc struct {
	r  *recorder
	mu sync.Mutex
	// Ports is the LISTEN set.
	Ports map[int]bool
	// Signals records every delivered signal as "<pid>:<sig>".
	Signals []string
	// StopsListening, when true, clears the port of a signalled process —
	// which is what a tenant that actually died looks like from outside.
	StopsListening map[int]int // pid -> port cleared on signal
}

func (p *FakeProc) Listening(_ context.Context, port int) (bool, error) {
	if err := p.r.record("proc", "Listening", strconv.Itoa(port)); err != nil {
		return false, err
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.Ports[port], nil
}

func (p *FakeProc) Signal(_ context.Context, pid int, wantCwd, wantCmd, sig string) error {
	if err := p.r.record("proc", "Signal", strconv.Itoa(pid), wantCwd, wantCmd, sig); err != nil {
		return err
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	p.Signals = append(p.Signals, fmt.Sprintf("%d:%s", pid, sig))
	if port, ok := p.StopsListening[pid]; ok {
		delete(p.Ports, port)
	}
	return nil
}

// ---------------------------------------------------------------- gateway

// FakeGateway records publishes, reloads and rollbacks.
type FakeGateway struct {
	r  *recorder
	mu sync.Mutex
	// Generation is the currently published generation; Apply bumps it.
	Generation int
	Applies    []bool // dry-run flag of each Apply, in order
	Reloads    []bool // dry-run flag of each Reload
	Rollbacks  []int  // targets of each Rollback
}

func (g *FakeGateway) Apply(_ context.Context, dryRun bool) (int, string, error) {
	if err := g.r.record("gateway", "Apply", strconv.FormatBool(dryRun)); err != nil {
		return 0, "", err
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	g.Applies = append(g.Applies, dryRun)
	if dryRun {
		return g.Generation, fmt.Sprintf("dry run: gen-%d would be published", g.Generation+1), nil
	}
	g.Generation++
	return g.Generation, fmt.Sprintf("published gen-%d", g.Generation), nil
}

func (g *FakeGateway) Reload(_ context.Context, dryRun bool) (string, error) {
	if err := g.r.record("gateway", "Reload", strconv.FormatBool(dryRun)); err != nil {
		return "", err
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	g.Reloads = append(g.Reloads, dryRun)
	return fmt.Sprintf("reloaded gen-%d (dry_run=%v)", g.Generation, dryRun), nil
}

func (g *FakeGateway) Rollback(_ context.Context, to int) (string, error) {
	if err := g.r.record("gateway", "Rollback", strconv.Itoa(to)); err != nil {
		return "", err
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	g.Rollbacks = append(g.Rollbacks, to)
	g.Generation = to
	return fmt.Sprintf("rolled back to gen-%d", to), nil
}

// ---------------------------------------------------------------- files

// FakeFile is one file of the in-memory filesystem.
type FakeFile struct {
	Data []byte
	Mode uint32
}

// FakeFiles is an in-memory filesystem that honours the approved roots.
type FakeFiles struct {
	r     *recorder
	mu    sync.Mutex
	Files map[string]FakeFile
	Roots []string
}

// ErrOutsideRoots is the containment refusal of both Files drivers.
func outsideRoots(path string, roots []string) error {
	return fmt.Errorf("%w: %s is outside every approved root %v", jobs.ErrRefused, path, roots)
}

// contained reports whether path is under one of roots (all of them when
// roots is empty).
func contained(path string, roots []string) bool {
	if len(roots) == 0 {
		return true
	}
	for _, root := range roots {
		if _, err := paths.SafePath(root, path); err == nil {
			return true
		}
	}
	return false
}

func (f *FakeFiles) WriteAtomic(_ context.Context, path string, data []byte, mode uint32) error {
	if err := f.r.record("files", "WriteAtomic", path, fmt.Sprintf("%04o", mode)); err != nil {
		return err
	}
	if !contained(path, f.Roots) {
		return outsideRoots(path, f.Roots)
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Files[path] = FakeFile{Data: append([]byte(nil), data...), Mode: mode}
	return nil
}

func (f *FakeFiles) Rename(_ context.Context, from, to string) error {
	if err := f.r.record("files", "Rename", from, to); err != nil {
		return err
	}
	for _, p := range []string{from, to} {
		if !contained(p, f.Roots) {
			return outsideRoots(p, f.Roots)
		}
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if v, ok := f.Files[from]; ok {
		f.Files[to] = v
		delete(f.Files, from)
		return nil
	}
	// A directory rename: move every path under from.
	prefix := strings.TrimSuffix(from, "/") + "/"
	moved := false
	for p, v := range f.Files {
		if strings.HasPrefix(p, prefix) {
			f.Files[filepath.Join(to, strings.TrimPrefix(p, prefix))] = v
			delete(f.Files, p)
			moved = true
		}
	}
	if !moved {
		return fmt.Errorf("rename %s: no such file or directory", from)
	}
	return nil
}

func (f *FakeFiles) Remove(_ context.Context, path string) error {
	if err := f.r.record("files", "Remove", path); err != nil {
		return err
	}
	if !contained(path, f.Roots) {
		return outsideRoots(path, f.Roots)
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	delete(f.Files, path)
	return nil
}

func (f *FakeFiles) ReadFile(_ context.Context, path string) ([]byte, error) {
	if err := f.r.record("files", "ReadFile", path); err != nil {
		return nil, err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	v, ok := f.Files[path]
	if !ok {
		return nil, fmt.Errorf("open %s: no such file or directory", path)
	}
	return append([]byte(nil), v.Data...), nil
}

// Paths lists the files present, sorted.
func (f *FakeFiles) Paths() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := make([]string, 0, len(f.Files))
	for p := range f.Files {
		out = append(out, p)
	}
	sort.Strings(out)
	return out
}

// Content is the bytes at path (nil when absent).
func (f *FakeFiles) Content(path string) []byte {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.Files[path].Data
}

// ---------------------------------------------------------------- stores

// FakeQdrant is a qdrant with a collection list and a snapshot ledger.
type FakeQdrant struct {
	r   *recorder
	now func() time.Time
	mu  sync.Mutex
	// ByURL maps a base URL to the collections it holds.
	ByURL map[string][]string
	// Snapshots maps a collection to the snapshot names taken of it, in order.
	Snapshots map[string][]string
	n         int
}

// Collections lists the collections of base, sorted.
func (q *FakeQdrant) Collections(_ context.Context, base string) ([]string, error) {
	if err := q.r.record("qdrant", "Collections", base); err != nil {
		return nil, err
	}
	q.mu.Lock()
	defer q.mu.Unlock()
	out := append([]string(nil), q.ByURL[base]...)
	sort.Strings(out)
	return out, nil
}

func (q *FakeQdrant) Snapshot(_ context.Context, base, collection string) (string, error) {
	if err := q.r.record("qdrant", "Snapshot", collection, base); err != nil {
		return "", err
	}
	q.mu.Lock()
	defer q.mu.Unlock()
	q.n++
	name := fmt.Sprintf("%s-%s-%d.snapshot", collection, q.now().UTC().Format("20060102T150405Z"), q.n)
	q.Snapshots[collection] = append(q.Snapshots[collection], name)
	return name, nil
}

// FakeElasticsearch is an ES with an index list and a snapshot ledger.
type FakeElasticsearch struct {
	r  *recorder
	mu sync.Mutex
	// ByURL maps a base URL to the indices it holds.
	ByURL map[string][]string
	// Snapshots maps a repo to the snapshot names taken into it, in order.
	Snapshots map[string][]string
}

// Indices lists the indices of base, sorted.
func (e *FakeElasticsearch) Indices(_ context.Context, base string) ([]string, error) {
	if err := e.r.record("es", "Indices", base); err != nil {
		return nil, err
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	out := append([]string(nil), e.ByURL[base]...)
	sort.Strings(out)
	return out, nil
}

func (e *FakeElasticsearch) Snapshot(_ context.Context, base, repo, name string) error {
	if err := e.r.record("es", "Snapshot", repo, name, base); err != nil {
		return err
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	e.Snapshots[repo] = append(e.Snapshots[repo], name)
	return nil
}

// ---------------------------------------------------------------- tenant API

// FakeTenantAPI records the two allowlisted calls.
type FakeTenantAPI struct {
	r        *recorder
	mu       sync.Mutex
	Healths  []string
	Accounts []string // "<origin> <action> <subject>"
}

func (a *FakeTenantAPI) Health(_ context.Context, origin string) error {
	if err := a.r.record("tenantapi", "Health", origin); err != nil {
		return err
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	a.Healths = append(a.Healths, origin)
	return nil
}

func (a *FakeTenantAPI) ServiceAccount(_ context.Context, origin, subject, action string) error {
	if err := a.r.record("tenantapi", "ServiceAccount", subject, action, origin); err != nil {
		return err
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	a.Accounts = append(a.Accounts, origin+" "+action+" "+subject)
	return nil
}

// ---------------------------------------------------------------- helpers

func setOf(ks []string) map[string]bool {
	m := make(map[string]bool, len(ks))
	for _, k := range ks {
		m[k] = true
	}
	return m
}

func trueKeys(mu *sync.Mutex, m map[string]bool) []string {
	mu.Lock()
	defer mu.Unlock()
	var out []string
	for k, v := range m {
		if v {
			out = append(out, k)
		}
	}
	sort.Strings(out)
	return out
}

func copyMapSlice(m map[string][]string) map[string][]string {
	out := make(map[string][]string, len(m))
	for k, v := range m {
		out[k] = append([]string(nil), v...)
	}
	return out
}
