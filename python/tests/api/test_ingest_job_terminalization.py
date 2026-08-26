"""#415: a job row reaches a terminal state on every exit path from the request
that minted it, at all four ingest sites.

The wedge this pins: ``single_inflight_ingest`` counts non-terminal rows for the
principal, so ONE row left ``accepted`` by an unwinding request 429s every later
ingest — into any collection — until the row goes stale six hours later. One 500
during an upload locked a tenant out of ingest for a working day.

Four sites × three exception classes (``RuntimeError``; ``HTTPException`` —
covered nowhere near the whole window before this change; ``CancelledError``,
which is a ``BaseException`` and so escapes even a wide ``except Exception``),
injected at points that were inside the window and outside every handler:

* the two ``POST /v1/ingest`` sites and the two ``POST /v1/ingest/upload``
  sites, injecting into an ``add_task`` ARGUMENT expression — those are
  evaluated inside the window, which is the part a per-site ``try`` around the
  "risky call" misses;
* plus the two regressions #415's investigation reproduced on the local upload
  path, both of which are ordinary code with nothing patched: an ``OSError``
  from ``staging_dir.mkdir`` and the ``HTTPException(400)`` from
  ``confine_to_root`` — each sitting ONE LINE above the ``try`` that was
  believed to cover them. The second is the one that proves per-site handlers
  were insufficient rather than merely too narrow in type.

And the other polarity: the happy path at all four sites leaves the row
NON-TERMINAL at response time (observed from the background worker's first
instruction, which runs after the scope exits and before anything else touches
the row). That guards the footgun the scope introduces — a call site that
forgets ``dispatched()`` would fail a job that is actually running.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

from ragstack.api import routers
from ragstack.api.main import app
from ragstack.ingestion.loaders import LoaderError
from ragstack.jobstore import ACCEPTED, FAILED, KIND_INGEST

# The `gowe` fixture (fake engine + fake Workspace + a registered collection
# owned by a bearer identity) and its request helpers, reused rather than
# rebuilt — the same import the chunk-cap tests make.
from tests.api.test_ingest_gowe_path import (  # noqa: F401 — the `gowe` fixture
    AUTH,
    SUBJECT,
    TENANT,
    _pdf,
    _upload,
    gowe,
)

pytestmark = pytest.mark.asyncio

documents = routers.documents  # the module the routes live in

WS_SOURCE = f"ws:///{SUBJECT}/home/papers/x.pdf"
#: The three exception classes, each with a way to build one.
RAISERS = [
    (RuntimeError, lambda: RuntimeError("boom")),
    (HTTPException, lambda: HTTPException(status_code=502, detail="boom")),
    (asyncio.CancelledError, lambda: asyncio.CancelledError()),
]
RAISER_IDS = [t.__name__ for t, _ in RAISERS]


@pytest.fixture
def rooted(tmp_path, monkeypatch):
    """INGEST_ROOT at an isolated tmp dir (wins over the conftest autouse
    fixture, which points at the system temp dir)."""
    from ragstack.config import settings

    monkeypatch.setattr(settings, "ingest_root", str(tmp_path))
    return tmp_path


def _explode(monkeypatch, name: str, build):
    """Make the module-level helper ``name`` raise. Every target here is
    resolved as a global when the route body runs, so this reaches the call the
    route actually makes."""
    def _boom(*_a, **_kw):
        raise build()

    monkeypatch.setattr(documents, name, _boom)


def _rows():
    return list(app.state.job_store._jobs.values())


def _the_row():
    rows = _rows()
    assert len(rows) == 1, f"expected exactly one job row, got {rows}"
    return rows[0]


async def _assert_wedge_released(tenant: str) -> None:
    """The row is terminal AND the in-flight guard it was holding is free."""
    row = _the_row()
    assert row.status == FAILED, f"row left non-terminal: {row}"
    assert await app.state.job_store.count_active(tenant, kind=KIND_INGEST) == 0


async def _expect_raise(exc_type, call):
    """Drive a request that fails. A ``RuntimeError``/``CancelledError`` escapes
    the app (ASGITransport re-raises it); an ``HTTPException`` is rendered as a
    response by FastAPI's handler. Either way the row must be terminal."""
    if exc_type is HTTPException:
        r = await call()
        assert r.status_code == 502, r.text
        return
    with pytest.raises(exc_type):
        await call()


# --------------------------------------------------------------------------- #
# S1–S4: any exception in the window leaves the row terminal
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("exc_type,build", RAISERS, ids=RAISER_IDS)
async def test_local_upload_site_terminalizes(client, rooted, monkeypatch, exc_type, build):
    """``POST /v1/ingest/upload``, local backend. Injected at
    ``get_collection_store``, an ``add_task`` argument — past the staging
    ``try``, so nothing but the scope covers it."""
    _explode(monkeypatch, "get_collection_store", build)
    await _expect_raise(exc_type, lambda: client.post(
        "/v1/ingest/upload",
        files=[("files", ("paper.pdf", _pdf(), "application/pdf"))],
    ))
    await _assert_wedge_released("default")
    assert _the_row().error == exc_type.__name__


