package gateway

import (
	"context"
	"fmt"
	"sync"
	"syscall"
)

// The fakes below are what makes a publish testable without a host: the whole
// flow — staged `nginx -t`, the pointer switch, the SIGHUP, the probes —
// runs against them, and NOTHING in a test touches the live proxy tree or
// signals a real process.

// FakeExec records argv and answers from a script.
type FakeExec struct {
	mu    sync.Mutex
	Calls [][]string
	// Run, when set, decides the answer for a call.
	Runner func(argv []string) (stdout, stderr []byte, err error)
}

// Run records the call and delegates to Runner (default: success, the string
// nginx prints when a configuration tests clean).
func (f *FakeExec) Run(_ context.Context, argv []string) ([]byte, []byte, error) {
	f.mu.Lock()
	f.Calls = append(f.Calls, append([]string(nil), argv...))
	runner := f.Runner
	f.mu.Unlock()
	if runner != nil {
		return runner(argv)
	}
	return nil, []byte("nginx: configuration file test is successful\n"), nil
}

// Count is how many times Run was called.
func (f *FakeExec) Count() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.Calls)
}

// FakeSignaller is an nginx master that exists only in the test.
type FakeSignaller struct {
	mu      sync.Mutex
	Info    ProcInfo
	UID     int
	Signals []syscall.Signal
	Err     error

	// FrozenWorkers makes Workers answer the same set no matter how many HUPs
	// were sent — which is what a master that REJECTED the configuration looks
	// like from outside: the signal was delivered, the old workers carry on.
	FrozenWorkers bool
	// WorkersErr, when set, is what Workers returns.
	WorkersErr error

	// workerCalls counts Workers lookups, which is how a test sees that the
	// reload was CONFIRMED rather than merely sent: confirmReload is the only
	// thing that samples the worker set after a HUP.
	workerCalls int
}

// NewFakeSignaller is a master owned by uid that looks like nginx.
func NewFakeSignaller(uid int) *FakeSignaller {
	return &FakeSignaller{
		Info: ProcInfo{Exists: true, Comm: "nginx", Cmdline: []string{"nginx: master process"}, UID: uid, StartTime: 1234567},
		UID:  uid,
	}
}

// Workers answers with a set derived from the number of HUPs delivered, so an
// accepted reload "spawns" new workers exactly as nginx does. FrozenWorkers
// pins the set to the pre-reload one.
func (f *FakeSignaller) Workers(int) ([]int, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.workerCalls++
	if f.WorkersErr != nil {
		return nil, f.WorkersErr
	}
	n := len(f.Signals)
	if f.FrozenWorkers {
		n = 0
	}
	return []int{9000 + 2*n, 9001 + 2*n}, nil
}

// Proc answers with the configured process.
func (f *FakeSignaller) Proc(int) (ProcInfo, error) { return f.Info, nil }

// Signal records the signal.
func (f *FakeSignaller) Signal(_ int, sig syscall.Signal) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.Err != nil {
		return f.Err
	}
	f.Signals = append(f.Signals, sig)
	return nil
}

// Self is the uid the publisher compares the master's against.
func (f *FakeSignaller) Self() int { return f.UID }

// Count is how many signals were sent.
func (f *FakeSignaller) Count() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.Signals)
}

// WorkersCount is how many times the worker set was sampled.
func (f *FakeSignaller) WorkersCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.workerCalls
}

// FakeProber answers GETs from a table keyed by request path.
type FakeProber struct {
	mu       sync.Mutex
	Bodies   map[string][]byte // path -> body
	Statuses map[string]int    // path -> status (default 200 when a body exists)
	Gets     []string
	Err      error
}

// Get answers from the table. An unknown path is a 404 with no body, which is
// what a gateway that does not know a route actually does.
func (f *FakeProber) Get(_ context.Context, url string) (int, []byte, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Gets = append(f.Gets, url)
	if f.Err != nil {
		return 0, nil, f.Err
	}
	path := url
	for _, prefix := range []string{"http://127.0.0.1:9000", "https://127.0.0.1:9443"} {
		if len(url) > len(prefix) && url[:len(prefix)] == prefix {
			path = url[len(prefix):]
		}
	}
	status, ok := f.Statuses[path]
	body := f.Bodies[path]
	if !ok {
		if body != nil {
			status = 200
		} else {
			status = 404
		}
	}
	return status, body, nil
}

// Count is how many GETs were made.
func (f *FakeProber) Count() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.Gets)
}

// String renders the table for a failure message.
func (f *FakeProber) String() string { return fmt.Sprintf("FakeProber(%d paths)", len(f.Bodies)) }
