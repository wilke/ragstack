package drivers

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"

	"github.com/ragstack/ragstack/internal/ctl/hostfacts"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
)

// RealProc is the /proc surface: which ports are listening, who owns them, and
// the identity-checked signal that stops a hand-started tenant.
//
// It reads /proc directly rather than running `ss`, `lsof` or `pgrep`. Those
// would be three more programs to allowlist, two of them absent on a minimal
// host, and `pkill`-shaped matching by name is precisely the mistake this
// driver exists to avoid: the ctl signals a pid it has verified belongs to the
// tenant it was asked about, never every process whose command line looks
// familiar.
type RealProc struct {
	// Listeners is hostfacts' listening-socket table, injected so that the
	// driver and `doctor` answer port questions from ONE parser. A host where
	// the two disagreed would refuse a port allocation the doctor calls free.
	Listeners func() ([]hostfacts.Listener, error)
	// ProcRoot returns /proc. It is a FUNCTION, not a string, for one reason:
	// the pid-reuse guard can only be exercised by a /proc that CHANGES
	// between the two reads Signal makes of it, and a fixture tree that a test
	// swaps under the driver is the only way to produce on demand the race the
	// guard exists to catch. Nil means the real /proc.
	ProcRoot func() string
	// Kill is syscall.Kill, injected only so a test can observe the signal
	// that a passing identity check would have delivered.
	Kill func(pid int, sig syscall.Signal) error
}

var _ jobs.Proc = (*RealProc)(nil)

// dir is /proc/<pid>, resolved at every access rather than once per call: see
// ProcRoot.
func (p *RealProc) dir(pid int) string {
	root := "/proc"
	if p.ProcRoot != nil {
		root = p.ProcRoot()
	}
	return filepath.Join(root, strconv.Itoa(pid))
}

func (p *RealProc) kill(pid int, sig syscall.Signal) error {
	if p.Kill != nil {
		return p.Kill(pid, sig)
	}
	return syscall.Kill(pid, sig)
}

func (p *RealProc) listeners() ([]hostfacts.Listener, error) {
	if p.Listeners == nil {
		return nil, fmt.Errorf("%w: the proc driver has no listener source", jobs.ErrRefused)
	}
	return p.Listeners()
}

// Listening reports whether anything holds a LISTEN socket on port.
//
// Any bind counts — loopback, wildcard or a specific address, over IPv4 or
// IPv6. The question a caller asks is "can this tenant's service take this
// port", and the answer is no whichever address the current holder chose.
func (p *RealProc) Listening(_ context.Context, port int) (bool, error) {
	ls, err := p.listeners()
	if err != nil {
		return false, err
	}
	for _, l := range ls {
		if l.Port == port {
			return true, nil
		}
	}
	return false, nil
}

// Owner is the pid and uid behind the LISTEN socket on port.
//
// A socket whose owning process this account cannot read comes back (0, 0,
// nil), and so does an unbound port: an unprivileged reader sees THAT a port
// is taken without seeing by whom, and that difference is what tells an
// operator whether a collision is the ctl's to fix or somebody else's to look
// at. Neither case is an error — "nothing is listening" is a fact.
func (p *RealProc) Owner(_ context.Context, port int) (int, int, error) {
	ls, err := p.listeners()
	if err != nil {
		return 0, 0, err
	}
	for _, l := range ls {
		if l.Port != port {
			continue
		}
		if l.Pid <= 0 || l.UID < 0 {
			// Either the socket could not be attributed to a process at all,
			// or the process was found but its uid could not be read (it
			// exited under the scan). Both are "not attributable", and
			// reporting a uid of 0 for the second would read as root.
			return 0, 0, nil
		}
		return l.Pid, l.UID, nil
	}
	return 0, 0, nil
}

// signals is the signal allowlist. Three signals, all of them a request to a
// process rather than an execution: TERM and INT ask a server to shut down,
// HUP asks it to reload. SIGKILL is absent on purpose — the ctl never takes
// the decision to lose a store's in-flight writes, and the ES graceful-stop
// check in the selftest exists to prove nothing does.
var signals = map[string]syscall.Signal{
	"TERM": syscall.SIGTERM,
	"INT":  syscall.SIGINT,
	"HUP":  syscall.SIGHUP,
}

