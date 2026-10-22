package settings

import (
	"os"
	"regexp"
	"sort"
	"strings"
	"testing"
)

// configPy is python/ragstack/config.py, from this package's directory.
const configPy = "../../../../python/ragstack/config.py"

// notOperatorSettable are Settings fields the ctl deliberately does NOT
// classify, each with the reason. Classify returns Unsupported for them, so
// `env set` refuses them and adopt records them as drift. A field belongs here
// only when an operator should never need to set it per tenant; anything an
// operator might reasonably flip goes in a live table instead.
//
// Keep this list SHORT and every entry commented: it is the reviewed record of
// "we decided the ctl does not own this", not a place to park a key nobody
// classified (#775 — the second time a new Python setting was invisible to the
// ctl's env surface).
//
// Empty as of #775: every Settings field is either classified or retired.
// Format: "KEY": "why an operator never sets this per tenant",
var notOperatorSettable = map[string]string{}

// settingsFields parses the field names of python/ragstack/config.py's
// Settings class: every `    name: …` annotation at class-body indent between
// `class Settings(` and the module-level `settings = Settings()`. Private
// attributes (a leading underscore) are not env-backed and do not match.
func settingsFields(t *testing.T) []string {
	t.Helper()
	b, err := os.ReadFile(configPy)
	if err != nil {
		// Not a skip: a guard that silently vanishes when the path moves is
		// the failure mode this test exists to prevent.
		t.Fatalf("read %s: %v", configPy, err)
	}
	src := string(b)
	start := strings.Index(src, "class Settings(")
	end := strings.Index(src, "\nsettings = Settings()")
	if start < 0 || end < start {
		t.Fatalf("%s: cannot find the Settings class body", configPy)
	}
	field := regexp.MustCompile(`(?m)^    ([a-z][a-z0-9_]*)\s*:`)
	seen := map[string]bool{}
	var out []string
	for _, m := range field.FindAllStringSubmatch(src[start:end], -1) {
		k := strings.ToUpper(m[1])
		if !seen[k] {
			seen[k] = true
			out = append(out, k)
		}
	}
	sort.Strings(out)
	if len(out) < 50 {
		t.Fatalf("%s: parsed only %d Settings fields — the parser no longer matches the file", configPy, len(out))
	}
	return out
}

// TestEverySettingsFieldIsClassified is #775's guard: every field of
// config.py's Settings is either in a live table (public, secret, executable
// surface), retired (a field the API still declares only to refuse its boot,
// e.g. GOWE_TOOL_IMAGE) or on the reviewed
// notOperatorSettable list. A new Python setting with neither fails here, in
// CI, instead of on an operator's `env set` weeks later.
func TestEverySettingsFieldIsClassified(t *testing.T) {
	fields := settingsFields(t)
	inConfig := map[string]bool{}
	for _, k := range fields {
		inConfig[k] = true
		class := Classify(k)
		_, allowed := notOperatorSettable[k]
		if _, retired := Retired(k); retired {
			class = Unsupported
			allowed = true // classified: refused with its reason
		}
		switch {
		case class == Unsupported && !allowed:
			t.Errorf("%s is a config.py Settings field the ctl does not classify: add it to the public, "+
				"executable-surface or secret table in settings.go, or (with a reason) to notOperatorSettable", k)
		case class != Unsupported && notOperatorSettable[k] != "":
			t.Errorf("%s is on notOperatorSettable but classifies as %s — drop it from the list", k, class)
		}
	}
	for k, why := range notOperatorSettable {
		if strings.TrimSpace(why) == "" {
			t.Errorf("notOperatorSettable lists %s without a reason", k)
		}
		if !inConfig[k] {
			t.Errorf("notOperatorSettable lists %s, which is not a Settings field any more — drop it", k)
		}
	}
	// The public table names only real settings (a typo there is a key the
	// env API accepts and the API never reads).
	for _, k := range PublicKeys() {
		if !inConfig[k] {
			t.Errorf("public table entry %s is not a config.py Settings field", k)
		}
	}
}
