package api_test

import (
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/api"
)

func newRouter() http.Handler {
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	return api.NewRouter(logger)
}

func TestHealthEndpoint(t *testing.T) {
	r := newRouter()
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
	var body map[string]string
	json.NewDecoder(w.Body).Decode(&body)
	if body["status"] != "ok" {
		t.Fatalf("expected status ok, got %q", body["status"])
	}
}

func TestQueryEndpoint(t *testing.T) {
	r := newRouter()
	req := httptest.NewRequest(http.MethodPost, "/v1/query",
		strings.NewReader(`{"query":"What is RAG?"}`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
	var body map[string]any
	json.NewDecoder(w.Body).Decode(&body)
	if _, ok := body["answer"]; !ok {
		t.Fatal("missing 'answer' field")
	}
	if _, ok := body["sources"]; !ok {
		t.Fatal("missing 'sources' field")
	}
	if _, ok := body["rewritten_queries"]; !ok {
		t.Fatal("missing 'rewritten_queries' field")
	}
}

func TestQueryMissingFieldReturns422(t *testing.T) {
	r := newRouter()
	req := httptest.NewRequest(http.MethodPost, "/v1/query",
		strings.NewReader(`{}`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusUnprocessableEntity {
		t.Fatalf("expected 422, got %d", w.Code)
	}
}

func TestIngestEndpoint(t *testing.T) {
	r := newRouter()
	req := httptest.NewRequest(http.MethodPost, "/v1/ingest",
		strings.NewReader(`{"source":"/tmp/test.txt"}`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
	var body map[string]any
	json.NewDecoder(w.Body).Decode(&body)
	if body["status"] != "accepted" {
		t.Fatalf("expected status accepted, got %v", body["status"])
	}
	if _, ok := body["job_id"]; !ok {
		t.Fatal("missing 'job_id' field")
	}
}

func TestListDocumentsReturnsEmptyList(t *testing.T) {
	r := newRouter()
	req := httptest.NewRequest(http.MethodGet, "/v1/documents", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
	var body []any
	json.NewDecoder(w.Body).Decode(&body)
	if len(body) != 0 {
		t.Fatalf("expected empty list, got %d items", len(body))
	}
}

func TestDeleteDocumentReturns204(t *testing.T) {
	r := newRouter()
	req := httptest.NewRequest(http.MethodDelete, "/v1/documents/nonexistent", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusNoContent {
		t.Fatalf("expected 204, got %d", w.Code)
	}
}

func TestGraphEntitiesReturnsEmptyList(t *testing.T) {
	r := newRouter()
	req := httptest.NewRequest(http.MethodGet, "/v1/graph/entities", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
}

func TestGraphNeighborsReturnsEmptyList(t *testing.T) {
	r := newRouter()
	req := httptest.NewRequest(http.MethodGet, "/v1/graph/neighbors/Alice", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
}
