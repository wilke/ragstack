# G1 — standard operating procedure for human relevance rating

**Companion to [`docs/g1-retrieval-protocol.md`](g1-retrieval-protocol.md).** Version 1.0,
2026-08-01.

The protocol says *what* must be measured and *why*. This document says **who does what, in
what order, and what disqualifies the result**. It covers the pilot's human-label track: the
queries, the graded `(query, chunk)` judgments, the κ that gates them, and the artefacts that
make a round reproducible.

Where this SOP and the protocol disagree, the protocol wins and the disagreement is a bug in
this document. Where this SOP adds a rule the protocol does not have — the κ bands in §6 are
the main one — it says so explicitly and gives the argument.

**Apparatus.**

| Thing | Where |
|---|---|
| Rating tool (single HTML file, opened from disk) | `python/scripts/eval/rating_tool/index.html` |
| Query generation / ingestion | `python/scripts/eval/g1_make_queries.py` |
| Pooling, subsampling, assignment | `python/scripts/eval/g1_make_pool.py` |
| Agreement statistics | `python/scripts/eval/g1_agreement.py` |
| Shared vocabulary (ids, blinding, IDF overlap) | `python/scripts/eval/_g1_rating.py` |
| κ math | `python/scripts/eval/_stats.py` |
| Corpus (98 open-access PMC PDFs) | `/rag/data/g1-corpus/` + `manifest.json` |

**Roles.** Every round names these in its manifest; one person may hold several, with the
exclusions noted.

| Role | Does | Must not |
|---|---|---|
| **Study lead** | Runs the scripts, sets the seeds, holds the gold calibration key, adjudicates | Rate live items in the round they lead |
| **Rater** | Rates assigned pairs | Rate a query they wrote (§3.4); see any configuration or LLM label (§4.2) |
| **Query reviewer** | The two-reviewer plausibility audit (§3.3) | — |
| **Adjudicator** | Resolves 0-vs-2 splits (§5) | Have rated the pair being adjudicated |

---

## 1. The round, end to end

Follow in order. Each step names the artefact it must produce before the next begins.

1. **Fix the queries** (§3) → `<fixture>.jsonl`, `<fixture>.manifest.json`.
   Queries are pinned **before** any retrieval run (protocol §4.2). A query added after a
   sweep is a different experiment.
2. **Run the sweep** (`g1_library_sweep.py`, outside this SOP) → `raw/<cell_id>.rankings.jsonl`
   per cell.
3. **Judge the pool with the LLM judge** (outside this SOP) → `qrels/pilot/judgments.jsonl`.
   Optional at this point but strongly preferred: with judge labels in hand, the human
   subsample can be stratified on grade (§4.1), which is what makes κ(judge–human) precise.
4. **Pool, subsample, assign** (§4) → `pool.jsonl`, `subsample.jsonl`,
   `assignments/<rater>.jsonl`, `calibration.jsonl`, `manifest.json`.
5. **Fill the calibration key** (§2.3) → `calibration_key.jsonl`.
6. **Onboard and calibrate every rater** (§2) → calibration exports; a pass/fail per rater.
7. **Rate** (§4.3) → one judgments export per rater per session.
8. **Adjudicate** (§5) → `adjudications.jsonl`.
9. **Score agreement** (§6) → `agreement.json`, `agreement.md`, a band, and a consequence.
10. **Record** (§7) — everything above lands under
    `reports/g1-library-retrieval/rating/<round>/`, and the round is closed by writing its
    entry in `AMENDMENTS.md` if anything deviated.

---

## 2. Rater onboarding

### 2.1 What a rater is being asked

> **One question. One passage. Does the passage answer the question?**

Not "is it about the same topic", not "is it a good passage", not "would I cite it". The
protocol's rubric row for the judge (§4.4, *leniency / topical drift*) applies verbatim to
humans: **the passage must answer the query**, and topical relatedness that does not answer is
grade 0.

| Grade | Name | Definition | Operational test |
|---|---|---|---|
| **0** | irrelevant | The passage does not help answer the question. | Could a reader answer *any part* of what was asked using only this passage? If no → 0. |
| **1** | partially answers | The passage contains part of an answer, or answers one component of a multi-part question, or gives the answer only for a narrower case than was asked. | You would still need another passage to answer properly. |
| **2** | fully answers | A reader could answer the question, as asked, from this passage alone. | Nothing further is needed for the question *as asked*. |

Judge the question **as asked**, not as you would have asked it. A vague question with a broad
answer is a 2 if the passage covers the breadth asked for.

