package acl

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"syscall"
)

// CelsGID is the 1869-member group every account on coconut is in, service
// account included. It is the reason this package exists: a chmod that gave
// svcbvbrc access through the group would give it to 1868 other people, so
// the grant explicitly zeroes the owning group's bits when the group IS cels
// — that half of the change is not a side effect, it is the point.
const CelsGID uint32 = 20001

// DefaultReadOnly are the basename globs that get read-only access even in a
// grant that is otherwise read/write. These are the files whose content is a
// credential: the service account has to LOAD them, never rewrite them, and a
// historical copy (`secrets.env.bak-20260817`) is exactly as sensitive as the
// live one.
var DefaultReadOnly = []string{"secrets.env", "ctl-secrets.env", "*.bak-*"}

// GrantOptions tune one grant or revoke.
type GrantOptions struct {
	// Recursive walks the whole tree below the path. It never follows a
	// symlink, never crosses onto another filesystem and never leaves the
	// root it started at.
	Recursive bool
	// Revoke removes the named user's entry instead of adding it (and the
	// mask with it, once nothing named is left to cap).
	Revoke bool
	// DryRun computes every Change without writing anything.
	DryRun bool
	// ReadOnly are basename globs that get r-- instead of rw-/rwx.
	// nil ⇒ DefaultReadOnly. An explicitly empty slice means "none".
	ReadOnly []string
	// DenyGroups are the gids whose GROUP_OBJ bits are cleared when they own
	// the path. nil ⇒ {CelsGID}. Any other owning group keeps its bits: the
	// grant is here to add one user, not to relitigate a directory's group.
	DenyGroups []uint32
	// Self is the uid the process runs as; a path owned by anyone else is
	// skipped, because setxattr on it would fail anyway and a partial,
	// half-refused walk is worse than a reported skip. 0 ⇒ os.Getuid().
	Self int
}

func (o GrantOptions) readOnlyGlobs() []string {
	if o.ReadOnly == nil {
		return DefaultReadOnly
	}
	return o.ReadOnly
}

func (o GrantOptions) denyGroups() []uint32 {
	if o.DenyGroups == nil {
		return []uint32{CelsGID}
	}
	return o.DenyGroups
}

func (o GrantOptions) self() int {
	if o.Self != 0 {
		return o.Self
	}
	return os.Getuid()
}

// Change is one path's before/after, the row of the table `fleet grant`
// prints. Note is non-empty when nothing was written and says why; Before and
// After are then equal.
type Change struct {
	Path   string `json:"path"`
	Before string `json:"before"`
	After  string `json:"after"`
	Note   string `json:"note,omitempty"`
}

// Changed reports whether this row is an actual edit (as opposed to a skip or
// a path that already had the grant).
func (c Change) Changed() bool { return c.Note == "" && c.Before != c.After }

// NotOwned is the note on a path the current account does not own.
const NotOwned = "not owned by you"

// Grant gives uid access to exactly one path, and returns what changed.
//
// For a DIRECTORY the access ACL becomes
//
//	user::<the owner's existing bits>  user:<uid>:rwx  group::<see below>
//	mask::rwx  other::---
//
// and the DEFAULT ACL is set to the same list, so everything created inside
// later inherits the grant without a second run. For a regular FILE the named
// entry is rw- (r-- when the basename matches a ReadOnly glob) and the mask
// matches it; a file the owner may execute keeps that x bit, because the
// owner's bits are carried across untouched rather than chosen here.
//
// group:: is zeroed when the owning group is in DenyGroups (cels by default)
// and otherwise kept exactly as it is. other:: is always zeroed: these are
// tenant data roots, and "everyone on the host" was never an intended reader.
//
// Grant handles ONE path. Apply is the entry point that honours
// GrantOptions.Recursive; o.Recursive is ignored here.
func Grant(path string, uid uint32, o GrantOptions) (Change, error) {
	return apply1(path, uid, o, false)
}

// Revoke removes uid's named entry from a path's access ACL (and from its
// default ACL when it is a directory), dropping the mask once no named entry
// is left for it to cap. Nothing else in the list is touched: a revoke undoes
// the named entry, not the grant's other half.
func Revoke(path string, uid uint32, o GrantOptions) (Change, error) {
	return apply1(path, uid, o, true)
}

