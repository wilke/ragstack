# Structure-preserving ingest: one contract, four extractors

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

(Neighbour *expansion* survives, with one caveat. The server-side
`context_window` walk is `expand_context` in `retrieval/retriever.py`: it hops
`window` steps each way, and each hop's ids come from `prev_chunk_id` /
`next_chunk_id` on the chunks already in hand — the function's own comment says
"ids are opaque uuid5s, not derivable from `chunk_index`" — fetched in one
batched `store.get_chunks(ids, filters)` per hop. So it follows **ids**, not
`doc_id`, and would cross a section boundary if the links were stitched. The
caveat: the walk is **scoped by the request's filters** — a neighbour the caller
cannot read under those filters is simply not returned and the walk stops there.
Stitched links only help if every section-record is visible under the same
scope. `chunk_index`, the per-document grouping, and document identity do not
survive regardless.)

So structure has to be carried as **per-chunk metadata on a whole-document
record**, not as a record boundary.

## 2. How the four document types differ

Markdown is a fourth type, not a PDF fallback — added 2026-09-22. `.md` is
already a registered loader (`loaders.py`: `registry.register(".md", text)`),
and unlike PDF it carries **explicit structure**: `#`/`##`/`###` headings are
cheaper to parse than either HTML tags or the PDF heuristic. It belongs beside
JATS and HTML in the "already there" half of this table, not beside PDF in the
"must infer" half — see #580 in §8.4 for why today's routing gets this backwards.

