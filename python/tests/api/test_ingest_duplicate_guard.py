"""The duplicate-paper guard through the API (#747): both ingest paths.

In-process app over the conftest's in-memory doubles; no live store. The
deployment default is ``refuse`` (owner decision D1), so the guard runs on
every API ingest unless the request says ``duplicate_policy: "allow"`` or the
deployment sets ``off``.

* **admission (D2)** — a file NAME carrying a work id that an indexed document
  carries too is a 409 before any job exists, with the D4 object ``detail``;
* **job time** — otherwise the item fails with the formatted
  ``duplicate_work: key=… existing_doc_id=…`` refusal and nothing is written;
* **allow** — admitted, every chunk stamped ``duplicate_of``;
* **gowe** — the pre-check runs before any Workspace write; the worker input
  travels only when the tenant's worker image carries the flag.
"""
from __future__ import annotations

import asyncio

import pytest

from ragstack.api.main import app
from ragstack.ingestion.work_identity import DUPLICATE_WORK, format_refusal
from tests.api.test_chunk_cap_status import (  # noqa: F401 — fixtures
    _cleanup,
    principals,
)
from tests.api.test_ingest_gowe_path import (  # noqa: F401 — the `gowe` fixture
    AUTH,
    TENANT,
    _upload,
    gowe,
)

pytestmark = pytest.mark.asyncio

DOI = "10.1073/pnas.2018995118"
BODY = " ".join(f"Indoor airborne transmission passage {i}." for i in range(40))
EPMC = f"PMCID: PMC8092463 · PMID: 33858987 · DOI: 10.1073/\npnas.2018995118\n\n{BODY}"
ARTICLE = f"PNAS https://doi.org/{DOI}\n\n{BODY}"


async def _poll(client, job_id: str, headers: dict | None = None) -> dict:
    body: dict = {}
    for _ in range(300):
        body = (await client.get(f"/v1/ingest/{job_id}", headers=headers or {})).json()
        if body["status"] in ("completed", "failed"):
            break
        await asyncio.sleep(0.01)
    return body


async def _upload_md(client, name: str, text: str, **form):
    return await client.post(
        "/v1/ingest/upload", files=[("files", (name, text.encode(), "text/markdown"))],
        data=form,
    )


def _doc_ids() -> set[str]:
    return {c.doc_id for c in app.state.text_index._chunks}


def _item(job_id: str):
    return next(iter(app.state.job_store._items[job_id].values()))


async def _seed_article(client) -> str:
    """The first copy: an upload whose text carries the DOI (no id in the name)."""
    r = await _upload_md(client, "sha-487426f38505525d.md", f"DOI: {DOI}\n\n{ARTICLE}")
    assert r.status_code == 202, r.text
    assert (await _poll(client, r.json()["job_id"]))["status"] == "completed"
    (doc_id,) = _doc_ids()
    return doc_id


# --------------------------------------------------------------------------- #
# local path
# --------------------------------------------------------------------------- #


async def test_job_time_refusal_fails_the_item_with_the_reason(client) -> None:
    first = await _seed_article(client)
    r = await _upload_md(client, "sha-5035d3ab6f1f8749.md", f"DOI: {DOI}/-/\n\n{BODY}")
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    body = await _poll(client, job_id)
    assert body["status"] == "failed"
    assert body["items"] == {"total": 1, "completed": 0, "failed": 1, "pending": 0}
    assert _item(job_id).error == format_refusal(("doi", DOI), first)
    assert _doc_ids() == {first}


