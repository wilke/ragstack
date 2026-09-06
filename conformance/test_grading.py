"""Conformance: the grading resources — reader independence, enforced server-side.

``docs/plans/grading-ui.md`` moves the two-independent-reader label validation
(SPEC-confirmation-run.md §6.6) off a claude.ai artifact, where reader
independence was honour-based, into RAGStack, where it is not. This file is
the black-box statement of what "not honour-based" means, run with the keyed
boot's four distinct principals (``run_authz_keyed.sh``, #405):

=============  ===============================  ==================================
principal      key variable                     role in this file
=============  ===============================  ==================================
admin          ``RAGSTACK_API_KEY_ADMIN``       creates, adjudicates, exports, deletes
reader A       ``RAGSTACK_API_KEY_NONADMIN``    first reader (label ``A``)
reader B       ``RAGSTACK_API_KEY_P2``          second reader (label ``B``)
outsider       ``RAGSTACK_API_KEY_B``           authenticated, on no batch
=============  ===============================  ==================================

**The rules it enforces**, each in a named test:

* a reader sees the batch's tasks in *their own* seeded order, and the two
  readers' orders differ (``GradingBatch``'s rule — CPython's
  ``random.Random(order_seed + k + 1).shuffle``, which this file recomputes);
* a reader's task listing carries *their own* verdict and never another's;
* a reader naming another reader's verdict row gets **404**, the same 404 as
  naming a subject who is not a reader at all; an admin gets that 404 too
  while the batch is ``open``;
* a principal on no batch gets **404** for the batch and everything under it —
  never a 403 that would confirm the batch exists (ADR-0003 §2);
* a non-admin cannot create, adjudicate, export or delete (**403** where they
  can see the batch, because a reader is not being told anything new);
* ``POST …/adjudicate`` freezes the readers' rows (a further PUT is **409**),
  reveals them to the admin, and unlocks the adjudication and the export;
* the export carries one CSV per reader plus the adjudicated one, with every
  task in batch order and exactly the rows the readers saved — checked with
  the acceptance rules of ``s0_rdev_score.py::read_verdicts``.

**How absence is detected.** No implementation serves ``/v1/grading/*`` yet
(phase 1 is the contract). ``GET /v1/grading/batches`` as the suite's default
principal answering **404** means the resource is not mounted, and the whole
module skips with *grading not implemented on this server*. That is
deliberately NOT a credential skip: the keyed run fails on any
``RAGSTACK_CREDENTIAL_SKIP``, and a server that has no grading at all is a
legitimate absence, not a provisioning bug. The credential checks run only
once the surface is known to exist, so a keyed server that implements grading
and lacks a principal fails the run — as it should.

**The scenario runs once, up front.** ``scenario`` (session-scoped, synchronous)
performs the entire scripted read — create, two orders, saves, re-save, the
negative probes that need the batch still ``open``, adjudicate, the joint
verdict, export — and records every response. The tests are assertions over
that record plus read-only probes of the final state, so no test depends on
another having run first, and a failure names the step. Teardown deletes the
batch as admin and verifies by listing, never by trusting the DELETE's status.
"""

from __future__ import annotations

import csv
import io
import json
import random
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import httpx
import jsonschema
import pytest
from conftest import key, skip_no_credential

pytestmark = [pytest.mark.asyncio, pytest.mark.grading]

NOT_IMPLEMENTED = "grading not implemented on this server (GET /v1/grading/batches -> 404)"

#: SPEC-confirmation-run.md §6.6.2 — byte-for-byte ``s0_rdev_score.VERDICTS``.
VERDICTS = (
    "correct",
    "wrong-location",
    "non-minimal",
    "missed-evidence",
    "correctly-none",
    "ambiguous",
)
#: sha256 of docs/plans/results/design/RUBRIC-evidence.md at the time of writing;
#: any 64-hex value satisfies the contract, this one is at least a real rubric.
RUBRIC_SHA256 = "2e11f3688de916da8bfc8b5b0a788050bf9d077960d616d33490c6ecf747363b"
#: With six tasks, the two readers' orders differ under the contract's rule;
#: the scenario asserts that rather than assuming it.
ORDER_SEED = 4242
TASK_COUNT = 6
#: Batch name / pair_id prefix, so litter on a real server is unmistakably ours.
PREFIX = "conf-grading-"


# --------------------------------------------------------------------------- #
# Task material — small documents, real shapes
# --------------------------------------------------------------------------- #
def _document(n: int) -> dict[str, Any]:
    sentences = [
        f"Sentence {i} of the conformance grading document {n}." for i in range(5)
    ]
    return {
        "doc_id": f"{PREFIX}doc-{n}",
        "title": f"Conformance grading document {n}",
        "units": [
            {
                "index": 0,
                "title": "Abstract",
                "sentences": [{"i": i, "text": s} for i, s in enumerate(sentences)],
            },
            {
                "index": 1,
                "title": "Results",
                "sentences": [
                    {"i": 0, "text": f"Result sentence 0 of document {n}."},
                    {"i": 1, "text": f"Result sentence 1 of document {n}."},
                ],
            },
        ],
    }


