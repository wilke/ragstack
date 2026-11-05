# Paper C — alias glossary attachment and citation-neighbour growth

Front matter for the paper tier. The argument lives in `OUTLINE.md`, the prose in `DRAFT.md`, every
number in `claims.json` (checked by `../check_claims.py`). Process rules are the `study-writeup` skill.

**Working title.** A glossary makes alias passages findable, and the gain is small: a pre-registered
test of glossary attachment and citation-neighbour growth in scientific-paper retrieval.

**The argument.** When a question names a concept by its full term and the answering passage uses only
the paper's short form (a symbol, or an abbreviation defined once elsewhere), attaching the paper's own
glossary to its chunks makes the passage findable: a confirmed, alias-specific gain under a frozen
pre-registration, but one of a single percentage point under the served hybrid-plus-cross-encoder path
(`h1-net-points`), on a corpus where 591 of 600 such records were already found by some chunk
(`h1-g-found-counts`), and larger only where the cross-encoder is absent (`h1-other-retrievers`).
Growing the collection with the papers' citation neighbours costs no more judged reach than growing it
with the same number of random same-field papers (a powered null, bounded at 1.39 points;
`h2-net-points`, `h2-equivalence`), although it costs more gold-chunk reach (`h2-gold-net-points`), a pattern consistent with neighbours displacing the gold
chunk and supplying an answer in its place. Every judged figure rests on an LLM judge whose agreement
with the single human reference fell below the pre-registered 0.90 threshold
(`judge-validity-reweighted`), and the paper reports the judge-free gold-only figure beside every judged one rather than treating either as the truth.

**Target population.** Questions that use a glossary entry's primary term, over passages in open-access
scientific full texts that carry only the short form; a collection of 1,000 papers chunked at 512 tokens
(`corpus-chunks`) and grown by 2,423 added papers per expanded arm (`added-papers-per-arm`); a served path of hybrid BM25 + dense retrieval with a cross-encoder
reranker at top 10; records drafted and verified by language models with a 40-record human check (`record-validity`). The
findings are conditional on that population and the paper says so where each one is stated.

**State.** Outline and two sections drafted (Problem; Design and what was frozen). `claims.json` holds
the load-bearing numbers and passes the checker. Related work is surveyed in
`../bib-inbox/14-alias-glossary.md` and cited in `OUTLINE.md`; entries enter `DRAFT.md` only after
promotion to `../bibliography.md`.

## Republish log

None. No artifact has been published from this paper.
