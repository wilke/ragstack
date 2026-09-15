package drivers

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io/fs"
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
	// Routed is the tenant list the fake gateway serves at the start.
	Routed []string
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

	// ---- PR-D seeds. Each names a host fact some step will read.

	// Linked maps a unit to the unit file it was linked from, for a host
	// where `systemctl --user link` has already run.
	Linked map[string]string
	// Failed is the set of units in the failed state. Show reports them as
	// failed until ResetFailed clears them.
	Failed []string
	// Owners maps a port to the process behind its LISTEN socket. A port that
	// is Listening with no Owners entry is one whose socket belongs to another
	// account — the real driver reports pid 0 for those, and so does the fake.
	Owners map[int]PortOwner
	// QdrantCounts maps "<baseURL>/<collection>" to its exact point count.
	QdrantCounts map[string]int64
	// ESRepos maps a registered snapshot repository to its settings.
	ESRepos map[string]Repo
	// ESCounts maps "<baseURL>/<index>" to its document count.
	ESCounts map[string]int64
	// Versions maps a tenant origin to what GET /v1/version answers.
	Versions map[string]map[string]any
	// CollectionsByOrigin maps a tenant origin to what GET /v1/collections
	// answers — the tenant API's own inventory, which is not the same thing
	// as the collections qdrant holds.
	CollectionsByOrigin map[string][]string
	// Refs maps a git ref to the sha it resolves to in the mirror.
	Refs map[string]string
	// Worktrees maps an existing worktree directory to the sha checked out.
	Worktrees map[string]string
	// Installed is the set of artifact worktrees whose node_modules are there.
	Installed []string
	// QdrantSnapshotDirs maps a qdrant base URL to the HOST directory its
	// snapshots land in (<data_dir>/qdrant/snapshots). With an entry, Snapshot
	// writes a placeholder file at <dir>/<collection>/<name> — which is what a
	// backup then renames into its bundle, so the step's move is moving
	// something. Without one, the fake only names the snapshot, as it did
	// before the backup had legs.
	QdrantSnapshotDirs map[string]string
	// PostgresReady maps a postgres run directory to whether pg_isready
	// succeeds. An ABSENT entry is READY: the ordinary fixture is a store
	// that works, and a fake defaulting to "not ready" would make every test
	// seed a map in order to say nothing.
	PostgresReady map[string]bool
	// DiskFree is the bytes Files.DiskFree reports. Zero means "a terabyte" —
	// the ordinary host with room — so only a test about the free-space
	// precheck has to say anything.
	DiskFree int64

	// ---- PR-D2 seeds.

	// InstancePorts links an apptainer instance to the port it makes listen,
	// exactly as UnitPorts does for a unit: running an instance binds the
	// port, stopping it frees it, so a readiness gate against this host
	// answers a fact rather than a fixture. A tenant created AFTER the driver
	// set was built says so with FakeInstances.BindInstancePort.
	InstancePorts map[string]int
	// RunningInstances are the instances already up when the fake is built —
	// an instance-supervised tenant the fixture says is ACTIVE. Without them
	// such a tenant looks stopped to `running`, so a fence would find nothing
	// to stop and a start would run a second copy of a store that is already
	// there.
	RunningInstances []string
	// AlivePIDs are the pids Proc.Alive answers true for without this fake
	// having spawned them — the pid in an adopted or fixture tenant's
	// pidfile. Spawn adds to the same set.
	AlivePIDs []int
	// Crontab is the account's crontab at the start. Nil is an account that
	// has never had one — which List reports as an empty body and no error,
	// as the real crontab(1) wrapper does.
	Crontab []byte
}

// PortOwner is the process behind a LISTEN socket, as FakeProc reports it.
type PortOwner struct {
	PID int
	UID int
}

// Repo is a registered elasticsearch snapshot repository.
type Repo struct {
	Location string
	ReadOnly bool
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
	git     *FakeGit
	build   *FakeBuild
	pg      *FakePostgres
	sqlite  *FakeSQLite
	archive *FakeArchive
	// PR-D2: the two surfaces `supervisor: instance` runs on.
	instances *FakeInstances
	crontab   *FakeCrontab
	now       func() time.Time
}

var _ jobs.Drivers = (*Fake)(nil)

