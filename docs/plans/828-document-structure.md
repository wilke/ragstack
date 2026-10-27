# Document structure: one model, per-format adapters, section-aware chunking and chunk metadata (#828)

**Status:** `PROPOSED`, design note, 2026-10-27. No code. Written for the owner's review
before the first PR of the series in §12. It folds in the owner's direction from the #828
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
  adapter runs, on both paths, because the adapter runs inside the loader.

**Invariants, tested in PR-1 and asserted on every load (§8):**

1. `0 <= start < end <= len(content)` for every section and block.
2. Sections nest: for any two sections, either one contains the other or they are disjoint; a
   child's `level` is its parent's `level + 1`.
3. The level-1 sections partition `[0, len(content))` in order, with no gaps. Text before the
   first heading is a level-1 section with `title=None` (untitled sections are first-class,
   `section-taxonomy.md` rule 7). `structure` absent means *unknown*; it never means one big
   section.
4. Blocks do not overlap each other and are sorted by `start`. They need not cover the text.
5. A `heading` block's text equals its section's `title` (whitespace-normalised).
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

Why not inside `metadata`: `jats.py` already states the rule — spans "are large and are not a
filterable field, so they must never reach `metadata` or the JSONL shard" — and `metadata` is
what the passthrough allow-list, `index_metadata` and the ES keyword mapping consume. A
top-level key is outside all three, so structure is immune to the metadata drift #833 item 4
describes and never lands in a store as a payload. `pdf_extract.py` writes the key when the
document has structure; `JsonlLoader._document` reads it, **validates the invariants of §2
against `text` before accepting it**, and on any violation drops the structure (not the
document) and counts it in a new `DropReport.structure_invalid` counter the shard prints, so a
corrupt or stale structure is loud and the document still ingests as unstructured. `--spans-out`
on `jats_extract.py` stays as it is; the record key is the carrier from now on.

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
- The classifier fills `role` only where the adapter left it `None`, from `title` alone, and
  writes `role_source="rules/1"`. A declared role (JATS `sec-type`) is never overwritten.
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
`ragstack/ingestion/structure.py` and runs in `IngestionPipeline.prepare_documents` between the
chunk loop and `_filter_boilerplate`, on every path that uses the pipeline (A, B, C; path D is
handled in §8). It writes only when `doc.structure is not None`; a chunk of an unstructured
document gets none of these keys. Absent is a state (`chunk_metadata.json`
`x-ragstack-absence`): it means *not processed*, never *no section*.

