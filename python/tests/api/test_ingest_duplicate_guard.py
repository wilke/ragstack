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


# --------------------------------------------------------------------------- #
# JATS .xml by reference (#828 PR 5a): the records are keyed by jats.record_key
# --------------------------------------------------------------------------- #


def _jats_article(n: str, **kw) -> str:
    import tests.jats_support as t

    return t._article(n, t.BODY_WITH_FLOATS, **kw)


async def test_path_reingest_of_a_jats_file_is_not_refused_as_its_own_duplicate(
    client, tmp_path
) -> None:
    """``PMC8092463.xml`` names a pmcid, so the admission pre-check runs; the
    indexed records are ``PMC8092463`` and ``PMC8092463#table-1`` (stem-keyed,
    never the file path). A re-ingest of the same file is a replace, not a
    409. MUTATION: drop the JATS keys from ``_source_identity`` → 409."""
    src = tmp_path / "a" / "PMC8092463.xml"
    src.parent.mkdir()
    src.write_text(_jats_article("8092463"), encoding="utf-8")
    r = await client.post("/v1/ingest", json={"source": str(src), "duplicate_policy": "allow"})
    assert r.status_code == 200, r.text
    assert (await _poll(client, r.json()["job_id"]))["status"] == "completed"
    first = _doc_ids()
    assert len(first) == 3  # the article, its table and its figure

    r = await client.post("/v1/ingest", json={"source": str(src)})  # refuse (default)
    assert r.status_code == 200, r.text
    body = await _poll(client, r.json()["job_id"])
    assert body["status"] == "completed"
    assert body["items"] == {"total": 1, "completed": 1, "failed": 0, "pending": 0}
    assert _doc_ids() == first
    assert all("duplicate_of" not in c.metadata for c in app.state.text_index._chunks)


async def test_a_fresh_jats_ingest_keeps_its_tables_and_figures_under_refuse(
    client, tmp_path
) -> None:
    """Every record of one JATS file carries the article's doi/pmcid, so the
    job-time guard used to refuse the table and figure records as duplicates
    of their own article: under the deployment default (``refuse``) a JATS
    file indexed ONE document of three, with the item ``completed``. They are
    one work (``same_record_family``). MUTATION: drop the sibling rule from
    ``_first_match`` → 1 document."""
    src = tmp_path / "PMC8092463.xml"
    src.write_text(_jats_article("8092463"), encoding="utf-8")
    r = await client.post("/v1/ingest", json={"source": str(src)})  # refuse (default)
    assert r.status_code == 200, r.text
    body = await _poll(client, r.json()["job_id"])
    assert body["status"] == "completed"
    sources = {c.metadata.get("source_path") for c in app.state.text_index._chunks}
    assert sources == {"PMC8092463", "PMC8092463#table-1", "PMC8092463#figure-1"}
    # …while another file carrying the same work is still refused.
    other = tmp_path / "copy.md"
    other.write_text(f"DOI: 10.1234/synth.8092463\n\n{BODY}", encoding="utf-8")
    r = await client.post("/v1/ingest", json={"source": str(other)})
    body = await _poll(client, r.json()["job_id"])
    assert body["status"] == "failed"
    assert _item(r.json()["job_id"]).error.startswith("duplicate_work: key=doi:10.1234/synth.8092463")


# --------------------------------------------------------------------------- #
# #975 review: record keys (F1), upload identity (F6), symlinks (F7a), F8
# --------------------------------------------------------------------------- #


async def _upload_xml(client, name: str, xml: str, **form):
    return await client.post(
        "/v1/ingest/upload", files=[("files", (name, xml.encode(), "application/xml"))],
        data=form,
    )


def _chunks_by_doi() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for c in app.state.text_index._chunks:
        out.setdefault(str(c.metadata.get("doi")), set()).add(str(c.metadata.get("source_path")))
    return out


