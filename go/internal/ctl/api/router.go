// Package api is the control plane's HTTP surface: the chi router, the
// handlers of the read operations, and the serve command that binds them.
//
// # The middleware order is the security model
//
//	request-id  → stamped on the response header FIRST, so it survives onto
//	              chi's 404, onto a recovered panic's 500 and onto every
//	              handler-written body alike ("every response carries
//	              X-Request-Id" is then true without an exception handler).
//	logging     → inside request-id so it can read the id; outside recover so
//	              a panic still produces a line with its 500.
//	recover     → a panic becomes a 500 `internal`, never a dropped connection.
//	rate limit  → a credential over its failure budget is answered 429 before
//	              the verifier ever sees it.
//	auth        → resolves the principal, or writes 400/401/403.
//	authz       → per route, inside the handler wrapper (see route()): the role
//	              gate runs BEFORE any path parameter is resolved, so a viewer
//	              probing an operator-only route gets 403 whether or not the
//	              tenant exists.
//
// authz is a per-route wrapper rather than an `r.Use` middleware for a
// mechanical reason: chi populates RouteContext.RoutePattern() during routing,
// which happens AFTER the r.Use chain runs, so a middleware there cannot know
// which operation it is gating. Wrapping each handler with its matrix row is
// the version that cannot silently gate the wrong route — and route() refuses
// to register a path the contract does not list at all.
package api

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/ragstack/ragstack/internal/ctl/auth"
	"github.com/ragstack/ragstack/internal/ctl/authz"
	"github.com/ragstack/ragstack/internal/ctl/doctor"
	"github.com/ragstack/ragstack/internal/ctl/fleet"
	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/ctl/paths"
	"github.com/ragstack/ragstack/internal/ctl/registry"
	"github.com/ragstack/ragstack/internal/ctl/render"
	"github.com/ragstack/ragstack/internal/ctl/session"
	"github.com/ragstack/ragstack/internal/ctl/version"
	"github.com/ragstack/ragstack/internal/observability"
)

// tenantNameRE is components/parameters/TenantName. A name outside it is a 422
// `validation` and never reaches the registry: it is not a lookup miss, it is
// a value that could not name anything.
var tenantNameRE = regexp.MustCompile(`^[a-z][a-z0-9-]{0,31}$`)

// jobIDRE is components/parameters/JobId (a ULID).
var jobIDRE = regexp.MustCompile(`^[0-7][0-9A-HJKMNP-TV-Z]{25}$`)

// logFiles is the `file` query enum of GET /v1/tenants/{name}/logs.
var logFiles = map[string]bool{"api": true, "qdrant": true, "es": true, "ui": true}

// maxBodyBytes bounds a request body. Nothing this API accepts is large, and
// an unbounded reader on a publicly mounted surface is a memory lever.
const maxBodyBytes = 1 << 20

// mutationsArriveInPRC is the detail of every mutating route's refusal. The
// contract's own wording for a verb that is not implemented yet is 409
// `refused` "with `detail` saying so" (see the ctlTenantOp description on
// `update-code`), so that is the status and code used here rather than a 501:
// error.json promises one status per code, and a 501 would make the code
// unreconstructable from the status for every logger downstream.
const mutationsArriveInPRC = "mutations arrive in PR-C: this daemon serves the read surface only"

// Server is the daemon's handler state.
type Server struct {
	Backend  Backend
	Resolver *auth.Resolver
	Sessions session.Store
	Logger   *slog.Logger
}

// log returns the server's logger, which is the REDACTING one built by
// newLogger. Every line this package writes must go through it: slog.Default()
// has no ReplaceAttr, so a package-level slog.Error is a hole straight past
// the redactor — and the things being logged there are a panic value and a
// backend error, which are exactly where a credential or a `|sig=` ends up.
func (s *Server) log() *slog.Logger {
	if s.Logger != nil {
		return s.Logger
	}
	return slog.Default()
}

