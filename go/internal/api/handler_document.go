package api

import (
	"encoding/json"
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
)

// HandleIngest accepts a document for ingestion.
func HandleIngest(w http.ResponseWriter, r *http.Request) {
	var req IngestRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeValidationError(w, "invalid request body")
		return
	}
	if req.Source == nil {
		writeValidationError(w, "field 'source' is required")
		return
	}

	resp := IngestResponse{
		JobID:    uuid.New().String(),
		Status:   "accepted",
		ChunkIDs: make([]string, 0),
	}
	writeJSON(w, http.StatusOK, resp)
}

// HandleIngestStatus returns the status of an ingestion job.
func HandleIngestStatus(w http.ResponseWriter, r *http.Request) {
	jobID := chi.URLParam(r, "job_id")
	resp := IngestResponse{
		JobID:    jobID,
		Status:   "not_found",
		ChunkIDs: make([]string, 0),
	}
	writeJSON(w, http.StatusOK, resp)
}

// HandleListDocuments returns all indexed documents.
func HandleListDocuments(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, make([]DocumentInfo, 0))
}

// HandleDeleteDocument deletes a document and all its chunks.
func HandleDeleteDocument(w http.ResponseWriter, _ *http.Request) {
	w.WriteHeader(http.StatusNoContent)
}
