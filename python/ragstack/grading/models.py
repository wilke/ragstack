"""Stored records for the grading resources, and the normative order rule.

These mirror ``contracts/schemas/grading_*.json`` one field at a time, with two
deliberate differences:

* a record carries the fields the *store* needs and the wire does not —
  ``GradingTaskRecord.position`` (batch order) and the ``batch_id`` on a
  verdict/adjudication row (so a batch-wide read is one query). The router's
  serializers drop them; the schemas have ``additionalProperties: false`` and
  would reject them.
* nothing here decides visibility. ``GradingTask.verdict`` /
  ``reader_verdicts`` / ``adjudication`` are assembled per caller in the
  router — the one place that knows who is asking.

Records are stored as JSON (``model_dump_json``) by the sqlite and postgres
backends, so adding an optional field is an additive migration and reading an
older row still works.
"""
from __future__ import annotations

import random
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

#: The six-verdict vocabulary, byte-for-byte ``s0_rdev_score.VERDICTS`` and
#: ``SPEC-confirmation-run.md`` §6.6.2. Order matters only as documentation.
VERDICTS: tuple[str, ...] = (
    "correct",
    "wrong-location",
    "non-minimal",
    "missed-evidence",
    "correctly-none",
    "ambiguous",
)

#: Per-span judgements — the pilot sheet's question (a).
SPAN_JUDGEMENTS: tuple[str, ...] = ("located", "wrong", "non-minimal")

#: A batch's protocol. ``citation-feedback`` is grading-ui.md phase 5; it is in
#: the vocabulary so the data model does not need a migration to reach it.
KINDS: tuple[str, ...] = ("evidence-read", "pointed-read", "citation-feedback")

#: Batch status. ``closed`` is reserved by grading-ui.md §3.1 for a later
#: phase — no v1 operation produces it.
STATUS_OPEN = "open"
STATUS_ADJUDICATING = "adjudicating"
STATUS_CLOSED = "closed"
STATUSES: tuple[str, ...] = (STATUS_OPEN, STATUS_ADJUDICATING, STATUS_CLOSED)

#: Answer types for a task's extra questions (r3 §11 guard 2).
ANSWER_TYPES: tuple[str, ...] = ("yes-no", "text")

#: The letters a reader is labelled with, by position in ``GradingBatch.readers``.
LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
#: ``GradingBatchCreateRequest.readers`` is capped at 26 for exactly this reason.
MAX_READERS = len(LABELS)


def now_iso() -> str:
    """The timestamp every record is stamped with: sortable ISO-8601 UTC."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def label_for(index: int) -> str:
    """``A`` for reader 0, ``B`` for 1, … — what the export's CSV filenames carry."""
    return LABELS[index]


def reader_order(order_seed: int, reader_index: int, task_count: int) -> list[int]:
    """The batch's private permutation for one reader, as the contract defines it.

    ``GradingBatch``'s rule, normative for every implementation: reader at index
    ``k`` sees the tasks in batch order permuted by CPython's
    ``random.Random(order_seed + k + 1).shuffle(list(range(task_count)))``. That
    is the same expression ``docs/plans/results/stage0/s0_rdev.py`` uses to
    build ``RDEV-readsheet-A/B.html`` (``SEED_RDEV + 1`` for A, ``+ 2`` for B),
    so a read begun on those sheets continues here at the same position.

    Returns the permutation as indices into batch order.
    """
    order = list(range(task_count))
    random.Random(order_seed + reader_index + 1).shuffle(order)
    return order


class _Frozen(BaseModel):
    """Records are authored once and stored; nothing mutates one in place."""

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- #
# The authored triple: question, document, claims
# --------------------------------------------------------------------------- #
class GradingSentence(_Frozen):
    """``grading_document.json#/$defs/GradingSentence``."""

    i: int
    text: str


class GradingUnit(_Frozen):
    """``grading_document.json#/$defs/GradingUnit``."""

    index: int
    title: str
    sentences: list[GradingSentence]


