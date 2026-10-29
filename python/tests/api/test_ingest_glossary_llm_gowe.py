"""#847 G2b: the glossary step's model travels on a GoWe submission.

``SYMBOL_GLOSSARY_EXTRACTION=llm`` on ``INGEST_BACKEND=gowe``: the API sends
``symbol_glossary: rules`` (``ingest_shard`` ignores the glossary file under
``off``, so ``off`` would discard the model's work — spec §0.1) and
``glossary_llm: "on"`` with the endpoint, the model, the key's NAME and the
limits (``ragstack.ingestion.glossary_inputs``). ``rules``/``off`` send
``glossary_llm: "off"`` and nothing else. A text that does not declare
``glossary_llm`` (or names no stamped image) gets exactly today's inputs. The
boot check refuses settings that cannot drive the registered text; the job
lifts the step's degraded counts into one WARNING and the exit-6 refusal into
its label. In-process: ``llm`` is ``rules`` plus one WARNING (§0.2).

In-process app over the conftest's in-memory doubles and the fake engine; no
live store, no GoWe server, no model.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from ragstack.api.main import app
from ragstack.api.routers.documents import gowe_symbol_glossary
from ragstack.ingestion.glossary_file import GLOSSARY_FILE_MISMATCH
from ragstack.ingestion.glossary_inputs import (
    GLOSSARY_LLM_DEFAULTS,
    glossary_inputs,
    glossary_settings_problems,
)
from ragstack.ingestion.gowe_backend import GoWeRun, glossary_refusal_of, glossary_summary_of
from ragstack.ingestion.manifest import ItemResult
from tests.api.test_chunk_cap_status import (  # noqa: F401 — fixtures
    _cleanup,
    principals,
)
from tests.api.test_ingest_duplicate_guard import _poll
from tests.api.test_ingest_gowe_path import (  # noqa: F401 — the `gowe` fixture
    AUTH,
    _upload,
    gowe,
)
from tests.ingestion.test_chunk_cap import _FailedEngineClient, _run_failed

pytestmark = pytest.mark.asyncio

SCATTER = Path(__file__).resolve().parents[3] / "cwl" / "pdf-ingest-scatter.cwl"
ENDPOINT = "http://glossary-model.invalid:8004"
MODEL = "org/glossary-model"


@pytest.fixture
def scatter_text() -> str:
    if not SCATTER.is_file():
        pytest.skip("no cwl/ in this checkout")
    return SCATTER.read_text(encoding="utf-8")


@pytest.fixture
def llm(monkeypatch):
    from ragstack.config import settings

    monkeypatch.setattr(settings, "symbol_glossary_extraction", "llm")
    monkeypatch.setattr(settings, "symbol_glossary_llm_endpoint", ENDPOINT)
    monkeypatch.setattr(settings, "symbol_glossary_llm_model", MODEL)
    monkeypatch.setattr(settings, "symbol_glossary_llm_api_key_env", "GLOSSARY_KEY")
    monkeypatch.setattr(settings, "symbol_glossary_llm_inputs_json", "{}")
    return settings


def _last(gowe) -> dict:  # noqa: F811
    return gowe["engine"].submissions[-1]["inputs"]


# --------------------------------------------------------------------------- #
# what the submission carries
# --------------------------------------------------------------------------- #


async def test_llm_mode_sends_symbol_glossary_rules_and_glossary_llm_on_with_knobs(
        client, gowe, llm, monkeypatch, scatter_text) -> None:  # noqa: F811
    monkeypatch.setattr(gowe["backend"], "workflow_cwl", scatter_text)
    assert (await _upload(client, "a.pdf")).status_code == 202
    inputs = _last(gowe)
    assert inputs["symbol_glossary"] == "rules"
    assert inputs["glossary_llm"] == "on"
    assert inputs["glossary_llm_endpoint"] == ENDPOINT
    assert inputs["glossary_llm_model"] == MODEL
    assert inputs["glossary_llm_api_key_env"] == "GLOSSARY_KEY"     # a NAME
    for key, value in GLOSSARY_LLM_DEFAULTS.items():
        assert inputs[key] == value, key
    assert inputs["glossary_llm_repair_rounds"] == 0                  # owner decision
    # no knob the job did not need, and never a key value
    assert "glossary_llm_seed" not in inputs and "glossary_llm_extra_body" not in inputs


@pytest.mark.parametrize("mode, sent", [("rules", "rules"), ("off", "off")])
async def test_rules_and_off_modes_send_glossary_llm_off_and_no_endpoint(
        client, gowe, llm, monkeypatch, scatter_text, mode, sent) -> None:  # noqa: F811
    monkeypatch.setattr(llm, "symbol_glossary_extraction", mode)
    monkeypatch.setattr(gowe["backend"], "workflow_cwl", scatter_text)
    assert (await _upload(client, "a.pdf")).status_code == 202
    inputs = _last(gowe)
    assert inputs["symbol_glossary"] == sent and inputs["glossary_llm"] == "off"
    assert not [k for k in inputs if k.startswith("glossary_llm_")]


async def test_undeclared_text_gets_todays_inputs(client, gowe, llm) -> None:  # noqa: F811
    """The fixture's bare text declares nothing: no glossary input at all."""
    assert (await _upload(client, "a.pdf")).status_code == 202
    assert not [k for k in _last(gowe) if k.startswith("glossary_llm")]


