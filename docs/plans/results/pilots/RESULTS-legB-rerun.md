# Leg B, re-run on the real LLM: the three §2.6 fixes, measured

*Run 2026-09-04 on `coconut`, repo `/home/wilke/Development/ragstack` at `d225cea`.
Predecessor: [`RESULTS-legBC-pilots.md`](RESULTS-legBC-pilots.md) §2 and §2.6 — this run
exists because that one concluded no LLM endpoint was available and fell back to Claude
subagents, so its yield figures were protocol-shakedown numbers rather than sizing inputs.
Brief: [`BRIEF-legBC-pilots.md`](BRIEF-legBC-pilots.md). Plan:
[`docs/plans/long-doc-judged-set.md`](../../../docs/plans/long-doc-judged-set.md) §7, §8.*

> ## Read this first
>
> **The generator was the real LLM. The three fixes were implemented and measured. Leg B
> passes, σ_d is smaller than feared, and the run turned up one finding that is bigger than
> Leg B.**
>
> 1. **Verdict: build Leg B at ~1,500 queries.** Yield is **65.0% (260/400)** automated and
>    ~52% after my own read of a random 30. σ_d on Leg B's own queries is **0.152**, so 1,500
>    queries resolve **δ = 0.017 under Holm** against config effects of **0.041–0.073** —
>    2.4× to 4.3× headroom. 1,000 would already meet plan §6's stated δ = 0.02.
> 2. **The feared σ_d inflation did not happen.** Round 1 said its 0.156 was "very likely a
>    lower bound for Leg B" because Leg B is near-binary known-item. Measured on Leg B's own
>    queries it is **0.152** at the comparable rung and **0.119** at the judged-only rung the
>    stage-1 grid actually runs on. **Expectation falsified**, and in the direction that makes
>    the plan cheaper, not more expensive.
> 3. **Fixes 1 and 3 worked, and the numbers resolve.** Median accepted query length went
>    **29 → 12 words** (δ80 2.65 w against an 18.7 w move) and compound multi-clause queries
>    **64.3% → 0%** (δ80 20.7% against 64.3%). The entity-stripping mechanism behind six of
>    round 1's ten bad accepts appears **zero times** in my read of 30. **But they worked as
>    prompt clauses, not as filters** — `too_long_20` fired on 0/400 and `not_specific` on
>    2/400. The abstract-answerability verifier is still carrying the entire protocol.
> 4. **Fix 2 bites very hard, and is not a strict improvement.** The positive section rule
>    rejects **1,491 of 2,655** candidate sections the deleted blocklist would have passed
>    (56.2%), and kills three of round 1's four defective sources by construction. It also
>    **admits 153 the blocklist rejected** — `Conclusion` and `Supporting information`
>    sections — and two of my six bad accepts came from exactly there.
> 5. **§8 check 4 passes on Leg B at its literal pre-registered pair and rung**, which no
>    leg had managed before: **100% of queries change their top-10 document set** between
>    `fixed_tok256/0` and `fixed_tok2048/0` (bar ≥25%), mean top-10 Jaccard 0.438, σ_d = 0.181.
>
> ### The finding that should not be filed under "Leg B"
>
> **Leg A says coarse chunks win. Leg B says fine chunks win, monotonically.** On the one
> contrast where *both* legs resolve — recall@100 on the pre-registered `tok256/0` vs
> `tok2048/0` pair — the signs are opposite and the intervals do not overlap: Leg A
> **+0.043** [+0.015, +0.076] for coarse, Leg B **−0.035** (t = −3.05) for fine. On nDCG@10
> only Leg B resolves (−0.041, t = −3.63, δ80 0.032), and that is because Leg A ran on
> **10 topics** — its point estimate is the *larger* one (+0.090) and simply cannot exclude
> zero. That contrast is a Leg B result and a Leg A non-result, not a head-to-head.
> Legs B and C exist to say whether Leg A's coarse-wins direction is a real retrieval effect
> or an artefact of document-level topical relevance. **On the evidence that resolves, they
> contradict it.**
>
> But Leg B's direction is not clean either, and I will not sell it as the answer. Fix 1
> requires the query to name a rare entity that occurs in the source section; document
> scores are a max-rollup over chunks; a small chunk carrying that entity is exactly what a
> max-rollup rewards. **The two legs answer different questions and give opposite answers.
> No config may be pruned on either direction until the study declares which query
> population it optimises for.** Plan §7's circularity rule stops a *chunker* grading its own
> homework; it has no clause for a *query construction* doing the same. It needs one.

---

## 0. The endpoint, the model, and the GPU-citizenship statement

The served model is **`Qwen/Qwen3.6-35B-A3B`** (`max_model_len` 131072) at
`http://mango.cels.anl.gov:8004/v1/chat/completions`. The repo's `docs/model-registry.md`
and `/rag/config/unified.models.json` are **stale** — they name
`RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic`, and sending that id fails.
[`mango.py`](mango.py) reads `/v1/models` at import and **asserts** the served id, so a
silent model swap under this harness becomes a loud error rather than a quiet change of
generator.

Three empirical facts about the server, encoded in the client:

* **Reasoning tokens are billed but not surfaced.** They appear in `completion_tokens` and
  in neither `content` nor `reasoning_content`. A two-word answer costs ~150 tokens; a
  summary of a 1,500-token section cost 3,085. An empty `content` with
  `finish_reason == "length"` therefore means *budget exhausted*, not refusal — the client
  retries once at double the budget and counts it. **19 of 419** query-writing calls hit
  that path at a 4,000-token budget.
