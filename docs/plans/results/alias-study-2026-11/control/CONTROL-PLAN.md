# Chunk-boundary control for the alias study — plan

**Status:** committed with the owner's answers of 2026-11-05 (§8), before any judge call. Drafted
2026-11-04 and revised the same day after the owner's decision that the control waits for the real
#828 chunker (PR 4 then PR 7) instead of a heading-line stand-in. As drafted, nothing in it had run;
the pre-judge outputs are listed at the end. Exploratory, post-freeze. It changes no confirmatory number, no cell, no
record, no judge, no seed and no gate of the frozen prereg (`prereg-alias-study-2026-11`, sha
`d54f4201…`). Written blind: no stage-A label, found@10, drift figure, owner label or spot-sheet
passage was opened (`main/judge/final.jsonl`, `labels-*.jsonl`, `main/logs`, `spot/*`, the stage-A
commit record). Counts below come from `wc`, the chunk exports' text shape, the prereg, the pilot
artifact and the code.

## 1. The question

**Do the judges' labels change when the same question is judged against a boundary-respecting chunk
covering the same text, instead of the `tok512o0` window it was judged against in stage A?**

The nine bench collections were chunked `tok512o0` = `fixed_token / 512 / 0` (`chunk_descriptor`,
`python/ragstack/provenance.py:47`; the index names in the private run config):
a sliding 512-token window, no overlap, blind to sentences and sections (`FixedTokenWindowChunker`,
`python/ragstack/ingestion/chunkers.py:1524`). Corpus-wide the windows look the way the owner's spot
sheet does: in the base export 25.6 % of 32,765 chunks end with `.`/`!`/`?` and 51.8 % start with a
capital or digit (neighbour export: 19.6 % / 50.1 %), and 11,630 base chunks (35 %) contain a `## `
section heading inside the window, i.e. cross a titled section boundary.

