# Document structure: one model, per-format adapters, section-aware chunking and chunk metadata (#828)

**Status:** `PROPOSED`, design note, 2026-10-27, revised the same day after an independent
review (its findings are folded in where they bit: §2 adapter placement and invariants 3 and 5,
§4 supersession and release order, §6 the `section_title` change on unit records and the
list-filter caveat, §7 the `embed_heading_path` forcing rule, §8 the shared helper, §9.1 the BOM
and parser details, §10 the statistics, §12 the #831 dependency and the contract change in
PR 5a), and revised again the same day to record the **owner's decisions** on the fourteen
questions (§15). The decisions that moved the design: roles are a **list of tags** inherited
down the section path (`section_roles`, Q4); **no backfill** of any collection (Q12); the
structure's eventual home is **Shock**, in a later series, and this series only guarantees a
serialisation that can go there unchanged, its code-point offsets converted to bytes by that
series (Q1, §4.1); and `embed_shard.py` gains **registry
resolution in this series** (PR 3), so the markers work on every ingest path (Q14, §7). No
code. Written for the owner's review before the first PR of the series in §12. It folds in the owner's direction from the #828
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
3. Every chunk can say which section it is in (title, path, level, roles), which kinds of blocks
   it holds, which figure and table captions it contains and which it cites. The owner's minimum
   is section type as metadata; this note delivers that first and section-aware cutting second.
4. The same file gives the same `content` and the same `structure` on every ingest path. This
   is the invariant #830 restored for `content`; structure must not re-break it.
5. Nothing changes for an existing collection unless someone asks for it, and asking for it is
   recorded where #829 records the semantic cap: in `chunk_params`, with the same marker
   discipline and one reader, which every ingest path reads (§7, §8).
6. Before any role or inferred boundary is used for filtering or cutting, its accuracy is
   measured on a hand-labelled sample, split by source.

**Non-goals**

- No new chunk method. Section-aware cutting is a modifier on the existing methods, selected by
  a `chunk_params` marker (resolves `structured-ingest.md` §8 Q3; see §7).
- No one-record-per-section. `structured-ingest.md` §1 records why that destroys document
  identity and neighbour links; this note keeps one record per document.
- No change to the existing `section` chunk field. It is the boilerplate verdict
  (`references` / `license` / `acknowledgements`) and keeps its meaning; the new field is
  `section_roles`, and the verdict does not consult it in this series (Q9).
- No backfill. No existing collection is re-stamped in place; an existing collection gets
  structure only by re-ingest, and the ASM and open-access corpora need a separately planned
  re-ingest outside this series (Q12).
- No store for the whole structure. In this series the structure travels in the JSONL and is
  consumed by the stamp; its eventual home is Shock, in a later series (§4.1, Q1).
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
| JATS (§9.2) | the XML | **at extract** (`jats_extract.py`, and the in-process `JatsLoader` of PR 5a) | the XML is not available after the record is written; the structure rides the JSONL (§4) |
| PDF (§9.4) | layout from the PDF bytes | **at extract** (`pdf_extract.py` → `PdfLoader`) | same: the layout is gone once the text is written; the structure rides the JSONL |
| extracted text (§9.5) | the text itself | **at ingest**, like Markdown, when the collection opts in | there is nothing else to read it from |

So the GoWe extract step gains no workflow input and no spec lookup (consistent with #829's
rule that `chunk_params` are read from the registry entry, never from a CWL input; the
registry *id* and *name* that PR 3 wires into the operator workflows' embed steps are not
`chunk_params` — they say which entry to read, §7), the
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
7. `structure.content_sha256` equals the sha256 of the UTF-8 encoding of `content`. A structure
   paired with any other text — an edited record, a stale side file, later a Shock node beside
   the wrong text node (§4.1) — fails here before any offset is read.

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
    roles: list[str] | None       # this section's OWN tags from roles/1 (§5), sorted, distinct,
                                  # never empty; None = not classified
    role_source: str | None       # "declared/1" | "rules/1" | "llm/1:<model>:<prompt-sha>";
                                  # one producer per section, for all its tags; None iff roles is None
    rank: int | None = None       # the raw heading rank as the source wrote it (Markdown ATX 1-6,
                                  # setext 1-2); None where the format has no rank (JATS, PDF, text).
                                  # Debugging only: never stamped, never used by the stamp or the cutter
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
    schema_version: str = "ragstack.structure/1"   # the serialisation's own version (§4.1)
    content_sha256: str           # sha256 of Document.content (UTF-8): binds the offsets to one text
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

- **`level` is tree depth, not the raw heading rank** (decided, Q11). The two agree only for
  well-formed Markdown — a document that starts at `#` and never skips a rank. They part on a
  skipped level (`#` then `###`: rank 3, depth 2), on a document whose first heading is `##`
  (rank 2, depth 1), and on every format without a rank: JATS nests `<sec>` elements and states
  no rank, a PDF's rank is inferred from font size. Tree depth is the only thing every adapter
  can state honestly. The raw Markdown rank is kept in the structure record as `Section.rank`,
  for debugging; it is not stamped on chunks.
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
- **`roles` is a list of tags** (decided, Q4). A section can be two things at once — *Results
  and Discussion*, JATS `materials|methods` — and a list says so without a tie-break rule. The
  list holds the section's *own* tags, what its title (or its `sec-type`) says; the tags a chunk
  inherits from the sections above it are added by the stamp (§6), never written back here, so
  the record keeps the distinction between own and inherited roles.
- **`role_source` is versioned even when declared** (decided, Q5: `declared/1`), because the map
  from JATS `sec-type` values to the role vocabulary is itself a rule table that can change. It
  is per section: all of a section's tags come from one producer.
- **`schema_version` and `content_sha256`** make the serialisation self-describing outside the
  JSONL record it rides in today, which is what a later Shock series needs (§4.1).
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
{"text": "...", "path": "...", "metadata": {...}, "structure": {"schema_version": "ragstack.structure/1", "content_sha256": "...", "sections": [...], "blocks": [...], "structure_source": "parsed", "adapter": "markdown/1"}}
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
`jats.py` docstring; PR 5b rewrites `jats_extract.py`'s and makes `--spans-out` a debugging
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

**Nothing stores the structure in this series** (decided, Q1). It travels in the JSONL record,
is validated, is consumed by the stamp, and is gone; Qdrant and Elasticsearch hold only the
stamped fields of §6, and the archive's roles are unchanged. Re-stamping a collection therefore
means re-ingesting it, which is also the only way an existing collection gets structure (Q12).

### 4.1 What this series guarantees for the Shock series that follows it

The owner's direction for the structure's eventual home is **Shock** — the BV-BRC object store
itself, not the Workspace that sits on top of it — with the document text stored once as a
node and chunks addressed by read offsets into it: the plan of
[`metadata-and-kg.md`](metadata-and-kg.md) §2b (a `text_node_id` on the document record, each
chunk a `(text_node_id, start_char, end_char)` pointer, proven on one dev collection before
anything stops writing `content`). The structure belongs beside that text node, in the same
coordinate system. Optionally, if graph queries over sections are wanted, the graph gains
section nodes (`Document -HAS_SECTION-> Section`, carrying the section's roles); chunks never go
in the graph. That is a **separate, later series**. Today there is no Shock or S3 backend at
all: `_refuse_unknown_backend` accepts only `local` and `gowe`, and adding one is an
implementation task ([`docs/runbooks/tenant-admin.md`](../runbooks/tenant-admin.md) §6a).

What this series guarantees, so that series converts as little as possible:

1. **The serialisation is written unchanged; its offsets are not yet byte offsets.** The JSONL
   `structure` value is `DocumentStructure.model_dump()` with its own `schema_version`, and the
   serialisation can be written to a Shock node as it stands; nothing in it refers to the JSONL
   record around it. Its offsets, like `start_char`/`end_char`, are code points, so the Shock
   series will need a code-point→byte conversion (item 2). This series does not record byte
   offsets: they are derivable from `content`, and `content_sha256` verifies that the text they
   are derived from is the right one (owner-visible item in §15).
2. **The offsets are the chunks' offsets.** Sections and blocks index the same `content` that
   `start_char`/`end_char` index, so the text node of `metadata-and-kg.md` §2b serves both
   without a second coordinate system. Offsets are Unicode code-point indices (Python `str`
   semantics), exactly as `start_char`/`end_char` are today; a byte-addressed reader (Shock's
   `size` and `subset` indices are byte ranges) converts both the same way, and that conversion
   belongs to the Shock series, once, for chunks and structure alike.
3. **The binding is checkable.** `content_sha256` (§2 invariant 7) lets a reader refuse a
   structure node paired with the wrong text node.
4. **The section record carries what a graph would need**: own `roles`, `role_source`, `path`,
   `level` and `locator`, so `HAS_SECTION` edges could be built from the stored structure
   without re-parsing anything.

---

## 5. Roles: one vocabulary of tags, one rule table, versioned

**`roles/1`** is a single flat vocabulary with no document-class switch (decided, Q4). The
classifier does not need to know whether a document is a paper or a manual: the synonym tables
of the two classes barely collide, and where they do the rule is explicit. A section carries a
**list** of these tags, not one value.

| class | role | heading cues (`rules/1`, case-insensitive, after stripping numbering and punctuation) |
|---|---|---|
| paper | `abstract` | abstract, summary (when level 1 and first) |
| paper | `introduction` | introduction, background |
| paper | `materials` | materials, materials and methods (with `methods`) |
| paper | `methods` | methods, materials and methods (with `materials`), experimental procedures, study design, patients and methods |
| paper | `results` | results, findings, results and discussion (with `discussion`) |
| paper | `discussion` | discussion, results and discussion (with `results`) |
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
  inferred structure ("never a guess"). An untitled parsed section is also `unknown`. Each is
  a section's *only* tag when it applies: a section is never `[methods, other]` on its own.
- **Combined headings get every tag they name.** A title of the form *A and B* (or *A & B*,
  *A/B*) whose parts each match a cue gets the tags of both parts: *Results and Discussion* →
  `[discussion, results]`, *Materials and Methods* → `[materials, methods]`. A title whose
  parts do not all match is matched whole (*Patients and Methods* → `[methods]` by its own cue).
  This replaces the first draft's tie-break (*results and discussion* → `results` after a
  `methods`, else `discussion`), which a list makes unnecessary.
