// Package auth resolves the calling principal of a control-plane request:
// BV-BRC signed tokens (this file), ctl API keys (keys.go), opaque sessions
// and the HTTP middleware that binds the three together (middleware.go).
//
// # Why a second BV-BRC verifier
//
// This is a deliberate port of python/ragstack/identity/bvbrc.py, not a
// re-design. The two implementations replay the SAME fixture table
// (contracts/fixtures/identity/bvbrc/vectors.json) so the cases that matter —
// tampering, the wrong key, the signed-region boundary, first-occurrence-wins
// parsing, expiry — cannot drift between the Python tenant API and the Go
// control plane. Where the Python raises IdentityInvalid this returns an error
// wrapping ErrInvalid; where it raises IdentityUnavailable, ErrUnavailable.
// The reason strings are copied verbatim so the fixtures' advisory
// `reason_substring` matches on both sides without a translation table.
//
// # The order is the contract
//
//	parse → pin the SigningSubject against the allowlist (BEFORE any fetch)
//	      → check expiry (uncached; `expiry <= now` is expired)
//	      → verify RSA PKCS#1 v1.5 over SHA-1
//	      → only THEN read `un` / `tokenid`
//
// BV-BRC's own validateToken.js fetches the verifying key from whatever URL the
// token embeds, and its allowlist guard builds an Error it never throws — so
// anyone who can serve a URL can mint a token for any username. Pinning the
// allowlist before the fetch is what closes that, and reading `un` only after
// the signature verifies is what makes it a server-set claim rather than an
// echo of client input.
package auth

import (
	"context"
	"crypto"
	"crypto/rsa"
	"crypto/sha1" //nolint:gosec // BV-BRC signs with SHA-1; verification of an existing scheme, not our choice
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"
)

// DefaultSigningSubjects is the canonical signing-subject set from BV-BRC's
// P3AuthConstants.pm — the default allowlist, identical to the Python
// provider's DEFAULT_SIGNING_SUBJECTS. Deployments may narrow it
// (CTL_IDENTITY_ISSUER_ALLOWLIST); an explicitly EMPTY allowlist is a
// configuration error, not "allow everything".
var DefaultSigningSubjects = []string{
	"https://user.patricbrc.org/public_key",
	"https://user.bv-brc.org/public_key",
	"https://user.alpha.patricbrc.org/public_key",
	"https://user.beta.patricbrc.org/public_key",
}

// Issuer is the issuer id this verifier stamps on the identities it returns.
const Issuer = "bvbrc"

// sigSeparator delimits the signed payload from the signature. Everything
// before the FIRST occurrence is signed; the hex run after it, up to the next
// '|', is the signature; anything past that is outside the signed region and
// is discarded rather than parsed.
const sigSeparator = "|sig="

// The two error kinds every failure wraps.
//
//	ErrInvalid     — the credential is not usable: malformed, expired, an
//	                 unpinned issuer, a signature that does not verify, a
//	                 verified token missing `un`/`tokenid`. Over HTTP: 401.
//	ErrUnavailable — we could not decide, because the key server did not
//	                 answer or answered something unusable. Over HTTP: 401 as
//	                 well (the caller learns nothing from the difference), but
//	                 the distinction drives logging and doctor.
var (
	ErrInvalid     = errors.New("identity invalid")
	ErrUnavailable = errors.New("identity unavailable")
)

// IdentityError carries the reason alongside the kind. Reason strings are the
// Python provider's, verbatim, so one fixture table serves both verifiers.
type IdentityError struct {
	Kind   error
	Reason string
}

func (e *IdentityError) Error() string { return e.Reason }

// Unwrap exposes the kind to errors.Is.
func (e *IdentityError) Unwrap() error { return e.Kind }

func invalid(reason string) error     { return &IdentityError{Kind: ErrInvalid, Reason: reason} }
func unavailable(reason string) error { return &IdentityError{Kind: ErrUnavailable, Reason: reason} }

// Identity is a verified BV-BRC caller. Subject is the raw `un=` value; the
// ctl's principal id is "bvbrc:" + Subject (see keys.go).
type Identity struct {
	Subject     string
	Issuer      string
	TokenID     string
	ExpiresAt   *time.Time
	DisplayName string
}

// FetchFunc retrieves the body served at url. url is ALWAYS a member of the
// pinned allowlist — never a value taken from the token — so an implementation
// cannot be steered into an SSRF by a caller.
type FetchFunc func(ctx context.Context, url string) ([]byte, error)

