"""Tests for the bulk-ingest registry seam (#263).

The property under test is narrow and absolute: **a bulk writer cannot create a
physical store the registry has never seen.** Everything else here — the build
check, the manifest, the routed-instance rule — exists because a resolution that
succeeded but pointed somewhere else would be worse than a refusal.
"""
from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import pytest

from ragstack.collection_store import CollectionSpec
from ragstack.ops import ingest_target as it


def _settings(**over):
    base = {
        "qdrant_url": "http://localhost:6333",
        "qdrant_collection_routes": {},
        "collection_store_backend": "json",
        "collection_store_path": "",
        "collections_file": "",
        "collections_json": "",
    }
    base.update(over)
    return SimpleNamespace(**base)


def _spec(cid="corpus", collection="store_a", **over):
    body = {
        "id": cid, "collection": collection, "embedding_model": "m",
        "embedding_model_dim": 4, "chunk_method": "fixed_token",
        "chunk_size": 256, "chunk_overlap": 32,
    }
    body.update(over)
    return CollectionSpec(**body)


def _args(**over):
    base = {"collection_id": "", "collection": "", "qdrant_url": "",
            "create_via_api": "", "api_key": "", "api_bearer": ""}
    base.update(over)
    return argparse.Namespace(**base)


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------


def test_resolve_returns_the_entrys_physical_names():
    spec = _spec(collection="ragstack_sfr_tok256", text_index="ragstack_sfr")
    t = it.resolve("corpus", settings=_settings(), specs=[spec])
    assert t.collection == "ragstack_sfr_tok256"
    assert t.es_index == "ragstack_sfr"  # legs may differ; both come from the entry
    assert t.qdrant_url == "http://localhost:6333"


def test_resolve_refuses_an_unknown_id_and_says_how_to_fix_it():
    with pytest.raises(it.TargetError) as e:
        it.resolve("nope", settings=_settings(collections_file="/x/c.json"),
                   specs=[_spec("corpus")])
    msg = str(e.value)
    assert "nope" in msg
    assert "corpus" in msg           # the ids that DO exist
    assert "/x/c.json" in msg        # which registry was consulted
    assert "POST /v1/collections" in msg or "--create-via-api" in msg


def test_resolve_names_an_empty_registry_as_empty():
    with pytest.raises(it.TargetError) as e:
        it.resolve("x", settings=_settings(), specs=[])
    assert "registry is empty" in str(e.value)


def test_routed_collection_wins_over_the_command_line_url():
    """Writing a routed collection to the default instance builds a second,
    invisible copy of a store that already exists elsewhere."""
    s = _settings(qdrant_collection_routes={"semantic": "http://localhost:6343"})
    t = it.resolve("c", settings=s, specs=[_spec("c", "semantic")],
                   qdrant_url="http://elsewhere:6333")
    assert t.qdrant_url == "http://localhost:6343"


def test_explicit_url_wins_for_an_unrouted_collection():
    t = it.resolve("c", settings=_settings(), specs=[_spec("c")],
                   qdrant_url="http://other:6333")
    assert t.qdrant_url == "http://other:6333"


# --------------------------------------------------------------------------
# the migration path: --collection names a PHYSICAL store
# --------------------------------------------------------------------------


def test_physical_name_claimed_by_one_entry_resolves():
    t = it.resolve_by_store_name("store_a", settings=_settings(), specs=[_spec("c")])
    assert t.collection_id == "c"


def test_physical_name_claimed_by_nobody_is_refused():
    with pytest.raises(it.TargetError) as e:
        it.resolve_by_store_name("brand_new", settings=_settings(), specs=[_spec("c")])
    msg = str(e.value)
    assert "brand_new" in msg
    assert "--collection-id" in msg
    # name the consequence, not just the rule
    assert "manifest" in msg


def test_physical_name_claimed_twice_is_refused_not_guessed():
    """ADR-0002 decision 5 broken the other way — do not guess whose ACLs govern."""
    specs = [_spec("a", "shared"), _spec("b", "shared")]
    with pytest.raises(it.TargetError) as e:
        it.resolve_by_store_name("shared", settings=_settings(), specs=specs)
    assert "2 registry entries" in str(e.value)


# --------------------------------------------------------------------------
# build-spec check — ADR-0002's guard, where the bulk path actually is
# --------------------------------------------------------------------------


def test_check_build_accepts_a_matching_spec():
    t = it.target_from_spec(_spec(), _settings())
    t.check_build(model="m", dim=4, chunk_method="fixed_token",
                  chunk_size=256, chunk_overlap=32)


