package main

// `fleet grant` is the one command in this binary that writes to the
// filesystem directly instead of posting an op_request, so these tests assert
// on the filesystem and on the exit code — the two things an operator and a
// script respectively act on.

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/acl"
)

// grantRoot builds a /rag-shaped tree and skips the test when the filesystem
// underneath cannot hold POSIX ACLs.
func grantRoot(t *testing.T) string {
	t.Helper()
	ragRoot := t.TempDir()
	for _, d := range []string{"data/tenants/dev/config", "repos/tenants/dev", "backups/tenants"} {
		if err := os.MkdirAll(filepath.Join(ragRoot, d), 0o750); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(ragRoot, "data/tenants/dev/config/secrets.env"), []byte("K=v\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := acl.Set(filepath.Join(ragRoot, "data", "tenants"), acl.KindAccess, acl.FromMode(0o750)); err != nil {
		if errors.Is(err, acl.ErrUnsupported) {
			t.Skipf("skipping: %s has no POSIX ACL support (needs ext4, as /rag is)", ragRoot)
		}
		t.Fatal(err)
	}
	return ragRoot
}

// self is the account running the tests: the only one it can grant to without
// touching a real service account.
func self(t *testing.T) (string, uint32) {
	t.Helper()
	uid := os.Getuid()
	name := nameOfUID(uid)
	if strings.HasPrefix(name, "uid ") {
		t.Skip("skipping: the current uid has no name in NSS, so --user has nothing to resolve")
	}
	return name, uint32(uid)
}

func TestFleetGrantWritesTheACLs(t *testing.T) {
	ragRoot := grantRoot(t)
	name, uid := self(t)

	rc, out, errs := capture(t, "fleet", "grant", "--user", name, "--rag-root", ragRoot)
	if rc != exitOK {
		t.Fatalf("rc %d (want 0): %s %s", rc, out, errs)
	}
	for _, rel := range []string{"data/tenants", "data/tenants/dev", "repos/tenants", "backups/tenants"} {
		p := filepath.Join(ragRoot, rel)
		access, err := acl.Get(p, acl.KindAccess)
		if err != nil {
			t.Fatal(err)
		}
		if !access.HasRWX(uid) {
			t.Errorf("%s: access ACL = %s, want rwx for %s", rel, access.Line(), name)
		}
		dflt, err := acl.Get(p, acl.KindDefault)
		if err != nil {
			t.Fatal(err)
		}
		if !dflt.HasRWX(uid) {
			t.Errorf("%s: default ACL = %s, want rwx (new files must inherit the grant)", rel, dflt.Line())
		}
	}
	// A secret is readable, never writable, however the rest of the tree went.
	secret := filepath.Join(ragRoot, "data/tenants/dev/config/secrets.env")
	access, err := acl.Get(secret, acl.KindAccess)
	if err != nil {
		t.Fatal(err)
	}
	e, ok := access.Find(acl.TagUser, uid)
	if !ok || access.Effective(e) != acl.PermRead {
		t.Errorf("secrets.env: entry %s (effective %s), want r--", e, access.Effective(e))
	}
	if !strings.Contains(out, "changed") {
		t.Errorf("the summary line is missing: %q", out)
	}
}

func TestFleetGrantDryRunWritesNothing(t *testing.T) {
	ragRoot := grantRoot(t)
	name, uid := self(t)

	rc, out, errs := capture(t, "fleet", "grant", "--user", name, "--rag-root", ragRoot, "--dry-run")
	if rc != exitOK {
		t.Fatalf("rc %d (want 0): %s %s", rc, out, errs)
	}
	access, err := acl.Get(filepath.Join(ragRoot, "data", "tenants"), acl.KindAccess)
	if err != nil {
		t.Fatal(err)
	}
	if access.HasRWX(uid) {
		t.Fatalf("the dry run wrote an ACL: %s", access.Line())
	}
	if !strings.Contains(out, "dry run") {
		t.Errorf("a dry run has to say so: %q", out)
	}
	if !strings.Contains(out, "before ") || !strings.Contains(out, "after  ") {
		t.Errorf("the dry run must show before → after, the only thing there is to review: %q", out)
	}
}

func TestFleetGrantJSON(t *testing.T) {
	ragRoot := grantRoot(t)
	name, _ := self(t)

	rc, out, errs := capture(t, "fleet", "grant", "--user", name, "--rag-root", ragRoot, "--json", "--dry-run")
	if rc != exitOK {
		t.Fatalf("rc %d: %s", rc, errs)
	}
	var got grantResult
	if err := json.Unmarshal([]byte(out), &got); err != nil {
		t.Fatalf("output is not JSON: %v\n%s", err, out)
	}
	if got.User != name || !got.DryRun || got.Revoke {
		t.Errorf("result header = %+v", got)
	}
	if got.Changed == 0 || len(got.Changes) == 0 {
		t.Fatalf("no changes reported: %+v", got)
	}
	for _, c := range got.Changes {
		if c.Path == "" || (c.Note == "" && c.After == "") {
			t.Errorf("incomplete change row: %+v", c)
		}
	}
}

func TestFleetGrantRevokeUndoesIt(t *testing.T) {
	ragRoot := grantRoot(t)
	name, uid := self(t)

	if rc, _, errs := capture(t, "fleet", "grant", "--user", name, "--rag-root", ragRoot); rc != exitOK {
		t.Fatalf("setup: rc %d %s", rc, errs)
	}
	rc, out, errs := capture(t, "fleet", "grant", "--user", name, "--rag-root", ragRoot, "--revoke")
	if rc != exitOK {
		t.Fatalf("rc %d: %s %s", rc, out, errs)
	}
	if !strings.Contains(out, "revoked from") {
		t.Errorf("the summary must say it revoked: %q", out)
	}
	for _, rel := range []string{"data/tenants", "data/tenants/dev", "data/tenants/dev/config/secrets.env"} {
		access, err := acl.Get(filepath.Join(ragRoot, rel), acl.KindAccess)
		if err != nil {
			t.Fatal(err)
		}
		if _, ok := access.Find(acl.TagUser, uid); ok {
			t.Errorf("%s still names the grantee: %s", rel, access.Line())
		}
	}
}

// TestFleetGrantRefusesARootYouDoNotOwn is exit code 3: the ctl never tries
// an operation the kernel will refuse, it says who has to run it instead.
func TestFleetGrantRefusesARootYouDoNotOwn(t *testing.T) {
	name, _ := self(t)
	// /usr is owned by root on every host this ever runs on, and the test
	// process is not root (the whole premise of PR-D2).
	if os.Getuid() == 0 {
		t.Skip("skipping: running as root, which this design never does")
	}
	// --rag-root / so /usr passes the containment check and reaches the
	// ownership one; outside the rag root a grant is a usage error instead.
	rc, out, errs := capture(t, "fleet", "grant", "--user", name, "--roots", "/usr", "--rag-root", "/")
	if rc != exitRefused {
		t.Fatalf("rc %d (want %d refused): %s %s", rc, exitRefused, out, errs)
	}
	if !strings.Contains(errs, "owned by") || !strings.Contains(errs, "fleet grant") {
		t.Errorf("the refusal must name the owner and the command they should run: %q", errs)
	}
}

func TestFleetGrantUsageErrors(t *testing.T) {
	cases := []struct {
		name string
		argv []string
		want int
	}{
		{"no --user", []string{"fleet", "grant"}, exitUsage},
		{"a relative root", []string{"fleet", "grant", "--user", "nobody", "--roots", "relative/path"}, exitUsage},
		{"an account that does not exist", []string{"fleet", "grant", "--user", "no-such-account-" + strconv.Itoa(os.Getpid())}, exitError},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			rc, _, _ := capture(t, c.argv...)
			if rc != c.want {
				t.Errorf("rc %d, want %d", rc, c.want)
			}
		})
	}
}

// TestFleetGrantIsNotADaemonOp: the usage text has to say WHY this one
// command bypasses the daemon, because every other mutation goes through it
// and the next reader will ask.
func TestFleetGrantUsageExplainsWhyItIsLocal(t *testing.T) {
	_, _, errs := capture(t, "fleet", "grant")
	for _, want := range []string{"OWNER", "daemon", "local action"} {
		if !strings.Contains(errs, want) {
			t.Errorf("usage does not mention %q:\n%s", want, errs)
		}
	}
}
