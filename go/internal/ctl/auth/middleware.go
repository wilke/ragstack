package auth

import (
	"context"
	"errors"
	"net/http"
	"strconv"
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/ratelimit"
)

// Error codes this package emits, from contracts/ctl/schemas/error.json. The
// status for each is fixed by the contract, so the code alone reconstructs it.
const (
	CodeAuthRequired     = "auth_required"
	CodeForbidden        = "forbidden"
	CodeBothCredentials  = "both_credentials"
	CodeRateLimited      = "rate_limited"
	HeaderAPIKey         = "X-API-Key"
	HeaderAuthorization  = "Authorization"
	schemeSessionPrefix  = "Session "
	schemeBearerPrefix   = "Bearer "
	sessionsAreReadsOnly = "sessions are read-only; re-present a ctl API key"
)

// AnonBucket is the rate-limiter key every CREDENTIAL-LESS failure is counted
// under.
//
// A request with no credential at all hashes to "" — there is nothing to hash
// — and Allow("") is always true, so a caller who simply sends no header could
// produce unbounded 401s: tarpitted, but never given a budget and never told
// to come back later. One fixed bucket gives that traffic the same per-window
// budget every credential has, and the 429 that goes with it. It is a literal,
// not a hash, so it cannot collide with the 64-hex key of a real credential.
//
// It is consulted ONLY inside Middleware, never in RateLimitMiddleware: the
// rate-limit middleware also runs for GET /health, the one anonymous
// operation, and keying that on the anon bucket would let a flood of
// credential-less 401s elsewhere turn the public health probe into a 429.
const AnonBucket = "anon"

// identityProviderUnavailable is the detail of the 401 a caller gets when the
// KEY SERVER — not the credential — is what could not answer. error.json fixes
// one status per code and has no code that maps to 503, so the status stays
// the 401 of "no usable credential" and the detail carries the distinction.
const identityProviderUnavailable = "identity provider unavailable; the presented credential could not be verified"

// SessionResolver resolves an opaque session id to the principal it
// authenticates as. *session.MemoryStore satisfies it; the interface lives
// here so package session may import auth and not the other way round.
type SessionResolver interface {
	Principal(id string) (Principal, error)
}

// Rejecter writes one contract error body. The auth middleware cannot write it
// itself: the body shape (error.json, with the request id) belongs to the API
// package, and having auth import api would invert the dependency. The api
// package passes its own writer in.
type Rejecter func(w http.ResponseWriter, r *http.Request, status int, code, detail string, extra map[string]any)

// Resolver turns a request's headers into a Principal. One per daemon.
type Resolver struct {
	// Keys is the principal list: enrolled ctl keys and bearer subjects.
	Keys *Keys
	// Verifier verifies BV-BRC bearer tokens. nil ⇒ bearer credentials are
	// refused with 401 (a daemon configured with no identity provider).
	Verifier *Verifier
	// Sessions resolves `Authorization: Session <id>`. nil ⇒ sessions refused.
	Sessions SessionResolver
	// Limiter counts credential failures. nil ⇒ no limiting (the --direct CLI
	// path, where the credential is the OS identity).
	Limiter *ratelimit.Limiter
	// Reject writes the error body. Required by Middleware.
	Reject Rejecter
}

type ctxKey int

const principalKey ctxKey = iota

// PrincipalFromContext returns the resolved caller, or false outside an
// authenticated request. Every caller must handle false: handlers are called
// directly by unit tests and by the CLI.
func PrincipalFromContext(ctx context.Context) (Principal, bool) {
	p, ok := ctx.Value(principalKey).(Principal)
	return p, ok
}

// WithPrincipal returns ctx carrying p. Exported for tests and for the
// --direct CLI, which resolves a local principal without an HTTP request.
func WithPrincipal(ctx context.Context, p Principal) context.Context {
	return context.WithValue(ctx, principalKey, p)
}

// Credential is what a request presented, before any of it is verified.
type Credential struct {
	// Kind is "none", "api_key", "session", "bearer" or "both".
	Kind string
	// Value is the api key, the session id or the bearer token.
	Value string
	// Hash is sha256 of the presented credential — the rate limiter's key.
	// Empty when nothing was presented.
	Hash string
}

