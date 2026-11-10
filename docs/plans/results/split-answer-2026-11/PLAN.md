<!-- experiment: Split answers under the adopted chunking: overlap, neighbours, or other cuts? | 2026-11-09 | EXPLORATORY, DRAFT; owner decisions 2026-11-10 -->
# Split answers: can overlap, judge-time neighbours or another cut keep an answer in one passage?

**Status: DRAFT, 2026-11-09; the owner's answers of 2026-11-10 are folded in (§9.1: OPEN-1, OPEN-3 and OPEN-6
decided; OPEN-4 superseded). Exploratory. No judge call, store, retrieval or LLM call has been made.
OPEN-2 and OPEN-5 are still open (§9.2). Nothing here is pre-registered. The feasibility counts in §4 come from
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
| `fixed_token` supports token overlap under `structure_cuts`. The window restarts, overlap included, at each segment. | `FixedTokenWindowChunker`, `chunkers.py:1858-2017` | `ft512o64s` is the OA eval's frozen decision arm, listed under option (c). |
| Neighbour links: `chunk_index`, `prev_chunk_id`, `next_chunk_id` per document, across section walls. | `link_neighbors`, `chunkers.py:2028-2071` | Neighbours are already stamped on every chunk. |
| **Retrieval-time neighbours exist in production.** The `context_window` request field allows 0 to 3, default 0. `expand_context` walks prev/next after rerank. The answer prompt adds `(context before)` / `(passage)` / `(context after)` blocks and scales the budget by `(2·window+1)`. | `python/ragstack/api/routers/query.py:79-82`, `:262`, `:376`; `python/ragstack/retrieval/retriever.py:765-839`; `python/ragstack/llm.py:184-239`, `:241-284` | Option (b) models a production feature, not a hypothetical one. The exact format is in §3.1. |
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
- **Judge:** the frozen alias judge: Qwen3.6-35B-A3B (C, n=5) and Qwen3.8-27B (E), with the Gemini 4 Argon tiebreak. Claude Opus 5.5 reads a 5 % drift sample and sets no label. This is the same registry and code as the confirmatory and cover-any batches. Argon and Opus are the gateway backends; C and E run on the Qwen endpoints.
- **Label reuse:** a chunk id is `uuid5(doc:start:end)`. The same id means the same span of the same text, so its label in the rechunk label table (450 label rows) is reused and not re-judged. Every neighbour passage is a new item, unless it equals the max-overlap chunk (a one-chunk document).
- **Requests per item:** C (one request, 5 samples) plus E, so 2 base requests per item, plus Argon on the escalated ones (7.0 % on the stage-A *answers* stratum, about 9 % in the cover batch) and Opus on 5 %. That is 2.12–2.14, rounded up to **2.15 requests per item**. The coordinator's gateway figure since stage A, about $9.2, is not broken down per item, so this plan gives items and requests, not dollars.
- **Missing negative control:** all 351 pairs were stage-A *answers*. Neither these files nor the label table hold a pair the judge said does *not* answer. See OPEN-2 (still open).

### 3.1 Neighbour passage format: exploratory protocol extension (OPEN-1, decided 2026-11-10)

The owner decided to judge the neighbour passage **as production delivers it**. The frozen judge prompt
(`alias-judge/1`, and `alias-judge/2` for Argon) is unchanged. Only the passage slot changes: it holds the
max-overlap chunk M together with its before/after neighbours, in the production context-window format. **This is
an exploratory extension of the judge protocol.** It is not covered by the alias prereg or the confirmatory prereg.
Its labels are reported as their own item type (`neighbour/M±w`) and never pooled with chunk-passage labels.

**Where production builds it.**

- `expand_context` (`python/ragstack/retrieval/retriever.py:765-839`) walks `prev_chunk_id` / `next_chunk_id` up
  to `window` hops each way. It attaches each neighbour as a `ContextChunk(position=direction * hop, content=…)`
  (`:834-836`) and sorts by position (`:838`). The walk stops at a document edge or at an unreadable neighbour.
