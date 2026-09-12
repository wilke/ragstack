package hostfacts

import (
	"bufio"
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"os/user"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/ragstack/ragstack/internal/ctl/paths"
)

// Program paths. Absolute and fixed: the ctl never resolves a program name
// through PATH and never passes one through a shell.
const (
	binGetent    = "/usr/bin/getent"
	binGit       = "/usr/bin/git"
	binSystemctl = "/usr/bin/systemctl"
	binDu        = "/usr/bin/du"
)

// cmdTimeout bounds every subprocess this package runs.
const cmdTimeout = 2 * time.Second

// OwnerGroup is the group that may own the ctl's files. A path writable by
// any other group is a doctor finding.
const OwnerGroup = "ragops"

// Real probes the live host.
type Real struct {
	// ProcRoot is /proc (overridable for tests of the parsers).
	ProcRoot string
	// MirrorDir is the bare mirror worktrees are expected to point into.
	MirrorDir string
	// ArtifactsDir is the prepared-artifact root (the second trusted gitdir).
	ArtifactsDir string
	// CheckRoot bounds WritableByOthers: only path components at or below it
	// are examined. Default "/" — the whole ancestry, which is what the plan's
	// permission table asks for. A caller that has already vetted a prefix
	// (or a test that cannot control /tmp) narrows it.
	CheckRoot string

	once      sync.Once
	self      string
	ragopsGID int
}

// NewReal returns a Real for the deployment at roots.
func NewReal(roots paths.Roots) *Real {
	return &Real{
		ProcRoot:     "/proc",
		MirrorDir:    filepath.Join(roots.RagRoot, "repos", "ragstack.git"),
		ArtifactsDir: filepath.Join(roots.CtlStateDir, "artifacts"),
	}
}

var _ Host = (*Real)(nil)

func (r *Real) init() {
	r.once.Do(func() {
		r.ragopsGID = -1
		if u, err := user.Current(); err == nil {
			r.self = u.Username
		}
		if out, err := runArgv(binGetent, "group", OwnerGroup); err == nil {
			if f := strings.Split(strings.TrimSpace(out), ":"); len(f) >= 3 {
				if gid, err := strconv.Atoi(f[2]); err == nil {
					r.ragopsGID = gid
				}
			}
		}
	})
}

func (r *Real) proc() string {
	if r.ProcRoot == "" {
		return "/proc"
	}
	return r.ProcRoot
}

// Username returns the account this process runs as.
func (r *Real) Username() string {
	r.init()
	return r.self
}

// ---------------------------------------------------------------- listeners

// listenState is the TCP_LISTEN value /proc/net/tcp spells in hex.
const listenState = "0A"

// Listeners parses /proc/net/tcp and /proc/net/tcp6 for listening sockets and
// joins them to processes through /proc/<pid>/fd. A socket whose owner this
// account cannot see keeps Pid 0 — that is the honest answer, not an error.
func (r *Real) Listeners() ([]Listener, error) {
	var found []Listener
	byInode := map[uint64]int{} // inode → index in found
	for _, name := range []string{"net/tcp", "net/tcp6"} {
		b, err := os.ReadFile(filepath.Join(r.proc(), name))
		if err != nil {
			if errors.Is(err, os.ErrNotExist) {
				continue
			}
			return nil, err
		}
		for _, l := range parseNetTCP(b) {
			if _, dup := byInode[l.Inode]; dup {
				continue
			}
			l.UID = -1
			byInode[l.Inode] = len(found)
			found = append(found, l)
		}
	}
	if len(found) == 0 {
		return nil, nil
	}
	// One pass over /proc: read each process's fd links and claim the
	// sockets it owns. Unreadable processes are skipped silently — they
	// belong to another account, which is itself a fact doctor reports.
	// The pass STOPS as soon as every socket has an owner: on a host with
	// thousands of processes the remaining fd scans cannot change the
	// answer, and each one is a directory read.
	entries, err := os.ReadDir(r.proc())
	if err != nil {
		return nil, err
	}
	unresolved := len(found)
	for _, e := range entries {
		if unresolved == 0 {
			break
		}
		pid, err := strconv.Atoi(e.Name())
		if err != nil || pid <= 0 {
			continue
		}
		fdDir := filepath.Join(r.proc(), e.Name(), "fd")
		fds, err := os.ReadDir(fdDir)
		if err != nil {
			continue
		}
		var claimed []int
		for _, fd := range fds {
			target, err := os.Readlink(filepath.Join(fdDir, fd.Name()))
			if err != nil || !strings.HasPrefix(target, "socket:[") {
				continue
			}
			ino, err := strconv.ParseUint(strings.TrimSuffix(strings.TrimPrefix(target, "socket:["), "]"), 10, 64)
			if err != nil {
				continue
			}
			if idx, ok := byInode[ino]; ok && found[idx].Pid == 0 {
				claimed = append(claimed, idx)
			}
		}
		if len(claimed) == 0 {
			continue
		}
		uid, uname := r.procOwner(pid)
		cmd := r.procCmdline(pid)
		cwd, _ := os.Readlink(filepath.Join(r.proc(), e.Name(), "cwd"))
		for _, idx := range claimed {
			found[idx].Pid = pid
			found[idx].UID = uid
			found[idx].User = uname
			found[idx].Cmdline = cmd
			found[idx].Cwd = cwd
			unresolved--
		}
	}
	sort.Slice(found, func(i, j int) bool { return found[i].Port < found[j].Port })
	return found, nil
}

