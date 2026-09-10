# PLAN — the hard pointed set

**Amends:** [`SPEC-confirmation-run-r3.md`](SPEC-confirmation-run-r3.md) §10 item 6 (the
proposal) and §11 (the pointed population and its guards). **Status: proposed 2026-09-08,
awaiting the owner's go.** Nothing in this plan has run. Once adopted, the rule in §4 is
copied into r3 §11 as a dated amendment *before* any confirmation candidate is generated.

**Evidence it rests on:** `../stage0/RESULTS-stage0b-prime.md` (pointed population at the
ceiling, guard 1 failed), `../stage0/RESULTS-pointed-at-scale.md` (#524: corpus size is not
the fix; 161 of 177 queries reached in every cell), and a re-read of the 177 accepted and 223
rejected dev candidates in `../stage0/artifacts/pointed/`.

---

## 1. The problem in one paragraph

The pointed population was built so that each question names a **rare entity** from a deep
section of its source article. A rare entity is a near-perfect retrieval key: every arm, at
every chunk size, in every retrieval mode, finds the source for 161 of the 177 questions.
A population every arm passes cannot rank arms, so the size contrasts the study exists to
settle are unresolvable on it. #524 showed the corpus is not the lever (reach falls only
0.98 → 0.94 from 4k to 32.7k documents and the paired noise grows faster than the between-arm
spread). The lever is the **question**: keep its shape — one finding, one passage, ≈ 12 words —
but remove the unique key, so the retriever has to find the *right passage among many that
mention the same things*, which is what chunk size actually governs.

## 2. Three things the item-6 proposal got wrong, found on re-read

1. **"Rare" was never measured on the corpus.** The rare-term gate (`no_deep_rare_term`) uses
   IDF ≥ 5.60 over a 10,000-document *title + abstract* table (`idf_oa10k.json`), not the
   32,663-document full text the arms retrieve over. Under it, *clinic*, *count*, *instrument*
   and *marrow* pass as rare. The hard set must define rarity as **document frequency over the
   full text of the actual corpus** (`/rag/tmp/stage0-conf/work/docs.jsonl`), or the "≥ 20
   documents" clause is not enforceable.
2. **The hardest queries today are hard for the wrong reason.** The five queries reached in
   fewer than half the cells are *underspecified*, not hard: "What was the initial haemoglobin
   level **of the patient**?", "What was the patient's leucocyte count at presentation?". No
   retriever can answer a question that does not say which patient. Hardness bought this way is
   noise, not signal, and it is exactly what a "drop the rare entity" rule would produce more
   of. The hard set needs a **deictic screen** and a **single-source screen** (§4, rules D and F)
   so that each question still determines one passage in the corpus.
3. **Outcome-stratified selection cannot be applied to the confirmation set.** Item 6 (iii)
   selects queries whose *pooled reach* sits in [0.3, 0.8]. On the development topics that is a
   legitimate diagnostic. On the confirmation topics it would mean running every arm over the
   confirmation queries and reading the result before freeze — a quarantine breach, and a
   selection on the very outcome the arms are compared on. The fix is a **two-stage design**:
   learn a *pre-retrieval* rule on the dev set (where reading outcomes is allowed), fix it,
   then generate the confirmation set under that rule with no retrieval before freeze (§5).

And one thing it under-priced: "about an hour" is the cost of the **pilot** on candidates
that already exist (§5 step 0). A confirmation set of up to 600 queries at the expected
15–25 % yield needs 2,500–4,000 generated candidates — 4.5–7 hours of Scout time at the
measured 43 min per 400 — plus the screens. Still no corpus embeddings.

## 3. What the hard set is

The same construction pipeline as `s0_pointed_gen.py` (pass A summary → pass B question written
blind to the section → pass C construction gold → pass D abstract-answerability verifier), with
the entity clause inverted and four screens added. A candidate survives when **all** of §4 hold.

**Example of the target shape, taken from the existing set.** "What tidal volume reduces
mortality in lung-protective ventilation for ALI/ARDS?" — *ARDS*, *tidal volume* and
*mortality* each occur in dozens of corpus documents, so no single term identifies the source,
yet the question fully determines its answer (6 ml/kg) and one passage carries it. It was
reached in 52 of 60 cells: hard, not ambiguous. Contrast "What is the outcome of using Fogarty
embolectomy catheter in axillary artery pseudoaneurysm treatment?" — reached in 60 of 60; the
catheter name is the key.

## 4. The rule (to be pre-registered verbatim in r3 §11 before generation)

