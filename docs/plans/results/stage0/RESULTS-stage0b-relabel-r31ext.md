# RESULTS — the r3.1 extension: Scout ×20, Qwen ×10, and what a *graded* gold is worth

**Specification:** [`../design/SPEC-confirmation-run-r3.md`](../design/SPEC-confirmation-run-r3.md)
§3.7 item 6 and **§10 item 4**, whose three options the owner is pursuing in full. This run
supplies the measurement two of them turn on: option (a)'s **graded per-sentence support** —
how reliable is it, and how many readings does it take? — and option (c)'s implicit claim that
**more presentations converge** on a canonical span set.

**Extends** [`RESULTS-stage0b-relabel-r31.md`](RESULTS-stage0b-relabel-r31.md) (#507). Same 308
development pairs, same two judges, same prompt revision 3.1, same seed formula. #507's five
presentations per judge are **read, not regenerated**, and its committed artifacts are untouched.

**Scope note, stated first because it bounds everything below.** No human read was performed.
**No κ(human–human) or κ(judge–human) appears anywhere in this document**, the R-dev pairs were
not read, and r3 §3.7 item 5's enumeration recall is **absent, not zero**. The human half stays
`PENDING-HUMAN`. Nothing here is a decision about §10 item 4; it is the measurement that
decision needs.

---

**Verdict: `THE SPAN GATES STILL FAIL — but the graded instrument clears r3's labeler bar.`**

* **The three span gates fail for both judges on every reading, and depth makes one of them
  worse.** Scout self-consistency **0.3831**, whether-agreement **0.8539** across all twenty
  readings; Qwen self-consistency **0.5617**, whether **0.961**. The copy gate passes for both
  (Scout 0.02517, Qwen 0.00279 at k = 0). Twenty presentations do not rescue what five could
  not: the r3.1 stop stands, unchanged and now measured four times deeper.
* **Graded per-sentence support at 30 pooled readings has Spearman–Brown reliability 0.9205**,
  above the **0.90** bar r3 §3.7 sets for a labeler — the number §10 item 4 (a) turns on.
  Ten readings buy 0.705; twenty buy 0.8588. **P-ext-1 passes** (predicted ≥ 0.85).
* **The union still does not saturate, and the pre-registered fit predicted exactly where it
  would land.** Scout's k = 20 union is **58.62** distinct sentences per pair against the
  five-point Michaelis–Menten projection of **58.7603** — a **0.24 %** four-fold extrapolation
  error, and **81.2 %** of the asymptote, against the 81.24 % the pre-registration computed
  before the run. **P-ext-2 is scored PASS as written and that PASS is an artefact** of a
  single locator blow-up in one reading of 6,160; on the cleaned reading it **fails exactly as
  the pre-registration said it would**, and its informative secondary passes. §3 is the whole
  story and it is the most interesting result in this document.
* **Reliable is not the same as valid.** The two judges' graded support correlates at mean
  **r = 0.2976** per pair, with top-3 sentence overlap **0.4473**. A pooled instrument that
  reproduces itself at 0.92 is still averaging two judges that largely disagree about *which*
  sentences carry the evidence. §5.

---

## 0. Provenance

| item | value |
|---|---|
| specification | `../design/SPEC-confirmation-run-r3.md` §3.7 item 6, §10 item 4 |
| extends | `RESULTS-stage0b-relabel-r31.md` (#507) — same 308 pairs, same judges, same rubric, same labeling set. #507's artifacts under `artifacts/r31/` are **not modified** |
| **pre-registration** | [`artifacts/r31ext/PREDICTIONS.md`](artifacts/r31ext/PREDICTIONS.md) + `predictions.json`, committed in `1ef771a` **before the first new label was generated**. `s0_labelgates_r31ext.py --prefit` still reproduces every number in it from the committed r3.1 labels: split-half *r* **0.5444**, SB full-10 **0.7050**, V<sub>max</sub> **72.3277**, K<sub>m</sub> **4.6179**, projected U(20) **58.7603**, threshold **65.0949**. Re-run it — the analysis edits made for §3 were checked against this reproduction |
| **rubric** | `../design/RUBRIC-evidence.md`, sha256 `2e11f3688de916da…c747363b` — unchanged from Stage 0, #501 and #507 |
| **prompt** | revision **3.1**, sha256 `ba09e122553833219a999f8a99c496fd2926b2b341f77026dfe7c6ab2f5131c7` — **asserted equal to #507's manifest value before the first call**, as the pre-registration required. System `f758cbf4…dcae166`, re-prompt `f5932e0e…3a3f4c0a`. One prompt, both judges |
| judge 1 | `mango:8003` → `RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic`, probed live and asserted against `s0_common.SCOUT_EXPECT` |
| judge 2 | `mango:8004` → `Qwen/Qwen3.6-35B-A3B`, asserted against `QWEN_EXPECT`. Reasoning model; 22,276,096 thinking characters counted and stripped before parsing, not committed |
| sampling | **temperature 0**, seed `20260914`, ≤ **4** concurrent per endpoint, `max_tokens` 3,000 (Scout) / 12,000 (Qwen) |
| pairs | **308** — 208 pooled + 100 bias-bound, over the **10 development topics only**, asserted against `C.DEV_TOPICS` in both the labeler and the gate reader. No confirmation topic was read, retrieved, packed or labeled |
| **presentations** | Scout **20** per pair (k = 0..19) = **6,160** records; Qwen **10** (k = 0..9) = **3,080**. Both dense and complete — every pair has every presentation, zero incomplete pairs. Unit order seeded `SEED_LABELDUP + 100·k + pair_index`; k = 0 is the natural order |
| **#507's records are the same bytes** | All 1,540 of #507's records per judge are present in the extension file and **byte-identical** to `artifacts/r31/labels-r31-{scout,qwen}.jsonl` (checked line by line on the `(topic, docno, presentation)` key: 1,540/1,540 for each judge). `s0_label_r31.py` is idempotent on that key, so `--presentations 0:20` over a file holding k = 0..4 ran only k = 5..19 |
| **and they still reproduce #507's gates** | The gate reader recomputes r3.1's three gates from this file's first five presentations and gets `artifacts/r31/gates-r31.json` back exactly: self-consistency **0.3831 / 0.5617**, hallucinated **0.02517 / 0.00279**, whether **0.9188 / 0.9805** (Scout / Qwen). This is the check that the extension did not silently move the baseline |
| segmentation | `ragstack.ingestion.chunkers.sentence_spans`; `git diff 55a0fc2..HEAD -- python/ragstack/ingestion/chunkers.py` asserted **empty** before the first call. (The wider `python/ragstack/` tree is *not* empty against `55a0fc2` — #505 added the grading module — which is why the assertion is scoped to the segmenter, as it always was) |
| windowing | §6.5, 48,000 tokens, applied along each presentation's own unit order |
| interpreter | `/rag/envs/ragstack/bin/python3`, `HF_HOME=/rag/cache`, `PYTHONPATH=…/ragstack/python`, `STAGE0_HELPERS=…/phase0-rescue/phase0` |
| ran | Scout 2026-09-07T00:35:37Z → 02:48:38Z; Qwen 01:21:05Z → 04:05:33Z, plus an earlier Qwen segment that died (see §7). Issued in parallel, one per endpoint |
| **endpoints contacted** | `mango:8003` and `mango:8004` **only**, plus `mango:8003/tokenize` for the window budget. No Qdrant / Elasticsearch / Neo4j / tenant API. **No store client is constructed** anywhere in `s0_label_r31.py` or `s0_labelgates_r31ext.py`. No GPU on this host was selected or touched — mango is a remote service. **The analysis in this document contacted nothing at all** |
| artifacts | `artifacts/r31ext/labels-r31-scout.jsonl` (sha256 `b219331fd6a8b104…`, 17.6 MB, 6,160 records), `labels-r31-qwen.jsonl` (`50f4694b776e6769…`, 5.4 MB, 3,080), `label-manifest-r31.json` (`2dc2397cccee0d8a…`), `label-manifest-r31-{scout,qwen}.json`, `gates-r31ext.json` (`19339467 8c826cf2…`), `gates-r31ext.md`. Both label files are under the 50 MB ceiling and are committed uncompressed |

---

## 1. The three pre-registered predictions, scored

| prediction | threshold | pre-registered projection | observed | scored |
|---|---|---|---|---|
| **P-ext-1** — SB reliability of graded per-sentence support at 30 pooled readings | ≥ 0.85 | 0.8826 | **0.9205** (split-half *r* 0.8527) | **PASS** |
| **P-ext-2** — Scout's U(20) within 10 % of the asymptote of the k = 1..5 MM fit | ≥ 65.0949 | 58.7603 (i.e. *expected to fail*) | **133.8267** | **PASS**, and see below |
| — the same, with the one locator blow-up dropped | ≥ 65.0949 | — | **58.6187** | **FAIL** |
| — P-ext-2 **secondary**: the projection is accurate to 10 % | rel. err ≤ 0.10 | — | rel. err **0.0024** | **PASS** |
| **P-ext-3** — hallucinated-span rate at the NEW presentations ≤ 0.05, both judges | ≤ 0.05 | — | Scout **0.01956**, Qwen **0.01061** | **PASS** |

**P-ext-1 passes and is not fragile.** Dropping the blow-up pair moves it to 0.9233; restricting
to pairs whose union has ≥ 3 touched sentences moves it to 0.9209. The scored value is the
pre-registered estimator (every pair with ≥ 2 sentences, no exclusions); both sensitivities are
in `gates-r31ext.json` under `predictions.P_ext_1.sensitivity`.

**P-ext-3 passes with room.** Scout 260/13,291 on the new presentations, Wilson 95 %
[0.01734, 0.02206]; Qwen 19/1,791, [0.00680, 0.01651]. Both sit an order of magnitude under the
gate. Beside them, the old presentations: Scout 85/4,344 = 0.01957, Qwen 16/1,802 = 0.00888. The
copy behaviour is **flat in the unit order** — Scout's rate is 0.01957 on the old block and
0.01956 on the new, which is as close to unchanged as counting allows. Decision C (whole-sentence
anchors, r3 §10 item 3) holds under reshuffling, which is what the prediction was for.

**P-ext-2 is the one worth reading in full.** It is scored PASS and the PASS is meaningless; the
pre-registration's stated expectation was right. §3.

---

## 2. The gate table at depth

Every r3.1 gate, re-read over all readings rather than over five.

| gate | requirement | Scout (20 readings) | Qwen (10 readings) |
|---|---|---|---|
| self-consistency, k = 0 vs k = 1, union | ≥ 0.90 | **0.3831 FAIL** | **0.5617 FAIL** |
| — mean over all C(k,2) presentation pairs | reported | 0.4210 (190 pairs) | 0.5437 (45 pairs) |
| — mean pairwise raw span-union Jaccard | reported | 0.4440 (median 0.3685, n = 55,434) | 0.5439 (median 0.7588, n = 13,726) |
| hallucinated-span rate, k = 0 | ≤ 0.05 | **0.02517 PASS** | **0.00279 PASS** |
| — all readings | reported | 0.01956 (345/17,635) | 0.00974 (35/3,593) |
| — anchor split, all readings | reported | 238 first / 107 last | 27 first / 8 last |
| — the NEW presentations only (P-ext-3) | ≤ 0.05 | 0.01956 | 0.01061 |
| whether-agreement, **all** readings agree | ≥ 0.90 | **0.8539 FAIL** | **0.9610 PASS** |
| — mean pairwise | reported | 0.9581 | 0.9895 |
| **ALL THREE GATES** | conjunctive | **FAIL** | **FAIL** |

**Neither judge passes the conjunction on any reading.** The r3.1 stop stands.

**Depth costs the whether-gate, and the reason is arithmetic, not behaviour.** "All k readings
agree" is a conjunction over more terms as k grows, so it can only fall:

| readings that must agree | 2 | 5 | 10 | 20 |
|---|---|---|---|---|
| Scout | 0.9610 | 0.9188 | 0.8734 | **0.8539** |
| Qwen | 0.9903 | 0.9805 | 0.9610 | — |

Scout crosses the 0.90 line somewhere between five and ten readings. This is **not** new
instability — the *mean pairwise* whether-agreement is 0.9581 (Scout) and 0.9895 (Qwen), both
comfortably above 0.90 and both stable. The gate as r3 writes it is a conjunction, and a
conjunction read over four times as many terms is a strictly harder gate. Anyone comparing this
row to #507's must compare **at the same k**, and at k = 5 the numbers are #507's exactly.

The anchor split is worth one line on its own. #501, with ten-word anchors, had **54 of Scout's
58** failures on the *closing* anchor. Over 17,635 spans here the split is **238 first / 107
last** — the closing-anchor pathology that motivated decision C is still gone at twenty
presentations, not just at five.

---

## 3. Saturation — and a single record that ate the mean

### 3.1 What the mean says, and why you must not believe it

The pre-registered estimator for P-ext-2 is the **mean over pairs positive somewhere** of the
number of distinct evidence sentences in the union of the first k readings. Read that way,
Scout's curve is:

| k | 1 | 5 | 10 | **11** | **12** | 15 | 20 |
|---|---|---|---|---|---|---|---|
| mean distinct sentences | 12.26 | 36.82 | 46.37 | **47.73** | **122.96** | 127.06 | **133.83** |

One reading adds **75 sentences to the mean**. That is not a saturation curve; it is a defect.

### 3.2 The defect, identified by mechanism

`s0_labelgates_r31ext.py` flags it from the label record's own bookkeeping, not from a threshold
on the answer's size:

> **criterion** `spans_emitted ≥ 10 × max(spans_seen, 1)` in a single reading.
> **Flagged: 1 reading of 9,240.** Scout, topic `2016_1` / doc `4212306`, presentation 11 — the
> model offered **22** spans, the locator emitted **986**, covering **22,366** distinct
> sentences, with `split_across_units = 1`.

The mechanism is the r3.1 locator's own: when a quoted span's first and last sentence locate in
*different* units, it emits one span per intervening unit **covering that unit whole**. One
model span became 986 emitted spans covering most of a large document. The three longest run
unit 327 sentences 0–448, unit 49 sentences 0–308, unit 325 sentences 0–290 — every one starting
at sentence 0, which is the signature of whole-unit filling rather than of a model answer.

For scale: the median reading in this corpus selects **6** distinct sentences; p99 is 99, p999 is
233, and the next-largest reading after the blow-up selects 520. Qwen has **zero** flagged
readings and zero `split_across_units` events in 3,080 readings.

### 3.3 The same prediction, on the cleaned reading

Dropping the one pair the criterion identifies (299 pairs remain):

| k | 1 | 5 | 10 | 15 | 20 |
|---|---|---|---|---|---|
| Scout mean, blow-up dropped | 12.26 | 36.50 | 45.23 | 52.04 | **58.62** |
| Scout **median**, all pairs (immune to it) | 3.0 | 23.0 | 33.0 | 38.5 | **42.0** |

**P-ext-2 as written then FAILS** — 58.62 against the 65.0949 the pre-registration derived — which
is precisely what the pre-registration said in as many words: *"the fit's own arithmetic says
k = 20 reaches only 81 % of V<sub>max</sub>, so the prediction as written is expected to FAIL."*

**And the secondary — the one registered as the informative half — passes emphatically.** The
five-point fit projected **U(20) = 58.7603**; the observed cleaned value is **58.6187**, a
relative error of **0.24 %**. A Michaelis–Menten curve fitted to k = 1..5 extrapolated four-fold
to within a quarter of a percent.

The refit on all twenty cleaned points says the same thing from the other side:

| fit | V<sub>max</sub> | K<sub>m</sub> | r² | fraction of asymptote at k = 20 |
|---|---|---|---|---|
| pre-registered, Scout k = 1..5 (before the run) | 72.3277 | 4.6179 | 0.9942 | 0.8124 (projected) |
| **refit, Scout k = 1..20, blow-up dropped** | **68.3302** | **4.6368** | 0.9887 | **0.8118** (observed) |

K<sub>m</sub> moves by 0.02 and the fraction-of-asymptote by 0.0006 across a four-fold extension
of the range. The shape was right.

**The all-in refit, by contrast, is degenerate and is reported as such.** On the uncleaned
points the least-squares optimum runs to K<sub>m</sub> → ∞ — the optimiser stops at the grid
ceiling, 500.04 — which means the fitted curve is a **straight line** with no identifiable
asymptote. `mm_fit` now detects this, sets `DEGENERATE: true`, suppresses
`frac_of_asymptote_at_n` as **absent rather than reporting a number**, and prints *do not quote
this V<sub>max</sub> as an asymptote*. The linear null fits those points as well as
Michaelis–Menten does (r² 0.8614 vs 0.8602) — the diagnostic definition of "no saturation
visible". On the **cleaned** points the ordering flips the right way: MM r² 0.9887 against the
linear null's 0.8864, so the cleaned curve genuinely bends.

### 3.4 Qwen, and the pooled curve

| | k = 1 | k = 5 | k = 10 | k = 20 | k = 30 | MM fit | fraction of asymptote |
|---|---|---|---|---|---|---|---|
| Qwen, sentences | 1.61 | 3.89 | **5.37** | — | — | V 7.4873, K 4.4284, r² 0.9915 | 0.6931 at k = 10 |
| pooled, sentences (blow-up dropped) | 11.98 | 27.00 | 36.80 | 45.67 | **58.48** | V 68.4676, K 8.3806, r² 0.9682 | 0.7816 at k = 30 |

Qwen's pre-registered fit projected U(10) = 4.72; observed 5.37 — a 14 % under-projection, the
opposite sign to Scout's and outside 10 %, on a judge whose spans are an order of magnitude
smaller. The pooled curve is interleaved s0, q0, s1, q1, … then the remaining Scout readings, so
it is a staircase (a Scout reading adds many sentences, the Qwen reading after it adds few) and
its fit is the loosest of the three.

In **D3-distinct sets** rather than sentences — the r3.1 unit, Jaccard ≥ 0.5 merge, accumulated
one reading at a time — the picture is the same and cleaner, because a set count is not
inflatable by a whole-unit span:

| | k = 1 | k = 5 | k = 10 | k = 20 | MM fit | marginal gain at the last reading |
|---|---|---|---|---|---|---|
| Scout | 2.24 | 6.00 | 8.59 | **11.91** | V 16.5552, K 8.7824, r² 0.9922 | +2.74 % |
| Qwen | 1.16 | 2.80 | **3.75** | — | V 5.1908, K 4.0961, r² 0.9946 | +4.61 % |

**The answer to §3.7 item 6, at four times the depth #507 could reach, is the same answer.** The
union does not saturate. Scout's cleaned sentence union is still gaining **2.7 %** per reading at
k = 20 and stands at 81 % of its own asymptote; Qwen's is gaining **6.7 %** at k = 10 and stands
at 69 %. #507 concluded from k = 5 that "the union of plausible locations" is not yet a stable
statistic. Twenty readings do not make it one — they make the *shape* of the non-saturation
predictable, which is a different and smaller claim.

---

## 4. The reliability of graded support

Per pair: take the first *n* readings in the pooled interleaved order; the universe is the
sentences at least one of them selected; split the *n* readings at random into halves; score each
sentence in each half by the fraction of that half's readings including it; Pearson *r* across the
pair's sentences; mean over pairs; 20 seeded draws; Spearman–Brown doubles it to the full-*n*
instrument. This is the estimator fixed in the pre-registration, not chosen here.

| n readings | split-half *r* | sd over draws | **Spearman–Brown full-*n*** | top-3 overlap between halves | SB, blow-up dropped | SB, pairs with ≥ 3 sentences |
|---|---|---|---|---|---|---|
| 4 | 0.1037 | 0.0201 | 0.1879 | 0.6546 | 0.1879 | 0.1666 |
| 6 | 0.3104 | 0.0160 | 0.4738 | 0.6370 | 0.4768 | 0.4697 |
| 8 | 0.4569 | 0.0140 | 0.6272 | 0.6475 | 0.6375 | 0.6221 |
| 10 | 0.5444 | 0.0148 | 0.7050 | 0.6557 | 0.7056 | 0.7045 |
| 14 | 0.6502 | 0.0122 | 0.7880 | 0.6736 | 0.7927 | 0.7896 |
| 20 | 0.7526 | 0.0074 | 0.8588 | 0.7044 | 0.8639 | 0.8591 |
| **30** | **0.8527** | 0.0059 | **0.9205** | 0.7551 | 0.9233 | 0.9209 |

*n* = 2 is **not** on the curve. A 1-vs-1 split makes both halves binary indicators of a single
reading each, so what it measures is the agreement of two readings, not the stability of a graded
score; it is omitted rather than reported as a degenerate value.

Read across the row and down the column:

* **The instrument crosses r3's 0.90 labeler bar between 20 and 30 readings**, and only there.
  Ten readings — what #507 could have built — buy **0.705**, which is not a labeler.
* **The pre-registered projection was conservative in the right direction.** It projected 0.877
  at 30 from the ten-reading measurement by Spearman–Brown; the realised value is **0.9205**. The
  instrument lengthens slightly *better* than Spearman–Brown assumes, which is what you expect
  when the extra readings are not exchangeable with the first ten (here, ten more Scout readings
  after the Scout/Qwen interleave runs out).
* **The two sensitivity columns move it by ≤ 0.003.** The conclusion does not rest on the
  estimator's choices.
* **Top-3 overlap is the statistic that stops improving.** The two halves agree on the identity
  of a pair's three best-supported sentences **75.5 %** of the time at n = 30, up from 65.5 % at
  n = 10. If what a downstream gold actually consumes is "the top three sentences", 30 readings
  buys three-quarters agreement on that set, not 0.92.

**Where the support mass sits**, at 30 pooled readings: the union holds **132.2** sentences per
pair on the contaminated mean and **42** at the median; the top three sentences carry only
**0.3285** of all votes; and the best-supported sentence appears in **0.7509** of readings. Per
judge, the difference in character is stark — Scout at 20 readings: union median 42, top-3 share
0.3073, best sentence in **0.9087** of readings. Qwen at 10: union median **3**, top-3 share
**0.8813**, best sentence in 0.7270. **Qwen votes narrowly and consistently; Scout votes broadly
and repeatably.** A pooled graded gold inherits both.

---

## 5. Cross-judge — reliable is not valid

At the common depth (10 readings each, the deepest reading both judges support):

| statistic | value |
|---|---|
| **per-sentence support correlation, Scout (20 readings) vs Qwen (10)** — Pearson *r* per pair over the union of both judges' sentences, averaged | **mean 0.2976**, median 0.3099 |
| — pooled over all 40,588 sentences at once | 0.2657 |
| — fraction of the 297 pairs with *r* ≥ 0.5 | 0.2727 |
| — **top-3 supported sentence overlap between judges** | **0.4473** |
| κ(Scout–Qwen) on *whether*, k = 0 | 0.29 (observed agreement 0.9286; Scout positive 0.9156, Qwen 0.9805) |
| span-union Jaccard at the 10-reading unions, where both positive | mean 0.1622, median 0.1022; 5.7 % at or above 0.5 |
| asymmetric coverage, 10-reading unions | Scout's characters inside Qwen's: **0.1865**. Qwen's inside Scout's: **0.8250** |

Three things follow, and the third is the one that matters for §10 item 4.

**The asymmetry is not disagreement, it is enumeration.** 82.5 % of Qwen's selected characters
lie inside Scout's selection while only 18.7 % of Scout's lie inside Qwen's. Qwen picks a small
set that Scout almost always also picks; Scout picks a great deal more besides. This is the
signature r3 §3.7 item 5 describes as under-enumeration, and it deepened with readings — at k = 0
the split was 0.5908 / 0.2252.

**κ on *whether* stays low for the reason #507 gave**: 281 of 308 pairs are positive for both, so
agreement is 0.9286 but expected agreement is 0.8994, and κ = 0.29 is a base-rate artefact, not a
finding about the judges.

**The graded instrument is reliable without the judges agreeing.** Split-half reliability at 30
readings is 0.9205; the cross-judge per-sentence support correlation is **0.2976**, and the two
judges pick the same top-3 sentence set less than half the time. These are consistent: random
halves of a pooled 30 each contain a Scout/Qwen mixture, so the split-half statistic measures
whether *the mixture* reproduces itself — and it does, in part because Scout supplies 20 of the
30 readings and Scout is internally very repeatable (best sentence in 0.9087 of its readings).
**Reliability of the pooled instrument is therefore not evidence that the instrument measures
"where the evidence is".** It is evidence that it measures *something* stably. Which of the two
judges' somethings — or neither — is the question only a human read can answer, and no human read
was performed.

---

## 6. What this licenses for r3 §10 item 4 — as a measurement, not a decision

r3 §10 item 4 puts three options on the table for what "where" means on the CDS population. This
run measures two of them. It decides nothing; the decision is the owner's, and both of the
options it touches also need the human half that is still `PENDING-HUMAN`.

**Option (a) — graded per-sentence support as the gold.** *Measured, and the news is good with a
caveat.* The instrument reaches Spearman–Brown reliability **0.9205** at 30 pooled readings,
clearing the ≥ 0.90 bar r3 §3.7 sets for a labeler; the value is robust to both sensitivity
readings tried. What it costs is now known rather than guessed: **30 readings per pair**, because
20 buys 0.8588 and 10 buys 0.7050. For the 308 development pairs that was 3.5 fleet-hours. What
the measurement does **not** license: (i) any claim that the graded score is *valid* — §5's
cross-judge *r* = 0.2976 is the direct counter-evidence, and validity against a human is absent,
not zero; (ii) any claim about the confirmation topics, which were not touched; (iii) reading
0.9205 as though it were the whole-instrument number for a *single* judge — it is the pooled
mixture's, and Scout contributes two-thirds of it.

**Option (c) — that more presentations converge on a canonical span set.** *Measured, and the
claim does not hold.* At four times #507's depth the union is still growing: Scout **+2.7 %** per
reading at k = 20 at **81 %** of its own asymptote, Qwen **+6.7 %** at k = 10 at **69 %**, the
pooled curve at **78 %** at k = 30. The pre-registered five-point fit predicted this to within
0.24 %, so the non-convergence is not noise and not a surprise — it is the measured shape of the
process. If option (c) requires a canonical set, twenty readings do not produce one, and the fit
says roughly 40–45 readings would still leave Scout ~10 % short.

**Option (b) is untouched by this run** and is neither supported nor undermined here.

**The three span gates remain failed**, which is the state #507 left and this run confirms four
times deeper: no judge passes the conjunction on any reading, so nothing here reopens the r3 §5
step 2 stop.

**One thing this run found that is not about §10 item 4 at all**, and should be fixed before the
r3.1 locator is used for anything downstream: the `split_across_units` fallback can turn one
model span into whole-unit spans covering most of a document (§3.2). It fired once in 9,240
readings, and that once was enough to move a pre-registered headline number by 128 %. A locator
that can do that to a mean will do it to a gold. The recommended change is recorded in §10 and
not applied here.

---

## 7. Deviations

Everything that departed from the brief or from the pre-registration, and nothing omitted.

1. **The previous agent was terminated by a session limit after the data runs completed, and
   this document was finished in a second session.** The cut fell after both label files were
   complete and after `s0_label_r31.py --presentations` and a partial `s0_labelgates_r31ext.py`
   were committed (`e07df29`, "wip: state at session-limit cutoff"), and before any analysis was
   run or any result written. **No label was regenerated.** The second session ran only the gate
   reader, over files it did not create, and every number in this document comes from that one
   command.
2. **The merged manifest was rebuilt, not recovered.** The per-judge manifests
   (`label-manifest-r31-{scout,qwen}.json`, and the two smoke manifests) were written by the runs
   themselves and are original. The merged `label-manifest-r31.json` was **not** present in
   `work/r31ext/` at resume, and was regenerated by `s0_label_r31.py --merge-manifest --workdir
   r31ext`, which folds the per-judge manifests and re-hashes the prompt. It contacts no
   endpoint. Its `prompt_sha256` recomputes to `ba09e122…5131c7`, equal to both per-judge
   manifests and to #507's — so the merge is a fold of original data, not a reconstruction of it.
3. **The Qwen run died once and was restarted; its manifest therefore under-reports Qwen's
   cost.** The first Qwen segment failed at roughly 250/1,540 records with
   `httpx.ConnectError: [Errno 16] Device or resource busy` (`run/full-qwen.seg1.log`, no
   summary line written). The restart found 1,803 records already present — #507's 1,540 plus
   **263** the dead segment had produced — and ran the remaining 1,277. Because the crash
   prevented a stats line, the committed manifest's Qwen counters cover **only the second
   segment**. From the segment-1 log the lost figures are **reconstructed** as ≈ **263 records,
   ≥ 2,115 s wall, ≈ 4.8–5.0 M prompt tokens**; they are marked as reconstructed in §8 and are
   *not* folded into the manifest. This affects the cost table only. It does not affect a single
   label: the seed is a pure function of `(pair index, k)`, temperature is 0, and the resume is
   idempotent on `(topic, docno, presentation)` — the 263 records were kept and are indistinguishable
   from records the second segment would have written.
4. **No `run/smoke-scout.log` exists**, though the scout smoke ran and its manifest
   (`label-manifest-r31-scout-smoke.json`, 20 records, 22.0 s) is original. The smoke stats in §8
   come from that manifest.
5. **The gate reader was extended after the data were seen, and this is the deviation that most
   needs stating.** §3's blow-up detector, the median curve, the linear null, the degenerate-fit
   diagnostic and the two reliability sensitivities were all written **after** the k = 12
   discontinuity was noticed. They are added as *diagnostics beside* the pre-registered numbers,
   never in place of them: **P-ext-2's `SCORED` field is still computed on the pre-registered
   estimator and still reads PASS**, and the cleaned reading is reported under a separate key
   (`THE_PASS_IS_AN_ARTEFACT_UNLESS_THIS_AGREES`) that a reader can ignore. The pre-registered
   path was checked for drift after every edit: `--prefit` still reproduces `predictions.json`
   exactly (*r* 0.5444, SB 0.7050, V<sub>max</sub> 72.3277, K<sub>m</sub> 4.6179, U(20) 58.7603),
   and the min-sentences filter is applied *after* the random shuffle specifically so that
   raising it cannot perturb the seeded stream and move the scored number.
6. **`reading_ii_all_ten_presentation_pairs` is a misnomer at this depth.** The key name is
   inherited unchanged from `s0_labelgates_r31.py`, which is imported rather than copied so that
   a number appearing in both documents is the same code path. At k = 20 it holds C(20,2) = **190**
   presentation pairs, not ten. Renaming it would have forked the r3.1 arithmetic; it is
   documented here instead.
7. **`cross_judge.at_k5_unions` is likewise read at the common depth 10**, not at 5 — `G.cross`
   is called with `min(20, 10)`. The key name is r3.1's.
8. **Qwen's per-judge reliability curve stops at n = 10 and its n = 4 entry is negative**
   (*r* = −0.2483). This is not a defect to fix: at four readings, 197 of Qwen's pairs have no
   variance in one half and 74 have a single-sentence union, so the correlation is computed on a
   handful of degenerate pairs. It is reported as measured and should not be quoted.
9. **No human read, no κ against a human, no enumeration recall.** `PENDING-HUMAN`, as the
   pre-registration stated. The R-dev pairs were not read. No confirmation topic was touched. No
   store client was constructed and no endpoint was contacted by any analysis in this document.

---

## 8. Cost

| run | requests | prompt tokens | completion tokens | wall | s/record | retries | failures | truncated |
|---|---|---|---|---|---|---|---|---|
| Scout (`mango:8003`), 308 × 15 new | 5,595 | 69,319,604 | 1,446,635 | **7,981.1 s** (2.22 h) | **1.73** | 4 | 0 | 4 |
| Qwen (`mango:8004`), segment 2 | 1,518 | 21,161,969 | 5,606,539 | **9,867.6 s** (2.74 h) | **7.73** | 0 | 0 | 34 |
| Qwen, segment 1 (**reconstructed** from the log — no manifest) | — | ≈ 4.8–5.0 M | — | ≥ 2,115 s | ≈ 8.5 | — | 1 crash | — |
| smoke, Scout (20 pairs × 1) | 20 | 76,948 | 5,449 | 22.0 s | 1.10 | 0 | 0 | 0 |
| smoke, Qwen (20 pairs × 1) | 20 | 83,538 | 68,947 | 110.2 s | 5.51 | 0 | 0 | 0 |
| **total, manifest-recorded** | **7,153** | **90.64 M** | **7.13 M** | — | — | **4** | **0** | **38** |

The two full runs overlapped, one per endpoint, ≤ 4 in flight each; end to end **00:35:37Z →
04:05:33Z = 3 h 30 min**, set by the reasoning judge. LLM-side service time was 31,053 s (Scout)
and 39,226 s (Qwen), 70,786 s in total — 19.7 service-hours drawn through 3.5 wall-hours by the
two-endpoint, four-way concurrency. Qwen's thinking came to **22,276,096** characters, stripped
before parsing and not committed. **$0** — both judges are local.

Analysis is free by comparison: `s0_labelgates_r31ext.py` reads 9,240 records and computes
C(20,2) + C(10,2) consistency readings, three saturation curves and 7 × 3 × 20 seeded
half-splits in **26 s** on one core, contacting nothing.

**The stop rules, all evaluated on the smoke before the full run, and none fired.** Both served
model ids matched `s0_common`'s expectations. The smoke hallucinated-span rates were **0.000**
(Scout, 0/51) and **0.000** (Qwen, 0/22), far under the 0.5 "the prompt is broken" threshold. The
prompt sha256 was asserted equal to #507's manifest value before the first call. Projected wall
from the smoke was well inside the 6 h ceiling, and the realised 3.5 h confirms it.

---

## 9. Reproduction

```bash
export HF_HOME=/rag/cache PYTHONPATH=/home/wilke/Development/ragstack/python
export STAGE0_HELPERS=/home/wilke/Development/worktrees/phase0-rescue/phase0
PY=/rag/envs/ragstack/bin/python3
cd docs/plans/results/stage0

# 0. the pre-registration, from the COMMITTED r3.1 labels only — no endpoint.
#    This must still print r = 0.5444 / SB 0.7050 / Vmax 72.3277 / Km 4.6179 / U(20) 58.7603,
#    which is the check that the analysis edits did not move the pre-registered path.
$PY s0_labelgates_r31ext.py --prefit

# 1. seed work/r31ext with #507's records, after asserting the prompt has not moved
#    (the assertions are in §0 of this document; the copy is byte-for-byte)
cp artifacts/r31/labels-r31-{scout,qwen}.jsonl "$STAGE0_BIG/work/r31ext/"

# 2. smoke, then the extension: k = 5..19 for scout, k = 5..9 for qwen
$PY s0_label_r31.py --judge scout --limit 20 --presentations 5:6 --tag smoke --workdir r31ext
$PY s0_label_r31.py --judge qwen  --limit 20 --presentations 5:6 --tag smoke --workdir r31ext
$PY s0_label_r31.py --judge scout --presentations 0:20 --workdir r31ext
$PY s0_label_r31.py --judge qwen  --presentations 0:10 --workdir r31ext
$PY s0_label_r31.py --merge-manifest --workdir r31ext

# 3. the gate table, the curves, the reliability and the scored predictions
$PY s0_labelgates_r31ext.py                 # artifacts/r31ext/gates-r31ext.{json,md}
```

`s0_label_r31.py` is resumable and idempotent on `(topic, docno, presentation)`, which is
exactly what makes the extension possible: `--presentations 0:20` over a file that already
holds k = 0..4 runs **only** k = 5..19 and leaves the existing records byte-identical. Work
goes to `$STAGE0_BIG/work/r31ext/` and nowhere else.

Step 3 alone reproduces every number in this document from the committed label files, in about
half a minute, without contacting anything.

---

## 10. Recommended, not applied — the locator's `split_across_units` fallback

Recorded here because §3.2 found it and because applying it would change the r3.1 locator, which
is frozen and hashed.

When a span's first and last quoted sentence locate in different units, the locator fills every
intervening unit whole. On one reading in 9,240 that produced 986 spans from 22, covering 22,366
sentences. The rubric's D1 rule already says a span is contiguous whole sentences **inside
exactly one unit**, so the fallback is producing output the rubric does not admit.

Two candidate fixes, neither applied:

* **Reject rather than fill.** Treat a cross-unit span as unlocatable — it already sets the
  `split_across_units` problem flag, so the accounting exists. This is the rubric-faithful
  option; it converts one silent 986-span emission into one honest failure.
* **Clip to the two anchor units.** Emit the first anchor's sentence-to-end-of-unit and the last
  anchor's start-of-unit-to-sentence, and nothing in between. This keeps what the model actually
  quoted and discards only the interpolation.

Either way, the analysis-side guard belongs in the gate reader permanently: `mm_fit` now refuses
to report an asymptote from a degenerate fit, and every mean over a heavy-tailed count in this
family should carry the median beside it. A future pre-registration of this curve should name the
**median** as the estimator; §3 is a worked example of why.
