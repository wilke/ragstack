# Structure-preserving ingest: one contract, three extractors

**Status:** plan, 2026-09-22. Not started.
**Near-term goal (owner):** the chunking session's corpus — **PDF and HTML**.
**Strategic bulk:** JATS, 1.23M open-access articles where structure is free.

---

## 1. The problem, stated once

A chunker that cuts a sliding window through a document destroys the document's
own structure, and the structure is the thing a reader uses to judge relevance.
`docs/plan-c5.md` proposed `CHUNK_SECTION_AWARE` and it was never built, so today
every method in `CHUNK_METHODS` is structure-blind.

The instinctive fix — one ingest record per section — **does not work**, and the
reason is worth recording because it is not obvious:

> One record per section makes each section its own **document**. `chunk_index`
> restarts at 0 per section, `link_neighbors` groups per document so it will not
> link across section edges, and `doc_id` stops meaning "paper". `CompareView`'s
> neighbour rendering stops dead at every boundary.

(Neighbour *expansion* survives — `_expand_sources` calls `get_chunks(ids=...)`
following `prev_chunk_id`/`next_chunk_id`, and is not scoped to `doc_id` — but
`chunk_index`, the per-document grouping, and document identity do not.)

So structure has to be carried as **per-chunk metadata on a whole-document
record**, not as a record boundary.

## 2. How the three document types differ

| | JATS XML | HTML (arXiv, rendered) | PDF |
|---|---|---|---|
| structure | **explicit, exact** — `<sec>`, `<title>`, `<table-wrap>`, `<fig>`, `<ref-list>` | **explicit**, publisher-variable tags | **absent** — inferred from layout and text |
| metadata | **in the file** (`<article-meta>`): DOI, title, authors, journal, pub-date, pmid/pmcid | `<meta>` + the arXiv API | **recovered**: filename → DOI regex → Crossref |
| references | `<ref-list>`, per-reference elements | list markup | prose blob, heuristic only |
| today | `jats.py` — section-aware, titles kept as markdown headings, tables/figures split out | **nothing** | `PdfLoader` + `enrich.py` + `doi_metadata.py` + boilerplate heuristics |

**Only the PDF column requires inference.** That is the whole shape of the work:
two extractors read structure that is already there, one has to guess.

## 3. One workflow, three extractors

Not three workflows. This is already the de facto shape — `jats-ingest.cwl` and
`pdf-ingest.cwl` differ **only in the extract step**, then both run
`embed_shard.py` → `load_embeddings.py`. The seam exists; what is missing is that
the PDF path's intermediate JSONL carries no structure, and there is no HTML
producer at all.

So: **one normalized extract record, three producers into it.**

```
JATS XML ─┐
HTML ─────┼──► normalized extract record ──► chunk ──► embed ──► index
PDF ──────┘         (text + structure + metadata + provenance)
```

The record needs, per document:

* `text` — the body, in reading order
* `sections` — ordered spans over that text: `(start, end, title, depth)`
* `metadata` — the `chunk_metadata.json` fields the source can supply
* `metadata_source` — **already a declared field**; say which tier supplied it
* `excluded` — reference-list and supplemental spans, carried out-of-band (§5)

Adding a fourth source later is one extractor, not a pipeline.

## 4. Where metadata comes from — three tiers, existing precedence

The rule already implemented by `derive_doi` / `derive_year` applies unchanged:
**declared beats inferred**, and every arm is bounded by `coerce_*`.