- The one collision — `references` (bibliography) against `parameters` (API reference) — is
  decided by the plural: *References*, *Bibliography* → `references`; *Reference*, *API
  reference*, *Command reference* → `parameters`.
- `introduction` appears in both classes; when the docs sibling test fires (an `installation`
  or `usage` sibling) the section gets `[introduction, overview]`, so a filter on either finds it.
- The classifier fills `roles` only where the adapter left it `None`, and writes
  `role_source="rules/1"`. Declared roles (JATS `sec-type`) are never overwritten or added to.
  **It takes the whole section list, not one title**: some rules need context — *summary* is
  `abstract` when first and `conclusions` when last, *introduction* gains `overview` on an
  `installation`/`usage` sibling. `classify_roles(sections) -> None` is one pass over the tree,
  deterministic for a given list.
- **Roles are inherited downward, for stamping only** (decided with Q4). A chunk carries every
  role along its owning section's path: the stamp's `section_roles` (§6) is the union of the
  own tags of the owning section and all its ancestors, so a filter on `methods` finds chunks in
  the subsections of *Methods* whatever their own titles say. The structure records what each
  section's title says (own tags); the stamp records what a reader would answer (the union).
  Inherited and own are therefore distinguished in the structure record, not on the chunk: the
  stamp writes only the union, and the own tags stay with the structure (today in the JSONL
  record, later in Shock, §4.1).

**Versioning.** Any change to the table, the normalisation or the collision rule bumps
`rules/N`. Any change to the vocabulary bumps both `roles/N` and `rules/N`, since a rule table
targets one vocabulary. The chunk carries `section_roles_version`: the sorted distinct
`role_source` values (`declared/1`, `rules/1`, `llm/1:…`) of the sections that contributed tags
to its `section_roles`, under the `symbol_glossary_version` precedent. It says which producers
wrote the value, so an evaluation can split by producer and a collection holding chunks of two
rule versions is auditable.

**JATS `sec-type` → `roles/1` (`declared/1`):** a `sec-type` is split on `|` and each part is
mapped on its own; the section's tags are the distinct results, sorted. `intro` → `introduction`;
`materials` → `materials`; `methods` → `methods`; `materials-and-methods` → `materials`,
`methods`; `results` → `results`; `discussion` → `discussion`; `conclusions` → `conclusions`;
`supplementary-material` → `supplementary`; `cases`, `subjects` → `other`. So
`materials|methods` → `[materials, methods]` and `results|discussion` → `[discussion, results]`.
A part not in the map → `other`, with `role_source="declared/1"` (a declared type that is not in
the map is still a declaration); `other` is dropped from a list that has any mapped tag. A
`<sec>` without `sec-type` gets `roles=None` from the adapter and goes to the rules.

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
| `section_title` | string | the **owning section's** title. Owning = among the deepest sections overlapping `[start_char, end_char)`, the one with the largest overlap in characters; ties → the earliest. Omitted when the owning section is untitled. **Existing field, and the stamp changes its value on one producer:** today only JATS unit records fill it, with the unit *suffix* (`table-2`, `figure-1`, set in `article_records` and carried to chunks by the passthrough allow-list on path C). Under the stamp a unit chunk's `section_title` becomes the caption's label text (`Table 2`), which is what the schema's description ("the source document's own heading") always meant; the suffix survives in `source`/`path` (`PMC…#table-2`). Collections ingested earlier keep the suffix, and `structure_version` tells the two apart. Decided, Q13. |
| `section_path` | list of strings | the owning section's `path`. Omitted when empty. |
| `section_level` | integer | the owning section's `level` (tree depth, Q11; the raw rank is not stamped). |
| `section_roles` | list of strings (`roles/1`) | the sorted union of the own `roles` of the owning section and every ancestor (§5, Q4), so a chunk in a subsection of *Methods* carries `methods`. `unknown` is dropped from the union whenever any other tag is in it, so `["unknown"]` means nothing on the path could be classified; `other` is kept (a titled section on the path matched no rule). Never empty for a structured document, because `classify_roles` runs on every structure before the stamp and the validator then asserts `roles is not None` on every section (without that, the stamp would have to map `None` to `unknown`, and an unclassified structure would pass silently as "nothing could be said"). |
| `section_roles_version` | list of strings | the sorted distinct `role_source` values of the sections that contributed a tag to `section_roles`. The first list-valued `*_version` field (`symbol_glossary_version` is a string), so a filter on it means "some contributing producer". |
| `section_crossing` | boolean, **stamped only when `true`** | the chunk overlaps two or more *segments* (§7.1: a leaf section, or a parent's own text outside its children), i.e. its text is owned by more than one section (the `is_boilerplate` precedent: `false` is never written, so unstamped chunks stay "unknown"). Expected on the first chunk of most titled parents, because a heading-only parent segment travels with the following segment (§7.1); not a defect signal. Decided, Q10. |
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

**List-valued fields and the in-memory predicate.** `section_path`, `section_roles`,
`section_roles_version`, `block_types`, `caption_labels` and `refers_to` are lists. Server-side,
Qdrant and Elasticsearch match a scalar filter against any element of a list (the `authors`
precedent), which is exactly the semantics a tag filter wants: `{"section_roles": "methods"}`
matches a chunk tagged `[materials, methods]`. The shared in-memory predicate
`stores/filters.py::payload_matches` does not: it compares `actual != value`, so a list-valued
payload never matches a scalar filter there — and `QdrantVectorStore.get_chunks` re-checks every
retrieved record through that predicate, as does `InMemoryVectorStore`. A filter on a list field
therefore already disagrees between the retrieval legs and the context-expansion path for
`authors`, and would for these six. Scalar fields (`section_title`, `section_level`,
`structure_source`) are unaffected. Because roles are now a list (Q4), the role filter — the
owner's first use of this metadata — is itself a list filter, so fixing `payload_matches` to
treat a list `actual` as "any element equals" (a small change with its own test and conformance
case) is a **prerequisite for offering any role filter**, listed in PR 1, and not a reason to
store these as anything but lists.

**Identity: stamping is not in `spec_hash`.** `spec_hash` is `sha1(model|dim|chunk_descriptor)`
and `chunk_descriptor` is `method/size/overlap/{chunk_params}`. The stamp changes neither content
nor offsets nor ids; it adds payload fields, exactly as the symbol glossary and the boilerplate
verdict do. So **turning stamping on does not fork a collection and does not change which
collection an ingest resolves to** (decided, Q3). Two consequences, accepted with it:

1. **Mixed coverage arises only from a re-ingest into an existing collection.** There is no
   backfill (Q12), so a collection ingested before the switch stays unstamped until someone
   re-ingests into it. If someone does so with stamping on, each re-ingested document's prior
   chunks are replaced by stamped ones (delete-prior, as for any re-ingest), every document not
   re-ingested keeps its unstamped chunks, and the collection is then partially covered: a
   `section_roles` filter on it under-returns with no error. The audit is `structure_version` on
   every stamped chunk and a conformance-report row listing collections with partial coverage;
   the rule is that a retrieval filter on any structure field is not offered in the UI for a
   partially covered collection. The **recommendation is to re-ingest into a new collection**,
   which is covered completely from its first document, and to retire the old one.
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
`ingest_shard._build_chunker`, `embed_shard._build_chunker` (once PR 3 gives it the registry
entry) and `ingest_jsonl`; a contradicting CLI flag is refused against a registry entry. The
three keys are decided (Q2). Because `chunk_params` is inside `chunk_descriptor()` → `spec_hash` → the physical
store name, a marked collection is a new collection with no schema change (`chunk_params` is
free-form in the contract).

| key | values | what it changes | why it is identity |
|---|---|---|---|
| `text_render` | absent (raw) \| `"markdown-plain/1"` | the Markdown adapter, at ingest (§2), replaces `content` with the stripped plain text of #828 §2 (headings on their own line without `#`; links as `text (url)`; images as alt plus target; inline HTML as visible text plus `href`; code verbatim; math verbatim; tables one row per line, cells joined by ` \| `; emphasis markers dropped). Applies to `.md` only; other suffixes are untouched. | content changes, so every offset, chunk and id changes |
| `structure_cuts` | absent \| `"sections/1"` | section boundaries become **preferred** cut points for every method (§7.1); the #831 merge never crosses one | chunk boundaries change |
| `embed_heading_path` | absent \| `"prefix/1"` | the embedded text is `" › ".join(section_path) + "\n" + content`; the stored `content`, the offsets and the ids are unchanged. Applied in the **embed input list only**, at the places a chunk's text is handed to an embedder: `IngestionPipeline._embed_and_link` (shared by `embed_source` and `iter_embed_source`: paths A, B and C, path C's `embed_shard.py` reading the marker from the registry entry through PR 3's resolution and PR 4's reader — see below) and `ingest_jsonl.py`'s `_embed_drop_bad` (path D). It needs `section_path` on the chunk, so it forces structure and stamping (§6). | vectors change; the collection's vectors are no longer a function of `content` alone |

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

