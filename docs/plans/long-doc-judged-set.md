# A long-document judged retrieval set for the chunking study

**Status: `PROPOSED` — Phase 0 run in full.** The set itself is still unbuilt: no fixture,
no qrels file, no index, nothing written under `/rag/`. What *has* run is **all six Phase-0
§9 items plus two unplanned ones** — the CDS coverage gate, the BM25 lead-only ablation, a
real dense-pipeline chunking contrast, the whole 24-config stage-1 grid, the §7a oracle on
all 90 topics, both leg pilots, an empirical σ_d, and a Leg B re-run against a real LLM —
all measured 2026-09-04 on this host, **plus three further runs on 2026-09-05**: the Leg B
stage-1 grid, a small-corpus chunk-granularity re-score, and a breadth × k run. **§13
records the first round, including one wrong inference that was acted on and then reversed;
§14 records the second, including one finding that gates the whole study: Legs A and B
resolve the same contrast with opposite signs; §15 records the third, whose first finding
is that every metric in §13 and §14 measured "found the right paper" rather than "found the
answering passage".** Acceptance checks 2 and 5 now have numbers. Check 4 passes on two legs.

The run reports are in [`results/`](results/) — copied verbatim, indexed in
[`results/README.md`](results/README.md). Every figure below is traceable to one of them.

Dating convention: claims marked 2026-09-03 were checked read-only on this host when the
plan was drafted; figures marked 2026-09-04 come from the Phase-0 runs and supersede any
drafted estimate they contradict; correction notes carry the date they were *written*;
everything else is an estimate or an open check and says so.

**What Phase 0 changed in this document, in one screen:**

| § | was | now |
|---|---|---|
| 1 | 12 of 24 stage-1 cells dead *on scifact* | dead on **every** planned BEIR dataset — `trec-covid`'s longest document is 925 tokens |
| 4, 13 | Leg A a plausible anchor | Leg A **keeps its place**, with a *measured* bias profile: it rewards coarse, aboutness-carrying configs |
| 6 | "~494 relevant/topic, CDS similarly deep" | that is TREC-COVID's number. CDS: **median 109 / mean 153.4**. The metric map survives; the figure did not |
| 8 | check 3 a gate on the leg | re-registered as a **diagnostic** (first-stage lead-chunk sufficiency = reranker load). Check 4 is the gate, and it passes 10/10 |
| 9 | ~80k embed tok/s, ±2× | **~164k tok/s** measured on six endpoints; costs recomputed, ±2× kept |
| 3, 14.3 | "resolving via pmid+pmcid+doi raises the 11.8% floor" | **falsified.** All three keys give **11.86%** against pmid's **11.85%** — 99.9% redundant. Leg C survives on volume, not rate |
| 6, 14.4 | σ_d assumed at 0.10–0.20 | **measured: 0.152** on Leg B's own queries. §6's "1,000 queries resolve δ = 0.02" does not hold; build at **~1,500** |
| 8 | checks 2 and 5 unmeasured | **check 2 PASS** (55.4% past token 1,024 against a ≥40% bar, on 90 topics); **check 5 PASS** on both legs, with its covariate needing re-registration |
| 9 | "semantic cells cost roughly double" | **~7×** a `token_window` config — it embeds a rolling sentence buffer per sentence (§14.1) |
| 7, 14.5 | circularity rule covers chunkers | it does **not** cover a *query construction* grading its own homework, and §14.5 is the evidence that it must |
| 2, 6, 7, **15.1** | position of evidence is a *property of the set* to be verified | it is **the measurement**. At one document every document metric is 1.0000 by arithmetic while the top chunk hits the gold section 55–65% of the time — `Gap@1` **+0.28 to +0.45**, resolved 15/15. Every metric above measured "found the right paper" |
| — , **15.2** | "1–100 documents, and 100 fit a 131k window" | full-text OA articles average **9,933 tokens**; 100 of them is ~**1.03M** and **0% fit**. A 131k window holds ~**13**. The regime needing retrieval work is **~13–100** |
| 14.5, **15.4** | the legs differ in breadth and bias | they differ in **difficulty**, by 15× — Leg B's queries were *written from* their gold passage. "Narrow queries saturate" is a statement about **easy** queries |
| 14.6 | overlap "not yet tested on Leg B" — cut it only if Leg B confirms | **Leg B confirms.** 12.5% − 0% = −0.0040, δ80 0.0081 below the 0.010 bar; ±0.0000 on recall@100 at ×11.5. The pre-condition is met |

**Recommendation in one paragraph.** Build a **three-legged judged set**: (A) **TREC CDS
2014–2016** — 90 human-judged topics whose corpus *is* PMC OA JATS full text, the same
markup, bucket and parser as the 500k-article target — as the human-judged anchor; (B) an
**LLM-generated deep-evidence query set (~~~1,000~~ **~1,500** queries — the size is now
measured, not assumed; §14.4) over our own 1.44M-article PMC OA
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
| In-corpus citation resolvability | measured **floor**: 11.8% of pmid-bearing refs resolve into our own corpus, median 4 in-corpus cited refs per citing doc (60-doc sample, one shard, **pmid only** — the sample also had 3,581 `doi` and 1,866 `pmcid` pub-ids, and the manifest carries `doi_xml`, ~~so resolving via all three raises this~~ — **falsified 2026-09-04: it does not.** 1,200 articles across all 256 shards, 64,606 refs: the union over pmid+pmcid+doi resolves **11.86%** of pub-id-bearing refs against pmid alone's **11.85%**, eight references' difference. §14.3) |
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

