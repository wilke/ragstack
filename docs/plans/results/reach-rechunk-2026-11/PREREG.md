# Pre-registration: does the adopted chunking cost reach? (fixed 512-token windows vs boundary-respecting chunks) — DRAFT v1, 2026-11-09

STATUS: DRAFT. Not frozen. The owner decided confirmatory (2026-11-09). The freeze follows the alias
study's procedure (signed tag, private sha file) after two independent reviews (§9). The owner's decisions
are in §11.

**Owner's go:** _(pending: the owner names, in writing in chat, the `main` commit to freeze, after the two
reviews of this text and of the §10 code; recorded in this directory's `README.md` at the freeze, §9.2)._

## 1. Question and hypotheses

**Question.** The project is adopting the chunking used in the confirmatory chunk-boundary test and rechunk
stage 1 (owner, 2026-11-09):
- `sentence/512/0` with `size_unit tokens`;
- `size_tolerance 0.25`, so a 512-token target with a 640-token ceiling;
- `structure_cuts sections/1`;
- `min_piece 128`.

Call it **S**. The alias study retrieved with fixed 512-token windows, `fixed_token/512/0`; call that **W**.
For the alias study's 1,091 questions: does retrieval under S reach the answer as often as under W?

**Why the earlier work cannot answer it.**
- The confirmatory test and stage 1 judged passages, or retrieved only for pairs selected from W's retrieved
  windows.
- The OA chunking evaluation (`../oa-chunking-eval-2026-10/`) compared related arms on another question set.
  Its passages sat inside a single section, so no gold quote crossed a section boundary.

**Two outcome families,** each tested on its own (§4):
- **Q (judge-free):** quote@10.
- **J (judged):** found@10 with the frozen alias judge.

**Hypotheses.** These depend on the owner's choice in §11 Q2. The draft takes non-inferiority as primary:
- **HQ:** quote@10(S) − quote@10(W) > −δ, with **δ = 3 points** (owner, 2026-11-09, from `power.json`). If
  non-inferiority holds, superiority is tested next (hierarchical, no extra α).
- **HJ:** the same, for judged found@10.

## 2. Data (fixed before any S-side outcome exists)

- **Questions.** The alias study's 1,091 accepted records (900 balanced + 191; private). The questions,
  answers and quotes are unchanged.
  - Each record carries a `quote`, about 160 characters (range 18–300), taken from its gold chunk.
  - After whitespace normalisation, the same normalisation quote@10 uses, 1,089 of the 1,091 quotes are
    verbatim in their gold chunk (`feasibility.json` `eligibility`; 1,065 without normalisation). Every
    quote was drawn from inside one W window, its gold chunk.
  - **S reachability** (operator default, §11). A record is S-reachable when
    `quote_cover(quote, every S chunk of the gold document) ≥ 0.90`, the quote@10 threshold. The check reads
    the S chunk exports only: chunk texts, not an outcome. All 1,089 W-verbatim quotes are S-reachable
    (`feasibility.json` `eligibility.s_reach_of_w_verbatim`). 1,088 lie inside one S chunk. 1 crosses an S
    chunk boundary: only the union of two S chunks reaches it.
  - **Q-eligible** means W-verbatim **and** S-reachable, so that both sides can reach the quote: **1,089
    records**, unchanged. The 2 others are excluded from Q and kept in J (§11 Q4). The count did not move,
    so `power.json` (computed on 1,089) is kept as it is. The rule fixed before the check: regenerate it
    from a clean tree only if the count moved by 1 % of the records or more.
- **W side (existing).** The alias study's committed stage-A retrieval runs, legs `<corpus>/none`, which hold
  each record's ranked chunk ids under default, norerank and BM25. The text comes from the W chunk exports.
  Nothing new is run on the W side.
- **S side (to be run).** The four bench collections of rechunk stage 1, `bench-rechunk-{base,neighbour,random1,random2}`
  (glossary none, 452,729 chunks, `../rechunk-found10-2026-11/artifacts/build-summary.json`). Run with the
  same harness settings as W: top 50 requested, top 10 read, the same three retrievers and server settings.
  The run config is `reach-config.json` (private; path and sha256 in §9.1): the alias study's run config
  with only `out_dir` changed and `rechunk: {all_records: true}` added. The report checks the settings
  against the W run summaries (§7.1, *retrieval settings*).
- **Prior S-side exposure, declared.** Rechunk stage 1 retrieved the S collections for the 218 questions of
  the confirmatory test. It computed no quote@10 and no found@10 for any of them. Those questions are re-run
  here with the rest (§11 Q5: kept).

## 3. Outcomes

