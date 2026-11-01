# Pre-registration, alias study (OA-1000)

**Status: DRAFT — not frozen; owner review pending.** No sha is recorded because nothing is frozen yet.
Current draft: **v1, 2026-11-01**, with the owner's ten decisions of that date folded in. The record set
(§4 of the prereg: 1,091 accepted records, the balanced set of 900 on 300 papers) was built before this
draft. No retrieval has been run on any study record, and no neighbour or random paper has been selected.

| file | tier | what |
|---|---|---|
| [PREREG-alias-study.md](PREREG-alias-study.md) | record, once frozen; a draft until then | the design: question, prior results (§2), the two layers (Layer 1: H1, H2 under Holm over 2, H1b in fixed sequence after H1; Layer 2: serving decisions on CI thresholds, H3 descriptive), the record set as built, the 24 cells (4 corpus arms × 3 retrievers × 2 glossary arms) plus 4 noise repeats, outcome and judge, analysis, power, gates, open decisions (§14), pre-freeze checklist (§15) |

Background notes, not part of the record: `docs/plans/alias-study-2026-11/` (ABBREV, VERIFIER,
JUDGE-CALIBRATION, JUDGE-VALIDATION-PLAN, TIEBREAKER-PLAN).

## Freeze procedure (proposed: round 3's, unchanged)

1. The owner says go, in writing, naming the commit of this directory the go applies to. The prereg is
   frozen **as written**, draft status line included. No edit is made to mark it frozen.
2. In a clean checkout of that commit, `sha256sum PREREG-alias-study.md` and a UTC timestamp go into a
   **private** `PREREG.sha256` kept with the outputs, **before** any study-record retrieval, any
   expanded-corpus store write that the run depends on, or any judge call on a study pair.
3. The same sha goes into the private run config (`prereg.file`, `prereg.sha256`), so every stage that
   reads it refuses on a mismatch.
4. One commit records the freeze **in this README only**: the status line becomes `FROZEN <sha> at <UTC>
   on the owner's go (commit <sha>)`. The prereg file is never touched again. Later changes go to
   `../DEVIATIONS.md`.
5. The PR carrying the freeze commit is merged, and the run starts after that merge.

## Preconditions

The prereg's §15 is the checklist. The items that need work outside this repository are:

- the neighbour and two random selection manifests (OpenAlex metadata only), and H2's power recomputed
  on them, raising the caps if it falls below .80;
- the three expanded corpora, built and censused on bench copies within a management window;
- glossaries for the added papers in every arm, made by the same pipeline, and the offline reach count;
- the record-set sha, the balanced set and the overlap counts recomputed by committed code;
- an offline power calculation by committed code;
- the owner's spot check of 40 records.

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
