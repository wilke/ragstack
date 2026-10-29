"""#821: the bulk driver seeds the provenance a stamped tree states.

``gowe_batch_ingest.py`` renders one inputs file per batch. On a stamped tree
it adds ``tool_image`` (the name the text's ``dockerPull`` carries) and
``tool_image_digest`` (the committed receipt's sha256 when it names that
image) — the API's rule (``tool_image.provenance_inputs``) — so a bulk
receipt records what an API job records. ``workflow_id`` only with
``--workflow-id``: ``gowe submit`` registers and submits in one call. An
unstamped text seeds nothing; a template that names any driver-owned key is
refused. Offline: parses files, submits nothing.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import gowe_batch_ingest as gbi  # noqa: E402

from ragstack.tool_image import (  # noqa: E402
    DEFAULT_TOOL_IMAGE,
    RECEIPT_BASENAME,
    STAMPED_IMAGE_RE,
    tool_image_of,
)

CWL_DIR = Path(__file__).resolve().parents[3] / "cwl"
JATS = CWL_DIR / "jats-ingest.cwl"
TEMPLATE = {"collection_id": "c", "qdrant_url": "http://127.0.0.1:1",
            "es_url": "http://127.0.0.1:1"}


def _render(tmp_path: Path, cwl: Path, **kw) -> dict:
    out = tmp_path / "inputs.json"
    gbi.render_inputs(dict(TEMPLATE), ["/s/shard-0000.jsonl"], str(out), cwl_path=str(cwl), **kw)
    return json.loads(out.read_text())


def test_rendered_inputs_carry_the_stamped_image_and_digest(tmp_path: Path) -> None:
    text = JATS.read_text(encoding="utf-8")
    name = tool_image_of(text)
    if name is None or not STAMPED_IMAGE_RE.match(name):
        pytest.skip("the shipped tree is unstamped")
    receipt = json.loads((CWL_DIR / RECEIPT_BASENAME).read_text())
    doc = _render(tmp_path, JATS)
    assert doc["tool_image"] == name == receipt["name"]
    assert doc["tool_image_digest"] == receipt["sha256"]
    assert "workflow_id" not in doc                  # not known before `gowe submit`
    assert doc["shards"] == [{"class": "File", "location": "/s/shard-0000.jsonl"}]
    doc = _render(tmp_path, JATS, workflow_id="wf_821")
    assert doc["workflow_id"] == "wf_821"


def test_unstamped_cwl_seeds_no_provenance(tmp_path: Path) -> None:
    text = JATS.read_text(encoding="utf-8")
    name = tool_image_of(text)
    assert name is not None
    cwl = tmp_path / "jats-ingest.cwl"          # no receipt beside it
    cwl.write_text(text.replace(name, DEFAULT_TOOL_IMAGE), encoding="utf-8")
    doc = _render(tmp_path, cwl, workflow_id="wf_821")
    assert not {"tool_image", "tool_image_digest", "workflow_id"} & set(doc)


def test_a_receipt_naming_another_image_lends_no_digest(tmp_path: Path) -> None:
    text = JATS.read_text(encoding="utf-8")
    name = tool_image_of(text)
    if name is None or not STAMPED_IMAGE_RE.match(name):
        pytest.skip("the shipped tree is unstamped")
    shutil.copy(JATS, tmp_path / "jats-ingest.cwl")
    other = json.loads((CWL_DIR / RECEIPT_BASENAME).read_text())
    other["name"] = "ragstack-tools-v9.9.9-b1.sif"
    (tmp_path / RECEIPT_BASENAME).write_text(json.dumps(other))
    doc = _render(tmp_path, tmp_path / "jats-ingest.cwl")
    assert doc["tool_image"] == name and doc["tool_image_digest"] is None


@pytest.mark.parametrize("key", ["tool_image", "tool_image_digest", "shards"])
def test_template_with_tool_image_is_refused(tmp_path: Path, key: str) -> None:
    template = {**TEMPLATE, key: "x"}
    with pytest.raises(SystemExit, match=key):
        gbi.render_inputs(template, ["/s/a.jsonl"], str(tmp_path / "i.json"),
                          cwl_path=str(JATS))
    assert not (tmp_path / "i.json").exists()
    # and the driver's main refuses it before anything is planned or submitted
    tpl = tmp_path / "t.json"
    tpl.write_text(json.dumps(template))
    with pytest.raises(SystemExit, match=key):
        gbi.main(["--plan", str(tmp_path), "--cwl", str(JATS), "--inputs-template", str(tpl),
                  "--out", str(tmp_path / "o"), "--dry-run"])


def test_the_ledger_row_names_the_glossary_counts() -> None:
    summary = {"n_chunks": 3, "glossary_file": {
        "counts": {"documents": 2, "with_llm": 1, "degraded": 1, "merged_entries": 4},
        "degraded_reasons": {"timeout": 1}, "sha256": "f" * 64}}
    assert gbi.glossary_of(summary) == {
        "counts": {"documents": 2, "with_llm": 1, "degraded": 1, "merged_entries": 4},
        "degraded_reasons": {"timeout": 1}}
    assert gbi.glossary_of({"n_chunks": 3}) is None and gbi.glossary_of(None) is None