def _question(n: int) -> dict[str, Any]:
    return {
        "id": f"{PREFIX}topic-{n}",
        "type": "diagnosis",
        "summary": f"Conformance case {n}.",
        "description": f"A conformance case {n}, described at length.",
    }


def _positive(n: int) -> dict[str, Any]:
    """Two evidence sets from two labelers — the union shape the pilot sheet shows."""
    return {
        "pair_id": f"{PREFIX}pair-{n}",
        "stratum": "model_positive",
        "question": _question(n),
        "document": _document(n),
        "claims": [
            {
                "set_index": 1,
                "spans": [
                    {
                        "unit": 0,
                        "first_sentence": 1,
                        "last_sentence": 2,
                        "text": "Sentence 1 … Sentence 2.",
                    }
                ],
                "sources": ["scout"],
            },
            {
                "set_index": 2,
                "spans": [
                    {"unit": 1, "first_sentence": 0, "last_sentence": 0, "text": "Result sentence 0."}
                ],
                "sources": ["qwen"],
            },
        ],
        "extra_questions": [],
    }


def _none(n: int) -> dict[str, Any]:
    """The labeler's 'no localizable evidence' verdict, with r3 §11's extra question."""
    return {
        "pair_id": f"{PREFIX}pair-{n}",
        "stratum": "model_negative",
        "question": _question(n),
        "document": _document(n),
        "claims": [],
        "extra_questions": [
            {
                "id": "other_passage",
                "text": "Does a passage other than any claimed above answer the question?",
                "answer_type": "yes-no",
            }
        ],
    }


def _create_body(readers: list[str]) -> dict[str, Any]:
    tasks = [_positive(0), _none(1)] + [_positive(n) for n in range(2, TASK_COUNT)]
    return {
        "name": f"{PREFIX}scenario",
        "kind": "evidence-read",
        "rubric_sha256": RUBRIC_SHA256,
        "order_seed": ORDER_SEED,
        "readers": readers,
        "tasks": tasks,
    }


def expected_order(pair_ids_in_batch_order: list[str], reader_index: int) -> list[str]:
    """The contract's rule, recomputed: ``random.Random(seed + k + 1).shuffle``."""
    order = list(range(len(pair_ids_in_batch_order)))
    random.Random(ORDER_SEED + reader_index + 1).shuffle(order)
    return [pair_ids_in_batch_order[i] for i in order]


# --------------------------------------------------------------------------- #
# Absence, then principals
# --------------------------------------------------------------------------- #
def _headers(name: str) -> dict[str, str]:
    k = key(name)
    return {"X-API-Key": k} if k else {}


@pytest.fixture(scope="session")
def grading_present(base_url: str, auth_headers: dict[str, str], schemas: dict[str, dict]) -> None:
    """Skip the module unless the server mounts ``/v1/grading/batches``.

    A 404 here is the resource being absent. The body is checked against the
    error envelope when it is JSON — FastAPI's ``{"detail": "Not Found"}`` — and
    tolerated when it is not, because a router that has no such route may
    answer with its framework's plain-text 404 (the Go scaffold does), and
    that is still absence, not a contract violation of an endpoint that does
    not exist.
    """
    with httpx.Client(base_url=base_url, timeout=30.0, headers=auth_headers) as c:
        resp = c.get("/v1/grading/batches")
    if resp.status_code == 404:
        try:
            body = resp.json()
        except json.JSONDecodeError:
            body = None
        if isinstance(body, dict):
            jsonschema.validate(instance=body, schema=schemas["error"])
        pytest.skip(NOT_IMPLEMENTED)
    assert resp.status_code in (200, 401), (
        f"GET /v1/grading/batches answered {resp.status_code}, which is neither the "
        f"surface (200/401) nor its absence (404): {resp.text[:300]}"
    )


