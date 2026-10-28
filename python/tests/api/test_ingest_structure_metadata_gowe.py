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


def test_no_shipped_workflow_declares_it_until_the_image_repin() -> None:
    """ADR-0010: a CWL declares only what the image it NAMES supports (#765:
    "this lands on the same branch as the tools-image repin"). Every shipped
    workflow names ragstack-tools-v1.6.x, which has no ``--structure-metadata``;
    declaring the input now would make the API send it to that image (the text
    IS stamped) and fail every task at argparse. Flip this pin in the commit
    that repins the tools image to a build carrying the flag."""
    if not CWL_DIR.is_dir():
        pytest.skip("no cwl/ in this checkout")
    for path in sorted(CWL_DIR.glob("*.cwl")):
        text = path.read_text(encoding="utf-8")
        doc = yaml.safe_load(text)
        assert "structure_metadata" not in declared_workflow_inputs(text), path.name
        assert "--structure-metadata" not in text, path.name
        assert isinstance(doc, dict)
