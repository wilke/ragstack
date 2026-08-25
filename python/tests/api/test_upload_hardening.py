"""``POST /v1/ingest/upload`` hardening — #202 phase 2c.

Both upload branches (``ingest_backend=local`` staging to INGEST_ROOT, and
``gowe`` writing to the caller's Workspace through the fakes of
``test_ingest_gowe_path``) are gated by the same helpers, so each bound is
pinned on both:

* content-type allowlist (``upload_content_types``) → 415; PDF magic → 415;
* ``max_document_bytes`` — refused from the declared size before anything is
  staged or written, and, for a parser that reports no size, stopped at byte
  ``max_bytes + 1`` of the spool while streaming out of it (exactly
  ``max_bytes + 1`` bytes read on the local branch; at most one
  ``STREAM_CHUNK`` past the cap on the Workspace path). The body itself has
  been received and spooled by then — ``test_upload_guard.py`` covers the
  Content-Length check that runs before it;
* per request: ≤ ``max_upload_files`` (50) files and ≤
  ``max_upload_bytes_per_request`` (500 MB) — the declared sum up front (no
  staging, no Workspace call, no version reserved, no job), and a running total
  while streaming;
* one in-flight ingest job per principal → 429 + ``Retry-After``; admin exempt
  and logged;
* a scanned (image-only) PDF → the item fails with the actionable
  ``no extractable text (scanned PDF?)`` error and the run logs the ``no_text``
  count at INFO;
* ``INGEST_ROOT`` is never touched on the gowe branch.

Under ASGITransport a background task finishes before the response returns,
so "a second concurrent job" is seeded in the job store rather than raced.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest
from fastapi import HTTPException

from ragstack.api import security
from ragstack.api.deps import INFLIGHT_RETRY_AFTER_SECONDS
from ragstack.api.main import app
from ragstack.api.routers import documents as docmod
from ragstack.api.security import ROLE_ADMIN
from ragstack.config import settings
from ragstack.ingestion.loaders import (
    NO_TEXT_ERROR,
    NO_TEXT_LABEL,
    LoaderError,
    NoTextExtracted,
    PdfLoader,
)
from ragstack.jobstore import ACCEPTED, COMPLETED, FAILED, RUNNING
from ragstack.workspace import STREAM_CHUNK, WorkspaceTooLarge, _bounded
from tests.api import test_ingest_gowe_path as _gowe_path

# The gowe fixture (fake engine + fake Workspace + bearer identity + lib1) is
# bound here by assignment so pytest registers it for this module.
gowe = _gowe_path.gowe
AUTH = _gowe_path.AUTH
TENANT = _gowe_path.TENANT

_FIXTURE = (
    Path(__file__).resolve().parents[3] / "contracts" / "fixtures" / "documents" / "sample_small.pdf"
)
PDF = "application/pdf"
MAX_BYTES = 50_000_000  # the documented max_document_bytes default


def _pdf_bytes() -> bytes:
    data = _FIXTURE.read_bytes()
    assert data.startswith(b"%PDF")
    return data


def _files(*specs: tuple[str, bytes, str]) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("files", spec) for spec in specs]


@pytest.fixture
def rooted(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ingest_root", str(tmp_path))
    return tmp_path


def _staged(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


class FakeUpload:
    """An ``UploadFile`` stand-in that counts what is read from it, with
    ``size=None`` — the parser-reports-no-size case the streaming aborts exist
    for."""

    def __init__(self, data: bytes, filename: str = "f.pdf", content_type: str = PDF) -> None:
        self._buf = io.BytesIO(data)
        self.filename = filename
        self.content_type = content_type
        self.size: int | None = None
        self.bytes_read = 0
        self.reads = 0

    async def read(self, n: int = -1) -> bytes:
        chunk = self._buf.read(n)
        self.bytes_read += len(chunk)
        self.reads += 1
        return chunk

    async def seek(self, pos: int) -> None:
        self._buf.seek(pos)


# --------------------------------------------------------------------------- #
# Defaults are what the spec says
# --------------------------------------------------------------------------- #


def test_documented_defaults():
    from ragstack.config import Settings

    defaults = Settings(_env_file=None)  # the code defaults, not a .env's
    assert defaults.max_upload_files == 50
    assert defaults.max_upload_bytes_per_request == 500_000_000
    assert defaults.max_document_bytes == MAX_BYTES
    assert defaults.upload_content_types == [
        "application/pdf", "text/plain", "text/markdown", "application/xml", "text/xml",
    ]


def test_upload_content_types_parses_comma_list_and_json_array():
    from ragstack.config import Settings

    assert Settings(upload_content_types="application/pdf, text/plain").upload_content_types == [
        "application/pdf", "text/plain",
    ]
    assert Settings(upload_content_types='["text/xml"]').upload_content_types == ["text/xml"]


# --------------------------------------------------------------------------- #
# Content-type allowlist + magic (415), on both branches
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wrong_content_type_is_415_local(client, rooted):
    r = await client.post(
        "/v1/ingest/upload", files=_files(("a.zip", b"PK\x03\x04", "application/zip"))
    )
    assert r.status_code == 415, r.text
    assert "not an accepted upload content type" in r.json()["detail"]
    assert _staged(rooted) == []
    assert app.state.job_store._jobs == {}  # refused before a job exists


@pytest.mark.asyncio
async def test_allowlist_is_the_setting(client, rooted, monkeypatch):
    monkeypatch.setattr(settings, "upload_content_types", ["application/pdf"])
    r = await client.post("/v1/ingest/upload", files=_files(("n.txt", b"text", "text/plain")))
    assert r.status_code == 415, r.text
    # …and a type the operator lists but this server has no loader for is 415 too.
    monkeypatch.setattr(settings, "upload_content_types", ["application/json"])
    r = await client.post(
        "/v1/ingest/upload", files=_files(("n.json", b"{}", "application/json"))
    )
    assert r.status_code == 415, r.text


@pytest.mark.parametrize("body, why", [
    (b"NOT-A-PDF-AT-ALL", "missing %PDF header"),
    (b"", "empty upload, no %PDF header"),
])
@pytest.mark.asyncio
async def test_pdf_magic_mismatch_is_415_local(client, rooted, body, why):
    r = await client.post("/v1/ingest/upload", files=_files(("fake.pdf", body, PDF)))
    assert r.status_code == 415, r.text
    assert why in r.json()["detail"]
    assert _staged(rooted) == []


@pytest.mark.asyncio
async def test_wrong_type_and_bad_magic_are_415_before_any_workspace_write(client, gowe):
    for spec in (("a.zip", b"PK\x03\x04", "application/zip"), ("fake.pdf", b"nope", PDF)):
        r = await client.post(
            "/v1/ingest/upload", files=_files(spec), data={"collection": "lib1"}, headers=AUTH
        )
        assert r.status_code == 415, r.text
    assert gowe["workspace"].uploads == [] and gowe["workspace"].folders == []
    assert gowe["store"].calls.get("next_version", 0) == 0
    assert app.state.job_store._jobs == {}


@pytest.mark.asyncio
async def test_text_and_markdown_are_accepted_and_ingested_local(client, rooted):
    r = await client.post(
        "/v1/ingest/upload",
        files=_files(
            ("notes.txt", b"plain text about proteins\n", "text/plain"),
            ("readme", b"# heading\n\nmarkdown body\n", "text/markdown"),
            ("draft.md", b"markdown sent as plain text\n", "text/plain"),
        ),
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    # Staged under suffixes the ingest manifest picks up: a name without one
    # gets the kind's default, a name with an accepted one keeps it (draft.md
    # sent as text/plain stays draft.md — not draft.md.txt).
    assert sorted(p.name for p in _staged(rooted)) == ["draft.md", "notes.txt", "readme.md"]
    poll = await client.get(f"/v1/ingest/{job_id}")
    assert poll.json()["status"] == "completed"
    assert poll.json()["items"] == {"total": 3, "completed": 3, "failed": 0, "pending": 0}


@pytest.mark.asyncio
async def test_xml_is_accepted_at_the_gate_but_fails_visibly_without_a_loader(
    client, rooted, caplog
):
    """Pinned limitation (#202): JATS XML passes the allowlist and is staged as
    ``.xml``, but the local loader registry has no ``.xml`` loader. The file
    must not vanish: it becomes a FAILED item with the constant, countable
    error ``no loader for .xml`` (and the run logs the ``no_loader`` count).
    When a JATS loader lands this test must change to assert the item
    completed."""
    xml = b"<article><body><p>x</p></body></article>"
    with caplog.at_level(logging.INFO, logger="ragstack.api.routers.documents"):
        r = await client.post(
            "/v1/ingest/upload", files=_files(("PMC1.xml", xml, "application/xml"))
        )
    assert r.status_code == 202, r.text
    assert [p.name for p in _staged(rooted)] == ["PMC1.xml"]
    job_id = r.json()["job_id"]
    poll = await client.get(f"/v1/ingest/{job_id}")
    assert poll.json()["status"] == "failed"  # every item failed
    assert poll.json()["items"] == {"total": 1, "completed": 0, "failed": 1, "pending": 0}
    (item,) = app.state.job_store._items[job_id].values()
    assert item.status == FAILED and item.error == "no loader for .xml"
    assert any(
        "1 of 1 file(s) have no loader for their suffix [no_loader]" in rec.getMessage()
        for rec in caplog.records
    )

    # Mixed: the supported file ingests, the unsupported one fails — the job is
    # completed (partial failure), the counts say exactly what happened.
    r = await client.post(
        "/v1/ingest/upload",
        files=_files(("PMC2.xml", xml, "text/xml"), ("ok.txt", b"real text\n", "text/plain")),
    )
    assert r.status_code == 202, r.text
    poll = await client.get(f"/v1/ingest/{r.json()['job_id']}")
    assert poll.json()["status"] == "completed"
    assert poll.json()["items"] == {"total": 2, "completed": 1, "failed": 1, "pending": 0}


def test_sniff_is_one_helper_used_by_both_branches():
    """The magic check lives in ``_sniff_upload`` only: neither branch carries
    its own copy (the pre-2c duplication)."""
    import inspect

    src = inspect.getsource(docmod)
    assert src.count("startswith(_PDF_MAGIC)") == 1
    assert "_sniff_upload" in inspect.getsource(docmod._admit_uploads)
    assert "_admit_uploads" in inspect.getsource(docmod.ingest_upload)


# --------------------------------------------------------------------------- #
# Per-request bounds: files (413) and bytes (413), up front and while streaming
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_51_files_is_413_local(client, rooted):
    pdf = _pdf_bytes()
    r = await client.post(
        "/v1/ingest/upload", files=_files(*((f"f{i}.pdf", pdf, PDF) for i in range(51)))
    )
    assert r.status_code == 413, r.text
    assert "too many files: 51 > max_upload_files (50)" in r.json()["detail"]
    assert _staged(rooted) == []
    assert app.state.job_store._jobs == {}
    r = await client.post(
        "/v1/ingest/upload", files=_files(*((f"f{i}.pdf", pdf, PDF) for i in range(50)))
    )
    assert r.status_code == 202, r.text


@pytest.mark.asyncio
async def test_51_files_is_413_gowe(client, gowe):
    pdf = _pdf_bytes()
    r = await client.post(
        "/v1/ingest/upload",
        files=_files(*((f"f{i}.pdf", pdf, PDF) for i in range(51))),
        data={"collection": "lib1"}, headers=AUTH,
    )
    assert r.status_code == 413, r.text
    assert gowe["workspace"].uploads == [] and gowe["workspace"].folders == []
    assert gowe["store"].calls.get("next_version", 0) == 0


@pytest.mark.asyncio
async def test_sum_of_declared_sizes_over_request_cap_is_413_before_any_read_local(
    client, rooted, monkeypatch
):
    pdf = _pdf_bytes()
    monkeypatch.setattr(settings, "max_upload_bytes_per_request", 2 * len(pdf) - 1)
    r = await client.post(
        "/v1/ingest/upload", files=_files(("a.pdf", pdf, PDF), ("b.pdf", pdf, PDF))
    )
    assert r.status_code == 413, r.text
    assert "max_upload_bytes_per_request" in r.json()["detail"]
    assert _staged(rooted) == []  # nothing staged: refused from the declared sizes
    assert app.state.job_store._jobs == {}
    monkeypatch.setattr(settings, "max_upload_bytes_per_request", 2 * len(pdf))
    r = await client.post(
        "/v1/ingest/upload", files=_files(("a.pdf", pdf, PDF), ("b.pdf", pdf, PDF))
    )
    assert r.status_code == 202, r.text


@pytest.mark.asyncio
async def test_sum_of_declared_sizes_over_request_cap_is_413_before_any_write_gowe(
    client, gowe, monkeypatch
):
    pdf = _pdf_bytes()
    monkeypatch.setattr(settings, "max_upload_bytes_per_request", 2 * len(pdf) - 1)
    r = await client.post(
        "/v1/ingest/upload", files=_files(("a.pdf", pdf, PDF), ("b.pdf", pdf, PDF)),
        data={"collection": "lib1"}, headers=AUTH,
    )
    assert r.status_code == 413, r.text
    assert gowe["workspace"].uploads == [] and gowe["workspace"].folders == []
    assert gowe["store"].calls.get("next_version", 0) == 0
    assert gowe["engine"].submissions == []
    assert app.state.job_store._jobs == {}


@pytest.mark.asyncio
async def test_request_budget_is_also_enforced_while_streaming(tmp_path):
    """The running total for a parser that reports no sizes: the second file
    is cut off mid-stream on the local branch (``_stage_upload``) and on the
    Workspace path (``_MeteredUpload`` under ``workspace._bounded``)."""
    budget = docmod._UploadBudget(limit=1000)
    a, b = FakeUpload(b"%PDF" + b"a" * 596, "a.pdf"), FakeUpload(b"%PDF" + b"b" * 596, "b.pdf")
    await docmod._stage_upload(a, tmp_path / "a.pdf", MAX_BYTES, budget)
    with pytest.raises(HTTPException) as exc:
        await docmod._stage_upload(b, tmp_path / "b.pdf", MAX_BYTES, budget)
    assert exc.value.status_code == 413
    assert "max_upload_bytes_per_request" in exc.value.detail
    assert budget.used == 1200 and b.bytes_read == 600

    budget = docmod._UploadBudget(limit=1000)
    c, d = FakeUpload(b"%PDF" + b"c" * 596, "c.pdf"), FakeUpload(b"%PDF" + b"d" * 596, "d.pdf")

    async def _drain(upload: FakeUpload) -> None:
        async for _ in _bounded(docmod._MeteredUpload(upload, budget), upload.filename, MAX_BYTES):
            pass

    await _drain(c)
    with pytest.raises(HTTPException) as exc:
        await _drain(d)
    assert exc.value.status_code == 413
    # Disabled by 0.
    docmod._UploadBudget(limit=0).charge(10**12, "x")


# --------------------------------------------------------------------------- #
# max_document_bytes: refused up front from the declared size; stopped at
# byte max_bytes + 1 while streaming out of the spool
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_50mb_plus_one_byte_is_413_without_staging_local(client, rooted):
    assert settings.max_document_bytes == MAX_BYTES
    payload = b"%PDF" + b"\0" * (MAX_BYTES + 1 - 4)
    r = await client.post(
        "/v1/ingest/upload", files=_files(("big.pdf", io.BytesIO(payload), PDF))
    )
    assert r.status_code == 413, r.text
    assert "exceeds max_document_bytes" in r.json()["detail"]
    assert _staged(rooted) == []  # refused from the declared size — nothing staged
    assert app.state.job_store._jobs == {}


@pytest.mark.asyncio
async def test_50mb_plus_one_byte_is_413_before_any_rpc_gowe(client, gowe):
    payload = b"%PDF" + b"\0" * (MAX_BYTES + 1 - 4)
    r = await client.post(
        "/v1/ingest/upload", files=_files(("big.pdf", io.BytesIO(payload), PDF)),
        data={"collection": "lib1"}, headers=AUTH,
    )
    assert r.status_code == 413, r.text
    assert gowe["workspace"].uploads == [] and gowe["workspace"].folders == []
    assert gowe["store"].calls.get("next_version", 0) == 0


@pytest.mark.asyncio
async def test_streaming_abort_reads_exactly_max_plus_one_byte_local(tmp_path):
    """No declared size → the local branch reads capped chunks and stops at
    byte ``max_bytes + 1``: never a whole trailing chunk, never the file."""
    upload = FakeUpload(b"%PDF" + b"\0" * (MAX_BYTES + 1 - 4), "big.pdf")
    dest = tmp_path / "big.pdf"
    with pytest.raises(HTTPException) as exc:
        await docmod._stage_upload(upload, dest, MAX_BYTES, docmod._UploadBudget(0))
    assert exc.value.status_code == 413
    assert upload.bytes_read == MAX_BYTES + 1
    assert dest.stat().st_size <= MAX_BYTES
    # A file of exactly max_bytes streams through whole.
    ok = FakeUpload(b"%PDF" + b"\0" * (MAX_BYTES - 4), "ok.pdf")
    await docmod._stage_upload(ok, tmp_path / "ok.pdf", MAX_BYTES, docmod._UploadBudget(0))
    assert (tmp_path / "ok.pdf").stat().st_size == MAX_BYTES == ok.bytes_read


@pytest.mark.asyncio
async def test_streaming_abort_reads_at_most_one_chunk_past_the_cap_workspace():
    """No declared size → ``workspace._bounded`` (what ``upload_source``
    streams through) raises within one STREAM_CHUNK of the cap."""
    upload = FakeUpload(b"%PDF" + b"\0" * (MAX_BYTES + 1 - 4), "big.pdf")
    with pytest.raises(WorkspaceTooLarge):
        async for _ in _bounded(upload, "big.pdf", MAX_BYTES):
            pass
    assert MAX_BYTES < upload.bytes_read <= MAX_BYTES + STREAM_CHUNK
    assert upload.bytes_read < MAX_BYTES + (1 << 20)


@pytest.mark.asyncio
async def test_declared_size_reaches_upload_source_on_gowe(client, gowe):
    pdf = _pdf_bytes()
    r = await client.post(
        "/v1/ingest/upload", files=_files(("a.pdf", pdf, PDF)),
        data={"collection": "lib1"}, headers=AUTH,
    )
    assert r.status_code == 202, r.text
    (up,) = gowe["workspace"].uploads
    assert up["size"] == len(pdf) == up["bytes"]


@pytest.mark.asyncio
async def test_ingest_root_is_unused_on_the_gowe_branch(client, gowe, rooted):
    """INGEST_ROOT is the local branch's staging root only: point it at an
    empty dir, upload through gowe, and the dir stays empty."""
    r = await client.post(
        "/v1/ingest/upload", files=_files(("a.pdf", _pdf_bytes(), PDF)),
        data={"collection": "lib1"}, headers=AUTH,
    )
    assert r.status_code == 202, r.text
    assert _staged(rooted) == [] and not (rooted / "uploads").exists()
    assert len(gowe["workspace"].uploads) == 1


# --------------------------------------------------------------------------- #
# One in-flight ingest job per principal (429 + Retry-After); admin exempt
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_second_job_for_the_same_principal_is_429_with_retry_after(client, rooted):
    store = app.state.job_store
    inflight = await store.create(source="/earlier", tenant_id="default")
    assert inflight.status == ACCEPTED
    files = _files(("a.pdf", _pdf_bytes(), PDF))
    r = await client.post("/v1/ingest/upload", files=files)
    assert r.status_code == 429, r.text
    assert r.headers["Retry-After"] == str(INFLIGHT_RETRY_AFTER_SECONDS)
    assert "still in flight" in r.json()["detail"]
    assert _staged(rooted) == [] and len(store._jobs) == 1  # refused before a job exists

    await store.update(inflight.job_id, status=RUNNING)
    assert (await client.post("/v1/ingest/upload", files=files)).status_code == 429

    # Another tenant's running job is not this principal's.
    await store.update(inflight.job_id, status=COMPLETED)
    other = await store.create(source="/other", tenant_id="someone-else")
    await store.update(other.job_id, status=RUNNING)
    r = await client.post("/v1/ingest/upload", files=files)
    assert r.status_code == 202, r.text
    # The upload's own job is terminal once the background task ran, so a
    # follow-up is admitted again — the slot frees itself with the job.
    assert (await client.post("/v1/ingest/upload", files=files)).status_code == 202

    # A failed job frees the slot too.
    stuck = await store.create(source="/x", tenant_id="default")
    await store.update(stuck.job_id, status=FAILED, error="rejected")
    assert (await client.post("/v1/ingest/upload", files=files)).status_code == 202


@pytest.mark.asyncio
async def test_inflight_check_applies_on_gowe_before_any_write(client, gowe):
    await app.state.job_store.create(source="/earlier", tenant_id=TENANT)
    r = await client.post(
        "/v1/ingest/upload", files=_files(("a.pdf", _pdf_bytes(), PDF)),
        data={"collection": "lib1"}, headers=AUTH,
    )
    assert r.status_code == 429, r.text
    assert r.headers["Retry-After"] == str(INFLIGHT_RETRY_AFTER_SECONDS)
    assert gowe["workspace"].uploads == [] and gowe["store"].calls.get("next_version", 0) == 0


@pytest.mark.asyncio
async def test_admin_is_exempt_from_the_inflight_check_and_logged(
    client, rooted, monkeypatch, caplog
):
    monkeypatch.setattr(security.settings, "api_keys", ["k-admin"])
    monkeypatch.setattr(security.settings, "api_key_tenants", {"k-admin": "ops"})
    monkeypatch.setattr(security.settings, "api_key_roles", {"k-admin": ROLE_ADMIN})
    await app.state.job_store.create(source="/earlier", tenant_id="ops")
    with caplog.at_level(logging.INFO, logger="ragstack.api.deps"):
        r = await client.post(
            "/v1/ingest/upload", files=_files(("a.pdf", _pdf_bytes(), PDF)),
            headers={"X-API-Key": "k-admin"},
        )
    assert r.status_code == 202, r.text
    assert any(
        "single-inflight ingest check bypassed for admin principal" in rec.getMessage()
        and "'ops'" in rec.getMessage()
        for rec in caplog.records
    )


@pytest.mark.asyncio
async def test_inflight_check_runs_before_the_rate_bucket(client, rooted, monkeypatch):
    """A 429 for an in-flight job must not also spend the caller's hourly
    ingest token — the in-flight dependency is ordered first."""
    monkeypatch.setattr(settings, "rate_limit_ingest_per_hour", 1)
    from ragstack.api.deps import build_rate_limiters

    app.state.rate_limiters = build_rate_limiters()
    inflight = await app.state.job_store.create(source="/earlier", tenant_id="default")
    files = _files(("a.pdf", _pdf_bytes(), PDF))
    assert (await client.post("/v1/ingest/upload", files=files)).status_code == 429
    await app.state.job_store.update(inflight.job_id, status=COMPLETED)
    assert (await client.post("/v1/ingest/upload", files=files)).status_code == 202
    assert (await client.post("/v1/ingest/upload", files=files)).status_code == 429  # bucket


# --------------------------------------------------------------------------- #
# Scanned PDFs: typed loader error → actionable per-item error, counted
# --------------------------------------------------------------------------- #


def _scanned_pdf_bytes() -> bytes:
    """A one-page PDF whose only content is an image — no text stream at all."""
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=200)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 64, 64), False)
    pix.set_rect(pix.irect, (120, 120, 120))
    page.insert_image(pymupdf.Rect(20, 20, 180, 180), pixmap=pix)
    data = doc.tobytes()
    doc.close()
    assert data.startswith(b"%PDF")
    return data


def test_pdf_loader_raises_typed_no_text_error(tmp_path):
    path = tmp_path / "scan.pdf"
    path.write_bytes(_scanned_pdf_bytes())
    with pytest.raises(NoTextExtracted) as exc:
        PdfLoader().load(str(path))
    assert isinstance(exc.value, LoaderError)
    assert exc.value.job_error == NO_TEXT_ERROR == "no extractable text (scanned PDF?)"
    assert "scanned" in str(exc.value)
    # The fixture PDF (with text) still loads.
    assert PdfLoader().load(str(_FIXTURE))[0].content


@pytest.mark.asyncio
async def test_scanned_pdf_upload_yields_actionable_item_error_and_no_text_count(
    client, rooted, caplog
):
    with caplog.at_level(logging.INFO, logger="ragstack.ingestion.sharded"):
        r = await client.post(
            "/v1/ingest/upload",
            files=_files(("scan.pdf", _scanned_pdf_bytes(), PDF), ("ok.pdf", _pdf_bytes(), PDF)),
        )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    poll = await client.get(f"/v1/ingest/{job_id}")
    assert poll.json()["status"] == "completed"  # partial failure: one item failed
    assert poll.json()["items"] == {"total": 2, "completed": 1, "failed": 1, "pending": 0}
    items = app.state.job_store._items[job_id]
    (failed,) = [it for it in items.values() if it.status == FAILED]
    assert failed.error == NO_TEXT_ERROR
    assert "scan.pdf" in failed.item_id or "scan.pdf" in failed.source
    counted = [
        rec.getMessage() for rec in caplog.records
        if f"[{NO_TEXT_LABEL}]" in rec.getMessage() and rec.levelno == logging.INFO
    ]
    assert counted and "1 of 2 item(s) failed with no extractable text (scanned PDF?)" in counted[0]
    assert job_id in counted[0]
