"""#828 PR 4: the path-C backstops for the document-structure markers (§7).

1. ``load_embeddings.py`` — authoritative: before any write it refuses an
   embedding file whose header's ``chunking`` object does not show the
   entry's markers as applied, or shows markers the entry does not record, or
   has no ``chunking`` object at all against a marked entry (an old image).
   An unmarked entry with an old header loads exactly as today.
2. ``gowe_batch_ingest.py`` — early: for a marked collection it exits 2 before
   any submission unless the workflow's ``embed_shard.py`` step binds
   ``collection_id``. Every SHIPPED text is wired since the repin step R, so
   the unwired case — an operator submitting from a checkout older than R — is
   a fixture text (``_unwired``), and the shipped texts are the wired case.

(The API's dispatch backstop, item 3, is in
``tests/api/test_structure_markers_api.py``.) Offline: a scratch sqlite
registry selected by name, ``amain`` / ``submit`` replaced by spies, dead
store URLs.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ragstack.collection_store import SqliteCollectionStore
from ragstack.ingestion.embedding_file import SCHEMA
from ragstack.ops import ingest_target as it
from tests.unit.test_embed_shard_registry import (  # noqa: F401 — fixtures
    CID,
    CWL_DIR,
    EMBED_WORKFLOWS,
    _spec,
    load,
    registry,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import gowe_batch_ingest as gbi  # noqa: E402

_APPLIED = {"collection_id": CID, "method": "sentence", "chunk_size": 400,
            "chunk_overlap": 0, "size_unit": "chars"}
PLAIN = {"text_render": "markdown-plain/1"}


# --------------------------------------------------------------------------- #
# 1. load_embeddings.py
# --------------------------------------------------------------------------- #

def test_a_marked_header_loads_into_its_marked_entry(load) -> None:  # noqa: F811
    rc, target = load({**_APPLIED, **PLAIN}, spec=_spec(chunk_params=dict(PLAIN)))
    assert rc == 0 and target is not None and target.collection_id == CID


def test_an_unmarked_file_is_refused_by_a_marked_entry_before_any_write(
    load, capsys  # noqa: F811
) -> None:
    """MUTATION: drop the marker comparison in ``check_header_chunking`` → a
    marked entry accepts a file embedded without its render → fails."""
    rc, target = load(dict(_APPLIED), spec=_spec(chunk_params=dict(PLAIN)))
    assert rc == 2 and target is None  # the amain spy was never reached
    err = capsys.readouterr().err
    assert "text_render='markdown-plain/1'" in err and "none" in err


def test_a_marked_file_is_refused_by_an_unmarked_entry(load, capsys) -> None:  # noqa: F811
    rc, target = load({**_APPLIED, **PLAIN}, spec=_spec())
    assert rc == 2 and target is None
    assert "text_render" in capsys.readouterr().err


@pytest.mark.parametrize("header_markers", [
    {"text_render": "markdown-plain/1", "structure_cuts": "sections/1"},
    {"structure_cuts": "sections/1"},
    {"text_render": "markdown-plain/2"},
])
def test_any_marker_difference_is_refused(load, capsys, header_markers) -> None:  # noqa: F811
    rc, target = load({**_APPLIED, **header_markers}, spec=_spec(chunk_params=dict(PLAIN)))
    assert rc == 2 and target is None
    assert "document-structure markers" in capsys.readouterr().err


def test_an_old_header_is_refused_by_a_marked_entry(load, capsys) -> None:  # noqa: F811
    """An image older than PR 3 writes no ``chunking`` object: nothing shows the
    markers were applied, so a marked entry refuses it."""
    rc, target = load(None, spec=_spec(chunk_params=dict(PLAIN)))
    assert rc == 2 and target is None
    assert "no 'chunking' object" in capsys.readouterr().err


def test_an_unmarked_entry_with_an_old_header_loads_exactly_as_today(load) -> None:  # noqa: F811
    rc, target = load(None, spec=_spec())
    assert rc == 0 and target is not None and target.collection_id == CID
    assert it.check_header_chunking({"schema": SCHEMA, "dim": 4}, target) is None


def test_a_pr3_header_without_markers_still_loads_into_an_unmarked_entry(load) -> None:  # noqa: F811
    rc, target = load(dict(_APPLIED), spec=_spec())
    assert rc == 0 and target is not None


def test_the_comparison_names_a_marker_this_build_cannot_apply(load, capsys) -> None:  # noqa: F811
    """The load step COMPARES (``unapplied=()``): a hand-edited entry recording
    ``structure_cuts`` is refused as a mismatch, and a header that shows it
    applied would match — nothing here applies a marker."""
    entry = {"structure_cuts": "sections/1"}
    rc, _ = load(dict(_APPLIED), spec=_spec(chunk_params=entry))
    assert rc == 2 and "structure_cuts='sections/1'" in capsys.readouterr().err
    rc, target = load({**_APPLIED, **entry}, spec=_spec(chunk_params=entry))
    assert rc == 0 and target is not None


def test_a_junk_marker_on_the_entry_is_refused(load, capsys) -> None:  # noqa: F811
    rc, _ = load(dict(_APPLIED), spec=_spec(chunk_params={"text_render": "junk"}))
    assert rc == 2 and "text_render" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# 2. gowe_batch_ingest.py
# --------------------------------------------------------------------------- #

def _wired(text: str) -> str:
    """A fixture: the shipped text with ``collection_id`` bound into the
    ``in:`` of its embed_shard.py step — what the repin step R ships (the
    tool's own input binding is R's too; the reader checks the step)."""
    import yaml

    doc = yaml.safe_load(text)
    steps = doc["steps"]
    n = 0
    for step in (steps.values() if isinstance(steps, dict) else steps):
        base = step.get("run", {}).get("baseCommand") if isinstance(step.get("run"), dict) else None
        if base and "embed_shard.py" in " ".join(base if isinstance(base, list) else [base]):
            step["in"]["collection_id"] = "collection_id"
            n += 1
    assert n == 1
    return yaml.safe_dump(doc, sort_keys=False)


def _unwired(text: str) -> str:
    """A fixture: the shipped text with ``collection_id`` dropped from the
    ``in:`` of its embed_shard.py step — the text of a checkout before the
    repin step R (the reader checks the step's ``in:``)."""
    import yaml

    doc = yaml.safe_load(text)
    n = 0
    for step in doc["steps"].values():
        run = step.get("run")
        base = run.get("baseCommand") if isinstance(run, dict) else None
        if base and "embed_shard.py" in " ".join(base if isinstance(base, list) else [base]):
            assert step["in"].pop("collection_id") == "collection_id"
            n += 1
    assert n == 1
    return yaml.safe_dump(doc, sort_keys=False)


@pytest.fixture
def driver(tmp_path, monkeypatch):
    """Run ``gowe_batch_ingest.main`` against a scratch sqlite registry and a
    one-shard plan, with every engine/store call replaced. Returns
    ``(rc, submitted)``; ``submitted`` lists the (cwl, inputs) of every
    submission the driver BUILT (``submit`` records and then stops the run)."""
    db = tmp_path / "registry.db"
    store = SqliteCollectionStore(str(db))
    plan = tmp_path / "plan"
    plan.mkdir()
    (plan / "shard-00000.jsonl").write_text("{}\n", encoding="utf-8")
    submitted: list[tuple[str, dict]] = []

    class _Stop(Exception):
        pass

    def fake_submit(gowe, server, cwl, inputs, group):
        submitted.append((cwl, json.loads(Path(inputs).read_text(encoding="utf-8"))))
        raise _Stop

    monkeypatch.setattr(gbi, "submit", fake_submit)
    monkeypatch.setattr(gbi, "store_counts", lambda *a, **k: (0, 0))

    def run(cwl_text: str, chunk_params: dict, *, name: str = "wf.cwl", dry: bool = False):
        import asyncio

        asyncio.run(store.put(_spec(chunk_params=chunk_params)))
        cwl = tmp_path / name
        cwl.write_text(cwl_text, encoding="utf-8")
        template = tmp_path / "template.json"
        template.write_text(json.dumps({
            "collection_id": CID, "registry_db": {"class": "File", "location": str(db)},
            "qdrant_url": "http://127.0.0.1:1", "es_url": "http://127.0.0.1:1",
        }), encoding="utf-8")
        submitted.clear()
        argv = ["--plan", str(plan), "--cwl", str(cwl), "--inputs-template", str(template),
                "--out", str(tmp_path / "run"), "--stage-out", str(tmp_path / "stage")]
        if dry:
            argv.append("--dry-run")
        try:
            rc = gbi.main(argv)
        except SystemExit as e:
            rc = e.code
        except _Stop:
            rc = "submitted"
        return rc, list(submitted)

    return run


def _shipped(name: str) -> str:
    path = CWL_DIR / name
    if not path.is_file():
        pytest.skip("no cwl/ in this checkout")
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", EMBED_WORKFLOWS)
def test_a_marked_entry_on_an_unwired_workflow_exits_before_any_submission(
    driver, capsys, name
) -> None:
    """The shipped text before R (``_unwired``). MUTATION: drop the pre-check →
    a submission is built for the unwired text → fails."""
    rc, submitted = driver(_unwired(_shipped(name)), dict(PLAIN))
    assert rc == 2 and submitted == []
    err = capsys.readouterr().err
    assert "collection_id" in err and "text_render" in err


def test_the_refusal_comes_before_the_dry_run_plan(driver, capsys) -> None:
    rc, submitted = driver(_unwired(_shipped("jats-ingest.cwl")), dict(PLAIN), dry=True)
    assert rc == 2 and submitted == []
    assert "batch(es)" not in capsys.readouterr().out


#: A marker that passes the wiring check whatever the workflow's glossary
#: step does (a hand-edited entry; this build refuses it at create).
CUTS = {"structure_cuts": "sections/1"}


@pytest.mark.parametrize("name", EMBED_WORKFLOWS)
def test_a_marked_entry_on_each_shipped_text_builds_a_submission(driver, name) -> None:
    """The repin step R wired every shipped embed step, so the pre-check
    (``refuse_marked_on_unwired_workflow``) accepts the shipped texts AS
    SHIPPED for a marked entry — the wired half of PR 4's test, now on real
    texts. MUTATION: drop collection_id from one shipped embed step → exit 2
    → fails."""
    rc, submitted = driver(_shipped(name), dict(CUTS))
    assert rc == "submitted" and len(submitted) == 1
    _cwl, inputs = submitted[0]
    assert inputs["collection_id"] == CID and inputs["shards"]


@pytest.mark.parametrize("name", EMBED_WORKFLOWS)
def test_a_marked_entry_on_a_wired_fixture_text_builds_a_submission(driver, name) -> None:
    rc, submitted = driver(_wired(_shipped(name)), dict(CUTS))
    assert rc == "submitted" and len(submitted) == 1
    _cwl, inputs = submitted[0]
    assert inputs["collection_id"] == CID and inputs["shards"]


def test_text_render_on_a_wired_glossary_free_workflow_builds_a_submission(driver) -> None:
    rc, submitted = driver(_wired(_shipped("pdf-ingest.cwl")), dict(PLAIN))
    assert rc == "submitted" and len(submitted) == 1
    rc, submitted = driver(_shipped("pdf-ingest.cwl"), dict(PLAIN))   # as shipped, after R
    assert rc == "submitted" and len(submitted) == 1


@pytest.mark.parametrize("name", ["jats-ingest.cwl", "embed-bulk.cwl"])
def test_text_render_on_a_workflow_with_a_glossary_step_is_refused(driver, capsys,
                                                                   name) -> None:
    """#828 PR 4 review B1: the glossary step hashes the RAW shard text; the
    load would refuse every file after the embeds ran. MUTATION: drop the
    glossary check → a submission is built → fails."""
    rc, submitted = driver(_wired(_shipped(name)), dict(PLAIN))
    assert rc == 2 and submitted == []
    assert "glossary file" in capsys.readouterr().err


def test_an_unmarked_entry_on_an_unwired_text_is_not_refused(driver) -> None:
    rc, submitted = driver(_unwired(_shipped("jats-ingest.cwl")), {})
    assert rc == "submitted" and len(submitted) == 1


def test_a_workflow_without_an_embed_shard_step_is_not_refused(driver) -> None:
    """``ingest-bulk.cwl`` chunks with ingest_shard.py, which resolves the entry
    itself: there is no embed step to wire (it does run a glossary step, so
    ``text_render`` there is refused for that reason, tested above)."""
    rc, submitted = driver(_shipped("ingest-bulk.cwl"), dict(CUTS))
    assert rc == "submitted" and len(submitted) == 1
    rc, submitted = driver(_shipped("ingest-bulk.cwl"), dict(PLAIN))
    assert rc == 2 and submitted == []


@pytest.mark.parametrize(("raw", "needle"), [
    ('"just a string"', "not an object"),
    ("[1, 2]", "not an object"),
    ("{not json", "unreadable chunk_params"),
])
def test_malformed_chunk_params_are_refused_not_read_as_unmarked(tmp_path, capsys,
                                                                 raw, needle) -> None:
    """Argo N1: fail closed."""
    import sqlite3

    db = tmp_path / "reg.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE collections (id TEXT, collection TEXT, chunk_method TEXT, "
                     "chunk_params TEXT)")
        conn.execute("INSERT INTO collections VALUES (?, ?, ?, ?)", (CID, "s", "fixed", raw))
    with pytest.raises(SystemExit) as ex:
        gbi.entry_chunking(str(db), CID)
    assert ex.value.code == 2 and needle in capsys.readouterr().err