// Options configures a Verifier. The zero value of every field is replaced by
// the default named on it.
type Options struct {
	// Allowlist of SigningSubject URLs. nil ⇒ DefaultSigningSubjects. An
	// explicitly empty (non-nil, len 0) slice is refused by New: it would
	// authenticate nobody, and the failure mode of "treat empty as allow all"
	// is exactly the forgery vector this list exists to close.
	Allowlist []string
	// KeyTTL is how long a fetched public key is reused. 0 ⇒ 24 h.
	KeyTTL time.Duration
	// MinRefetch rate-limits the rotation refetch that a BAD SIGNATURE
	// triggers, so a stream of garbage cannot be turned into a hammering loop
	// against the BV-BRC key server. 0 ⇒ 60 s.
	MinRefetch time.Duration
	// Now is the WALL clock, and is used for token EXPIRY only — an `expiry`
	// field is a wall-clock claim. nil ⇒ time.Now. Injected by the vector
	// replay.
	Now func() time.Time
	// Mono is the elapsed-time source the key cache and the refetch limiter
	// age against. It must be monotonic: with a wall clock, one NTP step
	// backwards makes every cached key look "fetched in the future" and pins
	// it for the length of the step — a key server that cannot be rotated out
	// of a running daemon, from a clock correction. nil ⇒ the process's
	// monotonic clock; when Now is injected and Mono is not, elapsed Now, so
	// a test that drives one clock keeps driving one clock.
	Mono func() time.Duration
	// Fetch retrieves a key-server body. nil ⇒ a net/http GET bounded by
	// Timeout that refuses redirects.
	Fetch FetchFunc
	// Timeout bounds the default Fetch. 0 ⇒ 5 s.
	Timeout time.Duration
}

type cachedKey struct {
	key *rsa.PublicKey
	// fetchedAt is monotonic elapsed time, not a wall clock; see Options.Mono.
	fetchedAt time.Duration
}

// Verifier verifies BV-BRC signed tokens against a pinned issuer allowlist.
// Safe for concurrent use.
type Verifier struct {
	allowlist  map[string]bool
	keyTTL     time.Duration
	minRefetch time.Duration
	now        func() time.Time
	mono       func() time.Duration
	fetch      FetchFunc

	mu   sync.Mutex
	keys map[string]cachedKey
	// lastAttempt is when a fetch of this URL was last ATTEMPTED, successful
	// or not — the negative cache. Without it, every bad signature arriving
	// while the key server is down costs a fresh 5 s outbound fetch, so a
	// stream of garbage turns a dead key server into a request-thread sink.
	lastAttempt map[string]time.Duration
}

// processStart anchors the default monotonic source. time.Since reads the
// monotonic component of the reading captured here, so it is unaffected by
// wall-clock steps.
var processStart = time.Now()

// New builds a Verifier. It fails only on an explicitly empty allowlist.
func New(opts Options) (*Verifier, error) {
	list := opts.Allowlist
	if list == nil {
		list = DefaultSigningSubjects
	}
	if len(list) == 0 {
		return nil, errors.New("bvbrc verifier requires a non-empty SigningSubject allowlist; an unpinned issuer lets anyone forge any username")
	}
	v := &Verifier{
		allowlist:   make(map[string]bool, len(list)),
		keyTTL:      opts.KeyTTL,
		minRefetch:  opts.MinRefetch,
		now:         opts.Now,
		mono:        opts.Mono,
		fetch:       opts.Fetch,
		keys:        map[string]cachedKey{},
		lastAttempt: map[string]time.Duration{},
	}
	for _, u := range list {
		v.allowlist[u] = true
	}
	if v.keyTTL == 0 {
		v.keyTTL = 24 * time.Hour
	}
	if v.minRefetch == 0 {
		v.minRefetch = 60 * time.Second
	}
	if v.now == nil {
		v.now = time.Now
	}
	if v.mono == nil {
		if opts.Now != nil {
			// An injected clock drives both: a test that steps its own clock
			// should see the cache age with it, unless it injects Mono too.
			epoch := opts.Now()
			v.mono = func() time.Duration { return opts.Now().Sub(epoch) }
		} else {
			v.mono = func() time.Duration { return time.Since(processStart) }
		}
	}
	if v.fetch == nil {
		timeout := opts.Timeout
		if timeout == 0 {
			timeout = 5 * time.Second
		}
		v.fetch = httpFetch(timeout)
	}
	return v, nil
}

// Allowlist returns the pinned signing subjects (sorted order is not promised;
// for doctor/logging only).
func (v *Verifier) Allowlist() []string {
	out := make([]string, 0, len(v.allowlist))
	for u := range v.allowlist {
		out = append(out, u)
	}
	return out
}