- **quote@10 (Q).** A question is found when the union of its top-10 chunks covers at least 90 % of the
  quote's characters, after whitespace normalisation. This is the OA evaluation's definition, implemented once
  as `quote_cover` / `quote_hit` in `python/scripts/eval/reach_quote.py` (tested). One function scores both
  sides.
  - It is chunking-symmetric: a quote split across two retrieved chunks counts.
  - It needs no judge.
  - It credits the quote's text wherever it is retrieved. It does not check the location (gold document or
    any other) or the order of the pieces: a 20-character run of the quote that also occurs elsewhere in the
    corpus counts.
  - **Edge-piece asymmetry, declared.** Pieces shorter than `MIN_MATCH` (20 characters) are credited only at
    a chunk's edge (the split-quote rule: a quote prefix that ends a text, a quote suffix that starts one).
    W windows start and end mid-sentence, so a chance edge match (a window that happens to end with the
    quote's first few characters) is slightly more available to W than to S, whose chunks end at sentence
    boundaries. A chance edge match of k characters can flip only a list whose genuine coverage is in
    [90 % − k/n, 90 %), n being the quote's length. Such matches are a few characters to a word long. This
    is reasoned, not measured. The direction favours W, against the hypothesis.
- **judged found@10 (J).** A question is found when quote@10 holds, **or** any top-10 chunk carries an
  *answers* label from the frozen alias judge.
  - This one rule applies on both sides. It replaces the alias study's gold-window shortcut, which has no
    S counterpart.
  - Lists with no quote hit are judged on demand. Already-labelled (record, chunk) pairs are reused, and so
    are labels for S chunks whose text is byte-identical to a judged W chunk of the same question (the
    content-hash rule).
  - Remaining labels come from new judging with the frozen judge: alias-judge prompts, C/E, Argon tiebreak,
    Opus drift reference. New (record, chunk) pairs are built with `build-control --any-paper`, the stage-1b
    mode.
  - **Prior S registries** (operator default, §11). There are three, and each `final.jsonl` is one registry:
    - the rechunk confirm control registry (`alias-study-2026-11/confirm/judge-control`);
    - the cover-any registry (`alias-study-2026-11/confirm/judge-cover`);
    - the stage-1b registry (`rechunk-found10-2026-11/stage1b/judge`).

    Each registry's `pairs.jsonl` is required beside its `final.jsonl` (`--s-prior-pairs`; `j-seeds` and
    `report` refuse without it). Every reused prior key's `content_sha256` must equal the sha256 of the S
    export's text of that chunk; `j-seeds` and `report` refuse otherwise. Label conflicts are checked
    **per registry**: two judged labels of one key within one registry are a gate (§7.1). **Across
    registries** the labels combine by the stage-1 rule: *answers* if any registry says answers. The
    disagreements are counted (`registries.S_prior.cross_registry_conflicts`) but not gated. The rule's
    direction, declared: it slightly favours S keys that were judged twice, because a key labelled *answers*
    in one registry and *not* in another counts as answering.

    The integrity check has been run now on the W registry and the three prior registries, reading their
    label files only (`feasibility.json` `registry_integrity`):
    - no problem in any registry: W 24,559 rows and 24,559 keys; control 351 and 351; cover-any 174 and
      174; stage-1b 671 and 671;
    - read together, the three prior registries hold 1,121 keys; 4 of them carry different judged labels in
      two registries.
  - **Pairs J needs that are never judged count as NOT answering.** Two kinds:
    - an **orphan**: a needed S pair whose record has no stage-A pair in the alias main registry to serve
      as `control_of`, so `build-control` cannot take it (owner, earlier: no judge-harness change to allow
      stage-B `control_of`);
    - a pair the judge left **unjudged** (its final row has `source` `unjudged`), or one with no registry
      row.

    Orphans are written to the private `orphans.jsonl` and counted in `j-seeds.json` and in the report
    (`j_labels.s_orphans`, `s_unjudged_by_kind`).

    **Orphans, counted now** (`feasibility.json` `orphans`). **748 of the 1,091 records** (746 of the 1,089
    Q-eligible, in 383 papers) have no stage-A pair in the main registry: 620 hold stage-B pairs only and
    128 hold no pair at all. The main registry's stage-A pairs come from W's default and norerank lists
    without the gold window. So these are the records W finds under both retrievers: every W default and
    norerank list of an orphan record is a quote hit (0 lists without a hit). Under BM25, 177 of W's 976
    lists without a hit belong to orphan records (1,200 of 6,850 unique pairs). The consequence for J's
    unjudged gate is in §5.
  - **Favourable-bound sensitivity** (the alias boundary-confirm prereg's §6.8, carried over). The report
    recomputes J's primary test with every unjudged or orphan S pair that the primary cell needs scored
    *answers*, and prints it beside the primary (`favourable_bound`). It decides nothing. When the bound's
    outcome differs from the primary's, the J sentence says so (§7.2).
- **Judge drift check** (owner, 2026-11-09). W's labels are from 2026-11-04/06; S's will be new. At run time, a
  seeded 5 % sample of the W (record, chunk) pairs that J uses (seed `261109|reach|drift|W`; ≈ 616 of
  12,310: `round_half_up(615.5)`) is re-judged by the frozen judge, in its own registry. The report gives
  the agreement between the original and the new labels, overall and by original label. This is
  descriptive and does not enter the test. If agreement on W's *answers* labels falls below 0.90, the J
  result carries a *judge drift* caveat. Two checks on the re-judge itself, on the Qwen label rows
  (`labels-qwen-C.jsonl`, `labels-qwen-E.jsonl`) of the W judge dirs and the drift judge dir, with
  `alias_control_chunks.cache_check` (the alias boundary-confirm prereg's §6.9 rule, reused):
  - **Prompt identity.** Each re-judged pair's `prompt_sha256` and `system_sha256` against the original
    row of the same pair id. Any difference, or no compared row at all, makes the drift check *not
    evaluable* (it would compare different prompts), and the J sentence carries *judge drift check not
    evaluable*.
  - **Cache check.** A re-judged pair whose `reasoning_sample` is byte-identical to the original's, where
    both have ≥ 200 characters, flags *cache suspected* (one pair is the threshold, as in the alias
    boundary-confirm prereg). A backend with compared rows but no eligible pair reads *cache check not
    evaluable*. Either is a caveat on J.
- **Context tokens per query** (top 10, SFR tokenizer): descriptive.

## 4. Tests and families

- **Primary cell** (§11 Q1). The default retriever, pooled over the four corpus collections, with records
  clustered by paper.
- **Clusters.** Q: 408 papers holding 1,089 eligible records: 25, 85 and 298 papers with 1, 2 and 3 records.
  J: 408 papers holding all 1,091 records: 25, 83 and 300 papers with 1, 2 and 3 records
  (`feasibility.json` `papers_by_records_all`). Each record contributes one paired difference per corpus.
- **Statistic.** The paired difference per record and corpus, S − W, in {−1, 0, +1}. For non-inferiority, a
  sign-flip over papers on the null-shifted cluster sums X_k = 100·T_k + m_k·δ, with δ in points (T_k is
  the sum of the cluster's paired differences, m_k their number), one-sided, α = 0.05. The p is computed exactly by lattice convolution
  (`power.py` `sign_flip_p_exact`). Superiority, after non-inferiority, is the exact sign-flip on T_k. Each
  family (Q, J) is tested at α = 0.05 on its own.
- **Size, declared.** The randomisation p is exact, but the composite null does not make d + δ
  sign-symmetric, so the test's size is only asymptotically α.
  - Simulated size at the sizing case: 0.0545 (`power.json` `recommendation.size_at_sizing_case`). That is
    just above α + 2 Monte-Carlo SE, 0.05 + 2·√(0.05·0.95 / 10,000) ≈ 0.0544.
  - At δ = 3, across the ρ grid: at most 0.0586 at 10 % discordance and 0.0766 at 5 %
    (`power.json` `size_summary_max_over_rho`). At 2 % discordance, δ = 3 is not evaluable in the grid.
  - For comparison, δ = 2 reaches 0.138 at 2 % discordance.

  A rejection at low observed discordance is reported with this caveat.
- **Secondary, not tested.** Each corpus and each retriever separately; G vs C records; context tokens;
  discordant records broken down by coverage of the gold window under S.

## 5. Power (`power.py`, `power.json`, seeded `261109|reach|rechunk|power`, 10,000 studies per cell)

**W baseline quote@10** (`feasibility.json` `w_side` `quote10_eligible`, the committed scorer, the 1,089
Q-eligible records):

| corpus | default | norerank | BM25 |
|---|---|---|---|
| base | 94.2 % | 86.8 % | 82.3 % |
| neighbour | 87.7 % | 74.0 % | 73.7 % |
| random-1 | 90.0 % | 78.8 % | 77.2 % |
| random-2 | 89.4 % | 78.9 % | 77.1 % |

**Correlations**, from a W-only proxy (`feasibility.json` `w_miss_correlation`):
- misses of the same record correlate 0.74 across corpora;
- different records of the same paper correlate 0.005.

The sizing case therefore takes ρ_record 0.75 and ρ_paper 0. The design effect is about 3.2: the four corpora
add far less than four times the information.

**Non-inferiority power** at true Δ = 0 under the sizing case (`power.json` `recommendation`):

| discordance | δ = 1 | δ = 2 | δ = 3 |
|---|---|---|---|
| 2 % | 0.82 | 1.00 | 1.00 |
| 5 % | 0.50 | 0.95 | 1.00 |
| 10 % | 0.32 | 0.76 | **0.97** |

δ = 3 is the smallest margin with power ≥ 0.90 at 10 % discordance. At true Δ = −1 and 10 % discordance, power
is 0.75.

**Beyond the grid.** 10 % is the top of the grid, not a bound: S's discordance cannot be observed before S
exists, and at Δ = 0 it can reach about 19 % (twice the pooled W miss rate of 9.7 %, since every S-only unit
needs a W miss; `power.json` `beyond_grid.max_discordance_at_true_0` = 0.1935). One cell at 15 % discordance,
sizing correlation, simulated after the grid so that every grid number above is unchanged (`beyond_grid`):
- NI power at δ = 3: **0.88** at true Δ = 0 and **0.60** at Δ = −1 (δ = 2: 0.60 and 0.24);
- simulated size of the δ = 3 test at its boundary Δ = −3: 0.0537 (Wald 0.0539, Johnson 0.0540).

So at 15 % discordance δ = 3 falls just below the 0.90 target at Δ = 0 (0.88; Monte-Carlo SE about 0.003);
power falls as discordance rises, and a realised discordance above 10 % leaves the test weaker than the
sizing case. The report prints the observed π, and §7.2's sentence carries it.

**Exact vs normal, δ = 3** (`power.json` `validation`, the fourth cell; 500 simulated studies at the boundary
Δ = −3, sizing case): the exact sign-flip and the normal approximation the grid uses agree on 99.8 % of the NI
decisions (rejection rates 0.060 exact, 0.062 normal; largest |p_exact − p_normal| 0.0013).

**Hierarchical superiority** (sizing correlation): at true +1, power is 0.30, 0.49 and 0.86 for 10, 5 and 2 %
discordance; at true +2, it is 0.76, 0.96 and 1.00. With the base corpus at 94 %, superiority is weak unless
discordance is low.

**Sensitivity.** Excluding the 26 raw non-verbatim quotes instead (1,065 records) gives practically the same
power (`raw_exclusion_cells`).

**J judging load** (`feasibility.json`):
- **W:** needs no new judging. All 12,310 (record, chunk) pairs in its no-hit top-10 lists already carry a
  final label.
- **S:** estimated at the same miss rate, all three retrievers: about 12,300 new pairs, about 24.6k Qwen
  calls. That means about 860 / 1,480 / 2,090 Argon calls at 7 / 12 / 17 % escalation, and about 616 Opus
  calls (`feasibility.json` `s_side_estimate.calls`).
- **Drift check:** about 616 more pairs (§3).

**Orphans and J's unjudged gate (expected; declared, not a design change).** Every W default list of the
748 orphan records is a quote hit (§3). So every S default miss on one of those records is a W-only unit,
and its pairs are orphans: never judged, and unjudged for the gate. J's primary-cell gate fires above 5 % of
the pairs that cell needs. At W's level that is about 157 of about 3,149 pairs, or about 21 lists at W's
7.5 unique pairs per list without a hit. About 21 S default misses among the 2,992 J primary-cell units of
orphan records (about 0.7 %) are therefore enough to make J GATE-NOT-EVALUABLE.
- J is likely to be not evaluable, for this structural reason.
- The favourable bound (§3) shows how much the not-answering rule carries.
- Q is unaffected: it needs no judge.
- The owner's earlier decision stands (§11: orphans count as not answering, and there is no judge-harness
  change for stage-B `control_of`).

## 6. Procedure (a fixed sequence; no discretionary step)

1. **Freeze** (§9). Every later step runs in a clean checkout of the tagged commit.
2. **S retrieval** for 1,091 records × 3 retrievers × 4 corpora (13,092 calls), with the `rechunk` mode
   extended to all records (§10: `rechunk: {all_records: true}`), from the frozen `reach-config.json`
   (§9.1).
3. **`reach_report.py j-seeds`, run once.** Its outputs:
   - private `s-seed.jsonl`, `s-export.jsonl`, `drift-seed.jsonl`, `drift-export.jsonl` and `orphans.jsonl`;
   - private `j-seeds-detail.json`: the breakdowns per retriever and per corpus, the label sources and the
     prior content check;
   - public `artifacts/j-seeds.json`, holding **totals per registry only** (operator default, §11): S seeds
     and orphans; the drift sample, its seeds and orphans. It also holds the registry problems, the
     `inputs_sha256` and `outputs_sha256`, and the provenance.

   quote@10 is computed inside it, by the committed function, only to find the lists without a quote hit.
   **Declared exposure:** `j-seeds.json` holds no quote@10 or found@10 level. But its S seed total (about
   ten per S list without a quote hit, less reuse) reveals roughly how many S lists miss the quote, which
   is about Q's S level, before J is judged. Nothing that follows depends on that number: the registries
   are built from the seed files as written, and every later step is fixed here.
4. **Judge on demand for J.** Two registries by `alias_study_judge.py build-control --any-paper`, each in its
   own new `out_dir` under the main judge config with only `out_dir` and `chunk_exports` changed: the S
   registry from `s-seed.jsonl` / `s-export.jsonl`, the drift registry from `drift-seed.jsonl` /
   `drift-export.jsonl`. Then `preflight`, the gateway notice and its acknowledgement (§12), `label` per
   backend, `finalize`. **Hard stop** on any provider notice (`provider_notice`, ACCESS DENIED, a
   served-model mismatch, the budget stop): the run halts, the state is reported, and the owner's word in
   chat is required to resume. A resume under the same frozen design is recorded in `RESULTS.md`; any change
   made while stopped is a deviation (§9.3). **Without a resume** (operator default, §11), J is reported as
   it stands under the written gates. The pairs left unjudged count as unjudged, the 5 % gate of §7.1
   decides whether J is evaluable, and the decision not to resume is recorded in `RESULTS.md`.
5. **`reach_report.py report`, run once**, with `--prereg PREREG.md --prereg-sha256 <the frozen sha>`. It
   refuses unless the file hashes to the sha **and** the sha is the committed `PREREG.md`'s, so a wrong sha
   or another file stops before anything is read. It also takes `--j-seeds artifacts/j-seeds.json`, and it
   refuses unless the shas of its own inputs equal that file's `inputs_sha256`. Those inputs are the
   records, the W and S runs and summaries, the W and S exports, the W registry, the prior S registries and
   their `pairs.jsonl`, and the main pairs (operator default, §11). It computes quote@10 on both sides with one committed
   function, J with the labels of §3, the tests, the gates and the frozen wording of §7.2 → public
   `artifacts/reach-report.json`.
6. `RESULTS.md` in this directory, with the two decision sentences of §7.2 verbatim.

**Operator exposure.** Until step 5 has written `reach-report.json`, no one reads any S-side outcome: no S
quote@10 or found@10, no S label counts (`final.jsonl`, `labels-*.jsonl`, `judge-summary.json`, the per-arm
or per-registry counts in a harness `LOG`), and no per-registry count of either registry. Progress is followed
by the harness's `status` totals only. The one exception is the declared per-registry totals in
`j-seeds.json` (step 3), read to size the gateway notice. The private `j-seeds-detail.json` is not read
before step 5. Any other look, accidental or not, is reported in `RESULTS.md` with what was
seen and when. This is a rule, not a mechanism (§8).

## 7. Gates and decision rules

### 7.1 Gates (a third answer, as in the earlier preregs)

- Any retrieval leg with failed calls or a fired depth or reranker gate.
- An S run whose retrieval settings differ from the W run's beyond the declared differences.
- An S collection whose census differs from `build-summary.json`.
- Unjudged pairs above 5 % for J, in J's primary cell.
- Label-table integrity: merged tables may only add labels (the stage-1b guard).

How `python/scripts/eval/reach_report.py` reads each gate:
- **Retrieval** (both families, W and S): a run summary whose status is not `complete`; any condition with
  a non-null `gate`; any failed call, missing row or duplicate row in any (corpus, retriever) leg. Also an
  S run that is not the reach run (`study.mode` `rechunk` with `rechunk_scope` `all_records`).
- **Retrieval settings** (both families; operator default, §11): per corpus, the S run summary against the W
  run summary. Compared:
  - the condition set;
  - each condition's `request` (the collection is a placeholder) and its `requested` depth (50);
  - `found_k`, `server_assumed`, `study.retrievers`, `study.request_order` and
    `study.record_set.accepted_sha256`;
  - the server config hash: S's `study.config_sha256_start` equals W's (`2e91056f…` in all four W
    summaries), and the hash is unchanged within each run.

  A field missing on either side counts as a difference. Declared expected differences, not compared:
  - the collection names and indexes;
  - the census and the collection build;
  - the run mode and its stamps (`study.mode`, `rechunk_scope`, `stage`, `legs`, `exploratory`);
  - the prereg block, the provenance and the clock;
  - the server version (`/v1/version`), printed beside the comparison.
- **Census** (both families): per S collection, the summary's live census before and after (total and
  stamps), the collection name, the load receipt's chunk count and sha256, all equal to
  `build-summary.json`.
- **Inputs** (both families): a top-10 chunk with no text in its side's exports; a W gold chunk missing; a
  record count other than 1,091; a Q-eligible count (W-verbatim and S-reachable, §2) other than 1,089. Both
  counts are fixed in the code, with no command-line override.
- **Unjudged** (J only; owner default, 2026-11-09): on either side, more than 5 % of the unique
  (record, chunk) pairs that J's **primary cell** needs (the top 10 of every default-retriever list without
  a quote hit, four corpora) carry no *answers* / *not* label. Orphans and pairs with no registry row count
  as unjudged. For the norerank and BM25 J figures (secondary), a shortfall above 5 % on either side is a
  caveat printed on that figure, not a gate. The unjudged counts by retriever and side are printed.
