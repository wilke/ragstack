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

**What changes on the outcome.** Guard 1 passes → N1, N3 (`EPACK` half) and R1–R4 become confirmatory on
this population under D4(i); the CDS family stays `ERET`-only. Guard 1 fails → the population is
descriptive (r3 §11 guard 1 as written), the containment question stays open, and the next lever is the df
threshold sweep (hard-set §5 step 2), not a bigger corpus. Either way CDS is untouched (pointed-at-scale §11
item 5).

## 2. Corpus

**What exists locally (read-only listing, 2026-10-10).** `/rag/oa/corpus/` is the CEPI journal-list harvest,
not a MeSH-defined subset: `manifest.jsonl` 1,439,753 records, `state.json` ok 1,233,988 / fail 3,143, JATS
XML under `xml/` (182 GB, 256 shards), 199 target journals with the 13 ASM journals excluded
(`/rag/oa/OA-COLLECTION-PLAN.md` §0b). No MeSH is held locally; `oa-full-ingest.md` says the bacteria ∪
viruses allowlist is `PROPOSED` and "how much of the target we already hold" is unknown. So the slice has to
be *derived*; two reproducible routes:

| route | definition | what it buys | what it costs |
|---|---|---|---|
| **M** (MeSH allowlist) | `esearch (bacteria[MeSH] OR viruses[MeSH]) AND "open access"[filter]` → PMCID list, committed with sha256; intersect with the local harvest | the production definition itself | one esearch run (not done from this host yet; oa-full-ingest says no path is verified); ASM journals absent locally |
| **J** (journal proxy) | 15 microbiology / virology / infectious-disease journals already harvested | zero fetch; JATS on disk | over-includes clinical ID (OFID, BMC ID), under-includes micro papers in megajournals |

Route J's size **(counted, not committed)**: 203,897 articles across the 15 journals, e.g. Frontiers in
Microbiology 38,601, Viruses 17,340, Microorganisms 17,512, BMC Microbiology 7,127, Virology Journal 5,423,
PLoS Pathogens 9,177. Either route gives ≥ 10× what the study corpus needs.

**Selection (proposal).** One seeded draw (`seed 20261010`) over the `sorted` allowlist, written as
`manifest.json` in the Stage 0 convention (`(pmcid, sha256)` pairs, `manifest_sha256`;
[`../stage0/artifacts/manifest.json`](../stage0/artifacts/manifest.json)), committed before the first
embedding. The draw is **topically concentrated on purpose**: pointed-at-scale §1 argues a uniformly thinned
CDS pool is a *strong*-distractor regime and a broad corpus a weak one; the production load is a single
field, so a single-field slice is the realistic regime. Source documents for questions are **inside** the
corpus (rule F needs the competitors; gold must be retrievable), one eligible deep unit per document as
in [`RESULTS-stage0b-pointed-gen.md`](../stage0/RESULTS-stage0b-pointed-gen.md) §1.

