# RESULTS — small-corpus chunk-granularity re-score of the Leg B ×0 rung

Pre-registered in [`PREREG-rescore.md`](PREREG-rescore.md) (written and frozen before the first
embedding call; Addendum A1 added while the embedding pass ran and before any scoring code had
been executed). Proposed by `design/ANSWER-completeness-and-subsets.md` §3.4–3.5.

**Cost: 5.22 GPU-minutes** against a 45-minute ceiling — 11.6 % of budget. 54.9 M tokens, 8 602
requests, **0 retries**. Scoring is CPU-only (19 s).

---

## 0. Headline

> **The study has been measuring the wrong thing for the small end.** At **one document** — where
> every document-level metric is exactly **1.000 by arithmetic** — the top-ranked chunk lands in
> the section the query was written from only **55–65 %** of the time. The gap between "found the
> right paper" and "found the answering passage" is **+0.28 to +0.45** at k = 1, **RESOLVED at
> every corpus size and every chunk size**, and it does not shrink as the corpus shrinks.
>
> **Chunk size stops mattering at the document level as the corpus shrinks** (four-cell nDCG@10
> spread: 0.0732 at 5 000 docs → 0.0403 at 100 → 0.0255 at 10 → **exactly 0.0000** at 1) **and
> goes on mattering, undiminished, at the passage level** (budget-matched hit-rate spread 0.181 /
> 0.181 / 0.173 / **0.154** over the same sizes). At N = 100 the passage-level size effect is
> **4.5× the document-level one**.
>
> **So: retrieval at 1–100 documents is trivial — but only the half of it that has been measured.**
> `TRIVIAL(document)` holds at N = 1 (degenerately) and N = 10. `TRIVIAL(passage)` **fails at
> every N, including N = 1.** The small end is a passage-selection problem, and the existing
> phase-0 grid is silent about it.
>
> A clean negative rides alongside: **the "just stuff the corpus in the window" alternative is
> live at N ≈ 10 and dead at N = 100** — 94.6 % of 10-document corpora fit a 131 k window; **0 %**
> of 100-document ones do (median **1.03 M tokens**). The brief's premise that 100 documents fit
> is wrong for full-text articles by ~8×.

---

## 1. Provenance, and what code actually ran

| item | value |
|---|---|
| repo commit | **`d225cea06b11278e0c1ff77c514239233a56aa35`** (asserted; harness aborts otherwise) |
| `ragstack` resolved to | **`/home/wilke/Development/ragstack/python/ragstack/__init__.py`** |
| editable-finder defence | `pin_repo()` strips the meta-path finder pointing at `/rag/repos/ragstack`, re-applied in every `multiprocessing` worker. Surviving `sys.meta_path`: `_distutils_hack.DistutilsMetaFinder` + the four builtin importers. **The `/rag/repos` checkout did not win.** |
| **served** model (live `GET /v1/models`, all six endpoints) | **`Salesforce/SFR-Embedding-Mistral`**, `max_model_len` **4096**, `owned_by` `vllm`. Identical on `:9001`–`:9006` (harness refuses to run if they differ). Matches the repo's recorded id in this case. |
| reranker | **not used** — `reranker_used: false`; `:50052` is imported as a constant by `stage1_common` and never called |
| corpus | 400 Leg B ×0 sources, sorted; `sha256` = `d83c7fe1399b92ab394bd914f22caac689e85913bd73bbd2e9713973a6e56b34` (computed **before** embedding) |
| seeds | `20260905` (random neighbours); Leg B's corpus/query seed inherited unchanged |
| versions | Python 3.12.13, numpy 2.5.0, transformers 5.12.1, tokenizers 0.22.2, httpx 0.28.1 |
| **offset gate** | **400/400** documents: `pilot_common.units_for_article`'s text is **byte-identical** to `stage1_common.doc_text`; **396/396** gold sections slice exactly (`doc_text[start:end] == sec_text`). Zero failures. |
| **chunk-offset gate** | every emitted chunk asserted `doc_text[start:end] == chunk.content`. Zero failures. |
| stores | **never contacted.** No Qdrant/Elasticsearch client is constructed in the harness or its imports; grep of `rescore_*.py` for `qdrant\|elasticsearch\|6333\|9200\|24041\|24043` returns only the docstring stating so. `:24041`/`:24043` were not touched at all. |
| GPUs | 6 and 7 at **0 MiB before and after**. No endpoint started. Fleet politeness unchanged: ≤ 2 in flight per endpoint on `:9001`–`:9006`. |

