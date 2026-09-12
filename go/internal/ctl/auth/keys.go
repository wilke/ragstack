package auth

import (
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strings"
	"time"
)

// Roles. There are exactly two, and there is NO default: a credential that
// verifies but whose subject is on neither list is 403, never a read-only
// fallback (contracts/ctl/openapi.yaml, `Forbidden`).
const (
	RoleViewer   = "viewer"
	RoleOperator = "operator"
)

// Authentication methods, as reported by GET /v1/me (`me_response.json`).
const (
	MethodAPIKey  = "api_key"
	MethodBearer  = "bearer"
	MethodSession = "session"
	MethodLocal   = "local"
)

// RoleAtLeast reports whether have satisfies need under viewer < operator.
// anonymous (the empty string as a requirement) is satisfied by anything.
func RoleAtLeast(have, need string) bool {
	switch need {
	case "", "anonymous":
		return true
	case RoleViewer:
		return have == RoleViewer || have == RoleOperator
	case RoleOperator:
		return have == RoleOperator
	default:
		return false // deny by default: an unknown requirement is not satisfiable
	}
}

// Principal is the resolved caller of one request. It is what the authz matrix
// is evaluated against and what an audit row records; it never carries the
// credential itself.
type Principal struct {
	// Subject is the stable principal id: "bvbrc:<un>" for a bearer identity,
	// "key:<label-or-fingerprint>" for a ctl API key, "local:<uid>" for
	// --direct. It is what me_response.json calls `principal`.
	Subject string
	Role    string
	// AuthMethod names the credential that WON, not the ones presented.
	AuthMethod string
	// KeyFingerprint is "sha256:<16 hex>" of the ctl key that authenticated
	// (or that a session was minted from); "" for a bearer identity. A
	// fingerprint, never a prefix of the key: it must not be invertible.
	KeyFingerprint string
	// ExpiresAt is the expiry of the credential that authenticated this
	// request — a token's `expiry`, a session's end. nil for a ctl key, which
	// does not expire (it is revoked).
	ExpiresAt *time.Time
	// BoundSubject is the identity principal a ctl API KEY belongs to, from
	// CTL_API_KEY_PRINCIPALS; "" when the key is bound to nothing but itself.
	// Set only by LookupKey, and never used to authenticate anyone: it is read
	// by the body-key rule to decide whether the key re-presented in a
	// mutation body belongs to the identity that authenticated the request.
	BoundSubject string
}

// Fingerprint is the ledger form of a key: "sha256:" + the first 16 hex
// characters of its SHA-256. Same shape as registry.json's `keys[].fingerprint`.
func Fingerprint(key string) string {
	sum := sha256.Sum256([]byte(key))
	return "sha256:" + hex.EncodeToString(sum[:])[:16]
}

// CredentialHash is the rate limiter's key: the full SHA-256 of whatever
// credential was presented. Never logged, never returned.
func CredentialHash(credential string) string {
	sum := sha256.Sum256([]byte(credential))
	return hex.EncodeToString(sum[:])
}

// CredentialHashFor is CredentialHash over a (kind, value) pair, which is what
// the limiter actually keys on: the kind separates namespaces, and hashing the
// EXTRACTED value — not the raw header line — is what stops `Bearer t` and
// `bearer t` from being two budgets for one token. The NUL separator cannot
// occur in an HTTP header value, so no two pairs collide by concatenation.
func CredentialHashFor(kind, value string) string {
	return CredentialHash(kind + "\x00" + value)
}

type keyEntry struct {
	value       string
	label       string
	role        string
	fingerprint string
	// boundSubject is the identity principal this key belongs to
	// ("bvbrc:alice@patricbrc.org"), from CTL_API_KEY_PRINCIPALS. "" when the
	// key is bound to nothing but itself.
	boundSubject string
}

// Keys is the control plane's principal list: the ctl API keys and the bearer
// subjects that are enrolled, with their roles. Immutable after Load; a
// rotation is a daemon restart (the "configured vs effective" distinction the
// credentials CLI reports).
type Keys struct {
	entries []keyEntry
	// subjects maps a bearer principal id ("bvbrc:<un>") to its role. Bearer
	// identities get a role ONLY from here — nothing in a token is an input.
	subjects map[string]string
}

// Environment variables Keys reads. Documented in one place in api/env.go.
const (
	EnvAPIKeys          = "CTL_API_KEYS"
	EnvAPIKeyRoles      = "CTL_API_KEY_ROLES"
	EnvAPIKeyNames      = "CTL_API_KEY_NAMES"
	EnvAPIKeyPrincipals = "CTL_API_KEY_PRINCIPALS"
	EnvAdminSubjects    = "CTL_ADMIN_SUBJECTS"
	EnvViewerSubjects   = "CTL_VIEWER_SUBJECTS"
)