- `RagGenerator._passage_text` (`python/ragstack/llm.py:189-239`) renders one source with its context, using the
  block markers defined at `llm.py:184-187`:

  ```python
  _BEFORE = "(context before)\n"
  _PASSAGE = "(passage)\n"
  _AFTER = "(context after)\n"
  _ELLIPSIS = "\u2026"
  ```

  The before-neighbours are joined with `"\n"` in position order (`:214`), and so are the after-neighbours
  (`:215`). The up-to-three parts are joined with `"\n"` (`:239`).
- `_format_context` (`llm.py:241-284`) prefixes each source with its citation number `[i] ` (`:266`) and joins
  sources with `"\n\n"` (`:284`). The public `format_context` (`llm.py:286-294`) is the same text. Its docstring
  says a second formatter "would be a second set of rules to keep in step".

**The judged passage at `context_window=1`**, literally (`⏎` = newline):

```
(context before)⏎<M−1 content>⏎(passage)⏎<M content>⏎(context after)⏎<M+1 content>
```

That is, as it reaches the prompt:

```
(context before)
<content of M−1>
(passage)
<content of M>
(context after)
<content of M+1>
```

At `context_window=2`, both before-neighbours sit under one `(context before)` marker, as `M−2⏎M−1`, and both
after-neighbours under one `(context after)`, as `M+1⏎M+2`. At a document edge the missing side is dropped,
marker included (`_passage_text` adds a part only when that side has text). A one-chunk document has no context.
`_passage_text` then returns the bare content (`:208-209`), which is why the 56 kept_identical pairs reuse M's label.

**Implementation rules for the item builder** (to be written; not in this PR):

1. Build the text by calling the production formatter: `RagGenerator.format_context([source])` on a single
   `Source` whose `context` holds the neighbours as `ContextChunk`s. Do not re-implement the markers.
2. Strip the leading citation prefix `[1] ` (`llm.py:266`). It numbers sources in a multi-source answer prompt and
   is not part of the passage. The frozen judge prompt has its own single-passage slot.
3. Assert that nothing was trimmed: the text contains no `…` that is absent from the chunk texts, and it equals the
   untrimmed concatenation above. With one source, the budget is `llm_max_context_chars · (2w+1)`
   (`config.py:1128`, default 8000; `llm.py:261`): 24,000 chars at w=1 and 40,000 at w=2. The longest M ± 1
   passage is 1,769 SFR tokens, and the longest M ± 2 is 2,582 (artifact). Trimming is therefore not expected, but
   the builder checks it rather than assuming it.
4. Neighbours come from the adopted chunks' own `prev_chunk_id` / `next_chunk_id`. These links cross section walls,
   as production's do. Production's de-duplication (a neighbour that is itself a returned source is not attached;
   `retriever.py:832-833`) cannot apply, because each item has one source.

**What this does and does not model.** The item is what production delivers for one source that has the whole
budget. In a multi-source answer prompt, each source is fitted into `budget // n` (`llm.py:262`, `:271`). With 10
sources at w=1 that is 2,400 chars, about one chunk. The context then gives way passage-first: `(context before)` is
trimmed from its left and `(context after)` from its right, each marked with `…`. A recovery here is therefore an
upper bound on what a top-10 answer prompt actually shows the model. That caveat belongs in any OPEN-5 conclusion.

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
| no section cuts + overlap 512 chars (*context only; dropped as an arm, OPEN-3*) | 0 / 18 / 17 | 53 / 43 | 219 / 193 | 10,445 | 1.327 |
| `ft512o64s` (fixed_token 512/64 + sections) | 0 / 2 / 2 | 11 / 6 | 67 / 35 | 10,621 | 1.088 |

Readings, all exploratory:

- **Overlap with section cuts saturates at the size cuts.** At 512 chars, overlap bridges all 9 of the split pairs' size cuts and 8 of 18 pairs. At 1024 chars it adds nothing and doubles the tokens. It bridges none of the 13 wall cuts, as `chunkers.py:1068` implies.
- **Only dropping section cuts bridges everything** (18/18 at k1), at about 33 % more tokens. The owner kept section cuts on 2026-11-10 (OPEN-3), so this row is context only. It shows that the walls, not the chunk size, are what overlap cannot reach.
- **More tolerance barely helps.** At the maximum (1.00), 3 of 18 windows fit in one chunk. The rest straddle walls or segments longer than 1024 tokens.
- **The OA eval's `ft512o64s` bridges 2 of 18.** 64 tokens is often shorter than one sentence either side of the cut.