### 2.2 The 1-vs-2 boundary — worked examples

This is where raters diverge, so the rule is stated as a single test and then exercised.

> **Sufficiency, not quality.** A terse passage that fully answers is a **2**. An
> eloquent, well-written passage that answers half the question is a **1**. Length,
> clarity, journal, recency, and whether you *believe* the finding are all irrelevant.

**Example A — the scope trap.**
*Q: "What mechanisms confer cefiderocol resistance in Enterobacterales?"*
*Passage: "In our Klebsiella pneumoniae isolates, cefiderocol MICs rose with NDM-5
carriage combined with cirA disruption."*
→ **1.** It gives a mechanism, but for one species and one isolate set; the question asked
about Enterobacterales generally. Rating this 2 is the single most common over-grade.

**Example B — the terse full answer.**
*Q: "Does cirA disruption raise cefiderocol MICs?"*
*Passage: "cirA-disrupted mutants showed a 16-fold MIC increase (p < 0.001)."*
→ **2.** Two clauses, no discussion, no context — and it completely answers the question as
asked. Under-grading this to 1 because it "feels thin" is the most common under-grade.

**Example C — the well-written near-miss.**
*Q: "How does porin loss change carbapenem susceptibility?"*
*Passage: A paragraph on outer-membrane permeability, porin families, and their regulation,
which never states an effect on susceptibility.*
→ **0**, not 1. It is background for the mechanism, not part of an answer. If a passage
contains no proposition that answers any part of the question, it is 0 however on-topic it is.

**Example D — the multi-part question.**
*Q: "Which carbapenemases are most prevalent in Hungary, and which treatments remain
effective?"*
*Passage: Prevalence table of NDM/OXA-48 by region, no treatment data.*
→ **1.** One of two components answered. Two components, one answered → 1. Both → 2.

**Example E — answer present but contradicted.**
*Q: "Is ceftazidime-avibactam effective against NDM producers?"*
*Passage: "Ceftazidime-avibactam showed no activity against NDM-producing isolates."*
→ **2.** A negative answer is an answer. Relevance is not agreement.

**Example F — the citation stub.**
*Q: "What is the mortality rate of carbapenem-resistant Klebsiella bacteraemia?"*
*Passage: "Mortality in CRKP bacteraemia is high [12,13]."*
→ **1.** It asserts a direction but not the quantity asked for; the number lives in the cited
work, which is not this passage.

**Example G — truncated at the chunk boundary.**
*Q: "What were the MIC50 and MIC90 for cefiderocol?"*
*Passage: "…the MIC50 was 2 mg/L and the MIC90 was"* (chunk ends).
→ **1.** Grade what is in front of you. Chunk boundaries are part of what the experiment
measures; do not mentally complete the sentence.

**Tie-break rule.** If after 60 seconds you are still between 1 and 2, choose **1**. The
graded gain is `2**grade - 1`, so a wrong 2 costs more than a wrong 1, and the rubric's known
failure mode is leniency.

### 2.3 Calibration (mandatory, before any live work)

1. The study lead grades the ~30-pair calibration set (`calibration.jsonl`) themselves and
   writes `calibration_key.jsonl` — `{"pair_id": …, "gold_grade": 0|1|2, "rationale": "…",
   "set_by": "<lead>"}`. **Every** gold grade carries a one-line rationale; a key without
   rationales cannot be used to retrain a failing rater.
2. The lead does this **before** raters see the set, and never edits a gold grade after a
   rater has been scored against it.
3. Each rater opens the tool, loads `calibration.jsonl`, rates it, and sends the export.
4. Score it:
   ```bash
   cd python && /rag/envs/ragstack/bin/python scripts/eval/g1_agreement.py \
       --judgments <round>/exports/calibration/*.jsonl \
       --calibration-key <round>/calibration_key.jsonl \
       --out-dir <round>/analysis/calibration
   ```
5. **Pass:** exact agreement with the key ≥ **0.70** *and* at most **2** qualitative (0-vs-2)
   errors. These are the thresholds `g1_agreement.score_calibration` applies.
6. **Fail:** the lead walks the rater through every mismatched item using the key's rationales
   (30–45 minutes), then the rater takes a **fresh** calibration set. A second failure means
   the rater does not rate in this study — that is not a judgement about the person; it means
   the rubric and that rater's reading of it have not converged, and their labels would
   depress κ for a reason unrelated to the judge.
7. Record every calibration attempt, pass or fail, in the round manifest. A rater who passed
   on the second attempt is flagged in the report, because their live agreement is the number
   most likely to drift.

