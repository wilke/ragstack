# Pre-registration, alias study (OA-1000)

**Status: DRAFT — not frozen; owner review pending.** No sha is recorded because nothing is frozen yet.
The record set (§3 of the prereg) was built before this draft. No retrieval has been run on any study
record.

| file | tier | what |
|---|---|---|
| [PREREG-alias-study.md](PREREG-alias-study.md) | record, once frozen; a draft until then | the design: question, proposed hypotheses H1–H3, the record set as built, the 18 cells (corpus arm × retriever × glossary arm), outcome and judge, analysis, power sketch, gates, open decisions (§13), pre-freeze checklist (§14) |

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

The prereg's §14 is the checklist, and each item is open in this draft. The four that need work outside
this repository are:

- the neighbour and random corpora, built and censused on bench copies within a management window;
- glossaries for the added papers, made by the same pipeline;
- the record-set sha recomputed by committed code;
- an offline power calculation by committed code.

## Changes of substance between drafts

- **v0, 2026-10-31:** first draft.
