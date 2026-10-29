# Pre-registration: a dedicated test of the reranker stage — DRAFT v1, 2026-10-29. Not frozen.

> **Public copy, drafted for freezing.** Frozen on the owner's go, as written: its sha256 and a UTC
> timestamp go into a private `PREREG.sha256` kept with the outputs, and the sha is then recorded at the
> top of this file and in `README.md` (the part-4 procedure). Freezing happens **after** the harness of §9
> is on `main` (it is: #862 at `e3002ba`) and **before** any arm in §3 is run on any set in §4. Any later
> change is a `DEVIATIONS.md` entry. The file names no tenant, collection, host, endpoint, product or
> gateway owner; the partner that holds the papers-OC collection is "the partner".

**v1 (this text):** the v0 draft of 2026-10-29 revised against the merged harness
(`python/scripts/eval/reranker_test.py`, #862) and the owner's decisions of the same day (§12).

**Status.** CONFIRMATORY for H1–H3 on set **docs-A/T512o0** (family A) and, when they exist, on the
round-2 multi-paper sets (family B, §4). Everything on the partner's 155-record set is EXPLORATORY.

**Terms.**
- **default:** the shipped path — per leg `top_k × RETRIEVAL_CANDIDATE_MULTIPLIER (2)` candidates (BM25
  and SFR dense), RRF k = 60, cut to `RERANK_CANDIDATES` (50), rescored by the cross-encoder on `content
  + "\n" + symbol_glossary`, cut to `top_k`. The reranker's order *replaces* the fused order; no
  first-stage score survives.
- **pool(D, leg):** the first D candidates the first stage hands to the reranker for a leg
  (`bm25` / `vector` / `hybrid`; `reranker_test.LEGS`).
- **ceiling@D:** a record counts if any gold chunk is in pool(D) — first-stage recall at depth, the
  oracle bound on anything the reranker can do.
- **found@k:** a gold chunk is in the final top k. **Gold** = the chunks containing the record's quote
  under the gold normalisation (NFKC, casefold, whitespace removed; the parts 2–4 rule and the partner's
  rule; `reranker_test.build_gold` against the full chunk dump).
- **ordering loss@D:** ceiling@D − found@10 for the reranked arm at pool depth D: records the reranker
  *had* and did not place in the top 10.
- **SFR / XLM-R:** chunk sizes are in SFR-Embedding-Mistral tokens, reranker input lengths in XLM-R
  tokens (bge-reranker-v2-m3's tokenizer; `census`).

## 0. Lab-book corrections this prereg depends on (made; `../../glossbench-2026-10/LABBOOK.md`, addendum 2026-10-29, evening; #860)

1. **"85 vs 65 on the live papers-OC collection" is not a contrast.** 85/155 is BM25 alone with the
   glossary field boost going 0 → 1 (28 → 85), no reranker (Test 2 of `docs/plans/symbol-glossary.md`,
   2026-10-22). 65/155 is the reranker with its input changed from `content` to `content + glossary`
   (53 → 65; shuffled 57), over the API's own 50-candidate pool (Test 3). Different tests, different legs;
   the pairing is withdrawn in the lab book. **No arm below uses it.**
2. **The 50-pool held a gold chunk for only 105 of 155 records** (Test 3), so the reranked path was capped
   at 105 before the reranker scored anything; the 50-pool's own fused top 10, unreranked, found 33. The
   candidate pool is a first-class quantity here (ceiling@D), separate from the reranker's ordering.
3. The four-condition bench-copy table (base 52/55/68/70, bm25 62/67/79/82, norerank 48/56/67/61; lab book
   Part 3) is the partner's measurement on the papers-OC bench copy and is the comparison the exploratory
   part of this test extends (§4, set P).

## 1. The question and the decomposition

On papers-OC, BM25 alone at top 10 found more records than the default hybrid+rerank path in every
glossary condition. Four non-exclusive causes are on the table; each has a measurement that isolates it:

| cause | what would show it | arm(s) |
|---|---|---|
| **C1 pool:** the fused 50-pool drops gold that BM25 ranks high (RRF k = 60 lets a weak dense leg dilute the pool; deep RRF rewards presence in both legs over a high rank in one) | ceiling@50(hybrid) < ceiling@50(bm25); rerank-over-bm25 recovers | A3, A5, A10 |
| **C2 reranker ordering:** gold is in the pool, the cross-encoder ranks it below 10 | ordering loss@50 large; rank-of-gold in the reranked list is 11–50 | A4, A5, rank-of-gold |
| **C3 reranker input / truncation:** the glossary suffix, its position, or `MAX_LENGTH` cuts | input variants move found@10; any input > `MAX_LENGTH` XLM-R tokens | A7, length census |
| **C4 evaluation matching:** the reranker promotes answer-bearing chunks that do not carry the quote | neighbour hits; judged answer-presence on discordant records | E6, E7 |

Decomposition reported for every arm and set: `n = (n − ceiling@D) + ordering loss@D + found@10`
(`stats`).

## 2. Hypotheses

**Confirmatory family (per set, Holm over 3, α = 0.05; `stats --delta 0.05 --alpha 0.05`):**

| id | hypothesis | type | contrast |
|---|---|---|---|
| H1 | BM25 alone finds more at top 10 than the default | superiority, one-sided | found@10(A0) − found@10(A4-50) > 0 |
| H2 | the fused pool has lower first-stage recall than the BM25 pool at the same depth | superiority, one-sided | ceiling@50(hybrid) − ceiling@50(bm25) < 0 |
| H3 | the reranker is non-inferior to BM25 when fed BM25's own candidates | non-inferiority, δ | found@10(A5-50) − found@10(A0) > −δ |

**δ = 0.05** of questions (owner, §12 D1). Reading the pattern: H1 ∧ H2 ∧ H3 → the pool is the cause
(C1); H1 ∧ ¬H3 → the reranker's ordering is at fault regardless of pool (C2); H1 ∧ ¬H2 → fusion is not
the mechanism, look at C2/C3. "No difference" is never written; an unconfirmed non-inferiority is *not
shown non-inferior*.

**Exploratory (reported with the same statistics, uncorrected p with a Bonferroni count beside, labelled
exploratory in every sentence; none is called a finding):**
E1 depth sweep found@10 and ordering loss at D ∈ {10, 20, 50, 100, 200} (A4, A5);
E2 reranker input: `content` / `content+gloss` / `gloss+content` / `content+shuffled` / `gloss-only` (A7);
E3 score fusion of reranker with first stage (A9);
E4 dense-only and rerank-over-dense (A1, A6);
E5 an alternative cross-encoder, if one is deployed (A8; §12 D7: none in the confirmatory run);
E6 neighbour hits and rank-of-gold distributions;
E7 judged answer-presence on discordant records (needs a judge; §10);
E8 the four glossary conditions × reranker arms on the papers-OC bench copy (set P; store swaps, under
round 2's live-ops plan).

## 3. Arms

Three routes, named in every result (`arm_map`): **API** (`pools --backend api`: `POST /v1/retrieve` with
`retrieval_mode`, `rerank=false`, `top_k = D`; `--live hybrid@50,bm25@50,vector@50` also captures the
API's own reranked top 10 with `rerank=true, rerank_candidates=D`), **in-process** (`pools --backend
inprocess`: the section-aware-trial BM25 / dense / RRF code as `deployed_docs_part4.production_env` uses
it, to any depth), **offline** (`rerank`: captured pools reranked through the same sidecar; the identity
gate below), **swap** (store write on a bench copy — a separate live-ops plan, E8 only).

| arm | definition | route |
|---|---|---|
| A0 **bm25** | `retrieval_mode=bm25, rerank=false`, top 10 (prefix of the bm25 pool) | API / in-process |
| A1 dense | `retrieval_mode=vector, rerank=false`, top 10 | API / in-process |
| A2 hybrid-shallow | default fusion, `rerank=false` (2×10 per leg, cut 10) — the lab book's "norerank" | API |
| A3 hybrid-deep-norerank | `top_k=50, rerank=false`; the fused 50-pool's own top 10 (Test 3: 33) | API, cut offline |
| A4-D **default at depth D** | `rerank_candidates=D`, D ∈ {10, 20, **50**, 100, 200}; A4-50 is the shipped default | API (`--live hybrid@D`) or offline |
| A5-D rerank over bm25 | `retrieval_mode=bm25, rerank=true, rerank_candidates=D` | API (`--live bm25@D`) or offline |
| A6 rerank over dense | `retrieval_mode=vector, rerank=true, rerank_candidates=50` | API or offline |
| A7-x reranker input | same 50-pool as A4-50; x ∈ `RENDERINGS` = {`content`, `content+gloss` (default), `gloss+content`, `content+shuffled`, `gloss-only`} | offline (`rerank --jobs hybrid@50:*`) |
| A8 alt reranker | A4-50 and A5-50 with a second cross-encoder | offline, 2nd sidecar (**absent**: §12 D7) |
| A9 score fusion | RRF (k = 60) of reranker rank with BM25 rank; and with the fused rank (`fuse`, `DEFAULT_FUSE`) | offline |
| A10 ceilings | `top_k=D, rerank=false` for bm25 / vector / hybrid → ceiling@D | API (D ≤ 100) / in-process (D ≤ 200) |

**`gloss-only` is exploratory only:** a glossary-less chunk is scored on an **empty passage**, so the
arm is recorded with `role: exploratory` and never enters a family.

**Pinned request fields.** Every API request carries **`use_graph=false`** (`reranker_test.capture_api`),
which pins the two legs the prereg defines (BM25, dense) and keeps the graph leg out of the pool.

**Shuffled glossary (A7-`content+shuffled`):** `rerank --shuffle-seed 261029 --shuffle-unit string`
(`shuffle_glossaries`): a seeded permutation of whole glossary strings across **all glossary-bearing
chunks of the set's dump**; fixed points are allowed and counted (`unchanged`). **This differs from the
57/155 control** of Test 3 (2026-10-22), which permuted whole glossary strings as a seeded
**derangement** (no fixed points) over the glossaried chunks **present in the 50-pools** only (source:
that session's scratch script, read in the review of #862). For replication of 57/155 the derangement
over pool chunks is the reference; our A7-shuffled is the pre-registered variant, and the difference is
stated beside every shuffled figure. `--shuffle-unit entry` (glossary lines) exists and is not used here.

**Fixed settings (manipulation check, logged before any arm; `PREREG_SETTINGS`, a mismatch stops the
run, exit 1):** RRF k 60; multiplier 2; `RERANK_CANDIDATES` 50; `RETRIEVAL_MAX_PER_DOC` 0;
`RETRIEVAL_DEMOTE_BOILERPLATE` false; `TEXT_SYMBOL_GLOSSARY_BOOST` 1.0 (the bench copies' value in round
1's step 8); `RERANK_INCLUDE_SYMBOL_GLOSSARY` true. **The multiplier and RRF k are not exposed by
`/v1/config`**: on the API route they are declared from the server's launch environment
(`--server-settings JSON`) and recorded in the output as **server-assumed** values, not read back; the
in-process route uses them directly (`--multiplier`). `--expect KEY=VALUE` overrides an expected value
and is itself recorded.

## 4. Evaluation sets

| set | holder | records / clusters | role |
|---|---|---|---|
| **docs-A / T512o0** — the part-4 question set (735 questions on 208 pages; `--part4-arm T512o0 --private-config`), retrieval-only, in-process arms as the part-4 harness builds them; the glossary reaches 1 chunk in 1,113 (lab book Part 2), so this set isolates fusion and reranker from the glossary | ours (private; alias only) | 735 / 208 pages | **family A, confirmatory**; L (live chunking) as replication (§12 D2) |
| **P: the partner's 155** on the papers-OC bench copy (14,456 chunks @512) | the partner's set, now held **privately on our host** (its user lifted the "never copy" rule for our private experiments repository; lab book addendum 2026-10-29) — never in this public repository | 155 / 7 papers (138 in one; 73 distinct queries) | exploratory; corpus-level claim is GATE-NOT-EVALUABLE (§8) |
| **R2:** round-2 multi-paper sets — O (OA-1000), A (ASM-1000), P (papers-OC) of `../../glossbench-round2-2026-11/prereg/PREREG-glossbench-round2.md` | ours | ≈ 250 records × 25 papers per set (planned) | **family B, confirmatory**; a set is admitted when its records are frozen (round 2's `accepted-<set>.jsonl`) before any §3 arm has run on it |

**Set P runs on our host, every leg through our API against the bench copy.** The partner's retrieval
gateway **clamps `top_k` to 10** (the depth read-back guard in `pools` exists because a gateway once
clamped 100 to 10 silently), so **no leg goes through it**: pools to depth 100 via the API route
(`API_MAX_TOP_K` = 100), reranked arms offline.

**Depth 200 only on docs-A, in-process.** The API route refuses D > 100. The in-process route's BM25 is a
standard-analyzer **approximation** in numpy (`section_aware_trial`: UAX#29-style tokens, Lucene BM25
parameters, no stop words) — on docs-A it is the reference the part-4 arms were built with, but on
papers-OC it is **not byte-identical** to the store's lists, so set P is capped at D = 100 on the API
route (§12 D10) and its depth-200 arms are **absent**.

The arms, measures and tests here apply to R2 unchanged; R2's own prereg names this file.

## 5. Measures (every arm × set; `stats`, `census`)

- found@k for k ∈ {5, 10, 20, 50} (`FOUND_K`); ceiling@D for D ∈ {10, 20, 50, 100, 200} (`DEPTHS`);
  ordering loss@D; the decomposition.
- Rank of the first gold chunk (median, IQR, the 11–50 share; `rank_summary`); MRR@50.
- Discordant counts gained / lost against the reference arm, per cluster.
- Reranker input length census in XLM-R tokens (`census --tokenizer BAAI/bge-reranker-v2-m3`), per
  rendering, with the count above the sidecar's `MAX_LENGTH` (declared: `--sidecar-max-length` from the
  sidecar's launch environment; `/ready` does not expose it; default 4096).
- Neighbour hits (a top-10 chunk that is `prev_chunk_id`/`next_chunk_id` of a gold chunk).
- Run-to-run repeat of A0 and A4-50 (`stats --repeat`; the noise floor; round 1's was 0–2 records). §12
  D12: once per set.
- **Depth read-back.** Every request's served depth is recorded against the requested one. A list
  shorter than requested is `under-served`; the run fails (exit 1) unless `--allow-short`, which marks the
  list GATE-NOT-EVALUABLE and every arm built on it inherits the mark. The in-process route can tell a
  corpus that has fewer matching chunks (`exhausted`, allowed) from a clamp; the API route cannot.

## 6. Analysis

**Unit and pairing.** The paired unit is the question (docs-A, R2) or record (P). Clusters: page
(docs-A), paper (P, R2; `--cluster-field`). Records sharing a query are resampled as one unit within the
paper (P).

**Test.** Sign-flip permutation over cluster sums of d ∈ {−1, 0, +1}, 10,000 flips (`--n-perm`), seed
261029 (`SEED`), exact by enumeration at ≤ 20 non-zero clusters; one-sided for H1, H2; H3 shifts each d by
+δ. p = (1 + #{≥ observed}) / (1 + flips); no mid-p in the decision (mid-p reported as sensitivity). Holm
over H1–H3 within a set, run over the hypotheses that are neither GATE-NOT-EVALUABLE nor absent (the
output names each). Exact McNemar and the cluster bootstrap (percentile, 10,000, `--ci-level 0.90`
two-sided = the one-sided 95% bound for H3) are reported, never decide. The machinery is the part-4 code
(`deployed_docs_part4`, `chunktrial_stats`), **imported, not copied**.

**Gold rule.** `--gold-rule any` (a record counts when any gold chunk is placed); `all` is reported as a
sensitivity.

**Confirmed when:** Holm-significant on family A; replicated when also Holm-significant on at least one
R2 set. A result that holds on docs-A and fails on R2 is *corpus-dependent*, stated as such.

**Multiplicity outside the family:** exploratory contrasts carry uncorrected p and a Bonferroni count
beside them; none is called a finding.

## 7. Power floor

Computed by the committed code before the first arm runs (`reranker_test.py power-table --pages 208
--questions-n 735 --delta 0.05 --alpha 0.05 --n-sims 2000 --n-flips 2000 --seed 261029 --out
power-table-reranker.json`; `--gold` takes the realised cluster layout instead) and saved beside the
results. Approximate reading before that table exists (design assumption): with discordance .15 (≈ 110
discordant questions) a two-sided exact split needs ≈ 66/44 (net 22, 3% of questions); clustering by page
costs more. docs-A's reranked arm is near ceiling (`found` 622 of 735 at k10 in round 1's step 7, lab
book Part 2), so discordance may be low; the table says what was resolvable, and an unresolved H is
reported as unresolved with its floor, never as a null. For P, the minimum attainable paper-clustered
two-sided p with 7 clusters is 2/128 = 0.016 and the dominant paper decides the sign: corpus-level
inference is not available (§8).

## 8. Exclusions and GATE-NOT-EVALUABLE

- Excluded: records with no gold chunk in the index (`build_gold` marks them `excluded`; P: 2 of 155 in
  Test 3's gold); questions whose quote fails the gold normalisation at build time (counted).
- GATE-NOT-EVALUABLE (`GNE`): a set with fewer than **8** clusters (`MIN_CLUSTERS`; P's corpus-level
  claim); an H whose discordant count is below the floor at which α is attainable; any list the depth
  read-back marked under-served, and every arm built on it; a manipulation-check mismatch (§3).
- **The identity gate.** When any reranked arm of the family (A4-50, A5-50) is the **offline** rerank
  rather than the API's own list, the offline `content+gloss` top 10 of `hybrid@50` must equal the live
  A4-50 list **and order** on 3 probe records (`identity_gate`; `--probe-ids` or 3 seeded). The reference
  is the `--live hybrid@50` capture or `--identity-reference`. **An ABSENT gate (no reference list) makes
  H1–H3 GATE-NOT-EVALUABLE on that set**, the same as a failed one (owner decision, in code: `family_gate`).
  On **docs-A** the reference is the part-4 retrieval store — `rerank --identity-reference
  part4-retrieval.jsonl --identity-arm T512o0`, part 4's own reranked lists per question — because the
  docs-A arms are in-process and have no live A4-50.
- **A wired-but-inactive reranker** answers `rerank=true` with the fused order. **A4-50 is
  GATE-NOT-EVALUABLE when its list equals A3's (the fused 50-pool's own order) on every record.** The
  round-2 runner enforces the same rule in code (`found10.py`: `base` identical to `norerank`); for this
  harness it is a stated analysis rule, checked from `stats`' per-record ranks, and `reranker_test.py` at
  `e3002ba` has no automated gate for it.
- Absent is written as *absent*: a set not yet frozen (R2), a judge not run (E7), a second reranker not
  deployed (A8), a fuse variant with a missing input, a depth the route cannot reach (P at 200).

## 9. Code and pin

**On `main` at `e3002ba` (#862):** `python/scripts/eval/reranker_test.py` with subcommands

| subcommand | what | key flags |
|---|---|---|
| `pools` | first-stage lists per leg and depth; the gold; the manipulation check; the depth read-back | `--backend api\|inprocess`, `--depths`, `--legs`, `--live`, `--base-url` (no default), `--api-key-env`, `--collection`, `--server-settings`, `--expect`, `--multiplier`, `--glossary-boost`, `--allow-short`, `--chunks DUMP.jsonl` or `--part4-arm ARM --private-config CFG`, `--questions`, `--cluster-field`, `--omit-text` (default on), `--out`, `--gold-out` |
| `rerank` | offline rerank of captured pools through the sidecar, five renderings, identity gate first | `--pools`, `--rerank-url` (no default), `--jobs` (default `hybrid@*:content+gloss,bm25@*:content+gloss,vector@50:content+gloss,hybrid@50:*`), `--cache`, `--identity-reference`, `--identity-arm`, `--probe-ids`, `--shuffle-seed 261029`, `--shuffle-unit string\|entry`, `--expect-model`, `--expect-snapshot`, `--reranker-cache`, `--reranker-snapshot`, `--sidecar-deps`, `--sidecar-max-length`, `--sidecar-batch-size`, `--inflight` (≤ 4) |
| `fuse` | A9 | `--lists`, `--variants`, `--rrf-k 60` |
| `census` | XLM-R lengths, `> MAX_LENGTH` count, neighbour hits | `--lists`, `--gold`, `--tokenizer`, `--max-length`, `--pool-keys`, `--renderings` |
| `stats` | decomposition, rank-of-gold, H1–H3 (Holm), exploratory contrasts, per-record ranks | `--lists`, `--gold`, `--set-label`, `--depths`, `--prefer live\|offline`, `--gold-rule any\|all`, `--delta 0.05`, `--alpha 0.05`, `--n-perm 10000`, `--n-boot 10000`, `--ci-level 0.90`, `--seed 261029`, `--repeat`, `--per-record` |
| `power-table` | the §7 floor | `--pages 208`, `--questions-n 735`, `--gold`, `--delta`, `--alpha`, `--n-sims`, `--n-flips`, `--seed` |

Every output embeds `experiment_provenance()`; outputs carry ids, ranks and counts only (`--omit-text`
checks the written JSON for any query or quote text, escaped forms included). Citable only from a clean
tree (`dirty: false, untracked: 0`), at one commit **at or after `e3002ba`**. Hard cap of 4 requests in
flight to any endpoint (`MAX_INFLIGHT`; a flag may lower it, never raise it).

## 10. Provenance to record in every result (`sidecar_record`)

- Reranker: `BAAI/bge-reranker-v2-m3` (`RERANKER_MODEL`), HF snapshot
  `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e` (`RERANKER_SNAPSHOT`), read from the sidecar's HF cache
  (`--reranker-cache`, `refs/main`) or declared (`--reranker-snapshot`); the sidecar's `MAX_LENGTH` and
  `RERANK_BATCH_SIZE` (defaults 4096, 32; declared from its launch environment); installed
  `sentence-transformers`, `transformers`, `torch` read from the sidecar's deps directory
  (`--sidecar-deps`, `*.dist-info`), beside the `requirements.txt` pin (`sentence-transformers>=3.0,<4`)
  and a `pin_drift` flag — the installed version on the run host is outside that pin, so the record says
  which one ran.
- Embedder: SFR-Embedding-Mistral, f32 (`--embed-model`); the API version serving the bench copy; the §3
  settings and their source (read back / declared).
- Sets: question-file sha256; the glossary condition and census stamp of the bench copy at run time (P:
  `rules/3@file/1` unless an E8 swap is planned; §12 D5); for R2, the round-2 `accepted-<set>.jsonl`
  sha256.
- No gateway call is expected. E7 would need a judge: a local endpoint (Qwen3.6) is preferred (§12 D8);
  a gateway judge would be a new dependency and is flagged, not assumed.

## 11. What was seen before writing

Round 1 in full (`../../glossbench-2026-10/LABBOOK.md`, its 2026-10-29 addenda included), the dev trial
of 2026-10-22 (Tests 1–4 in `docs/plans/symbol-glossary.md`) and the partner's six review points of
2026-10-29. The docs-A step-7 private result holds per-record ranks for the bm25, hybrid-shallow and
rerank legs; **the bm25-vs-rerank contrast has not been computed from it**, and it is not computed until
this prereg is frozen. §12 D3 settles how that file is used.

## 12. Decisions taken (owner, 2026-10-29)

The owner accepted every recommended default. Fixed at freezing; a later change is a deviation.

| # | decision | taken |
|---|---|---|
| D1 | non-inferiority margin δ for H3 | **0.05** of questions |
| D2 | primary docs-A arm | **T512o0**; L as replication |
| D3 | family A data | **both**: re-score the step-7 private result (clean `db97c28`; per-record ranks already there) for A0, A2, A4-50 as the pre-committed check, **and** a fresh retrieval-only run under the new harness, which is the citable family A; agreement between the two is a free replication |
| D4 | confirmatory family size | **H1–H3**; the depth sweep is exploratory |
| D5 | set P glossary condition for the API arms | **as the bench copy stands (`rules/3@file/1`)** now; the four conditions (E8) under round 2's swaps |
| D6 | who runs the set-P offline reranker-input arms (A7) | **our committed harness on our host**, the 155 held privately (the recommended default was "the partner runs our harness on its queries"; the lifted copy rule makes the host question moot and the identity gate keeps the rendering identical to live) |
| D7 | alternative reranker (A8) | **none in the confirmatory run**; `mixedbread-ai/mxbai-rerank-large-v1` as exploratory if the host can download it |
| D8 | judged answer-presence (E7) | **local Qwen3.6 judge, exploratory, discordant records only** |
| D9 | shuffled-control definition | **the partner's rule adopted** as the reference (string-level derangement over pool chunks); the harness's pre-registered variant and its difference stated beside every shuffled figure (§3) |
| D10 | depth-200 arms on set P | **cap at 100 on P**; 200 on docs-A in-process only |
| D11 | where the record lives | **this folder**, `docs/plans/results/reranker-test-2026-11/`; glossbench's lab book links to it |
| D12 | noise-floor repeats | **A0 and A4-50 repeated once per set** |
