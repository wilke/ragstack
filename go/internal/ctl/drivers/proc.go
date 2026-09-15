package drivers

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
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
	// Roots bound where Spawn may create a file: the pidfile IS the ctl's
	// record of what it started and the log is where a tenant's stderr lands,
	// and both paths come out of a registry row. Empty roots mean Spawn
	// refuses everything, which is the same rule the Files driver follows.
	Roots []string
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

// signals is the signal allowlist. TERM and INT ask a server to shut down,
// HUP asks it to reload, and KILL is the escalation the instance supervisor
// sends ONLY after a TERM and a full stop timeout have passed with the port
// still held — never as a first move, never to a store (stores are stopped
// through `apptainer instance stop`, which is graceful, and the selftest's
// ES check proves it). It was absent at first, and the escalation the plan
// promised ("TERM, then up to 60 s, then KILL") refused itself on the host.
// Every signal here is still gated by the identity proof above.
var signals = map[string]syscall.Signal{
	"TERM": syscall.SIGTERM,
	"INT":  syscall.SIGINT,
	"HUP":  syscall.SIGHUP,
	"KILL": syscall.SIGKILL,
}

// SignalNames is the allowlist by name, for the fake to agree with.
func SignalNames() []string {
	out := make([]string, 0, len(signals))
	for k := range signals {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
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
	fields, err := procStatFields(dir)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return 0, fmt.Errorf("%w: there is no process %s", jobs.ErrRefused, filepath.Base(dir))
		}
		if errors.Is(err, jobs.ErrRefused) {
			return 0, err
		}
		return 0, fmt.Errorf("%w: %s is not readable by this account: %v", jobs.ErrRefused, filepath.Join(dir, "stat"), err)
	}
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