### 1.1 Reproduction gate — the harness is an independent re-implementation and it lands on Leg B exactly

Scoring the N = 400 row (the whole ×0 corpus) through the new chunk-level path must recover Leg B's
published dense `nDCG@10`:

| config | this run | Leg B ×0 | \|diff\| |
|---|---:|---:|---:|
| `fixed_tok256_ov0pct` | 0.98214 | 0.9821 | 0.00004 |
| `fixed_tok512_ov0pct` | 0.96761 | 0.9676 | 0.00001 |
| `fixed_tok1024_ov0pct` | 0.94824 | 0.9482 | 0.00004 |
| `fixed_tok2048_ov0pct` | 0.94132 | 0.9413 | 0.00002 |

**All four pass** (tolerance ±0.005, achieved ≤ 0.00005). A separate chunking pass, a separate
embedding pass and a separate scoring path reproduce the earlier numbers to four decimals.

---

## 2. What the judged set is, and why it is a real passage task

Every accepted query (n = 260, one per document) was written from a **deep** section:

| property | value |
|---|---|
| gold sections that are the abstract | **0 of 260** (`abstract_answerable` is `False` for all) |
| section class | `other` 101, `discussion` 79, `methods` 48, `results` 31, `intro` 1 |
| median gold-section length | **1 233 tokens** (p10 338, p90 2 005) |
| median position in document | **69.3 %** of the way through |
| median document length | **8 778 tokens** (mean 9 933, max 43 587) |
| gold section as fraction of document | median **13.5 %** |

So the answering passage is a median 13.5 % of a ~9 k-token article, two-thirds of the way in, and
never the abstract. Finding the document is not finding the answer.

---

## 3. R1 — the passage-vs-document gap. **This is the point of the run.**

`Gap@k = DH@k − PH@k`, paired per query, `topical` neighbours, n = 260.
`DH@k` is the study's existing metric (gold document in the top-k documents by max-rollup);
`PH@k` asks whether any of the top-k **chunks** overlaps the gold section by ≥ 50 characters.

### 3.1 At k = 1 the gap is enormous and resolves everywhere

| N | config | `DH@1` | `PH@1` | **Gap@1** | 95 % CI | δ80 | verdict |
|---:|---|---:|---:|---:|---|---:|---|
| **1** | tok256 | **1.0000** | 0.5692 | **+0.4308** | [+0.371, +0.491] | 0.086 | **RESOLVED** |
| 1 | tok512 | **1.0000** | 0.6308 | **+0.3692** | [+0.311, +0.428] | 0.084 | **RESOLVED** |
| 1 | tok1024 | **1.0000** | 0.5500 | **+0.4500** | [+0.389, +0.511] | 0.087 | **RESOLVED** |
| 1 | tok2048 | **1.0000** | 0.6462 | **+0.3538** | [+0.296, +0.412] | 0.083 | **RESOLVED** |
| 1 | `whole4096` | **1.0000** | 0.4308 | **+0.5692** | [+0.509, +0.630] | 0.086 | **RESOLVED** |
| **10** | tok256 | 0.9846 | 0.5731 | **+0.4115** | [+0.350, +0.473] | 0.088 | **RESOLVED** |
| 10 | tok512 | 0.9692 | 0.6192 | **+0.3500** | [+0.291, +0.409] | 0.084 | **RESOLVED** |
| 10 | tok1024 | 0.9462 | 0.5308 | **+0.4154** | [+0.354, +0.477] | 0.089 | **RESOLVED** |
| 10 | tok2048 | 0.9308 | 0.6346 | **+0.2962** | [+0.235, +0.358] | 0.088 | **RESOLVED** |
| **100** | tok256 | 0.9769 | 0.5615 | **+0.4154** | [+0.354, +0.476] | 0.087 | **RESOLVED** |
| 100 | tok512 | 0.9462 | 0.6077 | **+0.3385** | [+0.279, +0.398] | 0.085 | **RESOLVED** |
| 100 | tok1024 | 0.9308 | 0.5269 | **+0.4038** | [+0.340, +0.467] | 0.091 | **RESOLVED** |
| 100 | tok2048 | 0.9154 | 0.6308 | **+0.2846** | [+0.223, +0.346] | 0.088 | **RESOLVED** |

