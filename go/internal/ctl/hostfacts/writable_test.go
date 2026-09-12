package hostfacts

import (
	"context"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// TestWritableByOthersFollowsSymlinks is the /rag/bin/ragstack-ctl case.
// install-ctl lays the binary down as ragstack-ctl-<version> and points a
// symlink at it; a symlink's own mode is 0777 on Linux and says nothing about
// who may rewrite it, so lstat-ing the link reported the control plane's own
// binary as world-writable — a red finding, on every run, that blocked
// `handover` and `render-units` and that no chmod could ever clear.
func TestWritableByOthersFollowsSymlinks(t *testing.T) {
	root := t.TempDir()
	bin := filepath.Join(root, "bin")
	if err := os.MkdirAll(bin, 0o755); err != nil {
		t.Fatal(err)
	}
	real := filepath.Join(bin, "ragstack-ctl-1.2.3")
	if err := os.WriteFile(real, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(bin, "ragstack-ctl")
	if err := os.Symlink(real, link); err != nil {
		t.Fatal(err)
	}
	r := &Real{CheckRoot: root}

	w, err := r.WritableByOthers(link)
	if err != nil {
		t.Fatal(err)
	}
	if w.Writable {
		t.Fatalf("a symlink to a 0755 file is not writable by others: %+v", w)
	}

	// And the TARGET's mode is what decides: make the real binary group
	// writable and the same link must now be a finding naming the target.
	if err := os.Chmod(real, 0o775); err != nil {
		t.Fatal(err)
	}
	w, err = r.WritableByOthers(link)
	if err != nil {
		t.Fatal(err)
	}
	if !w.Writable || w.Path != real {
		t.Fatalf("a group-writable target must be reported, at the target: %+v", w)
	}
}

// TestCachedDUIsSingleFlight: `du` over a tenant tree runs for minutes and
// the dashboard polls every 15 s, so concurrent callers must join ONE run
// rather than each fork their own. Without this, a slow tree accumulated one
// `du` per poll until the IO being measured was the IO being caused.
func TestCachedDUIsSingleFlight(t *testing.T) {
	var runs int64
	c := NewCachedDU(time.Hour)
	c.measure = func(context.Context, string) (int64, error) {
		atomic.AddInt64(&runs, 1)
		time.Sleep(30 * time.Millisecond)
		return 4242, nil
	}
	var wg sync.WaitGroup
	for i := 0; i < 16; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			n, err := c.Usage(context.Background(), "/rag/data/tenants/asm")
			if err != nil || n != 4242 {
				t.Errorf("usage = %d, %v", n, err)
			}
		}()
	}
	wg.Wait()
	if got := atomic.LoadInt64(&runs); got != 1 {
		t.Errorf("%d du runs for 16 concurrent callers, want 1", got)
	}
	// The answer is then cached, so a later caller does not run it again.
	if _, err := c.Usage(context.Background(), "/rag/data/tenants/asm"); err != nil {
		t.Fatal(err)
	}
	if got := atomic.LoadInt64(&runs); got != 1 {
		t.Errorf("%d du runs after a cached read, want 1", got)
	}
}

// TestCachedDUCachesFailuresBriefly: a failed measurement is remembered for
// duFailureTTL, not for the full TTL, so a directory that was mid-creation
// recovers on its own without an operator clearing anything — but an
// unreadable path does not fork `du` on every single request either.
func TestCachedDUCachesFailuresBriefly(t *testing.T) {
	var runs int64
	c := NewCachedDU(time.Hour)
	c.measure = func(context.Context, string) (int64, error) {
		atomic.AddInt64(&runs, 1)
		return 0, os.ErrNotExist
	}
	for i := 0; i < 3; i++ {
		if _, err := c.Usage(context.Background(), "/rag/data/tenants/gone"); err == nil {
			t.Fatal("want the recorded failure")
		}
	}
	if got := atomic.LoadInt64(&runs); got != 1 {
		t.Errorf("%d runs for 3 calls over a failing path, want 1 (the failure is cached)", got)
	}
	if got := c.ttlFor(os.ErrNotExist); got != duFailureTTL {
		t.Errorf("failure ttl = %s, want %s", got, duFailureTTL)
	}
	if got := c.ttlFor(nil); got != time.Hour {
		t.Errorf("success ttl = %s, want the configured hour", got)
	}
}

