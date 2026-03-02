package api

import "net/http"

// HandleHealth responds with the service health status.
func HandleHealth(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, HealthResponse{Status: "ok"})
}
