"""MCP (Model Context Protocol) server for RAGStack.

Exposes a running RAGStack HTTP API as MCP tools (``search``, ``answer``,
``list_collections``) so an MCP client — Claude Desktop or Claude Code — can
answer questions grounded in a RAGStack collection.

The tool *logic* lives in :mod:`ragstack.mcp.backend` and depends only on
``httpx`` so it is unit-testable without the ``mcp`` SDK installed. The SDK is
imported lazily in :mod:`ragstack.mcp.server` (the stdio entrypoint).
"""

from ragstack.mcp.backend import RagStackBackend, RagStackConfig

__all__ = ["RagStackBackend", "RagStackConfig"]
