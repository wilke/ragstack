package envfile

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/ctl/settings"
)

const golden = "../testdata/live-2026-09-10/tenants"

func readGolden(t *testing.T, rel string) []byte {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(golden, rel))
	if err != nil {
		t.Fatalf("golden %s: %v (run testdata/capture.sh)", rel, err)
	}
	return b
}

// The plan's list of dev's inline-comment keys, verified against the live
// file on 2026-09-10 (RATE_LIMIT_* expands to the two keys present).
var devInlineKeys = strings.Fields("INGEST_BACKEND GOWE_URL GOWE_WORKFLOW_CWL GOWE_WORKER_GROUP WORKSPACE_URL GRAPH_BACKEND NEO4J_URI NEO4J_USER MAX_COLLECTIONS_PER_OWNER MAX_CHUNKS_PER_COLLECTION ALLOW_USER_COLLECTION_CREATE RATE_LIMIT_COLLECTIONS_CREATE_PER_HOUR RATE_LIMIT_INGEST_PER_HOUR")

func TestLiveFixturesRoundTrip(t *testing.T) {
	for _, rel := range []string{
		"lucid/config/tenant.env", "asm/config/tenant.env", "dev/config/tenant.env", "demo/config/tenant.env",
		"dev/config/secrets.env", "demo/config/secrets.env", "dev/config/provision.env", "demo/config/provision.env",
	} {
		b := readGolden(t, rel)
		f, probs, err := ParseLenient(b)
		if err != nil {
			t.Errorf("%s: %v", rel, err)
			continue
		}
		if got := f.Render(); !bytes.Equal(got, b) {
			t.Errorf("%s: Render differs from source\n got %q\nwant %q", rel, got, b)
		}
		if rel != "dev/config/tenant.env" && len(probs) > 0 {
			t.Errorf("%s: unexpected problems %v", rel, probs)
		}
		if rel != "dev/config/tenant.env" {
			if _, err := Parse(b); err != nil {
				t.Errorf("%s: strict Parse: %v", rel, err)
			}
		}
	}
}

func TestDevInlineCommentsNormalize(t *testing.T) {
	b := readGolden(t, "dev/config/tenant.env")

	if _, err := Parse(b); err == nil {
		t.Fatal("strict Parse accepted inline comments")
	} else {
		var ps Problems
		if !errors.As(err, &ps) || len(ps) != 13 || ps[0].Class != ClassInlineComment {
			t.Fatalf("strict Parse error = %v", err)
		}
	}

	f, probs, err := ParseLenient(b)
	if err != nil {
		t.Fatal(err)
	}
	if got := strings.Join(probs.Keys(), " "); got != strings.Join(devInlineKeys, " ") {
		t.Fatalf("inline-comment keys:\n got %s\nwant %s", got, strings.Join(devInlineKeys, " "))
	}
	for _, p := range probs {
		if p.Class != ClassInlineComment {
			t.Errorf("%v: class %s", p, p.Class)
		}
	}
	if v := f.Validate(); len(v) != 13 {
		t.Fatalf("Validate before normalize = %d problems: %v", len(v), v)
	}
	// Values exclude the comment.
	if v, _ := f.Get("GRAPH_BACKEND"); strings.Contains(v, "#") || strings.TrimSpace(v) != v {
		t.Errorf("GRAPH_BACKEND value = %q", v)
	}

	moved := f.Normalize()
	if strings.Join(moved, " ") != strings.Join(devInlineKeys, " ") {
		t.Fatalf("Normalize moved %v", moved)
	}
	if v := f.Validate(); len(v) != 0 {
		t.Fatalf("Validate after normalize: %v", v)
	}
	out := f.Render()
	if _, err := Parse(out); err != nil {
		t.Fatalf("normalized output does not parse strictly: %v", err)
	}
	// The comment precedes the assignment and the value is intact.
	lines := strings.Split(string(out), "\n")
	for i, l := range lines {
		if strings.HasPrefix(l, "GRAPH_BACKEND=") && i > 0 {
			if !strings.HasPrefix(lines[i-1], "# ") {
				t.Errorf("comment not moved before GRAPH_BACKEND: %q", lines[i-1])
			}
		}
	}
	if len(f.Normalize()) != 0 {
		t.Error("Normalize not idempotent")
	}
	// Keys and values survive the round trip.
	g, _, _ := ParseLenient(b)
	for _, k := range g.Keys() {
		a, _ := g.Get(k)
		c, _ := f.Get(k)
		if a != c {
			t.Errorf("%s: %q != %q", k, a, c)
		}
	}
	if len(f.Keys()) != len(g.Keys()) {
		t.Errorf("key count changed: %d vs %d", len(f.Keys()), len(g.Keys()))
	}
}

