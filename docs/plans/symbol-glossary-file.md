# The glossary file: `glossary.jsonl` (#847 G1)

Status: G1 is built. The file format, its reader and writer, a builder, and attaching
from a file on two paths:

- the bulk load (`load_embeddings.py --glossary-file`);
- the Elasticsearch-only re-attach (`backfill_symbol_glossary.py --glossary-file`).

G2 (the model step that produces the file inside a workflow) and G3 (the ADR-0011
amendment and the migration note) are not built yet. No CWL or tools image takes the
new flags yet. Under ADR-0010 decision 4, no CWL declares a flag before its image
carries it.

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

**Symbols are not re-canonicalised on read.** They are stored as the canonical keys
the extractors produce. If a future canonicalisation change re-canonicalised them on
read, old files would become unreadable.

**`merged` in G1.** It is the glossary that today's attacher would use for the
document. That is the re-validated LLM glossary when the cache holds the document's
text, and the rules glossary otherwise. G1 does not merge the two; how they are
combined is G2's decision. When G2 makes `merged` a union, its version string must
still begin with `llm/` whenever it holds any LLM entry. Otherwise the backfill's
`llm/` guards misfire: they would treat an LLM glossary as rules output.

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
   `fixed_token` under WordPiece. A whitespace change *inside* a chunk shifts later
   offsets, so it fails this check too.
3. **Unverifiable.** Neither check passes, and the reassembly has a gap or a length
   mismatch, for example chunks dropped by `--boilerplate drop`. The document is
   refused; `--glossary-allow-unverifiable` renders it anyway and logs it.
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
