# Metadata schema, where it lives, and what goes in the knowledge graph

**Status:** `PROPOSED`. Three decisions, taken together because they constrain each other.
The KG is **not implemented** (`GRAPH_BACKEND=disabled` on `lucid`/`dev`/`demo`, unset on
`asm`), which is the best possible time to decide the third.

---

## 1. Should the PMC/JATS schema become RAGStack's general schema?

**No — but the instinct behind it is right, and the thing it is reaching for is missing.**

Our XML *is* PMC's: the corpus was fetched from `pmc-oa-opendata.s3.amazonaws.com`, so the
`<front>` tree is byte-identical to what `efetch db=pmc` returns. That makes PMC an
excellent **source mapping**. It does not make it a general schema, for two reasons:

- **JATS describes a biomedical journal article.** RAGStack ingests PDFs, plain text and
  Markdown as first-class inputs. A technical report has no `journal-meta`, no
  `article-id[@pub-id-type="doi"]`, no `permissions/license`. Adopting JATS as the general
  schema makes every non-article document a pile of absent fields.
- **A normalized vocabulary already exists — it is just undocumented and unenforced.**
  Chunks already carry `title, authors, journal, year, doi, pmid, pmcid, publisher, licence,
  keywords, source_url, sha256, doc_type, content_type, tenant_id, chunk_index,
  prev_chunk_id, next_chunk_id, start_char, end_char`. That *is* the schema. Nothing
  declares it, so it drifts.

**Evidence that it drifts, all observed:**