### 2.4 Before the first live session, the rater must be able to state

- the three grades and the sufficiency test (§2.1);
- that they may **not** discuss items with another rater until both have submitted (§5.3);
- that they must **not** look up the source paper, the retrieval configuration, or anything
  outside the passage on screen (§4.2);
- how to export and where to send the file (§7.1).

### 2.5 What disqualifies a rater or a session

Applied by the study lead from `agreement.md`'s rater-diagnostics table; each is a *session*
void unless stated otherwise.

| Trigger | Consequence |
|---|---|
| Calibration failed twice (§2.3) | Not a rater in this study |
| Median seconds/item < 5 s, or > 25% of items under 5 s | Session void; items reassigned |
| One grade used for ≥ 95% of ≥ 100 items | Session held; lead interviews before accepting |
| A grade never used across a full session of ≥ 100 items | Flagged in the report; not automatically void (a genuinely grade-0-heavy sample exists) |
| κ with **every** other rater < 0.2 while the others agree ≥ 0.5 | Outlier: their labels are set aside and the pairs re-rated |
| Any use of an outside source, or any sight of a configuration/LLM label | Round-level incident: all of that rater's judgments void, recorded as an amendment |
| More than 250 items rated in a calendar day | Everything beyond 250 void (§4.3) |

---

## 3. Query generation and ingestion

Both paths produce the **same schema** and the same manifest fields, so downstream nothing
knows or cares which one a query came from except through its `source` field — which is
exactly the property that lets §9's sensitivity analysis compare them.

### 3.1 LLM-generated (the starting point)

The protocol registers this as threat **T1b**: a query written from the chunk it is meant to
retrieve shares vocabulary with that chunk by construction, which inflates the sparse leg on
precisely the axis RQ1/H1 measures. Two mitigations are mandatory and both are implemented:

- **Paraphrase-first.** Pass 1 summarizes the chunk; pass 2 writes the question from the
  summary, with the verbatim chunk never in its context. Both prompt hashes go in the
  manifest. (`test_generation_never_shows_the_chunk_to_the_query_pass` pins the invariant.)
- **The overlap covariate.** Every query records the IDF-weighted term overlap and the
  Jaccard against its source chunk, with IDF computed over the **largest** rung's chunks so
  the number is comparable across rungs. The manifest carries the distribution and the
  tertile edges.

```bash
cd python && export PYTHONPATH="$PWD"
/rag/envs/ragstack/bin/python scripts/eval/g1_make_queries.py \
    --source llm \
    --chunks     /rag/data/g1-corpus/chunks.p50.jsonl \
    --idf-chunks /rag/data/g1-corpus/chunks.p200.jsonl \
    --corpus-manifest /rag/data/g1-corpus/manifest.json \
    --n 600 --seed 0 --critic \
    --llm-base-url http://localhost:9101 --llm-model <model> \
    --out reports/g1-library-retrieval/fixtures/g1_pilot_p50_queries
```

Automatic discards (protocol §4.2, in the order the funnel applies them): `names_document`,
`too_long`, `too_short`, `not_a_question`, `title_answerable`, `duplicate`,
`critic_rejected`. Counts by reason go in the manifest — a shifting discard histogram between
runs is a prompt regression, and it is visible for free.

**Use a judge model from a different family than the generator, and a third for the answer
generator** (protocol §4.4, *self-preference*). All three are recorded in manifests.

### 3.2 Human-written (documented input format)

A domain expert supplies a spreadsheet export or a JSONL dump. Columns:

| Column | Required | Meaning |
|---|---|---|
| `text` | **yes** | The question, one sentence, standalone |
| `author_id` | yes in practice | Pseudonymous id; enforces §3.4 and appears in the manifest |
| `source_doc_id` | no | PMCID, if the expert had a specific paper in mind |
| `source_chunk_id` | no | Chunk id, if known — enables the overlap covariate |
| `notes` | no | Free text, carried through, never shown to a rater |

`.csv`, `.tsv`, `.jsonl` and `.json` are all accepted. Minimal CSV:

```csv
text,author_id
"Which efflux pumps export tigecycline in Acinetobacter baumannii?",expert-01
"Does cirA disruption raise cefiderocol MICs in Klebsiella pneumoniae?",expert-01
```

```bash
/rag/envs/ragstack/bin/python scripts/eval/g1_make_queries.py \
    --source human --human-input expert_queries.csv \
    --idf-chunks /rag/data/g1-corpus/chunks.p200.jsonl \
    --corpus-manifest /rag/data/g1-corpus/manifest.json \
    --out reports/g1-library-retrieval/fixtures/g1_pilot_expert_queries
```

