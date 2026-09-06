# RUBRIC — minimal-sufficient evidence spans for the chunking confirmation run

**Status: FROZEN.** This document is written and hashed **before any labeling call is
issued**, dev or confirmation (SPEC §6.6.1, P.5). Its sha256 is recorded in
`phase0/stage0/provenance-stage0.json` and in the PREREG. A change after the freeze is an
amendment: dated, diffed, re-hashed, and accompanied by a fresh ≥100-pair R-dev read
(§6.6.4). It is not an edit.

**Audience.** Two independent human readers (the R-dev and R-conf reads) *and* the
`Llama-4-Scout` labeler prompt, which is generated from this document. Both must apply the
same definitions or the κ(Scout–human) statistic measures nothing.

**Scope.** You are shown one **topic** (a clinical case narrative with a stated need type —
diagnosis, test, or treatment) and one **document** (a biomedical article, segmented into
numbered structural units, each unit segmented into numbered sentences). TREC has already
judged this document relevant to this topic at grade ≥ 1. Your job is **not** to re-judge
relevance. Your job is to say **where in this document** the evidence for that relevance
lives — or that it is not localizable.

You never see a retrieval ranking, a chunk boundary, an arm name, a score, or another
reader's verdicts. If you believe you can infer any of these, ignore the inference.

---

## 1. The four frozen definitions (SPEC §6.2.1 D1–D4)

### D1 — span

A **span** is a contiguous run of **whole sentences inside exactly one structural unit**.

* Identified as `(unit_id, first_sentence_idx, last_sentence_idx)`, inclusive.
* Materialised as a `[start_char, end_char)` half-open interval into the exact indexed
  text.
* **A span never crosses a unit boundary.** Evidence that spans two units is expressed as a
  multi-span set (D2), never as one span.
* Sentence segmentation is fixed by the pipeline; you select whole sentences by number, so
  you cannot produce a partial-sentence span.
* Prefer the **shortest** run of sentences that carries the claim. A span is not a
  paragraph by default.

### D2 — evidence set

An **evidence set** is a **minimal** collection of one or more spans that together justify
this document's relevance to this topic's stated clinical need.

* *Minimal* means: **no proper subset of its spans suffices.** If you can delete a span and
  the remainder still justifies relevance, the set was not minimal — delete it.
* A document may carry **several alternative evidence sets** (several independent places
  where the relevance is established). List them all, as separate sets.
* A set may span **several units** (e.g. a Methods sentence naming the population plus a
  Results sentence carrying the effect). All of its spans must then be supplied together
  for it to count as covered (D4).
* "Justify relevance" is read against the topic's **type**:
  * *diagnosis* — the text bears on identifying what the patient has;
  * *test* — the text bears on which investigation to order or how to interpret it;
  * *treatment* — the text bears on what to do for the patient.
  A document about the right disease but the wrong axis (a treatment trial for a
  *diagnosis* topic) may still be relevant, but say so through the span you choose: pick
  the text that does the work for **this** need, not the text that merely names the
  disease.

### D3 — evidence unit (the endpoint's denominator atom)

One **unit** per **(document, evidence-set)**, after this deduplication, in this order:

1. **Within-document merge.** Two evidence sets of the same document merge into one unit
   iff their **character-span union Jaccard ≥ 0.5**. The merged unit's canonical span list
   is the **smaller** of the two sets — so merging can never make a unit *easier* to cover.
2. **Within-document containment.** If set *A*'s spans are a subset of set *B*'s spans,
   keep *A* and drop *B*. Alternative locations survive; strictly weaker restatements do
   not.
3. **Across documents: never merge.** The same finding restated in two documents is **two
   units**. Retrieving either document covers its own unit.
4. **Cap.** ≤ 12 units per topic, by seeded subsampling **stratified by source document**,
   applied after 1–3.

Rules 1–4 are applied **mechanically, downstream, by the pipeline**. You do not apply them
by hand. What you must do is make them *applicable*: emit each genuinely alternative
location as its own set, and do not pad a set with redundant spans.