// Apply is the one entry point the CLI uses: it dispatches on o.Revoke and
// walks when o.Recursive. The changes come back in walk order (the root
// first), which is also the order they were written — so an interrupted run
// can be read off the table it already printed.
func Apply(root string, uid uint32, o GrantOptions) ([]Change, error) {
	root = filepath.Clean(root)
	fi, err := os.Lstat(root)
	if err != nil {
		return nil, err
	}
	if fi.Mode()&fs.ModeSymlink != 0 {
		return nil, fmt.Errorf("acl: %s is a symlink; the grant never follows one", root)
	}
	if !o.Recursive {
		c, err := apply1(root, uid, o, o.Revoke)
		if err != nil {
			return nil, err
		}
		return []Change{c}, nil
	}
	rootDev, err := deviceOf(fi)
	if err != nil {
		return nil, err
	}

	var out []Change
	walkErr := filepath.WalkDir(root, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			// A directory this account may not read is a skip with a
			// reason, not the end of the walk: the rest of the tree is
			// still worth granting.
			out = append(out, Change{Path: p, Note: fmt.Sprintf("unreadable: %v", err)})
			if d != nil && d.IsDir() {
				return fs.SkipDir
			}
			return nil
		}
		// WalkDir hands us Lstat information, so a symlink arrives as a
		// symlink and is never descended into. Skipping it here is what
		// keeps a planted link from redirecting a grant out of the root —
		// and it is REPORTED, because a link inside a managed root is
		// something the operator should see in the table.
		if d.Type()&fs.ModeSymlink != 0 {
			out = append(out, Change{Path: p, Note: "symlink (never followed)"})
			return nil
		}
		info, ierr := d.Info()
		if ierr != nil {
			out = append(out, Change{Path: p, Note: fmt.Sprintf("unreadable: %v", ierr)})
			return nil
		}
		if dev, derr := deviceOf(info); derr == nil && dev != rootDev {
			// A different filesystem is a different administrative domain
			// (and on this host, possibly the NFS home). Stop at the mount.
			out = append(out, Change{Path: p, Note: "on another filesystem"})
			if d.IsDir() {
				return fs.SkipDir
			}
			return nil
		}
		if !d.IsDir() && !info.Mode().IsRegular() {
			out = append(out, Change{Path: p, Note: "not a regular file or directory"})
			return nil
		}
		c, cerr := apply1(p, uid, o, o.Revoke)
		if cerr != nil {
			return cerr
		}
		out = append(out, c)
		return nil
	})
	if walkErr != nil {
		return out, walkErr
	}
	return out, nil
}

// apply1 is the shared body of Grant and Revoke for one path.
func apply1(path string, uid uint32, o GrantOptions, revoke bool) (Change, error) {
	fi, err := os.Lstat(path)
	if err != nil {
		return Change{}, err
	}
	if fi.Mode()&fs.ModeSymlink != 0 {
		return Change{Path: path, Note: "symlink (never followed)"}, nil
	}
	st, ok := fi.Sys().(*syscall.Stat_t)
	if !ok {
		return Change{Path: path, Note: "no stat information"}, nil
	}
	if int(st.Uid) != o.self() {
		// Only the owner (or root, which the ctl never is) may set an ACL.
		// Report it rather than attempt it: a table row an operator can act
		// on beats an EPERM in the middle of a walk.
		return Change{Path: path, Note: NotOwned}, nil
	}

	access, err := Get(path, KindAccess)
	if err != nil {
		return Change{}, err
	}
	isDir := fi.IsDir()
	var dflt ACL
	if isDir {
		if dflt, err = Get(path, KindDefault); err != nil {
			return Change{}, err
		}
	}
	before := describe(access, dflt, isDir)

	var wantAccess, wantDefault ACL
	if revoke {
		wantAccess = withoutUser(access, uid)
		if isDir && len(dflt) > 0 {
			wantDefault = withoutUser(dflt, uid)
		}
	} else {
		wantAccess = granted(access, uid, o, isDir, fi.Mode(), st.Gid, filepath.Base(path))
		if isDir {
			wantDefault = wantAccess.Clone()
		}
	}
	after := describe(wantAccess, wantDefault, isDir)
	c := Change{Path: path, Before: before, After: after}
	if before == after || o.DryRun {
		return c, nil
	}
	if err := Set(path, KindAccess, wantAccess); err != nil {
		return Change{}, err
	}
	if isDir {
		if err := Set(path, KindDefault, wantDefault); err != nil {
			return Change{}, err
		}
	}
	return c, nil
}

