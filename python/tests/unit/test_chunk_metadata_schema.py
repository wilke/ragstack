"""``contracts/schemas/chunk_metadata.json`` and its Python mirror (#603).

The contract file is the source of truth; ``ragstack.metadata_schema`` is what
runtime code reads, because ``contracts/`` is not packaged into the wheel and the
module's stdlib-only promise is what lets ``ingestion/jats.py`` import it inside a
CPU-only CWL worker once per document across a 1.44M-document corpus. A mirror
that is not pinned is a mirror that drifts, and the repo already has two
precedents for pinning one (``test_error_schema.py``'s Error diff and
``test_grading_fixtures.py``'s two-sources discipline). This is the third.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from ragstack import metadata_schema as ms

_CONTRACT = (
    Path(__file__).resolve().parents[3] / "contracts" / "schemas" / "chunk_metadata.json"
)


@pytest.fixture(scope="module")
def contract() -> dict:
    return json.loads(_CONTRACT.read_text())


# --------------------------------------------------------------------------- #
# The mirror
# --------------------------------------------------------------------------- #


def test_the_contract_is_a_valid_2020_12_schema(contract):
    jsonschema.Draft202012Validator.check_schema(contract)
    assert contract["$id"] == _CONTRACT.name


def test_python_declares_exactly_the_contract_fields(contract):
    assert set(ms.DECLARED_FIELDS) == set(contract["properties"])


def test_python_agrees_with_the_contract_on_every_type(contract):
    """Field for field: scalar kind, array-ness, and the derived ES mapping."""
    for name, spec in contract["properties"].items():
        declared = ms.DECLARED_FIELDS[name]
        types = spec["type"] if isinstance(spec["type"], list) else [spec["type"]]
        concrete = [t for t in types if t != "null"]
        assert len(concrete) == 1, f"{name}: one concrete type per field"
        if concrete[0] == "array":
            assert declared.array, name
            assert declared.kind == spec["items"]["type"], name
        else:
            assert not declared.array, name
            assert declared.kind == concrete[0], name
        assert declared.elasticsearch_mapping == spec["x-ragstack-elasticsearch"], name


def test_required_matches_and_is_only_the_isolation_boundary(contract):
    assert set(contract["required"]) == ms.REQUIRED_FIELDS == {"tenant_id"}


def test_every_optional_field_admits_null(contract):
    """A JSON null means what absence means — ES does not index one, and
    ``chunkers.link_neighbors`` writes one into prev/next_chunk_id on purpose."""
    for name, spec in contract["properties"].items():
        if name in ms.REQUIRED_FIELDS:
            continue
        assert "null" in spec["type"], f"{name} must admit null"


def test_the_namespace_stays_open(contract):
    """The one place this directory departs from its closed-schema convention.
    Closing it would refuse, in bulk and at ingest, every corpus carrying a field
    the table has not caught up with."""
    assert contract["additionalProperties"] is True


def test_the_store_shape_difference_is_stated_in_the_contract(contract):
    shape = contract["x-ragstack-store-shape"]
    assert shape["elasticsearch"]["container"] == ms.ES_METADATA_CONTAINER
    assert shape["qdrant"]["container"] is None
    assert ms.es_field_path("pmid") == "metadata.pmid"
    assert ms.qdrant_field_path("pmid") == "pmid"


# --------------------------------------------------------------------------- #
# The derived Elasticsearch mapping
# --------------------------------------------------------------------------- #


def test_the_es_mapping_types_pmid_as_keyword_not_long():
    """The incident this issue exists for. The ID Converter returns a JSON int;
    a repair tool wrote it through; ES inferred ``long`` from that first write;
    a mapping cannot be changed in place."""
    props = ms.elasticsearch_metadata_properties()
    assert props["pmid"]["type"] == "keyword"
    assert props["pmcid"]["type"] == "keyword"
    assert props["year"]["type"] == "long"
    assert props["is_boilerplate"]["type"] == "boolean"


def test_every_keyword_field_is_bounded():
    """An unbounded keyword takes a >32 KB value as one Lucene term and aborts
    the whole bulk request — a paper's reference list mis-extracted into
    ``metadata.title`` was seen at ~38 KB in production."""
    for name, mapping in ms.elasticsearch_metadata_properties().items():
        if mapping["type"] == "keyword":
            assert mapping["ignore_above"] == ms.KEYWORD_IGNORE_ABOVE, name
    assert ms.KEYWORD_IGNORE_ABOVE * 4 <= 32766


def test_the_es_store_creates_new_indices_with_the_derived_mapping():
    from ragstack.stores.elasticsearch import _MAPPINGS

    assert (
        _MAPPINGS["properties"]["metadata"]["properties"]
        == ms.elasticsearch_metadata_properties()
    )


def test_the_dynamic_template_still_covers_undeclared_strings():
    """Deriving explicit properties must not close the namespace: an undeclared
    string field still needs the bounded keyword template."""
    from ragstack.stores.elasticsearch import _MAPPINGS

    tmpl = _MAPPINGS["dynamic_templates"][0]["metadata_strings_as_keyword"]
    assert tmpl["path_match"] == "metadata.*"
    assert tmpl["mapping"]["ignore_above"] == ms.KEYWORD_IGNORE_ABOVE


# --------------------------------------------------------------------------- #
# The filter-side table is deliberately narrower
# --------------------------------------------------------------------------- #


def test_the_filter_int_table_is_a_subset_of_the_declared_int_fields():
    """``KNOWN_INT_FIELDS`` says which keys a CALLER's filter VALUE is type-checked
    against; ``INT_FIELDS`` says what a PRODUCER must write. Widening the former is
    a behaviour change — ``{"chunk_index": "3"}`` is a 200-with-zero-hits today and
    would become a 400 — so it needs its own contract note and conformance case,
    and #603 deliberately does not make it."""
    assert ms.KNOWN_INT_FIELDS <= ms.INT_FIELDS
    assert ms.KNOWN_INT_FIELDS == {"year"}