The control is **judge-only** (option (a), §3): no collection, no retrieval, no confirmatory figure
touched. It is recorded as an **addendum** (`docs/plans/results/alias-study-2026-11/ADDENDUM-<date>-
chunk-boundary-control.md`, round 3's precedent `ADDENDUM-2026-10-30-spot-validity.md`), not a §12
deviation ("any change to a hypothesis, cell, record, judge, threshold, seed, gate or analysis" — none
is changed); a one-line cross-reference goes into `DEVIATIONS.md` if that file exists for another
reason, and every sentence quoting a control number carries *exploratory*.

## 2. The control chunking: the merged #828 path (owner decision, 2026-11-04)

**What exists on `main` today.** ≤ 512 tokens and whole sentences: `SentenceChunker(size_unit="tokens",
chunk_size=512, max_tokens=512, chunk_overlap=0, budget_mode="joined")`, `chunkers.py:876–937` →
`_pack_spans_tokens` `:1389–1522` (fill measured on the joined text, cut back to a sentence boundary;
a single over-budget sentence is token-split losslessly); `sentence_spans` `:835` (Punkt seeded with
`SENTENCE_ABBREVIATIONS` `:537`, regex fallback `:655` — assert `segmenter_version() ==
"punkt-abbrev-v1"`, `:817`; the fleet ran the regex until #815). Tokenizer: `HFTokenCounter` on
`Salesforce/SFR-Embedding-Mistral` (`ingestion/tokenization.py:135`, the study's embedder). Section
awareness: **not yet** — `CHUNK_METHODS` (`config.py:450`) has no section-aware method, the seam
`_merge_small_pieces(barriers=)` `chunkers.py:1148–1170` says "Nothing passes them today", and of the
#828 series (`docs/plans/828-document-structure.md` §12) only PR 1 `#839` (model + chunk stamp), PR 2
`#843` (Markdown adapter, raw mode) and PR 3 `#845` (registry resolution in `embed_shard.py`) are
merged. PR 4 (markers + the one reader `structure_from_params`) and PR 7 (`structure_cuts`,
`embed_heading_path`) are now being implemented; the control waits for them.

**The control chunking, once PR 4 and PR 7 are merged** (no stand-in, no bespoke segmenter; the
control script calls `ragstack.ingestion` only):

| item | value |
|---|---|
| structure | `attach_structure` (`structure.py:839`) with the Markdown adapter `markdown/1` in **raw mode** (`INGEST_ADAPTERS = {".md": …}`, `structure.py:52`; `markdown_structure.py`): top-level ATX headings open sections, a document with no heading is one untitled section. The study's record text is what `jats.py:section_text` emits — titled `<sec>` as `## Title` lines at level `min(depth+2, 6)` — so the adapter reproduces the titled JATS section tree with its nesting. **Untitled JATS sections are invisible to it** (that is PR 5b's adapter, unmerged) and **PDF-sourced documents have no headings** (PR 8, unmerged): both become one untitled section and are cut on sentences only; their share is reported as a stratum. Each `Document` is built with `source="<doc_id>.md"` so the adapter is selected; `content` is the record text, unchanged (`text_render` absent). |
| chunker | `make_chunker("sentence", chunk_size=512, chunk_overlap=0, max_tokens=<the SFR window, ≥ 640>, token_counter=HFTokenCounter(SFR), size_unit="tokens", budget_mode="joined", size_tolerance=0.25, min_piece=128)` plus PR 7's `structure_cuts="sections/1"`. `chunk_size` is the **target**; the #831 ceiling is min(⌊512×1.25⌋, max_tokens) = 640. `max_tokens` must not be 512, or the clamp silently removes the tolerance. A piece at or under 640 stays whole, a sentence over 512 but within 640 is never cut, and pieces under 128 merge under PR 7's walls (owner, 2026-11-04) (§7.1: segments = each leaf section, and each parent's lead text; each segment packed on its own; heading attached to the segment it opens; offsets document-global; ids `uuid5(doc_id:start:end)`). |
| markers (`chunk_params`, #828 §7) | `{"size_unit": "tokens", "pack": "318", "structure_cuts": "sections/1", "size_tolerance": 0.25, "min_piece": 128}`. The first two are as `ingest_jsonl.py:630` records them for `sentence` today; `text_render` **absent** (raw: content, offsets and `text[start:end] == content` against the stage-A record text all hold); `embed_heading_path` **absent** (no embedding in option (a); for option (b) it is a separate arm, §11 of #828, and a vector change only); `structure_infer` absent. The exact key spelling is PR 4's reader to fix; the values are §7's. |
| identity | `chunk_descriptor` = `sentence/512/0/{"min_piece":128,"pack":"318","size_tolerance":0.25,"size_unit":"tokens","structure_cuts":"sections/1"}` (sorted keys) → a new `spec_hash` → a new physical store name. Stage A's is `fixed_token/512/0`. A marked collection is a **new collection by construction** (#828 §7); no stage-A chunk id can coincide with a control chunk id (different spans, same uuid5 scheme), so no label carries by id. |
| stamp | PR 1's `stamp_documents` gives every control chunk `section_path`, `section_level`, `section_roles`, `section_crossing`, `structure_source`, `structure_version` — used for the §5 diagnostics only. |
| provenance | `experiment_provenance()` with `span_fingerprint(kind="sections")` (the adapter's section offsets) and `kind="sentences"` over the sampled documents (`provenance.py:425–500`), `segmenter_version`, tokenizer id, markers as applied, harness commit. |

**Two consequences to state in the addendum.** (i) Size: by owner decision (2026-11-04) PR 7 extends the #831 tolerance-and-merge to `sentence` under `structure_cuts`. Control chunks therefore aim at 512 tokens, may reach the 640 ceiling, and fall below 512 at section ends whose tail could not merge (counted). Stage A's windows are exactly 512, except the last window of each paper. So passage length still differs between the arms: the §5 diagnostics report both token distributions, and flips are also reported by control-chunk length band.
(ii) If PR 7 does extend the merge to `sentence`, a chunk may hold two **whole** sibling segments
(`section_crossing: true`); it never holds a fragment of two segments. The prerequisite check below is
phrased for both cases.

**Step R (tools image) is not needed** for the judge-only control. #828 §7: the markers work on paths
A, B and D without R; R is the repin that wires path C's CWL embed steps (`jats-ingest.cwl`,
`pdf-ingest.cwl`, `embed-bulk.cwl`) and adds `[markdown]` to the image defs. The control runs the
library functions in-process ("same code, not the same process", §7): `attach_structure` →
`make_chunker` with the markers → `stamp_documents`, no CWL, no image, no store. What *is* needed in the
env that runs it: the `[markdown]` extra (`markdown-it-py`, `mdit-py-plugins`; the study's Python
environment lacks `mdit-py-plugins` per §12 R — an install by the deployment coordinator), `[chunking]`
for Punkt, and the SFR tokenizer under the shared model cache (`HF_HOME`). A missing extra is a loud refusal (`StructureBackendUnavailable`),
never silent structure-less chunks.

## 3. Design options

**(a) Judge-only control — recommended.** For a sample of stage-A pairs, build the control chunks of
the pair's document (§2), map the window to a control chunk, re-judge with the byte-identical frozen
judge (prompt `alias-judge/2`, C = Qwen3.6-35B-A3B n=5 T0.7 thinking, E = Qwen3.8-27B n=1, Argon
tiebreak, Opus 5 % drift; `main/judge-config.json`), compare the two final labels per pair.
*Answers:* how often, in which direction and in which stratum the verdict depends on the cut.
*Cannot answer:* whether found@10 moves, or whether the false-yes rate would change on the pairs a new
retrieval would surface.

**Mapping rule** (window `W = [start_char, end_char)` of the stage-A chunk; control chunks `K_i` of the
same `doc_id`, same record text verified by offsets):

- *primary, `max-overlap`:* the `K_i` with the largest `|W ∩ K_i|` in characters; ties to the earlier.
  Record window coverage `|W ∩ K_i| / |W|` and chunk purity `|W ∩ K_i| / |K_i|`.
- *secondary, `cover-any`:* every `K_i` with `|W ∩ K_i| > 0` judged separately; the control label is
  *answers* if any is. Run only on pairs whose primary label differs from stage A, to attribute the
  flip to the mapping (evidence fell outside the chosen chunk) rather than to the judge. With hard
  section walls a window may overlap more control chunks than with 512-token packing; cap at 6, count.
- The answer span of a non-gold pair is unknown (the record's quote lives in the gold chunk), so a
  straddle is not detectable directly; `cover-any` is the bound. A document whose record text cannot
  be recovered, or whose stage-A chunks fail `text[start:end] == content`, is excluded and counted.

**(b) Full control arm — later, now unblocked by PR 7.** Re-chunk the four corpora with the §2 spec,
load one marked collection per arm on the non-production tenant, re-retrieve the 14 stage-A legs, re-judge.
*Answers:* found@10 under the new chunking. *Needs:* PR 4 + PR 7 merged (the registry entry carries
the markers; PR 5b would add untitled JATS sections), a live-ops Fable plan and a notice to the deployment coordinator,
≈ 811 k points re-embedded (more chunks if walls are hard), registry entries and load receipts, ≈ 15 k
retrieval calls, a new judged-pair registry of stage A's order (9,205 pairs). Loading through path D
(`ingest_jsonl.py`) or in-process needs no R; through the GoWe workflows it does. Decided by (a).

## 4. The sample (frozen before any label is compared)

- **Pool:** the 9,205 stage-A pairs (`main/judge/pairs.jsonl`), as the spot check's pool (§13.5, P4).
- **Draw:** the spot check's rule with its nominal sizes **tripled**: strata by final label *answers*
  / *not* / *tiebroken* = **150 / 99 / 51 = 300**, arms equal within a stratum (remainder to base, then
  neighbour), the §13.5 shortfall chain, cells filled in the order
  `sha256(261101|alias|spot|judge|<stratum>|<arm>|<pair id>)` (`alias_study_spot.py:237–262`,
  `NOMINAL` `:85`). Because the ordering is the spot check's, **the committed 100-pair spot sheet is
  the first third of every cell**: the owner's 100 pairs are in the sample by construction, verified
  by sha against the spot key; no new seed. The draw is run by the operator; its output is pair ids and
  counts; the plan author stays blind.
- **Precision / power at n = 300** (exact binomial, this plan's calculation): 0 flips ⇒ Wilson 95 %
  upper bound 1.3 % (1.9 % at 200, 3.7 % at 100); a true 5 % yields ≥ 1 flip with probability > 0.999
  and ≥ 3 with 0.999; the one-sided exact test of H0 *flip rate ≤ 1 %* rejects at ≥ 7 flips with power
  0.99 at 5 % (0.94 at 200, 0.74 at 100). Per stratum (150 / 99 / 51) intervals are about twice as
  wide; *tiebroken* resolves nothing below ~10 % and is reported as such.
- The private sample file (`control/sample.jsonl`: pair id, stratum, arm, control chunk id, overlap
  stats) has its sha256 and counts committed in `artifacts/control-sample.json` **before** any judge
  call.

## 5. Metrics (all exploratory, Wilson 95 % intervals)

1. **Flip rate** = share of pairs whose control final label ≠ stage-A final label: overall, by
   direction (*answers→not*, *not→answers*), by stratum, by arm, by stratum × direction; the 2 × 2
   table with κ and Gwet's AC1 (`alias_study_stats.agreement_metrics`, `:1261`).
2. **Mapping attribution:** among flipped pairs, the share recovered by `cover-any`; flip rate by
   window-coverage band (< 0.5, 0.5–0.8, ≥ 0.8) and by control-chunk token band.
3. **Judge-stage detail:** C alone, E alone, escalation share under each chunking.
4. **Owner agreement** (if Q4 = yes): owner–judge agreement on the 100 spot pairs under both
   chunkings; owner self-consistency across the two passages, with n.
5. **Boundary diagnostics** on the 300 windows and their control chunks: share ending in sentence
   punctuation, share starting with a capital/digit, share containing a `## ` line (windows) vs share
   with `section_crossing: true` (control), token count mean / p50 / p90 (SFR), share of control
   chunks that are token-split over-budget sentences, share of documents with one untitled section
   (PDF / unit records / untitled JATS).
6. Unjudged / filtered counts per backend, gateway call counts, served-model check results.

## 6. Costs (rates from prereg §6.5 and the pilot artifact only)

- **Qwen C** (the first of the two Qwen endpoints, 4 sequences): 1.85 s/pair at 4 in flight → 300 pairs ≈ 9 min; **E**
  (the second Qwen endpoint): 0.74 s/pair → ≈ 4 min. Input ≈ 3.3 k chars/pair (system 1.5 k + passage ≈ 1.4 k, less if
  walls are hard, + title, definitions, question) ≈ 1 k tokens; output with thinking ≤ 8,192 cap per
  sample, C × 5. `cover-any` adds ≤ 6 × (flipped pairs), expected tens of pairs.
- **Gateway:** Argon tiebreak 2–2.5 % of pairs (§6.5) → ≈ 8 calls (pilot 0.060/registry pair with
  retries → ≈ 18); Opus drift 5 % → 15 (pilot 0.054 → 16). ≈ 35 calls, ≈ 40 k input tokens,
  `max_tokens` 4,096 / 2,048, ≤ 4 in flight, `fallback_model ""`, one size notice to the gateway coordinator before
  the gateway batch (`preflight` writes it; `label` refuses without `--notice-acknowledged`,
  `glossbench_alias_judge.py:732–763`). Control gateway calls are logged in their own `out_dir`, never
  against the study's Opus cap ledger (§6.5).
- **Owner time:** 100 control passages ≈ 1.5 h; 300 would be 4.5 h — not recommended.
- The Qwen endpoints are shared with stage-B judging: run after stage-B judging or in a gap (Q8).

## 7. Prerequisites, code, review, provenance, artifacts, steps

**Prerequisite gate (new, before anything else):**

- **P1.** PR 4 (markers, `structure_from_params`) and PR 7 (`structure_cuts`) merged on `main`, each
  with its Fable review and the §12 tests passing ("no chunk spans two leaf sections", "absent markers
  reproduce today's spans", `chunk_descriptor` changes with the marker).
- **P2. Chunker check on the study papers**, offline, by committed code (`check` subcommand below) on
  every document of the sampled pairs and on a seeded 1,000-document draw per corpus arm, counts only:
  (i) no chunk holds a fragment of two segments — every chunk lies within one segment or is a union of
  whole sibling segments (`section_crossing: true`, counted; expected 0 unless PR 7 merges for
  `sentence`); (ii) every chunk boundary is a `sentence_spans` boundary or a segment boundary, except
  chunks that are pieces of a single sentence over the 640-token ceiling (counted, with the sentence count);
  (iii) every chunk ≤ the 640-token ceiling in SFR tokens, measured, with the share above 512 reported; (iv) chunks tile each document; (v)
  `text[start:end] == content` for every stage-A chunk of those documents against the record text;
  (vi) `segmenter_version() == "punkt-abbrev-v1"`, adapter `markdown/1`, `[markdown]` extra present.
  *Gate:* (i)–(vi) hold with the exceptions counted; the counts go into `artifacts/control-chunking.json`.
- **P3.** Env: `[markdown]`, `[chunking]`, SFR tokenizer available where the control runs; the
  record text of every sampled document recoverable (base OA-1000 from the source the load receipt
  names; added arms from `corpus/text*/`).

**Code** (one PR, branch from `main` after PR 7, in its own worktree):

- `python/scripts/eval/alias_control_chunks.py`, calling `ragstack.ingestion` only (`Document`,
  `attach_structure`, `make_chunker`/`build_chunker` with the §2 markers, `stamp_documents`,
  `sentence_spans`, `HFTokenCounter`, `experiment_provenance`, `span_fingerprint`): subcommands `check`
  (P2), `chunk` (control chunks + fingerprints for the sampled documents), `draw` (§4; verifies the
  spot sheet is its prefix by sha), `map` (§3; writes `control/chunks-control.jsonl` in the export row
  shape and `control/pairs-seed.jsonl` with `control_of` = the stage-A pair id), `report` (§5; public
  JSON through `alias_study_common.public_problems`, counts only). No segmenter, no regex, no chunk
  logic of its own.
- `alias_study_judge.py`: `build-control --seed-pairs FILE` writing the same registry schema
  (`pairs.jsonl`, `batches.json`, `pairs-manifest.json` with `inputs_sha256`) into a new `out_dir`
  (a `judge` directory under the control run directory); `preflight`, `label`, `finalize` unchanged. Control
  judge config = `main/judge-config.json` with only `out_dir` and `chunk_exports` (the control export
  added) changed; `pinned_problems` still enforces the prereg sha.
- Tests `python/tests/eval/test_alias_control_chunks.py`: P2's invariants on a fixture with titled,
  nested and untitled sections and one over-budget sentence; the mapping is deterministic and the tie
  rule holds; the draw with nominal ×1 reproduces the spot draw byte for byte on a fixture; `report`
  refuses text and host fields; the markers' `chunk_descriptor` is the §2 string.
- **Review:** Fable review before merge (CLAUDE.md), plus an independent argo-agent read of the draw
  and the mapping. The reviewer re-runs the fixture draw and P2 on the fixture.

**Steps, each gated:**

0. P1–P3. *Gate:* the merged PRs and P2's counts committed.
1. Owner answers §8; this plan is committed under `docs/plans/results/alias-study-2026-11/control/`
   as `CONTROL-PLAN.md`, its sha recorded in the addendum. *Gate:* owner's written go.
2. Control PR merged. *Gate:* Fable review, CI green, clean tree (`dirty false`).
3. `draw` + `chunk` + `map` on a clean checkout; commit `artifacts/control-sample.json` (counts,
   sha256 of the private sample, provenance, fingerprints) and `artifacts/control-chunking.json` (P2
   counts, boundary diagnostics on both chunkings). *Gate:* spot-sheet prefix check passes; every
   sampled document verified by offsets; exclusions counted. No label compared.
4. `build-control`, `preflight` per backend, size notice to the gateway coordinator, `label` C → E → Argon → Opus,
   `finalize`. *Gate:* served-model check, ≤ 4 in flight, `fallback_model ""`, budget stop.
5. `cover-any` on flipped pairs (a second small registry, same gates).
6. Owner labels the 100 control counterparts of the spot pairs on a separate blind sheet
   (`alias_study_spot.py` sheet writer, control chunk text, sheet order
   `sha256(261101|alias|control|sheet|<pair id>)`), only after the original spot labels are committed.
7. `report`; commit `artifacts/control-judge.json`; write the addendum (exploratory wording in every
   sentence; what was seen when). *Gate:* `public_problems` clean; no stage-A figure restated.

**Binding rules honoured:** non-production tenant only and no live store at all in (a); nothing stopped by name;
worktree in the designated worktree directory; artifacts carry `experiment_provenance()` and span
fingerprints; labels never cross chunkings by id (control chunk ids are new; the comparison key is the
stage-A `pair_id` via `control_of`, a pairing of judgements, not a label carry); the chunker is
product code at a pinned commit, never a harness copy.

## 8. Open questions for the owner (with recommended answers)

1. **Option (a) only, (b) deferred?** — Recommend (a) now; (b) is unblocked by PR 7 and decided by (a).
2. **Section barriers.** — **Resolved by the owner, 2026-11-04:** the merged #828 path (Markdown
   adapter raw mode + `structure_cuts`), no heading-line stand-in. Remaining consequence, stated in
   §2: untitled JATS sections and PDFs are one section until PR 5b / PR 8.
3. **Sample size 300 (spot rule × 3) or 200?** — Recommend 300: the spot sheet is a prefix, 0 flips
   bounds the rate at 1.3 %, per-stratum intervals stay usable.
4. **Owner labels the 100 control counterparts?** — Recommend yes, 1.5 h, after the original spot
   labels are committed; one rater who has seen both passages, stated in the addendum.
5. **Run `cover-any` on flipped pairs?** — Recommend yes; it separates a mapping miss from judge
   sensitivity at the cost of tens of pairs.
6. **Keep the Opus 5 % drift sample?** — Recommend yes (byte-identical judge configuration); ≈ 15
   calls logged in the control `out_dir`, outside the study's Opus cap ledger.
7. **Addendum, not a deviation?** — Recommend addendum in the record tier, cross-reference in
   `DEVIATIONS.md` only if that file exists; §12's definition is not met.
8. **When?** — Recommend after stage-B judging finishes (shared Qwen endpoints) and after P1–P3.
9. **Tokenizer for the 512 budget?** — Recommend the collection's, `Salesforce/SFR-Embedding-Mistral`;
   Qwen-side token counts reported separately.
10. **Gold chunks?** — Recommend no: stage A judged only non-gold pairs (§6.2).

**Owner answers, 2026-11-05: "all as recommended", except Q4.** The answers reflect what had changed since the plan was written: stage-B judging is finished, PR 4 and PR 7 are merged (#937, #938), the spot labels are committed (#941), and `DEVIATIONS.md` exists (D-1).
- **Q1:** option (a) only. (b) is decided later from (a).
- **Q3:** 300 pairs.
- **Q5:** yes, `cover-any` on flipped pairs.
- **Q6:** keep the Opus 5 % drift sample. If Argo answers with a `provider_notice`/ACCESS DENIED (it did twice in stage B), the run stops; finishing without Opus is then a separate owner decision, reported as such.
- **Q7:** an addendum, plus a one-line cross-reference in `DEVIATIONS.md`.
- **Q8:** the control-script PR starts now. The run waits for P3: `[chunking]` (Punkt) and `[markdown]` installed by the deployment coordinator. The judge environment currently runs the regex segmenter. Every stage-B judge log printed the Punkt-unavailable warning, and the stage-A/B analysis provenance records `segmenter_version: regex-abbrev-v1`.
- **Q9:** the SFR tokenizer.
- **Q10:** no gold chunks.
- **Added question:** LaTeX/markup (#940). The control re-chunks the exact record text stage A used, uncleaned, so that a flip is attributable to the cut alone. Papers with a LaTeX preamble (3.1 % of the corpus) are reported as their own stratum.
- **Q4: replaced (owner, 2026-11-05).** The owner does **not** re-label the 100 control passages. The owner's reasoning: the judge asks whether **one** passage answers the question. Under that question a partial answer is ambiguous. Under synthesis across several chunks, a partial answer is useful, and the next query can go after the missing piece. The spot check's disagreements cluster on partial answers (13 of 19 went judge-*answers*/owner-*not*). They are therefore most likely a disagreement about where the rubric's "core of what is asked" ends, not a chunking artefact. Re-labelling control passages would meet the same fuzzy line.
  - **Instead:** the owner tags the 19 disagreements as *full / partial / no answer / not about completeness*, on a private page (owner-only store). The tags go into the write-up's reading of the §13.5 flag as an exploratory, post-hoc diagnostic. They change no committed label.
  - **Metric §5.4 is dropped** from the control.

## 9. Design note for the next study: what a judged passage measures

**What this study measures** (frozen, unchanged). found@10 is the share of questions for which at least one top-10 chunk, *read alone*, states the core of the answer. The rubric's text: a passage "that states the core of what is asked is 'yes' even if it adds or omits details" (`alias-judge/2`, shown verbatim to the owner on the spot sheet). Each question was written from one passage of one paper, so the measure tests **reach**: can the retriever surface a passage that carries the fact? That fits H1 (does the glossary help the retriever find the passage written with the short form?) and H2 (do related papers displace it?). The write-up describes found@10 this way and does not present it as answer quality.

**What it does not measure.** A RAG system synthesises from several chunks. Under synthesis, a chunk that carries part of the answer is useful, and an iterative or agentic loop can refine the query to find the rest. Single-passage yes/no judging treats such a chunk inconsistently: "the core" is a threshold that the judges and the owner draw in different places (§13.5).

**Proposal for the next pre-registration** (none of it applies to the frozen study):
1. **Graded labels:** *full / partial / none* per (question, passage). This replaces the binary rule, so the threshold becomes data instead of a judgement buried in "the core".
2. **Set-level sufficiency:** one judgement per (question, top-k set): "do these passages together answer the question?", reported as its own measure beside found@10. Optionally, nugget coverage: split the reference answer into nuggets and count the nuggets the set covers.
3. **The chunking interaction:** sentence/section-respecting chunks (the owner's 2026-11-04 decision) matter most under single-passage judging. Under set-level judging, a cut that splits an answer between two retrieved chunks costs less. Reporting both measures shows how much of a chunking effect comes from the single-passage frame.
4. **End-to-end:** if the system under test is iterative, judge the final answer (correctness and attribution), not individual chunks.
5. **Before adopting any of these:** fetch and verify the established literature names and methods, under the study-writeup skill's citation rule. Candidates to check include graded relevance, nugget-based evaluation and "sufficient context" framings. None is cited here yet.

**Owner decision, 2026-11-04: control chunking settings.** sentence/512/0 + `structure_cuts` + `size_tolerance` 0.25 (640 ceiling) + `min_piece` 128, chosen from PR #938's length report on 200 study papers (seed 828, SFR tokens): p10 195, median 464, p90 510, 7.3 % of chunks over 512, 1.8 % under 128, 5.4 % `section_crossing`, none over 640.

## Pre-judge results (committed before any judge call)

Steps 0 and 3 ran on a clean checkout at commit `c9af66cc6c9b90c1cf3d6baddd477a894ccbab7e`
(`dirty: false`, `citable: true` in each file's provenance). Their public outputs are committed byte
for byte under `artifacts/`; they hold counts, settings, model ids and shas only. No label was
compared and no judge was called.

- `artifacts/control-draw.json`: the §4 draw (`draw`). It holds the counts per stratum and arm, the
  spot-sheet prefix check, and the sha256 of the private sample. This is the file §4 and step 3 call
  `control-sample.json`.
- `artifacts/control-chunk.json`: the control chunks of the sampled documents (`chunk`). It holds the
  chunker as applied, the P2 counts and gate, token distribution, and the sha256 of the private chunk
  and document files.
- `artifacts/control-map.json`: the §3 primary `max-overlap` mapping (`map`). It holds coverage and
  token bands, overlap counts, exclusions, and the sha256 of the private seed file.
- `artifacts/control-check.json`: the P2 chunker check (`check`) on the sampled documents and on the
  seeded per-arm draw. It holds counts per group, the gate, and token distributions. Together with
  `control-chunk.json`, this is what §7 and step 3 call `control-chunking.json`.

The sha256 of each file is listed in `README.md` beside this plan.