// parseNetTCP extracts the listening rows of one /proc/net/tcp[6] file.
func parseNetTCP(b []byte) []Listener {
	var out []Listener
	sc := bufio.NewScanner(bytes.NewReader(b))
	sc.Buffer(make([]byte, 0, 64*1024), 1<<20)
	first := true
	for sc.Scan() {
		if first { // header
			first = false
			continue
		}
		f := strings.Fields(sc.Text())
		if len(f) < 10 || f[3] != listenState {
			continue
		}
		addr, port, ok := splitHexAddr(f[1])
		if !ok {
			continue
		}
		inode, err := strconv.ParseUint(f[9], 10, 64)
		if err != nil {
			continue
		}
		out = append(out, Listener{Port: port, Addr: addr, Inode: inode})
	}
	return out
}

// splitHexAddr decodes a /proc/net/tcp local_address ("0100007F:1F90" or the
// 32-hex IPv6 form) into a printable address and a port.
func splitHexAddr(s string) (addr string, port int, ok bool) {
	i := strings.LastIndex(s, ":")
	if i < 0 {
		return "", 0, false
	}
	p, err := strconv.ParseUint(s[i+1:], 16, 32)
	if err != nil {
		return "", 0, false
	}
	hexAddr := s[:i]
	switch len(hexAddr) {
	case 8: // IPv4, little-endian word
		v4, ok := decodeV4(hexAddr)
		if !ok {
			return "", 0, false
		}
		addr = v4
	case 32: // IPv6, four little-endian 32-bit words
		up := strings.ToUpper(hexAddr)
		switch {
		case up == strings.Repeat("0", 32):
			addr = "::"
		case strings.HasPrefix(up, strings.Repeat("0", 24)) && strings.HasSuffix(up, "01000000"):
			addr = "::1"
		case strings.HasPrefix(up, strings.Repeat("0", 16)+"FFFF0000"):
			// An IPv4-mapped address (::ffff:a.b.c.d): what a dual-stack
			// listener that actually bound an IPv4 address looks like in
			// /proc/net/tcp6. Printing the 32 hex digits made a plain
			// 127.0.0.1 listener unreadable in every finding that quotes it.
			v4, ok := decodeV4(up[24:])
			if !ok {
				return "", 0, false
			}
			addr = v4
		default:
			addr = "[" + hexAddr + "]"
		}
	default:
		return "", 0, false
	}
	return addr, int(p), true
}

// decodeV4 turns one little-endian 32-bit word of /proc hex into dotted quad.
func decodeV4(hex8 string) (string, bool) {
	v, err := strconv.ParseUint(hex8, 16, 64)
	if err != nil {
		return "", false
	}
	return fmt.Sprintf("%d.%d.%d.%d", v&0xff, (v>>8)&0xff, (v>>16)&0xff, (v>>24)&0xff), true
}

