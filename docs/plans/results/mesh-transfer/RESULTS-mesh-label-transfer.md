# RESULTS — does a MeSH topic label transfer to an unseen microbiology journal?

**Run date:** 2026-09-15. **Status:** exploratory. No pre-registration exists for this run —
it was commissioned as a bounded exploratory experiment, and every threshold and
hyper-parameter below was selected on a development slice of the *training* set, never on
either test set. The full sweeps on both test sets are printed in
[`run.log`](run.log) anyway, so anyone can see what the selection rule left on the table.

**Scope note, stated first because it bounds everything.** This measures transfer to **one**
held-out journal, *Frontiers in Cellular and Infection Microbiology* (FCIM), which is
MEDLINE-indexed and therefore has ground truth. **Frontiers in Microbiology — the journal the
owner actually cares about — is not MEDLINE-indexed and can never be scored directly.** FCIM
is a *proxy*, not the target, and §8 says exactly what that proxy cannot tell you. Nothing
was written to `/rag/data`, `/scout`, or any tenant store; the corpus was read only; the
production embedding endpoints on `:9001`/`:9002` were never contacted.

---

**Verdict: `YES — A TOPIC LABEL LEARNED FROM THE MEDLINE-INDEXED PORTION TRANSFERS TO A
MICROBIOLOGY JOURNAL IT HAS NEVER SEEN. THE LINEAR PROBE LOSES 0.006 MICRO-F1 GOING FROM A
RANDOM HOLDOUT TO A HELD-OUT JOURNAL (95% CI [-0.018, +0.006] — INDISTINGUISHABLE FROM ZERO),
AND BUYING 1,479 IN-JOURNAL LABELLED DOCUMENTS RECOVERS ONLY +0.032. THE JOURNAL BOUNDARY IS
NOT THE OBSTACLE.`**

* **The A→B gap is nearly nothing on micro-F1 and real but modest on macro-F1.** Probe
  **0.596 → 0.591** micro (Δ **−0.006**, CI [−0.018, +0.006]); kNN **0.544 → 0.525**
  (Δ **−0.019**, CI [−0.032, −0.008]). On macro-F1 restricted to the 77 labels with support
  in both test sets: probe **0.503 → 0.466** (−0.037), kNN **0.436 → 0.371** (−0.065). §4.
* **The control says the journal boundary is worth almost nothing.** Scoring the *same*
  1,479 FCIM documents under a model that never saw the journal vs. one trained with 1,479
  additional in-journal documents: **0.585 → 0.617** micro-F1 for the probe, **+0.032**.
  Whatever is limiting quality on FCIM, it is mostly **not** "the journal was unseen". §5.
* **The linear probe beats kNN, and by more on the harder evaluation.** +0.052 micro-F1 on
  A, **+0.066** on B. The probe is also ~400× cheaper at inference against a 1.14M-document
  labelled set, and ~2,800× cheaper at 10M. §4, §7.
* **What transfers worst is not microbiology — it is the demographic and study-design check
  tags.** `Prevalence` −0.287, `Genomics` −0.275, `Cell Proliferation` −0.254,
  `Polymorphism, Single Nucleotide` −0.250, then `Male` −0.156, `Aged, 80 and over` −0.152,
  `Young Adult` −0.140, `Aged` −0.123, `Adult` −0.123, `Female` −0.118. These are labels
  about *who was studied*, and FCIM's study populations differ from the training mix. The
  subject-matter labels mostly transfer *better*: `Sensitivity and Specificity` **+0.215**,
  `Retrospective Studies` +0.212, `Biomarkers` +0.185, `SARS-CoV-2` +0.108,
  `COVID-19` +0.068. §6.
* **But the absolute label quality is moderate, and that is a separate question.** 121 labels
  were predictable at all; **51/121** reach F1 ≥ 0.5 on the random holdout and **37/119** on
  the held-out journal. Against the *full* MeSH heading set — not just the 121-label
  vocabulary — recall is **0.247** (probe, FCIM) against a structural ceiling of 0.382,
  because the ≥1% vocabulary can only express 38% of the true heading instances at all. §4,
  §8.
