# Lab book: chunk size on a deployed documentation collection

This lab book covers parts 1–4 and addendum Q. It is **append-only**: entries are dated, and an entry
that is later wrong is superseded by a new one, never edited. The finished argument lives in
[RESULTS.md](RESULTS.md) and the
[part 1–3 record](../chunk-size-deployed-docs-2026-10-23/README.md). This page holds the status, the
history, the decisions and what happens next.

All times are UTC. "The owner" is the project owner. "The gateway owner" is the session that operates the
LLM gateway.

## Status, 2026-10-29

This supersedes the status of 2026-10-27, which is kept below.

| | |
|---|---|
| Part 4, confirmatory, model-graded | **complete.** H2, H3 and H4 confirmed; H1 not confirmed. Unchanged. |
| Addendum Q, exploratory | **complete.** It now has a second labeller (FNKM), descriptive. |
| Human grader validation (prereg §5) | **waiting for two more graders.** FNKM's per-grader result is descriptive only. |
| Grader set | **fixed** on 2026-10-29T17:54:39Z: FNKM plus the next two complete files. See [DEVIATIONS.md](DEVIATIONS.md). |
| Public record | provisional. #827 stays PROVISIONAL. |
| Rebuild recommendation | **provisional:** cap at 512 tokens, unchanged |

## Status, 2026-10-27

| | |
|---|---|
| Part 4, confirmatory, model-graded | **complete.** H2, H3 and H4 confirmed; H1 not confirmed. |
| Addendum Q, exploratory | **complete.** It includes the owner's 40 labels. |
| Human grader validation (prereg §5) | **pending.** 2–3 graders, both questionnaires |
| How several graders combine | **approved** by the owner on 2026-10-27, before any grade from them existed |
| Public record | this folder. It is provisional until the human validation is in. |
| Rebuild recommendation | **provisional:** cap at 512 tokens, as capped semantic or fixed 512 |

## Timeline

### 2026-10-23: parts 1–3, Addendum S, k=10 (exploratory)

- Ran on the deployed collection, read-only. The live semantic chunker turned out to ignore
  `chunk_size`: chunks reach 3,398 tokens, and 235 are over 512 (Addendum S). This was reported on #647.
- Exploratory results favoured smaller, capped chunks. The record has three weaknesses:
  - the primary grader was changed after its first results were seen (Opus 5 returned empty replies; the
    owner switched to Opus 4.8 and Sol);
  - 66 contrasts went uncorrected;
  - one model family generated, answered and graded.
