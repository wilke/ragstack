package mcp

import (
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// handlerFunc lets a test inspect the request and craft a response.
type handlerFunc func(w http.ResponseWriter, r *http.Request)

// newBackend wires a Backend to an httptest server running handler. The caller
// must Close the returned server.
func newBackend(t *testing.T, cfg Config, handler handlerFunc) (*Backend, *httptest.Server) {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(handler))
	cfg.BaseURL = srv.URL
	return NewBackend(cfg, srv.Client()), srv
}

func writeJSON(t *testing.T, w http.ResponseWriter, status int, body any) {
	t.Helper()
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(body); err != nil {
		t.Fatalf("encode response: %v", err)
	}
}

func decodeBody(t *testing.T, r *http.Request) map[string]any {
	t.Helper()
	raw, err := io.ReadAll(r.Body)
	if err != nil {
		t.Fatalf("read body: %v", err)
	}
	var m map[string]any
	if len(raw) > 0 {
		if err := json.Unmarshal(raw, &m); err != nil {
			t.Fatalf("decode body: %v", err)
		}
	}
	return m
}

// --------------------------------------------------------------------------- //
// Config
// --------------------------------------------------------------------------- //
func TestConfigFromEnvReadsAndStripsTrailingSlash(t *testing.T) {
	env := map[string]string{
		"RAGSTACK_BASE_URL":   "http://localhost:8030/",
		"RAGSTACK_API_KEY":    "secret",
		"RAGSTACK_COLLECTION": "papers",
	}
	cfg := ConfigFromEnv(func(k string) string { return env[k] })
	if cfg.BaseURL != "http://localhost:8030" {
		t.Errorf("base url = %q", cfg.BaseURL)
	}
	if cfg.APIKey != "secret" || cfg.Collection != "papers" {
		t.Errorf("cfg = %+v", cfg)
	}
}

func TestConfigFromEnvDefaults(t *testing.T) {
	cfg := ConfigFromEnv(func(string) string { return "" })
	if cfg.BaseURL != DefaultBaseURL {
		t.Errorf("base url = %q, want default", cfg.BaseURL)
	}
	if cfg.APIKey != "" || cfg.Collection != "" {
		t.Errorf("expected empty api key/collection, got %+v", cfg)
	}
}

// --------------------------------------------------------------------------- //
// search  ->  POST /v1/retrieve
// --------------------------------------------------------------------------- //
func TestSearchFormatsRankedChunksWithTitles(t *testing.T) {
	var gotPath string
	var gotBody map[string]any
	backend, srv := newBackend(t, Config{}, func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		gotBody = decodeBody(t, r)
		writeJSON(t, w, 200, map[string]any{"sources": []any{
			map[string]any{"doc_id": "d1", "chunk_id": "c1", "content": "Paris is the capital of France.", "score": 0.91, "metadata": map[string]any{"title": "Geo"}},
			map[string]any{"doc_id": "d2", "chunk_id": "c2", "content": "It has about two million people.", "score": 0.42, "metadata": map[string]any{"filename": "pop.txt"}},
		}})
	})
	defer srv.Close()

	out := backend.Search(context.Background(), "capital of France", "", 2)
	if gotPath != "/v1/retrieve" {
		t.Errorf("path = %q", gotPath)
	}
	if gotBody["query"] != "capital of France" || gotBody["top_k"].(float64) != 2 {
		t.Errorf("body = %+v", gotBody)
	}
	if _, ok := gotBody["collection"]; ok {
		t.Errorf("collection should be omitted, body = %+v", gotBody)
	}
	for _, want := range []string{
		"[1] Geo  (score 0.9100)",
		"[2] pop.txt  (score 0.4200)",
		"doc_id: d1  chunk_id: c1",
		"Paris is the capital of France.",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("output missing %q:\n%s", want, out)
		}
	}
}

