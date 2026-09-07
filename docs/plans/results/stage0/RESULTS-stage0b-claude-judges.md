# Stage 0b′ — the Claude judge family: Sonnet 5, Opus 5, Fable 5.1, one reading each

*Run 2026-09-06/07 against [`SPEC-confirmation-run-r3.md`](../design/SPEC-confirmation-run-r3.md)
§3.7 and §10 item 4 (owner: pursue all three options; this run adds a third model family at
three capability tiers). Protocol: revision 3.1 — whole-sentence anchors — byte-for-byte
(`s0_label_r31.py`'s `PROMPT`, sha256 `ba09e12255383321…`, its `SYSTEM`, its windowing and its
locator `parse_and_verify_r31`). Transport: the Claude Code CLI in headless mode on the owner's
account, not an API key — the Argo gateway named in `/rag/llm-api.env` is unreachable from this
host (§6). Machine gates and the cross-judge statistics are in
[`artifacts/claude/gates-claude.md`](artifacts/claude/gates-claude.md) /
[`gates-claude.json`](artifacts/claude/gates-claude.json); every number below is quoted from
there.*

> **Read this first.** One reading per pair. That buys the copy gate, the enumeration
> descriptives, and agreement with the local judges' pooled support map. It does **not** buy
> self-consistency, whether-agreement across presentations, or union saturation — those need
> more than one reading and are **ABSENT** here, not zero and not passed. Nothing in this file
> says a Claude judge would pass the r3 §3.7 conjunction. κ against a human remains
> `PENDING-HUMAN`; no agent read was substituted; the R-dev pairs were not read by any judge.

---

## 0. Provenance

