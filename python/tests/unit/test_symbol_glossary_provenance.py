"""#754 provenance (ADR-0011): the glossary step is recorded in a hash-EXCLUDED
``enrichment`` field — never in ``chunk_params``, which names collections."""
from __future__ import annotations

import json

from ragstack import metadata_schema as ms
from ragstack.ingestion.receipts import ShardReceipt
from ragstack.ingestion.symbol_glossary import VERSION_KEY, attacher_from_mode
from ragstack.provenance import chunk_descriptor, make_ingest_manifest, spec_hash

ENRICH = attacher_from_mode("rules").provenance()  # type: ignore[union-attr]
BASE: dict = {"collection": "c", "model": "m", "dim": 8, "chunk_method": "fixed_token",
              "chunk_size": 512, "chunk_overlap": 64}


def test_enrichment_recorded_and_does_not_change_spec_hash() -> None:
    plain = make_ingest_manifest(**BASE)
    enriched = make_ingest_manifest(**BASE, enrichment=ENRICH)
    assert enriched.enrichment["symbol_glossary"]["extractor"] == "rules/2"
    assert enriched.spec_hash == plain.spec_hash == spec_hash(
        "m", 8, chunk_descriptor("fixed_token", 512, 64, None))
    assert enriched.chunk_params == {} == plain.chunk_params


def test_manifest_without_enrichment_serialises_as_before() -> None:
    plain = make_ingest_manifest(**BASE)
    assert "enrichment" not in plain.model_dump()
    assert "enrichment" not in json.loads(plain.model_dump_json())
    enriched = make_ingest_manifest(**BASE, enrichment=ENRICH)
    assert json.loads(enriched.model_dump_json())["enrichment"] == ENRICH


def test_receipt_enrichment_roundtrip_and_omitted_when_none() -> None:
    r = ShardReceipt("s", "t", "completed")
    assert "enrichment" not in json.loads(r.to_json())
    r.enrichment = ENRICH
    back = ShardReceipt.from_dict(json.loads(r.to_json()))
    assert back.enrichment == ENRICH


def test_version_key_is_declared_keyword() -> None:
    assert ms.metadata_problems({"tenant_id": "t", VERSION_KEY: "rules/2"}) == []
    assert ms.metadata_problems({"tenant_id": "t", VERSION_KEY: 3}) != []
    props = ms.elasticsearch_metadata_properties()
    assert props[VERSION_KEY]["type"] == "keyword"
