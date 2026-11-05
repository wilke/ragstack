"""#828 PR 1: ``STRUCTURE_METADATA`` on both ingest paths.

In-process, ``deps`` builds every pipeline with ``structure_metadata`` from the
setting (``on`` exactly, else off). On ``INGEST_BACKEND=gowe`` the work is a
CWL step that cannot read the tenant's settings, so the API sends the setting
as the ``structure_metadata`` workflow input — the ``symbol_glossary`` pattern
(#783), with one more gate: the registered text must also name a STAMPED tools
image (ADR-0010 decision 4, ``tool_image.names_stamped_image``), because no
deployed image takes ``--structure-metadata`` yet and an unstamped tree names
``ragstack-worker.sif``, which may be any build. In-process app over the
conftest's in-memory doubles and a fake engine; no live store, no GoWe server.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from ragstack.api.routers.documents import gowe_structure_metadata
from ragstack.ingestion.structure import structure_metadata_enabled
from ragstack.tool_image import declared_workflow_inputs, names_stamped_image
from tests.api.test_chunk_cap_status import (  # noqa: F401 — fixtures
    _cleanup,
    principals,
)
from tests.api.test_ingest_gowe_path import (  # noqa: F401 — the `gowe` fixture
    _upload,
    gowe,
)

CWL_DIR = Path(__file__).resolve().parents[3] / "cwl"

_STEP = (
    "steps:\n  ingest:\n    in: {{structure_metadata: structure_metadata}}\n    out: []\n"
    "    run:\n      class: CommandLineTool\n      requirements:\n"
    "        DockerRequirement:\n          dockerPull: {image}\n"
    "          dockerImageId: {image}\n"
    "      baseCommand: [python, /opt/ragstack/scripts/ingest_shard.py]\n"
    "      inputs:\n        structure_metadata:\n          type: ['null', string]\n"
    "          inputBinding: {{prefix: --structure-metadata}}\n      outputs: {{}}\n"
)


def _cwl(image: str, *, declare: bool = True) -> str:
    inputs = "  pdfs: File[]\n" + ("  structure_metadata: ['null', string]\n" if declare else "")
    return ("cwlVersion: v1.2\nclass: Workflow\ninputs:\n" + inputs + "outputs: {}\n"
            + _STEP.format(image=image))


STAMPED = _cwl("ragstack-tools-v1.7.0-b1.sif")
UNSTAMPED = _cwl("ragstack-worker.sif")
UNDECLARED = _cwl("ragstack-tools-v1.7.0-b1.sif", declare=False)


def _last_inputs(gowe) -> dict:  # noqa: F811
    return gowe["engine"].submissions[-1]["inputs"]


def test_the_fixtures_mean_what_they_say() -> None:
    assert names_stamped_image(STAMPED) and not names_stamped_image(UNSTAMPED)
    assert "structure_metadata" in declared_workflow_inputs(STAMPED)
    assert "structure_metadata" not in declared_workflow_inputs(UNDECLARED)


@pytest.mark.parametrize("mode,sent", [("on", "on"), ("off", "off"), ("On", "off"),
                                       ("bogus", "off")])
async def test_a_stamped_declaring_workflow_receives_the_setting(
    client, gowe, monkeypatch, mode, sent  # noqa: F811
) -> None:
    """Sent for both values, never verbatim (argparse would exit 2)."""
    from ragstack.config import settings

    monkeypatch.setattr(gowe["backend"], "workflow_cwl", STAMPED)
    monkeypatch.setattr(settings, "structure_metadata", mode)
    assert (await _upload(client, "a.pdf")).status_code == 202
    assert _last_inputs(gowe)["structure_metadata"] == sent


@pytest.mark.parametrize("cwl", [UNSTAMPED, UNDECLARED], ids=["unstamped", "undeclared"])
async def test_not_sent_to_an_unstamped_or_undeclaring_workflow(
    client, gowe, monkeypatch, cwl, caplog  # noqa: F811
) -> None:
    """MUTATION: drop the stamped-image gate → the unstamped case sends the
    input to a workflow whose image may predate the flag → fails."""
    from ragstack.config import settings

    monkeypatch.setattr(gowe["backend"], "workflow_cwl", cwl)
    monkeypatch.setattr(settings, "structure_metadata", "on")
    assert (await _upload(client, "a.pdf")).status_code == 202
    assert "structure_metadata" not in _last_inputs(gowe)
    if cwl is UNSTAMPED:
        assert "STRUCTURE_METADATA=on is not sent" in caplog.text


def test_gowe_value_matches_the_in_process_reading(monkeypatch) -> None:
    from ragstack.api import deps

    for mode in ("on", "off", "ON", "", "true"):
        monkeypatch.setattr(deps.settings, "structure_metadata", mode)
        assert (gowe_structure_metadata(mode) == "on") == structure_metadata_enabled(mode)


@pytest.mark.parametrize("mode,on", [("on", True), ("off", False), ("ON", False)])
async def test_settings_default_off_and_deps_build_the_pipeline_from_it(
    monkeypatch, mode, on
) -> None:
    from types import SimpleNamespace

    import httpx

    from ragstack.api import deps
    from ragstack.config import Settings
    from tests.api.test_ingest_collection_target import _entry

    assert Settings(_env_file=None).structure_metadata == "off"  # type: ignore[call-arg]
    monkeypatch.setattr(deps.settings, "structure_metadata", mode)
    async with httpx.AsyncClient() as http:
        state = SimpleNamespace(graph_store=None, kg_extractor=None, http_client=http,
                                job_store=None)
        assert deps.build_ingestor_for(state, _entry("fixed"))._pipeline.structure_metadata is on


#: The shipped workflows whose steps run a shard tool (``ingest_shard.py`` or
#: ``embed_shard.py``), the tools that take ``--structure-metadata``.
SHARD_TOOL_WORKFLOWS = ("embed-bulk.cwl", "ingest-bulk.cwl", "jats-ingest.cwl",
                        "pdf-ingest-scatter.cwl", "pdf-ingest.cwl")
SHARD_TOOLS = ("ingest_shard.py", "embed_shard.py")

#: The first tools tag whose shard tools take ``--structure-metadata`` AND whose
#: ``embed_shard.py`` takes ``--collection-id`` with #828 PR 4's marker reader:
#: the image the repin step R names (ragstack-tools-v1.6.23-b1).
FIRST_TOOLS_TAG_WITH_THE_FLAGS = (1, 6, 23)


def _shard_steps(doc: dict) -> dict:
    steps = doc.get("steps") or {}
    out = {}
    for sid, step in steps.items():
        run = step.get("run")
        base = run.get("baseCommand") if isinstance(run, dict) else None
        if isinstance(base, list) and any(
                isinstance(b, str) and b.rsplit("/", 1)[-1] in SHARD_TOOLS for b in base):
            out[sid] = step
    return out


def test_every_shard_tool_workflow_declares_it_and_no_other_does() -> None:
    """The repin step R (#828 §12) flipped PR 1's
    ``test_no_shipped_workflow_declares_it_until_the_image_repin``: every
    shipped workflow that runs a shard tool declares ``structure_metadata``
    like ``duplicate_policy`` (nullable, NO default — the API sends "on" or
    "off" only when the text declares it and names a stamped image; null
    leaves the flag off the argv) and threads it into EVERY shard-tool step,
    bound to ``--structure-metadata`` at a position no other input or
    argument of that tool uses. No other workflow declares or binds it.
    MUTATION: drop the input (or its step binding) from one shard workflow →
    fails."""
    if not CWL_DIR.is_dir():
        pytest.skip("no cwl/ in this checkout")
    seen = []
    for path in sorted(CWL_DIR.glob("*.cwl")):
        text = path.read_text(encoding="utf-8")
        doc = yaml.safe_load(text)
        assert isinstance(doc, dict)
        steps = _shard_steps(doc) if doc.get("class") == "Workflow" else {}
        if path.name not in SHARD_TOOL_WORKFLOWS:
            assert not steps, (path.name, sorted(steps))
            assert "structure_metadata" not in declared_workflow_inputs(text), path.name
            assert "--structure-metadata" not in text, path.name
            continue
        seen.append(path.name)
        assert names_stamped_image(text), path.name     # else the API sends nothing
        assert "structure_metadata" in declared_workflow_inputs(text), path.name
        inp = doc["inputs"]["structure_metadata"]
        assert inp["type"] == ["null", "string"] and "default" not in inp, path.name
        assert steps, path.name
        for sid, step in steps.items():
            assert step["in"].get("structure_metadata") == "structure_metadata", (path.name, sid)
            tool_inputs = step["run"]["inputs"]
            tool_in = tool_inputs["structure_metadata"]
            assert tool_in["type"] == ["null", "string"] and "default" not in tool_in
            assert tool_in["inputBinding"]["prefix"] == "--structure-metadata", (path.name, sid)
            positions = [i["inputBinding"]["position"] for i in tool_inputs.values()
                         if isinstance(i, dict) and "position" in (i.get("inputBinding") or {})]
            positions += [a["position"] for a in step["run"].get("arguments") or []
                          if isinstance(a, dict) and "position" in a]
            assert positions.count(tool_in["inputBinding"]["position"]) == 1, (path.name, sid)
    assert seen == sorted(SHARD_TOOL_WORKFLOWS)


def _tools_tag(name: str) -> tuple[int, ...]:
    from ragstack.tool_image import STAMPED_IMAGE_RE

    m = STAMPED_IMAGE_RE.match(name)
    assert m is not None, name
    core = m.group("version").lstrip("v").split("+", 1)[0]
    return tuple(int(x) for x in core.split(".")[:3])


def test_the_tree_names_a_tools_image_that_takes_the_new_flags() -> None:
    """ADR-0010 decision 4: a CWL binds a flag only once the image it NAMES
    carries it. The texts above bind ``--structure-metadata`` and (in the
    embed steps) ``--collection-id``/``--registry``, which a tools image
    older than tag v1.6.23 rejects at argparse on every task. So a STAMPED
    tree must name that image or a later one (an unstamped tree names
    ragstack-worker.sif, which the worker's ``--image-dir`` resolves, and to
    which the API sends no ``structure_metadata``).

    RESTAMP-DEPENDENT: this fails on the repin branch until
    ``scripts/stamp_tool_image.py`` has rewritten the tree to
    ragstack-tools-v1.6.23-b1 — the guard that keeps the branch from merging
    with the wiring but without the image."""
    from ragstack.tool_image import check_tree_state

    if not CWL_DIR.is_dir():
        pytest.skip("no cwl/ in this checkout")
    docs = {p.name: p.read_text(encoding="utf-8") for p in sorted(CWL_DIR.glob("*.cwl"))}
    state, name, problems = check_tree_state(docs)
    assert not problems, problems
    if state == "unstamped":
        return
    assert name is not None
    assert _tools_tag(name) >= FIRST_TOOLS_TAG_WITH_THE_FLAGS, (
        f"the tree names {name}, older than ragstack-tools-v1.6.23: it would refuse "
        "--structure-metadata/--collection-id. Restamp (scripts/stamp_tool_image.py).")
    receipt = json.loads((CWL_DIR / "tool-image.receipt.json").read_text(encoding="utf-8"))
    assert receipt["name"] == name
