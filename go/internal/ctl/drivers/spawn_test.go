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

	"github.com/ragstack/ragstack/internal/ctl/jobs"
)

// spawnFixture is a RealProc whose approved root is a temporary directory
// laid out the way a tenant's is: a working directory to run in and a place
// for the log and the pidfile.
type spawnFixture struct {
	proc    *RealProc
	root    string
	workDir string
	log     string
	pidFile string
}

func newSpawn(t *testing.T) spawnFixture {
	t.Helper()
	root := t.TempDir()
	work := filepath.Join(root, "worktree", "python")
	if err := os.MkdirAll(work, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(root, "logs"), 0o755); err != nil {
		t.Fatal(err)
	}
	return spawnFixture{
		proc:    &RealProc{Roots: []string{root}, ProcRoot: func() string { return "/proc" }},
		root:    root,
		workDir: work,
		log:     filepath.Join(root, "logs", "api-dev.log"),
		pidFile: filepath.Join(root, "api-dev.pid"),
	}
}

// spec is a SpawnSpec for program with these arguments.
func (f spawnFixture) spec(program string, args ...string) jobs.SpawnSpec {
	return jobs.SpawnSpec{
		Program: program,
		Args:    args,
		Dir:     f.workDir,
		Env:     []string{"PATH=/usr/bin:/bin", "TENANT=dev"},
		LogPath: f.log,
		PidFile: f.pidFile,
	}
}

// waitFor polls until cond is true or the deadline passes. The child is a real
// process: everything about it is observed after a fork, not after a return.
func waitFor(t *testing.T, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for %s", what)
}

// sessionOf is field 6 of /proc/<pid>/stat, the process's session id.
//
// Read from /proc rather than from getsid(2): Go's syscall package does not
// export Getsid on Linux, and the fact this test needs — "the child leads a
// session of its own" — is in the file either way.
func sessionOf(t *testing.T, pid int) int {
	t.Helper()
	fields, err := procStatFields(filepath.Join("/proc", strconv.Itoa(pid)))
	if err != nil {
		t.Fatal(err)
	}
	// fields[0] is field 3, so field 6 is fields[3].
	sid, err := strconv.Atoi(fields[3])
	if err != nil {
		t.Fatalf("/proc/%d/stat field 6 is %q: %v", pid, fields[3], err)
	}
	return sid
}

// sleepScript is a child that lives until it is signalled. It writes a file
// first, so a test can tell "started" from "about to start", and it creates
// that file with 0666 in the open so that the only thing deciding its mode is
// the umask it inherited.
//
// It does NOT `exec` the sleep: the identity check the stop path makes reads
// /proc/<pid>/cmdline, and a shell that exec'd would have replaced the command
// line this driver's caller knows it by. A tenant's uvicorn keeps its own.
func sleepScript(t *testing.T, dir, marker string) string {
	t.Helper()
	return script(t, "tenant-api", fmt.Sprintf(
		"#!/bin/sh\n"+
			"echo started\n"+
			"echo also-stderr >&2\n"+
			": > %q\n"+
			"sleep 60\n", filepath.Join(dir, marker)))
}