def test_a_registry_without_the_columns_is_an_exit_2_message(tmp_path, capsys) -> None:
    import sqlite3

    db = tmp_path / "reg.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE collections (id TEXT, collection TEXT)")
        conn.execute("INSERT INTO collections VALUES (?, ?)", (CID, "s"))
    with pytest.raises(SystemExit) as ex:
        gbi.entry_chunking(str(db), CID)
    assert ex.value.code == 2 and "cannot read chunk_method" in capsys.readouterr().err


def test_an_unreadable_embed_step_fails_closed(driver, capsys) -> None:
    text = ("cwlVersion: v1.2\nclass: Workflow\ninputs: {}\noutputs: {}\nsteps:\n"
            "  embed:\n    run: tools/embed.cwl\n    in: {collection_id: collection_id}\n"
            "    out: []\n# runs embed_shard.py\n")
    text += "doc: wraps embed_shard.py\n"
    rc, submitted = driver(text, dict(PLAIN))
    assert rc == 2 and submitted == []


def test_any_recorded_marker_counts_even_one_this_checkout_cannot_apply(driver) -> None:
    rc, submitted = driver(_unwired(_shipped("pdf-ingest.cwl")),
                           {"structure_cuts": "sections/1"})
    assert rc == 2 and submitted == []


# --------------------------------------------------------------------------- #
# B1: a glossary file is refused with text_render (path C's load step)
# --------------------------------------------------------------------------- #

