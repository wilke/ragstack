package acl

import (
	"bytes"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"golang.org/x/sys/unix"
)

// svcUID is svcbvbrc on coconut. Every fixture here uses it so the golden
// bytes are the bytes the real grant writes.
const svcUID uint32 = 10078

// theGrant is the access ACL `fleet grant --user svcbvbrc` puts on a managed
// directory owned by wilke whose group is cels.
func theGrant() ACL {
	return ACL{
		{Tag: TagUserObj, ID: UndefinedID, Perm: PermRWX},
		{Tag: TagUser, ID: svcUID, Perm: PermRWX},
		{Tag: TagGroupObj, ID: UndefinedID, Perm: PermNone},
		{Tag: TagMask, ID: UndefinedID, Perm: PermRWX},
		{Tag: TagOther, ID: UndefinedID, Perm: PermNone},
	}
}

// TestEncodeIsTheKernelsBytes pins the wire format against a hand-computed
// value. If this test ever has to be "updated", the ctl has stopped writing
// what getfacl on any other machine reads — the whole reason the format is
// implemented here rather than shelled out to.
func TestEncodeIsTheKernelsBytes(t *testing.T) {
	want := []byte{
		0x02, 0x00, 0x00, 0x00, // version 2, little-endian
		0x01, 0x00, 0x07, 0x00, 0xff, 0xff, 0xff, 0xff, // user::rwx
		0x02, 0x00, 0x07, 0x00, 0x5e, 0x27, 0x00, 0x00, // user:10078:rwx (0x275e)
		0x04, 0x00, 0x00, 0x00, 0xff, 0xff, 0xff, 0xff, // group::---
		0x10, 0x00, 0x07, 0x00, 0xff, 0xff, 0xff, 0xff, // mask::rwx
		0x20, 0x00, 0x00, 0x00, 0xff, 0xff, 0xff, 0xff, // other::---
	}
	got := theGrant().Encode()
	if !bytes.Equal(got, want) {
		t.Fatalf("Encode() =\n% x\nwant\n% x", got, want)
	}
}

func TestParseEncodeRoundTrip(t *testing.T) {
	cases := []struct {
		name string
		acl  ACL
	}{
		{"the grant", theGrant()},
		{"mode only", FromMode(0o750)},
		{"a named group too", ACL{
			{Tag: TagUserObj, ID: UndefinedID, Perm: PermRW},
			{Tag: TagUser, ID: 1000, Perm: PermR},
			{Tag: TagUser, ID: svcUID, Perm: PermRW},
			{Tag: TagGroupObj, ID: UndefinedID, Perm: PermNone},
			{Tag: TagGroup, ID: 4242, Perm: PermR},
			{Tag: TagMask, ID: UndefinedID, Perm: PermRW},
			{Tag: TagOther, ID: UndefinedID, Perm: PermNone},
		}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got, err := Parse(c.acl.Encode())
			if err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(got, c.acl) {
				t.Fatalf("round trip lost information:\ngot  %s\nwant %s", got.Line(), c.acl.Line())
			}
			if err := got.Validate(); err != nil {
				t.Fatalf("round-tripped ACL no longer validates: %v", err)
			}
		})
	}
}

func TestParseRejectsGarbage(t *testing.T) {
	cases := map[string][]byte{
		"short header":  {0x02, 0x00},
		"wrong version": {0x01, 0x00, 0x00, 0x00},
		"ragged entry":  {0x02, 0x00, 0x00, 0x00, 0x01, 0x00, 0x07},
	}
	for name, b := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := Parse(b); !errors.Is(err, ErrBadACL) {
				t.Fatalf("Parse(% x) = %v, want ErrBadACL", b, err)
			}
		})
	}
	// An absent xattr reaches Parse as zero bytes, and that is not garbage.
	if a, err := Parse(nil); err != nil || a != nil {
		t.Fatalf("Parse(nil) = %v, %v; want nil, nil", a, err)
	}
}

