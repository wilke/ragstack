"""Per-stage query timings — the accumulator, the context manager, and the
rendering the request summary line uses.

Issue #427 in one sentence: *a user's query failed with a 503 and we could not
say why*. W1 gave every request an id; this module gives every request a
**breakdown of where its time went**, so the next occurrence answers "the vector
leg spent 30 s of a 30 s bound" rather than "something was slow".

.. rubric:: Where the accumulator lives

On :class:`~ragstack.observability.context.RequestContext`, in the ``stages``
slot W1 reserved for it — **not** in a second ``ContextVar``. That is not a
detail: the package's whole contract is "one mutable object in one contextvar,
mutated in place, never re-``set()`` below the middleware", and a second var
would need its own copy of that reasoning and would get it wrong in exactly the
place it matters (see mechanic 2 below).

.. rubric:: Five mechanics an implementer will otherwise get wrong

1. **A sync context manager, not** ``@asynccontextmanager``.
   ``with stage("vector"): x = await store.search(...)`` works exactly as
   intended — ``__exit__`` runs when the await completes, because the ``with``
   block does not end until it does. Both were measured: the sync class costs
   **0.62 µs** per stage, the async-generator version **1.87 µs**. Three times
   the cost for no behaviour difference.
2. **Mutate; never** ``set()``. ``asyncio.gather`` children copy the *context*,
   so they see the same ``RequestContext`` object and the same accumulator, and
   their ``add()`` calls are visible to the middleware that renders the line.
   A ``ContextVar.set()`` inside a child is invisible to the parent. The query
   path has three nested gather sites (``routers/query.py`` per-variant and
   per-collection, ``retrieval/retriever.py`` per-leg), so this is load-bearing,
   not theoretical.
3. **A fresh accumulator per request**, installed by the middleware alongside
   the context object. A module-scope or reused one leaks request N's timings
   onto request N+1 and every number after that is quietly wrong.
4. :func:`stage` **no-ops when there is no request context.** CLI scripts
   (``python/scripts/*.py``), the ingest pipeline, library callers and most unit
   tests run without one; none of them should have to know this module exists.
   The unset path costs ~0.31 µs.
5. **Record** ``(sum, count)``, **never a bare sum.** Under those nested gathers
   the stage sums *exceed* wall time — five legs of 9 s render as 45 s. A bare
   ``vector_ms=45000`` will be read as one 45-second search by the next person
   to grep this log, so the rendering is ``vector_ms=45000.0/5`` and ``wall_ms``
   is always printed beside it.

.. rubric:: ``self_ms`` is an upper bound, and is labelled as one

ADR-0006 makes the Go-port trigger a **residual**: p95 attributable to the
Python layer *after subtracting* vLLM, Qdrant, Elasticsearch, reranker and LLM
time. So the residual is only honest if every external call on the query path is
timed — an untimed store round trip inflates "the Python layer" and could
justify a port the measurement does not support. That is why ``authz`` and
``expand`` are timed even though neither is a model call: since #419 both are
batched round trips to Postgres and to the vector store.

Two deliberate conservatisms keep ``self_ms`` an *upper* bound rather than a
number that could fire that trigger spuriously:

* the subtraction uses each stage's **mean** (``sum/count``), not its sum,
  because under concurrency N legs of 9 s occupy 9 s of wall time, not 45 s.
  Where the legs were in fact sequential this under-subtracts — the safe
  direction;
* a stage name this module does not recognise as external is **not** subtracted
  (see :data:`EXTERNAL_STAGES`). A new untimed-then-timed external call
  therefore inflates ``self_ms`` until someone adds it to the set, rather than
  silently deflating the residual. ``test_query_summary_line.py`` pins the
  emitted name set for the same reason.

A clean residual needs a serialised (concurrency-1) measurement, which is what a
load test (#118) would provide. Until then: upper bound, and the runbook says so.
"""

from __future__ import annotations

import hashlib
from time import perf_counter
from types import TracebackType
from typing import Literal

from ragstack.observability.context import MISSING, current_context

