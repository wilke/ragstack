# PREREG — small-corpus chunk-granularity re-score of the Leg B ×0 rung

**Written before any embedding, scoring or analysis was run.** Frozen at the commit recorded in
§9. Everything below the line "predictions" is a commitment, not a description.

Proposed by `design/ANSWER-completeness-and-subsets.md` §3.4–3.5 ("The small end: 1–100
documents" / "the cheapest experiment that brackets both ends"). It is the highest
value-per-GPU-minute experiment identified anywhere in this study, and it is cheap because the
ground truth already exists and has never been used.

---

## 1. What is new here, in one paragraph

Every retrieval number in phase 0 so far is **document-level**: chunks are scored, the maximum
chunk score is rolled up to its document, and the metric asks *did the gold document come back*.
That metric structurally cannot distinguish **"found the right paper"** from **"found the
answering passage."** Leg B's construction records, per query, the **deep section the query was
written from** (`legb2_sections.json`: `sec_start_char`, `sec_end_char`, offsets into exactly the
text stage 1 indexed). That is a free **passage-level qrel**. This run scores the ×0 rung at
**chunk** granularity against it, over **mini-corpora of 1, 10 and 100 documents** drawn from the
same 400 sources — because 1–100 documents is the user's stated end goal and no measurement in
this study speaks to it.

---

## 2. Questions, in quantitative form

| # | question | the number that answers it | decision |
|---|---|---|---|
| **R1** | **How large is the passage-vs-document gap?** | `Gap@k = DH@k − PH@k`, paired per query, at N = 1, 10, 100 and k = 1, 5, 10. | If `Gap@10 ≥ X_P` at any N, document-level metrics are established as an over-statement of retrieval quality by a quantified amount, and stage 2 must report a passage metric beside them. |
| **R2** | **Does chunk size matter at 1 / 10 / 100 documents?** | Spread (max−min) across the four 0 %-overlap sizes of the **primary budget-matched passage metric** `R_B@4096`, and of `PH@1`, at each N. Read against bar `X_P` with its own δ80. | If the spread resolves above `X_P`, size matters at the small end and the "small corpora are easy, do not tune" position is wrong. |
| **R3** | **Does size matter *more* or *less* at the small end than at ×0 (400 docs) and ×11.5 (5 000 docs)?** | The same four-cell spread on **document-level nDCG@10**, dense, compared with the already-measured values: **×0 = 0.0408**, **×11.5 = 0.0732** (see §5 table). Direction only; the passage-level spread has no measured comparator and is reported as new. | Establishes whether the corpus-size scaling law found at ×0/×11.5 extrapolates downward, or whether the small end is a different regime. |
| **R4** | **Is retrieval trivial at 1 / 10 / 100 documents?** | The pre-registered `TRIVIAL` predicate of §6, evaluated **separately for the document-level family and the passage-level family** at each N. | If document-level is TRIVIAL and passage-level is not, the small end is a *passage-selection* problem and every document-level result in the study is silent about it — which is §3.4's claim, converted from an argument into a measurement. |
| **R5** | **Is "don't retrieve, stuff the whole corpus in the window" a live alternative at these sizes?** | The distribution of **total corpus tokens** at N = 1, 10, 100, and the fraction of mini-corpora fitting a 131 072-token context window. | Purely descriptive; it bounds the regime in which any of R1–R4 matters at all. |
| **R6** (secondary) | **Does overlap buy anything at the passage level?** — the one thing the overlap null never tested. | `best-single-chunk coverage` = max over chunks of \|chunk ∩ gold\| / \|gold\|, and the **straddle rate** (no single chunk fully contains the gold section), contrasted 12.5 % − 0 % and 25 % − 0 % at each size. | Reported descriptively. The overlap **drop** decision is already made on document-retrieval evidence; this run can only add a passage-level caveat, not reverse it. |

---

## 3. Design

### 3.1 Arms

**Twelve `token_window` cells** — sizes 256 / 512 / 1024 / 2048 × overlap 0 % / 12.5 % / 25 %,
imported from `chunking_compare_7way.STAGE1_CONFIGS`, never re-declared. The four **0 %** cells
are the pre-registered primary set for R2/R3; the eight overlap cells exist for R6.

Plus one baseline arm, **`whole4096`**: one vector per document over the document's **first 4 080
tokens** (`HARD_CAP_TOKENS`, the SFR 4 096 window minus a safety margin). This is the
"do not chunk" control the design doc asked for.

**`sentence`, `words` and `semantic` are excluded.** Leg B already found their differences from
`fixed512` **unresolved** at the document level (RESULTS-stage1-legB §, contrasts 7/8/9), the
semantic arm costs a breakpoint embedding pass ~30× the token_window cells' cost, and the
question here is about *granularity*, which the `token_window` size axis isolates cleanly. Their
absence is a scope choice, declared in advance, not a result.

### 3.2 Mini-corpora

For each of the **260 accepted** queries and each N ∈ {1, 10, 100}, a corpus of the gold document
plus N−1 neighbours drawn from the other 399 Leg B sources, under **two policies**:

* **`topical`** (the realistic case): the N−1 nearest neighbours of the **gold document** by
  cosine over the `whole4096` document vectors. "My hundred papers on one subject." The corpus is
  chosen **independently of the query** — deliberately: a query-selected corpus would condition
  the distractor set on the very vector being evaluated and would make the `whole4096` arm
  adversarial by construction.
* **`random`** (the easy bound): a seeded uniform draw from the other 399, `seed = 20260905`.

Both are **nested**: the N=10 set is the first 9 neighbours of the N=100 set's 99, so the size
comparison is within-corpus and monotone. All 260 × 3 × 2 subsets are dumped as a JSON artifact
with a SHA.

`topical` is the pre-registered primary; `random` is reported beside it as the lower bound on
difficulty.

### 3.3 Retrieval

Dense, exact brute-force cosine over in-memory embeddings, restricted to the chunks whose
document is in the mini-corpus. **No reranking anywhere in this run** — the cross-encoder is out
of scope, and every cross-rung comparison in §5 is dense-to-dense. **Zero store writes**: no
Qdrant/Elasticsearch client is constructed in this harness or anything it imports.

### 3.4 Metrics

Let `G = [sec_start_char, sec_end_char)` be the gold section's character span in the document
text, and let each chunk carry its exact `[start_char, end_char)` (the `FixedTokenWindowChunker`
emits these; they are verified against the document text in the harness).

**Primary, budget-matched (this is the size-fair comparison):**

* **`R_B@4096`** — walk the chunk ranking in order, admitting a chunk while cumulative **tokens**
  ≤ 4 096; report `|⋃chunks ∩ G| / |G|`, character-level. `B = 1024` as secondary.
* **`H_B@4096`** — binary: does the admitted set overlap `G` by ≥ 50 characters.

  *Rationale, pre-declared:* top-10 chunks of `tok2048` is ≈20 000 tokens of context and top-10 of
  `tok256` is ≈2 500. **Any `@k` comparison across chunk sizes is confounded by that 8× mass
  difference.** A fixed token budget is the only cross-size-comparable reading, so it is the
  primary and the `@k` family is secondary.

**Secondary, rank-based (`k = 1, 5, 10`), kept for continuity with the design doc's proposal:**

* **`PH@k`** — passage hit: ≥ 1 of the top-k chunks overlaps `G` by ≥ 50 characters.
* **`P@k`**, **`R@k`**, **`F1@k`** — character-level micro precision / recall / F1 of the union of
  the top-k chunks against `G`.
* **`DH@k`** — document hit: the gold document is in the top-k **documents** by max-rollup, i.e.
  exactly the metric family the whole study has used so far. Plus `nDCG@10` and `MRR@10` by the
  same rollup, from `legb_common.per_query_metrics`, unchanged.
* **`Gap@k = DH@k − PH@k`**, paired per query. **This contrast is the point of the run.**

**R6 only:** `cov1` = max over all chunks in the gold document of `|chunk ∩ G| / |G|`;
`straddle` = 1 if no single chunk fully contains `G`. Both are corpus-size-independent.

### 3.5 Unit of analysis and subsets

The **query**, n = **260 accepted** (primary), the same set Leg B pre-registered. The n = 396
robustness set is reported as a sensitivity check only. Contrasts are **paired per query**.

---

## 4. Constructional degeneracies, declared before the run

These are arithmetic, not measurements. Any of them appearing in a results table is labelled
**DEGENERATE** and is never read as a finding.

1. **Every document-level metric at N = 1 is exactly 1.0.** One document in the corpus, one
   relevant document, so `DH@k = nDCG@10 = MRR@10 = 1.0` for every config, every policy. This is
   the sharpest possible statement of §3.4's claim and it needs no fleet time.
2. **`PH@k` at N = 1 is 1.0 whenever `k ≥ chunks/doc`.** Realised ×0 chunks/doc: `tok2048` 5.21,
   `tok1024` 9.93, `tok512` 19.37, `tok256` 38.32. So at N = 1, `PH@5` and `PH@10` are degenerate
   for `tok2048`, and `PH@10` is degenerate for `tok1024`. **Only `k = 1` is a real reading at
   N = 1**, and the budget-matched `R_B@4096` (which admits ≈2 chunks of `tok2048` but ≈16 of
   `tok256`) is the honest one.
3. **`whole4096` cannot contain most gold sections.** Median gold-section relative start is
   **0.693** of a median **8 778**-token document ≈ token 6 100, past the 4 080-token cap. Its
   passage-level recall is therefore largely arithmetic and is reported as a bound, not a
   comparison. Its document-level reading is a genuine baseline.
4. **A proportion at exactly 0 or 1 has a Wald SE of zero and a falsely "resolvable" δ80.**
   Carried over verbatim from `PREREG-stage1-legB.md` §4.2: any such proportion is read on its
   **Wilson** interval, and **no δ80 is quoted from a Wald SE**. The row is written
   **unresolvable by construction**.
5. **`recall@100` and any document-level recall at these N are meaningless** — at N = 100, top-10
   is 10 % of the corpus and top-100 is the entire corpus. Not reported for inference.

---

## 5. The power floor of every threshold this run intends to read

`δ80 = (z_{0.025} + z_{0.20}) · σ_d / √n = 2.802 · σ_d / √260 = **0.1738 · σ_d**`, the identical
convention to `PREREG-stage1-legB.md` §4.

**Two rules, pre-committed, both carried over verbatim:**

1. **δ80 is computed from this run's own paired differences, before the threshold is read.**
2. **A row whose δ80 exceeds the distance it must travel is written UNRESOLVED, never "null."**

**No pilot exists at chunk granularity**, so — unlike Leg B — there are no projected σ_d values to
tabulate. What *can* be pre-computed is the **resolvability bound**, and it is stated here so no
reading can be rescued after the fact:

> A contrast is resolvable at bar `b` only if `σ_d ≤ b·√260 / 2.802 = 5.755·b`.
> For a **paired binary** metric, `σ_d ≈ √d` where `d` is the **discordance rate** (fraction of
> queries where the two configs disagree). Hence:
>
> **resolvable at bar `b` ⟺ discordance `d ≤ 33.12·b²`.**

| bar | max discordance that still resolves | in queries out of 260 |
|---|---:|---:|
| `X_P = 0.05` (passage-level family) | **8.28 %** | 21.5 |
| `X_B = 0.010` (document-level nDCG@10, Leg B's bar) | **0.33 %** | 0.9 |

**This is the single most important line in this pre-registration.** At the document-level bar of
0.010, a paired binary contrast on 260 queries resolves **only if fewer than one query
disagrees** — i.e. `X_B` is unreachable for essentially any binary document-level contrast at
these corpus sizes. That is declared **now**, in advance. Document-level binary rows will
therefore mostly be written UNRESOLVED, and the ones that are not will be the DEGENERATE 1.0 rows
of §4. Neither is a null.

**Bars, chosen before the data:**

* **`X_P = 0.05`** for every passage-level metric (`R_B`, `H_B`, `PH@k`, `F1@k`, `Gap@k`).
  Justification, pre-data: passage metrics have full 0–1 headroom (unlike the document-level
  metrics, which sit at 0.94–0.98 at ×0), and 5 points of hit-rate is the smallest difference a
  practitioner would re-chunk a corpus for. `X_P` is **not** derived from this run's spread.
* **`X_B = 0.010`** for document-level `nDCG@10`, so R3's cross-rung comparison uses the same bar
  as the values it is compared against. Results are **also** reported against 0.010 for the
  passage family as a sensitivity column, labelled as such.

**A contrast is `resolved` iff** `|mean| ≥ bar` **and** the 95 % CI excludes 0 **and**
`δ80 ≤ |mean|`. Holm correction is applied across the R2 size family (6 pairwise size contrasts ×
3 corpus sizes) and reported; it is not applied across R1, which is a single pre-specified
contrast per (N, k) cell.

**Comparators for R3, quoted from `report-legB.json` (dense, n = 260, 0 %-overlap cells only):**

| rung | tok256 | tok512 | tok1024 | tok2048 | **spread** |
|---|---:|---:|---:|---:|---:|
| ×0 (400 docs) `nDCG@10` | 0.9821 | 0.9676 | 0.9482 | 0.9413 | **0.0408** |
| ×11.5 (5 000 docs) `nDCG@10` | 0.9595 | 0.9291 | 0.9094 | 0.8863 | **0.0732** |

These are **re-measured, not re-quoted**, for the N = 400 case where this harness can reproduce
them: the harness runs an N = 400 (= whole ×0 corpus) row as a **reproduction gate** and must
recover the four values above to within ±0.005, or the run is declared broken and reported as
such.

---

## 6. The `TRIVIAL` predicate — stated before it is measured

For a metric family **F** at corpus size **N**, `TRIVIAL(F, N)` holds iff **both**:

* **T1 — ceiling.** The *worst* of the four 0 %-overlap configs scores ≥ **0.95**, with a
  **Wilson 95 % lower bound ≥ 0.90** for proportions. (Not "the best config is good": triviality
  means the choice cannot hurt you.)
* **T2 — indistinguishability.** The max−min spread across the four configs is **< the family's
  bar** (`X_B` for document-level, `X_P` for passage-level) **and** that reading is powered
  (`δ80` of the extremes contrast `≤ bar`). If it is not powered, `TRIVIAL` is
  **UNRESOLVED** — never asserted.

`TRIVIAL(document, 1)` is **DEGENERATE-TRUE** by §4.1 and is not evidence.

The plain-language claim being tested — *"retrieval is near-trivial at 1–100 documents"* — is
`TRIVIAL(passage, N)`. It is the passage family, not the document family, because the document
family is degenerate at exactly the sizes in question.

---

## 7. Predictions, to be scored

Recorded so the run can falsify them. Scored automatically in the results; a wrong prediction is
reported, not quietly dropped.

| # | prediction |
|---|---|
| **P1** | `Gap@10 ≥ 0.15` at N = 100 under `topical` for at least three of the four 0 % configs — i.e. the document-level metric materially over-states retrieval quality. |
| **P2** | `Gap@k` **grows with chunk size**: largest for `tok2048`, smallest for `tok256`, at every N. (A big chunk finds the right paper as easily and points at the right passage less precisely.) |
| **P3** | `TRIVIAL(document, N)` holds for N = 1 (degenerately) and N = 10, and is at least T1-true at N = 100. |
| **P4** | `TRIVIAL(passage, N)` **fails T1 at every N**, including N = 1 — the passage problem does not become easy just because the corpus is one document. |
| **P5** | On `R_B@4096`, smaller chunks win at every N, and the four-cell spread **exceeds** `X_P = 0.05`, i.e. size matters *more* at the passage level than the 0.0408 / 0.0732 document-level spreads at ×0 / ×11.5. |
| **P6** | `topical` minus `random` on `DH@10` at N = 100 is ≤ 0.05 — document retrieval stays easy even against topically-matched distractors. |
| **P7** | Median total tokens at N = 100 is **> 500 000**, so the 131 072-token "just stuff it" alternative does **not** apply at N = 100; at N = 10 the median is **< 131 072** and it does. |

---

## 8. Cost, and the stop rule

Projected from `estats_x0_fixed_tok*_ov0pct.json` (measured, 5 Sept): each ×0 `token_window` cell
is **≈3.87 M tokens, 18–21 s** of fleet at 185–210 k tok/s.

| item | tokens | projected fleet time |
|---|---:|---:|
| 12 `token_window` cells | ≈49 M | ≈4.5 min |
| `whole4096` doc vectors (400 × ≤4 080) | ≈1.6 M | ≈0.2 min |
| query vectors | cached (`queries.npy`) | 0 |
| **total** | **≈51 M** | **≈4.7 GPU-minutes** |

Ceiling: **45 GPU-minutes**. Projection is **10.5 % of it**. Scoring is CPU-only (a 260 × 3 × 2 ×
13 re-score of an in-memory matrix) and consumes no fleet time.

**Stop rule:** if measured fleet time passes **20 minutes** with cells outstanding, stop and
report the partial grid. The per-cell embedding artifact is the checkpoint.

**Fleet politeness, unchanged:** `stage1_common.Fleet`, ≤ 2 in flight per endpoint on
`:9001–:9006` only. **GPUs 6 and 7 are reserved: no endpoint is started by this harness and none
is contacted outside :9001–:9006.**

---

## 9. Provenance to capture, as a hard gate

Written to `provenance-rescore.json` before the first embedding call; the harness **aborts** if
any check fails.

1. `git rev-parse HEAD` of `/home/wilke/Development/ragstack` — must be **`d225cea`**.
2. `stage1_common.pin_repo()` executed, and `ragstack.__file__` asserted to start with
   `/home/wilke/Development/ragstack/python`. **The `/rag/envs/ragstack` environment carries an
   editable-install meta-path finder pointing at a different commit at `/rag/repos/ragstack`; a
   meta-path finder runs before `sys.path`, so `PYTHONPATH` does not win.** The resolved path is
   recorded so the reader can see which code actually ran.
3. The **served** model id, from a live `GET /v1/models` on each of the six endpoints, recorded
   verbatim — not the id written in the repo, which is wrong in several places.
4. `sha256` of the sorted 400-file corpus list, **before** embedding.
5. Seeds: `20260905` (random neighbours); the Leg B corpus/query seed is inherited and unchanged.
6. Python and package versions (`numpy`, `transformers`, `httpx`, `tokenizers`).
7. **Offset gate:** for **all 400** documents, assert
   `doc_text[sec_start_char:sec_end_char] == sec_text` and that `pilot_common.units_for_article`'s
   reconstructed text is byte-identical to `stage1_common.doc_text`. Verified on 60 documents
   during design (60/60 identical); the harness re-runs it on all 400 and **aborts on any
   mismatch**, because every passage-level number depends on it.
8. **Chunk-offset gate:** for every chunk, assert `doc_text[start_char:end_char] == chunk.content`.
   Aborts on mismatch.
9. `nvidia-smi` before and after, showing GPUs 6/7 untouched.
10. **Store gate:** `:6333` and `:9200` are never contacted. The harness constructs no store
    client; grep of the harness's imports is recorded.

---

## 10. What this run cannot answer

Stated in advance so a null here is not over-read.

* **Nothing about reranking.** Dense only (§3.3).
* **Nothing about answer quality.** A chunk overlapping the gold section is not a chunk that
  answers the question; the gold section is the section the query was *written from*, which is a
  necessary but not sufficient condition. This is a **retrieval** measurement with a
  **passage-level** qrel, not an end-to-end evaluation.
* **Nothing about corpora that are not full-text open-access biomedical articles.** N = 100 of
  these is ≈1 M tokens; N = 100 support tickets is not.
* **Nothing at N between 100 and 400** beyond the reproduction-gate row, and nothing above 400 —
  ×11.5 is quoted from Leg B, not re-measured.
* **The overlap decision is not reopened.** §2 R6 can add a passage-level caveat to the
  already-made drop; it cannot reverse it, because it is one dense arm on one leg.

---

## Addendum A1 — the budget-matched admission rule, made explicit

*Added while the embedding pass was still running and **before any scoring code had been
executed**; no metric had been computed when this was written.*

§3.4 says the budget-matched metric admits chunks "while cumulative **tokens** ≤ B". Taken
literally that rule admits **nothing** for `tok2048` at `B = 1024` (its first chunk is already
2 048 tokens), which would report `R_B@1024 = 0` for that arm as an artifact of the rule rather
than of retrieval.

**Pre-committed clarification:** the **top-ranked chunk is always admitted**, even if it alone
exceeds `B`; subsequent chunks are admitted only while the cumulative total stays ≤ `B`. The
**realised** token total (`ntok_B`) is reported beside every `R_B` so the overshoot is visible
and no arm can gain a hidden context advantage unnoticed.

`B = 4096` is unaffected in either direction (2 × 2048 = 4096 exactly). `B = 1024` is therefore
reported with the caveat that `tok2048` receives 2 048 tokens of context against everyone else's
1 024, and **`R_B@4096` remains the primary**.
