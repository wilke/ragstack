package hostfacts

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
)

// Fake is a Host + Prober + DiskUsage backed by recorded facts. It is the
// only way adopt, doctor and fleet are tested: the live host is not a
// fixture, and the 2026-09-10 capture under testdata/live-2026-09-10 is.
//
// Every field is a plain map or slice so a test can state exactly the fact it
// is about and leave the rest at the zero value (which reads as "absent").
type Fake struct {
	Self        string // Username(); "" ⇒ "wilke"
	Ports       []Listener
	Env         map[int]map[string]string // pid → already SAFE_ENV-filtered env
	Lingering   map[string]bool
	RuntimeDirs map[int]bool
	DropIns     map[int]DropIn
	MaxMapCount int
	MemTotal    int64
	Free        map[string]int64 // path → free bytes (longest matching prefix)
	DU          map[string]int64 // path → recursive `du` bytes
	Groups      map[string][]string
	Units       map[string]UnitStatus // "<user>|<unit>" → status
	Writables   map[string]Writability
	Gitdirs     map[string]Gitdir
	Describes   map[string]string
	Errs        map[string]error // "describe:<worktree>", "listeners", "maxmapcount", …

	ProbeStatus map[string]int          // url → status code
	Collections map[string]StoreListing // qdrant base url → listing
	Indices     map[string]StoreListing // es base url → listing
}

var (
	_ Host      = (*Fake)(nil)
	_ Prober    = (*Fake)(nil)
	_ DiskUsage = (*Fake)(nil)
)

// Username returns the recorded account (default "wilke", the account that
// owns every adopted tenant today).
func (f *Fake) Username() string {
	if f.Self == "" {
		return "wilke"
	}
	return f.Self
}

// Listeners returns the recorded listening sockets.
func (f *Fake) Listeners() ([]Listener, error) {
	if err := f.Errs["listeners"]; err != nil {
		return nil, err
	}
	out := make([]Listener, len(f.Ports))
	copy(out, f.Ports)
	return out, nil
}

// ProcEnv returns the recorded environment of pid, filtered through allow.
func (f *Fake) ProcEnv(pid int, allow func(string) bool) (map[string]string, error) {
	if allow == nil {
		allow = SafeEnvAllowed
	}
	env, ok := f.Env[pid]
	if !ok {
		return nil, fmt.Errorf("hostfacts: no recorded environ for pid %d", pid)
	}
	out := map[string]string{}
	for k, v := range env {
		if allow(k) {
			out[k] = v
		}
	}
	return out, nil
}

// Linger reports the recorded linger state.
func (f *Fake) Linger(user string) bool { return f.Lingering[user] }

// RuntimeDir reports the recorded /run/user/<uid> state.
func (f *Fake) RuntimeDir(uid int) bool { return f.RuntimeDirs[uid] }

// UserDropIn returns the recorded drop-in.
func (f *Fake) UserDropIn(uid int) (DropIn, error) { return f.DropIns[uid], nil }

// SysctlMaxMapCount returns the recorded sysctl.
func (f *Fake) SysctlMaxMapCount() (int, error) {
	return f.MaxMapCount, f.Errs["maxmapcount"]
}

// MemTotalBytes returns the recorded MemTotal.
func (f *Fake) MemTotalBytes() (int64, error) { return f.MemTotal, f.Errs["memtotal"] }

// DiskFree returns the recorded free bytes for the longest recorded prefix of
// path, so a test can state one number for "/rag".
func (f *Fake) DiskFree(path string) (int64, error) {
	best, bestLen := int64(0), -1
	for p, v := range f.Free {
		if (path == p || strings.HasPrefix(path, strings.TrimSuffix(p, "/")+"/")) && len(p) > bestLen {
			best, bestLen = v, len(p)
		}
	}
	if bestLen < 0 {
		return 0, fmt.Errorf("hostfacts: no recorded free space for %s", path)
	}
	return best, nil
}

// SudoersGroupMembers returns the recorded group members.
func (f *Fake) SudoersGroupMembers(group string) ([]string, error) {
	m, ok := f.Groups[group]
	if !ok {
		return nil, fmt.Errorf("hostfacts: no recorded group %s", group)
	}
	return append([]string(nil), m...), nil
}

// UnitState returns the recorded unit status (StateUnknown when unrecorded,
// which is also what the live host answers for another user's manager).
func (f *Fake) UnitState(user, unit string) UnitStatus {
	if st, ok := f.Units[user+"|"+unit]; ok {
		return st
	}
	return UnitStatus{ActiveState: StateUnknown}
}

// WritableByOthers returns the recorded verdict for path (default: not
// writable by others).
func (f *Fake) WritableByOthers(path string) (Writability, error) {
	return f.Writables[path], nil
}

// Gitdir returns the recorded gitdir (default: unreadable).
func (f *Fake) Gitdir(worktree string) (Gitdir, error) {
	g, ok := f.Gitdirs[worktree]
	if !ok {
		return Gitdir{Location: GitdirUnreadable}, nil
	}
	return g, nil
}