func TestSearchUsesDefaultCollectionAndOverride(t *testing.T) {
	var seen []map[string]any
	backend, srv := newBackend(t, Config{Collection: "default_coll"}, func(w http.ResponseWriter, r *http.Request) {
		seen = append(seen, decodeBody(t, r))
		writeJSON(t, w, 200, map[string]any{"sources": []any{}})
	})
	defer srv.Close()

	backend.Search(context.Background(), "q", "", 5)
	backend.Search(context.Background(), "q", "other", 5)
	if seen[0]["collection"] != "default_coll" {
		t.Errorf("expected default_coll, got %v", seen[0]["collection"])
	}
	if seen[1]["collection"] != "other" {
		t.Errorf("expected override 'other', got %v", seen[1]["collection"])
	}
}

func TestSearchEmptyResultsIsFriendly(t *testing.T) {
	backend, srv := newBackend(t, Config{}, func(w http.ResponseWriter, r *http.Request) {
		writeJSON(t, w, 200, map[string]any{"sources": []any{}})
	})
	defer srv.Close()

	out := backend.Search(context.Background(), "nothing here", "", 5)
	if !strings.Contains(out, "No chunks matched") || !strings.Contains(out, "nothing here") {
		t.Errorf("unexpected output: %s", out)
	}
}

func TestSearchClampsTopK(t *testing.T) {
	var seen []map[string]any
	backend, srv := newBackend(t, Config{}, func(w http.ResponseWriter, r *http.Request) {
		seen = append(seen, decodeBody(t, r))
		writeJSON(t, w, 200, map[string]any{"sources": []any{}})
	})
	defer srv.Close()

	backend.Search(context.Background(), "q", "", 999)
	backend.Search(context.Background(), "q", "", 0)
	if seen[0]["top_k"].(float64) != 50 {
		t.Errorf("expected clamp to 50, got %v", seen[0]["top_k"])
	}
	if seen[1]["top_k"].(float64) != 1 {
		t.Errorf("expected clamp to 1, got %v", seen[1]["top_k"])
	}
}

// --------------------------------------------------------------------------- //
// answer  ->  POST /v1/query
// --------------------------------------------------------------------------- //
func TestAnswerReturnsGeneratedAnswerAndSources(t *testing.T) {
	backend, srv := newBackend(t, Config{}, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/query" {
			t.Errorf("path = %q", r.URL.Path)
		}
		writeJSON(t, w, 200, map[string]any{
			"answer": "The capital of France is Paris.",
			"sources": []any{
				map[string]any{"doc_id": "d1", "chunk_id": "c1", "content": "France's capital is Paris.", "score": 0.9, "metadata": map[string]any{"title": "Geo"}},
			},
			"rewritten_queries": []any{"capital of France"},
		})
	})
	defer srv.Close()

	out := backend.Answer(context.Background(), "What is the capital of France?", "")
	for _, want := range []string{
		"Answer:\nThe capital of France is Paris.",
		"Sources:",
		"[1] Geo  (score 0.9000)",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("output missing %q:\n%s", want, out)
		}
	}
}

func TestAnswerHandlesLLMNotConfiguredPlaceholder(t *testing.T) {
	backend, srv := newBackend(t, Config{}, func(w http.ResponseWriter, r *http.Request) {
		writeJSON(t, w, 200, map[string]any{
			"answer":  "[LLM not configured] retrieved 1 chunks for query 'q'; top score 0.9000",
			"sources": []any{map[string]any{"doc_id": "d1", "chunk_id": "c1", "content": "some passage", "score": 0.9, "metadata": map[string]any{}}},
		})
	})
	defer srv.Close()

	out := backend.Answer(context.Background(), "q", "")
	if !strings.Contains(out, "no LLM is configured") {
		t.Errorf("expected LLM note: %s", out)
	}
	if strings.Contains(out, "Answer:") {
		t.Errorf("placeholder must not be shown as an answer: %s", out)
	}
	if !strings.Contains(out, "Sources:") || !strings.Contains(out, "some passage") {
		t.Errorf("sources should still be surfaced: %s", out)
	}
}

