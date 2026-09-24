"""``chunk_method`` is a per-workflow CWL enum, not a free string (#613).

A free ``string`` accepts anything, so an unsupported or misspelled
``chunk_method`` was only discovered *inside a worker, mid-scatter* — after
staging and extraction had already run (#609). Five workflows declare a
``chunk_method`` workflow input and forward it to a ``CommandLineTool`` step:

* ``pdf-ingest-scatter.cwl`` and ``ingest-bulk.cwl`` call ``ingest_shard.py``,
  which builds the semantic embed bridge — their enum includes
  :data:`~ragstack.ingestion.chunker_config.SEMANTIC_METHODS`.
* ``embed-bulk.cwl``, ``pdf-ingest.cwl`` and ``jats-ingest.cwl`` call
  ``embed_shard.py``, which refuses semantic methods locally (#609) — their
  enum must NOT offer a value the tool will reject.

This test pins both the enum-ness and the per-workflow symbol split against
the canonical lists in ``ragstack.ingestion.chunkers`` /
``ragstack.ingestion.chunker_config``, so a hand-kept CWL enum cannot silently
drift from the Python source of truth.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from ragstack.ingestion.chunker_config import SEMANTIC_METHODS
from ragstack.ingestion.chunkers import CHUNK_METHODS

_CWL_DIR = Path(__file__).resolve().parents[3] / "cwl"

# workflow filename -> tool script the chunk_method-carrying step runs.
# The two ingest_shard workflows accept the semantic methods; the three
# embed_shard workflows must not (#609's local refusal).
_INGEST_SHARD_WORKFLOWS = ("pdf-ingest-scatter.cwl", "ingest-bulk.cwl")
_EMBED_SHARD_WORKFLOWS = ("embed-bulk.cwl", "pdf-ingest.cwl", "jats-ingest.cwl")
_ALL_WORKFLOWS = _INGEST_SHARD_WORKFLOWS + _EMBED_SHARD_WORKFLOWS


def _load_cwl(path: Path) -> dict[str, Any]:
    with path.open() as fh:
        return yaml.safe_load(fh)


def _chunk_method_symbols(input_spec: dict[str, Any]) -> list[str]:
    """Extract the enum symbols from a ``chunk_method`` input spec, asserting
    it actually *is* an enum (not a free string)."""
    type_spec = input_spec["type"]
    assert isinstance(type_spec, dict) and type_spec.get("type") == "enum", (
        f"chunk_method type is not a CWL enum: {type_spec!r}"
    )
    symbols = type_spec["symbols"]
    assert isinstance(symbols, list) and symbols, "enum declares no symbols"
    return symbols


def _resolve_run(step: dict[str, Any], workflow_dir: Path) -> dict[str, Any]:
    """A step's ``run`` is either an inlined CommandLineTool (dict) or a path
    to one (str), resolved relative to the workflow's own directory."""
    run = step["run"]
    if isinstance(run, str):
        return _load_cwl(workflow_dir / run)
    assert isinstance(run, dict)
    return run


@pytest.fixture(scope="module", params=_ALL_WORKFLOWS)
def workflow_name(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture(scope="module")
def workflow(workflow_name: str) -> dict[str, Any]:
    return _load_cwl(_CWL_DIR / workflow_name)


def test_cwl_files_exist() -> None:
    for name in _ALL_WORKFLOWS:
        assert (_CWL_DIR / name).is_file(), f"missing {name}"


def test_chunk_method_is_enum(workflow: dict[str, Any]) -> None:
    input_spec = workflow["inputs"]["chunk_method"]
    _chunk_method_symbols(input_spec)


def test_chunk_method_symbols_subset_of_canonical(workflow: dict[str, Any]) -> None:
    input_spec = workflow["inputs"]["chunk_method"]
    symbols = _chunk_method_symbols(input_spec)
    assert set(symbols) <= set(CHUNK_METHODS), (
        f"symbols {sorted(set(symbols) - set(CHUNK_METHODS))} are not in "
        f"ragstack.ingestion.chunkers.CHUNK_METHODS"
    )


def test_default_is_in_symbols(workflow: dict[str, Any]) -> None:
    input_spec = workflow["inputs"]["chunk_method"]
    symbols = _chunk_method_symbols(input_spec)
    assert input_spec["default"] in symbols


@pytest.mark.parametrize("workflow_name", _INGEST_SHARD_WORKFLOWS)
def test_ingest_shard_workflows_include_semantic_methods(workflow_name: str) -> None:
    workflow = _load_cwl(_CWL_DIR / workflow_name)
    symbols = set(_chunk_method_symbols(workflow["inputs"]["chunk_method"]))
    assert symbols & set(SEMANTIC_METHODS) == set(SEMANTIC_METHODS), (
        f"{workflow_name} (ingest_shard.py) must offer exactly the semantic "
        f"methods {SEMANTIC_METHODS}, has {sorted(symbols)}"
    )


@pytest.mark.parametrize("workflow_name", _EMBED_SHARD_WORKFLOWS)
def test_embed_shard_workflows_exclude_semantic_methods(workflow_name: str) -> None:
    workflow = _load_cwl(_CWL_DIR / workflow_name)
    symbols = set(_chunk_method_symbols(workflow["inputs"]["chunk_method"]))
    assert not (symbols & set(SEMANTIC_METHODS)), (
        f"{workflow_name} (embed_shard.py refuses semantic methods locally, "
        f"#609) must not offer {sorted(symbols & set(SEMANTIC_METHODS))}"
    )


def test_referenced_tool_inputs_declare_compatible_chunk_method_type(
    workflow_name: str, workflow: dict[str, Any]
) -> None:
    """Every step a workflow's chunk_method flows into must declare a
    matching enum on its own CommandLineTool input — a free ``string`` there
    would reopen the mid-scatter failure the workflow-level enum closes."""
    workflow_symbols = set(_chunk_method_symbols(workflow["inputs"]["chunk_method"]))
    workflow_dir = (_CWL_DIR / workflow_name).parent

    carrying_steps = [
        (step_id, step)
        for step_id, step in workflow["steps"].items()
        if "chunk_method" in step.get("in", {})
    ]
    assert carrying_steps, f"{workflow_name} declares chunk_method but no step consumes it"

    for step_id, step in carrying_steps:
        tool = _resolve_run(step, workflow_dir)
        assert tool.get("class") == "CommandLineTool", (
            f"{workflow_name}::{step_id} run is a {tool.get('class')!r}, expected CommandLineTool"
        )
        tool_input = tool["inputs"]["chunk_method"]
        tool_symbols = set(_chunk_method_symbols(tool_input))
        assert tool_symbols == workflow_symbols, (
            f"{workflow_name}::{step_id} tool chunk_method symbols {sorted(tool_symbols)} "
            f"do not match the workflow input's {sorted(workflow_symbols)}"
        )