@pytest.fixture(scope="session")
def principals(grading_present: None, base_url: str, impl: str) -> SimpleNamespace:
    """The four principals and their resolved subjects, or a credential skip.

    Runs only after :func:`grading_present`, so on a server without grading the
    module skips for absence and never reaches the credential path.
    """
    names = {
        "admin": "RAGSTACK_API_KEY_ADMIN",
        "a": "RAGSTACK_API_KEY_NONADMIN",
        "b": "RAGSTACK_API_KEY_P2",
        "outsider": "RAGSTACK_API_KEY_B",
    }
    missing = [v for v in names.values() if not key(v)]
    if missing:
        skip_no_credential(
            "the grading independence tests need four distinct principals "
            f"({', '.join(names.values())}); unset: {', '.join(missing)}. "
            "`make test-conformance-keyed` provisions all four."
        )
    keys = {who: key(v) for who, v in names.items()}
    assert len(set(keys.values())) == 4, (
        "the four principal variables do not hold four distinct keys; two names "
        "for one principal is the #405 defect and would make every cross-reader "
        "assertion here vacuous"
    )
    headers = {who: {"X-API-Key": k} for who, k in keys.items()}

    subjects: dict[str, str] = {}
    with httpx.Client(base_url=base_url, timeout=30.0) as c:
        for who, h in headers.items():
            resp = c.get("/v1/stats/tenants", headers=h)
            assert resp.status_code == 200, (
                f"PRECONDITION key_is_valid: GET /v1/stats/tenants as {who} returned "
                f"{resp.status_code}: {resp.text}"
            )
            body = resp.json()
            subjects[who] = body["tenant"]
            if who == "admin":
                assert body["role"] == "admin", f"the admin key resolves to role {body['role']!r}"
            else:
                assert body["role"] != "admin", f"the {who} key resolves to admin"
    assert len(set(subjects.values())) == 4, f"subjects are not distinct: {subjects}"

    return SimpleNamespace(headers=headers, subjects=subjects, impl=impl)


# --------------------------------------------------------------------------- #
# The scripted read
# --------------------------------------------------------------------------- #
def _json_or_text(resp: httpx.Response) -> object:
    try:
        return resp.json()
    except json.JSONDecodeError:
        return resp.text


def _step(resp: httpx.Response, expected: int, step: str) -> Any:
    assert resp.status_code == expected, (
        f"step {step!r}: expected {expected}, got {resp.status_code}: {resp.text[:500]}"
    )
    return _json_or_text(resp) if resp.content else None


