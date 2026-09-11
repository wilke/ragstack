package api

import (
	"encoding/json"
	"net/http"

	"github.com/ragstack/ragstack/internal/ctl/model"
	"github.com/ragstack/ragstack/internal/observability"
)

// writeJSON writes one JSON body. Every 2xx schema in contracts/ctl/schemas is
// additionalProperties:false, so nothing is added here.
func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

// writeError writes exactly contracts/ctl/schemas/error.json.
//
// The status is derived from the code rather than passed alongside it: the
// contract promises one status per code ("so the code alone is enough to
// reconstruct the status in a log"), and a helper that let a caller pair
// `forbidden` with a 401 would quietly break that promise. The one exception
// the contract itself names — the 410 of an already-delivered secrets envelope,
// which carries code `not_found` — goes through writeErrorStatus.
func writeError(w http.ResponseWriter, r *http.Request, code model.ErrorCode, detail string, extra map[string]any) {
	writeErrorStatus(w, r, code.HTTPStatus(), code, detail, extra)
}

// writeErrorStatus is writeError with the status stated explicitly.
func writeErrorStatus(w http.ResponseWriter, r *http.Request, status int, code model.ErrorCode, detail string, extra map[string]any) {
	writeJSON(w, status, model.Error{
		Detail: detail,
		Code:   code,
		// Redundant with the X-Request-Id header on purpose: a header does not
		// survive a user pasting the body into a ticket.
		RequestID: observability.RequestIDFromContext(r.Context()),
		Extra:     extra,
	})
}

// reject adapts writeError to auth.Rejecter. The auth package cannot write
// this body itself without importing this one, so it is handed this function.
func reject(w http.ResponseWriter, r *http.Request, status int, code, detail string, extra map[string]any) {
	writeErrorStatus(w, r, status, model.ErrorCode(code), detail, extra)
}

// notFound is the router's 404 for a path no operation claims. It is a
// contract error body, not chi's bare text: `X-Request-Id` and a machine
// readable `code` are promised on EVERY non-2xx, including this one.
func notFound(w http.ResponseWriter, r *http.Request) {
	writeError(w, r, model.CodeNotFound, "no such operation: "+r.Method+" "+r.URL.Path, nil)
}

// methodNotAllowed answers a known path with a verb no operation claims. It is
// a 404 rather than a 405 for the same reason authorization precedes lookup:
// the set of verbs a path accepts is not a fact worth publishing to a caller
// who may not call any of them.
func methodNotAllowed(w http.ResponseWriter, r *http.Request) {
	writeError(w, r, model.CodeNotFound, "no such operation: "+r.Method+" "+r.URL.Path, nil)
}