* **Thinking is switchable** with `chat_template_kwargs={"enable_thinking": false}`. The
  same paraphrase went from 16.3 s / 3,085 tokens to **0.4 s / 67 tokens** with no quality
  loss I could see. It is **off** for the mechanical stage (paraphrase) and **on** for the
  two judgement stages (query writing, verification). That one switch is why stage A cost
  87 s and stage B cost 36 minutes for the same 400 items.
* **131k context was not the binding constraint, and I will not claim it bought quality.**
  A source section is 250–2,200 tokens. What the window actually bought is that
  **nothing had to be truncated at any stage** and that I could run **one item per LLM
  call** instead of the previous round's batches of 14 — so cross-item bleed is excluded by
  construction rather than by instruction. That is a real improvement in provenance, not in
  the queries.

| work | requests | prompt tok | completion tok | wall |
|---|---:|---:|---:|---:|
| stage A paraphrase (thinking **off**) | 400 | 495,458 | 38,356 | 87 s |
| stage B query writing (thinking on) | 419 | 187,455 | 1,278,431 | 2,189 s |
| stage C abstract-answerability verifier (thinking on) | 389 | 226,211 | 212,855 | 336 s |
| **mango total** | **1,208** | **909,124** | **1,529,642** | **~43.5 min** |
| section sampling, 3,000 articles → 400 sections | — | — | — | 26 s CPU |
| §7a oracle cross-check, 396 pairs → 3,248 window-pairs | 396 | — | — | 50 s |

* **0 request failures** across all 1,208 mango calls; 19 budget-doubling retries, 0 hard
  errors. Concurrency was capped at **4 in flight** throughout (`ss` confirmed exactly four
  established connections to `:8004` during the run) and every failure path backs off
  rather than retrying immediately.
* **GPUs 6 and 7 were never used and no endpoint was started on them** — verified 0 MiB
  before, during and after.