@pytest.fixture(scope="session")
def scenario(principals: SimpleNamespace, base_url: str) -> Iterator[SimpleNamespace]:
    H, S = principals.headers, principals.subjects
    # A keyed principal's subject is what `@service:` keeps colon-free — a bare
    # name would be qualified to `bvbrc:<name>`, an inert reader (see
    # GradingBatchCreateRequest.readers).
    reader_specs = [f"@service:{S['a']}", f"@service:{S['b']}"]
    body = _create_body(reader_specs)
    pair_ids = [t["pair_id"] for t in body["tasks"]]
    rec = SimpleNamespace(subjects=S, headers=H, pair_ids=pair_ids, create_body=body)

    with httpx.Client(base_url=base_url, timeout=30.0) as c:
        # --- a non-admin cannot create (stateless; nothing exists yet) --------- #
        rec.create_as_reader = c.post("/v1/grading/batches", json=body, headers=H["a"])
        rec.create_as_outsider = c.post("/v1/grading/batches", json=body, headers=H["outsider"])

        # --- create ----------------------------------------------------------- #
        resp = c.post("/v1/grading/batches", json=body, headers=H["admin"])
        rec.batch = _step(resp, 201, "admin creates the batch")
        bid = rec.batch["id"]
        rec.batch_id = bid
        try:
            # --- each reader's order ------------------------------------------ #
            rec.tasks_a = _step(c.get(f"/v1/grading/batches/{bid}/tasks", headers=H["a"]), 200, "A lists tasks")
            rec.tasks_b = _step(c.get(f"/v1/grading/batches/{bid}/tasks", headers=H["b"]), 200, "B lists tasks")
            rec.tasks_admin_open = _step(
                c.get(f"/v1/grading/batches/{bid}/tasks", headers=H["admin"]), 200, "admin lists tasks (open)"
            )
            by_pair = {t["pair_id"]: t["id"] for t in rec.tasks_a["tasks"]}
            rec.task_ids = [by_pair[p] for p in pair_ids]  # batch order
            t0, t1 = rec.task_ids[0], rec.task_ids[1]
            rec.t0, rec.t1 = t0, t1

            # --- A saves, re-saves; saves the none-task with the extra answer --- #
            rec.a_v1 = _step(
                c.put(
                    f"/v1/grading/tasks/{t0}/verdict",
                    json={
                        "verdict": "correct",
                        "span_judgements": [
                            {"set": 1, "span": 1, "judgement": "located"},
                            {"set": 2, "span": 1, "judgement": "located"},
                        ],
                        "notes": "first pass",
                    },
                    headers=H["a"],
                ),
                200,
                "A saves a verdict",
            )
            rec.a_v2 = _step(
                c.put(
                    f"/v1/grading/tasks/{t0}/verdict",
                    json={
                        "verdict": "non-minimal",
                        "span_judgements": [
                            {"set": 1, "span": 1, "judgement": "non-minimal"},
                            {"set": 2, "span": 1, "judgement": "located"},
                        ],
                        "notes": "second pass: set 1 is background",
                    },
                    headers=H["a"],
                ),
                200,
                "A re-saves the verdict",
            )
            rec.a_none = _step(
                c.put(
                    f"/v1/grading/tasks/{t1}/verdict",
                    json={
                        "verdict": "correctly-none",
                        "extra_answers": [{"id": "other_passage", "answer": "no"}],
                    },
                    headers=H["a"],
                ),
                200,
                "A saves the none-task verdict",
            )
            # --- B saves on the positive task only ------------------------------ #
            rec.b_v1 = _step(
                c.put(
                    f"/v1/grading/tasks/{t0}/verdict",
                    json={
                        "verdict": "correct",
                        "span_judgements": [
                            {"set": 1, "span": 1, "judgement": "located"},
                            {"set": 2, "span": 1, "judgement": "located"},
                        ],
                        "notes": "B agrees with the labels",
                    },
                    headers=H["b"],
                ),
                200,
                "B saves a verdict",
            )

            # --- probes that must run while the batch is still open ------------- #
            a_subj, b_subj = S["a"], S["b"]
            rec.b_reads_a_open = c.get(f"/v1/grading/tasks/{t0}/verdicts/{a_subj}", headers=H["b"])
            rec.a_reads_a_open = c.get(f"/v1/grading/tasks/{t0}/verdicts/{a_subj}", headers=H["a"])
            rec.b_reads_nobody = c.get(
                f"/v1/grading/tasks/{t0}/verdicts/{S['outsider']}", headers=H["b"]
            )
            rec.admin_reads_a_open = c.get(f"/v1/grading/tasks/{t0}/verdicts/{a_subj}", headers=H["admin"])
            rec.admin_reads_b_open = c.get(f"/v1/grading/tasks/{t0}/verdicts/{b_subj}", headers=H["admin"])
            rec.a_task_open = _step(c.get(f"/v1/grading/tasks/{t0}", headers=H["a"]), 200, "A gets a task")
            rec.b_task_open = _step(c.get(f"/v1/grading/tasks/{t0}", headers=H["b"]), 200, "B gets a task")
            rec.admin_task_open = _step(
                c.get(f"/v1/grading/tasks/{t0}", headers=H["admin"]), 200, "admin gets a task (open)"
            )
            rec.export_open = c.get(f"/v1/grading/batches/{bid}/export", headers=H["admin"])
            rec.adjudication_put_open = c.put(
                f"/v1/grading/tasks/{t0}/adjudication", json={"verdict": "correct"}, headers=H["admin"]
            )
            rec.admin_puts_verdict = c.put(
                f"/v1/grading/tasks/{t0}/verdict", json={"verdict": "correct"}, headers=H["admin"]
            )
            rec.invalid_bodies = {
                "verdict outside the vocabulary": c.put(
                    f"/v1/grading/tasks/{t0}/verdict", json={"verdict": "fine"}, headers=H["a"]
                ),
                "span judgement naming a span the task lacks": c.put(
                    f"/v1/grading/tasks/{t0}/verdict",
                    json={
                        "verdict": "correct",
                        "span_judgements": [{"set": 9, "span": 1, "judgement": "located"}],
                    },
                    headers=H["a"],
                ),
                "unknown extra question id": c.put(
                    f"/v1/grading/tasks/{t1}/verdict",
                    json={"verdict": "correctly-none", "extra_answers": [{"id": "nope", "answer": "no"}]},
                    headers=H["a"],
                ),
                "non-yes/no answer to a yes-no question": c.put(
                    f"/v1/grading/tasks/{t1}/verdict",
                    json={
                        "verdict": "correctly-none",
                        "extra_answers": [{"id": "other_passage", "answer": "maybe"}],
                    },
                    headers=H["a"],
                ),
                "unknown field in the body": c.put(
                    f"/v1/grading/tasks/{t0}/verdict",
                    json={"verdict": "correct", "reader": b_subj},
                    headers=H["a"],
                ),
            }
            rec.batch_open = _step(c.get(f"/v1/grading/batches/{bid}", headers=H["a"]), 200, "A gets the batch")

            # --- non-admin, outsider ------------------------------------------- #
            rec.reader_refusals = {
                "adjudicate": c.post(f"/v1/grading/batches/{bid}/adjudicate", headers=H["a"]),
                "export": c.get(f"/v1/grading/batches/{bid}/export", headers=H["a"]),
                "delete": c.delete(f"/v1/grading/batches/{bid}", headers=H["a"]),
                "adjudication": c.put(
                    f"/v1/grading/tasks/{t0}/adjudication", json={"verdict": "correct"}, headers=H["a"]
                ),
            }
            out = H["outsider"]
            rec.outsider = {
                "GET batch": c.get(f"/v1/grading/batches/{bid}", headers=out),
                "GET tasks": c.get(f"/v1/grading/batches/{bid}/tasks", headers=out),
                "GET task": c.get(f"/v1/grading/tasks/{t0}", headers=out),
                "PUT verdict": c.put(f"/v1/grading/tasks/{t0}/verdict", json={"verdict": "correct"}, headers=out),
                "GET A's verdict": c.get(f"/v1/grading/tasks/{t0}/verdicts/{a_subj}", headers=out),
                "POST adjudicate": c.post(f"/v1/grading/batches/{bid}/adjudicate", headers=out),
                "GET export": c.get(f"/v1/grading/batches/{bid}/export", headers=out),
                "PUT adjudication": c.put(
                    f"/v1/grading/tasks/{t0}/adjudication", json={"verdict": "correct"}, headers=out
                ),
                "DELETE batch": c.delete(f"/v1/grading/batches/{bid}", headers=out),
            }
            rec.listing_outsider = _step(c.get("/v1/grading/batches", headers=out), 200, "outsider lists batches")
            rec.listing_a = _step(c.get("/v1/grading/batches", headers=H["a"]), 200, "A lists batches")
            rec.listing_admin = _step(c.get("/v1/grading/batches", headers=H["admin"]), 200, "admin lists batches")

            # --- adjudicate ---------------------------------------------------- #
            rec.adjudicated = _step(
                c.post(f"/v1/grading/batches/{bid}/adjudicate", headers=H["admin"]), 200, "admin adjudicates"
            )
            rec.adjudicate_replay = c.post(f"/v1/grading/batches/{bid}/adjudicate", headers=H["admin"])
            rec.a_put_frozen = c.put(
                f"/v1/grading/tasks/{t0}/verdict", json={"verdict": "correct"}, headers=H["a"]
            )
            rec.admin_reads_a_adj = c.get(f"/v1/grading/tasks/{t0}/verdicts/{a_subj}", headers=H["admin"])
            rec.b_reads_a_adj = c.get(f"/v1/grading/tasks/{t0}/verdicts/{a_subj}", headers=H["b"])
            rec.reader_adjudicate_after = c.post(f"/v1/grading/batches/{bid}/adjudicate", headers=H["a"])
            rec.tasks_admin_adj = _step(
                c.get(f"/v1/grading/batches/{bid}/tasks", headers=H["admin"]), 200, "admin lists tasks (adjudicating)"
            )
            rec.tasks_a_adj = _step(
                c.get(f"/v1/grading/batches/{bid}/tasks", headers=H["a"]), 200, "A lists tasks (adjudicating)"
            )
            rec.a_task_adj = _step(c.get(f"/v1/grading/tasks/{t0}", headers=H["a"]), 200, "A gets a task (adjudicating)")
            rec.adjudication = _step(
                c.put(
                    f"/v1/grading/tasks/{t0}/adjudication",
                    json={
                        "verdict": "non-minimal",
                        "span_judgements": [
                            {"set": 1, "span": 1, "judgement": "non-minimal"},
                            {"set": 2, "span": 1, "judgement": "located"},
                        ],
                        "notes": "joint read: A's reading stands",
                    },
                    headers=H["admin"],
                ),
                200,
                "admin saves the adjudication",
            )
            rec.admin_task_adj = _step(
                c.get(f"/v1/grading/tasks/{t0}", headers=H["admin"]), 200, "admin gets a task (adjudicating)"
            )
            rec.export = _step(c.get(f"/v1/grading/batches/{bid}/export", headers=H["admin"]), 200, "admin exports")
            rec.batch_adj = _step(c.get(f"/v1/grading/batches/{bid}", headers=H["admin"]), 200, "admin gets the batch")

            yield rec
        finally:
            # Delete as admin and VERIFY BY LISTING. A teardown that believes a
            # status it did not check is how scratch resources accumulate on a
            # real server (plan R5).
            c.delete(f"/v1/grading/batches/{bid}", headers=H["admin"])
            listing = c.get("/v1/grading/batches", headers=H["admin"])
            remaining = [b["id"] for b in listing.json().get("batches", [])] if listing.status_code == 200 else None
            assert remaining is not None and bid not in remaining, (
                f"teardown did not remove batch {bid!r}: listing {listing.status_code} "
                f"{remaining}. Delete it by hand before re-running."
            )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _validate(data: object, name: str, schemas: dict[str, dict]) -> None:
    store = {s.get("$id", n): s for n, s in schemas.items()}
    resolver = jsonschema.RefResolver.from_schema({}, store=store)
    jsonschema.validate(instance=data, schema=schemas[name], resolver=resolver)


