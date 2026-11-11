"""``h2h_report.py reembed fetch``: the text index stores a chunk under ``<tenant>:<chunk_id>``
and the vector store under ``_point_id(chunk_id, tenant)``; the fetch must look both up by
those ids and key its output by the original chunk id (found live on the first run). Fakes
stand in for Elasticsearch, Qdrant and the embedding endpoint; no network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
if str(_EVAL) not in sys.path:
    sys.path.insert(0, str(_EVAL))

import h2h_report as cli  # noqa: E402
from h2h import gates, reembed_sample  # noqa: E402

from ragstack.stores.qdrant import _point_id  # noqa: E402

IDS = ["8aef073a-1111-4111-8111-111111111111", "0b2c9d4e-2222-4222-8222-222222222222", "f00dbabe-3333-4333-8333-333333333333"]
TENANT = "public"


def fake_post(url: str, body: dict, headers: dict | None):
    if url.endswith("/_mget"):  # Elasticsearch: only the tenant-prefixed ids exist
        docs = []
        for i in body["ids"]:
            tenant, _, cid = i.partition(":")
            found = tenant == TENANT and cid in IDS
            docs.append({"_id": i, "found": found, **({"_source": {"content": f"text of {cid}"}} if found else {})})
        return {"docs": docs}
    if url.endswith("/points"):  # Qdrant: only the derived point ids exist
        valid = {_point_id(c, TENANT): c for c in IDS}
        return {"result": [{"id": i, "vector": [1.0, 0.0, float(IDS.index(valid[i]))]} for i in body["ids"] if i in valid]}
    if url.endswith("/v1/embeddings"):
        return {"model": "fake-model", "data": [{"index": k, "embedding": [1.0, 0.0, float(IDS.index(c))]}
                                               for k, c in enumerate(IDS[IDS.index(IDS[0]):]) if f"text of {c}" in body["input"]]}
    raise AssertionError(url)


def test_fetch_maps_tenant_ids_and_keys_output_by_chunk_id():
    out = reembed_sample.fetch_cosines(IDS, tenant=TENANT, post=fake_post, es_url="http://es", es_index="idx",
                                       qdrant_url="http://qd", qdrant_collection="coll", embed_url="http://emb",
                                       model="m", batch=2)
    assert [o["chunk_id"] for o in out] == IDS  # the original ids, not the prefixed or derived ones
    assert all(o["cosine"] == pytest.approx(1.0) for o in out) and all(o["stored_dim"] == 3 for o in out)
    assert all(o["model"] == "fake-model" for o in out)
    assert gates.check_reembed(out, 0.999, 3, gates.ids_sha256(IDS))[0] == []  # the ids-hash gate sees the sample's ids


def test_fetch_without_the_mapping_fails(monkeypatch):
    """Mutation: bare ids reach the stores -> nothing is found."""
    monkeypatch.setattr(reembed_sample, "es_doc_id", lambda c, t: c)
    with pytest.raises(SystemExit, match="not in the text index"):
        reembed_sample.fetch_cosines(IDS, tenant=TENANT, post=fake_post, es_url="http://es", es_index="idx",
                                     qdrant_url="http://qd", qdrant_collection="coll", embed_url="http://emb", model="m")
    monkeypatch.undo()
    monkeypatch.setattr(reembed_sample, "qdrant_point_id", lambda c, t: c)
    with pytest.raises(SystemExit, match="no stored vector"):
        reembed_sample.fetch_cosines(IDS, tenant=TENANT, post=fake_post, es_url="http://es", es_index="idx",
                                     qdrant_url="http://qd", qdrant_collection="coll", embed_url="http://emb", model="m")
    with pytest.raises(SystemExit, match="not in the text index"):  # the wrong tenant finds nothing either
        reembed_sample.fetch_cosines(IDS, tenant="other", post=fake_post, es_url="http://es", es_index="idx",
                                     qdrant_url="http://qd", qdrant_collection="coll", embed_url="http://emb", model="m")


def test_fetch_cli_uses_tenant_and_writes_cosines(tmp_path, monkeypatch):
    monkeypatch.setattr(reembed_sample, "_post_json", fake_post)
    ids = tmp_path / "ids.json"
    ids.write_text(json.dumps({"chunk_ids": IDS, "ids_sha256": gates.ids_sha256(IDS)}))
    out = tmp_path / "cos.json"
    assert cli.main(["reembed", "fetch", "--ids", str(ids), "--es-url", "http://es", "--es-index", "idx", "--qdrant-url", "http://qd",
                     "--qdrant-collection", "coll", "--embed-url", "http://emb", "--model", "m", "--out", str(out)]) == 0
    assert [o["chunk_id"] for o in json.loads(out.read_text())] == IDS
    assert cli.main(["reembed", "verify", "--cosines", str(out), "--ids", str(ids), "--n", "3"]) == 0
    assert reembed_sample.qdrant_point_id(IDS[0], "public") == _point_id(IDS[0], "public") != IDS[0]