func (r *Real) procOwner(pid int) (int, string) {
	fi, err := os.Stat(filepath.Join(r.proc(), strconv.Itoa(pid)))
	uid := -1
	if err == nil {
		if st, ok := fi.Sys().(*syscall.Stat_t); ok {
			uid = int(st.Uid)
		}
	}
	if uid < 0 { // fall back to /proc/<pid>/status, which is readable by all
		b, err := os.ReadFile(filepath.Join(r.proc(), strconv.Itoa(pid), "status"))
		if err == nil {
			for _, line := range strings.Split(string(b), "\n") {
				if strings.HasPrefix(line, "Uid:") {
					f := strings.Fields(line)
					if len(f) >= 2 {
						uid, _ = strconv.Atoi(f[1])
					}
					break
				}
			}
		}
	}
	if uid < 0 {
		return -1, ""
	}
	return uid, UsernameOf(uid)
}

// UsernameOf resolves a uid to a name. The ctl is a CGO-free static binary,
// so Go's own lookup reads /etc/passwd ONLY — every LDAP/NSS account (which
// on coconut includes the service account) is invisible to it. getent asks
// NSS, so it is the fallback, not the other way round.
func UsernameOf(uid int) string {
	if u, err := user.LookupId(strconv.Itoa(uid)); err == nil {
		return u.Username
	}
	out, err := runArgv(binGetent, "passwd", strconv.Itoa(uid))
	if err != nil {
		return ""
	}
	if f := strings.Split(strings.TrimSpace(out), ":"); len(f) > 0 {
		return f[0]
	}
	return ""
}

// LookupUID resolves a username to its uid, with the same NSS fallback.
// Returns -1 when the account does not exist on this host.
func LookupUID(username string) int {
	if u, err := user.Lookup(username); err == nil {
		if uid, err := strconv.Atoi(u.Uid); err == nil {
			return uid
		}
	}
	out, err := runArgv(binGetent, "passwd", username)
	if err != nil {
		return -1
	}
	f := strings.Split(strings.TrimSpace(out), ":")
	if len(f) < 3 {
		return -1
	}
	uid, err := strconv.Atoi(f[2])
	if err != nil {
		return -1
	}
	return uid
}

func (r *Real) procCmdline(pid int) []string {
	b, err := os.ReadFile(filepath.Join(r.proc(), strconv.Itoa(pid), "cmdline"))
	if err != nil {
		return nil
	}
	parts := strings.Split(strings.TrimRight(string(b), "\x00"), "\x00")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		if p != "" {
			out = append(out, p)
		}
	}
	return out
}

// ------------------------------------------------------------------ proc env

// safeEnvPattern is ops/coconut/snapshot.sh's SAFE_ENV allowlist, verbatim.
var safeEnvPattern = regexp.MustCompile(`^(CUDA_VISIBLE_DEVICES|HF_HOME|VLLM_CACHE_ROOT|PYTHONPATH|PORT|PYTHONUNBUFFERED|` +
	`QDRANT__[A-Z_]+|ES_JAVA_OPTS|NEO4J_server_[a-z_]+|PGDATA|POSTGRES_USER|POSTGRES_DB|` +
	`MODEL_NAME|DEVICE|VITE_[A-Z_]+|GF_SERVER_[A-Z_]+|GOWE_[A-Z_]+|HOME|VIRTUAL_ENV)$`)

// secretHintPattern is snapshot.sh's SECRET_HINT: a key naming any of these
// never leaves the process, allowlisted or not (GOWE_TOKEN is the case that
// makes the second filter necessary).
var secretHintPattern = regexp.MustCompile(`(?i)(PASS|SECRET|TOKEN|KEY|AUTH)`)

// SafeEnvAllowed is the env-key predicate ops/coconut/snapshot.sh applies
// before it writes a process environment to disk: on the allowlist AND not
// secret-hinted. It is the only predicate adopt uses.
func SafeEnvAllowed(key string) bool {
	if !safeEnvPattern.MatchString(key) {
		return false
	}
	return !secretHintPattern.MatchString(strings.ReplaceAll(key, "VISIBLE_DEVICES", ""))
}

