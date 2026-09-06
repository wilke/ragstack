# Measuring sufficiency: an operational definition, a judge protocol, and a pilot

*Design only. Written 2026-09-05 on `coconut`, repo `/home/wilke/Development/ragstack` at
`d225cea`. No experiment was run. The only network calls made were **17 `GET /v1/models`
probes across two port scans and two 2-token chat probes** against `mango.cels.anl.gov`
(§1.1) — no GPU on this host was used, no store was read or written, GPUs 6 and 7 were not
touched.*

*Inputs: `/home/wilke/Development/ragstack/docs/plans/chunking-evaluation.md`,
`/home/wilke/Development/ragstack/docs/plans/long-doc-judged-set.md`,
`/home/wilke/Development/ragstack/docs/g1-retrieval-protocol.md` §4.4/§6.3,
`/home/wilke/Development/ragstack/docs/g1-sop-rating.md` §6, and the seven Phase-0
`RESULTS-*.md` under
`/tmp/claude-3581/-home-wilke-Development-ragstack/d2d3e28e-62e9-452e-8a2b-cf27464f9e80/scratchpad/phase0/`.*

---

> ## Read this first
>
> **1. The measurement the study is missing is not "answer quality". It is *nugget support at
> a fixed context budget*.** Everything downstream of retrieval — generation, citation,
> phrasing — adds variance without adding information about chunking. The construct that
> *is* about chunking is: **for a fixed number of tokens delivered to the reader, how much of
> the evidence needed to answer the question does this chunking actually deliver?** That is
> measurable, paired, and immune to the incomplete-qrels problem that damages every retrieval
> metric in this study.
>
> **2. Equal *context token budget*, not equal `top_k`, is the load-bearing design decision.**
> Sufficiency@10-chunks is monotone in chunk size by construction: a `tok2048` arm gets 8×
> the text of a `tok256` arm. Measured that way the metric re-measures the treatment and
> confirms whichever direction the arm sizes point. Every number below is defined at a fixed
> delivered-token budget, and the budget is swept as a curve.
>
> **3. HyDE is not a sufficiency measure and should not be put under the grid.** It is a
> retrieval technique that sits *upstream* of the treatment, is plausibly *interacted* with
> chunk size (a hypothetical-answer vector is passage-shaped), and imports an LLM's priors
> into the query side of a corpus that LLM has likely memorised. It has two legitimate,
> narrow roles (§3). The valid form of the intuition behind it is its **inversion**: generate
> reference content from the *gold* document and use it to **score**, not to retrieve. That
> inversion is the nugget list.
>
> **4. The brief's "only one LLM endpoint is available" is false, and I verified it.** Mango
> serves **three** models on three ports: `Qwen/Qwen3.6-35B-A3B` on :8004 (the generator),
> `Qwen/Qwen3.6-27B` on :8000 (**same family — does not count**), and
> **`RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic` on :8003**, which answered a live
> probe. A genuine cross-family second judge exists, and it is *non-reasoning* — 2 completion
> tokens where Qwen spends 176–224 on the same two-word answer — so it is the cheap judge, not
> the expensive one. The design does not *depend* on it (§4.2).
>
> **5. The honest prior is that this comes back null, and the design is built for that.**
> Stage 1 on both legs found **every** chunk-size contrast unresolved behind the reranker,
> with the dense→reranked shrinkage itself a resolved effect (Leg B −0.0618, CI [−0.090,
> −0.034]). Sufficiency is measured on the reranked top-k inside a fixed budget — the exact
> arm where the contrasts already vanish. A null there is a *useful* result (ship the cheapest
> config) **but only if the instrument is proven able to detect a difference it should**, because
> non-differential judge noise manufactures equivalence for free (g1 SOP §6.2). Hence the
> controls in §6.3 are gates, not garnish.
>
> **6. Two designed exits, stated in advance.** (a) *Instrument valid, configs equivalent at
> equal budget* → the size decision falls to storage, which is worth ~0.1 TB on the OA target,
> and the study says so. (b) *Judge fails its validity gates, or per-query sufficiency
> correlates with per-query nDCG@10 at Kendall τ > 0.9* → **the metric adds nothing over what
> the study already measures**, the LLM tier is abandoned, and deterministic containment (§2,
> S1) plus a human tier is the whole sufficiency instrument. Exit (b) is a real possibility and
> is cheaper to discover than to ignore.

---

## 1. What is already measured, and what the brief got wrong

### 1.1 Verified discrepancies with the task framing

House style from `long-doc-judged-set.md` §11: state them rather than quietly working around
them.

| # | brief says | measured | consequence |
|---|---|---|---|
| 1 | "only one LLM endpoint is available on this infrastructure" | **Three.** :8004 `Qwen/Qwen3.6-35B-A3B` (131,072); :8000 `Qwen/Qwen3.6-27B` (262,144); :8003 `RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic` (60,000). :8001/:8002/:8005–:8012 closed. | A **cross-family** second judge exists. §4 uses it. |
| 2 | Scout / `mango-scout` "is stale; use the served id" | Half right. It is stale **for :8004**, which serves Qwen. Scout itself is **live on :8003** and answered `{"content":"ok"}`, `finish_reason:"stop"`, 2 completion tokens. | The registry's model id is right and its **port** is wrong. Assert the served id per endpoint, as `mango.py` already does. |
| 3 | cross-encoder "~658 pairs/s" | **Size-dependent: 1,037 / 786 / 391 pairs/s** at 256- / 512- / 2048-token chunks (`PREREG-stage1-legB.md`). The §7a oracle achieved **116.5 pairs/s** because its units are whole sections that must be windowed. | A flat 658 under-costs 2048-chunk work by 1.7× and over-costs 256 by 1.6×. Cost with the curve. |
| 4 | position oracle "already built and run on 90 topics" | **True, and better than the brief implies.** 2,161 (topic, doc) pairs, 90 topics, 2,095 documents, 21,735 window-pairs in 187 s. Offsets verified byte-identical to the text stage 1 indexed. | The gold-evidence spans for Leg A **already exist on disk** (`pilots/oracle_results.jsonl`). |
| 5 | "Leg B 260 accepted known-item queries" | True — and **Leg B's evidence position is known by construction**, not by oracle: 260/260 source sections start past token 1,024, median start 5,098. | Leg B needs **no oracle and no LLM** to score containment. That is why it becomes the circularity control, not the workhorse (§4.3). |
| 6 | LLM is a reasoning model, budget generously | Confirmed and **switchable**: `chat_template_kwargs={"enable_thinking": false}` took a paraphrase from 16.3 s / 3,085 tokens to 0.4 s / 67 tokens. Scout on :8003 has no reasoning tax at all. | Thinking on/off is a **pre-registered calibration**, not an assumption (§6.5). |

