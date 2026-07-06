package api

import "net/http"

// HandleGetChunks fetches chunks by id (context expansion around a source).
//
// Phase-1 scaffold: the Go pipeline isn't wired to a vector store yet, so this
// returns an empty—but schema-valid—chunk list for any request, so the
// advertised endpoint (and its contract) exists in both impls. The Python
// implementation is authoritative for real chunk resolution.
func HandleGetChunks(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, ChunksResponse{Chunks: []ChunkOut{}})
}
