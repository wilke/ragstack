# Pre-registration, glossary attachment benchmark round 3 — DRAFT v3, 2026-10-30. Not frozen.

> **Public copy, drafted for freezing.** Same procedure as round 2 (`../../glossbench-round2-2026-11/prereg/README.md`,
> *Freeze procedure*): on the owner's go the text as it stands is frozen, its sha256 and a UTC timestamp go
> into a private `PREREG.sha256` kept with the outputs, and the sha is recorded in this directory's
> `README.md` — **never in this file** (the drafter hashes `prereg.file` whole and refuses on a mismatch,
> §8, so a note at its top would make the committed file fail its own check). Freezing happens **after**
> every module of §9 is on `main` — **#874** at merge **`898e34d`** (2026-10-30; commits `1390051`,
> `bc9be96`, `cc955ea`) on top of round 2's harness (#861–#868, #870), and the BM25 exhaustion verifier
> **#877** at merge **`2254e4b`** (2026-10-30; commits `3dc7fdb`, `83b8d90`, `0ec8f1c`, `40c16c3`; §5 note
> 3) — both are, so `main` at `2254e4b` is the pin (§9) — and **before** any eval paper is
> sampled, any question is generated or any gateway call is made. Any later change is a `DEVIATIONS.md`
> entry, never an edit, with one pre-stated exception: the set-P promotion addendum of §3.5, whose every
> term is fixed here. The file names no tenant, host, endpoint, product or gateway owner; the bench copies
> are named by their collection id (§3) because the papers-OC owner asked that each leg's collection and
> chunk count be fixed at the freeze; the partner that holds the papers-OC collection is "the papers-OC
> owner".

