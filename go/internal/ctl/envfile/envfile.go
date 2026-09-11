// Package envfile parses and renders tenant.env / secrets.env files with
// comments and order preserved, in the subset of the shell-sourcing grammar
// that systemd's EnvironmentFile= reads identically:
//
//	KEY=value          unquoted; no '$', no ' #' (systemd keeps an inline comment)
//	KEY='literal'      no "'" inside
//	KEY="escaped"      backslash escapes \\ and \" ONLY; no '$'
//	# comment / ; comment / blank lines
//
// Inside double quotes only `\\` and `\"` are escapes. Every other backslash
// sequence — `\n` above all — is kept LITERALLY, as two characters, because
// that is what both readers do: systemd's EnvironmentFile= treats `\` as an
// escape for `"`, `\` and a line continuation and passes anything else
// through, and sh inside double quotes does the same. Decoding `\n` to a real
// newline (which this package used to do) meant Get returned a value neither
// reader would ever produce, and Set could write one they would both mangle.
//
// Leading whitespace in an unquoted value is dropped (`A=  x` is `x`), as in
// sh and systemd. CRLF line endings and a UTF-8 BOM are preserved verbatim
// through Parse→Render rather than being silently rewritten or refused.
//
// Refused outright (Parse fails): `export KEY=…`, line continuations, `$`
// anywhere in an unquoted or double-quoted value (the shell would expand it,
// systemd would not). Inline comments after an unquoted value are the one
// divergence the live dev tenant carries (13 keys, 2026-09-10): Parse refuses
// them too, ParseLenient loads them so Normalize can move the comment to the
// preceding line.
package envfile

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"sort"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/settings"
)

// Problem classes.
const (
	ClassInlineComment = "inline_comment" // unquoted value followed by ` # …`
	ClassExpansion     = "expansion"      // `$` in an unquoted/double-quoted value
	ClassExport        = "export"         // `export KEY=…`
	ClassContinuation  = "continuation"   // line ends in `\`
	ClassQuote         = "quote"          // unterminated / stray quote, `'` inside '…'
	ClassSyntax        = "syntax"         // not KEY=value
	ClassDuplicateKey  = "duplicate_key"  // key assigned more than once (last wins)
)

// Problem is one grammar or compatibility finding.
type Problem struct {
	Line  int    `json:"line"`
	Class string `json:"class"`
	Key   string `json:"key,omitempty"`
	Msg   string `json:"msg"`
}

func (p Problem) Error() string {
	if p.Key != "" {
		return fmt.Sprintf("line %d: %s (%s): %s", p.Line, p.Class, p.Key, p.Msg)
	}
	return fmt.Sprintf("line %d: %s: %s", p.Line, p.Class, p.Msg)
}

// Problems is a list of findings that is also an error.
type Problems []Problem

func (ps Problems) Error() string {
	parts := make([]string, len(ps))
	for i, p := range ps {
		parts[i] = p.Error()
	}
	return strings.Join(parts, "; ")
}

// Keys lists the keys of the problems in order (duplicates kept).
func (ps Problems) Keys() []string {
	out := make([]string, 0, len(ps))
	for _, p := range ps {
		if p.Key != "" {
			out = append(out, p.Key)
		}
	}
	return out
}

type quote int

const (
	unquoted quote = iota
	single
	double
)

type line struct {
	raw     string // exact source text (no newline); authoritative until modified
	kind    byte   // 'a' assignment, '#' comment, ' ' blank
	key     string
	value   string
	quote   quote
	trailer string // inline comment after an unquoted/quoted value (ParseLenient only), without leading space
	dirty   bool   // value changed: render from fields, not raw
	cr      bool   // the source line ended CRLF; Render puts the \r back
}

// File is a parsed env file.
type File struct {
	lines           []line
	trailingNewline bool
	bom             bool // the source began with a UTF-8 BOM; Render restores it
}

var keyPattern = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)

// Parse parses b strictly: any Problem is an error and no File is returned.
func Parse(b []byte) (*File, error) {
	f, lenient, err := parse(b)
	if err != nil {
		return nil, err
	}
	if len(lenient) > 0 {
		return nil, lenient
	}
	return f, nil
}

