# PLAN — a pointed question set on a microbiology/virology corpus

**Sits beside:** [`PLAN-hard-pointed-set.md`](PLAN-hard-pointed-set.md) (the CDS hard set, #526), whose
method this adapts. **Amends, once adopted:** [`SPEC-confirmation-run-r3.md`](SPEC-confirmation-run-r3.md)
§11 (a second pointed population) — as a dated amendment *before* any confirmation candidate is generated.
**Status: proposed 2026-10-10, nothing has run.** Owner context: D4 = option 1 "for now", D15 leaning
"both", contradiction 11 (no chunk spec is the baseline until the snapshot re-runs are verified) — all in
[`../../chunking-study-plan.md`](../../chunking-study-plan.md) §6, §8.

Every measured number below cites a committed file. Numbers counted today over uncommitted host files are
marked **(counted, not committed)**; everything else is an **estimate** or a **proposal**.

---

## 1. Purpose and the decision it feeds

Under D4(i) the containment ("where") primary and the superiority family's `EPACK` bar (R1–R4, r3 §3.5)
move to the pointed population, and N1/N3's `EPACK` half goes with them (r3 §3.6). The only pointed
population in hand fails guard 1: `EPACK∩@16k` inside [0.15, 0.90] for 1 of 10 arms, `ERET` 0.904–0.955
for all ([`RESULTS-stage0b-prime.md`](../stage0/RESULTS-stage0b-prime.md) §6; `stats.json`
`guard1_discrimination`). Corpus size does not fix that: 161 of 177 queries are reached in all 60 cells and
σ_d grows faster than the between-arm spread ([`RESULTS-pointed-at-scale.md`](../stage0/RESULTS-pointed-at-scale.md)
§5–§6). The lever is question construction (hard-set §1).

This plan builds that harder population on a corpus that (a) matches the production load — the bacteria ∪
viruses subset of PMC OA, ~497,942 articles by esearch 2026-08-30
([`../../oa-full-ingest.md`](../../oa-full-ingest.md)) — and (b) the available expert readers can
actually read (microbiology, genomics, metagenomics; CDS topics are clinical, D4(ii)).

**What changes on the outcome.** Guard 1 — r3 §11 as written: discrimination ≥ 25 % **and** `EPACK∩@16k`
inside [0.15, 0.90] for **every** arm; the same window on `ERET` is hard-set §5 step 2's addition — passes →
N1, N3 (`EPACK` half) and R1–R4 become confirmatory on this population under D4(i); the CDS family stays
`ERET`-only. Guard 1 fails → the population is descriptive, the containment question stays open, and the
next lever is the df threshold sweep (hard-set §5 step 2), not a bigger corpus. Either way CDS is untouched
(pointed-at-scale §11 item 5).

## 2. Corpus

**What exists locally (read-only listing, 2026-10-10).** `/rag/oa/corpus/` is the CEPI journal-list harvest,
not a MeSH-defined subset: `manifest.jsonl` 1,439,753 records, `state.json` ok 1,233,988 / fail 3,143, JATS
XML under `xml/` (182 GB, 256 shards), 199 target journals with the 13 ASM journals excluded
(`/rag/oa/OA-COLLECTION-PLAN.md` §0b). No MeSH is held locally; `oa-full-ingest.md` says the bacteria ∪
viruses allowlist is `PROPOSED` and "how much of the target we already hold" is unknown. So the slice has to
be *derived*; two reproducible routes:

| route | definition | what it buys | what it costs |
|---|---|---|---|
| **M** (MeSH allowlist) | `esearch (bacteria[MeSH] OR viruses[MeSH]) AND "open access"[filter]` → PMCID list, committed with sha256; intersect with the local harvest | the production definition itself | the PMCID allowlist has not been materialised on this host (oa-full-ingest reports esearch *counts*, 2026-08-30, not a list); its local intersection is unknown; ASM journals absent locally |
| **J** (journal proxy) | 15 microbiology / virology / infectious-disease journals already harvested, matched by **ISSN** (per-record ISSN in `/rag/oa/corpus/discovery.jsonl`; `journals.tsv` maps ISSN → title) or case-folded title — exact title strings drop the `PLOS`/`PLoS` variants (2,510 + 2,836). The 15 ISSNs: 1664-302X, 2328-8957, 2076-2607, 1999-4915, 1471-2334, 1080-6040, 1935-2735, 1553-7366, 2235-2988, 2076-393X (*Vaccines*), 2079-6382 (*Antibiotics*), 2076-0817, 1471-2180, 1743-422X, 1742-4690 — whether *Vaccines* and *Antibiotics* belong in a single-field slice is part of decision 1 | zero fetch; JATS on disk | over-includes clinical ID (OFID, BMC ID), under-includes micro papers in megajournals |

Route J's size **(counted, not committed)**: 209,247 records by case-folded title (208,162 by ISSN through `discovery.jsonl`; step 2 picks one rule for the committed manifest), e.g. Frontiers in
Microbiology 38,602, Viruses 17,340, Microorganisms 17,512, PLoS Pathogens 11,687, BMC Microbiology 7,127,
Virology Journal 5,423 — but **18,016 have `body_chars = 0` and 20,680 are under 2,000 chars** (Open Forum
Infectious Diseases 73 % of its 24,661, Retrovirology 38 %; Emerging Infectious Diseases median 10,021),
so J's **usable full text is ≈ 188,567**. Two rules are declared before the draw (owner decision 10): exclude
empty bodies (Stage 0 did: 128 `empty_body`, `manifest.json`) and a minimum-body floor (2,000 chars
proposed). J covers a 16,384-document draw ≥ 10× over; at 32,663 it is ≈ 5.8×; M's local coverage is unknown
until the allowlist exists.

**Selection (proposal).** One seeded draw (`seed 20261010`) over the `sorted` allowlist, written as
`manifest.json` in the Stage 0 convention (`(pmcid, sha256)` pairs, `manifest_sha256`;
[`../stage0/artifacts/manifest.json`](../stage0/artifacts/manifest.json)), committed before the first
embedding. The draw is **topically concentrated on purpose**: pointed-at-scale §1 argues a uniformly thinned
CDS pool is a *strong*-distractor regime and a broad corpus a weak one; the production load is a single
field, so a single-field slice is the realistic regime. Source documents for questions are **inside** the
corpus (rule F needs the competitors; gold must be retrievable), one eligible deep unit per document as
in [`RESULTS-stage0b-pointed-gen.md`](../stage0/RESULTS-stage0b-pointed-gen.md) §1.

**Size (proposal).** Corpus size is not the lever (#524), so buy the smallest size with realistic
distractors: **16,384 documents** or **32,663** (Stage 0 parity). **Embed cost scales by tokens, not
documents.** Stage 0's anchor is 1.93 fleet-hours for 1.132 B SFR tokens over 32,663 docs
([`../RUN-PACKAGE.md`](../RUN-PACKAGE.md) §3, §6), ≈ 5,782 SFR tokens/doc on the 256 arm (737,698 × 256 /
32,663). This slice's documents are longer **(counted, not committed, `body_chars`)**: J (≥ 2,000 chars)
median 31,148 / mean 33,463 against 23,286 / 24,565 for the 3,701 Stage 0 docs in the harvest → **≈ 1.35×**;
the harvest-wide mean 38,688 over the Stage 0 median gives 1.66× as a conservative upper bound (mean over mean: 1.57). The 3,701 are a self-selected 11 % of Stage 0, so the true ratio may sit higher than 1.35×; step 2's Σ-tokens recompute settles it. So **16,384 docs ≈ 1.3–1.6 fleet-h, ~26–32 GB;
32,663 ≈ 2.6–3.2 fleet-h, ~53–65 GB** (estimates; tokens/char for this slice is not measured). Step 2
computes Σ SFR tokens from the committed manifest before any GPU is touched. Owner decision 2 (§11).

**Overlap with CDS (counted, not committed).** Stage 0's 32,663 ∩ the local harvest = 3,701; ∩ route J =
1,009. Disjointness from Stage 0 is **not** required (different indexes); the manifest records the overlap
so it can be excluded if the owner wants (decision 1). Dev and confirmation *source documents* must be disjoint.

## 3. Arms

**Proposal: the same six index arms as Stage 0** — `fixed_tok256_ov0pct`, `fixed_tok512_ov0pct`,
`fixed_tok512` (512/64), `header512`, `fixed_tok1024_ov0pct`, `fixed_tok2048_ov0pct` — plus the
zero-embedding delivery arms `parent256`, `nbr1_512`, `nbr1_256`, `nbr2_512` (r3 §3.5), so N1, N3 and
R1–R4 read on the same arms and the 0b′ σ_d table is the comparator. `parent256` needs JATS `<sec>` units,
which is one reason the corpus is JATS from `/rag/oa/corpus/xml`, not PDF extraction. Each dropped index
arm saves a sixth of the embed cost; only `header512` is droppable, and R2's `EPACK` bar moves here too, so keep it.

**The `section` method (D8) is not built** ([`../../chunking-evaluation-candidates.md`](../../chunking-evaluation-candidates.md)
§6: consolidate → C-R0 → C-R1 → production `section`). It joins later as a seventh index arm on the same
committed manifest: one more embed pass (≈ 1/6 of the fleet-hours), the same queries, unit spans
fingerprinted beside the sentence spans (`docs/papers/README.md`). Pre-registering it is C-R1's job.

## 4. Question generation

**Pipeline:** `s0_pointed_gen.py`'s four passes (A summary → B question written blind to the section → C
construction gold, whole sentences located exact-then-normalised → D front-matter verifier;
pointed-gen §2), with the hard set's rules **A–G** (hard-set §4) applied verbatim: no unique key
(df ≥ 20 over the *study corpus's* full texts), shared entity (≥ 20 documents), leakage screens kept,
no deixis, deep source, single source (rule F, document-level BM25 top-20 + a Scout answerability judgement
per competitor), 6–15 words. One new screen is **proposed**: **no strain/accession key** — a strain
designation, accession, plasmid or primer id identifies one paper even when its df passes (hard-set §3's catheter).

**Generator and endpoints (proposal, as measured in pointed-gen §0, §8):** Llama-4-Scout on `mango:8003`,
≤ 4 in flight, temperature per pass, the served model asserted before the first call. Measured rate
**10 min 19 s per 400 candidates**. SFR `:9001–9006` ≤ 2 in flight per endpoint; reranker `:50052` ≤ 4;
GPUs 6 and 7 untouched, asserted before and after. Rule F's judge is Scout (hard-set §8 item 3).

**Difficulty strata (proposal).** Two factors, fixed quotas per cell, no stopping rule: OpenChia's Episode
statistic is calibrated only under even sampling and rarely terminates under heavy heterogeneity
([`../../../design/openchia-r0/RESULTS-R0-stopping-statistic-calibration.md`](../../../design/openchia-r0/RESULTS-R0-stopping-statistic-calibration.md) §4):

| factor | levels | why |
|---|---|---|
| entity df band | 20–99 / 100–999 / ≥ 1,000 documents | the hard set's lever, pre-declared instead of swept after the fact |
| section class | methods / results / discussion / **other** | rule E admits all four; on the easy set `other` was the largest class (pointed-gen §5.1: other 75, discussion 67, results 20, methods 15), so excluding it would be a recorded deviation; microbiology methods sections carry MICs, media, conditions |

Selection is **arm-symmetric**: no arm, no mode and no embedding is run on a candidate before it is
accepted; rule F's BM25 is whole-document and arm-agnostic (hard-set §4, "why F is not outcome-dependent").

**Dev / confirmation split (proposal).** Split the *source documents* by seeded draw before any
generation: half dev, half confirmation. **Quarantine means two things:** (i) no question is generated
from a confirmation-half document until the rule is frozen (§12 step 8); (ii) no retrieval outcome is
computed on a confirmation query before freeze. Confirmation-half documents **may** appear as rule F
competitors for dev candidates (whole-document BM25 is not an arm) and every such exposure is recorded.
Confirmation outputs live in a `QUARANTINE`-guarded directory as `s0c_*` does (RUN-PACKAGE §3). **Never overwrite a golden input on
re-invocation**: tagged output paths, refuse-if-exists, a single-writer lock (study plan E3; pointed-at-scale D3).

## 5. Expert check

Readers: the microbiology / genomics / metagenomics experts (D4(ii), D7). Per (question, gold passage) pair,
three judgements, in the Grading view's `pointed-read` kind
([`../../grading-ui.md`](../../grading-ui.md) §3.1 — `extra_questions` already carries r3 §11's *does
another passage answer it?*):

1. **Realistic?** Would a research agent building an argument ask this (yes / plausible / no)?
2. **Answerable from the gold passage alone?** (yes / partly / no) — a *no* is a construction defect.
3. **Other valid locations.** Is the question answered elsewhere in the same document, or by a shown
   competitor from rule F's top-20? Guard 2's under-count bound; in a single-field corpus (MICs, growth
   conditions recur across papers) it is the rate most likely to bite.

Pairs: ≈ 20–30 on the pilot (§6), ≈ 50 on the dev half (as r3 §11 guard 2 / hard-set step 5), and ≈ 50
on the confirmation half after freeze — the third read is an **addition** beyond r3 §11's single ≈ 50-pair
read. Time: r3 §11 costs ≈ 50 pairs × 2 readers at **10–15 person-hours**;
the pilot ≈ 2–3 person-hours **(estimate)**. Readers must sign the rubric first (G1, study plan §5).

## 6. Pilot — the 20 Salmonella-AMR papers

The 20 papers exist on the **hackathon** tenant as three collections (`fixed` 512/64 2,090 chunks;
`semantic` 375; `semantic_pooled` 418) from one PDF upload
([`../salmonella-amr-semantic-vs-pooled-2026-09-24.md`](../salmonella-amr-semantic-vs-pooled-2026-09-24.md)).
**Read-only use.** The pilot needs *text*, not a store: the extraction is `batch-00000.jsonl`
(md5 `f862891198df5fe8cc4f9e33fff8001d`, 20 docs, 933,068 chars), and 4 of the 10 PMC-named papers also have
JATS under `/rag/oa/corpus/xml` **(counted, not committed)**. Nothing is written to hackathon; no retrieval
smoke runs against it. If a retrieval smoke is wanted it goes to the **dev tenant** only
(`/rag/data/tenants/dev/config/tenant.env`), with `QDRANT_URL`/`ELASTICSEARCH_URL` otherwise pointed at
`http://127.0.0.1:1`.

**What the pilot does.** Generate candidates on the 20 papers — several eligible units per document since
there are only 20 (a recorded deviation), ≈ 60–100 candidates, ≈ 15–25 min Scout **(estimate)**. Rules
**A and B cannot be enforced on 20 documents** (df ≥ 20 is vacuous), and rule F's competitor set is 20
documents, so: A/B are *reported* against an external `df_corpus.json` built in one CPU pass over an
on-disk full-text set — the route-J usable slice (preferred, same field) or the whole harvest — if that
table exists by pilot time, otherwise deferred to step 5; F runs but is not trusted. Realism and
answerability are the point. The experts read 20–30 accepted pairs.

**Stop / go (proposal).** Go when: yield after **C–E plus the strain screen** ≥ 25 %; ≥ 70 % of accepted
questions rated realistic or plausible; ≥ 85 % answerable from gold alone; the other-valid-location rate is
measured and ≤ 25 % (above that, rule F's judge is re-specified before dev generation). Stop when realism
< 50 % — then the pass B prompt, not the corpus, is the problem, and §12 returns to step 1. **The pilot reads
direction, not rates:** on 20–30 pairs a Wilson interval is up to ± 20 pp, so every threshold above is a
go/stop heuristic, not a measurement to quote. The pilot says nothing about guard 1 (20 documents have no
distractors) and is not read as evidence about it.

## 7. Sizing and power (projection)

The only measured σ_d for a pointed population is 0b′'s, on the *easy* set
([`stats.json`](../stage0/artifacts/stage0b-prime/stats.json) `guard3_sizing`; 0b′ §6):
σ_d(`ERET`) 0.130–0.226, σ_d(`EPACK∩`) 0.189–0.367, joint n for 80 % at ε = 0.05: N1 200, N3 200, R1 400,
R2 140, R3 280, R4 100 — all inside the 600 cap. A harder set has more discordance; hard-set §5 step 2's
arithmetic (n ≈ 3,136 q for a paired binary endpoint) puts q ≤ 0.10 at n ≤ 314 and q = 0.19 at the cap.
**Projection:** generate ≥ 150 dev survivors (guard 3's floor), measure σ_d per contrast on both endpoints,
and expect a confirmation count of **300–600**, cap 600; above the cap the population is re-declared a
reported secondary before freeze (r3 §11 guard 3). At 44.2 % yield (pointed-gen §4) — likely lower under
A–G; hard-set §2 budgets 15–25 % — 600 survivors need 2,400–4,000 candidates.

## 8. Cost

| item | basis | estimate |
|---|---|---|
| allowlist + manifest | one esearch, one seeded draw | < 1 h, no GPU |
| parse JATS → `docs.jsonl`, `units.jsonl` | XML already on disk; RUN-PACKAGE §6 "~1 h fetch/parse" for 33k | 0.5–1 h CPU |
| embed six index arms | 1.93 fleet-h / 1.132 B SFR tokens / ~39 GB for Stage 0 (RUN-PACKAGE §3, §6), scaled **by tokens** at the §2 length ratio 1.35–1.66× (counted, not committed) | **≈ 1.3–1.6 fleet-h / ~26–32 GB at 16,384; ≈ 2.6–3.2 / ~53–65 GB at 32,663**; Σ tokens recomputed from the manifest at step 2 |
| pilot generation | 10 min / 400 candidates (pointed-gen §8) | ≈ 15–25 min Scout |
| dev generation, ≈ 1,000 candidates + rule F | same rate; F ≈ 20 Scout calls per A–E survivor | ≈ 25 min + ≈ 1–2 h Scout |
| dev calibration scoring | 0b′-shaped pass ≈ 25 min for 197 queries (RUN-PACKAGE §6) | ≈ 25–60 min, reranker-bound |
| confirmation generation, ≤ 4,000 candidates + F | same rates | ≈ 1.7 h + ≈ 4–5 h Scout, unattended |
| confirmation scoring after freeze | as above | ≈ 1 h |
| expert reads | r3 §11: 10–15 person-h per ≈ 50 pairs × 2 readers | pilot 2–3 + dev 10–15 + conf 10–15 person-h |

**Total (estimate): ≈ 1.3–3.2 fleet-hours of embedding, ≈ 8–11 h of Scout and reranker wall time, 22–33
person-hours.** Bought: a population the readers can judge, on the production field. Lost (D15): the hard
set's "no new embeddings" property and r3 §11's one-index-per-arm design across populations.

## 9. Provenance

A new corpus means new embeddings, so nothing here reuses the `55a0fc2` coordinate system and this is a
**new experiment on current `main`**, not a snapshot re-run. Every artifact (manifest, candidate files,
pools, stats) embeds `ragstack.provenance.experiment_provenance()` under `provenance`, run with
`PYTHONPATH=python` from the checkout being recorded; `citable: true` requires a clean tree with no
untracked files (`docs/papers/README.md` § *Every artifact records which code produced it*). The record
also carries `sentence_spans_fingerprint()` over the corpus's own texts and, **proposed as an added
field**, `git rev-parse HEAD:python` (the python tree hash) so a `main` commit that touched only docs is
distinguishable from one that touched the chunkers — useful only if it is added to
`EXPERIMENT_COMPARISON_KEYS` (`provenance.py`) or compared separately, since `comparison_key()` ignores
unknown fields. Before the first embed the #687 regression check runs
(the six arms' golden spans byte-identical to the pinned study's); served models are probed and asserted
(pointed-gen §0); seeds are listed in the manifest.

## 10. Predictions on record

1. **Guard 1, window half, on the 10 arms scored under `hybrid` + rerank** (6 index + `parent256`,
   `nbr1_512`, `nbr1_256`, `nbr2_512`; 0b′ §6 counts the same ten): with rules A–G at df ≥ 20 on a
   single-field corpus, `ERET@16k` lands inside [0.15, 0.90] for all 10, and `EPACK∩@16k` inside for
   **≥ 8 of 10**, with the coarsest delivered spans the likely exceptions (on the easy set the top `EPACK | reach`
   were `nbr2_512` 1.000, `nbr1_512` 0.994, `fixed_tok2048_ov0pct` 0.988: `stats.json`
   `window_demotions`). **Under
   the gate as written (every arm) that is a narrow FAIL**; my probability of a clean pass is ≈ 0.35
   (estimate). Reason: pointed-at-scale §1 and §6 say the ceiling is key uniqueness, and a field corpus
   makes the shared-entity condition bite harder than CDS does; but the coarsest arms contain almost
   everything they reach by construction. Whether a count-based window is acceptable is decision 11.
2. **Guard 1, discrimination half:** passes (it passed at 100 % even on the easy set, 0b′ §6).
3. **Yield** after A–G: 15–30 %, below the easy set's 44.2 %; rule F is the dominant rejecter.
4. **Other-valid-location rate** on the expert read: 10–25 %, higher than CDS's would be, because
   quantitative microbiology results recur across papers. This is the number most likely to force a
   re-specification of rule F.
5. **Direction:** if guard 1 passes, N1 is non-inferior on `EPACK∩`, and R1 (256 − 2048) is the contrast
   most likely to resolve — fine arm ahead on `ERET`, coarse arm on `EPACK∩`, the trade 0b′ §3 measured on CDS.

## 11. Open owner decisions

0. **D15 itself** (study plan §6): a second corpus at all, or the CDS hard set only. This plan costs it;
   it does not decide it. *Recommendation:* none beyond the study plan's — "both" is the owner's leaning.
1. **Slice definition:** M (MeSH allowlist ∩ local harvest), J (journal proxy by ISSN), or M with J as the
   fallback if the allowlist cannot be materialised. *Recommendation:* M; it is the production definition.
   Sub-question: exclude the 3,701 CDS-overlap documents? *Recommendation:* no, record them.
2. **Corpus size:** 16,384 (≈ 1.3–1.6 fleet-h, estimate by tokens) or 32,663 (≈ 2.6–3.2, Stage 0 parity
   in documents, not in tokens). *Recommendation:* 16,384 — #524 says size is not the lever; parity with
   0b′'s σ_d is informative, not required, because this set's σ_d is measured on its own dev half.
3. **ASM journals** (the readers' home journals; excluded from the harvest, held in the ASM tenant): fetch a
   seeded share into the *study* corpus, or leave them out. *Recommendation:* include if route M lists them
   — the dedup objection is about the production collection, not a study corpus.
4. **Arms:** all six index arms + four delivery arms, or drop `header512`. *Recommendation:* all.
5. **Tokenizer for the new embeddings** (interacts with D13): SFR tokens (run (a)'s recorded deviation) or
   the generator's (r3 §3.3). *Recommendation:* settle D13 first; a corpus embedded once should be embedded
   in the tokenizer r3 will be amended to.
6. **Which population carries the containment primary if both this set and the CDS hard set pass guard
   1** — this one (production field), the CDS hard set (replication), or a four-way conjunction (doubles
   guard 3's count). *Recommendation:* this one primary, CDS hard set reported as replication; **the owner
   decides, in the r3 §11 amendment**.
7. **Readers:** are the experts also the two R-dev readers (D7), and is the pilot read done before G1's
   rubric signing or after. *Recommendation:* after; the pilot is the rubric's first live use.
8. **Rule F's judge and the new strain/accession screen:** Scout as proposed; accept the extra screen or
   keep A–G verbatim. *Recommendation:* accept; it is a pre-retrieval filter and costs nothing.
9. **Go / no-go on the pilot** (§6; ≈ 1 h machine, 2–3 person-hours, nothing pre-registered, reversible).
10. **Body-length rules before the draw:** exclude `body_chars = 0` (as Stage 0 did) and set a minimum-body
    floor — 2,000 chars proposed (removes 20,680 of J's 209,247, counted, not committed) — or a different
    floor, or none. *Recommendation:* both rules at 2,000; a 1,500-char abstract-only record is not a
    document a research agent reads.
11. **Guard 1's window as every-arm or count-based:** r3 §11 requires every arm inside [0.15, 0.90] on
    `EPACK∩`; hard-set §5 step 2 adds the same window on `ERET`. Prediction 1 expects the two coarsest arms
    to sit above the ceiling. Options: keep every-arm (a near-miss is a FAIL and the population is
    descriptive); amend r3 §11 to a count-based window (e.g. ≥ 8 of 10 scored arms, the demoted arms
    descriptive for their contrasts under r3 §3.1's existing demotion rule). *Recommendation:* decide
    at step 0, or at the latest **before** step 6 is read, and write it into the step 8 amendment; the plan does not pick.

## 12. Steps, in order, with gates

| step | what | gate to proceed |
|---|---|---|
| 0 | owner: decisions 0–5, 7–11 | written in this file's successor or the study plan |
| 1 | **pilot** on the 20 Salmonella-AMR papers (§6), read-only text, no store | §6 stop/go met (direction, not rates); expert read logged in the Grading view |
| 2 | allowlist + body-length rules + seeded manifest, committed with sha256; CDS overlap recorded; **Σ SFR tokens computed from the manifest** → the embed cost line re-stated | manifest committed; `citable: true`; cost within the owner's budget |
| 3 | parse JATS; dev/confirmation **source-document split**; confirmation half quarantined | split committed; disjointness asserted in code |
| 4 | embed six arms on the fleet (≤ 2 in flight per endpoint); #687 regression check; GPUs 6/7 asserted idle | embeddings' row maps + provenance committed |
| 5 | `df_corpus.json` over the study corpus; dev generation under A–G (+ strain screen), ≥ 150 survivors; rule F on Scout, competitor exposures of confirmation-half docs recorded | yield ≥ 15 % (else df sweep on the *dev* candidates only, no regeneration) |
| 6 | dev calibration: 0b′-shaped scoring on all 10 scored arms, both windows (`EPACK∩` per r3 §11; `ERET` per hard-set §5 step 2), guard 3 sizing | **guard 1 pass (every arm, or the count rule if decision 11 amended it first) → confirmatory track; fail → descriptive, report, stop here** |
| 7 | expert pointed-read on ≈ 50 dev pairs (§5) | other-valid-location rate recorded; rule F re-specified if > 25 % (proposal) |
| 8 | **r3 §11 amendment**: rule, threshold, population, which primary (decision 6) — owner sign-off, dated before step 9 | signed |
| 9 | confirmation generation on the quarantined half to guard 3's count, cap 600; no arm is run and no outcome read (rule F's competitor screen runs and is recorded, §4), only screen counts read | counts logged; `QUARANTINE` intact |
| 10 | expert read on ≈ 50 confirmation pairs, blind; label freeze (G3) | κ and exclusion list committed |
| 11 | owner lifts quarantine (G4); confirmation scoring; contrasts read with projected power printed beside each | — |

Steps 1–7 touch no confirmation material and run off the critical path (the CDS human read). Step 6 is
where the plan either earns the amendment or stops with a descriptive population and a measured reason.
