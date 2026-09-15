// Package acl reads and writes POSIX access control lists directly, through
// the two extended attributes the Linux VFS stores them in.
//
// It exists because coconut has no `setfacl` and no `getfacl` binary and no
// root to install them with, while the filesystem under /rag is ext4 with
// ACLs honoured. The service account (svcbvbrc, uid 10078, whose only group
// is the 1869-member `cels`) has to read and write the wilke-owned managed
// roots, and `cels` must get nothing. A named-user ACL entry says exactly
// that and nothing more; a group or a chmod cannot.
//
// Three rules hold for everything here:
//
//   - NEVER root, never sudo, never chown, never chmod. The owner's
//     permission bits are read out of what is already on the path and written
//     back unchanged — setting an access ACL rewrites st_mode's owner, group
//     and other bits as a side effect, so "keep them" has to be explicit.
//   - NEVER follow a symlink. Every syscall is the l-variant (Lgetxattr,
//     Lsetxattr, Lremovexattr, Lstat), so a link planted inside a managed
//     root cannot redirect a grant onto a path outside it.
//   - The on-disk format is the kernel's, byte for byte. Anything this
//     package writes, `getfacl` on another host reads, and vice versa.
//
// The format (fs/posix_acl_xattr.h): a little-endian u32 version (2), then
// one 8-byte entry per ACE — u16 tag, u16 perm, u32 id — with 0xFFFFFFFF as
// the id of the entries that name no one. The kernel requires the entries
// sorted by tag then id, exactly one USER_OBJ, GROUP_OBJ and OTHER, and a
// MASK whenever a named USER or GROUP entry is present.
package acl

import (
	"encoding/binary"
	"errors"
	"fmt"
	"io/fs"
	"sort"
	"strconv"
	"strings"
)

// Tag is an ACL entry's kind, with the kernel's values (ACL_USER_OBJ …).
type Tag uint16

// The six entry kinds. Their numeric order is also the order the kernel
// requires entries in, which is why sorting by (tag, id) is the whole rule.
const (
	TagUserObj  Tag = 0x01 // the owner: "user::"
	TagUser     Tag = 0x02 // a named user: "user:10078:"
	TagGroupObj Tag = 0x04 // the owning group: "group::"
	TagGroup    Tag = 0x08 // a named group: "group:20001:"
	TagMask     Tag = 0x10 // the ceiling over every named entry and GROUP_OBJ
	TagOther    Tag = 0x20 // everyone else: "other::"
)

// Perm is the rwx triple of one entry.
type Perm uint16

// The permission bits, with the kernel's values.
const (
	PermExecute Perm = 0x01
	PermWrite   Perm = 0x02
	PermRead    Perm = 0x04

	PermNone Perm = 0
	PermRW   Perm = PermRead | PermWrite
	PermRWX  Perm = PermRead | PermWrite | PermExecute
	PermR    Perm = PermRead
)

// UndefinedID is the id of every entry that names no one (USER_OBJ,
// GROUP_OBJ, MASK, OTHER). The kernel writes ~0; a reader that compared ids
// numerically without knowing this would sort those entries last.
const UndefinedID uint32 = 0xFFFFFFFF

// Version is the only xattr version the kernel has ever written.
const Version uint32 = 2

// Entry is one access control entry.
type Entry struct {
	Tag  Tag
	ID   uint32 // uid for TagUser, gid for TagGroup, UndefinedID otherwise
	Perm Perm
}

// ACL is an ordered access control list — the whole content of one of the two
// xattrs. The zero value (nil) is "no ACL", which for the default xattr means
// "children inherit nothing".
type ACL []Entry

// Named reports whether the entry names a specific user or group, i.e.
// whether it is one of the entries a MASK has to exist for.
func (e Entry) Named() bool { return e.Tag == TagUser || e.Tag == TagGroup }

// String renders one entry in getfacl's long form.
func (e Entry) String() string {
	id := ""
	if e.Named() {
		id = strconv.FormatUint(uint64(e.ID), 10)
	}
	switch e.Tag {
	case TagUserObj, TagUser:
		return "user:" + id + ":" + e.Perm.String()
	case TagGroupObj, TagGroup:
		return "group:" + id + ":" + e.Perm.String()
	case TagMask:
		return "mask::" + e.Perm.String()
	case TagOther:
		return "other::" + e.Perm.String()
	}
	return fmt.Sprintf("tag(%#x):%s:%s", uint16(e.Tag), id, e.Perm.String())
}

