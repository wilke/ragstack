# How the test data was generated

This document follows the data from the OA corpus to the 2,035 questions the decision was computed over, and from
the extracted records to the two collections this study built (S and S-regex). Nothing here was made for this
study except the two collections: the questions, the corpus and the three reused arms are the OA chunking eval's
and Addendum C's, reused unchanged and pinned by hash. Each count names the committed artifact or record it comes
from. Private material (questions, quotes, passages, per-question rows, receipts, chunk ids, configs with hosts
and paths) stays in the private experiments repository; its hashes are in [`frozen-inputs.json`](frozen-inputs.json).

A **question** is the unit: one question with one exact answer quote from one article, scored on each arm by
whether the top-5 chunks from that article cover at least 90 % of the quote's characters (hit@5). The paired
difference between two arms on a question is in {−1, 0, +1}.

## 1. The corpus and the records (OA chunking eval, unchanged)

- **Articles.** 22,593 open-access PMC articles on bacteria and viruses: 2,600 eval articles sampled stratified by
  journal family × length band plus 20,000 background articles (seed 20261104), minus 7 that gave no extracted
  record ([`../oa-chunking-eval-2026-10/POSTHOC-HYPOTHESES-2026-11-06.md`](../oa-chunking-eval-2026-10/POSTHOC-HYPOTHESES-2026-11-06.md)
  § *Setup in brief*). Every question is searched against all 22,593.
- **Records.** Each article extracted once (`jats_extract.py`) into article, table and figure records:
  **241,930 records** in 16 shard files (`frozen-inputs.json` `n_main_records`, `shard_records`: 12,292 to 17,445
  per shard). This study ingested the Addendum C extract, which carries the `structure` key the section-cut arms
  need (PREREG §2, *Input text*); with that key removed it is byte-identical to the main run's input (private,
  `sections-2026-11/RESULTS.md`). The 17 shard files (16 main + sentinel) are pinned by sha256
  (`frozen-inputs.json` `shards`); the report checked all 17 at the run (`artifacts/h2h-report.json`
  `files.shards_hashed`).
- **The sentinel shard.** 40 records from 5 articles (5 article, 4 table, 31 figure records), the OA eval's
  5-article ingest test ([`../oa-chunking-eval-2026-10/SENTINEL-2026-11-04.md`](../oa-chunking-eval-2026-10/SENTINEL-2026-11-04.md)).
  Every OA eval arm holds it; its document ids (`uuid5(NAMESPACE_URL, path)`) are in no main shard
  (`frozen-inputs.json` `sentinel.doc_ids_also_in_main_shards` 0), so a new arm's expected store count is the sum
  of the main-shard chunk counts plus the sentinel's.

## 2. From articles to the decision set (OA chunking eval, unchanged)

| step | count | rule | source |
|---|---|---|---|
| eval articles | 2,600 (2,599 extracted) | stratified sample, seed 20261104 | POSTHOC-HYPOTHESES § *Setup in brief* |
| passages | 2,592 | one ≈ 1,200-character passage per eval article, grown from whole sentences inside one target section region (results, discussion, methods, introduction, abstract); a seeded draw picks the region and the start sentence; 8 articles skipped; 85 unheaded articles retried over the whole body | POSTHOC-HYPOTHESES § *Passage selection*; [`../oa-chunking-eval-2026-10/HARNESS-SPEC.md`](../oa-chunking-eval-2026-10/HARNESS-SPEC.md) step 2 |
| questions | 2,437 | one LLM-written question + exact answer quote per passage (Qwen3.8 writer, 10 samples, temperature 0.8), selected by programmatic checks only: the quote is an exact substring of the passage, question 25–400 and quote 10–300 characters, no copied 4-word run, self-contained; lowest echo among passers; 155 passages had no passer in 3 attempts | HARNESS-SPEC step 3; [`../oa-chunking-eval-2026-10/PLAN-AND-PASS-RULE.md`](../oa-chunking-eval-2026-10/PLAN-AND-PASS-RULE.md) § *Questions* |
| clean set | 2,335 | minus 63 ambiguous-gold items (the quote also occurs in another record) and 66 within-record repeats, some with both flags (102 flagged) | HARNESS-SPEC step 4 (gold audit); PLAN-AND-PASS-RULE Addendum B item 3 |
| **decision set** (`clean_excl_noise`) | **2,035** | minus the 300-question noise subset used to set the margins before the OA eval's freeze | PLAN-AND-PASS-RULE Addendum B item 3 |
| sensitivity set (`clean`) | 2,335 | the clean set with the noise subset included; reported, not the decision | same |

- **The sets are pinned** as sorted-id lists: decision n 2,035 sha256 `971bae6f…`, sensitivity n 2,335 sha256
  `d2b0f550…` (`frozen-inputs.json` `sets`), derived from Addendum C's setting (a) per-question rows (`ambiguous_gold`
  false and `noise_subset` false; `ambiguous_gold` false). The report's `[sets]` gate recounted both from the
  three-arm `score` output and compared the hashes; it held in all four settings (`settings.<s>.n_decision` 2035,
  `.n_sensitivity` 2335; `gates` empty).
