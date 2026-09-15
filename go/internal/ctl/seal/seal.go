// Package seal is the ctl's age envelope: the one way a tenant's secrets ever
// leave the process that minted them.
//
// It exists for `backup`. A tenant's bundle has to carry secrets.env — without
// it the bundle restores a tenant nobody can authenticate to — and a bundle
// sits in a directory on a shared host, so the secrets go in encrypted to a
// recipient list an operator configured beforehand, or they do not go in at
// all. There is no third option and in particular no passphrase prompt: the
// daemon has no terminal.
//
// The asymmetry is the point and it is deliberate. The ctl SEALS with public
// recipients it reads out of a file; it holds no identity, so it cannot unseal
// what it wrote. Unseal is here for the operator-side tooling and for this
// package's own tests, not for the daemon. That is also why
// NewEnvelopeSealer is not wired into the job engine: envelopes of minted
// credentials stay in daemon memory for the length of a job, and giving the
// daemon a way to write them to disk would turn a value that currently cannot
// outlive a process into one that can.
package seal

import (
	"bufio"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"strings"

	"filippo.io/age"
	"filippo.io/age/agessh"
)

// ErrNoRecipients is the absence of a recipients file, or a file that names
// none.
//
// It is a distinct error because the backup TURNS it into a fact it records
// rather than a failure: no recipients means the bundle is written without
// secrets, `secrets.included: false` in the manifest and a warning in the
// plan. Any other error from this package is a real failure — a malformed
// recipient line is an operator's typo, and silently writing a bundle without
// secrets because of one would be the worst possible reading of it.
var ErrNoRecipients = errors.New("no backup recipients are configured")

// maxRecipientsBytes caps the recipients file. It holds a handful of public
// keys; anything larger is not that file.
const maxRecipientsBytes = 64 << 10

// LoadRecipients parses an age recipients file.
//
// The format is one recipient per line: an X25519 public key (`age1…`) or an
// SSH public key (`ssh-ed25519 …` / `ssh-rsa …`), with `#` comments and blank
// lines ignored. The fingerprints come back alongside, in the same order, as
// `sha256:<first 16 hex>` of the LINE — they are what the manifest records so
// that a bundle says who can open it without carrying the keys themselves.
func LoadRecipients(path string) ([]age.Recipient, []string, error) {
	f, err := os.Open(path)
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return nil, nil, fmt.Errorf("%w: %s does not exist", ErrNoRecipients, path)
		}
		return nil, nil, err
	}
	defer f.Close()

	var (
		recipients   []age.Recipient
		fingerprints []string
	)
	sc := bufio.NewScanner(io.LimitReader(f, maxRecipientsBytes))
	line := 0
	for sc.Scan() {
		line++
		text := strings.TrimSpace(sc.Text())
		if text == "" || strings.HasPrefix(text, "#") {
			continue
		}
		r, err := parseRecipient(text)
		if err != nil {
			// The line itself is NOT quoted back. These are public keys, so
			// there is no secret in one — but the file is edited by hand next
			// to a file of private ones, and an error message is the cheapest
			// way for a paste into the wrong file to end up in a log.
			return nil, nil, fmt.Errorf("%s line %d is not an age or SSH public key: %v", path, line, err)
		}
		recipients = append(recipients, r)
		fingerprints = append(fingerprints, fingerprint(text))
	}
	if err := sc.Err(); err != nil {
		return nil, nil, err
	}
	if len(recipients) == 0 {
		return nil, nil, fmt.Errorf("%w: %s names none", ErrNoRecipients, path)
	}
	return recipients, fingerprints, nil
}

// parseRecipient accepts the two key kinds an operator on this host has.
func parseRecipient(text string) (age.Recipient, error) {
	if strings.HasPrefix(text, "age1") {
		return age.ParseX25519Recipient(text)
	}
	if strings.HasPrefix(text, "ssh-") {
		// SSH keys, because the operators of this host already have one in
		// authorized_keys and asking them to mint a second key type for
		// backups is how a recipients file ends up empty.
		return agessh.ParseRecipient(text)
	}
	return nil, fmt.Errorf("a recipient is an age1… or ssh-… public key")
}

// fingerprint is sha256 of the recipient line, truncated to 16 hex characters.
// Truncated because it identifies a key in a manifest an operator reads, not
// because anything trusts it: the recipient list itself is the authority.
func fingerprint(line string) string {
	sum := sha256.Sum256([]byte(line))
	return "sha256:" + hex.EncodeToString(sum[:])[:16]
}

// Seal encrypts plaintext to every recipient, as binary age (not armored: the
// output is written to a file in a bundle, never pasted into one).
func Seal(recipients []age.Recipient, plaintext []byte) ([]byte, error) {
	if len(recipients) == 0 {
		return nil, ErrNoRecipients
	}
	var buf bytes.Buffer
	w, err := age.Encrypt(&buf, recipients...)
	if err != nil {
		return nil, err
	}
	if _, err := w.Write(plaintext); err != nil {
		return nil, err
	}
	// Close, not a deferred one whose error is dropped: age writes its final
	// authentication tag here, and a payload missing it is a file that looks
	// sealed and cannot be opened.
	if err := w.Close(); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

// Unseal decrypts with the first identity that matches. It is the
// OPERATOR-side half — the daemon holds no identity — and exists so that the
// tooling which opens a bundle, and this package's tests, use the same code
// path the sealing side does.
func Unseal(identities []age.Identity, sealed []byte) ([]byte, error) {
	if len(identities) == 0 {
		return nil, errors.New("no identity was supplied to open the envelope")
	}
	r, err := age.Decrypt(bytes.NewReader(sealed), identities...)
	if err != nil {
		return nil, err
	}
	return io.ReadAll(r)
}

// EnvelopeSealer seals payloads to a recipients file that is read at SEAL
// time rather than at construction.
//
// Re-reading is deliberate: an operator who adds a recipient — a colleague, a
// replacement key after a laptop is lost — expects the next backup to use it,
// and a sealer that cached the list at daemon start would keep sealing to the
// old set until somebody restarted the service.
//
// It is NOT wired into the job engine. See the package comment: the engine's
// envelopes of minted credentials stay in memory, and this type is here for
// the backup's secrets payload, which is a different thing that happens to use
// the same primitive.
type EnvelopeSealer struct{ RecipientsPath string }

// NewEnvelopeSealer returns a sealer for the recipients file at path.
func NewEnvelopeSealer(recipientsPath string) *EnvelopeSealer {
	return &EnvelopeSealer{RecipientsPath: recipientsPath}
}

// Seal encrypts plaintext and returns the payload with the fingerprints of the
// recipients it was sealed to. ErrNoRecipients is the caller's signal to write
// the bundle without secrets.
func (s *EnvelopeSealer) Seal(plaintext []byte) (payload []byte, fingerprints []string, err error) {
	recipients, fps, err := LoadRecipients(s.RecipientsPath)
	if err != nil {
		return nil, nil, err
	}
	payload, err = Seal(recipients, plaintext)
	if err != nil {
		return nil, nil, err
	}
	return payload, fps, nil
}