* The only local GPU services used are the pre-existing SFR embedding fleet `:9001`–`:9006`
  (≤2 in flight per endpoint, stage-1's unchanged policy) and the crossencoder sidecar
  `:50052` (≤4 in flight).
* **Zero store writes.** No Qdrant or Elasticsearch client is constructed anywhere in this
  harness; retrieval in §6 is exact brute-force cosine over in-memory embeddings.
  `:6333`/`:9200` and `:24041`/`:24043` are never contacted. Nothing was written under
  `/rag/`; `/rag/oa` was read-only.

### 0.1 The pre-registration check, applied to every number below

Carried forward from round 1: compute δ80 — the smallest effect the instrument can resolve
at 80% power — **before** reading a threshold, and say when the bar is unreachable. **Fifteen**
readings below are gated this way and **six of them fail the check and are written as
unresolved, not as nulls** — including two where the naive Wald floor would have said
"resolvable" because a proportion sat at p = 1.0 and its SE degenerated to zero. The
consolidated table is in §7.

---

## 1. What was implemented, fix by fix

All three live in [`legb2_rules.py`](legb2_rules.py), and every predicate is evaluated on
**every** item independently rather than first-match-wins — so a "hit rate" below is a real
rate. Round 1 reported a funnel in which all six rule filters read 0/70, which is ambiguous:
a filter reads zero both when it never applies and when an earlier filter shadowed it.

**Fix 2 — the positive section rule replaces the blocklist.** A section is eligible iff it
is a body unit, **≥ 250 SFR tokens**, **≤ 2,200**, **starts past absolute token 1,024**, and
contains **≥ 1 numeric result or method noun** (a regex over measurements/statistics, and a
lexicon of method vocabulary). The 16-word title blocklist is **deleted**; it is retained in
the file only to compute the crosstab below. Note the depth clause is now **absolute**:
round 1 used "past 25% of the *body*", which is what let `legb_036` in at token 554.

**Fix 3 — shape.** ≤ **20 words**, and no compound clause (a coordinator followed within a
short window by an auxiliary, an interrogative, or a subject pronoun). Asked for in the
prompt *and* enforced as a gate.

**Fix 1 — specificity, machine-checked two ways.**
*Intrinsic* (the gate): at least one of the query's own content terms must be rare in the
corpus (IDF ≥ 5.60 over the 10,000-doc OA title+abstract table, i.e. df ≤ 1%) **and**
present in the source section. *Anchor* (reported, never gated): the generator is required
to emit the entity it used as JSON, and that string is checked against query, section and
IDF. The anchor is deliberately not a gate — **a gate must not depend on the thing it is
auditing.**

The generator prompt also changed in one further way that fix 1 requires to be achievable:
the paraphrase stage is now told to **carry specific names over verbatim**. A summariser
that turns "quercetin" into "a flavonoid compound" makes a downstream specificity check
impossible to satisfy, and that generalisation is the documented mechanism behind six of
round 1's ten bad accepts.

### 1.1 Validation against round 1's own failures, before generating anything

| round-1 defect | source section | does the new rule kill it? |
|---|---|---|
| `legb_027` — a funding-acknowledgement query | `Conflict of Interest`, 285 tok | **yes** — `has_result_or_method` fails |
| `legb_036` — complexity truism, the depth violation | untitled, 314 tok @ **tok 554** | **yes** — `past_1024` fails |
| `legb_062` — presupposes its own paper | `Appendix`, 468 tok | **yes** — `has_result_or_method` fails |
| `legb_061` — bibliometric trivia | `COMPARISON TO OTHER AVAILABLE TOOLS` | **no — survives.** §2.6 predicted three of four; this is the fourth |
| the 6 "false-negative magnet" accepts | various | 2 die to fix 1, and **all 6** die to fix 3 (5 are >20 words, 4 compound) |
| shape, all 70 | — | fix 3 rejects **65 of 70** of round 1's queries |

---

## 2. Sampling — does the positive rule actually bite?

3,000 seeded PMC OA articles across all 256 shards; 2,655 candidate sections examined;
**400 source sections kept in 26 s**. Nothing imports a chunker (the §7 circularity rule);
generation units are the article's own top-level JATS `<sec>`s.

**Per-clause failures, over every candidate section, independently:**

| clause | sections it rejects | of 2,655 |
|---|---:|---:|
| `max_tokens` (≤2,200) | 700 | 26.4% |
| `past_1024` (**absolute** depth) | 663 | 25.0% |
| `min_tokens` (≥250) | 492 | 18.5% |
| `has_result_or_method` | 420 | 15.8% |
| `is_body_unit` | 6 | 0.2% |

**The crosstab that answers "does the new rule bite":**

| | legacy blocklist would **pass** | legacy blocklist would **block** |
|---|---:|---:|
| positive rule **accepts** | 720 | **153** |
| positive rule **rejects** | **1,491** | 291 |

Read both off-diagonal cells, not just the flattering one.

* **1,491 sections (56.2% of all candidates) are killed by the positive rule and would have
  been let through by the blocklist.** That is the answer to §2.6's question: yes, it bites,
  and it bites an order of magnitude harder than the thing it replaced. The blocklist
  rejected 444/2,655 = 16.7% of candidates; the positive rule rejects 1,782/2,655 = 67.1%.
* **153 sections (17.5% of everything the positive rule accepts) are admitted only because
  the blocklist was deleted.** In the 400 sampled, **83 (20.8%)** are of this kind, and they
  are overwhelmingly two titles: **`Supporting information` (37)** and **`Conclusion`/
  `Conclusions` (40)**. This is a real cost, not a rounding error, and §4 shows two of my six
  bad accepts came from it.

Realised source-section position of the 400 (**known by construction, not inferred**):
start token min **1,040**, median **4,955**, p90 12,359, max 40,772; **400/400 past token
1,024** by construction. Median section length 1,249 tokens, median host document 8,591
tokens. Class mix: discussion 136, other 128, methods 75, results 59, intro 2 (two
introductions that begin past absolute token 1,024 behind a long abstract — the absolute
depth rule admitting them is the rule working, not failing).

---

## 3. The funnel, and each filter's independent hit rate

| stage | n | share of sections |
|---|---:|---:|
| sections sampled (positive rule) | 400 | — |
| generator returned a query | 396 | 99.0% |
| passed **all** rule gates | 389 | 97.2% |
| + verifier says **not** abstract-answerable | **260** | **65.0%** |

| filter | fires on | rate | which fix |
|---|---:|---:|---|
| `empty` (generator declined rule 3) | 4 | 1.0% | fix 1, via the prompt |
| `title_answerable` (IDF ≥ 0.80 vs title) | 4 | 1.0% | legacy |
| `compound` | 2 | 0.5% | **fix 3** |
| `not_specific` | 2 | 0.5% | **fix 1** (gate) |
| `too_long_20` | **0** | 0.0% | **fix 3** |
| `names_document`, `too_long_60`, `too_short`, `not_a_question`, `duplicate` | 0 | 0.0% | legacy |
| `anchor_fail` | 25 | 6.2% | fix 1 (reported, **not** gated) |
| **abstract-answerability verifier** | **129 / 389** | **33.2%** | the only filter that carries weight |

**The honest reading of this table is not "the new filters work".** It is that **the prompt
did the work and the filters are a backstop that almost never fires**: `too_long_20` caught
nothing because the generator never wrote a 21-word query, and `not_specific` caught two
because the generator almost always named an entity. Round 1's rule filters read 0/70 and
that was reported as "zero discriminating power"; round 2's read 8/400 and the correct
conclusion is the same one. **The abstract-answerability verifier is still carrying the
entire protocol**, and §4 says that is now the binding constraint on quality.

The verifier and the leakage covariate agree independently, more strongly than in round 1:
verifier-**rejected** queries have median IDF overlap **0.768** against title+abstract,
**accepted** ones **0.496** (round 1: 0.475 / 0.301).

Accept rate by source-section class:

| source class | generated | accepted | accept rate |
|---|---:|---:|---:|
| other | 128 | 101 | **79%** |
| methods | 75 | 48 | 64% |
| discussion | 136 | 79 | 58% |
| results | 59 | 31 | 53% |
| intro | 2 | 1 | — |

Round 1's actionable finding ("down-weight Discussion, oversample the untitled/`other`
sections") **replicates**: `other` 79% vs discussion 58%, on 4–6× the sample.

**And a null that is not a null.** Sections the deleted blocklist would have rejected accept
at **69% (57/83)** versus **64% (203/317)** for the rest, and the verifier rejects
Conclusion-sourced queries at 32% versus 34% for everything else. I had expected Conclusion
sections — which recapitulate the abstract by definition — to be caught far more often.
**δ80 for that contrast is 22.4 pp against an observed 2.4 pp: the instrument is 9× too
coarse.** Reported as **unresolved**, never as evidence that Conclusion sections are safe.

---

## 4. My read of a random 30 — the part no metric reports

Seeded sample of 30 of the 260 accepted ([`legb2_read30.py`](legb2_read30.py), seed
20260904, drawn before reading). I read each query alongside its declared entity, source
section title/class/offset, the article title, the stage-A summary and the oracle argmax.

**Six of the thirty should not be in a judged set — 20.0%, against round 1's 23.8%.**
δ80 for that comparison is **27.5 pp against an observed 3.8 pp**: the two rounds' bad-accept
rates are **not distinguishable**, and I will not claim the read-level quality improved. What
*is* claimable is that the **taxonomy changed completely**, and the mechanisms round 1 asked
me to remove are gone.

**The six bad accepts, and why:**

| qid | query | what is wrong |
|---|---|---|
| `legb2_021` | *"How does P. gingivalis W83 LPS affect AGTR1 expression in HCAEC cells?"* | **abstract-answerable and the verifier missed it.** IDF overlap vs title+abstract **0.918**; the oracle's own argmax is the **Abstract**. |
| `legb2_093` | *"What classification accuracy does the HDLID-ECSOA technique achieve on the Edge-IIoT dataset?"* | abstract-answerable (accuracy figures are abstract material); overlap **0.885**. Source is a `Conclusion` section — admitted only because fix 2 deleted the blocklist. |
| `legb2_207` | *"How accurately does the FDNN framework detect DDoS attacks on the CICIoT 2023 dataset?"* | same failure — **and the source article is titled `RETRACTED ARTICLE: …`**. Nothing in the protocol filters retractions. Also a `Conclusion` section. |
| `legb2_136` | *"How frequently are ineffective obstetric procedures administered to patients in China?"* | a **false-negative magnet**: the declared "entity" is `obstetric procedures`, which is not one, and the article's own title asks nearly the same question. The intrinsic gate passed it on the term *ineffective* — rare in a biomedical abstract corpus, but not an entity. |
| `legb2_285` | *"What percentage of new genital herpes cases in resource-rich countries is caused by HSV-1?"* | **background epidemiology**, not a finding of this paper — and the source is a `review-article`. Hundreds of corpus documents state it. Scoring one document as the only relevant one manufactures false negatives. |
| `legb2_316` | *"How does p53 regulate vascular remodeling in pulmonary hypertension under hypoxic conditions?"* | the source article is a **review**; the section is a review subsection. Same magnet mechanism by a different route. |

**Seven more I would keep with reservations** (`legb2_052`, `102`, `114`, `192`, `258`,
`299`, `311`) — mostly borderline abstract-answerability (three have overlap ≥ 0.75) and one
(`legb2_258`) generated from a 251-token fragment that *poses a hypothesis* rather than
reporting a result. **The other seventeen are good**: specific, short, plausible as typed,
and their evidence provably sits deep — `legb2_100` (skeletal autapomorphies of *Vouivria
damparisensis*, token 7,750), `legb2_188` (ceftriaxone MIC in *L. monocytogenes* ATCC 19116,
token 5,297), `legb2_331` (NBQX concentration in an in-vitro ERG protocol, token 1,395).

### 4.1 What changed, and what did not

| round-1 defect | round 2 | resolvable? |
|---|---|---|
| median 29 words, max 45 | **median 12, mean 12.1, max 16** | **yes** — δ80 2.65 words vs an 18.7-word move |
| 64.3% of accepted compound multi-clause | **0 / 260** | **yes** — δ80 20.7% vs 64.3% |
| 6/10 bad accepts entity-stripped ("a flavonoid compound", "a web-based tool") | **0 / 30 in the read**; the machine proxy for a generic stand-in goes 4.8% → 0.0% | **no** — δ80 9.2% vs a 4.8% move. Direction only. |
| bad-accept rate on read | 23.8% → 20.0% | **no** — δ80 27.5% vs 3.8% |
| *(new)* verifier false negatives | ~3–4 of 30 read | the binding constraint now |
| *(new)* retracted articles in the source pool | **2 / 400 sampled, both accepted** | no filter exists |

**Three concrete changes Phase 1 should make, in order of how much they cost:**

1. **Add a retraction filter.** `<article-categories>`/title prefix `RETRACTED`. 0.5% of the
   sample, trivially cheap, and a retracted article in a judged set is indefensible.
2. **Exclude non-research articles as generation sources.** The JATS `article-type`
   attribute is right there in the front matter, and **35 of the 260 accepted queries
   (13.5%, Wilson 9.8–18.1%) come from a source that is not a `research-article`** — 30
   `review-article`, plus an editorial, an article-commentary, a discussion and two
   brief-reports. A review has no findings of its own, so a query generated from one is a
   survey query with a single document marked relevant: the false-negative magnet, by
   construction.

   **The power caveat, stated rather than buried.** Both non-research sources that fell in
   my read-30 were among my six bad accepts (2/2 vs 4/28 for research articles). A naive
   Wald δ80 on that contrast reads 19% against an 86% effect and would say "resolvable" —
   but at p = 1.0 with n = 2 the Wald SE is *degenerate*, which is the same class of
   mistake this process check exists to catch. The Wilson interval on 2/2 is **34–100%**
   and overlaps the research arm's. So: **the bad-rate contrast is NOT resolvable at n = 2.**
   What *is* measured is the 13.5% prevalence and the mechanism; the recommendation rests
   on those, not on the 2/2.
3. **Strengthen the verifier, do not add more rule filters.** The rule gates fire on 2% of
   queries; the verifier fires on 33% and is wrong perhaps one time in eight. Two cheap
   upgrades: show it the **title, abstract *and* the article's own section titles** (a query
   answerable from a section title is not a deep-evidence query), and ask it to rate
   *how many other papers could answer this*, which is the magnet failure it currently
   cannot see at all.

