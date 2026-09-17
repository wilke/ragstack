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
from dataclasses import dataclass
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


# --------------------------------------------------------------------------- #
# The declared field table (#603)
#
# Mirrors ``contracts/schemas/chunk_metadata.json``, which is the source of
# truth; ``tests/unit/test_chunk_metadata_schema.py`` pins the two field for
# field, so a change to one that is not made to the other fails the suite.
#
# Why a mirror rather than loading the JSON: ``contracts/`` is not packaged into
# the wheel (the API ships from ``python/``), and this module's stdlib-only,
# import-anything-cheaply promise is the reason ``ingestion/jats.py`` can import
# it inside a CPU-only CWL worker that runs once per document across a 1.44M
# document corpus. A runtime file read — from a path that may not exist — would
# trade a proven property for a convenience.
# --------------------------------------------------------------------------- #

#: Longest keyword value Elasticsearch will index for exact match. A keyword is
#: ONE Lucene term and a term over 32766 BYTES aborts the whole bulk request with
#: a document_parsing_exception; 8191 characters is the largest bound that stays
#: under that even for 4-byte UTF-8. Over-long values are still stored in
#: ``_source`` and still returned — they are only unindexed. Lives here rather
#: than in ``stores/elasticsearch.py`` because the derived mapping is built here;
#: that module re-exports it under its historical name.
KEYWORD_IGNORE_ABOVE = 8191

#: Elasticsearch nests chunk metadata under this object, so ``pmid`` is addressed
#: as ``metadata.pmid``. Qdrant has no equivalent — it keeps the same fields FLAT
#: at the payload top level. See :func:`es_field_path` / :func:`qdrant_field_path`.
ES_METADATA_CONTAINER = "metadata"


@dataclass(frozen=True)
class DeclaredField:
    """One row of the declared chunk-metadata schema.

    ``kind`` is the JSON type of a *value* (``string`` / ``integer`` /
    ``boolean``); ``array`` says whether the field holds a list of those rather
    than one. ``required`` is true for exactly one field — see
    :data:`DECLARED_FIELDS`.
    """

    name: str
    kind: str
    array: bool = False
    required: bool = False

    @property
    def elasticsearch_mapping(self) -> dict[str, Any]:
        """The ES mapping fragment for this field.

        A keyword rather than ``text`` because filters are exact term matches,
        not BM25 — ``content`` is the only analyzed field in the index. An array
        needs no special mapping: ES maps a field by its element type and an
        array of strings is just repeated terms on the same keyword field.
        """
        if self.kind == "integer":
            return {"type": "long"}
        if self.kind == "boolean":
            return {"type": "boolean"}
        return {"type": "keyword", "ignore_above": KEYWORD_IGNORE_ABOVE}


def _f(name: str, kind: str, *, array: bool = False, required: bool = False) -> DeclaredField:
    return DeclaredField(name=name, kind=kind, array=array, required=required)


