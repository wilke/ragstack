package drivers

import (
	"context"
	"errors"
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

// ---------------------------------------------------------------- stubs
//
// Every host driver in this package runs a program by absolute path, so every
// test of one runs a program too. The programs are tiny shell scripts written
// into the test's own temporary directory: they record the argv they were
// called with and answer from a table the test fills in. That is what lets
// these tests cover the argv, the environment, the exit-code mapping and the
// output parsing on a host that has no systemd, no git and no node — the rule
// the plan states as "tests inject the program path; never skip a test because
// the host lacks the tool".
//
// The scripts have a #!/bin/sh shebang. That is not the drivers using a shell:
// the driver execs an absolute path with an argv, and what that file happens
// to be written in is the test's business.

// stub is one fake program.
type stub struct {
	// Path is the executable to point a driver's Bin field at.
	Path string
	dir  string
	t    *testing.T
}

// keyFirstWord derives the response key from the first argument that is
// neither a flag nor the value of `-C`. It covers `systemctl --user <verb>`
// and `git -C <dir> <verb>` alike.
const keyFirstWord = `
key=
skip=
for a in "$@"; do
  if [ -n "$skip" ]; then skip=; continue; fi
  case "$a" in
    -C) skip=1 ;;
    -*) ;;
    *) key="$a"; break ;;
  esac
done
`

// newStub writes an executable stub whose response key is computed by keyExpr
// (a /bin/sh fragment that sets `key`).
func newStub(t *testing.T, keyExpr string) *stub {
	t.Helper()
	dir := t.TempDir()
	s := &stub{Path: filepath.Join(dir, "prog"), dir: dir, t: t}
	script := "#!/bin/sh\n" +
		"printf '%s\\n' \"$*\" >> " + strconv.Quote(filepath.Join(dir, "argv")) + "\n" +
		keyExpr +
		"d=" + strconv.Quote(dir) + "\n" +
		"[ -f \"$d/$key.out\" ] && cat \"$d/$key.out\"\n" +
		"[ -f \"$d/$key.err\" ] && cat \"$d/$key.err\" >&2\n" +
		"if [ -f \"$d/$key.code\" ]; then exit \"$(cat \"$d/$key.code\")\"; fi\n" +
		"exit 0\n"
	if err := os.WriteFile(s.Path, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	return s
}

// respond makes the stub answer key with this stdout, stderr and exit code.
func (s *stub) respond(key, stdout, stderr string, code int) {
	s.t.Helper()
	write := func(suffix, body string) {
		if body == "" {
			return
		}
		if err := os.WriteFile(filepath.Join(s.dir, key+suffix), []byte(body), 0o644); err != nil {
			s.t.Fatal(err)
		}
	}
	write(".out", stdout)
	write(".err", stderr)
	if code != 0 {
		write(".code", strconv.Itoa(code))
	}
}

// argv is every invocation so far, one joined argv per entry.
func (s *stub) argv() []string {
	s.t.Helper()
	b, err := os.ReadFile(filepath.Join(s.dir, "argv"))
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		s.t.Fatal(err)
	}
	return strings.Split(strings.TrimRight(string(b), "\n"), "\n")
}

// ranNothing asserts the driver refused before running the program at all.
func (s *stub) ranNothing(what string) {
	s.t.Helper()
	if got := s.argv(); len(got) > 0 {
		s.t.Errorf("%s ran the program (%v); it must refuse before executing anything", what, got)
	}
}

// script writes an arbitrary executable into the test's directory.
func script(t *testing.T, name, body string) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), name)
	if err := os.WriteFile(p, []byte(body), 0o755); err != nil {
		t.Fatal(err)
	}
	return p
}

// ---------------------------------------------------------------- runner

func TestRunnerGivesTheChildOnlyTheAllowedEnvironment(t *testing.T) {
	// A secret in THIS process's environment is the case that matters: the
	// daemon holds CTL_API_KEYS, and a --direct run holds whatever the
	// operator's shell carries.
	t.Setenv("CTL_API_KEYS", "super-secret")
	t.Setenv("LANG", "C")
	prog := script(t, "env", "#!/bin/sh\nenv\n")

	r := &runner{}
	stdout, _, err := r.Run(context.Background(), Spec{Program: prog, ExtraEnv: []string{"FOO=bar"}})
	if err != nil {
		t.Fatalf("running the stub: %v", err)
	}
	got := map[string]string{}
	for _, line := range strings.Split(string(stdout), "\n") {
		if k, v, ok := strings.Cut(line, "="); ok {
			got[k] = v
		}
	}
	if got["FOO"] != "bar" {
		t.Errorf("ExtraEnv did not reach the child: %v", got)
	}
	if got["LANG"] != "C" {
		t.Errorf("LANG was not passed through: %v", got)
	}
	if _, ok := got["CTL_API_KEYS"]; ok {
		t.Error("the child inherited CTL_API_KEYS; the sanitizer is not doing its job")
	}
	// /bin/sh sets a few of its own (PWD, SHLVL, _); everything else must be
	// on the keep list or from ExtraEnv.
	allowed := map[string]bool{"FOO": true, "PWD": true, "SHLVL": true, "_": true}
	for _, k := range envKeep {
		allowed[k] = true
	}
	for k := range got {
		if !allowed[k] {
			t.Errorf("the child was given %s, which is on no list", k)
		}
	}
}

