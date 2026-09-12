// Package authz is the control plane's authorization table: which role may
// call which operation, and whether a session may call it at all.
//
// It is DENY BY DEFAULT. An operation with no row in the generated Matrix is
// not "probably a read" — it is refused, so that adding a route to the router
// without adding it to the contract cannot silently publish it. The table
// itself is generated from `x-ctl-authorization-matrix` in
// contracts/ctl/openapi.yaml by `go run ./internal/ctl/authz/gen`; see
// matrix_gen.go and gen/main.go.
//
// Two rules the contract states and this package enforces:
//
//   - Authorization precedes lookup. The role gate is evaluated before a path
//     parameter is resolved, so a viewer probing an operator-only route gets
//     403 whether or not the tenant exists. Tenant existence is not a fact a
//     viewer may probe for.
//   - Sessions are reads only. A session may call every row with
//     `session: true`; a mutation over a session is refused here, with the
//     remedy in the detail, rather than being half-accepted and then failing
//     on a missing body member.
package authz

import (
	"strings"

	"github.com/ragstack/ragstack/internal/ctl/auth"
)

// Row is one operation's authorization facts, mirroring one
// `x-ctl-authorization-matrix` entry.
type Row struct {
	OperationID string
	Method      string
	// Path is the contract path, which is also the chi route pattern
	// ("/v1/tenants/{name}"). The two are kept identical on purpose: a
	// mismatch would make a route unreachable by the matrix and therefore
	// denied, which is the safe direction but is caught by the router test.
	Path string
	// Role is the MINIMUM role: "anonymous", "viewer" or "operator".
	Role string
	// Session reports whether `Authorization: Session <id>` may call it.
	Session bool
	// Mutating reports whether this operation changes fleet state — derived by
	// the generator as "not a GET, and the contract gives it a request body
	// that can carry `ctl_api_key`", which is op_request.json's own definition
	// of "the body of EVERY mutating call".
	//
	// It exists so that "a session may not mutate without re-presenting a ctl
	// key" is enforced by the GUARD, for every mutating row at once, rather
	// than inside one handler that the next mutation handler could be written
	// without. The guard is where deny-by-default lives; a rule enforced per
	// handler is one forgotten call away from absent.
	Mutating bool
	// ViewerFields is the contract's prose description of what a viewer
	// receives; "all" when the response is the summary shape by construction
	// and "n/a" for operations no viewer may call. Prose, not a selector:
	// applying it is the handler's job (see ViewerFields).
	ViewerFields string
}

// Lookup returns the row for one operation, or false. Method is compared
// case-insensitively; path must be the contract/chi pattern exactly.
func Lookup(method, path string) (Row, bool) {
	for _, r := range Matrix {
		if r.Path == path && strings.EqualFold(r.Method, method) {
			return r, true
		}
	}
	return Row{}, false
}

// LookupID returns the row with this operationId, or false.
func LookupID(operationID string) (Row, bool) {
	for _, r := range Matrix {
		if r.OperationID == operationID {
			return r, true
		}
	}
	return Row{}, false
}

// Allowed reports whether role may call (method, routePattern).
//
// Deny by default: an unknown route, an unknown role and an unknown
// requirement all answer false.
func Allowed(method, routePattern, role string) bool {
	r, ok := Lookup(method, routePattern)
	if !ok {
		return false
	}
	if r.Role == "anonymous" {
		return true
	}
	return auth.RoleAtLeast(role, r.Role)
}

// SessionAllowed reports whether a session credential may call the operation.
// Unknown operations answer false, like everything else here.
func SessionAllowed(method, routePattern string) bool {
	r, ok := Lookup(method, routePattern)
	return ok && r.Session
}

// IsAnonymous reports whether the operation needs no credential at all.
func IsAnonymous(method, routePattern string) bool {
	r, ok := Lookup(method, routePattern)
	return ok && r.Role == "anonymous"
}

// ViewerFields returns the contract's viewer-field note for an operation,
// split on ";" and trimmed, or nil when the operation is "all" / "n/a" /
// unknown.
//
// The matrix stores prose ("summary, status, units, drift; registry is null")
// because the reductions are not a uniform field selector — one is "this
// member is null", another is "these rows are filtered by class". Handlers
// implement the reduction; this accessor exists so a handler (or a doc page)
// can quote the contract rather than paraphrase it, and so a test can assert
// that every viewer-readable operation has SOME stated reduction.
func ViewerFields(operationID string) []string {
	r, ok := LookupID(operationID)
	if !ok || r.ViewerFields == "" || r.ViewerFields == "all" || r.ViewerFields == "n/a" {
		return nil
	}
	parts := strings.Split(r.ViewerFields, ";")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}

// Operations returns every row, in the generated (path, method) order.
func Operations() []Row { return Matrix }