// NewRouter builds the control-plane handler.
func NewRouter(s *Server) http.Handler {
	logger := s.log()
	assertAnonymousRowsArePlainPaths()
	r := chi.NewRouter()
	r.NotFound(notFound)
	r.MethodNotAllowed(methodNotAllowed)

	r.Use(observability.RequestIDMiddleware)
	r.Use(observability.LoggingMiddleware(logger))
	r.Use(recoverer(logger))
	r.Use(s.Resolver.RateLimitMiddleware)
	// The one anonymous operation is named by the generated matrix, not by a
	// string here, so "which routes need no credential" has a single source.
	r.Use(s.Resolver.Middleware(func(req *http.Request) bool {
		return authz.IsAnonymous(req.Method, req.URL.Path)
	}))

	// ---- reads ----------------------------------------------------------
	s.route(r, http.MethodGet, "/health", s.handleHealth)
	s.route(r, http.MethodGet, "/v1/version", s.handleVersion)
	s.route(r, http.MethodGet, "/v1/me", s.handleMe)
	s.route(r, http.MethodPost, "/v1/session", s.handleSessionCreate)
	s.route(r, http.MethodDelete, "/v1/session", s.handleSessionRevoke)
	s.route(r, http.MethodGet, "/v1/fleet", s.handleFleet)
	s.route(r, http.MethodGet, "/v1/tenants", s.handleTenants)
	s.route(r, http.MethodGet, "/v1/tenants/{name}", s.handleTenant)
	s.route(r, http.MethodGet, "/v1/tenants/{name}/env", s.handleEnv)
	s.route(r, http.MethodGet, "/v1/tenants/{name}/logs", s.handleLogs)
	s.route(r, http.MethodGet, "/v1/doctor", s.handleDoctor)
	s.route(r, http.MethodGet, "/v1/gateway", s.handleGatewayStatus)
	s.route(r, http.MethodPost, "/v1/gateway/render", s.handleGatewayRender)
	s.route(r, http.MethodGet, "/v1/settings", s.handleSettings)
	s.route(r, http.MethodGet, "/v1/audit", s.handleAudit)
	s.route(r, http.MethodGet, "/v1/jobs", s.handleJobs)
	s.route(r, http.MethodGet, "/v1/jobs/{id}", s.handleJob)
	s.route(r, http.MethodGet, "/v1/jobs/{id}/steps/{n}/log", s.handleJobStepLog)
	s.route(r, http.MethodGet, "/v1/jobs/{id}/secrets", s.handleJobSecrets)

	// ---- mutations, refused --------------------------------------------
	// Registered, not omitted: an unregistered route would answer 404, and the
	// conformance matrix asserts that a viewer hitting an operator mutation
	// gets 403 — the authorization answer, before anything is looked up. The
	// refusal is what a caller sees only once it IS authorized.
	//
	// Derived from the matrix's `Mutating` rows rather than listed here. A
	// hand-kept list is a second place a mutation can be added to and, when it
	// is forgotten there, the route 404s instead of answering the contract's
	// 409 — and the guard's "a session may not mutate" rule, which keys off
	// the same flag, never runs for it.
	for _, row := range authz.Matrix {
		if row.Mutating {
			s.route(r, row.Method, row.Path, s.handleMutationRefused)
		}
	}
	return r
}

// assertAnonymousRowsArePlainPaths fires at start-up, like route()'s panic.
//
// The credential skip is `authz.IsAnonymous(method, r.URL.Path)`: a RAW
// request path matched against the matrix's PATTERNS. That is exact only while
// every anonymous row is a literal path — the moment one carries a `{name}`
// segment, the pattern stops matching the request path and the route silently
// starts requiring a credential (harmless), or a future looser matcher makes
// the opposite mistake (not harmless). The assertion is what keeps "anonymous
// rows are literal" from being an unwritten assumption.
func assertAnonymousRowsArePlainPaths() {
	assertAnonymousRowsArePlainPathsIn(authz.Matrix)
}

func assertAnonymousRowsArePlainPathsIn(rows []authz.Row) {
	for _, row := range rows {
		if row.Role != "anonymous" {
			continue
		}
		if strings.ContainsAny(row.Path, "{}") {
			panic(fmt.Sprintf(
				"api: anonymous operation %s has a templated path %q; the credential skip matches raw request paths against matrix patterns and cannot match one",
				row.OperationID, row.Path))
		}
	}
}

// route registers one contract operation with its authorization row.
//
// It PANICS on a path the matrix does not list. That is deliberate and it
// fires at start-up, not under traffic: deny-by-default means such a route
// would be permanently 403, and a route nobody can call is a bug that must be
// loud rather than a surface that quietly does nothing.
func (s *Server) route(r chi.Router, method, pattern string, h http.HandlerFunc) {
	row, ok := authz.Lookup(method, pattern)
	if !ok {
		panic(fmt.Sprintf("api: %s %s has no x-ctl-authorization-matrix row; add it to contracts/ctl/openapi.yaml and regenerate authz/matrix_gen.go", method, pattern))
	}
	r.MethodFunc(method, pattern, s.guard(row, h))
}

// guard is the authorization gate. It runs before the handler body, which is
// what "authorization precedes lookup" means in code.
func (s *Server) guard(row authz.Row, h http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if row.Role == "anonymous" {
			h(w, r)
			return
		}
		p, ok := auth.PrincipalFromContext(r.Context())
		if !ok {
			// Unreachable while the auth middleware is installed; written
			// anyway so a future router that forgets it fails closed.
			writeError(w, r, model.CodeAuthRequired, "this operation requires a credential", nil)
			return
		}
		if p.AuthMethod == auth.MethodSession && !row.Session {
			// Two contract-stated shapes for "not over a session":
			// POST /v1/session says 401 ("no usable credential — including a
			// Session credential, which is not accepted here"), because a
			// session may not mint a session; GET …/secrets says 403, because
			// the caller is known and the answer is still no.
			if row.OperationID == "ctlSessionCreate" {
				writeError(w, r, model.CodeAuthRequired,
					"a session cannot mint a session; present the ctl API key or BV-BRC token itself", nil)
				return
			}
			writeError(w, r, model.CodeForbidden, auth.SessionsAreReadsOnlyDetail, nil)
			return
		}
		if !authz.Allowed(row.Method, row.Path, p.Role) {
			writeError(w, r, model.CodeForbidden,
				fmt.Sprintf("%s requires role %s; %s is a %s", row.OperationID, row.Role, p.Subject, roleOrNone(p.Role)), nil)
			return
		}
		if row.Mutating {
			// The body is read HERE, once, for every mutating row — not in a
			// handler. "A session may not mutate without re-presenting a ctl
			// key" used to live inside handleMutationRefused, which made it a
			// property of one handler rather than of the authorization gate:
			// the first real mutation handler written without that check would
			// have silently let a stolen session id mutate the fleet.
			body, ok := s.readMutationBody(w, r)
			if !ok {
				return
			}
			if !s.mutationCredentialsAgree(w, r, p, body) {
				return
			}
			r = r.WithContext(withMutationBody(r.Context(), body))
		}
		h(w, r)
	}
}

