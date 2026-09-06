# Provenance and reproducibility packaging for the chunking study

**Status:** `PROPOSED`, design only. Nothing here was run; no store was written; no file in
`/home/wilke/Development/ragstack` was modified. Repo state at time of writing: `main` @
`d225cea`.

**Audience:** the person who decides how the chunking study is packaged, and — one level
down — the student who has to reproduce it.

## The five answers, on one screen

| Q | answer | the fact that decides it |
|---|---|---|
| **1. CWL or scripts?** | **Scripts + a typed run manifest + a committed report tree.** Leave `cwl/eval-scifact-chunking.cwl` untouched. | `cwltool` exists **only** in the base miniconda env on Python 3.8, not in `/rag/envs/ragstack`. An uncontainerised CWL run's real environment is whatever `PATH` resolved — and nothing records it. The manifest records strictly more. (§1.2) |
| **2. What must be captured?** | The 16 rows in §2.1. **12 already exist** as `ragstack.eval_run/v1`; **4 are new**: served model id, tokenizer file digests, prompt hashes + LLM sampling params, and the import pin. | Across all seven Phase-0 runs, **no run captured the served embedding model id and no run captured a single package version.** (§2.3) |
| **3. The student package** | `reports/chunking-study/`, same shape as the working `reports/g1-library-retrieval/`. README leads with a **10-minute, no-GPU reproduction of every figure**. | 46 MB working tree → **7.14 MiB git pack**, measured. (§3.3) |
| **4. The analysis half** | A separate stage over committed `cells/*.json` + `raw/*.jsonl`, byte-exact, no GPU/store/network — enforced as a test, not a hope. | The artefacts **already have the right shape**: `runs_*.json` is `{variant: {query_id: [{docno, score, chunk_i}…]}}` and `report1.json` carries `per_topic`. (§4.3) |
| **5. Retrofit** | **Rescue today · retroactive `PROVENANCE.md` this week (2–3 days) · forward standard next (~9 days) · never re-run the four.** | **6 of 11 write-ups** have a copy on an unmerged branch; **all 7,178 lines of harness, all intermediates and 5 more write-ups have none** — they exist only in a session-scoped `/tmp`. (§5.0) |

**The one thing to do before reading further:** copy the scratchpad out of `/tmp`. Everything
else in this document is a decision; that one is not.

---

## 0. The finding that reorganises the answer

**This repository has already solved this problem once, in writing, for a sibling study —
and the Phase-0 chunking runs did not use it.**

`reports/g1-library-retrieval/PROTOCOL.md` § 8 is titled *"Provenance and reproducibility"*.
It has three subsections that are, almost line for line, questions 2 and 3 of this task:

| § | title | what it is |
|---|---|---|
| 8.1 | *What must be captured* | a 15-row category → fields table |
| 8.2 | *Manifest schema* | the `ragstack.eval_run/v1` JSON schema, annotated |
| 8.3 | *On-disk layout* | the committed directory tree |
| 8.4 | *Regression use* | how the pinned per-query arrays become a CI baseline |

Its standard is stated as a sentence:

> *"a third party with access to the datasets and the hardware can reproduce every number
> from the artefacts alone."*

And it is **implemented, not aspirational**:

- `python/ragstack/provenance.py` — the shared vocabulary: `chunk_descriptor()`,
  `spec_hash()`, `ragstack_version()`, `CollectionManifest`.
- `python/scripts/eval/g1_library_sweep.py:1591` `build_run_manifest()` — emits
  `schema_version: "ragstack.eval_run/v1"`, with `_git_info()` (:1506),
  `_package_versions()` (:1539), `dataset_provenance()` (:1561),
  `reranker_provenance()` (:1299) beside it.
- `python/scripts/eval/_g1_rating.py:353` `manifest_header()` — the lightweight
  drop-in header (`ragstack.g1_rating/v1`) for any smaller artefact.
- `reports/g1-library-retrieval/pilot-2026-08-24/g1-20260824T211215Z/` — a **real
  committed run**: `manifest.json`, `report.md`, `results.csv`, `cells/*.json` (27),
  `raw/*.{rankings,counters}.jsonl` (54). **5.7 MB total, in git, no LFS.**

So the honest framing of this task is not *"design a provenance scheme"*. It is:

> **Adopt the G1 standard for the chunking study, extend it in four named places where the
> chunking study is harder than G1 was, and stop writing harnesses in `/tmp` that bypass
> it.**

Everything below is that, made concrete.

---

## 1. CWL workflows vs. scripts-plus-exact-calls

### Recommendation

**Scripts, plus a typed run manifest, plus a committed report tree. Not CWL.**

Specifically:

1. **The run stage** is a small family of scripts under `python/scripts/eval/`, each with
   required argparse flags and no defaults for anything that identifies a run, driven by a
   committed `run.sh` per leg. Every one emits a `ragstack.eval_run/v2` manifest.
2. **The analysis stage** is a separate script family over committed intermediates, with no
   network, no store and no GPU (§ 4).
3. **The typed job file that CWL would have given you** is kept — as the manifest and as a
   committed `params.yml` per run — but it is produced and consumed by the harness, not by
   a workflow runner.
4. **`cwl/eval-scifact-chunking.cwl` is left where it is**, and explicitly *not* extended to
   carry this study. § 1.4 says what to do with it instead.

### 1.1 What CWL genuinely buys here

Three things, and they are real:

- **Declared inputs.** A CWL workflow input with no `default:` cannot be forgotten; the
  runner refuses to start. That is a stronger guarantee than an argparse `required=True`,
  because it is visible in a static file a reviewer can read without running anything.
- **A typed, committed job file.** `cwl/*.inputs.yml` is exactly the "job file" the user's
  requirement asks for, in a standard format, with the repo's `CHANGE-ME` convention for
  write targets.
- **The store-target sweep test.** `python/tests/ingestion/test_pdf_ingest_scatter_cwl.py`
  (sweep at ~lines 195–600) globs `cwl/*.cwl` at pytest collection time and enforces, for
  any workflow invoking a known write CLI, a three-layer contract: the store input is
  **declared**, carries **no default at all**, is **threaded** to the step, and is **bound
  to the `--qdrant-url` / `--es-url` flag**. The last layer exists because "an input the
  tool accepts but never binds to the command line is accepted, dropped, and the worker
  falls back to the CLI default. Valid CWL, green suite, production writes." A loose script
  inherits none of this.

That last point is the strongest argument for CWL and it deserves to be stated at full
strength before it is answered.

### 1.2 What CWL costs here — five findings, four of them measured on this host

**(a) `cwltool` is not in the study's environment, and the study's environment is not
recorded when it runs.** Measured:

```
which cwltool       → /homes/wilke/miniconda3/bin/cwltool
cwltool --version   → 3.1.20241007082533   (Python 3.8.5, base miniconda env)
/rag/envs/ragstack/bin/cwltool → No such file or directory
which cwl-runner    → not found
```

The documented recipe in `cwl/README.md` is therefore a **cross-environment hybrid**: a
Python-3.8 base-env runner spawns `baseCommand: [python]`, which resolves through `PATH` to
whichever interpreter `. /rag/bin/activate` put there (the 3.12 ragstack env). Nothing in
the CWL, and nothing in its outputs, records which `python` won. **For an uncontainerised
workflow, that `PATH` resolution *is* the entire environment specification** — so the CWL
file, which looks like a declaration of the run, silently omits the single most
consequential fact about it. A script that stamps `sys.executable` and
`_package_versions()` into its manifest records strictly more.

**(b) The precedent workflow is structurally ineligible for the fleet.**
`cwl/eval-scifact-chunking.cwl` is the only one of the 13 with **zero** `dockerPull` and
**zero** `NetworkAccess` declarations, and it stages its tool code by a path relative to the
CWL file:

```yaml
evaldir:
  type: Directory
  default: {class: Directory, location: ../python/scripts/eval}
```

`cwl/README.md` names this as disqualifying — GoWe `register_workflow` POSTs the CWL *text*
with no bundle, so no CWL-file-relative path can resolve — and says in terms: *"don't submit
it to GoWe as-is."* So the CWL route as it exists today is `cwltool`-only, which means it
buys the *form* of a workflow engine without the distribution the form is for.

**(c) The undeclared inputs are the ones the study is about.** The eval workflow does not
declare, and therefore does not pin:

- the **embedding endpoint set** — `--endpoints` exists on `chunk_one.py` but is not bound;
  the step calls `c7.detect_live_endpoints()` and takes whatever is up,
- `HF_HOME` (no `EnvVarRequirement`), which after #477 is the difference between a run and a
  refusal,
- the model id, the model revision, any seed, and the dataset pin (SciFact is fetched from
  BEIR at runtime).

It declares `qdrant_url`, `es_url` and `embedding_api_key` — the three things the sweep test
made it declare. **The declaration surface is shaped by the test, not by the experiment.**
That is worth knowing before treating "declared inputs" as a general property of the CWL
route rather than a property of those three keys.

**(d) The sweep-test guarantee is narrower than it looks, and is not free.**
`_WRITE_CLIS = ("ingest_shard", "load_embeddings", "chunk_one")` — a new eval CLI is
invisible to the sweep until its key is added by hand. And
`test_the_write_cli_sweep_matches_the_workflows_that_write` pins the writer set with `==`,
so adding a workflow **turns that test red until you add its name**. This is good design
(forced acknowledgement) but it means the guarantee is *opted into per CLI*, not inherited
per workflow.

**(e) The container pin is currently anti-provenance.**

```
-rwxr-xr-x 1 wilke cels 193310720 Aug 25 18:58 /scout/containers/ragstack-worker.sif
```