### D4 — covered

A unit is **covered** at budget *B* iff **every span of its canonical span list is fully
contained** in the packed context's per-document character-span union.

* Full containment of the **whole** span. Not intersection. Not a token fraction. Not
  "most of it".
* **Several admitted chunks of the same document may jointly cover one unit** — containment
  is evaluated against the *union* of all admitted chunks of that document, so a span split
  across two adjacent admitted chunks counts as covered iff the union covers it
  contiguously.
* **Chunks of different documents never combine.** A unit is document-local by D3.
* A unit whose document was not packed at all is uncovered.
* Consequence, accepted and reported rather than hidden: a chunk boundary falling
  mid-span makes the unit uncovered even though most of the evidence was supplied. That is
  a real cost of chunking and the endpoint is *meant* to charge it. `EUC` is therefore a
  **lower bound** on "evidence the generator could have used". A ≥ 0.9-character-overlap
  variant is computed as a **descriptive column only** — it is never the primary and no
  decision is read off it.

---

## 2. The questions §6.6.1 requires this rubric to settle

| question | answer |
|---|---|
| distinct unit vs redundant restatement | D3 rules 1–2, applied mechanically. Your obligation: emit alternatives as separate sets; never restate one location as two sets differing by a sentence. |
| does "covered" mean sufficiency or intersection | **Full containment of every span** (D4). Intersection is not coverage. |
| may several retrieved chunks jointly cover one unit | **Yes**, within one document (D4). Across documents, **no**. |
| missing evidence | **"no localizable evidence" is a legal verdict.** Use it when the document is relevant by aboutness alone (a review that never states the fact, a case series whose relevance is the topic area). The pair then contributes **no units**. The rate is reported per grade. A topic driven below 3 units by such verdicts is excluded by §8.5.6, not here. |
| incorrect spans (the quote verifies but the location is wrong) | verdict `wrong-location`. It counts as a **label error** and enters the perturbation bound. |
| ambiguous units (readers disagree whether it is one unit or two) | adjudicate by joint read (§6.6.3). If adjudication cannot resolve it, mark the unit `ambiguous`; a sensitivity is run with those units dropped. |
| what triggers revision / relabeling / gate failure | §6.6.4's table, reproduced in §5 below. |

---

## 3. Worked examples — all drawn from **development** topics only

The five examples below are the ones §6.6.1 requires, including the near-duplicate case.
They use dev topics `2014_5`, `2015_23`, `2016_9`, `2014_29`, `2015_8`. **No confirmation
topic appears anywhere in this document.**

### E1 — one span, one set (the ordinary case)

*Topic `2014_5` (diagnosis): 56-year-old woman, shortness of breath 3 weeks after
mastectomy, right calf tenderness, decreased right basal breath sounds, elevated D-dimer.*

Document: a cohort study of post-operative venous thromboembolism. Unit 3 (`Results`),
sentence 7: *"Pulmonary embolism occurred in 14 of 412 patients (3.4%) within 30 days of
mastectomy, and 11 of these had concurrent deep venous thrombosis of the calf."*