#: Stages that are a call to something outside this process, and so are
#: subtracted from wall time to form ``self_ms``. ``fuse`` is deliberately
#: absent: RRF runs in-process and IS part of the Python-layer residual
#: ADR-0006 measures.
#:
#: Anything not listed here is treated as in-process. That default is the
#: conservative one — see the module docstring.
EXTERNAL_STAGES = frozenset(
    {"embed", "vector", "text", "graph", "rerank", "rewrite", "authz", "expand", "generate"}
)

#: Every stage name this codebase emits. Not enforced at runtime (a caller may
#: time whatever it likes); it exists so a test can assert the set a real
#: request produces, making a newly-added untimed external call loud rather than
#: silent.
STAGE_NAMES = EXTERNAL_STAGES | {"fuse"}

#: Hard cap on distinct ``(name, tag)`` series in one request, and on distinct
#: values remembered per note key. A request legitimately produces at most
#: ~(stages × collections) series and ``collections`` is itself capped, so this
#: can only be reached by a bug — but an unbounded dict fed from a request is
#: how a log line becomes a memory leak, and the cap costs one comparison.
MAX_SERIES = 64

#: How many characters of the query hash go on the line. 8 hex = 32 bits: enough
#: to say "these two 503s were the same query", far too little to reverse.
QSHA_CHARS = 8


def query_sha(text: str) -> str:
    """A short, non-reversible fingerprint of a query string.

    **The query text itself must never be logged** — #114 mandates redaction by
    default, and a query is user content that may carry anything. This is what
    goes on the line instead: it answers "was the request that 503'd the same
    query as the one that succeeded a minute later?" without recording what was
    asked.
    """
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:QSHA_CHARS]


class StageTimings:
    """Per-request ``(name, tag) -> (seconds, count)`` plus free-form notes.

    Mutated in place from anywhere below the middleware, including from inside
    ``asyncio.gather`` children (mechanic 2). Not thread-safe and does not need
    to be: everything on the request path runs on one event loop, and the
    increments have no await between read and write.
    """

    __slots__ = ("_series", "_notes", "_overflow")

    def __init__(self) -> None:
        self._series: dict[tuple[str, str], tuple[float, int]] = {}
        self._notes: dict[str, list[str]] = {}
        self._overflow = 0

    # -- writing ---------------------------------------------------------- #

    def add(self, name: str, seconds: float, tag: str | None = None) -> None:
        """Record one observation of ``name`` (optionally attributed to ``tag``,
        the physical collection the leg ran against)."""
        key = (name, tag or MISSING)
        current = self._series.get(key)
        if current is None:
            if len(self._series) >= MAX_SERIES:
                self._overflow += 1
                return
            self._series[key] = (seconds, 1)
        else:
            self._series[key] = (current[0] + seconds, current[1] + 1)

    def note(self, key: str, value: str) -> None:
        """Remember a distinct ``value`` seen for ``key`` during this request.

        Used for facts that are neither a duration nor known at the call site —
        today exactly one: **which of the embedding endpoints served the embed
        call**. That was previously carried only by ``httpx``'s INFO lines,
        which W1's dampening mutes; ``logging_config.apply_dampening`` names
        preserving it as W3's obligation. Distinct values accumulate in first-
        seen order, because a request that fans out across the fleet used
        several and "was it always the same slow one?" is the question.
        """
        seen = self._notes.get(key)
        if seen is None:
            if len(self._notes) >= MAX_SERIES:
                self._overflow += 1
                return
            self._notes[key] = [value]
        elif value not in seen and len(seen) < MAX_SERIES:
            seen.append(value)

    # -- reading ---------------------------------------------------------- #

    @property
    def overflow(self) -> int:
        """Observations dropped because :data:`MAX_SERIES` was reached."""
        return self._overflow

    def names(self) -> set[str]:
        """The distinct stage names recorded. The A5 assertion reads this."""
        return {name for name, _ in self._series}

    def totals(self) -> dict[str, tuple[float, int]]:
        """``name -> (summed seconds, observations)``, aggregated over tags."""
        out: dict[str, tuple[float, int]] = {}
        for (name, _), (seconds, count) in self._series.items():
            prev = out.get(name, (0.0, 0))
            out[name] = (prev[0] + seconds, prev[1] + count)
        return out

    def external_seconds(self) -> float:
        """Σ mean time over the stages that left this process.

        The **mean** (``sum/count``), not the sum: N concurrent legs of 9 s
        occupy 9 s of wall time. See the module docstring for why the resulting
        under-subtraction is the direction we want.
        """
        return sum(
            seconds / count
            for name, (seconds, count) in self.totals().items()
            if name in EXTERNAL_STAGES and count
        )

    def self_seconds(self, wall_seconds: float) -> float:
        """``wall - Σ external means``, clamped at zero.

        Clamped because the subtraction is an estimate and a negative residual
        is not a fact about the server, it is an artifact of concurrency — and
        ``self_ms=-1204.0`` on a log line reads as a bug in the logger, which
        costs more attention than it is worth.
        """
        return max(0.0, wall_seconds - self.external_seconds())

    # -- rendering -------------------------------------------------------- #

    def fields(self) -> dict[str, str]:
        """The stage half of the summary line, as ``extra=`` keys.

        ``<name>_ms=<summed ms>/<observations>`` per stage — the ``/count`` is
        never elided, even at 1, so that neither a human nor a parser has to
        handle two shapes and nobody reads a 5-leg sum as one call.

        Plus, when any observation carried a collection tag, one
        ``by_coll="vector@lib_oa=45000.1/1 …"`` field: the per-leg attribution
        that makes "which collection was slow" answerable on a multi-collection
        request, in one bounded value rather than a variable set of keys.
        """
        out: dict[str, str] = {
            f"{name}_ms": f"{seconds * 1000:.1f}/{count}"
            for name, (seconds, count) in sorted(self.totals().items())
        }
        tagged = sorted(
            (f"{name}@{tag}={seconds * 1000:.1f}/{count}")
            for (name, tag), (seconds, count) in self._series.items()
            if tag != MISSING
        )
        if tagged:
            out["by_coll"] = " ".join(tagged)
        for key, values in sorted(self._notes.items()):
            out[key] = ",".join(values)
        if self._overflow:
            out["stage_overflow"] = str(self._overflow)
        return out