| | |
|---|---|
| `jats.py` emits `year` as a **`str`**; production chunks carry an **`int`** | the deployed corpus was built by a different path |
| `asm-tok256` carries no `journal`, `publisher`, `licence`, `pmcid`, `source_url` | field presence varies by collection, silently |
| `{"year": ["2025"]}` returns 10 on `bm25`, **0** on `vector` (#471) | nothing pins the type, so the stores disagree |

### Proposal

Declare a **core metadata schema** in `contracts/` — the flat vocabulary above, each field
with a type and a nullability rule — and treat every ingest path as a **mapping into it**:

```
JATS/PMC  ─┐
PDF        ├─→  core metadata  ─→  chunk payload / document record
Markdown  ─┘        (typed)
```

Three rules make it worth the effort:

1. **Types are pinned and enforced at ingest**, not discovered at query time. `year` is an
   `int` in one place, and every writer coerces to it.
2. **A source-specific `extras` namespace** carries what does not generalize (`pmcid`,
   `pmid`, MeSH terms, `funding-group`) without inventing core fields that are null for 90%
   of a mixed corpus.
3. **PMC is the reference mapping** because it is the richest source we have — it fills
   almost every core field — so it is the mapping to write first and test against.

**What PMC adds beyond JATS, if we ever want it** (measured on 40 of our own PMIDs: 30
MEDLINE-indexed, 10 not; **77% carry MeSH**): `MeshHeadingList`, `ChemicalList`,
`PublicationTypeList`, normalized `GrantList`, `NlmUniqueID`/`ISSNLinking`,
`CommentsCorrectionsList` (retractions), and references with resolved ids. Note our local
copy has **`ref-list` stripped in 0% → 100% of files** — the "clean" tree dropped them.

---

## 2. Metadata on the chunk, or in a document record?

**Split it. The line is: does anything filter or rank on this field?**

### The numbers that decide it

47,625,155 chunks over ~1,439,753 documents — a **~33× duplication factor**. Every
descriptive field is stored 33 times on average, in **two** stores (Qdrant payload and the
ES document).

That duplication is not theoretical overhead; it is the cost we just scoped:

- **Correcting one document's year means rewriting ~33 chunk payloads in two stores.** The
  `year` backfill is a 47.6M-point operation *because* the metadata lives on the chunk.
- **Two copies can disagree, and one already does** (#471).
- The 8,408 future-dated chunks are perhaps ~250 wrong documents, amplified 33×.

### The split

**Stays on the chunk** — anything a store must filter or rank by, because a filter that
needs a join is a filter that cannot run inside the store:

`tenant_id`, `doc_id`, `collection`, `chunk_index`, `prev_chunk_id`, `next_chunk_id`,
`start_char`, `end_char`, `is_boilerplate`, and the **small filterable facet set** —
`year`, `doc_type`, `content_type`, and later `mesh_id[]` if topical filtering lands.

**Moves to a document record**, keyed by `doc_id` — everything descriptive, needed only for
the handful of chunks a response actually returns:

`title`, `authors`, `journal`, `publisher`, `licence`, `doi`, `pmid`, `pmcid`, `source_url`,
`sha256`, `keywords`, `abstract`, funding, and the `extras` namespace.

### Why this is cheap where it matters

A response returns `top_k` chunks — 5 by default, ≤100 by bound. Joining ≤100 document rows
at render time is one indexed lookup. Filtering, by contrast, runs against 47.6M points, and
**`doc_id` is already a Qdrant payload index** — so a two-phase filter (select documents →
constrain chunks by `doc_id`) is available for facets too selective or too volatile to
duplicate.

A document table also fits somewhere that already exists: the tenant's SQLite/Postgres store
already holds the collection registry, ACLs, users and jobs.

### Cost, stated honestly

- **A third store on the read path.** Today a chunk is self-describing; after this, rendering
  a source needs a join. If the document store is unavailable, the system must still answer
  with chunk-level fields rather than failing — that is a real design constraint, not a
  footnote.
- **A migration over 47.6M points**, which is the same shape as the `year` backfill.
- **Sequencing:** do the `year` backfill on-chunk first — it is needed either way and it is
  the cheaper rehearsal — then externalize. Do not couple them.

---

### 2b. Chunk text: one copy, addressed by offset

**The chunk text is stored twice and is already fully derivable from three fields we also
store.** Verified on a real document in the production index — the top-level payload of
every chunk is:

```
chunk_id, doc_id, start_char, end_char, content
```

and the offsets tile the document with the configured overlap:

| chunk | start | end | length | delta from previous end |
|---|---|---|---|---|
| 0 | 0 | 2044 | 2044 | |
| 1 | 1797 | 3779 | 1982 | −247 |
| 2 | 3547 | 5354 | 1807 | −232 |
| 3 | 5108 | 6920 | 1812 | −246 |

−247 characters is the `fixed_token 512/64` overlap (64 tokens ≈ 250 chars). So
`start_char`/`end_char` are **a consistent coordinate system over one contiguous document
text** — not per-chunk bookkeeping. `content` is a cached substring of a document we could
address directly.

That makes the offset model available without inventing anything:

```
document text  ──uploaded once──►  object store node
chunk          ──────────────────►  (node_id, start_char, end_char)
```

**Shock supports exactly this shape.** It is a RESTful object store with node indices
(`PUT /node/{nid}/index/{type}` — `size` is *virtual*, needing no precalculation; plus
`line`, `column`, `record`, `chunkrecord`, and `subset`, which is built on an existing
index), so parts of a parent file are individually addressable, and it supports bulk
retrieval. Correcting an earlier misreading in this plan: Shock's role in the BV-BRC
Workspace — opaque bytes behind a node URL — is *how we currently use it*, not the limit of
what it does.

#### What this buys, and what it costs

Storage, measured: **vectors 727 GB, Qdrant payload+index ~113 GB, Elasticsearch 82 GB.**
Text is ~23% of the Qdrant+ES footprint. Removing both copies is real but is not the
headline — the vectors dominate and cannot be deduplicated.

The stronger arguments are structural:

- **One canonical copy.** Two copies that can disagree is the shape of #471. A chunk becomes
  a *pointer*, and pointers cannot drift.
- **Re-chunking stops being a re-ingest.** Today changing the chunk size means re-extracting
  every document. With the text addressable, a new chunking is a new set of offsets over
  bytes that are already there — the expensive part (embedding) is unavoidable either way,
  but the extraction is not.
- **It composes with §2.** The document record gets a `text_node_id`; the chunk keeps the
  facets and the span.

The costs are real and belong in the decision:

- **Elasticsearch must still hold the text to invert it** — BM25 is an inverted index over
  the terms. What can go is ES's *retained* copy (`_source`), not the index. That is a
  smaller saving than it first appears, and disabling `_source` also disables reindex-in-place
  and highlighting.
- **A read-path dependency.** Rendering a response would need a bulk span fetch per query.
  If the object store is slow or down, the system can rank but not answer. That is a new
  availability coupling and needs an explicit answer — a cache, or keeping content in one
  index as the fallback.
- **Granularity is an open question.** One node per document (~1.44M nodes) with an index
  for span addressing, versus one node per chunk (47.6M nodes). The first is what the index
  mechanism appears designed for.

**Recommendation:** treat this as the target shape and prove it on one collection —
`oa-dev` (~24k chunks) — measuring bulk span-fetch latency at realistic `top_k` before
committing. The offsets already exist, so the migration is additive: write `text_node_id`,
verify spans reproduce `content` byte-for-byte, and only then stop writing `content`.

## 3. What goes in the knowledge graph

**Triples with provenance. Not chunks.** This is already decided in `models.py::Triple` and
the decision is right; what follows is why, and what is still open.

> Some designs put the chunks themselves in the graph. That makes the graph a **third full
> copy of the corpus** — a third thing to keep in sync, in a system that already has a
> live two-store divergence bug (#471) and is proposing a document store above. The graph
> would then be simultaneously the worst vector store, the worst text index, and a graph.

### What a triple already carries

| axis | fields | rule |
|---|---|---|
| scope | `tenant_id`, `collection` | **fail closed** — unstamped is invisible |
| epistemic provenance | `evidence` (verbatim span), `chunk_id`, `derived_by`, `confidence` 0–3 | fail **open** — unstamped is unfiltered |
| identity | `subject_id`, `object_id` (e.g. `bvbrc:genome:<id>`), with free-text `subject`/`object` as display | optional |

The `evidence` span is the important compromise: a triple is **explainable without a join**
— you can show why it is believed — while `chunk_id` gives the way back to full context.
That is the middle position between "graph holds pointers only" (unexplainable alone) and
"graph holds chunks" (a second corpus).

The `confidence` ladder is what makes the graph safe to fuse: 0 proposed, 1 LLM-plausible,
2 tool-corroborated, 3 verified against a structured source. A retrieval leg can demand a
floor.

### What is still open — the real design work

1. **Entity resolution.** `subject_id`/`object_id` are optional today. Without resolution,
   "E. coli", "Escherichia coli" and "*E coli*" are three nodes and the graph's whole value —
   connecting things — does not materialize. **This is the decision that determines whether
   the KG is useful at all**, and it is not a storage question.
2. **Predicate vocabulary.** Free-text predicates from an LLM give a graph that cannot be
   queried, because no one knows the edge names. A closed or semi-closed set is a schema
   decision to take before extraction runs, not after.
3. **What is worth extracting.** For this corpus the candidates are organism / gene /
   protein / disease / drug / method, and relations between them. Extracting everything an
   LLM will name produces volume, not signal.
4. **Scope asymmetry, already in the code.** Vector and text stores are **per collection**;
   there is **one graph store for all collections**, so the graph leg must push `collection`
   into the query *and* re-check on return. Any KG design has to keep that, or a triple
   crosses a corpus boundary the other two legs cannot.
5. **Whether MeSH is the entity backbone.** 77% of our PMIDs carry MeSH — a curated,
   already-resolved controlled vocabulary. Using MeSH terms as nodes sidesteps most of
   problem 1 for the biomedical corpus, at the price of only covering what MeSH covers.

---

## 4. Which leg answers which question

Verified against `retrieval/retriever.py`. `retrieval_mode` selects **vector**, **bm25** or
**hybrid** (both, RRF-fused); the graph leg is **orthogonal** — `use_graph` adds it under
any mode, as a **third ranked list** fused the same way.

| leg | what it is good at | what it misses |
|---|---|---|
| **vector** | paraphrase, concepts, a question worded unlike the corpus | exact strings; any token the embedder never saw |
| **bm25** | identifiers, accessions, gene/protein names, rare tokens, quoted phrases | synonymy and paraphrase |
| **graph** | relational questions — what connects X and Y, multi-hop neighbourhoods | anything not extracted as a triple |

### When to narrow

- **bm25 alone** when the query *is* a string you know: a DOI, a PMCID, an accession. The
  dense leg contributes noise around an exact match.
- **vector alone** when the query is conceptual and the corpus vocabulary differs from the
  user's — and when a BM25 leg would anchor on a common word.
- **graph off** when the corpus has no triples (all four tenants today) or the question is
  not relational. The leg costs a query-side entity-extraction pass plus one neighbourhood
  query per matched entity.
- **all three** is the default and the right one for an open question over a corpus that has
  a graph: each leg fails differently, and RRF only needs a document to rank well in *one*
  list to surface it.

### Two properties worth knowing before tuning

- **The graph leg contributes at a fixed score** (`graph_context_score`, default `0.5`), so
  its influence is positional through RRF, not similarity-weighted. It changes rank, not
  confidence.
- **A filtered query behaves differently per mode** until #471 is fixed. That interacts
  badly with "use all three": hybrid can look correct while the vector leg silently
  contributes nothing.
