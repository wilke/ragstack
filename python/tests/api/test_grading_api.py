"""``/v1/grading`` in process — the independence rules, and the create-time gate.

``conformance/test_grading.py`` is the black-box statement of this surface and
runs the whole scripted read over HTTP. These tests are the in-process
complement: they cover what conformance cannot reach cheaply — the create-time
422s (a span outside its document, a duplicate ``pair_id``, a group-form reader)
— and they re-state the independence rules against a second, differently-built
server so a rule that holds only because of the keyed boot's topology would show.

The personas mirror the keyed conformance boot: an admin, two readers, and an
authenticated principal on no batch.
"""
from __future__ import annotations

import random

import pytest

from ragstack.api import security
from ragstack.api.main import app
from ragstack.api.security import ROLE_ADMIN, ROLE_USER
from ragstack.grading.store import InMemoryGradingStore

pytestmark = pytest.mark.asyncio

ADMIN, READER_A, READER_B, OUTSIDER = "conf-admin", "reader-a", "reader-b", "outsider"
H_ADMIN = {"X-API-Key": "k-admin"}
H_A = {"X-API-Key": "k-a"}
H_B = {"X-API-Key": "k-b"}
H_OUT = {"X-API-Key": "k-out"}

RUBRIC = "2e11f3688de916da8bfc8b5b0a788050bf9d077960d616d33490c6ecf747363b"
SEED = 4242


@pytest.fixture(autouse=True)
def _principals(monkeypatch):
    """Four distinct keys → four distinct subjects, one of them admin."""
    monkeypatch.setattr(security.settings, "api_keys", ["k-admin", "k-a", "k-b", "k-out"])
    monkeypatch.setattr(security.settings, "api_key_roles", {"k-admin": ROLE_ADMIN})
    monkeypatch.setattr(
        security.settings,
        "api_key_tenants",
        {"k-admin": ADMIN, "k-a": READER_A, "k-b": READER_B, "k-out": OUTSIDER},
    )
    monkeypatch.setattr(security.settings, "default_role", ROLE_USER)


@pytest.fixture(autouse=True)
def _grading_store():
    """A fresh store per test. ASGITransport skips the lifespan, so nothing else
    installs one and a leftover batch would leak between tests."""
    app.state.grading_store = InMemoryGradingStore()
    yield
    app.state.grading_store = None


def _document(n: int, units: int = 2) -> dict:
    return {
        "doc_id": f"doc-{n}",
        "title": f"Document {n}",
        "units": [
            {
                "index": u,
                "title": ["Abstract", "Results"][u % 2],
                "sentences": [{"i": i, "text": f"Unit {u} sentence {i}."} for i in range(3)],
            }
            for u in range(units)
        ],
    }


def _task(n: int, *, claims: list | None = None, extra: list | None = None) -> dict:
    return {
        "pair_id": f"pair-{n}",
        "stratum": "model_positive",
        "question": {
            "id": f"topic-{n}", "type": "diagnosis", "summary": "s", "description": "d"
        },
        "document": _document(n),
        "claims": claims
        if claims is not None
        else [
            {
                "set_index": 1,
                "spans": [
                    {"unit": 0, "first_sentence": 0, "last_sentence": 1, "text": "quote"}
                ],
                "sources": ["scout"],
            }
        ],
        "extra_questions": extra or [],
    }


def _create_body(n_tasks: int = 4, **over) -> dict:
    body = {
        "name": "unit-read",
        "kind": "evidence-read",
        "rubric_sha256": RUBRIC,
        "order_seed": SEED,
        "readers": [f"@service:{READER_A}", f"@service:{READER_B}"],
        "tasks": [_task(i) for i in range(n_tasks)],
    }
    body.update(over)
    return body


async def _create(client, **over) -> dict:
    resp = await client.post("/v1/grading/batches", json=_create_body(**over), headers=H_ADMIN)
    assert resp.status_code == 201, resp.text
    return resp.json()


# --------------------------------------------------------------------------- #
# Create: what is refused, and what nothing is created for
# --------------------------------------------------------------------------- #
async def test_create_echoes_resolved_readers_in_label_order(client):
    batch = await _create(client)
    assert batch["readers"] == [READER_A, READER_B], (
        "'@service:<subject>' must keep the subject colon-free — a bare name "
        "would be qualified to 'bvbrc:<name>', an identity the key never "
        "authenticates as, and the reader would 404 on their own batch"
    )
    assert [p["label"] for p in batch["progress"]] == ["A", "B"]
    assert batch["status"] == "open" and batch["adjudicating_at"] == ""
    assert batch["created_by"] == ADMIN and batch["task_count"] == 4