// LoadKeysFromEnv reads the principal list from the process environment.
//
//	CTL_API_KEYS       JSON list (or a bare comma list) of ctl API keys
//	CTL_API_KEY_ROLES  JSON object key → "operator" | "viewer"
//	CTL_API_KEY_NAMES  JSON object key → label (optional; the fingerprint
//	                   stands in when a key has no label)
//	CTL_API_KEY_PRINCIPALS
//	                   JSON object key → the identity principal the key BELONGS
//	                   to ("bvbrc:alice@patricbrc.org"). Optional. It is what
//	                   lets a browser that authenticated as that identity
//	                   authorize a mutation by re-presenting this key in the
//	                   body: without it, a bearer or a session minted from a
//	                   bearer can never satisfy the body-key rule, because the
//	                   key's own principal id is "key:<label>" and can never
//	                   equal "bvbrc:<un>".
//	CTL_ADMIN_SUBJECTS  operator bearer subjects, comma or JSON list
//	CTL_VIEWER_SUBJECTS viewer bearer subjects, comma or JSON list
//
// A key listed in CTL_API_KEYS with no role in CTL_API_KEY_ROLES authenticates
// and is then 403: enrolment and authorization are two decisions, and the
// second one is never implied by the first.
func LoadKeysFromEnv() (*Keys, error) { return LoadKeys(os.Getenv) }

// LoadKeys is LoadKeysFromEnv with the lookup injected (tests, and the CLI's
// --direct path, must not have to mutate the process environment).
func LoadKeys(getenv func(string) string) (*Keys, error) {
	values, err := splitListEnv(EnvAPIKeys, getenv(EnvAPIKeys))
	if err != nil {
		return nil, err
	}
	roles, err := splitMapEnv(EnvAPIKeyRoles, getenv(EnvAPIKeyRoles))
	if err != nil {
		return nil, err
	}
	names, err := splitMapEnv(EnvAPIKeyNames, getenv(EnvAPIKeyNames))
	if err != nil {
		return nil, err
	}
	principals, err := splitMapEnv(EnvAPIKeyPrincipals, getenv(EnvAPIKeyPrincipals))
	if err != nil {
		return nil, err
	}
	k := &Keys{subjects: map[string]string{}}
	for _, v := range values {
		role := roles[v]
		if role != "" && role != RoleViewer && role != RoleOperator {
			return nil, fmt.Errorf("%s: role %q is neither %q nor %q", EnvAPIKeyRoles, role, RoleViewer, RoleOperator)
		}
		fp := Fingerprint(v)
		label := names[v]
		if label == "" {
			label = fp
		}
		bound := strings.TrimSpace(principals[v])
		if bound != "" && !strings.Contains(bound, ":") {
			// A binding that is not an `issuer:subject` id could never match a
			// resolved principal, so it would silently be a binding to nobody.
			return nil, fmt.Errorf("%s: %q is not an issuer:subject principal id", EnvAPIKeyPrincipals, bound)
		}
		k.entries = append(k.entries, keyEntry{
			value: v, label: label, role: role, fingerprint: fp, boundSubject: bound,
		})
	}
	for _, env := range [...]struct {
		name string
		role string
	}{{EnvAdminSubjects, RoleOperator}, {EnvViewerSubjects, RoleViewer}} {
		subjects, err := splitListEnv(env.name, getenv(env.name))
		if err != nil {
			return nil, err
		}
		for _, s := range subjects {
			if !strings.Contains(s, ":") {
				// A principal id is "<issuer>:<subject>", and that is what a
				// resolved bearer identity is compared against — so a bare
				// "alice" enrols NOBODY. It looked like an enrolment, the
				// daemon started, and the operator it was written for was 403
				// with the principal list apparently naming them. Refuse to
				// start, exactly as CTL_API_KEY_PRINCIPALS does.
				return nil, fmt.Errorf("%s: %q is not an issuer:subject principal id (e.g. %q)", env.name, s, "bvbrc:"+s)
			}
			// An operator listing wins over a viewer listing for the same
			// subject only if it is read second; make the order explicit
			// instead: admin first, and a viewer row never demotes it.
			if existing, ok := k.subjects[s]; ok && existing == RoleOperator {
				continue
			}
			k.subjects[s] = env.role
		}
	}
	return k, nil
}

// Len reports how many ctl keys are enrolled (for doctor and startup logging;
// never the values).
func (k *Keys) Len() int { return len(k.entries) }

