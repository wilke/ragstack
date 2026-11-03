# Pre-registration, alias study (OA-1000)

**Status: DRAFT — not frozen; owner review pending.** No sha is recorded because nothing is frozen yet.
Current draft: **v3, 2026-11-03**, with the owner's decisions of 2026-11-01 (v1), the changes the owner
approved on 2026-11-02/03 from an independent methodology review, 2026-11-02 (R1–R11), and the owner's
decisions of 2026-11-03 (R12–R28) and the pre-freeze corrections P1–P6 folded in. The
record set (§4 of the prereg: 1,091 accepted records, the balanced set of 900 on 300 papers) was built
before v1. No retrieval has been run on any study record. The pre-freeze pilot (prereg §5.6) will
retrieve and judge the 191 held-out records before the freeze; the 900 balanced records stay unretrieved
until after it.

| file | tier | what |
|---|---|---|
| [PREREG-alias-study.md](PREREG-alias-study.md) | record, once frozen; a draft until then | the design: question, prior results (§2), the two layers (Layer 1: Family G, H1 then H1b in fixed sequence, and Family N, H2, each at one-sided α = 0.05; Layer 2: serving decisions on CI-based rules with a harm rule, H3 descriptive), the record set as built, the 24 cells (4 corpus arms × 3 retrievers × 2 glossary arms) plus 4 noise repeats and a placebo glossary leg, the pre-freeze pilot and the two post-freeze stages, outcome and judge, analysis, power, gates, open decisions (§14), pre-freeze checklist (§15) |
| [artifacts/c_cost_rule.py](artifacts/c_cost_rule.py), [artifacts/c_cost_rule.json](artifacts/c_cost_rule.json) | record, once the prereg is frozen | the C cost rule's operating characteristics (prereg §3.3, R12): pass rates of the v2 and the R12 rule on 300 C records, and the −3.6-point bound's derivation; a planning calculation, no study data |
| [artifacts/spot_check_detection.py](artifacts/spot_check_detection.py), [artifacts/spot_check_detection.json](artifacts/spot_check_detection.json) | record, once the prereg is frozen | the judged-pair spot check's detection probabilities under the < 0.90 rule at 60 and 100 pairs (prereg §13.5, R15); exact binomial, no study data |

Background notes, not part of the record: `docs/plans/alias-study-2026-11/` (ABBREV, VERIFIER,
JUDGE-CALIBRATION, JUDGE-VALIDATION-PLAN, TIEBREAKER-PLAN).

## Freeze procedure (round 3's, with a signed public tag added by R10)

1. The owner says go, in writing, naming the commit of this directory the go applies to. The prereg is
   frozen **as written**, draft status line included. No edit is made to mark it frozen.
2. In a clean checkout of that commit, `sha256sum PREREG-alias-study.md` and a UTC timestamp go into a
   **private** `PREREG.sha256` kept with the outputs, **before the first post-freeze retrieval of a study
   record**. The 9 bench collections (prereg §5.3, R26) are loaded before the freeze, and the pre-freeze
   pilot (prereg §5.6) runs on them.
3. The same sha goes into the private run config (`prereg.file`, `prereg.sha256`), so every stage that
   reads it refuses on a mismatch.
4. **Signed public tag (R10).** At the commit named in the go, a **signed annotated** git tag
   `prereg-alias-study-2026-11` is made (`git tag -s`), its message holding the prereg's sha256, the UTC
   timestamp of step 2 and the **sha256 of each of the 9 load receipts** (prereg §9.3), and it is
   **pushed to the public repository** before the first post-freeze retrieval of a study record. Anyone can
   then check the freeze: `git tag -v` verifies the signature, and `sha256sum` of the prereg at the tagged
   commit must equal the tag message, the private `PREREG.sha256` and the run config's `prereg.sha256`,
   and each collection's load receipt must hash to the sha256 the tag pins.
   The tag is never moved or deleted; a tag that cannot be verified blocks the run.