- Record: [chunk-size-deployed-docs-2026-10-23](../chunk-size-deployed-docs-2026-10-23/README.md),
  EXPLORATORY, not citable (#823, merged 2026-10-24).

### 2026-10-24: the library is fixed, and part 4 is designed

- 00:45–00:49: #802 merged, which adds a sentence-preserving semantic cap and makes `chunk_size` usable
  as a token budget. #805 merged, which gives every entry point one chunker factory, and closed #647.
- The owner asked what would harden the conclusions. A confirmatory part 4 was designed, with these
  owner decisions:
  - non-inferiority margin δ = 7.5 points;
  - B2048 and k10 as secondary conditions;
  - Sol as the fallback generator;
  - human grades split between grader validation (part A) and decision validation (part B);
  - pause and resume on any budget limit;
  - Holm rather than a fixed sequence;
  - mid-p reported as a sensitivity only;
  - an output cap of 5M tokens, agreed with the gateway owner.
- The prereg went through four Fable reviews: three of the prereg itself and one of the code. The v1 review redesigned grading and analysis.
  Among other things, it caught that a 64-token Sol limit would have silently dropped about 24% of
  grades.

### 2026-10-25: frozen and run

- 03:40: the part-4 code merged (#824). 03:41: the prereg was frozen (sha `a41394e3`).
- 14:17: the first start hard-stopped at the manipulation gate, because SEM512 had 1,619 chunks against
  the 1,564 expected. The cause was the library fixes of 10-24. The owner chose to run with current
  main's cap ("Go with A"). Logged as D4-1, before any question existed.
- 14:18: the gateway owner restarted the gateway mid-run, and the run paused on 5 failures in a row. It
  was resumed after notices. From then on, the gateway owner pauses the run before any restart.
- An operator pause between reading and grading, then the run completed. Totals: 18,549 gateway calls,
  0.76M output tokens, and 20 failures counted, which does not cross the "more than 20" stop.

### 2026-10-26: human grading prepared; the reference-quote defect found

- The blinded 100-item grading page and the import were built (#825), with the "neither model" check
  fixed before any grade existed.
- While checking the grading page, the owner found that the graders see only the short reference quotes.
  In one M question, the second quote only restates the question's premise, so a complete answer can
  only be graded partial.
- Owner: "we have a problem". Addendum Q was designed to measure how common this is and whether H1–H4
  survive it. It was Fable-reviewed and merged as #826.

### 2026-10-27: addendum Q run; the owner's labels; this write-up

- 03:47: addendum Q frozen (sha `376b9afb`) and run: 1,522 calls, 2 failures.
- Result:
  - 28% of M questions have a quote that the classifiers call setup;
  - H2 holds on every clean set;
  - H3 is unresolved on the clean sets;
  - H4 changes on one classifier's clean set.
- The owner labelled the 40-pair human sample. The classifiers turned out lenient:
  - they accepted 5–6 of the 12 quotes the owner rejected;
  - the owner rejected 30% of the quotes, against the classifiers' 14%.
- The owner is recruiting 2–3 more graders for both questionnaires, and asked for a provisional write-up
  now, with the human checks marked pending. This folder is that write-up.

### 2026-10-29: the first additional grader (FNKM); the grader set is fixed

- **FNKM returned both questionnaires** on 2026-10-29: the 100-item part-4 page and the 40-pair
  addendum-Q page.
- **FNKM's part-4 result.** This is one grader, so it is **descriptive** under the approved rule. Only the
  majority verdict decides.
  - P_A = 22/40 = 0.55 [0.385, 0.707];
  - κ_B = 0.467 [0.233, 0.680];
  - κ against Opus 4.8 = 0.613 [0.14, 1.0], on 16 items;
  - per-grader verdict: **"not validated"**.
- **Addendum Q: the second labeller.** All descriptive and exploratory:
  - FNKM against Sol, answer vs not: κ 1.0;
  - FNKM against Opus 5.5: κ 0.91;
  - the owner against FNKM: κ 0.66 [0.35, 0.92]. They disagree on 5 of 40;
  - the non-answer rate: the owner 30% [17, 47] and FNKM 17.5% [7, 33], both on the 40 pairs; the
    majority 20% [8, 37] on n = 35, because the 5 disagreements have no majority.
- **The owner-only wording is now contested.** In the 2026-10-27 entry, and in *Open questions*, the
  record says the classifiers "turned out lenient" and that the defect depresses accuracy "about twice"
  the classifiers' rate. Both rest on the owner's labels alone. The second labeller does not support them:
  - FNKM agrees with Sol perfectly and with Opus 5.5 at κ 0.91;
  - FNKM's non-answer rate, 17.5%, is close to the classifiers' 14%, not twice it.

  Both statements are therefore **contested** until the remaining labellers are in. RESULTS.md is not
  edited here. Any correction to it will come as a dated change once the verdict is computed.
- **The grader set is fixed.** At 2026-10-29T17:54:39Z, after FNKM's file and before any other new
  grade, the owner fixed it: "at least two more graders". The set is FNKM plus the next two graders whose
  complete files arrive. The verdict is computed once, over those three. See
  [DEVIATIONS.md](DEVIATIONS.md). **The verdict waits for two more graders. #827 stays PROVISIONAL.**
- **Analysis provenance.**
  - The analysis ran at commit db97c28: a clean tree, citable, with the import and analysis scripts
    unchanged.
  - The private outputs stay on the host, under the study's private human-grading directory
    (`analysis-261029/`). Their receipt has sha256 prefix `38e63e1a`.

## Decision ledger

| when | decision | by | why |
|---|---|---|---|
| 10-24 | the tenant and collection stay anonymous in public; the residual link through status docs is accepted | owner | what must stay private is the eval set |
| 10-24 | δ = 7.5 points; B2048 + k10 secondary; Sol generator fallback; human split A/B | owner | prereg v2 |
| 10-24 | Holm over a fixed sequence; mid-p reported, never decisive | owner | every hypothesis stays testable whatever the order |
| 10-24 | output cap 5M tokens from the start of part 4 (addendum Q: its own 1M) | owner, agreed by the gateway owner | a compromise between the owner's 10M and the standing 3M |
| 10-25 | D4-1: run with current main's SEM512 (1,619 chunks) | owner ("Go with A") | that is what a rebuild deploys; no question existed yet |
| 10-26 | how the "neither model" check works when the human agrees with neither grader: item sets fixed | Fable review, logged | the prereg didn't name them; no grade existed |
| 10-26 | run addendum Q; the owner labels 40 pairs | owner ("Approved and run") | the reference-quote defect |
| 10-27 | write the record now, provisional; 2–3 more graders handle both questionnaires | owner | |
| 10-27 | how several graders combine (per grader, plus a majority verdict) | proposed | must be fixed before any grade exists |
| 10-27 | the multi-grader rule, as tightened by the Fable review of #827, approved | owner ("approve grader rule") | no grade from the new graders existed |
| 10-29 | the grader set: FNKM plus the next two complete files; the verdict is computed once, over those three | owner ("at least two more graders") | logged after FNKM's file, before any other new grade |

## What happens next

1. ~~The owner approves, or amends, the multi-grader rule.~~ **Done 2026-10-27:** approved without changes. See
   [DEVIATIONS.md](DEVIATIONS.md#proposal-2026-10-27-two-to-three-more-graders-for-both-questionnaires-pending-owner-approval-no-grade-exists-yet).
2. **Graders return their files.** Then:
   - import each one with `part4_human_import.py`, one output per grader;
   - import their addendum-Q labels with `part4_addendum_q.py import-human --out`;
   - compute human–human κ;
   - tally the grader notes that flag reference problems.
3. **Update this record:**
   - the grader-validation verdict;
   - the addendum-Q human agreement for each labeller and by majority;
   - a new dated status entry here;
   - drop PROVISIONAL from the status, or replace it with *grader-unvalidated* if that is the verdict.
4. **The rebuild.** If validation passes, recommend the 512 cap to the collection's operators. The
   per-collection cap needs a library change first: see the backlog below.

## Open questions

- How much does the reference-quote defect depress absolute accuracy? The owner's labels suggest about
  twice the classifiers' rate. The graders' labels will narrow that.
  - *2026-10-29: "about twice" is now **contested**. The second labeller's non-answer rate is 17.5%, against
    the owner's 30% and the classifiers' 14%. See the 2026-10-29 entry.*
- Is the B2048 result, where large chunks lose by 7–13 points, a property of this collection's very
  large live chunks, or general? This is a question for the cross-corpus study.
- Does H1 (512 over 128) depend on the budget? It is not confirmed at B4096. It is strong at k10, but
  confounded with the amount of text there. It is not significant at B2048. A budget sweep would answer
  it.

## Backlog connected to this study

These are not part of the record. They are tracked here so they don't get lost.

- **Semantic cap per collection (follow-up to #647).**
  - #802 and #805 fixed the uncapped path: semantic chunks are now capped at the embedding model's window
    (4,080 tokens for SFR), or at `CHUNK_MAX_TOKENS` when set.
  - A collection created as `semantic, chunk_size 512` still does **not** get a 512-token cap.
    `SemanticChunker` uses `chunk_size` only for its oversized-document fallback. `CHUNK_MAX_TOKENS` is
    server-wide, not per collection.
  - Rebuilding this collection as SEM512 therefore needs one of two changes, each needing the owner's go
    and a Fable review:
    - semantic honours `chunk_size` as a token cap;
    - or the API warns or refuses when `chunk_size` is set on a semantic collection without a cap, and the
      manifest records the cap actually applied.
- **Dev-collection chunk-count mismatch.** The deployment-management session ingested the owner's 336
  source files into a private dev collection as `fixed_token 512, overlap 0` through the GoWe path. All
  336 documents were ingested, with no failed items, giving **1,023** chunks. The offline T512o0 arm in
  parts 2–4 has **1,113** chunks. That arm chunks the page text reassembled from the live collection's
  spans. So the page count is not the cause. The candidates are:
  - the source files' text differs from the live reassembled text (extraction or normalisation);
  - the tools-image version on the ingest path, against the library commit the trial ran at;
  - the tokenizer or window rule on that path.

  Until the cause is known, "rebuild as fixed 512" may not reproduce the arm that was tested. Comparing the
  two needs a read share on the dev collection from the owner.
- **Acknowledgement to the deployment-management session.** That session built the two dev collections
  for this study and reported back. Our acknowledgement was never sent: the permission classifier blocked
  the message. It should thank the session, report the chunk-count mismatch above, and point its
  re-chunk planning to this record and to the #647 follow-up.
