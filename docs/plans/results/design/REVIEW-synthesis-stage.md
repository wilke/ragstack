# Review — `SPEC-synthesis-stage.md` (#517), independent, 2026-09-07

*Reviewer had no session context from the author. Every claim in the spec was checked against
the documents it rests on: r3 (§3.1, §3.2, §3.4, §3.5, §3.6, §5, §11), r2 (§6.1, §6.6, §7.2,
§7.3, §7.4, §8.1–8.5, §11, P.9), #506, #512, #514, `docs/plans/grading-ui.md`,
`contracts/schemas/grading_*.json`, `python/ragstack/llm.py`, `api/routers/query.py`,
`config.py`, `/rag/config/unified.env`, and the run directory `/rag/tmp/stage0-conf/work/`. No
model, store or GPU job was run; no artifact was modified. The changes I am confident in are
applied to the spec in the same PR, each marked `<!-- review: … -->`, with a §9 change log in
r3 §9's pattern, for the owner to accept or reject.*

---

## 1. Verdict

**Needs redesign in three places; otherwise sound with listed changes.** The stage asks the right
question — evidence delivery is a proxy and nobody has measured the answer — and its endpoints,
arms and contrasts are the right *shape*. But as written it cannot run and could not be read
confirmatorily if it did. (i) **It is a calibration pass presented as a confirmatory one**: the
177 pointed queries are r3 §11's *Stage 0b′ sizing set* on dev-topic documents, the population is
still a *candidate* pending guard 1, and the study's own spine (r2 §2.1, P.9) makes everything on
dev exploratory; the confirmatory synthesis contrasts belong on the confirmation-run pointed set
after freeze, and the spec needs the same two-stage shape as r3. (ii) **The primary endpoint is
not resolvable at the stated margin at this n**: a binary `Correct` at n = 177 is non-inferior at
ε = 0.05 only if fewer than ~1 in 18 queries change verdict between arms (d ≤ 0.056), where Stage 0
measured 0.10–0.40 on the analogous binary; the spec must gate on measured discordance and print
δ80, exactly as r2 §7.4/§8.5.7 row 4 did. (iii) **Two of the load-bearing mechanisms do not
exist as described**: the packed contexts "already produced by Stage 0b′" have not been produced
(r3 §5 steps 3–7 are unrun; #506 ran no retrieval), and the `citation-feedback` grading kind
cannot carry an (answer, gold) read — its verdict enum is the six *evidence* verdicts and there is
no answer field — so "reuses the R-dev machinery exactly" is false and a contract change is
required. Beyond those, the judge plan is under-specified where it matters (no claim segmentation,
no Faithful calibration, no blinding statement, a transport that cannot do 25k calls), the
generation prompt is not production-shaped and production's own context budget would truncate a
16k context eight-fold, and the reasoning generator cannot answer at all under `max_tokens 512`
with thinking on. All of these are fixable without changing the stage's intent, and the fixes are
in the PR.

---

## 2. Blocking findings

| # | section | source checked | discrepancy | change applied |
|---|---|---|---|---|
| B1 | §3, §4, §7 | r3 §11 guards 1 and 3; #506 "Scope" and §11; r2 §2.1, P.9 | The 177 queries are the **Stage 0b′ sizing set** ("generates ≥ 150 queries on the dev topics' documents and measures σ_d"); the confirmation-run pointed set (≤ 600) does not exist. #506 says the population is "a *candidate* second population, not an adopted one" until guard 1 is read, and guard 1 has not been read (no retrieval has run on it). Everything on dev is calibration by r2 §2.1. The spec pre-registers S1–S3 as confirmatory on this set. | §3 and §7 restated as two stages: **calibration** on the 177 (σ_d and discordance of `Correct`, judge κ, cost, window check, discrimination) and **confirmatory** contrasts on the confirmation-run pointed set after freeze; guard 1 named as a precondition |
| B2 | §4 | r2 §7.4 (binary-endpoint arithmetic), §8.5.1 (constant 2.802), §8.3 (δ80); Stage 0 §Row 4 (d = 0.10–0.40) | `Correct` is per-query binary. Resolvability at ε = 0.05, n = 177, one-sided α = 0.025, 80 % power needs σ_d ≤ 0.05·√177/2.802 = **0.237**, i.e. discordance **d ≤ 0.056**. At d = 0.10 / 0.20 / 0.40, δ80 = 0.067 / 0.094 / 0.133. n for ε = 0.05 at d = 0.20 is 628 — over the §11 cap of 600. No σ_d requirement, δ80 or discordance gate is stated. | Requirement and the d ↔ δ80 table written into §4; discordance measured at calibration and gating whether S1 is confirmatory (r2 §8.5.8 step 3); graded `Correct` (0 / ½ / 1) added as a pre-registered secondary with its own σ_d printed |
| B3 | §4 | r3 §3.6 (joint power, α of the conjunctive component); r2 §8.1.1 (ledger) | "Conjunctive with `Faithful`: unsupported-claim rate must be non-inferior" names no ε, no α, no joint power; the ledger does not say which of the 8 × 3 × 3 cells are confirmatory. Every superiority contrast in r3 §3.5 states α = 0.025 for its conjunctive component and r3 §3.6 makes joint power the gate. | ε_F = 0.05, α = 0.025 one-sided, joint bootstrap power per r3 §3.6; the confirmatory cell fixed as (production generator, `hybrid` + rerank, B = 16,384); ledger row added |
| B4 | §7 | r3 banner (third update: "The stop at step 2 stands"; §5 steps 3–7 unrun); #506 "This run … runs **no retrieval and no scoring**"; `/rag/tmp/stage0-conf/work/packed.json` (Stage 0: B = 4,096, dense + rerank, CDS `summary`, 7 arms) | "Contexts: already produced by Stage 0b′ packing for every arm; zero retrieval cost" — no 16k / three-mode / `nbr*` / pointed-set packing exists. Nothing has been retrieved for the 177 queries. | §7 restated: runs *after* r3 §5 step 4 produces the packs; the pointed retrieval pass is a dependency, not a sunk cost |
| B5 | §6 | `contracts/schemas/grading_verdict.json` (enum = the six §6.6.2 evidence verdicts, `additionalProperties: false`), `grading_task.json` (no answer field), `grading-ui.md` §3.1/§4 phase 5 (`citation-feedback` = a user's judgement of *citations*), `s0_rdev_score.py` (consumes the six verdicts) | An (answer, gold) calibration read needs an **answer** text, a **gold** sentence, and a verdict vocabulary about *correctness* (correct / partially / incorrect / abstained). The contract has none of these: the only free slots are `extra_questions` (yes-no / text) and the `verdict` field is required and evidence-typed. "Reuses the R-dev machinery exactly" is false; the scorer would mis-read the export. | §6 now states the contract change: a fourth kind `answer-read` with an `answer` field and its own verdict enum, landed per the repo rule (schemas + OpenAPI → Python → Go → conformance) before the calibration read; the interim `extra_questions` encoding is named as the only stop-gap and forbidden for the quoted κ |
| B6 | §2, §6 | r2 §6.6.2–6.6.4 (two readers, blind to arm, verdict vocabulary, κ tiers, positive-class agreement); r2 §6.4 rule 4 | `Faithful` needs claim segmentation and nobody is named to do it — a judge that segments *and* judges scores its own segmentation (the r2 §6.4/§6.6.6 self-agreement problem). The 100-pair calibration is "(answer, gold) pairs" — it calibrates `Correct` only; `Faithful`, the endpoint the conjunctive gate rests on, is uncalibrated. Blinding of readers to configuration/generator and to the judge's verdict is not stated. Only the ≥ 0.60 tier is carried; r2's < 0.40 stop and the positive-class-agreement alternative are dropped, and κ at n = 100 has a 95 % half-width ≈ 0.16–0.20, so a point estimate of 0.60 is read with a lower bound near the stop line. | Deterministic segmentation pre-specified (`ragstack.ingestion.chunkers.sentence_spans` over the answer; sentence + its `[n]` citations = one claim; uncited non-abstention sentence = unsupported by rule); a second calibration set of (claim, cited chunk) pairs; readers blind to arm, generator and judge verdict, order seeded per reader; r2 §6.6.4's full tiering with CIs and positive-class agreement |
| B7 | §5 | `python/ragstack/llm.py` (`_SYSTEM_PROMPT`, `RagGenerator._format_context`, `_passage_text`), `config.py:824` (`llm_max_context_chars = 8000`), `query.py` (`expand` then `generate`) | Production cites `[n]` **per source in rank order** ("Cite the passages you used as [n]"), concatenates `context_window` neighbours under **one** number with `(context before)/(passage)/(context after)` delimiters, abstains free-form ("say you don't know"), and packs at most **8,000 chars ≈ 2k tokens** (× (2w+1) with neighbours). The spec's prompt uses `[chunk_id]`, per-claim citation, a fixed abstention sentence, and a 16k-token context that production would cut ~8×. Neither side says which should change. | §5 restated: use `RagGenerator`'s formatting (`[n]`, neighbour blocks) with `max_context_chars` raised to fit the packed budget, an offline `[n] → chunk_id` map, and **two named deviations** (fixed abstention sentence, so `Abstains` is deterministic; the context budget). The 8k-char production default is recorded as a finding the ledger owes the product owner |
| B8 | §4, §5 | #512 §0 (Qwen3.6-35B-A3B: reasoning, `max_tokens` 12,000, 22 M thinking chars stripped; 3.7k completion tokens/record); `llm.py` (`extra_body` / `chat_template_kwargs enable_thinking`; empty content raises) | A reasoning generator under `max_tokens 512` spends the budget thinking and returns empty `content`, which `OpenAILLM.complete` raises on. `mango:8000` Qwen3.6-27B is the same family and plausibly the same. The spec does not say whether thinking is on, or how the answer budget is separated from the thinking budget. | §4 requires, per generator, the served `extra_body` (thinking off, production's pattern) **or** a stated thinking budget with the answer budget separate; recorded in the manifest |
| B9 | §7 | #514 §1, §5, §6 items 3 and 6 (CLI on the owner's account; $0.133/pair measured at ~12k-token prompts; the run hit the session limit **three times in 867 calls**; Argo unreachable); Sonnet 5 list price $2 / $10 per MTok | "≈ 12.7k Sonnet calls ≈ $0.02 each ≈ $250, or free on the gateway" states no basis; there are **two** judge tasks per answer (`Correct`, `Faithful`), so ≈ 25k calls; and the only transport that has been used cannot do 25k calls (it could not do 867). At list price a `Correct` call (~1k tokens) is ≈ $0.003 and a `Faithful` call with cited chunks (2–8k tokens) ≈ $0.005–0.02; at #514's *measured* effective rate (~$0.011/1k tokens, 4× list) the same calls are $0.01–0.09. | §7 gives both bases, the two-task count, names the transport (API key or Batches, not the CLI), and cuts the judged grid to the confirmatory cell plus a seeded descriptive sample |
| B10 | §2, §3 | #512 §4 ("best-supported sentence appears in 0.7509 of readings; top three carry 0.3285 of votes"; Qwen narrow, Scout broad); r3 §10 item 4(a) (graded `EPACK` = fraction of **support mass**, not a threshold); #512 §0 (support exists for the **308 labeled pairs** only) | "pooled support ≥ 0.5" is derived nowhere — neither #512 nor `gates-r31ext.md` reports the fraction of touched sentences at or above 0.5, and r3's own graded definition is mass-weighted. A cited chunk from a document outside the 308 labeled pairs has no support map, so `Cited precisely` is undefined for it and the spec does not say what happens. | CDS `Cited precisely` restated as the support mass of the cited text (r3 (a)), with a threshold sweep {0.25, 0.5, 0.75} descriptive; denominator = citations into labeled pairs, unlabeled-citation rate printed |
| B11 | §2, §3 | #506 §6.1 (2 of 10 gold sentences do not answer the question: `pq_0054`, `pq_0017`); r3 §11 guard 2 (gold = construction ∪ alternatives; D4 any-set); #506 §2 (gold is **located by pass C after the query**, not "written from a known sentence") | `Correct` = "states the fact the gold sentence states" targets the gold, not the question; on 2/10 hand-read queries those differ. `Abstains correctly` conditions on "no admitted chunk contains the gold", but D4 counts any valid location, so an answer from a valid *alternative* passage is scored as a false answer under "no evidence". On CDS there is no gold and the condition is undefined. §1's "written from a known sentence … measurement, not a judgment" overstates the construction. | `Correct` := answers the question in agreement with the gold; a pre-registered arm-invariant exclusion for queries whose gold the pointed human read (r3 §11 guard 2's extra question, plus "does the gold answer the question?") marks as not answering; `Abstains` conditions on the union gold once it exists, and until then carries the alternative-passage rate as its bound; CDS abstention is a raw rate, `DESCRIPTIVE`; §1 corrected |
| B12 | §2 vs §7 | r3 §3.4 (3 modes), §3.5 (5 delivery arms: `parent256`, `nbr1_512`, `nbr1_256`, `nbr2_512`, `multi256+1024`), §3.2 (3 budgets), Stage 0 table (7 arms) | §2 says "index arm × delivery arm × retrieval mode × budget" as if crossed; delivery arms are transforms on specific index arms, not a crossed axis. §7 counts "8 arms" — r3 has 6 index + 5 delivery = 11 scoring arms (Stage 0 had 7); no 8 exists. Budgets are absent from §7. The full r3 grid is 177 × 11 × 3 × 3 = 52,569 generations, not 12,744. | Cells enumerated in §4/§7: confirmatory cell (5 arms × 1 mode × 1 budget × 1 generator = 885 generations); descriptive families listed with their counts |
| B13 | §2 vs §7 | r3 §3.4 (Stage 2: served path on the dev tenant holds the shortlist only); r2 §9 | §2 wants "retrieval latency p50/p95 per configuration, timed on the served path" while §7 says "zero retrieval cost". Only the shipping index is on the served path; other arms reach it at Stage 2 for the shortlist. | Retrieval latency scoped to Stage 2's shortlist; offline harness timings labeled as not serving latency |

## 3. Non-blocking findings

| # | section | finding | applied? |
|---|---|---|---|
| N1 | §6 | **Judge ≠ labeler rule is needed and is currently satisfied.** Sonnet 5 labeled the 308 dev pairs (#514). Today the CDS support map is Scout + Qwen only (#512) and the pointed gold is construction (Scout), so a Sonnet judge is cross-family from every gold it scores against. The rule that keeps it so — the judge's own labels never enter a gold it is scored against; if the union gold later folds in Sonnet's #514 sets, the judge switches — is now written. r2 §6.6.6's *shared-bias* caveat still applies and is quoted. | yes |
| N2 | §4 | **Scout wrote the questions and picked the gold** (#506 §2: passes A–D on `mango:8003`). Scout as generator answers questions it authored from its own paraphrase vocabulary; the `generator × arm` family's Scout column may be easier for reasons unrelated to chunking. Not a judge circularity; a named confound. | yes, as a limitation in §8 |
| N3 | §6 | Two judges are named ("Sonnet … or the Argo gateway's GPT when reachable"). #514 §6.6: Argo is unreachable from this host. One judge must be pinned at freeze; a second family is a sensitivity, never pooled. | yes |
| N4 | §7 | **Human cost is uncosted.** A third read on the same two readers (after R-dev ≥ 100 and the pointed ≈ 50): 100 (answer, gold) + ~100 (claim, chunk) pairs × 2 readers at 2–4 min ≈ 13–27 person-hours + adjudication (r2 §11's rate for evidence pairs is 4–7 min). | yes |
| N5 | §6, §7 | The calibration readers are the R-dev readers, on the same dev documents; reading model answers about a document before reading its evidence could anchor the evidence read. Order the reads (R-dev first) or draw calibration pairs from documents outside the R-dev draw. | yes |
| N6 | §5 | "Temperature 0" on vLLM with continuous batching is not deterministic (r2 §6.4 rule 4, measured). Record seed; report duplicate-rate on a 5 % re-run. | yes |
| N7 | §4 | Ceiling risk: Phase 0 measured Leg B-style queries at PH@10 ≈ 0.97–0.99 (r3 §11 (i)), and at 16k the fine arms pack the whole pool. `Correct` may sit near 1.0 for several arms, which makes the contrast undiscriminating whatever the n. A [0.15, 0.90] window per arm on the calibration set, as r3 §3.1 applies to every endpoint, is added. | yes |
| N8 | §1 | "The decision ledger in the study report" — the PR body says "section 09 decision ledger"; no such document is in the repo (`grep -ri ledger docs/` finds only the exposure and multiplicity ledgers). Cite its path or drop the claim. | noted, not changed |
| N9 | §4 | `mango:8000` Qwen3.6-27B is called "one larger"; it is a 27B dense model beside a 35B-A3B MoE, and ANSWER-sufficiency §4 notes it is the same family as `:8004`. Fine as a generator arm; the label should be "dense" not "larger". | yes |
| N10 | §3 | #506 §5.1 flags `pq_0241` (a 928-unit conference-abstract volume) for the retrieval pass to decide on; the synthesis stage inherits whatever that pass decides and should say so. | yes |
| N11 | §2 | "from the serving log" — the offline harness calls the vLLM endpoints directly; token counts come from the chat-completions `usage` field. Minor. | yes |
| N12 | §2 | The secondary "normalised string containment of the gold's key entity/number" is not computable as written: the record's `entity` is the generator's declaration, present in the front matter 45.5 % of the time (#506 §3), and the question asks for a *value*. A deterministic extraction (numerals and rare terms in the gold sentence absent from the question) is pre-specified instead. | yes |

## 4. Every number and claim checked

| spec says | source | finding |
|---|---|---|
| pointed set = 177 queries, gold by construction, one per document | #506 headline, §5.5, D-3 | 177 ✓; one per document ✓; "by construction" overstated — gold is *located after* the query by pass C (B11) |
| "each query was written from a known sentence" | #506 §2 | ✗ written from a paraphrase of a *section* (pass A→B); sentence chosen by pass C |
| S1 = `fixed_tok512` − `fixed_tok1024_ov0pct`, control − candidate | r3 §3.6 N1 | ✓ direction and control match |
| S2 = `nbr1_512` − `fixed_tok512` | r3 §3.5 R4 | ✓ |
| S3 = `fixed_tok256_ov0pct` − `fixed_tok2048_ov0pct` | r3 §3.5 R1 | ✓ |
| ε = 0.05, one-sided α = 0.025 | r3 §3.6, r2 §8.3 | ✓ values; ✗ no σ_d requirement, δ80 or discordance gate (B2) |
| Holm α = 0.05 across S2, S3, bar 0.05 | r2 §8.1.1, r3 §3.5 | ✓ form; ledger incomplete (B3) |
| "conjunctive with Faithful" | r3 §3.6 | ✗ no ε, α, joint power (B3) |
| generators `mango:8003` Scout (production), `:8000` Qwen3.6-27B, `:8004` Qwen3.6-35B-A3B | `/rag/config/unified.env:47-48`, ANSWER-provenance §(mango table), ANSWER-sufficiency §4 | ✓ endpoints and ids; `LLM_MODEL` is Scout on all tenant envs ✓; "larger" is loose (N9); thinking budget unstated (B8) |
| 177 × 8 × 3 × 3 = 12,744 | arithmetic; r3 §3.4/§3.5/§3.2 | arithmetic ✓; "8 arms" matches nothing in r3 (11) or Stage 0 (7); budgets omitted (B12) |
| "~5 s each" | #512 §8 (Scout 1.73 s/record at ≈ 12.4k prompt tokens; Qwen 7.73 s with reasoning) | unmeasured for this prompt shape; plausible for Scout at 16k SFR ≈ 20k Scout tokens, low for a reasoning generator |
| "4 in flight ≈ 4.5 h" | 12,744 × 5 / 4 = 15,930 s = 4.4 h | ✓ arithmetic; three endpoints can run in parallel at ≤ 4 each, so it is a per-endpoint figure |
| judge "$0.02 each ≈ $250" | Sonnet 5 $2/$10 per MTok; #514 §5 $0.133/pair measured | no basis stated; two tasks per answer; transport cannot do it (B9) |
| "free on the gateway" | #514 §6.6 | Argo unreachable; access requested |
| "Contexts already produced by Stage 0b′" | r3 banner + §5; #506 scope; run dir | ✗ (B4) |
| "does not touch confirmation topics" | #506 §1 (−578 confirmation relevants, asserted in code) | ✓ |
| "reuses the R-dev machinery exactly" / `citation-feedback` | `grading_verdict.json`, `grading_task.json`, `grading-ui.md` §3.1, phase 5 | ✗ (B5) |
| κ(judge–human) ≥ 0.60 on 100 pairs | r2 §6.6.2 (≥ 100 pairs sized for *error-rate* detection), §6.6.4 (tiers) | 100 is r2's floor; κ CI half-width ≈ 0.16–0.20 at n = 100; tiers and positive-class agreement missing (B6) |
| pooled support ≥ 0.5 on CDS | #512 §4, §5; r3 §10 item 4(a) | threshold underived; support map covers 308 pairs only (B10) |
| "false answer rate under no evidence" | r3 §11 guard 2; #506 §6.1 | conditions on a single location where D4 admits alternatives (B11) |
| §5 prompt "production-shaped" | `llm.py`, `config.py:824` | ✗ four departures and an 8× context-budget mismatch (B7) |
| Temperature 0, max tokens 512 | `OpenAILLM.complete` defaults | ✓ defaults match; determinism caveat (N6); reasoning models (B8) |
| "topic-level (pointed: document-level) cluster bootstrap" | #506 D-3 (one query per document) | ✓ document = query on the pointed set |
| "retrieval latency … timed on the served path" vs "zero retrieval cost" | r3 §3.4 Stage 2 | contradictory (B13) |
| judge ≠ generator (Sonnet vs Scout/Qwen) | r2 §6.1 cross-family rule | ✓; judge ≠ labeler rule added (N1) |

## 5. What I did not change

The stage's intent, endpoints, arm list and contrast ids; the choice of Sonnet 5 as the judge;
the choice of the pointed set as the primary population. Where a change needs the owner (the
contract change in B5, the transport in B9, the prompt decision in B7) the spec now states the
decision and its default rather than making it.
