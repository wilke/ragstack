# `stage0/` — the calibration that stopped the confirmation run

**Verdict: `GATE-NOT-EVALUABLE — row 9 precondition failed`, on all five confirmatory
contrasts. The confirmation run does not proceed on this design.**

Read [`RESULTS-stage0-calibration.md`](RESULTS-stage0-calibration.md) first; everything else
in this directory is an input to it or an output of it.

**Stage 0b′ (revision 3) also stopped, and its write-up is
[`RESULTS-stage0b-relabel.md`](RESULTS-stage0b-relabel.md).** `SPEC-confirmation-run-r3.md`
§3.7 answered finding A with a quote-primary protocol and a second, reasoning judge; both
judges relabelled the same 308 development pairs. Span self-consistency went from **0.323 to
0.645** for `Llama-4-Scout` — the protocol change was real, and not enough — and sits at
**0.419** for `Qwen3.6-35B-A3B`, against the same ≥ 0.90 gate. **`NEITHER JUDGE PASSES —
stop per r3 §5 step 2.`** The new document-level whether-agreement gate passed for both
(0.968 each). The r3 harness is `s0_label_r3.py` + `s0_labelgates_r3.py` and its artifacts
are under [`artifacts/r3/`](artifacts/r3/). Stage 0's own `s0_label.py` and
`artifacts/labels-dev.jsonl` are untouched, so the original labels stay reproducible.

**Stage 0b′ then ran a second time under r3 §10's two decisions, and its write-up is
[`RESULTS-stage0b-relabel-r31.md`](RESULTS-stage0b-relabel-r31.md)** — whole-sentence anchors and
**five seeded presentations of every pair** at temperature 0, harness `s0_label_r31.py` +
`s0_labelgates_r31.py`, artifacts under [`artifacts/r31/`](artifacts/r31/). The anchor fix worked
and **both** judges now pass hallucinated-span (Scout 0.082 → **0.025**, Qwen 0.025 → **0.003**;
Scout's closing-anchor failures fell from 54 of 58 to 6 of 22); self-consistency did not follow
(Scout 0.645 → **0.383**, Qwen 0.419 → **0.562**, against ≥ 0.90) and the **union of plausible
locations does not saturate** — the marginal gain at the fifth presentation is still ~10 % of the
union size against a 5 % bar. **`NEITHER JUDGE PASSES — the stop stands.`** #501's harness and
artifacts are untouched, so both relabels stay reproducible side by side.

---

## What the confirmation run was, and what Stage 0 was for

Phase 0 left the chunk-size question open in a specific way: every metric it had was a
**document** metric, and the one passage-level reading it managed said the top-ranked chunk
lands in the answering section only 55–65 % of the time. The confirmation run
([`../design/SPEC-confirmation-run.md`](../design/SPEC-confirmation-run.md), rev. 2) was the
pre-registered study built to settle it properly: 90 TREC CDS topics split **10 development /
80 confirmation**, six index arms over a shared 32.7k-document corpus, and a passage-level
primary endpoint — **`EUC@4096`**, the fraction of a topic's located evidence units that
survive into a 4,096-generator-token context.

Five contrasts were pre-registered: two non-inferiority (`N1` 512 vs 1024 for the storage
lever, `N2` 512/64 vs 512/0 for overlap) and three superiority (`R1` 256 vs 2048, `R2`
header512, `R3` parent256).

**Stage 0 is the run that has to happen before the other 80 topics are touched.** It measures,
on the 10 development topics only, the quantities the pre-registration's power arithmetic
*assumes*: σ_d per contrast, the unit-level flip rate and its intra-class correlation ρ, the
per-topic units after capping, the retained-n projection — and, before any of that may be
read, the §7.6 **manipulation checks** that say whether the endpoint is behaving at all.

Its purpose is to be allowed to fail cheaply. It did.

---

## The three findings

**A. The gold is not reproducible — a study stop under §6.6.4.** Re-shown the same document,
the labeling model's span **self-consistency is 0.323** against a ≥ 0.90 gate (0.387 under
both alternative readings of the rule, so the verdict is checker-independent). It agrees with
itself about *whether* a document contains localizable evidence (0.871) and disagrees about
*where* (0.32); only **64.5 %** of verified spans were at the indices it claimed. The
hallucinated-span rate is 0.0504 against a ≤ 0.05 gate. The minimality audit could not be
measured at all — the audit re-prompt returned an empty set on 29 of 29 pairs.

**B. The endpoint is on a floor, so the power gate cannot be read.** Development `EUC@4096` is
**0.017–0.069** against a required **[0.15, 0.90]**. See
[`figures/fig-stage0-euc-floor.svg`](figures/fig-stage0-euc-floor.svg): `EUC` factors exactly
into P(document packed) × P(covered | packed), and the first factor is 0.045–0.209 — at
B = 4,096 the packed context holds **1.1 of a topic's 10.1** unit-bearing documents. Packing
the *entire* frozen D = 50 pool at unlimited budget still leaves the shipping arm at **0.025**,
and at that ceiling `EUC` is nearly proportional to chunk size, which is the confound the
endpoint exists to avoid. The cause is a mismatch **inside the specification**: §6.3 pools 20
documents per arm per variant, D3 spreads up to 12 units across up to 12 documents, and §7.3
at B = 4,096 admits 5–10.

