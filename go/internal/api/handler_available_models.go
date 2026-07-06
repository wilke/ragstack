package api

import "net/http"

// HandleListAvailableModels lists models assignable per-request (llm / reranker).
//
// Phase-2 scaffold: the Go pipeline has no model registry yet, so this returns an
// empty—but schema-valid—list, so the advertised endpoint (and its contract)
// exists in both impls. The Python implementation is authoritative.
func HandleListAvailableModels(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, AvailableModelsResponse{Models: []AvailableModel{}})
}
