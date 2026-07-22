package api

import (
	"encoding/json"
	"net/http"

	"github.com/go-chi/chi/v5"
)

// HandleListCollections lists the collections the query API can serve.
//
// Phase-1 scaffold: the Go pipeline isn't wired to a collection registry yet, so
// this returns the single "default" collection — a schema-valid, non-404
// response so the advertised endpoint (and its contract) exists in both impls.
// The Python implementation is authoritative for real multi-collection routing.
func HandleListCollections(w http.ResponseWriter, _ *http.Request) {
	resp := CollectionsResponse{
		Collections: []CollectionInfo{{
			ID:      "default",
			Label:   "default",
			Model:   "",
			Dim:     0,
			Default: true,
		}},
		Default: "default",
	}
	writeJSON(w, http.StatusOK, resp)
}

// HandleCreateCollection creates a content-addressed collection (build-time model
// selection).
//
// Phase-3 scaffold: the Go pipeline has no runtime collection registry or model
// registry yet, so this validates the request shape and echoes back a
// schema-valid CollectionInfo (201) — the advertised endpoint and its contract
// exist in both impls. The Python implementation is authoritative for actually
// resolving the embedding model, deriving the content-addressed name, and
// persisting the collection.
func HandleCreateCollection(w http.ResponseWriter, r *http.Request) {
	var req CollectionCreateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeValidationError(w, "invalid request body")
		return
	}
	if req.Embedding == "" {
		writeValidationError(w, "field 'embedding' is required")
		return
	}
	if req.Chunk.Method == "" {
		writeValidationError(w, "field 'chunk.method' is required")
		return
	}
	id := "pending"
	if req.ID != nil && *req.ID != "" {
		id = *req.ID
	}
	chunkMethod := req.Chunk.Method
	resp := CollectionInfo{
		ID:          id,
		Label:       req.Label,
		Model:       req.Embedding, // the model ref; Python resolves it to the real model
		Dim:         0,
		ChunkMethod: &chunkMethod,
		ChunkSize:   req.Chunk.Size,
		Default:     false,
	}
	writeJSON(w, http.StatusCreated, resp)
}

// HandleDeleteCollection removes a collection registry binding.
//
// Phase-3 scaffold: no runtime registry in Go yet, so this is a schema-valid
// no-op (204). The Python implementation is authoritative.
func HandleDeleteCollection(w http.ResponseWriter, r *http.Request) {
	_ = chi.URLParam(r, "collection_id")
	w.WriteHeader(http.StatusNoContent)
}