#: Every chunk-metadata field this project declares, by name.
#:
#: **Exactly one field is required.** ``tenant_id`` is the row-level isolation
#: boundary (ADR-0003): every store write is scoped by it and ``scope_filters``
#: pins reads to it, so a chunk without one is not a chunk that can be retrieved
#: safely. Everything else is optional **and absence is a state of its own** —
#: nothing here may be defaulted. ``is_boilerplate`` is the case that proves it:
#: it is stamped only on a chunk a boilerplate filter classified as non-body, so
#: ``{"is_boilerplate": false}`` matches nothing and /v1/query's
#: ``exclude_boilerplate`` had to be built as a negation of ``true`` (#601).
#: Backfilling a ``false`` would reclassify every chunk ingested before #597 from
#: "never classified" to "asserted not boilerplate".
#:
#: The table is OPEN: an undeclared key is allowed through and left to ES's
#: ``metadata_strings_as_keyword`` dynamic template (strings) or to dynamic
#: inference (everything else — which is exactly how ``metadata.pmid`` became a
#: ``long`` on one collection). Declaring a field is what moves it from inferred
#: to stated; ``ragstack.ops.metadata_conformance`` reports what a live
#: collection carries that is not declared here.
DECLARED_FIELDS: dict[str, DeclaredField] = {
    f.name: f
    for f in (
        # The isolation boundary.
        _f("tenant_id", "string", required=True),
        # Identifiers. All strings: opaque, and nothing range-filters them. The
        # NCBI ID Converter returns `pmid` as a JSON int and a repair tool wrote
        # that through (#594), which is how one collection ended up with a `long`
        # mapping that cannot be changed in place.
        _f("pmid", "string"),
        _f("pmcid", "string"),
        _f("doi", "string"),
        _f("doi_source", "string"),
        _f("doi_enriched_from", "string"),
        _f("metadata_source", "string"),
        _f("sha256", "string"),
        # Bibliographic.
        _f("title", "string"),
        _f("authors", "string", array=True),
        _f("keywords", "string", array=True),
        _f("journal", "string"),
        _f("publisher", "string"),
        _f("publication_type", "string"),
        _f("year", "integer"),
        # Publication date as a packed yyyymmdd integer, unknown components 0:
        # 19860000 is "1986, month and day unknown", 19820300 is "March 1982".
        # The packing is deliberate and is NOT a poor substitute for a date type
        # (docs/plans/date-filtering.md, decided 2026-09-17): the filter grammar
        # ANDs its terms and has no OR, so a split y/m/d needs a disjunction that
        # does not exist, while `date >= 20200315` needs only `gte`. The zeros are
        # self-describing — `% 10000 == 0` is year-only, `% 100 == 0` has no day —
        # so no separate precision field is needed, and unlike a real date type it
        # never has to invent 1986-01-01 and then fail to record that it did.
        # INVARIANT: where both exist, `year == date // 10000`. `year` is derived,
        # never captured separately. Verified 0 violations across open-access,
        # asm-semantic and ragstack_sfr_semantic_full (61M chunks) on 2026-09-17.
        _f("date", "integer"),
        # Classification. `section`/`is_boilerplate` are stamped only on a
        # non-body verdict, so absence is "body, or never classified".
        _f("doc_type", "string"),
        _f("content_type", "string"),
        _f("section", "string"),
        _f("section_title", "string"),
        _f("is_boilerplate", "boolean"),
        # Provenance of the artifact.
        _f("source_path", "string"),
        _f("filename", "string"),
        _f("source", "string"),
        _f("source_url", "string"),
        _f("url", "string"),
        _f("licence", "string"),
        _f("graphic", "string"),
        # Structure. `prev_chunk_id`/`next_chunk_id` are deliberately written as
        # None at a document's edges (chunkers.link_neighbors), which is why a
        # null is accepted wherever a value is.
        _f("chunk_index", "integer"),
        _f("prev_chunk_id", "string"),
        _f("next_chunk_id", "string"),
        # Counts.
        _f("n_citations", "integer"),
        _f("n_tables", "integer"),
        _f("n_figures", "integer"),
        _f("pages", "integer"),
    )
}

#: Names of the declared fields whose values are integers. NOT the same set as
#: :data:`KNOWN_INT_FIELDS`, and deliberately so: this one says what a PRODUCER
#: must write, while ``KNOWN_INT_FIELDS`` says which keys a CALLER's filter value
#: is type-checked against — and widening that is a behaviour change (a
#: ``{"chunk_index": "3"}`` that is a 200-with-zero-hits today would become a
#: 400), which needs its own contract note and conformance case. ``KNOWN_INT_FIELDS``
#: is a subset of this set; a test asserts it stays one.
INT_FIELDS = frozenset(n for n, f in DECLARED_FIELDS.items() if f.kind == "integer")

#: The one field a chunk may not reach a store without.
REQUIRED_FIELDS = frozenset(n for n, f in DECLARED_FIELDS.items() if f.required)


def es_field_path(name: str) -> str:
    """Where Elasticsearch keeps ``name`` — nested under ``metadata``.

    The ES/Qdrant shape difference is stated HERE and in
    ``contracts/schemas/chunk_metadata.json``'s ``x-ragstack-store-shape``, and
    nowhere else. Both #594 and #601 had to special-case each store because the
    difference lived only in each writer's head, and a writer that updates one
    store and not the other leaves the two retrieval legs disagreeing about the
    same chunk with no count-based check able to notice.

    Note this is the STORAGE path, not the filter key: the caller-facing filter
    grammar is bare in both stores (``{"journal": "mBio"}``) and it is
    ``_build_query`` in ``stores/elasticsearch.py`` that applies this prefix.
    """
    return f"{ES_METADATA_CONTAINER}.{name}"


def qdrant_field_path(name: str) -> str:
    """Where Qdrant keeps ``name`` — FLAT, at the payload top level, sharing the
    namespace with the five reserved point fields (``chunk_id``, ``doc_id``,
    ``content``, ``start_char``, ``end_char``; ``stores.filters.PAYLOAD_RESERVED``
    is the list, and ``upsert`` drops a metadata key that collides with one)."""
    return name


def elasticsearch_metadata_properties() -> dict[str, dict[str, Any]]:
    """The ``mappings.properties.metadata.properties`` block, derived from the
    table above.

    This is the point of the whole exercise: an index CREATED with these
    properties has its declared fields typed by decision rather than by whichever
    document happened to land first, and an ES mapping cannot be changed in
    place. An undeclared field still falls through to the dynamic template, so
    this does not close the namespace — it only stops the fields we have an
    opinion about from being inferred.
    """
    return {name: f.elasticsearch_mapping for name, f in DECLARED_FIELDS.items()}


