package api_test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// The filter-value grammar over the wire (issue #471).
//
// This mirrors, row for row, the Python table in
// python/tests/unit/test_filter_grammar_contract.py. The two implementations
// must answer the same status to the same body, which is the whole point of
// shipping the check here even though Go's handlers are stubs that never READ
// Filters: without it the conformance case for an invalid filter would pass
// vacuously against Go — a stub answering 200 to everything is not agreement,
// and the suite would have to be gated to impl == "python".
//
// The rows are raw request bodies rather than Go values on purpose: what is
// under test is the DECODE-level shape check, including the part Go's default
// decoder cannot express at all (2025 vs 2025.0 — both plain float64 without
// UseNumber).

type filterCase struct {
	name    string
	filters string
}

var refusedFilterBodies = []filterCase{
	// The reported defect: an object value. It was a 500 on Python.
	{"dict-range-operator", `{"year": {"gte": 2025}}`},
	{"dict-on-string-field", `{"doc_type": {"eq": "article"}}`},
	// Floats and nulls are the same latent 500, not a separate leniency:
	// Qdrant's MatchValue refuses them as hard as it refuses an object.
	{"float", `{"score": 1.5}`},
	{"float-that-is-integral", `{"year": 2025.0}`},
	{"exponent-notation", `{"year": 1e3}`},
	{"null", `{"doi": null}`},
	{"float-in-list", `{"score": [1.5]}`},
	{"null-in-list", `{"doi": [null]}`},
	{"nested-list", `{"tags": [["a"]]}`},
	{"dict-in-list", `{"year": [{"gte": 2025}]}`},
	// MatchAny is list[str] | list[int] — a bool element and a mixed list are
	// refused even though both parts are fine on their own.
	{"bool-in-list", `{"is_oa": [true]}`},
	{"mixed-str-int-list", `{"doc_type": ["article", 3]}`},
	// Type mismatch: refused, never coerced.
	{"str-year-scalar", `{"year": "2025"}`},
	{"str-year-in-list", `{"year": ["2025"]}`},
	{"bool-year", `{"year": true}`},
}

var acceptedFilterBodies = []filterCase{
	{"str-scalar", `{"doc_type": "article"}`},
	{"int-scalar", `{"year": 2025}`},
	{"bool-scalar", `{"is_oa": true}`},
	{"str-list", `{"doc_type": ["article", "supplement"]}`},
	{"int-list", `{"year": [2025, 2026]}`},
	// #196: a legal filter that matches nothing, NOT a refusal.
	{"empty-list", `{"doc_type": []}`},
	{"no-filters", `{}`},
	{"multiple-keys", `{"doc_type": "article", "year": 2025, "is_oa": true}`},
}

var filterEndpoints = []string{"/v1/query", "/v1/retrieve"}

func postFilters(t *testing.T, path, filters string) *httptest.ResponseRecorder {
	t.Helper()
	r := newRouter()
	body := `{"query":"anything","filters":` + filters + `}`
	req := httptest.NewRequest(http.MethodPost, path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	return w
}

func detailOf(t *testing.T, w *httptest.ResponseRecorder) string {
	t.Helper()
	var body map[string]any
	if err := json.NewDecoder(w.Body).Decode(&body); err != nil {
		t.Fatalf("response body is not JSON: %v", err)
	}
	detail, ok := body["detail"].(string)
	if !ok {
		t.Fatalf("expected a string `detail`, got %#v", body["detail"])
	}
	return detail
}

func TestUnsupportedFilterValueIsBadRequest(t *testing.T) {
	for _, path := range filterEndpoints {
		for _, tc := range refusedFilterBodies {
			t.Run(path+"/"+tc.name, func(t *testing.T) {
				w := postFilters(t, path, tc.filters)
				if w.Code != http.StatusBadRequest {
					t.Fatalf("filters=%s: expected 400, got %d (%s)",
						tc.filters, w.Code, w.Body.String())
				}
				// Every refusal teaches the whole grammar, so a caller can fix
				// the request from any single 400 — and it says range
				// operators are unsupported rather than silently ignored.
				detail := detailOf(t, w)
				if !strings.Contains(detail, "a string, an integer or a boolean") {
					t.Errorf("detail does not name the grammar: %q", detail)
				}
				if !strings.Contains(detail, "range operators") {
					t.Errorf("detail does not mention range operators: %q", detail)
				}
			})
		}
	}
}

func TestSupportedFilterValuesStillPass(t *testing.T) {
	for _, path := range filterEndpoints {
		for _, tc := range acceptedFilterBodies {
			t.Run(path+"/"+tc.name, func(t *testing.T) {
				w := postFilters(t, path, tc.filters)
				if w.Code != http.StatusOK {
					t.Fatalf("filters=%s: expected 200, got %d (%s)",
						tc.filters, w.Code, w.Body.String())
				}
			})
		}
	}
}

// An integer must survive the round trip as an integer. Without UseNumber every
// JSON number decodes to float64, `2025` and `2025.0` become the same value,
// and the two implementations then disagree about which one is a float. This is
// the test that fails if that decoder option is ever dropped.
func TestIntegerAndFloatAreDistinguished(t *testing.T) {
	if w := postFilters(t, "/v1/query", `{"year": 2025}`); w.Code != http.StatusOK {
		t.Fatalf("integer year should be accepted, got %d (%s)", w.Code, w.Body.String())
	}
	if w := postFilters(t, "/v1/query", `{"year": 2025.0}`); w.Code != http.StatusBadRequest {
		t.Fatalf("float year should be refused, got %d (%s)", w.Code, w.Body.String())
	}
}

// The type-mismatch rule names the field, so `year` cannot quietly leave the
// integer-field table without this going red.
func TestIntegerFieldMismatchNamesTheField(t *testing.T) {
	w := postFilters(t, "/v1/retrieve", `{"year": "2025"}`)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", w.Code)
	}
	detail := detailOf(t, w)
	if !strings.Contains(detail, "year") || !strings.Contains(detail, "integer field") {
		t.Fatalf("detail should name `year` as an integer field, got %q", detail)
	}
}

// A malformed body is still the pre-existing 422, not the new 400 — the filter
// check must not have swallowed the decode failure it sits behind.
func TestMalformedBodyIsStill422(t *testing.T) {
	r := newRouter()
	req := httptest.NewRequest(http.MethodPost, "/v1/query", strings.NewReader(`{"query":`))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	if w.Code != http.StatusUnprocessableEntity {
		t.Fatalf("expected 422 for a malformed body, got %d", w.Code)
	}
}
