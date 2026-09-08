# Synthesis stage — measuring the answer, not only the evidence

*2026-09-07. Status: `PROPOSED`. Companion to [`SPEC-confirmation-run-r3.md`](SPEC-confirmation-run-r3.md).
The chunking study measures whether the right evidence reaches the generator's context. This
stage measures what the generator then does with it — the quality the user actually receives —
and what it costs. Nothing here changes revision 3's endpoints or contrasts; it adds a stage
that consumes their packed contexts.*

## 1. Why, and why now

The decision ledger in the study report shows two empty columns: answer quality and generation
cost. Every configuration decision so far is justified by evidence delivery, on the assumption
that delivered evidence becomes a correct, cited answer. That assumption is testable now,
cheaply, because the **pointed-question population** (r3 §11, #506) carries a **gold answer by
construction**: each query was written from a known sentence, and the sentence is stored with
the query as a located span. On that population "did the system answer correctly" is a
measurement, not a judgment.

## 2. What is measured, per (query, configuration)

The configuration is an index arm × delivery arm × retrieval mode × budget from revision 3;
the packed context is exactly what §7.3's rule admits. The generator receives the query and
that context under a fixed prompt (§5) and must answer with citations to the supplied chunks,
or abstain.

| endpoint | definition | scored how |
|---|---|---|
| **Correct** | the answer states the fact the gold sentence states | primary: an LLM judge (§6) comparing answer to gold sentence, calibrated against a human-scored subset; secondary: normalised string containment of the gold's key entity/number |
| **Faithful** | every factual claim in the answer is supported by a cited chunk that was actually in the context | judge over (claim, cited chunk) pairs; unsupported-claim rate |
| **Cited precisely** | the cited chunk contains the gold sentence (or a sentence with pooled support ≥ 0.5 on CDS) | deterministic from spans |
| **Abstains correctly** | when no admitted chunk contains the gold, the answer says so rather than answering | deterministic given the context; the *false answer rate under no evidence* is the safety number |
| **Cost** | generator input tokens, output tokens, wall latency p50/p95, per call | from the serving log; plus retrieval latency p50/p95 per configuration, timed on the served path |

Reported per configuration with topic-level (pointed: document-level) cluster bootstrap CIs,
δ80 beside every descriptive row, and the same power-first reading as revision 3.

## 3. Populations

* **Primary: the pointed set** (§11) — gold answer by construction; correctness is exact-ish.
* **Secondary: the CDS development topics** — no single gold answer exists (the need is a
  decision, not a fact), so only *faithful*, *cited precisely* (against pooled support ≥ 0.5)
  and *abstains* are scored, plus a judge-rated *usefulness for the stated need* labeled
  `DESCRIPTIVE`.

## 4. Arms and contrasts

All arms are revision 3's; no new embeddings. The synthesis stage adds one axis:

| axis | values | why |
|---|---|---|
| generator | `mango:8003` Llama-4-Scout (production), `mango:8000` Qwen3.6-27B, `mango:8004` Qwen3.6-35B-A3B (reasoning) | the production model plus one larger and one reasoning alternative — the *generator × chunking* interaction is the thing nobody has measured |
| prompt | one fixed answer-with-citations prompt (§5) | prompt engineering is not this study |

**Pre-registered contrasts, on the pointed set, `Correct` as primary:**

| id | contrast | question |
|---|---|---|
| S1 | `fixed_tok512` (shipping) − `fixed_tok1024_ov0pct` at 16k, hybrid + rerank | does the storage lever cost correct answers? (the N1 decision seen from the answer) |
| S2 | `nbr1_512` − `fixed_tok512` | does neighbour delivery turn into better answers, not only better containment? |
| S3 | `fixed_tok256_ov0pct` − `fixed_tok2048_ov0pct` | the size replication, at the answer |

Non-inferiority for S1 (ε = 0.05 on `Correct`, one-sided α = 0.025); superiority for S2, S3
(Holm α = 0.05, bar 0.05). **Conjunctive with `Faithful`:** an arm that answers more
correctly by hallucinating more is not adopted — unsupported-claim rate must be non-inferior.

**Descriptive families:** generator × arm; retrieval mode × arm; abstention rate under
"no gold delivered"; cost per correct answer (tokens and seconds per configuration divided by
`Correct`), which is the number a product owner actually wants.

## 5. The generation prompt (frozen at Stage 0, hashed)

System: *You answer a research question using only the passages provided. Cite each claim
with the passage id in brackets. If the passages do not contain the answer, say "The provided
passages do not answer this question" and stop.* User: the question, then the admitted chunks
each prefixed `[chunk_id] `. Temperature 0. Max tokens 512. No tools. The prompt goes into
the report's prompt section like every other.

## 6. The judge, and how it is kept honest

* An LLM judge scores `Correct` and `Faithful` (judge = a model **not** in the generator set —
  `claude-sonnet-5` via the CLI under the isolation flags, or the Argo gateway's GPT when
  reachable; recorded).
* **Calibration:** 100 (answer, gold) pairs from the development slice scored by two humans,
  through the Grading view with a `citation-feedback`-style task; κ(judge–human) ≥ 0.60
  required for the judge's numbers to be quoted at full strength, else `MODERATE`. This
  reuses the R-dev machinery exactly.
* Deterministic checks (`Cited precisely`, `Abstains`) need no judge and are the backbone.

## 7. Cost and order

* Contexts: already produced by Stage 0b′ packing for every arm; zero retrieval cost.
* Generation: 177 pointed queries × 8 arms × 3 modes × 3 generators ≈ 12.7k calls on the
  fleet; at ~5 s each with 4 in flight ≈ 4.5 h. Judge: ≈ 12.7k Sonnet calls ≈ $0.02 each
  ≈ $250, or free on the gateway.
* Order: (1) freeze this spec; (2) generate on the 10 dev topics' share and the pointed set;
  (3) judge; (4) human calibration of 100 pairs in the Grading view; (5) read the gate
  (κ ≥ 0.60; endpoints in a resolvable window); (6) contrasts. Runs after Stage 0b′, on its
  packed contexts; does not touch confirmation topics.

## 8. What this stage cannot establish

Whether a *different* generator prompt, a different embedder, or a different reranker changes
the answer — one prompt, one embedder, one reranker. The ledger marks those as open axes; this
stage closes the answer-quality and generation-cost columns for the configurations the study
already compares.
