---
name: study-writeup
description: "Use when writing up, editing or publishing any of this project's empirical study material — a results write-up, the chunking-study artifact, a paper draft, a related-work or literature entry, a handoff or status update, or a claim that quotes a measured number. Also use when deciding WHERE something belongs: the record, the lab book, or a paper. Triggers include 'add this to the artifact', 'write up the results', 'paper', 'preprint', 'related work', 'literature review', 'cite', 'publish the study', 'update the handoff', or any request that would put a measured number in front of a reader."
---

# Writing up the studies

This project's empirical work lives in **three tiers with different update disciplines**.
Most write-up mistakes here are a tier confusion, so decide the tier before you write a
word.

| tier | where | discipline | contains |
|---|---|---|---|
| **The record** | `docs/plans/results/` | immutable once committed; a correction is a new file, never an edit | pre-registrations, `RESULTS-*.md` with provenance blocks, harness scripts, `artifacts/*.json` |
| **The lab book** | the study artifact (`~/Development/worktrees/phase0-rescue/artifact/chunking-study-report.html`) + `HANDOFF-*.md` | append-only, dated, superseded-not-deleted | status, dated addenda, decision ledger, what happens next, open questions |
| **The paper** | `docs/papers/<paper-id>/` | revised in place; must read as finished at every moment | problem, related work, design, data, results, threats to validity, reproducibility |

**The rule that decides the tier:** ask whether the sentence will be false in a month.
"Qwen is at 40 % and needs 37 more hours" is lab book. "Graded support reaches 0.92
reliability at 30 readings" is paper. A number with a date attached is lab book; a number
with a method attached is paper. If a sentence needs the word *currently*, *now*, *still*
or *next*, it is not paper text.

Never put status, progress, roadmaps or dated addenda in a paper. Never put a finished
argument only in the lab book. Never edit the record to make a paper read better — if the
record is wrong, add a correcting file and cite both.

## Every number in a paper must resolve to a committed artifact

This is the mechanism that keeps the tiers from drifting apart, and it is not optional.

1. Load-bearing numbers live in `docs/papers/<paper-id>/claims.json`, each with an `id`, the
   `statement` the paper asserts, the `value`, the repo-relative `source` artifact, and a
   `pointer` into it.
2. Prose cites the claim id, never a bare number typed from memory.
3. `docs/papers/check_claims.py` re-resolves every claim against the repo. Run it before
   any publish, any commit that touches a paper, and any time you quote a study number
   anywhere — including in chat.
4. A number that cannot be pointed at a committed artifact does not go in the paper. Measure
   it, commit the artifact, then write the sentence.

Prefer the committed **JSON** artifact as a source over a `RESULTS-*.md` file. Markdown is a
fallback for numbers that exist nowhere else, and that situation is a defect worth fixing.

## Citations must be verified, not recalled

A citation is a factual claim about someone else's work and this project has already been
burned by an unverified one. Before any reference enters `docs/papers/bibliography.md`:

- **Fetch it.** Confirm the title, author list, venue and year against the publisher page or
  arXiv abstract. A plausible-looking identifier is not evidence the paper exists.
- **Read past the abstract for anything load-bearing.** If a paper is cited as support for a
  claim the project relies on, get the PDF and check the methods. `pymupdf` is available in
  `/rag/envs/ragstack/bin/python3`; `WebFetch` on an ACL Anthology or arXiv PDF saves the
  file locally and the path is in the result.
- **Check the baseline whenever a paper reports a large multiple.** A "24× improvement" with
  baselines at 0.00–0.01 is a statement about the baseline, not the method. This exact case
  is recorded in the bibliography.
- **Record what it actually says and what it is worth**, in your own words, with the caveats
  the authors themselves state. Their limitations section is often the most useful part: it
  frequently names the gap your own work fills.
- Write the entry so a reader can tell **support** from **mention**. A paper that tested
  theses with an evaluation framework its own authors call unreliable is not the same kind
  of evidence as a peer-reviewed controlled comparison.

## Running a literature survey

A survey for a preprint is not "find some related work". It is a coverage argument, and it has
to hold up when a reviewer names a paper we missed.

**Fan out by area, not by keyword.** One agent per distinct literature, run concurrently, each
writing to its own file under `docs/papers/bib-inbox/` so they cannot collide; integrate and
dedupe afterwards. A single agent asked to cover everything returns the obvious papers and
stops.

**Give every agent the same three obligations**, because they are what make the result usable:

1. **Verify or drop.** Fetch the publisher page, confirm title, authors, venue and year.
   Anything unverifiable goes in a `COULD NOT VERIFY` section with the queries tried, never
   into the body.