// Signal sends sig to pid after proving the process is the one the caller
// meant.
//
// The proof is three facts, in this order:
//
//  1. /proc/<pid> is readable. An unreadable one belongs to another account,
//     and a control plane that signalled across accounts would be doing
//     exactly what the plan forbids.
//  2. The cwd is the tenant's worktree (or inside it) and the cmdline mentions
//     what the caller expected ("uvicorn"). A pid file is a file: whoever can
//     write it can name any pid on the host, so its contents are a hint that
//     has to be checked, never an instruction.
//  3. The start time read BEFORE the identity check still matches immediately
//     before the kill. Between reading a pidfile and sending a signal the
//     process may exit and the kernel may hand its pid to something else;
//     without this check, a stop that lost a race would deliver SIGTERM to
//     whatever inherited the number.
func (p *RealProc) Signal(_ context.Context, pid int, wantCwd, wantCmd, sig string) error {
	signal, ok := signals[strings.ToUpper(strings.TrimPrefix(sig, "SIG"))]
	if !ok {
		return fmt.Errorf("%w: %q is not a signal this driver sends (TERM, INT and HUP only)", jobs.ErrRefused, sig)
	}
	if pid <= 1 {
		return fmt.Errorf("%w: %d is not a pid the ctl signals", jobs.ErrRefused, pid)
	}
	// The start time comes first: a check made after the identity proof could
	// not tell a pid that was reused BEFORE the proof from one reused after.
	start, err := p.startTime(p.dir(pid))
	if err != nil {
		return err
	}
	if err := p.checkIdentity(p.dir(pid), pid, wantCwd, wantCmd); err != nil {
		return err
	}
	// Re-read immediately before the signal. The window is now as small as
	// this process can make it.
	again, err := p.startTime(p.dir(pid))
	if err != nil {
		return err
	}
	if again != start {
		return fmt.Errorf("%w: pid %d was replaced between the identity check and the signal (start time %d became %d)",
			jobs.ErrRefused, pid, start, again)
	}
	return p.kill(pid, signal)
}

// startTime is field 22 of /proc/<pid>/stat, the process's start time in clock
// ticks since boot. With the pid it is a unique process identity: the kernel
// reuses pids, it does not rewind the clock.
func (p *RealProc) startTime(dir string) (uint64, error) {
	b, err := os.ReadFile(filepath.Join(dir, "stat"))
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return 0, fmt.Errorf("%w: there is no process %s", jobs.ErrRefused, filepath.Base(dir))
		}
		return 0, fmt.Errorf("%w: %s is not readable by this account: %v", jobs.ErrRefused, filepath.Join(dir, "stat"), err)
	}
	// Split AFTER the last ')': field 2 is the executable name in parentheses
	// and may itself contain spaces and parentheses, which is why a plain
	// Fields() over this file has been a bug in every language that has one.
	i := bytes.LastIndexByte(b, ')')
	if i < 0 {
		return 0, fmt.Errorf("%w: %s/stat is not in the format this driver parses", jobs.ErrRefused, dir)
	}
	// fields[0] is field 3 (state), so field N is fields[N-3].
	fields := strings.Fields(string(b[i+1:]))
	const startIdx = 22 - 3
	if len(fields) <= startIdx {
		return 0, fmt.Errorf("%w: %s/stat has %d fields after the comm; the start time is not among them", jobs.ErrRefused, dir, len(fields))
	}
	start, err := strconv.ParseUint(fields[startIdx], 10, 64)
	if err != nil {
		return 0, fmt.Errorf("%w: %s/stat field 22 is %q, not a number", jobs.ErrRefused, dir, fields[startIdx])
	}
	return start, nil
}

// checkIdentity is rule 1 and 2 of Signal.
func (p *RealProc) checkIdentity(dir string, pid int, wantCwd, wantCmd string) error {
	cwd, err := os.Readlink(filepath.Join(dir, "cwd"))
	if err != nil {
		// /proc/<pid>/cwd is readable only by the process's own account: this
		// is the "another account's process" case, and it is a refusal rather
		// than a failure because there is nothing about it to retry.
		return fmt.Errorf("%w: the working directory of pid %d cannot be read (%v); the ctl signals only processes it can identify",
			jobs.ErrRefused, pid, err)
	}
	if wantCwd != "" && !cwdMatches(cwd, wantCwd) {
		return fmt.Errorf("%w: pid %d runs in %s, not in %s; it is not the process this tenant's pidfile claims",
			jobs.ErrRefused, pid, cwd, wantCwd)
	}
	if wantCmd != "" {
		b, err := os.ReadFile(filepath.Join(dir, "cmdline"))
		if err != nil {
			return fmt.Errorf("%w: the command line of pid %d cannot be read: %v", jobs.ErrRefused, pid, err)
		}
		if !cmdlineMatches(b, wantCmd) {
			return fmt.Errorf("%w: the command line of pid %d does not mention %q", jobs.ErrRefused, pid, wantCmd)
		}
	}
	return nil
}

// cwdMatches accepts the worktree itself and anything under it: a server
// started from `<worktree>/go` or `<worktree>/frontend` is still that tenant's.
func cwdMatches(cwd, want string) bool {
	cwd, want = filepath.Clean(cwd), filepath.Clean(want)
	if cwd == want {
		return true
	}
	return strings.HasPrefix(cwd, want+string(filepath.Separator))
}

// cmdlineMatches looks for want as a whole argv element or inside one.
//
// /proc/<pid>/cmdline is NUL-separated, so it is split rather than searched as
// one string: without the split, a want that spans the boundary between two
// arguments would match a command line in which it never appeared.
func cmdlineMatches(raw []byte, want string) bool {
	for _, arg := range strings.Split(string(raw), "\x00") {
		if arg == want || strings.Contains(arg, want) {
			return true
		}
	}
	return false
}
