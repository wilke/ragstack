"""Contract tests for ``cwl/pdf-ingest-scatter.cwl`` (#203 — Option B, batch per task).

The workflow exists to be driven by ``GoWeBackend``: the backend hands a flat
``File[]`` to one named input, the workflow groups it into batches ITSELF (an
ExpressionTool step — the driver never pre-groups), one task chain ingests a
batch, and the per-document results come back INSIDE the ``archive``
Directory (its ``receipt.json`` array: one receipt per batch, a ``docs`` row
per document), which must be the workflow's ONLY output — GoWe post-stages
every top-level File output flat into ``output_destination`` (by basename,
overwriting) and rewrites its location to ``ws://``. Those invariants — plus
"every tool is inlined", "both docker keys are declared" and "the extract
report reaches ingest_shard" (the scanned-PDF signal) — are the ones that break
silently, so they are asserted here rather than left to a live submission.

This file also carries a **repo-wide sweep** over every ``cwl/*.cwl`` and every
``cwl/*.inputs.yml`` (bottom of the file): no input anywhere may default to a
store address, and no example job may name a live one. It lives here rather than
in its own file because this is where the CWL contract is already asserted
offline — see the #407 block above those tests for why the class needs a sweep
and not five one-line fixes.

Offline: parses the YAML, runs nothing. The end-to-end run under cwltool is
``tests/integration/test_pdf_ingest_batch_cwl.py``.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.pinned_env_support import pinned_env

yaml = pytest.importorskip("yaml")

CHECKOUT_ROOT = Path(__file__).resolve().parents[2]
CWL_PATH = Path(__file__).resolve().parents[3] / "cwl" / "pdf-ingest-scatter.cwl"
PDF_INGEST_PATH = CWL_PATH.with_name("pdf-ingest.cwl")
PDF_EXTRACT_PATH = CWL_PATH.with_name("pdf-extract.cwl")


@pytest.fixture(scope="module")
def wf() -> dict:
    if not CWL_PATH.is_file():
        pytest.skip(f"{CWL_PATH} not present (checkout without cwl/)")
    return yaml.safe_load(CWL_PATH.read_text(encoding="utf-8"))


def _steps(wf: dict) -> dict:
    return wf["steps"]


def test_flat_pdfs_input_batched_inside_the_workflow(wf: dict) -> None:
    """The driver's input stays a flat ``pdfs: File[]`` (GoWeBackend's
    ``shards_input_key`` default); the ``batch`` ExpressionTool turns it into
    ``File[][]`` by ``batch_size`` (default 20) and the task steps scatter over
    the batches."""
    assert wf["inputs"]["pdfs"]["type"] == "File[]"
    assert wf["inputs"]["batch_size"]["type"] == "int"
    assert wf["inputs"]["batch_size"]["default"] == 20
    batch = _steps(wf)["batch"]
    assert batch["run"]["class"] == "ExpressionTool"
    assert batch["in"] == {"pdfs": "pdfs", "batch_size": "batch_size"}
    assert batch["run"]["outputs"]["batches"]["type"] == {
        "type": "array", "items": {"type": "array", "items": "File"}}
    assert "InlineJavascriptRequirement" in batch["run"]["requirements"]
    assert "InlineJavascriptRequirement" in wf["requirements"]
    extract = _steps(wf)["extract"]
    assert extract["scatter"] == ["pdfs", "batch_id"] and extract["scatterMethod"] == "dotproduct"
    assert extract["in"]["pdfs"] == "batch/batches" and extract["in"]["batch_id"] == "batch/batch_ids"
    assert extract["run"]["inputs"]["pdfs"]["type"] == "File[]"


def test_archive_is_the_only_workflow_output(wf: dict) -> None:
    """Post-staging uploads EVERY top-level File output flat into the user's
    ``versions/`` folder (basename, overwrite) — so nothing but the archive
    Directory may be exposed, and the receipts must ride inside it."""
    assert list(wf["outputs"]) == ["archive"]
    archive = wf["outputs"]["archive"]
    assert archive["type"] == "Directory"
    assert archive["outputSource"] == "pack/archive"
    # The per-batch receipts feed the pack step (→ receipt.json array, batch
    # order), sourced from the scattered ingest step (not extract), so each
    # receipt reflects the actual Qdrant/ES upsert.
    pack_in = _steps(wf)["pack"]["in"]
    assert pack_in["receipt"] == "ingest/receipt"
    assert pack_in["chunks"] == "ingest/embeddings"
    assert pack_in["version"] == "version"
    ingest = _steps(wf)["ingest"]
    assert ingest["scatter"] == ["shard", "report"] and ingest["scatterMethod"] == "dotproduct"
    assert ingest["in"]["shard"] == "extract/shard"
    assert "merge" not in _steps(wf)  # a summary nobody may expose = a dead task


def test_extract_report_reaches_ingest_shard(wf: dict) -> None:
    """The scanned-PDF signal (#377 → 2b): the extract step's report — which
    names the skipped files with the constant NO_TEXT_ERROR — is an input of
    the ingest task, bound to ``--extract-report``, so the receipt's per-document
    rows carry it and a no-text PDF fails alone rather than sinking its batch."""
    ingest = _steps(wf)["ingest"]
    assert ingest["in"]["report"] == "extract/report"
    binding = ingest["run"]["inputs"]["report"]["inputBinding"]
    assert binding["prefix"] == "--extract-report"
    assert ingest["run"]["inputs"]["report"]["type"] == "File"
    # Extract names its outputs by the batch id, and ingest names the receipt's
    # shard after the shard (== the batch id) — so a receipt names its batch.
    extract = _steps(wf)["extract"]["run"]
    assert extract["outputs"]["shard"]["outputBinding"]["glob"] == "$(inputs.batch_id).jsonl"
    assert extract["outputs"]["report"]["outputBinding"]["glob"] == "$(inputs.batch_id).report.json"
    shard_id = [a for a in ingest["run"]["arguments"] if a.get("prefix") == "--shard-id"]
    assert shard_id and shard_id[0]["valueFrom"] == "$(inputs.shard.nameroot)"


def test_pdf_ingest_workflow_exposes_only_the_archive_too() -> None:
    """Same rule for the one-shard-per-run PDF workflow."""
    if not PDF_INGEST_PATH.is_file():
        pytest.skip(f"{PDF_INGEST_PATH} not present")
    wf = yaml.safe_load(PDF_INGEST_PATH.read_text(encoding="utf-8"))
    assert list(wf["outputs"]) == ["archive"]
    assert wf["outputs"]["archive"]["type"] == "Directory"
    assert wf["outputs"]["archive"]["outputSource"] == "pack/archive"


def test_receipts_come_from_ingest_shard(wf: dict) -> None:
    """The receipt is produced by ingest_shard.py (ShardReceipt), not re-implemented."""
    cmd = _steps(wf)["ingest"]["run"]["baseCommand"]
    assert cmd[-1].endswith("ingest_shard.py")


def test_inlined_extract_agrees_with_the_standalone_tool(wf: dict) -> None:
    """The inlined extract step must not drift from cwl/pdf-extract.cwl in what
    matters: command, the pdfs binding, the docker keys. Only the output naming
    differs (the batch id instead of a fixed out_name)."""
    if not PDF_EXTRACT_PATH.is_file():
        pytest.skip(f"{PDF_EXTRACT_PATH} not present")
    ref = yaml.safe_load(PDF_EXTRACT_PATH.read_text(encoding="utf-8"))
    tool = _steps(wf)["extract"]["run"]
    assert tool["baseCommand"] == ref["baseCommand"]
    assert tool["requirements"]["DockerRequirement"] == ref["requirements"]["DockerRequirement"]
    assert tool["inputs"]["pdfs"]["inputBinding"] == ref["inputs"]["pdfs"]["inputBinding"]
    assert [a["prefix"] for a in tool["arguments"]] == ["--out", "--report"]


def test_every_tool_is_inlined(wf: dict) -> None:
    """GoWeClient.register_workflow POSTs the CWL text — an external ``run:`` file
    reference cannot be resolved engine-side."""
    for name, step in _steps(wf).items():
        assert isinstance(step["run"], dict), f"step {name} uses an external run: ref"


def test_docker_requirement_declares_both_keys(wf: dict) -> None:
    """GoWe reads only ``dockerPull``; cwltool --singularity needs ``dockerImageId``.
    Neither falls back to the other (see cwl/README.md). The ExpressionTool step
    runs in the engine and declares no image."""
    for name, step in _steps(wf).items():
        if step["run"]["class"] == "ExpressionTool":
            assert "DockerRequirement" not in step["run"].get("requirements", {}), name
            continue
        docker = step["run"]["requirements"]["DockerRequirement"]
        assert docker["dockerPull"] == "ragstack-worker.sif", name
        assert docker["dockerImageId"] == "ragstack-worker.sif", name


def test_network_access_only_where_needed(wf: dict) -> None:
    """extract is local PyMuPDF I/O; ingest talks to the fleet + Qdrant/ES."""
    reqs = {n: s["run"]["requirements"] for n, s in _steps(wf).items()}
    assert "NetworkAccess" not in reqs["extract"]
    assert reqs["ingest"]["NetworkAccess"]["networkAccess"] is True


def test_max_chunks_is_an_optional_input_threaded_to_ingest_shard(wf: dict) -> None:
    """#291: the per-collection chunk cap reaches the worker as ONE optional
    workflow input (default 0 = unlimited) bound to ``ingest_shard --max-chunks``
    — the API passes it per job; a hand-driven run may omit it."""
    inp = wf["inputs"]["max_chunks"]
    assert inp["type"] == "int" and inp["default"] == 0
    ingest = _steps(wf)["ingest"]
    assert ingest["in"]["max_chunks"] == "max_chunks"
    tool_in = ingest["run"]["inputs"]["max_chunks"]
    assert tool_in["type"] == "int" and tool_in["default"] == 0
    assert tool_in["inputBinding"]["prefix"] == "--max-chunks"


# --- the store-target sweep: every CWL in the repo, not just this one -------- #
#
# #407: `qdrant_url`/`es_url` defaulted to `http://localhost:6333`/`:9200` and
# `neo4j_uri` to `bolt://localhost:7687` — which on the deployment host are the
# PRODUCTION instances. A run that omitted them wrote to production, and one
# did: a dev-tenant ingest built a collection and an index on the production
# Qdrant and Elasticsearch. That is the fourth instance of "a default that
# resolves to the wrong thing" (#363, #369, #392, #407), so these two tests
# guard the CLASS across every file in `cwl/`, not the five that were wrong.

CWL_DIR = CWL_PATH.parent
_LIVE_URL = re.compile(r"^(https?|bolt)://(localhost|127\.0\.0\.1)")
# The keys that name a store the run WRITES to. Deliberately not
# `embedding_url`: an embedding endpoint is read-only and the examples legitimately
# name the local fleet, so sweeping every URL-shaped value would force a
# placeholder there and teach the next reader to ignore this test.
_STORE_KEYS = ("qdrant_url", "es_url", "neo4j_uri")


def _defaults(node: object, path: str = "") -> list[tuple[str, object]]:
    """Every value under a ``default`` key ANYWHERE in a parsed CWL document.

    Recursive on purpose: a scan of top-level ``inputs`` misses the tool inlined
    at ``steps.load.run`` in ``graph-extract.cwl``, which carried its own
    ``bolt://localhost:7687`` — a real hit this test is required to catch."""
    found: list[tuple[str, object]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            if key == "default":
                found.append((here, value))
            found.extend(_defaults(value, here))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found.extend(_defaults(value, f"{path}[{i}]"))
    return found


def _cwl_files() -> list[Path]:
    return sorted(CWL_DIR.glob("*.cwl"))


def test_the_sweep_actually_sees_the_cwl_files() -> None:
    """Anti-vacuity guard for the parametrized sweep below.

    ``_cwl_files()`` runs at COLLECTION time: a checkout without ``cwl/`` (or a
    moved directory) would parametrize over an empty list, and a sweep that
    examines nothing passes. This fails instead, and pins the known hits'
    files so a rename can't quietly shrink the swept set."""
    names = {p.name for p in _cwl_files()}
    assert len(names) >= 10, f"only {len(names)} CWL files found under {CWL_DIR}"
    assert {
        "pdf-ingest-scatter.cwl", "pdf-ingest.cwl", "load-embeddings.cwl",
        "restore-collection.cwl", "graph-extract.cwl",
    } <= names


@pytest.mark.parametrize("cwl", _cwl_files(), ids=lambda p: p.name)
def test_no_cwl_input_defaults_to_a_localhost_address(cwl: Path) -> None:
    """No input of any workflow or tool — inlined ``steps[].run`` tools included
    — may default to a localhost address.

    An address default is a decision about WHERE a run writes, made by the file
    instead of the caller, and on this host every localhost store address is a
    production one. Required inputs make an omission refuse loudly instead."""
    doc = yaml.safe_load(cwl.read_text(encoding="utf-8"))
    offenders = [
        (where, value)
        for where, value in _defaults(doc)
        if isinstance(value, str) and _LIVE_URL.match(value)
    ]
    assert offenders == [], (
        f"{cwl.name} defaults an input to a live-looking address: {offenders}. "
        "Store/service targets must be required inputs (#407) — the caller names "
        "them, or the run refuses."
    )


@pytest.mark.parametrize("cwl", _cwl_files(), ids=lambda p: p.name)
def test_no_cwl_write_target_declares_a_default_at_all(cwl: Path) -> None:
    """A write-target input may carry NO default, whatever the value.

    The localhost sweep above is necessary but not sufficient: a default written
    as the host's own name (``http://coconut:6333``) is just as much production
    on that box and matches no loopback pattern. There is no legitimate default
    for "which store does this run write to" — the caller names it or the run
    refuses — so the honest rule is the absence of a default, not the shape of
    its value. This is the assertion that actually closes the class; the regex
    one stays because it also covers inputs these three keys do not name."""
    doc = yaml.safe_load(cwl.read_text(encoding="utf-8"))
    offenders = [
        (where, value)
        for where, value in _defaults(doc)
        if any(where.endswith(f"{key}.default") for key in _STORE_KEYS)
    ]
    assert offenders == [], (
        f"{cwl.name} gives a write target a default: {offenders}. Store targets are "
        "required inputs (#407) — delete the default and let an omission refuse."
    )


def test_no_cwl_example_job_names_a_live_store() -> None:
    """The ``*.inputs.yml`` examples must name placeholder stores.

    Not a style point: ``jats-ingest.inputs.yml`` pointed ``qdrant_url``/``es_url``
    at :24041/:24043 — the DEV TENANT's live stores — so a copy-paste hand-run
    with the example file wrote into a live tenant. Only the write-target keys
    are swept; ``embedding_url`` names read-only model endpoints and may stay
    concrete."""
    offenders = []
    for job in sorted(CWL_DIR.glob("*.inputs.yml")):
        doc = yaml.safe_load(job.read_text(encoding="utf-8")) or {}
        for key in _STORE_KEYS:
            value = doc.get(key)
            if isinstance(value, str) and _LIVE_URL.match(value):
                offenders.append((job.name, key, value))
    assert offenders == [], (
        f"example job files name live stores: {offenders}. Use an obvious "
        "placeholder (http://CHANGE-ME-QDRANT:6333) — an example that names a real "
        "instance is one hand-run away from writing to it (#407)."
    )


#: CLIs that WRITE to a vector or text store. A workflow invoking one of these
#: decides where those writes land, so it must take that decision from its
#: caller. ``embed_shard.py`` is deliberately absent: it produces an embeddings
#: file and touches no store — ``load_embeddings.py`` is the step that writes it.
#:
#: ``chunk_one.py`` (#476) is the eval harness's scatter step: it ingests a whole
#: corpus into a Qdrant collection + ES index per config and drops them again, so
#: it decides where those writes land exactly like the ingest CLIs do.
_WRITE_CLIS = ("ingest_shard.py", "load_embeddings.py", "chunk_one.py")


def _invoked_scripts(node: object) -> list[str]:
    """Every string a parsed CWL document puts ON a command line.

    ``baseCommand`` entries, plus ``arguments`` (bare strings and the
    ``valueFrom`` of dict-form arguments). The second channel is not optional
    polish: ``eval-scifact-chunking.cwl`` runs ``baseCommand: [python]`` with the
    script at ``arguments[0].valueFrom``, because CWL does not evaluate
    expressions inside ``baseCommand`` — so the script name CANNOT be moved
    there, and a ``baseCommand``-only reader is structurally blind to that whole
    workflow (#476).

    Still no false positives, and not by luck: this walks the PARSED document, so
    ``pdf-extract.cwl``'s header comment naming ``ingest_shard.py`` (to say what
    consumes its output) is gone before the walk begins — ``yaml.safe_load``
    strips comments. The other prose channel, ``doc:``, is never descended into
    because only ``baseCommand``/``arguments`` values are harvested."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "baseCommand":
                found.extend(str(v) for v in (value if isinstance(value, list) else [value]))
            elif key == "arguments":
                for arg in (value if isinstance(value, list) else [value]):
                    if isinstance(arg, str):
                        found.append(arg)
                    elif isinstance(arg, dict) and "valueFrom" in arg:
                        found.append(str(arg["valueFrom"]))
            found.extend(_invoked_scripts(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_invoked_scripts(value))
    return found


def _invokes_a_write_cli(doc: object) -> bool:
    """Does this document actually RUN a write CLI?

    Reads the command line the document builds, not the file text."""
    cmds = _invoked_scripts(doc)
    return any(cli in cmd for cmd in cmds for cli in _WRITE_CLIS)


def test_the_walker_reads_command_lines_and_not_prose() -> None:
    """Pins both halves of ``_invoked_scripts``' contract.

    A ``valueFrom``-injected script IS seen (the #476 hole: reverting to a
    ``baseCommand``-only reader makes the eval workflow invisible again), and a
    write-CLI name that appears only in prose is NOT — ``pdf-extract.cwl`` names
    ``ingest_shard.py`` in its header comment and writes to no store. The
    ``doc:`` case is asserted synthetically because the repo currently has no
    file with a write CLI in a ``doc:`` string, and that is exactly the kind of
    absence a future edit removes."""
    eval_wf = yaml.safe_load(
        (CWL_DIR / "eval-scifact-chunking.cwl").read_text(encoding="utf-8"))
    assert any("chunk_one.py" in s for s in _invoked_scripts(eval_wf))
    assert _invokes_a_write_cli(eval_wf)

    extract = yaml.safe_load(PDF_EXTRACT_PATH.read_text(encoding="utf-8"))
    assert not any("ingest_shard.py" in s for s in _invoked_scripts(extract))
    assert not _invokes_a_write_cli(extract)

    prose_only = {
        "class": "CommandLineTool",
        "doc": "the shard this makes is consumed by ingest_shard.py downstream",
        "baseCommand": ["python", "pdf_extract.py"],
        "arguments": [{"prefix": "--out", "valueFrom": "shard.jsonl"}],
    }
    assert not _invokes_a_write_cli(prose_only)


def test_the_write_cli_sweep_matches_the_workflows_that_write() -> None:
    """Anti-vacuity guard: the set below must be non-empty and must be the
    workflows that actually run a write CLI.

    Pinned by name because the interesting failure is *shrinkage* — a workflow
    that stops matching (renamed CLI, a wrapper script) silently drops out of
    the presence test below and takes its store inputs with it."""
    writers = {
        p.name for p in _cwl_files()
        if _invokes_a_write_cli(yaml.safe_load(p.read_text(encoding="utf-8")))
    }
    assert writers == {
        "eval-scifact-chunking.cwl", "ingest-bulk.cwl", "jats-ingest.cwl",
        "load-embeddings.cwl", "pdf-ingest.cwl", "pdf-ingest-scatter.cwl",
        "restore-collection.cwl",
    }, f"the set of write-path workflows changed: {sorted(writers)}"


@pytest.mark.parametrize("cwl", _cwl_files(), ids=lambda p: p.name)
def test_every_write_workflow_declares_its_store_targets(cwl: Path) -> None:
    """A workflow that runs a write CLI must DECLARE ``qdrant_url``/``es_url``
    and thread them to the step. Presence, not just the absence of a default.

    This is the hole ``ingest-bulk.cwl`` fell through (#454). The two sweeps
    above are absence-based — "no declared default is production", "no write
    target declares a default at all" — and a workflow that declares no store
    input **at all** has nothing for them to examine, so it passes vacuously
    while every worker falls through to the CLI's own default. That default was
    ``localhost:6333``/``:9200``: production on the deployment host, on a path
    that bulk-ingests a whole corpus.

    The absence tests stay; they catch a different mistake (declaring the input
    and giving it a bad default). This one catches not declaring it."""
    doc = yaml.safe_load(cwl.read_text(encoding="utf-8"))
    if not _invokes_a_write_cli(doc):
        pytest.skip(f"{cwl.name} runs no write CLI")
    inputs = doc.get("inputs") or {}
    for key in ("qdrant_url", "es_url"):
        assert key in inputs, (
            f"{cwl.name} runs a write CLI but declares no {key!r} input, so every "
            f"worker falls through to the CLI's own default — production on this "
            f"host (#454). Declare it as a required string, no default."
        )
        spec = inputs[key]
        assert "default" not in spec, f"{cwl.name}: {key} must have no default"

    threaded = [
        name for name, step in (doc.get("steps") or {}).items()
        if _invokes_a_write_cli(step.get("run", {}))
    ]
    assert threaded, f"{cwl.name}: no step found running a write CLI"
    for name in threaded:
        step = doc["steps"][name]
        step_in = step.get("in") or {}
        tool_inputs = (step.get("run") or {}).get("inputs") or {}
        for key, flag in (("qdrant_url", "--qdrant-url"), ("es_url", "--es-url")):
            assert step_in.get(key) == key, (
                f"{cwl.name}: step {name!r} runs a write CLI but does not receive "
                f"{key!r} — declaring the input without threading it is the same "
                f"hole one level down."
            )
            # And threading it is still not enough: an input the tool accepts but
            # never binds to the command line is accepted, dropped, and the worker
            # falls back to the CLI default. Valid CWL, green suite, production
            # writes — the mutation that reopened #454 during review.
            spec = tool_inputs.get(key)
            assert isinstance(spec, dict), (
                f"{cwl.name}: step {name!r}'s tool does not declare {key!r}"
            )
            binding = spec.get("inputBinding") or {}
            assert binding.get("prefix") == flag, (
                f"{cwl.name}: step {name!r} threads {key!r} but does not bind it to "
                f"{flag} — the value reaches the tool and never reaches the command, "
                f"so the worker uses the CLI's own default (#454)."
            )


def test_ingest_shard_refuses_to_run_without_store_targets() -> None:
    """``ingest_shard.py`` must REFUSE when the store URLs are absent.

    The CWL sweep above guards the workflows; this guards the layer beneath them.
    Reverting ``required=True`` on these two flags — restoring the
    ``localhost:6333`` / ``:9200`` defaults — left the entire suite green during
    review, because the nine tests that invoke this CLI all *supply* the flags and
    pass either way. Nothing asserted the refusal, so the backstop was unpinned.

    Together with the binding assertion above this closes the pair: a workflow can
    no longer accept-and-drop the URLs, and if one ever does, the CLI still exits
    rather than writing to whatever ``localhost`` happens to be."""
    script = CHECKOUT_ROOT / "scripts" / "ingest_shard.py"
    proc = subprocess.run(
        [sys.executable, str(script), "shard.jsonl", "--collection", "x"],
        capture_output=True, text=True, timeout=120,
        env=pinned_env({"PATH": os.environ.get("PATH", ""),
                        "PYTHONPATH": str(CHECKOUT_ROOT)}),
    )
    assert proc.returncode != 0, (
        "ingest_shard.py ran without --qdrant-url/--es-url; the localhost defaults "
        "are the PRODUCTION stores on the deployment host (#454)"
    )
    combined = proc.stdout + proc.stderr
    for flag in ("--qdrant-url", "--es-url"):
        assert flag in combined, f"the refusal does not name {flag}: {combined[-400:]}"


def test_chunk_one_refuses_to_run_without_store_targets() -> None:
    """The same backstop for the eval scatter step (#476).

    ``chunk_one.py`` used to inherit ``chunking_compare_7way``'s hardcoded
    ``localhost`` store constants — production on the deployment host — with no
    flag to override them, and the CWL sweep above could not see the workflow that
    runs it because the script arrives via ``arguments``/``valueFrom``.

    ``--endpoints`` is pointed at the dead port deliberately. Without it the run
    would probe the live embedding fleet; with it, an UNFIXED chunk_one also exits
    nonzero ("No live embedding endpoints"), so the returncode assertion alone
    would pass vacuously. The assertion that actually fails without the fix is
    that the refusal NAMES both store flags."""
    script = CHECKOUT_ROOT / "scripts" / "eval" / "chunk_one.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--config", "fixed_tok512",
         "--endpoints", "http://127.0.0.1:1"],
        capture_output=True, text=True, timeout=300,
        env=pinned_env({"PATH": os.environ.get("PATH", ""),
                        "PYTHONPATH": str(CHECKOUT_ROOT)}),
    )
    assert proc.returncode != 0, (
        "chunk_one.py ran without --qdrant-url/--es-url; the store constants it "
        "inherits resolve to the PRODUCTION stores on the deployment host (#476)"
    )
    combined = proc.stdout + proc.stderr
    for flag in ("--qdrant-url", "--es-url"):
        assert flag in combined, f"the refusal does not name {flag}: {combined[-400:]}"