// TestSpawnStartsADetachedProcessAndRecordsItBeforeReturning is the contract
// of Proc.Spawn in one test: the pidfile exists BY THE TIME Spawn returns (the
// ctl's only record of what it started), the child is in its own session (it
// outlives the ctl), and its output is appended to the log.
func TestSpawnStartsADetachedProcessAndRecordsItBeforeReturning(t *testing.T) {
	f := newSpawn(t)
	prog := sleepScript(t, f.root, "started")

	pid, err := f.proc.Spawn(context.Background(), f.spec(prog, "--port", "24040"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = syscall.Kill(-pid, syscall.SIGKILL) })

	// Read the pidfile IMMEDIATELY: nothing is waited for, so a driver that
	// wrote it from a goroutine would fail right here.
	body, err := os.ReadFile(f.pidFile)
	if err != nil {
		t.Fatalf("the pidfile is not there when Spawn returned: %v", err)
	}
	if got := strings.TrimSpace(string(body)); got != strconv.Itoa(pid) {
		t.Errorf("the pidfile holds %q, want the pid %d Spawn returned", got, pid)
	}
	st, err := os.Stat(f.pidFile)
	if err != nil {
		t.Fatal(err)
	}
	if st.Mode().Perm() != 0o644 {
		t.Errorf("the pidfile is %v, want 0644: an operator and the other account both read it", st.Mode().Perm())
	}

	// Its own session, which is what makes it survive the ctl and what makes
	// a stop able to signal the group without reaching anything else.
	if sid := sessionOf(t, pid); sid != pid {
		t.Errorf("the child's session is %d, want its own (%d)", sid, pid)
	}
	if sid, ours := sessionOf(t, pid), sessionOf(t, os.Getpid()); sid == ours {
		t.Errorf("the child is in this process's session (%d); it is not detached", ours)
	}

	// Both streams, appended to one file.
	waitFor(t, "the child to write to the log", func() bool {
		b, _ := os.ReadFile(f.log)
		return strings.Contains(string(b), "started") && strings.Contains(string(b), "also-stderr")
	})

	// Alive agrees, and so does the identity check the stop path makes: a
	// process this driver spawned must be one it can afterwards signal.
	if alive, err := f.proc.Alive(context.Background(), pid); err != nil || !alive {
		t.Errorf("Alive(%d) = %v, %v, want true", pid, alive, err)
	}
	if err := f.proc.Signal(context.Background(), pid, f.workDir, "tenant-api", "TERM"); err != nil {
		t.Fatalf("Signal of a process this driver spawned = %v; the stop path could never stop it", err)
	}
	waitFor(t, "the signalled child to die", func() bool {
		alive, err := f.proc.Alive(context.Background(), pid)
		return err == nil && !alive
	})
}

// TestSpawnAppendsToTheLogItIsGiven: a restart must not truncate the log the
// last run wrote, which is where the reason it stopped is.
func TestSpawnAppendsToTheLogItIsGiven(t *testing.T) {
	f := newSpawn(t)
	if err := os.WriteFile(f.log, []byte("older lines\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	prog := script(t, "once", "#!/bin/sh\necho newer\n")
	pid, err := f.proc.Spawn(context.Background(), f.spec(prog))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = syscall.Kill(-pid, syscall.SIGKILL) })
	waitFor(t, "the second run's line", func() bool {
		b, _ := os.ReadFile(f.log)
		return strings.Contains(string(b), "newer")
	})
	b, _ := os.ReadFile(f.log)
	if !strings.Contains(string(b), "older lines") {
		t.Errorf("the log was truncated: %q", b)
	}
}

// TestSpawnGivesTheChildExactlyTheEnvironmentItWasGiven.
//
// The program is /usr/bin/env itself, so what lands in the log IS the child's
// environment, with nothing between it and the assertion. The case that
// matters is the one this test sets up deliberately: a variable in the
// DAEMON's environment (the ctl holds CTL_API_KEYS) must not be in the child's.
func TestSpawnGivesTheChildExactlyTheEnvironmentItWasGiven(t *testing.T) {
	envBin, ok := execLooksAvailable("env")
	if !ok {
		t.Skip("no env(1) on this host")
	}
	t.Setenv("CTL_API_KEYS", "a-key-the-tenant-must-never-see")

	f := newSpawn(t)
	spec := f.spec(envBin)
	spec.Env = []string{"PATH=/usr/bin:/bin", "TENANT=dev", "RAGSTACK_DB_PASSWORD=not-logged"}
	pid, err := f.proc.Spawn(context.Background(), spec)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = syscall.Kill(-pid, syscall.SIGKILL) })

	waitFor(t, "env(1) to print the child's environment", func() bool {
		b, _ := os.ReadFile(f.log)
		return strings.Contains(string(b), "TENANT=dev")
	})
	b, err := os.ReadFile(f.log)
	if err != nil {
		t.Fatal(err)
	}
	got := strings.Split(strings.TrimRight(string(b), "\n"), "\n")
	if len(got) != len(spec.Env) {
		t.Fatalf("the child has %d variables, want exactly the %d it was given: %v", len(got), len(spec.Env), got)
	}
	for i, want := range spec.Env {
		if got[i] != want {
			t.Errorf("variable %d = %q, want %q", i, got[i], want)
		}
	}
}