5. One commit records the freeze **in this README only**: the status line becomes `FROZEN <sha> at <UTC>
   on the owner's go (commit <sha>)`. The prereg file is never touched again. Later changes go to
   `../DEVIATIONS.md`.
6. The PR carrying the freeze commit is merged, and the run starts after that merge.

## Preconditions

The prereg's §15 is the checklist. The items that need work outside this repository are:

- the neighbour and two random selection manifests (OpenAlex metadata only) of the **v4** build (the v3
  pass with the nearest-band fill and the repetition screen, prereg R16–R18): built (#915 at `617d28d`, merged with a merge commit as `a623981`),
  counts entered in prereg §5.1, H2's power recomputed on them (the caps lever does not fire at a = 0.7,
  prereg §8); the power output still to be committed as an artifact;
- the 9 bench collections (one per corpus × glossary arm, prereg R26; the count pending the owner's
  confirmation), each corpus embedded once and each collection loaded once from those embeddings with its
  glossary file (an explicit empty-row file for *none*), each with a registry entry and a load receipt;
  base embedded once and shared across the four arms;
- the `glossary_file/v1` writer and its files (merged and placebo with the plural entries of R27, and the
  empty-row *none* file), with render-parity tests;
- glossaries for the added papers in every arm, made by the same pipeline, and the offline reach count;
- the record-set sha, the balanced set and the overlap counts recomputed by committed code;
- an offline power calculation by committed code;
- the owner's spot check of 40 records;
- the pre-freeze pilot on the 191 held-out records, run after the collections are loaded (prereg §5.6);
- the prompt-paraphrase check of the judge on the validation pairs (prereg §6.6);
- the judge metrics from the calibration follow-up, inserted into prereg §6.6;
- a signing key for the public freeze tag (step 4 above).

## Changes of substance between drafts

- **v0, 2026-10-31:** first draft.
- **v1, 2026-11-01:** the owner's ten decisions folded in. H1 is now G-only with C as a gate, and H1b
  (the G-vs-C DiD) is tested in fixed sequence after H1; H2 compares neighbours with the average of two
  random draws and carries a gold-only direction gate; the family is Holm over 2; H3 moves to a
  descriptive Layer 2 beside three CI-based serving thresholds. Neighbour caps, the random-arm rule, the
  reach gate (80 %), the arm-stratified drift alarm and both owner spot checks are fixed. Counts are
  updated to the 300-paper build (1,091 accepted; balanced 900 = 600 G + 300 C; record-set sha
  `09fc5ca4…`). H1 power at Holm over 2 is ≈ .97 / .88 at the two planning scenarios (normal
  approximation). New disclosure: the extension to 300 reached 8 papers on which the judge validation had
  drafted records (prereg §4.3 item 6).
- **v2, 2026-11-03:** the changes the owner approved on 2026-11-02/03 from an independent methodology
  review, 2026-11-02 (prereg R1–R11). The Holm pair is replaced by **two families**, each at one-sided
  α = 0.05: Family G (H1, then H1b in fixed sequence) and Family N (H2); H1 power ≈ .99 / .93 at the two
  planning scenarios (normal approximation), H2's power to confirm .96 / .90 at a = 0.7 and .79 / .73 at
  a = 0.6 on the v2 dose (scratch, not citable). Layer 2 gains a harm rule, a CI-based C-cost rule
  (margin −1 point), a reranker non-inferiority rule (margin −3 points), a wording table governed by
  Layer 1, and an equivalence bound for any *no effect* statement; the thresholds are labelled
  judgement-based. Scope wording for every claim; judge reporting (precision, recall, F1, specificity,
  κ, AC1; a false-yes bound on H2; a prompt-paraphrase check; the correlated-Qwen limitation; one Opus
  retry); the judged-pair spot check stratified by judge label; fixed sensitivities with a *fragile*
  label; a placebo glossary leg; a pre-freeze pilot on the 191 held-out records; two post-freeze stages
  with judging on demand; a signed public freeze tag; H2's estimand stated.
