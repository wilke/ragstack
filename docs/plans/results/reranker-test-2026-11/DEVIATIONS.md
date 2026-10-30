# Deviations from the frozen pre-registration: the dedicated reranker test

The pre-registration is frozen at commit 9e109c3 (PREREG-reranker-test.md, sha256
`5dfe9e65c96add94a550a7a040d8a425c99d949194b4e5b851f26483f15ad230`, frozen 2026-10-30T02:19:33Z; merge
dedc990). The file is never edited. Every change after the freeze is recorded here, dated, with what was
known when it was made. The partner that holds the papers-OC collection is "the papers-OC owner".

**Status at the last entry: no arm of §3 has run on any set of §4.** No `pools`, `rerank` or `stats` run
has happened; no API, sidecar, embedding, store-write or gateway call was made for this test. The sha of
the frozen text was verified by hand against the README and the private `PREREG.sha256` before the
first step (prereg README, freeze procedure step 3; 2026-10-30T03:10:13Z).

## D-1, 2026-10-30: the pre-committed re-score of the part-4 step-7 result is partial — A0 and A2 are *absent*

**What the frozen text says.** §12 D3: family A's data is *both* a re-score of the step-7 private result
(clean `db97c28`; "per-record ranks already there") for **A0, A2 and A4-50** as the pre-committed check,
*and* a fresh retrieval-only run under the new harness, which is the citable family A; agreement between
the two is a free replication.

**What the file holds.** The step-7 result stores, per cell (question × arm × condition), only the
**reranked `found` verdict** at the served depth — not the first-stage lists and not the per-leg ranks the
re-score of A0 (BM25 alone, top 10) and A2 (hybrid-shallow, unreranked) would need. The reader added for
it (#870, `test_reranker_part4_rescore.py`; see D-2) therefore yields **A4-50 only**. A0 and A2 from that
file are **absent**, written as *absent*, never as passed: the pre-committed check cannot compute H1 or
H3 from the step-7 file, and it cannot replicate the fresh run on anything but the default arm.

**What was known.** No arm had run; the bm25-vs-rerank contrast had not been computed from the file
(prereg §11), and still has not. The limitation was found by reading the file's keys, not its values.

**Effect on the design.** Family A rests on the fresh retrieval-only run alone, as the prereg already
makes it ("which is the citable family A"). The agreement check is reduced to A4-50 and is reported as
such. No hypothesis, arm, measure or threshold changes.

## D-2, 2026-10-30: input-reader fixes (#871, pending) and the pin move to `d87a02e`

**Pin.** The prereg pins every run to one `main` commit at or after `48d5c73` (`b5251db` for R2-P) in a
clean tree. The test's preparation (identity-reference check, the papers-OC glossary dump, the step-8
config) ran at **`d87a02e`**, the merge of **#870** (post-freeze tooling, 2026-10-30T03:08:47Z; see round
2's `DEVIATIONS.md` D-2): `export_chunks.py --with-glossary` (the dump the set-P gold and the boost probe
read) and the part-4 re-score reader of D-1. Any arm that runs will run at or after the merge of **#871**,
which is **open, not merged**, at the time of this entry; its merge commit will be recorded here when it
exists. Nothing in #870 or #871 changes `stats`: no arm, measure, threshold, test or gate.

**What was found before `pools`, and what #871 changes.** Three input problems stopped the plan at step
8a (docs-A) and 8b (set P) before any list was captured:

1. **The identity reference could not be read.** The frozen identity gate (§8) takes part 4's own
   reranked lists as the docs-A reference (`rerank --identity-reference part4-retrieval.jsonl
   --identity-arm T512o0`). The committed loader at the freeze required 3-field keys; the part-4 writer
   (`deployed_docs_part4.ckey`) writes **5-field** keys, `retrieval|<qid>|<arm>|-|-` (3,675 rows, 735
   questions × 5 arms, T512o0 among them). The loader exited with *no retrieval rows for arm 'T512o0'*,
   so `rerank` would have stopped **before** the gate — and an absent gate makes H1–H3
   GATE-NOT-EVALUABLE on docs-A. #871 makes the loader accept exactly the part-4 form (re-built through
   `dp4.ckey`) and **refuse** any other `retrieval` key rather than skip it; the unit test builds its
   keys with `dp4.ckey`. The gate itself is unchanged.