// NewFake builds the in-memory host described by opts.
func NewFake(opts FakeOptions) *Fake {
	now := opts.Now
	if now == nil {
		now = func() time.Time { return time.Date(2026, 9, 14, 12, 0, 0, 0, time.UTC) }
	}
	f := &Fake{now: now}
	f.proc = &FakeProc{r: &f.recorder, Ports: map[int]bool{}, Owners: copyMapOwners(opts.Owners)}
	for _, p := range opts.Listening {
		f.proc.Ports[p] = true
	}
	f.systemd = &FakeSystemd{
		r: &f.recorder, Active: setOf(opts.Active), Enabled: setOf(opts.Enabled),
		Linked: copyMapString(opts.Linked), Failed: setOf(opts.Failed),
		proc: f.proc, ports: opts.UnitPorts, pids: map[string]int{}, nextPID: 20001,
	}
	f.gateway = &FakeGateway{r: &f.recorder, Generation: opts.Generation, Routed: append([]string(nil), opts.Routed...)}
	f.files = &FakeFiles{
		r: &f.recorder, Files: map[string]FakeFile{}, Dirs: map[string]uint32{},
		Roots: append([]string(nil), opts.Roots...), Free: opts.DiskFree,
	}
	for p, b := range opts.Files {
		f.files.Files[p] = FakeFile{Data: append([]byte(nil), b...), Mode: 0o640}
	}
	// Spawn writes a pidfile, so this driver needs the filesystem too — the
	// real one writes it directly rather than through the Files driver (it is
	// the ctl's own record of what it started, not a tenant artefact), and the
	// fake follows.
	f.proc.files = f.files
	// The manager and the filesystem are the SAME fixture: a daemon-reload has
	// to be able to see that a unit file is gone.
	f.systemd.files = f.files
	f.qdrant = &FakeQdrant{
		r: &f.recorder, now: now, ByURL: copyMapSlice(opts.Collections), files: f.files,
		Snapshots: map[string][]string{}, Counts: copyMapInt64(opts.QdrantCounts),
		SnapshotDirs: copyMapString(opts.QdrantSnapshotDirs),
	}
	f.es = &FakeElasticsearch{
		r: &f.recorder, ByURL: copyMapSlice(opts.Indices), Taken: map[string][]string{},
		Repos: copyMapRepo(opts.ESRepos), Counts: copyMapInt64(opts.ESCounts),
	}
	f.api = &FakeTenantAPI{
		r: &f.recorder, Versions: copyMapAny(opts.Versions),
		CollectionsByOrigin: copyMapSlice(opts.CollectionsByOrigin),
	}
	f.git = &FakeGit{r: &f.recorder, Refs: copyMapString(opts.Refs), Worktrees: copyMapString(opts.Worktrees)}
	f.build = &FakeBuild{r: &f.recorder, files: f.files, Installed: setOf(opts.Installed)}
	f.pg = &FakePostgres{r: &f.recorder, files: f.files, Readiness: copyMapBool(opts.PostgresReady)}
	f.sqlite = &FakeSQLite{r: &f.recorder, files: f.files}
	f.archive = &FakeArchive{r: &f.recorder, files: f.files}
	// The instances and the LISTEN set are the SAME fixture, for the reason
	// the units and the LISTEN set are: a store this host started has to be a
	// store a readiness probe can find.
	f.instances = &FakeInstances{
		r: &f.recorder, proc: f.proc, files: f.files, ports: copyMapInt(opts.InstancePorts),
		Running: map[string]jobs.Instance{}, nextPID: 21001,
	}
	for _, name := range opts.RunningInstances {
		f.instances.Running[name] = jobs.Instance{Name: name, PID: f.instances.nextPID, Image: "fixture.sif"}
		f.instances.nextPID++
		if port, ok := f.instances.ports[name]; ok {
			f.proc.Ports[port] = true
		}
	}
	for _, pid := range opts.AlivePIDs {
		if f.proc.alive == nil {
			f.proc.alive = map[int]bool{}
		}
		f.proc.alive[pid] = true
	}
	f.crontab = &FakeCrontab{r: &f.recorder, Body: append([]byte(nil), opts.Crontab...)}
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
func (f *Fake) Git() jobs.Git                     { return f.git }
func (f *Fake) Build() jobs.Build                 { return f.build }
func (f *Fake) Postgres() jobs.Postgres           { return f.pg }
func (f *Fake) SQLite() jobs.SQLite               { return f.sqlite }
func (f *Fake) Archive() jobs.Archive             { return f.archive }
func (f *Fake) Instances() jobs.Instances         { return f.instances }
func (f *Fake) Crontab() jobs.Crontab             { return f.crontab }

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
func (f *Fake) FakeGit() *FakeGit                     { return f.git }
func (f *Fake) FakeBuild() *FakeBuild                 { return f.build }
func (f *Fake) FakePostgres() *FakePostgres           { return f.pg }
func (f *Fake) FakeSQLite() *FakeSQLite               { return f.sqlite }
func (f *Fake) FakeArchive() *FakeArchive             { return f.archive }
func (f *Fake) FakeInstances() *FakeInstances         { return f.instances }
func (f *Fake) FakeCrontab() *FakeCrontab             { return f.crontab }

// ---------------------------------------------------------------- systemd

// FakeSystemd is `systemctl --user` as a pair of sets.
type FakeSystemd struct {
	r    *recorder
	mu   sync.Mutex
	proc *FakeProc
	// files is the in-memory filesystem the unit fragments live in, so a
	// daemon-reload can forget a unit whose file has been removed.
	files   *FakeFiles
	ports   map[string]int
	pids    map[string]int
	nextPID int
	Active  map[string]bool
	Enabled map[string]bool
	// Linked maps a unit to the unit file Link was called with. It is what
	// makes a unit KNOWN to this fake manager even before it is started, which
	// is the state `tenant create` leaves behind between rendering the units
	// and starting the target.
	Linked map[string]string
	// Failed is the set of units in the failed state; ResetFailed clears one.
	Failed  map[string]bool
	Reloads int
}

// DaemonReload re-reads the unit files, and — like the real manager — FORGETS
// every unit whose fragment is no longer on disk.
//
// Without that the fake remembered a unit for the life of the process: a
// `decommission` that removed the unit files and reloaded still had `Show`
// answering with the FragmentPath of a file that was gone, so the post-check
// "systemd knows nothing about this tenant any more" could never pass against
// the fixture even though it passes on a host. The manager's rule is simple and
// worth modelling exactly: a reload drops a unit with no fragment.
func (s *FakeSystemd) DaemonReload(context.Context) error {
	if err := s.r.record("systemd", "DaemonReload"); err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.Reloads++
	if s.files == nil {
		return nil
	}
	for unit, path := range s.Linked {
		if s.files.has(path) {
			continue
		}
		delete(s.Linked, unit)
		delete(s.Active, unit)
		delete(s.Enabled, unit)
		delete(s.Failed, unit)
		delete(s.pids, unit)
	}
	// A unit that is neither linked nor running has no file this manager
	// could find (the fake's only notion of a unit file is a linked path), so
	// a reload forgets it — which is what the real manager does with a unit
	// whose file was removed after it was stopped and disabled. A unit the
	// fixture seeded as active keeps running and stays known.
	forget := func(m map[string]bool) {
		for unit := range m {
			if _, linked := s.Linked[unit]; linked || s.Active[unit] {
				continue
			}
			delete(s.Active, unit)
			delete(s.Enabled, unit)
			delete(s.Failed, unit)
			delete(s.pids, unit)
		}
	}
	forget(s.Active)
	forget(s.Enabled)
	forget(s.Failed)
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
	if err := checkUnit(unit); err != nil {
		if rerr := s.r.record("systemd", "Disable", unit); rerr != nil {
			return rerr
		}
		return err
	}
	// Two things the real `systemctl disable` does that the plain setter did
	// not: it REFUSES a unit the manager has never loaded ("Unit … does not
	// exist"), recording nothing — so a rollback that disables every name a
	// tenant could have does not conjure units the tenant never had — and it
	// removes the symlink `link` made, which is how a linked-but-never-enabled
	// unit is forgotten.
	s.mu.Lock()
	_, linked := s.Linked[unit]
	_, knownActive := s.Active[unit]
	_, knownEnabled := s.Enabled[unit]
	failed := s.Failed[unit]
	s.mu.Unlock()
	if !linked && !knownActive && !knownEnabled && !failed {
		if err := s.r.record("systemd", "Disable", unit); err != nil {
			return err
		}
		return fmt.Errorf("%w: unit %s does not exist", jobs.ErrRefused, unit)
	}
	if err := s.set("Disable", unit, s.Enabled, false); err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if linked && !s.Active[unit] {
		// A linked unit that is not running is gone the moment its link is:
		// the real manager reports it not-found at the next reload, and the
		// fake collapses the two so a Show after the disable already says so.
		delete(s.Linked, unit)
		delete(s.Active, unit)
		delete(s.Enabled, unit)
		delete(s.Failed, unit)
		delete(s.pids, unit)
	}
	return nil
}

// check records the call and applies the REAL driver's unit-name allowlist.
//
// The fake used to take any name at all, so a step that named `ssh-agent` (or
// a registry row that did) passed every test and was refused only on the host
// — which is the one place the refusal cannot be read as a test failure.
func (s *FakeSystemd) check(method, unit string) error {
	if err := s.r.record("systemd", method, unit); err != nil {
		return err
	}
	return checkUnit(unit)
}

func (s *FakeSystemd) set(method, unit string, m map[string]bool, v bool) error {
	if err := s.check(method, unit); err != nil {
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
	// A started unit has a main process; a stopped one does not. Show reads
	// this, so a caller that starts a unit and then asks what systemd thinks
	// of it gets an answer that agrees with itself.
	switch method {
	case "Start":
		s.assignPID(unit)
	case "Stop":
		delete(s.pids, unit)
	}
	return nil
}

// assignPID hands the unit a stable, obviously fake pid. Callers must hold mu.
func (s *FakeSystemd) assignPID(unit string) int {
	if pid, ok := s.pids[unit]; ok {
		return pid
	}
	if s.nextPID == 0 {
		s.nextPID = 20001
	}
	pid := s.nextPID
	s.nextPID++
	s.pids[unit] = pid
	return pid
}

func (s *FakeSystemd) IsActive(_ context.Context, unit string) (bool, error) {
	if err := s.check("IsActive", unit); err != nil {
		return false, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.Active[unit], nil
}

// Link records the unit file the unit was linked from. It is idempotent: the
// real `systemctl --user link` of a unit that already resolves to that path
// succeeds, and a create that is retried after a crash must not fail here.
func (s *FakeSystemd) Link(_ context.Context, unitPath string) error {
	if err := s.r.record("systemd", "Link", unitPath); err != nil {
		return err
	}
	// The real driver's two rules: an absolute, clean path, whose base name is
	// a unit this control plane may touch.
	if !filepath.IsAbs(unitPath) || filepath.Clean(unitPath) != unitPath {
		return fmt.Errorf("%w: %q must be an absolute, clean path to link", jobs.ErrRefused, unitPath)
	}
	if err := checkUnit(filepath.Base(unitPath)); err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.Linked[filepath.Base(unitPath)] = unitPath
	return nil
}

func (s *FakeSystemd) IsEnabled(_ context.Context, unit string) (bool, error) {
	if err := s.check("IsEnabled", unit); err != nil {
		return false, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.Enabled[unit], nil
}

// Show derives the unit's state from what this fake manager knows, rather
// than from a table a test has to keep in step with Start/Stop/Enable/Link.
//
// A unit the manager has never heard of — never linked, never started, never
// enabled — comes back as the ZERO UnitInfo, with an empty FragmentPath. That
// is how `systemctl show` answers for an unknown unit (LoadState=not-found,
// exit 0), and decommission's post-check reads exactly that emptiness to say
// "the units are gone".
func (s *FakeSystemd) Show(_ context.Context, unit string) (jobs.UnitInfo, error) {
	if err := s.check("Show", unit); err != nil {
		return jobs.UnitInfo{}, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	path, linked := s.Linked[unit]
	_, knownActive := s.Active[unit]
	_, knownEnabled := s.Enabled[unit]
	if !linked && !knownActive && !knownEnabled && !s.Failed[unit] {
		return jobs.UnitInfo{}, nil
	}
	if !linked {
		path = "/fake/systemd/user/" + unit
	}
	info := jobs.UnitInfo{FragmentPath: path, ActiveState: "inactive", SubState: "dead", Result: "success"}
	switch {
	case s.Failed[unit]:
		info.ActiveState, info.SubState, info.Result, info.ExecMainStatus = "failed", "failed", "exit-code", 1
	case s.Active[unit]:
		info.ActiveState, info.SubState = "active", "running"
		info.MainPID = s.assignPID(unit)
	}
	switch {
	case s.Enabled[unit]:
		info.UnitFileState = "enabled"
	case linked:
		info.UnitFileState = "linked"
	default:
		info.UnitFileState = "disabled"
	}
	return info, nil
}

// ResetFailed clears the failed state, as `systemctl --user reset-failed`
// does. It is not an error for a unit that never failed.
func (s *FakeSystemd) ResetFailed(_ context.Context, unit string) error {
	if err := s.check("ResetFailed", unit); err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.Failed, unit)
	return nil
}

// BindUnitPort tells this fake manager that starting unit binds port and
// stopping it frees the port, for a unit the FIXTURE did not know about.
//
// FakeOptions.UnitPorts is built once, from the tenants in the registry at the
// moment the driver set was made. A tenant CREATED afterwards — every sandbox
// the selftest makes — has units nothing has heard of, so starting its target
// moved no socket and a readiness gate waiting for its API port waited out the
// whole timeout against a host that was never going to answer. This is how a
// caller says "this unit exists now, and this is the port it owns".
func (s *FakeSystemd) BindUnitPort(unit string, port int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.ports == nil {
		s.ports = map[string]int{}
	}
	s.ports[unit] = port
}

// ActiveUnits lists the active units, sorted.
func (s *FakeSystemd) ActiveUnits() []string { return trueKeys(&s.mu, s.Active) }

// EnabledUnits lists the enabled units, sorted.
func (s *FakeSystemd) EnabledUnits() []string { return trueKeys(&s.mu, s.Enabled) }

// ---------------------------------------------------------------- proc

// FakeProc is the pidfile/proc surface of a manual tenant, and — from PR-D2 —
// the detached-spawn surface of an instance-supervised one.
type FakeProc struct {
	r  *recorder
	mu sync.Mutex
	// files is the in-memory filesystem Spawn writes its pidfile into.
	files *FakeFiles
	// Ports is the LISTEN set.
	Ports map[int]bool
	// Signals records every delivered signal as "<pid>:<sig>".
	Signals []string
	// StopsListening, when true, clears the port of a signalled process —
	// which is what a tenant that actually died looks like from outside.
	StopsListening map[int]int // pid -> port cleared on signal
	// Owners maps a port to the process behind its LISTEN socket.
	Owners map[int]PortOwner

	// Spawned is every SpawnSpec this fake was given, in order. It is
	// inspectable STATE, not a call record: a spec carries the child's whole
	// environment, so it is deliberately not what `proc.Spawn` puts in the
	// call log (see Spawn).
	Spawned []jobs.SpawnSpec
	// alive is the pid set Spawn created and TERM/KILL empties.
	alive map[int]bool
	// spawnPorts maps a spawned pid to the port it bound, so that killing it
	// frees the port the way a process that really died does.
	spawnPorts map[int]int
	nextSpawn  int
}

// Owner is the process behind the LISTEN socket on port.
//
// A bound port with no Owners entry answers (0, 0, nil), which is the real
// driver's answer for a socket owned by another account: `ss -ltnp` shows the
// port and withholds the process. An unbound port answers the same way,
// because "nobody is listening" is a fact a caller reads from Listening, not
// an error to raise here.
func (p *FakeProc) Owner(_ context.Context, port int) (int, int, error) {
	if err := p.r.record("proc", "Owner", strconv.Itoa(port)); err != nil {
		return 0, 0, err
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	o := p.Owners[port]
	return o.PID, o.UID, nil
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
	// A process this fake SPAWNED dies of a TERM or a KILL, and its port goes
	// with it. Without that, an instance-mode stop signalled its api, asked
	// Alive, was told yes, waited out the whole TimeoutStopSec and then killed
	// a process that had never been running — against a fixture that was never
	// going to say otherwise. Any other signal (a HUP, say) leaves it running,
	// which is the point of sending one.
	if (sig == "TERM" || sig == "KILL") && p.alive[pid] {
		delete(p.alive, pid)
		if port, ok := p.spawnPorts[pid]; ok {
			delete(p.Ports, port)
			delete(p.spawnPorts, pid)
		}
	}
	return nil
}

// Spawn starts a detached process: it assigns a pid, writes the pidfile, and
// binds the port the argv names.
//
// What it records is the PROGRAM and its ARGV — never spec.Env, which is the
// child's whole environment and therefore carries the tenant's secrets. The
// spec is kept in Spawned for a test to inspect; the call log, which tests
// print and compare, holds only what a `ps` line would show.
func (p *FakeProc) Spawn(_ context.Context, spec jobs.SpawnSpec) (int, error) {
	if err := p.r.record("proc", "Spawn", spec.Program, strings.Join(spec.Args, " ")); err != nil {
		return 0, err
	}
	// The real driver's rules, applied here so a caller that breaks one finds
	// out in a test rather than on the host: an absolute program (the drivers
	// never search PATH), a log to append to, and a pidfile to write — the
	// pidfile IS the ctl's record of what it started, so spawning without one
	// is starting a process nothing can ever find again.
	if !filepath.IsAbs(spec.Program) {
		return 0, fmt.Errorf("%w: proc.Spawn needs an absolute program, got %q", jobs.ErrRefused, spec.Program)
	}
	if spec.LogPath == "" || spec.PidFile == "" {
		return 0, fmt.Errorf("%w: proc.Spawn needs a log path and a pidfile", jobs.ErrRefused)
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.nextSpawn == 0 {
		p.nextSpawn = 30001
	}
	pid := p.nextSpawn
	p.nextSpawn++
	if p.alive == nil {
		p.alive = map[int]bool{}
	}
	p.alive[pid] = true
	p.Spawned = append(p.Spawned, spec)
	// BEFORE returning, as the contract says — and observable in the call log,
	// so a test can assert the ordering the real driver has to keep.
	if p.files != nil {
		p.files.Put(spec.PidFile, []byte(strconv.Itoa(pid)+"\n"), 0o644)
	}
	// The port comes off the ARGV rather than out of a fixture map: a spawned
	// process is the tenant's uvicorn, `--port <n>` is on its command line,
	// and reading it there means a test never has to state twice what port the
	// tenant is on.
	if port, ok := portFromArgs(spec.Args); ok {
		p.Ports[port] = true
		if p.spawnPorts == nil {
			p.spawnPorts = map[int]int{}
		}
		p.spawnPorts[pid] = port
	}
	return pid, nil
}

// Alive is true for a pid this fake spawned and has not seen die.
//
// A pid it never spawned is (false, nil), not an error: "that process is not
// running" is the answer, and it is the same answer the real driver gives for
// a pid whose /proc entry is gone.
func (p *FakeProc) Alive(_ context.Context, pid int) (bool, error) {
	if err := p.r.record("proc", "Alive", strconv.Itoa(pid)); err != nil {
		return false, err
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.alive[pid], nil
}

// portFromArgs reads the `--port <n>` an api command line carries.
func portFromArgs(args []string) (int, bool) {
	for i, a := range args {
		if a != "--port" || i+1 >= len(args) {
			continue
		}
		if n, err := strconv.Atoi(args[i+1]); err == nil {
			return n, true
		}
	}
	return 0, false
}

// ---------------------------------------------------------------- instances

// FakeInstances is `apptainer instance` as a map of running instances.
//
// It models the two facts an instance supervisor depends on and a stub could
// not give it: a name is UNIQUE (apptainer refuses a second instance under a
// name that is taken, which is what makes a start idempotency check possible
// at all), and a running store OWNS A PORT (so a readiness gate answers a fact
// rather than a fixture).
type FakeInstances struct {
	r    *recorder
	mu   sync.Mutex
	proc *FakeProc
	// files is the in-memory filesystem SeedConfigDir copies into.
	files *FakeFiles
	// ports links an instance name to the port running it binds.
	ports   map[string]int
	nextPID int
	// Running is the instance table, by name.
	Running map[string]jobs.Instance
	// Seeded records every SeedConfigDir as "<sif>:<containerDir>→<hostDir>",
	// in order: what a test reads to see that the ES config bind was filled
	// from the image BEFORE the instance started.
	Seeded []string
}

var _ jobs.Instances = (*FakeInstances)(nil)

// List is the running instances, sorted by name — `apptainer instance list`
// sorts too, and a driver whose order came out of a Go map would make every
// test that prints it flaky.
func (i *FakeInstances) List(context.Context) ([]jobs.Instance, error) {
	if err := i.r.record("instances", "List"); err != nil {
		return nil, err
	}
	i.mu.Lock()
	defer i.mu.Unlock()
	names := make([]string, 0, len(i.Running))
	for n := range i.Running {
		names = append(names, n)
	}
	sort.Strings(names)
	out := make([]jobs.Instance, 0, len(names))
	for _, n := range names {
		out = append(out, i.Running[n])
	}
	return out, nil
}

// Run starts an instance.
//
// What it records is the NAME and the IMAGE. Not spec.ExtraEnv — that is the
// child's environment and carries the postgres password — and not spec.Env
// either, which is public but long enough to bury the two facts a reader of
// the call log wants.
func (i *FakeInstances) Run(_ context.Context, spec jobs.InstanceSpec) error {
	if err := i.r.record("instances", "Run", spec.Name, spec.SIF); err != nil {
		return err
	}
	if err := checkInstanceName(spec.Name); err != nil {
		return err
	}
	if spec.SIF == "" {
		return fmt.Errorf("%w: instance %s has no image to run", jobs.ErrRefused, spec.Name)
	}
	i.mu.Lock()
	defer i.mu.Unlock()
	// apptainer's own refusal, modelled exactly: a name that is taken is an
	// error, not a no-op. An instance-mode `start` that treated it as success
	// would report a tenant started from the artifact it just checked out
	// while the OLD process kept serving.
	if _, ok := i.Running[spec.Name]; ok {
		return fmt.Errorf("%w: instance %s is already running", jobs.ErrRefused, spec.Name)
	}
	if i.nextPID == 0 {
		i.nextPID = 21001
	}
	pid := i.nextPID
	i.nextPID++
	i.Running[spec.Name] = jobs.Instance{Name: spec.Name, PID: pid, Image: spec.SIF}
	if port, ok := i.ports[spec.Name]; ok {
		i.proc.mu.Lock()
		i.proc.Ports[port] = true
		i.proc.mu.Unlock()
	}
	return nil
}

// Stop stops an instance, and an instance that is not running is SUCCESS —
// the interface's rule, because every caller of Stop is a step that gets
// re-run.
func (i *FakeInstances) Stop(_ context.Context, name string) error {
	if err := i.r.record("instances", "Stop", name); err != nil {
		return err
	}
	// The allowlist applies to a stop too, and more than to a start: this is
	// the call that takes something down.
	if err := checkInstanceName(name); err != nil {
		return err
	}
	i.mu.Lock()
	defer i.mu.Unlock()
	if _, ok := i.Running[name]; !ok {
		return nil
	}
	delete(i.Running, name)
	if port, ok := i.ports[name]; ok {
		i.proc.mu.Lock()
		delete(i.proc.Ports, port)
		i.proc.mu.Unlock()
	}
	return nil
}

// SeedConfigDir copies the image's config directory into hostDir.
//
// The fake has no image to read, so it writes the ONE file whose absence is
// the failure this seam exists to prevent: an Elasticsearch whose config bind
// shadows the image's own and holds no jvm.options exits before it logs
// anything useful. A caller that seeds and then lists the directory sees a
// populated one, which is what makes the "seed only when empty" rule testable.
func (i *FakeInstances) SeedConfigDir(_ context.Context, sif, containerDir, hostDir string) error {
	if err := i.r.record("instances", "SeedConfigDir", sif, containerDir, hostDir); err != nil {
		return err
	}
	if sif == "" || containerDir == "" || hostDir == "" {
		return fmt.Errorf("%w: instances.SeedConfigDir needs an image, a source inside it and a host directory",
			jobs.ErrRefused)
	}
	i.mu.Lock()
	defer i.mu.Unlock()
	i.Seeded = append(i.Seeded, sif+":"+containerDir+"→"+hostDir)
	if i.files != nil {
		for _, name := range []string{"elasticsearch.yml", "jvm.options", "log4j2.properties"} {
			i.files.Put(strings.TrimSuffix(hostDir, "/")+"/"+name, []byte("# seeded from "+sif+"\n"), 0o644)
		}
	}
	return nil
}

// BindInstancePort tells this fake that running name binds port and stopping
// it frees the port, for an instance the FIXTURE did not know about.
//
// It is FakeSystemd.BindUnitPort's twin and exists for the same reason:
// FakeOptions.InstancePorts is built from the tenants in the registry at the
// moment the driver set was made, and every sandbox the selftest creates comes
// later.
func (i *FakeInstances) BindInstancePort(name string, port int) {
	i.mu.Lock()
	defer i.mu.Unlock()
	if i.ports == nil {
		i.ports = map[string]int{}
	}
	i.ports[name] = port
}

// Names lists the running instances, sorted — the assertion a test usually
// wants, without walking the table.
func (i *FakeInstances) Names() []string {
	i.mu.Lock()
	defer i.mu.Unlock()
	out := make([]string, 0, len(i.Running))
	for n := range i.Running {
		out = append(out, n)
	}
	sort.Strings(out)
	return out
}

// ---------------------------------------------------------------- crontab

// FakeCrontab is the account's crontab as one byte slice.
type FakeCrontab struct {
	r  *recorder
	mu sync.Mutex
	// Body is the current crontab. Empty is an account with no crontab.
	Body []byte
	// Sets is every body written, in order — what a test reads to see that
	// `enable-boot` rewrote exactly one line and left the rest alone.
	Sets [][]byte
}

var _ jobs.Crontab = (*FakeCrontab)(nil)

// List returns the crontab. No crontab is an EMPTY BODY and no error, which
// is the interface's rule: crontab(1) exits non-zero for an account that has
// never had one, and passing that through would make a fresh host
// indistinguishable from a broken cron.
func (c *FakeCrontab) List(context.Context) ([]byte, error) {
	if err := c.r.record("crontab", "List"); err != nil {
		return nil, err
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	return append([]byte(nil), c.Body...), nil
}

// Set replaces the whole crontab.
//
// The body is not recorded on the call: a crontab is lines an operator wrote,
// it can be long, and the assertion a test wants is on Body or Sets. The call
// log says THAT it was written.
func (c *FakeCrontab) Set(_ context.Context, body []byte) error {
	if err := c.r.record("crontab", "Set"); err != nil {
		return err
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	c.Body = append([]byte(nil), body...)
	c.Sets = append(c.Sets, append([]byte(nil), body...))
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
	// Routed is the tenant list the fake's live gateway serves. Apply does
	// not recompute it (the fake knows no fleet); a test or the fixture seeds
	// it, and a create with gateway:true against the fake is routed only when
	// the caller says so.
	Routed []string
}

// Routes is the fake's live tenant list, sorted.
func (g *FakeGateway) Routes(_ context.Context) ([]string, error) {
	if err := g.r.record("gateway", "Routes"); err != nil {
		return nil, err
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	out := append([]string(nil), g.Routed...)
	sort.Strings(out)
	return out, nil
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
	// Dirs maps every directory MkdirAll created to the mode it was asked
	// for. The fake keeps no tree — a file's parents are implied by its path —
	// so this is where a test reads back "the tenant dir was made 2770".
	Dirs  map[string]uint32
	Roots []string
	// Free is what DiskFree answers; zero means the default terabyte.
	Free int64
}

// ErrOutsideRoots is the containment refusal of both Files drivers.
func outsideRoots(path string, roots []string) error {
	if len(roots) == 0 {
		return fmt.Errorf("%w: the files driver has no approved roots, so there is nowhere it may write; %s is refused",
			jobs.ErrRefused, path)
	}
	return fmt.Errorf("%w: %s is outside every approved root %v", jobs.ErrRefused, path, roots)
}

// contained reports whether path is under one of roots.
//
// An EMPTY root list DENIES. It used to allow everything, which made "no roots
// configured" — a driver set built without them, a fixture, a future caller
// that forgets — the one configuration in which the only guard between a job
// and the filesystem does nothing at all. Fail-open on a containment check is
// the wrong way round: a driver with no approved roots has nowhere it may
// write, and saying so is a refusal an operator can read rather than a silent
// write outside the deployment.
func contained(path string, roots []string) bool {
	if len(roots) == 0 {
		return false
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

// CopyFile copies one in-memory file to another path, at the given mode.
//
// The real driver streams; this one copies the bytes, because the fake host has
// no size. What it DOES keep is the two properties a step depends on: an absent
// source is fs.ErrNotExist (so "the bundle does not hold that file" is
// distinguishable from "the copy failed"), and the destination is contained by
// the approved roots exactly as WriteAtomic's is.
func (f *FakeFiles) CopyFile(_ context.Context, src, dst string, mode uint32) error {
	if err := f.r.record("files", "CopyFile", src, dst, fmt.Sprintf("%04o", mode)); err != nil {
		return err
	}
	if !contained(dst, f.Roots) {
		return outsideRoots(dst, f.Roots)
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	v, ok := f.Files[src]
	if !ok {
		return fmt.Errorf("open %s: %w", src, fs.ErrNotExist)
	}
	f.Files[dst] = FakeFile{Data: append([]byte(nil), v.Data...), Mode: mode}
	return nil
}

// MkdirAll records the directory and its mode. It honours the approved roots
// for the same reason WriteAtomic does: creating a directory outside the
// deployment is a mutation of somebody else's filesystem, and a fake that
// allowed it would let a step that does so pass its tests.
//
// Re-creating a directory is not an error — MkdirAll is idempotent on a real
// host — but the RECORDED mode stays the first one, so a test can tell that
// the second call did not re-mode a directory it did not create.
func (f *FakeFiles) MkdirAll(_ context.Context, path string, mode uint32) error {
	if err := f.r.record("files", "MkdirAll", path, fmt.Sprintf("%04o", mode)); err != nil {
		return err
	}
	if !contained(path, f.Roots) {
		return outsideRoots(path, f.Roots)
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if _, ok := f.Dirs[path]; !ok {
		f.Dirs[path] = mode
	}
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
	// An existing destination is refused, as the real driver refuses it:
	// rename(2) REPLACES a file (and an empty directory) silently, and a
	// decommission or a restore that landed on top of something already there
	// is data loss no step asked for.
	if _, taken := f.Files[to]; taken {
		return fmt.Errorf("%w: %s already exists; the ctl never renames over an existing path", jobs.ErrRefused, to)
	}
	if _, taken := f.Dirs[to]; taken {
		return fmt.Errorf("%w: %s already exists; the ctl never renames over an existing path", jobs.ErrRefused, to)
	}
	if v, ok := f.Files[from]; ok {
		f.Files[to] = v
		delete(f.Files, from)
		return nil
	}
	// A directory rename: move every path under from — the recorded
	// directories as well as the files, or a ReadDir of the destination would
	// report a tree that half moved.
	prefix := strings.TrimSuffix(from, "/") + "/"
	moved := false
	for p, v := range f.Files {
		if strings.HasPrefix(p, prefix) {
			f.Files[filepath.Join(to, strings.TrimPrefix(p, prefix))] = v
			delete(f.Files, p)
			moved = true
		}
	}
	for d, mode := range f.Dirs {
		switch {
		case d == from:
			f.Dirs[to] = mode
			delete(f.Dirs, d)
			moved = true
		case strings.HasPrefix(d, prefix):
			f.Dirs[filepath.Join(to, strings.TrimPrefix(d, prefix))] = mode
			delete(f.Dirs, d)
			moved = true
		}
	}
	if !moved {
		return fmt.Errorf("rename %s: no such file or directory", from)
	}
	return nil
}

// Remove deletes one file or one EMPTY directory.
//
// A directory with anything under it is refused, as os.Remove refuses one:
// the real driver's Remove is not a recursive delete, and a fake that quietly
// swallowed a whole subtree would let a step which removes a directory it
// thought was empty pass its tests and leave a tenant's data behind (or,
// worse, read as having deleted it) on the host.
func (f *FakeFiles) Remove(_ context.Context, path string) error {
	if err := f.r.record("files", "Remove", path); err != nil {
		return err
	}
	if !contained(path, f.Roots) {
		return outsideRoots(path, f.Roots)
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	prefix := strings.TrimSuffix(path, "/") + "/"
	for p := range f.Files {
		if strings.HasPrefix(p, prefix) {
			return fmt.Errorf("remove %s: directory not empty (it holds %s)", path, p)
		}
	}
	for d := range f.Dirs {
		if strings.HasPrefix(d, prefix) {
			return fmt.Errorf("remove %s: directory not empty (it holds %s)", path, d)
		}
	}
	delete(f.Files, path)
	delete(f.Dirs, path)
	return nil
}

// hasDir reports whether dir is a directory of this in-memory filesystem:
// one MkdirAll recorded, or one implied by a path stored under it.
func (f *FakeFiles) hasDir(dir string) bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	if _, ok := f.Dirs[dir]; ok {
		return true
	}
	prefix := strings.TrimSuffix(dir, "/") + "/"
	for p := range f.Files {
		if strings.HasPrefix(p, prefix) {
			return true
		}
	}
	for d := range f.Dirs {
		if strings.HasPrefix(d, prefix) {
			return true
		}
	}
	return false
}

func (f *FakeFiles) ReadFile(_ context.Context, path string) ([]byte, error) {
	if err := f.r.record("files", "ReadFile", path); err != nil {
		return nil, err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	v, ok := f.Files[path]
	if !ok {
		// fs.ErrNotExist, not a bare string: callers tell "the file is not
		// there" from "the file could not be read" with errors.Is, and a fake
		// whose absence is unrecognisable would let that distinction pass the
		// tests and fail on the host.
		return nil, fmt.Errorf("open %s: %w", path, fs.ErrNotExist)
	}
	return append([]byte(nil), v.Data...), nil
}

// ReadDir lists one level of the in-memory filesystem.
//
// The fake keeps no tree — a file's parents are implied by its path — so the
// entries are DERIVED: every stored path under dir contributes either its own
// base name (a file) or the first segment below dir (a directory). That is
// what lets the step that checksums a bundle read back what the steps before
// it wrote, without the fixture declaring directories nobody created.
func (f *FakeFiles) ReadDir(_ context.Context, dir string) ([]jobs.DirEntry, error) {
	if err := f.r.record("files", "ReadDir", dir); err != nil {
		return nil, err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	prefix := strings.TrimSuffix(dir, "/") + "/"
	seen := map[string]bool{}
	var out []jobs.DirEntry
	known := false
	add := func(name string, isDir bool) {
		if name == "" || seen[name] {
			return
		}
		seen[name] = true
		out = append(out, jobs.DirEntry{Name: name, IsDir: isDir})
	}
	for p := range f.Files {
		if !strings.HasPrefix(p, prefix) {
			continue
		}
		known = true
		rest := strings.TrimPrefix(p, prefix)
		if i := strings.Index(rest, "/"); i >= 0 {
			add(rest[:i], true)
			continue
		}
		add(rest, false)
	}
	for d := range f.Dirs {
		if d == dir {
			known = true
			continue
		}
		if !strings.HasPrefix(d, prefix) {
			continue
		}
		known = true
		name := strings.TrimPrefix(d, prefix)
		if i := strings.Index(name, "/"); i >= 0 {
			name = name[:i]
		}
		add(name, true)
	}
	if !known {
		// fs.ErrNotExist, like ReadFile: "there is no such directory" is a
		// fact a caller reads with errors.Is, not a failure to list one.
		return nil, fmt.Errorf("open %s: %w", dir, fs.ErrNotExist)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out, nil
}

// Sha256 is the digest and the size of one in-memory file.
func (f *FakeFiles) Sha256(_ context.Context, path string) (string, int64, error) {
	if err := f.r.record("files", "Sha256", path); err != nil {
		return "", 0, err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	v, ok := f.Files[path]
	if !ok {
		return "", 0, fmt.Errorf("open %s: %w", path, fs.ErrNotExist)
	}
	sum := sha256.Sum256(v.Data)
	return hex.EncodeToString(sum[:]), int64(len(v.Data)), nil
}

// DiskFree answers Free, defaulting to a terabyte: the ordinary fixture is a
// host with room, and a fake that answered zero would make every test which
// runs a backup seed a number in order to say nothing.
func (f *FakeFiles) DiskFree(_ context.Context, path string) (int64, error) {
	if err := f.r.record("files", "DiskFree", path); err != nil {
		return 0, err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.Free != 0 {
		return f.Free, nil
	}
	return 1 << 40, nil
}

// has reports whether path is in the in-memory filesystem. It is not a driver
// method and records nothing: the fake manager uses it to answer "is this
// unit's fragment still there", which on a real host is not a call at all.
func (f *FakeFiles) has(path string) bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	_, ok := f.Files[path]
	return ok
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

// Put seeds a file directly, bypassing the approved roots and the call log —
// it is how a test says "this file was already on the host", which is a fact
// about the fixture and not a driver call the assertions should see.
func (f *FakeFiles) Put(path string, data []byte, mode uint32) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Files[path] = FakeFile{Data: append([]byte(nil), data...), Mode: mode}
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
	r     *recorder
	now   func() time.Time
	files *FakeFiles
	mu    sync.Mutex
	// ByURL maps a base URL to the collections it holds.
	ByURL map[string][]string
	// Snapshots maps a collection to the snapshot names taken of it, in order.
	Snapshots map[string][]string
	// Counts maps "<baseURL>/<collection>" to its exact point count; an
	// absent key counts zero.
	Counts map[string]int64
	// Recovered records every recovery as "<baseURL> <collection> <location>".
	Recovered []string
	// SnapshotDirs maps a base URL to the host directory snapshots land in.
	SnapshotDirs map[string]string
	n            int
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
	// The FILE, where the real store would have written it: a backup moves the
	// snapshot into its bundle, and a fake that only returned a name would let
	// a step which never checked the move pass its tests.
	if dir := q.SnapshotDirs[base]; dir != "" && q.files != nil {
		q.files.Put(filepath.Join(dir, collection, name), []byte("fake qdrant snapshot of "+collection+"\n"), 0o640)
	}
	return name, nil
}

// Ready is the readiness probe; it has no state of its own, so a test makes
// a store un-ready through the failure table ("qdrant.Ready:<url>").
func (q *FakeQdrant) Ready(_ context.Context, base string) error {
	return q.r.record("qdrant", "Ready", base)
}

// Count is the exact point count of a collection.
//
// An UNKNOWN collection is an error, because that is what the store answers:
// POST /collections/<name>/points/count on a collection that is not there is a
// 404, not a zero. The fake used to answer zero, which made "the collection
// vanished between the inventory and the count" — the exact thing a fenced
// backup is checking for — indistinguishable from an empty collection.
func (q *FakeQdrant) Count(_ context.Context, base, collection string) (int64, error) {
	if err := q.r.record("qdrant", "Count", collection, base); err != nil {
		return 0, err
	}
	q.mu.Lock()
	defer q.mu.Unlock()
	if !containsString(q.ByURL[base], collection) {
		return 0, fmt.Errorf("qdrant at %s has no collection %q (HTTP 404)", base, collection)
	}
	return q.Counts[base+"/"+collection], nil
}

// Recover restores a collection from a snapshot location.
//
// It also REGISTERS the collection on the target URL, because after a real
// recover the collection exists: a restore whose verification step then asks
// for Collections would otherwise be checking a fixture rather than the
// effect of the step it just ran.
func (q *FakeQdrant) Recover(_ context.Context, base, collection, location string) error {
	if err := q.r.record("qdrant", "Recover", collection, location, base); err != nil {
		return err
	}
	q.mu.Lock()
	defer q.mu.Unlock()
	q.Recovered = append(q.Recovered, base+" "+collection+" "+location)
	for _, c := range q.ByURL[base] {
		if c == collection {
			return nil
		}
	}
	q.ByURL[base] = append(q.ByURL[base], collection)
	return nil
}

// DeleteSnapshot drops the snapshot from the ledger. It is Snapshot's
// rollback, so an absent snapshot is not an error: a rollback that runs twice
// must not fail the second time.
func (q *FakeQdrant) DeleteSnapshot(_ context.Context, base, collection, name string) error {
	if err := q.r.record("qdrant", "DeleteSnapshot", collection, name, base); err != nil {
		return err
	}
	q.mu.Lock()
	defer q.mu.Unlock()
	kept := q.Snapshots[collection][:0]
	for _, s := range q.Snapshots[collection] {
		if s != name {
			kept = append(kept, s)
		}
	}
	q.Snapshots[collection] = kept
	if dir := q.SnapshotDirs[base]; dir != "" && q.files != nil {
		q.files.mu.Lock()
		delete(q.files.Files, filepath.Join(dir, collection, name))
		q.files.mu.Unlock()
	}
	return nil
}

// FakeElasticsearch is an ES with an index list and a snapshot ledger.
type FakeElasticsearch struct {
	r  *recorder
	mu sync.Mutex
	// ByURL maps a base URL to the indices it holds.
	ByURL map[string][]string
	// Taken maps a repo to the snapshot names taken into it, in order. It is
	// not called `Snapshots` because the DRIVER METHOD is (GET
	// _snapshot/{repo}/_all), and Go lets a type have one or the other.
	Taken map[string][]string
	// Repos maps a registered repository to its settings.
	Repos map[string]Repo
	// Restored records every restore as "<repo> <name> <index,index>", and
	// SnapshotIndices every snapshot the same way.
	Restored        []string
	SnapshotIndices []string
	// Counts maps "<baseURL>/<index>" to its document count.
	Counts map[string]int64
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

// Snapshot records the snapshot and the index list it was given. The list is
// recorded because it is the fix for a real bug — the driver used to snapshot
// `*` while the caller's inventory excluded the cluster's system indices — and
// a fake that dropped it would let that come back untested.
func (e *FakeElasticsearch) Snapshot(_ context.Context, base, repo, name string, indices []string) error {
	if err := e.r.record("es", "Snapshot", repo, name, strings.Join(indices, ","), base); err != nil {
		return err
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	e.Taken[repo] = append(e.Taken[repo], name)
	e.SnapshotIndices = append(e.SnapshotIndices, repo+" "+name+" "+strings.Join(indices, ","))
	return nil
}

// Ready is the readiness probe; a test makes a cluster un-ready through the
// failure table ("es.Ready:<url>").
func (e *FakeElasticsearch) Ready(_ context.Context, base string) error {
	return e.r.record("es", "Ready", base)
}

// RegisterRepo registers a filesystem snapshot repository.
//
// Registering the SAME repo at a DIFFERENT location is refused, which is what
// ES does and what the backup depends on: two bundles that reused one repo
// name would each believe the other's directory was theirs. Re-registering at
// the same location is idempotent (the readonly flag may change — that is how
// the verify leg re-opens a copied directory read-only).
func (e *FakeElasticsearch) RegisterRepo(_ context.Context, base, repo, location string, readonly bool) error {
	if err := e.r.record("es", "RegisterRepo", repo, location, strconv.FormatBool(readonly), base); err != nil {
		return err
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	if have, ok := e.Repos[repo]; ok && have.Location != location {
		return fmt.Errorf("%w: repository %q is already registered at %s; it cannot also be %s",
			jobs.ErrRefused, repo, have.Location, location)
	}
	e.Repos[repo] = Repo{Location: location, ReadOnly: readonly}
	return nil
}

// UnregisterRepo drops the registration and leaves the files alone — the
// backup moves the directory into the bundle afterwards. An unknown repo is
// not an error: this is the rollback half of RegisterRepo.
func (e *FakeElasticsearch) UnregisterRepo(_ context.Context, base, repo string) error {
	if err := e.r.record("es", "UnregisterRepo", repo, base); err != nil {
		return err
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	delete(e.Repos, repo)
	return nil
}

// Snapshots lists the snapshot names a registered repository holds, sorted.
//
// An UNREGISTERED repository is an error, which is what makes it a
// verification: the backup re-registers the directory it just wrote under a
// second, read-only name and asks this question of it, and a directory
// elasticsearch could not open as a repository has to answer differently from
// one that is simply empty.
func (e *FakeElasticsearch) Snapshots(_ context.Context, base, repo string) ([]string, error) {
	if err := e.r.record("es", "Snapshots", repo, base); err != nil {
		return nil, err
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	if _, ok := e.Repos[repo]; !ok {
		return nil, fmt.Errorf("%w: no snapshot repository %q is registered", jobs.ErrRefused, repo)
	}
	// Every repository registered at the same LOCATION sees the same
	// snapshots: that is the whole point of re-registering a directory under a
	// verify name, and a fake that keyed snapshots by repo name alone would
	// make the verification pass on an empty answer.
	loc := e.Repos[repo].Location
	var out []string
	for name, r := range e.Repos {
		if r.Location != loc {
			continue
		}
		out = append(out, e.Taken[name]...)
	}
	sort.Strings(out)
	return out, nil
}

// Restore restores indices from a snapshot, and refuses when the repository
// is not registered or holds no such snapshot — the two ways a restore
// against the wrong bundle fails on a real cluster, and the two a restore
// test has to be able to reach.
func (e *FakeElasticsearch) Restore(_ context.Context, base, repo, name string, indices []string) error {
	if err := e.r.record("es", "Restore", repo, name, strings.Join(indices, ","), base); err != nil {
		return err
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	if _, ok := e.Repos[repo]; !ok {
		return fmt.Errorf("%w: no snapshot repository %q is registered", jobs.ErrRefused, repo)
	}
	found := false
	for _, s := range e.Taken[repo] {
		if s == name {
			found = true
		}
	}
	if !found {
		return fmt.Errorf("%w: repository %q holds no snapshot %q", jobs.ErrRefused, repo, name)
	}
	e.Restored = append(e.Restored, repo+" "+name+" "+strings.Join(indices, ","))
	for _, idx := range indices {
		if !containsString(e.ByURL[base], idx) {
			e.ByURL[base] = append(e.ByURL[base], idx)
		}
	}
	return nil
}

// Count is the document count of an index; an unknown index counts zero.
func (e *FakeElasticsearch) Count(_ context.Context, base, index string) (int64, error) {
	if err := e.r.record("es", "Count", index, base); err != nil {
		return 0, err
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.Counts[base+"/"+index], nil
}

// ---------------------------------------------------------------- tenant API

// FakeTenantAPI records the allowlisted calls to a tenant's own API.
type FakeTenantAPI struct {
	r        *recorder
	mu       sync.Mutex
	Healths  []string
	Accounts []string // "<origin> <action> <subject> <role>"
	// DeepHealths records every deep health check, by origin.
	DeepHealths []string
	// Versions maps an origin to what GET /v1/version answers; an origin with
	// no entry answers {"version": "fake"} rather than failing, so a post-check
	// on a tenant the fixture did not describe still runs.
	Versions map[string]map[string]any
	// CollectionsByOrigin maps an origin to the tenant API's inventory.
	CollectionsByOrigin map[string][]string
	// Ingests records every ingest as "<origin> <path>", in order.
	Ingests []string
	// IngestStates maps an ingest job id to what IngestStatus answers for it.
	// An id with no entry answers "succeeded": the ordinary fixture is an
	// ingest that worked, and a fake that made every caller seed a map in
	// order to say nothing would be a fake about bookkeeping. A test that
	// wants a failure either seeds this or fails `tenantapi.Ingest` outright.
	IngestStates map[string]string
	// nextIngest numbers the job ids this fake hands out.
	nextIngest int
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

// ServiceAccount records the account change.
//
// The API KEY is deliberately absent from both the call log and the ledger: it
// is a secret, and a fake that recorded it would put a live credential into
// every test's failure output and into the golden files that are read from
// them.
func (a *FakeTenantAPI) ServiceAccount(_ context.Context, origin, _, subject, role, purpose, action string) error {
	if err := a.r.record("tenantapi", "ServiceAccount", subject, action, role, purpose, origin); err != nil {
		return err
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	a.Accounts = append(a.Accounts, origin+" "+action+" "+subject+" "+role)
	return nil
}

// Version answers the seeded version document, or a recognisably fake one.
func (a *FakeTenantAPI) Version(_ context.Context, origin, _ string) (map[string]any, error) {
	if err := a.r.record("tenantapi", "Version", origin); err != nil {
		return nil, err
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	if v, ok := a.Versions[origin]; ok {
		out := make(map[string]any, len(v))
		for k, vv := range v {
			out[k] = vv
		}
		return out, nil
	}
	return map[string]any{"version": "fake"}, nil
}

func (a *FakeTenantAPI) DeepHealth(_ context.Context, origin, _ string) error {
	if err := a.r.record("tenantapi", "DeepHealth", origin); err != nil {
		return err
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	a.DeepHealths = append(a.DeepHealths, origin)
	return nil
}

// Collections is the tenant API's inventory, sorted.
func (a *FakeTenantAPI) Collections(_ context.Context, origin, _ string) ([]string, error) {
	if err := a.r.record("tenantapi", "Collections", origin); err != nil {
		return nil, err
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	out := append([]string(nil), a.CollectionsByOrigin[origin]...)
	sort.Strings(out)
	return out, nil
}

// Ingest records the ingest and hands back a job id.
//
// It does NOT carry the real driver's sandbox-only refusal. That rule is about
// which HOST a credential-bearing write may reach, and this fake reaches none;
// the tests that prove the rule exercise the real client against an httptest
// server, where the origin is the thing under test.
func (a *FakeTenantAPI) Ingest(_ context.Context, origin, _, path string) (string, error) {
	if err := a.r.record("tenantapi", "Ingest", origin, path); err != nil {
		return "", err
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	a.Ingests = append(a.Ingests, origin+" "+path)
	a.nextIngest++
	return fmt.Sprintf("fake-ingest-%d", a.nextIngest), nil
}

// IngestStatus answers the seeded state, or "succeeded".
func (a *FakeTenantAPI) IngestStatus(_ context.Context, origin, _, jobID string) (string, error) {
	if err := a.r.record("tenantapi", "IngestStatus", origin, jobID); err != nil {
		return "", err
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	if s, ok := a.IngestStates[jobID]; ok {
		return s, nil
	}
	return "succeeded", nil
}

// ---------------------------------------------------------------- git

// FakeGit is a mirror with a ref table and the worktrees checked out of it.
type FakeGit struct {
	r  *recorder
	mu sync.Mutex
	// Refs maps a ref to the sha it resolves to.
	Refs map[string]string
	// Worktrees maps a checked-out directory to the sha in it.
	Worktrees map[string]string
	// Removed records every worktree removal, in order.
	Removed []string
}

// ResolveRef resolves a ref through the table. A 40-hex ref resolves to
// ITSELF without a table entry, because that is what `rev-parse` does with a
// sha and because `fleet artifact prepare --tag <sha>` is a documented way to
// pin an artifact. An unknown ref is an error: silently resolving it to
// something would check out code nobody named.
func (g *FakeGit) ResolveRef(_ context.Context, mirror, ref string) (string, error) {
	if err := g.r.record("git", "ResolveRef", ref, mirror); err != nil {
		return "", err
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	if sha, ok := g.Refs[ref]; ok {
		return sha, nil
	}
	if isSHA(ref) {
		return ref, nil
	}
	return "", fmt.Errorf("%w: %s is not a ref this mirror (%s) knows", jobs.ErrRefused, ref, mirror)
}

// AddWorktree checks sha out into dest, refusing what the real driver refuses:
// a ref that is not a resolved 40-hex commit (a worktree pinned to a branch
// would move under the tenant — MEMORY "tenant code isolation"), and a dest
// that already exists (the ctl never checks out over a directory it did not
// make).
func (g *FakeGit) AddWorktree(_ context.Context, mirror, sha, dest string) error {
	if err := g.r.record("git", "AddWorktree", dest, sha, mirror); err != nil {
		return err
	}
	if !isSHA(sha) {
		return fmt.Errorf("%w: %q is not a 40-hex commit; a worktree is only ever checked out at a resolved sha",
			jobs.ErrRefused, sha)
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	if _, ok := g.Worktrees[dest]; ok {
		return fmt.Errorf("%w: %s already exists; the ctl never checks out over an existing directory",
			jobs.ErrRefused, dest)
	}
	g.Worktrees[dest] = sha
	return nil
}

// RemoveWorktree forgets the worktree. An unknown directory is not an error:
// this is a rollback, and `worktree remove` of something already gone is the
// state the rollback wanted.
func (g *FakeGit) RemoveWorktree(_ context.Context, mirror, dest string) error {
	if err := g.r.record("git", "RemoveWorktree", dest, mirror); err != nil {
		return err
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	delete(g.Worktrees, dest)
	g.Removed = append(g.Removed, dest)
	return nil
}

// Describe names the code in dir: the short sha of the worktree when this
// fake checked it out, and an obviously fake string when it did not.
func (g *FakeGit) Describe(_ context.Context, dir string) (string, error) {
	if err := g.r.record("git", "Describe", dir); err != nil {
		return "", err
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	if sha, ok := g.Worktrees[dir]; ok && len(sha) >= 12 {
		return sha[:12], nil
	}
	return "fake-describe", nil
}

// isSHA reports whether s is a 40-character hex object name.
func isSHA(s string) bool {
	if len(s) != 40 {
		return false
	}
	for _, c := range s {
		if !strings.ContainsRune("0123456789abcdef", c) {
			return false
		}
	}
	return true
}

// ---------------------------------------------------------------- build

// FakeBuild is npm and vite as two sets and a ledger.
type FakeBuild struct {
	r     *recorder
	files *FakeFiles
	mu    sync.Mutex
	// Installed is the set of worktrees whose node_modules are present.
	Installed map[string]bool
	// Builds records every UI build as "<worktree> <base> <outDir>".
	Builds []string
	// AllowUninstalled lets UI build without node_modules. It exists for the
	// tests that are about something else and should not have to run an npm
	// install they are not testing.
	AllowUninstalled bool
}

func (b *FakeBuild) NpmCI(_ context.Context, worktree, cacheDir string) error {
	if err := b.r.record("build", "NpmCI", worktree, cacheDir); err != nil {
		return err
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	b.Installed[worktree] = true
	return nil
}

// UI builds the tenant UI into outDir.
//
// It refuses when the worktree has no node_modules, exactly as the real driver
// does: a `tenant create` that quietly installed packages from the network
// would be running code nobody reviewed, on a host whose artifacts are
// supposed to be prepared in advance. The built index.html is written into the
// in-memory filesystem, so a later step that reads the dist tree — a
// checksum, a bundle — is reading the effect of this step.
func (b *FakeBuild) UI(_ context.Context, worktree, base, outDir string) error {
	if err := b.r.record("build", "UI", worktree, base, outDir); err != nil {
		return err
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	if !b.Installed[worktree] && !b.AllowUninstalled {
		return fmt.Errorf("%w: %s has no node_modules; run the artifact's npm ci first", jobs.ErrRefused, worktree)
	}
	// The real driver's other two rules. The base is hard-coded into every
	// asset URL of the built bundle, so a base that does not match the route
	// the gateway publishes is a UI that loads a blank page; and the build runs
	// `vite --emptyOutDir`, which DELETES the directory's contents first, so an
	// unchecked outDir is a delete of any path the caller named.
	if !baseRE.MatchString(base) {
		return fmt.Errorf("%w: %q is not a UI base path (/ragstack/<tenant>/ui/)", jobs.ErrRefused, base)
	}
	if !contained(outDir, b.files.Roots) {
		return outsideRoots(outDir, b.files.Roots)
	}
	b.Builds = append(b.Builds, worktree+" "+base+" "+outDir)
	b.files.Put(filepath.Join(outDir, "index.html"),
		[]byte("<!doctype html><!-- fake vite build of "+worktree+" at base "+base+" -->\n"), 0o644)
	return nil
}

// ---------------------------------------------------------------- postgres

// FakePostgres is pg_isready/pg_dump/pg_restore over a socket that is not
// there.
type FakePostgres struct {
	r     *recorder
	files *FakeFiles
	mu    sync.Mutex
	// Readiness maps a run directory to whether pg_isready succeeds. An
	// ABSENT entry is ready — see FakeOptions.PostgresReady. It is not called
	// `Ready` because the driver's METHOD is, and Go lets a type have one or
	// the other.
	Readiness map[string]bool
	// Dumps records every dump as "<runDir> <db> <out>", Restores likewise.
	Dumps    []string
	Restores []string
}

func (p *FakePostgres) pgReady(runDir string) bool {
	if v, ok := p.Readiness[runDir]; ok {
		return v
	}
	return true
}

func (p *FakePostgres) Ready(_ context.Context, spec jobs.PostgresSpec) error {
	if err := p.r.record("postgres", "Ready", spec.RunDir, spec.DB); err != nil {
		return err
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	if !p.pgReady(spec.RunDir) {
		return fmt.Errorf("pg_isready: no response on the socket in %s", spec.RunDir)
	}
	return nil
}

// Dump writes a placeholder where pg_dump would have written the archive, so
// the steps that follow — the checksum, the manifest, the free-space check —
// find a file rather than a gap.
func (p *FakePostgres) Dump(_ context.Context, spec jobs.PostgresSpec, out string) error {
	if err := p.r.record("postgres", "Dump", spec.RunDir, spec.DB, out); err != nil {
		return err
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	// The DIRECTORY must already exist, as it must for the real driver: it is
	// what gets bound at /mnt/ctl, and pg_dump writes the file inside it.
	if !p.files.hasDir(filepath.Dir(out)) {
		return fmt.Errorf("%w: %s is not an existing directory to write the dump into",
			jobs.ErrRefused, filepath.Dir(out))
	}
	p.Dumps = append(p.Dumps, spec.RunDir+" "+spec.DB+" "+out)
	p.files.Put(out, []byte("fake pg_dump -Fc of "+spec.DB+"\n"), 0o640)
	return nil
}

func (p *FakePostgres) Restore(_ context.Context, spec jobs.PostgresSpec, in string) error {
	if err := p.r.record("postgres", "Restore", spec.RunDir, spec.DB, in); err != nil {
		return err
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	p.Restores = append(p.Restores, spec.RunDir+" "+spec.DB+" "+in)
	return nil
}

// ---------------------------------------------------------------- sqlite

// FakeSQLite copies one in-memory file to another.
type FakeSQLite struct {
	r     *recorder
	files *FakeFiles
	mu    sync.Mutex
	// Backups records every backup as "<src> <dst>".
	Backups []string
}

// Backup copies src to dst at 0640 and reports integrity "ok".
//
// An absent src is fs.ErrNotExist, the same error FakeFiles.ReadFile gives,
// because the backup's rule is that it copies only the state files that exist
// and that anything else is an error the operator has to see — a caller
// telling those two apart with errors.Is must be able to do so here too.
func (s *FakeSQLite) Backup(_ context.Context, src, dst string) (string, error) {
	if err := s.r.record("sqlite", "Backup", src, dst); err != nil {
		return "", err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	data := s.files.Content(src)
	if data == nil {
		return "", fmt.Errorf("open %s: %w", src, fs.ErrNotExist)
	}
	// The real driver's two destination rules, which a backup depends on: the
	// bundle directory is made by an earlier step (a driver that created it
	// would create it with the wrong mode), and a backup never overwrites.
	if !s.files.hasDir(filepath.Dir(dst)) {
		return "", fmt.Errorf("%w: %s is not an existing directory to write the backup into",
			jobs.ErrRefused, filepath.Dir(dst))
	}
	if s.files.Content(dst) != nil {
		return "", fmt.Errorf("%w: %s already exists; a backup never overwrites", jobs.ErrRefused, dst)
	}
	s.files.Put(dst, data, 0o640)
	s.Backups = append(s.Backups, src+" "+dst)
	return "ok", nil
}

// ---------------------------------------------------------------- archive

// FakeArchive is tar without a tar.
type FakeArchive struct {
	r     *recorder
	files *FakeFiles
	mu    sync.Mutex
	// Created records every archive as "<dir> <out>", Extracted every
	// extraction as "<tarPath> <dest>".
	Created   []string
	Extracted []string
}

// Create writes a placeholder at out. The bundle's own checksum step reads
// it, so it has to exist; what is IN it is not something a fake can be honest
// about, and pretending otherwise would invite a test to assert on invented
// tar bytes.
func (a *FakeArchive) Create(_ context.Context, dir, out string) error {
	if err := a.r.record("archive", "Create", dir, out); err != nil {
		return err
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.files.Content(out) != nil {
		return fmt.Errorf("%w: %s already exists; an archive never overwrites", jobs.ErrRefused, out)
	}
	a.Created = append(a.Created, dir+" "+out)
	a.files.Put(out, []byte("fake tar of "+dir+"\n"), 0o640)
	return nil
}

// Extract records the extraction and writes NOTHING: the real driver's whole
// job is deciding which entries it refuses, and a fake that invented files
// would be asserting on a policy it does not implement.
func (a *FakeArchive) Extract(_ context.Context, tarPath, dest string, limits jobs.ArchiveLimits) error {
	if err := a.r.record("archive", "Extract", tarPath, dest,
		strconv.Itoa(limits.MaxEntries), strconv.FormatInt(limits.MaxBytes, 10)); err != nil {
		return err
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	a.Extracted = append(a.Extracted, tarPath+" "+dest)
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

// The seeds are COPIED rather than kept: a fixture map shared between the
// options a test builds and the fake it builds them into is a fixture the
// fake can rewrite under the test.
func copyMapString(m map[string]string) map[string]string {
	out := make(map[string]string, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}

func copyMapBool(m map[string]bool) map[string]bool {
	out := make(map[string]bool, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}

func copyMapInt(m map[string]int) map[string]int {
	out := make(map[string]int, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}

func copyMapInt64(m map[string]int64) map[string]int64 {
	out := make(map[string]int64, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}

func copyMapOwners(m map[int]PortOwner) map[int]PortOwner {
	out := make(map[int]PortOwner, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}

func copyMapRepo(m map[string]Repo) map[string]Repo {
	out := make(map[string]Repo, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}

func copyMapAny(m map[string]map[string]any) map[string]map[string]any {
	out := make(map[string]map[string]any, len(m))
	for k, v := range m {
		inner := make(map[string]any, len(v))
		for ik, iv := range v {
			inner[ik] = iv
		}
		out[k] = inner
	}
	return out
}

func containsString(hay []string, needle string) bool {
	for _, h := range hay {
		if h == needle {
			return true
		}
	}
	return false
}
