# Pre-registration, glossary attachment benchmark round 3

**Status: FROZEN `43637555d234922323655ca6f51216ca08c39596422f53d2d5437419f10d3765` at 2026-10-30T15:34:08Z
on the owner's go (commit a27b581).** The prereg file is unchanged since that commit and is never edited;
later changes go to `DEVIATIONS.md`, except the set-P promotion addendum under its pre-stated rule. At the
freeze, no eval paper had been sampled for drafting, no question generated and no gateway call made; the
offline dry-run counts in the prereg were the only round-3 data seen.

**Run: COMPLETE, 2026-10-30** (retrieval and analysis at `6b434e8`, clean). The record is
[`../RESULTS.md`](../RESULTS.md). Family F on set O: H1 **not confirmed** (power .90 at the planned
effect); H2 and H3 **GATE-NOT-EVALUABLE**. Set A ran as the descriptive leg, with the D-6 caveat. Set P
stopped at its pilot and was not promoted (D-1), so no promotion addendum is appended here. Deviations
D-1 to D-6 are in [`../DEVIATIONS.md`](../DEVIATIONS.md). The counts-only artifact is in
[`../artifacts/`](../artifacts/).

| file | tier | what |
|---|---|---|
| [PREREG-glossbench-round3.md](PREREG-glossbench-round3.md) | record (frozen on the owner's go) | the design: hypotheses, sets, arms, measures, analysis, stops, code pin, decisions |
| [PLAN-round3.md](PLAN-round3.md) | **lab book** (dated, superseded-not-deleted) | ordered steps, owners, budget estimates, timeline — not a design constraint |
| [R3-COUNTS.md](R3-COUNTS.md), [count_attachment.py](count_attachment.py), [counts.json](counts.json), [sampling_power.py](sampling_power.py), [sampling_power.json](sampling_power.json), [stratified_design.json](stratified_design.json) | planning aid | the offline planning counts of 2026-10-29 behind prereg §3, §4 and §7 (committed library at `d87a02e`, clean tree; counts only). `count_attachment.py` reads the private round-2 inputs from `GLOSSBENCH_R2_INPUTS` and writes `counts.json` plus the private per-paper files; `sampling_power.py` reads those per-paper files. `stratified_design.json` is the output of the stratified design pass, the source of prereg §7's table; its pass script was not kept, so the JSON stands as the record of that table. A design assumption, not a measurement; the run's own table comes from `found10_analysis.power_table_r3` |

## Freeze procedure

Round 2's procedure (`../../glossbench-round2-2026-11/prereg/README.md`), unchanged:

1. The owner says go, in writing, naming the commit of this directory the go applies to. The prereg is
   frozen **as written** — its draft header included; no edit is made to mark it frozen.
2. In a clean checkout of that commit, `sha256sum PREREG-glossbench-round3.md` and a UTC timestamp
   (`date -u +%FT%TZ`) are appended to a **private** `PREREG.sha256` kept with the outputs, off GitHub,
   **before** any eval paper is sampled, any question is generated or any gateway call is made.
3. The same sha goes into the private config (`prereg.file` = the committed prereg, `prereg.sha256`): the
   drafter refuses to start on a mismatch (`glossbench_r2_draft.py`, hard stop `prereg_sha_mismatch`,
   exit 1, checked at every stage and resume).
4. One commit records the freeze **in this README only**: the status line becomes `FROZEN <sha> at <UTC>
   on the owner's go (commit <sha of the frozen text>)`. **The prereg file is not touched** — the runner
   hashes `prereg.file` whole, so a note at its top would make the committed file fail its own check.
   The prereg's sha256 on `main` therefore equals the recorded sha for as long as the record stands. Any
   later change is a `DEVIATIONS.md` entry; the one pre-stated exception is the set-P promotion addendum
   (below).
5. The PR carrying the freeze commit is merged; its merge commit is the freeze record's commit. The first
   eval-paper sample, question or gateway call happens after that merge.

### Preconditions

Checked by re-reading every module, flag, config key, test name and reason code the prereg names against
the tree at **`2254e4b`** (2026-10-30, the merge of #877; `734cfa6` differs from `898e34d` only under
`docs/`, and #877 touches `found10.py`, `reranker_test.py`, their tests and the new `bm25_exhaustion.py`
only — the pool, probe, drafter and analysis modules are byte-identical at `898e34d` and `2254e4b`), and by
the offline dry runs of the committed pool and probe on the real round-2 inputs (review of #874 at
`bc9be96`; the re-run with P in fields mode at `898e34d`; counts only, no text, no model, store, API or
gateway call). The v3 check re-read every count the prereg quotes against the private manifests named by
sha256 in precondition 3 (gate, strata, eligibility, reach, jobs per field, probe candidates, arm shas,
`citable true` at `898e34d`): all agree:

| # | precondition | state |
|---|---|---|
| 1 | **#874** (`exp/r3-attach`): `rederive --attach` and the `+attach=` mark (`glossary_file.py`); the stamp slot in `found10.py` (`split_stamp_attach`, `stamp_allowed`, `check_arm_file_attach`) and `--probe-max-glossaries`; the round-3 family in `found10_analysis.py` (`analyse_r3`, selected by `analysis.round: 3`); `glossbench_r3_pool.py` (populations V / M_num, eligibility, exclusions, quotas, gold-differs gate, arm-pair identity); the drafter's M_num prompt and `symbol_is_the_quantity`; the six review guards | **met**: merge **`898e34d`** (commits `1390051`, `bc9be96`, `cc955ea`), 2026-10-30 |
| 2 | **Offline dry run** of `glossbench_r3_pool.py build --dry --corpus-reach` on O, A, P with the real chunk exports and the round-2 arm files (value) plus their `rederive --attach mention` copies; render parity of the mention files against `for_chunk` with the rule flipped on every chunk of every corpus (`mismatch 0`, `value_not_subset 0`) | **met**, 2026-10-30, at `bc9be96` (clean, `citable true`): eligible O 288 / A 28 / P 109; gold differs O **151** (V 72, M_num 79) / A **56** (16, 40) / P 116 (56, 60) **in open mode — superseded for P by precondition 3's fields-mode reading, 80**; every gate `EVALUABLE`; chunk counts 32,765 / 27,418 / 14,456 `match true`; `arm_pair_identity ok`. Prereg §3.1 step 5 quotes O and A from here and P from precondition 3 |
| 3 | **Offline P dry build in fields mode** (the dry run of 2 built P in open mode) and the **boost probe's offline candidate count** per arm × corpus (`glossary_boost_probe.pick_probes` on the chunk export plus the rendered fields), so the `declared` cells are known before the freeze | **met**, 2026-10-30, at **`898e34d`** (clean, `citable true`; counts only). **P, fields mode:** 7 excluded; 136 dense, 27 without a glossary → **109** eligible; 25 sampled; **300 jobs** (150 per stratum; V plan 4 short); `in_set` 13 / 13; sampled chunks **V 108 / M_num 112**; **gold differs 80 (V 42 / M_num 38)** against the gate of 60 → attainable; realised jobs per field: 10 new-ground fields 255 (51, 44, 42, 35, 30, 16, 14, 11, 9, 3), 12 `in_set` 41, 4 constants 4, 3 fields 0; sampled reach `value` / `mention`: V 36 / 57, M_num 0 / 38. **O 151 (72 / 79) of 288 and A 56 (16 / 40) of 28 re-confirmed.** Private manifests: `pool-manifest-P.json` sha256 `3369a867…`, `-O` `30822bc1…`, `-A` `32a23e2c…`. **Boost probe**, qualifying chunks (minimum 3) per `value` / `mention` / `mention-shuf` / `rules-value` / `rules-mention`: **O 0 / 2 / 2 / 0 / 0; A 0 / 18 / 18 / 0 / 0; P 0 / 1 / 1 / 0 / 0** — `not_testable` in every cell but A × mention; private `probe-candidates` file sha256 `e4966c6c…`. Prereg §3.1 step 5 and §5 note 2 quote these and pre-declare the `declared` cells |
| 4 | **Read-only BM25 exhaustion verifier** for prereg §5 note 3: **#877** (`exp/found10-exhaustion`): `python/scripts/eval/bm25_exhaustion.py` + `found10._verify_exhaustion`, one `_count` per short `bm25` list with the store's own `_build_query`, verdicts `exhausted_verified` (complete, not gated) / `clamp` (hard stop) / `under_served` (stays gated) / `query_mismatch` (hard stop: the rebuilt query is not the server's), `corpora.<c>.bm25_scope` for the exact tenant scope — refused unless it can see the tenant `es_id_prefix` names — `census.es_url`; in `reranker_test.py` the same verifier behind `--es-url` / `--es-index` / `--bm25-tenant` / `--bm25-extra-tenant`; the prereg specifies it as written and requires `bm25_scope` on every leg | **met**: merge **`2254e4b`** (commits `3dc7fdb`, `83b8d90`, `0ec8f1c`, `40c16c3`), 2026-10-30. The v2 "without it" branch is removed from the prereg at v3 because its condition cannot occur |
| 5 | Owner decisions D1–D14 folded in (prereg §11); the papers-OC owner's input and its user's volume approval of 2026-10-30 (prereg §3.3, §3.4, §8) | **met** (this directory) |
| 6 | **Live-ops plan** for every bench-store touch (`PLAN-round3.md` step 5) | **a run-time precondition, not a freeze precondition**: it precedes the first `swap.sh` apply (plan step 12), not the freeze. Round 2 froze the same way (its `PLAN-round2.md` step 3b was *pending; precedes step 5* at the freeze commit `2e60fb3`). The plan touches bench copies only and no number in the prereg depends on it |
| 7 | **Gate checked against its sample; bar checked against its pilot** (round 2's D-5 and D-1 lessons) | **met**, stated in prereg §12: every gold-differs gate read on the sampled set this design draws, in its build mode, with its seeds (margins 51 / 16 / 20; per-stratum 32 on O); the promotion bar read against the 100-record pilot (8 of 100 meets it, 9 does not). The gate is an evaluability floor, not a power guarantee; `power.realised` and `mde_split` are printed beside every result |

The planning counts of `R3-COUNTS.md` were made at `d87a02e`; the first dry run at `bc9be96`; the
fields-mode re-run and the probe counts at `898e34d`. None ran retrieval; nothing in #874 or #877 reads a
retrieval result, a pool, a stratum or a rendered field, so the `898e34d` manifests stand under the
`2254e4b` pin. One operational note for the private config: the round-3 example
(`glossbench/examples/found10-config-r3.example.json`) carries `es_id_prefix` but no `bm25_scope`; the
prereg requires `bm25_scope` on every corpus, and `found10.normalise_config` refuses a scope that cannot see
the `es_id_prefix` tenant. The reranker test's record (#876, merge `734cfa6`) is cited by prereg §5 notes 1–3 for
three lessons (the reranker reads the glossary field, so attachment changes its input too; short `bm25`
lists can be exhaustion, D-5; the boost probe can be structurally `not_testable`, D-6) and for one
exploratory reading (on papers-OC, ordering rather than the pool limited the reranked arm); it adds no
hypothesis.

After the freeze, exactly one addendum may be appended to this README: the set-P promotion decision of
prereg §3.5, *promoted* or *not promoted*, quoting the imported pilot verdict summary (n, rejects, seed,
6-decimal Wilson bound). Its terms are fixed in the prereg; nothing else is ever appended here.

## What changed from v2 to v3 (2026-10-30, same day) — freeze-readiness check against `main` at `2254e4b`

Text only; no hypothesis, arm, measure, test, threshold, set, seed or decision changed:

- **#877 is merged** (`2254e4b`; commits `3dc7fdb`, `83b8d90`, `0ec8f1c`, `40c16c3`), so precondition 4
  is met and every "in review" / "if it is not merged" clause of v2 (header, §5 note 3, §8, §9) now states
  the merge; the "without it" branch is gone. The pin is **`2254e4b`** (was "at or after `898e34d`, and
  after #877's merge if merged").
- **§5 note 3 carries the two review commits #877 gained after v2**: the fourth verdict
  **`query_mismatch`** (count below the served length under either scope: the rebuilt query, filters or
  index are not the server's; a hard stop, never evidence), added to §8's hard stops; and the refusal of a
  `bm25_scope` under which the tenant `es_id_prefix` names is not readable. The eligibility rule is stated
  as the code states it (no graph leg, `passthrough`, one collection) and the fixed request is named as
  meeting it. §9 lists #877's modules, flags and tests in the pin table.
- **§12 added**: the gate-against-sample and bar-against-pilot check stated explicitly (round 2's D-5 and
  D-1 lessons), with the gate named an evaluability floor rather than a power guarantee; and thirteen
  disclosed forking paths, each with the rule that bounds it (promotion, the pre-freeze visibility of the
  seeded sample, family condition, strata and sidedness, 300 vs 298, declared boost, short lists, two gold
  rules on P, failed calls, the noise-floor repeat, Holm's family, the operational decision, the design's
  dependence on round 2's counts).
- **Precondition 6 (live-ops plan) reclassified** as a run-time precondition preceding the first swap, on
  round 2's precedent; precondition 7 (gate checked against sample) added as met.
- The exclusion file of §3.1 step 2 is named private (v2 said "committed"; it names the partner's papers
  and is not in the repository).
- Every number in the prereg was re-read against the private manifests (`3369a867…`, `30822bc1…`,
  `32a23e2c…`, `e4966c6c…`; all four shas re-verified on disk), `stratified_design.json`, `R3-COUNTS.md`,
  the reranker test's `RESULTS.md` / `DEVIATIONS.md` and round 2's prereg: no discrepancy.

## What changed from v1 to v2 (2026-10-30, same day)

Substance, not wording. No hypothesis, arm, measure, threshold or seed changed.

- **Precondition 3 closed.** The P dry build in **fields mode** at `898e34d`: gold differs **80** (V 42 /
  M_num 38) on 108 / 112 sampled chunks, 300 jobs; gate 60 → attainable. **80 replaces the open-mode 116
  wherever P's gate reading is quoted** (prereg header table, §3 table, §3.1 step 5; this README; the
  plan). Why it changed: the fields-mode prefilter admits fewer chunks (220 against 284), while the
  gold-differs share per stratum is nearly unchanged (V 39 % vs 40 %, M_num 34 % vs 41 %); it is the
  mode the final P build uses, so it is the reading that counts. O and A re-confirmed at 151 / 56.
- **Boost probe pre-declared per cell** from the offline candidate counts: `not_testable` everywhere
  except A × `mention` / `mention-shuf` (18 candidates). Those cells run the probe; every other cell runs
  with the boost **declared** (`TEXT_SYMBOL_GLOSSARY_BOOST` 1, the v1.6.21 release's management
  declaration), labelled `declared`; the admin `/v1/config` read at the start and end of every leg must be
  identical (hard stop otherwise); a content-only `_count` beside a verified short list is descriptive
  behavioural evidence of boost > 0, never a gate. Reranker test D-6 is the lesson cited.
- **BM25 exhaustion rule specified as #877 writes it** (verdicts `exhausted_verified` / `clamp` /
  `under_served`; `clamp` a hard stop; `bm25_scope` set for every leg so no count is an unscoped bound;
  the per-row `exhaustion` record, never the query text). #877's merge is precondition 4; the "without
  it" branch is kept. Reranker test D-5 is the lesson cited; the reranker test's own hand verification
  could not lift the harness mark, which is exactly what the in-runner path changes.
- **The reranker test's finished record (#876, `734cfa6`) cited** in §5 note 1 for what round 3 leans on
  (attachment changes the reranker's input; on papers-OC the reranked arm's loss was ordering, not pool —
  exploratory) and explicitly for **no new hypothesis**. The D-5 / D-6 numbering in §5 notes 2–3 now
  matches the merged `DEVIATIONS.md` (v1 had them swapped).
- Draft text sha256 at v2, for the owner's go to name: see the PR; it is not a freeze and is not
  recorded here (the freeze procedure above records the frozen sha, once). Superseded by v3.

## What changed from the v0 draft (2026-10-29 → 2026-10-30)

Substance, not wording:

- **Code merged, pin named.** Every "required before freezing" clause of v0 (§9) is now a statement of
  fact with its merge commit: #874 at `898e34d`. Module, flag, key and test names are the merged code's
  (`glossbench_r3_pool.py`, not the v0 text's `glossbench_r2_pool.py build`; `split_stamp_attach`,
  `check_arm_file_attach`, `--probe-max-glossaries`, `analysis.round: 3`, `R3_HYPOTHESES`,
  `symbol_is_the_quantity` required to parse but never a reject).
- **Dry-run counts replace planning expectations** in the header, §3 and §3.1 step 5: eligible O 288 / A
  28 / P 109 (the P exclusion is the 7 R1-gold papers; the exclusion file's 8th line is a comment; 3 of
  the 7 were otherwise eligible), gold differs 151 / 56 / 116 against gates 100 / 40 / 60. The P dry run's
  open-mode caveat is stated, and the fields-mode re-run is precondition 3 (closed at v2: P reads 80).
- **298 vs 300 settled**: the committed pool prints **300** chunks with a field under `value` on O (298 in
  V, 1 in M_num, 1 outside); the round-2 manifests' 298 counted V chunks only. The prereg uses 300 and
  says why.
- **Bench collection ids and chunk counts per leg** named in §3 (D13), at the papers-OC owner's request;
  the live collections are never touched (the papers-OC live collection drifted on 2026-10-30).
- **H2's framing** carries the papers-OC owner's wording: a gain confined to M_num is the hypothesis;
  no change in V is a legitimate outcome; distractor harm in V would be a finding.
- **The reranker test's lessons** (its run of 2026-10-30; D-3 merged, D-5 / D-6 pending at the time of
  writing) are pre-declared in §5: the boost probe can be structurally `not_testable` (0 qualifying
  chunks on the papers-OC bench copy under `rules/3`) and is then `declared`, with the offline candidate
  count a freeze precondition; a short `bm25` list may be exhaustion and is verified by a read-only
  count or gates; the gold rule on P is named beside every figure (ours for the gold, the papers-OC
  owner's for its acceptance).
- **Gateway**: Gemini 2.5 Pro drafter, `max_tokens` 4096, `fallback_model ""`, ≤ 4 in flight, size notice
  per batch; the papers-OC owner's user approved ≈ 1.45 M drafting input tokens and the judging volume
  (100 pilot verdicts, ≈ 220 scripted + 50 judged); Claude only via the CLI verifier, with the papers-OC
  owner's empty-answer measurement recorded as context.
- **Promotion rule** carries round 2's D-1 wording (Wilson bound from raw counts, z = 1.959963984540054,
  full precision, 8 of 100 met / 9 not, 6 decimals in the addendum).
- **Seeds**: pool `261103|<set>`, chunks `261103|<set>|chunks|<stratum>|<paper>`, pilot `261103|P|pilot`,
  shuffle `261103|shuf`, spot `261103|spot|<set>`, analysis 261103.
- **Decisions section** replaces the separate decisions file (D1–D14; D13 collections and D14 Holm added).