func roleOrNone(role string) string {
	if role == "" {
		return "not enrolled"
	}
	return role
}

// recoverer turns a panic into the contract's 500 `internal`. chi's own
// Recoverer writes a bare text body, which would be the one response in this
// API that is not error.json.
//
// It takes the server's REDACTING logger rather than reaching for
// slog.Default(): a panic value is arbitrary program state — a token, a key, a
// `|sig=` — and the default logger has no redactor attached.
func recoverer(logger *slog.Logger) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			defer func() {
				rec := recover()
				if rec == nil {
					return
				}
				if rec == http.ErrAbortHandler {
					// net/http's own signal for "this response is deliberately
					// abandoned": the server recognises it, logs nothing and
					// closes the connection. Swallowing it here turned an
					// intentional abort into a 200-shaped 500 written onto a
					// connection the handler had already given up on.
					panic(rec)
				}
				logger.Error("panic", "request_id", observability.RequestIDFromContext(r.Context()), "panic", fmt.Sprint(rec))
				writeError(w, r, model.CodeInternal, "the control plane failed to handle this request", nil)
			}()
			next.ServeHTTP(w, r)
		})
	}
}

// isViewer reports whether this request must receive the viewer-reduced shape.
//
// It fails CLOSED: no principal at all is the viewer shape, not the operator
// one. Handlers are called directly by unit tests and by the --direct CLI, so
// "there is always a principal" is a property of one code path and not of the
// function — and the reduction is what keeps registry rows, secret refs and
// key fingerprints out of a response.
func isViewer(r *http.Request) bool {
	p, ok := auth.PrincipalFromContext(r.Context())
	return !ok || p.Role != auth.RoleOperator
}

// --------------------------------------------------------------------------
// Health, version, identity
// --------------------------------------------------------------------------

func (s *Server) handleHealth(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, model.HealthResponse{Status: model.HealthOKStatus, Version: version.Version})
}

func (s *Server) handleVersion(w http.ResponseWriter, _ *http.Request) {
	info := version.Info()
	body := model.VersionResponse{
		Version:       info.Version,
		Commit:        info.Commit,
		BuiltAt:       info.BuiltAt,
		Go:            info.Go,
		SchemaVersion: info.SchemaVersion,
	}
	// The contract pins `commit` to 40 hex or the literal "unknown" and
	// `built_at` to RFC 3339 or "unknown". An unstamped local build has
	// neither, and "" is not one of the two answers the schema allows.
	if body.Commit == "" {
		body.Commit = model.Unknown
	}
	if body.BuiltAt == "" {
		body.BuiltAt = model.Unknown
	}
	writeJSON(w, http.StatusOK, body)
}

func (s *Server) handleMe(w http.ResponseWriter, r *http.Request) {
	p, _ := auth.PrincipalFromContext(r.Context())
	// The one place a client learns its role; never cacheable, and never a
	// place the credential itself appears.
	w.Header().Set("Cache-Control", "no-store")
	writeJSON(w, http.StatusOK, model.MeResponse{
		Principal:  p.Subject,
		Role:       p.Role,
		AuthMethod: p.AuthMethod,
		ExpiresAt:  rfc3339OrNil(p.ExpiresAt),
		// SUDO_USER is a --direct CLI fact. Over HTTP it is always null; a
		// caller-supplied value would be an unauthenticated identity claim.
		SudoUser: nil,
	})
}

func rfc3339OrNil(t *time.Time) *string {
	if t == nil {
		return nil
	}
	s := t.UTC().Format(time.RFC3339)
	return &s
}