async def test_unstamped_text_sends_no_glossary_llm(
        client, gowe, llm, monkeypatch, scatter_text) -> None:  # noqa: F811
    from ragstack.tool_image import DEFAULT_TOOL_IMAGE, tool_image_of

    name = tool_image_of(scatter_text)
    assert name is not None
    monkeypatch.setattr(gowe["backend"], "workflow_cwl",
                        scatter_text.replace(name, DEFAULT_TOOL_IMAGE))
    assert (await _upload(client, "a.pdf")).status_code == 202
    assert not [k for k in _last(gowe) if k.startswith("glossary_llm")]


async def test_concurrency_is_split_across_batches(
        client, gowe, llm, monkeypatch, scatter_text) -> None:  # noqa: F811
    """The knob is the tenant's TOTAL documents in flight; a run of N batches
    gives each task ``max(1, total // N)``."""
    s = SimpleNamespace(symbol_glossary_llm_endpoint=ENDPOINT, symbol_glossary_llm_model=MODEL,
                        symbol_glossary_llm_api_key_env="K",
                        symbol_glossary_llm_inputs_json="{}")
    declared = set(GLOSSARY_LLM_DEFAULTS) | {"glossary_llm_endpoint", "glossary_llm_model",
                                             "glossary_llm_api_key_env"}
    # 41 items, batch 20 -> 3 batches -> 16 // 3 = 5
    assert glossary_inputs(s, declared, n_batches=3)["glossary_llm_concurrency"] == 5
    assert glossary_inputs(s, declared, n_batches=40)["glossary_llm_concurrency"] == 1
    # through the router: 5 files, batch_size 2 -> 3 batches -> 5
    monkeypatch.setattr(gowe["backend"], "workflow_cwl", scatter_text)
    monkeypatch.setitem(gowe["backend"].static_inputs, "batch_size", 2)
    assert (await _upload(client, *[f"{c}.pdf" for c in "abcde"])).status_code == 202
    assert _last(gowe)["glossary_llm_concurrency"] == 5


async def test_knob_json_overrides_defaults(
        client, gowe, llm, monkeypatch, scatter_text) -> None:  # noqa: F811
    monkeypatch.setattr(llm, "symbol_glossary_llm_inputs_json", json.dumps({
        "glossary_llm_max_tokens": 8192, "glossary_llm_min_chars": 1500,
        "glossary_llm_seed": 7, "glossary_llm_concurrency": 32,
        "glossary_llm_extra_body": '{"reasoning_effort": "low"}'}))
    monkeypatch.setattr(gowe["backend"], "workflow_cwl", scatter_text)
    assert (await _upload(client, "a.pdf")).status_code == 202
    inputs = _last(gowe)
    assert inputs["glossary_llm_max_tokens"] == 8192
    assert inputs["glossary_llm_min_chars"] == 1500 and inputs["glossary_llm_seed"] == 7
    assert inputs["glossary_llm_concurrency"] == 32               # one batch
    assert inputs["glossary_llm_extra_body"] == '{"reasoning_effort": "low"}'
    assert inputs["glossary_llm_timeout_s"] == 300.0             # untouched default


