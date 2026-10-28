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
| `created` | ISO-8601 UTC. |
| `counts` | `documents`, `with_llm`, `degraded` and `merged_entries`. They must equal what the rows hold, so a truncated or spliced file is refused. |
| `config` | The `GlossaryConfig` the producer used. The load renders with this config. An unknown key or a wrongly typed value is refused. A missing key takes the default. |

**Every other line, one document.**

| key | value |
|---|---|
| `kind` | `"document"` |
| `doc_id` | The document id. It is unique in the file. |
| `text_sha256` | `content_key(text)`: sha256 of the UTF-8 text with outer whitespace stripped. This is the LLM cache's key. |
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

**Symbols are not re-canonicalised on read.** They are stored as the canonical keys
the extractors produce. If a future canonicalisation change re-canonicalised them on
read, old files would become unreadable.

**`merged` in G1.** It is the glossary that today's attacher would use for the
document. That is the re-validated LLM glossary when the cache holds the document's
text, and the rules glossary otherwise. G1 does not merge the two; how they are
combined is G2's decision.

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
already does (`ingestion/reassembly.py`). The chunkers slice `content` from the
document's own spans and cover the whole document, so a complete set of chunks
reassembles the text exactly. The tests check this for the recursive, sentence and word
chunkers. The two failure cases are handled differently:

- **Unverifiable.** Chunks were dropped (`--boilerplate drop`), or a chunk's length
  disagrees with its offsets. The reassembly reports a gap or a mismatch, and the
  document is refused. `--glossary-allow-unverifiable` renders it anyway and logs it.
- **Mismatch.** The reassembly is complete but hashes differently. That is a different
  text, and it is refused even with the override. One case that cannot be detected: a
  dropped *last* chunk leaves no gap, so it reads as a mismatch, which errs on the side
  of refusing.

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

`--replay` (restore) refuses `--glossary-file`. A restore puts back the archived chunks
exactly as they were recorded.

## Re-attaching to an existing index

`backfill_symbol_glossary.py --glossary-file F` is the Elasticsearch-only path, with no
re-embed. It runs in three steps:

1. **A read-only pass over the whole index.** This pass runs in a dry run too. Each
   document is reassembled from its chunks and checked against `F` under the same three
   rules as the load: mismatch, missing and unverifiable.
2. **The replacement count.** The same pass counts the LLM glossaries that `F`'s
   rules-only rows would replace. `--apply` is refused if there are any, unless
   `--replace-llm` is given.
3. **The write.** Only after both checks pass does it write: first the mapping, then
   partial `_bulk` updates of `symbol_glossary` and the version stamp.

The existing safety rules are unchanged: a dry run by default, `--apply` together with
`--confirm-index`, one concrete index name, and resumability. `--glossary-file` cannot
be combined with `--llm-cache` or `--config`, because the file carries its own config.