func TestGrammar(t *testing.T) {
	ok := "# c\n; also c\n\nA=1\nB='x y # z'\nC=\"q \\\"n\\\" \\\\ \\n\"\nD=\nE=http://localhost:1/x?a=b&c=d\n"
	f, err := Parse([]byte(ok))
	if err != nil {
		t.Fatal(err)
	}
	// `\n` inside double quotes is TWO literal characters for both sh and
	// systemd's EnvironmentFile=; decoding it to a real newline produced a
	// value neither reader would ever hand the API.
	want := map[string]string{"A": "1", "B": "x y # z", "C": `q "n" \ \n`, "D": "", "E": "http://localhost:1/x?a=b&c=d"}
	for k, w := range want {
		if v, ok := f.Get(k); !ok || v != w {
			t.Errorf("%s = %q,%v want %q", k, v, ok, w)
		}
	}
	if strings.Join(f.Keys(), ",") != "A,B,C,D,E" {
		t.Errorf("Keys = %v", f.Keys())
	}
	if !bytes.Equal(f.Render(), []byte(ok)) {
		t.Errorf("round trip: %q", f.Render())
	}

	bad := map[string]string{
		"export A=1\n":     ClassExport,
		"A=1 \\\n2\n":      ClassContinuation,
		"A=$HOME\n":        ClassExpansion,
		"A=\"$HOME\"\n":    ClassExpansion,
		"A=\"\\$x\"\n":     ClassExpansion,
		"A='it''s'\n":      ClassQuote,
		"A='open\n":        ClassQuote,
		"A=\"open\n":       ClassQuote,
		"A=x'y\n":          ClassQuote,
		"A='x' y\n":        ClassSyntax,
		"just text\n":      ClassSyntax,
		"1A=2\n":           ClassSyntax,
		"A=v # c\n":        ClassInlineComment,
		"A=v\t# c\n":       ClassInlineComment,
		"A='v' # c\n":      ClassInlineComment,
		"A=\"v\"   # c\n":  ClassInlineComment,
		"A=a#b # real\n":   ClassInlineComment,
		"A=1\nB=$(id)\n":   ClassExpansion,
		"A=1\r\nB=x y # c": ClassInlineComment,
	}
	for in, class := range bad {
		_, err := Parse([]byte(in))
		var ps Problems
		if err == nil || !errors.As(err, &ps) {
			t.Errorf("%q: err = %v", in, err)
			continue
		}
		found := false
		for _, p := range ps {
			if p.Class == class {
				found = true
				if p.Key != "A" && p.Key != "B" && class != ClassSyntax && class != ClassExport && class != ClassContinuation {
					t.Errorf("%q: problem lacks key: %+v", in, p)
				}
			}
		}
		if !found {
			t.Errorf("%q: want class %s, got %v", in, class, ps)
		}
	}
	// Unquoted value with '#' not preceded by whitespace is NOT a comment.
	f, err = Parse([]byte("A=a#b\n"))
	if err != nil {
		t.Fatal(err)
	}
	if v, _ := f.Get("A"); v != "a#b" {
		t.Errorf("A = %q", v)
	}
	// Lenient parse of a hard error still fails.
	if _, _, err := ParseLenient([]byte("export A=1\n")); err == nil {
		t.Error("ParseLenient accepted export")
	}
}

