# Phase-0 items 3–6: the §7a oracle, the Leg B pilot, the Leg C pilot, and σ_d

*Run 2026-09-04 on `coconut`, repo `/home/wilke/Development/ragstack` at `d225cea`.
Brief: [`BRIEF-legBC-pilots.md`](BRIEF-legBC-pilots.md). Plan:
[`docs/plans/long-doc-judged-set.md`](../../../docs/plans/long-doc-judged-set.md).
Predecessor: [`../stage1/RESULTS-stage1-legA.md`](../stage1/RESULTS-stage1-legA.md).*

> ## Read this first
>
> **Four things landed, and two of them are negatives that matter more than the passes.**
>
> 1. **Check 2 now has a number, on 90 CDS topics, and it PASSES with room.** 55.4% of judged
>    pairs have their best-supporting section starting past token 1,024 (bar ≥40%); 11.9% have
>    it in abstract+intro (bar ≤35%); all six section classes are represented. This is the
>    first time the histogram the whole design is built around has existed.
> 2. **Leg C's "true" resolvability is NOT higher than the pmid-only floor — it is the same
>    number.** Resolving via pmid **+ pmcid + doi** gives **11.86%** of pub-id-bearing refs
>    against the recorded 11.8% pmid-only floor. The three keys are ~99.9% redundant. The
>    plan's stated expectation ("resolving via all three raises this") is **falsified**. Leg C
>    survives anyway, on volume, not on rate.
> 3. **Leg B's real yield is 46%, not the 60% the pipeline reports.** The automated funnel
>    accepts 42/70; reading all 70 myself removes 10 more that the filters cannot see. The
>    single most useful output of this pilot is the list of *why* those 10 got through.
> 4. **σ_d is measured, and it is at the top of the plan's assumed band, not the middle.**
>    Median paired sd for nDCG@10 across all 276 stage-1 config pairs is **0.156** dense /
>    **0.173** reranked. At 0.156, 1,000 Leg B queries resolve δ = 0.022 under Holm, not the
>    0.02 §6 claims — and this figure is very likely a **lower** bound for Leg B.
>
> **The generator was not Scout.** No LLM endpoint exists on this host right now: GPUs 0–5 are
> full of the SFR fleet and GPUs 6–7 are reserved, so a Llama-4-Scout server could not be
> started. Leg B's three LLM stages ran on Claude subagents instead (§Leg B.1). Yield and
> filter hit-rates are therefore **protocol-shakedown numbers, not Phase-1 sizing inputs**, and
> the asymmetry runs one way: a *stronger* generator producing bad queries is strong evidence
> the protocol is broken; producing good ones is weak evidence it works.

---

## 0. Cost, and the GPU-citizenship statement

| leg | work | wall | notes |
|---|---|---:|---|
| §7a oracle, Leg A | 2,161 pairs → **21,735** crossencoder window-pairs | 187 s | 0 retries |
| Leg C oracle | 400 pairs → 3,294 window-pairs | 51 s | 0 retries |
| Leg B cross-check oracle | 70 pairs → 549 window-pairs | 8 s | 0 retries |
| probe | 318 window-pairs | 3 s | |
| **crossencoder total** | **25,896 window-pairs** | **~4.2 min** | ≈ **0.07 GPU-h** on one shared GPU |
| Leg C resolvability scan | 1.44 M-line manifest index + 1,200 articles, 64,606 refs | 2.9 s + ~2 min index | CPU |
| Leg B section sampling / IDF | 600 + 10,000 article parses | ~3.5 min | CPU |
| S3 | 1,734 judged articles fetched | 11 s | `pmc-oa-opendata` |
| LLM (Claude subagents) | 15 calls, 3 isolated stages × 5 batches | ~6 min | **~779k subagent tokens** |

- **No embedding endpoint was called at all.** `:9001`–`:9006` were untouched by this work.
- **GPUs 6 and 7 were never used** and no endpoint was started — verified 0 MiB before and
  after.
- The only GPU service used is the existing crossencoder sidecar `:50052`
  (`bge-reranker-v2-m3`, `MAX_LENGTH=4096`, pinned to GPU 0), at ≤4 in-flight requests.
- **Zero store writes, and no store client exists in this harness.** `grep` over every file in
  `pilots/` finds no Qdrant/Elasticsearch import, no `:24041`/`:24043`, no `:6333`/`:9200`
  outside a docstring. Nothing was written under `/rag/`; `/rag/oa` was read-only.
- Projection was well under the ~1 GPU-hour stop-line: total GPU time was ~4 minutes.

### 0.1 The pre-registration check the brief demanded