- **Label integrity** (J only): a registry row without ids or with a `pair_id` that is not
  sha256(record|chunk)[:16]; one (record, chunk) with two different judged labels **within one registry**
  (each `final.jsonl` is one registry; disagreements across registries are combined answers-if-any and
  counted, not gated, §3); a new S label for a key that already had a judged label (prior registry or
  content-hash rule); no S registry.

The drift check (§3) and the favourable bound (§3) are not gates.

### 7.2 Decision rules and the wording of each outcome

Each family (Q, J) is decided on its own, in its primary cell (§4). Let n be the paired (record, corpus)
units, r the records and k the papers behind them; S% and W% the two rates; D = S% − W% in points; b and
c the units found under S only and under W only; π = (b + c) / n the observed discordance; p_NI the exact
one-sided sign-flip p over papers on X_k = 100 T_k + m_k δ with δ = 3; p_sup the exact sign-flip p on
T_k, computed only when non-inferiority holds. α = 0.05 one-sided, per family.

| outcome | rule | the sentence (filled from the report) |
|---|---|---|
| **Superior** | gates pass, p_NI ≤ 0.05 and p_sup ≤ 0.05 | "Superior (F): under the default retriever pooled over the four corpus collections, <F's outcome> was S% under S against W% under W on n paired (record, corpus) units (r records in k papers), a difference of D points (b units found under S only, c under W only; discordance π). Non-inferiority at δ = 3 points holds (exact one-sided sign-flip over papers, p = p_NI), and S is superior to W (p = p_sup; hierarchical, no extra α)." |
| **Non-inferior** | gates pass, p_NI ≤ 0.05, p_sup > 0.05 | "Non-inferior (F): <the same counts>. Non-inferiority at δ = 3 points holds (exact one-sided sign-flip over papers, p = p_NI): S is not worse than W by 3 points or more. Superiority is not shown (p = p_sup)." |
| **Non-inferiority not shown** | gates pass, p_NI > 0.05 | "Non-inferiority not shown (F): <the same counts>. The exact one-sided sign-flip over papers gives p = p_NI > 0.05: a loss of 3 points or more under S is not ruled out by this test (this is not evidence that S is worse)." *Never* "S is inferior", *never* "S loses reach". |
| **GATE-NOT-EVALUABLE** | any gate of §7.1 that applies to the family fails | "GATE-NOT-EVALUABLE (F): <each gate, with its count>. The test was computed (NI p = p_NI, n = n units) and decides nothing." The counts are still printed in full. |