func (s *Server) handleSessionCreate(w http.ResponseWriter, r *http.Request) {
	p, _ := auth.PrincipalFromContext(r.Context())
	sess, err := s.Sessions.Create(p)
	if err != nil {
		if errors.Is(err, session.ErrExpiredPrincipal) {
			// Unreachable while the verifier refuses expired tokens, which it
			// does uncached on every request — but the store refuses to mint
			// from a dead credential in its own right, and the answer to that
			// is the 401 of an unusable credential, not a 500.
			writeError(w, r, model.CodeAuthRequired,
				"the presented credential has expired; a session cannot outlive it", nil)
			return
		}
		// A session id that is not unguessable is a credential anyone can
		// mint, so a failed CSPRNG read fails the exchange rather than
		// falling back to something weaker. The caller still holds its
		// original credential.
		writeError(w, r, model.CodeInternal, "could not mint a session", nil)
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	writeJSON(w, http.StatusCreated, model.SessionResponse{
		SessionID: sess.ID,
		Principal: sess.Principal.Subject,
		Role:      sess.Principal.Role,
		ExpiresAt: sess.ExpiresAt.UTC().Format(time.RFC3339),
		ReadsOnly: true,
	})
}

func (s *Server) handleSessionRevoke(w http.ResponseWriter, r *http.Request) {
	p, _ := auth.PrincipalFromContext(r.Context())
	c := auth.ReadCredential(r)
	if c.Kind == "session" {
		s.Sessions.Revoke(c.Value)
	} else {
		// With a key or bearer credential instead: log me out everywhere.
		s.Sessions.RevokeSubject(p.Subject)
	}
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(http.StatusNoContent)
}

// --------------------------------------------------------------------------
// Fleet, tenants, env, logs
// --------------------------------------------------------------------------

func (s *Server) handleFleet(w http.ResponseWriter, r *http.Request) {
	body, err := s.Backend.Fleet(r.Context())
	if err != nil {
		s.backendError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, body)
}

func (s *Server) handleTenants(w http.ResponseWriter, r *http.Request) {
	body, err := s.Backend.Tenants(r.Context())
	if err != nil {
		s.backendError(w, r, err)
		return
	}
	if isViewer(r) {
		// The matrix's reduction for ctlTenantsList: a viewer's rows carry
		// `registry: null`. The row holds secret refs, key fingerprints and
		// the rollback descriptor — operator business.
		for i := range body.Tenants {
			body.Tenants[i].Registry = nil
		}
	}
	writeJSON(w, http.StatusOK, body)
}

// tenantName validates and returns the {name} path parameter, or writes the
// 422 and returns false. Called INSIDE the authorized handler: a viewer
// probing an operator route never reaches it.
func tenantName(w http.ResponseWriter, r *http.Request) (string, bool) {
	name := chi.URLParam(r, "name")
	if !tenantNameRE.MatchString(name) {
		writeError(w, r, model.CodeValidation,
			fmt.Sprintf("tenant name %q is outside ^[a-z][a-z0-9-]{0,31}$", name),
			map[string]any{"fields": []string{"name"}})
		return "", false
	}
	return name, true
}

func (s *Server) handleTenant(w http.ResponseWriter, r *http.Request) {
	name, ok := tenantName(w, r)
	if !ok {
		return
	}
	body, err := s.Backend.Tenant(r.Context(), name, isViewer(r))
	if err != nil {
		s.backendError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, body)
}

func (s *Server) handleEnv(w http.ResponseWriter, r *http.Request) {
	name, ok := tenantName(w, r)
	if !ok {
		return
	}
	body, err := s.Backend.Env(r.Context(), name)
	if err != nil {
		s.backendError(w, r, err)
		return
	}
	if isViewer(r) {
		// "keys[] with class == public only": a viewer does not learn WHICH
		// secrets a tenant has, which is a fact of its own.
		kept := make([]model.EnvKey, 0, len(body.Keys))
		for _, k := range body.Keys {
			if k.Class == model.ClassPublic {
				kept = append(kept, k)
			}
		}
		body.Keys = kept
	}
	writeJSON(w, http.StatusOK, body)
}

func (s *Server) handleLogs(w http.ResponseWriter, r *http.Request) {
	name, ok := tenantName(w, r)
	if !ok {
		return
	}
	file := r.URL.Query().Get("file")
	if !logFiles[file] {
		writeError(w, r, model.CodeValidation,
			fmt.Sprintf("file %q is not one of api, qdrant, es, ui", file),
			map[string]any{"fields": []string{"file"}})
		return
	}
	lines, ok := intQuery(w, r, "lines", 200, 1, model.MaxLogLines)
	if !ok {
		return
	}
	body, err := s.Backend.Logs(r.Context(), name, file, lines)
	if err != nil {
		s.backendError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, body)
}

// --------------------------------------------------------------------------
// Doctor
// --------------------------------------------------------------------------

func (s *Server) handleDoctor(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	tenant := q.Get("tenant")
	if tenant != "" && !tenantNameRE.MatchString(tenant) {
		writeError(w, r, model.CodeValidation,
			fmt.Sprintf("tenant %q is outside ^[a-z][a-z0-9-]{0,31}$", tenant),
			map[string]any{"fields": []string{"tenant"}})
		return
	}
	op := q.Get("op")
	// doctor.KnownOp, not a hand-kept list: the `op` query enum and the
	// precondition table were two copies of one set, and they had already
	// drifted — the router accepted `adopt` and `settings-put`, which doctor
	// had no rows for, so RedCodes returned nil and the whole precondition
	// gate was silently off for exactly those two ops. One source now, with
	// TestEveryContractOpIsKnownToDoctor asserting it still covers the
	// contract's enum.
	if op != "" && !doctor.KnownOp(op) {
		writeError(w, r, model.CodeValidation,
			fmt.Sprintf("op %q is not a known operation", op),
			map[string]any{"fields": []string{"op"}})
		return
	}
	body, err := s.Backend.Doctor(r.Context(), tenant, op)
	if err != nil {
		s.backendError(w, r, err)
		return
	}
	if isViewer(r) {
		// "findings[].detail is path-redacted for a viewer": the absolute
		// paths in a finding are host layout, which a viewer has no route to
		// act on and every reason not to learn.
		fleet, ferr := s.Backend.Registry(r.Context())
		if ferr == nil {
			for i := range body.Findings {
				body.Findings[i].Detail = redactPaths(body.Findings[i].Detail, fleet)
				body.Findings[i].Repair = redactPaths(body.Findings[i].Repair, fleet)
			}
		}
	}
	writeJSON(w, http.StatusOK, body)
}

// redactPaths replaces a tenant's absolute paths with their registry role, so
// a viewer sees "<data_dir>/config/tenant.env" rather than the host layout.
func redactPaths(detail string, f *registry.Fleet) string {
	if detail == "" || f == nil {
		return detail
	}
	type sub struct{ from, to string }
	subs := make([]sub, 0, 2*len(f.Tenants)+1)
	for _, t := range f.Tenants {
		if t.DataDir != "" {
			subs = append(subs, sub{t.DataDir, "<data_dir>"})
		}
		if t.Worktree != "" {
			subs = append(subs, sub{t.Worktree, "<worktree>"})
		}
	}
	// Longest first, so /rag/data/tenants/dev is replaced before /rag.
	for i := 0; i < len(subs); i++ {
		for j := i + 1; j < len(subs); j++ {
			if len(subs[j].from) > len(subs[i].from) {
				subs[i], subs[j] = subs[j], subs[i]
			}
		}
	}
	for _, sb := range subs {
		detail = strings.ReplaceAll(detail, sb.from, sb.to)
	}
	if f.RagRoot != "" {
		detail = strings.ReplaceAll(detail, f.RagRoot, "<rag_root>")
	}
	return detail
}

// --------------------------------------------------------------------------
// Gateway and settings (projections of the registry)
// --------------------------------------------------------------------------

// handleGatewayStatus answers honestly for a daemon that has published
// nothing: generation 0, `txn_state: none`, every nginx fact null (PR-A does
// not read the master's /proc), and `pending_diff: true` — because a registry
// that has never been published to the gateway IS ahead of what is serving,
// and the schema has no third value for "unknown".
func (s *Server) handleGatewayStatus(w http.ResponseWriter, r *http.Request) {
	f, err := s.Backend.Registry(r.Context())
	if err != nil {
		s.backendError(w, r, err)
		return
	}
	routes := make([]model.GatewayRoute, 0, len(f.Tenants))
	for _, t := range fleet.Order(f) {
		routes = append(routes, model.GatewayRoute{
			Name:     t.Name,
			API:      t.Ports.API,
			UI:       nullablePort(int(t.UI.Port)),
			UIMode:   t.UI.Mode,
			Readonly: false,
			Status:   routeStatus(t.State),
		})
	}
	legacy := make([]model.GatewayRoute, 0, len(f.LegacyRoutes))
	for _, l := range f.LegacyRoutes {
		l := l
		legacy = append(legacy, model.GatewayRoute{
			Name: l.Name, API: l.API, UI: nullablePort(l.UI),
			UIMode: registry.UIModeExternal, Readonly: l.Readonly, Status: l.Status,
		})
	}
	writeJSON(w, http.StatusOK, model.GatewayStatus{
		Generation:         0,
		PublishedAt:        nil,
		PublishedBy:        nil,
		TxnState:           "none",
		PendingDiff:        true,
		RegistryGeneration: f.Generation,
		Nginx:              model.GatewayNginx{},
		Routes:             routes,
		LegacyRoutes:       legacy,
	})
}

func routeStatus(state string) string {
	switch state {
	case "active":
		return "active"
	case "decommissioned":
		return "retired"
	default:
		return "maintenance"
	}
}

func nullablePort(p int) *int {
	if p == 0 {
		return nil
	}
	return &p
}

// handleGatewayRender is a READ despite the verb: it renders the next
// generation from the registry into memory and reports the digest that
// `gateway/apply` would pin. Nothing is written, no lock is taken.
func (s *Server) handleGatewayRender(w http.ResponseWriter, r *http.Request) {
	f, err := s.Backend.Registry(r.Context())
	if err != nil {
		s.backendError(w, r, err)
		return
	}
	tenants, err := render.NginxTenants(f, render.NginxConfig{})
	if err != nil {
		writeError(w, r, model.CodeInternal, "the gateway renderer refused the registry: "+err.Error(), nil)
		return
	}
	static, err := render.NginxStatic(f, render.NginxConfig{})
	if err != nil {
		writeError(w, r, model.CodeInternal, "the gateway renderer refused the registry: "+err.Error(), nil)
		return
	}
	files := []model.GatewayFile{
		{Path: "conf.d/05-tenants.generated.conf", Diff: unifiedAdd(tenants)},
		{Path: "snippets/tenants-ui-static.generated.conf", Diff: unifiedAdd(static)},
	}
	sum := sha256.New()
	for _, f := range files {
		sum.Write([]byte(f.Path))
		sum.Write([]byte{0})
		sum.Write([]byte(f.Diff))
	}
	writeJSON(w, http.StatusOK, model.GatewayRenderResponse{
		// The first generation this daemon would publish. Nothing has been
		// published, so everything in it is new and `changed` is true.
		Generation:         1,
		RegistryGeneration: f.Generation,
		Changed:            true,
		Files:              files,
		SHA256:             "sha256:" + hex.EncodeToString(sum.Sum(nil)),
	})
}

// unifiedAdd renders content as an all-additions diff — which is what it is
// against a gateway generation that does not exist yet.
func unifiedAdd(content []byte) string {
	lines := strings.Split(strings.TrimRight(string(content), "\n"), "\n")
	var b strings.Builder
	for _, l := range lines {
		b.WriteString("+")
		b.WriteString(l)
		b.WriteString("\n")
	}
	return b.String()
}

func (s *Server) handleSettings(w http.ResponseWriter, r *http.Request) {
	f, err := s.Backend.Registry(r.Context())
	if err != nil {
		s.backendError(w, r, err)
		return
	}
	roots := paths.NewRoots(f.RagRoot, paths.Overrides{})
	writeJSON(w, http.StatusOK, model.SettingsResponse{
		RegistryGeneration: f.Generation,
		Retention: model.SettingsRetention{
			KeepLast:         map[string]int{"backup": 7, "pre_update": 2},
			KeepPartialHours: 24,
			// v1 never deletes a bundle on its own: `backup prune --dry-run`
			// only. The schema pins this to false for exactly that reason.
			AutoDelete: false,
		},
		Images: model.SettingsImages{
			Qdrant:        settingsImage(roots, "qdrant", f.Images.Qdrant),
			Elasticsearch: settingsImage(roots, "elasticsearch", f.Images.Elasticsearch),
		},
		// Derived from the registry's own rag_root, like every path around it.
		// A hard-coded "/rag/envs/ragstack" made a test root, a second
		// deployment and a relocated tree all answer with the production
		// host's env — a value the caller did not observe, presented as fact.
		PythonEnvDefault: pythonEnvDefault(roots),
		Ctl:              settingsCtl(roots, f.Ctl),
		Recipients: model.SettingsRecipients{
			File: roots.CtlConfigDir + "/backup-recipients.txt",
			// Backup encryption fails closed without recipients; none are
			// configured yet, and a VIEWER never receives fingerprints at all.
			Count:        0,
			Fingerprints: []string{},
			ReadOnly:     true,
		},
	})
}

// pythonEnvDefault is <rag-root>/envs/ragstack, the shared env the deployment
// layout puts there.
func pythonEnvDefault(roots paths.Roots) string {
	return roots.RagRoot + "/envs/ragstack"
}

// digestRE is registry.json#/$defs/Image's `digest`.
var digestRE = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)