async def test_admission_409_on_a_pmcid_file_name_is_an_object(client) -> None:
    first = await _seed_article(client)
    # the seeded copy has no pmcid yet; a Europe PMC copy indexed WITH one is
    # what a later PMC-named upload collides with at admission
    r = await _upload_md(client, "europepmc.md", EPMC, duplicate_policy="allow")
    assert (await _poll(client, r.json()["job_id"]))["status"] == "completed"
    epmc_id = (_doc_ids() - {first}).pop()
    jobs_before = set(app.state.job_store._jobs)
    r = await _upload_md(client, "PMC8092463.md", EPMC)
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert set(detail) == {"error", "key", "existing_doc_id", "collection", "message"}
    assert detail["error"] == DUPLICATE_WORK and detail["key"] == "pmcid:PMC8092463"
    assert detail["existing_doc_id"] == epmc_id and detail["collection"]
    assert "duplicate_policy=allow" in detail["message"]
    assert set(app.state.job_store._jobs) == jobs_before  # no job was created


async def test_allow_admits_and_stamps_duplicate_of(client) -> None:
    first = await _seed_article(client)
    r = await _upload_md(client, "PMC8092463.md", EPMC, duplicate_policy="allow")
    assert r.status_code == 202, r.text
    assert (await _poll(client, r.json()["job_id"]))["status"] == "completed"
    second = [c for c in app.state.text_index._chunks if c.doc_id != first]
    assert second and {c.metadata["duplicate_of"] for c in second} == {first}
    # the Europe PMC header ids were filled before the lookup
    assert second[0].metadata["pmcid"] == "PMC8092463"


async def test_deployment_off_disables_the_guard(client, monkeypatch) -> None:
    from ragstack.config import settings

    first = await _seed_article(client)
    monkeypatch.setattr(settings, "duplicate_policy", "off")
    r = await _upload_md(client, "PMC8092463.md", EPMC)
    assert (await _poll(client, r.json()["job_id"]))["status"] == "completed"
    assert len(_doc_ids()) == 2
    assert all("duplicate_of" not in c.metadata for c in app.state.text_index._chunks)
    assert first in _doc_ids()


async def test_path_ingest_reingest_is_a_replace_and_a_copy_is_409(client, tmp_path) -> None:
    src = tmp_path / "a" / "PMC8092463.md"
    src.parent.mkdir()
    src.write_text(EPMC, encoding="utf-8")
    r = await client.post("/v1/ingest", json={"source": str(src)})
    assert r.status_code == 200, r.text
    assert (await _poll(client, r.json()["job_id"]))["status"] == "completed"
    (first,) = _doc_ids()
    # same path again: a replace (same doc id), not a duplicate — no 409
    r = await client.post("/v1/ingest", json={"source": str(src)})
    assert r.status_code == 200, r.text
    assert (await _poll(client, r.json()["job_id"]))["status"] == "completed"
    assert _doc_ids() == {first}
    # the same file under another path: 409 at admission
    copy = tmp_path / "b" / "PMC8092463.md"
    copy.parent.mkdir()
    copy.write_text(EPMC, encoding="utf-8")
    r = await client.post("/v1/ingest", json={"source": str(copy)})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["existing_doc_id"] == first
    # …which the request can override
    r = await client.post("/v1/ingest", json={"source": str(copy), "duplicate_policy": "allow"})
    assert r.status_code == 200, r.text
    assert (await _poll(client, r.json()["job_id"]))["status"] == "completed"
    assert len(_doc_ids()) == 2


async def test_unknown_policy_is_422_on_both_endpoints(client) -> None:
    r = await client.post("/v1/ingest", json={"source": "/tmp/x.md", "duplicate_policy": "off"})
    assert r.status_code == 422, r.text
    r = await _upload_md(client, "x.md", BODY, duplicate_policy="bogus")
    assert r.status_code == 422, r.text


async def test_precheck_lookup_failure_admits(client, monkeypatch) -> None:
    async def _down(*a, **k):
        raise ConnectionError("es down")

    monkeypatch.setattr(app.state.text_index, "find_documents_by_key", _down)
    r = await _upload_md(client, "PMC8092463.md", EPMC)
    assert r.status_code == 202, r.text
    assert (await _poll(client, r.json()["job_id"]))["status"] == "completed"


# --------------------------------------------------------------------------- #
# gowe path
# --------------------------------------------------------------------------- #


