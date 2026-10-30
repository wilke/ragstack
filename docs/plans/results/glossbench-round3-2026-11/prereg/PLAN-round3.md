# Round 3 plan — attachment rule as the arm (lab book, 2026-10-30)

> **Lab-book tier.** Dated, append-only, superseded-not-deleted. Nothing here constrains the design; the
> design is `PREREG-glossbench-round3.md`. Token and call figures are **design estimates** from round 2's
> drafter rates (1.3 attempts per record, ≈ 1k input / 250 output tokens per call, ± 20%). Counts are
> from `R3-COUNTS.md` (planning, 2026-10-29) and the offline dry run of 2026-10-30 (prereg §3.1 step 5);
> neither is a result.

Owner key: **O** = project owner, **F** = Fable (plan/review), **S** = subagent (implementation, one
worktree each), **papers-OC owner** = the partner that holds the papers-OC collection. Nothing below runs
until the prereg is frozen, except Phase 0.

## Why a round 3, in one paragraph

Round 2 froze a design whose confirmatory population was *value-statement chunks reached by at least one
glossary arm under the served value-only attachment* and gated each corpus at 150 such chunks. The gate
was measured only after the freeze: 12 (O), 3 (A), 12 (P) — every retrieval leg GATE-NOT-EVALUABLE. The
cause is the served attachment rule, not the glossary: the Qwen3.6 glossary has entries on 777 of 12,560
OA units and 545 of 1,000 ASM articles, but `attach_only_with_value` renders them onto 300 and 7 chunks.
Under mention attachment the same files render onto 5,918 and 6,246 chunks. Round 3 therefore manipulates
the **attachment rule**, holds the glossary fixed at the lab-book default (the round-2 pure Qwen3.6 files,
already on disk), sizes the eval set from offline counts **before** the freeze — and has now read its gate
offline with the committed pool (151 / 56 / 116 against 100 / 40 / 60) — and pre-registers the
paper-density criterion the papers-OC owner asked for.

## Phase 0 — before freezing (no gateway, no store writes)

