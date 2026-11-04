# Pre-registration, alias study (OA-1000) — DRAFT v3, 2026-11-03

**Status: DRAFT — not frozen; owner review pending.** Version 3 folds in the owner's decisions of
2026-11-03 (R12–R28, the second table below, and the pre-freeze corrections P1–P6 after it): a C cost rule that reads the point estimate (−1 point) and
the CI's lower bound (−3.6 points, the design's detection limit for C), the judge false-yes bound on H2
always taking the larger of the random arm's and the pooled limit, *fragile* restricted to named re-tests
of H1 and H2, a judged-pair spot check of 100 pairs, the corpus build facts of the v3 pass (six length
bands, the carry-over re-draw, the fetch budget, the relaxation order), a nearest-band fill at the last
relaxation level, a repetition screen for every added paper, and the chunk-volume target read as a build
target covered by H2's chunk-volume covariate; and stage A extended by the inactive-reranker gate's
norerank cells (retrieval and judging) and the Layer-1 attachments' BM25 cells (retrieval only), the
placebo stripped of each paper's own entries, a bootstrap-t and a Freedman–Lane covariate test as the re-tests behind *fragile*, and *not resolved* for an equivalence reading with zero
discordant records; and one bench collection per corpus × glossary arm, written once from a single
embedding per corpus, in place of in-place glossary swaps; and glossary files that carry abbreviation plurals, under fixed rendering
conventions; and a pilot blind to outcomes. Version 2 folded in the changes the owner approved
on 2026-11-02 and 2026-11-03 from an independent methodology review, 2026-11-02 (R1–R11, the second table
below): two separate families in place of the Holm pair, a harm rule and CI-based cost rules in Layer 2,
the claims' scope wording, judge reporting, a stratified judged-pair spot check, fixed sensitivities, a
placebo glossary leg, a pre-freeze pilot, a two-stage run with judging on demand, a signed freeze tag and
a stated H2 estimand. Version 1 folded in the owner's ten decisions of
2026-11-01 (table below), and their later decisions of the same day on O1 (the C gate, rule C), O2
(H1b routing), O5 (shared neighbours), O6 (text format and the control-character screen) and O9 (seeds),
with the glossary pipeline for added papers (§5.3). It also folds in the owner's resolutions of
2026-11-01 from the #904 code review (seed derivation, the power seed, the drift sample, the
minimum-attainable-p level, H1b with a missing record, the Opus cap, the admin key, the caps lever and
the exact sign-flip p: §5.5, §6.3, §7, §8, §9.2) and the corpus-build decisions of the same day
(neighbour walk-down, article types, random-draw matching, the recorded build facts: §5.1, §7), and
the owner's decisions of 2026-11-02: the test of each Layer-1 member (§7) and the caps-lever agreement
`a = 0.7` (§8, §14 O11). The decisions of §14 are all settled as of 2026-11-04 (§15 item 1). Nothing in this text binds a run until it is
frozen by the procedure in this directory's `README.md`. At freeze the text is frozen **as written**, and
its sha256 goes into the README, a private `PREREG.sha256` and the message of a signed annotated tag
pushed to the public repository (README, *Freeze procedure* step 4; R10), never into this file. After freeze this
file is never edited. Any later change is a `DEVIATIONS.md` entry.

> **Public copy.** The file names no tenant, host, endpoint, collection, person or gateway owner. The corpus
> is named by its alias, **OA-1000**. It holds counts, rates, model ids, version strings, commit shas and
> sha256 hashes only. It holds no question, answer, quote, passage, paper id, record id or file path. The
> partner whose gateway carries the model calls is "the papers-OC owner" where it has to be mentioned.

**Owner decisions of 2026-11-01, and where each is written.**

| # | decision, in short | where |
|---|---|---|
| D1 | H1 = G records only, base, default, merged vs none, one-sided; C as a **gate**; H1b = G-vs-C DiD, no extra Holm member (routing amended by §14 O2, Option B, and then by **R1**: H1b is tested at α = 0.05 in fixed sequence after H1 alone, within Family G); the H1 × H1b table | §3.1, §3.2, §3.4, §9.1 |
| D2 | H2 = neighbour vs random (average of two draws), default, none, one-sided, judged found@10; confirmed only if gold-only found@10 also drops (a gate, not a third test); drift stratified by arm; per-arm false-yes rates | §3.1, §3.2, §6.3 |
| D3 | two layers: Layer 1 confirmatory (H1, H2 under Holm over 2; H1b after both H1 and H2, §14 O2; **amended by R1**: two separate families, G and N, each at α = 0.05); Layer 2 serving decisions on CI-based thresholds, no gates (amended by R2); H3 moves to Layer 2 with a 95 % CI | §3 |
| D4 | neighbour caps: ≤ 5 references (most cited) + ≤ 5 citers (most recent), open-access full text, via OpenAlex, deduplicated against OA-1000 | §5.1 |
| D5 | random arm "same field": matched count per study paper, same OpenAlex primary topic, OA full text, no citation link to any study paper, year ± 3, seeded content-blind rule | §5.1 |
| D6 | two random draws; one noise repeat per arm; a rank-based secondary (Layer 2); H2 power from the realised layout; raising the caps is the lever | §5.1, §5.4, §3.3, §8 |
| D7 | merged glossary attached by mention, same pipeline for added papers; reach gate ≥ 80 %; Layer-2 split by alias type | §5.3, §9.1, §3.3 |
| D8 | judge as in v0; Opus drift sample stratified equally across arms; alarm < 0.97 overall or < 0.95 in any arm, after every batch; served-model check | §6.3 |
| D9 | owner spot checks: 40 records (0.85 rule, blind to G/C) and 60 judged pairs (10 per arm × glossary cell, < 0.90 rule; the stratification **amended by R5**: by judge label, spread across arms), labelled and committed before analysis; the size **amended by R15**: 100 pairs | §13.4–13.5 |
| D10 | a prior-results section, with the disclosure that prior evidence points against H1 | §2 |

**Owner decisions of 2026-11-02 and 2026-11-03, and where each is written.** R1–R11 were adopted from an
independent methodology review, 2026-11-02; the review's text is not part of this record, what the owner
adopted from it is. R12–R28 are the owner's decisions of 2026-11-03 on the C cost rule, the false-yes
bound, the *fragile* rule, the spot check, the corpus build, stage A, the placebo, the re-tests behind
*fragile*, the equivalence reading, the stage of the BM25 legs, the bench collections, the glossary
files' plurals and rendering, and the pilot's blindness.

