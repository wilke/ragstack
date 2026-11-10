# How the test data was generated

This document follows the data from the alias study's questions to the 4,356 paired units the confirmatory
test (Q) analysed, and to the pairs judged for the descriptive family (J). Each count names the committed
artifact or prereg section it comes from. The private files (questions, quotes, texts, chunk ids, runs,
labels) stay off the repository; their sha256 hashes are pinned in `artifacts/reach-report.json`
`inputs_sha256`, in `artifacts/j-seeds.json` and in [`frozen-inputs.json`](frozen-inputs.json).

A **paired unit** is *one question in one corpus collection*: the question's top-10 list under W and its top-10
list under S, from the same retriever, scored by the same function.

## 1. The papers, the corpora and the questions (alias study, unchanged)

- **Papers and corpora.** The alias study's four corpora: the base corpus OA-1000 (12,560 documents: articles
  plus their table and figure units) and three sets of added papers, neighbour, random-1 and random-2 (30,408 /
  32,671 / 33,409 documents). How they were drawn and extracted is in
  [`../alias-boundary-confirm-2026-11/TEST-DATA.md`](../alias-boundary-confirm-2026-11/TEST-DATA.md) stage 1; the
  document counts are in [`../rechunk-found10-2026-11/TEST-DATA.md`](../rechunk-found10-2026-11/TEST-DATA.md) §2.
  Each of the four **corpus collections** holds the base corpus plus, for the three larger ones, one set of
  added papers.
- **Questions.** The alias study's **1,091 accepted questions** (records) on **408 papers**, built blind to
  retrieval (`artifacts/reach-report.json` `records`, `papers`). How they were drafted and verified is in the
  confirmatory test's TEST-DATA, stage 2. They are unchanged here: the same questions, answers and quotes, in the
  pinned records file (`inputs_sha256.records`).
  - **Kinds.** 683 G questions (the question names a term the answering passage carries only in a short form)
    and 408 C questions (the passage carries the term) (`records_by_kind`).
  - **Papers by questions.** 25, 83 and 300 papers hold 1, 2 and 3 questions (`feasibility.json`
    `eligibility.papers_by_records_all`).
- **The quote.** Each record carries a `quote`, about 160 characters long (range 18–300), taken from its **gold
  chunk**, the 512-token window it was written from (PREREG §2). quote@10 scores this quote. Every quote was
  drawn from inside one W window (`feasibility.json` `eligibility.quotes_inside_one_w_window`, 1,089 of the
  eligible).

## 2. Eligibility for Q (PREREG §2; counted before the freeze, recounted by the report)

| filter | records | source |
|---|---|---|
| accepted questions | 1,091 | `reach-report.json` `records` |
| quote verbatim in its W gold chunk, raw | 1,065 | `feasibility.json` `eligibility.verbatim_raw` |
| quote verbatim after whitespace normalisation (the normalisation quote@10 uses): **W-verbatim** | 1,089 | `reach-report.json` `q_eligibility.w_verbatim` |
| … and **S-reachable**: `quote_cover` over every S chunk of the gold document ≥ 0.90 | 1,089 (1,088 inside one S chunk, 1 split across two) | `q_eligibility.s_reach_of_w_verbatim` |
| **Q-eligible** | **1,089**, in 408 papers | `q_eligible`, `q_eligible_papers` |

- The 2 records that are not W-verbatim are excluded from Q and kept in J (PREREG §2, §11 Q4).
- Q-eligible by kind: 682 G and 407 C (`families.Q.secondary.by_kind_primary.{G,C}.records`). Papers by Q-eligible
  questions: 25, 85 and 298 hold 1, 2 and 3 (`feasibility.json` `eligibility.papers_by_eligible_records`).
- A record count other than 1,091 or a Q-eligible count other than 1,089 is a gate on Q (fixed in the code, with
  no override; PREREG §7.1, *Inputs*). Neither fired (`gates.Q` empty).

## 3. From records to paired units

- **Q's primary cell:** the 1,089 Q-eligible records × the 4 corpus collections = **4,356 paired units**
  (`families.Q.counts.units`), clustered in 408 papers (`families.Q.counts.papers`), default retriever.
