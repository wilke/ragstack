# Pre-registration, glossary attachment benchmark round 3

**Status: DRAFT v1 (2026-10-30), aligned with `main` at `898e34d` (#874). Not frozen.** When the owner
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
at `898e34d` (2026-10-30), and by the offline dry run of the committed pool on the real round-2 inputs
(review of #874; counts only, no text, no model, store, API or gateway call):

| # | precondition | state |
|---|---|---|
| 1 | **#874** (`exp/r3-attach`): `rederive --attach` and the `+attach=` mark (`glossary_file.py`); the stamp slot in `found10.py` (`split_stamp_attach`, `stamp_allowed`, `check_arm_file_attach`) and `--probe-max-glossaries`; the round-3 family in `found10_analysis.py` (`analyse_r3`, selected by `analysis.round: 3`); `glossbench_r3_pool.py` (populations V / M_num, eligibility, exclusions, quotas, gold-differs gate, arm-pair identity); the drafter's M_num prompt and `symbol_is_the_quantity`; the six review guards | **met**: merge **`898e34d`** (commits `1390051`, `bc9be96`, `cc955ea`), 2026-10-30 |
| 2 | **Offline dry run** of `glossbench_r3_pool.py build --dry --corpus-reach` on O, A, P with the real chunk exports and the round-2 arm files (value) plus their `rederive --attach mention` copies; render parity of the mention files against `for_chunk` with the rule flipped on every chunk of every corpus (`mismatch 0`, `value_not_subset 0`) | **met**, 2026-10-30, at `bc9be96` (clean, `citable true`): eligible O 288 / A 28 / P 109; gold differs O **151** (V 72, M_num 79) / A **56** (16, 40) / P **116** (56, 60); every gate `EVALUABLE`; chunk counts 32,765 / 27,418 / 14,456 `match true`; `arm_pair_identity ok`. Prereg §3.1 step 5 quotes these |
| 3 | **Offline P dry build in fields mode** (the dry run of 2 built P in open mode) and the **boost probe's offline candidate count** per arm × corpus (`glossary_boost_probe.pick_probes` on the chunk export plus the rendered fields, at `max_glossaries` 10 and widened), so the `declared` cells are known before the freeze | **open** — runs of committed code; the readings are recorded here before the freeze |
| 4 | **Read-only BM25 exhaustion verifier** for prereg §5 note 3 (served length of a short `bm25` list = the `_count` of the server's BM25 clause on the bench index; recorded per query), or the owner's decision to run without it and let short `bm25` lists gate | **open** — not on `main` at `898e34d`; new code (one small PR) or a recorded decision |
| 5 | Owner decisions D1–D14 folded in (prereg §11); the papers-OC owner's input and its user's volume approval of 2026-10-30 (prereg §3.3, §3.4, §8) | **met** (this directory) |
| 6 | **Live-ops plan** for every bench-store touch (`PLAN-round3.md` step 5) | pending; precedes the first swap, not the freeze |

The planning counts of `R3-COUNTS.md` were made at `d87a02e`; the dry run at `bc9be96`. Neither ran
retrieval; nothing in #874 reads a retrieval result.

After the freeze, exactly one addendum may be appended to this README: the set-P promotion decision of
prereg §3.5, *promoted* or *not promoted*, quoting the imported pilot verdict summary (n, rejects, seed,
6-decimal Wilson bound). Its terms are fixed in the prereg; nothing else is ever appended here.

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
  open-mode caveat is stated, and the fields-mode re-run is precondition 3.
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
