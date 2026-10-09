"""Provenance fields (ADR-0010 decision 8; #655 step 2).

Every collection version records what built it: the GoWe ``workflow_id``, the
tools image name and its digest (seeded by the API on the submission —
neither is knowable by the worker), and the image's own ``RELEASE`` identity
(read by the worker — a file's sha256 cannot live inside the file). Covered:

* ``ragstack.provenance`` — the record with and without a ``RELEASE`` file,
  and the reader that makes a pre-step-2 manifest say "unknown";
* ``ragstack.tool_image`` — the image name of a CWL text, the committed
  receipt beside it, the digest only when the receipt names that image, and
  the seeding restricted to declared workflow inputs;
* the registrars (``GoWeBackend``, the restorer, the graph-extract runner)
  seed the inputs between registration and submission;
* the pack step (``archive_version.py`` / ``write_version`` /
  ``write_tombstone``) and the shard receipt carry the object;
* the stamping writes ``cwl/tool-image.receipt.json`` and ``--check`` holds
  it and the stamped name together;
* the shipped CWL declares the three inputs on every API-registered workflow
  and binds them to the tool that records them.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from ragstack.ingestion.archive import read_manifest, write_tombstone, write_version
from ragstack.ingestion.gowe_backend import GoWeBackend
from ragstack.ingestion.manifest import WorkItem
from ragstack.ingestion.receipts import COMPLETED, ShardReceipt
from ragstack.provenance import (
    PROVENANCE_KEYS,
    add_provenance_arguments,
    provenance_from_args,
    read_provenance,
    read_release,
    tool_provenance,
    unknown_provenance,
)
from ragstack.tool_image import (
    DEFAULT_TOOL_IMAGE,
    PROVENANCE_INPUTS,
    RECEIPT_BASENAME,
    declared_workflow_inputs,
    provenance_inputs,
    read_committed_receipt,
    stamped_image_name,
    tool_image_digest_for,
    tool_image_of,
)

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[3]
CWL_DIR = REPO / "cwl"
SCRIPTS = REPO / "python" / "scripts"
STAMP = SCRIPTS / "stamp_tool_image.py"
sys.path.insert(0, str(SCRIPTS))

DIGEST = "d" * 64
NAME = "ragstack-tools-v9.9.9-b1.sif"
RELEASE_TEXT = "version=v9.9.9\ncommit=" + "c" * 40 + "\nbuild=1\nbuild_date=2026-10-05T00:00:00Z\n"

WF = """\
cwlVersion: v1.2
class: Workflow
inputs:
  pdfs: File[]
  version: string
  workflow_id: ["null", string]
  tool_image: ["null", string]
  tool_image_digest: ["null", string]
steps:
  pack:
    in: {version: version}
    out: [archive]
    run:
      class: CommandLineTool
      requirements:
        DockerRequirement:
          dockerPull: ragstack-worker.sif
          dockerImageId: ragstack-worker.sif
      baseCommand: [true]
      inputs: {}
      outputs: {}
