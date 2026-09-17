package jobs

import (
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/gateway"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
)

func TestLocksAreTakenInFleetOrderAndReleasedInReverse(t *testing.T) {
	l := NewLocks(testRoots(t.TempDir()))
	// Deliberately scrambled: the caller's order must not matter.
	want := []model.LockName{model.LockImages, model.LockTenant, model.LockRegistry}
	set, err := l.Take(want, "dev", LockHolder{JobID: "01AAAA", PID: os.Getpid()}, time.Now())
	if err != nil {
		t.Fatalf("Take: %v", err)
	}
	defer set.Release()

	got := set.Names()
	expect := []model.LockName{model.LockRegistry, model.LockTenant, model.LockImages}
	if !reflect.DeepEqual(got, expect) {
		t.Fatalf("lock order = %v, want %v", got, expect)
	}
	// The tenant lock is per tenant, so another tenant is not blocked.
	otherTenant, err := l.Take([]model.LockName{model.LockTenant}, "demo", LockHolder{JobID: "01BBBB"}, time.Now())
	if err != nil {
		t.Fatalf("the tenant lock is not per tenant: %v", err)
	}
	otherTenant.Release()
}

// TestLockedErrorNamesTheHolder: a contender is told WHO holds the lock, from
// the {job_id, pid, since} the taker wrote into the file.
func TestLockedErrorNamesTheHolder(t *testing.T) {
	l := NewLocks(testRoots(t.TempDir()))
	since := time.Date(2026, 9, 14, 9, 0, 0, 0, time.UTC)
	holder := LockHolder{JobID: "01JQ8ZKE7R9X4M2V6T5S3N1P0B", PID: 4242, Since: since.Format(time.RFC3339)}
	first, err := l.Take([]model.LockName{model.LockRegistry, model.LockTenant}, "dev", holder, since)
	if err != nil {
		t.Fatal(err)
	}

	_, err = l.Take([]model.LockName{model.LockTenant}, "dev", LockHolder{JobID: "01OTHER", PID: 1}, time.Now())
	if !errors.Is(err, ErrLocked) {
		t.Fatalf("a contended lock = %v, want ErrLocked", err)
	}
	var le *LockedError
	if !errors.As(err, &le) {
		t.Fatalf("the error carries no holder: %v", err)
	}
	if le.Holder.JobID != holder.JobID || le.Holder.PID != 4242 || le.Holder.Since != holder.Since {
		t.Fatalf("holder = %+v, want %+v", le.Holder, holder)
	}
	if le.Name != model.LockTenant {
		t.Fatalf("the error names lock %q, want tenant", le.Name)
	}

	// A partial acquisition must unwind: the registry lock the failed Take
	// managed to get first has to be free again.
	first.Release()
	again, err := l.Take([]model.LockName{model.LockRegistry, model.LockTenant}, "dev", holder, time.Now())
	if err != nil {
		t.Fatalf("after Release: %v", err)
	}
	again.Release()
}

// TestGatewayLockIsTheGatewayPackagesFile is the one that matters for
// correctness across packages: a `gateway apply` JOB and a bare `ragstack-ctl
// gateway apply` must contend for the SAME inode, or they interleave.
func TestGatewayLockIsTheGatewayPackagesFile(t *testing.T) {
	roots := testRoots(t.TempDir())
	l := NewLocks(roots)
	want := filepath.Join(gateway.NewState(roots).Dir, gateway.LockName)
	if got := l.Path(model.LockGateway, ""); got != want {
		t.Fatalf("gateway lock path = %s, want the gateway package's %s", got, want)
	}

	set, err := l.Take([]model.LockName{model.LockGateway}, "", LockHolder{JobID: "01GW", PID: os.Getpid()}, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	// Stand in for the gateway package's own lock(): flock the same file.
	f, err := os.OpenFile(want, os.O_CREATE|os.O_RDWR, 0o660)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = f.Close() }()
	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); !errors.Is(err, syscall.EWOULDBLOCK) {
		t.Fatalf("a bare gateway publish could take the lock a job holds: %v", err)
	}
	set.Release()
	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		t.Fatalf("after the job released it, a gateway publish still cannot lock: %v", err)
	}
	_ = syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
}

