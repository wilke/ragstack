# RAGStack MCP server

A standalone [Model Context Protocol](https://modelcontextprotocol.io) server
that lets Claude (Claude Desktop or Claude Code) answer questions from a running
**RAGStack** instance. It wraps RAGStack's HTTP API and exposes three tools over
the stdio transport.

It talks to a RAGStack server you already have running (e.g. `make run-python`,
or a deployed instance) — it does not embed or start one.

## Tools

| Tool | RAGStack endpoint | What it does |
|---|---|---|
| `search` | `POST /v1/retrieve` | Hybrid retrieval only. Returns the ranked chunks (doc_id, title, score, snippet) so Claude can read and cite the raw sources. Args: `query`, `collection?`, `top_k?` (default 5). |
| `answer` | `POST /v1/query` | Full RAG: retrieval **+** LLM generation. Returns a grounded answer plus its sources. If the RAGStack server has no LLM configured, it returns the retrieved chunks with a note instead. Args: `query`, `collection?`. |
| `list_collections` | `GET /v1/collections` | Lists the collections you can query (id, label, model, chunk counts) and which is the default. No args. |

## Install

The server needs the official `mcp` SDK (2.x). Install RAGStack with the `mcp`
extra, or add the SDK to an existing environment:

```bash
cd python
pip install -e ".[mcp]"          # brings in the mcp SDK + httpx
# or, into an existing env:
pip install "mcp>=2.0"
```

Run it (reads configuration from the environment — see below):

```bash
RAGSTACK_BASE_URL=http://localhost:8030 \
RAGSTACK_COLLECTION=my_papers \
python -m ragstack.mcp
```

It serves MCP over stdio and blocks — that is expected; an MCP client
(Claude Desktop / Claude Code) launches and talks to it, you don't run it by hand
except to smoke-test that it starts.

## Configuration (environment variables)

| Variable | Required | Meaning |
|---|---|---|
| `RAGSTACK_BASE_URL` | yes | Base URL of the RAGStack API, e.g. `http://localhost:8030`. Defaults to `http://localhost:8000`. |
| `RAGSTACK_API_KEY` | no | If set, sent as the `X-API-Key` header on every request. |
| `RAGSTACK_COLLECTION` | no | Default collection id for `search`/`answer` when the tool call omits `collection`. |

## Claude Desktop

Edit `claude_desktop_config.json`
(macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`;
Windows: `%APPDATA%\Claude\claude_desktop_config.json`) and add a `ragstack`
entry. Use an absolute path to a Python that has RAGStack + the `mcp` SDK
installed (here the project env on the dev host):

```json
{
  "mcpServers": {
    "ragstack": {
      "command": "/rag/envs/ragstack/bin/python",
      "args": ["-m", "ragstack.mcp"],
      "env": {
        "RAGSTACK_BASE_URL": "http://localhost:8030",
        "RAGSTACK_COLLECTION": "my_papers",
        "RAGSTACK_API_KEY": "your-key-if-any"
      }
    }
  }
}
```

Restart Claude Desktop. The three tools appear under the `ragstack` server; ask
"What collections are available?" to confirm it is wired up.

## Claude Code

Add the server with the CLI (writes a `.mcp.json` in the project, or your user
config with `-s user`):

```bash
claude mcp add ragstack \
  -e RAGSTACK_BASE_URL=http://localhost:8030 \
  -e RAGSTACK_COLLECTION=my_papers \
  -e RAGSTACK_API_KEY=your-key-if-any \
  -- /rag/envs/ragstack/bin/python -m ragstack.mcp
```

Or write `.mcp.json` at the repo root by hand:

```json
{
  "mcpServers": {
    "ragstack": {
      "command": "/rag/envs/ragstack/bin/python",
      "args": ["-m", "ragstack.mcp"],
      "env": {
        "RAGSTACK_BASE_URL": "http://localhost:8030",
        "RAGSTACK_COLLECTION": "my_papers",
        "RAGSTACK_API_KEY": "your-key-if-any"
      }
    }
  }
}
```

Check it with `claude mcp list` (or `/mcp` inside a session).

## Error handling

Every tool degrades gracefully — connection refused, non-2xx responses, an
unauthorized `X-API-Key`, a 503, or a RAGStack server with no LLM configured all
come back as a plain, actionable sentence rather than a stack trace, so Claude
can relay the problem to you.

## Tests

The tool logic (HTTP + formatting) lives in `backend.py` and depends only on
`httpx`, so it is unit-tested with `httpx.MockTransport` and **no live server**:

```bash
cd python && pytest tests/unit/test_mcp_server.py -q
```
