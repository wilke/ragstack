"""The tool image in the CWL the API registers (ADR-0010, #655 step 1).

Two things are pinned here:

* **``GOWE_TOOL_IMAGE`` is retired.** The boot refuses when it is set (naming
  ADR-0010 and #655), and the text every runner registers — ingest,
  graph-extract, restore — is byte-identical to the file in the checkout.
  No substitution happens on the registration path any more.
* **#642's adversarial checks survive**, relocated to ``ragstack.tool_image``
  where the release-time stamping uses them: the anchored rewrite, the
  refuse-on-partial residual check, the bare-filename rule. They are what
  ``scripts/stamp_tool_image.py`` runs; ``test_cwl_tool_image_pin.py`` covers
  the stamping itself.

Stub the engine, never the text: the end-to-end tests drive the real
``make_ingest_backend`` / runner code over an ``httpx.MockTransport`` fake
engine and assert on the workflow text the engine actually received.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from ragstack.api import deps
from ragstack.ingestion.backends import make_ingest_backend
from ragstack.ingestion.gowe_client import GoWeError
from ragstack.ingestion.manifest import WorkItem
from ragstack.tool_image import (
    DEFAULT_TOOL_IMAGE,
    ToolImageError,
    _residual_image_sites,
    substitute_tool_image,
    validate_tool_image,
)

REPO = Path(__file__).resolve().parents[3]
CWL_DIR = REPO / "cwl"
PINNED = "ragstack-tools-v1.6.3+a2be96f-b1.sif"

SAMPLE = """\
# A comment that mentions dockerPull: ragstack-worker.sif must be left alone.
  # dockerPull: ragstack-worker.sif
cwlVersion: v1.2
class: Workflow
steps:
  a:
    run:
      class: CommandLineTool
      requirements:
        DockerRequirement:
          dockerPull: ragstack-worker.sif
          dockerImageId: ragstack-worker.sif
  b:
    run:
      class: CommandLineTool
      hints:
        DockerRequirement:
          dockerPull: "ragstack-worker.sif"      # GoWe reads only this
          dockerImageId: 'ragstack-worker.sif'
  c:
    run:
      class: CommandLineTool
      requirements:
        DockerRequirement:
          dockerPull: other.sif
          dockerImageId: other.sif
  d:
    doc: ragstack-worker.sif is mentioned in prose here
    run:
      class: CommandLineTool
      requirements:
        DockerRequirement:
          dockerPull: ragstack-worker.sif.bak
