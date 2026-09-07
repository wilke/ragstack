# Confirmation run — revision 3: the population is declared, and the endpoint is split

*Written 2026-09-06; revised the same day after independent review (§9). Amends
[`SPEC-confirmation-run.md`](SPEC-confirmation-run.md) (revision 2). Every section of
revision 2 not named below stands as written; where this document and revision 2 disagree,
this document wins. Stage 0's verdict on revision 2 is in
[`../stage0/RESULTS-stage0-calibration.md`](../stage0/RESULTS-stage0-calibration.md); this
revision is the response its §6 asked for, "stated so the next revision is not a guess."*

> **Status, 2026-09-06 (second update).** §5 step 2 has now run **twice**. First under §3.7
> as drafted ([`../stage0/RESULTS-stage0b-relabel.md`](../stage0/RESULTS-stage0b-relabel.md),
> #501): neither judge passed. Then as **r3.1** — whole-sentence anchors (decision C), five
> presentations per pair, union saturation
> ([`../stage0/RESULTS-stage0b-relabel-r31.md`](../stage0/RESULTS-stage0b-relabel-r31.md),
> #507): **the copy gate now passes for both judges** (hallucinated 0.025 / 0.003; the
> closing-anchor failure went from 54 of 58 to 6 of 22), **the whether-gate passes for both**
> (0.92 / 0.98), and **self-consistency fails for both** (0.38 / 0.56), with the union of
> locations **not saturating** at five presentations (+10 % per presentation). The reviewer's
> check on the committed labels: a sentence-level majority core is no more stable (split-half
> overlap 0.46–0.49); graded per-sentence support is moderately stable (split-half r ≈ 0.59).
> **Reading:** on CDS topical relevance, *where* the evidence is is intrinsically diffuse — a
> relevant document has many sentences that justify relevance and the judges sample among
> them. No labeler protocol will make a single canonical span set stable at ≥ 0.90. The
> pointed population (§11, #506: 177 queries with construction gold) has a well-defined
> *where* by construction. The run remains stopped at step 2 pending **§10 item 4**.
> Steps 3–7 have not run. Prediction Q1 is scored **FAIL** (§7).

---

## 0. What changed, in one paragraph

Revision 2 asked "does 256 beat 2048?" on an endpoint that measured the fraction of a
topic's evidence landing in a 4,096-token context. Stage 0 showed the endpoint sat at
2–7 % for every arm — not because chunking does not matter but because a topic's evidence
lives in ~10 documents and the context holds ~1 — and that the labeler could not say
*where* evidence was twice out of three times. Revision 3 does three things. It **declares
the query population** the study optimises for, which has gated pruning since Phase 0.
It **turns the question into a non-inferiority one** — how coarse and cheap can the index
be while serving that population as well as the fine one — because that is the product
decision. And it **splits the endpoint into the two factors Stage 0 measured separately**:
whether retrieval surfaces the evidence-bearing document, and whether the delivered text
contains the evidence once it does. At Stage 0's 4k budget the containment factor sat
inside the resolvable window for every arm; the reach factor did so for one arm only and
is unmeasured at the budget this revision adopts. Their product was inside for none.

---

## 1. Decision record

Decided 2026-09-06 by the project owner, in a session with the reviewer. Recorded here so
that no later document has to infer it.

| decision | value | consequence |
|---|---|---|
| **Query population** | **Pointed, evidence-seeking questions** — a specific finding, number, method, or claim, as a research agent would ask when building an argument. Broad topical questions ("papers about X") are the minority case and are **not** what the index is optimised for. | The endpoint asks for *specific located evidence*, which is the pointed property. **The queries this run retrieves with are still TREC CDS clinical case narratives — Leg A's population** (§2, P.2 of r2). See the limitation in §1.1 and the open decision in §10. |
| **Consumer** | **Research agents**, not humans reading a results page. | Delivery budgets are agent-sized (§3.2), not chat-sized. The fleet's generators run 60k–262k contexts. |
| **Decision rule** | **Non-inferiority of the coarser or cheaper index against the fine reference.** The owner's stated preference is larger chunks (and, later, semantic chunking) *if retrieval quality is retained*. | The NI family is primary (§3.6). Superiority contrasts remain for delivery mechanisms (§3.5). |
| **Production index** | **Not changed until the experiments are done.** | Nothing in this run touches a production index or store. Stage 2 runs on the dev tenant only, as before. |
| **Delivery** | **Test passing the previous and next chunk of a match** alongside the enclosing section. | New scoring arms (§3.5). Production already implements the neighbour walk (`context_window`, #322, capped at 3 hops); no new code on the serving path. |
| **Retrieval modes** | **Vector, BM25 and hybrid must be separable.** | Three scoring passes over the same frozen indexes (§3.4). Hybrid + rerank is confirmatory; the other two are pre-registered secondaries with the mode × size interaction reported. |
| **Knowledge graph** | **Out of scope for this run; needs its own discussion.** | Per `SPEC.md` §3.5 the KG is extracted entity triples in Neo4j with neighbourhood and path queries for multi-hop questions (M4). The dev corpus has none. The graph leg is off in every arm, as in revision 2. |
| **Semantic chunking** | **Deferred to a follow-up**, not an arm here. | Phase 0 found realised chunk length, not chunking method, drives quality (SA §5 r = +0.81); semantic costs ~7× to embed. Revisit once the size answer is in, at matched realised size. |

### 1.1 What the declaration does and does not close

`long-doc-judged-set.md` §14 item 8 asked the study to *declare* which population it
optimises for before pruning on the size axis. **It is now declared.** But §14.5's gate
was written because Legs A and B resolve the size contrast with opposite signs, each
biased by its own query construction — and this run's queries are Leg A's. What carries
the "pointed" property here is the **endpoint**, not the query: `EPACK` (§3.1) counts a
document only when a *specific, minimal, located* evidence set is delivered, which is what
a pointed question needs and what a topical judgment does not measure. That is a proxy,
and it is named as one:

* **Closed:** item 8 (the declaration). Configuration pruning is no longer blocked on an
  undeclared population.
* **Not closed by this run's contrasts alone:** §14.5's prune-gate on the size axis. A
  size decision taken on CDS narratives + `EPACK` is a decision about *evidence delivery
  for topical clinical queries*, carried to pointed questions by the argument above. The
  argument is stated in the results as a **named limitation**, and §10 records the owner
  decision on whether to add a pointed-question query population (Leg B-style generated
  queries, or Leg C citances — the tiebreak §14.5 itself names) before any size decision
  is called final.

---

## 2. What Stage 0 established, and what this revision keeps from it

Three findings, each carried forward as a constraint rather than re-argued.

**The gold is not reproducible under the revision-2 protocol.** Self-consistency 0.323
against a ≥ 0.90 gate; hallucinated-span rate 0.0504 against ≤ 0.05; only 64.5 % of
verified spans at the claimed position. The labeler agreed with itself about *whether*
(0.871) and not *where* (0.32). → §3.7 changes the protocol, not the sample size. (Step 2
has since run: the change helped and was not enough — see the status banner and §3.7.)

**The endpoint factorises.** Stage 0 §5 measured, per arm at B = 4,096 on `summary`
queries (P(doc packed) is unit-weighted there; §3.1's `ERET` is document-weighted, so the
correspondence is approximate):

| arm | P(doc packed) | P(covered \| packed) |
|---|---|---|
| `fixed_tok256_ov0pct` | 0.209 | 0.174 |
| `fixed_tok512_ov0pct` | 0.091 | 0.200 |
| `fixed_tok512` (shipping) | 0.100 | 0.182 |
| `header512` | 0.145 | 0.188 |
| `parent256` | 0.064 | 0.429 |
| `fixed_tok1024_ov0pct` | 0.082 | 0.889 |
| `fixed_tok2048_ov0pct` | 0.045 | 0.800 |

The second factor is the trade the study exists to measure, and it sits inside the
[0.15, 0.90] window for every arm. The first factor at 4k is inside the window for the
256 arm only; at the **pool ceiling** (the whole D = 50 pool, an unlimited budget) reach is
0.28–0.48, and 75 % of *out-of-pool* relevant documents carry labelable evidence — the
ceiling above the ceiling is retrieval. → §3.1 makes the factors the endpoints and §5
re-measures reach at the adopted budget before anything is read.

**No budget rescues the product.** Even the whole D = 50 pool leaves the shipping arm at
0.025, and at that ceiling `EUC` tracks total supplied text (D × chunk size), not chunking
quality. → §3.2 raises the budget for the *right* reason (agent-sized delivery), not in the
hope of lifting the floor, and §3.1's conditional endpoint is what removes the floor.

Also carried forward unchanged: the corpus (§4), the six index arms and their existing
embeddings at `/rag/tmp/stage0-conf/emb/` (34 GB — **do not delete**; Stage 0b′ needs zero
new embeddings; the confirmation run may re-embed, §3.3), D = 50, the dev/confirmation
split and exposure ledger (§2), quarantine and the blinding spine (§P.9), the circularity
rule, the sensitivity arms (§10 of r2), and the Stage 2 concordance gate (§9). The
exclusion rules (§8.5.6) are **amended** in §3.1 and §3.8.

---

## 3. The design changes

### 3.1 The endpoint is split — amends §7.4, §7.5, §8.4.3, §8.5.6, D3 rule 4, P.6

Two endpoints, both computed **behind the reranker** on the packed context, both paired
per topic across arms.

**`ERET@B` — evidence-document reach.** Per topic: of the topic's evidence-bearing
documents (documents with ≥ 1 evidence unit after D3 rules 1–3), the fraction with at
least one chunk **admitted into the packed context** at budget B. It measures what
retrieval + reranking + budget do to *which documents the agent sees*. Chunk size acts on
it through ranking (small chunks surface more documents per pool) and through budget
(large chunks fit fewer documents).

**`EPACK@B` — evidence containment given reach.** Per topic, over a defined set of
evidence-bearing documents, the mean per-document fraction of evidence units fully
contained (D4, unchanged) in that document's admitted text. It measures what chunk size
and delivery mechanism do to *how much of a reached document's evidence the agent gets*.

**Which documents `EPACK` is averaged over — the estimand, fixed in advance.** For a
contrast between arms X and Y:

* **Confirmatory `EPACK`** is computed over the per-topic **intersection** of the documents
  X reached and the documents Y reached. This is a within-document paired comparison of
  delivery: the same documents, delivered by each arm's chunks. It is the only version in
  which the two arms' averages are over the same denominator.
* **Reached-set `EPACK`** — each arm averaged over the documents *it* reached — is reported
  beside it as a pre-registered secondary. It is the natural product quantity, but it is not
  commensurable across arms: the fine arm reaches ~3–4× more documents than the coarse arm
  at 16k (`floor_diagnostic.json`: 39.3 / 29.3 / 8.9 documents packed for 256 / shipping /
  2048), and the marginal documents it adds are the ones least likely to carry the evidence
  chunk, so its reached-set `EPACK` is pulled down by a selection effect that has nothing to
  do with chunking. **If the two versions disagree in sign or verdict, the contrast is
  `UNRESOLVED-BY-ESTIMAND`** (the r2 §8.4.3 rule, reused).
* The product `ERET × EPACK` (reached-set) is revision 2's `EUC` and is reported as a
  descriptive column for continuity. It is never again a primary.

**When a topic has no observation.** A topic whose intersection set is empty for a contrast
(one arm reached no evidence-bearing document, or the two arms reached disjoint ones)
contributes no confirmatory `EPACK` observation **for that contrast** — and is dropped
**from both arms** of that contrast's `EPACK` analysis. This is an outcome-dependent
exclusion, which r2 §8.5.6 forbade; it is admitted here because the alternative (imputing a
containment for documents that were not delivered) is not a measurement. Three rules keep
it honest: (i) **n_retained is reported per contrast × endpoint**, and every δ80 and the
n_retained < 60 gate are read at that contrast's own n; (ii) the dropped-topic count per
contrast is a first-class row in the results, because a coarse arm that reaches nothing on
many topics is failing `ERET`, which is a co-primary and where that failure is charged;
(iii) an **`EPACK := 0` imputation sensitivity** (the dropped topics scored as zero
containment for the arm that reached nothing) is mandatory and recovers the product
reading. r2 §8.5.6's arm-invariance clause is amended accordingly, dated 2026-09-06: it
holds for `ERET` and for every topic-level exclusion; it does not hold for the
confirmatory `EPACK`'s per-contrast document set, for the reason above.

**Why conditioning on reach is not a shortcut — and what the guard actually is.** The
objection is that conditioning on reach lets an arm that reaches nothing look good. Two
guards, and they do different jobs. The **intersection estimand** makes the `EPACK`
comparison itself fair: both arms are scored on the same documents. The **conjunctive
decision rule** (§3.6) bounds what an arm may lose on reach: it must be non-inferior on
`ERET` at ε as well. Neither alone is enough — the conjunctive rule without the
intersection would compare incommensurable averages; the intersection without the
conjunctive rule would let an arm win delivery by reaching only the easy documents.

**Denominator changes (D3).** Rules 1–3 (within-document merge, containment, no
cross-document merge) stand. **Rule 4's 12-unit document-stratified cap is deleted**: it
existed to bound a per-topic denominator, and the per-topic denominator is gone. In its
place, **≤ 4 units per document** by seeded subsampling (seed `20260918`, cap rate
reported), so one heavily-labeled document cannot dominate its own `EPACK`.
Evidence-bearing documents per topic are not capped; the count is reported. Per-document
`EPACK` is therefore quantised at 0.25; the per-topic average is over ~10 documents, so
per-topic granularity is comparable to revision 2's.

**Unit-level sensitivity (r2 §8.4.3), restated for the split.** The always-reported
pooled version is: for `ERET`, the pooled document-reach rate over all evidence-bearing
documents of the resampled topics; for `EPACK`, the pooled unit-containment rate over all
units of the intersection documents. Cluster bootstrap over topics, 10,000 resamples, seed
`20260913`, BCa; disagreement with the macro-averaged primary in sign or verdict →
`UNRESOLVED-BY-ESTIMAND`. The overlap-tolerant (≥ 0.9) containment variant of r2 §7.5 is
**carried** as a descriptive column of `EPACK`.

**Manipulation checks (§7.6), re-read on the new endpoints.** GOLD packing control:
`EPACK` ≥ 0.95 and `ERET` = 1.0 when each topic's own labeled documents are packed.
NEGATIVE control: `ERET` = 0 from grade-0 documents only — **this is 0 by construction**
(evidence-bearing documents are grade ≥ 1) and is kept as a plumbing check, not a
finding. Discrimination check unchanged. Budget-bind check at the new primary budget.

**Window.** The Stage 0 gate window [0.15, 0.90] applies to each endpoint separately, per
arm, on the dev topics. Stage 0's numbers put `EPACK` inside it for every arm at 4k
(0.174–0.889 — the 1024 arm 0.011 below the ceiling and the 256 arm 0.024 above the floor,
so **neither margin is comfortable**), and `ERET` inside it at the pool ceiling only;
`ERET` at the packed budget is re-measured at §5 step 4. **An arm whose endpoint leaves
the window on the dev topics is demoted to descriptive for that endpoint's contrasts**;
the B/D recalibration r2 §8.5.7 row 9 authorises may then be run, once, with its result
recorded before any contrast is read.

### 3.2 Budget — amends §7.3 "Budgets", P.6

**Primary B = 16,384 tokens** (tokenizer per §3.3). Secondaries B ∈ {4,096, 32,768} as
curves. Rationale: the consumer is a research agent on generators with 60k–262k windows;
16k is a delivery budget such an agent can afford per retrieval call. At the Stage 0
corpus's mean of ≈ 5,650 SFR tokens per document (184.7 M tokens / 32,663 documents;
the median is not measured — the 4,573 figure elsewhere in the record is the *step-2
dev-pilot* corpus, 4,053 documents) it holds roughly three full articles. 4,096 is
retained as the continuity budget with revision 2 and RR/BK-R; 32,768 bounds the curve
above.

The packing rule (A1, stop-at-first-non-fit, rank-1 always admitted, no truncation,
parents charged once) is unchanged. What 16k does to the pool: the fine arms' entire
D = 50 pool fits (256 × 50 = 12,800; the existing 256 index realises 10,210 generator
tokens at 16k with 39.3 documents packed), so `ERET@16k` for them equals pool reach; the
2048 arm admits **8–9 chunks** (8.9 measured on the existing index). That asymmetry is the
real trade-off and is what `ERET` is for. It is not a confound to be removed.

**On what the 16k curve already says.** `floor_diagnostic.json` at B = 16,384 gives
`EUC` 0.164 for the 2048 arm (8.9 documents) and 0.025 for the shipping arm (29.3
documents). Since `EUC` = reach × containment and containment ≤ 1, the 2048 arm's
unit-weighted reach at 16k is **at least 0.164** — comfortably over the 0.15 floor — while
the shipping arm's is bounded above by its pool ceiling of 0.282 and is ≈ 0.14 at its 4k
containment. So on Stage 0's own numbers the N3 reach contrast at 16k is a **contest
inside ε, not a foregone loss for the coarse arm**, and the predictions in §7 are stated
against that curve, not against the 4k numbers.

### 3.3 One tokenizer — amends §7.2

Stage 0 check 4 found that counting budgets in the generator's tokenizer and chunk sizes
in the embedder's under-supplies the coarse arms by ≈ 20 % at a "matched" budget
(1,630 of 2,048; mean realised 3,269 of 4,096 — the handoff's "18 %" rounds the same
measurement), in the direction that flatters fine chunks. Revision 3 counts sizes and
budgets **in one tokenizer**, and takes two paths, one per stage, each stated with what it
assumes:

* **Stage 0b′ (calibration, existing indexes): both sides in SFR tokens.** The chunks
  already are; B = 16,384 is read as SFR tokens. This is one tokenizer, costs nothing,
  and needs no interpolation. The generator-token realised budget per arm is published as
  a descriptive column. What it gives up: the budget's meaning *to the agent* is
  second-order for a σ_d and level calibration. (The earlier draft of this revision
  proposed interpolating each arm's endpoint on its budget curve to a matched
  generator-token budget; withdrawn on review — the packing rule makes realised budget a
  step function of B, so interpolation reads a value at a budget no arm realises.)
* **Confirmation run: both sides in the generator's tokenizer**, by re-chunking. The
  chunker takes an injected HF `TokenCounter` (`FixedTokenWindowChunker` requires the fast
  tokenizer's offset mapping) and is constructed with the generator's tokenizer. Chunk
  sizes 256/512/1024/2048 are then generator tokens; a 2,048-generator-token chunk is
  ≈ 2,570 SFR tokens (ratio 2,048/1,630), still under the embedder's 4,096 truncation. This
  is a re-embed at the Stage 0a cost (1.93 fleet-hours). **"The generator" is one named
  model**, not the fleet: the index is a durable artifact and its chunk boundaries are
  defined in that tokenizer, so changing the serving generator later means re-chunking.
  The generator is `mango:8003` (`Llama-4-Scout-17B-16E-Instruct`) — the one r2 §7.2
  already named and probed — unless the owner names another at freeze.

SFR and reranker token counts are reported per arm as descriptive columns and enter no
budget or size decision.

### 3.4 The offline harness is hybrid, and the modes are separable — amends §7.1, §9

Revision 2's offline harness was dense + rerank, with BM25/RRF deferred to Stage 2. The
owner's requirement is that vector, BM25 and hybrid be separable. Revision 3:

* **Three retrieval modes, one embedding.** For every index arm the harness scores three
  pools: `vector` (exact brute-force cosine, top 50), `bm25` (in-process BM25 over the same
  chunks, k1 = 1.2, b = 0.75, top 50), and `hybrid` — **the served shape**: each leg fetches
  `top_k × candidate_multiplier` = **100** (`_retrieve_fused` sets `depth = max(top_k,
  rerank_candidates) = 50`; `HybridRetriever.retrieve` multiplies by
  `candidate_multiplier = 2`), RRF with k = 60 over the two lists, shaped, truncated to 50.
  All three pools are reranked by `:50052` and packed under the same rule. Zero new
  embeddings; the BM25 index is CPU-minutes per arm.
* **Confirmatory analysis runs on `hybrid` + rerank only** — the served path. The `vector`
  and `bm25` passes are **pre-registered secondaries**, reported with the identical tables
  and CIs, labeled `DESCRIPTIVE`, and gatekept per §8.1.1.
* **The mode × size interaction is a pre-registered descriptive table**: per contrast, the
  paired difference under each mode side by side, with the cluster-bootstrap CI. BM25 has
  no embedding in it, so this table is the cleanest available read of how chunk size acts
  on lexical retrieval alone, and on the fusion.
* **Rerank-off is a fourth column on frozen pools**, as revision 2's Stage 2 already
  specified, so the reranker's contribution is isolated from pool composition.
* **`multi256+1024`** (r2 §5.2, exploratory) fuses the two arms' `vector` rankings only.
* **The in-process BM25 is checked against the served one in Stage 0b′, not later.**
  Production's Elasticsearch mapping is `text` with no analyzer — ES `standard`: UAX#29
  word boundaries, lowercase, no stemming, no stopwords — queried with a plain `match`
  (OR semantics), BM25 k1 = 1.2, b = 0.75, one shard on the dev tenant. The in-process
  tokenizer is pinned in the manifest (lowercase, Unicode word boundaries, no stemming);
  its known departures from UAX#29 on biomedical text are listed there: decimals
  ("0.05" is one token under UAX#29), apostrophes, hyphen handling, and ES's lossy
  1-byte length norm. **Stage 0b′ loads one index arm's chunks into the dev tenant's
  Elasticsearch (`:24043`, prefix `chkconf_<runid>_`, deleted afterwards with a verifying
  listing) and measures pool overlap@50 and rank correlation against the in-process BM25
  on the ten dev topics, with a pre-set threshold of overlap ≥ 0.90.** A miss is fixed in
  the harness before any contrast is read, not discovered in Stage 2 after the
  confirmation topics are labeled. `header512`'s BM25 text includes the header, as its
  embedded text does.
* **Stage 2 stands.** The served path (`HybridRetriever` + `_retrieve_fused` in-process on
  the dev tenant's Qdrant `:24041` and Elasticsearch `:24043`) remains the blocking
  concordance gate on the shortlist.

### 3.5 Delivery arms — amends §5.2, §8.1 superiority family

Scoring arms, zero embeddings. Each is a packing-time transform on an existing index arm's
reranked list; retrieval and reranking are identical to the base arm.

| arm | definition | question |
|---|---|---|
| `parent256` | *(kept)* each admitted chunk replaced by its enclosing top-level JATS `<sec>` | small-to-section expansion |
| **`nbr1_512`** | each admitted chunk of `fixed_tok512` brings its previous and next chunk (`context_window = 1`) | **the owner's proposal**: does ±1 chunk on the shipping arm recover the containment that chunk boundaries cut? |
| **`nbr1_256`** | same on `fixed_tok256_ov0pct` | does fine retrieval + ±1 delivery beat the shipping arm outright? |
| `nbr2_512` *(descriptive)* | `context_window = 2` on the shipping arm | the curve's next point |
| `multi256+1024` *(descriptive, kept)* | RRF-fuse two arms' `vector` rankings | as revision 2 |

**Neighbour accounting, stated because production and the budget walk differ.**
Production's `expand_context` attaches neighbours per source after the top-k cut and
de-duplicates only against chunks that are themselves scored sources; a chunk that is the
neighbour of two admitted sources is supplied to both. The harness is a *budget model* of
that behaviour under the packing walk, and it **de-duplicates supplied text by chunk id**:
a neighbour already admitted (as a source or as another source's neighbour) is skipped at
zero cost. This is the more favourable accounting for the `nbr*` arms and it is what a
serving path that packs a context *should* do; the per-source-duplicated accounting is
reported as a descriptive column so the difference is visible. Containment (D4) is
evaluated on the per-document char-span union of admitted chunks and their attached
neighbours. Per source, ±1 at 512 costs **3×** the base arm's tokens (source plus two
neighbours, ≈ 1,224 generator tokens on the existing index), so `nbr1_512` admits ≈ 13
sources at 16k against ≈ 40 for the base arm — `ERET` is where that cost shows.

**Superiority family, revised (Holm α = 0.05 across four; bar 0.05 on confirmatory
`EPACK@16k`):**

| id | contrast | decision |
|---|---|---|
| R1 | `fixed_tok256_ov0pct` − `fixed_tok2048_ov0pct` | the replication, as revision 2 |
| R2 | `header512` − `fixed_tok512_ov0pct` | contextual headers, as revision 2 |
| R3 | `parent256` − `fixed_tok512` | section expansion on the serving path |
| **R4** | **`nbr1_512` − `fixed_tok512`** | **turn on `context_window = 1` by default** |

`nbr1_256` and `nbr2_512` are descriptive. Each superiority contrast is read
**conjunctively with `ERET@16k`**: an arm "wins" only if it clears the bar on `EPACK` *and*
its `ERET` is non-inferior at ε under a one-sided test at **α = 0.025** (the same α as the
NI family's members); a win on containment bought by pushing documents out of the budget
is reported as exactly that.

### 3.6 Non-inferiority family, revised — amends §8.1, §8.2, §8.5, P.7

Control is what ships: `fixed_tok512` (512 / 64). Overlap is settled (Phase 0, both legs,
both metrics, powered) and N2 is **dropped**; its α goes to the new size contrast.

| id | contrast (control − candidate) | decision it settles |
|---|---|---|
| **N1** | `fixed_tok512` − `fixed_tok1024_ov0pct` | adopt 1024/0: **2.157× fewer vectors** (measured, 423,386 → 196,247), ~0.16 TB at the ~500k-article target (r2's PLAN-C estimate) |
| **N3** | `fixed_tok512` − `fixed_tok2048_ov0pct` | adopt 2048/0: **3.98× fewer vectors** (measured, 423,386 → 106,353) |

**Rule.** One-sided non-inferiority at α = 0.025 each (Bonferroni within the family;
constant 2.802 as in §8.5.1), **on both endpoints, conjunctively**: the candidate is
adopted only if the 95 % CI upper bound of (control − candidate) is below ε on
confirmatory `EPACK@16k` **and** on `ERET@16k`. An intersection-union test needs no
further α split — its size is at most the per-component α. **Its power is not the
per-component power**: the probability that both intervals clear ε is at least
P(A) + P(B) − 1 and, under independence, P(A)·P(B) — as low as 60–64 % when each endpoint
alone meets the 80 % requirement. The power gate (§5 step 5) therefore reads the **joint**
power, estimated by a joint bootstrap over the dev topics (both endpoints resampled
together, 10,000 draws, seed `20260913`), with the per-endpoint figures printed beside it.
Per-endpoint σ_d against the 0.158 requirement is a necessary condition, not the gate.

**ε = 0.05 absolute on each endpoint.** The value is r2 §8.2's; **its meaning is restated
here because the denominators changed.** On `ERET`, 0.05 is a twentieth of the topic's
evidence-bearing documents — about half a document at the ~10 per topic Stage 0 measured,
quantised at 0.1 — and at reach levels bounded by the 0.28–0.48 pool ceiling it is a
**12–18 % relative** loss of document reach; that relative reading is printed beside the
absolute one. On confirmatory `EPACK`, 0.05 is a twentieth of the evidence in the
documents both arms delivered. Because the product is what the agent sees, two endpoints
each within ε admit a product loss of up to ≈ 0.05·(`ERET` + `EPACK`), larger than r2's
single 0.05; the product's own drop is reported as a descriptive row so that this is
visible. Nothing measured at Stage 0 licenses widening ε, and this revision does not.

**What the coarse arm is expected to lose on, stated in advance — against the 16k
curve.** Stage 0 §5 says the coarse arms *win* containment (0.80–0.89 vs 0.18) and *lose*
reach (0.045–0.082 vs 0.100 at 4k). At 16k the curve in §3.2 puts the 2048 arm's
unit-weighted reach at ≥ 0.164 and the shipping arm's at ≈ 0.14–0.28, so **both N1 and N3
are genuine contests on `ERET` at 16k**; the predictions in §7 say which way each is
expected to fall and why.

### 3.7 The labeler — amends §6.1, §6.4, §6.6. Step 2 has run; read with the banner.

Stage 0's finding A was diagnosed as a protocol failure: an index-primary protocol asked a
non-reasoning model to track sentence numbers across units with dozens of sentences.
Revision 3 changed the protocol and ran it (§5 step 2, PR #501). **The diagnosis was half
right.** Quote-primary anchoring doubled Scout's self-consistency (0.323 → 0.645; 0.903 on
the best-matching-set reading, which is not the gate's reading) — but the gate is ≥ 0.90,
and the reasoning judge was *less* stable (0.419), not more. Items are restated with what
is now known:

1. **Quote-primary anchoring stays — with whole-sentence anchors.** As run, the model
   quoted the first and last ten words of each span; **54 of Scout's 58 hallucinated
   spans were the `last_words` anchor**, hand-verified as genuinely absent (a plural, a
   dropped hyphen, a trimmed parenthetical). The closing anchor is replaced by **one
   verbatim anchor plus a sentence count**, or by quoting the whole final sentence — the
   next run states which — **decided: whole-sentence quotes (§10 item 3)**. Located quotes are snapped outward to whole-sentence boundaries
   within one unit (D1); a quote crossing a unit boundary becomes a multi-span set (a
   change from Stage 0, which dropped 14 such spans as unresolvable; recorded in #501 §4).
2. **Two judges, and no primary yet.** `mango:8003` (Llama-4-Scout) copies imperfectly and
   enumerates broadly (2.10 spans per positive pair); `mango:8004` (Qwen3.6-35B-A3B, run as
   a **reasoning** judge — a change from r2 §6.1's `enable_thinking: false` agreement-only
   role, declared here) copies faithfully (hallucinated 0.025, 335/335 quotes in the named
   unit) and **under-enumerates** (1.15 spans per positive pair; 65.9 % of its selected
   text lies inside Scout's, 16.2 % the reverse). The rule "primary judge = whichever
   passes self-consistency" **has no candidate**, and "pick the cleaner copier" would pick
   the judge with the omission failure the endpoint is most sensitive to. Neither judge is
   primary until §3.7 item 5 is decided.
3. **Gates, with results:** self-consistency ≥ 0.90 — **FAIL** both (0.645 / 0.419);
   hallucinated-span rate ≤ 0.05 — **FAIL** Scout (0.082, Wilson upper 0.105), **PASS**
   Qwen (0.025); document-level whether-agreement ≥ 0.90 — **PASS** both (0.968 / 0.968).
   The minimality audit (r2 §6.4 rule 3) was deliberately not run — it was an instrument
   failure in Stage 0 (29/29 empty) — so minimality shrinkage is **absent, not zero**.
4. **Item 8, the two-independent-human-reader validation, remains the gate on the labels
   and is now more necessary, not less**: it is the only instrument that can say which of
   two mutually-disagreeing judges is closer to right, and which of them is *missing*
   evidence. The package is rebuilt on the **union of both judges' r3 labels**, each span
   tagged with its judge, so the read yields per-judge wrong-location rates and a
   missed-evidence rate against the union. The Stage 0 readsheets are superseded. Cost
   32–48 person-hours. No agent read substitutes for it.
5. **An enumeration statistic is added as a gate.** Both judges agree on *whether*
   (0.93–0.97) and disagree on *where* (median span-union Jaccard 0.08). The endpoint's
   denominator (D3, alternative locations) is exactly the thing an under-enumerating
   labeler corrupts. The next labeling run reports, per judge, **enumeration recall against
   the human-marked evidence sets from item 4's read** (the fraction of human-found sets the
   judge also found, at ≥ 0.5 span-union Jaccard), with a bar of ≥ 0.80 for a judge to be
   primary. Until the read exists, the cross-judge asymmetric-coverage statistic from #501
   is reported in its place, labeled as a proxy.
6. **The location or a location — decided for D4, open for the labeler.** D4 already counts
   a unit covered when *any one complete* evidence set is contained. So `EPACK` is robust
   to a gold that lists *more* valid locations than any single judge finds; it is not robust
   to a gold that lists *fewer*. The stable statistic the study needs is therefore the
   **union of plausible locations**, and the question is whether that union saturates. §10
   records the owner's decision on how to build it (multi-sample union per judge; a
   third-family judge).

### 3.8 What does not change

Corpus and assembly (§4). The six index arms (§5.1) and their embeddings. D = 50 and
`rerank_candidates = 50`. The dev/confirmation split, the exposure ledger, and the
quarantine of confirmation retrieval outputs until label freeze. The circularity rule. The
sensitivity arms (§10 of r2). The multiple-comparison ledger. The house conventions:
failures reported, `UNRESOLVED` never called null, δ80 beside every descriptive row, cost
columns beside every quality column.

**Amended, not unchanged:** the exclusion rules (§8.5.6). Topic-level exclusions are now
on evidence-bearing *document* count — a topic with < 3 evidence-bearing documents is
excluded, identically for all arms, fixed at label freeze. Stage 0 row 6 projected
n_retained = 80 on the old unit-count rule; the new rule needs its own projection from
the confirmation topics' non-outcome data (13 topics have fewer than 40 judged relevants,
the smallest 8) and is a `[FROZEN-AT-STAGE-0]` item. The per-contrast `EPACK` document set
is governed by §3.1.

---

## 4. Cost

| item | cost | basis |
|---|---|---|
| New embeddings for Stage 0b′ | **0** | existing indexes, SFR-token budget (§3.3) |
| BM25 indexes, 6 arms | CPU minutes | in-process |
| Three-mode scoring + rerank, dev topics | **minutes** on `:50052` | Stage 0 reranked 9,000 pairs for 6 arms × 2 variants; three modes ≈ 18–20k pairs at 391–1,037 pairs/s (r2 §11). Delivery arms add no rerank passes |
| Dev-tenant Elasticsearch concordance check (§3.4) | minutes; one arm's chunks loaded and deleted | dev tenant only |
| Relabel the dev set, two judges (§5 step 2) — **measured** | 879 requests, 10.31 M prompt + 1.78 M completion tokens, **47 min** wall, 308 pairs + 31 duplicates + bias-bound sample | #501 §5 |
| Two-reader human validation (item 8) | **32–48 person-hours** | the blocking item; unchanged |
| Confirmation run re-embed in the generator's tokenizer (§3.3) | ~1.93 fleet-hours | Stage 0a, measured; only if Stage 0b′ passes |

GPUs 6 and 7 stay reserved and untouched. No store client is constructed in the offline
scoring harness; the one Elasticsearch load in §3.4 and Stage 2 use the dev tenant only.

---

## 5. Order of operations

1. **Freeze revision 3.** This document's hash goes in the manifest beside revision 2's.
2. **Relabel the development set** under §3.7 with both judges. Read the three machine
   gates. If neither judge passes self-consistency ≥ 0.90, **stop** — the protocol needs
   redesign, not another run. **→ Ran 2026-09-06. Neither passed. Stopped.** See the banner
   and §3.7 for what changes before step 2 runs again (item 1's anchor fix; item 6's union
   plan per §10).
3. **Rebuild the item-8 package** on the union of both judges' r3 labels (§3.7 item 4);
   **schedule the two-reader read**. It runs in parallel with step 4 and gates step 6.
4. **Stage 0b′** — re-run the calibration on the split endpoints at B = 16,384 (and 4,096,
   32,768) in SFR tokens, three modes, all arms including `nbr1_512`, `nbr1_256`,
   `nbr2_512`, on the existing indexes; the dev-tenant BM25 concordance check (§3.4).
   Produce the §8.5.7 table for each of `ERET` and confirmatory `EPACK` per contrast, the
   joint-power figure, the dropped-topic counts, and the mode × size table.
5. **Read the gate.** Both endpoints must sit in [0.15, 0.90] per arm on the dev topics;
   n_retained per contrast × endpoint; **joint** power at Δ ∈ {0, 0.01, 0.02} per
   contrast against 80 %. Three outcomes as in P.7. **ε does not move.**
6. **Confirmation run** on the 80 topics, only if step 5 passes and step 3's κ is in hand.
   Re-embed in the generator's tokenizer (§3.3).
7. **Stage 2** on the dev tenant, concordance gate per §3.4.

No confirmation topic is touched before step 6. Their retrieval outputs remain quarantined.

---

## 6. Out of scope, and why

* **Changing the production index.** Owner's decision: not until the experiments are done.
* **Semantic chunking.** Follow-up at matched realised size; ~7× embedding cost.
* **The knowledge graph.** Needs its own discussion of what it is for this corpus (entity
  triples? section-level citation graph? both?) before a graph leg can be an arm. The dev
  corpus has no KG; the graph leg is off everywhere in this run.
* **Query-time knobs** (`top_k`, `max_per_doc`, boilerplate demotion, multi-query
  rewrites): sensitivity rows at most; they do not force a rebuild and can be tuned later.

---

## 7. Predictions on record

House convention: written before the run; scored in the results; failures reported. These
are **Q1–Q9**, so they do not collide with revision 2's frozen P1–P8 (r2 §8.6 / P.10), which
this revision **supersedes**: r2's P2 concerned N2, which is dropped; the others are
re-expressed below where they survive.

| # | prediction | basis | confidence | result |
|---|---|---|---|---|
| Q1 | Quote-primary anchoring lifts self-consistency above 0.90 for at least one judge | the failure mode was index tracking, not evidence recognition (whether-agreement 0.871) | ~60 % | **FAIL** — 0.645 / 0.419. Partial: Scout doubled; 0.903 on the best-matching-set reading, which is not the gate's |
| Q2 | Confirmatory `EPACK@16k` sits in [0.15, 0.90] for every arm | Stage 0 measured 0.174–0.889 at 4k. The 1024 arm is 0.011 under the ceiling and the 256 arm's reached-set value *falls* as reach grows (23 documents added between 4k and 16k with no new unit contained); the intersection estimand removes the second effect | ~60 % | — |
| Q3 | `ERET@16k` clears 0.15 for every arm | 16k curve: 2048 ≥ 0.164 by arithmetic; fine arms pack the whole pool (reach 0.28–0.48). The **shipping arm** (≈ 0.14 at its 4k containment) is the risk, not the 2048 arm | ~70 % | — |
| Q4 | N1 (1024) is non-inferior on `EPACK` and a genuine contest on `ERET` | Stage 0 §5 factor table; 16k curve | ~50 % on the conjunctive rule | — |
| Q5 | N3 (2048) is non-inferior on `EPACK` and **fails** `ERET` non-inferiority at 16k | 8.9 documents packed vs 29.3 for the shipping arm; but the 16k curve puts its unit-weighted reach at ≥ 0.164 against the shipping arm's ≈ 0.14–0.28, so this is a contest, not the foregone loss the 4k numbers suggest | ~55 % | — |
| Q6 | R4 (`nbr1_512`) clears the 0.05 superiority bar on confirmatory `EPACK` | `parent256` lifted containment 0.18 → 0.43 by supplying surrounding text; ±1 chunk is a cheaper version of the same move | ~65 % | — |
| Q7 | R4's `ERET` is non-inferior at 16k | ±1 costs 3× per source: ≈ 13 sources at 16k against ≈ 40 for the base arm, so the arm reaches fewer documents; whether it loses more than half a document per topic is the bet | ~45 % | — |
| Q8 | The mode × size table shows BM25 favouring coarser chunks than dense does | BM25 rewards term coverage, which grows with chunk length | ~55 % | — |
| Q9 | Rerank-off reverses at least one contrast's sign relative to rerank-on | Phase 0: dense and reranked orderings correlate only +0.55 | ~70 % | — |

---

## 8. Cross-references this revision closes, and the one it does not

* `long-doc-judged-set.md` §14 item 8 — **closed** by §1 (population declared).
* `long-doc-judged-set.md` §14.5 prune-gate on the size axis — **not closed by this run's
  contrasts alone**; see §1.1 and §10.
* `HANDOFF-2026-09-06.md` §5 "what has to change before it runs again" items 1–4 —
  answered by §3.1, §3.7, §3.3, §3.6 respectively.
* `HANDOFF-2026-09-06.md` §14 items 4 and 5 — this document.
* Stage 0 §6 "what would have to change" items 1–4 — same mapping.
* **`../stage0/RESULTS-stage0b-relabel.md`** — the record of §5 step 2, and of why it
  stopped. Its §3 items 1–4 are taken up in §3.7 items 1, 5, 6 and 4.

---

## 9. Change log — review-driven revisions, 2026-09-06

An independent reviewer with no session context read the first draft against revision 2,
the Stage 0 record, #501, and the pinned production code. Its blocking findings and what
changed:

| finding | change |
|---|---|
| §0 and the PR body claimed both factors were "inside the window" at Stage 0; reach was inside for one arm at 4k | §0 and §2 restated; §3.1 window rule adds demotion |
| §1 declared a pointed population and §8 claimed §14.5's prune-gate closed, while the run's queries are Leg A's | §1.1 added: declaration closes item 8, not the prune-gate; the endpoint-as-proxy argument is named as a limitation; §10 records the decision |
| a topic with no packed evidence-bearing document was an outcome-dependent, arm-dependent exclusion left undefined for pairing | §3.1: confirmatory `EPACK` on the intersection of reached documents; drop-from-both rule; n_retained per contrast × endpoint; `EPACK := 0` sensitivity; §8.5.6 amended and dated |
| the conjunctive rule guarded the decision but not the estimand (reached-set `EPACK` compares different document sets) | same — intersection estimand confirmatory, reached-set secondary, `UNRESOLVED-BY-ESTIMAND` on disagreement |
| power gate stated per endpoint while the decision is conjunctive; α for the `ERET` check on superiority contrasts unstated | §3.6: joint bootstrap power is the gate; α = 0.025 stated |
| per-leg hybrid depth mis-stated as 50; production fetches 100 | §3.4 corrected |
| #501 had executed step 2 and stopped; the draft, the handoff and the README still presented it as the next run | banner; §3.7 rewritten with results; Q1 scored; §4 measured cost; §5 step 2 annotated; §8 cross-reference |
| numbers: 2.29× → 2.157× measured; 4.6× → 3.98×; ~2,400 → ≈ 2,570 SFR; 18 % → ≈ 20 % (check 4); corpus median 4,573 was the dev-pilot's; ~450 pairs → 308; rerank "~1 GPU-hour" → minutes; arm count | §3.2, §3.3, §3.6, §4 corrected |
| predictions numbered P1–P9 collided with r2's frozen P1–P8; Q3/Q5's bases used 4k numbers when the 16k curve in the same artifact points the other way | renumbered Q1–Q9; bases restated against the 16k curve; r2's list superseded explicitly |
| budget-curve interpolation for Stage 0b′ was not valid under a stop-at-first-non-fit walk | withdrawn; SFR-token budget for Stage 0b′ (§3.3) |
| "the generator's tokenizer" named three generators | one named generator (§3.3) |
| in-process BM25 vs Elasticsearch deferred to Stage 2, after confirmation labeling | dev-tenant concordance check moved into Stage 0b′ with a threshold (§3.4) |
| neighbour de-duplication contradicted production semantics; Q7's basis said 2× where it is 3× | §3.5 accounting stated; Q7 restated |
| ε's grounds changed with the denominators while the text said they had not | §3.6 restated per endpoint, with the relative reading |
| §2 said exclusion rules unchanged; §3.8 changed them | §3.8 marked amended; projection made a `[FROZEN-AT-STAGE-0]` item |

Non-blocking items (Qwen's reasoning-mode flip declared; `multi256+1024` mode named;
unit-level sensitivity and overlap-tolerant variant restated; NEGATIVE control marked as
0 by construction; D3 cap seed named) are folded into the sections above.

---

## 10. Open decisions for the owner

Recorded here so the next session does not have to reconstruct them.

1. ~~A pointed-question query population (§1.1).~~ **Decided 2026-09-06: add one.** Design
   in §11. Stage 0b′ proceeds under the proxy meanwhile; the confirmation run requires the
   coarse arm to pass on both populations, subject to §11's power clause.
2. **How to build the union gold (§3.7 item 6).** Proposed: (a) sample each local judge
   five times per pair under the whole-sentence-anchor protocol (Scout ≈ 1.3 s per pair,
   Qwen ≈ 9 s; ≈ 3 fleet-hours total, no API cost) and measure whether the union
   saturates; (b) add one third-family judge — Claude Opus 5 (`claude-opus-5`) via the
   Anthropic API, same prompt, same gates, model id pinned, raw responses hashed and
   stored — one to three samples at roughly $50 per pass on list pricing; the corpus is
   public PMC Open Access text and public TREC topics. (a) answers "does the union
   saturate"; (b) answers "can any labeler pass the gates as written" and breaks the
   two-judge tie on *where*. Neither substitutes for the human read; both are scored
   against it.
4. **What "where" means on the CDS population — the decision r3.1 forces.** Three options,
   each measured on the committed r3.1 labels:
   (a) **Graded support gold on CDS.** Per sentence, the fraction of the ten readings (two
   judges × five presentations) that include it; `EPACK` becomes the fraction of a
   document's support mass that the delivered text contains. Stability of the weights is
   moderate (split-half r ≈ 0.59, top-3 overlap 0.60), stated as the instrument's reliability
   rather than gated at 0.90. The human read validates the top-supported sentences.
   (b) **Move the containment primary to the pointed population**, where gold is the
   construction passage and the labeler is not needed for it; CDS carries the reach endpoint
   (`ERET`, which needs only *whether*, at 0.92–0.98 agreement) and a descriptive graded
   `EPACK`. This is the cleanest reading of what the data say and of §1's declaration.
   (c) **Keep chasing a canonical span set** — a third-family judge, more presentations. The
   saturation curve argues against it: the union grows, it does not converge.
   Recommendation: **(b), with (a) as CDS's descriptive containment.** Neither changes ε.
3. ~~The anchor fix (§3.7 item 1)~~ **Decided 2026-09-06: whole-sentence quotes.** The
   labeler quotes the first and last sentence of each span in full (one sentence when the
   span is one sentence); each quote is located by exact-then-normalised match of its first
   and last eight words, both required to land in the same sentence; a quote that does not
   locate is a hallucinated span. Owner's stated ground: evidence that is cited must not
   *appear* hallucinated either — a correct span discarded for a bad receipt is a
   correctness failure of the pipeline, not a cost to be tolerated.

---

## 11. The pointed-question population — decided 2026-09-06

**What.** A second query population of the declared shape — short, specific, one finding
or number or method, answered by one passage — generated on the **same 32,663-document
Stage 0 corpus** so that both populations retrieve against one index per arm. Construction
follows the Leg B re-run (`../pilots/RESULTS-legB-rerun.md`, `legb2_gen.py`): a query is
written *from* a deep section of a source article, must name a rare entity that occurs in
that section, is held to ≈ 12 words and one clause, and is screened for leakage — IDF
overlap against the title + abstract below the 0.80 `title_answerable` bar, and the entity
absent from the front matter.

**Why it was added, and the four reasons it might not have been.** The declared
population is pointed questions; the run's queries are Leg A's clinical narratives; the
reach endpoint is driven by the query the retriever sees, and narratives are the easier
test for coarse chunks (§1.1). Against adding it: (i) generated queries can be too easy
to separate arms — Phase 0 measured Leg B at ~15× easier than CDS, `PH@10` ≈ 0.97–0.99;
(ii) the construction passage is one location, and D4 counts any complete set, so an arm
delivering a *different* valid passage is scored as missing; (iii) two populations × two
endpoints is a four-way conjunction and the pointed set may be underpowered — the Leg B
pilot's σ_d of 0.152 on document nDCG implied ~1,500 queries for δ = 0.02; (iv) it delays
the confirmation run by a generation pass, a labeling pass and a ~50-pair human read.
Each has a guard below.

**Guards, pre-registered.**

1. **Discrimination gate before it counts.** On the development topics' share of the set,
   the pointed population must pass the same check the CDS set passed (top-10 document sets
   differ between the size extremes for ≥ 25 % of queries) **and** its confirmatory
   `EPACK@16k` must sit inside [0.15, 0.90] for every arm. A population every arm passes
   is not a gate; if it fails discrimination it is reported as a descriptive population,
   labeled so, and the CDS population alone carries the decision under §1.1's limitation.
2. **Gold = construction passage ∪ labeler-found alternatives.** The section the query was
   written from is the primary evidence set, recorded at generation (no labeler needed for
   it). Once a labeler passes §3.7's gates, its found sets on the same (query, document)
   pairs are unioned in under D3 rules 1–3. The human read on this population (≈ 50 pairs,
   two readers, §6.6.2 protocol) asks one extra question per pair: *does a delivered passage
   other than the construction passage answer the question?* Its rate bounds how much
   construction-only gold under-counts.
3. **Sized from its own spread, not from a guess.** Stage 0b′ generates **≥ 150** queries on
   the dev topics' documents and measures σ_d on both endpoints per contrast. The
   confirmation-run count is set from that measurement at the same 80 % power and α as
   the CDS population, with the joint power of the four-way conjunction printed. If the
   count exceeds what the budget allows (**cap: 600 queries**), the pointed population is
   re-declared a **reported secondary** *before* freeze, with its projected power stated,
   and the decision reverts to the CDS population under §1.1's limitation. It is never a
   silent fourth gate.
4. **Overlap with the labeler work, not after it.** Generation and the construction-gold
   half need no labeler and run now; the union and the human read wait for §3.7.

**Decision rule, amended (§3.6).** A candidate index arm is adopted only if it is
non-inferior at ε on both endpoints on **both** populations (four one-sided tests, each at
α = 0.025; the family's size is bounded by the per-component α as before; joint power
per guard 3). The superiority family (§3.5) is read on the CDS population as registered,
with the pointed population's reading reported beside it as descriptive.

**Cost.** Generation: ~150 queries at Stage 0b′, up to 600 for the confirmation run, on
`mango:8003` at the Leg B re-run's measured 43 min per 400 — under two hours. No new
embeddings. Human read: ≈ 50 pairs × 2 readers ≈ 10–15 person-hours. Labeling of the
union: one pass over ≤ 600 pairs once a labeler passes.

**Toward the real population.** Production hashes query text by design. A config flag to
log query text on the **dev and demo tenants only**, with a notice to their users, is
filed as an issue so that a set of real agent queries accumulates and can replace the
generated one; it is not part of this run.
