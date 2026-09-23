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

### The source XML has the year. All of it. (measured 2026-08-29)

**This overturns the original sizing of Part B and is the most important fact on this page.**

The corpus behind `open-access` is on disk as JATS XML at `/rag/oa/corpus/clean/` —
**1,439,753 files**. A random sample of 250 of them:

| field | present |
|---|---|
| `article-meta/pub-date/year` | **250/250 — 100%**, all parseable 4-digit, range 1986–2026 |
| `journal-title`, `article-title`, `permissions/license`, `pub-history`, `article-id[doi]` | 100% |
| `contrib` (authors), `article-id[pmid]`, `volume` | 98–99% |
| `abstract` | 96% |
| `publisher-name` | 93% |
| `issue`, `funding-group` | 60% |
| `kwd` (keywords) | 53% |

So the year is **not missing from the source**. It is lost somewhere between the XML and
the store, and `ragstack/ingestion/jats.py` already extracts it
(`.//article-meta//pub-date/year`, first match wins).

Two consequences:

1. **The backfill is a local re-extraction, not a network operation.** No Crossref, no
   DataCite, no rate limits. The original plan's dominant cost and its "could take weeks"
   risk both disappear.
2. **The document count is ~1.44M, not ~16.7M.** 47.6M chunks over 1,439,753 articles is
   ~33 chunks/article, which is a sane number for 512-token chunks of full text. The ES
   cardinality aggregation was simply wrong at that scale (`precision_threshold` caps at
   40 000) — a reminder not to size work off an approximate aggregation.

**The open question is no longer "can we recover the year" but "why was it dropped".** One
signal: `jats.py` emits `year` as a **string**, while production chunks carry an **int** —
so the deployed `open-access` was probably not built by this JATS path at all. Finding
which path built it is now step 0, because whatever dropped the year is still in service
and will drop it again on the next corpus.

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

### When finer precision is wanted: `date` as `yyyymmdd`, one int — decided 2026-09-15

Recorded here so nobody implements the alternative on the strength of an earlier
recommendation. **Not work for Part A** — it changes nothing until a range operator exists,
which is what Part A is for. But it constrains what the backfill writes, so it is a decision
now rather than later.

Three options were weighed. The earlier recommendation was **split `year`/`month`/`day` ints
plus a `date_precision` field**; the owner proposed **a single `yyyymmdd` int**; a real date
type was the third.

**`yyyymmdd` wins, and the deciding argument is the filter grammar, not the storage.**

    "after 2020-03-15"

      yyyymmdd    date >= 20200315                              one comparison; needs only `gte`

      split ints  year > 2020
                  OR (year == 2020 AND (month > 3
                  OR (month == 3 AND day >= 15)))               needs OR

`filters` are **ANDed** (`query_request.json`). Split ints therefore need disjunction *as well
as* a range operator — a far larger change across the four interpreters §"Why it is not a
one-line change" says must agree. `yyyymmdd` needs only what Part A already plans.

**Unknown components are zero-padded, and that is the point:**

| source | stored |
|---|---|
| `2020-03-15` | `20200315` |
| `2020-03` | `20200300` |
| `2020` | `20200000` |

This is **self-describing about precision** — `% 100 == 0` means no day, `% 10000 == 0` means
no month — so no separate `date_precision` field is needed. It also sorts correctly:
`20200000 < 20200300 < 20200315`, so a year-only document sorts before all of March and a
month-only document before the 15th. A range query excludes what is genuinely unknown rather
than guessing.

A real date type cannot do this. It must invent `2020-03-01` for a month-only date and then
**cannot record that it invented it** — the failure this repo keeps hitting, where a wrong
value looks like a right one. Not a corner case: the ASM metadata cache built 2026-09-15 is
**76.6% year-month only, 23.2% year-month-day**. Also, Qdrant's datetime payload index wants
RFC-3339 *strings*, which would reintroduce the string temporal field #573 just removed.

