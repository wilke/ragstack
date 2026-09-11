package settings

import (
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// TestTestdataHasNoSecretShapes re-runs capture.sh's canary sweep over the
// committed goldens on every test run: a 64-hex string (API key, sha256 of
// a key, token signature), a BV-BRC `sig=`, a DSN with credentials, or a
// secret-class assignment whose value is not a placeholder must never land
// in the repository.
func TestTestdataHasNoSecretShapes(t *testing.T) {
	root := "../testdata"
	if _, err := os.Stat(root); err != nil {
		t.Skip("no testdata tree")
	}
	canaries := map[string]*regexp.Regexp{
		"64-hex":      regexp.MustCompile(`[0-9a-fA-F]{64}`),
		"sig=":        regexp.MustCompile(`sig=`),
		"dsn-with-pw": regexp.MustCompile(`[a-z+]+://[^:/@\s]+:[^@\s]+@`),
		"jwt":         regexp.MustCompile(`eyJ[A-Za-z0-9_-]{10,}\.eyJ`),
		"private-key": regexp.MustCompile(`-----BEGIN [A-Z ]*PRIVATE KEY-----`),
		"long-base64": regexp.MustCompile(`[A-Za-z0-9+/]{48,}={0,2}`),
	}
	assign := regexp.MustCompile(`(?m)^([A-Z][A-Z0-9_]*)=(.*)$`)
	n := 0
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() || d.Name() == "capture.sh" { // the script spells the patterns it hunts
			return nil
		}
		n++
		b, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		text := string(b)
		for name, re := range canaries {
			if loc := re.FindStringIndex(text); loc != nil {
				t.Errorf("%s: canary %s at byte %d (context redacted)", path, name, loc[0])
			}
		}
		if strings.HasSuffix(path, ".env") {
			for _, m := range assign.FindAllStringSubmatch(text, -1) {
				k, v := m[1], strings.TrimSpace(m[2])
				if !IsSecret(k) || v == "" {
					continue
				}
				if !strings.Contains(v, "<REDACTED") && !strings.Contains(v, "<GENERATED:") {
					t.Errorf("%s: secret-class %s carries a non-placeholder value", path, k)
				}
			}
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if n == 0 {
		t.Fatal("no files under testdata")
	}
}