// ProcEnv reads /proc/<pid>/environ and keeps the keys allow accepts. A nil
// allow means SafeEnvAllowed.
func (r *Real) ProcEnv(pid int, allow func(string) bool) (map[string]string, error) {
	if allow == nil {
		allow = SafeEnvAllowed
	}
	b, err := os.ReadFile(filepath.Join(r.proc(), strconv.Itoa(pid), "environ"))
	if err != nil {
		return nil, err
	}
	out := map[string]string{}
	for _, kv := range strings.Split(string(b), "\x00") {
		k, v, ok := strings.Cut(kv, "=")
		if !ok || !allow(k) {
			continue
		}
		out[k] = v
	}
	return out, nil
}

// ------------------------------------------------------------------- systemd

// Linger reports whether the user manager of user survives logout.
func (r *Real) Linger(username string) bool {
	_, err := os.Stat(filepath.Join("/var/lib/systemd/linger", username))
	return err == nil
}

// RuntimeDir reports whether /run/user/<uid> exists.
func (r *Real) RuntimeDir(uid int) bool {
	fi, err := os.Stat(filepath.Join("/run/user", strconv.Itoa(uid)))
	return err == nil && fi.IsDir()
}

// UserDropIn parses every .conf under /etc/systemd/system/user@<uid>.service.d
// for the two settings the plan's boot persistence depends on.
func (r *Real) UserDropIn(uid int) (DropIn, error) {
	dir := fmt.Sprintf("/etc/systemd/system/user@%d.service.d", uid)
	matches, err := filepath.Glob(filepath.Join(dir, "*.conf"))
	if err != nil {
		return DropIn{}, err
	}
	sort.Strings(matches)
	var d DropIn
	for _, m := range matches {
		b, err := os.ReadFile(m)
		if err != nil {
			continue
		}
		d.Present = true
		d.Files = append(d.Files, m)
		for _, line := range strings.Split(string(b), "\n") {
			line = strings.TrimSpace(line)
			switch {
			case strings.HasPrefix(line, "Environment="):
				v := strings.Trim(strings.TrimPrefix(line, "Environment="), `"`)
				if k, val, ok := strings.Cut(v, "="); ok && k == "SYSTEMD_UNIT_PATH" {
					d.SystemdUnitPath = val
				}
			case strings.HasPrefix(line, "RequiresMountsFor="):
				d.RequiresMountsFor = append(d.RequiresMountsFor, strings.Fields(strings.TrimPrefix(line, "RequiresMountsFor="))...)
			}
		}
	}
	return d, nil
}

// UnitState asks the CURRENT user's manager about unit. Asking another
// user's manager would need sudo or a bus the ctl does not have, and the plan
// forbids sudo in every code path — so that answers StateUnknown.
func (r *Real) UnitState(username, unit string) UnitStatus {
	r.init()
	if username != "" && username != r.self {
		return UnitStatus{ActiveState: StateUnknown}
	}
	out, err := runArgv(binSystemctl, "--user", "show", unit, "-p", "ActiveState", "-p", "SubState", "-p", "NRestarts")
	if err != nil {
		return UnitStatus{ActiveState: StateUnknown}
	}
	st := UnitStatus{ActiveState: StateUnknown}
	for _, line := range strings.Split(out, "\n") {
		k, v, ok := strings.Cut(strings.TrimSpace(line), "=")
		if !ok {
			continue
		}
		switch k {
		case "ActiveState":
			st.ActiveState = v
		case "SubState":
			st.SubState = v
		case "NRestarts":
			st.NRestarts, _ = strconv.Atoi(v)
		}
	}
	return st
}

// ---------------------------------------------------------------- host facts

// SysctlMaxMapCount reads vm.max_map_count (Elasticsearch needs ≥ 262144).
func (r *Real) SysctlMaxMapCount() (int, error) {
	b, err := os.ReadFile("/proc/sys/vm/max_map_count")
	if err != nil {
		return 0, err
	}
	return strconv.Atoi(strings.TrimSpace(string(b)))
}

