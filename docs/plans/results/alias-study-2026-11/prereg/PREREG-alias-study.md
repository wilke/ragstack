# Pre-registration, alias study (OA-1000) — DRAFT v0, 2026-10-31

**Status: DRAFT — not frozen; owner review pending.** Every hypothesis below is a *proposal* for the owner
to confirm, reword or drop (§13). Nothing in this text binds a run until it is frozen by the procedure in
this directory's `README.md`. At freeze the text is frozen **as written**, and its sha256 goes into the
README and a private `PREREG.sha256`, never into this file. After freeze this file is never edited. Any
later change is a `DEVIATIONS.md` entry.

> **Public copy.** The file names no tenant, host, endpoint, collection, person or gateway owner. The corpus
> is named by its alias, **OA-1000**. It holds counts, rates, model ids, version strings, commit shas and
> sha256 hashes only. It holds no question, answer, quote, passage, paper id or record id. The partner whose
> gateway carries the model calls is "the papers-OC owner" where it has to be mentioned.

**Terms.**
- **Glossary entry:** a pair *alias → primary term* for one paper. The alias is a **symbol** (the round-3
  symbol glossary, `llm` ∪ `rules` entries) or a **defined abbreviation** (`alias_abbrev.py`, ABBREV.md:
  defined in the paper by pattern P1 `long form (ABBR)` or P2 `ABBR (long form)`, letter-aligned).
  **Merged glossary:** the two unioned and keyed by (paper, alias, primary words). Each entry has a `kind`:
  `symbol`, `abbreviation` or `both`.
- **G record:** a question asked **with the primary term** whose gold passage contains **the alias and not
  the term**. Under the strict rule this means neither the primary's word sequence nor any content word of
  the primary with ≥ 5 characters appears in the gold chunk (ABBREV.md, *Definitions*).
- **C record (control):** a question asked with its term whose gold passage **contains the term**.
- **Gold chunk:** the record's source chunk, exactly one per record in this set.
- **Hit (record × cell):** the gold chunk is in the top 10, **or** any other top-10 chunk is judged to
  answer the question (§5). A correct answer from **another paper** counts.
- **found@10:** the share of records with a hit. **found@10-gold:** the gold-only version (secondary).
- **Cell:** corpus arm × retriever × glossary arm (§4).
- **Cluster:** the record's paper.

**What had been seen before writing.** Rounds 1–3 of the glossary benchmark and the reranker test in full,
including their records, deviations and addenda (`../../glossbench-round3-2026-11/`,
`../../reranker-test-2026-11/`). One fact from them shaped this design: under the served pipeline, round 3
found **the same records** with the symbol glossary attached and with none (its *What this shows*, item 4).
Also seen: the alias-study notes in `docs/plans/alias-study-2026-11/` (ABBREV, VERIFIER,
JUDGE-CALIBRATION, JUDGE-VALIDATION-PLAN, TIEBREAKER-PLAN). Read only as counts were the private
build outputs of the record set (§3), the judge-calibration, validation and tiebreaker outputs (§5.4), and
two scratch computations made for this draft: the power sketch (§7) and a replay of the judge rule on
the validation labels (§5.4). **No retrieval of any kind has been run on any study record**, and no
found@10 figure for any record exists under any arm (§3.4 lists the one partial exception). The study's
corpus-arm design and its retriever factor were chosen after seeing round 3's null and the reranker test's
reading that the reranker can lose to BM25 on some corpora. Both are disclosed so that a reader can weigh
them (§12).

## 1. Question

The question has two parts, both the owner's.

1. **Glossary.** A question names a concept by its full term, and the passage that answers it uses only a
   short form: a symbol, or an abbreviation the paper defines once, somewhere else. Does attaching the
   paper's merged glossary to its chunks make that passage findable? The **C** records hold the same
   machinery fixed on passages that already carry the term. They are the control that separates a
   glossary effect specific to aliases from a general change in what is retrieved.
2. **Corpus neighbourhood.** OA-1000 is a sample of unrelated papers. A real collection holds a paper
   *with* its references and its citers, which use the same vocabulary and the same short forms, often
   with different meanings (about half of the abbreviations shared between papers collide; ABBREV.md).
   Does adding a study paper's citation neighbours cost more than adding the same number of random papers
   from the same field? And does the served reranker lose more than BM25 when the neighbours are added?

The study informs the decision on whether to ship the merged glossary. It does not make that decision.

## 2. Hypotheses (proposed; owner to confirm, §13 O1)

Confirmatory family **F = H1–H3**, **Holm over 3** at α = 0.05. Gated or absent members enter at p = 1.0
and the family is never shrunk (round 3 D14). **Each member is one contrast.** Everything else is
secondary or exploratory and is labelled as such in the sentence that quotes it.

