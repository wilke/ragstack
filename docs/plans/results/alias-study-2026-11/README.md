<!-- experiment: Alias study (OA-1000): glossary attachment and corpus growth | 2026-11-04/05 | CONFIRMATORY · JUDGE VALIDITY BELOW THRESHOLD -->
# Alias study (OA-1000): glossary attachment and corpus growth (2026-11-04 to 2026-11-05)

**Status: CONFIRMATORY, pre-registered, complete.** H1 confirmed, H1b confirmed, H2 not confirmed. Every
result carries the flag *judge validity below threshold (§13.5)*: the owner's 100-pair spot check agreed
with the frozen judge less often than the pre-registered 0.90 rule allows. The chunk-boundary control is
EXPLORATORY. This page is an orientation only; the record is [RESULTS.md](RESULTS.md), and where this page
and the record differ, the record wins.

| | |
|---|---|
| The record (every number, its artifact and key path) | [RESULTS.md](RESULTS.md) |
| Pre-registration (frozen 2026-11-04, sha256 `d54f4201…`, signed tag `prereg-alias-study-2026-11`) | [prereg/PREREG-alias-study.md](prereg/PREREG-alias-study.md), freeze record in [prereg/README.md](prereg/README.md) |
| Deviations D-1 to D-3 | [DEVIATIONS.md](DEVIATIONS.md) |
| Chunk-boundary control, exploratory | [ADDENDUM-2026-11-05-chunk-boundary-control.md](ADDENDUM-2026-11-05-chunk-boundary-control.md), plan and artifacts in [control/](control/README.md) |
| Presentation (17 slides, generated from the artifacts) | [the deck](../../../presentations/alias-study-2026-11.html), generator [`presentation/make_deck.py`](presentation/make_deck.py) |
| Lab book (dated status, decisions, open questions) | [LABBOOK.md](LABBOOK.md) |
| Public artifacts | [artifacts/](artifacts/), [control/artifacts/](control/artifacts/) (counts, rates and hashes only) |

## The question

Two questions, both the owner's (prereg §1), answered in two separately controlled families:

- **Family G, glossary.** A question names a concept by its full term; the passage that answers it uses
  only the paper's short form (a symbol, or an abbreviation defined once elsewhere). Does attaching the
  paper's merged glossary to its chunks make that passage findable? (H1; then H1b: is the gain larger on
  these records than on control records whose passage already carries the term?)
- **Family N, growth.** Does growing the collection with the study papers' citation neighbours cost more
  than growing it with the same number of random same-field papers? (H2)

The study informs two serving decisions (ship the merged glossary; keep the reranker default). It does not
make them.

**The measure.** found@10: a record counts as found when its gold chunk is in the top 10, or another
top-10 chunk, read alone, is judged to state the core of the answer. The balanced set is 900 records on 300
papers: 600 G records and 300 C records. One record is 0.17 points of the G records, 0.33 points of the C
records and 0.11 points of all 900.

## The headline, in records

Every line below carries *judge validity below threshold (§13.5)*. Sources are in RESULTS.md, *The answer*.

- **H1 confirmed.** The merged glossary raised the G records found from 591 to 597 of 600 (+1.00 point; 6
  gained, 0 lost; `layer2.placebo.G.found10`, `layer1.H1`). A placebo glossary (another paper's entries,
  same rendering rule) left it at 591.
- **H1b confirmed.** The G gain (+6 of 600) exceeded the change on the C records (1 of 300 lost, 300 to 299):
  +1.33 points, 7 papers non-zero and all positive (`layer1.H1b`). The C records started at 300 of 300, so
  C could not rise and the evidence against a general boost is one-sided; the placebo supports the
  alias-specific reading.
- **H2 not confirmed.** Gold-only, the neighbour arm found 776 of 900 against a random average of 795.5
  (−2.17 points, the direction gate passes); judged, 882 against 881.5 (+0.06 points, p 0.615;
  `layer2.cells.<arm>|none|default`). By the two-sided equivalence bound, no effect larger than 1.39 points
  (about 12.5 of 900 records).
- **Layer 2, descriptive.** Glossary: *not resolved* (6 of 600 against an action threshold of 12 of 600,
  beside H1 and H1b confirmed, D-3). Reranker: *keep the default (non-inferior to BM25)*, and ahead of BM25
  by 57 of 900 records on the neighbour arm. Growth: *not a concern* (`layer2.decisions`).
- **Chunk-boundary control, exploratory.** Re-judging stage-A pairs against boundary-respecting chunks
  flipped 28 of 249 agreed pairs against 7 of 249 in a test-retest null, and 12 of 51 tiebroken pairs against
  13 of 51; 24 of the 28 lost answers were found in an overlapping chunk (`control-judge.json`).
