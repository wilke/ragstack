"""``/v1/grading`` — the study's evidence read, with independence enforced here.

``docs/plans/grading-ui.md`` moves the two-independent-reader label validation
(``SPEC-confirmation-run.md`` §6.6) off a claude.ai artifact, where reader
independence was honour-based, into RAGStack, where it is not. This module is
the enforcement point; ``contracts/openapi.yaml`` (tag ``Grading``) and
``contracts/schemas/grading_*.json`` are authoritative for every shape below.

**The four rules this file exists for.**

1. *A reader reads and writes only their own verdict row.* ``PUT
   …/verdict`` has no field naming a reader — the row is keyed by the
   authenticated subject — and ``GET …/verdicts/{reader}`` answers **404** for
   anyone else's, the same 404 as naming a subject who is not a reader at all
   (ADR-0003 §2's existence-hiding, applied to a row instead of a collection).
2. *An admin is not exempt while the read is open.* ``reader_verdicts`` and
   ``adjudication`` appear only for an admin AND only once the batch has left
   ``open``; before that an admin reading a reader's row gets the same 404, and
   the export is a 409. The point of ``POST …/adjudicate`` is to be the moment
   independence ends, recorded with a timestamp.
3. *The order is the server's, not the client's.* Each reader's permutation is
   :func:`ragstack.grading.models.reader_order` — CPython's
   ``random.Random(order_seed + k + 1).shuffle`` — the same expression
   ``s0_rdev.py`` used to build the ``RDEV-readsheet-A/B.html`` sheets, so a
   read begun on those sheets continues here at the same position.
4. *Unseen is unknown.* A batch the caller neither administers nor reads is a
   404 from every endpoint under it, including the write and admin ones, so the
   surface is never an existence oracle for a read they are not on. A caller who
   CAN see the batch and lacks the role gets 403 — at that point "you can't do
   that" leaks nothing new.

Authorization is decided **before** state: a reader's ``POST …/adjudicate`` on
an already-adjudicating batch is 403, not 409.
"""
from __future__ import annotations

