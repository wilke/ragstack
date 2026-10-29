"""#849: every limit a CWL step's tool acts on is a declared input, default 0.

"A workflow carries no policy of its own": the cap a tool enforces and the
bounds of a model step are decided by whoever SUBMITS the workflow (the API
for user jobs, the operator for bulk jobs), so each one reaches the tool as a
top-level workflow input of the same name, with a default of ``0`` (= off /
unbounded / "the tool requires it when switched on") or a nullable type —
never a number written into the CWL, and never an ``arguments`` entry.

Walks every ``cwl/*.cwl``, parsed (not grepped): for a ``Workflow`` every
inlined tool's bound input whose prefix is a limit must be sourced
``name: name`` from a top-level input that defaults to 0 (or is nullable);
for a standalone ``CommandLineTool`` its own input must. Anti-vacuity: the
walk finds ``max_chunks`` on the four workflows that load chunks.

Deliberately NOT in the set: the graph extractor's ``--concurrency`` (default
8 in ``extract-graph.cwl``/``graph-extract.cwl``). It is a throughput knob of
a step that predates the rule and is outside this release; moving it to 0
changes the graph step's behaviour and belongs with that step's next change.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

yaml = pytest.importorskip("yaml")

CWL_DIR = Path(__file__).resolve().parents[3] / "cwl"

LIMIT_PREFIXES = frozenset({
    "--max-chunks", "--max-triples",
    "--llm-concurrency", "--llm-timeout-s", "--llm-max-tokens", "--llm-window-chars",
    "--llm-max-windows", "--llm-repair-rounds", "--llm-deadline-s", "--llm-min-chars",
})


def _cwl_files() -> list[Path]:
    return sorted(CWL_DIR.glob("*.cwl"))


def _type_is_nullable(t: Any) -> bool:
    return (isinstance(t, list) and "null" in t) or (isinstance(t, str) and t.endswith("?"))


def _defaults_off(decl: Any) -> bool:
    if not isinstance(decl, dict):
        return False
    if _type_is_nullable(decl.get("type")) and decl.get("default") is None:
        return True
    d = decl.get("default")
    return d is not None and not isinstance(d, bool) and d == 0


def _bound_limits(tool: dict) -> list[tuple[str, str]]:
    out = []
    for name, decl in (tool.get("inputs") or {}).items():
        if isinstance(decl, dict):
            prefix = (decl.get("inputBinding") or {}).get("prefix")
            if prefix in LIMIT_PREFIXES:
                out.append((name, prefix))
    return out


def limit_problems(doc: dict) -> tuple[list[str], set[str]]:
    """``(problems, limit input names found)`` for one parsed CWL document."""
    problems: list[str] = []
    found: set[str] = set()

    def _check_arguments(where: str, tool: dict) -> None:
        for arg in tool.get("arguments") or []:
            if isinstance(arg, dict) and arg.get("prefix") in LIMIT_PREFIXES:
                problems.append(f"{where}: {arg['prefix']} hardcoded in arguments")
            elif isinstance(arg, str) and arg.split("=")[0] in LIMIT_PREFIXES:
                problems.append(f"{where}: {arg} hardcoded in arguments")

    if doc.get("class") == "CommandLineTool":
        _check_arguments("tool", doc)
        for name, prefix in _bound_limits(doc):
            found.add(name)
            if not _defaults_off(doc["inputs"][name]):
                problems.append(f"tool input {name} ({prefix}) must default to 0 or be nullable")
        return problems, found
    wf_inputs = doc.get("inputs") or {}
    for sname, step in (doc.get("steps") or {}).items():
        tool = step.get("run")
        if not isinstance(tool, dict) or tool.get("class") != "CommandLineTool":
            continue
        _check_arguments(f"step {sname}", tool)
        for name, prefix in _bound_limits(tool):
            found.add(name)
            source = (step.get("in") or {}).get(name)
            if source != name:
                problems.append(f"step {sname}: {name} ({prefix}) is sourced {source!r}, "
                                f"not the workflow input {name!r}")
                continue
            if not _defaults_off(wf_inputs.get(name)):
                problems.append(f"workflow input {name} must default to 0 or be nullable")
            if not _defaults_off(tool["inputs"][name]) and "default" in tool["inputs"][name]:
                problems.append(f"step {sname}: tool input {name} has a non-zero default")
    return problems, found


@pytest.mark.parametrize("cwl", _cwl_files(), ids=lambda p: p.name)
def test_every_cwl_limit_is_a_declared_input_with_a_default(cwl: Path) -> None:
    problems, _ = limit_problems(yaml.safe_load(cwl.read_text(encoding="utf-8")))
    assert not problems, problems


def test_the_limit_walk_finds_max_chunks_where_chunks_are_loaded() -> None:
    found = {p.name for p in _cwl_files()
             if "max_chunks" in limit_problems(yaml.safe_load(p.read_text()))[1]}
    assert {"jats-ingest.cwl", "load-embeddings.cwl", "ingest-bulk.cwl",
            "pdf-ingest-scatter.cwl"} <= found, sorted(found)


def test_the_walk_sees_a_hardcoded_or_defaulted_limit() -> None:
    """The detector itself: a number in the CWL, an ``arguments`` entry, and a
    tool input not sourced from the workflow input each fail."""
    wf = {"class": "Workflow", "inputs": {"max_chunks": {"type": "int", "default": 50000}},
          "steps": {"s": {"in": {"max_chunks": "max_chunks"}, "run": {
              "class": "CommandLineTool",
              "inputs": {"max_chunks": {"type": "int",
                                        "inputBinding": {"prefix": "--max-chunks"}}}}}}}
    assert limit_problems(wf)[0]
    wf["inputs"]["max_chunks"]["default"] = 0
    assert not limit_problems(wf)[0]
    wf["steps"]["s"]["in"]["max_chunks"] = {"default": 7}
    assert limit_problems(wf)[0]
    wf["steps"]["s"]["in"]["max_chunks"] = "max_chunks"
    wf["steps"]["s"]["run"]["arguments"] = [{"prefix": "--llm-max-tokens", "valueFrom": "4096"}]
    assert limit_problems(wf)[0]