### 1.2 The measurements this design stands on

| fact | value | source |
|---|---|---|
| σ_d, nDCG@10, Leg A/CDS, 276 config pairs | **0.156** dense / **0.173** reranked | `RESULTS-legBC-pilots.md` §4 |
| σ_d, nDCG@10, Leg B's own 260 queries | **0.119** (×0 rung) / **0.152** (×11.5) | `RESULTS-legB-rerun.md` §6.1 |
| every chunk-size contrast behind the reranker | **unresolved on both legs**; shrinkage −0.0618 [−0.090, −0.034] | `RESULTS-stage1-legB.md` §1 |
| overlap | **uncontested powered null** on both legs, both metrics | `RESULTS-stage1-legB.md` §11 |
| CE truncation | hard cut at **4,096 tokens/pair**; real position effect below it (0.8589 front vs 0.8032 back at 2,048) | `chunking-evaluation.md` |
| Leg A evidence depth | 55.4% of argmaxes start past token 1,024; 11.9% in abstract+intro; median margin over best head unit **+0.163** | `RESULTS-legBC-pilots.md` §1.2 |
| Leg A grade-0 hard negatives | 300 seeded per topic already in the pilot corpus; 95% fetchable | `RESULTS-step1-cds-gate.md` |
| Leg C volume | 6,024 usable (citance, cited-doc) pairs from **1,200** citing articles; ~7M corpus-wide; 57.2% survive the position filter; cited docs median **9,330** tokens | `RESULTS-legBC-pilots.md` §3 |
| Leg C incompleteness | **62%** of citances co-cite ≥2 references | ibid. §3.4 |
| Leg B ceiling | nDCG@10 **0.92–0.99** on every config at every rung; 94–98% gold at rank 1 | `RESULTS-legB-rerun.md` §6.1 |
| embedding fleet | 164k tok/s model; **171k** and **175k** achieved; ±2× band retained | stage-1 Leg A / Leg B |
| reusable artefacts | step-3 `.npy` chunk embeddings for `tok256/32`, `tok512/64`, `tok2048/256`, `whole4096` over the 4,053-doc Leg A pilot corpus; all 24 stage-1 chunk `.jsonl` files | `phase0/step3/`, `phase0/stage1/` |

---

## 2. Question 1 — sufficiency, operationally

### 2.0 The construct, stated before the instruments

> **Sufficiency** is a property of *(query, assembled context)*, not of a ranking:
> **the fraction of the evidence required to answer the query that is present in the text
> delivered to the reader, at a fixed delivered-token budget.**

Three things this definition buys, each of which a document-level retrieval metric cannot:

1. **It is scored on text, not on doc-ids.** Therefore it is **immune to incomplete qrels** —
   the defect that damages every leg of this study. Leg C's 62% co-citation rate, Leg A's
   ~1,260-judged-of-many-more pooling, Leg B's single-gold-document known-item construction:
   all three manufacture false negatives in `recall@k`, and *none* of them touches sufficiency,
   because a passage from an unjudged document that supports the needed evidence counts as
   support. This is the single strongest argument for building the metric at all, and it should
   be headlined in the study report.
2. **It is defined at a budget, so it is a product statement.** "For 8k tokens of prompt, this
   chunking delivers 0.62 of the needed evidence and that one delivers 0.71" is directly
   actionable in a way that "nDCG@10 0.60 vs 0.63" is not.
3. **It separates two failures that max-rollup document ranking conflates**: *the right document
   was not retrieved* and *the right document was retrieved and the answering passage was cut in
   half*. The second is precisely what boundary-aware chunking claims to fix and precisely what
   the study currently cannot see.

### 2.1 The four candidate instruments

**S1 — Evidence containment (deterministic, no LLM).**
Gold evidence span = the §7a oracle's argmax structural section (Legs A and C) or the
generating section (Leg B, known by construction). Score = fraction of gold-span tokens present
in the assembled context, in the same token-offset coordinate system stage 1 already verified
byte-identical. Graded in [0,1]; a binary form thresholds at 0.5.
*Cost:* zero LLM. One oracle pass per (query, gold doc) — **already run for Leg A's 2,161
pairs** and needed only for new Leg C pairs (~20 window-pairs each, ≈0.2 s).

**S2 — Holistic LLM sufficiency rating.**
Judge sees query + context, returns 0–3 ("could a comprehensive answer be written from this
text alone?").
*Cost:* 1 LLM call per (query, arm).

**S3 — Answer equivalence against a full-document reference.**
Generate an answer per arm from its context; generate a reference answer once from the gold
document; judge coverage/equivalence.
*Cost:* 1 call/query + 2 calls/(query, arm), plus generator variance stacked on judge variance.

**S3′ — Nugget support rate (RECOMMENDED PRIMARY).**
Extract, **once per query and independently of every arm**, an atomic nugget list from the gold
evidence (6–12 claims, each one sentence, each independently checkable). The extraction prompt
**requires document-specific claims — measured values, named entities, specific findings — and
forbids background statements**: Leg B's own failure taxonomy shows why (`legb2_285`, background
epidemiology that "hundreds of corpus documents state"). A background nugget is genuinely
supported by a fluent topical grade-0 document, which would blunt the negative control (§6.2)
into meaninglessness. Then, per arm, ask the judge for each nugget: *is this supported by the
presented context?* — with a verbatim quote. Sufficiency = supported / total.
*Cost:* 1 call/query (amortised over all arms) + 1 call/(query, arm).

### 2.2 Cheapest, most valid, and the recommendation