async def test_two_uploads_of_one_name_are_two_documents(client) -> None:
    """#975 round 2: two different works both uploaded as ``paper.xml`` (no PMC
    id) shared the key ``paper``, so the second replaced the first. A non-PMC
    JATS file is keyed on its path now (``record_key`` step 3), and a staged
    upload's path is unique per job — exactly as for a PDF upload: two works,
    six documents, nothing replaced. MUTATION: step 3 back to the bare name →
    the second upload replaces the first."""
    r = await _upload_xml(client, "paper.xml",
                          _jats_article("1", pmc=False, doi="10.1234/work.a"))
    assert r.status_code == 202, r.text
    assert (await _poll(client, r.json()["job_id"]))["status"] == "completed"
    r = await _upload_xml(client, "paper.xml",
                          _jats_article("2", pmc=False, doi="10.1234/work.b"))
    assert r.status_code == 202, r.text
    assert (await _poll(client, r.json()["job_id"]))["status"] == "completed"
    by_doi = _chunks_by_doi()
    assert set(by_doi) == {"10.1234/work.a", "10.1234/work.b"}
    assert all(len(v) == 3 for v in by_doi.values())
    assert len(_doc_ids()) == 6
    a, b = (sorted(by_doi[d]) for d in ("10.1234/work.a", "10.1234/work.b"))
    assert a[0].endswith("/paper.xml") and b[0].endswith("/paper.xml") and a[0] != b[0]


async def test_a_reupload_of_a_non_pmc_jats_file_is_a_duplicate_like_a_pdf(client) -> None:
    """The same non-PMC ``paper.xml`` uploaded twice: a second copy (new staged
    path), so under ``refuse`` the job-time guard refuses it on its DOI — every
    record of it, so nothing of the copy is indexed — and under ``allow`` it
    is admitted and stamped ``duplicate_of``, as a re-uploaded PDF is."""
    xml = _jats_article("1", pmc=False, doi="10.1234/work.a")
    r = await _upload_xml(client, "paper.xml", xml)
    assert (await _poll(client, r.json()["job_id"]))["status"] == "completed"
    first = _doc_ids()
    r = await _upload_xml(client, "paper.xml", xml)
    job_id = r.json()["job_id"]
    body = await _poll(client, job_id)
    assert body["status"] == "failed"
    assert _item(job_id).error.startswith("duplicate_work: key=doi:10.1234/work.a")
    assert _doc_ids() == first
    r = await _upload_xml(client, "paper.xml", xml, duplicate_policy="allow")
    assert (await _poll(client, r.json()["job_id"]))["status"] == "completed"
    copy = [c for c in app.state.text_index._chunks if c.doc_id not in first]
    assert len({c.doc_id for c in copy}) == 3
    assert {c.metadata["duplicate_of"] for c in copy} <= first


async def test_a_text_file_whose_doi_changed_is_still_replaced_in_place(
    client, tmp_path
) -> None:
    """#975 round 2 (1)/N2: the round-1 id-collision check refused a
    path-keyed re-ingest whose DOI had changed (a corrected file at the same
    path, a different enrichment answer) under the default ``refuse`` — the
    contract is "re-ingesting the same source replaces it". The check is gone.
    RED at e67bd38 (the item failed with the collision error)."""
    src = tmp_path / "latest.md"
    src.write_text(f"DOI: 10.1234/first.version\n\n{BODY}", encoding="utf-8")
    r = await client.post("/v1/ingest", json={"source": str(src)})
    assert (await _poll(client, r.json()["job_id"]))["status"] == "completed"
    (doc_id,) = _doc_ids()
    src.write_text(f"DOI: 10.1234/corrected.version\n\n{BODY}", encoding="utf-8")
    r = await client.post("/v1/ingest", json={"source": str(src)})  # refuse (default)
    body = await _poll(client, r.json()["job_id"])
    assert body["status"] == "completed", _item(r.json()["job_id"]).error
    assert _doc_ids() == {doc_id}
    assert {c.metadata.get("doi") for c in app.state.text_index._chunks} == {
        "10.1234/corrected.version"}


