package jobs

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
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

// Locks locates one deployment's lock files.
type Locks struct {
	dir        string // <CtlStateDir>/locks
	gatewayDir string // <CtlStateDir>/gateway — the gateway package's own dir
}

// NewLocks derives the lock file locations from the roots.
func NewLocks(roots paths.Roots) Locks {
	return Locks{
		dir:        filepath.Join(roots.CtlStateDir, "locks"),
		gatewayDir: gateway.NewState(roots).Dir,
	}
}

// Path is the file backing one lock. The tenant lock is per tenant, so a job
// on `dev` and a job on `demo` do not serialize against each other.
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
	return filepath.Join(l.dir, base)
}

// LockSet is a set of held locks. Always `defer s.Release()`.
type LockSet struct {
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
			return nil, fmt.Errorf("opening %s: %w", p, err)
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
