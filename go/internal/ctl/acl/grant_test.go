package acl

import (
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
)

// grantTree builds the shape a managed root actually has: a directory, a
// config subdirectory with a secret in it, a backup copy of that secret, a
// plain file and an executable.
func grantTree(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	requireXattrs(t, root)
	mk := func(rel string, mode os.FileMode) string {
		p := filepath.Join(root, rel)
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte("x"), mode); err != nil {
			t.Fatal(err)
		}
		if err := os.Chmod(p, mode); err != nil { // umask does not get a vote
			t.Fatal(err)
		}
		return p
	}
	mk("config/tenant.env", 0o640)
	mk("config/secrets.env", 0o600)
	mk("config/secrets.env.bak-20260817", 0o600)
	mk("bin/up.sh", 0o750)
	mk("logs/api-dev.log", 0o644)
	if err := os.Chmod(root, 0o750); err != nil {
		t.Fatal(err)
	}
	return root
}

func accessOf(t *testing.T, p string) ACL {
	t.Helper()
	a, err := Get(p, KindAccess)
	if err != nil {
		t.Fatal(err)
	}
	return a
}

// noDeny keeps the test's own gid out of the deny list, so a case that is not
// about the cels rule does not accidentally depend on it.
func noDeny() GrantOptions { return GrantOptions{DenyGroups: []uint32{}} }

func TestGrantOnADirectory(t *testing.T) {
	root := grantTree(t)
	c, err := Grant(root, svcUID, noDeny())
	if err != nil {
		t.Fatal(err)
	}
	if !c.Changed() {
		t.Fatalf("grant reported no change: %+v", c)
	}
	got := accessOf(t, root)
	if e, ok := got.Find(TagUser, svcUID); !ok || got.Effective(e) != PermRWX {
		t.Fatalf("access ACL = %s, want an effective rwx for %d", got.Line(), svcUID)
	}
	if e, _ := got.Find(TagOther, UndefinedID); e.Perm != PermNone {
		t.Errorf("other:: = %s, want --- (a tenant root is not world-readable)", e.Perm)
	}
	if e, _ := got.Find(TagUserObj, UndefinedID); e.Perm != PermRWX {
		t.Errorf("the owner's bits changed: user:: = %s, want rwx (0750's owner triple)", e.Perm)
	}
	// The default ACL is what makes the grant survive the next tenant.
	dflt, err := Get(root, KindDefault)
	if err != nil {
		t.Fatal(err)
	}
	if !dflt.HasRWX(svcUID) {
		t.Errorf("default ACL = %s, want rwx for %d", dflt.Line(), svcUID)
	}
	// Idempotent: a second run is a no-op row, not a second write.
	c2, err := Grant(root, svcUID, noDeny())
	if err != nil {
		t.Fatal(err)
	}
	if c2.Changed() {
		t.Errorf("a repeated grant changed something: %s → %s", c2.Before, c2.After)
	}
}

func TestGrantOnFiles(t *testing.T) {
	root := grantTree(t)
	changes, err := Apply(root, svcUID, GrantOptions{Recursive: true, DenyGroups: []uint32{}})
	if err != nil {
		t.Fatal(err)
	}
	if len(changes) == 0 {
		t.Fatal("the recursive grant visited nothing")
	}
	cases := []struct {
		rel  string
		want Perm
		why  string
	}{
		{"config/tenant.env", PermRW, "a plain config file is read/write"},
		{"config/secrets.env", PermR, "a secret is read-only"},
		{"config/secrets.env.bak-20260817", PermR, "a historical copy of a secret is just as sensitive"},
		{"logs/api-dev.log", PermRW, "the service account appends to the API log"},
		{"bin/up.sh", PermRW, "an executable is still only rw- for the grantee"},
		{"config", PermRWX, "a directory is rwx so the account can traverse and create"},
	}
	for _, c := range cases {
		p := filepath.Join(root, c.rel)
		got := accessOf(t, p)
		e, ok := got.Find(TagUser, svcUID)
		if !ok {
			t.Errorf("%s: no entry for %d (%s): %s", c.rel, svcUID, c.why, got.Line())
			continue
		}
		if eff := got.Effective(e); eff != c.want {
			t.Errorf("%s: effective %s, want %s (%s) — %s", c.rel, eff, c.want, c.why, got.Line())
		}
	}
	// "executables keep their x bit for the owner": never chmod, so the
	// owner's triple comes across untouched.
	up := accessOf(t, filepath.Join(root, "bin", "up.sh"))
	if e, _ := up.Find(TagUserObj, UndefinedID); e.Perm != PermRWX {
		t.Errorf("up.sh owner bits = %s, want rwx (it was 0750)", e.Perm)
	}
	if fi, err := os.Stat(filepath.Join(root, "bin", "up.sh")); err != nil || fi.Mode().Perm()&0o100 == 0 {
		t.Errorf("up.sh lost its owner x bit: %v %v", fi.Mode().Perm(), err)
	}
	// A regular file never gets a default ACL (only directories carry one).
	if d, err := Get(filepath.Join(root, "config", "tenant.env"), KindDefault); err != nil || d != nil {
		t.Errorf("a file grew a default ACL: %v %v", d, err)
	}
}

