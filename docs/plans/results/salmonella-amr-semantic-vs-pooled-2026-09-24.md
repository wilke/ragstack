# On the owner's corpus, `semantic` and `semantic_pooled` share 2 boundaries in 791

**Date:** 2026-09-24 · **Tenant:** hackathon (live, via API → GoWe, worker image `ragstack-worker-v1.6.4.sif`) · **Status:** measured, reproducible, corpus-scale (20 documents, 13,274 distance pairs)

The corpus-scale run that `docs/plans/chunking-one-factory.md` §7c left pending. It
confirms the 2026-09-18 shard finding at 260× the pair count and adds three things
the shard could not: the two arms built **through the production path** (upload →
GoWe → `ingest_shard.py` → Qdrant/ES) agree exactly with the offline reproduction;
the "~7× at buffer 3" cost claim is measured at **6.78×** in tokens; and the token
saving does **not** reach the wall clock, because the pooled arm spends what it saves
in pure-Python mean-pooling.

**Verdict.** The two methods are two chunkers. On 20 real papers with identical
extraction, `semantic` and `semantic_pooled` place boundaries that overlap on 2 spans
out of 791 (Jaccard 0.0025); one of the two is a document that neither method split
at all. Rank correlation of their distance series is 0.27 overall and never above
0.68 on any document. Each arm reproduces itself perfectly (Spearman 1.0000, all 20
documents' spans identical across runs), so this is method, not noise. Anything that
presents pooled as "semantic, but cheaper" is mislabelled, and the 09-18 decision not
to alias `semantic` → pooled (§7d) holds at corpus scale.

**Proven here for the first time since the v1.6.4 roll:** a `semantic` collection
created through `POST /v1/collections` on hackathon and populated through
`POST /v1/ingest/upload` completes end-to-end on the GoWe path (submission
`sub_53583e51…`, 375 points, 20 documents, 119 s upload-to-registry). The pooled arm
did the same (`sub_84303b70…`, 418 points, 108 s).

**Not established:** which arm retrieves better. There is no query set for this
corpus; nothing here is a retrieval-quality claim.

## The three arms

Same 20 PDFs (`Salmonella_AMR`'s upload, `sub_247e392c…`), same extraction text
(the two new arms' `batch-00000.jsonl` files are text-identical to each other and
to the fixed arm's, 20/20 documents; only the staging path differs), same model
(`Salesforce/SFR-Embedding-Mistral`, dim 4096, `--embedding-url :9001 :9002` in the
worker command). Token counts are in that model's HF tokenizer via
`ragstack.ingestion.tokenization.make_token_counter("hf")`, read back from ES.

| arm | collection id | physical name suffix | spec_hash | chunks | docs | tokens p10 / p50 / p90 / max | mean tok | chars p50 | Σ tokens |
|---|---|---|---|---|---|---|---|---|---|
| `fixed` 512/64 (pre-existing, read-only) | `Salmonella_AMR` | `…fixed_512_64_4a77730a` | `c0ba7587` | 2,090 | 20 | 141 / 179 / 244 / 513 | 189.7 | 512 | 396,398 |
| `semantic` (buffer 3, p80, min 500) | `salmonella-amr-semantic` | `…semantic_9bcfa50f` | `b2edb53e` | 375 | 20 | 242 / 611 / 2,125 / 4,080 | 921.6 | 1,705 | 345,591 |
| `semantic_pooled` (same params) | `salmonella-amr-pooled` | `…semantic_pooled_300cec5e` | `10d1fcba` | 418 | 20 | 243 / 566 / 1,642 / 4,079 | 826.8 | 1,568 | 345,587 |

Qdrant points == ES documents for every arm (2,090 / 375 / 418). Both new arms have
exactly 20 distinct `doc_id`s. The 4,080-token maximum is the worker's resolved
budget (`max_model_len 4096 − reserve 16`): 16 semantic and 10 pooled chunks sit at
it, split by `_emit`'s token budget — so the GoWe path **does** apply a token budget
where the in-process API builder (`api/deps.py _chunker_for`) applies none. The
fixed arm's Σ tokens is higher because 64-char overlaps are counted twice.

Per-document chunk counts (ES read-back, `readback/readback-summary.json`):

| doc | fixed | semantic | pooled | doc | fixed | semantic | pooled |
|---|---|---|---|---|---|---|---|
| 15848289 | 214 | 72 | 53 | PMC11057200.2 | 173 | 26 | 27 |
| 19778917 | 59 | 11 | 14 | PMC155865 | 14 | 1 | 1 |
| 25769786 | 71 | 21 | 16 | PMC2443889 | 87 | 5 | 17 |
| 26683630 | 148 | 7 | 14 | PMC2650546 | 108 | 11 | 16 |
| 27559761 | 86 | 5 | 9 | PMC3035553.1 | 33 | 5 | 9 |
| 29091182 | 89 | 19 | 19 | PMC4244539.1 | 248 | 35 | 42 |
| 33877914 | 81 | 6 | 8 | PMC4371985 | 108 | 27 | 24 |
| 37906281 | 96 | 29 | 28 | PMC5020279 | 61 | 14 | 23 |
| 40391708 | 131 | 29 | 31 | PMC6881056.1 | 125 | 17 | 31 |
| 7590165 | 36 | 7 | 8 | PMC7762970.1 | 122 | 28 | 28 |

Metadata present on every chunk of all three arms: `chunk_index`, `doc_type`,
`filename`, `source_path`, `tenant_id`, `n_citations`, `prev/next_chunk_id` (absent
on the first/last chunk of each doc); on a subset: `doi`/`doi_source` (267 of 375
semantic chunks), `year`, `section` + `is_boilerplate` (89 / 88 chunks; the two
counts coincide on every arm, so `section` appears where boilerplate was labelled).
**No `section_title`** on any arm: the section-aware chunker's contract field is not
written by v1.6.4.

## Boundary analysis (offline, identical extraction, bulk endpoints)

Run inside `ragstack-worker-v1.6.4.sif` on the same `batch-00000.jsonl`
(md5 `f862891198df5fe8cc4f9e33fff8001d`, 20 docs, 933,068 chars, 13,294 sentences),
through the tool's own builders — `ingest_shard._build_bridge` and
`ingest_shard._build_chunker` with the same argv shape the worker used, so buffer 3 /
p80 / min 500 and the 4,080 budget are resolved the same way. Only the embedding
endpoints differ: `:9005`/`:9006` (bulk) here, `:9001`/`:9002` in the worker.
Distances come from the real `SemanticChunker._buffer_embeddings`; spans from the
chunker's own `_breakpoint_groups → _merge_short → _emit` sequence
(`chunkers.py` v1.6.4, `chunk()` lines 199–219), applied once per embed so each arm
is embedded once per run. `compare_corpus.py`; analysis in `analyze.py`.

**The offline spans are byte-identical to what the API/GoWe path wrote to ES** — all
20 documents, both arms (span Jaccard 1.0, `analysis.json → offline_vs_es`). That is
the link between this section and the live collections above, and it says the
bulk and query endpoint pairs place the same boundaries.

```
                            2026-09-24 corpus              2026-09-18 shard
                       semantic   semantic_pooled       semantic   pooled
texts embedded           13,294           13,294           108      108
TOKENS embedded       2,413,763          356,146        10,032    2,244
ratio                          6.78x                          4.47x   (buffer 2)
breakpoint embed time     39.9 s           14.8 s           —        —
arm wall time (offline)   52.1 s           50.1 s           —        —
chunks                      375              418

distance pairs                     13,274                        51
overall Spearman                   0.2694                        0.4254
per-doc Spearman   median 0.204, range −0.054 … 0.674     0.24 / 0.46 / 0.58
span Jaccard                       0.0025  (2 / 791)            0.111
docs with any shared span            2 / 20                     1 / 3
docs with identical spans            1 / 20                     1 / 3
```

The two shared spans: `PMC155865.pdf` `(0, 6112)` — 123 sentences, one chunk under
both methods, i.e. not a boundary decision at all — and one interior span
`(33977, 34652)` of `PMC11057200.2.pdf` (26 vs 27 chunks; 1 of the 52 in the union). Every
other document: Jaccard 0.000. Chunk *counts* agree on several documents (29091182:
19 vs 19; PMC7762970.1: 28 vs 28) while sharing no boundary — the 09-18 warning
against counting chunks, again.

Spearman is the tie-aware (average-rank) estimator; the 09-18 script's ordinal
estimator gives the same 0.2694 overall. Ties are real: pooled's 6-decimal rounding
produces 1,298 tied pairs and legacy has 585 (concentrated in `19778917.pdf`, 415,
and `PMC2443889.pdf`, 170 — documents with many repeated sentences, e.g. running
headers, whose buffers embed identically).

### Control

Each arm run twice against the same fleet (`run1` / `run2`, `control` in
`analysis.json`):

```
semantic         Spearman 1.0000   spans identical 20/20 docs   12,633 / 13,274 distances bit-identical (95.2%)   max |Δ| 1.7e-4
semantic_pooled  Spearman 1.0000   spans identical 20/20 docs   11,925 / 13,274 distances bit-identical (89.8%)   max |Δ| 1.4e-5
```

Same nuance as 09-18: float nondeterminism is present (5–10 % of distances differ
between runs) but never moved a boundary here. Legacy's reproducibility is observed,
not guaranteed; pooled's rounding is why its max |Δ| is an order of magnitude
smaller. (09-18: 0.9984 / 0.9993, 18/51 and 31/51 bit-identical.)

## Cost

**Tokens: 6.78× at buffer 3**, measured (2,413,763 vs 356,146 embedded through the
breakpoint bridge; both arms embed 13,294 texts — one per sentence — the difference
is that legacy's texts are 7-sentence windows). The arithmetic `2·3+1 = 7` predicts
7×; edge windows are shorter. The 09-18 4.47× was at buffer 2 (predicted 5×).

**Time: the saving does not reach the wall clock.** Breakpoint embed time is 39.9 s vs
14.8 s (2.7×, run2 37.6 / 14.7), but arm wall time is 52.1 s vs 50.1 s offline, and
the worker's ingest step took **65.8 s (semantic) vs 62.4 s (pooled)**. The cause is
measured, not inferred: `_mean_pool` is pure Python over 4,096-d lists, and
projecting its per-window cost onto this corpus's 13,294 windows gives ≈ 23 s —
almost exactly the 25 s of embed time pooled saves. (`_cosine_distance`, also pure
Python, costs ≈ 8 s in both arms.) Offline wall times also include this harness's
own tokenizer counting, which is heavier on the legacy arm; the worker's step times
are the clean comparison. A NumPy `_mean_pool` would make pooled's cost advantage
real; today it is a GPU-token advantage only.

Fleet: both arms' breakpoint embeds went to `:9005`/`:9006` offline (bulk) and to
`:9001`/`:9002` on the live path (the worker command's `--embedding-url`).

## The live run, step by step

Gate order: baseline listing → create A → upload A → completed + counts → create B →
upload B → completed + counts → read-back → offline. Nothing pre-existing was
modified; `Salmonella_AMR2` (0 points, another user's) was not touched. Both new
collections are left in place. Upload bounds on `origin/main` (`max_upload_files` 50,
`max_upload_bytes_per_request` 500 MB, tenant `MAX_DOCUMENT_BYTES` 50 MB) admit
20 PDFs / 17 MB in one request, so each arm is one GoWe submission.

| step | semantic (A) | semantic_pooled (B) |
|---|---|---|
| `POST /v1/collections` → 201 | 13:23:25Z | 13:27:57Z |
| `POST /v1/ingest/upload` → 202 | 13:23:34Z, 4.35 s to accept | 13:28:14Z, 3.77 s |
| `gowe: submitted` (API log) | `sub_53583e51-5f4b-48f8-8d38-25b0117a9172`, 13:23:38.7Z | `sub_84303b70-3e42-4e83-a936-37e3bd248c68`, 13:28:18.2Z |
| workflow | `wf_518c2e23-d988-48db-ac43-2279869ee1fe` | same |
| extract (`pdf_extract.py`) | worker-4, 13:24:18.2 → :20.3 (**2.1 s**; 40 s after submit) | worker-4, 13:28:48.2 → :50.4 (**2.2 s**; 30 s after submit) |
| ingest (`ingest_shard.py`) | worker-1, 13:24:22.2 → 13:25:28.0 (**65.8 s**) | worker-3, 13:28:52.2 → 13:29:54.5 (**62.4 s**) |
| pack (`archive_version.py --version 1`) | worker-2, 13:25:30.2 → :31.4 (1.2 s) | worker-2, 13:29:56.2 → :57.3 (1.1 s) |
| registry `ingested_at` | 13:25:37.4Z | 13:30:06.5Z |
| job `completed` (10 s poll) | 13:25:46Z; items 20/20/0 failed | 13:30:13Z; items 20/20/0 failed |
| upload → registry | **119 s** | **108 s** |
| Qdrant points / ES docs / distinct docs | 375 / 375 / 20 | 418 / 418 / 20 |

Worker timestamps are the logs' local time (UTC−5) converted to UTC. The
`WARN stage-out failed … no authentication token available` lines around each step
are the known benign GoWe#272. The ingest command carries `--chunk-size 256
--chunk-overlap 32` for both semantic arms (ignored by `SemanticChunker`; the
collections record `chunk_size: null`).

## What this does NOT establish

- **Which arm is better.** No query set exists for this corpus; boundary difference
  is not boundary quality.
- **Anything about other corpora or parameters.** One corpus, one parameter triple,
  one fleet state.
- **That legacy is reproducible in general.** Observed 20/20 here; unrounded.
- **Retrieval-side cost.** The chunk-embedding cost of the 375 vs 418 emitted chunks
  (Σ ≈ 345 k tokens either way) is the same for both arms and was not separated out.

## Consequences

- §7c is closed: the corpus-scale run confirms the shard finding, more strongly
  (0.27 / 0.0025 vs 0.43 / 0.11).
- The §7d decision (no `semantic` → pooled alias) stands; the two are not
  interchangeable on any document that splits.
- **Pooled is not cheaper in wall time until `_mean_pool` leaves pure Python.** That
  is a one-line NumPy change with a reproducibility argument to make (mean of a fixed
  input order is deterministic in NumPy too, but not bit-identical to the Python
  sum); it would be a follow-up, not part of this record.
- The GoWe ingest path applies the model-window token budget (4,080) to semantic
  chunks; the in-process API builder does not. Same collection spec, two chunk-length
  ceilings depending on `INGEST_BACKEND` — one more row for the §8 consolidation.
- The GoWe worker embeds breakpoints on the **query** endpoints (`:9001`/`:9002`),
  not the bulk pair. 2.4 M tokens per 20-paper semantic ingest lands on the pair
  serving live queries.

## Artifacts

All under `docs/plans/results/salmonella-amr-semantic-vs-pooled-2026-09-24/`. No
tokens, keys or DSNs; log excerpts are redacted (`Bearer <redacted>`).

| file | what it is |
|---|---|
| `PLAN.md` | the gated plan the run followed, rollback stated |
| `compare_corpus.py` | the two-arm comparison inside the worker image; writes raw distance series and spans per arm per run |
| `run_offline.sh` | the exact invocation (`apptainer exec --bind /rag`, `HF_HOME=/rag/cache`, `HF_HUB_OFFLINE=1`, `:9005`/`:9006`), run1 then run2 |
| `es_readback.py` | read-only ES read-back of all three arms with the HF tokenizer |
| `analyze.py` | every number in this file: cost, Spearman (tie-aware + 09-18 estimator), Jaccard, control, offline-vs-ES |
| `offline/distances-{semantic,semantic_pooled}-{run1,run2}.json` | **the raw per-document distance series** — the 09-18 known gap, closed |
| `offline/spans-*-{run1,run2}.json` | per-document chunk spans with char and token lengths |
| `offline/summary-{run1,run2}.json` | per-arm texts/tokens/calls/embed-time/wall, per-doc sentence and chunk counts |
| `offline/analysis.json` | the computed comparison and control |
| `offline/run.log` | the run's stdout |
| `readback/readback-summary.json`, `readback/readback-<arm>.json` | ES read-back: counts, distributions, metadata fields, per-chunk records |
| `receipts/baseline-*` | `GET /v1/collections`, Qdrant `/collections`, ES `_cat/indices` before anything was created |
| `receipts/create-{A,B}.json`, `upload-{A,B}.json`, `job-{A,B}-last.json`, `collection-{A,B}-after.json` | the API's own responses at each gate |
| `receipts/t-*.txt` | UTC timestamps taken immediately before each create/upload |
| `receipts/worker-{A,B}-window.txt`, `ingest-{A,B}-command.txt` | worker log windows and the full `ingest_shard.py` command lines (redacted) |
| `receipts/gowe-ingest-receipt-{semantic,pooled}.json`, `extract-report-*.json` | the worker's receipts, copied read-only from the GoWe workdir |
| `receipts/extraction-md5.txt` | md5 of the extraction the offline run consumed |

Re-runnable only on this host: the scripts need the v1.6.4 image, `/rag/cache`, and
the two bulk endpoints.