// GitDescribe returns the recorded describe output or error.
func (f *Fake) GitDescribe(worktree string) (string, error) {
	if err := f.Errs["describe:"+worktree]; err != nil {
		return "", err
	}
	d, ok := f.Describes[worktree]
	if !ok {
		return "", fmt.Errorf("hostfacts: no recorded describe for %s", worktree)
	}
	return d, nil
}

// Usage returns the recorded du size.
func (f *Fake) Usage(_ context.Context, path string) (int64, error) {
	v, ok := f.DU[path]
	if !ok {
		return 0, fmt.Errorf("hostfacts: no recorded usage for %s", path)
	}
	return v, nil
}

// Probe returns the recorded status for url (0 + error when unrecorded: the
// same shape as a refused connection).
func (f *Fake) Probe(_ context.Context, url string) (int, error) {
	if s, ok := f.ProbeStatus[url]; ok {
		return s, nil
	}
	return 0, fmt.Errorf("hostfacts: connection refused (no recorded probe for %s)", url)
}

// QdrantCollections returns the recorded listing.
func (f *Fake) QdrantCollections(_ context.Context, url string) (StoreListing, error) {
	if l, ok := f.Collections[strings.TrimSuffix(url, "/")]; ok {
		return l, nil
	}
	return StoreListing{}, fmt.Errorf("hostfacts: no recorded collections for %s", url)
}

// ESIndices returns the recorded listing.
func (f *Fake) ESIndices(_ context.Context, url string) (StoreListing, error) {
	if l, ok := f.Indices[strings.TrimSuffix(url, "/")]; ok {
		return l, nil
	}
	return StoreListing{}, fmt.Errorf("hostfacts: no recorded indices for %s", url)
}

// procRecord is one row of testdata/live-2026-09-10/procs.json: the listening
// port, the process behind it, and its SAFE_ENV environment. A row with a
// null pid is a socket owned by an account the capture could not see.
type procRecord struct {
	Port    int      `json:"port"`
	Pid     *int     `json:"pid"`
	User    *string  `json:"user"`
	Cmdline *string  `json:"cmdline"`
	Cwd     *string  `json:"cwd"`
	SafeEnv []string `json:"safe_env"`
}

// LoadFake builds a Fake from a capture directory (procs.json + listen.txt,
// as ops/coconut/snapshot.sh and go/internal/ctl/testdata/capture.sh write
// them). Everything else — linger, groups, units, probes — is left empty for
// the test to state.
func LoadFake(dir string) (*Fake, error) {
	f := &Fake{
		Self: "wilke",
		Env:  map[int]map[string]string{},
	}
	b, err := os.ReadFile(filepath.Join(dir, "procs.json"))
	if err != nil {
		return nil, err
	}
	var procs []procRecord
	if err := json.Unmarshal(b, &procs); err != nil {
		return nil, fmt.Errorf("%s/procs.json: %w", dir, err)
	}
	byPort := map[int]Listener{}
	for _, p := range procs {
		l := Listener{Port: p.Port, UID: -1}
		if p.Pid != nil {
			l.Pid = *p.Pid
		}
		if p.User != nil {
			l.User = *p.User
		}
		if p.Cmdline != nil {
			l.Cmdline = strings.Fields(*p.Cmdline)
		}
		if p.Cwd != nil {
			l.Cwd = *p.Cwd
		}
		byPort[p.Port] = l
		if l.Pid != 0 {
			env := f.Env[l.Pid]
			if env == nil {
				env = map[string]string{}
				f.Env[l.Pid] = env
			}
			for _, kv := range p.SafeEnv {
				if k, v, ok := strings.Cut(kv, "="); ok {
					env[k] = v
				}
			}
		}
	}
	// listen.txt is the authoritative port list (it includes sockets procs.json
	// has no row for) and carries the bind address.
	if lb, err := os.ReadFile(filepath.Join(dir, "listen.txt")); err == nil {
		for _, line := range strings.Split(string(lb), "\n") {
			line = strings.TrimSpace(line)
			if line == "" {
				continue
			}
			i := strings.LastIndex(line, ":")
			if i < 0 {
				continue
			}
			port, err := strconv.Atoi(line[i+1:])
			if err != nil {
				continue
			}
			addr := line[:i]
			switch addr {
			case "*":
				addr = "0.0.0.0"
			case "[::]":
				addr = "::"
			}
			l, ok := byPort[port]
			if !ok {
				l = Listener{Port: port, UID: -1}
			}
			l.Addr = addr
			byPort[port] = l
		}
	}
	ports := make([]int, 0, len(byPort))
	for p := range byPort {
		ports = append(ports, p)
	}
	sort.Ints(ports)
	for _, p := range ports {
		f.Ports = append(f.Ports, byPort[p])
	}
	return f, nil
}
