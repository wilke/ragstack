# Addendum Q to part 4: are the two-passage gold quotes answer-bearing? (EXPLORATORY; redacted public copy)

> **Redacted copy.** The private original stays with the outputs, off GitHub. Its sha256 is
> `376b9afba0de81bcbbb57abcece6b30c381fd7f6ce016b7d5ca62a07a5d15418` (frozen 2026-10-27T03:47:02Z; recorded in the private `PREREG.sha256` before any classifier call).
> Endpoint ports, file paths and the gateway owner's name are replaced by neutral descriptions;
> the product name in the prompt is `<PRODUCT>` as in the original; one four-word fragment of an eval question is removed; design, numbers and rules are unchanged.


**FROZEN, 2026-10-26, on the owner's go ("Approved and run").** Its sha256 and a UTC timestamp are in PREREG.sha256, recorded before any classifier call.

**Status: EXPLORATORY.** Nothing here changes a part-4 confirmatory verdict. Those stand as computed under the part-4 pre-registration ([PREREG-part4-confirmatory.md](PREREG-part4-confirmatory.md), sha a41394e3).

## Why
While checking the human grading page, the owner found a set-M question whose second gold quote describes the question's **premise** (a step the question itself mentions) and not information the question asks for. The answer covers what was asked, yet it can only be graded *partial*, because the rubric requires both parts of a two-part reference.

The cause is how the M questions were built: the prompt requires a question "whose full answer needs information from BOTH passages", and validation checks only that the quotes exist in the page and lie ≥ 1,500 characters apart. It cannot tell whether each quote is answer-bearing.

**What had been seen:** all part-4 results, and the one example above. No count of how common this is.

## What this measures
1. **Prevalence:** of the 372 valid M questions, how many have a gold quote that is not answer-bearing.
2. **Sensitivity:** whether H1–H4 (M, B4096, Sol, Holm over 4, both readers) read the same on the M questions whose two quotes are both answer-bearing.

## Classifier
Each (question, quote) pair is classified on its own: 744 pairs, two per M question.

**Primary: Claude Opus 5.5** via the gateway, temperature 0, max_tokens 1024, `fallback_model ""`.
- The limit is 1024 so a length cut cannot preferentially remove hard, ambiguous pairs from the clean set. If more than 1% of primary replies are length-cut, that is reported as a caveat on the prevalence.
- It is a different model from the question generator (Opus 4.8), the answer grader (GPT-5.6 Sol) and the readers.
- In the gateway owner's probe it answered directly at temperature 0.

**Secondary: GPT-5.6 Sol,** no temperature, max_tokens 4096, on all 744 pairs, for κ. Sol never saw the questions' passages, and this task does not involve any answer.

**Prompt (user; the product name comes from the private config):**
```
A user asked this question about <PRODUCT>:

Question: {question}

Here is a sentence from the <PRODUCT> documentation:
<quote>
{quote}
</quote>

Does this sentence give information that the question asks for?
- answer: it states (part of) what the user wants to know.
- setup: it only describes the situation, step or tool the question already mentions, not what the user asks to find out.
- neither: it is unrelated to the question.
Answer with one word: answer, setup, or neither.
```

**Parsing:** part 4's parser rule, with the labels answer, setup and neither, and the same negation and qualifier rule. The details:
- The explicit marker is a bold label, `Label: <label>` or `Answer: <label>`, where part 4 used `Grade:`. So "Answer: setup" parses as setup.
- A word of the same label never negates it, so "neither; neither" parses as neither.
- There are no synonyms: "set-up" and "answers" are unparseable.
- A quote that is `no_label` or unparseable makes its question not clean. Such quotes are counted separately.

**Gateway:** the part-4 safeguards apply, and the gateway owner gets a batch notice first. About 1,500 calls, roughly 0.4M input tokens.
- The output-token cap is 1M, counted from the start of this addendum.
- The failure stops (5 in a row, more than 20 in total) are counted for this addendum alone.

**Inputs gate:** the valid M ids in part4-questions.jsonl must equal the set-M ids in results-part4.json, and there must be 372. Otherwise the run hard-stops before any call.

## Human sample
- **By the owner** (agreed 2026-10-26): 40 (question, quote) pairs drawn at random from all 744 (seed `261026|Q`).
  - The owner does not see the model labels or the quote's position (A or B).
  - They are labelled with the same three labels, following the instructions shown to the owner on 2026-10-26: answer / setup / neither, the "the user wants to know…" test, "both → answer", and that a cut-off sentence still counts.
  - Reported as human–model κ with a 95% CI.
- **The grader's notes:** notes on M items flagging a reference problem (for example "Part 2 isn't needed for the question") are tallied by hand from the grades file's note fields, by item id only, and reported as counts. This is not in the script.

## Definitions and analysis
- **A question is "clean"** when the primary classifier labels both quotes `answer`.
- **κ** has 95% item-bootstrap CIs (10,000 resamples, seed 261026).
- **"Changes sign"** means opposite signs of the net share under some reader. A move from 0 to 0 is not a sign change. A stronger verdict on the clean set (not confirmed → confirmed) counts as *changed*.
- **Reproduction check:** the full-set recomputation must reproduce part 4's stored verdicts and, per reader, `p`, `p_holm` and `net_share` (`reproduces_part4`). If it doesn't, the sensitivity analysis is not read.
- **Reported:**
  - prevalence of `setup` and `neither` per quote and per question, overall and by stratum;
  - κ between Opus 5.5 and Sol (3-class, and answer vs not);
  - the human agreement, if it exists;
  - the two-quote positions in the page (is the setup quote more often A or B?).
- **Sensitivity:** H1–H4 recomputed on the clean M questions with exactly the part-4 procedure (same test, Holm, both-reader rule, δ). Reported beside the confirmatory result. Also reported under the secondary classifier's clean set, and the intersection.
  - Questions are dropped, never re-graded: no answer is graded again.
- **Reading rule (fixed now):**
  - If every hypothesis keeps its confirmatory verdict on the clean set, the part-4 conclusions are reported as robust to the gold defect.
  - If any verdict changes, the change is reported in the sentence, beside the confirmatory result. The confirmatory verdict is not replaced.
- **Power:** the clean set is smaller, so a non-confirmation there is reported as *unresolved on the clean set*, not as a reversal, unless its point estimate changes sign.

## Code
`python/scripts/eval/part4_addendum_q.py`, reusing part 4's gateway client, parser, pause and resume, and statistics. It went through a Fable review (approve-with-fixes, all applied) and was merged in PR #826. The run uses a clean checkout of main at **f8d7020**. Its outputs go to the private directory on the run host only.
