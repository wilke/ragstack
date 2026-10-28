"""#828 PR 3: path C's embed step reads the registry like every other path.

``embed_shard.py`` gains ``--collection-id``/``--registry``; with an id it
resolves the entry and refuses (exit 2, before any embed) a flag that
contradicts it — ``ingest_shard.py``'s build-spec check plus
``ingest_jsonl.py``'s ``--chunk-size-unit`` rule. The embedding-file header
gains a ``chunking`` object, and ``load_embeddings.py`` refuses, before any
write, a header that names another ``collection_id`` or whose applied chunking
contradicts the entry (``size_unit`` read method-conditionally, which
``check_build`` never compared). The workflow-wiring reader and the pin on the
shipped CWLs (no embed step binds ``collection_id`` until the repin step R) are
at the bottom.

Offline throughout. The registry is a scratch sqlite file selected by NAME
(``--registry``), so no ambient ``COLLECTION_STORE_*`` setting is read; the
embedder is a fake; store URLs are the dead port ``127.0.0.1:1``; and the
load-side tests replace ``amain`` (the first thing that builds a store) with a
spy, so "refused before any write" is observed as "the spy was not reached".
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from ragstack.collection_store import CollectionSpec, SqliteCollectionStore
from ragstack.ingestion.embed_shard import run_embed_shard
from ragstack.ingestion.embedding_file import SCHEMA, read_embedding_file, read_header
from ragstack.ops import ingest_target as it
from ragstack.tool_image import UNREADABLE_EMBED_STEP, embed_shard_step_wiring

ROOT = Path(__file__).resolve().parents[2]
CWL_DIR = ROOT.parent / "cwl"
sys.path.insert(0, str(ROOT / "scripts"))
import embed_shard as embed_cli  # noqa: E402
import gowe_batch_ingest as gbi  # noqa: E402
import load_embeddings as load_cli  # noqa: E402

DEAD = "http://127.0.0.1:1"
REG = "scratch828"
CID = "scratch-828-pr3"
_OFFLINE = ["--chunk-token-counter", "estimate", "--embedding-model", "",
            "--chunk-max-tokens", "4096", "--embedding-url", DEAD,
            "--symbol-glossary", "off"]
#: The shipped workflows whose embed step runs embed_shard.py (path C).
EMBED_WORKFLOWS = ("embed-bulk.cwl", "jats-ingest.cwl", "pdf-ingest.cwl")


def _spec(cid=CID, **over) -> CollectionSpec:
    body = {"id": cid, "collection": f"store_{cid.replace('-', '_')}",
            "embedding_model": "m", "embedding_model_dim": 4,
            "chunk_method": "sentence", "chunk_size": 400, "chunk_overlap": 0}
    body.update(over)
    return CollectionSpec(**body)


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """A scratch sqlite registry selected by name; returns ``put(spec)``."""
    db = tmp_path / "registry.db"
    store = SqliteCollectionStore(str(db))
    suffix = it.registry_env_suffix(REG)
    monkeypatch.setenv(f"COLLECTION_STORE_BACKEND_{suffix}", "sqlite")
    monkeypatch.setenv(f"COLLECTION_STORE_PATH_{suffix}", str(db))
    monkeypatch.delenv("RAGSTACK_COLLECTION_ID", raising=False)

    def put(spec: CollectionSpec) -> CollectionSpec:
        asyncio.run(store.put(spec))
        return spec

    return put


class _FakeEmbedder:
    calls = 0

    async def embed(self, texts):
        type(self).calls += 1
        return [[float(len(t)), 1.0, 2.0, 3.0] for t in texts]


@pytest.fixture
def embed(tmp_path, monkeypatch):
    """Run ``embed_shard.main`` over a small shard with a fake embedder.

    Returns ``(rc, out_path, embedder_class)``; ``embedder_class.calls`` counts
    embed() calls, so "exits before any embed" is directly observable."""
    shard = tmp_path / "shard.jsonl"
    text = " ".join(f"Sentence number {i} says something plain." for i in range(60))
    shard.write_text(json.dumps({"text": text, "path": "a.txt", "metadata": {}}) + "\n"
                     + json.dumps({"text": text[::-1], "path": "b.txt", "metadata": {}})
                     + "\n", encoding="utf-8")
    _FakeEmbedder.calls = 0
    made: list = []

    def fake_make(**_kw):
        made.append(1)
        return _FakeEmbedder()

    monkeypatch.setattr(embed_cli, "make_embedder_auto", fake_make)
    # No store, ever: this tool must not ask for store URLs.
    monkeypatch.setattr(it, "require_store_urls",
                        lambda *a, **k: pytest.fail("embed_shard asked for store URLs"))

    def _run(*argv: str, name: str = "out"):
        out = tmp_path / f"{name}.emb.jsonl"
        try:
            rc = embed_cli.main([str(shard), "--out", str(out),
                                 "--receipt", str(tmp_path / f"{name}.receipt.json"),
                                 *_OFFLINE, *argv])
        except SystemExit as e:
            rc = e.code
        return rc, out, made

    return _run


# --------------------------------------------------------------------------- #
# embed side: resolution and the build-spec check
# --------------------------------------------------------------------------- #


def test_resolved_run_builds_from_the_entry_and_records_it(registry, embed, monkeypatch):
    """size_unit comes from the entry (the flag agrees, or it would be refused);
    the header records what was resolved and applied."""
    registry(_spec(chunk_params={"size_unit": "tokens"}, chunk_size=64))
    seen: dict = {}
    real = embed_cli.build_chunker

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    monkeypatch.setattr(embed_cli, "build_chunker", spy)
    rc, out, _ = embed("--collection-id", CID, "--registry", REG,
                       "--chunk-method", "sentence", "--chunk-size", "64",
                       "--chunk-overlap", "0", "--chunk-size-unit", "tokens")
    assert rc == 0
    assert seen["size_unit"] == "tokens"
    assert read_header(out)["chunking"] == {
        "collection_id": CID, "method": "sentence", "chunk_size": 64,
        "chunk_overlap": 0, "size_unit": "tokens",
    }


def test_resolved_fixed_records_no_size_unit(registry, embed):
    """A key the method does not apply is absent, whatever the flag says."""
    registry(_spec(chunk_method="fixed", chunk_size=300, chunk_overlap=20,
                   chunk_params={"size_unit": "junk"}))
    rc, out, _ = embed("--collection-id", CID, "--registry", REG,
                       "--chunk-method", "fixed", "--chunk-size", "300",
                       "--chunk-overlap", "20", "--chunk-size-unit", "tokens")
    assert rc == 0
    assert read_header(out)["chunking"] == {
        "collection_id": CID, "method": "fixed", "chunk_size": 300, "chunk_overlap": 20,
    }


@pytest.mark.parametrize("flag,value,named", [
    ("--chunk-size", "401", "chunk_size"),
    ("--chunk-overlap", "7", "chunk_overlap"),
    ("--chunk-method", "words", "chunk_method"),
    ("--embedding-model", "other/model", "embedding_model"),
])
def test_a_contradicting_build_flag_exits_2_before_any_embed(
        registry, embed, capsys, flag, value, named):
    """Mutation: drop the build check from ``_resolve_target`` → the
    contradicting flag embeds (rc 0, a file, embed calls) → this fails."""
    registry(_spec())
    base = {"--chunk-method": "sentence", "--chunk-size": "400", "--chunk-overlap": "0"}
    base[flag] = value
    argv = [x for kv in base.items() for x in kv]
    if flag == "--embedding-model":
        argv += ["--embedding-model", value]  # last one wins over _OFFLINE's ""
    rc, out, made = embed("--collection-id", CID, "--registry", REG, *argv)
    assert rc == 2
    assert not made and _FakeEmbedder.calls == 0 and not out.exists()
    err = capsys.readouterr().err
    assert named in err and CID in err


@pytest.mark.parametrize("unit,params", [
    ("tokens", {}),                       # none recorded = chars
    ("tokens", {"size_unit": "chars"}),
    ("chars", {"size_unit": "tokens"}),
])
def test_a_contradicting_size_unit_exits_2_before_any_embed(
        registry, embed, capsys, unit, params):
    """Mutation: drop ``refuse_size_unit_mismatch`` from ``_resolve_target`` →
    the entry's unit is used silently and the run embeds → this fails."""
    registry(_spec(chunk_params=params))
    rc, out, made = embed("--collection-id", CID, "--registry", REG,
                          "--chunk-method", "sentence", "--chunk-size", "400",
                          "--chunk-overlap", "0", "--chunk-size-unit", unit)
    assert rc == 2
    assert not made and not out.exists()
    assert f"--chunk-size-unit {unit}" in capsys.readouterr().err