// Authenticate verifies token and returns the caller's identity.
//
// Every return path but the last is a refusal: see the package comment for why
// the order of the four checks is itself part of the contract.
func (v *Verifier) Authenticate(ctx context.Context, token string) (Identity, error) {
	payload, sig, err := splitToken(token)
	if err != nil {
		return Identity{}, err
	}
	fields := parseFields(payload)

	signingSubject := fields["SigningSubject"]
	if !v.allowlist[signingSubject] {
		// Before any network call: an unpinned SigningSubject is THE forgery
		// vector, so we never even fetch the key it names.
		return Identity{}, invalid("token SigningSubject is not an allowed issuer")
	}

	expiresAt, err := expiry(fields)
	if err != nil {
		return Identity{}, err
	}
	if expiresAt != nil && !expiresAt.After(v.now()) {
		// Checked on EVERY request, uncached: an expired credential never
		// reaches the verification path or any identity cache.
		return Identity{}, invalid("token expired")
	}

	if err := v.verify(ctx, signingSubject, payload, sig); err != nil {
		return Identity{}, err
	}

	// Past this line, and not one line earlier, `fields` is trustworthy: it was
	// parsed from the exact byte range the signature covers.
	subject := fields["un"]
	if subject == "" {
		return Identity{}, invalid("verified token carries no un= subject")
	}
	tokenID := fields["tokenid"]
	if tokenID == "" {
		return Identity{}, invalid("verified token carries no tokenid")
	}
	return Identity{
		Subject:   subject,
		Issuer:    Issuer,
		TokenID:   tokenID,
		ExpiresAt: expiresAt,
		// The token format carries no profile claims; `un` is the only
		// human-legible handle, so it doubles as the display name.
		DisplayName: subject,
	}, nil
}

func (v *Verifier) verify(ctx context.Context, url, payload string, sig []byte) error {
	key, err := v.publicKey(ctx, url)
	if err != nil {
		return err
	}
	if signatureOK(key, payload, sig) {
		return nil
	}
	// Rotation path: the pinned issuer may have rolled its key since we cached
	// it. Refetch once (rate-limited) and retry; still bad → invalid.
	refreshed, err := v.refreshKey(ctx, url)
	if err == nil && refreshed != nil && signatureOK(refreshed, payload, sig) {
		return nil
	}
	return invalid("token signature does not verify")
}

func (v *Verifier) publicKey(ctx context.Context, url string) (*rsa.PublicKey, error) {
	now := v.mono()
	v.mu.Lock()
	cached, ok := v.keys[url]
	attempted, tried := v.lastAttempt[url]
	v.mu.Unlock()
	if ok && now-cached.fetchedAt < v.keyTTL {
		return cached.key, nil
	}
	if tried && now-attempted < v.minRefetch {
		// Negative cache: a fetch was already tried this recently and we have
		// no usable key to show for it. Answering without a second outbound
		// call is what keeps a dead key server from turning each unverifiable
		// token into a 5 s wait.
		return nil, unavailable(fmt.Sprintf("bvbrc public key at %s is unreachable", url))
	}
	return v.fetchKey(ctx, url)
}

// refreshKey returns (nil, nil) when the refetch is rate-limited — the caller
// treats that exactly like a refetch that did not help.
func (v *Verifier) refreshKey(ctx context.Context, url string) (*rsa.PublicKey, error) {
	now := v.mono()
	v.mu.Lock()
	cached, ok := v.keys[url]
	attempted, tried := v.lastAttempt[url]
	v.mu.Unlock()
	if ok && now-cached.fetchedAt < v.minRefetch {
		return nil, nil // rate-limited: bad signatures must not become a DoS lever
	}
	if tried && now-attempted < v.minRefetch {
		return nil, nil // same rate limit, for the fetch that FAILED
	}
	return v.fetchKey(ctx, url)
}

func (v *Verifier) fetchKey(ctx context.Context, url string) (*rsa.PublicKey, error) {
	v.mu.Lock()
	v.lastAttempt[url] = v.mono()
	v.mu.Unlock()
	body, err := v.fetch(ctx, url)
	if err != nil {
		return nil, unavailable(fmt.Sprintf("bvbrc public key at %s is unreachable: %v", url, err))
	}
	pemText, err := extractPEM(body, url)
	if err != nil {
		return nil, err
	}
	key, err := parseRSAPublicKey(pemText)
	if err != nil {
		return nil, unavailable(fmt.Sprintf("bvbrc public key at %s is unparseable", url))
	}
	v.mu.Lock()
	v.keys[url] = cachedKey{key: key, fetchedAt: v.mono()}
	v.mu.Unlock()
	return key, nil
}

