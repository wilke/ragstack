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
// package's own tests, not for the daemon.
//
// What the job engine is given is a RecipientsSealer (LoadRecipientsSealer),
// for ONE purpose: the backup's sealed secrets payload. The engine's
// envelopes of minted credentials are a different thing and stay in daemon
// memory for their TTL; nothing here writes those to disk.
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
// It is NOT what the job engine is given — that is RecipientsSealer, which
// loads the list once so that a plan and the step that runs it seal to the
// same set. See the package comment.
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

// RecipientsSealer is the backup's sealer as the job engine holds it: the
// recipients file read ONCE, when the engine is built, and every Seal after
// that encrypting to exactly that set.
//
// Once rather than per call (EnvelopeSealer's choice) because the engine
// PLANS against it: a plan says "age-encrypted to <fingerprints>" and
// `secrets=require` refuses or proceeds on whether there are any, and both
// answers have to come from the process's dependencies rather than from a
// file read at plan time. A step that re-read the file could seal to a set the
// plan never named. The cost is stated: a recipient added to the file reaches
// the daemon at its next restart (a --direct run builds a fresh engine, so it
// sees the file as it is).
//
// It satisfies ops.Sealer structurally; this package does not import ops.
type RecipientsSealer struct {
	recipients   []age.Recipient
	fingerprints []string
}

// LoadRecipientsSealer parses the recipients file at path. It answers an
// error wrapping ErrNoRecipients when the file is absent or names nobody, and
// any other error for a file that exists and does not parse — which the
// caller must not mistake for "no recipients configured".
func LoadRecipientsSealer(path string) (*RecipientsSealer, error) {
	recipients, fps, err := LoadRecipients(path)
	if err != nil {
		return nil, err
	}
	return &RecipientsSealer{recipients: recipients, fingerprints: fps}, nil
}

// Seal encrypts plaintext to the recipients loaded at construction.
func (s *RecipientsSealer) Seal(plaintext []byte) ([]byte, error) {
	if s == nil {
		return nil, ErrNoRecipients
	}
	return Seal(s.recipients, plaintext)
}

// Fingerprints are the `sha256:<16 hex>` identifiers of the recipient lines,
// in file order — public, and what a plan and a manifest name.
func (s *RecipientsSealer) Fingerprints() []string {
	if s == nil {
		return nil
	}
	return append([]string(nil), s.fingerprints...)
}