def _assert_404_envelope(resp: httpx.Response, what: str, schemas: dict[str, dict]) -> None:
    assert resp.status_code == 404, f"{what}: expected 404, got {resp.status_code}: {resp.text[:300]}"
    _validate(resp.json(), "error", schemas)


def _read_csv(text: str) -> dict[str, dict]:
    """``s0_rdev_score.read_verdicts``'s acceptance rules, restated."""
    rd = csv.DictReader(io.StringIO(text, newline=""))
    assert rd.fieldnames == ["pair_id", "verdict", "notes"], rd.fieldnames
    rows: dict[str, dict] = {}
    for i, r in enumerate(rd, 2):
        pid = (r.get("pair_id") or "").strip()
        assert pid, f"line {i}: empty pair_id"
        v = (r.get("verdict") or "").strip().lower()
        assert not v or v in VERDICTS, f"line {i}: verdict {v!r} not in the vocabulary"
        assert pid not in rows, f"line {i}: duplicate pair_id {pid!r}"
        rows[pid] = {"verdict": v or None, "notes": (r.get("notes") or "").strip()}
    return rows


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
async def test_created_batch_validates_and_echoes_resolved_readers(scenario, schemas) -> None:
    b = scenario.batch
    _validate(b, "grading_batch", schemas)
    assert b["status"] == "open" and b["adjudicating_at"] == ""
    assert b["readers"] == [scenario.subjects["a"], scenario.subjects["b"]], (
        f"readers must echo the RESOLVED subjects in label order: {b['readers']}"
    )
    assert b["task_count"] == TASK_COUNT
    assert [p["label"] for p in b["progress"]] == ["A", "B"]
    assert all(p["saved"] == 0 for p in b["progress"])
    assert b["rubric_sha256"] == RUBRIC_SHA256 and b["order_seed"] == ORDER_SEED
    assert b["created_by"] == scenario.subjects["admin"]