| item | value |
|---|---|
| CLI | `claude` **2.1.263** (Claude Code), headless (`-p`) |
| flags, verbatim | `--model <id> --output-format json --restricted --no-session-persistence --strict-mcp-config --tools "" --system-prompt "<SYSTEM>"`, prompt on stdin, `cwd=/tmp`, ≤ 3 calls in flight, 300 s timeout, one retry on transport error only |
| isolation | a trivial call under these flags is 287 input tokens / $0.0025; without them the same call was $0.046 and a real pair $0.51 (Claude Code's system prompt, tool definitions and this repo's `CLAUDE.md` in the judge's context). The harness asserts on the first real call that the billed prompt tokens are within 2× of `len(prompt)/3.5`; it held. |
| models (served id from `modelUsage`) | `claude-sonnet-5`, `claude-opus-5`, `claude-fable-5-1` |
| pairs | the 308 development labeling pairs (+ the bias-bound sample as r3.1 built them), presentation k = 0, natural unit order |
| raw record | the CLI's full JSON per call (`result`, `usage`, `total_cost_usd`, `duration_ms`, `modelUsage`) in `/rag/tmp/stage0-conf/work/claude/raw-*.jsonl` (uncommitted); label records with `raw_response_sha256` in [`artifacts/claude/`](artifacts/claude/) |
| harness | [`s0_label_claude.py`](s0_label_claude.py), [`s0_gates_claude.py`](s0_gates_claude.py) |

## 1. What ran, and where the account's session limit cut it — three times

The account that runs this session also pays for the judge calls, so the judge run and the
agent driving it drew down the same session budget. Sonnet completed (308/308), Opus completed
(308/308); **Fable stopped at 251/308** when the limit hit for the third time, and the owner's
decision at that point was to analyse what exists rather than spend a fourth window on the
remaining 57 pairs. Every Fable number below is on **n = 251** and says so.

## 2. The gate table

| statistic | requirement | **Sonnet 5** | **Opus 5** | **Fable 5.1** (n = 251) |
|---|---|---|---|---|
| **hallucinated-span rate** | ≤ 0.05 | **0.0019** (1/525; Wilson upper 0.0107) PASS | **0.0010** (1/955; upper 0.0059) PASS | **0.0000** (0/645) PASS |
| — split first / last anchor | (#501 Scout: 4 / 54) | 1 / 0 | 1 / 0 | 0 / 0 |
| — quotes located byte-exact (first anchor) | descriptive | 509/525 | 914/955 | 633/645 |
| **self-consistency** | ≥ 0.90 | **ABSENT** (one reading) | **ABSENT** | **ABSENT** |
| **whether-agreement across presentations** | ≥ 0.90 | **ABSENT** | **ABSENT** | **ABSENT** |
| "no localizable evidence" | descriptive | 0.1006 (31/308) | 0.0065 (2/308) | 0.0120 (3/251) |
| pairs dropped / re-prompted | descriptive | 0 / 1 | 0 / 3 | 0 / 0 |
| mean evidence sets per positive pair | descriptive | 1.84 (n = 277) | **3.02** (n = 306) | 2.47 (n = 248) |
| median span length, sentences / chars | descriptive | 1 / 195 | 1 / 221 | 1 / 214 |
| spans split across a unit boundary | descriptive | 0 | 0 | 0 |
| quote inside the unit whose title it named | descriptive | 508/508 | 947/947 | 644/644 |
| pairs with every span in the abstract | descriptive | 77/277 | 36/306 | 28/248 |
| deep-section pairs (every span in unit ≥ 2) | (Stage 0: 3/308) | **105/277** | 52/306 | 44/248 |

**The copy gate is not the problem for this family.** Whole-sentence anchors (decision C) give
one hallucinated span in 525 for Sonnet, one in 955 for Opus, none in 645 for Fable; the
closing-anchor failure that was 54 of 58 for Scout under ten-word anchors does not occur at
all. Nothing in the *receipt* stands between a Claude judge and the gates. What the gates
would say about *stability* is untested (§7).

**Enumeration differs by tier, in a legible way.** Opus finds the most sets per document
(3.02), Sonnet the fewest (1.84) and declines most often (10 % "no localizable evidence" against
under 1.3 % for the other two). Sonnet also puts the most pairs entirely in the abstract
(77/277) *and* the most entirely in deep sections (105/277) — it commits to one place; Opus and
Fable spread. No served-model mismatch on Sonnet or Opus (0/360, 0/362 calls); the gate script
counts 2 Fable calls whose `modelUsage` names an additional model, while a direct read of the
251 raw records finds Fable answering every one — reported as an accounting difference, not
resolved.

## 3. Cross-judge agreement

### 3.1 Do the Claude judges pick the sentences the local judges support?

Support of a sentence = the number of the local judges' pooled readings that placed evidence
on it (r3.1 extension, #512: Scout k = 0..19 + Qwen k = 0..9 = **30**). Baseline = the mean
support of every sentence the local pool touched at all, on the same pairs.

| judge | pairs | sentences picked | mean support of picked | baseline | **lift** | picked with support 0 |
|---|---|---|---|---|---|---|
| Sonnet 5 | 277 | 626 | **10.78** | 2.86 | **+7.92** | 79 (12.6 %) |
| Opus 5 | 306 | 1,327 | **9.18** | 2.86 | **+6.31** | 288 (21.7 %) |
| Fable 5.1 | 248 | 825 | **10.80** | 5.35 | **+5.46** | 137 (16.6 %) |
| *Scout k = 0 (self-referential anchor)* | — | 3,678 | 10.97 | 2.86 | +8.11 | — |
| *Qwen k = 0 (self-referential anchor)* | — | 495 | 15.28 | 2.86 | +12.42 | — |

Every Claude judge's chosen sentences carry **three to four times** the pooled support of an
average touched sentence — as large a lift as Scout's own k = 0 reading, which is one of the 30
readings that *builds* the map and is therefore an upper anchor, not a comparable. A third
model family, trained separately, lands on the sentences the two local families converge on.
That is the first validity-shaped signal the study has, and it is a signal about *agreement
between models*, not about truth: it says the graded instrument is not measuring a Scout
idiosyncrasy. Whether the agreed-on sentences are the evidence is still the human read's
question.

Sentence-level rank correlation between "this judge picked it" and pooled support is low
(Spearman 0.12–0.14) because the vast majority of a document's sentences are picked by no one;
the point-biserial (0.24–0.28) and the lift table are the informative readings. Under r3.1's
own ten-reading pool (5 + 5) the lift is smaller (+0.65 to +1.31) and Opus's rank correlation
is ≈ 0 — the ten-reading map is too sparse to resolve it, which is #512's reliability curve
seen from the other side.

### 3.2 Agreement with each local judge

| Claude judge | vs | κ(whether) | Jaccard where both + (mean / median) | Claude chars inside local | local chars inside Claude |
|---|---|---|---|---|---|
| Sonnet | Scout k = 0 | 0.25 | 0.16 / 0.04 | 0.42 | 0.19 |
| Sonnet | Qwen k = 0 | 0.25 | 0.38 / 0.21 | 0.41 | 0.51 |
| Sonnet | Scout ∪ Qwen 10-reading union | 0.06 | 0.12 / 0.07 | **0.83** | 0.13 |
| Opus | Scout k = 0 | 0.13 | 0.19 / 0.14 | 0.39 | 0.33 |
| Opus | Qwen k = 0 | 0.50 | 0.27 / 0.23 | 0.28 | 0.67 |
| Opus | 10-reading union | 0.67 | 0.17 / 0.12 | **0.73** | 0.20 |
| Fable (n = 251) | Scout k = 0 | 0.29 | 0.19 / 0.14 | 0.40 | 0.29 |
| Fable | Qwen k = 0 | 0.75 | 0.29 / 0.27 | 0.30 | 0.65 |
| Fable | 10-reading union | 0.50 | 0.15 / 0.10 | **0.75** | 0.17 |

The same asymmetry the local judges show among themselves: 73–83 % of what a Claude judge
selects lies inside the local ten-reading union, while the union contains far more than any
one Claude reading. Single readings from different families are subsets of a common, larger
set — the diffuse "where" of #507/#512, now seen across three families. Whether-κ is a
base-rate artefact throughout (≥ 90 % of pairs positive for every judge); the observed
agreement is 0.87–0.99.

### 3.3 Among the three Claude judges

| pair | co-labeled | κ(whether) | observed agreement | Jaccard where both + (mean / median) |
|---|---|---|---|---|
| Sonnet vs Opus | 308 | 0.11 | 0.906 | 0.42 / 0.39 |
| Sonnet vs Fable | 251 | 0.23 | 0.928 | 0.46 / 0.42 |
| **Opus vs Fable** | 251 | **0.80** | 0.996 | **0.50 / 0.45** |

Opus and Fable agree with each other far more than either does with Sonnet, on *whether* and
on *where*. Two readings by the top two tiers overlap at a Jaccard of 0.50 — three times the
overlap between Scout and Qwen (#507: 0.16), and above a local judge against itself
(0.38–0.56). This is the capability gradient the owner asked about, and it points one way: the
stronger tiers converge on each other.

## 4. The capability gradient, read plainly

| question | Sonnet 5 | Opus 5 | Fable 5.1 |
|---|---|---|---|
| copies faithfully? | yes (0.0019) | yes (0.0010) | yes (0.0000) |
| enumerates? | least (1.84 sets/pair, 10 % none) | most (3.02) | between (2.47) |
| picks supported sentences? | yes (+7.9 lift) | yes (+6.3) | yes (+5.5) |
| agrees with the other tiers on *where*? | weakly (0.42–0.46) | with Fable, strongly (0.50) | with Opus, strongly (0.50) |
| cost per pair (measured) | **$0.13** | $0.30 | $0.24 |

Sonnet is the cheapest labeler that passes the copy gate and picks well-supported sentences,
but it enumerates least and declines most — the under-enumeration failure r3 §3.7 item 5 is
about. Opus enumerates most, and a fifth of what it picks has no local support at all (21.7 %
of its sentences), which is either recall the local judges lack or noise — exactly what the
human read's `missed-evidence` and `wrong-location` questions separate. Fable sits between on
breadth, copies perfectly, and agrees most with Opus. **Nothing here ranks them on validity.**

## 5. Cost

| model | pairs | total | per pair | median latency | wall (≤ 3 in flight) |
|---|---|---|---|---|---|
| Sonnet 5 | 308 | **$40.83** | $0.133 | 12.2 s | 33 min |
| Opus 5 | 308 | **$93.12** | $0.302 | 12.2 s | 30 min |
| Fable 5.1 | 251 | **$61.37** | $0.245 | 11.4 s | 7 min (final segment) |
| **total** | 867 calls | **$195.32** | | | |

Cost is the CLI's `total_cost_usd`. Opus cost more per pair than Fable because it wrote more
(1,327 sentences picked vs 825 on fewer pairs); Fable's per-token price is higher but its
answers are shorter. A full three-model reading of the 308 pairs would be ≈ $210; three
readings each, ≈ $630. The estimate before running was $22 / $54 / $110 — Sonnet and Opus
came in at roughly 2× that because the real documents are longer than the one-pair test.

## 6. Deviations, all of them

1. **Fable is incomplete (251/308)** — the account's session limit, three times; the owner
   chose to stop rather than spend a fourth window. Every Fable statistic states n.
2. **Two agents driving the run were terminated by that limit** before writing anything;
   this write-up was completed by the reviewing session from the committed gate outputs.
3. **The judge run and its driver share one budget.** This was not anticipated in the brief
   and is the reason the run was cut; recorded so the next run schedules judge calls when no
   agent needs the same window.
4. **Fable served-model count** — the gate script reports 2/251 calls with an extra model in
   `modelUsage`; a direct read of the raw records finds Fable answering all 251. Both stated;
   not resolved.
5. **One reading only** — self-consistency, whether-agreement and saturation are absent for
   this family. A multi-reading pass would cost ≈ $210 per reading across the three models.
6. **The Argo gateway** (`/rag/llm-api.env`) was not used: no TCP port on it is reachable from
   this host (ICMP answers; 443/80/8443/8080 do not). Access has been requested. A GPT judge
   waits on it; this run covers one additional family, not two.
7. The rubric is unmoved; its §6 output-format amendment for whole-sentence anchors remains
   **proposed, not applied** (#507 §8).

## 7. What this licenses, as a measurement

For r3 §10 item 4: a third family passes the copy gate outright and selects the sentences the
local pool supports most, so **option (a)'s graded gold is not a single-family artefact**. It
says nothing about whether the gold is *right* — the human read remains the only instrument
for that, and the pilot is rebuilt to ask it. For the choice of labeler if one is ever needed
for production: Sonnet at $0.13 per document passes the gate the study cares about most, and
the price of its under-enumeration is a number the human read will put on it.

## 8. Reproduction

```bash
cd docs/plans/results/stage0
export PYTHONPATH=/home/wilke/Development/ragstack/python STAGE0_HELPERS=/home/wilke/Development/worktrees/phase0-rescue/phase0 HF_HOME=/rag/cache
/rag/envs/ragstack/bin/python3 s0_label_claude.py --model claude-sonnet-5     # then claude-opus-5, claude-fable-5-1; skips pairs already labeled
/rag/envs/ragstack/bin/python3 s0_gates_claude.py --outdir artifacts/claude    # needs work/r31ext/ for the 30-reading support map
```

Politeness: ≤ 3 CLI calls in flight; no other endpoint contacted; development topics only,
asserted; no store client anywhere.