// ReadCredential classifies a request's credentials WITHOUT verifying any of
// them.
//
// The "both" case is decided here, first, and is a 400 rather than a
// preference order: which credential authenticated must never be ambiguous,
// and a server that silently picks one is a server where revoking the other
// changes nothing. It is refused before either is verified, so the answer is
// the same whether the bearer is real or garbage.
//
// A bare `Authorization` value with no recognised scheme is treated as a
// BV-BRC token: the wire format carries no scheme, and BV-BRC clients send it
// unadorned. That costs nothing — an unparseable value fails verification and
// lands on the same 401 as any other garbage.
func ReadCredential(r *http.Request) Credential {
	apiKey := strings.TrimSpace(r.Header.Get(HeaderAPIKey))
	authorization := strings.TrimSpace(r.Header.Get(HeaderAuthorization))
	switch {
	case apiKey != "" && authorization != "":
		return Credential{Kind: "both"}
	case apiKey != "":
		return credential("api_key", apiKey)
	case authorization == "":
		return Credential{Kind: "none"}
	}
	if rest, ok := cutScheme(authorization, schemeSessionPrefix); ok {
		return credential("session", rest)
	}
	if rest, ok := cutScheme(authorization, schemeBearerPrefix); ok {
		return credential("bearer", rest)
	}
	return credential("bearer", authorization)
}

// credential builds one Credential, hashing the EXTRACTED value rather than
// the raw header line.
//
// Hashing the raw `Authorization` value would give `Bearer t`, `bearer t` and
// `Bearer  t` three separate failure budgets for ONE token — three free
// guessing budgets, granted by nothing but case and spacing the scheme parser
// already ignores. The kind is mixed in so a session id and an API key that
// happened to be the same string could not share a budget either.
func credential(kind, value string) Credential {
	return Credential{Kind: kind, Value: value, Hash: CredentialHashFor(kind, value)}
}

func cutScheme(value, prefix string) (string, bool) {
	if len(value) >= len(prefix) && strings.EqualFold(value[:len(prefix)], prefix) {
		return strings.TrimSpace(value[len(prefix):]), true
	}
	return "", false
}

// Resolve verifies a credential and returns the principal.
//
// The two failure shapes are distinct and both are load-bearing:
//
//	an *IdentityError wrapping ErrInvalid / ErrUnavailable → 401, because
//	    nothing usable was presented (or we could not tell);
//	an *ErrUnlisted → 403, because something VERIFIED and the answer is still
//	    no. There is no default role, and conflating the two would mean either
//	    leaking that a subject exists (401→403) or hiding that enrolment is
//	    the missing step (403→401).
func (a *Resolver) Resolve(ctx context.Context, c Credential) (Principal, error) {
	switch c.Kind {
	case "api_key":
		return a.Keys.LookupKey(c.Value)
	case "session":
		if a.Sessions == nil {
			return Principal{}, invalid("sessions are not enabled on this daemon")
		}
		p, err := a.Sessions.Principal(c.Value)
		if err != nil {
			// Unknown, expired and revoked are ONE answer: the caller learns
			// nothing from the difference.
			return Principal{}, invalid("unknown or expired session")
		}
		if p.Role == "" {
			// The subject's enrolment was withdrawn after the session was
			// minted. The session is not a grandfathered role.
			return p, &ErrUnlisted{Subject: p.Subject}
		}
		return p, nil
	case "bearer":
		if a.Verifier == nil {
			return Principal{}, invalid("no identity provider is configured")
		}
		id, err := a.Verifier.Authenticate(ctx, c.Value)
		if err != nil {
			return Principal{}, err
		}
		return a.Keys.PrincipalForIdentity(id)
	default:
		return Principal{}, invalid("no credential")
	}
}

// RateLimitMiddleware answers 429 for a credential that has failed too often
// inside the window. Install it OUTSIDE Middleware: a credential over its
// budget must not reach the verifier at all, which is the point.
func (a *Resolver) RateLimitMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if a.Limiter != nil {
			// The PRESENTED credential's hash only — never the anon bucket:
			// this middleware also runs for the one anonymous operation, and
			// keying credential-less traffic here would let failures
			// elsewhere turn GET /health into a 429.
			if ok, retryAfter := a.Limiter.Allow(ReadCredential(r).Hash); !ok {
				a.rejectRateLimited(w, r, retryAfter,
					"too many failed authentication attempts for this credential; retry after ")
				return
			}
		}
		next.ServeHTTP(w, r)
	})
}