// TestSpawnGivesTheChildUmask0002.
//
// The tenant's data tree is shared between the operator who created it and the
// service account that runs the API, so a directory uvicorn creates has to be
// group-writable. Go has no per-child umask (see umaskMu), so this is the test
// that the workaround actually reaches the child — and that the daemon's own
// umask is back afterwards.
func TestSpawnGivesTheChildUmask0002(t *testing.T) {
	f := newSpawn(t)
	before := syscall.Umask(0o022)
	syscall.Umask(before)

	prog := sleepScript(t, f.root, "child-made-this")
	pid, err := f.proc.Spawn(context.Background(), f.spec(prog))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = syscall.Kill(-pid, syscall.SIGKILL) })

	made := filepath.Join(f.root, "child-made-this")
	waitFor(t, "the child to create its file", func() bool {
		_, err := os.Stat(made)
		return err == nil
	})
	st, err := os.Stat(made)
	if err != nil {
		t.Fatal(err)
	}
	// 0666 &^ 0002.
	if got := st.Mode().Perm(); got != 0o664 {
		t.Errorf("the child created a file with %v, want 0664 (umask 0002)", got)
	}
	// The log the driver opened inside the same window, and the pidfile.
	if lst, err := os.Stat(f.log); err == nil && lst.Mode().Perm() != 0o644 {
		t.Errorf("the log is %v, want 0644", lst.Mode().Perm())
	}
	// And this process's umask is what it was: the window is two syscalls
	// long and it closes.
	now := syscall.Umask(0o022)
	syscall.Umask(before)
	if now != before {
		t.Errorf("the daemon's umask is %#o after a Spawn, want %#o", now, before)
	}
}

func TestSpawnRefusesEverythingItCannotRecordOrStop(t *testing.T) {
	ctx := context.Background()
	f := newSpawn(t)
	prog := script(t, "prog", "#!/bin/sh\nsleep 1\n")
	outside := filepath.Join(t.TempDir(), "elsewhere")

	base := f.spec(prog)
	for what, mutate := range map[string]func(*jobs.SpawnSpec){
		"a relative program":          func(s *jobs.SpawnSpec) { s.Program = "uvicorn" },
		"a program that is not there": func(s *jobs.SpawnSpec) { s.Program = filepath.Join(f.root, "absent") },
		// No cwd of its own means a process the stop path's identity check
		// can never match: started and unstoppable.
		"no working directory":                       func(s *jobs.SpawnSpec) { s.Dir = "" },
		"a relative working directory":               func(s *jobs.SpawnSpec) { s.Dir = "python" },
		"no log":                                     func(s *jobs.SpawnSpec) { s.LogPath = "" },
		"a relative log":                             func(s *jobs.SpawnSpec) { s.LogPath = "logs/api.log" },
		"a log outside the roots":                    func(s *jobs.SpawnSpec) { s.LogPath = outside },
		"no pidfile":                                 func(s *jobs.SpawnSpec) { s.PidFile = "" },
		"a relative pidfile":                         func(s *jobs.SpawnSpec) { s.PidFile = "api.pid" },
		"a pidfile outside the roots":                func(s *jobs.SpawnSpec) { s.PidFile = outside },
		"an argument with a newline":                 func(s *jobs.SpawnSpec) { s.Args = []string{"--port\n24040"} },
		"an environment entry that is not KEY=VALUE": func(s *jobs.SpawnSpec) { s.Env = []string{"JUST_A_WORD"} },
		"an environment name that is not one":        func(s *jobs.SpawnSpec) { s.Env = []string{"NOT A NAME=x"} },
	} {
		spec := base
		spec.Args = append([]string(nil), base.Args...)
		spec.Env = append([]string(nil), base.Env...)
		mutate(&spec)
		pid, err := f.proc.Spawn(ctx, spec)
		if !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("Spawn with %s = %d, %v, want a refusal", what, pid, err)
			if pid > 0 {
				_ = syscall.Kill(-pid, syscall.SIGKILL)
			}
		}
	}
}

// TestSpawnRefusesAPidfileOrLogThatIsASymlink: an append to a link is an
// append to its target, and both paths come out of a registry row.
func TestSpawnRefusesAPidfileOrLogThatIsASymlink(t *testing.T) {
	f := newSpawn(t)
	prog := script(t, "prog", "#!/bin/sh\nsleep 1\n")
	target := filepath.Join(t.TempDir(), "precious")
	if err := os.WriteFile(target, []byte("not the ctl's"), 0o644); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(f.root, "linked.pid")
	if err := os.Symlink(target, link); err != nil {
		t.Fatal(err)
	}
	spec := f.spec(prog)
	spec.PidFile = link
	if _, err := f.proc.Spawn(context.Background(), spec); !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("Spawn with a symlinked pidfile = %v, want a refusal", err)
	}
	if b, _ := os.ReadFile(target); string(b) != "not the ctl's" {
		t.Fatalf("the symlink's target was written: %q", b)
	}
}