**And two more (2026-09-04, second round).** The citation-resolvability row is now a
measurement over 1,200 articles rather than a 60-document floor, and its stated expectation
is falsified (§14.3). The Scout row is **stale in the registry, not just in this plan**: no
Scout endpoint exists, and the model actually served at `mango.cels.anl.gov:8004` is
`Qwen/Qwen3.6-35B-A3B` — `docs/model-registry.md` and `/rag/config/unified.models.json` both
still name `RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic`, and sending that id fails.
Any Phase-1 harness should assert the served id against `/v1/models` at import, as the Leg B
re-run's client does, so a silent model swap is a loud error rather than a quiet change of
generator.

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
   text; apply ~~`g1_make_queries`-style filters including `title_answerable`~~
   **`title_answerable` + `names_document` only** (≥80% of query IDF mass in the cited
   doc's title → discard) — **amended 2026-09-04: the full `g1` filter set rejects 372 of
   400 citances as `not_a_question`, because a citance is a declarative sentence** (§14.3);
   record the IDF-overlap covariate vs the cited doc's title+abstract and report the
   low-overlap tertile separately. **Measured and reassuring:** median overlap 0.247,
   0/400 at the bar, and survival is *higher* in the low-overlap tertile (66.4%).
2. **Cited claims often sit in the cited paper's abstract** — the §2 failure mode.
   Mitigation is the position filter: keep only pairs whose best-supporting *section*
   (oracle, §7) is beyond abstract+intro. ~~Method-type citations (citances located in the
   citing paper's Methods section, citing protocols/software/datasets) are
   preferentially deep-evidence; oversample them.~~ **Measured 2026-09-04 and it is worse
   and better than written.** Worse: this threat is real and large — **42.8%** of Leg C
   pairs have their argmax in abstract+intro before filtering, 3.5× Leg A's rate, so the
   filter carries more weight here than on any other leg (**57.2%** survive it). The
   oversample-Methods hypothesis is **unresolved, not confirmed**: methods 61.3% vs intro
   56.2% survival, a +5.1 pp difference against a 21.4 pp power floor.
3. **Incomplete qrels.** Other corpus docs (especially reviews) also support the claim
   and get scored as non-relevant. For *paired config comparison* this mostly adds noise
   rather than bias, but it is not provably neutral (configs could differ in which
   unjudged docs they surface). Mitigations: multi-relevant qrels where one citance cites
   several in-corpus refs — **measured: 62% of citances co-cite ≥2 references, so this is
   the default shape of Leg C's qrels, not an enhancement** (§14.3); and this leg is a
   **validation slice, not the workhorse** — it confirms or contradicts the ranking, it
   does not carry the CIs. **That role has now become the study's most valuable unrun
   measurement**: Legs A and B contradict each other on the size axis (§14.5), and Leg C's
   independent bias profile is the only thing that can break the tie.

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
2. **Deep-section sampling:** ~~sample the source section uniformly from sections whose
   start offset is past the first 25% of the body and which are not
   abstract/intro/conclusion-titled~~ — **replaced 2026-09-04 by a positive rule** (§14.4):
   a section is eligible iff it is a body unit, **250–2,200 SFR tokens**, starts past
   **absolute token 1,024**, and contains ≥1 numeric result or method noun. Both halves of
   the change are load-bearing. *Absolute* depth replaces *relative* depth because "past
   25% of the body" is not the same constraint as "past 1,024 tokens" and let one source
   section in at token 554. And a **positive** rule replaces the title blocklist because a
   hand-written list leaks through one-word gaps forever — the round-1 blocklist had
   `funding` and `competing interest` but not `conflict of interest`, and a
   funding-acknowledgement query entered the set through that gap. Record section title,
   index, and char offsets in the fixture. Position-of-evidence is then **known by
   construction** — and was: 260/260 accepted sources start past token 1,024, median 5,098.
3. **Abstract-answerability rejection:** a separate verifier call sees only the query +
   the doc's title+abstract and answers "is this fully answerable from the above?" —
   reject if yes. This is the §2 rule made machine-checkable. (Verifier prompt hashed
   into the manifest, per the g1 convention.)
4. **Existing filters** apply unchanged: length, not-a-question, `names_document`,
   `title_answerable`, dedupe, optional critic. **Measured 2026-09-04, and this line is the
   one the plan was most wrong about: they contribute almost nothing.** Across 400
   round-2 queries the whole rule set fires on **8**, and the two new gates written to
   catch round 1's failures fire on **0** (`too_long_20`) and **2** (`not_specific`). They
   work as *prompt clauses*, not as filters — the generator simply never wrote a 21-word
   query. **So the abstract-answerability verifier in point 3 is carrying the entire
   protocol, and it is a single point of failure**: it fires on 33.2% and is wrong roughly
   one time in eight on a hand-read. The correct response is to strengthen it — show it the
   article's section titles as well as title+abstract, and ask it how many other papers
   could answer the query — not to add more rule filters.
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
| B: LLM | our `/rag/oa/corpus/xml` (docs with `body_chars>0`, `n_sections≥8`) | **~1,500** accepted (from ~2,900 generated) — *was ~1,000 from ~3,000; both halves re-measured 2026-09-04, §14.4* | source-doc known-item (primary); pooled LLM-judged multi-relevant (secondary) | ~1,500 source docs + pooled extras |
| C: citations | same | ~300 filtered citances | cited-doc(s), **multi-relevant by default, not as an enhancement** — 62% of citances co-cite ≥2 refs (§14.3) | ≤ ~1,000 |

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
2. ~~**σ_d must be measured, not assumed.**~~ **Measured 2026-09-04 — it is 0.152, and the
   band's *midpoint* was the wrong guess in both directions at different times.** Round 1
   read it off stage 1's persisted per-topic arrays (276 config pairs, 10 CDS topics):
   **0.156 dense / 0.173 reranked** — the *top* of the assumed 0.10–0.20 band. Round 1 then
   predicted Leg B's own σ_d would be **higher** still, because a Leg B query is a
   near-binary known-item where nDCG@10 has no ~109 relevants to average over. **That
   prediction is falsified.** Measured on Leg B's own 260 accepted queries: **0.152** at the
   comparable ×11.5 rung and **0.119** at the judged-only rung the grid actually runs on.
   The mechanism is worth keeping, because it will recur: nDCG@10 on Leg B sits at
   **0.92–0.99**, so most queries score identically under both configs and contribute a
   paired difference of *exactly zero*. The variance comes from the ~5% of queries that
   flip, not from the binariness. What this costs in sizing is in §14.4.
3. **Binary metrics need a different frame.** recall@1 / MRR-at-full-depth differences
   are driven by the *discordance rate* (queries where the two configs disagree), a
   McNemar-style analysis: with discordance rate p and effect δ, n ≈ z² · p / δ². These
   need more queries than nDCG for the same δ; the workhorse must carry them.

**Consequences.**
- ~~**Leg B at ~1,000 queries** resolves δ ≈ 0.02 nDCG@10 at 90% power *with* the Holm
  inflation for σ_d ≤ ~0.13~~ — **the stated condition is not met, so the claim does not
  hold** (2026-09-04). Every measured σ_d is above 0.13: 0.152 on Leg B's own queries,
  0.156/0.173 on Leg A. At those values the δ = 0.02 requirement under Holm is **1,173
  queries** (σ_d 0.156) to **1,429** (σ_d 0.173), and 1,000 queries resolve **δ = 0.0210**
  at σ_d = 0.152 — short of 0.02 on every reading, though only just on the last.
  **Build Leg B at ~1,500**, where δ90 under Holm is **0.0172** against measured config
  effects of 0.041–0.073, i.e. 2.4× to 4.3× headroom. §14.4.
- **The ≤0.01 rule should be re-registered as an equivalence test (TOST) with margin
  0.02**, or accept ~2,000+ queries. **The measurement settles this against the rule as
  written**: at the measured σ_d it needs **~4,700–5,700 queries** under Holm, which is
  three to four times the leg being built. Chasing 0.01 resolution with 1,000 queries would
  manufacture false "no difference" findings. Recommended, unchanged and now on evidence:
  TOST at 0.02.
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

**Ran 2026-09-04 on all 90 CDS topics** (2,161 pairs, 2,095 documents) and again on both leg
pilots. §14.2 has the histogram. Two implementation points are promoted from that run into
this specification, because a re-run that skips them measures something else:

- **Windowing is mandatory, not an optimisation.** The sidecar truncates at 4,096 tokens and
  3.0% of structural units exceed that. Silent truncation biases the argmax *toward evidence
  that sits early* — which is precisely the quantity being measured, so the instrument would
  read the answer it was built to test. Long units are windowed **client-side** and
  max-pooled.
- **The reconstructed document text must be byte-identical to the text the grid indexes**
  (`title \n\n abstract \n\n body`), or a unit's token offset is not in the same coordinate
  system as a chunk's. Verified on a 60-document sample, zero mismatches.

**Pre-registered circularity rule:** any *filtering or selection* on evidence position
(Leg C's filter, Leg B's deep-section sampling) operates on **structural units only —
never on any candidate chunker's cuts** — and every filtered result is reported beside
its unfiltered counterpart. The oracle is also never used to tune a chunker.

**Added 2026-09-05 — the clause this rule was missing.** The rule above stops a *chunker*
from grading its own homework. It says nothing about a **query construction** doing the
same, and §14.5 is the evidence that the gap is load-bearing rather than theoretical: Leg B's
queries are built to name a rare entity occurring in one deep section, document scores are a
max-rollup over chunks, and a small chunk carrying that entity is exactly what a max-rollup
rewards — so the leg is constructed to favour fine chunks in the same way Leg A is
constructed to favour coarse ones. Neither is a defect; both are unavoidable. What is a
defect is reading either leg's direction as the answer. So, pre-registered:

> **Every leg declares, before it is built, which retrieval behaviour its query construction
> mechanically rewards** — and no config may be pruned on a contrast whose direction that
> construction predicts, unless a leg with the *opposite* predicted bias agrees. A
> construction-biased contrast is reported with its bias named next to it, exactly as Leg A's
> aboutness bias is in §13.3.

This is the same discipline as the chunker clause and it is cheap: the answer is one sentence
per leg, written down before the queries exist.

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
| 2 | evidence depth | oracle 7a | ≥40% of (query, relevant-doc) pairs have argmax evidence past the first 1,024 tokens — **the strict reading: the argmax section *begins* past 1,024, pinned 2026-09-05**; ≤35% argmax in abstract+intro; every major section class represented |
| 3 | first-stage lead-chunk sufficiency — **a diagnostic, not a gate** (re-registered 2026-09-04) | ablation 7b (BM25) | reported, not gated: full-index recall@100 minus lead-only, and nDCG@10 likewise. The old thresholds (≥0.15 / ≥0.10) are retained only as the scale at which the first stage is doing the work rather than the reranker |
| 4 | config contrast is live | `fixed_tok256/0` vs `fixed_tok2048/0`, judged-only rung, one cheap build each | ≥25% of queries change their top-10 *doc* set; paired per-query deltas non-degenerate (σ_d > 0) |
| 5 | leakage bound (B, C) | IDF-overlap covariate — **needs re-registering, see below** | median query↔title+abstract IDF overlap below the `title_answerable` bar; low-tertile subset large enough to re-run the headline contrast (≥300 queries on Leg B) |

**Why check 2 had to pick a reading.** "Past the first 1,024 tokens" admits a strict form
(the argmax section *begins* past 1,024) and a lenient one (its *midpoint* does). On Leg A
they read **55.4%** and **78.7%** — both clear the ≥40% bar, so the choice changes no verdict
today, but it would on a weaker leg, and a threshold that can be satisfied two ways is not
pre-registered. **The strict form is the registered one.** Every figure in §14.2 is strict
unless it says otherwise.

**Why check 5's covariate needs re-registering (2026-09-04).** IDF overlap is the share of a
query's IDF *mass* that also occurs in the title+abstract, so a shorter query has fewer terms
and each shared term carries more of the mass. Cutting Leg B's median query from 29 words to
12 — a *deliberate realism fix*, nothing to do with leakage — moved the covariate from 0.400
to **0.496** and put 11.9% of accepted queries at or over the 0.80 bar where round 1's
maximum was 0.646. **The check as written will drift with any prompt change that alters query
length.** Two length-robust instruments were recorded alongside it and both say leakage is
fine: the count of **rare query terms absent from title+abstract** (median 2 per accepted
query; 83.8% ask for ≥1 thing the front matter never names) and unweighted Jaccard (median
0.032). Restate check 5 on the absent-rare-terms count, or normalise the covariate for query
length. This is decision 10 in §12.

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
| 2 | **ran, PASS on Leg A — its first number ever** | *(The oracle is `bge-reranker-v2-m3`, the production reranker — see §14.2; this measures what that model can find, not ground truth.)* **55.4%** of judged pairs have their argmax section starting past token 1,024 (bar ≥40%), topic-clustered CI **53.0–58.4**; **11.9%** in abstract+intro (bar ≤35%), CI 10.6–13.5; all six section classes win ≥66 pairs. 2,161 pairs, 90 topics. The power floors are **3.9%** and **2.1%** against distances of 15.8 and 23.0 points — unlike stage 1's interaction contrast, these bars were **reachable**, which is why the pass means something. **PASS on Leg B too**, by construction and independently: 100% of accepted sources start past token 1,024 and the oracle puts **87.7%** of argmaxes there with **5.4%** on the lead. §14.2 |
| 3 | **ran, re-registered** | −0.006 recall@100 (CI −0.029…+0.019), −0.017 nDCG@10 on the CDS pilot. Reported as the reranker-load diagnostic above, not as a gate |
| 4 | **ran, PASS on two legs** | Leg A: **10/10** queries change their top-10 *doc* set between the size extremes (bar ≥25%), top-10 overlaps of 2–5 out of 10 — measured on the real dense pipeline rather than BM25, and with 12.5%-overlap extremes rather than check 4's literal `/0`, a deviation recorded in the pre-registration (§13.2). Leg B: **260/260 = 100%**, at the **literal pre-registered pair and rung** (`fixed_tok256/0` vs `fixed_tok2048/0`, judged-only), mean top-10 Jaccard 0.438, σ_d = 0.181, mean δ = +0.0408. **Leg B is the first leg to clear check 4 with no deviation at all.** §14.4 |
| 5 | **ran, PASS on both legs; covariate needs re-registering** | Leg B accepted: median IDF overlap **0.496** vs title+abstract (bar < 0.80), **0/260** title-answerable. Leg C: median **0.247**, p90 0.520, **0/400** at the bar. The **≥300 low-tertile clause cannot be measured at pilot scale** and is recorded as a projection, not a pass — a tertile of 260 is 86. See the covariate caveat above |

## 9. Cost, and the cheapest useful version

Fleet facts used: 6 SFR endpoints up (:9001–:9006 — **exercised** 2026-09-04, not just
TCP-probed: ~108M tokens embedded in ~11 minutes with **0 retries** ≈ **164k tokens/s**
aggregate, roughly 2× the ~80k tok/s this plan assumed. Two measurements on different
fleets is still not a benchmark, and they are themselves ~2× apart, so **keep the ±2×
band** and read every hour below as an order of magnitude); crossencoder sidecar on
:50052; Scout LLM (`max_model_len` 60k) available for generation/judging; all experiment
stores = dev tenant only.

**Phase 0 — pilot (the de-risking buy; ~2–3 days wall, <2 GPU-h, one engineer).**
**All six items ran 2026-09-04**, plus three this list never planned: step 3 (the dense
contrast that reversed Leg A's demotion), the full 24-config stage-1 grid, and a Leg B
re-run against a real LLM. §13 has the first round, §14 the second. The GPU estimate held:
the oracle and both pilots together came to **~0.07 GPU-h** of crossencoder time; the
unplanned stage-1 grid is what cost real fleet time (1.57 h, §14.1).
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
3. **DONE — oracle on a sample** (GPU, well under 1 h): ~~2,000~~ **2,161** sampled
   (query, relevant-doc) pairs through the crossencoder → the position-of-evidence
   histogram, checks 2 and 5. **Widened from the 10-topic pilot to all 90 topics** before
   running, and that was not cosmetic: pairs inside a topic share a query, so a 10-topic
   sample has an effective n of 10 for anything the query drives. 187 s, 0 retries (§14.2).
4. **DONE, twice — Leg B pilot.** Round 1 ran ~~50~~ 70 queries on **Claude subagents**,
   because no LLM endpoint existed on this host at the time: GPUs 0–5 held the SFR fleet and
   6–7 were reserved. Those yields are protocol-shakedown numbers, not sizing inputs, and the
   asymmetry runs one way — a *stronger* generator producing bad queries is strong evidence
   the protocol is broken; producing good ones is weak evidence it works. Round 2 re-ran the
   whole protocol at **n = 400 against a real served LLM** with three fixes implemented, and
   that is the run the sizing rests on (§14.4).
5. **DONE — Leg C citances** with resolution via pmid+pmcid+doi. The 50-citance pilot was
   **extended to 400 mid-run**, because at n = 50 the survival rate had an 80%-power floor of
   19.0% against a 14.0% distance to its own bar — it could not have answered its own
   question. True resolvability and position-filter survival are in §14.3.
6. **DONE — per-query arrays persisted** from the stage-1 grid (24 configs × 4 conditions ×
   3 metrics, 276 config pairs) → empirical σ_d, then re-measured on Leg B's own queries
   (§14.4). The recommendation from round 1 — "re-measure σ_d on Leg B's own queries, it is
   the single highest-value follow-up" — was taken, and it falsified round 1's own
   expectation.

*Decision point with the user after Phase 0* — leg composition, thresholds, and sizes
confirmed against measurements. **This is now due**: §12 carries the open decisions, and
§14.5 carries the one that gates the rest.

**Phase 1 — full construction (≈1–2 weeks wall, mostly CPU/LLM, small embed cost):**

| item | estimate | basis |
|---|---|---|
| Leg A assembly (fetch ~30–60k judged docs, qrels filter, fixtures) | 1–2 days eng, no GPU | verified endpoints; S3 rate ~34–44 art/s measured previously |
| Leg B: **~2,900 generated → ~1,500 accepted**, 2-pass + verifier, **3 LLM calls/query** (*was ~3,000 → ~1,000 at 4–6 Scout calls*) | **~17.7 M LLM tokens**; ~**5.5 h** at 4 in flight, of which stage B's thinking budget is ~4.4 h; 2–3 days eng incl. review of a 100-query sample | **measured 2026-09-04** by linear scaling from the 400-item round-2 run (2.44 M tokens / 400 items, 65.0% yield, +~14% headroom for the retraction and review-article exclusions). Raise the in-flight cap before touching the prompt — the reasoning budget is what produced 12-word, single-clause, entity-named queries on the first try |
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
measured 164k tok/s, ~11 GPU-h for the 24-cell grid** (±2×; ~~semantic cells cost roughly
double — they embed the text again to find boundaries~~ — **corrected 2026-09-04: ~7×, not
2×.** `semantic` runs `pool_sentences=False`, so breakpoint detection embeds one overlapping
seven-sentence buffer *per sentence* — ~6× the corpus, **on top of** the config's own chunk
embedding. Project stage 2 from the **notional** figure, not from the actual: an identical-text
cache saved 60.4% across the four semantic cells only because they were run consecutively and
their buffers are identical except where the token cap bites. A single semantic cell run alone
pays the full 6×). ~~The step-3 run is the closest thing to a calibration~~ — **the stage-1
grid now is**: 968 M actual / 1,311 M notional tokens, 24 configs, 4,053 docs, **94.4 minutes
of fleet wall-clock, 0 retries in 186,647 requests**, against a 3 h ceiling (§14.1). If the
full grid is too dear, run it on Leg B + a short-list on Leg A — but see §14.5 before
choosing the short-list, because the axis most in need of pruning is the one the two legs
disagree about.

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
  must become a TOST-at-0.02 equivalence claim. **Measured, and it is now a floor rather
  than a guess**: δ = 0.02 under Holm needs ~1,200–1,430 queries and δ = 0.01 needs
  ~4,700–5,700 (§14.4).
- **That any one leg's direction on the chunk-size axis is the retrieval effect rather than
  its own construction bias.** Added 2026-09-04: Legs A and B resolve the same
  pre-registered contrast with opposite signs and non-overlapping intervals (§14.5). Until
  the study declares which query population it optimises for, the honest output on that axis
  is a recommendation *conditioned on query target*, not a single winning size.
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
   **Measured properly 2026-09-04: the floor was the ceiling.** Over 64,606 refs from 1,200
   articles across all 256 shards, the three keys together resolve **11.86%** and pmid alone
   **11.85%** — 99.9% redundant (§14.3). The leg is still feasible, but on **volume** (~7M
   candidate pairs against a need of ~300), not on a better resolution rate. Build the
   multi-key resolver for robustness; budget no extra yield for it.
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
5. ~~Green-light Phase 0~~ — **all six items ran** (§14). What replaces this item is the
   decision point §9 promised: leg composition, thresholds and sizes, now against
   measurements rather than estimates.
6. **Leg A length cap** — keep or exclude the conference-abstract compendia
   (PMC4212304 at ~436k tokens, PMC2799006 at ~252k). §13.4 has the evidence that it does
   not change results either way; it is a decision about honesty of description, not about
   the numbers.
7. **Whether check 1's ≥80% threshold stands** once assembly makes it exact (§8 status
   table).
8. **THE GATING ONE — declare which query population the study optimises for**, or accept
   that the chunk-size axis produces two conditional recommendations rather than one answer.
   **Declared 2026-09-06** — pointed, evidence-seeking questions from research agents; see
   [`results/design/SPEC-confirmation-run-r3.md`](results/design/SPEC-confirmation-run-r3.md) §1.
   That closes *this* item — the declaration. It does **not** by itself close §14.5's
   prune-gate: the confirmation run's queries are still Leg A's clinical narratives, and the
   "pointed" property is carried by its evidence-containment endpoint as a stated proxy
   (r3 §1.1). Pruning on the size axis waits on r3 §10 item 1.
   Legs A and B contradict each other on it with non-overlapping intervals and both
   directions are construction-predicted (§14.5). Everything downstream of "which configs
   survive to stage 2" waits on this. The current plan of record — keep the size axis intact,
   cut only uncontested axes — is in §14.6, with its cost stated.
9. **Leg C's two non-optional amendments** (§14.3): drop `not_a_question` from the citance
   filter set, and treat multi-relevant qrels as Leg C's default shape. The first is not a
   preference — the committed `screen_query` rejects **372 of 400** citances by definition,
   because a citance is a declarative sentence. Leaving it in place deletes the leg.
10. **Re-register check 5's covariate** on the absent-rare-terms count, or normalise the IDF
    overlap for query length (§8). As written it drifts with any prompt change that alters
    query length, and it just did.
11. **Leg B's three pre-assembly exclusions** (§14.4): retracted articles (2/400 sampled,
    both accepted), non-`research-article` sources (13.5% of accepted), and strengthening
    the abstract-answerability verifier rather than adding more rule filters. The first two
    are cheap and the case for them is a mechanism, not a p-value — the read-level evidence
    that non-research sources are worse is **not** resolvable at n = 2.
12. **Whether Scout is still the Phase-1 generator at all.** It does not exist on this host;
    the served model is `Qwen/Qwen3.6-35B-A3B` and the registry is stale (§3). Round 2's
    yields belong to that model, not to Scout.

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

### 13.5 What the first round did not measure — and what the second round did

Written when §13 was the whole of Phase 0. **Four of its five bullets are now false**, and
they are kept struck rather than deleted so the sequence is auditable: this is what was
honestly unknown on the morning of 2026-09-04, and §14 is what the rest of that day bought.

- ~~**Nothing about Legs B or C.** No queries generated, no citances mined, no resolvability
  measured beyond the 11.8% pmid-only floor from the drafting day. Acceptance check 5 is
  unmeasured because there is nothing yet to measure it on.~~ → **§14.3, §14.4.** 470 Leg B
  queries generated across two rounds, 6,024 usable citances mined from 1,200 articles,
  resolvability measured over 64,606 refs. Check 5 passes on both legs.
- ~~**The §7a oracle never ran**, so check 2 — the position-of-evidence histogram, the thing
  this design is built around — has no number on any leg.~~ → **§14.2.** It ran on all 90
  topics and check 2 passes with the distance to its bar four times its own power floor.
  (Step 2's chunk-position diagnostic — 35.2% / 30.1% / 34.7% — remains a *retrieval*
  statistic under BM25, not the oracle, and should not be quoted as one.)
- ~~**σ_d is still assumed, not measured.** The §6 power table remains an assumption; Phase-0
  item 6 did not run.~~ → **§14.4.** Measured twice, on two different legs, and the second
  measurement falsified the first's stated expectation about the second.
- **Check 1 is still not callable** — 80.2% with CI 74.1–85.2 straddles its own line (§8).
  This one stands unchanged; nothing in round 2 touched it, and only assembly can.
- ~~**One leg, one pilot, 10 topics, 4 configs.**~~ → **all 24 stage-1 cells ran on that
  pilot** (§14.1), and a second leg now exists at n = 260. **But the honest successor to this
  bullet is worse, not better**: with two legs measured, they *disagree* (§14.5). The
  n = 10 caveat on everything in §13.2 and §13.3 is unchanged.

---

## 14. Phase-0, second round (measured 2026-09-04)

Four more runs on the same day, in this order: the **24-config stage-1 grid** on the Leg A
pilot; the **§7a oracle** on all 90 CDS topics plus the **Leg B and Leg C pilots** and an
empirical **σ_d**; and a **Leg B re-run** against a real served LLM. Reports verbatim in
[`results/`](results/).

Store discipline, unchanged and verified in every run: no Qdrant or Elasticsearch client is
constructed anywhere in these harnesses — retrieval is exact brute-force cosine over
in-memory vectors. Production `:6333`/`:9200` were never contacted; the dev-tenant stores
`:24041`/`:24043` were *read* to snapshot them and are SHA-256 byte-identical before and
after. GPUs 6 and 7 were never used and no endpoint was started on them.

> ### Read this first
>
> **Three of these results are passes and one is a problem that outranks all of them.**
>
> - Check 2 finally has a number and it **passes with room** (§14.2). Check 4 passes on Leg B
>   at its literal pre-registered pair, which no leg had managed (§14.4). σ_d is measured and
>   **cheaper than feared** (§14.4).
> - Overlap **buys nothing at any chunk size** on Leg A, and that contrast is adequately
>   powered — the largest actionable finding in the whole of Phase 0 (§14.1).
> - **But Legs A and B resolve the same pre-registered contrast with opposite signs and
>   non-overlapping intervals**, and both directions are predicted by how each leg's queries
>   were built (§14.5). No config may be pruned on either. That is the gate on stage 2.
>
> One process finding travels with all four runs and is worth more than any single number:
> **a contrast's power floor is computed before its threshold is committed to.** Stage 1
> learned this the expensive way (§14.1); the Leg B round applied it to fifteen readings and
> **six of them failed the check and are written as unresolved, not as nulls**.

### 14.1 Stage 1 — the 24-config grid, run on the Leg A pilot

All 24 cells of `chunking_compare_7way.STAGE1_CONFIGS`, imported rather than re-declared,
against a pre-registration written before any embedding call. **968 M tokens actually
embedded (1,311 M notional), 94.4 minutes of fleet wall-clock, 0 retries in 186,647
requests.** The grid itself, its ranking and the config-level readings belong to
[chunking-evaluation.md](chunking-evaluation.md); what this document needs from it is four
things.

**The run reproduces step 3 exactly.** Three grid cells *are* step-3 configs. Re-chunked and
re-embedded from scratch — step 3's vectors were never read — they produce **byte-identical
chunk files** (SHA-256) and **max |diff| = 0.0000** across all twelve metric × config values.
That establishes more than harness correctness: the embedding fleet, batched differently
across six endpoints, is reproducible enough not to be a confound, and the repo pinning held
against the `/rag/repos` editable-install meta-path finder (which runs *before* `sys.path`, so
`PYTHONPATH` does not win — it had to be stripped in the parent and re-asserted in every
worker).

**The cost model in §9 is sound.** The chunk-embedding leg came in at **161k tok/s against
the 164k model — 98%** — with zero retries across 130,091 requests. Read the other rates with
care: the overall 171k and the breakpoint pass's 212k are *not* comparable to the model,
because the breakpoint pass embeds ~272-token buffers and is request-bound rather than
token-bound. Per-config rate also falls with chunk size, as a request becomes item-bound
rather than token-bound.

**Semantic is ~7×, not 2× — the correction now folded into §9.** And it is simultaneously the
worst-scoring kind on this leg and the largest index: 14.3 chunks/doc against `token_window`'s
4.2 at the same nominal size. That is the worst cost/benefit position in the grid, and per
§13.3 it still may not be pruned on Leg A alone.

**The process lesson, which changed how every later reading in Phase 0 was written.** Stage
1's *pre-registered primary contrast* — the size × overlap interaction, posed as a
difference-of-differences between the 256 and 2048 rungs — has an 80%-power floor of **0.213
nDCG against the 0.05 bar written for it**. At n = 10 it **could not have returned a positive
answer whatever the truth**, and that was knowable in advance from the contrast's variance
structure: a DiD compounds the variance of four cells. It is recorded as **structurally
unanswerable, not as a null** — those are different claims and only one of them is true here.
The slope form of the same question resolves four times better (floor 0.056) and should be
the primary contrast in stage 2.

### 14.2 The §7a oracle — an acceptance check that had never been run

2,161 (topic, relevant-doc) pairs, **2,095 documents, all 90 CDS topics**, 21,735
crossencoder window-pairs in 187 s. Bootstrap CIs are over **topics**, the clustering unit;
the pooled Wilson interval is the over-confident one and is reported beside it.

| statement | pairs | pooled | topic-mean | bootstrap 95% (topics) |
|---|---:|---:|---:|---|
| argmax section **starts** past token 1,024 *(the registered strict reading)* | 1,197 | **55.4%** | 55.8% | **53.0–58.4%** |
| argmax **midpoint** past token 1,024 *(lenient)* | 1,700 | 78.7% | 78.8% | 76.8–80.8% |
| argmax in **abstract+intro** | 258 | **11.9%** | 12.0% | **10.6–13.5%** |
| argmax is unit 0 (the lead) | 64 | 3.0% | 3.1% | 2.3–3.8% |
| argmax past 50% of the document | 637 | 29.5% | 29.6% | 27.4–31.8% |

**Check 2 passes on Leg A**, and the reason it *means* something is the power floor: **3.9%**
against a 15.8-point distance to the ≥40% bar, and **2.1%** against a 23.0-point distance to
the ≤35% head bar. Unlike stage 1's interaction contrast, these bars were reachable. The
margin over the best head unit is median **+0.163**, and **88.0%** of pairs have a non-head
unit that strictly beats *every* abstract/intro unit. Grade makes no difference (grade 1:
54.3%; grade 2: 56.6%).

Per-topic spread is wide and should be stated: min 20%, median 58%, max 88%, with **8 of 90
topics** individually below the 40% line. The check is a set-level property, not a per-topic
guarantee.

Two caveats travel with the pass, and a third observation the plan did not anticipate:

- **The oracle *is* `bge-reranker-v2-m3`**, the model that gates the production pipeline. §7a
  accepts that deliberately, but it means check 2 is a statement about what the reranker can
  find, not about ground truth. §7b, the model-free control, reads −0.006 recall@100.
- **The windowing detail is load-bearing** and is why it is now written into §7a: the sidecar
  truncates at 4,096 tokens, 3.0% of units exceed that, and silent truncation would have
  biased the argmax toward early evidence — the very quantity being measured.
- **`other` is the largest winning class at 39.1%**, dominated by untitled body leads and
  case-report / case-presentation sections. On a clinical-decision-support corpus that is
  what one would expect to find; it is not a parser artefact, the classifier simply has no
  CDS-shaped class for it.

### 14.3 Leg C — viable on volume, with a plan assumption falsified

**The falsified assumption first.** §3 recorded a pmid-only 11.8% floor and predicted that
resolving via pmid + pmcid + doi would raise it. Scanned properly — 1,200 citing articles
seeded across all 256 shards, joined against a full manifest index — it does not:

| denominator | refs | resolved | rate |
|---|---:|---:|---:|
| all `<ref>` elements | 64,606 | 6,767 | 10.47% |
| refs carrying ≥1 usable `<pub-id>` (88.3%) | 57,041 | 6,767 | **11.86%** |
| refs carrying a **pmid** | 51,994 | 6,759 | 13.00% |
| refs carrying a **doi** | 55,863 | 6,721 | 12.03% |
| refs carrying a **pmcid** | 26,253 | 6,757 | 25.74% |

**The union over three keys resolves 6,767 references; pmid alone resolves 6,759 — eight
fewer.** On the common pub-id-bearing denominator that is **11.86% against 11.85%**. The keys
are 99.9% redundant, because the same PMC records that populate our corpus are the ones that
populate a reference's pub-id block. The pmcid *rate* of 25.74% looks better only because
carrying a PMC id is itself a proxy for being in PMC OA — a selected subpopulation, not a
better key. **Build the multi-key resolver anyway** (it costs nothing and is robust to
individual missing ids) but **budget no extra yield for it.**

**Leg C survives on volume, which is what actually mattered.** 6,024 usable (citance,
cited-doc) pairs from 1,200 citing articles — on the order of **7 million** across the corpus,
against a need of ~300. **57.2%** survive the position filter (n = 400, Wilson 52.4–62.0),
which works out at ~2.9 surviving pairs per citing article: **~105 citing articles' worth of
mining** for 300 filtered citances. The cited documents are properly long (median **9,330**
tokens, only 2 of 400 under 1,024).

The filter is doing heavy lifting and the plan's rule to report filtered *and* unfiltered
results matters more here than anywhere: **Leg C's evidence sits in abstract+intro 42.8% of
the time before filtering** — three and a half times Leg A's 11.9%. That is Option 2's threat
2 arriving exactly as written, and exactly why the filter exists.

**Two amendments that are not optional** (decision 9 in §12):

1. **`g1`'s `not_a_question` filter destroys this leg.** Running the committed `screen_query`
   over 400 citances rejects **372 of them as `not_a_question`** — because a citance is a
   *declarative sentence*, which is what a citance is. Only 4 of 400 pass all rules. §4's
   "apply `g1_make_queries`-style filters including `title_answerable`" must be re-registered
   as **`title_answerable` + `names_document` only**, or the citances must be converted to
   questions, which reintroduces an LLM into the one leg whose whole selling point is that no
   LLM wrote it. `names_document` does real work and stays: it fires on **23/400 (5.8%)**,
   catching citances that say "the authors", "et al." or point at a figure.
2. **62% of citances co-cite ≥2 references in the same sentence.** The sentence supports every
   one of them, so single-cited-doc qrels are *systematically* incomplete on Leg C, not
   incidentally. §5 already anticipated multi-relevant qrels "where one citance cites several
   in-corpus refs"; this measures how often that path is needed — **most of the time**. It is
   the default shape of Leg C's qrels, not an enhancement.

Leakage on Leg C is low (median IDF overlap **0.247** against the cited paper's
title+abstract, **0.076** against the title alone, 0/400 at the 0.80 bar), and survival is
*higher* in the low-overlap tertile (66.4%, n = 134) than overall — the leakage covariate and
the position filter pull the same way, which is the reassuring sign.

**And one thing this leg may not claim.** The plan's suggestion to **oversample Methods-located
citances** as preferentially deep-evidence is **unresolved, neither confirmed nor refuted**.
Survival by citing-section class runs results 65.0% / methods 61.3% / intro 56.2% /
discussion 55.1% / other 54.5%; the methods-vs-intro difference is **+5.1 pp against an
80%-power floor of 21.4 pp** — the instrument is 4.2× too coarse. It is stage 1's
difference-of-differences trap in miniature, and it is written as unresolved.

### 14.4 Leg B — the fixes, the yield, σ_d, and the sizing

Round 1 ran 70 queries on Claude subagents because no LLM endpoint existed on this host; it
found ten bad accepts among 42 and proposed three fixes. **Round 2 implemented all three and
re-ran the protocol at n = 400 against `Qwen/Qwen3.6-35B-A3B`** at
`mango.cels.anl.gov:8004`, one item per LLM call rather than batches of 14 — so cross-item
bleed is excluded by construction rather than by instruction. 1,208 requests, **0 failures**,
~43.5 minutes.

**Yield: 65.0% (260/400) automated**, ~**52%** after a hand-read of a seeded 30. Against a
nominal 50% floor that reading is **resolvable** (power floor 6.7% against a 15.0-point
distance) — where round 1's 60% against the same floor had a power floor of 16.4% and was
structurally unable to answer its own question.

**What the fixes bought, and what they did not:**

| fix | round 1 | round 2 | resolvable? |
|---|---|---|---|
| **3 — shape.** ≤20 words, no compound clause | median 29 words, max 45 | **median 12**, max 16 | **yes** — floor 2.65 w against an 18.7 w move |
| **3 — compound queries among accepted** | 64.3% | **0 / 260** | **yes** — floor 20.7% against 64.3% |
| **1 — entity-stripping**, the mechanism behind 6 of round 1's 10 bad accepts | 6/10 bad accepts | **0/30 in the read** | **no** — floor 9.2% against a 4.8% move. **Direction only** |
| bad-accept rate on read | 23.8% | 20.0% | **no** — floor 27.5% against 3.8%. **Unresolved** |

**The honest reading is that the prompt did the work and the filters are a backstop that
almost never fires.** `too_long_20` fired on **0/400** and `not_specific` on **2/400**; the
rule gates together fire on ~2% of queries. Round 1's rule filters read 0/70 and that was
reported as "zero discriminating power"; round 2's read 8/400 and the conclusion is the same
one. **The abstract-answerability verifier is carrying the entire protocol** — it fires on
33.2% and is wrong perhaps one time in eight on the hand-read. Record that as what it is: a
**single point of failure** in the leg's quality control, and the binding constraint on Leg B
quality. Strengthening it beats adding rule filters (decision 11 in §12).

**Fix 2 bites hard and is not a strict improvement.** The positive section rule — body unit,
250–2,200 tokens, starting past **absolute** token 1,024, containing ≥1 numeric result or
method noun — rejects **1,491 of 2,655** candidate sections the deleted 16-word title
blocklist would have passed, and kills three of round 1's four defective sources by
construction. But read the other off-diagonal cell too: it **admits 153 sections the blocklist
rejected** (17.5% of everything it accepts), overwhelmingly `Conclusion` and `Supporting
information`, and **two of the six bad accepts in the hand-read came from exactly there**. The
lesson is not "positive rules good, blocklists bad" — it is that the positive rule is
**necessary and not sufficient**, and the residue needs a small number of *principled*
exclusions rather than a growing list of section titles.

Three of those exclusions are needed before assembly and are decision 11: **retracted
articles** (2/400 sampled, both accepted — indefensible in a judged set, and nothing in the
protocol filters them), **non-`research-article` sources** (**35 of 260 accepted = 13.5%**,
Wilson 9.8–18.1: 30 reviews plus an editorial, a commentary, a discussion and two brief
reports — a review has no findings of its own, so a query generated from one is a survey query
with a single document marked relevant), and a stronger verifier. The case for excluding
non-research sources rests on the **13.5% prevalence and the mechanism**, *not* on the read
data: both non-research sources in the read-30 were among the six bad accepts, but 2/2 against
4/28 is **not resolvable** — a naive Wald floor calls it resolvable only because the standard
error of a proportion at p = 1.0 with n = 2 is *zero*, and the Wilson interval on 2/2 is
34–100%.

**σ_d, measured on Leg B's own queries**, five stage-1 grid cells over 260 accepted queries,
against a distractor pool drawn from the same distribution as the sources (a topically
mismatched pool would make every query trivial and bias σ_d *down*):

| metric | ×0 rung (judged-only, where the grid runs) | ×11.5 rung | Leg A / stage 1 (CDS, 276 pairs) |
|---|---:|---:|---:|
| nDCG@10 | **0.119** (0.078–0.181) | **0.152** (0.098–0.214) | **0.156** dense / 0.173 reranked |
| MRR@10 | 0.132 | 0.178 | 0.308 |
| recall@100 | 0.062 *(meaningless at this rung)* | 0.131 | 0.035 |

**Round 1's expectation is falsified in the direction that makes the plan cheaper.** It
predicted Leg B's σ_d would be *higher* than Leg A's because Leg B is near-binary known-item;
it is the same or lower. §6 records the mechanism. Three caveats travel with the number: it is
a median over 10 config pairs from 5 cells, all in the `token_window` family, against stage
1's 276 pairs from 24 cells across four kinds; the ×0-rung recall@100 is meaningless (100 of
400 documents); and the paired SD carries its own sampling error, though far less than round
1's n = 10 topics did.

