"""#828 PR 1 prerequisite: a list-valued payload field matches a filter by ANY
ELEMENT in the in-memory predicates, as it does in Qdrant and Elasticsearch.

Before this, ``payload_matches`` (the ``get_chunks`` re-check both stores use,
i.e. context expansion) and ``stores/memory._matches`` compared the whole list
(``actual != value``), so ``{"authors": "A"}`` — and every role filter on the
list-valued ``section_roles`` — matched nothing in memory and on the re-check,
while both real store legs matched. Design note §6, "List-valued fields and the
in-memory predicate".
"""
from __future__ import annotations

import pytest

from ragstack.models import Chunk
from ragstack.stores.filters import Not, field_matches, payload_matches
from ragstack.stores.memory import InMemoryTextIndex, InMemoryVectorStore, _matches

META = {"tenant_id": "t", "section_roles": ["materials", "methods"], "doc_type": "article",
        "is_boilerplate": True, "authors": ["Author A", "Author B"]}


@pytest.mark.parametrize("filters,expected", [
    # scalar against a list: any element
    ({"section_roles": "methods"}, True),
    ({"section_roles": "results"}, False),
    ({"authors": "Author B"}, True),
    # list (membership) against a list: any element is a member
    ({"section_roles": ["results", "methods"]}, True),
    ({"section_roles": ["results", "discussion"]}, False),
    ({"section_roles": []}, False),  # #196: the empty list still matches nothing
    # negation against a list: excluded when any element equals the value
    ({"section_roles": Not("methods")}, False),
    ({"section_roles": Not("results")}, True),
    # scalars unchanged
    ({"doc_type": "article"}, True),
    ({"doc_type": ["article", "x"]}, True),
    ({"doc_type": "x"}, False),
    ({"is_boilerplate": Not(True)}, False),
    ({"missing_key": Not("x")}, True),  # an absent key satisfies a negation
    ({"missing_key": "x"}, False),
    # AND across keys
    ({"section_roles": "methods", "tenant_id": "t"}, True),
    ({"section_roles": "methods", "tenant_id": "u"}, False),
])
def test_both_python_predicates_agree(filters, expected) -> None:
    assert payload_matches(META, filters) is expected
    assert _matches(Chunk(id="c", doc_id="d", content="x", metadata=META), filters) is expected


def test_field_matches_on_scalars_is_the_old_reading() -> None:
    assert field_matches("a", "a") and not field_matches("a", "b")
    assert field_matches("a", ["a", "b"]) and not field_matches(None, ["a"])
    assert field_matches(None, Not("a")) and not field_matches("a", Not("a"))


async def test_in_memory_stores_and_get_chunks_match_a_list_field() -> None:
    """The two retrieval legs and the context-expansion re-check agree."""
    tagged = Chunk(id="c1", doc_id="d", content="alpha beta", embedding=[1.0, 0.0],
                   metadata={"tenant_id": "t", "section_roles": ["materials", "methods"]})
    other = Chunk(id="c2", doc_id="d", content="alpha gamma", embedding=[1.0, 0.0],
                  metadata={"tenant_id": "t", "section_roles": ["results"]})
    vs, ti = InMemoryVectorStore(), InMemoryTextIndex()
    await vs.upsert([tagged, other])
    await ti.index([tagged, other])
    f = {"tenant_id": ["t"], "section_roles": "methods"}
    assert [s.chunk.id for s in await vs.search([1.0, 0.0], top_k=5, filters=f)] == ["c1"]
    assert [s.chunk.id for s in await ti.search("alpha", top_k=5, filters=f)] == ["c1"]
    assert [c.id for c in await vs.get_chunks(["c1", "c2"], filters=f)] == ["c1"]
