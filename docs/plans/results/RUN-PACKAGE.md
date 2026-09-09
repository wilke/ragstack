# Chunking study — run package for another site

*Written 2026-09-09 from the state of `coconut` on that date. Everything below was measured or
read; where a fact is not recorded anywhere it says **not recorded** rather than an estimate.
This document is a **package**, not a result: for what the study found, read
[`README.md`](README.md) and [`stage0/README.md`](stage0/README.md) first.*

---

## 1. What the study is, and the rule that governs it

The chunking study asks a product question — **how coarse and cheap can a RAG index be while
still serving pointed, evidence-seeking questions as well as a fine index does** — and answers
it at the *passage* level rather than the document level, because every Phase-0 metric was a
document metric and the one passage-level reading it managed found the top-ranked chunk landing
in the answering section only 55–65 % of the time. The instrument is a pre-registered
confirmation run ([`design/SPEC-confirmation-run.md`](design/SPEC-confirmation-run.md) rev. 2,
amended by [`design/SPEC-confirmation-run-r3.md`](design/SPEC-confirmation-run-r3.md)): **90 TREC
CDS 2014–2016 topics split 10 development / 80 confirmation**, six index arms plus five delivery
arms over one shared **32,663-document PMC Open Access corpus**, three separable retrieval modes
(`vector` / `bm25` / `hybrid`), a **16,384-SFR-token** delivery budget, and two paired endpoints
computed behind the reranker on the packed context — `ERET@B` (of a topic's evidence-bearing
documents, the fraction with at least one chunk admitted) and `EPACK@B` (over the per-contrast
*intersection* of documents both arms reached, the mean fraction of a document's evidence units
fully contained). The decision family is non-inferiority (N1 512→1024, N3 512→2048) at
**ε = 0.05, α = 0.025**, conjunctive across both endpoints, plus a superiority family
(R1 256−2048, R2 headers, R3 `parent256`, R4 `nbr1_512`) under Holm. Stage 0, the development-set
calibration, returned `GATE-NOT-EVALUABLE`; revision 3 split the endpoint in response; Stage 0b′
then failed the gate **on power, not on levels** (joint power 0.37–0.56 at n = 80 on five of six
contrasts, and TREC CDS has only 90 topics in total).

**The pre-registration rule that must be respected.** The 80 confirmation topics are
**quarantined**. No aggregate metric over a confirmation topic may be computed, printed, logged
or committed until (i) the two-reader human label read has produced κ and (ii) the labels are
frozen with the exclusion list fixed. This is enforced in code, not only in prose:
`s0c_common.py` sets `QUARANTINE = True`, and `eret()`, `epack()`, `euc()`, `summarise()` and
`forbid_metric()` all funnel through one choke point `metric()` that **raises
`QuarantineViolation`**; `selftest()` asserts each of the five raises. Falling through with
`QUARANTINE = False` still raises (`NotImplementedError`), so flipping the flag does not silently
enable analysis. Only **counts** — pairs, records, bytes, sha256, wall clock, throughput — may
leave `/rag/tmp/stage0-conf/work/conf/`, whose `QUARANTINE.md` states the rule. **A site
re-running this must carry the guard over intact.** The human read is the single blocking item;
it is 32–48 person-hours and no agent read may substitute for it.

---

## 2. Stage-by-stage workflow

Wall times and hardware are as recorded in each RESULTS document's Provenance / Cost section.
`$BIG` = `/rag/tmp/stage0-conf` (`STAGE0_BIG`). Scripts live in
[`stage0/`](stage0/); helper modules live outside the repo (§5).

