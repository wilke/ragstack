# Pre-registration: does the adopted chunking cost reach? (fixed 512-token windows vs boundary-respecting chunks) — DRAFT v0, 2026-11-09

STATUS: DRAFT. Not frozen. The owner decided confirmatory (2026-11-09). The freeze follows the alias
study's procedure (signed tag, private sha file) after two independent reviews. Open questions for the owner
are in §11; nothing below that depends on them is final.

## 1. Question and hypotheses

**Question.** The project is adopting the chunking used in the confirmatory chunk-boundary test and rechunk
stage 1 (owner, 2026-11-09):
- `sentence/512/0` with `size_unit tokens`;
- `size_tolerance 0.25`, so a 512-token target with a 640-token ceiling;
- `structure_cuts sections/1`;
- `min_piece 128`.

Call it **S**. The alias study retrieved with fixed 512-token windows, `fixed_token/512/0`; call that **W**.
For the alias study's 1,091 questions: does retrieval under S reach the answer as often as under W?

**Why the earlier work cannot answer it.**
- The confirmatory test and stage 1 judged passages, or retrieved only for pairs selected from W's retrieved
  windows.
- The OA chunking evaluation (`../oa-chunking-eval-2026-10/`) compared related arms on another question set.
  Its passages sat inside a single section, so no gold quote crossed a section boundary.

**Two outcome families,** each tested on its own (§4):
- **Q (judge-free):** quote@10.
- **J (judged):** found@10 with the frozen alias judge.

**Hypotheses.** These depend on the owner's choice in §11 Q2. The draft takes non-inferiority as primary:
- **HQ:** quote@10(S) − quote@10(W) > −δ. The draft uses δ = 2 points, an owner decision. If non-inferiority
  holds, superiority is tested next (hierarchical, no extra α).
- **HJ:** the same, for judged found@10.

## 2. Data (fixed before any S-side outcome exists)

- **Questions.** The alias study's 1,091 accepted records (900 balanced + 191; private). The questions,
  answers and quotes are unchanged.
  - Each record carries a `quote`, about 160 characters (range 18–300), taken from its gold chunk.
  - 1,065 of the 1,091 quotes are verbatim substrings of the gold chunk's text (drafting session, read-only
    count). The handling of the 26 others is §11 Q4.
- **W side (existing).** The alias study's committed stage-A retrieval runs, legs `<corpus>/none`, which hold
  each record's ranked chunk ids under default, norerank and BM25. The text comes from the W chunk exports.
  Nothing new is run on the W side.
- **S side (to be run).** The four bench collections of rechunk stage 1, `bench-rechunk-{base,neighbour,random1,random2}`
  (glossary none, 452,729 chunks, `../rechunk-found10-2026-11/artifacts/build-summary.json`). Run with the
  same harness settings as W: top 50 requested, top 10 read, the same three retrievers and server settings.
- **Prior S-side exposure, declared.** Rechunk stage 1 retrieved the S collections for the 218 questions of
  the confirmatory test. It computed no quote@10 and no found@10 for any of them. Those questions are re-run
  here with the rest. §11 Q5 asks whether to exclude them.

## 3. Outcomes

- **quote@10 (Q).** A question is found when the union of its top-10 chunks covers at least 90 % of the
  quote's characters, after whitespace normalisation. This is the OA evaluation's definition.
  - It is chunking-symmetric: a quote split across two retrieved chunks counts.
  - It needs no judge.
  - It credits only the gold location.
- **judged found@10 (J).** A question is found when quote@10 holds, **or** any top-10 chunk carries an
  *answers* label from the frozen alias judge.
  - This one rule applies on both sides. It replaces the alias study's gold-window shortcut, which has no
    S counterpart.
  - Lists with no quote hit are judged on demand. Already-labelled (record, chunk) pairs are reused, and so
    are labels for S chunks whose text is byte-identical to a judged W chunk of the same question (the
    content-hash rule).
  - Remaining labels come from new judging with the frozen judge: alias-judge prompts, C/E, Argon tiebreak,
    Opus drift reference. New (record, chunk) pairs are built with `build-control --any-paper`, the stage-1b
    mode.