| | S1 containment | S2 holistic | S3′ nuggets | S3 answer-equivalence |
|---|---|---|---|---|
| LLM calls per query at 5 arms | **0** | 5 | **6** | 11 |
| per-query metric | graded [0,1] | ordinal 0–3 | **proportion over m≈8** | graded, judge-scored |
| variance behaviour | low, deterministic | worst (coarse ordinal + judge noise on one draw) | **best of the judged three** — averaging m nuggets divides the per-item judge noise by m | worst overall (generator + judge) |
| validity for "can we answer it" | proxy — assumes the answer lives in one section | plausible but the least controlled | **high, and decomposable** (you see *which* evidence went missing) | **highest**, and confounded with generation |
| verbosity bias | none | severe | mitigated but present | severe |
| immune to incomplete qrels | **no** (provenance-bound) | yes | **yes** | yes |
| circularity risk | none | high on any LLM-authored leg | contained (nugget list is arm-independent) | high |

**Recommendation, in one line:** **S3′ is the primary metric, S1 rides along for free as a
companion diagnostic on every query, S2 is run only as a calibration ablation on the pilot, and
S3 is a tertiary non-inferiority confirmation on the top two configs only** — the last is exactly
the position `docs/g1-retrieval-protocol.md` §6.3 already assigns to answer-level metrics
("never a selection metric, never swept"), and there is no reason for this study to relitigate
it.

S1 is the *cheapest by a factor of infinity* and is the honest floor of the whole exercise: if
the judged tiers fail their gates (§6.3), S1 plus a human read is what survives, and it is still
a real sufficiency measurement — just one with a provenance assumption baked in.

S3′ is the *most valid instrument that a paired config comparison can afford*. S3 is more valid
as a statement about the product and is the wrong thing to select on.

### 2.3 How they disagree — the part that decides the design

| situation | S1 | S3′ | S2 | reading |
|---|---|---|---|---|
| the chunk boundary cuts the evidence section, and the retrieved half carries 4 of 8 nuggets | **pass** (containment 0.5) | **0.5 — fails** | probably pass | **The boundary-damage case: the whole point of chunking, and S1 is blind to it.** This case alone justifies the LLM tier. |
| evidence is restated in a *different* retrieved document (a review) | **fail** | **pass** | pass | S1 carries a provenance assumption the product does not. S3′ is right here; S1's disagreement rate with S3′ is itself the measurement of how often the corpus is redundant. |
| the gold section is retrieved but sits at token 7,000 of an 8,000-token context | pass | may fail | may fail | Lost-in-the-middle **inside the judge's own prompt**. The CE has a measured position effect (0.8589 front / 0.8032 back at 2,048 tokens); assuming judges do not is unwarranted. → position probe, §6.3(c). |
| context is fluent, topical, and answers nothing (a grade-0 CDS document) | fail | **must fail** | **often passes** | S2's characteristic failure: topical vibes read as sufficiency. This is why the negative control is a grade-0 document and not an empty context. |
| the query is abstract-answerable (a Leg B *reject*) | pass trivially | pass trivially | pass trivially | Every instrument saturates. The query set must be deep-evidence **by construction**, which is exactly what Legs A/B/C were built to be. |
| coarse arm returns 4 chunks of 2,048; fine arm returns 32 of 256 — **at 10 chunks each** | fine loses | fine loses | fine loses | The confound. **Removed by the equal-budget rule, not by the metric.** |

### 2.4 The equal-budget rule, pinned

Assemble the context per arm by walking the **reranked** ranking and appending whole chunks
until the next chunk would exceed the budget **B**; never truncate a chunk mid-way; record:

- `realised_tokens` (≤ B) and `wasted_tokens` (B − realised). A `tok2048` arm can waste up to
  2,047 tokens of a fixed prompt. **That is a genuine product cost of coarse chunking and it must
  be reported, not smoothed away** — it is the first place in this study where chunk size has a
  cost that document-ranking metrics structurally cannot see.
- `duplicate_tokens` — text appearing twice because of overlap. Overlap is already the study's one
  uncontested (null) axis; under a budget it acquires a *positive cost* for the first time.
- `n_chunks`, `n_unique_docs`, and the rank position of the gold span inside the assembled
  context (feeds the position probe).

Budgets: **B ∈ {4,096, 8,192, 16,384}**. 8,192 is primary — it is the shipping
`llm_max_context_chars = 8000` order of magnitude, and it is under Scout's 60,000 window with
room for nuggets and instructions. The curve across B is a deliverable in its own right: *the
sufficiency-vs-budget curve is the honest form of the chunk-size question.*

---

## 3. Question 2 — HyDE

**Asked plainly: does HyDE serve as a sufficiency measure? No. It is a retriever, and putting it
under the grid changes what is being measured.** Four specific objections, in descending order
of how much they matter:

1. **It is upstream of the treatment, so it cannot measure the treatment.** Sufficiency is a
   property of an assembled context. HyDE changes *which* context gets assembled. Substituting a
   better retriever for a measurement is the same category error as reading `mean rerank score`
   as a quality metric — a mistake this plan already made once and corrected in
   `chunking-evaluation.md` ("mean score is a diagnostic; reranked recall/MRR are the quality
   measures").
2. **It plausibly interacts with chunk size, which doubles the design space rather than
   informing it.** A HyDE vector encodes a *passage-shaped* pseudo-document, so its similarity
   profile against 256- vs 2,048-token chunks is not a constant offset. Running the grid under
   HyDE therefore yields configs × {HyDE on, off} — 48 cells where there were 24 — and any
   difference is uninterpretable without the interaction term, which n cannot support.
3. **It imports LLM priors into the query side of a corpus the LLM has probably memorised.** The
   CDS topics are public TREC data from 2014–16; PMC OA full text is in every open pretraining
   mix. A hypothetical answer for CDS topic 2015_18 may contain the actual findings of the actual
   judged articles. That is not query expansion, it is leakage, and it would flatter whichever
   config best matches regurgitated abstract text — i.e. **the coarse ones**, in the same
   direction as Leg A's already-measured aboutness bias. On Leg B it is worse still: the queries
   were written by Qwen from the source section, so a Qwen-generated hypothetical answer is
   partially a reconstruction of the gold passage.
4. **There is no headroom to see it on the leg with the power.** Leg B sits at nDCG@10
   0.92–0.99 with 94–98% of golds at rank 1. A retrieval improvement has nowhere to go.

**Where HyDE does have a role — two places, both narrow, neither a measurement of sufficiency:**

- **(a) A robustness condition on the short-list, exactly like rerank-on/off.** After stage 2
  selects, run the top two configs with and without HyDE and check that the *ranking between
  them* does not invert. This is a legitimate question ("does a query-side technique we might
  ship overturn the chunking choice?") and it costs 2 configs × n queries, not 24 × n. It must be
  reported as a condition, never averaged into the headline.
- **(b) An attribution diagnostic for *why* sufficiency is low.** Where sufficiency falls short,
  the gap between retrieval-with-the-query and retrieval-with-a-hypothetical-answer bounds how
  much of the shortfall is *query-document vocabulary mismatch* versus *chunk boundaries*. If HyDE
  recovers the missing nuggets, the fix is query-side and no chunker will help; if it does not,
  the evidence genuinely is not reachable at that budget. That is useful, and it is a statement
  about causes, not a sufficiency score.

**And the part worth saying out loud, because the intuition behind the suggestion is good:**
HyDE's insight is *"a hypothetical answer is closer to the answering passage than the question
is."* That insight is correct and it belongs on the **scoring** side, not the retrieval side.
Invert it:

> Generate the reference content **from the gold document** — where it is not hypothetical, it is
> the real answer — and use it to **score** what retrieval delivered.

That is the nugget list. Same generator, same insight, opposite direction: HyDE injects a
synthetic document into the query (a confound); the gold-derived nugget list injects the true
answer into the measurement (an instrument). **This is the design's answer to the suggestion:
adopted in inverted form, rejected in its original form.**

---

## 4. Question 3 — judges, models, and protocol

### 4.1 Tiering

| tier | who | what it judges | volume in the pilot | cost |
|---|---|---|---|---|
| **T0** | deterministic code | S1 containment; quote-substring verification; budget accounting | every item, free | 0 |
| **T1** | **Qwen3.6-35B-A3B** (:8004) | S3′ nugget support — the primary metric | every (query, arm) | §6.4 |
| **T2** | **Llama-4-Scout-17B-16E** (:8003) — *different family* | the same items, on a stratified subsample | ~200 items + all 100 Leg B items | cheap (no reasoning tax) |
| **T3** | human (study lead) | 50 stratified items, oversampling T1/T2 disagreements | ~2.5–3 h | the binding cost |

**Why not a fourth judge.** `Qwen/Qwen3.6-27B` on :8000 is available and is **not** independent —
same family, same tokenizer lineage, same post-training pedigree as the T1 model and as the
generator that wrote Leg B. Adding it inflates apparent agreement without adding evidence. It has
one legitimate use: a **within-family stability check** (does the metric survive a model swap
inside the family?), reported separately and never pooled into κ.

### 4.2 What the one-endpoint world would have cost, and why the design survives it

The endpoints live on someone else's host, and this project's model registry has already gone
stale once (`chunking-evaluation.md` names Scout; :8004 serves Qwen). So the design is written to
degrade gracefully:

- **With Scout (the actual situation):** cross-family κ is measurable at scale, and the
  same-family circularity on Leg B gets a *number* (§4.3).
- **Without it:** T2 falls back to **Claude subagents**, for which there is direct precedent —
  round 1 of the Leg B pilot ran all three of its LLM stages that way with isolated contexts and
  hashed prompts. That tier cannot scale to thousands of items, but κ needs ~150–200, which it can
  do. T3 and T0 are unaffected.
- **Without any second judge:** the metric is capped at the g1 **MODERATE** band's rules
  (screening only; `EQUIVALENT` downgraded to `INCONCLUSIVE`) and the study says so.

**A second model is worth having but is not load-bearing.** The load-bearing defence against
circularity is *which leg gets judged*, not *which model judges* — see next.

### 4.3 Circularity: the plan's §7 rule needs an amendment, and this design supplies it

The plan's rule ("the oracle is never used to tune a chunker; selection on evidence position uses
structural units only") stops a **chunker** grading its own homework. The Leg B re-run already
flagged the gap: *"it has no clause for a query construction doing the same."* Sufficiency judging
opens a third hole — a **judge** grading text selected by a query its own family wrote.

**Proposed amendment, pre-registered:**

> **No LLM may serve as the primary sufficiency judge for a leg whose queries or verifier
> decisions were authored by the same model family. Where the constraint binds, that leg is
> scored by the deterministic instrument (S1) and by a cross-family or human tier only, and the
> same-family judged score is reported beside them as a measured circularity estimate rather
> than suppressed.**

**The clause deliberately does *not* cover the gold-evidence nugget list, and the asymmetry is
the point.** Query authorship acts on the **treatment side** — it shapes which text gets
retrieved, differently for different chunkers, so a same-family judge can reward the retrieval
its own family's query steered toward. Nugget extraction acts on the **measurement side** and is
**arm-independent by construction** (§4.5(4)): one list, generated before any arm is assembled,
reused verbatim across all five. A Qwen-flavoured nugget list therefore biases every arm
identically — it can move the *absolute* sufficiency level, which is exactly what the A4/A5
controls and the human tier exist to check, and it **cancels in the paired contrast**, which is
the quantity every decision rests on. Extraction stays on the stronger model (nugget quality is
the most load-bearing artefact in the design); the alternative — Scout extracts, Qwen judges — is
recorded as the fallback if the human tier finds the nugget lists themselves defective.

Applied to the three legs, this falls out cleanly:

| leg | queries authored by | primary sufficiency instrument | why |
|---|---|---|---|
| **A — CDS, 90 topics** | **humans** (NIST topic authors) | **S3′, judged by Qwen (T1) + Scout (T2)** | No LLM in the query construction. The validity leg. |
| **C — citances** | **humans** (paper authors) | **S3′, judged by Qwen + Scout** | Also LLM-free, and the only leg with unlimited volume (§5). The power leg. |
| **B — LLM-generated** | **Qwen3.6-35B-A3B** | **S1 containment** (position known by construction; zero LLM) | The generator wrote the query *from* the gold section. A Qwen nugget list from that same section, judged by Qwen, is a closed loop. |

Leg B keeps a **20-query judged arm anyway** — scored by *both* Qwen and Scout — for a purpose
nothing in this project has ever measured: the **difference-in-differences estimate of same-family
inflation**,

```
inflation = (S3′_Qwen − S3′_Scout)|Leg B  −  (S3′_Qwen − S3′_Scout)|Legs A,C
```

If that is ≈0, the §7 amendment is conservative and can be relaxed with evidence. If it is large,
the amendment is justified with a number instead of an assertion. Either outcome is worth the
~200 judge calls it costs (20 queries × 5 arms × 2 judges), budgeted in §6.4.

### 4.4 Is the cross-encoder a valid cheap judge? **No — with one exception.**

Tempting: `bge-reranker-v2-m3` is 1,037 pairs/s and already deployed. It fails three ways:

1. **Self-grading.** The CE *produces* the ranking that assembles the context. A CE-judged
   sufficiency score would reward whichever config the CE already ranked well, by construction.
   This is the same defect as the chunker grading its own cuts, one stage later.
2. **Wrong construct.** It scores query-passage *relevance*. "Comprehensive" is not in its output
   space. The study already has direct evidence its score is not a quality scale: mean score fell
   8.33 → 7.11 between configs whose ranking was identical to 0.004.
3. **Truncation and position.** 4,096 tokens per pair, with a measured within-window position
   effect (0.8589 front / 0.8032 back at 2,048). An 8,192-token assembled context does not fit,
   and a windowed max-pool over it re-introduces the position artefact into the metric.

**The exception, and it is real:** the CE is the right instrument for *locating gold evidence*
(that is §7a, already run and already validated by Leg B's construction cross-check — 66.9% exact
section hit against a 14.8% chance rate) and it is worth running as a **cheap covariate** —
CE(query, assembled context) max-over-windows — for one specific purpose: to test whether it
tracks S3′. Report Kendall τ. If τ were high, sufficiency would be obtainable at 5,000× less cost
and this whole design would be over-engineered. I predict it will not be, because the CE cannot
represent "comprehensive", but the test is nearly free and the upside is large.

### 4.5 Protocol rules (each one exists because something broke)

1. **Blind the arm.** The judge never sees the config label, chunk count, chunk sizes, or the
   ranking. One assembled context, neutral separators, one call.
2. **Never side-by-side.** One (query, arm) per call, as g1 §4.4 already requires for its
   relevance judge (*"judge one (query, doc) pair at a time, never a ranked list"*). Pairwise
   preference judging would import position bias directly onto the treatment axis.
3. **Randomise chunk order within the context**, with the rank-ordered variant kept as a
   sensitivity arm. Sufficiency is about content presence; presentation order is the judge's
   position bias, not the chunking's property.
4. **Fixed nugget list, fixed order, arm-independent.** The list is generated once from the gold
   evidence *before* any arm is assembled, hashed into the manifest, and reused verbatim. This is
   what removes between-query variance from the paired difference (§5.2) and it is the single
   biggest power lever in the design.
5. **Quote or it did not happen.** Each "supported" verdict must carry a verbatim span; T0 checks
   it is a substring of the presented context (whitespace-normalised). A failed check downgrades
   the nugget to unsupported and increments a reported **hallucinated-support rate**. Free, and it
   converts a validity threat into a measured quantity.
6. **Self-consistency ≥ 0.95** on a 10% duplicate set with a different item order and seed
   (g1 SOP §6.3(3)). Note that temperature 0 does **not** guarantee determinism under vLLM
   continuous batching — measure it, do not assume it.
7. **κ bands and the human ceiling, imported unchanged** from `docs/g1-sop-rating.md` §6:
   ≥0.80 STRONG · 0.60–0.79 SUBSTANTIAL · 0.40–0.59 MODERATE (screening only; EQUIVALENT →
   INCONCLUSIVE) · <0.40 FAIL. Plus: κ(human–human) < 0.40 ⇒ **RUBRIC_FAILURE** and the judge is
   not interpreted at all; κ(human–human) ∈ [0.40, 0.60) caps the judge at MODERATE; the band is
   read off the **CI lower bound** when the interval straddles a boundary.
8. **Differential-bias checks** (g1 §6.3), adapted to this metric: report the realised-token and
   duplicate-token distribution of high- vs low-sufficiency contexts, and recompute the headline
   contrast on the low IDF-overlap tertile. A `DIFFERENT` verdict whose winning arm also delivers
   systematically more unique text is reported as **confounded with delivered volume**.
9. **Manifest**: judge model id *as served* (asserted against `/v1/models` at import, as `mango.py`
   already does), endpoint port, `enable_thinking`, temperature, seed, prompt sha256s, nugget-list
   hash per query, concurrency cap, and the budget B.

**The agreement target.** κ(T1–human) ≥ **0.60** for the metric to carry a `DIFFERENT`/`EQUIVALENT`
verdict; ≥ **0.40** for screening. κ(T1–T2) is reported but is *not* a substitute — two models can
agree because they share a bias. Where they disagree, those items are oversampled into T3, which is
what makes 50 human items buy more than 50 random ones would.

---

## 5. Question 4 — power

### 5.1 What the measured σ_d does and does not transfer

`n ≈ ((z₁₋α/₂ + z₁₋β)·σ_d/δ)²`, z = 3.24 at 90% power / α = 0.05; **δ80** (the 80%-power floor
this project computes *before* reading any threshold) uses z = 2.80.

The measured retrieval σ_d — **0.152–0.173** for nDCG@10, 0.119 at the judged-only rung — is the
right starting point but **does not transfer directly**, for two reasons pulling in opposite
directions:

**Downward — the paired nugget design removes between-query variance.** For an arm-independent
nugget list, the per-query difference is

```
d_q = (1/m_q) · Σ_i [ supported_i(arm A) − supported_i(arm B) ]
```

Only nuggets whose verdict *flips* between arms contribute. Between-query difficulty — which
dominates the variance of nDCG@10 — cancels exactly. If the per-nugget flip rate is p_flip and
m = 8, then σ_d ≈ √(p_flip/m) in the pure-noise-free case: p_flip = 0.20 gives **0.158**,
p_flip = 0.10 gives **0.112**. Same ballpark as nDCG, and no worse.

**Upward — judge noise is *not* shared between arms.** Each arm gets its own judge call, so
independent judge error enters twice: σ_d² = σ_config² + 2σ_judge²/m. This is the term that could
kill the metric. With per-nugget judge error rate ε = 0.10 and m = 8, the added term is
√(2·0.10·0.90/8) = **0.150** — which would roughly *double* σ_d in quadrature. **Averaging over m
nuggets is the only defence, and it is why S2 (a single ordinal draw, m = 1) is the wrong primary
metric**: at m = 1 the same ε contributes 0.42.

**Consequence, stated as a design rule:** *m*, the nugget count, is a power parameter. Target
**8–12 checkable nuggets per query**; treat a query that yields fewer than 5 as ineligible.

### 5.2 What n buys, under three scenarios

| scenario | σ_d | δ90 at n=100 | δ90 at n=300 | δ90 at n=500 | n for δ=0.05 |
|---|---:|---:|---:|---:|---:|
| optimistic (low flip rate, clean judge) | 0.12 | 0.039 | 0.022 | 0.017 | **60** |
| central (flip 0.20, ε 0.05) | 0.18 | 0.058 | 0.034 | 0.026 | **135** |
| pessimistic (flip 0.20, ε 0.10) | 0.26 | 0.084 | 0.049 | 0.038 | **284** |

Under Holm across the study's four pre-registered decision contrasts (z ≈ 2.50 at α = 0.05/4 —
much gentler than stage 1's 23-way inflation, because sufficiency is asked only of the short-list),
multiply n by ≈1.34.

**Binary sufficiency, for contrast.** If the metric were the yes/no form (S2 binarised), power is
McNemar-driven: n ≈ z²·p_disc/δ². At a 10% discordance rate and δ = 0.05 that is **420 queries**;
at δ = 0.10, **105**. The binary form is not catastrophic, but it wastes the free variance
reduction that m nuggets provide, and it cannot say *which* evidence went missing.

**The honest headline for sizing:** the effects this metric is chasing behind the reranker are
**not known to be non-zero at all** — every size contrast is unresolved reranked on both legs. So
n should be chosen against the **equivalence** question, not the difference question: to declare
`EQUIVALENT` at a TOST margin of 0.05 with 90% power needs roughly the same n as detecting
δ = 0.05, i.e. **~135–285 judged queries at the central-to-pessimistic σ_d**. That is the number
the full study should budget: **~300 judged queries per leg**, which Leg C can supply without
strain (6,024 pairs already mined from 1,200 articles) and Leg A cannot (90 topics is the whole
leg).

**Therefore: Leg C is the sufficiency workhorse and Leg A is the validity anchor** — the exact
inverse of their roles on the retrieval metrics, and it follows from the arithmetic, not from
preference.

### 5.3 The pilot measures the real variance, and decomposes it

Assuming σ_d is the mistake this project already corrected once (`RESULTS-legBC-pilots.md` §4
replaced an assumed 0.10–0.20 band with a measured 0.156). The pilot must not repeat it. It
measures **three quantities separately**:

1. **σ_d(total)** — the paired per-query SD between the two extreme arms. Read directly.
2. **σ_judge** — from re-judging a **20% subsample** with a different seed/order. The
   within-item, between-replicate SD estimates σ_judge directly.
3. **σ_config = √(σ_d² − 2σ_judge²/m)** — what is left, and the only part that more queries can
   help with.

That decomposition answers the question that decides the scale-up budget: **if judge noise
dominates, buy replicates (k judge calls per item cuts that term by k), not queries** — replicates
cost one LLM call each, whereas a new query costs an S3 fetch, an oracle pass, a nugget extraction,
*and* a call per arm. If config variance dominates, buy queries. Nobody in this project has this
number, and it is ~120 extra LLM calls to get it.

---

## 6. Question 5 — the pilot

**Name:** Phase-0 step 4 — sufficiency instrument validation.
**Shape:** 100 queries × 5 arms × 1 budget (+ 3 probe arms), ~1 GPU-h, ~1.5–2 h of mango wall
time, 2.5–3 h of human reading. **It prunes no config.** Its only output is a verdict on whether
the instrument works.

### 6.1 The query set — 100 queries, three legs, chosen for what each proves

| leg | n | source | gold evidence | judged by | what it is for |
|---|---:|---|---|---|---|
| **A — CDS** | 40 | 40 of the 90 topics, seeded; the 10 step-3 topics **included** so three arms reproduce byte-identically | §7a oracle argmax sections of the top-3 graded relevant docs, pooled | Qwen + Scout + human | validity; human topics, human qrels, no LLM upstream |
| **C — citances** | 40 | 40 of the 6,024 mined pairs, position-filtered | oracle argmax section of the cited document | Qwen + Scout + human | the power leg; proves the workhorse construction before ~300 are built |
| **B — LLM-generated** | 20 | 20 of the 260 accepted | the recorded source section (**no oracle, no LLM**) | S1 + Qwen + Scout | the circularity estimate (§4.3); *not* part of any headline |

**Leg A's unit is the query, not the (query, doc) pair.** The 2,161 oracle pairs look like free n;
they are not — pairs inside a topic share a query, so effective n for anything query-driven is
**90 topics**, a point the Leg B/C pilot made about its own oracle sample. Nuggets are therefore
pooled across the top-3 graded documents' argmax sections and **capped at 12**, deduplicated by the
judge-free step of asking the extractor for distinct claims.

### 6.2 The arms — five, at a fixed 8,192-token budget

| arm | what | role |
|---|---|---|
| **A1** `fixed_tok256_ov0` | fine | treatment |
| **A2** `fixed_tok512_ov0` | shipping-adjacent | treatment / control config |
| **A3** `fixed_tok2048_ov0` | coarse | treatment |
| **A4 — GOLD** | the gold evidence section(s) alone, padded to nothing, truncated to budget | **positive control: must score ≥0.85** |
| **A5 — NEGATIVE** | top-k from a **grade-0 hard negative** document set only (Leg A) / a topically-matched non-cited document (Leg C) | **negative control: must score ≤0.25** |

Overlap gets no arm: it is the study's one uncontested powered null, and re-litigating it here
would spend the pilot's power on a settled axis. Its *budget* cost (`duplicate_tokens`) is
recorded for free anyway.

**Why grade-0 documents and not a lead-512 index as the negative control.** Dense lead-only was
*competitive* with full indexes on Leg A (`tok512-full − lead512` recall@100 = −0.062, CI [−0.084,
−0.040]) — it is not a floor, it is a strong baseline. Grade-0 CDS documents are fluent, topical,
and **human-judged non-relevant**; 300 per topic already exist in the pilot corpus. If a judge
scores that context as sufficient, it is measuring topical vibes and the instrument is dead. This
is the sharpest gate in the design.

### 6.3 Three probes, ~100 judge calls each, that a naive design would omit

- **(a) Budget slope.** Arm A2 re-judged at B ∈ {4,096, 16,384}. Gives the sufficiency-vs-budget
  curve (product-relevant on its own) and shows the metric responds to *more evidence* in the
  right direction — a monotonicity sanity check no single-budget design can make.
- **(b) Padded-gold probe.** Arm A4's gold context, re-judged after padding with irrelevant filler
  to the full budget. **Any movement in the score is judge length-bias, measured directly.** This
  replaces the naive "regress sufficiency on context length" check, which is degenerate at equal
  budget because realised tokens are near-constant by construction.
- **(c) Position probe.** The gold span placed first vs last inside an otherwise identical
  assembled context. The CE has a measured position effect at the same scale; assuming an LLM
  judge has none is an assumption, and this is 100 calls to replace it with a number. If the effect
  is large, chunk **rank order** (which decides where evidence lands in the prompt) becomes a
  confound the main protocol must randomise away — which §4.5(3) already does, and this probe is
  what justifies it.

### 6.4 Cost

**LLM (mango, concurrency ≤ 4 per endpoint, as every prior run held).**

| stage | model | calls | prompt tok | completion tok |
|---|---|---:|---:|---:|
| nugget extraction (thinking on) | Qwen :8004 | 100 | ~0.25 M | ~0.15 M |
| primary judging, 5 arms | Qwen :8004 | 500 | ~4.5 M | ~1.0 M |
| probes (a)(b)(c) | Qwen :8004 | ~300 | ~2.7 M | ~0.6 M |
| self-consistency duplicates (10%) | Qwen :8004 | ~90 | ~0.8 M | ~0.2 M |
| variance replicates (20%, §5.3) | Qwen :8004 | ~120 | ~1.1 M | ~0.25 M |
| cross-family judging (200 stratified + all 100 Leg B items) | **Scout :8003** | 300 | ~2.7 M | ~0.12 M |
| thinking on/off calibration (§6.5) | Qwen :8004 | 40 | ~0.36 M | ~0.02 M |
| **total** | | **~1,450** | **~12.4 M** | **~2.4 M** |

**Wall-clock**, extrapolated from the only measured rates on this endpoint (Leg B re-run: stage B
419 calls / 1.28 M completion tokens / 2,189 s; stage C 389 calls / 336 s; stage A thinking-off
495 k prompt / 87 s = 5.7 k prompt-tok/s): completion throughput ≈ 634 tok/s aggregate at
concurrency 4 ⇒ **~1.1 h** for the Qwen completion load, prefill overlapping at ~35 min. Scout adds
minutes. **Budget 1.5–2 h of mango wall time**, and note this is a shared host — the ≤4-in-flight
cap and back-off policy are not negotiable.

**GPU on `coconut`.**

| item | tokens | at 164 k tok/s (±2×) |
|---|---:|---:|
| Leg A: 30 new topics × ~400 docs × ~6.9 k tok × 3 configs | ~249 M | **0.42 h** |
| Leg A: 10 step-3 topics × 3 configs | 0 | **free** — `emb_tok256_ov32.npy`, `emb_tok512_ov64.npy`, `emb_tok2048_ov256.npy` exist; re-embedding one cell is the reproduction gate (stage 1 proved byte-identical chunks and 0.0000 max metric diff) |
| Leg C: 40 cited + 4,000 distractors × ~9.3 k tok × 3 configs | ~113 M | **0.19 h** |
| Leg B: 5,000-doc ×11.5 rung × 3 configs (corpus hash `c6fb04503fdee62a`, asserted before embedding) | ~132 M | **0.22 h** |
| reranking: 100 q × 100 cand × 3 configs | 30 k pairs | **30–75 s** at 391–1,037 pairs/s |
| oracle for new Leg C golds: 40 pairs × ~20 units | ~800 window-pairs | seconds |
| **total** | | **≈ 0.85 GPU-h**, call it **1 GPU-h**, band 0.4–1.7 h |

S3 fetches: ~12,000 Leg A judged documents from `pmc-oa-opendata` at the measured 34–44 art/s ≈
**5–6 min**, 98.5% fetchable.

**Human:** 50 items at ~3 min each ≈ **2.5 h**, plus ~30 min of calibration against a 10-item gold
key written by the study lead before reading anything (g1 SOP §2.3). A second reader for
κ(human–human) is **strongly preferred** — without it the human-ceiling rule cannot be applied and
the judge's band is uncapped-but-unverified. If only one reader is available, say so and cap the
claim at MODERATE.

### 6.5 What is pre-registered before a single call

A `PREREG-step4.md` file, on the pattern this project already uses, fixing: the queries and their
seeds; the nugget-extraction prompt and its hash; the judging prompt and its hash; the rubric;
`enable_thinking` **per stage** (decided by the 40-item calibration in §6.4 — if thinking-off
agrees with the human tier as well as thinking-on, it is 40× cheaper and it wins); temperature 0;
the budget B; the arm list; **and δ80 computed for every threshold below before any of them is
read**. Six readings in the Leg B re-run failed their own power check and were written as
unresolved rather than as nulls; that discipline is the house standard and applies here.

### 6.6 The acceptance criterion — "this metric works, scale it up"

All six must hold. Any single failure stops the scale-up and is itself the finding.

| # | gate | threshold | why this number |
|---|---|---|---|
| **1** | **Instrument validity** | A4 (gold) ≥ **0.85**, A5 (grade-0) ≤ **0.25**, and the A4−A5 gap resolvable at the pilot's own δ80 | If the judge cannot separate the true evidence from fluent human-judged-irrelevant text, nothing downstream means anything. This is the gate, and it is the one most likely to fail. |
| **2** | **Judge agreement** | κ(T1–human) ≥ **0.60** (SUBSTANTIAL) on 50 stratified items, read off the **CI lower bound**; κ(human–human) ≥ 0.40 or the verdict is RUBRIC_FAILURE; self-consistency ≥ **0.95**; hallucinated-support rate ≤ **0.05** | Imported unchanged from g1 SOP §6, plus the quote check. Below 0.60 the metric is screening-only and **no equivalence claim may be made at all** — attenuation manufactures equivalence (g1 §6.2), and equivalence is the most likely finding. |
| **3** | **Non-redundancy** | Kendall τ(per-query sufficiency, per-query nDCG@10) ≤ **0.90** | If sufficiency is a monotone re-encoding of the retrieval metric already in hand, it is an expensive way to learn nothing. **This is the designed "this won't work" exit** and it should be checked first, on the cheapest arms. |
| **4** | **Not a length proxy** | padded-gold probe (b) moves the score by ≤ **0.05**; position probe (c) moves it by ≤ **0.05**; the arm ranking is unchanged when contexts are re-ordered | Length and position bias are the two documented LLM-judge failure modes that align with the treatment axis. If either is large, the metric is measuring prompt shape. |
| **5** | **Powered** | σ_d measured and decomposed (§5.3); projected n for δ = 0.05 at 90% power under Holm ≤ **500 queries** | 500 Leg C queries are affordable; 2,000 are not. A projection above the line means the metric is real but unaffordable, which is a different finding from "it does not work" and must be reported as such. |
| **6** | **Circularity bounded** | the difference-in-differences inflation estimate (§4.3) reported with its CI; if \|inflation\| > **0.10**, the §7 amendment stands as written and Leg B is never judged by a Qwen model | Gives the plan's circularity rule a measurement instead of an assertion. |

### 6.7 What the pilot may **not** conclude

- **Nothing about which chunking config is better.** Five arms on 100 queries with an unvalidated
  instrument prunes nothing, exactly as stage 1 on both legs pruned nothing.
- **Nothing about absolute answer quality.** Sufficiency of *retrieved context* is an upper bound
  on answer quality, never an estimate of it: a generator can fail on sufficient context.
- **Nothing about the user query distribution.** Leg A is clinical case narratives, Leg C is
  citances, Leg B is LLM-written. `long-doc-judged-set.md` §10 already says none of them is our
  users, and sufficiency does not change that.
- **Nothing about Legs A and C agreeing.** If sufficiency reproduces the size disagreement (Leg A
  coarse-wins vs Leg B fine-wins at the 512→1024 step), that is the *finding*, not an averaging
  problem — and it would mean the disagreement is not an artefact of document-level rollup, which
  is currently the leading explanation.

---

## 7. What this changes in the two plan documents

Small, specific, and each traceable to something measured above.

1. **`long-doc-judged-set.md` §7 — extend the circularity rule** to query construction and to
   judging, in the words of §4.3 above. The Leg B re-run identified the hole; this supplies the
   patch and a measurement of whether it is needed.
2. **`long-doc-judged-set.md` §8 — add check 6, *instrument validity*,** with the gold/grade-0
   control pair. It belongs with the other acceptance checks: it gates spend, and it is cheap.
3. **`long-doc-judged-set.md` §5/§6 — swap the legs' roles for the sufficiency metric.** Leg A is
   the validity anchor and cannot supply n > 90; Leg C is the workhorse because 6,024 pairs already
   exist and no LLM wrote them. This is the opposite of their retrieval-metric roles and follows
   from §5.2's arithmetic.
4. **`chunking-evaluation.md` — the metric list gains `sufficiency@B`** beside `recall@k` /
   `nDCG@10` / `MRR@10`, **defined at a token budget**, with `realised_tokens`, `wasted_tokens` and
   `duplicate_tokens` reported alongside — the first three quantities in this study that give
   chunk size and overlap a *cost* that document ranking cannot see.
5. **`chunking-evaluation.md` pre-registration table — add one row:** *"is the retrieved set
   sufficient?"*, settled by `sufficiency@8192` on the short-listed configs, changed by a
   TOST-at-0.05 equivalence or a resolved difference, **and never settled on a leg whose queries
   an LLM wrote.**
6. **`docs/model-registry.md` is stale in a specific, fixable way:** Scout is live on
   `mango.cels.anl.gov:8003`, Qwen3.6-35B-A3B on :8004, Qwen3.6-27B on :8000. Fix the ports; keep
   the `/v1/models` assert-at-import pattern that caught this.

---

## 8. Files this design would produce

```
scratchpad/step4/
  PREREG-step4.md            thresholds + delta80 for each, before any call
  RESULTS-step4-sufficiency.md
  suff_common.py             budget-constrained context assembly, token accounting
  nuggets.py                 gold-evidence -> nugget list (once per query, hashed)
  judge.py                   Qwen/:8004 + Scout/:8003 clients, assert-served-id, <=4 in flight
  containment.py             S1, deterministic, no LLM
  probes.py                  budget slope / padded gold / position
  variance.py                sigma_d, decomposition, delta80, projected n
  agreement.py               kappa, bands, human ceiling, self-consistency
  human/                     50-item blinded export + the 10-item calibration key
  manifest.json              seeds, prompt hashes, model ids as served, budget, caps
```

*Nothing in the above writes to a store, reads production `:9200`/`:6333`, or touches GPUs 6–7.
Experiment stores, if any are ever needed, are the dev tenant only — and this design needs none:
retrieval is exact brute-force cosine over in-memory embeddings, as both stage-1 runs did.*