// MemTotalBytes reads MemTotal from /proc/meminfo.
func (r *Real) MemTotalBytes() (int64, error) {
	b, err := os.ReadFile("/proc/meminfo")
	if err != nil {
		return 0, err
	}
	for _, line := range strings.Split(string(b), "\n") {
		if !strings.HasPrefix(line, "MemTotal:") {
			continue
		}
		f := strings.Fields(line)
		if len(f) < 2 {
			break
		}
		kb, err := strconv.ParseInt(f[1], 10, 64)
		if err != nil {
			return 0, err
		}
		return kb * 1024, nil
	}
	return 0, errors.New("hostfacts: no MemTotal in /proc/meminfo")
}

// DiskFree returns the bytes available to an unprivileged writer on the
// filesystem holding path.
func (r *Real) DiskFree(path string) (int64, error) {
	var st syscall.Statfs_t
	if err := syscall.Statfs(path, &st); err != nil {
		return 0, fmt.Errorf("statfs %s: %w", path, err)
	}
	return int64(st.Bavail) * st.Bsize, nil
}

// SudoersGroupMembers lists the members of group (ADR-0007's fleet admins).
// Usernames are not secrets; the group's own gid is not returned.
func (r *Real) SudoersGroupMembers(group string) ([]string, error) {
	out, err := runArgv(binGetent, "group", group)
	if err != nil {
		return nil, err
	}
	f := strings.Split(strings.TrimSpace(out), ":")
	if len(f) < 4 || f[3] == "" {
		return []string{}, nil
	}
	members := strings.Split(f[3], ",")
	sort.Strings(members)
	return members, nil
}

// WritableByOthers walks every component of path and reports the first that
// is writable by other, or group-writable by a group other than ragops. The
// plan's permission table is what this enforces: a unit file, a binary, a SIF
// or a data dir that someone outside ragops can rewrite is a red finding.
//
// Symlinks are RESOLVED first and the TARGET's ancestry is what is walked. A
// symlink's own mode is 0777 on Linux and means nothing (the containing
// directory governs it), so lstat-ing /rag/bin/ragstack-ctl — a symlink to
// the versioned binary, which is exactly how install-ctl lays it out —
// reported the ctl's own binary as world-writable on every single run.
func (r *Real) WritableByOthers(path string) (Writability, error) {
	r.init()
	if !filepath.IsAbs(path) {
		return Writability{}, fmt.Errorf("hostfacts: %q is not absolute", path)
	}
	clean := filepath.Clean(path)
	root := r.CheckRoot
	if root == "" {
		root = "/"
	}
	root = filepath.Clean(root)
	if !under(clean, root) {
		return Writability{}, fmt.Errorf("hostfacts: %q is not under the check root %q", clean, root)
	}
	// EvalSymlinks fails on a path that does not exist (or a dangling link);
	// the literal path is then the honest thing to walk.
	target := clean
	if resolved, err := filepath.EvalSymlinks(clean); err == nil {
		target = resolved
	}
	base := root
	if !under(target, base) {
		// The link leaves the check root. Its target's ancestry is still
		// what an attacker would rewrite, so walk it from /.
		base = "/"
	}
	comps := []string{base}
	cur := base
	rest := strings.TrimPrefix(strings.TrimPrefix(target, strings.TrimSuffix(base, "/")), "/")
	for _, part := range strings.Split(rest, "/") {
		if part == "" {
			continue
		}
		cur = filepath.Join(cur, part)
		comps = append(comps, cur)
	}
	for _, c := range comps {
		fi, err := os.Lstat(c)
		if err != nil {
			if errors.Is(err, os.ErrNotExist) {
				continue // a path that does not exist cannot be rewritten
			}
			return Writability{}, err
		}
		if fi.Mode()&os.ModeSymlink != 0 {
			// A component EvalSymlinks could not flatten (it raced, or the
			// target is gone). Its 0777 mode says nothing about who may
			// rewrite it, so skip the mode test rather than raise a finding
			// no operator can act on.
			continue
		}
		mode := fi.Mode().Perm()
		if mode&0o002 != 0 {
			return Writability{Writable: true, Path: c, Reason: fmt.Sprintf("world-writable (mode %#o)", mode)}, nil
		}
		if mode&0o020 == 0 {
			continue
		}
		st, ok := fi.Sys().(*syscall.Stat_t)
		if !ok {
			continue
		}
		if r.ragopsGID >= 0 && int(st.Gid) == r.ragopsGID {
			continue
		}
		name := groupName(st.Gid)
		return Writability{Writable: true, Path: c, Reason: fmt.Sprintf("group-writable by %s (mode %#o), not %s", name, mode, OwnerGroup)}, nil
	}
	return Writability{}, nil
}