- **v3, 2026-11-03:** the owner's decisions of 2026-11-03 (prereg R12–R27). The C cost rule reads the
  point estimate (*do not attach* when ΔC < −1 point) and the CI's lower bound (C_lo < −3.6 points, the
  smallest C change 300 C records detect with 80 % power at 5 % discordance), with the statement that a
  C cost smaller than about 3.6 points cannot be ruled out; pass rates at 5 % C discordance and a true C
  change of 0 / −1 / −2 points are .16 / .05 / .01 for the v2 rule and .76 / .49 / .22 for the R12 rule
  (`artifacts/c_cost_rule.json`). The H2 false-yes bound always uses U = max(random arm, pooled).
  *Fragile* applies to H1 and H2 only, through a paper-cluster bootstrap-t (replacing the studentized
  test, which could not flip a verdict), topic clustering and (H2) the chunk-volume covariate, tested by a
  Freedman–Lane sign-flip; subgroup splits never make anything fragile, and H1b is exempt. The judged-pair
  spot check grows to 100 pairs (50 / 33 / 17); at a true 10 % error rate the *answers*-stratum rule
  fires with probability .38 (.35 for the 30-pair *answers* stratum of the 60-pair sheet; `artifacts/spot_check_detection.json`). The corpus
  build's v3 facts (six length bands with the top band split at 80, the carry-over re-draw, seeds
  unchanged, the fetch budget, relaxation source → type → band), a nearest-band fill at the last
  relaxation level, a repetition screen (distinct-chunk ratio < 0.90 excluded) for every added paper,
  and the ± 5 % chunk-volume target read as a build target covered by the covariate sensitivity, with a
  placeholder for the v4 counts. Stage A now also runs the 5 norerank cells the inactive-reranker gate reads, so every Layer-1 gate is
  read after stage A alone; stage-A retrieval also takes the Layer-1 attachments' BM25 legs (19 legs
  retrieved in stage A), whose pairs are judged in stage B (14 legs judged in stage A); the placebo drops every
  entry equal to one of the receiving paper's own entries. The covariate's test form (§14 O13, opened and
  closed in this version) is fixed by R23. An equivalence reading with zero discordant records prints
  *not resolved*, and Wilson upper limits are rounded up.
  The v4 build's counts fill the §5.1 table (2,423 papers per arm; random-1 −4.51 %, random-2 −4.75 % of
  the neighbour arm's chunks; the ≥ 80 band still short), with its carry-over rule quoted verbatim, the
  nearest-band fill's global-greedy order, the repetition screen's 9 hits (7 JATS) disclosed, and §8's
  power on the v4 dose.
  No glossary is swapped in place: each corpus × glossary arm is its own bench collection (9), written
  once by loading the corpus's single embedding with the variant's glossary file (an explicit empty-row
  file for *none*; no re-embedding, no backfill), with a registry entry and a load receipt, censused at
  every leg (R26). The glossary files carry an added `Xs` entry for every abbreviation, and the rendering
  conventions are fixed (R27). The freeze is now triggered by the first post-freeze study-record
  retrieval, and the signed tag also pins the 9 load receipts' sha256.
- **v3, pre-freeze corrections, 2026-11-03:** R28, the pilot blind to outcomes (no per-leg found@10,
  found@10-gold or RR@10 in pilot mode; pair counts per judging stage plus the registry total). P1: the
  admin-config hash pins neither the BM25 glossary boost nor the reranker field; the boost is shown by the
  behavioural probe, the reranker field is declared. P2: H2's U from the frozen stage-A judge summary,
  cross-checked. P3: the record spot check's draw by record id. P4: the judged-pair spot check's arm
  re-split, shortfall chain, sheet order, pool and public record. P5: the pilot's intervals, the
  escalated / tiebroken / unjudged shares, H2's four discordance readings. P6: public provenance without
  host or warnings; `artifacts/c_cost_rule.json` and `artifacts/spot_check_detection.json` regenerated so.