| id | rule | replaces / keeps | enforced how |
|---|---|---|---|
| **A** | **No unique key.** Every content term and every 2- and 3-gram of the query (stopwords removed, the existing tokenizer) has **corpus document frequency ≥ 20** over the 32,663 full texts. | *replaces* `no_deep_rare_term` (IDF ≥ 5.60 on 10k titles) | new `df_corpus.json` built once from `docs.jsonl`; screen `unique_key` |
| **B** | **Shared entity.** The entity pass B declares occurs (normalised substring) in ≥ 20 corpus documents. | *inverts* the rare-entity requirement | screen `entity_df_lt_20` (reported and gated) |
| **C** | **Leakage screens kept as-is.** `title_answerable` < 0.80 against title + abstract; entity absent from front matter; pass D verifier says NO. | *keeps* §11 | unchanged code |
| **D** | **No deixis.** Reject *the/this/our patient(s)/case/study/cohort/trial/authors/series* and questions whose subject is unnamed. | new | regex + pass B instruction; rejects the "of the patient" class |
| **E** | **Deep source.** Construction unit index ≥ 2 and unit class ∈ {results, discussion, methods, other} — no introduction. | *keeps* §11 | unchanged |
| **F** | **Single source in the corpus.** Retrieve the top 20 documents by document-level BM25 over full texts (arm-agnostic, no chunking, no embeddings); for each competitor the best-matching sentence window is shown to Scout with the question: *does this passage answer it?* A candidate with ≥ 1 competitor judged YES is **rejected** (recorded, with the competitor, for guard 2's under-count bound). | new | `s0h_single_source.py`; ≤ 4 in flight on `mango:8003` |
| **G** | **Form.** 6–15 words, one clause, one finding; construction gold ≤ 3 whole sentences, located exact-then-normalised. | *keeps* §11 | unchanged |

The df threshold **20** is a starting value. §5 step 2 may move it (20 → 50 → 100) on the dev
set only; the value used for the confirmation set is the one written into the amendment.

**Why F is not outcome-dependent selection.** F uses one retrieval that is not an arm and not
a mode under test (whole-document BM25), and it selects on *answerability of competitors*, not
on whether any arm reaches the gold. It makes the population cleaner (one passage answers),
not easier for any arm.

## 5. Steps, with what each one costs and decides

**Step 0 — pilot on candidates that already exist (≈ 1 h, no generation).** 74 of the 400
dev candidates were rejected *only* for `no_deep_rare_term` — they had already passed the
leakage screens — and are exactly the hard set's natural candidates. Re-screen them under
A–G (F needs ~74 × ≤ 20 Scout calls, ≈ 10 min), embed the survivors' query vectors (SFR,
≤ 2 in flight, seconds), and run the existing Stage 0b′ harness (`s0b_retrieve` / `s0b_score`,
all 6 arms × 3 modes, 16k budget, ≈ 30 min). **Decides:** whether questions of this shape land
in the reach window at all. If pooled reach on the survivors is still > 0.9, the hypothesis is
wrong at this threshold and step 2's sweep is the next thing to try before spending on
generation.

**Step 1 — dev generation under the hard rule (≈ 2 h Scout).** Regenerate on the dev topics'
documents with pass B instructed to prefer shared entities and forbidden deixis; target
≥ 150 survivors from ≈ 1,000 candidates. **Decides:** yield (the number that sets step 3's
cost).

**Step 2 — calibrate the rule on dev (≈ 40 min retrieval, no LLM).** Run Stage 0b′ on the
dev hard set. Read pooled reach (all arms × modes) and σ_d per contrast. Guard 1 as written:
top-10 sets differ ≥ 25 % (will pass) and EPACK@16k ∈ [0.15, 0.90] for every arm; add the
same window on ERET, which is the endpoint that was at ceiling. If reach is above the window,
raise the df threshold and re-screen (no regeneration: A and B are pre-retrieval filters over
already-generated candidates). **Decides:** the threshold, and — via guard 3 — the
confirmation count `n = ((1.96 + 0.84) σ_d / 0.05)²` per contrast, joint power printed.
Arithmetic to expect: for a paired binary endpoint the per-query variance is roughly the
discordance rate q, so n ≈ 3,136 q; q ≤ 0.10 → n ≤ 314; q = 0.19 hits the 600 cap. The
present easy set has q ≈ 0.035 (n ≈ 110) only because almost nothing is discordant.

**Step 3 — write the amendment (owner sign-off).** r3 §11 gains the rule A–G with the
calibrated threshold, the two-stage justification, and the estimand change: the confirmatory
pointed statement is now about *pointed questions without a unique lexical key*, and the
write-up must say that the easy class was measured separately (Stage 0b′) and found
insensitive to chunk size. Family unchanged: four one-sided NI tests, α = 0.025 each.