// groupName resolves a gid, with the same NSS fallback as UsernameOf.
func groupName(gid uint32) string {
	id := strconv.FormatUint(uint64(gid), 10)
	if g, err := user.LookupGroupId(id); err == nil {
		return g.Name
	}
	if out, err := runArgv(binGetent, "group", id); err == nil {
		if f := strings.Split(strings.TrimSpace(out), ":"); len(f) > 0 && f[0] != "" {
			return f[0]
		}
	}
	return "gid " + id
}

// ----------------------------------------------------------------------- git

// Gitdir resolves <worktree>/.git — a directory for a plain clone, a
// `gitdir: …` pointer file for a worktree — and says whether it lives in the
// bare mirror, in a prepared artifact, or somewhere else entirely.
func (r *Real) Gitdir(worktree string) (Gitdir, error) {
	dot := filepath.Join(worktree, ".git")
	fi, err := os.Lstat(dot)
	if err != nil {
		return Gitdir{Location: GitdirUnreadable}, nil
	}
	gitdir := dot
	if !fi.IsDir() {
		b, err := os.ReadFile(dot)
		if err != nil {
			return Gitdir{Location: GitdirUnreadable}, nil
		}
		line := strings.TrimSpace(string(b))
		v, ok := strings.CutPrefix(line, "gitdir:")
		if !ok {
			return Gitdir{Location: GitdirUnreadable}, nil
		}
		gitdir = strings.TrimSpace(v)
		if !filepath.IsAbs(gitdir) {
			gitdir = filepath.Join(worktree, gitdir)
		}
		gitdir = filepath.Clean(gitdir)
	}
	return Gitdir{Path: gitdir, Location: r.classifyGitdir(gitdir)}, nil
}

func (r *Real) classifyGitdir(gitdir string) string {
	switch {
	case r.MirrorDir != "" && under(gitdir, r.MirrorDir):
		return GitdirMirror
	case r.ArtifactsDir != "" && under(gitdir, r.ArtifactsDir):
		return GitdirArtifact
	default:
		return GitdirOutside
	}
}

func under(path, root string) bool {
	path, root = filepath.Clean(path), filepath.Clean(root)
	return path == root || strings.HasPrefix(path, root+string(filepath.Separator))
}

// GitDescribe returns `git describe --tags --always` for a worktree. An
// unreadable gitdir (the service account before handover) is an error, which
// adopt records as code.tag "unknown" plus a yellow finding.
func (r *Real) GitDescribe(worktree string) (string, error) {
	out, err := runArgv(binGit, "-C", worktree, "describe", "--tags", "--always")
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(out), nil
}

// -------------------------------------------------------------- disk usage

// CachedDU is the DiskUsage implementation: `du -s -B1 -- <path>`, ONE
// in-flight run per path, results cached for TTL and failures for
// duFailureTTL.
//
// The single-flight is not an optimisation. A `du` over a terabyte tenant
// tree runs for minutes; the dashboard polls every 15 s and every poll used
// to start another one, so a slow tree accumulated one process per poll until
// the IO it was measuring was the IO it was causing. A failure is cached too,
// briefly, so an unreadable dir does not fork `du` on every single request.
type CachedDU struct {
	TTL time.Duration

	mu       sync.Mutex
	entries  map[string]duEntry
	inflight map[string]*duCall
	// measure is the seam the single-flight test drives; nil ⇒ runDU, the
	// real `du -s -B1 -- <path>`.
	measure func(ctx context.Context, path string) (int64, error)
}

