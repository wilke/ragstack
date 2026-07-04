"""Transient-error classification + backoff for retriable ingest operations.

Shared by the coupled ingester (``scripts/ingest_jsonl.py``) and the decoupled
backpressured ingest agent (``scripts/qdrant_ingest_agent.py``). A flapping
endpoint (a remote vLLM replica dropping a connection, a store 5xx/timeout, and
crucially a capped-Qdrant ``ResponseHandlingException`` upsert drop — see
``/rag/documents/vma-exhaustion-incident-2026-07-04.md``) raises these and can
self-heal on a retry; a 4xx / bad-input is NOT transient and must surface so the
batch fails and ``--resume`` re-feeds it rather than silently spinning.
"""
from __future__ import annotations

_TRANSIENT_ERROR_NAMES = frozenset({
    "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout",
    "ConnectError", "ReadError", "WriteError", "RemoteProtocolError",
    "ConnectionError", "ConnectionResetError", "ConnectionTimeout",
    "ResponseHandlingException", "UnexpectedResponse", "ServiceException",
    "TimeoutError",
})
_TRANSIENT_ERROR_SUBSTRINGS = (
    "disconnect", "timed out", "timeout", "connection reset",
    "connection refused", "temporarily unavailable", "broken pipe",
    "server disconnected", "502", "503", "504",
    # PooledEmbedder raises this when every endpoint is momentarily down; it
    # chains the real (transient) fault as __cause__, which the walk below also
    # catches — the phrase is an explicit backstop.
    "all embedding endpoints failed",
)


def is_transient_error(exc: BaseException) -> bool:
    """True if ``exc`` (or a chained cause) looks like a transient network/store
    blip worth retrying.

    Walks ``__cause__``/``__context__`` because a fanned-out embed wraps the real
    fault: ``PooledEmbedder`` raises ``RuntimeError('all embedding endpoints
    failed') from last_exc``, so the retriable httpx/timeout error is one level
    down. Inspecting only the top exception would miss the multi-endpoint flap
    this feature exists to survive."""

    def _one(e: BaseException) -> bool:
        if isinstance(e, (TimeoutError, ConnectionError)):
            return True
        if type(e).__name__ in _TRANSIENT_ERROR_NAMES:
            return True
        status = getattr(getattr(e, "response", None), "status_code", None)
        if isinstance(status, int) and 500 <= status < 600:
            return True
        return any(s in str(e).lower() for s in _TRANSIENT_ERROR_SUBSTRINGS)

    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if _one(cur):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def retry_delay(attempt: int, base: float = 1.0, cap: float = 30.0) -> float:
    """Exponential backoff for --batch-retries; ``attempt`` is 1-based (1,2,4,…)."""
    return min(base * (2.0 ** (attempt - 1)), cap)