# --------------------------------------------------------------------------- #
# boot refusals (backends._make_gowe_backend)
# --------------------------------------------------------------------------- #


def _boot(tmp_path: Path, text: str, **over):
    from ragstack.ingestion.backends import make_ingest_backend

    cwl = tmp_path / "pdf-ingest-scatter.cwl"
    cwl.write_text(text, encoding="utf-8")
    base: dict[str, object] = {
        "ingest_backend": "gowe", "ingest_concurrency": 1, "gowe_url": "http://gowe.invalid:1",
        "gowe_token": "", "gowe_workflow_cwl": str(cwl), "gowe_workflow_name": "w",
        "gowe_workflow_inputs_json": "{}", "gowe_worker_group": "", "gowe_poll_interval": 1.0,
        "gowe_timeout": 1.0, "symbol_glossary_extraction": "llm",
        "symbol_glossary_llm_endpoint": ENDPOINT, "symbol_glossary_llm_model": MODEL,
        "symbol_glossary_llm_api_key_env": "OPENAI_API_KEY",
        "symbol_glossary_llm_inputs_json": "{}",
    }
    base.update(over)
    return make_ingest_backend(SimpleNamespace(**base))


async def test_the_shipped_scatter_text_boots_in_llm_mode(tmp_path, scatter_text) -> None:
    assert _boot(tmp_path, scatter_text) is not None


@pytest.mark.parametrize("blob, says", [
    ('{"glossary_llm_bogus": 1}', "unknown key"),
    ('{"glossary_llm_max_tokens": "4096"}', "must be int"),
    ('{"glossary_llm_thinking": "maybe"}', "must be one of"),
    ('{"glossary_llm_window_chars": 1000}', "window overlap"),
    ('{"glossary_llm_timeout_s": 0}', "must be > 0"),
    ('{"glossary_llm_extra_body": "{\\"api_key\\": \\"x\\"}"}', "glossary_llm_extra_body"),
    ('[1]', "must be a JSON object"),
    ('{"glossary_llm_endpoint": "http://x:1"}', "may not set glossary_llm_endpoint"),
])
async def test_unknown_knob_key_refuses_boot(tmp_path, scatter_text, blob, says) -> None:
    with pytest.raises(ValueError, match="SYMBOL_GLOSSARY_EXTRACTION=llm") as e:
        _boot(tmp_path, scatter_text, symbol_glossary_llm_inputs_json=blob)
    assert says in str(e.value)


@pytest.mark.parametrize("over, says", [
    ({"symbol_glossary_llm_endpoint": ""}, "SYMBOL_GLOSSARY_LLM_ENDPOINT is required"),
    ({"symbol_glossary_llm_model": ""}, "SYMBOL_GLOSSARY_LLM_MODEL is required"),
    ({"symbol_glossary_llm_endpoint": "http://u:p@h:1"}, "must not carry credentials"),
    ({"symbol_glossary_llm_endpoint": "http://h:1/?key=x"}, "must not carry credentials"),
    ({"symbol_glossary_llm_endpoint": "h:1"}, "http(s) URL"),
    ({"symbol_glossary_llm_api_key_env": "sk-123"}, "variable NAME"),
])
async def test_llm_without_endpoint_or_model_refuses_boot(tmp_path, scatter_text, over, says) -> None:
    with pytest.raises(ValueError) as e:
        _boot(tmp_path, scatter_text, **over)
    assert says in str(e.value)


async def test_llm_on_an_undeclaring_cwl_refuses_boot(tmp_path, scatter_text) -> None:
    from ragstack.tool_image import DEFAULT_TOOL_IMAGE, tool_image_of

    bare = "cwlVersion: v1.2\nclass: Workflow\ninputs:\n  pdfs: File[]\noutputs: {}\nsteps: {}\n"
    with pytest.raises(ValueError, match="declares no `glossary_llm` input"):
        _boot(tmp_path, bare)
    name = tool_image_of(scatter_text)
    assert name is not None
    with pytest.raises(ValueError, match="names no stamped tools image"):
        _boot(tmp_path, scatter_text.replace(name, DEFAULT_TOOL_IMAGE))
    # rules mode boots on either: the gate is only for llm
    assert _boot(tmp_path, bare, symbol_glossary_extraction="rules") is not None