| id | hypothesis | records | cells compared | type | per-cluster statistic |
|---|---|---|---|---|---|
| **H1** | the merged glossary helps G records more than C records | the 600 records on the 200 satisfied papers (400 G, 200 C) | corpus **base**, retriever **default**: glossary **merged** vs **none** | difference-in-differences, **one-sided** (> 0) | per paper: mean over its G records of Δ − its C record's Δ, with Δ = hit(merged) − hit(none) ∈ {−1, 0, +1} |
| **H2** | citation neighbours reduce found@10 more than random same-field papers | the same 600 records | retriever **default**, glossary **none**: corpus **neighbour** vs **random** | superiority, **one-sided** (< 0) | per paper: Σ over its records of hit(neighbour) − hit(random) |
| **H3** | the default retriever's change under neighbour expansion differs from BM25's | the same 600 records | glossary **none**: [default(neighbour) − default(base)] − [BM25(neighbour) − BM25(base)] | difference-in-differences, **two-sided** | per paper: Σ over its records of the per-record DiD ∈ {−2, …, +2} |

**Why these forms.**
- **H1 is a DiD, not a G-only contrast.** Attachment adds text to many chunks: distractors, and C gold
  chunks whose aliases also occur in them. A G-only gain could be a general shift that C records share.
  The DiD reads the alias-specific part. C is the **null control**: no C-only hypothesis is tested, and
  the C contrast on its own (merged − none) is a printed secondary. A paper scores equal weight for its G
  pair and its C record.
- **H2 is neighbour vs random, not neighbour vs base.** Adding any papers adds distractors. The random arm
  holds the number of added papers and their field fixed, so H2 reads what is specific to *citation*
  neighbours. Neighbour − base and random − base are printed secondaries.
- **H2 counts judged answers, and this works against the hypothesis.** A neighbour is more likely than a
  random paper to *answer* a question, and an answer from another paper counts as a hit (§5). So H2 asks
  whether neighbours displace more than they add. **found@10-gold** is the declared secondary that isolates
  displacement alone. The two are always printed together.
- **H3 is two-sided.** Under neighbour expansion the reranker could lose more than BM25 (same-vocabulary
  distractors outrank gold), or less (the reranker separates meaning that BM25 cannot). Both would be
  findings. A confirmed H3 is reported with its sign.
- **H2 and H3 are on glossary none.** This keeps the corpus factor from being confounded by attachment,
  which itself depends on the corpus. The same contrasts under glossary merged are printed secondaries.

**Secondary (printed, unadjusted, outside F).** H1–H3 under every other retriever, corpus arm and
glossary arm (H1 on the neighbour and random corpora; H2 and H3 under glossary merged). The G-only and
C-only glossary contrasts. Neighbour − base and random − base. found@10-gold beside every
found@10 figure. H1 split by alias kind (symbol / abbreviation / both). H1 split by whether the record's
alias collides across papers (ABBREV.md *collision*). Hits split into gold, same-paper judged and
other-paper judged. The 134 records on the 76 unsatisfied papers, as a descriptive leg (§3.2).

**Exploratory.** Anything else, labelled *exploratory* in every sentence that quotes it.

"No difference" is never written. A one-sided member that is not significant is *not confirmed*, with its
power floor beside it (§7). A member whose minimum attainable p exceeds α is **GATE-NOT-EVALUABLE** (§8).

## 3. Instrument: the record set, as built

### 3.1 What it is

The record set was built and closed before this text was drafted. It is frozen as is: no record is
added, redrafted or dropped for any reason after the freeze.