// TestSplitHexAddrDecodesIPv4Mapped: a dual-stack listener that bound an IPv4
// address shows up in /proc/net/tcp6 as ::ffff:a.b.c.d. Printing the 32 raw
// hex digits made a plain 127.0.0.1 listener unreadable in every finding that
// quotes the address.
func TestSplitHexAddrDecodesIPv4Mapped(t *testing.T) {
	cases := map[string]struct {
		addr string
		port int
	}{
		"0100007F:5DF6":                         {"127.0.0.1", 24054},
		"00000000:2328":                         {"0.0.0.0", 9000},
		"00000000000000000000000000000000:1F90": {"::", 8080},
		"00000000000000000000000001000000:1F90": {"::1", 8080},
		// ::ffff:127.0.0.1 — words 0,1 zero, word 2 = 0x0000FFFF little
		// endian, word 3 = the IPv4 address little endian.
		"0000000000000000FFFF00000100007F:5DF6": {"127.0.0.1", 24054},
		"0000000000000000FFFF000000000000:5DF6": {"0.0.0.0", 24054},
	}
	for in, want := range cases {
		addr, port, ok := splitHexAddr(in)
		if !ok || addr != want.addr || port != want.port {
			t.Errorf("splitHexAddr(%q) = %q, %d, %v; want %q, %d", in, addr, port, ok, want.addr, want.port)
		}
	}
}

// TestListenersResolveOverAFakeProc guards the early-exit added to the /proc
// scan: the loop now stops as soon as every socket has an owner (on a host
// with thousands of processes the remaining fd scans cannot change the
// answer, and each one is a directory read), so the correctness it short-cuts
// needs an assertion of its own.
func TestListenersResolveOverAFakeProc(t *testing.T) {
	proc := t.TempDir()
	write := func(path, body string) {
		t.Helper()
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	header := "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
	write(filepath.Join(proc, "net", "tcp"), header+
		"   0: 0100007F:5DF8 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 900001 1 0 100 0 0 10 0\n"+
		"   1: 0100007F:5DF9 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 900002 1 0 100 0 0 10 0\n")

	// Two pids own one socket each, plus a decoy pid with no sockets that
	// sorts AFTER them — if the early exit fired too eagerly the second
	// socket would come back unowned.
	for pid, inode := range map[string]string{"101": "900001", "102": "900002"} {
		fd := filepath.Join(proc, pid, "fd")
		if err := os.MkdirAll(fd, 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.Symlink("socket:["+inode+"]", filepath.Join(fd, "3")); err != nil {
			t.Fatal(err)
		}
		write(filepath.Join(proc, pid, "cmdline"), "python\x00-m\x00uvicorn\x00")
	}
	if err := os.MkdirAll(filepath.Join(proc, "999", "fd"), 0o755); err != nil {
		t.Fatal(err)
	}

	r := &Real{ProcRoot: proc}
	got, err := r.Listeners()
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 {
		t.Fatalf("got %d listeners, want 2: %+v", len(got), got)
	}
	for _, l := range got {
		if l.Pid == 0 {
			t.Errorf(":%d came back unowned; the scan stopped too early: %+v", l.Port, l)
		}
		if l.Addr != "127.0.0.1" {
			t.Errorf(":%d addr = %q, want 127.0.0.1", l.Port, l.Addr)
		}
	}
	if got[0].Port != 24056 || got[1].Port != 24057 {
		t.Errorf("ports = %d, %d; want 24056, 24057 (sorted)", got[0].Port, got[1].Port)
	}
}
