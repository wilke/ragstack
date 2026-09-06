# RESULTS — the pointed-question population, generated on the development topics

**Specification:** [`../design/SPEC-confirmation-run-r3.md`](../design/SPEC-confirmation-run-r3.md)
§11, the Stage 0b′ half. **Construction reference:**
[`../pilots/RESULTS-legB-rerun.md`](../pilots/RESULTS-legB-rerun.md) §1–§3, §5.

**Deliverable: 177 accepted pointed questions on the ten development topics' documents,
each with construction gold, from 400 screened candidates — a yield of 44.2 %
(39.5–49.1 %) against the Leg B re-run's 65.0 %.** All ten development topics are covered
(12–24 queries each), from 177 distinct source documents, at most one per document.

**Scope, stated first because it bounds everything below.** This run does §11's
*generation* half only. It runs **no retrieval and no scoring**. §11 guard 1 (the
discrimination gate — top-10 document sets differing between the size extremes for ≥ 25 %
of queries, and `EPACK@16k` inside [0.15, 0.90] for every arm) and guard 3 (sizing the
confirmation-run count from this set's own σ_d) are a later pass and **nothing here should
be read as evidence about either**. Until guard 1 has been read, this population is a
*candidate* second population, not an adopted one.

Two further things this run did **not** do, both deliberate:

* **The crossencoder construction cross-check is deferred.** The Leg B re-run's §5.2 asked
  `bge-reranker-v2-m3` on `:50052` whether the argmax window lands on the section a query
  was written from (66.9 % on its accepted set, 4.5× chance). `:50052` was **not contacted
  here** — the endpoint policy for this task is `mango:8003` and nothing else. The check
  belongs with the Stage 0b′ retrieval pass, which already loads that model.
* **No labeler ran, and no union gold exists yet.** §11 guard 2 splits gold into the
  construction passage (produced here, no labeler needed) and the labeler-found
  alternatives (union under D3 rules 1–3, once a labeler passes §3.7's gates — which, per
  [`RESULTS-stage0b-relabel.md`](RESULTS-stage0b-relabel.md), **neither judge has**). What
  this document ships is the construction half.

---

## 0. Provenance

| item | value |
|---|---|
| harness | [`s0_pointed_gen.py`](s0_pointed_gen.py), committed in full |
| code repo | `/home/wilke/Development/ragstack` at **`1a67662`** at run time, recorded in the manifest. Stage 0 pins `55a0fc2`; `git diff 55a0fc2 1a67662 -- python/ragstack/` is **empty**, so `sentence_spans` and the JATS unit split are byte-identical and the character offsets in this file's spans are in the same coordinate system as `artifacts/labels-dev.jsonl`. `s0_common.provenance()` was **not** used, because it aborts on any HEAD ≠ `55a0fc2`; a local provenance function records both commits instead. |
| worktree | `feat/stage0-pointed-set` off `1a67662`; two untracked paths at run time (`s0_pointed_gen.py` itself and `run/`) |
| helper tree | `STAGE0_HELPERS=/home/wilke/Development/worktrees/phase0-rescue/phase0` — `stage1_common`, `pilot_common`, and the Leg B re-run's `legb2_rules` are **imported**, not copied |
| interpreter | `/rag/envs/ragstack/bin/python3` (CPython 3.12.13), `HF_HOME=/rag/cache` |
| editable-install defence | `pin_repo()` via `s0_common`; `ragstack.__file__` recorded in the manifest |
| **served model, probed live** | `mango:8003` → **`RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic`**, asserted against `s0_common.SCOUT_EXPECT` before the first call. A silent model swap is a loud error, not a quiet change of generator. |
| endpoints contacted | **`mango:8003` and nothing else.** Not `:50052`, not `:9001`–`:9006`, not `mango:8004`, no store, no tenant API. No store client is constructed in this module or anything it imports. |
| concurrency | **≤ 4 in flight**, enforced by a slot queue; every failure path backs off rather than retrying immediately |
| GPUs 6 and 7 | **0 MiB / 0 %**, before and after. Nothing here selects a device. |
| seeds | grade-0 dev draw `20260904` (Stage 0a's, replayed); **candidate draw and round-robin `20260918` (new)**; sampling `seed` sent on every request is `20260918` |
| IDF table | `pilots/idf_oa10k.json` — the Leg B re-run's own table, **93,347 terms** over 10,000 PMC OA title+abstracts, sha256 `90759aec…15d6f2bd`. Rarity bar `IDF ≥ 5.60` (`df ≤ 1 %`), `title_answerable` bar `0.80`, both unchanged. |
| prompt provenance | stage A is `legb2_gen.PARAPHRASE_PROMPT` **asserted byte-identical** against `legb2_gen.py`; stage B's carried rules are asserted line-by-line and the full unified diff is recorded; stage D is `pilots/verifier_prompt.txt` read from disk, unmodified |

Full machine-readable record: [`artifacts/pointed/pointed-manifest.json`](artifacts/pointed/pointed-manifest.json).

---

## 1. Source documents — the development slice, and what was excluded

r3 §11's Stage 0b′ generates "on the development topics' documents". The set is rebuilt
here rather than read from a cache, by replaying `s0_corpus.build`'s own draw: one
`random.Random(20260904)` over `sorted(dev_topics)`, 300 grade-0 negatives per topic
sampled from the **sorted** negative list, unioned with every grade ≥ 1 relevant.

| step | documents |
|---|---:|
| dev slice (10 topics' grade ≥ 1 relevants ∪ their 300-per-topic grade-0 draws) | **4,099** |
| — asserted **byte-identical** to `step2/fetchlist.txt` | ✅ 4,099 / 4,099, zero on either side |
| **excluded: also a confirmation topic's grade ≥ 1 relevant** | **−578** (14.1 % of the slice) |
| remaining | 3,521 |
| — not in the Stage 0a parse (empty parsed body, §4.2.5) | −38 (1.1 %) |
| **usable source documents** | **3,483** |

The 578 exclusions are the point of the ledger: a document that is a confirmation topic's
judged relevant is never read, summarised or written from by this run, so no confirmation
topic's data is touched even indirectly. The assertion runs in code (`dev_documents()`),
not in prose — a dev/confirmation intersection, or a dev topic list that stops matching
`s0_common.DEV_TOPICS`, aborts before the first LLM call.

**Section eligibility.** "Deep" is r3 §11's rule: a unit that is neither the abstract nor
the first body unit — index ≥ 2, with `cls != "abstract"` for the handful of articles
carrying a second abstract-classed unit further down. "Enough prose" is the Leg B re-run's
positive section rule (`legb2_rules.section_signals`), evaluated clause by clause on every
candidate independently:

| clause | rejects | of 14,909 deep units |
|---|---:|---:|
| `min_words` (≥ 200) | 5,028 | 33.7 % |
| `has_result_or_method` (≥ 1 numeric result or method noun) | 3,176 | 21.3 % |
| `max_words` (≤ 1,600) | 849 | 5.7 % |
| **eligible** | **8,470** | **56.8 %**, across **3,079** documents |

One eligible unit is drawn per document (§7, deviation D-3), and the documents are ordered
round-robin over the ten topics, so topic coverage is by construction rather than by luck.

---

## 2. Construction — four passes, one item per call

Every stage is **one item per LLM call**. The Leg B re-run's round 1 batched 14 items per
call and could not rule out cross-item bleed; here isolation is by construction.

| pass | sees | never sees | writes | temp | budget |
|---|---|---|---|---:|---:|
| **A** paraphrase | the deep section | anything else | a 2–3 sentence abstractive summary, specific names carried over verbatim | 0.3 | 900 |
| **B** query | A's summary **+ the article's title and abstract** | **the section** | `{"entity", "query"}` — one clause, ≤ 15 words, naming a specific entity absent from the front matter | 0.5 | 1,200 |
| **C** gold | the query + the section | the front matter | `{"sentences"}` — the **verbatim whole sentence(s)** of the section that answer the query | 0.0 | 2,000 |
| **D** verifier | the query + title + abstract | the section, the body | `YES`/`NO` — is this fully answerable from the front matter? | 0.0 | 200 |

The **two-pass separation is the re-run's T1b defence and it is intact**: pass B never sees
the section, so the query cannot copy the passage it must retrieve. What pass B *does* see
is the front matter, as a **negative** constraint — the deviation argued in §7.

**Construction gold (§11 guard 2).** Pass C's quotes are located in the section by
**exact match, then whitespace-normalised match**, and the located character interval is
snapped out to whole sentence boundaries using
`ragstack.ingestion.chunkers.sentence_spans` — the repo's own segmentation at the pinned
commit, whose spans tile the text exactly, so every span is a D1
`(unit, first_sentence, last_sentence)` triple with absolute `[start, end)` offsets into
the indexed document text. Contiguous runs become one span; a gap becomes a second span.
**A quote that does not locate rejects the query** (r3 §10 item 3, decision C) — it is a
hallucinated receipt, and it is not repaired.

---

## 3. The screens, with counts

Every predicate is evaluated on **every** candidate independently, so a rate below is a
real rate and not a first-match-wins funnel; the funnel is reported beside it as a second
view of the same data. `n = 400` screened candidates.

Every **gate** is computed from the query, the section and the front matter — never from
what the generator says about itself. That is the re-run's rule ("a gate must not depend on
the thing it is auditing"), and it is why r3 §11's entity clause is enforced as
`no_deep_rare_term` **on the query's own terms** rather than on the declared entity string.

| gate | what it rejects | independent | 95 % Wilson | first-match |
|---|---|---:|---|---:|
| `empty` | generator declined rule 3, or emitted no parseable JSON | 11 | 2.8 % (1.5–4.9) | 11 |
| `not_a_question` | not interrogative | 0 | 0.0 % (0.0–1.0) | 0 |
| `names_document` | refers to a study, figure, table | 1 | 0.2 % (0.0–1.4) | 1 |
| `too_short` | < 4 content terms | 5 | 1.2 % (0.5–2.9) | 5 |
| `too_long_15` | > 15 words | 14 | 3.5 % (2.1–5.8) | 14 |
| `compound` | two clauses joined | 2 | 0.5 % (0.1–1.8) | 2 |
| **`title_answerable_ta`** | **IDF overlap vs title + abstract ≥ 0.80** | **90** | **22.5 % (18.7–26.8)** | **83** |
| `title_answerable_title` | IDF overlap vs title alone ≥ 0.80 | 6 | 1.5 % (0.7–3.2) | 0 |
| `not_specific` | no rare query term present in the section | 9 | 2.2 % (1.2–4.2) | 7 |
| **`no_deep_rare_term`** | **no rare query term that is in the section AND absent from the front matter** | **166** | **41.5 % (36.8–46.4)** | **74** |
| `gold_not_located` | no locating quote (incl. stage C declining, and stage C never reached) | 57 | 14.2 % (11.2–18.0) | 17 |
| `gold_too_many_sentences` | > 5 sentences of gold | 4 | 1.0 % (0.4–2.5) | 4 |
| `duplicate` | normalised query already accepted | 0 | 0.0 % (0.0–1.0) | 0 |
| `abstract_answerable` | pass D says the front matter fully answers it | 64 | 16.0 % (12.7–19.9) | 5 |
| | **accepted** | | | **177** |

**Reported, never gated** — these audit the generator's own declarations, and one of them
is the most interesting number in the table:

| covariate | independent | 95 % Wilson |
|---|---:|---|
| `anchor_fail` (declared entity in query ∧ in section ∧ rare) | 165 | 41.2 % (36.5–46.1) |
| `entity_no_rare_term` | 12 | 3.0 % (1.7–5.2) |
| `entity_rare_absent_from_section` | 23 | 5.8 % (3.9–8.5) |
| **`entity_rare_all_in_front_matter`** | **182** | **45.5 % (40.7–50.4)** |
| `entity_substring_absent_from_section` (strict surface match) | 70 | 17.5 % (14.1–21.5) |
| `entity_substring_in_front_matter` (strict surface match) | 173 | 43.2 % (38.5–48.1) |
| `too_long_20` (the re-run's own word bar) | 3 | 0.8 % (0.3–2.2) |
| `quote_snapped_to_sentence` (≥ 1 quote was a fragment) | 76 | 19.0 % (15.5–23.1) |

**Read the entity rows next to the gate rows and the reason for the gate design is
visible.** The generator's *declared* entity is already fully present in the title and
abstract 45.5 % of the time, and its surface form is absent from the section 17.5 % of the
time (the entity comes from a paraphrase, so surface identity with the section was never a
reasonable thing to demand). Had `entity_in_front_matter` been gated on the declaration —
which is how the first smoke was written — the yield would have been governed by an audit
of the generator rather than by a property of the query. `no_deep_rare_term` measures the
same property on the query itself and fires on 41.5 %, and the two disagree on enough
candidates to matter.

**The gold locator's own failure rate.** Of the 400 candidates, 11 never reached stage C
(no query to locate against). Of the **389** that did, **39 (10.0 %)** returned an empty
sentence list — stage C declining, which is a legal verdict — and **350** returned at
least one quote. Of those 350, **7 produced a quote that does not locate: 2.0 %
(1.0–4.1)**. For scale, the r3 relabel measured Llama-4-Scout's hallucinated-span rate at
**0.0504** against a ≤ 0.05 gate on the labeling task under the quote-primary protocol.
Copying a sentence out of one 200–1,600-word section it is looking at is an easier job than
locating evidence in a whole article, and the rate is correspondingly better. Among the
195 quotes behind the 177 accepted queries, **191 located by exact match and 4 by the
normalised fallback**.

---

## 4. Yield, against the Leg B re-run's 65 %

| set | screened | accepted | yield | 95 % Wilson |
|---|---:|---:|---:|---|
| **this run** | **400** | **177** | **44.2 %** | 39.5–49.1 % |
| this run, rule gates only (the verifier not applied) | 400 | 182 | 45.5 % | — |
| Leg B re-run (`RESULTS-legB-rerun.md` §3) | 400 | 260 | 65.0 % | 60.2–69.5 % |

**The distance is resolvable and it is not a rounding error.** δ80 for a two-proportion
comparison at these n is **9.9 pp**; the observed distance is **20.8 pp**. Applying the
pre-registration check *before* reading the number, as every reading in this tree is
required to: the instrument is 2.1× finer than the effect, so this is a real difference and
not an unresolved one.

**Where the 20.8 pp went, and what it says about the corpus.** In the re-run the verifier
was "the only filter that carries weight" (33.2 % of rule-passing queries) and the rule
filters read 8/400 combined. Here the verifier fires on 16.0 % and is almost never the
*first* reason (5 of 400) — because two gates in front of it already remove what it would
have caught. The two dominant gates are both leakage gates, and both fire far harder than
on the re-run's corpus:

* `title_answerable_ta` at **22.5 %** where the re-run's accepted set had 11.9 % of queries
  merely *sitting at or over* the same bar with no gate applied;
* `no_deep_rare_term` at **41.5 %**, a gate the re-run did not have at all.

The mechanism is the corpus, not the prompt. The re-run sampled 3,000 random PMC OA
articles, where a deep section usually reports something the abstract does not. This corpus
is the ten TREC CDS development topics' judged documents — heavy in **case reports and
short clinical papers whose Discussion restates the abstract**. On such an article there
is no pointed question to ask that the front matter does not already answer, and the
screens correctly throw away the attempt. **44.2 % is the price of the declared population
on this corpus, and it is the honest number to carry into §11's cost line** (which budgeted
generation at the re-run's rate).

---

## 5. The accepted set

### 5.1 Where the evidence lives — the unit-index distribution

| unit index | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 14 | 17 | 395 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| accepted | 63 | 62 | 22 | 10 | 8 | 3 | 2 | 1 | 1 | 2 | 1 | 1 | 1 |

**125 of 177 (70.6 %) sit at index 2 or 3** — the first and second unit after the abstract
and the first body unit. That is what the eligibility rule permits and what the corpus
offers: the median accepted source document has **5 structural units in total**, so index 2
is already past the halfway mark. Relative depth in the document is a better statement of
the same fact: **p25 0.60, median 0.75, p75 0.92**. Nothing here is a lead-unit query.

Source section class: `other` 75, `discussion` 67, `results` 20, `methods` 15. Section
length median **555 words** (201–1,586).

**One oddity, reported rather than filtered.** `pq_0241` was written from unit **395 of
928** of PMC2432466 — which is not an article but the *Purines 2008 Meeting* conference
abstract book (806 k characters). The query and its gold are both sound
(`Does MRS2179 prevent glutamate toxicity in rat hippocampal neurons?`, gold located
verbatim), and no rule in the design excludes a proceedings volume. It is flagged here
because a 928-unit "document" will behave unlike the other 176 under any packing budget,
and whether to exclude such volumes is a decision for the retrieval pass, not one to take
silently now.

### 5.2 Query shape

| words | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| accepted | 2 | 7 | 13 | 24 | 32 | 33 | 37 | 15 | 9 | 5 |

Median **11**, mean **10.76**, range 6–15. **148 of 177 (83.6 %, 77.5–88.3)** are ≤ 12
words, which is r3 §11's "held to ≈ 12 words". Only 3 of 400 candidates exceeded the
re-run's own 20-word bar, so the tightening to 15 is doing real work at the margin (14 of
400 exceeded 15) without the generator fighting it.

### 5.3 Construction gold

| | value |
|---|---|
| spans per query | 1 → 168, 2 → 7, 3 → 2 |
| whole sentences per query | 1 → 144, 2 → 21, 3 → 8, 4 → 3, 5 → 1 (median **1**) |
| span length | median **182 characters** |
| quotes located by exact match | **191 / 195** |
| quotes located by the normalised fallback | 4 / 195 |
| queries where ≥ 1 quote was a fragment snapped out to sentence bounds | 40 / 177 = **22.6 % (17.1–29.3)** |

Every accepted query's gold is a set of D1 spans inside the section it was written from,
with `unit`, `first_sentence`, `last_sentence` and absolute `[start, end)` character offsets
into the same indexed text `artifacts/labels-dev.jsonl` speaks in.

### 5.4 Leakage, measured both ways

| covariate (median) | accepted (n=177) | rejected (n=212 with a query) |
|---|---:|---:|
| IDF overlap vs title + abstract | **0.423** | 0.746 |
| IDF overlap vs title alone | 0.184 | 0.354 |
| unweighted Jaccard vs title + abstract | 0.031 | 0.046 |
| rare query terms **absent** from title + abstract | **2** | 0 |
| rare query terms in the section **and** absent from the front matter | **1** | 0 |

The re-run's §5.3 warned that the IDF-overlap covariate is mechanically inflated by short
queries and recommended restating it on the absent-rare-terms count. Both are given here,
and both agree: accepted 0.423 vs rejected 0.746 replicates the re-run's 0.496 / 0.768
separation almost exactly, and **177 / 177 (100 %, 97.9–100)** of accepted queries ask for
at least one rare thing the front matter never names. **0 / 177** sit at or over the 0.80
bar — but that is a gate here, so it is a tautology and is stated only to be explicit that
it is one.

### 5.5 Coverage

All **ten** development topics are covered, and no document contributes more than one
query (r3 §11's cap is two).

| topic | 2014_5 | 2014_11 | 2014_29 | 2015_8 | 2015_18 | 2015_23 | 2016_1 | 2016_9 | 2016_13 | 2016_26 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| accepted | 17 | 12 | 19 | 17 | 16 | 15 | 14 | 24 | 23 | 20 |

Source documents: **177 distinct**, of which **50** are a grade ≥ 1 relevant of at least one
development topic and **127** are grade-0 draws only. Five documents belong to more than one
development topic; the manifest records every membership per query, not just the one it was
drawn under.

---

## 6. Ten examples, verbatim, with their gold

One per development topic, taken in `qid` order — not curated for quality.

**`pq_0002`** — 2014_29, PMC1993940, unit 3 *"A new paradigm for the treatment of CVRFs and CVDs"*, 11 words
> **Q:** What is the duration of the feasibility trial of PHASE Clinic?
> **Gold** (unit 3, sentences 4–4, chars [8168, 8402)): *"The authors have recently launched such a program - The Prevention of Heart Attack and Stroke in the Elderly (PHASE) Clinic - and are currently conducting a 6-month long feasibility trial to measure its performance against usual care."*

**`pq_0005`** — 2015_23, PMC3547721, unit 3 *"Discussion"*, 14 words
> **Q:** What is the expression level of Leucine-Rich alpha-2 Glycoprotein 1 in severe dengue plasmas?
> **Gold** (unit 3, sentences 21–22, chars [14120, 14351)): *"Three proteins, identified by mass spectrometry, were confirmed by ELISA to have significant higher expression levels in SD plasmas. These proteins were Leucine-Rich alpha-2 Glycoprotein 1, Ferritin and Vitamin D Binding-Protein."*

**`pq_0006`** — 2015_8, PMC2492850, unit 2 *"Methods"*, 12 words
> **Q:** What is the composite equilibrium score range for perfect balance in SOT
> **Gold** (unit 2, sentences 16–16, chars [8088, 8222)): *"A score of 100 represents perfect balance (no sway), and a score of 0 represents a potential fall (sway exceeds limits of stability)."*

**`pq_0009`** — 2016_26, PMC2797520, unit 3 *"Discussion"*, 9 words
> **Q:** How do health coaches titrate medications for hypertension management?
> **Gold** (unit 3, sentences 6–6, chars [19308, 19478)): *"In one group, health coaches titrate medications according to an algorithm, thus preventing long revisit intervals from delaying appropriate medication intensification."*

**`pq_0010`** — 2016_9, PMC4031816, unit 2 *"CLINICAL CASE"*, 8 words
> **Q:** What was the PaO2/FiO2 ratio after methylprednisolone treatment?
> **Gold** (unit 2, sentences 18–18, chars [5054, 5138)): *"Upon ending the pulse therapy (23rd day), the patient showed a 116 PaO2/FiO2 ratio."*

**`pq_0011`** — 2014_11, PMC3543951, unit 3 *(untitled)*, 9 words
> **Q:** What was troponin I level in patient with hyperhomocysteinemia?
> **Gold** (unit 3, sentences 11–11, chars [2497, 2718)): *"The levels of cardiac biomarkers were elevated: troponin I, 0.35 ng/mL (range, 0 to 0.05); C-reactive protein, 1.6 mg/dL (range, 0.1 to 1); and N-terminal prohormone brain natriuretic peptide, 3,106 pg/mL (range, < 262)."*

**`pq_0017`** — 2016_1, PMC3907202, unit 2 *"Presentation of case"*, 12 words
> **Q:** What is the typical hemoglobin value in patients with ileal malignant hemangioendothelioma?
> **Gold** (unit 2, sentences 4–4, chars [3099, 3313)): *"Except for the positive fecal occult blood test and microcytic, hypochromic anemia with decrease hemoglobin values down to 5.5 g/L, all the other laboratory values including tumor markers were in the normal range."*

**`pq_0018`** — 2016_13, PMC3160384, unit 2 *"Conclusion"*, 9 words
> **Q:** What is the typical age of onset for SSEH?
> **Gold** (unit 2, sentences 9–9, chars [5951, 6040)): *"SSEH occurs in all age groups, but most frequently after the fourth decade of life [10]."*

**`pq_0033`** — 2014_5, PMC3445446, unit 2 *"Methods"*, 11 words
> **Q:** What method is used to estimate odds ratios in statin trials?
> **Gold** (unit 2, sentences 22–22, chars [7361, 7501)): *"Odds ratios (ORs) for each trial and summary estimates of ORs across trials were estimated using Peto's one-step method (see Text S3) [24]."*

**`pq_0054`** — 2015_18, PMC2952899, unit 2 *"2. Methods"*, 10 words
> **Q:** Does exercise training affect BNP expression in heart failure patients?
> **Gold** (unit 2, sentences 13–13, chars [4369, 4590)): *"Clinical measures: mortality and morbidity rates, functional capacity, quality of life, cardiac function, cytokine, and brain natriuretic peptide (BNP) expression in those who did and did not undertake exercise training."*

### 6.1 My read of those ten — the part no metric reports

Eight are what §11 asked for: a pointed question whose answer is a specific number, method
or finding, and a gold span that actually answers it. **Two are weak, and both fail the same
way — the gold locates but does not answer:**

* **`pq_0054`** asks whether exercise training *affects* BNP expression; the gold sentence
  is the trial's list of outcome measures, which says BNP was *measured*, not what
  happened to it. The question is answerable from that section, but from a later sentence.
* **`pq_0017`** asks for a *typical* hemoglobin value in a disease; the gold gives **this
  patient's** value (5.5 g/L). The question generalises past what a case report can
  support — the re-run's own failure mode of a query that "presupposes" more than the
  source establishes.

Two of ten is consistent with the Leg B re-run's read of a random 30, which found six bad
accepts. **It is also exactly the failure §11 guard 2 anticipates**: the construction
passage is one location and may not be the *best* one, which is why the human read on this
population asks the extra question "does a delivered passage other than the construction
passage answer this?" and why the labeler union exists. Ten queries is not an audit; a
proper read of ≈ 50 pairs by two readers is the §6.6.2 protocol and it has not been done.

---

## 7. Deviations — from the brief, and from the Leg B re-run

**D-1. The section-length band is in words, not SFR tokens.** The re-run's positive rule
admits sections of 250–2,200 **SFR** tokens. The SFR fleet on `:9001`–`:9006` is outside
this task's endpoint policy and Llama-4-Scout's tokenizer is not SFR's, so the band is
restated as **200–1,600 words**. Realised accepted section length is a median of 555 words
(201–1,586), which sits inside the re-run's realised 1,232-token median under any
reasonable words-to-tokens ratio. The clause counts in §1 are reported in the units they
were computed in.

**D-2. Pass B sees the article's front matter.** *This is the substantive one.* The
re-run's pass B sees the summary and nothing else. r3 §11 requires the entity to be absent
from the title + abstract and the query to sit below the 0.80 bar against them — **two
constraints stated over text the generator could not see**. The first 20-candidate smoke
measured the consequence directly (`artifacts/pointed/pointed-manifest-smoke1.json`):
**10 of 20 candidates died on `title_answerable_ta` and 2 more on the entity's presence in
the front matter, for a yield of 5/20 = 25 %.** Adding the front matter to pass B as a
*negative* constraint — "your question must ask for something the summary supplies that the
front matter does not state" — moved `title_answerable_ta` to 1 of 20 on the second smoke
(`…-smoke2.json`) and to 22.5 % of 400 on the run. **The section is still never shown to
pass B**, so the T1b contamination the two-pass form exists to exclude is excluded exactly
as before; what changed is that the generator can now satisfy a constraint the design
imposes on it. Stage A is unchanged and byte-identical; stage B's carried rules 2, 4, 5,
its opening and its JSON contract are asserted byte-identical, and the full diff is in the
manifest.

**D-3. One candidate unit per document, not two.** §11 caps *accepted* queries at two per
document. The first smoke drew two adjacent units per document and produced near-duplicate
pairs from the same article (two `TomoBreast` questions, two `idiopathic inflammatory
myopathies` questions). Correlated queries would deflate the σ_d that §11 guard 3 sizes the
confirmation-run count from — the one quantity this set exists to measure — so one unit per
document is drawn. Realised: **177 queries from 177 documents, maximum 1 per document.**

**D-4. The entity clause is gated on the query, not on the declaration.** The brief says
"entity present in the section and absent from the front matter". The generator's declared
entity is checked three ways and **reported** (§3), but the **gate** is
`no_deep_rare_term`: the query itself must contain a term that is rare in the corpus
(IDF ≥ 5.60), occurs in the source section, and does not occur in the title + abstract.
This is the re-run's own discipline — its `anchor_fail` was deliberately never a gate,
because "a gate must not depend on the thing it is auditing" — and the numbers in §3 show
the two readings disagree on enough candidates to matter (45.5 % vs 41.5 %). The stricter
surface-substring readings the first smoke gated on are kept as reported covariates so a
reader can see how much the choice of matching rule moves the count.

**D-5. The abstract-answerability verifier was run, and it is a gate.** §11's screen list
names the IDF and entity screens; the re-run's headline 65 % is *post-verifier*, so the
verifier is included here for the comparison in §4 to be like-for-like. Its prompt file is
used unmodified. The rule-gates-only yield is reported beside the headline (45.5 % vs
44.2 %); the verifier changes the answer by 5 queries, because the two leakage gates in
front of it already catch what it would have caught.

**D-6. A new seed.** `SEED_POINTED = 20260918` governs the candidate shuffle, the
round-robin and the `seed` sent on every request. It is not one of Stage 0's seven; it is
declared in `s0_pointed_gen.py` and in the manifest.

**D-7. Cosmetic, disclosed for completeness.** The unified diff recorded under
`prompt_provenance.query_prompt_unified_diff` compares a fixed 28-line window of
`legb2_gen.py` against the 33-line prompt, so its last five `-` lines are the end of the
source literal and the two lines after it, not prompt content. The *assertions* — stage A
byte-identical, and each carried stage-B rule present verbatim in both — are exact and are
what the provenance rests on. The harness is committed as run, so the artifact and the code
match.

---

## 8. Cost

| stage | requests | prompt tokens | completion tokens | LLM seconds |
|---|---:|---:|---:|---:|
| A paraphrase | 400 | 451,933 | 45,438 | 1,085.0 |
| B query | 400 | 349,868 | 13,437 | 496.0 |
| C gold | 389 | 458,951 | 21,250 | 659.0 |
| D verifier | 389 | 198,591 | 4,537 | 178.3 |
| **total** | **1,578** | **1,459,343** | **84,662** | **2,418.2** |

**Wall clock 10 min 19 s** (628.6 s including corpus load and screening), at ≤ 4 in flight
throughout. **0 request failures, 0 retries, 1 truncated response** (a verifier reply, which
still carried its verdict). The three smokes cost a further 318 requests and 121 s of wall
time.

r3 §11 budgeted "~150 queries at Stage 0b′ … on `mango:8003` at the Leg B re-run's measured
43 min per 400 — under two hours". The realised cost is **10 minutes for 400 candidates**,
because Llama-4-Scout is not a reasoning model and does not spend the invisible reasoning
tokens that dominated the re-run's Qwen budget. **The §11 cost line is 4× pessimistic for
this endpoint**, which matters for the confirmation-run scale-up: 600 queries is well under
an hour, not most of an afternoon.

---

## 9. Reproduction

```bash
export HF_HOME=/rag/cache
export PYTHONPATH=/home/wilke/Development/ragstack/python
export STAGE0_HELPERS=/home/wilke/Development/worktrees/phase0-rescue/phase0

cd docs/plans/results/stage0
/rag/envs/ragstack/bin/python3 s0_pointed_gen.py --smoke                       # 20 candidates
/rag/envs/ragstack/bin/python3 s0_pointed_gen.py --target 150 \
    --max-candidates 600 --block 100 --tag full
```

Outputs land under `$STAGE0_BIG/work/pointed/` (`STAGE0_BIG` defaults to
`/rag/tmp/stage0-conf`); the three committed files are copied from there. The harness
asserts, before the first call: the dev slice reproduces `step2/fetchlist.txt`
byte-for-byte, the dev and confirmation topic sets are disjoint, `mango:8003` serves
Llama-4-Scout, and the two carried prompts match `legb2_gen.py`. Any of those failing is a
`SystemExit`, not a warning.

**Never stop this by process name.** The pid is recorded at launch under `run/`; resolve by
that pid, and verify `/proc/<pid>/cwd` before signalling anything.

---

## 10. Artifacts

| path | what |
|---|---|
| [`s0_pointed_gen.py`](s0_pointed_gen.py) | the harness, committed as run |
| [`artifacts/pointed/pointed-dev.jsonl`](artifacts/pointed/pointed-dev.jsonl) | **the 177 accepted queries** — query, entity, source document and its dev-topic memberships, unit, gold spans with D1 offsets, every screen value, leakage covariates, per-stage raw-response sha256 |
| [`artifacts/pointed/pointed-dev-rejected.jsonl`](artifacts/pointed/pointed-dev-rejected.jsonl) | the 223 rejected candidates, same shape, with `first_reason` and the full independent screen vector |
| [`artifacts/pointed/pointed-dev-raw.jsonl`](artifacts/pointed/pointed-dev-raw.jsonl) | every generator response, verbatim, for all 400 candidates — the strings the sha256s in the two files above hash |
| [`artifacts/pointed/pointed-manifest.json`](artifacts/pointed/pointed-manifest.json) | served model, endpoint, concurrency, prompt sha256s and the prompt provenance assertions, seeds, IDF table hash, the source-document ledger, section-selection counts, every screen count, and the cost table |
| `artifacts/pointed/pointed-manifest-smoke{1,2,3}.json` | the three smokes, including the one that motivated deviation D-2 |

The 400 raw responses, the rejected candidates' own JSONL and the filtered
`dev_docs.jsonl` cache stay in the run directory; only the four files above plus the smoke
manifests are committed.

---

## 11. What has to happen before this population counts

1. **§11 guard 1, the discrimination gate.** Retrieve this set against the six frozen index
   arms; require top-10 document sets to differ between the size extremes for ≥ 25 % of
   queries, **and** confirmatory `EPACK@16k` inside [0.15, 0.90] for every arm. If it
   fails, the population is reported as descriptive and the CDS population alone carries
   the decision under r3 §1.1. **Nothing in this document anticipates that outcome.**
2. **§11 guard 3, sizing.** Measure σ_d on both endpoints per contrast on these 177, and
   set the confirmation-run count at the same 80 % power and α as the CDS population, with
   the joint power of the four-way conjunction printed. If it exceeds the 600-query cap,
   the population is re-declared a reported secondary *before* freeze.
3. **The deferred cross-check.** Run the re-run's §5.2 construction check on `:50052`
   during that pass — does the reranker's argmax window land on the section each query was
   written from? The re-run read 66.9 % on its accepted set against 14.8 % chance, and the
   accepted-vs-rejected split (5.4 % vs 33.1 % landing on the document lead) was its best
   internal-validity signal.
4. **§11 guard 2's other half.** Once a labeler passes §3.7's gates, union its found sets
   into these construction spans under D3 rules 1–3, and run the ≈ 50-pair two-reader read
   that bounds how much construction-only gold under-counts. Neither is possible today:
   `RESULTS-stage0b-relabel.md` reports **neither judge passes**.
