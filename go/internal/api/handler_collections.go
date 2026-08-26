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
//
// The invariant to implement when this IS wired to a registry (#419): the
// response-level CollectionsResponse.Default is the id an omitted `collection`
// targets **for this caller** — the registry pointer when that caller can read
// it, else the first collection in `collections` (the listing's own order, NOT
// sorted — picking sorted()[0] here reproduces the exact drift #419 is about),
// else "" (and /v1/query, /v1/retrieve and /v1/chunks then answer 404 "no
// collection is accessible to this caller").
//
// The per-item IsDefault/Default flags are the **global** registry pointer and
// do NOT vary by caller: at most one listed entry carries them, and zero do when
// the caller cannot read the collection the pointer names. Those are two
// different questions; answering the caller-aware one with the global pointer is
// #419.
func HandleListCollections(w http.ResponseWriter, _ *http.Request) {
	resp := CollectionsResponse{
		Collections: []CollectionInfo{{
			ID:        "default",
			Label:     "default",
			Model:     "",
			Dim:       0,
			Default:   true,
			IsDefault: true,
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
//
// Per the contract (ADR-0003), `embedding` and `chunk` are OPTIONAL: an omitted
// field is filled from the server-default build spec. The scaffold has no
// configured defaults to resolve, so it echoes a placeholder model ref and
// leaves the chunk fields null; Python is authoritative for the resolved values.
func HandleCreateCollection(w http.ResponseWriter, r *http.Request) {
	var req CollectionCreateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeValidationError(w, "invalid request body")
		return
	}
	id := "pending"
	if req.ID != nil && *req.ID != "" {
		id = *req.ID
	}
	model := req.Embedding // the model ref; Python resolves it to the real model
	if model == "" {
		model = "server-default" // omitted → the server-default build spec
	}
	resp := CollectionInfo{
		ID:      id,
		Label:   req.Label,
		Model:   model,
		Dim:     0,
		Default: false,
	}
	if req.Chunk != nil && req.Chunk.Method != "" {
		chunkMethod := req.Chunk.Method
		resp.ChunkMethod = &chunkMethod
		resp.ChunkSize = req.Chunk.Size
	}
	writeJSON(w, http.StatusCreated, resp)
}

// HandleDeleteCollection removes a collection registry binding.
//
// Phase-3 scaffold: no runtime registry in Go yet, so this is a schema-valid
// no-op (204). The Python implementation is authoritative.
//
// The contract's `purge` query param (also drop the physical Qdrant collection,
// the ES index and the provenance manifest) is deliberately NOT honoured here,
// and this stays a 204 rather than answering with a CollectionPurgeReport: there
// is nothing behind this scaffold to destroy, and a report claiming deletions
// that never happened would be worse than an obvious no-op. Implement it
// alongside a real registry.
func HandleDeleteCollection(w http.ResponseWriter, r *http.Request) {
	_ = chi.URLParam(r, "collection_id")
	w.WriteHeader(http.StatusNoContent)
}