**What n buys** (δ90 = (z_α + z_β) · σ_d / √n; Holm's tightest step over 24 configs is
z = 3.097):

| n accepted | σ_d | δ90 unadjusted | δ90 under Holm |
|---:|---:|---:|---:|
| 260 (this pilot) | 0.152 | 0.0306 | 0.0413 |
| 1,000 | 0.152 | 0.0156 | **0.0210** |
| **1,500** | **0.152** | **0.0127** | **0.0172** |
| 1,500 | 0.119 (×0 rung) | 0.0100 | 0.0135 |

Against the config effects Leg B actually shows — the size extremes differ by **0.041 (×0)**
to **0.073 (×11.5)** on nDCG@10 — **1,500 queries give 2.4× to 4.3× headroom**, and the
pilot's own 260 already resolves the primary size contrast. **Build at ~1,500.** 1,000 lands
at 0.0210, which misses §6's stated 0.02 but only just; the ~1,200–1,430 band in §6 comes from
Leg A's σ_d rather than Leg B's own, and is the conservative reading of the same arithmetic.

**Check 4 passes at the literal pre-registered pair and rung** — `fixed_tok256/0` vs
`fixed_tok2048/0`, judged-only: **260/260 = 100%** of queries change their top-10 document set
(bar ≥25%), mean top-10 Jaccard 0.438, σ_d = 0.181, mean δ = +0.0408. Leg A's pass used
12.5%-overlap extremes on 10 queries; **Leg B is the first leg to clear check 4 with no
deviation at all.** (The 100% figure is read on its **Wilson lower bound of 98.6%**, because
the Wald standard error at p = 1.0 is degenerate — the same trap as the 2/2 above.)