**15 of 15 readings RESOLVED** — `|mean| ≥ X_P = 0.05`, CI excludes 0, `δ80 ≤ |mean|` — at a power
floor of ≈0.087 that every effect clears by 3–5×. Note the N = 1 row in particular: the
document-level column is **1.000 by arithmetic**, and the passage-level column says the top chunk
is wrong 35–45 % of the time on the *same query, same document, same embedding*.

### 3.2 At k = 10 the gap vanishes — and two pre-registered predictions fail

| N = 100, topical | tok256 | tok512 | tok1024 | tok2048 |
|---|---:|---:|---:|---:|
| `DH@10` | 0.9962 | 0.9923 | 0.9731 | 0.9769 |
| `PH@10` | 0.9769 | 0.9692 | 0.9846 | 0.9923 |
| **`Gap@10`** | +0.0192 | +0.0231 | −0.0115 | −0.0154 |

**P1 FAILED** (predicted `Gap@10 ≥ 0.15` for ≥ 3 of 4; observed |Gap@10| ≤ 0.023 and two are
negative). **P2 FAILED** (predicted `Gap@10` grows with chunk size; it *shrinks* and inverts).

Reported as failures, not quietly dropped. The mechanism is clear in hindsight and is itself the
finding: with **ten** chunks in hand you nearly always touch the gold section *somewhere*
(`PH@10 ≈ 0.97–0.99`), so the gap is **not** a recall phenomenon — **it is entirely a top-1 /
precision phenomenon.** The pre-registration aimed the prediction at the wrong k. The measurement
it aimed at is unaffected: k = 1 was pre-registered in the same family and resolves decisively.

**What this means practically:** if the consumer is an LLM that will read ten passages, the
document-level metric is not badly misleading. If the consumer is a person, a citation, a snippet,
or an agent that acts on the first hit, **the document-level metric overstates quality by
roughly 30–45 points.**

---

## 4. R2 / R3 — does chunk size matter at 1 / 10 / 100 documents, and more or less than before?

### 4.1 Document level: the size effect shrinks with the corpus and reaches exactly zero at N = 1

Four-cell (256/512/1024/2048, all 0 % overlap) dense `nDCG@10`, `topical`:

| corpus | tok256 | tok512 | tok1024 | tok2048 | **spread** | extremes contrast (256−2048) |
|---|---:|---:|---:|---:|---:|---|
| **N = 1** | 1.0000 | 1.0000 | 1.0000 | 1.0000 | **0.0000** | **DEGENERATE** (all paired diffs exactly 0) |
| **N = 10** | 0.9932 | 0.9861 | 0.9746 | 0.9677 | **0.0255** | +0.0255, CI [+0.012, +0.039], δ80 0.0199, Holm p 0.0020 → **RESOLVED** |
| **N = 100** | 0.9871 | 0.9701 | 0.9539 | 0.9468 | **0.0403** | +0.0403, CI [+0.020, +0.061], δ80 0.0295, Holm p 0.00078 → **RESOLVED** |
| N = 400 (×0, gate) | 0.9821 | 0.9676 | 0.9482 | 0.9413 | 0.0408 | +0.0408, δ80 0.0315, Holm p 0.0017 → **RESOLVED** |
| N = 5 000 (×11.5, Leg B) | 0.9595 | 0.9291 | 0.9094 | 0.8863 | 0.0732 | (RESOLVED in Leg B) |