// settingsImage projects one registry image onto the contract's Image, filling
// in what the row does not carry.
//
// The registry is a file an operator can hand-edit and an older schema version
// can have written, so "the row has an image block" does not mean "the block
// has a sif, a version and a digest". Passing it through unchecked emitted a
// body that violates settings_response.json — an empty `version` fails
// minLength, an empty `digest` fails the sha256 pattern, an empty `sif` fails
// AbsPath — so the daemon's own contract test would pass while the wire
// response did not. The unpinned markers are the contract's way of saying "on
// disk, not pinned", which is exactly the truth about an unfilled row.
func settingsImage(roots paths.Roots, name string, im registry.Image) model.SettingsImage {
	out := model.SettingsImage{SIF: im.SIF, Version: im.Version, Digest: im.Digest}
	if out.SIF == "" {
		out.SIF = roots.ImagesDir + "/" + name + ".sif"
	}
	if out.Version == "" {
		out.Version = registry.UnpinnedVersion
	}
	if !digestRE.MatchString(out.Digest) {
		out.Digest = registry.UnpinnedDigest
	}
	return out
}

// settingsCtl projects the registry's ctl block, filling in the same way: an
// empty `ui_dist` is not an AbsPath and a zero `port` is below the schema's
// minimum, so both would make the response unparseable by its own clients.
func settingsCtl(roots paths.Roots, c registry.Ctl) model.SettingsCtl {
	out := model.SettingsCtl{Port: c.Port, UIDist: c.UIDist, GatewayEnabled: c.GatewayEnabled}
	if out.Port < 1024 || out.Port > 65535 {
		out.Port = paths.CtlPort
	}
	if out.UIDist == "" {
		out.UIDist = roots.CtlUIDist()
	}
	return out
}