<F's outcome> is "quote@10 (the top 10 cover at least 90 % of the gold quote's characters)" for Q and
"judged found@10 (quote@10, or a top-10 chunk the frozen alias judge labelled answers)" for J.

**The frozen form** (operator default, §11) is this table's text, Greek letters included (δ, α). `decide()`
in `reach_report.py` prints δ and α in Unicode, so the report's sentences match it character for character
outside the filled values; π is filled as a percentage. `RESULTS.md` carries this §7.2 text verbatim, beside
the two filled sentences as the report printed them.

Caveats appended to the sentence ("Caveats: …"):
- **Size** (either family): when non-inferiority holds and π < 10 %, "low observed discordance (π): the
  test's size is only asymptotically α", with the simulated size of the δ = 3 test from `power.json`
  `size_cells` (0.0545 at 10 % and 0.0615 at 5 % under the measured correlations, up to 0.0766 over the
  grid at 5 %; not simulated below 5 %, since δ = 3 needs a discordance of at least 3 %).
- **J, always:** W's labels come from the 2026-11-04/06 judge runs and S's from new judging, so any judge
  drift lands on S only; and J includes the 2 records whose quote is not verbatim in its gold chunk.
- **J, judge drift:** when the drift check's agreement on W's *answers* labels is below 0.90, or cannot be
  computed (no drift registry, no original *answers* label in the re-judged sample, no label rows to
  compare, or a re-judge prompt that differs from the original: *judge drift check not evaluable*); and
  *cache suspected* or *cache check not evaluable* from the cache check (§3).