async def test_same_named_files_in_two_directories_keep_both_articles(client, tmp_path) -> None:
    """argo N1(b): ``a/main.xml`` and ``b/main.xml`` (no PMC id, different
    DOIs) in ONE directory ingest shared every id, so the second shard
    overwrote (or mixed with) the first — the guard, deciding per document,
    could not see it. Keyed on their paths, they cannot collide. MUTATION:
    step 3 back to the bare name → 3 documents, not 6."""
    for d, n in (("a", "1"), ("b", "2")):
        f = tmp_path / "corpus" / d / "main.xml"
        f.parent.mkdir(parents=True)
        f.write_text(_jats_article(n, pmc=False), encoding="utf-8")
    r = await client.post("/v1/ingest", json={"source": str(tmp_path / "corpus")})
    body = await _poll(client, r.json()["job_id"])
    assert body["status"] == "completed"
    assert body["items"] == {"total": 2, "completed": 2, "failed": 0, "pending": 0}
    assert len(_doc_ids()) == 6
    assert set(_chunks_by_doi()) == {"10.1234/synth.1", "10.1234/synth.2"}


async def test_a_multi_unit_article_is_admitted_or_refused_whole(client, tmp_path) -> None:
    """argo N1(a), with collisions gone: the per-document guard must never
    split one JATS source. (1) A fresh ingest under ``refuse`` indexes every
    record — the units are not duplicates of their own article; (2) a
    re-ingest of the same file under ``refuse`` replaces every record, none
    refused against last time's siblings; (3) another file holding the same
    DOI is refused on EVERY record and the item fails — no unit of it is
    admitted next to the first article. MUTATION: drop the family rule from
    ``_first_match`` → (1) indexes one record of five."""
    import tests.jats_support as t

    body = (t.BODY_WITH_FLOATS + "<sec><title>More</title><p>More prose here.</p>"
            + t.TABLE_1.replace('id="T1"', 'id="T2"').replace("Table 1", "Table 2")
            + t.FIG_1.replace('id="F1"', 'id="F2"').replace("Figure 1", "Figure 2")
            + "</sec>")
    xml = t._article("5", body, pmc=False, doi="10.1234/multi.unit")
    src = tmp_path / "one" / "article.xml"
    src.parent.mkdir()
    src.write_text(xml, encoding="utf-8")
    key = str(src.resolve())
    expected = {key, *(f"{key}#{u}" for u in ("table-1", "table-2", "figure-1", "figure-2"))}
    for _ in range(2):  # (1) fresh, (2) re-ingest — both under refuse
        r = await client.post("/v1/ingest", json={"source": str(src)})
        b = await _poll(client, r.json()["job_id"])
        assert b["status"] == "completed", _item(r.json()["job_id"]).error
        assert {c.metadata.get("source_path") for c in app.state.text_index._chunks} == expected
    first = _doc_ids()
    assert len(first) == 5
    # (3) a different file (another path) carrying the same DOI
    other = tmp_path / "two" / "article.xml"
    other.parent.mkdir()
    other.write_text(xml, encoding="utf-8")
    r = await client.post("/v1/ingest", json={"source": str(other)})
    job_id = r.json()["job_id"]
    b = await _poll(client, job_id)
    assert b["status"] == "failed"
    assert b["items"] == {"total": 1, "completed": 0, "failed": 1, "pending": 0}
    assert _item(job_id).error.startswith("duplicate_work: key=doi:10.1234/multi.unit")
    assert _doc_ids() == first