func TestAnswerHandlesGenerationFailedPlaceholder(t *testing.T) {
	backend, srv := newBackend(t, Config{}, func(w http.ResponseWriter, r *http.Request) {
		writeJSON(t, w, 200, map[string]any{
			"answer":  "[answer generation failed] retrieved 1 chunks for query 'q'; top score 0.5000",
			"sources": []any{map[string]any{"doc_id": "d1", "chunk_id": "c1", "content": "passage", "score": 0.5, "metadata": map[string]any{}}},
		})
	})
	defer srv.Close()

	out := backend.Answer(context.Background(), "q", "")
	if !strings.Contains(out, "answer generation failed") {
		t.Errorf("expected failure note: %s", out)
	}
	if strings.Contains(out, "Answer:") {
		t.Errorf("placeholder must not be shown as an answer: %s", out)
	}
	if !strings.Contains(out, "passage") {
		t.Errorf("sources should still be surfaced: %s", out)
	}
}

// --------------------------------------------------------------------------- //
// list_collections  ->  GET /v1/collections
// --------------------------------------------------------------------------- //
func TestListCollectionsFormatsEntries(t *testing.T) {
	backend, srv := newBackend(t, Config{}, func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || r.URL.Path != "/v1/collections" {
			t.Errorf("method/path = %s %s", r.Method, r.URL.Path)
		}
		writeJSON(t, w, 200, map[string]any{
			"default": "papers",
			"collections": []any{
				map[string]any{"id": "papers", "label": "Research papers", "model": "bge-large", "dim": 1024, "default": true, "count": 1200, "text_count": 1200},
				map[string]any{"id": "notes", "label": "", "model": "sfr", "dim": 4096, "default": false, "count": 30, "text_count": nil},
			},
		})
	})
	defer srv.Close()

	out := backend.ListCollections(context.Background())
	for _, want := range []string{
		"Available collections (default: papers):",
		"- papers [default]",
		`"Research papers"`,
		"1200 vector chunks",
		"- notes",
		"text count n/a",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("output missing %q:\n%s", want, out)
		}
	}
}

func TestListCollectionsEmpty(t *testing.T) {
	backend, srv := newBackend(t, Config{}, func(w http.ResponseWriter, r *http.Request) {
		writeJSON(t, w, 200, map[string]any{"collections": []any{}, "default": nil})
	})
	defer srv.Close()

	out := backend.ListCollections(context.Background())
	if !strings.Contains(out, "no collections registered") {
		t.Errorf("unexpected output: %s", out)
	}
}

// --------------------------------------------------------------------------- //
// Error handling: never a stack trace
// --------------------------------------------------------------------------- //
func TestConnectionRefusedIsClearMessage(t *testing.T) {
	// Bind then immediately close a listener to get a port nothing listens on.
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	addr := l.Addr().String()
	l.Close()

	backend := NewBackend(Config{BaseURL: "http://" + addr}, nil)
	out := backend.Search(context.Background(), "q", "", 5)
	if !strings.Contains(out, "Cannot reach RAGStack") || !strings.Contains(out, addr) {
		t.Errorf("expected connection-refused message, got: %s", out)
	}
}

func TestHTTP500SurfacesDetail(t *testing.T) {
	backend, srv := newBackend(t, Config{}, func(w http.ResponseWriter, r *http.Request) {
		writeJSON(t, w, 500, map[string]any{"detail": "boom"})
	})
	defer srv.Close()

	out := backend.Answer(context.Background(), "q", "")
	if !strings.Contains(out, "HTTP 500") || !strings.Contains(out, "boom") {
		t.Errorf("expected detail surfaced: %s", out)
	}
}

func TestHTTP401PointsAtAPIKey(t *testing.T) {
	backend, srv := newBackend(t, Config{}, func(w http.ResponseWriter, r *http.Request) {
		writeJSON(t, w, 401, map[string]any{"detail": "unauthorized"})
	})
	defer srv.Close()

	out := backend.ListCollections(context.Background())
	if !strings.Contains(out, "401") || !strings.Contains(out, "RAGSTACK_API_KEY") {
		t.Errorf("expected api-key hint: %s", out)
	}
}