// procStatFields reads /proc/<pid>/stat and returns its fields FROM FIELD 3
// (the state) on, so field N is fields[N-3].
//
// It splits AFTER the last ')': field 2 is the executable name in parentheses
// and may itself contain spaces and parentheses, which is why a plain
// Fields() over this file has been a bug in every language that has one.
//
// The ReadFile error is returned unwrapped so a caller can tell an absent
// process (os.ErrNotExist — the answer Alive gives as `false`) from a file it
// may not read.
func procStatFields(dir string) ([]string, error) {
	b, err := os.ReadFile(filepath.Join(dir, "stat"))
	if err != nil {
		return nil, err
	}
	i := bytes.LastIndexByte(b, ')')
	if i < 0 {
		return nil, fmt.Errorf("%w: %s/stat is not in the format this driver parses", jobs.ErrRefused, dir)
	}
	return strings.Fields(string(b[i+1:])), nil
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

// ---------------------------------------------------------------- spawn

// spawnUmask is the umask the tenant's API and everything it writes inherit.
//
// 0002 — group-writable — because a tenant's data tree is shared between the
// operator who created it and the service account that runs it, and a
// directory uvicorn creates at 0755 is one the other account then cannot write
// into. It is the umask the tenants run under today.
const spawnUmask = 0o002

// umaskMu serializes the umask window of Spawn.
//
// Go has no per-child umask: SysProcAttr cannot carry one, and umask(2) is a
// PROCESS-wide setting, so the only way to give a child one is to set it, fork,
// and put it back. The window between those two calls is this daemon's whole
// umask, so it is held under a mutex, contains exactly the open of the log and
// the fork, and can block on neither.
//
// That is honest but not free: a child another driver forks INSIDE the window
// inherits 0002 as well. The window is two syscalls long, the files those
// children create are group-writable rather than world-anything, and the
// alternative — a helper binary whose only job is to call umask and exec — is a
// second program to ship and allowlist for a difference no operator would ever
// see.
var umaskMu sync.Mutex

// Spawn starts a DETACHED long-lived process and returns its pid.
//
// This is how `supervisor: instance` runs a tenant's API on a host with no
// user manager. What systemd would do — a new session, a clean environment, a
// log to append to, an umask — is done here, explicitly, in the order that
// matters:
//
//  1. every refusal first, so nothing is started that could not be recorded;
//  2. setsid, so the child outlives the ctl and a later stop can signal the
//     whole group;
//  3. the pidfile, atomically, BEFORE returning. The pidfile is the ctl's only
//     record of what it started: a crash between the fork and the write leaves
//     a running tenant nothing can find, so if the write fails the child is
//     killed rather than left behind.
//
// The child is never waited on by the caller. A reaper goroutine collects it
// so the daemon does not accumulate zombies, and that goroutine is the only
// thing in this process that ever touches it again.
func (p *RealProc) Spawn(_ context.Context, spec jobs.SpawnSpec) (int, error) {
	if err := checkProgram(spec.Program); err != nil {
		return 0, err
	}
	for _, a := range spec.Args {
		if err := checkArg(a); err != nil {
			return 0, err
		}
	}
	// A working directory is REQUIRED, and absolute. Proc.Signal proves a
	// process's identity from its cwd before signalling it, so a child spawned
	// without one of its own inherits the daemon's and can never afterwards be
	// stopped by this driver — a tenant started in a way that makes it
	// unstoppable is worse than one that did not start.
	if !filepath.IsAbs(spec.Dir) {
		return 0, fmt.Errorf("%w: proc.Spawn needs an absolute working directory, got %q; "+
			"the stop path identifies a process by its cwd", jobs.ErrRefused, spec.Dir)
	}
	env, err := checkSpawnEnv(spec.Env)
	if err != nil {
		return 0, err
	}
	// Both files are created by this driver from a path that came out of a
	// registry row, so both are held to the approved roots, and neither may be
	// a symlink: an append to a link is an append to its target.
	logPath, err := p.spawnPath("log file", spec.LogPath)
	if err != nil {
		return 0, err
	}
	pidFile, err := p.spawnPath("pidfile", spec.PidFile)
	if err != nil {
		return 0, err
	}

	devNull, err := os.OpenFile(os.DevNull, os.O_RDONLY, 0)
	if err != nil {
		return 0, err
	}
	defer devNull.Close()

	cmd := exec.Command(spec.Program, spec.Args...)
	cmd.Dir = spec.Dir
	// exec.Cmd INHERITS this process's environment when Env is nil, and this
	// process holds CTL_API_KEYS. An empty non-nil slice is how os/exec is
	// told "no environment at all", and checkSpawnEnv guarantees non-nil.
	cmd.Env = env
	cmd.Stdin = devNull
	// Setsid, not Setpgid: the child leads its own SESSION, so it survives the
	// ctl (and a terminal, on a --direct run) and a stop can signal its whole
	// group without reaching anything else.
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}

	logFile, err := startUnderSpawnUmask(cmd, logPath)
	if err != nil {
		return 0, err
	}
	// The parent's copy of the log. The child holds its own descriptors for
	// stdout and stderr; keeping this one open would pin the file for the life
	// of the daemon.
	defer logFile.Close()

	pid := cmd.Process.Pid
	if err := writeAtomic(pidFile, []byte(strconv.Itoa(pid)+"\n"), 0o644); err != nil {
		// Started, unrecordable. Killing it is the only answer that leaves the
		// host in a state a later `start` can reason about: the alternative is
		// a tenant serving on a port with nothing on disk saying it exists.
		// The GROUP, because setsid made this pid a group leader and the
		// program may already have forked.
		_ = p.kill(-pid, syscall.SIGKILL)
		go func() { _ = cmd.Wait() }()
		return 0, fmt.Errorf("writing the pidfile %s: %w (the process was killed; nothing was left running)", pidFile, err)
	}
	// Never Wait()ed by the caller: this goroutine exists only so the exited
	// child does not sit in the daemon's process table as a zombie. It ends
	// when the tenant does.
	go func() { _ = cmd.Wait() }()
	return pid, nil
}

