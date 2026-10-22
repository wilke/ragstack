package api

import (
	"net/http"

	"github.com/ragstack/ragstack/internal/auth"
)

// HandleHealth responds with the service health status.
func HandleHealth(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, HealthResponse{Status: "ok"})
}

// deepHealthCheckNames is every check GET /v1/health/deep reports, in the
// order the Python implementation lists them: the stores, then the
// dependencies a request can route around (#751).
var deepHealthCheckNames = []string{
	"vector", "text", "graph", "jobstore",
	"reranker", "embedder", "llm", "ingest_engine", "doi_resolver",
}

// deepHealthNotWired is the detail on every Go check. It names no backend, so
// even an admin body carries no hostname or port.
const deepHealthNotWired = "not wired in the Go scaffold"

// HandleHealthDeep serves GET /v1/health/deep (#751 decision 6: a minimal route
// so the conformance suite stops skipping it against the Go server).
//
// The Python server is the authoritative implementation; it probes real stores
// and sidecars. The Go scaffold holds no stores and no model clients, so the
// honest answer is every check `ok: true, state: "disabled"` — "not configured
// here, nothing to probe" — which by the contract does not degrade the overall
// status. The conformance admin test asserts only that the check-name set is
// non-empty and that the body validates against deep_health_response.json.
//
// Admin only, gated per handler like the grading routes (the Go scaffold has no
// router-level RBAC; /v1 is already wrapped in the auth middleware). A
// non-admin gets 403 with a detail that names no backend, check or latency, so
// it cannot trip the conformance leak regex. On a keyless server
// auth.FromContext yields the default role `user`, so this route is 403 there
// too — the same as the Python server, where a keyless caller also has the
// default role.
func (s *Server) HandleHealthDeep(w http.ResponseWriter, r *http.Request) {
	if !auth.FromContext(r.Context()).IsAdmin() {
		writeError(w, r, http.StatusForbidden, "deep health requires the admin role")
		return
	}
	checks := make([]DeepHealthCheck, 0, len(deepHealthCheckNames))
	for _, name := range deepHealthCheckNames {
		detail := deepHealthNotWired
		checks = append(checks, DeepHealthCheck{
			Name:   name,
			OK:     true,
			State:  "disabled",
			Detail: &detail,
		})
	}
	writeJSON(w, http.StatusOK, DeepHealthResponse{Status: "ok", Checks: checks})
}
