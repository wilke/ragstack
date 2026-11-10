<!-- experiment: Split answers under the adopted chunking: overlap, neighbours, or other cuts? | 2026-11-09 | EXPLORATORY, DRAFT -->
# Split answers: can overlap, judge-time neighbours or another cut keep an answer in one passage?

**Status: DRAFT, 2026-11-09. Exploratory. No judge call, store, retrieval or LLM call has been made.
Owner questions are marked OPEN (§8). Nothing here is pre-registered. The feasibility counts in §4 come from
[`artifacts/feasibility.json`](artifacts/feasibility.json), produced by
[`split_feasibility.py`](split_feasibility.py) at commit `57d2f15c` on a clean tree (`citable: true`).**

## 1. The question

The confirmatory boundary test ([`../alias-boundary-confirm-2026-11/`](../alias-boundary-confirm-2026-11/REPORT.md))
judged 351 stage-A *answers* pairs again under the adopted chunking. The adopted chunking is
`sentence/512/0`, `size_unit` tokens, `size_tolerance` 0.25, `structure_cuts` `sections/1`, `min_piece` 128.
Under it, 75 pairs lost the label, against 16 on the re-read window. The cover-any addendum
([ADDENDUM-2026-11-06-cover-any.md](../alias-boundary-confirm-2026-11/ADDENDUM-2026-11-06-cover-any.md)) split
those 75 into four outcomes:

| outcome | pairs | meaning |
|---|---|---|
| `other_only` (*recovered*) | 53 | another chunk overlapping the window answers |
| `none` (*split*) | 18 | no single overlapping chunk answers |
| `primary_only` | 4 | the re-read max-overlap chunk answered |
| `both` | 0 | |

Rechunk stage 1 ([`../rechunk-found10-2026-11/`](../rechunk-found10-2026-11/REPORT.md)) then showed that, for the
split pairs, the max-overlap chunk is in the default retriever's top 10 for 5 of 18.

This follow-up asks, exploratorily, which mitigation would put a split answer back into one judged passage:

- **(a)** chunk overlap, or more size tolerance;
- **(b)** judging a chunk together with its neighbours;
- **(c)** another cut the production chunker supports.

It also asks what each mitigation costs the 276 pairs that kept the label.

The OA chunking eval's post-hoc hypothesis B
([POSTHOC-HYPOTHESES-2026-11-06.md](../oa-chunking-eval-2026-10/POSTHOC-HYPOTHESES-2026-11-06.md)) is the prior.
There, 64 tokens of overlap recovered 34.5 points on the quotes that `ft512o0` split, and no gold quote crossed a
section boundary.

## 2. What the production chunker supports (checked in this tree)

