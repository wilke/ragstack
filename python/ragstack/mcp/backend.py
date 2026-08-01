"""RAGStack HTTP wrapper and result formatting for the MCP server.

Everything here depends only on ``httpx`` — no ``mcp`` SDK — so the tool logic
can be unit-tested with ``httpx.MockTransport`` and no live server. The three
public methods (:meth:`RagStackBackend.search`, :meth:`~.answer`,
:meth:`~.list_collections`) each return a ready-to-display string and never
raise on an API/transport error: they turn failures into a clear message so the
model surfaces something useful instead of a stack trace.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

# Truncated snippet length for a retrieved chunk — long enough to judge
# relevance and cite, short enough to keep the tool output scannable.
_SNIPPET_CHARS = 280

# Prefixes the /v1/query endpoint uses for a non-generated (retrieval-only)
# answer — no LLM configured, or generation failed. Kept in sync with
# ``ragstack.api.routers.query._fallback_answer``.
_LLM_NOT_CONFIGURED = "[LLM not configured]"
_GENERATION_FAILED = "[answer generation failed]"


@dataclass(frozen=True)
class RagStackConfig:
    """Connection settings for a RAGStack instance, read from the environment.

    - ``RAGSTACK_BASE_URL`` — base URL of the API (e.g. ``http://localhost:8030``).
    - ``RAGSTACK_API_KEY`` — optional; sent as the ``X-API-Key`` header when set.
    - ``RAGSTACK_COLLECTION`` — optional default collection id for tools that
      accept a ``collection`` argument.
    """

    base_url: str = "http://localhost:8000"
    api_key: str | None = None
    collection: str | None = None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> RagStackConfig:
        src = env if env is not None else dict(os.environ)
        base = (src.get("RAGSTACK_BASE_URL") or cls.base_url).rstrip("/")
        return cls(
            base_url=base,
            api_key=src.get("RAGSTACK_API_KEY") or None,
            collection=src.get("RAGSTACK_COLLECTION") or None,
        )


def _title_for(meta: dict[str, Any], doc_id: str) -> str:
    """Human label for a source, matching the UI precedence
    (title → filename → source_path → doi → doc_id)."""
    for key in ("title", "filename", "source_path", "doi"):
        val = meta.get(key)
        if val:
            return str(val)
    return doc_id


def _snippet(text: str, limit: int = _SNIPPET_CHARS) -> str:
    flat = " ".join(str(text).split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rstrip() + "…"


def _format_sources(sources: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for i, s in enumerate(sources, start=1):
        doc_id = str(s.get("doc_id", "?"))
        chunk_id = str(s.get("chunk_id", "?"))
        meta = s.get("metadata") or {}
        title = _title_for(meta, doc_id)
        score = s.get("score")
        score_str = f"{float(score):.4f}" if isinstance(score, (int, float)) else "n/a"
        lines.append(f"[{i}] {title}  (score {score_str})")
        lines.append(f"    doc_id: {doc_id}  chunk_id: {chunk_id}")
        content = s.get("content")
        if content:
            lines.append(f"    {_snippet(content)}")
    return "\n".join(lines)


class RagStackBackend:
    """Thin async client over the RAGStack HTTP API used by the MCP tools.

    Holds an ``httpx.AsyncClient`` (injectable for tests). Each public method
    returns a display string and handles transport/HTTP errors internally.
    """

    def __init__(self, config: RagStackConfig, http: httpx.AsyncClient) -> None:
        self._config = config
        self._http = http

    @property
    def config(self) -> RagStackConfig:
        return self._config

    def _headers(self) -> dict[str, str]:
        if self._config.api_key:
            return {"X-API-Key": self._config.api_key}
        return {}

    def _resolve_collection(self, collection: str | None) -> str | None:
        return collection or self._config.collection

    async def _request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> tuple[httpx.Response | None, str | None]:
        """Perform a request. Returns ``(response, None)`` on a completed HTTP
        round-trip (any status), or ``(None, message)`` when the server could
        not be reached at all."""
        url = f"{self._config.base_url}{path}"
        try:
            resp = await self._http.request(
                method, url, json=json, headers=self._headers()
            )
            return resp, None
        except httpx.ConnectError:
            return None, (
                f"Cannot reach RAGStack at {self._config.base_url} "
                "(connection refused). Is the server running and is "
                "RAGSTACK_BASE_URL correct?"
            )
        except httpx.HTTPError as exc:
            return None, f"Request to RAGStack at {self._config.base_url} failed: {exc}"

    @staticmethod
    def _http_error_message(resp: httpx.Response) -> str:
        """A concise message for a non-2xx response, pulling the API's
        ``detail`` when present."""
        detail: str | None = None
        try:
            body = resp.json()
            if isinstance(body, dict):
                d = body.get("detail")
                detail = d if isinstance(d, str) else (str(d) if d else None)
        except (ValueError, TypeError):
            detail = None
        if resp.status_code == 401 or resp.status_code == 403:
            return (
                f"RAGStack rejected the request ({resp.status_code}). "
                "Check RAGSTACK_API_KEY."
            )
        if resp.status_code == 503:
            return f"RAGStack is unavailable (503){f': {detail}' if detail else ''}."
        suffix = f": {detail}" if detail else ""
        return f"RAGStack returned HTTP {resp.status_code}{suffix}."

    async def search(
        self, query: str, collection: str | None = None, top_k: int = 5
    ) -> str:
        """Retrieve the top-k chunks for ``query`` via ``POST /v1/retrieve``."""
        if not query.strip():
            return "No query provided. Pass a non-empty 'query'."
        top_k = max(1, min(int(top_k), 50))
        coll = self._resolve_collection(collection)
        payload: dict[str, Any] = {"query": query, "top_k": top_k}
        if coll:
            payload["collection"] = coll

        resp, err = await self._request("POST", "/v1/retrieve", json=payload)
        if err:
            return err
        assert resp is not None
        if resp.status_code >= 400:
            return self._http_error_message(resp)

        try:
            sources = resp.json().get("sources") or []
        except ValueError:
            return "RAGStack returned a malformed response for /v1/retrieve."

        where = f" in collection '{coll}'" if coll else ""
        if not sources:
            return f'No chunks matched "{query}"{where}.'
        header = f'Top {len(sources)} chunks for "{query}"{where}:\n'
        return header + "\n" + _format_sources(sources)

    async def answer(self, query: str, collection: str | None = None) -> str:
        """Full RAG answer for ``query`` via ``POST /v1/query`` (retrieval +
        LLM generation). Degrades to a clear note when no LLM is configured."""
        if not query.strip():
            return "No query provided. Pass a non-empty 'query'."
        coll = self._resolve_collection(collection)
        payload: dict[str, Any] = {"query": query}
        if coll:
            payload["collection"] = coll

        resp, err = await self._request("POST", "/v1/query", json=payload)
        if err:
            return err
        assert resp is not None
        if resp.status_code >= 400:
            return self._http_error_message(resp)

        try:
            body = resp.json()
        except ValueError:
            return "RAGStack returned a malformed response for /v1/query."
        answer_text = str(body.get("answer") or "").strip()
        sources = body.get("sources") or []

        note = ""
        if answer_text.startswith(_LLM_NOT_CONFIGURED):
            note = (
                "Note: no LLM is configured on this RAGStack server, so no "
                "answer was generated. The relevant chunks are listed below — "
                "use them (or the 'search' tool) to answer directly.\n\n"
            )
            answer_text = ""
        elif answer_text.startswith(_GENERATION_FAILED):
            note = (
                "Note: answer generation failed on the RAGStack server; "
                "returning the retrieved chunks only.\n\n"
            )
            answer_text = ""

        parts: list[str] = []
        if note:
            parts.append(note.rstrip())
        if answer_text:
            parts.append(f"Answer:\n{answer_text}")
        if sources:
            parts.append("Sources:\n" + _format_sources(sources))
        elif not answer_text:
            parts.append(f'No relevant chunks were found for "{query}".')
        return "\n\n".join(parts)

    async def list_collections(self) -> str:
        """List queryable collections via ``GET /v1/collections``."""
        resp, err = await self._request("GET", "/v1/collections")
        if err:
            return err
        assert resp is not None
        if resp.status_code >= 400:
            return self._http_error_message(resp)

        try:
            body = resp.json()
        except ValueError:
            return "RAGStack returned a malformed response for /v1/collections."
        collections = body.get("collections") or []
        default = body.get("default")
        if not collections:
            return "This RAGStack instance has no collections registered."

        lines = [f"Available collections (default: {default}):", ""]
        for c in collections:
            cid = c.get("id", "?")
            label = c.get("label") or ""
            model = c.get("model") or "?"
            count = c.get("count")
            text_count = c.get("text_count")
            is_default = " [default]" if c.get("default") else ""
            head = f"- {cid}{is_default}"
            if label:
                head += f'  "{label}"'
            lines.append(head)
            counts = []
            counts.append(
                f"{count} vector chunks" if count is not None else "vector count n/a"
            )
            counts.append(
                f"{text_count} text chunks"
                if text_count is not None
                else "text count n/a"
            )
            lines.append(f"    model: {model};  {', '.join(counts)}")
        return "\n".join(lines)
