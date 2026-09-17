package drivers

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io/fs"
	"os"
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
	// CollectionCounts maps "<tenant origin>/<collection>" to the chunk count
	// GET /v1/collections?counts=true reports. Absent falls back to the qdrant
	// count of the same collection on the same port block, so the handover
	// census does not need seeding twice.
	CollectionCounts map[string]int64
	// RunningIngestJobs maps a tenant origin to the ingest jobs it reports as
	// still running. Absent is none, which is the ordinary fixture.
	RunningIngestJobs map[string][]string
	// KeyStatuses maps an API-KEY VALUE to the status the tenant answers when
	// it is presented. Absent is 200; a test proving a revocation seeds the
	// 401 for the value it revoked.
	KeyStatuses map[string]int
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
	// RunningInstances are the instances already up IN THE CTL's REGISTRY when
	// the fake is built — an instance-supervised tenant the fixture says is
	// ACTIVE. Without them such a tenant looks stopped to `running`, so a
	// fence would find nothing to stop and a start would run a second copy of
	// a store that is already there.
	RunningInstances []string
	// AccountRunningInstances are the instances up in the running ACCOUNT's
	// DEFAULT registry ($HOME/.apptainer) — a tenant somebody started by hand,
	// which is every tenant a handover release acts on.
	//
	// The two tables are separate because on the host they are separate
	// directories, and a fake with one table could not have caught the release
	// that reported "stopped postgres-hackathon" while postgres-hackathon kept
	// serving: the ctl looked in its OWN registry, found nothing, and called
	// that a stop.
	AccountRunningInstances []string
	// AlivePIDs are the pids Proc.Alive answers true for without this fake
	// having spawned them — the pid in an adopted or fixture tenant's pidfile
	// — mapped to the port each one holds (0 for none). Spawn fills the same
	// two tables from what it started.
	//
	// The PORT is half the entry because a fixture pid that dies on a TERM and
	// goes on holding its socket is a tenant nothing can ever stop: the
	// instance supervisor signals, then waits for the port to free, then
	// kills, then waits again — and against such a fixture it would do all of
	// that and time out.
	AlivePIDs map[int]int
	// Crontab is the account's crontab at the start. Nil is an account that
	// has never had one — which List reports as an empty body and no error,
	// as the real crontab(1) wrapper does.
	Crontab []byte
	// FileUID is the uid this fake host runs as: the owner of everything it
	// creates and Stat's answer for anything FileOwners does not name. Zero is
	// a perfectly good uid, so it needs no sentinel.
	FileUID int
	// FileOwners are the paths owned by ANOTHER account: path → uid, a
	// directory's entry covering its tree. It is how a fixture says "this
	// tenant's pgdata is wilke's and this ctl is svcbvbrc's", which is the
	// only fact that makes a postgres refuse to start.
	FileOwners map[string]int
	// InstanceExitOnRun names instances that START AND DIE, mapped to what
	// they write to their .err log on the way out. It models the container
	// apptainer starts successfully and that then exits — the shape of every
	// store failure the readiness wait used to discover three minutes later.
	InstanceExitOnRun map[string]string
	// InstanceLogRoot is where this fake files its instance logs. Empty takes
	// the fixture default.
	InstanceLogRoot string
	// InstanceIgnoreInitdbArgs makes a postgres instance initialise its cluster
	// as if POSTGRES_INITDB_ARGS had not been passed — the SQL_ASCII/C an
	// `LC_ALL=C` in the caller's environment produces on the real host.
	//
	// It models the failure the handover's encoding check exists to catch: an
	// image, an entrypoint or an environment that does not honour what the take
	// asked for. Without it a test cannot tell a take that pins the encoding
	// from one that is merely lucky.
	InstanceIgnoreInitdbArgs bool
	// PostgresContents is what each tenant's postgres holds, by RUN
	// DIRECTORY: the size and the exact per-table row counts a dump carries
	// and a restore replays. A cluster with no entry is EMPTY, which is what a
	// freshly initdb'd one is.
	PostgresContents map[string]jobs.PostgresCensus
	// PostgresRestoreDrops makes a restore lose rows (table name → how many),
	// which is the one failure the handover's row counts exist to catch.
	PostgresRestoreDrops map[string]int64
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
		UID: opts.FileUID, Owners: copyMapInt(opts.FileOwners),
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
		r: &f.recorder, Versions: copyMapAny(opts.Versions), qdrant: f.qdrant, files: f.files,
		CollectionsByOrigin: copyMapSlice(opts.CollectionsByOrigin),
		CountsByOrigin:      copyMapInt64(opts.CollectionCounts),
		RunningByOrigin:     copyMapSlice(opts.RunningIngestJobs),
		KeyStatuses:         copyMapInt(opts.KeyStatuses),
	}
	f.git = &FakeGit{r: &f.recorder, Refs: copyMapString(opts.Refs), Worktrees: copyMapString(opts.Worktrees)}
	f.build = &FakeBuild{r: &f.recorder, files: f.files, Installed: setOf(opts.Installed)}
	f.pg = &FakePostgres{r: &f.recorder, files: f.files, Readiness: copyMapBool(opts.PostgresReady),
		Contents: copyMapCensus(opts.PostgresContents), RestoreDrops: copyMapInt64(opts.PostgresRestoreDrops)}
	f.sqlite = &FakeSQLite{r: &f.recorder, files: f.files}
	f.archive = &FakeArchive{r: &f.recorder, files: f.files}
	// The instances and the LISTEN set are the SAME fixture, for the reason
	// the units and the LISTEN set are: a store this host started has to be a
	// store a readiness probe can find.
	f.instances = &FakeInstances{
		r: &f.recorder, proc: f.proc, files: f.files, pg: f.pg, ports: copyMapInt(opts.InstancePorts),
		Running: map[string]jobs.Instance{}, AccountRunning: map[string]jobs.Instance{}, nextPID: 21001,
		LogRoot: opts.InstanceLogRoot, ExitOnRun: copyMapString(opts.InstanceExitOnRun),
		IgnoreInitdbArgs: opts.InstanceIgnoreInitdbArgs,
	}
	// In this order, and not over a map of the two: the pids this fake hands
	// out are part of what a test asserts, and a map would shuffle them.
	for _, name := range opts.RunningInstances {
		f.instances.start(jobs.NamespaceCtl, name, "fixture.sif")
	}
	for _, name := range opts.AccountRunningInstances {
		f.instances.start(jobs.NamespaceAccountDefault, name, "fixture.sif")
	}
	for pid, port := range opts.AlivePIDs {
		if f.proc.alive == nil {
			f.proc.alive = map[int]bool{}
		}
		f.proc.alive[pid] = true
		if port != 0 {
			f.proc.Ports[port] = true
			if f.proc.spawnPorts == nil {
				f.proc.spawnPorts = map[int]int{}
			}
			f.proc.spawnPorts[pid] = port
		}
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
	// parents maps a pid to its parent, which is what Descends walks. It is
	// filled by FakeInstances when it starts an instance: the process on the
	// port is the instance's CHILD, as it is on the host.
	parents map[int]int
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

// MarkAlive makes a pid this fake did not spawn a live process holding port —
// the hand-started API a manual tenant runs, which the ctl finds through a
// pidfile and stops with a signal. A TERM or a KILL frees the port, exactly as
// it does for a process this fake spawned itself.
func (p *FakeProc) MarkAlive(pid, port int) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.alive == nil {
		p.alive = map[int]bool{}
	}
	if p.spawnPorts == nil {
		p.spawnPorts = map[int]int{}
	}
	if p.Ports == nil {
		p.Ports = map[int]bool{}
	}
	if p.Owners == nil {
		p.Owners = map[int]PortOwner{}
	}
	p.alive[pid] = true
	p.spawnPorts[pid] = port
	p.Ports[port] = true
	p.Owners[port] = PortOwner{PID: pid, UID: os.Getuid()}
}

