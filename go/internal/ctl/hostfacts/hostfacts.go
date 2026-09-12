// Package hostfacts is every read-only probe the ctl makes of the host it
// runs on: who listens on which port, what a process was launched with, what
// systemd knows about a unit, how much disk and memory are left, whether a
// path is writable by someone outside ragops, and what a worktree's gitdir
// says.
//
// Three rules hold for everything here:
//
//   - READ ONLY. Nothing in this package writes, creates, kills or reloads.
//   - argv only, never a shell. The three programs it may run are getent,
//     git and systemctl, each with a fixed argument list and a timeout.
//   - Never a secret. Process environments are filtered through the SAFE_ENV
//     allowlist copied from ops/coconut/snapshot.sh and then through its
//     SECRET_HINT denylist, so a value that looks like a credential is
//     dropped before it can reach a caller, a log or the registry.
//
// The probes sit behind the Host, Prober and DiskUsage interfaces so adopt,
// doctor and fleet can be tested against recorded fixtures (see Fake and
// LoadFake) instead of the live host.
package hostfacts

import (
	"context"
	"fmt"
	"net/url"
	"strconv"

	"github.com/ragstack/ragstack/internal/ctl/paths"
)

// Listener is one listening TCP socket and, when the ctl may see it, the
// process behind it. Pid is 0 and User "" for a socket owned by another
// account (the /proc/<pid>/fd scan cannot see it) — that is a fact, not an
// error: doctor reports such a port as an unexpected listener.
type Listener struct {
	Port    int      // host port
	Addr    string   // bind address as /proc/net/tcp spells it (0.0.0.0, ::, 127.0.0.1)
	Inode   uint64   // socket inode, the /proc/<pid>/fd join key
	Pid     int      // 0 when the owner is invisible to this account
	UID     int      // -1 when unknown
	User    string   // username of UID, or "" when unknown
	Cmdline []string // argv of Pid, empty when unknown
	Cwd     string   // /proc/<pid>/cwd target, "" when unknown
}

// DropIn is what the root drop-in for a user manager declares
// (/etc/systemd/system/user@<uid>.service.d/*.conf).
type DropIn struct {
	Present           bool
	SystemdUnitPath   string   // Environment=SYSTEMD_UNIT_PATH=…
	RequiresMountsFor []string // Unit RequiresMountsFor=…
	Files             []string // the .conf files parsed, in read order
}

// UnitStatus is the subset of `systemctl --user show` the ctl reads.
// ActiveState is StateUnknown when the ctl cannot ask (a different user's
// manager, no systemctl, no bus).
type UnitStatus struct {
	ActiveState string
	SubState    string
	NRestarts   int
}

// StateUnknown is the ActiveState of a unit this account cannot query.
const StateUnknown = "unknown"

// Writability is the verdict of the permission check doctor runs over unit
// files, binaries, SIFs and tenant data dirs.
type Writability struct {
	Writable bool   // true ⇒ some component is writable outside ragops
	Path     string // the offending component (empty when not writable)
	Reason   string // human-readable why, e.g. "group-writable by cels (mode 0775)"
}

// Gitdir locations.
const (
	GitdirMirror     = "mirror"         // under /rag/repos/ragstack.git
	GitdirArtifact   = "artifact"       // under /rag/data/ctl/artifacts
	GitdirOutside    = "outside_mirror" // anywhere else (e.g. /home/wilke/…)
	GitdirUnreadable = "unreadable"     // no .git, or this account may not read it
)

// Gitdir is where a worktree's git directory lives and whether that is one of
// the two trusted roots.
type Gitdir struct {
	Path     string // resolved gitdir, "" when unreadable
	Location string // one of the Gitdir* constants
}

