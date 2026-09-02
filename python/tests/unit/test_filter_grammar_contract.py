"""ONE table, four interpreters: the filter-value grammar contract (#471).

RAGStack reads the same caller-supplied ``filters`` dict through four separate
pieces of code:

* ``_build_filter``   — stores/qdrant.py       (Qdrant ``Filter``)
* ``_build_query``    — stores/elasticsearch.py (ES ``bool`` query)
* ``_matches``        — stores/memory.py        (in-memory ``search()``)
* ``payload_matches`` — stores/filters.py       (both stores' ``get_chunks``)

Before #471 they disagreed **three ways** on a value none of them could
represent. ``{"year": {"gte": 2025}}`` was:

* a **500** on the vector leg — ``MatchValue(value={"gte": 2025})`` raises a
  ``pydantic`` ``ValidationError`` that escaped into Starlette's generic
  handler;
* a search **error** on the BM25 leg — an object inside a ``term`` query;
* a silent **"no match"** in the two Python predicates.

So the answer to one request depended on which retrieval leg ran. And the
grammar could not be written loosely as "scalars": ``MatchValue`` refuses
``float`` and ``None`` exactly as hard as it refuses a dict, so ``{"score":
1.5}`` was the same latent 500.

The fix is a shared validator; **this file is what stops the four from
re-diverging.** It is deliberately a single ``CASES`` table driven through all
four interpreters rather than four per-store test files: a new interpreter
behaviour has to be added here once, for everybody, and any single
interpreter that drifts back to its own reading fails the row.

The assertion per row is *agreement*, not just correctness: either all four
accept, or all four raise the same typed ``InvalidFilterValue``. A mutation
that makes one interpreter silently answer ``False`` for an invalid value —
the pre-#471 in-memory behaviour — fails here even though it raises no error
anywhere.

Imports are direct, NOT ``importorskip``: qdrant-client and elasticsearch are
in the ``[all,dev]`` extra CI installs, and a leg that silently skips is a leg
that has stopped guarding anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from ragstack.models import Chunk
from ragstack.stores.elasticsearch import _build_query
from ragstack.stores.filters import (
    KNOWN_INT_FIELDS,
    InvalidFilterValue,
    payload_matches,
    validate_filter_values,
)
from ragstack.stores.memory import _matches
from ragstack.stores.qdrant import _build_filter

# The synthetic record every predicate leg is evaluated against. `year` is an
# int here on purpose: it is the field whose ES dynamic mapping (`long`) made a
# string year match on the BM25 leg and nothing on the vector leg.
CHUNK = Chunk(
    id="c1",
    doc_id="d1",
    content="a chunk",
    metadata={
        "tenant_id": "acme",
        "doc_type": "article",
        "year": 2025,
        "is_oa": True,
    },
)

# `_build_query` fails closed on a missing/empty tenant filter, so every row
# carries a tenant key. It is a list of strings — the shape `scope_filters`
# actually produces — which doubles as proof the grammar admits what the
# tenancy layer merges in.
TENANT: dict[str, Any] = {"tenant_id": ["acme", "public"]}


@dataclass(frozen=True)
class Case:
    """One filters dict and what all four interpreters must do with it."""

    name: str
    #: Merged on top of :data:`TENANT` to form the filters dict under test.
    filters: dict[str, Any]
    #: True when every interpreter must accept the value.
    accepted: bool
    #: Substrings the refusal message must contain (empty when accepted). They
    #: are what makes the test fail for the RIGHT reason: deleting `year` from
    #: KNOWN_INT_FIELDS still refuses `{"year": {"gte": 1}}` on shape, but
    #: stops naming `year` on the string-year rows.
    message_contains: tuple[str, ...] = ()
    #: For accepted rows: whether CHUNK satisfies the filter. Both predicate
    #: legs must agree on this, so an "accepted" row still pins semantics
    #: (notably the empty list, which matches nothing — #196).
    matches: bool = False
    #: For accepted rows: the Qdrant match kind ("value" | "any") and the ES
    #: clause kind ("term" | "terms") the builders must produce.
    qdrant_match: str = ""
    es_clause: str = ""
    marks: tuple[Any, ...] = field(default=())


CASES: list[Case] = [
    # ---------------------------------------------------------------- valid --
    Case(
        "str-scalar",
        {"doc_type": "article"},
        accepted=True,
        matches=True,
        qdrant_match="value",
        es_clause="term",
    ),
    Case(
        "int-scalar",
        {"year": 2025},
        accepted=True,
        matches=True,
        qdrant_match="value",
        es_clause="term",
    ),
    Case(
        "bool-scalar",
        {"is_oa": True},
        accepted=True,
        matches=True,
        qdrant_match="value",
        es_clause="term",
    ),
    Case(
        "str-scalar-miss",
        {"doc_type": "supplement"},
        accepted=True,
        matches=False,
        qdrant_match="value",
        es_clause="term",
    ),
    Case(
        "str-list",
        {"doc_type": ["article", "supplement"]},
        accepted=True,
        matches=True,
        qdrant_match="any",
        es_clause="terms",
    ),
    Case(
        "int-list",
        {"year": [2025, 2026]},
        accepted=True,
        matches=True,
        qdrant_match="any",
        es_clause="terms",
    ),
    Case(
        "int-list-miss",
        {"year": [2023, 2024]},
        accepted=True,
        matches=False,
        qdrant_match="any",
        es_clause="terms",
    ),
    # #196: an empty list is a real, unsatisfiable constraint — NOT refused,
    # and NOT "unconstrained". Refusing it here would break the fail-closed
    # reading of an empty tenant scope.
    Case(
        "empty-list-matches-nothing",
        {"doc_type": []},
        accepted=True,
        matches=False,
        qdrant_match="any",
        es_clause="terms",
    ),
    # -------------------------------------------------------------- refused --
    # The reported defect: an object value. Qdrant 500'd on it.
    Case(
        "dict-range-operator",
        {"year": {"gte": 2025}},
        accepted=False,
        message_contains=("year", "object", "range operators are not supported"),
    ),
    Case(
        "dict-on-a-string-field",
        {"doc_type": {"eq": "article"}},
        accepted=False,
        message_contains=("doc_type", "object"),
    ),
    # NOT in the original framing: MatchValue refuses float and None too, so a
    # grammar written as "any scalar" would have re-shipped the 500.
    Case(
        "float-scalar",
        {"score": 1.5},
        accepted=False,
        message_contains=("score", "float"),
    ),
    Case(
        "float-that-is-integral",
        {"year": 2025.0},
        accepted=False,
        message_contains=("year", "float"),
    ),
    Case(
        "none-scalar",
        {"doi": None},
        accepted=False,
        message_contains=("doi", "NoneType"),
    ),
    Case(
        "float-in-list",
        {"score": [1.5]},
        accepted=False,
        message_contains=("score", "float"),
    ),
    Case(
        "none-in-list",
        {"doi": [None]},
        accepted=False,
        message_contains=("doi", "NoneType"),
    ),
    Case(
        "nested-list",
        {"tags": [["a"]]},
        accepted=False,
        message_contains=("tags", "list"),
    ),
    Case(
        "dict-in-list",
        {"year": [{"gte": 2025}]},
        accepted=False,
        message_contains=("year", "object"),
    ),
    # Qdrant's MatchAny is `list[str] | list[int]` — NOT "a list of scalars".
    # These two rows are what a grammar written as "scalars, or a list of
    # scalars" would have let through as a FRESH 500 (`MatchValue` takes a
    # bool, `MatchAny` does not); this table is what caught them.
    Case(
        "bool-in-list",
        {"is_oa": [True]},
        accepted=False,
        message_contains=("is_oa", "bool", "scalar-only"),
    ),
    Case(
        "mixed-str-int-list",
        {"doc_type": ["article", 3]},
        accepted=False,
        message_contains=("doc_type", "all strings or all integers"),
    ),
    # ------------------------------------------------- type mismatch (#471) --
    # Refused, not coerced: coercion would generalise ES's query-time laxness —
    # the very mechanism that hid this defect — to the whole boundary.
    Case(
        "str-year-scalar",
        {"year": "2025"},
        accepted=False,
        message_contains=("year", "str", "integer field"),
    ),
    Case(
        "str-year-in-list",
        {"year": ["2025"]},
        accepted=False,
        message_contains=("year", "str", "integer field"),
    ),
    # `bool` is an `int` subclass in Python, which is not a reason to accept it
    # as a publication year.
    Case(
        "bool-year",
        {"year": True},
        accepted=False,
        message_contains=("year", "bool", "integer field"),
    ),
]

IDS = [c.name for c in CASES]


def _filters(case: Case) -> dict[str, Any]:
    return {**TENANT, **case.filters}


def _key(case: Case) -> str:
    """The single non-tenant key the case constrains."""
    (key,) = case.filters
    return key


def _assert_same_value(where: str, got: Any, expected: Any) -> None:
    """``got`` must be ``expected`` — same value AND same type, element by
    element for a list.

    Type-strict on purpose. ``==`` alone would accept two coercions this test
    exists to catch: ``True == 1`` and ``1 == 1.0`` are both true in Python, so
    a builder that turned a boolean filter into an integer one, or an integer
    into a float Qdrant would then refuse, would slip past a plain equality
    check. The value a builder emits must be the value the caller sent, not
    something that merely compares equal to it."""
    assert type(got) is type(expected), (
        f"{where}: built a {type(got).__name__} ({got!r}) from a "
        f"{type(expected).__name__} ({expected!r}) — the value was coerced"
    )
    if isinstance(expected, list):
        assert len(got) == len(expected), f"{where}: {got!r} != {expected!r}"
        for i, (g, e) in enumerate(zip(got, expected, strict=True)):
            _assert_same_value(f"{where}[{i}]", g, e)
        return
    assert got == expected, f"{where}: built {got!r}, expected {expected!r}"


# --------------------------------------------------------------------------- #
# The contract: all four agree, row by row
# --------------------------------------------------------------------------- #

#: ``(name, callable)`` — each takes the filters dict and either returns
#: (accepts) or raises. Two builders, two predicates; all four are the code an
#: end-user filter actually reaches.
INTERPRETERS = [
    ("qdrant/_build_filter", lambda f: _build_filter(f)),
    ("elasticsearch/_build_query", lambda f: _build_query("a query", f)),
    ("memory/_matches", lambda f: _matches(CHUNK, f)),
    ("filters/payload_matches", lambda f: payload_matches(CHUNK.metadata, f)),
    # The boundary validator is held to the same table, so the router's 400 and
    # the stores' defence can never be answering different questions.
    ("filters/validate_filter_values", validate_filter_values),
]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_all_four_interpreters_agree(case: Case) -> None:
    """Either every interpreter accepts the value, or every one raises the same
    typed ``InvalidFilterValue``. No interpreter may 500 with a foreign error
    type, and none may silently answer "no match" for a value it cannot
    represent — that is indistinguishable from an honest miss."""
    filters = _filters(case)
    outcomes: dict[str, str] = {}
    errors: dict[str, InvalidFilterValue] = {}
    for name, run in INTERPRETERS:
        try:
            run(filters)
        except InvalidFilterValue as e:  # the ONE error type all four may raise
            outcomes[name] = "refused"
            errors[name] = e
        except Exception as e:  # noqa: BLE001 - any other type is the bug
            pytest.fail(
                f"{name} raised {type(e).__module__}.{type(e).__name__} for "
                f"{filters!r}; the contract is InvalidFilterValue (a bare "
                f"pydantic/ES error is what became a 500 in #471): {e}"
            )
        else:
            outcomes[name] = "accepted"

    expected = "accepted" if case.accepted else "refused"
    assert set(outcomes.values()) == {expected}, (
        f"the interpreters disagree on {filters!r}: {outcomes} "
        f"(expected all {expected!r})"
    )
    for name, err in errors.items():
        assert err.key == _key(case), f"{name} blamed {err.key!r}, expected {_key(case)!r}"
        for fragment in case.message_contains:
            assert fragment in str(err), f"{name}: {fragment!r} missing from {err}"


@pytest.mark.parametrize("case", [c for c in CASES if not c.accepted], ids=[
    c.name for c in CASES if not c.accepted
])
def test_every_refusal_teaches_the_whole_grammar(case: Case) -> None:
    """A caller must be able to fix the request from any single refusal — so
    every message names the accepted grammar AND says range operators are not
    supported, rather than only naming what was wrong."""
    with pytest.raises(InvalidFilterValue) as exc:
        validate_filter_values(_filters(case))
    msg = str(exc.value)
    assert "a string, an integer or a boolean" in msg
    assert "a list of strings or a list of integers" in msg
    assert "booleans only as scalars" in msg
    assert "range operators" in msg


# --------------------------------------------------------------------------- #
# Accepted rows: the two predicates must return the SAME answer, and the two
# builders must emit the expected clause shape. "All four agree" is not enough
# on its own — four interpreters that accept a value and then match different
# records is the same bug one layer down.
# --------------------------------------------------------------------------- #

ACCEPTED = [c for c in CASES if c.accepted]
ACCEPTED_IDS = [c.name for c in ACCEPTED]


@pytest.mark.parametrize("case", ACCEPTED, ids=ACCEPTED_IDS)
def test_accepted_values_match_identically(case: Case) -> None:
    """``_matches`` and ``payload_matches`` give the same verdict on the same
    record — including the empty list, which matches NOTHING (#196)."""
    filters = _filters(case)
    memory = _matches(CHUNK, filters)
    payload = payload_matches(CHUNK.metadata, filters)
    assert memory == payload == case.matches, (
        f"{filters!r}: _matches={memory}, payload_matches={payload}, "
        f"expected {case.matches}"
    )


@pytest.mark.parametrize("case", ACCEPTED, ids=ACCEPTED_IDS)
def test_accepted_values_build_the_expected_clauses(case: Case) -> None:
    """A list value becomes membership (Qdrant ``MatchAny`` / ES ``terms``), a
    scalar becomes equality (``MatchValue`` / ``term``) — on BOTH builders, for
    the same row. An empty list is a real ``terms``/``MatchAny`` clause, not a
    dropped constraint.

    Both the clause KIND and the VALUE INSIDE IT are asserted, for EVERY key.
    Kind alone is not enough: a builder that emits the right clause carrying a
    coerced value — ``{"term": {field: str(value)}}``, which is exactly the
    Elasticsearch query-time laxness this whole change exists to stop — passes
    a kind-only check while silently reintroducing the defect one layer down.
    Values are compared with :func:`_assert_same_value`, which rejects a type
    change even when ``==`` would not (``True == 1`` in Python)."""
    filters = _filters(case)

    built = _build_filter(filters)
    assert built is not None
    assert built.must is not None
    conditions = {c.key: c for c in built.must}  # type: ignore[union-attr]
    assert set(conditions) == set(filters), "every key must contribute a condition"

    body = _build_query("a query", filters)
    clauses = body["bool"]["filter"]

    # Every key, not just the case's own: the tenant key rides along on each
    # row and is the one whose value a coercion bug would leak across.
    for key, value in filters.items():
        is_list = isinstance(value, (list, tuple, set))

        match = conditions[key].match  # type: ignore[union-attr]
        kind = "any" if type(match).__name__ == "MatchAny" else "value"
        if key == _key(case):
            assert kind == case.qdrant_match, (
                f"Qdrant built {type(match).__name__} for {value!r}"
            )
        else:
            assert kind == ("any" if is_list else "value")
        # MatchAny carries `any`, MatchValue carries `value`.
        got = match.any if kind == "any" else match.value  # type: ignore[union-attr]
        _assert_same_value(f"qdrant {kind} for {key!r}", got, list(value) if is_list else value)

        # ES prefixes every caller-facing bare key as `metadata.<key>`.
        field = f"metadata.{key}"
        found = [c for c in clauses if field in next(iter(c.values()))]
        assert len(found) == 1, f"expected exactly one clause for {key!r}, got {clauses!r}"
        es_kind = next(iter(found[0]))
        if key == _key(case):
            assert es_kind == case.es_clause
        else:
            assert es_kind == ("terms" if is_list else "term")
        _assert_same_value(
            f"es {es_kind} for {key!r}",
            found[0][es_kind][field],
            list(value) if is_list else value,
        )


def test_year_is_the_declared_integer_field() -> None:
    """The type-mismatch rows above are only meaningful while ``year`` is in
    the table — pin it, so removing it fails here and not just diffusely."""
    assert "year" in KNOWN_INT_FIELDS


def test_unconstrained_filters_are_still_unconstrained() -> None:
    """``None`` and ``{}`` mean "no filter" and must not be dragged into the
    grammar — only a key that is PRESENT with an empty list is a constraint."""
    validate_filter_values(None)
    validate_filter_values({})
    assert _build_filter(None) is None
    assert _build_filter({}) is None
    assert _matches(CHUNK, {}) is True
    assert payload_matches(CHUNK.metadata, {}) is True


@pytest.mark.parametrize("container", [tuple, set])
def test_tuples_and_sets_are_lists_for_this_grammar(container: Any) -> None:
    """The three interpreters all branch on ``(list, tuple, set)``, so the
    validator must too — otherwise an internal caller passing a set gets a
    refusal the store would have honoured."""
    validate_filter_values({"doc_type": container(["article"])})
    with pytest.raises(InvalidFilterValue):
        validate_filter_values({"year": container(["2025"])})


def test_the_grammar_is_qdrants_measured_bound_not_a_guess() -> None:
    """Every refusal above is justified only while Qdrant genuinely cannot
    carry the value, so assert that against ``MatchValue``/``MatchAny``
    directly. A future qdrant-client that widens its own grammar then shows up
    here as a failing test — a prompt to relax ours — instead of as silent
    over-strictness nobody re-measures.

    This is also the assertion that would have caught the two rows above at
    design time: ``MatchValue`` takes a ``bool``, ``MatchAny`` does not, and
    ``MatchAny`` is ``list[str] | list[int]`` rather than a list of scalars."""
    from pydantic import ValidationError
    from qdrant_client.models import MatchAny, MatchValue

    for ok in ("article", 2025, True):
        MatchValue(value=ok)
    for bad in (1.5, None, {"gte": 2025}):
        with pytest.raises(ValidationError):
            MatchValue(value=bad)

    MatchAny(any=[])  # #196: representable, and matches nothing
    MatchAny(any=["a", "b"])
    MatchAny(any=[2025, 2026])
    for bad_list in ([True], [2025, "a"], [1.5], [None]):
        with pytest.raises(ValidationError):
            MatchAny(any=bad_list)