def test_create_via_api_is_refused(registry, embed, capsys, monkeypatch):
    monkeypatch.setattr(it, "create_via_api",
                        lambda *a, **k: pytest.fail("embed_shard minted a collection"))
    rc, out, made = embed("--collection-id", "new-one", "--registry", REG,
                          "--create-via-api", DEAD)
    assert rc == 2 and not made and not out.exists()
    assert "--create-via-api" in capsys.readouterr().err


def test_registry_without_collection_id_is_refused(registry, embed, capsys):
    """A named registry with no id to resolve is a mis-wired step; it must not
    fall through to an unresolved run. Mutation: drop the refusal → rc 0 and
    an embedding file → this fails."""
    rc, out, made = embed("--registry", REG, "--chunk-method", "sentence")
    assert rc == 2 and not made and not out.exists()
    assert "--registry needs --collection-id" in capsys.readouterr().err


def test_an_unknown_id_is_refused_before_any_embed(registry, embed, capsys):
    registry(_spec())
    rc, out, made = embed("--collection-id", "not-there", "--registry", REG,
                          "--chunk-method", "sentence", "--chunk-size", "400",
                          "--chunk-overlap", "0")
    assert rc == 2 and not made and not out.exists()
    assert "not in the registry" in capsys.readouterr().err