async def test_a_non_admin_cannot_create(client):
    for headers in (H_A, H_OUT):
        resp = await client.post("/v1/grading/batches", json=_create_body(), headers=headers)
        assert resp.status_code == 403, resp.text


@pytest.mark.parametrize(
    ("over", "needle"),
    [
        pytest.param(
            {"tasks": [_task(0), _task(0)]}, "duplicate pair_id", id="duplicate pair_id"
        ),
        pytest.param(
            {"readers": ["@public", f"@service:{READER_B}"]},
            "not a group",
            id="group-form reader",
        ),
        pytest.param(
            {"readers": [f"@service:{READER_A}", f"@service:{READER_A}"]},
            "already reader A",
            id="two readers, one subject",
        ),
    ],
)
async def test_create_refuses_and_creates_nothing(client, over, needle):
    resp = await client.post("/v1/grading/batches", json=_create_body(**over), headers=H_ADMIN)
    assert resp.status_code == 422, resp.text
    assert needle in resp.json()["detail"]
    listing = await client.get("/v1/grading/batches", headers=H_ADMIN)
    assert listing.json()["batches"] == [], "a refused create must leave nothing behind"


@pytest.mark.parametrize(
    ("span", "needle"),
    [
        pytest.param({"unit": 9, "first_sentence": 0, "last_sentence": 0, "text": "q"},
                     "names unit 9", id="unit outside the document"),
        pytest.param({"unit": 0, "first_sentence": 0, "last_sentence": 9, "text": "q"},
                     "names sentence 9", id="sentence outside the unit"),
        pytest.param({"unit": 0, "first_sentence": 2, "last_sentence": 1, "text": "q"},
                     "first_sentence 2 > last_sentence 1", id="reversed range"),
    ],
)
async def test_a_span_outside_its_document_is_refused(client, span, needle):
    """Not cosmetic: a span the UI cannot render is a highlight the reader never
    sees, on a pair they would nonetheless grade."""
    tasks = [_task(0, claims=[{"set_index": 1, "spans": [span], "sources": ["scout"]}])]
    resp = await client.post(
        "/v1/grading/batches", json=_create_body(tasks=tasks), headers=H_ADMIN
    )
    assert resp.status_code == 422, resp.text
    assert needle in resp.json()["detail"]


async def test_an_empty_claims_list_is_a_legal_task(client):
    """The labeler's 'no localizable evidence' verdict — the pair a reader
    confirms with `correctly-none` or overturns with `missed-evidence`."""
    batch = await _create(client, tasks=[_task(0, claims=[])])
    tasks = (await client.get(f"/v1/grading/batches/{batch['id']}/tasks", headers=H_A)).json()
    assert tasks["tasks"][0]["claims"] == []


# --------------------------------------------------------------------------- #
# The order
# --------------------------------------------------------------------------- #
async def test_each_reader_gets_their_own_seeded_order(client):
    batch = await _create(client, n_tasks=6)
    a = (await client.get(f"/v1/grading/batches/{batch['id']}/tasks", headers=H_A)).json()
    b = (await client.get(f"/v1/grading/batches/{batch['id']}/tasks", headers=H_B)).json()
    admin = (
        await client.get(f"/v1/grading/batches/{batch['id']}/tasks", headers=H_ADMIN)
    ).json()

    batch_order = [t["pair_id"] for t in admin["tasks"]]
    assert batch_order == [f"pair-{i}" for i in range(6)], "an admin gets batch order"
    assert admin["reader"] is None

    for k, listing in ((0, a), (1, b)):
        perm = list(range(6))
        random.Random(SEED + k + 1).shuffle(perm)
        assert [t["pair_id"] for t in listing["tasks"]] == [batch_order[i] for i in perm]
    assert a["reader"] == READER_A and b["reader"] == READER_B
    assert [t["pair_id"] for t in a["tasks"]] != [t["pair_id"] for t in b["tasks"]]


