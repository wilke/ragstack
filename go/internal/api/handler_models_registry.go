package api

import "net/http"

// HandleListModelRegistry lists registered models + hot-swappable assignments.
//
// Phase-1 scaffold: the Go pipeline has no runtime model registry yet, so this
// returns an empty—but schema-valid—snapshot, so the advertised endpoint (and
// its contract) exists in both impls. The Python implementation is authoritative
// for the registry and the live hot-swap; the write/PATCH routes are Python-only
// in phase 1.
func HandleListModelRegistry(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, ModelsRegistryResponse{
		Models:      []ModelEntry{},
		Assignments: map[string]string{},
	})
}
