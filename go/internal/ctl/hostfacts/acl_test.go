package hostfacts

import (
	"errors"
	"os"
	"os/user"
	"path/filepath"
	"strconv"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/acl"
)

// TestACLProbeReadsWhatTheGrantWrote closes the loop between `fleet grant`
// and doctor on a real filesystem: what the acl package writes is what the
// host probe reports, ACL entry for ACL entry.
func TestACLProbeReadsWhatTheGrantWrote(t *testing.T) {
	dir := t.TempDir()
	granted := acl.ACL{
		{Tag: acl.TagUserObj, ID: acl.UndefinedID, Perm: acl.PermRWX},
		{Tag: acl.TagUser, ID: uint32(os.Getuid()), Perm: acl.PermRWX},
		{Tag: acl.TagGroupObj, ID: acl.UndefinedID, Perm: acl.PermNone},
		{Tag: acl.TagMask, ID: acl.UndefinedID, Perm: acl.PermRWX},
		{Tag: acl.TagOther, ID: acl.UndefinedID, Perm: acl.PermNone},
	}
	if err := acl.Set(dir, acl.KindAccess, granted); err != nil {
		if errors.Is(err, acl.ErrUnsupported) {
			t.Skipf("skipping: %s has no POSIX ACL support; these need an ext4 filesystem", dir)
		}
		t.Fatal(err)
	}
	if err := acl.Set(dir, acl.KindDefault, granted); err != nil {
		t.Fatal(err)
	}

	r := &Real{ProcRoot: "/proc", CheckRoot: dir}
	access, dflt, err := r.ACL(dir)
	if err != nil {
		t.Fatal(err)
	}
	if !access.HasRWX(uint32(os.Getuid())) {
		t.Errorf("access ACL = %s, want rwx for the current uid", access.Line())
	}
	if !dflt.HasRWX(uint32(os.Getuid())) {
		t.Errorf("default ACL = %s, want rwx for the current uid", dflt.Line())
	}

	// A regular file has no default ACL, and asking for one is not an error.
	f := filepath.Join(dir, "a.txt")
	if err := os.WriteFile(f, []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, d, err := r.ACL(f); err != nil || d != nil {
		t.Errorf("ACL of a file = default %v, err %v; want nil, nil", d, err)
	}
}

// TestWritableByOthersCarriesNamedGrants: the mode is 0750 throughout, so the
// old check says "clean" — and the named entry is exactly the thing that
// makes that answer wrong. The probe has to carry it up to doctor.
func TestWritableByOthersCarriesNamedGrants(t *testing.T) {
	dir := t.TempDir()
	if err := os.Chmod(dir, 0o750); err != nil {
		t.Fatal(err)
	}
	stranger := uint32(os.Getuid() + 4242)
	if err := acl.Set(dir, acl.KindAccess, acl.ACL{
		{Tag: acl.TagUserObj, ID: acl.UndefinedID, Perm: acl.PermRWX},
		{Tag: acl.TagUser, ID: stranger, Perm: acl.PermRW},
		{Tag: acl.TagGroupObj, ID: acl.UndefinedID, Perm: acl.PermNone},
		{Tag: acl.TagMask, ID: acl.UndefinedID, Perm: acl.PermRWX},
		{Tag: acl.TagOther, ID: acl.UndefinedID, Perm: acl.PermNone},
	}); err != nil {
		if errors.Is(err, acl.ErrUnsupported) {
			t.Skipf("skipping: %s has no POSIX ACL support", dir)
		}
		t.Fatal(err)
	}
	r := &Real{ProcRoot: "/proc", CheckRoot: dir}
	w, err := r.WritableByOthers(dir)
	if err != nil {
		t.Fatal(err)
	}
	if w.Writable {
		t.Errorf("the MODE is clean (0750): %s is %s", w.Path, w.Reason)
	}
	var found *ACLGrant
	for i, g := range w.ACLGrants {
		if g.ID == stranger && !g.Group {
			found = &w.ACLGrants[i]
		}
	}
	if found == nil {
		t.Fatalf("the named grant was not carried: %+v", w.ACLGrants)
	}
	if found.Perm != "rw-" || found.Path != dir {
		t.Errorf("grant = %+v, want rw- on %s", *found, dir)
	}
	if u, err := user.Current(); err == nil && w.Owner != u.Username {
		t.Errorf("Owner = %q, want %q", w.Owner, u.Username)
	}
	if found.Owner == "" {
		t.Error("a carried grant must name the owner of the component it sits on")
	}
	if want := "uid " + strconv.FormatUint(uint64(stranger), 10); found.Name != want && found.Name == "" {
		t.Errorf("Name = %q, want a resolved name or %q", found.Name, want)
	}
}

// TestReadOnlyACLEntriesAreNotCarried: this probe answers "who can REPLACE
// what the ctl executes". A reader cannot, and carrying it would turn every
// secrets.env grant into a doctor error.
func TestReadOnlyACLEntriesAreNotCarried(t *testing.T) {
	dir := t.TempDir()
	if err := os.Chmod(dir, 0o750); err != nil {
		t.Fatal(err)
	}
	reader := uint32(os.Getuid() + 4242)
	if err := acl.Set(dir, acl.KindAccess, acl.ACL{
		{Tag: acl.TagUserObj, ID: acl.UndefinedID, Perm: acl.PermRWX},
		{Tag: acl.TagUser, ID: reader, Perm: acl.PermR},
		{Tag: acl.TagGroupObj, ID: acl.UndefinedID, Perm: acl.PermNone},
		{Tag: acl.TagMask, ID: acl.UndefinedID, Perm: acl.PermRWX},
		{Tag: acl.TagOther, ID: acl.UndefinedID, Perm: acl.PermNone},
	}); err != nil {
		if errors.Is(err, acl.ErrUnsupported) {
			t.Skipf("skipping: %s has no POSIX ACL support", dir)
		}
		t.Fatal(err)
	}
	w, err := (&Real{ProcRoot: "/proc", CheckRoot: dir}).WritableByOthers(dir)
	if err != nil {
		t.Fatal(err)
	}
	for _, g := range w.ACLGrants {
		if g.ID == reader {
			t.Fatalf("a read-only entry was carried as a write grant: %+v", g)
		}
	}
}

// TestMaskedGrantIsNotCarried: a named entry the mask cuts down to r-- grants
// no write, and reporting the stored bits rather than the effective ones
// would be a finding with nothing behind it.
func TestMaskedGrantIsNotCarried(t *testing.T) {
	dir := t.TempDir()
	if err := os.Chmod(dir, 0o750); err != nil {
		t.Fatal(err)
	}
	stranger := uint32(os.Getuid() + 4242)
	if err := acl.Set(dir, acl.KindAccess, acl.ACL{
		{Tag: acl.TagUserObj, ID: acl.UndefinedID, Perm: acl.PermRWX},
		{Tag: acl.TagUser, ID: stranger, Perm: acl.PermRWX}, // stored rwx …
		{Tag: acl.TagGroupObj, ID: acl.UndefinedID, Perm: acl.PermNone},
		{Tag: acl.TagMask, ID: acl.UndefinedID, Perm: acl.PermR}, // … capped to r--
		{Tag: acl.TagOther, ID: acl.UndefinedID, Perm: acl.PermNone},
	}); err != nil {
		if errors.Is(err, acl.ErrUnsupported) {
			t.Skipf("skipping: %s has no POSIX ACL support", dir)
		}
		t.Fatal(err)
	}
	w, err := (&Real{ProcRoot: "/proc", CheckRoot: dir}).WritableByOthers(dir)
	if err != nil {
		t.Fatal(err)
	}
	for _, g := range w.ACLGrants {
		if g.ID == stranger {
			t.Fatalf("a masked-off entry was carried: %+v", g)
		}
	}
}

// TestTheGrantDoesNotReadAsGroupWritable is the regression that nearly made
// the whole feature unusable: writing an access ACL sets st_mode's group
// triple to the MASK, so a directory whose group:: is --- shows as 0770 and
// the mode-only check called it "group-writable by cels" — on every single
// path `fleet grant` touched. The ACL is the authority once a mask exists.
func TestTheGrantDoesNotReadAsGroupWritable(t *testing.T) {
	cases := []struct {
		name      string
		groupObj  acl.Perm
		wantDirty bool
	}{
		{"the grant: group:: is ---", acl.PermNone, false},
		{"a genuinely group-writable ACL", acl.PermRWX, true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			dir := t.TempDir()
			if err := os.Chmod(dir, 0o750); err != nil {
				t.Fatal(err)
			}
			err := acl.Set(dir, acl.KindAccess, acl.ACL{
				{Tag: acl.TagUserObj, ID: acl.UndefinedID, Perm: acl.PermRWX},
				{Tag: acl.TagUser, ID: uint32(os.Getuid()), Perm: acl.PermRWX},
				{Tag: acl.TagGroupObj, ID: acl.UndefinedID, Perm: c.groupObj},
				{Tag: acl.TagMask, ID: acl.UndefinedID, Perm: acl.PermRWX},
				{Tag: acl.TagOther, ID: acl.UndefinedID, Perm: acl.PermNone},
			})
			if errors.Is(err, acl.ErrUnsupported) {
				t.Skipf("skipping: %s has no POSIX ACL support", dir)
			}
			if err != nil {
				t.Fatal(err)
			}
			if fi, serr := os.Stat(dir); serr == nil && fi.Mode().Perm()&0o020 == 0 {
				t.Fatalf("setup: the mask should have set the mode's group write bit (mode %#o)", fi.Mode().Perm())
			}
			w, err := (&Real{ProcRoot: "/proc", CheckRoot: dir}).WritableByOthers(dir)
			if err != nil {
				t.Fatal(err)
			}
			if w.Writable != c.wantDirty {
				t.Fatalf("Writable = %v, want %v (%s is %s)", w.Writable, c.wantDirty, w.Path, w.Reason)
			}
		})
	}
}
