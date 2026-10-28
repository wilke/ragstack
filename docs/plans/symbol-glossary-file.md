# The glossary file: `glossary.jsonl` (#847 G1)

Status: G1 is built. The file format, its reader and writer, a builder, and attaching
from a file on two paths:

- the bulk load (`load_embeddings.py --glossary-file`);
- the Elasticsearch-only re-attach (`backfill_symbol_glossary.py --glossary-file`).

G2a (the step as a tool, not yet wired) is built: `scripts/glossary_step.py` writes
the file from a shard, rules plus an optional bounded LLM pass with per-document
degrade; `ingest_shard.py --glossary-file` renders from it on the coupled path;
`archive_version.py --glossary-file` packs it into the version (role `glossary`); and
`glossary_file.py merge` combines per-batch files. G2b (the CWL wiring and the API
inputs) waits for the tools-image repin, and G3 (the ADR-0011 amendment and the
migration note) is not built yet. Under ADR-0010 decision 4, no CWL declares a flag
before its image carries it.

Background: [symbol-glossary.md](symbol-glossary.md) describes the glossary itself (#754).
Issue #847 describes the design.

## Why a file

Today the glossary is computed inside every ingest path and rendered onto the chunks
before they are embedded. The LLM half lives in a scratch cache keyed by the document's
text. #847 makes the glossary a recorded artifact instead:

- one file per collection version, kept beside `chunks.jsonl.gz`;
- produced once;
- rendered onto the chunks by the load step.

A new glossary version is then a load-only rerun: an Elasticsearch update, with no
re-embed.

## The format

Code and the normative description: `python/ragstack/ingestion/glossary_file.py`
(module docstring). It is newline-delimited JSON in UTF-8, with sorted keys, one
object per line.

**Line 1, the header.**

| key | value |
|---|---|
| `kind` | `"header"` |
| `schema` | `"ragstack.glossary_file/v1"`. The reader refuses any other value. |
| `producer` | Free text naming the tool and its version. |
| `created` | ISO-8601, and it must be UTC (`Z` or `+00:00`). |
| `counts` | `documents`, `with_llm`, `degraded` and `merged_entries`. They must equal what the rows hold, so a truncated or spliced file is refused. |
| `config` | The `GlossaryConfig` the producer used. The load renders with this config. An unknown key or a wrongly typed value is refused. A missing key takes the reader's current default for that field, so a file that omits a key renders under whatever that default is at read time. The writer always writes every key. |

**Every other line, one document.**

| key | value |
|---|---|
| `kind` | `"document"` |
| `doc_id` | The document id. It is unique in the file. |
| `text_sha256` | `content_key(text)`: sha256 of the UTF-8 text with outer whitespace stripped. This is the LLM cache's key. |
| `text_chars` | The text's length in code points, before `content_key`'s strip. |
| `text_ws_sha256` | The masked identity: sha256 of the text with every character of the mask class replaced by a space (see below). |
| `glossary_version` | It must equal `merged.version`. |
| `rules` | A glossary. |
| `llm` | A glossary, or `null`. |
| `merged` | A glossary. The load renders this one. |
| `drops` | Entries dropped, as counts by reason. It is `{}` where the reasons are unknown. G1 records only `revalidation`. |
| `provenance` | A free-form object. The reader records it and never interprets it. |
| `degraded` | `null`, or the reason the document did not get its full glossary. |

A glossary is `{"version": …, "entries": [{"symbol": …, "definitions": [{"text": …,
"offset": …}]}]}`. The reader enforces four rules:

- Entries are sorted by symbol, strictly ascending, so each symbol appears once.
- Definitions are in document order: offsets never decrease, because positional
  rendering bisects on them.
- Each offset is a non-negative integer. It is a raw character offset into the document
  text, in the same coordinates as `Chunk.start_char`.
- Every key is required, and no other key is allowed.

The writer validates every row with the reader's own parser, refuses a duplicate
`doc_id`, and writes atomically: it writes a temporary file and renames it.

**The masked identity.** `text_ws_sha256` is a second, gap-tolerant identity
(`masked_key` in the module). Every character of the mask class is *replaced* by a
space, not removed, so the hash also pins the offset of every other character. The
class (`MASK_CLASS`, version `mask/1`) is fixed in the code, code point by code point,
so the hash does not depend on the Python build's Unicode version:

- Unicode White_Space: U+0009–000D, 0020, 0085, 00A0, 1680, 2000–200A, 2028, 2029,
  202F, 205F, 3000;
- Default_Ignorable_Code_Point (Unicode 15.1): U+00AD, 034F, 061C, 115F–1160,
  17B4–17B5, 180B–180F, 200B–200F, 202A–202E, 2060–206F, 3164, FE00–FE0F, FEFF,
  FFA0, FFF0–FFF8, 1BCA0–1BCA3, 1D173–1D17A, E0000–E0FFF;
- the C0 and C1 controls (Cc): U+0000–001F, 007F–009F;
- the remaining format characters (Cf, Unicode 15.1): U+0600–0605, 06DD, 070F,
  0890–0891, 08E2, 110BD, 110CD, 13430–1343F, FFF9–FFFB;
- U+FFFD, the replacement character.

These are the characters a tokenizer's normaliser drops (BERT's cleanup removes Cc,
Cf and U+FFFD) or its pre-tokenizer splits on (whitespace). They are therefore what
a WordPiece offset mapping leaves in no chunk.

