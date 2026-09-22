# Papers

Publication-track write-ups of this project's empirical work. Process and rules live in the
`study-writeup` skill (`.claude/skills/study-writeup/SKILL.md`) — read that before editing
anything here.

## The three tiers, and why this directory is separate

| tier | where | discipline |
|---|---|---|
| the record | `docs/plans/results/` | immutable once committed |
| the lab book | the study artifact + `HANDOFF-*.md` | append-only, dated |
| **the paper** | **here** | revised in place, reads as finished at every moment |

The lab book and the paper were one document until 2026-09-22 and it did not work: the
artifact opened with a status line that went stale weekly, because a living account and a
finished argument have opposite update disciplines. They are now separate, and the drift
that separation risks is held in check mechanically — see *Claims* below.

Nothing here carries a status, a date-stamped addendum or a roadmap. Those belong in the lab
book. If a sentence needs the word *currently*, *still* or *next*, it is in the wrong tier.

## Roster

| id | working title | argument | state |
|---|---|---|---|
| `paper-a-evidence-localisation` | Reliable but not canonical: LLM judges for passage-level evidence localisation | Judges agree on *whether* a document holds evidence and disagree on *where*; the set of locations never converges; a *graded* per-sentence support score does reproduce itself, across model families; reliability is not validity | drafting — the data exists |
| `paper-b-chunking` | (not started) How should a retrieval index chunk full-length scientific articles? | Document-level metrics answer a different question; realised chunk size confounds chunking method; reach and containment trade against each other and hybrid retrieval flattens the contrast | blocked on the two-reader human read |

Target for both: **arXiv preprint first**, venue-neutral, venue chosen afterwards.

Paper A is first because it is self-contained, general beyond chunking, and its data is
complete. Paper B's confirmatory verdict cannot be written until the human read produces κ
and the labels are frozen.

## Claims — how a number gets into a paper

Every load-bearing number is an entry in the paper's `claims.json`, pointing at a committed
artifact under `docs/plans/results/`. Prose cites the claim `id`; it never carries a number
typed from memory.

```bash
/rag/envs/ragstack/bin/python3 docs/papers/check_claims.py          # all papers
/rag/envs/ragstack/bin/python3 docs/papers/check_claims.py --json   # machine output
```

Run it before any commit that touches a paper and before any publish. A number that cannot
be pointed at a committed artifact does not go in the paper: measure it, commit the artifact,
then write the sentence.

## Bibliography

`bibliography.md`. Every entry is fetched and read before it is listed, and records what the
paper actually establishes rather than what its abstract claims. The entry for
`allamraju-2025` is the worked example of why: its headline multiple is measured against
baselines scoring zero.