**Step 4 — confirmation generation (4.5–7 h Scout, unattended).** Generate on the
**confirmation topics' documents** under the frozen rule to the count from step 2, cap 600.
No retrieval, no arm is run, nothing is read except the screens' pass/fail counts and yield.
Runs under the same `QUARANTINE` guard as `s0c_*`.

**Step 5 — human read on the hard set (≈ 50 pairs × 2 readers, in the Grading view).** §11
guard 2's extra question — *does a delivered passage other than the construction passage
answer the question?* — matters more here than on the easy set, because rule F is a Scout
judgment and its false-negative rate is what bounds gold under-count. This read is scheduled
with the CDS read, same readers.

**Step 6 — freeze, then Stage 2 on both populations.** Reach and containment on CDS (as
registered) and on the hard pointed set; the size contrasts return to confirmatory status on
the pointed population per §10 item 5(c) if guard 1 passed in step 2.

| step | wall time | endpoint load | reads confirmation material? |
|---|---|---|---|
| 0 pilot | ≈ 1 h | mango ≤ 4, SFR ≤ 2, :50052 ≤ 4 | no |
| 1 dev generation | ≈ 2 h | mango ≤ 4 | no |
| 2 calibrate | ≈ 40 min | SFR ≤ 2, :50052 ≤ 4 | no |
| 3 amendment | owner | — | no |
| 4 conf generation | 4.5–7 h | mango ≤ 4 | generates on conf docs; reads only screen counts |
| 5 human read | 10–15 person-h | — | yes, under the read protocol |
| 6 Stage 2 | ≈ 1 h | SFR, :50052 | yes, after freeze |

New corpus embeddings: **none**, at every step. GPUs 6 and 7 untouched. No store is contacted;
rule F's BM25 is the in-process `s0b_bm25` over `docs.jsonl`.

## 6. Pros, cons, and what could still go wrong

**Pros.** It targets the mechanism #524 isolated (query hardness), not a proxy for it. The
population stays the one §1 declared — pointed, evidence-seeking, one passage — so a verdict on
it is a verdict about the product's stated use. Step 0 tests the idea for an hour before any
generation is bought. The two-stage design keeps the confirmation set free of outcome
selection and keeps the quarantine intact. Rule F turns "many documents mention this" from a
gold-under-count hazard into a screen. Everything reuses the frozen matrices and the Stage 0b′
harness, which reproduced its own numbers to four decimals in #524.

**Cons.** The estimand narrows: the study then says "chunk size matters for pointed questions
*without a unique key*" and, separately, "it does not matter for those with one". Both are
true and useful, but the headline is conditional. Yield is unknown; if it is under 15 %, step
4 is a fleet-day. Rule F depends on a Scout judgment per competitor; a lenient Scout rejects
good questions (cost), a strict one lets multi-source questions through (under-counted gold —
bounded by step 5). Discordance may be too high to power at 600: a hard set with reach ≈ 0.5
and arms that disagree on a fifth of the queries needs the cap exactly; guard 3 then
re-declares the population a reported secondary, as already pre-registered.

**What it does not fix.** CDS stays underpowered on the size contrasts (item 5(c) still
applies to CDS). Generated questions are still generated; the dev-tenant query-log issue
(#502) is the path to real ones.

## 7. How it serves the goal

The owner's decision is how coarse and cheap the production index can be for the questions
research agents will actually ask. Stage 0b′ answered half of that: when the question carries
its own key, the index can be the cheapest one (2048 or neighbours are limited by the 16k
budget, not by retrieval). The hard set answers the other half — the questions where the
retriever must discriminate among near-duplicates on topic — which is where a finer chunk is
*expected* to earn its cost. If it does not, even there, the cheapest index wins outright and
the study can say so with a confirmatory verdict rather than an unresolved one.

## 8. Decisions this plan needs from the owner

1. **Go / no-go on step 0** (one hour, reversible, nothing pre-registered yet).
2. **Accept the estimand narrowing** in §5 step 3, or keep the pointed population as a single
   mixed set (then the ceiling class dilutes the contrast and guard 3's count roughly doubles).
3. **Rule F's judge.** Scout (free, local) is proposed. Opus via the CLI would cost ≈ $0.02 per
   competitor check, ≈ $500 for 4,000 candidates × 20 competitors on the account's session
   budget — not recommended for the screen; keep Claude for the human-read calibration.