@pytest.mark.parametrize("exc_type,build", RAISERS, ids=RAISER_IDS)
async def test_local_ingest_site_terminalizes(client, rooted, monkeypatch, exc_type, build):
    """``POST /v1/ingest``, local backend. Same injection point, same reason."""
    source = rooted / "doc.txt"
    source.write_text("hello")
    _explode(monkeypatch, "get_collection_store", build)
    await _expect_raise(exc_type, lambda: client.post(
        "/v1/ingest", json={"source": str(source)}
    ))
    await _assert_wedge_released("default")
    assert _the_row().error == exc_type.__name__


@pytest.mark.parametrize("exc_type,build", RAISERS, ids=RAISER_IDS)
async def test_gowe_upload_site_terminalizes(client, gowe, monkeypatch, exc_type, build):  # noqa: F811
    """``POST /v1/ingest/upload``, gowe backend. Injected at ``_gowe_inputs`` —
    an ``add_task`` argument, evaluated after the Workspace writes and outside
    the ``except HTTPException`` that guards only the upload call."""
    _explode(monkeypatch, "_gowe_inputs", build)
    await _expect_raise(exc_type, lambda: _upload(client, "a.pdf"))
    await _assert_wedge_released(TENANT)
    assert _the_row().error == exc_type.__name__


@pytest.mark.parametrize("exc_type,build", RAISERS, ids=RAISER_IDS)
async def test_gowe_ingest_site_terminalizes(client, gowe, monkeypatch, exc_type, build):  # noqa: F811
    """``POST /v1/ingest``, gowe backend (a Workspace reference). Same
    injection point; this site had NO handler at all."""
    _explode(monkeypatch, "_gowe_inputs", build)
    await _expect_raise(exc_type, lambda: client.post(
        "/v1/ingest", json={"source": WS_SOURCE, "collection": "lib1"}, headers=AUTH
    ))
    await _assert_wedge_released(TENANT)
    assert _the_row().error == exc_type.__name__


# --------------------------------------------------------------------------- #
# the two reproduced regressions — no patching of the code under test
# --------------------------------------------------------------------------- #


async def test_an_oserror_from_the_staging_mkdir_does_not_strand_the_row(
    client, rooted, monkeypatch,
):
    """``staging_dir.mkdir`` sits one line ABOVE the staging ``try``. Here it
    fails for the most ordinary reason there is — something is in the way of
    the per-tenant directory (ENOSPC and EACCES take the same path)."""
    blocker = rooted / "uploads" / "default"
    blocker.parent.mkdir(parents=True)
    blocker.write_text("not a directory")

    with pytest.raises(OSError):
        await client.post(
            "/v1/ingest/upload",
            files=[("files", ("paper.pdf", _pdf(), "application/pdf"))],
        )
    await _assert_wedge_released("default")


async def test_the_confine_to_root_400_does_not_strand_the_row(client, rooted, monkeypatch):
    """The one that proves a wider ``except`` was never the fix: this refusal
    IS an ``HTTPException``, the type the upload path already handled — and it
    still stranded the row, because it is raised one line above the ``try``.

    Reached the way production would: a tenant string with path separators
    (``config.py`` accepts any value, and under token auth the tenant derives
    from the credential), which relocates the staging tree outside
    ``{ingest_root}/uploads`` and is refused 400.
    """
    from ragstack.api import security

    monkeypatch.setattr(security.settings, "api_keys", ["k-1"])
    monkeypatch.setattr(security.settings, "api_key_tenants", {"k-1": "../escape"})

    r = await client.post(
        "/v1/ingest/upload",
        files=[("files", ("paper.pdf", _pdf(), "application/pdf"))],
        headers={"X-API-Key": "k-1"},
    )
    assert r.status_code == 400 and "invalid tenant" in r.json()["detail"]
    await _assert_wedge_released("../escape")
    # Nothing escaped the root either (the refusal's original purpose).
    assert not (Path(rooted).parent / "escape").exists()