**Monotone in corpus size, and it hits the floor.** 0.0732 → 0.0408 → 0.0403 → 0.0255 → **0.0000**.
The document-level size effect is a function of how many wrong documents there are to beat; at one
document there are none, and the effect is not small — it is *identically zero, for every query*.

### 4.2 Passage level: the size effect is essentially **constant** in corpus size

`H_B@4096` — budget-matched passage hit: walk the chunk ranking admitting chunks while cumulative
tokens ≤ 4 096 (top chunk always admitted, Addendum A1), then ask whether that set overlaps the
gold section. **This is the only cross-size-fair reading**: realised budgets are 4 054 / 4 023 /
3 904 / 3 696 tokens for 256 / 512 / 1024 / 2048, i.e. matched to within 9 %.

| corpus | tok256 | tok512 | tok1024 | tok2048 | **spread** | extremes contrast |
|---|---:|---:|---:|---:|---:|---|
| **N = 1** | 0.9962 | 0.9846 | 0.9346 | 0.8423 | **0.1538** | +0.1538, CI [+0.110, +0.198], δ80 0.063, Holm p 4.1e−11 → **RESOLVED** |
| **N = 10** | 0.9962 | 0.9731 | 0.9269 | 0.8231 | **0.1731** | +0.1731, CI [+0.127, +0.219], δ80 0.066, Holm p 1.1e−12 → **RESOLVED** |
| **N = 100** | 0.9962 | 0.9615 | 0.9154 | 0.8154 | **0.1808** | +0.1808, CI [+0.134, +0.228], δ80 0.067, Holm p 2.4e−13 → **RESOLVED** |
| N = 400 | 0.9962 | 0.9577 | 0.9115 | 0.8154 | 0.1808 | +0.1808, δ80 0.067 → **RESOLVED** |

`F1@10` (character-level F1 of the top-10 chunk union against the gold section) tells the same
story with a different instrument: spread **0.0874 / 0.1301 / 0.1247 / 0.1235** at N = 1/10/100/400,
**RESOLVED at every N** (Holm p ≤ 4e−18).

### 4.3 The answer to R3, stated plainly

| | N = 1 | N = 10 | N = 100 | N = 400 | N = 5 000 |
|---|---:|---:|---:|---:|---:|
| document-level spread (`nDCG@10`) | **0.0000** | 0.0255 | 0.0403 | 0.0408 | 0.0732 |
| passage-level spread (`H_B@4096`) | **0.1538** | 0.1731 | 0.1808 | 0.1808 | *not measured* |
| **ratio passage / document** | **∞** | 6.8× | **4.5×** | 4.4× | — |

**Chunk size matters *less* at the small end than at ×0 and ×11.5 — but only on the axis that has
been measured.** On the passage axis it matters just as much at one document as at four hundred,
and at N = 100 it is a 4.5× larger effect than the document-level one the grid was tuned on.

### 4.4 The primary recall metric does **not** resolve — reported as unresolved, not as a null

`R_B@4096` (budget-matched character **recall** of the gold section) was pre-registered as *the*
primary. It does not resolve:

| N = 100, `R_B@4096` | tok256 | tok512 | tok1024 | tok2048 | spread |
|---|---:|---:|---:|---:|---:|
| mean | 0.7151 | 0.7237 | **0.7291** | 0.6722 | 0.0570 |

Extremes 256−2048 = **+0.0430**, δ80 **0.0714**, Holm p 0.37 → **UNRESOLVED (δ80 > bar)**. All six
pairwise contrasts are UNRESOLVED or a powered NULL; per-query discordance is 0.83, so the
instrument is noisy relative to a 0.05 bar at n = 260. `R_B@1024` shows a larger spread (0.135)
but is confounded by Addendum A1's overshoot for `tok2048` and is not read as primary.

