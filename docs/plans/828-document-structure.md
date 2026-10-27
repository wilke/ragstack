# Document structure: one model, per-format adapters, section-aware chunking and chunk metadata (#828)

**Status:** `PROPOSED`, design note, 2026-10-27, revised the same day after an independent
review (its findings are folded in where they bit: §2 adapter placement and invariants 3 and 5,
§4 supersession and release order, §6 the `section_title` change on unit records and the
list-filter caveat, §7 the `embed_heading_path` forcing rule, §8 the shared helper, §9.1 the BOM
and parser details, §10 the statistics, §12 the #831 dependency and the contract change in
PR 4a). No code. Written for the owner's review before the first PR of the series in §12. It folds in the owner's direction from the #828
thread (document level, not format level; `Document.structure`; adapters; stamping by offset
overlap; inferred structure for PDF and extracted text; captions; placement as an evaluation
arm; an accuracy check before anything relies on the inference), the merge rule of #831, the
path-parity findings of #833, and the mechanisms #829 and #830 just landed.

**Relation to earlier plans.** [`structured-ingest.md`](structured-ingest.md) (2026-09-22) argued
for one normalized record with four producers and settled references/supplemental (its §5, §8
decisions stand). [`section-taxonomy.md`](section-taxonomy.md) supplied the evidence that untitled
sections are first-class and that overlap buys nothing. This note replaces the record shape of
`structured-ingest.md` §3 with a typed model on `Document`, and is the design the owner asked for
before any §2–§4 code in #828.

---

## 1. Goals and non-goals

**Goals**

1. A document carries its own structure as data: sections (a tree), blocks (a sequence), with
   character offsets into the text the chunker sees, and a statement of where the structure came
   from and how much to trust it.
2. Everything downstream is format-agnostic. The chunkers, the role classifier, the metadata
   stamp and the evaluation harness read `Document.structure` and nothing else about the format.
3. Every chunk can say which section it is in (title, path, level, role), which kinds of blocks
   it holds, which figure and table captions it contains and which it cites. The owner's minimum
   is section type as metadata; this note delivers that first and section-aware cutting second.
4. The same file gives the same `content` and the same `structure` on every ingest path. This
   is the invariant #830 restored for `content`; structure must not re-break it.
5. Nothing changes for an existing collection unless someone asks for it, and asking for it is
   recorded where #829 records the semantic cap: in `chunk_params`, with the same marker
   discipline and one reader.
6. Before any role or inferred boundary is used for filtering or cutting, its accuracy is
   measured on a hand-labelled sample, split by source.

**Non-goals**

- No new chunk method. Section-aware cutting is a modifier on the existing methods, selected by
  a `chunk_params` marker (resolves `structured-ingest.md` §8 Q3; see §7).
- No one-record-per-section. `structured-ingest.md` §1 records why that destroys document
  identity and neighbour links; this note keeps one record per document.
- No change to the existing `section` chunk field. It is the boilerplate verdict
  (`references` / `license` / `acknowledgements`) and keeps its meaning; the new field is
  `section_role`.
- No routing of references to Postgres or the graph. That is `structured-ingest.md` §5, decided,
  unbuilt, and independent.
- No LLM in the ingest path. An LLM role classifier, if ever, is an offline cached step with an
  `llm/N:…` version, like the symbol glossary.
- No decision on caption placement. Placement is an evaluation arm (§11), not a design choice.

---

## 2. Where the model sits, and the one rule about offsets

```
file ──► loader/adapter ──► Document(content, structure) ──► chunker ──► chunks(start_char, end_char)
                                                   │                          │
                                                   └──── stamp(structure, offsets) ──► chunk.metadata
```

**Offsets index `Document.content`, the exact string the chunker slices.** `_make_chunk` already
guarantees `content[start_char:end_char]` is the chunk's text and that the chunk id is
`uuid5(doc_id:start:end)`; the structure uses the same coordinate system, so stamping is a pure
offset-overlap computation and never a string search.