Note that (1) and (2) are **negative rules**, which is what §2.6 asked me to stop writing.
The honest lesson from the crosstab in §2 is not "positive rules good, blocklists bad" — it
is that the positive rule is **necessary and not sufficient**, and the residue needs a small
number of *principled* exclusions (retracted, review, front-matter) rather than a growing
list of section titles.

---

## 5. Position of evidence, the construction cross-check, and leakage

### 5.1 Realised depth of the 260 accepted sources — known by construction

| statistic | value |
|---|---|
| start token: min / p25 / median / p75 / max | **1,040** / 2,358 / **5,098** / 8,383 / 40,772 |
| past token 1,024 | **260 / 260 = 100%**, by construction |
| relative depth in document: p25 / median / p75 | 0.374 / **0.693** / 0.872 |
| source section length (median) | 1,232 tokens |
| host document length (median) | 8,778 tokens |

| start-token bucket | n | share |
|---|---:|---:|
| 1,024–2,048 | 56 | 21.5% |
| 2,048–4,096 | 56 | 21.5% |
| 4,096–8,192 | 82 | 31.5% |
| 8,192–16,384 | 59 | 22.7% |
| 16,384+ | 7 | 2.7% |

Round 1's realised median start was 5,396 with **69/70** past 1,024 — one violation, caused
by the relative-depth rule. The absolute rule removes that failure mode entirely.

