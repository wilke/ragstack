# Date filtering: a range operator, and a `year` backfill

**Status:** `SCOPED`, not started. Two independent pieces — A is a code change, B is a
data operation on production stores. A is worth doing regardless; B is what makes A useful
on `open-access`.

Prompted by "can we search everything after 2025?" The answer today is *partly*, by
enumerating years, and on `open-access` it silently misses most of the corpus.

---

## What is true today (measured, 2026-08-28)

`year` is the only temporal field. There is no `date`, `published` or `issued`. It is an
**`int`**, produced by DOI metadata resolution (`ingestion/doi_metadata.py` — Crossref
`issued`/`published`, DataCite `publicationYear`), so it exists only where a DOI resolved.

Measured on the production `open-access` index by aggregation:

| | |
|---|---|
| chunks | **47,625,155** |
| chunks with a `year` | **7,042,037 — 14.8%** |
| of those, plausible (1800–2026) | 7,033,629 (99.88%) |
| **future-dated (> 2026)** | **8,408** — parse errors, e.g. 2049, 2048, 2047 |
| chunks currently matching "2025 or later" | 1,126,205 |

Qdrant payload indexes on that collection: **`doc_id` and `tenant_id` only.** `year` is
**not** indexed across 47.6M points.

> ⚠️ **The distinct-document count is not established.** The ES cardinality aggregation
> estimates ~16.7M documents, which at 47.6M chunks implies ~2.9 chunks/document — hard to
> reconcile with the 118.3 chunks/document measured on full-text PDFs. Either a large
> fraction of the corpus is abstract-only, or the estimate is bad at this scale
> (`precision_threshold` caps at 40 000). **This is step 0 of Part B**, because it decides
> whether the backfill addresses ~10^5 or ~10^7 documents — a difference between a day and
> a quarter.

---

## Part A — a range operator in the filter grammar

### Why it is not a one-line change

Four independent interpreters must agree on the grammar, or a filter means different things
on different retrieval paths:

| Interpreter | File |
|---|---|
| Qdrant search | `stores/qdrant.py::_build_filter` |
| Elasticsearch search | `stores/elasticsearch.py::_build_query` |
| In-memory search | `stores/memory.py::_matches` |
| `get_chunks` re-application | `stores/filters.py` |

`filters.py`'s docstring already states the rule that makes this non-negotiable: the
predicate *"MUST accept the same grammar or every valid user filter 400s"*, and silently
dropping a constraint is *"worse than dropping one in `search()`: the caller believes a
scope constraint took effect when it never touched the result."*

**We have already shipped a live instance of exactly that.** #471: `{"year": ["2025"]}`
(string against an int payload) returns 10 results on `bm25`, **0 on `vector`**, and 10 on
`hybrid` — because Elasticsearch coerces and Qdrant compares payload types exactly, and
hybrid hides the disagreement behind the BM25 leg. A range operator adds a second axis for
the same class of divergence.

### Work

1. **Fix #471 first — it is the same seam.** A `dict` filter value is currently a **500**
   (`unhandled ValidationError`), not a 400. Validation has to land at the router before
   any store client sees the value, and that validator is where range syntax gets parsed.
2. **Choose the operator vocabulary** — a decision, not an implementation detail. Options:
   Mongo-ish (`{"year": {"gte": 2025}}`), a two-key form (`{"year_min": …}`), or a typed
   `RangeFilter` model. Recommend the first: it is what a caller reaching for a range tries
   first, which is precisely how #471 was found.
3. **Contract first.** `contracts/schemas/{query,retrieve}_request.json` currently type
   `filters` as a free object; the operator form has to be expressible without loosening
   `additionalProperties: false` elsewhere.
4. **Implement in all four interpreters, with parity tests** — the same filter over the
   same fixture must return the same set on qdrant / elasticsearch / memory, and
   `filters.py`'s predicate must agree. **The parity test is the deliverable**, not the four
   implementations; without it this reintroduces #471 one release later.
5. **Resolve the type-coercion question** (#471 part 2) in the same change: coerce at the
   boundary against a known field type, or refuse the mismatch with a 400. Doing ranges
   without this leaves `{"year": {"gte": "2025"}}` as a fresh silent divergence.
6. **Add the Qdrant payload index on `year`** — otherwise every range query on
   `open-access` filters 47.6M unindexed points. Creating a payload index on a live 47.6M
   collection is itself an operation with a cost; it belongs in this plan, not as an
   afterthought.

### Not in scope

A real `date` field. `year` is what the data has; adding month/day precision is a schema
and re-ingest question, not a filter question.

---

## Part B — backfill `year` on `open-access`

### The shape of it

85% of chunks have no year. The recovery path is **not** uniform:

- Documents with a `pmcid` (the sampled ones all had) may carry a year in the PMC metadata
  already held at ingest, in which case this is a **local** re-derivation, not a network
  operation.
- Documents needing Crossref/DataCite resolution are rate-limited network work, and at
  10^6–10^7 documents that is the dominant cost and the reason step 0 matters.

Both stores must be updated **together**. Updating one and not the other reproduces #471's
divergence at data level, permanently and silently.

### Work

0. **Establish the true document count and the recoverable fraction.** Sample N documents
   lacking `year`, and measure: how many have a `pmcid`, how many have a resolvable DOI,
   how many have neither. Everything below is sized off this number and it is currently
   unknown to an order of magnitude.
1. **Correct the 8,408 future-dated chunks.** Small, bounded, and independently useful —
   these actively corrupt any "recent" query. Find the parse path in `doi_metadata.py` that
   produced 2049 before fixing the values, or the next ingest reintroduces them.
2. **Write the backfill as a resumable, idempotent job** — `set_payload` by `doc_id` in
   Qdrant (indexed, so selection is cheap) plus `update_by_query` in Elasticsearch, batched,
   with a checkpoint. It must be safe to stop with SIGINT and restart, per the bulk-load
   runbook.
3. **Rehearse on `oa-dev`** (dev tenant, ~24k chunks) before production. Not `demo` or
   `asm` — both write to the production stores.
4. **Store URLs are required inputs** (#454). This job is a write path; it takes explicit
   `--qdrant-url`/`--es-url` and has no defaults.
5. **Verify by re-running the aggregation above**, not by spot-checking: coverage before and
   after, and zero implausible years.

### Risks

- **It writes to production stores** serving four tenants. Needs the Fable plan CLAUDE.md
  mandates for live-infrastructure operations, off-peak scheduling, and a rollback position
  (a payload write is not trivially reversible — capture the prior values).
- **Crossref rate limits** could make the network path take weeks. Step 0 decides whether
  that path is needed at all.
- **A partial backfill is worse than none for user trust** if it lands unevenly: a year
  filter that returns 30% of the true matches looks like an empty corpus, not a broken
  filter.

---

## Sequencing

**A1 (the #471 500→400 fix) is independently shippable and should go first** — it is small,
it removes a live 500, and it establishes the validation seam the rest of Part A needs.

Then **B0**, because it is cheap and it sizes everything else. Only then decide whether the
rest of Part A is worth doing before or after the backfill: a range operator over a 14.8%-
populated field is a feature that mostly returns nothing.