**One property a reader must know before quoting any absolute number from Leg B: it is an
easy task.** nDCG@10 sits between **0.92 and 0.99** on every config at every rung tested and
94–98% of queries put the gold document at rank 1 — the direct consequence of fix 1's
rare-entity requirement. It did not flatten the config contrast, but it does mean the headroom
on this leg is 1–8 nDCG points against Leg A's 0.47–0.63 range. **An absolute score from Leg B
is not comparable with one from Leg A, and a "0.02 improvement" means very different things on
the two.**

### 14.5 The finding that gates the study: the legs disagree, and both are biased

**On recall@100 — the one contrast where both legs resolve — the signs are opposite and the
intervals do not overlap.**

| contrast: size 2048 − size 256 | Leg A (CDS, n = 10 topics) | Leg B ×0 (n = 260) | Leg B ×11.5 (n = 260) |
|---|---:|---:|---:|
| nDCG@10 | +0.0904, CI [−0.028, +0.220] | **−0.0408**, t = −3.63 | **−0.0733**, t = −5.53 |
| power floor for it | 0.210 → **not resolvable** | 0.0315 → **resolvable** | 0.0371 → **resolvable** |
| recall@100 | **+0.0432**, CI [+0.015, +0.076] → **resolvable** | −0.0038 → not resolvable | **−0.0346**, t = −3.05 → **resolvable** |