// extractPEM pulls the PEM out of a key-server body: JSON {"pubkey"|
// "public_key": …} or a bare PEM. Some deployments serve the PEM with literal
// backslash-n where the newlines belong, which every PEM parser rejects
// outright, so both shapes are unescaped.
func extractPEM(body []byte, url string) (string, error) {
	var doc map[string]any
	if err := json.Unmarshal(body, &doc); err == nil && doc != nil {
		text, _ := doc["pubkey"].(string)
		if text == "" {
			text, _ = doc["public_key"].(string)
		}
		if text == "" {
			return "", unavailable(fmt.Sprintf("bvbrc key server response at %s has no pubkey field", url))
		}
		return unescapeNewlines(text), nil
	}
	return unescapeNewlines(string(body)), nil
}

func unescapeNewlines(s string) string {
	return strings.TrimSpace(strings.ReplaceAll(s, `\n`, "\n"))
}

func parseRSAPublicKey(pemText string) (*rsa.PublicKey, error) {
	block, _ := pem.Decode([]byte(pemText))
	if block == nil {
		return nil, errors.New("not a PEM block")
	}
	if key, err := x509.ParsePKIXPublicKey(block.Bytes); err == nil {
		rsaKey, ok := key.(*rsa.PublicKey)
		if !ok {
			return nil, errors.New("not an RSA key")
		}
		return rsaKey, nil
	}
	// PKCS#1 ("BEGIN RSA PUBLIC KEY") is the other shape in the wild.
	return x509.ParsePKCS1PublicKey(block.Bytes)
}

func signatureOK(key *rsa.PublicKey, payload string, sig []byte) bool {
	// SHA-1 is what BV-BRC signs with; the choice is theirs. It is sound for
	// VERIFYING an existing scheme (a collision attack needs the signer's
	// cooperation) and is the reason this verifier stays pinned to a small,
	// explicit issuer set.
	sum := sha1.Sum([]byte(payload)) //nolint:gosec // see above
	return rsa.VerifyPKCS1v15(key, crypto.SHA1, sum[:], sig) == nil
}

// splitToken returns (signed payload, signature bytes).
//
// The signed payload is everything before the FIRST "|sig="; the signature is
// the hex run that follows, up to the next '|'. Anything after that is outside
// the signed region and is discarded rather than parsed — appending "|un=eve"
// to a valid token must not change who the caller is.
func splitToken(credential string) (string, []byte, error) {
	idx := strings.Index(credential, sigSeparator)
	if idx <= 0 {
		return "", nil, invalid("malformed BV-BRC token: no signature")
	}
	payload := credential[:idx]
	tail := credential[idx+len(sigSeparator):]
	if cut := strings.Index(tail, "|"); cut >= 0 {
		tail = tail[:cut]
	}
	sigHex := strings.TrimSpace(tail)
	sig, err := hex.DecodeString(sigHex)
	if err != nil {
		return "", nil, invalid("malformed BV-BRC token: signature is not hex")
	}
	if len(sig) == 0 {
		return "", nil, invalid("malformed BV-BRC token: empty signature")
	}
	return payload, sig, nil
}

// parseFields parses "k=v|k=v". The FIRST occurrence of a key wins, so a
// duplicated field cannot shadow the one a validator already read.
func parseFields(payload string) map[string]string {
	fields := map[string]string{}
	for _, part := range strings.Split(payload, "|") {
		key, value, found := strings.Cut(part, "=")
		if !found {
			continue
		}
		if _, seen := fields[key]; !seen {
			fields[key] = value
		}
	}
	return fields
}

// expiry reads `expiry=`. A token with NO expiry is refused rather than turned
// into an immortal session; a non-numeric one is refused as malformed. Python
// parses it as int(float(raw)), so "1757548801.5" is accepted there and here.
func expiry(fields map[string]string) (*time.Time, error) {
	raw, ok := fields["expiry"]
	if !ok {
		return nil, invalid("token carries no expiry")
	}
	secs, err := strconv.ParseFloat(raw, 64)
	if err != nil || math.IsNaN(secs) || math.IsInf(secs, 0) {
		return nil, invalid("token expiry is not a number")
	}
	t := time.Unix(int64(secs), 0).UTC()
	return &t, nil
}

// httpFetch is the default FetchFunc: a plain GET bounded by timeout that
// refuses redirects (a 302 out of the pinned allowlist would undo the pinning)
// and caps the body so a hostile key server cannot exhaust memory.
func httpFetch(timeout time.Duration) FetchFunc {
	client := &http.Client{
		Timeout: timeout,
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return errors.New("redirect refused: the signing subject is pinned")
		},
	}
	return func(ctx context.Context, url string) ([]byte, error) {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
		if err != nil {
			return nil, err
		}
		resp, err := client.Do(req)
		if err != nil {
			return nil, err
		}
		defer func() { _ = resp.Body.Close() }()
		if resp.StatusCode != http.StatusOK {
			return nil, fmt.Errorf("key server answered %d", resp.StatusCode)
		}
		return io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	}
}
