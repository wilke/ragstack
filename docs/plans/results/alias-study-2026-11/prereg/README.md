# Pre-registration, alias study (OA-1000)

**Status: DRAFT — not frozen; owner review pending.** No sha is recorded because nothing is frozen yet.
Current draft: **v2, 2026-11-03**, with the owner's decisions of 2026-11-01 (v1) and the changes the owner
approved on 2026-11-02/03 from an independent methodology review, 2026-11-02 (R1–R11), folded in. The
record set (§4 of the prereg: 1,091 accepted records, the balanced set of 900 on 300 papers) was built
before v1. No retrieval has been run on any study record. The pre-freeze pilot (prereg §5.6) will
retrieve and judge the 191 held-out records before the freeze; the 900 balanced records stay unretrieved
until after it.

| file | tier | what |
|---|---|---|
| [PREREG-alias-study.md](PREREG-alias-study.md) | record, once frozen; a draft until then | the design: question, prior results (§2), the two layers (Layer 1: Family G, H1 then H1b in fixed sequence, and Family N, H2, each at one-sided α = 0.05; Layer 2: serving decisions on CI-based rules with a harm rule, H3 descriptive), the record set as built, the 24 cells (4 corpus arms × 3 retrievers × 2 glossary arms) plus 4 noise repeats and a placebo glossary leg, the pre-freeze pilot and the two post-freeze stages, outcome and judge, analysis, power, gates, open decisions (§14), pre-freeze checklist (§15) |

Background notes, not part of the record: `docs/plans/alias-study-2026-11/` (ABBREV, VERIFIER,
JUDGE-CALIBRATION, JUDGE-VALIDATION-PLAN, TIEBREAKER-PLAN).

## Freeze procedure (round 3's, with a signed public tag added by R10)

1. The owner says go, in writing, naming the commit of this directory the go applies to. The prereg is
   frozen **as written**, draft status line included. No edit is made to mark it frozen.
2. In a clean checkout of that commit, `sha256sum PREREG-alias-study.md` and a UTC timestamp go into a
   **private** `PREREG.sha256` kept with the outputs, **before** any study-record retrieval, any
   expanded-corpus store write that the run depends on, or any judge call on a study pair.
3. The same sha goes into the private run config (`prereg.file`, `prereg.sha256`), so every stage that
   reads it refuses on a mismatch.
4. **Signed public tag (R10).** At the commit named in the go, a **signed annotated** git tag
   `prereg-alias-study-2026-11` is made (`git tag -s`), its message holding the prereg's sha256 and the
   UTC timestamp of step 2, and it is **pushed to the public repository** before any study-record
   retrieval, expanded-corpus store write the run depends on, or judge call on a study pair. Anyone can
   then check the freeze: `git tag -v` verifies the signature, and `sha256sum` of the prereg at the tagged
   commit must equal the tag message, the private `PREREG.sha256` and the run config's `prereg.sha256`.
   The tag is never moved or deleted; a tag that cannot be verified blocks the run.
5. One commit records the freeze **in this README only**: the status line becomes `FROZEN <sha> at <UTC>
   on the owner's go (commit <sha>)`. The prereg file is never touched again. Later changes go to
   `../DEVIATIONS.md`.
6. The PR carrying the freeze commit is merged, and the run starts after that merge.

## Preconditions

The prereg's §15 is the checklist. The items that need work outside this repository are:

- the neighbour and two random selection manifests (OpenAlex metadata only), and H2's power recomputed
  on them, raising the caps if it falls below .80;
- the three expanded corpora, built and censused on bench copies within a management window;
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