def _glossary_file(tmp_path: Path) -> str:
    import glossary_step

    shard = tmp_path / "g-shard.jsonl"
    shard.write_text(json.dumps({"text": "x", "path": "/d/p.md", "metadata": {}}) + "\n",
                     encoding="utf-8")
    out = tmp_path / "glossary.jsonl"
    assert glossary_step.main([str(shard), "--out", str(out)]) == 0
    return str(out)


def test_load_refuses_a_glossary_file_for_a_text_render_entry(load, tmp_path,  # noqa: F811
                                                              capsys) -> None:
    """MUTATION: drop the refusal → the load reaches verification against the
    raw-text hashes (unverifiable or refused per document) → fails."""
    gl = _glossary_file(tmp_path)
    rc, target = load({**_APPLIED, **PLAIN}, "--glossary-file", gl,
                      spec=_spec(chunk_params=dict(PLAIN)))
    assert rc == 2 and target is None
    assert "glossary file cannot be used" in capsys.readouterr().err


def test_load_keeps_the_glossary_file_for_an_unmarked_entry(load, tmp_path, capsys) -> None:  # noqa: F811
    gl = _glossary_file(tmp_path)
    rc, _target = load(dict(_APPLIED), "--glossary-file", gl, spec=_spec())
    # The file is verified against the embedding file's documents (it names
    # another document here, so that verification refuses) — but not by the
    # text_render refusal.
    err = capsys.readouterr().err
    assert "glossary file cannot be used" not in err
    assert rc == 2 and "refusing: glossary file" in err  # the #847 verification, not ours