// String renders the permission triple as getfacl does: "rwx", "r--", "---".
func (p Perm) String() string {
	b := []byte("---")
	if p&PermRead != 0 {
		b[0] = 'r'
	}
	if p&PermWrite != 0 {
		b[1] = 'w'
	}
	if p&PermExecute != 0 {
		b[2] = 'x'
	}
	return string(b)
}

// String renders the list the way `getfacl` prints its entry block: one entry
// per line, no trailing newline. It is what the CLI and doctor quote, so an
// operator can compare it against getfacl output from any other machine.
func (a ACL) String() string {
	lines := make([]string, 0, len(a))
	for _, e := range a {
		lines = append(lines, e.String())
	}
	return strings.Join(lines, "\n")
}

// Line renders the list on one line, comma separated — the table form, where
// a whole before/after pair has to fit in a terminal row.
func (a ACL) Line() string {
	if len(a) == 0 {
		return "(none)"
	}
	parts := make([]string, 0, len(a))
	for _, e := range a {
		parts = append(parts, e.String())
	}
	return strings.Join(parts, ",")
}

// Find returns the first entry with tag and id, and whether there was one.
func (a ACL) Find(tag Tag, id uint32) (Entry, bool) {
	for _, e := range a {
		if e.Tag == tag && (!e.Named() || e.ID == id) {
			return e, true
		}
	}
	return Entry{}, false
}

// HasNamed reports whether any USER or GROUP entry names someone — the
// condition a MASK entry is required by.
func (a ACL) HasNamed() bool {
	for _, e := range a {
		if e.Named() {
			return true
		}
	}
	return false
}

// Effective is the permission an entry actually grants: every entry except
// USER_OBJ and OTHER is capped by the mask. doctor uses it so a named user
// whose write bit the mask removes is not reported as holding write.
func (a ACL) Effective(e Entry) Perm {
	if e.Tag == TagUserObj || e.Tag == TagOther {
		return e.Perm
	}
	if m, ok := a.Find(TagMask, UndefinedID); ok {
		return e.Perm & m.Perm
	}
	return e.Perm
}

// Clone returns an independent copy, so a caller may edit an ACL it was
// handed without writing through to the one the probe cached.
func (a ACL) Clone() ACL {
	if a == nil {
		return nil
	}
	out := make(ACL, len(a))
	copy(out, a)
	return out
}

// Sort puts the entries in the order the kernel requires: by tag, then by id.
func (a ACL) Sort() {
	sort.SliceStable(a, func(i, j int) bool {
		if a[i].Tag != a[j].Tag {
			return a[i].Tag < a[j].Tag
		}
		return a[i].ID < a[j].ID
	})
}

// ErrBadACL is the class of every parse and validation failure, so a caller
// can tell "this path's ACL is not something I understand" from an IO error.
var ErrBadACL = errors.New("acl: malformed access control list")

// Parse decodes an xattr value. It does NOT validate: a list the kernel
// somehow holds but this package would refuse to write is still reported
// faithfully, because reporting it is how doctor makes it visible.
func Parse(b []byte) (ACL, error) {
	if len(b) == 0 {
		return nil, nil
	}
	if len(b) < 4 {
		return nil, fmt.Errorf("%w: %d bytes is shorter than the header", ErrBadACL, len(b))
	}
	if v := binary.LittleEndian.Uint32(b[:4]); v != Version {
		return nil, fmt.Errorf("%w: version %d, want %d", ErrBadACL, v, Version)
	}
	rest := b[4:]
	if len(rest)%8 != 0 {
		return nil, fmt.Errorf("%w: %d trailing bytes is not a whole number of 8-byte entries", ErrBadACL, len(rest))
	}
	out := make(ACL, 0, len(rest)/8)
	for i := 0; i < len(rest); i += 8 {
		out = append(out, Entry{
			Tag:  Tag(binary.LittleEndian.Uint16(rest[i : i+2])),
			Perm: Perm(binary.LittleEndian.Uint16(rest[i+2 : i+4])),
			ID:   binary.LittleEndian.Uint32(rest[i+4 : i+8]),
		})
	}
	return out, nil
}

// Encode renders the xattr value. An empty ACL encodes to nil rather than a
// bare header: a header with no entries is not a list the kernel accepts, and
// "remove the xattr" is the operation that means it.
func (a ACL) Encode() []byte {
	if len(a) == 0 {
		return nil
	}
	b := make([]byte, 4+8*len(a))
	binary.LittleEndian.PutUint32(b[:4], Version)
	for i, e := range a {
		off := 4 + 8*i
		binary.LittleEndian.PutUint16(b[off:off+2], uint16(e.Tag))
		binary.LittleEndian.PutUint16(b[off+2:off+4], uint16(e.Perm))
		id := e.ID
		if !e.Named() {
			id = UndefinedID
		}
		binary.LittleEndian.PutUint32(b[off+4:off+8], id)
	}
	return b
}