**Path C reads the markers through the registry, like every other path** (decided, Q14). The
owner's principle is that GoWe/CWL is the main ingest path and that the same code runs on every
path, so the first draft's answer — refuse marked collections on path C and give
`embed_shard.py` registry access "later" — is replaced by giving it that access in this series,
as PR 3, before any marker exists.

*Today.* Path C has two chunk tools. `ingest-bulk.cwl` runs `ingest_shard.py`, which already
resolves its registry entry: `main()` calls `ingest_target.resolve_or_exit(args, model=…,
chunk_method=…, chunk_size=…, chunk_overlap=…)` before anything is embedded, and
`_build_chunker(args, spec)` then takes `size_unit` (`_size_unit`), the semantic tunables
(`_semantic_params`) and the cap (`_semantic_cap`) from `target.spec`, never from a flag. The
other three operator workflows — `jats-ingest.cwl`, `pdf-ingest.cwl`, `embed-bulk.cwl` — run
`embed_shard.py`, whose `_build_chunker(args)` builds from CLI arguments alone, including
`--chunk-size-unit`, and which resolves no entry. Those workflows hand the registry only to
their **load** step: `pdf-ingest.cwl` and `load-embeddings.cwl` bind the `collection_id` and
`registry` workflow inputs to `load_embeddings.py --collection-id/--registry` (the #263
resolution with #563's registry name: the registry *name* on the submission, its `COLLECTION_STORE_DSN_<NAME>` from the
worker's `--secret-file`, resolved by `ingest_target.registry_settings`); `jats-ingest.cwl`
binds `collection_id` and points the load step at a sqlite registry file through
`EnvVarRequirement` (`COLLECTION_STORE_BACKEND: sqlite`, `COLLECTION_STORE_PATH:
$(inputs.registry_db.path)`), which is why `ingest_target.add_arguments` documents it as the
workflow that binds no `--registry`; `embed-bulk.cwl` declares neither input.

*PR 3.* `embed_shard.py` gains `ingest_target.add_arguments(p)` and, when `--collection-id` is
given, calls `ingest_target.resolve_or_exit` with the same build-spec check `ingest_shard.py`
passes, so a `--chunk-method`/`--chunk-size`/`--chunk-overlap`/`--embedding-model` that
contradicts the entry exits 2 before any embed. It needs no store: resolution reads the registry
and computes store names and URLs but contacts neither store (`require_store_urls` is a separate
call this tool does not make), and `--create-via-api` is refused here, since an embed step must
not mint a collection. Its `_build_chunker` takes `size_unit` from the entry as
`ingest_shard._size_unit` does — method-conditional: only `sentence`/`words` read it, an entry
that records none means `chars` — and refuses a contradicting `--chunk-size-unit` by the rule
`ingest_jsonl._refuse_size_unit_mismatch` already implements (today the flag is a second source
for an identity value on this tool). The embedding-file header gains a `chunking` object — the resolved
`collection_id`, the method, size, overlap and size unit as applied, and from PR 4 the
structure markers as applied, each key absent when not applied. The workflows wire what they
already have into the embed step: `pdf-ingest.cwl` and `jats-ingest.cwl` bind their existing
`collection_id` (and `registry`, or `registry_db` through the same `EnvVarRequirement` as their
load step) into the embed step's tool; `embed-bulk.cwl` gains two **optional** workflow inputs,
`collection_id` and `registry`. From PR 4 on, the resolved spec goes through
`structure_from_params` exactly as it does in `ingest_shard.py`, so `embed_shard.py` builds its
chunker and markers from the collection's spec through the same reader as every other path.
Without `--collection-id` (a hand run on an unregistered scratch store) the tool behaves as
today and writes no `collection_id` in the header.

What PR 3 does **not** change: `embed_shard.py` still builds no breakpoint bridge, so the
semantic methods remain refused there (its local #609 refusal) and on its workflows' enum; a
semantic collection on path C still goes through `ingest_shard.py`. That is a method limit of
one tool, not a second reading of the spec.

**The option-(b) gates stay, re-purposed as backstops** for old images, old workflow texts and
hand-run submissions:

1. **`scripts/load_embeddings.py` — authoritative.** The load step inside `jats-ingest.cwl` and
   `pdf-ingest.cwl`, and `load-embeddings.cwl` run after `embed-bulk.cwl` (which itself ends at
   `merge_receipts` and has no load step), already resolves the target and runs `check_build`
   before writing. It now also refuses, before any write, when the registry entry's
   `chunk_params` carry a structure marker the header's `chunking` object does not show as
   applied, or the reverse, or when the header names a different `collection_id`, or when the
   header's applied chunking contradicts the entry — `size_unit` read the same method-conditional
   way as `ingest_shard._size_unit`. `IngestTarget.check_build` compares only method, size,
   overlap, model and dim, so the size-unit comparison closes a pre-existing hole on this path
   (an embed run under `--chunk-size-unit tokens` loads today into a `chars` entry). This is the
   ADR-0002 build-spec guard extended by one object, and it holds whatever produced the file: an
   old image (no `chunking` object, so a marked entry is refused and an unmarked one loads
   exactly as today), an old workflow text that does not pass the id to the embed step, a
   hand-run `cwltool` submission. Under `--vector-backend memory` the tool resolves no target
   (`target = None`), so the check is skipped with a logged line; the memory backend owns no
   physical store, so nothing durable can be forked by it.
2. **`gowe_batch_ingest.py` — early, so a refusal costs a command, not a scattered GPU run.** It
   already reads the registry read-only (`resolve_store_name`) and is given the workflow file
   (`--cwl`). It reads the entry's `chunk_params` and, for a marked collection, refuses before
   submitting unless the workflow text **wires the registry id into its `embed_shard.py`
   step** — a step whose tool's `baseCommand` names `embed_shard.py` and whose `in:` binds
   `collection_id`. That is a property of the text it is about to submit and can be read
   reliably; the image's age cannot be, from the driver (an unstamped tree names
   `ragstack-worker.sif`, which a worker's `--image-dir` can resolve to any build). It does not
   need to: an image older than PR 3 handed `--collection-id` fails every embed task at once
   with argparse's `unrecognized arguments` (exit 2) — loud and before any embedding — and on a
   stamped tree the stamp is the release's declaration that the named image supports the
   workflow (ADR-0010 decision 4). So the driver refuses exactly the silent case, an old
   workflow text; the load-time check covers everything else.
3. **The API's GoWe dispatch — dormant today.** `_refuse_unrunnable_chunk_method` refuses a
   collection whose method the workers cannot run, from a setting
   (`settings.ingest_worker_unsupported_methods`, through `shard_refusal`), not from the
   workflow text. The API does already parse the registered text, for its declared inputs
   (`tool_image.declared_workflow_inputs`, used when `_gowe_inputs` seeds the submission). The
   new gate adds a second reading of that text — the chunk step's `baseCommand` and its `in:`
   bindings — and sits beside `_refuse_unrunnable_chunk_method`: it refuses a *marked*
   collection when `GOWE_WORKFLOW_CWL` names a workflow whose chunk step is `embed_shard.py` and
   that step is not wired as in item 2. Today the API's workflow is
   `pdf-ingest-scatter.cwl` (`ingest_shard.py`), so the gate is dormant; it keeps a future
   workflow swap from re-opening the hole.

Consequence: once PR 3 and PR 4 ship, the markers work on paths A, B, C and D, including a
collection carrying only `structure_infer` (§9.5), so a text-only paper corpus can be inferred
through the operator workflows too. The backstops refuse only what they cannot prove was built
from the entry. Stamping (not identity) runs on path C through `iter_embed_source` when
`--structure-metadata` is on, or when a marker forces it.

**Same code, not the same process.** Path A (in-process) does not invoke the CWL tools: the API
calls the library functions the tools are thin wrappers over — the loader registry,
`attach_structure`, `IngestionPipeline`, `structure_from_params`, `stamp` — in its own process.
"The same code on every path" means every path reaches the same `ragstack.ingestion` functions
with the same inputs; what *proves* they give the same output is the parity tests (#833's
three-path test, grown by this series in §8), not the shared import.

**Release order, for the whole design** (#829's trap, restated and widened). ADR-0010 has two
release artifacts here, not three: the **tools image** at a tag T, and the **server release** at
a tag S, which stamps the image name into every CWL text and ships them all, the operator
workflows included (ADR-0010 decision 3, migration step 6(d)). A workflow is therefore not
released on its own; what can lag is the text an operator actually submits, because
`gowe_batch_ingest.py --cwl` and a hand `cwltool` run take any path, including an older
checkout. Each can lag:

| what lags | what happens | the tell |
|---|---|---|
| the **tools image** (`ingest_shard.py` / `embed_shard.py`) predates this series | on path B, all three markers are ignored *silently*: a marked collection is chunked unmarked and embedded without the prefix; a structured shard is ingested unstructured. On path C, a workflow that wires `--collection-id` into the embed step fails every embed task at argparse (loud); an unwired one embeds unmarked and `load_embeddings.py` refuses the file | receipt has no `chunking.structure_cuts` / `text_render` / `embed_heading_path` keys and no `enrichment.structure`; embedding-file header has no `chunking` object |
| the **extract** half of the image (`pdf_extract.py` / `jats_extract.py`) predates it | no `structure` key in the shard, so PDF and JATS documents ingest unstructured; Markdown is unaffected (its adapter runs at ingest) | `enrichment.structure.structure_missing` counts them, `adapters` shows `pdf-layout/1: 0` |
| the **server** predates it | no marker can be recorded at create; stamping on an ingest the server runs in-process is off | `GET /v1/collections` shows no marker; no `structure_version` on new chunks |
| the **workflow text submitted** predates PR 3 (an operator submits from a checkout older than the deployed server release, so the embed step is not wired) | `embed_shard.py` runs unresolved, so a marked collection's file is embedded unmarked | `gowe_batch_ingest.py` refuses before submitting; `load_embeddings.py` refuses before writing; the header has no `collection_id` |

What must ship in what order. (1) The **tools image at tag T** carrying PR 3's
`embed_shard.py` (and, for markers, PR 4's reader), since a workflow that passes
`--collection-id` to an older image fails. (2) The **server release at tag S**, which records
markers at create (PR 4), carries the dispatch gate, and stamps and ships every CWL text with
image T: the wiring of `collection_id`/`registry` into the embed steps of `jats-ingest.cwl` and
`pdf-ingest.cwl` (a change to a step's tool) and `embed-bulk.cwl`'s two new optional inputs (a
**workflow contract change**: new declared inputs, optional, so every existing input file still
validates) reach operators only this way, so a stamped tree never pairs the new text with an old
image. Operators must submit from the stamped tree of the deployed server release; the
`gowe_batch_ingest.py` pre-check and the load-time check are what catch a submission from an
older checkout. No runtime guard is added on path B,
as the owner decided for #829; the receipts are the tell there. And a marked collection is not
created on a GoWe tenant, nor submitted on path C, until a scratch ingest's receipt and
embedding-file header show the keys.

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
(`prepare_documents`, `iter_embed_source`, and `ingest_jsonl.py`'s own). The markers must
reach each of them from the same registry entry through the same reader, which after PR 3
they do (§7).

| path | where the adapter runs | how structure travels | what proves it |
|---|---|---|---|
| **A. API, in-process** | PDF and JATS: inside the loader `default_loader_registry()` dispatches to (`build_ingestor_for`); Markdown and extracted text: the pipeline's `attach_structure` step. The API calls these library functions directly; it never invokes a CWL tool (§7, *same code, not the same process*) | in memory, on `Document.structure` | the invariants (§2) on every loaded document; the parity tests below compare its output with B, C and D |
| **B. API → GoWe** | PDF: `pdf_extract.py`, through the same registry (#830); Markdown: `attach_structure` inside `ingest_shard.py`'s pipeline, from the raw text the shard carries | PDF structure as the `"structure"` key in the shard JSONL → `JsonlLoader` validates and restores it; Markdown structure is built at ingest | **parity test:** `test_extract_text_parity.py` grows a structure half — for each fixture, the structure the GoWe path ends with equals the in-process structure field for field, and the stamped chunk metadata of the two paths is identical |
| **C. operator CWL** (`jats-ingest.cwl`, `pdf-ingest.cwl`, `embed-bulk.cwl` on `embed_shard.py`; `ingest-bulk.cwl` on `ingest_shard.py`) | `jats_extract.py` (JATS adapter, §9.2) or `pdf_extract.py` | the same JSONL key → `embed_shard.py` → `run_embed_shard` → `IngestionPipeline.iter_embed_source`, which does **not** call `prepare_documents`; the `_stamp_structure` helper (§6) is therefore called from `iter_embed_source` too, next to its existing `_filter_boilerplate` / `_attach_glossary` calls. **Markers are read from the registry entry** (PR 3 gives `embed_shard.py` the resolution `ingest_shard.py` has; PR 4 routes it through `structure_from_params`), and the header's `chunking` object records what was applied; `load_embeddings.py`'s header-vs-entry check (§7) refuses anything else. `ingest-bulk.cwl` reads markers exactly as path B does | the same parity test, driven through `embed_shard.py` with a resolved entry; the load-time refusal test; the `gowe_batch_ingest.py` pre-check test |
| **D. legacy `ingest_jsonl.py`** | none (it consumes JSONL) | the tool does **not** use `JsonlLoader` — it builds `Document` itself from `enrich(record)` at three sites — so it gets the structure key through the same shared reader `JsonlLoader` uses, `structure_from_record(record, text)` (validate, or drop with a counted `structure_dropped`); its own chunk loop calls `attach_structure`, `_stamp_structure` and the `embed_heading_path` prefix in `_embed_drop_bad`, and reads the markers through `structure_from_params`. Under `--no-index` (catalog only) it resolves no entry (`target = None`), so the markers cannot be read and the marker checks are skipped — a documented skip like `load_embeddings.py`'s memory backend, since nothing durable is written | #831's rule: the API, `ingest_shard` and `ingest_jsonl` are checked for parity in one test, which feeds the same structured shard to all three |

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
  with a parity test before the JATS adapter PR (decided, Q7: PR 5a). **The second is a contract
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
  character offsets. ATX and setext headings open sections, each recording its raw rank in
  `Section.rank` (the preamble section has `rank=None`); a section closes at the next heading
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
  to no `Span` fall inside a section (§2 invariant 3); `roles` from `sec-type` via the
  `declared/1` map, split on `|` (§5; the adapter must start reading `sec-type`, which nothing
  does today); `rank=None` (JATS states nesting, not rank, Q11);
  `kind="abstract"` → a level-1 section titled `Abstract` with `roles=["abstract"]`,
  `role_source="declared/1"`, extended likewise over the abstract–body join; body-lead paragraphs
  before the first `<sec>` → the untitled level-1 section.
- `kind="title"` → `heading` block over the title text only: the span covers the rendered
  `## Title` line, so the block starts `min(depth+2, 6) + 1` characters after `span.start` and
  ends at `span.end`. `p` → `prose`; `list` → `list`; `disp-quote` → `quote`;
  `disp-formula` → `math`; `preformat`/`code` → `code`; `label` → `prose`.
- Table and figure **unit records** (`content_type=table|figure`) are separate documents today
  and stay so; each gets a one-section structure (`title` = the caption's label text, so the
  stamped `section_title` is `Table 2`, decided in Q13; `roles=["supplementary"]` with
  `role_source="declared/1"` only if the float sat in `<supplementary-material>`, else `None`) and a
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
pair (matched section, tag), for captions the labelled caption. Because section starts are
1–2% of lines, κ on boundaries is dominated by the prevalence; PABAK and raw agreement are
reported beside it. Role tags are rare per section too (most sections carry one or two of
twenty tags), so the same applies to role agreement: per tag, κ, PABAK and raw agreement
over the matched sections.

**Labels per document:** the ordered list of section starts (offset), titles, levels and each
section's **own** role tags (a set from `roles/1`, labelled from the section's own title or
`sec-type`, never inherited); every caption's `[start, end)` and label; every table body's `[start, end)`;
for each caption citation in the body, the caption it refers to. For PDFs additionally a
per-document checklist of the failure modes: two-column order broken, running header/footer in
`content`, caption splitting a paragraph, reference list present as body.

**Metrics:**

- Section boundaries: precision and recall of inferred section starts against labelled starts,
  at exact offset and at a one-line tolerance; reported per adapter, and for `pdf-layout/1` also
  per failure-mode checklist state.
- Roles, **gated at the chunk level, diagnosed at the section level** (roles are lists, Q4).
  *Gate metric:* for each tag *t*, the precision of the stamped `section_roles` over the chunks
  of the labelled documents (the chunks of the §11 base arm): |chunks stamped *t* whose labelled
  roles contain *t*| / |chunks stamped *t*|, with recall the same over |chunks whose labelled
  roles contain *t*|. A chunk's labelled roles are what the stamp would give it on the labelled
  structure: the union of the labelled own tags along the path of its owning section, the owner
  chosen by §6's rule over the labelled sections. This is what a filter acts on, so it is what
  the gate measures; it counts every chunk, **including chunks in sections whose boundary did
  not match a labelled one** — their tags are stamped too, and a spurious or misplaced section
  passes its tags to real chunks, so they cannot be left out the way the section metric leaves
  them out. It also prices inheritance: one wrong parent tag costs every chunk beneath it.
  *Diagnostic metric:* per-tag precision and recall of **own** tags over the sections whose
  boundary matched — each tag *t* a binary decision per section (*t* in the predicted set, *t*
  in the labelled set), precision |sections predicted *t* and labelled *t*| / |sections
  predicted *t*|, recall over |sections labelled *t*| — which attributes a chunk-level error to
  the section that caused it. `unknown` and `other` are tags like any other, so a wrong
  `unknown` is a recall miss on each true tag and a precision miss on `unknown`. Beside these:
  exact-set accuracy (predicted set = labelled set), mean Jaccard of the two sets, and a tag
  co-occurrence confusion table (labelled tag × predicted tag, counted over section pairs); all
  split by `role_source` (`declared/1` vs `rules/1`) and by `structure_source`. Every proportion
  carries a Wilson 95% interval; a tag with fewer than 10 labelled sections is reported but not
  gated on. Chunks of one document are not independent, so the chunk-level Wilson interval is
  computed with the document as the cluster (an effective-n correction), and the report says so.
  **Parsed roles** (`declared/1` on JATS, `rules/1` on parsed Markdown and JATS) have no gate,
  per Q6, but §10 measures them the same way, on the Markdown and both JATS strata.
- Captions and tables: precision and recall of `figure_caption`, `table_caption` and `table`
  blocks, a match being IoU ≥ 0.5 between the inferred and the labelled span (not the fraction
  of the labelled span covered, which a block that swallows the following paragraph would pass);
  label accuracy on matched captions.
- Citation resolution: the share of labelled `refers_to` citations whose resolved label matches
  the labelled caption; the wrong-caption rate separately from the unresolved rate.
- Agreement between `section_roles` containing `references` or `acknowledgements` and the
  existing boilerplate verdict `section`, as a descriptive table (Q9: the verdict does not
  consult roles in this series).

**Gates** (pre-registered; the numbers are the proposal), **per tag**: inferred roles are
stamped from the start with `structure_source=inferred` (Q6), but a `section_roles` filter on
tag *t* is exposed only for `structure_source=parsed` until, on the PDF stratum, tag *t*'s
**chunk-level** precision of the stamped `section_roles` ≥ 0.85 and boundary recall ≥ 0.80 at
one-line tolerance, **both judged on the lower bound of the Wilson interval**, not the point
estimate (40 documents give a few hundred sections; a point estimate of 0.86 with a lower bound
of 0.78 has not cleared the bar). The own-tag section metric is not a gate; it is how a failed
gate is traced to the sections that caused it. A tag with fewer than 10 labelled sections
cannot clear its gate and stays parsed-only. The gate is
per tag because a filter is per tag: `methods` can clear while `supplementary` does not.
`text-heuristic/1` roles stay `unknown`-only unless a tag's precision clears the same bar the
same way. §11's results are reported split by adapter in any case, so a
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
| **1 — model and stamp** | `Section` (with list-valued `roles` and the debugging `rank`)/`Block`/`DocumentStructure` (with `schema_version` and `content_sha256`)/`Document.structure`; `structure.py` with `validate_structure` (§2 invariants 1–7) and `stamp` (`section_roles` as the union along the path, §6); the `rules/1` table, combined-heading tags and `classify_roles(sections)`; the pipeline helpers `attach_structure` and `_stamp_structure`, called from `prepare_documents` **and** `iter_embed_source`, and from `ingest_jsonl.py`'s loop; schema: `chunk_metadata.json`, `metadata_schema.DECLARED_FIELDS`, ES mapping derived; `pdf_extract.py` writes the key; one shared reader `structure_from_record(record, text)` (validate or drop with a counted `structure_dropped`) used by `JsonlLoader` **and** by `ingest_jsonl.py`'s three `Document(...)` construction sites, which bypass `JsonlLoader`; `STRUCTURE_METADATA` setting, `--structure-metadata` on `ingest_shard.py` / `embed_shard.py` / `ingest_jsonl.py`, the CWL input; receipt `enrichment.structure`; `payload_matches` treats a list `actual` as any-element (the prerequisite for any role filter, §6); the `jats.py` docstring rewrite (§4). No adapter yet: structure only arrives via JSONL or a test. | invariant property tests (nesting, partition with extended ends, half-open, heading block first-in-section); stamp is pure and total (every chunk of a structured doc gets `section_level`, `structure_source`, `structure_version`); owner-selection ties; `section_roles` is the union of own tags along the path with `unknown` dropped beside any other tag (mutation: stamp own tags only → a chunk in a *Methods* subsection lacks `methods` → fails); `content_sha256` mismatch drops the structure; `caption_labels`/`refers_to` normalisation table; schema pin test; `Document.model_dump()` byte-identical when `structure is None`; `JsonlLoader` and `ingest_jsonl` both drop an invalid structure with a counted reason and keep the document, and ignore the key byte-for-byte when absent; the same structured shard stamps identically through `prepare_documents`, `iter_embed_source` and `ingest_jsonl` (the parity test feeds one shard to all three); `payload_matches` list test plus a conformance case; mutation: remove the validation and the test fails. |
| **2 — Markdown raw mode on both paths** | `markdown/1` raw mode in `attach_structure` under the `[markdown]` extra (preset + `table` + `front_matter` + `dollarmath`; BOM-shifted parse; title-only heading blocks; extended section ends; `Section.rank` recorded, never stamped); the refusal when the extra is missing and the adapter was asked for; parity test growth. | adversarial fixture (the one in `test_extract_text_parity.py`: BOM, front matter, CRLF, fences containing `#`, setext headings, tables, images, `$$` maths); front matter is a `front_matter` block and **not** a heading; heading blocks equal titles after inline-markup removal; structure built by `ingest_shard` from the shard equals the in-process structure field for field; stamped metadata identical on both paths; `STRUCTURE_METADATA=on` without the extra: refused, and `content` untouched. |
| **3 — registry resolution in `embed_shard.py`** (new; before any marker exists) | `embed_shard.py` gains `ingest_target.add_arguments` and, with `--collection-id`, `ingest_target.resolve_or_exit` plus the same build-spec check `ingest_shard.py` passes (method, size, overlap, model); no store contact (`require_store_urls` is not called); `--create-via-api` refused; `size_unit` from the entry as `ingest_shard._size_unit` does, a contradicting `--chunk-size-unit` refused by the `ingest_jsonl._refuse_size_unit_mismatch` rule; the load-side size-unit comparison (`check_build` compares only method/size/overlap/model/dim, so this closes a pre-existing hole); the embedding-file header gains a `chunking` object (`collection_id` as resolved, method/size/overlap/size unit as applied); `load_embeddings.py` refuses a header whose `collection_id` or applied chunking contradicts the entry before any write (skipped, logged, under the memory backend); CWL: `pdf-ingest.cwl` and `jats-ingest.cwl` wire their existing `collection_id` and `registry` (or `registry_db` via the load step's `EnvVarRequirement`) into the embed step; `embed-bulk.cwl` gains optional `collection_id` and `registry` inputs (a workflow contract change, §7 release order); `gowe_batch_ingest.py` learns to read whether the submitted workflow's `embed_shard.py` step binds `collection_id` (used by PR 4's pre-check). | resolved run: chunker built from the entry, contradicting `--chunk-size` / `--chunk-size-unit` exits 2 before any embed (mutation: drop the build check → a contradicting flag embeds → fails); unresolved run (no `--collection-id`) is byte-identical to today's output apart from the header's `chunking` object; the embed step resolves the same entry as the load step for each of the three workflows (cwltool on a scratch sqlite registry); `load_embeddings` refuses a header naming another `collection_id` (mutation: drop the check → loads → fails); `load_embeddings` refuses a `sentence`/`words` file whose header's applied `size_unit` contradicts the entry (read method-conditionally, as `ingest_shard._size_unit`), and accepts any `size_unit` on a `fixed_token` entry (mutation: compare unconditionally → a `fixed_token` file with a junk unit is refused → fails; drop the comparison → a `tokens` file loads into a `chars` entry → fails); an old header (no `chunking` object) on an unmarked entry loads exactly as today; the workflow-wiring reader recognises the wired and unwired forms of all three workflow texts. |
| **4 — `text_render` and the one reader** | `structure_from_params` (returns the four markers — `text_render`, `structure_cuts`, `embed_heading_path`, `structure_infer` — plus `needs_structure`); `text_render="markdown-plain/1"` stripped mode, applied at ingest from the record's suffix; API create validation and explicit recording; `ingest_shard`, `embed_shard` (through PR 3's resolved entry) and `ingest_jsonl` read through the reader and refuse contradicting flags; **path C backstops** (§7): the header's `chunking` object gains the markers as applied, `load_embeddings.py` refuses a marker mismatch between header and entry before any write, `gowe_batch_ingest.py` refuses a marked collection **before submitting** unless the workflow's `embed_shard.py` step binds `collection_id` (PR 3's reader), and the API's GoWe dispatch refuses a marked collection on a workflow whose `embed_shard.py` step is unwired; receipt `chunking` keys; `CHUNKING.md` section. | every-word-survives test (links, code, cells, math); heading block text equals title exactly; absent marker → `content` byte-identical to `TextFileLoader` on both paths; the GoWe shard is identical with and without the marker (the extract step does not see it); unknown value → 400 / exit 2; `chunk_descriptor` changes with the marker (identity proven); **`load_embeddings` refuses** an embedding file whose header shows no markers against an entry that records one, and the reverse, with nothing written (mutation: drop the check → a marked entry accepts an unmarked file → fails); an unmarked entry with an old header (no `chunking` object) loads exactly as today; a marked entry chunks and embeds identically through `ingest_shard`, `embed_shard` (resolved) and `ingest_jsonl` (the markers' parity test); `gowe_batch_ingest.py` exits before any submission for a marked entry on an unwired workflow and submits on a wired one (mutation: drop the pre-check → a submission is built for the unwired text → fails). |
| **5a — JATS parity (#833 items 1–2), a contract change** | `.xml` → `JatsLoader` in the registry; the in-process unknown-suffix fallback becomes a refusal with the shared error. Contract first: `contracts/openapi.yaml` wording for `/v1/ingest` by reference (a file with no loader is a failed item with `no loader for <suffix>`, where today it is ingested as text), a conformance case, then the Python change and the Go side's equivalent. | three-path parity test for a JATS fixture (in-process, `pdf_extract.py`, `jats_extract.py`): same `content`, same doc id, same metadata subset; unknown suffix refused identically on A and B; the conformance case passes against both implementations. |
| **5b — JATS adapter** | `jats/1` from `Span` + `sec-type` → `declared/1` (split on `\|`, multiple tags); unit-record structures, whose `section_title` is the caption label (Q13); `jats_extract.py` writes the `structure` key on every record, and its `--spans-out` help and module docstring are rewritten (§4: the side file becomes a debugging duplicate of the key). | fixture with untitled `<sec>`, nested `<sec>`, `sec-type` present and absent, floats in `<floats-group>`; untitled section is a first-class section; `section_title` on article chunks is the heading, not the unit suffix; `materials\|methods` gives `[materials, methods]`; declared roles never overwritten or added to by rules. |
| **6 — accuracy-check harness** | `scripts/eval/structure_accuracy.py`: label format (own role tags per section), per-tag κ / PABAK, the §10 metrics including the per-tag chunk-level gate (document-clustered Wilson interval) and the own-tag section diagnostic, the public export with the part-4 allow-list discipline; the labelling run itself is an owner-scheduled activity, its record a results directory. | metrics on a synthetic labelled fixture; the exporter refuses any string that is not a short safe token. |
| **7 — `structure_cuts` and `embed_heading_path`** | segmenter in `chunkers.py` (§7.1); heading attached to the following segment; `embed_heading_path` applied in `_embed_and_link` and in `ingest_jsonl._embed_drop_bad` to the embed input only; both markers force `needs_structure`; receipt `chunking` keys. **Depends on #831's merge PR** (opt-in `size_tolerance` / `min_piece`, in progress separately): if it has landed, this PR adds the section wall to its merge; if not, this PR ships the cuts without any merge and the wall is added in #831's PR, which then depends on this one. The table assumes the first order. | chunks tile the document under every method; no chunk spans two leaf sections; sibling merge sets `section_crossing`; `fixed_token` overlap does not cross a segment; stored `content` unchanged under `embed_heading_path` on all three embed loops, `embed_shard` included (mutation: prefix leaks into `content` → fails); `embed_heading_path` with `STRUCTURE_METADATA=off` still stamps and prefixes (mutation: honour the setting → fails); absent markers reproduce today's spans against the #829 baseline fixture. |
| **8 — inferred adapters and captions** | `pdf-layout/1` with the text-identity guard; `text-heuristic/1`; caption and table blocks; `refers_to` for all adapters. | fixture PDFs built in-test (reportlab) with known headings, a two-column page, a running header, a caption mid-paragraph, a reference list; text-mismatch guard emits no structure (mutation: emit anyway → fails); every inferred section has `structure_source=inferred`; unrecognised → `unknown`. |
| **9 — evaluation arms** | the §11 harness extensions, the placement transforms, the pre-registration template. | arms built through `ragstack.ingestion` only (a test imports nothing else); result files hold counts and ids only. |
| **10 — rollout** | conformance report row for partial coverage (a collection re-ingested into with stamping on, §6); docs (`CHUNKING.md`, `ingest-paths.md`, `cookbook-users.md`, including "re-ingest into a new collection" as the way an existing corpus gets structure); tools image + workflow stamp (ADR-0010, §7 release order); `STRUCTURE_METADATA` default flipped to `on` only after §10's record exists. No backfill script (Q12); the ASM and open-access corpora's re-ingest is planned separately. | the partial-coverage row fires for a scratch collection with one re-ingested document and not for a freshly ingested one; a non-production tenant is the only live target. |

PRs 1–2 deliver the owner's minimum (section type as metadata, on Markdown, on both paths).
PR 3 makes path C read the registry the way every other path does, before any marker exists to
be missed; PR 4 adds the markers on all four paths at once. PRs 5a–6 make JATS and the accuracy
check real. PR 7 is the chunking change. PR 8 is the only one that can be wrong about a
document, and it lands after the thing that measures it. (The draft numbered these 1–9 with
PR 3 absent; old PR 3 is now 4, 4a/4b are 5a/5b, and 5–9 are 6–10.)

---

## 13. Compatibility, stated plainly

- **Existing collections are untouched.** No marker → the loaders return the same `content`,
  the chunkers return the same spans and ids (proven against the #829 baseline fixture), the
  stores receive the same payload. `STRUCTURE_METADATA=off` → no new metadata keys at all.
- **No existing collection gets structure in place.** There is no backfill (Q12); an existing
  collection gets structure only by re-ingest, preferably into a new collection. A re-ingest
  into the existing one with stamping on (no marker) adds declared fields to the re-ingested
  documents' chunks only; `structure_version` makes the mixture auditable, the conformance
  report lists it, and no filter on the new fields is offered for a partially covered
  collection. The ASM and open-access corpora get structure through a separately planned
  re-ingest, outside this series.
- **A marker is a new collection**, by content address, exactly as `size_cap` is. Nothing
  converts in place; the UI's create form gains the three fields the way it gained `size_cap`.
  A marked collection behaves the same on all four ingest paths once PR 3 and PR 4 ship (§7).
- **Old receipts, manifests, embedding files and archives read unchanged**: every new key is
  optional and omitted when absent (`chunking`/`enrichment` precedent; an embedding file without
  the header's `chunking` object loads into an unmarked entry exactly as today). The archive
  format gains no role: the structure is not archived in this series (Q1). A restored archive
  carries whatever stamped fields its chunks had; it is never re-stamped by restore.
- **`embed_shard.py` without `--collection-id` behaves as today.** PR 3's registry resolution is
  opt-in per invocation, so a hand run against an unregistered scratch store, and every
  existing input file of the three workflows, keeps working; only marked collections need the
  wired workflow, and the backstops refuse them otherwise.
- **Workflow contracts.** `embed-bulk.cwl` gains two optional inputs (`collection_id`,
  `registry`); `jats-ingest.cwl` and `pdf-ingest.cwl` gain no workflow input, only bindings in
  their embed step. All of them ship stamped in the server release at tag S, after the tools
  image at tag T (ADR-0010, §7): a workflow text that passes `--collection-id` to an image older
  than PR 3 fails at argparse, loudly, and a stamped release never pairs them.
- **The embed step now needs the registry, per scatter task** — a worker-group precondition.
  For `jats-ingest.cwl` the sqlite `registry_db` is staged into every embed task through the
  same `EnvVarRequirement` the load step uses; for `pdf-ingest.cwl` (and `embed-bulk.cwl` when
  given a `registry`) the worker reads `COLLECTION_STORE_DSN_<NAME>` from its `--secret-file`.
  These are the same workers as the load step, and the embed steps already declare
  `NetworkAccess: true`, but a worker group that serves embed tasks and was never configured for
  the registry fails each resolved embed task with `resolve_or_exit`'s refusal (exit 2) before
  any embedding.
- **A new embedding-file header loads under an old loader.** `read_header` validates only the
  `schema` tag, so an old `load_embeddings.py` ignores the header's `chunking` key and loads the
  file as it would any other (without the new checks, which is the old-image row of §7).
- **Elasticsearch**: new declared fields reach existing indices through the existing
  `put_mapping` path; a divergent index that rejects the full mapping falls back as today and
  is listed by `metadata_conformance.py`.
- **Go**: the Go server treats chunk metadata as an opaque map; the contract change is the
  schema file, which both implementations are checked against by conformance. No Go code change
  is expected for the metadata and chunking PRs. **PR 5a is the exception**: the unknown-suffix
  refusal on by-reference ingest is contract-visible, so it needs the `openapi.yaml` wording, a
  conformance case, and the Go side's equivalent behaviour (per CLAUDE.md's rule for API
  changes).
- **The chunking study's pins** (`EXPECT_COMMIT`, `sentence_spans_fingerprint`) are unaffected:
  `sentence_spans` does not change; `structure_cuts` runs before it on offset ranges.

---

## 14. Red team: how this design fails, and what catches each failure

| failure mode | how it would show | what catches it |
|---|---|---|
| **Offsets drift** — a transform runs on `content` after structure exists (a `.strip()`, a newline normalisation, a BOM removal) | `content[start:end]` is off by a few characters; `section_title` lands on the wrong section near the edges; `heading` blocks do not equal titles | §2 invariants 1–5 and 7 (`content_sha256`) asserted on every load (`JsonlLoader`, in-process); the parity test compares structures field for field after the JSONL hop; every adapter runs after the loader's normalisation (inside the loader for PDF/JATS, in `attach_structure` for Markdown), and the BOM is handled as a parse-input shift, not a strip |
| **Structure built from a different text than the stored one** (PDF dict walk vs `get_text()`; later, a structure node beside the wrong text node) | plausible sections, wrong positions, silently | the byte-identity guard in `pdf-layout/1`: mismatch → no structure, counted; §10 reports the rate; `content_sha256` (§2 invariant 7) on every load |
| **Heading false positives in PDFs** — bold figure labels, running headers, author affiliations, the first line of every page | hundreds of tiny sections; `section_title` is a page header; `structure_cuts` shreds the document | running-header suppression by repetition; the score threshold; §10 boundary precision per PDF; `structure_source=inferred` lets filters and the evaluation exclude it; the §7.1 merge recombines tiny siblings |
| **Two-column order** — blocks emitted column-interleaved | a section boundary falls mid-sentence; `refers_to` and captions land in the wrong section | §10's checklist counts it; a diagnostic: the share of section starts that are not sentence starts (cheap, computable on every PDF, logged) |
| **Reference list parsed as body** | the bibliography is `prose` in the last titled section, retrievable as evidence | `references` role via heading rules; the boilerplate verdict still runs per chunk and is independent; §10 reports the agreement between the two; `--boilerplate drop` is a config switch today |
| **Role mislabels propagating into filters** — `methods` on a results section | a `methods`-only query misses evidence silently | roles are hand-checked per tag (§10) before any filter is offered; `section_roles_version` names the producers; inferred roles are gated per tag; the vocabulary has `other` and `unknown` so the classifier is never forced to guess |
| **Inheritance amplifies one wrong tag** — a parent mis-tagged `methods` passes the tag to every subsection's chunks | a `methods` filter returns a whole results subtree | §10's chunk-level table of the stamped union shows the amplification beside the own-tag numbers; own tags are kept in the structure record, so the cause is attributable to one section |
| **Combined headings over-tag** — *Results and Discussion* tagged both | a `results` filter returns discussion text | by design (Q4): a combined section *is* both, and a list says so; the per-tag precision in §10 measures it, and a reader filtering on one tag accepts the shared sections |
| **Markdown regex shortcut** — a `#` inside a fence or a `---` setext line read as a heading | code comments become sections; front matter opens a section | CommonMark parser only, no regex fallback; the adversarial fixture contains both cases |
| **Untitled JATS sections merged into their predecessor** | 39% of evidence-bearing sections lose their boundary | JATS structure comes only from `Span`s, never from re-parsing `## ` lines; fixture with untitled `<sec>` |
| **Repeated text aliasing** — the same heading twice in a document | a search-based locator picks the first occurrence | offsets are accumulated, never searched (§2); the stamp uses offsets, not titles |
| **Mixed collection** — stamped and unstamped chunks after a re-ingest into an existing collection, or two `structure_version`s | a `section_roles` filter under-returns with no error | no backfill exists to create it silently (Q12); `structure_version` on every stamped chunk; the conformance report row (PR 10); no filter offered on partial coverage; the documented advice to re-ingest into a new collection |
| **Old worker image ignores a marker** (the #829 trap) | on path B, a marked collection chunked unmarked, no error | receipt `chunking.structure_cuts`/`text_render`; release order documented; the `[chunker]` stderr line; on path C the header's missing `chunking` object makes `load_embeddings.py` refuse |
| **Old workflow text on path C** — the embed step does not pass `collection_id` to `embed_shard.py` | a marked collection embedded unresolved, unmarked | `gowe_batch_ingest.py` refuses before submitting (it reads the wiring from the text); `load_embeddings.py` refuses before writing; a hand-run `cwltool` submission is caught by the load step alone |
| **New workflow text on an old image** | every embed task exits 2 with `unrecognized arguments: --collection-id` | loud by construction, before any embedding; a stamped release names the image that carries the flag (ADR-0010 decision 4) |
| **Embed and load resolve different entries** — the embed step and the load step pointed at different registries | vectors built for one spec loaded under another | the header's `collection_id` and applied chunking are checked against the load step's entry (PR 3); both steps take the id and the registry from the same workflow inputs |
| **Old extract image writes no `structure` key; old ingest image ignores one** | PDF and JATS documents ingest unstructured with no error | `enrichment.structure` absent (old ingest image) or `structure_missing` counting (old extract image); §7 release-order table |
| **One spec, two results by deployment setting** — `embed_heading_path` set but `STRUCTURE_METADATA=off` on one host | the same collection embedded with and without the prefix | the markers force `needs_structure`; the PR-7 mutation test |
| **Silent parser fallback** (the #815 class) — `[markdown]` extra missing in one image | structure silently absent on one path, present on the other | refusal when the adapter was asked for; `enrichment.structure.backend` names the parser |
| **A list field filtered through the in-memory predicate** | context expansion and in-memory stores return nothing for `{"section_roles": "methods"}` or `{"block_types": "table"}` while the ES/Qdrant legs match | `payload_matches` any-element fix as a prerequisite in PR 1, with a conformance case |
| **Stripped text changes token counts** | chunk sizes and counts differ from the raw collection | by design: `text_render` is identity; the two are different collections |
| **`refers_to` false positives** — "Table 2 of [12]" cites another paper's table; "Fig. 1A–C" variants | wrong caption linked; the attached-placement arm attaches the wrong text | normalisation table with tests; §10's wrong-caption rate is a first-class metric; `refers_to` is metadata, never moves text until an arm wins |
| **Duplicate labels** — two "Figure 1" (main and supplementary) | `caption_labels` ambiguous | labels keep their prefix (`Figure S1` ≠ `Figure 1`); duplicates within a document are counted in §10 |
| **Heading path leaks into stored content** | `content` no longer equals `doc.content[start:end]`; ids still match, text does not | PR-7 test: stored `content` byte-identical under `embed_heading_path`; the prefix is applied to the embed input list only |
| **Structure bloats the JSONL** — a block per line on a pathological PDF | shard files grow, `JsonlLoader` slows | size ceiling with a counted drop; blocks need not cover the text, so an adapter may emit fewer |
| **Filter grammar surprises** — `section_path` is a list | a filter on it matches any element | the same as `authors` today; documented in the schema description |
| **Two sources for an identity value** (a CLI flag beside the entry's `chunk_params`; today `embed_shard.py --chunk-size-unit`) | #609 again | the one reader on all four paths; flags refused when they contradict the entry, `--chunk-size-unit` included (PR 3); no CWL input for the markers — the embed step receives the registry id and name, never a marker — which is possible only because `text_render` is applied at ingest (§2, §7) |

---

## 15. Decisions (owner, 2026-10-27)

The fourteen questions the first draft put to the owner, each with the decision and its
consequence for this note. Where a decision changed the recommendation, the change is stated.

1. **Store the whole structure per document?** *Decision: not in this series.* The eventual
   home is Shock — the BV-BRC object store, not the Workspace — with chunks as read offsets over
   the document text (`metadata-and-kg.md` §2b, proved on a dev collection first), and possibly
   section nodes in the knowledge graph (`Document -HAS_SECTION-> Section`, with roles) if graph
   queries over sections are wanted; chunks never go in the graph. In this series the structure
   travels in the JSONL `structure` key, serialised so it can be written to a Shock node
   unchanged. The archive-role alternative of the draft is dropped. *Consequence:* §4.1 states
   what this series guarantees for the Shock series (`schema_version`, `content_sha256`,
   offsets shared with the chunks); nothing stores structure today, and there is no Shock or S3
   backend (`tenant-admin.md` §6a).
2. **One `chunk_params` key or three?** *Decision: three* (`text_render`, `structure_cuts`,
   `embed_heading_path`), as recommended. *Consequence:* each §11 arm is its own collection;
   none needs a CWL input (§7).
3. **Is metadata stamping identity?** *Decision: no*, as recommended — a deployment setting
   (`STRUCTURE_METADATA`) with a per-chunk `structure_version`, and the markers force stamping
   regardless of the setting. *Consequence:* mixed coverage is possible only through a re-ingest
   into an existing collection (§6, with Q12), and is audited, not prevented.
4. **One role vocabulary without a document-class switch?** *Decision: yes, `roles/1`, but as a
   list of tags*: a section carries a list of roles, and the chunk field is `section_roles`. A
   chunk carries every role along its section path (subsections inherit their ancestors' roles),
   so a filter on `methods` finds chunks in *Methods*' subsections; combined JATS `sec-type`s and
   combined headings map to several tags. *Consequence:* `Section.roles` (own tags) in §3, the
   tag rules and the `|`-split `sec-type` map in §5 (`materials` added as a tag, see below), the
   union stamp in §6, per-tag scoring and per-tag gates in §10, and the list-filter predicate fix
   of PR 1 becoming a prerequisite for any role filter. Own and inherited roles are told apart
   in the structure record (own tags only); the chunk carries the union.
5. **`role_source` spelling?** *Decision: `declared/1`*, as recommended. *Consequence:* the
   `sec-type` map is versioned like the rule table (§3, §5).
6. **Stamp inferred roles from the start?** *Decision: yes*, marked `structure_source=inferred`,
   with no filter on inferred roles until the gate passes; as recommended. *Consequence:* §10's
   gate, now per tag, decides when a filter on an inferred tag is offered; `text-heuristic/1`
   stays `unknown`-only unless a tag clears it.
7. **Fix #833 items 1–2 in this series?** *Decision: yes*, before the JATS adapter, as a contract
   change (`openapi.yaml` wording, a conformance case, both implementations); as recommended.
   *Consequence:* it is PR 5a in the renumbered §12 (the draft's PR 4a).
8. **Caption placement: ingest option or harness-only?** *Decision: harness-only until an arm
   wins*, as recommended. *Consequence:* placement is a §11 transform; a winning `separate` or
   `attached` arm becomes a content-changing marker in a later series.
9. **Should the boilerplate verdict consult section roles?** *Decision: not in this series*, as
   recommended. *Consequence:* §10 reports the agreement between `section_roles` and `section`
   descriptively, to revisit with numbers.
10. **Add `section_crossing`, stamped only when true?** *Decision: yes*, as recommended.
    *Consequence:* the §6 field, expected on the first chunk of most titled parents (§7.1).
11. **`level` as tree depth or raw heading rank?** *Decision: tree depth*, as recommended. The
    owner asked whether raw rank should match it in theory: only for well-formed Markdown. It
    breaks on skipped levels (`#` then `###`), on documents that start at `##`, and on formats
    without rank (JATS nests, a PDF's rank is inferred from font size). *Consequence:* the raw
    Markdown rank is kept in the structure record as `Section.rank`, for debugging, and is not
    stamped on chunks (§3, §9.1).
12. **Backfill existing collections?** *Decision: no backfill for any collection.* Existing
    collections get structure only by re-ingest; the ASM and open-access corpora need a
    separately planned re-ingest, outside this series. *Consequence:* the backfill script and
    steps are gone from §6, §12 and §13; mixed coverage arises only when someone re-ingests into
    an existing collection with stamping on, and the recommendation is to re-ingest into a new
    collection instead (§6).
13. **`section_title` on JATS table and figure unit chunks?** *Decision: the caption label*
    (`Table 2`), as recommended. *Consequence:* the unit suffix survives in `source`/`path`, and
    `structure_version` separates old rows from new (§6, §9.2).
14. **`structure_infer`: identity marker or non-identity setting?** *Decision: an identity
    marker in `chunk_params`*, as recommended — **and option (a) moves into this series**: the
    owner's principle is that GoWe/CWL is the main ingest path and the same code runs on every
    path, so `embed_shard.py` gets registry resolution the way `ingest_shard.py` has it (the #263
    resolution with #563's registry name), and builds its chunker and markers from the collection's spec through
    `structure_from_params`. The draft's refuse-on-path-C gates stay as backstops for old images,
    old workflow texts and hand-run submissions. *Consequence:* PR 3 is new and lands before the
    markers (PR 4); once both ship, `structure_infer` and the other three markers work on all four
    paths (§7, §8); the release order is the tools image at tag T, then the server release at tag
    S, which stamps and ships every CWL text — `embed-bulk.cwl`'s new optional inputs, a workflow
    contract change, included — and operators submit from that stamped tree (§7, §13). Path A calls the same library code
    in-process rather than invoking the CWL tool, and the parity tests (#833) are what prove the
    paths give the same output.

**Follow-on decisions, confirmed by the owner (2026-10-27).** The first revision had to make
these to carry the decisions above out; the owner confirmed each.

- **`materials` is a `roles/1` tag**, so *Materials and Methods* and `materials|methods` are
  `[materials, methods]` and a `methods` filter still finds them (§5).
- **`unknown` never sits beside another tag**, in a section's own list or in the stamped union;
  `other` sits beside inherited tags in the union (§5, §6), so `["unknown"]` keeps meaning
  "nothing on the path could be classified".
- **`section_roles_version` is a list** (the distinct producers along the path), because a union
  can mix a declared parent with a rules-classified child. It is the first list-valued
  `*_version` field — its precedent, `symbol_glossary_version`, is a string — so a filter on it
  means "some contributing producer was *v*", not "the producer was *v*" (§6's any-element
  semantics).
- **`content_sha256` and `schema_version` on `DocumentStructure`**, so the serialisation checks
  itself when it leaves the JSONL record (§4.1).
- **The `gowe_batch_ingest.py` pre-check reads the workflow text, not the image** (§7): it
  refuses a marked collection unless the workflow's `embed_shard.py` step binds `collection_id`.
  The image's age is not knowable from the driver on an unstamped tree, and an old image handed
  the new flag fails loudly at argparse, so the text is the only silent case left.
- **`--collection-id` on `embed_shard.py` is opt-in per invocation** (§7, §13), so hand runs on
  unregistered scratch stores and existing input files keep working.

**Open for the owner: byte offsets.** Structure offsets, like `start_char`/`end_char`, are
code points; Shock's indices are byte ranges, so the Shock series needs a code-point→byte
conversion (§4.1). *Recommendation:* do not record byte offsets in this series. They are
derivable from `content` at any time, `content_sha256` verifies that the text they are derived
from is the right one, and recording them now would add a second coordinate system to every
section, block and chunk before anything reads it.
