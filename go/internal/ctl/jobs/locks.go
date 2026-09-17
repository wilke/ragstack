package jobs

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"syscall"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/gateway"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
)

// The single fleet lock order (plan: "registry → manifest → tenant → gateway
// → images"). Two writers that take the locks they need in this order can
// never deadlock, because a holder of a later lock never waits for an earlier
// one. The --direct CLI takes the SAME files as the daemon — flock(2) is a
// property of the file, not of a process's memory, which is the only reason
// `ragstack-ctl --direct start dev` and a daemon job cannot interleave.
//
// The lock files live under <CtlStateDir>/locks/, EXCEPT the gateway lock:
// that one is gateway.LockName inside the gateway state dir, the very file
// internal/ctl/gateway takes for a publish. A `gateway apply` job and a bare
// `ragstack-ctl gateway apply` must exclude each other, and they only do if
// they contend for one inode.

// LockHolder is the {job_id, pid, since} record the taker writes into the
// lock file so a blocked taker can say WHO holds it rather than just "busy".
// It is advisory information, not the lock: the lock is the flock.
type LockHolder struct {
	JobID string `json:"job_id"`
	PID   int    `json:"pid"`
	Since string `json:"since"`
}

// LockedError is ErrLocked with the holder attached; the API layer puts
// Holder into the error body's `extra`.
type LockedError struct {
	Name   model.LockName
	Path   string
	Holder LockHolder
}

func (e *LockedError) Error() string {
	who := "another operation"
	if e.Holder.JobID != "" {
		who = "job " + e.Holder.JobID
	}
	if e.Holder.PID != 0 {
		who = fmt.Sprintf("%s (pid %d)", who, e.Holder.PID)
	}
	if e.Holder.Since != "" {
		who = fmt.Sprintf("%s since %s", who, e.Holder.Since)
	}
	return fmt.Sprintf("locked: %s holds the %s lock (%s); wait for it rather than running two at once",
		who, e.Name, e.Path)
}

// Unwrap makes errors.Is(err, ErrLocked) true.
func (e *LockedError) Unwrap() error { return ErrLocked }

// SharedLockDir is the directory the FLEET-WIDE locks live in:
// `<DataDir>/.ctl-locks`, which on this deployment is
// `/rag/data/tenants/.ctl-locks`.
//
// It is not under `CtlStateDir`, and that is the whole point. The two accounts
// a handover involves run with DIFFERENT state directories — they have to: the
// daemon's `jobs.db` belongs to the service account, so an owner-side
// `--direct` job runs against a scratch `CTL_STATE_DIR` of its own. Locks
// derived from that directory are two disjoint sets of files, and two disjoint
// sets of files serialise nothing: wilke's `handover --abandon` and svcbvbrc's
// `tenant restart` could both hold "the tenant lock" at the same instant, on
// the same tenant, and the loser of that race is the registry (the restart
// frees the ports, the abandon flips the row to `manual`, the restart's own
// registry write lands on a row that no longer describes what it started).
//
// `<DataDir>` is the right shared root because it is one of the three the ACL
// grant covers (`ragstack-ctl fleet grant`): the service account holds rwx on
// it, the owner owns it, and both can therefore create and flock a file there.
// The directory is dot-prefixed so nothing that lists tenants reads it as one,
// and 2770 so the group is inherited by whichever account creates it first.
const SharedLockDir = paths.SharedLockDirName

// Locks locates one deployment's lock files.
type Locks struct {
	// shared holds the locks BOTH accounts must contend for: registry,
	// manifest and tenant.
	shared string // <DataDir>/.ctl-locks
	// dir holds the locks that are one installation's own (images).
	dir        string // <CtlStateDir>/locks
	gatewayDir string // <CtlStateDir>/gateway — the gateway package's own dir
}

// NewLocks derives the lock file locations from the roots.
func NewLocks(roots paths.Roots) Locks {
	return Locks{
		shared:     roots.SharedLockDir,
		dir:        filepath.Join(roots.CtlStateDir, "locks"),
		gatewayDir: gateway.NewState(roots).Dir,
	}
}