Two precisions before anyone quotes this:

- **The nDCG@10 row is not a head-to-head.** Leg A's point estimate is the *larger* one
  (+0.090 vs −0.041); it simply cannot exclude zero at n = 10. That row is a Leg B result and
  a Leg A **non-result**, and presenting it as a contradiction would be dishonest.
- **The recall@100 row is the head-to-head, with one provenance caveat.** Leg B measured the
  literal `fixed_tok256/0` vs `fixed_tok2048/0` pair. Leg A's **+0.0432 [+0.0151, +0.0764]**
  is the *overlap-averaged* size main effect from the stage-1 pre-registered family, not that
  literal pair; the literal `/0` cells in stage 1's grid read 0.3374 and 0.3816, a **+0.0442**
  point estimate in the same direction, but **no CI was published for that specific pair**.
  The conclusion is unaffected — same sign, same magnitude — but the two numbers are not the
  same estimator and should not be presented as though they were.

**Why neither leg is simply right.** This is the part that matters, and it is not hedging:

- **Leg A's judgments are document-level and topical.** Aboutness is declared in the title and
  abstract, so a coarse, aboutness-carrying unit is rewarded — §13.3 measured that bias
  directly.
- **Leg B anchors a rare entity in one deep section and scores by max-rollup over chunks.** A
  small chunk carrying that entity is exactly what a max-rollup rewards. Fix 1 — the
  specificity requirement that made round 2's queries good — is *also* the mechanism that
  makes fine chunks win.

