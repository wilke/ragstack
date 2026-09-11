package envfile

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/settings"
)

// keysOnlySeeds are the env keys whose JSON value is a map KEYED BY the
// secret: `API_KEY_TENANTS` maps an API key to a tenant name and
// `API_KEY_ROLES` maps it to a role. Only the map's KEYS are secret. Seeding
// the values too would put every tenant name into the redactor, and a tenant
// whose name is at least minSeedLen long would then be struck out of its own
// logs (the `lucid-next` case).
var keysOnlySeeds = map[string]bool{
	"API_KEY_TENANTS": true,
	"API_KEY_ROLES":   true,
}

// historyGlobs are the leftovers a hand-run deployment accumulates beside its
// live env files — the same set adopt already lists as unmanaged files. A key
// rotated OUT of secrets.env is still printed by a process that was started
// before the rotation, so the redactor has to know the old values too.
var historyGlobs = []string{"*.bak*", "*.orig", "*.recovered"}

// tolerantAssign is the last-resort `KEY=value` scanner: it reads what the
// strict grammar refuses (an `export` prefix, a line continuation, a `$`),
// because a file the parser rejects still contains the secrets that must not
// reach a log.
var tolerantAssign = regexp.MustCompile(`(?m)^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*=[ \t]*(.*)$`)

// SeedRedactor seeds r from every secret value in a tenant's config
// directory: secrets.env (all values), tenant.env and provision.env
// (secret-class keys only), and every historical copy of those beside them
// (*.bak, *.bak-<stamp>, *.orig, *.recovered).
//
// It FAILS CLOSED on the LIVE files: one that exists but cannot be read, or
// that yields no assignment at all although it has content, is an error
// rather than a silent zero-seed redactor — a caller that stamps
// `redacted: true` on its answer has to be able to tell the difference.
//
// Historical copies are best-effort by design. They are whatever somebody
// left behind (a diff, a truncated copy, a note), and a file with no
// `KEY=value` line in it holds no env secret to seed from in the first
// place, so it is skipped rather than made fatal.
func SeedRedactor(configDir string, r *settings.Redactor) error {
	if r == nil {
		return errors.New("envfile: nil redactor")
	}
	live, history := seedFiles(configDir)
	for _, path := range live {
		b, err := os.ReadFile(path)
		if err != nil {
			if errors.Is(err, fs.ErrNotExist) {
				continue
			}
			return fmt.Errorf("seed redactor: %s: %w", path, err)
		}
		if err := seedFrom(path, b, r, true); err != nil {
			return err
		}
	}
	for _, path := range history {
		b, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		_ = seedFrom(path, b, r, false)
	}
	return nil
}

// seedFiles returns the live env files (stable order) and every historical
// copy in the directory (sorted).
func seedFiles(configDir string) (live, history []string) {
	live = []string{
		filepath.Join(configDir, "secrets.env"),
		filepath.Join(configDir, "tenant.env"),
		filepath.Join(configDir, "provision.env"),
	}
	seen := map[string]bool{}
	for _, p := range live {
		seen[p] = true
	}
	for _, g := range historyGlobs {
		matches, err := filepath.Glob(filepath.Join(configDir, g))
		if err != nil {
			continue
		}
		for _, m := range matches {
			if seen[m] {
				continue
			}
			seen[m] = true
			history = append(history, m)
		}
	}
	sort.Strings(history)
	return live, history
}

// seedFrom mines one file. The live secrets.env and every backup of it are
// all-secret; anywhere else only secret-class keys are.
func seedFrom(path string, b []byte, r *settings.Redactor, strict bool) error {
	everything := strings.HasPrefix(filepath.Base(path), "secrets.env")
	add := func(key, value string) {
		if value == "" {
			return
		}
		if !everything && !settings.IsSecret(key) {
			return
		}
		r.Add(value)
		r.Add(jsonSeeds(key, value)...)
	}
	f, _, err := ParseLenient(b)
	if err == nil {
		for _, k := range f.Keys() {
			v, _ := f.Get(k)
			add(k, v)
		}
		return nil
	}
	// The strict grammar refused the file. Fall back to the tolerant scan,
	// and only give up when that finds nothing in a file that has content.
	matches := tolerantAssign.FindAllStringSubmatch(string(b), -1)
	for _, m := range matches {
		add(m[1], strings.Trim(strings.TrimSpace(m[2]), `"'`))
	}
	if strict && len(matches) == 0 && hasContent(b) {
		return fmt.Errorf("seed redactor: %s: %w", path, err)
	}
	return nil
}

// hasContent reports whether b holds anything but blanks and comments.
func hasContent(b []byte) bool {
	for _, line := range strings.Split(string(b), "\n") {
		t := strings.TrimSpace(line)
		if t != "" && t[0] != '#' && t[0] != ';' {
			return true
		}
	}
	return false
}

// jsonSeeds pulls the individual secrets out of a JSON triple value so each
// key is seeded on its own and not only as part of the whole blob. API_KEYS
// is an array of keys; API_KEY_TENANTS / API_KEY_ROLES are objects keyed BY
// the key, so only their keys are seeded (see keysOnlySeeds).
func jsonSeeds(key, v string) []string {
	v = strings.TrimSpace(v)
	if len(v) < 2 || (v[0] != '[' && v[0] != '{') {
		return nil
	}
	var arr []string
	if err := json.Unmarshal([]byte(v), &arr); err == nil {
		return arr
	}
	var obj map[string]json.RawMessage
	if err := json.Unmarshal([]byte(v), &obj); err != nil {
		return nil
	}
	var out []string
	for k, raw := range obj {
		out = append(out, k)
		if keysOnlySeeds[key] {
			continue
		}
		var s string
		if err := json.Unmarshal(raw, &s); err == nil {
			out = append(out, s)
		}
	}
	sort.Strings(out)
	return out
}