def test_the_semantic_methods_stay_refused(registry, embed):
    registry(_spec(chunk_method="semantic", chunk_size=None, chunk_overlap=None))
    rc, out, made = embed("--collection-id", CID, "--registry", REG,
                          "--chunk-method", "semantic")
    assert "breakpoint embedding bridge" in str(rc)
    assert not out.exists()


# --------------------------------------------------------------------------- #
# embed side: an unresolved run is today's run plus the header object
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("method,unit", [("sentence", "tokens"), ("words", "chars"),
                                         ("fixed", "tokens")])
def test_unresolved_run_is_todays_output_apart_from_the_chunking_object(
        embed, tmp_path, method, unit):
    """Without --collection-id: the header is the old one plus ``chunking`` (no
    ``collection_id`` in it), and every chunk line is byte-identical to the file
    written by a chunker built the pre-PR way (``--chunk-size-unit`` as the
    unit, no spec) with no ``chunking`` object — i.e. this PR changed nothing
    about what an unresolved run chunks or embeds."""
    argv = ["--chunk-method", method, "--chunk-size", "120", "--chunk-overlap", "10",
            "--chunk-size-unit", unit]
    rc, out, _ = embed(*argv)
    assert rc == 0
    new = out.read_text(encoding="utf-8").splitlines(keepends=True)

    # The baseline: the tool's pipeline with the chunker built exactly as the
    # pre-PR ``_build_chunker(args)`` built it (the flag's unit, no spec),
    # written with no chunking object — the writer then emits the old header.
    import httpx

    from ragstack.ingestion.chunker_config import build_chunker

    args = embed_cli.parse_args([str(tmp_path / "shard.jsonl"), *_OFFLINE, *argv])
    todays_chunker, _c, _m = build_chunker(
        args.chunk_method, chunk_size=args.chunk_size, chunk_overlap=args.chunk_overlap,
        model=args.embedding_model, token_backend=args.chunk_token_counter,
        max_tokens=args.chunk_max_tokens, base_url=args.embedding_url[0],
        api_key=args.embedding_api_key, size_unit=args.chunk_size_unit,
    )

    async def _baseline():
        async with httpx.AsyncClient() as http:
            pipeline = embed_cli._build_pipeline(args, http)
            pipeline.chunker = todays_chunker
            await run_embed_shard(pipeline, str(tmp_path / "shard.jsonl"), args.tenant,
                                  "s", tmp_path / "old.emb.jsonl",
                                  group_size=embed_cli._derive_group_size(args))

    asyncio.run(_baseline())
    old = (tmp_path / "old.emb.jsonl").read_text(encoding="utf-8").splitlines(keepends=True)

    assert new[1:] == old[1:] and len(new) > 1
    new_header, old_header = json.loads(new[0]), json.loads(old[0])
    chunking = new_header.pop("chunking")
    assert new_header == old_header and set(old_header) == {"schema", "tenant", "dim"}
    assert old[0] == json.dumps(old_header, sort_keys=True) + "\n"
    assert "collection_id" not in chunking
    assert chunking["method"] == method
    assert ("size_unit" in chunking) == (method in ("sentence", "words"))