// TestStringIsGetfaclOrder is the golden for the human rendering: an operator
// compares this against getfacl output from another host, so the spelling and
// the order are part of the contract.
func TestStringIsGetfaclOrder(t *testing.T) {
	want := strings.Join([]string{
		"user::rwx",
		"user:10078:rwx",
		"group::---",
		"mask::rwx",
		"other::---",
	}, "\n")
	if got := theGrant().String(); got != want {
		t.Fatalf("String() =\n%s\nwant\n%s", got, want)
	}
	if got, want := theGrant().Line(), "user::rwx,user:10078:rwx,group::---,mask::rwx,other::---"; got != want {
		t.Fatalf("Line() = %q, want %q", got, want)
	}
	if got, want := ACL(nil).Line(), "(none)"; got != want {
		t.Fatalf("empty Line() = %q, want %q", got, want)
	}
}

func TestValidate(t *testing.T) {
	cases := []struct {
		name    string
		acl     ACL
		wantErr string
	}{
		{"the grant", theGrant(), ""},
		{"mode only", FromMode(0o644), ""},
		{"empty", nil, ""},
		{"no owner entry", ACL{
			{Tag: TagGroupObj, ID: UndefinedID}, {Tag: TagOther, ID: UndefinedID},
		}, "want exactly 1"},
		{"two owners", ACL{
			{Tag: TagUserObj, ID: UndefinedID, Perm: PermRWX},
			{Tag: TagUserObj, ID: UndefinedID, Perm: PermRW},
			{Tag: TagGroupObj, ID: UndefinedID}, {Tag: TagOther, ID: UndefinedID},
		}, "want exactly 1"},
		{"named entry with no mask", ACL{
			{Tag: TagUserObj, ID: UndefinedID, Perm: PermRWX},
			{Tag: TagUser, ID: svcUID, Perm: PermRWX},
			{Tag: TagGroupObj, ID: UndefinedID}, {Tag: TagOther, ID: UndefinedID},
		}, "named entries require a mask"},
		{"mask with nothing to cap", ACL{
			{Tag: TagUserObj, ID: UndefinedID, Perm: PermRWX},
			{Tag: TagGroupObj, ID: UndefinedID},
			{Tag: TagMask, ID: UndefinedID, Perm: PermRWX},
			{Tag: TagOther, ID: UndefinedID},
		}, "mask entry with no named entries"},
		{"out of order", ACL{
			{Tag: TagGroupObj, ID: UndefinedID},
			{Tag: TagUserObj, ID: UndefinedID, Perm: PermRWX},
			{Tag: TagOther, ID: UndefinedID},
		}, "out of order"},
		{"named ids out of order", ACL{
			{Tag: TagUserObj, ID: UndefinedID, Perm: PermRWX},
			{Tag: TagUser, ID: 2000, Perm: PermRW},
			{Tag: TagUser, ID: 1000, Perm: PermRW},
			{Tag: TagGroupObj, ID: UndefinedID},
			{Tag: TagMask, ID: UndefinedID, Perm: PermRW},
			{Tag: TagOther, ID: UndefinedID},
		}, "out of order"},
		{"duplicate named user", ACL{
			{Tag: TagUserObj, ID: UndefinedID, Perm: PermRWX},
			{Tag: TagUser, ID: svcUID, Perm: PermRW},
			{Tag: TagUser, ID: svcUID, Perm: PermR},
			{Tag: TagGroupObj, ID: UndefinedID},
			{Tag: TagMask, ID: UndefinedID, Perm: PermRW},
			{Tag: TagOther, ID: UndefinedID},
		}, "duplicate entry"},
		{"named user with the undefined id", ACL{
			{Tag: TagUserObj, ID: UndefinedID, Perm: PermRWX},
			{Tag: TagUser, ID: UndefinedID, Perm: PermRW},
			{Tag: TagGroupObj, ID: UndefinedID},
			{Tag: TagMask, ID: UndefinedID, Perm: PermRW},
			{Tag: TagOther, ID: UndefinedID},
		}, "undefined id"},
		{"unknown tag", ACL{
			{Tag: TagUserObj, ID: UndefinedID, Perm: PermRWX},
			{Tag: Tag(0x40), ID: UndefinedID},
			{Tag: TagGroupObj, ID: UndefinedID}, {Tag: TagOther, ID: UndefinedID},
		}, "unknown tag"},
		{"bits outside rwx", ACL{
			{Tag: TagUserObj, ID: UndefinedID, Perm: Perm(0x08)},
			{Tag: TagGroupObj, ID: UndefinedID}, {Tag: TagOther, ID: UndefinedID},
		}, "outside rwx"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			err := c.acl.Validate()
			if c.wantErr == "" {
				if err != nil {
					t.Fatalf("Validate() = %v, want nil", err)
				}
				return
			}
			if err == nil || !strings.Contains(err.Error(), c.wantErr) {
				t.Fatalf("Validate() = %v, want an error containing %q", err, c.wantErr)
			}
			if err != nil && !errors.Is(err, ErrBadACL) {
				t.Errorf("Validate() error is not ErrBadACL: %v", err)
			}
		})
	}
}

