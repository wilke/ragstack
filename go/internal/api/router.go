package api

import (
	"log/slog"
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
)

// NewRouter creates the HTTP handler with all routes registered.
func NewRouter(_ *slog.Logger) http.Handler {
	r := chi.NewRouter()

	r.Use(middleware.RequestID)
	r.Use(middleware.RealIP)
	r.Use(middleware.Recoverer)

	r.Get("/health", HandleHealth)

	r.Route("/v1", func(r chi.Router) {
		r.Post("/query", HandleQuery)
		r.Post("/retrieve", HandleRetrieve)
		r.Post("/ingest", HandleIngest)
		r.Get("/ingest/{job_id}", HandleIngestStatus)
		r.Get("/documents", HandleListDocuments)
		r.Delete("/documents/{doc_id}", HandleDeleteDocument)
		r.Get("/graph/entities", HandleListEntities)
		r.Get("/graph/neighbors/{entity}", HandleGetNeighbors)
	})

	return r
}
