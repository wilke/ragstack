// Package mcp implements a standalone Model Context Protocol server that
// exposes a running RAGStack HTTP API to an MCP client (Claude Desktop or
// Claude Code) over stdio.
//
// This file holds the HTTP wrapper and result formatting. It depends only on
// the standard library — no MCP SDK — so the tool logic can be unit-tested
// with net/http/httptest and no live server. The three public methods
// (Backend.Search, Backend.Answer, Backend.ListCollections) each return a
// ready-to-display string and never surface a stack trace: they turn transport
// and API failures into a clear, actionable message. Mirrors the Python server
// in python/ragstack/mcp/backend.py.
package mcp

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"syscall"
	"time"
)

// snippetChars is the truncated snippet length for a retrieved chunk — long
// enough to judge relevance and cite, short enough to keep output scannable.
const snippetChars = 280

// Prefixes the /v1/query endpoint uses for a non-generated (retrieval-only)
// answer — no LLM configured, or generation failed. Kept in sync with
// ragstack.api.routers.query._fallback_answer.
const (
	llmNotConfigured = "[LLM not configured]"
	generationFailed = "[answer generation failed]"
)

// Config holds connection settings for a RAGStack instance, read from the
// environment:
//
//   - RAGSTACK_BASE_URL — base URL of the API (e.g. http://localhost:8030);
//     defaults to http://localhost:8000.
//   - RAGSTACK_API_KEY — optional; sent as the X-API-Key header when set.
//   - RAGSTACK_COLLECTION — optional default collection id for tools that
//     accept a collection argument.
type Config struct {
	BaseURL    string
	APIKey     string
	Collection string
}

// DefaultBaseURL is used when RAGSTACK_BASE_URL is unset or empty.
const DefaultBaseURL = "http://localhost:8000"

// ConfigFromEnv reads the configuration from the given lookup function
// (typically os.Getenv). The base URL is defaulted and stripped of any
// trailing slash so path joining is safe.
func ConfigFromEnv(getenv func(string) string) Config {
	base := getenv("RAGSTACK_BASE_URL")
	if base == "" {
		base = DefaultBaseURL
	}
	base = strings.TrimRight(base, "/")
	return Config{
		BaseURL:    base,
		APIKey:     getenv("RAGSTACK_API_KEY"),
		Collection: getenv("RAGSTACK_COLLECTION"),
	}
}

// Backend is a thin client over the RAGStack HTTP API used by the MCP tools.
// The *http.Client is injectable for tests. Each public method returns a
// display string and handles transport/HTTP errors internally.
type Backend struct {
	cfg    Config
	client *http.Client
}

// NewBackend constructs a Backend. If client is nil a default client with
// sensible timeouts is used.
func NewBackend(cfg Config, client *http.Client) *Backend {
	if client == nil {
		client = &http.Client{Timeout: 60 * time.Second}
	}
	return &Backend{cfg: cfg, client: client}
}

// Config returns the connection settings this backend was built with.
func (b *Backend) Config() Config { return b.cfg }

func (b *Backend) resolveCollection(collection string) string {
	if collection != "" {
		return collection
	}
	return b.cfg.Collection
}