async def test_reupload_of_the_same_pmc_file_replaces_it(client) -> None:
    """F6 + the reverse safety of F1(ii): re-uploading ``PMC8092463.xml`` (same
    DOI) was a 409 at upload time (the name carries a pmcid, and only the
    by-reference path had the JATS identity) although the job replaces. Now
    no 409, the job completes, and the ids are the same — no second copy.
    MUTATION: drop ``same_document`` at the local upload site → 409."""
    xml = _jats_article("8092463")
    r = await _upload_xml(client, "PMC8092463.xml", xml)
    assert r.status_code == 202, r.text
    assert (await _poll(client, r.json()["job_id"]))["status"] == "completed"
    first = _doc_ids()
    assert len(first) == 3

    r = await _upload_xml(client, "PMC8092463.xml", xml)
    assert r.status_code == 202, r.text
    body = await _poll(client, r.json()["job_id"])
    assert body["status"] == "completed"
    assert body["items"] == {"total": 1, "completed": 1, "failed": 0, "pending": 0}
    assert _doc_ids() == first
    assert all("duplicate_of" not in c.metadata for c in app.state.text_index._chunks)
    # …while a Markdown file of that name is still a second copy: 409.
    r = await _upload_md(client, "PMC8092463.md", EPMC)
    assert r.status_code == 409, r.text


async def test_directory_ingest_of_plos_names_keeps_every_article(client, tmp_path) -> None:
    """F1(i): ``journal.pone.0012345.xml`` and ``journal.pone.0067890.xml`` both
    keyed to ``journal`` and overwrote each other. Each is keyed on its path
    now. MUTATION: first-dot keys → 3 documents, not 6."""
    d = tmp_path / "plos"
    d.mkdir()
    (d / "journal.pone.0012345.xml").write_text(_jats_article("1", pmc=False), encoding="utf-8")
    (d / "journal.pone.0067890.xml").write_text(_jats_article("2", pmc=False), encoding="utf-8")
    r = await client.post("/v1/ingest", json={"source": str(d)})  # refuse (default)
    assert r.status_code == 200, r.text
    body = await _poll(client, r.json()["job_id"])
    assert body["status"] == "completed"
    assert body["items"] == {"total": 2, "completed": 2, "failed": 0, "pending": 0}
    sources = {c.metadata.get("source_path") for c in app.state.text_index._chunks}
    assert sources == {
        f"{(d / f'journal.pone.00{n}.xml').resolve()}{suffix}"
        for n in ("12345", "67890") for suffix in ("", "#table-1", "#figure-1")
    }
    assert len(_doc_ids()) == 6


async def test_a_dotfile_jats_keeps_its_tables_and_figures(client, tmp_path) -> None:
    """F1(iii): ``.hidden.xml`` keyed to ``""``, so under ``refuse`` its table
    and figure were refused as duplicates of their own article."""
    src = tmp_path / ".hidden.xml"
    src.write_text(_jats_article("3", pmc=False), encoding="utf-8")
    r = await client.post("/v1/ingest", json={"source": str(src)})
    body = await _poll(client, r.json()["job_id"])
    assert body["status"] == "completed"
    key = str(src.resolve())
    sources = {c.metadata.get("source_path") for c in app.state.text_index._chunks}
    assert sources == {key, f"{key}#table-1", f"{key}#figure-1"}


async def test_source_identity_keys_a_versioned_name_on_its_pmc_stem() -> None:
    """F8: ``PMC5.v2.xml`` is keyed ``PMC5`` (the first-dot stem), as
    JatsLoader keys it. MUTATION: ``Path(...).stem`` → ``PMC5.v2``."""
    from ragstack.api.routers.documents import _source_identity
    from ragstack.ingestion.loaders import deterministic_doc_id

    ident = await _source_identity("does/not/exist/PMC5.v2.xml")
    assert {"PMC5", deterministic_doc_id("PMC5")} <= ident
    assert "PMC5.v2" not in ident