Brief for the expert:

1. Write questions **you would actually type into a literature search** while working.
2. Do not write from a specific passage in front of you. If you have one in mind, say so in
   `source_chunk_id` — an honest overlap covariate is worth far more than a clean-looking one.
3. Never refer to a document ("this paper", "the authors", "Table 2"): the filter rejects it,
   and it makes the query unanswerable outside its source.
4. One sentence, ending in a question mark. Standalone: a colleague who has read nothing must
   understand it.
5. Both a specific question ("what MIC did X show") and a general one ("what mechanisms
   cause Y") are welcome — the mix is realistic.

A human query with no declared source chunk carries a **null** overlap covariate. That is the
correct value, and those queries are the sub-population T1b's sensitivity analysis wants most:
they are the only ones carrying neither T1's nor T1b's contamination.

### 3.3 Acceptance criteria (both paths)

1. The automatic filters (§3.1) run on both paths.
2. **Two-reviewer plausibility audit.** `g1_make_queries.py` writes `<out>.audit.csv` — a
   seeded sample (default 100) with blank `reviewer_a_plausible` / `reviewer_b_plausible`
   columns. Two reviewers fill it in independently: *is this a plausible question for a
   working researcher?* yes/no.
3. **Accept the fixture** when both reviewers mark ≥ **90%** plausible and the two reviewers
   agree on ≥ **85%** of the sampled items. Otherwise revise the prompt (or brief the expert
   again), regenerate, and re-audit. A prompt change after any query has been used in a run is
   an amendment.
4. Sanity-check the manifest's `idf_overlap` block before accepting. A mean above ≈ 0.5 means
   the paraphrase pass is not doing its job — inspect a handful of `paraphrase` fields and
   fix the prompt rather than shipping a fixture whose bias is known and unbounded.
5. The accepted fixture is copied to `contracts/fixtures/queries/` and its SHA-256 recorded.
   **From this point the query set is frozen for the round.**

### 3.4 Conflict of interest

**Whoever wrote a query does not judge relevance for it.** They know the answer they had in
mind, which is exactly the information a relevance judgment is supposed to be independent of.
`g1_make_pool.py` enforces this mechanically: any pair whose query's `author_id` matches a
rater on the panel is withheld from that rater, and the count appears in the manifest as
`assignment.author_conflicts_withheld`. If the exclusion leaves fewer eligible raters than
`--replication`, the script fails rather than quietly single-rating the pair.

---

## 4. Pooling, assignment and rating sessions

### 4.1 Build the pool and the assignments

```bash
cd python && export PYTHONPATH="$PWD"
/rag/envs/ragstack/bin/python scripts/eval/g1_make_pool.py \
    --rankings reports/g1-library-retrieval/<run>/raw \
    --queries  reports/g1-library-retrieval/fixtures/g1_pilot_p50_queries.jsonl \
    --chunks   /rag/data/g1-corpus/chunks.p200.jsonl \
    --corpus-manifest /rag/data/g1-corpus/manifest.json \
    --llm-labels reports/g1-library-retrieval/qrels/pilot/judgments.jsonl \
    --raters alice,bob --replication 2 --subsample 400 \
    --calibration-n 30 --seed 0 \
    --out-dir reports/g1-library-retrieval/rating/round1
```

What the defaults mean, and when to change them.

- **`--pool-depth 20`** — protocol §4.3, fixed for every cell. **Do not vary it.** A
  depth that moved with a cell's parameters would advantage the configurations that
  contribute more to the pool, and sweeping `rerank_candidates` *is* a pool-depth
  manipulation. The script warns when a cell returned fewer than 20 chunks for some query;
  that is a finding about the cell (H1b), not licence to lower the depth.
- **`--subsample 400`**, range-checked to 300–500. The reason is precision, not taste. The
  manifest's `kappa_precision_forecast` block reports the simulated SD of κ̂ at the realized
  design: at **n = 100** the SD around κ = 0.5 is ≈ **0.08**, i.e. a 95% CI of roughly
  **±0.15**, which spans the protocol's 0.4 gate from 0.35 to 0.65 — the gate cannot decide
  what it exists to decide. At **n = 400** the SD is ≈ **0.04**. Use `--allow-any-size` only
  with a written justification in the round manifest.
- **`--allocation proportional` with `--min-per-stratum 10`** — the subsample is stratified on
  the pair's best rank across cells and, when LLM labels are supplied, on the judge's grade. A
  uniform draw would be overwhelmingly deep-rank grade-0 pairs and κ estimated on a
  near-degenerate marginal is both imprecise and not comparable to anything. Proportional
  allocation keeps κ interpretable as an estimate *for the pool as it will be judged*; the
  floor is what guarantees the rare strata are observed at all. `--allocation balanced`
  exists but changes the estimand and must be justified.
  *Known limitation:* the floor mildly over-samples rare strata, so κ on the subsample is not
  exactly κ on the pool. Report the strata table (`subsample.strata_pool_sizes` vs
  `strata_drawn`) beside κ so the distortion is visible; keep the floor small relative to n.
- **`--replication 2`** — every pair double-rated. **`--overlap-frac 0.2`** — the share of
  pairs every rater on the panel sees. With exactly two raters those coincide. With three or
  more, the overlap set is the only thing that makes a single panel-wide Fleiss' κ exist
  rather than a bag of incomparable pairwise numbers.
- **`--calibration-n 30`** — drawn first, with balanced allocation across strata, and then
  **withheld from the live subsample**: a rater who has seen a pair together with its gold
  grade is not blind to it.

### 4.2 Blinding rules

Non-negotiable; protocol §4.4, *config leakage*.

1. A rater sees **only** the query, the chunk text and the source document title. The
   assignment file may contain only: `pair_id`, `assignment_id`, `rater_id`, `set`,
   `query_id`, `query`, `chunk_text`, `doc_title`. Anything else — a rank, a cell id, an LLM
   grade, even `doc_id` — is a violation. Both `g1_make_pool.py` and the rating tool enforce
   the allowlist; the tool **refuses** a non-conforming file rather than stripping fields,
   because a silent strip lets a broken pipeline keep producing plausible-looking data.
2. Raters do not open `pool.jsonl`, the sweep outputs, the judge's labels, or any analysis
   file. Those live in directories raters are not given.
3. Raters do not look up the source paper, search the web, or ask a colleague *during*
   rating. Grade what is on the screen.
4. Raters do not discuss any item with another rater until both have submitted (§5.3).
5. Presentation order is randomised per rater by the tool with a recorded seed. Raters do not
   set the seed; the lead does, or lets the tool derive it from `rater_id:assignment_id`.

### 4.3 Session conduct and length

Fatigue is a measurement error, not a virtue.

1. **Block = 60 items**, then a break of at least 5 minutes away from the screen.
2. **At most 3 blocks (≈ 180 items) per day**, hard stop at **250**. Anything beyond 250 in a
   calendar day is void (§2.5).
3. Budget **20–40 s** per item for a ~512-token full-text chunk. A block is therefore roughly
   25 minutes.
4. **Self-check between blocks:** if your median time in the last block is under 60% of your
   first block's median, stop for the day. The tool shows a running count; `agreement.md`
   reports the medians per rater afterwards.
5. Export at the end of every block — `e`, or the *Export judgments* button — and keep the
   files. Progress is autosaved to the browser after every item, but a browser is not an
   archive. If the tool shows the storage warning banner, export every 50 items.
6. Do not rate while in a meeting, on a call, or with anything else on screen.

**Keyboard.** `0` `1` `2` grade and advance · `u` / Backspace undo the last judgment and
return to it · `s` / Space skip (the item returns at the end) · `e` export · `?` help.

### 4.4 When you are uncertain

In order:

1. **Re-read the question**, not the passage. Most 1-vs-2 uncertainty is drift in what was
   actually asked.
2. Apply the sufficiency test (§2.2): *as asked*, is another passage needed?
3. If still stuck after ~60 seconds, apply the tie-break: **choose the lower grade**.
4. If the item is broken rather than hard — the passage is a reference list, a table of
   figure captions, mangled OCR, or a different language — grade it **0** and note the
   `pair_id` in your handover message. Broken chunks are a real retrieval outcome and must be
   graded, not skipped; the note is for the corpus, not for the label.
5. `s` (skip) is for "I need a break from this one", not for "I cannot decide". Skipped items
   return at the end of the session and must be graded before the session is complete.
6. Never ask another rater. Ask the study lead, who answers with a rubric clarification only —
   never about the specific item.

---

## 5. Adjudication

### 5.1 What goes to adjudication

`g1_agreement.py` writes `adjudication_queue.jsonl`: every pair whose grades differ by **2**
(one rater said 0, another said 2). A 0-vs-1 or 1-vs-2 split is a *boundary* call and is
resolved automatically by the consensus rule — majority, ties to the **lower** grade — because
the rubric's failure mode is leniency and an upward error inflates every recall-flavoured
metric. A 0-vs-2 split is *qualitative*: one rater saw an answer where the other saw noise.
Averaging that would invent a label neither rater would defend, so it does not resolve on its
own.

### 5.2 Procedure

1. The lead assigns each queued pair to an **adjudicator who did not rate it**. With a
   two-person panel that is the study lead.
2. **Blind third rating first.** The adjudicator rates the pair in the tool from a
   single-pair assignment file, without seeing the two existing grades. If the third rating
   matches one of the two, that grade wins, 2-of-3, and the case is closed. This resolves
   most of the queue without discussion and without anybody defending a position.
3. **Only if the third rating differs from both** (e.g. 0, 2 and 1) does the lead convene a
   5-minute discussion with both original raters, decide, and record a one-line rationale.
4. **Timebox: 5 minutes per pair.** Unresolved pairs are marked `unresolved`, excluded from
   the qrels, and **counted** — the unresolved rate is reported, because a large one says the
   rubric is ambiguous for a whole class of items and that is a finding.
5. **Who decides:** the study lead, named in the round manifest, who must not have rated the
   pair. If the lead rated it, a second lead is named for that pair.
6. Adjudicated grades go to `adjudications.jsonl`
   (`{"pair_id", "resolved_grade", "adjudicator", "rationale"}`) and are passed to
   `g1_agreement.py --adjudications`.

### 5.3 Discussion rules

Discussion happens **after** both raters have submitted, never during, and never about an item
either of them still has open. This is not ceremony: a discussion before submission converts
two independent labels into one correlated label, and κ computed over correlated labels
overstates agreement — which is the direction that flatters the study.

### 5.4 κ is reported before adjudication

The gate (§6) uses **pre-adjudication** κ. Adjudication removes exactly the hardest pairs, so
post-adjudication κ is optimistic by construction. Both are reported; the pre-adjudication
value is the one that decides.

---

## 6. κ bands and their consequences

**This section proposes rules the protocol does not have.** Protocol §4.4 gates at
κ(judge–human) < 0.4 and says nothing about what 0.45 buys versus 0.75. The bands below fill
that gap. They are implemented in `g1_agreement.KAPPA_BANDS` and appear in every
`agreement.json`.

### 6.1 The bands

| κ(judge–human) | Band | What the LLM labels may be used for |
|---|---|---|
| **≥ 0.80** | **STRONG** | Everything SUBSTANTIAL allows. In addition, the judge may substitute for one human rater in later rounds, and the human subsample drops to a ~100-pair **drift audit** per round — at this level the audit only has to detect change, not estimate the level. |
| **0.60 – 0.79** | **SUBSTANTIAL** | The full §7.5 verdict vocabulary — DIFFERENT / EQUIVALENT / INCONCLUSIVE — on the LLM-judged libraries, scoped to 50–200 documents, still requiring the SciFact anchor to agree in sign (§7.2) and the T1b overlap covariate to be reported. |
| **0.40 – 0.59** | **MODERATE** | **Screening only.** Stage-1 nomination and the *sign* of a contrast may use LLM labels. No shippable claim rests on them alone. **EQUIVALENT is downgraded to INCONCLUSIVE** unless the SciFact anchor is independently EQUIVALENT. **DIFFERENT** additionally requires the differential-bias checks (§6.3) to come back flat. |
| **< 0.40** | **FAIL** | Protocol §4.4's gate: the pilot's quality track is **descriptive only**. The recommendation falls back to the SciFact anchor plus Track C; the size question defers entirely to Part II. The human labels are still published, as a finding about the judge. |

### 6.2 Why the bands are not symmetric — the noise argument

The instinct is that noisy labels make every claim harder. They do not; they make *different*
claims harder in different directions, and the MODERATE band's asymmetry follows from that.

- **Non-differential noise attenuates.** Label error that is unrelated to which configuration
  produced a chunk shrinks measured differences toward zero. That does **not** make an
  observed difference suspect — it makes it conservative. It *does* make an observed
  **equivalence** nearly worthless: attenuation manufactures equivalence, so "the 90% CI of
  the difference lies within ±δ" is precisely the verdict noise produces for free. Under
  MODERATE, EQUIVALENT is therefore downgraded to INCONCLUSIVE unless a corpus with real
  qrels — the SciFact anchor — says the same thing.
- **Differential noise fabricates.** The judge's known biases are *not* independent of the
  systems being compared. §4.4 lists verbosity (longer chunks judged more relevant) and
  position; configurations differ in exactly those properties — rerank-on returns a different
  length distribution, depth changes the rank profile. So a judge bias correlated with a
  configuration can create a difference that is not there. That is why DIFFERENT under
  MODERATE requires §6.3's checks.

Above 0.60 both effects are small enough relative to δ that the protocol's normal machinery
is adequate; below 0.40 the protocol has already decided.

### 6.3 The differential-bias checks (required in the MODERATE band)

1. **Chunk length.** Report the length distribution of judged-relevant vs judged-irrelevant
   chunks, and the length distribution of each compared cell's top-k. A DIFFERENT verdict
   whose winning cell also returns systematically longer chunks is reported as
   *confounded with length*, not as a result.
2. **IDF overlap (T1b).** Recompute the contrast on the lowest-overlap tertile. A leg
   conclusion that holds only in the high-overlap tertile is an artefact of query generation
   (protocol §9, T1b(c)).
3. **Judge self-consistency.** The duplicate-pair re-label rate must be ≥ 0.95. Below that,
   the judge is not stable enough for any verdict at any κ.

### 6.4 Rules that apply to all bands

1. **The interval, not the point.** The effective band is the band of the **95% CI lower
   bound** whenever the CI spans a boundary. `banded_verdict` computes this and flags
   `spans_boundary`. A gate decided by a point estimate whose interval crosses it is a coin
   flip with a decimal point.
2. **The human ceiling.** κ(judge–human) cannot meaningfully exceed κ(human–human): the judge
   is being scored against a target the humans themselves dispute. Therefore:
   - κ(human–human) **< 0.40** → verdict **RUBRIC_FAILURE**, regardless of κ(judge–human).
     The rubric or the panel is the finding. Re-calibrate (§2.3) and re-rate; do not
     interpret the judge at all. `g1_agreement.py` emits this automatically.
   - κ(human–human) in **[0.40, 0.60)** → the judge's band is **capped at MODERATE**, however
     high κ(judge–human) comes out.
   - Report `normalized_vs_human_ceiling` = κ(judge–human) / κ(human–human) alongside both.
3. **Unweighted κ decides; linear-weighted κ informs.** The gate is stated against the
   unweighted value, which is the conservative one (it treats a 1-vs-2 boundary call as
   exactly as bad as a 0-vs-2 blowup). The linear-weighted value is reported beside it because
   it is closer to how the labels are used — nDCG's gain is `2**grade - 1`.
4. **Pre-adjudication** (§5.4).
5. **The band is recorded in the deliverable.** The `LibraryRetrievalDefaults` evidence block
   already carries `judge_agreement: "kappa(judge-human)=<float>"` (protocol §11.1); it must
   also carry the band and, if the CI spans a boundary, say so.

### 6.5 Running it

```bash
cd python && export PYTHONPATH="$PWD"
/rag/envs/ragstack/bin/python scripts/eval/g1_agreement.py \
    --judgments   reports/g1-library-retrieval/rating/round1/exports/*.jsonl \
    --llm-labels  reports/g1-library-retrieval/qrels/pilot/judgments.jsonl \
    --adjudications reports/g1-library-retrieval/rating/round1/adjudications.jsonl \
    --out-dir     reports/g1-library-retrieval/rating/round1/analysis
```

Outputs: `agreement.json` (with the full provenance header and the band table),
`agreement.md` (the human-readable report), `adjudication_queue.jsonl`.

---

## 7. Data handling and reproduction

### 7.1 Where things live

```
reports/g1-library-retrieval/
  fixtures/g1_pilot_*_queries.{jsonl,manifest.json,rejected.jsonl,audit.csv}
  qrels/pilot/judgments.jsonl               # the LLM judge's labels
  rating/<round>/
    pool.jsonl                              # every pooled pair + cells + best_rank  [NOT for raters]
    subsample.jsonl                         # the human subsample + strata           [NOT for raters]
    assignments/<rater>.jsonl               # blinded                                [given to raters]
    calibration.jsonl                       # blinded                                [given to raters]
    calibration_key.jsonl                   # gold grades                            [lead only]
    exports/<rater>_<timestamp>.jsonl       # judgments as received
    exports/<rater>_<timestamp>.session.json
    adjudications.jsonl
    manifest.json
    analysis/{agreement.json,agreement.md,adjudication_queue.jsonl}
```

Raters receive **only** `assignments/<their id>.jsonl` and `calibration.jsonl`, and return
their exports. Nothing else is shared with them at any point during the round.

The judgments contain no personal data; `rater_id` is a pseudonym assigned by the lead, and
the mapping to real people is held by the lead and is not committed. Chunk text comes from
open-access, CC-licensed PMC articles (`/rag/data/g1-corpus/manifest.json` records the licence
per document), so the passages themselves are freely redistributable.

### 7.2 Provenance carried by every artefact

Every manifest this apparatus writes carries: `schema_version`, `tool`, `tool_version`,
`generated_utc`, the **SHA-256 of `docs/g1-retrieval-protocol.md`** (`protocol.sha256` — the
protocol's `protocol_version`), git commit/branch/dirty, the Python version, and the verbatim
`argv`. Beyond that:

| Artefact | Also carries |
|---|---|
| Query fixture | generator model, generation mode, paraphrase/query/critic prompt SHA-256s, temperature, seed, accept/discard counts by reason, IDF-overlap distribution + tertile edges, input file hashes |
| Pool / assignment | pool depth, cell list, cells short of depth, strata sizes and draws, allocation mode, all seeds, per-rater file hashes and assignment ids, overlap size, author conflicts withheld, κ precision forecast, the blinding allowlist actually enforced |
| Judgment export | per line: `pair_id`, `grade`, `rater_id`, `timestamp`, `seconds_on_item`, `shuffle_seed`, `assignment_id`, `set`, `presentation_index`, `revised`, `tool_version` |
| Session manifest | shuffle seed **and algorithm**, item counts, grade histogram, median seconds, start/export times, the blinding check that passed, user agent |
| Agreement | every κ with its CI and band, per-rater diagnostics, consensus method counts, the adjudication queue, the band table with consequences, bootstrap iters and seed |

### 7.3 Reproducing a round

A third party with the corpus and the artefacts can reproduce every number:

1. Check out the commit in `manifest.json.git.commit`; confirm `git.dirty` was `false`. If it
   was `true`, the run is not reproducible and must be labelled as such.
2. Verify `protocol.sha256` matches the `docs/g1-retrieval-protocol.md` at that commit. A
   mismatch means the pre-registration changed under the run — check `AMENDMENTS.md`.
3. Verify the input hashes in `inputs` (queries, chunks, LLM labels).
4. Re-run `g1_make_pool.py` with the recorded `argv`. The pool, the subsample, the overlap set
   and the assignments are deterministic in `(inputs, seed)`; the per-rater file SHA-256s must
   match those in the manifest.
5. Re-run `g1_agreement.py` with the recorded `argv` over the archived exports. The bootstrap
   is seeded, so every κ and CI reproduces exactly.
6. Query generation is **not** bit-reproducible — an LLM endpoint is not a pure function even
   at temperature 0. What is reproducible is the *fixture*: the pinned `.jsonl` plus its
   SHA-256 is the artefact everything downstream depends on, and its manifest records the
   model, both prompt hashes and the seed so a regeneration can be compared rather than
   assumed identical.

### 7.4 Retention

Keep the raw exports forever — they are cheap and they are the only record of what a human
actually did. Never edit an export in place; corrections are new files, and an adjudication is
a separate artefact, never an overwrite. If a session is voided (§2.5), move the export to
`exports/void/` with a note; do not delete it, because a voided session is evidence about the
process.

---

## 8. Checklists

**Before a round opens (study lead)**

- [ ] Query fixture accepted (§3.3) and copied to `contracts/fixtures/queries/`
- [ ] Sweep rankings present for every cell that must contribute to the pool
- [ ] LLM labels available (preferred) so the subsample can be stratified on grade
- [ ] `g1_make_pool.py` run; manifest reviewed; κ forecast acceptable
- [ ] Calibration key written, with a rationale per item
- [ ] Roles named; author conflicts (§3.4) reported as 0 or explained
- [ ] Raters onboarded (§2.4) and calibrated (§2.3)

**Each session (rater)**

- [ ] Correct assignment file, correct rater id shown in the header
- [ ] ≤ 60 items per block, break between blocks, ≤ 250 items today
- [ ] Export at the end of every block; both files sent
- [ ] No outside sources, no discussion of open items

**Closing the round (study lead)**

- [ ] Every assigned pair rated by its full replication, or the shortfall recorded
- [ ] Adjudication queue emptied or its unresolved pairs counted (§5.2.4)
- [ ] `g1_agreement.py` run; band and consequence recorded
- [ ] κ(human–human) checked against the ceiling rule (§6.4.2)
- [ ] Deviations written into `AMENDMENTS.md` with a date
- [ ] Artefacts in place per §7.1; `git.dirty == false` for the analysis run