**C. The binary secondary resolves cleanly, negatively.** Per-topic discordance for
`ES-Hit@4096` is **0.10–0.40** where ≤ 0.025 is required. It is not resolvable at ε = 0.05,
n = 80 on any contrast.

### The trap this directory exists to document

Applied mechanically, §8.5.5's power rule reads **"passes" on all five contrasts** —
σ_d **0.070 / 0.000 / 0.081 / 0.029 / 0.064** against a 0.1577 requirement (the governing
model-based values of row 2; the direct row-1 estimates are lower still). That reading is wrong
and [`RESULTS-stage0-calibration.md`](RESULTS-stage0-calibration.md) § 4 refuses it. `N2` is
the proof: σ_d = **0.0000** with `p_flip` = **0.0000** means the shipping arm and the 0 %-overlap
arm covered *identical* unit sets on all ten topics — **at 1.7 % coverage**. That is not two
configurations shown to be equivalent. It is two configurations both covering almost nothing.

Measured ρ is **−0.046 to +0.064** with every 95 % interval containing 0, which on its face is
the *good* branch of §8.5.2. It is also uninformative: an intra-class correlation of indicators
that flip 0.5–7.3 % of the time has nothing to correlate. The correlation objection that
motivated Stage 0 is neither confirmed nor refuted — it is unmeasurable here.

**The failure is not n = 80.** At n = 80 the design would have ample power *if* the endpoint
behaved. Two preconditions fail before power is reachable.

---

## What is in this directory

| path | what |
|---|---|
| [`RESULTS-stage0-calibration.md`](RESULTS-stage0-calibration.md) | the write-up — provenance, both stages, the nine rows, the floor mechanism, the conclusions |
| [`TABLE-8.5.7.md`](TABLE-8.5.7.md) | the §8.5.7 table, generated from the JSON artifacts and never retyped |
| [`figures/`](figures/) | the EUC-floor figure, generated by `fig_stage0.py` from `artifacts/floor_diagnostic.json` |
| `s0_*.py`, `run_0b.sh` | the harness, committed in full — 0a corpus/fetch/parse/chunk/embed, 0b retrieve/pack/label/score/stats/checks |
| `artifacts/*.json` | every machine-readable output the write-up quotes |
| [`RESULTS-stage0b-relabel.md`](RESULTS-stage0b-relabel.md) | the **revision-3 relabel** — quote-primary protocol, two judges, the three machine gates per judge, the cross-judge agreement, the stop |
| `s0_label_r3.py`, `s0_labelgates_r3.py` | the r3 harness. `s0_label_r3.py --selftest` checks the locator offline (in-unit snap, cross-unit split, hallucination, `<think>` stripping) and contacts no endpoint |
| [`artifacts/r3/`](artifacts/r3/) | both judges' labels, the run manifest, `gates-r3.json` and the rendered gate table |
| [`RESULTS-stage0b-pointed-gen.md`](RESULTS-stage0b-pointed-gen.md) | the **pointed-question population** (r3 §11, Stage 0b′) — 177 queries on the development topics with construction gold, at 44.2 % yield against the Leg B re-run's 65 %; generation and screens only, no retrieval |
| `s0_pointed_gen.py`, [`artifacts/pointed/`](artifacts/pointed/) | that harness and its artifacts — the accepted and rejected sets, every raw generator response, and the run manifest |
| [`RESULTS-stage0b-relabel-r31.md`](RESULTS-stage0b-relabel-r31.md) | the **r3.1 relabel** — whole-sentence anchors, five presentations per pair, the gate table per judge, the union-saturation curves, the enumeration proxy and the proposed rubric §6 amendment |
| `s0_label_r31.py`, `s0_labelgates_r31.py` | the r3.1 harness. `s0_label_r31.py --selftest` checks the whole-sentence locator offline — including that the eight-word fallback rescues a mangled middle but **refuses** a quote whose two halves straddle a sentence boundary — and contacts no endpoint |
| [`artifacts/r31/`](artifacts/r31/) | both judges' 1,540 records each (308 pairs × 5 presentations), the merged manifest, `gates-r31.json` and the rendered gate table |
| [`../design/SPEC-confirmation-run.md`](../design/SPEC-confirmation-run.md) | the pre-registration Stage 0 was run against (rev. 2; § 14 is its change log) |
| [`../design/RUBRIC-evidence.md`](../design/RUBRIC-evidence.md) | the labeling rubric, frozen and hashed before the first labeling call |

The two provenance hashes the write-up pins were **re-verified after the copy into this
repository**: the rubric's sha256 is `2e11f368…c747363b` as stated, and the corpus manifest's
`b15f059f…4666a8cbf` recomputes from the 32,663 `(pmcid, sha256)` pairs in
`artifacts/manifest.json` under the convention that file records.

