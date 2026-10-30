# Deviations from the frozen pre-registration: glossary-model benchmark, round 2

The pre-registration is frozen at commit 9e109c3 (PREREG-glossbench-round2.md, sha256
`39c579e1bc3aa7d80bffc6a93ef36157ce4c41c28ac3f37c939b6a5953cd4778`, frozen 2026-10-30T02:19:33Z; merge
dedc990). The file is never edited. Every change after the freeze is recorded here, dated, with what was
known when it was made.

## D-1, 2026-10-30T02:27:02Z: the set-P pilot is 100 records, not about 50

**What changes.** §3.4 (c) and the worked example in §3.5. The frozen text reads: *"About **50** records
from **3** papers (`--pilot 50 --papers 3`, papers seeded `<seed>|pilot`) are drafted, checked and sent
through the adapter before full set-P drafting"* (§3.4 c) and *"At n = 50 judged pilot records the rule
admits at most **2** rejects (upper bound .135; 3 of 50 gives .162)"* (§3.5). Under this entry the pilot
batch is drafted at **100 records** over **5** papers (`--pilot 100 --papers 5`, owner decision 2026-10-30,
recorded in `preflight-draft-P-pilot.json` under `plan.pilot`), and the papers-OC owner
judges **all 100** (every verdict `basis: sampled`, `sample.n` 100). The promotion condition is
unchanged: the Wilson 95% upper bound of the judged error rate must be ≤ 0.15, with at least 30 judged
records. The pilot's records remain data for no hypothesis. §3.4 (b), the judged sample of 50 for the
full set P, is not touched.

**The decision rule, stated so it cannot be read two ways.** 8 of 100 sits at the threshold (upper bound
0.149981), so the rule is fixed here before any verdict exists:

- the bound is computed **from the raw counts** (`n`, rejects) as the partner's verdict file gives them,
  with the two-sided z = **1.959963984540054** (`glossbench_r2_draft.wilson`, no continuity correction,
  no rounding) — never from a bound the partner reports (the partner quotes it to 4 decimals, 0.1500,
  beside the raw count; the 4-decimal figure is not the input);
- the condition is met **iff bound ≤ 0.15 at full float precision**, and n ≥ 30 (z = 1.96 gives
  0.149983 at 8 of 100, the same verdict, so the choice of z does not decide any count);
- the bound is recorded **to 6 decimals** in the §3.5 addendum, beside n, rejects and seed.

`import-partner-verdicts` on a pilot writes this as `promotion` in `partner-verdicts-P-pilot.json`
(`z`, `threshold`, `min_judged`, `upper_bound`, `upper_bound_6dp`, `met`; `promotion_condition`, with
the boundary test at exactly 8/100 → met and 9/100 → not met). The addendum quotes that block; it is not
re-derived by hand.

**Why.** After the freeze, the papers-OC owner pointed out that the bar and the sample size had not been
checked against each other. With the frozen rule:

| judged | rejects allowed | Wilson 95% upper bound at that count | first failing count |
|---|---|---|---|
| 30 | 0 | 0 → 0.113513 | 1 → 0.166704 |
| 50 | 2 | 2 → 0.134601 | 3 → 0.162171 |
| 100 | 8 | 8 → 0.149981 | 9 → 0.162262 |

(z = 1.959963984540054, as `glossbench_r2_draft.wilson` computes it; the same rows are asserted in
`test_promotion_condition_at_the_boundary`.)

At 50 judged records, an observed error rate of 6% fails. The outcome would then depend mostly on chance in
a small sample, not on the set's quality. At 100 records the same threshold tests the set rather than the
sample. The papers-OC owner offered to judge 100. The threshold itself, the 30-record floor, the hypotheses
added on promotion, and the "fail → replication, no further option" rule are all unchanged.

**What was known.** Nothing from round 2 had been run. No paper had been sampled, no record drafted, no
gateway call made, and no partner verdict existed. The owner chose this option on 2026-10-30. The other
options were keeping 50 records, and changing to a point-estimate rule; the latter was rejected because it
loosens the bar after the freeze.