async def _seed_index_for(tenant: str, pmcid: str = "PMC8092463") -> str:
    """Put an indexed document carrying ``pmcid`` into the in-memory index the
    gowe fixture's ``lib1`` entry serves."""
    from ragstack.models import Chunk

    doc_id = "existing-doc"
    await app.state.text_index.index([Chunk(
        id="c0", doc_id=doc_id, content=BODY,
        metadata={"tenant_id": tenant, "pmcid": pmcid, "filename": "sha-1.md"},
    )])
    return doc_id


async def test_gowe_upload_precheck_is_409_before_any_workspace_write(client, gowe) -> None:  # noqa: F811,E501
    existing = await _seed_index_for(TENANT)
    r = await _upload(client, "PMC8092463.pdf")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["existing_doc_id"] == existing
    assert gowe["workspace"].uploads == [] and gowe["workspace"].folders == []
    assert gowe["store"].calls.get("next_version", 0) == 0
    assert gowe["engine"].submissions == []


async def test_gowe_ws_reference_precheck_is_409(client, gowe) -> None:  # noqa: F811
    await _seed_index_for(TENANT)
    src = f"ws:///{TENANT.split(':', 1)[1]}/home/papers/PMC8092463.pdf"
    r = await client.post("/v1/ingest", json={"source": src, "collection": "lib1"},
                          headers=AUTH)
    assert r.status_code == 409, r.text
    assert gowe["engine"].submissions == []


async def test_gowe_precheck_never_names_another_tenants_document(client, gowe) -> None:  # noqa: F811,E501
    await _seed_index_for("someone-else")
    r = await _upload(client, "PMC8092463.pdf")
    assert r.status_code == 202, r.text


async def test_gowe_worker_input_only_when_the_workflow_declares_it(
    client, gowe, monkeypatch  # noqa: F811
) -> None:
    """Review fix 4 / ADR-0010: the CWL declares which image runs, so the API
    sends ``duplicate_policy`` only when the REGISTERED workflow declares that
    input (the ``provenance_inputs`` precedent). The fixture registers a bare
    CWL that does not declare it; then one that does."""
    from ragstack.config import settings

    backend = gowe["backend"]
    assert (await _upload(client, "a.pdf")).status_code == 202
    assert "duplicate_policy" not in gowe["engine"].submissions[-1]["inputs"]

    monkeypatch.setattr(backend, "workflow_cwl", (
        "cwlVersion: v1.2\nclass: Workflow\ninputs:\n"
        "  pdfs: File[]\n  duplicate_policy: [\"null\", string]\n"
        "outputs: {}\nsteps: {}\n"))
    await _poll(client, gowe["engine"].submissions[-1]["inputs"]["job_id"], AUTH)
    assert (await _upload(client, "b.pdf")).status_code == 202
    assert gowe["engine"].submissions[-1]["inputs"]["duplicate_policy"] == "refuse"

    await _poll(client, gowe["engine"].submissions[-1]["inputs"]["job_id"], AUTH)
    r = await client.post("/v1/ingest/upload", headers=AUTH,
                          data={"collection": "lib1", "duplicate_policy": "allow"},
                          files=[("files", ("c.pdf", b"%PDF-1.4 x", "application/pdf"))])
    assert r.status_code == 202, r.text
    assert gowe["engine"].submissions[-1]["inputs"]["duplicate_policy"] == "allow"

    await _poll(client, gowe["engine"].submissions[-1]["inputs"]["job_id"], AUTH)
    monkeypatch.setattr(settings, "duplicate_policy", "off")
    assert (await _upload(client, "d.pdf")).status_code == 202
    assert "duplicate_policy" not in gowe["engine"].submissions[-1]["inputs"]