The four prose documents were also confirmed **byte-identical to the run's own copies**, which
is the convention [`../README.md`](../README.md) states for this whole tree:

| file | sha256 |
|---|---|
| `RESULTS-stage0-calibration.md` | `d41c62d53a6d8321…` |
| `TABLE-8.5.7.md` | `a1cc38135c4d3665…` |
| `../design/SPEC-confirmation-run.md` | `b2a7cb30b7c66bc3…` |
| `../design/RUBRIC-evidence.md` | `2e11f3688de916da…` |

### Where the write-up's own paths point

`RESULTS-stage0-calibration.md` is committed **verbatim**, so its § 7 reproduction table names
the run directory's layout rather than this one. The mapping:

| as written | here |
|---|---|
| `phase0/stage0/s0_*.py`, `run_0b.sh` | this directory |
| `artifacts/…` | [`artifacts/`](artifacts/) — resolves as written |
| `design/SPEC-confirmation-run.md`, `design/RUBRIC-evidence.md` | [`../design/`](../design/) — one `../` short |
| `work/labels-v1-strictchecker.jsonl` | [`artifacts/labels-v1-strictchecker.jsonl`](artifacts/labels-v1-strictchecker.jsonl) |
| `work/RDEV-readsheet-{A,B}.html`, `work/rdev_verdicts_{A,B}.csv` | the verdict sheets are in `artifacts/`; **the two readsheets are not committed** — 15 MB each, see below |
| `/rag/tmp/stage0-conf/` | not committed — the run directory of embeddings, chunk spans, pools and packed contexts |

## What is not here

- **The two R-dev readsheets** (`RDEV-readsheet-{A,B}.html`, 15 MB each). They are rendered,
  independently shuffled per reader, and held in the run directory; 30 MB of generated HTML
  against a record whose whole tree packs to under 15 MB is not a trade this repo should make.
  [`artifacts/rdev_sample.json`](artifacts/rdev_sample.json) holds the draw itself — 100 pairs,
  seed `20260915` — so the sheets regenerate from `s0_rdev.py`.
- **The embeddings, chunk spans, retrieval pools and packed contexts.** The write-up records
  49 GB at the time of the run; the directory measures 37 GB today (34 GB of it the six arms'
  embeddings). Regenerable from the committed corpus manifest, config and seeds.
- **The corpus itself.** `artifacts/manifest.json` identifies all 32,663 documents by PMCID
  and content hash; the JATS come from `pmc-oa-opendata`.

---

## Item 8 is `PENDING-HUMAN`, and that is a scope statement, not an omission

Stage 0's deliverable 8 — the ≥ 100-pair, **two-independent-human-reader** label validation —
was not performed, and **no agent read was substituted for a human read**. No κ(human–human)
appears anywhere in the write-up. The draw is complete and the sheets are rendered; the read
is 32–48 person-hours.

One thing the draw itself already measured, and it is a finding rather than a logistics note:
the **deep-section stratum wanted 20 pairs and only 3 existed** across all 308 dev pairs — only
3 had *every* supplied span outside the Abstract and the first body unit. That is the abstract
bias §6.6.6 warns about, observed directly. The stratum the human read most needs is the one
the labels barely populate.

A second deviation is recorded with it: §6.6.1 requires both readers to sign off on the rubric
before any labeling call, and no human did. A reader-forced rubric revision would require a
development relabel under §6.6.4.

**The scoring half is now written and waiting.** [`s0_rdev_score.py`](s0_rdev_score.py)
consumes the two filled-in verdict sheets and produces κ(A–B) on the 6-category verdict with a
bootstrap CI, the binary collapse, per-stratum κ, the label-error / missed-evidence /
correctly-none / ambiguous rates with Wilson uppers, κ(labeler–human) with positive-class
agreement in both directions, and the §6.6.4 acceptance table evaluated row by row. It reads
verdicts; it never reads a pair. Pointed at the blank sheets that ship in `artifacts/` it exits
non-zero with *"fewer than 10 scored pairs"* and emits no κ — which is the current state:

```
python3 s0_rdev_score.py --a artifacts/rdev_verdicts_A.csv --b artifacts/rdev_verdicts_B.csv \
    --sample artifacts/rdev_sample.json --labels artifacts/labels-dev.jsonl \
    [--adjudicated artifacts/rdev_verdicts_ADJ.csv] [--extra NAME=PATH] \
    --out artifacts/rdev_score.json --md RESULTS-rdev.md
```

Tests: `python3 -m pytest test_s0_rdev_score.py -q` (synthetic verdicts only).

---

## Cost

1.13 B embedding tokens over six index arms in **1.93 fleet-hours** at 167k tok/s (≈ 11.6
device-GPU-hours), 0 retries in 168,134 requests; 9,000 rerank pairs; 972 labeling requests
over 11.36 M prompt tokens and ~56 minutes of LLM time. The second judge was never called. The
80 confirmation topics were never embedded, ranked, packed, labeled or inspected — asserted in
code before the first query embed. The reserved GPUs read 0 MiB before and after every stage,
and **no store was contacted at any point**.