// request performs an HTTP round-trip. It returns the response on any completed
// round-trip (any status code), or a non-empty connErr message when the server
// could not be reached at all. The API key, if any, is never logged.
func (b *Backend) request(
	ctx context.Context, method, path string, body any,
) (resp *http.Response, connErr string) {
	url := b.cfg.BaseURL + path

	var reader io.Reader
	if body != nil {
		buf, err := json.Marshal(body)
		if err != nil {
			return nil, fmt.Sprintf("Failed to encode request to RAGStack: %v", err)
		}
		reader = bytes.NewReader(buf)
	}

	req, err := http.NewRequestWithContext(ctx, method, url, reader)
	if err != nil {
		return nil, fmt.Sprintf("Failed to build request to RAGStack at %s: %v", b.cfg.BaseURL, err)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if b.cfg.APIKey != "" {
		req.Header.Set("X-API-Key", b.cfg.APIKey)
	}

	resp, err = b.client.Do(req)
	if err != nil {
		if errors.Is(err, syscall.ECONNREFUSED) {
			return nil, fmt.Sprintf(
				"Cannot reach RAGStack at %s (connection refused). "+
					"Is the server running and is RAGSTACK_BASE_URL correct?",
				b.cfg.BaseURL,
			)
		}
		return nil, fmt.Sprintf("Request to RAGStack at %s failed: %v", b.cfg.BaseURL, err)
	}
	return resp, ""
}

// httpErrorMessage builds a concise message for a non-2xx response, pulling the
// API's "detail" field when present.
func httpErrorMessage(status int, raw []byte) string {
	detail := extractDetail(raw)
	switch status {
	case http.StatusUnauthorized, http.StatusForbidden:
		return fmt.Sprintf("RAGStack rejected the request (%d). Check RAGSTACK_API_KEY.", status)
	case http.StatusServiceUnavailable:
		if detail != "" {
			return fmt.Sprintf("RAGStack is unavailable (503): %s.", detail)
		}
		return "RAGStack is unavailable (503)."
	}
	if detail != "" {
		return fmt.Sprintf("RAGStack returned HTTP %d: %s.", status, detail)
	}
	return fmt.Sprintf("RAGStack returned HTTP %d.", status)
}

// extractDetail pulls the "detail" field from a JSON error body, matching the
// Python behaviour: a string is used verbatim, any other truthy value is
// stringified, and an absent/empty value yields "".
func extractDetail(raw []byte) string {
	var body map[string]any
	if err := json.Unmarshal(raw, &body); err != nil {
		return ""
	}
	d, ok := body["detail"]
	if !ok || d == nil {
		return ""
	}
	if s, ok := d.(string); ok {
		return s
	}
	return fmt.Sprint(d)
}

// Search retrieves the top-k chunks for query via POST /v1/retrieve. topK is
// clamped to the range [1, 50] (the caller supplies the default of 5).
func (b *Backend) Search(ctx context.Context, query, collection string, topK int) string {
	if strings.TrimSpace(query) == "" {
		return "No query provided. Pass a non-empty 'query'."
	}
	if topK < 1 {
		topK = 1
	}
	if topK > 50 {
		topK = 50
	}
	coll := b.resolveCollection(collection)

	payload := map[string]any{"query": query, "top_k": topK}
	if coll != "" {
		payload["collection"] = coll
	}

	resp, connErr := b.request(ctx, http.MethodPost, "/v1/retrieve", payload)
	if connErr != "" {
		return connErr
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 400 {
		return httpErrorMessage(resp.StatusCode, raw)
	}

	var body struct {
		Sources []source `json:"sources"`
	}
	if err := json.Unmarshal(raw, &body); err != nil {
		return "RAGStack returned a malformed response for /v1/retrieve."
	}

	where := ""
	if coll != "" {
		where = fmt.Sprintf(" in collection '%s'", coll)
	}
	if len(body.Sources) == 0 {
		return fmt.Sprintf("No chunks matched %q%s.", query, where)
	}
	header := fmt.Sprintf("Top %d chunks for %q%s:\n", len(body.Sources), query, where)
	return header + "\n" + formatSources(body.Sources)
}

// Answer returns a full RAG answer for query via POST /v1/query (retrieval +
// LLM generation). It degrades to a clear note when no LLM is configured or
// generation failed, surfacing the retrieved chunks instead.
func (b *Backend) Answer(ctx context.Context, query, collection string) string {
	if strings.TrimSpace(query) == "" {
		return "No query provided. Pass a non-empty 'query'."
	}
	coll := b.resolveCollection(collection)

	payload := map[string]any{"query": query}
	if coll != "" {
		payload["collection"] = coll
	}

	resp, connErr := b.request(ctx, http.MethodPost, "/v1/query", payload)
	if connErr != "" {
		return connErr
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 400 {
		return httpErrorMessage(resp.StatusCode, raw)
	}

	var body struct {
		Answer  string   `json:"answer"`
		Sources []source `json:"sources"`
	}
	if err := json.Unmarshal(raw, &body); err != nil {
		return "RAGStack returned a malformed response for /v1/query."
	}

	answerText := strings.TrimSpace(body.Answer)
	note := ""
	switch {
	case strings.HasPrefix(answerText, llmNotConfigured):
		note = "Note: no LLM is configured on this RAGStack server, so no " +
			"answer was generated. The relevant chunks are listed below — " +
			"use them (or the 'search' tool) to answer directly."
		answerText = ""
	case strings.HasPrefix(answerText, generationFailed):
		note = "Note: answer generation failed on the RAGStack server; " +
			"returning the retrieved chunks only."
		answerText = ""
	}

	var parts []string
	if note != "" {
		parts = append(parts, note)
	}
	if answerText != "" {
		parts = append(parts, "Answer:\n"+answerText)
	}
	if len(body.Sources) > 0 {
		parts = append(parts, "Sources:\n"+formatSources(body.Sources))
	} else if answerText == "" {
		parts = append(parts, fmt.Sprintf("No relevant chunks were found for %q.", query))
	}
	return strings.Join(parts, "\n\n")
}

// ListCollections lists queryable collections via GET /v1/collections.
func (b *Backend) ListCollections(ctx context.Context) string {
	resp, connErr := b.request(ctx, http.MethodGet, "/v1/collections", nil)
	if connErr != "" {
		return connErr
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 400 {
		return httpErrorMessage(resp.StatusCode, raw)
	}

	var body struct {
		Default     *string      `json:"default"`
		Collections []collection `json:"collections"`
	}
	if err := json.Unmarshal(raw, &body); err != nil {
		return "RAGStack returned a malformed response for /v1/collections."
	}
	if len(body.Collections) == 0 {
		return "This RAGStack instance has no collections registered."
	}

	def := "None"
	if body.Default != nil {
		def = *body.Default
	}
	lines := []string{fmt.Sprintf("Available collections (default: %s):", def), ""}
	for _, c := range body.Collections {
		cid := "?"
		if c.ID != "" {
			cid = c.ID
		}
		head := "- " + cid
		if c.Default {
			head += " [default]"
		}
		if c.Label != "" {
			head += fmt.Sprintf("  %q", c.Label)
		}
		lines = append(lines, head)

		model := "?"
		if c.Model != "" {
			model = c.Model
		}
		var counts string
		if c.Count != nil {
			counts = fmt.Sprintf("%d vector chunks", *c.Count)
		} else {
			counts = "vector count n/a"
		}
		if c.TextCount != nil {
			counts += fmt.Sprintf(", %d text chunks", *c.TextCount)
		} else {
			counts += ", text count n/a"
		}
		lines = append(lines, fmt.Sprintf("    model: %s;  %s", model, counts))
	}
	return strings.Join(lines, "\n")
}

// source is a single retrieved chunk in a /v1/retrieve or /v1/query response.
type source struct {
	DocID    any            `json:"doc_id"`
	ChunkID  any            `json:"chunk_id"`
	Content  string         `json:"content"`
	Score    *float64       `json:"score"`
	Metadata map[string]any `json:"metadata"`
}

// collection is a single entry in a /v1/collections response.
type collection struct {
	ID        string `json:"id"`
	Label     string `json:"label"`
	Model     string `json:"model"`
	Default   bool   `json:"default"`
	Count     *int   `json:"count"`
	TextCount *int   `json:"text_count"`
}

// titleFor returns a human label for a source, matching the UI precedence
// (title → filename → source_path → doi → doc_id).
func titleFor(meta map[string]any, docID string) string {
	for _, key := range []string{"title", "filename", "source_path", "doi"} {
		if v, ok := meta[key]; ok && v != nil {
			if s := fmt.Sprint(v); s != "" {
				return s
			}
		}
	}
	return docID
}

// snippet collapses whitespace in text and truncates to limit runes, appending
// an ellipsis when truncated.
func snippet(text string, limit int) string {
	flat := strings.Join(strings.Fields(text), " ")
	r := []rune(flat)
	if len(r) <= limit {
		return flat
	}
	return strings.TrimRight(string(r[:limit-1]), " ") + "…"
}

func idString(v any) string {
	if v == nil {
		return "?"
	}
	if s, ok := v.(string); ok {
		return s
	}
	// JSON numbers decode to float64; render integers without a trailing ".0".
	if f, ok := v.(float64); ok && f == float64(int64(f)) {
		return fmt.Sprintf("%d", int64(f))
	}
	return fmt.Sprint(v)
}

func formatSources(sources []source) string {
	var lines []string
	for i, s := range sources {
		docID := idString(s.DocID)
		chunkID := idString(s.ChunkID)
		title := titleFor(s.Metadata, docID)
		scoreStr := "n/a"
		if s.Score != nil {
			scoreStr = fmt.Sprintf("%.4f", *s.Score)
		}
		lines = append(lines, fmt.Sprintf("[%d] %s  (score %s)", i+1, title, scoreStr))
		lines = append(lines, fmt.Sprintf("    doc_id: %s  chunk_id: %s", docID, chunkID))
		if s.Content != "" {
			lines = append(lines, "    "+snippet(s.Content, snippetChars))
		}
	}
	return strings.Join(lines, "\n")
}
