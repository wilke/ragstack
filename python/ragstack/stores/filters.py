"""Shared filter-matching predicate for stores' ``get_chunks`` (#197, phase 1
of #201).

``_build_filter`` (stores/qdrant.py), ``_matches`` (stores/memory.py), and
``_build_query`` (stores/elasticsearch.py) all treat a filters dict the same
way: every key is a BARE chunk-metadata field name, looked up directly (ES
internally prefixes it as ``metadata.<key>`` against its own mapping —
elasticsearch.py's ``_build_query`` — but the caller-facing key is still bare;
see docs/USER-GUIDE.md and docs/API.md's ``filters`` documentation, e.g.
``{"journal": "mBio"}``). There is no notion of an "unknown key" in any of the
three builders — every key is just a payload lookup, and #322 passes
``scope_filters(request.filters, …)`` — a caller-supplied, bare-key dict —
straight through, so this predicate MUST accept the same grammar or every
valid user filter 400s.

``get_chunks`` still needs a stricter contract than ``search()``, but the
strictness is about WHICH records a filter can address, not about requiring a
different key spelling. It resolves ids by point id — an O(ids) ``retrieve``,
not a store-side filtered scan — so *every other* filter key has to be
re-applied against the returned payload in Python, identically in both stores,
or ADR-0003's "the leg must not depend on one store getting it right" rule
breaks. Silently dropping a key here is worse than dropping one in
``search()``: the caller believes a scope constraint took effect when it never
touched the result.

Grammar:

* A bare key (``tenant_id``, ``collection``, ``source``, any chunk metadata
  field) is looked up directly against ``Chunk.metadata`` — exactly what
  ``_build_filter``/``_matches``/``_build_query`` already do for ``search()``.
* ``metadata.<key>`` is NOT an alias. The three search builders take bare
  keys only (``_matches`` looks a ``metadata.x`` key up literally), and this
  predicate must agree with them on every key — so it does the same.
* Every other key is a payload lookup — there is nothing left to refuse... The
  ONE exception is the finite set of keys that can never appear on
  ``Chunk.metadata`` in the first place: :data:`PAYLOAD_RESERVED`
  (``chunk_id``, ``doc_id``, ``content``, ``start_char``, ``end_char`` —
  ``_chunk_from_payload`` in stores/qdrant.py pops these OUT of metadata, so a
  filter on one of them could never match, silently returning nothing rather
  than the caller's evidently-intended row) plus ``library_id`` (not yet a
  real filterable field; docs/libraries-spec.md already mandates 400 for it
  ahead of the libraries feature landing). Those raise
  :class:`UnknownFilterKey` — refuse, don't silently no-op.

List/tuple/set values match by membership (MatchAny — used for tenant reads:
own + public); an *empty* list matches nothing, not "unconstrained" (#196:
membership in the empty set is false). A scalar value is an exact match. Keep
this value grammar in sync with ``_build_filter`` and ``_matches`` for the
keys they both already support.

Value grammar (#471)
--------------------

The four interpreters — ``_build_filter`` (stores/qdrant.py), ``_build_query``
(stores/elasticsearch.py), ``_matches`` (stores/memory.py) and
``payload_matches`` below — used to disagree three ways on a value they could
not represent: Qdrant handed it to ``MatchValue`` and a ``pydantic``
``ValidationError`` escaped as a **500**; Elasticsearch shipped an object into
a ``term`` query; the two Python predicates silently answered "no match". So
the same request was a 500, an error, or an honest-looking empty result
depending on which leg ran.

:func:`validate_filter_values` pins one grammar for all four:

* a SCALAR value is ``str``, ``int`` or ``bool``;
* a LIST (or tuple/set) value is homogeneous — all ``str`` or all ``int``.
  Booleans are scalar-only, and a list may not mix the two types;
* an empty list still matches nothing (#196), it is not refused;
* ``float``, ``None``, a ``dict`` and a nested list are REFUSED with
  :class:`InvalidFilterValue`.
* a ``dict`` value is where range operators would go. They are NOT supported
  (that is docs/plans/date-filtering.md's later work), and the refusal says so
  rather than leaving ``{"gte": 2025}`` to 500.

The bound is *what Qdrant can represent*, measured rather than assumed — a
grammar written loosely as "scalars, or a list of scalars" would have re-shipped
the very 500 it was meant to close:

===============================  ==========================================
``MatchValue(value=…)``          accepts ``str``/``int``/``bool``; refuses
                                 ``float`` and ``None`` as hard as a ``dict``
                                 — so ``{"score": 1.5}`` and ``{"doi": null}``
                                 were the same latent 500 as
                                 ``{"year": {"gte": 2025}}``.
``MatchAny(any=…)``              is ``list[str] | list[int]``. It refuses a
                                 ``bool`` ELEMENT (``{"is_oa": [true]}``) and
                                 a MIXED list (``{"year": [2025, "x"]}``),
                                 even though both are fine as scalars/uniform
                                 lists. ``[]`` is accepted.
===============================  ==========================================

Types are matched, never coerced. For a key in :data:`KNOWN_INT_FIELDS`
(``year``) a ``str`` — or a ``bool``, which is an ``int`` in Python but not a
year — is refused. Coercion was rejected deliberately: it would generalise
Elasticsearch's query-time laxness (``term`` on a ``long`` field happily
coerces ``"2025"``, which is exactly what let ``{"year": "2025"}`` return ten
hits on the BM25 leg and zero on the vector leg) to the whole boundary, and a
400 can be relaxed into coercion later while shipped coercion can never be
tightened back.

Enforcement is at the API boundary (``api/routers/query.py`` maps the error to
a 400) AND defensively inside every interpreter, so a CLI or a direct store
caller cannot 500 either — and so no single interpreter can quietly drift back
to its own reading. ``tests/unit/test_filter_grammar_contract.py`` drives one
shared table through all four and asserts they agree case by case.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Reserved ``Chunk`` fields that ``_chunk_from_payload`` (stores/qdrant.py)
#: pops OUT of a record's payload before it becomes ``Chunk.metadata`` — so a
#: filter on one of these could never match a real row. Defined here (not in
#: qdrant.py) because qdrant.py already imports from this module; qdrant.py
#: imports this constant back rather than the two modules duplicating it.
PAYLOAD_RESERVED = frozenset({"chunk_id", "doc_id", "content", "start_char", "end_char"})

#: Filter keys that are refused outright by ``get_chunks`` even though they
#: aren't chunk-metadata reserved fields: ``library_id`` isn't a real
#: filterable field yet, and docs/libraries-spec.md already mandates a 400 for
#: it ahead of the libraries feature landing.
_REFUSED_KEYS = PAYLOAD_RESERVED | {"library_id"}

#: Metadata fields whose values are integers, so a string is a type error
#: rather than something to coerce (module docstring, #471). ``year`` is
#: stamped as an int by the bulk loader (``ingest_jsonl.py``; see docs/API.md's
#: metadata table) and Elasticsearch dynamically maps it as a ``long`` — its
#: query-time coercion of ``"2025"`` is precisely the laxness that made the
#: same filter return ten hits on the BM25 leg and zero on the vector leg.
#: Lives here, next to :data:`PAYLOAD_RESERVED`, as the seed of the declared
#: metadata schema in docs/plans/metadata-and-kg.md.
KNOWN_INT_FIELDS = frozenset({"year"})

#: The one sentence every refusal ends with, so a caller learns the whole
#: grammar from any single 400 — including that range operators are a planned
#: feature and not a silently-ignored one.
_GRAMMAR = (
    "a filter value must be a string, an integer or a boolean, or a list of "
    "strings or a list of integers — one type per list, booleans only as "
    "scalars, an empty list matches nothing; floats, nulls, objects and nested "
    "lists are not supported, and neither are range operators such as "
    "{'gte': ...} — use exact values"
)


class InvalidFilterValue(ValueError):
    """A filters dict carried a value outside the grammar in the module
    docstring (#471).

    Raised — never coerced, never silently answered "no match" — by
    :func:`validate_filter_values` at the API boundary and again by each of the
    four filter interpreters, so the answer to an unrepresentable value is one
    400 rather than a 500 from Qdrant, an Elasticsearch error, or an empty
    result that looks like a legitimately empty corpus."""

    def __init__(self, key: str, reason: str) -> None:
        self.key = key
        super().__init__(f"unsupported filter value for {key!r}: {reason}; {_GRAMMAR}")


class UnknownFilterKey(ValueError):
    """A ``get_chunks`` filters dict carried a key that can never address a
    real chunk field (see :data:`_REFUSED_KEYS` / module docstring). Raised
    rather than silently ignored so a caller can't believe an unsupported
    scope constraint took effect."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"unsupported get_chunks filter key: {key!r}")


def _resolve_key(key: str) -> str:
    """The metadata field ``key`` addresses — the key itself, looked up
    literally like ``_matches`` does — or raise if it is in the refused set
    (module docstring)."""
    if key in _REFUSED_KEYS:
        raise UnknownFilterKey(key)
    return key


def validate_filters(filters: Mapping[str, Any] | None) -> None:
    """Raise :class:`UnknownFilterKey` if ``filters`` carries a refused key
    (module docstring); otherwise a no-op.

    Callers MUST run this before anything data-dependent (an early return on
    empty ids/tenants, a store round trip that might come back empty) — a
    refused key has to reject the call outright, not just the records that
    happen to be returned. ``payload_matches`` alone can't guarantee that: zero
    matching records means its loop body never runs and the bad key silently
    slips through. Validated once per call, not once per candidate record."""
    if not filters:
        return
    for key in filters:
        _resolve_key(key)


def _check_value(key: str, value: Any, *, in_list: bool) -> None:
    """Raise :class:`InvalidFilterValue` unless ``value`` is a single value the
    grammar admits (``str``/``int``/``bool``, plus the ``KNOWN_INT_FIELDS``
    type rule). Lists are unwrapped by :func:`validate_filter_values` and each
    element arrives here with ``in_list=True`` — a list inside a list is a
    nested list, which is refused."""
    where = f"list element {value!r}" if in_list else f"{value!r}"
    if isinstance(value, dict):
        raise InvalidFilterValue(
            key, f"{where} is an object — range operators are not supported"
        )
    # ``bool`` is a subclass of ``int``, so this admits True/False as intended.
    # ``float`` is not: Qdrant's MatchValue refuses it, so admitting it here
    # would only move the 500 one layer down.
    if not isinstance(value, (str, int)):
        raise InvalidFilterValue(key, f"{where} has unsupported type {type(value).__name__}")
    if key in KNOWN_INT_FIELDS and (isinstance(value, bool) or not isinstance(value, int)):
        raise InvalidFilterValue(
            key,
            f"{where} is a {type(value).__name__} but {key!r} is an integer field "
            f"(values are matched by type, not coerced)",
        )


def _kind(value: Any) -> str:
    """The list-element type for the homogeneity rule. ``bool`` is its own kind
    even though ``isinstance(True, int)`` is True in Python — Qdrant's
    ``MatchAny`` is ``list[str] | list[int]`` and rejects a bool element."""
    if isinstance(value, bool):
        return "bool"
    return "int" if isinstance(value, int) else type(value).__name__


def _check_list(key: str, values: Any) -> None:
    """Raise :class:`InvalidFilterValue` unless ``values`` is a list Qdrant's
    ``MatchAny`` can carry: every element admissible on its own, no ``bool``
    elements, and one single type across the whole list. An empty list is
    valid — it matches nothing (#196).

    Homogeneity cannot be decided per element, which is why this exists next to
    :func:`_check_value` and is called from BOTH the boundary validator and the
    per-record predicate: the two must not drift."""
    seen: set[str] = set()
    for item in values:
        _check_value(key, item, in_list=True)
        if isinstance(item, bool):
            raise InvalidFilterValue(
                key, f"list element {item!r} is a bool — booleans are scalar-only"
            )
        seen.add(_kind(item))
    if len(seen) > 1:
        raise InvalidFilterValue(
            key,
            f"list mixes {' and '.join(sorted(seen))} values — a list must be "
            f"all strings or all integers",
        )


def validate_filter_values(filters: Mapping[str, Any] | None) -> None:
    """Raise :class:`InvalidFilterValue` if any value in ``filters`` is outside
    the grammar in the module docstring (#471); otherwise a no-op.

    Like :func:`validate_filters`, callers MUST run this **data-independently**
    — before any early return on empty ids/tenants and before any store round
    trip — so an unrepresentable value refuses the call outright instead of
    only the records that happen to come back. An empty list is a valid value
    (it matches nothing, #196), so it is not refused here.

    Keys are not inspected beyond :data:`KNOWN_INT_FIELDS`; that is
    :func:`validate_filters`' job."""
    if not filters:
        return
    for key, value in filters.items():
        if isinstance(value, (list, tuple, set)):
            _check_list(key, value)
        else:
            _check_value(key, value, in_list=False)


def payload_matches(metadata: Mapping[str, Any], filters: Mapping[str, Any] | None) -> bool:
    """Does ``metadata`` (a Chunk's metadata dict — the same shape from both
    stores; see ``_chunk_from_payload`` in stores/qdrant.py) satisfy every key
    in ``filters``?

    ``None`` or an empty ``filters`` is unconstrained (matches everything).
    Assumes ``filters`` already passed :func:`validate_filters` — callers that
    skip straight to this without a data-independent call to it first can end
    up refusing only when a record happens to come back (see that function's
    docstring). Still raises :class:`UnknownFilterKey` / :class:`InvalidFilterValue`
    itself if handed an unvalidated, refused key or value, as a second line of
    defence — answering ``False`` for a value this predicate cannot represent
    would be indistinguishable from an honest miss (#471)."""
    if not filters:
        return True
    for key, value in filters.items():
        field = _resolve_key(key)
        actual = metadata.get(field)
        if isinstance(value, (list, tuple, set)):
            _check_list(key, value)
            if actual not in value:
                return False
        else:
            _check_value(key, value, in_list=False)
            if actual != value:
                return False
    return True