func TestGrantZeroesTheOwningGroupOnlyWhenItIsDenied(t *testing.T) {
	root := grantTree(t)
	gid := gidOf(t, root)

	// Not in the deny list: the group's bits are left exactly as they were.
	if _, err := Grant(root, svcUID, GrantOptions{DenyGroups: []uint32{}}); err != nil {
		t.Fatal(err)
	}
	got := accessOf(t, root)
	if e, _ := got.Find(TagGroupObj, UndefinedID); e.Perm != PermRX() {
		t.Errorf("group:: = %s, want r-x (0750's group triple, kept)", e.Perm)
	}

	// In the deny list — the cels case — the group is zeroed. That is the
	// whole point: 1869 people must not get what the one account gets.
	if _, err := Grant(root, svcUID, GrantOptions{DenyGroups: []uint32{gid}}); err != nil {
		t.Fatal(err)
	}
	got = accessOf(t, root)
	if e, _ := got.Find(TagGroupObj, UndefinedID); e.Perm != PermNone {
		t.Fatalf("group:: = %s, want --- for a denied group: %s", e.Perm, got.Line())
	}
	if !got.HasRWX(svcUID) {
		t.Errorf("zeroing the group also took the grant away: %s", got.Line())
	}
}

// PermRX is spelled out here rather than as a package constant: it is a test
// expectation about 0750, not a permission the grant ever writes.
func PermRX() Perm { return PermRead | PermExecute }

func gidOf(t *testing.T, p string) uint32 {
	t.Helper()
	fi, err := os.Stat(p)
	if err != nil {
		t.Fatal(err)
	}
	return statGID(t, fi)
}

func TestDryRunWritesNothing(t *testing.T) {
	root := grantTree(t)
	before := accessOf(t, root).Line()
	changes, err := Apply(root, svcUID, GrantOptions{Recursive: true, DryRun: true, DenyGroups: []uint32{}})
	if err != nil {
		t.Fatal(err)
	}
	var planned int
	for _, c := range changes {
		if c.Changed() {
			planned++
		}
	}
	if planned == 0 {
		t.Fatal("the dry run planned nothing to do")
	}
	if got := accessOf(t, root).Line(); got != before {
		t.Fatalf("the dry run wrote: %s → %s", before, got)
	}
	if got, err := Get(root, KindDefault); err != nil || got != nil {
		t.Fatalf("the dry run set a default ACL: %v %v", got, err)
	}
}

func TestRecursiveWalkNeverFollowsASymlink(t *testing.T) {
	root := grantTree(t)
	outside := t.TempDir()
	victim := filepath.Join(outside, "victim.txt")
	if err := os.WriteFile(victim, []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(root, "escape")); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(victim, filepath.Join(root, "config", "link.env")); err != nil {
		t.Fatal(err)
	}

	changes, err := Apply(root, svcUID, GrantOptions{Recursive: true, DenyGroups: []uint32{}})
	if err != nil {
		t.Fatal(err)
	}
	for _, c := range changes {
		if strings.HasPrefix(c.Path, outside) {
			t.Fatalf("the walk left the root: %s", c.Path)
		}
	}
	got := accessOf(t, victim)
	if _, ok := got.Find(TagUser, svcUID); ok {
		t.Fatalf("a symlink redirected the grant onto %s: %s", victim, got.Line())
	}
	// The link itself is reported — an operator has to see a link inside a
	// managed root — but never granted.
	var sawLink bool
	for _, c := range changes {
		if filepath.Base(c.Path) == "escape" {
			sawLink = true
			if c.Changed() || !strings.Contains(c.Note, "symlink") {
				t.Errorf("the symlink row is %+v, want a skip", c)
			}
		}
	}
	if !sawLink {
		t.Error("the symlink was not reported at all")
	}
}

