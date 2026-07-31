"""_build_query: metadata-field mapping + fail-closed tenant scoping.

Pure-function tests — no elasticsearch client required (the client import in
ElasticsearchTextIndex is lazy, so _build_query is importable on its own).
"""
import pytest

from ragstack.stores.elasticsearch import _build_query


def test_filters_target_metadata_fields_and_tenant_list_is_terms():
    q = _build_query("hello", {"tenant_id": ["alice", "public"], "source": "g.pdf"})
    clauses = q["bool"]["filter"]
    assert {"terms": {"metadata.tenant_id": ["alice", "public"]}} in clauses
    assert {"term": {"metadata.source": "g.pdf"}} in clauses
    assert q["bool"]["must"] == [{"match": {"content": "hello"}}]


@pytest.mark.parametrize("filters", [None, {}, {"tenant_id": []}, {"tenant_id": None}, {"source": "x"}])
def test_missing_or_empty_tenant_filter_fails_closed(filters):
    # An unscoped (or empty-scoped) search would leak across tenants — refuse it.
    with pytest.raises(ValueError, match="tenant_id"):
        _build_query("q", filters)


# --- empty multi-value list fails closed on every key (#196) -----------------

def test_empty_list_on_a_second_scope_key_still_constrains():
    # A DB-sourced scope (e.g. visible libraries) coming back empty must narrow
    # to nothing, not silently drop the constraint. An empty ES `terms` array
    # matches no documents, which is exactly the fail-closed reading.
    q = _build_query("hello", {"tenant_id": ["alice", "public"], "library_id": []})
    clauses = q["bool"]["filter"]
    assert {"terms": {"metadata.library_id": []}} in clauses
    assert len(clauses) == 2


def test_absent_key_is_still_unconstrained():
    # Only a key that isn't there means "no constraint" — unchanged behaviour.
    q = _build_query("hello", {"tenant_id": ["alice", "public"]})
    assert q["bool"]["filter"] == [{"terms": {"metadata.tenant_id": ["alice", "public"]}}]