*(Caveat added 2026-09-22, from the #624 review: this 76.6% / 23.2% split does not resolve to a committed artifact, and the committed `results/asm-metadata-cache/coverage-final.txt` — which measures the maximum `date-parts` length across **all** Crossref date kinds, `created`/`deposited` included — reports `{3: 263532}`, i.e. 100% three-part, and does not support it. Treat as unverified until re-measured on `issued` alone; see the note under that table.)*

**Costs nothing structurally:** it is an int, so the type rule merged in #573 covers it,
`KNOWN_INT_FIELDS` already exists, and both stores index ints natively — no ES mapping-class
problem, unlike a date type (seven of nine live indices already map `year: long`; ~136 GB
would need reindexing).

**The one hazard: `year` and `date` must not drift.** `year` already exists, is an int, and is
indexed. Where `date` is present, **`year` is derived as `date // 10000`** — never captured
independently. Two independently-written representations of the same fact is precisely the
bug class of #573 and the `asm-semantic` divergence. `year` continues to stand alone for
documents that have nothing finer, which is most of ASM.

Honest downsides: unreadable at a glance, and arithmetic on it is meaningless
(`20200315 + 1` is not the next day). Neither matters for a field that is only ever compared.

---

## Part B — backfill `year` on `open-access`

### The shape of it

85% of chunks have no year. The recovery path is **not** uniform:

- **The year is recoverable locally for the whole corpus** — 100% of sampled source files
  carry a parseable `pub-date/year`, and the files are on disk. Re-extracting 1.44M small
  XML files is an afternoon of CPU, parallelisable, with no external dependency.
- Crossref/DataCite resolution — the original plan's dominant cost — **is not needed**.

Both stores must be updated **together**. Updating one and not the other reproduces #471's
divergence at data level, permanently and silently.

### Work

0. **Find the ingest path that built the deployed `open-access`, and why it dropped the
   year.** Superseded question: the count is ~1.44M and the source coverage is 100%, so
   recoverability is settled. What is not settled is the defect — production stores `year`
   as an `int` while `jats.py` produces a `str`, which suggests a different path built this
   collection. Whatever dropped the year is still in service; backfilling without fixing it
   means doing this again after the next corpus.
1. **Correct the future-dated chunks.** Small, bounded, and independently useful —
   these actively corrupt any "recent" query.

   > **Corrected 2026-09-15.** Two things on this line were wrong. The count is not 8,408:
   > that is `open-access` alone, and ASM's three **production** indices carry **16,176**
   > more (`ragstack_sfr_tok256` 9,916 · `tok512` 4,980 · `semantic` 1,280, measured
   > read-only). And the producer is not `doi_metadata.py` — it is
   > **`enrich.derive_year`**, whose `_YEAR` regex admits `20[0-4]\d` and returned the
   > first match in the path or DOI unbounded. Three confirmed mechanisms: a scratch
   > **UUID** in `source_path` (`/local/scratch/03220d5a-2049-4ab5-…/` → 2049, for an
   > article whose real year `mra.2021.10.issue-33` is further along the same path); an
   > **ISSN inside a DOI** (`10.1186/2049-2618-*` is *Microbiome*, ISSN 2049-2618); and an
   > **accession in a caption** (`BGS.GSE2028/9680` → 2028). The producer is fixed on
   > `fix/metadata-type-consistency`: every arm is bounded, and the first *plausible*
   > candidate wins rather than the first candidate, which recovers the true year instead
   > of merely dropping the false one. The stored values are untouched — nothing was
   > backfilled.
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
- ~~Crossref rate limits~~ — **retired.** The source XML has the year; no network
  resolution is involved.
- **A partial backfill is worse than none for user trust** if it lands unevenly: a year
  filter that returns 30% of the true matches looks like an empty corpus, not a broken
  filter.

---

## The packed form (decided 2026-09-17)

Part B above backfills `year`. That leaves the question this plan did not answer: what
happens when a source carries a **full** publication date, and what a caller filters on
when precision varies across a corpus. The answer, now declared in
`contracts/schemas/chunk_metadata.json` and produced by `ingestion.enrich.packed_date()`:

**`date` is a packed `yyyymmdd` integer, with unknown components 0.** `19860000` is "1986,
month and day unknown"; `19820300` is "March 1982"; `20200315` is a full date.

Three properties made this the choice over the alternatives:

- **One field, one range.** The filter grammar ANDs and has no OR (Part A § *Why it is not
  a one-line change*). A split `year`/`month`/`day` would need a disjunction that does not
  exist — "after 2020-03-15" becomes *(year > 2020) OR (year = 2020 AND month > 3) OR …*.
  Packed, it is `date >= 20200315`, which needs only the range operator Part A adds.
- **Precision is self-describing.** `% 10000 == 0` is year-only, `% 100 == 0` has no day.
  No companion precision field to keep in sync, and nothing to get wrong on a partial write.
- **Zeros sort where the ambiguity belongs.** `19860000 < 19860101`, so a year-only record
  sorts at the head of its year and is included by `>= 1986` and by `< 1987` — the two
  bounds a caller actually writes.

**INVARIANT: where both are present, `year == date // 10000`.** `year` is DERIVED. Nothing
should capture the two independently, and a producer that writes one without the other is
writing a record whose two temporal fields can drift apart. This holds across all 61M
chunks measured at the time of writing.

**What does not follow from this.** Declaring the field is not the same as populating it:
**no ingest path writes `date` today**, so its coverage is strictly narrower than `year`'s
already-thin 14.8% and is limited to whatever a backfill put there. Part B remains the work
that makes either field useful, and it should write `date` alongside `year` rather than
leaving a second field to backfill later.

---

## Sequencing

**A1 (the #471 500→400 fix) is independently shippable and should go first** — it is small,
it removes a live 500, and it establishes the validation seam the rest of Part A needs.

Then **B0**, because it is cheap and it sizes everything else. Only then decide whether the
rest of Part A is worth doing before or after the backfill: a range operator over a 14.8%-
populated field is a feature that mostly returns nothing.