**Effect on the design.** Drafting cost: about 100 pilot drafts instead of about 50, roughly +0.1 M gateway
input tokens (the plan's step 8b budgets ~0.1 M for 50). Judging cost: the partner's. The pilot is still
excluded from every hypothesis set. Code: the `promotion` block above is the one addition to
`import-partner-verdicts`; it computes the frozen rule and adds no choice. Every P run is at or after
the commit that carries it (§3.4 pins P runs at or after `b5251db`; this is later).

## D-2, 2026-10-30T03:08:47Z: the code pin moves from `0a7a7f3` to `d87a02e` for post-freeze tooling (#870)

**What changes.** Prereg §9 pins every run to *one* `main` commit at or after `b5251db`, in a clean
worktree. Round 2 ran at two: the chunk exports, the pure and empty arm files, the dry-run backfills and
the three pool builds (02:41–02:58Z) at **`0a7a7f3`** (merge of #869, D-1); the frontier batch of D-4 and
the reranker test's preparation (from 03:09Z) at **`d87a02e`**, the merge of **#870**. Both trees were
clean (`dirty false, untracked 0`; every output's provenance block says so).

**What #870 adds, and why it was needed after the freeze.** Tooling only; no hypothesis, set, arm,
measure, threshold or test of either frozen prereg changed, and no analysis code changed:

- `glossary_file.py subset` / `shard-subset --keep|--drop IDS` — a glossary file or a loader shard
  restricted to, or stripped of, a list of document ids, rows copied unchanged. Needed to hand the frontier
  step the **eval papers only** (prereg §4, D2) and to build the `qwen36p`-minus-eval-papers file that the
  stated `frontierp` mixture (§4, *frontier scope*) is assembled from.
- `export_chunks.py --with-glossary` — a `dump-*` export that carries the stored glossary field, for the
  reranker test's set-P gold and the glossary-boost probe candidates (reranker prereg §3). The default
  `chunks-*` export still cannot request the glossary (its client sends its own `_source` list).
- a reader for the part-4 step-7 result, the reranker test's pre-committed re-score (reranker prereg
  §12 D3); see `../reranker-test-2026-11/DEVIATIONS.md` D-1 for what it can and cannot read.
- `found10.py`: the API credential is sent as an `Authorization` header (`api.auth_header`) because the
  bench API refuses the query-string form (`/v1/config` answered 403 at go/no-go 5); the live census gets
  its own client so the credential reaches the API host only (review commit `981606c`).

**What was known.** The pool manifests (D-5's counts) existed; no frontier call, no draft, no retrieval
call and no partner verdict existed. Nothing in #870 reads a pool, a stratum or a reach count.

**Effect on the design.** None on any number: the pool builds are not re-run at `d87a02e` (they would
have to be, had anything in `_glossbench_r2.py` or `glossbench_r2_pool.py` changed; nothing did). The
record quotes both pins and says which step ran at which.

## D-3, 2026-10-30T02:51:56Z: set P's `in_set` source is the partner's pairs file; the R1 exclusion is mapped by PMC id

**What changes.** Two inputs to the set-P pool build (prereg §3.4, §3.1 step 1) that the frozen text
names by role but could not name by file:

1. **`fields.in_set_records`** is the partner's **pairs** file (`pairs.dedup2-261022.jsonl`, 65 rows,
   sha256 `f8d3b7f15144ed4ba23b2519751c2d9860c11acf131aadfddf75c6988eeebef5`), not one of the 155-record
   files. The prereg says `in_set` "is computed from the held set's records" and asserts 13 covered / 13
   uncovered. The three 155-record files carry **no `field` key** (their keys are `record_id, pair, kind,
   origin, query, gold_chunk_ids[, gold_rule]`); fed to the loader, one gives a split of 0 / 26 and is
   refused. The pairs files (65 rows each; three variants, all `13 / 13`, `records_without_key 0`,
   `outside_list 0`) are the partner's record of which binding field each pair asks about, and the
   155-record set is built from them by the `pair` key. The pool header and the manifest record the sha.
2. **`exclude_papers`** (the papers of the partner's R1 gold) is derived from the 155-record set's
   **7** source papers, mapped live collection → bench copy **by PMC id through the bench document map**.
   The 155-record files point at live-collection chunk ids, which do not exist in the bench copy
   (0 of 14,456 resolve). Check before use: the mapped titles agree for **7 of 7** papers, and
   **138 of 155** pair quotes occur verbatim in the mapped bench document (the other 17 are the quotes the
   partner's own 60 % boundary rule exists for). The pairs file names **9** papers; the exclusion follows
   the R1 gold, which is the 155-record set and its 7, as the prereg says. The manifest records
   `excluded_papers 7`, `eligible_papers 253` of 275.

**What was known.** Nothing from set P existed: no pool, no draft, no pilot. The set-O and set-A pools
had been built (02:49Z) and their dry gate readings were known (12 and 3; D-5). Neither input depends
on any of that.

**Effect on the design.** The 13 / 13 assertion is met from the file that actually carries the key; the
run would otherwise have stopped (`in_set` split other than 13 / 13 is a hard stop, §8). The exclusion
is the one the prereg intends, with its mapping checked and counted.

## D-4, 2026-10-30T03:23:17Z: the frontier batch ran on set O only, truncated at `max_tokens`, and stopped A and P

**What ran.** Prereg §4's `frontierp` step (GPT-5.5, pure, round 1's prompt and settings: thinking
**unset**, repair 0, cut-off retry `split`, `max_tokens` **4096**, windows 80,000 × ≤ 4, timeout 300 s,
seed 20261028, `fallback_model ""`, concurrency 4, no Authorization header; expected version string
`llm/2:GPT-5.5:dcb924271b1e:w80000x4:t4096:ku+ed9751a17:r0`, prompt hash round 1's) on the **set-O eval
papers** (266 OA-1000 units; 240 float units under `--llm-min-chars 1500` were skipped, **26** documents
went to the model), at `d87a02e` (D-2), 2026-10-30 **03:13:23Z → 03:23:17Z**. The `/v1/models` probe
listed the exact id. Our step's summary: **58 calls**, **9 of 26 documents degraded** (`cut_off`,
unresolved after the split retry; 16 cut-off windows), `with_llm` 17, 26 kept entries, served id
recorded per call, no visible reasoning content (`reasoning_seen 0`); the degraded documents are spread
over the whole window, not a burst. Round 1's Qwen3.6 proposed 634 entries on the same 26 documents with
0 cut-off windows (size notice, computed offline from the round-1 file).

**The gateway owner's audit, external and not re-measured here** (the partner operates the gateway):
58 calls, 03:13:45Z → 03:22:50Z, every outcome `ok`; served model **`gpt-5.5-2026-04-24`** on all 58,
no substitution; **330,236 tokens in, 181,266 out**; **30 of the 58 calls returned exactly 4,096 output
tokens** (median 4,096, minimum 421). Those 30 were cut at the cap. The `max_tokens` cap reached the
provider on every call.

**What this means.** The GPT-5.5 glossaries of round 2 are **truncated outputs, not a measure of GPT-5.5
glossary quality**. Whether hidden reasoning is spent inside the 4,096 is **unverified**: the provider
reports `usage.completion_tokens_details.reasoning_tokens`, and our step did not record it; the
hypothesis that it does (the drafter note of §3.2 assumes ~1,024 hidden tokens for a different model) is
consistent with 30 of 58 calls hitting the cap and with no visible reasoning content, and is not tested.
Round 1's three local models ran the same prompt at the same cap with ≤ 2 degraded documents per corpus.

**Stop.** The stop rule the coordinator set in the batch brief before the batch started — more than 2
degraded documents on a set stops the batch before the next set — fired at 9. **Sets A and P were not run; no `--reuse` rerun, no
`rederive`, no merge and no `final` pool reading were made.** The raw set-O output is kept privately
(sha256 `839ff301…`); it enters no arm file and no index.

**What was known.** The dry gate readings of all three sets (D-5); no draft, no retrieval call. The stop
rule does not read them. Gateway use against our budget pause (prereg §8: 5.0 M in / 0.75 M out / 4,000
calls): 0.33 M / 0.18 M / 58.

**Effect on the design.** `frontierp` is **absent** on every set, so H2 is GATE-NOT-EVALUABLE (prereg §8:
"`frontierp` if the gateway closes before its batch completes" — here the batch was stopped by us on
its failure rule, the same consequence) and would enter Holm at p = 1.0 had the family been evaluated.
The pool's `final` reading was never taken; D-5 shows it did not need to be.

## D-5, 2026-10-30 (after the D-4 stop; time not logged): the reach gate is settled by an upper bound, and round 2 stops

**What the frozen text says.** Prereg §3.1 step 4: S1 must hold ≥ **150** chunks per corpus, read `dry`
without the `frontierp` file and `final` with it; below 150 the corpus's retrieval leg is
GATE-NOT-EVALUABLE. §8 repeats it. The plan (step 6) expected the `final` reading to decide.

**What the dry readings showed** (pool manifests, `0a7a7f3`, clean; `artifacts/reach-r2.json`):

| set | value-statement chunks of the 25 eval papers | S1 (dry) | S2 | gate |
|---|---|---|---|---|
| O (OA-1000) | **144** | 12 | 132 | GATE-NOT-EVALUABLE |
| A (ASM-1000) | **119** | 3 | 116 | GATE-NOT-EVALUABLE |
| P (papers-OC) | **72** | 12 | 60 | GATE-NOT-EVALUABLE |

**The upper bound.** S1 is, by §3.1 step 3, a subset of the value-statement chunks of the eval papers;
every arm, `frontierp` included, can only move chunks from S2 to S1 within that set. S1 is therefore at
most 144, 119 and 72 — **below 150 on every set under any arm**. The `final` reading cannot change the
verdict, so it is settled by arithmetic without the frontier file, and the frontier batch of D-4 was
not needed for the gate.

**What this is.** A **design error that the frozen text did not catch**: with `n_papers` 25 and
`min_vs_chunks` 3, the sample's value-statement chunks number 72–144, so the gate of 150 was
**unattainable for every set by construction of the sample**, whatever the glossaries did. It was
**knowable before freezing from a count we did not run** — the value-statement chunks per paper, which
needs no glossary and no model call (`glossbench_r2_pool.py build` without arm files would have printed
it). The gate was set from round 1's chunk-level reach on ASM-1000 (3–7 of 27,418) and from the planning
table's 500-record target; it was never checked against the number of value-statement chunks 25 papers
hold. The eligible-paper counts in the manifests (papers with ≥ 3 value-statement chunks: 384 of 1,000 on
OA-1000 by `path_root`, **38** of 1,000 on ASM-1000; on papers-OC 226 of 275 have any value-statement
chunk, and the field mode's own eligibility is 253) say the same thing from the other side.

**Owner decision, 2026-10-30: stop round 2 at the gate.** No drafting (steps 7–8), no verifier pass, no
set-P pilot, no owner spot-check, no store swap, no retrieval run, no proxy, no gold sample, no judge
pass, no promotion addendum. Any later round is its own pre-registration; nothing of it is written here.

**Why stop rather than report the proxy and precision legs.** The frozen text allows "only the proxy and
precision are reported" on a gated corpus (§3.1 step 4). Both need drafted records (M2) or a gold sample
drawn from eval papers under every arm including `frontierp` (§6), and the frontier arm is absent with
truncated output (D-4). Running the drafter and three judges to report two secondary legs on a round
whose confirmatory family cannot be evaluated on any set, against a frontier arm that is not a measure
of the frontier model, would spend the partner's gateway and the judges' budget on figures no decision
would rest on. The owner chose not to.

**What was known.** The three dry gate readings and the per-arm reach counts of the manifests; the D-4
outcome. No record, no retrieval figure, no proxy figure, no precision figure existed; no glossary
condition was compared on any set. The decision is conditioned on counts that no arm can change and on a
batch failure, not on any outcome of any arm.

**Effect on the design.** The record (`RESULTS.md`) reports the reach table and the frontier-batch
outcome as descriptive counts and states that **no confirmatory hypothesis was tested**; H1–H4 are
GATE-NOT-EVALUABLE on every set; proxy, precision, S3, set P, R1 and the pilot are **absent**.