func TestSetUnsetRender(t *testing.T) {
	f, _ := Parse([]byte("# head\nA=1\nB=2\nA=3\n"))
	if v, _ := f.Get("A"); v != "3" {
		t.Fatalf("last wins: %q", v)
	}
	if p := f.Validate(); len(p) != 1 || p[0].Class != ClassDuplicateKey {
		t.Fatalf("duplicate not reported: %v", p)
	}
	if err := f.Set("A", "x y"); err != nil {
		t.Fatal(err)
	}
	if err := f.Set("C", `{"k":"v"}`); err != nil {
		t.Fatal(err)
	}
	if err := f.Set("D", "it's"); err != nil {
		t.Fatal(err)
	}
	if err := f.Set("E", "plain"); err != nil {
		t.Fatal(err)
	}
	if err := f.Set("F", "it's $x"); err == nil {
		t.Error("' with $ accepted")
	}
	if err := f.Set("G", "a\x01b"); err == nil {
		t.Error("control char accepted")
	}
	if err := f.Set("bad key", "v"); err == nil {
		t.Error("bad key accepted")
	}
	want := "# head\nB=2\nA='x y'\nC='{\"k\":\"v\"}'\nD=\"it's\"\nE=plain\n"
	if got := string(f.Render()); got != want {
		t.Errorf("Render:\n got %q\nwant %q", got, want)
	}
	if _, err := Parse(f.Render()); err != nil {
		t.Errorf("rendered file does not parse: %v", err)
	}
	if !f.Unset("B") || f.Unset("B") {
		t.Error("Unset")
	}
	if _, ok := f.Get("B"); ok {
		t.Error("B still present")
	}
	if p := f.Validate(); len(p) != 0 {
		t.Errorf("Validate: %v", p)
	}
	// Trailing whitespace ends the word in sh and is trimmed by systemd:
	// not part of the value, and the raw line still round-trips.
	g, _ := Parse([]byte("A=v \nB = 1\n"))
	if g != nil {
		t.Fatal("whitespace before '=' accepted (sh would run a command named B)")
	}
	g, _ = Parse([]byte("A=v \n"))
	if v, _ := g.Get("A"); v != "v" || string(g.Render()) != "A=v \n" {
		t.Errorf("trailing space: value %q render %q", v, g.Render())
	}
	// Empty file and no trailing newline.
	e, _ := Parse(nil)
	if len(e.Render()) != 0 {
		t.Error("empty render")
	}
	n, _ := Parse([]byte("A=1"))
	if string(n.Render()) != "A=1" {
		t.Errorf("no trailing newline preserved: %q", n.Render())
	}
}

func TestSplitSecrets(t *testing.T) {
	src := "# tenant.env — tenant 'x'\nAPI_KEYS='[\"a\"]'\nAPI_KEY_TENANTS='{\"a\":\"x\"}'\nAPI_KEY_ROLES='{\"a\":\"admin\"}'\nDEFAULT_ROLE=user\n\nQDRANT_URL=http://localhost:1\nPOSTGRES_DSN=postgresql://u:p@h/db\nNEO4J_PASSWORD=pw\nGOWE_TOKEN=t\nLOG_LEVEL=INFO\n"
	f, err := Parse([]byte(src))
	if err != nil {
		t.Fatal(err)
	}
	pub, sec := SplitSecrets(f, settings.Classify)
	if got := strings.Join(sec.Keys(), ","); got != "API_KEYS,API_KEY_TENANTS,API_KEY_ROLES,POSTGRES_DSN,NEO4J_PASSWORD,GOWE_TOKEN" {
		t.Errorf("secret keys: %s", got)
	}
	if got := strings.Join(pub.Keys(), ","); got != "DEFAULT_ROLE,QDRANT_URL,LOG_LEVEL" {
		t.Errorf("public keys: %s", got)
	}
	if !strings.HasPrefix(string(pub.Render()), "# tenant.env") || strings.Contains(string(pub.Render()), "API_KEYS") {
		t.Errorf("public render: %q", pub.Render())
	}
	if strings.Contains(string(sec.Render()), "LOG_LEVEL") || !strings.Contains(string(sec.Render()), "NEO4J_PASSWORD=pw") {
		t.Errorf("secret render: %q", sec.Render())
	}
	if len(f.Keys()) != 9 {
		t.Error("source modified")
	}
	if got := strings.Join(f.SecretKeys(), ","); got != "API_KEYS,API_KEY_ROLES,API_KEY_TENANTS,GOWE_TOKEN,NEO4J_PASSWORD,POSTGRES_DSN" {
		t.Errorf("SecretKeys: %s", got)
	}
	// Live fixture: dev's secret keys.
	d, _, _ := ParseLenient(readGolden(t, "dev/config/tenant.env"))
	if got := strings.Join(d.SecretKeys(), ","); got != "API_KEYS,API_KEY_ROLES,API_KEY_TENANTS" {
		t.Errorf("dev secret keys: %s", got)
	}
}

