# Synthesis stage — measuring the answer, not only the evidence

*2026-09-07. Status: `PROPOSED`; **revised the same day after independent review (§9)** — the
review is [`REVIEW-synthesis-stage.md`](REVIEW-synthesis-stage.md). Companion to
[`SPEC-confirmation-run-r3.md`](SPEC-confirmation-run-r3.md).
The chunking study measures whether the right evidence reaches the generator's context. This
stage measures what the generator then does with it — the quality the user actually receives —
and what it costs. Nothing here changes revision 3's endpoints or contrasts; it adds a stage
that consumes their packed contexts.*

<!-- review: every change below is marked inline; the change log is §9. Unmarked text is the
     author's. -->

## 1. Why, and why now

The decision ledger — section 09 of the published study report (artifact `e5b3169b-4897-48f7-b350-2de7276caff0`; source at `~/Development/worktrees/phase0-rescue/artifact/`, not in this repository) — shows two empty metric columns: answer quality and generation cost. Every configuration decision so far is justified by evidence delivery, on the assumption
that delivered evidence becomes a correct, cited answer. That assumption is testable now,
cheaply, because the **pointed-question population** (r3 §11, #506) carries a **gold sentence by
construction**: each query was written from a paraphrase of a deep section, and the sentence(s)
of that section answering it were then located verbatim and stored with the query as D1 spans
(#506 §2, pass C). <!-- review: was "written from a known sentence … a measurement, not a
judgment". The gold is located *after* the query by the same generator, and #506 §6.1's hand
read found 2 of 10 gold sentences answer a narrower question than asked (`pq_0054`, `pq_0017`).
On this population "did the system answer correctly" is close to a measurement — closer than
anything the CDS population offers — but it is a judged one, with the mis-targeting rate bounded
by the human read in §6. --> On this population correctness can be scored against a located
sentence rather than against a judgment about a topic, which is what makes the stage cheap.

## 2. What is measured, per (query, configuration)

The configuration is one of revision 3's **scoring arms** (an index arm, or a delivery arm on
its base index arm — §3.5's arms are transforms, not a crossed axis) × retrieval mode × budget;
<!-- review: was "index arm × delivery arm × retrieval mode × budget"; the delivery arms are
not crossed with the index arms (r3 §3.5). --> the packed context is exactly what §7.3's rule
admits. The generator receives the query and that context under a fixed prompt (§5) and must
answer with citations to the supplied passages, or abstain.

| endpoint | definition | scored how |
|---|---|---|
| **Correct** | the answer **answers the question** in agreement with the gold sentence(s) <!-- review: was "states the fact the gold sentence states" — that targets the gold, not the question, and the two differ on 2/10 hand-read queries (#506 §6.1). Queries whose gold the human read marks as *not answering the question* (§6, extra question 2) are excluded **identically for all arms**, fixed before any contrast is read (r2 §8.5.6's arm-invariance rule); the exclusion count is a first-class row. --> | primary: an LLM judge (§6) given question, answer and gold, calibrated against a human-scored subset, on a **three-point scale** 0 / ½ / 1 (wrong or abstained-with-evidence / partial / correct) reported both as the binary `Correct` (= 1) and as the graded mean; <!-- review: the graded form is the pre-registered secondary with lower variance — see §4 on why a binary endpoint at n = 177 is unlikely to resolve ε = 0.05. --> secondary: deterministic containment of the gold's **answer tokens** — the numerals and rare terms (IDF ≥ 5.60, #506's bar) present in the gold sentence and absent from the question, extracted before any answer is generated and stored per query <!-- review: was "the gold's key entity/number", which is not computable: the record's `entity` is the generator's declaration and sits in the front matter 45.5 % of the time (#506 §3). --> |
| **Faithful** | every factual claim in the answer is supported by a cited passage that was actually in the context | claims are segmented **deterministically**: `ragstack.ingestion.chunkers.sentence_spans` over the answer; each sentence with the `[n]` citations it carries is one claim; a sentence with no citation that is not the abstention sentence is **unsupported by rule** (no judge call); the judge scores only (cited claim, cited passage) pairs. Unsupported-claim rate per answer, mean per configuration. <!-- review: segmentation was unspecified; a judge that segments and scores its own segments is r2 §6.4/§6.6.6's self-agreement problem. --> |
| **Cited precisely** | pointed: a cited passage contains a gold span; CDS: the **pooled support mass** (#512, r3 §10 item 4(a)) of the sentences in the cited passages, as a fraction of the document's mass | deterministic from spans. <!-- review: was "a sentence with pooled support ≥ 0.5 on CDS". The 0.5 threshold is derived nowhere (#512 reports the best-supported sentence in 0.75 of readings and the top three carrying 0.33 of votes; no fraction-above-0.5 statistic exists) and r3's graded definition is mass-weighted. A threshold sweep at {0.25, 0.5, 0.75} is a descriptive column. The support map exists for the **308 labeled pairs only** (#512 §0): on CDS the endpoint is computed over citations into labeled pairs, and the **unlabeled-citation rate** is printed beside it. --> |
| **Abstains correctly** | when the admitted context contains **no gold** — pointed: no span of the *union* gold (construction ∪ labeler-found alternatives, r3 §11 guard 2) — the answer abstains rather than answers | deterministic given the context; the *false answer rate under no evidence* is the safety number. <!-- review: was "no admitted chunk contains the gold", which conditions on one location where D4 admits any valid set: an answer drawn from a valid alternative passage would be scored as a false answer. Until the union gold exists (it needs a labeler to pass r3 §3.7), the rate is computed on construction gold and **bounded** by the pointed read's alternative-passage rate (r3 §11 guard 2's extra question), printed beside it. On CDS no gold exists: the raw abstention rate is reported, `DESCRIPTIVE`, and "correctly" is not claimed. --> |
| **Cost** | generator prompt tokens, completion tokens (thinking tokens separately where a generator thinks), wall latency p50/p95, per call | from the chat-completions `usage` field and the harness clock <!-- review: was "from the serving log"; the harness calls the vLLM endpoints directly. --> ; plus retrieval latency p50/p95 **for the Stage 2 shortlist only, timed on the served path at Stage 2** (r3 §3.4) — the offline harness's timings are not serving latency and are labeled so <!-- review: §7 says "zero retrieval cost" while this row wanted every configuration timed on the served path; only the shipping index is on the served path before Stage 2. --> |

Reported per configuration with topic-level (pointed: query-level — one query per document,
#506 D-3) cluster bootstrap CIs, δ80 beside every descriptive row, and the same power-first
reading as revision 3.

## 3. Populations — and the two stages this runs in

<!-- review: this section is rewritten. The 177 queries are r3 §11 guard 3's *Stage 0b′ sizing
set*, generated on the development topics' documents; #506 says the population "is a candidate
second population, not an adopted one" until guard 1 (discrimination + window) is read, and
guard 1 has not been read because no retrieval has run on it. By r2 §2.1 and P.9 everything on
the development set is calibration. The confirmatory synthesis contrasts therefore belong on the
confirmation-run pointed set (≤ 600 queries, r3 §11 guard 3) after freeze, and this stage takes
the same two-stage shape as revision 3. -->

* **Primary: the pointed set** (r3 §11) — gold sentence by construction; correctness is scored
  against a located sentence. **Calibration** runs on the 177 development-slice queries
  (#506); **confirmatory** contrasts run on the confirmation-run pointed set, once §11 guard 1
  has admitted the population and guard 3 has sized it. Whatever the pointed retrieval pass
  decides about `pq_0241` (#506 §5.1: a 928-unit conference-abstract volume) is inherited here.
* **Secondary: the CDS development topics** — no single gold answer exists (the need is a
  decision, not a fact), so only *faithful*, *cited precisely* (support mass) and the raw
  *abstention rate* are scored, plus a judge-rated *usefulness for the stated need* labeled
  `DESCRIPTIVE`. Nothing on CDS is confirmatory in this stage.

**What the calibration stage must produce before anything is confirmatory** (the r2 §8.5.7
pattern): per contrast, σ_d of binary and graded `Correct`, the per-query **discordance d**
(§4), δ80 at the calibration n and projected at the confirmation n; the per-arm level of every
endpoint against the [0.15, 0.90] window (r3 §3.1 — an arm outside it is descriptive for that
endpoint; Phase 0 measured Leg-B-style queries at PH@10 ≈ 0.97–0.99, so a `Correct` **ceiling**
is the live risk, not a floor); κ(judge–human) for both judge tasks (§6); the cost table (§7)
measured, not projected. All of it `[FROZEN-AT-CALIBRATION]` in the manifest.

## 4. Arms, cells and contrasts

All arms are revision 3's; no new embeddings. The synthesis stage adds one axis:

| axis | values | why |
|---|---|---|
| generator | `mango:8003` Llama-4-Scout (production — `LLM_MODEL` on every tenant env), `mango:8000` Qwen3.6-27B (dense, same family as `:8004`), `mango:8004` Qwen3.6-35B-A3B (MoE, reasoning) <!-- review: was "one larger and one reasoning alternative"; 27B dense beside 35B-A3B is not simply larger, and ANSWER-sufficiency §4 records the two Qwens as one family. --> | the production model plus two alternatives — the *generator × chunking* interaction is the thing nobody has measured |
| prompt | one fixed answer-with-citations prompt (§5) | prompt engineering is not this study |

**Per generator, stated in the manifest before the first call** <!-- review: added. #512 ran
`mango:8004` as a reasoning judge with `max_tokens` 12,000 and stripped 22 M thinking characters;
under §5's `max_tokens` 512 a thinking model spends the budget thinking and returns empty
`content`, which `OpenAILLM.complete` raises on. -->: whether thinking is on. Default: **off**,
via the served model's `extra_body` (`chat_template_kwargs: {enable_thinking: false}`, the
production pattern in `llm.py`), so all three generators answer under the same 512-token answer
budget. A reasoning-on variant of `:8004` is a descriptive fourth column with its own thinking
budget stated and its thinking tokens in the cost row.

**The confirmatory cell, fixed in advance.** Every confirmatory number in this stage is read on
**the production generator (`mango:8003`), `hybrid` + rerank, B = 16,384** — the served shape,
as r3 §3.4 fixes it. <!-- review: added; the draft named the mode and budget for S1 only and the
generator for none. --> Its arms are the five the contrasts name (`fixed_tok512`,
`fixed_tok1024_ov0pct`, `fixed_tok256_ov0pct`, `fixed_tok2048_ov0pct`, `nbr1_512`): **5 arms ×
1 mode × 1 budget × 1 generator = 5 generations per query**. Everything else is descriptive and
counted in the ledger below.

**Pre-registered contrasts, on the pointed set, `Correct` as primary:**

| id | contrast | question |
|---|---|---|
| S1 | `fixed_tok512` (shipping) − `fixed_tok1024_ov0pct` | does the storage lever cost correct answers? (the N1 decision seen from the answer) |
| S2 | `nbr1_512` − `fixed_tok512` | does neighbour delivery turn into better answers, not only better containment? |
| S3 | `fixed_tok256_ov0pct` − `fixed_tok2048_ov0pct` | the size replication, at the answer |

Non-inferiority for S1 (ε = 0.05 on `Correct`, control − candidate, one-sided α = 0.025, the
single member of its family); superiority for S2, S3 (Holm α = 0.05 across the two, bar 0.05).
**Conjunctive with `Faithful`:** an arm that answers more correctly by hallucinating more is
not adopted — its unsupported-claim rate must be non-inferior at **ε_F = 0.05 absolute, one-sided
α = 0.025**, and the gate is the **joint** power of the two intervals, estimated by a joint
bootstrap over queries (both endpoints resampled together, 10,000 draws, seed `20260913`) with
the per-endpoint figures printed beside it — r3 §3.6's rule, reused verbatim. <!-- review: the
draft named no ε, α or joint power for the conjunctive component. -->

**Resolvability, stated before the data** <!-- review: added; the draft stated ε with no σ_d
requirement, and the arithmetic is r2 §7.4's. -->. `Correct` is binary per query, so σ_d ≈ √d
where d is the per-query discordance rate between the two arms. At n = 177, one-sided NI at
α = 0.025 and 80 % power (constant 2.802, r2 §8.5.1), ε = 0.05 needs

> σ_d ≤ ε·√n / 2.802 = 0.05 · 13.30 / 2.802 = **0.237**, i.e. **d ≤ 0.056** — fewer than
> 1 query in 18 may change verdict between the arms.

Stage 0 measured d = 0.10–0.40 on the analogous binary (`ES-Hit@4096`, row 4) on CDS. If the
pointed set behaves anything like that:

| d | σ_d | δ80 at n = 177 | n for ε = 0.05 |
|---|---|---|---|
| 0.056 | 0.237 | 0.050 | 177 |
| 0.10 | 0.316 | 0.067 | 314 |
| 0.20 | 0.447 | 0.094 | 628 (over the §11 cap of 600) |
| 0.40 | 0.632 | 0.133 | 1,256 |

So the calibration stage **measures d per contrast** and the rule is r2 §8.5.8 step 3: a
contrast whose measured d puts ε = 0.05 outside the confirmation n's reach is re-declared
descriptive *before* freeze, with its δ80 printed; ε does not move. The graded `Correct` (0 / ½ /
1) is carried as the pre-registered secondary with its own σ_d, because it is the form most likely
to resolve, and if binary and graded disagree in sign or verdict the contrast is
`UNRESOLVED-BY-ESTIMAND` (r3 §3.1's rule).

**Multiple-comparison ledger** <!-- review: added, per r2 §8.1.1. -->: three confirmatory
contrasts (S1 at α = 0.025 one-sided; S2, S3 Holm at α = 0.05), each with a conjunctive
`Faithful` component at α = 0.025 one-sided; every other cell — the two other generators, the
`vector` and `bm25` modes, B ∈ {4,096, 32,768}, the remaining six scoring arms, the CDS
population, the reasoning-on column — is `DESCRIPTIVE`, with a CI and δ80 and no p-value.

**Descriptive families:** generator × arm; retrieval mode × arm; abstention rate under
"no gold delivered"; cost per correct answer (tokens and seconds per configuration divided by
`Correct`), which is the number a product owner actually wants. On the generator × arm family,
note that **Scout authored the pointed queries and located their gold** (#506 §2: passes A–D on
`mango:8003`); the Scout column may be easier for reasons unrelated to chunking, and is read with
that in mind. <!-- review: added as a named confound; not a judge circularity. -->

## 5. The generation prompt (frozen at calibration, hashed) — production-shaped

<!-- review: rewritten. The draft's prompt departed from production in four ways and did not
say why: production (`python/ragstack/llm.py`) cites `[n]` per *source in rank order* ("Cite the
passages you used as [n]"), concatenates `context_window` neighbours under ONE number with
`(context before)` / `(passage)` / `(context after)` delimiters, abstains free-form ("say you
don't know"), and packs at most `llm_max_context_chars` = 8,000 characters (≈ 2k tokens, × (2w+1)
with neighbours; `config.py:824`). A 16k-token packed context would be cut roughly eight-fold by
the shipping default. Measuring a prompt production does not send would answer a question the
product does not ask, so the stage adopts production's shape with two named deviations. -->

* **Formatting is `RagGenerator._format_context`'s**, byte-for-byte: passages numbered `[n]` in
  reranked order; a `nbr*` arm's neighbours concatenated under the source's number with
  production's delimiters; the user turn is `Context:\n…\n\nQuestion: …`. The harness keeps an
  offline `[n] → chunk_id` map per (query, configuration) so `Cited precisely` and `Faithful`
  resolve citations to spans.
* **Deviation 1 — the context budget.** `max_context_chars` is set so that the packed context
  is never trimmed (the packed budget in characters plus headroom, recorded); production's
  8,000-character default is **reported as a finding** in the ledger, because on the served path
  it, not chunking, bounds what the generator sees.
* **Deviation 2 — a fixed abstention sentence.** Production's system prompt is used verbatim
  with one sentence appended: *If the passages do not contain the answer, reply exactly "The
  provided passages do not answer this question." and stop.* `Abstains` is then deterministic.
  The delta to `_SYSTEM_PROMPT` is recorded and hashed.
* Temperature 0, `max_tokens` 512, no tools — `OpenAILLM.complete`'s defaults. Temperature 0 on
  vLLM under continuous batching is **not** deterministic (r2 §6.4 rule 4, measured): the seed
  is sent and recorded, and a seeded 5 % of generations is re-run to report the disagreement
  rate. <!-- review: added. --> The prompt goes into the report's prompt section like every
  other.

## 6. The judge, and how it is kept honest

* An LLM judge scores `Correct` and `Faithful` — a model **not** in the generator set.
  **One judge is pinned at calibration freeze: `claude-sonnet-5`.** The Argo gateway's GPT is
  named as a *second-family sensitivity*, reported separately and never pooled, if and when the
  gateway is reachable (#514 §6.6: it is not, from this host). <!-- review: was "Sonnet … or the
  gateway's GPT when reachable; recorded" — two judges would have been two instruments. -->
* **Transport.** Not the Claude Code CLI on the owner's account: #514 §1/§6.3 records that the
  judge run and the agent driving it share one session budget and that 867 calls hit the limit
  three times. An API key (or the Message Batches endpoint, at half price, since nothing here is
  latency-bound), model id pinned, raw responses hashed and stored. <!-- review: added. -->
* **Judge ≠ labeler, as a rule.** Sonnet 5 labeled the 308 development pairs in #514. Today no
  gold this judge scores against contains its labels: the CDS support map is Scout + Qwen only
  (#512), and the pointed gold is construction. **The judge's own labels never enter a gold it is
  scored against**; if the union gold (r3 §11 guard 2) later folds in #514's Sonnet sets, the
  judge is `claude-opus-5` or the gateway instead. What this does *not* buy is independence of
  inductive bias — r2 §6.6.6's shared-bias caveat is quoted in the results header, not a
  footnote. <!-- review: added. -->
* **Calibration, two reads, both through the Grading view** (r2 §6.6.2 protocol: two
  independent readers, same blinded subset, order seeded per reader, adjudication by joint read,
  pre-adjudication κ reported):
  1. **`Correct`:** 100 (question, answer, gold) triples, stratified by judge verdict
     (correct / partial / wrong / abstained) and by generator, drawn seeded from the calibration
     generations. Readers see **no configuration, generator, or judge verdict**. Each triple
     carries two extra questions: *(1) does the answer answer the question?* and *(2) does the
     gold sentence answer the question?* — (2) feeds the arm-invariant exclusion in §2.
  2. **`Faithful`:** 100 (claim, cited passage) pairs, stratified by judge verdict, same
     blinding. <!-- review: the draft calibrated `Correct` only; the conjunctive gate rests on
     `Faithful`. -->
  Acceptance is r2 §6.6.4's tiering, not only its top tier: κ(judge–human) < 0.40 → the judge is
  not usable for that endpoint and it is `DESCRIPTIVE`; 0.40–0.60 → `MODERATE`; ≥ 0.60 **or**
  positive-class agreement ≥ 0.85 → full strength. κ at n = 100 carries a 95 % half-width of
  roughly 0.16–0.20, so the CI is printed and the tier is read on the point estimate, as r2
  does. κ(human–human) is reported first and caps everything (< 0.40 is a rubric failure).
* **The read needs a contract change.** <!-- review: the draft said "a `citation-feedback`-style
  task … reuses the R-dev machinery exactly". It cannot: `grading_verdict.json`'s `verdict` is
  the six *evidence* verdicts (`correct` / `wrong-location` / `non-minimal` / `missed-evidence` /
  `correctly-none` / `ambiguous`), required and `additionalProperties: false`; `grading_task.json`
  has no answer field; `citation-feedback` is grading-ui.md phase 5's production feedback on
  *citations*; and `s0_rdev_score.py` would mis-read the export. --> A fourth kind,
  `answer-read`, with an `answer` text on the task, the gold as a claim tagged
  `sources: ["construction"]`, and its own verdict enum (`correct` / `partially-correct` /
  `incorrect` / `abstained-correctly` / `abstained-wrongly` / `ambiguous`), landed per the repo
  rule — `contracts/schemas` + `openapi.yaml`, then Python, then Go, then conformance — before
  the first calibration pair is read (grading-ui.md's phase sizing: about a day each side).
  Encoding the read through `extra_questions` with a sentinel `verdict` is the only stop-gap and
  **no κ quoted in this stage may come from it**.
* **Order against the other reads.** The same two readers do R-dev (≥ 100 evidence pairs) and
  the pointed read (≈ 50). R-dev runs **first**, or the calibration triples are drawn from
  documents outside the R-dev draw, so that reading a model's answer about a document does not
  anchor the evidence read of the same document. <!-- review: added. -->
* Deterministic checks (`Cited precisely`, `Abstains`, the uncited-claim rule) need no judge and
  are the backbone.

## 7. Cost and order

<!-- review: rewritten. The draft's "Contexts: already produced by Stage 0b′ packing" is not
true: r3 §5 steps 3–7 are unrun (banner, third update), #506 ran no retrieval, and the only pack
on disk (`/rag/tmp/stage0-conf/work/packed.json`) is Stage 0's — B = 4,096, dense + rerank, CDS
`summary` queries, seven arms. Its cell count (177 × 8 × 3 × 3) matched no arm list in r3 (11
scoring arms) or Stage 0 (7) and omitted budgets; the full r3 grid is 177 × 11 × 3 × 3 = 52,569
generations. -->

* **Contexts: produced by r3 §5 step 4 (Stage 0b′)** — the pointed retrieval pass, three modes,
  all arms, B ∈ {4k, 16k, 32k} — which has not run. This stage starts after it, on its packs,
  and adds no retrieval of its own.
* **Generation, calibration stage.** Confirmatory cell: 177 × 5 arms = **885** generations on
  `mango:8003`. Descriptive: the same 5 arms × the two other generators (1,770); the remaining
  six scoring arms × 3 generators at the served mode and budget (3,186); the `vector` / `bm25`
  modes and the 4k / 32k budgets on the confirmatory cell only (177 × 5 × 4 = 3,540). Total
  ≈ **9,400** generations, ≈ 5.5k on Scout and ≈ 1.9k on each Qwen. Basis for time: #512 measured
  Scout at **1.73 s/record at ≈ 12.4k prompt tokens** and Qwen3.6-35B-A3B at 7.73 s with
  reasoning on; a 16k-SFR context is ≈ 20k Scout tokens (r3 §3.3's ratio), so **2–3 s per Scout
  call and 3–5 s per thinking-off Qwen call are the projections, measured at the smoke**. At ≤ 4
  in flight per endpoint, the three endpoints in parallel: **≈ 1–1.5 h wall**. The draft's
  "~5 s, 4.5 h" was unmeasured.
* **Judge, calibration stage.** Two tasks per answer. `Correct`: one call per generation
  (question + answer + gold ≈ 1k tokens). `Faithful`: one call per cited claim (claim + cited
  passage, 0.6–2.5k SFR tokens per passage, ×3 on `nbr1_512`), ≈ 3–5 claims per answer. Basis,
  both stated: Sonnet 5 list price is **$2 / $10 per MTok** → ≈ $0.003 per `Correct` call and
  $0.005–0.02 per `Faithful` call; #514's *measured* CLI rate was **$0.133 per ≈ 12k-token pair
  ≈ $0.011 / 1k tokens, 4× list** — which of the two the API-key transport realises is the first
  number the smoke reports. Projection: 9.4k `Correct` calls + ≈ 35k `Faithful` calls ≈ **$250 at
  list, ≈ $1,000 at #514's rate**; Batches halves either. Judge everything in the confirmatory
  cell; judge the descriptive cells on a seeded 30 % sample if the rate comes in at #514's. The
  draft's "$0.02 each ≈ $250 or free on the gateway" had no basis and counted one task.
* **Human calibration.** Two reads of 100 × 2 readers at 2–4 min per item (r2 §11's 4–7 min is
  for evidence pairs): **≈ 13–27 person-hours** plus adjudication — the third read on the same
  two readers, scheduled, not discovered. <!-- review: was uncosted. -->
* **Order:** (1) freeze this spec (hash beside r3's); (2) r3 §5 step 4 runs and the pointed
  retrieval pass reads §11 guard 1; (3) the `answer-read` contract lands; (4) smoke: 20 queries ×
  5 arms × 3 generators, measured seconds and dollars, prompt hash asserted; (5) generate the
  calibration grid; (6) judge; (7) the two human reads (R-dev first); (8) read the calibration
  gate — κ tiers, d per contrast, window per arm, δ80 at the projected confirmation n — and
  re-declare contrasts per §4; (9) **confirmatory** generation and judging on the confirmation-run
  pointed set after r3's freeze and its retrieval pass, contrasts read once. Development topics
  only until step 9; no confirmation topic's document is read (#506 §1 excluded the 578
  confirmation-topic relevants from the pointed source slice, asserted in code).

## 8. What this stage cannot establish

Whether a *different* generator prompt, a different embedder, or a different reranker changes
the answer — one prompt, one embedder, one reranker. The ledger marks those as open axes; this
stage closes the answer-quality and generation-cost columns for the configurations the study
already compares. **Named limitations** <!-- review: added. -->: the pointed queries and their
gold were authored by the production generator (§4); the pointed gold is a single location until
the union exists (§2, `Abstains`); the judge shares web/biomedical pre-training with every model
in the stack (r2 §6.6.6); and production's own 8,000-character context budget (§5) means the
served path does not today deliver the 16k context this stage measures — a finding, not a
confound, and the one that most needs the product owner's decision.

## 9. Change log — review-driven revisions, 2026-09-07

An independent reviewer with no session context read the first draft against r3, r2, #506, #512,
#514, the grading contract and the pinned production code
([`REVIEW-synthesis-stage.md`](REVIEW-synthesis-stage.md)). Its blocking findings and what changed:

| finding | change |
|---|---|
| the 177 queries are r3 §11's Stage 0b′ sizing set on dev documents, a *candidate* population until guard 1 is read; the draft pre-registered confirmatory contrasts on them | §3 split into calibration (the 177) and confirmatory (the confirmation-run pointed set after freeze); guard 1 a precondition; `[FROZEN-AT-CALIBRATION]` slots listed |
| binary `Correct` at n = 177 resolves ε = 0.05 only at discordance d ≤ 0.056 (σ_d ≤ 0.237); Stage 0 measured 0.10–0.40 on the analogous binary; no δ80 or gate stated | §4 resolvability table; d measured at calibration and gating confirmatory status (r2 §8.5.8 step 3); graded `Correct` as the pre-registered secondary; `UNRESOLVED-BY-ESTIMAND` on disagreement |
| the conjunctive `Faithful` component had no ε, α or joint power; the confirmatory cell (generator, mode, budget) was unnamed; no ledger | §4: ε_F = 0.05, α = 0.025, joint bootstrap per r3 §3.6; confirmatory cell fixed; ledger added |
| "contexts already produced by Stage 0b′" — Stage 0b′ has not run and the pointed set has never been retrieved | §7: a dependency on r3 §5 step 4, not a sunk cost |
| `citation-feedback` cannot carry an (answer, gold) read: six evidence verdicts, no answer field, scorer coupling | §6: `answer-read` kind as a contract change before the read; stop-gap encoding barred from quoted κ |
| `Faithful` had no claim segmentation and no calibration; readers' blinding unstated; only the top κ tier carried | §2 deterministic segmentation; §6 second calibration read, blinding, r2 §6.6.4 tiering with CIs and positive-class agreement |
| the prompt was not production-shaped (`[chunk_id]` vs `[n]`, per-claim vs per-source citation, free-form abstention) and production's 8,000-char budget would cut a 16k context ~8× | §5 rewritten on `RagGenerator`'s formatting with two named deviations; the 8k default recorded as a finding |
| a reasoning generator under `max_tokens` 512 returns empty content | §4: thinking off by default via `extra_body`; reasoning-on as a descriptive column with its own budget |
| judge cost had no basis, counted one task, and named a transport that failed at 867 calls | §7: list and measured bases, two tasks, API key / Batches, judged-cell policy |
| CDS "pooled support ≥ 0.5" underived and undefined off the 308 labeled pairs | §2: support mass (r3 §10 item 4(a)), threshold sweep descriptive, labeled-pair denominator with the unlabeled-citation rate |
| `Correct` targeted the gold rather than the question (2/10 mis-targeted in #506 §6.1); `Abstains` conditioned on one location where D4 admits alternatives; undefined on CDS | §2 definitions restated; arm-invariant exclusion via the human read; union-gold condition with the alternative-passage bound; CDS abstention descriptive |
| §2 crossed delivery arms with index arms; §7's "8 arms" matched no list; budgets omitted; "served-path retrieval latency per configuration" contradicted "zero retrieval cost" | §2, §4, §7 cells enumerated; latency scoped to Stage 2's shortlist |

Non-blocking items (judge ≠ labeler rule; Scout-authored queries as a named confound; one judge
pinned, GPT as sensitivity; human read costed; read ordering; temperature-0 determinism; the
`Correct` ceiling in the window check; `pq_0241`; the `usage` field; the answer-token secondary)
are folded into the sections above. Not changed: §1's "decision ledger in the study report" is
not a document in this repository — cite its path or drop the sentence.