// ParseLenient parses b, tolerating inline comments (reported as Problems of
// class inline_comment so the caller can Normalize). Hard grammar violations
// still fail.
func ParseLenient(b []byte) (*File, Problems, error) {
	f, lenient, err := parse(b)
	if err != nil {
		return nil, nil, err
	}
	return f, lenient, nil
}

// utf8BOM is what a Windows editor (or a careless `>` from PowerShell) puts
// in front of the first key. It is not part of the first key's name.
var utf8BOM = []byte{0xEF, 0xBB, 0xBF}

func parse(b []byte) (*File, Problems, error) {
	f := &File{}
	if bytes.HasPrefix(b, utf8BOM) {
		// Strip it for parsing and remember it for Render. Leaving it in
		// made the first line a syntax error ("invalid key \ufeffKEY"), so a
		// file the shell reads perfectly well was a hard parse failure.
		f.bom = true
		b = b[len(utf8BOM):]
	}
	f.trailingNewline = len(b) == 0 || b[len(b)-1] == '\n'
	text := string(b)
	if f.trailingNewline && len(text) > 0 {
		text = text[:len(text)-1]
	}
	var hard, soft Problems
	if len(b) == 0 {
		return f, nil, nil
	}
	for i, raw := range strings.Split(text, "\n") {
		n := i + 1
		cr := strings.HasSuffix(raw, "\r")
		raw = strings.TrimSuffix(raw, "\r")
		l, p := parseLine(n, raw)
		l.cr = cr
		if p != nil {
			if p.Class == ClassInlineComment {
				soft = append(soft, *p)
			} else {
				hard = append(hard, *p)
				continue
			}
		}
		f.lines = append(f.lines, l)
	}
	if len(hard) > 0 {
		return nil, nil, hard
	}
	return f, soft, nil
}

func parseLine(n int, raw string) (line, *Problem) {
	// Left-trim only: trailing whitespace on an unquoted value is part of
	// the value in sh (and a compatibility finding, see Validate).
	t := strings.TrimLeft(raw, " \t")
	switch {
	case strings.TrimSpace(t) == "":
		return line{raw: raw, kind: ' '}, nil
	case t[0] == '#' || t[0] == ';':
		return line{raw: raw, kind: '#'}, nil
	}
	if strings.HasSuffix(strings.TrimRight(t, " \t"), "\\") {
		return line{}, &Problem{Line: n, Class: ClassContinuation, Msg: "line continuations are not supported"}
	}
	if strings.HasPrefix(t, "export ") || strings.HasPrefix(t, "export\t") {
		return line{}, &Problem{Line: n, Class: ClassExport, Msg: "`export` prefix is not supported (systemd would treat it as a key)"}
	}
	eq := strings.IndexByte(t, '=')
	if eq < 0 {
		return line{}, &Problem{Line: n, Class: ClassSyntax, Msg: "expected KEY=value"}
	}
	key := t[:eq]
	if !keyPattern.MatchString(key) {
		return line{}, &Problem{Line: n, Class: ClassSyntax, Key: strings.TrimSpace(key), Msg: fmt.Sprintf("invalid key %q", key)}
	}
	// Leading whitespace before the value is not part of it: systemd trims
	// it, and in sh `A=  x` is not an assignment of "  x" either. Keeping it
	// made Get return a value no reader of the same file would see.
	rest := strings.TrimLeft(t[eq+1:], " \t")
	l := line{raw: raw, kind: 'a', key: key}
	if strings.TrimSpace(rest) == "" {
		return l, nil
	}
	switch rest[0] {
	case '\'':
		end := strings.IndexByte(rest[1:], '\'')
		if end < 0 {
			return line{}, &Problem{Line: n, Class: ClassQuote, Key: key, Msg: "unterminated single quote"}
		}
		l.quote = single
		l.value = rest[1 : 1+end]
		tail := rest[2+end:]
		if strings.Contains(tail, "'") && !isComment(tail) {
			return line{}, &Problem{Line: n, Class: ClassQuote, Key: key, Msg: "a single-quoted value cannot contain ' (no escape exists in shell)"}
		}
		return finishTail(n, l, tail)
	case '"':
		val, consumed, perr := parseDouble(n, key, rest[1:])
		if perr != nil {
			return line{}, perr
		}
		l.quote = double
		l.value = val
		return finishTail(n, l, rest[1+consumed:])
	}
	// unquoted: trailing whitespace ends the word in sh and is trimmed by
	// systemd, so it is not part of the value for either.
	rest = strings.TrimRight(rest, " \t")
	if strings.ContainsAny(rest, "'\"") {
		return line{}, &Problem{Line: n, Class: ClassQuote, Key: key, Msg: "quote inside an unquoted value; quote the whole value"}
	}
	if strings.ContainsRune(rest, '$') {
		return line{}, &Problem{Line: n, Class: ClassExpansion, Key: key, Msg: "`$` in an unquoted value: the shell expands it, systemd does not"}
	}
	if idx := inlineCommentIndex(rest); idx >= 0 {
		l.value = strings.TrimRight(rest[:idx], " \t")
		l.trailer = strings.TrimSpace(rest[idx:])
		return l, &Problem{Line: n, Class: ClassInlineComment, Key: key, Msg: "inline comment after an unquoted value: systemd keeps it as part of the value"}
	}
	l.value = rest
	return l, nil
}