def current_stages() -> StageTimings | None:
    """The accumulator for the request in flight, or ``None`` outside one."""
    ctx = current_context()
    return None if ctx is None else ctx.stages


class stage:
    """Time a block and record it against the current request. A **no-op**
    outside a request (mechanic 4).

    Deliberately lower-case: it reads as a verb at the call site
    (``with stage("vector", coll):``), which is what it is.

    Never swallows an exception — ``__exit__`` returns ``False`` — and records
    the elapsed time **whether or not** the body raised. That is not incidental:
    the incident this issue exists for is a stage that raised after 30 s, and a
    timer that only records on success would have nothing to say about it.
    """

    __slots__ = ("_name", "_tag", "_acc", "_t0")

    def __init__(self, name: str, tag: str | None = None) -> None:
        self._name = name
        self._tag = tag
        self._acc: StageTimings | None = None
        self._t0 = 0.0

    def __enter__(self) -> stage:
        # Resolved once, at entry: the accumulator object cannot change under a
        # request, and one contextvar read per stage is the whole overhead.
        self._acc = current_stages()
        if self._acc is not None:
            self._t0 = perf_counter()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        # Literal[False], not bool: mypy warns that a `bool` return type means a
        # context manager MAY swallow exceptions, and this one must never — the
        # incident it exists to explain is a stage that raised.
        acc = self._acc
        if acc is not None:
            acc.add(self._name, perf_counter() - self._t0, self._tag)
        return False


def note(key: str, value: str) -> None:
    """Attach a fact to the request in flight; a no-op outside one.

    Module-level so callers deep in a client pool — ``embed_pool`` is the one
    today — can record something the call site could not have known, without
    holding a reference to anything request-scoped.
    """
    acc = current_stages()
    if acc is not None:
        acc.note(key, value)


def note_query_sha(text: str) -> None:
    """Stamp :func:`query_sha` of ``text`` onto the request context.

    Lives here rather than in the middleware because the middleware **cannot
    read the request body** — by the time it could, the body has been consumed
    by the handler. So the handler computes it and mutates the context, which is
    exactly the mutate-in-place path the package is built around.
    """
    ctx = current_context()
    if ctx is not None:
        ctx.qsha = query_sha(text)
