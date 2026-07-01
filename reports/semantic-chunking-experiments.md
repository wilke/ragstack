# Semantic chunking — ingest throughput experiments

Testing & experiment record for the semantic-ingest performance investigation:
where the wall-clock actually goes, and whether a cheaper breakpoint-embedding
model is viable.

## Provenance

| Field | Value |
|---|---|
| Date | 2026-07-01 |
| Host | `coconut` (8× H200 NVL) |
| Author | wilke (Claude Code session) |
| Repo code under test | `/rag/repos/ragstack` @ `main` `30018a4` (has #67/#68/#70); breakpoint logic unchanged since PR #36 |
| Committed harness | `python/scripts/eval/profile_semantic_cpu.py`, `python/scripts/eval/breakpoint_model_compare.py` |
| Python env | `/rag/envs/ragstack` — Python 3.12.13, `transformers` 5.12.1 (tokenizer-only; no torch) |
| Expensive model | `Salesforce/SFR-Embedding-Mistral` (7B), vLLM on `localhost:9001–9008`, `max_model_len=4096`, 4096-dim |
| Cheap model | `BAAI/bge-base-en-v1.5`, embedding sidecar on `localhost:50053`, 512-token context, 768-dim |
| Input corpus | `/rag/ingest/inputs/09320c55-a8a7-4f4d-81b3-ae55b7a329fa.jsonl` (~478 MB, one JSON doc per line) |
| Prod chunker config | `--chunk-method semantic --chunk-buffer-size 3 --chunk-breakpoint-percentile 80 --chunk-min-length 500`, hf token counter (default), `--concurrency 8 --batch-size 64`, 8-way embed fan-out |
| Prod throughput at time of test | ~1 doc/s aggregate; 6.8k of ~10.4k docs indexed on file 1 |

> **Env gotcha (affects reproduction):** the HF token counter needs `transformers`.
> `/rag/envs/ragstack` has it; the base `miniconda3` env does not and silently
> falls back to the *estimate* counter, which undercounts token-counting CPU cost
> by ~250× (0.24 ms vs 63 ms per doc). Always run these with `/rag/envs/ragstack`.

## Background & rationale

PR #67 fanned the semantic **breakpoint embedding** across the 8 SFR endpoints
(2.06×, all GPUs vs 1-pinned). PR #68 (#66 phase-1) moved `chunker.chunk()` off
the event loop so embed+upsert workers overlap CPU splitting. The prod session
reported the semantic build still **bursty between docs** and asked whether #66
**phase-2** (`--chunk-concurrency`, concurrent chunking) would speed it up — and,
separately, whether a **cheap model** could do the breakpoint embed (#73).

Two questions, two experiments:

1. **Is CPU chunking the bottleneck?** (decides whether phase-2 helps)
2. **Does a cheap model place the same chunk boundaries as SFR?** (decides #73)

### How the semantic chunker works (why this matters)

`SemanticChunker.chunk()` (`python/ragstack/ingestion/chunkers.py`):

1. `sentence_spans(text)` — regex sentence split (CPU).
2. For **each sentence** build an overlapping buffer of `2*buffer_size+1 = 7`
   sentences; if over `max_tokens`, bound it (CPU token-count per buffer).
3. `embed_fn(buffers)` — embed **every buffer** (one per sentence) — **GPU**,
   fanned out by #67.
4. Cosine distance between adjacent buffer embeddings → percentile threshold →
   breakpoints → chunk spans → token-split oversized spans (CPU).

The **stored** vectors are a separate, later embed of the ~N final chunks. So the
breakpoint embed of ~N_sentences buffers is pure boundary-finding overhead, and it
is **never stored**.

---

## Experiment 1 — CPU vs GPU split (is phase-2 worth it?)

**Rationale.** Phase-2 (concurrent chunking) only helps if a single chunk thread
can't keep the GPU workers fed. Measure the CPU cost of `chunk()` in isolation and
compare its throughput to the fleet's consumption rate.

**Algorithm.** `profile_semantic_cpu.py`: build the real `SemanticChunker` with prod
params and the SFR hf token counter, but a **mock embed_fn** (instant, content-hashed
vectors → realistic distances, zero GPU). For 120 real docs (≥500 chars) time:
`sentence_spans`, the per-buffer token-count loop, and full `chunk()`. Zero GPU
contention with the live job.

**Metrics.** ms/doc for each phase; derived single-thread chunk throughput (docs/s);
buffers/doc (= breakpoint embeds) vs chunks/doc (= stored embeds).

**Results** (120 docs, medians):

| Quantity | Median | Mean |
|---|---|---|
| chars/doc | 30,816 | 33,454 |
| sentences/doc = **breakpoint embeds/doc** | **373.5** | 390.3 |
| chunks/doc = **stored embeds/doc** | **19** | 20.5 |
| `sentence_spans` | 5.16 ms | 6.13 ms |
| per-buffer token-count | 63.1 ms | 67.3 ms |
| **TOTAL CPU `chunk()`** | **79.5 ms** | 89.7 ms |

- Single-thread chunk throughput ≈ **12.6 docs/s** (CPU only).
- Prod aggregate ≈ **1 doc/s** (8-way GPU).
- CPU is ~**8%** of the ~1 s/doc wall-clock.

**Interpretation.** One chunk thread already runs **~12× faster** than the 8-GPU
fleet consumes. CPU chunking is not the bottleneck; concurrent chunking would queue
behind the same GPU ceiling **and** add more concurrent breakpoint-embed load on the
same endpoints. The ceiling is **GPU embed throughput**, dominated by the ~**374
per-sentence breakpoint embeds/doc (~20× the 19 stored vectors)** on a 7B model.

> **Conclusion:** #66 phase-2 is **not a throughput lever** here (recorded on #71).
> The real levers act on the breakpoint embed: (a) switch to `fixed_tok512` (no
> breakpoint embed at all — eval-blessed as retrieval-indistinguishable), or (b) a
> cheaper breakpoint model (Experiment 2 / #73).

---

## Experiment 2 — cheap vs expensive breakpoint model (#73)

**Rationale.** The breakpoint embeddings only feed cosine distances → boundary
placement; they are never stored. So a cheap model could, in principle, place the
same boundaries at a fraction of the GPU cost. Test whether it does.

**Algorithm.** `breakpoint_model_compare.py`: for 10 real docs (≥2000 chars) build
the per-sentence buffers **once**, embed them with **both** models — SFR pooled
across the 8 live endpoints (`make_pooled_embedder`, least-loaded) and BGE on the
sidecar — then run the **identical** prod breakpoint logic (`_breakpoint_groups` +
`_merge_short`) on each model's distances and compare the resulting boundaries.
Requests sub-batched at 32 to barely perturb the live job (~3.7k SFR embeds total,
spread across 8 endpoints ≈ a few seconds of fleet time).

**Metrics.**
- **Chunk-span Jaccard (primary):** exact `(start,end)` overlap of the semantic
  chunk spans. Chunk ids are `uuid5(doc_id:start:end)`, so exact spans are what
  determine stored identity.
- **Internal-boundary F1 (primary):** precision/recall of interior boundary offsets,
  SFR = reference (exact offset match).
- **Distance Spearman (diagnostic):** rank correlation of the two models' per-adjacent-
  pair distance sequences — threshold-independent signal agreement.
- **chunks/doc (descriptive):** structure/cost parity.

**Per-doc results** (SFR = reference; buffers bounded to SFR's 4096-token context):

| doc | sentences | chunks SFR | chunks BGE | span Jaccard | dist Spearman |
|---:|---:|---:|---:|---:|---:|
| 0 | 86 | 8 | 6 | 0.27 | 0.75 |
| 1 | 136 | 3 | 3 | 0.20 | 0.77 |
| 2 | 1013 | 42 | 45 | 0.05 | 0.73 |
| 3 | 486 | 12 | 16 | 0.08 | 0.76 |
| 4 | 475 | 20 | 27 | 0.15 | 0.78 |
| 5 | 749 | 23 | 21 | 0.02 | 0.80 |
| 6 | 899 | 34 | 36 | 0.08 | 0.77 |
| 7 | 20 | 1 | 2 | 0.00 | 0.73 |
| 8 | 8 | 1 | 2 | 0.00 | 0.21 |
| 9 | 345 | 11 | 11 | 0.00 | 0.81 |

**Aggregate:**

| Metric | Value |
|---|---|
| chunk-span Jaccard (exact) | mean **0.084**, median 0.063 |
| internal-boundary F1 (SFR ref) | **0.349** (precision 0.333, recall 0.366) |
| distance Spearman | mean **0.711**, median 0.762 |
| chunks/doc | SFR 15.5 vs BGE 16.9 |

**Interpretation.**
- The distance **signal is decently correlated** (Spearman 0.71) — BGE sees broadly
  the same semantic structure.
- But **exact boundary placement diverges** (Jaccard 0.08, F1 0.35): a similar rank
  order still crosses the 80th-percentile threshold at different pairs, and a
  one-sentence shift = a different `(start,end)` = zero exact-match credit. So BGE is
  **not a boundary-identical drop-in** for SFR.
- **Confound — BGE's 512-token context vs SFR's 4096.** Long 7-sentence buffers are
  truncated by BGE; the longest docs scored worst (doc 2, 1013 sentences → Jaccard
  0.05). The fair re-test bounds breakpoint buffers to 512 tokens for BGE
  (`CHEAP_MAX_TOKENS=512`) — not yet run.
- **Exact-match may be the wrong bar.** The v0.14/v0.15 chunking evals show boundaries
  barely affect retrieval (`fixed_tok512` statistically indistinguishable from
  semantic). If boundaries aren't retrieval-critical, BGE-semantic chunks are
  plausibly retrieval-equivalent despite different offsets — the deciding metric is a
  **retrieval eval**, not boundary match.

> **Conclusion (#73):** a naive BGE swap does **not** reproduce SFR's boundaries.
> Before adopting it: (1) re-run with `CHEAP_MAX_TOKENS=512` to remove the truncation
> confound; (2) if still low, decide on a **retrieval eval** (BGE- vs SFR-breakpoint
> into separate collections), not exact boundaries. Given boundaries aren't
> retrieval-critical, `fixed_tok512` (no breakpoint embed) remains the cleanest win.

---

## Experiment 3 — embed throughput: is the cheap model actually faster?

**Rationale.** #73 assumes "smaller model → faster breakpoint embed." Measure it
directly before believing it.

**Algorithm.** `embed_speed.py`: embed the same 256 real sentence-buffers (avg 710
chars) with each model/serving, one warmup sub-batch then timed; report embeds/s.

**Results.**

| Model / serving | Throughput | vs cheap |
|---|---|---|
| **BGE-base — sidecar (CPU)** | **23.1 embeds/s** | 1× |
| SFR-Mistral — 1 endpoint (H200 vLLM, loaded) | 78.4 embeds/s | 3.4× faster |
| SFR-Mistral — pooled ×8 (H200 vLLM, loaded) | 280.7 embeds/s | 12× faster |

**Interpretation.** As deployed, the *smaller* model is **3–12× slower**, not faster.
The cause is **serving, not model size**: the BGE sidecar runs **CPU inference**
(confirmed — its PID is on no GPU), while SFR runs on vLLM across 8 H200s. Swapping
the breakpoint embed to the current sidecar would make the build **slower**.

The BGE model is ~64× smaller (110M vs 7B params, 512- vs 4096-token context), so on
**equal footing — served on a GPU via vLLM — it should be dramatically faster** than
SFR (plausibly 10–50× a single SFR endpoint). But that serving does not exist today.

> **Conclusion:** #73's "cheap = faster" is **not realizable on the *current* CPU
> sidecar** — but see Experiment 4: on a GPU the small model is an order of magnitude
> faster, and the machine has idle GPU headroom.

---

## Experiment 4 — BGE on a GPU, and the break-even vs CPU

**Rationale.** Experiment 3's cheap model was slow only because it ran on CPU. The
machine has **384 cores** and **8 H200s that sit at ~0% compute between embed bursts**
(~64 GB free each). Measure BGE on a GPU, then work out how much cheap capacity —
GPU or many-CPU — is needed to move the build.

**Algorithm.** `scratchpad/bge_gpu_bench.py` (torch+CUDA via the `/rag/envs/vllm` env,
transformers `AutoModel`, fp16, CLS pooling): load `BAAI/bge-base-en-v1.5` on one idle
H200 (GPU 6) and time encoding ~512 real buffers (truncated to 512 tokens) at several
batch sizes.

**Results — throughput of every option (embeds/s):**

| Serving | embeds/s | vs CPU sidecar |
|---|---:|---:|
| BGE-base — **CPU sidecar** (1 proc) | 23 | 1× |
| SFR-Mistral — 1 endpoint (H200, loaded) | 78 | 3.4× |
| SFR-Mistral — pooled ×8 (loaded) | 281 | 12× |
| BGE-base — **1 H200** (bs=64 / 128 / 256) | **1,322 / 1,675 / 1,959** | ~85× |

**One BGE GPU ≈ 85 CPU sidecar instances ≈ 7× the entire 8-way SFR fleet.**

### Break-even analysis

The plan: offload the **~374 breakpoint embeds/doc** to BGE, leaving the SFR fleet to
do only the **~19 stored embeds/doc**.

- **Today (SFR does both):** 393 embeds/doc ÷ 281 embeds/s = **0.71 doc/s**.
- **SFR-final ceiling (breakpoints fully offloaded):** 19 embeds/doc ÷ 281/s = **14.8 doc/s** — a ~20× headroom, *if* breakpoint capacity keeps up.
- Breakpoint capacity needed to hit a target rate = `374 × docs/s`.

| Breakpoint serving | embeds/s | → semantic docs/s | vs today |
|---|---:|---:|---:|
| **CPU BGE ×12** | ~276 | ~0.7 | **break-even** with today |
| CPU BGE ×80 | ~1,840 | ~4.9 | ~7× |
| CPU BGE ×240 | ~5,520 | ~14.8 (SFR-bound) | ~20× — impractical (RAM/procs) |
| **1 GPU BGE** | ~1,959 | **~5.2** | **~7×** |
| **~3 GPU BGE** | ~5,880 | ~14.8 (SFR-bound) | **~20×** |

**Break-even points:**
- **CPU multi-instance:** ~**12** BGE workers break even with today's rate; ~**80** for ~7×; ~**240** to saturate the SFR-final ceiling (impractical — ~120 GB RAM, process overhead, CPU encode of 512-token inputs is heavy).
- **GPU:** **1** co-located BGE instance already beats 85 CPU workers *and* the whole SFR fleet, lifting the build to **~5 doc/s (~7×)**; **~3** co-located instances saturate the SFR-final ceiling (**~15 doc/s, ~20×**). BGE-base is ~1 GB and the H200s are idle between bursts, so this needs **no new hardware** — just run BGE (GPU) alongside SFR on a few existing GPUs.

**The crossover:** one GPU does the work of ~85 CPU instances, so with GPU headroom
available the CPU-scaling path is never worth it beyond ~12 workers (break-even). Use
a GPU.

**Caveats.** GPU numbers are peak (fp16, 512-token-truncated buffers, buffers slightly
shorter than Exp. 2's); real throughput with full 512-token buffers is somewhat lower
but still ~5–6× the SFR fleet. And this whole win only matters if semantic chunking
must stay: `fixed_tok512` reaches the same SFR-final ceiling (~15 doc/s) with **no
breakpoint embed and no BGE serving at all**, and the evals find it retrieval-equivalent.

---

## Reproducibility

Both harnesses run against the live coconut layout (read-only on the corpus; the
comparison adds a few seconds of SFR fleet time). Run in the prod env for the real
HF tokenizer.

```bash
. /rag/env.sh                       # HF cache/auth; endpoints already up

# Experiment 1 — CPU profile (mock embed; no GPU contention)
/rag/envs/ragstack/bin/python python/scripts/eval/profile_semantic_cpu.py

# Experiment 2 — cheap vs expensive breakpoint model
/rag/envs/ragstack/bin/python python/scripts/eval/breakpoint_model_compare.py
# fair BGE re-test (bound buffers to BGE's 512-token context):
CHEAP_MAX_TOKENS=512 /rag/envs/ragstack/bin/python \
  python/scripts/eval/breakpoint_model_compare.py

# Experiment 3 — embed throughput (is the cheap model faster?)
/rag/envs/ragstack/bin/python python/scripts/eval/embed_speed.py

# Experiment 4 — BGE on a GPU (needs torch+CUDA; use the vllm env)
CUDA_VISIBLE_DEVICES=6 /rag/envs/vllm/bin/python python/scripts/eval/bge_gpu_bench.py
```

Both are parameterized by env vars (`INPUT`, `N_SAMPLE`, `EMBED_MODEL`/`REF_MODEL`,
`REF_URLS`, `REF_KEY`, `CHEAP_URL`, `MAX_TOKENS`, `CHEAP_MAX_TOKENS`, `BUFFER_SIZE`)
— see each script's docstring. Defaults reproduce the runs recorded here.

## Data

Sample sizes: Experiment 1 = 120 docs; Experiment 2 = 10 docs (per-doc table above
is the full raw output). The input corpus is the operator file
`09320c55-a8a7-4f4d-81b3-ae55b7a329fa.jsonl` under `/rag/ingest/inputs/` (not in the
repo; ~478 MB). No intermediate data files are persisted — both harnesses print
their tables to stdout and are cheap to re-run.

## Outcomes

- **#71** — phase-2 deprioritized (profiling comment added); it is not a throughput lever.
- **#73** — filed for the cheap-breakpoint-model path. Experiment 2 (boundaries) + 3
  (throughput) posted. Two gates before it can pay off: (a) the small model must be
  served on **GPU** (the CPU sidecar is 3–12× *slower*), and (b) boundary/retrieval
  equivalence must hold (512-bound re-test, then a retrieval eval if borderline).
- **Standing recommendation** — for ingest speed with no new serving, prefer
  `fixed_tok512` (the evals' recommended method): it eliminates the ~20×-volume
  breakpoint embed entirely and reaches the same ~15 doc/s SFR-final ceiling.
  If semantic must stay, serve **BGE on a GPU** (1 co-located instance ≈ 7×, ~3 ≈ 20×;
  one GPU beats ~85 CPU workers) — the CPU-scaling path only reaches break-even at
  ~12 workers and isn't worth it given idle GPU headroom.

## References

- Chunker: `python/ragstack/ingestion/chunkers.py` (`SemanticChunker`)
- Prior evals: `python/scripts/eval/chunking_compare_7way_report.md`,
  `python/scripts/eval/scifact_chunk_eval_report.md`
- Issues: [#66](https://github.com/wilke/ragstack/issues/66) (phase-1, PR #68),
  [#71](https://github.com/wilke/ragstack/issues/71) (deferred ingest hardening),
  [#73](https://github.com/wilke/ragstack/issues/73) (cheap breakpoint model),
  PR #67 (breakpoint-embed fan-out)
