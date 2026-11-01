# Pre-registration, alias study (OA-1000) — DRAFT v1, 2026-11-01

**Status: DRAFT — not frozen; owner review pending.** This version folds in the owner's ten decisions of
2026-11-01 (table below), and their later decisions of the same day on O1 (the C gate, rule C), O2
(H1b routing), O5 (shared neighbours), O6 (text format and the control-character screen) and O9 (seeds),
with the glossary pipeline for added papers (§5.3). The items still open are in §14. Nothing in this text binds a run until it is
frozen by the procedure in this directory's `README.md`. At freeze the text is frozen **as written**, and
its sha256 goes into the README and a private `PREREG.sha256`, never into this file. After freeze this
file is never edited. Any later change is a `DEVIATIONS.md` entry.

> **Public copy.** The file names no tenant, host, endpoint, collection, person or gateway owner. The corpus
> is named by its alias, **OA-1000**. It holds counts, rates, model ids, version strings, commit shas and
> sha256 hashes only. It holds no question, answer, quote, passage, paper id, record id or file path. The
> partner whose gateway carries the model calls is "the papers-OC owner" where it has to be mentioned.

**Owner decisions of 2026-11-01, and where each is written.**

| # | decision, in short | where |
|---|---|---|
| D1 | H1 = G records only, base, default, merged vs none, one-sided; C as a **gate**; H1b = G-vs-C DiD, no extra Holm member (routing amended by §14 O2, Option B: tested at α = 0.05 only if H1 **and** H2 are confirmed, fixed sequence after the Holm pair); the H1 × H1b table | §3.1, §3.2, §3.4, §9.1 |
| D2 | H2 = neighbour vs random (average of two draws), default, none, one-sided, judged found@10; confirmed only if gold-only found@10 also drops (a gate, not a third test); drift stratified by arm; per-arm false-yes rates | §3.1, §3.2, §6.3 |
| D3 | two layers: Layer 1 confirmatory (H1, H2 under Holm over 2; H1b after both H1 and H2, §14 O2); Layer 2 serving decisions on CI-based thresholds, no gates; H3 moves to Layer 2 with a 95 % CI | §3 |
| D4 | neighbour caps: ≤ 5 references (most cited) + ≤ 5 citers (most recent), open-access full text, via OpenAlex, deduplicated against OA-1000 | §5.1 |
| D5 | random arm "same field": matched count per study paper, same OpenAlex primary topic, OA full text, no citation link to any study paper, year ± 3, seeded content-blind rule | §5.1 |
| D6 | two random draws; one noise repeat per arm; a rank-based secondary (Layer 2); H2 power from the realised layout; raising the caps is the lever | §5.1, §5.4, §3.3, §8 |
| D7 | merged glossary attached by mention, same pipeline for added papers; reach gate ≥ 80 %; Layer-2 split by alias type | §5.3, §9.1, §3.3 |
| D8 | judge as in v0; Opus drift sample stratified equally across arms; alarm < 0.97 overall or < 0.95 in any arm, after every batch; served-model check | §6.3 |
| D9 | owner spot checks: 40 records (0.85 rule, blind to G/C) and 60 judged pairs (10 per arm × glossary cell, < 0.90 rule), labelled and committed before analysis | §13.4–13.5 |
| D10 | a prior-results section, with the disclosure that prior evidence points against H1 | §2 |

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
  answer the question (§6). A correct answer from **another paper** counts.
- **found@10:** the share of records with a hit, quoted in **points** (percentage points of records).
  **found@10-gold:** the gold-only version.
- **RR@10:** the reciprocal rank of the first correct chunk in the top 10, 0 if none; judged and gold-only
  versions (Layer 2 only).
- **Cell:** corpus arm × retriever × glossary arm (§5).
- **Arm** (where a judge or spot-check rule says "per arm"): the corpus arm group **base**, **neighbour**
  or **random** (the two random draws together).
- **Cluster:** the record's paper.

