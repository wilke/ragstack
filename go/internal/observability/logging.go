package observability

import (
	"log/slog"
	"net/http"
	"time"
)

// LoggingMiddleware returns an HTTP middleware that logs each request.
//
// Install it AFTER RequestIDMiddleware so `request_id` is populated; the line is
// otherwise unusable for the thing #427 asks for — joining a user's report to
// the server log.
//
// `upstream_rid` is emitted only when a caller supplied an id that passed
// validation. A rejected one is never logged in any form: that is the whole
// point of the charset guard in requestid.go, and echoing it into the log "just
// to see what it was" would hand the caller the log-forging primitive back.
func LoggingMiddleware(logger *slog.Logger) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			start := time.Now()
			ww := &responseWriter{ResponseWriter: w, status: http.StatusOK}
			next.ServeHTTP(ww, r)

			attrs := []any{
				"request_id", RequestIDFromContext(r.Context()),
				"method", r.Method,
				"path", r.URL.Path,
				"status", ww.status,
				"duration_ms", time.Since(start).Milliseconds(),
			}
			if upstream := UpstreamRequestIDFromContext(r.Context()); upstream != "" {
				attrs = append(attrs, "upstream_rid", upstream)
			}
			logger.Info("request", attrs...)
		})
	}
}

type responseWriter struct {
	http.ResponseWriter
	status int
}

func (rw *responseWriter) WriteHeader(code int) {
	rw.status = code
	rw.ResponseWriter.WriteHeader(code)
}