// TestReleasedLockFileNamesNobody: a released lock must not leave a holder
// record behind, or the next contender blames the wrong job.
func TestReleasedLockFileNamesNobody(t *testing.T) {
	l := NewLocks(testRoots(t.TempDir()))
	set, err := l.Take([]model.LockName{model.LockManifest}, "", LockHolder{JobID: "01AAAA", PID: 7}, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	set.Release()
	if h := readHolder(l.Path(model.LockManifest, "")); h.JobID != "" || h.PID != 0 {
		t.Fatalf("a released lock still names %+v", h)
	}
}

// TestTheFleetWideLocksAreSharedBetweenTheTwoAccounts is the regression for
// the handover's one real race.
//
// The two accounts a handover involves run with DIFFERENT ctl state
// directories — they have to, because the daemon's jobs.db belongs to the
// service account and an owner-side `--direct` job cannot write it. When the
// registry, manifest and tenant locks were derived from that directory, the
// two accounts held two disjoint sets of files and serialised nothing: wilke's
// `handover --abandon` and svcbvbrc's `tenant restart` could hold "the dev
// lock" at the same instant, and the restart's registry write would land on a
// row the abandon had already flipped to `manual`.
func TestTheFleetWideLocksAreSharedBetweenTheTwoAccounts(t *testing.T) {
	ragRoot := t.TempDir()
	// What the two accounts really differ in: CTL_STATE_DIR (and CTL_CONFIG_DIR,
	// which does not reach the locks at all).
	daemon := NewLocks(paths.NewRoots(ragRoot, paths.Overrides{
		CtlStateDir: filepath.Join(ragRoot, "data", "ctl"),
	}))
	owner := NewLocks(paths.NewRoots(ragRoot, paths.Overrides{
		CtlStateDir: filepath.Join(ragRoot, "data", "ctl-selftest"),
	}))

	for _, name := range []model.LockName{model.LockRegistry, model.LockManifest, model.LockTenant} {
		a, b := daemon.Path(name, "dev"), owner.Path(name, "dev")
		if a != b {
			t.Errorf("%s lock: the daemon uses %s and the owner uses %s — they serialise nothing", name, a, b)
		}
		if !strings.Contains(a, SharedLockDir) {
			t.Errorf("%s lock is at %s, which is not the shared directory", name, a)
		}
	}
	// `images` is deliberately NOT shared: it guards one installation's image
	// staging, not a fact about the deployment.
	if daemon.Path(model.LockImages, "") == owner.Path(model.LockImages, "") {
		t.Error("the images lock became shared; it guards this ctl's own staging")
	}

	// And it really serialises: a lock held through one Locks refuses through
	// the other, naming the holder.
	now := time.Now()
	held, err := daemon.Take([]model.LockName{model.LockTenant}, "dev",
		LockHolder{JobID: "01ARZ3NDEKTSV4RRFFQ69G5FAV", PID: 4242}, now)
	if err != nil {
		t.Fatalf("the daemon could not take the tenant lock: %v", err)
	}
	defer held.Release()

	_, err = owner.Take([]model.LockName{model.LockTenant}, "dev", LockHolder{JobID: "other", PID: 1}, now)
	if !errors.Is(err, ErrLocked) {
		t.Fatalf("the other account took a held tenant lock: %v", err)
	}
	// The refusal has to name WHO, out of the lock file, or an operator has
	// nothing to go on.
	for _, want := range []string{"01ARZ3NDEKTSV4RRFFQ69G5FAV", "4242"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("the refusal does not name %q: %v", want, err)
		}
	}
	// A DIFFERENT tenant is not serialised against it.
	other, err := owner.Take([]model.LockName{model.LockTenant}, "demo", LockHolder{JobID: "x"}, now)
	if err != nil {
		t.Fatalf("a job on another tenant was refused: %v", err)
	}
	other.Release()

	// The shared directory is setgid, so whichever account creates it first
	// leaves one the other can write.
	fi, err := os.Stat(filepath.Join(ragRoot, "data", "tenants", SharedLockDir))
	if err != nil {
		t.Fatalf("stat the shared lock dir: %v", err)
	}
	if fi.Mode()&os.ModeSetgid == 0 {
		t.Errorf("the shared lock dir is %v: without setgid the second account cannot write into it", fi.Mode())
	}
	if fi.Mode().Perm()&0o070 != 0o070 {
		t.Errorf("the shared lock dir is %v: the group needs rwx or the second account cannot create a lock file",
			fi.Mode())
	}
	// And the lock FILE: the umask takes the group-write bit off the mode
	// Take asks for, and a file the other account cannot open O_RDWR is a
	// lock that answers EACCES instead of serialising.
	lf, err := os.Stat(daemon.Path(model.LockTenant, "dev"))
	if err != nil {
		t.Fatalf("stat the tenant lock file: %v", err)
	}
	if lf.Mode().Perm()&0o060 != 0o060 {
		t.Errorf("the tenant lock file is %v, want group rw", lf.Mode())
	}
}

// TestAFixtureDaemonsLocksStayOutOfTheDeploymentTree is the regression for the
// side effect the shared lock directory caused the first time it existed: a
// `--fake-drivers` conformance run left fifteen lock files in
// /rag/data/tenants, named after tenants the host has never had.
//
// The shared directory is under DataDir on purpose — that is what makes two
// accounts contend for one tenant — so the escape is an override, and the one
// process that takes it is the daemon that serves a fleet which does not
// exist.
func TestAFixtureDaemonsLocksStayOutOfTheDeploymentTree(t *testing.T) {
	scratch := t.TempDir()
	roots := paths.NewRoots("/rag", paths.Overrides{
		CtlStateDir:   filepath.Join(scratch, "state"),
		SharedLockDir: filepath.Join(scratch, "state", "locks"),
	})
	l := NewLocks(roots)
	for _, name := range []model.LockName{model.LockRegistry, model.LockManifest, model.LockTenant} {
		p := l.Path(name, "ctlfixture")
		if strings.HasPrefix(p, "/rag/") {
			t.Errorf("%s lock is at %s: a fixture daemon must not write into the deployment tree", name, p)
		}
	}
	// And without the override it IS under the deployment's data dir, which is
	// the behaviour the two-account handover needs.
	def := NewLocks(paths.NewRoots("/rag", paths.Overrides{CtlStateDir: filepath.Join(scratch, "state")}))
	if got := def.Path(model.LockTenant, "dev"); got != "/rag/data/tenants/"+SharedLockDir+"/dev.lock" {
		t.Errorf("the default tenant lock is %s, which the other account would not contend for", got)
	}
}
