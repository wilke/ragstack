# Deviations and decisions, part 4 and addendum Q (redacted public copy)

> **Redacted copy** of the part-4 section of the private deviation log, which stays with the outputs, off
> GitHub. Hosts, file paths, process ids and the names of other sessions are removed or replaced. "The
> gateway" is the LLM gateway that the generator, grader and classifier calls went through, and "the
> gateway owner" is the session that operates it. Entries are in time order, with their original UTC
> times. Numbers are as logged at the time. The record's tables come from [`artifacts/`](artifacts/), not
> from here.

Pre-registration: [prereg/PREREG-part4-confirmatory.md](prereg/PREREG-part4-confirmatory.md), sha
`a41394e3`, frozen 2026-10-25T03:41:39Z. Addendum Q:
[prereg/PREREG-part4-addendum-Q.md](prereg/PREREG-part4-addendum-Q.md), sha `376b9afb`, frozen
2026-10-27T03:47:02Z.

## D4-1, 2026-10-25T14:17:21Z: SEM512 expected chunk count 1,564 → 1,619 (owner: "Go with A")

- **What happened:** the first start (commit c35d1ce, clean checkout) hard-stopped at the §3 manipulation
  gate before any gateway, reader or grader call: 0 calls, 0 tokens. SEM512 built 1,619 chunks against the
  expected 1,564. L (1,265), T128o0 (3,955), T512o0 (1,113), T1024o0 (657) and the 76-page M census all
  matched.
- **Cause:** the 1,564 came from Addendum S and part 3, which ran on the library at dec0ae1 (v1.6.14).
  Three chunker changes landed between dec0ae1 and c35d1ce:
  - #802, a sentence-preserving semantic cap (the fix for the defect these trials found);
  - #804, Punkt abbreviations;
  - #816, nltk in the images.

  They change only the capped semantic arm.
- **Decision:** run part 4 with current main's SEM512, which is what a rebuild would deploy. The gate's
  expected SEM512 count in the private config becomes 1,619. The prereg text is not edited.
- **Why this is not data-dependent:** no part-4 question, answer or grade existed when the decision was
  made. Only the chunk counts had been seen.
- **Consequence for reading H2:** H2 (SEM512 − L) is about the current implementation of the cap, not the
  one Addendum S measured.

## Incident note, 2026-10-25T14:20:19Z: gateway restart during generation (not a deviation)

- At 14:18:58Z the gateway owner restarted the gateway to add models, believing part 4 was idle. It
  wasn't. Eight in-flight and immediately following calls failed (4 RemoteProtocolError, 4 ReadError).
  After 35 calls the run paused on "5 failures in a row". It resumed under the same frozen design after
  notices.
- The gateway owner confirmed that the cause was on their side. The run's persisted failure count was
  **not** edited, because the state and config are part of the record and the resume sha checks cover
  them.
- From then on, the gateway owner pauses part 4 (via the PAUSE file) before any gateway or tunnel
  restart.
- **Outcome:** the run finished with exactly 20 failures counted (these 8, eleven HTTP 502s and one 599).
  The pause threshold is more than 20, so the threshold was not crossed and no discounting decision was
  needed.

## Note, 2026-10-26T03:20:30Z: human grading page and import built (by design, not a deviation; prereg §7 item 10)

- The export was written from the complete run with `--resume --batch-calls 0 --export-human`, so no
  gateway call could be made. The results file changed only in provenance and run segments.
- The export has 100 items: 40 in part A, and 30 part-B pairs (60 answers). The key is in a separate file.
- The grading page is self-contained and works offline. It carries no arm, condition, reader or grade
  information.
- The import, `python/scripts/eval/part4_human_import.py` (PR #825), was built before any human grade
  existed.

## Decision, 2026-10-26T03:35:06Z: the human "neither model" check (fixed before any human grade existed)

Prereg §5 says "κ < 0.6 against both" but doesn't name the item sets. They were fixed at this point:
- **against Sol:** κ_B, the pre-registered κ;
- **against Opus 4.8:** κ(correct vs not) over the part-B items that Opus also graded (16 items, 8 pairs).

The check needs at least 10 Opus part-B items to be evaluable. Part-A κ against Opus is reported only as
the complement of P_A: Sol and Opus disagree on every part-A item by construction, so κ there can't
measure agreement. κ CIs bootstrap items, not pairs. The page's instructions are the four model-grader
definitions verbatim.

## Note, 2026-10-27: addendum Q run, and the owner's 40 labels

- Addendum Q (exploratory) was frozen and run from a clean checkout at f8d7020: 1,522 gateway calls,
  2 failures, no length cuts. Opus 5.5 returned an empty reply on 15 of 744 pairs. Under the frozen
  parsing rule those count as `no_label`, so 8 questions are not clean for that reason alone.
- The owner labelled the 40-pair human sample on 2026-10-27. Those labels were imported and folded into
  the addendum-Q results. They are one labeller's labels, reported as such.

## Proposal, 2026-10-27: two to three more graders for both questionnaires (PENDING owner approval; no grade exists yet)

The owner is recruiting two or three more graders. Each will grade both the 100-item part-4 page and the
40-pair addendum-Q page. The frozen rules name one human grader, plus "a second person grades [part A]
if available". Nothing says how three graders combine. This proposal fixes that **before any of those
grades exists**:

**Part 4 (grader validation, frozen §5).**
1. Each grader is analysed alone with the frozen rules (P_A, κ_B and the neither-model check), and every
   grader's result is reported.
2. Human–human agreement: pairwise κ(correct vs not) on part A and on part B, with 95% item-bootstrap
   CIs, and Fleiss' κ when there are three graders.
3. **The validation verdict** uses the item-wise **majority** label (correct vs not) across the graders.
   With an even number of graders, an item they split on has no majority. Such items are excluded from
   the verdict and counted.
4. If the per-grader verdicts disagree with each other, the record says so beside the majority verdict.
5. Grader notes that flag a reference problem are tallied by hand, per grader and by item id.

**Addendum Q (exploratory).** Each labeller, the owner included, is compared with both classifiers. The
record reports human–human κ, and the item-wise majority label of the human labellers against each
classifier. The non-answer prevalence on the 40 pairs is reported per labeller and by majority.

**Code consequence.** The addendum-Q import wrote to one fixed file, so a second labeller would have
replaced the first. It gains `--out` and an export-id check. The owner's import was kept under its own
name before this change.
