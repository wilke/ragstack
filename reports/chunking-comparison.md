# Chunking Strategy — RAGStack vs. embedding_app, distllm, ExaForge

**Purpose:** justify why RAGStack's chunking implementation differs from the three sibling
systems. Short version: **they are offline/HPC batch embedding & inference toolkits; RAGStack
is an online, multi-tenant serving RAG API.** The different job drives the different design —
chiefly *token-exact, lossless sizing* and *deterministic provenance*, neither of which the
batch tools need.

_Compiled 2026-06-30. Source-verified against each repo under `/rag/repos/`._

---

## 0. Context: the HiPerRAG ecosystem

These systems are best understood as **stages of an HPC-scale scientific-RAG pipeline**, the kind
exemplified by Argonne National Laboratory's **HiPerRAG** — *"High-Performance Retrieval Augmented
Generation for Scientific Insights"* (PASC '25). HiPerRAG indexes and retrieves over **3.6 million
scientific articles**, scaling RAG across thousands of GPUs on the **Polaris, Sunspot, and Frontier**
supercomputers. Its named contributions are **Oreo** (high-throughput multimodal document parsing —
PDF/paper → text) and **ColTrast** (query-aware encoder fine-tuning with contrastive + late-interaction
retrieval); it reports ~90% on SciQ and 76% on PubMedQA, beating PubMedGPT and GPT-4 on those.

**`distllm` is HiPerRAG's backbone.** This is not just thematic: `distllm` is published by the
**Ramanathan Lab @ Argonne** (`github.com/ramanathanlab/distllm`) — the group behind the HiPerRAG
paper — authored by Alexander Brace (`abrace@anl.gov`) and Ozan Gokdemir (`ogokdemir@uchicago.edu`).
It ships the **exact evaluation tasks HiPerRAG reports** — `distllm/rag/tasks/sciq.py` and
`pubmedqa.py` (SciQ 90% / PubMedQA 76% in the paper) — plus LitQA, protein QA, an MCQA module, and
Polaris@ALCF run configs. The same authorship threads the ecosystem: ExaForge's **Aegis** launcher
(`github.com/ogkdmr/Aegis`) is by the same Gokdemir. So `distllm` is, for practical purposes, the
**RAG + embedding + MCQA-evaluation engine of HiPerRAG**.

> **One nuance (where the named novelties live):** HiPerRAG's two headline contributions are **not**
> in `distllm` (grep-confirmed) and live in other Ramanathan-Lab repos:
> - **Oreo** (layout-aware multimodal PDF→text parser) → **`ramanathanlab/pdfwf`** (the PDF-workflow
>   repo, which hosts multiple parsers including Oreo).
> - **ColTrast** (query-aware contrastive + late-interaction encoder fine-tuning) → described in the
>   paper; a distinct public repo was **not confirmed** (it may be unreleased or folded into another
>   repo — stated here as unverified rather than asserted).
>
> So `distllm` is HiPerRAG's *RAG + embedding + MCQA-evaluation backbone* — not the whole paper. (Note:
> external sources also describe the HiPerRAG *project* as hosted at `ramanathanlab/distllm`, consistent
> with distllm being its primary engine.)

Mapping the pipeline onto the systems compared here:

| Pipeline stage | Representative system here | Role |
|---|---|---|
| Parse documents (PDF → text) | *HiPerRAG's **Oreo*** (in `ramanathanlab/pdfwf`) | upstream of all chunkers |
| **Chunk** text for embedding | **embedding_app**, **RAGStack** | the subject of this doc |
| Create embeddings at scale (HPC) | **distllm** *(= HiPerRAG)*, **embedding_app** | batch encode on Polaris/ALCF |
| Encoder fine-tuning for retrieval | *HiPerRAG's **ColTrast*** (repo unconfirmed) | query-aware retrieval accuracy |
| Batch LLM inference (HPC) | **ExaForge** | Aurora; consumes pre-chunked JSONL |
| RAG eval / MCQA (SciQ, PubMedQA) | **distllm** *(= HiPerRAG)* | accuracy benchmarking |
| **Serve** retrieval online (multi-tenant API) | **RAGStack** | query-time hybrid retrieval + rerank |

