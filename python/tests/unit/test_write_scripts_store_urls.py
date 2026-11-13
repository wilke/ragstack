"""The remaining write scripts have no default store URLs (#454).

On the deployment host ``localhost:6333`` / ``:9200`` are the PRODUCTION
stores. ``load_embeddings.py`` (#636) and ``ingest_shard.py`` (#470) already
refuse to guess; this pins the same rule on the four scripts #454 still listed:

* ``ingest_jsonl.py`` / ``ingest_chunks.py`` resolve a registry target, so —
  like ``load_embeddings.py`` — per leg the URL is a ROUTE, else the flag, else
  the setting ONLY IF explicitly configured, else exit 2 naming the flag.
* ``backfill_es_from_qdrant.py`` / ``bench_filter_truncation.py`` resolve no
  registry target, so their URL flags are plain ``required=True``.

No test opens a connection: the async ``run`` each registry script would start
is replaced by a spy, so a regression that lets a run through shows up as "the
spy was reached". Every URL that IS supplied is the dead port ``127.0.0.1:1``.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ragstack.collection_store import CollectionSpec
from ragstack.ops import ingest_target as it

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
DEAD = "http://127.0.0.1:1"
LOCAL_Q, LOCAL_ES = "http://localhost:6333", "http://localhost:9200"


def _load(name: str, monkeypatch):
    """Import a script by path, registered (dataclasses need that) only for the
    duration of the test."""
    modname = f"_t454_{name}"
    spec = importlib.util.spec_from_file_location(modname, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    monkeypatch.setitem(sys.modules, modname, mod)
    spec.loader.exec_module(mod)
    return mod


def _spec() -> CollectionSpec:
    return CollectionSpec(id="corpus", collection="store_a", embedding_model="m",
                          embedding_model_dim=4, chunk_method="fixed_token",
                          chunk_size=256, chunk_overlap=32)


def _settings(explicit=(), **over):
    """A settings double; ``explicit`` is pydantic's ``model_fields_set``. The
    code-default URLs are held too, so a fallback to them is visible."""
    base = {
        "qdrant_url": LOCAL_Q, "qdrant_collection_routes": {},
        "elasticsearch_url": LOCAL_ES, "es_collection_routes": {},
        "collection_store_backend": "json", "collection_store_path": "",
        "collections_file": "", "collections_json": "",
        "model_fields_set": set(explicit),
    }
    base.update(over)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------- #
# ingest_jsonl.py / ingest_chunks.py — registry-resolved writers
# --------------------------------------------------------------------------- #

_BUILD = ["--collection-id", "corpus", "--embedding-model", "m",
          "--chunk-method", "fixed_token", "--chunk-size", "256", "--chunk-overlap", "32"]


@pytest.fixture(params=["ingest_jsonl", "ingest_chunks"])
def script(request, monkeypatch, tmp_path):
    """``(name, run)``: ``run(argv, settings=…)`` drives ``main()`` with the
    registry stubbed and a spy in place of the async ``run``; it returns
    ``(exit_code, target_or_None)``."""
    name = request.param
    mod = _load(name, monkeypatch)
    monkeypatch.delenv("RAGSTACK_COLLECTION_ID", raising=False)
    monkeypatch.setattr(it, "load_specs", lambda s=None: [_spec()])
    reached: list = []

    async def spy(args, target=None):
        reached.append(target)

    monkeypatch.setattr(mod, "run", spy)
    inp = tmp_path / "in.json"
    inp.write_text("{}", encoding="utf-8")

    # ingest_jsonl's --es-index has a non-registry default ("ragstack") that
    # the target check refuses; name the entry's own index, as an operator must.
    extra = ["--es-index", _spec().es_index()] if name == "ingest_jsonl" else []

    def _run(argv, *, settings):
        try:
            mod.main([str(inp), *argv, *extra], settings=settings)
            rc = 0
        except SystemExit as e:
            rc = e.code
        return rc, (reached[0] if reached else None)

    return name, _run


def test_bare_run_exits_2_naming_qdrant_url(script, capsys):
    _, run = script
    rc, reached = run(_BUILD, settings=_settings())
    assert rc == 2
    assert reached is None, "the run started with no store URL — it would have hit localhost"
    err = capsys.readouterr().err
    assert "--qdrant-url is required" in err
    assert "PRODUCTION" in err


def test_explicit_qdrant_url_is_used(script):
    _, run = script
    rc, target = run([*_BUILD, "--qdrant-url", DEAD], settings=_settings())
    assert rc == 0
    assert target.qdrant_url == DEAD


def test_env_configured_qdrant_url_is_honoured(script):
    """The second #454 defect: with a non-empty argparse default, the flag
    always won, so an explicitly configured QDRANT_URL was silently ignored."""
    _, run = script
    rc, target = run(_BUILD, settings=_settings(explicit={"qdrant_url"}, qdrant_url=DEAD))
    assert rc == 0
    assert target.qdrant_url == DEAD


def test_a_routed_collection_needs_no_flag(script):
    _, run = script
    rc, target = run(_BUILD, settings=_settings(qdrant_collection_routes={"store_a": DEAD}))
    assert rc == 0 and target.qdrant_url == DEAD


@pytest.mark.parametrize("name", ["ingest_jsonl", "ingest_chunks"])
def test_the_parser_has_no_localhost_default(name):
    """The argparse default WAS the mechanism; pin its absence in the source."""
    src = (SCRIPTS / f"{name}.py").read_text(encoding="utf-8")
    assert 'default="http://localhost:6333"' not in src
    assert 'default="http://localhost:9200"' not in src


# --- ingest_jsonl's text leg: asked for only when the text backend is on ---- #


@pytest.fixture
def jsonl(script):
    name, run = script
    if name != "ingest_jsonl":
        pytest.skip("ingest_chunks writes Qdrant only")
    return run


def test_jsonl_text_backend_without_es_url_exits_2(jsonl, capsys):
    rc, reached = jsonl([*_BUILD, "--qdrant-url", DEAD, "--text-backend", "elasticsearch"],
                        settings=_settings())
    assert rc == 2 and reached is None
    err = capsys.readouterr().err
    assert "--es-url is required" in err and "--qdrant-url is required" not in err


def test_jsonl_text_backend_with_es_url_is_used(jsonl):
    rc, target = jsonl([*_BUILD, "--qdrant-url", DEAD, "--text-backend", "elasticsearch",
                        "--es-url", DEAD], settings=_settings())
    assert rc == 0 and target.es_url == DEAD


def test_jsonl_text_backend_env_configured_es_url_is_honoured(jsonl):
    s = _settings(explicit={"elasticsearch_url"}, elasticsearch_url=DEAD)
    rc, target = jsonl([*_BUILD, "--qdrant-url", DEAD, "--text-backend", "elasticsearch"],
                       settings=s)
    assert rc == 0 and target.es_url == DEAD


def test_jsonl_no_text_backend_needs_no_es_url(jsonl):
    rc, _ = jsonl([*_BUILD, "--qdrant-url", DEAD], settings=_settings())
    assert rc == 0


def test_jsonl_no_index_needs_no_url(jsonl):
    """``--no-index`` builds the catalog only and writes to no store."""
    rc, target = jsonl([*_BUILD, "--no-index"], settings=_settings())
    assert rc == 0 and target is None


# --------------------------------------------------------------------------- #
# backfill_es_from_qdrant.py / bench_filter_truncation.py — no registry target
# --------------------------------------------------------------------------- #


@pytest.fixture
def backfill(monkeypatch):
    mod = _load("backfill_es_from_qdrant", monkeypatch)
    seen: list = []

    async def spy(args):
        seen.append(args)

    monkeypatch.setattr(mod, "run", spy)
    return mod, seen


@pytest.mark.parametrize("given,missing", [
    ([], "--qdrant-url"),
    (["--qdrant-url", DEAD], "--es-url"),
    (["--es-url", DEAD], "--qdrant-url"),
])
def test_backfill_requires_both_urls(backfill, capsys, given, missing):
    mod, seen = backfill
    with pytest.raises(SystemExit) as e:
        mod.main(["--collection", "c", *given])
    assert e.value.code == 2 and not seen
    assert missing in capsys.readouterr().err


def test_backfill_explicit_urls_are_used(backfill):
    mod, seen = backfill
    mod.main(["--collection", "c", "--qdrant-url", DEAD, "--es-url", DEAD])
    assert (seen[0].qdrant_url, seen[0].es_url) == (DEAD, DEAD)


@pytest.fixture
def bench(monkeypatch):
    pytest.importorskip("numpy")
    pytest.importorskip("qdrant_client")
    return _load("bench_filter_truncation", monkeypatch)


def test_bench_requires_target_url(bench, capsys):
    """The target is where the g2bench_* collection is CREATED; it used to fall
    back to --source-url, which itself defaulted to localhost."""
    with pytest.raises(SystemExit) as e:
        bench.parse_args(["--source-url", DEAD])
    assert e.value.code == 2
    assert "--target-url" in capsys.readouterr().err


def test_bench_requires_source_url_unless_cleanup_only(bench, capsys):
    with pytest.raises(SystemExit) as e:
        bench.parse_args(["--target-url", DEAD])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "--source-url is required" in err and "PRODUCTION" in err
    # the broom reads nothing, so it needs only the target
    assert bench.parse_args(["--target-url", DEAD, "--cleanup-only"]).target_url == DEAD
