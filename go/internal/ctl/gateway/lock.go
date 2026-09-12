package gateway

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"syscall"
)

// LockName is the file every WRITING gateway operation holds an exclusive
// flock on for its whole duration.
//
// Publishing is a sequence of steps — write gen-<N>, stage, `nginx -t`, move
// `current`, HUP, probe, maybe revert — whose intermediate states are not
// safe to interleave. Two applies running at once would each read the other's
// half-finished `current`, and the loser's revert would put the pointer back
// on a generation the winner had already left. One writer at a time is the
// only invariant that makes txn.json readable as a history.
//
// It is flock(2), not a pidfile: the lock is released by the KERNEL when the
// holder exits, so a killed publish does not leave a lock nobody can clear.
const LockName = ".lock"

// ErrLocked is "another gateway operation is in progress". It wraps ErrRefused
// so the CLI exits 3 and the API answers 409 — a lock conflict is a refusal
// with a remedy (wait, or find the other operator), not a host failure.
var ErrLocked = fmt.Errorf("%w: another gateway publish, rollback or repair holds %s; "+
	"wait for it to finish rather than running two at once", ErrRefused, LockName)

// lockHandle is a held flock. Always `defer l.unlock()`.
type lockHandle struct{ f *os.File }

// lock takes the exclusive lock, without blocking.
//
// Non-blocking on purpose: an operator who runs a second `gateway apply` while
// the first is in its probe budget wants to be TOLD, not to have their
// terminal sit there and then publish a generation whose plan they saw ten
// minutes ago.
func (s State) lock() (*lockHandle, error) {
	if err := os.MkdirAll(s.Dir, 0o2770); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(filepath.Join(s.Dir, LockName), os.O_CREATE|os.O_RDWR, 0o660)
	if err != nil {
		return nil, err
	}
	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		_ = f.Close()
		if errors.Is(err, syscall.EWOULDBLOCK) {
			return nil, ErrLocked
		}
		return nil, fmt.Errorf("locking %s: %w", filepath.Join(s.Dir, LockName), err)
	}
	return &lockHandle{f: f}, nil
}

// unlock releases it. Closing the descriptor would be enough — the kernel drops
// the lock with the last close of the open file description — but the explicit
// LOCK_UN says so at the call site.
func (l *lockHandle) unlock() {
	if l == nil || l.f == nil {
		return
	}
	_ = syscall.Flock(int(l.f.Fd()), syscall.LOCK_UN)
	_ = l.f.Close()
	l.f = nil
}