func TestPathsNotOwnedAreSkipped(t *testing.T) {
	root := grantTree(t)
	// Self is a uid this process is not: every path is then somebody else's,
	// which is the shape a root under another account has.
	changes, err := Apply(root, svcUID, GrantOptions{Recursive: true, Self: os.Getuid() + 1000})
	if err != nil {
		t.Fatal(err)
	}
	if len(changes) == 0 {
		t.Fatal("nothing was visited")
	}
	for _, c := range changes {
		if c.Note != NotOwned {
			t.Fatalf("%s: note %q, want %q", c.Path, c.Note, NotOwned)
		}
	}
	if got := accessOf(t, root); got.HasRWX(svcUID) {
		t.Errorf("a path nobody owns was written anyway: %s", got.Line())
	}
	changed, unchanged, skipped := Summarize(changes)
	if changed != 0 || unchanged != 0 || skipped != len(changes) {
		t.Errorf("Summarize = %d/%d/%d, want 0/0/%d", changed, unchanged, skipped, len(changes))
	}
	if reasons := SkipReasons(changes); len(reasons) != 1 || !strings.Contains(reasons[0], NotOwned) {
		t.Errorf("SkipReasons = %v, want one %q line", reasons, NotOwned)
	}
}

func TestRevokeRemovesTheEntryAndTheMask(t *testing.T) {
	root := grantTree(t)
	if _, err := Apply(root, svcUID, GrantOptions{Recursive: true, DenyGroups: []uint32{}}); err != nil {
		t.Fatal(err)
	}
	if !accessOf(t, root).HasRWX(svcUID) {
		t.Fatal("setup: the grant did not land")
	}
	changes, err := Apply(root, svcUID, GrantOptions{Recursive: true, Revoke: true, DenyGroups: []uint32{}})
	if err != nil {
		t.Fatal(err)
	}
	if len(changes) == 0 {
		t.Fatal("the revoke visited nothing")
	}
	for _, p := range []string{root, filepath.Join(root, "config"), filepath.Join(root, "config", "secrets.env")} {
		got := accessOf(t, p)
		if _, ok := got.Find(TagUser, svcUID); ok {
			t.Errorf("%s still names %d: %s", p, svcUID, got.Line())
		}
		if _, ok := got.Find(TagMask, UndefinedID); ok {
			t.Errorf("%s kept a mask with nothing to cap: %s", p, got.Line())
		}
		if err := got.Validate(); err != nil {
			t.Errorf("%s: revoke left an invalid ACL: %v", p, err)
		}
	}
	if d, err := Get(root, KindDefault); err != nil {
		t.Fatal(err)
	} else if _, ok := d.Find(TagUser, svcUID); ok {
		t.Errorf("the default ACL still names %d: %s", svcUID, d.Line())
	}
}

