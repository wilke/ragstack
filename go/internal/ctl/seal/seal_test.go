package seal

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"filippo.io/age"
)

// recipientsFile writes a recipients file and returns its path.
func recipientsFile(t *testing.T, lines ...string) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "backup-recipients.txt")
	if err := os.WriteFile(p, []byte(strings.Join(lines, "\n")+"\n"), 0o640); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestSealRoundTripsToTwoRecipients(t *testing.T) {
	// Two identities, because the case that matters operationally is a second
	// recipient added later: every one of them must be able to open the
	// payload on its own.
	a, err := age.GenerateX25519Identity()
	if err != nil {
		t.Fatal(err)
	}
	b, err := age.GenerateX25519Identity()
	if err != nil {
		t.Fatal(err)
	}
	path := recipientsFile(t,
		"# the operators who may open a bundle's secrets",
		a.Recipient().String(),
		"",
		b.Recipient().String(),
	)

	recipients, fingerprints, err := LoadRecipients(path)
	if err != nil {
		t.Fatal(err)
	}
	if len(recipients) != 2 || len(fingerprints) != 2 {
		t.Fatalf("loaded %d recipients and %d fingerprints, want 2 of each", len(recipients), len(fingerprints))
	}
	for _, fp := range fingerprints {
		// sha256:<16 hex> — what a manifest records so a bundle says who can
		// open it without carrying the keys themselves.
		if !strings.HasPrefix(fp, "sha256:") || len(fp) != len("sha256:")+16 {
			t.Errorf("fingerprint %q is not sha256:<16 hex>", fp)
		}
	}
	if fingerprints[0] == fingerprints[1] {
		t.Error("two different recipients share a fingerprint")
	}

	secret := []byte("API_KEYS=abc\nTENANT_PG_PASSWORD=hunter2\n")
	sealed, err := Seal(recipients, secret)
	if err != nil {
		t.Fatal(err)
	}
	// The payload is not the plaintext, which is the whole point of writing it
	// into a directory on a shared host.
	if strings.Contains(string(sealed), "hunter2") {
		t.Fatal("the sealed payload carries the plaintext")
	}
	for name, id := range map[string]age.Identity{"the first": a, "the second": b} {
		got, err := Unseal([]age.Identity{id}, sealed)
		if err != nil {
			t.Fatalf("%s identity could not open the payload: %v", name, err)
		}
		if string(got) != string(secret) {
			t.Errorf("%s identity read %q", name, got)
		}
	}
}