**The favourable bound in the J sentence.** When the favourable bound of §3 (every unjudged or orphan S pair
of the primary cell scored *answers*) gives another outcome than the test, the J sentence adds, before the
caveats: "Favourable bound: with the N S pair(s) J needs that carry no judged label (unjudged or orphan)
scored answers, the outcome would be <outcome> (NI p = …); this decision scores them not answering and rests
on that rule." It is added under any outcome, the gate sentence included; it never changes the outcome.

The report also writes, for each family, the secondary figures of §4 (not tested), the gates that fired,
and the outcome name (`superior`, `non_inferior`, `ni_not_shown`, `gate_not_evaluable`).

## 8. Threats to validity

- quote@10 credits the quote's text wherever it is retrieved, not only at the gold location, and the
  edge-piece rule gives W a slight chance-match advantage (§3). J credits answers anywhere but inherits
  single-passage judging, which the confirmatory test showed penalises answers split across S chunks.
- Unjudged and orphan S pairs count as not answering, against S; the favourable bound (§3) shows how much
  that rule carries. 748 records are orphans and W finds every one of them under the default retriever, so
  each S default miss on them is an orphan W-only unit. J's unjudged gate is therefore likely to fire
  (§5).
- Every eligible quote was drawn from inside one W window, so W never needs two chunks to reach it. S
  splits 1 of the 1,089 quotes across a chunk boundary (§2). quote@10 credits that quote under S only when
  both pieces are in the top 10. The sampling favours W, slightly, against the hypothesis.
- Operator exposure: `j-seeds.json` reveals about Q's S level before J is judged (§6 step 3), and harness
  logs show per-registry counts before the report exists. §6 forbids reading them and requires any look to
  be reported; this is a rule, not a mechanism.
- The W runs date from 2026-11-04 and the S runs will be later. The same server settings are required by
  the retrieval-settings gate (§7.1). The tools image and API versions are recorded, and the server
  version is printed beside the comparison.