// Middleware resolves the principal and puts it in the request context, or
// writes the contract's 400/401/403.
//
// skip reports the requests that need no credential at all (GET /health). It
// is a predicate rather than a path list so the router keeps one source of
// truth for which operation is anonymous — the generated authz matrix.
func (a *Resolver) Middleware(skip func(*http.Request) bool) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if skip != nil && skip(r) {
				next.ServeHTTP(w, r)
				return
			}
			c := ReadCredential(r)
			switch c.Kind {
			case "both":
				a.fail(w, r, c, http.StatusBadRequest, CodeBothCredentials,
					"X-API-Key and Authorization were both present; which credential authenticated must never be ambiguous")
				return
			case "none":
				// Credential-less failures are budgeted under one fixed
				// bucket; over it, this is a 429 like any other.
				if !a.allowAnon(w, r) {
					return
				}
				a.fail(w, r, c, http.StatusUnauthorized, CodeAuthRequired,
					"this operation requires a ctl API key, a BV-BRC token or a session")
				return
			}
			p, err := a.Resolve(r.Context(), c)
			if err != nil {
				var unlisted *ErrUnlisted
				if errors.As(err, &unlisted) {
					a.fail(w, r, c, http.StatusForbidden, CodeForbidden,
						"credential verified, but "+unlisted.Subject+" is not on the control plane's principal list; there is no default role")
					return
				}
				if errors.Is(err, ErrUnavailable) {
					// The key server did not answer. NOTHING about the
					// credential was learned, so counting this as a credential
					// failure punished the caller for the identity provider's
					// outage: an operator retrying during one would burn their
					// own budget to a 429 and be tarpitted on every attempt,
					// turning a provider outage into a lockout that outlives
					// it. Refused, not counted, not delayed.
					a.Reject(w, r, http.StatusUnauthorized, CodeAuthRequired, identityProviderUnavailable, nil)
					return
				}
				a.fail(w, r, c, http.StatusUnauthorized, CodeAuthRequired,
					"the presented credential is missing, malformed, unknown or expired")
				return
			}
			next.ServeHTTP(w, r.WithContext(WithPrincipal(r.Context(), p)))
		})
	}
}

// fail records the credential failure, serves the global tarpit if it is on,
// and writes the error. Only AUTHENTICATION failures pass through here: a role
// refusal for a good credential is written by the authz guard and is not
// counted, or a viewer clicking around the UI would lock their own key out.
func (a *Resolver) fail(w http.ResponseWriter, r *http.Request, c Credential, status int, code, detail string) {
	if a.Limiter != nil {
		a.Limiter.Fail(bucketFor(c))
		// Delay FAILURES only. A legitimate operator's successful request is
		// never held, which is what makes a tarpit acceptable here. The delay
		// is bounded by the request context: a client that hung up, and a
		// daemon that is shutting down, both end it immediately.
		a.Limiter.Tarpit(r.Context())
	}
	a.Reject(w, r, status, code, detail, nil)
}

// bucketFor is the limiter key one credential's failures are counted under:
// its hash, or the shared anon bucket when nothing was presented.
func bucketFor(c Credential) string {
	if c.Hash == "" && c.Kind == "none" {
		return AnonBucket
	}
	return c.Hash
}

// allowAnon applies the anon bucket's budget to a credential-less request. It
// writes the contract's 429 — the same body and Retry-After the per-credential
// limiter writes — and reports false when it did.
func (a *Resolver) allowAnon(w http.ResponseWriter, r *http.Request) bool {
	if a.Limiter == nil {
		return true
	}
	ok, retryAfter := a.Limiter.Allow(AnonBucket)
	if ok {
		return true
	}
	a.rejectRateLimited(w, r, retryAfter,
		"too many failed authentication attempts without a credential; retry after ")
	return false
}

// rejectRateLimited writes the 429 both budgets share.
func (a *Resolver) rejectRateLimited(w http.ResponseWriter, r *http.Request, retryAfter int, detail string) {
	w.Header().Set("Retry-After", strconv.Itoa(retryAfter))
	a.Reject(w, r, http.StatusTooManyRequests, CodeRateLimited,
		detail+strconv.Itoa(retryAfter)+"s",
		map[string]any{"retry_after": retryAfter})
}

// SessionsAreReadsOnlyDetail is the refusal a mutating route gives a session
// credential. Exported so the authz guard and the admin UI quote one string.
const SessionsAreReadsOnlyDetail = sessionsAreReadsOnly