**Size (proposal).** Corpus size is not the lever (#524), so buy the smallest size with realistic
distractors and a cost line that already exists: **16,384 documents** (≈ 0.97 fleet-hours by linear scaling
of RUN-PACKAGE §6's 1.93 for 32,663 — estimate) or **32,663** (parity with Stage 0, 1.93 fleet-hours
measured, [`../RUN-PACKAGE.md`](../RUN-PACKAGE.md) §3, §6). Owner decision 2 (§11).

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
| section class | methods / results / discussion | pointed-gen §5.1 reports where the easy set's evidence sat by unit index; microbiology methods sections carry MICs, media, conditions |

Selection is **arm-symmetric**: no arm, no mode and no embedding is run on a candidate before it is
accepted; rule F's BM25 is whole-document and arm-agnostic (hard-set §4, "why F is not outcome-dependent").

**Dev / confirmation split (proposal).** Split the *source documents* by seeded draw before any
generation: half dev, half confirmation. The confirmation half is written to a `QUARANTINE`-guarded
directory the way `s0c_*` does (RUN-PACKAGE §3), is never read by a generation pass until the rule is
frozen (§12 step 8), and no retrieval runs on it before freeze. **Never overwrite a golden input on
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

Pairs: ≈ 20–30 on the pilot (§6), ≈ 50 on the dev half (as r3 §11 guard 2 / hard-set step 5), ≈ 50 on the
confirmation half after freeze. Time: r3 §11 costs ≈ 50 pairs × 2 readers at **10–15 person-hours**;
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

**What the pilot does.** Generate candidates under A–G on the 20 papers — several eligible units per
document since there are only 20 (a recorded deviation), ≈ 60–100 candidates, ≈ 15–25 min Scout
**(estimate)**. Rule F's "corpus" is 20 documents, so F runs but is *not* trusted; realism and
answerability are the point. The experts read 20–30 accepted pairs.

**Stop / go (proposal).** Go when: yield after A–E ≥ 25 %; ≥ 70 % of accepted questions rated realistic or
plausible; ≥ 85 % answerable from gold alone; the other-valid-location rate is measured and ≤ 25 %
(above that, rule F's judge is re-specified before dev generation). Stop when realism < 50 % — then the
pass B prompt, not the corpus, is the problem, and §12 returns to step 1. The pilot says nothing about
guard 1 (20 documents have no distractors) and is not read as evidence about it.

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
| embed six index arms | 1.93 fleet-hours per 32,663 docs measured (RUN-PACKAGE §3, §6); ~39 GB disk | **0.97 fleet-h / ~20 GB at 16k; 1.93 / ~39 GB at 32k** |
| pilot generation | 10 min / 400 candidates (pointed-gen §8) | ≈ 15–25 min Scout |
| dev generation, ≈ 1,000 candidates + rule F | same rate; F ≈ 20 Scout calls per A–E survivor | ≈ 25 min + ≈ 1–2 h Scout |
| dev calibration scoring | 0b′-shaped pass ≈ 25 min for 197 queries (RUN-PACKAGE §6) | ≈ 25–60 min, reranker-bound |
| confirmation generation, ≤ 4,000 candidates + F | same rates | ≈ 1.7 h + ≈ 4–5 h Scout, unattended |
| confirmation scoring after freeze | as above | ≈ 1 h |
| expert reads | r3 §11: 10–15 person-h per ≈ 50 pairs × 2 readers | pilot 2–3 + dev 10–15 + conf 10–15 person-h |

**Total (estimate): ≈ 1–2 fleet-hours of embedding, ≈ 8–11 h of Scout and reranker wall time, 22–33
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
distinguishable from one that touched the chunkers. Before the first embed the #687 regression check runs
(the six arms' golden spans byte-identical to the pinned study's); served models are probed and asserted
(pointed-gen §0); seeds are listed in the manifest.

## 10. Predictions on record

1. **Guard 1, window half:** with rules A–G at df ≥ 20 on a single-field corpus, pooled `hybrid` + rerank
   `ERET@16k` lands inside [0.15, 0.90] for ≥ 3 of 6 index arms and `EPACK∩@16k` inside for ≥ 4 of 6.
   Reason: pointed-at-scale §1 and §6 say the ceiling is key uniqueness, and a field corpus makes the
   shared-entity condition bite harder than CDS does (many papers name the same organisms, genes, drugs).
2. **Guard 1, discrimination half:** passes (it passed at 100 % even on the easy set, 0b′ §6).
3. **Yield** after A–G: 15–30 %, below the easy set's 44.2 %; rule F is the dominant rejecter.
4. **Other-valid-location rate** on the expert read: 10–25 %, higher than CDS's would be, because
   quantitative microbiology results recur across papers. This is the number most likely to force a
   re-specification of rule F.
5. **Direction:** if guard 1 passes, N1 is non-inferior on `EPACK∩`, and R1 (256 − 2048) is the contrast
   most likely to resolve — fine arm ahead on `ERET`, coarse arm on `EPACK∩`, the trade 0b′ §3 measured on CDS.

## 11. Open owner decisions

1. **Slice definition:** M (MeSH allowlist ∩ local harvest), J (journal proxy), or M with J as the fallback
   if esearch cannot be run from this host. *Recommendation:* M; it is the production definition.
   Sub-question: exclude the 3,701 CDS-overlap documents? *Recommendation:* no, record them.
2. **Corpus size:** 16,384 (≈ 0.97 fleet-h, estimate) or 32,663 (1.93 measured, Stage 0 parity).
   *Recommendation:* 16,384 — #524 says size is not the lever; parity with 0b′'s σ_d is informative, not
   required, because this set's σ_d is measured on its own dev half.
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

## 12. Steps, in order, with gates

| step | what | gate to proceed |
|---|---|---|
| 0 | owner: decisions 1–5, 7–9 | written in this file's successor or the study plan |
| 1 | **pilot** on the 20 Salmonella-AMR papers (§6), read-only text, no store | §6 stop/go met; expert read logged in the Grading view |
| 2 | allowlist + seeded manifest, committed with sha256; CDS overlap recorded | manifest committed; `citable: true` |
| 3 | parse JATS; dev/confirmation **source-document split**; confirmation half quarantined | split committed; disjointness asserted in code |
| 4 | embed six arms on the fleet (≤ 2 in flight per endpoint); #687 regression check; GPUs 6/7 asserted idle | embeddings' row maps + provenance committed |
| 5 | dev generation under A–G (+ strain screen), ≥ 150 survivors; rule F on Scout | yield ≥ 15 % (else df sweep on the *dev* candidates only, no regeneration) |
| 6 | dev calibration: 0b′-shaped scoring, both windows (`ERET` and `EPACK∩`), guard 3 sizing | **guard 1 pass → confirmatory track; fail → descriptive, report, stop here** |
| 7 | expert pointed-read on ≈ 50 dev pairs (§5) | other-valid-location rate recorded; rule F re-specified if > 25 % (proposal) |
| 8 | **r3 §11 amendment**: rule, threshold, population, which primary (decision 6) — owner sign-off, dated before step 9 | signed |
| 9 | confirmation generation on the quarantined half to guard 3's count, cap 600; no retrieval, only screen counts read | counts logged; `QUARANTINE` intact |
| 10 | expert read on ≈ 50 confirmation pairs, blind; label freeze (G3) | κ and exclusion list committed |
| 11 | owner lifts quarantine (G4); confirmation scoring; contrasts read with projected power printed beside each | — |

Steps 1–7 touch no confirmation material and run off the critical path (the CDS human read). Step 6 is
where the plan either earns the amendment or stops with a descriptive population and a measured reason.