// FreePort takes a port out of the LISTEN set, as a fixture adjustment: the
// tenant this test is about has already been stopped.
func (p *FakeProc) FreePort(port int) {
	p.mu.Lock()
	defer p.mu.Unlock()
	delete(p.Ports, port)
	delete(p.Owners, port)
}

// SetOwner puts a process behind a port's LISTEN socket. pid 0 is the real
// driver's answer for a socket this account cannot attribute — another
// account's — which is the case several refusals are written against.
func (p *FakeProc) SetOwner(port, pid, uid int) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.Ports == nil {
		p.Ports = map[int]bool{}
	}
	if p.Owners == nil {
		p.Owners = map[int]PortOwner{}
	}
	p.Ports[port] = true
	p.Owners[port] = PortOwner{PID: pid, UID: uid}
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

// Descends walks the recorded parent map, as the real driver walks
// /proc/<pid>/stat. A pid with no recorded parent is its own root: it descends
// from itself and from nothing else, which is the answer for every pid in a
// fixture that has not said otherwise.
func (p *FakeProc) Descends(_ context.Context, pid, ancestor int) (bool, error) {
	if err := p.r.record("proc", "Descends", strconv.Itoa(pid), strconv.Itoa(ancestor)); err != nil {
		return false, err
	}
	if pid <= 0 || ancestor <= 0 {
		return false, fmt.Errorf("%w: %d and %d are not both pids the ctl asks about", jobs.ErrRefused, pid, ancestor)
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	for i := 0; i < 64; i++ {
		if pid == ancestor {
			return true, nil
		}
		next, ok := p.parents[pid]
		if !ok || next == 0 {
			return false, nil
		}
		pid = next
	}
	return false, fmt.Errorf("%w: this fixture's process ancestry is a loop", jobs.ErrRefused)
}

// SetParent records that pid's parent is ppid — the fixture form of a process
// tree, for a test that needs one the instances fake did not build.
func (p *FakeProc) SetParent(pid, ppid int) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.parents == nil {
		p.parents = map[int]int{}
	}
	p.parents[pid] = ppid
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

// FakeInstances is `apptainer instance` as TWO maps of running instances.
//
// It models the facts an instance supervisor depends on and a stub could not
// give it:
//
//   - a name is UNIQUE WITHIN A REGISTRY (apptainer refuses a second instance
//     under a name that is taken, which is what makes a start idempotency
//     check possible at all);
//   - there is MORE THAN ONE REGISTRY. The ctl forces its own
//     APPTAINER_CONFIGDIR on its calls; a tenant somebody started by hand is
//     in the account's default one. A fake with a single table said "stopped"
//     to a release that had looked in the empty one, which is precisely the
//     outage this fixture now reproduces;
//   - a running store OWNS A PORT, and the process on that port is the
//     instance's CHILD, not the instance's own pid (coconut: instance
//     postgres-hackathon 630746, postgres 631059 on 24085). An identity check
//     written against pid equality passes here and refuses there, so the fake
//     models the child.
type FakeInstances struct {
	r    *recorder
	mu   sync.Mutex
	proc *FakeProc
	// files is the in-memory filesystem SeedConfigDir copies into.
	files *FakeFiles
	// pg is the postgres driver, so that starting a postgres instance on an
	// EMPTY data directory initialises an empty cluster — which is what the
	// image's entrypoint does, and the fact the handover's whole migration
	// turns on.
	pg *FakePostgres
	// ports links an instance name to the port running it binds.
	ports   map[string]int
	nextPID int
	// Running is the CTL's instance table (jobs.NamespaceCtl), by name.
	Running map[string]jobs.Instance
	// AccountRunning is the running account's DEFAULT instance table
	// (jobs.NamespaceAccountDefault), by name.
	AccountRunning map[string]jobs.Instance
	// Seeded records every SeedConfigDir as "<sif>:<containerDir>→<hostDir>",
	// in order: what a test reads to see that the ES config bind was filled
	// from the image BEFORE the instance started.
	Seeded []string
	// LogRoot is where this fake host files its instance logs — the fixture's
	// spelling of `<configdir>/instances/logs/<host>/<user>`. Empty takes
	// fakeInstanceLogRoot.
	LogRoot string
	// IgnoreInitdbArgs makes this fake's initdb behave as if it had been given
	// no POSTGRES_INITDB_ARGS: see FakeOptions.InstanceIgnoreInitdbArgs.
	IgnoreInitdbArgs bool
	// ExitOnRun makes a named instance START AND DIE: Run succeeds, nothing
	// enters the table, and the text is written to the instance's .err file on
	// the in-memory filesystem.
	//
	// It is the only way to model the failure this fixture now has to produce.
	// A postgres handed a data directory it does not own is not a Run that
	// fails — apptainer starts it happily — it is a container that writes one
	// line ("data directory has wrong ownership") and exits, leaving no row in
	// the instance table and no exit status any caller can read. A fake whose
	// instances either start or fail to start cannot produce the three minutes
	// of waiting that followed on coconut.
	ExitOnRun map[string]string
}

// fakeInstanceLogRoot is the fixture's instance-log directory. It mirrors the
// real layout (`<configdir>/instances/logs/<host>/<user>`) closely enough that
// a path read out of the fake is recognisably the one the host would give.
const fakeInstanceLogRoot = "/rag/data/ctl/apptainer/config/instances/logs/coconut/svcbvbrc"

// logRoot is LogRoot or the default.
func (i *FakeInstances) logRoot() string {
	if i.LogRoot != "" {
		return i.LogRoot
	}
	return fakeInstanceLogRoot
}

var _ jobs.Instances = (*FakeInstances)(nil)

// table is the registry a namespace names. Caller holds the lock.
func (i *FakeInstances) table(ns jobs.InstanceNamespace) map[string]jobs.Instance {
	if ns == jobs.NamespaceAccountDefault {
		return i.AccountRunning
	}
	return i.Running
}

// nsLabel is what a call record says about the namespace, so a test reading
// the trace sees WHICH registry a step asked about — the fact the outage
// turned on.
func nsLabel(ns jobs.InstanceNamespace) string {
	if ns == jobs.NamespaceAccountDefault {
		return "account-default"
	}
	return "ctl"
}

// start puts an instance in one registry and makes it hold its port, through a
// CHILD process the way apptainer does. Caller holds the lock (or is the
// constructor, which is not shared yet).
func (i *FakeInstances) start(ns jobs.InstanceNamespace, name, sif string) jobs.Instance {
	if i.nextPID == 0 {
		i.nextPID = 21001
	}
	pid := i.nextPID
	// Two pids: the instance's starter and the service inside it. They are
	// consecutive rather than equal because an identity check that compares
	// the port's owner to the instance's pid is the bug this models.
	servicePID := pid + 1
	i.nextPID += 2
	in := jobs.Instance{Name: name, PID: pid, Image: sif,
		LogOut: filepath.Join(i.logRoot(), name+".out"),
		LogErr: filepath.Join(i.logRoot(), name+".err")}
	i.table(ns)[name] = in
	if port, ok := i.ports[name]; ok {
		i.proc.mu.Lock()
		if i.proc.Ports == nil {
			i.proc.Ports = map[int]bool{}
		}
		if i.proc.Owners == nil {
			i.proc.Owners = map[int]PortOwner{}
		}
		if i.proc.parents == nil {
			i.proc.parents = map[int]int{}
		}
		i.proc.Ports[port] = true
		i.proc.Owners[port] = PortOwner{PID: servicePID, UID: os.Getuid()}
		i.proc.parents[servicePID] = pid
		i.proc.mu.Unlock()
	}
	return in
}

// stop removes an instance from one registry and frees its port. Caller holds
// the lock.
func (i *FakeInstances) stop(ns jobs.InstanceNamespace, name string) {
	delete(i.table(ns), name)
	port, ok := i.ports[name]
	if !ok {
		return
	}
	i.proc.mu.Lock()
	delete(i.proc.Ports, port)
	if o, had := i.proc.Owners[port]; had {
		delete(i.proc.parents, o.PID)
	}
	delete(i.proc.Owners, port)
	i.proc.mu.Unlock()
}

// List is the running instances of ONE registry, sorted by name — `apptainer
// instance list` sorts too, and a driver whose order came out of a Go map
// would make every test that prints it flaky.
func (i *FakeInstances) List(_ context.Context, opts jobs.ListOptions) ([]jobs.Instance, error) {
	if err := i.r.record("instances", "List", nsLabel(opts.Namespace)); err != nil {
		return nil, err
	}
	i.mu.Lock()
	defer i.mu.Unlock()
	tbl := i.table(opts.Namespace)
	names := make([]string, 0, len(tbl))
	for n := range tbl {
		names = append(names, n)
	}
	sort.Strings(names)
	out := make([]jobs.Instance, 0, len(names))
	for _, n := range names {
		out = append(out, tbl[n])
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
	if err := i.r.record("instances", "Run", spec.Name, spec.SIF, nsLabel(spec.Namespace)); err != nil {
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
	//
	// "Taken" is per REGISTRY, as it is on the host: the same name can be
	// running in the account's default registry and free in the ctl's, which
	// is the situation every handed-over tenant passes through.
	if _, ok := i.table(spec.Namespace)[spec.Name]; ok {
		return fmt.Errorf("%w: instance %s is already running", jobs.ErrRefused, spec.Name)
	}
	// The instance that starts and dies. apptainer RETURNS SUCCESS here — it
	// started a container, and what the container then did is between the
	// container and its log — so this writes the log and leaves the table
	// untouched, exactly as the host does.
	if text, dies := i.ExitOnRun[spec.Name]; dies {
		// APPENDED, as apptainer appends: the log holds every previous run of
		// this instance, which is exactly why a caller has to know where this
		// attempt's output starts before it quotes any of it.
		path := filepath.Join(i.logRoot(), spec.Name+".err")
		body := append([]byte(nil), i.files.Content(path)...)
		if text != "" {
			body = append(body, []byte(text+"\n")...)
		}
		i.files.Put(path, body, 0o644)
		return nil
	}
	i.start(spec.Namespace, spec.Name, spec.SIF)
	i.maybeInitdb(spec)
	return nil
}

// maybeInitdb models the one thing the postgres image's entrypoint does that a
// handover depends on: an EMPTY PGDATA is initialised into an empty cluster.
//
// It reads the instance's own binds — `<data>:/var/lib/postgresql/data` and
// `<run>:/var/run/postgresql` — because that is where the two paths are, and
// because a fake that took them from anywhere else could disagree with the
// argv the renderer actually produces. A data directory with anything in it is
// an EXISTING cluster and is left alone, exactly as the entrypoint leaves one.
func (i *FakeInstances) maybeInitdb(spec jobs.InstanceSpec) {
	if i.pg == nil || !strings.HasPrefix(spec.Name, "postgres-") {
		return
	}
	var data, run string
	for _, b := range spec.Binds {
		host, container, ok := strings.Cut(b, ":")
		if !ok {
			continue
		}
		switch strings.TrimSuffix(container, ":ro") {
		case "/var/lib/postgresql/data":
			data = host
		case "/var/run/postgresql":
			run = host
		}
	}
	if data == "" || run == "" {
		return
	}
	i.files.mu.Lock()
	empty := true
	prefix := strings.TrimSuffix(data, "/") + "/"
	for p := range i.files.Files {
		if strings.HasPrefix(p, prefix) {
			empty = false
			break
		}
	}
	i.files.mu.Unlock()
	if !empty {
		return
	}
	// initdb: a cluster with the tenant's database in it and not one row —
	// and with the ENCODING and LOCALES initdb chose.
	//
	// Which is the point. initdb takes them from POSTGRES_INITDB_ARGS when the
	// entrypoint is given any, and from its own environment when it is not, and
	// the two are not the same: a cluster made without them is modelled here as
	// SQL_ASCII/C, which is what an `LC_ALL=C` in whatever ran the take
	// actually produces on this host. A dump restored into that cluster exits
	// 0, keeps every row, and is a different database — so a fake that always
	// initialised a UTF8 cluster would make the one failure this models
	// impossible to write a test for.
	c := jobs.PostgresCensus{
		Tables:   map[string]int64{},
		Encoding: "SQL_ASCII", Collate: "C", Ctype: "C",
		Databases: []string{fakeInitdbDB(spec)}, Roles: []string{fakeInitdbDB(spec)},
	}
	if enc, loc, ok := initdbArgs(spec); ok && !i.IgnoreInitdbArgs {
		c.Encoding, c.Collate, c.Ctype = enc, loc, loc
	}
	i.pg.mu.Lock()
	if i.pg.Contents == nil {
		i.pg.Contents = map[string]jobs.PostgresCensus{}
	}
	i.pg.Contents[run] = c
	i.pg.mu.Unlock()
}

// fakeInitdbDB is the database (and role) the image's entrypoint creates:
// POSTGRES_DB, which the renderer sets to the tenant name.
func fakeInitdbDB(spec jobs.InstanceSpec) string {
	if db := spec.Env["POSTGRES_DB"]; db != "" {
		return db
	}
	return strings.TrimPrefix(spec.Name, "postgres-")
}

// initdbArgs reads `--encoding=` and `--locale=` out of POSTGRES_INITDB_ARGS,
// wherever the caller put it: the container environment, or apptainer's own
// (`APPTAINERENV_POSTGRES_INITDB_ARGS`, which apptainer forwards under the
// bare name). Both spellings reach the entrypoint identically on the host, so
// a fake that understood only one would pass a take that the host fails.
func initdbArgs(spec jobs.InstanceSpec) (encoding, locale string, ok bool) {
	raw := spec.Env["POSTGRES_INITDB_ARGS"]
	if raw == "" {
		raw = spec.ExtraEnv["APPTAINERENV_POSTGRES_INITDB_ARGS"]
	}
	if raw == "" {
		return "", "", false
	}
	for _, f := range strings.Fields(raw) {
		if v, found := strings.CutPrefix(f, "--encoding="); found {
			encoding = v
		}
		if v, found := strings.CutPrefix(f, "--locale="); found {
			locale = v
		}
	}
	return encoding, locale, encoding != "" && locale != ""
}

// LogPaths answers from the table when the instance is running and composes
// the fixture's paths when it is not — the same order the real driver uses,
// and for the same reason: the instance a caller wants the log of is usually
// the one that is no longer there.
func (i *FakeInstances) LogPaths(_ context.Context, name string, opts jobs.ListOptions) (string, string, error) {
	if err := i.r.record("instances", "LogPaths", name, nsLabel(opts.Namespace)); err != nil {
		return "", "", err
	}
	if err := checkInstanceName(name); err != nil {
		return "", "", err
	}
	i.mu.Lock()
	defer i.mu.Unlock()
	if in, ok := i.table(opts.Namespace)[name]; ok && (in.LogOut != "" || in.LogErr != "") {
		return in.LogOut, in.LogErr, nil
	}
	return filepath.Join(i.logRoot(), name+".out"), filepath.Join(i.logRoot(), name+".err"), nil
}

// Stop stops an instance IN ONE REGISTRY, and an instance that is not running
// there is SUCCESS — the interface's rule, because every caller of Stop is a
// step that gets re-run.
//
// It is also exactly how a stop in the WRONG registry looks, which is why the
// handover release no longer treats this success as proof of anything: it
// checks the namespace first and the port afterwards.
func (i *FakeInstances) Stop(_ context.Context, name string, opts jobs.StopOptions) error {
	if err := i.r.record("instances", "Stop", name, nsLabel(opts.Namespace)); err != nil {
		return err
	}
	// The allowlist applies to a stop too, and more than to a start: this is
	// the call that takes something down.
	if err := checkInstanceName(name); err != nil {
		return err
	}
	i.mu.Lock()
	defer i.mu.Unlock()
	if _, ok := i.table(opts.Namespace)[name]; !ok {
		return nil
	}
	i.stop(opts.Namespace, name)
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

// Names lists the CTL registry's running instances, sorted — the assertion a
// test usually wants, without walking the table.
func (i *FakeInstances) Names() []string { return i.namesOf(jobs.NamespaceCtl) }

// AccountNames is Names for the ACCOUNT's default registry: what `apptainer
// instance list` prints for the operator who started the tenant by hand.
func (i *FakeInstances) AccountNames() []string { return i.namesOf(jobs.NamespaceAccountDefault) }

func (i *FakeInstances) namesOf(ns jobs.InstanceNamespace) []string {
	i.mu.Lock()
	defer i.mu.Unlock()
	tbl := i.table(ns)
	out := make([]string, 0, len(tbl))
	for n := range tbl {
		out = append(out, n)
	}
	sort.Strings(out)
	return out
}

// StartInAccount puts an instance in the ACCOUNT's default registry as a
// FIXTURE adjustment — a hand-started store, for a tenant the driver set did
// not know about when it was built. It is FakeInstances.BindInstancePort's
// companion and records no call, for the same reason StopAll does not.
func (i *FakeInstances) StartInAccount(name string) jobs.Instance {
	i.mu.Lock()
	defer i.mu.Unlock()
	return i.start(jobs.NamespaceAccountDefault, name, "fixture.sif")
}

// StopAll clears BOTH instance tables without recording a call: it is a
// FIXTURE adjustment ("this tenant has been stopped"), not something a step
// did, and a test that had to call Stop() for each name would put those calls
// in the trace it is about to assert.
func (i *FakeInstances) StopAll() {
	i.mu.Lock()
	defer i.mu.Unlock()
	for _, ns := range []jobs.InstanceNamespace{jobs.NamespaceCtl, jobs.NamespaceAccountDefault} {
		for name := range i.table(ns) {
			i.stop(ns, name)
		}
	}
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
	// ProbeStatus is what Probe answers, per PATH. A path with no entry
	// answers 200 when the tenant it names is Routed and 404 otherwise, which
	// is what the live gateway does — so a test that seeded Routed gets the
	// honest default and one testing a broken alias seeds the 404 it wants.
	ProbeStatus map[string]int
	// Probes records every path probed, in order.
	Probes []string
}

// Probe answers the recorded status for path.
func (g *FakeGateway) Probe(_ context.Context, path string) (int, error) {
	if err := g.r.record("gateway", "Probe", path); err != nil {
		return 0, err
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	g.Probes = append(g.Probes, path)
	if status, ok := g.ProbeStatus[path]; ok {
		return status, nil
	}
	// `/ragstack/<name>/…` — the only shape the gateway routes per tenant.
	if rest, ok := strings.CutPrefix(path, "/ragstack/"); ok {
		name, _, _ := strings.Cut(rest, "/")
		for _, routed := range g.Routed {
			if routed == name {
				return 200, nil
			}
		}
	}
	return 404, nil
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
	// UID is the account this fake host runs as — the owner of everything it
	// creates, and the answer Stat gives for a path nothing in Owners names.
	UID int
	// Owners are the paths owned by SOMEBODY ELSE: path → uid, and a
	// directory's entry covers everything under it unless a deeper entry says
	// otherwise.
	//
	// It exists for exactly one fact, and it is the fact the second failed
	// handover turned on: postgres compares its data directory's st_uid with
	// its own euid and refuses when they differ, whatever the mode is and
	// whatever the ACL grants. A fake filesystem with no owners at all cannot
	// express "wilke's pgdata, svcbvbrc's postgres", so it cannot test the
	// step that exists to fix it.
	Owners map[string]int
}

// ownerOf is the uid of path on this fake host: the deepest Owners entry that
// covers it, else f.UID. Caller holds the lock.
func (f *FakeFiles) ownerOf(path string) int {
	best, bestLen := f.UID, -1
	for p, uid := range f.Owners {
		if p != path && !strings.HasPrefix(path, strings.TrimSuffix(p, "/")+"/") {
			continue
		}
		if len(p) > bestLen {
			best, bestLen = uid, len(p)
		}
	}
	return best
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
	// OWNERSHIP follows the inode, here as on the host: a rename changes a
	// path, never a uid. Without this the handover's postgres migration would
	// rename the other account's directory aside and the fake would go on
	// answering "that path is the other account's" about the copy that
	// replaced it — which is the one fact the whole step turns on.
	f.renameOwner(from, to)
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

// renameOwner moves every Owners entry at or under `from` to `to`. Caller
// holds the lock.
func (f *FakeFiles) renameOwner(from, to string) {
	if f.Owners == nil {
		return
	}
	prefix := strings.TrimSuffix(from, "/") + "/"
	moved := map[string]int{}
	for p, uid := range f.Owners {
		switch {
		case p == from:
			moved[to] = uid
			delete(f.Owners, p)
		case strings.HasPrefix(p, prefix):
			moved[filepath.Join(to, strings.TrimPrefix(p, prefix))] = uid
			delete(f.Owners, p)
		}
	}
	for p, uid := range moved {
		f.Owners[p] = uid
	}
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
	return f.hasDirLocked(dir)
}

// hasDirLocked is hasDir for a caller that already holds the lock.
func (f *FakeFiles) hasDirLocked(dir string) bool {
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

// Stat is the fake host's lstat: the owner, the mode, the size and whether
// the path is a directory.
//
// A path that is neither a recorded file nor a recorded (or implied) directory
// is fs.ErrNotExist, so a caller telling "not there" from "cannot be read"
// behaves here as it does on the host. The fake has no symlinks, so IsSymlink
// is always false — a fake that claimed one would be claiming a case nothing
// here can create.
func (f *FakeFiles) Stat(_ context.Context, path string) (jobs.FileStat, error) {
	if err := f.r.record("files", "Stat", path); err != nil {
		return jobs.FileStat{}, err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if v, ok := f.Files[path]; ok {
		return jobs.FileStat{UID: f.ownerOf(path), Mode: v.Mode, Size: int64(len(v.Data))}, nil
	}
	if mode, ok := f.Dirs[path]; ok {
		return jobs.FileStat{UID: f.ownerOf(path), Mode: mode, IsDir: true}, nil
	}
	if f.hasDirLocked(path) {
		// A directory nothing recorded but a file under it implies. 0700 is
		// the conservative answer: it is what a postgres data directory
		// carries, and what the handover's ownership questions are asked of.
		return jobs.FileStat{UID: f.ownerOf(path), Mode: 0o700, IsDir: true}, nil
	}
	return jobs.FileStat{}, fmt.Errorf("lstat %s: %w", path, fs.ErrNotExist)
}

// SelfUID is the uid this fake host creates files as.
func (f *FakeFiles) SelfUID(context.Context) (int, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.UID, nil
}

// Sync records the fsync and checks that there is something there to sync.
//
// The in-memory filesystem has no durability to model, so what this pins is
// the CALL: the handover's dump step has to sync the dump and its directory,
// and a fake that accepted any path would let a step that synced the wrong one
// pass. An absent path is fs.ErrNotExist, as the real driver's open would give.
func (f *FakeFiles) Sync(_ context.Context, path string) error {
	if err := f.r.record("files", "Sync", path); err != nil {
		return err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if _, ok := f.Files[path]; ok {
		return nil
	}
	if _, ok := f.Dirs[path]; ok || f.hasDirLocked(path) {
		return nil
	}
	return fmt.Errorf("open %s: %w", path, fs.ErrNotExist)
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
// holdsCredential reports whether any env file on this fake filesystem carries
// the value, and whether there was any env file to ask at all.
//
// The second half is what keeps the answer honest: "no env file mentions this
// key" and "there are no env files" are different statements, and only the
// first is evidence that a tenant would refuse it.
func (f *FakeFiles) holdsCredential(value string) (held, any bool) {
	if value == "" {
		return false, false
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	for path, file := range f.Files {
		if !strings.HasSuffix(path, ".env") {
			continue
		}
		any = true
		if strings.Contains(string(file.Data), value) {
			return true, true
		}
	}
	return false, any
}

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
	// CollectionsByOrigin maps an origin to the tenant API's inventory. An
	// origin with NO entry falls back to the qdrant collections of the same
	// port block — see Collections.
	CollectionsByOrigin map[string][]string
	// qdrant is the store behind that fallback.
	qdrant *FakeQdrant
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
	// CountsByOrigin is the census table, keyed "<origin>/<collection>". An
	// entry that is absent falls back to the qdrant fake's own counts.
	CountsByOrigin map[string]int64
	// RunningByOrigin is the ingest jobs a tenant reports as still running.
	RunningByOrigin map[string][]string
	// KeyStatuses maps a credential VALUE to the status the tenant answers
	// for it; an unseeded value answers 200.
	KeyStatuses map[string]int
	// KeyProbes records every credential proof as "<origin> <fingerprint>" —
	// the fingerprint, never the value, for the reason ServiceAccount gives.
	KeyProbes []string
	// files is the fake filesystem the tenant's key ledger lives on, so that
	// KeyStatus can answer from the ledger rather than from a fixture.
	files *FakeFiles
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
//
// An origin the fixture said nothing about answers with the QDRANT collections
// of the same port block (the api is at <base>, qdrant at <base>+1). That
// fallback is what makes a `restore --as` verifiable against this fake: the
// fresh tenant's origin is on a block nobody could have named in advance, and
// `Qdrant.Recover` registers each collection as it recovers it — so the answer
// is the effect of the steps that just ran rather than a fixture somebody
// remembered to seed. An EXPLICIT entry, empty list included, always wins.
func (a *FakeTenantAPI) Collections(ctx context.Context, origin, apiKey string) ([]string, error) {
	if err := a.r.record("tenantapi", "Collections", origin); err != nil {
		return nil, err
	}
	return a.collections(ctx, origin, apiKey)
}

// collections is Collections without the call record, so that
// CollectionCounts records itself once rather than twice.
func (a *FakeTenantAPI) collections(_ context.Context, origin, _ string) ([]string, error) {
	a.mu.Lock()
	seeded, ok := a.CollectionsByOrigin[origin]
	out := append([]string(nil), seeded...)
	a.mu.Unlock()
	if !ok && a.qdrant != nil {
		if url, ok := qdrantURLForOrigin(origin); ok {
			a.qdrant.mu.Lock()
			out = append([]string(nil), a.qdrant.ByURL[url]...)
			a.qdrant.mu.Unlock()
		}
	}
	sort.Strings(out)
	return out, nil
}

// qdrantURLForOrigin is paths.BlockAt's layout, read backwards: the api is at
// the block's base and qdrant's HTTP port one above it.
func qdrantURLForOrigin(origin string) (string, bool) {
	i := strings.LastIndex(origin, ":")
	if i < 0 {
		return "", false
	}
	port, err := strconv.Atoi(origin[i+1:])
	if err != nil {
		return "", false
	}
	return origin[:i+1] + strconv.Itoa(port+1), true
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

// CollectionCounts is Collections with the seeded counts attached.
//
// The counts come from the same table the qdrant fake answers `Count` out of
// (`QdrantCounts`, keyed `<url>/<collection>`) when the fixture named none of
// its own, so a handover census taken through this fake and a qdrant census
// taken through it agree without a test having to seed the same numbers twice.
// A collection with no number anywhere is -1: "listed, not counted", which is
// the value the real driver records for a tenant that answers no count.
func (a *FakeTenantAPI) CollectionCounts(ctx context.Context, origin, apiKey string) (map[string]int64, error) {
	if err := a.r.record("tenantapi", "CollectionCounts", origin); err != nil {
		return nil, err
	}
	names, err := a.collections(ctx, origin, apiKey)
	if err != nil {
		return nil, err
	}
	out := make(map[string]int64, len(names))
	for _, name := range names {
		out[name] = -1
		a.mu.Lock()
		seeded, ok := a.CountsByOrigin[origin+"/"+name]
		a.mu.Unlock()
		if ok {
			out[name] = seeded
			continue
		}
		if a.qdrant == nil {
			continue
		}
		url, ok := qdrantURLForOrigin(origin)
		if !ok {
			continue
		}
		a.qdrant.mu.Lock()
		if n, ok := a.qdrant.Counts[url+"/"+name]; ok {
			out[name] = n
		}
		a.qdrant.mu.Unlock()
	}
	return out, nil
}

// RunningIngestJobs answers the seeded ids for this origin, and none by
// default: the ordinary fixture is a tenant nobody is ingesting into.
func (a *FakeTenantAPI) RunningIngestJobs(_ context.Context, origin, _ string) ([]string, error) {
	if err := a.r.record("tenantapi", "RunningIngestJobs", origin); err != nil {
		return nil, err
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	return append([]string(nil), a.RunningByOrigin[origin]...), nil
}

// KeyStatus answers whether this fake tenant accepts the credential.
//
// A seeded status wins. Otherwise the answer comes from the LEDGER this host
// actually holds: a value that appears in one of the tenant env files on the
// fake filesystem is accepted, and one that appears in none is refused with a
// 401. That is what makes a credential PROOF meaningful against the fakes —
// `key revoke --restart --prove` rewrote the file, so the fake tenant stops
// accepting the value for the same reason a real one does, rather than because
// a fixture remembered to say so.
//
// A fake with no files at all keeps the old, permissive default: a driver set
// built without a filesystem has no ledger to disagree with.
func (a *FakeTenantAPI) KeyStatus(_ context.Context, origin, apiKey string) (int, error) {
	if err := a.r.record("tenantapi", "KeyStatus", origin); err != nil {
		return 0, err
	}
	a.mu.Lock()
	seeded, ok := a.KeyStatuses[apiKey]
	a.KeyProbes = append(a.KeyProbes, origin+" "+fingerprintFake(apiKey))
	files := a.files
	a.mu.Unlock()
	if ok {
		return seeded, nil
	}
	if files == nil {
		return 200, nil
	}
	held, any := files.holdsCredential(apiKey)
	if !any {
		return 200, nil
	}
	if held {
		return 200, nil
	}
	return 401, nil
}

// fingerprintFake keeps a credential out of the fake's own call log while
// still letting a test tell two probes apart.
func fingerprintFake(key string) string {
	sum := sha256.Sum256([]byte(key))
	return "sha256:" + hex.EncodeToString(sum[:])[:16]
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

// RepairWorktree records the repair; the fake keeps no back-pointers, so the
// only state it can reflect is that the path is now a known worktree.
func (g *FakeGit) RepairWorktree(_ context.Context, mirror, path string) error {
	if err := g.r.record("git", "RepairWorktree", path, mirror); err != nil {
		return err
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	if _, ok := g.Worktrees[path]; !ok {
		g.Worktrees[path] = "repaired"
	}
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

// HeadSHA returns the sha this fake has recorded for the worktree at dir —
// the same Worktrees table AddWorktree populates, and which a test may also
// seed directly (FakeOptions.Worktrees) to stand in for a worktree this fake
// never checked out itself, such as `tenant rebase-worktree`'s pre-existing,
// non-ctl-managed source tree.
func (g *FakeGit) HeadSHA(_ context.Context, dir string) (string, error) {
	if err := g.r.record("git", "HeadSHA", dir); err != nil {
		return "", err
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	if sha, ok := g.Worktrees[dir]; ok {
		return sha, nil
	}
	return "", fmt.Errorf("%w: %s is not a worktree this fake knows", jobs.ErrRefused, dir)
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
	// Contents is what each cluster holds, by RUN DIRECTORY — the one thing
	// that identifies a cluster across this driver's calls. It is not called
	// `Census` because the driver's METHOD is, and Go lets a type have one or
	// the other (the same reason `Readiness` is not `Ready`).
	//
	// It is a model rather than a seed, and the modelling is the point: Dump
	// WRITES the current census into the archive it creates, and Restore READS
	// it back into the target cluster. That is what a dump and a restore
	// actually are, and it is the only way a test of the handover's postgres
	// migration can assert that the tenant came back with what it went down
	// with — the fresh cluster the take initialises starts EMPTY here, exactly
	// as initdb leaves one.
	Contents map[string]jobs.PostgresCensus
	// MissingTools names programs this image does NOT carry, so that a test
	// can produce the one failure a handover cannot discover any later than
	// its release: an image with no initdb, which nothing would notice until
	// a take had already renamed a tenant's cluster aside.
	MissingTools map[string]bool
	// RestoreDrops makes a restore lose rows: table name → how many. It is how
	// a test produces the one failure the row counts exist to catch, a restore
	// that succeeded and moved less than everything.
	RestoreDrops map[string]int64
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
	if !p.pgReady(spec.RunDir) {
		return fmt.Errorf("pg_dump of %s: no response on the socket in %s", spec.DB, spec.RunDir)
	}
	p.Dumps = append(p.Dumps, spec.RunDir+" "+spec.DB+" "+out)
	// The archive CARRIES the census, so that a later Restore can put it into
	// another cluster. A real custom-format dump carries the rows themselves;
	// this is the smallest model of that which lets a test prove a handover
	// moved everything.
	body := "fake pg_dump -Fc of " + spec.DB + "\n"
	c := p.Contents[spec.RunDir]
	body += dumpCensusPrefix + "=size==" + strconv.FormatInt(c.SizeBytes, 10) + "\n"
	for _, name := range sortedKeys(c.Tables) {
		body += dumpCensusPrefix + name + "=" + strconv.FormatInt(c.Tables[name], 10) + "\n"
	}
	p.files.Put(out, []byte(body), 0o640)
	return nil
}

// sortedKeys keeps a fake archive byte-identical between two runs of the same
// dump: a map has no order, and a test that checksums one needs it to.
func sortedKeys(m map[string]int64) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func (p *FakePostgres) Restore(_ context.Context, spec jobs.PostgresSpec, in string) error {
	if err := p.r.record("postgres", "Restore", spec.RunDir, spec.DB, in); err != nil {
		return err
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	if !p.pgReady(spec.RunDir) {
		return fmt.Errorf("pg_restore into %s: no response on the socket in %s", spec.DB, spec.RunDir)
	}
	body := p.files.Content(in)
	if body == nil {
		return fmt.Errorf("the dump to restore is not readable: open %s: %w", in, fs.ErrNotExist)
	}
	// The archive carries the census the dump captured; restoring it is what
	// puts those rows into THIS cluster. A dump this fake did not write has no
	// census in it, which restores as an empty database — the honest model of
	// an archive whose contents are unknown.
	c := dumpCensus(body)
	for name, drop := range p.RestoreDrops {
		if n, ok := c.Tables[name]; ok {
			c.Tables[name] = n - drop
		}
	}
	if p.Contents == nil {
		p.Contents = map[string]jobs.PostgresCensus{}
	}
	// The ROWS come from the archive; the ENCODING and the locales do not. They
	// belong to the cluster initdb made, and a restore leaves them exactly as
	// they are — which is the whole reason a handover has to pin them at initdb
	// time and check them afterwards.
	target := p.Contents[spec.RunDir]
	c.Encoding, c.Collate, c.Ctype = target.Encoding, target.Collate, target.Ctype
	c.Databases, c.Roles = target.Databases, target.Roles
	p.Contents[spec.RunDir] = c
	p.Restores = append(p.Restores, spec.RunDir+" "+spec.DB+" "+in)
	return nil
}

// ToolVersions answers for an image that carries the three tools, unless a
// test says otherwise with MissingTools — which is how the one failure that
// cannot be discovered later (an image with no initdb) is reproduced here.
func (p *FakePostgres) ToolVersions(_ context.Context, spec jobs.PostgresSpec) (map[string]string, error) {
	if err := p.r.record("postgres", "ToolVersions", spec.SIF); err != nil {
		return nil, err
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	out := map[string]string{}
	for _, tool := range handoverTools {
		if p.MissingTools[tool] {
			return nil, fmt.Errorf("%w: %s cannot run %s, which a handover needs", jobs.ErrRefused, spec.SIF, tool)
		}
		out[tool] = tool + " (PostgreSQL) 16.13"
	}
	return out, nil
}

// Census is what the cluster on this run directory holds. An unseeded cluster
// is EMPTY, which is what a freshly initdb'd one is.
func (p *FakePostgres) Census(_ context.Context, spec jobs.PostgresSpec) (jobs.PostgresCensus, error) {
	if err := p.r.record("postgres", "Census", spec.RunDir, spec.DB); err != nil {
		return jobs.PostgresCensus{}, err
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	if !p.pgReady(spec.RunDir) {
		return jobs.PostgresCensus{}, fmt.Errorf("counting the rows of %s: no response on the socket in %s",
			spec.DB, spec.RunDir)
	}
	c, ok := p.Contents[spec.RunDir]
	if !ok {
		return jobs.PostgresCensus{Tables: map[string]int64{}}, nil
	}
	return jobs.PostgresCensus{
		SizeBytes: c.SizeBytes, Tables: copyMapInt64(c.Tables),
		Encoding: c.Encoding, Collate: c.Collate, Ctype: c.Ctype,
		Databases: append([]string(nil), c.Databases...),
		Roles:     append([]string(nil), c.Roles...),
	}, nil
}

// dumpCensusPrefix marks the census this fake writes into an archive.
const dumpCensusPrefix = "census:"

// dumpCensus reads the census back out of a fake archive. An archive without
// one is an empty database.
func dumpCensus(body []byte) jobs.PostgresCensus {
	c := jobs.PostgresCensus{Tables: map[string]int64{}}
	for _, line := range strings.Split(string(body), "\n") {
		rest, ok := strings.CutPrefix(line, dumpCensusPrefix)
		if !ok {
			continue
		}
		name, count, ok := strings.Cut(rest, "=")
		if !ok {
			continue
		}
		n, err := strconv.ParseInt(count, 10, 64)
		if err != nil {
			continue
		}
		if name == "=size=" {
			c.SizeBytes = n
			continue
		}
		c.Tables[name] = n
	}
	return c
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

// copyMapCensus deep-copies the seeded cluster contents, so that a fixture's
// map is not mutated by the restore this fake models.
func copyMapCensus(m map[string]jobs.PostgresCensus) map[string]jobs.PostgresCensus {
	out := make(map[string]jobs.PostgresCensus, len(m))
	for k, v := range m {
		out[k] = jobs.PostgresCensus{
			SizeBytes: v.SizeBytes, Tables: copyMapInt64(v.Tables),
			Encoding: v.Encoding, Collate: v.Collate, Ctype: v.Ctype,
			Databases: append([]string(nil), v.Databases...),
			Roles:     append([]string(nil), v.Roles...),
		}
	}
	return out
}