// TestSpawnWithNoApprovedRootsRefuses is the Files driver's rule: a driver
// with no roots writes nothing, rather than everything.
func TestSpawnWithNoApprovedRootsRefuses(t *testing.T) {
	f := newSpawn(t)
	f.proc.Roots = nil
	prog := script(t, "prog", "#!/bin/sh\nsleep 1\n")
	if _, err := f.proc.Spawn(context.Background(), f.spec(prog)); !errors.Is(err, jobs.ErrRefused) {
		t.Fatalf("Spawn with no approved roots = %v, want a refusal", err)
	}
}

// TestSpawnMatchesTheFakesRefusals: the fake claims an absolute program, a log
// and a pidfile are required, and an op tested against it must not meet a
// different rule on the host.
func TestSpawnMatchesTheFakesRefusals(t *testing.T) {
	ctx := context.Background()
	f := newSpawn(t)
	fake := NewFake(FakeOptions{Roots: []string{f.root}}).FakeProc()
	prog := script(t, "prog", "#!/bin/sh\nsleep 1\n")

	for what, mutate := range map[string]func(*jobs.SpawnSpec){
		"a relative program": func(s *jobs.SpawnSpec) { s.Program = "uvicorn" },
		"no log":             func(s *jobs.SpawnSpec) { s.LogPath = "" },
		"no pidfile":         func(s *jobs.SpawnSpec) { s.PidFile = "" },
	} {
		spec := f.spec(prog)
		mutate(&spec)
		_, fakeErr := fake.Spawn(ctx, spec)
		pid, realErr := f.proc.Spawn(ctx, spec)
		if pid > 0 {
			_ = syscall.Kill(-pid, syscall.SIGKILL)
		}
		if !errors.Is(fakeErr, jobs.ErrRefused) || !errors.Is(realErr, jobs.ErrRefused) {
			t.Errorf("Spawn with %s: fake = %v, real = %v; both must refuse", what, fakeErr, realErr)
		}
	}
}

func TestAliveAnswersAboutExistenceOnly(t *testing.T) {
	ctx := context.Background()
	p := &RealProc{ProcRoot: func() string { return "/proc" }}

	// This process.
	if alive, err := p.Alive(ctx, os.Getpid()); err != nil || !alive {
		t.Errorf("Alive(self) = %v, %v, want true", alive, err)
	}

	// A pid with no /proc entry. The root is an EMPTY directory rather than a
	// number guessed to be free: a pid this host happens to have reused would
	// make the test flaky for a reason that has nothing to do with the driver.
	empty := t.TempDir()
	gone := &RealProc{ProcRoot: func() string { return empty }}
	if alive, err := gone.Alive(ctx, 4242); err != nil || alive {
		t.Errorf("Alive of a pid that is not running = %v, %v, want false and no error", alive, err)
	}

	// pid 1 and below are not pids the ctl asks about: pid 1 is init, 0 and
	// the negatives are process GROUPS as far as kill(2) is concerned, and a
	// pidfile holding one of them is a corrupt pidfile.
	for _, pid := range []int{1, 0, -1, -4242} {
		if _, err := p.Alive(ctx, pid); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("Alive(%d) = %v, want a refusal", pid, err)
		}
	}
}

// TestAliveIsFalseForAZombie is the one that matters for the supervisor.
//
// A process that has exited and not been reaped still has a /proc entry and
// still accepts kill(pid, 0), so a driver that asked either question would
// call a dead tenant running — and go on skipping its start for as long as
// whatever forked it stays up.
func TestAliveIsFalseForAZombie(t *testing.T) {
	sh, ok := execLooksAvailable("sh")
	if !ok {
		t.Skip("no sh(1) on this host")
	}
	cmd := exec.Command(sh, "-c", "exit 0")
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	// Deliberately NOT waited on until the assertion is made: that is what
	// makes it a zombie.
	pid := cmd.Process.Pid
	t.Cleanup(func() { _ = cmd.Wait() })

	p := &RealProc{ProcRoot: func() string { return "/proc" }}
	ctx := context.Background()
	waitFor(t, "the child to become a zombie", func() bool {
		fields, err := procStatFields(filepath.Join("/proc", strconv.Itoa(pid)))
		return err == nil && len(fields) > 0 && fields[0] == "Z"
	})
	alive, err := p.Alive(ctx, pid)
	if err != nil {
		t.Fatal(err)
	}
	if alive {
		t.Error("Alive of a zombie = true; a reaped-nowhere process is not a running tenant")
	}
}
