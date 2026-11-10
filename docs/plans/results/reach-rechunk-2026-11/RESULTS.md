# Results: reach comparison, fixed 512-token windows (W) vs the adopted chunking (S) (2026-11-10)

**Outcome (Q, the one confirmatory family): Superior.** No gate on Q fired (`gates.Q` is empty). J is
descriptive and has no outcome. Pre-registration: [`PREREG.md`](PREREG.md), frozen at `f78af6ee` (sha256
`1e3791ed…4458`, signed tag `prereg-reach-rechunk-2026-11`; freeze record in [`README.md`](README.md)).

Every number below is read from the committed [`artifacts/reach-report.json`](artifacts/reach-report.json); the
field is named in backticks after the number. Numbers from [`artifacts/j-seeds.json`](artifacts/j-seeds.json) are
marked `j-seeds:`. Operator times marked † come from the private run log and are in no committed artifact. The
gateway figures are in no committed artifact either; they are in [`DEVIATIONS.md`](DEVIATIONS.md) D-1, with their
source. Deviations: D-1 (the gateway) and D-2 (procedural: the first report attempt).

## Provenance

| step (PREREG §6) | UTC | artifact | commit |
|---|---|---|---|
| 1 freeze | 2026-11-10 17:38:07 | the freeze record in `README.md` (#994); private `PREREG.sha256` | tag at `f78af6ee` |
| 2 S retrieval, 13,092 calls | 17:40:47 – 17:48:40† | private S runs, pinned by sha256 in `inputs_sha256.s_runs` | `f78af6ee`, clean checkout of the tag |
| 3 `j-seeds`, once | 17:50:31 (`j-seeds: provenance.recorded_at`) | `artifacts/j-seeds.json` | `f78af6ee` |
| 4 judging: S registry (4 batches), drift registry (1 batch) | S registry built 18:11†, drift registry built 19:22†; gateway share 20:18:29 – 23:36:11† (D-1) | private registries, pinned in `inputs_sha256.s_final`, `inputs_sha256.drift_final` | — |
| 5 `report`, computed once (attempt 2; attempt 1 stopped before computing, D-2) | 23:38:55 (`provenance.recorded_at`) | `artifacts/reach-report.json` | `f78af6ee` |
| 6 this file | — | — | this commit |

- **Code state.** Both artifacts record commit `f78af6ee46e5d7f3604c10b1d14a77a7caa47b7d` (`provenance.commit`),
  `dirty: false`, `untracked: 0`, `citable: true`, `in_image: false`, Python 3.12.13; the report also records
  `confirmatory: true` (`confirmatory`).
- **Frozen code.** `code_sha256` equals the sha256 of the file at `f78af6ee` for all three: `reach_report.py`
  `a481a43f…`, `reach_quote.py` `8f9e7e5d…`, `power.py` `7289721c…` (checked against `git show f78af6ee:<file>`
  when this file was written).
- **Environments.** `j-seeds` and `report` ran in different Python environments at the same commit: nltk
  3.10.4 and 3.9.4 (`provenance.segmentation[0].nltk` in each file), installed distribution `0.1.0` and none. The
  segmentation fingerprint is the same in both (`provenance.segmentation[0].sha256` `adcd9cfe…`).
- **PREREG.** `prereg.verified` true, `prereg.sha256` `1e3791ede2b7252cf43b940fd24f99a40a4126cabfc3a698f9f3a3ba3ade4458`.

## The decision sentence (PREREG §7.2)

**The frozen form** (PREREG §7.2, verbatim), for the outcome that applied:

```text
"Superior (F): under the default retriever pooled over the four corpus collections, <F's outcome> was S% under S against W% under W on n paired (record, corpus) units (r records in k papers), a difference of D points (b units found under S only, c under W only; discordance π). Non-inferiority at δ = 3 points holds (exact one-sided sign-flip over papers, p = p_NI), and S is superior to W (p = p_sup; hierarchical, no extra α)."

<F's outcome> is "quote@10 (the top 10 cover at least 90 % of the gold quote's characters)": F is Q, the
one confirmatory family.
```

**Q's decision sentence, as the report printed it** (`families.Q.sentence`, verbatim):

> Superior (Q): under the default retriever pooled over the four corpus collections, quote@10 (the top 10 cover at least 90 % of the gold quote's characters) was 91.5 % under S against 90.3 % under W on 4356 paired (record, corpus) units (1089 records in 408 papers), a difference of +1.1 points (112 units found under S only, 62 under W only; discordance 4.0 %). Non-inferiority at δ = 3 points holds (exact one-sided sign-flip over papers, p = 0.0), and S is superior to W (p = 0.007889; hierarchical, no extra α). Caveats: low observed discordance (4.0 %): the test's size is only asymptotically α (simulated size at δ = 3: 0.0545 at 10 % and 0.0615 at 5 % discordance under the measured correlations (up to 0.0766 over the grid at 5 %); not simulated below 5 %).

**J's descriptive sentence, the frozen form** (PREREG §7.2, verbatim):

```text
"Descriptive (J, not tested): under the default retriever pooled over the four corpus collections, judged found@10 (quote@10, or a top-10 chunk the frozen alias judge labelled answers) was S% under S against W% under W on n paired (record, corpus) units (r records in k papers), a difference of D points (b units found under S only, c under W only; discordance π). Of the pairs this cell needs, u_S of N_S on S and u_W of N_W on W carry no judged label (S: o orphan(s)). Favourable bound: with those u_S S pair(s) scored answers, J would be S_b% under S, a difference of D_b points. No test was computed for J; this sentence decides nothing."
```

**J's descriptive sentence, as the report printed it** (`descriptive.J.sentence`, verbatim):

> Descriptive (J, not tested): under the default retriever pooled over the four corpus collections, judged found@10 (quote@10, or a top-10 chunk the frozen alias judge labelled answers) was 98.1 % under S against 98.0 % under W on 4364 paired (record, corpus) units (1091 records in 408 papers), a difference of +0.1 points (42 units found under S only, 38 under W only; discordance 1.8 %). Of the pairs this cell needs, 71 of 2758 on S and 0 of 3149 on W carry no judged label (S: 71 orphan(s)). Favourable bound: with those 71 S pair(s) scored answers, J would be 98.3 % under S, a difference of +0.3 points. No test was computed for J; this sentence decides nothing. Caveats: W labels are from the 2026-11-04/06 judge runs and S labels from new judging; any judge drift lands on S only; J includes the 2 record(s) that are not Q-eligible (§2).

Both printed sentences equal their frozen form character for character outside the filled values; each ends
with the caveats the report appended (§7.2, "Caveats: …"). Neither sentence has been edited here.

**Precision of the printed p.** The report rounds both p values to 6 decimals (`round(p_ni, 6)`,
`reach_report.py` at `f78af6ee`). The printed NI p of 0.0 (`families.Q.test.ni.p`) therefore means p < 5 × 10⁻⁷;
the report holds no unrounded value.

**What one point is here.** The denominator is the 4,356 paired (record, corpus) units of the primary cell
(`families.Q.counts.units`), so one point is about 43.6 units. quote@10 holds on 3,935 units under W
(`families.Q.counts.W.k`, 90.3 %) and on 3,985 under S (`families.Q.counts.S.k`, 91.5 %). The difference
D = (b − c) / n = (112 − 62) / 4,356 = 1.148 points (`families.Q.counts.D_pts`).

## Q: the primary table (`families.Q.counts`, `families.Q.test`)

Default retriever, pooled over the four corpus collections, clustered by paper (`primary_cell`).

| | W: quote@10 holds | W: quote@10 fails |
|---|---|---|
| **S: quote@10 holds** | 3,873 (both) | **112** (b, S only; `S_only`) |
| **S: quote@10 fails** | **62** (c, W only; `W_only`) | 309 (neither) |

The *both* and *neither* cells are derived: both = `S.k` − `S_only`; neither = `units` − both − b − c.

| quantity | value | field |
|---|---|---|
| paired units / records / papers | 4,356 / 1,089 / 408 | `families.Q.counts.units`, `.records`, `.papers` |
| S quote@10 | 3,985 / 4,356 = 91.48 % | `families.Q.counts.S` (rate 0.9148) |
| W quote@10 | 3,935 / 4,356 = 90.34 % | `families.Q.counts.W` (rate 0.9034) |
| D = S − W | +1.148 points | `families.Q.counts.D_pts` |
| discordance π = (b + c) / n | 3.99 % | `families.Q.counts.discordance` (0.0399) |
| δ / α / clusters | 3 points / 0.05 one-sided / 408 papers | `families.Q.test.delta_pts`, `.alpha_one_sided`, `.clusters` |
| non-inferiority: exact one-sided sign-flip over papers on X_k = 100 T_k + m_k δ | p = 0.0 as printed (< 5 × 10⁻⁷), reject | `families.Q.test.ni` |
| superiority (hierarchical, no extra α): exact sign-flip on T_k | p = 0.007889, reject | `families.Q.test.superiority` |
| outcome | `superior` | `families.Q.outcome` |
| caveat (size) | low observed discordance (4.0 %): the test's size is only asymptotically α | `families.Q.caveats` |

## Q: secondary figures (not tested; `families.Q.secondary`, `tested: false`)

**By retriever, pooled over the four corpora** (`families.Q.secondary.by_retriever_pooled`; 4,356 units each):

| retriever | S | W | D (points) | S only | W only | discordance |
|---|---|---|---|---|---|---|
| default (the primary cell) | 91.48 % | 90.34 % | +1.148 | 112 | 62 | 3.99 % |
| norerank | 81.89 % | 79.61 % | +2.273 | 268 | 169 | 10.03 % |
| BM25 | 79.71 % | 77.59 % | +2.112 | 220 | 128 | 7.99 % |

**By corpus, default retriever** (`families.Q.secondary.by_corpus_default`; 1,089 units each):

| corpus | S | W | D (points) | S only | W only | discordance |
|---|---|---|---|---|---|---|
| base | 94.86 % | 94.21 % | +0.643 | 17 | 10 | 2.48 % |
| neighbour | 89.44 % | 87.70 % | +1.745 | 36 | 17 | 4.87 % |
| random-1 | 90.82 % | 89.99 % | +0.826 | 25 | 16 | 3.76 % |
| random-2 | 90.82 % | 89.44 % | +1.377 | 34 | 19 | 4.87 % |

**By question kind, primary cell** (`families.Q.secondary.by_kind_primary`):

| kind | units | records / papers | S | W | D (points) | S only | W only |
|---|---|---|---|---|---|---|---|
| C | 1,628 | 407 / 407 | 96.68 % | 96.38 % | +0.307 | 14 | 9 |
| G | 2,728 | 682 / 383 | 88.38 % | 86.73 % | +1.650 | 98 | 53 |

**Units of the primary cell by the share of the W gold window held by the single S chunk overlapping it
most** (descriptive; `discordant_by_gold_window_coverage_under_S`):

| coverage | S only | W only | both or neither |
|---|---|---|---|
| ≥ 0.8 | 28 | 25 | 1,659 |
| 0.5–0.8 | 57 | 36 | 2,215 |
| < 0.5 | 27 | 1 | 308 |

**Context tokens per query** (descriptive; sum of the top-10 chunk texts' tokens, tokenizer
`Salesforce/SFR-Embedding-Mistral`; `context_tokens`; all 1,091 records, so 4,364 lists per retriever and 1,091
per corpus):

| | S mean / median | W mean / median | field |
|---|---|---|---|
| default, pooled | 4,141.8 / 4,232.5 | 4,777.4 / 4,902.5 | `context_tokens.{S,W}.by_retriever_pooled.default` |
| norerank, pooled | 3,829.9 / 3,910 | 4,529.6 / 4,691 | `….norerank` |
| BM25, pooled | 3,947.0 / 4,025 | 4,664.4 / 4,784 | `….bm25` |
| default, base | 4,106.5 / 4,187 | 4,763.6 / 4,874 | `context_tokens.{S,W}.by_corpus_default.base` |
| default, neighbour | 4,180.4 / 4,272 | 4,808.6 / 4,948 | `….neighbour` |
| default, random-1 | 4,143.1 / 4,237 | 4,770.0 / 4,907 | `….random1` |
| default, random-2 | 4,137.3 / 4,241 | 4,767.3 / 4,893 | `….random2` |

## J: descriptive figures (not tested, no gate, no outcome; `descriptive.J`)

`descriptive.J.status`: "descriptive (owner decision 2026-11-10; PREREG §1, §4, §7.2): no test, no outcome, no gate".

**Primary cell** (`descriptive.J.counts`): default retriever, four corpora, all 1,091 records.

| quantity | value | field |
|---|---|---|
| paired units / records / papers | 4,364 / 1,091 / 408 | `descriptive.J.counts.units`, `.records`, `.papers` |
| S judged found@10 | 4,282 / 4,364 = 98.12 % | `descriptive.J.counts.S` (rate 0.9812) |
| W judged found@10 | 4,278 / 4,364 = 98.03 % | `descriptive.J.counts.W` (rate 0.9803) |
| D | +0.092 points | `descriptive.J.counts.D_pts` |
| S only / W only / discordance | 42 / 38 / 1.83 % | `.S_only`, `.W_only`, `.discordance` |
| pairs the cell needs without a judged label | S 71 of 2,758 (2.57 %), all orphans; W 0 of 3,149 | `descriptive.J.unjudged_primary`, `descriptive.J.s_unjudged_primary_by_kind` |
| favourable bound (those 71 S pairs scored *answers*) | S 4,290 / 4,364 = 98.30 %, D +0.275 points (S only 42, W only 30) | `descriptive.J.favourable_bound` |
| caveats | the two *always* caveats of §7.2 (judge drift lands on S only; the 2 records that are not Q-eligible) | `descriptive.J.caveats` |
| caveats from checks | none: `label_integrity`, `label_reuse`, `retrieval_and_inputs`, `unjudged_primary` all empty | `descriptive.J.caveat_sources` |

**By retriever, pooled** (`descriptive.J.secondary.by_retriever_pooled`; 4,364 units each):

| retriever | S | W | D (points) | S only | W only | caveat printed by the report |
|---|---|---|---|---|---|---|
| default | 98.12 % | 98.03 % | +0.092 | 42 | 38 | — |
| norerank | 94.41 % | 94.20 % | +0.206 | 137 | 128 | "S: 499 of 5780 pairs this retriever's J needs are unjudged (> 5%)" |
| BM25 | 91.41 % | 92.78 % | −1.375 | 130 | 190 | "S: 1168 of 6221 pairs this retriever's J needs are unjudged (> 5%)" |

**By corpus, default retriever** (`descriptive.J.secondary.by_corpus_default`; 1,091 units each):

| corpus | S | W | D (points) | S only | W only |
|---|---|---|---|---|---|
| base | 98.99 % | 98.63 % | +0.367 | 8 | 4 |
| neighbour | 98.08 % | 97.98 % | +0.092 | 13 | 12 |
| random-1 | 97.80 % | 97.89 % | −0.092 | 10 | 11 |
| random-2 | 97.62 % | 97.62 % | 0.000 | 11 | 11 |

**By question kind, primary cell** (`descriptive.J.secondary.by_kind_primary`):

| kind | units | records / papers | S | W | D (points) | S only | W only |
|---|---|---|---|---|---|---|---|
| C | 1,632 | 408 / 408 | 99.82 % | 99.88 % | −0.061 | 2 | 3 |
| G | 2,732 | 683 / 383 | 97.11 % | 96.93 % | +0.183 | 40 | 35 |

**J's labels** (`j_labels`):

| quantity | S | W | field |
|---|---|---|---|
| unique (record, chunk) pairs J needs, all retrievers | 10,957 | 12,310 | `j_labels.pairs_needed` |
| … default / norerank / BM25 | 2,758 / 5,780 / 6,221 | 3,149 / 6,609 / 6,850 | `j_labels.pairs_needed_by_retriever` |
| unjudged (all orphans on S) | 1,549 (14.14 %) | 0 | `j_labels.unjudged`, `j_labels.unjudged_share`, `j_labels.s_unjudged_by_kind` |
| … default / norerank / BM25 | 71 / 499 / 1,168 | 0 / 0 / 0 | `j_labels.unjudged_by_retriever` |

S label sources (`j_labels.s_label_source`): new judging 1,456 *answers* and 5,740 *not* (7,196); prior
registries 371 *answers* and 234 *not* (605); the content-hash rule 256 *answers* and 1,351 *not* (1,607);
no label 1,549 (orphans). Prior content check (`j_labels.s_prior_content_check`): 605 reused keys checked, 0 not
reused for a content mismatch, 0 for no S text, 0 reused keys without a pairs row.

## Judge drift check (descriptive; `drift_check`)

| quantity | value | field |
|---|---|---|
| W pairs J uses / sample (5 %, seed `261109\|reach\|drift\|W`) | 12,310 / 616 | `drift_check.w_pairs_used`, `.sample`, `.seed` |
| re-judged / judged on both occasions | 555 / 555 (61 not re-judged: orphans, `j-seeds: registries.drift.orphans`) | `drift_check.rejudged`, `.judged_both` |
| agreement, overall | 0.9712 (539 of 555) | `drift_check.agreement` |
| agreement on W's *answers* labels | 0.9327 (97 of 104), at or above the 0.90 threshold: no *judge drift* caveat | `drift_check.agreement_on_answers`, `.answers_judged_both`, `.caveat` (null) |
| original *answers* → re-judged *answers* / *not* / not re-judged | 97 / 7 / 9 | `drift_check.by_original_label.answers` |
| original *not* → re-judged *answers* / *not* / not re-judged | 9 / 442 / 52 | `drift_check.by_original_label.not` |
| prompt identity (C and E, prompt and system sha) | identical on 1,110 compared rows, 0 differing | `drift_check.prompt_identity` |
| cache check | evaluable on C and E; `reasoning_sample` identical on 0 of 555 (C) and 0 of 550 eligible (E); flag false; gateway response-cache status absent | `drift_check.cache_check` |
| Qwen verdict agreement with the original rows | C 548 / 555 = 0.9874 (Wilson 95 % 0.9741–0.9939); E 538 / 554 = 0.9711 (0.9536–0.9822) | `drift_check.cache_check.{C,E}.verdict_agreement` |
| descriptives beside the cache check | `texts_sample` identical C 4, E 27; `ct` equal C 2, E 4 | `drift_check.cache_check.{C,E}` |
| registry problems / label rows outside the sample / sample pairs without W text | 0 / 0 / 0 | `drift_check.registry_problems`, `.labels_outside_sample`, `.sample_without_w_text` |
| evaluable | true | `drift_check.evaluable` |

The drift registry's Argon tiebreak and Opus reference ran on the second gateway; W's original labels were
judged on the first (D-1).

## Gates and checks (PREREG §7.1)

**Gates on Q.** None fired: `gates.Q` = []. Each check, as the report read it:

| check | result | field |
|---|---|---|
| retrieval (W and S): run status `complete`, no fired depth or reranker gate, no failed call, no missing or duplicate row in any (corpus, retriever) leg; S runs are reach runs (`rechunk`, `all_records`) | pass (no entry in `gates.Q`) | `gates.Q`; `descriptive.J.caveat_sources.retrieval_and_inputs` (empty) |
| retrieval settings, S summary against W summary per corpus, compared fields only | pass: `identical` true and `differs` empty in all four corpora; server version S 1.6.25, W 1.6.22 (printed, not compared) | `retrieval_settings.per_corpus`, `retrieval_settings.compared` |
| census: live census before and after, collection, load receipt chunk count and sha256, all equal to `build-summary.json` | pass in all four: 38,548 / 141,592 / 136,402 / 136,187 chunks | `census` |
| inputs: records, Q-eligible, top-10 chunks with text, W gold chunks | pass: 1,091 records, 1,089 Q-eligible (1,089 W-verbatim; 1,088 S-reachable in one chunk, 1 split), 0 unresolved top-10 chunk texts on either side | `records`, `q_eligible`, `q_eligibility`, `unresolved_chunk_texts` |
| frozen inputs (§9.1 pins, `frozen-inputs.json`) | verified: 27 rows, 9 flags | `frozen_inputs_check` |
| PREREG sha | verified | `prereg` |
| `j-seeds` check: `report`'s input shas equal `j-seeds.json`'s `inputs_sha256` | inputs match; sha256 `e326679e…`, equal to the committed `artifacts/j-seeds.json` | `j_seeds_check`, `inputs_sha256.j_seeds` |

The report stores no per-leg row counts. The operator record of step 2 has every S condition at 1,091 rows,
1,091 ok and 0 failed, and the server config hash at the start and end of every corpus equal to W's
(`2e91056f…`)†; the retrieval and settings checks above are what the report itself verified.

**Caveats on J** (no gates; §7.1). From checks: none (`descriptive.J.caveat_sources`, all empty). Label integrity:
`label_row_errors` empty; `label_row_duplicates` W 0, drift 0; per-registry rows equal keys in every registry
(`registries`: W 24,559; S new 7,196; drift 555; prior 351, 174 and 671). Cross-registry disagreements, counted
and not gated: 4 among the prior S registries (`registries.S_prior.cross_registry_conflicts`), 0 in the new S
registry. The primary cell's unjudged share is 2.57 % on S, below 5 % (`descriptive.J.unjudged_primary.S.share`).

**`j-seeds.json`** (§6 step 3; totals per registry only):

| quantity | value | field |
|---|---|---|
| S seeds / S orphans | 7,196 / 1,549 | `j-seeds: registries.S` |
| drift sample / seeds / orphans | 616 / 555 / 61 | `j-seeds: registries.drift` |
| not seeded: prior content mismatch / S pair without export text / drift pair without W export text | 0 / 0 / 0 | `j-seeds: not_seeded` |
| registry problems | none | `j-seeds: registry_problems` |
| prior S cross-registry conflicts | 4 | `j-seeds: s_prior_cross_registry_conflicts` |

The new S registry holds 7,196 keys (`registries.S_new.combined_keys`), the seed total, and all 7,196 carry a
new label (`j_labels.s_label_source`, `new:*`).

## Procedural notes

1. **The report was computed once** (§6 step 5; D-2). Attempt 1, shortly before 23:38:55Z†, ran under a Python
   without `transformers`. It passed the PREREG-sha, `j-seeds` and frozen-input checks and then stopped
   importing the tokenizer (`hf_token_counter`), before `build_report` computed anything. It wrote no output, and
   nothing was printed or seen. Attempt 2 ran under the project environment (with `transformers`), from the same
   frozen checkout with the same inputs, and completed (`provenance.recorded_at` 23:38:55Z).
2. **Gateway share per batch on the second gateway** (D-1). The harness finalizes each batch (Qwen, then the Argon
   tiebreak and the Opus drift reference) before the next, so the gateway share ran per batch: S registry in 4
   batches, the drift registry in 1. All 821 gateway calls ran on the second gateway; the figures are in D-1.
3. **One empty Opus item** (batch S3). The Opus drift-reference call of one pair returned an empty completion
   twice (the harness re-issues once by design), so that pair has no Opus reference: the S3 drift reference
   holds 99 pairs instead of 100. Opus sets no label (the drift reference is report-only), so no label changed.
   The cause is not known (an empty completion and a filtered stop cannot be told apart; the audit holds no
   body). In the same batch one Argon reply held no parseable verdict, and its re-issue returned one. Source:
   the gateway audit (D-1), in no committed artifact.
4. **No hard stop.** No provider notice, ACCESS DENIED reply, served-model mismatch or budget stop occurred
   (gateway audit, D-1), so there was no resume to record (§6 step 4).
5. **Exposure log** (§6, *Operator exposure*). Before the report was written, the only study figures read were
   the per-registry totals of `j-seeds.json` (S seeds 7,196 and orphans 1,549; drift sample 616, seeds 555,
   orphans 61; `j-seeds: registries`), read to size the gateway notice, as declared. No S quote@10 or found@10,
   no `final.jsonl`, `labels-*.jsonl` or `judge-summary.json`, no per-registry count in a harness `LOG` and no
   `j-seeds-detail.json` was read. The per-batch gateway call counts by model, which the audit reports (D-1),
   were seen during judging; they hold no label and no quote@10 or found@10.
