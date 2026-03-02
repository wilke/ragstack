package api

import (
	"encoding/json"
	"net/http"
)

// HandleQuery runs the full RAG pipeline: rewrite, retrieve, rerank, generate.
// This stub returns a placeholder response.
func HandleQuery(w http.ResponseWriter, r *http.Request) {
	var req QueryRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeValidationError(w, "invalid request body")
		return
	}
	if req.Query == nil {
		writeValidationError(w, "field 'query' is required")
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
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeValidationError(w, "invalid request body")
		return
	}
	if req.Query == nil {
		writeValidationError(w, "field 'query' is required")
		return
	}

	resp := RetrieveResponse{
		Sources: make([]Source, 0),
	}
	writeJSON(w, http.StatusOK, resp)
}
