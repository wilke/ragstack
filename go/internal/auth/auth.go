// Package auth resolves the calling principal from an API key.
//
// The Go side was a Phase-1 scaffold with NO authentication of any kind: every
// /v1 route answered any caller as the same anonymous identity. That is fine
// for a stub pipeline and impossible for `/v1/grading`, whose entire purpose is
// that reader A cannot see reader B's verdict — a surface with no principal has
// no independence to enforce, and `conformance/test_grading.py` needs four
// distinct ones.
//
// So this implements the API-key half of ADR-0003, from the SAME environment
// variables the Python implementation reads (`API_KEYS`, `API_KEY_TENANTS`,
// `API_KEY_ROLES`, `DEFAULT_ROLE`) and with the same semantics, so
// `conformance/run_authz_keyed.sh`'s provisioning works unchanged against
// either server. The bearer-identity scheme (BV-BRC / OIDC) is NOT implemented
// here; `Authorization` is ignored, exactly as it is on a Python server with
// `IDENTITY_PROVIDER=none` (the default).
//
// KEYLESS IS UNCHANGED BEHAVIOUR. With no `API_KEYS` configured — every
// existing Go invocation, including `make run-go` and `make
// test-conformance-go` — the caller is the `default` tenant with `DEFAULT_ROLE`
// and nothing is rejected. Adding this package does not close a door that was
// open yesterday; it opens one that could not be closed at all.
package auth

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"log/slog"
	"net/http"
	"os"
	"strings"
)

// Roles, per ADR-0003. `researcher` is a deprecated alias for `user`.
const (
	RoleAdmin      = "admin"
	RoleUser       = "user"
	roleResearcher = "researcher"
)

// DefaultTenant is the tenant a keyless caller — and a valid key with no
// mapping — resolves to (`ragstack.tenancy.DEFAULT_TENANT`).
const DefaultTenant = "default"

// Principal is the authenticated caller: its data tenant (which is also its
// SUBJECT — what a grading batch's `readers` names) and its RBAC role.
type Principal struct {
	Tenant string
	Role   string
}

// IsAdmin reports the `admin` role — the one `/v1/config` and `/v1/admin/*`
// admit, and the one a grading batch's administration is gated on.
func (p Principal) IsAdmin() bool { return p.Role == RoleAdmin }

// Authenticator maps an API key to a principal.
type Authenticator struct {
	keys        []string
	tenants     map[string]string
	roles       map[string]string
	defaultRole string
}

// Enabled reports whether any key is configured. False is the open dev/test
// path.
func (a *Authenticator) Enabled() bool { return a != nil && len(a.keys) > 0 }

// LoadFromEnv reads the API-key configuration from the environment.
func LoadFromEnv() *Authenticator {
	return &Authenticator{
		keys:        parseList(os.Getenv("API_KEYS")),
		tenants:     parseMap(os.Getenv("API_KEY_TENANTS")),
		roles:       parseMap(os.Getenv("API_KEY_ROLES")),
		defaultRole: normalizeRole(envOr("DEFAULT_ROLE", RoleUser)),
	}
}

// New builds an Authenticator explicitly — for tests, which must not have to
// mutate process environment to get a principal.
func New(keys []string, tenants, roles map[string]string, defaultRole string) *Authenticator {
	if defaultRole == "" {
		defaultRole = RoleUser
	}
	return &Authenticator{
		keys:        keys,
		tenants:     tenants,
		roles:       roles,
		defaultRole: normalizeRole(defaultRole),
	}
}

// Resolve authenticates apiKey. `ok` false means 401.
//
// The comparison runs over EVERY configured key with no short-circuit, so the
// time taken does not reveal which key matched or how far down the list it sat
// — the same property `secrets.compare_digest` in a `sum()` gives the Python
// path.
func (a *Authenticator) Resolve(apiKey string) (Principal, bool) {
	if !a.Enabled() {
		return Principal{Tenant: DefaultTenant, Role: a.keylessRole()}, true
	}
	matched := 0
	var hit string
	for _, k := range a.keys {
		if subtle.ConstantTimeCompare([]byte(apiKey), []byte(k)) == 1 {
			matched++
			hit = k
		}
	}
	if matched == 0 {
		return Principal{}, false
	}
	tenant, ok := a.tenants[hit]
	if !ok || tenant == "" {
		tenant = DefaultTenant
	}
	role, ok := a.roles[hit]
	if !ok || role == "" {
		role = a.defaultRole
	}
	return Principal{Tenant: tenant, Role: normalizeRole(role)}, true
}