2. **The partner-held set P could not be read as is.** The three 155-record files carry
   `record_id, pair, kind, origin, query, gold_chunk_ids[, gold_rule]` — **no quote** and **no
   paper/cluster field** — and their `gold_chunk_ids` are **live-collection ids: 0 of the 14,456
   bench-copy chunk ids resolve**. #871 adds `--questions-format partner-held --pairs PAIRS.jsonl`:
   each record is joined to its pair by `pair` (a missing pair exits); the query is the record's
   `query`; the cluster is the pair's paper locator; **gold is the pair's expected quote, resolved in the
   bench-copy dump by the partner's own quote rule** (NFKC, whitespace runs to one space, strip,
   casefold; a chunk is gold if it contains the quote or the quote's first or last ⌈0.6·len⌉
   characters). The records' live ids are ignored and counted; a record resolving to no bench chunk is
   excluded as `no_gold_chunk_in_index` and counted; a per-record `gold_rule` of `any` / `all` is kept
   and wins in `rank_of_gold`. On the held set this gives **155 records, 7 papers, 73 distinct queries,
   2 excluded** — the counts prereg §4 and §8 state. The quote rule applied here is **not** the one the
   prereg's Terms name; that is a deviation of its own, **D-3** below.
3. **docs-A questions have no `page` key.** The plan's `--cluster-field page` exits; the page is the
   question's `doc_id` (208 distinct among the 735 `status == valid` questions; 809 rows in the file).
   This is a flag choice (`--set-field status --set-value valid`, cluster `doc_id`), not a code change.

**What was known.** No arm had run on any set; no list had been captured; no contrast had been computed.
The problems were found from the files' keys and from a count of resolvable ids, not from any retrieval
result.

**Effect on the design.** None on any hypothesis, arm, measure, threshold or test. The docs-A identity
gate becomes runnable as pre-registered; set P becomes readable with its gold built the partner's way and
its exclusions counted. Until #871 is merged, no arm runs, and this file records that nothing has.

## D-3, 2026-10-30T03:33:07Z: set P's gold uses the partner's actual quote rule, not the normalisation the Terms name

**What the frozen text says.** Terms, *found@k*: "**Gold** = the chunks containing the record's quote
under the gold normalisation (NFKC, casefold, whitespace removed; the parts 2–4 rule and the partner's
rule; `reranker_test.build_gold` against the full chunk dump)." One normalisation, *all whitespace
removed*, attributed to both the parts 2–4 harness and the partner.

**What it should say.** The two rules are not the same. The partner's actual rule — in its reference code,
and the matching round 1's step 8 was scored with (lab book Part 3) — is **NFKC, each whitespace run
collapsed to one space, strip, casefold**, applied to quote and chunk alike; it is a **containment** test,
and a quote cut at a chunk boundary matches on its **first or last ⌈0.6·len⌉ characters**. It has no
minimum quote length. The parts 2–4 rule removes all whitespace and has no 60 % clause. The frozen
sentence conflated them.

**Decision (owner, 2026-10-30).** **Set P uses the partner's actual rule**, as #871 implements it in
`--questions-format partner-held` (D-2 item 2). The frozen text's stated intent was to match the partner's
measurement, and only the partner's rule reproduces round 1's step-8 figures on the same records.
**docs-A keeps the parts 2–4 rule as frozen** (`build_gold`, whitespace removed): the part-4 question set
was built and graded under it, and family A is confirmatory. Every set-P figure names the rule beside it.

**Effect on real data** (counts only, from the #871 review of the held set against the bench-copy dump):

- gold differs on **16 of 155** records. The frozen rule's gold is a **strict superset** of the partner's
  on every one of them: **+1 chunk on 14** records, **+10 on 2** (whitespace removal lets a quote match
  across a line break or a hyphenation that the partner's rule keeps as a space);
- exclusions are **unchanged: 2 of 155** (`no_gold_chunk_in_index`, prereg §8);
- included: **153 records — 57 pairs, 73 distinct queries, 7 clusters**;
- the partner's rule has **no minimum quote length**; the shortest real normalised quote is **8**
  characters;
- **two pairs' gold spans 50 chunks across 27 documents, and 32 across 18**: a short quote the 60 %
  clause and the containment test match in many places. That is the partner's rule reproduced
  faithfully, and it is a property of its gold, not a defect of the reader. The **gold-size
  distribution** (chunks and documents per record) is reported beside every set-P result, and the
  `--gold-rule all` sensitivity (prereg §6) is reported with it.

**What was known.** No reranker arm had been run on any set; no list had been captured; no contrast had
been computed. The 16-record difference was found by building both golds from the held records and the
dump, which involves no retrieval.

**Effect on the design.** Set P is exploratory (prereg Status, §4), so no confirmatory hypothesis changes
its gold. Family A (docs-A) is untouched. Family B (the round-2 sets) would use round 2's gold rule as its
own prereg fixes it, had round 2 produced a set; it did not (round 2 `RESULTS.md`).