### 5.2 The construction cross-check (crossencoder oracle vs the recorded source section)

396 (query, source-document) pairs, 3,248 window-pairs, 50 s. The oracle is
`bge-reranker-v2-m3` on `:50052` — the same cross-encoder the production pipeline reranks
with — so "argmax" below means where *that model* finds the best support, and the check is
not independent of any reranked retrieval result. If the construction is sound
the argmax should land on the section the query was generated from.

| set | n | argmax **==** source | within ±1 | argmax past tok 1,024 | argmax = lead unit |
|---|---:|---:|---:|---:|---:|
| all generated | 396 | 55.8% (50.9–60.6) | 69.2% | 78.0% | 14.9% |
| **accepted** | **260** | **66.9%** (61.0–72.4) | **79.2%** | **87.7%** | **5.4%** |
| rejected | 136 | 34.6% (27.1–42.9) | 50.0% | 59.6% | 33.1% |

Chance exact-hit rate (mean 1/n_units over the accepted set) is **14.8%**, so 66.9% is
4.5× chance. Round 1 read 59.5% on 42 accepted.

**Do not read 66.9% as an improvement over round 1's 59.5%.** δ80 for that comparison is
**8.2% against a 7.4% distance** — the instrument is marginally too coarse, and this is
reported as **unresolved**. What *is* resolvable, and is the more interesting half, is the
accepted-vs-rejected split within this run: the rejected queries' argmax collapses onto the
document lead (**33.1% vs 5.4%**), which is mechanically what "abstract-answerable" means
and is the pilot's best internal-validity signal.

### 5.3 §8 check 5 — the leakage bound, and a confound fix 3 created

| clause | measured (accepted, n=260) | bar | verdict |
|---|---|---|---|
| median query↔title+abstract IDF overlap below the `title_answerable` bar | **0.496** | < 0.80 | **PASS** |
| queries at or over 0.80 against the **title** | **0 / 260** | — | **PASS** |
| low-overlap tertile ≥ 300 queries | tertile of 260 = 86 | ≥300 | **projection only** |

**But read the first row next to round 1's 0.400 and notice it moved the wrong way.** That
is not increased leakage; it is a **mechanical artefact of fix 3**. IDF overlap is the share
of a query's IDF *mass* that also occurs in the title+abstract, so a 12-word query has fewer
terms and each shared term carries more of the mass. Cutting query length from 29 words to
12 inflates this covariate even with leakage held constant. **11.9% (31/260) of accepted
queries now sit at or above the 0.80 bar** against title+abstract, where round 1's maximum
was 0.646.

Two length-robust instruments are recorded alongside it and both say leakage is fine:

* **rare query terms *absent* from title+abstract**: median **2** per accepted query, and
  **83.8%** of accepted queries ask for ≥ 1 rare thing the front matter never names;
* **unweighted Jaccard** vs title+abstract: median **0.032**.

**Recommendation for the plan: check 5's covariate should be normalised for query length,
or restated on the absent-rare-terms count.** As written, it will drift with a prompt change
that has nothing to do with leakage — which is what just happened.

---

## 6. σ_d measured on Leg B's own queries — and the direction flip nobody has seen yet

Round 1's σ_d (0.156 dense / 0.173 reranked) came from **CDS topics, which average ~109
relevant documents each**, and averaging over 109 relevants smooths per-topic nDCG@10. Leg B
is near-binary known-item, so round 1 flagged its own figure as **very likely a lower bound
for Leg B** and called re-measuring it the highest-value unrun follow-up. It has now been
run, on Leg B's own queries.

**Two rungs, both measured, because the plan runs the stage-1 grid on the judged-only one**
("that is where the config contrasts live", §5) while the operating point is far out on the
ladder. Corpus for the ×11.5 rung = all 400 source articles + 4,600 seeded PMC OA
distractors drawn from the same distribution the sources were drawn from (a topically
*mismatched* pool would make every query trivial and bias σ_d **down**). Five stage-1 grid
cells, imported not re-declared. Exact brute-force cosine, no store.

| rung | docs | `tok256/0` | `tok512/0` | `tok1024/0` | `tok2048/0` | `tok512/25%` |
|---|---:|---:|---:|---:|---:|---:|
| ×0 (judged-only) — nDCG@10 | 400 | **0.9873** | 0.9762 | 0.9635 | 0.9589 | 0.9743 |
| ×0 — gold at rank 1 | 400 | 97.7% | 96.2% | 94.7% | 94.2% | 96.0% |
| ×11.5 — nDCG@10 | 5,000 | **0.9712** | 0.9480 | 0.9355 | 0.9198 | 0.9499 |
| ×11.5 — recall@100 | 5,000 | 0.9949 | 0.9874 | 0.9773 | 0.9697 | 0.9874 |

### 6.1 σ_d, and the falsified expectation

**σ_d, paired per-query SD over the 10 config pairs, accepted queries (n = 260):**

