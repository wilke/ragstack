"""The grading store's three backends, and the order rule they all serve.

Two properties are load-bearing and are asserted against EVERY backend, because
the study's κ depends on them and a backend that quietly differs would be
discovered only after a read:

* **verdict rows are append-only.** A re-save must not overwrite the earlier
  row: the pre-adjudication κ is the number the study reports, and it needs the
  originals even after a reader changes their mind.
* **``begin_adjudication`` is conditional.** Two clicks must not both freeze the
  read — the second has to lose, so the router can answer the contract's 409.

The order rule is tested against a literal expectation and against
``s0_rdev.py``'s own expression, because it is the one thing two implementations
must agree on byte-for-byte for a reader to keep their place when a read moves
from the readsheets to the UI.

Postgres is skipped unless ``RAGSTACK_TEST_POSTGRES_DSN`` names a database: this
suite must never reach for a default DSN, which on the dev host resolves to
production (#363/#369/#392).
"""
from __future__ import annotations

import os
import random

import pytest

from ragstack.grading.models import (
    ANSWER_TYPES,
    KINDS,
    SPAN_JUDGEMENTS,
    VERDICTS,
    GradingAdjudicationRecord,
    GradingBatchRecord,
    GradingDocument,
    GradingEvidenceSet,
    GradingQuestion,
    GradingSentence,
    GradingSpan,
    GradingSpanJudgement,
    GradingTaskRecord,
    GradingUnit,
    GradingVerdictRecord,
    label_for,
    now_iso,
    reader_order,
)
from ragstack.grading.store import (
    InMemoryGradingStore,
    PostgresGradingStore,
    SqliteGradingStore,
    make_grading_store,
)

# ``asyncio_mode = "auto"`` (pyproject) runs the async tests; a module-level
# asyncio mark would warn on every sync one here.

READER_A = "conf-a"
READER_B = "conf-b"

_PG_DSN = os.environ.get("RAGSTACK_TEST_POSTGRES_DSN", "")


@pytest.fixture(params=["memory", "sqlite", "postgres"])
async def store(request, tmp_path):
    if request.param == "postgres":
        if not _PG_DSN:
            pytest.skip(
                "set RAGSTACK_TEST_POSTGRES_DSN to exercise the postgres grading "
                "store; there is deliberately no default DSN (the host default "
                "resolves to production)"
            )
        s = PostgresGradingStore(_PG_DSN)
    elif request.param == "sqlite":
        s = SqliteGradingStore(str(tmp_path / "grading.db"))
    else:
        s = InMemoryGradingStore()
    try:
        yield s
    finally:
        await s.close()


def _batch(**over) -> GradingBatchRecord:
    base = {
        "id": "b1",
        "name": "unit-batch",
        "kind": "evidence-read",
        "status": "open",
        "rubric_sha256": "0" * 64,
        "order_seed": 4242,
        "readers": [READER_A, READER_B],
        "task_count": 2,
        "created_at": now_iso(),
        "created_by": "conf-admin",
        "adjudicating_at": "",
    }
    base.update(over)
    return GradingBatchRecord(**base)


def _task(batch: GradingBatchRecord, position: int) -> GradingTaskRecord:
    return GradingTaskRecord(
        id=f"{batch.id}-t{position}",
        batch_id=batch.id,
        kind=batch.kind,
        position=position,
        pair_id=f"topic__doc{position}",
        stratum="model_positive",
        question=GradingQuestion(
            id=f"topic-{position}", type="diagnosis", summary="s", description="d"
        ),
        document=GradingDocument(
            doc_id=f"doc{position}",
            title="A document",
            units=[
                GradingUnit(
                    index=0,
                    title="Abstract",
                    sentences=[GradingSentence(i=i, text=f"Sentence {i}.") for i in range(3)],
                )
            ],
        ),
        claims=[
            GradingEvidenceSet(
                set_index=1,
                spans=[GradingSpan(unit=0, first_sentence=0, last_sentence=1, text="q")],
                sources=["scout"],
            )
        ],
        extra_questions=[],
        readers=list(batch.readers),
        created_at=batch.created_at,
        created_by=batch.created_by,
    )


def _verdict(task_id: str, reader: str, verdict: str, notes: str = "") -> GradingVerdictRecord:
    return GradingVerdictRecord(
        task_id=task_id,
        batch_id="b1",
        reader=reader,
        verdict=verdict,
        span_judgements=[GradingSpanJudgement(set=1, span=1, judgement="located")],
        extra_answers=[],
        notes=notes,
        version=0,
        saved_at=now_iso(),
    )


async def _seed(store) -> GradingBatchRecord:
    batch = _batch()
    await store.create_batch(batch, [_task(batch, 0), _task(batch, 1)])
    return batch


