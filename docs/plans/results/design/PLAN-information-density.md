# PLAN — information density and context dependency as chunking variables (a candidate sub-study)

**Status: proposed 2026-10-13. Nothing has run, nothing is measured, nothing is decided.** Every
quantity this note defines is a **proposal**. Every number it quotes is from the committed record,
with its source. **Sits beside:** [`../../chunking-study-plan.md`](../../chunking-study-plan.md) (SG1–SG6,
E1–E4, D1–D17) and [`../../system-study-plan.md`](../../system-study-plan.md) (§2.3 Q3, §2.5).
**Literature:** [`docs/papers/bib-inbox/11-information-density-and-context.md`](../../../papers/bib-inbox/11-information-density-and-context.md),
written for this note. It checks every citation in the input and searches five areas.

**Input.** On 2026-10-13 the owner pasted an LLM-written analysis and "literature review" proposing
information density and contextual dependency as the variables that should set chunk size. That text
is not in the repo. Its citations were checked one by one (bib-inbox 11 §0): of 16, 11 verify, 4 are
already in our surveys, and **one claim is wrong**. That is the "62–64 % → 72 % answer correctness"
attributed to `demoura-2026`, which is the mean of two metrics, not answer correctness (that paper's
Table 5). None of the text's citations measures density before retrieval and relates it to a
retrieval outcome.

**Scope.** This is a **candidate chunking sub-study, gated on the human read (E1).** It is **not in
the LDRD scope**, with one optional exception: a single rule-based pre-read feature (§4.4). It changes
no decision, no pre-registration and no D-number. Its open items are numbered ID-1 to ID-6 (§6) so
that they cannot be mistaken for decisions.

---

## 1. The external text's proposals, in our terms, against what already exists

