package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"filippo.io/age"

	"github.com/ragstack/ragstack/internal/ctl/seal"
)

// identityEnv points CTL_CONFIG_DIR at a scratch directory and returns the two
// paths `fleet backup-identity init` writes.
func identityEnv(t *testing.T) (dir, idPath, recPath string) {
	t.Helper()
	root := t.TempDir()
	dir = filepath.Join(root, "ctlconfig")
	t.Setenv("CTL_CONFIG_DIR", dir)
	t.Setenv("CTL_STATE_DIR", filepath.Join(root, "state"))
	return dir, filepath.Join(dir, "backup-identity.txt"), filepath.Join(dir, "backup-recipients.txt")
}

func mode(t *testing.T, path string) os.FileMode {
	t.Helper()
	st, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	return st.Mode().Perm()
}

func TestBackupIdentityInitWritesBothFilesAndPrintsOnlyThePublicHalf(t *testing.T) {
	_, idPath, recPath := identityEnv(t)
	rc, out, errOut := capture(t, "fleet", "backup-identity", "init", "--rag-root", t.TempDir())
	if rc != exitOK {
		t.Fatalf("rc = %d, stderr: %s", rc, errOut)
	}
	if got := mode(t, idPath); got != 0o600 {
		t.Errorf("identity mode = %o, want 600", got)
	}
	if got := mode(t, recPath); got != 0o640 {
		t.Errorf("recipients mode = %o, want 640", got)
	}
	body, err := os.ReadFile(idPath)
	if err != nil {
		t.Fatal(err)
	}
	ids, err := age.ParseIdentities(strings.NewReader(string(body)))
	if err != nil || len(ids) != 1 {
		t.Fatalf("the identity file does not parse: %v", err)
	}
	// The secret half never reaches the terminal.
	if strings.Contains(out+errOut, "AGE-SECRET-KEY-") {
		t.Fatal("init printed the private key")
	}
	// The recipient it printed is the identity's, and the file seals to it.
	pub := ids[0].(*age.X25519Identity).Recipient().String()
	if !strings.Contains(out, pub) {
		t.Errorf("init did not print the public recipient")
	}
	s, err := seal.LoadRecipientsSealer(recPath)
	if err != nil {
		t.Fatal(err)
	}
	sealed, err := s.Seal([]byte("payload"))
	if err != nil {
		t.Fatal(err)
	}
	if got, err := seal.Unseal(ids, sealed); err != nil || string(got) != "payload" {
		t.Fatalf("the identity cannot open what the recipients file seals: %v", err)
	}
}

func TestBackupIdentityInitRefusesToOverwrite(t *testing.T) {
	_, idPath, recPath := identityEnv(t)
	if rc, _, errOut := capture(t, "fleet", "backup-identity", "init"); rc != exitOK {
		t.Fatalf("first init rc = %d: %s", rc, errOut)
	}
	before, _ := os.ReadFile(idPath)
	recBefore, _ := os.ReadFile(recPath)
	rc, _, errOut := capture(t, "fleet", "backup-identity", "init")
	if rc != exitRefused || !strings.Contains(errOut, "already exists") {
		t.Fatalf("second init rc = %d, stderr %q; want a refusal", rc, errOut)
	}
	after, _ := os.ReadFile(idPath)
	recAfter, _ := os.ReadFile(recPath)
	if string(before) != string(after) || string(recBefore) != string(recAfter) {
		t.Fatal("a refused init changed a file")
	}
}

func TestBackupIdentityInitRefusesADuplicateRecipient(t *testing.T) {
	dir, idPath, recPath := identityEnv(t)
	fixed, err := age.GenerateX25519Identity()
	if err != nil {
		t.Fatal(err)
	}
	orig := generateBackupIdentity
	generateBackupIdentity = func() (*age.X25519Identity, error) { return fixed, nil }
	t.Cleanup(func() { generateBackupIdentity = orig })

	if err := os.MkdirAll(dir, 0o750); err != nil {
		t.Fatal(err)
	}
	// The recipient is already listed (say, by hand), with no trailing newline.
	if err := os.WriteFile(recPath, []byte(fixed.Recipient().String()), 0o640); err != nil {
		t.Fatal(err)
	}
	rc, _, errOut := capture(t, "fleet", "backup-identity", "init")
	if rc != exitRefused || !strings.Contains(errOut, "already lists") {
		t.Fatalf("rc = %d, stderr %q; want a duplicate refusal", rc, errOut)
	}
	if _, err := os.Stat(idPath); !os.IsNotExist(err) {
		t.Fatal("a refused init left an identity file behind")
	}
}

func TestBackupIdentityInitAppendsToAnExistingRecipientsFile(t *testing.T) {
	dir, _, recPath := identityEnv(t)
	other, _ := age.GenerateX25519Identity()
	if err := os.MkdirAll(dir, 0o750); err != nil {
		t.Fatal(err)
	}
	// No trailing newline: the append must not glue two keys together.
	if err := os.WriteFile(recPath, []byte(other.Recipient().String()), 0o644); err != nil {
		t.Fatal(err)
	}
	if rc, _, errOut := capture(t, "fleet", "backup-identity", "init"); rc != exitOK {
		t.Fatalf("rc = %d: %s", rc, errOut)
	}
	_, fps, err := seal.LoadRecipients(recPath)
	if err != nil || len(fps) != 2 {
		t.Fatalf("recipients after append = %v, %v; want two", fps, err)
	}
	if got := mode(t, recPath); got != 0o644 {
		t.Errorf("an existing file's mode changed to %o", got)
	}
}

func TestBackupIdentityInitDryRunWritesNothing(t *testing.T) {
	dir, _, _ := identityEnv(t)
	rc, out, errOut := capture(t, "fleet", "backup-identity", "init", "--dry-run")
	if rc != exitOK {
		t.Fatalf("rc = %d: %s", rc, errOut)
	}
	if !strings.Contains(out, "backup-identity.txt") || !strings.Contains(out, "backup-recipients.txt") {
		t.Errorf("dry run does not name the paths: %q", out)
	}
	if _, err := os.Stat(dir); !os.IsNotExist(err) {
		t.Fatalf("dry run created %s", dir)
	}
}

func TestBackupIdentityUsage(t *testing.T) {
	identityEnv(t)
	for _, args := range [][]string{{"fleet", "backup-identity"}, {"fleet", "backup-identity", "rotate"},
		{"fleet", "backup-identity", "init", "extra"}} {
		if rc, _, _ := capture(t, args...); rc != exitUsage {
			t.Errorf("%v rc = %d, want usage", args, rc)
		}
	}
}