| metric | ×0 rung | ×11.5 rung | stage-1 / Leg A (CDS, 276 pairs) |
|---|---:|---:|---:|
| nDCG@10 | **0.119** (0.078–0.181) | **0.152** (0.098–0.214) | **0.156** |
| MRR@10 | 0.132 (0.098–0.194) | 0.178 (0.115–0.241) | 0.308 |
| recall@100 | 0.062 | 0.131 (0.088–0.183) | 0.035 |

**The expectation is falsified. Leg B's σ_d is not higher than Leg A's — it is the same or
lower** (0.152 vs 0.156 at the comparable rung; 0.119 at the rung the grid will actually run
on). The near-binary reasoning was right about the *shape* of the per-query metric and wrong
about its *variance*: because nDCG@10 sits at 0.92–0.99, most queries score identically
under both configs and contribute a paired difference of exactly zero. The variance comes
from the ~5% of queries that flip, not from the binariness.

**Three caveats travel with the number.** (i) It is a median over **10** config pairs from
**5** cells, all in the `token_window`/fixed family — stage 1's 0.156 is a median over 276
pairs from 24 cells across four chunker kinds, so the two are comparable in definition but
not in coverage. (ii) recall@100 at the ×0 rung is meaningless (100 of 400 documents) and is
shown only for completeness. (iii) n = 260 accepted queries; the paired SD itself carries
sampling error, though far less than round 1's n = 10 topics did.

**One property a reader must know before using any of these absolute numbers: Leg B is an
easy task.** nDCG@10 sits between **0.92 and 0.99** on every config at every rung tested, and
94–98% of queries put the gold document at rank 1. That is what a known-item query naming a
rare entity does against a dense retriever, and it is the direct consequence of fix 1. It did
**not** flatten the config contrast — check 4 passes at 100% and the size extremes separate
with |t| up to 5.5 — but it does mean the *headroom* on this leg is 1–8 nDCG points, so an
absolute score from Leg B is not comparable with one from Leg A (0.47–0.63), and a config
"improvement" of 0.02 means something very different on the two legs. The median gold-vs-best-
distractor score margin is **+0.108** at the judged-only rung, which is why adding 4,600
distractors cost only 2–4 nDCG points: displacing the gold document takes a lot of competition.

### 6.2 §8 check 4, read at its own pre-registered pair

> **check 4** | config contrast is live | `fixed_tok256/0` vs `fixed_tok2048/0`, judged-only
> rung | ≥25% of queries change their top-10 **doc** set; paired per-query deltas
> non-degenerate (σ_d > 0)

| clause | measured (×0 rung, n = 260 accepted) | bar | verdict |
|---|---|---|---|
| ≥25% of queries change their top-10 doc set | **260 / 260 = 100.0%** | ≥25% | **PASS** |
| mean top-10 Jaccard between the two configs | 0.438 (≈ 6 of 10 documents differ) | — | — |
| paired per-query deltas non-degenerate | σ_d = **0.181**, mean δ = **+0.0408** | σ_d > 0 | **PASS** |

**Check 4 passes on Leg B, at its literal pre-registered pair and rung, with no deviation.**
This is the first leg to clear it that way — Leg A's pass was measured with 12.5%-overlap
extremes rather than the literal `/0`, and on 10 queries.

### 6.3 The result that should change what the study does next

The five configs are **monotone in chunk size, and the direction is the opposite of Leg A's.**

| contrast: size 2048 − size 256 | Leg A (CDS, **n = 10 topics**) | Leg B ×0 (**n = 260**) | Leg B ×11.5 (**n = 260**) |
|---|---:|---:|---:|
| nDCG@10 | **+0.0904**, CI [−0.028, +0.220] | **−0.0408**, t = −3.63 | **−0.0733**, t = −5.53 |
| δ80 for it | 0.210 → **NOT resolvable** | 0.0315 → **RESOLVABLE** | 0.0371 → **RESOLVABLE** |
| recall@100 | **+0.0432**, CI [+0.015, +0.076] | −0.0038 → not resolvable | **−0.0346**, t = −3.05 |
| δ80 for it | → **RESOLVABLE** | 0.0108 → not resolvable | 0.0318 → **RESOLVABLE** |

**Be precise about why the nDCG@10 row splits the way it does: it is n, not noise.** Leg A's
contrast was measured on **10 topics** and its point estimate is *larger* in magnitude than
Leg B's (+0.090 vs −0.041) — it simply cannot exclude zero at that n. On its own, that row
is a Leg B result and a Leg A non-result, and it would be dishonest to present it as a
head-to-head.

**The head-to-head that does stand is recall@100, where both legs resolve and the signs are
opposite and non-overlapping:** Leg A says coarse is better by **+0.043** (CI [+0.015,
+0.076]); Leg B at the comparable rung says fine is better by **0.035** (t = −3.05). Two
resolvable contrasts on the same pre-registered config pair, pointing in opposite directions.

That is precisely the job Legs B and C were built for. §Why-these-two-legs-matter says Leg A's
coarse-wins direction is *provisional* because CDS relevance is document-level and topical,
and that Legs B and C — which control evidence position by construction — are the only thing
that can say whether it is a real retrieval effect or that bias. **On the one contrast where
both legs can speak, they contradict each other.**

**The honest counter-argument, which the plan must weigh and I cannot settle here.** Leg B's
fine-wins direction is not bias-free either. Fix 1 requires the query to name a rare entity
that occurs in the source section; document scores are a **max-rollup over chunks**; a small
chunk containing that entity is exactly what a max-rollup rewards. So Leg B is constructed to
favour localized matching in the same way Leg A is constructed to favour aboutness. The right
reading is not "Leg B is correct and Leg A is wrong" but:

> **The two legs answer different questions and give opposite answers, and the study has to
> declare which query population it is optimising for before it prunes a single config.**
> §7's circularity rule stops a chunker from grading its own homework; it does not stop a
> *query construction* from doing so, and nothing in the plan currently checks for that.

