# RAGStack MCP server (Go, single binary)

`ragstack-mcp` is a standalone [Model Context Protocol](https://modelcontextprotocol.io)
server for **RAGStack**, compiled to a **single static binary**. Drop the one
binary on your machine and point Claude Desktop or Claude Code at it — **no
Python, no repo checkout, no virtualenv**. It wraps a RAGStack instance you
already have running and exposes three tools over the stdio transport.

It is a faithful port of the Python MCP server (`python/ragstack/mcp/`): same
tool names, same arguments, same output formatting.

## Tools

| Tool | RAGStack endpoint | What it does |
|---|---|---|
| `search` | `POST /v1/retrieve` | Hybrid retrieval only. Returns the ranked chunks (doc_id, title, score, snippet) so Claude can read and cite the raw sources. Args: `query`, `collection?`, `top_k?` (default 5). |
| `answer` | `POST /v1/query` | Full RAG: retrieval **+** LLM generation. Returns a grounded answer plus its sources. If the RAGStack server has no LLM configured, it returns the retrieved chunks with a note instead. Args: `query`, `collection?`. |
| `list_collections` | `GET /v1/collections` | Lists the collections you can query (id, label, model, chunk counts) and which is the default. No args. |

## MCP library

Built on the **official Go SDK**,
[`github.com/modelcontextprotocol/go-sdk`](https://github.com/modelcontextprotocol/go-sdk),
pinned to **v0.8.0** — the newest release whose module targets Go 1.23 (the
toolchain this repo builds with). The SDK's v1.x line requires Go 1.25; when
this repo bumps its toolchain, `go get github.com/modelcontextprotocol/go-sdk@latest`
moves to the v1.x API with only minor changes.

## Build

The command lives in the repo's `go/` module, so it builds with the same
toolchain as the API.

```bash
cd go
CGO_ENABLED=0 go build -o bin/ragstack-mcp ./cmd/mcp
```

`CGO_ENABLED=0` yields a static binary with no libc dependency. To confirm it
starts:

```bash
./bin/ragstack-mcp --version     # prints: ragstack-mcp 0.1.0
```

### Cross-compile

The binary is pure Go, so cross-compiling is just a matter of `GOOS`/`GOARCH`:

```bash
# macOS (Apple Silicon)
CGO_ENABLED=0 GOOS=darwin  GOARCH=arm64 go build -o bin/ragstack-mcp-darwin-arm64 ./cmd/mcp

# Linux (x86-64)
CGO_ENABLED=0 GOOS=linux   GOARCH=amd64 go build -o bin/ragstack-mcp-linux-amd64  ./cmd/mcp
```

(For completeness: `GOOS=darwin GOARCH=amd64` for Intel Macs,
`GOOS=windows GOARCH=amd64` for Windows — append `.exe`.)

Copy the resulting binary anywhere on the target machine; it has no runtime
dependencies.

## Configuration (environment variables)

| Variable | Required | Meaning |
|---|---|---|
| `RAGSTACK_BASE_URL` | no | Base URL of the RAGStack API, e.g. `http://localhost:8030`. Defaults to `http://localhost:8000`. A trailing slash is fine — it is stripped. |
| `RAGSTACK_API_KEY` | no | If set, sent as the `X-API-Key` header on every request. Never logged or echoed. |
| `RAGSTACK_COLLECTION` | no | Default collection id for `search`/`answer` when the tool call omits `collection`. |

It serves MCP over stdio and blocks — that is expected. An MCP client launches
it and talks to it; you don't run it by hand except to smoke-test that it
starts.

## Claude Desktop

Edit `claude_desktop_config.json`
(macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`;
Windows: `%APPDATA%\Claude\claude_desktop_config.json`) and add a `ragstack`
entry pointing at the **absolute path of the binary** — no Python, no repo:

```json
{
  "mcpServers": {
    "ragstack": {
      "command": "/absolute/path/to/ragstack-mcp",
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

Add the server with the CLI, pointing directly at the binary:

```bash
claude mcp add ragstack \
  -e RAGSTACK_BASE_URL=http://localhost:8030 \
  -e RAGSTACK_COLLECTION=my_papers \
  -e RAGSTACK_API_KEY=your-key-if-any \
  -- /absolute/path/to/ragstack-mcp
```

Or write `.mcp.json` at the repo root by hand:

```json
{
  "mcpServers": {
    "ragstack": {
      "command": "/absolute/path/to/ragstack-mcp",
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
unauthorized `X-API-Key` (→ "Check RAGSTACK_API_KEY."), a 503, or a RAGStack
server with no LLM configured all come back as a plain, actionable sentence
rather than a stack trace, so Claude can relay the problem to you. The API key
is never logged or echoed.

## Tests

The tool logic (HTTP + formatting) lives in `internal/mcp/backend.go` and depends
only on the standard library, so it is tested with `net/http/httptest` and **no
live server**. `internal/mcp/server_test.go` additionally drives real MCP tool
calls through the SDK over an in-memory transport.

```bash
cd go && go test ./internal/mcp/...
```