**The rule that makes this safe: offsets are accumulated while the text is built, never
recovered afterwards.** `jats.py` already does this (`_section_walk`: "child offsets are
accumulated from the same `len`s the `_SEP` join produces, so they are exact by construction").
Every adapter in this note follows it. In particular:

- A Markdown adapter that *strips* syntax does not parse the raw file, strip it, and then try to
  map heading positions into the stripped text. It renders the plain text through an emitter that
  appends strings and returns the offset each one landed at; a section opens at the emitter
  position when its heading is emitted and closes at the emitter position when the next heading
  of equal or higher rank arrives. The headings are read from the parse, before anything is
  stripped (#828 §2: "Order matters"), and the offsets are born in the stripped text.
- An adapter that leaves the text *as the loader produced it* (raw Markdown, PDF text, JATS record
  text) walks a structure whose coordinates are already in that text (line table, `Span`,
  PyMuPDF block geometry) and emits offsets into it directly.
- No step ever transforms `content` after structure exists. Normalisation that the loader does
  today (universal newlines in `TextFileLoader`, `.strip()` of PDF text) happens *before* the
  adapter runs, on both paths. `TextFileLoader` reads `utf-8`, not `utf-8-sig`, so a BOM is
  `content[0]` and stays there; the Markdown adapter parses `content` with the BOM removed and
  adds its length back to every offset (§9.1), which is a parse-input choice, not a transform of
  `content`.

**Where each adapter runs** (this decides what the JSONL must carry and which image must be
new; the review found the first draft's answer could not work on GoWe):

| adapter | what the structure is read from | where it runs | why |
|---|---|---|---|
| Markdown (§9.1) | the raw text itself | **at ingest**, on every path: a pipeline step `attach_structure(documents, options)` dispatching on the suffix of `Document.source`, after load and DOI enrichment, before chunking | raw Markdown travels intact through the GoWe extract step since #830, so nothing is lost by deferring; and the ingest step is the only one that knows the collection spec (`ingest_shard._build_pipeline` resolves the target; the extract step's CWL inputs are `pdfs` and `batch_id`, nothing else), so `text_render` can only be applied there. `pdf_extract.py` never writes structure for `.md`/`.txt`. |
| JATS (§9.2) | the XML | **at extract** (`jats_extract.py`, and the in-process `JatsLoader` of PR 4a) | the XML is not available after the record is written; the structure rides the JSONL (§4) |
| PDF (§9.4) | layout from the PDF bytes | **at extract** (`pdf_extract.py` → `PdfLoader`) | same: the layout is gone once the text is written; the structure rides the JSONL |
| extracted text (§9.5) | the text itself | **at ingest**, like Markdown, when the collection opts in | there is nothing else to read it from |

So the GoWe extract step gains no workflow input and no spec lookup (consistent with #829's
rule that `chunk_params` are read from the registry entry, never from a CWL input), the
`text_render` marker is honoured where the entry is known, and the JSONL carries structure only
for formats whose source is unavailable downstream.

**Invariants, tested in PR-1 and asserted on every load (§8):**

1. `0 <= start < end <= len(content)` for every section and block.
2. Sections nest: for any two sections, either one contains the other or they are disjoint; a
   child's `level` is its parent's `level + 1`.
3. The level-1 sections partition `[0, len(content))` in order, with no gaps. The children of
   any section are disjoint, ordered and lie within the parent — they do **not** partition it,
   because a parent normally has text of its own before its first child (its heading and lead
   paragraphs, in Markdown and in a JATS `<sec>` with `<p>` before a nested `<sec>`); that text
   belongs to the parent alone, and the deepest-overlapping-section stamp (§6) needs nothing
   stronger. **Separators belong to the preceding section:** a section's `end` is the next
   sibling's `start`, or its parent's `end`, or `len(content)` at level 1. This matters because the sources do not hand over gap-free spans:
   `jats.py` joins sibling spans with `"\n\n"` that belongs to no `Span` (and the abstract and the
   body are joined the same way), and a Markdown blank line between a paragraph and the next
   heading is in no token's `map`. The adapters therefore *extend* each section's `end` to the
   next start before emitting, and the validator checks the result, not the source spans. Text
   before the first heading is a level-1 section with `title=None` (untitled sections are
   first-class, `section-taxonomy.md` rule 7). `structure` absent means *unknown*; it never
   means one big section.
4. Blocks do not overlap each other and are sorted by `start`. They need not cover the text.
5. A titled section has exactly one `heading` block, it is the section's first block, and it
   lies within the section. A `heading` block covers **the title text only**, never the heading
   markup: in raw Markdown an ATX heading's `map` covers `## Title\n`, so the block starts after
   the `#` run and its following spaces and ends before the optional closing `#` sequence and
   the newline; a setext heading's block is its first line's text; JATS' `title` span covers
   the rendered `## Title` line, so the block starts `min(depth+2, 6) + 1` characters in. The
   stronger property — the heading block's text equals `title` — holds exactly in stripped
   Markdown and JATS, and after inline-markup removal in raw Markdown (`*tool*` → `tool`); it is
   asserted by each adapter's own tests, not by the load-time validator, which would otherwise
   need a renderer and would have dropped every raw-Markdown and JATS structure of the first
   draft.
6. `stamp(structure, start, end)` is a pure function; the same inputs give the same metadata
   on every path, which is what the parity tests compare.

---

## 3. The data model (`python/ragstack/models.py`)

Exact names and types. Pydantic models beside `Document`; JSON shape identical to
`model_dump()`.

```python
class Section(BaseModel):
    start: int                    # half-open [start, end) into Document.content
    end: int
    level: int                    # nesting depth in the tree, 1 = top level
    title: str | None             # the document's own heading text, verbatim, no markup; None = untitled
    path: list[str]               # titles of the titled ancestors and self, outermost first
    role: str | None              # one value of roles/1 (§5), or None = not classified
    role_source: str | None       # "declared/1" | "rules/1" | "llm/1:<model>:<prompt-sha>"; None iff role is None
    locator: str | None = None    # adapter-native address, for the labeller and for debugging:
                                  # JATS "body/sec[2]/sec[1]", Markdown "L42", PDF "p3.b7"

class Block(BaseModel):
    start: int
    end: int
    kind: str                     # one of BLOCK_KINDS below
    label: str | None = None      # captions only: the normalised label, e.g. "Figure 3", "Table S1"

BLOCK_KINDS = ("prose", "heading", "code", "list", "quote", "table", "figure",
               "figure_caption", "table_caption", "math", "front_matter")

class DocumentStructure(BaseModel):
    sections: list[Section]       # document pre-order: a container before its first child
    blocks: list[Block]
    structure_source: str         # "parsed" | "inferred"
    adapter: str                  # "markdown/1" | "jats/1" | "html/1" | "pdf-layout/1" | "text-heuristic/1"

class Document(BaseModel):
    id: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    source: str = ""
    structure: DocumentStructure | None = None   # omitted from model_dump() when None
```

Notes on the choices:

- **`level` is tree depth, not the raw heading rank.** Markdown skips levels (`#` then `###`),
  JATS counts enclosing `<sec>` from 0, PDFs have no rank at all. Tree depth is the only thing
  every adapter can state honestly. The raw rank is recoverable from the source and is not
  stored (open question Q11).
- **`path` contains titles only.** An untitled ancestor contributes nothing to `path`, so
  `path` is the heading path a reader would write (`Install › Linux › From source`) and
  `section_path` on the chunk is the same list. Its position in the tree is still exact through
  `start`/`end`/`level`.
- **The enclosing section of a block is derived, not stored.** Storing it would be a second copy
  of the containment relation and a second thing to keep consistent (`_make_chunk`'s "never
  re-find" rule applies to relations too). Containment by offsets is the one source.
- **`structure_source` is coarse on purpose** (`parsed` | `inferred`), because the filter grammar
  is equality-only: a caller must be able to say `{"structure_source": "parsed"}`. The fine
  identity is `adapter`.
- **`role_source` is versioned even when declared.** The owner's comment spells it `declared`;
  this note proposes `declared/1`, because the map from JATS `sec-type` values to the role
  vocabulary is itself a rule table that can change (open question Q5).
- **`heading` and `front_matter` are block kinds** beyond the owner's list. A chunk that is only a
  heading line is exactly the small piece #831 wants to merge, and it must be identifiable;
  YAML front matter is neither prose nor code and must not reach the boilerplate classifier as
  body text.
- **Serialisation.** `structure: None` is omitted from `model_dump()`, under the precedent of
  `Chunk.symbol_glossary` and `ScoredChunk.collection`, so every record written without
  structure is byte-identical to one written before the field existed.

---

## 4. The JSONL carrier: a fourth top-level key

The GoWe path and the operator CWL paths hop through JSONL (`{"text", "path", "metadata"}`,
`JsonlLoader`). Structure rides as a **fourth top-level key**, `"structure"`, holding
`DocumentStructure.model_dump()`:

```json
{"text": "...", "path": "...", "metadata": {...}, "structure": {"sections": [...], "blocks": [...], "structure_source": "parsed", "adapter": "markdown/1"}}
```

Why not inside `metadata`: `metadata` is what the passthrough allow-list, `index_metadata` and
the ES keyword mapping consume. A top-level key is outside all three, so structure is immune to
the metadata drift #833 item 4 describes and never lands in a store as a payload.

**This supersedes a rule in the code.** `jats.article_records` (the `with_spans` docstring) and
`scripts/jats_extract.py` (`--spans-out`) both say spans "must never reach `metadata` or the
JSONL shard". Half of that stands — never `metadata` — and half is reversed here: the shard
record *is* the carrier, as a top-level key. The reason the rule existed (spans are large and
not filterable) is met by keeping them out of `metadata`; the reason to reverse it is that the
JATS and PDF structures have no other way to reach the ingest step (§2). PR 1 rewrites the
`jats.py` docstring; PR 4b rewrites `jats_extract.py`'s and makes `--spans-out` a debugging
side file that duplicates the record key.

`pdf_extract.py` and `jats_extract.py` write the key when the document has structure;
`JsonlLoader._document` reads it, **validates the invariants of §2 against `text` before
accepting it**, and on any violation drops the structure (not the document) and counts it in a
new `DropReport.structure_invalid` counter the shard prints, so a corrupt or stale structure is
loud and the document still ingests as unstructured.

**An old image drops the key silently.** `JsonlLoader._document` reads `text`, `path` and
`metadata` and ignores every other key, so an ingest image without this series ingests a
structured shard as unstructured with no error; and an extract image without it writes no key,
so a new ingest image fed by an old extract image sees no PDF or JATS structure. Neither is a
failure the engine notices. The tells are in the receipt: `enrichment.structure` absent means an
old ingest image; present with `adapters` counting `pdf-layout/1: 0` beside `structure_missing: N`
means an old extract image. §7's release-order table carries this.

Size: a section or block is about 60–120 bytes of JSON; a long JATS article with 200 spans
is ~20 KB beside ~60 KB of text. Acceptable. A structure over 1 MB is dropped with the same
counter (a pathological PDF with a block per line), never truncated.

---

## 5. Roles: one vocabulary, one rule table, versioned

**`roles/1`** is a single flat vocabulary with no document-class switch. The classifier does not
need to know whether a document is a paper or a manual: the synonym tables of the two classes
barely collide, and where they do the rule is explicit.

| class | role | heading cues (`rules/1`, case-insensitive, after stripping numbering and punctuation) |
|---|---|---|
| paper | `abstract` | abstract, summary (when level 1 and first) |
| paper | `introduction` | introduction, background |
| paper | `methods` | methods, materials and methods, experimental procedures, study design, patients and methods |
| paper | `results` | results, findings |
| paper | `discussion` | discussion, results and discussion (→ `results` if preceded by a `methods`, else `discussion`) |
| paper | `conclusions` | conclusion(s), concluding remarks, summary (when last) |
| paper | `acknowledgements` | acknowledg(e)ments, funding, author contributions, conflicts of interest, competing interests |
| paper | `references` | references, bibliography, literature cited, works cited |
| paper | `supplementary` | supplementary material/information/data, supporting information, appendix |
| docs | `overview` | overview, about, what is …, introduction (when the document has an `installation` or `usage` sibling) |
| docs | `installation` | install(ation), setup, getting started, requirements, prerequisites |
| docs | `usage` | usage, how to …, running, tutorial, quick start, workflow |
| docs | `parameters` | parameters, options, arguments, configuration, reference, API reference, inputs |
| docs | `examples` | example(s), use cases, recipes |
| docs | `output` | output(s), results format, output files |
| docs | `troubleshooting` | troubleshooting, known issues, errors, debugging |
| docs | `faq` | faq, frequently asked questions, questions |
| both | `other` | a **titled** section whose title matched no rule |
| both | `unknown` | no title, or an **inferred** section whose heading matched no rule |

- `other` and `unknown` are different states. `other` is a positive statement: there is a real
  heading and it is non-canonical (the largest evidence-bearing class in the oracle,
  `section-taxonomy.md` §1). `unknown` is "could not say", which is what the owner asked for on
  inferred structure ("never a guess"). An untitled parsed section is also `unknown`.
- The one collision — `references` (bibliography) against `parameters` (API reference) — is
  decided by the plural: *References*, *Bibliography* → `references`; *Reference*, *API
  reference*, *Command reference* → `parameters`.
- `introduction` appears in both classes under one role; the docs cue only *refines* it to
  `overview` when the sibling test fires. Either answer is a correct `roles/1` value.
- The classifier fills `role` only where the adapter left it `None`, and writes
  `role_source="rules/1"`. A declared role (JATS `sec-type`) is never overwritten. **It takes the
  whole section list, not one title**: five of the rules above need context — *summary* is
  `abstract` when first and `conclusions` when last, *results and discussion* depends on a
  preceding `methods`, *introduction* becomes `overview` on an `installation`/`usage` sibling,
  and the stamp's inheritance (§6) walks ancestors. `classify_roles(sections) -> None` is one
  pass over the tree, deterministic for a given list.
- **Roles are inherited downward for stamping only** (§6): a level-2 section under *Methods* has
  no role of its own unless its title matches; the chunk's `section_role` is the nearest titled
  ancestor's role. The structure records what each section's title says; the stamp records what
  a reader would answer.

**Versioning.** Any change to the table, the normalisation or the collision rule bumps
`rules/N`. Any change to the vocabulary bumps both `roles/N` and `rules/N`, since a rule table
targets one vocabulary. The chunk carries `section_role_version` = the section's `role_source`
(`declared/1`, `rules/1`, `llm/1:…`), under the `symbol_glossary_version` precedent: it says which
producer wrote the value, so a backfill can be resumable and an evaluation can split by producer.

**JATS `sec-type` → `roles/1` (`declared/1`):** `intro` → `introduction`; `methods`,
`materials|methods`, `materials-and-methods` → `methods`; `results` → `results`; `discussion` →
`discussion`; `results|discussion` → `results`; `conclusions` → `conclusions`;
`supplementary-material` → `supplementary`; `cases`, `subjects` → `other`; anything else →
`other` with `role_source="declared/1"` (a declared type that is not in the map is still a
declaration). A `<sec>` without `sec-type` gets `role=None` from the adapter and goes to the
rules.

---

## 6. Chunk metadata: the stamp

`stamp(structure, start_char, end_char, content) -> dict` lives in
`ragstack/ingestion/structure.py`. The pipeline wraps it as `_stamp_structure(documents, chunks)`,
a shared helper in the shape of `_filter_boilerplate` / `_attach_glossary`, called from **both**
chunk loops — `prepare_documents` (paths A and B) and `iter_embed_source` (path C:
`embed_shard.py` → `run_embed_shard` → `iter_embed_source`, which never calls
`prepare_documents`) — between the chunk loop and `_filter_boilerplate`; `ingest_jsonl.py`'s own
loop (path D) calls the same helper. One helper, three call sites, one parity test. It writes
only when `doc.structure is not None`; a chunk of an unstructured document gets none of these
keys. Absent is a state (`chunk_metadata.json`
`x-ragstack-absence`): it means *not processed*, never *no section*.

| field | type | rule |
|---|---|---|
| `section_title` | string | the **owning section's** title. Owning = among the deepest sections overlapping `[start_char, end_char)`, the one with the largest overlap in characters; ties → the earliest. Omitted when the owning section is untitled. **Existing field, and the stamp changes its value on one producer:** today only JATS unit records fill it, with the unit *suffix* (`table-2`, `figure-1`, set in `article_records` and carried to chunks by the passthrough allow-list on path C). Under the stamp a unit chunk's `section_title` becomes the caption's label text (`Table 2`), which is what the schema's description ("the source document's own heading") always meant; the suffix survives in `source`/`path` (`PMC…#table-2`). Collections ingested earlier keep the suffix, and `structure_version` tells the two apart. Open question Q13. |
| `section_path` | list of strings | the owning section's `path`. Omitted when empty. |
| `section_level` | integer | the owning section's `level`. |
| `section_role` | string (`roles/1`) | the owning section's role, or the nearest titled ancestor's when the owning section has none; `unknown` otherwise. |
| `section_role_version` | string | the `role_source` of the section whose role was used. |
| `section_crossing` | boolean, **stamped only when `true`** | the chunk overlaps two or more *segments* (§7.1: a leaf section, or a parent's own text outside its children), i.e. its text is owned by more than one section (the `is_boilerplate` precedent: `false` is never written, so unstamped chunks stay "unknown"). Expected on the first chunk of most titled parents, because a heading-only parent segment travels with the following segment (§7.1); not a defect signal. Open question Q10. |
| `block_types` | list of strings | the sorted distinct `kind`s of the blocks overlapping the chunk. |
| `caption_labels` | list of strings | `label` of every `figure_caption` / `table_caption` block whose `start` lies inside the chunk (the label is at the start of a caption, so the chunk that holds the start holds the label). |
| `refers_to` | list of strings | labels cited in the chunk's own `content`, normalised to the same form as `label` (`Fig. 3`, `FIG 3`, `Figure 3A`, `Figs. 2 and 3` → `Figure 3`, `Figure 2`, `Figure 3`). Computed from `content`, not from structure, but stamped only for structured documents so it is versioned with them. |
| `structure_source` | string | `parsed` \| `inferred`, from the document. The field that lets a filter or an evaluation keep inferred structure out. |
| `structure_version` | string | `"<adapter>;stamp/1"`, e.g. `markdown/1;stamp/1`. Bumped when the stamp rules (owner selection, label normalisation, inheritance) change. Row-level audit, like `symbol_glossary_version`. |

**Where it is stored.** Chunk metadata lands in two layouts (`x-ragstack-store-shape`): flat at
the top of the Qdrant payload, and under `metadata.*` in Elasticsearch. All new fields are
declared in `contracts/schemas/chunk_metadata.json` **and** `metadata_schema.DECLARED_FIELDS`
(the test pins them field for field); strings and lists of strings map to `keyword` with
`ignore_above`, `section_level` to `long`, `section_crossing` to `boolean`. The ES mapping is
derived from the table. How it reaches an **existing** index is exactly `ensure_index`'s current
behaviour, no more: a new index is created with the full derived mapping; an existing one gets
one `put_mapping` of the full derived mapping, which succeeds when nothing already mapped
conflicts (the new fields themselves conflict with nothing), and on a divergent index (one whose
`metadata.year` is already a `keyword`, say) the whole push is rejected and the module falls back
to pushing the dynamic template alone. On such an index the new fields are typed by the template
on first write: the strings and string lists become `keyword` through
`metadata_strings_as_keyword`, and `section_level` / `section_crossing` fall to ES's own
inference, which gives `long` / `boolean` — the declared types, by luck rather than by decision.
Only `symbol_glossary` has a field-specific third push today; this series adds none, and
`metadata_conformance.py` reports the divergence as it does for every other field.
`section_level` is **not** added to `KNOWN_INT_FIELDS` in this series (that widens the
filter-side 400 and needs its own contract note, as the module says).

**List-valued fields and the in-memory predicate.** `section_path`, `block_types`,
`caption_labels` and `refers_to` are lists. Server-side, Qdrant and Elasticsearch match a scalar
filter against any element of a list (the `authors` precedent). The shared in-memory predicate
`stores/filters.py::payload_matches` does not: it compares `actual != value`, so a list-valued
payload never matches a scalar filter there — and `QdrantVectorStore.get_chunks` re-checks every
retrieved record through that predicate, as does `InMemoryVectorStore`. A filter on a list field
therefore already disagrees between the retrieval legs and the context-expansion path for
`authors`, and would for these four. Scalar fields (`section_role`, `section_title`,
`section_level`, `structure_source`) are unaffected. Fixing `payload_matches` to treat a list
`actual` as "any element equals" is a small change with its own test and conformance case; it
is a **prerequisite for offering a filter on any list field**, listed in PR 1, and not a reason
to store these as anything but lists.

**Identity: stamping is not in `spec_hash`.** `spec_hash` is `sha1(model|dim|chunk_descriptor)`
and `chunk_descriptor` is `method/size/overlap/{chunk_params}`. The stamp changes neither content
nor offsets nor ids; it adds payload fields, exactly as the symbol glossary and the boilerplate
verdict do. So **turning stamping on does not fork a collection and does not change which
collection an ingest resolves to.** Two consequences the owner should accept explicitly:

1. A collection can hold stamped and unstamped chunks at once (ingested before and after the
   switch). This is the glossary's situation too, and the remedy is the same: `structure_version`
   on every stamped chunk, a backfill script (`backfill_structure_metadata.py`, modelled on
   `backfill_symbol_glossary.py`) that re-parses and re-stamps without re-chunking, and the
   conformance report listing collections with partial coverage. A retrieval filter on
   `section_role` must not be offered in the UI for a collection with partial coverage.
2. Because stamping is not identity, **changing the stamp cannot silently fork a collection
   either** — it silently changes *metadata* on new ingests only. That is why `structure_version`
   is a stamped field and why a stamp change is a version bump, never an in-place edit.

Stamping is controlled by a deployment setting, `STRUCTURE_METADATA=off|on`, with a matching
`--structure-metadata` flag on `ingest_shard.py` / `embed_shard.py` / `ingest_jsonl.py` and a
`structure_metadata` CWL input the API seeds from the setting when the registered workflow
declares it — the `symbol_glossary` pattern (#783) exactly, including the ADR-0010 stamped-image
gate on the declared-inputs check. Default `off` until the accuracy check (§10) has run; then
`on` for new ingests. **A marker overrides the setting** (§7): a collection whose `chunk_params`
carry `structure_cuts` or `embed_heading_path` needs structure to chunk or embed at all, so the
one reader returns `needs_structure=True` and the adapters run and the stamp is written whatever
`STRUCTURE_METADATA` says, on every path; otherwise one spec would give two results by
deployment setting. What ran is recorded in the shard receipt under
`enrichment.structure = {"stamp": "stamp/1", "adapters": {"markdown/1": n, ...},
"structure_missing": n, "backend": "markdown-it-py/3.0.0"}` — the interim post-chunking
enrichment slot `ShardReceipt.enrichment` already holds for the symbol glossary
(`receipts.py`, "folds into ADR-0011's `core.activity.params` when ADR-0011 PR-3 lands") — and
in the collection manifest.

**A missing parser is a refusal, never a fallback.** The Markdown adapter needs the `[markdown]`
extra. When the adapters are asked to run (setting `on`, or a marker set) and the extra is
absent, the API refuses the ingest (503 naming the extra, like the token-counter refusal) and the
bulk tools exit at configuration; nothing degrades to "no structure" or to a regex. The fleet ran
a regex sentence segmenter for months because an optional import fell back silently (#815), and
the backend name in `enrichment.structure.backend` is the audit that would have caught it.

---

## 7. Opt-in behaviours that *are* identity: three `chunk_params` markers

Three things change the chunks or the vectors and therefore fork a collection by construction
(a fourth marker, `structure_infer`, opts a collection into the inferred text adapter and is
described in §9.5; it rides under the same discipline). Each is a `chunk_params` key under the
#829 discipline: absent = today's behaviour exactly;
present = applied; an unknown value is refused (400 at create, exit 2 on the bulk tools); the API
records defaults explicitly so a later default change cannot re-chunk; **one reader**,
`structure_from_params(method, params)` in `chunker_config.py`, serves the API's `_chunk_param_kwargs`,
`ingest_shard._build_chunker` and `ingest_jsonl`; a contradicting CLI flag is refused against a
registry entry. Because `chunk_params` is inside `chunk_descriptor()` → `spec_hash` → the physical
store name, a marked collection is a new collection with no schema change (`chunk_params` is
free-form in the contract).

| key | values | what it changes | why it is identity |
|---|---|---|---|
| `text_render` | absent (raw) \| `"markdown-plain/1"` | the Markdown adapter, at ingest (§2), replaces `content` with the stripped plain text of #828 §2 (headings on their own line without `#`; links as `text (url)`; images as alt plus target; inline HTML as visible text plus `href`; code verbatim; math verbatim; tables one row per line, cells joined by ` \| `; emphasis markers dropped). Applies to `.md` only; other suffixes are untouched. | content changes, so every offset, chunk and id changes |
| `structure_cuts` | absent \| `"sections/1"` | section boundaries become **preferred** cut points for every method (§7.1); the #831 merge never crosses one | chunk boundaries change |
| `embed_heading_path` | absent \| `"prefix/1"` | the embedded text is `" › ".join(section_path) + "\n" + content`; the stored `content`, the offsets and the ids are unchanged. Applied in the **embed input list only**, at the places a chunk's text is handed to an embedder: `IngestionPipeline._embed_and_link` (shared by `embed_source` and `iter_embed_source`: paths A and B, and path C once C can read markers — see below, it cannot in this series) and `ingest_jsonl.py`'s `_embed_drop_bad` (path D). It needs `section_path` on the chunk, so it forces structure and stamping (§6). | vectors change; the collection's vectors are no longer a function of `content` alone |

They are three keys, not one, because §11 evaluates each as its own arm; one key would couple
the arms and force a re-ingest to change any of them.

Which methods accept them: all six. `structure_cuts` on `fixed` (characters) is allowed but
pointless and logged; `embed_heading_path` on any method is a vector change only.

**`text_render` is applied at ingest, from the record's suffix.** The GoWe extract step cannot
apply it (§2: it has no spec), and it must not be a CWL input (#829: a second source for an
identity value is how #609 happened). So the stripped render is produced by the Markdown adapter
in the ingest step, on the raw text the shard carries, for records whose `path` ends in `.md`;
the in-process path does the same in the same helper. The extract step's output is identical with
or without the marker.

**Path C cannot read markers, so marked collections are refused there.** `scripts/embed_shard.py`
(the chunk-and-embed step of `jats-ingest.cwl`, `pdf-ingest.cwl` and `embed-bulk.cwl`) builds its
chunker from CLI arguments alone — `_build_chunker(args)` — and resolves no registry entry, so it
can see neither `chunk_params` nor the markers, and it cannot tell that it is working for a
marked collection. Two options:

- **(a)** give `embed_shard.py` the registry resolution `ingest_shard.py` has (`ingest_target`,
  the #563 pattern: the registry name on the submission, the DSN from the worker's secret file).
  Correct, and the eventual answer, but it changes an operator workflow's inputs and the tools
  image contract; **later, not in this series**.
- **(b), chosen for this series:** marked collections are **refused on path C at the point that
  does know the spec**, which is not `embed_shard.py` itself. Two gates, both with registry
  access today:
  1. `scripts/load_embeddings.py` — the load step inside `jats-ingest.cwl` and `pdf-ingest.cwl`,
     and `load-embeddings.cwl` run after `embed-bulk.cwl` (which itself ends at `merge_receipts`
     and has no load step; its embedding files reach a store only through that second workflow)
     — already resolves the target and runs `check_build` before writing. The embedding-file
     header gains a `chunking` object (the same shape as the receipt's: method, size cap, and
     now the structure markers as applied, absent when none). Load refuses, before any write,
     when the registry entry's `chunk_params` carry a structure marker the header does not show
     as applied (or the reverse). This is the authoritative guard: nothing reaches a store that
     was chunked or embedded under a different spec than the entry records — the ADR-0002
     build-spec guard, extended by one object. It needs a resolved registry target: under
     `--vector-backend memory` the tool resolves none (`target = None`; only `--collection-id`
     is required there), so the check cannot run and is skipped with a logged line — the memory
     backend owns no physical store, so nothing durable can be forked by it.
  2. The API's GoWe dispatch already refuses a collection whose method the deployed workflow
     cannot run (`_refuse_unrunnable_chunk_method`); the same check refuses a *marked*
     collection when `GOWE_WORKFLOW_CWL` names a workflow whose chunk step is `embed_shard.py`.
     Today the API's workflow is `pdf-ingest-scatter.cwl` (`ingest_shard.py`), so this gate is
     dormant, but it is what keeps a future workflow swap from re-opening the hole.
  3. `gowe_batch_ingest.py` already reads the registry read-only (`resolve_store_name`); it
     **must** also read the entry's `chunk_params` and refuse a marked collection before it
     submits anything, with a message naming the marker and option (a). This early refusal is
     **required**, not a convenience: without it the load-time guard fires only after the whole
     scattered embed has spent its GPU time, and the refusal is then a wasted run rather than a
     wasted command. The load-time guard stays the authoritative one (a hand-run `cwltool`
     submission bypasses the driver), and both are pinned by tests.

  Consequence: in this series the markers work on paths A, B and D; on path C a marked
  collection is refused and an unmarked one behaves exactly as today. That includes a collection
  carrying only `structure_infer` (§9.5), so a text-only paper corpus can be inferred through
  paths A, B or D only, until option (a) lands (Q14). Stamping (not identity) still runs on
  path C through `iter_embed_source` when `--structure-metadata` is on.

**Release order, for the whole design** (#829's trap, restated and widened). Three images are
involved and each can lag:

| what lags | what happens, silently | the tell |
|---|---|---|
| the **ingest** image (`ingest_shard.py` / `embed_shard.py`) predates this series | all three markers are ignored: a marked collection is chunked unmarked and embedded without the prefix; a structured shard is ingested unstructured | receipt has no `chunking.structure_cuts` / `text_render` / `embed_heading_path` keys and no `enrichment.structure` |
| the **extract** image (`pdf_extract.py` / `jats_extract.py`) predates it | no `structure` key in the shard, so PDF and JATS documents ingest unstructured; Markdown is unaffected (its adapter runs at ingest) | `enrichment.structure.structure_missing` counts them, `adapters` shows `pdf-layout/1: 0` |
| the **server** predates it | no marker can be recorded at create; stamping on an ingest the server runs in-process is off | `GET /v1/collections` shows no marker; no `structure_version` on new chunks |

No runtime guard, as the owner decided for #829; the images and the workflows roll first
(ADR-0010), and a marked collection is not created on a GoWe tenant until the receipt of a
scratch ingest shows the keys.

### 7.1 Section-preferred cuts and the #831 wall

The chunkers keep their algorithms; `structure_cuts` adds two rules at the seams they already
have:

- **Preferred cut.** Before packing (`sentence`, `words`, `semantic*`) or sliding (`fixed_token`),
  the document is split into *segments*. A segment is a maximal offset range owned by exactly one
  section: each **leaf** section is one segment, and a **parent's own text** — its heading and
  lead paragraphs before its first child, and any text after its last child — is a segment of its
  own, owned by the parent. (Invariant 3 does not make children partition their parent, so this
  is where the parent's text goes; it is never folded into the first child.) Each segment is
  chunked on its own with the collection's method and size, so a chunk never starts in one
  segment and ends in another. Offsets stay document-global (segments are offset ranges, not
  substrings), so ids are still `uuid5(doc_id:start:end)` and the chunks still tile the document.
- **Merge up to the cap.** Hard section walls produced a median chunk of 108 tokens in the
  exploratory run (#828 §4); that is the small-piece problem #831 addresses. Under
  `structure_cuts`, the #831 merge runs with one extra rule: a piece may merge with its neighbour
  only when both lie in the same segment, **or** both are whole segments under the same parent
  (a parent's lead text with its first child, or two sibling leaves). The result of the second
  kind is a chunk owned by the parent, and it is exactly when `section_crossing` fires: a chunk
  that overlaps two or more segments. The merge never joins a fragment of one segment with a
  fragment of another. Without #831's `size_tolerance` the merge target is `chunk_size` itself.
- **Headings travel with their body.** A `heading` block is never a segment's whole content when
  the next block fits with it; the segmenter attaches a heading to the segment that follows it.
  A consequence worth stating once: when a titled parent's own text is only its heading, that
  segment joins the first child's, so **the first chunk of most titled parents carries
  `section_crossing: true`**. Its prevalence in a structured collection is therefore expected —
  roughly one per titled non-leaf section — and §10 and §11 must read it as the heading-with-body
  case, not as a defect of the cutter.
- **`fixed_token` with overlap.** Overlap does not reach back across a segment boundary. The first
  window of a segment starts at the segment start.

What this does *not* do: it does not change `sentence_spans` or the semantic breakpoints inside a
segment, so `cap_split`, `breakpoint_cap` and `size_cap` compose unchanged; the segmenter runs
before them. The chunking study's labels (`section-taxonomy.md` § sentence coordinates) are a
different coordinate system regardless, as every chunking change is.

---

## 8. Carrying structure through every ingest path

[`ingest-paths.md`](../ingest-paths.md) names four paths and one pipeline. Structure must arrive
at the chunk loop identically on each — and there are three chunk loops, not one
(`prepare_documents`, `iter_embed_source`, and `ingest_jsonl.py`'s own).

| path | where the adapter runs | how structure travels | what proves it |
|---|---|---|---|
| **A. API, in-process** | PDF and JATS: inside the loader `default_loader_registry()` dispatches to (`build_ingestor_for`); Markdown and extracted text: the pipeline's `attach_structure` step | in memory, on `Document.structure` | the invariants (§2) on every loaded document |
| **B. API → GoWe** | PDF: `pdf_extract.py`, through the same registry (#830); Markdown: `attach_structure` inside `ingest_shard.py`'s pipeline, from the raw text the shard carries | PDF structure as the `"structure"` key in the shard JSONL → `JsonlLoader` validates and restores it; Markdown structure is built at ingest | **parity test:** `test_extract_text_parity.py` grows a structure half — for each fixture, the structure the GoWe path ends with equals the in-process structure field for field, and the stamped chunk metadata of the two paths is identical |
| **C. operator CWL** (`jats-ingest.cwl`, `ingest-bulk.cwl`, `pdf-ingest.cwl`) | `jats_extract.py` (JATS adapter, §9.2) or `pdf_extract.py` | the same JSONL key → `embed_shard.py` → `run_embed_shard` → `IngestionPipeline.iter_embed_source`, which does **not** call `prepare_documents`; the `_stamp_structure` helper (§6) is therefore called from `iter_embed_source` too, next to its existing `_filter_boilerplate` / `_attach_glossary` calls. **Markers are not honoured here** (`embed_shard.py` has no registry access): a marked collection is refused at `load_embeddings.py` by the header-vs-entry check of §7, and stamping runs only under `--structure-metadata` | the same parity test, driven through `embed_shard.py`; plus the load-time refusal test |
| **D. legacy `ingest_jsonl.py`** | none (it consumes JSONL) | the tool does **not** use `JsonlLoader` — it builds `Document` itself from `enrich(record)` at three sites — so it gets the structure key through the same shared reader `JsonlLoader` uses, `structure_from_record(record, text)` (validate, or drop with a counted `structure_dropped`); its own chunk loop calls `attach_structure`, `_stamp_structure` and the `embed_heading_path` prefix in `_embed_drop_bad`, and reads the markers through `structure_from_params` | #831's rule: the API, `ingest_shard` and `ingest_jsonl` are checked for parity in one test, which feeds the same structured shard to all three |

**#833 and this design.** Three of its four findings touch structure directly.

- *Item 2, JATS extracted three ways.* Today JATS `.xml` reaches a store by `jats_extract.py`
  (the parser), by the GoWe extract step (since #830: skipped, `no loader for .xml`), or
  in-process by reference, where an unknown suffix **falls back to `TextFileLoader` and ingests
  the raw XML as text** (`LoaderRegistry.load` uses `self._default`; the upload route's
  `_fail_unsupported_items` refusal runs only for uploads). The structure model needs **one JATS
  adapter** (§9.2), so: register `.xml` → a `JatsLoader` that wraps
  `jats.article_records(..., with_spans=True)` in `default_loader_registry()`, and make the
  in-process fallback for an unknown suffix a refusal with the same `no loader for <suffix>`
  error the other two paths record (item 1). Both are parity fixes and belong in their own PR
  with a parity test before the JATS adapter PR (open question Q7). **The second is a contract
  change:** a by-reference `POST /v1/ingest` of a `.rst` or `.csv` file today returns 200 and
  ingests the bytes as text; afterwards it returns 200 with a failed item carrying the constant
  error. CLAUDE.md's rule applies — `contracts/openapi.yaml` wording first, a conformance case,
  then both implementations.
- *Item 3, `.jsonl` twice on GoWe.* Harmless for structure as long as `JsonlLoader` round-trips
  the key unchanged; the parity test includes a double pass.
- *Item 4, metadata differs after the shard.* Structure is not metadata (§4), so it is not subject
  to `index_metadata`, the passthrough allow-list or `enrich()`. The stamped fields are written
  by the pipeline on both paths from the same structure, so they cannot differ by path unless the
  structure did, which the parity test catches. The pre-existing metadata differences stay
  #833's.

**JATS sections come from the XML, never from the record text.** `section_text` emits a
`## Title` line only for a titled `<sec>`; an untitled `<sec>` leaves no heading, so a
Markdown-style re-parse of the record text would merge every untitled section into its
predecessor (`structured-ingest.md` §6.1). The JATS adapter maps `Span`s; the Markdown adapter
is never run on JATS text.

---

## 9. Adapters: what each can and cannot detect

An adapter fills `Document.structure` and does nothing else. Each is a function in
`ragstack.ingestion` (not in `scripts/`, not in CWL JavaScript — `structured-ingest.md` §6.5),
called either by the loader for its suffix (PDF, JATS: the source is only available there) or by
the pipeline's `attach_structure` step at ingest (Markdown, extracted text: the text is the
source) — §2's placement table.

### 9.1 Markdown — `markdown/1`, `parsed`

Parser: `markdown-it-py` on the `commonmark` preset with `md.enable("table")` (tables are not in
the preset), `mdit_py_plugins.front_matter` and `mdit_py_plugins.dollarmath` (without it `$$…$$`
is a paragraph), under a new `[markdown]` extra beside `[pdf]` and `[chunking]`. The adapter
runs only when asked (§6: setting `on` or a marker), and a missing extra is then a refusal, not a
fallback. It never guesses with a regex, because `#` inside a fenced block is code and a regex
cannot know that.

**The BOM.** `TextFileLoader` keeps the byte-order mark as `content[0]`. Fed to the parser it
breaks the front-matter plugin (the opening `---` is no longer at column 0), and the front matter
then parses as a setext `h2` whose title is the YAML — the review's probe on the #830 parity
fixture showed exactly that. The adapter parses `content[len(BOM):]` and adds `len(BOM)` (1) to
every offset; `content` itself is untouched, and the preamble section still starts at 0.

One parse, two outputs:

- **Raw mode** (`text_render` absent). `content` is the file text as `TextFileLoader` reads it
  today, byte for byte. Block tokens carry line ranges (`token.map`, end-exclusive, covering the
  whole line including its newline); a line-offset table over `content` turns them into
  character offsets. ATX and setext headings open sections; a section closes at the next heading
  of equal or higher rank or at EOF, and its `end` is then extended to the next section's `start`
  (§2 invariant 3); the `heading` block is the title text only (§2 invariant 5: after up to three
  spaces of indentation, the `#` run and its following spaces, and before any closing `#`
  sequence and trailing spaces). A setext heading's content may span several lines; **the title
  is its first line** — a decision, made because the stamp wants one line a reader would quote —
  and the heading block covers that first line only. The title-equality test fixtures
  single-line setext headings only; a multi-line setext heading is covered by a separate test
  asserting the first-line rule, not equality with the whole content. Front
  matter is a `front_matter` block; fenced and indented code are `code`; pipe tables `table`;
  lists `list`; blockquotes `quote`; a paragraph consisting only of an image is `figure`, and if
  its alt or the following paragraph starts with a caption label it is also a `figure_caption`;
  `math_block` tokens are `math`; `html_block` and everything else is `prose`.
- **Stripped mode** (`text_render="markdown-plain/1"`). The same token stream is rendered through
  the emitter of §2 with the #828 §2 rules; sections and blocks are recorded at emitter positions.
  A test asserts every content word of the source survives (link targets, code, table cells,
  math), and that `content[s.start:s.end]` for a `heading` block equals the heading text. The
  review's probe ran this emitter over the #830 parity fixture: every needle (link targets, the
  `$VAR` and `${HOME}` forms, code lines, table cells including an escaped pipe, the maths, the
  text inside `<details>`) survived, and the heading blocks equalled their titles exactly.

Detects: headings and their nesting, the block kinds above, image captions by label. Cannot
detect: tables drawn in HTML (`<table>` is inline HTML and stays `prose`), semantic roles beyond
the heading text (that is the classifier's job), and anything about a document with no headings
(it gets one untitled level-1 section, which is still a valid, honest structure).

### 9.2 JATS and open-access XML — `jats/1`, `parsed`

Source: the existing `Span` list from `article_spans` / `article_records(with_spans=True)`. The
mapping is mechanical:

- `kind="sec"` → `Section(level=depth+1, title=span.title, locator=span.path)`, with `end`
  extended to the next sibling's `start` or the parent's `end` so the `"\n\n"` joins that belong
  to no `Span` fall inside a section (§2 invariant 3); `role` from `sec-type` via the
  `declared/1` map (the adapter must start reading `sec-type`, which nothing does today);
  `kind="abstract"` → a level-1 section titled `Abstract` with `role="abstract"`,
  `role_source="declared/1"`, extended likewise over the abstract–body join; body-lead paragraphs
  before the first `<sec>` → the untitled level-1 section.
- `kind="title"` → `heading` block over the title text only: the span covers the rendered
  `## Title` line, so the block starts `min(depth+2, 6) + 1` characters after `span.start` and
  ends at `span.end`. `p` → `prose`; `list` → `list`; `disp-quote` → `quote`;
  `disp-formula` → `math`; `preformat`/`code` → `code`; `label` → `prose`.
- Table and figure **unit records** (`content_type=table|figure`) are separate documents today
  and stay so; each gets a one-section structure (`title` = the caption's label text,
  `role="supplementary"` only if the float sat in `<supplementary-material>`, else `None`) and a
  `table_caption` / `figure_caption` block over the caption span plus a `table` / `figure` block
  over the body, so `caption_labels` works on them. The article record's `refers_to` resolves to
  those labels across records of the same `pmcid`.

Detects: everything the XML states. Cannot detect: anything the publisher did not tag (an
untitled `<sec>` is a real untitled section, not a failure), and `sec-type` is absent on most
non-PMC-style articles, which is why the rules exist.

### 9.3 HTML — `html/1`, `parsed` (later)

`h1`–`h6` outline for sections, `<figure>/<figcaption>`, `<table><caption>`, `<pre>/<code>`,
`<ul>/<ol>`, `<blockquote>`; visible text rendered through the same emitter as the Markdown
stripped mode (an HTML page has no raw mode worth storing). Not in the PR series of §12; listed
so the model is shown to fit it.

### 9.4 PDF — `pdf-layout/1`, `inferred`

Source: PyMuPDF `page.get_text("dict")` — blocks, lines, spans with font size, flags (bold) and
bounding boxes — walked in the same order `get_text()` emits plain text.

**Content must not change.** `PdfLoader` stores `"\n".join(page.get_text() for pages).strip()`.
The adapter accumulates the dict walk's line texts with the same joins and asserts the result
equals the loader's `content` byte for byte; **if they differ for a document, the adapter emits
no structure for it** and counts the reason (`structure_skipped: text-mismatch`). Offsets into a
text that is not the stored text are worse than no structure. (The accuracy check reports how
often this fires; if it is common, the loader itself is switched to the dict walk under a
`text_render` value, which is identity, so no existing collection moves.)

Heading signals, each a feature with the rule table versioned in `adapter`:
font size above the body mode by a threshold; bold flag; a line alone in its block; fewer than
~12 words; matches a known section name (the `rules/1` cue table); numbered (`2.`, `2.1`,
`III.`). A line is a heading when it clears a score; its level comes from clustering the heading
font sizes (largest = level 1). Running headers and footers: a line repeated on ≥ 3 pages at the
same y-band is removed from candidacy (not from `content`). Captions: a line starting with a
caption label whose font differs from the body, or any such line immediately below/above an
image block; the block extends to the next body-font line. Tables: a block whose lines have a
high ratio of numeric tokens and a low `function_word_ratio` (reused from `boilerplate.py`), or
whose spans have many x-gaps; marked `table` even though the grid is not rebuilt. Reference list:
after a `references` heading, or a run of blocks matching the citation density test the
boilerplate classifier already has (`reference_signal_density`); marked as a `references` section
only when a heading is found, otherwise as blocks of kind `prose` inside whatever section they
fall in (the boilerplate verdict still handles them per chunk).

Cannot detect reliably: two-column reading order (PyMuPDF emits blocks in content-stream order,
which is usually but not always column-major; a boundary falling mid-sentence is the symptom,
and §10 counts it), headings set in the body font with only spacing as the cue, section nesting
deeper than two levels, and footnotes.

### 9.5 Extracted text — `text-heuristic/1`, `inferred`

For documents available only as the text extracted at an earlier ingest (no PDF). Layout is
gone; the signals are: a short line alone between blank lines; matches a known section name;
ALL CAPS; a numbered heading (`2.1 Results`). Captions by label pattern at line start only.
Tables by the numeric-token / function-word test over runs of lines. Anything unrecognised is
`unknown`; the adapter emits sections only at the headings it is confident of and leaves the
rest of the text in untitled sections. Expected weaker than 9.4, and the owner's instruction
applies: **if the PDFs still exist, re-extract from them** and run 9.4 instead.

**It runs only on an explicit opt-in, and never as a fallback.** The trigger is a fourth
`chunk_params` marker, `structure_infer = "text-heuristic/1"`, read by the same one reader as the
other three. A marker rather than a deployment setting, for three reasons: inference is a
property of a *corpus* (the text-only paper collections), not of a deployment, and a setting
would reinterpret every PDF record lacking a structure key across every collection on the
host; the choice must be recorded on the collection so §10 and §11 can split by it; and it
should fork the collection, since the §11 arms that need it are new collections anyway (the
identity cost is accepted knowingly, although stamping alone would not require it). What the
marker does **not** do: it is never triggered by `STRUCTURE_METADATA=on` alone; it never
substitutes for a `.pdf` record that arrived without a structure key — that record is still
counted as `structure_missing` (the old-extract-image tell of §4 and §7) *and then* inferred, so
the two counts stay separate and the tell survives; and plain `.txt` without the marker stays
structure-absent, per the #828 model table. With the marker, a `.txt` record is inferred too.

---

## 10. The accuracy check (before anything filters or cuts on inferred structure)

A hand-labelled sample, labelled against the stored `content` so labels are offsets in the same
coordinate system as the structure. The sample, the labels and the per-document results stay
private on the run host (document ids, page names and text are private for every corpus
involved); the public record under `docs/plans/results/structure-accuracy-<date>/` carries counts,
metrics and the labelling protocol, in the three-tier discipline of `docs/papers/README.md`.

**Sample** (counts are the proposal; the owner sets them):

| stratum | n | adapter |
|---|---|---|
| Markdown documentation pages, from the 336-page collection | 30 | `markdown/1` |
| JATS articles **with** `sec-type` on top-level `<sec>` | 30 | `jats/1` (`declared/1`) |
| JATS articles **without** `sec-type` | 30 | `jats/1` + `rules/1` |
| PDF papers (the PDF-only corpus) | 40 | `pdf-layout/1` |
| the same papers' previously extracted text | 40 | `text-heuristic/1` |

Selection by seeded random draw from each population; the seed and the id list hashes are
recorded. Two labellers: 100% double-labelled on the PDF stratum (the one adapter that can be
badly wrong, and the one whose labels are hardest), a 20% overlap subset on the others.
Agreement is reported before the metrics, with the unit stated: for boundaries the unit is the
**line** (a line is or is not a section start, matched at one-line tolerance), for roles the
matched section, for captions the labelled caption. Because section starts are 1–2% of lines,
κ on boundaries is dominated by the prevalence; PABAK and raw agreement are reported beside it.

**Labels per document:** the ordered list of section starts (offset), titles, levels and roles
(from `roles/1`); every caption's `[start, end)` and label; every table body's `[start, end)`;
for each caption citation in the body, the caption it refers to. For PDFs additionally a
per-document checklist of the failure modes: two-column order broken, running header/footer in
`content`, caption splitting a paragraph, reference list present as body.

**Metrics:**

- Section boundaries: precision and recall of inferred section starts against labelled starts,
  at exact offset and at a one-line tolerance; reported per adapter, and for `pdf-layout/1` also
  per failure-mode checklist state.
- Roles: accuracy given a matched boundary; per-class precision and recall over `roles/1`
  (precision's denominator is *sections the classifier assigned that role*, recall's is
  *sections labelled with it*; `unknown` is a class like any other, so a wrong `unknown` is a
  recall miss on the true class and a precision miss on `unknown`); the confusion matrix; split
  by `role_source` (`declared/1` vs `rules/1`) and by `structure_source`. Every proportion
  carries a Wilson 95% interval, and a class with fewer than 10 labelled sections is reported
  but not gated on.
- Captions and tables: precision and recall of `figure_caption`, `table_caption` and `table`
  blocks, a match being IoU ≥ 0.5 between the inferred and the labelled span (not the fraction
  of the labelled span covered, which a block that swallows the following paragraph would pass);
  label accuracy on matched captions.
- Citation resolution: the share of labelled `refers_to` citations whose resolved label matches
  the labelled caption; the wrong-caption rate separately from the unresolved rate.
- Agreement between `section_role in {references, acknowledgements}` and the existing boilerplate
  verdict `section`, as a descriptive table (open question Q9).

**Gates** (pre-registered; the numbers are the proposal): a `section_role` filter is exposed only
for `structure_source=parsed` until inferred role precision ≥ 0.85 and boundary recall ≥ 0.80 at
one-line tolerance on the PDF stratum, **both judged on the lower bound of the Wilson interval**,
not the point estimate (40 documents give a few hundred sections; a point estimate of 0.86 with
a lower bound of 0.78 has not cleared the bar); `text-heuristic/1` roles stay `unknown`-only
unless its precision clears the same bar the same way. §11's results are reported split by adapter in any case, so a
null on an arm is attributable.

---

## 11. The exploratory retrieval-only evaluation (#828 §5)

**Harness.** The in-process, no-store, no-LLM harness `scripts/eval/section_aware_trial.py`
established (BM25 as Lucene approximates it + exact dense cosine + the live reranker, the served
pipeline's fusion), extended so that every arm is built through `ragstack.ingestion` — the real
loaders, adapters and chunkers under the markers of §7 — never a lookalike. Pre-registered as
exploratory, with the record under `docs/plans/results/`. The question sets are the existing
part-4 sets (two-passage and single-passage) on the documentation collection, and a JATS set and
a PDF-paper set built by the same procedure; this note names no question, page or document.

**Metric.** Delivery of the answer passage(s) within 2,048 and 4,096 tokens and at a fixed
top-10, per #831; chunk-size distributions beside every arm.

**Factors** (each a marker or a harness-only transform):

| factor | levels |
|---|---|
| text | raw \| stripped (`text_render`) — Markdown corpus only |
| cuts | none \| section-preferred (`structure_cuts`) |
| embedded text | content \| heading path + content (`embed_heading_path`) |
| caption placement | inline \| separate \| attached \| table bodies excluded — harness-only transforms over the structured document, not ingest options until one wins |
| size rule | strict 512 (#829) \| 512 ± 25% with merge (#831), the merge honouring the section wall |
| `cap_split` (semantic arms) | greedy \| balanced \| topic |

Full factorial is 2 × 2 × 2 × 4 × 2 × 3 = 192 arms per corpus; not run. Design: a base arm
(`fixed_token` 512/0, raw text, no structure — the part-4 confirmatory configuration), then each
factor varied alone against the base, then the best single-factor levels combined, then that
combination under the semantic method with the three `cap_split` values. Contrasts are paired per
question, Holm-corrected within each corpus, and reported split by `structure_source` for the
PDF corpus so that the §10 accuracy numbers sit beside every inferred-structure result. A null on
an inferred arm with poor §10 numbers is "detection failed", not "structure does not help".

---

## 12. Implementation plan: small PRs, tests per PR

Each PR is reviewed before merge and mutation-checked ("what is the narrowest mutation that
still passes?"). Order is chosen so metadata lands before cutting, parsed before inferred, and
every carrier hop is proven before the next adapter needs it.

| PR | content | tests |
|---|---|---|
| **1 — model and stamp** | `Section`/`Block`/`DocumentStructure`/`Document.structure`; `structure.py` with `validate_structure` (§2 invariants) and `stamp`; the `rules/1` table and `classify_roles(sections)`; the pipeline helpers `attach_structure` and `_stamp_structure`, called from `prepare_documents` **and** `iter_embed_source`, and from `ingest_jsonl.py`'s loop; schema: `chunk_metadata.json`, `metadata_schema.DECLARED_FIELDS`, ES mapping derived; `pdf_extract.py` writes the key; one shared reader `structure_from_record(record, text)` (validate or drop with a counted `structure_dropped`) used by `JsonlLoader` **and** by `ingest_jsonl.py`'s three `Document(...)` construction sites, which bypass `JsonlLoader`; `STRUCTURE_METADATA` setting, `--structure-metadata` on the three tools, the CWL input; receipt `enrichment.structure`; `payload_matches` treats a list `actual` as any-element (the list-filter prerequisite, §6); the `jats.py` docstring rewrite (§4). No adapter yet: structure only arrives via JSONL or a test. | invariant property tests (nesting, partition with extended ends, half-open, heading block first-in-section); stamp is pure and total (every chunk of a structured doc gets `section_level`, `structure_source`, `structure_version`); owner-selection ties; `caption_labels`/`refers_to` normalisation table; schema pin test; `Document.model_dump()` byte-identical when `structure is None`; `JsonlLoader` and `ingest_jsonl` both drop an invalid structure with a counted reason and keep the document, and ignore the key byte-for-byte when absent; the same structured shard stamps identically through `prepare_documents`, `iter_embed_source` and `ingest_jsonl` (the parity test feeds one shard to all three); `payload_matches` list test plus a conformance case; mutation: remove the validation and the test fails. |
| **2 — Markdown raw mode on both paths** | `markdown/1` raw mode in `attach_structure` under the `[markdown]` extra (preset + `table` + `front_matter` + `dollarmath`; BOM-shifted parse; title-only heading blocks; extended section ends); the refusal when the extra is missing and the adapter was asked for; parity test growth. | adversarial fixture (the one in `test_extract_text_parity.py`: BOM, front matter, CRLF, fences containing `#`, setext headings, tables, images, `$$` maths); front matter is a `front_matter` block and **not** a heading; heading blocks equal titles after inline-markup removal; structure built by `ingest_shard` from the shard equals the in-process structure field for field; stamped metadata identical on both paths; `STRUCTURE_METADATA=on` without the extra: refused, and `content` untouched. |
| **3 — `text_render` and the one reader** | `structure_from_params` (returns the four markers — `text_render`, `structure_cuts`, `embed_heading_path`, `structure_infer` — plus `needs_structure`); `text_render="markdown-plain/1"` stripped mode, applied at ingest from the record's suffix; API create validation and explicit recording; `ingest_shard`/`ingest_jsonl` read through the reader and refuse contradicting flags; **path C**: the embedding-file header gains a `chunking` object with the markers as applied, `load_embeddings.py` refuses a header-vs-entry mismatch before any write (skipped, logged, under the memory backend, which resolves no target), `gowe_batch_ingest.py` refuses a marked collection **before submitting** (required: the load-time guard alone fires after the scattered embed has spent its GPU time), and the API's GoWe dispatch refuses a marked collection on a workflow whose chunk step is `embed_shard.py` (§7, option b); receipt `chunking` keys; `CHUNKING.md` section. | every-word-survives test (links, code, cells, math); heading block text equals title exactly; absent marker → `content` byte-identical to `TextFileLoader` on both paths; the GoWe shard is identical with and without the marker (the extract step does not see it); unknown value → 400 / exit 2; `chunk_descriptor` changes with the marker (identity proven); **`load_embeddings` refuses** an embedding file whose header shows no markers against an entry that records one, and the reverse, with nothing written (mutation: drop the check → a marked entry accepts an unmarked file → fails); an unmarked entry with an old header (no `chunking` object) loads exactly as today; `gowe_batch_ingest.py` exits before any submission for a marked entry (mutation: drop the pre-check → a submission is built → fails). |
| **4a — JATS parity (#833 items 1–2), a contract change** | `.xml` → `JatsLoader` in the registry; the in-process unknown-suffix fallback becomes a refusal with the shared error. Contract first: `contracts/openapi.yaml` wording for `/v1/ingest` by reference (a file with no loader is a failed item with `no loader for <suffix>`, where today it is ingested as text), a conformance case, then the Python change and the Go side's equivalent. | three-path parity test for a JATS fixture (in-process, `pdf_extract.py`, `jats_extract.py`): same `content`, same doc id, same metadata subset; unknown suffix refused identically on A and B; the conformance case passes against both implementations. |
| **4b — JATS adapter** | `jats/1` from `Span` + `sec-type` → `declared/1`; unit-record structures. | fixture with untitled `<sec>`, nested `<sec>`, `sec-type` present and absent, floats in `<floats-group>`; untitled section is a first-class section; `section_title` on article chunks is the heading, not the unit suffix; declared role never overwritten by rules. |
| **5 — accuracy-check harness** | `scripts/eval/structure_accuracy.py`: label format, κ, the §10 metrics, the public export with the part-4 allow-list discipline; the labelling run itself is an owner-scheduled activity, its record a results directory. | metrics on a synthetic labelled fixture; the exporter refuses any string that is not a short safe token. |
| **6 — `structure_cuts` and `embed_heading_path`** | segmenter in `chunkers.py` (§7.1); heading attached to the following segment; `embed_heading_path` applied in `_embed_and_link` and in `ingest_jsonl._embed_drop_bad` to the embed input only; both markers force `needs_structure`; receipt `chunking` keys. **Depends on #831's merge PR** (opt-in `size_tolerance` / `min_piece`, in progress separately): if it has landed, this PR adds the section wall to its merge; if not, this PR ships the cuts without any merge and the wall is added in #831's PR, which then depends on this one. The table assumes the first order. | chunks tile the document under every method; no chunk spans two leaf sections; sibling merge sets `section_crossing`; `fixed_token` overlap does not cross a segment; stored `content` unchanged under `embed_heading_path` on all three embed loops (mutation: prefix leaks into `content` → fails); `embed_heading_path` with `STRUCTURE_METADATA=off` still stamps and prefixes (mutation: honour the setting → fails); absent markers reproduce today's spans against the #829 baseline fixture. |
| **7 — inferred adapters and captions** | `pdf-layout/1` with the text-identity guard; `text-heuristic/1`; caption and table blocks; `refers_to` for all adapters. | fixture PDFs built in-test (reportlab) with known headings, a two-column page, a running header, a caption mid-paragraph, a reference list; text-mismatch guard emits no structure (mutation: emit anyway → fails); every inferred section has `structure_source=inferred`; unrecognised → `unknown`. |
| **8 — evaluation arms** | the §11 harness extensions, the placement transforms, the pre-registration template. | arms built through `ragstack.ingestion` only (a test imports nothing else); result files hold counts and ids only. |
| **9 — rollout** | `backfill_structure_metadata.py`; conformance report row for partial coverage; docs (`CHUNKING.md`, `ingest-paths.md`, `cookbook-users.md`); tools image + workflow stamp (ADR-0010); `STRUCTURE_METADATA` default flipped to `on` only after §10's record exists. | backfill is idempotent and resumable by `structure_version`; dry run writes nothing; a non-production tenant is its only live target. |

PRs 1–3 deliver the owner's minimum (section type as metadata, on Markdown, on both paths).
PRs 4–5 make JATS and the accuracy check real. PR 6 is the chunking change. PR 7 is the only
one that can be wrong about a document, and it lands after the thing that measures it.

---

## 13. Compatibility, stated plainly

- **Existing collections are untouched.** No marker → the loaders return the same `content`,
  the chunkers return the same spans and ids (proven against the #829 baseline fixture), the
  stores receive the same payload. `STRUCTURE_METADATA=off` → no new metadata keys at all.
- **New metadata on an old collection** (stamping on, no marker) adds declared fields to new
  chunks only; `structure_version` makes the mixture auditable and the backfill makes it whole.
  No filter on the new fields is offered for a partially covered collection.
- **A marker is a new collection**, by content address, exactly as `size_cap` is. Nothing
  converts in place; the UI's create form gains the three fields the way it gained `size_cap`.
- **Old receipts, manifests, embedding files and archives read unchanged**: every new key is
  optional and omitted when absent (`chunking`/`enrichment` precedent). A restored archive
  carries whatever stamped fields its chunks had; it is never re-stamped by restore.
- **Elasticsearch**: new declared fields reach existing indices through the existing
  `put_mapping` path; a divergent index that rejects the full mapping falls back as today and
  is listed by `metadata_conformance.py`.
- **Go**: the Go server treats chunk metadata as an opaque map; the contract change is the
  schema file, which both implementations are checked against by conformance. No Go code change
  is expected for the metadata and chunking PRs. **PR 4a is the exception**: the unknown-suffix
  refusal on by-reference ingest is contract-visible, so it needs the `openapi.yaml` wording, a
  conformance case, and the Go side's equivalent behaviour (per CLAUDE.md's rule for API
  changes).
- **The chunking study's pins** (`EXPECT_COMMIT`, `sentence_spans_fingerprint`) are unaffected:
  `sentence_spans` does not change; `structure_cuts` runs before it on offset ranges.

---

## 14. Red team: how this design fails, and what catches each failure

| failure mode | how it would show | what catches it |
|---|---|---|
| **Offsets drift** — a transform runs on `content` after structure exists (a `.strip()`, a newline normalisation, a BOM removal) | `content[start:end]` is off by a few characters; `section_title` lands on the wrong section near the edges; `heading` blocks do not equal titles | §2 invariants 1–5 asserted on every load (`JsonlLoader`, in-process); the parity test compares structures field for field after the JSONL hop; every adapter runs after the loader's normalisation (inside the loader for PDF/JATS, in `attach_structure` for Markdown), and the BOM is handled as a parse-input shift, not a strip |
| **Structure built from a different text than the stored one** (PDF dict walk vs `get_text()`) | plausible sections, wrong positions, silently | the byte-identity guard in `pdf-layout/1`: mismatch → no structure, counted; §10 reports the rate |
| **Heading false positives in PDFs** — bold figure labels, running headers, author affiliations, the first line of every page | hundreds of tiny sections; `section_title` is a page header; `structure_cuts` shreds the document | running-header suppression by repetition; the score threshold; §10 boundary precision per PDF; `structure_source=inferred` lets filters and the evaluation exclude it; the §7.1 merge recombines tiny siblings |
| **Two-column order** — blocks emitted column-interleaved | a section boundary falls mid-sentence; `refers_to` and captions land in the wrong section | §10's checklist counts it; a diagnostic: the share of section starts that are not sentence starts (cheap, computable on every PDF, logged) |
| **Reference list parsed as body** | the bibliography is `prose` in the last titled section, retrievable as evidence | `references` role via heading rules; the boilerplate verdict still runs per chunk and is independent; §10 reports the agreement between the two; `--boilerplate drop` is a config switch today |
| **Role mislabels propagating into filters** — `section_role=methods` on a results section | a `methods`-only query misses evidence silently | roles are hand-checked (§10) before any filter is offered; `section_role_version` names the producer; inferred roles are gated; the vocabulary has `other` and `unknown` so the classifier is never forced to guess |
| **Markdown regex shortcut** — a `#` inside a fence or a `---` setext line read as a heading | code comments become sections; front matter opens a section | CommonMark parser only, no regex fallback; the adversarial fixture contains both cases |
| **Untitled JATS sections merged into their predecessor** | 39% of evidence-bearing sections lose their boundary | JATS structure comes only from `Span`s, never from re-parsing `## ` lines; fixture with untitled `<sec>` |
| **Repeated text aliasing** — the same heading twice in a document | a search-based locator picks the first occurrence | offsets are accumulated, never searched (§2); the stamp uses offsets, not titles |
| **Mixed collection** — stamped and unstamped chunks, or two `structure_version`s | a `section_role` filter under-returns with no error | `structure_version` on every stamped chunk; the conformance report; the backfill; no filter offered on partial coverage |
| **Old worker image ignores a marker** (the #829 trap) | a marked collection chunked unmarked, no error | receipt `chunking.structure_cuts`/`text_render`; release order documented; the `[chunker]` stderr line |
| **Old extract image writes no `structure` key; old ingest image ignores one** | PDF and JATS documents ingest unstructured with no error | `enrichment.structure` absent (old ingest image) or `structure_missing` counting (old extract image); §7 release-order table |
| **One spec, two results by deployment setting** — `embed_heading_path` set but `STRUCTURE_METADATA=off` on one host | the same collection embedded with and without the prefix | the markers force `needs_structure`; the PR-6 mutation test |
| **Silent parser fallback** (the #815 class) — `[markdown]` extra missing in one image | structure silently absent on one path, present on the other | refusal when the adapter was asked for; `enrichment.structure.backend` names the parser |
| **A list field filtered through the in-memory predicate** | context expansion and in-memory stores return nothing for `{"block_types": "table"}` while the ES/Qdrant legs match | `payload_matches` any-element fix as a prerequisite in PR 1, with a conformance case |
| **Stripped text changes token counts** | chunk sizes and counts differ from the raw collection | by design: `text_render` is identity; the two are different collections |
| **`refers_to` false positives** — "Table 2 of [12]" cites another paper's table; "Fig. 1A–C" variants | wrong caption linked; the attached-placement arm attaches the wrong text | normalisation table with tests; §10's wrong-caption rate is a first-class metric; `refers_to` is metadata, never moves text until an arm wins |
| **Duplicate labels** — two "Figure 1" (main and supplementary) | `caption_labels` ambiguous | labels keep their prefix (`Figure S1` ≠ `Figure 1`); duplicates within a document are counted in §10 |
| **Heading path leaks into stored content** | `content` no longer equals `doc.content[start:end]`; ids still match, text does not | PR-6 test: stored `content` byte-identical under `embed_heading_path`; the prefix is applied to the embed input list only |
| **Structure bloats the JSONL** — a block per line on a pathological PDF | shard files grow, `JsonlLoader` slows | size ceiling with a counted drop; blocks need not cover the text, so an adapter may emit fewer |
| **Filter grammar surprises** — `section_path` is a list | a filter on it matches any element | the same as `authors` today; documented in the schema description |
| **Two sources for an identity value** (a CLI flag beside the entry's `chunk_params`) | #609 again | the one reader; flags refused when they contradict the entry; no CWL input for the markers, which is possible only because `text_render` is applied at ingest (§2, §7) |

---

## 15. Open questions for the owner, each with a recommendation

1. **Store the whole structure per document (for display and re-stamping without re-parsing)?**
   Recommendation: not in Qdrant or Elasticsearch. Keep the JSONL `structure` key as the carrier
   and `--spans-out` as a debugging side file. What a backfill (Q12) needs differs by format:
   a Markdown-raw collection's `content` is reconstructable from its chunks (they tile the
   document; `ingestion/reassembly.py` exists for this) and the adapter re-runs on it; a JATS
   collection's structure can only come from the XML (§8), which a backfill must find by `pmcid`
   in the harvest tree the collection was built from. So: no archive role in this series, and the
   JATS backfill is conditional on that tree being available; where it is not, the collection is
   not backfilled. If the owner wants JATS backfills to be unconditional, the alternative is an
   archive role `structure.jsonl.gz` written by the archive step from the shard's key, which is a
   `ragstack-archive/1` manifest change.
2. **One `chunk_params` key or three?** Recommendation: three (`text_render`, `structure_cuts`,
   `embed_heading_path`). Each is an evaluation arm in §11 and each is identity on its own; one
   key would force a new collection to change any of them. With `text_render` applied at ingest
   from the record's suffix (§7), none of the three needs a CWL input or an extract-step change,
   so the three-key shape costs nothing on the workflow side.
3. **Is metadata stamping identity?** Recommendation: no — a deployment setting with a per-chunk
   `structure_version`, default off until §10 has run, then on, with a backfill; the symbol
   glossary precedent. The costs, stated: mixed coverage within a collection (auditable, not
   impossible); the markers `structure_cuts` and `embed_heading_path` must *force* stamping
   regardless of the setting, or one spec gives two results (§6, §7); and on JATS unit records the
   stamp changes the meaning of an existing field, `section_title` (Q13).
4. **One role vocabulary without a document-class switch?** Recommendation: yes, `roles/1` as the
   union, with the plural rule for *References* vs *Reference*. A class switch would need a new
   per-collection setting and buys one disambiguation.
5. **`role_source` spelling: `declared` or `declared/1`?** Recommendation: `declared/1`; the
   `sec-type` map is a versioned table too.
6. **Stamp inferred roles from the start, or `unknown` only until the gate passes?**
   Recommendation: stamp them with `structure_source=inferred` from the start so §10 and §11 can
   measure them, but expose no filter on inferred roles until the gate passes; `text-heuristic/1`
   stays `unknown`-only unless it clears the bar.
7. **Fix #833 items 1–2 (the `.xml` loader and the unknown-suffix fallback) inside this series?**
   Recommendation: yes, as PR 4a, before the JATS adapter — one JATS adapter is a premise of the
   model, and the in-process raw-XML-as-text fallback is a live defect. Note it is a **contract
   change** (a by-reference ingest of an unknown suffix goes from "ingested as text" to "failed
   item"), so it carries the `openapi.yaml` wording, a conformance case and both implementations,
   which is more than a loader fix.
8. **Caption placement: ingest option or harness-only?** Recommendation: harness-only until an
   arm wins; if `separate` or `attached` wins it becomes a `text_render`-class marker because it
   changes content.
9. **Should the boilerplate verdict (`section`) consult `section_role`?** Recommendation: not in
   this series; report their agreement in §10 and revisit with numbers.
10. **Add `section_crossing` (stamped only when true)?** Recommendation: yes; it is the one field
    that tells a reader whether a chunk's `section_title` is the whole story, and it is free.
11. **`level` as tree depth or raw heading rank?** Recommendation: tree depth; the rank is a
    Markdown-only notion and the source still has it.
12. **Backfill existing collections' metadata without re-chunking?** Recommendation: yes for
    Markdown-raw collections (reassemble `content` from the chunks, re-run the adapter, re-stamp)
    after §10, a non-production tenant first; for JATS collections only where the harvest tree is
    still available to re-read the XML by `pmcid` (§8: JATS structure never comes from the record
    text; see Q1); no backfill for PDF collections until the inferred adapter clears its gate.
13. **`section_title` on JATS table and figure unit chunks: keep the unit suffix (`table-2`) or
    stamp the caption label (`Table 2`)?** Recommendation: stamp the label. The schema already
    defines the field as the document's own heading, the suffix stays available in `source`, and
    `structure_version` separates old rows from new. The alternative — the stamp writes
    `section_title` only when the loader left it empty — would make the stamp non-uniform across
    producers, which is the kind of special case this model exists to remove.
14. **`structure_infer`: an identity marker in `chunk_params` (forks the collection), or a
    recorded, non-identity per-collection setting?** The second does not exist today — the only
    per-collection record is the spec, and everything in `chunk_params` is identity by
    construction; a non-identity per-collection field would be a new registry column with its
    own manifest and conformance surface. Recommendation: the **marker**, for the §9.5 reasons
    (inference is a corpus property, not a deployment one; it must be recorded for the §10/§11
    splits; the arms that need it are new collections anyway), accepting that it forks a
    collection for what is, on its own, a metadata-only step. Consequence to accept with it: on
    path C a collection carrying only `structure_infer` is refused like any marked collection
    (§7), so in this series a text-only corpus can be inferred only through paths A, B or D;
    option (a) — registry resolution in `embed_shard.py` — lifts that later.