// --------------------------------------------------------------------------
// Jobs and audit — empty until the engine lands in PR-C
// --------------------------------------------------------------------------

func (s *Server) handleJobs(w http.ResponseWriter, r *http.Request) {
	limit, ok := intQuery(w, r, "limit", 50, 1, 500)
	if !ok {
		return
	}
	if state := r.URL.Query().Get("state"); state != "" && !jobStates[state] {
		writeError(w, r, model.CodeValidation, fmt.Sprintf("state %q is not a job state", state),
			map[string]any{"fields": []string{"state"}})
		return
	}
	writeJSON(w, http.StatusOK, model.JobsResponse{
		Jobs: []json.RawMessage{}, Limit: limit, Truncated: false,
	})
}

var jobStates = map[string]bool{
	"queued": true, "running": true, "awaiting_cutover": true, "succeeded": true,
	"failed": true, "rolled_back": true, "interrupted": true, "cancelled": true,
}

// jobID validates {id} and returns false having written the 422.
func jobID(w http.ResponseWriter, r *http.Request) (string, bool) {
	id := chi.URLParam(r, "id")
	if !jobIDRE.MatchString(id) {
		writeError(w, r, model.CodeValidation, fmt.Sprintf("job id %q is not a ULID", id),
			map[string]any{"fields": []string{"id"}})
		return "", false
	}
	return id, true
}

func (s *Server) handleJob(w http.ResponseWriter, r *http.Request) {
	id, ok := jobID(w, r)
	if !ok {
		return
	}
	writeError(w, r, model.CodeNotFound, "no job "+id+": the job engine lands in PR-C", nil)
}

func (s *Server) handleJobStepLog(w http.ResponseWriter, r *http.Request) {
	id, ok := jobID(w, r)
	if !ok {
		return
	}
	if _, ok := intQuery(w, r, "lines", 200, 1, model.MaxLogLines); !ok {
		return
	}
	writeError(w, r, model.CodeNotFound, "no job "+id+": the job engine lands in PR-C", nil)
}

func (s *Server) handleJobSecrets(w http.ResponseWriter, r *http.Request) {
	id, ok := jobID(w, r)
	if !ok {
		return
	}
	// The one response in this API that would ever carry a secret value.
	// There is nothing to deliver, and the header is set anyway so the
	// no-store property is not something a later PR has to remember.
	w.Header().Set("Cache-Control", "no-store")
	writeError(w, r, model.CodeNotFound, "no job "+id+": the delivery envelope lands in PR-C", nil)
}