func TestRunnerRefusesWhatIsNotAnArgvCall(t *testing.T) {
	r := &runner{}
	ctx := context.Background()
	prog := script(t, "ok", "#!/bin/sh\nexit 0\n")
	for _, c := range []struct {
		name string
		spec Spec
	}{
		{"a relative program", Spec{Program: "systemctl"}},
		{"an absent program", Spec{Program: "/nonexistent/systemctl"}},
		{"a directory", Spec{Program: t.TempDir()}},
		{"an argument with a newline", Spec{Program: prog, Args: []string{"a\nb"}}},
		{"an argument with a NUL", Spec{Program: prog, Args: []string{"a\x00b"}}},
		{"a relative working directory", Spec{Program: prog, Dir: "frontend"}},
	} {
		if _, _, err := r.Run(ctx, c.spec); !errors.Is(err, jobs.ErrRefused) {
			t.Errorf("%s = %v, want a refusal", c.name, err)
		}
	}
}

func TestRunnerErrorCarriesTheRedactedTailOfStderr(t *testing.T) {
	// Much more stderr than the cap, with the reason at the END — which is
	// where a program that printed progress first puts it.
	prog := script(t, "noisy", "#!/bin/sh\n"+
		"i=0; while [ $i -lt 400 ]; do echo 'progress line that nobody needs to read' >&2; i=$((i+1)); done\n"+
		"echo 'fatal: could not read Password for https://x:hunter2@example.com' >&2\n"+
		"exit 3\n")
	r := &runner{Redact: func(s string) string { return strings.ReplaceAll(s, "hunter2", "<redacted>") }}

	_, _, err := r.Run(context.Background(), Spec{Program: prog})
	if err == nil {
		t.Fatal("a program that exited 3 produced no error")
	}
	var ee *ExecError
	if !errors.As(err, &ee) {
		t.Fatalf("error %v is not an *ExecError", err)
	}
	if ee.Code != 3 {
		t.Errorf("exit code = %d, want 3", ee.Code)
	}
	if ee.Program != "noisy" {
		t.Errorf("program = %q, want the basename", ee.Program)
	}
	if !strings.Contains(err.Error(), "fatal: could not read Password") {
		t.Errorf("the error lost the tail of stderr: %v", err)
	}
	if strings.Contains(err.Error(), "hunter2") {
		t.Errorf("the error carries an unredacted secret: %v", err)
	}
	// The cap is applied before redaction, which may lengthen what it kept;
	// the point is that 400 lines of progress did not reach a job log.
	if len(ee.Stderr) > maxStderr+64 {
		t.Errorf("the error carries %d bytes of stderr, far past the %d-byte cap", len(ee.Stderr), maxStderr)
	}
	if strings.Contains(ee.Stderr, "progress line") && strings.Count(ee.Stderr, "progress line") > 60 {
		t.Errorf("the error carries %d progress lines; the tail was not trimmed", strings.Count(ee.Stderr, "progress line"))
	}
}

func TestRunnerTimeoutKillsTheWholeProcessGroup(t *testing.T) {
	dir := t.TempDir()
	childPID := filepath.Join(dir, "child.pid")
	// The stub forks a long sleeper and then sleeps itself: killing only the
	// process the runner started would leave the sleeper behind, which is how
	// a timed-out `npm ci` keeps writing into a worktree the job is rolling
	// back.
	prog := script(t, "forker", "#!/bin/sh\nsleep 60 &\necho $! > "+strconv.Quote(childPID)+"\nsleep 60\n")

	r := &runner{}
	start := time.Now()
	_, _, err := r.Run(context.Background(), Spec{Program: prog, Timeout: 300 * time.Millisecond})
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("a run past its timeout = %v, want a DeadlineExceeded", err)
	}
	if elapsed := time.Since(start); elapsed > 10*time.Second {
		t.Fatalf("the runner took %s to give up on a 300ms timeout", elapsed)
	}
	b, rerr := os.ReadFile(childPID)
	if rerr != nil {
		t.Skipf("the stub never recorded its child's pid (%v); the group kill cannot be checked", rerr)
	}
	pid, _ := strconv.Atoi(strings.TrimSpace(string(b)))
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if err := syscall.Kill(pid, 0); err != nil {
			return // the grandchild is gone: the GROUP was killed
		}
		time.Sleep(50 * time.Millisecond)
	}
	_ = syscall.Kill(pid, syscall.SIGKILL) // do not leak it out of the test
	t.Fatalf("the forked child %d survived the timeout; only the direct child was killed", pid)
}

func TestRunnerReturnsStdoutAndStderrOnSuccess(t *testing.T) {
	prog := script(t, "both", "#!/bin/sh\necho out; echo err >&2\n")
	stdout, stderr, err := (&runner{}).Run(context.Background(), Spec{Program: prog})
	if err != nil {
		t.Fatalf("Run = %v", err)
	}
	if strings.TrimSpace(string(stdout)) != "out" || strings.TrimSpace(string(stderr)) != "err" {
		t.Errorf("stdout=%q stderr=%q", stdout, stderr)
	}
}

func TestRunnerPassesStdinAndDir(t *testing.T) {
	dir := t.TempDir()
	prog := script(t, "cat", "#!/bin/sh\ncat\npwd\n")
	stdout, _, err := (&runner{}).Run(context.Background(), Spec{Program: prog, Dir: dir, Stdin: []byte("hello\n")})
	if err != nil {
		t.Fatalf("Run = %v", err)
	}
	real, _ := filepath.EvalSymlinks(dir)
	if !strings.Contains(string(stdout), "hello") || !strings.Contains(string(stdout), real) {
		t.Errorf("stdin or dir did not reach the child: %q", stdout)
	}
}

// execLooksAvailable reports whether a program exists, for the ONE integration
// test that wants the real git.
func execLooksAvailable(name string) (string, bool) {
	p, err := exec.LookPath(name)
	return p, err == nil
}