- **Q's secondary cells:** 4,356 units per retriever (`families.Q.secondary.by_retriever_pooled.*.units`) and 1,089
  per corpus under the default retriever (`families.Q.secondary.by_corpus_default.*.units`).
- **J:** all 1,091 records × 4 = **4,364 paired units** (`descriptive.J.counts.units`); 1,091 per corpus.
- A unit's score on each side is 1 when the union of its top-10 chunks covers at least 90 % of the quote's
  characters after whitespace normalisation, else 0 (`quote_cover` / `quote_hit` in
  `python/scripts/eval/reach_quote.py`; `scorer`: `min_match` 20, `hit_threshold` 0.9, `top_k` 10). The paired
  difference S − W is in {−1, 0, +1}.

## 4. The W side: fixed 512-token windows (existing; nothing new was run)

- **Chunking.** Every corpus chunked by `fixed_token/512/0`: 512-token windows with no overlap, blind to sentences
  and sections, in the `Salesforce/SFR-Embedding-Mistral` tokenizer
  ([`../alias-boundary-confirm-2026-11/TEST-DATA.md`](../alias-boundary-confirm-2026-11/TEST-DATA.md) stage 3).
- **Retrieval.** The alias study's committed stage-A runs, legs `<corpus>/none` (no glossary), 2026-11-04: each
  record's ranked chunk ids under the three retrievers (the served default, a BM25 + dense hybrid reranked by
  `BAAI/bge-reranker-v2-m3`; the same hybrid without the reranker, "norerank"; BM25 alone), top 50 requested, top
  10 read. Pinned: `inputs_sha256.w_runs` (runs and summaries), `inputs_sha256.w_exports` (the window texts).

## 5. The S side: the adopted chunking

- **Collections.** The four bench collections of rechunk stage 1, `bench-rechunk-{base,neighbour,random1,random2}`,
  built with `sentence/512/0`, tokens, `size_tolerance` 0.25 (a 512-token target, a 640-token ceiling),
  `structure_cuts sections/1`, `min_piece` 128, glossary none. Their build is in
  [`../rechunk-found10-2026-11/TEST-DATA.md`](../rechunk-found10-2026-11/TEST-DATA.md) §3–§4 and
  [`../rechunk-found10-2026-11/artifacts/build-summary.json`](../rechunk-found10-2026-11/artifacts/build-summary.json):

  | collection | chunks |
  |---|---|
  | base | 38,548 |
  | neighbour | 141,592 |
  | random-1 | 136,402 |
  | random-2 | 136,187 |
  | **all** | **452,729** (`build-summary.json` `chunks_total`) |

  The report's census check found each collection equal to `build-summary.json` before and after the run
  (`census`). The S chunk texts are the exports pinned in `inputs_sha256.s_exports`; for the three larger
  collections they are derived from the image's own embedding files (rechunk DEVIATIONS D-5).
- **Retrieval** (PREREG §6 step 2). 1,091 records × 3 retrievers × 4 collections = 13,092 calls, by
  `alias_study_run.py` in `rechunk` mode with `all_records: true`, from the frozen run config
  `reach-config.json` (private; sha256 `7ed883dc…` in PREREG §9.1), at the frozen commit. Same three retrievers,
  top 50 requested, top 10 read. The report's settings check found the S run summaries equal to W's in every
  compared field, server config hash included (`retrieval_settings`). Pinned: `inputs_sha256.s_runs`.

## 6. J: the pairs that need a label (PREREG §3, §6 step 3)

`reach_report.py j-seeds`, run once at the frozen commit (`artifacts/j-seeds.json`):

- **Which pairs.** For every list **without** a quote hit, under all three retrievers, its top-10 (record, chunk)
  pairs. A list with a quote hit is found under J without a label. Unique pairs needed: **S 10,957, W 12,310**
  (`reach-report.json` `j_labels.pairs_needed`; by retriever `j_labels.pairs_needed_by_retriever`).
