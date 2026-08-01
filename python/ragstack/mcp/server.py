"""Stdio MCP server exposing a RAGStack instance to an MCP client.

Run it with ``python -m ragstack.mcp``. It speaks the Model Context Protocol
over stdio — the transport Claude Desktop and Claude Code use for local
servers — and registers three tools: ``search``, ``answer`` and
``list_collections`` (see :mod:`ragstack.mcp.backend` for the logic).

The ``mcp`` SDK is imported lazily inside :func:`build_server` / :func:`main`
so that importing this module (and unit-testing the backend) does not require
the SDK to be installed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import httpx

from ragstack.mcp.backend import RagStackBackend, RagStackConfig

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mcp.server import MCPServer

SEARCH_DESCRIPTION = (
    "Retrieve the passages from the user's RAGStack knowledge base that are most "
    "relevant to a question, WITHOUT generating an answer. Use this whenever you "
    "need source material to ground a response, to quote or cite from the user's "
    "documents, or to check what the knowledge base actually says before "
    "answering. Returns a ranked list of chunks, each with its doc_id, chunk_id, "
    "a relevance score, and a text snippet you can cite. Prefer this over 'answer' "
    "when you want to read and reason over the raw sources yourself. "
    "Args: query (the search text), collection (optional collection id; omit to "
    "use the server default), top_k (how many chunks to return, default 5)."
)

ANSWER_DESCRIPTION = (
    "Ask the user's RAGStack knowledge base a question and get a single grounded "
    "answer generated from the retrieved passages, along with its sources. Use "
    "this when the user wants a direct, cited answer synthesized from their "
    "documents rather than a list of raw passages. If the RAGStack server has no "
    "LLM configured it cannot generate text; in that case this tool returns the "
    "retrieved passages plus a note, and you should answer from those. "
    "Args: query (the question), collection (optional collection id; omit to use "
    "the server default)."
)

LIST_COLLECTIONS_DESCRIPTION = (
    "List the collections available in the user's RAGStack instance, with each "
    "collection's id, label, embedding model, and chunk counts, plus which one is "
    "the default. Use this first when you are unsure which collection to query, or "
    "when the user asks what data or knowledge bases are available. The ids "
    "returned here are what you pass as the 'collection' argument to 'search' and "
    "'answer'. Takes no arguments."
)


def build_server(
    config: RagStackConfig | None = None,
    http: httpx.AsyncClient | None = None,
) -> MCPServer:
    """Construct the configured :class:`MCPServer` with the three tools wired.

    Imports the ``mcp`` SDK lazily so this module is importable without it.
    ``config``/``http`` are injectable for tests; by default the config is read
    from the environment and a fresh ``httpx.AsyncClient`` is created.
    """
    from mcp.server import MCPServer

    cfg = config or RagStackConfig.from_env()
    owns_http = http is None
    client = http or httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))
    backend = RagStackBackend(cfg, client)

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if owns_http:
                await client.aclose()

    mcp: MCPServer = MCPServer(
        name="ragstack",
        version="0.1.0",
        instructions=(
            "Tools to query a RAGStack retrieval-augmented-generation knowledge "
            f"base at {cfg.base_url}"
            + (f" (default collection: {cfg.collection})." if cfg.collection else ".")
            + " Use 'list_collections' to discover collections, 'search' to fetch "
            "raw source passages, and 'answer' for a grounded synthesized answer."
        ),
        lifespan=lifespan,
    )

    @mcp.tool(description=SEARCH_DESCRIPTION)
    async def search(
        query: str, collection: str | None = None, top_k: int = 5
    ) -> str:
        return await backend.search(query, collection=collection, top_k=top_k)

    @mcp.tool(description=ANSWER_DESCRIPTION)
    async def answer(query: str, collection: str | None = None) -> str:
        return await backend.answer(query, collection=collection)

    @mcp.tool(description=LIST_COLLECTIONS_DESCRIPTION)
    async def list_collections() -> str:
        return await backend.list_collections()

    # Silence "assigned but never used" — the decorators register the tools.
    _ = (search, answer, list_collections)
    return mcp


def main(argv: Any = None) -> None:
    """Entry point: build the server and serve over stdio until EOF."""
    server = build_server()
    server.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