| # | decision, in short | where |
|---|---|---|
| R1 | **two families.** Family G (glossary): H1 at one-sided α = 0.05, then H1b in fixed sequence at 0.05 (exact label randomization). Family N (growth): H2 at one-sided α = 0.05 (exact sign-flip, gold-only direction gate). No level passes between H1 and H2. Powers restated | §3.1, §3.2, §7, §8 |
| R2 | **Layer-2 rules.** A harm rule (G lift CI upper bound < 0: *harmful, do not attach*); the C cost judged by its CI (non-inferiority at −1 point; **replaced by R12**); the reranker rule as non-inferiority at −3 points; Layer 1's verdict governs the wording of the serving statement; the thresholds labelled judgement-based; *no effect* only through an equivalence bound | §3, §3.3 |
| R3 | **scope wording.** Every claim reads *for questions using the glossary's primary term*; the records are LLM-drafted, LLM-verified, plus the owner check | §3, §13.2 |
| R4 | **judge reporting.** Precision, recall and F1 for *answers*, specificity, κ and Gwet's AC1 with counts; the false-yes rate's Wilson upper limit carried as a bound on H2 (its limit **amended by R13**); a prompt-paraphrase check before the freeze; the two Qwen primaries' correlated errors stated as a limitation; Opus empties retried at most once | §3.4, §6.3, §6.6, §13.3 |
| R5 | **judged-pair spot check** of 60 pairs stratified by judge label (30 *answers* / 20 *not* / 10 tiebroken), spread across arms; single rater a limitation (the size **amended by R15**) | §13.5, §14 O12 |
| R6 | **fixed sensitivities**, never deciding, with a *fragile* label when one flips a verdict (**narrowed by R14**; the studentized test **replaced by R22**) | §7 |
| R7 | **placebo glossary leg** (base × default, entries permuted across papers), descriptive (own entries **removed by R21**) | §5.3, §5.4 |
| R8 | **pre-freeze pilot** on the 191 held-out records, with its allowed consequences fixed now | §5.6 |
| R9 | **two stages after the freeze** (stage A: the Layer-1 legs; stage B: the rest) and **judging on demand** (stage A **extended by R20 and R25**) | §5.7, §6.2 |
| R10 | **freeze verification** by a signed annotated git tag pushed to the public repository, plus the private `PREREG.sha256` | README, §15 |
| R11 | **H2's estimand** stated | §3.1 |
| R12 | **C cost rule** (replaces R2's margin-only rule). In the attach decision, step (2) is *do not attach (C cost)* when the point estimate ΔC **< −1 point** or C_lo **< −3.6 points**; *attach* (step 3) needs L ≥ 2, L_lo > 0, ΔC ≥ −1 and C_lo ≥ −3.6. −3.6 is the smallest C change 300 C records detect with 80 % power at 5 % discordance (two-sided 0.05); a C cost smaller than about 3.6 points cannot be ruled out, and this is stated | §3.1, §3.3, §8, §13.9–13.10, §14 O1 |
| R13 | **false-yes bound on H2**: always U = max(U of the random arm, U pooled over the three arms), not the pooled limit only when an arm has < 30 pairs | §3.4 |
| R14 | ***fragile*** only for H1 and H2, and only through pre-named re-tests of the same claim: H1, the studentized test and topic clustering; H2, those two and the chunk-volume covariate (the studentized test **replaced by R22**, the covariate's test fixed by **R23**). Subgroup splits (XML-only, terciles, origin counts, alias kind) are reported and never make anything fragile; H1b is exempt | §3.4, §7 |
| R15 | **judged-pair spot check of 100 pairs** (was 60; D9 as amended by R5): stratified by final judge label (50 / 33 / 17), spread across arms; about 1.5 h of owner time. The 40-record check is unchanged | §5.5, §13.5, §14 O12 |
| R16 | **corpus v3 build facts**: six chunk-length bands at `tok512o0` (< 20 / 20–26 / 27–33 / 34–45 / 46–79 / ≥ 80, the open top band split at 80); v2 takes kept where their cell still matches, only moved and band-relaxed slots re-drawn; seeds unchanged; the fetch budget max(40, 15 × slots) per phase; relaxation source → type → band | §5.1 |
| R17 | **nearest-band fill** at the last relaxation level (`any`): the already-fetched candidate of nearest band, ties by type, source, seeded order, assigned by a global greedy over (slot, candidate) pairs; 0 new fetches | §5.1 |
| R18 | **repetition screen**: every added paper in every arm, after the C0 screen; distinct-chunk ratio < 0.90 excluded and replaced like other exclusions | §5.1, §15 |
| R19 | **residual chunk-volume gap accepted**: ±5 % of the neighbour arm's chunks is a build target, not a gate; the gap is covered by H2's pre-named chunk-volume covariate sensitivity (which by R14 can make H2 fragile); the deeper search for ≥ 80-chunk slots declined | §5.1, §7, §13.11 |
| R20 | **stage A includes the 5 norerank cells** the inactive-reranker gate needs (base × none, base × merged, neighbour × none, random-1 × none, random-2 × none, each × norerank), so every Layer-1 gate is read after stage A alone; stage A was 14 legs, stage B the remaining 14 cells plus the placebo (**superseded by R25**: stage-A retrieval 19 legs, stage-A judging 14, stage-B retrieval 10); the total is unchanged | §5.4, §5.7, §6.2, §9.1 |
| R21 | **placebo self-entries removed**: after the derangement, every entry equal to one of the receiving paper's own merged entries is dropped; drops counted per paper and in total; a paper left empty gets no placebo field and is counted | §5.3 |
| R22 | **bootstrap-t replaces the studentized randomization test**: a one-sided paper-cluster bootstrap-t (seed `261101\|alias\|boot`). The *fragile* set is H1 = bootstrap-t + topic clustering, and H2 = bootstrap-t + topic clustering + volume covariate. §7's statement about the weak null is corrected | §3.4, §5.5, §7 |
| R23 | **the H2 volume-covariate re-test** is a Freedman–Lane sign-flip with the studentized intercept (10,000 flips, seed `261101\|alias\|perm`), reusing the primary's unadjusted gold-only gate; closes O13 | §5.5, §7, §14 O13, §15 item 19 |
| R24 | **equivalence with zero discordant records** prints *not resolved* with the count, never *no effect larger than 0 points*; Wilson upper limits are rounded up | §3.3 |
| R25 | **BM25 legs of the Layer-1 attachments retrieved in stage A, judged in stage B**: stage-A retrieval is 19 legs (5 attachments × default, norerank, BM25, plus the 4 noise repeats; 20,729 calls), stage-A judging the default and norerank legs and the repeats (14); each attachment's glossary swapped once in total (**superseded by R26**: no swap at all); the stage-A commit pins the BM25 rows, their labels arrive in stage B; total unchanged | §5.4, §5.7, §6.2, §9.3 |
| R26 | **no in-place glossary swaps**: each (corpus arm, glossary arm) is its own bench collection, built once and never written again (base × none, merged, placebo; neighbour, random-1, random-2 × none, merged: 9 collections); each corpus embedded once, and its embedding shards loaded once per variant collection with the variant's glossary file (for *none*, an explicit file of empty rows), so no re-embedding and no backfill; each collection written exactly once, with a registry entry and a load receipt with provenance, censused at the start and end of every leg | §5.1, §5.3, §5.7, §9.3, §10, §15 |
| R27 | **abbreviation plurals in the glossary files**: for every `abbreviation` or `both` entry an added `Xs` entry with the same definitions, because the served renderer matches only the exact token (48 of 600 G records would otherwise get no entry on their gold chunk); and the rendering conventions (one line per alias, the 12-entry cap counting lines, served order, the 1,200-character stop, config, version strings, definition text) | §5.3 |
| R28 | **the pilot stays blind to outcomes**: in pilot mode no found@10, found@10-gold or RR@10 of any leg is printed, written to a public file or derivable from the pilot report, the baseline C found@10 included; §5.6's per-leg pair count is replaced by judge-pair counts per judging stage plus the registry total | §5.6 |
| R29 | **drift alarm report-only, arm-gap pause** (owner, 2026-11-04, before the freeze, blind to outcomes): the pilot's drift agreement was 0.946 overall (157/166; arms 0.944 / 0.946 / 0.946) against the 0.97 alarm; the overall and per-arm thresholds become reported reference lines; labelling pauses only when the Opus agreement of the arms differs by more than 0.05 (each arm >= 100 Opus-answered pairs), the threat to the arm contrasts; judge validity is otherwise carried by the owner's 100-pair spot check (§13.5) and the gold-only gate on H2 | §6.3, §14 |
| R30 | **no Argon cap** (owner, 2026-11-04, before the freeze; decides §14 O7 for Argon): the pilot's tiebreak share was 6.0 % of pairs (8.1 % in stage A) against the 4 % drafted; `argon_share` 1.0 in the judge config | §6.5, §14 O7 |

**Pre-freeze corrections of 2026-11-03, and where each is written.** These correct statements of this
text against the code and the serving configuration; none changes a decision above.

| # | correction, in short | where |
|---|---|---|
| P1 | `/v1/config` pins neither the BM25 glossary boost nor the reranker-field setting; the boost is established by found10's behavioural probe, the reranker field is declared only | §5.2, §9.2 |
| P2 | H2's U is always read from the frozen stage-A judge summary and cross-checked against the stage-A final rows; outside a dry run a summary without the counts is refused | §3.4 |
| P3 | the record spot check's draw: the first 40 record ids by seeded hash, not stratified; what the sheet shows | §5.5, §13.4 |
| P4 | the judged-pair spot check: arm re-split, the shortfall chain, the sheet order, the pool, the public record | §5.5, §13.5 |
| P5 | the pilot's intervals, the tiebreaker share split three ways, and H2's four discordance readings | §5.6 |
| P6 | public artifacts carry provenance without host or warnings; the two committed prereg artifacts regenerated so | §11, `artifacts/` |

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
record exists under any arm (§4.3 item 6 lists the one partial exception). **Corpus build (offline, #905
at `dd8ef5b`).** Between 2026-11-01T21:42Z and 2026-11-02T03:18Z the corpus builder ran once, under
the §5.1 rules as they stood before this version (no walk-down, no article-type filter, no type or
chunk-band matching, seeds `…|random|1` and `…|random|2`). It made **861** keyless OpenAlex API queries
(logged; its cache was seeded from the earlier scratch feasibility count of §5.1), read the **OpenAlex
parquet snapshot of 2026-10-14** for the random pools, downloaded NCBI's **PMC-ids** file, and fetched
**5,273** JATS files from the PMC OA bucket and **4,535** PDFs from OpenAlex OA locations (14,507 works
resolved). It selected 2,580 neighbours, of which **1,553** were kept, and two random draws of 1,553
each (overlap 6), and chunked them at `tok512o0` (61,460 / 43,910 / 44,759 chunks). These are counts in
the private manifest `counts.json` (sha256 `d54464ea…`). The build read bibliographic metadata and
paper full text only: **no store or index write, no retrieval, no model or judge call, and no outcome
data** of any kind. Its selections predate this version's rules and are re-drawn under them (§15 item
4); the counts above shaped none of this version's decisions except as stated in §5.1. The study's corpus-arm design and
its retriever factor were chosen after seeing round 3's null and the reranker test's reading that the
reranker can lose to BM25 on some corpora. H1's form (G-only, with C as a gate) and the extension of the
record set from 200 to 300 papers were both decided after v0's power sketch showed the DiD form weak, on
that sketch and acceptance counts alone. **For version 2** the independent methodology review of
2026-11-02 was read, and a scratch run of the merged power script on the second build's neighbour counts
(the *v2* manifests: 2,424 attributed neighbours) was made (§8); neither reads any retrieval or judge
output. **For version 3** the counts of the corpus build's third pass (*v3*, #911; private manifest
`counts-v3.json`, sha256 `44180414…`) were read: papers and chunks per length band and arm, each draw's
chunk-volume gap against the neighbour arm, the re-draw and relaxation counts, and a scan of the added
papers' distinct-chunk ratios in all three arms (a scratch scan by the PR #911 reviewer, not a
committed artifact). They shaped R16–R19 (§5.1, §13.7). The v3 pass, like the first, read
bibliographic metadata and paper full text only. **The v4 build** (#915, at `617d28d`; private
`counts-v4.json`, sha256 `fd15b1bf…`, and `power-v4.json`, sha256 `5ea9d25c…`) was read as counts and
entered in §5.1 and §8. Its repetition screen excluded **9** papers across the three arms, **7** of them
JATS papers in which one table chunk repeats across records (§5.1, R18); these 9 hits were seen before
the freeze (§13.7). The v4 build, too, read bibliographic metadata and paper full text only. **The
blind pre-freeze pilot (§5.6) ran on the 191 held-out records** from 2026-11-03T23:26Z to
2026-11-04T00:38Z (the report's `run_dates_utc`; the report was written at 02:12Z). All 29 legs
completed, and 3,574 deduplicated pairs were judged (213 tiebroken, 0 unjudged). No found@10,
found@10-gold or RR@10 of any leg was computed or seen (R28). What was seen: the unsigned discordance
shares (glossary G 6/83, glossary C 0/108, growth 6/191), the judge shares and gateway counts, the drift
agreement 0.946 (157/166), the tiebreak share 6.0 %, and the owner's 12-pair diagnostic and 40-record
check (§13.4). Its consequences are R29, R30 and the decisions on O7, O8, O10 and O12 (§14). The public
copy of its report is committed as `artifacts/pilot-report.json`. So this paragraph's statement that no
retrieval has been run holds for the **900 balanced records** only. All of this is disclosed so that a
reader can weigh it (§13.7).

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

The same runs feed both layers. **Neither layer changes the other's numbers**: a Layer-1 label does not
move a Layer-2 threshold, and a Layer-2 reading does not move a Layer-1 label. **Layer 1's verdict does
govern the wording of a serving statement** (R2, §3.3): a Layer-2 reading is quoted with the Layer-1
verdict on the same question beside it, in the words fixed there.

**Scope of every claim (R3).** Every H1, H1b and glossary sentence, in either layer, reads *for questions
using the glossary's primary term*: the G and C records ask with the primary term, and nothing here
measures questions asked with the alias, with another synonym, or in other words. Every claim in either
layer is about records that were **LLM-drafted** (Gemini 2.5 Pro, §4.1), **LLM-verified** (the
mechanical layer 1 and the `alias-verify/3` verifier, Claude with a Codex fallback, §4.1), **plus the
owner's check** of 40 records (§13.4). No sentence calls them human-written or human-validated.

### 3.1 Layer 1: the claim (confirmatory)

| id | family | hypothesis | records | cells compared | type | per-cluster statistic |
|---|---|---|---|---|---|---|
| **H1** | **G** | the merged glossary raises found@10 on G records | the **600 G** records of the balanced set (§4.2) | corpus **base**, retriever **default**: glossary **merged** vs **none** | superiority, **one-sided** (> 0), at α = **0.05** | per paper: Σ over its 2 G records of hit(merged) − hit(none) ∈ {−2, …, +2} |
| **H1b** | **G** | the gain on G exceeds the change on C (alias-specific) | the **900** balanced records (600 G, 300 C) | as H1 | difference-in-differences, **one-sided** (> 0); tested **only if H1 is confirmed**, at α = 0.05 (fixed sequence) | per paper: ½ Σ over its G records of Δ − its C record's Δ, with Δ = hit(merged) − hit(none) |
| **H2** | **N** | citation neighbours reduce found@10 more than random same-field papers | the **900** balanced records | retriever **default**, glossary **none**: corpus **neighbour** vs **random** (the average of random-1 and random-2) | superiority, **one-sided** (< 0), at α = **0.05**; **gold-only direction gate** | per paper: Σ over its 3 records of hit(neighbour) − ½ [hit(random-1) + hit(random-2)] |

**H2's estimand (R11).** H2 estimates the effect, on a study record's judged found@10 under the default
retriever, of adding **the citation neighbours of all 300 study papers** to OA-1000, against adding the
same number of matched random same-field papers (§5.1). A record's retrieval in the neighbour arm faces
its own paper's neighbours **and** every other study paper's neighbours at once; the two are **not
separated**, and H2 makes no claim about either alone. The origin of the chunks that displace a gold
chunk (own paper's neighbour, another study paper's neighbour, base) is reported descriptively (§7).

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
  keeps pace with a G rise. A C drop is read in Layer 2, by the C cost rule (R12: the point estimate at −1 point and
  the CI's lower bound at −3.6 points, §3.3). Printed beside H1 whatever the verdict: ΔC with its CI, ΔG, the ratio ΔC / ΔG, and the
  baseline C found@10 (base × default × none) with its headroom to 100 %. **Ceiling note:** if the
  headroom is below ½ ΔG (in records, 300 C), condition (b) cannot be met upward, so the gate cannot fire
  however much C would have risen without the ceiling; the sentence quoting H1 then says *C gate
  ceiling-limited*.
- **The reach gate on H1** (§9.1): at least 80 % of G records' gold chunks render the record's own entry.
- **The gold-only direction gate on H2.** H2 is *confirmed* only if its judged test is significant **and**
  found@10-gold points the same way: the net gold-only change, neighbour − random average, on the same
  900 records, is below 0 at the point estimate. The gold-only figure is a gate, not a third test: it has
  no p value. If the judged test is significant and the gate fails, H2 is *not confirmed (gold-only
  direction gate)* (§14 O3).

**Multiplicity: two families, each controlled at one-sided α = 0.05 (owner, R1).** The v1 design (Holm
over H1 and H2, with H1b after both) is replaced.

| family | question | members, in order | level |
|---|---|---|---|
| **G** (glossary) | does attaching the merged glossary make alias passages findable, and is the gain alias-specific? | **H1**, then **H1b** in fixed sequence | H1 at 0.05; H1b at 0.05, tested **only if H1 is confirmed** |
| **N** (growth) | does growing the collection with citation neighbours cost more than growing it with random same-field papers? | **H2** alone | 0.05, with the gold-only direction gate |

- **No level passes between the families.** H1's verdict does not change the level H2 is tested at, nor
  the reverse, and H1b's route depends on H1 alone.
- **H1b is tested only if H1 is confirmed.** If H1 is *not confirmed*, GATE-NOT-EVALUABLE or absent, H1b
  is **not tested**; its estimate, CI and power at 0.05 are reported descriptively (§3.2).
- A GATE-NOT-EVALUABLE or absent member enters at p = 1.0, and a family is never shrunk (round 3 D14).

**Why two families.** H1/H1b and H2 are separate claims that answer unrelated questions: a serving
decision about attaching a glossary field (base corpus, glossary arms varied), and a property of corpus
growth (corpus arms varied, no glossary). They compare disjoint pairs of cells, feed different
decisions in Layer 2 (§3.3), and no conclusion of this study is drawn from their conjunction: no sentence
says "the glossary helps **and** neighbours hurt" as one finding. Each family is therefore controlled on
its own. **Within Family G** the family-wise error is ≤ 0.05 in the strong sense by the fixed-sequence
argument: if H1's null is true, any false rejection in G needs H1 rejected, with probability ≤ 0.05; if
only H1b's null is true, the only error is p(H1b) ≤ 0.05. **Family N** has one member. **What this does
not control:** the probability of at least one false confirmation across both families is up to
1 − 0.95² ≈ 0.0975 when every null is true. A reader who wants a joint statement across H1 and H2 has no
study-wide error control for it, and this prereg makes none.

**Why these forms.**
- **H1 is G-only, with C as a gate (D1).** The DiD form of v0 gave each paper's single C record the weight
  of its two G records and needed 10–20 % of G records discordant (v0 §7). The G-only test at α = 0.05
  is resolvable at much lower discordance (§8). The price is a weaker control, which the C gate and
  H1b pay back: H1b keeps the DiD reading, tested only where H1 has something to explain (R1); otherwise
  it is descriptive.
- **H2 is neighbour vs random, not neighbour vs base.** Adding any papers adds distractors. The random arm
  holds the number of added papers, their field and their age fixed, so H2 reads what is specific to
  *citation* neighbours. Two random draws, averaged, keep one unlucky draw from deciding H2 (§5.1).
- **H2 counts judged answers, and this works against the hypothesis.** A neighbour is more likely than a
  random paper to *answer* a question, and an answer from another paper counts as a hit. So the judged H2
  asks whether neighbours displace more than they add. The gold-only gate requires that the displacement
  itself goes the same way, so a judged drop that is only a judge artefact cannot confirm H2.
- **H2 is on glossary none.** This keeps the corpus factor from being confounded by attachment, which
  itself depends on the corpus.

### 3.2 Outcome interpretation: Family G (H1 × H1b) and Family N (H2)

Every reading below is *for questions using the glossary's primary term* (§3, R3). H1b is tested only
when H1 is confirmed (§3.1, R1); H2's verdict does not enter this table.

| H1 | H1b | reading |
|---|---|---|
| **confirmed** | **confirmed** | the merged glossary makes alias passages findable, and the gain on G exceeds the change on C: an alias-specific gain |
| **confirmed** | **not confirmed** | G gained and the C gate did not fire (rule C), but the G gain was not shown to exceed the C change. The gain is on alias questions; its specificity is unresolved. Read beside the baseline C found@10 (ceiling, §2 item 4) and H1b's power floor |
| **confirmed** | **GATE-NOT-EVALUABLE** | as the row above, and H1b could not be read (for example too few non-zero clusters) |
| **not confirmed** | *absent* (not tested) | no evidence that the glossary helps alias questions; the power floor of §8 is printed beside it. Not a null unless the power at the stated scenario is ≥ .80, and *no effect* is written only through the equivalence bound of §3.3 |
| **GATE-NOT-EVALUABLE** | *absent* (not tested) | the design could not read H1: the C gate fired (C rose with G, rule C), the reach gate failed, or a general gate fired. Nothing is said about the glossary's effect on G, in either direction |

| H2 | reading |
|---|---|
| **confirmed** | adding all 300 papers' citation neighbours lowered judged found@10 more than adding matched random same-field papers, and the gold-only change points the same way (the estimand of §3.1). Quoted with the draw-gap flag and the judge false-yes bound (§3.4) |
| **not confirmed (gold-only direction gate)** | the judged test was significant but the gold-only change did not point the same way: the judged drop is not shown to be displacement and may be a judge artefact |
| **not confirmed** | no evidence that neighbours cost more than random papers; the power floor of §8 beside it; *no effect* only through the equivalence bound of §3.3 |
| **GATE-NOT-EVALUABLE** | the design could not read H2 (a general gate of §9.1). Nothing is said in either direction |

### 3.3 Layer 2: serving decisions (descriptive, CI-based, no gates)

Layer 2 reads the same runs as Layer 1. Every figure carries a **95 % paper-bootstrap percentile CI**
(10,000 resamples, seed `261101|alias|boot`, §5.5). No p value is computed in Layer 2, nothing in it is a test,
and no gate makes a Layer-2 figure unreadable. Each figure is printed with the gate verdicts that would
apply to it in Layer 1, for the reader, and is labelled *Layer 2, descriptive* in its sentence. Where a
CI is wide, the rule below does not fire, and the sentence says *not resolved*.

| decision | figure (900 balanced records unless stated) | rule, fixed now (R2) |
|---|---|---|
| **attach the merged glossary** | the G lift L = found@10(merged) − found@10(none), base × default, 600 G, 95 % CI [L_lo, L_hi]; the C change ΔC = found@10(merged) − found@10(none) on the 300 C, 95 % CI [C_lo, C_hi] (the C cost is −ΔC) | applied in this order: (1) **harm rule:** L_hi **< 0** → *harmful, do not attach*; (2) **C cost rule (R12):** ΔC **< −1 point** **or** C_lo **< −3.6 points** → *do not attach (C cost)*; (3) L **≥ 2 points** **and** L_lo **> 0** (and so, past step 2, ΔC ≥ −1 and C_lo ≥ −3.6) → *attach*; (4) otherwise *not resolved* |
| **keep the reranker default** | D = found@10(default) − found@10(BM25), neighbour arm, glossary none, 95 % CI [D_lo, D_hi] | **non-inferiority, margin −3 points:** D_lo **> −3** → *keep the default (non-inferior to BM25)*; D_hi **< −3** → *default inferior to BM25 by more than the margin: reconsider*; otherwise *not resolved* |
| **related-paper growth is a concern** | neighbour − random (average) found@10, default, none (the H2 figure) | a concern if neighbours cost **≥ 3 points more** than random with the CI **excluding 0** |

**The C cost rule in precise form (R12; owner, 2026-11-03).** ΔC is in points of the 300 C records
(1 record = 0.33 points), and C_lo is the lower limit of its 95 % paper-bootstrap percentile CI. The rule
has two parts, and either one blocks attaching: the **point estimate** ΔC < −1 point (a net loss of
more than 3 of the 300 C records at the estimate), or the **CI bound** C_lo < −3.6 points (a loss
larger than 3.6 points is not ruled out). *Attach* (step 3) needs ΔC ≥ −1 and C_lo ≥ −3.6 besides the
lift conditions. **A C cost smaller than about 3.6 points cannot be ruled out with 300 C records**, and
every *attach* statement says so (below).

*Why −3.6.* It is the smallest C change that 300 C records detect with 80 % power at 5 % discordance,
two-sided 0.05: §8's formula μ = k · √(q / (n + k²)), k = 1.960 + 0.842, at n = 300 and q 5 % gives
3.57 points (`artifacts/c_cost_rule.json`, `bound_derivation`). A tighter bound is one the design cannot
meet at the discordance it plans for.

*Why not the CI margin alone.* The v2 rule (attach only when C_lo ≥ −1) passed a glossary with **no** C
effect in about one study in six at 5 % discordance, because with one C record per paper the CI's width is
set by how many C records change at all. Pass rates of the C cost step (the share of studies in which
step 2 does not fire), from the committed calculation
(`artifacts/c_cost_rule.py`: 300 C records, the rule's paper bootstrap with 10,000 resamples, 10,000
simulated studies per row, seed `261101|alias|power|c-cost|…`):

| true C change, 5 % C discordance | v2 rule (C_lo ≥ −1) | R12 rule |
|---|---|---|
| 0 | .16 | .76 |
| −1 point | .05 | .49 |
| −2 points | .01 | .22 |

So the R12 rule lets a glossary with no C effect through about three times in four, and still stops about
four in five C costs of 2 points. It is not a fixed operating point: at 10 % C discordance a glossary
with no C effect passes about half the time (.49), at 2 % about nine times in ten (.93)
(`artifacts/c_cost_rule.json`, `scenarios`). The pilot (§5.6) estimates C discordance before the
freeze; the rule is not changed on that estimate except as a recorded deviation before the freeze.

**Layer 1 governs the wording (R2).** A Layer-2 decision is quoted with the Layer-1 verdict on the same
question, in these words (glossary decision with H1, and H1b where tested; growth with H2). The reranker
decision has no Layer-1 member and is quoted as *Layer 2, descriptive*.

| Layer 2 says | Layer 1 verdict | the serving statement reads |
|---|---|---|
| *attach* | H1 confirmed | *attach, for questions using the glossary's primary term* (with *alias-specificity unresolved* unless H1b is confirmed) |
| *attach* | H1 not confirmed | *attach provisionally; the confirmatory claim was not confirmed* |
| *attach* | H1 GATE-NOT-EVALUABLE | *attach provisionally, claim not evaluable* (and the gate that fired) |
| *harmful, do not attach* | any | *harmful, do not attach*, with the H1 verdict beside it |
| *do not attach (C cost)* | H1 confirmed | *do not attach: the gain on alias questions is confirmed, but* the part of the C cost rule that fired: *term-bearing passages lost more than 1 point* (ΔC < −1) or *a loss on term-bearing passages larger than 3.6 points is not ruled out* (C_lo < −3.6) |
| *do not attach (C cost)* or *not resolved* | H1 not confirmed or GATE-NOT-EVALUABLE | the Layer-2 phrase, with the H1 verdict beside it |
| growth *a concern* | H2 confirmed | *related-paper growth is a concern* |
| growth *a concern* | H2 not confirmed (either form) or GATE-NOT-EVALUABLE | *a concern provisionally; the confirmatory claim was not confirmed* (or *not evaluable*) |

**Every *attach* statement** (any row above that reads *attach*) adds *a cost on term-bearing passages
smaller than about 3.6 points is not ruled out (300 C records)* (R12).

**The thresholds are judgement-based.** The 2-point lift, the −1-point C limit on the point estimate and
the 3-point margins are the owner's judgement of what is worth acting on. **No cost model** of a missed
answer, a displaced answer or the added text exists, and none was built; the thresholds are not derived
from one. The −3.6-point C bound is not a judgement of what matters: it is the design's detection limit
for C (above). All are fixed here and are never tuned on any result, the pilot's included.

**Statements of no effect (R2).** "No difference" is never written. A figure in either layer may be
described as showing no effect **only through an equivalence bound**: when its **90 %** paper-bootstrap
percentile CI (same draws, seed `261101|alias|boot`) lies entirely within ±δ, which is the two one-sided
tests procedure at 0.05 each side (Lakens 2017, *Equivalence Tests: A Practical Primer for t Tests,
Correlations, and Meta-Analyses*, Social Psychological and Personality Science 8(4):355–362,
doi:10.1177/1948550617697177), the sentence reads *no effect larger than δ points*. **δ is the contrast's
realised 80 %-power MDE**: §8's formula (k = 1.960 + 0.842) at the contrast's realised discordance, the
share of its records whose hit differs between the two cells, printed beside it. At the planning
discordance q 5 % that is **≈ 2.5 points** for H1 (2.54, 600 G) and ≈ 2.1 points for H2 (900). Otherwise
the figure is *not resolved*. **With zero discordant records** (R24; owner, 2026-11-03) the formula gives
δ = 0, and the reading is *not resolved (0 discordant records)*, never *no effect larger than 0 points*.
*Why:* round 3 saw 0 discordant records between the served glossary and none (§2 item 1), so the case is
live, and a zero-width bound is no evidence of equivalence.

**Wilson limits are rounded up.** Wherever a Wilson upper limit is printed or enters a rule (the false-yes
bound of §3.4, the per-arm false-yes rates of §6.3, the spot checks of §13.4–13.5), the committed
statistics round it **up**, never to nearest, so rounding can only widen it.

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
  the 191 records on the 108 unsatisfied papers, as a descriptive leg (§4.2; they are also the pre-freeze
  pilot, §5.6, and every sentence quoting them says so);
- **the placebo glossary leg (R7, §5.3):** found@10 under base × default × placebo against base × default
  × none and against merged, on G and on C, with the placebo's field coverage beside it.

**Exploratory.** Anything else, labelled *exploratory* in every sentence that quotes it.

A one-sided member that is not significant is *not confirmed*, with its power floor beside it (§8), and
*no effect* is written only through the equivalence bound above. A member whose minimum attainable p
exceeds its level is **GATE-NOT-EVALUABLE** (§9.1).

### 3.4 What is printed beside the Layer-1 members

Beside H1: the C net change with its CI, the ratio ΔC / ΔG and the C gate's verdict (rule C, §3.1), the
baseline C found@10 (base × default × none) with its headroom and the ceiling note, the reach count,
found@10-gold. Beside H1b: the baseline C found@10 and its distance from the ceiling. Beside H2: the
gold-only net change (the gate), the draw-to-draw gap, the per-arm judge false-yes rates (§6.3), and the
**judge false-yes bound B** (below). If |H2's net effect| is smaller than the draw-to-draw gap
|found@10(random-1) − found@10(random-2)|, every sentence that quotes H2 carries *within random-draw
variation*, whatever H2's label. Beside H1 and H2 both: the sensitivities of §7 and, if one of the
named re-tests flips the verdict (R14, R22: for H1 the bootstrap-t and topic clustering; for H2 those
two and the chunk-volume covariate), the label *fragile*. H1b carries the same displays and is never labelled
*fragile* (R14, §7).

**The judge false-yes bound on H2 (R4).** A false *answers* label adds a spurious hit. In the neighbour
arm that works **against** H2; in the random arms it lowers the neighbour − random contrast and so works
**for** H2. The bound therefore reads the random arms. Let J_r be the number of the 900 records whose hit
in a random-draw cell (default × none) rests on judged pairs only (gold outside the top 10), averaged
over the two draws. Let U_r be the **Wilson 95 % upper limit of the random arm's false-yes share**:
among the random-arm drift pairs (§6.3) that the final label calls *answers* and Opus answers, the share
Opus calls *not*; let U_p be the same limit over the three arms pooled; and let
**U = max(U_r, U_p)**, always (R13; owner, 2026-11-03). (§6.3's per-arm false-yes rate, per Opus-*not*
pair, is printed beside it; the bound uses the share among *answers* labels because a hit is made by an
*answers* label.) Then **B = 100 · U · J_r / 900 points** is an upper limit on how much of H2's net effect
spurious random-arm hits could account for. U_r, U_p and their n are printed beside B. **U is always
read from the frozen stage-A judge summary** (P2), because all of H2's judged pairs are stage A; it is
cross-checked against the stage-A final rows. Outside a dry run a mismatch is refused, and so is a
summary that carries no false-yes counts (`alias_study_stats.false_yes_inputs`); only a dry run falls back
to the rows. A scope with no
such pair gives no limit; if neither has one, B is *absent*. **Rule:** if |H2's net effect| ≤ B, every
sentence that quotes H2 carries *within the judge false-yes bound*, and the gold-only change is quoted
first; B never changes H2's label. *Why the maximum:* the random arm alone may hold few such pairs, so
its limit can be loose or, by chance, low; the pooled limit is steadier but can sit below a random arm
whose false-yes share really is higher. The larger of the two never understates the bound.

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
calling those committed functions at `323d470`. The committed recount (`alias_study_recount.py`,
`artifacts/record-recount.json`) reproduces every count of this table (§15 item 2).

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
fixed before any retrieval, so the split cannot depend on an outcome. The 191 are also the **pre-freeze
pilot** (§5.6): they are retrieved and judged once before the freeze, and their post-freeze descriptive
leg is quoted as *retrieved in the pilot*.

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
7. **Layer-3 record validity.** The owner's record spot check of §13.4 (round 3's rule, D9) was done on
   2026-11-04: 37 of 40 valid (0.925, at or above 0.85; `artifacts/spot-records-counts.json`).

## 5. Design: factors and cells

Four factors. Corpus arm, retriever and glossary arm define the **24 cells** that every record is run in.
Question type (G / C) is a property of the record. One further leg, the placebo glossary (§5.3, R7), is
run on base × default only.

### 5.1 Corpus arm

| arm | content |
|---|---|
| **base** | OA-1000 as it stands (32,765 chunks), embedded once; base × {none, merged, placebo} are new bench collections loaded from those embeddings (R26, §5.3), never the live collection |
| **neighbour** | base + the **citation neighbours** of the 300 satisfied papers (rule below) |
| **random-1**, **random-2** | base + **matched random papers**, two independent draws (rule below) |

Each expanded arm is a new bench collection. Base chunks are copied **with identical chunk ids and
content**. Base chunks are embedded once and shared by the four corpus arms, and each added paper is
embedded once; each arm's collections load the base shards plus that arm's added-paper shards, and its
glossary variants are separate collections loaded from the same embeddings (R26, §5.3, §9.3). Each added paper goes through the **same ingest** (chunker `tok512o0`, the same embedder and
version, the same tools image) and the **same glossary pipeline** (§5.3).

**Neighbour selection (D4).** Metadata from OpenAlex only, made before any retrieval. For each study
paper:
- **References:** of the works it references that have **open-access full text** and are not in OA-1000,
  the **5 most cited** (OpenAlex citation count at query time).
- **Citers:** of the works that cite it, have open-access full text and are not in OA-1000, the **5 most
  recent** (OpenAlex publication date).
- **Walk-down (owner, 2026-11-01).** Each ranked list (references by citation count, citers by date) is
  walked down past works that **cannot be fetched**: not in the PMC OA subset and with no licensed OA
  PDF, refused by robots, HTTP 403 or 429, a download that is not a PDF, or a PDF paper excluded by the
  control-character screen (below). The walk stops when the paper has **5 references and 5 citers**, or
  when the list ends. The skipped works, by reason, are counted in the manifest.
- **Article types (owner, 2026-11-01).** Only **research articles and reviews** are taken, in both the
  neighbour and the random arms. Errata, editorials, letters, comments, retractions and paratext are
  dropped before ranking and do not count towards a cap. The drops by type are counted per arm.
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
- are not already drawn for another study paper in the same draw;
- are research articles or reviews (above).

**Matching per study paper (owner, 2026-11-01).** In addition to the rules above, each draw is matched
per study paper to that paper's attributed neighbours on **paper count**, **article type** (research
article / review) and **chunk-length band** (the paper's chunk count at `tok512o0`, in bands fixed by the
committed selector before any draw and recorded in the manifest; the six bands are fixed under *Build
facts of the v3 pass*, below), and on the PDF share (below). Where a cell of that match cannot be filled
at the strict level, it is relaxed in a fixed order (source, then type, then band, below), and the strict
shortfall per cell is printed per draw.

