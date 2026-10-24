# Pre-registration, part 2: chunk size × overlap on a deployed documentation collection (redacted public copy)

> **Redacted copy.** The private original is kept with the outputs, off GitHub. Its sha256 is
> `3619ce0d8ea0bad3fc2765fc71ba3328fb8d3eed2d87a809143eb56383e1a793` (recorded 2026-10-23T14:23:19Z).
> The collection's name, index name, endpoint hosts and filename prefixes are replaced by neutral
> labels; the product name in the prompts is `<PRODUCT>`. Design, prompts (as templates), rules and arms
> are unchanged.

Written 2026-10-23.

What had been seen before writing: only structure, from three read-only store queries:
- 1,265 chunks, 336 pages, all public software documentation;
- live chunking: semantic, budget 512/64;
- page size: median about 3,000 characters, 110 single-chunk pages;
- the filenames.

No question had been generated, nothing retrieved or scored. Owner go (2026-10-23) to questions generated
from sampled pages, with the seed set alongside. EXPLORATORY. Part 1 is unchanged, except that its judge
prompt is fixed here (§6), as part 1 required.

Data is read in place from the tenant's loopback Elasticsearch, read-only, `[REDACTED: index name]`. The
owner asked for no copy. Only derived artifacts are written, outside the repo:
- questions;
- embeddings cached by text hash;
- results;
- a manifest of (doc_id, chunk count, sha256 of reassembled text), so drift between runs is detected.
  Drift is a logged deviation.

## 1. Page text
Each page's text is reassembled from its live chunks by start_char/end_char. Check: the spans cover each
page contiguously. Gaps or conflicts are counted, and such a page is dropped and logged.

## 2. Strata (by filename prefix, fixed now)
- **cli:** command-line tutorials
- **quickref:** quick references
- **tutorial:** tutorials
- **topic:** a family of topic pages (the stratum's private name is replaced by "topic" everywhere in this record)
- **other:** everything else.

The prefixes themselves are `[REDACTED: private]`.

## 3. Question generation
**Sampling**
- 150 pages are drawn without replacement, stratified in proportion to each stratum's page count
  (largest-remainder rounding), with seed 261023. Pages with fewer than 400 characters are not eligible.
- From each page, one passage is drawn: a window of 1,200 characters (the whole page if shorter), with its
  start uniform over the page and snapped to the next sentence start, seed 261023.

**Generator**
- Qwen3.8-27B on a local endpoint, thinking off, temperature 0, max_tokens 512, at most 4 in flight. Never
  the deployed assistant's endpoint.
- One attempt. A second attempt is made only if the answer can't be parsed.

**Prompt (template, system then user)**

System:
```
You write test questions for a help-desk search engine over <PRODUCT> documentation. Real users ask short, practical questions in their own words; they have not seen the page.
```

User:
```
Here is a passage from a <PRODUCT> documentation page.

<passage>
{passage}
</passage>

Write ONE question a <PRODUCT> user might ask that this passage answers. Rules:
- Use your own words. Do not copy any phrase of four or more words from the passage, except names of tools, commands, file formats or organisms.
- Do not mention "the passage", "this page" or "the documentation".
- The question must be answerable from the passage alone.
Then copy, exactly and character for character, the shortest span of the passage (one sentence or a part of one, at most 300 characters) that answers the question.

Answer with JSON only: {"question": "...", "answer_quote": "..."}
```

**Validation, applied before any retrieval**
- `answer_quote` must appear in the page text under the gold normalisation (NFKC, casefold, whitespace
  removed) and be at most 300 characters.
- On failure, the question is dropped and counted. No replacement draw is made.

**Echo measure**
- Echo is the longest run of shared word tokens (lowercased, `\w+`) between the question and the passage.
- Questions with echo ≥ 5 are the **high-echo** subset; the rest are **low-echo**.
- Every endpoint is reported on all questions and on the low-echo subset.

## 4. Arms
| arm | chunks |
|---|---|
| **L** | live chunks verbatim (semantic 512/64), live `symbol_glossary` field |
| **T<s>o<ov>** | fixed_token on the reassembled page text, s ∈ {128, 256, 512, 1024}, ov ∈ {0, s/8} (SFR-Embedding-Mistral tokenizer, `FixedTokenWindowChunker`) |
| **X<s>o<ov>** | the same, inside markdown sections. A section starts at a line beginning with one or more `#`; text before the first heading is a section. A short section is one chunk, never merged. |

- **Glossary:** T and X carry the rules/3 attacher glossary, with no LLM cache. L carries its live field.
  The glossary-off state is reported for every arm as a sensitivity.
- **Dense storage variants:** f32, int8, int8+r, bin and bin+r, exactly as Addendum D, on the T arms.

## 5. Pipeline and endpoints
**Pipeline:** the served path of the section-aware trial:
- BM25 top 100 and SFR dense top 100 (embedding fleet, at most 2 in flight per endpoint);
- RRF k=60, cap 0, cut to 50;
- bge-reranker-v2-m3 (at most 4 in flight);
- top 10.

**Gold:** the quote-text rule. A full hit means the chunk contains the normalised `answer_quote`. A
partial hit means a prefix or suffix of at least 60% is found. "Found" counts both.

**Endpoints:**
- primary across sizes: found within the delivered budgets b2048 and b5120 (SFR-Embedding-Mistral
  tokens of chunk text, walked down the reranked list, stopping before the first chunk that would exceed
  the budget);
- reranked@10;
- full-only;
- split-quote classes (contained / partial only / split) over the whole pool;
- storage per arm (N; f32/int8/bin GB estimates, as in D).

## 6. Part 1 judge (seed group W)
- **Judge:** Llama-4-Scout on a local endpoint, temperature 0, max_tokens 16, at most 4 in flight.
- **Pool:** the union of every arm's reranked top 10 for each W question, deduplicated by chunk text.

Prompt (template, user):
```
Question from a <PRODUCT> user:
{question}

Text from the <PRODUCT> documentation:
<text>
{chunk}
</text>

Does this text tell the user how to do what they ask, at least in part? Answer with one word: yes, partial, or no.
```

- **Unparseable answers:** counted as "no" and listed.
- **Group P:** E5, E6 and M6 keep the part-1 page gold. All the cited pages exist in the collection.

## 7. Contrasts and rule
Each contrast reports gained/lost/net, an exact two-sided McNemar test, and the split by stratum.
1. **Overlap:** `<s>o<s/8>` − `<s>o0`, for each family and size.
2. **Size:** each size − 512, same overlap, at b2048 and b5120.
3. **Sections:** X − T.
4. **Against live:** each T and X arm − L.
5. **Storage:** each variant − f32.

**Rule** (for contrasts 1 and 4):
- net ≥ +5;
- no loss at b5120;
- net gains in at least 2 strata. (This replaces an earlier trial's single-dominant-paper exclusion,
  since there is no dominant page here.)

**Reading:** no equivalence claims. 110 of 336 pages are a single live chunk, so a chunking effect can only
appear on longer pages. Results are also reported for questions from pages with more than 2,048
SFR-Embedding-Mistral tokens.

## 8. Load and outputs
- **LLM calls:** about 150 generation calls, and about 13 × (number of unique pooled chunks) judge calls.
  Their counts are logged before they start.
- **No other model calls:** not the deployed assistant's endpoint, no gateway, no store writes.
- **Outputs:** the questions file (question, quote, page, stratum, echo) and the part-1/2 results file.
  Both are kept outside the repo and go nowhere else. The public export carries ids and counts only.
