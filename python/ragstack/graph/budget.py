"""Per-collection graph budget (#350) — the chunk cap's sibling (#291).

One collection's graph may hold at most ``settings.graph_max_triples_per_collection``
triples (200,000 by default: the chunk cap of 50,000 chunks × the ~4 triples
per chunk the LLM extractor yields on prose, with headroom). Enforced ONCE per
extraction job, before the first write to the graph store, by the load-graph
tool: one live ``GraphStore.stats(collection=…)`` round trip (the relationship
count, collection-wide — the cap bounds the collection, not a tenant's stripe
of it), and a load whose ``live + incoming`` would cross the cap is refused
whole — nothing is loaded, not the part that would have fit — with the label
:data:`GRAPH_CAP_EXCEEDED` and the four numbers.

The extract-graph tool applies the same cap to its own output alone
(``incoming > cap``, live unknown to it) so a version that could never be
loaded is refused before its leg is even written. Both tools exit
:data:`GRAPH_CAP_REFUSED_EXIT_CODE` on a refusal — the deterministic signal the
API classifies a FAILED submission by (:func:`graph_cap_refusal_of`), exactly
as ``GoWeBackend.cap_refusal_of`` does for the chunk cap.

A restore replay is never capped: it re-admits triples that were archived.
"""
from __future__ import annotations

from typing import Any

#: The job error label. A constant string (never a formatted one on the job
#: row) so the SQL job stores can GROUP BY it, like ``CHUNK_CAP_EXCEEDED``.
GRAPH_CAP_EXCEEDED = "graph_cap_exceeded"
#: The exit code the extract-graph / load-graph tools return on a cap refusal
#: — the same value as the chunk cap's (the two never share a submission), so
#: an operator learns one code.
GRAPH_CAP_REFUSED_EXIT_CODE = 4


class GraphCapExceeded(RuntimeError):
    """The job would push the collection's graph past its triple cap. Raised
    BEFORE the first write; nothing was loaded. ``live`` is ``None`` when the
    refusing tool could not count (the extract tool has no store)."""

    job_error = GRAPH_CAP_EXCEEDED

    def __init__(self, live: int | None, incoming: int, cap: int) -> None:
        self.live = int(live) if live is not None else None
        self.incoming = int(incoming)
        self.cap = int(cap)
        self.would_fit = max(0, self.cap - (self.live or 0))
        super().__init__(format_graph_refusal(self.live, self.incoming, self.cap))

    def detail(self) -> dict[str, Any]:
        """The refusal as data: ``{error, live, incoming, cap, would_fit}``."""
        return {
            "error": GRAPH_CAP_EXCEEDED, "live": self.live, "incoming": self.incoming,
            "cap": self.cap, "would_fit": self.would_fit,
        }


def format_graph_refusal(live: int | None, incoming: int, cap: int) -> str:
    """``graph_cap_exceeded: live=L incoming=I cap=C would_fit=W`` — the one
    wire form of the refusal (stderr line, job error, log line). ``live=?``
    when the refusing tool could not count."""
    live_s = "?" if live is None else str(live)
    return (
        f"{GRAPH_CAP_EXCEEDED}: live={live_s} incoming={incoming} cap={cap} "
        f"would_fit={max(0, cap - (live or 0))}"
    )


def is_graph_cap_refusal(error: str | None) -> bool:
    """Is ``error`` (a stderr / job error line) a graph-cap refusal?"""
    return bool(error) and str(error).startswith(GRAPH_CAP_EXCEEDED)


async def check_graph_cap(
    graph_store: Any, incoming: int, cap: int | None, *, collection: str
) -> int | None:
    """ONE live count of ``collection``'s triples (every tenant's — the
    collection is the unit) against ``graph_store``; refuse when
    ``live + incoming`` would exceed ``cap``. Returns the live count (``None``
    when uncapped, in which case the store is not contacted). Raises
    :class:`GraphCapExceeded` — nothing has been loaded when it does."""
    if cap is None or cap <= 0:
        return None
    _entities, live = await graph_store.stats(tenant_id=None, collection=collection)
    live = int(live)
    if live + int(incoming) > cap:
        raise GraphCapExceeded(live, incoming, cap)
    return live


def _failure_context(submission: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """GoWe's terminal failure record is ``error: {code, message, context:
    {stderr, exit_code, …}}``; returns ``(context, message)``, tolerating a
    bare-string or missing ``error`` (the reading ``restore.classify_failure``
    and ``GoWeBackend.cap_refusal_of`` apply)."""
    err = submission.get("error")
    if isinstance(err, dict):
        ctx = err.get("context")
        return (ctx if isinstance(ctx, dict) else {}), str(err.get("message") or err.get("code") or "")
    return {}, (str(err) if err else str(submission.get("message") or ""))


def graph_cap_refusal_of(submission: dict[str, Any]) -> str | None:
    """The graph-cap refusal a FAILED submission carries, or ``None``.

    DETERMINISTIC first: ``error.context.exit_code == GRAPH_CAP_REFUSED_EXIT_CODE``
    decides. The refusal line the tool printed to stderr supplies the numbers
    when it is inside the engine's stderr window; without it the bare label is
    returned — the label is what the job records either way. A refusal line
    with a DIFFERENT exit code is not a cap refusal (the exit code is
    authoritative)."""
    ctx, message = _failure_context(submission)
    try:
        code = int(ctx["exit_code"]) if ctx.get("exit_code") is not None else None
    except (TypeError, ValueError):
        code = None
    if code != GRAPH_CAP_REFUSED_EXIT_CODE:
        return None
    for text in (str(ctx.get("stderr") or ""), message):
        for line in text.splitlines():
            if is_graph_cap_refusal(line.strip()):
                return line.strip()
    return GRAPH_CAP_EXCEEDED
