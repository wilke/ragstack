"""#847 G2b: the glossary step in the four shard workflows.

Offline YAML walkers (``test_pdf_ingest_scatter_cwl.py`` patterns). Each of
``pdf-ingest-scatter``, ``ingest-bulk``, ``jats-ingest`` and ``embed-bulk``
runs ``glossary_step.py`` once per shard — scattered over the shard source,
every knob a top-level input with the stated type and default, sourced
``name: name``, outputs named by the shard's nameroot — and hands the file to
the step that renders chunks: ``--glossary-file`` on ``ingest_shard`` (one
file, dotproduct with the shard) or, through ``merge_glossary``, on
``load_embeddings``; ``File[]`` on the scatter workflow's ``pack``. The
inlined copies agree with ``cwl/glossary-step.cwl``.

The end-to-end run under cwltool is
``tests/integration/test_pdf_ingest_batch_cwl.py``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

yaml = pytest.importorskip("yaml")

CWL_DIR = Path(__file__).resolve().parents[3] / "cwl"
STANDALONE = CWL_DIR / "glossary-step.cwl"

#: name -> (CWL type, default; ``None`` = nullable with no default).
KNOBS: dict[str, tuple[Any, Any]] = {
    "glossary_llm": ("string", "off"),
    "glossary_llm_endpoint": (["null", "string"], None),
    "glossary_llm_model": (["null", "string"], None),
    "glossary_llm_api_key_env": ("string", "OPENAI_API_KEY"),
    "glossary_llm_concurrency": ("int", 0),
    "glossary_llm_timeout_s": ("float", 0),
    "glossary_llm_max_tokens": ("int", 0),
    "glossary_llm_window_chars": ("int", 0),
    "glossary_llm_max_windows": ("int", 0),
    "glossary_llm_thinking": ("string", "off"),
    "glossary_llm_repair_rounds": ("int", 0),
    "glossary_llm_cutoff_retry": ("string", "off"),
    "glossary_llm_deadline_s": ("float", 0),
    "glossary_llm_min_chars": ("int", 0),
    "glossary_llm_seed": (["null", "int"], None),
    "glossary_llm_extra_body": (["null", "string"], None),
}

#: workflow -> (the glossary step's shard source, the consumer check)
WORKFLOWS = {
    "pdf-ingest-scatter.cwl": "extract/shard",
    "ingest-bulk.cwl": "shards",
    "jats-ingest.cwl": "extract/jsonl",
    "embed-bulk.cwl": "shards",
}


def _load(name: str) -> dict:
    return yaml.safe_load((CWL_DIR / name).read_text(encoding="utf-8"))


def _positions(tool: dict) -> list[int]:
    pos = [i["inputBinding"]["position"] for i in tool["inputs"].values()
           if isinstance(i, dict) and "position" in (i.get("inputBinding") or {})]
    pos += [a["position"] for a in tool.get("arguments", []) if isinstance(a, dict)]
    return pos


def _assert_unique_positions(tool: dict, where: str) -> None:
    pos = _positions(tool)
    assert len(pos) == len(set(pos)), (where, sorted(pos))


@pytest.mark.parametrize("wf_name", sorted(WORKFLOWS))
def test_glossary_step_is_scattered_and_threaded(wf_name: str) -> None:
    wf = _load(wf_name)
    step = wf["steps"]["glossary"]
    tool = step["run"]
    assert tool["baseCommand"] == ["python", "/opt/ragstack/scripts/glossary_step.py"]
    assert step["scatter"] in ("shard", ["shard"])
    assert step["in"]["shard"] == WORKFLOWS[wf_name]
    assert step["out"] == ["glossary", "summary"]
    for name, (typ, default) in KNOBS.items():
        decl = wf["inputs"][name]
        assert decl["type"] == typ, (wf_name, name)
        assert decl.get("default") == default, (wf_name, name)
        assert step["in"][name] == name, (wf_name, name)
        assert tool["inputs"][name]["type"] == typ, (wf_name, name)
        assert tool["inputs"][name].get("default") == default, (wf_name, name)
    for name in ("workflow_id", "tool_image", "tool_image_digest"):
        assert step["in"][name] == name and wf["inputs"][name]["type"] == ["null", "string"]
    # Per-shard output names (#852).
    args = {a["prefix"]: a["valueFrom"] for a in tool["arguments"]}
    assert args == {"--out": "$(inputs.shard.nameroot).glossary.jsonl",
                    "--summary": "$(inputs.shard.nameroot).glossary-summary.json"}
    assert tool["outputs"]["glossary"]["outputBinding"]["glob"] == args["--out"]
    assert tool["outputs"]["summary"]["outputBinding"]["glob"] == args["--summary"]
    assert tool["permanentFailCodes"] == [2, 3]
    assert tool["requirements"]["NetworkAccess"]["networkAccess"] is True
    _assert_unique_positions(tool, f"{wf_name}:glossary")
    assert "when" not in step


def _ingest_consumer(wf: dict, step_name: str, position: int, shard_sources: list[str]) -> None:
    step = wf["steps"][step_name]
    assert step["scatter"] == shard_sources and step["scatterMethod"] == "dotproduct"
    assert step["in"]["glossary"] == "glossary/glossary"
    tool = step["run"]
    assert tool["inputs"]["glossary"] == {
        "type": "File", "inputBinding": {"prefix": "--glossary-file", "position": position}}
    assert tool["permanentFailCodes"] == [4, 6]
    _assert_unique_positions(tool, step_name)


def test_scatter_ingest_and_pack_consume_the_glossary() -> None:
    wf = _load("pdf-ingest-scatter.cwl")
    _ingest_consumer(wf, "ingest", 29, ["shard", "report", "glossary"])
    pack = wf["steps"]["pack"]
    assert pack["in"]["glossary"] == "glossary/glossary"
    assert pack["run"]["inputs"]["glossary"] == {
        "type": "File[]", "inputBinding": {"prefix": "--glossary-file", "position": 12}}
    _assert_unique_positions(pack["run"], "pack")
    # still nothing but the archive is exposed (GoWe post-stages top-level Files)
    assert list(wf["outputs"]) == ["archive"]


def test_ingest_bulk_consumes_the_glossary() -> None:
    _ingest_consumer(_load("ingest-bulk.cwl"), "ingest_shard", 18, ["shard", "glossary"])


@pytest.mark.parametrize("wf_name", ["jats-ingest.cwl", "embed-bulk.cwl"])
def test_bulk_plane_merges_the_glossary_once(wf_name: str) -> None:
    wf = _load(wf_name)
    merge = wf["steps"]["merge_glossary"]
    assert "scatter" not in merge
    assert merge["in"] == {"files": "glossary/glossary"}
    tool = merge["run"]
    assert tool["baseCommand"] == ["python", "/opt/ragstack/scripts/glossary_file.py", "merge"]
    assert tool["inputs"]["files"] == {"type": "File[]", "inputBinding": {"position": 2}}
    assert tool["arguments"] == [{"position": 3, "prefix": "--out", "valueFrom": "glossary.jsonl"}]
    assert tool["permanentFailCodes"] == [2]
    assert "NetworkAccess" not in tool["requirements"]
    assert wf["outputs"]["glossary_file"]["outputSource"] == "merge_glossary/glossary_file"
    assert wf["outputs"]["glossary_summaries"]["outputSource"] == "glossary/summary"


def test_jats_load_renders_from_the_merged_file() -> None:
    wf = _load("jats-ingest.cwl")
    load = wf["steps"]["load"]
    assert load["in"]["glossary_file"] == "merge_glossary/glossary_file"
    assert load["run"]["inputs"]["glossary_file"] == {
        "type": "File", "inputBinding": {"prefix": "--glossary-file", "position": 13}}
    _assert_unique_positions(load["run"], "load")


def test_load_embeddings_takes_an_optional_glossary_file() -> None:
    wf = _load("load-embeddings.cwl")
    assert wf["inputs"]["glossary_file"]["type"] == ["null", "File"]
    assert "default" not in wf["inputs"]["glossary_file"]
    load = wf["steps"]["load"]
    assert load["in"]["glossary_file"] == "glossary_file"
    assert load["run"]["inputs"]["glossary_file"]["inputBinding"] == {
        "prefix": "--glossary-file", "position": 13}
    _assert_unique_positions(load["run"], "load")


@pytest.mark.parametrize("wf_name", sorted(WORKFLOWS))
def test_inlined_glossary_agrees_with_the_standalone_tool(wf_name: str) -> None:
    """GoWe registers the CWL text, so the tool is inlined; the copies must not
    drift from cwl/glossary-step.cwl (docs aside)."""
    ref = yaml.safe_load(STANDALONE.read_text(encoding="utf-8"))
    tool = _load(wf_name)["steps"]["glossary"]["run"]
    for key in ("baseCommand", "requirements", "permanentFailCodes", "arguments"):
        assert tool[key] == ref[key], (wf_name, key)
    assert tool["inputs"] == ref["inputs"], wf_name
    strip = {k: {kk: vv for kk, vv in v.items() if kk != "doc"} for k, v in ref["outputs"].items()}
    assert tool["outputs"] == strip, wf_name


def test_glossary_llm_values_are_quoted_strings() -> None:
    """An unquoted ``off``/``on`` is a YAML 1.1 boolean (the ``symbol_glossary``
    precedent): every string-valued switch must parse as the string."""
    for name in [*WORKFLOWS, "glossary-step.cwl"]:
        doc = _load(name)
        tools = [doc] if doc["class"] == "CommandLineTool" else [doc, doc["steps"]["glossary"]["run"]]
        for node in tools:
            for key in ("glossary_llm", "glossary_llm_thinking", "glossary_llm_cutoff_retry"):
                d = node["inputs"][key]["default"]
                assert d == "off" and isinstance(d, str), (name, key, d)
