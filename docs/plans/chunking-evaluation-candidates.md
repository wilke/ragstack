# Chunking: what we support, how it is built, and what to evaluate next

**Status:** `OPEN`. A research record and a plan, written 2026-10-06 from a code audit of
`main` @ `0f511e6` and a literature survey built on
[`docs/papers/bib-inbox/04-chunking-segmentation-wide.md`](../papers/bib-inbox/04-chunking-segmentation-wide.md).
Nothing here is built yet.

Companion to [chunking-evaluation.md](chunking-evaluation.md), which plans *how* the evaluation
is run (ground truth, grid, cost). This page answers which **methods** belong in it, and what
the **code** needs before new methods can be added cleanly. For choosing a method today, see
[`docs/CHUNKING.md`](../CHUNKING.md).

**Owner decisions this page builds on (2026-10-06):**

- `fixed_token` is the default chunk method for new collections (merged in #677, `9d47530`; method-less specs and the settings-derived default collection keep `fixed`, so no existing collection changes).
- **`python/ragstack/ingestion/chunkers.py` is unfrozen.** The chunking study's commit pin
  (`docs/plans/results/stage0/s0_common.py` `EXPECT_COMMIT`) protects that study's re-runs. It
  no longer forbids changing the file on `main`. Every experiment instead records the code
  version, the raw `git describe` and the full commit, and should run from a versioned tools
  image (ADR-0010).
- **Section-aware chunking goes into `chunkers.py`**, as a method beside the others. Existing
  external libraries are surveyed before writing our own (§5).

---

## 1. The six supported methods

All in `python/ragstack/ingestion/chunkers.py`, listed in `CHUNK_METHODS` and dispatched by
`make_chunker`. Every chunk comes from `_make_chunk` with id `uuid5(doc_id:start:end)`, and
spans cover the source text losslessly (overlapping configs overlap by construction).

| Method | Class | Unit | Needs an embedder | Notes |
|---|---|---|---|---|
| `fixed` | `RecursiveCharacterChunker` | characters | no | Not actually recursive: raw character offsets |
| `fixed_token` | `FixedTokenWindowChunker` | tokens of the model's HF tokenizer | no (needs the tokenizer) | The default from #677 |
| `sentence` | `SentenceChunker` | chars, or tokens under a budget | no | `sentence_spans`: Punkt, with a sub-split for very long spans |
| `words` | `WordChunker` | as `sentence` | no | `word_spans` |
| `semantic` | `SemanticChunker(pool_sentences=False)` | sentence buffers | yes | ~7× the embedding tokens of `fixed` on 20 papers (derived: breakpoint 2,413,763 + chunk 345,591 tokens over `fixed`'s 396,398, which counts its 64-char overlaps; the artifact itself reports 6.78× as `semantic` ÷ `semantic_pooled` breakpoint tokens) |
| `semantic_pooled` | `SemanticChunker(pool_sentences=True, distance_round=6)` | sentences, mean-pooled | yes | ~1.8× `fixed` (derived the same way: 356,146 + 345,587 over 396,398); **different boundaries from `semantic`** (span Jaccard 0.0025) |

Sources for the cost and boundary figures:
`docs/plans/results/salmonella-amr-semantic-vs-pooled-2026-09-24.md`. Reachability per ingest
path, per-tenant availability and the guards (`INGEST_WORKER_UNSUPPORTED_METHODS`, admin-only
explicit chunk spec, the per-workflow CWL enum) are in `docs/CHUNKING.md` and
[`docs/ingest-paths.md`](../ingest-paths.md).

Chunking-adjacent transforms that change chunks but are not methods:

- JATS lifts tables and figures out as separate units (`ingestion/jats.py`);
- the boilerplate filter, which runs after chunking (`ingestion/boilerplate.py`);
- the token-budget splitter (`split_text_to_token_budget`);
- neighbour linking (`link_neighbors_by_document`);
- the segmentation cache, used only by `scripts/ingest_jsonl.py`.

## 2. Is chunking cleanly separated?

**Partly.** (Note: `chunking-one-factory.md` §5/§8 still describes `chunkers.py` as frozen; that text is being updated with the experiment-provenance change.) The algorithms sit in one module behind a common structural interface
(`protocols.Chunker`: `chunk(doc) -> list[Chunk]`). Construction, configuration and the
token-budget policy are spread out and disagree. Ranked problems:

1. **Five builders, three token-budget policies.** `api/deps.py` `_chunker_for` (no cap, ever)
   and `_build_chunker` (opt-in cap); `ingestion/chunker_config.py` `build_chunker` (always the
   model window, read live from `/v1/models`), used by `scripts/ingest_shard.py`,
   `embed_shard.py` and `ingest_jsonl.py`. So the same spec chunks differently by entry point:
   `sentence`/`words` ignore `chunk_size` under a token budget (30 vs 1 chunks on one document,
   [chunking-one-factory.md](chunking-one-factory.md) §8), and no test asserts that the API and
   the bulk tools chunk identically. The consolidation is planned in chunking-one-factory.md §8
   and not built.
2. **Local-path semantic is uncapped and can collapse.** `_chunker_for` passes no counter for
   semantic collections, so a document over 3,000 sentence spans becomes **one whole-document
   chunk** instead of falling back to the `fixed_token` window, as it does on GoWe. Recorded on
   #647.
3. **Method names and defaults are hard-coded in about a dozen places:** the enum in five `cwl/*.cwl` workflows (ten literals; `jats-ingest`, `embed-bulk` and `pdf-ingest` list only the four non-semantic methods), `contracts/schemas/collection_create_request.json`, `frontend/src/lib/glossary.ts`,
   `scripts/ingest_jsonl.py` choices, `frontend/src/lib/chunkers.ts` (its `SEMANTIC_METHODS`
   copy is not checked against Python), `contracts/openapi.yaml`, `collections.WHOLE_DOC_METHODS`,
   and `fixed_token` special cases in `api/deps.py`. There are three different default specs: `fixed_token`/512/64 in `Settings` (since #677), `fixed`/512/64 in `ingest_jsonl.py`, and `fixed_token`/256/32 in the shard tools and every CWL workflow.
4. **The factory does network I/O and reaches into private members:** `resolve_max_tokens`
   calls `/v1/models`, the HF check reads `_tokenizer`, and `segmentation_cache` imports
   `_make_chunk`.
5. **Small inconsistencies:** `breakpoint_percentile` vs `breakpoint_percentile_threshold`;
   `contracts/openapi.yaml` still says semantic is "not wired" on GoWe (the GoWe bridge landed in #615; #609 stays open for the factory).
6. **Harnesses build their own chunkers.** `python/scripts/eval/` evaluates configs (e.g.
   `sentence` with `chunk_size=10**9, max_tokens=512`) that no collection spec can mint, and
   never exercises the production paths' behaviours.

**Adding one method touches about 14 places** today: the algorithm, `CHUNK_METHODS` and
`make_chunker`, `chunker_config.py`, `api/deps.py`, `routers/collections.py` validation,
`config.py`, `ingest_jsonl.py`, the shard tools, five CWL enums, the frontend, the contract,
four test files and two user docs. A section-aware method also needs section spans from the
loaders (§4, C-R1).

**Before adding methods, do the consolidation:**

- one factory in `chunker_config.py` that every caller uses;
- method names and defaults declared once and imported everywhere, with the CWL enums and the
  frontend generated or tested against them;
- one token-budget policy per spec;
- an equality test that the API and the bulk tools produce identical chunk ids for the same
  spec, over all methods, with a topic-sensitive fake embedder (chunking-one-factory.md §8,
  "How the consolidation proves itself").

Now that `chunkers.py` is unfrozen, the dispatch can stay in it or move to `chunker_config.py`;
pick one.

## 3. What the evaluation has covered

From committed artifacts only:

| Harness | fixed | fixed_token | sentence | words | semantic | semantic_pooled |
|---|---|---|---|---|---|---|
| 7-way known-item (`python/scripts/eval/chunking_compare_7way_report.md`) | tied | tied | tied | tied | tied | no result |
| SciFact (`python/scripts/eval/scifact_chunk_eval_report.md`) | ≈ | ≈ (tok256 +0.023, CI [0.006, 0.040], Holm p 0.077) | ≈ | ≈ | ≈ | no result |
| Chunking study, Stage 1 Leg A/B (`docs/plans/results/stage1*`) | — | yes | yes¹ | yes¹ | yes² (~350 realised tokens) | — |
| Boundary studies (2026-09-18, 2026-09-24) | structure | — | — | — | boundaries, cost | boundaries, cost, **no retrieval** |

¹ Legacy `summed` fill, not comparable with runs after #488.
² Its `size` parameter never governed realised size (fill 0.17–1.00 across the four rows), a different caveat from ¹.

- **No method contrast has resolved under its pre-registered bar.** The one whose CI excludes zero (Leg B `words512 − fixed512`, +0.015 [+0.004, +0.027], Holm 0.028) is below δ80, on a leg whose method readings are query-construction artefacts (long-doc-judged-set gating finding).
- **The confirmation run has no outcome yet:** its labels await the human two-reader pass
  (`docs/plans/results/design/SPEC-confirmation-run-r3.md`).
- **What the harnesses cannot measure:**
  - known-item queries are insensitive to chunking;
  - BEIR abstracts are too short to test large chunks or overlap;
  - Leg A has n = 10 topics;
  - Leg B's queries were written from the gold section;
  - semantic was never compared at matched realised size.

The study already has more arms than the six methods: `header512` (contextual headers),
`parent256` (section-level small-to-big; EPACK∩ −0.112 [−0.283, +0.017] vs `fixed_tok512` on CDS at B = 16k, not significant, with the opposite sign at 4k in Stage 0, 0.43 vs 0.18; −0.018 [−0.061, +0.024] on the pointed set), neighbour expansion
`nbr*`, and `multi256+1024`.

## 4. Candidates to add, ranked

Labels are prefixed `C-` (chunking) so they don't collide with other ladders in the study, e.g. the OpenChia reproduction's R1.

Ranked for this project's question: how coarse and cheap can the production index be for
pointed evidence questions over full-length scientific articles, **with realised chunk size
controlled**.

| Rank | Arm | Tests what the current arms can't | Size control | Cost here |
|---|---|---|---|---|
| **C-R0** | **Length-yoked random-boundary control**: per document, draw segment lengths from arm X's realised distribution and cut at random sentence edges | Not a method to ship. It makes "method at matched realised size" a paired contrast, X − yoked(X), which no published study reports | by construction | trivial; no model |
| **C-R1** | **Section-bounded token packing** (JATS), caps 512 / 1024 / 2048, plus a whole-section variant | Every current indexing arm ignores structure. It is the zero-model answer to "how coarse", and it probes the 39.1% untitled-section and 55.4% evidence-past-token-1,024 findings | vs C-R0 and vs `fixed_token` at the arm's realised median; untitled sections first-class; never merge across sections | low for the study: `units.jsonl` already has unit spans, but they are **top-level `<body>` units of at least 400 characters** (`pilot_common.units_for_article`), not nested sections, so the study arm packs at that granularity; fingerprint the units alongside the sentence spans (a re-segmentation moves both). Production needs true section spans from the XML, see below |
| **C-R2** | **PIC with the abstract as the guide** (zero-LLM), plus the LLM-summary variant (Wang et al., Findings ACL 2025) | The cheapest *informed* segmentation; the bibliography notes only naive breakpoints were tested | pack groups to the token target; plus C-R0 | low–medium; same bridge as `semantic_pooled` |
| **C-R3** | **TextTiling**, plus WindowDiff against JATS section boundaries for every arm | Whether an embedder buys anything over CPU lexical cohesion; boundary agreement as a covariate | tune to `semantic_pooled`'s realised median; plus C-R0 | CPU minutes |
| **C-R4** | **Late chunking** (Günther et al. 2024) | Representation vs segmentation; the only candidate size-controlled by construction | identical spans to `fixed_token` | **high**: SFR-Embedding-Mistral is causal with last-token pooling, so it needs a new embedder and an endpoint that returns token states |
| **C-R5** | **Auto-merge delivery**: replace ≥ m retrieved chunks from one section with the section | Small-to-big that expands only where retrieval votes for it (`parent256` expanded everything) | delivery-time; charged once | no new embeddings |

**Re-run `header512` with the header counted inside the 512.** The DAPR benchmark (Wang,
Reimers & Gurevych, ACL 2024) found title prefixes *hurt* its biomedical set (Genomics
37.2 → 25.8 nDCG@10, DRAGON+).

**A production prerequisite for C-R1:** `ingestion/jats.py` `section_text` emits a `#` heading
only when a `<title>` exists. Untitled `<sec>` elements and body-lead paragraphs leave no
boundary in the flat text, so a heading-based parser would merge the 39.1% "other" class into
whatever precedes it. Section spans must come from the XML, so `jats.py` should emit them as
data (`start_char`, `end_char`, title or `None`, depth). The chunker then stamps
`section_title`, which `contracts/schemas/chunk_metadata.json` already declares.

### Deliberately not added

- **Propositions / Dense X:** they rewrite the text, so units have no character spans and the
  containment endpoint cannot score them. Worst of eight chunkers in `smigielski-2026` (Accuracy@5 69.10 vs 87.71 fixed-size), and worst of six segmentation methods in-corpus in `zhou-2026` (−15–27%).
- **LLM / agentic boundary chunkers** (LumberChunker, Meta-Chunking, MoC): LumberChunker runs at 1.11 docs/s against 1,854 for paragraph splitting in `zhou-2026`, with no in-corpus gain, and its realised size is ~40% below nominal (`duarte-2024` Table 10). Meta-Chunking and MoC are in the bibliography from abstracts only.
- **RAPTOR and LLM summary indexes:** summary nodes have no source spans and uncontrolled
  length, so neither size control nor containment is definable.

Deferred, not rejected: LLM contextual retrieval (`header512` tests the zero-cost version), and
layout-aware PDF extractors such as GROBID, Nougat or docling (a structured-ingest Stage 3
question; once C-R1 exists, run it on GROBID-inferred sections of the same articles to measure
the loss).

## 5. External libraries for section-aware chunking

**In progress (2026-10-06).** Survey of docling (`HierarchicalChunker`, `HybridChunker`),
LangChain header splitters, LlamaIndex node parsers, unstructured `chunk_by_title`, chonkie,
semchunk / semantic-text-splitter, GROBID and JATS parsers, against our hard requirements:

- character offsets that tile the source;
- token budgeting with the collection model's own tokenizer;
- JATS / Markdown / HTML / PDF input with untitled sections representable;
- CPU-only and dependency weight inside the tools image;
- licence, maintenance and determinism.

The result will be added here.

## 6. Order of work

1. **Experiment provenance helper** and the rule that every experiment records it (in flight).
2. **Consolidate construction** (§2): one factory, one method/default declaration, the equality
   test. The prerequisite for adding methods without touching 14 places each time.
3. **C-R0**, then **C-R1** in the study harness (no production change needed), with
   pre-registered size control.
4. **`jats.py` section spans plus the section-aware method in `chunkers.py`** (production C-R1),
   informed by §5.
5. **C-R2, C-R3, C-R5** as further study arms; **C-R4** only if an embedder change is on the table
   anyway.

## References

All are in [`docs/papers/bibliography.md`](../papers/bibliography.md) or
[`docs/papers/bib-inbox/04-chunking-segmentation-wide.md`](../papers/bib-inbox/04-chunking-segmentation-wide.md),
verified there, except DAPR, which was verified against the ACL PDF (Table 3: DRAGON+ passage-only 37.2 vs prepending titles 25.8 on Genomics; the paper's headline drop is 11.9 with ColBERTv2) and is not yet in the bibliography:

- Wang et al., *Document Segmentation Matters for RAG* (PIC), Findings ACL 2025;
- Günther et al., *Late Chunking*, arXiv 2409.04701;
- Wang, Reimers & Gurevych, *DAPR*, ACL 2024;
- Chen et al., *Dense X Retrieval*, EMNLP 2024;
- Zhou, Wang, Koopman & Zuccon, *Beyond Chunk-Then-Embed*, arXiv 2602.16974;
- Hearst, *TextTiling*, CL 1997;
- Pevzner & Hearst, *WindowDiff*, CL 2002;
- Sarthi et al., *RAPTOR*, ICLR 2024;
- Qu et al., *Is Semantic Chunking Worth the Computational Cost?*, Findings NAACL 2025.
