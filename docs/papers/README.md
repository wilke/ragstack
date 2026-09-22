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

### What the literature survey did to the roster, 2026-09-22

Both papers narrowed, and both are better for it. The surveys in `bib-inbox/` were told to hunt
for work that contradicts us, and they found a lot. Read `paper-a-evidence-localisation/OUTLINE.md`
for A's repositioning; for B the essentials are:

- **`zhou-2026`** (Zhou, Wang, Koopman, Zuccon) asks our confound question *verbatim* — "prior
  work has not controlled for this variable … do effectiveness differences reflect segmentation
  quality, or merely chunk size?" — and answers it with a **per-query correlation**, not a matched
  comparison. This is the paper B must cite and then distinguish, and the distinction is the
  estimand.
- **The claim "nobody controls for chunk size" is not defensible** and must be replaced by the
  finer, true one: the literature *controls nominal* size (`amiri-2025`), *bounds* realised size
  (`demoura-2026`), *reports* realised size (`duarte-2024`, `smith-2024-chroma`) and *matches
  delivered token budget* (RAPTOR, HiChunk, Dense X) — but no published study conditions its
  method comparison on matched **realised** chunk length.
- **`kaszkiel-2001`** is the threat to take seriously: if effectiveness is flat across 50–450
  words, our semantic chunker collapsing to ~350 tokens supports "the four labels were meaningless"
  but is neutral on "and it cost us performance". Do not overclaim the second.
- **`pevzner-2002`** shows the realised-length confound was diagnosed in text segmentation in 2002
  — WindowDiff replaced Pk precisely because Pk "is affected by variation in segment size
  distribution". Retrieval inherited the segmenters and not the lesson. Good framing, freely given.
- **Parent-document / small-to-big retrieval has almost no peer-reviewed evidence base**, and
  **late chunking is arXiv-only**. Both absences are findings worth stating.

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
