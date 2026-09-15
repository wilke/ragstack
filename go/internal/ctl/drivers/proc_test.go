package drivers

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/hostfacts"
	"github.com/ragstack/ragstack/internal/ctl/jobs"
)

func TestProcListeningAndOwnerReadTheHostfactsTable(t *testing.T) {
	ctx := context.Background()
	p := &RealProc{Listeners: func() ([]hostfacts.Listener, error) {
		return []hostfacts.Listener{
			// A loopback bind, a wildcard bind and a socket whose owner this
			// account cannot see (pid 0) — the three shapes a port question
			// comes in.
			{Port: 24040, Addr: "127.0.0.1", Pid: 4242, UID: 3581},
			{Port: 24041, Addr: "0.0.0.0", Pid: 99, UID: 0},
			{Port: 9200, Addr: "::", Pid: 0, UID: -1},
		}, nil
	}}

	for _, c := range []struct {
		port int
		want bool
	}{{24040, true}, {24041, true}, {9200, true}, {24099, false}} {
		got, err := p.Listening(ctx, c.port)
		if err != nil {
			t.Fatalf("Listening(%d) = %v", c.port, err)
		}
		if got != c.want {
			t.Errorf("Listening(%d) = %v, want %v", c.port, got, c.want)
		}
	}

	if pid, uid, err := p.Owner(ctx, 24040); err != nil || pid != 4242 || uid != 3581 {
		t.Errorf("Owner(24040) = %d, %d, %v", pid, uid, err)
	}
	// A socket this account cannot attribute, and an unbound port, both answer
	// (0, 0, nil): "somebody has it, not visibly me" and "nobody has it" are
	// facts a caller reads, not errors.
	if pid, uid, err := p.Owner(ctx, 9200); err != nil || pid != 0 || uid != 0 {
		t.Errorf("Owner(9200) = %d, %d, %v, want 0, 0, nil", pid, uid, err)
	}
	if pid, uid, err := p.Owner(ctx, 24099); err != nil || pid != 0 || uid != 0 {
		t.Errorf("Owner of an unbound port = %d, %d, %v, want 0, 0, nil", pid, uid, err)
	}
}

func TestProcRefusesWithoutAListenerSource(t *testing.T) {
	p := &RealProc{}
	if _, err := p.Listening(context.Background(), 24040); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("Listening with no source = %v, want a refusal", err)
	}
}

// sleeper starts a real child process in dir and returns its pid. It is a real
// process because the identity check reads /proc, and the one thing a fixture
// tree cannot prove is that the check agrees with the kernel.
func sleeper(t *testing.T, dir string) int {
	t.Helper()
	sleep, ok := execLooksAvailable("sleep")
	if !ok {
		t.Skip("no sleep(1) on this host; the signal test needs a real child process")
	}
	cmd := exec.Command(sleep, "60")
	cmd.Dir = dir
	if err := cmd.Start(); err != nil {
		t.Fatalf("starting the child: %v", err)
	}
	t.Cleanup(func() {
		_ = cmd.Process.Kill()
		_, _ = cmd.Process.Wait()
	})
	return cmd.Process.Pid
}

func TestProcSignalChecksIdentityBeforeItSignalsAnything(t *testing.T) {
	ctx := context.Background()
	// EvalSymlinks: /proc/<pid>/cwd is the RESOLVED path, and on a host whose
	// temp directory is a symlink the two spellings differ.
	dir, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	other := t.TempDir()
	pid := sleeper(t, dir)
	p := &RealProc{}

	// A pid whose cwd is not the tenant's worktree: refused. A pidfile is a
	// file, so its contents are a hint to check, never an instruction.
	if err := p.Signal(ctx, pid, other, "sleep", "TERM"); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("signalling a process in the wrong cwd = %v, want a refusal", err)
	}
	// A cmdline that does not mention what the caller expected: refused.
	if err := p.Signal(ctx, pid, dir, "uvicorn", "TERM"); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("signalling a process whose cmdline does not match = %v, want a refusal", err)
	}
	// A signal outside the allowlist: refused, whatever the identity says.
	// (KILL is IN the list — it is the instance supervisor's escalation after
	// a TERM and a full stop timeout — so a signal that is not is used here.)
	if err := p.Signal(ctx, pid, dir, "sleep", "USR1"); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("SIGUSR1 = %v, want a refusal", err)
	}
	if err := p.Signal(ctx, 1, dir, "sleep", "TERM"); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("signalling pid 1 = %v, want a refusal", err)
	}
	// The process is still alive: none of the refusals delivered anything.
	if err := syscall.Kill(pid, 0); err != nil {
		t.Fatalf("the child died despite every call being refused: %v", err)
	}

	// The matching cwd and cmdline: the signal is delivered and the child dies.
	if err := p.Signal(ctx, pid, dir, "sleep", "TERM"); err != nil {
		t.Fatalf("Signal with a matching identity = %v", err)
	}
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		// The child is this test's own, so it is reaped by the cleanup Wait;
		// until then /proc/<pid> exists as a zombie. Its state field says so.
		b, err := os.ReadFile(filepath.Join("/proc", strconv.Itoa(pid), "stat"))
		if err != nil {
			return // gone
		}
		if i := strings.LastIndexByte(string(b), ')'); i > 0 {
			if f := strings.Fields(string(b)[i+1:]); len(f) > 0 && f[0] == "Z" {
				return // dead, awaiting the test's own Wait
			}
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("the child survived a SIGTERM the driver reported as delivered")
}

