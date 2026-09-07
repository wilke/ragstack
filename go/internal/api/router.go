package api

import (
	"log/slog"
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"

	"github.com/ragstack/ragstack/internal/auth"
	"github.com/ragstack/ragstack/internal/grading"
	"github.com/ragstack/ragstack/internal/observability"
)

// Server holds the per-process state the stateful handlers need.
//
// Most of this scaffold's handlers are stubs with nothing to hold, so they stay
// package-level functions; `/v1/grading` is the first surface with real state,
// and it hangs off here rather than off a package global so a test can stand up
// an isolated one.
type Server struct {
	// Auth resolves the calling principal. A keyless Authenticator is the open
	// dev/test path, not an absent one.
	Auth *auth.Authenticator
	// Grading is the grading store. The Go side has no database layer of any
	// kind (no `database/sql`, no driver in go.mod, no registry or job store to
	// share one with), so this is the in-memory store; the Python
	// implementation is authoritative for durable grading state.
	Grading grading.Store
}

// NewServer builds the handler state from the environment.
func NewServer() *Server {
	return &Server{
		Auth:    auth.LoadFromEnv(),
		Grading: grading.NewMemoryStore(),
	}
}

// NewRouter creates the HTTP handler with all routes registered.
//
// The logger used to be discarded (`NewRouter(_ *slog.Logger)`), which made
// observability.LoggingMiddleware dead code — a repo-wide grep found only its
// own definition. It is wired up here (#427 W7), which is also what puts a
// request_id on every log line.
func NewRouter(logger *slog.Logger) http.Handler {
	return NewRouterWithServer(logger, NewServer())
}

// NewRouterWithServer is NewRouter with the handler state supplied — for tests,
// which must be able to give a server its own store and its own key set without
// mutating the process environment.
func NewRouterWithServer(logger *slog.Logger, s *Server) http.Handler {
	if logger == nil {
		logger = slog.Default()
	}
	if s == nil {
		s = NewServer()
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
		// Authentication wraps the WHOLE of /v1, not just the grading routes:
		// a surface where one route knows who is calling and its neighbours do
		// not is the shape that produces an IDOR. On a keyless server this
		// middleware rejects nothing and every route behaves exactly as it did
		// before it existed. `/health` stays outside it on purpose — a probe
		// must not need a credential.
		r.Use(s.Auth.Middleware(writeUnauthorized))

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
		r.Get("/stats/tenants", s.HandleStatsTenants)

		// Grading — all eleven operations (docs/plans/grading-ui.md phase 4).
		r.Get("/grading/batches", s.HandleListGradingBatches)
		r.Post("/grading/batches", s.HandleCreateGradingBatch)
		r.Get("/grading/batches/{batch_id}", s.HandleGetGradingBatch)
		r.Delete("/grading/batches/{batch_id}", s.HandleDeleteGradingBatch)
		r.Get("/grading/batches/{batch_id}/tasks", s.HandleListGradingTasks)
		r.Post("/grading/batches/{batch_id}/adjudicate", s.HandleAdjudicateGradingBatch)
		r.Get("/grading/batches/{batch_id}/export", s.HandleExportGradingBatch)
		r.Get("/grading/tasks/{task_id}", s.HandleGetGradingTask)
		r.Put("/grading/tasks/{task_id}/verdict", s.HandlePutGradingVerdict)
		r.Get("/grading/tasks/{task_id}/verdicts/{reader}", s.HandleGetGradingVerdict)
		r.Put("/grading/tasks/{task_id}/adjudication", s.HandlePutGradingAdjudication)
	})

	return r
}