type duEntry struct {
	bytes int64
	err   error
	at    time.Time
}

// duCall is one in-flight run; every caller that arrives while it runs waits
// on done and takes its answer.
type duCall struct {
	done  chan struct{}
	bytes int64
	err   error
}

// duFailureTTL is how long a failed `du` is remembered. Short, because the
// usual cause (a directory that was being created) fixes itself.
const duFailureTTL = 60 * time.Second

var _ DiskUsage = (*CachedDU)(nil)

// NewCachedDU returns a DiskUsage caching for ttl (1 h when zero).
func NewCachedDU(ttl time.Duration) *CachedDU {
	if ttl <= 0 {
		ttl = time.Hour
	}
	return &CachedDU{TTL: ttl, entries: map[string]duEntry{}, inflight: map[string]*duCall{}}
}

// ttlFor is how long an answer stays fresh: the full TTL for a size, the
// (shorter) failure TTL for an error.
func (c *CachedDU) ttlFor(err error) time.Duration {
	ttl := c.TTL
	if ttl <= 0 {
		ttl = time.Hour
	}
	if err != nil && ttl > duFailureTTL {
		return duFailureTTL
	}
	return ttl
}

// Usage returns the recursive size of path in bytes, from cache when fresh,
// joining an in-flight run for the same path when there is one.
func (c *CachedDU) Usage(ctx context.Context, path string) (int64, error) {
	c.mu.Lock()
	if c.entries == nil {
		c.entries = map[string]duEntry{}
	}
	if c.inflight == nil {
		c.inflight = map[string]*duCall{}
	}
	if e, ok := c.entries[path]; ok && time.Since(e.at) < c.ttlFor(e.err) {
		c.mu.Unlock()
		return e.bytes, e.err
	}
	if call, ok := c.inflight[path]; ok {
		c.mu.Unlock()
		select {
		case <-call.done:
			return call.bytes, call.err
		case <-ctx.Done():
			return 0, ctx.Err()
		}
	}
	call := &duCall{done: make(chan struct{})}
	c.inflight[path] = call
	measure := c.measure
	c.mu.Unlock()
	if measure == nil {
		measure = runDU
	}

	call.bytes, call.err = measure(ctx, path)

	c.mu.Lock()
	c.entries[path] = duEntry{bytes: call.bytes, err: call.err, at: time.Now()}
	delete(c.inflight, path)
	c.mu.Unlock()
	close(call.done)
	return call.bytes, call.err
}

func runDU(ctx context.Context, path string) (int64, error) {
	ctx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	// `--` so a path that starts with a dash is a path, not a flag.
	cmd := exec.CommandContext(ctx, binDu, "-s", "-B1", "--", path)
	cmd.Env = []string{"LC_ALL=C"}
	out, err := cmd.Output()
	if err != nil {
		return 0, fmt.Errorf("du %s: %w", path, err)
	}
	f := strings.Fields(string(out))
	if len(f) == 0 {
		return 0, fmt.Errorf("du %s: empty output", path)
	}
	n, err := strconv.ParseInt(f[0], 10, 64)
	if err != nil {
		return 0, fmt.Errorf("du %s: %w", path, err)
	}
	return n, nil
}

// runArgv runs an absolute program with a fixed argument list, no shell, a
// sanitized environment and a 2 s timeout.
func runArgv(bin string, args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), cmdTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, bin, args...)
	cmd.Env = []string{"LC_ALL=C", "PATH=/usr/bin:/bin"}
	if home, ok := os.LookupEnv("HOME"); ok {
		cmd.Env = append(cmd.Env, "HOME="+home)
	}
	// systemctl --user needs the bus address of the caller's own manager.
	for _, k := range []string{"XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"} {
		if v, ok := os.LookupEnv(k); ok {
			cmd.Env = append(cmd.Env, k+"="+v)
		}
	}
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	if err := cmd.Run(); err != nil {
		return stdout.String(), fmt.Errorf("%s %s: %w: %s", filepath.Base(bin), strings.Join(args, " "), err, strings.TrimSpace(stderr.String()))
	}
	return stdout.String(), nil
}