**This is a genuine unresolved reading and is not converted into a null.** The size conclusion in
§4.2 rests on `H_B@4096` and `F1@10`, both of which were pre-registered in the same family and
both of which resolve by 2–5× their own floors.

> **Reading-rule discrepancy, disclosed.** The harness also emits a `spread_reading` field that
> requires `δ80 ≤ bar` — the *indistinguishability* rule from PREREG §6 T2, which is the correct
> test for proving an effect **small**. Applied to a **large** spread it wrongly prints
> `UNRESOLVED` (e.g. `H_B@4096` spread 0.181 with δ80 0.067). The pre-registered `resolved` rule
> of PREREG §5 — `|mean| ≥ bar` ∧ CI excludes 0 ∧ `δ80 ≤ |mean|`, plus Holm — is the one used
> above, applied to the extremes contrast. `spread_reading` is left in
> `report-rescore.json` unchanged rather than silently edited; it should be ignored.

### 4.5 A post-hoc control that resolves the one non-monotonicity

Raw `PH@1` is **not** monotone in chunk size (0.569 / 0.631 / 0.550 / 0.646 at N = 1) and none of
its contrasts resolve (δ80 ≈ 0.107 against effects ≤ 0.104). A bigger chunk covers more of the
document, so it hits the gold section more often *by arithmetic*. Computing the arithmetic
baseline — the probability that a **uniformly random** chunk of the gold document overlaps the
gold section by ≥ 50 characters — separates skill from size:

| config | chunks / gold doc | P(random chunk hits) | observed `PH@1` (N = 1) | **lift ratio** |
|---|---:|---:|---:|---:|
| `tok256` | 39.4 | 0.1633 | 0.5692 | **3.49×** |
| `tok512` | 19.9 | 0.1891 | 0.6308 | **3.34×** |
| `tok1024` | 10.2 | 0.2413 | 0.5500 | **2.28×** |
| `tok2048` | 5.4 | 0.3486 | 0.6462 | **1.85×** |
| `whole4096` | 1.0 | 0.4308 | 0.4308 | **1.00×** (by construction) |

**Monotone, and steeply.** `tok2048`'s attractive raw hit-rate is mostly arithmetic; measured as
retrieval *skill above chance*, small chunks are better by ~1.9× at the extremes. **This is
post-hoc and descriptive** — it was not pre-registered, carries no bar or δ80, and is reported as
an interpretive control, not as a test.

---

## 5. R4 — is retrieval trivial at 1 / 10 / 100 documents?

`TRIVIAL(F, N)` was defined in PREREG §6 **before measuring**: **T1** the *worst* of the four
configs scores ≥ 0.95 (Wilson 95 % lower bound ≥ 0.90 for proportions), **and T2** the four-cell
spread is below the family's bar *and* that reading is powered.

| family | metric | N = 1 | N = 10 | N = 100 |
|---|---|---|---|---|
| **document** | `DH@10` | **TRIVIAL** (degenerate: all four = 1.0000) | **TRIVIAL** (all four = 1.0000) | UNRESOLVED (T2 not powered: spread 0.0231, δ80 0.0284) |
| **document** | `nDCG@10` | **TRIVIAL** (degenerate) | UNRESOLVED (T2 not powered) | **NOT TRIVIAL** (T1 fails, worst 0.9468) |
| **passage** | `PH@1` | **NOT TRIVIAL** (worst 0.5500, Wilson lo 0.489) | **NOT TRIVIAL** (0.5308) | **NOT TRIVIAL** (0.5269) |
| **passage** | `H_B@4096` | **NOT TRIVIAL** (worst 0.8423) | **NOT TRIVIAL** (0.8231) | **NOT TRIVIAL** (0.8154) |
| **passage** | `R_B@4096` | **NOT TRIVIAL** (worst 0.7086) | **NOT TRIVIAL** (0.6814) | **NOT TRIVIAL** (0.6722) |

**Answer: yes and no, and the split is the finding.**