- J asymmetry: W's labels come from the 2026-11-04/06 judge runs and S's from new judging, so any judge drift
  lands on S only. The content-hash reuse rule will rarely apply, because S and W chunk texts are seldom
  identical. The drift check (§3) measures this.
- 19 documents chunk differently in the image than in the host export (rechunk DEVIATIONS D-5). The S side
  uses the collections as built.

## 9. Freeze and deviations

### 9.1 The freeze set (frozen before any S-side outcome exists)

At **one `main` commit**, named by the owner's go:
- this text, `PREREG.md`;
- `power.py` and `power.json`, `feasibility.py` and `feasibility.json`, each JSON produced from a clean tree
  at the commit its provenance names (`dirty` false);
- `python/scripts/eval/reach_quote.py` (the scorer: `MIN_MATCH` 20, `HIT_THRESHOLD` 0.90; `TOP_K` 10),
  `python/scripts/eval/reach_report.py` (`j-seeds`, `report`), `python/scripts/eval/alias_study_run.py` (the
  `rechunk: {all_records: true}` mode), `python/scripts/eval/rechunk_prep.py`;
- the judge harness as the alias study froze it (`alias_study_judge.py`, `glossbench_alias_judge.py`;
  unchanged) and its main config, byte for byte but for `out_dir` and `chunk_exports`;
- the S collections as built (`../rechunk-found10-2026-11/artifacts/build-summary.json`);
- the S run config and every input file of `j-seeds` and `report` that exists before §6 step 2, by sha256
  (operator default, §11; the tag message carries them, §9.2). `report` refuses unless its inputs are the
  ones `j-seeds` read (§6 step 5).

| input | file | sha256 |
|---|---|---|
| S run config | `/rag/tmp/rechunk-found10-2026-11/ops/reach-config.json` | `7ed883dcef78f9b88ff05274772c909e94b0a711fac8ae2766fd4dff68872ddf` |
| records (`--records`) | `/rag/tmp/alias-study-2026-11/out/main/accepted-alias-O.jsonl` | `09fc5ca434f50c5d0e527f2424dd31a2d59e019f59762c7f961dd7d7804ad24f` |
| W run, base (`--w-runs`) | `/rag/tmp/alias-study-2026-11/main/runs/base/none.jsonl` | `6826ededf081ab9609723b24e346b2def66a38ef7d4dd8f78892f012372720ed` |
| | `…/base/none.summary.json` | `20936e99d9b0fb964de44d06b133fc20349b3c57bdd6c1cf90540935b6ab2fa7` |
| W run, neighbour | `…/runs/neighbour/none.jsonl` | `4ee00164514daa3e8901c33f08fd7195efc6ac00d9f32655fba56c7816fd7882` |
| | `…/neighbour/none.summary.json` | `7295a67826e178dd2cd75f15c1f2b35aa41b93efeeae7eb3a7a22c26903af40c` |
| W run, random1 | `…/runs/random1/none.jsonl` | `4e9b48a5d265a6d2a105a60e6fe4082434778861a72cc138eca8a5219dc40c6e` |
| | `…/random1/none.summary.json` | `7d30412f716f79989ef1a9c28dac2a8bdaf8ba223a8631f87cfe96ea459e703b` |
| W run, random2 | `…/runs/random2/none.jsonl` | `ef31c5aa7f86bda180567018d3a4f03fc27cdf2cba2593b0bbb7e4824850bc87` |
| | `…/random2/none.summary.json` | `302ffb23ff35bbdb808dfe29f365f7d5451fbd15879a5217dedb0d4924c531db` |
| W export, base (`--w-export`) | `/rag/tmp/glossbench-round2-2026-11/exports/chunks-oa.jsonl` | `ff16e611a28c48022a41d9de162b7aeabaa0e0c9b95d7ad2516fe515dc37cf8d` |
| W export, neighbour | `/rag/tmp/alias-study-2026-11/corpus/chunks-neighbour-v4.jsonl` | `0050a538c9fdaf6691835b7c0f21436465def270f7a23c3b6fa7d84f35a78b9f` |
| W export, random1 | `…/corpus/chunks-random-1-v4.jsonl` | `2defc4839017cb7ab70ee2470d97cd9ebf23847605a47975cfe7e2a48cc098a1` |
| W export, random2 | `…/corpus/chunks-random-2-v4.jsonl` | `f6b012e5f0f9e6be56de68c40f7ba5556a70d622bb26dc0e1bc255e090aa725c` |
| S export, base (`--s-export`) | `/rag/tmp/rechunk-found10-2026-11/exports/base.chunks.jsonl` | `027653dba6e570567e0a846cb31ee2442a35620549c3bf23aa149059cb908a58` |
| S export, neighbour | `…/exports/neighbour.image.chunks.jsonl` | `80410911040188a819227b1fff4064ba5326a56ccabafe501c193d96101e4687` |
| S export, random1 | `…/exports/random1.image.chunks.jsonl` | `ca34eadc3695b3a303b4b3b674269875887aa5954839ed71031c2c12428b5026` |
| S export, random2 | `…/exports/random2.image.chunks.jsonl` | `d9ee41225442b290af3c482bfd870a6c80d6591cadd51b3ef69a5a6e9ce2099c` |
| W registry (`--w-final`) | `/rag/tmp/alias-study-2026-11/main/judge/final.jsonl` | `4e36010ccfacd389141508b6decf5d57a786902f19d26a3a29d1fed43515eecb` |
| main pairs (`--main-pairs`) | `/rag/tmp/alias-study-2026-11/main/judge/pairs.jsonl` | `04d39e3ce8299d397f37092dd4537c97d995f18bfb91a7c22c9a92aea987c378` |
| prior S, control (`--s-prior-final`) | `/rag/tmp/alias-study-2026-11/confirm/judge-control/final.jsonl` | `f0c6c7e6805c94a7be0c90c5dd69d96d4b03818273633c31bedd9b82e4065f99` |
| (`--s-prior-pairs`) | `…/confirm/judge-control/pairs.jsonl` | `6a2608200aa64c1f5e3a6b5ee90d4763c0e57e59de1698f47f435f955ec0f604` |
| prior S, cover-any | `/rag/tmp/alias-study-2026-11/confirm/judge-cover/final.jsonl` | `956fb547a5546e9d454a7c0d92cd7804f3dc11db55d654eb4e3e5fa3eb14fe4e` |
| | `…/confirm/judge-cover/pairs.jsonl` | `f802cfd7106fff265466fa6c4a444aff34530b81a0c48d7669e876fae38df531` |
| prior S, stage-1b | `/rag/tmp/rechunk-found10-2026-11/stage1b/judge/final.jsonl` | `af15725a25844225a9f0f5040637969afac094d4aa04252348478ac9b494987a` |
| | `…/stage1b/judge/pairs.jsonl` | `aee3d117cf592a0087449f615eca9686f71d47c1f126ac44303d5acc03ecd2e6` |
| build summary (`--build-summary`) | `../rechunk-found10-2026-11/artifacts/build-summary.json` (in the repository) | `a7fbaa0385a13bb2074cfbb0ae17adf6c52dfebd39566b40f0ae88f113145256` |