func TestFromModeAndBack(t *testing.T) {
	for _, m := range []fs.FileMode{0o755, 0o644, 0o700, 0o000, 0o777, 0o750} {
		a := FromMode(m)
		if err := a.Validate(); err != nil {
			t.Fatalf("FromMode(%#o) does not validate: %v", m, err)
		}
		if got := a.Mode(); got != m {
			t.Errorf("FromMode(%#o).Mode() = %#o", m, got)
		}
	}
}

// TestEffectiveAppliesTheMask: a grant whose mask does not admit write does
// not grant write, and doctor has to report the effective answer.
func TestEffectiveAppliesTheMask(t *testing.T) {
	a := ACL{
		{Tag: TagUserObj, ID: UndefinedID, Perm: PermRWX},
		{Tag: TagUser, ID: svcUID, Perm: PermRWX},
		{Tag: TagGroupObj, ID: UndefinedID, Perm: PermNone},
		{Tag: TagMask, ID: UndefinedID, Perm: PermR},
		{Tag: TagOther, ID: UndefinedID, Perm: PermNone},
	}
	e, _ := a.Find(TagUser, svcUID)
	if got := a.Effective(e); got != PermR {
		t.Errorf("effective = %s, want r--", got)
	}
	if a.HasRWX(svcUID) {
		t.Error("HasRWX must see through the mask")
	}
	if !theGrant().HasRWX(svcUID) {
		t.Error("the grant does give rwx")
	}
	if theGrant().HasRWX(9999) {
		t.Error("an unnamed uid holds nothing")
	}
	// The owner is never capped by the mask.
	owner, _ := a.Find(TagUserObj, UndefinedID)
	if got := a.Effective(owner); got != PermRWX {
		t.Errorf("owner effective = %s, want rwx", got)
	}
}

// ---------------------------------------------------------- filesystem tests

// requireXattrs skips the test when the filesystem under t.TempDir() does not
// carry POSIX ACLs. On coconut /tmp is tmpfs and DOES; a CI container may not,
// and a skipped test has to say which of the two it hit.
func requireXattrs(t *testing.T, dir string) {
	t.Helper()
	err := Set(dir, KindAccess, FromMode(0o755))
	if err == nil {
		return
	}
	if errors.Is(err, ErrUnsupported) {
		t.Skipf("skipping: %s has no POSIX ACL support (setxattr → EOPNOTSUPP); "+
			"run these on an ext4 filesystem such as /rag", dir)
	}
	t.Fatalf("probing ACL support on %s: %v", dir, err)
}

func TestSetGetRemoveOnARealFilesystem(t *testing.T) {
	dir := t.TempDir()
	requireXattrs(t, dir)

	// A directory with no ACL xattr still HAS an access ACL: its mode.
	if err := os.Chmod(dir, 0o750); err != nil {
		t.Fatal(err)
	}
	if err := Remove(dir, KindAccess); err != nil {
		t.Fatal(err)
	}
	got, err := Get(dir, KindAccess)
	if err != nil {
		t.Fatal(err)
	}
	if want := FromMode(0o750); !reflect.DeepEqual(got, want) {
		t.Fatalf("access ACL of a bare dir = %s, want %s", got.Line(), want.Line())
	}
	if got, err := Get(dir, KindDefault); err != nil || got != nil {
		t.Fatalf("default ACL of a bare dir = %v, %v; want nil, nil", got, err)
	}

	// Set → Get is the identity for both kinds.
	want := theGrant()
	for _, kind := range []Kind{KindAccess, KindDefault} {
		if err := Set(dir, kind, want); err != nil {
			t.Fatalf("Set %s: %v", kind, err)
		}
		got, err := Get(dir, kind)
		if err != nil {
			t.Fatalf("Get %s: %v", kind, err)
		}
		if !reflect.DeepEqual(got, want) {
			t.Fatalf("%s round trip = %s, want %s", kind, got.Line(), want.Line())
		}
	}
	// Writing the access ACL rewrote st_mode — the documented side effect.
	fi, err := os.Stat(dir)
	if err != nil {
		t.Fatal(err)
	}
	if got := fi.Mode().Perm(); got != 0o770 {
		// group:: is ---, but `ls` shows the MASK in the group triple.
		t.Errorf("mode after the grant = %#o, want 0770 (owner rwx, mask rwx, other ---)", got)
	}

	// Remove is idempotent.
	for i := 0; i < 2; i++ {
		if err := Remove(dir, KindDefault); err != nil {
			t.Fatalf("Remove(default) #%d: %v", i, err)
		}
	}
	if got, _ := Get(dir, KindDefault); got != nil {
		t.Errorf("default ACL survived Remove: %s", got.Line())
	}
}