class GradingDocument(_Frozen):
    """The segmented document, stored DENORMALISED on every task: a read must
    be reproducible against exactly what the reader saw, independent of any
    later re-ingest of the corpus (grading-ui.md §3.1)."""

    doc_id: str
    title: str
    units: list[GradingUnit]


class GradingSpan(_Frozen):
    """A contiguous run of sentences inside one unit. Validated against the
    task's document at create time — a span pointing outside it is a 422, never
    a silently unrenderable highlight."""

    unit: int
    first_sentence: int
    last_sentence: int
    text: str


class GradingEvidenceSet(_Frozen):
    """One labeler's claim that these spans, together, are sufficient support
    (``SPEC-confirmation-run.md`` D3). A task's ``claims`` is the union of every
    labeler's sets, each tagged with the labelers that produced it."""

    set_index: int
    spans: list[GradingSpan]
    sources: list[str]


class GradingQuestion(_Frozen):
    """The information need the task is read against. ``id`` is the study's
    topic id; ``""`` means none was supplied and the serializer omits it."""

    id: str = ""
    type: str
    summary: str
    description: str


class GradingExtraQuestion(_Frozen):
    """An additional per-task question (r3 §11 guard 2), answered in
    ``GradingVerdict.extra_answers`` under the same ``id``."""

    id: str
    text: str
    answer_type: str


class GradingSpanJudgement(_Frozen):
    """Question (a) for one span, addressed as (``set``, 1-based ``span``)."""

    set: int
    span: int
    judgement: str


class GradingExtraAnswer(_Frozen):
    """One answer to a task's extra question, keyed by its ``id``."""

    id: str
    answer: str


# --------------------------------------------------------------------------- #
# Stored records
# --------------------------------------------------------------------------- #
class GradingBatchRecord(_Frozen):
    """A read: the unit that fixes the rubric, the readers, the order seed and
    the status. Progress counts are derived from the verdict rows, never stored
    here — a count that can drift from the rows it summarises is a bug waiting
    for a partial write."""

    id: str
    name: str
    kind: str
    status: str
    rubric_sha256: str
    order_seed: int
    #: Resolved subjects in LABEL order: index 0 is reader ``A``.
    readers: list[str]
    task_count: int
    created_at: str
    created_by: str
    #: Empty string while ``open`` — the ``ShareRecord.revoked_at`` convention.
    adjudicating_at: str = ""


class GradingTaskRecord(_Frozen):
    """One thing to grade. ``position`` is the task's index in BATCH order (the
    draw's order, which is the order the export's CSV rows use); every reader's
    order is a permutation of it, computed on read."""

    id: str
    batch_id: str
    kind: str
    position: int
    pair_id: str
    #: ``""`` when the authored task carried none; the serializer omits it then.
    stratum: str = ""
    question: GradingQuestion
    document: GradingDocument
    claims: list[GradingEvidenceSet]
    extra_questions: list[GradingExtraQuestion]
    #: The batch's readers, denormalised in label order.
    readers: list[str]
    created_at: str
    created_by: str


class GradingVerdictRecord(_Frozen):
    """One reader's answer to one task. Rows are APPEND-ONLY by ``version``:
    a re-save writes a new row and the current one is the highest version, so
    the pre-adjudication read is recoverable even after an overwrite."""

    task_id: str
    #: Not on the wire (``grading_verdict.json`` forbids it) — it is what makes
    #: a batch-wide export one query instead of one per task.
    batch_id: str
    reader: str
    verdict: str
    span_judgements: list[GradingSpanJudgement]
    extra_answers: list[GradingExtraAnswer]
    notes: str
    version: int
    saved_at: str


class GradingAdjudicationRecord(_Frozen):
    """The joint-read verdict for one task (SPEC §6.6.3) — the verdict the study
    USES. The readers' own rows are never modified by it: the pre-adjudication
    κ is the one reported, and it needs the originals."""

    task_id: str
    batch_id: str  # as above: not on the wire
    verdict: str
    span_judgements: list[GradingSpanJudgement]
    notes: str
    adjudicated_by: str
    version: int
    saved_at: str
