package doctor

import (
	"fmt"
	"io/fs"
	"os"
	"os/user"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
)

// ForeignSidecar is one SQLite sidecar of the job store (`jobs.db-shm` or
// `jobs.db-wal`) that belongs to an account other than the one asked about.
//
// #716: it is the one way a control-plane daemon loses its job store without
// anything in the ctl doing it wrong. Any process that opens `jobs.db` with
// SQLite — `sqlite3`, a python one-liner, a diagnostic agent "just reading" —
// leaves its OWN `-shm`/`-wal` beside the database, and a daemon that cannot
// write them opens nothing read-write: "attempt to write a readonly database
// (8)". The ctl cannot stop a foreign SQLite client; it can only say exactly
// what happened and what to do about it.
type ForeignSidecar struct {
	Path  string
	UID   int
	Owner string // the account name, or the uid as a string when it has none
	Size  int64
}

// SidecarProbe is how ForeignSidecars looks at the filesystem. Every field is
// optional and defaults to the real thing; a test injects UID (and User) to
// simulate a foreign owner, because a test cannot chown.
type SidecarProbe struct {
	Lstat func(string) (fs.FileInfo, error)
	// UID reads the owner out of a FileInfo; false means "cannot tell", and the
	// file is then not reported (a guess is worse than silence here).
	UID func(fs.FileInfo) (int, bool)
	// User maps a uid to an account name; "" falls back to the number.
	User func(uid int) string
}

func (p SidecarProbe) lstat(path string) (fs.FileInfo, error) {
	if p.Lstat != nil {
		return p.Lstat(path)
	}
	return os.Lstat(path)
}

func (p SidecarProbe) uid(fi fs.FileInfo) (int, bool) {
	if p.UID != nil {
		return p.UID(fi)
	}
	if st, ok := fi.Sys().(*syscall.Stat_t); ok {
		return int(st.Uid), true
	}
	return 0, false
}

func (p SidecarProbe) user(uid int) string {
	name := ""
	if p.User != nil {
		name = p.User(uid)
	} else if u, err := user.LookupId(strconv.Itoa(uid)); err == nil {
		name = u.Username
	}
	if name == "" {
		return strconv.Itoa(uid)
	}
	return name
}

// ForeignSidecars returns the `-shm`/`-wal` sidecars of store whose owner is
// not wantUID, in that order. Absent sidecars are not reported: SQLite creates
// them, and an absent file is nobody's lockout.
func ForeignSidecars(store string, wantUID int, probe SidecarProbe) []ForeignSidecar {
	var out []ForeignSidecar
	for _, p := range []string{store + "-shm", store + "-wal"} {
		fi, err := probe.lstat(p)
		if err != nil {
			continue
		}
		uid, ok := probe.uid(fi)
		if !ok || uid == wantUID {
			continue
		}
		out = append(out, ForeignSidecar{Path: p, UID: uid, Owner: probe.user(uid), Size: fi.Size()})
	}
	return out
}

// DescribeForeignSidecars renders what a "job engine unavailable" report says
// about them: each file, its owner and its size, then the recovery. With
// fullPaths false the files are named by their base name only. Neither form is
// for the anonymous `GET /health`: both name the owner, uid and sizes, which
// stay in the log, the operator-only 409 and doctor.
//
// The recovery depends on the WAL. An EMPTY (or absent) `-wal` holds no
// transaction, so removing the pair as their owner — once nothing holds the
// store open — loses nothing, and the daemon recreates them under its own uid
// on its next retry. A NON-empty `-wal` holds committed transactions SQLite
// has not yet copied into the database: removing it loses them, so it is copied
// aside first.
func DescribeForeignSidecars(files []ForeignSidecar, fullPaths bool) string {
	if len(files) == 0 {
		return ""
	}
	name := func(p string) string {
		if fullPaths {
			return p
		}
		return filepath.Base(p)
	}
	var parts, names []string
	owners := map[string]bool{}
	var ownerList []string
	walBytes := int64(0)
	for _, f := range files {
		parts = append(parts, fmt.Sprintf("%s owned by %s (uid %d, %d bytes)", name(f.Path), f.Owner, f.UID, f.Size))
		names = append(names, name(f.Path))
		if strings.HasSuffix(f.Path, "-wal") {
			walBytes = f.Size
		}
		if !owners[f.Owner] {
			owners[f.Owner] = true
			ownerList = append(ownerList, f.Owner)
		}
	}
	as := strings.Join(ownerList, "/")
	where := ""
	if !fullPaths {
		where = " in the ctl state directory"
	}
	var recovery string
	if walBytes == 0 {
		recovery = fmt.Sprintf("the WAL is empty, so as %s, with nothing holding the store open (fuser), run%s: rm %s",
			as, where, strings.Join(names, " "))
	} else {
		recovery = fmt.Sprintf("the WAL holds %d bytes of committed transactions: copy the sidecars aside first "+
			"(cp -p %s <backup dir>), then as %s, with nothing holding the store open (fuser), run%s: rm %s",
			walBytes, strings.Join(names, " "), as, where, strings.Join(names, " "))
	}
	return fmt.Sprintf("foreign-owned SQLite sidecar(s) beside the job store: %s. Another account opened jobs.db "+
		"with SQLite (never open it with sqlite3/python; read through `ragstack-ctl job list|show` and "+
		"`ragstack-ctl audit list`), and this daemon cannot write its files. Recovery: %s; the daemon keeps "+
		"retrying and picks the store up without a restart (doctor: %s)",
		strings.Join(parts, "; "), recovery, JobEngineUnavailable)
}