| capability | where | consequence for this plan |
|---|---|---|
| **Size tolerance and overlap cannot be combined on `sentence`.** `size_tolerance` needs `chunk_overlap=0` ("merged pieces cannot also overlap"). | `python/ragstack/ingestion/chunkers.py:1165-1168` | Overlap *on the adopted chunker* is refused. An overlap arm must drop the tolerance and min-piece merge, which is the "strict" sentence chunker. The strict chunker without overlap is therefore a required comparator. |
| The tolerance needs `structure_cuts` and `size_unit=tokens`. The ceiling is `floor(512·(1+tol))`, clamped to the window budget. `MAX_SIZE_TOLERANCE = 1.0`. `min_piece` defaults to `chunk_size // 4`. | `chunkers.py:1156-1164`, `chunkers.py:160-176` | Tolerance can go up to 1.0 (ceiling 1024). Adopted: ceiling 640. |
| `chunk_overlap` is counted in **characters** for `sentence`/`words`, even under `size_unit=tokens`. | `chunkers.py:81-83`; CLI help `chunker_config.py:334` | Overlap arms are specified in chars (256/512/1024). |
| Overlap re-takes **whole trailing sentences** whose combined length is ≤ `chunk_overlap`. A last sentence longer than that gives no overlap at all. | `_overlap_resume`, `chunkers.py:1341-1359`; token packer `_pack_spans_tokens`, `chunkers.py:1723` | "k sentences of overlap" is not a setting; it is the realised effect of a char budget. §4.3 measures it. |
| Under `structure_cuts`, each cut segment is packed on its own, "so overlap restarts at every segment". | `_pack_segments`, `chunkers.py:1062-1092` (quote at `:1068`) | **Overlap cannot bridge a section wall.** |
| Tolerant cut: a segment at or under the ceiling stays whole. Pieces under `min_piece` merge within walls, or as whole sibling segments. | `_tolerant_cut`, `chunkers.py:1212-1248`; `_segment_walls`, `chunkers.py:939-960` | Only small sibling segments can merge across a wall. |
| `structure_cuts` value `sections/1`; segments from `doc_cut_segments`. | `chunkers.py:880-913` | Wall vs size-cut classification in §4.1. |
| `fixed_token` supports token overlap under `structure_cuts`. The window restarts, overlap included, at each segment. | `FixedTokenWindowChunker`, `chunkers.py:1858-2017` | `ft512o64s` is the OA eval's frozen decision arm, available as option (c). |
| Neighbour links: `chunk_index`, `prev_chunk_id`, `next_chunk_id` per document, across section walls. | `link_neighbors`, `chunkers.py:2028-2071` | Neighbours are already stamped on every chunk. |
| **Retrieval-time neighbours exist in production.** The `context_window` request field allows 0 to 3, default 0. `expand_context` walks prev/next after rerank. The answer prompt adds `(context before)` and `(context after)` blocks and scales the budget by `(2·window+1)`. | `python/ragstack/api/routers/query.py:79-82`, `:262`, `:376`; `python/ragstack/retrieval/retriever.py:765-784`; `python/ragstack/llm.py:184-186`, `:249-261` | Option (b) models a production feature, not a hypothetical one. |
| `embed_heading_path prefix/1` changes only the embedded text, not the spans. | `chunker_config.py:375-401` | Does not address splits. Out of scope. |
| `semantic` and `semantic_pooled` need embedding calls, and shard builds are unsupported for them. | `chunkers.py:2849`; `chunker_config.py:76`, `:259` | Not computable offline. Out of scope here. |

Every option is built from collection params by `chunker_config.build_chunker` (`chunker_config.py:695`), with the
production readers (`semantic_cap_from_params`, `size_unit_from_params`, `structure_from_params`).

## 3. Pairs, judge, labels

- **Groups** (from the rechunk label table):

  | group | pairs | definition |
  |---|---|---|
  | split | 18 | `none` |
  | recovered | 53 | `other_only` |
  | primary_only | 4 | |
  | kept_identical | 56 | the adopted chunk's text equals the window |
  | kept_changed | 220 | the other kept pairs |

  The 56 kept_identical pairs are all one-chunk documents of about 250 tokens on average. Every option is a no-op on them, so they serve as a sanity check, not a control.
- **Judge:** the frozen alias judge: Qwen3.6-35B-A3B (C, n=5) and Qwen3.8-27B (E), with the Gemini 4 Argon tiebreak. Claude Opus 5.5 reads a 5 % drift sample and sets no label. This is the same registry and code as the confirmatory and cover-any batches.
- **Label reuse:** a chunk id is `uuid5(doc:start:end)`. The same id means the same span of the same text, so its label in the rechunk label table (450 label rows) is reused and not re-judged. Every neighbour passage is a new item, unless it equals the max-overlap chunk (a one-chunk document).
- **Requests per item:** C (one request, 5 samples) plus E, so 2 base requests per item, plus Argon on the escalated ones (7.0 % on the stage-A *answers* stratum, about 9 % in the cover batch) and Opus on 5 %. That is about **2.15 requests per item**. The coordinator's gateway figure since stage A, about $9.2, is not broken down per item, so this plan gives items and requests, not dollars.
- **Missing negative control:** all 351 pairs were stage-A *answers*. Neither these files nor the label table hold a pair the judge said does *not* answer. See OPEN-2.

## 4. Offline feasibility counts (exploratory; no store, retrieval or LLM)

`split_feasibility.py` reads the private confirmatory run files and the label table, and re-chunks the 343 pair
documents (document text = the concatenated control chunks, which tile it) under each chunking. It runs three gates:

- all 351 windows reproduce from the document text;
- the 75 cover-seed windows agree with the retest export;
- the adopted chunking reproduces all 343 documents' control chunks (ids and spans).

Tokens are counted with the `Salesforce/SFR-Embedding-Mistral` HF tokenizer. The windows are the stage-A passages:
in every group except kept_identical, nearly all are 512 tokens (split: min 450, p10 512).

### 4.1 Where the adopted chunking cuts the windows

| group | pairs | cuts inside the window | at a section wall | size cuts | pairs with only wall cuts | only size cuts | both |
|---|---|---|---|---|---|---|---|
| split | 18 | 22 | **13** | 9 | 9 | 8 | 1 |
| recovered | 53 | 73 | 55 | 18 | 35 | 15 | 3 |
| primary_only | 4 | 4 | 2 | 2 | 2 | 2 | 0 |
| kept_changed | 220 | 258 | 159 | 99 | 114 | 78 | 17 (11 have no cut) |

In 10 of the 18 split windows a section edge falls inside the window. Overlap under section cuts can therefore
bridge at most the 9 size cuts. Even if it bridged every one, at most 8 split pairs would have all their cuts
bridged; the 9 wall-only pairs and the 1 mixed pair would not.

### 4.2 Alternative chunkings: does one chunk span every cut?

`bridged k1` / `bridged k2` = pairs for which every adopted cut inside the window lies inside one chunk of the
alternative, with at least 1 (or 2) sentences of the window on each side. Answer positions inside the window were
never annotated, so this is a proxy upper bound, not a recovery rate. An alternative can also cut the window
somewhere new. `contain` = one chunk holds the whole ~512-token window. That is possible only with a ceiling above
512, so the overlap arms show 0 by construction.

| chunking (descriptor) | split 18: contain / bridged k1 / k2 | recovered 53: k1 / k2 | kept_changed 220: k1 / k2 | chunks (343 docs) | tokens vs adopted |
|---|---|---|---|---|---|
| adopted `sentence/512/0` tol 0.25 | 0 / 0 / 0 | 0 / 0 | 11 / 11 (no-cut pairs) | 9,150 | 1.000 |
| tol 0.50 (ceiling 768) | 0 / 0 / 0 | 0 / 0 | 18 / 18 | 8,785 | 1.000 |
| tol 1.00 (ceiling 1024, the maximum) | 3 / 3 / 3 | 2 / 2 | 28 / 28 | 8,318 | 1.000 |
| strict (no tolerance, overlap 0) | 0 / 0 / 0 | 0 / 0 | 11 / 11 | 10,451 | 1.000 |
| strict + overlap 256 chars | 0 / 6 / 4 | 15 / 6 | 82 / 53 | 10,915 | 1.065 |
| strict + overlap 512 chars | 0 / **8** / **8** | 15 / 14 | 89 / 80 | 11,891 | 1.208 |
| strict + overlap 1024 chars | 0 / 8 / 8 | 15 / 15 | 89 / 88 | 18,522 | 2.118 |
| no section cuts + overlap 512 chars | 0 / **18** / 17 | 53 / 43 | 219 / 193 | 10,445 | 1.327 |
| `ft512o64s` (fixed_token 512/64 + sections) | 0 / 2 / 2 | 11 / 6 | 67 / 35 | 10,621 | 1.088 |

Readings, all exploratory:

- **Overlap with section cuts saturates at the size cuts.** At 512 chars, overlap bridges all 9 of the split pairs' size cuts and 8 of 18 pairs. At 1024 chars it adds nothing and doubles the tokens. It bridges none of the 13 wall cuts, as `chunkers.py:1068` implies.
- **Only dropping section cuts bridges everything** (18/18 at k1). That reverses the owner's 2026-11-04 section-cut decision and costs about 33 % more tokens.
- **More tolerance barely helps.** At the maximum (1.00), 3 of 18 windows fit in one chunk. The rest straddle walls or segments longer than 1024 tokens.
- **The OA eval's `ft512o64s` bridges 2 of 18.** 64 tokens is often shorter than one sentence either side of the cut.

### 4.3 Realised overlap (adjacent chunks that share text, 343 docs)