func TestUnsealRefusesAnIdentityThatIsNotARecipient(t *testing.T) {
	a, _ := age.GenerateX25519Identity()
	stranger, _ := age.GenerateX25519Identity()
	sealed, err := Seal([]age.Recipient{a.Recipient()}, []byte("secret"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := Unseal([]age.Identity{stranger}, sealed); err == nil {
		t.Fatal("an identity that is not a recipient opened the payload")
	}
	if _, err := Unseal(nil, sealed); err == nil {
		t.Fatal("no identity at all opened the payload")
	}
}

func TestLoadRecipientsAcceptsAnSSHPublicKey(t *testing.T) {
	// The operators of this host already have an SSH key in authorized_keys;
	// asking them to mint a second key type for backups is how a recipients
	// file ends up empty.
	//
	// Both key types, because an operator's existing key may be either. These
	// are throwaway PUBLIC keys generated for this test; their private halves
	// were never kept.
	const (
		sshEd25519 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIG7kPfUj+Ik0dlZ9BBpl/nIRgP72hkP7Arr42HTHFoPw ctl-backup-test@example"
		sshRSA     = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCvzkXdrtNyIwj+kVTW4AXQlHIIt70gaNs6drb4G6USiywQKney36hQq8ZmqXGPyLvXyD/q7GwmzPXcAXcQ8ygDA0/fytrWkb581QLk3laZnBwDzPlpTJyI/GNlWF7+WGUKott0bwYlwpQ4+sDPC9esa0Z1Eslu90W+nNY4ASQiwT5NrooSlxZGnVHRVoTk7itiL0ONEn99af4IthjViZQE2zpZW49JvMxrh3YKCIJHeXIy9znECVvJNUs7WxddpXcSagXLa0ZSV1gOG26QTlVw83yE4UqBIy1GKxp1MjPViolFnq4BFsVuC7uyP9j8FPEMqD3xl+G7GMOcD4o5Ctqx ctl-backup-test-rsa@example"
	)
	recipients, fingerprints, err := LoadRecipients(recipientsFile(t, sshEd25519, sshRSA))
	if err != nil {
		t.Fatalf("an SSH recipient was refused: %v", err)
	}
	if len(recipients) != 2 || len(fingerprints) != 2 {
		t.Fatalf("loaded %d recipients, want 2", len(recipients))
	}
}

func TestLoadRecipientsRefusesAMalformedLineWithoutQuotingIt(t *testing.T) {
	// The line is not echoed: the file is edited by hand next to a file of
	// PRIVATE keys, and an error message is the cheapest way for a paste into
	// the wrong file to end up in a log.
	const pasted = "AGE-SECRET-KEY-1QQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQSRY3CV"
	path := recipientsFile(t, "# ours", pasted)
	_, _, err := LoadRecipients(path)
	if err == nil {
		t.Fatal("a malformed recipient line was accepted")
	}
	if strings.Contains(err.Error(), pasted) {
		t.Fatalf("the error quotes the line back: %q", err)
	}
	if !strings.Contains(err.Error(), "line 2") {
		t.Errorf("error = %q, want it to name the line", err)
	}
	if errors.Is(err, ErrNoRecipients) {
		// A typo must NOT read as "no recipients": that would silently write a
		// bundle with no secrets in it because somebody mistyped a key.
		t.Error("a malformed line was reported as ErrNoRecipients")
	}
}

func TestLoadRecipientsReportsAnAbsentOrEmptyFileAsErrNoRecipients(t *testing.T) {
	// ErrNoRecipients is the signal the backup turns into "secrets excluded"
	// plus a warning, rather than a failure.
	if _, _, err := LoadRecipients(filepath.Join(t.TempDir(), "absent.txt")); !errors.Is(err, ErrNoRecipients) {
		t.Errorf("an absent recipients file = %v, want ErrNoRecipients", err)
	}
	empty := recipientsFile(t, "# nobody is configured yet", "")
	if _, _, err := LoadRecipients(empty); !errors.Is(err, ErrNoRecipients) {
		t.Errorf("a comment-only recipients file = %v, want ErrNoRecipients", err)
	}
}

func TestSealWithNoRecipientsIsErrNoRecipients(t *testing.T) {
	if _, err := Seal(nil, []byte("secret")); !errors.Is(err, ErrNoRecipients) {
		t.Fatalf("Seal to nobody = %v, want ErrNoRecipients", err)
	}
}

func TestEnvelopeSealerRereadsTheRecipientsFileEachTime(t *testing.T) {
	a, _ := age.GenerateX25519Identity()
	b, _ := age.GenerateX25519Identity()
	path := recipientsFile(t, a.Recipient().String())
	s := NewEnvelopeSealer(path)

	first, fps, err := s.Seal([]byte("secret"))
	if err != nil {
		t.Fatal(err)
	}
	if len(fps) != 1 {
		t.Fatalf("sealed to %d recipients, want 1", len(fps))
	}
	if _, err := Unseal([]age.Identity{b}, first); err == nil {
		t.Fatal("the second identity could open a payload it was not a recipient of")
	}

	// An operator adds a colleague. The NEXT seal must use them — a sealer
	// that cached the list at construction would keep sealing to the old set
	// until somebody restarted the daemon.
	if err := os.WriteFile(path, []byte(a.Recipient().String()+"\n"+b.Recipient().String()+"\n"), 0o640); err != nil {
		t.Fatal(err)
	}
	second, fps, err := s.Seal([]byte("secret"))
	if err != nil {
		t.Fatal(err)
	}
	if len(fps) != 2 {
		t.Fatalf("sealed to %d recipients after the file changed, want 2", len(fps))
	}
	got, err := Unseal([]age.Identity{b}, second)
	if err != nil || string(got) != "secret" {
		t.Fatalf("the added recipient could not open the payload: %q %v", got, err)
	}
}