// startUnderSpawnUmask opens the log and starts cmd with the process umask at
// spawnUmask, then restores it. See umaskMu.
//
// The log is opened INSIDE the window on purpose: a log file the daemon
// created under its own umask would not have the mode the tenant's own files
// get, and the one file whose mode an operator actually looks at is that one.
func startUnderSpawnUmask(cmd *exec.Cmd, logPath string) (*os.File, error) {
	umaskMu.Lock()
	defer umaskMu.Unlock()
	old := syscall.Umask(spawnUmask)
	defer syscall.Umask(old)

	f, err := os.OpenFile(logPath, os.O_WRONLY|os.O_APPEND|os.O_CREATE, 0o644)
	if err != nil {
		return nil, err
	}
	// ONE file for both streams, as the api unit's two `append:` lines are:
	// two descriptors on the same file would interleave by offset rather than
	// by append, and a traceback would arrive shredded.
	cmd.Stdout, cmd.Stderr = f, f
	if err := cmd.Start(); err != nil {
		f.Close()
		return nil, err
	}
	return f, nil
}

// spawnPath holds a file Spawn creates to the approved roots and refuses a
// symlink at the leaf: an append to a link is an append to its target, and
// both of these paths come out of a registry row.
func (p *RealProc) spawnPath(what, path string) (string, error) {
	if path == "" {
		return "", fmt.Errorf("%w: proc.Spawn needs a %s", jobs.ErrRefused, what)
	}
	if !filepath.IsAbs(path) {
		return "", fmt.Errorf("%w: the %s %q must be an absolute path", jobs.ErrRefused, what, path)
	}
	resolved, err := resolvedContainedNoLeafLink(path, p.Roots)
	if err != nil {
		return "", err
	}
	return resolved, nil
}

// checkSpawnEnv validates the child's whole environment and returns it as a
// NON-NIL slice (nil is how os/exec is asked to inherit this process's).
//
// No entry is ever quoted back in an error: this slice is tenant.env ∪
// secrets.env, so every value in it is potentially a credential. An entry is
// named by its POSITION, which is enough to find it and says nothing.
func checkSpawnEnv(env []string) ([]string, error) {
	out := make([]string, 0, len(env))
	for i, e := range env {
		k, _, ok := strings.Cut(e, "=")
		if !ok {
			return nil, fmt.Errorf("%w: environment entry %d is not KEY=VALUE", jobs.ErrRefused, i)
		}
		if !envVarRE.MatchString(k) {
			return nil, fmt.Errorf("%w: environment entry %d has %q, which is not a variable name", jobs.ErrRefused, i, k)
		}
		// A NUL truncates the entry at the execve boundary, which would hand
		// the child a variable it cannot use and a value it half-has.
		if strings.ContainsRune(e, 0) {
			return nil, fmt.Errorf("%w: the value of %s contains a NUL", jobs.ErrRefused, k)
		}
		out = append(out, e)
	}
	return out, nil
}

// Alive reports whether pid is a live process.
//
// EXISTENCE only: whether the process is the one the ctl started is Signal's
// identity check, and a caller that cares asks both. A pid that is not running
// is (false, nil) — a dead tenant is a fact the reconcile and the `running`
// post-check read, not an error.
//
// A ZOMBIE is not alive. /proc/<pid> exists for a process that has exited and
// not been reaped, and kill(pid, 0) succeeds on one; a supervisor that called
// that "running" would skip the start of a tenant that is dead, and go on
// skipping it for as long as whatever forked it stays up.
func (p *RealProc) Alive(_ context.Context, pid int) (bool, error) {
	if pid <= 1 {
		return false, fmt.Errorf("%w: %d is not a pid the ctl asks about", jobs.ErrRefused, pid)
	}
	fields, err := procStatFields(p.dir(pid))
	switch {
	case errors.Is(err, os.ErrNotExist):
		return false, nil
	case err != nil:
		return false, err
	case len(fields) == 0:
		return false, fmt.Errorf("%w: /proc/%d/stat has no state field", jobs.ErrRefused, pid)
	}
	// fields[0] is field 3, the state.
	return fields[0] != "Z", nil
}