The candidates are ordered by sha256 of `<draw seed>|<study paper>|<work id>`, ascending, with draw seed
`261101|alias|random-1-v2` or `261101|alias|random-2-v2` (§5.5), and the first eligible ones are taken. The
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

**Build facts (owner, 2026-11-01).** These are how the corpus build reads the rules above. They are
recorded here as design facts, because the text is not frozen; after the freeze any such departure is a
`DEVIATIONS.md` entry.
- **Random pools** are drawn from the **OpenAlex parquet snapshot of 2026-10-14**. The study papers'
  topics are read from the **live OpenAlex API**. The snapshot date and the API query date are both in
  the manifest.
- **PMCIDs** come from NCBI's **PMC-ids** file (the snapshot's ids carry none).
- **"Open-access full text"** means a PMCID in the **PMC OA subset**, or an OpenAlex OA location with a
  **licensed OA PDF**. A landing page does not count.
- **JATS** is taken from the **PMC OA bucket**, the source OA-1000 was built from.
- **"Not in OA-1000"** is matched on the OpenAlex id, the DOI and the title; a match on any one excludes
  the work.
- **Reference lists.** PDF-sourced papers keep their reference lists; OA-1000's text had none. This is a
  **known difference** between the added and the base text, and the share of added chunks that fall in a
  reference list is reported per arm and draw.

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
recorded change to this text. **An excluded neighbour is walked past** (the walk-down above): the next
work on its ranked list is tried, within the caps, and the attributed counts are taken after the screen. **An excluded random candidate is skipped**, and the draw
continues in its seeded order. Exclusions are counted and printed **per arm and draw**, split by
references and citers in the neighbour arm.

**Repetition screen (owner, 2026-11-03, R18).** Every added paper in every arm, **whatever its source**
(JATS or PDF), after the control-character screen and before it counts towards its arm, gets a
**distinct-chunk ratio**: the number of distinct chunk texts divided by the number of chunks, at
`tok512o0`. Two chunk texts are the same when they match exactly after Unicode **NFC normalisation** and
**whitespace collapsing** (every run of whitespace replaced by one space, leading and trailing whitespace
removed). A paper whose ratio is **below 0.90 is excluded**, and it is replaced as other exclusions are:
an excluded neighbour is walked past (the walk-down above), and for a random draw the walk continues in
its seeded order. Exclusions are counted and printed **per arm and draw**. *Why 0.90:* ordinary papers
sit near 1.0, and the papers below 0.90 are ones whose chunk store holds the same text many times over.

*What the screen caught in the v4 build (a disclosure).* It excluded **9** papers of the v3 arms
(neighbour 3, random-1 4, random-2 2). Two are PDFs: one a broken extraction yielding 2,500 chunks (ratio
≈ 0.53), the other a paper in which 2 chunks repeat about 70 times each (≈ 0.61). **Seven are JATS
papers** whose converter repeats a caption plus a header at chunk size, so one table chunk recurs across
records; the #915 review confirmed that this is genuinely identical text in the chunk store. The scratch
v3 scan by the PR #911 reviewer saw only the two PDFs and missed the 7 JATS cross-record cases. The rule
is per paper, as written, and is applied to all 9 unchanged. By the same measure **OA-1000 holds 4 papers below 0.90** (0.4 % of
1,000; a scratch count made for #915, not a committed artifact); the base corpus is not screened, so the
screen applies to added papers only (§13.7).

**Build facts of the v3 pass (owner, 2026-11-03, R16).** These record how the third build pass reads the
matching rule above. They are design facts, as the build facts of 2026-11-01 are.
- **Length bands.** Six bands of a paper's chunk count at `tok512o0`: **< 20, 20–26, 27–33, 34–45,
  46–79, ≥ 80**. The cuts 20, 27, 34 and 46 are the committed selector's quantile cuts of the neighbour
  arm's chunks per paper (five bands in v2, `alias_corpus.length_cuts`); the v3 pass splits v2's open top
  band at **80** (`alias_corpus.V3_SPLIT_AT`). The neighbour arm itself is unchanged by the split; it
  changes the random draws' matching cells only.
- **Re-draw only what moved.** Per study paper and draw, the v3 pass **keeps every v2 take whose cell
  still matches** under the six bands, at its v2 level, and re-draws only the affected slots: a take
  **moved by the split** (the v3 cell of its own band has no room left) and every v2 take matched at the
  band-relaxed level `any`, which is **released**. The takes are considered in v2 take order
  (`alias_corpus.carry_over`; `alias_corpus_run.V3_REDRAW_RULE`).
- **Same seeds, same order.** The open slots are re-drawn by the v2 matched-draw rule
  (`alias_corpus.draw_matched`) on the same seeded candidate order, seeds unchanged
  (`261101|alias|random-1-v2`, `261101|alias|random-2-v2`, §5.5), walked from the top: a candidate taken
  by any kept or re-drawn slot of the same draw (any study paper, or the same PMCID or DOI) is skipped;
  released takes and passed-over candidates are eligible again; the study papers are served in the v2
  attribution order.
- **Fetch budget.** Per study paper and per phase of the draw (the strict pass, then relaxation), at most
  **max(40, 15 × n)** candidates are resolved, where n is **all** of that study paper's slots (kept and
  open) and a candidate read from an earlier fetch counts against the budget
  (`alias_corpus_run.V3_BUDGET_RULE`). **The budget can decide which candidate fills a slot**, in both
  phases. These are disclosed properties of `alias_corpus.draw_matched`, not rule changes, and the code
  stays as it is:
  - *Strict pass.* When the strict budget is spent, the walk stops, and the rest of the seeded order is
    passed to relaxation with the candidates already passed over. Relaxation at `source` then walks them
    in seeded order, so an earlier candidate that matches type and band (with another source) can take a
    slot ahead of an exact match further down that the strict pass never reached.
  - *Relaxation.* At the `source` and `source+type` levels, once the relaxation budget is spent, an
    unfetched candidate is skipped, so a later candidate already fetched can be taken in its place.
  - *Late exact matches.* Relaxation can fetch a candidate the strict pass never reached and, when it
    matches its slot exactly, records the take as level `strict`. A `strict` take is therefore not always
    one the strict pass made.
  - *The other source's cell at `source`.* At the `source` level a take goes to the first open cell, in
    sorted cell order, that matches its type and band. When its own exact cell and the other source's
    cell are both open, it can go to the other source's cell and be recorded at level `source` while its
    own exact cell stays open.

  In every case the choice depends only on metadata, the seeded order and fetch availability; it is
  content-blind. At `any` the budget plays no part, because the nearest-band fill makes no new fetches
  (R17).

- **Relaxation order.** A cell the strict pass (type, band and source all matching) leaves open is relaxed
  **source first, then type, then band**: the levels are `strict`, `source` (type and band match),
  `source+type` (band matches) and `any` (band relaxed too; `alias_corpus.RELAX_ORDER`). Every take
  records the level it was matched at, and the levels are counted per draw.

**Nearest-band fill at the last level (owner, 2026-11-03, R17).** At `any`, a slot is filled not by the
first passed-over candidate in seeded order but by the candidate **already fetched by that study paper's
walk** in this draw (accepted by every screen and not taken) whose band is **nearest** the slot's band,
by band-index distance; ties are broken by **type match**, then **source match**, then the **seeded
order**. The level makes **0 new fetches**; a slot with no such candidate left is a printed shortfall.
Each take at `any` records its band distance, and the distances are counted per draw.

*Fill order when a study paper has several open `any` slots* (`alias_corpus.nearest_band_fill`). The
assignment is a **global greedy** over all (open slot, already-fetched candidate) pairs of that study
paper: the pair with the smallest key is filled first, then the smallest of the pairs left, and so on.
The key is band distance, then type mismatch, then source mismatch, then the slot's position in sorted
cell order, then the candidate's seeded order. The result is deterministic and does not depend on the
order in which the slots are listed. It is greedy, **not a minimum-total assignment**: with open slots in
bands 5 and 3 and candidates in bands 4 and 0 (slots and candidates of one type and one source, so the
band distance and the cell order decide), it pairs 3 ↔ 4 first (distance 1, the band-3 slot first
in cell order) and then 5 ↔ 0, a total band distance of 6, where a minimum-total assignment (5 ↔ 4,
3 ↔ 0) has 4. On the 8 study-paper × draw cases of the v4 build with several `any` slots (6 distinct
study papers), a minimum-total assignment gives the same totals as the greedy (checked in the #915
review).