// finishTail handles what follows a closing quote: nothing, or a comment.
func finishTail(n int, l line, tail string) (line, *Problem) {
	if strings.TrimSpace(tail) == "" {
		return l, nil
	}
	if isComment(tail) {
		l.trailer = strings.TrimSpace(tail)
		return l, &Problem{Line: n, Class: ClassInlineComment, Key: l.key, Msg: "inline comment after a quoted value"}
	}
	return line{}, &Problem{Line: n, Class: ClassSyntax, Key: l.key, Msg: "unexpected text after the closing quote"}
}

func isComment(tail string) bool {
	tt := strings.TrimLeft(tail, " \t")
	return len(tt) < len(tail) && strings.HasPrefix(tt, "#")
}

// inlineCommentIndex finds ` #` / `\t#` in an unquoted value; -1 if none.
func inlineCommentIndex(v string) int {
	for i := 1; i < len(v); i++ {
		if v[i] == '#' && (v[i-1] == ' ' || v[i-1] == '\t') {
			return i - 1
		}
	}
	return -1
}

// parseDouble decodes a double-quoted value starting after the opening quote.
// Returns the value, the number of bytes consumed INCLUDING the closing quote.
func parseDouble(n int, key, s string) (string, int, *Problem) {
	var b strings.Builder
	for i := 0; i < len(s); i++ {
		c := s[i]
		switch c {
		case '\\':
			if i+1 >= len(s) {
				return "", 0, &Problem{Line: n, Class: ClassQuote, Key: key, Msg: "dangling backslash"}
			}
			i++
			switch s[i] {
			case '\\', '"':
				// The only two escapes systemd's EnvironmentFile= and sh
				// agree on inside double quotes.
				b.WriteByte(s[i])
			case '$':
				return "", 0, &Problem{Line: n, Class: ClassExpansion, Key: key, Msg: "`\\$` is not portable between sh and systemd"}
			default:
				// Everything else is two literal characters for BOTH
				// readers — `A="x\ny"` is x, backslash, n, y, not two lines.
				b.WriteByte('\\')
				b.WriteByte(s[i])
			}
		case '$':
			return "", 0, &Problem{Line: n, Class: ClassExpansion, Key: key, Msg: "`$` in a double-quoted value: the shell expands it, systemd does not"}
		case '"':
			return b.String(), i + 1, nil
		default:
			b.WriteByte(c)
		}
	}
	return "", 0, &Problem{Line: n, Class: ClassQuote, Key: key, Msg: "unterminated double quote"}
}