async def test_source_identity_follows_a_symlink_as_the_loader_does(tmp_path) -> None:
    """F7(a): JatsLoader gets the RESOLVED path, so ``PMC4.xml →
    store/abc123.xml`` (an article without a PMC id) is keyed on the target's
    path; the API's same-document identity must name the same id, or a
    re-ingest of the link is refused as a duplicate of itself."""
    import tests.jats_support as t
    from ragstack.api.routers.documents import _source_identity
    from ragstack.ingestion.loaders import default_loader_registry

    store = tmp_path / "store"
    store.mkdir()
    (store / "abc123.xml").write_text(t._article("4", t.BODY_WITH_FLOATS, pmc=False),
                                      encoding="utf-8")
    link = tmp_path / "PMC4.xml"
    link.symlink_to(store / "abc123.xml")
    docs = default_loader_registry(ingest_root=str(tmp_path)).load(str(link))
    assert docs[0].source == str((store / "abc123.xml").resolve())
    ident = await _source_identity(str(link))
    assert docs[0].id in ident and docs[0].source in ident
    assert "PMC4" not in ident


async def test_a_path_keyed_reingest_whose_name_carries_a_pmcid_is_not_a_409(
    client, tmp_path
) -> None:
    """``notes_PMC7000005.xml`` with no PMC id in the article: the name gives
    the pre-check a key (pmcid), the records are path-keyed and carry that
    pmcid (filled from the name), so the pre-check finds them — and must
    recognise them, and their units by family, as this source."""
    src = tmp_path / "notes_PMC7000005.xml"
    src.write_text(_jats_article("7000005", pmc=False), encoding="utf-8")
    r = await client.post("/v1/ingest", json={"source": str(src)})
    assert (await _poll(client, r.json()["job_id"]))["status"] == "completed"
    first = _doc_ids()
    assert len(first) == 3
    assert {c.metadata.get("pmcid") for c in app.state.text_index._chunks} == {"PMC7000005"}
    r = await client.post("/v1/ingest", json={"source": str(src)})
    assert r.status_code == 200, r.text
    assert (await _poll(client, r.json()["job_id"]))["status"] == "completed"
    assert _doc_ids() == first


async def test_a_step_2_reingest_reads_the_articles_pmc_id_lazily(
    client, tmp_path, monkeypatch
) -> None:
    """A non-PMC name whose ARTICLE carries a PMC id (step 2): the records are
    ``PMC7004242*``, and only reading the file tells the pre-check so. #975
    round 2 (5)/N3: that read used to run eagerly, on the event loop, before
    confinement and with no size cap, on every by-reference ingest of a
    non-PMC XML name — whatever the policy. Now it runs only when the
    pre-check actually has a hit to compare, confined, size-capped, streamed
    and in a worker thread. MUTATION: pass ``_source_identity(...)`` eagerly
    → parse calls under ``allow`` (and on the loop)."""
    import threading

    import ragstack.api.routers.documents as documents
    from ragstack.config import settings

    calls: list[tuple[str, bool]] = []
    real = documents.scan_article_pmcid

    def spy(path):
        calls.append((str(path), threading.current_thread() is threading.main_thread()))
        return real(path)

    monkeypatch.setattr(documents, "scan_article_pmcid", spy)
    src = tmp_path / "notes_PMC7004242.xml"
    src.write_text(_jats_article("7004242"), encoding="utf-8")

    async def ingest(**kw) -> dict:
        r = await client.post("/v1/ingest", json={"source": str(src), **kw})
        assert r.status_code == 200, r.text
        return await _poll(client, r.json()["job_id"])

    assert (await ingest(duplicate_policy="allow"))["status"] == "completed"
    assert (await ingest(duplicate_policy="allow"))["status"] == "completed"
    assert calls == []  # allow: never read at the pre-check
    assert {c.metadata.get("source_path") for c in app.state.text_index._chunks} == {
        "PMC7004242", "PMC7004242#table-1", "PMC7004242#figure-1"}
    first = _doc_ids()
    assert (await ingest())["status"] == "completed"  # refuse: read once, off the loop
    assert calls == [(str(src.resolve()), False)]
    assert _doc_ids() == first
    # Over the size cap: not read (the job refuses the file anyway).
    calls.clear()
    monkeypatch.setattr(settings, "max_document_bytes", 10)
    r = await client.post("/v1/ingest", json={"source": str(src)})
    assert r.status_code == 409, r.text  # unread → not recognised as this source
    assert calls == []