// Path is the file backing one lock. The tenant lock is per tenant, so a job
// on `dev` and a job on `demo` do not serialize against each other.
//
// Three of the five live in the SHARED directory (see SharedLockDir): the
// registry, the manifest and the tenant are what two accounts operating the
// same fleet have to contend for. The gateway keeps its own file in the
// gateway state (the gateway package owns that name), and `images` stays in
// the installation's own directory: it guards this ctl's image staging, not a
// fact about the deployment two accounts share.
func (l Locks) Path(name model.LockName, tenant string) string {
	if name == model.LockGateway {
		return filepath.Join(l.gatewayDir, gateway.LockName)
	}
	base := string(name) + ".lock"
	if name == model.LockTenant {
		if tenant == "" {
			base = "tenant.lock"
		} else {
			base = tenant + ".lock"
		}
	}
	if l.sharedLock(name) {
		return filepath.Join(l.shared, base)
	}
	return filepath.Join(l.dir, base)
}

// sharedLock reports whether name is one of the fleet-wide locks.
func (l Locks) sharedLock(name model.LockName) bool {
	switch name {
	case model.LockRegistry, model.LockManifest, model.LockTenant:
		return l.shared != ""
	}
	return false
}

// LockSet is a set of held locks. Always `defer s.Release()`.
//
// Release is idempotent and synchronised: the engine can reach it from the
// run goroutine and from a Cancel at the same moment, and a second Release
// that closed an already-closed descriptor would unlock whatever file the
// kernel had since handed that number to.
type LockSet struct {
	mu    sync.Mutex
	held  []*heldLock
	names []model.LockName
	since time.Time
}

type heldLock struct {
	name model.LockName
	path string
	f    *os.File
}

// Take acquires every lock in want, in model.LockOrder regardless of the
// order given, writing holder into each file. It does NOT block: an operator
// who runs into a held lock wants to be told who holds it, not to have their
// terminal sit there. On failure every lock already taken is released, so the
// caller never has to unwind a partial set.
func (l Locks) Take(want []model.LockName, tenant string, holder LockHolder, now time.Time) (*LockSet, error) {
	ordered := orderLocks(want)
	if err := os.MkdirAll(l.dir, 0o2770); err != nil {
		return nil, fmt.Errorf("creating the lock dir: %w", err)
	}
	// Created by whichever account gets there first, and then made writable by
	// the other one EXPLICITLY.
	//
	// Two things make that necessary. Go's FileMode does not carry the Unix
	// setgid bit in its low octal digits — `0o2770` there is plain `0770` —
	// and the process umask (0027 on coconut) then takes the group-write bit
	// off whatever is left. A directory created as `drwxr-x---` is one the
	// second account cannot create a lock file in, so the locks would be
	// "shared" in name and unusable in fact. The chmod is best effort: the
	// account that did NOT create it cannot chmod it, and does not need to.
	if l.shared != "" {
		if err := os.MkdirAll(l.shared, 0o770); err != nil {
			return nil, fmt.Errorf("creating the shared lock dir %s (both accounts must be able to write it, "+
				"which is what `ragstack-ctl fleet grant` arranges): %w", l.shared, err)
		}
		_ = os.Chmod(l.shared, 0o770|os.ModeSetgid)
	}
	set := &LockSet{since: now}
	for _, name := range ordered {
		p := l.Path(name, tenant)
		if err := os.MkdirAll(filepath.Dir(p), 0o2770); err != nil {
			set.Release()
			return nil, fmt.Errorf("creating %s: %w", filepath.Dir(p), err)
		}
		f, err := os.OpenFile(p, os.O_CREATE|os.O_RDWR, 0o660)
		if err != nil {
			set.Release()
			if l.sharedLock(name) && errors.Is(err, os.ErrPermission) {
				return nil, fmt.Errorf("opening the shared %s lock %s: %w — both accounts must be able to write "+
					"it. `chmod 660` that file and `chmod 2770` its directory as its owner, or run "+
					"`ragstack-ctl fleet grant` to put the ACL back", name, p, err)
			}
			return nil, fmt.Errorf("opening %s: %w", p, err)
		}
		// Group-writable, explicitly: the umask takes that bit off the mode
		// above, and a lock file the other account cannot open O_RDWR is a
		// lock that refuses with EACCES instead of serialising. It fails
		// harmlessly for the account that did not create the file.
		if l.sharedLock(name) {
			_ = f.Chmod(0o660)
		}
		if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
			_ = f.Close()
			set.Release()
			if errors.Is(err, syscall.EWOULDBLOCK) {
				return nil, &LockedError{Name: name, Path: p, Holder: readHolder(p)}
			}
			return nil, fmt.Errorf("locking %s: %w", p, err)
		}
		h := holder
		if h.Since == "" {
			h.Since = now.UTC().Format(time.RFC3339)
		}
		writeHolder(f, h)
		set.held = append(set.held, &heldLock{name: name, path: p, f: f})
		set.names = append(set.names, name)
	}
	return set, nil
}