async def test_the_repo_scatter_cwl_declares_the_input_so_it_is_sent(
    client, gowe, monkeypatch  # noqa: F811
) -> None:
    """#759 (R1): the shipped ``cwl/pdf-ingest-scatter.cwl`` declares
    ``duplicate_policy``, so once it is the registered text the API seeds the
    input whenever the resolved policy is not ``off`` — and still omits it
    when the deployment turns the guard off."""
    from pathlib import Path

    from ragstack.config import settings
    from ragstack.tool_image import declared_workflow_inputs

    cwl = Path(__file__).resolve().parents[3] / "cwl" / "pdf-ingest-scatter.cwl"
    if not cwl.is_file():
        pytest.skip("no cwl/ in this checkout")
    text = cwl.read_text()
    assert "duplicate_policy" in declared_workflow_inputs(text)

    monkeypatch.setattr(gowe["backend"], "workflow_cwl", text)
    assert (await _upload(client, "a.pdf")).status_code == 202
    assert gowe["engine"].submissions[-1]["inputs"]["duplicate_policy"] == "refuse"

    await _poll(client, gowe["engine"].submissions[-1]["inputs"]["job_id"], AUTH)
    monkeypatch.setattr(settings, "duplicate_policy", "off")
    assert (await _upload(client, "b.pdf")).status_code == 202
    assert "duplicate_policy" not in gowe["engine"].submissions[-1]["inputs"]


# --------------------------------------------------------------------------- #
# review fixes 2 and 3: shared scope, copies inside one request
# --------------------------------------------------------------------------- #


async def test_two_files_of_one_request_sharing_a_key_are_409(client) -> None:
    jobs_before = set(app.state.job_store._jobs)
    r = await client.post("/v1/ingest/upload", files=[
        ("files", ("PMC8092463.md", EPMC.encode(), "text/markdown")),
        ("files", ("copy-PMC8092463.md", EPMC.encode(), "text/markdown")),
    ])
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["key"] == "pmcid:PMC8092463" and detail["existing_doc_id"] is None
    assert "in this request" in detail["message"]
    assert set(app.state.job_store._jobs) == jobs_before


async def test_two_unnamed_copies_in_one_upload_second_item_refused(client) -> None:
    """Names carry no id, so admission passes; the job-time check refuses the
    second copy against the first (the shared per-job ``seen``)."""
    r = await client.post("/v1/ingest/upload", files=[
        ("files", ("one.md", f"DOI: {DOI}\n\n{BODY}".encode(), "text/markdown")),
        ("files", ("two.md", f"DOI: {DOI}/-/\n\n{BODY}".encode(), "text/markdown")),
    ])
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    body = await _poll(client, job_id)
    assert body["items"]["completed"] == 1 and body["items"]["failed"] == 1
    (failed,) = [i for i in app.state.job_store._items[job_id].values() if i.error]
    assert failed.error.startswith(f"duplicate_work: key=doi:{DOI} existing_doc_id=")
    assert len(_doc_ids()) == 1


async def test_shared_collection_scope_finds_the_owners_copy(
    client, principals, _cleanup, tmp_path  # noqa: F811
) -> None:
    """Review fix 2: the owner's chunks are stamped with the OWNER's tenant, so a
    non-owner writer (here an admin, the one non-owner write allowed today) must
    look up with ``count_scope`` — own + public + the owner — or the owner's
    copy is invisible. The 409 names the owner's doc, which the caller can read."""
    from tests.api.test_chunk_cap_status import _h, _install

    await _install("lib", owner="owner")
    src = tmp_path / "a" / "PMC8092463.md"
    src.parent.mkdir()
    src.write_text(EPMC, encoding="utf-8")
    r = await client.post("/v1/ingest", json={"source": str(src), "collection": "lib"},
                          headers=_h("owner"))
    assert r.status_code == 200, r.text
    assert (await _poll(client, r.json()["job_id"], _h("owner")))["status"] == "completed"
    owners = {c.doc_id for c in app.state.text_index._chunks
              if c.metadata.get("tenant_id") == "owner"}
    assert len(owners) == 1
    copy = tmp_path / "b" / "PMC8092463.md"
    copy.parent.mkdir()
    copy.write_text(EPMC, encoding="utf-8")
    r = await client.post("/v1/ingest", json={"source": str(copy), "collection": "lib"},
                          headers=_h("admin"))
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["existing_doc_id"] == owners.pop()