**What had been seen before writing.** Rounds 1–3 of the glossary benchmark and the reranker test in full,
including their records, deviations and addenda (`../../glossbench-round3-2026-11/`,
`../../reranker-test-2026-11/`). §2 states what from them shaped this design. Also seen: the alias-study
notes in `docs/plans/alias-study-2026-11/` (ABBREV, VERIFIER, JUDGE-CALIBRATION, JUDGE-VALIDATION-PLAN,
TIEBREAKER-PLAN), and draft v0 of this file with its review (#900). Read only as counts were the private
build outputs of the record set (§4), the judge-calibration, validation and tiebreaker outputs (§6.4), and
scratch computations made for the drafts: the v0 power sketch, the normal-approximation powers of §8, a
replay of the judge rule on the validation labels (§6.4), and the recounts of §4 that are marked as
scratch, including the overlap between the study papers and the judge validation set (§4.3 item 6). **No retrieval of any kind has been run on any study record**, and no found@10 figure for any
record exists under any arm (§4.3 item 6 lists the one partial exception). **No neighbour or random paper
has been selected**, and no OpenAlex query for this study has been made. The study's corpus-arm design and
its retriever factor were chosen after seeing round 3's null and the reranker test's reading that the
reranker can lose to BM25 on some corpora. H1's form (G-only, with C as a gate) and the extension of the
record set from 200 to 300 papers were both decided after v0's power sketch showed the DiD form weak, on
that sketch and acceptance counts alone. All of this is disclosed so that a reader can weigh it (§13.7).

## 1. Question

The question has two parts, both the owner's.

1. **Glossary.** A question names a concept by its full term, and the passage that answers it uses only a
   short form: a symbol, or an abbreviation the paper defines once, somewhere else. Does attaching the
   paper's merged glossary to its chunks make that passage findable? The **C** records hold the same
   machinery fixed on passages that already carry the term. They check that a glossary effect on G is not
   a general change in what is retrieved.
2. **Corpus neighbourhood.** OA-1000 is a sample of unrelated papers. A real collection holds a paper
   *with* its references and its citers, which use the same vocabulary and the same short forms, often
   with different meanings (about half of the abbreviations shared between papers collide; ABBREV.md).
   Does adding a study paper's citation neighbours cost more than adding the same number of random papers
   from the same field? And how does the served reranker fare against BM25 when the neighbours are added?

The study informs the decision on whether to ship the merged glossary, and on whether the reranker default
holds up in a related-paper collection. It does not make either decision.

## 2. Prior results that bear on this design

These are the earlier records this design rests on. They are cited, not re-measured. Each figure is in the
named record.

1. **Round 3 (glossary attachment, OA-1000) points against H1.** Round 3 attached the symbol glossary by
   the broad **mention** rule (every chunk that mentions the symbol) and compared it with the served
   **value** rule. On the stratum the served rule cannot reach (M_num), `mention` gained **1** record and
   lost **9** (found@10 134 vs 142 of 149). Its H1 (a gain) was *not confirmed* with power .90 at the
   planned effect: a **powered null for the hypothesised gain**. The adverse direction is descriptive (a
   one-sided test), and the unadjusted pooled secondary was 1 gained to 14 lost (two-sided p 0.0017). Under
   the served path, `value` and no glossary found **exactly the same records** (0 discordant). `mention`
   put a field on 18.1 % of OA-1000 chunks against 0.9 % for `value`.
   (`../../glossbench-round3-2026-11/RESULTS.md`, *The answer*, *What this shows*, *The proxy*.)

   **So the prior evidence on this corpus points against H1, and it is tested anyway, for this reason.**
   Round 3's records asked for a quantity whose answer chunk binds a symbol to a value. The glossary could
   add only a definition of the symbol, and the question's words were often already on the chunk. The G
   records here are built the other way: the answer chunk uses the alias **without a value binding and
   without the term** (the strict rule, Terms), so the alias → term entry is the only lexical bridge
   between the question and the passage. That is the case the glossary is meant for, and round 3 did not
   sample it. The merged glossary also adds defined abbreviations, which round 3 did not attach at all.
   The distractor load that round 3 measured applies here too: mention attachment adds text to many
   chunks. The C gate (§3.1) and the C-cost threshold of Layer 2 (§3.3) are there for that.

2. **The reranker test motivates the corpus-neighbour question and H3.** On the papers-OC owner's set
   (exploratory by design, **7 papers**, every hypothesis GATE-NOT-EVALUABLE), BM25 alone found 61 records
   at top 10 and the served default 52, of 153 (20 lost, 11 won), descriptively. On the confirmatory
   docs-A set the default beat BM25 (707 vs 629 of 732). The post-hoc *why* analysis is exploratory and
   **unresolved**: none of the four candidate explanations describes the data well, and 11 of the 20 lost
   records come from one query. (`../../reranker-test-2026-11/RESULTS.md`, *The answer*; `why/WHY.md`,
   *The headline*.) A collection of related papers is the setting the papers-OC reading points at. H3
   asks it descriptively (§3.3), because one exploratory reading on 7 papers is not a basis for a
   confirmatory claim.

3. **Rounds 2 and 3 taught two design lessons, and both are applied here.** Round 2's reach gate was
   **unattainable by arithmetic**: it needed 150 value-statement chunks, and the 25 sampled papers held
   144, 119 and 72. That count needed no model call and was not made before the freeze
   (`../../glossbench-round2-2026-11/RESULTS.md`, *The answer*). Round 3's papers-OC pilot passed **7 of
   100** jobs at layer 1, so that set was stopped (`../../glossbench-round3-2026-11/DEVIATIONS.md` D-1).
   Here, therefore: the record set is already built and counted (§4); the glossary reach (§9.1) and the
   neighbour and random counts (§5.1) are read offline **before** the freeze; and H2's power is computed
   from the realised layout before the freeze (§8), with raising the caps named as the lever.

4. **The C ceiling.** Round 3's found@10 under the served path was high: 142 of 149 and 56 of 57
   (`../../glossbench-round3-2026-11/RESULTS.md`, *found@10 per arm*). If C records sit near the ceiling
   under base × default × none, C cannot rise much, and both the C gate and H1b lose sensitivity to an
   upward general shift. The baseline C found@10 is therefore printed beside the C gate and beside H1b
   (§3.4).

## 3. Hypotheses, in two layers

The same runs feed both layers. **Neither layer changes the other**: a Layer-1 label does not move a
Layer-2 threshold, and a Layer-2 reading does not move a Layer-1 label.

### 3.1 Layer 1: the claim (confirmatory)

| id | hypothesis | records | cells compared | type | per-cluster statistic |
|---|---|---|---|---|---|
| **H1** | the merged glossary raises found@10 on G records | the **600 G** records of the balanced set (§4.2) | corpus **base**, retriever **default**: glossary **merged** vs **none** | superiority, **one-sided** (> 0) | per paper: Σ over its 2 G records of hit(merged) − hit(none) ∈ {−2, …, +2} |
| **H1b** | the gain on G exceeds the change on C (alias-specific) | the **900** balanced records (600 G, 300 C) | as H1 | difference-in-differences, **one-sided** (> 0); tested **only if H1 and H2 are both confirmed**, at α = 0.05 | per paper: ½ Σ over its G records of Δ − its C record's Δ, with Δ = hit(merged) − hit(none) |
| **H2** | citation neighbours reduce found@10 more than random same-field papers | the **900** balanced records | retriever **default**, glossary **none**: corpus **neighbour** vs **random** (the average of random-1 and random-2) | superiority, **one-sided** (< 0); **gold-only direction gate** | per paper: Σ over its 3 records of hit(neighbour) − ½ [hit(random-1) + hit(random-2)] |

**Gates specific to Layer 1** (in addition to the general gates of §9.1):
- **The C gate on H1 (rule C; owner, 2026-11-01, §14 O1).** H1 reads the G records alone, so a general
  shift that C records share would pass as a glossary effect. On base × default, glossary merged vs none,
  let ΔG be the net change in found@10 of the 600 G records and ΔC that of the 300 C records, both in
  points (merged − none). **H1 is one-sided (a gain on G), so the gate is evaluated only when ΔG > 0.**
  When ΔG ≤ 0, H1 cannot be confirmed whatever C does, the gate is **moot** (verdict printed as *C gate
  moot, ΔG ≤ 0*), and H1 is read as not confirmed on its own test. When ΔG > 0, H1 is
  **GATE-NOT-EVALUABLE**, and H1b is not tested, when **all three** hold: (a) ΔC has the **same sign** as
  ΔG, that is ΔC > 0; (b) |ΔC| ≥ ½ |ΔG|; (c) the 95 % CI of ΔC (paper-cluster
  percentile bootstrap, 10,000 resamples, seed `261101|alias|boot`, §5.5) excludes 0. **A C drop never
  trips the gate**: with ΔG > 0, condition (a) excludes it, and with ΔG ≤ 0 the gate is moot, so there is
  no case in which "same sign" and "a C drop never trips" conflict. The gate fires only on a C rise that
  keeps pace with a G rise. A C drop is read in Layer 2, by the C-cost rule (≤ 1 point, §3.3). Printed beside H1 whatever the verdict: ΔC with its CI, ΔG, the ratio ΔC / ΔG, and the
  baseline C found@10 (base × default × none) with its headroom to 100 %. **Ceiling note:** if the
  headroom is below ½ ΔG (in records, 300 C), condition (b) cannot be met upward, so the gate cannot fire
  however much C would have risen without the ceiling; the sentence quoting H1 then says *C gate
  ceiling-limited*.
- **The reach gate on H1** (§9.1): at least 80 % of G records' gold chunks render the record's own entry.
- **The gold-only direction gate on H2.** H2 is *confirmed* only if its judged test is significant **and**
  found@10-gold points the same way: the net gold-only change, neighbour − random average, on the same
  900 records, is below 0 at the point estimate. The gold-only figure is a gate, not a third test: it has
  no p value in the family. If the judged test is significant and the gate fails, H2 is *not confirmed
  (gold-only direction gate)*, and for the multiplicity procedure it counts as not rejected (§14 O3).

**Multiplicity: Holm over H1 and H2, then H1b in fixed sequence after both (owner, 2026-11-01, §14
O2).** α = 0.05, all members one-sided. In graphical (weight-passing) form (Bretz, Maurer, Brannath and
Posch 2009, *A graphical approach to sequentially rejective multiple test procedures*, Statistics in
Medicine 28(4):586–604, doi:10.1002/sim.3495):

| node | initial level | when it is rejected, its level passes to |
|---|---|---|
| **H1** | α/2 = 0.025 | H2 (weight 1); to H1b once H2 is also rejected |
| **H2** | α/2 = 0.025 | H1 (weight 1); to H1b once H1 is also rejected |
| **H1b** | 0 | — (last node) |

```
         weight 1
  H1  ────────────>  H2
 .025 <────────────  .025
         weight 1

  H1 and H2 both rejected  ──>  H1b tested at α = 0.05
```

- **H1 and H2 are Holm over 2.** Each is tested first at 0.025. If H1 is confirmed, its 0.025 passes to
  H2, which is then tested at 0.05; if H2 is confirmed, its 0.025 passes to H1, which is then tested at
  0.05. This is exactly the Holm procedure over the pair.
- **H1b is tested only if both H1 and H2 are confirmed, at α = 0.05.** If either is not confirmed
  (including *not confirmed*, GATE-NOT-EVALUABLE or absent), H1b is **not tested**; its estimate, CI and
  power are reported descriptively (§3.2). H1b adds no Holm member and costs neither H1 nor H2 anything.
- **H2's gold-only direction gate** failing makes H2 *not confirmed*: it passes no level to H1, and H1b is
  not tested (§14 O3).
- A GATE-NOT-EVALUABLE or absent member enters at p = 1.0, and the family is never shrunk (round 3 D14).

**Why this controls the family-wise error at 0.05 (strong control).** Let I be the set of true null
hypotheses. If I contains H1 or H2, a false rejection of H1b requires rejecting both H1 and H2, so it
requires a false rejection inside the Holm pair; the family-wise error is then the Holm pair's, which is
≤ 0.05. If I = {H1b} alone, the only possible error is p(H1b) ≤ 0.05, with probability ≤ 0.05. This is
the fixed-sequence argument, and the procedure is a closed test; in the graphical notation of Bretz et
al. it is the limit of the graph with ε-weight edges H1 → H1b and H2 → H1b as ε → 0.

**Why these forms.**
- **H1 is G-only, with C as a gate (D1).** The DiD form of v0 gave each paper's single C record the weight
  of its two G records and needed 10–20 % of G records discordant (v0 §7). The G-only test at Holm over
  2 is resolvable at much lower discordance (§8). The price is a weaker control, which the C gate and
  H1b pay back: H1b keeps the DiD reading, tested only where H1 has something to explain and H2 has also been
  confirmed (§14 O2); otherwise it is descriptive.
- **H2 is neighbour vs random, not neighbour vs base.** Adding any papers adds distractors. The random arm
  holds the number of added papers, their field and their age fixed, so H2 reads what is specific to
  *citation* neighbours. Two random draws, averaged, keep one unlucky draw from deciding H2 (§5.1).
- **H2 counts judged answers, and this works against the hypothesis.** A neighbour is more likely than a
  random paper to *answer* a question, and an answer from another paper counts as a hit. So the judged H2
  asks whether neighbours displace more than they add. The gold-only gate requires that the displacement
  itself goes the same way, so a judged drop that is only a judge artefact cannot confirm H2.
- **H2 is on glossary none.** This keeps the corpus factor from being confounded by attachment, which
  itself depends on the corpus.

### 3.2 Outcome interpretation, H1 × H1b

H1b is tested only when H1 **and** H2 are both confirmed (§3.1), so its *confirmed*, *not confirmed* and
*GATE-NOT-EVALUABLE* rows below arise only when H2 is confirmed.

| H1 | H1b | reading |
|---|---|---|
| **confirmed** | **confirmed** | the merged glossary makes alias passages findable, and the gain on G exceeds the change on C: an alias-specific gain |
| **confirmed** | **not confirmed** | G gained and the C gate did not fire (rule C), but the G gain was not shown to exceed the C change. The gain is on alias questions; its specificity is unresolved. Read beside the baseline C found@10 (ceiling, §2 item 4) and H1b's power floor |
| **confirmed** | **GATE-NOT-EVALUABLE** | as the row above, and H1b could not be read (for example too few non-zero clusters) |
| **confirmed** | *not tested* (H2 not confirmed) | G gained and the C gate did not fire; H1b's level was never released because H2 was not confirmed (including its gold-only gate failing, or H2 GATE-NOT-EVALUABLE). The specificity is unresolved; H1b's estimate, CI and power at α = 0.05 are printed descriptively, never as a test |
| **not confirmed** | *absent* (not tested) | no evidence that the glossary helps alias questions; the power floor of §8 is printed beside it. Not a null unless the power at the stated scenario is ≥ .80 |
| **GATE-NOT-EVALUABLE** | *absent* (not tested) | the design could not read H1: the C gate fired (C rose with G, rule C), the reach gate failed, or a general gate fired. Nothing is said about the glossary's effect on G, in either direction |

### 3.3 Layer 2: serving decisions (descriptive, CI-based, no gates)

Layer 2 reads the same runs as Layer 1. Every figure carries a **95 % paper-bootstrap percentile CI**
(10,000 resamples, seed `261101|alias|boot`, §5.5). No p value is computed in Layer 2, nothing in it is a test,
and no gate makes a Layer-2 figure unreadable. Each figure is printed with the gate verdicts that would
apply to it in Layer 1, for the reader, and is labelled *Layer 2, descriptive* in its sentence. Where a
CI is wide, the rule below does not fire, and the sentence says *not resolved*.

| decision | figure (900 balanced records unless stated) | threshold, fixed now |
|---|---|---|
| **attach the merged glossary** | G found@10 lift (merged − none, base × default, 600 G) and C cost (none − merged, 300 C) | attach if the G lift is **≥ 2 points** with CI lower bound **> 0**, **and** the C cost is **≤ 1 point** (point estimate) |
| **keep the reranker default** | default − BM25 found@10, neighbour arm, glossary none | keep, unless default **trails BM25 by ≥ 3 points** in the neighbour arm with the CI **excluding 0** |
| **related-paper growth is a concern** | neighbour − random (average) found@10, default, none (the H2 figure) | a concern if neighbours cost **≥ 3 points more** than random with the CI **excluding 0** |

**Other Layer-2 figures, printed with 95 % CIs:**
- **H3 (moved from Layer 1, D3):** the default retriever's change under neighbour expansion against BM25's,
  glossary none: [default(neighbour) − default(base)] − [BM25(neighbour) − BM25(base)], in points.
- **The rank-based outcome (D6):** mean RR@10, judged and gold-only, in every cell; and RR@10 versions of
  H1, H2 and H3.
- **H1 split by alias kind (D7):** symbol (88 G), abbreviation (508 G) and both (4 G, printed, never
  interpreted).
- H1 and H1b under the other retrievers and on the neighbour and random corpora; H2 under glossary merged
  and under the other retrievers; neighbour − base and random − base; random-1 − random-2 (the
  draw-to-draw gap); found@10-gold beside every found@10 figure; H1 split by whether the alias collides
  across papers (ABBREV.md *collision*); hits split into gold, same-paper judged and other-paper judged;
  the 191 records on the 108 unsatisfied papers, as a descriptive leg (§4.2).

**Exploratory.** Anything else, labelled *exploratory* in every sentence that quotes it.

"No difference" is never written. A one-sided member that is not significant is *not confirmed*, with its
power floor beside it (§8). A member whose minimum attainable p exceeds its level is
**GATE-NOT-EVALUABLE** (§9.1).

### 3.4 What is printed beside the Layer-1 members

Beside H1: the C net change with its CI, the ratio ΔC / ΔG and the C gate's verdict (rule C, §3.1), the
baseline C found@10 (base × default × none) with its headroom and the ceiling note, the reach count,
found@10-gold. Beside H1b: the baseline C found@10 and its distance from the ceiling. Beside H2: the
gold-only net change (the gate), the draw-to-draw gap, the per-arm judge false-yes rates (§6.3). If
|H2's net effect| is smaller than the draw-to-draw gap |found@10(random-1) − found@10(random-2)|, every
sentence that quotes H2 carries *within random-draw variation*, whatever H2's label.

## 4. Instrument: the record set, as built

### 4.1 What it is

The record set was built and closed before this text was drafted. It is frozen as is: no record is
added, redrafted or dropped for any reason after the freeze.

| item | value |
|---|---|
| accepted records | **1,091** (G 683, C 408) on **408** papers |
| satisfied papers (≥ 2 accepted G on distinct aliases and ≥ 1 accepted C) | **300**, each with **exactly 2 G and 1 C** (the stop rule ends a paper at its goal): **900 records**, 600 G / 300 C — the **balanced set** |
| records on unsatisfied papers | 191 (G 83, C 108) on 108 papers: 83 papers with 1 G + 1 C, 25 with 1 C only |
| record-set file | the private accepted-record file, sha256 **`09fc5ca434f50c5d0e527f2424dd31a2d59e019f59762c7f961dd7d7804ad24f`** of its bytes, equal to the `accepted_sha256` the acceptance summary records |
| balanced-set identity | sha256 **`2be86f8c5fd7dafaeb5a183c4dd55cc3e7b31366a48267424916dcbcf2557a0f`** of the 900 record ids, sorted, each followed by `\n` (method below) |
| alias kind of the 900 (G / C) | abbreviation 508 / 257, symbol 88 / 38, both 4 / 5 |
| gold | one source chunk per record (`gold_chunks` length 1 on all 1,091) |
| corpus | the OA-1000 chunk export, 32,765 chunks @512, 12,560 documents, 1,000 papers (sha256 `ff16e611…`) |
| merged glossary | sha256 `229519d7…`, built at `34fa58f` (#887); abbreviation entries `0f2c37e3…` |
| pool | seed **`261031|alias|main|O`**; pool sha256 `b49b2daf…`; 857 eligible papers (eval-paper list sha256 `4849699c…`); schema `glossbench-alias-pool-manifest/1`, built at `9350aac` (clean, `citable true`) |
| wave activation | seeded order `<pool seed>|waves` over the reachable papers; **532** papers activated over 20 waves; `c_first`; target **300** satisfied papers; stop `target_met` |
| drafter | prompt **`alias-draft/2`** on all 1,091; Gemini 2.5 Pro via the gateway, temperature 0, `max_tokens` 16384, ≤ 4 in flight; served id `Gemini 2.5 Pro` on all 2,904 draft rows |
| layer 1 | `glossbench_alias_checks` mechanical rules (quote ≤ 300 chars, leak threshold 0.5, question 5–40 words, answer support ≥ 0.5, primary ≥ 5 chars) |
| layer 2 verifier | prompt **`alias-verify/3`** on all 1,091; Claude CLI `claude-opus-5-5` off the gateway, with a Codex fallback after a CLI safeguard stop. Verdicts: Claude 1,037 pass / 504 reject; Codex 54 pass / 36 reject |
| verdict source of the 1,091 (of the 900) | `claude-opus-5-5` **1,037** (853); Codex (`gpt-6-astra`) **54** (47), of which 47 (40) followed a CLI safeguard fallback that the CLI answered with `claude-opus-5`, and 7 (7) followed a safeguard API error. The split by reason is a scratch count from the stored refusal reason; the committed recount is a pre-freeze item (§15) |
| run summary | written at `323d470` (clean, `citable true`); main config sha256 `bc1c56e6…`; gateway calls **2,912** for the main run (2,904 answered; 8 HTTP failures, 502 × 5 and 503 × 3), 3,040 including 128 from the pilots; 2,544,684 prompt / 6,027,230 completion tokens (main run); verifier CLI calls 1,638 (90 refusals, 80 safeguard fallbacks, 6 isolation anomalies), Codex 90; CLI cost $25.68 |

**How the balanced set and its sha were computed.** The committed functions
`glossbench_alias_waves.satisfied` (goal G 2, C 1) and `glossbench_alias_checks.alias_key` were applied
to the accepted-record file, grouping by paper. A paper is satisfied when its accepted G records cover 2
distinct alias keys and it has ≥ 1 accepted C. 300 papers qualify, and every one holds exactly 2 G (on 2
distinct keys) and 1 C, so no selection within a paper is needed. The count was made by a scratch script
calling those committed functions at `323d470`. A committed recount script is a pre-freeze item (§15).

**Code commits of the build.** The pool was built at `9350aac`. The waves ran in five segments, at
`9350aac`, `1026576`, `617fd8a`, `233cdfb` and `323d470`, each change sealed in the run log (§4.3). The
summary was written at `323d470`, which is the merge of #900. The pipeline modules are
`glossbench_alias_pool.py`, `glossbench_alias_waves.py`, `glossbench_alias_draft.py` and
`glossbench_alias_checks.py` (layer 1, the alias-verify/3 prompt; #889 for the current layer 1, #891
for the safeguard-fallback detection, #895 for the same-model re-ask fix). The abbreviation builder is
`alias_abbrev.py` (#887, `34fa58f`).

### 4.2 Which records carry Layer 1

**The 900 records on the 300 satisfied papers carry Layer 1** (H1 its 600 G; H1b and H2 all 900). The
layout is exactly balanced, 2 G + 1 C per paper, so every per-cluster statistic of §3.1 is defined for
every cluster. The 191 records on the 108 unsatisfied papers are run in every cell. They are reported as a
**descriptive leg** and never enter a test or a Layer-2 decision. Satisfaction is a property of the build,
fixed before any retrieval, so the split cannot depend on an outcome.

### 4.3 Disclosures about the build

1. **Built blind to retrieval.** The pool, the drafter, both verifier layers and the stop rule read no
   retrieval result, because none existed for these records. The yield model behind the wave sizes reads
   acceptance rates only.
2. **The target was extended twice mid-run.** The first segment closed with 445 accepted records; the
   owner extended the target from 100 to 200 satisfied papers and raised the caps (config sha `e802a99c…`
   → `c5e960d2…`). That run closed with 734 accepted on 200 satisfied papers, the set described in v0. After
   v0 and its review, the owner extended the target to **300** for H1's power (config → `bc1c56e6…`; the
   only change was `waves.target_papers` 200 → 300). Both decisions used acceptance counts and, for the
   second, v0's power sketch; no retrieval existed. Each reseal is in the run log.
3. **Two isolation hard stops** were cleared by the owner and recorded in the run log. One was a CLI
   safeguard fallback to `claude-opus-5`, not a leak (#891). The other was a same-model CLI re-ask, fixed
   in #895. **At the first clearance, the run log records that 27 verifier verdicts written after a CLI
   safeguard fallback were kept by owner decision and noted.** Which accepted records carry those 27 is
   not recoverable from the served-model field, which the verify log did not yet record in that segment.
   The 47 Codex-verdict records in the balanced set are flagged (§4.1). A sensitivity without them is
   printed beside H1, H1b and H2 (§7).
4. **Pilots.** No pilot record is in the set (`pilot: false` on all 1,091). Pilot papers were **not**
   excluded from the main pool: 18 of the 408 papers with accepted records (13 of the 300 satisfied) were
   also pilot papers (scratch count from the two pilots' draft logs). The pilots were drafted under earlier
   prompt versions. Their records were read to develop the pipeline and were never retrieved.
5. **Calibration overlap.** The judge's calibration set was built from the two pilots' records. **8**
   calibration record ids also occur in the main draft log, because a pilot and the main run drafted the
   same job. **4 of the 8 are among the 1,091** (1 G, 3 C), and **2 are in the balanced set** (both C)
   (scratch count over the calibration pair files). For those, BM25 candidates over the chunk export were
   computed for their pilot versions during calibration. This is the only retrieval-like computation that
   has touched any study record id. A sensitivity without the 2 is printed (§7).
6. **The extension to 300 reached papers the judge validation had used.** The validation set excluded
   every paper activated by the 200-paper run (355 activated; 377 papers excluded in all, §6.4). The
   extension activated further papers in the same seeded order, and the validation exclusion did not
   cover them. Scratch counts, made for this draft: **91** of the 300 satisfied papers are outside the
   validation exclusion list; **8** of the 300 had validation records drafted on them (44 validation
   drafts, 25 passed layer 1, 18 accepted) and carry **24** balanced-set records (16 G, 8 C); **6** of the
   300 are question papers of **121** of the 587 validation pairs; **39** validation pairs (35 distinct
   chunks) have a balanced-set gold chunk as their passage (21 of them with a question paper among the
   300); and, independently of the extension, **190** of the 587 pairs have their passage on one of the
   300 papers (**243** pairs touch a study paper as question or passage paper). So the judge's validation
   figure is **not fully out of sample** with respect to the study papers, and BM25 candidate lists were
   computed for validation questions on 6 study papers. A scratch re-score of the frozen validation rule
   (C + E, escalations to Opus) without those pairs still passes the plan's criterion: without the 121
   pairs, **0.992** on 388 Opus-unanimous pairs, 2.8 % escalated; without all 243, **0.990** on 287,
   2.8 % escalated (excluding coll_diff: 0.991 / 3.5 % and 0.988 / 3.3 %). No study record was retrieved or judged. A
   sensitivity without the 8 papers is printed beside H1, H1b and H2 (§7), and the committed recount
   (§15) reproduces these counts.
7. **Layer-3 record validity is open.** No owner spot check of the records has been recorded. §13.4 fixes
   one with round 3's rule (D9).

## 5. Design: factors and cells

Four factors. Corpus arm, retriever and glossary arm define the **24 cells** that every record is run in.
Question type (G / C) is a property of the record.

### 5.1 Corpus arm

| arm | content |
|---|---|
| **base** | OA-1000 as it stands (32,765 chunks): the existing OA-1000 bench copy, never the live collection |
| **neighbour** | base + the **citation neighbours** of the 300 satisfied papers (rule below) |
| **random-1**, **random-2** | base + **matched random papers**, two independent draws (rule below) |

Each expanded arm is a new bench collection. Base chunks are copied **with identical chunk ids and
content**. Each added paper goes through the **same ingest** (chunker `tok512o0`, the same embedder and
version, the same tools image) and the **same glossary pipeline** (§5.3).

**Neighbour selection (D4).** Metadata from OpenAlex only, made before any retrieval. For each study
paper:
- **References:** of the works it references that have **open-access full text** and are not in OA-1000,
  the **5 most cited** (OpenAlex citation count at query time).
- **Citers:** of the works that cite it, have open-access full text and are not in OA-1000, the **5 most
  recent** (OpenAlex publication date).
- Ties are broken by sha256 of `261101|alias|neighbours|tie|<refs or citers>|<study paper>|<work id>`,
  ascending (§5.5).
- **Shared neighbours (owner, 2026-11-01, §14 O5):** a work that is a neighbour of several study papers
  is **added once**. It is **attributed to the study paper that comes first** in the seeded order of the
  study papers, ascending sha256 of `261101|alias|neighbours|order|<study paper>`; the others do not draw
  a replacement. Each study paper's **attributed count** is the number of unique works it brought. A work
  that is in OA-1000, or is itself a study paper, is never added.
- **Expected size, an estimate:** about 9–10 added per study paper, so about **2,800** papers for 300, and,
  at OA-1000's 32.8 chunks per paper, on the order of 90,000 added chunks. The realised counts go in the
  manifest.
- **Feasibility (scratch, not citable):** an offline OpenAlex count for this draft resolved 298 of the 300
  study papers and found **2,625** unique neighbours, **60.8 %** with a PMCID and **39.2 %** with a PDF or
  other open-access copy only; a random same-topic pool is about **32 %** PMC, so the PDF-share matching
  below is required, and it is feasible: the smallest per-paper pool holds 160 open-access works, 72 of
  them non-PMC. The committed manifests (§15 item 4) replace these counts.

**Random selection (D5).** For each study paper and each draw *d* ∈ {1, 2}, as many random papers as were
**attributed to it** in the neighbour arm (its attributed count, above, after the control-character
screen below), so that each random draw holds the **same number of unique added papers** as the neighbour
arm, less any printed shortfall. They are drawn from works that:
- share the study paper's **OpenAlex primary topic**;
- have **open-access full text**;
- have a publication year within **± 3** of the study paper's;
- are not in OA-1000, not in the neighbour arm, and **neither cite nor are cited by any study paper**;
- are not already drawn for another study paper in the same draw.

The candidates are ordered by sha256 of `<draw seed>|<study paper>|<work id>`, ascending, with draw seed
`261101|alias|random|1` or `261101|alias|random|2` (§5.5), and the first eligible ones are taken. The
study papers are served in the neighbour arm's attribution order, so that a candidate eligible for two
study papers goes to the earlier one. The rule reads metadata only and is blind to content. The two draws use their own
seeds and may overlap. Their overlap is counted and printed. If a study paper has fewer eligible
candidates than it needs, it takes all of them, and the shortfall is printed per draw.

**Text source, for both arms (owner, 2026-11-01, §14 O6).** Every OA-1000 document's source path in the
chunk export is a PMC id, so a PMC source for the added papers matches the base's. For **every added paper in both arms**, full text is
taken from **PMC JATS XML** where a PMC copy exists, and only otherwise from the open-access PDF (or other
open-access copy) through the extractor that built OA-1000. The **random arm's PDF share is matched to the
neighbour arm's**: for each study paper the random draw takes, in its seeded order, as many non-JATS
papers as that study paper's attributed neighbours have, and the rest from JATS candidates; where one pool
is short the other fills in, and the per-arm PDF share and its difference from the neighbour arm's are
printed. The source of every added paper is recorded, and the JATS / PDF mix is printed per arm and draw.

**Control-character screen (owner, 2026-11-01, §14 O6).** Every added paper in every arm, after
extraction and chunking at `tok512o0` and before it counts towards its arm, is scanned for **C0 control
characters other than tab, newline and carriage return** (U+0000–U+0008, U+000B, U+000C,
U+000E–U+001F). A **PDF-sourced paper is excluded when any C0 character occurs in more than 0.5 % of its
chunks**; a JATS-sourced paper is scanned and counted but never excluded on this ground. *Why 0.5 %:*
OA-1000 holds **0** chunks with such a character among its 32,765 (scratch count over the chunk export for
this draft), so any such character is foreign to the base text; in practice a C0 character in extracted
PDF text is a lost glyph (a symbol or ligature the font maps to a control code), which corrupts exactly
the short forms this study is about. At about 33 chunks per paper the threshold excludes a PDF paper with
**one** affected chunk, and only a paper of at least 200 chunks is allowed a single stray character. If
the extractor emits form feeds (U+000C) as page breaks, every PDF would fail; the extraction for the expanded corpora (§15
item 5) counts this before the freeze, and if it happens the owner decides the remedy on that count, as a
recorded change to this text. **An excluded neighbour is not replaced** (the caps stay caps), and the
attributed counts are taken after the screen. **An excluded random candidate is skipped**, and the draw
continues in its seeded order. Exclusions are counted and printed **per arm and draw**, split by
references and citers in the neighbour arm.

**Manifests.** Before any retrieval, a manifest per expanded arm records, as counts in public: references
and citers found, open-access, already in OA-1000, deduplicated, added per study paper (distribution),
chunks added, text-source mix, control-character exclusions, random shortfalls, the draw-to-draw overlap,
the seed strings (§5.5), and the OpenAlex query date.
The ids live only in the private manifests, whose sha256s are public.

**If the realised neighbour count leaves H2 underpowered** at the planning scenarios (§8), the lever is
to raise the caps, before the freeze and on the counts alone.

### 5.2 Retriever

| id | definition |
|---|---|
| **BM25** | `retrieval_mode=bm25`, `rerank=false`, top 10 |
| **default** | the served path: hybrid (BM25 + SFR dense, `RETRIEVAL_CANDIDATE_MULTIPLIER` 2, RRF k 60), cut to `RERANK_CANDIDATES` 50, rescored by the cross-encoder (`BAAI/bge-reranker-v2-m3`) on `content + "\n" + symbol_glossary`, top 10 |
| **norerank** | the same hybrid with `rerank=false`, top 10 |

Every request carries `use_graph=false` and `rewrite_strategies ["passthrough"]`, as round 3's
`FIXED_REQUEST` does. The settings are checked against `/v1/config` where it exposes them and are
otherwise declared, as in the reranker test §3. **default** is the retriever of every Layer-1 member.
BM25 enters Layer 2 (H3 and the reranker decision). norerank is a Layer-2 secondary and feeds the
inactive-reranker gate (§9.1).

### 5.3 Glossary arm

| arm | rendered `symbol_glossary` field |
|---|---|
| **none** | empty on every chunk |
| **merged** | the paper's merged glossary (symbol + defined abbreviation), rendered on **every chunk where the entry's alias occurs** as a whole token (the **mention** rule; abbreviations state no value), under the served caps (12 entries, 1,200 characters) |

The arms are swapped by the backfill on each bench collection, with the round-3 stamp census, and no
re-indexing. For added papers in **every** expanded arm, the glossaries are generated **by the same
pipeline at the same versions** (owner, 2026-11-01):
- **Symbol entries:** the Qwen LLM step with the same prompt, model and settings as the OA-1000 round-3
  arm file, version string `llm/2:Qwen3.6-35B-A3B:dcb924271b1e:w80000x4:t4096:k0:r0+merge=llm-only`
  (prompt hash `dcb924271b1e`), unioned with round 1's `rules/3` entries, as the base file's symbol part
  was built.
- **Abbreviation entries:** the `alias_abbrev.py` builder at `34fa58f`, the commit that built the base
  file's abbreviation entries (§4.1).
- **Merge:** the same (paper, alias, primary words) key and `kind` rule as the base merged glossary.
- **Compute local only:** the Qwen step runs on a local endpoint; no glossary call goes through the
  gateway or any external service. The served model id is checked on every reply, as the judge's is.
- **Versions recorded** in the manifest: the LLM version string, prompt hash, requested and served model
  ids, the `rules/3` version, the `alias_abbrev.py` commit, the code commit and `experiment_provenance()`,
  and the sha256 of every glossary file (per arm), with a census of entries per added paper by `kind`.

The judge's definitions block (§6.2) reads the merged glossary in every cell, so the judge context does not
depend on the glossary arm.

### 5.4 Volume

24 cells, plus **one noise repeat per corpus arm** (default × none, run twice; §9.4): 28 legs × 1,091
records = **30,548** retrieval calls, at ≤ 4 in flight against the bench copies. No gateway call is made
for retrieval.

### 5.5 Seeds (owner, 2026-11-01, §14 O9)

One recorded seed string per draw, fixed here and copied into every manifest and artifact that uses it.
They follow the project's `<yymmdd>|<study>|<purpose>` pattern (the pool seed was `261031|alias|main|O`).
The **seed base** is `261101|alias`. Every hash below is sha256 of the seed string followed by `|` and
the named parts, ascending.

| draw | seed string | what it orders |
|---|---|---|
| **neighbour selection** | `261101\|alias\|neighbours` | tie-breaks within the reference and citer caps (`…\|tie\|<refs or citers>\|<study paper>\|<work id>`) and the attribution order of shared neighbours (`…\|order\|<study paper>`), §5.1 |
| **random draw 1** | `261101\|alias\|random\|1` | the candidate order of random-1 (`…\|<study paper>\|<work id>`), §5.1 |
| **random draw 2** | `261101\|alias\|random\|2` | the candidate order of random-2, §5.1 |
| **noise repeats** | `261101\|alias\|noise` | the request order of each repeat leg (`…\|<corpus arm>`), so a repeat is not a replay of the first leg's request sequence, §9.4 |
| **bootstrap** | `261101\|alias\|boot` | every paper-cluster percentile bootstrap: the C gate's CI (§3.1), the CIs beside Layer-1 members (§7) and every Layer-2 CI (§3.3) |
| permutation | `261101\|alias\|perm` | the sign-flip test, §7 |
| judge order | `261101\|alias\|judge\|order` | the labelling order of deduplicated pairs, §6.2 |
| drift sample | `261101\|alias\|drift` | the per-batch drift sample (`…\|<batch>`), §6.3 |
| record spot check | `261101\|alias\|spot\|records` | the 40 records of §13.4 |
| judged-pair spot check | `261101\|alias\|spot\|judge` | the 60 pairs of §13.5 |

## 6. Outcome and judge

### 6.1 The outcome

**found@10.** A record has a hit in a cell when the gold chunk is in the top 10, **or** any other top-10
chunk has the final judge label *answers* (§6.3). **found@10-gold** is printed beside it everywhere, and
is H2's direction gate. **RR@10** (Layer 2) uses the same labels.

### 6.2 What is judged, and blindness

- **Only top-10 non-gold chunks** are judged. The gold chunk is a hit by definition.
- **Pairs are deduplicated across cells.** The unit is (record, chunk). One final label serves every cell
  in which that chunk appears in that record's top 10.
- **Blind to arm, retriever and rank.** The pair prompt (`alias-judge/2`) carries the question, its term,
  the passage, the passage paper's title, and the passage paper's own definitions (its merged-glossary
  entries whose alias occurs in the passage, at most 25, sorted). It carries **no** corpus arm, retriever,
  glossary arm, rank, score or count of cells. Because pairs are deduplicated before labelling, no
  label can depend on which cell produced the pair. Pairs are labelled in a seeded shuffled order
  (`261101|alias|judge|order`).
- **A pair's arm**, for the drift and spot-check strata only, is the arm its passage's paper came from:
  base (an OA-1000 paper), neighbour (an added neighbour) or random (a paper added by either draw).

### 6.3 The judge (owner decision, D8)

| role | model | prompt | settings | label |
|---|---|---|---|---|
| **primary C** | `Qwen3.6-35B-A3B` | `alias-judge/2` | thinking on, n = 5, T 0.7, `max_tokens` 8192, served-model check | majority of the valid samples |
| **primary E** | `Qwen3.8-27B` | `alias-judge/2` | thinking on, n = 1, T 0.7, `max_tokens` 8192, served-model check | the one sample |
| **tiebreaker** | `Gemini 4 Argon` via the gateway | `alias-judge/2` | `max_tokens` 4096, default temperature, `fallback_model ""`, ≤ 4 in flight, budget stop | its label |
| **drift reference** | `Claude Opus 5.5` via the gateway | `alias-judge/1` (the calibration reference's prompt) | 1 repetition, `max_tokens` 2048 | not used for any hit |

**Rule.** When C and E both give a label and agree, that label is final. Otherwise the pair goes to the
tiebreaker, and its label is final. "Otherwise" covers three cases: C and E disagree, C has no majority,
or E's sample is invalid. A tiebreaker reply that is empty or unparseable is re-issued once. If the pair
still has no label, it is **unjudged**. An unjudged pair counts as *not answering* in the primary analysis.
The count of unjudged pairs is printed, and a sensitivity counts them as *answering*. If unjudged pairs
exceed **1 %** of judged pairs, every found@10 figure carries *judge incomplete* in its sentence.
**Served-model check:** a reply whose served model id is not the requested one stops the run and is not
stored.

**Drift reference, stratified by arm.** A seeded **5 %** sample of the deduplicated pairs, drawn per
labelling batch (`261101|alias|drift|<batch>`) and allocated **equally across the three arms** (base,
neighbour, random), each stratum capped at its size, is re-labelled by Opus. Agreement is computed on the
pairs where Opus answers. **Alarm, checked after every judging batch:** cumulative agreement between the
final label and Opus below **0.97 overall**, or below **0.95 in any arm**, **pauses labelling**. Each
check needs at least 100 Opus-answered pairs in its scope before it can fire (carried from v0; §14 O4).
The pause is reported to the owner, the owner decides the remedy, and the remedy is recorded as a
deviation. Opus labels never replace a final label. The Opus-filtered share of the sample is printed per
arm. **Topic filter:** Opus via the gateway returns empty answers on some questions, clustered on vaccine
and virus topics: 33 of 410 calibration pairs and 81 of 587 validation pairs had no answer on any attempt
(83 validation pairs have no reference; the other 2 had no parseable verdict). The Claude CLI refuses
most of the same pairs, while Argon and both Qwen judges answer them. A filtered pair is **not** a drift
failure. It is excluded from the drift agreement and counted.

**Per-arm false-yes rates (D2).** For each arm: the share of Opus-*not* drift pairs that the final label
calls *answers*, and the share of owner-*not* spot-check pairs (§13.5) that the final label calls
*answers*, each with its n and a Wilson 95 % interval. A higher false-yes rate in the neighbour arm would
inflate its judged found@10 and work against H2; this is why the gold-only gate exists.

### 6.4 Why this judge: calibration facts (counts only)

These are the facts behind the owner's choice. Each is a calibration result with its commit. None is a
study result.

| fact | value | where |
|---|---|---|
| in-sample C + E agreement rule (410 pairs, 372 Opus-unanimous) | accuracy 0.992, 2.7 % escalated | JUDGE-CALIBRATION.md rounds 2–3 (#896 `65a58e1`, #897 `9dabeb4`) |
| **out-of-sample validation**, plan frozen first (`e2713e1`), run at `390777d` | **0.992** [0.979, 0.997] on 487 Opus-unanimous pairs; 2.5 % escalated; PASS against ≥ 0.98 and ≤ 5 %; 377 papers excluded, every paper of the 734-record build among them, but not every paper added by the extension to 300 (§4.3 item 6) | JUDGE-VALIDATION-PLAN.md; JUDGE-CALIBRATION.md *Validation* (#898) |
| **tiebreaker selection**, plan frozen first (`10a986c`) | `Gemini 4 Argon` **0.996** agreement with Opus on the 487 (κ 0.988; 0 of 83 filtered pairs unlabelled); `GPT-5.6 Sol` 0.990; Argon selected on agreement | TIEBREAKER-PLAN.md; JUDGE-CALIBRATION.md *Tiebreaker* (#899, `5e6a722`) |
| residual errors of the gate | 4 on 475 accepted pairs, all false yes: 3 on same-paper non-gold chunks (lex_same) and 1 in coll_same; 3 of the 4 on C records | JUDGE-CALIBRATION.md *Validation* |
| the composed rule (C + E, then Argon), replayed on the validation labels | 482 of 487 (0.990); 12 sent to Argon, 11 of them right; 0 unlabelled | **scratch replay for v0, post-selection, not citable**; the committed replay is a pre-freeze item (§15) |

**What the calibration does not show.** Validation scored the C + E rule with **Opus** labelling the
escalated pairs. The composed rule with Argon was chosen after both results were seen, and its replay is
post-selection. The **coll_diff** stratum is decidable from the definitions block alone and is not
evidence of judgement. Every pair in both sets came from **base OA-1000**. Pairs from neighbour or random
papers are new ground: they have their own definitions blocks and possibly denser collisions. The
stratified drift reference and the owner's judged-pair spot check (§13.5) are what cover them. The
calibration and validation pairs are BM25-constructed candidates. They are not the retriever outputs
judged here.

### 6.5 Volume (planning only)

There are at most 10 non-gold chunks per record and leg, so 30,548 lists give an upper bound of
**305,480** pair instances before deduplication. The deduplicated count is known only after retrieval;
it is far smaller, because most top-10 lists repeat across retrievers, glossary arms and the repeats. At
the validation throughput, judge C took about 1.85 s per pair and judge E about 0.74 s per pair (4 in
flight). The tiebreaker load is expected at about 2–2.5 % of pairs and Opus at 5 %. **Gateway caps for
the judge** are set before the freeze from the realised pair count (§14 O7). The budget stop and `PAUSE`
apply, as in the calibration harness.

## 7. Analysis

Round 3 §7 machinery (`chunktrial_stats`), with these specifications:

- **Test (Layer 1 only):** paper-clustered sign-flip permutation on the per-cluster statistic of §3.1,
  10,000 flips, seed `261101|alias|perm`, exact enumeration at ≤ 20 non-zero clusters, p = (1 + #{≥ obs}) /
  (1 + n). All three members are one-sided.
- **Multiplicity:** the graphical Holm-with-gatekeeping procedure of §3.1. Gated or absent members at p =
  1.0. `holm_drop_gated` is a labelled sensitivity that never decides.
- **Beside every Layer-1 member:** the 95 % paper-bootstrap percentile CI of the net statistic (10,000
  resamples); the gained / lost record counts; the number of non-zero clusters; the minimum attainable p;
  the level the member was tested at; the realised power (§8); `mde_split`; found@10-gold; the items of
  §3.4.
- **Layer 2:** point estimates in points with 95 % paper-bootstrap percentile CIs, and the decision rules
  of §3.3 applied mechanically by the committed script.
- **Declared sensitivities**, printed beside H1, H1b and H2 and never deciding: without the 47
  Codex-verdict records; without the 2 calibration-overlap records; without the 13 satisfied papers that
  were pilot papers; without the 8 satisfied papers that carry validation records (§4.3 item 6); unjudged pairs as *answering*; tiebreaker-labelled pairs set to each primary judge's
  own label in turn; H2 against random-1 alone and random-2 alone.
- **Missing data:** a record whose retrieval call failed in any cell of a contrast is excluded from it
  and counted. `family_missing_as_not_found` is the one pre-named sensitivity. More than 2 % failed calls
  in a leg is a hard stop.
- Every analysis is the committed script. Scratch analyses are not citable.

## 8. Power and MDE

**Layout:** 300 clusters × (2 G + 1 C), realised and fixed. H1 reads 600 G records; H2 reads 900 records.
No layout uncertainty remains for the records. What is unknown is **discordance**: the share q of records
whose hit changes between the two cells of a contrast, and the share p of the discordant records that move
in the hypothesised direction.

**Normal approximation** (this draft; the committed calculation is a pre-freeze item, §15). Each record
gets d ∈ {−1, 0, +1}: non-zero with probability q, in the hypothesised direction with probability p. Then

  μ = q (2p − 1),  σ² = q − μ²,  power = Φ( √n · μ / σ − z ),

with z = 1.95996 for the Holm worst case, one-sided α/2 = 0.025 (the conditional rows use z = 1.64485,
one-sided α = 0.05). This treats records as independent and
paper-level shares as constant. A paper-level spread of p lowers power somewhat; the committed simulation
adds it (below).

| member (n) | q .02 | q .03 | q .05 | q .10 | q .20 |
|---|---|---|---|---|---|
| **H1**, p .85 (600) | .68 | .85 | **.97** | 1.00 | 1.00 |
| **H1**, p .70 (600) | .28 | .40 | .59 | **.88** | .99 |
| **H2**, p .85 (900) | .85 | .96 | 1.00 | 1.00 | 1.00 |
| **H2**, p .70 (900) | .40 | .55 | .77 | .97 | 1.00 |

**Conditional power at α = 0.05** (§3.1: H2 is tested at 0.05 once H1 is confirmed, and H1 at 0.05 once H2
is confirmed). These are powers *given* that the other member was confirmed; the unconditional floor is
the 0.025 table above.

| member (n), at α = 0.05 | q .02 | q .03 | q .05 | q .10 | q .20 |
|---|---|---|---|---|---|
| **H1**, p .85 (600), given H2 confirmed | .79 | .91 | .99 | 1.00 | 1.00 |
| **H1**, p .70 (600), given H2 confirmed | .40 | .52 | .71 | .93 | 1.00 |
| **H2**, p .85 (900), given H1 confirmed | .91 | .98 | 1.00 | 1.00 | 1.00 |
| **H2**, p .70 (900), given H1 confirmed | .52 | .67 | .85 | .99 | 1.00 |

**The two planning scenarios for H1** are q 5 % / p .85 and q 10 % / p .70: power **≈ 0.97** and **≈ 0.88**
at 0.025 (≈ 0.99 and ≈ 0.93 given H2 confirmed). For H2 at the same two scenarios: 1.00 and .97 at 0.025;
given H1 confirmed, 1.00 and .99 (and at q 5 % / p .70, .77 → .85).
The 80 %-power MDE, from μ = k · √(q / (n + k²)) with k = 1.960 + 0.842, is 2.5 points for H1 at q 5 % and
2.1 points for H2 at q 5 % (3.6 and 2.9 at q 10 %).

**H2 is approximated conservatively.** H2 compares the neighbour arm with the **average** of two random
draws, so d takes values in {−1, −½, 0, ½, 1}. Averaging two draws removes part of the random-arm noise,
which lowers σ for the same mean, so the three-valued table understates H2's power at a given net effect.
The gold-only gate is a second condition, so the power to *confirm* H2 is at most the table's figure. Its
size depends on how often the judged and gold-only directions agree, which the committed simulation takes
as a stated assumption.

**Read this beside §2.** Round 3 saw **0 discordant records** between the served glossary and none under
the served path, and a 1 / 9 split in the adverse direction under mention attachment. If G discordance
here is as low, or the split as adverse, H1 ends *not confirmed* with this floor printed beside it. It is
not a null unless the power at the stated scenario is ≥ .80.

**The committed calculation (pre-freeze, §15)** runs on the realised layout and, for H2, on the realised
neighbour and random counts per study paper. It repeats the table above by the normal approximation and
by simulation of the cluster sign-flip test (2,000 simulations × 2,000 flips, a between-paper SD of p of
.15, a fixed seed), and it computes H2 with the two-draw average and the gold-only gate under stated
agreement assumptions. **If H2's simulated power is below .80 at q 5 % / p .85, the caps of §5.1 are raised
before the freeze**; that is the lever, and it acts on metadata counts only.

**Evaluability floor.** With k non-zero clusters the minimum attainable one-sided p is 2^−k. At the Holm
worst case α/2 a member needs k ≥ 6; at α = 0.05 (H1b, or H1 / H2 after the other is confirmed) k ≥ 5.

## 9. Gates, stops, and restore points

### 9.1 GATE-NOT-EVALUABLE (a third answer, preserved in every table)

A Layer-1 member is GATE-NOT-EVALUABLE when:
- its minimum attainable p exceeds its level (too few non-zero clusters);
- its cells have **fewer than 8 clusters** with records (`MIN_CLUSTERS`);
- any of its cells failed the depth read-back without an `exhausted_verified` verdict (§9.2);
- the **inactive-reranker gate** fired: default's top 10 equals norerank's on every record in that corpus ×
  glossary condition;
- a census of any of its cells failed (§9.2) and was not repaired and re-run;
- (H1, and so H1b) **the C gate** fired (§3.1);
- (H1, and so H1b) **the reach gate** failed: fewer than **80 %** of the 600 G records (**480**) have a gold
  chunk that renders the record's own entry under the caps, on base. The count is read offline before the
  freeze (§15) and quoted beside H1.

A GATE-NOT-EVALUABLE member enters the multiplicity procedure at p = 1.0. **Absent** is written *absent*,
never left blank. Layer 2 has no gates (§3.3); a gate verdict that would apply is printed beside the figure.

### 9.2 Checks per leg (a leg is one cell run over all records)

- **Census before and after:** the chunk count of the collection equals the corpus arm's manifest (base
  32,765; the expanded arms as built). The `metadata.symbol_glossary_version` stamp census equals the
  glossary arm. Every gold chunk id is present, with the same content hash, in all four corpus arms.
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
  difference; a served judge model id that is not the requested one; a judge budget stop; a drift alarm
  (§6.3); a frozen-prereg sha mismatch in any stage that reads `prereg.file`.

### 9.3 Restore points

Every store write is made on a **bench copy**, never on a live collection. Store writes are building the
three expanded collections and every glossary swap. Each write goes inside a management window, under a
reviewed live-ops plan. Before each write a **restore point** is recorded: the snapshot id, the chunk
count and the stamp census. After the write the census must match the plan. At the end of the run every
collection is restored to its recorded state, and the restore is verified by census. A failed restore
blocks every later leg until it is repaired.

### 9.4 Noise floor (a determinism check, not power)

One cell per corpus arm (default × none) is run **twice**. The run-to-run discordance is printed beside
every figure from that arm. Round 1's floor was 0–2 records, and round 3's repeat was 0 discordant. No
result is re-run to move the floor.

## 10. Code, pin, provenance

**Exists on `main` (at or before `323d470`):** the record pipeline (§4.1); the judge harness
`glossbench_alias_judge.py` with the Qwen, gateway and Opus backends, the served-model check,
`analyse-gate` and `analyse-tiebreaker` (#894–#899); `bm25_exhaustion.py` (#877); the round-3 runner
and analysis (`found10.py`, `found10_analysis.py`, `chunktrial_stats`), the backfill and the stamp
census.

**Must be written, reviewed and merged before the freeze (§15):** a recount script for the balanced set
and the verdict-source split (§4.1); the OpenAlex neighbour and random selectors (metadata only, seeded,
writing the manifests of §5.1); an alias adapter for the runner (the records schema; the 24 cells and 4
repeats; the four corpus collections); the pair builder and the deduplication for judged pairs; the
composed-rule labeller (C + E, then Argon), with the arm-stratified drift sampler and both alarms; the
statistics for H1, H1b (with the graphical procedure) and H2 (two-draw average, gold-only gate), with
tests; the Layer-2 script (CIs, the three decision rules, RR@10, H3); a glossary-file writer for the merged
glossary that the backfill renders, with render parity tests, and the offline reach count; the committed
power calculation (§8); the public-artifact check (§11).

**Pin.** Every run is from one `main` commit, at or after the merge of the last item above, in a clean
worktree (`dirty false`, `untracked 0`), or from a tools image built from a tag at or after it (ADR-0010).
Every output embeds `experiment_provenance()`. A dirty run is not citable.

## 11. Data provenance, privacy, and what is released

- **Private, never in this repository:** the records (questions, answers, quotes), the chunk exports,
  the glossaries, the paper lists, the neighbour and random manifests with ids, the judged pairs and their
  labels, the spot-check sheets and keys, and the per-record hits.
- **Released (public, counts only):** this prereg; `RESULTS.md`; `DEVIATIONS.md`; `artifacts/*.json`
  holding counts, rates, CIs, p values, model ids, version strings, commit shas and sha256 hashes of the
  private files; per-paper quantities by paper rank only. A committed check refuses any public artifact
  that carries record text or ids, in the style of round 3's `string_values` and `--omit-text`.
- **External calls.** Neighbour and random selection queries OpenAlex for public bibliographic metadata;
  no record content is sent. Judge C and E run on local endpoints. Argon and Opus run through the gateway
  under its standing terms (`fallback_model ""`, `max_tokens` always, ≤ 4 in flight, size notice per
  batch, budget stop). Pair prompts carry chunk text to the gateway, within those terms. Retrieval makes
  no model call.

## 12. Deviations policy

After freeze the prereg is never edited. Any change to a hypothesis, cell, record, judge, threshold, seed,
gate or analysis is a dated entry in `DEVIATIONS.md` stating what changed, why, when, what had been
seen at that time, and which results it touches. A result whose computation depends on a deviation says
so in its sentence. Re-running a leg after a hard stop, and any remedy after a drift alarm, is a deviation
entry, not a silent retry.

## 13. Limitations and threats to validity

1. **One corpus.** OA-1000 only. The expanded arms add papers around it but do not make a second corpus.
2. **The population is glossary papers.** Records exist only for papers with ≥ 2 usable G entries and ≥ 1
   C. The 300 satisfied papers are those the stop rule reached first in a seeded order, and the estimand
   is conditional on them. Abbreviations dominate: 508 of 600 G records.
3. **The judge is not truth.** found@10 inherits the judge's errors. On the validation set those errors
   were few (§6.4) but were all false yeses, 3 of 4 on same-paper non-gold chunks. They inflate found@10
   equally across arms only if such chunks are retrieved equally often, which is why false-yes rates are
   printed per arm and H2 carries the gold-only gate.
4. **Record validity (owner spot check, D9).** The owner labels **40** records drawn from the 900 balanced
   records (seed `261101|alias|spot|records`), valid / invalid, on a sheet that carries no kind (G / C), arm
   or record id. **Rule, as round 3's:** validity below **0.85** at the point estimate makes every result
   sentence carry *validity below threshold*. The Wilson 95 % interval is printed and decides nothing. The
   set is not rebuilt. The labels are committed (as counts, with the sheet's sha) **before any analysis**.
   The verifier's spot-reads found about 1 in 12 current-pipeline G records doubtful (VERIFIER.md).
5. **Judge validity (owner spot check, D9).** The owner labels **60** judged pairs, **10 per arm × glossary
   cell** (base, neighbour, random × none, merged), drawn uniformly from the non-gold pairs judged in the
   default retriever's top 10 for that cell (seed `261101|alias|spot|judge`; a pair drawn for one cell is
   not drawn again). The sheet is **blind to arm and to the judge's verdict**. **Rule:** owner–judge
   agreement below **0.90** at the point estimate is stated in every result sentence, and found@10-gold is
   reported alongside each found@10 result as the sensitivity. Nothing is re-labelled. The labels are
   committed **before any analysis**.
6. **Neighbour definition.** References and citers come from OpenAlex, whose coverage is incomplete, and
   open-access availability filters them. The caps take the most cited references and the most recent
   citers, so the added set is a sample of the neighbourhood, not the neighbourhood itself.
7. **Forking paths.** The corpus and retriever factors were chosen after round 3's null and the reranker
   test's papers-OC reading. H1's form and the 300-paper target were chosen after v0's power sketch. The
   record set's target was extended twice mid-build (§4.3). The judge's tiebreaker was chosen after its
   calibration results. Each is bounded by being fixed here before any retrieval on a study record.

## 14. Open decisions for the owner

Only items not settled by D1–D10.

| # | decision | proposal in this draft |
|---|---|---|
| O1 | **Numeric definition of the C gate** | **Decided (owner, 2026-11-01): rule C.** Evaluated only when ΔG > 0 (H1 is one-sided; at ΔG ≤ 0 the gate is moot). H1 is GATE-NOT-EVALUABLE if ΔC (merged − none, base × default, 300 C) has the same sign as ΔG, \|ΔC\| ≥ ½ \|ΔG\| in points, and ΔC's 95 % paper-cluster bootstrap CI (seeded) excludes 0. A C drop never trips the gate (it feeds Layer 2's ≤ 1-point C-cost rule). Baseline C found@10 printed beside it with a ceiling note. Written in §3.1, §3.2, §3.4 |
| O2 | Reading of "H1b at full α" | **Decided (owner, 2026-11-01): Option B.** H1 and H2 start at one-sided 0.025 each (Holm over 2); a confirmed member passes its 0.025 to the other. H1b is tested only if **both** H1 and H2 are confirmed, at α = 0.05 (fixed sequence after the Holm pair); otherwise it is not tested and is reported descriptively. H2's gold-only gate failing = H2 not confirmed. Written in §3.1, §3.2 and §8 |
| O3 | H2 when the judged test is significant but the gold-only gate fails | label *not confirmed (gold-only direction gate)*; counted as not rejected, so it passes no level on |
| O4 | Minimum Opus-answered pairs before a drift alarm can fire | 100 per scope (overall and per arm), carried from v0 |
| O5 | Attribution of a neighbour shared by several study papers | **Decided (owner, 2026-11-01).** Added once; attributed to the first study paper in the seeded order (`261101\|alias\|neighbours`, `…\|order\|<study paper>`); no replacement for the others; each random draw takes per study paper as many papers as were attributed to it, so the arms hold equal unique counts. Written in §5.1 |
| O6 | How "prefer PMC/JATS" is applied | **Decided (owner, 2026-11-01).** PMC JATS XML for every added paper in both arms where it exists; the random arm's PDF share matched to the neighbour arm's; every added paper scanned for C0 control characters (other than tab, newline, carriage return), a PDF paper excluded when any occurs in > 0.5 % of its chunks; exclusions counted per arm. Written in §5.1 |
| O7 | Gateway caps for the judge | set from the realised deduplicated pair count before the freeze: Argon ≤ 4 % and Opus ≤ 6 % of pairs, with an absolute ceiling agreed with the papers-OC owner |
| O8 | **Whether to add GPT-6.1 Sol later** as a second tiebreaker or drift reference | not now: it is **not on the gateway allow-list**. Adding it later would be a deviation with its own frozen selection plan, as TIEBREAKER-PLAN.md was |
| O9 | Seeds | **Decided (owner, 2026-11-01).** One recorded seed string per draw, seed base `261101\|alias`: neighbours `…\|neighbours`, random draws `…\|random\|1` and `…\|random\|2`, noise repeats `…\|noise`, bootstrap `…\|boot`, and the derived strings of §5.5. Written in §5.5 |
| O10 | **The validation overlap of §4.3 item 6** (8 satisfied papers with validation records; the validation figure not fully out of sample) | keep the 900 as frozen, print the sensitivity without the 8 papers, and state in §6.4's reading that the validation figure is not fully out of sample. Alternatives: drop the 8 papers from Layer 1 before the freeze (292 papers, 876 records; H1 power at the two scenarios .97 → .97 and .88 → .87), or re-score the validation without the 6 question papers' 121 pairs (scratch re-score: 0.992, 2.8 % escalated, still PASS; §4.3 item 6) |

## 15. What must exist before freeze

| # | item | state |
|---|---|---|
| 1 | Owner decisions folded into this text | D1–D10 folded in v1; O1, O2, O5, O6 and O9 decided 2026-11-01; O3, O4, O7, O8 and O10 open |
| 2 | **Record-set sha** and the balanced set recounted by committed code: sha `09fc5ca4…` (equal to the summary's `accepted_sha256`), balanced-set id sha `2be86f8c…`, 300 / 900 / 191, the verdict-source split, and the pilot and calibration overlaps | recomputed for this draft by a scratch script over committed functions (§4.1); committed recount open |
| 3 | The validation set's paper exclusion checked against the papers added by the extension to 300 | checked for this draft by scratch count: it does **not** cover them (§4.3 item 6); the counts go into the committed recount of item 2, and the owner decides whether the overlap changes anything beyond the declared sensitivity (§14 O10) |
| 4 | **Neighbour and random selection manifests** from OpenAlex (metadata only, seeded), with the counts of §5.1 public; the **H2 power** recomputed on them, and the caps raised if it is below .80 (§8) | open |
| 5 | **The three expanded corpora built and censused on bench copies, within a management window** under a reviewed live-ops plan: the ingest at the same chunker, embedder and tools image, base chunk ids and content verified identical, the restore points recorded | open |
| 6 | **Glossaries for every added paper** in every arm, generated by the same pipeline at the same versions (symbol model and prompt, `rules/3`, `alias_abbrev.py`), merged, and their shas recorded; a census of entries per added paper | open |
| 7 | The merged-glossary file the backfill renders, with render parity tests; the **reach count** of §9.1 read offline against the 480-of-600 gate | open |
| 8 | **Offline power calculation** by committed code on the realised layout (normal approximation and simulation, §8), with a clean tree and `citable true` | open |
| 9 | The committed replay of the composed judge rule on the validation labels (§6.4), replacing the scratch replay | open |
| 10 | The code of §10 *Must be written*, merged after an independent review | open |
| 11 | **Owner spot check of the 40 records** (§13.4): may be done before the freeze; must be committed before any analysis | open |
| 12 | A dry run of the whole chain on base × BM25 × none for **non-study records only** (for example the validation set's), to check the plumbing without reading any study outcome | open |
| 13 | O1 (the C gate's numeric definition) and O9 (seed base) fixed in the text | done in this draft: rule C (§3.1) and the seed strings (§5.5) |