| the text proposes | in our terms | what exists | source |
|---|---|---|---|
| "information density is not one quantity": information per token; independent meaningful information per token; context needed to interpret it | three different properties: **(i)** a text property (surprisal, lexical or propositional density); **(ii)** evidence per **delivered** token; **(iii)** context dependency (decontextualizability, bib-inbox 11 *Established names*) | (ii) is the system plan's **Q3**: "evidence delivered per delivered token at budget B", **proposed, no committed metric**. (i) and (iii) have no instance anywhere in the record | [system-study-plan.md](../../system-study-plan.md) §2.3 |
| "task-relevant density" = relevant units in c / tokens in c, per query | evidence per delivered token at a fixed budget. The field's name is **`EM @ l tokens`** (and "recall within the initial l retrieved words"); our sufficiency construct is **nugget support at a fixed delivered-token budget** | the closest committed reading is budget-matched `PR_B@4096`: `tok256` 0.291 vs `tok2048` 0.132 at m = 1, so "chunk small" by **2.2× at m = 1 and 3.8× at m = 16**. The fixed-k ordering points the other way | [breadth-k/RESULTS-breadth-k.md](../breadth-k/RESULTS-breadth-k.md) §7; naming: [docs/papers/README.md](../../../papers/README.md) § *Naming decisions*; [ANSWER-sufficiency-and-judges.md](ANSWER-sufficiency-and-judges.md) §2.0 |
| "context loss" when a unit is cut from its surroundings | **containment** (`EPACK`), the per-reached-document fraction of evidence units fully inside the admitted text; with **reach** (`ERET`), the "within-document retrieval failure" decomposition | from 256 to 2048 tokens, `ERET` falls **0.277 → 0.112** and `EPACK` rises **0.188 → 0.696**. The product `ERET×EPACK(a)` stays within **0.046–0.100** across arms: "size moves evidence between reach and containment rather than creating or destroying it". **Calibration** (10 CDS dev topics, B = 16,384 SFR tokens, hybrid + rerank) | [stage0/RESULTS-stage0b-prime.md](../stage0/RESULTS-stage0b-prime.md) §3 |
| C_index ≠ C_context ≠ C_return | **SG4**: index at one granularity, deliver another (`parent256`, `nbr1_512` = production's `context_window = 1`). Post-retrieval compression (bib-inbox 11 §3) acts on C_return only | `nbr1_512` reach **0.101**, below the 0.15 floor at 16k, so R4's `ERET` leg is window-demoted. Joint power at n = 80: R3 **0.495**, R4 **0.529**, failing the gate. **Calibration** | 0b′ §3, §4; [chunking-study-plan.md](../../chunking-study-plan.md) SG4 |
| C* = argmax [EvidenceCoverage − λ·ContextLoss − μ·RetrievalCost] | the study's objective in its **cost-minimising, non-inferiority** form: the cheapest C with E(C) ≥ E(C_ref) − ε on `ERET` (and `EPACK` on the population that carries it). μ·RetrievalCost is **D16**, which is undefined: "'Cheapest' has no defined cost term" | the quality-maximising form (A) and the NI form (B) are both stated; the choice is open (S1, S2; D16 is its chunking instance) | chunking-study-plan §1 *The objective*, D16; system-study-plan §2.5 |
| C* = f(D, K, Q, R, B) | Q (population), R (served path: hybrid + `bge-reranker-v2-m3`) and B (16,384) are **fixed by r3**. D (density) and K (dependency) are **new covariates**. The record already shows the Q term is the largest: the construction difficulty of the query legs moved `PH@1` by **−0.43 to −0.48**, about 15× any breadth effect | system-study-plan §2.2, citing [results/README.md](../README.md) § breadth-k |
| minimum sufficient evidence budget B_min(q, d) | the field's **minimal context** / SciFact's **rationale** ("a minimal collection of sentences … sufficient"), measured in tokens; bib-inbox 11 *Established names* | **this is the "where" of Paper A**, which the record shows is the unstable object. Judges agree with themselves on *whether* a document holds evidence (Qwen **0.9677**) far more than on *where* (**0.4194**). Under whole-sentence anchors, span self-consistency is **0.3831** (Scout) and **0.5617** (Qwen) against a ≥ 0.90 gate | Paper A claims `r3-qwen-whether-agreement`, `r3-qwen-span-self-consistency`, `r31-scout-span-self-consistency`, `r31-qwen-span-self-consistency` → [r3/gates-r3.json](../stage0/artifacts/r3/gates-r3.json), [r31/gates-r31.json](../stage0/artifacts/r31/gates-r31.json) |
| fixed sizes {128, 256, 512, 1024} | the arms 256 / 512 / 1024 / 2048, plus `header512`, `parent256`, `nbr*`, all embedded at the Stage 0 pin. No 128 arm exists | 0b′ §3 table; [RUN-PACKAGE.md](../RUN-PACKAGE.md) |
| density by genre and section; section-level predictions | section is a **confound** to control, not yet a measured variable. `section_title` exists only for JATS and is discarded at OA ingest today | system-study-plan §3 (*Parsing / ingest*, *Chunking* rows); [section-taxonomy.md](../../section-taxonomy.md) |
| adaptive chunking last | the new method arms (C-R0 … C-R5) are **not designed** (D8) | chunking-study-plan §3, D8 |
| H1 density, H2 contextual dependency, H3 joint | restated as H-a, H-b, H-c in §4, each with the instrument that would read it | — |

**What the mapping shows.** About two thirds of the text restates the study's existing frame under new
names. The context-loss term is containment; task-relevant density is `EM @ l tokens` / nugget
support at a budget; the cost term is D16; C_index ≠ C_return is SG4; B_min is the "where" of Paper
A. Adopting the field's names (bib-inbox 11) avoids coining "RID" and "SID".

## 2. What is genuinely new

Three things the study does not have, each tied to verified literature:

1. **Context dependency as a dimension.** The study measures *outcomes* of cutting text (containment),
   never the *text property* that predicts them. The field has an annotation scheme for that property:
   **decontextualization feasibility**. Each sentence is labelled stands-alone / feasible-with-edits /
   infeasible (`choi-2021-decontextualization`, *TACL* 9). Choi et al. found encyclopedic text
   "substantially easier to decontextualize" than newswire or literary text. `newman-2023` did the
   same on scientific snippets and found that references to other documents are a dependency type of
   their own. `gunjal-durrett-2024` names the trade-off: **decontextuality vs minimality**. The nearest
   use of dependency for chunking, `demoura-2026`'s *References Completeness* (the share of
   entity–pronoun pairs split by a boundary), **selects a chunking method per document**. It does not
   set a size, it is not budget-matched, and it is machine-judged on 33 documents (bib-inbox 11 §4,
   *CHALLENGES* 1–2). **No paper found sets chunk size per document from a pre-chunking dependency
   measure.**
