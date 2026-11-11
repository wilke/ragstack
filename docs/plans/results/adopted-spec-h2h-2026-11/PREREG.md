# Pre-registration: the adopted chunking spec head-to-head with the OA eval's pick and the server default — DRAFT v5, 2026-11-11

STATUS: **DRAFT, not frozen; freeze = owner says freeze.** Nothing in §7 runs before the freeze of §9. The
owner answered the §10 questions on 2026-11-11 (recorded there). This is a record-tier file: once frozen it is never edited; changes go to
`DEVIATIONS.md` (§9.3). v2–v4 address the three 2026-11-11 independent reviews (11 + 9 + 4 findings, all
applied). v5 records the sentinel dry run of 2026-11-11 (§2), makes the worker-stderr gate enforceable on
the main shards (§2, §6: a study copy of the workflow text stages every ingest task's full stderr out as
a file), corrects the create body (§7) and names the committed tooling (§9.4); the open freeze items are
listed in `README.md`.

Private material (questions, quotes, passages, per-question rows, configs with hosts and paths) stays in the
private experiments repository, `oa-chunking-eval-2026-10/`. This file carries aggregates, file names and
hashes only; a number from a private file is marked "(private, `<file>`)".

## 1. Question and hypotheses

**The three candidates for the re-ingest (#811).**

| name | chunking | status |
|---|---|---|
| **ft512o64** | `fixed_token` 512 / 64 | the server default today (`Settings`) |
| **ft512o64s** | `fixed_token` 512 / 64 + `structure_cuts sections/1` | the OA chunking eval's pick under its frozen rule plus Addendum C: +3.49 hit@5 [+2.11, +4.86] over ft512o64 in setting (a), Holm k = 3 (`../oa-chunking-eval-2026-10/POSTHOC-HYPOTHESES-2026-11-06.md`) |
| **S** | `sentence`, 512 with `size_unit tokens`, overlap 0, `size_tolerance` 0.25 (ceiling 640), `min_piece` 128, `structure_cuts sections/1` | the adopted production default (`docs/CHUNKING.md` § *The adopted default*), not yet deployed |

**Why this test.** The 2026-11-11 verification and red-team review found that S was never compared directly
with either of the other two. What exists:
- The OA eval's plain-sentence section arm, **sent512s** (`sentence` 512 tokens + sections, *no* tolerance,
  *no* `min_piece`), tied ft512o64s in setting (a): −0.44 [−1.38, +0.49] (private,
  `sections-2026-11/RESULTS.md`; the public table gives 82.06 vs 82.51). Under Addendum C's cost ladder a tie
  went to the cheaper ft512o64s.
- The reach study (`../reach-rechunk-2026-11/REPORT.md`) compared S with `fixed_token/512/0` only, on another
  question set and metric (quote@10), and found +1.1 points.