| field | type | rule |
|---|---|---|
| `section_title` | string | the **owning section's** title. Owning = among the deepest sections overlapping `[start_char, end_char)`, the one with the largest overlap in characters; ties → the earliest. Existing field; today only JATS unit records fill it (with the unit suffix). Omitted when the owning section is untitled. |
| `section_path` | list of strings | the owning section's `path`. Omitted when empty. |
| `section_level` | integer | the owning section's `level`. |
| `section_role` | string (`roles/1`) | the owning section's role, or the nearest titled ancestor's when the owning section has none; `unknown` otherwise. |
| `section_role_version` | string | the `role_source` of the section whose role was used. |
| `section_crossing` | boolean, **stamped only when `true`** | the chunk overlaps two or more leaf sections (the `is_boilerplate` precedent: `false` is never written, so unstamped chunks stay "unknown"). Open question Q10. |
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
derived from the table, and `ensure_index` pushes the derived mapping to an existing index with
`put_mapping`; a brand-new field conflicts with nothing (the `symbol_glossary` precedent), so an
existing index accepts the new fields without reindexing. `section_level` is **not** added to
`KNOWN_INT_FIELDS` in this series (that widens the filter-side 400 and needs its own contract
note, as the module says).

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
`--structure-metadata` flag on `ingest_shard.py` / `ingest_jsonl.py` and a `structure_metadata`
CWL input the API seeds from the setting — the `symbol_glossary` pattern (#783) exactly. Default
`off` until the accuracy check (§10) has run; then `on` for new ingests. What ran is recorded in
the shard receipt under `enrichment.structure = {"stamp": "stamp/1", "adapters": {...counts}}`
(ADR-0011 interim slot) and in the collection manifest.

---

## 7. Opt-in behaviours that *are* identity: three `chunk_params` markers

Three things change the chunks or the vectors and therefore fork a collection by construction.
Each is a `chunk_params` key under the #829 discipline: absent = today's behaviour exactly;
present = applied; an unknown value is refused (400 at create, exit 2 on the bulk tools); the API
records defaults explicitly so a later default change cannot re-chunk; **one reader**,
`structure_from_params(method, params)` in `chunker_config.py`, serves the API's `_chunk_param_kwargs`,
`ingest_shard._build_chunker` and `ingest_jsonl`; a contradicting CLI flag is refused against a
registry entry. Because `chunk_params` is inside `chunk_descriptor()` → `spec_hash` → the physical
store name, a marked collection is a new collection with no schema change (`chunk_params` is
free-form in the contract).

| key | values | what it changes | why it is identity |
|---|---|---|---|
| `text_render` | absent (raw) \| `"markdown-plain/1"` | the Markdown loader emits the stripped plain text of #828 §2 as `content` (headings on their own line without `#`; links as `text (url)`; images as alt plus target; inline HTML as visible text plus `href`; code verbatim; math verbatim; tables one row per line, cells joined by ` \| `; emphasis markers dropped). Applies to `.md` only; other suffixes are untouched. | content changes, so every offset, chunk and id changes |
| `structure_cuts` | absent \| `"sections/1"` | section boundaries become **preferred** cut points for every method (§7.1); the #831 merge never crosses one | chunk boundaries change |
| `embed_heading_path` | absent \| `"prefix/1"` | the embedded text is `" › ".join(section_path) + "\n" + content`; the stored `content`, the offsets and the ids are unchanged | vectors change; the collection's vectors are no longer a function of `content` alone |

They are three keys, not one, because §11 evaluates each as its own arm; one key would couple
the arms and force a re-ingest to change any of them.

Which methods accept them: all six. `structure_cuts` on `fixed` (characters) is allowed but
pointless and logged; `embed_heading_path` on any method is a vector change only.

**Release order (#829's trap, restated).** An older tools image ignores all three keys and
chunks a marked collection unmarked, with no error. The receipt's `chunking` object gains
`structure_cuts`, `embed_heading_path` and the loader's `text_render`; a receipt without them came
from an older image. No runtime guard, as the owner decided for #829; the image and workflows roll
first (ADR-0010).

### 7.1 Section-preferred cuts and the #831 wall

The chunkers keep their algorithms; `structure_cuts` adds two rules at the seams they already
have:

- **Preferred cut.** Before packing (`sentence`, `words`, `semantic*`) or sliding (`fixed_token`),
  the document is split into *segments* at the boundaries of its **leaf** sections. Each segment
  is chunked on its own with the collection's method and size. A chunk therefore never starts in
  one leaf section and ends in another. Offsets stay document-global (segments are offset ranges,
  not substrings), so ids are still `uuid5(doc_id:start:end)` and the chunks still tile the
  document.
- **Merge up to the cap.** Hard section walls produced a median chunk of 108 tokens in the
  exploratory run (#828 §4); that is the small-piece problem #831 addresses. Under
  `structure_cuts`, the #831 merge runs with one extra rule: a piece may merge with its neighbour
  only when both lie in the same leaf section **or** both are whole leaf sections under the same
  parent (sibling merge; the result is then a chunk whose owning section is the parent and whose
  `section_crossing` is `true`). It never merges a fragment of one section with a fragment of
  another. Without #831's `size_tolerance` the merge target is `chunk_size` itself.
- **Headings travel with their body.** A `heading` block is never a segment's whole content when
  the next block fits with it; the segmenter attaches a heading to the segment that follows it.
- **`fixed_token` with overlap.** Overlap does not reach back across a segment boundary. The first
  window of a segment starts at the segment start.

What this does *not* do: it does not change `sentence_spans` or the semantic breakpoints inside a
segment, so `cap_split`, `breakpoint_cap` and `size_cap` compose unchanged; the segmenter runs
before them. The chunking study's labels (`section-taxonomy.md` § sentence coordinates) are a
different coordinate system regardless, as every chunking change is.

---

## 8. Carrying structure through every ingest path

[`ingest-paths.md`](../ingest-paths.md) names four paths and one pipeline. Structure must arrive
at `prepare_documents` identically on each.

| path | where the adapter runs | how structure travels | what proves it |
|---|---|---|---|
| **A. API, in-process** | inside the loader `default_loader_registry()` dispatches to (`build_ingestor_for`) | in memory, on `Document.structure` | the invariants (§2) on every loaded document |
| **B. API → GoWe** | `pdf_extract.py`, through the same registry (#830) | `"structure"` key in the shard JSONL → `JsonlLoader` validates and restores it → `ingest_shard.py` → the same pipeline | **parity test:** `test_extract_text_parity.py` grows a structure half — for each fixture, the structure after the JSONL round trip equals the in-process structure field for field, and the stamped chunk metadata of the two paths is identical |
| **C. operator CWL** (`jats-ingest.cwl`, `ingest-bulk.cwl`, `pdf-ingest.cwl`) | `jats_extract.py` (JATS adapter, §9.2) or `pdf_extract.py` | the same JSONL key → `embed_shard.py` / `ingest_shard.py` → the pipeline | the same parity test, driven through `embed_shard.py` |
| **D. legacy `ingest_jsonl.py`** | none (it consumes JSONL) | `JsonlLoader` restores it; the tool's own chunk loop must call `stamp()` and honour `structure_from_params` | #831's rule: the API, `ingest_shard` and `ingest_jsonl` are checked for parity in one test |

**#833 and this design.** Three of its four findings touch structure directly.

- *Item 2, JATS extracted three ways.* Today JATS `.xml` reaches a store by `jats_extract.py`
  (the parser), by the GoWe extract step (since #830: skipped, `no loader for .xml`), or
  in-process by reference, where an unknown suffix **falls back to `TextFileLoader` and ingests
  the raw XML as text**. The structure model needs **one JATS adapter** (§9.2), so: register
  `.xml` → a `JatsLoader` that wraps `jats.article_records(..., with_spans=True)` in
  `default_loader_registry()`, and make the in-process fallback for an unknown suffix a refusal
  with the same `no loader for <suffix>` error the other two paths record (item 1). Both are
  parity fixes and belong in their own PR with a parity test before the JATS adapter PR (open
  question Q7).
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
called by the loader for its suffix.

### 9.1 Markdown — `markdown/1`, `parsed`

Parser: a CommonMark implementation (`markdown-it-py`, with `mdit_py_plugins` `front_matter`),
under a new `[markdown]` extra beside `[pdf]` and `[chunking]`. Without the extra the `.md`
loader produces no structure and logs once, like the nltk fallback (#815); it never guesses with
a regex, because `#` inside a fenced block is code and a regex cannot know that.

One parse, two outputs:

- **Raw mode** (`text_render` absent). `content` is the file text as `TextFileLoader` reads it
  today, byte for byte. Block tokens carry line ranges (`token.map`); a line-offset table turns
  them into character offsets into that same text. ATX and setext headings open sections; a
  section closes at the next heading of equal or higher rank or at EOF; front matter is a
  `front_matter` block; fenced and indented code are `code`; pipe tables `table`; lists `list`;
  blockquotes `quote`; a paragraph consisting only of an image is `figure`, and if its alt or the
  following paragraph starts with a caption label it is also a `figure_caption`; `$$` blocks are
  `math`; everything else is `prose`.
- **Stripped mode** (`text_render="markdown-plain/1"`). The same token stream is rendered through
  the emitter of §2 with the #828 §2 rules; sections and blocks are recorded at emitter positions.
  A test asserts every content word of the source survives (link targets, code, table cells,
  math), and that `content[s.start:s.end]` for a `heading` block equals the heading text.

Detects: headings and their nesting, the block kinds above, image captions by label. Cannot
detect: tables drawn in HTML (`<table>` is inline HTML and stays `prose`), semantic roles beyond
the heading text (that is the classifier's job), and anything about a document with no headings
(it gets one untitled level-1 section, which is still a valid, honest structure).

### 9.2 JATS and open-access XML — `jats/1`, `parsed`

Source: the existing `Span` list from `article_spans` / `article_records(with_spans=True)`. The
mapping is mechanical:

- `kind="sec"` → `Section(level=depth+1, title=span.title, locator=span.path)`; `role` from
  `sec-type` via the `declared/1` map (the adapter must start reading `sec-type`, which nothing
  does today); `kind="abstract"` → a level-1 section titled `Abstract` with `role="abstract"`,
  `role_source="declared/1"`; body-lead paragraphs before the first `<sec>` → the untitled
  level-1 section.
- `kind="title"` → `heading` block; `p` → `prose`; `list` → `list`; `disp-quote` → `quote`;
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
recorded. Two labellers on a 20% overlap subset; Cohen's κ on boundaries, roles and caption
labels is reported before the metrics.

**Labels per document:** the ordered list of section starts (offset), titles, levels and roles
(from `roles/1`); every caption's `[start, end)` and label; every table body's `[start, end)`;
for each caption citation in the body, the caption it refers to. For PDFs additionally a
per-document checklist of the failure modes: two-column order broken, running header/footer in
`content`, caption splitting a paragraph, reference list present as body.

**Metrics:**

- Section boundaries: precision and recall of inferred section starts against labelled starts,
  at exact offset and at a one-line tolerance; reported per adapter, and for `pdf-layout/1` also
  per failure-mode checklist state.
- Roles: accuracy given a matched boundary; per-class precision and recall over `roles/1`;
  the confusion matrix; split by `role_source` (`declared/1` vs `rules/1`) and by
  `structure_source`.
- Captions and tables: precision and recall of `figure_caption`, `table_caption` and `table`
  blocks by span overlap ≥ 0.5; label accuracy on matched captions.
- Citation resolution: the share of labelled `refers_to` citations whose resolved label matches
  the labelled caption; the wrong-caption rate separately from the unresolved rate.
- Agreement between `section_role in {references, acknowledgements}` and the existing boilerplate
  verdict `section`, as a descriptive table (open question Q9).

**Gates** (pre-registered; the numbers are the proposal): a `section_role` filter is exposed only
for `structure_source=parsed` until inferred role precision ≥ 0.85 and boundary recall ≥ 0.80 at
one-line tolerance on the PDF stratum; `text-heuristic/1` roles stay `unknown`-only unless its
precision clears the same bar. §11's results are reported split by adapter in any case, so a
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
| **1 — model and stamp** | `Section`/`Block`/`DocumentStructure`/`Document.structure`; `structure.py` with `validate_structure` (§2 invariants) and `stamp`; the `rules/1` table and `classify_roles`; schema: `chunk_metadata.json`, `metadata_schema.DECLARED_FIELDS`, ES mapping derived; `pdf_extract.py` writes the key, `JsonlLoader` reads/validates it; `prepare_documents` stamps when `STRUCTURE_METADATA=on`; receipt `enrichment.structure`. No adapter yet: structure only arrives via JSONL or a test. | invariant property tests (nesting, partition, half-open); stamp is pure and total (every chunk of a structured doc gets `section_level`, `structure_source`, `structure_version`); owner-selection ties; `caption_labels`/`refers_to` normalisation table; schema pin test; `Document.model_dump()` byte-identical when `structure is None`; `JsonlLoader` drops an invalid structure with a counted reason and keeps the document; mutation: remove the validation and the test fails. |
| **2 — Markdown raw mode + GoWe carry** | `markdown/1` raw mode in the `.md` loader under the `[markdown]` extra; parity test growth. | adversarial fixture (the one in `test_extract_text_parity.py`: front matter, CRLF, BOM, fences containing `#`, setext headings, tables, images); structure after JSONL round trip equals in-process; stamped metadata identical on both paths; without the extra: no structure, one log line, content unchanged. |
| **3 — `text_render` and the one reader** | `structure_from_params`; `text_render="markdown-plain/1"` stripped mode; API create validation and explicit recording; `ingest_shard`/`ingest_jsonl` read through the reader and refuse contradicting flags; `CHUNKING.md` section. | every-word-survives test (links, code, cells, math); heading block text equals title; absent marker → `content` byte-identical to `TextFileLoader`; unknown value → 400 / exit 2; `chunk_descriptor` changes with the marker (identity proven). |
| **4a — JATS parity (#833 items 1–2)** | `.xml` → `JatsLoader` in the registry; unknown-suffix fallback becomes a refusal with the shared error. | three-path parity test for a JATS fixture (in-process, `pdf_extract.py`, `jats_extract.py`): same `content`, same doc id, same metadata subset; unknown suffix refused identically on A and B. |
| **4b — JATS adapter** | `jats/1` from `Span` + `sec-type` → `declared/1`; unit-record structures. | fixture with untitled `<sec>`, nested `<sec>`, `sec-type` present and absent, floats in `<floats-group>`; untitled section is a first-class section; `section_title` on article chunks is the heading, not the unit suffix; declared role never overwritten by rules. |
| **5 — accuracy-check harness** | `scripts/eval/structure_accuracy.py`: label format, κ, the §10 metrics, the public export with the part-4 allow-list discipline; the labelling run itself is an owner-scheduled activity, its record a results directory. | metrics on a synthetic labelled fixture; the exporter refuses any string that is not a short safe token. |
| **6 — `structure_cuts` and `embed_heading_path`** | segmenter in `chunkers.py` (§7.1); the #831 wall; heading attached to the following segment; `embed_heading_path` applied in `_embed_and_link` to the embed input only; receipt `chunking` keys. | chunks tile the document under every method; no chunk spans two leaf sections; sibling merge sets `section_crossing`; `fixed_token` overlap does not cross a segment; stored `content` unchanged under `embed_heading_path` (mutation: prefix leaks into `content` → fails); absent markers reproduce today's spans against the #829 baseline fixture. |
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
  is expected for PRs 1–7.
- **The chunking study's pins** (`EXPECT_COMMIT`, `sentence_spans_fingerprint`) are unaffected:
  `sentence_spans` does not change; `structure_cuts` runs before it on offset ranges.

---

## 14. Red team: how this design fails, and what catches each failure

| failure mode | how it would show | what catches it |
|---|---|---|
| **Offsets drift** — a transform runs on `content` after structure exists (a `.strip()`, a newline normalisation, a BOM removal) | `content[start:end]` is off by a few characters; `section_title` lands on the wrong section near the edges; `heading` blocks do not equal titles | §2 invariant 5 asserted on every load (`JsonlLoader`, in-process); the parity test compares structures field for field after the JSONL hop; the adapter runs *inside* the loader, after every normalisation the loader does |
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
| **Stripped text changes token counts** | chunk sizes and counts differ from the raw collection | by design: `text_render` is identity; the two are different collections |
| **`refers_to` false positives** — "Table 2 of [12]" cites another paper's table; "Fig. 1A–C" variants | wrong caption linked; the attached-placement arm attaches the wrong text | normalisation table with tests; §10's wrong-caption rate is a first-class metric; `refers_to` is metadata, never moves text until an arm wins |
| **Duplicate labels** — two "Figure 1" (main and supplementary) | `caption_labels` ambiguous | labels keep their prefix (`Figure S1` ≠ `Figure 1`); duplicates within a document are counted in §10 |
| **Heading path leaks into stored content** | `content` no longer equals `doc.content[start:end]`; ids still match, text does not | PR-6 test: stored `content` byte-identical under `embed_heading_path`; the prefix is applied to the embed input list only |
| **Structure bloats the JSONL** — a block per line on a pathological PDF | shard files grow, `JsonlLoader` slows | size ceiling with a counted drop; blocks need not cover the text, so an adapter may emit fewer |
| **Filter grammar surprises** — `section_path` is a list | a filter on it matches any element | the same as `authors` today; documented in the schema description |
| **Two sources for an identity value** (a CLI flag beside the entry's `chunk_params`) | #609 again | the one reader; flags refused when they contradict the entry; no CWL input for the markers |

---

## 15. Open questions for the owner, each with a recommendation

1. **Store the whole structure per document (for display and re-stamping without re-parsing)?**
   Recommendation: not in Qdrant or Elasticsearch. Keep the JSONL `structure` key as the carrier,
   keep `--spans-out`, and defer an archive role (`structure.jsonl.gz`) until the backfill needs
   it; Markdown and JATS re-parse in milliseconds, and PDF re-extraction is what the accuracy
   check wants anyway.
2. **One `chunk_params` key or three?** Recommendation: three (`text_render`, `structure_cuts`,
   `embed_heading_path`). Each is an evaluation arm in §11 and each is identity on its own; one
   key would force a new collection to change any of them.
3. **Is metadata stamping identity?** Recommendation: no — a deployment setting with a per-chunk
   `structure_version`, default off until §10 has run, then on, with a backfill; the symbol
   glossary precedent. The cost is mixed coverage, made auditable rather than impossible.
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
   model, and the in-process raw-XML-as-text fallback is a live defect.
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
    Markdown-raw and JATS collections after §10, a non-production tenant first; no backfill for PDF
    collections until the inferred adapter clears its gate.