* **One evidence set, one span**: `(unit 3, s7, s7)`.
* The sentence alone connects the topic's presentation (post-mastectomy, calf findings,
  dyspnoea) to the diagnosis. Adding sentence 6 (the cohort's age distribution) would make
  the set **non-minimal**: delete it.
* Do **not** extend the span to the whole Results paragraph "for context". Context is not
  evidence.

### E2 — one set, two spans (multi-passage evidence)

*Topic `2015_23` (treatment): 18-year-old back from Asia, high fever, severe headache,
joint pain, leukopenia, raised haematocrit, thrombocytopenia.*

Document: a dengue management trial. Unit 2 (`Methods`), sentence 4: *"Patients with
confirmed dengue and haematocrit rise > 20% were randomised to colloid or crystalloid
resuscitation."* Unit 4 (`Results`), sentence 2: *"Colloid recipients required fewer rescue
boluses (12% vs 31%, p = 0.004)."*

* **One evidence set, two spans**: `(unit 2, s4, s4)` and `(unit 4, s2, s2)`.
* Neither alone justifies relevance to a *treatment* need: the Methods sentence names the
  population without a recommendation; the Results sentence gives an effect without saying
  for whom. Together they are minimal and sufficient.
* Under D4 this unit is covered **only if both spans are fully supplied**. That is
  deliberate: a context containing half of this argument does not let a generator make the
  recommendation.

### E3 — two alternative sets on one document (both survive)

*Topic `2016_9` (diagnosis): infant, respiratory distress syndrome, extreme prematurity,
diffuse bilateral opacities with increased lung volumes.*

Document: a review of neonatal lung disease with a radiology section and a pathophysiology
section.

* Set A: `(unit 5 "Radiographic findings", s2, s3)` — the ground-glass/air-bronchogram
  description with the volume caveat.
* Set B: `(unit 7 "Differential diagnosis", s1, s1)` — *"Increased rather than decreased
  lung volumes should prompt consideration of transient tachypnoea or pneumonia rather than
  surfactant deficiency."*
* These are **two evidence sets → two units** (their span-union Jaccard is 0, far below the
  0.5 merge threshold). Both are emitted. Retrieval that supplies either one covers that
  one; nothing about A makes B redundant.
* This is the shape that makes `EUC` continuous rather than binary, and it is the shape you
  should look hardest for.

### E4 — the near-duplicate case (what D3 rule 1 merges, and what it does not)

Same document as E3.

* Candidate set C: `(unit 5, s2, s3)` — identical to set A.
  → **Merged** with A (Jaccard 1.0). Never emit it twice.
* Candidate set D: `(unit 5, s2, s4)` — set A plus one trailing sentence restating the
  finding.
  → Span-union Jaccard with A is `|A| / |D|` ≈ 0.7 ≥ 0.5, so D3 rule 1 **merges** D into A
  and keeps **A**'s (smaller) span list as canonical. Equivalently, D3 rule 2's containment
  rule keeps A and drops D. **You should not have emitted D at all**: it is non-minimal,
  and emitting it is the `non-minimal` label error.
* Candidate set E: `(unit 5, s2, s3)` **and** `(unit 5, s9, s9)` — set A plus an unrelated
  sentence from the same section.
  → Jaccard with A is below 0.5 only if s9 is long; do not rely on the arithmetic. **Emit
  E only if s9 is genuinely required.** If s9 is a second, independent location, emit it as
  its **own set** `(unit 5, s9, s9)`, not bolted onto A.

The operational rule that follows from E4, and the single most important instruction in
this rubric: **one set per argument, the shortest sentences that carry it. Two locations
are two sets, never one set with four spans.**

### E5 — "no localizable evidence" (a legal verdict, and when to use it)

*Topic `2014_29` (treatment): 51-year-old smoker, hypertension, diabetes, menopausal,
needs osteoporosis prevention.*

Document: an editorial on the burden of post-menopausal bone disease. It asserts that
prevention matters, cites no regimen, reports no outcome, and states no recommendation.

* Verdict: **`no localizable evidence`**. The document is genuinely *about* the topic — a
  human judge graded it relevant — but no span of it justifies a *treatment* decision.
* This pair contributes **zero units**. It is not an error and it is not a failure: it is
  information, and its rate is reported per grade.
* Do **not** invent a span to avoid returning nothing. A fabricated span is worse than an
  honest decline: it becomes a unit that no arm can legitimately cover, and it biases every
  contrast that uses it.

### E6 — the deep-section trap (why the human read stratifies on it)

*Topic `2015_8` (diagnosis): 10-year-old, poor concentration, daytime sleepiness, failure
to thrive, restless snoring sleep with gasping.*

Document: a paediatric sleep-apnoea cohort whose **abstract** already states *"adenotonsillar
hypertrophy was the commonest cause of obstructive sleep apnoea in children presenting with
failure to thrive"*, and whose Discussion restates it at length.

* The correct evidence set is the **abstract** span, not the Discussion span. The abstract
  states the fact, minimally, and is the shorter sufficient location.
* The Phase-0 failure mode this guards against: attributing evidence to a deep section when
  the abstract already carried it. It matters here because chunk arms differ most in what
  they do to deep sections, so a systematically deep-shifted label set would not be
  neutral across arms.
* **Reader instruction (R-dev / R-conf, deep-section stratum):** when a supplied span sits
  outside the abstract/introduction, check explicitly whether the abstract already contained
  the same evidence. If it did, the supplied span is `wrong-location`.

---

## 4. Reader protocol (R-dev and R-conf)

For **every** pair — including every pair where the labeler returned *no localizable
evidence* — answer both questions:

**(a) Are the supplied spans correct and minimal?**
**(b) Is there evidence in this document that the labeler did **not** supply?**

Record exactly one pair-level verdict:

| verdict | meaning |
|---|---|
| `correct` | every supplied span is correctly located and minimal, and nothing material was missed |
| `wrong-location` | a supplied span's quote verifies but the location does not justify relevance (includes the E6 deep-section case) |
| `non-minimal` | a supplied set contains a span that can be deleted without losing sufficiency |
| `missed-evidence` | there is evidence in the document the labeler did not supply (applies to model positives *and* model negatives) |
| `correctly-none` | the labeler returned "no localizable evidence" and the reader agrees |
| `ambiguous` | the reader cannot decide whether this is one unit or two, or whether a span is sufficient |

Also record, per supplied span, a unit-level covered/not-applicable judgement so
κ(human–human) can be computed at unit level as well as at pair level.

Readers work **independently** on the same blinded subset, with the pair order shuffled
independently per reader. Disagreements are adjudicated by joint read; the **adjudicated**
verdict is used and the **pre-adjudication** κ is the one reported.

---

## 5. Acceptance criteria (SPEC §6.6.4, reproduced so the reader can see the stakes)

| statistic | value | consequence |
|---|---|---|
| κ(human–human) | < 0.40 | `RUBRIC_FAILURE` — the study stops |
| κ(human–human) | 0.40–0.60 | one dated rubric revision + a fresh ≥100-pair R-dev; a second shortfall stops the study |
| κ(Scout–human) | < 0.40 | stop |
| κ(Scout–human) | 0.40–0.60 | proceed with every claim capped `MODERATE` |
| κ(Scout–human) ≥ 0.60 **or** positive-class agreement ≥ 0.85 | — | full-strength claims permitted |
| label-error rate (`wrong-location` + `non-minimal`, Wilson upper) | > 0.10 | relabel; if still > 0.10, label-limited, no NI conclusion |
| `missed-evidence` rate (Wilson upper) | > 0.15 | NI verdicts downgraded to UNRESOLVED-BY-LABEL-OMISSION |
| hallucinated-span rate | > 0.05 | stop |
| self-consistency | < 0.90 | stop |

---

## 6. Output format the labeler must emit

Strict JSON, no prose outside it:

```json
{"evidence_sets": [
   {"spans": [{"unit": 3, "first_sentence": 7, "last_sentence": 7,
               "first_words": "Pulmonary embolism occurred in 14 of 412 patients",
               "last_words": "concurrent deep venous thrombosis of the calf"}]}
 ]}
```

* `evidence_sets: []` **is** the "no localizable evidence" verdict.
* `first_words` / `last_words` are **verbatim** — the first and last ten words of the span,
  copied from the numbered sentences shown. A deterministic checker verifies both as
  substrings of the document; a failure gets **one** re-prompt, then the pair is dropped and
  the **hallucinated-span rate** is incremented (gate ≤ 0.05).
* Unit and sentence indices are the ones printed in the prompt. Never invent an index.

---

*This rubric governs both the model labeler and the human readers. Nothing in it depends on
any retrieval result, any chunk size, or any arm.*
