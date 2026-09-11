package settings

import (
	"strings"
	"testing"
)

// TestRedactStripsURLCredentials: `scheme://user:pass@host` is how a DSN, a
// proxy URL or a git remote carries a credential, and no seed and no
// `KEY=value` rule sees it — the whole thing is one opaque word in the middle
// of a line. The userinfo goes in full: the username is half the credential.
func TestRedactStripsURLCredentials(t *testing.T) {
	r := NewRedactor()
	cases := map[string]string{
		"connecting to postgresql://raguser:hunter2hunter2@localhost:5432/asm now": "raguser",
		"remote: https://gituser:ghp_abcdefghijklmnop@github.com/org/repo.git":     "ghp_abcdefghijklmnop",
		"proxy http://svc:s3cr3tpassword@127.0.0.1:3128 refused":                   "s3cr3tpassword",
	}
	for line, secret := range cases {
		out := r.Redact(line)
		if strings.Contains(out, secret) {
			t.Errorf("credential survived: %q", out)
		}
		if !strings.Contains(out, "://"+Redacted+"@") {
			t.Errorf("expected the userinfo to become ://%s@: %q", Redacted, out)
		}
	}
	// A URL with no credential is left alone.
	plain := "GET http://localhost:24041/collections"
	if got := r.Redact(plain); got != plain {
		t.Errorf("a credential-free URL was rewritten: %q", got)
	}
}

// TestRedactStripsEmbeddedAssignments: the anchored rule only ever saw
// `KEY=value` at the START of a line, so an `export` prefix, an argv element
// of a rollback descriptor and a JSON-embedded command string all carried
// their secret through untouched.
func TestRedactStripsEmbeddedAssignments(t *testing.T) {
	r := NewRedactor()
	for _, line := range []string{
		`export TENANT_PG_PASSWORD=hunter2hunter2`,
		`env API_KEY_USER=k-aaaaaaaaaaaaaaaaaaaa /usr/bin/python -m uvicorn`,
		`{"argv":["sh","-c","OPENAI_API_KEY=sk-livekeyvalue exec serve"]}`,
		`  running with JWT_SECRET=topsecretvalue in the environment`,
	} {
		out := r.Redact(line)
		for _, secret := range []string{"hunter2hunter2", "k-aaaaaaaaaaaaaaaaaaaa", "sk-livekeyvalue", "topsecretvalue"} {
			if strings.Contains(out, secret) {
				t.Errorf("%q: secret %q survived as %q", line, secret, out)
			}
		}
		if !strings.Contains(out, Redacted) {
			t.Errorf("%q: nothing was redacted: %q", line, out)
		}
	}
	// A non-secret assignment inside a line is NOT touched: the rule keys on
	// the classification table, not on the shape alone.
	keep := `started with LOG_LEVEL=info and TOP_K=12`
	if got := r.Redact(keep); got != keep {
		t.Errorf("public keys must survive: %q", got)
	}
	// An already-redacted value is not redacted again into nonsense.
	twice := r.Redact(r.Redact(`export TENANT_PG_PASSWORD=hunter2hunter2`))
	if twice != `export TENANT_PG_PASSWORD=`+Redacted {
		t.Errorf("not idempotent: %q", twice)
	}
}

// TestRedactStripsQuotedInlineAssignments is item 10 of the PR-A review.
//
// The inline (non-line-anchored) pattern matched only a BARE word after the
// `=`, so a value that began with a quote matched nothing at all and the
// secret stayed in the text. That is not an exotic shape: it is how every one
// of these files and command lines actually writes a value —
// `export API_KEYS='["…"]'`, `env PGPASSWORD="…" psql`, a rollback
// descriptor's argv element — and the anchored pass does not cover them,
// because the assignment is not at the start of the line.
func TestRedactStripsQuotedInlineAssignments(t *testing.T) {
	r := NewRedactor()
	cases := []struct{ in, secret string }{
		{`export TENANT_PG_PASSWORD='hunter2hunter2'`, "hunter2hunter2"},
		{`env NEO4J_AUTH="neo4j/ragstackpw" cypher-shell`, "ragstackpw"},
		{`run: export API_KEYS='["k-aaaaaaaaaaaaaaaa","k-bbbbbbbbbbbbbbbb"]' && start`, "k-aaaaaaaaaaaaaaaa"},
		{`{"argv":["sh","-c","TENANT_API_KEY_ADMIN='k-cccccccccccccccc' run"]}`, "k-cccccccccccccccc"},
	}
	for _, c := range cases {
		out := r.Redact(c.in)
		if strings.Contains(out, c.secret) {
			t.Errorf("%q: secret %q survived as %q", c.in, c.secret, out)
		}
		if !strings.Contains(out, Redacted) {
			t.Errorf("%q: nothing was redacted: %q", c.in, out)
		}
	}
	// The quoted run does not swallow what follows it: the rest of the line
	// is still there to read.
	got := r.Redact(`env TENANT_PG_PASSWORD='hunter2hunter2' psql -h db`)
	if !strings.Contains(got, "psql -h db") {
		t.Errorf("the redaction ate the rest of the line: %q", got)
	}
	// A public key keeps its quoted value.
	keep := `export LOG_LEVEL='info'`
	if out := r.Redact(keep); out != keep {
		t.Errorf("public keys must survive: %q", out)
	}
	// Idempotent, as for the bare-word form.
	twice := r.Redact(r.Redact(`export TENANT_PG_PASSWORD='hunter2hunter2'`))
	if strings.Count(twice, Redacted) != 1 {
		t.Errorf("not idempotent: %q", twice)
	}
}