"""


# --- the substitution (now the stamping step's rewrite primitive) ----------- #

def test_replaces_every_default_dockerpull_and_nothing_else():
    out = substitute_tool_image(SAMPLE, PINNED)
    # Every DockerRequirement value naming the default is now the pin …
    assert out.count(f"dockerPull: {PINNED}") == 1
    assert out.count(f'dockerPull: "{PINNED}"') == 1
    assert out.count(f"dockerImageId: {PINNED}") == 1
    assert out.count(f"dockerImageId: '{PINNED}'") == 1
    # … the unrelated image, the look-alike, the comment and the prose are not.
    assert "dockerPull: other.sif" in out and "dockerImageId: other.sif" in out
    assert "dockerPull: ragstack-worker.sif.bak" in out
    assert out.splitlines()[:2] == SAMPLE.splitlines()[:2]  # both comment lines
    assert "  # dockerPull: ragstack-worker.sif\n" in out  # pins the ^ anchor
    assert "doc: ragstack-worker.sif is mentioned in prose here" in out
    assert '"ragstack-worker.sif"      # GoWe reads only this' not in out
    assert f'"{PINNED}"      # GoWe reads only this' in out
    # Only the four substituted lines differ.
    changed = [(a, b) for a, b in zip(SAMPLE.splitlines(), out.splitlines(), strict=True)
               if a != b]
    assert len(changed) == 4


@pytest.mark.parametrize("image", ["", "   ", DEFAULT_TOOL_IMAGE])
def test_empty_or_default_setting_is_byte_identical(image):
    assert substitute_tool_image(SAMPLE, image) is SAMPLE


def test_warns_when_there_is_nothing_to_substitute(caplog):
    cwl = "cwlVersion: v1.2\nrequirements:\n  DockerRequirement:\n    dockerPull: other.sif\n"
    with caplog.at_level(logging.WARNING, logger="ragstack.tool_image"):
        out = substitute_tool_image(cwl, PINNED, source="/x/wf.cwl")
    assert out == cwl
    warned = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warned) == 1
    msg = warned[0].getMessage()
    assert PINNED in msg and "/x/wf.cwl" in msg


def test_no_warning_when_it_substitutes(caplog):
    with caplog.at_level(logging.WARNING, logger="ragstack.tool_image"):
        substitute_tool_image(SAMPLE, PINNED)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# One regex-visible site plus one the regex cannot see: without the residual
# check this is HALF-substituted with no log line, and the second step runs the
# unpinned image. (fuzz case id, document, 1-based residual line)
_VISIBLE = "a:\n  DockerRequirement:\n    dockerPull: ragstack-worker.sif\n"
_HALF = [
    ("flow-mapping", _VISIBLE + "b: {dockerPull: ragstack-worker.sif}\n", 4),
    ("list-item", _VISIBLE + "b:\n  - dockerPull: ragstack-worker.sif\n", 5),
    ("value-next-line", _VISIBLE + "b:\n  dockerPull:\n    ragstack-worker.sif\n", 6),
    ("space-before-colon", _VISIBLE + "b:\n  dockerPull : ragstack-worker.sif\n", 5),
    ("capitalised-key", _VISIBLE + "b:\n  DockerPull: ragstack-worker.sif\n", 5),
    ("quoted-flow", _VISIBLE + 'b: {"dockerPull": "ragstack-worker.sif"}\n', 4),
]


@pytest.mark.parametrize(("doc", "line"), [(d, n) for _, d, n in _HALF],
                         ids=[i for i, _, _ in _HALF])
def test_half_substitution_is_refused_naming_the_residual_line(doc, line):
    with pytest.raises(ToolImageError) as info:
        substitute_tool_image(doc, PINNED, source="wf.cwl")
    msg = str(info.value)
    assert "wf.cwl" in msg
    assert f"line(s) {line} " in msg  # exactly the residual site, not line 3


def test_only_invisible_sites_is_refused_too():
    """No visible site at all but one the regex cannot see: still a pin that
    would not apply — refused, not merely the nothing-substituted warning."""
    with pytest.raises(ToolImageError, match=r"line\(s\) 1 "):
        substitute_tool_image("b: {dockerPull: ragstack-worker.sif}\n", PINNED)


def test_crlf_document_is_fully_substituted():
    doc = _VISIBLE.replace("\n", "\r\n") + "b:\r\n  dockerPull: ragstack-worker.sif\r\n"
    out = substitute_tool_image(doc, PINNED)
    assert out.count(f"dockerPull: {PINNED}\r\n") == 2
    assert DEFAULT_TOOL_IMAGE not in out


@pytest.mark.parametrize("path", sorted(CWL_DIR.glob("*.cwl")), ids=lambda p: p.name)
def test_every_shipped_cwl_image_site_is_the_one_known_token(path):
    """Guard: a CWL that names its image any other way would silently escape the
    stamping. Every dockerPull / dockerImageId line must be rewritable, and a
    file either has none (a workflow with no container step) or has them all.
    Only meaningful on an unstamped tree; the pin test covers the stamped one."""
    text = path.read_text(encoding="utf-8")
    sites = re.findall(r"^[ \t]*(?:dockerPull|dockerImageId):.*$", text, re.MULTILINE)
    if sites and DEFAULT_TOOL_IMAGE not in sites[0]:
        pytest.skip("tree is stamped; see test_cwl_tool_image_pin.py")
    out = substitute_tool_image(text, PINNED, source=path.name)  # raises on a residual
    assert _residual_image_sites(out) == []
    assert DEFAULT_TOOL_IMAGE not in "\n".join(
        re.findall(r"^[ \t]*(?:dockerPull|dockerImageId):.*$", out, re.MULTILINE)
    )
    assert out.count(PINNED) == len(sites)


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
    cwl.write_text(_HALF[0][1])
    r = CollectionRestorer(InMemoryCollectionStore(), workspace=None, gowe=None,
                           cwl_path=cwl, tool_image=PINNED)
    assert r.tool_image == ""
    assert r._cwl() == _HALF[0][1]
