# How should a retrieval index chunk full-length scientific articles?

> Drafting convention: every number cites its claim id inline, in the form
> value (`claim-id`), e.g. +3.49 (`oa-sections-ft-setting-a`). Add the claim to
> `claims.json` before writing the sentence. No status words, dates or roadmap. Every sentence that
> rests on a `JUDGE-DEPENDENT` claim carries the validity flag; every `MARKDOWN-ONLY` claim is
> resolved to a committed JSON before its sentence is drafted.

## Abstract

[Four to six sentences: the decision, the design, the result: +3.49 (`oa-sections-ft-setting-a`, private
pre-specification) and its random-cut caveat (`oa-sections-vs-random-cuts-setting-a`, unresolved in
production; `oa-sections-vs-random-cuts-vector-reranker`); the tie (`h2h-s-minus-ft512o64s`); reach +1.15
against plain windows (`reach-quote10-rates`); the judged 21.4 % / 4.6 % (`confirm-control-not-rate`,
`confirm-retest-not-rate`) only as the flagged mechanism; what it does not show.]

## 1. Problem

[Placeholder: the decision, the target population, why the obvious measurement differs
(`rescore-gap-k1-tok512-n1`, `position-oracle-past-1024`).]

## 2. Related work

[Placeholder: concede the prior art, then locate the gap. From `../bibliography.md` only; the inbox keys
named in OUTLINE.md §2 are promoted first.]

## 3. Design and what was frozen

[Placeholder: the four pre-registrations, the frozen items, the binding constraints
(`oa-margins-floor`, `s0-joint-power-at-80`).]

## 4. Data

[Placeholder: provenance and counts (`oa-corpus-articles`, `oa-decision-set-n`, `reach-units`,
`confirm-pairs`); see RUN-PACKAGE.md and each study's TEST-DATA.md.]

## 5. Results

[Placeholder: one subsection per finding, as OUTLINE.md §5.1–5.8, each number as value (`claim-id`),
each subsection opening with its label, n, judge-free or judge-dependent, and the power floor beside
every unresolved contrast.]

## 6. Threats to validity

[Placeholder: the judge first (`judge-validity-reweighted`), then quote coverage versus answer quality,
the single-section short-quote construction (`oa-no-quote-crosses-section`), single domain / embedder /
reranker / writer, the realised-size confound inside the section result, adoption before evidence, the
cost-decided tie (`h2h-decision-and-other-order`).]

## 7. Reproducibility

[Placeholder: pre-registration tags and sha256s, commit pins, seeds, model ids, image builds, what
another site must supply.]
