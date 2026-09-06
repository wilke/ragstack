# Confirmation run — revision 3: the population is declared, and the endpoint is split

*2026-09-06. Amends [`SPEC-confirmation-run.md`](SPEC-confirmation-run.md) (revision 2).
Every section of revision 2 not named below stands as written. Where this document and
revision 2 disagree, this document wins. Stage 0's verdict on revision 2 is in
[`../stage0/RESULTS-stage0-calibration.md`](../stage0/RESULTS-stage0-calibration.md); this
revision is the response its §6 asked for, "stated so the next revision is not a guess."*

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
contains the evidence once it does. Both were inside the resolvable window at Stage 0 even
as their product was not.

---

## 1. Decision record

Decided 2026-09-06 by the project owner, in a session with the reviewer. Recorded here so
that no later document has to infer it.

| decision | value | consequence |
|---|---|---|
| **Query population** | **Pointed, evidence-seeking questions** — a specific finding, number, method, or claim, as a research agent would ask when building an argument. Broad topical questions ("papers about X") are the minority case and are **not** what the index is optimised for. | Leg B's construction is the model of the target; Leg A's topical judgments remain the human anchor for corpus and relevance, not for the endpoint. Closes item 8 of `long-doc-judged-set.md` §14 and the §14.5 gate. |
| **Consumer** | **Research agents**, not humans reading a results page. | Delivery budgets are agent-sized (§3.2), not chat-sized. The fleet's generators run 60k–262k contexts. |
| **Decision rule** | **Non-inferiority of the coarser or cheaper index against the fine reference.** The owner's stated preference is larger chunks (and, later, semantic chunking) *if retrieval quality is retained*. | The NI family is primary (§3.6). Superiority contrasts remain for delivery mechanisms (§3.5). |
| **Production index** | **Not changed until the experiments are done.** | Nothing in this run touches a production index or store. Stage 2 runs on the dev tenant only, as before. |
| **Delivery** | **Test passing the previous and next chunk of a match** alongside the enclosing section. | New scoring arms (§3.5). Production already implements the neighbour walk (`context_window`, #322, capped at 3 hops); no new code on the serving path. |
| **Retrieval modes** | **Vector, BM25 and hybrid must be separable.** | Three scoring passes over the same frozen indexes (§3.4). Hybrid + rerank is confirmatory; the other two are pre-registered secondaries with the mode × size interaction reported. |
| **Knowledge graph** | **Out of scope for this run; needs its own discussion.** | Per `SPEC.md` §3.5 the KG is extracted entity triples in Neo4j with neighbourhood and path queries for multi-hop questions (M4). The dev corpus has none. The graph leg is off in every arm, as in revision 2. |
| **Semantic chunking** | **Deferred to a follow-up**, not an arm here. | Phase 0 found realised chunk length, not chunking method, drives quality (SA §5 r = +0.81); semantic costs ~7× to embed. Revisit once the size answer is in, at matched realised size. |

---

## 2. What Stage 0 established, and what this revision keeps from it

Three findings, each carried forward as a constraint rather than re-argued.

**The gold is not reproducible under the revision-2 protocol.** Self-consistency 0.323
against a ≥ 0.90 gate; hallucinated-span rate 0.0504 against ≤ 0.05; only 64.5 % of
verified spans at the claimed position. The labeler agreed with itself about *whether*
(0.871) and not *where* (0.32). → §3.7 changes the protocol, not the sample size.

**The endpoint factorises, and both factors were resolvable.** Stage 0 §5 measured, per
arm at B = 4,096:

| arm | P(doc packed) | P(covered \| packed) |
|---|---|---|
| `fixed_tok256_ov0pct` | 0.209 | 0.174 |
| `fixed_tok512` (shipping) | 0.100 | 0.182 |
| `parent256` | 0.064 | 0.429 |
| `fixed_tok1024_ov0pct` | 0.082 | 0.889 |
| `fixed_tok2048_ov0pct` | 0.045 | 0.800 |

The first factor is the retrieval ceiling (only 0.28–0.48 of evidence-bearing documents
reach the top-50 pool at all; 75 % of *out-of-pool* relevant documents carry labelable
evidence). The second factor is the trade the study exists to measure, and it sits inside
the [0.15, 0.90] window for every arm. The product does not. → §3.1 makes the factors the
endpoints.

**No budget rescues the product.** Even the whole D = 50 pool leaves the shipping arm at
0.025, and at that ceiling `EUC` tracks total supplied text (D × chunk size), not chunking
quality. → §3.2 raises the budget for the *right* reason (agent-sized delivery), not in the
hope of lifting the floor, and §3.1's conditional endpoint is what removes the floor.

Also carried forward unchanged: the corpus (§4), the six index arms and their existing
embeddings at `/rag/tmp/stage0-conf/emb/` (34 GB — **do not delete**; revision 3 needs zero
new embeddings), D = 50, the dev/confirmation split and exposure ledger (§2), quarantine
and the blinding spine (§P.9), the circularity rule, the exclusion rules (§8.5.6), and the
Stage 2 concordance gate (§9).

---

## 3. The design changes

### 3.1 The endpoint is split — amends §7.4, §7.5, D3 rule 4, P.6

Two endpoints, both computed **behind the reranker** on the packed context, both paired
per topic across arms.

**`ERET@B` — evidence-document reach.** Per topic: of the topic's evidence-bearing
documents (documents with ≥ 1 evidence unit after D3 rules 1–3), the fraction with at
least one chunk **admitted into the packed context** at budget B. This is Stage 0's
P(doc packed), now named. It measures what retrieval + reranking + budget do to *which
documents the agent sees*. Chunk size acts on it through ranking (small chunks surface
more documents per pool) and through budget (large chunks fit fewer documents).

**`EPACK@B` — evidence containment given reach.** Per topic: over the topic's
evidence-bearing documents that *were* packed, the mean per-document fraction of evidence
units fully contained (D4, unchanged) in that document's admitted text. This is Stage 0's
P(covered | packed), now named. It measures what chunk size and delivery mechanism do to
*how much of a reached document's evidence the agent actually gets*. A topic with no packed
evidence-bearing document contributes no `EPACK` observation for that arm (its `ERET` is 0;
the missing-`EPACK` rate is reported per arm and is itself a finding).

The product `ERET × EPACK` is revision 2's `EUC` and is reported as a descriptive column
for continuity. It is never again a primary.

**Denominator changes (D3).** Rules 1–3 (within-document merge, containment, no
cross-document merge) stand. **Rule 4's 12-unit document-stratified cap is deleted**: it
existed to bound a per-topic denominator, and the per-topic denominator is gone. In its
place, **≤ 4 units per document** by seeded subsampling (cap rate reported), so one
heavily-labeled document cannot dominate its own `EPACK`. Evidence-bearing documents per
topic are not capped; the count is reported.

**Why per-document `EPACK` is not a shortcut.** The obvious objection is that
conditioning on reach lets an arm that reaches nothing look good. That is why `ERET` is
carried as a co-primary and the decision rules (§3.6) are conjunctive: an arm must be
non-inferior on *both* to be adopted, and a superiority arm must not lose on `ERET` while
winning on `EPACK`. Neither endpoint alone licenses a decision.

**Manipulation checks (§7.6), re-read on the new endpoints.** GOLD packing control:
`EPACK` ≥ 0.95 and `ERET` = 1.0 when each topic's own labeled documents are packed.
NEGATIVE control: `ERET` ≤ 0.05 from grade-0 documents only. Discrimination check
unchanged. Budget-bind check at the new primary budget.

**Window.** The Stage 0 gate window [0.15, 0.90] applies to each endpoint separately.
Stage 0's numbers put `EPACK` inside it for every arm (0.174–0.889) and `ERET` inside it
at the pool ceiling (0.28–0.48); `ERET` at the packed budget must be re-measured (§5).

### 3.2 Budget — amends §7.3 "Budgets", P.6

**Primary B = 16,384 generator tokens.** Secondaries B ∈ {4,096, 32,768} as curves.
Rationale: the consumer is a research agent on generators with 60k–262k windows; 16k is a
delivery budget such an agent can afford per retrieval call and holds ~3–4 full articles
at the corpus median (4,573 SFR tokens). 4,096 is retained as the continuity budget with
revision 2 and RR/BK-R; 32,768 bounds the curve above.

The packing rule (A1, stop-at-first-non-fit, rank-1 always admitted, no truncation,
parents charged once) is unchanged. Note what 16k does to the pool: 50 × 256 = 12,800
tokens, so the fine arms' entire D = 50 pool fits and `ERET@16k` for them equals pool
reach; 50 × 2,048 ≫ 16k, so the coarse arms admit ~8 chunks. That asymmetry is the real
trade-off and is what `ERET` is for. It is not a confound to be removed.

### 3.3 One tokenizer — amends §7.2

Stage 0 check 4 found that counting budgets in the generator's tokenizer and chunk sizes in
the embedder's under-supplies the coarse arms by ~18 % at a "matched" budget, in the
direction that flatters fine chunks. Revision 3 counts **both in the generator's tokenizer**.
The chunker takes an injected HF `TokenCounter` (`FixedTokenWindowChunker` requires the
fast tokenizer's offset mapping); it is constructed with the served generator's tokenizer,
probed live at freeze as revision 2 already requires. Chunk sizes 256/512/1024/2048 are
therefore generator tokens. SFR and reranker token counts are reported per arm as
descriptive columns and enter no budget or size decision.

**Consequence for the existing indexes.** The six arms at `/rag/tmp/stage0-conf/emb/` were
chunked in SFR tokens. A 2,048-generator-token chunk is ~2,400 SFR tokens, still under the
embedder's 4,096 truncation, so re-chunking in the generator's tokenizer is *safe* — but it
is a **re-embed** (≈ 1.93 fleet-hours, the Stage 0a cost). The alternative that keeps the
existing indexes is to **publish realised generator-token budgets per arm and read every
contrast at matched realised budget** (interpolated on the budget curve). Revision 3
adopts the second for Stage 0b' (§5) so the calibration re-run costs no GPU time, and the
first for the confirmation run proper, where the re-embed is affordable and removes the
interpolation. Both realised-budget tables are published either way.

### 3.4 The offline harness is hybrid, and the modes are separable — amends §7.1, §9

Revision 2's offline harness was dense + rerank, with BM25/RRF deferred to Stage 2. The
owner's requirement is that vector, BM25 and hybrid be separable. Revision 3:

* **Three retrieval modes, one embedding.** For every index arm the harness scores three
  pools: `vector` (exact brute-force cosine, D = 50, as before), `bm25` (in-process BM25
  over the same chunks, k1 = 1.2, b = 0.75, D = 50), and `hybrid` (per-leg depth D = 50,
  RRF k = 60, union truncated to 50 by fused rank — the shape of `_retrieve_fused` at
  `depth = max(top_k, rerank_candidates)`). All three pools are reranked by `:50052` and
  packed under the same rule. Zero new embeddings; the BM25 index is CPU-minutes per arm.
* **Confirmatory analysis runs on `hybrid` + rerank only** — the served path. The `vector`
  and `bm25` passes are **pre-registered secondaries**, reported with the identical tables
  and CIs, labeled `DESCRIPTIVE`, and gatekept per §8.1.1.
* **The mode × size interaction is a pre-registered descriptive table**: per contrast, the
  paired difference under each mode side by side, with the cluster-bootstrap CI. BM25 has
  no embedding in it, so this table is the cleanest available read of how chunk size acts
  on lexical retrieval alone, and on the fusion.
* **Rerank-off is a fourth column on frozen pools**, as revision 2's Stage 2 already
  specified, so the reranker's contribution is isolated from pool composition.
* **Stage 2 stands.** The served path (`HybridRetriever` + `_retrieve_fused` in-process on
  the dev tenant's Qdrant `:24041` and Elasticsearch `:24043`) remains the blocking
  concordance gate. Its purpose narrows: with the offline harness now hybrid, Stage 2 checks
  that the in-process BM25 and the served Elasticsearch BM25 (analyzer, parameters) agree
  in sign per contrast, not that hybrid agrees with dense.

The in-process BM25's tokenisation is pinned in the manifest (lower-case, Unicode word
boundaries, no stemming — the closest cheap approximation of ES's `standard` analyzer);
the Stage 2 concordance gate is what catches a divergence that matters.

### 3.5 Delivery arms — amends §5.2, §8.1 superiority family

Scoring arms, zero embeddings. Each is a packing-time transform on an existing index arm's
reranked list; retrieval and reranking are identical to the base arm.

| arm | definition | question |
|---|---|---|
| `parent256` | *(kept)* each admitted chunk replaced by its enclosing top-level JATS `<sec>` | small-to-section expansion |
| **`nbr1_512`** | each admitted chunk of `fixed_tok512` brings its previous and next chunk (`context_window = 1`, production semantics: neighbours attach to the source, are budget-charged, deduplicated against already-admitted text, and never re-ranked) | **the owner's proposal**: does ±1 chunk on the shipping arm recover the containment that chunk boundaries cut? |
| **`nbr1_256`** | same on `fixed_tok256_ov0pct` | does fine retrieval + ±1 delivery beat the shipping arm outright? |
| `nbr2_512` *(descriptive)* | `context_window = 2` on the shipping arm | the curve's next point |
| `multi256+1024` *(descriptive, kept)* | RRF-fuse two arms' dense rankings | as revision 2 |

Neighbour text is charged to the budget exactly as production would supply it. A neighbour
that is itself an admitted chunk is not double-counted. Containment (D4) is evaluated on
the per-document char-span union of admitted chunks *and* their attached neighbours.

**Superiority family, revised (Holm α = 0.05 across four; bar 0.05 on `EPACK@16k`):**

| id | contrast | decision |
|---|---|---|
| R1 | `fixed_tok256_ov0pct` − `fixed_tok2048_ov0pct` | the replication, as revision 2 |
| R2 | `header512` − `fixed_tok512_ov0pct` | contextual headers, as revision 2 |
| R3 | `parent256` − `fixed_tok512` | section expansion on the serving path |
| **R4** | **`nbr1_512` − `fixed_tok512`** | **turn on `context_window = 1` by default** |

`nbr1_256` and `nbr2_512` are descriptive. Each superiority contrast is read
**conjunctively with `ERET@16k`**: an arm "wins" only if it clears the bar on `EPACK` *and*
its `ERET` is not inferior at ε (§3.6); a win on containment bought by pushing documents
out of the budget is reported as exactly that.

### 3.6 Non-inferiority family, revised — amends §8.1, §8.2, P.7

Control is what ships: `fixed_tok512` (512 / 64). Overlap is settled (Phase 0, both legs,
both metrics, powered) and N2 is **dropped**; its α goes to the new size contrast.

| id | contrast (control − candidate) | decision it settles |
|---|---|---|
| **N1** | `fixed_tok512` − `fixed_tok1024_ov0pct` | adopt 1024/0: 2.29× fewer vectors, ~0.16 TB at the ~500k-article target |
| **N3** | `fixed_tok512` − `fixed_tok2048_ov0pct` | adopt 2048/0: ~4.6× fewer vectors |

**Rule.** One-sided non-inferiority at α = 0.025 each (Bonferroni within the family;
constant 2.802 as in §8.5.1), **on both endpoints, conjunctively**: the candidate is
adopted only if the 95 % CI upper bound of (control − candidate) is below ε on `EPACK@16k`
**and** on `ERET@16k`. An intersection-union test needs no further α split. **ε = 0.05
absolute on each endpoint**, unchanged from revision 2 in value and in the grounds stated
at §8.2; nothing measured at Stage 0 licenses widening it, and this revision does not.

**What the coarse arm is expected to lose on, stated in advance.** Stage 0 §5 says the
coarse arms *win* `EPACK` (0.80–0.89 vs 0.18) and *lose* `ERET` (0.045–0.082 vs 0.100 at
4k). At 16k the `ERET` gap narrows for the 1024 arm and stays wide for 2048 (§3.2). The
prediction is therefore that N1 is a genuine contest and N3 fails on `ERET` (§7). If that
is what happens, it is the answer to the owner's question — 1024 is as coarse as the index
can go for this population — and it will have been reached by measurement.

### 3.7 The labeler — amends §6.1, §6.4, §6.6

Stage 0's finding A is a protocol failure, not a model-size failure: an index-primary
protocol asked a non-reasoning model to track sentence numbers across units with dozens of
sentences, and it failed a third of the time. Revision 3:

1. **Quote-primary anchoring becomes the protocol.** The labeler returns verbatim quotes;
   spans are located by exact-then-normalised string match against the indexed text;
   a quote that does not locate is a hallucinated span (the existing gate). This is the
   relocation Stage 0 had to implement anyway; it is now the primary path, not a repair.
2. **A reasoning judge is the second labeler.** `mango:8004` (`Qwen3.6-35B-A3B`, reasoning
   model, 131k context) was provisioned and never used because the first judge failed
   against itself. Both label the development set; agreement between judges is reported
   beside each judge's self-consistency. The primary judge is chosen at freeze **by the
   self-consistency gate, not by preference**, and the choice is recorded here.
3. **Gates unchanged:** self-consistency ≥ 0.90 (span-union Jaccard on 10 % re-presented
   pairs), hallucinated-span rate ≤ 0.05, and — because the denominator is now
   per-document — a new **document-level agreement** gate: whether-any-evidence agreement
   ≥ 0.90 (Stage 0 measured 0.871 under the old protocol; this gate is expected to pass and
   is stated so its passing is a measurement).
4. **Item 8, the two-independent-human-reader validation, remains the gate on the labels
   and is unchanged in design.** The package (100 stratified pairs, blank verdict sheets,
   readsheets) is rebuilt on the *new* labels once they pass the machine gates; the old
   readsheets are superseded. Cost 32–48 person-hours. No agent read substitutes for it.

### 3.8 What does not change

Corpus and assembly (§4). The six index arms (§5.1) and their embeddings. D = 50 and
`rerank_candidates = 50`. The dev/confirmation split, the exposure ledger, and the
quarantine of confirmation retrieval outputs until label freeze. The circularity rule.
The exclusion rules (§8.5.6), now applied per topic on evidence-bearing document count
(< 3 evidence-bearing documents excludes the topic) rather than unit count. The
sensitivity arms (§10). The multiple-comparison ledger. The house conventions: failures
reported, `UNRESOLVED` never called null, δ80 beside every descriptive row, cost columns
beside every quality column.

---

## 4. Cost

| item | cost | note |
|---|---|---|
| New embeddings for Stage 0b' | **0** | existing indexes, realised-budget matching (§3.3) |
| BM25 indexes, 6 arms | CPU minutes | in-process |
| Three-mode scoring + rerank, 8 arms × 3 modes × 2 variants, dev topics | ~1 GPU-hour on `:50052` | pools are small; rerank is the only GPU step |
| Relabel the dev set, two judges, quote-primary | ~2× Stage 0's labeling cost on `mango` | ~450 pairs + 10 % re-presentation each |
| Two-reader human validation (item 8) | **32–48 person-hours** | the blocking item; unchanged |
| Confirmation run re-embed in the generator's tokenizer (§3.3, optional) | ~1.93 fleet-hours | only if Stage 0b' passes |

GPUs 6 and 7 stay reserved and untouched. No store client is constructed anywhere in the
offline harness; Stage 2 uses the dev tenant only.

---

## 5. Order of operations

1. **Freeze revision 3.** This document's hash goes in the manifest beside revision 2's.
2. **Relabel the development set** under §3.7 with both judges. Read the three machine
   gates. If neither judge passes self-consistency ≥ 0.90, **stop** — the labeler is wrong,
   not unstable, and the protocol needs redesign, not another run.
3. **Rebuild the item-8 package** on the passing judge's labels; **schedule the two-reader
   read**. It runs in parallel with step 4 and gates step 6.
4. **Stage 0b'** — re-run the calibration on the split endpoints at B = 16,384 (and 4,096,
   32,768), three modes, all arms including `nbr1_512`, `nbr1_256`, `nbr2_512`, on the
   existing indexes at matched realised budget. Produce the §8.5.7 table for each of `ERET`
   and `EPACK` per contrast, plus the mode × size table.
5. **Read the gate.** Both endpoints must sit in [0.15, 0.90] on the dev topics; σ_d per
   contrast per endpoint against the requirement at n_retained; power at Δ ∈ {0, 0.01,
   0.02}. Three outcomes as in P.7. **ε does not move.**
6. **Confirmation run** on the 80 topics, only if step 5 passes and step 3's κ is in hand.
   Re-embed in the generator's tokenizer (§3.3) if affordable; otherwise matched realised
   budgets, published.
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

House convention: written before Stage 0b' runs; scored in the results; failures reported.

| # | prediction | basis | confidence |
|---|---|---|---|
| P1 | Quote-primary anchoring lifts self-consistency above 0.90 for at least one judge | the failure mode was index tracking, not evidence recognition (whether-agreement 0.871) | ~60 % |
| P2 | `EPACK@16k` sits in [0.15, 0.90] for every arm | Stage 0 measured 0.174–0.889 at 4k; 16k admits more text per document | ~90 % |
| P3 | `ERET@16k` clears 0.15 for every arm | pool reach is 0.28–0.48; at 16k the fine arms pack their whole pool | ~75 %; the 2048 arm is the risk |
| P4 | **N1 (1024) is non-inferior on `EPACK` and a genuine contest on `ERET`** | Stage 0 §5 factor table | ~50 % on the conjunctive rule |
| P5 | **N3 (2048) fails non-inferiority on `ERET@16k`** | ~8 chunks fit; reach at 4k was 0.045 vs 0.100 | ~75 % |
| P6 | **R4 (`nbr1_512`) clears the 0.05 superiority bar on `EPACK`** | `parent256` lifted containment 0.18 → 0.43 by supplying surrounding text; ±1 chunk is a cheaper version of the same move | ~65 % |
| P7 | R4's `ERET` is *not* inferior at 16k | neighbours cost ~2× budget per source; at 16k the shipping arm's pool still largely fits | ~60 % |
| P8 | The mode × size table shows BM25 favouring coarser chunks than dense does | BM25 rewards term coverage, which grows with chunk length | ~55 % |
| P9 | Rerank-off reverses at least one contrast's sign relative to rerank-on | Phase 0: dense and reranked orderings correlate only +0.55 | ~70 % |

---

## 8. Cross-references this revision closes

* `long-doc-judged-set.md` §14 item 8 and §14.5's gate — **closed** by §1 (population
  declared). Pruning on the size axis is now permitted *by the confirmation run's
  contrasts*, not by either Phase-0 leg's direction, which remain construction-biased.
* `HANDOFF-2026-09-06.md` §5 "what has to change before it runs again" items 1–4 —
  answered by §3.1, §3.7, §3.3, §3.6 respectively.
* `HANDOFF-2026-09-06.md` §14 items 4 and 5 — this document.
* Stage 0 §6 "what would have to change" items 1–4 — same mapping.