@pytest.mark.parametrize("bad", [
    {"model": "other"},
    {"dim": 768},
    {"chunk_method": "sentence", "chunk_size": 256, "chunk_overlap": 32},
    {"chunk_method": "fixed_token", "chunk_size": 512, "chunk_overlap": 64},
])
def test_check_build_refuses_a_differing_spec(bad):
    t = it.target_from_spec(_spec(), _settings())
    with pytest.raises(it.TargetError) as e:
        t.check_build(**bad)
    assert "ADR-0002" in str(e.value)


def test_check_build_tolerates_what_the_caller_did_not_say():
    """A script that ingests pre-chunked JSON never learns the chunker. It must
    be prevented from contradicting the entry, not blocked by silence."""
    t = it.target_from_spec(_spec(), _settings())
    t.check_build(dim=4)  # no model, no chunk fields
    t.check_build(model=None, chunk_method=None, chunk_size=None, chunk_overlap=None)


# --------------------------------------------------------------------------
# manifest — the thing that arms the guard for every LATER ingest
# --------------------------------------------------------------------------


def test_manifest_is_written_from_the_entry_not_the_command_line(tmp_path):
    from ragstack.provenance import read_manifest

    spec = _spec(collection="phys", chunk_method="fixed_token", chunk_size=256,
                 chunk_overlap=32)
    t = it.target_from_spec(spec, _settings())
    t.write_manifest(str(tmp_path), corpus="corpus.jsonl", chunk_count=7)

    m = read_manifest(str(tmp_path), "phys")
    assert m is not None
    assert (m.model, m.dim) == ("m", 4)
    assert (m.chunk_method, m.chunk_size, m.chunk_overlap) == ("fixed_token", 256, 32)
    assert m.chunk_count == 7
    assert m.spec_hash == spec.spec_hash()  # the value a later ingest is compared to


def test_manifest_arms_the_api_guard(tmp_path, monkeypatch):
    """The whole point of #263: with a manifest present, check_ingest_build_spec
    no longer early-returns, so a later API ingest with a different chunker is
    refused instead of silently interleaving."""
    from ragstack.api import deps

    spec = _spec(collection="phys")
    it.target_from_spec(spec, _settings()).write_manifest(str(tmp_path))

    monkeypatch.setattr(deps.settings, "collection_manifest_dir", str(tmp_path))
    monkeypatch.setattr(deps.settings, "collection_spec_guard", True)
    entry = SimpleNamespace(id="c", collection="phys", model="m", dim=4,
                            chunk_method="sentence", chunk_size=512,
                            chunk_overlap=64, chunk_params={})
    with pytest.raises(Exception) as e:
        deps.check_ingest_build_spec(entry)
    assert "chunk" in str(e.value).lower()

    # and the same guard passes for an ingest that matches the entry
    deps.check_ingest_build_spec(
        SimpleNamespace(id="c", collection="phys", model="m", dim=4,
                        chunk_method="fixed_token", chunk_size=256,
                        chunk_overlap=32, chunk_params={})
    )


def test_manifest_is_skipped_without_a_directory():
    t = it.target_from_spec(_spec(), _settings())
    assert t.write_manifest("") == ""


# --------------------------------------------------------------------------
# argparse wiring
# --------------------------------------------------------------------------


def test_no_target_at_all_is_refused():
    with pytest.raises(it.TargetError) as e:
        it.resolve_from_args(_args(), settings=_settings())
    assert "--collection-id is required" in str(e.value)


def test_physical_flag_alone_still_works_when_registered(tmp_path):
    """An existing invocation that targets a registered store keeps working —
    a flag day would strand running pipelines."""
    reg = tmp_path / "c.json"
    reg.write_text(json.dumps([json.loads(_spec("c", "store_a").model_dump_json())]))
    s = _settings(collections_file=str(reg))
    t = it.resolve_from_args(_args(collection="store_a"), settings=s)
    assert t.collection_id == "c"


def test_physical_flag_contradicting_the_id_is_refused(tmp_path):
    reg = tmp_path / "c.json"
    reg.write_text(json.dumps([json.loads(_spec("c", "store_a").model_dump_json())]))
    s = _settings(collections_file=str(reg))
    with pytest.raises(it.TargetError) as e:
        it.resolve_from_args(_args(collection_id="c", collection="somewhere_else"),
                             settings=s)
    assert "contradicts" in str(e.value)