async def test_each_reader_sees_the_tasks_in_their_own_seeded_order(scenario, schemas) -> None:
    _validate(scenario.tasks_a, "grading_tasks_response", schemas)
    _validate(scenario.tasks_b, "grading_tasks_response", schemas)
    order_a = [t["pair_id"] for t in scenario.tasks_a["tasks"]]
    order_b = [t["pair_id"] for t in scenario.tasks_b["tasks"]]
    assert sorted(order_a) == sorted(scenario.pair_ids) == sorted(order_b), "each reader sees every task once"
    assert order_a != order_b, f"the two readers were shown the SAME order: {order_a}"
    assert order_a == expected_order(scenario.pair_ids, 0), (
        f"reader A's order is not random.Random({ORDER_SEED}+1).shuffle of batch order: {order_a}"
    )
    assert order_b == expected_order(scenario.pair_ids, 1), (
        f"reader B's order is not random.Random({ORDER_SEED}+2).shuffle of batch order: {order_b}"
    )
    assert scenario.tasks_a["reader"] == scenario.subjects["a"]
    assert scenario.tasks_b["reader"] == scenario.subjects["b"]
    # An admin who is not a reader gets batch order and no reader.
    assert scenario.tasks_admin_open["reader"] is None
    assert [t["pair_id"] for t in scenario.tasks_admin_open["tasks"]] == scenario.pair_ids


async def test_saving_a_verdict_bumps_version_and_names_the_caller(scenario, schemas) -> None:
    v1, v2 = scenario.a_v1, scenario.a_v2
    _validate(v1, "grading_verdict", schemas)
    _validate(v2, "grading_verdict", schemas)
    assert (v1["version"], v2["version"]) == (1, 2)
    assert v1["reader"] == v2["reader"] == scenario.subjects["a"]
    assert v2["verdict"] == "non-minimal" and v2["notes"] == "second pass: set 1 is background"
    assert v2["task_id"] == scenario.t0
    # The none-task's defaults: no spans, the extra answer kept, empty notes.
    none = scenario.a_none
    assert none["span_judgements"] == [] and none["notes"] == ""
    assert none["extra_answers"] == [{"id": "other_passage", "answer": "no"}]


