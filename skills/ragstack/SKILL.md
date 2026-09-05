---
name: ragstack
description: >-
  Query the user's RAGStack retrieval-augmented-generation knowledge base.
  Use whenever the user asks a question that should be answered from their
  indexed documents/collections, asks what data or knowledge bases are
  available, or wants cited passages from their corpus rather than the model's
  own knowledge. Backed by the bundled `ragstack` MCP server.
---

# RAGStack knowledge base

This skill routes questions to the `ragstack` MCP server, which talks to a
RAGStack RAG API (default `http://localhost:8030`, default collection
`demo_g1_sfr_tok512`). Prefer these tools over answering from memory whenever the
user is asking about *their* documents.

## Tools

- **`list_collections`** — no arguments. Returns each collection's id, label,
  embedding model, and chunk counts, and marks the default. Call this first when
  you don't know which collection to use, or when the user asks what data is
  available. The `id` values are what you pass as `collection` to the other
  tools.
- **`search`** — `query` (required), `collection` (optional; omit for default),
  `top_k` (optional integer, default 5). Returns the most relevant raw passages
  **without** generating an answer. Use when the user wants to see source
  passages, when you want to inspect evidence before answering, or when you will
  synthesize across several chunks yourself.
- **`answer`** — `query` (required), `collection` (optional). Returns a single
  grounded answer synthesized from retrieved passages, with sources. Use when
  the user wants a direct, cited answer. If the server has no LLM configured, it
  returns the retrieved passages plus a note — in that case answer from those
  passages yourself.

## How to choose

1. Unsure which collection? → `list_collections`.
2. User wants a direct answer → `answer`.
3. User wants evidence / passages, or you need to reason across chunks → `search`
   (raise `top_k` for broader recall).

## Grounding rules

- Only assert what the retrieved passages support. If they don't cover the
  question, say the knowledge base doesn't have it — do not fill gaps from your
  own knowledge without flagging that you're doing so.
- Always cite sources: collection id plus the document/chunk identifiers the
  tools return.

## Configuration

The server reads these environment variables (set in the plugin's `.mcp.json`,
overridable in the user's environment):

- `RAGSTACK_BASE_URL` — RAGStack API base URL (default `http://localhost:8030`).
- `RAGSTACK_COLLECTION` — default collection id (default `demo_g1_sfr_tok512`).
- `RAGSTACK_API_KEY` — optional; sent as `X-API-Key` when set.

If tool calls fail to connect, the RAGStack API at `RAGSTACK_BASE_URL` is
probably not running — tell the user rather than guessing an answer.
