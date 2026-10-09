"""The tool image in the CWL the API registers (ADR-0010, #655 step 1).

Two things are pinned here:

* **``GOWE_TOOL_IMAGE`` is retired.** The boot refuses when it is set (naming
  ADR-0010 and #655), and the text every runner registers — ingest,
  graph-extract, restore — is byte-identical to the file in the checkout.
  No substitution happens on the registration path any more. #642's
  substitution code (``substitute_tool_image``) was removed from
  ``ragstack.tool_image`` once the first stamped release (v1.6.6) shipped
  (ADR-0010 Migration step 5, 2026-10-08).
* **#642's adversarial checks survive**, in ``ragstack.tool_image`` where the
  release-time stamping uses them: the anchored rewrite, the
  refuse-on-partial residual check, the bare-filename rule. They are what
  ``scripts/stamp_tool_image.py`` runs; ``test_cwl_tool_image_pin.py`` covers
  the stamping itself, including the residual-site adversarial cases that
  used to be exercised here against the now-removed substitution.

Stub the engine, never the text: the end-to-end tests drive the real
``make_ingest_backend`` / runner code over an ``httpx.MockTransport`` fake
engine and assert on the workflow text the engine actually received.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from ragstack.api import deps
from ragstack.ingestion.backends import make_ingest_backend
from ragstack.ingestion.gowe_client import GoWeError
from ragstack.ingestion.manifest import WorkItem
from ragstack.tool_image import DEFAULT_TOOL_IMAGE, validate_tool_image

REPO = Path(__file__).resolve().parents[3]
CWL_DIR = REPO / "cwl"
PINNED = "ragstack-tools-v1.6.3+a2be96f-b1.sif"

# A document with one regex-visible dockerPull site and one residual, flow-
# mapping site the stamping rewrite cannot see (used only to exercise a
# constructor that accepts and ignores `tool_image=`; the refuse-on-partial
# behavior itself is covered against stamp_tool_image in
# test_cwl_tool_image_pin.py::test_stamp_refuses_on_partial).
_HALF_SUBSTITUTED_DOC = (
    "a:\n  DockerRequirement:\n    dockerPull: ragstack-worker.sif\n"
    "b: {dockerPull: ragstack-worker.sif}\n"
)


# --- the name rule (kept for the stamping step) ----------------------------- #

@pytest.mark.parametrize("bad", ["../x.sif", "/abs/x.sif", "x.img", "dir/x.sif",
                                 "..sif", ".hidden.sif", "x.sif/", "x\\y.sif", "x.sif.bak",
                                 "a" * 296 + ".sif"])
def test_validate_rejects_non_bare_sif_names(bad):
    with pytest.raises(ValueError, match=r"\.sif"):
        validate_tool_image(bad)


@pytest.mark.parametrize("good", ["", PINNED, DEFAULT_TOOL_IMAGE, "ragstack-tools-v1.6.4-b1.sif",
                                  "a" * 251 + ".sif"])
def test_validate_accepts_bare_sif_names(good):
    assert validate_tool_image(good) == good


# --- GOWE_TOOL_IMAGE is retired: the boot refuses it ------------------------ #

@pytest.mark.parametrize("value", [PINNED, "ragstack-worker-v1.6.3.sif", "../x.sif", "  x.sif "])
def test_boot_refuses_when_gowe_tool_image_is_set(monkeypatch, value):
    monkeypatch.setattr(deps.settings, "require_durable_backends", False)
    monkeypatch.setattr(deps.settings, "ingest_root", "")
    monkeypatch.setattr(deps.settings, "gowe_tool_image", value)
    with pytest.raises(RuntimeError, match="GOWE_TOOL_IMAGE") as info:
        deps._validate_production_settings()
    msg = str(info.value)
    assert "ADR-0010" in msg and "#655" in msg and "retired" in msg
    # Control: the same boot with the variable unset passes, so the refusal is the variable.
    monkeypatch.setattr(deps.settings, "gowe_tool_image", "")
    deps._validate_production_settings()


@pytest.mark.parametrize("value", ["", "   "])
def test_boot_accepts_an_unset_or_blank_gowe_tool_image(monkeypatch, value):
    monkeypatch.setattr(deps.settings, "require_durable_backends", False)
    monkeypatch.setattr(deps.settings, "ingest_root", "")
    monkeypatch.setattr(deps.settings, "gowe_tool_image", value)
    deps._validate_production_settings()


# --- what reaches the engine is the file, byte for byte --------------------- #

class _Engine:
    """Fake GoWe: records every registered workflow body, then refuses the
    submission so the run stops right after registration."""

    def __init__(self) -> None:
        self.registered: list[dict] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if req.method == "POST" and req.url.path == "/api/v1/workflows":
            self.registered.append(json.loads(req.content))
            return httpx.Response(201, json={"data": {"id": "wf_1"}})
        return httpx.Response(500, text="stop here")


def _gowe_settings(cwl: Path, tool_image: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        ingest_backend="gowe", ingest_concurrency=1, gowe_url="http://gowe.test",
        gowe_token="t", gowe_workflow_cwl=str(cwl), gowe_workflow_name="wf",
        gowe_workflow_inputs_json="{}", gowe_worker_group="", gowe_poll_interval=0,
        gowe_timeout=1, gowe_tool_image=tool_image,
    )


async def _register_through_backend(tool_image: str = "") -> str:
    engine = _Engine()
    async with httpx.AsyncClient(transport=httpx.MockTransport(engine)) as http:
        backend = make_ingest_backend(
            _gowe_settings(CWL_DIR / "pdf-ingest-scatter.cwl", tool_image), http=http
        )
        with pytest.raises(GoWeError):
            await backend.run_submission(  # type: ignore[attr-defined]
                [WorkItem(item_id="d0", source="/in/d0.pdf")], token="user-token"
            )
    assert len(engine.registered) == 1
    return engine.registered[0]["cwl"]


@pytest.mark.asyncio
async def test_ingest_registration_is_the_file_byte_for_byte():
    source = (CWL_DIR / "pdf-ingest-scatter.cwl").read_text(encoding="utf-8")
    assert await _register_through_backend("") == source


@pytest.mark.asyncio
async def test_backend_factory_ignores_a_stale_tool_image_setting():
    """The factory no longer reads the setting at all (the boot refuses it
    first); a caller that builds the backend directly with one set still
    registers the file as written."""
    source = (CWL_DIR / "pdf-ingest-scatter.cwl").read_text(encoding="utf-8")
    assert await _register_through_backend(PINNED) == source


def test_graph_extract_and_restore_runners_register_the_file_as_written(monkeypatch):
    """The other two workflows the API registers carry no substitution either."""
    from ragstack.collection_store import InMemoryCollectionStore

    monkeypatch.setattr(deps.settings, "gowe_tool_image", "")
    http = httpx.AsyncClient(transport=httpx.MockTransport(_Engine()))
    graph = deps._build_graph_extract_runner(None, InMemoryCollectionStore(), http)
    gate = deps._build_lifecycle_gate(InMemoryCollectionStore(), http)
    for runner in (graph, gate.restorer):
        assert runner.tool_image == ""
        assert runner._cwl() == Path(runner._cwl_path).read_text(encoding="utf-8")


def test_runner_constructors_accept_and_ignore_tool_image(tmp_path):
    """Older callers may still pass tool_image=; it is accepted and ignored,
    and the text is the file, not a half-substituted document (the residual
    refusal of #642 now lives at release time)."""
    from ragstack.collection_store import InMemoryCollectionStore
    from ragstack.restore import CollectionRestorer

    cwl = tmp_path / "wf.cwl"
    cwl.write_text(_HALF_SUBSTITUTED_DOC)
    r = CollectionRestorer(InMemoryCollectionStore(), workspace=None, gowe=None,
                           cwl_path=cwl, tool_image=PINNED)
    assert r.tool_image == ""
    assert r._cwl() == _HALF_SUBSTITUTED_DOC
