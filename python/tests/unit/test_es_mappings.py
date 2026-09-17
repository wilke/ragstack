"""ES keyword mapping guard (cherry-pick from the closed #144 branch).

A keyword indexes the whole value as ONE Lucene term. A term over ~32 KB raises
document_parsing_exception, ``index()`` re-raises on the first item error (not a
transient), and the bulk run dies with the checkpoint stalled. Real corpora carry
poison rows — a paper's reference list mis-extracted into ``metadata.title``,
seen at ~38 KB in production.
"""
from __future__ import annotations

import pytest
from elasticsearch import ApiError

from ragstack.stores.elasticsearch import (
    _MAPPINGS,
    _METADATA_KEYWORD_IGNORE_ABOVE,
    ElasticsearchTextIndex,
)


def test_metadata_keyword_template_bounds_the_term_length():
    tmpl = _MAPPINGS["dynamic_templates"][0]["metadata_strings_as_keyword"]
    assert tmpl["mapping"]["type"] == "keyword"
    assert tmpl["mapping"]["ignore_above"] == _METADATA_KEYWORD_IGNORE_ABOVE


def test_the_bound_stays_under_lucene_limit_for_4_byte_utf8():
    # Lucene's hard limit is 32766 BYTES; ignore_above counts CHARACTERS, so the
    # bound must survive the worst case of 4 bytes per character.
    assert _METADATA_KEYWORD_IGNORE_ABOVE * 4 <= 32766


class _AlreadyExists(ApiError):
    """Stands in for the real create-race error. ensure_index catches ApiError and
    matches on str(e), and the real ApiError needs a meta object with .status just
    to stringify — so subclass it and supply only what the code under test reads."""

    def __init__(self) -> None:  # noqa: D107 — deliberately skips ApiError.__init__
        pass

    def __str__(self) -> str:
        return "resource_already_exists_exception"


class _MapperConflict(ApiError):
    """A mapping conflict, as ES answers one: `metadata.year` is already a
    keyword and the declared mapping says long. Subclassed like _AlreadyExists so
    it stringifies without a transport meta object."""

    def __init__(self) -> None:  # noqa: D107 — deliberately skips ApiError.__init__
        pass

    def __str__(self) -> str:
        return "illegal_argument_exception: mapper [metadata.year] cannot be changed"


class _FakeIndices:
    def __init__(self, exists: bool):
        self._exists = exists
        self.created = False
        self.put_mapping_calls: list[dict] = []

    async def create(self, index, mappings):  # noqa: ARG002
        if self._exists:
            raise _AlreadyExists()
        self.created = True

    async def put_mapping(self, index, body):  # noqa: ARG002
        self.put_mapping_calls.append(body)


class _FakeES:
    def __init__(self, exists: bool):
        self.indices = _FakeIndices(exists)


def _store(exists: bool) -> tuple[ElasticsearchTextIndex, _FakeES]:
    idx = ElasticsearchTextIndex.__new__(ElasticsearchTextIndex)
    es = _FakeES(exists)
    idx._es = es  # type: ignore[attr-defined]
    idx._index = "t"  # type: ignore[attr-defined]
    return idx, es


@pytest.mark.asyncio
async def test_a_new_index_is_created_with_the_bounded_template():
    idx, es = _store(exists=False)
    await idx.ensure_index()
    assert es.indices.created
    assert es.indices.put_mapping_calls == []  # nothing to migrate


@pytest.mark.asyncio
async def test_an_existing_index_gets_the_template_pushed_to_it():
    """The branch this came from stopped at `create`, so an existing index never
    received the template at all and even NEW metadata fields stayed unbounded.

    Scope note: this covers newly-encountered fields only. Fields already mapped
    as bare keyword keep that mapping — see the comment in ensure_index."""
    idx, es = _store(exists=True)
    await idx.ensure_index()
    assert not es.indices.created
    assert len(es.indices.put_mapping_calls) == 1
    tmpl = es.indices.put_mapping_calls[0]["dynamic_templates"][0]
    assert tmpl["metadata_strings_as_keyword"]["mapping"]["ignore_above"] == (
        _METADATA_KEYWORD_IGNORE_ABOVE
    )


@pytest.mark.asyncio
async def test_a_transport_error_during_the_mapping_update_does_not_escape():
    """elasticsearch.ConnectionError is a TransportError, NOT an ApiError. A
    narrow `except ApiError` would let a connection blip escape ensure_index()
    where it previously returned cleanly — aborting startup under
    require_durable_backends."""
    idx, es = _store(exists=True)

    async def boom(index, body):  # noqa: ARG001
        raise ConnectionError("connection refused")

    es.indices.put_mapping = boom  # type: ignore[assignment]
    await idx.ensure_index()  # must not raise


# --------------------------------------------------------------------------- #
# Derived metadata properties, and the two-attempt update path (#603)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_new_index_is_created_with_the_declared_metadata_types():
    """The point of #603: a new index's declared fields are typed by decision,
    not inferred from whichever document arrives first — and an ES mapping cannot
    be changed in place, so there is no second chance."""
    from ragstack.metadata_schema import elasticsearch_metadata_properties

    idx, es = _store(exists=False)
    captured: dict = {}

    async def create(index, mappings):  # noqa: ARG001
        captured.update(mappings)

    es.indices.create = create  # type: ignore[assignment]
    await idx.ensure_index()
    assert (
        captured["properties"]["metadata"]["properties"]
        == elasticsearch_metadata_properties()
    )


@pytest.mark.asyncio
async def test_an_existing_index_that_rejects_the_declared_mapping_keeps_its_template():
    """put_mapping is all-or-nothing, and a live 1.55M-chunk index maps
    ``metadata.year`` as a keyword where the schema declares a long (measured
    2026-09-17). That index must still receive the bounded dynamic template —
    losing it would leave every NEWLY-seen string field mapped as an unbounded
    keyword, where one >32 KB value aborts the whole bulk request."""
    idx, es = _store(exists=True)
    calls: list[dict] = []

    async def put_mapping(index, body):  # noqa: ARG001
        calls.append(body)
        if "properties" in body:
            raise _MapperConflict()

    es.indices.put_mapping = put_mapping  # type: ignore[assignment]
    await idx.ensure_index()  # must not raise

    assert len(calls) == 2
    assert "properties" in calls[0]  # the full derived mapping, attempted first
    assert set(calls[1]) == {"dynamic_templates"}  # the safe fallback
    tmpl = calls[1]["dynamic_templates"][0]["metadata_strings_as_keyword"]
    assert tmpl["mapping"]["ignore_above"] == _METADATA_KEYWORD_IGNORE_ABOVE


@pytest.mark.asyncio
async def test_a_transport_error_does_not_trigger_the_fallback_attempt():
    """A connection blip says nothing about the mapping, so a second round trip
    with a smaller body would only fail again."""
    idx, es = _store(exists=True)
    calls: list[dict] = []

    async def put_mapping(index, body):  # noqa: ARG001
        calls.append(body)
        raise ConnectionError("connection refused")

    es.indices.put_mapping = put_mapping  # type: ignore[assignment]
    await idx.ensure_index()  # must not raise
    assert len(calls) == 1