func (s *Server) handleAudit(w http.ResponseWriter, r *http.Request) {
	limit, ok := intQuery(w, r, "limit", 100, 1, 1000)
	if !ok {
		return
	}
	if tenant := r.URL.Query().Get("tenant"); tenant != "" && !tenantNameRE.MatchString(tenant) {
		writeError(w, r, model.CodeValidation, fmt.Sprintf("tenant %q is outside ^[a-z][a-z0-9-]{0,31}$", tenant),
			map[string]any{"fields": []string{"tenant"}})
		return
	}
	// A read never produces an audit row, so an empty log is the truth here:
	// this daemon has performed no mutation.
	writeJSON(w, http.StatusOK, model.AuditResponse{
		Rows: []json.RawMessage{}, Limit: limit, Truncated: false,
	})
}

// --------------------------------------------------------------------------
// Mutations — registered, authorized, then refused
// --------------------------------------------------------------------------

// opRequest is the envelope every mutating body shares — the union of
// op_request.json and create_request.json's top-level members. PR-A reads only
// the one the credential rules need; PR-C validates the rest against
// x-ctl-op-args. The other members are declared so that rejecting UNKNOWN
// members (below) refuses what the contract does not define, rather than
// refusing what PR-A has not got round to reading.
type opRequest struct {
	DryRun              bool            `json:"dry_run"`
	IdempotencyKey      string          `json:"idempotency_key"`
	Confirm             string          `json:"confirm"`
	CtlAPIKey           string          `json:"ctl_api_key"`
	ForceWithDoctorDiff string          `json:"force_with_doctor_diff"`
	Args                json.RawMessage `json:"args"`
}

type mutationBodyKey struct{}

// withMutationBody carries the body the GUARD parsed to the handler. The body
// is a stream: reading it in the guard and again in the handler would give the
// handler an empty one, and re-reading is also how a check and its subject
// come to disagree.
func withMutationBody(ctx context.Context, body opRequest) context.Context {
	return context.WithValue(ctx, mutationBodyKey{}, body)
}

// mutationBodyFromContext returns the envelope the guard parsed. ok is false
// outside a mutating route: a handler must not assume how it was reached.
func mutationBodyFromContext(ctx context.Context) (opRequest, bool) {
	body, ok := ctx.Value(mutationBodyKey{}).(opRequest)
	return body, ok
}

func (s *Server) handleMutationRefused(w http.ResponseWriter, r *http.Request) {
	// The body and every credential rule about it were settled by the guard;
	// what is left for PR-A is to say when the verb arrives.
	writeError(w, r, model.CodeRefused, mutationsArriveInPRC, nil)
}

// mutationCredentialsAgree applies the contract's rules about the ctl key a
// mutation body may carry, for every mutating operation at once.
//
//   - `ctl_api_key` is REQUIRED, with exactly one exemption: "when the request
//     already carries `X-API-Key`, `ctl_api_key` may be omitted"
//     (op_request.json). The exemption is about the HEADER, not about the
//     principal — so it is read off the presented credential, not off
//     p.AuthMethod.
//   - With that header, the two must be the SAME key (400 `both_credentials`):
//     which credential authorized must never be ambiguous, exactly as for the
//     two headers.
//   - Otherwise the key must belong to the principal that authenticated (403),
//     or a viewer's session plus a borrowed operator key would be a privilege
//     escalation with two owners.
//
// Only the session case used to be refused here, so a BEARER token — a
// credential a browser can hold for hours and one the ctl does not issue —
// mutated the fleet with no ctl key anywhere in the request. The whole point
// of `ctl_api_key` is that a long-lived read credential is not enough to
// change anything; a bearer that skipped it was a session with better luck.
func (s *Server) mutationCredentialsAgree(w http.ResponseWriter, r *http.Request, p auth.Principal, body opRequest) bool {
	if body.CtlAPIKey == "" {
		if auth.ReadCredential(r).Kind == "api_key" {
			return true
		}
		if p.AuthMethod == auth.MethodSession {
			writeError(w, r, model.CodeForbidden, auth.SessionsAreReadsOnlyDetail, nil)
			return false
		}
		writeError(w, r, model.CodeForbidden,
			"this operation mutates: send the ctl API key in the body as ctl_api_key, or present it as the X-API-Key header", nil)
		return false
	}
	return s.bodyKeyAgrees(w, r, p, body.CtlAPIKey)
}