outputs: {}
"""


def _receipt(name: str = NAME, sha: str = DIGEST, build: str = "1") -> dict:
    return {"name": name, "version": "v9.9.9", "commit": "c" * 40, "build": build,
            "build_date": "2026-10-05T00:00:00Z", "sha256": sha}


def _write_receipt(cwl_dir: Path, **kw) -> Path:
    cwl_dir.mkdir(parents=True, exist_ok=True)
    p = cwl_dir / RECEIPT_BASENAME
    p.write_text(json.dumps(_receipt(**kw)), encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# ragstack.provenance — the worker's record
# --------------------------------------------------------------------------- #


def test_record_inside_an_image_reads_release(tmp_path: Path) -> None:
    rel = tmp_path / "RELEASE"
    rel.write_text("# comment\n\n" + RELEASE_TEXT + "junk line without equals\n")
    assert read_release(rel) == {"version": "v9.9.9", "commit": "c" * 40, "build": "1",
                                 "build_date": "2026-10-05T00:00:00Z"}
    got = tool_provenance("wf_1", NAME, DIGEST, release_path=rel)
    assert got == {"workflow_id": "wf_1", "tool_image": NAME, "tool_image_digest": DIGEST,
                   "image_version": "v9.9.9", "image_commit": "c" * 40, "image_build": "1"}
    assert tuple(got) == PROVENANCE_KEYS


def test_record_outside_an_image_has_null_release_fields(tmp_path: Path) -> None:
    got = tool_provenance("wf_1", NAME, DIGEST, release_path=tmp_path / "absent")
    assert got["image_version"] is None and got["image_commit"] is None
    assert got["image_build"] is None
    assert (got["workflow_id"], got["tool_image"], got["tool_image_digest"]) == ("wf_1", NAME, DIGEST)
    # A hand run: nothing seeded, nothing read → every field null, never "".
    assert tool_provenance(release_path=tmp_path / "absent") == unknown_provenance()
    assert tool_provenance("", "  ", None, release_path=tmp_path / "absent") == unknown_provenance()


def test_old_manifest_without_the_key_reads_as_unknown() -> None:
    old = {"format": "ragstack-archive/1", "collection_id": "c", "version": 1}
    assert read_provenance(old) == dict.fromkeys(PROVENANCE_KEYS)
    assert read_provenance({"provenance": None}) == unknown_provenance()
    assert read_provenance({"provenance": "garbage"}) == unknown_provenance()
    assert read_provenance(None) == unknown_provenance()
    # Partial / over-full objects normalise to exactly the known keys.
    got = read_provenance({"provenance": {"workflow_id": "wf_9", "extra": 1, "tool_image": ""}})
    assert got == {**unknown_provenance(), "workflow_id": "wf_9"}


def test_cli_flags_build_the_record(tmp_path: Path, monkeypatch) -> None:
    import argparse

    monkeypatch.setattr("ragstack.provenance.RELEASE_PATH", str(tmp_path / "none"))
    p = argparse.ArgumentParser()
    add_provenance_arguments(p)
    args = p.parse_args(["--workflow-id", "wf_2", "--tool-image", NAME,
                         "--tool-image-digest", DIGEST])
    got = provenance_from_args(args)
    assert got["workflow_id"] == "wf_2" and got["tool_image_digest"] == DIGEST
    assert provenance_from_args(p.parse_args([])) == unknown_provenance()


# --------------------------------------------------------------------------- #
# ragstack.tool_image — what the API seeds
# --------------------------------------------------------------------------- #


def test_tool_image_of_a_document() -> None:
    assert tool_image_of(WF) == DEFAULT_TOOL_IMAGE
    assert tool_image_of(WF.replace("ragstack-worker.sif", NAME)) == NAME
    assert tool_image_of("inputs: {}\n") is None
    mixed = WF + "\n# second tool\nx:\n  dockerPull: ragstack-tools-v1.0.0-b1.sif\n"
    assert tool_image_of(mixed) is None  # two names: record none, never pick one


def test_committed_receipt_and_digest_rules(tmp_path: Path) -> None:
    cwl_dir = tmp_path / "cwl"
    assert read_committed_receipt(cwl_dir) is None  # unstamped tree: no file
    _write_receipt(cwl_dir)
    receipt = read_committed_receipt(cwl_dir)
    assert receipt is not None and receipt["sha256"] == DIGEST
    assert tool_image_digest_for(NAME, receipt) == DIGEST
    # A receipt for another image lends nothing; nor does a malformed digest.
    assert tool_image_digest_for("ragstack-tools-v9.9.9-b2.sif", receipt) is None
    assert tool_image_digest_for(DEFAULT_TOOL_IMAGE, receipt) is None
    assert tool_image_digest_for(NAME, {**receipt, "sha256": "nope"}) is None
    assert tool_image_digest_for(None, receipt) is None
    (cwl_dir / RECEIPT_BASENAME).write_text("{not json", encoding="utf-8")
    assert read_committed_receipt(cwl_dir) is None
    (cwl_dir / RECEIPT_BASENAME).write_text("[1, 2]", encoding="utf-8")
    assert read_committed_receipt(cwl_dir) is None


def test_declared_inputs_mapping_list_and_garbage() -> None:
    assert declared_workflow_inputs(WF) >= set(PROVENANCE_INPUTS)
    listed = "class: Workflow\ninputs:\n  - id: a\n    type: string\n  - id: workflow_id\n"
    assert declared_workflow_inputs(listed) == {"a", "workflow_id"}
    assert declared_workflow_inputs("class: CommandLineTool\n") == set()
    assert declared_workflow_inputs("- [unbalanced\n: {") == set()
    assert declared_workflow_inputs("just a string") == set()


def test_provenance_inputs_unstamped_tree_seeds_nothing(tmp_path: Path) -> None:
    """THE GATE. An unstamped tree names ``ragstack-worker.sif`` — whatever
    build a worker's ``--image-dir`` resolves it to, and the deployed builds
    predate the flags (``--workflow-id`` → argparse exit 2 on every task). The
    stamp is the declaration that the image supports the workflow (ADR-0010
    decision 4), so without one nothing is seeded, the ``["null", string]``
    inputs stay null and the flags are omitted. Even a committed receipt that
    happens to name the default does not change that."""
    cwl_path = tmp_path / "cwl" / "wf.cwl"
    cwl_path.parent.mkdir()
    cwl_path.write_text(WF)
    assert tool_image_of(WF) == DEFAULT_TOOL_IMAGE
    assert provenance_inputs(WF, cwl_path, "wf_abc") == {}
    assert provenance_inputs(WF, None, "wf_abc") == {}
    _write_receipt(cwl_path.parent, name=DEFAULT_TOOL_IMAGE)
    assert provenance_inputs(WF, cwl_path, "wf_abc") == {}
    # A mixed document (two names) or one with no image site seeds nothing either.
    assert provenance_inputs(WF.replace("inputs:", "inputs:\n  x: string", 1)
                             .replace("baseCommand", "x:\n        dockerPull: ragstack-tools-v1.0.0-b1.sif\n      baseCommand"),
                             None, "wf_abc") == {}


def test_provenance_inputs_stamped_tree_with_receipt(tmp_path: Path) -> None:
    cwl_path = tmp_path / "cwl" / "wf.cwl"
    stamped = WF.replace("ragstack-worker.sif", NAME)
    _write_receipt(cwl_path.parent)
    cwl_path.write_text(stamped)
    assert provenance_inputs(stamped, cwl_path, "wf_abc") == {
        "workflow_id": "wf_abc", "tool_image": NAME, "tool_image_digest": DIGEST}
    # The receipt names another build: the digest is withheld, the name kept.
    _write_receipt(cwl_path.parent, name="ragstack-tools-v9.9.9-b2.sif")
    assert provenance_inputs(stamped, cwl_path, "wf_abc")["tool_image_digest"] is None
    assert provenance_inputs(stamped, cwl_path, "wf_abc")["tool_image"] == NAME


def test_provenance_inputs_only_for_declared_names(tmp_path: Path) -> None:
    """On a stamped tree: a workflow that declares none of the three (a
    bulk-plane CWL) gets nothing seeded; one that declares a subset gets
    that subset."""
    stamped = WF.replace("ragstack-worker.sif", NAME)
    bulk = stamped.replace("  workflow_id: [\"null\", string]\n", "") \
                  .replace("  tool_image: [\"null\", string]\n", "") \
                  .replace("  tool_image_digest: [\"null\", string]\n", "")
    assert provenance_inputs(bulk, None, "wf_1") == {}
    partial = stamped.replace("  tool_image_digest: [\"null\", string]\n", "")
    assert set(provenance_inputs(partial, None, "wf_1")) == {"workflow_id", "tool_image"}


# --------------------------------------------------------------------------- #
# the registrars seed between register and submit
# --------------------------------------------------------------------------- #


class _Client:
    def __init__(self) -> None:
        self.registered: list[tuple[str, str]] = []
        self.submitted: dict | None = None

    async def register_workflow(self, name, cwl, labels=None, **kw) -> str:
        self.registered.append((name, cwl))
        return f"wf_{len(self.registered)}"

    async def submit(self, wf_id, inputs, *, labels=None, **kw):
        assert self.registered, "submit before register"
        self.submitted = {"wf": wf_id, "inputs": dict(inputs)}
        return {"id": "sub_1"}

    async def wait(self, sub_id, **kw):
        return {"id": sub_id, "state": "FAILED"}


@pytest.mark.asyncio
async def test_gowe_backend_seeds_the_three_inputs(tmp_path: Path) -> None:
    cwl_path = tmp_path / "cwl" / "wf.cwl"
    stamped = WF.replace("ragstack-worker.sif", NAME)
    _write_receipt(cwl_path.parent)
    cwl_path.write_text(stamped)
    client = _Client()
    backend = GoWeBackend(client, stamped, shards_input_key="pdfs", cwl_path=cwl_path,
                          poll_interval=0, interactive_poll_interval=0)
    # A caller's attempt to forge the fields loses to the registered truth.
    await backend.run_submission(
        [WorkItem(item_id="i1", source="ws:///u/home/a.pdf")],
        inputs={"version": "3", "workflow_id": "wf_forged", "tool_image_digest": "f" * 64},
    )
    assert client.submitted is not None
    sub = client.submitted["inputs"]
    assert sub["workflow_id"] == client.submitted["wf"] == "wf_1"
    assert sub["tool_image"] == NAME and sub["tool_image_digest"] == DIGEST
    assert sub["version"] == "3" and len(sub["pdfs"]) == 1


@pytest.mark.asyncio
async def test_gowe_backend_unstamped_submits_the_callers_inputs_unchanged(tmp_path: Path) -> None:
    """The gate through the fake engine: an unstamped CWL → the submitted
    inputs carry NONE of the three keys, byte-identical to the caller's plus
    the scattered files. (A key present with a null value would still be
    harmless — GoWe skips nulls before any prefix — but absent is the proof.)"""
    for cwl_path in (None, tmp_path / "cwl" / "wf.cwl"):
        if cwl_path is not None:
            cwl_path.parent.mkdir()
            cwl_path.write_text(WF)
            _write_receipt(cwl_path.parent, name=DEFAULT_TOOL_IMAGE)
        client = _Client()
        backend = GoWeBackend(client, WF, shards_input_key="pdfs", cwl_path=cwl_path,
                              poll_interval=0, interactive_poll_interval=0)
        await backend.run_submission([WorkItem(item_id="i1", source="ws:///u/home/a.pdf")],
                                     inputs={"version": "1", "collection_id": "c"})
        sub = client.submitted["inputs"]  # type: ignore[index]
        assert set(sub) == {"version", "collection_id", "pdfs"}
        assert not set(sub).intersection(PROVENANCE_INPUTS)
        assert json.dumps({k: v for k, v in sub.items() if k != "pdfs"}, sort_keys=True) == \
            json.dumps({"version": "1", "collection_id": "c"}, sort_keys=True)


@pytest.mark.asyncio
async def test_gowe_backend_leaves_an_undeclaring_workflow_alone() -> None:
    bulk = "cwlVersion: v1.2\nclass: Workflow\ninputs:\n  shards: File[]\nsteps: {}\noutputs: {}\n"
    client = _Client()
    backend = GoWeBackend(client, bulk, poll_interval=0, interactive_poll_interval=0)
    await backend.run_submission([WorkItem(item_id="i1", source="/tmp/a.jsonl")])
    assert set(client.submitted["inputs"]) == {"shards"}  # type: ignore[index]


@pytest.mark.asyncio
async def test_shipped_workflows_seed_only_once_stamped(tmp_path: Path) -> None:
    """The three API-registered texts as shipped: they DECLARE the inputs, and
    the seeding follows the tree's state — nothing on the unstamped text,
    the full triple once the same text is stamped and its receipt committed."""
    from ragstack.tool_image import check_tree_state, stamp_tool_image

    for src in ("pdf-ingest-scatter.cwl", "restore-collection.cwl", "graph-extract.cwl"):
        cwl_dir = tmp_path / src.split(".")[0] / "cwl"
        cwl_dir.mkdir(parents=True)
        shutil.copy(CWL_DIR / src, cwl_dir / src)
        text = (cwl_dir / src).read_text(encoding="utf-8")
        assert set(PROVENANCE_INPUTS) <= declared_workflow_inputs(text), src
        state = check_tree_state({src: text})[0]
        got = provenance_inputs(text, cwl_dir / src, "wf_x")
        if state == "unstamped":
            assert got == {}, src
            text = stamp_tool_image(text, NAME, source=src)
            (cwl_dir / src).write_text(text, encoding="utf-8")
        else:
            pytest.skip("shipped tree is stamped; the unstamped half is moot")
        assert provenance_inputs(text, cwl_dir / src, "wf_x") == {
            "workflow_id": "wf_x", "tool_image": NAME, "tool_image_digest": None}, src
        _write_receipt(cwl_dir)
        assert provenance_inputs(text, cwl_dir / src, "wf_x") == {
            "workflow_id": "wf_x", "tool_image": NAME, "tool_image_digest": DIGEST}, src


@pytest.mark.asyncio
@pytest.mark.parametrize("stamped", [False, True], ids=["unstamped", "stamped"])
async def test_restorer_submits_provenance_only_when_stamped(tmp_path: Path, stamped: bool) -> None:
    """The restorer's own submit path: on the shipped (unstamped) text the
    inputs are exactly ``inputs_for``'s; stamped, the registered id lands on
    them with the name and the committed digest."""
    from ragstack.restore import CollectionRestorer
    from ragstack.tool_image import stamp_tool_image

    cwl_dir = tmp_path / "cwl"
    cwl_dir.mkdir()
    # The tree itself may be stamped (it is, since the v1.6.6 server release),
    # so derive the UNSTAMPED baseline instead of assuming the checkout is one.
    text = re.sub(r"(dockerPull|dockerImageId):\s*\S+", r"\1: " + DEFAULT_TOOL_IMAGE,
                  (CWL_DIR / "restore-collection.cwl").read_text(encoding="utf-8"))
    if stamped:
        text = stamp_tool_image(text, NAME)
        _write_receipt(cwl_dir)
    (cwl_dir / "restore-collection.cwl").write_text(text, encoding="utf-8")
    client = _Client()

    class _WS:
        async def list_versions(self, token, folder):
            return [(1, "ws:///u/home/.ragstack/collections/c/versions/1")]

    class _Store:
        async def set_state(self, *a, **k):
            return True

        async def update_lifecycle(self, *a, **k):
            return None

    restorer = CollectionRestorer(
        _Store(), workspace=_WS(), gowe=client, cwl_path=cwl_dir / "restore-collection.cwl",
        static_inputs={"qdrant_url": "http://127.0.0.1:1", "es_url": "http://127.0.0.1:1"},
    )
    rec = SimpleNamespace(spec=SimpleNamespace(id="c", owner="u"), spec_hash="abcd1234",
                          versions=[1])
    restorer.folder_for = lambda r: "/u/home/.ragstack/collections/c"  # type: ignore[method-assign]
    try:
        await restorer._submit(rec, "tok")
    except Exception as e:  # noqa: BLE001 — only the submission is under test
        if client.submitted is None:
            raise AssertionError(f"nothing submitted: {e!r}") from e
    sub = client.submitted["inputs"]  # type: ignore[index]
    expected = restorer.inputs_for(rec, [(1, "ws:///u/home/.ragstack/collections/c/versions/1")])  # type: ignore[arg-type]
    if stamped:
        expected.update({"workflow_id": "wf_1", "tool_image": NAME, "tool_image_digest": DIGEST})
    else:
        assert not set(sub).intersection(PROVENANCE_INPUTS)
    assert sub == expected and sub["collection_id"] == "c"


# --------------------------------------------------------------------------- #
# the pack step and the shard receipt
# --------------------------------------------------------------------------- #


def _embed(path: Path, n: int = 3, dim: int = 4) -> None:
    from ragstack.ingestion.embedding_file import SCHEMA

    lines = [json.dumps({"schema": SCHEMA, "tenant": "public", "dim": dim})]
    for i in range(n):
        lines.append(json.dumps({"id": f"c{i}", "doc_id": "d0", "content": f"p{i}",
                                 "embedding": [0.1 * i] * dim, "metadata": {},
                                 "start_char": 0, "end_char": 2}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_write_version_records_provenance_additively(tmp_path: Path) -> None:
    _embed(tmp_path / "s.emb.jsonl")
    (tmp_path / "r.json").write_text(json.dumps({"shard_id": "s", "status": "completed"}))
    prov = tool_provenance("wf_1", NAME, DIGEST, release_path=tmp_path / "none")
    m = write_version(tmp_path / "out", 1, [tmp_path / "s.emb.jsonl"], [tmp_path / "r.json"],
                      collection_id="c", tenant="public", workers=1, provenance=prov)
    assert m["provenance"] == prov
    assert read_manifest(tmp_path / "out" / "1")["provenance"] == prov
    assert read_provenance(read_manifest(tmp_path / "out" / "1")) == prov
    # Without it: no key (the pre-step-2 shape), read as unknown.
    m2 = write_version(tmp_path / "out", 2, [tmp_path / "s.emb.jsonl"], [tmp_path / "r.json"],
                       collection_id="c", tenant="public", workers=1)
    assert "provenance" not in m2
    assert read_provenance(read_manifest(tmp_path / "out" / "2")) == unknown_provenance()
    t = write_tombstone(tmp_path / "out", 3, ["d0"], collection_id="c", tenant="public",
                        provenance=prov)
    assert read_manifest(tmp_path / "out" / "3")["provenance"] == prov and t["has_tombstone"]


def test_archive_version_cli_takes_the_flags(tmp_path: Path, monkeypatch) -> None:
    import archive_version

    rel = tmp_path / "RELEASE"
    rel.write_text(RELEASE_TEXT)
    monkeypatch.setattr("ragstack.provenance.RELEASE_PATH", str(rel))
    _embed(tmp_path / "s.emb.jsonl")
    (tmp_path / "r.json").write_text(json.dumps({"shard_id": "s", "status": "completed"}))
    rc = archive_version.main([
        "--version", "5", "--collection-id", "c", "--chunks", str(tmp_path / "s.emb.jsonl"),
        "--receipt", str(tmp_path / "r.json"), "--out", str(tmp_path / "out"), "--workers", "1",
        "--workflow-id", "wf_7", "--tool-image", NAME, "--tool-image-digest", DIGEST,
    ])
    assert rc == 0
    got = read_manifest(tmp_path / "out" / "5")["provenance"]
    assert got == {"workflow_id": "wf_7", "tool_image": NAME, "tool_image_digest": DIGEST,
                   "image_version": "v9.9.9", "image_commit": "c" * 40, "image_build": "1"}
    # The flags are optional: a hand run writes the seeded fields as null.
    (tmp_path / "ids.json").write_text('["d0"]')
    rc = archive_version.main([
        "--version", "6", "--collection-id", "c", "--tombstone", str(tmp_path / "ids.json"),
        "--out", str(tmp_path / "out"),
    ])
    assert rc == 0
    got = read_manifest(tmp_path / "out" / "6")["provenance"]
    assert got["workflow_id"] is None and got["image_version"] == "v9.9.9"


def test_shard_receipt_carries_provenance_and_tolerates_its_absence() -> None:
    prov = {"workflow_id": "wf_1", "tool_image": NAME, "tool_image_digest": DIGEST,
            "image_version": None, "image_commit": None, "image_build": None}
    r = ShardReceipt("s", "public", COMPLETED, n_docs=1, provenance=prov)
    back = ShardReceipt.from_dict(json.loads(r.to_json()))
    assert back.provenance == prov and back == r
    old = {"shard_id": "s", "status": "completed"}  # pre-step-2 receipt
    assert ShardReceipt.from_dict(old).provenance is None
    assert read_provenance(old) == unknown_provenance()
    assert ShardReceipt.from_dict({**old, "provenance": "x"}).provenance is None
    assert read_provenance(json.loads(r.to_json())) == prov


# --------------------------------------------------------------------------- #
# the stamping writes the committed receipt; --check holds them together
# --------------------------------------------------------------------------- #


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    r = tmp_path / "r"
    r.mkdir()
    shutil.copytree(CWL_DIR, r / "cwl")
    return r


def _run(tree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(STAMP), "--repo", str(tree), *args],
                          capture_output=True, text=True)


def test_shipped_tree_carries_no_receipt_when_unstamped() -> None:
    from ragstack.tool_image import check_tree_state

    docs = {p.name: p.read_text(encoding="utf-8") for p in CWL_DIR.glob("*.cwl")}
    state, name, _ = check_tree_state(docs)
    receipt = read_committed_receipt(CWL_DIR)
    if state == "unstamped":
        assert receipt is None, f"{RECEIPT_BASENAME} on an unstamped tree is stale"
    else:
        assert receipt is not None and receipt.get("name") == name


def test_stamping_writes_the_committed_receipt(tree: Path, tmp_path: Path) -> None:
    rp = tmp_path / f"{NAME}.receipt.json"
    rp.write_text(json.dumps(_receipt()))
    out = _run(tree, str(rp))
    assert out.returncode == 0, out.stderr + out.stdout
    committed = tree / "cwl" / RECEIPT_BASENAME
    assert json.loads(committed.read_text()) == _receipt()
    assert f"stamped cwl/{RECEIPT_BASENAME}" in out.stdout
    assert _run(tree, "--check").returncode == 0
    # The API now reads the digest from it, for the name the tree carries.
    text = (tree / "cwl" / "pdf-ingest-scatter.cwl").read_text()
    assert provenance_inputs(text, tree / "cwl" / "pdf-ingest-scatter.cwl", "wf_1") == {
        "workflow_id": "wf_1", "tool_image": NAME, "tool_image_digest": DIGEST}
    # Re-stamping with another build refreshes it; an identical re-run is a no-op.
    rp2 = tmp_path / "b2.receipt.json"
    rp2.write_text(json.dumps(_receipt(name=stamped_image_name("v9.9.9", 2), sha="e" * 64, build="2")))
    assert _run(tree, str(rp2)).returncode == 0
    assert json.loads(committed.read_text())["sha256"] == "e" * 64
    again = _run(tree, str(rp2))
    assert again.returncode == 0 and "stamped " not in again.stdout


def test_check_refuses_a_stamped_tree_without_its_receipt(tree: Path, tmp_path: Path) -> None:
    rp = tmp_path / f"{NAME}.receipt.json"
    rp.write_text(json.dumps(_receipt()))
    assert _run(tree, str(rp)).returncode == 0
    (tree / "cwl" / RECEIPT_BASENAME).unlink()
    out = _run(tree, "--check")
    assert out.returncode == 1 and "is missing" in out.stderr
    # ...and one whose receipt names another image, or carries a bad digest.
    _write_receipt(tree / "cwl", name=stamped_image_name("v9.9.9", 2))
    out = _run(tree, "--check")
    assert out.returncode == 1 and "names 'ragstack-tools-v9.9.9-b2.sif'" in out.stderr
    _write_receipt(tree / "cwl", sha="short")
    out = _run(tree, "--check")
    assert out.returncode == 1 and "64-hex" in out.stderr
    # With the receipt given too, its digest must match the committed one.
    _write_receipt(tree / "cwl", sha="f" * 64)
    out = _run(tree, "--check", str(rp))
    assert out.returncode == 1 and "differs" in out.stderr


def test_check_refuses_a_stale_receipt_on_an_unstamped_tree(tree: Path) -> None:
    from ragstack.tool_image import check_tree_state

    docs = {p.name: p.read_text(encoding="utf-8") for p in (tree / "cwl").glob("*.cwl")}
    if check_tree_state(docs)[0] != "unstamped":
        pytest.skip("the shipped tree is stamped; the stale-receipt rule needs an unstamped one")
    _write_receipt(tree / "cwl")
    out = _run(tree, "--check")
    assert out.returncode == 1 and "stale receipt" in out.stderr


# --------------------------------------------------------------------------- #
# the shipped CWL
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("wf_name, step, tool_script", [
    ("pdf-ingest-scatter.cwl", "pack", "archive_version.py"),
    ("pdf-ingest-scatter.cwl", "ingest", "ingest_shard.py"),
    ("graph-extract.cwl", "extract", "extract_graph.py"),
    ("restore-collection.cwl", "replay", "load_embeddings.py"),
    ("pdf-ingest.cwl", "pack", "archive_version.py"),
])
def test_registered_workflows_declare_and_bind_the_inputs(wf_name, step, tool_script) -> None:
    wf = yaml.safe_load((CWL_DIR / wf_name).read_text(encoding="utf-8"))
    for name in PROVENANCE_INPUTS:
        assert wf["inputs"][name]["type"] == ["null", "string"], (wf_name, name)
        assert wf["steps"][step]["in"][name] == name, (wf_name, step, name)
        tool = wf["steps"][step]["run"]
        assert tool["baseCommand"][-1].endswith(tool_script)
        binding = tool["inputs"][name]["inputBinding"]
        assert binding["prefix"] == "--" + name.replace("_", "-"), (wf_name, step, name)
        assert tool["inputs"][name]["type"] == ["null", "string"]
    # No position collides with another binding of the same tool.
    tool = wf["steps"][step]["run"]
    positions = [i["inputBinding"]["position"] for i in tool["inputs"].values()
                 if isinstance(i, dict) and i.get("inputBinding")]
    positions += [a["position"] for a in tool.get("arguments", []) if isinstance(a, dict)]
    assert len(positions) == len(set(positions)), (wf_name, step, sorted(positions))


@pytest.mark.parametrize("tool_name", ["archive-collection.cwl", "archive-tombstone.cwl",
                                       "extract-graph.cwl"])
def test_standalone_tools_bind_the_inputs(tool_name) -> None:
    tool = yaml.safe_load((CWL_DIR / tool_name).read_text(encoding="utf-8"))
    for name in PROVENANCE_INPUTS:
        assert tool["inputs"][name]["inputBinding"]["prefix"] == "--" + name.replace("_", "-")
