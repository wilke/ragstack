# Paper [X] — outline

**Working title.** [title]

**Target.** [arXiv preprint, venue-neutral. Optional: why this paper does not wait on anything else; omit for a gated paper]

**Audience.** [who builds or decides on the thing this paper measures; say what the result is
not specific to]

**The one-sentence claim.** [one sentence, with the conditions it holds under stated in it]

**The thesis in two numbers, from the same data** (optional; omit if the paper has no such pair)**.** [quantity A] reaches [value]
(`[claim-id-a]`). [quantity B] reaches [value] (`[claim-id-b]`). [One sentence on why the
difference is the object measured, not the data.]

**What this paper does not claim.** [the nearest over-reading of the result, and the reason it
is not licensed — e.g. reliability is not validity, a validation step has not happened]

**What this paper does NOT claim to have discovered** — established by the literature survey
on [date]. Every one of these is prior art and must be cited as such, not rediscovered:

| we do not claim | who has it |
|---|---|
| [finding we might be mistaken for having made] | `[bib-key]`, [what they controlled for and found] |
| [name we were about to coin] | `[bib-key]` — adopt their term |
| [estimator or method] | `[bib-key]` |

**The claim that survives.** [the narrower, better claim left once the table above is honoured:
what the field observed and worked around, and what nobody has measured]

Things the survey looked for and did not find, which is what the contribution rests on:

1. [searched and did not find — with the search that was run]
2. [searched and did not find]

---

## Sections

### 1. Problem

[The decision someone has to make, and why the obvious measurement answers a different
question. State the target population explicitly; findings are conditional on it.]

### 2. Related work

[From `../bibliography.md`, every entry verified. Say what each result actually controlled for.
If published negatives share a confound with ours, say so rather than claiming they corroborate
us. Concede generously, then locate the gap precisely.]

### 3. Design and what was frozen

[The pre-registration and what was frozen before data was seen. The constraints that bounded
the design (corpus, topic count, budget, endpoint politeness) are results too: state them here,
not in a limitations paragraph.]

### 4. Data

[Provenance, counts, sizes, what is released and what cannot be. Point at
`../../plans/results/RUN-PACKAGE.md` rather than restating it.]

### 5. Results

[Every number cites a claim id from `claims.json`; no bare numbers. Report powered nulls as
nulls and underpowered contrasts as unresolved, never as the same thing. Preserve
GATE-NOT-EVALUABLE as its own outcome. Quote the power floor beside every null.]

**5.1 [first finding].** [claims: `claim-id`]

### 6. Threats to validity

[Reliability is not validity. If the human validation has not happened, say so here in the
paper's own voice, not in a footnote. Name each competing reading of the main result.]

### 7. Reproducibility

[The run package, the commit pin, the seeds, the model ids, and an honest statement of what a
different site must supply.]

---

## Working rules for this draft

- Every number cites a claim id from `claims.json`. Run `../check_claims.py` before any commit
  that touches the draft.
- Read the `note` field of a claim before writing its sentence; a caveat there (differing `n`,
  a misleading key name, a non-comparable baseline) changes what may be said.
- No status, no dates, no roadmap; those live in the lab book. A sentence that needs
  *currently*, *still* or *next* is in the wrong tier.
- Absent is not passed. Where a statistic was not measured, the word is *absent*.
- Powered nulls and unresolved contrasts are different things and are never merged.
- Where the human read is pending, every sentence that depends on validity says so.
- Name the tokenizer for any token count.
- Quote n whenever it differs from the headline n.
- Label exploratory results as exploratory in the sentence itself.
