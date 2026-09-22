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

### Naming decisions, settled 2026-09-22 — adopt, do not coin

The surveys were asked whether the field already has names for things we were about to invent.
It does, in two cases, and adopting them costs nothing while coining duplicates is a reviewer's
easiest objection.

| we were going to call it | the field already calls it | source |
|---|---|---|
| budget-normalised retrieval evaluation | **`EM @ l tokens`**, "at a fixed retrieval token budget" | `chen-2024-densex` (EMNLP 2024), with `lu-2025-hichunk` giving the fairness argument independently |
| reach vs containment (the phenomenon) | **"within-document retrieval failure"** — correct document retrieved, answer-bearing chunk missed | `kobeissi-2026` (arXiv 2602.17981) |

Nuance on the second: `kobeissi-2026` is an unrefereed preprint on 150 FinanceBench questions and
is **not** budget-normalised, but it is the only prior work that decomposes this way *behind a
cross-encoder reranker*. So: adopt their name for the phenomenon, keep **reach** and
**containment** as our names for the two measured quantities (nobody has named those), and claim
the budget-normalised version as ours. Do **not** borrow `retrievability` (Azzopardi & Vinay) —
it is collection-level retrieval bias, a different concept.

**Where we appear to be genuinely first:** non-inferiority testing in IR/NLP. The survey searched
exhaustively and every hit was clinical or biostatistical. Cite Lakens 2017 for TOST and the
smallest effect size of interest, and say plainly that we found no IR precedent.

**Best citable statement of the powered-null rule**, and it is from 2008, not from us:
Webber, Moffat & Zobel (CIKM 2008) — a failure to find significance does not license concluding no
difference exists, and the experimenter needs to know how large a difference could have been
missed. That *is* the power floor. The same paper warns that growing the topic set until power is
reached biases toward finding significance, which speaks directly to our n = 80 problem. Pair
Fuhr's guidelines with Sakai's response, which answers "agreed" on hypotheses-before-experiment
and on multiplicity, so the practice does not read as one researcher's opinion.

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
- **`boytsov-2025`** is the threat with teeth: long-document benchmarks front-load their evidence,
  so any large-chunk advantage may be a corpus artefact. We can answer it directly — our position
  oracle measured that 55.4 % of judged evidence *starts past token 1,024* — and the paper must
  report that rather than assume the corpus is neutral.
- **`jin-2025`** and Yu and colleagues both find an inverted U in answer quality against delivered
  context, so budget-normalised *retrieval* quality is an upper bound and not a proxy for answer
  quality. That is an argument for the synthesis stage, and it should be stated as one.
- **Cite `cuconasu-2024` precisely or not at all.** Its famous result is that random documents beat
  *topically related distractors*, not that noise beats no noise. It is routinely misquoted.

### Survey coverage, for the record

287 verified entries across five areas, each file ending with a "searched and did not find"
section so coverage is evidenced rather than asserted. Two operational notes for whoever runs the
next one: the 200-call WebSearch budget is shared across concurrent agents and was exhausted
partway, after which the agents fell back to the **arXiv API** and **OpenAlex**, which are better
defaults for this work anyway. Publisher access that fails: ACM DL (403), dblp (Anubis), Springer
(IdP bounce), OpenReview (bot wall), Semantic Scholar (429). Access that works: ACL Anthology,
arXiv `/abs/`, PMLR, CEUR, sigir.org, institutional repositories, OpenAlex.

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