async def test_the_lazy_pmc_read_is_confined_to_the_ingest_root(tmp_path, monkeypatch) -> None:
    """The pre-check never opens a file outside ``INGEST_ROOT``."""
    import ragstack.api.routers.documents as documents
    from ragstack.config import settings

    calls: list[str] = []
    monkeypatch.setattr(documents, "scan_article_pmcid", lambda p: calls.append(str(p)) or "")
    inside, outside = tmp_path / "root", tmp_path / "elsewhere"
    inside.mkdir()
    outside.mkdir()
    (outside / "x.xml").write_text("<article/>", encoding="utf-8")
    (inside / "x.xml").write_text("<article/>", encoding="utf-8")
    monkeypatch.setattr(settings, "ingest_root", str(inside))
    assert await documents._confined_article_pmcid(str(outside / "x.xml")) == ""
    assert await documents._confined_article_pmcid(str(inside / ".." / "elsewhere" / "x.xml")) == ""
    assert calls == []
    await documents._confined_article_pmcid(str(inside / "x.xml"))
    assert calls == [str((inside / "x.xml").resolve())]


async def test_reingest_of_a_symlinked_jats_file_is_not_a_409(client, tmp_path) -> None:
    """F7(a) through the API: a link named for one PMC id whose target file is
    named for another. The records are keyed on the target (``PMC7000001``),
    carry the article's pmcid (``PMC8092464``, what the link's name says), so
    the pre-check finds them by the link's name — and must recognise them as
    this source. MUTATION: key the link's own name → 409."""
    store = tmp_path / "store"
    store.mkdir()
    (store / "PMC7000001.xml").write_text(_jats_article("8092464"), encoding="utf-8")
    link = tmp_path / "PMC8092464.xml"
    link.symlink_to(store / "PMC7000001.xml")
    r = await client.post("/v1/ingest", json={"source": str(link)})
    assert (await _poll(client, r.json()["job_id"]))["status"] == "completed"
    first = _doc_ids()
    assert {c.metadata.get("source_path") for c in app.state.text_index._chunks} == {
        "PMC7000001", "PMC7000001#table-1", "PMC7000001#figure-1"}
    r = await client.post("/v1/ingest", json={"source": str(link)})
    assert r.status_code == 200, r.text
    assert (await _poll(client, r.json()["job_id"]))["status"] == "completed"
    assert _doc_ids() == first


async def _seed_jats_for(tenant: str, key: str = "PMC8092463") -> set[str]:
    """Index JATS-shaped records (``key``, ``key#table-1``) for ``tenant``."""
    from ragstack.ingestion.loaders import deterministic_doc_id
    from ragstack.models import Chunk

    chunks = [Chunk(id=f"c{i}", doc_id=deterministic_doc_id(src), content=BODY,
                    metadata={"tenant_id": tenant, "pmcid": key, "source_path": src})
              for i, src in enumerate((key, f"{key}#table-1"))]
    await app.state.text_index.index(chunks)
    return {c.doc_id for c in chunks}


async def test_gowe_reupload_of_a_jats_file_is_not_a_409(client, gowe) -> None:  # noqa: F811
    """F6 on the gowe upload and Workspace-reference sites: the same JATS
    identity. MUTATION: drop ``same_document`` at either site → 409."""
    await _seed_jats_for(TENANT)
    r = await client.post(
        "/v1/ingest/upload", data={"collection": "lib1"}, headers=AUTH,
        files=[("files", ("PMC8092463.xml", _jats_article("8092463").encode(),
                          "application/xml"))],
    )
    assert r.status_code == 202, r.text
    src = f"ws:///{TENANT.split(':', 1)[1]}/home/papers/PMC8092463.xml"
    r = await client.post("/v1/ingest", json={"source": src, "collection": "lib1"},
                          headers=AUTH)
    assert r.status_code in (200, 202), r.text
    # The PDF of that name is still a second copy.
    r = await _upload(client, "PMC8092463.pdf")
    assert r.status_code == 409, r.text
