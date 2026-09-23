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
		writeValidationError(w, r, "invalid request body")
		return
	}
	if req.Source == nil {
		writeValidationError(w, r, "field 'source' is required")
		return
	}

	resp := IngestResponse{
		JobID:    uuid.New().String(),
		Status:   "accepted",
		ChunkIDs: make([]string, 0),
	}
	writeJSON(w, http.StatusOK, resp)
}

// HandleIngestUpload accepts multipart PDF uploads for ingestion.
//
// Phase 1 scaffold: file-upload ingestion (issue #202) is implemented in the
// Python surface only; this returns 501 until the Go pipeline can stage and
// ingest uploaded files.
func HandleIngestUpload(w http.ResponseWriter, r *http.Request) {
	writeError(w, r, http.StatusNotImplemented,
		"file upload ingestion is not yet implemented in the Go server")
}

// ingestStatusUnknown is the contract's answer for a job id the caller cannot
// read: one that does not exist, one stamped for another tenant, or a legacy one.
// All three are the same 200 so the endpoint never confirms that a foreign id
// exists (no IDOR via 404) — see GET /v1/ingest/{job_id} in contracts/openapi.yaml.
const ingestStatusUnknown = "unknown"

// HandleIngestStatus returns the status of an ingestion job.
//
// Phase 1 scaffold: the Go server keeps no job store (HandleIngest runs nothing),
// so every id is one it cannot read and answers status "unknown" — the same
// shape Python gives a missing or foreign id. It used to answer "not_found",
// a status the contract does not define (#628).
func HandleIngestStatus(w http.ResponseWriter, r *http.Request) {
	jobID := chi.URLParam(r, "job_id")
	resp := IngestResponse{
		JobID:    jobID,
		Status:   ingestStatusUnknown,
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