func TestRenderJSONValue(t *testing.T) {
	got, err := RenderJSONValue([]string{"<GENERATED:k_user>", "<GENERATED:k_admin>"})
	if err != nil || got != `'["<GENERATED:k_user>","<GENERATED:k_admin>"]'` {
		t.Errorf("array: %q %v", got, err)
	}
	got, err = RenderJSONValue(map[string]string{"b": "admin", "a": "user"})
	if err != nil || got != `'{"a":"user","b":"admin"}'` {
		t.Errorf("object: %q %v", got, err)
	}
	if _, err := RenderJSONValue([]string{"it's"}); err == nil {
		t.Error("single quote accepted")
	}
	f := &File{trailingNewline: true}
	_ = f.Set("API_KEYS", strings.Trim(got, "'"))
	if string(f.Render()) != "API_KEYS="+got+"\n" {
		t.Errorf("Set of a JSON value: %q", f.Render())
	}
}

// TestDoubleQuoteEscapesMatchBothReaders is S9. Inside double quotes systemd's
// EnvironmentFile= treats `\` as an escape for `"`, `\` and a line
// continuation and passes everything else through; sh does the same. Decoding
// `\n` to a real newline produced a value NEITHER reader would ever hand the
// API — and Set could then write one they would both mangle.
func TestDoubleQuoteEscapesMatchBothReaders(t *testing.T) {
	cases := map[string]string{
		`A="x\ny"`:       `x\ny`,     // two literal characters, not a newline
		`A="x\ty"`:       `x\ty`,     // ditto
		`A="a\\b"`:       `a\b`,      // the one backslash escape
		`A="say \"hi\""`: `say "hi"`, // the other
		`A="C:\path\to"`: `C:\path\to`,
	}
	for in, want := range cases {
		f, err := Parse([]byte(in + "\n"))
		if err != nil {
			t.Errorf("%s: %v", in, err)
			continue
		}
		got, _ := f.Get("A")
		if got != want {
			t.Errorf("%s → %q, want %q", in, got, want)
		}
		if strings.Contains(got, "\n") {
			t.Errorf("%s decoded a real newline", in)
		}
		// And it round-trips: what Render writes, Parse reads back the same.
		f2, err := Parse(f.Render())
		if err != nil {
			t.Errorf("%s: re-parse: %v", in, err)
			continue
		}
		if again, _ := f2.Get("A"); again != want {
			t.Errorf("%s: round trip %q → %q", in, want, again)
		}
	}
}

// TestSetRefusesAValueNoReaderCanHold: Set must not write what sh and systemd
// would disagree about. A newline has no representation both decode, so it is
// refused rather than encoded as `\n` (which they read literally).
func TestSetRefusesAValueNoReaderCanHold(t *testing.T) {
	f, err := Parse([]byte("A=1\n"))
	if err != nil {
		t.Fatal(err)
	}
	for _, bad := range []string{"x\ny", "\n", "a\x00b", "a\x07b"} {
		if err := f.Set("B", bad); err == nil {
			t.Errorf("Set accepted %q", bad)
		}
	}
	// A tab is fine — inside quotes it is a tab for both readers — and so is
	// every value that only needs quoting.
	for _, ok := range []string{"a\tb", "a b", "it's", `say "hi"`, `a'b"c`, `back\slash`} {
		if err := f.Set("B", ok); err != nil {
			t.Fatalf("Set(%q) = %v", ok, err)
		}
		f2, err := Parse(f.Render())
		if err != nil {
			t.Fatalf("%q: re-parse %v\n%s", ok, err, f.Render())
		}
		if got, _ := f2.Get("B"); got != ok {
			t.Errorf("%q round-tripped to %q\n%s", ok, got, f.Render())
		}
	}
}

// TestCRLFRoundTrips is S24: a CRLF file (or one that mixes endings, which is
// what a hand-edited tenant.env on a shared host looks like) must come back
// byte-for-byte. Silently rewriting every line ending turns an `env-set` of
// one key into a whole-file diff nobody asked for.
func TestCRLFRoundTrips(t *testing.T) {
	src := "# header\r\nA=1\r\nB=2\r\n"
	f, err := Parse([]byte(src))
	if err != nil {
		t.Fatal(err)
	}
	if got := string(f.Render()); got != src {
		t.Errorf("CRLF round trip: %q, want %q", got, src)
	}
	if v, _ := f.Get("A"); v != "1" {
		t.Errorf("A = %q; the \\r must not be part of the value", v)
	}
	mixed := "# crlf header\r\nA=1\nB=2\r\nC=3\n"
	f, err = Parse([]byte(mixed))
	if err != nil {
		t.Fatal(err)
	}
	if got := string(f.Render()); got != mixed {
		t.Errorf("mixed round trip: %q, want %q", got, mixed)
	}
}

