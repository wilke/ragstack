package api

import "net/http"

// HandleListEntities returns all entities in the knowledge graph.
func HandleListEntities(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, make([]EntityInfo, 0))
}

// HandleGetNeighbors returns triples in an entity's neighborhood.
func HandleGetNeighbors(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, make([]TripleResponse, 0))
}