---

## 7. Every threshold, with its power floor computed first

δ80 is the smallest effect this instrument resolves at 80% power. A row whose floor exceeds
the distance it must travel is a **non-result**, and is written as one.

| # | statement | measured | bar / comparator | δ80 | distance | reachable? |
|---|---|---:|---:|---:|---:|---|
| 1 | Leg B yield vs a nominal 50% floor | **65.0%** (260/400) | 50% | 6.7% | 15.0% | **YES** |
| 2 | accepted: oracle (= production reranker) argmax past tok 1,024 (check 2) | **87.7%** (228/260) | ≥40% | 5.7% | 47.7% | **YES** |
| 3 | accepted: oracle argmax in abstract+intro (check 2) | **11.5%** (30/260) | ≤35% | 5.6% | 23.5% | **YES** |
| 4 | median query length, round 1 → round 2 | 30.8 → **12.1 words** | — | 2.65 w | 18.7 w | **YES** |
| 5 | compound queries among accepted, round 1 → 2 | 64.3% → **0.0%** | — | 20.7% | 64.3% | **YES** |
| 6 | check 4: top-10 doc set changes (256 vs 2048, ×0) | **100%** (260/260) | ≥25% | *degenerate SE* | 75.0% | **YES**, on the Wilson bound |
| 7 | check 4: size 2048 − 256 on nDCG@10, ×0 rung | **−0.0408** | ≠ 0 | 0.0315 | 0.0408 | **YES** |
| 8 | same contrast, ×11.5 rung | **−0.0733** | ≠ 0 | 0.0371 | 0.0733 | **YES** |
| 9 | oracle exact-hit vs round 1's 59.5% | 66.9% (174/260) | 59.5% | 8.2% | 7.4% | **NO — unresolved** |
| 10 | manual-read bad-accept rate, round 1 → 2 | 23.8% → 20.0% | — | 27.5% | 3.8% | **NO — unresolved** |
| 11 | generic entity stand-in (machine proxy), r1 → r2 | 4.8% → 0.0% | — | 9.2% | 4.8% | **NO — direction only** |
| 12 | verifier reject rate, `Conclusion`-sourced vs other | 32% vs 34% | — | 22.4% | 2.4% | **NO — unresolved** |
| 13 | bad-accept rate, non-research vs research source | 2/2 vs 4/28 | — | *degenerate* | — | **NO — see §4.1** |
| 14 | recall@100, size 2048 − 256, ×0 rung | −0.0038 | ≠ 0 | 0.0108 | 0.0038 | **NO — unresolved** |
| 15 | recall@100, size 2048 − 256, ×11.5 rung | **−0.0346** | ≠ 0 | 0.0318 | 0.0346 | **YES**, and it is the head-to-head with Leg A (§6.3) |

Row 6's Wald SE is zero at p = 1.0, the same degeneracy row 13 falls into — so it is read on
its **Wilson lower bound of 98.6%**, which clears the 25% bar without needing a δ80 at all.

Rows 9–14 are the ones this run may not claim. Row 13 in particular is the trap this check
exists for: a naive Wald δ80 on 2/2 vs 4/28 reads "resolvable" because the SE of a
proportion at p = 1.0 with n = 2 is **zero**. It is not resolvable; the Wilson interval on
2/2 is 34–100%.

Rows 1–3 are what round 1 could not do. Its yield read 60% against a 50% floor with δ80 =
16.4% — structurally unable to answer its own question. At n = 260 that floor is **6.7%**.

---

## 8. Sizing, and the verdict

### 8.1 What n buys, at the measured σ_d

δ90 = (z_α + z_β) · σ_d / √n. Holm's tightest step over 24 configs is z = 3.097.

| n accepted queries | σ_d | δ90 unadjusted | δ90 under Holm |
|---:|---:|---:|---:|
| 260 (this pilot) | 0.152 | 0.0306 | 0.0413 |
| 1,000 | 0.152 | 0.0156 | **0.0210** |
| **1,500** | **0.152** | **0.0127** | **0.0172** |
| 1,500 | 0.119 (×0 rung, where the grid runs) | 0.0100 | 0.0135 |
| 3,000 | 0.152 | 0.0090 | 0.0122 |

Set that against **the effects Leg B actually shows**: the size extremes differ by
**0.041 (×0)** to **0.073 (×11.5)** on nDCG@10. At 1,500 queries the resolvable δ under Holm
is **0.017** — **2.4× to 4.3× smaller than the effect**. At 1,000 it is 0.021, which meets
plan §6's stated δ = 0.02 almost exactly.

**So ~1,500 queries is not merely enough for Leg B, it is comfortable, and 1,000 would do.**
The pilot's own 260 already resolves the primary size contrast (|t| = 3.63 at the ×0 rung,
δ80 0.032 against a 0.041 effect).

### 8.2 What it costs to build 1,500

At the measured 65.0% automated yield, 1,500 accepted needs **~2,310 generated sections**
(~2,880 to leave headroom for the retraction/review exclusions in §4.1, which will remove a
further ~14%). Scaling this run linearly, at the same 4-in-flight politeness:

| stage | scaling | ~2,900 items |
|---|---|---|
| section sampling | 26 s / 400 | ~4 min CPU |
| stage A paraphrase (thinking off) | 87 s / 400 | ~11 min |
| stage B query writing (thinking on) | 2,189 s / 400 | **~4.4 h** at 4 in flight, ~1.5 h at 12 |
| stage C verifier | 336 s / 389 | ~42 min |
| §7a oracle cross-check | 50 s / 396 | ~6 min GPU |
| **LLM tokens** | 2.44 M / 400 | **~17.7 M** |