Each leg is constructed to favour the direction it reports. That is not a defect in either;
it is what "a leg with an independent bias profile" means, and the three-leg design exists
precisely so the biases can be seen against each other. What it forbids is a shortcut:

> **No config may be pruned on either leg's direction.** Not on Leg A's coarse-wins, and not
> on Leg B's fine-wins. The two legs answer different questions and give opposite answers, and
> the study must declare which query population it is optimising for before it prunes a single
> config on the size axis.

And it exposes a gap in this plan's own rules, now closed in §7: **the circularity rule covers
a chunker grading its own homework and has no clause for a query construction doing the
same.** This run is the evidence that it needed one.

**Leg C is now the tiebreak, and it is the only leg that can be.** Its queries are
human-authored (a citance is a real author asserting real support), no LLM wrote them, and its
construction bias is different from both — which is exactly the role §4 option 4 assigned it,
and the first time that role has had work to do rather than hedging to do. It has not been run
against the grid. That is the highest-value unrun measurement in the study.

### 14.6 The plan of record: keep the contested axis, cut the uncontested ones

Given §14.5, the group's chosen direction — recorded here as the current plan of record, to be
confirmed at the §12 decision point rather than assumed:

**Do not prune to ~4 configs.** Keep the **size** axis intact and produce recommendations
**conditioned on query target** — coarse for topical/aboutness retrieval, fine for
entity-anchored known-item retrieval — rather than declaring one winning size the evidence
does not support. Cut only on:

- **uncontested axes** — **overlap** is the candidate: negative on nDCG at all four rungs on
  Leg A, |Δ recall@100| ≤ 0.0033 everywhere, at a cost of up to 1.32× the vectors for an
  index's whole lifetime, and adequately powered on that leg. **But it has not been tested on
  Leg B**: the five-cell σ_d run included a `tok512/25%` cell and published no overlap
  contrast from it. Cut overlap **if Leg B confirms the null**, not before.
- **cost-effectiveness** — **semantic** is the candidate: worst-scoring kind on Leg A, ~7× the
  embedding cost of a `token_window` config, and a 3.4× larger index. If GPU budget forces a
  cut in stage 2's scope, this is where the cost is.

**State the cost of this choice rather than discovering it.** Stage 2 was budgeted at ~4
surviving configs × 3 rungs = 12 index builds. **24 configs × 3 rungs is 72 builds — about 6×
that budget.** And keeping more contrasts costs power twice over: Holm's correction tightens
with every contrast added to the family, so the same n resolves a larger δ. The trade being
made is explicit: **more budget and less resolution per contrast, in exchange for not pruning
the one axis the evidence cannot yet adjudicate.** If that trade is refused, the alternative
is not "prune anyway" — it is to run Leg C first and let a third bias profile break the tie.

> **Updated 2026-09-05 — the overlap condition is now met.** The Leg B grid ran all 24 cells
> at ×0 and the 12 `token_window` cells at ×11.5:
> [`results/stage1-legB/`](results/stage1-legB/RESULTS-stage1-legB.md). **12.5% − 0% =
> −0.0040**, CI [−0.0098, +0.0015], with δ80 = 0.0081 *below* the X_B = 0.010 bar — a powered
> null, on a leg whose bias runs opposite to Leg A's. At ×11.5 overlap moves recall@100 by
> **exactly 0.0000** at every size. The pre-condition written above — *cut overlap if Leg B
> confirms the null* — is satisfied.
>
> Two caveats travel with the cut. The `sentence`/`words` rows labelled 12.5% carry ≈8.9%
> effective overlap (their packer converts at 2.5 chars/token against a measured 3.50), so
> only the `token_window` rows tested the fraction exactly. And the weaker ×11.5 nDCG@10
> replication (−0.0078, CI [−0.0162, −0.0002], δ80 0.0114) is **above** the bar and is a
> direction, not a result.

---

## 15. Post-#487 findings (measured 2026-09-05)

Three more runs after §14: the **Leg B stage-1 grid**, a **small-corpus re-score** of the
Leg B ×0 rung at chunk granularity, and a **breadth × k** run. Reports, pre-registrations and
machine-readable outputs in [`results/`](results/README.md), whose index also lists which
earlier conclusions each one revised.

Store and fleet discipline unchanged and independently gated in each run: no Qdrant or
Elasticsearch client is constructed anywhere in the harnesses, `:6333`/`:9200`/`:24041`/`:24043`
were not contacted at all, and GPUs 6 and 7 sat at 0 MiB before and after. Both later runs
additionally assert their repo commit, probe the served model live on all six endpoints and
refuse to run if the six disagree, and hash their corpus before embedding — the gaps
[`design/ANSWER-provenance-and-repro.md`](results/design/ANSWER-provenance-and-repro.md)
found in the earlier runs.

### 15.1 The passage/document gap — the study has been measuring the wrong half

`Gap@k = DH@k − PH@k`: the document metric this study already reports, minus the question
*did any of the top-k chunks actually overlap the gold section*. Measured on the same query,
the same document and the same embedding, n = 260. The judged set makes it a real passage
task: **0 of 260** gold sections are the abstract, median gold section **1,233** tokens
(13.5% of its document), median position **69.3%** of the way through a median **8,778**-token
article.

| N | `DH@1` | `PH@1`, tok256 / 512 / 1024 / 2048 | **`Gap@1`** | reading |
|---:|---|---|---|---|
| **1** | **1.0000** (arithmetic) | 0.5692 / 0.6308 / 0.5500 / 0.6462 | **+0.4308 … +0.3538** | RESOLVED |
| 10 | 0.9308–0.9846 | 0.5308 … 0.6346 | +0.4115 … +0.2962 | RESOLVED |
| 100 | 0.9154–0.9769 | 0.5269 … 0.6308 | +0.4154 … +0.2846 | RESOLVED |

**15 of 15 readings RESOLVED** — `|mean| ≥ 0.05`, CI excludes 0, `δ80 ≤ |mean|`, against a
power floor of ≈0.087 that every effect clears by 3–5×. Read the `N = 1` row twice: **every
document-level metric is 1.0000 by arithmetic** while the top chunk is wrong 35–45% of the
time.

**It is a top-1 phenomenon, not a recall one.** At `k = 10` the gap collapses to
+0.019 / +0.023 / −0.012 / −0.015 (N = 100) — with ten chunks in hand you touch the gold
section somewhere, `PH@10 ≈ 0.97–0.99`. The pre-registration aimed two predictions at `k = 10`
(P1 `Gap@10 ≥ 0.15`; P2 it grows with chunk size) and **both failed**. They are recorded as
failures; the `k = 1` reading pre-registered in the same family resolves decisively.

**What this changes here.** Every metric in this study before 2026-09-05 — §13.2's +0.137 and
all of §14 included — measured *"found the right paper"*, not *"found the answering passage"*.
For a consumer that reads ten passages the document metric is not badly misleading; for a
person, a citation, a snippet, or an agent acting on the first hit it **overstates quality by
roughly 30–45 points**. §6's metric map must carry a passage-level metric beside every
document metric on every leg, and §7's position-of-evidence measurement stops being a
qualification on the set and becomes the thing being measured.

**Also measured: chunk size stops mattering at the document level as the corpus shrinks, and
goes on mattering, undiminished, at the passage level.**