// TestRevokeDoesNotWidenTheGroup: while the named entry existed the mask was
// the group's real ceiling. Dropping the mask has to fold it in, or removing
// one account's grant hands the owning group more than it had.
func TestRevokeDoesNotWidenTheGroup(t *testing.T) {
	root := t.TempDir()
	requireXattrs(t, root)
	if err := Set(root, KindAccess, ACL{
		{Tag: TagUserObj, ID: UndefinedID, Perm: PermRWX},
		{Tag: TagUser, ID: svcUID, Perm: PermRWX},
		{Tag: TagGroupObj, ID: UndefinedID, Perm: PermRWX},
		{Tag: TagMask, ID: UndefinedID, Perm: PermR}, // the group really has r--
		{Tag: TagOther, ID: UndefinedID, Perm: PermNone},
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := Revoke(root, svcUID, noDeny()); err != nil {
		t.Fatal(err)
	}
	got := accessOf(t, root)
	if e, _ := got.Find(TagGroupObj, UndefinedID); e.Perm != PermR {
		t.Fatalf("group:: = %s after the revoke, want r-- (the mask it was under): %s", e.Perm, got.Line())
	}
}

// TestGrantKeepsOtherPeoplesEntries: `fleet grant` grants ONE user. Another
// account's entry is somebody's decision, and doctor is what reports it — the
// grant does not silently undo it.
func TestGrantKeepsOtherPeoplesEntries(t *testing.T) {
	root := t.TempDir()
	requireXattrs(t, root)
	if err := Set(root, KindAccess, ACL{
		{Tag: TagUserObj, ID: UndefinedID, Perm: PermRWX},
		{Tag: TagUser, ID: 4242, Perm: PermRW},
		{Tag: TagGroupObj, ID: UndefinedID, Perm: PermNone},
		{Tag: TagGroup, ID: 5150, Perm: PermR},
		{Tag: TagMask, ID: UndefinedID, Perm: PermRW},
		{Tag: TagOther, ID: UndefinedID, Perm: PermNone},
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := Grant(root, svcUID, noDeny()); err != nil {
		t.Fatal(err)
	}
	got := accessOf(t, root)
	if e, ok := got.Find(TagUser, 4242); !ok || e.Perm != PermRW {
		t.Errorf("the other named user was dropped: %s", got.Line())
	}
	if e, ok := got.Find(TagGroup, 5150); !ok || e.Perm != PermR {
		t.Errorf("the named group was dropped: %s", got.Line())
	}
	if !got.HasRWX(svcUID) {
		t.Errorf("the new grant did not land: %s", got.Line())
	}
	if err := got.Validate(); err != nil {
		t.Errorf("the merged ACL is invalid: %v (%s)", err, got.Line())
	}
}

func TestApplyRefusesASymlinkRoot(t *testing.T) {
	dir := t.TempDir()
	requireXattrs(t, dir)
	link := filepath.Join(dir, "link")
	if err := os.Symlink(dir, link); err != nil {
		t.Fatal(err)
	}
	if _, err := Apply(link, svcUID, GrantOptions{}); err == nil || !strings.Contains(err.Error(), "symlink") {
		t.Fatalf("Apply on a symlink root = %v, want a refusal", err)
	}
}

func TestReadOnlyGlobsAreConfigurable(t *testing.T) {
	root := grantTree(t)
	// An explicitly empty list means "nothing is read-only" — the escape
	// hatch for a root whose secret files the account really must rewrite.
	if _, err := Apply(root, svcUID, GrantOptions{Recursive: true, ReadOnly: []string{}, DenyGroups: []uint32{}}); err != nil {
		t.Fatal(err)
	}
	got := accessOf(t, filepath.Join(root, "config", "secrets.env"))
	e, _ := got.Find(TagUser, svcUID)
	if got.Effective(e) != PermRW {
		t.Errorf("with no read-only globs secrets.env is rw-, got %s", got.Effective(e))
	}
	if DefaultReadOnly[0] != "secrets.env" {
		t.Errorf("the default read-only list changed shape: %v", DefaultReadOnly)
	}
}

func statGID(t *testing.T, fi os.FileInfo) uint32 {
	t.Helper()
	st, ok := fi.Sys().(*syscall.Stat_t)
	if !ok {
		t.Fatal("no stat information")
	}
	return st.Gid
}

// The default ACL of a granted directory names the tree's owner as well as
// the granted account: a file the granted account creates there is owned by
// it and inherits only the default's named entries, so without the owner's
// entry the owner is locked out of its own tree's new files.
func TestDefaultACLNamesTheOwnerTooSoNewFilesStayShared(t *testing.T) {
	base := ACL{
		{Tag: TagUserObj, ID: UndefinedID, Perm: PermRWX},
		{Tag: TagUser, ID: 10078, Perm: PermRWX},
		{Tag: TagGroupObj, ID: UndefinedID, Perm: PermNone},
		{Tag: TagMask, ID: UndefinedID, Perm: PermRWX},
		{Tag: TagOther, ID: UndefinedID, Perm: PermNone},
	}
	got := withOwnerEntry(base.Clone(), 3581, 10078)
	if e, ok := got.Find(TagUser, 3581); !ok || e.Perm != PermRWX {
		t.Fatalf("owner entry missing or wrong: %s", got.String())
	}
	if err := got.Validate(); err != nil {
		t.Fatalf("default ACL with the owner entry does not validate: %v", err)
	}
	// Owner == granted: nothing to add.
	if same := withOwnerEntry(base.Clone(), 10078, 10078); len(same) != len(base) {
		t.Errorf("an owner who is the granted account got a second entry: %s", same.String())
	}
}