// Get returns the value of key (last assignment wins, as in sh and systemd).
func (f *File) Get(key string) (string, bool) {
	for i := len(f.lines) - 1; i >= 0; i-- {
		if f.lines[i].kind == 'a' && f.lines[i].key == key {
			return f.lines[i].value, true
		}
	}
	return "", false
}

// Keys returns the assigned keys in first-seen order, without duplicates.
func (f *File) Keys() []string {
	seen := map[string]bool{}
	var out []string
	for _, l := range f.lines {
		if l.kind == 'a' && !seen[l.key] {
			seen[l.key] = true
			out = append(out, l.key)
		}
	}
	return out
}

// Set assigns key=value, replacing the last existing assignment in place
// (earlier duplicates are removed) or appending. The quoting style is chosen
// for the value: unquoted when safe, else single quotes, else double quotes.
// An error is returned when value cannot be represented (contains a newline
// AND a '"'/'\\' sequence is fine; only control characters other than \n\t
// are refused).
func (f *File) Set(key, value string) error {
	if !keyPattern.MatchString(key) {
		return fmt.Errorf("invalid key %q", key)
	}
	q, err := chooseQuote(value)
	if err != nil {
		return fmt.Errorf("%s: %w", key, err)
	}
	last := -1
	for i, l := range f.lines {
		if l.kind == 'a' && l.key == key {
			last = i
		}
	}
	nl := line{kind: 'a', key: key, value: value, quote: q, dirty: true}
	if last < 0 {
		f.lines = append(f.lines, nl)
		return nil
	}
	f.lines[last] = nl
	f.removeDuplicatesBefore(key, last)
	return nil
}

// Unset removes every assignment of key. Reports whether any existed.
func (f *File) Unset(key string) bool {
	var kept []line
	found := false
	for _, l := range f.lines {
		if l.kind == 'a' && l.key == key {
			found = true
			continue
		}
		kept = append(kept, l)
	}
	f.lines = kept
	return found
}

func (f *File) removeDuplicatesBefore(key string, idx int) {
	var kept []line
	for i, l := range f.lines {
		if i < idx && l.kind == 'a' && l.key == key {
			continue
		}
		kept = append(kept, l)
	}
	f.lines = kept
}

// chooseQuote picks the quoting style for a value, or refuses a value no env
// file can hold. A NEWLINE is refused outright (and so is NUL and every other
// control character but tab): there is no encoding of it that sh and systemd
// read the same way — `\n` inside double quotes is literal for both — so
// writing one would produce a file whose readers disagree about the value.
func chooseQuote(v string) (quote, error) {
	for _, r := range v {
		if r == '\n' {
			return unquoted, errors.New("value contains a newline, which no env-file quoting can represent for both sh and systemd")
		}
		if r < 0x20 && r != '\t' {
			return unquoted, fmt.Errorf("control character %U in value", r)
		}
	}
	if v != "" && !strings.ContainsAny(v, " \t#'\"$\\") && strings.TrimSpace(v) == v {
		return unquoted, nil
	}
	if !strings.ContainsRune(v, '\'') {
		return single, nil
	}
	if strings.ContainsRune(v, '$') {
		return unquoted, errors.New("value contains both ' and $ — not representable without shell expansion")
	}
	return double, nil
}

func renderValue(v string, q quote) string {
	switch q {
	case single:
		return "'" + v + "'"
	case double:
		// Only the two escapes both readers decode. A tab goes in literally
		// (inside quotes it is a tab for sh and for systemd); a newline
		// cannot reach here — chooseQuote refuses it.
		var b strings.Builder
		b.WriteByte('"')
		for _, r := range v {
			switch r {
			case '\\':
				b.WriteString(`\\`)
			case '"':
				b.WriteString(`\"`)
			default:
				b.WriteRune(r)
			}
		}
		b.WriteByte('"')
		return b.String()
	default:
		return v
	}
}