async def test_a_reader_never_receives_another_readers_verdict(scenario) -> None:
    """A's listing carries A's rows; B's carries B's; neither carries the other's."""
    a_by_pair = {t["pair_id"]: t["verdict"] for t in scenario.tasks_a["tasks"]}
    b_by_pair = {t["pair_id"]: t["verdict"] for t in scenario.tasks_b["tasks"]}
    p0, p1 = scenario.pair_ids[0], scenario.pair_ids[1]
    # tasks_a/tasks_b were listed BEFORE any save; the per-task GETs afterwards.
    assert all(v is None for v in a_by_pair.values()) and all(v is None for v in b_by_pair.values())
    assert scenario.a_task_open["verdict"]["version"] == 2, "A must see A's latest row"
    assert scenario.a_task_open["verdict"]["reader"] == scenario.subjects["a"]
    assert scenario.b_task_open["verdict"]["reader"] == scenario.subjects["b"]
    assert scenario.b_task_open["verdict"]["notes"] == "B agrees with the labels"
    for t in (scenario.a_task_open, scenario.b_task_open, scenario.a_task_adj):
        assert "reader_verdicts" not in t and "adjudication" not in t, (
            "a reader must never receive reader_verdicts/adjudication, whatever the batch status"
        )
    # After adjudication A's listing still carries only A's rows.
    adj_a = {t["pair_id"]: t["verdict"] for t in scenario.tasks_a_adj["tasks"]}
    assert adj_a[p0]["reader"] == scenario.subjects["a"] and adj_a[p1]["reader"] == scenario.subjects["a"]
    assert all(v is None for pid, v in adj_a.items() if pid not in (p0, p1))
    assert all("reader_verdicts" not in t for t in scenario.tasks_a_adj["tasks"])


async def test_reading_another_readers_row_is_a_404(scenario, schemas) -> None:
    """B naming A's row gets the same 404 as naming a subject who is not a reader."""
    _assert_404_envelope(scenario.b_reads_a_open, "B reads A's verdict (open)", schemas)
    _assert_404_envelope(scenario.b_reads_nobody, "B reads a non-reader's verdict", schemas)
    _assert_404_envelope(scenario.b_reads_a_adj, "B reads A's verdict (adjudicating)", schemas)
    assert scenario.a_reads_a_open.status_code == 200, scenario.a_reads_a_open.text
    _validate(scenario.a_reads_a_open.json(), "grading_verdict", schemas)
    assert scenario.a_reads_a_open.json()["version"] == 2


async def test_an_admin_cannot_read_reader_rows_while_the_batch_is_open(scenario, schemas) -> None:
    _assert_404_envelope(scenario.admin_reads_a_open, "admin reads A's verdict (open)", schemas)
    _assert_404_envelope(scenario.admin_reads_b_open, "admin reads B's verdict (open)", schemas)
    assert "reader_verdicts" not in scenario.admin_task_open and "adjudication" not in scenario.admin_task_open
    assert scenario.admin_task_open["verdict"] is None, "the admin is not a reader and has no row"
    assert all("reader_verdicts" not in t for t in scenario.tasks_admin_open["tasks"])
    assert scenario.export_open.status_code == 409, (
        f"export of an OPEN batch must be 409, got {scenario.export_open.status_code}"
    )
    assert scenario.adjudication_put_open.status_code == 409, (
        f"an adjudication on an OPEN batch must be 409, got {scenario.adjudication_put_open.status_code}"
    )
    assert scenario.admin_puts_verdict.status_code == 403, (
        "an admin who is not a reader has no verdict row to write: expected 403, got "
        f"{scenario.admin_puts_verdict.status_code}"
    )


async def test_a_principal_on_no_batch_gets_404_everywhere(scenario, schemas) -> None:
    for what, resp in scenario.outsider.items():
        _assert_404_envelope(resp, f"outsider {what}", schemas)
    ids = [b["id"] for b in scenario.listing_outsider["batches"]]
    assert scenario.batch_id not in ids, "the batch appears in an outsider's listing"
    assert scenario.batch_id in [b["id"] for b in scenario.listing_a["batches"]]
    assert scenario.batch_id in [b["id"] for b in scenario.listing_admin["batches"]]


async def test_a_non_admin_cannot_create_adjudicate_export_or_delete(scenario, schemas) -> None:
    assert scenario.create_as_reader.status_code == 403, scenario.create_as_reader.text
    assert scenario.create_as_outsider.status_code == 403, scenario.create_as_outsider.text
    for what, resp in scenario.reader_refusals.items():
        assert resp.status_code == 403, (
            f"reader A tried to {what} and got {resp.status_code}, expected 403 (A can see "
            f"the batch, so a 404 would be a lie and a 2xx a breach): {resp.text[:300]}"
        )
        _validate(resp.json(), "error", schemas)
    assert scenario.reader_adjudicate_after.status_code == 403, (
        "authorization is decided before state: a reader's adjudicate on an adjudicating "
        f"batch is still 403, got {scenario.reader_adjudicate_after.status_code}"
    )