* **Document-level retrieval is trivial at 1–10 documents.** At N = 1 it is *degenerately* trivial
  — every metric is 1.000 for every config, by arithmetic, not by measurement. At N = 10, `DH@10`
  is 1.0000 for all four configs (top-10 of a 10-document corpus **is** the corpus). This is
  exactly the "small corpora are easy" position, confirmed, and it confirms it is a statement
  about the corpus size and not about the chunking.
* **Passage-level retrieval is not trivial at any size, including one document.** T1 fails
  everywhere and by a wide margin — the worst config's top-1 chunk is in the answering section
  only 53 % of the time, and even the *budget-matched* reading tops out at 0.82 for `tok2048`.
* **P3 CONFIRMED**, **P4 CONFIRMED**. **P5 PARTIALLY CONFIRMED** — the spread on `R_B@4096`
  exceeds `X_P` at every N (0.062 / 0.067 / 0.057) as predicted, but the predicted **ordering**
  ("smaller chunks win") is **wrong on that metric**: `tok1024` has the highest recall at every N.
  The harness's `hit: true` for P5 checks only the spread clause; the ordering clause failed and
  is recorded here as a failure. On `H_B@4096`, `F1@10` and the §4.5 lift the ordering does hold.

---

## 6. R5 — does "don't retrieve, stuff it in the window" apply? **The brief's premise is wrong by ~8×.**

Total corpus tokens per mini-corpus, n = 260, against a 131 072-token window:

| corpus | median | p10 | p90 | max | **fraction fitting 131 k** |
|---|---:|---:|---:|---:|---:|
| N = 1 | 8 778 | 5 629 | 15 294 | 43 587 | **100 %** |
| N = 10 (`topical`) | **101 412** | 81 376 | 123 915 | 180 878 | **94.6 %** |
| N = 10 (`random`) | 94 935 | 79 193 | 114 281 | 140 727 | 97.7 % |
| N = 100 (`topical`) | **1 029 657** | 921 641 | 1 087 426 | 1 199 758 | **0 %** |
| N = 400 (whole ×0) | 3 861 045 | — | — | — | 0 % |

**P7 CONFIRMED.** The task brief states that "at 100 documents the whole corpus fits in a 131k-token
context window". For this corpus it does not, by roughly **8×**: these are full-text open-access
articles averaging **9 933 tokens**, so a 131 k window holds about **13** of them, not 100.

What the numbers imply:

* **N = 1–13: retrieval is optional.** The corpus fits. The decision is cost and latency, not
  quality — and §3.1 says the *quality* argument actually favours not retrieving in one narrow
  sense (the whole document is guaranteed to contain the answer) while §7 says `whole4096` is the
  worst arm on every passage metric, because a single truncated vector cannot point *inside* the
  document.
* **N ≈ 10: it is a genuine coin-flip**, 94.6 % of these corpora fit. Stuffing works today and
  breaks on the first long article.
* **N = 100: retrieval is mandatory.** Nothing fits, and it is not close — the *minimum* observed
  100-document corpus is ~7× the window.
* The regime where §3's finding bites hardest is therefore **N ≈ 10–100**: too big to stuff, small
  enough that document-level metrics are saturated and tell you nothing about which passage you
  get.

---

## 7. The `whole4096` baseline — "do not chunk" loses on both axes

One vector per document over its first 4 080 tokens, `topical`:

| N | `DH@1` | `nDCG@10` | `PH@1` | `R_B@4096` | `P_B@4096` |
|---:|---:|---:|---:|---:|---:|
| 1 | 1.0000 | 1.0000 | 0.4308 | 0.3607 | 0.1034 |
| 10 | 0.8769 | 0.9438 | 0.4308 | 0.3596 | 0.1031 |
| 100 | **0.8577** | **0.9128** | **0.4308** | **0.3596** | 0.1032 |