class ChunkMetadataTypeError(ValueError):
    """A chunk carried a declared field with a type the schema does not allow.

    Raised at the ingest boundary — the cheap place. The expensive place is an
    Elasticsearch mapping, which is inferred from the first document to arrive
    and then cannot be changed: by the time the divergence is visible, the fix is
    a reindex of the whole collection.
    """

    def __init__(self, problems: list[str], where: str = "") -> None:
        self.problems = problems
        subject = f"{where}: " if where else ""
        super().__init__(
            subject
            + "chunk metadata does not match the declared schema "
            + "(contracts/schemas/chunk_metadata.json): "
            + "; ".join(problems)
        )


def _type_problem(field: DeclaredField, value: Any) -> str | None:
    """Why ``value`` is not a legal value for ``field``, or ``None`` if it is.

    A ``None`` is legal for any optional field and means exactly what absence
    means — ES does not index a null, and ``chunkers.link_neighbors`` writes one
    into ``prev_chunk_id``/``next_chunk_id`` at a document's edges on purpose.
    """
    if value is None:
        return None if not field.required else f"{field.name!r} is required but null"
    if field.array:
        if not isinstance(value, (list, tuple)):
            return (
                f"{field.name!r} has type {type(value).__name__} but the schema declares "
                f"a list of {field.kind}s"
            )
        for element in value:
            if _scalar_problem(field.kind, element) is not None:
                return (
                    f"{field.name!r} contains an element of type {type(element).__name__} but the "
                    f"schema declares a list of {field.kind}s"
                )
        return None
    reason = _scalar_problem(field.kind, value)
    if reason is None:
        return None
    return f"{field.name!r} has type {type(value).__name__} but the schema declares {reason}"


def _scalar_problem(kind: str, value: Any) -> str | None:
    """``None`` if ``value`` is of ``kind``, else the kind's name for the message.

    ``bool`` is a subclass of ``int`` in Python and is refused for an integer
    field anyway: ``True`` is not a year, and Elasticsearch would map it to a
    different type than the field declares.
    """
    if kind == "integer":
        return None if isinstance(value, int) and not isinstance(value, bool) else "an integer"
    if kind == "boolean":
        return None if isinstance(value, bool) else "a boolean"
    return None if isinstance(value, str) else "a string"


def metadata_problems(metadata: Any) -> list[str]:
    """Every way ``metadata`` departs from the declared schema, as messages.

    Report-shaped rather than raise-shaped so the same check can drive a
    diagnostic that lists everything wrong with a corpus. Undeclared keys are
    NOT problems — the namespace is open by design (see :data:`DECLARED_FIELDS`);
    they are reported by the conformance tool against live collections instead,
    where the question is "should this be declared?" rather than "is this ingest
    valid?".
    """
    if not isinstance(metadata, dict):
        return [f"metadata has type {type(metadata).__name__}, not an object"]
    problems: list[str] = []
    for name in REQUIRED_FIELDS:
        if name not in metadata:
            problems.append(f"{name!r} is required and absent")
    # Iterate the metadata, not the table: a chunk carries ~20 keys and the table
    # has 33, and this runs once per chunk across corpora of 47.6M of them.
    for key, value in metadata.items():
        field = DECLARED_FIELDS.get(key)
        if field is None:
            continue
        problem = _type_problem(field, value)
        if problem is not None:
            problems.append(problem)
    return problems


def validate_chunk_metadata(metadata: Any, *, where: str = "") -> None:
    """Raise :class:`ChunkMetadataTypeError` unless ``metadata`` matches the
    declared schema. ``where`` names the offending record in the message."""
    problems = metadata_problems(metadata)
    if problems:
        raise ChunkMetadataTypeError(problems, where)


def validate_chunks(chunks: Any, *, where: str = "") -> None:
    """Validate a batch of objects carrying ``.metadata`` and ``.id``.

    The ingest-boundary form: called by ``IngestionPipeline.index_chunks`` and by
    the bulk loaders that write to the stores without it. Fails on the FIRST bad
    chunk rather than collecting the batch's problems — a batch is written
    atomically enough that one bad record makes the whole write wrong, and a
    47.6M-chunk run should stop at the first one, not accumulate a report.
    """
    for chunk in chunks:
        problems = metadata_problems(getattr(chunk, "metadata", None))
        if problems:
            subject = f"{where} chunk {getattr(chunk, 'id', '?')!r}" if where else (
                f"chunk {getattr(chunk, 'id', '?')!r}"
            )
            raise ChunkMetadataTypeError(problems, subject)
