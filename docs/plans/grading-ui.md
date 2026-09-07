# Grading in the RAGStack UI — plan

*2026-09-06. Status: `PROPOSED`; phase 1 (contract) landed as #505. Owner decision: the two-reader label validation has to reach
its readers through RAGStack itself in the near term, not through a session artifact. This is
the plan for that, sized so the study is not blocked on it.*

## 1. What it is for, in order

1. **The study's human reads.** The R-dev read (≥ 100 pairs, two independent readers, six
   verdicts, per-span judgements) and the R-conf read after it, per
   `results/design/SPEC-confirmation-run.md` §6.6 and revision 3 §3.7 item 4. Today this runs
   on a claude.ai artifact with an honour-system reader split. The scorer that consumes the
   verdicts already exists (`results/stage0/s0_rdev_score.py`, #500) and defines the export
   shape.
2. **The pointed-question read** (revision 3 §11): ≈ 50 pairs with one extra question per pair.
3. **Later, production feedback.** The frontend's `FeedbackControl` is a thumbs up/down on an
   answer that never leaves the browser. The same verdict model can carry "was this citation
   the right passage?" from real users once the study's shape has settled. Out of scope for
   phase 1; the data model is designed so it fits without a migration.

## 2. What exists that this reuses

| piece | where | reuse |
|---|---|---|
| Evidence rendering with highlighted spans | `frontend/src/components/HighlightedContent.tsx`, `EvidenceView.tsx` | the pair view's document pane |
| Answer feedback (ephemeral) | `frontend/src/components/FeedbackControl.tsx`, `lib/feedback.ts` | the verdict-chip pattern; replaced by a server call |
| Durable per-tenant persistence | `collection_store_backend`/`job_store_backend` = `postgres` (`postgres_dsn`), sqlite fallback | the verdict tables use the same backend switch |
| Identity and authz | `issuer:subject` principals; owner / share / public scopes (ADR-0003) | reader assignment is by subject; admin = collection owner or tenant admin |
| Contract discipline | `contracts/openapi.yaml` + `contracts/schemas/*.json` (`additionalProperties: false`), conformance suite | new resources land there first, then Python, then Go |
| The pilot sheet | artifact `e1a4ac31…` (session scratch; source in `~/Development/worktrees/phase0-rescue/artifact/` to be added) | the visual design of the pair view, the reading guide, the verdict legend |
| Verdict scorer | `results/stage0/s0_rdev_score.py` | consumes the export unchanged |

## 3. Design

### 3.1 Resources (contract first)

Three resources under `/v1/grading/`, all tenant-scoped, all `additionalProperties: false`.

**`GradingTask`** — one thing to grade.
```
id, tenant, kind: "evidence-read" | "pointed-read" | "citation-feedback",
batch_id,                       # the read this task belongs to (R-dev pilot, R-dev, R-conf, …)
question: { type, summary, description, need_type? },
document: { doc_id, title, units: [{index, title, sentences: [{i, text}]}] },
claims: [{ set_index, spans: [{unit, first_sentence, last_sentence, text}], sources: ["scout"|"qwen"|…] }],
extra_questions: [{ id, text }],  # e.g. r3 §11's "does another passage answer it?"
readers: [subject…],             # who may grade it
created_at, created_by
```
The document is stored **denormalised per task** (segmented text as shown), because a read
must be reproducible against exactly what the reader saw, independent of later re-ingests.

**`GradingVerdict`** — one reader's answer to one task. One row per (task, reader); a
re-save overwrites and bumps `version`, previous versions kept.
```
task_id, reader (subject), verdict: correct | wrong-location | non-minimal | missed-evidence | correctly-none | ambiguous,
span_judgements: { "<set>.<span>": located | wrong | non-minimal },
extra_answers: { "<id>": value }, notes, version, saved_at
```

**`GradingBatch`** — the read: name, protocol hash (rubric sha256), reader list, order seed,
status (`open` → `adjudicating` → `closed`), and the export.

### 3.2 Endpoints

| method | path | who | what |
|---|---|---|---|
| `POST` | `/v1/grading/batches` | admin | create a batch; body carries tasks or a reference to a committed package (the R-dev `rdev_sample.json` + labels) |
| `GET` | `/v1/grading/batches/{id}` | admin, readers | batch status; per-reader progress counts (never verdicts) |
| `GET` | `/v1/grading/batches/{id}/tasks` | reader | the reader's tasks **in the reader's own seeded order**; each task carries the reader's own verdict if any, **never another reader's** |
| `GET` | `/v1/grading/tasks/{id}` | reader | one task |
| `PUT` | `/v1/grading/tasks/{id}/verdict` | reader | save/overwrite the caller's verdict |
| `POST` | `/v1/grading/batches/{id}/adjudicate` | admin | moves to `adjudicating`; readers' verdicts become visible to the adjudicator only |
| `PUT` | `/v1/grading/tasks/{id}/adjudication` | admin | the joint-read verdict |
| `GET` | `/v1/grading/batches/{id}/export` | admin | CSVs in the scorer's shape: `rdev_verdicts_<reader>.csv` per reader (+ `_ADJ.csv`), plus a JSON with span judgements and extra answers |

**Independence is enforced server-side**, which the artifact could not do: a reader can read
and write only their own verdict rows; a 404 for another reader's, per the existence-hiding
convention. The shuffled order is computed from `(batch.seed, reader)` on the server.

### 3.3 Storage

Two tables (`grading_task`, `grading_verdict`) plus `grading_batch`, on the same backend
switch as the job store: `postgres` on the tenants, `sqlite` for local dev and tests, `memory`
for the keyed conformance boot. Verdicts are append-only by version. No store client is
constructed by the study harness; the export is a file the scorer reads.

### 3.4 UI

A **Grading** view beside Explore / Evidence / Ops, visible only when the signed-in subject
has tasks:

* **Batch list** — batches with the reader's progress.
* **Pair view** — the pilot sheet's layout, in the frontend's components: a reading guide
  (collapsible; the six verdicts with meaning, example and "counts as"), the **question**
  labeled as such (case + need type, with the need type explained as *what the clinician has
  to decide*, not a synthesis), the **claimed answer** (spans with source tags, per-span
  judgement toggles, jump links), the **document** (numbered sentences, highlighted spans,
  reusing `HighlightedContent`), and a fixed verdict bar (six chips with hover meaning and
  "counts as", notes, save-and-advance).
* **Adjudication view** (admin) — both readers' verdicts side by side per task, the joint
  verdict, disagreement count.
* **Export** button (admin).

No `dangerouslySetInnerHTML` (the existing XSS guard applies); sentences render as text nodes.

### 3.5 What does not change

Query, ingest, collections, authz semantics. The study's protocol and scorer. The rubric.

## 4. Phases, with what each unblocks

| phase | work | unblocks | size |
|---|---|---|---|
| **0** | Move the pilot sheet's HTML source into the repo (`docs/plans/results/stage0/artifacts/rdev-pilot-read/`) so the design reference is durable | nothing; hygiene | 1 h |
| **1** | Contract: schemas + OpenAPI for the three resources; fixtures; conformance tests (keyed boot, two reader principals, independence checks) — **done, #505** (`contracts/schemas/grading_*.json`, eleven `/v1/grading/*` operations, `contracts/fixtures/grading/`, `conformance/test_grading.py` + `test_grading_fixtures.py`; the module skips as *not implemented* until phase 2 mounts the paths) | Python and Go can start | 1 day, strongest model — external contract |
| **2** | Python: stores (memory/sqlite/postgres), router, export; an importer that turns the committed R-dev package (`rdev_sample.json` + a labels file or the r3 union) into a batch — **done, #509** (`python/ragstack/grading/{models,store}.py`, `python/ragstack/api/routers/grading.py` mounted at `/v1`, `python/scripts/grading_import.py`, `GRADING_STORE_BACKEND`/`GRADING_STORE_PATH`; `conformance/test_grading.py` passes 12/12 on the keyed boot instead of skipping) | the read can run on the dev tenant | 2 days |
| **3** | Frontend: Grading view, pair view, adjudication, export | readers use RAGStack, not the artifact | 2–3 days |
| **4** | Go: handlers to contract; conformance green on both | parity rule satisfied | 1–2 days |
| **5** | Production feedback: `FeedbackControl` posts a `citation-feedback` task+verdict for the answer's citations | later; needs the query path to expose chunk spans it already has | not scheduled |

Phases 1–3 are the near-term path; 4 follows per the repo's parity rule before any release
tag. Each phase is one worktree, one PR, one independent review; phases 1 and 2 are
implementation tasks for a Sonnet/Opus subagent with the contract reviewed by Fable; phase 3
the same with a screenshot-backed review.

## 5. Acceptance

* Two readers on the dev tenant complete the ten-pair pilot through the Grading view; the
  export drops into `s0_rdev_score.py` unchanged and produces the §6.6.4 table.
* A reader cannot obtain another reader's verdict by any endpoint (conformance test, both
  personas).
* The per-reader order matches `s0_rdev.py`'s seeded shuffle for the same seed and reader
  letter (so a read started on the artifact can continue in the UI).
* Rubric hash recorded on the batch and shown in the view.

## 6. Open questions

1. **Reader identity for people outside the tenant's usual users.** The live tenants use
   BV-BRC token auth; the dev tenant can carry keyed principals. The second reader needs one
   of these. Decide per read.
2. **Where the first real read runs** — dev tenant (recommended: no production stores, keyed
   principals available) vs demo.
3. **Unit-level κ.** The scorer computes pair-level κ; per-span judgements are captured but
   the unit-level statistic in §6.6.3 needs a small scorer extension. Add it in phase 2.

## 7. Non-goals

Replacing the study's labeling harness; a general annotation tool; anything on the two live
tenants before the study's reads are done.