- **Context tokens per query** (top 10, SFR tokenizer): descriptive.

## 4. Tests and families

- **Primary cell** (§11 Q1). The default retriever, pooled over the four corpus collections, with records
  clustered by paper.
- **Statistic.** The paired difference per record, S − W, in {−1, 0, +1}. The test is an exact sign-flip
  over papers (clusters of the 2 G + 1 C records), one-sided, as non-inferiority against −δ. Each family is
  tested at α = 0.05 on its own (Q and J).
- **Secondary, not tested.** Each corpus and each retriever separately; G vs C records; context tokens;
  discordant records broken down by coverage of the gold window under S.

## 5. Power

Not computed yet (§10). The W baseline quote@10 (read-only count, committed data):

| corpus | default | norerank | BM25 |
|---|---|---|---|
| base | 94.2 % | 86.8 % | 82.3 % |
| neighbour | 87.6 % | 74.1 % | 73.8 % |
| random-1 | 90.0 % | 78.8 % | 77.3 % |
| random-2 | 89.5 % | 78.9 % | 77.2 % |

The ceiling on the base corpus limits superiority, not non-inferiority. A seeded simulation, `power.py`, will
size δ and the discordance assumptions: discordance rates of 2–10 %, within-paper correlation 0–0.5.

## 6. Procedure

1. Freeze.
2. Run the S-side retrieval for 1,091 records × 3 retrievers × 4 corpora, with the `rechunk` mode extended
   to all records (§10).
3. Compute quote@10 on both sides, using one committed function applied to both.
4. Judge on demand for J: one gateway notice, OpenChia's ack, a hard stop on any provider notice.
5. Run the report once, with the frozen decision wording.

## 7. Gates (a third answer, as in the earlier preregs)

- Any retrieval leg with failed calls or a fired depth or reranker gate.
- An S collection whose census differs from `build-summary.json`.
- Unjudged pairs above 5 % for J.
- Label-table integrity: merged tables may only add labels (the stage-1b guard).

## 8. Threats to validity

- quote@10 credits only the gold location. J credits answers anywhere but inherits single-passage judging,
  which the confirmatory test showed penalises answers split across S chunks.
- The W runs date from 2026-11-04 and the S runs will be later. Same server settings; tools image and API
  versions to be recorded.
- 19 documents chunk differently in the image than in the host export (rechunk DEVIATIONS D-5). The S side
  uses the collections as built.

## 9. Freeze and deviations

As in the alias boundary-confirm prereg: two reviews, the owner's go, then `sha256sum` into a private file,
then a signed annotated tag, then the run. After the freeze, any change is a dated `DEVIATIONS.md` entry.

## 10. Code needed (small)

- `rechunk` mode: run all accepted records, not only the label table's. This needs a new option, so that
  stage 1's behaviour is unchanged.
- `quote_cover(quote, chunk_texts)`: committed, tested, and the same function for W and S.
- A J on-demand builder: the lists without a quote hit, the reuse of existing labels by key and content hash,
  and the new pairs for `build-control --any-paper`.
- The report: the paired sign-flip over papers, the families, the gates and the decision wording.

## 11. Owner decisions (2026-11-09)

| question | owner's answer |
|---|---|
| Q1: primary cell | **default retriever, pooled over the 4 corpora**, records clustered by paper |
| Q2: hypothesis | **non-inferiority, δ = 2 points, then superiority** (hierarchical, no extra α); δ is confirmed after the power simulation |
| Q3: J judging scope | **all three retrievers** (default, norerank and BM25 are all judged; J's primary is still the default retriever) |
| Q4: 26 non-verbatim quotes | **excluded from Q**, kept in J |
| Q5: 218 records retrieved on S in stage 1 | **kept** (no S-side outcome was ever computed for them; declared in §2) |