// Host is the read-only host surface adopt, doctor and fleet consume.
// Implemented by Real (the live host) and Fake (recorded fixtures).
type Host interface {
	// Listeners lists every listening TCP socket, with its owning process
	// where this account can see it.
	Listeners() ([]Listener, error)
	// ProcEnv returns the environment of pid, keeping only keys allow
	// accepts. Pass SafeEnvAllowed for the snapshot.sh allowlist.
	ProcEnv(pid int, allow func(key string) bool) (map[string]string, error)
	// Linger reports whether /var/lib/systemd/linger/<user> exists.
	Linger(user string) bool
	// RuntimeDir reports whether /run/user/<uid> exists.
	RuntimeDir(uid int) bool
	// UserDropIn parses /etc/systemd/system/user@<uid>.service.d/*.conf.
	UserDropIn(uid int) (DropIn, error)
	// SysctlMaxMapCount reads vm.max_map_count.
	SysctlMaxMapCount() (int, error)
	// MemTotalBytes reads MemTotal from /proc/meminfo.
	MemTotalBytes() (int64, error)
	// DiskFree returns free bytes on the filesystem holding path.
	DiskFree(path string) (int64, error)
	// SudoersGroupMembers lists the members of a group (getent, argv only).
	SudoersGroupMembers(group string) ([]string, error)
	// UnitState asks the user manager about one unit. It is only ever run
	// as the current user: for any other user it answers StateUnknown.
	UnitState(user, unit string) UnitStatus
	// WritableByOthers reports whether any component of path is writable by
	// other, or by a group that is not the ragops group.
	WritableByOthers(path string) (Writability, error)
	// Gitdir resolves a worktree's gitdir and classifies its location.
	Gitdir(worktree string) (Gitdir, error)
	// GitDescribe runs `git -C <worktree> describe --tags --always`.
	GitDescribe(worktree string) (string, error)
	// Username returns the account this process runs as.
	Username() string
}

// StoreListing is a read-only listing of a store's contents: what a health
// probe needs and nothing else.
type StoreListing struct {
	Status int      // HTTP status of the listing request
	Names  []string // collection / index names
	Count  int      // len(Names), carried explicitly for a caller that drops Names
}

// Prober is the HTTP surface: GET only, redirects refused, short timeout.
type Prober interface {
	// Probe GETs url and returns the status code (0 with an error when the
	// request never completed).
	Probe(ctx context.Context, url string) (int, error)
	// QdrantCollections lists <url>/collections.
	QdrantCollections(ctx context.Context, url string) (StoreListing, error)
	// ESIndices lists <url>/_cat/indices.
	ESIndices(ctx context.Context, url string) (StoreListing, error)
}

// DiskUsage is the (expensive) recursive size of a directory. Implementations
// cache: fleet asks on every poll and `du` over a tenant data dir is minutes
// of IO.
type DiskUsage interface {
	Usage(ctx context.Context, path string) (int64, error)
}

// DefaultExternalStorePorts are the shared-store ports a tenant may point at
// from outside its own block (CTL_EXTERNAL_STORE_PORTS): the shared Qdrant,
// lucid's qdrant2 and the shared Elasticsearch.
var DefaultExternalStorePorts = []int{6333, 6343, 9200}

// AllowedStoreURL reports whether u is a store URL the ctl may probe: http
// (never https — these are loopback services), NO userinfo, host 127.0.0.1,
// ::1 or localhost, and a port that is either inside the tenant's own block
// or in the external set. Ports are evidence, never authority: this is what
// stops a registry row pointing the daemon at an arbitrary address.
//
// Every probe the ctl makes goes through this — fleet's health column and
// doctor's store checks alike — so a URL it refuses is never dialled, not
// merely reported.
func AllowedStoreURL(u string, block paths.Ports, external []int) (bool, string) {
	if u == "" {
		return false, "empty URL"
	}
	parsed, err := url.Parse(u)
	if err != nil {
		return false, fmt.Sprintf("unparsable URL %q", u)
	}
	if parsed.Scheme != "http" {
		return false, fmt.Sprintf("scheme %q is not http", parsed.Scheme)
	}
	// `http://u:p@localhost:9200` passes every host and port test below
	// while carrying a credential the ctl would put on the wire (and into
	// the probe's error strings). A store URL has no business holding one.
	if parsed.User != nil {
		return false, "URL carries userinfo (a credential); a store URL must not"
	}
	host := parsed.Hostname()
	if host != "127.0.0.1" && host != "localhost" && host != "::1" {
		return false, fmt.Sprintf("host %q is not loopback", host)
	}
	port, err := strconv.Atoi(parsed.Port())
	if err != nil || port <= 0 {
		return false, fmt.Sprintf("no port in %q", u)
	}
	for _, p := range []int{block.API, block.QdrantHTTP, block.QdrantGRPC, block.ESHTTP, block.ESTransport, block.PG} {
		if port == p {
			return true, ""
		}
	}
	for _, p := range external {
		if port == p {
			return true, ""
		}
	}
	return false, fmt.Sprintf("port %d is neither in the tenant's block (%d-%d) nor an external store port", port, block.Base, block.PG)
}
