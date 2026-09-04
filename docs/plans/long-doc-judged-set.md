# A long-document judged retrieval set for the chunking study

**Status: `PROPOSED` — Phase 0 partly run.** The set itself is still unbuilt: no fixture,
no qrels file, no index, nothing written under `/rag/`. What *has* run is Phase-0 §9 items
1–2 plus one unplanned step — the CDS coverage gate, the BM25 lead-only ablation, and a
real dense-pipeline chunking contrast on a 10-topic / 4,053-document CDS pilot — all
measured 2026-09-04 on this host. **§13 records what they found, including one wrong
inference that was acted on and then reversed.** Phase-0 items 3–6 have **not** run, so
acceptance checks 2 and 5 (§8) remain unmeasured and Legs B and C are exactly as
speculative as when this was written.

Dating convention: claims marked 2026-09-03 were checked read-only on this host when the
plan was drafted; figures marked 2026-09-04 come from the Phase-0 runs and supersede any
drafted estimate they contradict; everything else is an estimate or an open check and
says so.

**What Phase 0 changed in this document, in one screen:**

| § | was | now |
|---|---|---|
| 1 | 12 of 24 stage-1 cells dead *on scifact* | dead on **every** planned BEIR dataset — `trec-covid`'s longest document is 925 tokens |
| 4, 13 | Leg A a plausible anchor | Leg A **keeps its place**, with a *measured* bias profile: it rewards coarse, aboutness-carrying configs |
| 6 | "~494 relevant/topic, CDS similarly deep" | that is TREC-COVID's number. CDS: **median 109 / mean 153.4**. The metric map survives; the figure did not |
| 8 | check 3 a gate on the leg | re-registered as a **diagnostic** (first-stage lead-chunk sufficiency = reranker load). Check 4 is the gate, and it passes 10/10 |
| 9 | ~80k embed tok/s, ±2× | **~164k tok/s** measured on six endpoints; costs recomputed, ±2× kept |

**Recommendation in one paragraph.** Build a **three-legged judged set**: (A) **TREC CDS
2014–2016** — 90 human-judged topics whose corpus *is* PMC OA JATS full text, the same
markup, bucket and parser as the 500k-article target — as the human-judged anchor; (B) an
**LLM-generated deep-evidence query set (~1,000 queries) over our own 1.44M-article PMC OA
corpus** as the statistical workhorse, generated with the paraphrase-first,
overlap-measured protocol already implemented in `g1_make_queries.py`, extended with a
*generate-from-deep-sections, reject-if-abstract-answerable* rule so position-of-evidence
is controlled by construction; and (C) a **citation-based validation slice (~300 queries)**
mined from the same corpus's in-text citations (the raw XML in `/rag/oa/corpus/xml/`
retains ref-lists for all 1,439,753 articles), filtered to pairs whose evidence sits in the
cited paper's body. A config ranking that agrees across three legs with independent bias
profiles is defensible; any single leg alone is not. Before any of it is trusted, the set
must pass a pre-registered **chunking-sensitivity acceptance test** (§8). The cheapest
de-risking pilot is ~2–3 days of engineering, under ~2 GPU-hours, and is specified in §9.

---

## 1. Why this exists (recap, one screen)

Stage 1 of the chunking study was specified over `scifact` (+ nfcorpus, scidocs,
trec-covid). Measured on the real corpus with the SFR tokenizer, scifact's median document
is **354 tokens**; at chunk size 2048, 5,182 of 5,183 documents are a single chunk, so all
three overlap fractions build the same index. **Twelve of the 24 stage-1 cells carry no
signal**, and every planned dataset is abstract-length (nfcorpus ~468 mean tokens, scidocs
~343, trec-covid-as-cached ~340 — see §11 flag 2).

**Re-measured for Phase 0 with the SFR tokenizer, and it is worse than "scifact is short".**
scifact: 5,183 docs, median **348** tokens, p95 **649**, exactly **one** document over
2,048. BeIR `trec-covid` — the dataset the chunking plan calls "full text, closest to the
PMC target" — 171,332 docs, median **378** tokens, **max 925**: *not one document in it
could ever split at size 1024*. So the twelve dead cells (sizes 1024 and 2048 × all kinds
and fractions) are dead on **every planned BEIR dataset**, not just on scifact — the
fallback "run stage 1 on trec-covid instead" does not exist. Measured overlap inflation
says the same thing from the other side: 1.036× / 1.085× at size 256, 1.002× / 1.006× at
512, 1.000× at both 1024 and 2048, against the `1/(1−f)` prediction of 1.143× / 1.333×.
(These supersede the 354-token median and 662-token p95 quoted in
[chunking-evaluation.md](chunking-evaluation.md); the ~6-token drift between the two
scifact measurements is unexplained and small — see the PR that landed this file.)

The target corpus is nothing like that:

**Measured, this host, from `/rag/oa/corpus/manifest.jsonl` (205,679-doc sample, 1-in-7):**
median body **35,318 chars ≈ 10,100 tokens** at the measured 3.5 chars/token; p95 ≈ 21,200
tokens; **96.9% of articles exceed 2,048 tokens; 93.7% exceed 4,096**. (1.4% have empty
bodies — scanned-only or front-matter-only articles; exclude them.) A judged set over
documents like these exercises every cell of the grid: sizes 256→2048 differ by 5–40
chunks/doc, and overlap inflation actually approaches its `1/(1−f)` bound.

The user's decision, taken: build a long-document judged set *before* running the study.

## 2. The failure mode this design is built against

**Long documents are necessary, not sufficient.** If the relevant evidence always sits in
the abstract or introduction, a lead-chunk baseline wins trivially: every config keeps the
lead of every doc intact (chunk 1 starts at char 0 under all sizes and overlaps), so the
set is chunking-insensitive no matter how long the tails are. The repo has already built
this trap once — `chunking_compare_7way`'s known-item-by-title harness, where BM25 sees the
query verbatim in the lead chunk — and its own docs flag it.

The design rule, applied to every leg:

> **A (query, relevant-doc) pair earns its place only if the best evidence for the query
> sits measurably beyond the document's head, and the set as a whole must show a
> position-of-evidence distribution spread across document depth — verified by
> measurement (§7), not assumed — before any GPU is spent on the grid (§8).**

How each leg satisfies it:

- **Leg B (LLM)** — by construction: queries are generated *from* a specific deep
  structural unit (a `<sec>` beyond the intro), the source section and char offsets are
  recorded in the fixture, and a verifier pass rejects any query answerable from the
  title+abstract alone. Position-of-evidence is a recorded field, not an inference.
