package gateway

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"errors"
	"fmt"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// substituteUnreadableTLS repairs the one thing about a staged proxy tree that
// the operator cannot fix from their own account.
//
// `nginx -t` does not only parse: it OPENS every certificate and key the
// config names. The live key is mode 0600 and owned by the account that runs
// the proxy, so for anyone else the staged test fails with
//
//	cannot load certificate key ".../tls/proxy.key": … Permission denied
//
// — a failure about file ownership, not about the change under test, and one
// that would make `gateway apply --dry-run` useless to everybody except the
// proxy's owner. Where the key cannot be read, the staged copy (a temp
// directory, thrown away at the end of the run) gets a freshly generated
// throwaway self-signed pair under the same names, and the substitution is
// reported as a warning so nobody mistakes the result for a test of the real
// certificate. Everything else in the configuration is tested exactly as it
// stands.
//
// The generated key never leaves the staging dir and is never used to serve.
//
// It substitutes in ONE case: the key cannot be opened because of its
// permissions, and the certificate beside it CAN be. Two cases are deliberately
// left alone:
//
//   - both readable — the real pair is staged and `nginx -t` tests it
//     completely, pairing included. There is nothing to fix.
//   - the CERTIFICATE is what cannot be read. Replacing the pair here would
//     hide a cert/key mismatch behind a pair this package generated and made
//     consistent by construction, and it would do so for a file whose
//     unreadability is a real finding. nginx -t says so instead, and the
//     warning names it.
//
// What is NOT tested when the substitution happens is stated in the warning:
// the real certificate is not loaded, so its expiry, its chain and its pairing
// with the real key go unchecked by this run. (A throwaway key beside the real
// certificate cannot be staged instead: nginx refuses a certificate and key
// that do not match, so the test would fail on the substitution itself.)
func substituteUnreadableTLS(stagedTLSDir string) ([]string, error) {
	ents, err := os.ReadDir(stagedTLSDir)
	if err != nil {
		return nil, nil // no tls/ in this tree: nothing to substitute
	}
	var warns []string
	for _, e := range ents {
		name := e.Name()
		if !strings.HasSuffix(name, ".key") {
			continue
		}
		base := strings.TrimSuffix(name, ".key")
		keyPath := filepath.Join(stagedTLSDir, name)
		crtPath := filepath.Join(stagedTLSDir, base+".crt")
		keyErr, crtErr := openErr(keyPath), openErr(crtPath)
		if keyErr == nil && crtErr == nil {
			continue
		}
		if crtErr != nil {
			warns = append(warns, fmt.Sprintf(
				"tls/%s.crt cannot be opened by this account (%v); it was NOT substituted, because replacing a "+
					"certificate here would mask a cert/key mismatch. nginx -t will report it", base, crtErr))
			continue
		}
		if !errors.Is(keyErr, os.ErrPermission) {
			warns = append(warns, fmt.Sprintf(
				"tls/%s.key cannot be opened (%v) and that is not a permission problem, so it was NOT substituted; "+
					"nginx -t will report it", base, keyErr))
			continue
		}
		if err := writeThrowawayPair(crtPath, keyPath); err != nil {
			return warns, fmt.Errorf("generating a substitute certificate for the staged %s: %w", base, err)
		}
		warns = append(warns, fmt.Sprintf(
			"tls/%s.key is not readable by this account, so the STAGED copy uses a throwaway self-signed pair and "+
				"nginx -t tests the configuration rather than the file permissions. NOT TESTED by this run: the real "+
				"certificate itself and its pairing with the real key. The real files are untouched", base))
	}
	return warns, nil
}

// openErr reports why a file cannot be opened, or nil when it can. It is the
// only honest readability test: stat + a mode comparison gets ACLs, group
// membership and mount options wrong.
func openErr(p string) error {
	f, err := os.Open(p)
	if err != nil {
		return err
	}
	_ = f.Close()
	return nil
}

func readable(p string) bool { return openErr(p) == nil }

// writeThrowawayPair writes a self-signed P-256 certificate and its key, valid
// for a day, for localhost only.
func writeThrowawayPair(crtPath, keyPath string) error {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return err
	}
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		return err
	}
	tmpl := x509.Certificate{
		SerialNumber:          serial,
		Subject:               pkix.Name{CommonName: "ragstack-ctl staged config test"},
		NotBefore:             now().Add(-time.Hour),
		NotAfter:              now().Add(24 * time.Hour),
		KeyUsage:              x509.KeyUsageDigitalSignature | x509.KeyUsageCertSign,
		ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		BasicConstraintsValid: true,
		IsCA:                  true,
		DNSNames:              []string{"localhost"},
		IPAddresses:           []net.IP{net.ParseIP("127.0.0.1")},
	}
	der, err := x509.CreateCertificate(rand.Reader, &tmpl, &tmpl, &key.PublicKey, key)
	if err != nil {
		return err
	}
	keyDER, err := x509.MarshalECPrivateKey(key)
	if err != nil {
		return err
	}
	// Remove first: what is there may be a SYMLINK to the real key, and
	// writing through it would overwrite the live certificate.
	for _, p := range []string{crtPath, keyPath} {
		if err := os.Remove(p); err != nil && !os.IsNotExist(err) {
			return err
		}
	}
	if err := os.WriteFile(crtPath, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}), 0o644); err != nil {
		return err
	}
	return os.WriteFile(keyPath, pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: keyDER}), 0o600)
}