**Stage B's thinking budget is the whole cost.** If Phase 1 needs it cheaper, raise the
in-flight cap before touching the prompt — the reasoning is what produced 12-word, single-
clause, entity-named queries on the first try.

Fleet cost for the σ_d/check-4 measurements reported here: **277.7 M tokens, 28.5 min wall**
across both rungs, 38.5k requests, **0 retries**, ≤2 in flight per endpoint on `:9001`–`:9006`.

### 8.3 Verdict

**Build Leg B at ~1,500 queries. It clears every check it can be read against, and the
sizing has real headroom.** Specifically:

* **check 2 (evidence depth): PASS by construction and confirmed by the oracle** — 100% of
  accepted sources start past token 1,024, and the crossencoder independently puts 87.7% of
  argmaxes past token 1,024 with only 5.4% on the lead. Leg A's oracle read 55.4% / 3.0%.
* **check 4 (config contrast is live): PASS at the literal pre-registered pair and rung** —
  100% of queries change their top-10 doc set, σ_d = 0.181, mean δ = 0.041 and resolvable.
* **check 5 (leakage): PASS on the letter, but the covariate needs re-registering** — median
  overlap 0.496 < 0.80 and 0/260 title-answerable, yet the covariate moved *up* from round
  1's 0.400 purely because fix 3 shortened the queries. Normalise it for length, or restate
  it on the count of rare query terms the front matter never names (median 2, ≥1 for 83.8%).
* **σ_d: 0.152, not the higher figure that was feared.** 1,500 queries resolve δ = 0.017
  under Holm against effects of 0.041–0.073.

**Three things must be fixed before assembly, none of them expensive:**

1. **Exclude retracted articles** (2/400 sampled, both accepted — indefensible in a judged set).
2. **Exclude non-`research-article` sources** (35/260 accepted = 13.5%, Wilson 9.8–18.1%).
   Review sections are where the surviving false-negative magnets come from.
3. **Strengthen the verifier rather than adding rule filters.** The rule gates fire on 2% of
   queries and the verifier on 33%; roughly one in eight of its passes is wrong on my read.
   Show it the article's **section titles** as well as title+abstract, and ask it how many
   other papers could answer the query — the magnet failure it currently cannot see.

**And one finding that is bigger than Leg B and should not be filed under it.** Leg A says
coarse chunks win; Leg B says fine chunks win, monotonically. On recall@100 — the one
contrast where both legs resolve — the two point estimates have opposite signs and
non-overlapping intervals (§6.3). Both legs' directions are partly artefacts of how their
queries were built —
CDS relevance is topical aboutness, Leg B's queries are entity-anchored localized evidence,
and a max-rollup over chunks rewards the latter at small chunk sizes. **No config may be
pruned on Leg A's direction, and none should be pruned on Leg B's either, until the study
states which query population it is optimising for.** The plan's circularity rule (§7)
guards against a chunker grading its own homework; it has no clause for a *query
construction* doing the same, and this run is the evidence that it needs one.

---

## 9. Artefacts

| file | what |
|---|---|
| [`legb2_rules.py`](legb2_rules.py) | the three fixes as code; every predicate independent |
| [`legb2_sample.py`](legb2_sample.py) / [`legb2_sections.json`](legb2_sections.json) | positive-rule sampler, crosstab vs the deleted blocklist, 400 sources |
| [`mango.py`](mango.py) | the LLM client: served-model assertion, reasoning-budget retry, 4 in flight |
| [`legb2_gen.py`](legb2_gen.py) | stages A and B, one item per call, prompts hashed |
| [`legb2_screen.py`](legb2_screen.py) | all filters on all queries + the length-robust leakage covariate |
| [`legb2_verify.py`](legb2_verify.py) | stage C, round 1's verifier prompt **verbatim** for comparability |
| [`legb2_analyse.py`](legb2_analyse.py) / [`legb2_final.json`](legb2_final.json) | the join, the power floors, 260 accepted |
| [`legb2_sigma.py`](legb2_sigma.py) / [`legb2_sigma.json`](legb2_sigma.json) | σ_d at the ×11.5 rung |
| [`legb2_rungs.py`](legb2_rungs.py) / [`legb2_rung_x0.json`](legb2_rung_x0.json) | the judged-only rung and check 4 |
| [`legb2_read30.py`](legb2_read30.py) / [`legb2_read30.txt`](legb2_read30.txt) | the seeded 30 I read, as I read them |
| [`legb2_oracle_out.jsonl`](legb2_oracle_out.jsonl) | the §7a construction cross-check, 396 pairs |

### 9.1 Provenance

| item | value |
|---|---|
| generator / verifier model | `Qwen/Qwen3.6-35B-A3B`, asserted against `/v1/models` at import |
| paraphrase prompt sha256 | `f20b4a8cc3a5151d…` (changed from the committed g1 prompt in one way: names carried verbatim — see §1) |
| query prompt sha256 | `ea5b1f368c1e3926…` (new; fixes 1 and 3 asked for here, enforced in `legb2_screen.py`) |
| verifier prompt sha256 | `055a1997ab1810a1…` — **byte-identical to round 1's**, so the 33.2% vs 40.0% rejection rates are comparable |
| IDF table | 10,000 seeded PMC OA title+abstracts, sha256 `90759aec0119046e…`, 93,347 terms |
| specificity threshold | IDF ≥ 5.60 (df ≤ 1% of the IDF corpus) |
| seeds | sampler / read-30 `20260904`; distractors `20260911` (×11.5) and `20260915` (×0) |
| repo | `/home/wilke/Development/ragstack` @ `d225cea`, pinned past the `/rag/repos` editable-install meta-path finder |