@pytest.mark.parametrize("key", ["glossary_llm", "glossary_llm_max_tokens",
                                 "glossary_llm_endpoint", "glossary_llm_api_key_env"])
async def test_reserved_glossary_keys_in_workflow_inputs_json_refuse_boot(
        tmp_path, scatter_text, key) -> None:
    with pytest.raises(ValueError, match=f"may not set {key}"):
        _boot(tmp_path, scatter_text, symbol_glossary_extraction="rules",
              gowe_workflow_inputs_json=json.dumps({key: 1}))


async def test_settings_problems_are_empty_outside_llm_mode() -> None:
    s = SimpleNamespace(symbol_glossary_extraction="rules", symbol_glossary_llm_endpoint="",
                        symbol_glossary_llm_model="", symbol_glossary_llm_api_key_env="",
                        symbol_glossary_llm_inputs_json="{not json")
    assert glossary_settings_problems(s, set()) == []


# --------------------------------------------------------------------------- #
# the in-process reading (§0.1, §0.2)
# --------------------------------------------------------------------------- #


async def test_gowe_value_matches_the_in_process_reading_for_llm(monkeypatch) -> None:
    from ragstack.api import deps

    monkeypatch.setattr(deps.settings, "symbol_glossary_extraction", "llm")
    assert gowe_symbol_glossary("llm") == "rules"
    assert deps._build_glossary_attacher() is not None


async def test_local_backend_llm_is_rules_with_a_warning(monkeypatch, caplog) -> None:
    from ragstack.api import deps

    monkeypatch.setattr(deps.settings, "symbol_glossary_extraction", "llm")
    monkeypatch.setattr(deps.settings, "ingest_backend", "local")
    monkeypatch.setattr(deps.settings, "symbol_glossary_llm_endpoint", ENDPOINT)
    with caplog.at_level(logging.INFO, logger=deps.log.name):
        attacher = deps._build_glossary_attacher()
        deps._build_glossary_attacher()          # per collection: no second warning
        deps._log_glossary_config()
    assert attacher is not None                  # rules, not off
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "only as a workflow step" in warnings[0].getMessage()
    assert "disabled" not in caplog.text


async def test_startup_log_names_llm_mode(monkeypatch, caplog) -> None:
    from ragstack.api import deps

    monkeypatch.setattr(deps.settings, "symbol_glossary_extraction", "llm")
    monkeypatch.setattr(deps.settings, "ingest_backend", "gowe")
    monkeypatch.setattr(deps.settings, "symbol_glossary_llm_endpoint",
                        "https://glossary-model.invalid/v1")
    monkeypatch.setattr(deps.settings, "symbol_glossary_llm_model", MODEL)
    monkeypatch.setattr(deps.settings, "symbol_glossary_llm_api_key_env", "GLOSSARY_KEY")
    monkeypatch.setattr(deps.settings, "symbol_glossary_llm_inputs_json",
                        '{"glossary_llm_max_tokens": 8192}')
    with caplog.at_level(logging.INFO, logger=deps.log.name):
        deps._log_glossary_config()
    text = caplog.text
    assert "extraction=llm" in text and "unknown mode" not in text
    assert "endpoint=glossary-model.invalid:443" in text and f"model={MODEL}" in text
    assert "api_key_env=GLOSSARY_KEY" in text and "knobs=SYMBOL_GLOSSARY_LLM_INPUTS_JSON" in text
    assert "/v1" not in text
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


# --------------------------------------------------------------------------- #
# lifting the step's outcome onto the job
# --------------------------------------------------------------------------- #