| item | value |
|---|---|
| accepted records | **734** (G 458, C 276) on **276** papers |
| satisfied papers (≥ 2 accepted G on distinct aliases and ≥ 1 accepted C) | **200**, each with **exactly 2 G and 1 C** (the stop rule ends a paper at its goal): **600 records**, 400 G / 200 C |
| records on unsatisfied papers | 134 (G 58, C 76) on 76 papers |
| record set file | `accepted-alias-O.jsonl`, sha256 **`4aa0a36d9b1f3dc876a4f4d8b275d02b4ef1e15d4d014a4a1545b0cd4e13bcb0`** (private) |
| alias kind of the 600 (G / C) | abbreviation 335 / 170, symbol 63 / 26, both 2 / 4 |
| gold | one source chunk per record (`gold_chunks` length 1 on all 734) |
| corpus | the OA-1000 chunk export, 32,765 chunks @512, 12,560 documents, 1,000 papers (sha256 `ff16e611…`) |
| merged glossary | sha256 `229519d7…`, built at `34fa58f` (#887); abbreviation entries `0f2c37e3…` |
| pool | seed **`261031|alias|main|O`**; pool sha256 `b49b2daf…`; 857 eligible papers (eval-paper list sha256 `4849699c…`); schema `glossbench-alias-pool-manifest/1`, built at `9350aac` (clean, `citable true`) |
| wave activation | seeded order `<pool seed>|waves` over the reachable papers; 355 papers activated; `c_first`; target **200** satisfied papers |
| drafter | prompt **`alias-draft/2`** on all 734; Gemini 2.5 Pro via the gateway, temperature 0, `max_tokens` 16384, ≤ 4 in flight; served id `Gemini 2.5 Pro` on all 1,946 draft rows |
| layer 1 | `glossbench_alias_checks` mechanical rules (quote ≤ 300 chars, leak threshold 0.5, question 5–40 words, answer support ≥ 0.5, primary ≥ 5 chars) |
| layer 2 verifier | prompt **`alias-verify/3`** on all 734; Claude CLI `claude-opus-5-5` off the gateway, with a Codex fallback after a CLI safeguard stop |
| verdict source of the 734 | `claude-opus-5-5` **702**; Codex (`gpt-6-astra`) **32**, of which 26 followed a CLI safeguard fallback that the CLI answered with `claude-opus-5`, and 6 followed a safeguard API error |
| run summary | written at `233cdfb` (clean, `citable true`); main config sha256 `c5e960d2…`; gateway calls 1,952 for the main run (2,080 including 128 from the pilots); verifier CLI calls 1,083, Codex 51 |

**Code commits of the build.** The pool was built at `9350aac`. The waves ran in four segments, at
`9350aac`, then `1026576`, `617fd8a` and `233cdfb`, each change sealed in the run log (§3.3). The
summary was written at `233cdfb`, which is the merge of #895. The pipeline modules are
`glossbench_alias_pool.py`, `glossbench_alias_waves.py`, `glossbench_alias_draft.py` and
`glossbench_alias_checks.py` (layer 1, the alias-verify/3 prompt; #889 for the current layer 1, #891
for the safeguard-fallback detection, #895 for the same-model re-ask fix). The abbreviation builder is
`alias_abbrev.py` (#887, `34fa58f`).

### 3.2 Which records carry F

**The 600 records on the 200 satisfied papers carry F** (proposed; §13 O2). That layout is exactly
balanced: 2 G + 1 C per paper, so H1's per-paper DiD is defined for every cluster. The 134 records on the
76 unsatisfied papers are run in every cell. They are reported as a **descriptive leg** and never enter
a test. Satisfaction is a property of the build, fixed before any retrieval, so the split cannot depend
on an outcome.

### 3.3 Disclosures about the build

1. **Built blind to retrieval.** The pool, the drafter, both verifier layers and the stop rule read no
   retrieval result, because none existed for these records. The yield model behind the wave sizes reads
   acceptance rates only.
2. **The target was extended mid-run.** The first segment closed with 445 accepted records. The owner then
   extended the target from 100 to 200 satisfied papers and raised the caps, and the config was resealed
   in the run log (config sha `e802a99c…` → `c5e960d2…`). The decision used acceptance counts only.
3. **Two isolation hard stops** were cleared by the owner and recorded in the run log. One was a CLI
   safeguard fallback to `claude-opus-5`, not a leak (#891). The other was a same-model CLI re-ask, fixed
   in #895. **At the first clearance, the run log records that 27 verifier verdicts written after a CLI
   safeguard fallback were kept by owner decision and noted.** The 32 Codex-verdict records are flagged
   (§3.1). A sensitivity without them is printed beside H1–H3 (§6).
4. **Pilots.** No pilot record is in the set (`pilot: false` on all 734). Pilot papers were **not**
   excluded from the main pool: 12 of the 276 papers (8 of the 200 satisfied) were also pilot papers. The
   pilots were drafted under earlier prompt versions. Their records were read to develop the pipeline and
   were never retrieved.
5. **Calibration overlap (7 records).** The judge's calibration set was built from the two pilots' records.
   Seven calibration record ids also occur in the main draft log, because a pilot and the main run drafted
   the same job. **3 of the 7 are among the 734** (1 G, 2 C; 1 on a satisfied paper). For those 3, BM25
   candidates over the chunk export were computed for their pilot versions during calibration. This is the
   only retrieval-like computation that has touched any study record id. A sensitivity without them is
   printed (§6). The judge's validation set excluded every study paper (§5.4), so the judge's accuracy
   figure is out of sample with respect to the records.
6. **Layer-3 record validity is open.** No owner spot-check of the 734 records has been recorded. §13 O9
   proposes one with round 3's rule.

## 4. Design: factors and cells

Four factors. Corpus arm, retriever and glossary arm define the **18 cells** that every record is run in.
Question type (G / C) is a property of the record.

### 4.1 Corpus arm

| arm | content | built how |
|---|---|---|
| **base** | OA-1000 as it stands (32,765 chunks) | the existing OA-1000 bench copy, never the live collection |
| **neighbour** | base + the **citation neighbours** of the 200 satisfied papers: per study paper, up to **5 references and 5 citers** (proposed caps, §13 O3), open access only, papers already in OA-1000 not added again | a new bench collection. Base chunks are copied **with identical chunk ids and content**. Each added paper goes through the **same ingest** (chunker `tok512o0`, the same embedder and version, the same tools image) and the **same glossary pipeline** (§4.3) |
| **random** | base + **an equal number of random open-access papers from the same field**, matched per study paper: as many as that paper received neighbours, drawn from its field (definition: §13 O4), excluding OA-1000, every neighbour and every study paper | a new bench collection, built like neighbour |

Selection when a paper has more references or citers than the cap is seeded: `<seed base>|nbr|refs|<paper>`
and `…|citers|<paper>`. Random papers are drawn with `<seed base>|rand|<paper>`. Every choice is made
from metadata only, with no retrieval and no record. The realised counts are written to a manifest
before any retrieval and quoted in every caption: neighbours found, open access, already in base, added
per paper, and chunks added per arm.

### 4.2 Retriever

| id | definition |
|---|---|
| **BM25** | `retrieval_mode=bm25`, `rerank=false`, top 10 |
| **default** | the served path: hybrid (BM25 + SFR dense, `RETRIEVAL_CANDIDATE_MULTIPLIER` 2, RRF k 60), cut to `RERANK_CANDIDATES` 50, rescored by the cross-encoder (`BAAI/bge-reranker-v2-m3`) on `content + "\n" + symbol_glossary`, top 10 |
| **norerank** | the same hybrid with `rerank=false`, top 10 |

Every request carries `use_graph=false` and `rewrite_strategies ["passthrough"]`, as round 3's
`FIXED_REQUEST` does. The settings are checked against `/v1/config` where it exposes them and are
otherwise declared, as in the reranker test §3. Which retrievers are confirmatory is §13 O5.

### 4.3 Glossary arm

| arm | rendered `symbol_glossary` field |
|---|---|
| **none** | empty on every chunk |
| **merged** | the paper's merged glossary (symbol + abbreviation), rendered where the entry's alias occurs as a whole token (the **mention** rule; abbreviations state no value), under the served caps (12 entries, 1,200 characters) |

The arms are swapped by the backfill on each bench collection, with the round-3 stamp census, and no
re-indexing. For added papers, the glossaries are generated **by the same pipeline at the same versions**:
the symbol glossary model and prompt the round-3 file used, the `rules/3` merge, and `alias_abbrev.py` at
the commit that built the base file. Their file shas are recorded. The judge's definitions block (§5.2)
reads the merged glossary in every cell, so the judge context does not depend on the glossary arm.

**Manipulation check, read offline before the freeze (§14):** the number of G records whose gold chunk
renders the record's own entry under the caps. This is the glossary's *reach*. A G record whose entry does
not render on its gold chunk can differ between arms only through distractors. **Proposed gate:** at least
**200 of the 400** G records reached. Below that, H1 is GATE-NOT-EVALUABLE. The reach is quoted beside H1.

### 4.4 Volume

18 cells × 734 records = **13,212** retrieval calls, plus the noise-floor repeat (§8.4), at ≤ 4 in
flight against the bench copies. No gateway call is made for retrieval.

## 5. Outcome and judge

### 5.1 The outcome

**found@10.** A record has a hit in a cell when the gold chunk is in the top 10, **or** any other top-10
chunk has the final judge label *answers* (§5.3). **found@10-gold** is printed beside it everywhere.

### 5.2 What is judged, and blindness

- **Only top-10 non-gold chunks** are judged. The gold chunk is a hit by definition.
- **Pairs are deduplicated across cells.** The unit is (record, chunk). One final label serves every cell
  in which that chunk appears in that record's top 10.
- **Blind to arm, retriever and rank.** The pair prompt (`alias-judge/2`) carries the question, its term,
  the passage, the passage paper's title, and the passage paper's own definitions (its merged-glossary
  entries whose alias occurs in the passage, at most 25, sorted). It carries **no** corpus arm, retriever,
  glossary arm, rank, score or count of cells. Because pairs are deduplicated before labelling, no
  label can depend on which cell produced the pair. Pairs are labelled in a seeded shuffled order
  (`<seed base>|judge|order`).

### 5.3 The judge (owner decision)

| role | model | prompt | settings | label |
|---|---|---|---|---|
| **primary C** | `Qwen3.6-35B-A3B` | `alias-judge/2` | thinking on, n = 5, T 0.7, `max_tokens` 8192, served-model check | majority of the valid samples |
| **primary E** | `Qwen3.8-27B` | `alias-judge/2` | thinking on, n = 1, T 0.7, `max_tokens` 8192, served-model check | the one sample |
| **tiebreaker** | `Gemini 4 Argon` via the gateway | `alias-judge/2` | `max_tokens` 4096, default temperature, `fallback_model ""`, ≤ 4 in flight, budget stop | its label |
| **drift reference** | `Claude Opus 5.5` via the gateway | `alias-judge/1` (the calibration reference's prompt; §13 O6) | 1 repetition, `max_tokens` 2048 | not used for any hit |

**Rule.** When C and E both give a label and agree, that label is final. Otherwise the pair goes to the
tiebreaker, and its label is final. "Otherwise" covers three cases: C and E disagree, C has no majority,
or E's sample is invalid. A tiebreaker reply that is empty or unparseable is re-issued once. If the pair
still has no label, it is **unjudged**. An unjudged pair counts as *not answering* in the primary analysis.
The count of unjudged pairs is printed, and a sensitivity counts them as *answering*. If unjudged pairs
exceed **1 %** of judged pairs, every found@10 figure carries *judge incomplete* in its sentence. A reply
whose served model id is not the requested one stops the run and is not stored.

**Drift reference.** A seeded **5 %** sample of the deduplicated pairs (`<seed base>|drift`, drawn per
labelling batch so that drift shows during the run) is re-labelled by Opus. Agreement is computed on the
pairs where Opus answers. **Alarm:** agreement between the final label and Opus below **0.97** on at least
100 Opus-answered pairs, cumulatively or in any batch, **pauses labelling**. The pause is reported to the
owner and recorded as a deviation. Opus labels never replace a final label. The Opus-filtered share of
the sample is printed. **Topic filter:** Opus via the gateway returns empty answers on some questions,
clustered on vaccine and virus topics: 33 of 410 calibration pairs and 83 of 587 validation pairs had no
answer on any attempt. The Claude CLI refuses most of the same pairs, while Argon and both Qwen judges
answer them. A filtered pair is **not** a drift failure. It is excluded from the drift agreement and
counted.

### 5.4 Why this judge: calibration facts (counts only)

These are the facts behind the owner's choice. Each is a calibration result with its commit. None is a
study result.

| fact | value | where |
|---|---|---|
| in-sample C + E agreement rule (410 pairs, 372 Opus-unanimous) | accuracy 0.992, 2.7 % escalated | JUDGE-CALIBRATION.md rounds 2–3 (#896 `65a58e1`, #897 `9dabeb4`) |
| **out-of-sample validation**, plan frozen first (`e2713e1`), run at `390777d` | **0.992** [0.979, 0.997] on 487 Opus-unanimous pairs; 2.5 % escalated; PASS against ≥ 0.98 and ≤ 5 %; 377 papers excluded, every study paper among them | JUDGE-VALIDATION-PLAN.md; JUDGE-CALIBRATION.md *Validation* (#898) |
| **tiebreaker selection**, plan frozen first (`10a986c`) | `Gemini 4 Argon` **0.996** agreement with Opus on the 487 (κ 0.988; 0 of 83 filtered pairs unlabelled); `GPT-5.6 Sol` 0.990; Argon selected on agreement | TIEBREAKER-PLAN.md; JUDGE-CALIBRATION.md *Tiebreaker* (#899, `5e6a722`) |
| residual errors of the gate | 4 on 475 accepted pairs, all false yes, 3 of them on same-paper non-gold chunks (lex_same) | JUDGE-CALIBRATION.md *Validation* |
| the composed rule (C + E, then Argon), replayed on the validation labels | 482 of 487 (0.990); 12 sent to Argon, 11 of them right; 0 unlabelled | **scratch replay for this draft, post-selection, not citable**; the committed replay is a pre-freeze item (§14) |

**What the calibration does not show.** Validation scored the C + E rule with **Opus** labelling the
escalated pairs. The composed rule with Argon was chosen after both results were seen, and its replay is
post-selection. The **coll_diff** stratum is decidable from the definitions block alone and is not
evidence of judgement. Every pair in both sets came from **base OA-1000**. Pairs from neighbour or random
papers are new ground: they have their own definitions blocks and possibly denser collisions. The drift
reference and the owner's spot-check (§12.4) are what cover them. The calibration and validation pairs
are BM25-constructed candidates. They are not the retriever outputs judged here.

### 5.5 Volume (planning only)

There are at most 10 non-gold chunks per record and cell, so 13,212 lists give an upper bound of
**132,120** pair instances before deduplication. The deduplicated count is known only after retrieval.
At the validation throughput, judge C took about 1.85 s per pair and judge E about 0.74 s per pair (4 in
flight). The tiebreaker load is expected at about 2–2.5 % of pairs and Opus at 5 %. **Gateway caps for
the judge** (proposed; §13 O7): Argon ≤ 3,000 calls and Opus ≤ 6,000 calls. The budget stop and
`PAUSE` apply, as in the calibration harness.

## 6. Analysis

Round 3 §7 machinery (`chunktrial_stats`), with these specifications:

- **Test:** paper-clustered sign-flip permutation on the per-cluster statistic of §2, 10,000 flips, seed
  `<seed base>|perm`, exact enumeration at ≤ 20 non-zero clusters, p = (1 + #{≥ obs}) / (1 + n). H1 and
  H2 are one-sided and H3 is two-sided.
- **Holm over 3**, gated or absent members at p = 1.0. `holm_drop_gated` is a labelled sensitivity that
  never decides.
- **Beside every member:** 90 % paper-bootstrap percentile CI of the net statistic (10,000 resamples);
  the gained / lost record counts; the number of non-zero clusters; the minimum attainable p; the
  realised power (§7); `mde_split`; found@10-gold.
- **Declared sensitivities**, printed and never deciding: without the 32 Codex-verdict records; without
  the 3 calibration-overlap records; without the 8 satisfied papers that were pilot papers; unjudged
  pairs as *answering*; tiebreaker-labelled pairs set to each judge's own label in turn.
- **Missing data:** a record whose retrieval call failed in either cell of a contrast is excluded from it
  and counted. `family_missing_as_not_found` is the one pre-named sensitivity. More than 2 % failed calls
  in a leg is a hard stop.
- Every analysis is the committed script. Scratch analyses are not citable.

## 7. Power and MDE

**Layout:** 200 clusters × (2 G + 1 C), realised and fixed. No layout uncertainty remains. The unknown is
discordance: the share of records whose hit changes between the two cells of a contrast.

**Planning sketch** (a scratch simulation made for this draft, **not citable**; the committed calculation
is a pre-freeze item, §14). Each record gets d ∈ {−1, 0, +1}. It is non-zero with probability q, and
positive with a paper-level share s ~ N(s, 0.15). The test is the cluster sign-flip at α/3, with 1,000
simulations × 2,000 flips, seed 20261031. For H1, C records are discordant at q = 0.05 with share 0.5
(no effect on C). H3 is approximated by the same three-valued d, two-sided.

| member (n) | q .05, s .70 | q .05, s .85 | q .10, s .70 | q .10, s .85 | q .20, s .70 | q .20, s .85 |
|---|---|---|---|---|---|---|
| **H1** DiD (400 G vs 200 C) | .10 | .32 | .31 | .78 | .76 | 1.0 |
| **H2** one-sided (600) | .41 | .95 | .78 | 1.0 | .98 | 1.0 |
| **H3** two-sided (600) | .34 | .90 | .71 | 1.0 | .97 | 1.0 |

**Read this before choosing H1's form.** H1 is the weakest member, because the C control enters each
paper's DiD with the weight of two G records. H1 needs about 10 % of G records discordant at a share of
.85, or 20 % at .70. **Round 3 saw 0 discordant records between symbol glossary and none under the
served path on its set**, which is a different population and only the symbol glossary. If G discordance
here is as low, H1 ends *not confirmed* with this floor printed beside it. It is not a null.

**Evaluability floor.** With k non-zero clusters the minimum attainable p is 2^−k one-sided and 2^(1−k)
two-sided. At the Holm worst case α/3 a one-sided member needs k ≥ 6, and H3 needs k ≥ 7.

## 8. Gates, stops, and restore points

### 8.1 GATE-NOT-EVALUABLE (a third answer, preserved in every table)

A member is GATE-NOT-EVALUABLE when:
- its minimum attainable p exceeds α (too few non-zero clusters);
- its cells have **fewer than 8 clusters** with records (`MIN_CLUSTERS`);
- any of its cells failed the depth read-back without an `exhausted_verified` verdict (§8.2);
- it names the default retriever and the **inactive-reranker gate** fired: default's top 10 equals
  norerank's on every record in that corpus × glossary condition;
- (H1 only) the glossary reach gate failed (§4.3);
- a census of any of its cells failed (§8.2) and was not repaired and re-run.

A GATE-NOT-EVALUABLE member enters Holm at p = 1.0. **Absent** is written *absent*, never left blank.

### 8.2 Checks per leg (a leg is one cell run over all records)

- **Census before and after:** the chunk count of the collection equals the corpus arm's manifest (base
  32,765; the expanded arms as built). The `metadata.symbol_glossary_version` stamp census equals the
  glossary arm. Every gold chunk id is present, with the same content hash, in all three corpus arms.
- **Admin config hash:** `/v1/config` is read at the start and the end of the leg. The two reads must be
  identical, as in round 3 §5 note 2(c). A difference stops the leg, and the leg is re-run after the cause
  is found and recorded.
- **Depth read-back:** the served depth is checked against the requested depth on every request.
- **BM25 exhaustion verifier** (#877, `bm25_exhaustion.py`, round 3 §5 note 3, unchanged): a short `bm25`
  list costs one read-only `_count` on the leg's bench index, on the exact tenant scope. The verdicts
  are `exhausted_verified` (scored, not gated), `clamp` (hard stop), `query_mismatch` (hard stop) and
  `under_served` (gated).
- **Glossary boost:** the round-3 behavioural probe rule. Where the probe is structurally `not_testable`,
  the boost is declared from the serving release and the label `declared` is printed, never `pass`.
- **Hard stops:** more than 2 % failed calls; a `clamp` or `query_mismatch` verdict; a config-hash
  difference; a served judge model id that is not the requested one; a judge budget stop; a frozen-prereg
  sha mismatch in any stage that reads `prereg.file`.

### 8.3 Restore points

Every store write is made on a **bench copy**, never on a live collection. Store writes are building the
two expanded collections and every glossary swap. Each write goes inside a management window, under a
reviewed live-ops plan. Before each write a **restore point** is recorded: the snapshot id, the chunk
count and the stamp census. After the write the census must match the plan. At the end of the run every
collection is restored to its recorded state, and the restore is verified by census. A failed restore
blocks every later leg until it is repaired.

### 8.4 Noise floor

One cell (base × default × none) is run **twice**. The run-to-run discordance is printed beside every
figure. Round 1's floor was 0–2 records. No result is re-run to move the floor.

## 9. Code, pin, provenance

**Exists on `main` (at or before `bee9626`):** the record pipeline (§3.1); the judge harness
`glossbench_alias_judge.py` with the Qwen, gateway and Opus backends, the served-model check,
`analyse-gate` and `analyse-tiebreaker` (#894–#899); `bm25_exhaustion.py` (#877); the round-3 runner
and analysis (`found10.py`, `found10_analysis.py`, `chunktrial_stats`), the backfill and the stamp
census.

**Must be written, reviewed and merged before the freeze (§14):** an alias adapter for the runner (the
records schema; the 18 cells; the three corpus collections); the pair builder and the deduplication for
judged pairs; the composed-rule labeller (C + E, then Argon), with the drift sampler and its alarm; the
DiD statistics for H1 and H3, with tests; a glossary-file writer for the merged glossary that the
backfill renders, with render parity tests; the neighbour and random selectors (metadata only, seeded).

**Pin.** Every run is from one `main` commit, at or after the merge of the last item above, in a clean
worktree (`dirty false`, `untracked 0`), or from a tools image built from a tag at or after it (ADR-0010).
Every output embeds `experiment_provenance()`. A dirty run is not citable.

## 10. Data provenance, privacy, and what is released

- **Private, never in this repository:** the records (questions, answers, quotes), the chunk exports,
  the glossaries, the paper lists, the neighbour and random manifests with ids, the judged pairs and their
  labels, and the per-record hits.
- **Released (public, counts only):** this prereg; `RESULTS.md`; `DEVIATIONS.md`; `artifacts/*.json`
  holding counts, rates, CIs, p values, model ids, version strings, commit shas and sha256 hashes of the
  private files; per-paper quantities by paper rank only. A committed check refuses any public artifact
  that carries record text or ids, in the style of round 3's `string_values` and `--omit-text`.
- **Model calls.** Judge C and E run on local endpoints. Argon and Opus run through the gateway under its
  standing terms (`fallback_model ""`, `max_tokens` always, ≤ 4 in flight, size notice per batch, budget
  stop). Pair prompts carry chunk text to the gateway, within those terms. Retrieval makes no model call.

## 11. Deviations policy

After freeze the prereg is never edited. Any change to a hypothesis, cell, record, judge, threshold, seed,
gate or analysis is a dated entry in `DEVIATIONS.md` stating what changed, why, when, what had been
seen at that time, and which results it touches. A result whose computation depends on a deviation says
so in its sentence. Re-running a leg after a hard stop is a deviation entry, not a silent retry.

## 12. Limitations and threats to validity

1. **One corpus.** OA-1000 only. The expanded arms add papers around it but do not make a second corpus.
2. **The population is glossary papers.** Records exist only for papers with ≥ 2 usable G entries and ≥ 1
   C. The 200 satisfied papers are those the stop rule reached first in a seeded order, and the estimand
   is conditional on them. Abbreviations dominate: 335 of 400 G records on satisfied papers.
3. **The judge is not truth.** found@10 inherits the judge's errors. On the validation set those errors
   were few (§5.4) but were false yeses on same-paper non-gold chunks, which inflate found@10
   symmetrically across arms only if such chunks are retrieved equally often.
4. **Layer-3 judge validity:** the owner spot-checks a seeded sample of judged pairs, blind to cell
   (proposed: 40 judged-*answers* pairs whose record had no gold in the top 10, plus 20 judged-*not*
   pairs; seed `<seed base>|spot|judge`; §13 O8). **Rule, as round 3's:** a validity point estimate below
   **0.85** on the *answers* pairs makes every found@10 result carry *judge validity below threshold* in
   its sentence. Nothing is re-labelled. The Wilson interval is printed beside it and decides nothing.
5. **Record validity** rests on layers 1–2 until an owner spot-check of records exists (§13 O9). The
   verifier's spot-reads found about 1 in 12 current-pipeline G records doubtful (VERIFIER.md).
6. **Neighbour definition.** References and citers come from a bibliographic source with incomplete
   coverage, and open-access availability filters them. The added set is a sample of the neighbourhood,
   not the neighbourhood itself.
7. **Forking paths.** The corpus and retriever factors were chosen after round 3's null and the reranker
   test's papers-OC reading (*What had been seen*). The record set's target was extended mid-build
   (§3.3). The judge's tiebreaker was chosen after its calibration results. Each is bounded by being fixed
   here before any retrieval on a study record.

## 13. Open decisions for the owner

| # | decision | proposal in this draft |
|---|---|---|
| O1 | **Hypothesis wording and the family** | H1 (glossary DiD on base × default), H2 (neighbour vs random, default, none), H3 (default vs BM25 DiD under neighbour expansion, two-sided). Holm over 3. Alternatives: H1 G-only, with C as a gate rather than a DiD (more power, weaker control); H2 neighbour vs base |
| O2 | Records carrying F | the 600 on the 200 satisfied papers; the 134 others descriptive |
| O3 | **Neighbour caps** | ≤ 5 references + ≤ 5 citers per study paper, open access, seeded selection over the cap |
| O4 | "Same field" for the random arm, and "equal number" | matched per study paper, in that paper's primary field from the bibliographic source; equal in papers, with chunk counts printed (alternative: equal in chunks) |
| O5 | **Which retrievers are confirmatory** | default in H1 and H2; default and BM25 in H3; norerank secondary everywhere |
| O6 | Prompt for the Opus drift reference | `alias-judge/1`, which keeps comparability with the calibrated 0.992 / 0.996 figures (alternative: `/2`, the primary judges' prompt) |
| O7 | **Judge drift threshold** and gateway caps | pause when agreement < 0.97 on ≥ 100 Opus-answered pairs; Argon ≤ 3,000 and Opus ≤ 6,000 calls; unjudged > 1 % marks *judge incomplete* |
| O8 | Layer-3 judged-pair spot-check size and split | 40 *answers* pairs (record had no gold in top 10) + 20 *not* pairs, 0.85 point-estimate rule on the *answers* pairs |
| O9 | Owner spot-check of the records | 40 records from the 600, seed `<seed base>|spot|records`, round 3's 0.85 rule; to be done before the freeze |
| O10 | **Whether to add GPT-6.1 Sol later** as a second tiebreaker or drift reference | not now: it is **not on the gateway allow-list**. Adding it later would be a deviation with its own frozen selection plan, as TIEBREAKER-PLAN.md was |
| O11 | Glossary attachment rule for the merged arm, and the reach gate | mention rule under the served caps; H1 gated below 200 of 400 G records reached |
| O12 | Seed base | `261101|alias|study`, fixed at freeze |

## 14. What must exist before freeze

| # | item | state |
|---|---|---|
| 1 | Owner decisions O1–O12 folded into this text | open |
| 2 | **Record-set sha** confirmed against the private file and recorded in the README (`4aa0a36d…`, §3.1); the 600 / 134 split recomputed by committed code | sha read for this draft; committed recomputation open |
| 3 | **Neighbour and random corpora built and censused on bench copies, within a management window** under a reviewed live-ops plan: the selection manifests (counts only in public), the ingest at the same chunker, embedder and tools image, base chunk ids and content verified identical, the restore points recorded | open |
| 4 | **Glossaries for every added paper** generated by the same pipeline at the same versions (symbol model and prompt, `rules/3`, `alias_abbrev.py`), merged, and their shas recorded; a census of entries per added paper | open |
| 5 | The merged-glossary file the backfill renders, with render parity tests; the **reach count** of §4.3, read offline against the gate | open |
| 6 | **Offline power calculation** by committed code on the realised layout, replacing the scratch sketch of §7, with a clean tree and `citable true` | open |
| 7 | The committed replay of the composed judge rule on the validation labels (§5.4), replacing the scratch replay | open |
| 8 | The code of §9 *Must be written*, merged after an independent review | open |
| 9 | Owner spot-check of the records, if O9 is accepted | open |
| 10 | A dry run of the whole chain on base × BM25 × none for **non-study records only** (for example the validation set's), to check the plumbing without reading any study outcome | open |
