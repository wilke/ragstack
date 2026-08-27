package api

import (
	"encoding/json"
	"net/http"

	"github.com/ragstack/ragstack/internal/observability"
)

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v) //nolint:errcheck
}

// Error bodies carry `request_id` alongside `detail`, matching the Python
// implementation's 503 body (`python/ragstack/api/main.py`). It is redundant
// with the X-Request-Id header on purpose: a user who pastes the raw JSON into a
// ticket carries the id with it, and a response header does not survive a
// copy-paste.
//
// `omitempty` on both, so a handler exercised without the middleware — a direct
// unit-test call, a CLI — emits exactly the pre-#427 shape rather than an empty
// string. Success bodies deliberately do NOT carry it: every 2xx schema in
// contracts/schemas/ is `additionalProperties: false`.

type validationError struct {
	Detail    []fieldError `json:"detail"`
	RequestID string       `json:"request_id,omitempty"`
}

type fieldError struct {
	Loc  []string `json:"loc"`
	Msg  string   `json:"msg"`
	Type string   `json:"type"`
}

// errorBody is the plain `{detail, request_id}` shape for errors that are not
// per-field validation failures.
type errorBody struct {
	Detail    string `json:"detail"`
	RequestID string `json:"request_id,omitempty"`
}

func writeValidationError(w http.ResponseWriter, r *http.Request, msg string) {
	writeJSON(w, http.StatusUnprocessableEntity, validationError{
		Detail: []fieldError{{
			Loc:  []string{"body"},
			Msg:  msg,
			Type: "value_error",
		}},
		RequestID: observability.RequestIDFromContext(r.Context()),
	})
}

func writeError(w http.ResponseWriter, r *http.Request, status int, detail string) {
	writeJSON(w, status, errorBody{
		Detail:    detail,
		RequestID: observability.RequestIDFromContext(r.Context()),
	})
}