### 4.3 Realised overlap (adjacent chunks that share text, 343 docs)

| chunking | adjacent pairs overlapping | mean shared chars |
|---|---|---|
| strict + 256 | 4,317 / 10,572 (41 %) | 181 |
| strict + 512 | 5,981 / 11,548 (52 %) | 405 |
| strict + 1024 | 12,681 / 18,179 (70 %) | 878 |
| no sections + 512 (context only) | 9,749 / 10,102 (97 %) | 405 |
| `ft512o64s` | 5,018 / 10,278 (49 %) | 222 |

Under section cuts about half of all adjacent pairs share no text, because a segment edge separates them or the last
sentence is longer than the budget.

### 4.4 Neighbour passages on the adopted chunks (option b)

"New items" counts distinct (record, passage); a passage equal to the max-overlap chunk M reuses M's label. Tokens
are counted for new passages only, as plain chunk text. The §3.1 markers add a few tokens per passage.

| passage | split 18: window inside / crosses a wall | recovered 53: inside | kept_changed 220: inside | tokens mean (p90), split | new items split / recovered / primary_only / kept_changed |
|---|---|---|---|---|---|
| M ± 1 (`context_window=1`) | **16** / 15 | 44 | 202 | 1,171 (1,603) | 18 / 53 / 4 / 219 |
| M ± 2 (`context_window=2`) | **18** / 17 | 53 | 220 | 1,938 (2,483) | 18 / 53 / 4 / 219 |
| adjacent pairs of overlapping chunks | 14 (all 22 cuts inside one pair) | 36 | 174 | 789 (1,036) | 22 / 73 / 4 / 258 |

Neighbour passages contain the split windows across walls. They are also 1.5× (adjacent pairs) to 3.8× (M ± 2)
longer than the ~512-token passages the judge labelled at stage A. That length difference is the main confound of
option (b). The owner accepted it as an exploratory protocol extension (§3.1). The full kept_changed control (§5)
measures the length-driven loss of *answers*. Length-driven creation of *answers* needs the negative control
(OPEN-2, open).

### 4.5 Judge items each option would need (not in the label table)

Items over the ≤ 6 chunks overlapping each window (the cover-any rule) plus M, with no label row:

| option | lost 75 (split / recovered / primary_only) | kept_changed | requests (≈ 2.15 × items), lost 75 only |
|---|---|---|---|
| tol 1.00 | 22 (5 / 14 / 3) | 266 | ≈ 47 |
| strict (comparator) | 40 (11 / 25 / 4) | 357 | ≈ 86 |
| strict + 512 | 100 (26 / 68 / 6) | 456 | ≈ 215 |
| no sections + 512 (context only, not an arm) | 193 (44 / 137 / 12) | 541 | ≈ 415 |
| `ft512o64s` | 131 (34 / 90 / 7) | 479 | ≈ 282 |
| M ± 1 or M ± 2 (each) | 75 (18 / 53 / 4) | 219 | ≈ 161 |
| adjacent pairs | 99 (22 / 73 / 4) | 258 | ≈ 213 |

Under the adopted chunking, kept_changed already has 258 unjudged overlapping chunks, because cover-any ran only on
the 75 lost pairs. These are not needed for option (b). The owner chose full controls for (b) (OPEN-6): all 219
kept_changed M ± 1 items, not a sample. That supersedes OPEN-4.

## 5. The options

### (b) Judge-time neighbours: recommended first, and the only arm planned now