func TestSetRefusesADefaultACLOnAFile(t *testing.T) {
	dir := t.TempDir()
	requireXattrs(t, dir)
	f := filepath.Join(dir, "a.txt")
	if err := os.WriteFile(f, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := Set(f, KindDefault, theGrant()); err == nil || !strings.Contains(err.Error(), "not a directory") {
		t.Fatalf("Set(default) on a file = %v, want a refusal", err)
	}
}

func TestSetRefusesAnInvalidACL(t *testing.T) {
	dir := t.TempDir()
	requireXattrs(t, dir)
	bad := ACL{
		{Tag: TagUserObj, ID: UndefinedID, Perm: PermRWX},
		{Tag: TagUser, ID: svcUID, Perm: PermRWX}, // no mask
		{Tag: TagGroupObj, ID: UndefinedID},
		{Tag: TagOther, ID: UndefinedID},
	}
	if err := Set(dir, KindAccess, bad); !errors.Is(err, ErrBadACL) {
		t.Fatalf("Set(invalid) = %v, want ErrBadACL before the syscall", err)
	}
}

// TestDefaultACLIsInherited is the property the whole grant rests on — a file
// created later inside a granted directory carries the named entry without a
// second run — AND the caveat that comes with it, which cost an afternoon to
// find: the kernel clamps the inherited MASK by the GROUP bits of the create
// mode. A 0644 create leaves the service account with r-- however rwx the
// default ACL was; a 0664 create (umask 0002, which is what the ctl spawns
// tenant processes with) leaves it rw-. The runbook says so because the
// filesystem will not.
func TestDefaultACLIsInherited(t *testing.T) {
	dir := t.TempDir()
	requireXattrs(t, dir)
	if err := Set(dir, KindDefault, theGrant()); err != nil {
		t.Fatal(err)
	}
	cases := []struct {
		name string
		mode fs.FileMode
		want Perm
	}{
		{"created 0664 (umask 0002)", 0o664, PermRW},
		{"created 0644 (umask 0022) is clamped to read", 0o644, PermR},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			child := filepath.Join(dir, strings.ReplaceAll(c.name, " ", "_"))
			old := unix.Umask(0)
			err := os.WriteFile(child, []byte("x"), c.mode)
			unix.Umask(old)
			if err != nil {
				t.Fatal(err)
			}
			got, err := Get(child, KindAccess)
			if err != nil {
				t.Fatal(err)
			}
			e, ok := got.Find(TagUser, svcUID)
			if !ok {
				t.Fatalf("the inherited ACL has no entry for %d: %s", svcUID, got.Line())
			}
			if eff := got.Effective(e); eff != c.want {
				t.Errorf("inherited %s has effective %s, want %s (whole ACL: %s)", e, eff, c.want, got.Line())
			}
		})
	}

	// A directory created inside a granted directory inherits BOTH lists, so
	// one recursive grant covers every tenant provisioned afterwards.
	sub := filepath.Join(dir, "sub")
	if err := os.Mkdir(sub, 0o775); err != nil {
		t.Fatal(err)
	}
	for _, kind := range []Kind{KindAccess, KindDefault} {
		got, err := Get(sub, kind)
		if err != nil {
			t.Fatal(err)
		}
		if !got.HasRWX(svcUID) {
			t.Errorf("a new subdirectory's %s ACL does not give rwx: %s", kind, got.Line())
		}
	}
}