// Validate reports why the list is not one the kernel would accept. Set calls
// it before every write: an ACL the kernel rejects with EINVAL costs an
// operator a round of guessing, and the reason is knowable here.
func (a ACL) Validate() error {
	if len(a) == 0 {
		return nil // "no ACL" is legal; Set turns it into a removal
	}
	counts := map[Tag]int{}
	seen := map[[2]uint32]bool{}
	for i, e := range a {
		switch e.Tag {
		case TagUserObj, TagUser, TagGroupObj, TagGroup, TagMask, TagOther:
		default:
			return fmt.Errorf("%w: entry %d has unknown tag %#x", ErrBadACL, i, uint16(e.Tag))
		}
		if e.Perm&^PermRWX != 0 {
			return fmt.Errorf("%w: entry %d (%s) has bits outside rwx (%#x)", ErrBadACL, i, e, uint16(e.Perm))
		}
		if e.Named() && e.ID == UndefinedID {
			return fmt.Errorf("%w: entry %d is a named %s with the undefined id", ErrBadACL, i, e)
		}
		counts[e.Tag]++
		key := [2]uint32{uint32(e.Tag), e.ID}
		if e.Named() && seen[key] {
			return fmt.Errorf("%w: duplicate entry %s", ErrBadACL, e)
		}
		seen[key] = true
		if i > 0 {
			prev := a[i-1]
			if e.Tag < prev.Tag || (e.Tag == prev.Tag && e.Named() && e.ID < prev.ID) {
				return fmt.Errorf("%w: entry %d (%s) is out of order after %s", ErrBadACL, i, e, prev)
			}
		}
	}
	for _, tag := range []Tag{TagUserObj, TagGroupObj, TagOther} {
		if counts[tag] != 1 {
			return fmt.Errorf("%w: %d %s entries, want exactly 1", ErrBadACL, counts[tag], tagName(tag))
		}
	}
	if counts[TagMask] > 1 {
		return fmt.Errorf("%w: %d mask entries, want at most 1", ErrBadACL, counts[TagMask])
	}
	// The mask is what makes a named entry mean anything, and a mask with
	// nothing to cap is a list the ctl never builds — treat the mismatch as
	// an error in either direction so a bug in Grant cannot reach the disk.
	if named, mask := a.HasNamed(), counts[TagMask] == 1; named != mask {
		if named {
			return fmt.Errorf("%w: named entries require a mask", ErrBadACL)
		}
		return fmt.Errorf("%w: a mask entry with no named entries", ErrBadACL)
	}
	return nil
}

func tagName(t Tag) string {
	switch t {
	case TagUserObj:
		return "user::"
	case TagUser:
		return "user:<id>:"
	case TagGroupObj:
		return "group::"
	case TagGroup:
		return "group:<id>:"
	case TagMask:
		return "mask::"
	case TagOther:
		return "other::"
	}
	return fmt.Sprintf("tag(%#x)", uint16(t))
}

// FromMode is the ACL a path with no access xattr already has: the three
// permission triples of its mode. Get returns it for the access kind, so a
// caller never has to special-case "this path has no ACL yet" — every path
// has one, most just store it in st_mode.
func FromMode(m fs.FileMode) ACL {
	p := m.Perm()
	return ACL{
		{Tag: TagUserObj, ID: UndefinedID, Perm: Perm(p>>6) & PermRWX},
		{Tag: TagGroupObj, ID: UndefinedID, Perm: Perm(p>>3) & PermRWX},
		{Tag: TagOther, ID: UndefinedID, Perm: Perm(p) & PermRWX},
	}
}

// Mode is the inverse for the three *_OBJ/OTHER entries: the permission bits
// the kernel would store in st_mode for this list. GROUP_OBJ is reported
// through the mask, which is what `ls -l` shows once a named entry exists.
func (a ACL) Mode() fs.FileMode {
	var m fs.FileMode
	for _, e := range a {
		switch e.Tag {
		case TagUserObj:
			m |= fs.FileMode(e.Perm) << 6
		case TagGroupObj:
			m |= fs.FileMode(a.Effective(e)) << 3
		case TagOther:
			m |= fs.FileMode(e.Perm)
		}
	}
	return m
}