* **Cost is not the constraint.** Embedding runs at **2,575 docs/s** on one H200 —
  **9.3 minutes** for the whole 1.44M corpus, **65 minutes** for 10M. Probe inference is
  0.4 s for 1.44M. §7.
* **The journal-structured MeSH gap is confirmed at larger n than the brief's spot check.**
  In a fresh random sample of 16,000 corpus documents: *Frontiers in Microbiology*
  **0 / 447** carry MeSH, *Microorganisms* **0 / 191**, *Antibiotics* **0 / 109**,
  *Frontiers in Pharmacology* **0 / 319**, *Frontiers in Genetics* **0 / 215**; against
  *PLOS One* 3,690 / 3,717 (99.3%) and FCIM 2,987 / 3,000 (99.6%). §2.

---

## 0. Provenance

| item | value |
|---|---|
| corpus | `/rag/oa/corpus/discovery.jsonl` (1,441,791 records) and `/rag/oa/corpus/clean/**/*.xml`, **read-only**. Shard path is `sha1(pmcid)[0:2]/[2:4]` |
| labels | NCBI efetch `db=pubmed&retmode=xml`, `MeshHeadingList/MeshHeading/DescriptorName`. **175 requests**, 100 PMIDs each, POST, descriptive `User-Agent`, `0` HTTP 429 and `0` 5xx |
| embedder | **`BAAI/bge-base-en-v1.5`**, **768-dim**, CLS pooling, L2-normalised, fp16, `max_length=512`, from the local HF cache (`HF_HOME=/rag/cache`, `HF_HUB_OFFLINE=1`) |
| device | **GPU 7** only (idle at the time; 1.54 GB peak). GPUs 0–5 carry live endpoints; 0–6 were untouched |
| **production endpoints** | **none contacted.** No `:9001`, no `:9002`, no sidecar, no Qdrant, no Elasticsearch, no tenant API |
| **writes** | **none** under `/rag/data`, `/rag/data/tenants`, `/scout`, or the corpus. Everything else lives in the session scratch directory |
| interpreter | `/homes/wilke/miniconda3/envs/cepi/bin/python` (torch 2.7.1+cu126, transformers 4.52.4). `sklearn` is **not** installed in that env, so the logistic regression is implemented directly in torch (§3.3) |
| seeds | sample + split `20260915`; the FCIM half-split `20260916`; bootstrap `7` |
| harness | [`fetch_mesh.py`](fetch_mesh.py), [`extract_text.py`](extract_text.py), [`embed.py`](embed.py), [`experiment.py`](experiment.py), [`experiment2.py`](experiment2.py) |
| logs | [`run.log`](run.log) (main run, all sweeps), [`run2.log`](run2.log) (controls), [`report.json`](report.json) |
| repo state | `main` at `94ac4bf`; **no repo file outside this directory was modified**, no schema touched, no PR |

### 0.1 One protocol deviation, disclosed

