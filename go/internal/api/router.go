package api

import (
	"log/slog"
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"

	"github.com/ragstack/ragstack/internal/observability"
)

// NewRouter creates the HTTP handler with all routes registered.
//
// The logger used to be discarded (`NewRouter(_ *slog.Logger)`), which made
// observability.LoggingMiddleware dead code — a repo-wide grep found only its
// own definition. It is wired up here (#427 W7), which is also what puts a
// request_id on every log line.
func NewRouter(logger *slog.Logger) http.Handler {
	if logger == nil {
		logger = slog.Default()
	}

	r := chi.NewRouter()

	// Order is load-bearing: chi runs middlewares in registration order,
	// outermost first.
	//
	//	RequestID — outermost, so the header is stamped and the context is
	//	            populated before anything else can write a response. That
	//	            includes chi's own 404 handler, which sits at the end of
	//	            this same chain.
	//	Logging   — inside RequestID so it can read the id; outside Recoverer
	//	            so a recovered panic still produces a line, with its 500.
	//
	// chi's middleware.RequestID is deliberately NOT used: its id format can
	// never match the contract's `^[0-9a-f]{16}$`, and it echoes an inbound
	// X-Request-Id verbatim. See internal/observability/requestid.go.
	r.Use(observability.RequestIDMiddleware)
	r.Use(middleware.RealIP)
	r.Use(observability.LoggingMiddleware(logger))
	r.Use(middleware.Recoverer)

	r.Get("/health", HandleHealth)

	r.Route("/v1", func(r chi.Router) {
		r.Post("/query", HandleQuery)
		r.Post("/retrieve", HandleRetrieve)
		r.Post("/ingest", HandleIngest)
		r.Post("/ingest/upload", HandleIngestUpload)
		r.Get("/ingest/{job_id}", HandleIngestStatus)
		r.Get("/documents", HandleListDocuments)
		r.Get("/collections", HandleListCollections)
		r.Post("/collections", HandleCreateCollection)
		r.Delete("/collections/{collection_id}", HandleDeleteCollection)
		r.Get("/chunks", HandleGetChunks)
		r.Get("/models/available", HandleListAvailableModels)
		r.Get("/admin/models/registry", HandleListModelRegistry)
		r.Delete("/documents/{doc_id}", HandleDeleteDocument)
		r.Get("/graph/entities", HandleListEntities)
		r.Get("/graph/neighbors/{entity}", HandleGetNeighbors)
	})

	return r
}
