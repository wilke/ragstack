package settings

import (
	"regexp"
	"sort"
	"strings"
	"sync"
)

// minSeedLen is the shortest value the Redactor will strip. Shorter strings
// (a port number, "true", a tenant name) would shred ordinary text.
const minSeedLen = 8

var (
	// sigPattern is a BV-BRC token signature: `|sig=<hex>`; the hex is a
	// SHA-1/RSA signature and must never reach a log.
	sigPattern = regexp.MustCompile(`\|sig=[0-9a-f]{64,}`)
	// assignPattern matches `KEY=value` at a line start (optionally preceded
	// by whitespace, a diff marker or a comment marker, the shapes the
	// new-tenant.sh diff redactor had to cover). The value runs to end of line.
	assignPattern = regexp.MustCompile(`(?m)^([ \t]*[-+]?[ \t]*(?:#[ \t]*)?)([A-Z][A-Z0-9_]*)=(.*)$`)
	// inlineAssignPattern is assignPattern without the line anchor: the same
	// `KEY=value` shape wherever it appears INSIDE a line — `export KEY=v`,
	// one argv element of a rollback descriptor, a JSON-embedded
	// `"…API_KEY=v…"`, an `env KEY=v cmd` prefix. The value stops at the
	// first character that ends a shell word or a JSON string, so the
	// surrounding quote, comma or brace is never swallowed with it.
	inlineAssignPattern = regexp.MustCompile(`([A-Z][A-Z0-9_]*)=([^\s"',;}\]]+)`)
	// urlCredPattern is a credential carried in a URL authority
	// (`scheme://user:pass@host`): a DSN, a proxy URL, a git remote. The
	// whole userinfo goes — the username is half the credential.
	urlCredPattern = regexp.MustCompile(`://[^/:@\s]+:[^@\s]+@`)
)

// Redactor strips known secret VALUES (seeded from every current and
// historical secret file) and secret-shaped text (token signatures,
// `SECRET_KEY=value` lines) from free text: API responses, job logs, rendered
// units, nginx config, bundle manifests.
//
// Safe for concurrent use.
type Redactor struct {
	mu    sync.RWMutex
	seeds []string // sorted longest-first so overlapping values strip fully
}

// NewRedactor returns a redactor seeded with values. Values shorter than
// minSeedLen are ignored.
func NewRedactor(values ...string) *Redactor {
	r := &Redactor{}
	r.Add(values...)
	return r
}

// Add seeds more values (e.g. after reading a *.bak-* secrets file).
func (r *Redactor) Add(values ...string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	seen := make(map[string]bool, len(r.seeds)+len(values))
	for _, s := range r.seeds {
		seen[s] = true
	}
	for _, v := range values {
		v = strings.TrimSpace(v)
		if len(v) < minSeedLen || seen[v] {
			continue
		}
		seen[v] = true
		r.seeds = append(r.seeds, v)
	}
	sort.SliceStable(r.seeds, func(i, j int) bool { return len(r.seeds[i]) > len(r.seeds[j]) })
}

// Seeds reports how many values are loaded (for doctor/tests; never the values).
func (r *Redactor) Seeds() int {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return len(r.seeds)
}

// Redact returns text with every seeded value, every `|sig=…` signature,
// every URL-carried credential and the value of every secret-class
// `KEY=value` — at a line start or embedded in one — replaced by
// "<REDACTED>".
func (r *Redactor) Redact(text string) string {
	r.mu.RLock()
	seeds := r.seeds
	r.mu.RUnlock()
	for _, s := range seeds {
		text = strings.ReplaceAll(text, s, Redacted)
	}
	text = sigPattern.ReplaceAllString(text, "|sig="+Redacted)
	text = urlCredPattern.ReplaceAllString(text, "://"+Redacted+"@")
	text = assignPattern.ReplaceAllStringFunc(text, func(line string) string {
		m := assignPattern.FindStringSubmatch(line)
		if m == nil || !IsSecret(m[2]) || m[3] == "" || m[3] == Redacted {
			return line
		}
		return m[1] + m[2] + "=" + Redacted
	})
	// The anchored pass has already flattened whole lines; this one catches
	// the same assignment wherever a line only CONTAINS it.
	text = inlineAssignPattern.ReplaceAllStringFunc(text, func(m string) string {
		g := inlineAssignPattern.FindStringSubmatch(m)
		if g == nil || !IsSecret(g[1]) || g[2] == "" || g[2] == Redacted {
			return m
		}
		return g[1] + "=" + Redacted
	})
	return text
}

// RedactText is Redact with no seeds: the pattern passes only.
func RedactText(text string) string { return NewRedactor().Redact(text) }