| chunking | adjacent pairs overlapping | mean shared chars |
|---|---|---|
| strict + 256 | 4,317 / 10,572 (41 %) | 181 |
| strict + 512 | 5,981 / 11,548 (52 %) | 405 |
| strict + 1024 | 12,681 / 18,179 (70 %) | 878 |
| no sections + 512 | 9,749 / 10,102 (97 %) | 405 |
| `ft512o64s` | 5,018 / 10,278 (49 %) | 222 |

Under section cuts about half of all adjacent pairs share no text, because a segment edge separates them or the last
sentence is longer than the budget.

### 4.4 Neighbour passages on the adopted chunks (option b)

"New items" counts distinct (record, passage); a passage equal to the max-overlap chunk M reuses M's label. Tokens
are counted for new passages only.

| passage | split 18: window inside / crosses a wall | recovered 53: inside | kept_changed 220: inside | tokens mean (p90), split | new items split / recovered / primary_only / kept_changed |
|---|---|---|---|---|---|
| M ± 1 (`context_window=1`) | **16** / 15 | 44 | 202 | 1,171 (1,603) | 18 / 53 / 4 / 219 |
| M ± 2 (`context_window=2`) | **18** / 17 | 53 | 220 | 1,938 (2,483) | 18 / 53 / 4 / 219 |
| adjacent pairs of overlapping chunks | 14 (all 22 cuts inside one pair) | 36 | 174 | 789 (1,036) | 22 / 73 / 4 / 258 |

Neighbour passages contain the split windows across walls. They are also 1.5× (adjacent pairs) to 3.8× (M ± 2)
longer than the ~512-token passages the judge labelled at stage A. That length difference is the main confound
of option (b) (OPEN-1, OPEN-2).

### 4.5 Judge items each option would need (not in the label table)

Items over the ≤ 6 chunks overlapping each window (the cover-any rule) plus M, with no label row:

| option | lost 75 (split / recovered / primary_only) | kept_changed | requests (≈ 2.15 × items), lost 75 only |
|---|---|---|---|
| tol 1.00 | 22 (5 / 14 / 3) | 266 | ≈ 47 |
| strict (comparator) | 40 (11 / 25 / 4) | 357 | ≈ 86 |
| strict + 512 | 100 (26 / 68 / 6) | 456 | ≈ 215 |
| no sections + 512 | 193 (44 / 137 / 12) | 541 | ≈ 415 |
| `ft512o64s` | 131 (34 / 90 / 7) | 479 | ≈ 282 |
| M ± 1 or M ± 2 (each) | 75 (18 / 53 / 4) | 219 | ≈ 161 |
| adjacent pairs | 99 (22 / 73 / 4) | 258 | ≈ 213 |

Under the adopted chunking, kept_changed already has 258 unjudged overlapping chunks, because cover-any ran only on
the 75 lost pairs. A control arm should sample kept_changed rather than judge all of it (OPEN-4).

## 5. The options

### (b) Judge-time neighbours: recommended first

- **Measures:** whether the frozen judge labels M ± 1 *answers* for the split pairs (16 of 18 windows fit inside), and M ± 2 for the remaining 2. Also the same rate on recovered and primary_only. Then whether longer passages *create* answers: kept_changed should stay *answers*, and stage-A *not* pairs should stay *not*.
- **Why first:** it is the production `context_window` path (§2). It needs no new collection and no re-chunking, it is the only option that crosses walls without reversing the section-cut decision, and it is the cheapest per pair.
- **Pairs:** split 18 + recovered 53 + primary_only 4, as M ± 1 (75 items). M ± 2 for the 2 split windows that M ± 1 does not contain (2 items). Controls: a kept_changed sample and a stage-A *not* sample (OPEN-2, OPEN-4).
- **Cost:** about 75–80 items for the lost pairs (≈ 170 requests), plus controls. For example, 60 kept_changed + 60 *not* would add ≈ 260 requests.
- **Passage format:** plain concatenation in the frozen prompt, or the production `(context before)` / `(context after)` blocks (`llm.py:184-186`). OPEN-1.

### (a) Overlap, which needs the strict sentence chunker

