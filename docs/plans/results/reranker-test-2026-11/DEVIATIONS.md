# Deviations from the frozen pre-registration: the dedicated reranker test

The pre-registration is frozen at commit 9e109c3 (PREREG-reranker-test.md, sha256
`5dfe9e65c96add94a550a7a040d8a425c99d949194b4e5b851f26483f15ad230`, frozen 2026-10-30T02:19:33Z; merge
dedc990). The file is never edited. Every change after the freeze is recorded here, dated, with what was
known when it was made. The partner that holds the papers-OC collection is "the papers-OC owner".

**Status at the last entry (D-7, 2026-10-30): the test is COMPLETE; the record is [`RESULTS.md`](RESULTS.md).**
Up to D-3 (2026-10-30T03:33:07Z) no `pools`, `rerank` or `stats` run had happened and no API,
sidecar, embedding, store-write or gateway call had been made for this test. D-4 records the one run
between D-3 and itself: docs-A `pools` in-process and a `rerank` that stopped at the identity gate after 3
reranker-sidecar calls. D-5 to D-7 record what the full run (2026-10-30T13:49–14:31Z, at `f408b7a`) met and
did not do. The sha of
the frozen text was verified by hand against the README and the private `PREREG.sha256` before the
first step (prereg README, freeze procedure step 3; 2026-10-30T03:10:13Z) and again before the full run.

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
which was **open, not merged**, when this entry was written; it merged as **`b300d0e`**
(2026-10-30T03:32:47Z), recorded here before the entry was committed. Nothing in #870 or #871 changes `stats`: no arm, measure, threshold, test or gate.

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

## D-4, 2026-10-30T03:42:38Z: the docs-A identity reference is rebuilt on the bench copy

**What happened.** Family A's first run, at 2026-10-30T03:34–03:36Z with the harness at b300d0e and a clean
tree, captured the docs-A pools in-process. Every depth from 10 to 200 was served in full; 735 questions
over 208 clusters; gold for 732, with 3 excluded under §8 (`no_gold_chunk_in_index`). The run then
stopped at the identity gate, as the prereg requires: all 3 probes differed at rank 1. It made three
sidecar calls. No test arm was reranked, and no list, contrast or hypothesis was computed.

**Why it failed.** The reference named in the prereg (§8, with §12 D3: part 4's own reranked lists) was built
on the deployed docs-A index. The bench copy this test runs on (1,113 chunks) assigns different document
and chunk ids. The two share **0 of 50** candidate ids on every probe, and their document ids are
disjoint (336 vs 335). So the gate compared two id spaces and could never pass. That says nothing about
the harness.

**What changes.** The identity reference becomes the reranked lists that **part 4's committed retrieval
code** produces on the **bench copy**: the retrieval-only harness that round 1's step 7 ran
(`part4_glossary.py`), arm T512o0, the bench index's own glossary state (rules), the same 735 valid
questions.
- The harness gains a write-only option that saves those lists in part 4's key layout. A test proves the
  results are byte-identical with and without it.
- The code pin moves to the commit that adds that option.
- The gate's rule (offline top 10 must equal the reference list and order on 3 probes) is unchanged, and
  so is its purpose: our harness reproduces part 4's pipeline.
- Family A reruns from `pools` at the new pin. The 03:34Z outputs are kept as the failed attempt's
  record and are not reused.

**What was known.** No reranker arm had been compared on any set. The only outcomes seen were the gate's
own: rank-1 differences and the id overlap. Set P is untouched by this change.

## D-5, 2026-10-30T14:25:19Z: set P's `bm25@100` lists were short on 19 records; verified as index exhaustion; run with `--allow-short`

**What the frozen text says.** §5, *Depth read-back*: every request's served depth is recorded against the
requested one; a list shorter than requested is *under-served*; the run fails (exit 1) unless
`--allow-short`, which marks the list GATE-NOT-EVALUABLE and every arm built on it inherits the mark. "The
in-process route can tell a corpus that has fewer matching chunks (`exhausted`, allowed) from a clamp; the API
route cannot."

**What happened.** The first complete set-P `pools` pass (API route, 2026-10-30T14:22:36Z, 1,395 calls)
served `bm25@100` short on **19 of 155 records**: 23 documents on 8 records, 36 on 10, 79 on 1, over
**5 distinct queries** (three at 23, one at 36, one at 79; records sharing a query share a list). Every other list — `hybrid@10/20/50/100`, `vector@100`, and the three live reranked captures —
was served in full; 0 degraded responses; the API does not echo `top_k`. As pre-registered, the run exited 1
and no list from it became an arm. No `rerank` or `stats` ran on P at that point.

**Verification (read-only, before the rerun).** For each of the 5 queries the coordinator counted, directly
on the bench index, the documents matching the server's own `most_fields` clause (content + glossary field,
boost 1 — an upper bound on the filtered count, since filters only narrow). Served length = that count for all
five (23, 23, 23, 36, 79; `d5_exhaustion_check` in `artifacts/reranker-P-r2.json`). The lists were short
because the index holds no more matching documents: **exhaustion, not a clamp**. The verification reads
the store; it makes no retrieval call through the API and touches no arm.

