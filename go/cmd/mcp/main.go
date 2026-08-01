// Command ragstack-mcp is a standalone Model Context Protocol server for
// RAGStack. It is a single static binary a user can drop on their machine and
// point Claude Desktop or Claude Code at — no Python, no repo checkout, no
// virtualenv. It wraps a running RAGStack HTTP API and exposes three tools
// (search, answer, list_collections) over stdio.
//
// Configuration comes from the environment only:
//
//	RAGSTACK_BASE_URL    base URL of the API (default http://localhost:8000)
//	RAGSTACK_API_KEY     optional; sent as the X-API-Key header when set
//	RAGSTACK_COLLECTION  optional default collection id
//
// See go/cmd/mcp/README.md for build and client-configuration instructions.
package main

import (
	"context"
	"fmt"
	"os"
	"os/signal"

	"github.com/ragstack/ragstack/internal/mcp"
)

func main() {
	// Handle --version / -v without needing a full flag package, so the binary
	// stays dependency-light and its stdout is otherwise reserved for MCP.
	for _, arg := range os.Args[1:] {
		switch arg {
		case "--version", "-v":
			fmt.Println("ragstack-mcp " + mcp.Version)
			return
		case "--help", "-h":
			fmt.Fprint(os.Stderr, usage)
			return
		}
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()

	backend := mcp.NewBackend(mcp.ConfigFromEnv(os.Getenv), nil)

	// Never write anything but MCP protocol traffic to stdout; diagnostics go
	// to stderr so they don't corrupt the stdio JSON-RPC stream.
	if err := mcp.Serve(ctx, backend); err != nil && ctx.Err() == nil {
		fmt.Fprintln(os.Stderr, "ragstack-mcp: "+err.Error())
		os.Exit(1)
	}
}

const usage = `ragstack-mcp — RAGStack MCP server (stdio transport)

Speaks the Model Context Protocol over stdin/stdout. Launch it from an MCP
client (Claude Desktop or Claude Code), not by hand.

Environment:
  RAGSTACK_BASE_URL    base URL of the RAGStack API (default http://localhost:8000)
  RAGSTACK_API_KEY     optional; sent as the X-API-Key header when set
  RAGSTACK_COLLECTION  optional default collection id

Flags:
  --version, -v   print version and exit
  --help, -h      print this help and exit
`