// TestBOMIsPreservedNotRefused is S24: a UTF-8 BOM made the first key a hard
// syntax error ("invalid key \ufeffKEY"), so a file the shell reads perfectly
// well could not be parsed at all — and doctor reported the tenant's env as
// unloadable when it was not.
func TestBOMIsPreservedNotRefused(t *testing.T) {
	src := "\ufeffA=1\nB=2\n"
	f, err := Parse([]byte(src))
	if err != nil {
		t.Fatalf("a BOM must not be a parse error: %v", err)
	}
	if v, ok := f.Get("A"); !ok || v != "1" {
		t.Errorf("A = %q, %v; the BOM must not be part of the key", v, ok)
	}
	if got := string(f.Render()); got != src {
		t.Errorf("BOM round trip: %q, want %q", got, src)
	}
}

// TestLeadingWhitespaceIsNotPartOfTheValue is S24: systemd trims it and in sh
// `A=  x` is not an assignment of "  x" either, so keeping it made Get return
// a value no reader of the same file would ever see.
func TestLeadingWhitespaceIsNotPartOfTheValue(t *testing.T) {
	f, err := Parse([]byte("A=  lead\nB=\t'quoted'\nC=   \"dq\"\n"))
	if err != nil {
		t.Fatal(err)
	}
	for k, want := range map[string]string{"A": "lead", "B": "quoted", "C": "dq"} {
		if got, _ := f.Get(k); got != want {
			t.Errorf("%s = %q, want %q", k, got, want)
		}
	}
	// An untouched line still renders byte-exact: the trim is a READING
	// decision, not a rewrite.
	src := "A=  lead\n"
	f, _ = Parse([]byte(src))
	if got := string(f.Render()); got != src {
		t.Errorf("untouched render = %q, want %q", got, src)
	}
}

// TestSeedRedactorMinesHistoryAndKeysOnly covers the two seeding rules S16 and
// S17 turn on: rotated-out secrets in *.bak-*/*.orig are seeded, and the
// VALUES of API_KEY_TENANTS / API_KEY_ROLES (tenant names, roles) are not.
func TestSeedRedactorMinesHistoryAndKeysOnly(t *testing.T) {
	dir := t.TempDir()
	wr := func(name, body string) {
		t.Helper()
		if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	wr("tenant.env", `API_KEYS='["k-current-aaaaaaaaaa"]'`+"\n"+
		`API_KEY_TENANTS='{"k-current-aaaaaaaaaa":"lucid-next"}'`+"\n"+
		"LOG_LEVEL=info\n")
	wr("secrets.env", "TENANT_PG_PASSWORD=pw-current-value\n")
	wr("secrets.env.bak-20260801", "TENANT_PG_PASSWORD=pw-rotated-value\n")
	wr("tenant.env.orig", `API_KEYS='["k-rotated-bbbbbbbbbb"]'`+"\n")

	r := settings.NewRedactor()
	if err := SeedRedactor(dir, r); err != nil {
		t.Fatal(err)
	}
	out := r.Redact("k-current-aaaaaaaaaa k-rotated-bbbbbbbbbb pw-current-value pw-rotated-value lucid-next info")
	for _, secret := range []string{"k-current-aaaaaaaaaa", "k-rotated-bbbbbbbbbb", "pw-current-value", "pw-rotated-value"} {
		if strings.Contains(out, secret) {
			t.Errorf("%s survived: %q", secret, out)
		}
	}
	if !strings.Contains(out, "lucid-next") {
		t.Errorf("a tenant name must not be a seed: %q", out)
	}
	if !strings.Contains(out, "info") {
		t.Errorf("a public value must not be a seed: %q", out)
	}
}

// TestSeedRedactorFailsClosedOnALiveFile: a live env file that cannot be
// mined at all is an error, so a caller that stamps `redacted: true` cannot
// do it on an empty seed set. A historical copy that cannot be mined is not:
// a file with no KEY=value line in it holds no env secret to begin with.
func TestSeedRedactorFailsClosedOnALiveFile(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "tenant.env"), []byte("this is a note, not an env file\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := SeedRedactor(dir, settings.NewRedactor()); err == nil {
		t.Fatal("an unminable tenant.env must be an error")
	}

	dir2 := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir2, "tenant.env"), []byte("A=1\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir2, "tenant.env.bak-old"), []byte("a note\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := SeedRedactor(dir2, settings.NewRedactor()); err != nil {
		t.Fatalf("an unminable BACKUP must not fail the seeding: %v", err)
	}
}
