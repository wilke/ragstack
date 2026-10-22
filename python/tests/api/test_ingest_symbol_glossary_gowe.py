"""#783: the symbol-glossary choice travels on a GoWe submission.

``SYMBOL_GLOSSARY_EXTRACTION`` (default ``rules``) decides the in-process
ingest; on ``INGEST_BACKEND=gowe`` the work is a CWL step in a container that
cannot read the tenant's settings, so the API sends the setting as the
``symbol_glossary`` workflow input (``documents.py::_gowe_inputs``). It is sent
for BOTH values (the CWL input defaults to ``rules``, so an omitted ``off``
would be ignored), but only when the registered workflow declares the input —
the ``duplicate_policy`` precedent (ADR-0010). In-process app over the
conftest's in-memory doubles and a fake engine; no live store, no GoWe server.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ragstack.api.routers.documents import gowe_symbol_glossary
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

pytestmark = pytest.mark.asyncio

SCATTER = Path(__file__).resolve().parents[3] / "cwl" / "pdf-ingest-scatter.cwl"
DECLARING = (
    "cwlVersion: v1.2\nclass: Workflow\ninputs:\n"
    "  pdfs: File[]\n  symbol_glossary: {type: string, default: rules}\n"
    "outputs: {}\nsteps: {}\n"
)


def _last_inputs(gowe) -> dict:  # noqa: F811
    return gowe["engine"].submissions[-1]["inputs"]


@pytest.mark.parametrize("mode", ["rules", "off"])
async def test_submission_carries_the_setting(client, gowe, monkeypatch, mode) -> None:  # noqa: F811
    from ragstack.config import settings

    monkeypatch.setattr(gowe["backend"], "workflow_cwl", DECLARING)
    monkeypatch.setattr(settings, "symbol_glossary_extraction", mode)
    assert (await _upload(client, "a.pdf")).status_code == 202
    assert _last_inputs(gowe)["symbol_glossary"] == mode


async def test_default_setting_is_sent_as_rules(client, gowe, monkeypatch) -> None:  # noqa: F811
    """With nothing configured, the submission says ``rules`` — the same
    default the in-process path uses (config.Settings)."""
    from ragstack.config import Settings, settings

    default = Settings(_env_file=None).symbol_glossary_extraction  # type: ignore[call-arg]
    assert default == "rules"
    monkeypatch.setattr(settings, "symbol_glossary_extraction", default)
    monkeypatch.setattr(gowe["backend"], "workflow_cwl", DECLARING)
    assert (await _upload(client, "a.pdf")).status_code == 202
    assert _last_inputs(gowe)["symbol_glossary"] == "rules"


async def test_not_sent_when_the_registered_workflow_does_not_declare_it(
    client, gowe, monkeypatch  # noqa: F811
) -> None:
    """An older registered text (whose image may predate the flag) is submitted
    exactly as before: the fixture's bare CWL declares no such input."""
    from ragstack.config import settings

    monkeypatch.setattr(settings, "symbol_glossary_extraction", "rules")
    assert (await _upload(client, "a.pdf")).status_code == 202
    assert "symbol_glossary" not in _last_inputs(gowe)


async def test_an_unknown_mode_is_sent_as_off_never_verbatim(
    client, gowe, monkeypatch  # noqa: F811
) -> None:
    """In-process, a typo degrades to off (deps._build_glossary_attacher). Sent
    verbatim it would make the tool's argparse exit 2 and fail every task."""
    from ragstack.config import settings

    monkeypatch.setattr(gowe["backend"], "workflow_cwl", DECLARING)
    monkeypatch.setattr(settings, "symbol_glossary_extraction", "Rules")
    assert (await _upload(client, "a.pdf")).status_code == 202
    assert _last_inputs(gowe)["symbol_glossary"] == "off"


async def test_gowe_value_matches_the_in_process_reading(monkeypatch) -> None:
    """Both paths agree: the API sends ``rules`` exactly when the in-process
    attacher would be built."""
    from ragstack.api import deps

    for mode in ("rules", "off", "bogus", "", "RULES"):
        monkeypatch.setattr(deps.settings, "symbol_glossary_extraction", mode)
        on_in_process = deps._build_glossary_attacher() is not None
        assert (gowe_symbol_glossary(mode) == "rules") == on_in_process, mode


async def test_the_repo_scatter_cwl_declares_it_so_both_values_are_sent(
    client, gowe, monkeypatch  # noqa: F811
) -> None:
    from ragstack.config import settings
    from ragstack.tool_image import declared_workflow_inputs

    if not SCATTER.is_file():
        pytest.skip("no cwl/ in this checkout")
    text = SCATTER.read_text()
    assert "symbol_glossary" in declared_workflow_inputs(text)
    monkeypatch.setattr(gowe["backend"], "workflow_cwl", text)

    monkeypatch.setattr(settings, "symbol_glossary_extraction", "rules")
    assert (await _upload(client, "a.pdf")).status_code == 202
    assert _last_inputs(gowe)["symbol_glossary"] == "rules"

    await _poll(client, _last_inputs(gowe)["job_id"], AUTH)
    monkeypatch.setattr(settings, "symbol_glossary_extraction", "off")
    assert (await _upload(client, "b.pdf")).status_code == 202
    assert _last_inputs(gowe)["symbol_glossary"] == "off"