**Decision (owner, 2026-10-30).** Rerun `pools` with `--allow-short`. The harness marks `bm25@100`
GATE-NOT-EVALUABLE and A0, A5-D and A9-bm25 inherit it, exactly as §5 and §8 prescribe for the API route,
which cannot distinguish the two cases; the verification is reported **descriptively** beside the mark as
"verified exhausted" and does not lift it. The rerun and its repeat served the **same 19 records at the same
lengths** as the verified pass (`same short set+lengths: True` for both). The A0 top 10 is full on every record
(every short list has ≥ 23 entries); the live A5-50 had 23–36 candidates to rerank on those 19 records.

**Effect on the design.** None on any hypothesis, arm, measure or threshold; the harness's gate rule is
applied as written. H1–H3 on P carry the reason `depth read-back: bm25@100 under-served` in their `gate` list
beside the cluster-count reasons that gate P regardless (§8: 7 clusters < 8). Family A (docs-A, in-process,
every depth served in full) is untouched.

**What was known.** The set-P pools had been captured but not reranked or scored; no set-P contrast had been
computed. The docs-A family had completed (14:20Z) before any set-P list was captured.

## D-6, 2026-10-30T14:22:36Z: the glossary-boost probe is structurally `not_testable` on P's `rules` condition; continued with `--boost-declared 1.0`

**What the frozen text says.** §3, *The glossary boost is proven, not declared*: before the first captured
list on any API-route set, `pools --backend api` runs the behavioural probe — up to 5 chunks whose stored
glossary holds a whole-word ASCII token (≥ 5 characters) that occurs in no chunk's content and in at most 10
glossaries; `not_testable` (fewer than 3 qualifying chunks) gates the boost-dependent arms unless
`--boost-declared X` is given, recorded as `declared`.

**What happened.** The first set-P start (2026-10-30T14:20:45Z) returned `not_testable`: **0 chunks qualify**
of 14,456 (241 with a glossary). The process was stopped by its recorded pid before any list was captured;
`not_testable` is not a pass. The diagnosis on the chunk dump (no retrieval): every ≥ 5-character whole-word
ASCII token in any `rules/3@file/1` glossary also occurs in some chunk's content, so the probe has nothing to
query. That is a property of the rules extractor on this corpus (it defines symbols with words the papers
use), not of the server, and it would recur on every rerun under this condition.

**Decision (owner, 2026-10-30T14:22:36Z).** Continue with **`--boost-declared 1.0`**: the server release
serving the bench copy (v1.6.21) sets `TEXT_SYMBOL_GLOSSARY_BOOST` to 1.0 by management declaration; the admin
`/v1/config` (which does not expose the boost) was read before and after every set-P pass and was identical
each time (full-config hash prefix `f85cf2e2f73c36ab`; eight reads including docs-A's). Every boost-dependent
figure on P is labelled **`declared`** (`boost_check.status declared`, `declared_label: declared
(--boost-declared), not read back`), never `pass`.

**Behavioural support, descriptive.** D-5's read-only counts give an unplanned check: on **2 of the 5**
exhaustion queries the server served more documents than match on content alone (23 served; 22 and 17 on
content only; 23 on content + glossary). Glossary-only matches were returned, so the glossary field *is*
queried on this server (boost > 0). The magnitude is not verified by this; the declared value stands as
declared.

**Effect on the design.** None on any hypothesis, arm, measure or threshold. The label `declared` is the
prereg's own third verdict for this case. Set P is exploratory, so no confirmatory figure depends on it;
on docs-A the arms are in-process and the probe is `not applicable`, as frozen.

**What was known.** No set-P list had been captured; the docs-A family had completed.

## D-7, 2026-10-30: the L replication of family A (D2) was not run — and on the docs-A bench copy it could not be one

**What the frozen text says.** §4, docs-A row: "**family A, confirmatory**; L (live chunking) as replication
(§12 D2)". §12 D2: "primary docs-A arm — T512o0; L as replication". The plan's step 4 names "Arms T512o0
(primary) and L (replication)".

**What happened.** Family A ran on **T512o0 only**. No `pools`, `rerank` or `stats` ran with `--part4-arm L`
on any day of the test, and the run log records no decision about it: the omission was found while writing
the record, after the test's last step (2026-10-30T14:31:12Z). The identity reference rebuilt in D-4 was also
written for T512o0 alone.

**Why L stays absent (read-only check, 2026-10-30T14:49:08Z, at `f408b7a`, clean).** When the omission was
noticed the coordinator opened a worktree to run L and first checked what L *is* on the bench copy. Arm L is
built from the index's own stored chunks (`deployed_docs_trial.build_arms`). The docs-A bench copy was
itself chunked at fixed-token 512 / overlap 0, so on it L and T512o0 hold the **same (doc_id, content)
multiset: 1,113 of 1,113 chunks shared**. Part 4's L was the deployed docs-A collection's *live* chunking;
no bench copy of docs-A at that chunking exists. Running L on the bench copy would therefore re-run T512o0
under `L|` ids, not replicate it on live chunking. Making a bench copy at the live chunking is a store write
outside this phase, so it was not created. Nothing was run; the worktree stayed clean. The check is in the
run log.

**Effect on the design.** Family A's confirmatory verdicts are those of the T512o0 run and are reported as
such; the L replication is written as **absent, with this reason** in `RESULTS.md`, never as agreeing. A
real L replication needs a docs-A bench copy at its live chunking first; if one is made, running L would be
a new, post-hoc run under this prereg and would be recorded as a later entry with its own pin; it is not
promised here.

**What was known.** Everything in `RESULTS.md`: the T512o0 verdicts and the set-P descriptives had been
computed when the omission was noticed and when the `build_arms` check was made.