# --------------------------------------------------------------------------- #
# Independence
# --------------------------------------------------------------------------- #
async def _save(client, task_id: str, headers: dict, **body):
    resp = await client.put(
        f"/v1/grading/tasks/{task_id}/verdict", json=body, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_a_reader_never_receives_another_readers_row(client):
    batch = await _create(client)
    tasks = (await client.get(f"/v1/grading/batches/{batch['id']}/tasks", headers=H_A)).json()
    t0 = tasks["tasks"][0]["id"]

    await _save(client, t0, H_A, verdict="correct", notes="A's reading")
    await _save(client, t0, H_B, verdict="wrong-location", notes="B's reading")

    a_task = (await client.get(f"/v1/grading/tasks/{t0}", headers=H_A)).json()
    b_task = (await client.get(f"/v1/grading/tasks/{t0}", headers=H_B)).json()
    assert a_task["verdict"]["notes"] == "A's reading"
    assert b_task["verdict"]["notes"] == "B's reading"
    for t in (a_task, b_task):
        assert "reader_verdicts" not in t and "adjudication" not in t

    # Naming the other reader's row is the SAME 404 as naming a stranger's.
    assert (
        await client.get(f"/v1/grading/tasks/{t0}/verdicts/{READER_A}", headers=H_B)
    ).status_code == 404
    assert (
        await client.get(f"/v1/grading/tasks/{t0}/verdicts/{OUTSIDER}", headers=H_B)
    ).status_code == 404
    own = await client.get(f"/v1/grading/tasks/{t0}/verdicts/{READER_B}", headers=H_B)
    assert own.status_code == 200 and own.json()["notes"] == "B's reading"


async def test_progress_is_counts_and_nothing_else(client):
    batch = await _create(client)
    tasks = (await client.get(f"/v1/grading/batches/{batch['id']}/tasks", headers=H_A)).json()
    ids = [t["id"] for t in tasks["tasks"]]
    await _save(client, ids[0], H_A, verdict="correct")
    await _save(client, ids[1], H_A, verdict="ambiguous")
    await _save(client, ids[0], H_B, verdict="correct")

    seen = (await client.get(f"/v1/grading/batches/{batch['id']}", headers=H_A)).json()
    assert {p["label"]: p["saved"] for p in seen["progress"]} == {"A": 2, "B": 1}
    assert all(set(p) == {"reader", "label", "saved"} for p in seen["progress"])


async def test_an_admin_who_is_not_a_reader_has_no_row_to_write(client):
    batch = await _create(client)
    tasks = (await client.get(f"/v1/grading/batches/{batch['id']}/tasks", headers=H_ADMIN)).json()
    t0 = tasks["tasks"][0]["id"]
    resp = await client.put(
        f"/v1/grading/tasks/{t0}/verdict", json={"verdict": "correct"}, headers=H_ADMIN
    )
    assert resp.status_code == 403, resp.text
    assert tasks["tasks"][0]["verdict"] is None
    assert "reader_verdicts" not in tasks["tasks"][0]


async def test_an_admin_cannot_read_reader_rows_or_export_while_open(client):
    batch = await _create(client)
    tasks = (await client.get(f"/v1/grading/batches/{batch['id']}/tasks", headers=H_A)).json()
    t0 = tasks["tasks"][0]["id"]
    await _save(client, t0, H_A, verdict="correct")

    assert (
        await client.get(f"/v1/grading/tasks/{t0}/verdicts/{READER_A}", headers=H_ADMIN)
    ).status_code == 404
    assert (
        await client.get(f"/v1/grading/batches/{batch['id']}/export", headers=H_ADMIN)
    ).status_code == 409
    assert (
        await client.put(
            f"/v1/grading/tasks/{t0}/adjudication",
            json={"verdict": "correct"},
            headers=H_ADMIN,
        )
    ).status_code == 409


async def test_a_principal_on_no_batch_gets_404_everywhere(client):
    batch = await _create(client)
    bid = batch["id"]
    tasks = (await client.get(f"/v1/grading/batches/{bid}/tasks", headers=H_A)).json()
    t0 = tasks["tasks"][0]["id"]
    probes = [
        await client.get(f"/v1/grading/batches/{bid}", headers=H_OUT),
        await client.get(f"/v1/grading/batches/{bid}/tasks", headers=H_OUT),
        await client.get(f"/v1/grading/tasks/{t0}", headers=H_OUT),
        await client.put(
            f"/v1/grading/tasks/{t0}/verdict", json={"verdict": "correct"}, headers=H_OUT
        ),
        await client.get(f"/v1/grading/tasks/{t0}/verdicts/{READER_A}", headers=H_OUT),
        await client.post(f"/v1/grading/batches/{bid}/adjudicate", headers=H_OUT),
        await client.get(f"/v1/grading/batches/{bid}/export", headers=H_OUT),
        await client.delete(f"/v1/grading/batches/{bid}", headers=H_OUT),
    ]
    assert [p.status_code for p in probes] == [404] * len(probes), (
        "a 403 anywhere here would confirm the batch exists to someone not on it"
    )
    listing = (await client.get("/v1/grading/batches", headers=H_OUT)).json()
    assert listing["batches"] == []
    assert [b["id"] for b in
            (await client.get("/v1/grading/batches", headers=H_A)).json()["batches"]] == [bid]


async def test_a_reader_is_refused_the_admin_actions_with_403(client):
    """403, not 404: reader A can already see this batch, so refusing by role
    tells them nothing new — and a 404 would be a lie."""
    batch = await _create(client)
    bid = batch["id"]
    tasks = (await client.get(f"/v1/grading/batches/{bid}/tasks", headers=H_A)).json()
    t0 = tasks["tasks"][0]["id"]
    refusals = {
        "adjudicate": await client.post(f"/v1/grading/batches/{bid}/adjudicate", headers=H_A),
        "export": await client.get(f"/v1/grading/batches/{bid}/export", headers=H_A),
        "delete": await client.delete(f"/v1/grading/batches/{bid}", headers=H_A),
        "adjudication": await client.put(
            f"/v1/grading/tasks/{t0}/adjudication", json={"verdict": "correct"}, headers=H_A
        ),
    }
    assert {k: r.status_code for k, r in refusals.items()} == dict.fromkeys(refusals, 403)
    # And the delete did not happen.
    assert (await client.get(f"/v1/grading/batches/{bid}", headers=H_A)).status_code == 200


# --------------------------------------------------------------------------- #
# Verdict validation
# --------------------------------------------------------------------------- #
async def test_a_verdict_is_validated_against_the_task(client):
    batch = await _create(
        client,
        tasks=[
            _task(
                0,
                extra=[
                    {
                        "id": "other_passage",
                        "text": "Does another passage answer it?",
                        "answer_type": "yes-no",
                    }
                ],
            )
        ],
    )
    tasks = (await client.get(f"/v1/grading/batches/{batch['id']}/tasks", headers=H_A)).json()
    t0 = tasks["tasks"][0]["id"]
    bad = {
        "verdict outside the vocabulary": {"verdict": "fine"},
        "a span the task lacks": {
            "verdict": "correct",
            "span_judgements": [{"set": 9, "span": 1, "judgement": "located"}],
        },
        "an unknown extra question": {
            "verdict": "correct",
            "extra_answers": [{"id": "nope", "answer": "no"}],
        },
        "a non-yes/no answer": {
            "verdict": "correct",
            "extra_answers": [{"id": "other_passage", "answer": "maybe"}],
        },
        "a field naming another reader": {"verdict": "correct", "reader": READER_B},
    }
    for what, body in bad.items():
        resp = await client.put(
            f"/v1/grading/tasks/{t0}/verdict", json=body, headers=H_A
        )
        assert resp.status_code == 422, f"{what}: {resp.status_code} {resp.text[:200]}"
    # None of them created a row.
    assert (
        await client.get(f"/v1/grading/tasks/{t0}/verdicts/{READER_A}", headers=H_A)
    ).status_code == 404


async def test_a_re_save_is_a_whole_row_replace(client):
    batch = await _create(client)
    tasks = (await client.get(f"/v1/grading/batches/{batch['id']}/tasks", headers=H_A)).json()
    t0 = tasks["tasks"][0]["id"]
    v1 = await _save(
        client, t0, H_A,
        verdict="correct",
        span_judgements=[{"set": 1, "span": 1, "judgement": "located"}],
        notes="first",
    )
    v2 = await _save(client, t0, H_A, verdict="ambiguous")
    assert (v1["version"], v2["version"]) == (1, 2)
    assert v2["span_judgements"] == [] and v2["notes"] == "", (
        "an omitted list CLEARS it; it does not keep the previous value"
    )
    assert v2["reader"] == READER_A


# --------------------------------------------------------------------------- #
# Adjudication and export
# --------------------------------------------------------------------------- #
async def test_adjudicate_freezes_reveals_and_unlocks(client):
    batch = await _create(client, n_tasks=2)
    bid = batch["id"]
    tasks = (await client.get(f"/v1/grading/batches/{bid}/tasks", headers=H_ADMIN)).json()
    t0, t1 = tasks["tasks"][0]["id"], tasks["tasks"][1]["id"]
    await _save(client, t0, H_A, verdict="non-minimal", notes="A")
    await _save(client, t0, H_B, verdict="correct", notes="B")
    await _save(client, t1, H_A, verdict="correctly-none")

    adj = await client.post(f"/v1/grading/batches/{bid}/adjudicate", headers=H_ADMIN)
    assert adj.status_code == 200 and adj.json()["status"] == "adjudicating"
    assert adj.json()["adjudicating_at"]
    # Not idempotent, on purpose: a second click is not a silent no-op.
    assert (
        await client.post(f"/v1/grading/batches/{bid}/adjudicate", headers=H_ADMIN)
    ).status_code == 409
    # Frozen for the readers, visible to the admin, invisible to the other reader.
    assert (
        await client.put(
            f"/v1/grading/tasks/{t0}/verdict", json={"verdict": "correct"}, headers=H_A
        )
    ).status_code == 409
    assert (
        await client.get(f"/v1/grading/tasks/{t0}/verdicts/{READER_A}", headers=H_ADMIN)
    ).status_code == 200
    assert (
        await client.get(f"/v1/grading/tasks/{t0}/verdicts/{READER_A}", headers=H_B)
    ).status_code == 404
    a_task = (await client.get(f"/v1/grading/tasks/{t0}", headers=H_A)).json()
    assert "reader_verdicts" not in a_task, (
        "a reader never receives reader_verdicts, whatever the batch status"
    )

    admin_task = (await client.get(f"/v1/grading/tasks/{t0}", headers=H_ADMIN)).json()
    assert [v["reader"] for v in admin_task["reader_verdicts"]] == [READER_A, READER_B]
    assert admin_task["adjudication"] is None

    saved = await client.put(
        f"/v1/grading/tasks/{t0}/adjudication",
        json={"verdict": "non-minimal", "notes": "joint read"},
        headers=H_ADMIN,
    )
    assert saved.status_code == 200
    assert saved.json()["version"] == 1 and saved.json()["adjudicated_by"] == ADMIN

    # The readers' own rows are untouched by the adjudication.
    a_row = (
        await client.get(f"/v1/grading/tasks/{t0}/verdicts/{READER_A}", headers=H_ADMIN)
    ).json()
    assert a_row["notes"] == "A" and a_row["version"] == 1

    export = (await client.get(f"/v1/grading/batches/{bid}/export", headers=H_ADMIN)).json()
    assert [c["filename"] for c in export["csv"]] == [
        "rdev_verdicts_A.csv",
        "rdev_verdicts_B.csv",
        "rdev_verdicts_ADJ.csv",
    ]
    by_label = {c["label"]: c["content"] for c in export["csv"]}
    assert by_label["A"] == (
        '"pair_id","verdict","notes"\n'
        '"pair-0","non-minimal","A"\n'
        '"pair-1","correctly-none",""\n'
    ), "RFC 4180, every field quoted, one row per task in batch order"
    assert by_label["B"].endswith('"pair-1","",""\n'), (
        "a task B never read is a BLANK verdict cell, not a verdict"
    )
    assert [(r["pair_id"], r["label"]) for r in export["verdicts"]] == [
        ("pair-0", "A"), ("pair-0", "B"), ("pair-1", "A")
    ]
    assert export["verdicts"][0]["stratum"] == "model_positive"
    assert [a["pair_id"] for a in export["adjudications"]] == ["pair-0"]


async def test_deleting_a_batch_removes_it_from_the_listing(client):
    batch = await _create(client)
    assert (
        await client.delete(f"/v1/grading/batches/{batch['id']}", headers=H_ADMIN)
    ).status_code == 204
    listing = (await client.get("/v1/grading/batches", headers=H_ADMIN)).json()
    assert [b["id"] for b in listing["batches"]] == []
    assert (
        await client.get(f"/v1/grading/batches/{batch['id']}", headers=H_ADMIN)
    ).status_code == 404


async def test_a_store_outage_is_a_503_not_a_partial_answer(client, monkeypatch):
    """Fail closed. A 200 that silently omitted a reader's saved row would read,
    on this surface, as 'not read yet' — a wrong κ, not a visible outage."""
    batch = await _create(client)

    async def boom(*_a, **_k):
        raise RuntimeError("store is gone")

    monkeypatch.setattr(app.state.grading_store, "list_verdicts", boom)
    resp = await client.get(f"/v1/grading/batches/{batch['id']}", headers=H_ADMIN)
    assert resp.status_code == 503
    assert "fail closed" in resp.json()["detail"]
