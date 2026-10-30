# Pre-registration, glossary-model benchmark round 2 — DRAFT v1, 2026-10-29. Not frozen.

> **Public copy, drafted for freezing.** When the owner gives the go, the text as it stands is frozen:
> its sha256 and a UTC timestamp go into a private `PREREG.sha256` kept with the outputs, and the sha is
> then recorded at the top of this file and in the directory's `README.md` (the part-4 procedure,
> `../../chunk-size-deployed-docs-part4-2026-10-25/prereg/`). Freezing happens **after** every module of
> §9 is on `main` (the harness is, at `e3002ba`; the two code PRs of §9's second table, **#867** and
> **#868**, must be merged first) and **before** any eval paper is sampled, any question is
> generated or any gateway glossary call is made. Any later change is a `DEVIATIONS.md` entry, never an
> edit — with one pre-stated exception, the set-P promotion addendum of §3.5, whose every term is fixed
> here. The file names no tenant, collection, host, endpoint, product or gateway owner; the partner that
> holds the papers-OC collection is "the partner" or "the papers-OC owner".

**v1 (this text):** the v0 draft of 2026-10-29 revised against the merged harness (#861–#865) and the
owner's decisions of the same day (§11). Changes of substance against v0 are listed in `README.md`.
**v1.1 (review of 2026-10-29, same day):** the set-P design of §3.4 (the partner's sampled acceptance
and its binding export), the two manipulation checks of §5 and §8, and the Holm rule of §7 pinned;
no other number or rule changed.
**v1.2 (final pre-freeze edits, owner decisions of 2026-10-29):** D16 closed (S3 on P = 6 per paper);
D17 added (promotion of set P into F by a bounded addendum, §3.5); D18 added (Holm with gated members at
p = 1.0 as the code's default, #867); the binding export re-pointed at its v2 file (§3.4); set-P reason
codes and config keys named as #868 implements them; §9's second table names #867 and #868. No
hypothesis, measure, test or threshold of O and A changed.

**Terms.** *Arm*: a glossary condition (per document; one glossary file serves every chunk size).
*Record*: one question with its gold quote, gold chunk(s), the quantity asked and its binding term(s).
*Paper*: the clustering unit (OA-1000: the article with its float units, `paper_key = "path_root"`;
ASM-1000 and papers-OC: the document, `paper_key = "doc_id"`; `_glossbench_r2.paper_of`). *Pure*: the
LLM half only, no rules fallback (`rederive --merge llm-only`; a document with an empty or absent LLM
half renders ""). *Stamp*: `metadata.symbol_glossary_version` on a chunk,
`<base>[+merge=<mode>]@file/1[+shuf/1:<seed>:<perm12>]` (§4). *SFR*: every token count of the index is in
the SFR-Embedding-Mistral tokenizer; gateway token counts are the gateway's own. *Gold normalisation*:
NFKC, casefold, all whitespace removed (part 4's rule; `_glossbench_r2.gold_norm`).

**What had been seen before writing:** all of round 1 (`../../glossbench-2026-10/LABBOOK.md`, the
partner's review of 2026-10-29 and gold-r3), the partner's calibration of the wording proxy (live
collection: Scout 41 of 63 pairs, Qwen 42, Opus 43; JATS 512/64: rules 23, Scout 25, Qwen 42, Opus 39;
lab book, review point 4) and its shuffled-glossary control for the reranker (content only 53, with the
glossary 65, shuffled 57 of 155; lab book, review point 4 and the addendum of 2026-10-29). Round 2's
design is chosen because of those results. It is a fair test because every record is new, every eval
set is new, and the pure-LLM condition has never been measured.

## 1. Question

Does the model that writes the symbol glossary change (a) what retrieval finds and (b) whether the
entries are right, on records from many papers, with no rules fallback? The two measures are reported
separately and never merged into one ranking (the partner's point 1). The operational model choice is
made **after** this round, on these results plus cost, speed and licence (the partner's point e); the
round informs it and does not make it.

## 2. Hypotheses

Confirmatory family **F** = H1–H4 on **set O ∪ A** (§3), stratum **S1**, condition `base` (hybrid +
rerank, the served pipeline), outcome **found@10** (§5), Holm over 4 at α = 0.05. These are the defaults
of `glossbench/found10_analysis.py` (`DEFAULT_HYPOTHESES`) and of the example config
(`glossbench/examples/found10-config.example.json`: `family_corpora ["O","A"]`, `family_strata ["S1"]`,
`family_condition "base"`). **F may grow once, to seven, by the set-P promotion addendum of §3.5** (H1-P,
H3-P, H4-P; Holm over 7); nothing else can change it. Everything else is secondary or exploratory and is
labelled so in every sentence that quotes it.

| id | hypothesis | type | contrast |
|---|---|---|---|
| H1 | the pure Qwen3.6 glossary finds more records than rules | superiority, one-sided | `qwen36p` − `rules` > 0 |
| H2 | the pure Qwen3.6 glossary is no worse than the pure frontier glossary | non-inferiority | `qwen36p` − `frontierp` > −δ, **δ = 0.05** net share (owner, §11 D5) |
| H3 | the *right* glossary beats *a* glossary | superiority, one-sided | `qwen36p` − `qwen36p-shuf` > 0 |
| H4 | the pure Qwen3.6 glossary's definitions carry the binding words more often than rules' (wording proxy, §5 M2) | superiority, one-sided | `qwen36p` − `rules` on **proxy pass** > 0 |

**Secondary (reported, outside F; `DEFAULT_SECONDARY` plus the per-condition tables):** the same four
under `bm25` and `norerank`; H1–H4 per corpus (O and A separately, with their power floors); `qwen38p`,
`scoutp` and `none` in every table; `frontierp` − `rules`; `frontierp` − `qwen36p` two-sided; the
words-only stratum S3 (the value-only attachment blind spot, the partner's point 3); the set-P and R1
papers-OC replications (§3).

**Exploratory:** anything not listed above, including every precision-vs-retrieval cross-tabulation.

**Precision (gold labelling, §6) is a separate measure with its own report**: harm, strict and lenient
precision per arm with document-clustered CIs. It is not in F. Its one pre-stated contrast is
descriptive: `qwen36p` harm vs `rules` harm, 95% CI.

Not confirmed is reported as *not confirmed*; non-inferiority not shown is *not shown non-inferior*;
"no difference" is never written. A contrast whose minimum attainable p exceeds α is
**GATE-NOT-EVALUABLE**, not a null (`found10_analysis.min_attainable_p`).

## 3. Eval sets and how each is built

Three sets plus a replication. Each carries an explicit **status** that is printed beside every number
from it:

| set | corpus | records (target) | status |
|---|---|---|---|
| **O** | OA-1000 (12,560 article + float units; 32,765 chunks @512 — lab book, corpus table) | ~250 from 25 papers | *our three-layer check* (§3.3), owner-spot-checked |
| **A** | ASM-1000 (1,000 articles; 27,418 chunks @512) | ~250 from 25 papers | *our three-layer check*, owner-spot-checked |
| **P** | papers-OC (275 papers; 14,456 chunks @512) | ~200 S1/S2 drafted-and-accepted from 25 papers plus **≈ 150 S3** (6 per paper, D16), drafted **against the partner's binding** (§3.4) | **scripted-clean, judged sample of 50, error rate X% (95% CI …)** — the partner's sampled acceptance (§3.4, §11 D4). Every P number carries that label and rate; P is **never called equivalent** to the 155-record set |
| R1 | the partner's round-1 155-record set (7 papers, 138 of 155 from one; lab book Part 3) | as is | reviewed-by-holder; **replication only**, one-paper reach stated |

Set P is **outside F** unless the addendum of §3.5 — whose content, condition and deadline are all fixed
here — promotes it **before any non-pilot P record is drafted**. The partner advised starting with O and
A, which need nothing from it.

**Where sets run.** All four run **on our host**, through the committed runner (§5) against bench copies
of the three indices. The partner's user has lifted the "never copy" rule on the 155-record set for this
project's **private** experiments repository (lab book, status addendum of 2026-10-29), so R1 can be held
privately and measured by us; it never enters this public repository. The partner's own read-only
measurement over our swaps (round 1's step 8) remains available as a cross-check and is reported as such
when it happens.

### 3.1 Papers and the arm-blind candidate pool (`glossbench_r2_pool.py build --set <S>`)

1. **Eval papers**, per corpus: a seeded random sample (`<seed_base>|<set>`, seed base `261101`:
   `261101|O`, `261101|A`, `261101|P`) of **25** papers among those with **≥ 3 value-statement chunks**
   (`pool.n_papers`, `pool.min_vs_chunks`). A *value-statement chunk* is a chunk whose canonical text
   holds a token that contains a letter, is not a unit and is not a stoplisted statistical token,
   followed by `[=≈≃~∼]` and a number (`_glossbench_r2.value_statements`: the library's
   `value_pattern` applied to the chunk's own tokens). **Statistical stoplist**, pinned in code as
   `_glossbench_r2.STAT_TOKENS` — `p n N df R2 R² r t F z χ2 χ² chi2 CI SD SE SEM OR HR RR IQR AUC`,
   compared in canonical form, case-sensitive — so `p = 0.03` or `n = 12` does not make a value
   statement; `pool.vs_stoplist` may override it and the pool header and manifest record the list used.
   This step uses **no glossary** and is identical under every arm. Set P excludes the papers of the
   partner's R1 gold (`exclude_papers`).
2. **The frontier glossary runs on the eval papers first** (§4, gateway), so that step 3's union includes
   it. Running it before chunk sampling removes the bias that excluding the frontier's unique reaches
   would put on H2. Step 1 never reads a glossary file, so the frontier batch cannot change it.
3. **Strata**, per value-statement chunk of an eval paper, from the rendered field of each reach arm
   (`Glossary.for_chunk` under the file's own config, which must be the served value-only attachment or
   the file is refused):
   - **S1 — reachable:** ≥ 1 of `pool.reach_arms` {rules, scoutp, qwen38p, qwen36p, frontierp} renders
     a non-empty field on it;
   - **S2 — unreachable:** none does (every arm renders ""; a record here can differ between arms only
     through distractors, the partner's residual (ii));
   - **S3 — words-only:** a chunk with no value statement but a printed number
     (`_glossbench_r2.words_only_candidate`); whether it yields a record about a quantity printed in
     words is the drafter's call (§3.2). Target 2 records per paper.

   **The confirmatory population is S1** (§11 D6): *questions about symbol-bound quantities whose chunk at
   least one candidate glossary reaches*. S2 is sampled thinly (2 per paper) as the distractor/tie
   check. The estimand is stated with the reach rates (share of value-statement chunks reached per arm,
   `--corpus-reach` for the whole corpus), so a reader can scale it to the corpus.
4. **Reach gate** (before any generation call): S1 must hold ≥ **150** chunks per corpus
   (`pool.reach_gate`). Below that the corpus's retrieval leg is **GATE-NOT-EVALUABLE** and only the
   proxy and precision are reported there. Without the `frontierp` file the gate reading is `dry`; with
   it, `final`. Round 1's chunk-level reach on ASM-1000 (3–7 of 27,418 under the mixture rule; lab book
   Part 1) makes this gate real; the pure rule and the frontier change the counts, and the gate decides.
5. **Chunk sample:** per paper and stratum a seeded order (`<seed_base>|<set>|chunks|<stratum>|<paper>`);
   the first 12 S1 (all if fewer), 2 S2, 2 S3 are sampled (`s1_per_paper`, `s2_per_paper`,
   `s3_per_paper`). **On P the S3 quota is 6 per paper** (D16, 2026-10-29), set by the per-set override
   `pool.per_set = {"P": {"s3_per_paper": 6}}` (#868; the effective quotas are recorded in the pool
   header and manifest as `quotas_per_paper`). O and A keep 2.

**Manipulation checks before anything is written:** the export's chunk count equals the configured
`expected_chunks` (32,765 / 27,418 / 14,456, round 1's counts); every arm file resolves every eval-paper
document (`verified` or `verified_ws`, no missing, mismatched or unverifiable row). Outputs:
`pool-<set>.jsonl` (private: ids, offsets, `content_sha256`, strata, reach booleans, sample order; no
text) and `pool-manifest-<set>.json` (counts only, with `experiment_provenance()`). The chunk export
itself is `glossbench/export_chunks.py` (#865): read-only PIT scroll of one bench index, `_source`
limited to `chunk_id, doc_id, content, start_char, end_char, metadata.source_path, metadata.title` —
**`symbol_glossary` is never requested**, so the drafter cannot see a glossary; the exported count must
equal `_count` or nothing is published.

### 3.2 Drafting (`glossbench_r2_draft.py preflight | draft | check | verify | summary`)

- **Drafter:** one gateway model from a family that is **not a glossary arm** and **not the verifier**:
  **Gemini 2.5 Pro** (§11 D3; `DEFAULTS["drafter"]`). Temperature 0 unless the model refuses it,
  **`max_tokens` 4096** (the model's hidden thinking can spend ~1,024 tokens before any visible output,
  and a cut answer is dropped, not retried), `fallback_model ""` and `max_tokens` asserted in every body
  (`R2Gateway`), no Authorization header, ≤ 4 in flight (`_glossbench_r2.MAX_INFLIGHT`; a config asking
  for more is refused), the served model id recorded per call.
- **Blindness:** the drafter sees the chunk's **content only** — never any `symbol_glossary`, never an
  arm name, never the word "glossary" (`glossbench_r2_checks.draft_messages`; pinned by
  `tests/eval/test_glossbench_r2_draft.py`). It is asked for a question **in words** about the quantity
  the chunk states; the **exact quote** (≤ 300 chars, inside the chunk); the **quantity asked** in words;
  the **value**; the **binding term(s)** (1–3 short noun phrases, 1–4 words each, naming the quantity as
  the paper words it); and the **symbol** the chunk uses (so validation can check the question avoids
  it). For S3 it is asked for a quantity printed in words with no symbol.
- **Why the drafter cannot favour an arm:** it never sees a glossary; every contrast is paired within a
  record; its family is none of the arms'. It can only shift which chunks yield a valid record, which
  is reported (yield per stratum and corpus).
- **Empty / cut answers:** an empty answer is retried once after 2 s (`empty_retry_s`); still empty, the
  attempt is dropped and counted `empty_twice` and counts as a gateway failure; `finish_reason length`
  is dropped and counted (`truncated` / `cut`). Neither is redrawn.
- **One redraw after a layer-1 reject:** a draft that fails layer 1 for any reason other than
  `no_quantity` is redrawn **once** (`max_attempts_per_chunk` 2) with its reject codes in the prompt.
  **Every attempt is logged**: dropped attempts as `draft` rows and each attempt's layer-1 verdict in
  `acceptance-<set>.jsonl`; the summary reports `draft_attempts`, `attempts_by_number`,
  `attempts_per_accepted` (and per stratum). Record ids carry the attempt:
  `<set>-<sha256(chunk_id)[:12]>-a<attempt>`.
- **Yield assumption:** ~1.3 attempts per accepted record (`attempts_factor`; part 4 saw 372 valid of 429
  attempts, `../../chunk-size-deployed-docs-part4-2026-10-25/RESULTS.md`), plus the verifier's rejects
  here. Realised yields are reported.

### 3.3 Acceptance: the three-layer check (does not depend on the partner)

Every drafted record passes all three layers or is rejected with a reason code. Reject counts per code,
stratum and corpus are reported (`summary`).

**Layer 1 — mechanical** (`glossbench_r2_checks.layer1`; code, no model). Reason codes, as implemented:

| code | rule |
|---|---|
| `unparseable` | the answer is not one JSON object with the six fields |
| `no_quantity` | the drafter said the passage states no such quantity (no redraw) |
| `question_missing`, `quote_too_long` | empty question; quote over 300 chars |
| `quote_not_in_paper`, `quote_not_unique`, `cut_by_boundary`, `quote_not_in_source` | the quote (gold normalisation) is absent from the paper; occurs more than once; occurs once but in no single chunk of the 512 index (the partner's failure class); is in the paper but not in the chunk it was drafted from |
| `quantity_not_in_quote` | the value is empty, has no number, or is not in the quote |
| `number_in_question` | the question prints the value's number |
| `symbol_missing`, `symbol_not_in_chunk`, `symbol_in_question` | S1/S2: no symbol; symbol not in the source chunk; the question contains the symbol (canonical comparison) |
| `s3_has_symbol`, `s3_value_statement` | S3: a symbol was given; the quote holds a symbol value statement |
| `binding_terms_invalid`, `binding_word_not_in_paper` | not 1–3 terms of 1–4 words; a binding word the paper does not contain |
| `lexical_leak` | the question shares > **0.5** of the quote's distinctive tokens (`leak_threshold`; tokens `[a-z0-9]+` after NFKC/casefold, `_glossbench_r2.STOP_WORDS` removed, "distinctive" = not among the paper's 200 most frequent, `top_frequent`) |
| `duplicate_question` | cross-record: same question under the gold normalisation in the same paper; the earlier record is kept |

**Gold chunk(s)** = exactly the chunk ids whose content contains the quote (`gold_chunk_ids`); **gold
width = quote-bearing chunks only**, never "any chunk of the paper" (the partner's precedent: an id-based
gold moved a headline 35 → 44 → 62; the rule is fixed here).

**Flags (counted, never a reject):** `symbol_collision` — the paper's own definition sentences give the
symbol two unrelated definitions (content-word Jaccard < 0.5). It is a **flag to the verifier, not a
reject** (`collision: "flag"`, §11): the check reads the rules extractor's definition sentences, so a
reject would make the record population depend on the `rules` arm; the verifier is asked
`symbol_unique` with a note instead. Also `multi_value` (the symbol states two or more different values
in the paper), `scenario_terms` (the quote names a case/scenario/condition word the question does not)
and, cross-record, `shared_chunk` (two accepted records share a gold chunk).

**Layer 2 — verifier** (`verify`): a model from a **third family**, not the drafter and not an arm —
**Claude Opus 5.5 via the CLI, off-gateway** (§11 D3): `claude -p --model claude-opus-5-5
--output-format json --restricted --no-session-persistence --strict-mcp-config --tools ""
--system-prompt …`, prompt on stdin, cwd an empty 0700 directory; the first call of a run must pass the
context-size isolation check (round 1's judge rule). It sees the question, quote, value, the source
chunk and the first 2,000 characters of the paper (`head_chars`) with its title, and answers a fixed JSON
of `supports`, `single_reading`, `same_case`, `symbol_unique`; any `false` rejects. Empty or unparseable
answers are retried once, then the record is rejected (`l2_empty` / `l2_unparseable`). A usage-limit
signal pauses.

**Layer 3 — owner spot-check** (`spot-export` / `spot-import`): a seeded random sample of **40** accepted
records per set (`261101|spot|<set>`), shown blind to stratum and arm; the owner marks valid / invalid.
Reported: validity with a Wilson 95% CI. **Rule:** validity below **0.85** at the point estimate makes
every result from that set carry *validity below threshold* in the sentence; the set is not rebuilt
(that would be a forking path).

**Layer 4, set P only:** the partner's sampled acceptance, §3.4.

### 3.4 Set P: the partner's binding, its sampled acceptance, and the pilot (§11 D4, D14–D16)

Set P differs from O and A in three fixed ways. O and A keep the open value-statement design of §3.1–3.3
unchanged. The code is #868 (`glossbench_r2_fields.py`, `draft_mode: fields` in the pool, the field-mode
prompt and layer-1 codes in `glossbench_r2_checks.py`, the `export-partner` / `import-partner-verdicts`
subcommands of `glossbench_r2_draft.py`); the identifiers below are its.

**(a) The drafter is restricted to the partner's binding quantities.** The field list is the partner's
binding export **v2**, `binding_fields_261029_v2.json` (sha256
`1c3778918c76ae206de0fb1b66e256da33faebf8840d6c49ca232552b2911dfa`, 9,445 bytes, **26** numeric fields;
a named input held privately, its sha256 re-recorded by the drafter on load and on resume and a mismatch
refused). **v1 of the same export (sha256 `08309d0f…`) is superseded and kept only for provenance:** its
term lists were truncated for two fields (`air_exchange_rate_s` 12 → 14 terms in v2, adding a
subscripted-lambda form and `ach`; `deactivation_rate_s` 12 → 13), so under v1 a question written with
"ACH" would have passed the term-leak guard for the most ACH-heavy field. v2 has the same 26 fields, units
and definitions, and `n_terms == len(terms)` for every field; **the loader asserts that equality on
import** (a mismatch refuses the file — required of #868 before the freeze; the loader at its head
`d52efef` reads `terms` and ignores `n_terms`). It is **not** the field list of the held 155-record set.
Each row carries the field name, its canonical unit, the binding's one-line definition and its extraction
terms (`terms`, alias `extraction_terms`). Rules, frozen now:

- A record's `field` must be one of the 26. Drafting jobs are **(chunk, field) pairs planned from the
  file**, so no other field can be asked; a job or draft row naming a field outside the file stops the
  run (exit 1), and `export-partner` refuses and counts such a record (`field_not_in_list`). Fields of
  `kind: condition` are excluded from the plan. That is what excludes model conditions (not numbers) and
  the binding's non-researchable fields; the prereg names no field as a target beyond the quota list
  below.
- The record's `binding_terms` are the field's **extraction terms** from the file, not the drafter's.
- The question is written in the **definition's own words**. A layer-1 guard rejects a question whose
  overlap with the field's extraction terms exceeds the leak threshold (**0.5**, `leak_threshold`, the
  same stemming as the wording proxy, §5 M2) — copying the terms would flatter any glossary that uses
  them (`term_leak`).
- `value` is recorded **as printed, with the printed unit**, never converted. Unit compatibility with
  the canonical unit is checked **by dimension** against a small fixed map (`unit_incompatible`, a
  reject); a `ventilation_flow_m3_s` quote printed in air changes per hour is recorded as printed and
  **flagged** (`bridge`), not rejected; a unit outside the map is flagged (`unknown` / `field_unknown`),
  not rejected.
- The file carries no in-set or constant flag. `in_set` is computed from the held set's records
  (`--in-set-records`, the records' sha256 recorded) and **asserted 13 covered / 13 uncovered**
  (`expect_in_set {"true": 13, "false": 13}`; a different split stops the run). The **4 constants** —
  `gravity_m_s2` (covered), `air_density_kg_m3`, `air_dynamic_viscosity_pa_s`, `particle_density_kg_m3`
  (uncovered) — come from the explicit config list `constants` and are **capped at 2 records each**
  (`constant_cap`).
- **Quotas favour the 10 new-ground fields 2 : 1** (`weight_new_ground` 2.0 vs `weight_other` 1.0;
  the stated and the final quota plan are both recorded) over the covered ones: `clearance_fraction`,
  `exposure_time_s`, `filter_efficiency`, `initial_concentration_quanta_m3`, `neighbor_distance_m`,
  `occupant_count`, `recirculation_flow_m3_s`, `risk_tolerance`, `susceptible_count`,
  `ventilation_flow_m3_s`. Field names are generic model quantities and may appear in public files;
  no partner-identifying text does. Quotas are not weighted by the held set's field frequencies.
- The pool (§3.1) stays arm-blind and unchanged; the restriction acts on the drafter's prompt and on
  layer 1 only.

**(b) Acceptance: the partner's scripted checks on every record plus a judged random sample of 50.**
The partner's scripted checks are: the quote is verbatim under NFKC with case and whitespace folded,
with a first-or-last-60% rule for a quote cut by a boundary; the quote prints a number with a unit; the
field maps to a quantity in the binding; the source resolves; no duplicate source + quote + field. Then
a random sample of **50** accepted records, seed recorded, is judged. What comes back: accept or reject
with a reason for every scripted reject and every sampled record; for everything else, the sample's
error rate with a 95% CI. P's status label is therefore **"scripted-clean, judged sample of 50, error
rate X% (95% CI …)"**, printed beside every P number. Our own layers 1–3 run first and unchanged; a
record the partner rejects is excluded and counted by reason. The partner's 60% rule is its acceptance
rule only: our gold stays *quote-bearing chunks only* (§3.3), so a `cut_by_boundary` record never
reaches the partner. Records go to the partner in its format through a committed adapter from
`accepted-P.jsonl`: `id`, `question`, `source {doc_id, locator}` (locator a PMC id or DOI), `quote`,
`field`, `case`, `model`, `prompt_sha256`.

**(c) A pilot batch first.** About **50** records from **3** papers (`--pilot 50 --papers 3`, papers
seeded `<seed>|pilot`) are drafted, checked and sent through the adapter before full set-P drafting, to
prove the format end to end. The pilot runs **after freezing**, is part of the plan (`PLAN-round2.md`),
and is **data for no hypothesis**: it writes its own `*-P-pilot*` files, its records carry
`pilot: true`, `export-partner` refuses an export that mixes pilot and hypothesis records, and its papers
are excluded from P's paper sample (`exclude_papers`). **The pilot runs no retrieval arm**: nothing about
any glossary condition is measured on it. Its partner verdicts (§3.4 b, through
`import-partner-verdicts`) are the one input to the promotion rule of §3.5.

**S3 on P carries the key test, and it is sized for it.** The partner's own analysis (external, not
re-measured here) found that the symbol glossary names the asked quantity for 65–68% of its pairs and
that every failure was a quantity printed **without a symbol** — the words-only case. On P the words-only
stratum S3 is therefore the stratum of interest, and its planned size is **≈ 150 records** (D16,
2026-10-29: `s3_per_paper` **6** on P × 25 papers, before rejects, via `pool.per_set`, §3.1 step 5; the
v1.1 plan of 2 per paper ≈ 50 was closed by the owner). It remains a **descriptive, exploratory**
stratum, outside F before and after any §3.5 promotion: at discordance .17 about 25 discordant records
are expected, and the one-sided floor at α .05 is then 18 of 25 of one sign (exact p = .022; 17 of 25
gives .054); the planning table's rows do not apply to it, and the realised `mde_split` is quoted beside
every S3-on-P figure.

**Code this section needs** (not on `main` at `e3002ba`; §9, second table): **#868** — the field-mode
drafter, `term_leak`, `unit_incompatible` and the `bridge` flag, `expect_in_set`, `constant_cap`, the
2 : 1 quota plan, `pool.per_set`, the pilot and the two partner subcommands. It must be merged before
the freeze (D13).

### 3.5 Promotion of set P into F: one bounded addendum (§11 D17)

This section adds **one decision point after the pilot's quality is seen**. It is therefore a **disclosed
forking path**, bounded by the rule below, which leaves the addendum **no free choice**: not which
hypotheses, not the family size, not the threshold, not the deadline. The pilot shows the format and the
partner's acceptance quality only; it runs no retrieval arm (§3.4 c), so the fork is not conditioned on
any outcome of any arm.

- **When.** After the pilot batch has been drafted, checked and sent through the adapter, and the
  partner's verdicts on it have been imported (`import-partner-verdicts`), and **before any non-pilot
  set-P record is drafted**. An addendum dated after the first non-pilot P draft call is void.
- **Condition (the owner-accepted default).** The partner's judged error rate over the pilot records it
  judged has a **Wilson 95% upper bound ≤ 0.15** (the same 0.15 the design already uses for layer 3:
  owner spot-check validity below 0.85 marks a set *validity below threshold*, §3.3; a P set whose
  partner-judged quality cannot be shown better than that bar is not fit to carry a confirmatory
  hypothesis). At n = 50 judged pilot records the rule admits at most **2** rejects (upper bound .135;
  3 of 50 gives .162); a smaller judged n is harsher. A pilot the partner does not judge, or judges with
  fewer than **30** records, fails the condition (no promotion).
- **What is added, exactly.** Three hypotheses mirroring H1, H3 and H4 on **set P**, stratum S1,
  condition `base`, outcome found@10 (H4-P: proxy pass), same contrasts, same one-sided superiority
  form: **H1-P** `qwen36p` − `rules` > 0; **H3-P** `qwen36p` − `qwen36p-shuf` > 0; **H4-P** `qwen36p`
  − `rules` on proxy pass > 0. **H2 is not mirrored**: a non-inferiority test at δ .05 on one 25-paper set
  is under-powered by the planning table (§7, `H1_one_corpus_P25` is .47 at share .70 for a superiority
  test, and NI is weaker still), so `qwen36p` − `frontierp` on P stays secondary.
- **How Holm changes.** F becomes **{H1, H2, H3, H4, H1-P, H3-P, H4-P}**, Holm over **7** at α 0.05,
  from the addendum's date and for the rest of the round; a gated or absent member (P's S1 reach gate
  < 150, a failed probe, a short-served depth) enters at **p = 1.0** (§7). The P members use the same
  `contrast_test` call with `family_corpora ["P"]`, papers as the exchangeable units, and the seven p
  values go through `chunktrial_stats.holm` in a committed script, never by hand. The O ∪ A members and
  their α are unchanged except through the larger family.
- **If the condition fails,** set P stays a replication with its status label (§3), **with no further
  option**: no second pilot for promotion purposes, no later addendum, no partial promotion.
- **Form.** The addendum is one dated section appended to this file's `README.md` (the body of this
  prereg is not edited), quoting the imported verdict summary (n, rejects, seed, Wilson bound) and the
  one word *promoted* or *not promoted*; its sha256 joins the private `PREREG.sha256`.

## 4. Arms

| arm | what | source of entries | new model calls |
|---|---|---|---|
| `none` | empty field everywhere | `rederive --merge none` | none |
| `rules` | `rules/3` | round-1 files | none |
| `scoutp` | Llama-4-Scout, **pure** | round-1 files, `rederive --merge llm-only` | none |
| `qwen38p` | Qwen3.8-27B, **pure** | same | none |
| `qwen36p` | Qwen3.6-35B-A3B, **pure** | same | none |
| `frontierp` | **GPT-5.5**, **pure**, same prompt and step settings as round 1 | **new**, eval papers only (§11 D1–D2) | gateway |
| `qwen36p-shuf` | `qwen36p`'s rendered fields permuted across the chunks that carry one | the backfill's `--shuffle-rendered 261101\|shuf` | none |

- **Pure conditions cost nothing** for the three local models: `python/scripts/glossary_file.py rederive
  --merge llm-only` (`ragstack.ingestion.glossary_file.rederive_file`) re-decides `merged` from the two
  halves a round-1 row already holds, keeps both halves, and marks the version `<base>+merge=llm-only`
  before the `@file/1` stamp. A document with no LLM half at all stamps `none+merge=llm-only@file/1`.
  `llm-only` on a file with no LLM half in any row is refused. Its census (`rows_with_entries`,
  `rows_empty`, `llm_absent`, `llm_empty`, `degraded`) is recorded.
- **Stamp grammar** (`glossbench/found10.py`, `stamp_allowed`): `<base>[+merge=<mode>]@file/1[+shuf/1:
  <seed>:<perm12>]`. A pure arm's file loaded unmarked, a merged file passed off as pure, or a true index
  passed off as the shuffled one is refused on the stamp itself; the pinned contract is
  `glossbench/test_integration_contract.py`.
- **OA reach under pure, and the estimand it implies.** Round 1 ran the LLM on OA-1000 with
  `--llm-min-chars 1500` (lab book, run settings), so the ≈ 10,965 of 12,560 OA units that are float
  units below that length carry no LLM half. Under `llm-only` they render "". A dry run of `rederive` on
  the OA Qwen3.6 file left **777** units with entries and **11,783** empty (private dry run of
  2026-10-29, not a result; the census is re-recorded by the committed command when the arm files are
  built). **Consequence:** on OA-1000 every LLM arm's reach is confined to article bodies; a float unit
  can be found under an LLM arm only through its own content, while `rules` can still attach there. The
  O estimand is therefore *article-unit-dominated*, its per-arm reach (chunks with a field; records whose
  gold chunk has a field) is printed beside every O figure, and no O figure is read as a corpus-wide
  statement about float units.
- **Frontier settings** are round 1's, recorded in the version string: thinking unset, repair 0, cut-off
  retry `split`, max_tokens 4096, windows 80,000 × ≤ 4, timeout 300 s, seed 20261028,
  `--llm-extra-body '{"fallback_model": ""}'`, `--llm-concurrency 4`, no Authorization header. The step's
  `/v1/models` probe must list the exact model id or every document degrades `model_not_served`. An empty
  non-cut answer degrades the document (`bad_response`, no in-step retry); one rerun with `--reuse` is the
  "retry once"; a document still degraded is **empty under pure** and counted.
- **Why GPT-5.5 and not Claude via the gateway** (§11 D1): Opus 5.5 through the gateway answered empty on
  **14 of 60** long-output generation calls in the OA chunking pilot (private ledger,
  `ragstack-experiments` `oa-chunking-eval-2026-10/ledgers/ledger-opus55.jsonl`; 26 of 42 in its
  `diag.jsonl` — a private record, not citable as a result), against the short grading calls of part-4
  addendum Q: **1 of 1,522** calls failed as empty at the transport level
  (`../../chunk-size-deployed-docs-part4-2026-10-25/artifacts/addq_summary.json`,
  `gateway_failures:empty`), while 15 of 744 pairs returned an empty reply that parsed to no label
  (`../../chunk-size-deployed-docs-part4-2026-10-25/DEVIATIONS.md`, note of 2026-10-27). Long generation
  is the at-risk case, and a glossary document is long generation. The partner's own proxy showed little
  headroom for Opus over Qwen (43 vs 42 of 63), so a different family is also the more informative
  choice.
- **Frontier scope:** eval papers only (§11 D2). On the eval papers' *distractors* `frontierp` is
  therefore `qwen36p` — a stated **mixture** (the example config's `frontierp.stamp_prefixes` lists both
  model ids), so corpus-wide claims about the frontier are not made; the retrieval contrasts compare the
  models' own glossaries on every gold paper, as round 1's table did.
- **Shuffle rule** (the partner's control): permute the rendered `symbol_glossary` strings among the
  chunks of one index that carry a non-empty one, seed **`261101|shuf`**
  (`ragstack.ingestion.glossary_shuffle.shuffle_rendered`: sorted keys, Fisher–Yates over a SHA-256
  counter stream, fixed points repaired by seeded swaps and the residual **reported** as `fixed_points`,
  never dropped). Field population, the multiset of strings and the length distribution are unchanged
  (`multiset_sha256` equal to the true arm's). **Keying differs between tools and is stated with every
  shuffled figure:** the backfill (`backfill_symbol_glossary.py --glossary-file F --shuffle-rendered
  SEED`, #861) keys chunks by **ES `_id`**; the wording proxy (`wording_proxy.py --shuffle-rendered
  NAME=SEED`) and the in-memory `part4-glossary` harness (`--shuffle-rendered NAME=SEED`) key by
  **`chunk_id`**. The keys sort in the same order, so the same seed yields the same assignment of strings
  to chunks, but `permutation_sha256` hashes the (target key, source key) pairs and therefore differs
  between the two tools. The index's shuffle record (seed, `permutation_sha256`, `fixed_points`,
  `multiset_sha256`, `stamp_suffix`) is what the runner verifies; the proxy's shuffled figure is the
  chance level and carries its own shuffle record.
- **Wording proxy vs the partner's reference** (behavioural differential found in the review of #861,
  stated with every proxy figure): identical tokenisation, stemming and normalisation. Ours scores every
  glossary line; theirs keeps the **first definition per normalised symbol** — no repeated symbol
  occurred in 2,234 real rendered fields (a count from the #861 review, not a committed artifact), so no
  practical effect is expected. Their caller **drops
  binding terms of ≤ 3 characters and adds a field-name term**; ours scores `binding_terms` as the
  drafter gave them. The proxy was reimplemented from the written spec; no partner code was consulted.

## 5. Measures

**M1 — found@10** (`glossbench/found10.py run --private-config CFG --questions accepted-<set>.jsonl
--corpus <S> --arm <arm> --swap-record SWAP.json --out DIR [--repeat 2]`). One arm, one corpus, through
the served API (`POST /v1/retrieve`), under three conditions (the example config):

| condition | request | pipeline |
|---|---|---|
| `base` | `retrieval_mode hybrid, rerank true, rerank_candidates 50, top_k 50` | BM25 2×50 + SFR dense 2×50 → RRF k 60 → cut 50 → bge-reranker-v2-m3 over `content + "\n" + symbol_glossary` → served 50 |
| `bm25` | `retrieval_mode bm25, rerank false, top_k 50` | BM25 with the glossary field at the bench server's boost |
| `norerank` | `retrieval_mode hybrid, rerank false, top_k 50` | the fused list, unreranked |

Every request also carries `use_graph false`, `rewrite_strategies ["passthrough"]`, `context_window 0`
(`FIXED_REQUEST`). A record is *found* under an arm if any gold chunk is at rank ≤ 10 of the served list;
ranks to the served depth are recorded per record. Chunk ids and text are identical across arms; only
the glossary field differs. **The glossary field boost** is the bench server's `TEXT_SYMBOL_GLOSSARY_BOOST`
(1 on the bench copies in round 1's step 8, lab book). Its shipped default is **0.0**
(`python/ragstack/config.py`) and `/v1/config` does not expose it, so a bench server launched without
the setting would silently run the `bm25` condition with the field unweighted. The runner therefore
**proves the boost is active behaviourally** before the first call of every arm × corpus run (the probe
of `glossary_boost_probe.py`, shared with the reranker test, **#867**, §9 second table): up to 5 chunks
whose rendered glossary field holds a whole-word ASCII token (≥ 5 characters) that occurs in no chunk's
content and in at most 10 glossaries are queried `bm25`, `rerank false`, `top_k 10` with that token
alone; each must return its chunk. Candidates come from the chunk export (`--chunk-export`) plus the
arm's glossary file rendered offline as the backfill renders it (sha checked against the arm; for the
shuffled arm the permutation is reproduced and hash-checked against the swap record), or from
`--rendered-fields`. Token sha256s (never the text), ranks, requested vs served depth and the verdict
are recorded in the run summary. **Verdicts `inactive`, `mixed` and `error` are a hard stop (exit 1,
configuration error), not a gate**; nothing from that server process is a result. `not_testable` (fewer
than 3 qualifying chunks) gates the boost-dependent conditions unless `--boost-declared X` is given, in
which case the reading is recorded as `declared`. For `none` (every field empty) the probe is
`not applicable` and recorded so; the boost reading for that run is the probes of the arms run
immediately before and after it on the same server process (pid recorded). The configured value is
also copied into the private config and recorded.

Controls built into the runner, all refusals before the first call or hard stops during it:
- **Which arm is attached:** the swap record written by `glossbench/swap.sh` (dry run → apply →
  census, bench index names only, `--confirm-index` always) must name this arm and index, show a
  post-apply census whose total equals `expected_chunks` and whose every stamp is allowed for the arm
  (`stamp_allowed`), a glossary-file sha256 equal to the arm's, the arm's shuffle seed (`null` for a true
  arm) matching the backfill's own `shuffle` record, and an apply whose `chunks_written` equals
  `chunks_to_update`. With `census.es_url` the runner re-reads the census itself before the first call
  and after the last; every `bm25` hit must carry an allowed stamp (`hit_stamp_conditions`).
- **Depth read-back:** a served depth below the requested 50 marks that arm/condition
  GATE-NOT-EVALUABLE; a `degraded` response (reranker fallback) is retried, then a failed call.
- **Two server settings are assumed, not read back:** the per-leg multiplier (2) and RRF k (60) are
  absent from `/v1/config`; the operator pins them from the server environment in the private config
  (`server_assumed`) and every summary repeats them.
- **A wired-but-inactive reranker** answers `rerank: true` with the RRF order: `base` is
  **GATE-NOT-EVALUABLE** when its ranked list equals `norerank`'s on every record.
- Failures: retries with backoff; a 4xx stops (configuration error); > 2% failed calls is a hard stop
  (`MAX_FAILED_SHARE`); `PAUSE` file / SIGTERM → exit 75, resumable; ≤ 4 in flight.

**M2 — wording proxy** (`python/scripts/eval/wording_proxy.py --records accepted-<set>.jsonl
--glossary-file NAME=PATH --chunks CHUNKS.jsonl [--fields NAME=PATH] [--shuffle-rendered NAME=SEED] --out
RESULTS.json`; #861). For each record and arm, render the gold chunk's field; split each line at the
first `" — "` (U+2014, a space each side); symbol left, definition right. Normalise the definition: NFKC,
casefold, whitespace collapsed. Tokens `[a-z0-9]+` of length > 1, stemmed by stripping the first of
`ations, ation, ated, ions, ion, ing, ed, es, s` that the token ends with and that leaves ≥ 4 characters.
Binding terms are tokenised the same way; a term with no word never passes and is counted
(`empty_terms`). **Pass** = some entry of some gold chunk's field has a definition word **set containing**
every word of at least one binding term (set containment: not substring, not ordered). Reported per
quantity asked, per stratum and in total; also under `qwen36p-shuf` as the chance level. The output is
refused inside the repository; `found10.py analyse --proxy RESULTS.json` reads
`per_record[record_id][arm]["pass"]` as the H4 outcome.

**M3 — precision** (§6). Separate report.

**Descriptives, always printed:** per-arm reach (chunks with a field; records whose gold chunk has a
field), proxy pass under shuffle, discordant counts per contrast, the run-to-run noise floor (one `rules`
repeat per corpus, `--repeat 2`; round 1's was 0–2 records on papers-OC), realised sizes, windowing
(documents at the 4 × 80k cap) and degraded counts per arm.

## 6. Precision (gold labelling)

- **Rubric v3, frozen as written in round 1** (`glossbench/rubric_v3_human.html`; labels
  `correct / partial / wrong / not-a-symbol`). `judge.py` carries no other rubric. No rubric change after
  any label is seen; a needed change means a new round.
- **Sample** (`glossbench/build_sample.py --corpus <alias>=SHARD --glossary <alias>:<arm>=FILE --docs
  <alias>=EVAL_IDS --seed 261101|gold --per-tercile 5 --owner-items 40`): 15 documents per corpus from
  the **eval papers**, stratified by terciles of the rules arm's rules-entry count; items = every
  distinct (canonical symbol, normalised definition) proposed by any arm (a `--rules-arms` arm its rules
  half, every other arm its LLM half), shuffled, arm hidden, document text as the arms saw it (the union
  of their windows).
- **Judges** (`glossbench/judge.py run --judge claude|codex|gateway`): Claude `claude-opus-5-5` (CLI,
  off-gateway), Codex `gpt-6-astra` (CLI, concurrency 1), Gemini 2.5 Pro (gateway, `--model`,
  `--max-tokens` always sent; the owner's chosen fallback, Gemini 3.5 Flash, if 2.5 Pro returns incomplete
  answers twice on a document — round 1 lost 3 documents that way, lab book; the served model id is
  recorded per call). Majority of three (`gold_common.majority`); items without a strict majority are `split`,
  excluded and counted. **Family overlap is stated:** with GPT-5.5 as `frontierp`, the Codex judge shares
  its vendor; `analyse_gold.py --overlap-judge codex` gives the whole majority table again without it.
- **Owner labels 40 items blind** (`build_sample.py --owner-items 40`, seed `261101|gold|owner`) on the
  committed page (`glossbench/make_sheet_html.py --storage-key glossbench-r2-owner`): a **fresh**
  labelling round — the browser storage key is new, so round 1's labels never carry over — and the
  page's **"Missed symbols" line is dropped** (`--missed` is off by default; round 1's page asked for
  symbols no item covered). κ against each judge and the majority (`glossbench/human_agree.py`) is
  reported; a second rater if available (§11 D10).
- Reported per arm (`glossbench/analyse_gold.py --contrast qwen36p:rules --seed 261101 --n-boot 2000`):
  harm (`wrong`), strict (`correct`), lenient (`correct + partial`), not-a-symbol share;
  document-clustered bootstrap 95% CIs; the pre-stated descriptive contrast `qwen36p` − `rules` harm.

## 7. Analysis (`glossbench/found10.py analyse`, `glossbench/found10_analysis.py`)

- **Statistics are part 4's code, not reimplemented**: every test, interval and Holm step is
  `chunktrial_stats` through `deployed_docs_part4.contrast_test`.
- **Test:** sign-flip permutation with **papers as the exchangeable units** (`<corpus>/<paper>`, so two
  corpora never share one): per paper, sum the record differences d ∈ {−1, 0, +1}; flip paper sums
  10,000 times (`n_perm`), seed 261101; exact enumeration when ≤ 20 papers are non-zero; p = (1 + #{≥
  obs}) / (1 + 10,000); no mid-p in the decision (mid-p reported as sensitivity). H2 shifts every d by +δ
  and tests one-sided > 0 (`chunktrial_stats.noninferiority_test`; the shift-to-null construction is
  approximate for d ∈ {−1, 0, +1}, as that function's docstring states, so the level is nominal).
  **Holm over H1–H4**, α 0.05, **always over four** (over **seven** after a §3.5 promotion): a missing
  or gated p enters Holm as **1.0** (`found10_analysis.holm_family`; the same rule is the default of the
  reranker test's `stats` from #867, D18). The family is not shrunk after any result is seen; a Holm
  over the evaluable members only (`holm_drop_gated` in the reranker test's output) is reported as a
  sensitivity reading only, never as the decision.
- **Intervals:** net share (gained − lost)/n, paper-cluster percentile bootstrap, 10,000 (`n_boot`), seed
  261101, 90% two-sided (`ci_level`; the matching one-sided 95% bound for H2). Exact McNemar beside,
  descriptive.
- **Missing:** a record with a failed retrieval call on either side is excluded from that contrast and
  counted; a sensitivity reading scores it not found.
- **Confirmed when** Holm-significant on O ∪ A. Per-corpus readings are secondary; a sign that differs
  between O and A is reported as *corpus-dependent* (`corpus_dependent`).
- **Minimum detectable effect**, quoted beside every null (`mde_split`): the gained/lost split a one-sided
  test at the Holm-adjusted α would have needed with the observed discordant count.
- **Power.** Two tables. (i) The **planning aid**, `power_sim.py` in this directory (stdlib only, seed
  20261029; its output `power_sim.json` is committed beside it and re-running the script reproduces it
  byte for byte): 50 papers × 10 records, between-paper SD of the gain share 0.15, α 0.0125 (Holm worst
  case), 400 simulations × 600 flips. It is a design assumption, not a measurement. (ii) The **committed
  table** the run writes: `found10_analysis.power_table` (`analyse --power-sims 2000`) on the realised
  paper layout, through `chunktrial_stats.simulate_power`, with the gain share s mapped to part 4's
  `discordant_share = 2s − 1` and the share SD to a beta ICC `sd²/(s(1−s))`. Round 1's discordance rates
  on R1 `base` (lab book Part 3): qwen36 vs rules 26 of 155 = .17 with gain share 22/26 = .85; qwen36 vs
  scout 29/155 = .19, share .76; qwen36 vs qwen38 6/155 = .04 (the frontier-vs-Qwen prior, given the
  partner's 43 vs 42 proxy).

  From `power_sim.json`:

  | | discordance .05 | .10 | .17 | .25 |
  |---|---|---|---|---|
  | H1/H3/H4 superiority, gain share .70 | .30 | .60 | .84 | .94 |
  | same, gain share .85 | .82 | .99 | 1.0 | 1.0 |
  | H2 NI, δ .05, true net 0 | 1.0 | .86 | .59 | — |
  | H2 NI, δ .03, true net 0 | .75 | .46 | .25 | — |
  | H2 NI, δ .05, true net −.02 | .71 | .45 | .25 | — |

  A round-1-sized qwen-vs-rules effect is resolved with power ≈ 1. **H2 is resolvable only if the
  frontier and Qwen are as concordant as the two Qwens were** (d ≈ .05); at d .17, δ .05 gives .59, and a
  small real frontier advantage will most likely end *not shown non-inferior*. That outcome is acceptable
  and is reported as such. One corpus alone (25 papers) at d .17 / share .85 gives .95; at share .70, .47
  (`H1_one_corpus_P25`) — so the per-corpus readings are secondary. The 500-record target is a floor, not
  a cap: more accepted records are kept.

## 8. Stopping, exclusion, gates

- **Manipulation checks, before any retrieval:** chunk counts of the three indices equal round 1's
  (32,765 / 27,418 / 14,456; `expected_chunks`); the stamp census shows one arm at a time (`swap.sh`,
  `verify_swap_record`); every glossary file resolves every document (`verified == docs`, `unverified
  0`); the reach gate (§3.1 step 4); the **glossary-boost probe** (§5 M1) per arm × corpus run; and,
  from the run itself, the **inactive-reranker rule** — `base` identical to `norerank` on every record
  makes `base` GATE-NOT-EVALUABLE for that arm × corpus (`found10.py`, in code at `e3002ba`).
- **Gateway terms, enforced in code** (`R2Gateway`, `judge.py`): `fallback_model ""`, `max_tokens`
  always, ≤ 4 in flight, empty = failure → retry once → skip after two; **5 failures in a row or > 20 in
  total pauses** (`max_fail_consecutive`, `max_fail_total`); HTTP 429/quota after 3 backoffs (30 s, 120 s,
  600 s) pauses; a `PAUSE` file or SIGTERM stops issuing, lets in-flight calls finish, writes state and
  totals, exits 75. **Notice gate:** `preflight --stage draft|verify --set S` prints and records one
  `GATEWAY-NOTICE` (planned calls, estimated tokens in/out, models, concurrency); `draft` and `verify`
  refuse to start without `--notice-acknowledged`, without a preflight for the same plan, or after a
  pause without a fresh preflight. **The partner set no cap and no end date** for the gateway; the
  per-batch size notice stays as our side of that. **Our own budget stop:** the run pauses at
  **5.0M gateway input tokens or 0.75M output tokens or 4,000 calls** (`limits.input_token_cap`,
  `output_token_cap`, `call_cap`), counted across resumes plus `prior_gateway_usage` (the frontier
  batch's `glossary.jsonl` and earlier stages' totals, summed by `resolve_prior_usage`). The planning
  estimate is ~1.3M input for the frontier batch, ~1.0M for drafting O and A, ~0.5M for P and ~0.7M for
  the Gemini judge pass (PLAN, Phase 1; design assumptions from round 1's measured rates). Resume only
  after a fresh notice.
- **Hard stops** (exit 1, no resume without a logged decision): a manipulation-check mismatch; a failed
  glossary-boost probe (§5 M1); > 2% of retrieval calls failing after retries; a frozen-prereg sha
  mismatch (`prereg.sha256` in the private config); a resume against a different commit, config or
  pool; a failed isolation check; on P, a binding-file sha mismatch or an `in_set` split other than
  13 / 13 (§3.4).
- **Exclusions, fixed now:** records rejected by §3.3; records whose gold chunk is absent from an index
  (none expected: same chunks); judge items with a `split` majority.
- **GATE-NOT-EVALUABLE:** a corpus whose S1 < 150 (retrieval leg); a contrast whose minimum attainable p
  > α; an arm/condition whose served depth fell short; `base` identical to `norerank` on every record;
  `frontierp` if the gateway closes before its batch completes — then H2 is GATE-NOT-EVALUABLE and
  enters Holm at p = 1.0 (§7); F stays as fixed (H1–H4, or the seven of §3.5).
- **Set P absent** if neither drafting nor acceptance happens: the report says the papers-OC leg rests
  on R1 (one paper) only.

## 9. Code, pin, provenance

**On `main` at `e3002ba` (2026-10-29), unit-tested and reviewed:**

| item | module | PR |
|---|---|---|
| pure and empty arms | `python/ragstack/ingestion/glossary_file.py` (`rederive_file`, `MERGE_MODES`, `MERGE_MARK`); CLI `python/scripts/glossary_file.py rederive --merge {llm-only,rules-only,merged,none}` | #861 |
| shuffled control | `python/ragstack/ingestion/glossary_shuffle.py`; `python/scripts/backfill_symbol_glossary.py --glossary-file F --shuffle-rendered SEED`; `python/scripts/eval/part4_glossary.py --shuffle-rendered NAME=SEED` (in-memory, docs-A) | #861 |
| wording proxy | `python/scripts/eval/wording_proxy.py` | #861 |
| arm-blind pool, strata, reach gate | `python/scripts/eval/glossbench_r2_pool.py`, shared `python/scripts/eval/_glossbench_r2.py` | #863 |
| drafter, layer-1 checks, verifier, leak guard, acceptance log, spot-check | `python/scripts/eval/glossbench_r2_draft.py`, `python/scripts/eval/glossbench_r2_checks.py` | #863 |
| found@10 runner and analysis, swap | `python/scripts/eval/glossbench/found10.py`, `found10_analysis.py`, `swap.sh`; contract test `test_integration_contract.py` | #864 |
| gold sample, judges, precision analysis, owner page, κ | `python/scripts/eval/glossbench/build_sample.py`, `judge.py`, `analyse_gold.py`, `make_sheet_html.py`, `human_agree.py`, `gold_common.py` | #864 |
| read-only chunk export | `python/scripts/eval/glossbench/export_chunks.py` | #865 |
| statistics | `python/scripts/eval/chunktrial_stats.py` via `deployed_docs_part4.contrast_test` (part 4's, unchanged) | — |

**Not yet on `main` at `e3002ba`; both required before freezing (D13):**

| item | module | PR |
|---|---|---|
| glossary-boost behavioural probe (§5 M1; `inactive` / `mixed` / `error` = hard stop, `not_testable` = gate unless declared); Holm with gated or absent members at p = 1.0 as the reranker test's default, `holm_drop_gated` as its labelled sensitivity (§7); the reranker test's inactive-reranker gate | new shared `python/scripts/eval/glossary_boost_probe.py`; `glossbench/found10.py` (`--chunk-export`, `--rendered-fields`, `--boost-declared`); `reranker_test.py` | **#867** (`exp/r2-manip-checks`) |
| set-P field mode: `glossbench_r2_fields.py` (field list v2 with the `n_terms == len(terms)` assertion, `in_set` from the held set with `expect_in_set`, `constants` + `constant_cap`, 2 : 1 quota plan, arm-blind lexical prefilter, dimension map, term-leak guard); pool `draft_mode: fields` and the per-set quota override `pool.per_set`; layer-1 `unit_incompatible`, `term_leak`; `--pilot N --papers K`; `export-partner`, `import-partner-verdicts` (§3.4) | `python/scripts/eval/glossbench_r2_fields.py`, `glossbench_r2_pool.py`, `glossbench_r2_checks.py`, `glossbench_r2_draft.py` | **#868** (`exp/r2-setp-fields`) |

**Pin.** Every run from one `main` commit **at or after `e3002ba`** in a clean worktree (`dirty false,
untracked 0`) or from a tools image built from a tools tag at or after it (ADR-0010 order); a run that
uses a row of the second table runs at or after that row's merge commit. Every output
embeds `experiment_provenance()`; glossary files carry `producer` and `tool`; `citable` is read from the
record (`_glossbench_r2.citable`). Scratch-script analyses are **not citable** (round 1's lesson); every
analysis here is a committed script.

**Record.** `docs/plans/results/glossbench-round2-2026-11/`: this prereg and its `README.md` (the
freeze record), `PLAN-round2.md` (lab-book tier), `power_sim.py` + `power_sim.json`, and after the run
`RESULTS.md` with the status of each set in every table caption, `artifacts/*.json` counts-only with
`artifact_provenance` (sha256 of the private source, citable flag, private pointer), `DEVIATIONS.md`,
`LABBOOK.md`.

## 10. Privacy

- **The partner's R1 set and any P set it accepts** may be held in this project's **private**
  experiments repository (the partner's user lifted the "never copy" rule for it, lab book addendum of
  2026-10-29) and are **never** copied into this public repository; the public record holds counts and
  ids only.
- **Our sets O, A and P** hold article text (quotes): private, never in the public record; the binding
  export is held privately too (its sha256 is public).
  Every private output is refused inside the repository (`refuse_in_repo`). Public artifacts hold
  counts, rates, κ, CIs, model ids, version strings and sha256 hashes only, checked by listing every
  string value (`_glossbench_r2.string_values`).
- The record names **no tenant, collection, host, endpoint, product or gateway owner**; corpus aliases
  are round 1's. Owner labels stay private; only agreement statistics are exported.
- Drafter prompts carry chunk text to the gateway; that is within the partner's standing approval. The
  verifier and two judges run off-gateway. No glossary text, no arm name and no partner data goes to any
  model other than as a judge item.

## 11. Decisions taken (owner, 2026-10-29)

The owner accepted every recommended default of the decisions list put to it on 2026-10-29. Fixed at
freezing; a later change is a deviation.

| # | decision | taken |
|---|---|---|
| D1 | frontier glossary model | **GPT-5.5** (reason in §4) |
| D2 | frontier scope | **eval papers only** (~1.3M input, design estimate); whole corpora (~27M) is a separate ask, not planned |
| D3 | drafter / verifier families | **Gemini 2.5 Pro drafts (gateway); Claude Opus 5.5 verifies (CLI, off-gateway)** |
| D4 | set P acceptance | **the partner's sampled acceptance** (§3.4 b): scripted checks on every record plus a judged random sample of 50 with its seed; P's label is *scripted-clean, judged sample of 50, error rate X% (95% CI …)*; P is never called equivalent to the 155-record set (settled 2026-10-29, after v1) |
| D5 | H2 form and margin | **non-inferiority, δ = 0.05 net share** |
| D6 | confirmatory population | **S1**, S2 thinly sampled as the tie/distractor check |
| D7 | arms in the family | **{rules, qwen36p, frontierp, qwen36p-shuf}**; `qwen38p`, `scoutp`, `none` secondary |
| D8 | retrieval path for O and A | **served pipeline through bench copies with swaps** (`swap.sh` + `found10.py`) |
| D9 | gateway budget | the partner set **no cap and no end date**; the per-batch notice stays; **our own pause at 5.0M in / 0.75M out / 4,000 calls** |
| D10 | owner labelling | **40 blind items (κ) + 40 spot-check records per set; a second rater if available** |
| D11 | judges | **three (Claude CLI, Codex CLI, Gemini gateway), majority; vendor-overlap flag and a table without the overlapping judge** |
| D12 | Qwen arm in the family | **Qwen3.6** (`qwen38p` secondary) |
| D13 | freeze gate | **after all §9 code is merged** — the harness at `e3002ba`, plus **#867** and **#868** (§9, second table); both before the freeze, not only before P's pilot (re-stated 2026-10-29, final pre-freeze pass) |
| D14 | set-P drafting population | **the partner's binding export v2** `binding_fields_261029_v2.json` (26 fields, sha in §3.4; v1 `08309d0f…` superseded, 2026-10-29), not the held set's fields; constants capped at 2; the 10 new-ground fields at 2 : 1; definition-words questions with the term-leak guard; value as printed, dimension-checked |
| D15 | set-P pilot | **≈ 50 records from 3 papers after freezing**, format check only, data for no hypothesis; its papers excluded from P; its partner verdicts are the input to D17 |
| D16 | S3 quota on P | **closed 2026-10-29: 6 per paper** (≈ 150 records over 25 papers, before rejects), via the per-set override `pool.per_set = {"P": {"s3_per_paper": 6}}` (#868). O and A stay at 2. S3 remains exploratory |
| D17 | promotion of set P into F | **allowed by one bounded addendum** (2026-10-29), §3.5: after the pilot and the partner's judged error rate on it, before any non-pilot P record is drafted; adds exactly H1-P, H3-P, H4-P (not H2); Holm over 7, gated members at p = 1.0; condition = Wilson 95% upper bound of the partner's pilot error rate ≤ **0.15** (owner-accepted default, the layer-3 bar) on ≥ 30 judged records; if it fails, P stays a replication with no further option. Disclosed as one post-pilot decision point bounded by that rule |
| D18 | Holm with a gated or absent member | **p = 1.0, the family never shrinks** — implemented as the default in #867 (`found10_analysis.holm_family` already had it; the reranker test's `stats` adopts it); `holm_drop_gated` is a labelled sensitivity only, never the decision (2026-10-29; the reranker test's D14) |