func Test503IsGraceful(t *testing.T) {
	backend, srv := newBackend(t, Config{}, func(w http.ResponseWriter, r *http.Request) {
		writeJSON(t, w, 503, map[string]any{"detail": "llm backend down"})
	})
	defer srv.Close()

	out := backend.Answer(context.Background(), "q", "")
	if !strings.Contains(out, "503") || !strings.Contains(out, "unavailable") {
		t.Errorf("expected graceful 503: %s", out)
	}
}

func TestAPIKeySentAsHeaderWhenConfigured(t *testing.T) {
	var seen string
	var present bool
	backend, srv := newBackend(t, Config{APIKey: "s3cr3t"}, func(w http.ResponseWriter, r *http.Request) {
		seen = r.Header.Get("X-API-Key")
		_, present = r.Header["X-Api-Key"]
		writeJSON(t, w, 200, map[string]any{"sources": []any{}})
	})
	defer srv.Close()

	backend.Search(context.Background(), "q", "", 5)
	if seen != "s3cr3t" || !present {
		t.Errorf("expected X-API-Key header 's3cr3t', got %q present=%v", seen, present)
	}
}

func TestNoAPIKeyHeaderWhenAbsent(t *testing.T) {
	var present bool
	backend, srv := newBackend(t, Config{}, func(w http.ResponseWriter, r *http.Request) {
		_, present = r.Header["X-Api-Key"]
		writeJSON(t, w, 200, map[string]any{"sources": []any{}})
	})
	defer srv.Close()

	backend.Search(context.Background(), "q", "", 5)
	if present {
		t.Errorf("X-API-Key header should be absent when no key configured")
	}
}

func TestEmptyQueryShortCircuitsWithoutCall(t *testing.T) {
	calls := 0
	backend, srv := newBackend(t, Config{}, func(w http.ResponseWriter, r *http.Request) {
		calls++
		writeJSON(t, w, 200, map[string]any{"sources": []any{}})
	})
	defer srv.Close()

	if out := backend.Search(context.Background(), "   ", "", 5); !strings.Contains(out, "No query") {
		t.Errorf("search: %s", out)
	}
	if out := backend.Answer(context.Background(), "", ""); !strings.Contains(out, "No query") {
		t.Errorf("answer: %s", out)
	}
	if calls != 0 {
		t.Errorf("expected no HTTP calls, got %d", calls)
	}
}

func TestMalformedResponseIsHandled(t *testing.T) {
	backend, srv := newBackend(t, Config{}, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(200)
		io.WriteString(w, "not json")
	})
	defer srv.Close()

	out := backend.Search(context.Background(), "q", "", 5)
	if !strings.Contains(out, "malformed response") {
		t.Errorf("expected malformed-response message: %s", out)
	}
}

func TestSnippetTruncates(t *testing.T) {
	long := strings.Repeat("a", 400)
	got := snippet(long, snippetChars)
	if !strings.HasSuffix(got, "…") {
		t.Errorf("expected ellipsis suffix, got %q", got)
	}
	if len([]rune(got)) != snippetChars {
		t.Errorf("expected %d runes, got %d", snippetChars, len([]rune(got)))
	}
}

func TestTitlePrecedence(t *testing.T) {
	cases := []struct {
		meta map[string]any
		want string
	}{
		{map[string]any{"title": "T", "filename": "F"}, "T"},
		{map[string]any{"filename": "F", "source_path": "S"}, "F"},
		{map[string]any{"source_path": "S", "doi": "D"}, "S"},
		{map[string]any{"doi": "D"}, "D"},
		{map[string]any{}, "doc-1"},
	}
	for _, c := range cases {
		if got := titleFor(c.meta, "doc-1"); got != c.want {
			t.Errorf("titleFor(%v) = %q, want %q", c.meta, got, c.want)
		}
	}
}
