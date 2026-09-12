package gateway

import (
	"context"
	"syscall"
)

// The three host seams a publish needs. Each is small, argv- or value-shaped,
// and has a fake in fake.go — a publish test never runs apptainer, never
// signals a process and never opens a socket.

// Exec runs one program. argv[0] is an absolute program path; there is no
// shell, no PATH lookup by the caller's environment and no string command
// line, because every argument here is built from host paths.
type Exec interface {
	Run(ctx context.Context, argv []string) (stdout, stderr []byte, err error)
}

// ProcInfo is what /proc says about a pid. Exists is false for a pid that is
// gone — which is not an error, it is the answer.
type ProcInfo struct {
	Exists  bool
	Comm    string
	Cmdline []string
	UID     int
	// StartTime is field 22 of /proc/<pid>/stat, in clock ticks since boot.
	// Together with the pid it IDENTIFIES a process: a pid can be reused
	// within a publish, and "the master is still the one I signalled" is only
	// answerable by comparing this.
	StartTime uint64
}

// Signaller identifies and signals the nginx master. Identify first, signal
// second: a pidfile is a file, and the pid in it may have been reused.
type Signaller interface {
	Proc(pid int) (ProcInfo, error)
	Signal(pid int, sig syscall.Signal) error
	// Self is the uid this process runs as; a HUP to a master owned by anyone
	// else cannot work and must be refused with the account to use.
	Self() int
	// Workers lists the pids whose parent is the nginx master, ascending.
	//
	// It is how a reload is CONFIRMED. nginx answers a SIGHUP it cannot use by
	// logging `[emerg]`, keeping the old configuration and carrying on — the
	// master does not exit and kill(2) reported success, so nothing about the
	// signal itself says whether the new configuration was taken. What does
	// say so is the worker set: a reload nginx accepted spawns new workers and
	// retires the old ones, and a reload it rejected leaves the set untouched.
	Workers(masterPID int) ([]int, error)
}

// Prober performs one HTTP GET against the gateway. It returns the status and
// the body because the PR-B go/no-go compares four bodies byte-for-byte.
type Prober interface {
	Get(ctx context.Context, url string) (status int, body []byte, err error)
}

// Signals used by the publisher, named so the call sites read as intent.
const (
	sigHUP = syscall.SIGHUP
)