**v1:** the v0 draft of 2026-10-29 revised against the merged code (#874, `898e34d`), the owner's
decisions of 2026-10-30 (§11), the papers-OC owner's input and its user's volume approval of 2026-10-30
(§3.3, §8, §11), and the **offline dry run of the committed pool on the real round-2 inputs** (*What had
been seen*, §3.1 step 5). **v2 (this text):** v1 with its two open preconditions closed or specified — the
**fields-mode dry build of P** and the **offline boost-probe candidate counts** (both at `898e34d`, clean,
`citable true`; §3.1 step 5, §5 note 2), the **BM25 exhaustion verifier** of #877 specified as it is written
(§5 note 3) — and the reranker test's finished record (#876, merge `734cfa6`) cited where round 3 leans on it
(§5 note 1). No hypothesis, arm, measure, threshold or seed changed between v1 and v2. **v3 (this text):**
v2 re-read against `main` at **`2254e4b`**, the merge of #877: the exhaustion verifier is now stated as merged
(header, §5 note 3, §8, §9), with the two review commits it gained after v2 was written — the
**`query_mismatch`** verdict, a hard stop (`0ec8f1c`), and the refusal of a `bm25_scope` that cannot see the
index's own tenant (`83b8d90`); the v2 "without it" branch is gone because its condition cannot occur; the
pin is `2254e4b`; §12 discloses the forking paths a reader would name and states that every gate was checked
against the sample it is read on before the freeze (round 2's lesson). The exclusion file of §3.1 step 2 is
named as private, which it is. No hypothesis, arm, measure, threshold or seed changed between v2 and v3.
Changes of substance against v0, v1 and v2 are listed in `README.md`.

**Terms** are round 2's (`../../glossbench-round2-2026-11/prereg/PREREG-glossbench-round2.md`, *Terms*:
arm, record, paper, pure, stamp, SFR, gold normalisation), plus: *attachment rule*: how a document's
glossary is rendered onto its chunks — **value** (the served default, `attach_only_with_value: true`: an
entry renders where its symbol occurs with a value) or **mention** (`attach_only_with_value: false`: an
entry renders wherever its symbol occurs as a whole token; every other cap unchanged — 12 entries, 1,200
characters, positional definitions). *Σ(paper)*: the set of symbol-like tokens the paper binds to a value
somewhere (`_glossbench_r2.value_statements` over the paper's chunks; uses no glossary). *V*: a
value-statement chunk. *M_num*: a chunk with no value statement that mentions a Σ(paper) token and prints
a number. *Gold differs*: a sampled chunk whose rendered field differs between the two attachment rules.
*Stamp*, round 3: `<base>[+merge=<mode>][+attach=<rule>]@file/1[+shuf/1:<seed>:<perm12>]` (§4).

**What had been seen before writing.** All of round 1 (`../../glossbench-2026-10/LABBOOK.md`); round 2's
frozen prereg, its `RESULTS.md` and `DEVIATIONS.md` (S1 = 12 / 3 / 12 against a gate of 150: every
retrieval leg GATE-NOT-EVALUABLE by construction of the sample; no record drafted, no retrieval run; the
GPT-5.5 batch cut at `max_tokens` on 30 of 58 calls); the reranker test's **finished record**
(`../../reranker-test-2026-11/RESULTS.md`, #876, merge `734cfa6`, with its `DEVIATIONS.md` D-1 to D-7;
what round 3 takes from it is in §5 and nothing in it adds a hypothesis here); the offline planning counts
of 2026-10-29 (`R3-COUNTS.md` in this directory: chunks with a field per attachment rule × arm × corpus,
per-paper densities, sampled pool sizes, power); the **offline dry run of 2026-10-30** of the committed
pool builder (`glossbench_r3_pool.py build --dry --corpus-reach`, review of #874 at `bc9be96`, clean tree,
`citable true`) on the real round-2 chunk exports and arm files; and its **re-run at `898e34d`** (clean,
`citable true`; O and A re-confirmed, **P rebuilt in fields mode**, the mode the final P build uses) —
**counts only, no text, no model, store, API or gateway call**:

| set | eligible papers | sampled chunks V / M_num | **gold differs** (gate) | per stratum V / M_num | chunks with a field, corpus-wide: `value` / `mention` |
|---|---|---|---|---|---|
| O | **288** of 1,000 | 241 / 293 | **151** (≥ 100) | 72 / 79 (≥ 40 each) | 300 / 5,918 |
| A | **28** of 1,000 | 50 / 150 | **56** (≥ 40) | 16 / 40 | 7 / 6,246 |
| P | **109** of 275 (7 excluded, §3.1 step 2) | 108 / 112 (**fields-mode** build at `898e34d`, 300 jobs; §3.1 step 5) | **80** (≥ 60) | 42 / 38 | 509 / 2,236 |

The P row is the fields-mode reading; the open-mode dry run at `bc9be96` read 116 (56 / 60) on 139 / 145
sampled chunks, and §3.1 step 5 says why the two differ. Every gate is **attainable with the sampled sets
this design draws** — the count round 2 did not run before freezing (its `DEVIATIONS.md` D-5) has been run
here, in the mode each set is built in. **No retrieval result under any attachment
rule other than the served one exists**, apart from the #754 design experiment (mention attachment at
boost 2 lifted whole symbol-dense papers into the top 10 on a development copy; a design note, not a
result). Round 3's design is chosen because of these counts, and it is a fair test because every record
is new, every paper sample is seeded, the population is defined without any glossary, and the glossary is
a fixed factor shared by both attachment arms.

## 1. Question

Holding the glossary fixed (the lab-book default model, Qwen3.6, pure, the round-2 files), does rendering
each entry onto **every chunk that mentions its symbol** find more records than rendering it only where
the symbol **states a value** — on the chunks the served rule cannot reach (a quantity printed without
`symbol = value`), and without losing on the chunks it can? The two strata are reported separately. The
operational choice of attachment rule is made **after** this round, on these results plus the distractor
load (§5 descriptives); the round informs it and does not make it.

## 2. Hypotheses

Confirmatory family **F** = H1–H3 on **set O** (§3), condition `base` (hybrid + rerank), outcome
**found@10** (§5), **Holm over 3** at α = 0.05. These are the defaults of
`glossbench/found10_analysis.py` (`R3_HYPOTHESES`, `R3_DEFAULT_ANALYSIS`: `family_corpora ["O"]`,
`family_condition "base"`, selected by `analysis.round: 3`) and of the example config
`glossbench/examples/found10-config-r3.example.json`. F may grow once, to six, by the set-P promotion
addendum of §3.5 (H1-P, H2-P, H3-P; Holm over 6); nothing else can change it. Each member is tested on
**its own stratum**; the family is not one record set read under three contrasts.

| id | hypothesis | stratum | type | contrast |
|---|---|---|---|---|
| H1 | mention attachment finds more records where the symbol is mentioned but states no value (the served rule's blind spot) | **M_num** | superiority, one-sided | `mention` − `value` > 0 |
| H2 | mention attachment changes what is found where the served rule already attaches | **V** | **two-sided** | `mention` − `value` ≠ 0 |
| H3 | the *right* entries beat *some* entries under mention attachment | V ∪ M_num | superiority, one-sided | `mention` − `mention-shuf` > 0 |

**The hypothesis is a gain confined to the M_num stratum** (the papers-OC owner's framing, 2026-10-30).
On a V chunk the served rule already renders the entry of the symbol that states the value; the mention
rule can only *add* entries — for other symbols the chunk mentions — on the gold chunk and on every
distractor that mentions any glossaried symbol. So H2 is two-sided on purpose: more entries on the gold
chunk may help (the locality argument), more glossaried distractors may hurt (the #754 pattern), and the
operational question is whether either is large. **No change in V is a legitimate outcome of this
design, not a disappointment**; a distractor penalty in V would be a finding; a confirmed H2 is reported
with its sign (`found10_analysis.holm_family_r3` records `sign`). There is no H4 (wording proxy): with
the glossary fixed, the proxy pass under `mention` is a superset of the pass under `value` by
construction; the proxy is a **manipulation check** (§5 M2), computed before any retrieval, not a
hypothesis.

**Secondary (reported, outside F, unadjusted; `R3_SECONDARY` plus the per-condition tables):** H1–H3
under `bm25` and `norerank`; H1–H3 on set P (with its status label) and on set A (descriptive leg, §3);
the pooled two-sided `mention` − `value` on V ∪ M_num; `mention` − `none` and `value` − `none`; the 2 × 2
model × attachment table (`rules-value`, `rules-mention`, `value`, `mention`: `rules-mention` −
`rules-value`, `mention` − `rules-mention`, `value` − `rules-value`, all two-sided) on every set; the
M_num split into *symbol-bound* and *incidental* records by the drafter's `symbol_is_the_quantity`
(§3.2; `secondary.m_num_split`).

**Exploratory:** anything else, labelled so in every sentence that quotes it.

Not confirmed is reported as *not confirmed*; "no difference" is never written. A contrast whose minimum
attainable p exceeds α is **GATE-NOT-EVALUABLE** (`found10_analysis.min_attainable_p`), not a null.

## 3. Eval sets

Every set runs through the committed runner (§5) against the **bench copies** of the three indices,
named here with their chunk count at the freeze; the live collections are never touched (the papers-OC
live collection has drifted since the bench copy was taken — one paper ingested 2026-10-30 — which is
one more reason the copy is the fixed object):

| set | corpus | bench collection (chunks @512) | papers | records drafted (target) | role | status label |
|---|---|---|---|---|---|---|
| **O** | OA-1000 (12,560 units) | `bench-oa-tok512o0-261028` (**32,765**) | **50** | 50 × (6 V + 6 M_num) ≈ 530 (dry run: 534 sampled chunks) | **carries F** | *our three-layer check*, owner-spot-checked |
| **P** | papers-OC (275 papers) | `bench-openchia-tok512o0-261028` (**14,456**) | 25 | 25 × (6 + 6) = 300 jobs, fields mode (§3.3; dry build: 300 jobs on 220 sampled chunks) | replication; promotable (§3.5) | *scripted-clean, judged sample of 50, error rate X% (95% CI …)* |
| **A** | ASM-1000 (1,000 articles) | `bench-asm-tok512o0-261028` (**27,418**) | 25 of 28 eligible | 25 × (2 V + 6 M_num) = 200 | **descriptive only** | *our three-layer check*; **floor quoted beside every number** |
| R1 | the papers-OC owner's 155-record set (7 papers, 138 from one) | the papers-OC bench copy | as is | — | replication, one-paper reach stated | reviewed-by-holder |

**Why A cannot carry a hypothesis** (stated before the freeze, from the counts): the served rule renders
a field on **7 of 27,418** ASM chunks and on **0** of the sampled chunks of the 25 eligible papers (dry
run: `value` reach 0 in both strata); **28** papers meet the eligibility rule; the 25-paper set yields
**56** gold-differs chunks (≈ 125 accepted M_num records) and power ≤ .52 at d .10 / share .85, .82 at
d .17 / .85 (§7). On A the `value` arm is empty on every sampled gold chunk, so the A contrast reads *a
glossary on the gold chunk vs none*. A is kept (owner, D4) because it is the corpus where the served
rule reaches nothing — the blind spot at its largest.

### 3.1 Papers, population, strata, quotas, gate (`glossbench_r3_pool.py build --set <S>`)

All of this is computed **without any retrieval** and recorded in `pool-manifest-<S>.json` before the
first draft call (#874; `POOL_SCHEMA glossbench-r3-pool/1`; the round-2 module `glossbench_r2_pool.py`
is reused and unchanged, `test_round2_pool_module_is_unchanged`). The glossary files are read at step 2 —
unlike round 2 there is no generation to protect, the files are frozen by sha256 in the private config,
and the glossary is a **fixed factor shared by both arms**, so reading it cannot favour one attachment
rule over the other. Design constants are `glossbench_r3_pool.DEFAULTS` (seed base **`261103`**,
`n_papers` 50, `min_v` 2, `min_m` 2, `min_pop` 10, `require_glossary` true, `v_per_paper` 6,
`m_per_paper` 6, `gold_differs_gate` 100, `gold_differs_gate_per_stratum` 40) with the per-set overrides
`per_set` = P {`n_papers` 25, gate 60, per stratum 0}, A {`n_papers` 25, `v_per_paper` 2,
`m_per_paper` 6, gate 40, per stratum 0}; the effective values are recorded in the pool header and the
manifest (`quotas_per_paper`, `quotas_overridden`).

1. **Populations, per chunk, no glossary** (`populations`): **V** = `value_statements(content)` non-empty
   (round 2's definition, the pinned `STAT_TOKENS` stoplist, recorded as `vs_stoplist`); **M_num** = no
   value statement, mentions a token of Σ(paper) as a whole token (`symbol_pattern` on the canonical
   text), prints a number (`has_number`). Everything else is outside the population (attachment cannot
   reach a chunk that mentions no symbol). Round 2's S3 is dropped. Corpus-wide: O V 3,369 / M_num 8,285;
   A 341 / 1,104; P 3,142 / 7,612 (dry run, `population_corpus`).
2. **Eligible papers:** V ≥ **2** and M_num ≥ **2** and V + M_num ≥ **10** and the fixed glossary renders
   ≥ 1 chunk of the paper under mention (`require_glossary`; manifest `papers_dense`,
   `papers_dense_without_glossary`, `papers_eligible`). The last condition is symmetric between the arms
   (a paper with an empty glossary is a tie on every gold chunk under both) and is the papers-OC owner's
   density criterion made explicit. Dry run: **O 288** (348 dense, 60 without a glossary), **A 28** (37,
   9), **P 109** (136 dense, 27 without a glossary; the same 109 in the open-mode and the fields-mode
   build — eligibility does not depend on the mode). **Exclusions on P** (`exclude_papers`, one or several files, `read_exclusions`; the
   reader skips blank and `#` lines): the **7 papers of the papers-OC owner's R1 gold**, mapped to the
   bench copy by PMC id as round 2's D-3 records (the exclusion file is private — it names the partner's
   papers — and carries 8 lines: the 7 ids and one `#` comment line; manifest `excluded_papers 7`); 3 of the 7 were otherwise eligible (112
   without the exclusion → 109 with it). **The pilot's papers (§3.4) are added as a second exclusion
   file before the final P build**, so the final eligible count is expected at 109 − 5 = 104; at the dry
   run no pilot had been drawn.
3. **Paper sample:** seeded (`random.Random("<seed_base>|<set>").sample`, i.e. `261103|O`, `261103|A`,
   `261103|P`) from the sorted eligible ids: **50** (O), **25** (P), **25** (A; all if fewer).
4. **Chunk sample, per paper** (open mode, O and A): seeded order per stratum
   (`<seed_base>|<set>|chunks|<stratum>|<paper>`); the first **6 V and 6 M_num** on O, **2 V and 6
   M_num** on A (all if fewer). On P (fields mode, §3.3) round 2's arm-blind lexical prefilter names each
   population chunk's candidate fields and round 2's quota plan (`glossbench_r2_fields.plan_strata` on
   the strata V / M_num, `strata_order`) assigns 6 + 6 jobs per paper; a chunk with a job is "sampled".
5. **Gate, computed by the pool and read before any draft:** the number of sampled chunks whose rendered
   field **differs** between the two attachment arms (each rendered by `Glossary.for_chunk` under its own
   file's config, as the backfill renders) must be ≥ **100** on O (and ≥ **40** per stratum), ≥ **60** on
   P, ≥ **40** on A; else the set is **GATE-NOT-EVALUABLE** for retrieval and is reported as such
   (`gate.status`, `gate.reason`). No paper is added after the gate is read. The gate is on *gold
   differs*, not on reach, because a record whose gold field is identical under both arms can differ
   only through distractors; the per-arm reach is printed beside it (sampled and pool-wide, per stratum).
   **Dry-run readings (2026-10-30, same seeds and sizes as here; at `898e34d`, clean, `citable true`):**
   O **151** (V 72, M_num 79); A **56** (16, 40); **P 80 (V 42, M_num 38) in fields mode** — every gate
   `EVALUABLE`, O with a margin of 51 over its gate and 32 over its per-stratum gates, P with a margin of
   20. Planning expectation from `R3-COUNTS.md` § 5 was O ≈ 150 [p5 ≈ 123], A ≈ 61, P ≈ 85; the dry run
   is one seeded draw and agrees on all three. **Why P reads 80 and not the 116 of the first dry run:**
   the `bc9be96` dry run built P in **open** mode (its config carried no field list) and sampled 139 V /
   145 M_num chunks; the fields-mode build, the mode the final P build uses, plans 300 jobs (150 per
   stratum; the V plan 4 short of its quota) on **108 V / 112 M_num** sampled chunks, because the
   arm-blind lexical prefilter admits only the population chunks that name a candidate binding field
   (round 2 saw the same narrowing: 72 of 117 V chunks admitted on P). The gold-differs *share* of the
   sampled chunks is nearly unchanged between the two builds (V 42 / 108 = 39 % against 56 / 139 = 40 %;
   M_num 38 / 112 = 34 % against 60 / 145 = 41 %); the count fell because fewer chunks are sampled, not
   because the prefilter selects chunks the glossary does not reach. 80 is the reading that counts, and it
   is the one quoted everywhere in this text; 116 is history. Fields-mode build detail, from the manifest
   (counts only): `in_set` split 13 / 13 asserted; realised jobs per field — the 10 new-ground fields
   carry 255 jobs (51, 44, 42, 35, 30, 16, 14, 11, 9, 3), the 12 `in_set` fields 41, the 4 constants 4
   (cap 2 per constant respected), 3 fields 0; per-arm reach on the sampled chunks, `value` / `mention`:
   V 36 / 57, M_num 0 / 38 (the M_num gold-differs count equals the mention reach, as §3 predicts). The
   manifests of this dry run are private (`pool-manifest-P.json` sha256 `3369a867…`, `-O` `30822bc1…`,
   `-A` `32a23e2c…`); the public record is these counts with those shas.
6. **Manipulation checks** (all refusals, `SystemExit`, before anything is written): the export's chunk
   count equals `expected_chunks` (32,765 / 27,418 / 14,456; dry run `match true` on all three); the two
   attachment arms are **one glossary under two rules** (`arm_pair_problems`: the value arm's header rule
   is `value` and the mention arm's `mention`; configs equal but for `attach_only_with_value`; every
   `+attach=` mark agrees with its header; same documents; every row's halves, entries, drops, identity
   and version-up-to-the-mark equal; dry run `arm_pair_identity ok`); every arm file resolves every
   eval-paper document (`verified`, no `unverified`; dry run O 1,251 / A 25 / P 25 documents). The value
   file's rendering equals the served pipeline's (`test_render_parity_with_for_chunk_mention`;
   `R3-COUNTS.md` fidelity check: 0 mismatches on 400 seeded chunks per corpus × 4 arms × 2 rules; the
   dry run's `corpus_reach`: **300** / 7 / 509 chunks with a field under `value` — the round-2 manifests
   quote **298** on O because they count V chunks only; the pool's 300 is 298 in V + 1 in M_num + 1
   outside, two chunks whose glossary symbol states a value with a token `value_statements` stoplists;
   the committed pool prints 300 and that is the figure used here). Outputs are round 2's (`pool-<S>.jsonl`
   private, counts-only manifest with `experiment_provenance()`), plus per-paper densities **by paper
   rank** (`per_paper_by_rank`, no ids) and the eligible-paper density quantiles (`eligible_density`).

### 3.2 Drafting (`glossbench_r2_draft.py`), acceptance, spot-check

Round 2's §3.2–3.3 apply unchanged (Gemini 2.5 Pro drafter, temperature 0, `max_tokens` **4096**,
`fallback_model ""` and `max_tokens` asserted in every body, ≤ 4 in flight (`MAX_INFLIGHT`), the served
model id recorded per call, blind to any glossary, arm or attachment word; layer-1 mechanical codes;
**Claude Opus 5.5 verifier via the CLI, off-gateway**; owner spot-check of 40 per set with the 0.85
validity rule, seed `<spot.seed_base>|<set>` with `spot.seed_base` set to **`261103|spot`** in the private
config; one redraw after a layer-1 reject; every attempt logged; gold = quote-bearing chunks only), with
these additions (#874, `bc9be96`; `glossbench_r2_checks.R3_STRATA`, `SYMBOL_BOUND_KEY`; pinned by
`tests/eval/test_glossbench_r3_draft.py`):

- **A record keeps its chunk's stratum** (`V` or `M_num`; never relabelled `S3`). The drafter recognises
  a round-3 pool by its header schema (`R3_POOL_SCHEMA`).
- **M_num chunks use a symbol prompt of their own** (`DRAFT_SYSTEM_MNUM`, `_MNUM_TASK`): the drafter is
  told which of the paper's symbols the chunk mentions (`sigma_tokens`, arm-blind: from the paper's own
  value statements), asked for a quantity whose value the passage prints, and for the symbol the passage
  uses for it, or `""` if none. `symbol_missing` is not a reject on M_num. The answer gains one field,
  **`symbol_is_the_quantity`** (true/false: is the quantity asked the one a mentioned symbol stands for),
  **required to parse, recorded, never a reject**; it splits M_num into *symbol-bound* and *incidental*
  records for the secondary table (`analyse_r3`, `split_field`). The drafter is not told why. Round 2's
  prompts are byte-identical (`test_m_num_prompt_unit_and_round2_prompts_unchanged`); no glossary, arm or
  attachment word reaches the drafter.
- **Yield assumption** 1.3 attempts per accepted record (round 2's); the gate of §3.1 is on sampled
  chunks, the power table (§7) on accepted records.

### 3.3 Set P: fields mode and the papers-OC owner's sampled acceptance (carried over)

Round 2 §3.4 (a)–(b) apply unchanged: the binding export **v2** (`binding_fields_261029_v2.json`, sha256
`1c3778918c76ae206de0fb1b66e256da33faebf8840d6c49ca232552b2911dfa`, 9,445 bytes, 26 fields; 13 / 13
`in_set` split asserted from the papers-OC owner's pairs file as round 2's D-3 fixed it, sha256
`f8d3b7f1…`; constants capped at 2; new-ground fields 2 : 1; `term_leak` ≥ 0.5 and `term_abbreviation`
rejects; value as printed with the dimension check), the papers-OC owner's scripted checks on every
record plus a judged random sample of **50** with its seed, and the status label printed beside every P
number. The arm-blind lexical prefilter of fields mode acts on the drafter's jobs only; populations,
eligibility and strata are §3.1's (`test_fields_mode_pool_plans_both_strata`). On an M_num job the
field-mode prompt carries the `symbol_is_the_quantity` key too (`DRAFT_SYSTEM_FIELDS_MNUM`,
`field_draft_messages(..., symbol_bound=True)`).

**Gold rule on P.** Our gold stays *quote-bearing chunks under round 2's gold normalisation* (NFKC,
casefold, whitespace removed). The papers-OC owner's own acceptance applies **its** rule (NFKC, whitespace
runs collapsed, casefold, containment, first-or-last 60 % of a quote cut by a boundary) — the two differ,
as the reranker test's D-3 found on 16 of 155 held records, and a `cut_by_boundary` record never reaches
the papers-OC owner. Every P figure names the rule its gold was built under.

### 3.4 Set P pilot (carried over with round 2's D-1)

The round-2 pilot never ran. It runs first here, unchanged: **100 records from 5 papers** (`--pilot 100
--papers 5`, papers seeded `<pool seed>|pilot` = `261103|P|pilot`), fields mode, layers 1–3, the adapter
(`export-partner`), the papers-OC owner's verdicts on all 100 (`import-partner-verdicts`); data for no
hypothesis; no retrieval arm; its papers excluded from P (§3.1 step 2). **Volume, approved by the
papers-OC owner's user on 2026-10-30:** 100 pilot verdicts + scripted checks on ≈ 220 accepted P records +
a judged sample of 50 (≈ 150 judged, ≈ 320 scripted); judging turnaround, not tokens, is the likely
bottleneck (the papers-OC owner will give an estimate).

### 3.5 Promotion of set P into F: one bounded addendum

Round 2 §3.5 and its D-1 verbatim, with the hypotheses renamed. **When:** after the pilot's verdicts are
imported and **before any non-pilot P record is drafted** (an addendum dated after the first non-pilot P
draft call is void). **Condition:** the Wilson 95% upper bound of the papers-OC owner's judged pilot error
rate ≤ **0.15** on ≥ **30** judged records, computed **from the raw counts** (`n`, rejects) with
z = **1.959963984540054**, no continuity correction, compared at full float precision
(`glossbench_r2_draft.wilson`; `import-partner-verdicts` writes the `promotion` block — `z`, `threshold`,
`min_judged`, `upper_bound`, `upper_bound_6dp`, `met`); at 100 judged: at most **8** rejects (bound
0.149981), 9 rejects fail (0.162262); the addendum quotes that block and re-derives nothing by hand.
**What is added, exactly:** **H1-P, H2-P, H3-P** — same strata, types and contrasts, on set P
(`analyse_r3` reads a promoted member as `[A, B, kind, outcome, strata, "P"]`); Holm over **6**; gated or
absent members at p = 1.0. **If the condition fails,** P stays a replication with its status label, with
no further option. **Form:** one dated section appended to this directory's `README.md`, quoting n,
rejects, seed and the 6-decimal bound, and the one word *promoted* or *not promoted*; its sha256 joins the
private `PREREG.sha256`.

## 4. Arms

| arm | glossary file | attachment | source | stamp on a chunk | new model calls |
|---|---|---|---|---|---|
| `value` | round-2 pure Qwen3.6 (`qwen36p`), per corpus | value (served) | round-2 file as is | `llm/2:<Qwen3.6 id>:…+merge=llm-only@file/1` | none |
| `mention` | same file, re-configured | mention | `glossary_file.py rederive --attach mention` | `…+merge=llm-only+attach=mention@file/1` | none |
| `mention-shuf` | `mention`'s rendered fields permuted among the chunks that carry one | mention | backfill `--glossary-file <mention file> --shuffle-rendered 261103\|shuf` | `…+attach=mention@file/1+shuf/1:261103\|shuf:<perm12>` | none |
| `none` | empty field everywhere | — | round-2 `none` file (`rederive --merge none`) | `none+merge=none@file/1` | none |
| `rules-value` | round-2 `rules/3` file | value | as is (the production default) | `rules/3@file/1` | none |
| `rules-mention` | same, re-configured | mention | `rederive --attach mention` | `rules/3+attach=mention@file/1` | none |

- **Pure Qwen3.6 files** are round 2's, byte-identical: O sha256 `4c6f8bef88612237…`, A
  `59d5ce2ee9eba1de…`, P `d29d245daed428ea…`; the `rules/3` files O `5facfee4041e4c03…`, A
  `e17725422ebdfe59…`, P `0e351ee49409a033…` (full sha256s in the private config and in `counts.json`).
  File counts: O 12,560 documents, 1,593 with an LLM half, 3,209 entries, 2 degraded; A 1,000 / 998 /
  2,008 / 2; P 275 / 275 / 1,857 / 0. Their census (`rows_with_entries` 777 / 545 / 185) is re-recorded by
  the committed command.
- **`rederive --attach <rule>`** (#874, `ragstack.ingestion.glossary_file.rederive_file`, `ATTACH_RULES`,
  `ATTACH_MARK`; CLI `python/scripts/glossary_file.py rederive --attach {value,mention} [--merge …]`)
  writes a copy whose header `config.attach_only_with_value` is set to the rule and whose every
  `merged.version` is marked `+attach=<rule>` after any `+merge=` marker; halves, entries, drops,
  identity, `degraded` and `created` are unchanged (`rows_entries_changed` 0 in the census;
  `test_attach_mention_changes_only_the_rule_and_the_mark`). Every reader renders under the file's own
  config — the backfill (`backfill_symbol_glossary.py --glossary-file`, writes exactly `symbol_glossary`
  and `metadata.symbol_glossary_version`; `test_backfill_renders_a_mention_file_and_writes_only_two_fields`),
  the wording proxy, the pool — so **no re-indexing** is involved: content, embeddings and the vector
  store are untouched. The mention files' sha256s are recorded at build time in the private config and the
  manifests (`arms.<arm>.sha256`); the dry run's copies are scratch and are rebuilt by the committed
  command.
- **Stamp grammar** `<base>[+merge=<mode>][+attach=<rule>]@file/1[+shuf/1:<seed>:<perm12>]`
  (`found10.split_stamp_attach`). An arm config **with** the `attach` key (a rule, or `null` = no slot:
  a file loaded as is, the served value rule) is checked on the slot by `stamp_allowed`, so a value file
  passed off as the mention arm, or the reverse, is refused on the stamp; a round-2 arm config (no key)
  refuses any stamp carrying an attach slot. With `--glossary-file`, the file's header rule must be the
  arm's and every row's mark must agree with the header (`check_arm_file_attach`; contract tests
  `test_found10_r3.py`: `test_contract_stamps_from_the_tool`, `test_swap_record_of_a_mention_arm`,
  `test_every_attach_arm_stamps_differently`). `swap.sh` is unchanged: its census is the `terms`
  aggregation on `metadata.symbol_glossary_version`, and the runner's `verify_swap_record` reads every
  stamp through `stamp_allowed`.
- **OA estimand** as in round 2: float units below 1,500 characters have no LLM half, so under both
  attachment rules they render ""; O figures are article-unit-dominated and say nothing about float
  units. `rules-value` / `rules-mention` can attach on float units.
- **Shuffle rule** as in round 2 (`glossary_shuffle.shuffle_rendered`, seed `261103|shuf`, ES `_id`
  keying in the backfill, `chunk_id` in the proxy; `fixed_points` and `multiset_sha256` recorded; the
  permutation composes with the attach mark, `test_backfill_shuffle_composes_with_the_attach_mark`).
  Under mention the permuted population is the ≈ 18 % of chunks that carry a field (5,918 on O), not
  0.9 %.
- **Distractor load**, printed with every figure (dry run, `corpus_reach`, chunks with a non-empty
  field, corpus-wide): O `value` **300** / `mention` **5,918** / `rules-value` 104 / `rules-mention`
  1,720; A 7 / 6,246 / 3 / 850; P 509 / 2,236 / 241 / 1,614. Where the mention field lands (by
  population, qwen36p): O V 1,076 / M_num 1,917 / outside 2,925; A 89 / 207 / 5,950; P 908 / 994 / 334 —
  on A 95 % of the chunks that gain a field are outside the population. Share of eligible-paper chunks
  with a field under mention: O 21.9 % (2,864 of 13,050), A 27.0 % (399 of 1,479), P 17.8 % (1,850 of
  10,376, after the exclusions).

## 5. Measures

**M1 — found@10**, round 2 §5 M1 unchanged (`found10.py run`; conditions `base`, `bm25`, `norerank` as
the example config requests them; `FIXED_REQUEST`; swap record checked; census before and after;
inactive-reranker rule; `server_assumed` multiplier 2 / RRF k 60; ≤ 4 in flight; > 2 % failed calls a
hard stop), with four notes fixed here:

1. **The reranker reads the field.** `rerank_passage` = `content + "\n" + symbol_glossary` under
   `RERANK_INCLUDE_SYMBOL_GLOSSARY` (default on), with hydration of vector-only candidates from the text
   index, so `mention` changes the reranker's input on every chunk it reaches; `norerank` isolates the
   BM25 side and both are reported. The cross-encoder sidecar's `MAX_LENGTH` is 4,096 tokens (the
   reranker test's census, #876: no query–passage pair over 745 XLM-R tokens in any rendering on either
   set), so no passage is truncated. **What the reranker test's record (#876, `734cfa6`) adds, and what it
   does not:** on the papers-OC bench copy, *descriptively and under gates that make it untestable* (7
   clusters, 19 exhausted `bm25@100` lists), what the reranker reads over the same 50 candidates changed
   found@10 — `content` 43, `content+gloss` 52, `content+shuffled` 41 of 153 under `rules/3` — and the
   reranked arms' loss against BM25's own order was **ordering, not the pool** (the fused and BM25 pools
   held gold for 83 and 82 records at depth 50). Round 3 takes from this only that **attachment is a
   reranker-side manipulation as much as a BM25-side one** — `mention` puts glossary text in front of the
   cross-encoder on 15–23 % of chunks instead of 0.03–3.5 % — which is why `base`, `norerank` and `bm25`
   are all run and reported per arm. It is an **exploratory** reading from one run on one corpus; it adds
   **no hypothesis** to F or to the secondaries. On docs-A the same record found the shipped default well
   ahead of BM25 alone (its H1 and H2 reversed, H3 confirmed), with a glossary on 1 of 1,113 chunks — a
   result about fusion and the reranker, not about attachment, and not used here.
