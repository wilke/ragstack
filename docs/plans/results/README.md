# Phase-0 evidence record

The run reports behind [chunking-evaluation.md](../chunking-evaluation.md) and
[long-doc-judged-set.md](../long-doc-judged-set.md). Every one of them existed only in a
session scratch directory until this commit; they are copied here **verbatim**, unedited,
so a number quoted in a plan can be traced to the run that produced it.

The plans are where a finding is *acted on*. These files are where it is *recorded*. If the
two ever disagree, the run report is the measurement and the plan is the reading of it — fix
the plan.

## The files

| file | what it is | run |
|---|---|---|
| [`PREREG-step3.md`](PREREG-step3.md) | step 3's predictions and falsification bar, written before any embedding call | 2026-09-04 |
| [`RESULTS-step3-real-experiment.md`](RESULTS-step3-real-experiment.md) | the real dense contrast that reversed Leg A's demotion — 4 configs, 10 CDS topics | 2026-09-04 |
| [`PREREG-stage1.md`](PREREG-stage1.md) | stage 1's 9-contrast family, bar X, predictions and budget — pre-committed | 2026-09-04 |
| [`RESULTS-stage1-legA.md`](RESULTS-stage1-legA.md) | the 24-config chunking grid on the Leg A pilot; 968M tokens, 94 min, 0 retries | 2026-09-04 |
| [`tables-stage1-legA.md`](tables-stage1-legA.md) | stage 1's generated Tables 1–8: full grid, panels, every metric, auto-scored verdicts | 2026-09-04 |
| [`RESULTS-legBC-pilots.md`](RESULTS-legBC-pilots.md) | Phase-0 items 3–6: the §7a oracle on 90 topics, the Leg B pilot, the Leg C pilot, empirical σ_d | 2026-09-04 |
| [`RESULTS-legB-rerun.md`](RESULTS-legB-rerun.md) | Leg B re-run against the real LLM with the three §2.6 fixes; σ_d on Leg B's own queries; **the two legs disagree** | 2026-09-04 |

Read in that order — each one's "Read this first" block assumes its predecessors.

## Reading them in-repo

Three things about the copies, none of which is a defect in the reports:

- **`tables.md` was renamed on copy** to `tables-stage1-legA.md`, because this directory will
  hold more than one run's tables. `RESULTS-stage1-legA.md` links to it as `tables.md`; that
  link does not resolve here, and this row is the map.
- **Links to `.py`, `.json`, `.jsonl` and `BRIEF-*.md` artefacts do not resolve.** Each report
  ends with a file manifest naming the harness that produced it; those files stayed in the run
  directory and are not in the repo. The manifests are kept because they say what was run, not
  because they are navigable.
- **`RESULTS-legBC-pilots.md` links to its predecessor as `../stage1/RESULTS-stage1-legA.md`**,
  the layout of the run directory. Here everything is flat, so that one is
  [`RESULTS-stage1-legA.md`](RESULTS-stage1-legA.md).

Links back to the plans — written as `../../../docs/plans/…` from the run directory — happen
to resolve unchanged from here, because this directory sits at the same depth. Nothing to fix,
but do not assume it of a future copy.

## What each one settled

**Step 3 — document-level qrels are not blind to chunking.** A BM25 lead-only ablation had
inferred that Leg A could not rank chunking configs, and Leg A was demoted on that inference.
The real dense pipeline separates configs at +0.137 nDCG@10 (CI [+0.051, +0.225]) and check 4
passes 10/10. The demotion is reversed. Recorded in
[long-doc-judged-set.md § 13.2](../long-doc-judged-set.md).

**Stage 1 — overlap buys nothing at any size**, and the pre-registered interaction contrast
was *structurally unanswerable* at its own bar rather than null. Also: realised median chunk
tokens explains the grid better than nominal size, reranking scrambles the config ranking
(r = +0.55), and semantic costs ~7× a `token_window` config rather than the 2× the brief
assumed. Recorded in [chunking-evaluation.md § Stage 1, run](../chunking-evaluation.md).

**Leg B/C pilots — the §7a oracle finally has a number**, on all 90 CDS topics: 55.4% of
judged pairs have their best-supporting section starting past token 1,024, against a ≥40%
bar. Leg C's resolvability expectation is falsified (11.86% via three keys vs 11.85% via
pmid alone) and two protocol amendments became non-optional.

**Leg B re-run — the legs disagree.** On recall@100, the one contrast where both legs
resolve, Leg A says coarse wins (+0.043) and Leg B says fine wins (−0.035), with
non-overlapping intervals. Both directions are partly artefacts of how each leg's queries
were built. This is the gating finding for the whole study and it is recorded in
[long-doc-judged-set.md § 14.5](../long-doc-judged-set.md).

## The discipline these reports keep

Worth stating once, because the plans lean on it: every threshold in the Leg B round is read
against a **power floor computed before the reading**. Fifteen readings are gated that way
and **six fail their own power check and are written as unresolved, not as nulls** — including
two where a naive Wald floor would have said "resolvable" because a proportion sat at p = 1.0
and its standard error degenerated to zero.

That habit came out of stage 1, where the pre-registered primary contrast turned out to have
an 80%-power floor of 0.213 nDCG against the 0.05 bar written for it — 4× too coarse, and
knowable in advance from its variance structure. A contrast's power floor is now computed
before its threshold is committed to.