async def test_invalid_verdict_bodies_are_422(scenario, schemas) -> None:
    for what, resp in scenario.invalid_bodies.items():
        assert resp.status_code == 422, f"{what}: expected 422, got {resp.status_code}: {resp.text[:300]}"
        _validate(resp.json(), "error", schemas)
    # None of them changed the row.
    assert scenario.a_reads_a_open.json()["version"] == 2


async def test_batch_progress_counts_never_verdicts(scenario, schemas) -> None:
    b = scenario.batch_open
    _validate(b, "grading_batch", schemas)
    saved = {p["label"]: p["saved"] for p in b["progress"]}
    assert saved == {"A": 2, "B": 1}, saved
    assert "verdict" not in json.dumps(b["progress"])


async def test_adjudicate_freezes_reveals_and_unlocks(scenario, schemas) -> None:
    adj = scenario.adjudicated
    _validate(adj, "grading_batch", schemas)
    assert adj["status"] == "adjudicating" and adj["adjudicating_at"]
    assert scenario.adjudicate_replay.status_code == 409, scenario.adjudicate_replay.text
    assert scenario.a_put_frozen.status_code == 409, (
        f"a reader's PUT after adjudication must be 409, got {scenario.a_put_frozen.status_code}"
    )
    assert scenario.admin_reads_a_adj.status_code == 200, scenario.admin_reads_a_adj.text
    assert scenario.admin_reads_a_adj.json()["version"] == 2
    # The admin's task view now carries every reader's row and the adjudication.
    t = scenario.admin_task_adj
    _validate(t, "grading_task", schemas)
    assert [v["reader"] for v in t["reader_verdicts"]] == [scenario.subjects["a"], scenario.subjects["b"]]
    assert t["adjudication"]["verdict"] == "non-minimal"
    by_pair = {x["pair_id"]: x for x in scenario.tasks_admin_adj["tasks"]}
    assert [v["reader"] for v in by_pair[scenario.pair_ids[1]]["reader_verdicts"]] == [scenario.subjects["a"]]
    assert by_pair[scenario.pair_ids[2]]["reader_verdicts"] == []
    assert by_pair[scenario.pair_ids[2]]["adjudication"] is None
    _validate(scenario.adjudication, "grading_adjudication", schemas)
    assert scenario.adjudication["version"] == 1
    assert scenario.adjudication["adjudicated_by"] == scenario.subjects["admin"]


async def test_export_carries_both_csvs_with_the_right_rows(scenario, schemas) -> None:
    ex = scenario.export
    _validate(ex, "grading_export_response", schemas)
    S = scenario.subjects
    assert ex["readers"] == [{"subject": S["a"], "label": "A"}, {"subject": S["b"], "label": "B"}]
    assert [c["filename"] for c in ex["csv"]] == [
        "rdev_verdicts_A.csv",
        "rdev_verdicts_B.csv",
        "rdev_verdicts_ADJ.csv",
    ]
    sheets = {c["label"]: _read_csv(c["content"]) for c in ex["csv"]}
    p0, p1 = scenario.pair_ids[0], scenario.pair_ids[1]
    for label, rows in sheets.items():
        assert list(rows) == scenario.pair_ids, f"{label}: every task, in batch order"
    assert sheets["A"][p0] == {"verdict": "non-minimal", "notes": "second pass: set 1 is background"}
    assert sheets["A"][p1] == {"verdict": "correctly-none", "notes": ""}
    assert sheets["B"][p0] == {"verdict": "correct", "notes": "B agrees with the labels"}
    assert sheets["B"][p1] == {"verdict": None, "notes": ""}, "B never read the none-task: blank, not a verdict"
    assert sheets["ADJ"][p0] == {"verdict": "non-minimal", "notes": "joint read: A's reading stands"}
    assert sheets["ADJ"][p1] == {"verdict": None, "notes": ""}
    for pid in scenario.pair_ids[2:]:
        for label in ("A", "B", "ADJ"):
            assert sheets[label][pid] == {"verdict": None, "notes": ""}, (label, pid)
    # The JSON side carries what the CSV cannot: the span judgements.
    rows = {(r["pair_id"], r["label"]): r for r in ex["verdicts"]}
    assert set(rows) == {(p0, "A"), (p0, "B"), (p1, "A")}
    assert rows[(p0, "A")]["span_judgements"] == [
        {"set": 1, "span": 1, "judgement": "non-minimal"},
        {"set": 2, "span": 1, "judgement": "located"},
    ]
    assert rows[(p0, "A")]["version"] == 2 and rows[(p0, "A")]["stratum"] == "model_positive"
    assert rows[(p1, "A")]["extra_answers"] == [{"id": "other_passage", "answer": "no"}]
    assert [a["pair_id"] for a in ex["adjudications"]] == [p0]
    assert ex["status"] == "adjudicating" and ex["rubric_sha256"] == RUBRIC_SHA256