- **Measures:** cover-any under `strict + 512 chars`, compared with `strict` alone. The comparator separates the overlap effect from the effect of dropping the tolerance. The question is whether some single new chunk answers the 8 split pairs whose cuts are all size cuts, and what the arm does to recovered and kept pairs.
- **Expected ceiling:** 8 of 18 split pairs (§4.2). The 13 wall cuts are out of reach by construction.
- **Pairs:** the lost 75 under both arms (100 + 40 items, ≈ 300 requests), plus a kept_changed sample.
- **What it does not measure:** retrieval. A found@10 under the new chunks needs bench collections (as in rechunk stage 1); that is a separate, later stage.

### (c1) Larger size tolerance (`tol 1.00`)

- Cheapest re-chunk arm: 22 items, ≈ 47 requests.
- Ceiling: 3 of 18 split pairs.
- Worth judging only if (b) is rejected, because it changes chunk sizes corpus-wide (p90 626 tokens, max 1024) for a small gain.

### (c2) `ft512o64s`, the OA eval's frozen decision arm

- Bridges 2 of 18 split pairs.
- Listed for completeness, since hypothesis B is about it. On these pairs, 64 tokens is too short to span a sentence on each side of a cut.

### (c3) Sentence overlap without section cuts

- Bridges 18 of 18 split pairs at k1 and 17 at k2, and 53 of 53 recovered.
- Costs 193 items for the lost pairs and 1.33× the tokens.
- Reverses the adopted section-cut decision. Include it only if the owner wants the wall cost measured (OPEN-3).

## 6. Order and stopping (draft)

1. **(b) M ± 1** on the lost 75, with the two controls.
   - If split pairs recover at a rate the controls do not show (both *not*-pair false recovery and kept_changed loss low), report and stop.
   - Option (b) needs no chunking change: the production `context_window=1` already delivers it.
2. Only if (b) fails or is ruled out by OPEN-1: run **(a) strict + 512 vs strict**, cover-any, on the lost 75.
3. Re-chunk arms that pass (a) go to a retrieval stage with new bench collections, under their own plan.

No threshold is pre-registered. This is exploratory, and any rate it produces is descriptive.

## 7. What this plan does not do

- No retrieval, no collection, no gateway call.
- No answer-span annotation.
- No reading of private text into any public file.

## 8. Owner questions (OPEN)

- **OPEN-1:** Is judging a neighbour passage (1.5–3.8× the stage-A length) inside the frozen judge protocol, or a deviation that needs its own registry entry? Should the passage be plain text or the production `(context before)` / `(context after)` format?
- **OPEN-2:** A negative control needs stage-A pairs the judge said do *not* answer, judged as M ± 1. They are not in the confirmatory files. Should they be sampled from the stage-A records, and how many?
- **OPEN-3:** 13 of 22 split cuts are section walls. That conflicts with the OA eval's finding that no gold quote crossed a section boundary, though here the windows are fixed 512-token stage-A passages, not quotes. May a private, counts-only read of the 10 wall-crossing split windows check whether the answer needs text from both sides? Is the section-cut decision open to revisiting (c3) at all?
- **OPEN-4:** How large a kept_changed control sample? All 219 neighbour items cost ≈ 470 requests; a sample of 60 costs ≈ 130.
- **OPEN-5:** If (b) succeeds, is the conclusion "turn on `context_window=1` in the production answer path", and who owns that change? It doubles to triples the answer-prompt context (`llm.py:249-261`).
- **OPEN-6:** Gateway budget and notice for about 300–600 requests. Which window, and who is notified?

## 9. Reproduce

```
HF_HUB_OFFLINE=1 PYTHONPATH=python:python/scripts/eval python \
  docs/plans/results/split-answer-2026-11/split_feasibility.py \
  --confirm-dir CONFIRM_DIR --label-table LABEL_TABLE.jsonl \
  --out docs/plans/results/split-answer-2026-11/artifacts/feasibility.json
```

`CONFIRM_DIR` is the confirmatory test's private run directory, and `LABEL_TABLE.jsonl` is rechunk stage 1's
label table. The script also reads that directory's `retest/chunks-retest.jsonl` for the 276 kept windows; the
lost windows are cross-checked against the cover seeds. Input sha256s are in the artifact. The output is
counts-only, and the script refuses ids, private text, host fields and paths before writing.