- **Leg C (citations)** — by filtering: for each (citance, cited-doc) pair, locate the
  best-supporting *section* of the cited doc (§7's oracle, run over structural units);
  keep only pairs whose evidence section is not the abstract/intro. Report results on
  the filtered and unfiltered sets both (§7's circularity rule).
- **Leg A (CDS)** — by measurement + stratified reporting: qrels are document-level and
  we cannot re-judge, so we *measure* the oracle position-of-evidence profile over the
  judged positives and (a) require the corpus-level acceptance test to pass, (b) report
  metrics on the "deep-evidence" query subset alongside the full topic set.

And one global behavioural check that does not depend on any oracle model: the
**lead-only-vs-full-index ablation** (§8), which directly measures how much of the set a
lead-chunk baseline can already solve.

## 3. What was verified on this host (2026-09-03)

| claim | status |
|---|---|
| `/rag/oa/corpus/clean/` = 1.44M JATS files, `<back>` **stripped** (no ref-lists) | verified — 111 GB, sample files have `<front>`/`<body>` only |
| `/rag/oa/corpus/xml/` = **raw** JATS incl. `<ref-list>`, **1,439,753 files, 182 GB** | verified — this is what makes Leg C feasible locally |
| `manifest.jsonl`: per-article `pmcid`, `pmid_xml`, `doi_xml`, `body_chars`, `n_sections` (median 17), `n_refs` (median 49) | verified |
| Corpus length: median ≈10.1k tokens, 96.9% > 2048 tokens | measured (205,679-doc sample, 3.5 c/t) |
| In-corpus citation resolvability | measured **floor**: 11.8% of pmid-bearing refs resolve into our own corpus, median 4 in-corpus cited refs per citing doc (60-doc sample, one shard, **pmid only** — the sample also had 3,581 `doi` and 1,866 `pmcid` pub-ids, and the manifest carries `doi_xml`, so resolving via all three raises this) |
| ~103 in-text `<xref ref-type="bibr">` anchors per article | measured (40-doc sample) |
| `/rag/cache/datasets/` holds **all four** BeIR sets + qrels (scifact, nfcorpus, scidocs, trec-covid) | verified — the task brief's "only scifact" is stale (§11) |
| BeIR `trec-covid` corpus is **abstracts only** — median 1,198 chars ≈ 340 tokens (500-doc sample) | verified; qrels: 66,336 rows, grades {2:14,217, 1:10,456, 0:41,661, −1:2}, ids are CORD-19 `cord_uid`s |
| CORD-19 2020-07-16 full release downloadable: **3.66 GB** tar.gz, HTTP 200 | verified (`ai2-semanticscholar-cord-19.s3…/historical_releases/`) |
| TREC-COVID complete qrels (`qrels-covid_d5_j0.5-5.txt`) live at `ir.nist.gov` | verified HTTP 200 |
| **TREC CDS topics + qrels live at `trec.nist.gov`**: `qrels-treceval-{2014,2015,2016}.txt`, `topics-2014.xml` | verified HTTP 200 |
| SFR embedding endpoints: **:9001–:9006 open; :9007–:9008 closed** — quote 6, not 8 | verified (TCP only; no embedding was run) |
| Dev tenant stores for any experiment: Qdrant `:24041`, ES `:24043`; URLs are required flags with no default | per CLAUDE.md / #476; not exercised |
| Anti-contamination query-gen machinery exists: `g1_make_queries.py` (paraphrase-first two-pass, IDF-overlap covariate, `title_answerable` filter, human-query path) | verified by reading the source |
| `_stats.py` paired-bootstrap layer takes per-query arrays; harnesses retain them in memory | verified by reading; whether past runs *persisted* them is a pilot check (§6) |

**Superseded in part by measurement (2026-09-04).** Four rows above were desk checks that
Phase 0 then exercised for real: the CDS topics/qrels were downloaded and merged (90 topics,
after year-prefixing — §13.4), the judged PMCIDs were fetched from `pmc-oa-opendata` at
98.5%, the six SFR endpoints were *used* rather than TCP-probed (~164k tok/s, 0 retries),
and the dev-tenant ES was used and cleaned up with a verifying listing. §13 has the numbers.

## 4. Options evaluated

### Option 1 — reuse existing expert judgments over long documents

Real human judgments are worth a lot, and two candidates genuinely fit. Ranked:

**1a. TREC CDS 2014–2016 — RECOMMENDED as the human anchor (Leg A).**
- *What it is:* TREC Clinical Decision Support. Corpus = **PMC OA full-text NXML
  snapshots** (Jan 2014, ~733k articles for 2014/15; Mar 2016, ~1.25M for 2016). Topics =
  patient case narratives (30/year × 3 years = **90 topics**), each in three variants
  (description / summary / [2016] actual EHR note), tagged diagnosis/test/treatment.
  Qrels = document-level, graded, pooled NIST assessment, thousands of judged PMCIDs per
  year.
- *Why it wins:* the judged documents are **the same JATS/NXML, from the same
  `pmc-oa-opendata` lineage, parsed by the same `jats.py`** as the 500k target.
  `structure_tok512` — the headline config the study exists to evaluate — runs *as
  designed*, on real `<sec>`/`<p>` markup. No other human-judged option offers that. 90
  topics ≈ 1.8× TREC-COVID's power. Cost ≈ zero: qrels/topics are public (verified),
  judged docs are fetched by PMCID from the S3 bucket we already use.
- *Risks / open checks (pre-registered pilot items) — **all three now measured**, see §13.1:*
  (i) fetchability from `pmc-oa-opendata` is **98.5%** (197/200, Wilson 95% CI 95.7–99.5)
  for grade≥1 and **95%** (57/60) for the grade-0 hard negatives; the ~1.5% loss is
  filtered out of the qrels, and three relevants are genuinely withdrawn from the bucket;
  (ii) version drift is smaller than feared — of 20 ids probed, 19 exist only at `.1`, so
  the bucket effectively carries a single earliest version and fetched copies are close to
  assessor-era; accepted and recorded; (iii) query style is a case narrative, not a keyword
  query — a *different* realism than Leg B's, which is a feature for triangulation but
  means CDS alone doesn't represent short-query behaviour. A fourth risk the plan did not
  name has now been measured too: Leg A's **bias profile** — it rewards coarse,
  aboutness-carrying configs (§13.3).

**1b. TREC-COVID over CORD-19 full text — fallback / optional second human leg.**
- Real graded qrels (66k rows, verified), assessors saw full articles, download verified
  (3.66 GB). But: **50 topics** (resolves only large effects, §6); and CORD-19 ships
  `pdf_json`/`pmc_json` derived parses, **not JATS** — section structure is lossy, so the
  structure-aware config is handicapped on exactly the corpus chosen to test it. Also
  full text exists for only a fraction of the 171k docs (coverage among the 35,480 judged
  docs is a pilot measurement if we pursue it). Keep it in the plan only if the CDS
  fetchability check fails, or later as a cheap third domain.
- Note the trap flagged in §11: the *BeIR* trec-covid in our cache is abstracts-only —
  Phase 0 put a number on it (median 378 tokens, **max 925**, §1). Using it as a "full
  text" dataset would silently rebuild the scifact situation, only worse: at size 1024 it
  cannot split a single document.
- **Still a live fallback.** An intermediate draft of the Phase-0 findings retired
  TREC-COVID on the argument that document-level qrels cannot see chunking at all. That
  argument is falsified (§13.2), so its corollary is void: TREC-COVID keeps its place here
  as an optional second human leg, subject to the same CORD-19-not-JATS caveat above and
  to its 50 topics.

**Rejected for this purpose:**
- **BioASQ** — Task B corpus is PubMed **abstracts** (snippets are abstract spans);
  registration-gated. Fails the core requirement.
- **TREC Precision Medicine 2017–19** — MEDLINE abstracts + clinical-trial registrations.
  Abstracts again. Fails.
- **TREC Genomics 2006–07** — the closest thing ever built to what we want
  (**passage-level** judgments over 162k full-text Highwire HTML articles), but the
  corpus was distributed under a signed agreement via OHSU and current availability is
  doubtful; the HTML needs its own parser; content is pre-2007. Worth one email to
  confirm availability if passage-level ground truth is ever wanted; not on the critical
  path.

### Option 2 — citation-based qrels over our own corpus (Leg C, validation slice)

Mine `(citance → cited article)` pairs from `/rag/oa/corpus/xml/`: the sentence around an
`<xref ref-type="bibr">` anchor becomes the query; the cited article (resolved via
pmid/pmcid/doi against the manifest) is the relevant doc. Measured floor: ~12% of
pmid-bearing refs resolve in-corpus, median 4 per citing doc — across 1.44M citing docs
that is **millions of candidate pairs**; we need hundreds. Free, large, real signal
(a human author asserting "this document supports this claim"), and native to the target
corpus — distractors come from the same 1.44M for free.

*Validity threats, taken seriously:*
1. **A citance is not an information need.** It presupposes its answer, is written
   post-hoc, and often paraphrases the cited paper's title — a lexical-leak cousin of the
   known-item trap. Mitigations: strip the citation marker and bracketed author-year
   text; apply `g1_make_queries`-style filters including `title_answerable` (≥80% of
   query IDF mass in the cited doc's title → discard); record the IDF-overlap covariate
   vs the cited doc's title+abstract and report the low-overlap tertile separately.
2. **Cited claims often sit in the cited paper's abstract** — the §2 failure mode.
   Mitigation is the position filter: keep only pairs whose best-supporting *section*
   (oracle, §7) is beyond abstract+intro. Method-type citations (citances located in the
   citing paper's Methods section, citing protocols/software/datasets) are
   preferentially deep-evidence; oversample them.
3. **Incomplete qrels.** Other corpus docs (especially reviews) also support the claim
   and get scored as non-relevant. For *paired config comparison* this mostly adds noise
   rather than bias, but it is not provably neutral (configs could differ in which
   unjudged docs they surface). Mitigations: multi-relevant qrels where one citance cites
   several in-corpus refs; and this leg is a **validation slice, not the workhorse** — it
   confirms or contradicts the ranking, it does not carry the CIs.

### Option 3 — LLM-generated queries over our own corpus (Leg B, workhorse)

The circularity threat is real and has a real answer, most of which this repo has already
built (`g1_make_queries.py` §T1b). The defensible protocol:

1. **Never generate from a chunk of any candidate chunker.** Generation units are the
   document's own **structural sections** (`<sec>` from JATS) — a natural unit that no
   config produces verbatim: sections (median 17/doc, typically 300–1,500 tokens) are
   larger than most chunks and are not aligned to any overlap phase. Residual concern —
   sections are *related* to `structure_tok512`'s boundaries (it refuses to cross them).
   Handled three ways: (a) the two-pass **paraphrase-first** pipeline (query written from
   an abstractive summary; the verbatim text never enters the query-writing context);
   (b) the recorded IDF-overlap covariate with sensitivity analysis on the lowest
   tertile; (c) the claim discipline in §10 — Leg B alone is never the sole evidence for
   the structure-aware config; that contrast leans on Legs A and C.
2. **Deep-section sampling:** sample the source section uniformly from sections whose
   start offset is past the first 25% of the body and which are not
   abstract/intro/conclusion-titled; record section title, index, and char offsets in
   the fixture. Position-of-evidence is then **known by construction**.
3. **Abstract-answerability rejection:** a separate verifier call sees only the query +
   the doc's title+abstract and answers "is this fully answerable from the above?" —
   reject if yes. This is the §2 rule made machine-checkable. (Verifier prompt hashed
   into the manifest, per the g1 convention.)
4. **Existing filters** apply unchanged: length, not-a-question, `names_document`,
   `title_answerable`, dedupe, optional critic.
5. **False-negative control:** with 1.44M docs, a generated fact may be restated
   elsewhere (reviews). Primary metric is therefore recall of the *source* document
   (passage-provenance known-item — legitimate here because the query is deliberately
   deep and paraphrase-shielded, unlike the title proxy); secondarily, a pooled
   LLM-judge pass over top-10 union across configs upgrades to multi-relevant qrels for
   nDCG (cost bounded: queries × pooled docs, §9). Report both; pre-register that the
   known-item recall figures carry the CIs.

*What it cannot claim:* the LLM query distribution is not a user query distribution, and
an LLM-judged relevance upgrade is not human ground truth. Leg B measures *config ranking
under localized-deep-evidence queries on the target corpus* — exactly the property the
study needs — not absolute product quality.

### Option 4 — hybrids (recommended shape)

The three legs above, run as one study with one pre-registered analysis: **Leg A anchors
validity** (real judges, real full text, native markup), **Leg B carries the statistics**
(~1,000 queries, native corpus, controlled evidence position), **Leg C cross-checks with
human-authored signal that no LLM wrote**. The pre-registered concordance criterion:
the sign of every decision-relevant contrast (§10 of chunking-evaluation.md's decision
table) must agree between Leg B and Leg A(±C) wherever Leg A has power to call it; a
disagreement is a finding to investigate, not to average away.

## 5. The judged set, concretely

| leg | corpus | queries | qrels | judged docs (target) |
|---|---|---|---|---|
| A: CDS | judged PMCIDs fetched as raw JATS from `pmc-oa-opendata` (+ grade-0 docs kept — best hard negatives) | 90 topics × summary variant (description as sensitivity) | NIST, graded, doc-level, filtered to fetchable docs; drop topics with <5 fetchable relevants | **12,307 distinct grade≥1 PMCIDs** (13,807 topic-doc pairs), measured; ~1.5% unfetchable; plus grade-0 negatives |
| B: LLM | our `/rag/oa/corpus/xml` (docs with `body_chars>0`, `n_sections≥8`) | ~1,000 accepted (from ~3,000 generated) | source-doc known-item (primary); pooled LLM-judged multi-relevant (secondary) | 1,000 source docs + pooled extras |
| C: citations | same | ~300 filtered citances | cited-doc(s), multi-relevant where co-cited | ≤ ~1,000 |

All three share one distractor pool: the remainder of the 1.44M (§ compatibility below).
Fixtures are pinned files (queries JSONL + qrels TSV + manifest with prompt hashes and
corpus doc lists) *before* any retrieval run, per the g1 convention.

**Distractor-ladder compatibility.** The ladder holds the judged set fixed and pads with
unjudged distractors; subsampling is never used, so qrels are never destroyed. Legs B and
C are native: distractors are drawn (seeded, pinned list) from the other ~1.4M articles —
in-domain, full-length, the honest competition. Leg A: distractors are *also* drawn from
our PMC OA corpus — legitimate because CDS docs are the same kind of object (PMC OA
biomedical full text), and length-matched by construction, so distractor competition
scales the same way for every config. Rungs: judged-only ("×0"), ×1, ×10, capped at the
error bound of ~500k total docs (the operating point). **Pre-registered dedupe rule:
every leg's distractor pool excludes that leg's judged PMCIDs** — some CDS-judged
articles are almost certainly already in our 1.44M (both are PMC OA), and a judged
article re-entering as a distractor under a second doc_id would let the retriever return
the copy and be scored 0, a false negative manufactured by the ladder itself. The Phase-0
CDS coverage check computed that overlap: **10.0% (1,229 / 12,307)** of CDS relevants are
already in the local `/rag/oa` corpus. Two consequences — the dedupe rule above has real
work to do on 1,229 documents, and Leg A assembly must fetch the other 90% from S3 rather
than reading them off local disk. One caveat
carried from the existing plan: rung labels are not comparable across legs (judged
fractions differ); normalise on distractors-per-judged-doc as GLOSSARY.md already
requires.

**Stage-1 rerun on this set** stays cheap by running the grid on the **judged-only rung**
(that is where the config contrasts live; the ladder is stage 2's axis, unchanged). Cost
scales with judged-set tokens, not corpus tokens — see §9.

## 6. Size and power — the reasoning, not a folk number

The analysis is **paired**: every config scores the same queries, and `_stats.py` already
does paired bootstrap + Holm-corrected Wilcoxon. For a paired comparison on a per-query
metric, the queries needed to detect a true mean difference δ with two-sided size α and
power 1−β is approximately

  n ≈ ((z₁₋α/2 + z₁₋β) · σ_d / δ)²

where σ_d is the **standard deviation of per-query differences** — small when configs are
similar and everything downstream is shared, which is exactly our situation. With
α = 0.05, **power 90%** (z = 1.96 + 1.28 = 3.24):

| σ_d ↓ / δ → | 0.01 | 0.02 | 0.05 |
|---|---|---|---|
| 0.10 | 1,050 | 263 | 42 |
| 0.15 | 2,360 | 590 | 95 |
| 0.20 | 4,200 | 1,050 | 168 |

Three corrections to that table, stated rather than hidden:

1. **Multiplicity.** Stage 1 compares ~24 configs against a reference under Holm; the
   effective α for the tightest comparison is ~0.05/23, z₁₋α/2 ≈ 3.1, inflating n by
   ~(3.1+1.28)²/3.24² ≈ **1.8×**. The pre-registered decision contrasts (4 rows in the
   decision table) are the ones that must be powered; the rest of the grid is
   exploratory and says so.
2. **σ_d must be measured, not assumed.** The scifact/7-way harnesses retain per-query
   arrays in memory for `_stats`; the pilot persists them (one-line change at report
   time) and reads off the empirical σ_d for nDCG@10 between adjacent configs. Until
   then 0.10–0.20 is an assumption. (For similar systems sharing a pipeline, published
   IR test-collection experience puts paired σ_d for nDCG@10 in roughly that band; the
   measurement replaces the citation.)
3. **Binary metrics need a different frame.** recall@1 / MRR-at-full-depth differences
   are driven by the *discordance rate* (queries where the two configs disagree), a
   McNemar-style analysis: with discordance rate p and effect δ, n ≈ z² · p / δ². These
   need more queries than nDCG for the same δ; the workhorse must carry them.

**Consequences.**
- **Leg B at ~1,000 queries** resolves δ ≈ 0.02 nDCG@10 at 90% power *with* the Holm
  inflation for σ_d ≤ ~0.13, and δ ≈ 0.05 comfortably in any case. That is enough for
  every pre-registered decision except the "≤0.01 nDCG for 2× fewer chunks" size rule.
- **The ≤0.01 rule should be re-registered as an equivalence test (TOST) with margin
  0.02**, or accept ~2,000+ queries. Chasing 0.01 resolution with 1,000 queries would
  manufacture false "no difference" findings. Recommended: TOST at 0.02.
- **Leg A (90 topics)** resolves only δ ≳ 0.10–0.15 as a hypothesis test — its role is
  anchor and sanity check (sign agreement, gross effects), not fine discrimination.
  TREC-COVID's 50 topics, likewise, if used. **This is now less pessimistic than it
  reads:** the config contrasts Phase-0 step 3 actually measured on CDS are 0.04–0.14
  (§13.2), i.e. at or above what 90 topics can resolve, so Leg A is not being asked to
  find effects below its own floor.
- Generating 1,000 accepted queries is cheap (§9), so the workhorse size is not the
  bottleneck; the pooled LLM-judge upgrade is the only cost that scales with it.

**Metric–density mismatch — pre-registered metric map.** **Corrected 2026-09-04: the
"~494 relevant docs/topic" attributed to CDS above was TREC-COVID's own number** —
(14,217 + 10,456) / 50 = 493.5, from this document's own §3 qrels grade counts — and the
claim that "CDS topics are similarly deep" was wrong by ~4×. Measured on the real qrels:
CDS has **90 topics, 12,307 distinct grade≥1 PMCIDs, 13,807 (topic, relevant-doc) pairs,
median 109 and mean 153.4 relevant per topic**, and ~1,260 judged docs per topic. The
metric map below **survives the correction**: at median 109 relevants, recall@5 still caps
at ~4.6%, so it still cannot discriminate. **recall@5 saturates in the low single digits
and cannot discriminate anything there.** The task's metric list (`recall@{1,5,10}`,
`nDCG@10`, `MRR@10`) works as stated only on sparse-qrels legs. Per-leg map: deep human
legs (A, and TREC-COVID if used) report **nDCG@10, P@10, MRR@10**; sparse native legs
(B, C) report **recall@{1,5,10}, nDCG@10, MRR@10**. All legs report both
retrieval-only and reranked families, per the existing plan.

## 7. Measuring position-of-evidence (and the circularity rule)

Two instruments, one model-based and one behavioural, so neither's bias is load-bearing:

**7a. The section-level oracle.** For each judged (query, relevant-doc) pair: split the
doc into its **structural sections** (JATS `<sec>`; sub-sections merged to a ~1k-token
floor), score every section against the query with the crossencoder sidecar (`:50052`,
4,096-token truncation — sections over that are windowed and max-pooled), and record the
argmax section's **relative start offset** (0–1), section title class
(abstract/intro/methods/results/discussion/other), and the margin over the best
head-section score. Output: the set's position-of-evidence histogram, per leg. This is a
proxy with the reranker's biases — which is acceptable *because the same reranker is the
pipeline's last gate*: evidence this model cannot see deep is evidence the product cannot
use — and it is why 7b exists as the model-free control.

**Pre-registered circularity rule:** any *filtering or selection* on evidence position
(Leg C's filter, Leg B's deep-section sampling) operates on **structural units only —
never on any candidate chunker's cuts** — and every filtered result is reported beside
its unfiltered counterpart. The oracle is also never used to tune a chunker.

**7b. The lead-only ablation (model-free, behavioural).** Build two BM25-only indexes in
the dev tenant's ES (no GPU at all): (i) full docs chunked at `fixed_tok512/0`; (ii) the
**first chunk only** of each doc. The gap in recall@10/nDCG@10 between (ii) and (i) is
the fraction of the set a lead-chunk baseline cannot solve — measured on model behaviour,
with no embedding model and no oracle in the loop. Repeat with one dense config on the
judged-only rung as confirmation (small GPU cost, §9).

**Ran 2026-09-04, and it is a narrower instrument than this paragraph claims.** On the CDS
pilot the gap is **−0.006 recall@100** (CI −0.029…+0.019): lead-only is not worse. But a
BM25 index has no reranker, and it is the reranker that reads passages — with one in the
loop the same contrast reverses (§13.2). What 7b measures is *first-stage* lead-chunk
sufficiency, i.e. how much work the reranker is left to do. It does **not** measure
whether a leg can rank chunking configs; that is check 4's job. Read the number as a
reranker-load diagnostic and nothing more, on any leg.

**Optional human spot-check:** 50 sampled (query, doc, oracle-argmax-section) triples
read by a human (~2 hours) to confirm the oracle is pointing at real evidence. Cheap
insurance on Leg A, where nothing is known by construction.

## 8. The acceptance test — the gate before any grid GPU is spent

Pre-registered thresholds; a leg that fails is fixed or dropped *before* stage 1 runs on
it. Chosen to be demanding on the failure mode while achievable by a genuinely
distributed set; they are proposals for review, and the user may tighten them.

| # | check | instrument | threshold |
|---|---|---|---|
| 1 | length precondition | tokenizer over judged docs | ≥80% of judged docs > 2,048 SFR tokens; overlap inflation at 12.5%/25% within 10% of `1/(1−f)` at sizes ≤1024 |
| 2 | evidence depth | oracle 7a | ≥40% of (query, relevant-doc) pairs have argmax evidence past the first 1,024 tokens; ≤35% argmax in abstract+intro; every major section class represented |
| 3 | first-stage lead-chunk sufficiency — **a diagnostic, not a gate** (re-registered 2026-09-04) | ablation 7b (BM25) | reported, not gated: full-index recall@100 minus lead-only, and nDCG@10 likewise. The old thresholds (≥0.15 / ≥0.10) are retained only as the scale at which the first stage is doing the work rather than the reranker |
| 4 | config contrast is live | `fixed_tok256/0` vs `fixed_tok2048/0`, judged-only rung, one cheap build each | ≥25% of queries change their top-10 *doc* set; paired per-query deltas non-degenerate (σ_d > 0) |
| 5 | leakage bound (B, C) | IDF-overlap covariate | median query↔title+abstract IDF overlap below the `title_answerable` bar; low-tertile subset large enough to re-run the headline contrast (≥300 queries on Leg B) |

Check 4 is the whole exercise in miniature: on scifact those two configs build
near-identical result lists; on a fit-for-purpose set they must not.

**Why check 3 was demoted.** It was written as a gate on a leg, and used as one: an
intermediate reading of the ablation recommended demoting Leg A on the strength of check
3 failing. Check 3 asks *can a lead-chunk baseline already solve this set* — a **coverage**
question, on the axis lead-vs-full. The study's contrasts are on the **granularity** and
**boundary** axes, which is a different question about the same documents, and check 4 is
the pre-registered instrument for it. A set can fail check 3 and pass check 4 (CDS does,
§13.2). Keeping check 3 is still worth it: a set where the lead suffices at first stage is
a set where the reranker carries the result, which is a real property to know before
reading any rerank-off number.

### Phase-0 status of each check (2026-09-04)

| # | status | reading |
|---|---|---|
| 1 | **measured, not callable** | 80.2% of judged docs > 2,048 tokens, CI 74.1–85.2 (n=197) — the CI straddles the pre-registered ≥80% line. It becomes exact, with zero sampling error, when assembly fetches all ~12k relevants. A larger, differently-drawn sample (4,053 docs, the step-2 pilot) gives 84.9%. If the full-corpus figure lands below 80%, **re-register the threshold consciously** — it was calibrated against the native corpus's 96.9% — rather than rounding it into a pass |
| 2 | **not run** | needs the §7a oracle; Phase-0 item 3 never ran |
| 3 | **ran, re-registered** | −0.006 recall@100 (CI −0.029…+0.019), −0.017 nDCG@10 on the CDS pilot. Reported as the reranker-load diagnostic above, not as a gate |
| 4 | **ran, PASS** | **10/10** queries change their top-10 *doc* set between the size extremes (bar: ≥25%), with top-10 overlaps of 2–5 documents out of 10. Measured on the real dense pipeline rather than BM25, and with 12.5%-overlap extremes rather than check 4's literal `/0` — a deviation recorded in the pre-registration (§13.2) |
| 5 | **not run** | Legs B and C do not exist yet |

## 9. Cost, and the cheapest useful version

Fleet facts used: 6 SFR endpoints up (:9001–:9006 — **exercised** 2026-09-04, not just
TCP-probed: ~108M tokens embedded in ~11 minutes with **0 retries** ≈ **164k tokens/s**
aggregate, roughly 2× the ~80k tok/s this plan assumed. Two measurements on different
fleets is still not a benchmark, and they are themselves ~2× apart, so **keep the ±2×
band** and read every hour below as an order of magnitude); crossencoder sidecar on
:50052; Scout LLM (`max_model_len` 60k) available for generation/judging; all experiment
stores = dev tenant only.

**Phase 0 — pilot (the de-risking buy; ~2–3 days wall, <2 GPU-h, one engineer).**
Items 1–2 **ran 2026-09-04**, plus a step 3 this list never planned; items 3–6 have not.
§13 has the results.
1. **DONE — CDS coverage check** (CPU + S3, hours): download qrels/topics (verified live),
   fetch judged PMCIDs from `pmc-oa-opendata`, measure fetchable fraction, grade coverage
   per topic, token-length distribution of judged docs. Gate: if <70% of relevant docs per
   topic fetchable for <60 topics, demote Leg A to TREC-COVID fallback. **PASS by wide
   margins** — 90 topics, 98.5% fetchable (§13.1).
2. **DONE — lead-only ablation, BM25**, on a 10-topic / 4,053-doc CDS pilot (dev ES
   `:24043` only, no GPU): check 3 reads −0.006 recall@100. Its *inference* — that Leg A
   cannot rank chunking configs — was tested directly in step 3 and falsified (§13.2). The
   200-doc Leg B/C prototype was not built.
2b. **DONE (not on this list, but pre-registered in its own right) — step 3, the real dense
   contrast:** 4 chunking configs, real SFR embeddings, real reranker, on the same pilot
   set, with predictions and a falsification bar written down before any embedding call.
   This is the run that reversed the demotion (§13.2).
3. **Oracle on a sample** (GPU, well under 1 h): 2,000 sampled (query, relevant-doc)
   pairs through the crossencoder → first position-of-evidence histograms (checks 2, 5).
4. **50 pilot Leg B queries** end-to-end through the extended `g1_make_queries` protocol
   (Scout; ~1 M LLM tokens): yield rate, filter hit rates, manual read of all 50.
5. **50 pilot Leg C citances** with resolution via pmid+pmcid+doi: measure the true
   resolvability rate (the 11.8% is a pmid-only floor) and the position-filter survival
   rate.
6. **Persist per-query arrays** from the existing scifact stage-1 harness run (or re-run
   its cheap cells) → empirical σ_d for the power table (§6).

*Decision point with the user after Phase 0* — leg composition, thresholds, and sizes
confirmed against measurements.

**Phase 1 — full construction (≈1–2 weeks wall, mostly CPU/LLM, small embed cost):**

| item | estimate | basis |
|---|---|---|
| Leg A assembly (fetch ~30–60k judged docs, qrels filter, fixtures) | 1–2 days eng, no GPU | verified endpoints; S3 rate ~34–44 art/s measured previously |
| Leg B: ~3,000 generated → ~1,000 accepted, 2-pass + verifier ≈ 4–6 Scout calls/query avg | ~30–60 M LLM tokens ≈ a few node-hours; 2–3 days eng incl. review of a 100-query sample | g1 yield experience; prompt sizes ~2–6k tokens |
| Leg C: mine ~5k candidate citances → filter → 300 | 1–2 days eng; oracle pass ~5k pairs × ~20 sections ≈ 100k crossencoder pairs, ≲1 GPU-h | measured anchors/doc and resolvability floor |
| Oracle over all legs' positives (~40k pairs × ~20 sections ≈ 0.8 M CE pairs) | few GPU-h | sidecar throughput to be measured in pilot |
| Acceptance-test dense builds (2 configs × judged-only rung) | ~1–2 GPU-h per leg-corpus | 80k tok/s ±2× |
| Pooled LLM-judge upgrade for Leg B (1,000 q × ~30 pooled docs, judged at doc level from title+abstract+oracle section) | ~50–100 M LLM tokens, node-hours | optional, secondary qrels |
| Human effort total | ~1 week of one engineer + ~2–4 h expert spot-reads | — |

**What stage 1 then costs on this set** (for context, not part of this build): 24 index
builds over the judged-only rungs. Leg B judged corpus ≈ 1,000 docs × ~10k tokens ≈ 10 M
tokens → minutes per build. Leg A: the pilot measured judged-doc length at a **median
4,097 body tokens** and a mean of 6,880 — the tail is long — so ~12.3k relevants plus
grade-0 negatives at ~40k docs × ~6.9k tokens ≈ 275 M tokens → **~0.47 h per build at the
measured 164k tok/s, ~11 GPU-h for the 24-cell grid** (±2×; semantic cells cost roughly
double — they embed the text again to find boundaries). The step-3 run is the closest thing
to a calibration: 108 M tokens, four configs, 4,053 docs, ~11 minutes. If that is too dear,
run the full grid on Leg B + the 4-config short-list on Leg A.

## 10. What the resulting study can and cannot claim

**Can claim:**
- How chunk size, overlap fraction, and boundary policy change retrieval and reranked
  retrieval of full-length PMC articles, *on the target corpus and markup*, for queries
  whose evidence provably sits at varying document depths — with CIs from ~1,000 paired
  queries and sign-anchoring on 90 human-judged topics.
- Whether the structure-aware packer earns its build (the pre-registered contrast), with
  the JATS structure actually present — and, per the circularity rule, with that
  contrast never resting on Leg B alone.
- How each config degrades under in-domain distractor competition (the ladder, stage 2).

**Cannot claim:**
- Absolute product quality for real users: Leg B's queries are LLM-authored, Leg C's are
  citances, Leg A's are clinical case narratives — none is "our users' query
  distribution".
- Anything at δ < 0.02 nDCG resolution (unless the set is doubled); the ≤0.01 size rule
  must become a TOST-at-0.02 equivalence claim.
- Generalisation beyond biomedical full text, or to corpora without structural markup.
- That LLM-judged secondary qrels equal human judgment — they are labelled secondary
  throughout.

## 11. Flags on the task framing (verified discrepancies)

1. **"/rag/cache — only scifact cached" is stale.** `/rag/cache/datasets/` now holds all
   four BeIR datasets *and* their qrels (nfcorpus, scidocs, trec-covid included).
2. **"trec-covid … full text" needs care.** TREC-COVID *qrels* are over CORD-19, which
   ships full text — but the **BeIR trec-covid corpus in our cache is abstracts-only**
   (median ~1,200 chars ≈ 340 tokens, measured). The chunking plan's "closest to the PMC
   target / full text" row is wrong *as cached*; using it unmodified would recreate the
   scifact problem at 33× the embedding cost. Full text requires the CORD-19 release
   itself (verified downloadable, 3.66 GB), with the JATS-loss caveat of §4.
3. **Endpoints: 6 up, on :9001–:9006 specifically**; :9007/:9008 closed (TCP check).
   Matches "6 live", not the ":9001–9008" range as written.
4. **The citation option is *more* feasible than the corpus layout suggests**: `clean/`
   has `<back>` (ref-lists) stripped, but the raw `corpus/xml/` tree retains them for
   all 1.44M articles — and the manifest's `pmid_xml`/`doi_xml` give the join keys. The
   11.8% resolvability figure quoted in §3 is a pmid-only floor from one shard.
5. **One addition to the option list** the brief did not name: **TREC CDS 2014–16**, which
   this plan recommends as the human anchor precisely because its corpus is PMC OA JATS —
   the brief's option 1 examples (TREC-COVID, BioASQ, TREC-PM, Genomics) are all either
   non-JATS, abstracts-only, or access-encumbered.
6. Minor: the brief's "at chunk size 2048 every document is a single chunk" — measured:
   5,182 of 5,183 (one two-chunk doc). Same conclusion.

## 12. Decisions needed from the user

Items 1–4 stand; item 5 is partly discharged and two new ones join it.

1. Approve the three-leg shape, or trim (cheapest defensible core: Leg A + Leg B; Leg C
   is the first thing to cut, at the price of losing the only human-authored native
   signal).
2. Approve acceptance-test thresholds (§8) — they are proposals. Check 3's demotion to a
   diagnostic is the one change already made, on the evidence in §13.2.
3. Re-register the ≤0.01-nDCG size rule as TOST at 0.02, or fund ~2,000 Leg B queries.
4. Leg A topic variant (summary vs description — the pilot ran `summary` as primary and
   reproduced its ordering on `description`) and whether TREC-COVID is added as a second
   human leg after the pilot. It is still available: the argument that would have retired
   it is falsified (§13.2).
5. ~~Green-light Phase 0~~ — items 1–2 ran. **Green-light Phase-0 items 3–6**: the §7a
   oracle histogram (checks 2 and 5), the 50-query Leg B pilot, the 50-citance Leg C
   pilot, and persisting per-query arrays for an empirical σ_d. Nothing about Legs B and
   C has been measured yet.
6. **Leg A length cap** — keep or exclude the conference-abstract compendia
   (PMC4212304 at ~436k tokens, PMC2799006 at ~252k). §13.4 has the evidence that it does
   not change results either way; it is a decision about honesty of description, not about
   the numbers.
7. **Whether check 1's ≥80% threshold stands** once assembly makes it exact (§8 status
   table).

---

## 13. Phase-0 results (measured 2026-09-04)

Three runs on this host: the CDS coverage gate (step 1), the BM25 lead-only ablation
(step 2), and a real dense-pipeline chunking contrast written against a pre-registration
(step 3). Steps 2 and 3 share one pilot corpus: **10 CDS topics, 4,053 fetched judged
documents** (all grade≥1 for those topics plus 300 seeded grade-0 hard negatives per
topic, deduped). No store was written anywhere — step 2 used three scratch indices on the
dev tenant's Elasticsearch `:24043` and deleted them with a verifying listing; step 3 did
exact brute-force cosine in numpy and never contacted a vector store at all. Production
`:9200` and `:6333` were never touched.

### 13.1 The CDS gate: PASS, by wide margins

| quantity | measured | note |
|---|---|---|
| topics | **90** (30/year, all with ≥1 relevant, min 8) | zero cross-year duplicates, verified by comparing normalised description text |
| distinct relevant PMCIDs (grade≥1) | **12,307** | |
| (topic, relevant-doc) pairs | **13,807** | the correct denominator; the difference is cross-year re-judgement of the same articles |
| relevant per topic | **median 109, mean 153.4** | not ~494 — see §6 |
| judged per topic | ~1,260 | |
| fetchable, grade≥1 | **98.5%** (197/200), Wilson 95% CI 95.7–99.5 | |
| fetchable, grade-0 hard negatives | **95%** (57/60) | load-bearing: the design keeps grade-0 docs as negatives |
| judged body length | **median 4,097 tokens**; **80.2% over 2,048** (CI 74.1–85.2); 95.9% over 1,024; 50.3% over 4,096 | SFR tokenizer, n=197 |
| JATS parse, our own `jats.article_prose` | **237/237, zero failures, zero empty bodies** | the silent-empty-body mode did not trigger |
| already in local `/rag/oa` | **10.0%** (1,229 / 12,307) | so Leg A assembly is an S3 fetch, not a disk read |

**S3 path, taken from our own manifest and confirmed** — this plan's earlier S3 paths were
explicitly unverified:

```
https://pmc-oa-opendata.s3.amazonaws.com/PMC<id>.<ver>/PMC<id>.<ver>.xml
```

Of 20 ids fetched at `.1`, one has a `.2`. The bucket effectively carries a single earliest
version, so fetched copies sit close to the assessor-era text; the drift §4 accepts is
smaller than it feared.

One correction worth keeping visible, because it moved a number in the safe direction: the
first pass sampled with `shuf --random-source=<(yes)`, which put 28 of 60 draws in one
decile of the id space and none in two others. Every figure above comes from a properly
seeded draw. The bias had *understated* document length — the corpus is longer than first
reported, so the length precondition strengthened rather than weakened.

### 13.2 The important one: a wrong inference, and its correction

**What step 2 measured, and it stands.** BM25, lead-only index vs full chunked index,
10 topics, paired per-topic bootstrap:

| gap | measured | 95% CI | against |
|---|---|---|---|
| full − lead, recall@100 | **−0.006** | −0.029 … +0.019 | a ≥0.15 bar |
| full − lead, nDCG@10 | −0.017 | −0.097 … +0.060 | a ≥0.10 bar |
| whole-doc − lead, recall@100 | +0.059 | +0.018 … +0.098 | the one gap distinguishable from zero (9/10 topics) |

Also measured, and also still true: the evidence is *not* concentrated in the lead — of the
369 relevant documents the full index surfaced, 35.2% won the max-rollup on chunk 0, 30.1%
on chunks 1–2 and **34.7% on chunks ≥3**. CDS fails check 3 for a different reason than
"the answer is in the abstract".

**What step 2 concluded, and it is wrong.** From that null it inferred that *a chunking grid
on Leg A would be measuring differences smaller than the lead/full difference,
indistinguishable from zero*, and recommended demoting Leg A from anchor to sign-check
corpus. **That inference is falsified and the demotion is reversed.**

Step 3 ran the experiment the proxy had vetoed — four configs, real SFR embeddings, real
`bge-reranker-v2-m3`, predictions pre-registered before any embedding call. Summary
queries, grade≥1, means over the same 10 topics:

| metric | tok256/32 | tok512/64 (shipping) | tok2048/256 | whole4096 (head-4000) | lead512 |
|---|---|---|---|---|---|
| recall@100 | 0.3403 | 0.3349 | **0.3833** | 0.3811 | 0.3965 |
| nDCG@10 | 0.4952 | 0.4631 | **0.6000** | 0.5684 | 0.5703 |
| MRR@10 | 0.6111 | 0.6750 | **0.8033** | 0.7583 | 0.7417 |

Against the pre-registered falsification bar — **|Δ| ≥ 0.05 with either a paired-bootstrap
CI excluding zero or ≥7/10 sign consistency**:

- **tok2048 − tok512, nDCG@10 = +0.137, CI [+0.051, +0.225], 8/10 topics.** The
  load-bearing contrast: it clears the bar on point estimate, CI and sign simultaneously,
  replicates at grade≥2 (+0.090, CI [+0.036, +0.142]), and its CI lower bound survives an
  informal Holm correction across the six named contrasts.
- **tok2048 − tok256, recall@100 = +0.043, CI [+0.015, +0.077]**, 9/10 topics; at grade≥2
  **+0.077, CI [+0.038, +0.117]**, 8/10.
- **Check 4 passes 10/10** — every query changes its top-10 *doc* set between the size
  extremes, against a ≥25% bar, with top-10 overlaps of 2–5 documents out of 10.
- Not everything clears it: tok256 − tok2048 on nDCG@10 (−0.105) has a CI spanning zero and
  3/7 sign consistency. The recall@100 family carries that pair instead.

So the config contrasts on Leg A are **larger** than the BM25 lead/full gap the proxy
measured, and distinguishable from zero even at n = 10.

**Why the proxy failed — the transferable part.** Three independent reasons, any one of
which is sufficient:

1. **Wrong axis.** Lead-vs-full is a **coverage** contrast. The study's contrasts are
   **granularity** and **boundary** contrasts. "A lead-chunk baseline already solves this
   set" does not imply "all ways of cutting the rest of the document score alike".
2. **Wrong retriever.** BM25 only, while the pipeline is dense + reranking. Dense recall@100
   on the same set is 0.33–0.40 against BM25's 0.19–0.26 — the SFR retriever is nowhere near
   the regime the proxy measured.
3. **The instrument for the actual question was never run.** Check 4 is this plan's own
   pre-registered test for "can this leg separate configs". Step 2 did not run it. It passes
   10/10.

**And the finding that makes reason 2 concrete.** Dense retrieval *confirms* the step-2
negative at first stage: lead-only **beats** the full 512 index, tok512-full − lead512
recall@100 = **−0.062, CI [−0.084, −0.040]**. After reranking, full recovers: tok512_rr −
lead512_rr nDCG@10 = **+0.137** — read honestly, that one's CI is **[−0.005, +0.294]** and
**spans zero** (sign consistency 7/10 topics), so it clears the bar on sign but not on the
interval. The CI-clean form of the same reversal is grade≥2 **MRR@10 +0.299, CI [+0.074,
+0.542]**. (Two different +0.137s appear above and it is not a
copy-paste error: one is the dense tok2048−tok512 contrast, the other this reranked
full−lead one. They are unrelated quantities that happen to round the same.)

The mechanism is the point: **the reranker is the component that reads passages, and it
reverses "the lead suffices".** A BM25-only ablation has no reranker in it and was
structurally blind to the effect. That is why check 3 is now a reranker-load diagnostic
(§7b, §8) rather than a gate on a leg.

Two labels the step-3 run put on its own numbers, carried here so nobody over-reads them:

- **`whole4096` is really a head-4000-token arm.** The pilot's median document is 4,573
  tokens, so the truncation cuts roughly half the corpus. It is a coarse-granularity
  control, not the dense analog of step 2's whole-document control.
- **Reranked numbers rank arms; they do not grade the product.** `bge-reranker-v2-m3`
  sometimes *lowers* absolute nDCG against the SFR dense ordering on this clinical set
  (tok2048_rr 0.578 < dense 0.600). The informative reranked read is the lead-suffices
  reversal, not the level.

**What this does not license.** n = 10 topics; most of step 2's CIs span zero; step 3 ran 4
configs, not the 24 of the stage-1 grid, on one leg's pilot subset. The claim earned here is
narrow and sufficient: *document-level qrels are not blind to chunking, and Leg A is worth
building*. It is **not** "coarse chunks win" — that is §13.3's problem.

### 13.3 Leg A's measured bias profile — the reason not to anchor on it alone

The ordering is consistent across metrics and query variants (`description` reproduces it:
tok2048 nDCG 0.603 vs tok256 0.431): **tok2048 ≈ head4000 > tok512 ≳ tok256**, and
lead-only dense is competitive with all of them until the reranker runs. Coarse,
aboutness-carrying units win. The *gradient* is what is established; the 512-vs-256 step is
not — tok256 edges tok512 on this pilot (nDCG 0.4952 vs 0.4631, recall@100 0.3403 vs
0.3349), a local reversal inside the coarse-wins trend that 10 topics cannot resolve.

That direction is exactly what a topical, document-level judged set would be expected to
reward, which is the warning: **the coarse-wins result may itself be the aboutness bias that
Legs B and C exist to contradict.** So:

- Leg A is a full leg again, with this profile stated in the report rather than discovered
  by a reader.
- **No decision in [chunking-evaluation.md](chunking-evaluation.md)'s pre-registration table
  may rest on Leg A alone** — least of all the chunk-size decision, on which Leg A's bias
  points the same way as the cheaper option, the direction in which a biased instrument does
  the most damage.
- The three-leg concordance criterion (§4, option 4) is now doing real work rather than
  hedging: Legs B and C are deep-evidence by construction and plausibly push the other way.
  A disagreement is the finding, not something to average away.

The structural claim underneath the demotion — *"chunking changes which passage is
retrieved; document-level judgments cannot see that, so they cannot score it"* — is **true
of passage choice and false as a veto**. Granularity changes *document* ranking through the
score-rollup statistics and the amount of context carried per vector, and CDS's judgments
see that clearly. Step 2's own whole−lead = +0.059, in its own results, was the warning sign
that the rollup was doing visible work.

### 13.4 Assembly traps for whoever builds Leg A

Four, all measured, all cheap to hit:

1. **All three qrels files number their topics 1–30.** A naive concatenation silently
   collapses 90 topics into 30, and every result after that is meaningless.
   **Year-prefix the topic ids** (`2014_5`, `2015_18`, …) before merging; the merged file
   must hold 90 distinct topics. Verified separately that no description text repeats across
   years, so the 90 are genuinely distinct needs.
2. **The 2014 topics are not on the TREC page** that carries the qrels. They are at
   `http://www.trec-cds.org/topics2014.xml`. Record the 2015 variant chosen with them — the
   pilot used **Task A** (`topics-2015-A.xml` with `qrels-treceval-2015.txt`, a consistent
   pair); Task B was not used.
3. **Filter qrels to successfully fetched documents** — ~1.5% loss. Three relevants are
   genuinely withdrawn from the OA bucket (absent at versions 1–8 and under the modern
   `oa_comm|oa_noncomm|oa_other/xml/all/` prefixes). Filter, do not impute, and make the
   recall denominators the restricted qrels — identical across configs, so a gap can never
   be a coverage artefact.
4. **The length tail is conference-abstract compendia.** PMC4212304 is ~436k tokens
   (~213 chunks at size 2048); PMC2799006 is ~252k. Make the cap-or-keep decision explicitly
   and record it. The evidence says it is not a numerical question: excluding every document
   over 50k tokens (18 of 4,053) moved the step-2 ablation gap from −0.0058 to −0.0074 and
   left nDCG@10 unchanged. **Outliers neither manufacture nor mask the result** — but they do
   change what "a document" means in the report, and 13 of 197 sampled documents have empty
   *abstracts* for the same reason, which matters to any title+abstract-based filter.

### 13.5 What Phase 0 did not measure

Stated plainly so this section is not read as a finished pilot:

- **Nothing about Legs B or C.** No queries generated, no citances mined, no resolvability
  measured beyond the 11.8% pmid-only floor from the drafting day. Acceptance check 5 is
  unmeasured because there is nothing yet to measure it on.
- **The §7a oracle never ran**, so check 2 — the position-of-evidence histogram, the thing
  this design is built around — has no number on any leg. Step 2's chunk-position diagnostic
  (35.2% / 30.1% / 34.7%) is a *retrieval* statistic under BM25, not the oracle.
- **σ_d is still assumed, not measured.** The §6 power table remains an assumption; Phase-0
  item 6 did not run.
- **Check 1 is not callable** — 80.2% with CI 74.1–85.2 straddles its own line (§8).
- **One leg, one pilot, 10 topics, 4 configs.** Everything in §13.2 and §13.3 is CDS-only,
  and the stage-1 grid it speaks to has 24 cells.