Against the chunked arms at N = 100 (`DH@1` 0.915–0.977, `R_B@4096` 0.672–0.729) it is **worse on
both**. Its `PH@1` of 0.4308 is *exactly* its random-chunk baseline (§4.5) — with one unit per
document there is no passage selection at all, only truncation. And because the median gold
section starts at ~69 % of a ~8.8 k-token document (≈ token 6 100, past the 4 080 cap), most gold
sections are **structurally outside** the indexed window: this arm's passage numbers are largely
arithmetic, as pre-declared in PREREG §4.3. It is reported as a bound, not a comparison.

---

## 8. R6 — overlap at the passage level (secondary, descriptive)

The overlap null in Leg A and Leg B is a null about *document retrieval*. This is the first
measurement of the thing overlap is actually for: not cutting the answer in half.

**It does what it is supposed to do, at large chunk sizes** (`topical`, N = 100):

| size | `cov1` (best single-chunk coverage of gold section) 0 % | 12.5 % | 25 % | 25 %−0 % | verdict |
|---|---:|---:|---:|---:|---|
| 256 | 0.3182 | 0.3266 | 0.3297 | +0.0115 (δ80 0.0096) | UNRESOLVED |
| 512 | 0.4960 | 0.5213 | 0.5251 | +0.0291 (δ80 0.0187) | UNRESOLVED |
| 1024 | 0.7142 | 0.7360 | 0.7531 | +0.0389 (δ80 0.0246) | UNRESOLVED |
| **2048** | 0.8407 | 0.8912 | **0.9202** | **+0.0795** (δ80 0.0310) | **RESOLVED** |

| size | straddle rate (no single chunk contains the gold section) 0 % | 25 % | 25 %−0 % | verdict |
|---|---:|---:|---:|---|
| 256 | 1.0000 | 1.0000 | 0.0000 | DEGENERATE (a 256-token chunk can never contain a 1 233-token section) |
| 512 | 0.9500 | 0.9231 | −0.0269 (δ80 0.0443) | NULL (powered) |
| **1024** | 0.8077 | 0.7385 | **−0.0692** (δ80 0.0689) | **RESOLVED** |
| **2048** | 0.6192 | 0.4731 | **−0.1462** (δ80 0.0906) | **RESOLVED** |

**And it buys nothing measurable in passage retrieval.** Across all four sizes and both overlap
levels, every `R_B@4096` and `PH@1` overlap contrast is **UNRESOLVED** or a **powered NULL**
(`tok512`: −0.0048 and −0.0060 on `R_B@4096`, δ80 ≈ 0.042 — powered nulls). Not one is a resolved
gain.

**Interpretation, bounded.** Overlap measurably reduces straddling and improves single-chunk
coverage at 1024/2048 — the mechanism is real and this is the first time it has been shown. It
does **not** translate into a resolved passage-*retrieval* gain here, and the overlap-drop decision
made on document-retrieval evidence stands. The honest caveat this run adds: *if* the downstream
consumer depends on a single chunk containing the whole answering passage, overlap at large chunk
sizes does help, and no retrieval metric in this study would have shown you that. Smaller chunks
are the better answer to the same problem — `tok256` straddles 100 % of the time yet has the best
budget-matched hit rate (0.9962), because four small chunks reassemble the section that one big
chunk failed to contain.

---

## 9. Sensitivity, and what moved

