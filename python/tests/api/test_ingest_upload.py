"""POST /v1/ingest/upload — multipart PDF upload ingestion (issue #202).

Exercised over ASGITransport (see tests/api/conftest.py): a valid PDF is staged
under a per-tenant/per-job dir and ingested via the same background path as
POST /v1/ingest; non-PDF → 415, oversize → 413, too many files → 413, a
traversal filename is confined to a basename, and an unset INGEST_ROOT → 503.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "contracts"
    / "fixtures"
    / "documents"
    / "sample_small.pdf"
)


def _pdf_bytes() -> bytes:
    data = _FIXTURE.read_bytes()
    assert data.startswith(b"%PDF")
    return data


@pytest.fixture
def rooted(tmp_path, monkeypatch):
    """Point INGEST_ROOT at an isolated tmp dir (wins over the conftest autouse
    fixture, which is applied first) so uploads stage somewhere we can assert on."""
    from ragstack.config import settings

    monkeypatch.setattr(settings, "ingest_root", str(tmp_path))
    return tmp_path


def _uploads_dir(root: Path, tenant: str = "default") -> Path:
    return root / "uploads" / tenant


@pytest.mark.asyncio
async def test_upload_valid_pdf_returns_202_and_stages(client, rooted):
    r = await client.post(
        "/v1/ingest/upload",
        files=[("files", ("paper.pdf", _pdf_bytes(), "application/pdf"))],
    )
    assert r.status_code == 202, r.text
    body = r.json()
    job_id = body["job_id"]
    assert job_id
    assert body["status"] == "accepted"

    # The staged file lands under the per-tenant/per-job dir and nowhere else.
    job_dir = _uploads_dir(rooted) / job_id
    staged = job_dir / "paper.pdf"
    assert staged.is_file()
    assert staged.read_bytes().startswith(b"%PDF")
    # No file escaped the per-job staging dir.
    all_files = [p for p in rooted.rglob("*") if p.is_file()]
    assert all_files == [staged]

    # Background task ran under ASGITransport → status is terminal + polls cleanly.
    poll = await client.get(f"/v1/ingest/{job_id}")
    assert poll.status_code == 200
    assert poll.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_upload_non_pdf_content_type_is_415(client, rooted):
    r = await client.post(
        "/v1/ingest/upload",
        files=[("files", ("notes.txt", b"just some text", "text/plain"))],
    )
    assert r.status_code == 415, r.text
    # Nothing was left staged.
    assert not list(_uploads_dir(rooted).rglob("*")) if _uploads_dir(rooted).exists() else True


@pytest.mark.asyncio
async def test_upload_pdf_content_type_but_bad_magic_is_415(client, rooted):
    r = await client.post(
        "/v1/ingest/upload",
        files=[("files", ("fake.pdf", b"NOT-A-PDF-AT-ALL", "application/pdf"))],
    )
    assert r.status_code == 415, r.text


@pytest.mark.asyncio
async def test_upload_oversize_is_413(client, rooted, monkeypatch):
    from ragstack.config import settings

    monkeypatch.setattr(settings, "max_document_bytes", 10)  # fixture is ~900 bytes
    r = await client.post(
        "/v1/ingest/upload",
        files=[("files", ("big.pdf", _pdf_bytes(), "application/pdf"))],
    )
    assert r.status_code == 413, r.text
    # Partial write cleaned up.
    assert not list(_uploads_dir(rooted).rglob("*")) if _uploads_dir(rooted).exists() else True


@pytest.mark.asyncio
async def test_upload_too_many_files_is_413(client, rooted, monkeypatch):
    from ragstack.config import settings

    monkeypatch.setattr(settings, "max_upload_files", 1)
    r = await client.post(
        "/v1/ingest/upload",
        files=[
            ("files", ("a.pdf", _pdf_bytes(), "application/pdf")),
            ("files", ("b.pdf", _pdf_bytes(), "application/pdf")),
        ],
    )
    assert r.status_code == 413, r.text


@pytest.mark.asyncio
async def test_upload_traversal_filename_is_confined(client, rooted):
    r = await client.post(
        "/v1/ingest/upload",
        files=[("files", ("../../../etc/evil.pdf", _pdf_bytes(), "application/pdf"))],
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    job_dir = _uploads_dir(rooted) / job_id
    # Sanitized down to a basename inside the staging dir — did not escape.
    assert (job_dir / "evil.pdf").is_file()
    escaped = [
        p
        for p in rooted.rglob("*")
        if p.is_file() and job_dir not in p.parents
    ]
    assert escaped == []


@pytest.mark.asyncio
async def test_upload_disabled_when_ingest_root_unset_is_503(client, monkeypatch):
    from ragstack.config import settings

    monkeypatch.setattr(settings, "ingest_root", "")
    r = await client.post(
        "/v1/ingest/upload",
        files=[("files", ("paper.pdf", _pdf_bytes(), "application/pdf"))],
    )
    assert r.status_code == 503, r.text


@pytest.mark.asyncio
async def test_upload_into_unknown_collection_is_404(client, rooted):
    r = await client.post(
        "/v1/ingest/upload",
        files=[("files", ("paper.pdf", _pdf_bytes(), "application/pdf"))],
        data={"collection": "ghost"},
    )
    assert r.status_code == 404, r.text
