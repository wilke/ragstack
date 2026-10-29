# Round 2 plan — ordered steps, owners, budgets (lab book, 2026-10-29)

> **Lab-book tier.** Dated, append-only, superseded-not-deleted. Nothing here constrains the design; the
> design is `PREREG-glossbench-round2.md`. Token and call figures are **design estimates** from round 1's
> measured rates (lab book Part 1; gateway counts run ≈ 3.4 chars/token, so ± 20%), not results.

Owner key: **O** = project owner, **F** = Fable (plan/review), **S** = subagent (implementation, one
worktree each), **partner** = the papers-OC owner. The partner set no cap and no end date on the gateway;
the per-batch notice stays. Nothing below runs until the prereg is frozen, except step 0.

## Phase 0 — before freezing (done 2026-10-29; no gateway, no store writes)

| # | step | owner | state |
|---|---|---|---|
| 0 | Lab-book addendum correcting "85 against 65" | F | **done** (#860; `../../glossbench-2026-10/LABBOOK.md`, addendum 2026-10-29) |
| 1 | Owner decisions | O | **done** (prereg §11) |
| 2 | Harness: (a) `glossary_file.py rederive`; (b) `--shuffle-rendered` in the backfill and `part4-glossary`; (c) arm-blind pool + strata + reach gate; (d) drafter + layer 1 + verifier + leak guard + acceptance log; (e) wording proxy; (f) found@10 runner + analysis + `swap.sh`; (g) round 1's gold scripts committed; (h) read-only chunk exporter | S, F review | **done**: #861 (a, b, e), #863 (c, d), #864 (f, g), #865 (h), merged at `e3002ba` |
| 3 | Prereg v1 → owner's go → **freeze** (sha into the private `PREREG.sha256`; README procedure) | F, O | v1 drafted; freeze pending |
| 3b | **Live-ops plan** for every bench-store touch (steps 5, 9, 12, 13): bench indices only, index-name guard as `swap.sh`, dry run → apply → census, stop by pid, restore to `rules/3@file/1` | F | pending; precedes step 5 |

## Phase 1 — gateway-dependent, front-loaded

Round-1 anchors (lab book): papers-OC 16.6k prompt tokens and 1.3 calls per paper; ASM 13.9k per article;
OA 6.6k per non-skipped unit; Gemini judging 60 docs = 0.71M in / 20k out; part-4 generation ≈ 840 in /
210 out per call.

| # | step | owner | calls | in | out | notes |
|---|---|---|---|---|---|---|
| 4 | `export_chunks.py` per bench index (read-only); `glossbench_r2_pool.py build --set O|A|P` **without** the frontier file (reach gate, `dry` reading) | S | — | — | — | counts manifest only |
| 5 | **Frontier glossary (GPT-5.5), eval papers only** (3 corpora × 25 papers; OA articles + their ≥ 1,500-char float units): `glossary_step.py --llm on` against the gateway, concurrency 4, `fallback_model ""`; rerun once with `--reuse` for degraded docs | S, notice to the partner before/after | ~150 (+~30) | **~1.3M** | ~0.1M | the `/v1/models` probe must list the exact id |
| 6 | Rebuild strata with the frontier included (`final` reading); the reach gate decides each corpus's retrieval leg | S | — | — | — | GATE-NOT-EVALUABLE is a valid outcome |
| 7 | **Draft O and A** (`preflight --stage draft`, notice, `draft --notice-acknowledged`; 25 papers × 12 S1 + 2 S2 + 2 S3 ≈ 400 chunks × 1.3 attempts per corpus) | S, notice | ~1,050 | **~1.0M** | ~0.25M | drafter blind to glossaries |
| 8 | **Draft P** (papers-OC, 25 papers; expect a substantial reject share) | S, notice | ~500 | **~0.5M** | ~0.12M | within the partner's standing approval |
| 9 | `check` (layer 1, offline) then **`verify`** (layer 2, Claude CLI, off-gateway; `preflight --stage verify` first) on every draft of 7–8 | S | ~1,550 CLI | — | — | CLI dollars, not gateway tokens (~$25 estimate) |
| 10 | Gold sample (`build_sample.py`, 45 docs, 6 arms) → **Gemini judge pass** (`judge.py run --judge gateway`) | S, notice | ~50 (+ retries) | **~0.7M** | ~0.03M | Claude + Codex judges run off-gateway in Phase 2 |
| | **Phase-1 gateway total (core)** | | **~1,750** | **~3.5M** | **~0.5M** | our pause: 5.0M in / 0.75M out / 4,000 calls, cumulative across stages (`prior_gateway_usage`) |

Order inside Phase 1: 4 → 5 (the frontier must precede chunk sampling) → 6 → 7/8 in parallel → 9 → 10.
Steps 7–8 do not depend on 6's gate: generation on a gated-out corpus still feeds the proxy and
precision. If the window is tight, priority is **5, 7, 9, 10, 8** (P is a replication; O and A carry the
family).

## Phase 2 — no gateway needed

| # | step | owner | touches a store? |
|---|---|---|---|
| 11 | Layer-3 owner spot-check (`spot-export` / `spot-import`, 40 per set, blind) | O | no |
| 12 | **Retrieval O and A**: per arm {none, rules, scoutp, qwen38p, qwen36p, frontierp, qwen36p-shuf}, `swap.sh` → `found10.py run` (base, bm25, norerank in one run) + one `rules` repeat (`--repeat 2`); restore `rules/3@file/1`; census before/after | S under the 3b plan | **yes** (bench copies of OA-1000 and ASM-1000 only) |
| 13 | **P and R1** on the papers-OC bench copy, same procedure, the 155 held privately on our host; the partner's read-only re-measurement over the same swaps as an optional cross-check | S under 3b; partner optional | **yes** (bench copy of papers-OC only) |
| 14 | Claude (CLI) and Codex (CLI) judge passes; the owner's 40 blind labels on the committed page | S, O | no |
| 15 | `wording_proxy.py` (offline), `found10.py analyse --proxy`, `analyse_gold.py`, `human_agree.py`; `RESULTS.md`, counts-only artifacts with provenance, `DEVIATIONS.md` | S, F review | no |
| 16 | Optional: the partner's acceptance of P (if it decides to); P's status label set accordingly | partner, O | no |
| 17 | Model decision (the partner's point e): cost, speed, licence, these results | O | no |

## Addendum, 2026-10-29 (review): set P under the partner's sampled acceptance

Prereg §3.4 (v1.1). Steps 8 and 16 above are superseded as follows; the rest stands.

| # | step | owner | calls | in | notes |
|---|---|---|---|---|---|
| 8a | **Set-P harness**: field-restricted drafter mode, `field_not_in_binding`, `terms_leak`, dimension check, `in_set` 13/13 assertion, constants cap, 2 : 1 quota, adapter to the partner's record format | S, F review | — | — | before 8b; PR number into `README.md` |
| 8b | **Pilot**: ≈ 50 records from 2–3 papers → layers 1–3 → adapter → the partner's scripted checks; format only, data for no hypothesis; pilot papers into `exclude_papers` | S, partner | ~70 | ~0.1M | after freeze |
| 8 | **Draft P** against `binding_fields_261029.json` (sha re-recorded), 25 papers, the S3 quota per D16 | S, notice | ~500 | ~0.5M | within the partner's standing approval |
| 16 | **The partner's acceptance**: scripted checks on every accepted record + a judged sample of 50 (seed recorded) → rejects with reasons, sample error rate with 95% CI → P's status label | partner, O | — | — | before any P retrieval (step 13) |

Also before freezing (both preregs): the in-flight PR with the glossary-boost probe (`found10.py`,
`reranker_test.py`) and the inactive-reranker gate (`reranker_test.py`).

## What must be committed before any citable run

Everything in prereg §9, on `main` at one commit ≥ `e3002ba`; runs from a clean worktree or a tools image
at or after it. Round 1's scratch analyses are `citable: false` for exactly this reason.

## Fallbacks if the gateway closes early

| lost | fallback |
|---|---|
| frontier glossary (step 5) incomplete | `frontierp` → GATE-NOT-EVALUABLE; H2 enters Holm at p = 1.0; the family stays four (prereg §7, v1.1) |
| drafter (7–8) | Claude Opus 5.5 via the CLI (`judge.py` transport; ≈ $20 for 1,500 drafts, estimate); still a non-arm family; then the verifier must move to Codex `gpt-6-astra` (CLI) — the same vendor as the GPT-5.5 arm, which would be stated |
| verifier (9) | already off-gateway |
| Gemini judge (10) | two judges (Claude, Codex CLI) + the owner adjudicates splits blind; stated in the sentence |
| the partner's acceptance of P (§3.4 b) does not happen | P's label is *our three-layer check only, not accepted by the partner*; P stays outside F and is never called equivalent to the 155-record set |

## Timeline sketch (UTC)

- 10-29: harness merged (#861–#865); decisions taken; prereg v1.
- 10-30: owner's go → **freeze**; live-ops plan; steps 4–5 (frontier batch); 7–8 overnight.
- 10-31: step 9 (verifier), step 10 (Gemini judging); retries; totals to the partner.
- 11-01 onward: 11–17 at the owner's pace.