2. **Cheap linguistic density features.** Propositional idea density from POS tags (CPIDR,
   `brown-2008-cpidr`: r(80) = .97 against two-rater consensus, but validated on narrative language,
   not scientific prose); content-word lexical density (`zhu-2024-lingua`: varies by discipline);
   per-token surprisal (`rubino-2016`, and UID as its theory, `levy-jaeger-2007`). All are computable
   at ingest without a model call or with a small one. **None has been related to a retrieval outcome.**
   Two cautions come from the same search. LLMLingua-2 argues that surprisal is a poor proxy for what
   matters. `dettori-2026` shows that denser delivered context can *hurt* the generator, and it uses
   "lexical density" to mean lexical *diversity*, a name collision.
3. **Task-relevant density per document**, as opposed to per arm. The record reads delivered evidence
   per budget **per arm** (Q3's closest instance, `PR_B@4096`). It never asks whether the *same* arm
   delivers more evidence per token in some documents than others, or why. B_min(q, d) in tokens, read
   per (question, document), is that per-document quantity. It is new to the study, though not to the
   field (`min-2018-minimal-context`).

## 3. The measurement problem

**3.1 Unit counts and interpretability scores are themselves stochastic judgements.** The quantities
in §1–§2 need someone, a model or a reader, to decide what a "unit" of evidence is, where it sits,
and whether a sentence stands alone. Paper A's record shows such judgements cannot be taken at face
value. LLM judges reproduce *whether* a document holds evidence and do not reproduce *where*. A graded
per-sentence support score becomes reliable only with depth: Spearman–Brown **0.9205** at 30 pooled
readings (Paper A claim `graded-support-reliability-at-30`,
[r31ext/gates-r31ext.json](../stage0/artifacts/r31ext/gates-r31ext.json)). Two judges' graded maps
correlate at only **r = 0.2976** per pair (claim `judge-judge-graded-support-correlation`, same file):
**reliable is not valid**. So, in order:

- **Reliability first.** Any LLM-derived density count (atomic facts per 1k tokens, Choi labels, a
  Claimify-style decontextualisation score) must pass a test–retest gate on the same items before it
  is used as a covariate. The Paper A protocol (re-presentation, split-half, Spearman–Brown) is the
  template. A score that fails the gate is reported as failed, not used.
- **Validity from people.** The human read (E1) is the only validity anchor the study plans. B_min
  and dependency labels from experts need two readers' agreement (κ) before they are used. This is why
  the sub-study is gated on E1.
- **Rule-based features need no gate for reliability.** CPIDR or a regular-expression back-reference
  share are deterministic. They still need **validity** (do they track the expert labels?), and they
  live in the coordinate system of the sentence segmenter. Any sentence-keyed feature records
  `sentence_spans_fingerprint()` (docs/papers/README.md § *Every artifact records which code produced
  it*).

**3.2 Confounds.** Density will co-vary with things the study already knows matter:

- **Section.** Methods, results and discussion differ in register (`rubino-2016`). Evidence position
  is not uniform either: **55.4 %** of judged evidence starts past token 1,024 (the position oracle, as
  quoted in [docs/papers/README.md](../../../papers/README.md) § *What the literature survey did*).
  Density must be read within section, or section must be a covariate. On the OA load that needs the
  JATS `section` field kept at ingest (system-study-plan §3).
- **Length.** Document length and realised chunk length move with density (a dense paragraph is
  shorter). The study's rule applies: compare at **matched realised size and matched delivered
  budget** (chunking-study-plan *Summary*; breadth-k §7).
- **Field and query difficulty.** Lexical density differs by discipline (`zhu-2024-lingua`), and query
  construction difficulty is the largest effect on the record (−0.43 to −0.48 on `PH@1`, system plan
  §2.2). A density effect must be shown to add to the population effect, not to restate it
  (`bhat-2025`: the optimal size already varies by dataset, attributed to answer type).

**3.3 Power on CDS.** TREC CDS has **90 topics in total**, 80 for confirmation, and the σ-bound
sizing for the size contrasts is **286–453 topics** (bootstrap 140–220)
([SPEC-confirmation-run-r3.md](SPEC-confirmation-run-r3.md), the status block above §0). On the 80
topics the size contrasts already fail the joint-power gate: N1 **0.560**, N3 **0.369**, R1 **0.513**
([stage0/RESULTS-stage0b-prime.md](../stage0/RESULTS-stage0b-prime.md) §4; `stats.json`
`cds_sizing`). A density × size **interaction** is a contrast between subsets of those topics, so it
has less power than the main effect it modifies. **On CDS, H-a to H-c can only be descriptive.** On
the existing pointed set every arm is at the ceiling (`ERET` 0.904–0.955; 0b′ §3, §6), which leaves no
room for a covariate to show. A confirmatory reading needs a pointed population that clears guard 1
(D1, D15).

## 4. Hypotheses and a minimal first measurement

All hypotheses are **proposed and exploratory**. None is pre-registered, and none may be read on the
quarantined confirmation labels before unblinding (G4).

**H-a (density).** At matched delivered budget and matched realised size, the reach cost of coarse
chunks (`ERET` at 256 minus `ERET` at 1024 or 2048) is **larger in documents with higher pre-read
evidence density**. Several evidence units per chunk compete for one retrieval slot. Reading:
per-(topic, document) `ERET`/`EPACK` by arm, with density tercile as a covariate. In the text's own
form this is Y = β0 + β1·C + β2·D + β3·(C×D) + β4·K + ε, with **β3 the quantity of interest**, fitted
within topic (paired arms).

**H-b (context dependency).** Fine chunks lose more containment in documents (or sections) with a
higher share of context-dependent sentences, and neighbour or section delivery (`nbr1_*`,
`parent256`, SG4) recovers more of it there. Reading: the interaction of the R3/R4 delivery gain on
`EPACK` with the dependency measure.

**H-c (joint).** A size rule computed **before retrieval** from (D, K) per document is non-inferior to
the best single global size on `ERET` and `EPACK` at matched budget, with fewer vectors. It is read
**only if** H-a or H-b shows an interaction, and only on a population that clears guard 1. This is
the step `demoura-2026` took for method choice; ours would be for size, budget-matched, behind the
reranker.

**4.1 Minimal first measurement: minimal sufficient context from expert-marked spans.** No
retrieval and no new embeddings. For each (question, gold document) pair the expert reads anyway:

1. **Mark the minimal set of sentences sufficient to answer**, in SciFact's rationale sense, giving
   B_min in sentences and in tokens (name the tokenizer).
2. **Label each marked sentence** with Choi et al.'s scheme: stands alone / needs edits / cannot stand
   alone. Where it needs edits, say what: a back-reference, a term defined elsewhere, another document.
3. **Offline, against the existing arms' committed spans:** for each arm, does B_min fall inside one
   chunk, across k chunks, or outside the delivered window? This is a containment-like quantity
   computed from human spans. It needs no retrieval run, only the pinned arms' span files read at
   their pin.

Two populations are proposed:

- **The planned microbiology/virology pointed set** (D15,
  [PLAN-micro-pointed-set.md](PLAN-micro-pointed-set.md) §5). Its expert check already asks
  "answerable from the gold passage alone?" and "other valid locations?". Items 1–2 above would add
  about one minute per pair (estimate). The pilot on the 20 Salmonella-AMR papers (§6 of that plan)
  is the cheapest place to try the protocol. A metagenomics/wastewater question set was named in the
  session that commissioned this note. It is not on the record, so this note attaches to the D15 set
  and would apply to that set unchanged if it is built.
- **The CDS human read** (HR-dev; chunking-study-plan §3). Adding questions to the two-reader R-dev
  protocol before freeze is a **pre-registered amendment** (D4, *Still open*). This note proposes
  only a **separate pass on the same pairs after R-dev's κ is committed**. CDS confirmation pairs are
  untouched.

**4.2 What the first measurement yields, all proposed:**

- the distribution of B_min (tokens) by section and by population;
- the share of B_min sentences that cannot stand alone;
- the share of pairs whose B_min straddles a boundary at 256 / 512 / 1024 / 2048;
- κ between the two readers on B_min and on the dependency labels. **If κ is low, B_min is not a usable
  gold object**, which would be a Paper-A-shaped finding about humans and a reason to stop.

**4.3 Pre-read features to test against it** (proposed, cheapest first). Each is computed per document
and per section, at ingest, with its segmentation fingerprint:

- back-reference share (§4.4);
- CPIDR propositional density;
- content-word ratio;
- mean surprisal under a small LM.

The first test is validity: does each feature rank documents the way B_min and the dependency labels do?

**4.4 The one feature that could enter LDRD scope (optional).** The **share of sentences that open with
a back-reference**: the first token or bigram is in a fixed list of anaphoric openers ("This",
"These", "Such", "It", "They", "The latter", "In this …"). The list is to be fixed before any data is
seen. It is rule-based, deterministic, costs one pass over `sentence_spans()` output, needs no model,
and is a direct, crude proxy for Choi et al.'s "cannot stand alone" label. It is the only part of
this note proposed as a possible LDRD item, and only as a recorded feature, not a hypothesis test.

## 5. What this note is not

- **Not in the LDRD scope**, except optionally §4.4.
- **Not a change to r3, the rubric, the human-read protocol or any D-number.** Every interaction with
  them (§4.1 CDS pass, D15 expert check) is proposed as an addition after, or beside, what is planned.
- **Not a claim that density matters.** Nothing here is measured. The literature search found no
  study that relates a pre-retrieval density or dependency measure to retrieval at a matched budget,
  and found two reasons for caution (`dettori-2026`, `pan-2024-llmlingua2`).
- **Not adaptive chunking.** H-c is the last step, and it is read only after H-a/H-b and only on a
  population that clears guard 1.

## 6. Open items (numbered for reference, not decisions)

| # | item | depends on |
|---|---|---|
| ID-1 | Whether to run §4.1 at all, as a candidate chunking sub-study, after the human read (E1) | E1 (HR-dev κ committed); owner |
| ID-2 | Whether §4.1 rides on the D15 expert check (pilot first) or waits for the D15 set to be built | D15, D1; owner |
| ID-3 | Whether a post-κ, separate pass on the R-dev CDS pairs is acceptable, or whether CDS stays out of this entirely | D4 (*Still open*), D6; owner |
| ID-4 | Whether §4.4's back-reference share is recorded as an LDRD-scope feature; if yes, the opener list is fixed and committed before any document is read | owner |
| ID-5 | Whether the JATS `section` field is kept at OA ingest, which is needed for any section-level reading (§3.2) | oa-full-ingest.md § *What must land before the load*; system plan |
| ID-6 | Promotion of bib-inbox 11 entries (`choi-2021`, `min-2018`, `gunjal-durrett-2024`, `demoura-2026` correction) to `bibliography.md` when Paper B's related work is written | Paper B |