- S's two extra knobs (`size_tolerance`, `min_piece`) have no retrieval evidence of their own.
- Every tenant but `dev` runs images without `nltk`, where `sentence` uses the regex segmenter, while every
  study ran Punkt (#815).

**Incumbent, fixed now.** The incumbent is **ft512o64s**: it is the OA eval's decision on this question set,
and this run reuses its collection. S is the challenger. ft512o64 is retrieved as the baseline the frozen
harness needs and as the drift gate below; it is not a candidate the sequence can select.

**Hypotheses, setting (a), hit@5, decision set n = 2,035:**
- **Gate G1 (drift, not a hypothesis):** lower(ft512o64s − ft512o64) > 0 on this run's server version. Same
  collections and questions as Addendum C, so this recomputes a known result (+3.49 [+2.11, +4.86]); a
  failure signals server or embedding drift and makes the run GATE-NOT-EVALUABLE, not evidence against
  ft512o64s.
- **H1 (non-inferiority):** S − ft512o64s > −2.0 points, the frozen margin M_ni (Addendum B).
- **H2 (superiority):** S − ft512o64s > 0, tested only if H1 holds.
- **H3 (segmenter):** S-regex − S > −2.0 points.

The decision maps to {deploy S, deploy ft512o64s}; GATE-NOT-EVALUABLE leaves the server default (ft512o64)
as it is until a re-run, which is not a decision for ft512o64. H3 does not choose; it decides whether S may be
deployed on a worker whose segmenter is not asserted (§4.3).

**Declared exposure.** The analyst has seen every reused arm's per-question result on this question set,
including sent512s ≈ ft512o64s, so a tie at H2 is the known prior. The cost order (§4.2) decides what a tie
means, and the owner rules on it knowing that prior; for that reason the decision under the other order is
always printed beside the decision (§4.5). This file fixes the rule before S's numbers exist; it does not make
the question set unseen.

**What this test can and cannot show** (§3, §8). It can show whether S reaches the gold quote in the top 5
as often as the two fixed-token candidates on the same questions, corpus, embedder and reranker, and whether
the regex segmenter changes that. It cannot see answers that cross a section boundary (0 of 2,035 quotes do)
or run longer than the quotes (median 119 characters), nor separate `size_tolerance` from `min_piece`.

## 2. Arms

| arm | chunking (all: SFR-Embedding-Mistral 4096-d, same reranker, same 241,930 extracted records with `structure`) | collection | retrieval in this run |
|---|---|---|---|
| ft512o64 | `fixed_token` 512/64 | exists (main run, dev, `oa-eval-*`) | re-retrieved (baseline, gate G1) |
| ft512o64s | `fixed_token` 512/64, `structure_cuts sections/1` | exists (Addendum C, dev) | re-retrieved (incumbent) |
| **S** | `sentence` 512 tokens, overlap 0, `size_tolerance` 0.25, `min_piece` 128, `structure_cuts sections/1`, Punkt | **to build** | new (challenger) |
| **S-regex** | the same spec, regex segmenter (`regex-abbrev-v1`) | **to build** | new (H3 only) |
| sent512s (secondary) | `sentence` 512 tokens, overlap 0, `structure_cuts sections/1` | exists (Addendum C, dev) | re-retrieved (owner Q3) |

sent512s is a secondary arm only: S − sent512s isolates what `size_tolerance` + `min_piece` add; it enters no
decision step. Every arm holds the 5 sentinel articles, as every OA eval arm does
(`../oa-chunking-eval-2026-10/SENTINEL-2026-11-04.md`; the count rule is in §6).

**Input text.** The Addendum C extract (241,930 records with the `structure` key; with that key removed,
byte-identical to the main-run input; private, `sections-2026-11/RESULTS.md`). The new arms ingest the same
shard files, pinned by sha256 at the freeze (§9.1), through `ingest-bulk.cwl`.

**Images.** A collection with `structure_cuts` is refused — at create (400), at dispatch (422) and by
`gowe_batch_ingest.py` — unless the workflow text names a **stamped** tools image at v1.6.23 or later
(`tool_image.names_marker_capable_image`; `docs/CHUNKING.md` § *Release order*). An unstamped image is not
"skipped", it is refused. So both new arms are built from stamped images of **one commit** (the tools tag the
owner names, ≥ v1.6.24, which carries `structure_cuts` and the `sentence` tolerance):
- **S:** the release image of that tag: `ragstack-tools-v1.6.24-b1.sif` (build 1, sha256 `ecf81440…`,
  commit `5d65bead…`; the image dev's registered workflows name).
- **S-regex:** a second stamped build of the same tag with its own build number:
  `ragstack-tools-v1.6.24-b2.sif` (build 2, sha256 `413f6ec3…`, same commit), built on 2026-11-11 from
  `ragstack-tools-nonltk.def` beside this file (`ragstack-tools.def` at v1.6.24 with `chunking` removed from
  the extras line, a build-time assertion that `python -c "import nltk"` **fails** and that
  `segmenter_version()` is `regex-abbrev-v1`, and one extra label `org.ragstack.variant no-nltk`); placed in
  the dev worker group's image directory by management, never in the release store. There is **no non-image
  fallback**: outside an image `tool_provenance` writes `image_commit` as null, which fails the gates below,
  and `ingest_shard.py` alone is not the full ingest. If the b2 image cannot be dispatched in the real run,
  the S-regex arm is dropped and H3 is recorded as *not run*. Gate: every S and S-regex receipt carries the
  same `image_commit`; S receipts carry `image_build` `1`, S-regex receipts `2`.

**Workflow text.** Both new arms are submitted directly (`gowe submit`, as the Addendum C arms were) on
**one study copy** of `cwl/ingest-bulk.cwl`, `ingest-bulk-h2h.cwl` beside this file: the v1.6.25 text (dev's
registered text, sha256 `0f2bc341…`) with one functional addition — the `ingest_shard` tool declares
`stderr: <shard>.ingest-stderr.txt` and surfaces it as a `File` output, gathered into a workflow output
`ingest_stderr`. GoWe tees a task's stderr into that file **in full** and into the 256 KB tail it keeps in its
database for the logs endpoint (GoWe v0.26.1, confirmed by the GoWe session; `maxLogCapture` in the worker
source). The tail alone could lose the once-per-process warning on a 16,000-document shard (it is written
early), so the §6 stderr gate on the 16 main shards reads the staged-out file of every ingest task; the
sentinel shard is additionally read through the logs endpoint (the dry run's proof) and the two counts must
agree. The S text is the committed file (its six image-name lines name the b1 image); the S-regex text is the
same file with each of those six lines rewritten to the b2 name — the **only** difference between the two
arms' texts. Both sha256 are pinned in `frozen-inputs.json` (§9.1). Consequence, stated plainly: **S does not
run dev's registered workflow id**; the registered text and the study text differ in the stderr lines only,
and the create gate (400) and `names_marker_capable_image` on the submitted text still apply (the dispatch
gate, 422, is the API's and a direct submit does not pass through it). The study text has been checked by
GoWe's parser semantics in source (the `stderr` expression is evaluated per shard; a `type: stderr` output
collects it) but **has not yet run on GoWe**: a sentinel-shard submission of the study text for both arms,
on scratch collections as in the dry run, is a pre-freeze item (`README.md`); it must reproduce the dry run's
receipts (168 chunks, identical ids) and stage out a stderr file holding 0 / ≥ 1 warning lines.

**Dry run (2026-11-11).** Done on `dev` (server v1.6.25, `67e53fd`), on two scratch collections created with
the exact spec of §7 step 2 (registry `spec_hash` `29236085` on both), with the sentinel shard ingested into
each through dev's own v1.6.25 text (S, the registered workflow id) and the same text stamped with the b2
receipt (S-regex; image names only, sha256 `5f7a3a42…`). Evidence that the gates work, both ways:

- receipts: 40 documents, 0 failed, **168 chunks** on each arm; `provenance.image_commit` equal
  (`5d65bead…`), `image_version v1.6.24`, `image_build` `1` vs `2`, `segmenter_version` `punkt-abbrev-v1` vs
  `regex-abbrev-v1`; `chunking` = `{v 1, method sentence, chunk_size 512, size_tolerance 0.25, min_piece 128,
  ceiling 640, structure_cuts sections/1, window_budget 4080}` on both (the full §6 key list, plus
  `window_budget`, which is reported and not gated);
- worker stderr through the GoWe logs endpoint: **0 vs 1** lines containing `NLTK Punkt is unavailable`
  (1,501 vs 1,851 bytes, far under the 256 KB tail); the warning reaches stderr through Python's last-resort
  handler as the code reading predicted; the task's `exit_code` 0;
- stores: Qdrant = Elasticsearch = receipt `n_chunks` = distinct `chunk_ids` = 168 on both;
- `GET /v1/collections`: identical `chunk_params` on the two entries (`size_unit tokens`, `size_tolerance
  0.25`, `min_piece 128`, `structure_cuts sections/1`), method `sentence`, size 512, overlap 0, embedding
  `Salesforce/SFR-Embedding-Mistral` 4096-d `openai` resolved from dev's config default (§7 step 2);
- cleanup: a plain `DELETE` is refused (409) when no other registry entry claims the store; both were purged
  with `?purge=true` (registry, vectors, text index, manifest), the listing is clean and both physical stores
  answer 404.

**Observation, not a prediction:** on these 40 documents (5 articles, 4 tables, 31 figures) the S and S-regex
chunk ids — `uuid5(doc_id:start:end)` — were **identical, 168 of 168**: with the 640-token ceiling, the
`min_piece` merge and the section cuts, the two segmenters produced the same chunk boundaries on every
sentinel document. What that implies for H3, honestly: if it holds across the 241,930 records, S-regex and S
are the same collection up to embedding noise, the discordance of S-regex − S is near zero rather than the
0.02 reference row of §5, the interval collapses around 0, and "non-inferior at 2.0 points" is then
trivially true and uninformative; the descriptive chunk-id overlap of §4.3 carries the finding
("the segmenter does not move a boundary under this spec on this corpus"), and `power.py` prints the rows at
π = 0.005 and 0.001 so the collapse is visible. If it does not hold corpus-wide (long articles have sentence
forms the 40 documents do not), H3 is the designed test. The dry run cannot tell which; the real run's
overlap figure will. Forty documents, most of them tables and figures, are not a sample of the corpus.

**How the segmenter is selected, per call.** `chunkers.sentence_spans` calls `_punkt_sentence_spans`, which
imports `nltk` inside a `try` and returns `None` on `ImportError`, `LookupError` **or any other exception**,
and it does so **per call, per document**; the caller then runs `_fallback_sentence_spans` (the regex backend)
and logs one warning per process ("NLTK Punkt is unavailable"). There is no switch, env var or parameter.
What is recorded, `chunkers.segmenter_version()`, is a **single cached probe** per process, written as
`provenance.segmenter_version` in every receipt (`punkt-abbrev-v1` / `regex-abbrev-v1`). So a receipt saying
`punkt-abbrev-v1` proves the process *could* import `nltk`, not that every document was split by Punkt; the
warning is not stored in receipts. Two gates follow (§6): the receipt value per arm, **and** the captured
stderr of every S worker holding zero "NLTK Punkt is unavailable" lines (every S-regex worker: at least one).
A pre-#816 image (tools v1.6.13, the one on every tenant but `dev`) cannot build S at all; S-regex models a
new worker without `nltk`, for example a `--runtime none` worker on a host environment, and that is the
deployment path it tests.

**Version drift, controlled by re-retrieval.** The main run retrieved on server v1.6.22 and Addendum C on
v1.6.25 (its `drift/` check found the top-5 set identical for 2,434–2,437 of 2,437 questions across the two;
private, `sections-2026-11/RESULTS.md`). This run does not rely on that: **every arm, reused or new, is
retrieved afresh in every setting on one server version** (`GET /v1/version` stored in every row; gate: one
`git_sha` across all rows). The old per-question files are used for no test; the share of questions whose
top-5 set equals the old run's is a descriptive drift figure per reused arm. Latest tag at drafting v1.6.26;
the version used is whatever `dev` runs at the freeze, named in `README.md` then.

**Embedding and reranker drift.** The reused arms were embedded on 2026-11-04/06; S and S-regex are embedded
at run time, and `git_sha` covers neither the embedding endpoint nor the reranker build. Recorded at the
freeze and at the run, for both: the embedding model id and the serving build reported by the endpoint, and
the reranker model id and image. Gate: a pinned sample of 200 ft512o64s chunk texts (seeded draw from the
Addendum C export) is re-embedded through the run-time endpoint, and the cosine similarity to the stored
vectors is ≥ 0.999 for every one; a lower value is GATE-NOT-EVALUABLE for embedding drift (operator default;
owner Q5 may move the threshold).

## 3. Questions, metric, settings

- **Questions:** the OA eval's frozen decision set, n = 2,035 (`clean_excl_noise`: 2,437 selected, minus
  ambiguous-gold and within-record repeats, minus the 300-question noise subset; Addendum B item 3). The
  sensitivity set (n = 2,335, noise subset included) is scored beside it and is not the decision.
- **Strata:** journal family (8) and length band (3), **separately**, k = 11 for the guard; on the decision
  set the smallest stratum has 132 questions (`multidisciplinary`; private, `settings/a-hybrid-rerank/score.json`;
  the plan's 165 was the planned figure), so every stratum clears the n ≥ 30 floor and k = 11. Journal ×
  length cells are reported, not tested.
- **Known weakness, declared.** The set has been used twice (main run, Addendum C) and every reused arm's
  per-question result has been seen. The passages were cut inside one section, so **no gold quote crosses a
  section boundary**, and the quotes are short, sentence-shaped single spans (median 119 characters, p90 214).
  Section arms get no structural advantage on the metric, and the test is blind to what section cuts or the
  640-token ceiling do to cross-section or multi-span answers. **A cross-section / multi-span supplementary
  set is recommended as a separate pre-registered study, not designed here** (owner Q4): it needs new
  passage sampling (passages that straddle a heading, multi-sentence quotes longer than the 64-token overlap),
  new writer calls, and its own gold audit; none of that reuses this run.
- **Passages were cut by a sentence segmenter.** The passage step grew passages from
  `chunkers.sentence_spans` boundaries and recorded the backend it ran in the `segmenter` field of
  `passages.summary.json` (private; the file is in the freeze set). Quote edges therefore follow that
  segmenter's sentence boundaries, which favours the arm using the same backend in H3: if the field reads
  `punkt-abbrev-v1` the bias favours S, if `regex-abbrev-v1` it favours S-regex. The value is copied into
  `README.md` at the freeze and the direction stated in H3's sentence.
- **Clustering:** none. Each decision-set question comes from a different eval article (one passage per
  article), so questions are the exchangeable units and the paired bootstrap over questions is the test; no
  paper-level cluster adjustment applies.
- **Primary metric:** hit@5 — the union of the top-5 chunks from the gold record covers ≥ 90 % of the gold
  quote's characters (verified offsets; Addendum A item 2). Secondary: hit@1, hit@10, strict single-chunk
  containment, context tokens at k = 5 (ctx@5, chars/4 as in the frozen configs, labelled estimated;
  owner Q6 offers the SFR tokenizer).
- **Settings:** (a) hybrid + reranker, graph off — **primary, the decision**; (d) hybrid without reranker,
  (b) vector only, (c) BM25 only — secondary, reported with any disagreement with (a) stated. Each request
  sets `rerank`, `use_graph`, `retrieval_mode` explicitly (the harness refuses otherwise), `top_k` 10.
- **ctx@5 rule, adapted from the frozen one:** the frozen rule raises the threshold of the arm-vs-baseline
  test (S − ft512o64); here it is applied to the decision contrast: if ctx@5(S) / ctx@5(ft512o64) > 1.5,
  H1 requires lower(S − ft512o64s) ≥ +2.0 (the next class's margin, M_mid) instead of ≥ −2.0. S is expected
  well under 1× (sent512s was 0.82×; private, `settings/a-hybrid-rerank/score.json`), so the rule is kept
  for form.
- **No LLM is called.** Questions and gold exist; hit@5 is programmatic; no grader. The only model work is
  embedding at ingest and the cross-encoder at retrieval. No gateway call, no notice needed.

## 4. Decision rule (fixed before any S or S-regex retrieval)

All intervals: paired bootstrap over questions, 10,000 resamples, seeded, two-sided 95 % (one-sided
α = 0.025 per step), exactly the harness's `stats.paired_diff`. "lower" = the interval's lower bound, in
hit@5 points, setting (a), decision set.

### 4.1 The sequence

| step | test | passes → | fails → |
|---|---|---|---|
| **G** | the gates of §6, G1 among them | H1 | GATE-NOT-EVALUABLE |
| **H1** | lower(S − ft512o64s) ≥ −2.0 (≥ +2.0 under the adapted ctx@5 rule of §3), **and** the stratum guard: no stratum with n ≥ 30 has point < −2.0 and a (1 − 0.05/11) paired-bootstrap upper bound < 0 | H2 | **decision = ft512o64s**; outcome *S not shown non-inferior* |
| **H2** | lower(S − ft512o64s) > 0 | **decision = S** | **Order A: decision = ft512o64s** (the cheaper arm). **Order B: decision = S, unless lower(ft512o64s − S) > 0, then ft512o64s** (the frozen ladder's step from the cheaper S to the costlier ft512o64s). Outcome *tie* |

H1 then H2 on one contrast is non-inferiority followed by superiority: a closed sequence on one difference,
which needs no multiplicity correction. The Order B branch is the frozen paired ladder itself (a costlier
candidate replaces a cheaper incumbent only on a lower bound > 0), so §4.1 and the harness agree under either
order. Nothing else is tested for the decision; the secondary contrasts (§4.4) carry their own Holm family
and decide nothing.

**Implementation, two configs.** The frozen harness's `decide` treats every non-baseline arm as a challenger
(Holm across them) and admits every passing challenger to the ladder, and `cost_rank` must be an integer. So:
- a **decision config** with exactly three arms — ft512o64 (baseline, `cost_rank` 1), ft512o64s and S with
  integer ranks per §4.2 — `tie_break: paired_ladder`, `stratum_correction: bonferroni`, margins from the
  frozen `noise.json`, **its own `out_dir`**, run through both `score` and `decide`;
- a **reporting config** with all five arms, its own `out_dir`, run through `score` only.
`score` keeps only questions with a usable ok row in **every** arm of its config, so a failed S-regex or
sent512s row would shrink the five-arm paired set; the three-arm output cannot be touched by those arms.
The frozen `noise.json` is placed in both `out_dir`s so `decide`'s preflight passes. The §9.4 script computes
G1, H1 and H2 (D, D1, E) from the **three-arm** `per_question.jsonl` and **its output is the decision**. The
harness's `decision.json` from the decision config is a **cross-check of estimates, not of the choice**, and
only of quantities it always writes: (i) D1 must equal `arms.ft512o64s.{point, lo, hi}` to 4 decimals (the
same `paired_diff` on the same rows, iterations and seed); (ii) **every step present** in `ladder.steps` is
recomputed by the script for that pair and must match to 4 decimals. Either mismatch is GATE-NOT-EVALUABLE.
The ladder walks `[baseline] + passing challengers` by `cost_rank`, each step against the *current*
incumbent, so which pairs appear depends on the order and on `decide`'s own class test and guard (run on
S − ft512o64, not S − ft512o64s): a ±D step is checked only when present, and a missing step is **reported,
not a gate**. `decide`'s pass/fail and ladder choice are reported and decide nothing. S-regex and sent512s
are in no decision config.

### 4.2 Cost order and tie-break (the owner rules; no default is recommended)

A tie at H2 goes to the cheaper arm, and the two written rules rank S and ft512o64s differently:

- **Order A — Addendum C's convention.** Each section arm sits just after its parent: ft512o64 (2) <
  ft512o64s (2.5) < sent512s (3.5) (private, `sections-2026-11/ADDENDUM-C.md`), and Addendum B's
  owner-confirmed ranks already put sentence 512 above 512/64. S is sent512s plus a merge pass, so **S ranks after ft512o64s**; a tie deploys **ft512o64s**.
  Integer ranks for the decision config: ft512o64 1, ft512o64s 2, S 3.
- **Order B — Addendum B's within-class rule.** Within a class, rank by stored chunks / ingest compute. By
  stored chunks sent512s was cheaper than ft512o64s (850,119 vs 859,087; private,
  `sections-2026-11/RESULTS.md`), S merges small pieces under a 640 ceiling so it should store fewer still,
  and no-overlap arms embed about 12 % fewer tokens; sentence segmentation and the merge pass are extra
  ingest compute. Under this reading **S ranks before ft512o64s**; a tie deploys **S**. Integer ranks:
  ft512o64 1, S 2, ft512o64s 3.

**Owner ruling, 2026-11-11 (Q1): Order B.** The decision config's integer ranks are ft512o64 1, S 2,
ft512o64s 3, and a tie at H2 deploys S unless the frozen ladder finds ft512o64s superior to S. The ruling is
recorded in `README.md` and in the decision config. Stored chunk counts, embedded tokens and ctx@5 are reported for every arm and do not move the rank
after the fact. Whatever the order, the §4.5 sentence always states what the decision would be under the
other one.

### 4.3 The segmenter contrast (H3) and what blocks deployment

- lower(S-regex − S) ≥ −2.0 **and** the stratum guard holds (k = 11, Bonferroni, on S-regex − S, the same
  parameters as H1) → the segmenter is not a deployment blocker: S may be deployed on any worker (its
  receipts still record the backend).
- lower(S-regex − S) < −2.0, or a stratum fails, **or H3 is GATE-NOT-EVALUABLE** (an S-regex gate of §6) →
  **S is deployable only where the Punkt backend is asserted at ingest** (#815's assert-the-backend rule,
  receipts `punkt-abbrev-v1` and clean worker stderr); the re-ingest must not run on a worker without
  `nltk`. This is a condition on the decision *S*, not a change of decision.
- Descriptive beside it: S-regex − ft512o64s; the share of S chunk ids (uuid5 of `doc_id:start:end`) also
  present in S-regex, from the two arms' shard receipts (`chunk_ids`; the exports give the same set; no
  retrieval needed), which says how many boundaries the segmenters actually move on this corpus — on the
  40 sentinel documents of the dry run it was 100 % (§2); the count of fallback warnings in each arm's
  worker stderr.

### 4.4 Secondary contrasts (reported, Holm across the four, not decisions)

C1 S − sent512s (what tolerance + `min_piece` add); C2 S − ft512o64 (S against the server default); C3
S-regex − S; C4 ft512o64s − ft512o64. **Holm across {C1, C2, C3, C4} within each setting**, one family per
setting: in (a) the family is C1 and C2 only (C3 is H3 and C4 is G1 there), in (d), (b), (c) all four. The
sensitivity set and the per-stratum figures are unadjusted and descriptive.

### 4.5 Wording of the decision sentence

One line each, filled by the §9.4 script; `RESULTS.md` carries it verbatim. D [lo, hi] = S − ft512o64s and
its interval, π the discordance, D1 [lo1, hi1] = ft512o64s − ft512o64 (gate G1), E [loE, hiE] =
ft512o64s − S, O the frozen cost order. Every row ends with the clause **"Under cost order <the other
order> the decision would be <X>"**, where X is the script's result of applying §4.1 to the same numbers
under the other order — computed, never fixed text. The "…" stands for the counts of the first row.

| outcome | sentence |
|---|---|
| **S** (H2 holds) | "Deploy S: in setting (a), hit@5 was S% under S against I% under ft512o64s (n = 2,035); S − ft512o64s = D [lo, hi], discordance π; non-inferiority at 2.0 points holds and S is superior (lower bound > 0). Gate G1: ft512o64s − ft512o64 = D1 [lo1, hi1]. Under …" |
| **ft512o64s** (tie, Order A) | "Deploy ft512o64s: … S is non-inferior at 2.0 points but not shown superior; the tie goes to the cheaper arm under cost order A (ft512o64s − S = E [loE, hiE]). Under …" |
| **S** (tie, Order B) | "Deploy S: … S is non-inferior at 2.0 points but not shown superior, and ft512o64s is not shown superior to S (ft512o64s − S = E [loE, hiE]); the tie goes to the cheaper arm under cost order B. Under …" |
| **ft512o64s** (ladder, Order B) | "Deploy ft512o64s: … S is non-inferior at 2.0 points but not shown superior, and ft512o64s is superior to S (ft512o64s − S = E [loE, hiE], lower bound > 0; frozen ladder). Under …" |
| **ft512o64s** (S not NI, margin) | "Deploy ft512o64s: … a loss of 2.0 points or more under S is not ruled out (this is not evidence that S is worse)<; and S failed the stratum guard (…)>. Under …" *Never* "S is inferior". |
| **ft512o64s** (ctx rule) | "Deploy ft512o64s: … S returns R× the baseline's context at k = 5 (> 1.5) and did not clear +2.0 points (context-token rule)<; and S failed the stratum guard (…)>. Under …" |
| **ft512o64s** (stratum guard only) | "Deploy ft512o64s: … S is within 2.0 points of ft512o64s on the pooled set but failed the stratum guard (<dim = value>, point P, upper bound U at 1 − 0.05/11). Under …" |
| **GATE-NOT-EVALUABLE** | "GATE-NOT-EVALUABLE: <each gate with its count>. The contrasts were computed (…) and decide nothing; the server default stays as it is." |

**Exactly one row per path, both orders (self-check).** G fails → row 8. H1 fails → the margin row if the
pooled bound misses its threshold (the ctx row when the threshold was raised), with any stratum failure
appended in the angle brackets; the stratum row only when the pooled bound passes and a stratum fails. H1
holds and H2 holds → row 1 (both orders). H1 holds and H2 fails → Order A: row 2; Order B: row 4 if
loE > 0, else row 3. No other path exists.

H3's sentence is appended: "Segmenter: S-regex − S = D3 [lo3, hi3]; S may be deployed on any worker / only
where Punkt is asserted (reason: margin / stratum / not evaluable: <gate>)."

## 5. Power (planning figures; no new data)

Inputs: the frozen margin 2.0; the observed k = 5 discordance rates of the Addendum C scores on the decision
set (private, `settings/<setting>/score.json`, `sets.clean_excl_noise.discordance`). Normal approximation
for a paired binary difference: SE(points) = 100·√((π − d²)/n), n = 2,035, a bound at z = 1.96.

Discordance, k = 5, of the proxies for this run's contrasts (private, the files above):

| pair (proxy for) | π (a) | π (d) | π (b) | π (c) |
|---|---|---|---|---|
| sent512s vs ft512o64s (S − ft512o64s, H1–H2) | 0.047 | 0.081 | 0.103 | 0.048 |
| ft512o64s vs ft512o64 (gate G1) | 0.103 | 0.172 | 0.224 | 0.106 |
| sent512s vs ft512o64 (C2) | 0.109 | 0.174 | 0.223 | 0.112 |
| sent512s vs sent512sh (a text-only perturbation; a reference point for S-regex − S, not a proxy) | 0.020 | 0.044 | 0.076 | 0.000 |

Half-width of the 95 % paired interval at n = 2,035: 0.61 (π 0.02), 0.97 (0.05), 1.37 (0.10), 1.68 (0.15).

Power at n = 2,035, setting (a):

| test | π | true −1.0 | true −0.5 | true 0 | true +1.5 | true +2.0 | true +2.5 | true +3.0 |
|---|---|---|---|---|---|---|---|---|
| H1 NI at 2.0 | 0.05 | 0.52 | 0.86 | 0.98 | — | — | — | — |
| H1 NI at 2.0 | 0.10 | 0.30 | 0.57 | 0.81 | — | — | — | — |
| H2 superiority | 0.05 | — | — | 0.025 | 0.86 | 0.98 | 1.00 | 1.00 |
| G1 (ft512o64s − ft512o64 > 0) | 0.10 | — | — | 0.025 | 0.57 | 0.82 | 0.95 | 0.99 |
| H3 NI at 2.0 | 0.02 | 0.89 | 1.00 | 1.00 | — | — | — | — |

Reading: at the ~5 % discordance a sentence-section arm shows against ft512o64s, S passes NI with power 0.98
if truly equal and 0.52 if truly 1 point worse; the minimal superiority detectable with power 0.80 is 1.4
points (1.6 at 0.90). G1 is expected to pass (Addendum C: +3.49). Normal-approximation planning numbers; the
run's intervals come from the bootstrap; the script is committed as `power.py` beside this file with its
`power.json` (§9.1). The H3 row assumes π = 0.02; the dry run's identical chunk ids (§2) say π may be far
lower, and `power.py` also prints π = 0.005 and 0.001: there the half-width is 0.31 and 0.14 points and NI
at 2.0 is passed at any plausible true difference — a collapse of the interval, not power.

## 6. Gates, validity checks, deviations

Any gate below makes the affected step **GATE-NOT-EVALUABLE**: the numbers are still computed and printed in
full, and the sentence says *decides nothing*. A gate on S, ft512o64s or ft512o64 voids the decision; a gate
on S-regex voids H3 (which then reads *S only where Punkt is asserted*, §4.3); a gate on sent512s voids C1.

- **G1 (drift).** lower(ft512o64s − ft512o64) > 0 in setting (a), decision set.
- **Collections (receipts = store counts).** Per new arm: every shard receipt complete, 0 failed documents,
  241,930 documents in the main shards. The sentinel shard is upserted first; counted from the pinned shard
  files (`frozen-inputs.json`): **0** sentinel `doc_id`s (`uuid5(NAMESPACE_URL, path)`) are also in the
  main shards, all 40 are sentinel-only, so the expected store count = Σ main-shard `n_chunks` + the
  sentinel `n_chunks`, which the dry run fixed at **168** for both arms and the real run's sentinel ingest
  must reproduce exactly (same image, same spec, deterministic ids). Where receipts carry `chunk_ids` (they
  do on a direct submit: per shard and per document), the count of **distinct** ids replaces the sum
  (tenant-level GoWe jobs report `chunk_ids=[]`, STATUS.md). Qdrant = Elasticsearch = that number, exactly.
  Reused arms: the live census equals the census recorded at the freeze.
- **Spec, on the receipt's `chunking` object** (written by `ingest_shard.applied_chunking` from
  `sentence_tolerance_provenance` plus the applied markers). Every S and S-regex receipt has **all** of:
  `method "sentence"`, `v 1` (`SENTENCE_TOLERANCE_PROVENANCE_VERSION`; the v2 counter belongs to the
  semantic chunkers), `chunk_size 512`, `size_tolerance 0.25`, `min_piece 128`, `ceiling 640`,
  `structure_cuts "sections/1"`. A receipt without the object, or with any key missing, fails the gate
  (an older worker writes none and chunks strictly without error). `window_budget` (4080 on the dry run) is
  recorded on the receipt and reported, not gated. `GET /v1/collections` shows the same `chunk_params` for
  both (`size_unit tokens`, `size_tolerance 0.25`, `min_piece 128`, `structure_cuts sections/1`).
- **Segmenter actually used, per arm.** Every S receipt `provenance.segmenter_version` = `punkt-abbrev-v1`
  and every S worker's captured stderr holds zero "NLTK Punkt is unavailable" lines; every S-regex receipt
  = `regex-abbrev-v1` and every S-regex worker's stderr holds at least one; no receipt without the key.
  **Source of the stderr:** for every shard, sentinel included, the staged-out `<shard>.ingest-stderr.txt`
  of the ingest task (the full stream; a missing file fails the gate for that arm); for the sentinel shard
  also the GoWe logs endpoint's tail, whose line count must equal the file's. A logs-endpoint capture at or
  over 256 KB is not evidence either way and is never used alone. Every S and S-regex receipt carries the
  same `image_commit`; S receipts carry `image_build 1`, S-regex receipts `image_build 2` (the no-`nltk`
  build). `tool_image`, `tool_image_digest` and `workflow_id` are null on a direct submit (as on every
  Addendum C receipt) and are not gated; the images are tied to the receipts by `image_commit` +
  `image_build` and to `frozen-inputs.json` by their digests.
- **Same input.** The shard files' sha256 equal the §9.1 pins.
- **Retrieval.** Per setting: 2,437 questions × 5 arms, no missing or duplicate rows; one
  `server_version.git_sha` across **all four settings**; request fields (`rerank`, `use_graph`,
  `retrieval_mode`, `top_k`) equal the frozen Addendum C config for that setting. **Failures:** the harness's
  own 503 waits apply; beyond them a failed call may be re-run by resuming the checkpointed `retrieve` at
  most 3 times per setting, each resume logged; a row still failed after that is a gate, scoped **per arm
  and setting**: in (a), a failed row on S, ft512o64s or ft512o64 voids the decision, on S-regex voids H3,
  on sent512s voids C1; in (d), (b) or (c) it voids only the contrasts of that setting that use the arm.
- **Embedding drift.** The 200-chunk re-embed check of §2: cosine ≥ 0.999 for every chunk; the embedding
  and reranker model ids recorded at the run equal those recorded at the freeze.
- **Hit quality.** The harness's own refusals: `max_unlocated_share` 0.01 per arm, no question without
  record text.
- **Decision set.** In the **three-arm** `score` output: n = 2,035 and n = 2,335 exactly, by question id,
  equal to the Addendum C sets (sha256 of the sorted id list pinned at the freeze). The five-arm output's
  paired n is reported; a shortfall there voids only the contrasts that use the arm with the missing rows
  (H3 for S-regex, C1 for sent512s), never the decision.
- **Cross-check.** `decision.json` `arms.ft512o64s.{point, lo, hi}` equals D1, and every step present in
  `ladder.steps` equals the script's recomputation of that pair (4 decimals); a missing step is reported,
  not a gate; the harness's choice is reported, not compared.
- **Provenance.** Harness run from the tagged commit in a clean tree (`experiment_provenance()` in
  `score.json` / `decision.json`: `dirty: false`, `untracked: 0`, `citable: true`); server `/v1/version`,
  tools images and endpoint versions per arm in `README.md`.

**Deviations (§9.3).** After the freeze any change is a dated entry in `DEVIATIONS.md`: what, why, when, what
had been seen, which result it touches. What counts, at least: the margin, the sequence, the cost order and
tie-break, the arms and their specs, the images, the settings and request fields, the decision set, the
bootstrap iterations and seed, the stratum guard parameters, every gate threshold (0.999 included), the
wording of §4.5.

**Operator exposure.** Until the §9.4 script has written its output, no one reads an S or S-regex
per-question file or score; progress is followed by the harness's row counts. Any other look is reported in
`RESULTS.md`. A rule, not a mechanism.

## 7. Procedure and cost

1. **Freeze** (§9); the owner names the server version on `dev`, the tools tag and the cost order; the
   two sentinel dry runs of §2 have succeeded (2026-11-11, receipts and stderr both ways), the study
   workflow text has reproduced them on the sentinel shard with its stderr file staged out (§2, open at
   v5), and the sentinel overlap count (0) and chunk count (168) of §6 are pinned.
2. **Build** two collections on `dev`, created by management as admin on the owner's typed go (as the dry
   run was): `oa-eval-s` (S) and `oa-eval-sregex` (S-regex), tenant `public` as the other arms, with the
   chunk spec at create and **no `embedding` key** — dev's config default resolves
   `Salesforce/SFR-Embedding-Mistral`, 4096-d, `openai` api (the model registry is empty), exactly as the
   registry entries of the dry run show. The create body is
   `{"id", "label", "chunk": {"method": "sentence", "size": 512, "overlap": 0, "params": {"size_unit":
   "tokens", "size_tolerance": 0.25, "min_piece": 128, "structure_cuts": "sections/1"}}}`; the entry's
   `chunk_params` must equal the dry run's and its `spec_hash` `29236085`. Ingest the pinned shard files by
   direct `gowe submit` of the study text (§2: `ingest-bulk-h2h.cwl` for S, the b2-renamed copy for
   S-regex), with the same inputs file keys as the Addendum C arms (shards, physical collection and
   `collection_id`, tenant, method/size/overlap, embedding endpoints and model, store URLs, glossary
   `rules`, `max_chunks` 0; no `registry`, no `structure_metadata`): the sentinel shard first, checked with
   `h2h_report.py dryrun` (receipt, summary, staged stderr file, logs endpoint, store counts, registry
   entry; it must give 168 chunks with the dry run's chunk ids), then the 16 main shards. Capacity: `dev` allows `MAX_COLLECTIONS=150` tenant-wide and 100 per
   owner, admins exempt (STATUS.md); the private inventories list 10 `oa-eval-*` collections (private,
   `oa-eval-collections.json` and `sections-2026-11/ingest/oa-eval-collections-sections.json`), so two
   more need no archiving. The operator still lists collections before creating and records the count in
   `README.md`. Compute: Addendum C ingested four such arms in one day on `dev` (private,
   `sections-2026-11/ingest/start.txt` and `RESULTS.md`); S embeds the corpus once with no overlap and
   should store fewer chunks than sent512s.
3. **Retrieve** 5 arms × 4 settings × 2,437 questions = **48,740 calls** (38,992 without sent512s), max 4
   in flight, arms interleaved per question, `/v1/retrieve` on `dev`. No LLM, no gateway, no judge.
4. **Score** per setting twice, into separate `out_dir`s: the three-arm decision config and the five-arm
   reporting config, each with the frozen `noise.json` beside it; **`decide`** per setting on the three-arm
   output (ft512o64 baseline, `paired_ladder`, `bonferroni`, integer ranks per the frozen order; the harness
   refuses otherwise).
5. **The §9.4 script** computes G1, H1, H2, H3, C1–C4, the drift and overlap figures, the file-checkable
   gates and the cross-check against `decision.json`, and prints the §4.5 sentences → public
   `artifacts/h2h-report.json` (aggregates only).
6. `RESULTS.md` with the sentences verbatim; `REPORT.md` in the owner's five-part order afterwards.

Owner time: the freeze decisions, the acknowledgement of the decision sentence.

## 8. Threats to validity

- **Seen question set** (§1, §3): the reused arms' results are known, S's are not; the rule is fixed in
  writing, the set is not unseen, and the cost-order choice is made knowing the prior.
- **Segmenter-cut passages** (§3): quote edges follow the passage step's segmenter, a declared bias in H3.
- **Single-section, single-span quotes.** The metric cannot see section-crossing or long answers; the
  640-token ceiling and `min_piece` merge are tested only on short quotes (remedy: Q4, outside this study).
- **One knob pair.** C1 measures `size_tolerance` + `min_piece` jointly and cannot attribute the effect.
- **Versions.** One server version for all arms (gate), but not the record's; the reused arms were embedded
  weeks earlier (re-embed gate, §2); their absolute hit@5 may differ from the record by score-tie noise.
- **Segmenter identity is per process, not per document** (§2); the stderr gate covers the rest.
- **ctx@5 is chars/4** unless the owner takes Q6; **S-regex models a hypothetical worker**, not an image that
  exists today; **the cost order is a convention** (§4.2), so both orders' decisions are printed.

## 9. Freeze, provenance, release

### 9.1 The freeze set (one `main` commit named by the owner)
- this `PREREG.md`; `power.py` (the §5 script; standard library, deterministic, no data) and its
  `power.json`, generated from a clean tree;
- the harness `python/scripts/eval/oa_chunk_eval/` as on `main` at that commit (frozen at d2a8b9b for the OA
  eval and extended only by Addendum C's config keys; any change since is listed in `README.md`);
- the §9.4 script and its tests;
- the study workflow text `ingest-bulk-h2h.cwl` and the S-regex `.def`, both beside this file;
- by sha256 in the tag message and in a public `frozen-inputs.json`: the 16 shard files + sentinel shard,
  the two workflow texts (the committed S text and its b2-renamed S-regex copy), the decision and reporting
  configs per setting (derived from the Addendum C configs by `h2h_report.py configs`, each with its own
  `out_dir`; the key-by-key diff is the public `config-diff.json`), the frozen `noise.json` per setting
  (copied into both `out_dir`s), the decision-set and sensitivity-set question-id lists, the Addendum C
  census per reused arm (a read-only live count at the freeze), the 200-chunk re-embed sample's ids
  (private file, public sha256; drawn by `h2h_report.py reembed select` from the ft512o64s receipts, seed
  20261111), the embedding and reranker model ids, the two tools images' digests and the `.def`.

### 9.2 Procedure (the reach study's) and 9.3 deviations
The owner says freeze in writing, naming the commit, the cost order, the server version and the tools tag;
`sha256sum PREREG.md` and a UTC timestamp go into a private `PREREG.sha256`; a signed annotated tag
`prereg-adopted-spec-h2h-2026-11` at that commit carries the file's sha256 and every §9.1 sha256; one commit
records the freeze in this directory's `README.md`; `PREREG.md` is never touched again. The §9.4 script takes
`--prereg PREREG.md --prereg-sha256 <sha>` and refuses on a mismatch. Deviations: as §6, `DEVIATIONS.md`,
dated, with what had been seen.

### 9.4 Code needed (small), and what is released
A public script `python/scripts/eval/h2h_report.py` (package `python/scripts/eval/h2h/`), on
`addendum_c.py`'s pattern: `report` reads the four settings' three-arm and five-arm `per_question.jsonl`
+ `score.json` and the decision config's `decision.json`, plus a private manifest naming the receipts,
staged stderr files, store counts, shard files and re-embed cosines, and computes G1, H1, H2 (ctx@5 and
stratum rules), H3, C1–C4 (Holm per setting), strata, the sensitivity set, the drift and chunk-id-overlap
figures, the file-checkable gates (receipt `chunking` object, `segmenter_version`, `image_commit` and
`image_build`, worker stderr lines, `git_sha` per row, set sizes and id-list hashes, shard hashes, store
counts, re-embed cosines and model ids), the cross-check and both cost orders' decisions; prints the §4.5
sentences; takes `--prereg --prereg-sha256` and refuses on a mismatch; refuses to write a public file
holding a question, quote, record id, host or path. Companions: `dryrun` (the sentinel checks of §2 and §7
step 2), `reembed select|fetch|verify` (the 200-chunk sample; `fetch` is the one live step, run by the
operator), `configs` (the two configs per setting from the Addendum C configs). Tests
(`python/tests/eval/test_h2h_report.py`): every §4.5 row under both orders on synthetic numbers, the
order-swap mutation, H3, every gate, the cross-check pass and fail, the GATE-NOT-EVALUABLE paths, the
public-file guard, the end-to-end report on synthetic rows, the seeded sample, the config derivation and
the §5 numbers; no harness change. Public here: this file, `README.md`, `power.py`, `power.json`,
`frozen-inputs.json`, `config-diff.json`, `ingest-bulk-h2h.cwl`, `ragstack-tools-nonltk.def`,
`artifacts/h2h-report.json`, `RESULTS.md`, `REPORT.md`, `DEVIATIONS.md` if any. Private: configs, the
manifest, per-question files, retrieval rows, receipts, worker stderr, the dry-run records, exports, the
re-embed ids and cosines, `PREREG.sha256`.

## 10. Owner decisions (answered 2026-11-11, before the freeze)

1. **Cost order** (§4.2): **Order B** (Addendum B's within-class rule by stored chunks / ingest compute).
   Decision-config ranks ft512o64 1, S 2, ft512o64s 3; a tie at H2 deploys S unless lower(ft512o64s − S) > 0.
   Chosen with the prior (sent512s ≈ ft512o64s) known; the §4.5 sentence still prints what Order A would
   have decided.
2. **Margin:** M_ni = 2.0 hit@5 points, the frozen Addendum B value, for H1 and H3.
3. **sent512s as a secondary arm:** included (re-retrieval only, 9,748 calls, no collection).
4. **Cross-section / multi-span supplementary set:** out of scope here; a separate pre-registration after
   this run. The owner's working assumption for that study is that questions whose answers span sections
   are usually composite or complex questions; it is an assumption to be tested (from data, or from
   evidence in the literature) before that study's design depends on it.
5. **Embedding-drift threshold:** cosine ≥ 0.999 on all 200 re-embedded ft512o64s chunks.
6. **Context tokens:** chars/4 as frozen (labelled estimated).
7. **Server version and tools tag:** whatever `dev` runs at the freeze (≥ v1.6.25), named in the freeze
   statement; both new arms built from stamped images of that tag (S-regex as its no-`nltk` build), all five
   arms retrieved on it.