// Subjects returns the enrolled bearer principal ids, sorted. For doctor and
// startup logging: these are user names, not credentials.
func (k *Keys) Subjects() []string {
	out := make([]string, 0, len(k.subjects))
	for s := range k.subjects {
		out = append(out, s)
	}
	sort.Strings(out)
	return out
}

// ErrUnlisted is returned when a credential VERIFIED and the answer is still
// no, because its subject is not on the principal list. It is the 403 the
// contract singles out as its own case, distinct from the 401 of an unusable
// credential. Compared with errors.Is by the middleware.
type ErrUnlisted struct{ Subject string }

func (e *ErrUnlisted) Error() string {
	return fmt.Sprintf("subject %s is not on the control plane's principal list", e.Subject)
}

// ErrUnknownKey is returned when the presented key is not enrolled at all —
// indistinguishable, to the caller, from a malformed one: both are 401.
var ErrUnknownKey = &IdentityError{Kind: ErrInvalid, Reason: "unknown or revoked ctl API key"}

// LookupKey resolves a presented ctl API key.
//
// The comparison is constant-time against EVERY enrolled key, with no early
// exit on a match: a timing difference between "wrong at byte 1" and "wrong at
// byte 63" is a byte-at-a-time oracle for a credential that is otherwise only
// guessable at 2^256.
func (k *Keys) LookupKey(key string) (Principal, error) {
	if key == "" {
		return Principal{}, ErrUnknownKey
	}
	match := -1
	for i := range k.entries {
		if subtle.ConstantTimeCompare([]byte(key), []byte(k.entries[i].value)) == 1 {
			match = i
		}
	}
	if match < 0 {
		return Principal{}, ErrUnknownKey
	}
	e := k.entries[match]
	p := Principal{
		Subject:        "key:" + e.label,
		Role:           e.role,
		AuthMethod:     MethodAPIKey,
		KeyFingerprint: e.fingerprint,
		// A ctl key does not expire; it is revoked. me_response.json says so
		// by requiring `expires_at: null` for one.
		ExpiresAt:    nil,
		BoundSubject: e.boundSubject,
	}
	if e.role == "" {
		return p, &ErrUnlisted{Subject: p.Subject}
	}
	return p, nil
}

// RoleForSubject resolves a bearer principal id to its role, or ErrUnlisted.
func (k *Keys) RoleForSubject(principal string) (string, error) {
	role, ok := k.subjects[principal]
	if !ok || role == "" {
		return "", &ErrUnlisted{Subject: principal}
	}
	return role, nil
}

// PrincipalForIdentity turns a verified BV-BRC identity into a Principal, or
// ErrUnlisted when the subject is not enrolled. The principal id is
// "bvbrc:<un>"; the role comes from the ctl's own lists and from nowhere else.
func (k *Keys) PrincipalForIdentity(id Identity) (Principal, error) {
	subject := id.Issuer + ":" + id.Subject
	p := Principal{Subject: subject, AuthMethod: MethodBearer, ExpiresAt: id.ExpiresAt}
	role, err := k.RoleForSubject(subject)
	if err != nil {
		return p, err
	}
	p.Role = role
	return p, nil
}

// splitListEnv parses a list env var from either a JSON array or a bare
// comma-separated string — the same rule as the tenant API's
// `_split_list_env` (python/ragstack/config.py), so an operator writing
// CTL_ADMIN_SUBJECTS does not have to remember which service reads it.
func splitListEnv(name, value string) ([]string, error) {
	value = strings.TrimSpace(value)
	if value == "" {
		return nil, nil
	}
	if strings.HasPrefix(value, "[") {
		var out []string
		if err := json.Unmarshal([]byte(value), &out); err != nil {
			return nil, fmt.Errorf("%s: not a JSON list of strings: %w", name, err)
		}
		return out, nil
	}
	var out []string
	for _, item := range strings.Split(value, ",") {
		if item = strings.TrimSpace(item); item != "" {
			out = append(out, item)
		}
	}
	return out, nil
}

// splitMapEnv parses a JSON object env var. Empty ⇒ empty map.
func splitMapEnv(name, value string) (map[string]string, error) {
	value = strings.TrimSpace(value)
	if value == "" {
		return map[string]string{}, nil
	}
	var out map[string]string
	if err := json.Unmarshal([]byte(value), &out); err != nil {
		return nil, fmt.Errorf("%s: not a JSON object of string→string: %w", name, err)
	}
	return out, nil
}

// SplitListEnv is splitListEnv exported for the daemon's own CTL_* parsing
// (the issuer allowlist), so there is one implementation of "JSON list or bare
// comma list" in the control plane rather than two that drift.
func SplitListEnv(name, value string) ([]string, error) { return splitListEnv(name, value) }