def test_load_specs_reads_the_configured_registry(tmp_path):
    reg = tmp_path / "c.json"
    reg.write_text(json.dumps([json.loads(_spec("c").model_dump_json())]))
    specs = it.load_specs(_settings(collections_file=str(reg)))
    assert [s.id for s in specs] == ["c"]


def test_resolve_or_exit_prints_the_message_and_exits_2(capsys):
    with pytest.raises(SystemExit) as e:
        it.resolve_or_exit(_args(), settings=_settings())
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "--collection-id is required" in err
    assert "Traceback" not in err


# --------------------------------------------------------------------------
# create-via-api
# --------------------------------------------------------------------------


class _Resp:
    def __init__(self, code, payload=None):
        self.status_code = code
        self._payload = payload or {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


def test_create_via_api_posts_the_id_with_credentials(monkeypatch):
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, body=json, headers=headers)
        return _Resp(201, {"id": "new"})

    monkeypatch.setattr("httpx.post", fake_post)
    it.create_via_api("http://api/", "new", api_key="k", label="L")
    assert seen["url"] == "http://api/v1/collections"
    assert seen["body"] == {"id": "new", "label": "L"}
    assert seen["headers"]["X-API-Key"] == "k"


def test_create_via_api_treats_409_as_success(monkeypatch):
    monkeypatch.setattr("httpx.post", lambda *a, **k: _Resp(409, {"detail": "exists"}))
    it.create_via_api("http://api", "new")  # does not raise


def test_create_via_api_surfaces_a_refusal(monkeypatch):
    monkeypatch.setattr("httpx.post", lambda *a, **k: _Resp(403, {"detail": "nope"}))
    with pytest.raises(it.TargetError) as e:
        it.create_via_api("http://api", "new")
    assert "403" in str(e.value)


def test_create_then_resolve_reads_the_durable_registry(tmp_path, monkeypatch):
    """Re-resolve rather than trust the response: if the API wrote to a different
    registry than this CLI reads, that must fail here, not after a 500k load."""
    reg = tmp_path / "c.json"
    reg.write_text("[]")
    s = _settings(collections_file=str(reg))

    def fake_post(url, json=None, headers=None, timeout=None):
        reg.write_text(json_dumps_spec())
        return _Resp(201, {"id": "new"})

    def json_dumps_spec():
        return json.dumps([json.loads(_spec("new", "phys").model_dump_json())])

    monkeypatch.setattr("httpx.post", fake_post)
    t = it.resolve_from_args(
        _args(collection_id="new", create_via_api="http://api"), settings=s)
    assert (t.collection_id, t.collection) == ("new", "phys")


# --------------------------------------------------------------------------
# the four bulk writers, end to end
# --------------------------------------------------------------------------

_SCRIPTS = [
    ("ingest_chunks.py", ["in.json", "--collection", "brand_new"]),
    ("ingest_jsonl.py", ["in.jsonl", "--collection", "brand_new"]),
    ("ingest_shard.py", ["shard.jsonl", "--collection", "brand_new"]),
    ("load_embeddings.py", ["e.jsonl", "--collection", "brand_new"]),
]


@pytest.mark.parametrize("script,argv", _SCRIPTS, ids=[s for s, _ in _SCRIPTS])
def test_bulk_writer_refuses_an_unregistered_store(script, argv, tmp_path):
    """Every one of these invocations used to mint a physical store the registry
    never saw. They now exit 2 before touching a backend."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    reg = tmp_path / "empty.json"
    reg.write_text("[]")
    env = {
        **os.environ,
        "COLLECTIONS_FILE": str(reg),
        "COLLECTION_STORE_BACKEND": "json",
        "PYTHONPATH": str(root),
        # do not inherit an operator's ambient target
        "RAGSTACK_COLLECTION_ID": "",
    }
    r = subprocess.run(
        [sys.executable, str(root / "scripts" / script), *argv],
        capture_output=True, text=True, timeout=180, cwd=tmp_path, env=env,
    )
    assert r.returncode == 2, r.stderr[-2000:]
    assert "brand_new" in r.stderr
    assert "#263" in r.stderr


def test_create_via_api_that_writes_elsewhere_still_fails(tmp_path, monkeypatch):
    reg = tmp_path / "c.json"
    reg.write_text("[]")
    s = _settings(collections_file=str(reg))
    monkeypatch.setattr("httpx.post", lambda *a, **k: _Resp(201, {"id": "new"}))
    with pytest.raises(it.TargetError) as e:
        it.resolve_from_args(
            _args(collection_id="new", create_via_api="http://api"), settings=s)
    assert "not in the registry" in str(e.value)