**Chunk volume (owner, 2026-11-03, R19).** Each random draw's total added chunks is targeted at **± 5 %**
of the neighbour arm's. This is a **build target, not a gate**: a draw outside it is used as built, and
its gap is printed per draw. The residual gap is covered by the pre-named **H2 chunk-volume covariate**
sensitivity (§7), which by R14 can make H2 *fragile*. A deeper search for slots of ≥ 80 chunks (about
4,000–8,000 further fetches) was declined.

**What the v3 pass measured** (private `counts-v3.json`, sha256 `44180414…`): the neighbour arm added
89,180 chunks; random-1 86,573 (−2.9 %) and random-2 84,131 (−5.66 %), each on 2,424 papers. These were
the counts seen before R16–R19 were written; the v4 counts below supersede them as the build.

**The v4 carry-over** (the build that applies R17 and R18 to the v3 pass). Verbatim from the v4 draw
manifests (`redraw_rule`, `alias_corpus_run.V4_REDRAW_RULE`):

> per study paper and draw, the v3 takes are carried over in v3 take order (alias_corpus.carry_over_v4):
> a take is released when its paper fails the repetition screen (repetition_screen), when it was matched
> at the band-relaxed level (any_nearest_band), or when its slot has no room in the v4 quota
> (quota_changed: the study paper's neighbours changed under the screen); every other take is kept
> exactly. A study paper whose only released slots are any_nearest_band (quota unchanged) refills them
> with alias_corpus.nearest_band_fill from the candidates its v3 walk fetched before the 'any' level
> (replayed from memoised outcomes and checked take-for-take against the v3 manifest), screened, not
> taken in the draw: no walk, no fetch. Any other study paper with open slots walks again with
> alias_corpus.draw_matched (any_rule='nearest', repetition-screened outcomes) on the same seeded order
> from the top, skipping candidates taken in the draw; study papers in the v3 order

In short: v3 takes are read in take order and released if screen-failed, else if matched at `any`, else
if their cell has no room in the v4 quota; everything else is kept exactly. A study paper whose only
releases are `any` takes, with its quota unchanged, refills them from its v3 walk's accepted candidates
fetched before `any`, with no new walk or fetch; any other study paper with open slots walks again from
the top of the same seeded order, with the same budget. **A neighbour excluded by the screen** re-runs
that study paper's walk-down with the screened outcomes, and its random quota is re-derived from the new
neighbours: a take whose cell lost room is released and not refilled, and a new cell is filled by a
walk. **Disclosed:** one study paper's walk-down was exhausted after its screened neighbour, so it has
one neighbour fewer, and each draw one random slot fewer, than in v3 (2,423 papers per arm, from 2,424).

**Build counts, v4.** The v4 build commit is **`617d28d`**, the build commit of #915, which is merged
with a merge commit (`a623981`), so it is reachable from `main`. `counts-v4.json`,
`neighbour-selection-v4.json`, both draw manifests and `power-v4.json` embed provenance at `617d28d`
(clean, `citable true`, the stages' commits agreeing); `neighbour-counts-v4.json`,
`per-study-chunks-v4.json` and `chunkcheck-v4.json` carry none and are pinned by `counts-v4.json`'s
`manifest_sha256`. Private manifests, by sha256 prefix: `counts-v4.json` `fd15b1bf…`,
`random-1-draw-v4.json` `1d8a72e5…`, `random-2-draw-v4.json` `cb3b9acc…`, `neighbour-selection-v4.json`
`2da5dc13…`, `neighbour-counts-v4.json` `ed71fcb9…`, `per-study-chunks-v4.json` `5c0cfe15…`,
`chunkcheck-v4.json` `8d898a69…`, `power-v4.json` `5ea9d25c…`; chunk files (neighbour / random-1 /
random-2) `0050a538…` / `2defc483…` / `f6b012e5…`. The earlier v4 runs at `e9379c1` and `1574286` are
superseded and kept.

| v4 count | neighbour | random-1 | random-2 |
|---|---|---|---|
| papers added | 2,423 | 2,423 | 2,423 |
| chunks added | 88,765 | 84,762 | 84,552 |
| chunks vs neighbour | — | −4.51 % | −4.75 % |
| papers per band, < 20 / 20–26 / 27–33 / 34–45 / 46–79 / ≥ 80 | 418 / 520 / 515 / 481 / 371 / 118 | 418 / 520 / 515 / 486 / 385 / 99 | 418 / 520 / 515 / 489 / 381 / 100 |
| PDF share | 8.34 % | 9.45 % | 9.41 % |
| article / review | 1,845 / 578 | 1,930 / 493 | 1,927 / 496 |
| takes at `strict` / `source` / `source+type` / `any` | — | 2,283 / 41 / 77 / 22 | 2,293 / 35 / 74 / 21 |
| band distance at `any`, 1 / 2 | — | 20 / 2 | 16 / 5 |
| repetition-screen exclusions (R18), papers of the v3 arm | 3 | 4 | 2 |
| v3 takes kept / slots re-drawn | — | 2,397 / 26 | 2,399 / 24 |
| v3 takes released: `any` / screen / quota changed | — | 23 / 4 / 0 | 21 / 2 / 2 |
| shortfall | — | 0 | 0 |
| draw-to-draw overlap | — | 201 | 201 |

Both draws are within ± 5 % of the neighbour arm's chunks. These are the **uncapped** figures, the
headline. `counts-v4.json` also records them under a 500-chunks-per-paper cap (one neighbour paper lies
above it): −4.27 % and −4.50 % capped, against −4.51 % and −4.75 % uncapped. The **≥ 80 band is still short** in both
draws: 118 neighbour papers against 99 and 100. **Fetches:** the first v4 run (`e9379c1`, superseded)
made 41 paper fetches and 51 PMC metadata lookups; the later runs (`1574286`, superseded, and the final
`617d28d`, re-run from memo with identical takes) re-used them and made none.

**Manifests.** Before any retrieval, a manifest per expanded arm records, as counts in public: references
and citers found, open-access, already in OA-1000, deduplicated, skipped by the walk-down (by reason),
dropped by article type, added per study paper (distribution), chunks added, text-source mix,
control-character exclusions, repetition-screen exclusions, papers and chunks per length band, takes per
relaxation level and band distances at `any`, random shortfalls (by matching cell), the chunk-volume gap
per draw, the draw-to-draw overlap, the seed strings (§5.5), the snapshot date and the OpenAlex query
date. The ids live only in the private manifests, whose sha256s are public.

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
otherwise declared, as in the reranker test §3. **Declared settings** (corrected, P1): the bench tenant
serves with the BM25 glossary boost on and with the reranker reading the glossary field, as in round 3.
`/v1/config` is an allowlist that includes neither setting, so the per-leg admin-config hash pins
neither. The boost is established behaviourally by found10's boost probe (§9.2), which reads *active*,
*inactive* or *mixed*: it proves a boost greater than 0, not its value, and reads `declared` on the
*none*-arm collections. The reranker field is declared only. **default** is the retriever of every
Layer-1 member.
BM25 enters Layer 2 (H3 and the reranker decision). norerank is a Layer-2 secondary and feeds the
inactive-reranker gate (§9.1), whose cells are run in stage A (R20, §5.7).

### 5.3 Glossary arm

| arm | rendered `symbol_glossary` field |
|---|---|
| **none** | empty on every chunk, from an explicit glossary file of empty rows (every document has a row) with its own version stamp (R26) |
| **merged** | the paper's merged glossary (symbol + defined abbreviation), rendered on **every chunk where the entry's alias occurs** as a whole token (the **mention** rule; abbreviations state no value), under the served caps (12 lines, one per alias, and 1,200 characters; R27, below) |
| **placebo** (one leg: base × default only; R7) | another paper's merged glossary: the entry lists are **permuted across the OA-1000 papers that have at least one merged entry** by a seeded derangement (each paper receives the whole entry list of the paper that follows it in the order of sha256 of `261101\|alias\|placebo\|<paper>`, ascending, the last receiving the first's), with the receiving paper's own entries removed (R21, below), then rendered on the receiving paper's chunks by the same mention rule and caps |

**The placebo leg is a descriptive control for "extra text"** (R7). It attaches glossary-shaped text that
carries no entry of the chunk's own paper, so a G gain under merged that the placebo also shows is not an
alias bridge. Because rendering is by mention, a foreign alias renders only where that token happens to
occur, so the placebo attaches to fewer chunks than merged: its field coverage (share of chunks with a
field, mean rendered characters) is printed beside merged's, and the comparison is read with it. The
placebo enters no test and no Layer-2 decision. It is its own collection, loaded from the base corpus's
embeddings with the placebo glossary file (R26), with its own version stamp and census, and retrieved in
stage B (§5.7).

**Own entries removed (owner, 2026-11-03, R21).** After the derangement, every entry of a receiving
paper's placebo list that equals one of that paper's own merged entries (the alias exactly, and the
primary term under the glossary pipeline's merge-key normalisation, *Merge* below) is **removed**. The
drops are counted per paper and in total. A paper left with an empty list gets **no placebo field**, and
such papers are counted. All three counts are printed beside the placebo's field coverage. *Why:* a
whole-list permutation can hand a paper one of its own entries, which would break "the placebo carries no
entry of the chunk's own paper".

**Each glossary arm is its own collection (owner, 2026-11-03, R26).** No glossary is swapped in place.
Each (corpus arm, glossary arm) is a separate bench collection, written exactly once: base × {none,
merged, placebo} and {neighbour, random-1, random-2} × {none, merged}, **9 collections**. **The load
route:** each corpus is **embedded once** (`embed-bulk.cwl`), and the same embedding shards are **loaded
once per variant collection** (`load-embeddings.cwl`) with `glossary_file` set to a
`ragstack.glossary_file/v1` file: for *none*, an explicit file of **empty rows** (every document has a
row) with its own version stamp; for *merged* or *placebo*, that arm's file. `glossary_file: null` is
**not** used for *none*: it would keep the embedding shards' own field, the rules glossary by default. The
loader renders the field under the mention rule and the conventions of R27 (below). So nothing is
re-embedded and nothing is backfilled. **Base embeddings are shared:** `load-embeddings.cwl` takes its
embeddings as a list of files, so each collection loads the base shards and its arm's added-paper shards
in one list, and base is embedded once. `glossary_file` is one file per load, so each variant's file
covers the base and the added documents together, and every document in the load has a row. The loads run through the workflow
engine (GoWe) on the bench tenant, under the approved path. Each collection has a **registry entry** and a
**load receipt** with provenance. They are bench collections with an unmistakable prefix, recorded in the
private run config. **The stamp census** (§9.2) expects every chunk of a collection to carry its glossary
file's version stamp; for a *none* collection, every chunk carries the *none* file's stamp and an empty
field. *Why:* nothing is overwritten, nothing has to be restored, the legs do not depend on the order they
run in, and a re-run reads the same collection. *Capacity:* about 16–19 GB of dense vectors and 2–3 GB of
BM25 index for the 9 collections; they need not be built in sequence, but bulk writes go one index at a
time, with the 85 % heap stop.

**Plurals and rendering conventions (owner, 2026-11-03, R27).** The served renderer matches an alias only
as the exact token, while the study's matcher (`alias_abbrev.alias_occurs`) also accepts an
abbreviation's plural `Xs`. Under the exact token alone, **48 of the 600 G records** would get no entry on
their gold chunk (this and the two-line count below are scratch counts from the glossary-file feasibility
check on the base chunk export, not committed artifacts). The glossary files therefore carry, for every `abbreviation` or `both` entry, an added
**`Xs` entry with the same definitions**. Where both forms occur in a chunk (about 2,247 base chunks), the
field shows two lines. With the plural entries, **600 of the 600** balanced G records render their own
entry on the gold chunk under base × merged (the writer's private parity record
`base.merged.parity.json`, sha256 `b2a1b3a6…`, for the file `1804f55c…` and the balanced set `2be86f8c…`).
The rendering conventions, for every glossary file:
- **one line per alias**: `alias — p1; p2`;
- the **12-entry cap counts lines** (aliases), not merged entries. Disclosed in the writer's census
  (`alias_glossary_file.ENTRY_CAP_RULE`): on base × merged, **88** chunks render a different field than
  under a cap of 12 distinct merged entries with the same lines, order and character rule (**90** if a
  line counts only the entries whose alias is the form); 98 chunks match more than 12 entries; the line
  cap cuts 38 chunks and the character cap none. Source: the private census
  `base.merged.jsonl.manifest.json` (sha256 `bd805a18…`), written by the writer at `c111f64` (#918),
  clean and citable. (An earlier scratch figure of 61 is withdrawn: no committed computation reproduces
  it);
- **served order**: aliases by codepoint, ascending; definitions in file order;
- the **1,200-character cap stops at the first line that would exceed it** (the count includes the
  newline between lines); that line and the ones after it are dropped, and no line is truncated;
- served config `attach_only_with_value` false and `positional` false;
- each variant has **its own version string**, ending in `+attach=mention`; the chunk stamp is
  `<version>@file/1`, so a stamp ends in `+attach=mention@file/1`;
- **definition text**: an abbreviation's primary term is cut from the paper's **canonical text**
  (`alias_abbrev.find_definitions` on `symbol_glossary.canonical_text`). That text is NFKD-decomposed
  character by character, with bar spellings mapped to a combining macron, Greek variant letters unified
  (`ϕ` → `φ`), LaTeX Greek names and underscore subscripts folded. So the compatibility folds stay folded
  in the rendered line: the micro sign becomes Greek mu, a ligature becomes separate letters, a
  math-italic letter a plain one. The renderer, not the glossary-file writer, then passes every line
  through `symbol_glossary.display_form` (NFC; applied in `Glossary.for_chunk` on `main`), which recomposes canonical decompositions (an accented letter, a barred letter that has a
  precomposed form) but undoes none of those folds.

For added papers in **every** expanded arm, the glossaries are generated **by the same pipeline at the
same versions** (owner, 2026-11-01):
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

24 cells, plus **one noise repeat per corpus arm** (default × none, run twice; §9.4), plus the **placebo
leg** (base × default × placebo, §5.3): 29 legs × 1,091 records = **31,639** retrieval calls, at ≤ 4 in
flight against the bench collections. Moving the 5 norerank and the 5 BM25 cells of the Layer-1 attachments
into stage-A retrieval (R20, R25, §5.7: 19 legs, 20,729 calls) changes the order of the calls, not their
number. After the freeze the 191 pilot records are run again in every leg as
the descriptive leg (§4.2). No gateway call is made for retrieval. The pilot (§5.6) adds its own calls
before the freeze.

### 5.5 Seeds (owner, 2026-11-01, §14 O9)

One recorded seed string per draw, fixed here and copied into every manifest and artifact that uses it.
They follow the project's `<yymmdd>|<study>|<purpose>` pattern (the pool seed was `261031|alias|main|O`).
The **seed base** is `261101|alias`. Every hash below is sha256 of the seed string followed by `|` and
the named parts, ascending. **Where a numeric RNG seed is needed** (the permutation, bootstrap and power
draws), it is the **first 8 bytes of sha256(seed string)**, read big-endian as an unsigned integer
(owner, 2026-11-01; `alias_study_common.seed_int`).

| draw | seed string | what it orders |
|---|---|---|
| **neighbour selection** | `261101\|alias\|neighbours` | tie-breaks within the reference and citer caps (`…\|tie\|<refs or citers>\|<study paper>\|<work id>`) and the attribution order of shared neighbours (`…\|order\|<study paper>`), §5.1 |
| **random draw 1** | `261101\|alias\|random-1-v2` | the candidate order of random-1 (`…\|<study paper>\|<work id>`), §5.1 (owner, 2026-11-01, with the matching rule; replaces `…\|random\|1`) |
| **random draw 2** | `261101\|alias\|random-2-v2` | the candidate order of random-2, §5.1 (replaces `…\|random\|2`) |
| **noise repeats** | `261101\|alias\|noise` | the request order of each repeat leg (`…\|<corpus arm>`), so a repeat is not a replay of the first leg's request sequence, §9.4 |
| **bootstrap** | `261101\|alias\|boot` | every paper-cluster bootstrap: the C gate's CI (§3.1), the CIs beside Layer-1 members (§7), every Layer-2 CI (§3.3) and the bootstrap-t re-test of H1 and H2 (§7, R22) |
| permutation | `261101\|alias\|perm` | any Layer-1 test (sign-flip or randomization) where Monte Carlo is used, and the Freedman–Lane covariate re-test of H2 (§7, R23) |
| power | `261101\|alias\|power` | the committed power simulation, §8 (owner, 2026-11-01) |
| judge order | `261101\|alias\|judge\|order` | the labelling order of deduplicated pairs, §6.2 |
| drift sample | `261101\|alias\|drift` | the per-batch drift sample (`…\|<batch>`), §6.3 |
| record spot check | `261101\|alias\|spot\|records` | the 40 records of §13.4 (`…\|<record id>`; P3) |
| judged-pair spot check | `261101\|alias\|spot\|judge` | the 100 pairs of §13.5 (`…\|<label stratum>\|<arm>\|<pair>`; R15) and the sheet order (`…\|sheet\|<pair>`; P4) |
| placebo glossary | `261101\|alias\|placebo` | the derangement of entry lists across papers (`…\|<paper>`), §5.3 (R7) |

### 5.6 Pre-freeze pilot on the 191 held-out records (R8)

**What.** The 191 records on the 108 unsatisfied papers (§4.1), which enter no test and no Layer-2
decision, are run **before the freeze** in all 29 legs, and their pairs are judged by the composed rule
of §6.3 with the drift reference, on demand as in §6.2. It runs **after** the expanded collections are
loaded and censused (§15 item 5) and after the code of §10 is merged, so it also exercises the chain on
real collections. No balanced-set record is retrieved or judged by it.

**What it estimates** (each with its n and a 95 % interval, committed as counts before the freeze):
discordance between the two cells of each Layer-1 contrast (G and C separately for the glossary
contrast; for H2, against random-1, against random-2, against either and against both); the share of
judged pairs labelled *answers*, per arm; the escalated, tiebroken and unjudged shares; the Opus-filtered
share of the drift sample, per arm; and the judge-pair counts per judging stage plus the registry total
(for the judge caps, §14 O7). **Intervals** (P5): a paper-cluster percentile bootstrap (seed
`261101|alias|boot`) for the discordances, a Wilson interval for the pair shares.

**The pilot stays blind to outcomes (owner, 2026-11-03, R28).** In pilot mode **no found@10,
found@10-gold or RR@10** of any leg is printed, written to a public file, or derivable from the pilot
report; the estimates above are the only figures it reports, and pair counts are reported per judging
stage and as the registry total, never per leg. The harness's stage-A commit step, which printed per-leg
found@10, must not do so in pilot mode (as implemented in the judge's pilot mode, R28 / #923). *Why:* a
leg's on-demand judge-pair demand equals 10 × (rows − failed − found@10-gold), so per-leg pair counts
would reveal the gold-only H1 and H2 changes. A baseline C found@10 (base × default × none), with the C
discordance under merged vs none, would bound the direction of the pilot's C change: gains are at most the
headroom and losses at most the baseline hits, and C sits near the ceiling (§2 item 4). So the pilot does
not report it; the main run prints the baseline C found@10 beside the C gate anyway (§3.4). The
discordances carry no level, so they are direction-free; none of the listed estimates, alone or
combined, bounds the direction of any contrast.

**Its limits, stated now.** The 191 sit on papers the stop rule did not satisfy (83 G, 108 C), so they are
not a sample of the balanced set. Their own papers' neighbours are **not** in the neighbour arm (§5.1
adds neighbours of the 300 satisfied papers only), so their neighbour-vs-random discordance measures only
other papers' neighbours and is expected to understate H2's. Discordance estimated from a small pilot is
itself uncertain, and power computed from it can be badly off (Webber, Moffat and Zobel 2008,
*Statistical power in retrieval experimentation*, CIKM 2008, 571–580, doi:10.1145/1458082.1458158): the
planning scenarios of §8 stay as they are, and the pilot's estimate is added as a row, never substituted.

**Allowed consequences, fixed now.** On the pilot's counts, before the freeze, and recorded in this text
with the counts they rest on: (a) add the pilot-estimate rows to §8's power tables; (b) set the judge
gateway caps and the Argon budget (§14 O7) and the batch sizes; (c) set operational limits (timeouts,
in-flight counts) that do not change any label or hit. **Not allowed:** changing a hypothesis, a family,
a level, a gate, a Layer-2 threshold or rule, a cell, a record, a judge or its prompt, a seed or an
analysis — **unless** the change is recorded in this text before the freeze as a **deviation before
freeze**, with its reason and the pilot counts that were seen, and listed in the README's change log. The
pilot's labels are **not** carried into the main run: after the freeze the 191 are judged again with the
main run's labels (§6.2).

**Disclosure.** The pilot's counts and the date it ran are entered in *What had been seen before writing*
before the freeze, and the freeze commit's text includes them.

**Done** (2026-11-04): all 29 legs, blind to outcomes; the public copy of the report is
`artifacts/pilot-report.json`; its consequences are R29 and R30.

### 5.7 After the freeze: two stages (R9)

Each stage has a **retrieval** part and a **judging** part, and since R25 they differ: the stage a leg
is retrieved in is not always the stage its pairs are judged in. A **Layer-1 attachment** is one of the
five corpus × glossary conditions the Layer-1 members read: base × none, base × merged, neighbour × none,
random-1 × none, random-2 × none.

- **Stage A retrieval** (R20, R25; owner, 2026-11-03): each of the 5 Layer-1 attachments under all three
  retrievers (default, norerank, BM25: 15 cells), plus the noise repeat of each corpus arm (default ×
  none, §9.4): **19 legs**, 20,729 retrieval calls. The 5 norerank cells are the ones the
  inactive-reranker gate reads (§9.1): base × norerank × none, base × norerank × merged, neighbour ×
  norerank × none, random-1 × norerank × none, random-2 × norerank × none. An attachment's three
  retriever legs are retrieved **in one run**, against its own collection, which no stage writes to
(R26, §9.3).
- **Stage A judging**: the found@10 pairs (§6.2) of the 5 default and 5 norerank cells and the 4 noise
  repeats: **14 legs**. The BM25 legs retrieved in stage A are **not** judged in stage A.
- **The stage-A commit**: the stage-A outputs (hits, pair labels, logs, as private files with public
  sha256s) are **committed before stage B starts**. It pins the BM25 rows retrieved in stage A as
  well; their labels arrive in stage B.
- **Stage B retrieval**: the remaining 9 cells and the placebo leg (10 legs). **Stage B judging**: the
  found@10 pairs of the 5 BM25 cells retrieved in stage A and of the stage-B legs, then the pairs that
  RR@10 needs (§6.2). No descriptive split needs more labels than these.
- *Why R20:* the inactive-reranker gate is a Layer-1 gate (§9.1), but its norerank cells were in stage B,
  which contradicted "the Layer-1 analysis can be run after stage A". Now every Layer-1 gate is read
  after stage A alone. The cost: 5 × 1,091 retrieval calls and their found@10 judge pairs move earlier;
  the total is unchanged.
- *Why R25:* each attachment is retrieved in **one run**, so its outputs are not split across the two
  stages' files, and the inactive-reranker gate compares legs from that one run. (Under R26 there is no
  swap at all, since each variant is its own collection.) The cost: 5 × 1,091 BM25 calls move earlier; no extra judging comes before
  the Layer-1 analysis; the total is unchanged.
- **A stage-A label is never changed by stage B.** Pairs are deduplicated across cells (§6.2), so a pair
  that stage-B judging meets again keeps its stage-A label; stage-B judging labels only new pairs. Stage
  B cannot move a Layer-1 figure.
- No analysis is run until stage A is committed and both owner spot checks (§13.4, §13.5) are committed.
  The judged-pair spot check is drawn from stage A's labels (§13.5), so the Layer-1 analysis can be run
  after stage A without waiting for stage B.

## 6. Outcome and judge

### 6.1 The outcome

**found@10.** A record has a hit in a cell when the gold chunk is in the top 10, **or** any other top-10
chunk has the final judge label *answers* (§6.3). **found@10-gold** is printed beside it everywhere, and
is H2's direction gate. **RR@10** (Layer 2) uses the same labels.

### 6.2 What is judged, and blindness

- **Only top-10 non-gold chunks** are judged. The gold chunk is a hit by definition.
- **Judging on demand (R9).** For found@10, a non-gold pair (record, chunk) is judged **only if the
  record's gold chunk is outside that cell's top 10**: when the gold is in the top 10 the record is a hit
  whatever the other chunks are. Stage-A judging (§5.7) labels the pairs its 14 legs need this way,
  first (R20, R25). Stage-B judging labels the found@10 pairs of the 5 BM25 legs retrieved in stage A
  and of the legs retrieved in stage B, then the pairs that **RR@10** needs (non-gold chunks
  ranked **above** the gold in a cell where the gold is in the top 10, stage-A legs included). The
  descriptive splits of §3.3 and §7 (gold, same-paper and other-paper hits; alias kind) need no labels
  beyond those of found@10 and RR@10. A pair is labelled once, whatever first asked for it.
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
neighbour, random), each stratum capped at its size, is re-labelled by Opus. **Sample arithmetic
(owner, 2026-11-01):** a labelling batch is **2,000** deduplicated pairs (the last batch takes the
remainder); the sample size is 5 % of the batch **rounded half up**; it is split equally across the
three arms (the remainder to base, then neighbour); a stratum smaller than its share is taken whole, and
its **shortfall is reallocated** equally to the arms that still have pairs, until the sample size is
reached or every stratum is exhausted; within a stratum the first pairs in the order
`sha256(261101|alias|drift|<batch>|<pair>)` are taken. Agreement is computed on the
pairs where Opus answers. **Alarm, checked after every judging batch (as amended by R29):** the cumulative agreement between the
final label and Opus is computed and reported overall and per arm, with **0.97 overall** and **0.95 per
arm** as reported reference lines: a scope with at least 100 Opus-answered pairs that is below its line is flagged, and this never pauses labelling.
Labelling **pauses** only on the **arm gap**: when the largest and the smallest of the three arms'
agreements differ by **more than 0.05**. The gap is checked only when every arm has at least **100**
Opus-answered pairs (owner, 2026-11-01; §14 O4).
The pause is reported to the owner, the owner decides the remedy, and the remedy is recorded as a
deviation. Opus labels never replace a final label. The Opus-filtered share of the sample is printed per
arm. **Topic filter:** Opus via the gateway returns empty answers on some questions, clustered on vaccine
and virus topics: 33 of 410 calibration pairs and 81 of 587 validation pairs had no answer on any attempt
(83 validation pairs have no reference; the other 2 had no parseable verdict). The Claude CLI refuses
most of the same pairs, while Argon and both Qwen judges answer them. **An empty or unparseable Opus
reply is re-issued at most once (R4); if the second reply is also empty or unparseable, the pair is
*no-reference*** (filtered). A filtered pair is **not** a drift failure. It is excluded from the drift
agreement and counted, and no further call is made for it.

**Per-arm false-yes rates (D2).** For each arm: the share of Opus-*not* drift pairs that the final label
calls *answers*, and the share of owner-*not* spot-check pairs (§13.5) that the final label calls
*answers*, each with its n and a Wilson 95 % interval. A higher false-yes rate in the neighbour arm would
inflate its judged found@10 and work against H2; this is why the gold-only gate exists. A higher
false-yes rate in the random arms would work **for** H2; that is what the false-yes bound of §3.4
carries.

### 6.4 Why this judge: calibration facts (counts only)

These are the facts behind the owner's choice. Each is a calibration result with its commit. None is a
study result.

| fact | value | where |
|---|---|---|
| in-sample C + E agreement rule (410 pairs, 372 Opus-unanimous) | accuracy 0.992, 2.7 % escalated | JUDGE-CALIBRATION.md rounds 2–3 (#896 `65a58e1`, #897 `9dabeb4`) |
| **out-of-sample validation**, plan frozen first (`e2713e1`), run at `390777d` | **0.992** [0.979, 0.997] on 487 Opus-unanimous pairs; 2.5 % escalated; PASS against ≥ 0.98 and ≤ 5 %; 377 papers excluded, every paper of the 734-record build among them, but not every paper added by the extension to 300 (§4.3 item 6) | JUDGE-VALIDATION-PLAN.md; JUDGE-CALIBRATION.md *Validation* (#898) |
| **tiebreaker selection**, plan frozen first (`10a986c`) | `Gemini 4 Argon` **0.996** agreement with Opus on the 487 (κ 0.988; 0 of 83 filtered pairs unlabelled); `GPT-5.6 Sol` 0.990; Argon selected on agreement | TIEBREAKER-PLAN.md; JUDGE-CALIBRATION.md *Tiebreaker* (#899, `5e6a722`) |
| residual errors of the gate | 4 on 475 accepted pairs, all false yes: 3 on same-paper non-gold chunks (lex_same) and 1 in coll_same; 3 of the 4 on C records | JUDGE-CALIBRATION.md *Validation* |
| the composed rule (C + E, then Argon), replayed on the validation labels | 482 of 487 (0.990); 12 sent to Argon, 11 of them right; 0 unlabelled | post-selection; the committed replay (`alias_study_replay.py`, `artifacts/judge-rule-replay.json`) reproduces it exactly (§15 item 9) |

**What the calibration does not show.** Validation scored the C + E rule with **Opus** labelling the
escalated pairs. The composed rule with Argon was chosen after both results were seen, and its replay is
post-selection. The **coll_diff** stratum is decidable from the definitions block alone and is not
evidence of judgement. Every pair in both sets came from **base OA-1000**. Pairs from neighbour or random
papers are new ground: they have their own definitions blocks and possibly denser collisions. The
stratified drift reference and the owner's judged-pair spot check (§13.5) are what cover them. The
calibration and validation pairs are BM25-constructed candidates. They are not the retriever outputs
judged here. **The validation figure is not fully out of sample** with respect to the study papers
(§4.3 item 6; owner, 2026-11-04, §14 O10): the sensitivity without the 8 papers is printed beside H1, H1b
and H2 (§7).

### 6.5 Volume (planning only)

There are at most 10 non-gold chunks per record and leg, so the 31,639 lists of §5.4 (29 legs) give an
upper bound of **316,390** pair instances before deduplication. The deduplicated count is known only after retrieval;
it is far smaller, because most top-10 lists repeat across retrievers, glossary arms and the repeats,
and judging on demand (§6.2) skips the pairs of every record whose gold is in a cell's top 10. At
the validation throughput, judge C took about 1.85 s per pair and judge E about 0.74 s per pair (4 in
flight). The tiebreaker load is expected at about 2–2.5 % of pairs and Opus at 5 %. **Gateway caps for
the judge** are set before the freeze (§14 O7): Opus as below; Argon has no study cap below the registry size (`argon_share` 1.0, enforced in main mode; R30). **The Opus drift cap**
(owner, 2026-11-01) is **7 % of the judged (deduplicated) pairs**, and it counts **every Opus call,
re-issued calls included** (the cumulative calls log plus the planned selection may not exceed it). The budget stop and `PAUSE`
apply, as in the calibration harness.

### 6.6 Judge reporting (R4)

**Agreement metrics, with counts.** The judge is reported against its reference labels (Opus on the
validation set, and the owner on the spot check of §13.5) as a 2 × 2 table of counts (final label ×
reference label, *answers* / *not*), and from it: **precision, recall and F1 for *answers***,
**specificity** (the *not* class's recall), **Cohen's κ** and **Gwet's AC1** (Gwet 2008, *Computing
inter-rater reliability and its variance in the presence of high agreement*, British Journal of
Mathematical and Statistical Psychology 61(1):29–48, doi:10.1348/000711006X126600), each with its n. AC1
is printed beside κ because κ is depressed when one class dominates even at high raw agreement (Zec,
Soriani, Comoretto and Baldi 2017, *High Agreement and High Prevalence: The Paradox of Cohen's Kappa*,
The Open Nursing Journal 11:211–218, doi:10.2174/1874434601711010211), and *not* dominates these pairs.
**The validation-set figures** (JUDGE-CALIBRATION.md, review follow-ups (#910, exploratory); code at
`8f4043b`, clean; the 487 Opus-unanimous validation pairs, Opus *answers* on 109, prevalence 0.224; a
missing label counts as *not*; Wilson 95 % intervals):

| judge | TP / FP / FN / TN | precision | recall | specificity | F1 | κ | AC1 |
|---|---|---|---|---|---|---|---|
| C + E, escalation → Opus | 109 / 4 / 0 / 374 | 0.965 [0.913, 0.986] | 1.000 [0.966, 1] | 0.989 | 0.982 | 0.977 | 0.987 |
| **C + E, escalation → Argon (the study's rule)** | 109 / 5 / 0 / 373 | 0.956 [0.901, 0.981] | 1.000 [0.966, 1] | 0.987 | 0.978 | 0.971 | 0.984 |
| C alone | 107 / 10 / 2 / 368 | 0.915 [0.850, 0.953] | 0.982 [0.936, 0.995] | 0.974 | 0.947 | 0.931 | 0.962 |
| E alone | 106 / 5 / 3 / 373 | 0.955 [0.899, 0.981] | 0.972 [0.922, 0.991] | 0.987 | 0.964 | 0.953 | 0.975 |
| Argon alone | 109 / 2 / 0 / 376 | 0.982 [0.937, 0.995] | 1.000 [0.966, 1] | 0.995 | 0.991 | 0.988 | 0.994 |

Every error of the study's rule is a false yes. Its false-yes rate on non-gold pairs is 5 / 378 = 1.32 %
[0.57, 3.06]. Without the 243 pairs that touch a study paper (§4.3 item 6) it has precision 0.955 and
recall 1.000 on 287 pairs, and a false-yes rate of 4 / 203 = 1.97 % [0.77, 4.96]. These figures were not
in a frozen plan and are labelled exploratory. The same metrics are computed by the committed analysis for the drift reference (per arm) and for
the owner spot check (per label stratum and weighted, §13.5).

**The false-yes bound on H2** is the rule of §3.4.

**Prompt-paraphrase check, before the freeze.** LLM relevance labels are known to vary unpredictably with
simple paraphrases of the prompt (Thomas, Spielman, Craswell and Mitra 2024, *Large Language Models can
Accurately Predict Searcher Preferences*, SIGIR 2024, 1930–1940, doi:10.1145/3626772.3657707). Before the
freeze, **three paraphrases** of `alias-judge/2` (same fields, same output format, wording changed only;
written and committed before any is run) are run by both primary judges on the validation pairs (never on
a study pair), and the composed C + E rule is scored under each against the Opus-unanimous labels.
Reported: accuracy, escalation share, the metrics above, and each paraphrase's label agreement with the
original prompt. **Consequence:** the study keeps `alias-judge/2`. If any paraphrase's accuracy falls
below the validation criterion (0.98) or its escalation share above 5 %, this is recorded here before the
freeze and stated in §13.3 as a measured prompt sensitivity; replacing the prompt is a deviation before
freeze with its own frozen plan, as TIEBREAKER-PLAN.md was.

**Result of the paraphrase check** (JUDGE-CALIBRATION.md, review follow-ups (#910, exploratory)). It ran
outside a frozen plan, on a 200-pair stratified subset of the Opus-unanimous validation pairs (100
*answers*, 100 *not*; seed `20261102|judge-followups|paraphrase`), not on all of them: three paraphrases
(`alias-judge/2-p1..p3`) and a fresh re-run of /2 as the run-to-run noise floor, local Qwen calls only.
The largest change against /2 in precision or recall, for the composed rule or either primary, was
**1 point** (one pair), equal to the re-run's own change. No paraphrase took accuracy below 0.98 or the
escalation share above 5 % (at most 6 of 200 escalated, against 7 for /2). The owner accepted it as the
check of R4 (owner, 2026-11-04); the study keeps `alias-judge/2`.

**Correlated primaries (known limitation).** Judges C and E are both Qwen models. Errors they share give
the same label twice, so the pair is never escalated, and **correlated errors do not reach the
tiebreaker**. The composed rule's error rate is only as low as the two models' errors are independent.
This is stated in §13.3. It is mitigated, not removed, by the drift reference (a different model family
on a 5 % sample) and by the owner spot check, which oversamples *answers* labels, the direction of every
validation error (§13.5).

## 7. Analysis

Round 3 §7 machinery (`chunktrial_stats`), with these specifications:

- **Tests (Layer 1 only; decided, owner, 2026-11-02).** Each member is tested by the exact test
  whose null is that member's null. All three are one-sided and computed **exactly** (enumeration or
  convolution on the scores' lattice, ties counted in the tail); **only if** the lattice is infeasible
  (scores off a lattice of step 1/12 or finer, or half-width above 1,000,000) is the p Monte Carlo,
  10,000 draws, seed `261101|alias|perm`, with the corrected p = (b + 1) / (m + 1). The mid-p is printed
  as a sensitivity and never decides.

  | member | test | unit and relabelling | tested null, and why it matches |
  |---|---|---|---|
  | **H1** | exact conditional paper sign-flip (`chunktrial_stats.sign_flip_test_exact`) | paper; the sign of each non-zero paper score S_j (2^k vectors) | the **sharp null per question** of no glossary effect: H1 compares two conditions (merged, none), so swapping them negates each question's difference, each S_j is symmetric about 0 and its sign is a fair coin given \|S_j\| |
  | **H1b** | exact G / C label randomization (`chunktrial_stats.cluster_randomization_test`) | paper; the G / C labels permuted over the paper's balanced records (2 G + 1 C: 3 arrangements, equally likely; 2 for a paper left with 1 G + 1 C) | **label exchangeability**: the glossary effect does not depend on whether a record is G or C. The statistic is a 2 : 1 difference of differences, which a relabelling does not negate, so a sign flip is not exact for this null. **Deciding scenario: S3c** (G and C gaining alike, skewed and sparse, q 2 %), under which a scratch simulation in the #907 review found the sign flip anti-conservative for H1b and the label randomization within level |
  | **H2** | exact conditional paper sign-flip (`chunktrial_stats.sign_flip_test_exact`) | paper; the sign of each non-zero paper score S_j = Σ (neighbour − mean(random-1, random-2)) | the **symmetric mean-difference null**: each paper's score is symmetric about 0, so the neighbour arm does not shift found@10 in either direction. H2 claims a **mean drop**, and this null allows the neighbour arm to differ from the draws on more questions without a direction. **Deciding scenario: S8** (a neighbour arm that scatters more, with no mean shift), under which the same scratch simulation found the arm-label randomization test above level (exchangeability counts extra scatter as an effect) and the sign flip within level. The gold-only direction gate and the draw-gap flag are unchanged |

  Under its null each p is **exactly level-α**: P(p ≤ α) ≤ α, with equality whenever α is an
  attainable value of the discrete null distribution. **What a rejection says.** A rejection of H1 is
  evidence against the sharp null; of H2, against a symmetric per-paper difference, read in the
  claimed direction and confirmed only with the gold-only gate; of H1b, against G / C exchangeability.
  **The weak null (corrected by R22).** For H1's and H2's one-sample sign-flip the permutation variance
  Σ S_j² estimates Σ E[S_j²] ≥ Var(Σ S_j), so both are already asymptotically valid, and conservative,
  under the weak null of zero mean effect. What they lack is a finite-sample correction for a skewed
  per-paper distribution; the bootstrap-t re-test below supplies it. H1b's label randomization tests
  exchangeability, and nothing here addresses a weaker null for it.
- **Minimum attainable p** (owner, 2026-11-01) is 2^−k for H1's and H2's exact p. For H1b (owner,
  2026-11-02) it is the null mass of the most extreme value: the product, over the units whose
  relabellings change the statistic, of each unit's share of relabellings at its extreme. For any
  Monte-Carlo p it is max(that floor, 1/(m + 1)). It is judged against the level at which the member is
  tested, which is **0.05 for all three** (R1).
- **H1b with a missing record** (owner, 2026-11-01: as implemented in `alias_study_stats.h1b_values`):
  a record excluded under *Missing data* (below) drops out of its paper's statistic. A paper's H1b score
  is **the mean of Δ over its remaining G records minus its C record's Δ**
  (with both G records present this is ½ Σ_G Δ − Δ_C, as in §3.1). **A paper whose C record, or both of
  whose G records, are excluded is dropped** from H1b, and it is counted with the missing records.
- **Multiplicity:** the two families of §3.1 (R1): Family G, H1 then H1b in fixed sequence at 0.05;
  Family N, H2 at 0.05. Gated or absent members at p = 1.0.
- **Beside every Layer-1 member:** the 95 % paper-bootstrap percentile CI of the net statistic (10,000
  resamples); the gained / lost record counts; the number of non-zero clusters; the minimum attainable p;
  the level the member was tested at; the realised power (§8); `mde_split`; found@10-gold; the items of
  §3.4.
- **Layer 2:** point estimates in points with 95 % paper-bootstrap percentile CIs, and the decision rules
  of §3.3 applied mechanically by the committed script.
- **Declared sensitivities**, printed beside H1, H1b and H2 and never deciding: without the 47
  Codex-verdict records; without the 2 calibration-overlap records; without the 13 satisfied papers that
  were pilot papers; without the 8 satisfied papers that carry validation records (§4.3 item 6); unjudged pairs as *answering*; tiebreaker-labelled pairs set to each primary judge's
  own label in turn; H2 against random-1 alone and random-2 alone; **H2 with a chunk-volume covariate**
  (owner, 2026-11-01): the H2 contrast adjusted for the difference in added chunks between the neighbour
  arm and the random average, per study paper, reported as a sensitivity that never decides. It is the
  pre-named cover for the residual chunk-volume gap of §5.1 (R19), and one of H2's re-tests for
  *fragile* (R14, below); its test is the Freedman–Lane re-test of R23 (below).

- **Fixed sensitivities of R6**, displayed in a fixed table beside H1 and H2 whatever the verdict, never
  deciding:
  - **Bootstrap-t, H1 and H2 (R22; owner, 2026-11-03):** a **one-sided paper-cluster bootstrap-t** of the
    mean paper score (`alias_study_stats.bootstrap_t`): t = mean / se, se = sd / √P over all P papers,
    zero scores included; B resamples of the papers with replacement (B = the bootstrap's 10,000, seed
    `261101|alias|boot`) give t*_b = (mean*_b − mean) / se*_b; and **p = (#{t* ≥ t} + 1) / (B + 1)** for
    H1's gain, mirrored (#{t* ≤ t}) for H2's drop, the comparison carrying a relative tolerance of 10⁻⁹.
    **se* = 0** (a resample whose paper scores are all equal; `_safe_ratio`): t* is +∞ or −∞ by the sign
    of mean* − mean, and 0 when mean* = mean; the observed t follows the same rule. With fewer than 2
    papers there is no p. It **replaces the studentized
    randomization test** of v2. *Why:* with one score per paper, Σ S_j² does not change under sign
    flips, so the studentized statistic is strictly increasing in the sum and its exact p equals the
    primary's; it could never flip a verdict, so it was a vacuous re-test. The bootstrap-t adds a
    finite-sample correction for skew (above). In the #913 reviewer's scratch simulation (k = 300,
    2,000 replications) its type-I rate was .056 under a skewed mean-zero null, and it disagreed with the
    primary in 7–8 % of studies at power .55–.75.
  - **Topic-level clustering, H1 and H2:** the same tests with the cluster raised from the paper to the
    study paper's **OpenAlex primary topic** (papers sharing a topic pooled into one cluster score),
    against correlation between papers of one field.
  - **H2 with the chunk-volume covariate** (above), **tested by a Freedman–Lane sign-flip** (R23; owner,
    2026-11-03; closes §14 O13). Per study paper, x_j is the added-chunk difference (neighbour arm minus
    the random average) and S_j the H2 paper score. The restricted model S_j = γ x_j is fitted; its
    residuals are sign-flipped (10,000 flips, seed `261101|alias|perm`), added back to γ̂ x_j, and the
    full model S_j = α + β x_j is refitted on each; the statistic is the **studentized intercept**
    t = α̂ / se(α̂), and the one-sided p (in H2's direction, α < 0) is the Monte Carlo
    (b + 1) / (m + 1). The re-test reuses the primary's **unadjusted gold-only direction gate**. *Why:* a
    fixed-slope adjustment ignores that the slope is estimated; in the #913 reviewer's scratch
    simulation its type-I rate was .067 / .148 / .263 at x̄ / s_x = 0.5 / 1 / 2, against .035–.047 for
    Freedman–Lane. At the realised v4 gap, x̄ / s_x = 0.216 (per-study-paper added chunks, neighbour arm
    minus the random average, over the 298 study papers with added papers; private
    `per-study-chunks-v4.json`), both hold.
  - **H2 on the XML-only stratum:** H2 recomputed on the study papers whose attributed neighbours and
    whose papers in both random draws are **all** JATS-sourced (§5.1), so that no PDF extraction enters
    the contrast; the stratum's size is printed.
  - **H2 by citation-count tercile and by text-similarity tercile:** the study papers split into terciles
    by their OpenAlex citation count at the query date (§5.1), and separately by the mean dense-embedding
    cosine similarity between the study paper's chunks and its attributed neighbours' chunks (the served
    embedder, computed offline from the stored vectors before any outcome is read); the tercile cut points
    are fixed and committed before the stage-A outputs are read. Each tercile's H2 estimate and CI.
  - **Origin of displacing chunks (descriptive):** for each record that is a hit in the random average
    but not in the neighbour arm, the papers of the top-10 chunks that outrank its gold chunk, counted as
    the record's **own paper's neighbour**, **another study paper's neighbour** or **base**.

  **Fragile (R14; owner, 2026-11-03).** *Fragile* applies to **H1 and H2 only**, and only through these
  pre-named sensitivities, each of which re-tests the same claim on the same records at the member's
  level: for **H1**, the bootstrap-t and topic-level clustering; for **H2**, those two and the
  chunk-volume covariate's Freedman–Lane test (R22, R23). One of them **flips** the verdict when, at the member's level, it gives
  the other verdict (*confirmed* ↔ *not confirmed*; for H2 with the gold-only direction gate read as in
  the primary), and the verdict is then labelled ***fragile*** in every sentence that quotes it. Every
  other display in this section is reported and **never makes anything fragile**: the declared
  sensitivities (the record and paper exclusions, unjudged pairs as *answering*, the tiebreaker swaps, H2
  against one draw alone), the subgroup splits (the XML-only stratum, the terciles, the origin counts),
  and the alias-kind split of §3.3. **H1b is exempt**: it is already reported as underpowered at the
  planning scenarios (§8). *Fragile* never changes a label. *Why:* a subgroup has less power than the
  whole, so a subgroup that disagrees is expected by chance and is no evidence against the claim; a
  re-test of the same claim is.
- **Missing data:** a record whose retrieval call failed in any cell of a contrast is excluded from it
  and counted. `family_missing_as_not_found` is the one pre-named sensitivity. More than 2 % failed calls
  in a leg is a hard stop.
- Every analysis is the committed script. Scratch analyses are not citable.

## 8. Power and MDE

**Layout:** 300 clusters × (2 G + 1 C), realised and fixed. H1 reads 600 G records; H2 reads 900 records.
No layout uncertainty remains for the records. What is unknown is **discordance**: the share q of records
whose hit changes between the two cells of a contrast, and the share p of the discordant records that move
in the hypothesised direction.

**Every member is tested at one-sided α = 0.05 (R1)**, so every power below is at 0.05. The v1 tables at
the Holm worst case (0.025) and the conditional tables are withdrawn with the Holm pair.

**Normal approximation** (the committed calculation is `artifacts/power-v4.json`, §15 item 8). Each record
gets d ∈ {−1, 0, +1}: non-zero with probability q, in the hypothesised direction with probability p. Then

  μ = q (2p − 1),  σ² = q − μ²,  power = Φ( √n · μ / σ − z ),

with z = 1.64485 (one-sided α = 0.05). This treats records as independent and paper-level shares as
constant. A paper-level spread of p lowers power somewhat; the simulation adds it (below). H2's rows are
at the **planned dose** (about 2,800 added papers, §5.1), where q is defined, and at the **realised v4
dose** (2,423 attributed neighbours, dose 0.865; q scaled by it; `power-v4.json`, below).

| member (n), α = 0.05, normal approximation | q .02 | q .03 | q .05 | q .10 | q .20 |
|---|---|---|---|---|---|
| **H1**, p .85 (600) | .79 | .91 | **.99** | 1.00 | 1.00 |
| **H1**, p .70 (600) | .40 | .52 | .71 | **.93** | 1.00 |
| **H2**, p .85 (900), planned dose | .91 | .98 | 1.00 | 1.00 | 1.00 |
| **H2**, p .70 (900), planned dose | .52 | .67 | .85 | .99 | 1.00 |
| **H2**, p .85 (900), realised v4 dose | .87 | .96 | 1.00 | 1.00 | 1.00 |
| **H2**, p .70 (900), realised v4 dose | .47 | .62 | .81 | .97 | 1.00 |

**The two planning scenarios** are q 5 % / p .85 and q 10 % / p .70. **H1: power ≈ 0.99 and ≈ 0.93**
(normal approximation; the simulation below gives .99 and .92). The 80 %-power MDE, from
μ = k · √(q / (n + k²)) with k = 1.960 + 0.842, is 2.5 points for H1 at q 5 % and 2.1 points for H2 at
q 5 % (3.6 and 2.9 at q 10 %); it is also the equivalence bound δ of §3.3 at those discordances. For
the **300 C records** at q 5 % the same formula, read two-sided, gives **3.57 points**: the C bound of
§3.3's C cost rule (R12; `artifacts/c_cost_rule.json`).

**Simulation of each member's test, on the v4 counts.** The power script
(`python/scripts/eval/alias_study_power.py` at `617d28d`, clean tree, `citable true`) was run on the
accepted-record file (sha `09fc5ca4…`, balanced-set id sha `2be86f8c…`) and the v4 neighbour counts
(the private `neighbour-counts-v4.json`, sha256 `ed71fcb9…`: 2,423 attributed, mean 8.08 per study paper,
min 0, max 10; dose 0.865), 2,000 simulated studies per cell, between-paper SD of p .15, seed
`261101|alias|power`. Its output is the private `power-v4.json` (sha256 `5ea9d25c…`), committed as
`artifacts/power-v4.json`: a public copy of the private run at `617d28d` (clean, `citable true`), with
the host removed. `h2_caps_lever_fires` is false, so the caps are not raised. (The v2 draft's run on the v2 counts, 2,424 neighbours, gave the
same table to two decimals except one cell, .58 for .57.) H2 is at the realised dose, with **ρ = 1** (the draws' discordance fully shared, the
conservative three-valued case) and, in brackets, ρ = 0.5; *confirm* means the judged test rejects **and**
the gold-only gate passes, at agreement a between the gold-only and judged changes.

| member, α = 0.05, simulation | q .02 | q .03 | q .05 | q .10 | q .20 |
|---|---|---|---|---|---|
| **H1** sign-flip, p .85 | .74 | .92 | **.99** | 1.00 | 1.00 |
| **H1** sign-flip, p .70 | .28 | .44 | .64 | **.92** | 1.00 |
| **H1b** label randomization, p .85 (C with no effect) | .33 | .47 | .67 | .92 | 1.00 |
| **H1b** label randomization, p .70 (C with no effect) | .14 | .20 | .32 | .54 | .80 |
| **H2** confirm, **a = 0.7**, p .85 | .73 [.85] | .88 [.93] | **.96** [.98] | 1.00 [1.00] | 1.00 [1.00] |
| **H2** confirm, **a = 0.7**, p .70 | .34 [.44] | .45 [.59] | .65 [.78] | **.90** [.93] | .98 [.99] |
| **H2** confirm, a = 0.6 (sensitivity), p .85 | .57 [.67] | .70 [.76] | **.79** [.83] | .88 [.92] | .95 [.97] |
| **H2** confirm, a = 0.6 (sensitivity), p .70 | .26 [.36] | .36 [.47] | .53 [.64] | **.73** [.76] | .83 [.86] |

**H2 at the realised v4 dose, α = 0.05** (`power-v4.json`). Test alone / confirm at a = 0.7 / confirm
at a = 0.6:

| scenario | ρ = 1 | ρ = 0.5 |
|---|---|---|
| q 5 %, p .85 | .999 / **.958** / .794 | 1.000 / .980 / .829 |
| q 5 %, p .70 | .763 / .654 / .528 | .883 / .777 / .641 |
| q 10 %, p .70 | .971 / **.902** / .730 | .989 / .934 / .761 |

So at the two planning scenarios, ρ = 1, the power to confirm is **.96 and .90 at a = 0.7** and .79 and
.73 at a = 0.6. H1b is a fixed-sequence member, tested only after H1 is confirmed; its power is low at the
planning scenarios (.67 and .54) and is printed beside it as its floor.

**H2 is approximated conservatively at ρ = 1.** H2 compares the neighbour arm with the **average** of two
random draws, so d takes values in {−1, −½, 0, ½, 1}. Averaging two draws removes part of the random-arm
noise, which lowers σ for the same mean; ρ = 0.5 shows what that buys. The gold-only gate is a second
condition, so the power to *confirm* H2 is at most the test's power. Its size depends on how often the
judged and gold-only directions agree, which the simulation takes as the stated assumption a.

**Read this beside §2.** Round 3 saw **0 discordant records** between the served glossary and none under
the served path, and a 1 / 9 split in the adverse direction under mention attachment. If G discordance
here is as low, or the split as adverse, H1 ends *not confirmed* with this floor printed beside it. It is
not a null unless the power at the stated scenario is ≥ .80.

**The committed calculation** (`artifacts/power-v4.json`, §15 item 8) runs on the realised layout and, for H2, on the realised
neighbour and random counts per study paper. It repeats the tables above by the normal approximation and
by simulation of each member's test of §7 (2,000 simulations; H1's sign-flip with 2,000 flips, H2's
sign-flip and H1b's randomization test exactly; a between-paper SD of p of .15, seed `261101|alias|power`, §5.5),
and it computes H2 with the two-draw average and the gold-only gate under stated agreement assumptions.
The pilot's discordance estimates (§5.6) are added as rows, never substituted for the planning scenarios.
**If H2's simulated power to confirm is below .80 at q 5 % / p .85, at α = 0.05, the caps of §5.1 are
raised before the freeze**; that is the lever, and it acts on metadata counts only. **The power the lever
reads is the power to *confirm* H2** (owner, 2026-11-01): the judged test rejects **and** the gold-only
direction gate passes, not the test's power alone. **The lever reads the agreement level `a = 0.7`, and
`a = 0.6` is reported as a sensitivity** (owner, 2026-11-02, §14 O11;
`alias_study_power.LEVER_AGREEMENT`). On the v4 counts the lever does **not** fire at a = 0.7 (.958);
at the a = 0.6 sensitivity it would (.794). **The script reads the lever at α = 0.05**
(`alias_study_power.LEVER_ALPHA`, changed from the v1 Holm level 0.025 in `e3b9695`; the
0.025 row stays in its output for comparison, where the a = 0.7 figure is .95).

**Evaluability floor.** For H1 and H2, with k non-zero clusters, the minimum attainable one-sided p
is 2^−k, so at α = 0.05 a member needs k ≥ 5. For H1b the floor is the randomization floor of §7: each
paper whose relabellings change the statistic contributes 1/3 or 2/3 (2 G + 1 C), or 1/2 (a paper left
with 1 G + 1 C).

## 9. Gates, stops, and collections written once

### 9.1 GATE-NOT-EVALUABLE (a third answer, preserved in every table)

A Layer-1 member is GATE-NOT-EVALUABLE when:
- its minimum attainable p exceeds its level (too few non-zero clusters);
- its cells have **fewer than 8 clusters** with records (`MIN_CLUSTERS`);
- any of its cells failed the depth read-back without an `exhausted_verified` verdict (§9.2);
- the **inactive-reranker gate** fired: default's top 10 equals norerank's on every record in that corpus ×
  glossary condition (its norerank cells are run in stage A, R20, so the gate is read after stage A
  alone, as every gate in this list is);
- a census of any of its cells failed (§9.2) and was not repaired and re-run;
- (H1, and so H1b) **the C gate** fired (§3.1);
- (H1, and so H1b) **the reach gate** failed: fewer than **80 %** of the 600 G records (**480**) have a gold
  chunk that renders the record's own entry under the caps, on base. The count is read offline before the
  freeze (§15) and quoted beside H1.

A GATE-NOT-EVALUABLE member enters the multiplicity procedure at p = 1.0. **Absent** is written *absent*,
never left blank. Layer 2 has no gates (§3.3); a gate verdict that would apply is printed beside the figure.

### 9.2 Checks per leg (a leg is one cell run over all records)

- **Census before and after:** the chunk count of the collection equals the corpus arm's manifest (base
  32,765; the expanded arms as built) and its load receipt's `n_chunks` (§9.3). The `metadata.symbol_glossary_version` stamp census equals the
  glossary arm's file stamp on every chunk; for *none*, the *none* file's stamp with an empty field
  (§5.3). Every gold chunk id is present, with the same content hash, in all four corpus arms.
- **Admin config hash:** `/v1/config` is read at the start and the end of the leg. The two reads must be
  identical, as in round 3 §5 note 2(c). **An admin credential is required** (owner, 2026-11-01): if
  `/v1/config` is not readable, the leg is refused and nothing is measured. A difference stops the leg, and the leg is re-run after the cause
  is found and recorded.
- **Depth read-back:** the served depth is checked against the requested depth on every request.
- **BM25 exhaustion verifier** (#877, `bm25_exhaustion.py`, round 3 §5 note 3, unchanged): a short `bm25`
  list costs one read-only `_count` on the leg's bench index, on the exact tenant scope. The verdicts
  are `exhausted_verified` (scored, not gated), `clamp` (hard stop), `query_mismatch` (hard stop) and
  `under_served` (gated).
- **Glossary boost:** the round-3 behavioural probe rule (found10's boost probe: *active*, *inactive* or
  *mixed*; it proves a boost greater than 0, not its value; *inactive* or *mixed* is a hard stop, below). Where the probe is structurally
  `not_testable`, as on the *none*-arm collections, the boost is declared from the serving release and the
  label `declared` is printed, never `pass`. The reranker's glossary field is declared only; the
  admin-config hash pins neither setting (§5.2, P1).
- **Hard stops:** more than 2 % failed calls; a `clamp` or `query_mismatch` verdict; a config-hash
  difference; a boost probe reading *inactive* or *mixed* (found10 refuses the leg and measures nothing); a served judge model id that is not the requested one; a judge budget stop; a drift alarm
  (§6.3); a frozen-prereg sha mismatch in any stage that reads `prereg.file`.

### 9.3 Collections written once (R26)

Every store write is made on a **bench collection**, never on a live collection. The store writes are the
loads of the 9 bench collections of §5.3 (R26): each corpus's embedding shards loaded once per variant,
with that variant's glossary file. Each collection is **written exactly once**: nothing writes to it
again, and no glossary is swapped in place. Each has a **load receipt** with provenance, which supplies
its chunk count (`n_chunks`), the glossary file's sha256 and the file's versions. **At the start and the
end of every leg** the live census (§9.2) checks the collection's chunk total against the receipt's
`n_chunks`, and every chunk's stamp against the arm's stamp and the file's versions; a mismatch stops the
leg. The receipt holds no stamp census of its own. Each collection also has a **registry entry**, which
the management-approved load route guarantees; the harness does not machine-check it per leg. The loads run under the
approved path with management (§5.3); bulk writes go one index at a time, with the 85 % heap stop. No
restore-to-state step exists, because no leg changes a collection.

### 9.4 Noise floor (a determinism check, not power)

One cell per corpus arm (default × none) is run **twice**. The run-to-run discordance is printed beside
every figure from that arm. Round 1's floor was 0–2 records, and round 3's repeat was 0 discordant. No
result is re-run to move the floor.

## 10. Code, pin, provenance

**Exists on `main` (at or before `323d470`):** the record pipeline (§4.1); the judge harness
`glossbench_alias_judge.py` with the Qwen, gateway and Opus backends, the served-model check,
`analyse-gate` and `analyse-tiebreaker` (#894–#899); `bm25_exhaustion.py` (#877); the round-3 runner
and analysis (`found10.py`, `found10_analysis.py`, `chunktrial_stats`), the backfill (unused under R26) and the stamp
census.

**Must be written, reviewed and merged before the freeze (§15):** a recount script for the balanced set
and the verdict-source split (§4.1); the OpenAlex neighbour and random selectors (metadata only, seeded,
writing the manifests of §5.1, with the v3 carry-over re-draw of R16, the nearest-band fill of R17 and
the repetition screen of R18); an alias adapter for the runner (the records schema; the 24 cells and 4
repeats; the 9 collections of R26); the pair builder and the deduplication for judged pairs; the
composed-rule labeller (C + E, then Argon), with the arm-stratified drift sampler and both alarms, the
Opus single retry (§6.3) and the on-demand pair selection by stage (§6.2, §5.7); the statistics for H1,
H1b (fixed sequence within Family G) and H2 (two-draw average, gold-only gate), with tests (v1's Option-B Holm procedure and its drop-gated sensitivity were removed in #913) and the fixed
sensitivities of §7 (the bootstrap-t of R22, topic clustering, the chunk-volume covariate with its
Freedman–Lane test of R23, XML-only stratum, terciles, origin counts, the *fragile* rule of R14); the false-yes bound of
§3.4 with U = max(U_r, U_p) (R13) and the judge metrics of §6.6; the Layer-2 script (CIs, the decision
rules of §3.3 with the harm rule, the C cost rule of R12 and the reranker non-inferiority rule, the Layer-1
wording table and the equivalence bound, RR@10, H3); the writer of the `ragstack.glossary_file/v1`
files (merged, placebo) that `load-embeddings` renders, with render-parity tests against this text's
mention rule and caps (§5.3), and the offline reach count; the per-leg census check against each
collection's load receipt (§9.3);
the committed power calculation (§8, the lever at α = 0.05); the public-artifact check (§11).

**Pin.** Every run is from one `main` commit, at or after the merge of the last item above, in a clean
worktree (`dirty false`, `untracked 0`), or from a tools image built from a tag at or after it (ADR-0010).
Every output embeds `experiment_provenance()`. A dirty run is not citable.

## 11. Data provenance, privacy, and what is released

- **Private, never in this repository:** the records (questions, answers, quotes), the chunk exports,
  the glossaries, the paper lists, the neighbour and random manifests with ids, the judged pairs and their
  labels, the spot-check sheets and keys, and the per-record hits.
- **Released (public, counts only):** this prereg; `RESULTS.md`; `DEVIATIONS.md`; `artifacts/*.json`
  holding counts, rates, CIs, p values, model ids, version strings, commit shas and sha256 hashes of the
  private files; per-paper quantities by paper rank only; and the `experiment_provenance()` block
  **without its `host` and `warnings` fields** (P6): the statistics and the pilot drop them, and the
  committed check (`alias_study_common.public_problems`) refuses any host field, as it refuses any public
  artifact that carries record text or ids, in the style of round 3's `string_values` and `--omit-text`.
  The two planning artifacts beside this text (`artifacts/c_cost_rule.json`,
  `artifacts/spot_check_detection.json`) are regenerated without host or warnings, still `citable true`.
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
   is conditional on them. Abbreviations dominate: 508 of 600 G records. **Every claim is for questions
   using the glossary's primary term** (§3, R3): no record asks with the alias or in other words. The
   records are **LLM-drafted and LLM-verified, plus the owner's check of 40** (§13.4); none was written
   by a person.
3. **The judge is not truth.** found@10 inherits the judge's errors. On the validation set those errors
   were few (§6.4) but were all false yeses, 3 of 4 on same-paper non-gold chunks. They inflate found@10
   equally across arms only if such chunks are retrieved equally often, which is why false-yes rates are
   printed per arm, H2 carries the gold-only gate, and the false-yes bound of §3.4 is quoted beside H2.
   **Both primary judges are Qwen models** (§6.6): errors they share are never escalated, so correlated
   errors do not reach the tiebreaker. The drift reference (Opus, 5 %) and the owner spot check, which
   oversamples *answers* labels (§13.5), mitigate this; they do not remove it. The prompt-paraphrase check
   (§6.6; exploratory, owner-accepted) moved precision or recall by at most 1 point, the size of the
   re-run noise, so no measured prompt sensitivity is stated.
4. **Record validity (owner spot check, D9).** The owner labels **40** records drawn from the 900 balanced
   records, valid / invalid: the first 40 in ascending order of sha256 of
   `261101|alias|spot|records|<record id>`, not stratified by kind (P3). The sheet reveals no kind (G / C),
   arm, paper id or record id; it shows the paper's title, and the passage inevitably shows the short or
   the spelled-out form. **Rule, as round 3's:** validity below **0.85** at the point estimate makes every result
   sentence carry *validity below threshold*. The Wilson 95 % interval is printed and decides nothing. The
   set is not rebuilt. The labels are committed (as counts, with the sheet's sha) **before any analysis**.
   The verifier's spot-reads found about 1 in 12 current-pipeline G records doubtful (VERIFIER.md).
5. **Judge validity (owner spot check, D9 as amended by R5 and R15).** The owner labels **100** judged
   pairs (R15; owner, 2026-11-03; about 1.5 h of owner time) drawn from the non-gold pairs labelled in
   **stage A** (§5.7), **stratified by the final judge label**. R5's 30 / 20 / 10 split is scaled to 100,
   and within a stratum the remainder goes to base, then neighbour. The **pool** is every pair labelled in
   stage A, including the pairs of the 191 descriptive records and of the norerank and noise-repeat legs
   (P4); the re-weighting below is to that pool:

   | stratum | pairs | per arm (base / neighbour / random) |
   |---|---|---|
   | *answers*, C and E agreed | **50** | 17 / 17 / 16 |
   | *not*, C and E agreed | **33** | 11 / 11 / 11 |
   | **tiebroken** (the Argon label is final, either verdict) | **17** | 6 / 6 / 5 |

   **Why this split.** Every residual error of the validation was a false *yes* (§6.4), a false *yes* is
   what inflates found@10, and *answers* labels are the minority of pairs, so a uniform draw would hold few
   of them: half the sheet goes to *answers*, which estimates the judge's precision. The *not* stratum
   (33) estimates false *no*s, which only lower found@10 and were not seen in validation. The tiebroken stratum (17) covers the pairs on which the two primaries
   disagreed, the hardest pairs and the only ones the tiebreaker decides; it is small because those pairs
   are about 2–2.5 % of all (§6.5). Within each stratum the pairs are spread across arms as above, so the
   neighbour and random papers, which calibration never saw (§6.4), carry two thirds of the sheet.
   **Shortfalls** (P4): a stratum with fewer pairs than its total passes its shortfall on in a fixed
   chain: *answers* → *not*; *not* → *answers*; tiebroken → *answers*, then *not*. The tiebroken stratum
   never receives, so the sheet can hold fewer than 100 pairs while tiebroken pairs remain. Each
   stratum's **realised total is re-split equally across the arms** (remainder to base, then neighbour);
   an arm × stratum cell with fewer pairs than its share is taken whole and the rest goes to the other
   arms of that stratum. Within a cell the first pairs in the order sha256 of
   `261101|alias|spot|judge|<stratum>|<arm>|<pair>` are taken, and the sheet is ordered by sha256 of
   `261101|alias|spot|judge|sheet|<pair>`. The sheet is **blind to arm and to the judge's verdict** (the
   stratum is not on it).

   **What 100 pairs detect** (exact binomial at the nominal stratum sizes,
   `artifacts/spot_check_detection.json`). For a judge whose true error rate on *answers* pairs is 10 %,
   the threshold itself, the *answers*-stratum rule below fires with probability **0.38** at 50 pairs
   (0.35 at R5's 30). At a true error rate of 15 % it fires with probability 0.78 (0.68 at 30), at 20 %
   with 0.95 (0.88). Read alone at the same < 0.90 cut, the *not* stratum (33) would flag a 10 % error
   rate with probability 0.42 (0.75 at 15 %, 0.92 at 20 %) and the tiebroken stratum (17) with 0.52
   (0.75, 0.88); the rule reads neither alone. At these sizes a judge whose error rate sits exactly at the
   threshold is caught on the *answers* stratum less often than not (0.35 at 30, 0.38 at 50); that is not
   a property of every n (at 17 pairs, as above, it is 0.52, because 0.90 × 17 is not a whole count).
   What the larger sheet buys is better detection of error rates above the threshold, and narrower
   intervals. The sizes are nominal: the shortfall chain above can move pairs into *answers*, so the
   realised *answers* stratum can exceed 50.

   **Reported:** per-stratum owner–judge agreement with Wilson 95 % intervals, the §6.6 metrics, and the
   agreement **re-weighted to the stage-A label shares** (each stratum × arm cell weighted by its share of
   stage-A labels, because the arms are allocated equally rather than in proportion to their pairs), which
   is the estimate of the judge's overall agreement. **Rule:** a re-weighted owner–judge
   agreement below **0.90** at the point estimate, or an *answers*-stratum agreement below 0.90, is stated
   in every result sentence, and found@10-gold is reported alongside each found@10 result as the
   sensitivity. Nothing is re-labelled. The labels are committed privately **before any analysis**; the
   public record of the judged-pair spot check is the sha256 of the ingest JSONL and of the sheet, and the
   stage-A commit sha (P4).

   **Single rater.** The spot check has one rater, the owner, who also designed the study. That is a
   limitation: no inter-rater agreement for the reference exists. The owner decided on a single rater for
   this study; further raters are planned for a second wave (owner, 2026-11-04, §14 O12).
6. **Neighbour definition.** References and citers come from OpenAlex, whose coverage is incomplete, and
   open-access availability filters them. The caps take the most cited references and the most recent
   citers, so the added set is a sample of the neighbourhood, not the neighbourhood itself.
7. **Forking paths.** The corpus and retriever factors were chosen after round 3's null and the reranker
   test's papers-OC reading. H1's form and the 300-paper target were chosen after v0's power sketch. The
   record set's target was extended twice mid-build (§4.3). The judge's tiebreaker was chosen after its
   calibration results. Version 2's changes (R1–R11) were adopted from an independent methodology review
   before any retrieval on a study record. Version 3's corpus decisions (R16–R19) are **post hoc to the
   v3 build**: they were made after reading the v3 counts (per band, the chunk-volume gap, the re-draw and
   relaxation counts) and a scan of distinct-chunk ratios over the added papers, which was a scratch scan
   made by the PR #911 reviewer, not a committed artifact. The v4 build's 9 repetition-screen hits (7 of
   them JATS papers with one repeated table chunk, §5.1) were seen before the freeze, and so was the
   scratch count of 4 OA-1000 papers below 0.90; the rule was kept per paper, as written. None of this
   read retrieval, judge or outcome data. The pilot (§5.6) ran on held-out records before the freeze,
   blind to outcomes, and what it could change was fixed in advance. Each is bounded by being fixed here before any retrieval
   on a balanced-set record.
8. **No study-wide error control across the two families** (§3.1, R1). Family G and Family N are each
   controlled at 0.05; the chance of at least one false confirmation across both, with every null true,
   is up to about 0.0975.
9. **Judgement-based thresholds.** The Layer-2 thresholds and margins (§3.3) rest on the owner's judgement,
   not on a cost model. The −3.6-point C bound is the exception: it is the design's detection limit for C
   (R12).
10. **Small C costs are not resolvable** (R12). With one C record on each of 300 papers, a cost on
    term-bearing passages smaller than about 3.6 points cannot be ruled out at the discordance the design
    plans for (5 %), and every *attach* statement says so (§3.3).
11. **Residual chunk-volume gap** (R19). The random draws are matched to the neighbour arm on paper count,
    type, length band and source, but their total added chunks is a build target (± 5 %), not a gate, and
    the gap is printed per draw (§5.1). Its effect on H2 is read through the pre-named chunk-volume
    covariate sensitivity (§7), which can make H2 *fragile*; it is not removed by design.

## 14. Open decisions for the owner

Only items not settled by D1–D10 and R1–R30.

| # | decision | proposal in this draft |
|---|---|---|
| O1 | **Numeric definition of the C gate** | **Decided (owner, 2026-11-01): rule C.** Evaluated only when ΔG > 0 (H1 is one-sided; at ΔG ≤ 0 the gate is moot). H1 is GATE-NOT-EVALUABLE if ΔC (merged − none, base × default, 300 C) has the same sign as ΔG, \|ΔC\| ≥ ½ \|ΔG\| in points, and ΔC's 95 % paper-cluster bootstrap CI (seeded) excludes 0. A C drop never trips the gate (it feeds Layer 2's C cost rule, made CI-based at margin −1 point by R2 and replaced by R12: point estimate at −1 point, CI bound at −3.6 points). Baseline C found@10 printed beside it with a ceiling note. Written in §3.1, §3.2, §3.4 |
| O2 | Reading of "H1b at full α" | **Decided (owner, 2026-11-01): Option B.** H1 and H2 start at one-sided 0.025 each (Holm over 2); a confirmed member passes its 0.025 to the other. H1b is tested only if **both** H1 and H2 are confirmed, at α = 0.05 (fixed sequence after the Holm pair); otherwise it is not tested and is reported descriptively. H2's gold-only gate failing = H2 not confirmed. Written in §3.1, §3.2 and §8. **Superseded (owner, 2026-11-02/03, R1):** two families; H1 at 0.05 with H1b after it in fixed sequence (Family G), H2 alone at 0.05 (Family N); no level passes between H1 and H2 |
| O3 | H2 when the judged test is significant but the gold-only gate fails | **Decided (owner, 2026-11-02/03, with R1):** label *not confirmed (gold-only direction gate)*. With H2 alone in Family N there is no level to pass on. Written in §3.1, §3.2 |
| O4 | Minimum Opus-answered pairs before a drift alarm can fire | **Decided (owner, 2026-11-01).** 100 per scope (overall and per arm); as amended by R29, the 100-pair minimum applies per arm before the arm gap is checked, and per scope before a reference-line flag is shown; with it the drift-sample arithmetic: 5 % of each 2,000-pair batch rounded half up, a capped stratum's shortfall reallocated to the others. Written in §6.3 |
| O5 | Attribution of a neighbour shared by several study papers | **Decided (owner, 2026-11-01).** Added once; attributed to the first study paper in the seeded order (`261101\|alias\|neighbours`, `…\|order\|<study paper>`); no replacement for the others; each random draw takes per study paper as many papers as were attributed to it, so the arms hold equal unique counts. Written in §5.1 |
| O6 | How "prefer PMC/JATS" is applied | **Decided (owner, 2026-11-01).** PMC JATS XML for every added paper in both arms where it exists; the random arm's PDF share matched to the neighbour arm's; every added paper scanned for C0 control characters (other than tab, newline, carriage return), a PDF paper excluded when any occurs in > 0.5 % of its chunks; exclusions counted per arm. Written in §5.1 |
| O7 | Gateway caps for the judge | **Opus decided (owner, 2026-11-01):** ≤ 7 % of judged pairs, re-issued calls counted (§6.5). **Argon decided (owner, 2026-11-04, R30): no study cap.** The pilot measured 213 tiebreaks on 3,574 pairs (6.0 %; 8.1 % in stage A), above the 4 % drafted here; the judge config sets `argon_share` 1.0, which only stops a pathological run. The gateway budget (at least 10 M tokens a day) and the harness's per-run budget stop still apply |
| O8 | **Whether to add GPT-6.1 Sol later** as a second tiebreaker or drift reference | **Decided (owner, 2026-11-04): not for the main run.** GPT-6.1 Sol is now reachable through the gateway; it is a candidate in the second judge validation (a refinement round, not a gate) |
| O9 | Seeds | **Decided (owner, 2026-11-01).** One recorded seed string per draw, seed base `261101\|alias`: neighbours `…\|neighbours`, random draws `…\|random\|1` and `…\|random\|2`, noise repeats `…\|noise`, bootstrap `…\|boot`, and the derived strings of §5.5. Written in §5.5. **Amended (owner, 2026-11-01):** numeric seeds are the first 8 bytes of sha256(seed string), big-endian unsigned; power seed `…\|power`; random draws `…\|random-1-v2` and `…\|random-2-v2` |
| O10 | **The validation overlap of §4.3 item 6** (8 satisfied papers with validation records; the validation figure not fully out of sample) | **Decided (owner, 2026-11-04): keep the 900 as frozen**, print the sensitivity without the 8 papers beside H1, H1b and H2 (§7), and state in §6.4's reading that the validation figure is not fully out of sample. Dropping the 8 papers (292 papers, 876 records) and re-scoring the validation without the 121 pairs (scratch: 0.992, still PASS; §4.3 item 6) were declined |
| O11 | **Agreement level `a` the caps lever reads** (§8): the power to confirm H2 is computed at several assumed agreement levels (`a` = 1.0, 0.8, 0.7, 0.6 in `alias_study_power`) | **Decided (owner, 2026-11-02): `a = 0.7`**; `a = 0.6` is reported as a sensitivity. Written in §8 |
| O12 | **A second rater for the judged-pair spot check** (§13.5, R5, R15) | **Decided (owner, 2026-11-04): a single rater, the owner, for this study**, stated as a limitation (§13.5). Further raters are planned for a second wave |
| O13 | **The test form of the H2 chunk-volume covariate sensitivity** (§7), which by R14 can make H2 *fragile* (R19) | **Decided (owner, 2026-11-03, R23):** a Freedman–Lane sign-flip with the studentized intercept (restricted model S = γx, restricted residuals flipped, refit; 10,000 flips, seed `261101\|alias\|perm`; Monte Carlo p), reusing the primary's unadjusted gold-only gate. Written in §7 |

## 15. What must exist before freeze

| # | item | state |
|---|---|---|
| 1 | Owner decisions folded into this text | D1–D10 folded in v1; O1, O2, O4, O5, O6 and O9 decided 2026-11-01, O7 decided for Opus; the #904-review resolutions and the corpus-build decisions of 2026-11-01 folded in (§5.1, §5.5, §6.3, §7, §8, §9.2); the test per member (§7) and O11 (`a = 0.7`, §8) decided 2026-11-02; R1–R11 from the independent methodology review decided 2026-11-02/03 and folded in v2, with O2 superseded and O3 decided; R12–R28 decided 2026-11-03 and folded in v3, with the pre-freeze corrections P1–P6, O13 with R23; R29 (drift alarm report-only, arm-gap pause) and R30 (no Argon cap, deciding O7 for Argon) decided 2026-11-04 after the blind pilot; O8, O10 and O12 decided 2026-11-04 (§14). Decisions folded through R30 and O7/O8/O10/O12 (O13 with R23) |
| 2 | **Record-set sha** and the balanced set recounted by committed code: sha `09fc5ca4…` (equal to the summary's `accepted_sha256`), balanced-set id sha `2be86f8c…`, 300 / 900 / 191, the verdict-source split, and the pilot and calibration overlaps | **done**: `alias_study_recount.py` (committed, tested) reproduces all 95 checked figures of §4 at the frozen shas; `artifacts/record-recount.json` (counts and shas only) |
| 3 | The validation set's paper exclusion checked against the papers added by the extension to 300 | **done**: in `artifacts/record-recount.json`: the 377-paper exclusion list covers the 355 papers activated before the extension and not the extension (166 of its 177 papers outside, 91 of its 98 satisfied); 8 satisfied papers have validation records (O10 decided: keep the 900, print the sensitivity) |
| 4 | **Neighbour and random selection manifests** from OpenAlex (metadata only, seeded), with the counts of §5.1 public; the **H2 power** recomputed on them, and the caps raised if it is below .80 (§8) | **done**: the private **v4** manifests (R17, R18; #915, at `617d28d`, reachable from `main` through the merge commit `a623981`), counts and sha256s in §5.1; the power run committed (item 8), caps not raised |
| 5 | **The 9 bench collections of R26 loaded** (§5.3, §9.3) under a reviewed live-ops plan: each corpus embedded once at the same chunker, embedder and tools image, base chunk ids and content verified identical across the arms, each collection loaded once with its glossary file (an empty-row file for *none*), each with its registry entry and load receipt | **done**: 9 collections loaded and verified 2026-11-03; 811,043 points; 0 retries, evictions or purges; load receipts with `SHA256SUMS`, private |
| 6 | **Glossaries for every added paper** in every arm, generated by the same pipeline at the same versions (symbol model and prompt, `rules/3`, `alias_abbrev.py`), merged, and their shas recorded; a census of entries per added paper | **done** (#919, #929): glossaries for every added paper in every arm, 7,068 distinct papers (2,423 per arm; the random draws overlap by 201) |
| 7 | The `ragstack.glossary_file/v1` files (merged, placebo) that `load-embeddings` renders, with render-parity tests against the prereg's mention rule and caps (§5.3); the **reach count** of §9.1 read offline against the 480-of-600 gate | **done** (#918): parity 600 of 600 balanced G records render their own entry on the gold chunk (§5.3) |
| 8 | **Offline power calculation** by committed code on the realised layout (normal approximation and simulation of each member's test of §7 at α = 0.05, R1; H2's caps lever at `a = 0.7` with `a = 0.6` as the sensitivity, read at α = 0.05, §8), with a clean tree and `citable true`, committed as an artifact | **done**: `artifacts/power-v4.json`, the public copy of the private v4 run (`617d28d`, clean, `citable true`; host removed; §8); the lever reads α = 0.05 (`LEVER_ALPHA`, `e3b9695`) and does not fire. The C cost rule's operating characteristics and the spot check's detection probabilities are committed (`artifacts/c_cost_rule.json`, `artifacts/spot_check_detection.json`; R12, R15) |
| 9 | The committed replay of the composed judge rule on the validation labels (§6.4), replacing the scratch replay | **done**: `alias_study_replay.py`; `artifacts/judge-rule-replay.json`: 482 of 487 (0.990), 12 escalated, 11 right, 0 unlabelled; the gate as validated 0.992 / 2.5 % (= `analysis-gate.json`); O10 sensitivities included |
| 10 | The code of §10 *Must be written*, merged after an independent review | **done**: merged with independent reviews (#913, #914, #918, #919, #922, #923, #925, #929, #932); the recount and replay scripts of items 2 and 9 are added with tests in the pre-freeze close-out PR and reviewed there |
| 11 | **Owner spot check of the 40 records** (§13.4): may be done before the freeze; must be committed before any analysis | **done** (owner, 2026-11-04): 37 of 40 valid (0.925, at or above 0.85), ingested by `alias_study_spot.py`; `artifacts/spot-records-counts.json`, sheet sha256 `8fd9987e…` |
| 12 | A dry run of the whole chain on base × BM25 × none for **non-study records only** (for example the validation set's), to check the plumbing without reading any study outcome | **done**: a dry run of the whole chain at `main` `27def28` on 83 validation records, fake retriever and judges, exit 0; private log |
| 13 | O1 (the C gate's numeric definition) and O9 (seed base) fixed in the text | done in this draft: rule C (§3.1) and the seed strings (§5.5) |
| 14 | **The pre-freeze pilot** on the 191 held-out records (§5.6, R8): run after item 5 and item 10, in all 29 legs, judged, blind to outcomes (R28: no per-leg found@10, found@10-gold or RR@10, pair counts per judging stage only); its counts committed and entered in *What had been seen before writing*; any consequence recorded with the counts it rests on, and anything beyond the allowed consequences recorded as a deviation before freeze | **done** (2026-11-04): `artifacts/pilot-report.json`; entered in *What had been seen before writing*; consequences R29, R30 |
| 15 | **The prompt-paraphrase check** (§6.6, R4): three paraphrases of `alias-judge/2`, committed before they are run, scored on the validation pairs by both primaries; the result recorded in §6.6 and §13.3 | **done** (exploratory, owner-accepted 2026-11-04; §6.6, §13.3) |
| 16 | **Judge metrics inserted** (§6.6, R4): precision, recall, F1 for *answers*, specificity, κ and Gwet's AC1 with counts, from the calibration follow-up PR that computes them from the committed validation data; the placeholders of §6.6 replaced by its numbers and commit | **done** (§6.6, from JUDGE-CALIBRATION.md, review follow-ups, #910, `8f4043b`) |
| 17 | **Freeze verification ready** (README, R10): a signing key for the signed annotated tag, with its public part registered so the public repository shows the tag as verified; the tag name (`prereg-alias-study-2026-11`) is fixed in the README | **partly** (2026-11-04): the owner's SSH signing key is configured for git (`gpg.format ssh`, `tag.gpgsign`); the key is passphrase-protected, so the owner signs the tag; a test signature and `git tag -v` before the tag remain |
| 18 | **The corpus builder implements R17 and R18** (the nearest-band fill at `any` with 0 new fetches; the repetition screen on every added paper in every arm, after the C0 screen, with its exclusions counted per arm and draw), reviewed and merged before the v4 build of item 4 | **done**: implemented in #915, merged with a merge commit (`a623981`); the v4 build ran on it at `617d28d` (item 4) |
| 19 | **O13 fixed**: the test form of the H2 chunk-volume covariate sensitivity, in §7, and implemented with the other sensitivities (§10) | **done**: decided (R23, §7); the Freedman–Lane intercept implemented in `alias_study_stats.py` (#913) |
| 20 | **The stage-A leg lists** (R20, R25, §5.7): retrieval 19 legs (the 5 Layer-1 attachments × default, norerank, BM25, and the 4 noise repeats), judging 14 legs (without the BM25 legs), checked against the run harness (PR #914) | **done** (#914): the harness's leg lists match §5.7 |
| 21 | **The load route** (R26): each corpus embedded once (`embed-bulk.cwl`), its shards loaded once per variant with `load-embeddings.cwl` and the variant's `ragstack.glossary_file/v1` file (an explicit empty-row file for *none*, never `null`), through the workflow engine under the approved path | **done**: settled with management; the owner confirmed 9 collections; loaded (item 5) |
| 22 | **The capacity check** (R26): about 16–19 GB of dense vectors and 2–3 GB of BM25 index for the 9 collections | **done**: confirmed by management |
| 23 | **Base embeddings shared across the four corpus arms** (R26, §5.3) | **done**: resolved (management, from `load-embeddings.cwl` at v1.6.21): `embeddings` is a list of files, so base is embedded once and loaded with each arm's added-paper shards; one glossary file per load covers base and added documents |
| 24 | **The glossary files of R27** (merged and placebo with the `Xs` entries, and the empty-row *none* file), written by the `glossary_file/v1` writer of item 7 under the rendering conventions of §5.3 | **done** (#918): the glossary files with the `Xs` entries, loaded per R26 |

