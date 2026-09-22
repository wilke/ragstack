# `pubtator3/` — does NCBI PubTator3 supply the typed entity layer?

2026-09-15. A **bounded data experiment**, not a scored evaluation — there is no
pre-registration here, because nothing was being contrasted. It measures how much of
`/rag/oa/corpus/discovery.jsonl` (1,441,791 documents) the 2026-08-17 PubTator3 bulk release
covers, where it does not, and whether a separate AMR vocabulary is needed on top.

| file | what |
|---|---|
| [`RESULTS-pubtator3-coverage.md`](RESULTS-pubtator3-coverage.md) | the write-up |
| [`report-pubtator3.json`](report-pubtator3.json) | every number in it, machine-readable |
| `*.py`, `*.sh` | the harness, verbatim from the run directory |

**Headline:** yes, except AMR genes and except 2024+. Species coverage is 99.6–100.0%
through 2023 on the three journals that carry no MeSH at all (Frontiers in Microbiology,
Microorganisms, Antibiotics) — the gap MeSH leaves is exactly the gap PubTator fills. But a
known AMR gene family appears in the text and *not* in PubTator's gene annotations in 68.7%
of (document, family) pairs over 57,267 microbiology full texts, and where an allele *is*
resolved the identifier is a per-genome NCBI Gene locus record rather than an allele
identity. CARD's ARO (CC-BY 4.0 for the ontology download only) covers 92.2% of the AMR
surface strings the corpus actually uses and is fit for purpose behind a normalisation layer.

**Three things a reader should hold onto before quoting a number:**

- **The bulk files and the live API disagree for 2024+ documents, badly** (§4.2). Every
  coverage figure in §2–§3 is the bulk release, because that is what a pipeline ingests.
  Re-measure §4.2 against whichever release you actually load; the lag is not a constant.
- **The AMR instrument was wrong twice before it was right**, and §6.1 names both failures
  and the numbers they produced (78.0% → 71.6% → 68.7%). Only the v3 figure is reported;
  `amr_terms3.py` is the version that produced it. `amr_terms.py` and `amr_terms2.py` stayed
  in the run directory.
- **"Mentions per document" is not measurable from the bulk files.** A bulk row is one
  *(pmid, concept id)* with surface forms pipe-joined, so every density figure here is
  *distinct concepts* per document. The mention-level resolution rates in §5.2 come from the
  API sample, not from the bulk files.

The `.gz` sources (5.21 GiB), the `ft_raw/` API responses, the per-entity `*_bypmid.tsv`
aggregates and the 57,267-row AMR scan stay in **`/rag/data/pubtator3/`** and are not
committed; they regenerate from the scripts here plus a re-download.
