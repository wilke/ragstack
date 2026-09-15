"""The declared TYPE of each chunk-metadata field, and the coercions that enforce it.

A dependency-free leaf. Import it from anywhere — a producer, a store, a CWL
worker step — without dragging in pydantic, qdrant-client or neo4j. That is the
whole reason it is its own module rather than living in ``stores/filters.py``
(which cannot be imported without executing ``ragstack.stores.__init__``) or in
``ingestion/enrich.py`` (which imports pydantic): ``ingestion/jats.py`` runs
inside the CPU-only CWL extract worker, once per document across a 1.44M-document
corpus, and its module docstring's "stdlib only" promise is a cost statement, not
a style note.

.. rubric:: Why a table at all

``contracts/schemas/query_request.json`` states the rule: filter values are
matched **by type, never coerced** — ``year`` is an integer field, so
``{"year": "2025"}`` is a 400 rather than a silent zero-hit read (#471). That
rule is only half an answer. A producer that *stores* ``year`` as a string builds
documents no correct filter can ever reach, and nothing errors: the query
succeeds and quietly omits them. Measured read-only on 2026-09-15, the ``lucid``
tenant's collection stores ``year`` as a string, and ``{"year": 2021}`` — the
only form the API accepts — matches 129,248 chunks on the Elasticsearch leg (ES
coerces at query time) and **0** on the Qdrant leg (Qdrant compares payload types
exactly).

So the declared type has to be readable from both ends: the filter validator asks
"may a caller send this?", and every producer asks "what must I write?". One
table, consulted by both.
"""
from __future__ import annotations

import re
from datetime import date as _date
from typing import Any

#: Metadata fields whose values are integers, so a string is a type error rather
#: than something to coerce at query time (#471). Re-exported by
#: ``stores.filters`` — which is where the *filter* side reads it — and consulted
#: by ``ingestion.loaders`` on the *producer* side.
#:
#: Deliberately still just ``year``: widening it is a behaviour change on the
#: filter side (``{"pages": "12"}`` becomes a 400 where it is a 200-with-zero-hits
#: today) and needs a contract note plus a conformance case. ``chunk_index``,
#: ``n_citations``, ``pages``, ``n_tables`` and ``n_figures`` are ints in every
#: producer and every live collection sampled, but nothing yet *enforces* that.
KNOWN_INT_FIELDS = frozenset({"year"})

#: Oldest year a scholarly record may plausibly *declare*. PMC carries articles
#: back to the early 1800s (*Med Chir Trans*, 1809), so the floor sits well below
#: that rather than being tuned to the current corpus.
MIN_PLAUSIBLE_YEAR = 1500

#: A 4-digit run that is the WHOLE token. Without the boundaries ``"20191"`` and
#: ``"PMC2019123"`` both read as 2019 — a confident wrong year, which is worse
#: than none.
_FOUR_DIGITS = re.compile(r"(?<!\d)(\d{4})(?!\d)")


def plausible_year_range() -> tuple[int, int]:
    """``(min, max)`` any publication year must fall in — ``max`` is next year.

    Computed per call rather than frozen at import so a long-lived process does
    not start refusing January's ahead-of-print records.

    The upper bound is not hypothetical hygiene. Parse errors produce *futures*,
    not pasts, and they are already in production: 8,408 chunks on
    ``open-access`` and **16,176 across ASM's three indices** carry a year after
    2026 (measured read-only, 2026-09-15). They actively corrupt any "recent"
    query — a "2027 or later" filter returns them and nothing else.

    This is the **validity** bound and it applies to every year from every
    source. It is not the same thing as the narrow window the *inference* regexes
    in ``ingestion.enrich`` scan for; see :func:`coerce_year`.
    """
    return MIN_PLAUSIBLE_YEAR, _date.today().year + 1


def coerce_year(value: Any) -> int | None:
    """The one place a raw ``year`` becomes the ``int`` the filter grammar demands.

    Accepts an ``int`` or a string containing a 4-digit year as a whole token
    (JATS ``<year>`` is occasionally ``"2019 Mar"`` or ``"c2019"``); returns
    ``None`` — meaning *omit the key*, never store a null or an empty string —
    for anything else, including a year outside :func:`plausible_year_range`.
    ``bool`` is refused explicitly: it is an ``int`` in Python but it is not a
    year.

    .. rubric:: Two windows, and why they differ

    ``enrich``'s ``_YEAR`` / ``_YEAR_IN_TEXT`` regexes match only 1950–2049. That
    is an **inference precision heuristic** — a bare 4-digit number in a path or
    in prose is only *probably* a year, and a narrow window is what keeps the
    guess cheap. This function is the **validity bound**, 1500..next year, and it
    applies to a year however it was obtained. They are not competing
    definitions: a declared ``<year>1809</year>`` is trusted and accepted, while
    an inferred 1809 is never even proposed, because inferring a year from an
    1809-shaped number in a file path is not a guess worth making. Every arm of
    ``derive_year`` passes its result through here, so the intersection —
    1950..next year — is what inference can actually yield.
    """
    if value is None or isinstance(value, bool):
        return None
    lo, hi = plausible_year_range()
    if isinstance(value, int):
        return value if lo <= value <= hi else None
    if isinstance(value, float):
        # A JSON float that is exactly an integer year (2019.0) is a year; a
        # fractional one is not a year at all.
        return coerce_year(int(value)) if value.is_integer() else None
    if isinstance(value, str):
        m = _FOUR_DIGITS.search(value)
        return coerce_year(int(m.group(1))) if m else None
    return None


def coerce_int(value: Any) -> int | None:
    """``value`` as an ``int``, or ``None`` (drop it) if it is not one.

    The generic arm of the declared-int rule, for whatever
    :data:`KNOWN_INT_FIELDS` grows to hold. ``year`` has :func:`coerce_year`
    instead, because it carries plausibility bounds no other integer field does.
    ``bool`` is not an integer field value even though Python says
    ``isinstance(True, int)``."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def coerce_declared(key: str, value: Any) -> Any | None:
    """Coerce ``value`` to the type this table declares for ``key``.

    Returns the value unchanged for a key the table says nothing about, and
    ``None`` for a declared field whose value cannot be represented — "drop the
    key", which is always safe: an absent field is honestly unmatched, where a
    wrong-typed one is silently unreachable."""
    if key not in KNOWN_INT_FIELDS:
        return value
    return coerce_year(value) if key == "year" else coerce_int(value)