# --------------------------------------------------------------------------- #
# load side
# --------------------------------------------------------------------------- #


def _emb_file(path: Path, chunking: dict | None) -> str:
    header = {"schema": SCHEMA, "tenant": "public", "dim": 4}
    if chunking is not None:
        header["chunking"] = chunking
    line = {"id": "c1", "doc_id": "d1", "content": "x", "embedding": [1.0, 2.0, 3.0, 4.0],
            "metadata": {}}
    path.write_text(json.dumps(header, sort_keys=True) + "\n"
                    + json.dumps(line, sort_keys=True) + "\n", encoding="utf-8")
    return str(path)


@pytest.fixture
def load(tmp_path, monkeypatch, registry):
    """Drive ``load_embeddings.main`` with ``amain`` replaced by a spy.

    Returns ``(rc, reached_target_or_None)``."""
    reached: list = []

    async def spy(args, target=None):
        reached.append(target)
        return 0

    monkeypatch.setattr(load_cli, "amain", spy)

    def _run(chunking, *extra, spec=None, backend="qdrant"):
        registry(spec or _spec())
        reached.clear()
        f = _emb_file(tmp_path / "e.emb.jsonl", chunking)
        argv = [f, "--out", str(tmp_path / "s.json"), "--collection-id", CID,
                "--registry", REG, "--qdrant-url", DEAD, "--es-url", DEAD, *extra]
        if backend == "memory":
            argv += ["--vector-backend", "memory", "--text-backend", "memory"]
        try:
            rc = load_cli.main(argv)
        except SystemExit as e:
            rc = e.code
        return rc, (reached[0] if reached else None)

    return _run


_APPLIED = {"method": "sentence", "chunk_size": 400, "chunk_overlap": 0, "size_unit": "chars"}


def test_load_accepts_a_header_that_matches_the_entry(load):
    rc, target = load({"collection_id": CID, **_APPLIED})
    assert rc == 0 and target is not None and target.collection_id == CID


def test_load_refuses_a_header_naming_another_collection(load, capsys):
    """Mutation: drop the collection_id comparison → the file loads → fails."""
    rc, target = load({"collection_id": "some-other-collection", **_APPLIED})
    assert rc == 2 and target is None
    err = capsys.readouterr().err
    assert "some-other-collection" in err and CID in err