Combining marks (Mn) and private-use characters (Co) are deliberately not in the
class, although BGE/WordPiece leaves them uncovered too. At a document's edge or at
an overlap-0 window boundary such a document is unverifiable, so the override
applies, and SFR (byte-level, the default stack) verifies it exactly. Masking them
would make `r̄` equal to `r ` at an uncovered boundary, and we chose not to.

**Symbols are not re-canonicalised on read.** They are stored as the canonical keys
the extractors produce. If a future canonicalisation change re-canonicalised them on
read, old files would become unreadable.

**`merged`.** It is `choose_merged(rules, llm)`, which both the G1 builder and the
G2a step use. It is the LLM glossary when that glossary has at least one entry, and
the rules glossary otherwise. When the LLM half is present but empty (the gate kept
nothing), the row records that empty `llm` half and its provenance unchanged, and
`merged` is the rules glossary, stamped `rules/3@file/1`.

That last case changed after the #850 review. Before it, an empty LLM glossary won:
`merged` was empty, the row was stamped `llm/…`, and the rules' entries for that
document were dropped. This old prefer-LLM-even-if-empty rule is still the rule of
the in-process cache path (`SymbolGlossaryAttacher.glossary_for`), which this change
leaves alone. So the two now diverge on that one case:

- **The October "rules+LLM" arm used the old rule.** It was built by
  `scripts/eval/glossary_scratch_experiment.py` through `glossary_for`
  ([symbol-glossary.md](symbol-glossary.md#the-experiment-2026-10-21)).
- **How much it matters.** On a 260-document sample (an `llm/1` cache from
  thinking-off calls; the rules half recomputed with `rules/3`), the old rule
  emptied 7 documents, which lost 9 of the sample's 136 rules entries.
- **The backfill guard.** `backfill_symbol_glossary.py --glossary-file --apply`
  over an index that was stamped `llm/…` refuses such a row without
  `--replace-llm`, because the row now carries a rules stamp. That is the guard
  doing its job.

The file keeps both halves, so a later union is a re-render, not a re-run. If
`merged` ever becomes a union, its version string must still begin with `llm/`
whenever it holds any LLM entry. Otherwise the backfill's `llm/` guards misfire:
they would treat an LLM glossary as rules output.

**Changes after the #850 review.** These change the step's behaviour, not the file
format:

- **Degraded `cut_off`.** A document whose every window ended in an unresolved
  cut-off, with nothing kept, is degraded `cut_off`. It gets the rules and
  `llm: null`, and is not recorded as a success.
- **`--reuse`.** A prior row is reused only when its `text_sha256` and LLM version
  match and its recorded `cutoff_retry` and `seed` equal this run's.
- **Window floor.** `--llm-window-chars` must be greater than the 2,000-character
  window overlap.
- **Thinking flag.** `--llm-thinking unset` sends no `chat_template_kwargs`. The
  version string records this as `ku`, next to `k0` and `k1`.
- **The gate's labels.** A proposed "symbol" that is really a term the document
  contains (`air change rate`) is labelled `not_a_symbol`, not `symbol_absent`. The
  label is the only thing that changes: what the gate keeps and rejects stays the
  same.
- **The repair round.** It sends back only rejections a rewording can fix:
  `word_not_in_document`, `not_local`, `another_symbol_between`, `implausible`, and
  `symbol_absent` for a single-token symbol. When none is left, it makes no call.
  `not_specific` was dropped from the repair prompt's list of reasons. That changed
  the prompt hash, and with it every `llm/2` version string. `--llm-repair-rounds` > 0
  has no measured gain yet.

## The version stamp

A chunk rendered from a file is stamped
`metadata.symbol_glossary_version = <glossary_version>@file/1`. Two examples:

- `rules/3@file/1`;
- `llm/1:<model>:<prompt hash>:w80000x4:t2048@file/1`.

A suffix is used rather than a prefix, so the existing prefix meaning is unchanged:
`llm/…` still means "an LLM glossary". The backfill's guard against replacing an LLM
glossary with rules output relies on that prefix. The suffix also records that the
field came from a file, and which file-render version produced it.

## Building a file from what exists today

```
python scripts/glossary_file.py build --jsonl docs.jsonl [--llm-cache cache.jsonl] \
    [--llm-cache-version V] [--config JSON] --out glossary.jsonl
python scripts/glossary_file.py verify glossary.jsonl [--jsonl docs.jsonl]
```

`build` reads the same pre-extracted JSONL the ingest tools read (`JsonlLoader`). A
file built without a cache renders exactly the field that today's in-process attach
produces. The parity test checks this chunk by chunk.

`verify` reads the file strictly. With `--jsonl`, it also checks each document's
`text_sha256` and exits 1 on a mismatch or on a document that has no row.

## Attaching at load

`load_embeddings.py --glossary-file F` renders each chunk's `symbol_glossary` and its
version stamp from `F`. It uses `Glossary.for_chunk` with the file's config:
positional and value-only by default. Whatever the embedding file carries is ignored.

Every check runs before any store is created or written. One failing document refuses
the whole load with exit 2. Without the flag, chunks load exactly as the embedding file
holds them; a byte-identity test checks this.

**Where the text comes from.** An embedding file holds chunks, not documents. The text
is reassembled from the chunks: `content` is pasted by `start_char`, as the backfill
already does (`ingestion/reassembly.py`), with any gap filled with spaces. Each document
is then checked in order:

1. **Verified.** The reassembly's `content_key` equals `text_sha256`. This exact check
   holds only when the chunks *tile* the text. The recursive, sentence, word and
   semantic chunkers do. `fixed_token` does only when its tokenizer's offset mapping
   covers every character: a byte-level tokenizer (SFR-Mistral's) does, and a
   WordPiece one (BGE's) does not. Under WordPiece, whitespace at a window boundary
   (`\n`, `\t`, NBSP, CRLF) and characters the normaliser strips (a BOM, ZWSP, bidi
   marks) fall in no chunk.
2. **Verified by the masked identity (`verified_ws`).** No chunk reaches past
   `text_chars`, and the masked reassembly, right-padded with spaces to `text_chars`,
   hashes to `text_ws_sha256`. Every character outside the mask class is then at the
   same offset in both texts, so the glossary's offsets are sound. Only whitespace and
   ignorable characters that no chunk covered go unchecked. This is what verifies
   `fixed_token` under WordPiece. A whitespace change inside a chunk that alters the
   length shifts later offsets, so it fails this check. A same-length swap within the
   mask class (`\n\n` for two spaces, a space for an NBSP) passes `verified_ws` by
   design: no offset moves.
3. **Unverifiable.** Neither check passes, and the reassembly is incomplete: it has a
   gap, a length mismatch, or it is shorter than `text_chars`. Examples are chunks
   dropped by `--boilerplate drop`, a dropped last chunk, and a trailing character
   that no token covers and the mask does not list (a private-use U+F0B7 bullet, or a
   combining accent, under WordPiece). The document is refused;
   `--glossary-allow-unverifiable` renders it anyway and logs it.
4. **Mismatch.** Neither check passes and the reassembly is complete. That is a
   different text, and it is refused even with the override.

The load reports how many documents fell into each verified kind.

The other option was to read the source shard at load. It was rejected for two reasons:

- the load step would need a second input, which the workflows do not pass it;
- it would have to replay the loader exactly as the embed step ran it.

**A document with no row is refused, not given the rules glossary instead.** The file is
the record of what is attached:

- A silent fallback would load a glossary that nothing records.
- A missing row shows the file was built over a different document set.
- Rules output is still available by loading without `--glossary-file`.

A file that holds *more* documents than a load is normal: one file covers a collection
version, and the load step loads one shard at a time.

**Order and cost.** The glossary file is validated in full before any embedding file
is parsed, so a malformed file fails fast. It is then read a second time, keeping only
this load's rows. Every shard's load therefore validates the whole file twice. That is
acceptable at today's sizes; a per-shard index could remove it later.

**Provenance.** The load summary records the file under `glossary_file`: its path,
sha256, schema, `producer`, `created`, `counts`, the `file/1` stamp, the mask version,
and the counts of verified, verified_ws and unverified documents. When a manifest is
written, the same record goes into its hash-excluded `enrichment.symbol_glossary`, with
`source: glossary_file`. The backfill's report carries the same `glossary_file`
record. Without the flag, none of this is written.

`--replay` (restore) refuses `--glossary-file`. A restore puts back the archived chunks
exactly as they were recorded.

## Re-attaching to an existing index

`backfill_symbol_glossary.py --glossary-file F` is the Elasticsearch-only path, with no
re-embed. It runs in three steps:

1. **A read-only pass over the whole index.** This pass runs in a dry run too. Each
   document is reassembled from its chunks and checked against `F` exactly as the load
   checks it: verified, verified_ws, unverifiable, mismatch and missing.
2. **The replacement count.** The same pass counts the LLM glossaries that `F`'s
   rules-only rows would replace. `--apply` is refused if there are any, unless
   `--replace-llm` is given.
3. **The write.** Only after both checks pass does it write: first the mapping, then
   partial `_bulk` updates of `symbol_glossary` and the version stamp.

The existing safety rules are unchanged: a dry run by default, `--apply` together with
`--confirm-index`, one concrete index name, and resumability. `--glossary-file` cannot
be combined with `--llm-cache` or `--config`, because the file carries its own config.