2. **Answer our specific question of every paper.** Not "what is this about" but the thing our
   contribution turns on — *does it report realised chunk size or only nominal?*, *does it
   repeat an annotation on the same item?*, *is the unit a document or a span inside one?* A
   survey that cannot answer that paper by paper cannot establish a gap.
3. **Hunt for what contradicts us.** Every survey file ends with a section for work that
   challenges our result or our framing, and finding it is the best outcome, not the worst. A
   survey that returns only supportive work was run wrong.

**Two more sections earn their place in every survey file:**

- **Searched and did not find** — the queries that returned nothing relevant. This is the
  coverage evidence; without it, "no prior work does this" is an assertion.
- **Established names for what we invented** — before coining a term, find out whether the
  field already has one. Adopting it costs nothing; inventing a duplicate is a reviewer's
  easiest objection. This has already paid twice: `EM @ l tokens` for budget-normalised
  evaluation and "within-document retrieval failure" for the reach/containment phenomenon were
  both already in the literature when we were about to name them ourselves.

**Prefer the arXiv API and OpenAlex over web search.** The WebSearch budget is shared across
concurrent agents and runs out; `export.arxiv.org/api/query` and `api.openalex.org` are
authoritative for metadata and do not. Publisher access that reliably fails: ACM DL, dblp,
Springer, OpenReview, Semantic Scholar. Access that works: ACL Anthology, arXiv `/abs/`, PMLR,
CEUR, sigir.org, institutional repositories.

**Chase the citation graph both ways** from every anchor paper. Forward citations find the
critiques; backward citations find the pre-neural ancestor the modern work forgot to cite.

**Read the limitations sections.** They are the most useful paragraphs in the literature for
positioning, because they often name, in the authors' own words, the gap the new work fills.
The strongest framing this project has came from a negative result whose limitations section
listed three gaps that were exactly our three contributions.

## What a paper in this project must contain

Shape it venue-neutral for a preprint first; a venue's template is a later transformation,
not an earlier constraint.

1. **Problem** — the decision someone has to make, and why the obvious measurement answers a
   different question. State the target population explicitly; this project's findings are
   conditional on it.
2. **Related work** — from `bibliography.md`, every entry verified. Say what each result
   actually controlled for. If the published negatives share a confound with ours, say so
   rather than claiming they corroborate us.
3. **Design and constraints** — the pre-registration, what was frozen before data was seen,
   and the constraints that bounded the design (corpus, topic count, budget, endpoint
   politeness). Constraints are results too: "TREC CDS has 90 topics in total" is why the
   confirmatory family is what it is.
4. **Data** — provenance, counts, sizes, what is released and what cannot be. Point at
   `docs/plans/results/RUN-PACKAGE.md` rather than restating it.
5. **Results** — every claim cited to `claims.json`. Report powered nulls as nulls and
   underpowered contrasts as unresolved; never as the same thing.
6. **Threats to validity** — reliability is not validity, and if the human validation has
   not happened the paper says so in its own voice, not in a footnote.
7. **Reproducibility** — the run package, the commit pin, the seeds, the model ids, and an
   honest statement of what a different site must supply.

## Reporting discipline, inherited from the pre-registration

These are not style preferences. They are what makes the numbers usable.

- A null only counts where the design could have seen an effect. Quote the power floor beside
  every null; a contrast that was never resolvable must not be read as a null.
- `GATE-NOT-EVALUABLE` is a third answer alongside resolved and unresolved. Preserve it.
- Absent is not passed. A statistic that was never measured says *absent*, never a blank or a
  hopeful dash.
- Name the tokenizer any token count was measured in.
- Quote a sample size whenever it differs from the headline n.
- Exploratory is labelled exploratory, in the sentence, not only in the caption.

## Artifact publishing conventions

- The lab-book artifact is **republished to its existing URL** (pass `url`), never
  re-created. A new artifact orphans the link everyone has.
- Log every republish in the `README.md` beside the artifact source: date, what changed, new
  `sha256` prefix.
- Keep the title and favicon stable for the life of an artifact.
- A paper draft published as an artifact is a **separate** artifact from the lab book, with
  its own URL, and it carries no status line.

## Where the pieces currently are

| thing | path |
|---|---|
| pre-registration | `docs/plans/results/design/SPEC-confirmation-run-r3.md` |
| results write-ups | `docs/plans/results/stage0/RESULTS-*.md` |
| committed artifacts | `docs/plans/results/stage0/artifacts/` |
| reproducibility | `docs/plans/results/RUN-PACKAGE.md` |
| status | `HANDOFF-*.md` (newest supersedes) |
| lab-book artifact source | `~/Development/worktrees/phase0-rescue/artifact/chunking-study-report.html` |
| papers | `docs/papers/` — see its `README.md` for the roster |
| bibliography | `docs/papers/bibliography.md` |
| claim checker | `docs/papers/check_claims.py` |
