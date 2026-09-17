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

	"github.com/ragstack/ragstack/internal/ctl/acl"
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

// StorageBind is one host directory a process has mounted into itself — the
// host side of an `apptainer --bind`, read back from the process rather than
// from the command line that started it.
//
// It exists because the argv of a containerized store is the argv of the
// process INSIDE the container (`./qdrant`, the elasticsearch JVM, `postgres`)
// and carries no bind at all: the `apptainer instance run --bind …` that set
// them up is a different, already-exited process. The mounts are the fact that
// survives, and `/proc/<pid>/mountinfo` is where this account reads them.
type StorageBind struct {
	// HostPath is the directory as THIS host sees it (e.g.
	// /rag/data/tenants/dev/qdrant/storage).
	HostPath string
	// ContainerPath is where the process sees it (e.g. /qdrant/storage).
	ContainerPath string
	// Device is the "major:minor" the mount is on, kept because it is what
	// made the translation possible and is worth quoting in a refusal.
	Device string
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
//
// Since PR-D2 the mode bits are only half the answer: `fleet grant` gives the
// service account access through a named POSIX ACL entry, so a path can be
// perfectly 0750 and still be writable by an account the mode says nothing
// about. ACLGrants carries every such entry found on the path's ancestry, and
// doctor decides which of them are the deliberate grant and which are not.
type Writability struct {
	Writable bool   // true ⇒ some component is writable outside ragops
	Path     string // the offending component (empty when not writable)
	Reason   string // human-readable why, e.g. "group-writable by cels (mode 0775)"

	// Owner is the account that owns the path itself ("" when unreadable).
	// A named ACL entry for the owner is not a grant to anyone new.
	Owner string
	// ACLGrants are the named user/group ACL entries with WRITE on any
	// component of the path. Read-only entries are not carried: they cannot
	// replace what the ctl executes, which is what this probe is about.
	ACLGrants []ACLGrant
}

// ACLGrant is one named POSIX ACL entry that gives write access, and the path
// component it sits on.
type ACLGrant struct {
	Path  string // the component carrying the entry
	Owner string // the account owning Path ("" when unreadable)
	Group bool   // false ⇒ a named user, true ⇒ a named group
	ID    uint32 // uid or gid
	Name  string // resolved name, or "uid <n>" / "gid <n>"
	Perm  string // EFFECTIVE permission after the mask, e.g. "rwx"
}

// String renders the grant the way doctor quotes it in a finding.
func (g ACLGrant) String() string {
	kind := "user"
	if g.Group {
		kind = "group"
	}
	return fmt.Sprintf("%s:%s:%s on %s", kind, g.Name, g.Perm, g.Path)
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
	// ProcessOwner resolves the account a pid runs as, from /proc/<pid>.
	//
	// It is the ONE attribution that works across accounts. Listeners joins
	// sockets to processes through /proc/<pid>/fd, which only THIS account's
	// own processes expose, so a port held by anybody else arrives with Pid 0
	// and User "" — while a process's uid is readable by everyone (the
	// directory's st_uid, and `status`'s `Uid:` line behind it). Given a pid
	// from somewhere else — a tenant's `api.pidfile` — this answers who runs
	// it, which is how a row's owner is attributed when the listen table
	// cannot.
	//
	// An error means there is no such process (or no /proc entry for it), and
	// it is deliberately not the same as "unknown": a caller that cannot read
	// an owner must record that it could not, never a default dressed up as
	// an observation.
	ProcessOwner(pid int) (uid int, user string, err error)
	// StorageBinds reads /proc/<pid>/mountinfo and returns the mounts whose
	// source this account can locate in its OWN namespace, translated to host
	// paths. Pseudo filesystems (proc, sysfs, tmpfs, cgroup, the container's
	// own squashfs/overlay rootfs) are dropped: what is left is the data the
	// process is actually reading and writing.
	//
	// It is how `adopt --confirm-stores` answers "is this store's storage
	// inside the tenant's own data dir". Only the process's own account may
	// read its mountinfo, which is exactly the account that runs the
	// confirmation (--direct, as the owner of the tree).
	StorageBinds(pid int) ([]StorageBind, error)
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
	// other, or by a group that is not the ragops group, and carries the
	// named POSIX ACL entries that grant write on the way.
	WritableByOthers(path string) (Writability, error)
	// ACL reads the two POSIX ACLs of path without following symlinks. The
	// access list is never empty (a path with no xattr has the ACL its mode
	// implies); the default list is empty unless the directory carries one.
	// This is how doctor tells "the service account was granted access" from
	// "the service account happens to own it".
	ACL(path string) (access, dflt acl.ACL, err error)
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
