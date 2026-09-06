# RESULTS — Stage 0b′ step 2: the development relabel under the revision-3 protocol

**Specification:** [`../design/SPEC-confirmation-run-r3.md`](../design/SPEC-confirmation-run-r3.md)
§3.7, executed as its §5 step 2. This document reports the three machine label gates for
**both** judges, the cross-judge agreement, and the decision the gates force.

**Scope note, stated first because it bounds everything below.** The two-independent-human-reader
validation (item 8, §6.6.2) is **not** in this run and was not performed. **No κ(human–human)
or κ(judge–human) appears anywhere in this document**, no agent read was substituted for a
human read, and the R-dev pairs were not read by the agent that produced these labels. The
human half stays **`PENDING-HUMAN`**.

**Verdict: `NEITHER JUDGE PASSES — stop per r3 §5 step 2.`** Both judges relabelled all
308 development pairs under the quote-primary protocol. Self-consistency is **0.645** for
`Llama-4-Scout` and **0.419** for the reasoning judge `Qwen3.6-35B-A3B`, against a ≥ 0.90
gate. The protocol change was not cosmetic — Scout's self-consistency **doubled**, from
Stage 0's 0.323 — and it was not enough.

---

## 0. Provenance

| item | value |
|---|---|
| specification | `../design/SPEC-confirmation-run-r3.md` §3.7, run as its §5 step 2 |
| **rubric** | `../design/RUBRIC-evidence.md`, sha256 `2e11f3688de916da…c747363b` — **unchanged** from Stage 0 and hashed before the first call |
| **prompt** | revision 3 (quote-primary), sha256 `49f567d77cbe1ac4…9f1e979e`; system prompt `f758cbf4d74bf3d4…` (byte-identical to Stage 0's); re-prompt string hashed in the manifest. **One prompt, both judges** |
| judge 1 | `mango:8003` → `RedHatAI/Llama-4-Scout-17B-16E-Instruct-FP8-dynamic`, probed live and asserted against `s0_common.SCOUT_EXPECT` |
| judge 2 | `mango:8004` → `Qwen/Qwen3.6-35B-A3B`, probed live and asserted against `s0_common.QWEN_EXPECT`. Reasoning model: thinking arrives in a sibling `reasoning` field, is counted, and is stripped before parsing |
| sampling | temperature 0, seed `20260914`, ≤ **4** concurrent per endpoint (§6.4 rule 5), `max_tokens` 3,000 (scout) / 12,000 (qwen) |
| pairs | **308** — 208 pooled + 100 bias-bound sample, over the **10 development topics only**; asserted against `C.DEV_TOPICS` in both the labeler and the gate reader. No confirmation topic was read, retrieved, packed or labeled |
| labeling set | `s0_label.labeling_set` re-run on the pools already in `work/`; asserted **equal** to Stage 0's `labeling_set.json`. Zero retrieval, zero embedding |
| duplicates | 31 pairs (10 %), seed `20260914`, asserted **equal** to Stage 0's `label_meta.json` `dup_indices` — the same 31 pairs both runs measured |
| segmentation | `ragstack.ingestion.chunkers.sentence_spans`; `git diff 55a0fc2..HEAD -- python/ragstack/ingestion/chunkers.py` asserted **empty** before the first call, so the segmentation is Stage 0's |
| windowing | §6.5, 48,000 tokens in the served generator's tokenizer; **2 of 308** pairs windowed, for both judges |
| interpreter | `/rag/envs/ragstack/bin/python3`, `HF_HOME=/rag/cache`, `PYTHONPATH=/home/wilke/Development/ragstack/python` |
| ran | scout 2026-09-06T04:26:05Z → 04:32:48Z; qwen 04:26:05Z → 05:13:07Z |
| **endpoints contacted** | `mango:8003` and `mango:8004` **only**. No `:9001`–`:9006`, no `:50052`, no Qdrant/Elasticsearch/Neo4j, no tenant API. **No store client is constructed** anywhere in `s0_label_r3.py` or `s0_labelgates_r3.py` (grep for every port and client name returns only the comment that says so). GPUs 6 and 7 untouched — nothing here selects a device and mango is a remote host |
| artifacts | `artifacts/r3/labels-r3-scout.jsonl` (sha256 `a7a736e302c306d3…`), `artifacts/r3/labels-r3-qwen.jsonl` (`cc32a23a4e7d9b1b…`), `artifacts/r3/label-manifest.json`, `artifacts/r3/gates-r3.json` (`4d651c150fd9ec3b…`), `artifacts/r3/gates-r3.md` |
| not committed | `work/r3/raw-r3-{scout,qwen}.jsonl` — the full raw responses **including 6.3 MB of Qwen thinking**. The committed label records keep the post-strip answer text plus a sha256 over the true raw response, so any record is checkable against the run directory |

Each label record carries `judge`, `served_model`, `prompt_sha256` and `raw_response_sha256`
alongside Stage 0's own fields, so the two runs' records are directly comparable.

---

## 1. What changed in the protocol, and what deliberately did not

Stage 0's finding A was a **protocol** failure, not a model-size failure
([`RESULTS-stage0-calibration.md`](RESULTS-stage0-calibration.md) §3 row 8, §6 A): an
index-primary protocol asked a non-reasoning model to track sentence numbers across units
with dozens of sentences, and only **64.5 %** of its verified spans sat at the indices it
claimed. r3 §3.7 answers with two changes.

**Quote-primary anchoring.** The judge is shown the article as titled units with the
sentences one per line and **no numbers at all**, and returns, per span, three verbatim
strings: the unit's `unit_title`, the span's `first_words` (first ten) and `last_words`
(last ten). Spans are located purely by string match — exact, then over the
whitespace-collapsed, case-folded map `s0_label._norm_map` already built for Stage 0's
relocation path. The named unit title is used only to **disambiguate** a quote that occurs
more than once; it never overrides the match. A quote that locates nowhere is a
hallucinated span and is dropped (the existing P.5 gate).

**A reasoning judge as the second labeler.** `mango:8004` (`Qwen/Qwen3.6-35B-A3B`,
provisioned for Stage 0 and never called because the first judge failed against itself)
labels the same pairs with the same prompt. Its thinking is emitted in a sibling
`reasoning` field rather than inline, and is stripped before parsing either way; the
stripper handles all four response shapes and is covered by `s0_label_r3.py --selftest`.

Carried over **unchanged**, so that a difference between these labels and Stage 0's is
attributable to the anchoring change and not to a rewritten task:

* the **rubric**, `../design/RUBRIC-evidence.md`, sha256 `2e11f3688de916da…` — unmoved;
* the prompt's clinical framing, worked example, enumeration rule and the
  "no localizable evidence" clause, transcribed from `s0_label.PROMPT` revision 2 with only
  the span identifier and output block replaced;
* the **labeling set** — `s0_label.labeling_set` re-run over the pools already on disk, and
  asserted equal to Stage 0's committed `labeling_set.json`. **Nothing was re-retrieved and
  nothing was re-embedded**;
* the **10 % duplicate draw and its shuffle**, seeded `SEED_LABELDUP = 20260914` and
  asserted equal to Stage 0's `label_meta.json` `dup_indices`, so the self-consistency
  numbers of the two runs are measured on the same 31 pairs;
* **D1** — a located quote is snapped **outward** to whole-sentence boundaries;
* `evidence_sets: []` as a **legal** verdict;
* the §6.5 **48,000-token windowing**, counted in the served generator's own tokenizer, so
  both judges see byte-identical windows;
* the sentence segmenter, `ragstack.ingestion.chunkers.sentence_spans`, asserted **unchanged**
  between Stage 0's pinned commit `55a0fc2` and repo `HEAD` before the first call.

### Two deliberate changes, and why

**A quote crossing a unit boundary is split into a multi-span set, not dropped.** Stage 0
counted that case as `unresolvable` and discarded the span. Under a quote-primary protocol
the judge is never given unit numbers to respect, so honouring the quote — one span per unit
it touches, each snapped to whole sentences — is the faithful reading of D1 and D2's
multi-span set. The rate is reported below.

**`index_agreement` is not reported.** There are no claimed indices to agree with; the
statistic Stage 0 invented to describe the failure has no referent under this protocol.

---

## 2. The gate table

| gate | requirement | **scout** | **qwen** |
|---|---|---|---|
| **self-consistency** (span-union Jaccard, 10 % re-presented) | ≥ 0.90 | **0.6452** (20/31) **FAIL** | **0.4194** (13/31) **FAIL** |
|   — reading: first set only | reported | 0.7742 | 0.4194 |
|   — reading: best-matching set pair | reported | 0.9032 | 0.4194 |
| **hallucinated-span rate** | ≤ 0.05 | **0.08204** (58/707 spans; Wilson 95 % upper 0.10459) **FAIL** | **0.02528** (9/356 spans; Wilson 95 % upper 0.04734) **PASS** |
|   — of which the *last*-ten-words anchor | reported | 54 (first-words anchor: 4) | 7 (first-words anchor: 2) |
| **document-level whether-agreement** (new, r3 §3.7) | ≥ 0.90 | **0.9677** (30/31) **PASS** | **0.9677** (30/31) **PASS** |
| “no localizable evidence” rate | descriptive | 0.0812 (25/308 pairs) | 0.013 (4/308 pairs) |
| spans emitted / attempted | descriptive | 689 / 707 | 347 / 356 |
| spans split across a unit boundary | descriptive | 14 | 0 |
| quotes ambiguous (>1 occurrence) | descriptive | 104 | 30 |
| quote landed inside the unit whose title it named | descriptive | 647/679 | 335/335 |
| pairs dropped (no verified span survived) | descriptive | 1/308 | 3/308 |
| mean evidence sets / spans per positive pair | descriptive | 1.809 / 2.096 (n=282) | 1.153 / 1.153 (n=301) |
| pairs whose every span is in the abstract | descriptive | 168/282 | 183/301 |
| deep-section pairs (every span outside abstract + first body unit) | descriptive (Stage 0: 3/308) | 6/282 | 80/301 |
| **ALL THREE GATES** | conjunctive | **FAIL** | **FAIL** |

### Cross-judge agreement (scout vs qwen)

| statistic | value |
|---|---|
| co-labeled pairs | 308 |
| κ(scout–qwen), pair-level binary evidence/none | **0.34** |
| observed / expected agreement | 0.9318 / 0.8967 |
| confusion (both+ / both− / scout-only+ / qwen-only+) | 281 / 6 / 1 / 20 |
| span-union Jaccard where both positive | mean **0.1605**, median 0.0793, ≥ 0.5 on 0.0676 of 281 pairs |
| asymmetric coverage (scout's chars inside qwen's / qwen's inside scout's) | **0.1622** / **0.6592** (n=281) |

### What the numbers say, read against Stage 0

| statistic | Stage 0 (index-primary, Scout) | r3 Scout | r3 Qwen |
|---|---|---|---|
| self-consistency (union) | 0.323 (10/31) | **0.645** (20/31) | **0.419** (13/31) |
| self-consistency (best-matching set pair) | 0.387 | **0.903** | 0.419 |
| hallucinated-span rate | 0.0504 (24/476) | **0.0820** (58/707) | **0.0253** (9/356) |
| whether-any-evidence self-agreement | 0.871 (27/31) | **0.968** (30/31) | **0.968** (30/31) |
| "no localizable evidence" | 0.123 (38/308) | 0.081 (25/308) | 0.013 (4/308) |
| spans per positive pair | 1.83 (audit baseline) | 2.10 | 1.15 |
| deep-section pairs | 3/308 | 6/282 | **80/301** |

**The protocol change worked, and it was not enough.** Quote-primary anchoring took
Scout's span self-consistency from **0.323 to 0.645** on the *same 31 pairs*, and under the
most generous of the three readings — best-matching set pair — to **0.903**. That is a real
effect of the design change and it vindicates r3 §3.7's diagnosis: a large part of Stage 0's
instability was the model losing its place in a numbered list. But the gate is read on the
union reading, where 0.645 is not 0.90, and the study cannot proceed on it.

**Quote-primary made the hallucinated-span rate *worse* for Scout, and the failure is
specific.** 0.0504 → **0.0820** (Wilson 95 % upper 0.105), and **54 of the 58 failures are
the `last_words` anchor**, not the `first_words` one. Stage 0's prompt printed a numbered
sentence for the model to copy from; r3's does not, so the last ten words of a multi-sentence
span have to be reconstructed, and Scout paraphrases them — a singular made plural, a
hyphen dropped, a parenthetical trimmed. **This is not a matcher artefact**: every failing
quote was re-checked by hand against the whitespace-collapsed case-folded document and is
genuinely absent, while every quote the checker accepted was present. The gate is measuring
what it is meant to measure.

**The reasoning judge copies faithfully and enumerates badly.** Qwen's hallucinated-span
rate is **0.0253** (9/356, Wilson upper 0.047) — it passes the copy gate Scout fails, and
its quotes landed inside the unit it named **335 of 335** times. But it returns **1.15 spans
per positive pair** against Scout's 2.10 and almost never declines (4/308 vs 25/308), and
its span self-consistency is **0.419** — *worse* than Scout's. A model that picks one span
out of several valid ones picks a different one when the units are reshuffled. Reasoning
bought verbatim fidelity; it did not buy stability.

**Both judges agree about *whether* and disagree about *where* — the same split Stage 0
found, now confirmed across two models and two protocols.** Each judge agrees with *itself*
on the whether-question at **0.968**, comfortably over the new r3 gate. Across judges,
observed agreement on whether is **0.932** (281 both-positive, 6 both-negative of 308).
κ = **0.34**, which is low only because the negative class is nearly empty — 26 of 308
verdicts — not because the judges disagree; the confusion counts are the honest reading.
On *where*, the span-union Jaccard between them is **0.161 mean, 0.079 median, and ≥ 0.5 on
6.8 %** of the 281 pairs both call positive.

**And the asymmetric coverage says the disagreement is under-enumeration, not confusion.**
**65.9 %** of Qwen's selected characters lie inside Scout's selection, while only **16.2 %**
of Scout's lie inside Qwen's. Qwen is picking a *subset* — usually one span where Scout
picks two or three. That is exactly the `missed-evidence` failure mode §6.6.2 audits and the
one Stage 0 §3 row 8 predicted the human read would settle. It is also why a "pick the judge
with the better hallucination rate" rule would have been wrong: Qwen's clean copy rate comes
with the enumeration failure the endpoint's denominator is most sensitive to.

**One thing did improve that the study will want.** Stage 0's R-dev draw could not fill its
deep-section stratum: only **3 of 308** pairs had every span outside the abstract and the
first body unit, which §6.6.6 flagged as abstract bias observed directly. Qwen finds
**80 of 301**. Whatever else is true of the reasoning judge, it reads past the abstract.

**Nothing here licenses moving a gate.** Neither judge passes the conjunction, so r3 §5
step 2's instruction applies as written: **stop**. The next revision has to change the
protocol again — not the sample size, not the threshold, and not the choice of judge, since
the two available judges fail in *different* ways and neither failure is fixed by preferring
the other.

---

## 3. What the next revision would have to change, stated so it is not a guess

None of these is a decision this document takes; they are the places the measurement points
at, in the house style of Stage 0 §6.

1. **Print the span's own text back for the model to copy, or stop asking for a closing
   anchor.** 54 of Scout's 58 verification failures are the `last_words` anchor and none of
   them is a matcher artefact. A protocol that asks for one anchor plus a sentence *count*,
   or that asks the model to quote a whole sentence rather than its last ten words, removes
   the failure mode without reintroducing index tracking.
2. **Force enumeration explicitly, and measure it.** Qwen returns 1.15 spans per positive
   pair and 65.9 % of what it selects is inside what Scout selects. The rubric's E3 already
   demands every alternative location; the prompt does not make the model pay for missing
   one. An enumeration-recall statistic against a small human-marked set would tell the
   study whether either judge is usable before another 308-pair run.
3. **Decide whether the endpoint needs *the* location or *a* location.** Both judges agree
   at 0.93–0.97 on whether a document carries evidence and at 0.08 median Jaccard on where.
   If `EPACK` can be defined over the union of any judge's plausible locations rather than a
   single canonical set, the stable statistic is the one the study already has; if it cannot,
   the labeler must become stable first.
4. **The two-reader human read (item 8) is now more, not less, necessary.** It is the only
   instrument that can say which of two judges that disagree with each other and with
   themselves is closer to right, and it is the thing this run cannot substitute for.

**The gates do not move.** Nothing measured here licenses relaxing ≥ 0.90, ≤ 0.05, or the
new ≥ 0.90 whether-agreement — which, for the record, **both judges passed**.

---

## 4. Deviations from the brief and from Stage 0, all of them

1. **The §6.4 rule 3 minimality audit was not run.** The brief's gate list does not include
   it and r3 §3.7 item 3 does not gate on it. In Stage 0 it was an *instrument failure* —
   the audit re-prompt returned an empty set on 29 of 29 audited pairs — so re-running it
   unchanged would have re-measured a broken instrument at ~60 extra requests. Minimality
   shrinkage is therefore **not measured** in this run and is not claimed to be anything.
   It is not `0`; it is absent.

2. **A quote crossing a unit boundary is split into a multi-span set rather than dropped**
   (§1 above). This changes the denominator slightly relative to Stage 0, which counted
   14 such spans as `unresolvable` and discarded them. Scout produced **14** split spans,
   Qwen **0**. Re-reading them as Stage 0 would (drop, count as unresolvable) moves Scout's
   hallucinated-span rate not at all — splits are not hallucinations under either reading —
   and moves 14 of 689 emitted spans. The verdict does not depend on it.

3. **`index_agreement` is not reported**, having no referent under a protocol with no
   claimed indices.

4. **The gate's denominator is the primary presentation only.** Stage 0's
   `s0_labelgates.py` summed `vstats`, and Stage 0's labeler discarded the duplicate
   presentation's verification statistics entirely. To keep the two runs comparable this
   run does the same, and reports the duplicate pass separately: Scout 7 hallucinated of 62
   attempted there, Qwen 0 of 31. The all-in sensitivity reading is Scout **0.0845**
   (65/769) and Qwen **0.0233** (9/387) — both on the same side of the gate as the primary
   reading, so the verdict is denominator-independent.

5. **Qwen truncated 5 responses** at `max_tokens` = 12,000 (`finish_reason: length`) despite
   the brief's ≥ 8k floor; those pairs took their one re-prompt, and **3 pairs of 308** ended
   with no verified span and are recorded as `dropped`, not as "no localizable evidence".
   Scout truncated **0**.

6. **`mango:8004` returns its thinking in a `reasoning` field, not an inline
   `<think>…</think>` block.** The stripper handles both (and the unclosed-block and
   stray-close-tag cases) and is exercised by `s0_label_r3.py --selftest`; on this run the
   inline path never fired and **6,290,974 characters** of thinking were stripped from the
   sibling field instead. A first pass of the smoke run read only `reasoning_content` and
   under-counted the thinking as 0; that was an accounting bug in the *manifest*, never in
   the parse, and it was fixed and re-verified before the full runs.

7. **`stage1_common.py` and `pilot_common.py` are not repo files.** They live in the Phase-0
   build tree, and `s0_common` imports both at module load. `s0_label_r3.py` appends
   `$STAGE0_HELPERS/{stage1,pilots}` to `sys.path` before importing it, defaulting to the
   working copy at `~/Development/worktrees/phase0-rescue/phase0`. Likewise
   `cds/topics_merged.json` is a build artifact and is resolved from the same tree. Neither
   is a change to Stage 0's behaviour; both are consequences of running committed code from
   a repo checkout rather than from the directory it was written in.

8. **Repo `HEAD` is `8779641`, not Stage 0's pinned `55a0fc2`.** `s0_common.provenance()`
   asserts the commit and would have refused; it is not called here. What actually matters
   is asserted directly instead: `git diff 55a0fc2..HEAD -- python/ragstack/ingestion/chunkers.py`
   is **empty**, so the sentence segmenter these labels were built on is byte-identical to
   Stage 0's. (`git diff --stat 55a0fc2..HEAD -- python/ragstack/` is empty in full.)

9. **No minimality, no human read, no κ against a human.** Item 8 remains
   `PENDING-HUMAN`. The R-dev pairs were not read by this agent, and no agent read was
   substituted for one.

---

## 5. Cost

| judge | requests | prompt tokens | completion tokens | wall | s/pair | retries | failures | truncated |
|---|---|---|---|---|---|---|---|---|
| scout (`mango:8003`) | 430 | 4,864,863 | 51,387 | **402.9 s** | **1.31** | 0 | 0 | 0 |
| qwen (`mango:8004`) | 404 | 5,365,280 | 1,635,825 | **2,822.4 s** | **9.16** | 0 | 0 | 5 |
| smoke, scout (20 pairs) | 23 | 86,027 | 2,761 | 15.4 s | 0.77 | 0 | 0 | 0 |
| smoke, qwen (20 pairs) | 22 | 90,044 | 92,530 | 158.1 s | 7.91 | 0 | 0 | 0 |
| **total** | **879** | **10.31 M** | **1.78 M** | **~57 min** | — | **0** | **0** | **5** |

The two full runs were issued in parallel, one per endpoint, ≤ 4 in flight each; wall-clock
end to end was **47 minutes**, set by the reasoning judge. Scout's 430 requests are
308 pairs + 31 duplicates + 2 window continuations + **89 re-prompts**; Qwen's 404 are
308 + 31 + 2 + **63**. LLM-side service time was 1,486 s (scout) and 11,179 s (qwen).

A third accounting-only Qwen call of 3 requests / 35 s was made between the smoke and the
full run to verify the thinking-token counter after deviation 6; its output was deleted and
is not part of any label file.

**Throughput check against the brief's stop rule.** At smoke throughput the projected full
runs were 4 min (scout) and 41 min (qwen), against a 6-hour ceiling; the realised figures
were 6.7 min and 47 min. The smoke hallucinated-span rates were 0.029 (scout, 1/34) and
0.000 (qwen, 0/22), far under the 0.5 "prompt is broken" threshold. Both served model ids
matched `s0_common`'s expectations. **No stop rule fired**, so the full run proceeded.

---

## 6. Reproduction

```bash
export HF_HOME=/rag/cache PYTHONPATH=/home/wilke/Development/ragstack/python
PY=/rag/envs/ragstack/bin/python3
cd docs/plans/results/stage0

$PY s0_label_r3.py --selftest                     # offline; contacts no endpoint
$PY s0_label_r3.py --judge scout --limit 20 --tag smoke   # the smoke run
$PY s0_label_r3.py --judge scout                  # the full development set
$PY s0_label_r3.py --judge qwen
$PY s0_labelgates_r3.py                           # artifacts/r3/gates-r3.{json,md}
```

`s0_label_r3.py` is **resumable and idempotent**: it reads any labels already in its output
file and runs only the pairs missing from it, so an interrupted run is restarted by
re-issuing the same command. `STAGE0_HELPERS` points at the directory holding
`stage1/stage1_common.py` and `pilots/pilot_common.py` (the Phase-0 build tree; these are
not repo files).