# --------------------------------------------------------------------------- #
# Round-trip
# --------------------------------------------------------------------------- #
async def test_a_batch_round_trips_with_its_tasks_in_batch_order(store):
    batch = await _seed(store)
    got = await store.get_batch(batch.id)
    assert got == batch, "the record must survive the store byte-for-byte"
    tasks = await store.list_tasks(batch.id)
    assert [t.position for t in tasks] == [0, 1]
    assert [t.pair_id for t in tasks] == ["topic__doc0", "topic__doc1"]
    # A whole segmented document is stored denormalised on the task — a read has
    # to be reproducible against exactly what the reader saw.
    assert tasks[0].document.units[0].sentences[2].text == "Sentence 2."
    assert await store.get_task("b1-t1") == tasks[1]
    assert await store.get_batch("nope") is None
    assert await store.get_task("nope") is None


async def test_listing_is_newest_first(store):
    older = _batch(id="b0", created_at="2026-01-01T00:00:00+00:00")
    newer = _batch(id="b1", created_at="2026-02-01T00:00:00+00:00")
    await store.create_batch(older, [_task(older, 0)])
    await store.create_batch(newer, [_task(newer, 0)])
    assert [b.id for b in await store.list_batches()] == ["b1", "b0"]


async def test_deleting_a_batch_removes_everything_under_it(store):
    batch = await _seed(store)
    await store.put_verdict(_verdict("b1-t0", READER_A, "correct"))
    await store.put_adjudication(
        GradingAdjudicationRecord(
            task_id="b1-t0", batch_id=batch.id, verdict="correct", span_judgements=[],
            notes="", adjudicated_by="conf-admin", version=0, saved_at=now_iso(),
        )
    )
    assert await store.delete_batch(batch.id) is True
    assert await store.get_batch(batch.id) is None
    assert await store.list_tasks(batch.id) == []
    assert await store.list_verdicts(batch.id) == []
    assert await store.list_adjudications(batch.id) == []
    assert await store.get_verdict("b1-t0", READER_A) is None
    # A second delete is False, not an error: the router turns it into a 404.
    assert await store.delete_batch(batch.id) is False


# --------------------------------------------------------------------------- #
# Append-only versioning
# --------------------------------------------------------------------------- #
async def test_a_re_save_bumps_the_version_and_keeps_the_earlier_row(store):
    await _seed(store)
    first = await store.put_verdict(_verdict("b1-t0", READER_A, "correct", "first pass"))
    second = await store.put_verdict(_verdict("b1-t0", READER_A, "non-minimal", "second pass"))
    assert (first.version, second.version) == (1, 2)
    current = await store.get_verdict("b1-t0", READER_A)
    assert current is not None
    assert (current.version, current.verdict, current.notes) == (2, "non-minimal", "second pass")
    # The earlier version is not surfaced in v1, but it must still be THERE:
    # the pre-adjudication κ needs the row the reader first saved.
    assert await _row_count(store, "grading_verdicts", "b1-t0") in (None, 2)


async def _row_count(store, table: str, task_id: str) -> int | None:
    """Version count for a task, for the backends where it is observable.

    The Protocol deliberately has no "give me every version" method — v1 does
    not surface history — so this reaches into the sqlite backend directly. The
    in-memory and postgres backends return ``None`` (not checked here); the
    append-only *behaviour* is what the assertions above pin on every backend.
    """
    if isinstance(store, SqliteGradingStore):
        import sqlite3
        from contextlib import closing

        with closing(sqlite3.connect(store._path)) as conn:
            return conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE task_id = ?", (task_id,)  # noqa: S608
            ).fetchone()[0]
    if isinstance(store, InMemoryGradingStore):
        return sum(1 for v in store._verdicts if v.task_id == task_id)
    return None


async def test_one_readers_row_is_never_the_others(store):
    """The store keys a row by (task, reader). Two readers on one task are two
    independent histories — the property the whole feature exists for."""
    await _seed(store)
    await store.put_verdict(_verdict("b1-t0", READER_A, "correct", "A"))
    await store.put_verdict(_verdict("b1-t0", READER_A, "ambiguous", "A again"))
    await store.put_verdict(_verdict("b1-t0", READER_B, "wrong-location", "B"))
    a = await store.get_verdict("b1-t0", READER_A)
    b = await store.get_verdict("b1-t0", READER_B)
    assert a is not None and b is not None
    assert (a.version, a.notes) == (2, "A again")
    assert (b.version, b.notes) == (1, "B"), "B's own sequence starts at 1"
    assert await store.get_verdict("b1-t0", "somebody-else") is None


async def test_list_verdicts_returns_one_current_row_per_task_and_reader(store):
    batch = await _seed(store)
    await store.put_verdict(_verdict("b1-t0", READER_A, "correct"))
    await store.put_verdict(_verdict("b1-t0", READER_A, "ambiguous"))
    await store.put_verdict(_verdict("b1-t1", READER_B, "correctly-none"))
    rows = await store.list_verdicts(batch.id)
    assert sorted((r.task_id, r.reader, r.version) for r in rows) == [
        ("b1-t0", READER_A, 2),
        ("b1-t1", READER_B, 1),
    ]


