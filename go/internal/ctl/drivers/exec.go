package drivers

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/jobs"
)

// runner is the one way every host driver in this package starts a program.
//
// It exists so that the rules the plan states once — argv only, an absolute
// program path, a sanitized environment, a bounded run, redacted output — are
// implemented once as well. A driver that built its own exec.Cmd would be a
// second place those rules could be forgotten, and the one that forgets is the
// one that leaks a tenant's secrets into a subprocess or hangs a job forever
// on a `git fetch` nobody bounded.
//
// There is no shell anywhere in this file, and there is no PATH search: the
// program is an absolute path the operator configured, and the arguments are
// an argv slice. Nothing a registry row or a ref name contains can therefore
// become a command, whatever characters it holds.
type runner struct {
	// Env returns the environment a child sees. Nil means sanitizedEnv, which
	// is the only environment the drivers use in production; a test replaces
	// it to prove what a child was given.
	Env func(extra []string) []string
	// Timeout bounds one run when the Spec does not name its own. Zero means
	// the context alone decides, which is how a caller asks for "as long as
	// the job has".
	Timeout time.Duration
	// Logger records each run at debug level, with the program, the argv and
	// the duration. It is never given the output: stdout may be a tenant's
	// configuration and stderr may quote a DSN.
	Logger *slog.Logger
	// Redact is applied to captured stderr BEFORE it reaches an error string,
	// because that error travels into a job log, an audit row and an operator's
	// terminal. Nil is the identity function — the drivers are wired with the
	// engine's redactor, and a test that does not care passes nothing.
	Redact func(string) string
}

// Spec is one program run.
type Spec struct {
	// Program is the absolute path of the executable.
	Program string
	// Args is argv[1:]; the program name is never repeated here.
	Args []string
	// Dir is the working directory (absolute, or empty for this process's).
	Dir string
	// ExtraEnv is "KEY=VALUE" pairs added to the sanitized environment. It is
	// how a driver passes NPM_CONFIG_CACHE or CI=1 without inheriting the
	// daemon's whole environment.
	ExtraEnv []string
	// Stdin, when non-empty, is written to the child's standard input.
	Stdin []byte
	// Timeout overrides the runner's for this run: `npm ci` needs minutes,
	// `systemctl is-active` needs a second, and one timeout for both would be
	// either uselessly long or wrong.
	Timeout time.Duration
}

// maxStderr is how much of a failed program's stderr an error carries. The
// LAST 2 KiB rather than the first: a program that fails after printing
// progress puts the reason at the end, and an error message that scrolled a
// job log with a hundred lines of npm output would be read by nobody.
const maxStderr = 2 << 10

// ExecError is a non-zero exit. It names the program by BASENAME (the full
// path is an operator's configuration, not news to them), the exit code, and
// the tail of stderr after redaction.
type ExecError struct {
	Program string
	Code    int
	Stderr  string
	Err     error
}

func (e *ExecError) Error() string {
	msg := fmt.Sprintf("%s exited %d", e.Program, e.Code)
	if e.Stderr != "" {
		msg += ": " + e.Stderr
	}
	return msg
}

// Unwrap keeps the os/exec error matchable (context.DeadlineExceeded reaches
// a caller through it).
func (e *ExecError) Unwrap() error { return e.Err }

// envKeep is every variable a child may inherit from this process.
//
// The list is short on purpose. The ctl's own environment holds CTL_API_KEYS
// and, on a --direct run, whatever the operator's shell carries; a subprocess
// that inherited it would put credentials in /proc/<pid>/environ of a program
// that has no use for them. PATH, HOME and the locale are what a well-behaved
// tool needs to run at all; XDG_RUNTIME_DIR and DBUS_SESSION_BUS_ADDRESS are
// how `systemctl --user` finds the user manager, without which every unit
// verb fails with "Failed to connect to bus".
var envKeep = []string{
	"PATH", "HOME", "LANG", "LC_ALL", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
}

// sanitizedEnv is envKeep from this process, plus extra.
func sanitizedEnv(extra []string) []string {
	out := make([]string, 0, len(envKeep)+len(extra))
	for _, k := range envKeep {
		if v, ok := os.LookupEnv(k); ok {
			out = append(out, k+"="+v)
		}
	}
	return append(out, extra...)
}

func (r *runner) env(extra []string) []string {
	if r.Env != nil {
		return r.Env(extra)
	}
	return sanitizedEnv(extra)
}

func (r *runner) redact(s string) string {
	if r.Redact == nil {
		return s
	}
	return r.Redact(s)
}