`ragstack-worker.sif` is dated **2026-08-25** and predates `df1cb03` (#476/#478, required
store flags) and `aee6e40` (#477, the token counter refusing rather than falling back).
A workflow containerised against it would run **pre-#477 code**, where a missing tokenizer
silently degrades to `chars_per_token = 2.5` and a "512-token" config becomes ~366 tokens —
**the exact 29%-under-fill failure the chunking plan was rewritten to prevent.** And it would
do so while *looking* pinned.

> **The general lesson, and it is the one to carry:** a container is provenance only if its
> **digest is recorded in the run manifest** and a **rebuild policy** exists. An
> unrecorded, unrebuilt SIF referenced by a bare filename is worse than no container: it
> converts a visible environment question into an invisible one.

**Recommendation on the SIF, regardless of which route is chosen:** do not route any
chunking-study run through GoWe until `ragstack-worker.sif` is rebuilt from
`apptainer/ragstack-worker.def` at a commit ≥ `aee6e40` and re-copied to
`/scout/containers/`, and until the run manifest records `sha256(sif)`. Until then, the
`/rag/envs/ragstack` virtualenv, whose package set the manifest *can* enumerate, is the more
honest environment.

### 1.3 The decisive asymmetry

The CWL argument reduces to one guarantee — *the store target must be declared* — because
the harness might otherwise write to production.

**The chunking study can eliminate that hazard rather than guard it.** Phase-0 step 3 and
Leg B already did: step 3 *"did exact brute-force cosine in numpy and never contacted a
vector store at all"*, and the Leg B grid likewise persisted `runs_*.json` without a store.
At judged-set scale (Leg A ≈ 40k docs, Leg B ≈ 1k docs, both far under the ~10k-vector
Qdrant `indexing_threshold` at which HNSW would even build) a store buys nothing but
approximation and a production-write hazard — and the G1 manifest's own
`recommendation_gate` had to *refuse to recommend* precisely because HNSW never built at its
decision rung.

So:

> **Design the study to have no store target, and the guarantee CWL uniquely provides has
> nothing left to protect.** A run that cannot write cannot write to production. That is a
> stronger property than a test asserting a flag is bound, and it is checkable in one line
> (`grep -L QdrantClient`).

What remains — declared inputs, a typed job file, a runner-enforced contract — is delivered
by the manifest at equal or better fidelity, *and* the manifest records the things
(interpreter, package versions, served model id, dataset digests, seeds, dirty-tree digest)
that an uncontainerised CWL run leaves ambient.

### 1.4 What is lost, stated plainly

1. **Static readability.** A reviewer can read `eval.cwl` and see the inputs without running
   anything. With scripts they read `argparse` and a `run.sh`. Mitigation: commit
   `params.yml` per run *beside* the manifest, in the CWL job-file shape, so the same
   static read is available. It is not runner-enforced; it is manifest-enforced (the
   harness refuses to start if `params.yml` and its own resolved args disagree).
2. **Scatter/parallelism for free.** Irrelevant here — the grid is a `for` loop over 24
   cells on one host, already driven that way by `chain.sh`.
3. **A future GoWe path.** If the study ever needs to fan across hosts, a CWL wrapper can be
   added later around a script whose interface is already fully declared. Building the
   script-with-manifest first does not foreclose it; building CWL first around an
   undeclared script does not deliver it either (per (b)).
4. **The sweep test.** Genuinely lost *if* the study ever writes to a store. § 1.3 removes
   the premise; if a stage-2 ladder run later needs a real store, that stage — and only
   that stage — should be a CWL workflow invoking `chunk_one`, which is already in
   `_WRITE_CLIS`.

**Leave `cwl/eval-scifact-chunking.cwl` alone.** Do not extend it. It currently keeps
`test_the_write_cli_sweep_matches_the_workflows_that_write` honest by being in the pinned
set; deleting or editing it churns a test that exists to force acknowledgement. Add one line
to its `doc:` recording that the chunking study does not run through it and why.

---

## 2. What must be captured for a run here to be reproducible

### 2.0 The two-tier contract — say which tier a number is in

The single most important sentence in the package, because "reproduce the runs" means two
different things in this study:

| tier | stages | contract | evidence it holds |
|---|---|---|---|
| **T1 — byte-exact** | corpus assembly, chunking, tokenization, all analysis and statistics | re-running produces **byte-identical** files; verified by SHA-256 manifest | Phase-0 already proved it: byte-identical chunk files by SHA-256; `_stats.py` bootstrap is seeded at `default_rng(0)` with 10,000 iters, so CIs are exactly reproducible |
| **T2 — metric-tolerant** | embedding, cross-encoder reranking | re-running reproduces **metrics within a stated tolerance**, not bytes; the *served* model identity is recorded so a discrepancy is diagnosable rather than mysterious | Phase-0's `0.0e+00 over 396 queries` gate was same-day, same-fleet, same process generation — it is evidence of harness determinism, **not** of cross-time GPU reproducibility |

**Do not let the T1 gate's exactness be read as a T2 promise.** The `0.0e+00` result proves
the harness has no hidden nondeterminism of its own. It does not prove that the same fleet,
restarted next month on a different vLLM build, returns the same float32 vectors — and
nothing in the Phase-0 record establishes that, because it was never tested across a
restart. State the tolerance (§ 3, step 6) and test against it.

### 2.1 The capture list, specific to this project

Categories marked **[G1]** are already in `ragstack.eval_run/v1` and need no new work beyond
calling the existing function. Categories marked **[NEW]** are the four extensions this
study needs and G1 did not.

| # | what | how | status |
|---|---|---|---|
| 1 | **Pre-registration**, by content hash | `protocol_version: sha256(PREREG-<leg>.md)` + `protocol_path` | **[G1]** — and G1 goes further: `primary_comparison.preregistered: false` with a comment explaining that claiming otherwise *"would be the one provenance field a reader most relies on being true"* |
| 2 | **git commit**, and a *dirty digest* | `_git_info()` — `commit`, `branch`, `dirty`, `dirty_digest` = sha256 of `git status --porcelain` + `git diff HEAD`, `dirty_files`, `diff_bytes` | **[G1]** — *"a bare `dirty: true` is not reproducible provenance"* |
| 3 | **Config ids / the grid**, generated not hand-listed | `grid.cells`, plus `build_spec.chunk_descriptor` and `spec_hash` from `provenance.chunk_descriptor()` | **[G1]**. For this study: record `overlap_frac` **and** `overlap_tokens` for every cell — the plan is explicit that either alone misleads (64 tokens is 25% at 256 and 3.1% at 2048) |
| 4 | **The *served* model id** — not the configured one | probe `GET <endpoint>/v1/models` per live endpoint; record `id`, `max_model_len`, `root` **per endpoint**; assert homogeneity across the pool and fail the run if it is violated | **[NEW]** — see § 2.2 |
| 5 | **Reranker id + its truncation point** | `reranker_provenance()` probes `GET :50052/health` → `{"status":"ok","model":"BAAI/bge-reranker-v2-m3"}`; add the measured `MAX_LENGTH` (4096 per-pair, measured 2026-09-02) as a recorded constant with the date of measurement | **[G1]** for the id; **[NEW]** for the limit |
| 6 | **LLM / judge model id** (Legs B, C generation, oracle, verifier) | `GET http://mango.cels.anl.gov:8003/v1/models` → `RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic`, `max_model_len: 60000`; plus **sha256 of every prompt template**, per the g1 convention the long-doc plan already requires (*"Verifier prompt hashed into the manifest"*), plus sampling params (`temperature`, `top_p`, `seed` if the server honours one) | **[NEW]** |
| 7 | **Tokenizer identity and version** | `transformers` + `tokenizers` versions from `_package_versions()` (the G1 run recorded `transformers 5.12.1`, `tokenizers 0.22.2`); **plus** sha256 of `tokenizer.json` + `tokenizer.model` as actually loaded from `HF_HOME`, and the resolved `HF_HOME` path | **[G1]** for versions; **[NEW]** for the file digests. This is load-bearing: §3.1 of the plan turns on 2.5 vs 3.50 chars/token, and a tokenizer file swap is invisible in a version string |
| 8 | **Token counter backend, explicitly** | record `chunk_token_counter` as resolved (`hf` / `endpoint` / `estimate`) and **assert it is `hf`** for any published run. #477 makes a missing tokenizer refuse rather than fall back, but `--chunk-token-counter estimate` is still a legal opt-in and would silently change every chunk boundary | **[NEW]** |
| 9 | **Endpoint set actually used** | `runtime.embedding_endpoints_live` — the *live* set after `detect_live_endpoints()`, not the candidate set | **[G1]**. Add: a **post-run re-probe**, so an endpoint that dropped mid-run is recorded rather than inferred from a retry count |
| 10 | **Seeds** — all four | `seeds: {sample, query_split, bootstrap, bootstrap_iters}` | **[G1]**. Add `distractor_sample` and, for LLM legs, the generation seed. **Reconcile one divergence first:** the Phase-0 harnesses bootstrap at **seed 7** (`step3/score3.py:147`, `step2/boot.py:3`, carried into A1 and B1) while the repo's `_stats.py` uses `SEED = 0`. Both are fine; *unrecorded* is not, and one recorded value in `seeds.bootstrap` must be the truth for a given run. Note also that P2 records a seed (`20260915`, ×0 rung) that is **inert** — that rung runs with `--distractors 0`, so the RNG draws nothing. A recorded seed that does nothing is a small lie in the manifest; assert reachability |
| 11 | **Corpus / judged-set / qrels digests** | `dataset_provenance()` digests **the content as loaded**, deliberately *"not of a download URL, so a silently different cache is detectable"* | **[G1]**. The Leg B practice of hashing the corpus file list *before* embedding is the same instinct and should be kept as the *file-list* digest alongside the *content* digest — the list catches "a different set of documents", the content digest catches "the same ids, different bytes" |
| 12 | **Software + platform** | `_package_versions()` (9 packages), `platform.python_version()`, `platform.platform()`, `platform.node()` | **[G1]**. Add `sys.executable` — see § 1.2(a) |
| 13 | **Invocation** | `argv` verbatim + `cwd` | **[G1]**, with a publication-time redaction pass (the committed G1 manifest shows `"<redacted>"` for `argv`, `cwd`, `host`, and all URLs) |
| 14 | **Store / no-store declaration** | `runtime.qdrant_url`, `runtime.es_url` — and for this study, the assertion `stores_used: false` with the harness importing no store client at all | **[G1]** for the fields; the assertion is **[NEW]** |
| 15 | **Wall-clock and cost** | `started_at`, `finished_at`, per-stage seconds, tokens embedded, cross-encoder pairs | **[G1]** |
| 16 | **Results, per query** | `results.query_ids` + `results.per_query.<metric>` arrays, in the `chunk_one.py` contract so `aggregate_stats.py` consumes them | **[G1]** — and this is the artefact that makes § 4 possible |

### 2.2 On the served model id — what is actually true on this host

The task states the registry's recorded id is stale and the endpoints serve something else.
Probed read-only, 2026-09-05:

| source | says |
|---|---|
| `GET localhost:{9001..9006}/v1/models` | `Salesforce/SFR-Embedding-Mistral`, `max_model_len: 4096`, 6 endpoints up |
| `:9007`, `:9008` | no response |
| `GET localhost:50052/health` | `{"status":"ok","model":"BAAI/bge-reranker-v2-m3"}` |
| `GET mango:8003/v1/models` | `RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic`, `max_model_len: 60000` |

**The embedding and reranker ids currently agree** with
`/rag/config/proxy/html/models.json`. **The LLM's does not, and this is the live instance of
the problem** — probed just now:

| port on `mango` | serves |
|---|---|
| `:8000` | `Qwen/Qwen3.6-27B` |
| `:8003` | `RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic` |
| `:8004` | `Qwen/Qwen3.6-35B-A3B` |

`/rag/config/unified.env:48` sets `LLM_MODEL=RedHatAI/Llama-4-Scout-…`;
`docs/plans/chunking-evaluation.md`'s *"What the model limits allow"* table names Scout,
`max_model_len: 60000`, as **the study's LLM**. But the run that actually generated Leg B's
queries used `:8004` — `Qwen/Qwen3.6-35B-A3B`, `max_model_len` 131072. The harness got this
right and said so (`pilots/mango.py:7-11`, quoted in §2.3); the *plan document* still names
the other model.

> So the failure mode is not "the registry names a model nothing serves". It is worse and
> more ordinary: **two similar models are both up, the documents name one, the run used the
> other, and only the harness that asserted the served id knows.** Everything downstream of
> Leg B — the σ_d estimate, the query set, the acceptance thresholds sized from it — was
> produced by a generator the study's own plan does not name.

The remaining staleness is structural and worth listing, because it is the reason no
configured constant can be trusted as the record:

- The registry is a **hand-maintained snapshot**: `"_generated": "2026-08-05, from what was
  live on coconut and mango"`, with a header comment saying it must be edited in two places
  by hand or "the route will 403".
- Its own note says *"ragstack fans out across 9001-9004"*. The Phase-0 runs used
  **9001–9006**. The registry is a month behind the fleet it describes.
- It lists `mango:8004` (`Qwen/Qwen3.6-35B-A3B`), which did not respond when probed.
- `chunking_compare_7way.py:143` still says *"16 endpoints: coconut keyless :9001-9008 +
  lambda13 keyed :9990-9997"*, and `scifact_chunk_eval.py:19` says *"across the 16 vLLM
  endpoints"*. Both are stale docstrings for a 6-endpoint fleet.

**The rule this justifies:** *no configured constant, registry entry or docstring may be the
recorded model identity.* The only authority is what the endpoint answered, at run time,
recorded per endpoint. That is a one-function change (`served_models()` beside
`reranker_provenance()`), and it converts the whole class of "which model actually ran"
questions from archaeology into a lookup.

**Do not record vLLM's `created` field.** Measured: two consecutive `GET /v1/models` to
`:9001` returned `1788616178` and `1788616186`. It is request-time, not process-start time,
so it is not a process-generation token and would create a false sense of restart
detectability. (If a restart marker is wanted, take `sha256` of the vLLM `/version` response
or the worker's start time from the launcher, and say which.)

### 2.3 Per-run capture inventory — what the completed runs actually recorded

Seven runs, not four. Codes as used below:

| code | run | primary docs (under `…/scratchpad/phase0/`) |
|---|---|---|
| **S1** | step-1 CDS coverage gate | `RESULTS-step1-cds-gate.md` |
| **S2** | step-2 BM25 lead ablation | `RESULTS-step2-lead-ablation.md` |
| **S3** | step-3 real dense experiment | `step3/PREREG-step3.md`, `step3/RESULTS-step3-real-experiment.md` |
| **A1** | stage-1 Leg A, 24-config grid | `stage1/PREREG-stage1.md`, `stage1/RESULTS-stage1-legA.md`, `stage1/NOTES-banked.md` |
| **P1** | Leg B/C pilots, round 1 | `pilots/RESULTS-legBC-pilots.md` |
| **P2** | Leg B re-run, round 2 | `pilots/RESULTS-legB-rerun.md` |
| **B1** | stage-1 Leg B grid | `stage1-legB/PREREG-stage1-legB.md`, `stage1-legB/RESULTS-stage1-legB.md` |

**C** = captured · **P** = partial · **N** = not captured · **–** = n/a

| # | fact | S1 | S2 | S3 | A1 | P1 | P2 | B1 |
|---|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| 1 | git commit / repo state | N | N | **N** | C | C | C | C |
| 2 | config ids / grid definition | – | P | C | **C** | – | P | **C** |
| 3a | embedding model — *sent* id | – | P | C | C | – | C | C |
| 3b | embedding model — **served** id | – | – | **N** | **N** | – | **N** | **N** |
| 4 | reranker id + endpoint | – | – | C | C | C | P | C |
| 5 | LLM / judge model id | – | – | – | – | **P** | **C** | – |
| 6 | tokenizer identity | N | P | P | P | – | P | P |
| 6b | tokenizer / package **versions** | **N** | **N** | **N** | **N** | **N** | **N** | **N** |
| 7 | endpoint set used | – | – | P | **C** | C | C | **C** |
| 8 | seeds | P | P | P | C | P | C | **C** |
| 9 | corpus file-list hash | N | N | N | **N** | N | **C** | **C** |
| 10 | chunk SHA-256 / repro gate | – | – | – | **C** | – | – | **C** |
| 11 | store snapshots | P | P | **N** | C | P | N | **C** |
| 12 | env path / versions / GPU | N | P | N | P | P | P | P |
| 13 | wall-clock / cost | N | N | C | **C** | C | C | **C** |
| 14 | prereg in version control | – | – | **N** | **N** | – | – | **N** |

Load-bearing evidence, quoted:

- **Nothing anywhere captured the served embedding id.** Every run recorded only the string
  it *sent*: `stage1/stage1_common.py:37-38` — `ENDPOINTS = [f"http://localhost:{p}" for p in
  range(9001, 9007)]` / `MODEL = "Salesforce/SFR-Embedding-Mistral"`, used at `:130` as
  `json={"model": MODEL, "input": texts}`. Same two lines in `step3/embed3.py:12-13`. No
  `/v1/models` probe of `:9001`–`:9006` exists in any harness. The only evidence the fleet
  served what was asked is *indirect* — `"retries": 0` in 186,647 requests, i.e. vLLM
  accepted the string. **That is an inference, not a record**, and all seven runs share the
  one fleet.
- **The gap is closed for the LLM and only for the LLM.** `pilots/mango.py:40-51`:
  ```python
  def served_model() -> str:
      with urllib.request.urlopen(BASE + "/v1/models", timeout=30) as r:
          return json.load(r)["data"][0]["id"]
  MODEL = served_model()
  EXPECTED = "Qwen/Qwen3.6-35B-A3B"
  if MODEL != EXPECTED:
      raise SystemExit(f"mango is serving {MODEL!r}, not the {EXPECTED!r} this run was "
                       f"calibrated against. Refusing to run: the generator identity is "
                       f"part of the result.")
  ```
  **This is the pattern to generalise, verbatim.** It is already written; it just has to
  reach the embedder and the reranker.
- **Version capture is empty across all seven runs.** No `transformers`, `tokenizers`,
  python, numpy, vLLM, Qdrant or ES version is recorded anywhere. What is recorded is the
  *interpreter path* — `PREREG-stage1.md:231` — *"`/rag/envs/ragstack/bin/python` with
  `HF_HOME=/rag/cache` — the only env with a loadable `Salesforce/SFR-Embedding-Mistral`
  tokenizer."* The chunk-file SHA-256 gate pins the tokenizer's *output* but not the code
  that produced it.
- **The Leg B corpus hash is real and is the best practice in the set — but read what it
  hashes.** `stage1-legB/legb_common.py:73-80`:
  ```python
  h = hashlib.sha256("\n".join(files).encode()).hexdigest()[:16]
  ... raise SystemExit(f"×11.5 corpus hash {h} != the pilot's {PILOT_CORPUS_SHA} — this is
      NOT the corpus legb2_sigma.json measured. Refusing to run…")
  ```
  It is over the **sorted list of file paths**, truncated to **16 hex chars** — so a
  silently mutated XML file passes. It catches *"a different set of documents"* and misses
  *"the same ids, different bytes"*. **Keep it and add a content digest beside it**; that is
  why §2.1 row 11 asks for both. The Leg A corpus (`step2/xml/`, 4,053 files, reused
  unchanged by S2, S3 **and** A1) is **never hashed in any run** — the largest asymmetry in
  the set.
- **The two reproduction gates are different instruments and neither compared embeddings.**
  A1's (`stage1/verify_step3.py`) streams SHA-256 over whole `chunks_*.jsonl` files and
  passed on three configs (`979fc92bf296b772` / 124,207,880 bytes for `fixed_tok512`); its
  metric half agreed to 4 decimals. B1's (`stage1-legB/verify_pilot.py`) compares per-query
  `rank`/`ndcg@10`/`mrr@10`/`recall@100` at tolerance `1e-12` and is where the **0.0e+00
  over 396 queries** comes from — note 396 is the *all-generated* set, not the 260 accepted
  primary queries. **Vectors were never byte-compared in either gate.**
- **Version control covers 6 of the 11 write-ups, on an unmerged branch, and nothing else.**
  `docs/stage1-and-pilot-findings` @ `0a753ab` (2026-09-05 00:59:25, pushed to origin, **not
  merged to main**) carries `docs/plans/results/{PREREG-stage1, PREREG-step3,
  RESULTS-stage1-legA, RESULTS-step3-real-experiment, RESULTS-legBC-pilots,
  RESULTS-legB-rerun, tables-stage1-legA, README}.md` — verified **byte-identical** to the
  scratchpad copies (all six sha256 prefixes match). **Not on any branch:**
  `PREREG-stage1-legB.md`, `RESULTS-stage1-legB.md`, `NOTES-banked.md`,
  `RESULTS-step1-cds-gate.md`, `RESULTS-step2-lead-ablation.md`, every `.py`/`.sh`, and every
  JSON intermediate.
- **The commit does not rescue the "written before the run" claim.** `0a753ab` is dated
  00:59:25 — *after* step 3 finished (15:55), *after* stage-1 Leg A finished (20:35), and
  five minutes after `PREREG-stage1-legB.md` was written (00:54) but not containing it; the
  Leg B grid's `chain.pid` was created in that same minute. So it is a **post-hoc archival
  commit**. It fixes the *content* as of 00:59 — genuinely valuable — but the ordering claim
  still rests on mutable mtimes, which do at least order consistently (`PREREG-step3.md`
  15:37 → `RESULTS-step3…` 15:55; `PREREG-stage1.md` 18:42 → `RESULTS-stage1-legA.md` 20:35).
- **S3 has no recoverable code identity.** It records no commit, and it cannot be
  back-assigned `d225cea`: that commit is dated 18:29 while `PREREG-step3.md` has mtime
  15:37. S3 is the run A1 later reproduces byte-for-byte, so **the reproduction gate is
  currently the only thing pinning S3's code identity at all.**

**What the pre-registrations promised about analysis, and whether the results honoured it.**
Uniformly yes, and this is the study's strongest existing practice — worth saying plainly
because §5 is about preserving it:

- **A1** pre-declared a single primary contrast, a fixed 9-contrast Holm family at α=0.05
  with everything else "descriptive", predictions P1–P5 with falsification conditions, a
  budget gate at group boundaries, and a "may not prune the grid" clause. All were executed
  and scored, §5 was explicitly labelled *"Exploratory — NOT pre-registered … gets no Holm
  protection"*, and the results **scored the pre-registration's own defect**: *"the
  pre-registered primary contrast is structurally under-powered for its own threshold … its
  resolution floor is 4× the bar. That was knowable in advance … and we did not catch it."*
- **B1** declared an 11-contrast family with each contrast's rung fixed in advance, a derived
  bar with its derivation written out, degenerate metrics declared degenerate *before* the
  run, and one contrast *"declared unreachable before the run … it will be reported as
  UNRESOLVED regardless of what it returns."* Its single deviation is declared rather than
  buried: a vacuous-UNIFORM rule was tightened *"after seeing the data, in the conservative
  direction."* It also corrected its own predecessor's headline.
- **S3** scored all five predictions against a bar written before any embedding call, kept
  the rerank arm labelled secondary as registered, and flagged its own *"informal Holm"*.

> **Preserve this.** The pre-registration discipline is better than the provenance
> discipline by a wide margin, and the fix is not to add process — it is to put the
> pre-registrations in git so the claim "written before the run" stops resting on a `/tmp`
> mtime.

### 2.4 One more capture, missing from every list: which `ragstack` was imported

`/rag/envs/ragstack` carries an **editable-install meta-path finder pointing at
`/rag/repos/ragstack`, a different commit (`6d6fcf6`)**, and it *overrides* `PYTHONPATH`.
A1 found this and defended against it — `stage1/stage1_common.py:48-59`:

```python
sys.meta_path[:] = [f for f in sys.meta_path if "editable" not in type(f).__module__]
for p in (REPO_PY, os.path.join(REPO_PY, "scripts", "eval")):
    if p not in sys.path: sys.path.insert(0, p)
import ragstack
if not ragstack.__file__.startswith(REPO_PY):
    raise SystemExit(f"ragstack resolved to {ragstack.__file__!r}, not the working copy "
                     f"under {REPO_PY!r} — the editable-install finder for /rag/repos won.")
```

and had to re-apply the removal **in every multiprocessing worker initialiser**.

This is the same hazard `python/tests/unit/test_harness_guard.py` was written for (#432,
*"the harness's own guards must fail loudly"*, closed by #444). It is a **booby trap for a
student**: without the pin, a run on this host silently executes a *different commit's*
chunkers, and no output says so. Therefore:

- `manifest.json` records `ragstack.__file__`, `ragstack_version()`, and the resolved
  `sys.executable`;
- the pre-flight (README step B2) asserts it and refuses;
- and the assertion is in the shared library, not copy-pasted per script.

---

## 3. The student-reproducible package

### 3.0 Where it lives, and why there

`reports/chunking-study/` — **in the repo, next to `reports/g1-library-retrieval/`, which is
the same shape.** Not in `/tmp`, not in a tarball, not in the scratchpad. Three reasons:

1. The g1 precedent is committed and works: 5.7 MB in git, no LFS, a real 27-cell sweep with
   per-query raw rankings.
2. The pre-registration must be **hashed by the harness at run time**
   (`protocol_version: sha256(...)`). A file the harness can only find if someone remembered
   to copy it out of `/tmp` is not a pre-registration; it is a claim about one.
3. Everything the student runs — `python/scripts/eval/*` — is already in the repo. Splitting
   the protocol from the code guarantees they drift.

### 3.1 Directory tree

```
reports/chunking-study/
├── README.md                     ← THE student entry point (§3.2). Step-by-step.
├── PROTOCOL.md                   ← study protocol; its sha256 is `protocol_version`
├── AMENDMENTS.md                 ← dated, append-only; every deviation lands here
├── GLOSSARY-pointer.md           ← one line: terms live in docs/GLOSSARY.md
├── .gitignore                    ← *.npy, chunks_*.jsonl, cache/  (regenerable bulk)
│
├── prereg/                       ← COMMITTED BEFORE EACH RUN, never edited after
│   ├── PREREG-step3-cds-dense.md
│   ├── PREREG-stage1-legA.md
│   ├── PREREG-stage1-legB.md
│   └── PREREG-legBC-pilots.md
│
├── fixtures/                     ← the judged sets, PINNED before any retrieval
│   ├── legA-cds/
│   │   ├── topics.json           ← 90 topics, YEAR-PREFIXED ids (2014_5, 2015_18, …)
│   │   ├── qrels.tsv             ← filtered to fetched docs; the restricted denominator
│   │   ├── doclist.txt           ← judged PMCIDs, one per line, sorted
│   │   └── SOURCES.md            ← every download URL + fetch date + sha256 (§3.3 step 3)
│   ├── legB-llm/
│   │   ├── queries.jsonl         ← the ACCEPTED queries, with source section + offsets
│   │   ├── qrels.tsv
│   │   ├── doclist.txt
│   │   └── prompts/{paraphrase,query,verify}.txt      ← hashed into the manifest
│   └── legC-citations/  (same shape)
│
├── digests/                      ← the T1 contract, made checkable
│   ├── fixtures.sha256           ← every file under fixtures/
│   ├── corpus/legA.doclist.sha256    ← digest of the FILE LIST (which documents)
│   ├── corpus/legA.content.sha256    ← digest of the CONTENT as loaded (which bytes)
│   ├── chunks/<cell_id>.sha256       ← the byte-identical-chunks gate
│   └── tokenizer/SFR-Embedding-Mistral.sha256   ← tokenizer.json + tokenizer.model
│
├── runs/<run_id>/                ← one directory per run. run_id = chunk-<leg>-<UTC>
│   ├── manifest.json             ← ragstack.eval_run/v2  (§2.1) — the provenance record
│   ├── params.yml                ← the CWL-shaped job file; harness refuses on mismatch
│   ├── run.sh                    ← the exact call, verbatim, runnable
│   ├── env.txt                   ← pip freeze, sys.executable, nvidia-smi, endpoint probe
│   ├── console.log
│   ├── cells/<cell_id>.json      ← means + PER-QUERY arrays (chunk_one.py contract)
│   ├── raw/<cell_id>.rankings.jsonl        ← top-200 doc ids/query — THE key artefact
│   ├── raw/<cell_id>.rerank.jsonl          ← reranked order + scores/query
│   └── stats/<cell_id>.{chunk,embed}.json  ← cstats/estats: counts, fill, tokens, seconds
│
├── analysis/                     ← re-runnable with NO GPU, NO store, NO network (§4)
│   ├── 00_sanity.md              ← gates: qrels alignment, fill table, no-store assertion
│   ├── 10_grid.md                ← the 24-cell tables
│   ├── 20_stats.md               ← paired bootstrap CIs, Holm, decision-table verdicts
│   ├── 30_figures.md
│   └── figures/*.svg             ← committed; regenerable byte-identically
│
└── RESULTS-<leg>.md              ← the narrative write-up, per leg
```

Code stays where code lives: `python/scripts/eval/`. The scratch harnesses are promoted
there and de-duplicated (§5), joining `scifact_chunk_eval.py`, `chunking_compare_7way.py`,
`chunk_one.py`, `_stats.py`, `aggregate_stats.py`, `g1_make_queries.py`.

### 3.2 README skeleton

What follows is the skeleton, at the level of detail the student actually needs. Prose is
abbreviated; the **structure, the gates and the commands are the deliverable**.

````markdown
# Reproducing the RAGStack chunking study

You will: get the data, build a judged set, run a 24-cell chunking grid, analyse it,
and produce the figures. You have never seen this project; that is assumed throughout.

**Two ways to use this document.**

| you want | do | needs | takes |
|---|---|---|---|
| the figures and tables, verified | **Part A** only | any machine, Python 3.12, numpy | ~10 min |
| the whole study from raw data | Parts A → F | this host, the GPU fleet, ~15 GPU-h | ~1 week |

Do Part A first **even if you intend to do all of it.** It proves your environment can
reproduce our published numbers before you spend any GPU time, so a later disagreement
is attributable.

---
## Part A — reproduce every number and figure, with no GPU  (10 minutes)

Everything under `analysis/` is computed from committed files under `runs/*/cells/` and
`runs/*/raw/`. No model, no store, no network.

```bash
git clone <repo> && cd ragstack
python3 -m venv .venv && . .venv/bin/activate
pip install -e "python/[dev]"            # numpy + pydantic; no torch, no CUDA

make repro-analysis                       # ≈ 3 min
```

Expected, verbatim:

```
digests   : 412/412 files match digests/*.sha256           OK
cells     : 24 cells × 396 queries, query-id alignment      OK
stats     : bootstrap seed=0 iters=10000                    OK
analysis/10_grid.md      regenerated, byte-identical        OK
analysis/20_stats.md     regenerated, byte-identical        OK
analysis/figures/*.svg   6 files regenerated, byte-identical OK
```

**Every line must say `OK`.** This is the T1 (byte-exact) tier — analysis over committed
data is deterministic, so anything else is a real difference, not noise. If a line fails,
stop and read `analysis/00_sanity.md § troubleshooting`; do not proceed to Part B.

> **Why this works.** `runs/*/raw/<cell>.rankings.jsonl` stores the top-200 retrieved
> document ids per query per cell. Every metric at every cutoff, every doc-collapse rule
> and every re-analysis after a reviewer objection is recomputable from those ids alone.
> The embeddings that produced them are **not** committed and are not needed.

---
## Part B — pre-flight  (30 minutes, do not skip)

Seven checks. Each has a command and an expected output. **A failure here is cheap; the
same failure 6 GPU-hours into a grid is not.**

| # | check | command | expect |
|---|---|---|---|
| B1 | you are on the right host | `hostname` | `coconut` |
| B2 | the study environment | `. /rag/bin/activate && python -c 'import sys;print(sys.executable, sys.version)'` | `/rag/envs/ragstack/bin/python` · 3.12.x |
| **B2b** | **which `ragstack` you imported** | `python -m scripts.eval.preflight --import-pin` | `ragstack from <your checkout>/python/ragstack/__init__.py @ <commit>` |
| B3 | model cache | `echo $HF_HOME` | `/rag/cache` — if empty, `export HF_HOME=/rag/cache` |
| B4 | **tokenizer loads offline** | `python -m scripts.eval.preflight --tokenizer` | prints the SFR tokenizer's `tokenizer.json` sha256, matching `digests/tokenizer/` |
| B5 | **embedding fleet, and what it serves** | `python -m scripts.eval.preflight --endpoints` | 6 live: `:9001…:9006`, all `Salesforce/SFR-Embedding-Mistral`, `max_model_len 4096` |
| B6 | reranker | `curl -s localhost:50052/health` | `{"status":"ok","model":"BAAI/bge-reranker-v2-m3"}` |
| B7 | **no store will be touched** | `python -m scripts.eval.preflight --no-store` | `stores_used: false — this harness imports no store client` |

**On B4.** The token counter *refuses* rather than falling back (#477). That is
deliberate: the estimator assumes 2.5 chars/token where this corpus measures 3.50, so a
silent fallback would make every "512-token" chunk ≈366 tokens — 29% under-filled, ~1.4×
the chunks — and nothing downstream would say so. If B4 fails, fix the cache; **never**
pass `--chunk-token-counter estimate` to get past it.

**On B2b — the booby trap.** `/rag/envs/ragstack` carries an **editable-install meta-path
finder pointing at `/rag/repos/ragstack`, a different commit**, and it *overrides*
`PYTHONPATH`. Without the pin, your run silently executes a different commit's chunkers and
nothing in the output says so. The harness strips the finder from `sys.meta_path` — in the
parent **and in every multiprocessing worker initialiser** — then asserts
`ragstack.__file__` and refuses if it lost. Do not work around this check; it is the same
hazard `python/tests/unit/test_harness_guard.py` exists for (#432/#444).

**On B7.** This study writes to no vector store and no search index at all. It does exact
brute-force cosine in numpy. That is not a limitation — at judged-set scale (≈40k
documents) Qdrant would not build an HNSW index anyway, and on this host the *default*
store URLs resolve to **production** (#476/#478). A harness that cannot write cannot
write to the wrong place.

---
## Part C — obtain the data

### C1. TREC CDS 2014–2016 (Leg A — the human-judged anchor)

```bash
python -m scripts.eval.fetch_cds --out reports/chunking-study/fixtures/legA-cds
```

It downloads, verifies against `fixtures/legA-cds/SOURCES.md`, and stops on any mismatch.
Four traps it handles, all of which cost someone a day already:

1. **All three qrels files number their topics 1–30.** Naive concatenation collapses 90
   topics into 30 and every number after that is meaningless. Ids are **year-prefixed**
   (`2014_5`, `2015_18`) before merging; the merged file must hold **90** distinct topics.
2. **The 2014 topics are not on the TREC page that carries the 2014 qrels.** They are at
   `http://www.trec-cds.org/topics2014.xml`.
3. **2015 has a Task A and a Task B.** We use **Task A** (`topics-2015-A.xml` with
   `qrels-treceval-2015.txt`). Mixing them is a silent mismatch.
4. **~1.5% of judged documents are unfetchable** (3 relevants genuinely withdrawn from the
   OA bucket). Qrels are **filtered to what was fetched, never imputed**, so the recall
   denominators are identical across configs and a gap can never be a coverage artefact.

### C2. Judged documents from S3

```bash
python -m scripts.eval.fetch_pmc --doclist fixtures/legA-cds/doclist.txt --out /rag/scratch/<you>/legA-xml
```
Path shape, taken from our own manifest and confirmed:
`https://pmc-oa-opendata.s3.amazonaws.com/PMC<id>.<ver>/PMC<id>.<ver>.xml`

Expect ~98.5% success. The script writes `fetch_stats.json` and a miss list; both go into
the run manifest.

### C3. The local PMC OA corpus (distractors; Legs B and C)
`/rag/oa/corpus/xml/` — 1,439,753 raw JATS files, 182 GB, with `<ref-list>` intact.
Read-only. `manifest.jsonl` carries the join keys. **This is host-local and cannot be
obtained elsewhere** (§6).

### C4. Gate
```bash
python -m scripts.eval.verify_fixtures --leg legA
```
→ `90 topics · 12,307 grade≥1 PMCIDs · 13,807 pairs · digests match`

---
## Part D — build the judged set

### D1. Legs B and C are **fixtures, not procedures**

The LLM-generated queries (Leg B) and mined citances (Leg C) are **committed under
`fixtures/`** and you should use them as given. Regenerating them will *not* reproduce
them: the generator is an LLM served by vLLM, and vLLM is not bitwise deterministic across
batch composition even at temperature 0. Their generation is documented and rerunnable —
`scripts/eval/legb_generate.py`, prompts under `fixtures/legB-llm/prompts/` with their
sha256 in the manifest — but a rerun produces a *comparable* set, not *the* set.
**Regenerating is a validity experiment, not a reproduction.** Say which you are doing.

### D2. The acceptance test — the gate before any grid GPU is spent

```bash
python -m scripts.eval.acceptance --leg legA --out analysis/00_sanity.md
```
Five pre-registered checks (see `PROTOCOL.md §8`). Check 4 is the one that must pass:
between the size extremes, ≥25% of queries must change their top-10 *document* set. On our
Leg A pilot it passed **10/10**. A set that fails check 4 cannot rank chunking configs and
must be fixed or dropped **before** the grid runs.

---
## Part E — run the grid

```bash
cd reports/chunking-study
cp runs/chunk-legA-20260904T2036Z/params.yml /rag/scratch/<you>/my-run.yml
$EDITOR /rag/scratch/<you>/my-run.yml         # set out_dir and corpus paths; change nothing else

python -m scripts.eval.chunking_grid --params /rag/scratch/<you>/my-run.yml
```

The harness, before doing any work:
- hashes `PROTOCOL.md` and the leg's `prereg/PREREG-*.md` into the manifest,
- records the git commit **and a `dirty_digest`** if the tree is dirty,
- probes every endpoint's `/v1/models` and **refuses if the pool is not homogeneous**,
- digests the corpus file list **and** its content, before embedding anything,
- writes `manifest.json` with `finished_at: null`, then fills it at the end.

**A run whose `manifest.json` has `finished_at: null` did not complete. Do not analyse it.**

Cost: ~1.4 h for the 24-cell grid on Leg A at the measured ~164k tokens/s across six
endpoints (±2× — that rate is two measurements, not a benchmark). Semantic cells cost
roughly double: they embed the text again to find boundaries.

Stop it with **SIGINT to the recorded pid**, never `pkill -f`. The pid is in
`runs/<run_id>/run.pid`. On this host, a process-name pattern once took down every API on
the box (#402).

### E1. Chunking is byte-exact; embedding is not
```bash
python -m scripts.eval.verify_chunks --run <run_id>
```
→ every `chunks/<cell>.sha256` matches ours. **This must pass exactly.** Chunking is
CPU-deterministic given a pinned tokenizer, so any difference is a real difference.

Embeddings are **not** promised byte-exact (§6). Part F compares metrics within tolerance.

---
## Part F — analyse, and compare to us

```bash
python -m scripts.eval.analyse --run <run_id> --out analysis/
python -m scripts.eval.compare --run <run_id> --against runs/chunk-legA-20260904T2036Z
```

`compare` prints a per-cell, per-metric table and applies the tolerance contract:

| quantity | tier | tolerance |
|---|---|---|
| chunk counts, chunk sha256, fill % | T1 | **exact** |
| bootstrap CIs from the *same* per-query arrays | T1 | **exact** (seed 0, 10,000 iters) |
| per-query nDCG@10 from *your* embeddings | T2 | mean abs diff ≤ 0.005 |
| cell-mean nDCG@10 / recall@k | T2 | ≤ 0.01 |
| **the sign and ordering of every decision contrast** | T2 | **must match** |

The last row is the real reproduction claim. The study's conclusions are *contrasts between
cells*, and those are what must survive; the third decimal place of an absolute score is
not a finding and never was.

If a sign flips, that is a result, not a bug — open an issue with your `manifest.json`
attached and diff it against ours (`python -m scripts.eval.diff_manifests`). The most
likely culprits are named in §6.

---
## Part G — figures

```bash
python -m scripts.eval.figures --run <run_id> --out analysis/figures/
```
Six SVGs, deterministic (fixed font metrics, no timestamps embedded), regenerated
byte-identically by Part A.

---
## Troubleshooting

| symptom | cause | fix |
|---|---|---|
| `token counter refuses` | `HF_HOME` unset or cache cold | B3/B4. Never `--chunk-token-counter estimate` |
| `store URLs unset — pass --qdrant-url` | you invoked a *different* harness | this study needs no store; check you ran `chunking_grid`, not `scifact_chunk_eval` |
| endpoint pool not homogeneous | fleet mid-restart | wait, re-run B5 |
| Part A `figures … DIFFER` | wrong `numpy` or a locale-dependent float format | check `pip freeze` against `runs/*/env.txt`; the figure writer has no plotting dependency (see §4.6) |
| a cell is missing from `cells/` | run did not finish | `finished_at: null` in the manifest; re-run that cell |
````

### 3.3 Committed vs. regenerated, with measured sizes

The rule: **commit anything from which a number can be recomputed; regenerate anything from
which it cannot.**

| artefact | per run (measured) | committed? | why |
|---|---|---|---|
| `chunks_*.jsonl` | **2.8 GB** (stage-1 legA, 24 cells) | **no** | regenerable byte-exactly; pinned by `digests/chunks/*.sha256` — 24 lines of hex replace 2.8 GB |
| `emb_*.npy` | **1.7 GB** (step 3, 4 cells) | **no** | T2 artefact; not byte-reproducible anyway, so committing it would misrepresent the contract |
| `queries.npy` | 0.3–6.2 MB | **no** | derived; regenerable, T2 |
| fetched judged XML | 314 MB (step 2, 4,053 docs) | **no** | pinned by `digests/corpus/*.sha256` + `doclist.txt`; refetchable from S3 (§6 caveat) |
| `runs_*.json` (per-query rankings) | **7.3 MB** legA · 4.3 MB legB · 2.0 MB step 3 | **YES** | **the artefact that makes Part A possible** |
| `rerank_*.json` | 0.58 MB legA · 0.11 MB step 3 | **YES** | same |
| `cstats_/estats_*.json` | 0.19 MB legA · 0.29 MB legB | **YES** | chunk counts, fill %, token stats, seconds — the cost half of every table |
| `report*.json`, `tables*.md` | 0.35 MB legA | **YES** | regenerable, but committed as the byte-exact target of Part A |
| `manifest.json`, `params.yml`, `run.sh`, `env.txt` | ~50 KB | **YES** | the provenance record |
| PREREG / RESULTS / PROTOCOL | 80–120 KB per run | **YES** | the study |

**Measured, not estimated.** I built a throwaway git repo containing the committable half of
all seven runs — 353 files, everything except `chunks_*.jsonl` and `*.npy` — committed it and
ran `git gc --aggressive`:

| | |
|---|---|
| working tree | **46 MB** |
| git pack | **7.14 MiB** |

For scale: the repo's current pack is 13.76 MiB and `reports/g1-library-retrieval/` is 5.7 MB
in the working tree today. So the entire evidence base of the chunking study is a **one-time
+50% to the pack**. That is not a size problem, and it never was — it was a discipline
problem.

**Commit the JSON *uncompressed*.** This reverses `PROTOCOL.md §8.3`'s `rankings.jsonl.zst`,
on measurement:

| storage | pack |
|---|---|
| plain `.json` (8.5 MB of stage-1 Leg A artefacts) | **1.87 MiB** |
| the same, pre-compressed to `.xz` | 1.38 MiB |

Pre-compression saves ~26% of the pack and costs: greppability, reviewable diffs, a
decompression step for the student, and a dependency (`zstandard` is **not installed** in
`/rag/envs/ragstack` — checked; stdlib `lzma` is the only compressor available without one).
Git's own delta compression across 24 near-identical `runs_*.json` already recovers most of
the benefit — pre-compressing turns each file into an opaque blob that cannot delta. **Plain
wins on every criterion except a half-megabyte.** `.zst` made sense in `PROTOCOL.md` for a
filesystem archive; it does not for a git tree.

---

## 4. The analysis half — a separate, re-runnable stage

### 4.1 The design rule

> **Every table and every figure in every `RESULTS-*.md` must regenerate from files under
> `runs/*/cells/` and `runs/*/raw/` alone — no model, no store, no network, no GPU — and the
> regeneration must be byte-identical.**

That is Part A of the README, and it is a *test*, not a hope. It is checkable in CI on a
machine with no CUDA at all.

This is not a new idea in the repo. `PROTOCOL.md §8.3` already says it, about the same
artefact:

> *"`rankings.jsonl.zst` is the highest-value artefact. With the top-200 chunk ids per query
> per cell stored, every metric, every k, every doc-collapse rule, and every re-analysis
> after a reviewer objection is recomputable with no GPU, no store, and no network."*

And the Phase-0 Leg B run already *behaves* this way by accident: it wrote **no chunk files
at all** — 12 MB total for a 24-cell × 2-rung grid — chunking in memory and persisting only
`runs_*/cstats_*/estats_*`. That is the target architecture, arrived at under time pressure
rather than by design. Make it the design.

### 4.2 Stage boundary

```
                       ┌──────────────── committed (≈30 MB) ────────────────┐
 corpus → chunk → embed → retrieve+rerank → cells/*.json + raw/*.jsonl     → analyse → figures
   T1      T1      T2         T2                    ↑                          T1       T1
                                          the stage boundary
        └────── GPU, fleet, ~1.4 GPU-h ──────┘      └──── laptop, ~3 min, no deps ────┘
```

The boundary is placed **after reranking and before metric computation** — deliberately one
step later than the obvious place. Reasons:

- Reranking is a T2 GPU stage. Putting the boundary before it would force a student to have
  the cross-encoder to reproduce any reranked number, and the study's *load-bearing* results
  (§13.2 of the long-doc plan: the reranker reverses a first-stage verdict) are reranked.
- Reranked *order and scores* are what the analysis needs; re-scoring is not.

### 4.3 What must be committed for the boundary to hold

Four things. Miss any one and Part A stops working.

1. **`cells/<cell_id>.json`** — in the existing `chunk_one.py` contract:
   `{config, source, n_queries, query_ids, means, per_query}`. The `per_query` arrays are
   non-negotiable: `_stats.bootstrap_diff_ci` resamples a **shared query-index matrix**
   across configs, so paired CIs cannot be recovered from means. `aggregate_stats.py`
   already validates query-id alignment across files before pairing them — free, if the
   contract is honoured.
2. **`raw/<cell_id>.rankings.jsonl`** — one row per query: `query_id` plus the ranked
   top-200 `(doc_id, chunk_id, score)`. This is what lets a reader recompute a metric the
   study did not report, or apply a different doc-collapse rule, without asking for GPU time.
   Measured: 1.9 MB plain for one step-3 cell; git packs the whole set at 7.14 MiB (§3.3).
3. **`raw/<cell_id>.rerank.jsonl`** — the reranked order and cross-encoder scores over
   the same pool. Needed for the with/without-reranker contrast, which the plan treats as a
   factor rather than an afterthought, and for the mean-score *diagnostic* that must not be
   confused with the quality measures.
4. **`stats/<cell_id>.{chunk,embed}.json`** — chunk count, chunks/doc, realised median/p95/max
   tokens, the `fill` column, chunking seconds, embed seconds, tokens embedded. Cost belongs
   in the same table as quality; semantic's headline was 4.4× fewer chunks and its unreported
   figure was 748× the chunking cost.

**Sizes for all seven completed runs: 46 MB working tree, 7.14 MiB packed** (§3.3). This is not a
budget problem; it was a discipline problem.

### 4.4 What the analysis stage looks like

Four scripts under `python/scripts/eval/`, each a pure function of committed files:

| script | reads | writes | tier |
|---|---|---|---|
| `analysis/sanity.py` | `cells/`, `stats/`, `manifest.json` | `analysis/00_sanity.md` | T1 |
| `analysis/grid.py` | `cells/`, `stats/` | `analysis/10_grid.md`, `results.csv` | T1 |
| `analysis/stats.py` | `cells/` (per-query arrays) | `analysis/20_stats.md` | T1 |
| `analysis/prep*.py` + `analysis/fig*.py` | `cells/`, `stats/` → `figdata_*.json` → SVG | `analysis/figures/*.svg` | T1 |

`stats.py` is a thin driver over the existing `_stats.py` — `bootstrap_metric_ci`,
`bootstrap_diff_ci`, `wilcoxon_signed_rank`, `holm_bonferroni`, `build_stats_table` — which
is already dependency-free (no scipy) and seeded (`default_rng(0)`, 10,000 iters). **Write no
new statistics.** The bespoke per-run analysis in `stage1_report.py` (20 KB) and
`legb_report.py` (31 KB) is where the divergence lives; those two files are the retrofit's
main body of work (§5).

Two properties worth designing in:

- **`sanity.py` gates the rest.** It asserts query-id alignment across all 24 cells, that
  `manifest.finished_at` is non-null, that `manifest.stores_used` is false, that the
  tokenizer digest matches, and that no cell's `chunks/<cell>.sha256` is missing. It exits
  non-zero on any failure, and `make repro-analysis` runs it first.
- **Figures must be deterministic** — see §4.6. Otherwise "byte-identical" quietly becomes
  "looks the same", and the strongest property of the whole package is lost.

### 4.5 The regression dividend

Once the confirm-split per-query arrays for the shipping default are pinned, they are a CI
baseline — `PROTOCOL.md §8.4` already proposes the two-tier scheme: deterministic checks
(chunk counts, index parity) every CI run; metric-vs-baseline on retrieval-path changes,
failing when the metric drops below `baseline_lower_CI − δ`. The analysis stage is that
machinery, already built and already running on every commit. This is the strongest argument
for doing the work at all, and it is worth more than the reproduction claim.

### 4.6 A note on figures — and it is half-built already

**Phase 0 itself produced no figures at all.** Every output was a markdown table
(`tables.md`, `tables-legB.md`, the RESULTS files). And `matplotlib` is **not installed** in
`/rag/envs/ragstack` — checked: `import matplotlib` raises, while `numpy 2.5.0` and Python
3.12.13 are present.

So "produce figures" is a **new capability**, and that is an opportunity rather than a cost:

> **Emit SVG directly from a small pure-Python writer. Do not add a plotting library.**

Rationale, in order:
1. **Determinism.** matplotlib embeds a creation timestamp in SVG metadata by default, and
   its text layout depends on which fonts the machine has. Both silently break the
   byte-identical gate — the package's single strongest property — in ways that look like
   noise. Suppressing them is possible (`svg.hashsalt`, `metadata={'Date': None}`, font
   pinning) but it is a standing obligation, and one nobody will re-check.
2. **No new dependency** in an environment a student must reconstruct, and none added to
   `python/[all]` for a reporting-only need.
3. The figures this study needs are simple: grouped bars over 24 cells, a size×overlap
   heatmap, and forest plots of paired CIs. All are ~100 lines of `<rect>`/`<line>`/`<text>`
   with numbers already computed by `stats.py`.

If a plotting library is wanted later, add it behind the same byte-identical gate and make
the gate prove it.

**This is already prototyped, in the same scratchpad, by a parallel task.** `design/figlib.py`
is a ~200-line dependency-free SVG plotter whose own docstring reaches the same conclusion —
*"matplotlib is not installed in any interpreter on this host and nothing may be installed,
so the figures are emitted as hand-written SVG"* — and it already drives five figures
(`figures/fig{1..5}-*.svg`) through exactly the architecture §4.4 proposes:

```
runs/*/cells,raw  →  prep*.py  →  figdata_*.json  →  fig*.py (figlib)  →  figures/*.svg
                      T1            committed          T1                  T1
```

The `figdata_*.json` intermediate is the right seam: it is small (7–142 KB), human-readable,
and lets a figure be re-styled without re-deriving it. **Promote `figlib.py` and the
`prep`/`fig` pair into `python/scripts/eval/analysis/` rather than writing a figure stage
from scratch** — §5.4's figures line is already most of the way spent. It is also, like
everything else described here, in a `/tmp` scratchpad (§5.0).

---

## 5. Retrofit cost, and what to actually do

### 5.0 Step zero, before any of the options: RESCUE

**This is not part of the retrofit decision. Do it whichever option is chosen, today.**

Every artefact of the entire Phase-0 programme lives in a **session-scoped `/tmp`
scratchpad**, and all but six write-ups live **only** there:

| what | size |
|---|---|
| harness code — **55 files, 7,178 lines** of Python and shell | 320 KB |
| 4 pre-registrations + 7 RESULTS write-ups + `NOTES-banked.md` | ~400 KB |
| every `runs_*/rerank_*/cstats_*/estats_*/report*.json` | ~20 MB |
| chunk JSONL and `.npy` embeddings | ~5.4 GB |

**Six write-ups have a copy on the unmerged branch `docs/stage1-and-pilot-findings`
(`0a753ab`), byte-identical to the scratchpad. Nothing else does** — not the 7,178 lines of
harness, not one JSON intermediate, and not `PREREG-stage1-legB.md`,
`RESULTS-stage1-legB.md`, `NOTES-banked.md`, `RESULTS-step1-cds-gate.md` or
`RESULTS-step2-lead-ablation.md`. The rest disappears with the session or the host's `/tmp`
policy — and **a subagent that searched this host's real filesystems, every git branch, every
dangling object and nine NFS snapshots for those scripts found nothing**, because it did not
think to look in `/tmp`. That is exactly the experience the next person will have.

```bash
# ~1 hour, no decisions required
mkdir -p reports/chunking-study/{prereg,runs,_rescue}
cp <scratchpad>/phase0/*/PREREG-*.md reports/chunking-study/prereg/
cp <scratchpad>/phase0/*/{RESULTS-*,NOTES-banked}.md reports/chunking-study/
cp -r <scratchpad>/phase0/{stage1,stage1-legB,step3,step2,pilots,cds,review} \
      reports/chunking-study/_rescue/        # code + small JSON only, see .gitignore
git add -A && git commit    # commit message records the original mtimes
```

Two things to do while copying, both cheap and both load-bearing:
- **Diff the six branch copies against the scratchpad** before overwriting either
  (`git show 0a753ab:docs/plans/results/<f> | sha256sum`). They are identical **today** —
  I checked all six — so record that fact in the commit message rather than re-deriving it
  later.
- **Decide the fate of `docs/stage1-and-pilot-findings`.** It is pushed to origin and
  unmerged. Either merge it or fold it into the rescue commit; leaving the study's evidence
  on a dangling branch is how the next person concludes there is none.

**Measured cost of committing it: 46 MB working tree → a 7.14 MiB git pack.** (Real
measurement: the four runs' committable set — 353 files, everything except `chunks_*.jsonl`
and `*.npy` — packed with `git gc --aggressive`.) The repo's current pack is 13.76 MiB, so
this is roughly +50%, once, for the entire evidence base of the study. That is cheap.

> **Note on this document.** It is in the same volatile place. Copy it out too.

### 5.1 The three options

| | option | what it means | cost | verdict |
|---|---|---|---|---|
| **A** | **Re-run the four runs under the new standard** | re-execute step 3, stage-1 Leg A, the Leg B pilots and the Leg B grid with a manifest emitter in place | ~15 GPU-h + **2–3 weeks** engineering | **No — and it does not do what it looks like it does** |
| **B** | **Retroactive provenance, no re-running** | commit the preregs; write a per-run `PROVENANCE.md` from the §2.3 matrix; commit the small intermediates; backfill only what is still recoverable, labelled as such | **2–3 days** | **Do this** |
| **C** | **Forward-only standard** | build the manifest emitter, the shared library, the analysis stage and the README; apply to Legs B/C construction and stage 2 | **8.5–10.5 engineer-days** | **Do this, after B** |

### 5.2 Why A is wrong, not merely expensive

Three independent reasons, any one sufficient:

1. **A re-run is a new run, not provenance for the old one.** The value of a
   pre-registration is that it was written before *that* execution. Re-running under a
   manifest produces a well-documented **new** result, and the old numbers — the ones in
   `docs/plans/chunking-evaluation.md` and `long-doc-judged-set.md` § 13 — remain exactly as
   documented as they are now.
2. **It would not reproduce, and the divergence would be uninterpretable.** The fleet has
   already moved: `:8004` served Leg B's generator (`Qwen3.6-35B-A3B`) and the plan names a
   different model; every vLLM process has an unknown restart history; `/rag/cache` is
   unversioned. A re-run that disagreed would leave you unable to say whether the harness,
   the fleet, or the corpus moved — because the *original* run recorded none of the three.
   **Retrofitting cannot manufacture a baseline that was never taken.**
3. **The GPU hours are the small part.** ~15 GPU-h is under a day of fleet time. The 2–3
   weeks is re-establishing 7,178 lines of harness under a new interface, and that work is
   option C's work done twice.

### 5.3 What B actually delivers, per run

The deliverable is one `PROVENANCE.md` per run, whose honesty is the point. Its shape:

```markdown
# Provenance — stage-1 Leg A (A1)
Standard: reports/chunking-study/PROTOCOL.md §8 · this run PREDATES that standard.

## Recorded at run time
| fact | value | where |
| git commit | d225cea (working copy, meta-path pinned past /rag/repos @ 6d6fcf6) | PREREG-stage1.md:8,225-230 |
| grid | 24 cells, imported from chunking_compare_7way.STAGE1_CONFIGS | PREREG-stage1.md:8-11 |
| endpoints | :9001–:9006 only, ≤2 in flight each; GPUs 6/7 untouched | PREREG-stage1.md:248-252 |
| seeds | bootstrap 7 / 10,000 resamples; doc sampling 0 | PREREG-stage1.md:43-44 |
| repro gate | chunk files byte-identical to step 3, SHA-256 | NOTES-banked.md:3-9 |
| … |

## NOT recorded, and not recoverable
- served embedding model id — no /v1/models probe exists in this harness
- transformers / tokenizers / python / numpy versions — no freeze was taken
- Leg A corpus content digest — step2/xml/ has a fetch log, no hash
- per-endpoint request tallies — only the aggregate retries: 0

## Backfilled AFTER the run (2026-09-XX) — NOT run-time evidence
- pip freeze of /rag/envs/ragstack as it stands today
- /v1/models responses from :9001–:9006 as they stand today
These are recorded because they are better than nothing and worse than a record.
They do NOT establish what ran.
```

Effort: **~3 hours per run for A1/B1/P1/P2** (whose write-ups already contain most facts in
prose — transcription, not investigation), **~1 hour each for S1/S2/S3** (short lists,
mostly "not captured"), plus half a day for the rescue commit and the `.gitignore`. **2–3
days total**, one engineer, no GPU, no fleet.

Three things B can *partly* recover and must label as such:
- **Package versions:** a `pip freeze` today is a lower bound — the env may have moved.
- **Served model ids:** probeable today (§2.2); pin them *now* so the next run has a
  baseline even though this one does not.
- **Leg A corpus digest:** computable now over `step2/xml/` **if those 4,053 files still
  exist in the scratchpad** — which makes it a rescue-priority item, since after the
  scratchpad goes, the corpus S2/S3/A1 all ran on is only refetchable from a live S3 bucket
  (§6.2).

**One thing B cannot recover at all: S3's code identity.** Its prereg predates `d225cea` by
three hours and no tree survives. Record it as unrecoverable and note that A1's byte-identical
chunk-file gate is the only evidence pinning it.

### 5.4 What C costs, itemised

| item | what | days |
|---|---|---|
| `python/scripts/eval/eval_provenance.py` | lift `build_run_manifest`, `_git_info`, `_package_versions`, `dataset_provenance` from `g1_library_sweep.py`; add `served_models()` (generalising `mango.py:40-51`), `tokenizer_digest()`, `import_pin()` (generalising `stage1_common.py:48-59`); bump to `ragstack.eval_run/v2` | **1** |
| one shared library | the three scratch `*_common.py` are **702 lines and share only 13 identical lines** — they are three independent rewrites, not copies. Collapse to one ~300-line module with the fleet client, the import pin, the token queue and the cache | **2** |
| declared interfaces | **22 of 49 scratch `.py` files have no `argparse` at all**; 15 hardcode an endpoint, model id or `/rag` path. Give every stage required flags and a `params.yml` loader | **2** |
| analysis stage | `stage1_report.py` (435 lines) + `legb_report.py` (635) → one `analysis/` family over committed `cells/` (§4.4). This is the bulk of the divergence and the bulk of the work | **2–3** |
| figures | **already prototyped** (§4.6): `design/figlib.py` + `prep*.py`/`fig*.py` produce five SVGs today, dependency-free. Promote and pin, don't rewrite | **0.5** |
| `PROTOCOL.md` + `README.md` | the student document (§3.2) and the protocol it hashes | **1–2** |
| | **total** | **8.5–10.5** |

Two of those lines are cheaper than they look because the pattern already exists in the
repo or in scratch: the manifest emitter is a lift, and the two guards
(`served_model()`, the meta-path pin) are written and tested by use.

### 5.5 Recommendation

**B now, C next, never A.** Concretely, in order:

1. **Today** — the rescue commit (§5.0). Unconditional. The evidence base is one `/tmp`
   sweep away from gone, and no later decision can restore it.
2. **This week** — B: the five uncommitted write-ups and `PREREG-stage1-legB.md` into git with their dates in the commit message, the six already on `docs/stage1-and-pilot-findings` merged rather than left dangling, seven
   `PROVENANCE.md` files, the small intermediates committed, today's served-model and
   package baselines pinned and labelled as backfill.
3. **Before Legs B and C are built** — C, because that is the next thing that will produce
   artefacts, and applying the standard to a set *as it is constructed* costs nothing extra
   while retrofitting it later costs §5.3 again.
4. **Independently** — rebuild `ragstack-worker.sif` and record its digest (§6.4), whether
   or not anything is ever routed through GoWe. It is currently a loaded gun pointed at
   #477.

---

## 6. What a student could NOT reproduce

Stated plainly, because a package that does not say this is making a promise it cannot keep.

### 6.1 The GPU tier is not byte-reproducible, and the reason is mechanical

vLLM serves under **continuous batching**: the batch a given chunk lands in depends on what
else arrived in the same window. Floating-point reduction order varies with batch
composition, so the same text embedded twice on the same endpoint can differ in the low bits
of the float32 vector. Consequences that actually reach the numbers:

- **Tied and near-tied scores break differently**, so a rank can flip. With document-level
  rollup over many chunks, near-ties are common.
- Fleet **load** and **endpoint availability** change batch composition, so the *shared*
  fleet is part of the run condition. A quiet fleet and a busy one are different experiments
  at the last decimal place.

What the package promises instead: sign and ordering of every decision contrast, cell means
within 0.01 (§3.2 Part F). What it must **not** claim: that the Phase-0 `0.0e+00 over 396
queries` gate establishes cross-time reproducibility. That gate was same-day, same-fleet,
same process generation — it proves the *harness* has no hidden nondeterminism, which is a
different and also valuable thing. Read as a T2 claim it is over-read.

### 6.2 External sources drift

- **`pmc-oa-opendata` S3.** Of 20 ids probed, 19 exist only at `.1` and one has a `.2`, so
  the bucket is *effectively* stable — but it is a live bucket. **Three relevant documents
  are genuinely withdrawn** and absent at every version and under the modern
  `oa_comm|oa_noncomm|oa_other` prefixes. A student fetching next year may get a different
  count. Mitigation: `doclist.txt` + `digests/corpus/*.content.sha256` make drift *detectable*
  and the qrels filter makes it *harmless to the comparison* (identical denominators across
  configs) — but the absolute recall numbers would move.
- **TREC / NIST hosting.** The qrels are at `trec.nist.gov`, the 2014 topics at a *different*
  host (`trec-cds.org`). Both verified live; neither is under our control. `SOURCES.md`
  records URL + date + sha256 so a mirror can be substituted and proven equivalent.
- **`/rag/cache` (HF_HOME)** is not versioned. A model re-download could change the tokenizer
  files under the same model id. `digests/tokenizer/*.sha256` is the detector; there is no
  preventer.

### 6.3 Host-local state a student off this host cannot obtain

- `/rag/oa/corpus/xml/` — **1,439,753 JATS files, 182 GB**, the distractor pool and the Leg
  B/C source corpus. Not redistributable at that size and not ours to redistribute wholesale.
- The **GPU fleet**: 8× H200 NVL, six SFR endpoints, the cross-encoder sidecar, Scout on
  `mango:8003`. Off-host, only **Part A** runs — which is the reason Part A exists and is
  placed first.
- The **dev tenant** stores, if a later ladder stage needs them.

### 6.4 The stale SIF, and the class of failure it represents

`/scout/containers/ragstack-worker.sif`, dated **2026-08-25**, predates both #476/#478 and
#477. Anything routed through GoWe today runs **pre-#477 code**, where a missing tokenizer
silently degrades to `chars_per_token = 2.5` — the ~29%-under-fill this study was rewritten
to prevent — while presenting as a pinned, containerised, reproducible run.

Three consequences, in order of importance:

1. **Do not route chunking-study runs through GoWe** until the SIF is rebuilt from
   `apptainer/ragstack-worker.def` at a commit ≥ `aee6e40` and re-copied to
   `/scout/containers/`.
2. **When it is rebuilt, record `sha256(ragstack-worker.sif)` in the run manifest.** A bare
   filename (`dockerPull: ragstack-worker.sif`) identifies nothing across time.
3. **Add a rebuild trigger.** A CI check that the SIF's build commit is an ancestor of `main`
   within N commits, or the pin is a decoration.

### 6.5 The LLM legs are fixtures, not procedures

Leg B's queries and Leg C's citances cannot be regenerated identically: Scout is served by
vLLM under the same continuous batching as §6.1, and temperature-0 sampling does not make it
bitwise deterministic. The package commits the *outputs* as pinned fixtures with the prompts
hashed into the manifest, and documents regeneration as a **validity experiment** (does a
fresh generation reach the same config ranking?) rather than a reproduction. A package that
blurred those two would be claiming reproducibility for the least reproducible part of the
study.

### 6.6 The evidence itself is currently volatile

Every artefact discussed here — four pre-registrations, four RESULTS write-ups, every
`runs_*.json` — lives under a **session-scoped `/tmp` scratchpad**
(`/tmp/claude-3581/…/scratchpad/phase0/`). It is not in git, not backed up, and disappears
with the session or the host's `/tmp` policy. **This document is in the same place.**

That is not a footnote; it is the most urgent finding in this answer, and it is why §5's
first step is *rescue*, before any standard is applied to anything.
