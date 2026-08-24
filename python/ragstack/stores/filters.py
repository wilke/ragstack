"""Shared filter-matching predicate for stores' ``get_chunks`` (#197, phase 1
of #201).

``_build_filter`` (stores/qdrant.py) and ``_matches`` (stores/memory.py) build
and evaluate filters permissively for ``search()``: any key is accepted and
applied by literal match against the Qdrant payload / Chunk metadata.
``get_chunks`` needs a stricter contract than that. It resolves ids by point
id — an O(ids) ``retrieve``, not a store-side filtered scan — so *every other*
filter key has to be re-applied against the returned payload in Python, and it
has to be applied identically by both stores or ADR-0003's "the leg must not
depend on one store getting it right" rule breaks. Silently dropping a key
here is worse than dropping one in ``search()``: the caller believes a scope
constraint took effect when it never touched the result.

Grammar (deliberately narrow — widen only when a real caller needs it):

* ``tenant_id`` — the reader's tenant scope (own + public), the same field
  ``_build_filter``/``_matches`` use.
* ``collection`` — phase-1 prep for #201's per-library payload stamp. No
  ingest path writes this key onto a chunk yet (see ``ingestion/pipeline.py``:
  the vector store IS per-collection, so it carries that boundary implicitly),
  but the predicate enforces it now so a later ingestion change lights it up
  everywhere at once instead of silently no-op'ing wherever nobody remembered
  to wire it in.
* ``metadata.<key>`` — an arbitrary chunk metadata field, prefix stripped
  before lookup (both stores keep metadata flat, so ``metadata.source`` and a
  bare ``source`` address the same field — the prefix just makes "this
  targets metadata" explicit at the call site, mirroring the ``metadata.<key>``
  field addressing ``_build_query`` uses against Elasticsearch).

Any other key raises :class:`UnknownFilterKey` — refuse, don't ignore.

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
_KNOWN_BARE_KEYS = frozenset({"tenant_id", "collection"})


class UnknownFilterKey(ValueError):
    """A ``get_chunks`` filters dict carried a key outside the supported
    grammar (see module docstring). Raised rather than silently ignored so a
    caller can't believe an unsupported scope constraint took effect."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"unsupported get_chunks filter key: {key!r}")


def _resolve_key(key: str) -> str:
    """The metadata field ``key`` addresses, or raise if it isn't in the
    supported grammar (module docstring)."""
    if key in _KNOWN_BARE_KEYS:
        return key
    if key.startswith(_METADATA_PREFIX) and len(key) > len(_METADATA_PREFIX):
        return key[len(_METADATA_PREFIX) :]
    raise UnknownFilterKey(key)


def validate_filters(filters: Mapping[str, Any] | None) -> None:
    """Raise :class:`UnknownFilterKey` if ``filters`` carries a key outside the
    supported grammar (module docstring); otherwise a no-op.

    Callers MUST run this before anything data-dependent (an early return on
    empty ids/tenants, a store round trip that might come back empty) — an
    unsupported key has to refuse the call outright, not just the records that
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
    unvalidated, out-of-grammar key, as a second line of defence."""
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
