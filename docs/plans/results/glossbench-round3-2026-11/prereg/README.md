# Pre-registration, glossary attachment benchmark round 3

**Status: DRAFT v2 (2026-10-30), aligned with `main` at `734cfa6` (#876; the code the prereg names is
unchanged since `898e34d`, #874). Not frozen.** Freezable once precondition 4 closes (#877 merged, or the
owner's recorded decision to run without it). When the owner
gives the go, this line becomes `FROZEN <sha256> at <UTC> on the owner's go (commit <sha of the frozen
text>)`; the prereg file itself is never edited after that, not even for a "frozen" note.

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

Checked by re-reading every module, flag, config key and reason code the prereg names against the tree
at `898e34d` (2026-10-30; `734cfa6` differs from it only under `docs/`), and by the offline dry runs of
the committed pool and probe on the real round-2 inputs (review of #874 at `bc9be96`; the re-run with P
in fields mode at `898e34d`; counts only, no text, no model, store, API or gateway call):

| # | precondition | state |
|---|---|---|
| 1 | **#874** (`exp/r3-attach`): `rederive --attach` and the `+attach=` mark (`glossary_file.py`); the stamp slot in `found10.py` (`split_stamp_attach`, `stamp_allowed`, `check_arm_file_attach`) and `--probe-max-glossaries`; the round-3 family in `found10_analysis.py` (`analyse_r3`, selected by `analysis.round: 3`); `glossbench_r3_pool.py` (populations V / M_num, eligibility, exclusions, quotas, gold-differs gate, arm-pair identity); the drafter's M_num prompt and `symbol_is_the_quantity`; the six review guards | **met**: merge **`898e34d`** (commits `1390051`, `bc9be96`, `cc955ea`), 2026-10-30 |
| 2 | **Offline dry run** of `glossbench_r3_pool.py build --dry --corpus-reach` on O, A, P with the real chunk exports and the round-2 arm files (value) plus their `rederive --attach mention` copies; render parity of the mention files against `for_chunk` with the rule flipped on every chunk of every corpus (`mismatch 0`, `value_not_subset 0`) | **met**, 2026-10-30, at `bc9be96` (clean, `citable true`): eligible O 288 / A 28 / P 109; gold differs O **151** (V 72, M_num 79) / A **56** (16, 40) / P 116 (56, 60) **in open mode — superseded for P by precondition 3's fields-mode reading, 80**; every gate `EVALUABLE`; chunk counts 32,765 / 27,418 / 14,456 `match true`; `arm_pair_identity ok`. Prereg §3.1 step 5 quotes O and A from here and P from precondition 3 |
| 3 | **Offline P dry build in fields mode** (the dry run of 2 built P in open mode) and the **boost probe's offline candidate count** per arm × corpus (`glossary_boost_probe.pick_probes` on the chunk export plus the rendered fields), so the `declared` cells are known before the freeze | **met**, 2026-10-30, at **`898e34d`** (clean, `citable true`; counts only). **P, fields mode:** 7 excluded; 136 dense, 27 without a glossary → **109** eligible; 25 sampled; **300 jobs** (150 per stratum; V plan 4 short); `in_set` 13 / 13; sampled chunks **V 108 / M_num 112**; **gold differs 80 (V 42 / M_num 38)** against the gate of 60 → attainable; realised jobs per field: 10 new-ground fields 255 (51, 44, 42, 35, 30, 16, 14, 11, 9, 3), 12 `in_set` 41, 4 constants 4, 3 fields 0; sampled reach `value` / `mention`: V 36 / 57, M_num 0 / 38. **O 151 (72 / 79) of 288 and A 56 (16 / 40) of 28 re-confirmed.** Private manifests: `pool-manifest-P.json` sha256 `3369a867…`, `-O` `30822bc1…`, `-A` `32a23e2c…`. **Boost probe**, qualifying chunks (minimum 3) per `value` / `mention` / `mention-shuf` / `rules-value` / `rules-mention`: **O 0 / 2 / 2 / 0 / 0; A 0 / 18 / 18 / 0 / 0; P 0 / 1 / 1 / 0 / 0** — `not_testable` in every cell but A × mention; private `probe-candidates` file sha256 `e4966c6c…`. Prereg §3.1 step 5 and §5 note 2 quote these and pre-declare the `declared` cells |
| 4 | **Read-only BM25 exhaustion verifier** for prereg §5 note 3: **#877** (`exp/found10-exhaustion`, head `3dc7fdb`): `python/scripts/eval/bm25_exhaustion.py` + `found10._verify_exhaustion`, one `_count` per short `bm25` list with the store's own `_build_query`, verdicts `exhausted_verified` (complete, not gated) / `clamp` (hard stop) / `under_served` (stays gated), `corpora.<c>.bm25_scope` for the exact tenant scope, `census.es_url`; the prereg specifies it as written and requires `bm25_scope` on every leg | **pending #877** — in review; its merge sha goes here. If it is not merged, the owner's decision to run without it is recorded here instead and the prereg's "without it" branch applies (short `bm25` lists gate) |
| 5 | Owner decisions D1–D14 folded in (prereg §11); the papers-OC owner's input and its user's volume approval of 2026-10-30 (prereg §3.3, §3.4, §8) | **met** (this directory) |
| 6 | **Live-ops plan** for every bench-store touch (`PLAN-round3.md` step 5) | pending; precedes the first swap, not the freeze |

The planning counts of `R3-COUNTS.md` were made at `d87a02e`; the first dry run at `bc9be96`; the
fields-mode re-run and the probe counts at `898e34d`. None ran retrieval; nothing in #874 reads a
retrieval result. The reranker test's record (#876, merge `734cfa6`) is cited by prereg §5 notes 1–3 for
three lessons (the reranker reads the glossary field, so attachment changes its input too; short `bm25`
lists can be exhaustion, D-5; the boost probe can be structurally `not_testable`, D-6) and for one
exploratory reading (on papers-OC, ordering rather than the pool limited the reranked arm); it adds no
hypothesis.

After the freeze, exactly one addendum may be appended to this README: the set-P promotion decision of
prereg §3.5, *promoted* or *not promoted*, quoting the imported pilot verdict summary (n, rejects, seed,
6-decimal Wilson bound). Its terms are fixed in the prereg; nothing else is ever appended here.

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
  recorded here (the freeze procedure above records the frozen sha, once).

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
