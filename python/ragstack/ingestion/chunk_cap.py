"""Per-collection chunk cap (#291, phase 3 of #201).

One user-created collection may hold at most ``settings.max_chunks_per_collection``
chunks (50,000 by default — 1,000 documents x the measured ~34 chunks per article,
plus headroom; the derivation lives on the setting). The three decisions
recorded on #291, and where each lands here:

1. **Where the cap is stored** — derived: a collection is capped when it is
   *user-created* (an active owner row whose owner is not an admin —
   ``api/access.py::is_user_created``) and the global default is on; an admin
   may set an explicit per-collection override on the registry entry
   (``CollectionSpec.max_chunks``: ``None`` = derive, ``0`` = exempt, ``N`` =
   cap at ``N``), which wins over the derivation on every path.
   :func:`effective_chunk_cap` is that rule.
2. **Partial ingest** — the WHOLE job is refused when it would cross the cap,
   nothing is written, and the refusal reports how much would have fit
   (:class:`ChunkCapExceeded`).
3. **Live vs counter** — ONE live ``VectorStore.count()`` per ingest job, before
   the first write (:func:`check_chunk_cap`); never a per-chunk store call, and
   never a counter that drifts from the store. A delete frees budget by
   construction.

Enforcement points (each calls :func:`check_chunk_cap` exactly once per job):

* the API/local path — ``ShardedIngestor.ingest_manifest`` (the manifest is the job);
* the GoWe worker — ``ingestion.shard.run_shard`` (``ingest_shard --max-chunks``,
  the ``max_chunks`` workflow input the API derives per job);
* the bulk loader — ``scripts/load_embeddings.py`` (the invocation is the job).

A **replay** (restore, ``load_embeddings --replay``) is deliberately never
capped: it restores chunks that were already admitted.
"""
from __future__ import annotations

from typing import Any

#: The job / receipt error label. A constant string (never a formatted one on the
#: job row) so the SQL job stores can GROUP BY it, like ``NO_TEXT_ERROR``.
CHUNK_CAP_EXCEEDED = "chunk_cap_exceeded"
#: The exit code ``scripts/ingest_shard.py`` returns for a cap refusal — the
#: deterministic signal the API classifies a FAILED GoWe submission by
#: (``GoWeBackend``), mirroring ``restore.py``'s ``REFUSED_EXIT_CODE = 3``.
#: Distinct from 1 (a batch-level error the engine may retry) and from 2/3.
CAP_REFUSED_EXIT_CODE = 4


class ChunkCapExceeded(RuntimeError):
    """The job would push the collection past its chunk cap. Raised BEFORE the
    first write; nothing was written. Carries the four numbers the refusal
    reports: ``live`` (chunks already in the collection), ``incoming`` (what
    the job would add), ``cap`` and ``would_fit`` (``max(0, cap - live)``).

    ``job_error`` is the label the ingest job records (the
    ``NoTextExtracted.job_error`` convention: a caller-safe constant, not the
    class name)."""

    job_error = CHUNK_CAP_EXCEEDED

    def __init__(self, live: int, incoming: int, cap: int) -> None:
        self.live = int(live)
        self.incoming = int(incoming)
        self.cap = int(cap)
        self.would_fit = max(0, self.cap - self.live)
        super().__init__(format_refusal(self.live, self.incoming, self.cap))

    def detail(self) -> dict[str, Any]:
        """The refusal as data: ``{error, live, incoming, cap, would_fit}``."""
        return {
            "error": CHUNK_CAP_EXCEEDED, "live": self.live, "incoming": self.incoming,
            "cap": self.cap, "would_fit": self.would_fit,
        }


def format_refusal(live: int, incoming: int, cap: int) -> str:
    """``chunk_cap_exceeded: live=L incoming=I cap=C would_fit=W`` — the one
    wire form of the refusal (receipt ``error``, job-item ``error``, log line).
    Starts with the label so a consumer can recognise it by prefix
    (:func:`is_cap_refusal`) and parse the numbers after it."""
    return (
        f"{CHUNK_CAP_EXCEEDED}: live={live} incoming={incoming} cap={cap} "
        f"would_fit={max(0, cap - live)}"
    )


def is_cap_refusal(error: str | None) -> bool:
    """Is ``error`` (an item / receipt error string) a chunk-cap refusal?"""
    return bool(error) and str(error).startswith(CHUNK_CAP_EXCEEDED)


def effective_chunk_cap(
    *, override: int | None, user_created: bool, default_cap: int
) -> int | None:
    """The cap that applies, or ``None`` for unlimited.

    An explicit registry override wins (``0`` = exempt, ``N`` = cap at ``N``);
    otherwise the global default applies to user-created collections only, and
    ``default_cap <= 0`` disables it deployment-wide."""
    if override is not None:
        return int(override) if int(override) > 0 else None
    if not user_created or default_cap <= 0:
        return None
    return int(default_cap)


async def check_chunk_cap(vector_store: Any, incoming: int, cap: int | None) -> int | None:
    """ONE live count against ``vector_store``; refuse when ``live + incoming``
    would exceed ``cap``. Returns the live count (``None`` when uncapped, in
    which case the store is not contacted at all). Raises
    :class:`ChunkCapExceeded` — nothing has been written when it does."""
    if cap is None:
        return None
    live = int(await vector_store.count())
    if live + int(incoming) > cap:
        raise ChunkCapExceeded(live, incoming, cap)
    return live