async def test_glossary_refusal_of_classifies_exit_6_and_lifts_the_label() -> None:
    line = "glossary_file_mismatch: missing=1 (e.g. 'doc-a')"
    run = await _run_failed(_FailedEngineClient(6, "INFO: reading\n" + line + "\n"))
    assert {r.error for r in run.results} == {line}
    record = {"code": "TASK_FAILED", "message": "step 'ingest' task failed with exit code -1",
              "context": {"exit_code": -1,
                          "stderr": "execute: apptainer command failed (exit 6): " + line}}
    assert glossary_refusal_of({"state": "FAILED", "error": record}) == line
    # the bare label when the line is outside the window; the exit code decides
    assert glossary_refusal_of({"error": {"context": {"exit_code": 6, "stderr": "x"}}}) \
        == GLOSSARY_FILE_MISMATCH
    assert glossary_refusal_of({"error": {"context": {"exit_code": 1, "stderr": line}}}) is None
    assert glossary_refusal_of({}) is None


async def test_a_glossary_refusal_becomes_the_job_label(
        client, gowe, monkeypatch) -> None:  # noqa: F811
    async def refused(items, **kw):
        return GoWeRun(results=[ItemResult(item_id=i.item_id, source=i.source, status="failed",
                                           error=f"{GLOSSARY_FILE_MISMATCH}: missing=1")
                                for i in items], state="FAILED")

    monkeypatch.setattr(gowe["backend"], "run_submission", refused)
    r = await _upload(client, "a.pdf")
    body = await _poll(client, r.json()["job_id"], AUTH)
    assert body["status"] == "failed"
    job = await app.state.job_store.get(r.json()["job_id"])
    assert job.error == GLOSSARY_FILE_MISMATCH


def _glossary_receipts(engine, reasons_per_batch):
    real = engine.receipts_for

    def with_glossary(version):
        rs = real(version)
        for r, reasons in zip(rs, reasons_per_batch, strict=False):
            n = 1
            r["enrichment"] = {"symbol_glossary": {
                "source": "glossary_file",
                "counts": {"documents": n, "with_llm": n - sum(reasons.values()),
                           "degraded": sum(reasons.values()), "merged_entries": 2},
                "degraded_reasons": reasons,
                "glossary_versions": ["rules/3"] if reasons else ["llm/2:m:h"]}}
        return rs
    engine.receipts_for = with_glossary


async def test_degraded_counts_become_a_job_warning(
        client, gowe, llm, caplog) -> None:  # noqa: F811
    _glossary_receipts(gowe["engine"], [{"timeout": 1}, {"endpoint_unavailable": 1}])
    with caplog.at_level(logging.WARNING):
        r = await _upload(client, "a.pdf", "b.pdf")
        body = await _poll(client, r.json()["job_id"], AUTH)
    assert body["status"] == "completed"          # degraded is not failed
    lines = [rec.getMessage() for rec in caplog.records
             if "symbol glossary DEGRADED" in rec.getMessage()]
    assert len(lines) == 1, lines
    assert "2 of 2 document(s)" in lines[0]
    assert "endpoint_unavailable=1, timeout=1" in lines[0]
    assert "glossary-model.invalid:8004" in lines[0]


async def test_glossary_summary_of_sums_the_receipts() -> None:
    entries = [
        {"enrichment": {"symbol_glossary": {
            "source": "glossary_file", "counts": {"documents": 2, "with_llm": 1, "degraded": 1},
            "degraded_reasons": {"timeout": 1}, "glossary_versions": ["llm/2:a", "rules/3"]}}},
        {"enrichment": {"symbol_glossary": {
            "source": "glossary_file", "counts": {"documents": 3, "with_llm": 1, "degraded": 2},
            "degraded_reasons": {"timeout": 1, "http_500": 1}, "glossary_versions": ["rules/3"]}}},
        {"enrichment": {"symbol_glossary": {"extractor": "rules/3"}}},   # in-process record
        {"status": "completed"},
    ]
    g = glossary_summary_of(entries)
    assert g == {"counts": {"documents": 5, "with_llm": 2, "degraded": 3}, "degraded": 3,
                 "degraded_reasons": {"http_500": 1, "timeout": 2},
                 "glossary_versions": ["llm/2:a", "rules/3"], "batches": 2,
                 "batches_without_glossary": 2}
    assert glossary_summary_of(entries[2:]) is None