So the contrast in this document is fundamentally **HiPerRAG-style batch/offline corpus construction
(distllm + the HPC tooling) vs. online multi-tenant serving (RAGStack)** — which is exactly why the
chunking trade-offs differ. Sources: [arXiv:2505.04846](https://arxiv.org/abs/2505.04846) ·
[PASC '25 (ACM)](https://dl.acm.org/doi/10.1145/3732775.3733586) ·
[ramanathanlab/distllm](https://github.com/ramanathanlab/distllm) ·
[ramanathanlab/pdfwf](https://github.com/ramanathanlab/pdfwf).

### Scope of this comparison (important)
**This document compares the _chunking_ stage only — it is not a RAG-quality comparison, and it is not
a critique of HiPerRAG's contributions.** HiPerRAG's claimed wins are in **parsing (Oreo)** and
**retrieval-encoder fine-tuning (ColTrast)** — *different stages from chunking* — and its reported
SciQ/PubMedQA numbers come from that full stack. RAGStack and HiPerRAG also make different **retrieval**
choices that this doc does **not** adjudicate:

| | RAGStack | HiPerRAG (distllm) |
|---|---|---|
| Retrieval | dense (SFR-4096, off-the-shelf) + BM25 → **RRF fusion** → cross-encoder rerank | **ColTrast**-fine-tuned encoder + **late-interaction** (ColBERT-style) |
| Optimized for | online, multi-tenant, low-latency serving | offline accuracy at 3.6M-doc scale |

So nothing here claims RAGStack retrieves *better* than HiPerRAG. The claim is narrower and defensible:
**for the chunking stage, RAGStack's token-exact lossless sizing + deterministic provenance are the
right choices for online serving**; HiPerRAG optimizes a different objective with a fine-tuned encoder
we don't attempt to match here.

---

## 1. Side-by-side matrix

| Dimension | **RAGStack** | embedding_app | distllm | ExaForge |
|---|---|---|---|---|
| **Role** | Online serving RAG API (retrieval at query time) | Offline embedding module (BV-BRC) | HPC batch embedding + Faiss RAG | HPC batch LLM inference (Aurora) |
| **Chunking methods** | `fixed`, `sentence`, `words`, `semantic` (4, first-class, selectable) | `fixed`, `sentence`, `words`, `semantic` (4) | `semantic_chunk`, `full_sequence` (2) | **none** — document-level |
| **Sentence splitting** | NLTK **Punkt** (+ regex fallback) for sentence & semantic | **naive** word-accumulator w/ hardcoded 100-char cap (sentence); Punkt only in semantic | NLTK **Punkt** | n/a |
| **Word splitting** | `\S+` regex, **gapless span tiling** (exact offsets) | `str.split()` (naive) | n/a | n/a |
| **Sizing unit** | **characters _or_ tokens** | characters only | characters only | n/a (4 chars/token *estimate*, for filtering) |
| **Token awareness** | **Yes** — `TokenCounter` (HF tokenizer default, `/tokenize` endpoint, or estimator); budget **auto-detected** from the model's `max_model_len` | none | none (truncation only) | none |
| **Oversize-vs-context-window handling** | **Lossless token-split** to fit the window — no text dropped | char-cap workaround (lossy / boundary-arbitrary) | **silent tokenizer truncation** (`truncation=True`) — text dropped *(but long-context encoders, e.g. its NVEmbed 32k path, rarely truncate)* | **reject the whole document** (`too_long.jsonl`) — doc dropped |
| **Semantic algorithm lineage** | llama_index | own implementation | llama_index | n/a |
| **Semantic defaults** (buffer / percentile / min-len) | 3 / 80 / **500 chars** | 3 / 80 / 500 chars | **1 / 90 / 750 chars** | n/a |
| **Default behavior (no flags)** | chunks @ 512 char / 64 overlap | `chunk_size=-1` → **no chunking** | semantic chunk, or full-seq truncate | passthrough (whole doc) |
| **Offsets / provenance** | **deterministic char spans + `uuid5` chunk ids** (idempotent re-ingest, citations, tenant scoping) | bare strings | bare strings / HF dataset rows | whole-doc id only |
| **Validation** | conformance suite + **retrieval benchmark** (recall@k / MRR / nDCG, full corpus) | — | — | — |

---

## 2. What's actually shared (we are not gratuitously different)

- **Semantic chunking is the same family.** RAGStack, distllm, and embedding_app all implement the
  *sentence-buffer → embed → cosine-distance → adaptive-percentile breakpoint → merge-short* method;
  RAGStack's and distllm's both descend from **llama_index**. RAGStack's defaults
  (`buffer_size=3`, `percentile=80`, `min_chunk_length=500`) **match embedding_app exactly**;
  distllm only differs in tuning (`1 / 90 / 750`).
- **Method names match embedding_app** (`fixed`/`sentence`/`words`/`semantic`) — a deliberate choice
  so configs read the same and operators aren't surprised.

So the divergences below are **additive/qualitative**, not a different paradigm.

---

## 3. Where we diverge — and why

### D1. Token-exact, *lossless* sizing (the big one)
- **Them:** all three either ignore the embedder's context window until encode time and then
  **silently truncate** (distllm, `truncation=True`), **char-cap** with an arbitrary boundary
  (embedding_app), or **drop the whole document** (ExaForge rejects oversize). Every one of these
  **loses text** — the tail of a long passage never gets embedded, so it can never be retrieved.
- **Us:** chunkers can size by **tokens** against a budget **auto-detected from the model**
  (`max_model_len` via `/v1/models`; exact counts via the model's own tokenizer or the `/tokenize`
  endpoint). Any over-budget unit is **split losslessly** so it still fits — **no chunk ever
  overflows, no text is ever dropped.**
- **Why we need it and they don't:** we serve *retrieval*. A silently truncated chunk is a silently
  unsearchable document — unacceptable online. Batch toolkits optimize throughput over a corpus they
  re-run at will; an occasional truncated tail is tolerable. **(Quantified:** our full-corpus
  benchmark showed 12% of semantic chunks would overflow the 4096-token SFR window — exactly the
  text the other tools would have dropped.)
- **Scope / fairness caveat:** this advantage is decisive for **small / fixed-context embedders** like
  our SFR-Embedding-Mistral (**4096 tokens**), where overflow is common (the 12% above). It **narrows
  for long-context encoders** — distllm also supports **NVEmbed with a 32,768-token window**, where
  whole passages fit and truncation rarely triggers, so lossless token-sizing buys much less. The honest
  framing: token-exact sizing is a *must-have* when the embedder window is tight (the typical
  serving/rerank setup), and a *nice-to-have* when the encoder is long-context. RAGStack is built for
  the former.

### D2. Real linguistic boundaries everywhere
- **Them (embedding_app):** the `sentence` method is a naive word-accumulator that "ends a sentence"
  on `.!?` **or a hardcoded 100-char cap** — artificial, non-configurable, not real NLP.
- **Us:** **NLTK Punkt** (with a regex fallback) for both `sentence` and `semantic`; `words` uses a
  span-preserving `\S+` regex. Boundaries are linguistically meaningful and the 100-char artifact is
  gone. distllm agrees here (it also uses Punkt).

### D3. Deterministic offsets & provenance
- **Them:** semantic chunkers return **bare strings** (llama_index style); no stable identity.
- **Us:** every chunk carries **exact source character spans** and a **deterministic `uuid5` id**
  (`doc.id:start:end`). This is what makes **idempotent upsert re-ingest**, **tenant-scoped reads**,
  and **citation/provenance** possible — all online-serving requirements the batch tools simply
  don't have.

### D4. Four methods, selectable, tested, and benchmarked
- **Them:** distllm hardcodes one of two paths; ExaForge has no chunker; embedding_app has the four
  but no comparative evidence.
- **Us:** all four are first-class and we **measured** them (full-corpus recall@k / MRR / nDCG): the
  methods are statistically tied on quality, so we default to the cheapest (`fixed`) on **data**, not
  folklore. Choice is contract-tested, not incidental.

### D5. Different defaults, on purpose
- embedding_app defaults to `chunk_size=-1` (no chunking) because it's a library you drive explicitly;
  RAGStack defaults to a sensible `512/64` because it's a service that must do something reasonable
  out of the box. (We still honor `-1` = whole-doc for drop-in parity.)

---

## 4. ExaForge is not a counter-example
ExaForge does **no chunking at all** — it's a *document-level* inference pipeline that consumes
pre-chunked/whole-doc JSONL and *rejects* anything too large (no tokenizer, no NLP, a 4-chars/token
estimate used only to filter). It assumes chunking happened **upstream**. So "RAGStack chunks
differently than ExaForge" reduces to "RAGStack chunks and ExaForge doesn't" — they sit at different
pipeline stages.

---

## 5. One-paragraph justification (for a reviewer)
> *Scope: this concerns the **chunking** stage only — not RAG quality, and not a critique of HiPerRAG's
> Oreo/ColTrast contributions, which operate at different stages.* All four systems share the same
> semantic-chunking DNA (sentence-buffer embedding-similarity, mostly from llama_index) and RAGStack
> deliberately mirrors embedding_app's method names and semantic defaults. RAGStack diverges only where
> its job demands it: it is the only **online, multi-tenant retrieval API** in the set, so it adds
> **token-exact, lossless sizing** (auto-detected from the model's context window — eliminating the
> silent truncation / char-cap / whole-doc-rejection that the batch tools accept), **real NLP
> boundaries** in place of embedding_app's naive 100-char heuristic, and **deterministic offsets + ids**
> for idempotent re-ingest, tenant isolation, and citations. The HPC tools (distllm, ExaForge) correctly
> optimize for batch throughput and tolerate truncation — a trade-off that's wrong for online serving on
> a **fixed/small-context embedder** (our SFR-4096), though it matters less for long-context encoders
> (distllm's NVEmbed 32k). RAGStack is built for the former.