| | N = 1 | N = 10 | N = 100 | N = 400 | N = 5,000 |
|---|---:|---:|---:|---:|---:|
| document-level spread (`nDCG@10`) | **0.0000** | 0.0255 | 0.0403 | 0.0408 | 0.0732 |
| passage-level spread (`H_B@4096`) | **0.1538** | 0.1731 | 0.1808 | 0.1808 | not measured |
| ratio | ∞ | 6.8× | **4.5×** | 4.4× | — |

*Not resolved, and not converted into a null:* the pre-registered **primary** metric,
budget-matched character recall of the gold section (`R_B@4096`), does not resolve at any
size — extremes +0.0430 against δ80 0.0714, Holm p 0.37, per-query discordance 0.83. The size
conclusion above rests on `H_B@4096` and `F1@10`, which resolve by 2–5× their own floors.

### 15.2 Scale correction — the regime needing retrieval starts at ~13 documents, not 1

The framing this work inherited was "1–100 documents, where the whole corpus fits a
131,072-token window anyway". Measured against the actual corpus, n = 260 mini-corpora:

| corpus | median tokens | p10 | p90 | max | **fits 131k** |
|---|---:|---:|---:|---:|---:|
| N = 1 | 8,778 | 5,629 | 15,294 | 43,587 | **100%** |
| N = 10 (topical) | **101,412** | 81,376 | 123,915 | 180,878 | **94.6%** |
| N = 100 (topical) | **1,029,657** | 921,641 | 1,087,426 | 1,199,758 | **0%** |
| N = 400 (whole ×0) | 3,861,045 | — | — | — | 0% |

Full-text OA articles average **9,933 tokens**, so a 131k window holds about **13** of them,
not 100 — the premise is wrong by ~8×, and at N = 100 it is not close: the *smallest* observed
100-document corpus is ~7× the window. (This is consistent with §1's corpus measurement:
median body ≈10,100 tokens over a 205,679-document sample.)

Three regimes, and only the middle one is this design's problem:

- **N ≈ 1–13 — retrieval is optional.** The corpus fits; the decision is cost and latency. If
  you do retrieve here, note that `whole4096` — one vector per document — is the **worst** arm
  on every passage metric: a single truncated vector cannot point *inside* a document, and 69%
  of answers sit past its truncation point.
- **N ≈ 10 — a coin flip.** 94.6% of these corpora fit. Stuffing works today and breaks on the
  first long article.
- **N ≈ 13–100 — the regime that needs the work.** Too large to stuff, and document-level
  metrics are saturated at 0.92–0.99 while the top-1 chunk is right only ~56–63% of the time.
  This is exactly where §15.1's gap bites hardest.

### 15.3 Breadth × k — a null interaction, a structurally-zero residual, one untested axis

Hypothesis: the marginal value of raising `k` grows with topic breadth. Tested by holding
corpus, corpus size, query text, model, chunker and gold provenance **exactly** fixed and
varying only `m`, the number of gold (document, passage) pairs per topic, 1 → 16. A licensing
identity was asserted numerically first: at `m = 1` the recall forms reduce *exactly* to the
hit forms, to the last digit.

**The interaction is not supported.** The pre-registered primary is **−0.014 to −0.032** across
the four chunk sizes — two powered nulls at the 0.05 bar, two unresolved — and negative on
every arm.

**And the residual is *structurally* zero, not empirically null.** With query and embedding
fixed, the ranking over a given corpus does not depend on `m` at all. `m` can reach the k-curve
through exactly two channels: the `min(1, k/m)` ceiling, absorbed by the pre-registered
random-ranking null, and competition between gold documents for the same top-k slots. **There
is no third channel.** A post-hoc control measures that competition directly at **−0.03 to
−0.06 at k=1, growing to −0.12 to −0.13 at k=20**, and the one pre-registered contrast that
resolved (−0.081 and −0.097) is that same competition seen on the gap.

**What this run cannot say, stated as a limitation and not as a null.** The ladder manipulates
**qrel** breadth, not **query** breadth. A semantically broader *question* — "what are the
treatment options for X" versus "what dose of Y was used" — would differ in its wording, its
embedding and therefore its whole ranking. **Semantic question breadth remains untested**, and
testing it means varying the query text, which reintroduces every confound the design was
built to exclude: a harder experiment, not a variant of this one. Two further gaps: **Term C**
(oracle-derived vs recorded gold) is not identified and sits inside the leg term, so *any* part
of the −0.43 leg difference could be gold provenance rather than difficulty; and `m` tops out
at 16 against a true CDS breadth of ~109, so nothing here speaks to `m` in the hundreds.

**The k budget is leg-dependent, and on the hard leg 20 is not enough.** On Leg A, `k*` — the
smallest `k` reaching 90% of the `k = 20` value — is **20 at every rung of the ladder**, and at
`k = 20` only **21–32%** of gold passages have been retrieved with the gap still widening. On
Leg B, `PH@10 ≈ 0.97–0.99` and `k = 20` is free. Anything quoting a `k` must name which of
those two regimes it was chosen in.

**Budget-matched, small chunks win at every rung.** At fixed `k`, ten 2048-token chunks is 8×
the context of ten 256-token chunks, so a fixed-`k` cross-size comparison is not a comparison
of chunking. Admitting chunks in rank order until a 4,096-token budget is spent (realised
budgets matched to within 13%):

| Leg A, topical | tok256 | tok512 | tok1024 | tok2048 |
|---|---:|---:|---:|---:|
| raw `PR@1`, m=1 | 0.058 | 0.052 | 0.054 | **0.087** ← best |
| **budget-matched `PR_B@4096`, m=1** | **0.291** ← best | 0.208 | 0.156 | 0.132 |
| raw `PR@1`, m=16 | 0.018 | 0.017 | 0.023 | **0.023** ← best |
| **budget-matched `PR_B@4096`, m=16** | **0.181** ← best | 0.107 | 0.067 | 0.047 |

The fixed-`k` ordering favours the largest chunk; the budget-matched ordering reverses it
completely and favours the smallest, by **2.2× at m=1 and 3.8× at m=16**. This replicates the
re-score's finding on a different corpus, a different query type, and now across breadth. So
**"chunk small" is a budget-matched result and only a budget-matched result** — the raw `@1`
reading points the other way, and it is the one the document-level grid was tuned on.

### 15.4 "Narrow queries saturate" was wrong — it is easy versus hard

§15.3's hypothesis assumed narrow queries saturate. They do not; **easy** queries do.

| N=100, tok512 | k=1 | k=2 | k=3 | k=5 | k=10 | k=20 |
|---|---:|---:|---:|---:|---:|---:|
| Leg B `PH@k` (m=1, n=396) | 0.515 | 0.720 | 0.818 | 0.891 | 0.977 | **1.000** |
| Leg A `PR@k` (m=1) | 0.052 | 0.096 | 0.125 | 0.166 | 0.237 | **0.320** |

Both rows have **exactly one gold passage per query**. Leg B's queries were *written from*
their gold section, so the retriever is being asked to invert a generator; Leg A's gold is a
cross-encoder's best unit inside a document TREC merely judged topically relevant to a clinical
case narrative. The lumped leg term is **−0.43 to −0.48 on `PH@1`** (Leg A 0.05–0.09 vs Leg B
0.48–0.56) — roughly **15× any candidate breadth effect**. A naive Leg-A-vs-Leg-B comparison
would have charged all of it to breadth.

**This sharpens §14.5 rather than replacing it.** The legs disagree on direction *and* differ
by 15× in difficulty, and the second fact is the one that has been invisible. Leg B is the
workhorse precisely because its gold is recorded by construction — and that construction is
what makes it easy. Its absolute numbers (nDCG@10 0.92–0.99; 94–98% of queries putting the gold
document at rank 1) are an **upper bound on a real user's experience, not an estimate of it**,
and §10's claims-and-cannot-claims list should say so before any of them is quoted outside this
document.

**One consequence for §12's decision point.** §14.6 keeps the size axis because the two legs
contradict each other on it. §15.1 says both legs were answering the wrong question at k=1, and
§15.3 says that budget-matched — the only size-fair reading — *both* corpora and *all* breadth
rungs point the same way, at 256 tokens. That is not enough to prune the axis: it is one
metric family on two legs whose queries were built by two constructions that both favour small
chunks for stateable reasons. But it is the first reading on which the legs **agree**, and the
next run that touches size should be the one that tests it against a third bias profile
(Leg C), not a rerun of the document-level grid.