- **Measures:** whether the frozen judge labels M ± 1 *answers* for the split pairs (16 of 18 windows fit inside), and M ± 2 for the remaining 2. Also the same rate on recovered and primary_only. Then, as controls, whether longer passages *lose* answers (the 219 kept_changed items should stay *answers*) and, if OPEN-2 is granted, whether they *create* answers (stage-A *not* pairs should stay *not*).
- **Why first:** it is the production `context_window` path (§2, §3.1). It needs no new collection and no re-chunking, it is the only option that crosses walls without reversing the section-cut decision, and it is the cheapest per pair.
- **Passage:** the production context-window format, exactly as in §3.1, in the frozen judge prompt.
- **Items (OPEN-6, decided):**

  | item set | items |
  |---|---|
  | M ± 1 on the lost 75 (split 18, recovered 53, primary_only 4) | 75 |
  | M ± 1 on all kept_changed control items | 219 |
  | **owner-decided core** | **294** |
  | M ± 2 for the 2 split windows that M ± 1 does not contain (carried over from the draft) | 2 |
  | **total** | **296** |

  The 56 kept_identical pairs need no item: the passage is M, and its label is reused (§3.1). They remain a sanity check.
- **Cost:** see §6.

### (a) Overlap, which needs the strict sentence chunker: only if (b) fails

- **Measures:** cover-any under `strict + 512 chars`, compared with `strict` alone. The comparator separates the overlap effect from the effect of dropping the tolerance. The question is whether some single new chunk answers the 8 split pairs whose cuts are all size cuts, and what the arm does to recovered and kept pairs.
- **Expected ceiling:** 8 of 18 split pairs (§4.2). The 13 wall cuts are out of reach by construction, and the owner kept section cuts (OPEN-3).
- **Pairs:** the lost 75 under both arms (100 + 40 items, ≈ 300 requests), plus a kept_changed sample sized at the time.
- **What it does not measure:** retrieval. A found@10 under the new chunks needs bench collections (as in rechunk stage 1); that is a separate, later stage.

### (c1) Larger size tolerance (`tol 1.00`): not planned

- Cheapest re-chunk arm: 22 items, ≈ 47 requests.
- Ceiling: 3 of 18 split pairs.
- Not planned. It changes chunk sizes corpus-wide (p90 626 tokens, max 1024) for a small gain. Reconsider only if both (b) and (a) fail.

### (c2) `ft512o64s`, the OA eval's frozen decision arm: not planned

- Bridges 2 of 18 split pairs.
- Listed for completeness, since hypothesis B is about it. On these pairs, 64 tokens is too short to span a sentence on each side of a cut.

### (c3) Sentence overlap without section cuts: dropped (OPEN-3, 2026-11-10)

- The owner kept section cuts. This option is not an arm. Its feasibility rows in §4.2, §4.3 and §4.5 stay as context only: they show what the walls cost.

## 6. Cost and gateway notice (option b, OPEN-6 decided)

| | items | requests |
|---|---|---|
| per item | 1 | 2 base (C, one request with n=5; E) + Argon on escalations (7–9 %) + Opus drift (5 %) = 2.12–2.14, **≈ 2.15** |
| owner-decided core (75 + 219) | 294 | ≈ 632 |
| plus M ± 2 for 2 split windows | 2 | ≈ 4 |
| **total** | **296** | **≈ 636** (floor 592 = 2 × 296 base requests if nothing escalates and no Opus) |

This matches the owner's "about 600".