// granted builds the target access ACL for one path.
func granted(cur ACL, uid uint32, o GrantOptions, isDir bool, mode fs.FileMode, gid uint32, base string) ACL {
	// The owner's bits come from what is already there, never from a
	// constant: Set rewrites st_mode as a side effect, so anything this
	// function invents here would be an undeclared chmod. An executable
	// keeps its x bit for exactly that reason.
	ownerPerm := Perm(mode.Perm()>>6) & PermRWX
	if e, ok := cur.Find(TagUserObj, UndefinedID); ok {
		ownerPerm = e.Perm
	}
	groupPerm := Perm(mode.Perm()>>3) & PermRWX
	if e, ok := cur.Find(TagGroupObj, UndefinedID); ok {
		groupPerm = e.Perm
	}
	for _, deny := range o.denyGroups() {
		if gid == deny {
			groupPerm = PermNone
		}
	}

	namedPerm := PermRW
	if isDir {
		namedPerm = PermRWX
	} else if matchesAny(base, o.readOnlyGlobs()) {
		namedPerm = PermR
	}

	out := ACL{
		{Tag: TagUserObj, ID: UndefinedID, Perm: ownerPerm},
		{Tag: TagUser, ID: uid, Perm: namedPerm},
		{Tag: TagGroupObj, ID: UndefinedID, Perm: groupPerm},
	}
	// Every other named entry that was already there is kept: this command
	// grants ONE user, and silently dropping somebody else's grant would be
	// a change nobody asked for (doctor reports foreign grants instead).
	for _, e := range cur {
		if e.Tag == TagUser && e.ID != uid {
			out = append(out, e)
		}
		if e.Tag == TagGroup {
			out = append(out, e)
		}
	}
	// The mask has to admit at least what the named entry asks for, or the
	// grant is written and has no effect.
	mask := namedPerm | groupPerm
	for _, e := range out {
		if e.Named() {
			mask |= e.Perm
		}
	}
	out = append(out,
		Entry{Tag: TagMask, ID: UndefinedID, Perm: mask},
		Entry{Tag: TagOther, ID: UndefinedID, Perm: PermNone},
	)
	out.Sort()
	return out
}

// withoutUser drops uid's named entry, and the mask too once no named entry
// remains for it to cap (a mask over nothing is a list Validate refuses).
func withoutUser(cur ACL, uid uint32) ACL {
	if len(cur) == 0 {
		return nil
	}
	out := make(ACL, 0, len(cur))
	for _, e := range cur {
		if e.Tag == TagUser && e.ID == uid {
			continue
		}
		out = append(out, e)
	}
	if !out.HasNamed() {
		// The mask was the effective ceiling on GROUP_OBJ while it existed;
		// fold it in before dropping it, so removing a grant cannot quietly
		// WIDEN the owning group's access.
		if m, ok := out.Find(TagMask, UndefinedID); ok {
			rest := make(ACL, 0, len(out))
			for _, e := range out {
				if e.Tag == TagMask {
					continue
				}
				if e.Tag == TagGroupObj {
					e.Perm &= m.Perm
				}
				rest = append(rest, e)
			}
			out = rest
		}
	}
	out.Sort()
	return out
}

// describe renders the before/after cell: the access ACL, plus the default
// ACL for a directory, because a directory whose default is missing is a
// grant that will not survive the next file created in it.
func describe(access, dflt ACL, isDir bool) string {
	s := access.Line()
	if isDir {
		s += " | default:" + dflt.Line()
	}
	return s
}

func matchesAny(base string, globs []string) bool {
	for _, g := range globs {
		if ok, err := filepath.Match(g, base); err == nil && ok {
			return true
		}
	}
	return false
}

func deviceOf(fi fs.FileInfo) (uint64, error) {
	st, ok := fi.Sys().(*syscall.Stat_t)
	if !ok {
		return 0, errors.New("acl: no stat information")
	}
	return uint64(st.Dev), nil
}

// Summarize counts a slice of changes for the CLI's closing line.
func Summarize(changes []Change) (changed, unchanged, skipped int) {
	for _, c := range changes {
		switch {
		case c.Note != "":
			skipped++
		case c.Before != c.After:
			changed++
		default:
			unchanged++
		}
	}
	return
}

// SkipReasons groups the skipped rows by reason, so a recursive run over a
// tree with a thousand foreign files reports one line instead of a thousand.
func SkipReasons(changes []Change) []string {
	byReason := map[string]int{}
	for _, c := range changes {
		if c.Note != "" {
			byReason[c.Note]++
		}
	}
	out := make([]string, 0, len(byReason))
	for r, n := range byReason {
		out = append(out, fmt.Sprintf("%s (%d)", r, n))
	}
	sort.Strings(out)
	return out
}

// HasRWX reports whether uid holds read, write and execute on this list —
// through its own named entry (capped by the mask), which is the only way the
// grant gives it. It is what doctor asks of a managed root.
func (a ACL) HasRWX(uid uint32) bool {
	e, ok := a.Find(TagUser, uid)
	if !ok {
		return false
	}
	return a.Effective(e)&PermRWX == PermRWX
}