func (a *Authenticator) keylessRole() string {
	if a == nil || a.defaultRole == "" {
		return RoleUser
	}
	return a.defaultRole
}

type ctxKey struct{}

// WithPrincipal stores p on the context.
func WithPrincipal(ctx context.Context, p Principal) context.Context {
	return context.WithValue(ctx, ctxKey{}, p)
}

// FromContext returns the request's principal. A request that reached a handler
// without passing the middleware resolves to the keyless identity rather than a
// zero value, so a direct unit-test call to a handler behaves like the open dev
// path instead of like an unnamed subject.
func FromContext(ctx context.Context) Principal {
	if p, ok := ctx.Value(ctxKey{}).(Principal); ok {
		return p
	}
	return Principal{Tenant: DefaultTenant, Role: RoleUser}
}

// Middleware authenticates every request and stores the principal on the
// context. On a keyless server it authenticates nothing and rejects nothing.
//
// unauthorized is called instead of writing the 401 directly so the error body
// keeps the api package's `{detail, request_id}` shape without this package
// importing it.
func (a *Authenticator) Middleware(unauthorized func(http.ResponseWriter, *http.Request)) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if !a.Enabled() {
				next.ServeHTTP(w, r)
				return
			}
			p, ok := a.Resolve(r.Header.Get("X-API-Key"))
			if !ok {
				unauthorized(w, r)
				return
			}
			next.ServeHTTP(w, r.WithContext(WithPrincipal(r.Context(), p)))
		})
	}
}

// normalizeRole maps the deprecated `researcher` alias to `user` (ADR-0003), so
// a config written against the old vocabulary keeps working on both servers.
func normalizeRole(role string) string {
	if role == roleResearcher {
		slog.Warn("role 'researcher' is a deprecated alias for 'user' (ADR-0003); update DEFAULT_ROLE / API_KEY_ROLES")
		return RoleUser
	}
	return role
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

// parseList reads `API_KEYS`, accepting the JSON array pydantic-settings parses
// on the Python side (`["k1","k2"]`, which is what run_authz_keyed.sh writes)
// and a plain comma-separated list for hand invocation.
func parseList(raw string) []string {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return nil
	}
	if strings.HasPrefix(raw, "[") {
		var out []string
		if err := json.Unmarshal([]byte(raw), &out); err == nil {
			return nonEmpty(out)
		}
		slog.Warn("API_KEYS looks like JSON but did not parse; ignoring it — this server is KEYLESS")
		return nil
	}
	return nonEmpty(strings.Split(raw, ","))
}

// parseMap reads `API_KEY_TENANTS` / `API_KEY_ROLES` as a JSON object, or as
// `k=v,k=v` for hand invocation.
func parseMap(raw string) map[string]string {
	raw = strings.TrimSpace(raw)
	out := map[string]string{}
	if raw == "" {
		return out
	}
	if strings.HasPrefix(raw, "{") {
		if err := json.Unmarshal([]byte(raw), &out); err != nil {
			slog.Warn("an API_KEY_* map looks like JSON but did not parse; ignoring it")
			return map[string]string{}
		}
		return out
	}
	for _, pair := range strings.Split(raw, ",") {
		k, v, found := strings.Cut(strings.TrimSpace(pair), "=")
		if found && strings.TrimSpace(k) != "" {
			out[strings.TrimSpace(k)] = strings.TrimSpace(v)
		}
	}
	return out
}

func nonEmpty(in []string) []string {
	out := make([]string, 0, len(in))
	for _, s := range in {
		if s = strings.TrimSpace(s); s != "" {
			out = append(out, s)
		}
	}
	if len(out) == 0 {
		return nil
	}
	return out
}