- **One question per article**, so questions are the exchangeable units and no cluster adjustment applies
  (PREREG §3).
- **The quotes.** Short, single-span, inside one section: median 119 characters, p90 214; 0 of 2,035 cross a
  section boundary (PREREG §3, from the private passage and question files). Their edges follow the sentence
  boundaries of the passage step's segmenter, recorded as `punkt-abbrev-v1` (`frozen-inputs.json`
  `passages_segmenter`; the staged `passages.summary.json` at the run has the pinned sha256
  `passages_summary_sha256`), which is the H3 bias PREREG §3 declares: it favours S.
- **Strata.** Journal family (8: infectious-disease 341, microbiology 287, molbio-immunology 263, multidisciplinary
  132, other 139, plos-one 365, sci-reports 207, virology 301) and length band (3: long 641, medium 783, short 611)
  on the decision set (`artifacts/h2h-report.json` `settings.a.stratum_guard.tested[].n`); every stratum clears the
  n ≥ 30 floor, k = 11.

## 3. The arms

| arm | chunking | collection | built by | chunks (sentinel included) | source |
|---|---|---|---|---|---|
| ft512o64 | `fixed_token` 512/64 | OA eval main run (2026-11-04) | the main run's registered workflow on an earlier tools image | 705,204 | `files.reused_census` |
| ft512o64s | `fixed_token` 512/64, `structure_cuts sections/1` | Addendum C (2026-11-06) | Addendum C's direct submit of dev's `ingest-bulk.cwl`, tools v1.6.24-b1 | 859,280 | `files.reused_census` |
| sent512s | `sentence` 512 tokens, overlap 0, `sections/1` | Addendum C | same | 850,312 | `files.reused_census` |
| **S** | `sentence` 512 tokens, overlap 0, `size_tolerance` 0.25 (ceiling 640), `min_piece` 128, `sections/1`, Punkt | **this study** | the study text `ingest-bulk-h2h.cwl`, tools v1.6.24-b1 (`punkt-abbrev-v1`) | **767,984** = 767,816 + 168 | `files.arms.S.census` |
| **S-regex** | the same spec, regex segmenter | **this study** | the same text with the six image-name lines renamed, tools v1.6.24-b2 (no `nltk`, `regex-abbrev-v1`) | **767,956** = 767,788 + 168 | `files.arms.S-regex.census` |

All five: `Salesforce/SFR-Embedding-Mistral` 4096-d embeddings, the same reranker (`BAAI/bge-reranker-v2-m3`), the
same 241,930 records plus the sentinel. The reused arms' live census at the run equalled the census read at the
freeze, registry = Qdrant = Elasticsearch (`files.reused_census`, gate `[census]`).

### 3.1 Building S and S-regex (PREREG §7 step 2; `artifacts/ingest-gates-summary.json`)

1. **Create** (22:31:53Z): two registry entries, `oa-eval-s` and `oa-eval-sregex`, tenant `public`, with the chunk spec
   in the create body and no `embedding` key (the server default resolved SFR 4096-d, `openai`); `spec_hash`
   `29236085` and identical `chunk_params` on both (`ingest-summary: sentinel.<arm>.registry`).
2. **Sentinel shard first** (submitted 22:32:38Z, checked 22:34:03Z): one direct `gowe submit` per arm of the study
   text; `h2h_report.py dryrun` on the receipt, the staged stderr file, the logs-endpoint tail, the store counts
   and the registry entry: 40 documents, 0 failed, **168 chunks** on each arm with **identical chunk ids** across
   the arms (168 of 168), and equal to the pre-freeze dry run's and study-text pass's ids (checked by this
   write-up); `image_build` 1 / 2, segmenter `punkt-abbrev-v1` / `regex-abbrev-v1`, the full `chunking` object;
   stderr 0 / 1 warning lines, file = endpoint; Qdrant = Elasticsearch = 168 (`ingest-summary: sentinel`).
3. **The 16 main shards** (submitted 22:34:17Z; S complete ≈ 23:33:47Z, S-regex ≈ 23:48:47Z): one submission per arm
   (16 glossary tasks, 16 `ingest_shard` tasks, 1 merge; all `SUCCESS`; two ingest tasks retried, see
   [RESULTS.md](RESULTS.md) § *Ingest record*). Per arm, 16 receipts with `chunk_ids` per shard and per document:
   241,930 documents, 0 failed; distinct chunk ids = Σ `n_chunks`; stderr per shard 0 (S) / 1 (S-regex) warning
   lines (`files.arms.<arm>`).
4. **Store census** (23:50–23:51Z): Qdrant = Elasticsearch = distinct ids = 767,984 (S) and 767,956 (S-regex)
   (`files.arms.<arm>.{qdrant,es,census.distinct_chunk_ids}`).
