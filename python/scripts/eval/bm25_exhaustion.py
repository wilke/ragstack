"""Read-only BM25 exhaustion verifier: is a short ``bm25`` list exhausted or clamped?

A ``POST /v1/retrieve`` in ``bm25`` mode that serves fewer sources than requested
is either **exhausted** (the query matches fewer chunks than the depth asked for:
the list is complete) or **clamped** (something between the caller and the index
cut it). The API cannot tell them apart; the index can. This module builds the
query the server's BM25 leg sends for one question and counts its matches with
ONE read-only ``_count`` on the bench index:

* ``count == served`` -> ``exhausted_verified``: the served list IS the full list,
  so a gold chunk is found on it or matches nothing;
* ``count > served`` with the exact scope -> ``clamp``: a hard error for the run;
* anything else (no count, an unscoped count above the served length, a count
  below it) -> ``under_served``: the existing depth gate stands.

**The query is the server's own, not a copy.** The scoring clause and the filter
clauses come from :func:`ragstack.stores.elasticsearch._build_query` (or, with no
tenant scope, :func:`~ragstack.stores.elasticsearch._text_clause`), the function
:meth:`ElasticsearchTextIndex.search` calls. The filter dict is assembled the way
``/v1/retrieve`` assembles it: the request's ``filters``, then ``is_boilerplate``
:class:`~ragstack.stores.filters.Not` ``True`` when ``exclude_boilerplate`` is set
(``ragstack.api.routers.query._exclude_boilerplate``, not imported: it would pull
in the API app), then ``tenant_id`` set LAST to
:func:`ragstack.tenancy.readable_tenants` (``scope_filters``). Matching is
independent of the boost's magnitude: a boost ``> 0`` adds the ``symbol_glossary``
field to the matched set (``most_fields``), ``<= 0`` matches ``content`` alone, so
only the sign must be right.

**Two scopes.** With the caller's tenant (and any shared-collection owner tenant
the server adds via ``shared_scope``) the count is ``exact``. Without it the count
drops every filter and is an ``unscoped_upper_bound``: filters only narrow, so a
bound EQUAL to the served length still proves exhaustion (served <= true count <=
bound = served), while a bound above it proves nothing (``under_served``, never
``clamp``).

**Eligible requests only.** ``bm25`` mode, no graph leg, a single query variant
(``rewrite_strategies`` absent or ``["passthrough"]``) and one collection: then the
served list is the BM25 leg's list cut to ``top_k`` (fusion of one list and the
server's shaping reorder, never drop). Hybrid and vector lists cannot be checked
this way; they keep the depth gate. One known way an exact ``clamp`` can be a
false alarm: two indexed chunks under different tenants sharing one chunk id fuse
into one source (RRF keys on the chunk id) -- the verdict says so, it never passes.

Nothing here records or logs the query text: a result carries counts, the verdict,
the clause kind and the scope.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ragstack.ingestion.boilerplate import BOILERPLATE_KEY
from ragstack.stores.elasticsearch import _build_query, _text_clause
from ragstack.stores.filters import Not
from ragstack.tenancy import readable_tenants

CHECK = "bm25_exhaustion_es_count"
EXHAUSTED = "exhausted_verified"
CLAMP = "clamp"
UNDER = "under_served"
SCOPE_EXACT = "exact"
SCOPE_BOUND = "unscoped_upper_bound"


class CredentialRefused(Exception):
    """The index client carries an API credential header."""


def ineligible(request: Mapping[str, Any]) -> str | None:
    """Why a ``/v1/retrieve`` body's served list cannot be verified by one count, or
    ``None`` when it can (module docstring, *Eligible requests only*)."""
    if request.get("retrieval_mode") != "bm25":
        return f"retrieval_mode {request.get('retrieval_mode')!r} is not bm25"
    if request.get("use_graph", True):
        return "the graph leg may add sources (use_graph is not false)"
    rw = request.get("rewrite_strategies")
    if rw is not None and list(rw) != ["passthrough"]:
        return "more than one query variant (rewrite_strategies is not passthrough)"
    if request.get("collections"):
        return "a multi-collection fan-out"
    return None


def server_filters(request: Mapping[str, Any], tenant: str, extra_tenants: Sequence[str] | None = None
                   ) -> dict[str, Any]:
    """The filter dict ``/v1/retrieve`` hands the text index for ``request``."""
    filters: dict[str, Any] = dict(request.get("filters") or {})
    if request.get("exclude_boilerplate"):
        filters = {**filters, BOILERPLATE_KEY: Not(True)}
    return {**filters, "tenant_id": readable_tenants(tenant, list(extra_tenants or []) or None)}


def count_query(question: str, request: Mapping[str, Any], glossary_boost: float,
                tenant: str | None = None, extra_tenants: Sequence[str] | None = None
                ) -> tuple[dict[str, Any], str]:
    """``(the ES query, scope)`` for one question: ``exact`` with a tenant, else the
    unscoped upper bound (the text clause alone)."""
    if tenant:
        return _build_query(question, server_filters(request, tenant, extra_tenants), glossary_boost), SCOPE_EXACT
    return {"bool": {"must": [_text_clause(question, glossary_boost)]}}, SCOPE_BOUND


def clause_kind(glossary_boost: float) -> str:
    return "content+symbol_glossary" if glossary_boost > 0 else "content"


def verdict(served: int, count: int | None, scope: str) -> str:
    if count is None:
        return UNDER
    if count == served:
        return EXHAUSTED
    if count > served and scope == SCOPE_EXACT:
        return CLAMP
    return UNDER


def refuse_credentials(headers: Mapping[str, str], credential_headers: Iterable[str]) -> None:
    carried = sorted(h for h in credential_headers if h in headers)
    if carried:
        raise CredentialRefused(f"the index client carries {carried}; the API credential "
                                "must never be sent to the index host")


def count_url(es_url: str, index: str) -> str:
    return f"{es_url.rstrip('/')}/{index}/_count"


def parse_count(payload: Mapping[str, Any]) -> int:
    return int(payload["count"])


def record(requested: int, served: int, count: int | None, scope: str | None, glossary_boost: float | None,
           error: str | None = None, reason: str | None = None) -> dict[str, Any]:
    """The per-list record (no query text)."""
    v = verdict(served, count, scope or SCOPE_BOUND)
    out: dict[str, Any] = {"check": CHECK, "requested": int(requested), "served": int(served), "es_count": count,
                           "verdict": v, "scope": scope,
                           "clause": None if glossary_boost is None else clause_kind(glossary_boost)}
    if error:
        out["error"] = error
    if reason:
        out["reason"] = reason
    if v == CLAMP:
        out["reason"] = ("the index matches more chunks than were served: a clamp (or two tenants' chunks "
                         "sharing one chunk id, which fusion merges)")
    return out