async def test_adjudications_version_and_collapse_the_same_way(store):
    batch = await _seed(store)

    def adj(verdict: str) -> GradingAdjudicationRecord:
        return GradingAdjudicationRecord(
            task_id="b1-t0", batch_id=batch.id, verdict=verdict, span_judgements=[],
            notes="", adjudicated_by="conf-admin", version=0, saved_at=now_iso(),
        )

    assert (await store.put_adjudication(adj("correct"))).version == 1
    assert (await store.put_adjudication(adj("non-minimal"))).version == 2
    current = await store.get_adjudication("b1-t0")
    assert current is not None and current.verdict == "non-minimal"
    rows = await store.list_adjudications(batch.id)
    assert [(r.task_id, r.version) for r in rows] == [("b1-t0", 2)]
    assert await store.get_adjudication("b1-t1") is None


# --------------------------------------------------------------------------- #
# The one-way transition
# --------------------------------------------------------------------------- #
async def test_begin_adjudication_is_conditional_and_wins_only_once(store):
    batch = await _seed(store)
    assert await store.begin_adjudication(batch.id, "2026-09-06T10:00:00+00:00") is True
    after = await store.get_batch(batch.id)
    assert after is not None
    assert after.status == "adjudicating"
    assert after.adjudicating_at == "2026-09-06T10:00:00+00:00"
    # The second click loses — and does NOT re-stamp the timestamp, which is the
    # record of when independence ended.
    assert await store.begin_adjudication(batch.id, "2026-09-06T11:00:00+00:00") is False
    again = await store.get_batch(batch.id)
    assert again is not None and again.adjudicating_at == "2026-09-06T10:00:00+00:00"
    assert await store.begin_adjudication("nope", "x") is False


# --------------------------------------------------------------------------- #
# The order rule
# --------------------------------------------------------------------------- #
def test_reader_order_is_the_contracts_shuffle():
    """``random.Random(order_seed + k + 1).shuffle`` — recomputed here rather
    than restated, and pinned to a literal so a change to either side shows."""
    for k in (0, 1, 2):
        expected = list(range(6))
        random.Random(4242 + k + 1).shuffle(expected)
        assert reader_order(4242, k, 6) == expected
    assert reader_order(4242, 0, 6) != reader_order(4242, 1, 6), (
        "two readers must not be shown the same order"
    )
    # A literal, so a Python that changed MT19937 or shuffle would be caught
    # rather than silently agreeing with itself.
    assert reader_order(4242, 0, 6) == [2, 1, 5, 0, 3, 4]


def test_reader_order_matches_the_rdev_readsheets_seeds():
    """``s0_rdev.py`` shuffles with ``SEED_RDEV + 1`` for A and ``+ 2`` for B.
    The contract's rule is ``order_seed + k + 1``; for the R-dev seed those are
    the same two permutations, which is what lets a read that began on the
    readsheets continue in the UI at the same position."""
    seed_rdev = 20260915
    for k, sheet_offset in ((0, 1), (1, 2)):
        sheet = list(range(100))
        random.Random(seed_rdev + sheet_offset).shuffle(sheet)
        assert reader_order(seed_rdev, k, 100) == sheet


def test_labels_are_letters_by_position():
    assert [label_for(i) for i in range(3)] == ["A", "B", "C"]


# --------------------------------------------------------------------------- #
# Backend selection and vocabulary drift
# --------------------------------------------------------------------------- #
def test_make_grading_store_selects_the_backend(tmp_path):
    assert isinstance(make_grading_store("memory", "x"), InMemoryGradingStore)
    assert isinstance(
        make_grading_store("sqlite", str(tmp_path / "g.db")), SqliteGradingStore
    )
    assert isinstance(
        make_grading_store("postgres", "x", "postgresql://u@h/db"), PostgresGradingStore
    )
    # A typo must not take down an API that also serves query and ingest.
    assert isinstance(make_grading_store("postgress", "x"), InMemoryGradingStore)
    assert isinstance(make_grading_store("", "x"), InMemoryGradingStore)


def test_wire_vocabularies_match_the_models():
    """The router's ``Literal``s are what the wire accepts; the models' tuples
    are what the store round-trips. A value in one and not the other is either
    a request that cannot be stored or a stored row nobody can produce."""
    from typing import get_args

    from ragstack.api.routers import grading as router

    assert set(get_args(router.VerdictLiteral)) == set(VERDICTS)
    assert set(get_args(router.JudgementLiteral)) == set(SPAN_JUDGEMENTS)
    assert set(get_args(router.KindLiteral)) == set(KINDS)
    assert set(get_args(router.AnswerTypeLiteral)) == set(ANSWER_TYPES)
