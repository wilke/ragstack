package mcp

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// connectClient wires an in-memory MCP client to a server built over backend,
// and returns the connected client session.
func connectClient(t *testing.T, backend *Backend) *mcp.ClientSession {
	t.Helper()
	ctx := context.Background()
	server := NewServer(backend)
	clientT, serverT := mcp.NewInMemoryTransports()
	if _, err := server.Connect(ctx, serverT, nil); err != nil {
		t.Fatalf("server connect: %v", err)
	}
	client := mcp.NewClient(&mcp.Implementation{Name: "test-client", Version: "0"}, nil)
	cs, err := client.Connect(ctx, clientT, nil)
	if err != nil {
		t.Fatalf("client connect: %v", err)
	}
	t.Cleanup(func() { cs.Close() })
	return cs
}

func TestServerRegistersThreeTools(t *testing.T) {
	backend := NewBackend(Config{BaseURL: "http://rag.test"}, nil)
	cs := connectClient(t, backend)

	res, err := cs.ListTools(context.Background(), nil)
	if err != nil {
		t.Fatalf("list tools: %v", err)
	}
	got := map[string]bool{}
	for _, tool := range res.Tools {
		got[tool.Name] = true
	}
	for _, want := range []string{"search", "answer", "list_collections"} {
		if !got[want] {
			t.Errorf("missing tool %q; got %v", want, got)
		}
	}
	if len(res.Tools) != 3 {
		t.Errorf("expected exactly 3 tools, got %d", len(res.Tools))
	}
}

// TestSearchToolEndToEnd drives a real MCP CallTool through the SDK against a
// mock RAGStack HTTP server, exercising schema decoding + result formatting.
func TestSearchToolEndToEnd(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/retrieve" {
			t.Errorf("path = %q", r.URL.Path)
		}
		writeJSON(t, w, 200, map[string]any{"sources": []any{
			map[string]any{"doc_id": "d1", "chunk_id": "c1", "content": "hello world", "score": 0.5, "metadata": map[string]any{"title": "Doc"}},
		}})
	}))
	defer srv.Close()

	backend := NewBackend(Config{BaseURL: srv.URL}, srv.Client())
	cs := connectClient(t, backend)

	res, err := cs.CallTool(context.Background(), &mcp.CallToolParams{
		Name:      "search",
		Arguments: map[string]any{"query": "hi", "top_k": 1},
	})
	if err != nil {
		t.Fatalf("call tool: %v", err)
	}
	if len(res.Content) == 0 {
		t.Fatal("no content returned")
	}
	text, ok := res.Content[0].(*mcp.TextContent)
	if !ok {
		t.Fatalf("expected TextContent, got %T", res.Content[0])
	}
	if !strings.Contains(text.Text, "[1] Doc  (score 0.5000)") {
		t.Errorf("unexpected tool output:\n%s", text.Text)
	}
}

func TestListCollectionsToolEndToEnd(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || r.URL.Path != "/v1/collections" {
			t.Errorf("method/path = %s %s", r.Method, r.URL.Path)
		}
		writeJSON(t, w, 200, map[string]any{"default": nil, "collections": []any{}})
	}))
	defer srv.Close()

	backend := NewBackend(Config{BaseURL: srv.URL}, srv.Client())
	cs := connectClient(t, backend)

	res, err := cs.CallTool(context.Background(), &mcp.CallToolParams{Name: "list_collections"})
	if err != nil {
		t.Fatalf("call tool: %v", err)
	}
	text := res.Content[0].(*mcp.TextContent)
	if !strings.Contains(text.Text, "no collections registered") {
		t.Errorf("unexpected output:\n%s", text.Text)
	}
}