- **W.** All 12,310 W pairs already carry a final label in the alias study's main registry (`--w-final`, 24,559
  keys; `registries.W`). No W pair was judged anew for J.
- **S, in order** (`j_labels.s_label_source`):

  | source | S pairs | rule |
  |---|---|---|
  | a prior S registry | 605 | a judged label for the key in one of the three prior registries (rechunk confirm control 351 keys, cover-any 174, stage-1b 671; `registries.S_prior`), reused only if its `content_sha256` equals the S export text's sha256 (605 checked, 0 mismatches; `j_labels.s_prior_content_check`) |
  | the content-hash rule | 1,607 | the S chunk's text is byte-identical to a judged W chunk of the same question |
  | **new judging (seeds)** | **7,196** | `j-seeds: registries.S.seeds` |
  | **orphans** (never judged; count as not answering) | **1,549** | the record has no stage-A pair in the main registry to serve as `control_of`, so `build-control` cannot take the pair (`j-seeds: registries.S.orphans`; `j_labels.s_orphans`). 748 records are orphan records (`feasibility.json` `orphans.records`) |
  | **total** | **10,957** | |

- **Not seeded for other reasons:** 0 prior content mismatches, 0 S pairs without export text (`j-seeds:
  not_seeded`).
- **Drift sample.** A seeded 5 % sample of the 12,310 W pairs (seed `261109|reach|drift|W`): **616** pairs
  (`drift_check.sample`; `j-seeds: registries.drift.sample`), of which 555 seeded for re-judging and 61 orphans
  (`j-seeds: registries.drift`).

## 7. J: the judging registries (PREREG §6 step 4)

- **Two new registries**, each built by `alias_study_judge.py build-control --any-paper` in its own new output
  directory, under the alias study's frozen main judge configuration with only `out_dir` and `chunk_exports`
  changed:
  - the **S registry**, from the 7,196 seeds: 7,196 keys and rows (`registries.S_new`), every one with a new label
    (1,456 *answers*, 5,740 *not*; `j_labels.s_label_source`); judged in 4 batches;
  - the **drift registry**, from the 555 drift seeds: 555 keys and rows (`registries.drift`); 1 batch.

  Pinned: `inputs_sha256.s_final`, `inputs_sha256.drift_final`; the seed and export files by
  `j-seeds: outputs_sha256`.
- **The judge** (frozen, the alias study's): C = Qwen3.6-35B-A3B (5 samples) and E = Qwen3.8-27B (1 sample),
  each reading the question and one chunk; Gemini 4 Argon breaks their ties; Claude Opus 5.5 reads a 5 % drift
  reference and sets no label. The gateway calls ran on the second gateway ([`DEVIATIONS.md`](DEVIATIONS.md) D-1).
- **Combining labels.** Within one registry, two judged labels of one key are a label-integrity caveat (none:
  `descriptive.J.caveat_sources.label_integrity`). Across registries, *answers* if any registry says *answers*; the
  disagreements are counted (4 among the prior registries, `registries.S_prior.cross_registry_conflicts`).
- **Favourable bound.** The report recomputes J's primary cell with every unjudged or orphan S pair it needs
  scored *answers*: 71 pairs (`descriptive.J.favourable_bound.pairs`).

The results are in [RESULTS.md](RESULTS.md) and [REPORT.md](REPORT.md).

## What a different site would need

- The four corpora's texts, the alias study's 1,091 question records and its main judge registry; the three prior
  S registries. These are private; their hashes are pinned in the artifacts and in PREREG §9.1.
- The W runs and exports (pinned), or a re-run of the alias study's stage A at its pin.
- The S collections, or a rebuild as in [`../rechunk-found10-2026-11/TEST-DATA.md`](../rechunk-found10-2026-11/TEST-DATA.md),
  and a RAGStack deployment with the same retriever settings (the server config hash is compared).
- The code at tag `prereg-reach-rechunk-2026-11` and the SFR tokenizer. Q needs nothing else.
- For J: the two Qwen judge models at the alias study's settings, and gateway access for the Argon tiebreak and
  the Opus drift reference.