// Render serialises the file. Unchanged lines are emitted byte-exact, so
// Parse→Render round-trips an untouched file.
func (f *File) Render() []byte {
	var b bytes.Buffer
	if f.bom {
		b.Write(utf8BOM)
	}
	// The line ending belongs to the line BEFORE it, so each line restores
	// its own CRLF: a file that mixes them (a CRLF header over LF additions,
	// which is what a hand-edited tenant.env on a shared host looks like)
	// round-trips byte-for-byte instead of being silently normalised.
	for i, l := range f.lines {
		if i > 0 {
			if f.lines[i-1].cr {
				b.WriteByte('\r')
			}
			b.WriteByte('\n')
		}
		if l.kind != 'a' || !l.dirty {
			b.WriteString(l.raw)
			continue
		}
		b.WriteString(l.key)
		b.WriteByte('=')
		b.WriteString(renderValue(l.value, l.quote))
	}
	if f.trailingNewline && (len(f.lines) > 0 || b.Len() > 0) {
		if n := len(f.lines); n > 0 && f.lines[n-1].cr {
			b.WriteByte('\r')
		}
		b.WriteByte('\n')
	}
	return b.Bytes()
}

// Validate reports the systemd-EnvironmentFile compatibility problems that
// survive parsing: inline comments (which only ParseLenient admits) and
// duplicate keys. Leading and trailing whitespace around an unquoted value is
// NOT among them — parse drops it, exactly as both readers do, so there is
// nothing left to report. A strictly parsed, normalized file is clean.
func (f *File) Validate() []Problem {
	var ps []Problem
	seen := map[string]int{}
	for i, l := range f.lines {
		if l.kind != 'a' {
			continue
		}
		n := i + 1
		if l.trailer != "" {
			ps = append(ps, Problem{Line: n, Class: ClassInlineComment, Key: l.key, Msg: "inline comment: systemd keeps it as part of the value"})
		}
		if first, dup := seen[l.key]; dup {
			ps = append(ps, Problem{Line: n, Class: ClassDuplicateKey, Key: l.key, Msg: fmt.Sprintf("also assigned on line %d (last wins)", first)})
		} else {
			seen[l.key] = n
		}
	}
	return ps
}

// Normalize moves every inline comment to a full comment line immediately
// before its assignment (`K=v   # c` → `# c` / `K=v`) and returns the keys
// touched, in file order.
func (f *File) Normalize() []string {
	var out []line
	var moved []string
	for _, l := range f.lines {
		if l.kind == 'a' && l.trailer != "" {
			out = append(out, line{raw: l.trailer, kind: '#'})
			l.trailer = ""
			l.dirty = true
			moved = append(moved, l.key)
		}
		out = append(out, l)
	}
	f.lines = out
	return moved
}

// SplitSecrets partitions f by classify: assignments whose key classifies
// Secret go to secrets (in order, assignment lines only); everything else —
// comments, blanks, public and executable-surface keys — stays in public.
// f is not modified.
func SplitSecrets(f *File, classify func(string) settings.Class) (public, secrets *File) {
	public = &File{trailingNewline: true}
	secrets = &File{trailingNewline: true}
	for _, l := range f.lines {
		if l.kind == 'a' && classify(l.key) == settings.Secret {
			secrets.lines = append(secrets.lines, l)
			continue
		}
		public.lines = append(public.lines, l)
	}
	return public, secrets
}

// SecretKeys lists the keys of f that classify Secret, sorted.
func (f *File) SecretKeys() []string {
	var out []string
	for _, k := range f.Keys() {
		if settings.IsSecret(k) {
			out = append(out, k)
		}
	}
	sort.Strings(out)
	return out
}

// RenderJSONValue renders v as compact JSON wrapped in single quotes, the
// form tenant.env uses for API_KEYS / API_KEY_TENANTS / EMBEDDING_ENDPOINTS.
// It fails when the JSON contains a single quote (unrepresentable in '…').
func RenderJSONValue(v any) (string, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false) // "<GENERATED:k_user>" must stay literal
	if err := enc.Encode(v); err != nil {
		return "", err
	}
	s := strings.TrimSuffix(buf.String(), "\n")
	if strings.ContainsRune(s, '\'') {
		return "", errors.New("JSON value contains a single quote and cannot be single-quoted in an env file")
	}
	return "'" + s + "'", nil
}
