package jobs

import (
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"syscall"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/gateway"
	"github.com/ragstack/ragstack/internal/ctl/model"
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