func TestProcSignalRefusesAProcessItCannotIdentify(t *testing.T) {
	ctx := context.Background()
	// A fixture /proc with nothing in it: this is what another account's
	// process looks like from here — the entry is there, its cwd is not
	// readable — and what an exited pid looks like.
	root := t.TempDir()
	p := &RealProc{ProcRoot: func() string { return root }}
	if err := p.Signal(ctx, 4242, "/rag/repos/tenants/dev", "uvicorn", "TERM"); !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("signalling a pid with no /proc entry = %v, want a refusal", err)
	}

	// The stat file exists (every /proc/<pid>/stat is world-readable) but the
	// cwd link is not: another account's process, exactly.
	dir := filepath.Join(root, "4242")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "stat"), []byte(fakeStat(4242, "python3", 900100)), 0o644); err != nil {
		t.Fatal(err)
	}
	err := p.Signal(ctx, 4242, "/rag/repos/tenants/dev", "uvicorn", "TERM")
	if !errors.Is(err, jobs.ErrRefused) {
		t.Errorf("signalling a process whose cwd cannot be read = %v, want a refusal", err)
	}
}

func TestProcSignalRefusesAPidThatWasReusedUnderIt(t *testing.T) {
	// Two /proc trees for the same pid: the SAME command line and cwd, a
	// DIFFERENT start time — a pid the kernel handed to another process
	// between the identity check and the signal. Without the start-time guard
	// the driver would SIGTERM whatever inherited the number.
	before := t.TempDir()
	after := t.TempDir()
	cwd, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	for _, c := range []struct {
		root  string
		start uint64
	}{{before, 900100}, {after, 987654}} {
		dir := filepath.Join(c.root, "4242")
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, "stat"), []byte(fakeStat(4242, "python3", c.start)), 0o644); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, "cmdline"), []byte("python3\x00-m\x00uvicorn\x00app:app\x00"), 0o644); err != nil {
			t.Fatal(err)
		}
		if err := os.Symlink(cwd, filepath.Join(dir, "cwd")); err != nil {
			t.Fatal(err)
		}
	}

	signalled := 0
	calls := 0
	p := &RealProc{
		// The third read of /proc — the one taken immediately before the kill
		// — lands on the tree where the pid belongs to a different process.
		ProcRoot: func() string {
			calls++
			if calls >= 3 {
				return after
			}
			return before
		},
		Kill: func(int, syscall.Signal) error { signalled++; return nil },
	}
	err = p.Signal(context.Background(), 4242, cwd, "uvicorn", "TERM")
	if !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("signalling a reused pid = %v, want a refusal", err)
	}
	if !strings.Contains(err.Error(), "replaced") {
		t.Errorf("the refusal does not say what happened: %v", err)
	}
	if signalled != 0 {
		t.Errorf("the driver signalled %d times; a reused pid must receive nothing", signalled)
	}

	// The same fixture without the swap signals normally, so the test above
	// proves the GUARD and not merely a broken fixture.
	signalled = 0
	stable := &RealProc{
		ProcRoot: func() string { return before },
		Kill: func(_ int, sig syscall.Signal) error {
			if sig != syscall.SIGTERM {
				t.Errorf("signal = %v, want SIGTERM", sig)
			}
			signalled++
			return nil
		},
	}
	if err := stable.Signal(context.Background(), 4242, cwd, "uvicorn", "TERM"); err != nil {
		t.Fatalf("Signal on a stable process = %v", err)
	}
	if signalled != 1 {
		t.Errorf("the driver delivered %d signals, want 1", signalled)
	}
}

func TestProcSignalAcceptsACwdInsideTheWorktree(t *testing.T) {
	root := t.TempDir()
	worktree, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	inside := filepath.Join(worktree, "go")
	if err := os.MkdirAll(inside, 0o755); err != nil {
		t.Fatal(err)
	}
	dir := filepath.Join(root, "77")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "stat"), []byte(fakeStat(77, "ragstack-api", 42)), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "cmdline"), []byte("./ragstack-api\x00-addr\x00:8080\x00"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(inside, filepath.Join(dir, "cwd")); err != nil {
		t.Fatal(err)
	}

	got := 0
	p := &RealProc{
		ProcRoot: func() string { return root },
		Kill:     func(int, syscall.Signal) error { got++; return nil },
	}
	// A server started from <worktree>/go is still that tenant's; and the
	// cmdline match is a substring of one argv element, not of the whole file.
	if err := p.Signal(context.Background(), 77, worktree, "ragstack-api", "HUP"); err != nil {
		t.Fatalf("Signal from a subdirectory of the worktree = %v", err)
	}
	if got != 1 {
		t.Errorf("the signal was not delivered")
	}
}

// fakeStat renders a /proc/<pid>/stat line with a comm that contains the
// spaces and parentheses the real file may contain, so the parser is exercised
// on the shape that has broken every naive implementation of it.
func fakeStat(pid int, comm string, start uint64) string {
	fields := make([]string, 0, 52)
	fields = append(fields, strconv.Itoa(pid), "("+comm+" (x))", "S")
	for i := 4; i <= 52; i++ {
		switch i {
		case 22:
			fields = append(fields, strconv.FormatUint(start, 10))
		default:
			fields = append(fields, "0")
		}
	}
	return fmt.Sprintln(strings.Join(fields, " "))
}
