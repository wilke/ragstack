package gateway

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"
)

// RealExec runs a program by absolute path with an explicit argv and a
// sanitized environment. No shell, no PATH search, no inherited variables
// beyond the three apptainer needs to find its own state.
type RealExec struct {
	// Env, when set, replaces the sanitized default.
	Env []string
	// Timeout bounds one run; 0 means the context decides.
	Timeout time.Duration
}

// NewRealExec is the production Exec.
func NewRealExec() *RealExec { return &RealExec{Timeout: 60 * time.Second} }

// sanitizedEnv is the only environment a child sees: enough for apptainer to
// find its cache and config, nothing else. A tenant's secrets are in this
// process's environment and must not leak into a subprocess.
func sanitizedEnv() []string {
	keep := []string{"HOME", "PATH", "APPTAINER_CACHEDIR", "APPTAINER_CONFIGDIR", "TMPDIR", "LANG"}
	var out []string
	for _, k := range keep {
		if v, ok := os.LookupEnv(k); ok {
			out = append(out, k+"="+v)
		}
	}
	return out
}

// Run executes argv.
func (r *RealExec) Run(ctx context.Context, argv []string) ([]byte, []byte, error) {
	if len(argv) == 0 {
		return nil, nil, errors.New("gateway: empty argv")
	}
	if !filepath.IsAbs(argv[0]) {
		return nil, nil, fmt.Errorf("gateway: program %q must be an absolute path", argv[0])
	}
	if r.Timeout > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, r.Timeout)
		defer cancel()
	}
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	cmd.Env = r.Env
	if cmd.Env == nil {
		cmd.Env = sanitizedEnv()
	}
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	err := cmd.Run()
	return stdout.Bytes(), stderr.Bytes(), err
}

// RealSignaller reads /proc and sends signals. ProcRoot is injectable so the
// identity checks can be exercised against a fixture tree.
type RealSignaller struct {
	ProcRoot string
	UID      int
}

// NewRealSignaller is the production Signaller.
func NewRealSignaller() *RealSignaller { return &RealSignaller{ProcRoot: "/proc", UID: os.Getuid()} }

func (s *RealSignaller) procRoot() string {
	if s.ProcRoot == "" {
		return "/proc"
	}
	return s.ProcRoot
}

// Proc reports what /proc says about pid.
func (s *RealSignaller) Proc(pid int) (ProcInfo, error) {
	if pid <= 0 {
		return ProcInfo{}, fmt.Errorf("gateway: invalid pid %d", pid)
	}
	dir := filepath.Join(s.procRoot(), strconv.Itoa(pid))
	st, err := os.Stat(dir)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return ProcInfo{}, nil
		}
		return ProcInfo{}, err
	}
	info := ProcInfo{Exists: true, UID: -1}
	if sys, ok := st.Sys().(*syscall.Stat_t); ok {
		info.UID = int(sys.Uid)
	}
	if b, err := os.ReadFile(filepath.Join(dir, "comm")); err == nil {
		info.Comm = strings.TrimSpace(string(b))
	}
	if b, err := os.ReadFile(filepath.Join(dir, "cmdline")); err == nil {
		for _, a := range strings.Split(string(b), "\x00") {
			if a != "" {
				info.Cmdline = append(info.Cmdline, a)
			}
		}
	}
	if b, err := os.ReadFile(filepath.Join(dir, "stat")); err == nil {
		if _, start, ok := parseStat(b); ok {
			info.StartTime = start
		}
	}
	return info, nil
}

// parseStat pulls the parent pid (field 4) and the start time (field 22) out of
// /proc/<pid>/stat.
//
// It splits AFTER the last ')' rather than on every space: field 2 is the
// executable name in parentheses and may itself contain spaces and
// parentheses, which is why a naive Fields() on this file has been a source of
// bugs in every language that has one.
func parseStat(b []byte) (ppid int, startTime uint64, ok bool) {
	i := bytes.LastIndexByte(b, ')')
	if i < 0 {
		return 0, 0, false
	}
	// fields[0] is field 3 (state), so field N is fields[N-3].
	fields := strings.Fields(string(b[i+1:]))
	const ppidIdx, startIdx = 4 - 3, 22 - 3
	if len(fields) <= startIdx {
		return 0, 0, false
	}
	ppid, err := strconv.Atoi(fields[ppidIdx])
	if err != nil {
		return 0, 0, false
	}
	startTime, err2 := strconv.ParseUint(fields[startIdx], 10, 64)
	if err2 != nil {
		return ppid, 0, false
	}
	return ppid, startTime, true
}

// Workers lists the children of the nginx master, ascending.
//
// /proc is walked rather than `pgrep -P`: this package runs no shell, and the
// answer has to be right for a master owned by another account (every
// /proc/<pid>/stat is world-readable, /proc/<pid>/exe is not).
func (s *RealSignaller) Workers(masterPID int) ([]int, error) {
	if masterPID <= 0 {
		return nil, fmt.Errorf("gateway: invalid pid %d", masterPID)
	}
	ents, err := os.ReadDir(s.procRoot())
	if err != nil {
		return nil, err
	}
	var out []int
	for _, e := range ents {
		pid, err := strconv.Atoi(e.Name())
		if err != nil || pid <= 0 {
			continue
		}
		b, err := os.ReadFile(filepath.Join(s.procRoot(), e.Name(), "stat"))
		if err != nil {
			// The process exited between the readdir and the read. That is not
			// an error, it is a process that is no longer a worker.
			continue
		}
		if ppid, _, ok := parseStat(b); ok && ppid == masterPID {
			out = append(out, pid)
		}
	}
	sort.Ints(out)
	return out, nil
}

// Signal sends sig to pid.
func (s *RealSignaller) Signal(pid int, sig syscall.Signal) error {
	if pid <= 0 {
		return fmt.Errorf("gateway: invalid pid %d", pid)
	}
	return syscall.Kill(pid, sig)
}

// Self is this process's uid.
func (s *RealSignaller) Self() int { return s.UID }

// RealProber is a loopback-only HTTP GET with a short timeout and no redirect
// following: a probe asks what THIS gateway answers, never what it points at.
type RealProber struct{ Client *http.Client }

// NewRealProber is the production Prober.
func NewRealProber() *RealProber {
	return &RealProber{Client: &http.Client{
		Timeout:       5 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}}
}

// Get performs the GET.
func (p *RealProber) Get(ctx context.Context, url string) (int, []byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return 0, nil, err
	}
	resp, err := p.Client.Do(req)
	if err != nil {
		return 0, nil, err
	}
	defer resp.Body.Close()
	// Bounded: a probe reads a status line and a small JSON body, never a
	// stream someone points at it.
	body, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	return resp.StatusCode, body, err
}