@pytest.mark.parametrize("key,value", [("method", "words"), ("chunk_size", 512),
                                       ("chunk_overlap", 64)])
def test_load_refuses_applied_chunking_that_contradicts_the_entry(load, capsys, key, value):
    rc, target = load({**_APPLIED, key: value})
    assert rc == 2 and target is None
    assert key.replace("method", "chunk_method") in capsys.readouterr().err


@pytest.mark.parametrize("entry_params,applied", [
    ({}, "tokens"),                         # nothing recorded = chars
    ({"size_unit": "chars"}, "tokens"),
    ({"size_unit": "tokens"}, "chars"),
])
@pytest.mark.parametrize("method", ["sentence", "words"])
def test_load_refuses_a_contradicting_size_unit(load, capsys, method, entry_params, applied):
    """The hole ``check_build`` left: a ``tokens`` embed run loaded into a
    ``chars`` entry. Mutation: drop the size_unit comparison → loads → fails."""
    rc, target = load({**_APPLIED, "method": method, "size_unit": applied},
                      spec=_spec(chunk_method=method, chunk_params=entry_params))
    assert rc == 2 and target is None
    assert "size_unit" in capsys.readouterr().err


@pytest.mark.parametrize("method", ["sentence", "words"])
def test_load_accepts_a_matching_size_unit(load, method):
    rc, target = load({**_APPLIED, "method": method, "size_unit": "tokens"},
                      spec=_spec(chunk_method=method, chunk_params={"size_unit": "tokens"}))
    assert rc == 0 and target is not None


@pytest.mark.parametrize("unit", ["tokens", "chars", "junk"])
@pytest.mark.parametrize("entry_params", [{}, {"size_unit": "garbage"}])
def test_load_accepts_any_size_unit_on_a_fixed_token_entry(load, unit, entry_params):
    """Only sentence/words read the unit. Mutation: compare unconditionally →
    a fixed_token file with a junk unit is refused → this fails."""
    rc, target = load({"collection_id": CID, "method": "fixed_token", "chunk_size": 256,
                       "chunk_overlap": 32, "size_unit": unit},
                      spec=_spec(chunk_method="fixed_token", chunk_size=256,
                                 chunk_overlap=32, chunk_params=entry_params))
    assert rc == 0 and target is not None


def test_an_old_header_loads_exactly_as_today(load):
    """No ``chunking`` object (an image older than PR 3) → nothing to
    contradict; the same target reaches the load as before this PR."""
    rc, target = load(None)
    assert rc == 0 and target is not None and target.collection_id == CID
    assert it.check_header_chunking({"schema": SCHEMA, "dim": 4}, target) is None


def test_a_malformed_chunking_object_is_refused(load, capsys):
    rc, target = load(["not", "an", "object"])  # type: ignore[arg-type]
    assert rc == 2 and target is None
    assert "must be an object" in capsys.readouterr().err


def test_memory_backend_skips_the_check_with_a_logged_line(load, capsys):
    """No target is resolved under --vector-backend memory, so there is nothing
    to check against; the skip is said, not silent."""
    rc, target = load({"collection_id": "some-other-collection", **_APPLIED},
                      backend="memory")
    assert rc == 0 and target is None
    assert "header chunking check skipped" in capsys.readouterr().err


def test_a_resolved_embed_file_loads_into_its_own_entry(registry, embed, load):
    """The two halves agree: what embed_shard writes for an entry, the load
    step accepts for that entry."""
    registry(_spec(chunk_params={"size_unit": "tokens"}, chunk_size=64))
    rc, out, _ = embed("--collection-id", CID, "--registry", REG, "--chunk-method",
                       "sentence", "--chunk-size", "64", "--chunk-overlap", "0",
                       "--chunk-size-unit", "tokens")
    assert rc == 0
    header = read_header(out)
    chunks, _h = read_embedding_file(out)
    assert chunks
    target = it.resolve(CID, registry=REG)
    it.check_header_chunking(header, target)  # no raise