A first launch of `fetch_mesh.py` was mis-backgrounded by the shell and was believed to have
failed; a second was started. **Both ran concurrently for roughly four minutes**, so the
request rate against NCBI briefly reached ≈5 req/s against the 3 req/s no-key courtesy limit.
The duplicate was located by reading `/proc/<pid>/cmdline` and stopped **by pid** (never by
process-name pattern — MEMORY.md, #402). NCBI returned **no 429 and no 5xx** at any point,
and the duplicated records were deduplicated by PMID before use, so the measurement is
unaffected. It is recorded because it happened, not because it changed anything.

---

## 1. The question, and the design that answers it

The corpus wants one topic label space over every document. ~79% of documents carry curated
MeSH; the missing 21% is **journal-structured**, and the journals with no MeSH at all are
precisely the microbiology/AMR titles the owner cares about. So the question is not "can a
classifier reproduce MeSH" — it is "does a label learned where the labels *are* survive the
trip to a journal where they are *not*".

The design is a matched pair of test sets over **one identical training set**:

```
labelled pool  =  BG  (background journals)   ∪   F  (FCIM)
R              =  random |F|-sized sample of BG
TRAIN          =  BG \ R                          <- identical for both evaluations
   eval A      =  predict R      (random in-distribution holdout, training's journal mix)
   eval B      =  predict F      (journal held out entirely, never seen in training)
```

Because `TRAIN` is byte-identical across A and B, **the only thing that differs between the
two numbers is the test distribution**. That is what isolates the transfer gap. The label
vocabulary and every threshold are derived from `TRAIN` alone.

---

## 2. What was assembled

19,000 PMIDs were drawn from `discovery.jsonl`: **16,000 uniformly at random** from the
1,398,738 non-FCIM records carrying both a PMID and a PMCID, plus **3,000 at random** from
FCIM's 10,986. All 19,000 resolved at efetch; none was missing.

| | sampled | ≥1 MeSH heading | rate |
|---|---:|---:|---:|
| background (non-FCIM) | 16,000 | 12,647 | **79.0%** |
| FCIM | 3,000 | 2,987 | **99.6%** |

The background rate reproduces the brief's ~79% figure to the decimal on an independent
16,000-document draw.

### 2.1 The structured gap, at n = 16,000 instead of n = 20

| journal | sampled | MeSH | rate |
|---|---:|---:|---:|
| PLOS One | 3,717 | 3,690 | 0.993 |
| Frontiers in Cellular and Infection Microbiology | 3,000 | 2,987 | 0.996 |
| Nucleic Acids Research | 317 | 316 | 0.997 |
| International Journal of Molecular Sciences | 1,212 | 1,203 | 0.993 |
| BMC Infectious Diseases | 190 | 190 | 1.000 |
| Frontiers in Immunology | 552 | 492 | 0.891 |
| Proceedings of the National Academy of Sciences | 235 | 195 | 0.830 |
| Scientific Reports | 3,194 | 2,342 | 0.733 |
| Nature Communications | 929 | 624 | 0.672 |
| Science Advances | 216 | 119 | 0.551 |
| PeerJ | 231 | 91 | 0.394 |
| Pathogens | 107 | 36 | 0.336 |
| **Frontiers in Microbiology** | **447** | **0** | **0.000** |
| **Frontiers in Pharmacology** | **319** | **0** | **0.000** |
| **Frontiers in Genetics** | **215** | **0** | **0.000** |
| **Microorganisms** | **191** | **0** | **0.000** |
| **Antibiotics** | **109** | **0** | **0.000** |

The zeroes are exact, not rounded — 1,281 documents across five journals, not one of which
carries a MeSH heading. The gap is a property of the journal, not of the document.

### 2.2 The usable pool

Text is `front/title-group/article-title` + `front/abstract` from the corpus JATS under
`clean/`. 29 of 19,000 PMCIDs had no file on disk and 248 had no JATS abstract; both fall
back to the PubMed abstract already present in the efetch response. Requiring ≥1 MeSH
heading and an abstract of ≥200 characters leaves:

| | documents |
|---|---:|
| labelled pool | **15,423** |
| ├ background (BG) | 12,465 |
| └ FCIM (F) | 2,958 |
| distinct journals in the pool | 140 |
| publication years | 1975 – 2026 |
| **TRAIN** = BG \ R | **9,507** (fit 8,081 / dev 1,426) |
| **eval A** test = R | **2,958** |
| **eval B** test = F | **2,958** |

TRAIN's journal mix: PLOS One 2,812, Scientific Reports 1,771, International Journal of
Molecular Sciences 906, Nature Communications 477, Frontiers in Immunology 375, Nucleic Acids
Research 250, BMC Genomics 171, Viruses 157, PNAS 143, BMC Infectious Diseases 142, then a
tail of 130 journals. **TRAIN contains infection-adjacent
content but very little of it**: `Anti-Bacterial Agents` is on 1.9% of TRAIN and `Bacteria`
on 1.6%, against 12.0% and 10.1% of FCIM. §8 treats this as a limitation, not a footnote.

---

## 3. Predictors

### 3.1 Label vocabulary

MeSH descriptors appearing in **≥1% of TRAIN** (≥95 of 9,507 documents): **121 labels**.
Computed on TRAIN only. Ranges from `Humans` (61.4% of TRAIN) down to the 1% floor.

Those 121 labels can express only **38.0%** of the true heading instances in eval A and
**38.2%** in eval B — documents carry 11.2 (A) and 10.4 (B) headings each, most of them
rarer than the 1% floor. Micro/macro-F1 below are computed **within the vocabulary**, which
is the right instrument for the transfer question; §4.3 gives the number against the
unrestricted heading set, which is the right instrument for "is this label space useful".

### 3.2 kNN label propagation (the control, zero training)

Cosine over the normalised embeddings; for each test document take its `k` nearest TRAIN
documents and predict a label when the fraction of those `k` carrying it clears a threshold.
Swept `k ∈ {10, 25, 50, 100}` × `θ ∈ {0.15 … 0.50}`, plus a per-label threshold variant.
Selected on the dev slice: **k = 50, per-label thresholds** — dev micro-F1 **0.570** for the
per-label variant against **0.567** for the best global threshold (k=50, θ=0.25).

### 3.3 Linear probe

One-vs-rest L2-regularised logistic regression on the same 768-dim embeddings, objective
`Σᵢ BCEᵢ + (1/2C)‖W‖²`, optimised with **LBFGS (strong-Wolfe line search) in float64 to
convergence** — 121 binary problems solved jointly as one 768×121 weight matrix. Swept
`C ∈ {0.1, 1, 10, 100}`; selected **C = 10** with per-label thresholds on dev.

**This detail is load-bearing and is recorded because the first attempt got it wrong.** An
initial fit using 600 full-batch Adam steps produced a probe with micro-F1 0.452 and
macro-F1 **0.039** — *worse than kNN* — and would have supported the conclusion "the linear
probe does not beat label propagation". That was an optimisation failure, not a finding.
Fitting the same model properly moved it to 0.596 / 0.469. Both fits are in the git history
of this directory's harness; the Adam numbers appear nowhere in §4.

---

## 4. The result

### 4.1 Headline

Identical TRAIN (9,507 docs), two test sets of 2,958 documents each.

| predictor | eval | micro-F1 | micro-P | micro-R | macro-F1 | macro-P | macro-R |
|---|---|---:|---:|---:|---:|---:|---:|
| kNN k=50 | **A** random holdout | 0.544 | 0.480 | 0.627 | 0.403 | 0.399 | 0.464 |
| kNN k=50 | **B** journal holdout | 0.525 | 0.467 | 0.599 | 0.291 | 0.355 | 0.315 |
| linear probe C=10 | **A** random holdout | **0.596** | 0.560 | 0.638 | **0.469** | 0.469 | 0.506 |
| linear probe C=10 | **B** journal holdout | **0.591** | 0.545 | 0.646 | **0.379** | 0.425 | 0.402 |
| prior baseline (always top-3 TRAIN labels) | A / B | 0.347 / 0.374 | — | — | — | — | — |

Macro-F1 is averaged over the labels with ≥1 true positive in that test set: 121 in A, 119
in B.

### 4.2 The delta, which is the actual deliverable

| predictor | metric | A | B | Δ (B − A) | relative | bootstrap 95% CI |
|---|---|---:|---:|---:|---:|---|
| linear probe | micro-F1 | 0.596 | 0.591 | **−0.006** | −0.9% | **[−0.018, +0.006]** |
| kNN | micro-F1 | 0.544 | 0.525 | **−0.019** | −3.6% | **[−0.032, −0.008]** |
| linear probe | macro-F1 (all supported labels) | 0.469 | 0.379 | −0.090 | −19.2% | — |
| kNN | macro-F1 (all supported labels) | 0.403 | 0.291 | −0.112 | −27.8% | — |
| linear probe | macro-F1 (77 common labels) | 0.503 | 0.466 | **−0.037** | −7.4% | — |
| kNN | macro-F1 (77 common labels) | 0.436 | 0.371 | **−0.065** | −14.9% | — |

2,000 bootstrap resamples, both test sets resampled independently.

**Read the macro row carefully.** The −0.090 / −0.112 figures average over *different* label
sets in A and B, so part of that number is "FCIM has different labels", not "the model got
worse". Restricting to the **77 labels with ≥20 true positives in both** test sets — an
apples-to-apples average — the probe's macro degradation is **−0.037**. The honest statement
is: **micro-F1 does not degrade measurably; macro-F1 degrades by about 7% relative on a
matched label set, and by ~19% if you also count the label-mix change.** Both are small
compared to the failure the brief was worried about.

The probe's precision falls slightly on B (0.560 → 0.545) while its **recall rises**
(0.638 → 0.646). It is not becoming timid on the unseen journal; it is making slightly more
errors of the same kind.

### 4.3 The ceiling nobody should skip

Against the **unrestricted** MeSH heading set — every heading a document actually carries,
not just the 121 in vocabulary:

| | recall vs. all true headings | structural ceiling (vocab coverage) |
|---|---:|---:|
| probe, eval A | 0.243 | 0.380 |
| probe, eval B | **0.247** | 0.382 |
| kNN, eval A | 0.239 | 0.380 |
| kNN, eval B | 0.229 | 0.382 |

So a probe trained this way reproduces about **a quarter** of a document's real MeSH
headings, and cannot exceed 38% at this vocabulary size no matter how good it gets. Notably
**eval B is not worse than eval A on this measure either** (0.247 vs 0.243) — the transfer
result survives the change of instrument.

### 4.4 How many labels are predictable at all

| | F1 ≥ 0.3 | F1 ≥ 0.5 |
|---|---:|---:|
| eval A (random holdout) | 99 / 121 | 51 / 121 |
| eval B (journal holdout) | **71 / 119** | **37 / 119** |

This is where the cost of transfer actually shows up: the *aggregate* barely moves, but the
count of labels you would trust individually drops by roughly a quarter.

---

## 5. The control — unseen journal, or harder journal?

A low score on eval B has two possible causes that the A→B comparison cannot separate:
the journal was **unseen**, or FCIM is simply **harder to label**. So: split F in half and
score the *same* 1,479 documents twice.

* **B′** — predicted by the TRAIN-only model (journal unseen, 9,507 training docs)
* **C** — predicted by a model trained on TRAIN **+ the other 1,479 FCIM documents**
  (journal seen)

| predictor | F_test micro-F1, journal **unseen** | journal **seen** | gain from in-journal labels |
|---|---:|---:|---:|
| linear probe | 0.585 | 0.617 | **+0.032** |
| kNN | 0.519 | 0.539 | +0.020 |
| linear probe, macro-F1 (77 common labels) | 0.463 | 0.491 | +0.028 |
| kNN, macro-F1 (77 common labels) | 0.364 | 0.366 | +0.002 |

**Handing the model 1,479 labelled documents from the exact journal it is being asked to
predict — a 16% increase in training data, perfectly targeted — buys +0.032 micro-F1.** That
is five times the A→B gap, and still small in absolute terms. The reading: FCIM's residual
difficulty is mostly *intrinsic to the label problem*, not a consequence of the journal
being out of distribution. A label space built from the MEDLINE-indexed portion is close to
as good as it is going to get on a journal outside that portion.

---

## 6. Which labels transfer worst — the diagnostic

Per-label F1, linear probe, for the labels with ≥20 true positives in **both** test sets.

**Worst-transferring (probe F1 on B minus F1 on A):**

| label | support A | support B | probe A | probe B | Δ | kNN A | kNN B |
|---|---:|---:|---:|---:|---:|---:|---:|
| Prevalence | 53 | 51 | 0.612 | 0.324 | **−0.287** | 0.415 | 0.242 |
| Genomics | 51 | 39 | 0.368 | 0.093 | −0.275 | 0.261 | 0.205 |
| Cell Proliferation | 64 | 25 | 0.387 | 0.133 | −0.254 | 0.374 | 0.133 |
| Polymorphism, Single Nucleotide | 72 | 27 | 0.667 | 0.417 | −0.250 | 0.578 | 0.129 |
| Brain | 66 | 31 | 0.459 | 0.216 | −0.242 | 0.439 | 0.458 |
| Male | 816 | 425 | 0.704 | 0.547 | −0.156 | 0.668 | 0.476 |
| Vaccination | 34 | 28 | 0.554 | 0.400 | −0.154 | 0.556 | 0.370 |
| Aged, 80 and over | 96 | 46 | 0.392 | 0.240 | −0.152 | 0.314 | 0.192 |
| Apoptosis | 62 | 42 | 0.632 | 0.481 | −0.151 | 0.429 | 0.364 |
| Bacteria | 51 | 298 | 0.580 | 0.439 | −0.142 | 0.531 | 0.457 |
| Young Adult | 191 | 97 | 0.439 | 0.299 | −0.140 | 0.393 | 0.256 |
| Gene Expression Profiling | 102 | 98 | 0.533 | 0.405 | −0.128 | 0.430 | 0.227 |
| Aged | 291 | 202 | 0.599 | 0.476 | −0.123 | 0.558 | 0.404 |
| Adult | 429 | 276 | 0.626 | 0.503 | −0.123 | 0.582 | 0.434 |
| Female | 857 | 581 | 0.717 | 0.599 | −0.118 | 0.691 | 0.522 |

**Best-transferring:**

| label | support A | support B | probe A | probe B | Δ |
|---|---:|---:|---:|---:|---:|
| Sensitivity and Specificity | 29 | 126 | 0.469 | 0.684 | **+0.215** |
| Retrospective Studies | 101 | 175 | 0.426 | 0.638 | +0.212 |
| Biomarkers | 85 | 96 | 0.441 | 0.626 | +0.185 |
| Child | 109 | 130 | 0.534 | 0.711 | +0.177 |
| High-Throughput Nucleotide Sequencing | 41 | 97 | 0.381 | 0.520 | +0.139 |
| Machine Learning | 44 | 28 | 0.475 | 0.605 | +0.130 |
| Pregnancy | 55 | 71 | 0.703 | 0.813 | +0.110 |
| SARS-CoV-2 | 65 | 155 | 0.808 | 0.916 | +0.108 |
| COVID-19 | 84 | 179 | 0.860 | 0.928 | +0.068 |

**The pattern is clean and it is not the pattern one would have guessed.** The labels that
break are (a) MeSH *check tags* describing the study population — `Male`, `Female`, `Adult`,
`Aged`, `Aged, 80 and over`, `Young Adult` — and (b) generic methodology descriptors
belonging to research programmes underrepresented in FCIM (`Genomics`, `Polymorphism, Single
Nucleotide`, `Cell Proliferation`, `Brain`, `Gene Expression Profiling`). The labels that
*improve* are subject-matter and study-design terms that FCIM has far more of than the
training mix, so the probe sees them at higher prevalence and its precision rises.

`Bacteria` is the instructive one: support goes 51 → 298 (5.8× more prevalent in FCIM) and
F1 still falls 0.142. A microbiology-specific label is genuinely harder in a microbiology
journal, because there it must be discriminated against *other* microbiology content rather
than against everything else.

### 6.1 Prevalence shift, TRAIN → FCIM

| over-represented in FCIM | TRAIN | FCIM | | under-represented | TRAIN | FCIM |
|---|---:|---:|---|---|---:|---:|
| Anti-Bacterial Agents | 0.019 | **0.120** | | Male | 0.270 | 0.144 |
| Bacteria | 0.016 | **0.101** | | Female | 0.285 | 0.196 |
| RNA, Ribosomal, 16S | 0.010 | **0.084** | | Adult | 0.145 | 0.093 |
| Microbial Sensitivity Tests | 0.010 | **0.075** | | Middle Aged | 0.127 | 0.082 |
| Bacterial Proteins | 0.020 | 0.075 | | Cell Line, Tumor | 0.040 | 0.005 |
| Host-Pathogen Interactions | 0.010 | 0.069 | | Algorithms | 0.038 | 0.003 |
| Humans | 0.614 | 0.726 | | Models, Molecular | 0.025 | 0.004 |
| Animals | 0.341 | 0.385 | | Software | 0.022 | **0.000** |

The shift is large — 6–8× on the AMR-relevant labels — and the probe survives it. That is
the substance behind the verdict.

---

## 7. Cost

Measured on one idle H200 NVL (GPU 7), batch 128, fp16, `max_length=512`, mean 373 tokens
per document.

| stage | measured | 1.44M docs | 10M docs |
|---|---|---|---|
| embedding (title+abstract) | **2,575 docs/s**, 1.54 GB peak | **9.3 min** (0.16 GPU-h) | **65 min** (1.08 GPU-h) |
| probe training (LBFGS, 9,507×768, 121 labels) | **1.4 s** | unchanged | unchanged (scales with labelled set, not corpus) |
| probe inference | 3.3M docs/s | **0.4 s** | 3 s |
| kNN scoring | 9.4×10⁹ doc-pairs/s | ~40 s for 303k unlabelled × 1.14M labelled | ~30 min for 2.1M × 7.9M |

**Total wall time to put a 121-label topic vector on every document of the current 1.44M
corpus: under 15 minutes on one H200, using the probe.** At 10M it is 1–2 GPU-hours. Storage
for the labelled matrix at 10M scale is 12 GB in fp16, which fits on one card.

The expensive leg is **not** compute, it is **label acquisition**: 175 efetch requests bought
19,000 documents' MeSH. Scaling that to the ~1.14M MEDLINE-indexed documents of the current
corpus is ~11,400 requests ≈ **63 minutes** at the 3 req/s courtesy limit — feasible but
rude; at that volume the right instrument is the PubMed annual baseline over FTP, not the
E-utilities. This experiment did not exercise that path.

kNN costs 9.4×10⁹ doc-pairs/s, so its per-document rate falls as the labelled set grows:
**~8,200 docs/s** against 1.14M labelled (≈400× slower than the probe) and **~1,200 docs/s**
against 7.9M (≈2,800× slower). It is also worse on every accuracy metric here. **There is no
configuration in this experiment where kNN is the right choice.**

---

## 8. What this experiment could NOT determine

Stated at length because the headline is a "yes", and a "yes" earns more scrutiny than a
"no".

1. **The proxy is not the target, and the gap between them is unmeasured.** FCIM is
   MEDLINE-indexed; *Frontiers in Microbiology* is not. **Whether that difference reflects a
   content difference or purely an indexing-policy difference is unknown, and this
   experiment cannot address it** — by construction, because the target has no ground truth.
   If NLM's non-indexing of *Frontiers in Microbiology* tracks something real about its
   content (scope, article type, rigour), the transfer measured here is optimistic. If it is
   an administrative artifact of journal selection, the transfer measured here is the right
   estimate. **I have no evidence either way and did not attempt to get any.** A partial
   probe that *would* be informative and was not run: compare embedding-space distance from
   TRAIN's centroid for FCIM vs. *Frontiers in Microbiology* documents — if the two
   microbiology journals sit at similar distance, the proxy is more defensible.
2. **n = 1 journal.** One held-out journal, chosen because it is the closest available
   analogue of the target. A second and third held-out journal would say whether −0.006 is
   typical or lucky; the design supports it and it was not run for time.
3. **TRAIN is not microbiology-free, and nobody should read this as zero-shot.** 1.9% of
   TRAIN carries `Anti-Bacterial Agents` and 1.6% `Bacteria`; BMC Infectious Diseases,
   PLoS Pathogens, Viruses and Frontiers in Immunology are all in the training mix. FCIM was
   held out as a *journal*, not as a *topic*. That is the realistic production setting — the
   labelled portion genuinely does contain infection biology — but it is **not** a test of
   transfer to a topic the training set has never seen.
4. **The 121-label vocabulary is an artifact of this sample's size**, not a design choice
   with evidence behind it. A ≥1% floor on 9,507 documents is ≥95 occurrences; a labelled
   set of 1.14M would admit thousands of labels at the same floor. The macro-F1 trend in §4.2
   and the label counts in §4.4 both suggest **the transfer gap is larger for rarer labels**,
   so a production-size vocabulary would likely show a worse A→B gap than the one measured
   here. This experiment provides no measurement at that vocabulary size.
5. **Absolute quality is not the same question as transfer, and only transfer was asked.**
   Micro-F1 ≈ 0.60 within a 121-label space and ~0.25 recall against real MeSH is *not*
   obviously good enough for any particular downstream use. Whether a 121-dimension topic
   vector at F1 0.6 is useful for retrieval, filtering, or a knowledge graph is untested here
   and should not be inferred.
6. **Title + abstract only.** Bodies were not read. Whether full text narrows or widens the
   gap is unmeasured.
7. **One embedder, no ablation.** `bge-base-en-v1.5` at 768 dims. A domain-specific encoder
   (PubMedBERT, SPECTER2, BioLinkBERT) might change both levels and the gap; not tested.
8. **MeSH is treated as flat.** No tree, no explosion to ancestors, no major-topic weighting
   (the `MajorTopicYN` flag was captured but unused). A hierarchy-aware evaluation would
   score near-misses differently and is likely to be *more* favourable.
9. **Curated MeSH is treated as ground truth.** It is human indexing with its own error rate
   and its own drift over a 1975–2026 window; no inter-indexer reliability bound was
   established, so "F1 = 0.60" has an unmeasured ceiling below 1.0.
10. **No calibration or abstention was studied.** Every number uses a hard threshold. A
    production labeller would want per-label confidence and the option to emit nothing; the
    probe produces probabilities that make that possible, and their calibration on the
    held-out journal was not checked.

---

## 9. File manifest

| file | what it is |
|---|---|
| [`fetch_mesh.py`](fetch_mesh.py) | efetch client — batched, rate-limited, resumable, hard-stops on 429/5xx |
| [`extract_text.py`](extract_text.py) | title + abstract out of the corpus JATS, `sha1(pmcid)` sharding, PubMed fallback |
| [`embed.py`](embed.py) | `bge-base-en-v1.5`, CLS pooling, L2 normalisation, fp16 |
| [`experiment.py`](experiment.py) | splits, vocabulary, kNN sweep, linear probe, headline table, per-label and prevalence diagnostics |
| [`experiment2.py`](experiment2.py) | §5 seen/unseen control, common-label macro, bootstrap CIs, cost timings |
| [`run.log`](run.log) | main run, every sweep cell on dev **and** on both test sets |
| [`run2.log`](run2.log) | controls, bootstrap, cost |
| [`report.json`](report.json) | machine-readable headline metrics, chosen hyper-parameters, label vocabulary |

Intermediates — `sample.jsonl`, `mesh.jsonl`, `fetched.jsonl`, `labelled.jsonl`, `emb.npy`
(15,423 × 768 float32, 45 MB) — stayed in the session scratch directory and are not
committed. `fetch_mesh.py` + `extract_text.py` + `embed.py` reproduce them from the corpus in
about six minutes, seeds included in `experiment.py`.