| tier | source | confidence |
|---|---|---|
| 1 | JATS `<article-meta>` | authoritative, free |
| 2 | HTML `<meta>` + arXiv API | authoritative for arXiv ids |
| 3 | PDF: filename → DOI regex in text → Crossref (`doi_metadata.py`, #602 resolves at upload) | recovered, fallible |

`contracts/schemas/chunk_metadata.json` (#604) is already the common target and is
**enforced at ingest**, with the ES mapping derived from it. `section_title` is
already declared: *"the source document's own heading for the section this chunk
came from"*.

> **`section` is NOT that field.** It is a boilerplate-filter verdict —
> `references` / `license` / `acknowledgements`, never `body`. The schema says the
> two are unrelated. Putting a heading in `section` collides with filter queries.

## 5. References and supplemental material

**The exclusion already exists.** `--boilerplate` has `off` / `flag` / `drop`
(`filter_from_mode`), and production runs `flag`. Keeping references out of the
vector index is **a config change, not code**.

**The routing does not exist**, and that is the actual work. `drop` discards;
#593 deliberately chose flag-over-delete for literature for exactly that reason.

The reframing that matters: **references are not boilerplate to suppress, they are
a different data type belonging in a different store.** Citations are already
parsed — `extract_citations()` in `enrich.py`, `n_citations` in the contract — and
`graph_backend: memory | neo4j` with `extract-graph.cwl` already exists. Paper →
paper citation edges are what a property graph is for; today they are parsed and
then thrown away at a filter.

Proposal: the extract record carries reference and supplemental spans out-of-band;
the vector leg never sees them; a separate sink writes them to Postgres (rows) and
/ or the graph (edges). Which of the two is an owner decision — Postgres suits
"give me this paper's reference list", the graph suits "what cites what".

## 6. Constraints that bound any design

1. **`sentence_spans` is frozen.** The chunking study keys its labels by sentence
   index and asserts `chunkers.py` has not moved (`s0_common.EXPECT_COMMIT` +
   `pin_repo()`, plus a `git diff` assertion in the labelers). A section-aware
   chunker that changes sentence segmentation invalidates every existing label.
   Comments in that file are safe; behaviour is not.
2. **Chunk method is collection identity** (ADR-0002). A new method mints a new
   collection; nothing is converted in place.
3. **The chunk-metadata contract is enforced at ingest.** A new field is not
   rejected (`additionalProperties: true`) but gets no declared ES mapping and is
   typed by whichever document lands first. Declare, don't improvise.
4. **`chunk_index` must be an `int`.** The schema records that
   `scripts/ingest_chunks.py` accepted a caller-supplied value verbatim and has
   already written it as a **string** to a live collection.

## 7. Plan, in the owner's order

**Stage 1 — HTML extractor (near-term goal, and the cheap half).**
arXiv's rendered HTML carries real section tags, so this is extraction, not
inference. Produces the normalized record with true `sections`. No new workflow:
register an `.html` loader and an extract step alongside `pdf-extract`.
*Note `DEFAULT_INGEST_SUFFIXES` is `(.pdf, .txt, .md, .jsonl)` — no `.html`, no
`.xml`.*

**Stage 2 — section-aware chunking over the normalized record.**
A chunker that takes `sections` and cuts **at** boundaries, falling back to the
configured method **within** a section. Stamps `section_title` per chunk. Must not
touch `sentence_spans` (§6.1). This is the piece `CHUNK_SECTION_AWARE` named and
never delivered.
*Supporting evidence from the chunking session's own confirmation run: overlap
returned a powered null — 61,559 extra vectors for a recall@100 change of exactly
0.0000 — so cutting at a boundary costs nothing overlap was paying for.*

**Stage 3 — PDF structure, heuristic + filter.**
The genuinely hard, low-coverage end: infer headings from font size, numbering and
position, then filter. Lands last because it is the only tier that can be wrong,
and because Stage 2 must exist for it to feed.
*Design input from the same run: the largest evidence-bearing class is "other" at
**39.1%** of wins — untitled body leads and case presentations — ahead of
discussion (28.9%), with abstract last (3.1%). **Untitled sections must be
first-class, not a fallback bucket.** And **55.4%** of best-supporting sections
start past token 1,024, so anything privileging head matter optimises for an
eighth of the evidence.*

**Stage 4 — references and supplemental to their own store.** §5.

**Stage 5 — JATS onto the same contract.**
Largest corpus, smallest effort per document: `jats.py` already keeps section
titles as markdown headings inline. Deliberately **not** first — the owner's
near-term corpus is PDF and HTML, and JATS is not blocked on any of the above.

## 8. Open questions

1. **Postgres, graph, or both** for references (§5).
2. **Supplemental material**: excluded entirely, or its own collection? It is
   often where methods detail lives.
3. **Does the section-aware chunker get a new `CHUNK_METHODS` entry**, or is it a
   modifier on existing methods? Identity implications either way (§6.2).
4. **#580** — the GoWe worker feeds every input to `PdfLoader`, so `.md` behaviour
   depends on the installed PyMuPDF. An `.html` loader hits the same seam and
   should be designed with that issue's fix, not around it.
