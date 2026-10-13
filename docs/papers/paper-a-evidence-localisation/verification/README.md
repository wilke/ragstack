# Paper A — independent recomputation of every claim

This directory checks every number in `../claims.json` (105 claims) and the numerals in
`../DRAFT.md` against the committed record, in three layers, plus a fourth section asked for
mid-task: the **current-code delta** (would `main`'s code give the same numbers?).

It does **not** edit `claims.json`, `DRAFT.md` or any committed artifact. Every correction
listed below is a proposal for the owner.

| file | what it is |
|---|---|
| `RESULTS-recompute.json` | per claim: committed value, Layer A value (pinned harness), value from the same harness on `main`, Layer B value (independent scripts), status, and `why` / `finding` |
| `out/*.json` | the outputs of the scripts below, each with an `experiment_provenance()` block |
| `reproduce_pinned.sh` | Layer A, and the same gate scripts run from `main` |
| `recompute_*.py`, `vcommon.py` | Layer B — new code, written from DRAFT.md / RESULTS-*.md / PREDICTIONS.md; imports nothing from the harness |
| `recompute_derived.py` | Layer C — derived-claim arithmetic, other stated arithmetic, and a numeral audit of DRAFT.md |
| `coord_spans.py`, `coord_check.py` | current-code delta D2 — sentence-coordinate check, pin vs main |
| `relocate_main.py` | current-code delta D3 — main's locator re-run over the stored model answers |
| `build_results.py` | assembles `RESULTS-recompute.json` |

## How to rerun

No endpoint, model, GPU or store is contacted. Inputs are the committed label files under
`docs/plans/results/stage0/artifacts/`, plus three things that exist only on `coconut`, all
outside any `work/conf` path: `/rag/tmp/stage0-conf/work/{docs,units}.jsonl` (the DEV
corpus text and unit boundaries, for D2/D3), `work/claude/raw-*.jsonl` (read by the pinned
Claude gate script for its cost accounting) and `work/r31ext-filtered/` (the #513 filter's
output on the DEV labels; its sha256 equals the `sha256_out` recorded in
`artifacts/conf-a/span-filter-dev-verification.json`).

```bash
cd docs/papers/paper-a-evidence-localisation/verification
PY=/rag/envs/ragstack/bin/python3; S=<scratch dir>
OUT=$S/A bash reproduce_pinned.sh                               # Layer A + main run (~3 min)
for s in recompute_hallucination recompute_agreement recompute_unions \
         recompute_crossfamily recompute_derived recompute_reliability; do
  PAPER_A_VERIFY_OUT=$S/B PYTHONPATH=../../../../python $PY $s.py   # reliability: ~40 min
done
$PY coord_spans.py --repo ~/Development/worktrees/paper-a-pin-55a0fc2 --out $S/pin.json
$PY coord_spans.py --repo "$(git rev-parse --show-toplevel)"     --out $S/main.json
PAPER_A_VERIFY_OUT=$S/B PYTHONPATH=../../../../python $PY coord_check.py $S/pin.json $S/main.json
STAGE0_BIG=$S/big-main PAPER_A_VERIFY_OUT=$S/B $PY relocate_main.py   # ~10 min, 32 procs
PYTHONPATH=../../../../python $PY build_results.py --a $S/A --b $S/B
$PY ../../check_claims.py                                       # must still pass
```

`PAPER_A_SEEDS=1` shortens the reliability seed-sensitivity loop for a quick run.

## Status counts (105 claims)

| status | n |
|---|---|
| MATCH | 67 |
| MATCH-ROUNDING | 7 |
| MISMATCH | 8 |
| AMBIGUOUS DEFINITION | 10 |
| NOT RECOMPUTED (quarantine) | 9 |
| NOT RECOMPUTED (constant / statement) | 4 |

Layer A reproduced all 80 claims whose source is a gate artifact exactly; the gate scripts run from `main` give the same 80 (CURRENT-CODE DELTA).


## Layer A — reproduce at the pin

Every gate script that produced a claim source was re-run **at the commit that committed its
artifact**, in a detached worktree under `~/Development/worktrees/paper-a-pin-<sha>`, writing to
scratch (`--outdir`, and `STAGE0_BIG` pointed at a scratch copy of the DEV inputs, because the
scripts also write under `$STAGE0_BIG/work`). No harness was edited; relocation was by
environment only. Every script and artifact involved was committed exactly once, so the pin is
unambiguous:

| artifact | pin | script |
|---|---|---|
| `artifacts/label_gates.json` | `b1f9ead` | `s0_labelgates.py` (needs `PYTHONPATH` to the `phase0-rescue` helpers — it predates the `STAGE0_HELPERS` hook) |
| `artifacts/r3/gates-r3.json` | `0886ddc` | `s0_labelgates_r3.py` |
| `artifacts/r31/gates-r31.json` | `bd9db86` | `s0_labelgates_r31.py` |
| `artifacts/r31ext/gates-r31ext.json` (+ `--prefit` for `predictions.json`) | `fc1ace4` | `s0_labelgates_r31ext.py` |
| `artifacts/claude/gates-claude.json` | `9696d41` | `s0_gates_claude.py` |

**Result: byte-identical** for `label_gates.json`, `gates-r3.json`, `gates-r31.json`,
`gates-r31ext.json` and their `.md` renderings. `gates-claude.json` differs in exactly five
lines, all of them the absolute path of the input directory (`raw_path`, `source`), which is
the scratch copy. `--prefit` reproduces every number in `predictions.json`. So every claim
whose source is a gate artifact is reproduced exactly (`A_vs_committed: "eq"` on all of them).

Not reproducible by a script, and so not in Layer A: the markdown-only claims
(`RESULTS-*.md`, `README.md`, `PREDICTIONS.md` text), the pre-registered constants, and
everything under `conf-a/` (quarantine; see below).

## Layer B — independent recomputation

`recompute_*.py` re-derive each statistic from the raw label records using the definitions
written in DRAFT.md §3–5, the RESULTS files and PREDICTIONS.md, with new code (character-span
Jaccard, D3 set accumulation, sentence unions, Wilson, Cohen's κ, Pearson/Spearman,
Michaelis–Menten by golden-section search with Km free to 10⁶, split-half with Spearman–Brown).
Where the text leaves a choice open, the script states the reading it took and computes the
alternative beside it.

Coverage, against the brief:

- hallucinated-span rates and Wilson upper bounds — all protocols and judges, k = 0 and all
  presentations, P-ext-3 blocks; failure counts cross-checked against the records' `problems`
  lists (agree everywhere) — **all MATCH**;
- whether-agreement (all-k and mean pairwise; curve at 2/5/10/20) — MATCH under the
  harness's reading, **AMBIGUOUS** (below);
- span self-consistency presentation 0 vs 1, on the 31, all ten pairs, partial credit — MATCH;
- union marginal gains and set-union saturation — MATCH / MATCH-ROUNDING;
- Michaelis–Menten fits (pre-registered, refits, blow-up-excluded, degenerate) — MATCH-ROUNDING
  (last-digit; the harness fits the 4-dp-rounded curve on a grid). The degenerate fits are
  confirmed degenerate: with Km unbounded the optimum runs to the ceiling (Km → ∞) and a
  straight line fits as well, so "absent" is right;
- graded-support split-half reliability with Spearman–Brown at k = 4, 6, 8, 10, 14, 20, 30,
  pooled and per judge, same seeds and draw protocol — **MISMATCH at four points** (below);
- implied single-reading reliability — the arithmetic is right (Layer C) but inherits the
  MISMATCH of its inputs;
- cross-judge κ and Jaccard values; the judge–judge support correlation; the cross-family lift
  and point-biserial r; the Claude costs and n's — MATCH, with two findings about the blow-up
  pair and one AMBIGUOUS definition (below).

### Random-draw statistics: same seeds, and sensitivity across seeds

The split-half estimator was implemented from PREDICTIONS.md. To reproduce the committed
draws bit-for-bit, three details the text does not state had to be matched (and are now
documented here): one `random.Random(20260914 + n)` stream per n; pairs consumed in sorted
(topic, docno) order; one `shuffle(range(n))` per pair with a non-empty universe, taken
*before* the "fewer than two sentences" check. With those, every pooled and per-judge value
reproduces — except where the no-variance test bites (next section).

Sensitivity across seeds (100 alternative seeds × 20 draws each, Spearman–Brown):

| instrument, k | committed | exact, same seeds | mean over 100 seeds | SD | min – max |
|---|---|---|---|---|---|
| pooled, 4 | 0.1879 | 0.1879 | 0.1802 | 0.0070 | 0.1659 – 0.1974 |
| pooled, 6 | 0.4738 | 0.4738 | 0.4741 | 0.0042 | 0.4646 – 0.4845 |
| pooled, 8 | 0.6272 | 0.6272 | 0.6292 | 0.0030 | 0.6216 – 0.6366 |
| pooled, 10 | 0.705 | 0.7063 | 0.7081 | 0.0026 | 0.7003 – 0.7154 |
| pooled, 14 | 0.788 | 0.7880 | 0.7900 | 0.0018 | 0.7847 – 0.7944 |
| pooled, 20 | 0.8588 | 0.8589 | 0.8587 | 0.0012 | 0.8559 – 0.8618 |
| pooled, 30 | 0.9205 | 0.9205 | 0.9197 | 0.0008 | 0.9178 – 0.9222 |
| scout, 10 | 0.8338 | 0.8338 | 0.8336 | 0.0016 | 0.8300 – 0.8378 |
| scout, 14 | 0.8821 | 0.8821 | 0.8831 | 0.0011 | 0.8800 – 0.8872 |
| scout, 20 | 0.9142 | 0.9141 | 0.9146 | 0.0007 | 0.9128 – 0.9163 |
| qwen, 10 | 0.7216 | 0.7220 | 0.7250 | 0.0040 | 0.7129 – 0.7366 |


The committed values sit inside these ranges; the headline 0.9205 at k = 30 is 1 SD above
the across-seed mean (0.9197, range 0.9178–0.9222). The k = 30 result does not depend on the
seed. Two other estimator choices are reported in `out/recompute_reliability.json`: counting
an undefined r as 0 gives 0.9174 at k = 30; averaging r on Fisher's z gives 0.9576 (the
per-pair r's are bounded near 1, so z-averaging inflates). Neither is the registered
estimator.

## MISMATCH — the no-variance test in the split-half estimator

| claim | committed | recomputed (exact) |
|---|---|---|
| `graded-support-reliability-at-10` | r 0.5444, SB 0.7050 | r 0.5460, SB 0.7063 |
| `graded-support-reliability-at-20` | r 0.7526, SB 0.8588 | r 0.7527, SB 0.8589 |
| `graded-support-degenerate-halves-curve` [k = 10] | 2 | 3 |
| `judge-scout-support-reliability-curve` [k = 20] | SB 0.9142, 4 no-variance pairs | SB 0.9141, 5 |
| `judge-qwen-support-reliability-curve` [k = 10] | SB 0.7216 | SB 0.7220 |
| `graded-support-implied-single-reading-pooled` | inputs 0.705 / 0.8588 → ρ₁ 0.1929 / 0.2332 | 0.7063 / 0.8589 → 0.1939 / 0.2333 |
| `graded-support-implied-single-reading-scout` | input 0.9142 → ρ₁(20) 0.3476 | 0.9141 → 0.3472 |
| `graded-support-implied-single-reading-qwen` | input 0.7216 → ρ₁ 0.2058 | 0.7220 → 0.2061 |

**Cause.** The estimator (PREDICTIONS.md P-ext-1, DRAFT §3.4) drops a pair whose half has no
variance. The harness tests that with floats: support fractions are Python floats, and a half
is "no variance" only if `sqrt(sum((x − mean)²)) == 0.0`. A half whose values are all equal to,
say, 1/5 can have a computed mean that differs from 1/5 in the last bit, so the test fails, the
pair is kept, and it contributes a Pearson r computed from ~1e-17 rounding noise (anything in
[−1, 1]). The recomputation tests variance on integer vote counts, which is exact. Emulating
the float test (`results.float_test` in `out/recompute_reliability.json`) reproduces **every**
committed reliability value exactly, so this is the whole explanation.

**Size.** At most 0.0013 (k = 10 pooled). The k = 30 headline (0.9205), every pre-registered
score, and every conclusion are unchanged: P-ext-1 still PASSES, Scout alone still clears 0.9
at twenty readings (0.9141), and the Scout implied single-reading values are 0.3341 / 0.3483 /
0.3472 — still flat from 14 to 20. The k = 10 basis quoted in PREDICTIONS.md (0.5444 / 0.7050)
and the span-filter verification's unfiltered recomputation use the same code and carry the
same artefact; the latter is unaffected at k = 30.

## AMBIGUOUS DEFINITION — readings the text allows that change a number

Each is computed both ways in `out/`; the harness's reading is the one that matches.

1. **What counts as a "yes" in whether-agreement** (`r31-scout-whether-agreement-five-presentations`,
   `r31-qwen-whether-agreement-five-presentations`, `r31ext-scout-whether-agreement-curve`,
   `r31ext-qwen-whether-agreement-curve`). A reading in which the judge returned evidence but
   every span failed verification (`dropped: true`) carries no set, so the harness scores it
   "no evidence". DRAFT §3.1 frames the verdict as the judge's answer. Counting those readings
   as positive: Scout all-five 0.9188 → **0.9253** (285/308), mean pairwise 0.9610 → 0.9636;
   curve 2/5/10/20 = 0.961 / **0.9253 / 0.8799 / 0.8604**; Qwen 10 readings 0.961 → **0.974**.
   The gate verdicts do not change.
2. **"Union" in the union split-half** (`r31-scout-union-as-labeler-unstable`,
   `r31-qwen-union-as-labeler-unstable`). The harness unions the D3-*distinct* sets, so a set
   that merged into an earlier one contributes none of its own spans. The plain union of all
   spans in each half gives Scout 0.4708 → **0.4838**, Qwen 0.5779 → **0.5877**. (The same
   choice moves the cross-judge five-reading-union Jaccard only from 0.1613 to 0.1607.)
3. **"Excluding the blown-up pair" for Qwen** (`r31ext-qwen-sentence-fit-excluding-blowups`).
   The excluded pair, 2016_1 / 4212306, is where *Scout* blew up; Qwen has no blow-up anywhere.
   Qwen's own curve, unfiltered: 307 pairs, marginal gain **0.0674**, fraction of asymptote
   **0.6931** (DRAFT Table 4 says 0.0694 / 0.7098 under "blow-up excluded").
4. **Fable's lift baseline** (`cross-family-fable51-support-lift`). DRAFT §5.5 and the claim
   note say the baseline is "recomputed over its own 248 positive pairs". The harness takes
   every pair Fable labeled that has local support — 250 pairs, 14,907 sentences; 248 is the
   number of pairs with picks. Over the 248 positive pairs: baseline 5.3497, lift 5.4528.
5. **Index agreement's denominator** (`s0-span-index-agreement`). The artifact's own
   definition string says "fraction of *verified* spans", but the rate is 307/476 over all
   attempted spans (24 hallucinated and 5 unresolvable included). Over verified spans it is
   307/447 = **0.6868**. DRAFT §3.2 says "of 476 attempted spans", which matches the
   computation; the artifact's definition text is the inconsistent part.
6. **"Rewrote" in the span-filter counts** (`span-filter-dev-counts`). `records_filtered = 82`
   counts records where the #513 filter was *applied*. Diffing the filtered DEV file against
   the committed labels, only **54** records' evidence changed; 28 applications were no-ops
   (a group was collapsed, nothing dropped). `sentences_dropped = 25,208` counts sentences per
   dropped span, with overlaps; **25,101** distinct sentences were removed. `spans_dropped`
   (1,088) and the blow-up flag (1) match. DRAFT §5.6 "rewrote 82 of Scout's 6,160 readings,
   1.33 %" should read "changed 54 (0.88 %)", or say "the filter fired on 82". The scale-run
   figure (1,050) is presumably the same "applied" statistic, so the dev-vs-scale comparison
   is like-for-like; that could not be checked (quarantine).

## Findings that do not change a claimed value

- **The 2.86 baseline is mostly one pathological pair.** The local pool's baseline for Sonnet
  and Opus (`cross-family-sonnet5-support-lift`, `cross-family-opus5-support-lift`) includes the
  locator blow-up pair: 22,693 of its 40,588 touched sentences, almost all at support 1.
  Without that pair the baseline is **5.19**, the lifts are Sonnet **+5.96** and Opus
  **+4.43**, and Fable's baseline (5.35, on a sample that happens not to contain the pair)
  becomes comparable. DRAFT §5.5's explanation, "Fable's baseline differs because Fable
  labeled only 251 pairs", is mechanically true but misses the cause. The paper excludes this
  pair in §5.3 and filters it in §5.6, but not here.
- The same pair contributes 22,693 of the 40,588 sentences in the **pooled judge–judge r**
  (0.2657; **0.2282** without it) and most of the sentences behind the **pooled point-biserial
  r** (Sonnet 0.2353 → **0.2017**, Opus 0.2758 → **0.2226**; Fable unchanged). The per-pair
  mean r (0.2976) is unaffected.
- `r31ext-set-union-saturates` / `r31ext-qwen-set-union-marginal-gain`: the recomputed Qwen
  set gain is 0.04598, committed 0.0461; the harness computes the gain from mean curves
  already rounded to 4 dp. The verdict (< 0.05) is unchanged.
- P-ext-1's registered projection 0.8826 is not reproduced exactly by its stated basis:
  Spearman–Brown from the reviewer's r = 0.556 at n = 10 to 30 readings gives 0.8825, and
  tripling SB10 = 0.714 gives 0.8822. PREDICTIONS.md's own "0.877" for 0.705 tripled is
  0.8776 (truncated). All within rounding of an unrounded r; noted, not counted.
- The pre-registered `projected_U20` (58.7603) was computed from the rounded Vmax/Km;
  unrounded it is 58.7602.

## Quarantined claims — NOT RECOMPUTED (quarantine)

`confirmation-scale-pairs`, `confirmation-scale-pairs-total`, `confirmation-scale-composition`,
`confirmation-readings-per-judge`, `confirmation-total-readings`, `confirmation-record-counts`,
`span-filter-conf-counts`, `instrument-hashes-conf`, `confirmation-outputs-quarantined`.
Nothing under `work/conf/` was read and no `s0c_*` script was run. They resolve against the
committed artifacts (`check_claims.py`), and their internal arithmetic holds:
3,738 = 2,962 + 776; 3,738 × 20 = 74,760; 3,738 × 10 = 37,380; 74,760 + 37,380 = 112,140;
33,238 + 4,142 = 37,380; 3,738 / 80 = 46.725 → 46.73; 1,050 / 74,760 = 1.40 %;
1,653 / 9 = 183.7 ("about 184"). The scale run's prompt hash `ba09e122…` equals the
`prompt_sha256` on every committed r3.1 DEV label record.

`span-filter-dev-verification.json` is about DEV labels and its inputs are outside `work/conf`,
so it **was** recomputed: the post-filtered DEV files are read directly (not regenerated) and
the independent estimator gives unfiltered 0.9205, filtered 0.9192, delta −0.0013 — MATCH.

## Not recomputed for other reasons

- `prereg-p-ext-3-as-written`, `prereg-p-ext-1-as-registered`, `graded-support-bar`: these are
  pre-registered constants and text. They resolve; there is nothing to compute.
- `human-read-not-performed`: a statement about the record.
- Hallucination is recomputed from the labeler's own per-record bookkeeping. Re-verifying each
  quote against the article text is D3 below (main's locator over the stored answers).
- `fable51-incomplete-n.stopped_by_usage_limit` is a run-control fact that exists only in the
  run manifest; it is resolved, not recomputed.

## Layer C — derived claims, stated arithmetic, and the DRAFT numeral audit

The five `derived` claims' arithmetic is right (`out/recompute_derived.json`):
n/(Km+n) = 0.6949 (Scout sets, n = 20) and 0.7094 (Qwen, n = 10); observed/Vmax = 0.7194 and
0.7223; ρ₁ = R/(k − (k−1)R) gives 0.1929 / 0.2098 / 0.2332 / 0.2785 (pooled), 0.3341 / 0.3483 /
0.3476 (Scout), 0.2058 (Qwen) from the committed R. The inputs themselves are the MISMATCH
above, so the stated ρ₁ at pooled k = 10 and 20, Scout k = 20 and Qwen k = 10 move in the 4th
decimal under the exact estimator. 40 further pieces of arithmetic stated in claims or DRAFT
(Spearman–Brown doubling at 10/20/30, the P-ext-2 projection, threshold and relative errors,
cost sums, percentages, shares of the pooled order) check out; see the file.

**DRAFT numerals with no claim id.** `recompute_derived.py` scans the DRAFT body (references
excluded) for every numeral and tests it against all claim values. 62 of 483 resolve to no
claim value. They fall into three groups:

- *Section numbers and model names* (2.1, 3.4, 5.6, "Fable 5.1", …) — not numbers.
- *Quotations of the literature* — FEVER 95.42 / 72.36 %, WiCE 56.1 / 34.4 %, NQ 25-way,
  HotpotQA 61.50 / 83.60 / 90.04, PICO 0.357–0.576 / 0.713–0.758, Norman > 0.95 over 541,000,
  CHYD < 40 %, Wong & Paritosh 0.590 → 0.950, Scholer 51.62 / 44.80 %, and others that match
  a claim value only by coincidence (Kasner's 24 %, Thakur's κ −0.03 / 0.07, Reiss 0.75 → 0.91,
  Hofstätter's κ bands, He's 80 of 1,000). These are claims about other people's papers;
  they belong to `bibliography.md`, not to this record, and were not checked here.
- *In-house numbers stated in prose without a claim id* — each recomputed:
  140 misindexed spans (§3.2; recomputed 140); 867 pair readings (§4; 308 + 308 + 251);
  0.3534 over 2,898 and 0.7518 over 3,056 (§5.3 partial-credit medians and n's; recomputed
  exactly); 230 pairs (§5.5 Sonnet–Fable; recomputed); 1.33 % and 1.40 % (§5.6; arithmetic
  correct, but see AMBIGUOUS 6). None is wrong; all should get claim ids.

## CURRENT-CODE DELTA

The owner asked whether the claims still hold under the code on `main` (`c2d63c2`), not
only at the pins.

### D1 — code diff, pin → `origin/main`, on every path that can move a number

| path | role | commits after the pin | effect on a number |
|---|---|---|---|
| `python/ragstack/ingestion/chunkers.py` (`sentence_spans`, `_punkt_sentence_spans`) | segmentation (all labels) | `e0dc63d` (#620) — **comment only** in `make_chunker` | none |
| `python/ragstack/ingestion/{tokenization,models}.py` (chunkers' imports) | — | none | none |
| `python/ragstack/ingestion/jats.py` | built the DEV texts (stored in `docs.jsonl`, not regenerated) | `008e899` (#573) — `year` coerced to int | none on text or units |
| `s0_label.py` (`segment`, `_find`, `_norm_map`, `parse_and_verify`) | Stage 0 locator, label parsing | none since `b1f9ead` | none |
| `s0_label_r3.py` (`parse_and_verify_r3`, `_snap`, `FAIL_PROBLEMS`) | r3 locator | none since `0886ddc` | none |
| `s0_label_r31.py` (`parse_and_verify_r31`, `_Locator`) | r3.1 / extension / Claude locator | `fc1ace4` — adds `presentation_range()` and `--workdir` only (and that is the extension's own pin) | none |
| `s0_label_claude.py` | Claude labeler | none since `9696d41` | none |
| `s0c_span_filter.py`, `s0c_common.py` (#513 filter) | post-filter | none since `2f85092` | none |
| `s0_labelgates*.py`, `s0_gates_claude.py`, `s0_score.py`, `s0_common.py` | gates / statistics | none since their pins | none |
| `s0_math.py` | Wilson, κ | `27d4422` (#500) — adds κ functions; before the r3 pin, after Stage 0's; `wilson()` unchanged | none |
| `stage1_common.py` / `pilot_common.py` | helpers (`pin_repo`, `atomic_json`) | in-repo copies added `10a4068` (#529); `pin_repo` identical to the `phase0-rescue` copy | none |
| environment | `nltk` punkt backend | 3.9.4 at both | none |

`git diff <pin> origin/main` on each harness file that existed at its pin is empty. Running the
five gate scripts from `main` (`reproduce_pinned.sh`, `OUT/main`) gives byte-identical artifacts
(Claude: the same five path lines).

### D2 — coordinate check

`sentence_spans()` was run at the Stage 0 pin `55a0fc2` (detached worktree, editable-install
finder removed, `ragstack.__file__` asserted) and at `main`, over the 5,232 unit texts of the
306 distinct development documents (308 pairs), using the stored `docs.jsonl` / `units.jsonl`.

| | pin `55a0fc2` | main |
|---|---|---|
| `sha256` (offsets) | `1f674ec5f262fc0ed471da61d94f2ec3497141d711da680ca4850a42e32b9553` | `1f674ec5f262fc0ed471da61d94f2ec3497141d711da680ca4850a42e32b9553` |
| `texts_sha256` | `20b083ff8e6c67b52d5b1e7ebc958a37d2d1392d36b2372a77bc394879415dd7` | `20b083ff8e6c67b52d5b1e7ebc958a37d2d1392d36b2372a77bc394879415dd7` |
| unit texts / spans | 5232 / 114537 | 5232 / 114537 |
| backend, nltk | punkt, 3.9.4 | punkt, 3.9.4 |


**The fingerprints are EQUAL.** No document's offsets moved (0 of 306). As a direct check, every
stored label span — Stage 0, r3, the 9,240 extension readings (which contain r3.1's 1,540) and
the three Claude judges — starts exactly at its first sentence's offset and ends exactly at its
last sentence's offset in `main`'s segmentation: 0 of 25,050 spans off. The stored labels are
valid in `main`'s coordinates. **No claim needs relabeling.**

Re-running the analysis with `main`'s code paths on the stored labels gives the per-claim
`main_recomputed` column of `RESULTS-recompute.json`: equal to the committed value for every
claim with a gate-artifact source (the main-run artifacts are byte-identical to the
committed ones). The Layer B statuses above apply unchanged to `main`.

### D3 — main's locator over the stored model answers

The committed label records keep the post-strip answer text (`raws`), so `main`'s locator
(`parse_and_verify` / `_r3` / `_r31`) was re-run over every primary reading and the record was
reassembled exactly as the labelers do (one parse per answer; a failed parse is followed by
its one re-prompt; re-prompt sets replace the first attempt's only when non-empty).

| run, judge | records | sets identical | problems identical | vstats identical | spans seen | hallucinated |
|---|---|---|---|---|---|---|
| s0.scout | 308 | 308 | 308 | 308 | 476 | 24 |
| r3.scout | 308 | 308 | 308 | 308 | 707 | 58 |
| r3.qwen | 308 | 308 | 308 | 308 | 356 | 9 |
| r31ext.scout | 6160 | 6160 | 6160 | 6160 | 17635 | 345 |
| r31ext.qwen | 3080 | 3080 | 3080 | 3080 | 3593 | 35 |
| claude.sonnet5 | 308 | 308 | 308 | 308 | 525 | 1 |
| claude.opus5 | 308 | 308 | 308 | 308 | 955 | 1 |
| claude.fable51 | 251 | 251 | 251 | 251 | 645 | 0 |

Every record is re-derived identically: the same spans (character offsets and sentence ids), the same problem lists, the same counters — including the one blow-up reading (986 emitted spans from 22). The hallucinated-span counts therefore equal the committed ones (s0 24/476, r3 Scout 58/707, r3 Qwen 9/356, Claude 1/525, 1/955, 0/645; the extension's 345/17,635 and 35/3,593 are the k = 0..19 / 0..9 totals of which the claimed k = 0 and P-ext-3 blocks are parts). The harness's `pin_repo()` insists on importing `ragstack` from the primary checkout; that checkout was at `c2d63c2` with `chunkers.py` identical to this commit and a clean `python/` tree (recorded in `out/relocate_main.json`).


The #513 span filter was not re-run (`s0c_*` scripts are out of bounds for this task). Its code
is unchanged on `main` since `2f85092`, and its DEV output was checked from the stored filtered
files instead (AMBIGUOUS 6). Not re-located: the Stage 0 and r3 *duplicate* and *audit*
presentations, whose answer text is not in the committed records.

### Claims that need relabeling

**None.** The segmenter, the locators, the span filter and the gate code are unchanged on
`main` in every path that affects a number; the sentence fingerprints over the 306 development
documents are identical; and main's locator re-derives the stored spans from the stored
answers. The differences this verification found (MISMATCH, AMBIGUOUS above) are properties of
the analysis code and of the text, identical at the pin and on `main`, and need no new
readings.