| # | stage | script(s) | inputs | outputs | endpoints / models | recorded wall time & hardware | reported in | status |
|---|---|---|---|---|---|---|---|---|
| 0a-1 | corpus plan | `s0_corpus.py` | `$CDS/qrels-treceval-{2014,2015,2016}.txt`, topics XML | `$BIG/work/fetchlist.txt`, `corpus_plan.json` | none | not recorded | `RESULTS-stage0-calibration.md` §1.1 | **done** |
| 0a-2 | fetch | `s0_fetch.py` | fetchlist; `pmc-oa-opendata` S3, versions 1..3, 32 threads; hardlink reuse from Phase-0 dirs | `$BIG/xml/` 32,791 files | S3 only | ~2 min (mtimes 15:40→15:42) | ibid. | **done** |
| 0a-3 | parse + manifest | `s0_parse.py` | `$BIG/xml/*.xml` | `docs.jsonl`, `units.jsonl`, `manifest.json` | none | not recorded | ibid. | **done** |
| 0a-4 | chunk, 6 arms | `s0_chunk.py` | `docs.jsonl` | `$BIG/chunks/spans_<arm>.jsonl` | `FixedTokenWindowChunker` @ `55a0fc2`, SFR tokenizer (`hf` backend asserted) | **207.8 s over 32 processes** | ibid. §1.2 | **done** |
| 0a-5 | embed, 6 arms | `s0_embed.py` | spans + re-verified manifest hash | `$BIG/emb/emb_<arm>.npy`, `rows_<arm>.json`, `estats_*.json` | SFR `:9001`–`:9006`, ≤ 2 in flight each | **1.926 fleet-hours** = **≈ 11.6 device-GPU-hours**, 1.132 B SFR tokens at 167 k tok/s, **168,134 requests, 0 retries** | ibid. §1.3 | **done** |
| 0b | dev retrieval / pack / label / score / gates (rev. 2) | `s0_retrieve.py`, `s0_pack.py`, `s0_label.py`, `s0_score.py`, `s0_labelgates.py`, `s0_checks.py`, `s0_floor.py`, `s0_stats.py`, `s0_rdev.py`, `s0_report.py` — driver `run_0b.sh` | arm embeddings, 10 dev topics × 2 variants | `pool_<arm>.json`, `packed.json`, `labels.jsonl`, `euc.json`, `floor_diagnostic.json`, `stage0_table.json`, `TABLE-8.5.7.md` | brute-force cosine; `:50052` full-pool rerank (9,000 pairs); labeler `mango:8003`, ≤ 4 concurrent; `mango:8003/tokenize` for budgets | labeling **972 requests, 11.36 M prompt tokens, ~56 min LLM time** | `RESULTS-stage0-calibration.md` | **done** — `GATE-NOT-EVALUABLE` |
| r3 | relabel, quote-primary, 2 judges | `s0_label_r3.py`, `s0_labelgates_r3.py` | 308 dev pairs (208 pooled + 100 bias-bound) | `artifacts/r3/labels-r3-{scout,qwen}.jsonl`, `gates-r3.json` | `mango:8003` + `mango:8004`, ≤ 4 each, temp 0, seed `20260914` | **47 min end-to-end**, 879 requests, 10.31 M prompt / 1.78 M completion tokens, $0 | `RESULTS-stage0b-relabel.md` | **done** — neither judge passed |
| r3.1 | relabel, whole-sentence anchors ×5 | `s0_label_r31.py`, `s0_labelgates_r31.py` | same 308 pairs | `artifacts/r31/` — 1,540 records/judge | same two judges | **3 h 22 min**, 3,782 requests, 49.47 M / 7.43 M tokens, $0 | `RESULTS-stage0b-relabel-r31.md` | **done** — copy gate passes, self-consistency fails |
| r3.1-ext | Scout ×20, Qwen ×10 | `s0_label_r31.py --presentations`, `s0_labelgates_r31ext.py` | r3.1 records + 15 new Scout / 5 new Qwen per pair | `artifacts/r31ext/` — **6,160 + 3,080 = 9,240 readings** | same two judges | **3 h 30 min wall = 19.7 service-hours**; Scout **1.73 s/record**, Qwen **7.73 s/record**; 7,153 requests, 90.64 M / 7.13 M tokens, $0 | `RESULTS-stage0b-relabel-r31ext.md` | **done** — graded support reliability **0.9205 at 30 readings** |
| judges | Claude third family | `s0_label_claude.py`, `s0_gates_claude.py` | same 308 pairs, k = 0 | `artifacts/claude/` | Claude Code CLI 2.1.263 headless — `claude-sonnet-5`, `claude-opus-5`, `claude-fable-5-1`; ≤ 3 in flight | 33 + 30 + 7 min; 867 calls; **$195.32** ($0.133 / $0.302 / $0.245 per pair) | `RESULTS-stage0b-claude-judges.md` | **done** — Fable stopped at 251/308 on the account session limit |
| pointed-gen | pointed query population, dev | `s0_pointed_gen.py` | 3,483 usable dev source documents; `pilots/idf_oa10k.json` | `artifacts/pointed/pointed-dev.jsonl` (**177 accepted of 400, 44.2 %**), `-rejected` (223), `-raw` (400) | `mango:8003` only, ≤ 4 in flight; A/B/C/D passes | **10 min 19 s** (628.6 s incl. load), 1,578 requests, 1.46 M prompt tokens, $0 | `RESULTS-stage0b-pointed-gen.md` | **done** |
| 0b′ | calibration re-run, split endpoints | `s0b_*.py` — driver `run_0bp.sh` | frozen `emb/` (read-only), `artifacts/r31ext/` gold, pointed gold, dev-tenant `tenant.env` | `artifacts/stage0b-prime/` (`stats.json`, `checks.json`, `es_concordance.json`, `TABLES.md`), `$BIG/work/stage0b-prime/` contexts | `:9001`–`:9006` (197 query embeds, 13 requests, 0.4 s); `:50052` **89,668 pairs in 270 s**; dev-tenant Elasticsearch `:24043` once | **≈ 25 min total**; retrieval 7.5 min, pack 3.6 min, stats 41 s, ES concordance 112 s; **0 new corpus embeddings** | `RESULTS-stage0b-prime.md` | **done** — gate fails on power |
| pointed-scale | reach vs corpus size | `s0s_*.py` — driver `run_s0s.sh` | frozen `emb/` masked by row; `stage0b-prime/query_vectors.npy` | `artifacts/pointed-scale/` (`levels.json`, `fit.json`, `separation.json`, `difficulty.json`) | `:50052` only — **389,697 pairs, 21,364 requests, 0 retries** | **≈ 35 min** (+ 9 min discarded partial), 167 MB written, **0 embeddings** | `RESULTS-pointed-at-scale.md` | **done** — corpus size is not the fix; **step 2 (5× corpus) not run** |
| conf-a | confirmation setup, quarantined | `s0c_common.py`, `s0c_retrieve.py`, `s0c_pack.py`, `s0c_contexts.py`, `s0c_pool.py`, `s0c_span_filter.py` | 160 confirmation queries; frozen `emb/` | `$BIG/work/conf/{queries,pools,contexts,pool,gentok}` — **all quarantined**; committed: `artifacts/conf-a/` counts + manifests | `:9001`–`:9006` (0.4 s, 21,210 tokens); `:50052` **93,061 pairs, 1,128.7 s**; `mango:8003/tokenize` 9,142 calls | retrieval **1,242 s (20.7 min)**, packing **101.8 s**, BM25 tokenization 40.9 s | `RESULTS-confirmation-run-a-setup.md` | **done** |
| span filter | #513 locator fix | `s0c_span_filter.py` | `artifacts/r31ext/` dev labels | `$BIG/work/r31ext-filtered/`, `artifacts/conf-a/span-filter-dev-verification.json` | none | seconds | ibid. §3 | **done** — reliability 0.9205 → 0.9192, Δ = −0.0013 vs ±0.01 → PASS |
| conf-label | 30-reading confirmation labeling | `s0c_label.py`, `s0c_supervise.sh` | `pool/labeling_set.json` — **ids only**, 3,738 pairs | `$BIG/work/conf/labels/labels-conf-{scout,qwen}.jsonl` | `mango:8003` (Scout ×20 = 74,760 records) + `mango:8004` (Qwen ×10 = 37,380) | **Scout FINISHED** 74,760/74,760 at **1.234 s/record** (92,122 s ≈ 25.6 h). **Qwen RUNNING**: 15,774 / 37,380 at **6.037 s/record**, **21,606 remaining, ≈ 36.2 h projected** (2026-09-09 09:18 UTC) | ibid. §4 | **RUNNING** |
| figures | SVG, dependency-free | `fig_stage0.py`, `figlib.py`, `s0s_fig.py` | `floor_diagnostic.json`, `levels.json`, `fit.json` | `stage0/figures/*.svg`, `design/figures/*.svg` | none | seconds | `README.md` § *The figures* | **done** — never rasterised; open in a browser first |
| report | tables spliced into the write-ups | `s0_report.py`, `s0b_report.py` / `s0b_writeup.py`, `s0s_report.py` / `s0s_writeup.py` | the committed JSON | `TABLE-8.5.7.md`, `TABLES.md`, the RESULTS files | none | seconds | — | **done** |
| — | **two-reader human read (item 8)** | `s0_rdev.py` (draw), `s0_rdev_score.py` (κ) | `artifacts/rdev_sample.json` — 100 pairs, seed `20260915` | κ(A–B), the §6.6.4 acceptance table | none | **32–48 person-hours** | `RESULTS-stage0-calibration.md` row 8 | **NOT STARTED — `PENDING-HUMAN`, and it blocks everything downstream** |
| — | hard pointed set | `s0h_single_source.py` (proposed, does not exist) | 74 existing rejected candidates; `docs.jsonl` | a df-screened, difficulty-stratified pointed set | `mango:8003` ≤ 4, SFR ≤ 2, `:50052` ≤ 4 | see §6 | `design/PLAN-hard-pointed-set.md` (**PR #526, not on `main`**) | **NOT STARTED — awaiting owner** |
| — | confirmation run proper (steps 5–7) | — | frozen labels + κ | the five contrasts | — | not recorded | r3 §5 | **NOT STARTED — blocked on the human read** |

---

## 3. Data inventory

**`/rag/tmp/stage0-conf/` — 39 GB total, not committed, not backed up.**

| path | size | records | format | produced by, from what |
|---|---|---|---|---|
| `xml/` | **2.3 GB** | 32,791 files | JATS XML | `s0_fetch.py` from `pmc-oa-opendata` (S3, versions 1..3) + hardlink reuse of Phase-0 dirs. 33,165 wanted, 5,967 reused, 27,198 attempted, 374 misses → **98.87 %** |
| `work/docs.jsonl` | 683 MB | **32,663** | JSONL, one doc | `s0_parse.py` from `xml/`; 128 empty-body exclusions |
| `work/units.jsonl` | 20 MB | 32,663 | JSONL | `s0_parse.py` — structural JATS units |
| `work/manifest.json` | 2.6 MB | 32,663 `(pmcid, sha256)` pairs | JSON | `s0_parse.py`; `manifest_sha256 = b15f059f…4666a8cbf`, re-verified inside the embed process before the first vector |
| `chunks/spans_<arm>.jsonl` | **100 MB**, 6 files | one line per doc | JSONL char spans + SFR token counts | `s0_chunk.py`. 256: 15.9 MB · 512/0: 8.6 MB · 1024/0: 5.1 MB · 2048/0: 3.2 MB · `fixed_tok512`: 9.6 MB · `header512`: 61.7 MB (carries headers) |
| **`emb/emb_<arm>.npy`** | **34 GB**, 6 files | 2,216,716 vectors | float32 `N × 4096`, memmapped | `s0_embed.py` on `:9001`–`:9006`. **256** 12.09 GB / 737,698 · **512/0** 6.17 GB / 376,516 · **1024/0** 3.22 GB / 196,247 · **2048/0** 1.74 GB / 106,353 · **`fixed_tok512`** (512/64, shipping) 6.94 GB / 423,386 · **`header512`** 6.17 GB / 376,516 |
| `emb/rows_<arm>.json` | 3.3–23.0 MB | — | row → `(docno, span)` map | `s0_embed.py` |
| `work/dev_queries.npy` | 328 KB | 20 | float32 | `s0_retrieve.py` |
| `work/pool_<arm>.json` | ~100 KB × 6 | 10 topics × 2 variants × 50 | JSON, reranked | `s0_retrieve.py` |
| `work/labels.jsonl` (= `artifacts/labels-dev.jsonl`) | 356 KB | **308** | JSONL | `s0_label.py`, Scout on `mango:8003` |
| `work/RDEV-readsheet-{A,B}.html` | 15 MB each | 100 pairs | HTML, per-reader shuffled | `s0_rdev.py`; **not committed**, regenerable from `artifacts/rdev_sample.json` |
| `work/r3/` · `r31/` · `r31ext/` · `r31ext-filtered/` | 7.6 / 38 / 57 / 24 MB | 308×1 · 308×5 · 6,160+3,080 · filtered copy | JSONL labels + raws | the three relabel harnesses; raws carry 6.3 / 26.9 / 22.3 MB of stripped Qwen thinking and are **not committed** |
| `work/claude/` | 8.5 MB | 308 / 308 / 251 | JSONL + raw CLI JSON | `s0_label_claude.py` |
| `work/pointed/` | 75 MB | `dev_docs.jsonl` 3,483; `pointed-dev.jsonl` **177**; rejected 223; raw 400 | JSONL | `s0_pointed_gen.py` on `mango:8003` |
| `work/stage0b-prime/` | 70 MB | pools 197 lines × 6; **12,214 packed contexts**; 36,642 endpoint records | JSONL(.gz) | `s0b_*.py` |
| `work/pointed-scale/` | 167 MB | pools 1,770 lines × 6; 63,720 contexts | JSONL(.gz) | `s0s_*.py` |
| **`work/conf/`** | **614 MB** | see below | **QUARANTINED** | `s0c_*.py` |
| ├ `conf/queries/` | 2.6 MB | 160 | `query_vectors.npy` + meta | `s0c_retrieve.py` |
| ├ `conf/pools/` | 18 MB | 160 lines × 6 arms | JSONL, 3 modes, reranked, sha256'd | `s0c_retrieve.py` |
| ├ `conf/contexts/` | 37 MB | 9,920 records | `packed-conf.jsonl.gz` (6,608,093 B) + `text/<arm>.jsonl.gz` × 10 | `s0c_pack.py`, `s0c_contexts.py` |
| ├ `conf/pool/labeling_set.json` | 46 KB | **3,738 pairs** (2,962 pooled + 776 bias-bound) over 3,490 documents | ids only | `s0c_pool.py` over **76 distinct rankings** |
| └ `conf/labels/` | **557 MB** | `labels-conf-scout.jsonl` **74,760** (193 MB); `labels-conf-qwen.jsonl` **15,763 and growing** (25 MB); raws 104 MB + 235 MB | JSONL | `s0c_label.py` — **live** |

**Committed in the repo** (`stage0/artifacts/`, ~40 MB): `manifest.json`, `provenance-stage0.json`,
`labels-dev.jsonl`, `label_gates.json`, `checks.json`, `floor_diagnostic.json`,
`stage0_table.json`, `rdev_sample.json`, the blank verdict sheets, and the per-run subdirs
`r3/` (928 K), `r31/` (6.8 M), `r31ext/` (23 M), `claude/` (2.5 M), `pointed/` (1.8 M),
`pointed-scale/` (180 K), `stage0b-prime/` (424 K), `conf-a/` (64 K — counts and manifests only),
`rdev-pilot-read/` (276 K).

**Must the 34 GB of embeddings be recomputed at another site? Yes — but they are deterministic
model outputs, not decisions.** `emb_<arm>.npy` is exactly what `s0_embed.py` gets by sending
`spans_<arm>.jsonl` to an OpenAI-compatible `/v1/embeddings` serving
`Salesforce/SFR-Embedding-Mistral`. Given the same corpus (verified by `manifest_sha256`), the
same chunker at `55a0fc2` and the same served model, the matrices reproduce. Nothing else in the
tree depends on the bytes being *identical* — Stage 0b′ and pointed-at-scale re-derive their
numbers from them and check the CDS query vectors reproduce at **minimum cosine 1.000000**.
**Recorded cost to rebuild: 1.926 fleet-hours across six endpoints = ≈ 11.6 device-GPU-hours,
1.132 B SFR tokens at 167 k tok/s, 168,134 requests, 0 retries.** Chunking adds 207.8 s over 32
processes; fetch and parse add roughly a further hour plus 33k S3 fetches. Budget **~35 GB of
disk for `emb/` alone, ~39 GB for the whole tree.**

---

## 4. Models, endpoints and containers

| role | model id | how it is served here | limits the scripts assume |
|---|---|---|---|
| **embedder** | `Salesforce/SFR-Embedding-Mistral`, `max_model_len` 4096 | six vLLM instances, one per GPU 0–5, ports **9001–9006**, keyless, restarted by `/rag/cache/vllm_watchdog_lucid6.sh` | **≤ 2 requests in flight per endpoint**, six endpoints round-robin |
| **reranker** | `BAAI/bge-reranker-v2-m3` | apptainer instance `crossencoder` from `/rag/apptainer/images/python.sif` (41 MB, 2026-06-23), `uvicorn main:app --port 50052`, on GPU 0 | **≤ 4 in flight**; truncates at 4,096 tokens per query–chunk pair |
| **labeling judge 1 / generator** | `RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic`, `max_model_len` 60000 | `http://mango.cels.anl.gov:8003`, OpenAI-compatible chat + `/tokenize` | **≤ 4 in flight**, temperature 0, `max_tokens` 3000 |
| **labeling judge 2 (reasoning)** | `Qwen/Qwen3.6-35B-A3B`, `max_model_len` 131072 | `http://mango.cels.anl.gov:8004` | **the server admits 4** (PR #527); the live run is at **concurrency 5**. `max_tokens` 12000; thinking arrives in a sibling `reasoning` field and is stripped before parsing |
| **third-family judges** | `claude-sonnet-5`, `claude-opus-5`, `claude-fable-5-1` | Claude Code CLI **2.1.263** headless, on the owner's account — the Argo gateway in `/rag/llm-api.env` is unreachable from this host | **≤ 3 in flight**, 300 s timeout, one retry on transport error only |

**The recorded SFR serve command**, verbatim from the running processes and from
`/rag/cache/vllm_watchdog_lucid6.sh` (gpu = port − 9001):

```bash
CUDA_VISIBLE_DEVICES=$gpu /rag/envs/vllm/bin/vllm serve Salesforce/SFR-Embedding-Mistral \
  --runner pooling --port $port --gpu-memory-utilization 0.9 \
  --served-model-name Salesforce/SFR-Embedding-Mistral
```

`--task embed` is invalid in vLLM 0.23; `--runner pooling` is required.

**The crossencoder launch**, from `apptainer/sidecars-up.sh`:

```bash
apptainer instance run --bind "$SIDECARS_SRC/crossencoder:/app:ro" \
    --bind "$DATA/crossencoder/deps:/deps:ro" --bind "$DATA/crossencoder/cache:/cache" \
    --nv --env CUDA_VISIBLE_DEVICES=0 --env PYTHONPATH=/deps --env HF_HOME=/cache \
    --env MODEL_NAME=BAAI/bge-reranker-v2-m3 --env DEVICE=cuda --env PORT=50052 \
    "$SIF" crossencoder \
    /bin/sh -c 'cd /app && exec /usr/local/bin/python -m uvicorn main:app --host 0.0.0.0 --port 50052'
```

**The Claude judge invocation**, verbatim (prompt on **stdin** — `--tools ""` is variadic and
swallows a positional prompt; `--bare` skips the keychain read and fails):

```bash
echo "$PROMPT" | claude -p --model claude-opus-5 --output-format json \
  --restricted --no-session-persistence --strict-mcp-config --tools "" \
  --system-prompt "$SYSTEM"        # cwd=/tmp
```

Without those isolation flags a real pair costs **$0.51** instead of ~$0.18, because Claude
Code's system prompt, tool definitions and the repo `CLAUDE.md` enter the judge's context.

**GPU memory observed** (2026-09-09): 8 × H200 NVL, 143,771 MiB each. GPU 0 **142,463 MiB used**
(an SFR endpoint at 0.9 utilization *plus* the reranker — only ~1.3 GB headroom); GPUs 1–5
130,637–130,697 MiB; **GPUs 6 and 7 at 0 MiB and reserved** — every stage asserts they are
untouched before and after.

**What another site must provide, and where to point it.** All four are HTTP; none is a library
dependency:

1. an OpenAI-compatible **`/v1/embeddings`** serving SFR (or a substitute — but then every
   recorded token count and every 4,096-token truncation claim is void);
2. an OpenAI-compatible **chat** endpoint per judge and per generator;
3. an HTTP **crossencoder** sidecar answering `/health` with `{"status","model"}`;
4. read access to TREC CDS qrels and topics.

The constants to change are all in **`stage0/s0_common.py`**:

```python
BIG = pathlib.Path(os.environ.get("STAGE0_BIG", "/rag/tmp/stage0-conf"))
REPO = "/home/wilke/Development/ragstack"
EXPECT_COMMIT = "55a0fc2f6bf64e592c2c65d8825524216c423e2b"
SFR_PORTS = list(range(9001, 9007));  SFR_MODEL = "Salesforce/SFR-Embedding-Mistral"
RERANK_URL = "http://localhost:50052"; RERANK_EXPECT = "BAAI/bge-reranker-v2-m3"
MANGO = "http://mango.cels.anl.gov:8003"; SCOUT_EXPECT = "RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic"
MANGO_QWEN = "http://mango.cels.anl.gov:8004"; QWEN_EXPECT = "Qwen/Qwen3.6-35B-A3B"
DEPTH = 50; BUDGETS = (2048, 4096, 8192); PRIMARY_BUDGET = 4096; EPS = 0.05
CDS = PHASE0 / "cds"      # TREC CDS topics + qrels — in the repo since this PR (docs/plans/results/cds)
```

and in **`stage0/s0b_common.py`** for the revision-3 shape:

```python
HELPERS = pathlib.Path(os.environ.get("STAGE0_HELPERS",
              "/home/wilke/Development/worktrees/phase0-rescue/phase0"))
LEG_DEPTH = 100; DEPTH = 50; RRF_K = 60
BUDGETS = (4096, 16384, 32768); PRIMARY_BUDGET = 16384
EPS = 0.05; WINDOW = (0.15, 0.90); UNIT_CAP_PER_DOC = 4; SEED_UNITCAP_R3 = 20260918
BM25_K1 = 1.2; BM25_B = 0.75; SFR_PER_GEN = 2048 / 1630.0
```

Only one store is ever contacted, once: `s0b_es.py` loads one arm into the **dev tenant's**
Elasticsearch at `:24043` (URL read from `/rag/data/tenants/dev/config/tenant.env`, with
`:9200` / `:6333` / `:24041` refused by an explicit check) for the BM25 concordance check, then
deletes the index with a verifying listing. Nothing else constructs a store client. **Note for
any site: unset store URLs on this host resolve to production — point them at
`http://127.0.0.1:1` so a leak fails loudly.**

**Seeds**, all of them: grade-0 dev `20260904`, grade-0 confirmation `20260912`, unit cap
`20260912` (r3: `20260918`), bootstrap `20260913`, labeling duplicates / presentation order
`20260914`, R-dev draw `20260915`, bias-bound sample `20260916`, pointed subsample `20260917`,
pointed generation `20260918`. Presentation order is `SEED_LABELDUP + 100·k + pair_index`.

---

## 5. Environment

Both conda environments are **CPython 3.12.13**.

| package | `/rag/envs/ragstack` (the harness) | `/rag/envs/vllm` (the servers) |
|---|---|---|
| numpy | **2.5.0** | 2.3.5 |
| transformers / tokenizers | **5.12.1 / 0.22.2** | 5.12.1 / 0.22.2 |
| httpx | 0.28.1 | — |
| elasticsearch | 8.19.3 | absent |
| qdrant-client | 1.18.0 | absent |
| nltk | 3.9.4 | absent |
| torch | **absent** | **2.11.0** |
| vllm | absent | **0.23.0** |
| tiktoken | absent | 0.13.0 |
| **scipy** | **absent** | absent |
| **lxml, rank-bm25, bm25s, sentence-transformers** | **absent** | absent |

Two absences are load-bearing and deliberate: **`scipy` is not installed**, so every
distribution function (χ², Student-t, non-central t, Wilson, bootstrap) is hand-rolled in
`s0_math.py` and **self-validates against the SPEC's published tables on import** — the gate
refuses to run if a published cell fails to reproduce. **`matplotlib` is not installed**, so
`figlib.py` emits hand-written SVG. No BM25 library is installed either; `s0b_bm25.py` is
in-process, pinned (`k1 = 1.2`, `b = 0.75`, `\w+` Unicode tokenizer, no stemming, no stopwords)
and checked against Elasticsearch at overlap@50 **0.9469** against a 0.90 bar.

**Two helper directories live outside the repository, and the harness will not import without
them.**

- `/home/wilke/Development/worktrees/phase0-rescue/phase0/stage1/` — `stage1_common.py`
  (`Fleet`, `CE`, `doc_text`), plus the 155-file Leg A run record (~10 MB).
- `/home/wilke/Development/worktrees/phase0-rescue/phase0/pilots/` — `pilot_common.py`
  (`units_for_article`), `legb2_rules.py`, `legb2_gen.py` (`PARAPHRASE_PROMPT`),
  `verifier_prompt.txt`, and **`idf_oa10k.json`** (93,347 terms over 10,000 PMC OA
  title+abstracts, sha256 `90759aec…15d6f2bd`) — ~22 MB.
- `/home/wilke/Development/worktrees/phase0-rescue/phase0/cds/` — **the TREC CDS topics and
  qrels**: `qrels-treceval-{2014,2015,2016}.txt`, `topics2014.xml`, `topics-2015-A.xml`,
  `topics2016.xml`, `topics_merged.json`. This is what `C.CDS` means.

**Until 2026-09-09 that directory was the only copy** — `phase0-rescue` is a plain directory, not
a git worktree, not under version control, on no remote. **The PR that adds this document also
commits the minimal set into the repository, where `s0_common.py` already looks for it:**

| in the repo | what | from |
|---|---|---|
| `stage1/stage1_common.py` | `Fleet`, `CE`, `doc_text`, `pin_repo` | `phase0/stage1/` |
| `pilots/pilot_common.py`, `legb2_rules.py`, `legb2_gen.py`, `mango.py`, `verifier_prompt.txt`, `idf_oa10k.json` (3.0 MB) | the Leg B rules, the generator prompts, the IDF table | `phase0/pilots/` |
| `cds/qrels-treceval-{2014,2015,2016}.txt`, `topics2014.xml`, `topics-2015-A.xml`, `topics2016.xml`, `topics_merged.json` (1.9 MB) | the 90 TREC CDS topics and their qrels (public NIST data) | `phase0/cds/` |

Verified on 2026-09-09: with **no** `STAGE0_HELPERS` set, `import s0_common` from the checkout
resolves `C.CDS` to `docs/plans/results/cds` (90 topics) and `s0_math` self-validates; with
`STAGE0_HELPERS=<repo>/docs/plans/results`, `s0b_common`, `s0s_common` and `s0_pointed_gen` import
and find `idf_oa10k.json` and `legb2_gen.py`. **Another site therefore needs only the repository
checkout for code and reference data** — the fetched XML, the chunks and the embeddings are
regenerated per §3. The full unversioned tree (2.3 GB with its Leg A/B run records, without the
fetched XML) is archived at `/rag/backups/phase0-rescue-2026-09-09-noxml.tar.gz` (1.76 GB) on
coconut as the historical record.

Every reproduce command sets the same four:

```bash
export HF_HOME=/rag/cache                                   # /home is NFS and space-constrained
export PYTHONPATH=/home/wilke/Development/ragstack/python   # #432: the editable install in both
                                                            # envs points at a legacy production checkout
export STAGE0_HELPERS=/home/wilke/Development/ragstack/docs/plans/results   # in-repo since 2026-09-09
# (the historical value was the unversioned ~/Development/worktrees/phase0-rescue/phase0)
export STAGE0_BIG=/rag/tmp/stage0-conf                      # optional; this is the default
PY=/rag/envs/ragstack/bin/python3
```

One more trap: `s0_common.provenance()` **aborts** unless `git rev-parse HEAD` equals
`EXPECT_COMMIT = 55a0fc2`. The main checkout is currently on `ops/reboot-2026-09-10` at
`772b7a4`, so any script calling it from there exits immediately. The later harnesses
(`s0_pointed_gen.py`, `s0b_*`, `s0s_*`) record both commits and assert instead that
`git diff 55a0fc2..HEAD -- python/ragstack/ingestion/chunkers.py` is **empty** — the segmenter is
the one file whose drift moves a label.

---

## 6. Compute sizing for a larger site

Sized from the measured rates above, not from estimates.

| work item | quantity | rate as measured | projected cost | parallelises across GPUs? |
|---|---|---|---|---|
| **Qwen confirmation labeling, remaining** | **21,606 records** of 37,380 | **6.037 s/record** at concurrency 5 on one server | **≈ 36.2 h** (the live projection). Scout's half is **finished** (74,760 records, 1.234 s/record, 25.6 h) | **Yes, and this is the biggest single win** — the record loop is embarrassingly parallel over `(topic, docno, presentation)` and idempotent per key. Four Qwen replicas ≈ 9 h. The current run's 19.7 service-hours-per-3.5-wall-hours ratio at r3.1-ext shows the pattern already |
| **rebuild the corpus + six index arms from scratch** | 32,663 docs, 2,216,716 chunks, 1.132 B SFR tokens | 167 k tok/s across six endpoints | **1.93 fleet-hours = 11.6 device-GPU-hours** + 207.8 s chunking + ~1 h fetch/parse; **~39 GB disk** | **Yes** — linear in endpoint count; the six-endpoint fleet is the unit |
| **re-embed in the generator's tokenizer** (r3 §3.3, required for the confirmation run proper) | same corpus, re-chunked | same | **≈ 1.93 fleet-hours**, quoted from Stage 0a | Yes |
| **Stage 0b′-shaped scoring pass** (6 arms × 3 modes × 2 rerank × 3 budgets) | 197 queries | 89,668 rerank pairs at 332 pairs/s | **≈ 25 min**, zero embeddings | Reranker only; ≤ 4 in flight per sidecar, so scale by adding sidecars |
| **hard pointed set** (`PLAN-hard-pointed-set.md` §5) | step 0 pilot ≈ **1 h**; step 1 dev generation ≈ **2 h** Scout; step 2 calibrate ≈ **40 min**; step 4 confirmation generation **4.5–7 h** Scout unattended; step 5 human read **10–15 person-h**; step 6 Stage 2 ≈ **1 h** | — | **≈ 8–11 h machine + 10–15 person-hours. New corpus embeddings: none, at every step** | Generation parallelises over `mango` replicas at ≤ 4 in flight each; retrieval over reranker replicas |
| **the 5× corpus embed — NOT RUN** | ≈ 120k S3 fetches, ≈ 6 B SFR tokens | — | **≈ 10 GPU-hours, ≈ +130 GB**, plus a day of fetch/parse/chunk | Yes. **`STEP 2 DOES NOT RUN`** — it buys a population *further apart in absolute terms and harder to separate statistically*. Scaling to 500k instead: **≈ 40 GPU-hours, ≈ +510 GB** |
| **two-reader human read** | 100 CDS pairs (+ ≈ 50 pointed) × 2 readers | — | **32–48 person-hours** (CDS) + 10–15 (pointed) | **No.** This is the blocking item and no GPU shortens it |

**Overrule condition on the 5× decision, recorded so it can be checked rather than trusted:** if
the logit fit is the right functional form *and* the decay accelerates below 0.90, the population
would leave the ceiling somewhere past 500k — the 500k logit projections (0.721–0.838, intervals
reaching to 0.26) are the first numbers in the study plausibly *inside* the [0.15, 0.90] window.
**If the owner wants the pointed population as a gate rather than a descriptive set, 500k is the
size worth buying and 150k is not.** But §5's separation table says the arms still will not
separate, and that argument does not depend on corpus size at all — which is why
`PLAN-hard-pointed-set.md` proposes **query hardness** as the lever instead.

**What a larger site cannot buy.** TREC CDS has **90 topics in total**; the confirmatory family
needs 286–453 (σ bound) or 140–220 (bootstrap). More compute does not produce more topics. The
routes out are r3 §10 item 5's (a) run at n = 80 with projected power printed, (b) a harder
pointed population, or (c) re-scope the confirmatory family to R2.

---

## 7. Links

**Design and pre-registration** — [`design/SPEC-confirmation-run.md`](design/SPEC-confirmation-run.md) (rev. 2) ·
[`design/SPEC-confirmation-run-r3.md`](design/SPEC-confirmation-run-r3.md) (rev. 3 — §3 endpoints,
§5 order of operations, §10 open decisions, §11 pointed population) ·
[`design/RUBRIC-evidence.md`](design/RUBRIC-evidence.md) (frozen, sha256 `2e11f368…c747363b`) ·
[`design/SPEC-synthesis-stage.md`](design/SPEC-synthesis-stage.md) ·
[`design/REVIEW-synthesis-stage.md`](design/REVIEW-synthesis-stage.md) ·
`design/PLAN-hard-pointed-set.md` — **not on `main`**; it is commit `38f5ff9` on branch
`plan/hard-pointed-set`, open as **PR #526** (`git show plan/hard-pointed-set:docs/plans/results/design/PLAN-hard-pointed-set.md`).

**Design analyses** — [`design/ANSWER-completeness-and-subsets.md`](design/ANSWER-completeness-and-subsets.md) ·
[`design/ANSWER-sufficiency-and-judges.md`](design/ANSWER-sufficiency-and-judges.md) ·
[`design/ANSWER-provenance-and-repro.md`](design/ANSWER-provenance-and-repro.md) ·
[`design/ANSWER-visualisation-audit.md`](design/ANSWER-visualisation-audit.md).

**Run reports** — [`README.md`](README.md) (index; read § *Conclusions that were later revised* before
quoting anything) · [`stage0/README.md`](stage0/README.md) ·
[`stage0/RESULTS-stage0-calibration.md`](stage0/RESULTS-stage0-calibration.md) ·
[`stage0/TABLE-8.5.7.md`](stage0/TABLE-8.5.7.md) ·
[`stage0/RESULTS-stage0b-relabel.md`](stage0/RESULTS-stage0b-relabel.md) ·
[`stage0/RESULTS-stage0b-relabel-r31.md`](stage0/RESULTS-stage0b-relabel-r31.md) ·
[`stage0/RESULTS-stage0b-relabel-r31ext.md`](stage0/RESULTS-stage0b-relabel-r31ext.md) ·
[`stage0/RESULTS-stage0b-claude-judges.md`](stage0/RESULTS-stage0b-claude-judges.md) ·
[`stage0/RESULTS-stage0b-pointed-gen.md`](stage0/RESULTS-stage0b-pointed-gen.md) ·
[`stage0/RESULTS-stage0b-prime.md`](stage0/RESULTS-stage0b-prime.md) ·
[`stage0/RESULTS-pointed-at-scale.md`](stage0/RESULTS-pointed-at-scale.md) ·
[`stage0/RESULTS-confirmation-run-a-setup.md`](stage0/RESULTS-confirmation-run-a-setup.md).
Earlier legs: [`step1/`](step1/) · [`step2/`](step2/) · [`step3/`](step3/) · [`stage1/`](stage1/) ·
[`stage1-legB/`](stage1-legB/) · [`pilots/`](pilots/) · [`rescore/`](rescore/) · [`breadth-k/`](breadth-k/).
Plans: [`../chunking-evaluation.md`](../chunking-evaluation.md) · [`../long-doc-judged-set.md`](../long-doc-judged-set.md) ·
[`../grading-ui.md`](../grading-ui.md) · [`../../../HANDOFF-2026-09-06.md`](../../../HANDOFF-2026-09-06.md).

**Artifact (the public write-up)** — `https://claude.ai/code/artifact/e5b3169b-4897-48f7-b350-2de7276caff0`
(*RAGStack Chunking Study*; republish to this URL). Source of truth:
`~/Development/worktrees/phase0-rescue/artifact/chunking-study-report.html`. The earlier page
`…/artifact/54a6dc7f-9998-4306-8350-82da9a843e2b` is superseded.

**Pull requests that landed the stages** (merged unless marked):

| # | title |
|---|---|
| 499 | docs(plans): confirmation run revision 3 — the population is declared, the endpoint is split |
| 500 | feat(stage0): scorer for the two-reader R-dev read — κ, rates, and the §6.6.4 table |
| 501 | feat(stage0): relabel the development set under the revision-3 protocol — quote-primary, two judges |
| 503 | docs(plans): r3 §11 — the pointed-question population is added, with its four guards |
| 504 | docs(plans): grading in the RAGStack UI — plan; r3 decision 3; pilot sheet source in-repo |
| 505 | feat(contracts): grading resources — schemas, OpenAPI, fixtures, conformance (phase 1) |
| 506 | feat(stage0): the pointed-question set on the development topics — generation and screens (r3 §11) |
| 507 | feat(stage0): relabel r3.1 — whole-sentence anchors, five presentations, union saturation |
| 508 | docs(plans): r3 status after r3.1 — copy gate passes, 'where' is diffuse on CDS; §10 item 4 |
| 509 | feat(grading): Python implementation of /v1/grading — store backends, router, importer (phase 2) |
| 511 | feat(frontend): Grading view — pair read, verdict bar, adjudication, export (phase 3) |
| 512 | feat(stage0): relabel r3.1 extension — Scout ×20, Qwen ×10; reliability of graded support and saturation |
| 514 | feat(stage0): Claude judges via the CLI — Sonnet 5, Opus 5, Fable 5.1, one reading each (r3 §10 item 4) |
| 517 / 518 | docs(plans): the synthesis stage — measure the answer, not only the evidence (+ after review) |
| 520 | feat(stage0): Stage 0b′ — three-mode harness, split endpoints, both populations, on the existing indexes |
| 521 / 522 | docs(plans): r3 status after Stage 0b′ — the gate fails on power; §10 item 5 (a)+(b) started, (c) planned |
| 523 | feat(stage0): confirmation run (a) — quarantined retrieval, pooling, and the 30-reading labeling launched |
| 524 | feat(stage0): the pointed population at corpus scale — reach vs size, and the 5× pilot |
| 525 | docs(plans): r3 §10 — (b) measured; item 6: a hard pointed set |
| **526** | **OPEN** — docs(plans): the hard pointed set — explanation and plan (r3 §10 item 6) |
| **527** | **OPEN** — stage0(conf): Qwen labeler concurrency — server admits 4, run at 5; resume-safe truncation |

(#502, #510, #513, #515, #516, #519 exist but are not stage-0 study PRs; #513 is the locator bug
this package's span filter fixes.)

**Memory notes** (`~/.claude/projects/-home-wilke-Development-ragstack/memory/`) —
`project_chunking_study_state.md` (the running state of this study, and the traps) ·
`reference_claude_cli_judge.md` (the judge CLI flags, pitfalls and measured cost) ·
`project_hardware.md` · `reference_cache_layout.md` (`HF_HOME=/rag/cache`; `/home` is NFS) ·
`feedback_worktree_location.md` · `feedback_service_teardown.md` (**never stop by process-name
pattern**) · `feedback_test_target_dev_tenant.md` · `project_prod_api_topology.md` ·
`feedback_run_the_experiment.md` · `feedback_persistence_model.md` ·
`project_live_validation_state.md` · `reference_gowe_deployment.md`.

---

### Four things to check before the first command at another site

1. **The human read is the blocking item.** Everything after `s0c_label.py` waits on κ. Schedule
   it first; it is people-time, not machine-time.
2. **Carry the quarantine guard over intact** (`s0c_common.py`), and keep confirmation artifacts
   behind their own `QUARANTINE.md`.
3. **Set `STAGE0_HELPERS` to the checkout's `docs/plans/results`** — the helper modules and the
   TREC CDS topics/qrels are in the repository as of 2026-09-09 (§5); before that they lived in an
   unversioned directory on one host.
4. **Never stop a service by process-name pattern**, and point unset store URLs at
   `http://127.0.0.1:1`. Both rules exist because breaking them once took down a production fleet.