**Neighbour policy.** `topical` (gold-document nearest neighbours by `whole4096` cosine — "my 100
papers on one subject") vs `random`. **P6 CONFIRMED**: at N = 100, `DH@10` is 0.9923 topical vs
0.9962 random, a difference of 0.0039. Document retrieval barely notices topically-matched
distractors. Passage metrics are almost policy-invariant too (`H_B@4096` at N = 100: 0.9962 /
0.9615 / 0.9154 / 0.8154 topical vs 0.9962 / 0.9731 / 0.9269 / 0.8308 random). Every conclusion
above holds under both policies.

**Query set.** Primary is the pre-registered **260 accepted**; the 396-query robustness set is a
sensitivity check. At N = 100 topical: `PH@1` drops 0.05–0.09 on all four configs (the 136
non-accepted queries are harder, as expected — they failed the verifier), document metrics rise
0.001–0.017, and **the ordering of the four configs is unchanged on every metric**. No conclusion
depends on the subset.

**Degeneracies, as pre-declared (PREREG §4) and observed.** Document metrics at N = 1 all exactly
1.0000 (§4.1). `DH@10` at N = 10 exactly 1.0000 for all four (top-10 = whole corpus). `PH@10` at
N = 1 is 1.0000 for `tok2048` (5.2 chunks/doc < 10) — flagged, not read. `straddle` at `tok256` is
1.0000 in all three overlap arms (arithmetically impossible to contain a 1 233-token section in a
256-token chunk). All Wilson-read, none quoted from a Wald SE.

**The δ80 bound, as pre-registered.** PREREG §5 predicted that at the document-level bar
`X_B = 0.010` a paired binary contrast on 260 queries resolves only if fewer than one query
disagrees. Borne out: `DH@10` extremes at N = 100 has discordance 2.7 % (7 queries) → δ80 0.0284,
**UNRESOLVED at its own bar**, exactly as declared in advance. Six of twenty-eight document-level
spread readings are unresolvable this way. **None is written as a null.**

---

## 10. What this means for the 1–100 document use case

1. **Stop reporting only document-level retrieval for small collections.** At 1–10 documents it is
   1.000 by construction and carries no information. Any stage-2 evaluation aimed at this regime
   must carry a passage-level metric or it is measuring the corpus size.
2. **Below ~13 documents, consider not retrieving.** The corpus fits a 131 k window. If you do
   retrieve, note that `whole4096` — one vector per document — is the *worst* arm measured: it
   cannot point inside a document, and 69 % of answers live past its truncation point.
3. **Between ~13 and 100 documents is the regime that needs the work.** Too large to stuff, and
   the document-level metrics are saturated at 0.92–0.99 while the top-1 chunk is right only
   ~56–63 % of the time.
4. **Chunk small.** On every size-fair passage reading — budget-matched hit rate, F1@10, and lift
   over the random-chunk baseline — 256 tokens is best and 2048 is worst, by margins (0.15–0.18,
   0.09–0.13, 1.9×) that dwarf the 0.02–0.04 document-level spread the grid was tuned on. The one
   dissent is budget-matched *recall*, where `tok1024` leads and **nothing resolves**.
5. **The overlap drop stands**, with a named caveat for single-chunk consumers at 1024/2048 (§8).
6. **Cheapest next step, if any:** rerank the chunk lists. This run is dense-only by
   pre-registration, and reranking is precisely a top-1 precision instrument — the axis on which
   the entire gap of §3 lives. That is the one intervention these numbers predict would move it,
   and it is untested here.

---

## 11. Artifacts

All under `phase0/rescore/` (durable copy at `~/Development/worktrees/phase0-rescue/`, and copied
to the session scratchpad path):

| file | what |
|---|---|
| `PREREG-rescore.md` | the pre-registration + Addendum A1 |
| `rescore_common.py` | corpus, judged set, provenance and the two hard gates |
| `rescore_embed.py` | pass 1 — chunk with exact char offsets, embed, persist |
| `rescore_score.py` | pass 2 — mini-corpora, chunk-granularity scoring |
| `rescore_report.py` | pass 3 — δ80, Wilson, Holm, TRIVIAL, predictions |
| `provenance-rescore.json` | commit, served model, gates, versions, GPU state |
| `spans_<key>.json`, `emb_<key>.npy` | 13 configs: chunk `(doc, start, end, ntok)` + fp16 vectors |
| `runs-rescore.json.gz` | **raw per-query arrays**, 104 cells (13 configs × 2 policies × 4 sizes), all 396 queries |
| `report-rescore.json` | every reading with its CI, δ80, discordance and Holm p |
| `neighbours.json`, `corpus_tokens.json` | the mini-corpus definitions and their token totals |
| `posthoc-random-chunk-baseline.json` | §4.5, labelled post-hoc |
| `estats-rescore.json`, `embed.log` | cost: 5.22 GPU-minutes, 0 retries |