The inputs made after the freeze (the S runs, `j-seeds.json`, the S and drift registries) are pinned by the
shas `j-seeds.json` and the report record.

**Sequencing.** The §10 code is in the same PR as this text. The freeze comes after that PR has its two
independent reviews and is merged to `main`; the tag of §9.2 points at the merge commit on `main` (or a
later `main` commit the owner names), so the frozen text and the code it names are pinned together. Nothing
in §6 runs before that tag exists.

### 9.2 Freeze procedure (the alias boundary-confirm prereg's)

1. The owner says go, in writing, naming the commit (the line at the top of this file is filled in the
   README, not here: this text is frozen as written).
2. In a clean checkout of that commit, `sha256sum PREREG.md` and a UTC timestamp go into a private
   `PREREG.sha256` kept with the outputs, before step 2 of §6.
3. A **signed annotated tag `prereg-reach-rechunk-2026-11`** at that commit. Its message holds this file's
   sha256, the timestamp, the sha256 of `power.json` and `feasibility.json`, and every sha256 of the §9.1
   input table (`reach-config.json`, the inputs and `build-summary.json`). The tag is pushed to the public
   repository before §6 step 2. `git tag -v` verifies it; the tag is never moved.
4. One commit records the freeze (the sha, the tag, the commit, the owner's go) in this directory's
   `README.md` only. `PREREG.md` is never touched again.
5. `report` takes `--prereg PREREG.md --prereg-sha256 <sha>` (both required) and refuses on a mismatch, or
   when the sha is not the committed `PREREG.md`'s, so the public report carries the frozen sha.

### 9.3 Deviations

After the freeze, any change is a dated entry in `DEVIATIONS.md` in this directory, stating what changed,
why, when, what had been seen at that time and which result it touches; a result whose computation depends
on it says so in its sentence. What counts, at least: δ (3 points), α (0.05 one-sided), the primary cell
(default retriever, four corpora, clusters by paper), the families and the test, the scorer constants
(`MIN_MATCH`, `HIT_THRESHOLD`, `TOP_K`) and the eligibility rule, the seeds (`261109|reach|drift|W`,
`261109|reach|rechunk|power`), the label rules (prior reuse, content hash, orphans and unjudged as not
answering, the favourable bound), the judge configuration, the collections and the retriever settings, the
gates and their thresholds (5 % unjudged, 0.90 drift agreement), the step order of §6, and the wording of
§7.2. A resume after a hard stop under the same design is recorded in `RESULTS.md`, not a deviation; any
change made while stopped is one.

## 10. Code needed (small)

- `rechunk` mode: run all accepted records, not only the label table's. This needs a new option, so that
  stage 1's behaviour is unchanged.
- `quote_cover(quote, chunk_texts)`: committed, tested, and the same function for W and S.
- A J on-demand builder: the lists without a quote hit, the reuse of existing labels by key and content hash,
  and the new pairs for `build-control --any-paper`.
- The report: the paired sign-flip over papers, the families, the gates and the decision wording.

Implemented (2026-11-09): the option is `rechunk: {all_records: true}` in `alias_study_run.py` (presence:
`rechunk_prep.check_rechunk_export`); the builder is `reach_report.py j-seeds` (S seeds, the drift seeds,
their export rows, the orphans) and the report is `reach_report.py report` (§7). The analysis's p is
`power.py`'s `sign_flip_p_exact`, imported from the committed file. After the first review:
- `report` requires `--prereg` / `--prereg-sha256` and the committed-file check (§9.2), and `--main-pairs`
  (orphans);
- the unjudged gate reads the primary cell, with caveats on the secondary retrievers (§7.1);
- the favourable bound and its sentence (§3, §7.2);
- the drift check's prompt identity and cache check (§3), reading the Qwen label rows of the judge dirs that
  hold the `--w-final` and `--drift-final` files (a drift registry sharing a W judge dir is refused);
- `--s-prior-pairs`: the prior registries' `content_sha256` checked against the S export (§3).

After the second review (operator defaults, §11):
- label conflicts are checked per registry (each `final.jsonl`), and registries combine answers-if-any, the
  disagreements counted (`read_registry`; §3, §7.1);
- `--s-prior-pairs` is required with `--s-prior-final`, from the same judge dirs;
- `report` requires `--j-seeds` and refuses unless its input shas equal `j-seeds.json`'s `inputs_sha256`;
- the `--expected-records` / `--expected-eligible` overrides are removed;
- Q eligibility requires S reachability (`s_reach`, `eligible_records`; §2);
- the retrieval-settings gate (`settings_gates`; §7.1);
- `j-seeds.json` carries totals per registry only, and the breakdowns go to the private
  `j-seeds-detail.json` (§6 step 3);
- the sentences print δ and α (§7.2);
- `feasibility.py` reads the S exports (reachability), the main pairs (orphans) and the prior S registries
  (integrity); run with the ragstack environment's Python.

## 11. Owner decisions (2026-11-09)

| question | owner's answer |
|---|---|
| Q1: primary cell | **default retriever, pooled over the 4 corpora**, records clustered by paper |
| Q2: hypothesis | **non-inferiority, then superiority** (hierarchical, no extra α); **δ = 3 points** (owner, 2026-11-09, after the power simulation: δ = 2 had power 0.76 at 10 % discordance) |
| Q3: J judging scope | **all three retrievers** (J's primary is still the default retriever), **plus a W re-judge drift check** on a seeded 5 % sample (owner, 2026-11-09, after the load estimate: about 12,300 new S pairs) |
| Q4: non-verbatim quotes | restated by the owner (2026-11-09): **only the 2 that fail after whitespace normalisation are excluded from Q**, and they are kept in J; 1,089 records in Q |
| Q5: 218 records retrieved on S in stage 1 | **kept** (no S-side outcome was ever computed for them; declared in §2) |
| unjudged gate (after the first review) | **owner default**: gate on the primary cell's pairs at 5 %; norerank / BM25 shortfalls are caveats on those secondary figures (§7.1) |
| orphans (after the first review) | count as not answering, with the favourable bound printed; **no judge-harness change** for stage-B `control_of` (owner, declined earlier) |
| prior S registries (after the second review) | **operator default**: three registries, named in §3 (rechunk confirm control, cover-any, stage-1b). Label conflicts are checked per registry and gate only within one registry. Across registries the stage-1 rule applies, *answers* if any registry says answers; it slightly favours S keys judged twice. The integrity check has been run now (§3: no problem; 4 cross-registry disagreements) |
| inputs pinned (after the second review) | **operator default**: every pre-freeze input listed with its sha256 (§9.1) and carried in the tag message (§9.2). `report` refuses unless its input shas equal `j-seeds.json`'s. `--s-prior-pairs` is required with `--s-prior-final`. The `--expected-records` / `--expected-eligible` overrides are removed |
| S reachability (after the second review) | **operator default**: Q-eligible = W-verbatim **and** `quote_cover` over every S chunk of the gold document ≥ 0.90 (§2). Result: 1,089, unchanged (1 split quote). `power.json` is kept: the rule was to regenerate it only if the count moved by ≥ 1 % of the records |
| retrieval settings (after the second review) | **operator default**: the S run config `reach-config.json` is in the freeze set (§9.1). `report` gates on unexpected differences between the S and W run summaries and declares the expected ones (§7.1) |
| orphans counted (after the second review) | **operator default**: 748 orphan records, counted before the run (§3). The consequence for J's unjudged gate is declared (§5); the owner's orphan decision above is unchanged |
| exposure (after the second review) | **operator default**: the public `j-seeds.json` carries totals per registry only, and the breakdowns are private (§6 step 3). Without a resume after a hard stop, J is reported as it stands under the written gates and the decision is recorded (§6 step 4) |
| wording (after the second review) | **operator default**: the frozen form is the §7.2 text with its Greek letters; `RESULTS.md` carries §7.2 verbatim; the report prints δ and α in Unicode |
| corrections (after the second review) | **operator default**: §4's formula is X_k = 100·T_k + m_k·δ (δ in points). §5's W baseline uses the eligible rates (random-1 BM25 77.2, random-2 default 89.4, random-2 BM25 77.1), and all 12 cells were rechecked against `quote10_eligible`. §4 notes that 0.0545 is just above α + 2 MC SE |

## 12. Cost (planning figures)

- **Retrieval:** 13,092 calls = 1,091 records × 3 retrievers × 4 corpora (top 50 requested), on the bench
  collections of the non-production environment; no collection is built or changed.
- **Qwen** (local, 4 in flight; C 1.85 s and E 0.74 s per pair, the rates of the alias boundary-confirm
  prereg §12): about 12,310 S pairs (`feasibility.json` `s_side_estimate`) + about 616 drift pairs ≈ 12,926
  pairs × 2.59 s ≈ **9.3 h** of judging (24.6k + 1.2k calls). The content-hash rule and prior registries can
  only lower this.
- **Gateway, Argon** (no cap, R30): about 860 / 1,480 / 2,090 calls for the S registry at 7 / 12 / 17 %
  escalation, plus about 43 / 74 / 105 for the drift registry.
- **Gateway, Opus** (the 5 % drift reference): about 616 for the S registry plus about 31 for the drift
  registry. Capped at **7 %** of each registry (`caps.opus_share` 0.07: about 862 and 43), and the harness's
  `limits` call cap and budget stop (exit 76, PAUSE) of the judge config apply.
- **Notices:** one gateway notice to the gateway coordinator (OpenChia), naming both registries' expected
  call counts per backend, before the first gateway call; no gateway call before its acknowledgement in
  chat (`label` refuses without `--notice-acknowledged`); if no acknowledgement arrives within about
  5 minutes, hold and ask the owner. **Hard stop** on any provider notice (§6 step 4); the owner's word in
  chat is required to resume, and the resume is recorded.
- **Owner time:** the go, the acknowledgements, any resume.

## 13. What is released

**Private, never in the repository:** the S retrieval runs, the seed and export files of `j-seeds`
(`s-seed.jsonl`, `s-export.jsonl`, `drift-seed.jsonl`, `drift-export.jsonl`, `orphans.jsonl`) and its
breakdowns (`j-seeds-detail.json`), both registries and every label, call and preflight file, the private
configs (`reach-config.json` among them, pinned by sha256 in §9.1), and `PREREG.sha256`.
**Public, in this directory:** this file; `README.md`; `power.py`, `power.json`, `feasibility.py`,
`feasibility.json`; after §6 step 3 `artifacts/j-seeds.json` (totals per registry only); after step 5
`artifacts/reach-report.json`;
`RESULTS.md`; `DEVIATIONS.md` if any entry exists. Every public JSON holds counts, rates, p, model ids,
version strings, commit shas and sha256 hashes only; `report` and `j-seeds` refuse a public file holding a
record, paper or chunk id or a question or quote text (`alias_control_chunks.write_public` /
`write_summary`).