2. **The boost probe may be structurally `not_testable` under either rule.** The probe
   (`glossary_boost_probe.py`, #867) wants a glossary token that occurs in no chunk's content and in at
   most 10 glossaries. The reranker test's run of 2026-10-30 found **0 qualifying chunks** on the
   papers-OC bench copy under `rules/3` (14,456 chunks, 241 glossaried: every ≥ 5-character ASCII
   glossary token also occurs in some chunk's content) — `not_testable` is a property of that corpus and
   glossary, not a failed check, and the reranker test continued with `--boost-declared 1.0` (reading
   `declared`; its **D-6**, now in the merged record, #876). Under mention a token's glossary frequency
   rises further. **The candidate search was run offline before the freeze** (`glossary_boost_probe.pick_probes`
   on each corpus's chunk export plus each arm's rendered fields, at `898e34d`, clean; counts only; the
   private `probe-candidates` file has sha256 `e4966c6c…`). Chunks with a qualifying token, per arm, against
   the probe's minimum of **3** (`MIN_PROBES`):

   | corpus | `value` | `mention` | `mention-shuf` | `rules-value` | `rules-mention` |
   |---|---|---|---|---|---|
   | O | 0 | 2 | 2 | 0 | 0 |
   | A | 0 | **18** | **18** | 0 | 0 |
   | P | 0 | 1 | 1 | 0 | 0 |

   (`none` renders no field and has no cell; `mention-shuf` permutes `mention`'s fields among the same
   chunks, so its candidate set is `mention`'s.) The probe is therefore **structurally `not_testable` in
   every cell except A × `mention` and A × `mention-shuf`**. The zeros under both value rules repeat the
   D-6 mechanism — every candidate token also occurs in some chunk's content — which no widening of the
   glossary-frequency cap can change. **Pre-declared, so no run-time decision is left:** (a) the cells with
   fewer than 3 candidates run with the boost **declared**: `--boost-declared 1` with the value copied into
   the private config, the value being the server release serving the bench copies (**v1.6.21**), which sets
   `TEXT_SYMBOL_GLOSSARY_BOOST` to 1 by management declaration; the reading **`declared`** is printed
   beside every `bm25` figure of that cell, never `pass`; (b) the two A × mention cells run the probe
   (`--probe-max-glossaries` left at the default; the request is recorded in the summary) and take its
   reading, `pass` or otherwise — `inactive`, `mixed` and `error` remain hard stops there; a `not_testable`
   on a stored field whose offline rendering had 18 candidates is a swap fault, caught by the census, and
   the leg is not scored; (c) on every leg the admin `/v1/config` is read **at the start and at the end**
   and the two reads must be identical (the reranker test's eight identical reads, hash prefix
   `f85cf2e2f73c36ab`, are the model); a difference stops the leg, which is re-run after the cause is
   found and recorded as a deviation. (d) **Behavioural evidence, descriptive and not a gate:** where note 3's
   verifier has counted a short `bm25` list, a second read-only `_count` of the same clause restricted to
   `content` may be made beside it (the verifier itself makes one count, with the server's full clause);
   the server's count above the content-only count means glossary-only matches were served, i.e. the
   glossary field is queried (boost > 0) — the reranker test saw this on 2 of its 5 exhaustion queries
   (D-6). When such a comparison is available it is printed beside the `declared` label of that cell; it
   says nothing about the boost's magnitude and lifts no label. No short list, no comparison: *absent*.
3. **A short `bm25` list may be exhaustion, not a clamp.** The reranker test saw `bm25@100` served
   short on 19 of 155 held records (5 distinct queries; 23, 36 and 79 hits) on the papers-OC bench copy,
   and verified by a **read-only `_count` of the server's BM25 clause on the bench index** that every
   served length equalled the match count (its **D-5**, #876): the lists were exhausted, not clamped —
   and, because that verification was done by hand beside the harness, the harness's mark stood and every
   set-P hypothesis there carried `bm25@100 under-served` regardless. The runner's depth read-back at
   `898e34d` marked an arm × condition GATE-NOT-EVALUABLE when any record was served short of the requested
   50 and had **no exhaustion path**. **#877** (`exp/found10-exhaustion`, merge **`2254e4b`**, 2026-10-30;
   `python/scripts/eval/bm25_exhaustion.py`, `found10._verify_exhaustion`; tests
   `glossbench/test_found10_exhaustion.py`, `test_reranker_test.py`) puts that path in the runner. Rule,
   fixed here as #877 writes it: with `census.es_url` set, a `bm25` list served shorter than requested
   costs **one read-only `_count`** on the arm's bench index, with the server's own BM25 query for that
   question (the store's `_build_query` / `_text_clause`, imported, not copied; filters assembled as
   `/v1/retrieve` assembles them; the clause reads `content+symbol_glossary` when the boost sign is
   positive — `active` or `declared` — and only the sign matters for matching), sent by the credential-free
   census client (`refuse_credentials`); eligible (`bm25_exhaustion.ineligible`) are `bm25`-mode requests
   with no graph leg, one query variant (`passthrough`) and one collection, which is every `bm25` leg here
   (`FIXED_REQUEST` sets `use_graph false` and `rewrite_strategies ["passthrough"]`). **Four verdicts**
   (`bm25_exhaustion.verdict`): count **= served → `exhausted_verified`**, the list is complete, the record
   is scored on it (a gold chunk absent from a complete match list is *not found*) and the list does not
   count as short, so the condition is not gated; count **> served on the exact tenant scope → `clamp`**,
   a **hard stop** (the run exits; nothing from it is scored until the cause is found); count **< served
   under either scope → `query_mismatch`**, a **hard stop** of a different kind (`0ec8f1c`): the index
   cannot match fewer chunks than the server returned for the same query, so the rebuilt query, filters or
   index are not the server's — never evidence either way, the verifier is fixed before anything is scored;
   anything else (no count, an unknown boost sign, an unscoped bound above the served length) →
   `under_served`, and the arm × condition stays GATE-NOT-EVALUABLE. **`corpora.<c>.bm25_scope`
   (`tenant`, `extra_tenants`) is set for every leg in the private config**, so every count is on the exact
   scope and never the unscoped upper bound (which can verify exhaustion but can never claim a clamp);
   `normalise_config` refuses a malformed scope, and — because `corpora.<c>.es_id_prefix` is set for every
   corpus (the shuffled arm's probe needs it) — refuses a scope under which the tenant that stamped the
   index is not readable (`83b8d90`: an undercount that happened to equal the served length would read as
   exhausted). The round-3 example config carries no `bm25_scope`; the private config adds it to every
   corpus, and a leg started without it is a deviation. Each such row records `exhaustion` {requested,
   served, es_count, verdict, scope, clause} — never the query text — and `collect()` reports
   `exhausted_verified` and `exhaustion_verdicts` beside `depth_short` per cell; both are printed beside
   every `bm25` figure. Hybrid and vector lists keep the gate as before. On P, `bm25` lists are expected to
   come up short on some records (reranker test D-5: 19 of 155 on the same bench copy) and to be verified
   exhausted; without the verifier they would have gated, which is why its merge preceded the freeze.
   `base` and `norerank` fuse a dense leg of full depth and are not at risk.
4. **Gold rule on P** as §3.3: round 2's normalisation for our gold; the papers-OC owner's rule for its
   acceptance; named beside every P figure.

**M2 — wording proxy** (`wording_proxy.py`, round 2 §5 M2 unchanged; it renders under each file's own
config) per arm on every accepted record's gold chunk: a **manipulation check**, not a hypothesis —
`mention` pass ≥ `value` pass on every record by construction (`value ⊂ mention` held on every chunk in
the planning counts and the dry run, `value_not_subset 0`); the realised pass rates and the shuffled
chance level are printed. It runs **before** any retrieval.

**Descriptives, always printed:** per-arm reach on the sampled chunks and per stratum; gold-differs
count; discordant counts per contrast; the run-to-run noise floor (one `value` repeat per corpus,
`repeat_arm`, `--repeat 2`); realised sizes (`descriptives.sizes`); distractor load (§4); degraded
counts; the boost-probe reading per cell; the depth read-back per cell.

## 6. Precision

Not measured in this round: the glossary is unchanged from round 2 / round 1, so precision is their
question (owner, D9). Any statement about entry quality quotes round 1's gold table with its labels.

## 7. Analysis (`found10.py analyse` with `analysis.round: 3`; `found10_analysis.analyse_r3`; `chunktrial_stats`)

Round 2 §7 unchanged (paper-clustered sign-flip, `n_perm` 10,000, seed **261103**, exact enumeration
≤ 20 non-zero papers, p = (1 + #{≥ obs}) / (1 + n); 90 % paper-bootstrap percentile CIs on the net share,
`n_boot` 10,000; Holm with gated or absent members at p = 1.0, the family never shrunk
(`holm_family_r3`; `test_r3_two_sided_loss_and_a_gated_member_enter_holm_at_one`); exact McNemar beside;
a record with a failed call on either side excluded and counted, `family_missing_as_not_found` as
sensitivity; `mde_split` beside every null — for H2 at α/2 and at the Holm worst case α/(2·3)), except:
**Holm over 3** (or 6 after §3.5), **each member on its own strata** (`run_hypotheses_r3`), and H2
**two-sided** (`alternative="two-sided"` of `chunktrial_stats.sign_flip_test`; a confirmed H2 carries
`sign`). Every analysis is the committed script; scratch analyses are not citable.

**Power, planning table** (`stratified_design.json` in this directory, from `R3-COUNTS.md` § 6:
`chunktrial_stats.simulate_power` on the sampled layouts of one seeded draw shrunk by 1/1.3, α/3,
between-paper SD of the gain share .15, 1,500 sims × 1,500 flips, seed 20261030; **a design assumption,
not a measurement**; the committed table is `analyse_r3`'s `power.realised` (`power_table_r3`, per member
on the realised layout, one- or two-sided as the member is, `--power-sims 2000`)):

| set, stratum (accepted ≈ sampled / 1.3) | one-sided d .10 s .85 | d .17 s .70 | d .17 s .85 | two-sided d .17 s .85 | d .25 s .85 |
|---|---|---|---|---|---|
| **O M_num (≈ 241) — H1** | .87 | .56 | **.99** | .97 | 1.0 |
| **O V (≈ 186) — H2 (two-sided)** | .74 | .43 | .95 | **.92** | .99 |
| P M_num (≈ 124) | .49 | .29 | .79 | .68 | .88 |
| P V (≈ 106) | .43 | .24 | .71 | .60 | .81 |
| A M_num (≈ 125) | .52 | .28 | .82 | .70 | .89 |

A round-1-sized effect (d .17, share .85) is resolved on O with power ≥ .92 in every member; a d .10 /
share .70 effect is not (≤ .33), and such a result ends *not confirmed* with its floor. H3 pools the
strata (≈ 410 accepted on O) and is at least as powered as H1. The dry run's sampled layout (534 chunks
on O) is within 2 % of the planning draw's (528), so the table stands as the pre-freeze expectation. On P
the fields-mode layout is **300 jobs over 220 sampled chunks** (several jobs can share a chunk, so a gold
chunk can be shared by records; clustering is by paper, so the sign-flip test is unaffected), ≈ 230
accepted records against the planning rows' ≈ 230 (106 + 124); the P rows stand as the expectation, and
`power.realised` on the accepted layout is the committed figure.

## 8. Stopping, exclusion, gates

Round 2 §8 applies (manipulation checks; gateway terms in code — `fallback_model ""`, `max_tokens`
always, ≤ 4 in flight, empty = failure → retry once → skip, `max_fail_consecutive` 5 / `max_fail_total`
20 pause, 429 backoff; the **notice gate**: `preflight --stage draft|verify --set S` prints one
`GATEWAY-NOTICE` per batch and `draft` / `verify` refuse to start without `--notice-acknowledged`; hard
stops; exclusions), with: **our own budget stop at 2.5 M gateway input tokens / 0.5 M output / 2,500
calls** (`limits.input_token_cap`, `output_token_cap`, `call_cap`; the plan is ≈ 1.45 M / 0.36 M / 1,450,
the volume the papers-OC owner's user approved on 2026-10-30; the gateway owner set no cap and no end
date, the per-batch size notice stays); the **gold-differs gate of §3.1 step 5** replaces round 2's reach
gate; the **arm-pair identity check** and the **chunk-count check** of §3.1 step 6 are hard stops; a
frozen-prereg sha mismatch (`prereg.file` hashed whole against `prereg.sha256`, every stage and resume,
`prereg_sha_mismatch`) is a hard stop. **Claude is used only through the CLI verifier, off-gateway**
(the papers-OC owner measured Claude Opus 5.5 through its gateway at 157 of 612 empty answers on a runner
arm — external, not re-measured; it does not bind here and is recorded as context). **No glossary model
is called**: no frontier arm, no `glossary_step.py`; the round-2 cap finding (D-4) does not bind and is a
precondition for any future gateway glossary arm (D11).

**GATE-NOT-EVALUABLE:** a set below its gold-differs gate; a contrast whose minimum attainable p > α; an
arm × condition whose served depth fell short and was not verified exhausted (§5 note 3); a `bm25` cell
whose boost probe is `not_testable` without a declared boost (§5 note 2); `base` identical to `norerank`
on every record. **Hard stops added at v2 and v3** (§5 notes 2–3): an exhaustion `clamp` verdict; an
exhaustion `query_mismatch` verdict (v3; the verifier, not the data, is wrong — nothing is scored until it
is fixed and the leg re-run, recorded as a deviation); two admin `/v1/config` reads of one leg that differ.
**Absent** is written *absent*, never a blank.

## 9. Code, pin, provenance

**On `main` at `898e34d` (2026-10-30), unit-tested and reviewed:** everything round 2 §9 lists (#861–#865
at `e3002ba`, #867 `48d5c73`, #868 `b5251db`, #870 `d87a02e`) and:

| item | module | PR, merge commit |
|---|---|---|
| re-configured attachment: `rederive --attach {value,mention}` (alone or with `--merge`), `ATTACH_RULES`, `ATTACH_MARK`, `version_attach`, `attach_rule_of_config`, `attach_mark_problems`; the backfill and proxy render under the file's config | `python/ragstack/ingestion/glossary_file.py`, `python/scripts/glossary_file.py`; tests `python/tests/unit/test_glossary_round3_attach.py` | **#874** (`exp/r3-attach`), merge **`898e34d`**, commit `1390051` |
| stamp grammar with the attach slot: `split_stamp_attach`, `stamp_allowed` on `arms.<arm>.attach`, `check_arm_file_attach`, `normalise_config` refusing an unknown rule; `--probe-max-glossaries` (`glossary_boost_probe.probe_request`, `MAX_GLOSSARIES_CAP` 100); `analyse` dispatch on `analysis.round` | `python/scripts/eval/glossbench/found10.py`, `python/scripts/eval/glossary_boost_probe.py`; tests `python/scripts/eval/glossbench/test_found10_r3.py` | #874, `1390051` |
| round-3 family: `R3_HYPOTHESES`, `R3_SECONDARY`, `R3_DEFAULT_ANALYSIS`, `run_hypotheses_r3`, `holm_family_r3` (sign, two-sided floors), `power_table_r3`, `analyse_r3` (promoted members, `m_num_split`); round 2's `analyse` untouched | `python/scripts/eval/glossbench/found10_analysis.py`; example `glossbench/examples/found10-config-r3.example.json` | #874, `1390051` |
| round-3 pool: populations V / M_num, Σ(paper), eligibility, exclusions from several files, seeded sample, per-stratum quotas, gold-differs gate, `arm_pair_problems`, densities by rank, fields mode on V / M_num | `python/scripts/eval/glossbench_r3_pool.py`; `glossbench_r2_fields.strata_order`; tests `python/tests/eval/test_glossbench_r3_pool.py` | #874, `1390051` |
| drafter on the round-3 pool: `R3_POOL_SCHEMA`, M_num prompts (`DRAFT_SYSTEM_MNUM`, `DRAFT_SYSTEM_FIELDS_MNUM`, `_MNUM_TASK`), `SYMBOL_BOUND_KEY` parsed as a boolean, layer 1 on `R3_STRATA`, the summary's `round3.symbol_bound_by_stratum` | `python/scripts/eval/glossbench_r2_draft.py`, `glossbench_r2_checks.py`; tests `python/tests/eval/test_glossbench_r3_draft.py` | #874, `bc9be96` |
| six guards the review's mutation probe found untested (attach slot compared; round-2 arm refuses the slot; file rule vs declared; mark vs header; unknown rule; probe bounds) | the three test files above | #874, `cc955ea` |

**Done since v1, no new code on `main`** (`README.md` precondition 3, **met** at `898e34d`): the offline
fields-mode P dry build and the offline boost-probe candidate counts are runs of the committed code on
the private inputs, clean tree, `citable true`; their readings are in §3.1 step 5 and §5 note 2 and their
private files are named there by sha256.

**On `main` at `2254e4b` (2026-10-30), unit-tested and reviewed** (`README.md` precondition 4, **met**):

| item | module | PR, merge commit |
|---|---|---|
| read-only BM25 exhaustion verifier of §5 note 3: `ineligible`, `server_filters`, `count_query` (two scopes), `verdict` with the four verdicts `exhausted_verified` / `clamp` / `under_served` / `query_mismatch`, `record` (no query text), `refuse_credentials`; `found10._verify_exhaustion` and `_exhaustion_boost`, `corpora.<c>.bm25_scope` validated against `es_id_prefix` in `normalise_config`, `census.es_url`; `collect()`'s `exhausted_verified` / `exhaustion_verdicts`; the same verifier in `reranker_test.py` (`--es-url`, `--es-index`, `--bm25-tenant`, `--bm25-extra-tenant`; `status query_mismatch`) | `python/scripts/eval/bm25_exhaustion.py`, `python/scripts/eval/glossbench/found10.py`, `python/scripts/eval/reranker_test.py`; tests `glossbench/test_found10_exhaustion.py`, `test_reranker_test.py` | **#877** (`exp/found10-exhaustion`), merge **`2254e4b`**, commits `3dc7fdb`, `83b8d90` (scope must see the index's tenant; three guards pinned), `0ec8f1c` (`query_mismatch` hard stop), `40c16c3` |

Between `898e34d` and `2254e4b`, `main` also took **#876** (`734cfa6`, the reranker test's record; no code
under `python/`). Every module, flag, key and reason code named in this text was re-read against the tree at
`2254e4b` before v3 was written; the dry-run manifests of §3.1 step 5 and the probe counts of §5 note 2
were made at `898e34d` and are not re-run, because nothing in #876 or #877 reads a pool, a stratum, a reach
count or a rendered field (`glossbench_r3_pool.py`, `_glossbench_r2.py`, `glossbench_r2_fields.py` and
`glossary_boost_probe.py` are byte-identical at the two commits).

**Pin.** Every run from one `main` commit **at or after `2254e4b`** in a clean worktree (`dirty false,
untracked 0`) or from a tools image built from a tools tag at or after it (ADR-0010 order). Every output
embeds `experiment_provenance()`; glossary files carry `producer` and `tool`; `citable` is read from the
record (`_glossbench_r2.citable`).

**Record.** `docs/plans/results/glossbench-round3-2026-11/`: this prereg and its `README.md` (the freeze
record), `PLAN-round3.md` (lab-book tier), `R3-COUNTS.md` with `count_attachment.py`, `counts.json`,
`sampling_power.py`, `sampling_power.json`, `stratified_design.json` (planning aids), and after the run
`RESULTS.md` with the status of each set in every table caption, `artifacts/*.json` counts-only with
`artifact_provenance`, `DEVIATIONS.md`.

## 10. Privacy

Round 2 §10 unchanged: the papers-OC owner's R1 set and any P set it accepts are held privately and never
enter this repository; our sets hold article text and are private (`refuse_in_repo`); public artifacts
hold counts, rates, CIs, model ids, version strings and sha256 hashes only (`_glossbench_r2.string_values`);
the record names no tenant, host, endpoint, product or gateway owner; corpus aliases are round 1's; the
bench collection ids of §3 are the one identifier added, at the papers-OC owner's request. Per-paper
densities are published by paper rank only. Drafter prompts carry chunk text to the gateway, within the
papers-OC owner's standing approval; the verifier runs off-gateway.

## 11. Decisions taken (owner, 2026-10-30)

The owner accepted every recommended default of `DECISIONS-round3.md` (2026-10-29) as proposed. Fixed at
freezing; a later change is a deviation.

| # | decision | taken |
|---|---|---|
| D1 | the manipulation | **attachment rule as the arm**, `value` vs `mention`, glossary fixed at the round-2 pure Qwen3.6 files; **no glossary generation** |
| D2 | confirmatory population and strata | arm-blind **V** and **M_num**, 6 + 6 per paper on O |
| D3 | set O size and gate | **50 papers**; gate **≥ 100 gold-differs sampled chunks, ≥ 40 per stratum**, read before any draft (dry run 151: 72 / 79) |
| D4 | set A | **kept as a descriptive leg**, 25 of 28 eligible, 2 V + 6 M_num, floor beside every number |
| D5 | set P | 25 papers × (6 + 6), fields mode, binding v2 (`1c377891…`), the papers-OC owner's sampled acceptance; pilot 100 / 5 first; promotion rule of §3.5 with its Wilson-from-raw-counts rule; **volume approved by the papers-OC owner's user, 2026-10-30** |
| D6 | document-level attachment | **not in round 3** (48–85 % of chunks would carry a field; no code) |
| D7 | baselines and controls | `none`, `mention-shuf`, and the 2 × 2 with `rules-value` / `rules-mention`, all secondary |
| D8 | H2's form | **two-sided** on V; a gain confined to M_num is the hypothesis; no change in V is a legitimate outcome; distractor harm in V would be a finding |
| D9 | precision | **not re-measured** |
| D10 | gateway budget | our pause at **2.5 M in / 0.5 M out / 2,500 calls**; plan ≈ 1.45 M / 0.36 M / 1,450; Gemini 2.5 Pro drafter, `max_tokens` 4096, `fallback_model ""`, ≤ 4 in flight, size notice per batch; Claude only via the CLI verifier |
| D11 | frontier / gateway glossary arms | **none**; precondition for any future one: a 5-call probe recording `usage.completion_tokens_details.reasoning_tokens`, `max_tokens` ≥ 4 × the measured reasoning share or a pre-measured share |
| D12 | paper-density criterion | V ≥ 2, M_num ≥ 2, V + M_num ≥ 10, glossary non-empty under mention; densities by rank in the manifest |
| D13 | collections | the three **bench copies** named in §3 with their chunk counts; never a live collection (the papers-OC owner's point, 2026-10-30) |
| D14 | Holm | **over 3** (6 after §3.5), gated or absent members at p = 1.0, never shrunk |

## 12. Disclosed forking paths, and the gate checked against its sample

**The gate and the sample were checked against each other before the freeze.** Round 2 froze a gate of 150
on a sample that could hold at most 144, 119 and 72 (its `DEVIATIONS.md` D-5), and froze a promotion bar
whose pilot size made the verdict a coin toss (its D-1). Both counts needed no model call. Round 3 ran
them: the gold-differs gate of each set has been read on the sampled set this design draws, with this
design's seeds and quotas, in the mode each set is built in (§3.1 step 5: O 151 against 100 and 72 / 79
against 40 per stratum; A 56 against 40; P 80 against 60 — every margin is stated, the smallest is P's 20),
and the promotion bar has been read against the pilot's size (§3.5: at 100 judged, 8 rejects meet it and 9
do not). The power table (§7) is on the same sampled layout. **What the gate is and is not:** a floor on
*evaluability* — below it no retrieval contrast is read — not a power guarantee; the power a set realises
depends on its discordant records, and `power.realised` and `mde_split` are printed beside every result
and every null. A set that clears its gate with a small margin is reported with that margin.

**Forking paths a reader would name, and the rule that bounds each.** Each is a decision point; each is
closed here, before data, by a fixed rule, and any other choice at it would be a `DEVIATIONS.md` entry.

| # | the fork | bounded by |
|---|---|---|
| F1 | set P's promotion into F after the pilot | §3.5: one addendum, one condition from raw counts, dated before the first non-pilot P draft; fail → replication, no further option |
| F2 | the sample is seeded and the dry run used the final seeds, so the O and A paper samples and their gate readings were known before the freeze | the seed base `261103` was fixed in code (`glossbench_r3_pool.DEFAULTS`, commit `1390051`, 09:10) before the first dry run (`bc9be96`, 09:48); the pool is arm-blind, reads no retrieval result and no record exists; the seed is not changed after the freeze for any reason (a changed seed is a changed sample is a deviation). P's final eligible set differs from the dry run's by the pilot's papers (§3.1 step 2), so its final sample is a new seeded draw from 104; its gate is read fresh and no paper is added after it is read |
| F3 | which condition carries F: `base`, not `bm25` or `norerank` | §2: fixed to `base` (the served pipeline); the other two are secondary and both are always reported, so a `base` null beside a `bm25` gain is visible, not selectable |
| F4 | which stratum carries which hypothesis, and H2's sidedness | §2: H1 on M_num one-sided, H2 on V two-sided, H3 pooled one-sided; the M_num *symbol-bound* / *incidental* split (§3.2) is a secondary table and never redefines the stratum a record was sampled in |
| F5 | the definition of a chunk "with a field" (300 vs 298 on O) | §3.1 step 6: the committed pool's count (300) is the figure, and the two-chunk difference is explained; neither figure enters a test |
| F6 | the glossary boost on `bm25` cells where the probe is structurally `not_testable` | §5 note 2: pre-declared per cell from the offline candidate counts; `declared` is printed, never `pass`; the two A × mention cells run the probe and take its reading |
| F7 | a short `bm25` list | §5 note 3: four verdicts with no run-time choice — verified exhausted is scored, `clamp` and `query_mismatch` stop the run, `under_served` gates |
| F8 | the gold rule on P (ours vs the papers-OC owner's) | §3.3: ours for every P figure we report, the papers-OC owner's for its acceptance; each figure names its rule; no figure is reported under both and the better one chosen |
| F9 | a record whose call failed on one side | §7: excluded and counted, with `family_missing_as_not_found` as the one pre-named sensitivity |
| F10 | the noise-floor repeat (`value` run twice) disagreeing with itself | §5 descriptives: the floor is printed beside every figure; it is not a hypothesis and no result is re-run to move it |
| F11 | Holm's family after a gate or an absent arm | §7, D14: gated or absent members enter at p = 1.0; the family is never shrunk after a result is seen (`holm_drop_gated` is a labelled sensitivity) |
| F12 | the operational choice of attachment rule | §1: made after the round, on the results plus the distractor load, and not by any rule here; this text informs it and does not make it |
| F13 | the design itself was chosen after seeing round 2's counts and the #754 design note | stated in *What had been seen*; no retrieval result under any attachment rule other than the served one existed, and every record here is new |