| | JATS XML | HTML (arXiv, rendered) | Markdown | PDF |
|---|---|---|---|---|
| structure | **explicit, exact** — `<sec>`, `<title>`, `<table-wrap>`, `<fig>`, `<ref-list>` | **explicit**, publisher-variable tags | **explicit** — `#`/`##`/`###` headings | **absent** — inferred from layout and text |
| metadata | **in the file** (`<article-meta>`): DOI, title, authors, journal, pub-date, pmid/pmcid | `<meta>` + the arXiv API | none in-band — frontmatter if the source adds it, else recovered like PDF | **recovered**: filename → DOI regex → Crossref |
| references | `<ref-list>`, per-reference elements | list markup | a heading-delimited section like any other — no special markup | prose blob, heuristic only |
| today | `jats.py` — section-aware, titles kept as markdown headings, tables/figures split out | **nothing** | registered, but the GoWe worker feeds it to `PdfLoader` regardless (#580) — its structure is currently discarded before anything can read it | `PdfLoader` + `enrich.py` + `doi_metadata.py` + boilerplate heuristics |

**Structure and metadata are separate axes, and Markdown splits them** — the one
case here where "has structure" doesn't imply "has metadata". A markdown record
with no frontmatter needs PDF-style recovery for authors/DOI/year even though its
sections are free. The normalized record (§3) has to let a document be
structure-tier-1 and metadata-tier-3 at once.

**Three of the four columns require no structural inference.** That is the whole
shape of the work: three extractors read structure that is already there, one has
to guess.

## 3. One workflow, four extractors

Not four workflows. This is already the de facto shape — `jats-ingest.cwl` and
`pdf-ingest.cwl` differ **only in the extract step**, then both run
`embed_shard.py` → `load_embeddings.py`. The seam exists; what is missing is that
the PDF path's intermediate JSONL carries no structure, Markdown's structure is
discarded before it reaches one (§2), and there is no HTML producer at all.

So: **one normalized extract record, four producers into it.**

```
JATS XML ─┐
HTML ─────┤
Markdown ─┼──► normalized extract record ──► chunk ──► embed ──► index
PDF ──────┘         (text + structure + metadata + provenance)
```

The record needs, per document:

* `text` — the body, in reading order
* `sections` — ordered spans over that text: `(start, end, title, depth)`
* `metadata` — the `chunk_metadata.json` fields the source can supply
* `metadata_tier` (or similar) — which of §4's three tiers supplied the
  metadata. **Not `metadata_source`**: that field is already declared, but its
  declared meaning is narrower than it sounds — "the operator-facing twin of
  `doi_enriched_from`, carrying the same service list", i.e. which DOI-enrichment
  services touched the record. Reusing it for the extraction tier would overload
  a field whose consumers expect a service list. Either extend its declared
  semantics deliberately or declare a new field (§6.3); don't improvise on the
  name.
* `excluded` — reference-list and supplemental spans, carried out-of-band (§5)

Adding a fifth source later is one extractor, not a pipeline — as Markdown
becoming the fourth already shows: it cost a table column and a stage, not a
redesign.

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
(`filter_from_mode`), and the API side has the same switch as
`boilerplate_drop: bool = False` beside `boilerplate_detection_enabled: bool =
True` (`config.py:438-444`). Both planes default to **flag** semantics — detect
and stamp `section` / `is_boilerplate`, remove nothing — and no tenant sets
either knob explicitly (hackathon, dev and demo all leave `BOILERPLATE*` unset;
the `action=flag` in dev's startup log is the default, not a decision). Keeping
references out of the vector index is therefore **a config change, not code**:
`--boilerplate drop` on the CLI tools, `BOILERPLATE_DROP=true` on the API.

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
5. **One extractor library, four callers, not four extractors.** Owner
   constraint, 2026-09-22, and the exact defect #609 was — "two chunker
   builders" turned out to be five, with three different token-budget policies,
   because convenience functions kept getting written beside their first caller
   instead of in the shared module. That consolidation is **not finished**:
   `chunker_config.build_chunker` is the one factory the three bulk tools share
   today, but the API still carries two builders of its own (`deps._chunker_for`
   and `deps._build_chunker`), and folding them into one `chunker_for` is the
   deferred follow-up in `docs/plans/chunking-one-factory.md` (PR #612, not yet on `main`) §8. §3's four
   extractors need the one-library shape from the start — not a retrofit once a
   second caller exists, which is precisely how the chunker side ended up with
   five.

   The precedent for *where* is already in the tree and already has two
   callers: `ragstack.ingestion.jats` / `ragstack.ingestion.loaders` (not
   `scripts/`) is what `scripts/jats_extract.py` imports, and
   `loaders.default_loader_registry()` is the same factory `api/deps.py` calls
   for the local (non-gowe) in-process ingest path. Every new extractor —
   Markdown's heading parser (§7 Stage 1a), the HTML extractor (§7 Stage 1b),
   the PDF heuristic (§7 Stage 3) — goes in `ragstack.ingestion`, never inline
   in a `scripts/*.py` tool or embedded in a CWL `InlineJavascriptRequirement`.
   CLI tools and CWL steps (which just containerize the CLI tools) get this for
   free by importing the module; nothing CWL-specific to design.

   **"Chunking experiments" names a caller explicitly**, and it is not
   hypothetical — the chunking-study session already needed exactly this
   discipline. Their first comparison script built a lookalike embedder instead
   of reusing `ingest_shard._build_embedder`, and was corrected to call the
   tool's own builder so the comparison ran on the real path rather than a
   stand-in (`docs/plans/results/semantic-vs-pooled-2026-09-18.md`). The same
   rule applies to every extractor here: an experiment script imports
   `ragstack.ingestion`, it does not re-implement a heading parser to save an
   import.

## 7. Plan, in the owner's order

**Stage 1 — Markdown and HTML extractors (near-term goal).**
Two sub-stages, ordered by cost, both producing the normalized record with true
`sections`:

* **1a — Markdown.** The cheaper of the two, possibly free. The `.md` loader
  already exists (`loaders.py`); the only code needed is a trivial
  heading-span parser (`#`/`##`/`###` → `(start, end, title, depth)`, no DOM) and
  #580's routing fix, so the GoWe worker path stops discarding that structure by
  feeding every `.md` to `PdfLoader` regardless of extension (§2, §8.4). Do this
  fix *as* Stage 1a, not before it — there is no reason to fix the routing and
  then not also finish the parser it was blocking.
* **1b — HTML.** arXiv's rendered HTML carries real section tags, so this is
  extraction, not inference, but it needs an actual parser (DOM/tag-walk) where
  Markdown needed a regex. No new workflow either: register an `.html` loader
  and an extract step alongside `pdf-extract`.

*Note `DEFAULT_INGEST_SUFFIXES` is `(.pdf, .txt, .md, .jsonl)` — `.md` is present
but, per §2, its structure is currently thrown away before it reaches a chunker;
there is no `.html` entry at all.*

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

1. ~~Postgres, graph, or both for references (§5)?~~ **Decided (owner,
   2026-09-22): both.** Both are sinks off the same parsed
   `extract_citations()` output — one write, two destinations — so this is
   confirmation to build both writers, not a choice between them.
2. ~~Supplemental material: excluded entirely, or its own collection?~~
   **Decided (owner, 2026-09-22): kept, not excluded — labeled.** Not dropped
   like references (§5), and not silently left in the main body stream either.
   Needs a field on the normalized record (§3) and a corresponding declared
   chunk-metadata field — `is_supplemental`, or a value alongside the existing
   boilerplate verdicts (`references` / `license` / `acknowledgements`) — so a
   caller filters it in or out per query rather than the ingest-time choice
   being final. The label is the deliverable; unlike references there is no
   separate sink to move it to. "If we can label it" is load-bearing: JATS and
   arXiv HTML (§2) have an explicit supplemental section to detect;
   PDF does not, so Stage 3 inherits this as a detection problem it may not
   fully solve.
3. **Does the section-aware chunker get a new `CHUNK_METHODS` entry**, or is it a
   modifier on existing methods? Identity implications either way (§6.2). Still
   open.
4. **#580, sharper now that Markdown is in scope (§2).** The GoWe worker feeds
   *every* input to `PdfLoader` regardless of extension, so `.md` behaviour
   depends on the installed PyMuPDF — even though Markdown headers are
   **explicit structure**, cheaper to parse than either HTML tags or the PDF
   heuristic. Today's routing throws that away before it can be used. An
   `.html` loader hits the same seam and should be designed together with
   #580's fix, not around it — not sequenced after.