Stage 1's lesson — *compute a contrast's power floor before reading it against a bar* — was
applied to every threshold here ([`power_checks.py`](power_checks.py)):

| threshold | measured | bar | δ80 (80%-power floor) | distance to bar | bar reachable? |
|---|---:|---:|---:|---:|---|
| check 2, past-1024, **topics as the unit** | 55.8% | 40% | **3.9%** | +15.8% | **yes** |
| check 2, head share | 12.0% | 35% | **2.1%** | −23.0% | **yes** |
| Leg C survival, n=400 | 57.2% | 50% | 6.9% | +7.3% | yes, barely |
| Leg C survival, **50-pair pilot alone** | 64.0% | 50% | **19.0%** | +14.0% | **no** |
| Leg B yield, n=70 | 60.0% | 50% | **16.4%** | +10.0% | **no** |
| "Methods-located citances are deeper" | +5.1 pp | — | **21.4 pp** | — | **no — 4.2× too coarse** |

Two consequences taken, not hedged: the 50-citance pilot was **extended to 400** for the
survival rate, because at n=50 it could not have answered its own question; and the
Methods-oversampling contrast is reported as **unresolved**, never as a null — it is the exact
shape of stage 1's difference-of-differences trap.

---

## 1. §7a — the section-level oracle, and what checks 2 and 5 now read

### 1.1 What was run

Every judged (topic, relevant-doc) pair is split into **structural units** — the
title+abstract lead plus each **top-level `<body>` `<sec>`** — never a chunker's cuts (the §7
circularity rule). Each unit is scored against the topic's `summary` query with the
crossencoder, and the argmax unit's SFR-token start offset, class and margin over the best
head unit are recorded.

Three implementation points that are load-bearing:

* **The sample was widened from the 10-topic pilot to all 90 CDS topics.** Pairs inside a topic
  share a query, so a 10-topic sample has an effective n of 10 for anything the query drives.
  Up to 25 relevants per topic were drawn seeded; 1,734 documents were fetched from S3 in 11 s
  at **98.9% fetchability** (independently reproducing step 1's 98.5%). Final sample:
  **2,161 pairs, 2,095 distinct documents, 90 distinct topics.**
* **Long units are windowed client-side and max-pooled**, not left to the sidecar. The sidecar
  truncates at 4,096 tokens; 3.0% of units exceed that, and silent truncation would bias the
  argmax *toward evidence that sits early* — precisely the quantity being measured.
* **The reconstructed document text is byte-identical to the text stage 1 indexed**
  (`title \n\n abstract \n\n body`), verified on a 60-document sample with zero mismatches, so
  a unit's token offset is in the same coordinate system as the grid's chunks.

### 1.2 The position-of-evidence histogram

**2,161 pairs, 90 topics, 2,095 documents.** Bootstrap CIs are over **topics**, the clustering
unit; the pooled Wilson interval is shown too and is the over-confident one.

| statement | pairs | pooled | Wilson 95% (pairs) | topic-mean | bootstrap 95% (topics) |
|---|---:|---:|---|---:|---|
| argmax **starts** past token 1,024 | 1,197 | **55.4%** | 53.3–57.5% | 55.8% | **53.0–58.4%** |
| argmax **midpoint** past token 1,024 | 1,700 | 78.7% | 76.9–80.3% | 78.8% | 76.8–80.8% |
| argmax in **abstract+intro** | 258 | **11.9%** | 10.6–13.4% | 12.0% | **10.6–13.5%** |
| argmax is unit 0 (the lead) | 64 | 3.0% | 2.3–3.8% | 3.1% | 2.3–3.8% |
| argmax past 50% of the document | 637 | 29.5% | 27.6–31.4% | 29.6% | 27.4–31.8% |

Start-token buckets:

| bucket | pairs | share |
|---|---:|---:|
| 0–512 | 555 | 25.7% |
| 512–1,024 | 408 | 18.9% |
| 1,024–2,048 | 570 | 26.4% |
| 2,048–4,096 | 431 | 19.9% |
| 4,096–8,192 | 175 | 8.1% |
| 8,192+ | 22 | 1.0% |

Depth deciles (argmax relative character start):

```
  0– 10% |  389 | 18.0% | ##########################################################
 10– 20% |  401 | 18.6% | ############################################################
 20– 30% |  293 | 13.6% | ############################################
 30– 40% |  183 |  8.5% | ###########################
 40– 50% |  258 | 11.9% | #######################################
 50– 60% |  268 | 12.4% | ########################################
 60– 70% |  212 |  9.8% | ################################
 70– 80% |   92 |  4.3% | ##############
 80– 90% |   24 |  1.1% | ####
 90–100% |   41 |  1.9% | ######
```

Argmax section class, and — the honest denominator — the win rate *where that class exists*:

| class | wins | share of pairs | present in | win rate where present |
|---|---:|---:|---:|---:|
| abstract | 66 | 3.1% | 100.0% | 3.1% |
| intro | 192 | 8.9% | 76.3% | 11.7% |
| methods | 278 | 12.9% | 39.8% | **32.3%** |
| results | 155 | 7.2% | 37.7% | 19.0% |
| discussion | 625 | 28.9% | 83.0% | **34.9%** |
| other | 845 | 39.1% | 78.5% | **49.8%** |

The margin over the best head unit: median **+0.163**, p25 +0.032, p90 +0.593; **88.0%** of
pairs have a non-head unit that strictly beats *every* abstract/intro unit.

Per-topic spread of the past-1024 rate: min 20%, median 58%, max 88%; **8 of 90 topics** sit
below 40% individually. Grade makes no difference (grade 1: 54.3% / 11.6% head; grade 2:
56.6% / 12.3%).

### 1.3 What check 2 now reads

| clause | bar | measured | verdict |
|---|---|---|---|
| ≥40% of pairs have argmax past the first 1,024 tokens | ≥40% | **55.4%**, topic-clustered CI 53.0–58.4% | **PASS** |
| ≤35% argmax in abstract+intro | ≤35% | **11.9%**, CI 10.6–13.5% | **PASS**, by 23 pp |
| every major section class represented | — | all six classes win ≥66 pairs | **PASS** |

**Check 2: PASS on Leg A**, with the power floor (3.9%) five times smaller than the distance
to the bar. Two caveats travel with it:

* The oracle *is* `bge-reranker-v2-m3` — the same model that gates the production pipeline.
  §7a accepts that deliberately ("evidence this model cannot see deep is evidence the product
  cannot use"), but it means check 2 is a statement about what the reranker can find, not
  about ground truth. §7b (the model-free control) has already run and reads −0.006 recall@100.
* The strict and lenient readings of "past the first 1,024 tokens" differ a lot — 55.4% vs
  78.7%. The table above uses the **strict** one (the section *begins* past 1,024). The plan
  should record which it meant; both clear the bar.

A third observation the plan did not anticipate: `other` is the single largest winning class
at 39.1%, and it is dominated by *untitled* body leads and **case-report / case-presentation**
sections — which is what a clinical-decision-support corpus would be expected to contain. It
is not a parser artefact; the classifier simply has no CDS-shaped class for it.

### 1.4 What check 5 now reads

Check 5 is a **Leg B/C** check, so it takes its numbers from §2 and §3 below:

| clause | Leg B (n=70) | Leg C (n=400) | verdict |
|---|---|---|---|
| median query↔title+abstract IDF overlap below the `title_answerable` bar (0.80) | median **0.400**, max 0.646 among accepted | median **0.247**, p90 0.520 | **PASS on both** |
| queries at or over the 0.80 bar against the **title** | **0 / 70** | **0 / 400** | **PASS** |
| low-overlap tertile ≥300 queries on Leg B | tertile of 70 = 23 | — | **PROJECTION only** |

The last clause **cannot be measured at pilot scale** and is reported as a projection: scaling
the observed distribution to 1,000 accepted queries puts ~333 in the low tertile, conditional
on the distribution holding. Recording it as a "pass" would be manufacturing a result.

**Check 5: the leakage bound PASSES on both legs; its sizing clause is untested.**

---

## 2. Leg B pilot — 50 (in fact 70) generated queries

### 2.1 The deviation that has to be stated first: the generator

The plan budgets Scout (`RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic`, the registry's
`mango-scout`). **No LLM endpoint is running on this host**, GPUs 0–5 carry the SFR fleet with
~5–13 GB free each, and GPUs 6–7 are reserved — a Scout server could not be started inside the
constraints. The three LLM stages therefore ran on **Claude subagents**, one isolated context
per stage, batched 14 items per call:

| stage | model | sees | must not see |
|---|---|---|---|
| A paraphrase | sonnet | the section text only | the title, the abstract, the article |
| B query-writing | sonnet | the stage-A summary only | the section text, the title, the abstract |
| C abstract-answerability verifier | opus | the query + title + abstract | the section text |

Isolation is by construction: each stage is a separate agent with its own context and an
explicit instruction not to open any other file. Prompt hashes are in
[`legb_final.json`](legb_final.json) (paraphrase/query/critic taken **verbatim** from the
committed `g1_make_queries.py`; verifier prompt written for this run, sha256
`055a1997ab1810a1…`, text in [`verifier_prompt.txt`](verifier_prompt.txt)).

**Read every yield number below as a protocol shakedown, not as a Scout forecast.** Scout will
almost certainly yield worse; the useful transfer is the *shape* of the funnel and the failure
taxonomy in §2.5, not the percentages.

### 2.2 Sampling — position by construction, and never from a chunker

600 articles were drawn seeded across 254 of the 256 corpus shards. Nothing in the sampler
imports a chunker; generation units are the article's own top-level JATS `<sec>`s. Eligibility:
past the first 25% of the **body**, not abstract/intro/conclusion/front-matter-titled, 200–2,200
SFR tokens, article has ≥6 structural units.

| stage | count |
|---|---:|
| articles examined | 145 |
| — rejected: fewer than 6 structural units | 52 |
| — rejected: no eligible deep section | 22 |
| — rejected: no body | 1 |
| **articles yielding a source section** | **70 (48.3%)** |

Realised position of the 70 source sections: median start token **5,396**; **69 / 70** start
past token 1,024; median section length 1,354 tokens. Class mix: discussion 28, other 22,
results 14, methods 6.

### 2.3 The funnel

| filter | rejected | rate | note |
|---|---:|---:|---|
| `names_document` | 0 | 0% | committed `g1_make_queries.screen_query`, imported not reimplemented |
| `too_long` (>60 words) | 0 | 0% | |
| `too_short` (<4 content terms) | 0 | 0% | |
| `not_a_question` | 0 | 0% | |
| `title_answerable` (IDF ≥0.80 vs title) | 0 | 0% | IDF built from 10,000 sampled OA title+abstracts |
| `duplicate` | 0 | 0% | |
| **all rule filters** | **0 / 70** | **0%** | |
| **abstract-answerability verifier** | **28 / 70** | **40.0%** | the only filter that fired |
| **automated yield** | **42 / 70** | **60.0%** | |
| my read of all 70 removes | **10 more** | | §2.5 |
| **effective yield** | **32 / 70** | **45.7%** | |

**The rule filters have zero discriminating power against this generator.** That is itself a
finding: they were calibrated against a weaker one, and with a strong generator the
*abstract-answerability pass is carrying the entire protocol*. If Phase 1 runs on Scout the
rule filters will start firing again — but the pilot cannot tell you at what rate.

The verifier and the leakage covariate **agree independently**, which is the pilot's best
internal validity signal: rejected queries have median IDF overlap **0.475** against
title+abstract, accepted ones **0.301**.

Rejection is strongly section-class-dependent:

| source class | generated | accepted | accept rate |
|---|---:|---:|---:|
| other | 22 | 19 | **86%** |
| discussion | 28 | 14 | **50%** |
| results | 14 | 7 | 50% |
| methods | 6 | 2 | 33% |

Discussion sections recapitulate the abstract, so half of what they generate is
abstract-answerable. **Actionable for Phase 1: down-weight Discussion in deep-section
sampling, and oversample the untitled/domain-specific `other` sections that yield best.**

### 2.4 The construction cross-check (free, and it works)

The oracle was run over each query against **its own source document**. If the construction is
sound, the argmax should land on the recorded source section.

| set | argmax == source section | within ±1 section | argmax past tok 1,024 | argmax = lead unit |
|---|---:|---:|---:|---:|
| all 70 | 44.3% (Wilson 33.2–55.9) | 54.3% | 57.1% | 22.9% |
| **the 42 accepted** | **59.5%** (44.5–73.0) | **69.0%** | **73.8%** | **11.9%** |

Two things follow. The **construction and the oracle corroborate each other** on the accepted
set — a 59.5% exact hit against a chance rate of roughly 1/6 units. And the rejected queries
behave exactly as the theory says they should: their argmax collapses onto the lead
(22.9% → 11.9% once they are removed), which is what "abstract-answerable" means mechanically.

### 2.5 My read of all 70 — the part no metric reports

I read every query alongside its source section title, class, offset and the article title.
Ten of the 42 the pipeline accepted should not be in a judged set. They fall into two kinds:

**Defective (4) — the query should never have been generated.**

| qid | source section | what went wrong |
|---|---|---|
| `legb_027` | `Conflict of Interest` (285 tok) | produced *"What German and Luxembourgish funding sources … support biomedical research?"*. My section-eligibility blocklist has `funding`, `competing interest` and `consent` but **not `conflict of interest`**. A one-word gap in a hand-written list put a funding-acknowledgement query into the set. |
| `legb_036` | untitled 314-token fragment | *"Why does exhaustively enumerating all stable states of a large biological network become computationally infeasible…?"* — a complexity truism that a very large fraction of the corpus "answers". This is also the **single depth violation**: it starts at token 554, because "past 25% of the body" is not the same constraint as "past 1,024 tokens". |
| `legb_061` | `COMPARISON TO OTHER AVAILABLE TOOLS` | *"Which … tools dominate usage as measured by scholarly citation counts?"* — bibliometric trivia, time-varying, and the "right" answer changes yearly. |
| `legb_062` | `Appendix` | *"why is the seed node used to build a candidate complex core always retained…?"* — a within-paper design-rationale question that **presupposes the paper it is supposed to retrieve**. |

**False-negative magnets (6) — well-formed, but so generic that dozens of corpus documents
answer them, so scoring the source document as the only relevant one manufactures false
negatives.** `legb_018`, `legb_022` (*"a web-based tool for identifying disordered protein
regions"* — names nothing), `legb_025`, `legb_035`, `legb_042`, `legb_055`. The recurring
mechanism is the query-writing prompt's rule *"the question must stand on its own"*, which the
generator satisfies by **stripping the specific entity** — "a flavonoid compound", "a
web-based tool" — turning a known-item query into a survey query. `legb_054` is the same
failure and the verifier happened to catch it; the other six it did not.

**A third defect that affects all 70 and no filter sees: shape.** Median query length is
**29 words** (max 45), and **35 of 70** are compound multi-clause questions joined by "and"
— e.g. *"Can preexisting CD4+ T cell memory … cross-react with SARS-CoV-2 through shared
epitopes, **and** does SARS-CoV-2 infection boost this cross-reactive response rather than
causing original antigenic sin?"*. Real literature-search queries are far shorter. Compound
queries also carry more IDF mass, which makes them *easier* than a realistic query and
inflates every absolute metric. This is a prompt problem, not a corpus problem: the two-pass
design asks for one question over a 2–3-sentence summary, and the generator dutifully packs
the whole summary into it.

**The other 32 are good.** They are specific, standalone, plausible as typed queries, and their
evidence provably sits deep — e.g. `legb_002` (a Methods-derived pipeline question at token
9,634), `legb_057` (Fkh1 self-association maintaining Cdc45 loading, token 5,540),
`legb_065` (skin sympathetic nerve activity + deep network for autonomic dysreflexia, token
1,996). That set is exactly what the plan wanted Leg B to be.

### 2.6 Three concrete protocol changes Phase 1 should make

1. **Add a "specificity" filter or prompt clause.** Require the query to name at least one
   specific entity (gene, organism, compound, tool, cohort). Six of the ten bad accepts are
   entity-stripped. This is cheap and machine-checkable against the source section's terms.
2. **Replace the hand-written section blocklist with a positive rule**: require the source
   section to be ≥ *N* tokens **and** start past token 1,024 **and** carry ≥1 numeric result
   or method noun. `legb_027`, `legb_036` and `legb_062` all die to that rule; the blocklist
   approach will keep leaking one-word gaps forever.
3. **Ask for a short question.** Cap at ~20 words and forbid compound clauses in the prompt.

---

## 3. Leg C pilot — citances

### 3.1 The true resolvability rate, with denominators

The recorded 11.8% is a pmid-only, one-shard, 60-document floor. This scan: **1,200 citing
articles seeded across all 256 shards**, joined against a full index of the manifest
(1,439,753 pmcids, 1,398k pmids, 1,412k normalised DOIs).

| denominator | refs | resolved | rate |
|---|---:|---:|---:|
| **all `<ref>` elements** | 64,606 | 6,767 | **10.47%** |
| refs carrying ≥1 usable `<pub-id>` (88.3% of all) | 57,041 | 6,767 | **11.86%** |
| refs carrying a **pmid** | 51,994 | 6,759 | 13.00% |
| refs carrying a **doi** | 55,863 | 6,721 | 12.03% |
| refs carrying a **pmcid** | 26,253 | 6,757 | **25.74%** |

**The union over all three keys is 6,767. Pmid alone gets 6,759 — eight fewer.** The keys are
99.9% redundant: a reference that resolves into our corpus almost always carries all three
ids, because the same PMC records that populate our corpus are the ones that populate the
reference's pub-id block. The pmcid *rate* looks better (25.7%) only because carrying a PMC id
is itself a proxy for being in PMC OA — it is a selected subpopulation, not a better key.

**So the plan's §3 note — "the manifest carries `doi_xml`, so resolving via all three raises
this" — is falsified.** Build the multi-key resolver anyway (it costs nothing and it is more
robust to individual missing ids), but do not budget any extra yield for it.

Volume, which is what actually matters:

| quantity | measured |
|---|---:|
| refs per citing article | median 48 |
| in-corpus resolved refs per citing article | median **3**, mean 5.64 |
| citing articles with ≥1 resolved ref | 965 / 1,200 = **80.4%** |
| citing articles with ≥5 | 499 / 1,200 = 41.6% |

### 3.2 Citance mining

Of the 6,767 resolved refs, **89.0%** could be anchored to at least one in-text
`<xref ref-type="bibr">` and yield a usable sentence:

| stage | count |
|---|---:|
| in-text anchors found for resolved refs | 10,439 |
| — dropped: duplicate anchor (same ref cited repeatedly) | 3,899 |
| — dropped: sentence <8 words | 175 |
| — dropped: sentence >60 words | 341 |
| **usable (citance, cited-doc) pairs** | **6,024** |

From **1,200** citing articles. Scaled to the corpus's 1,439,753 articles that is on the order
of **7 million candidate pairs**; the plan needs ~300. Volume is not a constraint on Leg C
under any assumption.

Citance location in the *citing* paper: intro 1,988, other 1,272, discussion 1,247, methods
950, results 563.

### 3.3 Position-filter survival

The §7a oracle was run over 400 (citance, cited-doc) pairs — **the 50-pair pilot was extended
to 400 because at n=50 the survival rate could not be distinguished from a 50% bar** (δ80 =
19.0% against a 14.0% distance).

| filter | k/n | rate | Wilson 95% |
|---|---:|---:|---|
| **argmax NOT in abstract+intro** (the plan's position filter) | 229/400 | **57.2%** | 52.4–62.0% |
| argmax starts past token 1,024 | 193/400 | 48.2% | 43.4–53.1% |
| both | 192/400 | 48.0% | 43.1–52.9% |
| argmax is the lead unit | 59/400 | 14.8% | 11.6–18.6% |
| *(the 50-pair pilot alone, for reference)* | 32/50 | 64.0% | 50.1–75.9% |

**Position-filter survival is 57.2%.** Combined with §3.1–3.2 that means roughly
`0.1186 × 0.89 × 0.572 ≈ 6.0%` of **pub-id-bearing** references become a deep-evidence Leg C
pair — equivalently **5.3 per 100 references of any kind**, or, measured directly,
`6,024 / 1,200 × 0.572 ≈ **2.9 surviving pairs per citing article**`. Three hundred filtered
citances need ~105 citing articles' worth of mining. Trivially affordable.

The cited documents are properly long: median **9,330** tokens, median 6 structural units, only
2 of 400 under 1,024 tokens.

Argmax class on the cited papers: intro 112, discussion 102, other 69, **abstract 59**, results
29, methods 29. The abstract+intro concentration (42.8%) is much higher than on Leg A (11.9%)
— which is exactly the §Option-2 threat 2 the plan named, and exactly why the filter exists.

**The Methods-oversampling hypothesis is UNRESOLVED, not confirmed and not refuted.**
Survival by citing-section class: results 65.0% (n=40), methods 61.3% (n=62), intro 56.2%
(n=121), discussion 55.1% (n=89), other 54.5% (n=88). The methods-vs-intro difference is
+5.1 pp against a δ80 of **21.4 pp** — the instrument's floor is 4.2× the observed effect. This
is the stage-1 difference-of-differences trap in miniature and it is reported as unresolved.

### 3.4 Two Leg C defects that need a decision before Phase 1

1. **`g1`'s `not_a_question` filter destroys Leg C.** Running the committed `screen_query`
   over the 400 citances rejects **372 of 400 as `not_a_question`** — because a citance is a
   *declarative sentence*, which is what a citance is. Only 4 pass all rules. The plan says to
   apply "`g1_make_queries`-style filters including `title_answerable`"; that must be
   re-registered as **`title_answerable` + `names_document` only**, or the citances must be
   converted to questions (which reintroduces an LLM and its biases into the one leg whose
   selling point is that no LLM wrote it). `names_document` does real work here — it fires on
   **23 / 400 (5.8%)**, catching citances that say "the authors", "et al." or point at a figure.
2. **62% of citances co-cite ≥2 references in the same sentence.** The sentence supports every
   one of them, so single-cited-doc qrels are *systematically* incomplete on Leg C, not
   incidentally. The plan already anticipates "multi-relevant qrels where one citance cites
   several in-corpus refs" — this measures how often that path is needed: **most of the time**.
   Treat multi-relevant as the default shape of Leg C's qrels, not an enhancement.

Leakage on Leg C is low: median IDF overlap against the cited paper's title+abstract **0.247**
(p90 0.520, one pathological 1.000), against the title alone **0.076**, and **0 of 400** reach
the 0.80 `title_answerable` bar. Survival is *higher* in the low-overlap tertile
(**66.4%**, n=134) than overall — the leakage covariate and the position filter are pulling in
the same direction, which is the reassuring sign.

---

## 4. Empirical σ_d, and what it does to the plan's sizing

Stage 1 persisted per-topic arrays for 24 configs × 4 conditions × 3 metrics, so σ_d — the SD
of **per-query differences** between two configs — can be read off instead of assumed. Computed
over all **276** config pairs on the 10 CDS topics ([`sigma_d.py`](sigma_d.py)):

| metric | arm | min | p25 | **median** | p75 | p90 | max |
|---|---|---:|---:|---:|---:|---:|---:|
| nDCG@10 | dense | 0.021 | 0.122 | **0.156** | 0.194 | 0.231 | 0.298 |
| nDCG@10 | reranked | 0.009 | 0.138 | **0.173** | 0.212 | 0.234 | 0.296 |
| recall@100 | dense | 0.005 | 0.024 | 0.035 | 0.050 | 0.061 | 0.078 |
| MRR@10 | dense | 0.000 | 0.233 | 0.308 | 0.378 | 0.429 | 0.562 |

**The headline: σ_d for nDCG@10 is 0.156 dense / 0.173 reranked — the top of the plan's assumed
0.10–0.20 band, not the middle.** Because the reranked figure is the larger one and stage 1
showed the two orderings correlate only +0.55, **the reranked σ_d is the one that should be
used for sizing**: it is the arm any shipping decision is evaluated in.

The §6 power table, re-derived (α = 0.05, power 90%):

| σ_d | δ=0.01 | δ=0.02 | δ=0.05 | source |
|---|---:|---:|---:|---|
| 0.100 | 1,051 | 263 | 43 | plan, assumed low |
| 0.150 | 2,365 | 592 | 95 | plan, assumed mid |
| 0.200 | 4,203 | 1,051 | 169 | plan, assumed high |
| **0.156** | **2,570** | **643** | **103** | **measured median, dense** |
| **0.173** | **3,131** | **783** | **126** | **measured median, reranked** |
| 0.231 | 5,628 | 1,407 | 226 | measured p90, dense |

With the plan's ~1.8× Holm inflation (z = 3.097 at α = 0.05/23):

| σ_d | δ=0.01 | δ=0.02 | δ=0.05 |
|---|---:|---:|---:|
| 0.156 | 4,690 | **1,173** | 188 |
| 0.173 | 5,713 | **1,429** | 229 |
| 0.231 | 10,269 | 2,568 | 411 |

What **1,000 Leg B queries** actually resolve at 90% power:

| σ_d | δ90 unadjusted | δ90 under Holm |
|---|---:|---:|
| 0.156 (measured, dense) | 0.0160 | **0.0217** |
| 0.173 (measured, reranked) | 0.0177 | **0.0239** |
| 0.231 (measured p90, dense) | 0.0237 | 0.0320 |

### 4.1 Three honesty clauses, and the sizing recommendation

1. **Each sd is itself uncertain.** n = 10 topics, df = 9: the 95% interval on a single sd is
   ×0.69 – ×1.83, i.e. the 0.156 median is consistent with anything in **[0.108, 0.286]**.
2. **This is very likely a LOWER bound for Leg B.** A CDS topic averages over ~109 relevant
   documents, which smooths its per-topic nDCG@10 heavily. A Leg B query is a **known-item**
   with one relevant document, where nDCG@10 is near-binary and per-query differences are
   large-or-zero. A doubling of σ_d on Leg B would be unsurprising; at σ_d = 0.30, 1,000
   queries resolve only δ = 0.042 under Holm.
3. **These are per-*topic* sds on a different leg.** They are the best empirical anchor
   available today, and they are not a measurement of Leg B.

**Sizing recommendation:**

* **§6's claim that 1,000 queries resolve δ = 0.02 under Holm is not supported.** At the
  measured σ_d it takes **~1,200 (dense) to ~1,430 (reranked)**. The plan's own condition —
  "for σ_d ≤ ~0.13" — is not met.
* **The ≤0.01-nDCG size rule is dead as a hypothesis test**: it needs ~4,700–5,700 queries under
  Holm. Decision 3 in §12 should be taken as written: **re-register it as TOST at margin 0.02**.
* **Budget ~1,500 Leg B queries, and re-measure σ_d on Leg B's own queries** before fixing the
  final size. The cheapest way to do that: run two configs over the 50-query pilot plus a small
  distractor pool — ~1–2 minutes of fleet time — and read the per-query sd directly. That is
  the single highest-value follow-up in this whole report and it was not run here.

---

## 5. Are Legs B and C viable? A straight answer

**Leg C: yes, viable, and cheaper than the plan assumes — but with two protocol amendments
that are not optional.**
Resolvability is 11.9%, not more, and that is fine: 1,200 citing articles already produced
6,024 usable pairs, of which 57.2% survive the position filter. Getting 300 filtered citances
needs ~100 citing articles. The cited documents are long (median 9.3k tokens) and their
evidence is genuinely distributed. The two amendments: **drop `not_a_question`** (it rejects
93% of citances by definition), and **treat multi-relevant qrels as the default** (62% of
citances co-cite). The one thing to watch is that Leg C's evidence sits in abstract+intro
42.8% of the time before filtering — three and a half times Leg A's rate — so the filter is
doing heavy lifting and the plan's rule to report filtered *and* unfiltered results matters
more here than anywhere else.

**Leg B: viable, but not at the quality the plan implies, and not at 1,000 queries.**
Position-by-construction works — 69 of 70 source sections start past token 1,024, and the
oracle independently confirms the construction on 59.5% of accepted queries. The
abstract-answerability pass is real and load-bearing: it rejects 40%, and its rejections track
the leakage covariate. But three things are worse than the plan assumes:

* **the rule filters contribute nothing** (0/70) — the entire protocol currently rests on one
  LLM verifier call;
* **a human read removes another 24%** of what the pipeline accepts, for reasons no filter in
  the plan can see (entity-stripping, front-matter sections, within-paper rationale questions);
* **the query shape is unrealistic** — 29 words median, half of them compound.

None of those is fatal, and §2.6 gives three cheap fixes. But the honest planning number is an
**effective yield of ~46%**, so ~1,500 accepted queries means generating ~3,300 candidates,
and the plan's "~3,000 generated → ~1,000 accepted" is roughly right *for the count* and wrong
*for the size that count needs to be*.

**And one thing this pilot cannot tell you.** Everything in §2 was generated by a model
substantially stronger than the one Phase 1 will use. The failure modes I found are ones a
strong generator still commits — they will not get better with Scout. The yield will.

### 5.1 What is now unblocked, and what is still open

| item | status after this run |
|---|---|
| check 2 | **measured, PASS** on 90 CDS topics — the first number it has ever had |
| check 5 (leakage clause) | **measured, PASS** on both Leg B and Leg C pilots |
| check 5 (low-tertile ≥300 clause) | **projection only** — untestable at pilot scale |
| Leg C true resolvability | **measured: 11.86%**; the "all three keys raise it" expectation is falsified |
| Leg C position-filter survival | **measured: 57.2%** (n=400, CI 52.4–62.0) |
| Leg B yield | **60% automated, 46% effective**; generator is not Scout |
| σ_d | **measured: 0.156 dense / 0.173 reranked**; §6's 1,000-query claim does not hold |
| σ_d **on Leg B's own queries** | **still assumed** — the recommended next measurement |
| Legs B/C on the overlap question | **not run** — needs an actual index build, out of scope here |
| stage 1's "realised size explains the grid" | **not tested here** — realised size is recorded per config in the fixtures this run emits, so Phase 1 can test it |

---

## 6. Files

```
pilots/
  RESULTS-legBC-pilots.md     this document
  pilot_common.py             structural units, section classifier, windowed CE client
  probe_units.py              byte-identity check vs stage 1's doc_text + unit-size distribution
  oracle_build.py             90-topic seeded sample + S3 fetch  -> oracle_pairs.json, xml/
  oracle_run.py               the §7a oracle over Leg A          -> oracle_results.jsonl
  oracle_analyse.py           the histogram, check 2, clustered CIs
  oracle_any.py               the oracle over arbitrary (query, doc) pairs
  legb_sample.py              deep-section sampling (no chunker imported)
  legb_screen.py              committed g1 screen_query + IDF covariate
  legc_resolve.py             manifest index + resolvability under all denominators
  legc_citances.py            citance mining, marker stripping, section attribution
  sigma_d.py                  empirical sigma_d + the re-derived power table
  power_checks.py             the delta80 floor of every threshold read above
  verifier_prompt.txt         the abstract-answerability prompt (sha256 055a1997ab1810a1…)
  gen/                        the three isolated LLM stages, in and out, 5 batches each
  legb_final.json             70 queries with every covariate, verdict and my read
  legc_pilot.json             the 50-citance pilot; legc_citances_all.json = all 6,024
  legc_oracle_out.jsonl       400 Leg C pairs through the oracle
  legb_oracle_out.jsonl       the construction cross-check
  oracle_summary.json / sigma_d.json / legc_resolve.json / oracle_cost.json
```