// Names are the locks held, in LockOrder — what job.lock.order records.
func (s *LockSet) Names() []model.LockName {
	if s == nil {
		return nil
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]model.LockName, len(s.names))
	copy(out, s.names)
	return out
}

// Release drops the locks in REVERSE order (images → gateway → tenant →
// manifest → registry): a waiter never sees an earlier lock free while a
// later one it also needs is still held.
func (s *LockSet) Release() {
	if s == nil {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	for i := len(s.held) - 1; i >= 0; i-- {
		h := s.held[i]
		// Clear the holder record first: the flock is gone the moment the
		// descriptor closes, and a stale {job_id,pid} left behind would make
		// the next contender name the wrong culprit.
		_ = h.f.Truncate(0)
		_ = syscall.Flock(int(h.f.Fd()), syscall.LOCK_UN)
		_ = h.f.Close()
	}
	s.held = nil
	s.names = nil
}

// Free reports whether every lock in want is currently UNHELD, by trying a
// non-blocking flock on each and dropping it again. It is the second half of
// reconcile's liveness test: a worker that is still running holds its flocks,
// so a set that can be taken says the worker is gone no matter what the pid
// table claims. flock(2) is a property of the open file DESCRIPTION, not of a
// process, so this answers honestly even when the caller is the daemon that
// would otherwise be asking about itself.
//
// It fails CLOSED: a lock file we cannot open is reported as held, because
// "we could not tell" must never read as "the worker is dead".
func (l Locks) Free(want []model.LockName, tenant string) bool {
	for _, name := range orderLocks(want) {
		p := l.Path(name, tenant)
		f, err := os.OpenFile(p, os.O_RDWR, 0o660)
		if err != nil {
			if errors.Is(err, os.ErrNotExist) {
				continue // no file, so nothing holds it
			}
			return false
		}
		err = syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
		if err != nil {
			_ = f.Close()
			return false
		}
		_ = syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
		_ = f.Close()
	}
	return true
}

// orderLocks sorts want into LockOrder and drops duplicates and unknowns.
func orderLocks(want []model.LockName) []model.LockName {
	rank := map[model.LockName]int{}
	for i, n := range model.LockOrder {
		rank[n] = i
	}
	seen := map[model.LockName]bool{}
	var out []model.LockName
	for _, n := range want {
		if _, ok := rank[n]; !ok || seen[n] {
			continue
		}
		seen[n] = true
		out = append(out, n)
	}
	sort.Slice(out, func(i, j int) bool { return rank[out[i]] < rank[out[j]] })
	return out
}

func writeHolder(f *os.File, h LockHolder) {
	b, err := json.Marshal(h)
	if err != nil {
		return
	}
	_ = f.Truncate(0)
	if _, err := f.WriteAt(append(b, '\n'), 0); err != nil {
		return
	}
	_ = f.Sync()
}

// readHolder reads the holder record of a lock we could NOT take. An empty
// or unparsable file is not an error — a lock taken by something that did not
// write a record is still a lock, we just cannot name it.
func readHolder(path string) LockHolder {
	b, err := os.ReadFile(path)
	if err != nil {
		return LockHolder{}
	}
	var h LockHolder
	if err := json.Unmarshal(b, &h); err != nil {
		return LockHolder{}
	}
	return h
}