# --------------------------------------------------------------------------- #
# the workflow-wiring reader (used by gowe_batch_ingest.py; the pre-check is PR 4)
# --------------------------------------------------------------------------- #

_TOOL = (
    "    run:\n      class: CommandLineTool\n"
    "      baseCommand: {base}\n"
    "      inputs:\n        shard: {{type: File, inputBinding: {{position: 2}}}}\n"
    "        collection_id:\n          type: ['null', string]\n"
    "          inputBinding: {{prefix: --collection-id}}\n"
    "      outputs: {{}}\n"
)


def _workflow(step_in: str, base: str = "[python, /opt/ragstack/scripts/embed_shard.py]") -> str:
    return ("cwlVersion: v1.2\nclass: Workflow\ninputs:\n  shards: File[]\n"
            "  collection_id: string\noutputs: {}\nsteps:\n  embed:\n"
            f"    in: {step_in}\n    out: []\n" + _TOOL.format(base=base))


WIRED_MAP = _workflow("{shard: shards, collection_id: collection_id}")
WIRED_LIST = _workflow("[{id: shard, source: shards}, {id: collection_id, source: collection_id}]")
WIRED_STRING_BASE = _workflow("{shard: shards, collection_id: {source: collection_id}}",
                              base="embed_shard.py")
UNWIRED = _workflow("{shard: shards}")
OTHER_TOOL = _workflow("{shard: shards, collection_id: collection_id}",
                       base="[python, /opt/ragstack/scripts/ingest_shard.py]")


@pytest.mark.parametrize("text", [WIRED_MAP, WIRED_LIST, WIRED_STRING_BASE],
                         ids=["map", "list", "string-baseCommand"])
def test_the_reader_recognises_a_wired_fixture(text):
    assert embed_shard_step_wiring(text) == {"embed": True}


def test_the_reader_recognises_an_unwired_fixture():
    assert embed_shard_step_wiring(UNWIRED) == {"embed": False}


def test_the_reader_ignores_other_tools_and_reports_unreadable_steps():
    assert embed_shard_step_wiring(OTHER_TOOL) == {}
    external = ("cwlVersion: v1.2\nclass: Workflow\ninputs: {}\noutputs: {}\n"
                "steps:\n  embed:\n    in: {collection_id: x}\n    out: []\n"
                "    run: tools/embed.cwl\n")
    assert embed_shard_step_wiring(external) == {"embed": None}
    assert embed_shard_step_wiring("not: [valid") == {}
    assert embed_shard_step_wiring("- a list, not a workflow") == {}


_SCRIPT = "/opt/ragstack/scripts/embed_shard.py"
#: Shapes the reader does not parse. Each names embed_shard.py, so each must
#: read as unreadable (fail closed), never as "no embed step".
UNPARSED_SHAPES = {
    "packed-graph": (
        "cwlVersion: v1.2\n$graph:\n- id: main\n  class: Workflow\n  inputs: []\n"
        "  outputs: []\n  steps:\n  - id: embed\n    in: []\n    out: []\n"
        "    run: '#embed_tool'\n- id: embed_tool\n  class: CommandLineTool\n"
        f"  baseCommand: [python, {_SCRIPT}]\n  inputs: []\n  outputs: []\n"
    ),
    "script-in-arguments": _workflow(
        "{shard: shards}", base="python").replace(
        "      outputs: {}\n", f"      arguments: [{_SCRIPT}]\n      outputs: {{}}\n"),
    "shell-baseCommand": _workflow(
        "{shard: shards}", base=f'[bash, -c, "python {_SCRIPT} $0"]'),
    "nested-subworkflow": (
        "cwlVersion: v1.2\nclass: Workflow\ninputs: {}\noutputs: {}\nsteps:\n"
        "  outer:\n    in: {}\n    out: []\n    run:\n      class: Workflow\n"
        "      inputs: {}\n      outputs: {}\n      steps:\n        embed:\n"
        "          in: {}\n          out: []\n          run:\n"
        "            class: CommandLineTool\n"
        f"            baseCommand: [python, {_SCRIPT}]\n"
        "            inputs: {}\n            outputs: {}\n"
    ),
    "not-yaml": f"steps: [unclosed\n  baseCommand: {_SCRIPT}\n",
}


