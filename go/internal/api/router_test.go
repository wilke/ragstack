package api_test

import (
	"bytes"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"regexp"
	"strings"
	"testing"

	"github.com/ragstack/ragstack/internal/api"
)

func newRouter() http.Handler {
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	return api.NewRouter(logger)
}

// newRouterLogging returns a router whose log lines land in the returned buffer,
// for the tests that assert on what does — and does not — reach the log.
func newRouterLogging() (http.Handler, *bytes.Buffer) {
	var buf bytes.Buffer
	logger := slog.New(slog.NewTextHandler(&buf, nil))
	return api.NewRouter(logger), &buf
}

// ridRE is the format the contract pins at components/headers/XRequestId and
// conformance/test_request_id.py asserts over the wire. Kept literal here rather
// than imported so this test fails if the generator's format drifts.
var ridRE = regexp.MustCompile(`^[0-9a-f]{16}$`)

func doGet(t *testing.T, r http.Handler, path string, headers map[string]string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, path, nil)
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	return w
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

// --- X-Request-Id (#427 W7) ---------------------------------------------------
//
// The Go half of the parity the conformance suite pins over HTTP
// (conformance/test_request_id.py), asserted in-process so most breaks are
// caught by `go test` without a running server.
//
// "Most", not "all", and the gap is worth knowing: httptest.ResponseRecorder's
// Header() is a LIVE map, so a header `Set` performed after the handler chain
// has run is still visible to a later `Get` on it. A real server serialises the
// header block when the response is written and cannot see such a `Set` at all.
// So the recorder-based tests below pin the header's VALUE but not the point at
// which it is stamped — and that point is the whole design (it is what covers
// chi's 404 and Recoverer's panic-500).
// TestRequestIDIsPresentOnAnErrorResponse runs over a real socket for exactly
// that reason; do not "simplify" it back to a recorder.

func TestRequestIDHeaderIsPresentAndWellFormed(t *testing.T) {
	w := doGet(t, newRouter(), "/health", nil)

	rid := w.Header().Get("X-Request-Id")
	if rid == "" {
		t.Fatal("no X-Request-Id header on the response")
	}
	if !ridRE.MatchString(rid) {
		t.Fatalf("X-Request-Id %q does not match %s", rid, ridRE)
	}
}

func TestRequestIDDiffersBetweenRequests(t *testing.T) {
	// The property that makes the id useful at all: two concurrent users'
	// failures must be distinguishable in the log.
	r := newRouter()
	first := doGet(t, r, "/health", nil).Header().Get("X-Request-Id")
	second := doGet(t, r, "/health", nil).Header().Get("X-Request-Id")

	if first == "" || second == "" {
		t.Fatal("missing X-Request-Id")
	}
	if first == second {
		t.Fatalf("two requests received the same X-Request-Id %q", first)
	}
}

func TestInboundRequestIDIsNeverEchoed(t *testing.T) {
	// The server always generates its own id. A caller-supplied one is recorded
	// for gateway correlation but never becomes the response's — otherwise a
	// client could forge an id, or make two requests indistinguishable.
	//
	// This is also the property chi's middleware.RequestID does NOT have: it
	// honours an inbound header verbatim, which is why it is not the source of
	// this header.
	const inbound = "gateway-upstream-id.1"
	r, buf := newRouterLogging()
	w := doGet(t, r, "/health", map[string]string{"X-Request-ID": inbound})

	rid := w.Header().Get("X-Request-Id")
	if rid == inbound {
		t.Fatal("the server echoed a caller-supplied request id")
	}
	if !ridRE.MatchString(rid) {
		t.Fatalf("X-Request-Id %q is not a server-generated id", rid)
	}
	// A VALID inbound id is still recorded, as upstream_rid — that is what makes
	// gateway correlation possible without trusting the caller's value.
	if !strings.Contains(buf.String(), "upstream_rid="+inbound) {
		t.Fatalf("a valid inbound id was not recorded as upstream_rid; log was %q", buf.String())
	}
}