func (r *runner) log() *slog.Logger {
	if r.Logger != nil {
		return r.Logger
	}
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

// Run executes spec and returns its captured output.
//
// Every refusal here is a jobs.ErrRefused rather than a plain error: a
// relative program path, an argument carrying a NUL or a newline, a missing
// binary — none of them is a host that failed, all of them are a call this
// driver will not make, and the contract answers 409 `refused` for exactly
// that.
func (r *runner) Run(ctx context.Context, spec Spec) ([]byte, []byte, error) {
	if err := checkProgram(spec.Program); err != nil {
		return nil, nil, err
	}
	for _, a := range spec.Args {
		if err := checkArg(a); err != nil {
			return nil, nil, err
		}
	}
	if spec.Dir != "" && !filepath.IsAbs(spec.Dir) {
		return nil, nil, fmt.Errorf("%w: the working directory %q must be an absolute path", jobs.ErrRefused, spec.Dir)
	}

	timeout := spec.Timeout
	if timeout == 0 {
		timeout = r.Timeout
	}
	if timeout > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, timeout)
		defer cancel()
	}

	cmd := exec.CommandContext(ctx, spec.Program, spec.Args...)
	cmd.Dir = spec.Dir
	cmd.Env = r.env(spec.ExtraEnv)
	if len(spec.Stdin) > 0 {
		cmd.Stdin = bytes.NewReader(spec.Stdin)
	}
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	// The child leads its own process group, and the cancellation signal goes
	// to the GROUP. `npm ci` and `vite build` fork; killing the process the
	// ctl started leaves its children running against the worktree the job is
	// about to roll back, which is how a timed-out build comes back to life
	// halfway through the cleanup.
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.Cancel = func() error {
		if cmd.Process == nil {
			return nil
		}
		return syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
	}
	// A killed process group can still hold the output pipes open through a
	// grandchild; WaitDelay bounds the wait rather than blocking the job on
	// one. It is short because the group has already been SIGKILLed.
	cmd.WaitDelay = 2 * time.Second

	started := time.Now()
	err := cmd.Run()
	r.log().Debug("ran a host program",
		"program", filepath.Base(spec.Program), "args", strings.Join(spec.Args, " "),
		"dir", spec.Dir, "ms", time.Since(started).Milliseconds(), "err", err != nil)
	if err != nil {
		return stdout.Bytes(), stderr.Bytes(), r.fail(ctx, spec, stderr.Bytes(), err)
	}
	return stdout.Bytes(), stderr.Bytes(), nil
}

// fail turns os/exec's error into an ExecError an operator can read.
func (r *runner) fail(ctx context.Context, spec Spec, stderr []byte, err error) error {
	code := -1
	var ee *exec.ExitError
	if errors.As(err, &ee) {
		code = ee.ExitCode()
	}
	out := &ExecError{
		Program: filepath.Base(spec.Program),
		Code:    code,
		Stderr:  r.redact(tail(stderr, maxStderr)),
		Err:     err,
	}
	// A run the context ended is a TIMEOUT, not a program that chose to exit
	// non-zero, and the two lead an operator to different places. The context
	// is checked rather than the exit code because a SIGKILLed process reports
	// exit status -1 for several unrelated reasons.
	if ctxErr := ctx.Err(); ctxErr != nil {
		return fmt.Errorf("%s timed out after %s: %w", out.Program, runTimeout(spec, r), ctxErr)
	}
	return out
}

func runTimeout(spec Spec, r *runner) time.Duration {
	if spec.Timeout > 0 {
		return spec.Timeout
	}
	return r.Timeout
}

// checkProgram enforces "absolute path that exists and is executable".
func checkProgram(program string) error {
	if program == "" {
		return fmt.Errorf("%w: no program was configured for this driver", jobs.ErrRefused)
	}
	if !filepath.IsAbs(program) {
		return fmt.Errorf("%w: the program %q must be an absolute path; this package never searches PATH", jobs.ErrRefused, program)
	}
	st, err := os.Stat(program)
	if err != nil {
		return fmt.Errorf("%w: %s is not usable: %v", jobs.ErrRefused, program, err)
	}
	if st.IsDir() || st.Mode()&0o111 == 0 {
		return fmt.Errorf("%w: %s is not an executable file", jobs.ErrRefused, program)
	}
	return nil
}

// checkArg refuses the two characters that make an argument something other
// than an argument: a NUL truncates it at the execve boundary, and a newline
// turns one line of a log, a unit file or an env file into two.
func checkArg(a string) error {
	if strings.ContainsAny(a, "\x00\n\r") {
		return fmt.Errorf("%w: the argument %q contains a NUL or a newline", jobs.ErrRefused, a)
	}
	return nil
}

// tail is the last n bytes of b, trimmed, with a marker when it was cut.
func tail(b []byte, n int) string {
	s := strings.TrimSpace(string(b))
	if len(s) <= n {
		return s
	}
	return "…" + s[len(s)-n:]
}