// bodyKeyAgrees decides whether the key in the body belongs to the caller.
//
// "Belongs to" has two forms, because a ctl key's own principal id is
// "key:<label>" and an identity's is "bvbrc:<un>", so the two can never be
// compared directly:
//
//   - EXPLICITLY, through CTL_API_KEY_PRINCIPALS: the key is declared to
//     belong to an identity subject, and a caller who authenticated AS that
//     subject — by bearer token, or by a session minted from one — may use it.
//     Without this, a bearer principal could never satisfy the rule at all,
//     which made `ctl_api_key` unusable for exactly the browser flow the
//     contract designed it for.
//   - IMPLICITLY: the caller was itself minted from this key (an X-API-Key
//     request, or a session created from it). Same fingerprint, same key.
//
// A key with an explicit binding is usable ONLY by that subject: an unbound
// key is a key nobody's browser may re-present on someone else's behalf, and a
// bound one must not become a second path to an implicit match.
func (s *Server) bodyKeyAgrees(w http.ResponseWriter, r *http.Request, p auth.Principal, bodyKey string) bool {
	c := auth.ReadCredential(r)
	if c.Kind == "api_key" {
		if subtle.ConstantTimeCompare([]byte(c.Value), []byte(bodyKey)) != 1 {
			writeError(w, r, model.CodeBothCredentials,
				"the body's ctl_api_key differs from the X-API-Key header; which credential authorized must never be ambiguous", nil)
			return false
		}
		return true
	}
	keyPrincipal, err := s.Resolver.Keys.LookupKey(bodyKey)
	if err != nil {
		// An unusable key in the body is a credential failure like any other:
		// unrecorded, the body is a guessing channel with no budget at all,
		// reachable by anyone holding one read-only session.
		s.failBodyKey(r)
		writeError(w, r, model.CodeForbidden, "the ctl_api_key in the body is not usable", nil)
		return false
	}
	if keyPrincipal.BoundSubject != "" {
		if keyPrincipal.BoundSubject == p.Subject {
			return true
		}
	} else if p.KeyFingerprint != "" && p.KeyFingerprint == keyPrincipal.KeyFingerprint {
		return true
	} else if keyPrincipal.Subject == p.Subject {
		return true
	}
	s.failBodyKey(r)
	writeError(w, r, model.CodeForbidden,
		"the ctl_api_key in the body is bound to a different principal than the credential that authenticated", nil)
	return false
}

// failBodyKey counts a body-key refusal against the credential that PRESENTED
// it, not against the key that was guessed: an attacker supplies a different
// guess every time, so a per-guess budget would never fire, while the session
// or token doing the guessing is the thing worth bounding.
func (s *Server) failBodyKey(r *http.Request) {
	if s.Resolver == nil || s.Resolver.Limiter == nil {
		return
	}
	s.Resolver.Limiter.Fail(auth.ReadCredential(r).Hash)
}

// readMutationBody parses the envelope, or writes the 422.
//
// Three refusals that used to be silent acceptances:
//
//   - a body over 1 MiB was TRUNCATED at the limit and the truncated prefix
//     parsed, so an oversized request produced a decision about a body nobody
//     sent. MaxBytesReader makes it an error instead. It is reported as 422
//     `validation` rather than 413: error.json promises one status per code,
//     and a 413 would need a code of its own to stay reconstructable.
//   - unknown members were dropped, so `ctl_api_kye` (or `dry_run` misspelt on
//     a destructive verb) read as absent.
//   - the literal `null` decoded into the zero envelope — a mutation with no
//     body that looked like a mutation with an empty one.
func (s *Server) readMutationBody(w http.ResponseWriter, r *http.Request) (opRequest, bool) {
	var body opRequest
	r.Body = http.MaxBytesReader(w, r.Body, maxBodyBytes)
	raw, err := io.ReadAll(r.Body)
	if err != nil {
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			writeError(w, r, model.CodeValidation, "body exceeds 1 MiB", nil)
			return body, false
		}
		writeError(w, r, model.CodeValidation, "could not read the request body", nil)
		return body, false
	}
	if len(bytes.TrimSpace(raw)) == 0 {
		return body, true
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&body); err != nil {
		writeError(w, r, model.CodeValidation, "the request body is not a JSON object matching this operation's schema", nil)
		return body, false
	}
	if bytes.Equal(bytes.TrimSpace(raw), []byte("null")) {
		// json.Decode accepts `null` into a struct and leaves it zeroed.
		writeError(w, r, model.CodeValidation, "the request body is null; send a JSON object or no body at all", nil)
		return body, false
	}
	if dec.More() {
		writeError(w, r, model.CodeValidation, "the request body carries trailing content after the JSON object", nil)
		return body, false
	}
	return body, true
}

// --------------------------------------------------------------------------
// Shared helpers
// --------------------------------------------------------------------------

// intQuery reads a bounded integer query parameter. Out of range is a 422
// `validation`, never a silent clamp: a caller that asked for 100000 lines
// should learn the cap, not receive 5000 and believe it was everything.
func intQuery(w http.ResponseWriter, r *http.Request, name string, def, min, max int) (int, bool) {
	raw := r.URL.Query().Get(name)
	if raw == "" {
		return def, true
	}
	n, err := strconv.Atoi(raw)
	if err != nil || n < min || n > max {
		writeError(w, r, model.CodeValidation,
			fmt.Sprintf("%s must be an integer in [%d, %d]", name, min, max),
			map[string]any{"fields": []string{name}})
		return 0, false
	}
	return n, true
}

// backendError maps a backend failure onto the contract. ErrNotFound is the
// only one with a specific answer; everything else is a 500 whose detail stays
// on the server side of the log, never in the body.
func (s *Server) backendError(w http.ResponseWriter, r *http.Request, err error) {
	if errors.Is(err, ErrNotFound) {
		writeError(w, r, model.CodeNotFound, "no such tenant, log or job", nil)
		return
	}
	// The server's REDACTING logger, not slog.Default(): a backend error is
	// wrapped text from the host — a failed envfile read quotes the line it
	// failed on, and that line is `API_KEYS=…`.
	s.log().Error("backend", "request_id", observability.RequestIDFromContext(r.Context()), "err", err.Error())
	writeError(w, r, model.CodeInternal, "the control plane could not answer this request", nil)
}