# --------------------------------------------------------------------------- #
# The ingest-boundary check
# --------------------------------------------------------------------------- #


def _ok() -> dict:
    return {"tenant_id": "public", "pmid": "31234567", "year": 2019, "authors": ["A B"]}


def test_a_conforming_payload_has_no_problems():
    assert ms.metadata_problems(_ok()) == []


def test_an_int_pmid_is_refused():
    (problem,) = ms.metadata_problems({"tenant_id": "t", "pmid": 31234567})
    assert "pmid" in problem and "int" in problem


def test_a_string_year_is_refused():
    """The measured live defect: the ``lucid`` collection stores ``year`` as a
    string, so ``{"year": 2021}`` matched 129,248 chunks on the ES leg (which
    coerces at query time) and 0 on the Qdrant leg (which compares types)."""
    (problem,) = ms.metadata_problems({"tenant_id": "t", "year": "2021"})
    assert "year" in problem and "integer" in problem


def test_a_bool_is_not_an_integer():
    (problem,) = ms.metadata_problems({"tenant_id": "t", "year": True})
    assert "year" in problem


def test_a_joined_author_string_is_refused():
    """jats.py emits a ``; ``-joined string at the extract stage and
    ``enrich.parse_authors`` is what splits it — a corpus that skips enrichment
    would otherwise build an ``authors`` field meaning something different from
    its peers'."""
    (problem,) = ms.metadata_problems({"tenant_id": "t", "authors": "A B; C D"})
    assert "authors" in problem and "list" in problem


def test_a_missing_tenant_is_refused():
    (problem,) = ms.metadata_problems({"pmid": "1"})
    assert "tenant_id" in problem


def test_null_is_accepted_wherever_a_value_is():
    assert ms.metadata_problems({"tenant_id": "t", "prev_chunk_id": None}) == []
    assert ms.metadata_problems({"tenant_id": "t", "is_boilerplate": None}) == []


def test_absent_is_not_coerced_to_false():
    """``is_boilerplate`` is stamped only on a non-body chunk, so
    ``{"is_boilerplate": false}`` matches nothing and /v1/query expresses
    "exclude boilerplate" as a negation of true (#601). Nothing here may invent
    the false: that would reclassify every chunk ingested before #597 from
    'never classified' to 'asserted not boilerplate'."""
    md = {"tenant_id": "t"}
    assert ms.metadata_problems(md) == []
    assert "is_boilerplate" not in md  # the check does not mutate


def test_undeclared_keys_pass_through():
    assert ms.metadata_problems({"tenant_id": "t", "grant": "NIH-123", "n": 7}) == []


def test_every_problem_is_reported_not_just_the_first():
    problems = ms.metadata_problems({"tenant_id": "t", "pmid": 1, "year": "x"})
    assert len(problems) == 2


def test_validate_raises_with_the_contract_path_in_the_message():
    with pytest.raises(ms.ChunkMetadataTypeError) as e:
        ms.validate_chunk_metadata({"tenant_id": "t", "pmid": 1}, where="unit")
    assert "contracts/schemas/chunk_metadata.json" in str(e.value)
    assert "unit" in str(e.value)


def test_validate_chunks_names_the_offending_chunk():
    from ragstack.models import Chunk

    good = Chunk(id="a", doc_id="d", content="x", metadata=dict(_ok()))
    bad = Chunk(id="b", doc_id="d", content="x", metadata={"tenant_id": "t", "year": "no"})
    ms.validate_chunks([good])
    with pytest.raises(ms.ChunkMetadataTypeError) as e:
        ms.validate_chunks([good, bad])
    assert "'b'" in str(e.value)


def test_a_sample_payload_validates_against_the_contract_itself(contract):
    """The contract is a real JSON Schema, not only an annotation carrier: a
    payload the Python check accepts must also validate against it."""
    jsonschema.validate(_ok(), contract)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"tenant_id": "t", "pmid": 31234567}, contract)