import csv
import functools
import io
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from ragstack.acl_store import GRANTEE_GROUP
from ragstack.api.deps import get_grading_store
from ragstack.api.routers.collections import _DEFAULT_ISSUER, _resolve_grantee
from ragstack.api.security import ROLE_ADMIN, Principal, resolve_principal
from ragstack.grading.models import (
    MAX_READERS,
    STATUS_OPEN,
    GradingAdjudicationRecord,
    GradingBatchRecord,
    GradingDocument,
    GradingEvidenceSet,
    GradingExtraAnswer,
    GradingExtraQuestion,
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
from ragstack.grading.store import GradingStore

log = logging.getLogger(__name__)

router = APIRouter()

#: Literal aliases so FastAPI rejects a value outside the vocabulary with its own
#: 422 (matching ``error.json``'s array-shaped ``detail``) instead of us
#: hand-rolling a second validation dialect. The schemas' enums are the source.
VerdictLiteral = Literal[
    "correct", "wrong-location", "non-minimal", "missed-evidence", "correctly-none", "ambiguous"
]
JudgementLiteral = Literal["located", "wrong", "non-minimal"]
KindLiteral = Literal["evidence-read", "pointed-read", "citation-feedback"]
AnswerTypeLiteral = Literal["yes-no", "text"]

# The Literals above and the models' vocabularies must not drift apart — the
# models are what the store round-trips, the Literals are what the wire accepts.
# ``tests/unit/test_grading_store.py::test_wire_vocabularies_match_the_models``
# pins that; it is a test rather than an import-time assert so a mismatch names
# which vocabulary moved instead of failing every request with an ImportError.


def fail_closed[**P, R](fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Turn a grading-store failure into the contract's 503.

    Every ``/v1/grading`` response documents ``503 — grading store unavailable``.
    Without this a sqlite/asyncpg error would surface as a bare 500, and worse,
    a *partial* read (say, tasks fetched but verdicts not) could answer 200 with
    a reader's row missing — which on this surface reads as "not saved yet".
    Fail closed; never a 200 that under-reports (#196).
    """

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await fn(*args, **kwargs)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001 — every store failure is a 503
            log.warning("grading store failure in %s", fn.__name__, exc_info=True)
            raise HTTPException(
                503, "grading store unavailable; refusing to serve (fail closed)"
            ) from e

    return wrapper


def _store(store: GradingStore | None) -> GradingStore:
    """The app's grading store, or the 503 for an app assembled without one."""
    if store is None:
        raise HTTPException(
            503, "grading store unavailable; refusing to serve (fail closed)"
        )
    return store


# --------------------------------------------------------------------------- #
# Request bodies — one pydantic model per ``*_request`` schema
# --------------------------------------------------------------------------- #
class _Body(BaseModel):
    """``additionalProperties: false`` in the schemas, ``extra="forbid"`` here —
    the mechanism the rest of this API already uses to keep the two in step. An
    unknown field is FastAPI's own 422, which is what the contract promises."""

    model_config = ConfigDict(extra="forbid")


class SentenceIn(_Body):
    i: int = Field(..., ge=0)
    text: str


class UnitIn(_Body):
    index: int = Field(..., ge=0)
    title: str
    sentences: list[SentenceIn] = Field(default_factory=list)


class DocumentIn(_Body):
    doc_id: str = Field(..., min_length=1)
    title: str
    units: list[UnitIn] = Field(default_factory=list)


class SpanIn(_Body):
    unit: int = Field(..., ge=0)
    first_sentence: int = Field(..., ge=0)
    last_sentence: int = Field(..., ge=0)
    text: str


class EvidenceSetIn(_Body):
    set_index: int = Field(..., ge=1)
    spans: list[SpanIn] = Field(..., min_length=1)
    sources: list[str] = Field(..., min_length=1)


class QuestionIn(_Body):
    id: str = ""
    type: str = Field(..., min_length=1)
    summary: str
    description: str


class ExtraQuestionIn(_Body):
    id: str = Field(..., pattern=r"^[A-Za-z0-9_.\-]{1,64}$")
    text: str = Field(..., min_length=1)
    answer_type: AnswerTypeLiteral


class TaskCreateIn(_Body):
    pair_id: str = Field(..., min_length=1, max_length=200)
    stratum: str = ""
    question: QuestionIn
    document: DocumentIn
    claims: list[EvidenceSetIn] = Field(default_factory=list)
    extra_questions: list[ExtraQuestionIn] = Field(default_factory=list)


class BatchCreateRequest(_Body):
    """``grading_batch_create_request.json`` — the whole read in one body. The
    importer (``python/scripts/grading_import.py``) builds it from the committed
    study package; the API reads no files."""

    name: str = Field(..., min_length=1, max_length=200)
    kind: KindLiteral
    rubric_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    order_seed: int
    readers: list[str] = Field(..., min_length=1, max_length=MAX_READERS)
    tasks: list[TaskCreateIn] = Field(..., min_length=1)


class SpanJudgementIn(_Body):
    set: int = Field(..., ge=1)
    span: int = Field(..., ge=1)
    judgement: JudgementLiteral


class ExtraAnswerIn(_Body):
    id: str
    answer: str


class VerdictPutRequest(_Body):
    """``grading_verdict_put_request.json`` — a WHOLE-ROW replace: an omitted
    list clears it rather than keeping the previous value."""

    verdict: VerdictLiteral
    span_judgements: list[SpanJudgementIn] = Field(default_factory=list)
    extra_answers: list[ExtraAnswerIn] = Field(default_factory=list)
    notes: str = Field("", max_length=10000)


class AdjudicationPutRequest(_Body):
    """``grading_adjudication_put_request.json``."""

    verdict: VerdictLiteral
    span_judgements: list[SpanJudgementIn] = Field(default_factory=list)
    notes: str = Field("", max_length=10000)


# --------------------------------------------------------------------------- #
# Serializers — the wire shapes, built by hand
# --------------------------------------------------------------------------- #
# Built as dicts rather than response models because two of the contract's rules
# are about a field's PRESENCE, not its value: ``GradingTask.stratum`` is absent
# when none was authored, and ``reader_verdicts``/``adjudication`` are absent
# unless the caller is an admin on a batch that has left ``open``. A response
# model would have to choose one exclude rule for every caller; presence here is
# decided per caller, which is the whole point.
def _question_json(q: GradingQuestion) -> dict[str, Any]:
    out: dict[str, Any] = {
        "type": q.type,
        "summary": q.summary,
        "description": q.description,
    }
    if q.id:
        out["id"] = q.id
    return out


def _verdict_json(v: GradingVerdictRecord) -> dict[str, Any]:
    return {
        "task_id": v.task_id,
        "reader": v.reader,
        "verdict": v.verdict,
        "span_judgements": [j.model_dump() for j in v.span_judgements],
        "extra_answers": [a.model_dump() for a in v.extra_answers],
        "notes": v.notes,
        "version": v.version,
        "saved_at": v.saved_at,
    }


def _adjudication_json(a: GradingAdjudicationRecord) -> dict[str, Any]:
    return {
        "task_id": a.task_id,
        "verdict": a.verdict,
        "span_judgements": [j.model_dump() for j in a.span_judgements],
        "notes": a.notes,
        "adjudicated_by": a.adjudicated_by,
        "version": a.version,
        "saved_at": a.saved_at,
    }


def _batch_json(batch: GradingBatchRecord, saved: dict[str, int]) -> dict[str, Any]:
    return {
        "id": batch.id,
        "name": batch.name,
        "kind": batch.kind,
        "status": batch.status,
        "rubric_sha256": batch.rubric_sha256,
        "order_seed": batch.order_seed,
        "readers": list(batch.readers),
        "task_count": batch.task_count,
        # Counts, never verdicts — this is all one reader learns of the other.
        "progress": [
            {"reader": r, "label": label_for(i), "saved": saved.get(r, 0)}
            for i, r in enumerate(batch.readers)
        ],
        "created_at": batch.created_at,
        "created_by": batch.created_by,
        "adjudicating_at": batch.adjudicating_at,
    }


def _task_json(
    task: GradingTaskRecord,
    *,
    own: GradingVerdictRecord | None,
    admin_view: bool,
    reader_verdicts: list[GradingVerdictRecord] | None = None,
    adjudication: GradingAdjudicationRecord | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": task.id,
        "batch_id": task.batch_id,
        "kind": task.kind,
        "pair_id": task.pair_id,
        "question": _question_json(task.question),
        "document": task.document.model_dump(),
        "claims": [c.model_dump() for c in task.claims],
        "extra_questions": [q.model_dump() for q in task.extra_questions],
        "readers": list(task.readers),
        "created_at": task.created_at,
        "created_by": task.created_by,
        "verdict": _verdict_json(own) if own is not None else None,
    }
    if task.stratum:
        out["stratum"] = task.stratum
    if admin_view:
        out["reader_verdicts"] = [_verdict_json(v) for v in (reader_verdicts or [])]
        out["adjudication"] = (
            _adjudication_json(adjudication) if adjudication is not None else None
        )
    return out


# --------------------------------------------------------------------------- #
# Authorization: who may see this batch, and as what
# --------------------------------------------------------------------------- #
class _View:
    """What the calling principal is, relative to one batch.

    ``reader_index`` is the caller's position in ``batch.readers`` — their label
    and their order — or ``None``. ``is_admin`` is the ``admin`` role, exactly as
    ``/v1/admin/*`` uses it; the contract says an admin's authority over a batch
    comes from the role, not from having created it ("it confers nothing —
    every admin may administer every batch").
    """

    __slots__ = ("batch", "is_admin", "reader_index", "subject")

    def __init__(
        self, batch: GradingBatchRecord, subject: str, is_admin: bool, reader_index: int | None
    ) -> None:
        self.batch = batch
        self.subject = subject
        self.is_admin = is_admin
        self.reader_index = reader_index

    @property
    def is_reader(self) -> bool:
        return self.reader_index is not None

    @property
    def admin_view(self) -> bool:
        """An admin sees the other readers' rows only once the read has ended."""
        return self.is_admin and self.batch.status != STATUS_OPEN


def _not_found(what: str) -> HTTPException:
    """The one 404. A batch/task the caller may not see is indistinguishable
    from one that does not exist — otherwise this surface would confirm a read's
    existence, and its reader list, to anyone who guessed an id."""
    return HTTPException(404, f"unknown grading {what}")


def _view_or_404(batch: GradingBatchRecord | None, principal: Principal, what: str) -> _View:
    if batch is None:
        raise _not_found(what)
    subject = principal.tenant
    is_admin = principal.role == ROLE_ADMIN
    index = batch.readers.index(subject) if subject in batch.readers else None
    if not is_admin and index is None:
        raise _not_found(what)
    return _View(batch, subject, is_admin, index)


def _require_admin(view: _View, action: str) -> None:
    """403, not 404: the caller can already see this batch, so refusing by role
    tells them nothing they did not know."""
    if not view.is_admin:
        raise HTTPException(403, f"{action} requires the admin role")


# --------------------------------------------------------------------------- #
# Create-time validation
# --------------------------------------------------------------------------- #
def _resolve_readers(readers: list[str]) -> list[str]:
    """Resolve the readers to subjects, in label order.

    The share-grantee vocabulary MINUS the group forms, resolved by the SAME
    function ``POST /v1/collections/{id}/shares`` and the ownership transfer use
    (:func:`ragstack.api.routers.collections._resolve_grantee`), so
    ``@service:<subject>`` / a full ``issuer:subject`` / a bare BV-BRC username
    cannot come to mean something different here. A group form parses fine and
    is then refused: a read is assigned to people, and label order is a list of
    people.
    """
    resolved: list[str] = []
    for raw in readers:
        grantee_type, subject = _resolve_grantee(raw, _DEFAULT_ISSUER)
        if grantee_type == GRANTEE_GROUP:
            raise HTTPException(
                422,
                f"a reader must be a person, not a group: {raw!r} resolves to the "
                f"group {subject!r}. Readers are listed individually, in label "
                f"order (first = 'A')",
            )
        if subject in resolved:
            raise HTTPException(
                422,
                f"{raw!r} resolves to {subject!r}, which is already reader "
                f"{label_for(resolved.index(subject))}: one subject cannot be two "
                "independent readers",
            )
        resolved.append(subject)
    return resolved


def _validate_task(t: TaskCreateIn, where: str) -> None:
    """Everything the schemas cannot say: that the spans point INTO the document.

    A span outside its document is not a cosmetic error — it is a highlight the
    reader will never see, on a pair they will nonetheless grade. Caught at
    create, when the importer can be fixed, rather than at read time.
    """
    for i, u in enumerate(t.document.units):
        if u.index != i:
            raise HTTPException(
                422, f"{where}: unit at position {i} declares index {u.index}; "
                "a unit's index MUST equal its position in `units`"
            )
        for k, s in enumerate(u.sentences):
            if s.i != k:
                raise HTTPException(
                    422, f"{where}: unit {i} sentence at position {k} declares i={s.i}; "
                    "a sentence's `i` MUST equal its position in `sentences`"
                )

    seen_sets: set[int] = set()
    for c in t.claims:
        if c.set_index in seen_sets:
            raise HTTPException(422, f"{where}: duplicate set_index {c.set_index}")
        seen_sets.add(c.set_index)
        if len(set(c.sources)) != len(c.sources):
            raise HTTPException(
                422, f"{where}: set {c.set_index} lists a source twice: {c.sources}"
            )
        for pos, sp in enumerate(c.spans, 1):
            if sp.unit >= len(t.document.units):
                raise HTTPException(
                    422,
                    f"{where}: set {c.set_index} span {pos} names unit {sp.unit}, but the "
                    f"document has {len(t.document.units)} unit(s)",
                )
            n = len(t.document.units[sp.unit].sentences)
            if sp.first_sentence > sp.last_sentence:
                raise HTTPException(
                    422,
                    f"{where}: set {c.set_index} span {pos} has first_sentence "
                    f"{sp.first_sentence} > last_sentence {sp.last_sentence}",
                )
            if sp.last_sentence >= n:
                raise HTTPException(
                    422,
                    f"{where}: set {c.set_index} span {pos} names sentence "
                    f"{sp.last_sentence} of unit {sp.unit}, which has {n} sentence(s)",
                )

    seen_q: set[str] = set()
    for q in t.extra_questions:
        if q.id in seen_q:
            raise HTTPException(422, f"{where}: duplicate extra_questions id {q.id!r}")
        seen_q.add(q.id)


def _to_task_record(
    t: TaskCreateIn, *, batch: GradingBatchRecord, position: int
) -> GradingTaskRecord:
    return GradingTaskRecord(
        id=uuid.uuid4().hex,
        batch_id=batch.id,
        kind=batch.kind,
        position=position,
        pair_id=t.pair_id,
        stratum=t.stratum,
        question=GradingQuestion(**t.question.model_dump()),
        document=GradingDocument(
            doc_id=t.document.doc_id,
            title=t.document.title,
            units=[
                GradingUnit(
                    index=u.index,
                    title=u.title,
                    sentences=[GradingSentence(i=s.i, text=s.text) for s in u.sentences],
                )
                for u in t.document.units
            ],
        ),
        claims=[
            GradingEvidenceSet(
                set_index=c.set_index,
                spans=[GradingSpan(**sp.model_dump()) for sp in c.spans],
                sources=list(c.sources),
            )
            for c in t.claims
        ],
        extra_questions=[GradingExtraQuestion(**q.model_dump()) for q in t.extra_questions],
        readers=list(batch.readers),
        created_at=batch.created_at,
        created_by=batch.created_by,
    )


def _check_judgements(
    task: GradingTaskRecord, judgements: list[SpanJudgementIn]
) -> list[GradingSpanJudgement]:
    """Every (set, span) must exist on the task — otherwise the row records a
    judgement of nothing, and the per-span κ silently counts it."""
    index = {(c.set_index, pos) for c in task.claims for pos in range(1, len(c.spans) + 1)}
    out: list[GradingSpanJudgement] = []
    for j in judgements:
        if (j.set, j.span) not in index:
            raise HTTPException(
                422,
                f"span judgement names set {j.set} span {j.span}, which this task "
                f"does not have (it has {sorted(index)})",
            )
        out.append(GradingSpanJudgement(set=j.set, span=j.span, judgement=j.judgement))
    return out


def _check_answers(
    task: GradingTaskRecord, answers: list[ExtraAnswerIn]
) -> list[GradingExtraAnswer]:
    by_id = {q.id: q for q in task.extra_questions}
    out: list[GradingExtraAnswer] = []
    for a in answers:
        q = by_id.get(a.id)
        if q is None:
            raise HTTPException(
                422,
                f"extra answer names question {a.id!r}, which this task does not ask "
                f"(it asks {sorted(by_id)})",
            )
        if q.answer_type == "yes-no" and a.answer not in ("yes", "no"):
            raise HTTPException(
                422,
                f"question {a.id!r} is yes-no; {a.answer!r} is neither 'yes' nor 'no'",
            )
        out.append(GradingExtraAnswer(id=a.id, answer=a.answer))
    return out


# --------------------------------------------------------------------------- #
# Batches
# --------------------------------------------------------------------------- #
async def _saved_counts(store: GradingStore, batch_id: str) -> dict[str, int]:
    rows = await store.list_verdicts(batch_id)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.reader] = counts.get(r.reader, 0) + 1
    return counts


@router.get("/grading/batches")
@fail_closed
async def list_grading_batches(
    principal: Principal = Depends(resolve_principal),
    store: GradingStore | None = Depends(get_grading_store),
) -> dict[str, Any]:
    """Every batch for an admin; for anyone else, exactly the batches that name
    the caller in ``readers``. Newest first, unpaginated.

    This is also the probe a client uses to learn whether a server implements
    grading at all — an implementation without it answers 404 — so it must not
    401 or 403 a caller who simply has no reads: an empty list is the answer.
    """
    s = _store(store)
    subject = principal.tenant
    is_admin = principal.role == ROLE_ADMIN
    batches = [b for b in await s.list_batches() if is_admin or subject in b.readers]
    # One verdict read per visible batch. A deployment runs a handful of reads
    # (the schema says so and refuses to paginate), so this stays a small
    # constant; it is the same query the batch detail endpoint makes.
    return {
        "batches": [_batch_json(b, await _saved_counts(s, b.id)) for b in batches]
    }


@router.post("/grading/batches", status_code=201)
@fail_closed
async def create_grading_batch(
    req: BatchCreateRequest,
    principal: Principal = Depends(resolve_principal),
    store: GradingStore | None = Depends(get_grading_store),
) -> dict[str, Any]:
    """Create a read with its tasks (admin only).

    Validated whole and stored whole: a duplicate ``pair_id``, a span outside
    its document, a duplicate ``set_index``, a group-form reader or two readers
    that resolve to one subject is a 422 and nothing is created. Nothing here
    is a partial batch — a reader who is handed half a draw would produce a
    κ over a sample nobody recorded.
    """
    s = _store(store)
    if principal.role != ROLE_ADMIN:
        # Stateless: no batch exists to hide, so 403 leaks nothing.
        raise HTTPException(403, "creating a grading batch requires the admin role")

    readers = _resolve_readers(req.readers)

    seen: set[str] = set()
    for i, t in enumerate(req.tasks):
        if t.pair_id in seen:
            raise HTTPException(
                422,
                f"tasks[{i}]: duplicate pair_id {t.pair_id!r}; it is the export's key "
                "column and must be unique within the batch",
            )
        seen.add(t.pair_id)
        _validate_task(t, f"tasks[{i}] ({t.pair_id})")

    batch = GradingBatchRecord(
        id=uuid.uuid4().hex,
        name=req.name,
        kind=req.kind,
        status=STATUS_OPEN,
        rubric_sha256=req.rubric_sha256,
        order_seed=req.order_seed,
        readers=readers,
        task_count=len(req.tasks),
        created_at=now_iso(),
        created_by=principal.tenant,
        adjudicating_at="",
    )
    tasks = [_to_task_record(t, batch=batch, position=i) for i, t in enumerate(req.tasks)]
    await s.create_batch(batch, tasks)
    log.info(
        "grading batch created id=%s name=%r tasks=%d readers=%s by=%s",
        batch.id, batch.name, batch.task_count, readers, principal.tenant,
    )
    return _batch_json(batch, {})


@router.get("/grading/batches/{batch_id}")
@fail_closed
async def get_grading_batch(
    batch_id: str,
    principal: Principal = Depends(resolve_principal),
    store: GradingStore | None = Depends(get_grading_store),
) -> dict[str, Any]:
    """The batch record, identical for an admin and for a reader: status, rubric
    hash, seed, readers, and how many tasks each reader has saved."""
    s = _store(store)
    view = _view_or_404(await s.get_batch(batch_id), principal, "batch")
    return _batch_json(view.batch, await _saved_counts(s, batch_id))


@router.delete("/grading/batches/{batch_id}", status_code=204)
@fail_closed
async def delete_grading_batch(
    batch_id: str,
    principal: Principal = Depends(resolve_principal),
    store: GradingStore | None = Depends(get_grading_store),
) -> Response:
    """Hard-delete a batch and everything under it (admin only).

    Exists so a harness that created a batch on a real server can remove it and
    verify by listing. It is not part of the read's protocol: a completed read
    is exported, not deleted.
    """
    s = _store(store)
    view = _view_or_404(await s.get_batch(batch_id), principal, "batch")
    _require_admin(view, "deleting a grading batch")
    await s.delete_batch(batch_id)
    log.warning(
        "grading batch deleted id=%s name=%r by=%s", batch_id, view.batch.name, principal.tenant
    )
    return Response(status_code=204)


@router.get("/grading/batches/{batch_id}/tasks")
@fail_closed
async def list_grading_tasks(
    batch_id: str,
    principal: Principal = Depends(resolve_principal),
    store: GradingStore | None = Depends(get_grading_store),
) -> dict[str, Any]:
    """The batch's tasks in the CALLER'S order, each with the caller's own
    verdict or null — never another reader's, whatever the batch status.

    An admin who is not a reader gets batch order, ``reader: null`` and a null
    verdict on every task; once the batch has left ``open`` every task also
    carries ``reader_verdicts`` and ``adjudication`` (the adjudication view).
    """
    s = _store(store)
    view = _view_or_404(await s.get_batch(batch_id), principal, "batch")
    tasks = await s.list_tasks(batch_id)

    if view.reader_index is not None:
        order = reader_order(view.batch.order_seed, view.reader_index, len(tasks))
        ordered = [tasks[i] for i in order]
    else:
        ordered = tasks

    own = {
        v.task_id: v
        for v in (await s.list_verdicts(batch_id) if view.is_reader else [])
        if v.reader == view.subject
    }
    by_task: dict[str, list[GradingVerdictRecord]] = {}
    adjs: dict[str, GradingAdjudicationRecord] = {}
    if view.admin_view:
        for v in await s.list_verdicts(batch_id):
            by_task.setdefault(v.task_id, []).append(v)
        adjs = {a.task_id: a for a in await s.list_adjudications(batch_id)}

    return {
        "batch_id": batch_id,
        "reader": view.subject if view.is_reader else None,
        "tasks": [
            _task_json(
                t,
                own=own.get(t.id),
                admin_view=view.admin_view,
                reader_verdicts=_in_label_order(by_task.get(t.id, []), view.batch.readers),
                adjudication=adjs.get(t.id),
            )
            for t in ordered
        ],
    }


def _in_label_order(
    rows: list[GradingVerdictRecord], readers: list[str]
) -> list[GradingVerdictRecord]:
    """Readers' rows in label order, omitting readers who have not saved one."""
    by_reader = {r.reader: r for r in rows}
    return [by_reader[r] for r in readers if r in by_reader]


@router.post("/grading/batches/{batch_id}/adjudicate")
@fail_closed
async def adjudicate_grading_batch(
    batch_id: str,
    principal: Principal = Depends(resolve_principal),
    store: GradingStore | None = Depends(get_grading_store),
) -> dict[str, Any]:
    """Freeze the readers' verdicts and open adjudication (admin only).

    The moment independence ends, recorded with a timestamp. Not idempotent:
    replaying it on a batch that is no longer ``open`` is a 409, so an
    accidental second click is not a silent no-op hiding a state the UI did not
    expect. Authorization is decided first — a reader gets 403 here even on a
    batch that is already adjudicating.
    """
    s = _store(store)
    view = _view_or_404(await s.get_batch(batch_id), principal, "batch")
    _require_admin(view, "adjudicating a grading batch")
    if not await s.begin_adjudication(batch_id, now_iso()):
        raise HTTPException(
            409,
            f"batch {batch_id!r} is {view.batch.status!r}, not 'open': the readers' "
            "rows are already frozen",
        )
    batch = await s.get_batch(batch_id)
    if batch is None:  # pragma: no cover — deleted between the two calls
        raise _not_found("batch")
    log.warning(
        "grading batch adjudication opened id=%s by=%s — reader rows are now frozen "
        "and visible to admins",
        batch_id, principal.tenant,
    )
    return _batch_json(batch, await _saved_counts(s, batch_id))


# --------------------------------------------------------------------------- #
# Tasks, verdicts, adjudications
# --------------------------------------------------------------------------- #
async def _task_view(
    s: GradingStore, task_id: str, principal: Principal
) -> tuple[GradingTaskRecord, _View]:
    task = await s.get_task(task_id)
    if task is None:
        raise _not_found("task")
    view = _view_or_404(await s.get_batch(task.batch_id), principal, "task")
    return task, view


@router.get("/grading/tasks/{task_id}")
@fail_closed
async def get_grading_task(
    task_id: str,
    principal: Principal = Depends(resolve_principal),
    store: GradingStore | None = Depends(get_grading_store),
) -> dict[str, Any]:
    """One task with the caller's own verdict or null. A reader never receives
    ``reader_verdicts`` or ``adjudication``, whatever the batch status."""
    s = _store(store)
    task, view = await _task_view(s, task_id, principal)
    own = await s.get_verdict(task_id, view.subject) if view.is_reader else None
    rows: list[GradingVerdictRecord] = []
    adj: GradingAdjudicationRecord | None = None
    if view.admin_view:
        rows = _in_label_order(
            [v for v in await s.list_verdicts(task.batch_id) if v.task_id == task_id],
            view.batch.readers,
        )
        adj = await s.get_adjudication(task_id)
    return _task_json(
        task, own=own, admin_view=view.admin_view, reader_verdicts=rows, adjudication=adj
    )


@router.put("/grading/tasks/{task_id}/verdict")
@fail_closed
async def put_grading_verdict(
    task_id: str,
    req: VerdictPutRequest,
    principal: Principal = Depends(resolve_principal),
    store: GradingStore | None = Depends(get_grading_store),
) -> dict[str, Any]:
    """Save or overwrite the CALLER'S own verdict — the one write a reader has.

    The row is keyed by (task, authenticated subject) and the body has no field
    naming a reader, so a caller can only ever write their own row. Always 200:
    a client does not care whether this was the first save, and ``version``
    says so anyway.
    """
    s = _store(store)
    task, view = await _task_view(s, task_id, principal)
    if not view.is_reader:
        # Reachable only by an admin (a non-reader non-admin got the 404 above).
        raise HTTPException(
            403,
            "only a reader of this batch has a verdict row; an admin adjudicates "
            f"instead (PUT /v1/grading/tasks/{task_id}/adjudication)",
        )
    if view.batch.status != STATUS_OPEN:
        raise HTTPException(
            409,
            f"batch {view.batch.id!r} is {view.batch.status!r}: the readers' rows are "
            "frozen for adjudication and cannot be changed",
        )
    row = GradingVerdictRecord(
        task_id=task_id,
        batch_id=task.batch_id,
        reader=view.subject,
        verdict=req.verdict,
        span_judgements=_check_judgements(task, req.span_judgements),
        extra_answers=_check_answers(task, req.extra_answers),
        notes=req.notes,
        version=0,  # assigned by the store: it owns the sequence
        saved_at=now_iso(),
    )
    return _verdict_json(await s.put_verdict(row))


@router.get("/grading/tasks/{task_id}/verdicts/{reader}")
@fail_closed
async def get_grading_verdict(
    task_id: str,
    reader: str,
    principal: Principal = Depends(resolve_principal),
    store: GradingStore | None = Depends(get_grading_store),
) -> dict[str, Any]:
    """The caller's own row — or any reader's, for an admin once the batch has
    left ``open``.

    Every other case is a 404, deliberately indistinguishable: a reader naming
    another reader, an admin naming a reader while the read is still open, a
    subject who is not a reader at all, and a reader who has simply not saved
    yet all answer the same way. This is the endpoint the independence test
    probes.
    """
    s = _store(store)
    _task, view = await _task_view(s, task_id, principal)
    own = view.is_reader and reader == view.subject
    if not (own or view.admin_view):
        raise _not_found("verdict")
    row = await s.get_verdict(task_id, reader)
    if row is None:
        raise _not_found("verdict")
    return _verdict_json(row)


@router.put("/grading/tasks/{task_id}/adjudication")
@fail_closed
async def put_grading_adjudication(
    task_id: str,
    req: AdjudicationPutRequest,
    principal: Principal = Depends(resolve_principal),
    store: GradingStore | None = Depends(get_grading_store),
) -> dict[str, Any]:
    """The joint-read verdict (admin only, batch ``adjudicating``).

    The verdict the study USES (SPEC §6.6.3). The readers' own rows are never
    modified by it — the pre-adjudication κ is the one reported, and it needs
    the originals.
    """
    s = _store(store)
    task, view = await _task_view(s, task_id, principal)
    _require_admin(view, "saving a joint-read verdict")
    if view.batch.status == STATUS_OPEN:
        raise HTTPException(
            409,
            f"batch {view.batch.id!r} is still 'open': adjudicate it first "
            f"(POST /v1/grading/batches/{view.batch.id}/adjudicate)",
        )
    row = GradingAdjudicationRecord(
        task_id=task_id,
        batch_id=task.batch_id,
        verdict=req.verdict,
        span_judgements=_check_judgements(task, req.span_judgements),
        notes=req.notes,
        adjudicated_by=principal.tenant,
        version=0,  # assigned by the store
        saved_at=now_iso(),
    )
    return _adjudication_json(await s.put_adjudication(row))


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
#: ``s0_rdev_score.read_verdicts``'s header, byte-for-byte.
_CSV_HEADER = ("pair_id", "verdict", "notes")


def _csv_text(rows: list[tuple[str, str, str]]) -> str:
    """RFC 4180 with EVERY field quoted, ``\\n`` endings, trailing newline —
    the form ``GradingExportResponse`` pins so two implementations produce the
    same bytes and the scorer reads either unchanged."""
    buf = io.StringIO(newline="")
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    w.writerow(_CSV_HEADER)
    w.writerows(rows)
    return buf.getvalue()


@router.get("/grading/batches/{batch_id}/export")
@fail_closed
async def export_grading_batch(
    batch_id: str,
    principal: Principal = Depends(resolve_principal),
    store: GradingStore | None = Depends(get_grading_store),
) -> dict[str, Any]:
    """The read's results in the scorer's shape (admin only, batch not ``open``).

    One ``rdev_verdicts_<label>.csv`` text per reader plus
    ``rdev_verdicts_ADJ.csv``, and the same rows as JSON with the per-span
    judgements and extra answers the CSV cannot carry. Refused with 409 while
    the batch is ``open``: exporting mid-read would show an admin the readers'
    rows before independence ends, which is what ``POST …/adjudicate`` is for.
    """
    s = _store(store)
    view = _view_or_404(await s.get_batch(batch_id), principal, "batch")
    _require_admin(view, "exporting a grading batch")
    batch = view.batch
    if batch.status == STATUS_OPEN:
        raise HTTPException(
            409,
            f"batch {batch_id!r} is still 'open': adjudicate it first "
            f"(POST /v1/grading/batches/{batch_id}/adjudicate). Exporting mid-read "
            "would end reader independence without recording that it ended",
        )

    tasks = await s.list_tasks(batch_id)
    verdicts = await s.list_verdicts(batch_id)
    adjudications = {a.task_id: a for a in await s.list_adjudications(batch_id)}
    by_key = {(v.task_id, v.reader): v for v in verdicts}

    sheets: list[dict[str, Any]] = []
    for i, subject in enumerate(batch.readers):
        label = label_for(i)
        rows = [
            (
                t.pair_id,
                by_key[(t.id, subject)].verdict if (t.id, subject) in by_key else "",
                by_key[(t.id, subject)].notes if (t.id, subject) in by_key else "",
            )
            for t in tasks
        ]
        sheets.append(
            {
                "filename": f"rdev_verdicts_{label}.csv",
                "reader": subject,
                "label": label,
                "content": _csv_text(rows),
            }
        )
    adj_rows = [
        (
            t.pair_id,
            adjudications[t.id].verdict if t.id in adjudications else "",
            adjudications[t.id].notes if t.id in adjudications else "",
        )
        for t in tasks
    ]
    sheets.append(
        {
            "filename": "rdev_verdicts_ADJ.csv",
            "reader": None,
            "label": "ADJ",
            "content": _csv_text(adj_rows),
        }
    )

    verdict_rows: list[dict[str, Any]] = []
    adjudication_rows: list[dict[str, Any]] = []
    for t in tasks:
        for i, subject in enumerate(batch.readers):
            v = by_key.get((t.id, subject))
            if v is None:
                continue
            row = _verdict_json(v)
            row.update({"pair_id": t.pair_id, "task_id": t.id, "label": label_for(i)})
            if t.stratum:
                row["stratum"] = t.stratum
            verdict_rows.append(row)
        a = adjudications.get(t.id)
        if a is not None:
            arow = _adjudication_json(a)
            arow.update({"pair_id": t.pair_id, "task_id": t.id})
            if t.stratum:
                arow["stratum"] = t.stratum
            adjudication_rows.append(arow)

    return {
        "batch_id": batch.id,
        "name": batch.name,
        "kind": batch.kind,
        "status": batch.status,
        "rubric_sha256": batch.rubric_sha256,
        "order_seed": batch.order_seed,
        "exported_at": now_iso(),
        "readers": [
            {"subject": r, "label": label_for(i)} for i, r in enumerate(batch.readers)
        ],
        "csv": sheets,
        "verdicts": verdict_rows,
        "adjudications": adjudication_rows,
    }
