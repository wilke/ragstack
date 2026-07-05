package api

import "net/http"

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
