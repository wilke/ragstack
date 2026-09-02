package api

import (
	"net/http"
)

// HandleQuery runs the full RAG pipeline: rewrite, retrieve, rerank, generate.
// This stub returns a placeholder response.
func HandleQuery(w http.ResponseWriter, r *http.Request) {
	var req QueryRequest
	if err := decodeJSONBody(r.Body, &req); err != nil {
		writeValidationError(w, r, "invalid request body")
		return
	}
	if req.Query == nil {
		writeValidationError(w, r, "field 'query' is required")
		return
	}
	// Schema-level refusal of an unrepresentable filter value (#471). A 400,
	// not the 422 above: it matches what Python answers, and the conformance
	// suite asserts one status for both implementations.
	if reason := validateFilterValues(req.Filters); reason != "" {
		writeError(w, r, http.StatusBadRequest, reason)
		return
	}

	resp := QueryResponse{
		Answer:           "[pipeline not yet wired]",
		Sources:          make([]Source, 0),
		RewrittenQueries: []string{*req.Query},
	}
	writeJSON(w, http.StatusOK, resp)
}

// HandleRetrieve retrieves relevant chunks without generating an answer.
func HandleRetrieve(w http.ResponseWriter, r *http.Request) {
	var req RetrieveRequest
	if err := decodeJSONBody(r.Body, &req); err != nil {
		writeValidationError(w, r, "invalid request body")
		return
	}
	if req.Query == nil {
		writeValidationError(w, r, "field 'query' is required")
		return
	}
	// Schema-level refusal of an unrepresentable filter value (#471). A 400,
	// not the 422 above: it matches what Python answers, and the conformance
	// suite asserts one status for both implementations.
	if reason := validateFilterValues(req.Filters); reason != "" {
		writeError(w, r, http.StatusBadRequest, reason)
		return
	}

	resp := RetrieveResponse{
		Sources: make([]Source, 0),
	}
	writeJSON(w, http.StatusOK, resp)
}
