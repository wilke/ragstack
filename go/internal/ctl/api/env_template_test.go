package api

import (
	"os"
	"regexp"
	"sort"
	"strings"
	"testing"
)

// TestEnvKeysMatchAnsibleTemplates reconciles EnvKeys()/SecretEnvKeys() — the
// contract this package documents in env.go — against the rendered key sets
// of ops/ansible/roles/ragstack-ctl's two env templates. A key that exists in
// a template but not in env.go is a setting that silently does nothing; a key
// in env.go but missing from a template is a setting nobody on the host can
// configure. See env.go's package comment for the full rationale.
func TestEnvKeysMatchAnsibleTemplates(t *testing.T) {
	const (
		publicTemplate = "../../../../ops/ansible/roles/ragstack-ctl/templates/ctl.env.j2"
		secretTemplate = "../../../../ops/ansible/roles/ragstack-ctl/templates/ctl-secrets.env.j2"
	)

	publicKeys, ok := templateEnvKeys(t, publicTemplate)
	if !ok {
		t.Skipf("%s not found; skipping template reconciliation", publicTemplate)
	}
	secretKeys, ok := templateEnvKeys(t, secretTemplate)
	if !ok {
		t.Skipf("%s not found; skipping template reconciliation", secretTemplate)
	}

	secret := map[string]bool{}
	for _, k := range SecretEnvKeys() {
		secret[k] = true
	}

	wantPublic := []string{}
	for _, k := range EnvKeys() {
		if secret[k] || k == EnvIdentityKeyFetchFile {
			continue
		}
		wantPublic = append(wantPublic, k)
	}
	wantSecret := append([]string(nil), SecretEnvKeys()...)

	assertSameKeySet(t, "ctl.env.j2", wantPublic, publicKeys)
	assertSameKeySet(t, "ctl-secrets.env.j2", wantSecret, secretKeys)

	// CTL_IDENTITY_KEY_FETCH_FILE is test-only and must never be templated.
	if publicKeys[EnvIdentityKeyFetchFile] || secretKeys[EnvIdentityKeyFetchFile] {
		t.Errorf("%s must never appear in an Ansible template (test-only override)", EnvIdentityKeyFetchFile)
	}
}

var ctlKeyRE = regexp.MustCompile(`^(CTL_[A-Z0-9_]+)=`)

// templateEnvKeys extracts every `^CTL_[A-Z0-9_]+=` key from a Jinja env
// template. Returns ok=false if the file does not exist (the caller skips
// rather than fails, since this repo checkout may not carry ops/ansible).
func templateEnvKeys(t *testing.T, path string) (map[string]bool, bool) {
	t.Helper()
	data, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return nil, false
	}
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	keys := map[string]bool{}
	for _, line := range strings.Split(string(data), "\n") {
		if m := ctlKeyRE.FindStringSubmatch(line); m != nil {
			keys[m[1]] = true
		}
	}
	return keys, true
}

// assertSameKeySet compares a wanted key list against the keys found in a
// rendered template, reporting both missing and unexpected keys sorted for a
// stable diff.
func assertSameKeySet(t *testing.T, label string, want []string, got map[string]bool) {
	t.Helper()
	wantSet := map[string]bool{}
	for _, k := range want {
		wantSet[k] = true
	}

	var missing, extra []string
	for k := range wantSet {
		if !got[k] {
			missing = append(missing, k)
		}
	}
	for k := range got {
		if !wantSet[k] {
			extra = append(extra, k)
		}
	}
	sort.Strings(missing)
	sort.Strings(extra)

	if len(missing) > 0 {
		t.Errorf("%s: missing keys env.go declares but the template does not emit: %v", label, missing)
	}
	if len(extra) > 0 {
		t.Errorf("%s: extra keys the template emits that env.go does not declare: %v", label, extra)
	}
}
