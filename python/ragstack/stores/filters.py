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
* ``metadata.<key>`` is accepted too, as an explicit alias: the prefix is
  stripped before lookup, so ``metadata.source`` and bare ``source`` address
  the same field. Useful for a caller that wants to be unambiguous that a key
  targets metadata rather than a reserved/refused one below.
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
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_METADATA_PREFIX = "metadata."

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


class UnknownFilterKey(ValueError):
    """A ``get_chunks`` filters dict carried a key that can never address a
    real chunk field (see :data:`_REFUSED_KEYS` / module docstring). Raised
    rather than silently ignored so a caller can't believe an unsupported
    scope constraint took effect."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"unsupported get_chunks filter key: {key!r}")


def _resolve_key(key: str) -> str:
    """The metadata field ``key`` addresses, or raise if ``key`` (after
    stripping an optional ``metadata.`` prefix) is in the refused set (module
    docstring)."""
    field = key[len(_METADATA_PREFIX) :] if key.startswith(_METADATA_PREFIX) else key
    if field in _REFUSED_KEYS:
        raise UnknownFilterKey(key)
    return field


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


def payload_matches(metadata: Mapping[str, Any], filters: Mapping[str, Any] | None) -> bool:
    """Does ``metadata`` (a Chunk's metadata dict — the same shape from both
    stores; see ``_chunk_from_payload`` in stores/qdrant.py) satisfy every key
    in ``filters``?

    ``None`` or an empty ``filters`` is unconstrained (matches everything).
    Assumes ``filters`` already passed :func:`validate_filters` — callers that
    skip straight to this without a data-independent call to it first can end
    up refusing only when a record happens to come back (see that function's
    docstring). Still raises :class:`UnknownFilterKey` itself if handed an
    unvalidated, refused key, as a second line of defence."""
    if not filters:
        return True
    for key, value in filters.items():
        field = _resolve_key(key)
        actual = metadata.get(field)
        if isinstance(value, (list, tuple, set)):
            if actual not in value:
                return False
        elif actual != value:
            return False
    return True