# --------------------------------------------------------------------------- #
# ingest_chunks.py: pre-chunked JSON cannot honour a marker
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("params", [dict(PLAIN), {"structure_cuts": "sections/1"},
                                    {"text_render": "junk"}])
def test_ingest_chunks_refuses_a_marked_entry(registry, capsys, params) -> None:  # noqa: F811
    import ingest_chunks

    registry(_spec(chunk_params=params))
    target = it.resolve(CID, registry="scratch828")
    with pytest.raises(SystemExit) as ex:
        ingest_chunks.refuse_marked_entry(target)
    assert ex.value.code == 2
    assert next(iter(params)) in capsys.readouterr().err


def test_ingest_chunks_main_exits_before_reading_for_a_marked_entry(
    registry, tmp_path, monkeypatch, capsys  # noqa: F811
) -> None:
    import ingest_chunks

    registry(_spec(chunk_params=dict(PLAIN)))
    monkeypatch.setattr(ingest_chunks, "run", lambda *a, **k: pytest.fail("reached run"))
    monkeypatch.setattr(sys, "argv", ["ingest_chunks.py", str(tmp_path / "absent.json"),
                                      "--collection-id", CID, "--registry", "scratch828"])
    with pytest.raises(SystemExit) as ex:
        ingest_chunks.main()
    assert ex.value.code == 2 and "pre-chunked" in capsys.readouterr().err


def test_ingest_chunks_accepts_an_unmarked_entry(registry) -> None:  # noqa: F811
    import ingest_chunks

    registry(_spec())
    ingest_chunks.refuse_marked_entry(it.resolve(CID, registry="scratch828"))