async def test_after_a_failed_upload_the_next_upload_is_admitted(client, rooted, monkeypatch):
    """C5, end to end: the failure and then the retry, through the guard that
    #415 reported wedged. Before this change the second call was 429 for six
    hours."""
    real_get_collection_store = documents.get_collection_store
    _explode(monkeypatch, "get_collection_store", lambda: RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        await client.post(
            "/v1/ingest/upload",
            files=[("files", ("paper.pdf", _pdf(), "application/pdf"))],
        )
    monkeypatch.setattr(documents, "get_collection_store", real_get_collection_store)

    r = await client.post(
        "/v1/ingest/upload",
        files=[("files", ("paper.pdf", _pdf(), "application/pdf"))],
    )
    assert r.status_code == 202, r.text
    assert r.json()["status"] == ACCEPTED


# --------------------------------------------------------------------------- #
# the other polarity: the happy path must NOT fail the row
# --------------------------------------------------------------------------- #


def _worker_probe(monkeypatch, name: str) -> dict[str, object]:
    """Replace a background worker with a probe recording the row's status as
    the worker sees it — i.e. right after the scope exits and before anything
    else writes. A call site that forgot ``dispatched()`` shows up here as
    ``failed``; nothing observable later would, because the real worker then
    overwrites the row with its own terminal status."""
    seen: dict[str, object] = {}

    async def _probe(job_store, *a, **kw):
        row = await job_store.get(_the_row().job_id)
        seen["status"] = row.status if row else None
        seen["error"] = row.error if row else None

    monkeypatch.setattr(documents, name, _probe)
    return seen


async def test_happy_path_leaves_the_row_non_terminal_local_upload(client, rooted, monkeypatch):
    seen = _worker_probe(monkeypatch, "_run_ingest")
    r = await client.post(
        "/v1/ingest/upload", files=[("files", ("paper.pdf", _pdf(), "application/pdf"))]
    )
    assert r.status_code == 202, r.text
    assert seen == {"status": ACCEPTED, "error": ""}


async def test_happy_path_leaves_the_row_non_terminal_local_ingest(client, rooted, monkeypatch):
    source = rooted / "doc.txt"
    source.write_text("hello")
    seen = _worker_probe(monkeypatch, "_run_ingest")
    r = await client.post("/v1/ingest", json={"source": str(source)})
    assert r.status_code == 200, r.text
    assert seen == {"status": ACCEPTED, "error": ""}


async def test_happy_path_leaves_the_row_non_terminal_gowe_upload(client, gowe, monkeypatch):  # noqa: F811
    seen = _worker_probe(monkeypatch, "_run_gowe_ingest")
    r = await _upload(client, "a.pdf")
    assert r.status_code == 202, r.text
    assert seen == {"status": ACCEPTED, "error": ""}


async def test_happy_path_leaves_the_row_non_terminal_gowe_ingest(client, gowe, monkeypatch):  # noqa: F811
    seen = _worker_probe(monkeypatch, "_run_gowe_ingest")
    r = await client.post(
        "/v1/ingest", json={"source": WS_SOURCE, "collection": "lib1"}, headers=AUTH
    )
    assert r.status_code == 200, r.text
    assert seen == {"status": ACCEPTED, "error": ""}


# --------------------------------------------------------------------------- #
# A3: the scope must not clobber a more specific label
# --------------------------------------------------------------------------- #


async def test_a_rejected_upload_keeps_its_rejected_label_local(client, rooted, monkeypatch):
    """The staging handler marks ``error="rejected"`` and re-raises; the scope
    then sees the exception with no ``dispatched()`` and must leave the row
    alone rather than overwrite the label with a generic one. Nothing asserted
    this before, so a naive scope would have silently coarsened every rejection
    the poll endpoint reports."""
    def _reject(*_a, **_kw):
        raise HTTPException(status_code=413, detail="too big")

    monkeypatch.setattr(documents, "_stage_upload", _reject)
    r = await client.post(
        "/v1/ingest/upload", files=[("files", ("paper.pdf", _pdf(), "application/pdf"))]
    )
    assert r.status_code == 413, r.text
    row = _the_row()
    assert (row.status, row.error) == (FAILED, "rejected")


async def test_a_rejected_upload_keeps_its_rejected_label_gowe(client, gowe, monkeypatch):  # noqa: F811
    """The same label, on the branch whose handler wraps the Workspace writes."""
    async def _reject(*_a, **_kw):
        raise HTTPException(status_code=413, detail="too big")

    monkeypatch.setattr(documents, "_gowe_upload_sources", _reject)
    r = await _upload(client, "a.pdf")
    assert r.status_code == 413, r.text
    row = _the_row()
    assert (row.status, row.error) == (FAILED, "rejected")


async def test_the_loader_error_label_is_unchanged_by_the_scope(client, rooted, monkeypatch):
    """And the in-loop confine refusal (a traversal filename), which the same
    handler labels ``rejected`` — pinned so the scope's re-read is not silently
    doing nothing for the case it exists to protect."""
    def _escape(path, root):
        if str(path).endswith("paper.pdf"):
            raise LoaderError("outside")
        return path

    monkeypatch.setattr(documents, "confine_to_root", _escape)
    r = await client.post(
        "/v1/ingest/upload", files=[("files", ("paper.pdf", _pdf(), "application/pdf"))]
    )
    assert r.status_code == 400, r.text
    row = _the_row()
    assert (row.status, row.error) == (FAILED, "rejected")
