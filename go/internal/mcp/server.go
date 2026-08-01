// This file wires the RAGStack Backend into a Model Context Protocol server
// using the official Go SDK (github.com/modelcontextprotocol/go-sdk). It
// registers three tools — search, answer and list_collections — with
// model-facing descriptions that mirror python/ragstack/mcp/server.py, and
// serves them over the stdio transport that Claude Desktop and Claude Code use
// for local servers.
package mcp

import (
	"context"
	"fmt"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// Version is reported to MCP clients as the server implementation version.
const Version = "0.1.0"

// defaultTopK matches the Python tool signature: when the caller omits top_k
// the server retrieves 5 chunks.
const defaultTopK = 5

const searchDescription = "Retrieve the passages from the user's RAGStack knowledge base that are most " +
	"relevant to a question, WITHOUT generating an answer. Use this whenever you " +
	"need source material to ground a response, to quote or cite from the user's " +
	"documents, or to check what the knowledge base actually says before " +
	"answering. Returns a ranked list of chunks, each with its doc_id, chunk_id, " +
	"a relevance score, and a text snippet you can cite. Prefer this over 'answer' " +
	"when you want to read and reason over the raw sources yourself. " +
	"Args: query (the search text), collection (optional collection id; omit to " +
	"use the server default), top_k (how many chunks to return, default 5)."

const answerDescription = "Ask the user's RAGStack knowledge base a question and get a single grounded " +
	"answer generated from the retrieved passages, along with its sources. Use " +
	"this when the user wants a direct, cited answer synthesized from their " +
	"documents rather than a list of raw passages. If the RAGStack server has no " +
	"LLM configured it cannot generate text; in that case this tool returns the " +
	"retrieved passages plus a note, and you should answer from those. " +
	"Args: query (the question), collection (optional collection id; omit to use " +
	"the server default)."

const listCollectionsDescription = "List the collections available in the user's RAGStack instance, with each " +
	"collection's id, label, embedding model, and chunk counts, plus which one is " +
	"the default. Use this first when you are unsure which collection to query, or " +
	"when the user asks what data or knowledge bases are available. The ids " +
	"returned here are what you pass as the 'collection' argument to 'search' and " +
	"'answer'. Takes no arguments."

// searchInput is the argument schema for the search tool.
type searchInput struct {
	Query      string `json:"query" jsonschema:"the search text"`
	Collection string `json:"collection,omitempty" jsonschema:"optional collection id; omit to use the server default"`
	TopK       int    `json:"top_k,omitempty" jsonschema:"how many chunks to return, default 5"`
}

// answerInput is the argument schema for the answer tool.
type answerInput struct {
	Query      string `json:"query" jsonschema:"the question to answer"`
	Collection string `json:"collection,omitempty" jsonschema:"optional collection id; omit to use the server default"`
}

// listCollectionsInput carries no arguments.
type listCollectionsInput struct{}

// textResult wraps a plain string in the CallToolResult shape MCP clients
// expect.
func textResult(s string) *mcp.CallToolResult {
	return &mcp.CallToolResult{
		Content: []mcp.Content{&mcp.TextContent{Text: s}},
	}
}

// NewServer builds the configured MCP server with the three tools wired to the
// given backend.
func NewServer(backend *Backend) *mcp.Server {
	cfg := backend.Config()
	instructions := fmt.Sprintf(
		"Tools to query a RAGStack retrieval-augmented-generation knowledge base at %s",
		cfg.BaseURL,
	)
	if cfg.Collection != "" {
		instructions += fmt.Sprintf(" (default collection: %s).", cfg.Collection)
	} else {
		instructions += "."
	}
	instructions += " Use 'list_collections' to discover collections, 'search' to fetch " +
		"raw source passages, and 'answer' for a grounded synthesized answer."

	server := mcp.NewServer(
		&mcp.Implementation{Name: "ragstack", Version: Version},
		&mcp.ServerOptions{Instructions: instructions},
	)

	mcp.AddTool(server, &mcp.Tool{Name: "search", Description: searchDescription},
		func(ctx context.Context, _ *mcp.CallToolRequest, in searchInput) (*mcp.CallToolResult, any, error) {
			topK := in.TopK
			if topK == 0 {
				topK = defaultTopK
			}
			return textResult(backend.Search(ctx, in.Query, in.Collection, topK)), nil, nil
		})

	mcp.AddTool(server, &mcp.Tool{Name: "answer", Description: answerDescription},
		func(ctx context.Context, _ *mcp.CallToolRequest, in answerInput) (*mcp.CallToolResult, any, error) {
			return textResult(backend.Answer(ctx, in.Query, in.Collection)), nil, nil
		})

	mcp.AddTool(server, &mcp.Tool{Name: "list_collections", Description: listCollectionsDescription},
		func(ctx context.Context, _ *mcp.CallToolRequest, _ listCollectionsInput) (*mcp.CallToolResult, any, error) {
			return textResult(backend.ListCollections(ctx)), nil, nil
		})

	return server
}

// Serve runs the server over the stdio transport until the client disconnects
// or ctx is cancelled.
func Serve(ctx context.Context, backend *Backend) error {
	return NewServer(backend).Run(ctx, &mcp.StdioTransport{})
}