5. **What differs between the two new arms.** The tools image build (1 vs 2) and so whether `nltk` is importable;
   nothing else: same commit, same spec, same shards, same text but for the image name. 761,915 chunk ids are
   common to both (99.2 % of either; `files.chunk_id_overlap`): the segmenters moved a boundary in under 1 % of
   chunks on this corpus.

### 3.2 What differs between the new arms and the reused ones

The ingest path (PREREG §8): S and S-regex ran the study text (dev's v1.6.25 text plus a per-shard stderr `File`
output; `test_study_workflow_texts_pinned`) on tools v1.6.24 builds; ft512o64s and sent512s ran Addendum C's
direct submit of dev's text on v1.6.24-b1; ft512o64 ran the main run's text on an earlier image. Capturing stderr
does not touch chunking (the sentinel chunk ids reproduced across the texts). The reused arms were embedded on
2026-11-04/06; the re-embed gate (200 ft512o64s chunks, seed 20261111, re-embedded at the run; minimum cosine to
the stored vector 0.9999995, `files.reembed`) shows the embedding endpoint unchanged.

## 4. Retrieval (PREREG §7 step 3)

- 5 arms × 4 settings × 2,437 questions = **48,740 calls** (12,185 per setting) against the dev API, `/v1/retrieve`,
  `top_k` 10, max 4 in flight, arms interleaved per question, 23:53:12Z to 00:27:51Z†; 0 failed rows, 0 rows
  ignored for a request mismatch (`settings.<s>.missing_by_arm` all 0; private `score.json`
  `request_mismatch_by_arm` 0).
- **Settings** (`frozen-inputs.json` `frozen_request`; gate `[request]`): (a) `hybrid`, `rerank` true, `use_graph`
  false, **the decision**; (d) `hybrid`, no reranker; (b) `vector`; (c) `bm25`.
- Every row carries the server version: one label across all 48,740 rows, `1.6.25+67e53fd4…` (`server_version`).
- The three decision arms' ledgers were copied into the decision `out_dir` per setting (00:28Z†); the five-arm
  reporting `out_dir` kept all five. Both `out_dir`s per setting also held the frozen inputs (questions, passages,
  sample, audit, extracted text; staged as symlinks, [DEVIATIONS.md](DEVIATIONS.md) D-2) and the frozen `noise.json`.

## 5. Scoring (PREREG §3; HARNESS-SPEC step 6)

- Per question × arm × k ∈ {1, 5, 10}: **hit@k** = the union of the top-k chunks whose `source_path` is the gold
  record covers ≥ 90 % of the quote's characters (verified offsets; maximum over occurrences when the quote
  repeats); **strict** = one chunk holds the whole quote; **ctx@k** = the sum of the top-k chunk lengths, chars/4,
  labelled estimated (owner Q6).
- `score` ran twice per setting: the three-arm decision config (ft512o64 baseline, S, ft512o64s) and the five-arm
  reporting config; each keeps a question only when every arm of its config has an ok row (n = 2,035 in both,
  `settings.<s>.n_decision`, `.n_five_arm`). `decide` ran on the three-arm output (`paired_ladder`, `bonferroni`,
  margins 2.0 from the frozen `noise.json`, integer ranks ft512o64 1, S 2, ft512o64s 3).
- Hit quality (harness, private `score.json`): `unlocated_share` 0.0 and `offsets_unverified` 0 for every arm, 0
  questions without record text, no refusal.
- The §9.4 script then computed every figure in [RESULTS.md](RESULTS.md) from the three-arm and five-arm
  `per_question.jsonl` and `score.json` and the decision config's `decision.json`, with the paired bootstrap over
  questions (10,000 resamples, seed 0).

## What a different site would need

- The 22,593 articles (PMC OA; the manifest and the bacteria/virus id list are private), the Addendum C extract
  (241,930 records with `structure`; 16 shards + sentinel, hashes pinned), the 2,437 questions with their quotes
  and the audit flags (private; the decision and sensitivity id lists pinned by hash).
- The five collections, or a rebuild: the three reused arms as in the OA eval and Addendum C; S and S-regex from
  `ingest-bulk-h2h.cwl` and the two tools images (v1.6.24 build 1; build 2 from `ragstack-tools-nonltk.def`;
  digests pinned), with the create body of PREREG §7 step 2.
- A RAGStack server at v1.6.25 with the same embedding model (SFR-Embedding-Mistral, 4096-d) and reranker
  (bge-reranker-v2-m3), and a GoWe deployment that stages a task's full stderr out as a file.
- The code at tag `prereg-adopted-spec-h2h-2026-11` (`python/scripts/eval/oa_chunk_eval/`,
  `python/scripts/eval/h2h_report.py`), the four decision and four reporting configs (private; sha256 pinned) and
  the four frozen `noise.json` files (sha256 pinned). No LLM, judge or gateway is needed.