| # | step | owner | state |
|---|---|---|---|
| 0 | Offline counts: attachment reach per rule × arm × corpus, per-paper densities, eligibility, sampled pool sizes, power | F | **done** 2026-10-29 (`R3-COUNTS.md`, scripts beside it) |
| 1 | Owner decisions D1–D12 (`DECISIONS-round3.md` of 2026-10-29; folded into prereg §11 as D1–D14) | O | **done** 2026-10-30: every recommended default taken as proposed |
| 1b | The papers-OC owner's input (H2 framing, bench collection ids, volume) and its user's approval of the volume (100 pilot verdicts; ≈ 220 scripted + 50 judged; ≈ 1.45 M drafting input tokens, Gemini 2.5 Pro, size notice per batch) | papers-OC owner | **done** 2026-10-30 (prereg §3.3, §3.4, §8, §11) |
| 2 | **Code** (one worktree; Fable review with a mutation probe): `rederive --attach`; the `+attach=` stamp slot and contract test; the pool's V / M_num populations, eligibility, quotas, gold-differs gate; the drafter's `symbol_is_the_quantity`; `--probe-max-glossaries`; `analyse_r3` | S, F | **done**: **#874** merged **`898e34d`** (2026-10-30; `1390051`, `bc9be96`, `cc955ea`) |
| 3 | Offline manipulation checks with the merged code: (a) `rederive --attach mention` of the three Qwen3.6 and three `rules/3` files, render parity on every chunk (0 mismatches, `value ⊂ mention` on every chunk); (b) pool build in **dry** form for O, A, P, manifests with densities, gate read | S (review of #874) | **done** 2026-10-30 at `bc9be96`: eligible 288 / 28 / 109, gold differs 151 / 56 / 116, every gate `EVALUABLE` |
| 3b | (c) the **P dry build in fields mode** (3b built P in open mode); (d) the boost probe's candidate search offline per arm × corpus on the rendered fields (`not_testable` risk, prereg §5 note 2) | S | **pending**; README precondition 3 |
| 3c | Read-only **BM25 exhaustion verifier** (prereg §5 note 3): served length of a short `bm25` list = ES `_count` of the server's BM25 clause on the bench index; or the owner's decision to run without it | S, O | **pending**; README precondition 4 — the one open decision at v1 |
| 4 | Prereg v1 → owner's go → **freeze** (sha into a private `PREREG.sha256`; README procedure) | F, O | v1 drafted 2026-10-30 (this PR); freeze pending 3b–3c |
| 5 | Live-ops plan for every bench-store touch (bench indices only, `swap.sh` dry run → apply → census, stop by recorded pid, restore to `rules/3@file/1`) | F | pending; precedes step 12 |

## Phase 1 — gateway-dependent (drafting only)

| # | step | owner | calls | in | out | notes |
|---|---|---|---|---|---|---|
| 6 | `glossbench_r3_pool.py build --set O\|A\|P` (final, not `--dry`; the arm files frozen by sha, P in fields mode with the pilot's papers excluded) → eligibility, seeded paper sample, strata, quotas, **gold-differs gate** | S | — | — | — | counts manifest; the gate decides each leg **before any draft** |
| 7 | **P pilot** (round 2's D-1, never ran): 100 records / 5 papers, fields mode against binding v2, layers 1–3, adapter, the papers-OC owner's verdicts → §3.5 promotion rule | S, papers-OC owner | ≈ 130 | ≈ 0.13 M | ≈ 0.03 M | data for no hypothesis; its papers excluded from P before step 6's P build |
| 8 | **Draft O**: 50 papers × (6 V + 6 M_num) = 534 sampled chunks (dry run) × 1.3 | S, notice | ≈ 690 | ≈ 0.69 M | ≈ 0.17 M | drafter blind to glossaries, as round 2 |
| 9 | **Draft P**: 25 papers × (6 + 6) ≈ 280 jobs, fields mode | S, notice | ≈ 370 | ≈ 0.37 M | ≈ 0.09 M | within the approved volume |
| 9b | **Draft A**: 25 papers × (2 V + 6 M_num) = 200 chunks | S, notice | ≈ 260 | ≈ 0.26 M | ≈ 0.07 M | descriptive leg (D4 kept it) |
| 10 | `check` (layer 1) then `verify` (layer 2, Claude CLI, off-gateway) | S | ≈ 1,130 CLI | — | — | ≈ $20 CLI, not gateway |
| | **Phase-1 gateway total** | | **≈ 1,450** | **≈ 1.45 M** | **≈ 0.36 M** | the approved volume; pause at 2.5 M in / 0.5 M out / 2,500 calls (D10) |

No glossary model is called in round 3: the arms are renderings of files that exist. The round-2 frontier
cap finding (30 of 58 calls cut at `max_tokens` 4096) **does not bind** here; it is recorded as a
precondition for any future gateway glossary arm (D11). Claude is used only through the CLI verifier.

## Phase 2 — no gateway

| # | step | owner | touches a store? |
|---|---|---|---|
| 11 | Layer-3 owner spot-check, 40 per set, blind | O | no |
| 12 | **Retrieval**, per corpus, arms in this order: `value` (served rule, Qwen3.6) → `mention` → `mention-shuf` → `none` → `rules-value` → `rules-mention` → `value` repeat (`--repeat 2`); each run = base + bm25 + norerank; boost probe per cell (widened or `declared` as step 3b found); `swap.sh` dry run → apply → census before and after; restore `rules/3@file/1` | S under step 5 | **yes** (bench copies only: `bench-oa-tok512o0-261028`, `bench-asm-tok512o0-261028`) |
| 13 | P and R1 on the papers-OC bench copy (`bench-openchia-tok512o0-261028`), same procedure; short `bm25` lists verified exhausted (step 3c) or gated | S; the papers-OC owner optional read-only cross-check | **yes** (bench copy) |
| 14 | `wording_proxy.py` per arm (a **manipulation check** here, not a hypothesis), `found10.py analyse` (`analysis.round: 3`), `RESULTS.md`, counts-only artifacts, `DEVIATIONS.md` | S, F review | no |
| 15 | The papers-OC owner's acceptance of P (scripted checks + judged sample of 50) → P's status label | papers-OC owner, O | no |
| 16 | Operational decision on the attachment rule (owner), on these results plus the distractor-load figures | O | no |

Precision (round 2 § 6) is **not repeated**: the glossary is unchanged (D9).

## Fallbacks

| lost | fallback |
|---|---|
| gateway closes before O is drafted | O is the family; pause, resume after a fresh notice; nothing else substitutes |
| the papers-OC owner cannot judge the P volume | P stays a replication with the label *our three-layer check only*; promotion rule void (no pilot verdicts) |
| boost probe `not_testable` on a cell after widening | `--boost-declared` with the configured value copied into the private config; the reading is `declared` and printed beside every bm25 figure of that cell (prereg §5 note 2) |
| short `bm25` lists without the exhaustion verifier | that arm × condition is GATE-NOT-EVALUABLE on `bm25` and reported so; `base` and `norerank` are unaffected (prereg §5 note 3) |
| P's fields-mode gate reading below 60 at step 6 | P is GATE-NOT-EVALUABLE for retrieval and reported so; no paper is added after the gate is read; O carries the family regardless |
| `mention` on a symbol-dense corpus floods top-10 (the #754 pattern) | that is a result, not a failure: H2 (V stratum) is two-sided for exactly this reason |

## Timeline sketch (UTC)

- 10-30: decisions, code, offline dry run, prereg v1 (done); fields-mode P dry build, probe counts,
  exhaustion-verifier decision.
- 10-31: review, freeze; live-ops plan; pool build (step 6).
- 11-01: pilot (step 7) → promotion addendum; drafts O, P, A overnight.
- 11-02: verifier; spot-check; retrieval swaps (steps 12–13).
- 11-03: analysis, RESULTS, DEVIATIONS.