- **Split by backend:** about 592 Qwen requests (296 C + 296 E), and about **36–42 gateway requests**: Argon ≈ 21–27 (7–9 % of 296) and Opus ≈ 15 (5 %). These are under the 7 %-of-registry Opus cap the confirmatory test used (≈ 21 here).
- **Notice before any gateway batch:** an OpenChia/gateway size notice is required before any Argon or Opus call. It names the expected Argon and Opus call counts and token estimate, and the batch waits for acknowledgement (as in the confirmatory prereg §5 step 4). If the acknowledgement cannot be obtained, the owner's explicit go is the only substitute, and it is recorded as a deviation (as in the confirmatory test's D-1 and D-2).
- **The preflight size notice now uses per-model rates (PR #991).** `gateway_est_input_tokens` counts the text the batch actually sends (the judge version's system message plus each user message) and divides by a per-family chars/token rate: Gemini 4.26, Claude 2.81, other 3.4. PR #991 is merged to `main` (`5814c465`), but this branch predates it (base `8afb4cab`). The preflight must run from a tree that includes it.
- **Do not reuse stage-A per-call token figures.** Neighbour passages average ≈ 1,170 SFR tokens against ≈ 512 at stage A (§4.4). The gateway input per call will be correspondingly larger, and the preflight computes it from the §3.1 text.
- **No dollar figure:** as in §3, the gateway spend since stage A is not broken down per request.

## 7. Order and stopping (draft)

0. **Private, counts-only read of the 10 wall-crossing split windows (OPEN-3, granted).** For each window, does the
   answer need text from both sides of the section wall? Only counts are published (needs both / one side suffices /
   unclear). No text, ids or spans. Do it before any neighbour label is seen, so the read cannot be steered by the
   labels. It makes no LLM call and sets no label.
1. **(b) M ± 1** on the lost 75 plus the 219 kept_changed control items, and M ± 2 on the 2 uncontained split
   windows, in the §3.1 format, after the §6 notice.
   - If split pairs recover at a rate the controls do not show (low kept_changed loss and, if OPEN-2 is granted, low false recovery on *not* pairs), report and stop. Without OPEN-2, false creation is unmeasured, and the report says so.
   - Option (b) needs no chunking change: production `context_window=1` already delivers the format. Whether it delivers the full context in a multi-source prompt is the §3.1 caveat (OPEN-5).
2. **Only if (b) fails:** run **(a) strict + 512 vs strict**, cover-any, on the lost 75. Its ceiling is the 8 size-cut-only split pairs.
3. Re-chunk arms that pass (a) go to a retrieval stage with new bench collections, under their own plan.

No threshold is pre-registered. This is exploratory, and any rate it produces is descriptive.

## 8. What this plan does not do

- No retrieval, no collection, no gateway call (until the §6 notice is acknowledged for step 1).
- No answer-span annotation. The §7 step 0 read gives counts, not spans.
- No reading of private text into any public file.

## 9. Owner decisions and open questions

### 9.1 Owner decisions (2026-11-10)

| question | decision | effect on this plan |
|---|---|---|
| **OPEN-1:** is judging a neighbour passage inside the frozen protocol, and in which format? | **Yes, judge it as production delivers it.** Same frozen judge prompt. The passage is the chunk with its before/after neighbours in the production context-window format. Recorded as an exploratory protocol extension. | §3.1 (format with `llm.py` / `retriever.py` file:line; builder rules), §4.4, §5 (b). |
| **OPEN-3:** may the wall-crossing split windows be read, and is the section-cut decision open? | **No, keep section cuts.** The no-section-cut option is dropped as an arm; its feasibility row stays as context only. The wall-crossing windows may be read privately, counts only. | §4.2, §4.3, §4.5 rows marked context only; §5 (c3) dropped; §7 step 0. |
| **OPEN-6:** gateway budget and notice. | **Full controls, about 600 judge requests:** neighbours M ± 1 on the 75 lost pairs plus all 219 kept-label control items. | §5 (b) items; §6 cost (≈ 2.15 per item, 296 items, ≈ 636 requests), OpenChia/gateway size notice, PR #991 preflight. |
| **OPEN-4:** size of the kept_changed control sample. | **Superseded by OPEN-6:** all 219 items, no sample. | §4.5, §5 (b). |

### 9.2 Still open

- **OPEN-2:** A negative control needs stage-A pairs the judge said do *not* answer, judged as M ± 1 in the §3.1 format. They are not in the confirmatory files. Should they be sampled from the stage-A records, and how many? Each 60 such items would add ≈ 130 requests to §6's total.
- **OPEN-5:** If (b) succeeds, is the conclusion "turn on `context_window=1` in the production answer path", and who owns that change? It triples the answer-prompt budget (`llm.py:261`). Under a multi-source budget the context is trimmed passage-first (§3.1), so (b)'s rate is an upper bound for a top-10 prompt.

## 10. Reproduce

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