@pytest.mark.parametrize("name", sorted(UNPARSED_SHAPES))
def test_an_unparsed_shape_naming_the_script_fails_closed(name):
    """Mutation: drop the fallback → these read ``{}`` ("no embed step"), which
    PR 4's pre-check would take as nothing to refuse → this fails."""
    wiring = embed_shard_step_wiring(UNPARSED_SHAPES[name])
    assert wiring.get(UNREADABLE_EMBED_STEP, False) is None, wiring
    assert not any(wiring.values())


def test_a_comment_naming_the_script_is_not_a_mention():
    text = "# feeds embed_shard.py downstream\n" + OTHER_TOOL
    assert embed_shard_step_wiring(text) == {}


@pytest.mark.parametrize("name", EMBED_WORKFLOWS)
def test_the_reader_sees_each_shipped_embed_workflow_as_unwired(name):
    """Through the driver's own entry point, on the real texts."""
    path = CWL_DIR / name
    if not path.is_file():
        pytest.skip("no cwl/ in this checkout")
    wiring = gbi.embed_step_wiring(str(path))
    assert len(wiring) == 1, wiring
    assert list(wiring.values()) == [False], wiring


def test_every_other_shipped_workflow_reads_as_no_embed_step():
    """``{}`` for every shipped CWL but the three embed workflows — including
    ``pdf-extract.cwl``, whose header COMMENT names embed_shard.py."""
    if not CWL_DIR.is_dir():
        pytest.skip("no cwl/ in this checkout")
    others = [p for p in sorted(CWL_DIR.glob("*.cwl")) if p.name not in EMBED_WORKFLOWS]
    assert any(p.name == "pdf-extract.cwl" for p in others)
    for path in others:
        assert gbi.embed_step_wiring(str(path)) == {}, path.name


# --------------------------------------------------------------------------- #
# the pin: no shipped embed step binds collection_id until the repin step R
# --------------------------------------------------------------------------- #


def test_no_shipped_embed_step_binds_collection_id_until_the_image_repin() -> None:
    """ADR-0010 decision 4: a CWL binds a tool flag only once the image it NAMES
    carries it. Every shipped workflow names a tools image older than #828 PR 3,
    whose ``embed_shard.py`` has no ``--collection-id`` — binding it now would
    fail every embed task at argparse. Flip this pin on the repin branch (step R
    of docs/plans/828-document-structure.md §12), which wires ``collection_id``
    (and ``registry`` / ``registry_db``) into the embed steps of
    ``pdf-ingest.cwl`` and ``jats-ingest.cwl`` and gives ``embed-bulk.cwl`` its
    two optional inputs."""
    if not CWL_DIR.is_dir():
        pytest.skip("no cwl/ in this checkout")
    import yaml

    seen = 0
    for path in sorted(CWL_DIR.glob("*.cwl")):
        text = path.read_text(encoding="utf-8")
        wiring = embed_shard_step_wiring(text)
        assert not any(wiring.values()), (path.name, wiring)
        steps = (yaml.safe_load(text) or {}).get("steps") or {}
        for sid in wiring:
            tool_inputs = steps[sid]["run"].get("inputs") or {}
            assert "collection_id" not in tool_inputs, (path.name, sid)
            assert "--collection-id" not in json.dumps(steps[sid]["run"]), (path.name, sid)
            seen += 1
    assert seen == len(EMBED_WORKFLOWS)