func TestHostileInboundRequestIDIsRejectedAndNeverLogged(t *testing.T) {
	// A value that would forge a log line or flood the log must be dropped — not
	// reflected in the response, and not written to the log in any form.
	cases := map[string]string{
		"newline":     "forged\nlevel=ERROR msg=\"fake line\"",
		"over-length": strings.Repeat("z", 512),
		"space":       "not an id",
	}
	for name, hostile := range cases {
		t.Run(name, func(t *testing.T) {
			r, buf := newRouterLogging()
			w := doGet(t, r, "/health", map[string]string{"X-Request-ID": hostile})

			rid := w.Header().Get("X-Request-Id")
			if !ridRE.MatchString(rid) {
				t.Fatalf("X-Request-Id %q is not a server-generated id", rid)
			}
			if strings.Contains(buf.String(), hostile) {
				t.Fatalf("a rejected inbound id reached the log; log was %q", buf.String())
			}
			if strings.Contains(buf.String(), "upstream_rid") {
				t.Fatalf("a rejected inbound id was recorded as upstream_rid; log was %q", buf.String())
			}
		})
	}
}

func TestRequestIDIsPresentOnAnErrorResponse(t *testing.T) {
	// The whole point: the id has to be on the responses a user reports, not
	// just on the ones that worked. chi's own 404 handler runs at the end of the
	// middleware chain, so stamping the header BEFORE the chain is what covers
	// it — along with Recoverer's panic-500.
	//
	// Over a REAL socket, deliberately, and this is the one test in the file
	// that is: a ResponseRecorder's live header map would let a stamp performed
	// AFTER the chain pass just as happily, so a recorder cannot distinguish the
	// implementation from a mutant of it that breaks every conformance test.
	// This is the in-tree pin for the property the whole PR exists to establish.
	srv := httptest.NewServer(newRouter())
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/v1/this-route-does-not-exist")
	if err != nil {
		t.Fatalf("GET failed: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("expected 404, got %d", resp.StatusCode)
	}
	if rid := resp.Header.Get("X-Request-Id"); !ridRE.MatchString(rid) {
		t.Fatalf("X-Request-Id %q on a 404 is not a server-generated id", rid)
	}
}

func TestValidationErrorBodyCarriesTheRequestID(t *testing.T) {
	// The `{detail, request_id}` shape the Python 503 body uses. Redundant with
	// the header on purpose: a header does not survive a copy-paste into a
	// ticket.
	r := newRouter()
	req := httptest.NewRequest(http.MethodPost, "/v1/query", strings.NewReader(`{}`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusUnprocessableEntity {
		t.Fatalf("expected 422, got %d", w.Code)
	}
	var body map[string]any
	if err := json.NewDecoder(w.Body).Decode(&body); err != nil {
		t.Fatalf("undecodable body: %v", err)
	}
	got, _ := body["request_id"].(string)
	if got == "" {
		t.Fatal("no request_id in the 422 body")
	}
	if want := w.Header().Get("X-Request-Id"); got != want {
		t.Fatalf("body request_id %q != header X-Request-Id %q", got, want)
	}
}

func TestLogLineCarriesTheRequestID(t *testing.T) {
	// LoggingMiddleware was dead code before #427 W7 — NewRouter discarded its
	// logger parameter, and a repo-wide grep for LoggingMiddleware found only
	// its own definition. This asserts it is actually installed AND that the id
	// on the line is the one the client was given.
	r, buf := newRouterLogging()
	w := doGet(t, r, "/health", nil)

	rid := w.Header().Get("X-Request-Id")
	line := buf.String()
	if line == "" {
		t.Fatal("the logging middleware produced no line — is it wired up?")
	}
	if !strings.Contains(line, "request_id="+rid) {
		t.Fatalf("log line does not carry request_id=%s; line was %q", rid, line)
	}
	if !strings.Contains(line, "status=200") || !strings.Contains(line, "path=/health") {
		t.Fatalf("log line is missing method/path/status; line was %q", line)
	}
}
